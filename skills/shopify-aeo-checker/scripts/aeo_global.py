#!/usr/bin/env python3
"""AEO Share of Shelf: how often a brand shows up when AI shopping agents search ALL of Shopify.

Give it one product page URL. It reads the product page, works out the brand, builds shopper
search terms (or takes yours via --query), runs each through Shopify's public Global Catalog
(catalog.shopify.com, the cross-store pool agents search), and counts how many of the results
belong to the brand versus competitors. Standard library only.

Outputs: stdout summary, report.md, aeo-data.json and aeoData.ts (the data module for the
Type app template in app_template/, which renders the Google-style results page with an
editable search bar that runs new live searches).
"""
import argparse, base64, collections, datetime, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

GLOBAL_MCP = "https://catalog.shopify.com/api/ucp/mcp"
PROFILE = "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/valid-with-capabilities.json"
UA = "Mozilla/5.0 (compatible; aeo-checker/2.0)"
RANK_POINTS = [(1, 100), (3, 85), (5, 70), (10, 50), (20, 25), (50, 10)]
TGC_URL = "https://thegrowthchef.com"
TGC_LOGO_URL = "https://thegrowthchef.com/img/og.png"
TGC_LOGO = "https://thegrowthchef.com/favicon.svg"  # fallback if the website logo cannot be fetched


# ---------------------------------------------------------------- helpers
def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def plain(desc):
    if isinstance(desc, dict):
        desc = desc.get("plain") or desc.get("html") or ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(desc or ""))).strip()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def host_of(url):
    return (urllib.parse.urlparse(url or "").netloc or "").lower().removeprefix("www.")


def handle_of(url):
    m = re.search(r"/products/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def money(minor, cur="USD"):
    if minor is None:
        return "n/a"
    sym = {"USD": "$", "CAD": "CA$", "GBP": "£", "EUR": "€", "AUD": "A$"}.get(cur, cur + " ")
    return f"{sym}{minor / 100:,.2f}"


def rank_points(pos):
    if not pos:
        return 0
    for cutoff, pts in RANK_POINTS:
        if pos <= cutoff:
            return pts
    return 0


def rating(s):
    return "Strong" if s >= 80 else "Fair" if s >= 60 else "Weak" if s >= 40 else "Poor"


def slim(p):
    v = (p.get("variants") or [{}])[0]
    seller = v.get("seller") or {}
    url = (v.get("url") or p.get("url") or "").split("?")[0]
    media = p.get("media") or v.get("media") or []
    pr = ((p.get("price_range") or {}).get("min") or v.get("price") or {})
    rt = p.get("rating") or {}
    return {"title": p.get("title") or "", "url": url, "handle": handle_of(url),
            "description": plain(p.get("description"))[:600],
            "price": pr.get("amount"), "currency": pr.get("currency", "USD"),
            "image": (media[0].get("url") if media else "") or "", "alt": (media[0].get("alt_text") if media else "") or "",
            "seller": seller.get("name") or host_of(url) or "Unknown", "seller_domain": (seller.get("domain") or "").lower(),
            "seller_url": seller.get("url") or "", "rating": rt.get("value"), "reviews": rt.get("count")}


# ---------------------------------------------------------------- sources
class Live:
    def __init__(self, record=None, delay=1.0, country="US"):
        self.record, self.delay, self.country = record, delay, country

    def _get(self, url):
        req = urllib.request.Request(url, headers={"user-agent": UA})
        return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

    def _save(self, rel, data):
        if not self.record:
            return
        path = os.path.join(self.record, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)

    def pdp(self, url):
        u = urllib.parse.urlparse(url)
        base, handle = f"{u.scheme or 'https'}://{u.netloc}", handle_of(url)
        if not handle:
            raise SystemExit(f"Not a product URL (expected /products/<handle>): {url}")
        pj = json.loads(self._get(f"{base}/products/{handle}.js"))
        try:
            meta = json.loads(self._get(f"{base}/meta.json"))
        except Exception:
            meta = {}
        facts = {"url": url, "handle": handle, "domain": u.netloc.lower(), "title": pj.get("title", ""),
                 "type": pj.get("type") or "", "vendor": pj.get("vendor") or "", "tags": pj.get("tags") or [],
                 "description": plain(pj.get("description"))[:1500], "price": pj.get("price"),
                 "options": [{"name": o.get("name"), "values": o.get("values", [])} for o in (pj.get("options") or []) if isinstance(o, dict)],
                 "shop_name": meta.get("name") or pj.get("vendor") or "", "myshopify_domain": (meta.get("myshopify_domain") or "").lower(),
                 "shop_domain": (meta.get("domain") or u.netloc).lower()}
        self._save("pdp.json", facts)
        return facts

    def search(self, query, limit):
        time.sleep(self.delay)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_catalog", "arguments": {
            "meta": {"ucp-agent": {"profile": PROFILE}},
            "catalog": {"query": query, "context": {"address_country": self.country}, "pagination": {"limit": limit}}}}}
        req = urllib.request.Request(GLOBAL_MCP, data=json.dumps(body).encode(), headers={"user-agent": UA, "content-type": "application/json"})
        for attempt in range(2):
            try:
                res = json.load(urllib.request.urlopen(req, timeout=60))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise SystemExit(f"Global Catalog search failed for '{query}' after retry: {exc}") from exc
            if "error" not in res:
                break
            if attempt == 0:
                time.sleep(2)
                continue
            raise SystemExit(f"Global Catalog search failed for '{query}' after retry: {res['error'].get('message')}")
        r = res.get("result") or {}
        data = r.get("structuredContent") or json.loads(r["content"][0]["text"])
        out = {"total": (data.get("pagination") or {}).get("total_count"), "products": [slim(p) for p in data.get("products", [])[:limit]]}
        self._save(f"search/{slug(query)}.json", out)
        return out


class Offline:
    def __init__(self, root):
        self.root = root

    def _load(self, rel):
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            raise SystemExit(f"Missing fixture {path}")
        with open(path) as f:
            return json.load(f)

    def pdp(self, url):
        return self._load("pdp.json")

    def search(self, query, limit):
        d = self._load(f"search/{slug(query)}.json")
        return {"total": d.get("total"), "products": d["products"][:limit]}


# ---------------------------------------------------------------- queries + scoring
def default_queries(f):
    """Fallback when the agent passes no --query: generic terms from the product type and title."""
    t = (f.get("type") or "").lower().strip()
    title = f.get("title", "").lower()
    tags = " ".join(f.get("tags") or []).lower()
    who = "mens " if re.search(r"\b(men|mens|men's|dep_mens)\b", tags + " " + title) else "womens " if re.search(r"\b(women|womens|women's)\b", tags + " " + title) else ""
    q = []
    if t:
        q += [who + t, f"best {t}"]
        if re.search(r"\b(\d+-?pack|pack|set|bundle)\b", title):
            q.append(f"{t} pack")
    return q or [re.sub(r"\s+", " ", re.sub(r"\d+-?pack", "", title)).strip()]


def is_brand(p, f):
    names = {norm(f.get("shop_name")), norm(f.get("vendor"))} - {""}
    hosts = {host_of("https://" + f.get("shop_domain", "")), host_of("https://" + f.get("domain", ""))} - {""}
    return (p["seller_domain"] and p["seller_domain"] == f.get("myshopify_domain")) or host_of(p["url"]) in hosts \
        or host_of(p["seller_url"]) in hosts or norm(p["seller"]) in names


def analyse(f, runs, top):
    for r in runs:
        r["brand_pos"] = [i for i, p in enumerate(r["products"], 1) if p["is_brand"]]
        r["product_pos"] = next((i for i, p in enumerate(r["products"], 1) if p["is_brand"] and p["handle"] == f["handle"]), None)
    shown = sum(len(r["products"]) for r in runs) or 1
    brand_n = sum(len(r["brand_pos"]) for r in runs)
    vis = round(sum(rank_points(r["brand_pos"][0] if r["brand_pos"] else None) for r in runs) / len(runs))
    sellers = collections.OrderedDict()
    for r in runs:
        for i, p in enumerate(r["products"], 1):
            s = sellers.setdefault(p["seller"], {"seller": p["seller"], "count": 0, "queries": set(), "best": i, "brand": p["is_brand"]})
            s["count"] += 1; s["queries"].add(r["query"]); s["best"] = min(s["best"], i)
    board = sorted(sellers.values(), key=lambda s: (-s["count"], s["best"], s["seller"]))
    for i, s in enumerate(board, 1):
        s["rank"] = i
    fixes = []
    for r in runs:
        if not r["brand_pos"]:
            lead = [s for s in collections.Counter(p["seller"] for p in r["products"]).most_common(3)]
            fixes.append(("High", f"Brand is absent from the top {top} for '{r['query']}'; shelf is led by " + ", ".join(f"{n} ({c})" for n, c in lead)))
        elif r["brand_pos"][0] > 10:
            fixes.append(("Medium", f"First brand result for '{r['query']}' is #{r['brand_pos'][0]}, below the top 10"))
    if not any(r["product_pos"] for r in runs):
        fixes.append(("High", f"The product itself ({f['title']}) never appears; other brand SKUs or competitors take its place"))
    return {"visibility": vis, "brand_results": brand_n, "shown": shown, "share": round(100 * brand_n / shown),
            "top10_queries": sum(1 for r in runs if r["brand_pos"] and r["brand_pos"][0] <= 10), "board": board, "fixes": fixes}


# ---------------------------------------------------------------- outputs
def text_summary(brand, f, date, runs, a, top):
    L = [f"AEO share of shelf: {brand} ({f['domain']}), {date}",
         f"Product: {f['title']}",
         f"Visibility: {a['visibility']}/100 ({rating(a['visibility'])}). Brand holds {a['brand_results']} of {a['shown']} results ({a['share']}%); top-10 on {a['top10_queries']} of {len(runs)} searches.",
         "Source: Shopify Global Catalog (all Shopify stores), anonymous agent tier. Not ChatGPT/Gemini final rankings.", ""]
    for r in runs:
        pos = ", ".join(f"#{p}" for p in r["brand_pos"][:6]) or "none"
        L.append(f"  {r['query']!r:40} brand {len(r['brand_pos']):2}/{len(r['products'])}  positions {pos}" + (f"  (product #{r['product_pos']})" if r["product_pos"] else ""))
    L += ["", "Top sellers on these searches:"]
    for s in a["board"][:8]:
        L.append(f"  {s['rank']:2}. {s['seller'] + (' (YOU)' if s['brand'] else ''):32} {s['count']:3} results, best #{s['best']}, on {len(s['queries'])}/{len(runs)} searches")
    if not any(s["brand"] for s in a["board"][:8]):
        me = next((s for s in a["board"] if s["brand"]), None)
        L.append(f"  .. {brand} (YOU): " + (f"#{me['rank']} with {me['count']} results" if me else "no results"))
    if a["fixes"]:
        L += ["", "Fixes:"] + [f"  - {sev}: {msg}" for sev, msg in a["fixes"]]
    return "\n".join(L) + "\n"


def report_md(brand, f, date, runs, a, top):
    L = [f"## AEO share of shelf: {brand}, {date}", "",
         f"**Visibility {a['visibility']}/100 ({rating(a['visibility'])}).** {brand} holds **{a['brand_results']} of {a['shown']}** results ({a['share']}%) that Shopify's Global Catalog returns to AI shopping agents across {len(runs)} shopper searches built from [{f['title']}]({f['url']}).", "",
         "| Search | Brand results | First brand position | This product | Top seller |", "| --- | ---: | ---: | ---: | --- |"]
    for r in runs:
        lead = collections.Counter(p["seller"] for p in r["products"]).most_common(1)
        L.append(f"| {r['query']} | {len(r['brand_pos'])}/{len(r['products'])} | {'#' + str(r['brand_pos'][0]) if r['brand_pos'] else '—'} | {'#' + str(r['product_pos']) if r['product_pos'] else '—'} | {lead[0][0] + ' (' + str(lead[0][1]) + ')' if lead else '—'} |")
    L += ["", "| # | Seller | Results | Best position | Searches |", "| ---: | --- | ---: | ---: | ---: |"]
    for s in a["board"][:10]:
        L.append(f"| {s['rank']} | {'**' + s['seller'] + ' (you)**' if s['brand'] else s['seller']} | {s['count']} | #{s['best']} | {len(s['queries'])}/{len(runs)} |")
    if a["fixes"]:
        L += ["", "**What to look at**", ""] + [f"- **{sev}**: {msg}" for sev, msg in a["fixes"]]
    L += ["", f"_Method: search terms come from the product page; each is sent to Shopify's Global Catalog `search_catalog` (catalog.shopify.com, anonymous tier, top {top}). "
          "This is the cross-store shelf agents start from; ChatGPT, Gemini and others may re-rank, personalise or use other data. Heuristic, not traffic._",
          "", f"_AEO check by The Growth Chef Marketing Group · {TGC_URL}_"]
    return "\n".join(L) + "\n"


def app_data(brand, f, date, runs, top, country="US"):
    """Everything the web app needs: brand matching rules, the product, and the recorded searches."""
    hosts = sorted({h for h in (host_of("https://" + f.get("shop_domain", "")), host_of("https://" + f.get("domain", ""))) if h})
    return {"brand": brand, "date": date, "top": top, "country": country, "endpoint": GLOBAL_MCP, "profile": PROFILE, "tgcLogo": TGC_LOGO,
            "product": {"handle": f["handle"], "title": f["title"], "url": f["url"]},
            "match": {"names": sorted({norm(f.get("shop_name")), norm(f.get("vendor"))} - {""}), "hosts": hosts, "myshopify": f.get("myshopify_domain") or ""},
            "runs": [{"query": r["query"], "total": r.get("total"),
                      "products": [{k: v for k, v in p.items() if k != "is_brand"} for p in r["products"]]} for r in runs]}


# ---------------------------------------------------------------- main
def main():
    global TGC_LOGO
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--product", required=True, help="One product page URL (/products/<handle>)")
    ap.add_argument("--query", action="append", default=[], help="Shopper search term; repeatable. Best: 3-5 broad terms written from the PDP")
    ap.add_argument("--brand", help="Display name (default: store name from the site)")
    ap.add_argument("--facts", action="store_true", help="Print the product page facts as JSON and stop (use them to write --query terms)")
    ap.add_argument("--top", type=int, default=20, help="Results per search, max 50 (default 20)")
    ap.add_argument("--country", default="US")
    ap.add_argument("--fixtures", help="Replay recorded responses from this folder (offline)")
    ap.add_argument("--record", help="Save live responses to this folder for later offline replay")
    ap.add_argument("--out", default="aeo-output")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    top = max(1, min(a.top, 50))

    src = Offline(a.fixtures) if a.fixtures else Live(a.record, country=a.country)
    f = src.pdp(a.product)
    if a.facts:
        print(json.dumps({k: f[k] for k in ("title", "vendor", "shop_name", "type", "price", "tags", "options", "description")}, indent=1))
        return
    if not a.fixtures:
        try:
            raw = urllib.request.urlopen(urllib.request.Request(TGC_LOGO_URL, headers={"user-agent": UA}), timeout=15).read()
            TGC_LOGO = "data:image/png;base64," + base64.b64encode(raw).decode()
        except Exception:
            pass
    brand = a.brand or f.get("shop_name") or f.get("vendor") or f["domain"]
    queries = list(dict.fromkeys(q.strip() for q in (a.query or default_queries(f)) if q.strip()))[:8]
    runs = []
    for q in queries:
        res = src.search(q, top)
        for p in res["products"]:
            p["is_brand"] = bool(is_brand(p, f))
        runs.append({"query": q, "total": res.get("total"), "products": res["products"]})
    an = analyse(f, runs, top)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "report.md"), "w") as fh:
        fh.write(report_md(brand, f, a.date, runs, an, top))
    data = json.dumps(app_data(brand, f, a.date, runs, top, a.country), ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "aeo-data.json"), "w") as fh:
        fh.write(data + "\n")
    with open(os.path.join(a.out, "aeoData.ts"), "w") as fh:  # drop-in data module for the Type app template
        fh.write("// Generated by aeo_global.py. Recorded searches and brand matching rules for this check.\n"
                 "import type { AeoData } from \"./aeoTypes\";\n\nexport const AEO_DATA: AeoData = " + data + ";\n")
    sys.stdout.write(text_summary(brand, f, a.date, runs, an, top))


if __name__ == "__main__":
    main()
