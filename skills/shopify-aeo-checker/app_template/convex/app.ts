import { v } from "convex/values";
import { query, readAction } from "./typeFunctions";

// AEO Share of Shelf backend. The only outward call is a read-only search of
// Shopify's public Global Catalog (every Shopify store), the same pool AI
// shopping agents search. No credentials, no writes.

const GLOBAL_MCP = "https://catalog.shopify.com/api/ucp/mcp";
const PROFILE =
  "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/valid-with-capabilities.json";

const product = v.object({
  title: v.string(),
  url: v.string(),
  handle: v.string(),
  description: v.string(),
  price: v.union(v.number(), v.null()),
  currency: v.string(),
  image: v.string(),
  alt: v.string(),
  seller: v.string(),
  seller_domain: v.string(),
  seller_url: v.string(),
  rating: v.union(v.number(), v.null()),
  reviews: v.union(v.number(), v.null()),
});

export const status = query({
  args: {},
  returns: v.object({ ready: v.boolean() }),
  handler: async () => ({ ready: true }),
});

type Json = Record<string, unknown>;
const obj = (x: unknown): Json => (x && typeof x === "object" ? (x as Json) : {});
const str = (x: unknown): string => (typeof x === "string" ? x : "");
const num = (x: unknown): number | null =>
  typeof x === "number" && Number.isFinite(x) ? x : null;

function plain(d: unknown): string {
  const o = obj(d);
  const raw = typeof d === "string" ? d : str(o.plain) || str(o.html);
  return raw
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

function hostOf(u: string): string {
  try {
    return new URL(u).host.toLowerCase().replace(/^www\./, "");
  } catch {
    return "";
  }
}

function slim(p: unknown) {
  const P = obj(p);
  const variants = Array.isArray(P.variants) ? P.variants : [];
  const V = obj(variants[0]);
  const S = obj(V.seller);
  const url = (str(V.url) || str(P.url)).split("?")[0];
  const mediaList = Array.isArray(P.media) ? P.media : Array.isArray(V.media) ? V.media : [];
  const M = obj(mediaList[0]);
  const pr = obj(obj(P.price_range).min ?? V.price);
  const rt = obj(P.rating);
  return {
    title: str(P.title),
    url,
    handle: (url.match(/\/products\/([^/?#]+)/) || [])[1] || "",
    description: plain(P.description).slice(0, 600),
    price: num(pr.amount),
    currency: str(pr.currency) || "USD",
    image: str(M.url),
    alt: str(M.alt_text),
    seller: str(S.name) || hostOf(url) || "Unknown",
    seller_domain: str(S.domain).toLowerCase(),
    seller_url: str(S.url),
    rating: num(rt.value),
    reviews: num(rt.count),
  };
}

export const searchCatalog = readAction({
  args: { query: v.string(), top: v.number(), country: v.string() },
  returns: v.object({ total: v.union(v.number(), v.null()), products: v.array(product) }),
  handler: async (_ctx, args) => {
    const q = args.query.trim();
    if (q.length < 1 || q.length > 200) throw new Error("Search must be 1-200 characters.");
    if (!Number.isFinite(args.top)) throw new Error("Invalid result count.");
    const top = Math.max(1, Math.min(50, Math.round(args.top)));
    const country = /^[A-Z]{2}$/.test(args.country) ? args.country : "US";
    const body = {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: {
        name: "search_catalog",
        arguments: {
          meta: { "ucp-agent": { profile: PROFILE } },
          catalog: { query: q, context: { address_country: country }, pagination: { limit: top } },
        },
      },
    };
    const res = await fetch(GLOBAL_MCP, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Shopify catalog returned HTTP ${res.status}.`);
    const j = obj(await res.json());
    if (j.error) throw new Error(str(obj(j.error).message) || "Shopify catalog search failed.");
    const r = obj(j.result);
    let data = obj(r.structuredContent);
    if (!data.products && Array.isArray(r.content)) {
      try {
        data = obj(JSON.parse(str(obj(r.content[0]).text)));
      } catch {
        data = {};
      }
    }
    const list = Array.isArray(data.products) ? data.products : [];
    return { total: num(obj(data.pagination).total_count), products: list.slice(0, top).map(slim) };
  },
});
