import { useMemo, useState, type FormEvent } from "react";
import { useAction } from "convex/react";
import { api } from "../convex/_generated/api";
import { AEO_DATA } from "./aeoData";
import type { AeoProduct } from "./aeoTypes";

// AEO Share of Shelf: a Google-style view of what AI shopping agents get back
// from Shopify's Global Catalog. Recorded searches load instantly; editing the
// search bar runs a new live search and recalculates the brand's share.

const D = AEO_DATA;
const TGC_URL = "https://thegrowthchef.com";
const RANK_POINTS: [number, number][] = [[1, 100], [3, 85], [5, 70], [10, 50], [20, 25], [50, 10]];

type Run = {
  id: number;
  query: string;
  state: "ok" | "loading" | "error";
  total: number | null;
  products: AeoProduct[];
  live: boolean;
  error?: string;
};

const norm = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
const hostOf = (u: string) => {
  try {
    return new URL(u).host.toLowerCase().replace(/^www\./, "");
  } catch {
    return "";
  }
};
const isBrand = (p: AeoProduct) =>
  (!!p.seller_domain && p.seller_domain === D.match.myshopify) ||
  D.match.hosts.includes(hostOf(p.url)) ||
  D.match.hosts.includes(hostOf(p.seller_url)) ||
  D.match.names.includes(norm(p.seller));
const points = (pos?: number) => {
  if (!pos) return 0;
  for (const [cut, pts] of RANK_POINTS) if (pos <= cut) return pts;
  return 0;
};
const rating = (s: number) => (s >= 80 ? "Strong" : s >= 60 ? "Fair" : s >= 40 ? "Weak" : "Poor");
const tone = (s: number) => (s >= 80 ? "good" : s >= 60 ? "fair" : "poor");
const money = (m: number | null, c: string) =>
  m == null
    ? "n/a"
    : (({ USD: "$", CAD: "CA$", GBP: "£", EUR: "€", AUD: "A$" } as Record<string, string>)[c] ?? `${c} `) +
      (m / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function brandPositions(r: Run) {
  const out: number[] = [];
  r.products.forEach((p, i) => isBrand(p) && out.push(i + 1));
  return out;
}

function analyse(runs: Run[]) {
  const done = runs.filter((r) => r.state === "ok");
  const shown = done.reduce((a, r) => a + r.products.length, 0);
  const pos = done.map(brandPositions);
  const brandN = pos.reduce((a, p) => a + p.length, 0);
  const vis = done.length ? Math.round(pos.reduce((a, p) => a + points(p[0]), 0) / done.length) : 0;
  const sellers = new Map<string, { seller: string; count: number; best: number; brand: boolean; qs: Set<string> }>();
  done.forEach((r) =>
    r.products.forEach((p, i) => {
      const s = sellers.get(p.seller) ?? { seller: p.seller, count: 0, best: i + 1, brand: false, qs: new Set<string>() };
      s.count += 1;
      s.best = Math.min(s.best, i + 1);
      s.brand = s.brand || isBrand(p);
      s.qs.add(r.query);
      sellers.set(p.seller, s);
    }),
  );
  const board = [...sellers.values()]
    .sort((a, b) => b.count - a.count || a.best - b.best || a.seller.localeCompare(b.seller))
    .map((s, i) => ({ ...s, rank: i + 1 }));
  const fixes: [string, string][] = [];
  done.forEach((r, i) => {
    const p = pos[i];
    if (!p.length) {
      const c = new Map<string, number>();
      r.products.forEach((x) => c.set(x.seller, (c.get(x.seller) ?? 0) + 1));
      const lead = [...c.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([n, k]) => `${n} (${k})`).join(", ");
      fixes.push(["High", `${D.brand} is absent from the top ${r.products.length} for “${r.query}”; the shelf is led by ${lead}`]);
    } else if (p[0] > 10) fixes.push(["Medium", `First ${D.brand} result for “${r.query}” is #${p[0]}, below the top 10`]);
  });
  const productSeen = done.some((r) => r.products.some((p) => isBrand(p) && p.handle === D.product.handle));
  if (done.length && !productSeen)
    fixes.push(["High", `${D.product.title} itself never appears; other ${D.brand} listings or competitors take its place`]);
  return {
    vis,
    brandN,
    shown,
    share: shown ? Math.round((100 * brandN) / shown) : 0,
    top10: pos.filter((p) => p[0] && p[0] <= 10).length,
    board,
    fixes,
    n: done.length,
  };
}

let nextId = 1;
const initialRuns: Run[] = D.runs.map((r) => ({ id: nextId++, query: r.query, state: "ok", total: r.total, products: r.products, live: false }));

function Brandline() {
  return (
    <a className="aeo-tgc" href={TGC_URL} target="_blank" rel="noopener noreferrer">
      <img src={D.tgcLogo} alt="" />
      <span>by The Growth Chef Marketing Group</span>
    </a>
  );
}

function Result({ p, i }: { p: AeoProduct; i: number }) {
  const mine = isBrand(p);
  const me = mine && p.handle === D.product.handle;
  const host = hostOf(p.url) || hostOf(p.seller_url);
  let path = "";
  try {
    path = new URL(p.url).pathname.split("/").filter(Boolean).join(" › ");
  } catch {
    path = "";
  }
  return (
    <article className={`aeo-result${me ? " is-product" : mine ? " is-brand" : ""}`}>
      <span className="aeo-pos">{i + 1}</span>
      <div className="aeo-result-body">
        <div className="aeo-crumb">
          <span className="aeo-fav">{p.seller.slice(0, 1).toUpperCase()}</span>
          <div>
            <div className="aeo-site">{p.seller}</div>
            <div className="aeo-url">
              {host}
              {path ? ` › ${path}` : ""}
            </div>
          </div>
        </div>
        <a className="aeo-title" href={p.url} target="_blank" rel="noopener noreferrer">
          {p.title}
          {me ? <span className="aeo-tag aeo-tag-product">Your product</span> : mine ? <span className="aeo-tag aeo-tag-brand">Your brand</span> : null}
        </a>
        <p className="aeo-snippet">
          {p.description.slice(0, 170)}
          {p.description.length > 170 ? "…" : ""}
        </p>
        <div className="aeo-price">
          {money(p.price, p.currency)}
          {p.rating && p.reviews ? (
            <>
              {" · "}
              <span className="aeo-stars">★ {p.rating}</span> ({p.reviews.toLocaleString()})
            </>
          ) : null}
        </div>
      </div>
      {p.image ? <img className="aeo-thumb" src={p.image} alt={p.alt} loading="lazy" /> : <div className="aeo-thumb">{(p.title || "?")[0]}</div>}
    </article>
  );
}

export function App() {
  const search = useAction(api.app.searchCatalog);
  const [runs, setRuns] = useState<Run[]>(initialRuns);
  const [cur, setCur] = useState(0);
  const [draft, setDraft] = useState(initialRuns[0]?.query ?? "");
  const a = useMemo(() => analyse(runs), [runs]);
  const run = runs[cur];
  const busy = runs.some((r) => r.state === "loading");

  const choose = (i: number) => {
    setCur(i);
    setDraft(runs[i]?.query ?? "");
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    const q = draft.trim();
    if (!q || busy) return;
    const existing = runs.findIndex((r) => r.query.toLowerCase() === q.toLowerCase());
    if (existing >= 0 && runs[existing].state === "ok") return choose(existing);
    const id = nextId++;
    const pending: Run = { id, query: q, state: "loading", total: null, products: [], live: true };
    const idx = existing >= 0 ? existing : runs.length;
    setRuns((rs) => (existing >= 0 ? rs.map((r, i) => (i === existing ? pending : r)) : [...rs, pending]));
    setCur(idx);
    try {
      const res = await search({ query: q, top: D.top, country: D.country });
      setRuns((rs) => rs.map((r) => (r.id === id ? { ...r, state: "ok", total: res.total, products: res.products } : r)));
    } catch (err) {
      const msg = err instanceof Error ? err.message.replace(/^.*Uncaught Error:\s*/s, "").split("\n")[0] : "Search failed";
      setRuns((rs) => rs.map((r) => (r.id === id ? { ...r, state: "error", error: msg } : r)));
    }
  }

  function remove(i: number) {
    setRuns((rs) => rs.filter((_, j) => j !== i));
    const next = Math.max(0, i < cur ? cur - 1 : Math.min(cur, runs.length - 2));
    setCur(next);
    setDraft(runs.filter((_, j) => j !== i)[next]?.query ?? "");
  }

  const pos = run && run.state === "ok" ? brandPositions(run) : [];
  const max = Math.max(1, ...a.board.map((s) => s.count));
  const me = a.board.find((s) => s.brand);
  const rows = a.board.slice(0, 8);
  if (me && !rows.includes(me)) rows.push(me);

  return (
    <div className="aeo">
      <header className="aeo-top">
        <div className="aeo-logo">
          <div>
            <b className="l1">A</b>
            <b className="l2">E</b>
            <b className="l3">O</b>
            <b className="l4">·</b> <span className="aeo-logo-sub">agent view</span>
          </div>
          <Brandline />
        </div>
        <form className="aeo-box" onSubmit={submit} role="search">
          <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14"
            />
          </svg>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Try a shopper search, e.g. mens t-shirts"
            aria-label="Shopper search"
            maxLength={200}
          />
          <button className="button button-primary" type="submit" disabled={busy || !draft.trim()}>
            {busy ? "Searching…" : "Search"}
          </button>
        </form>
      </header>
      <p className="aeo-hint text-secondary">Edit the search and press Enter to see what agents get from every Shopify store. New searches are added as chips and update the score.</p>

      <nav className="aeo-chips" aria-label="Searches">
        {runs.map((r, i) => {
          const p = r.state === "ok" ? brandPositions(r) : [];
          const cls = r.state === "loading" ? "load" : r.state === "error" ? "poor" : p[0] && p[0] <= 3 ? "good" : p[0] && p[0] <= 10 ? "fair" : "poor";
          const lab = r.state === "loading" ? "…" : r.state === "error" ? "!" : `${p.length}/${r.products.length}`;
          return (
            <span key={r.id} className={`aeo-chip${i === cur ? " on" : ""}`}>
              <button type="button" className="aeo-chip-main" onClick={() => choose(i)}>
                {r.query}
                <i className={`aeo-count ${cls}`}>{lab}</i>
              </button>
              <button type="button" className="aeo-chip-x" aria-label={`Remove ${r.query}`} onClick={() => remove(i)} disabled={r.state === "loading"}>
                ×
              </button>
            </span>
          );
        })}
      </nav>

      <div className="aeo-wrap">
        <main className="aeo-main">
          {!run ? (
            <p className="aeo-meta">Type a shopper search above to see what agents get back.</p>
          ) : run.state === "loading" ? (
            <p className="aeo-meta">
              <span className="aeo-spin" />
              Searching every Shopify store for “{run.query}”…
            </p>
          ) : run.state === "error" ? (
            <div className="aeo-note aeo-note-warn">
              Live search failed for “{run.query}”: {run.error}. Press Search to try again.
            </div>
          ) : (
            <>
              <p className="aeo-meta">
                {run.total ? `About ${run.total.toLocaleString()} matching products across Shopify · ` : ""}top {run.products.length} shown to agents · {D.brand} holds {pos.length}
                {run.live ? " · live" : ` · recorded ${D.date}`}
              </p>
              <div className="aeo-note">
                What Shopify's Global Catalog (every Shopify store) returns to an AI shopping agent for this search, anonymous tier. ChatGPT, Gemini and other assistants may
                re-rank, personalise or add other sources. Treat it as the shelf agents start from.
              </div>
              {!pos.length && (
                <div className="aeo-note aeo-note-miss">
                  <b>{D.brand}</b> does not appear in the top {run.products.length} results agents get for “{run.query}”.
                </div>
              )}
              {run.products.length === 0 && <p className="aeo-meta">No products came back for this search.</p>}
              {run.products.map((p, i) => (
                <Result key={`${run.id}-${i}`} p={p} i={i} />
              ))}
            </>
          )}
        </main>

        <aside className="aeo-side">
          <section className="card aeo-card">
            <h2>{D.brand}</h2>
            <p className="text-secondary aeo-small">
              {D.product.title} · {D.date}
            </p>
            <div className={`aeo-big ${tone(a.vis)}`}>
              {a.vis}
              <span>/100 · {rating(a.vis)}</span>
            </div>
            <p className="text-secondary aeo-small">
              Holds <b>{a.brandN} of {a.shown}</b> results ({a.share}%) · top 10 on {a.top10} of {a.n} searches
            </p>
          </section>
          <section className="card aeo-card">
            <h2>Who owns this shelf</h2>
            <p className="text-secondary aeo-small">Results per seller across all searches</p>
            <table className="aeo-board">
              <tbody>
                {rows.map((s) => (
                  <tr key={s.seller} className={s.brand ? "you" : ""}>
                    <td className="n">{s.rank}</td>
                    <td>
                      {s.seller}
                      {s.brand ? " (you)" : ""}
                      <div className="aeo-bar">
                        <i style={{ width: `${Math.round((100 * s.count) / max)}%` }} />
                      </div>
                    </td>
                    <td className="n">{s.count}</td>
                    <td className="n">best #{s.best}</td>
                  </tr>
                ))}
                {!me && (
                  <tr className="you">
                    <td className="n">–</td>
                    <td>{D.brand} (you)</td>
                    <td className="n">0</td>
                    <td className="n">—</td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>
          {a.fixes.length > 0 && (
            <section className="card aeo-card">
              <h2>What to look at</h2>
              {a.fixes.map(([sev, msg], i) => (
                <div key={i} className={`aeo-fix ${sev}`}>
                  <b>{sev}</b>
                  {msg}
                </div>
              ))}
            </section>
          )}
        </aside>
      </div>
      <footer className="aeo-foot">
        <a className="aeo-tgc" href={TGC_URL} target="_blank" rel="noopener noreferrer">
          <img src={D.tgcLogo} alt="" />
          <span>AEO check by The Growth Chef Marketing Group · thegrowthchef.com</span>
        </a>
      </footer>
    </div>
  );
}

export default App;
