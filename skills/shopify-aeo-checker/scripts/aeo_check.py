#!/usr/bin/env python3
"""AEO Checker: what Shopify's UCP catalog shows AI shopping agents for a brand's products.

Standard library only. Live mode calls the store's public UCP MCP endpoint at the
anonymous tier. Offline mode (--fixtures) replays recorded responses so runs are
reproducible. Outputs a text summary (stdout), report.md and a search-results-style
results.html.
"""
import argparse, datetime, html, json, os, re, sys, time, urllib.parse, urllib.request

PROFILE = "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/valid-with-capabilities.json"
UA = "Mozilla/5.0 (compatible; aeo-checker/1.0)"
TOP_N = 20
RANK_POINTS = [(1, 100), (3, 85), (5, 70), (10, 50), (20, 25)]
QUERY_WEIGHTS = {"exact": 1, "name": 1, "category": 2, "intent": 2, "custom": 2}
TGC_URL = "https://thegrowthchef.com"
TGC_LOGO_URL = "https://thegrowthchef.com/img/og.png"
TGC_LOGO = "https://thegrowthchef.com/favicon.svg"  # fallback if the website logo cannot be fetched
INTERNAL_TAG = re.compile(r"(_|^(bfcm|promo|upsell|hidden|visible|test|internal|sync|yotpo|judgeme|klaviyo|rebuy|tigris|swatch)|upsell|exclude|feed|gid:|[A-Z]{2,}\d{2})", re.I)


# ---------------------------------------------------------------- sources
class Live:
    def __init__(self, domain, record=None, delay=1.0):
        self.domain, self.record, self.delay = domain, record, delay
        self._ep = None

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

    def manifest(self):
        m = json.loads(self._get(f"https://{self.domain}/.well-known/ucp"))
        self._save("manifest.json", m)
        return m

    def endpoint(self, manifest):
        for s in manifest["ucp"]["services"].get("dev.ucp.shopping", []):
            if s.get("transport") == "mcp" and s.get("endpoint"):
                return s["endpoint"]
        raise SystemExit("Store manifest has no UCP MCP endpoint; this store is not agent-searchable via UCP.")

    def page(self, url):
        handle = handle_of(url)
        base = url.split("/products/")[0]
        try:
            pj = json.loads(self._get(f"{base}/products/{handle}.js"))
            opts = pj.get("options") or []
            facts = {"handle": handle, "url": url, "title": pj.get("title", ""), "type": pj.get("type") or "",
                     "vendor": pj.get("vendor") or "", "tags": pj.get("tags") or [],
                     "price": pj.get("price"), "compare_at": pj.get("compare_at_price") or 0,
                     "available": bool(pj.get("available")), "images": len(pj.get("images") or []),
                     "options": [{"name": o.get("name"), "values": o.get("values", [])} for o in opts if isinstance(o, dict)]}
        except Exception:
            h = self._get(url)
            og = lambda p: (re.search(r'<meta[^>]+property="og:%s"[^>]+content="([^"]*)"' % p, h) or [None, ""])[1]
            facts = {"handle": handle, "url": url, "title": html.unescape(og("title")).split(" – ")[0].split(" - ")[0],
                     "type": "", "vendor": "", "tags": [], "price": None, "compare_at": 0, "available": True,
                     "images": len(re.findall(r'og:image"', h)), "options": []}
        self._save(f"pages/{handle}.json", facts)
        return facts

    def search(self, ep, query):
        time.sleep(self.delay)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_catalog", "arguments": {
            "meta": {"ucp-agent": {"profile": PROFILE}}, "catalog": {"query": query, "pagination": {"limit": TOP_N}}}}}
        req = urllib.request.Request(ep, data=json.dumps(body).encode(), headers={"user-agent": UA, "content-type": "application/json"})
        res = json.load(urllib.request.urlopen(req, timeout=45))
        if "error" in res:
            raise SystemExit(f"UCP search failed for '{query}': {res['error'].get('message')} {res['error'].get('data', '')}")
        data = json.loads(res["result"]["content"][0]["text"])
        slim = {"products": [slim_product(p) for p in data.get("products", [])[:TOP_N]]}
        self._save(f"search/{slug(query)}.json", slim)
        return slim


class Offline:
    def __init__(self, root):
        self.root = root

    def _load(self, rel):
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            raise SystemExit(f"Missing fixture {path}")
        with open(path) as f:
            return json.load(f)

    def manifest(self):
        return self._load("manifest.json")

    def endpoint(self, manifest):
        return Live.endpoint(self, manifest)

    def page(self, url):
        return self._load(f"pages/{handle_of(url)}.json")

    def search(self, ep, query):
        return self._load(f"search/{slug(query)}.json")


# ---------------------------------------------------------------- helpers
def handle_of(url):
    m = re.search(r"/products/([^/?#]+)", url)
    if not m:
        raise SystemExit(f"Not a product URL (expected /products/<handle>): {url}")
    return m.group(1)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def plain(desc):
    if isinstance(desc, dict):
        desc = desc.get("plain") or desc.get("html") or ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(desc or ""))).strip()


def slim_product(p):
    media = p.get("media") or []
    return {"handle": p.get("handle"), "title": p.get("title"), "url": p.get("url"),
            "description": plain(p.get("description"))[:1200],
            "price": ((p.get("price_range") or {}).get("min") or {}).get("amount"),
            "currency": ((p.get("price_range") or {}).get("min") or {}).get("currency", "USD"),
            "list_price": ((p.get("list_price_range") or {}).get("min") or {}).get("amount") or 0,
            "media": [{"url": m.get("url", ""), "alt": m.get("alt_text") or ""} for m in media[:6]],
            "media_count": len(media),
            "categories": [c.get("value") if isinstance(c, dict) else c for c in (p.get("categories") or [])],
            "tags": p.get("tags") or [],
            "available": any(v.get("availability", {}).get("available", True) for v in (p.get("variants") or [{}]))}


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


def build_queries(facts, extra):
    title = facts["title"].strip()
    colors = set()
    for o in facts.get("options", []):
        if (o.get("name") or "").lower() in ("color", "colour", "shade"):
            colors |= {str(v).lower() for v in o.get("values", [])}
    words = title.split()
    name = title
    for k in range(len(words) - 1, 0, -1):
        if " ".join(words[k:]).lower() in colors:
            name = " ".join(words[:k])
            break
    q = [("exact", title)]
    if name.lower() != title.lower():
        q.append(("name", name))
    if facts.get("type"):
        q.append(("category", facts["type"].lower()))
    for text in extra:
        q.append(("custom", text))
    if not extra and facts.get("type"):
        q.append(("intent", f"best {facts['type'].lower()}"))
    seen, out = set(), []
    for kind, text in q:
        if text.lower() not in seen:
            seen.add(text.lower())
            out.append((kind, text))
    return out


# ---------------------------------------------------------------- scoring
def score_product(facts, runs, seen_hint=None):
    fw = sum(QUERY_WEIGHTS[r["kind"]] for r in runs)
    find = round(sum(QUERY_WEIGHTS[r["kind"]] * rank_points(r["position"]) for r in runs) / fw) if fw else 0
    seen = next((r["seen"] for r in runs if r["seen"]), None) or seen_hint
    fixes, data, match = [], 0, 0
    if seen:
        d = len(seen["description"])
        if d >= 150:
            data += 25
        elif d:
            data += 10; fixes.append(("Medium", f"Description agents receive is short ({d} chars); aim for 150+ with fit, material and use case"))
        else:
            fixes.append(("High", "Agents receive no product description"))
        mc = seen["media_count"]
        data += 25 if mc >= 3 else 10 if mc else 0
        if mc < 3:
            fixes.append(("Medium" if mc else "High", f"Agents receive {mc} image{'s' if mc != 1 else ''}; the product page has {facts.get('images', '?')}"))
        alts = [m for m in seen["media"] if m["alt"].strip()]
        if seen["media"] and len(alts) == len(seen["media"]):
            data += 15
        elif seen["media"]:
            data += 5; fixes.append(("Low", f"{len(seen['media']) - len(alts)} of {len(seen['media'])} images agents see have no alt text"))
        if seen["categories"]:
            data += 15
        else:
            fixes.append(("Medium", "No Shopify standard product category; set it in admin so agents can classify the product"))
        leaked = sorted({t for t in seen["tags"] if INTERNAL_TAG.search(t)})
        if not leaked:
            data += 20
        else:
            data += 5; fixes.append(("Low", f"{len(leaked)} internal tag{'s' if len(leaked) != 1 else ''} exposed to agents: {', '.join(leaked[:5])}"))
        if facts.get("price") is None or seen["price"] == facts["price"]:
            match += 50
        else:
            fixes.append(("High", f"Price mismatch: page {money(facts['price'])}, agents see {money(seen['price'], seen['currency'])}"))
        if (facts.get("compare_at") or 0) == (seen["list_price"] or 0) or not facts.get("compare_at"):
            match += 25
        else:
            fixes.append(("Medium", f"Compare-at price {money(facts['compare_at'])} on page is not shown to agents"))
        if seen["title"].strip().lower() == facts["title"].strip().lower():
            match += 25
        else:
            fixes.append(("Low", f"Title differs: page '{facts['title']}', agents see '{seen['title']}'"))
        data_pct, match_pct = data, match
    else:
        data_pct = match_pct = 0
        fixes.append(("Critical", "Product never appeared in any query, so agents may not be able to reach it at all; check it is published to the Shopify Catalog / Online Store channel"))
    for r in runs:
        if not r["position"] and r["kind"] in ("category", "intent", "custom"):
            fixes.append(("High", f"Not in the top {TOP_N} for '{r['query']}'; winners: {', '.join(t for t in r['top3'])}"))
        elif r["position"] and r["position"] > 10 and r["kind"] in ("category", "intent", "custom"):
            fixes.append(("Medium", f"Ranks #{r['position']} for '{r['query']}' (page 2 for an agent)"))
    total = round(find * 0.5 + data_pct * 0.3 + match_pct * 0.2)
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    fixes.sort(key=lambda f: order[f[0]])
    return {"score": total, "findability": find, "data": data_pct, "match": match_pct, "fixes": fixes, "seen": seen}


def rating(s):
    return "Strong" if s >= 80 else "Fair" if s >= 60 else "Weak" if s >= 40 else "Poor"


# ---------------------------------------------------------------- outputs
def text_summary(brand, domain, date, results):
    L = []
    avg = round(sum(r["score"]["score"] for r in results) / len(results))
    broad = [q for r in results for q in r["runs"] if q["kind"] in ("category", "intent", "custom")]
    found = sum(1 for q in broad if q["position"] and q["position"] <= 10)
    L.append(f"AEO check: {brand} ({domain}), {date}")
    L.append(f"AEO score: {avg}/100 ({rating(avg)}). Top-10 on {found} of {len(broad)} broad shopper queries across {len(results)} product{'s' if len(results) != 1 else ''}.")
    L.append("Source: Shopify UCP catalog search, anonymous agent tier. Not ChatGPT/Gemini final rankings.")
    for r in results:
        s = r["score"]
        L.append("")
        L.append(f"== {r['facts']['title']}  score {s['score']}/100  (find {s['findability']}, data {s['data']}, match {s['match']})")
        for q in r["runs"]:
            pos = f"#{q['position']}" if q["position"] else f"not in top {TOP_N}"
            L.append(f"  [{q['kind']:8}] {q['query']!r:48} {pos}")
        for sev, msg in s["fixes"]:
            L.append(f"  - {sev}: {msg}")
    return "\n".join(L) + "\n"


def report_md(brand, domain, date, results):
    avg = round(sum(r["score"]["score"] for r in results) / len(results))
    L = [f"## AEO check: {brand}, {date}", "",
         f"**AEO score: {avg}/100 ({rating(avg)}).** What Shopify's UCP catalog returns to AI shopping agents for {len(results)} product{'s' if len(results) != 1 else ''} on {domain}.", "",
         "| Product | Score | Findability | Agent data | Page match |", "| --- | ---: | ---: | ---: | ---: |"]
    for r in results:
        s = r["score"]
        L.append(f"| {r['facts']['title']} | **{s['score']}** | {s['findability']} | {s['data']} | {s['match']} |")
    L += ["", "| Product | Query | Type | Position |", "| --- | --- | --- | --- |"]
    for r in results:
        for q in r["runs"]:
            L.append(f"| {r['facts']['title']} | {q['query']} | {q['kind']} | {'#' + str(q['position']) if q['position'] else 'not in top ' + str(TOP_N)} |")
    L += ["", "**Fixes, highest priority first**", ""]
    for r in results:
        for sev, msg in r["score"]["fixes"]:
            L.append(f"- **{sev}** · {r['facts']['title']}: {msg}")
    L += ["", "_Method: queries are built from each product page (exact title, name without colour, product type, plus the brand's own shopper questions) and sent to the store's UCP `search_catalog` endpoint. "
          "This is what Shopify returns to agents; ChatGPT, Gemini and other assistants may re-rank, blend other stores or use other data. Scores are heuristics, not measured traffic._"]
    return "\n".join(L) + "\n"


CSS = """
*{box-sizing:border-box}body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#202124;background:#fff}
.top{display:flex;align-items:center;gap:28px;padding:20px 28px 0}
.logo{font-size:24px;font-weight:700;letter-spacing:-.5px}.logo b:nth-child(1){color:#4285f4}.logo b:nth-child(2){color:#ea4335}.logo b:nth-child(3){color:#fbbc05}.logo b:nth-child(4){color:#34a853}
.box{flex:1;max-width:690px;display:flex;align-items:center;border:1px solid #dfe1e5;border-radius:24px;padding:11px 20px;font-size:16px;box-shadow:0 1px 6px rgba(32,33,36,.18)}
.box svg{margin-right:12px;flex:none}
.tabs{display:flex;gap:8px;flex-wrap:wrap;padding:14px 28px 10px 180px;border-bottom:1px solid #ebebeb}
.tab{border:1px solid #dadce0;border-radius:18px;padding:6px 14px;font-size:13px;cursor:pointer;background:#fff;color:#3c4043}
.tab.on{background:#e8f0fe;border-color:#d2e3fc;color:#1967d2;font-weight:700}
.tab i{font-style:normal;margin-left:6px;padding:1px 7px;border-radius:9px;font-size:12px}
.ok{background:#e6f4ea;color:#137333}.mid{background:#fef7e0;color:#b06000}.no{background:#fce8e6;color:#c5221f}
.wrap{display:flex;gap:40px;padding:0 28px 60px 180px}.main{width:652px}.side{width:370px;padding-top:18px}
.meta{color:#70757a;font-size:14px;margin:14px 0 18px}
.disc{background:#f8f9fa;border:1px solid #e8eaed;border-radius:8px;padding:10px 14px;font-size:12.5px;color:#5f6368;margin-bottom:18px}
.miss{border:1px solid #f6aea9;background:#fce8e6;color:#a50e0e;border-radius:8px;padding:12px 14px;margin-bottom:18px;font-size:14px}
.r{display:flex;gap:16px;padding:14px 0;border-radius:10px;position:relative}
.r.me{background:#f1f8ff;outline:2px solid #1a73e8;padding:14px;margin:0 -14px}
.r .pos{position:absolute;left:-44px;top:16px;color:#9aa0a6;font-size:13px;width:32px;text-align:right}
.r.me .pos{left:-30px;color:#1a73e8;font-weight:700}
.crumb{font-size:12px;color:#4d5156;display:flex;align-items:center;gap:8px}.fav{width:26px;height:26px;border-radius:50%;background:#f1f3f4;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#5f6368;flex:none}
.crumb .site{font-size:14px;color:#202124}
.t{font-size:20px;color:#1a0dab;margin:4px 0 3px;line-height:1.3;text-decoration:none;display:block}.t:hover{text-decoration:underline}
.s{font-size:14px;line-height:1.58;color:#4d5156}.price{font-size:14px;color:#202124;margin-top:4px}.price s{color:#70757a;margin-left:6px}
.badge{display:inline-block;background:#1a73e8;color:#fff;font-size:11px;font-weight:700;border-radius:4px;padding:2px 6px;margin-left:6px;vertical-align:middle}
.th{width:104px;height:104px;border-radius:8px;object-fit:cover;flex:none;background:#f1f3f4;display:flex;align-items:center;justify-content:center;color:#9aa0a6;font-size:28px}
.card{border:1px solid #dadce0;border-radius:8px;padding:18px;margin-bottom:16px}
.card h3{margin:0 0 4px;font-size:18px;font-weight:400}.big{font-size:44px;font-weight:700;margin:6px 0}
.bar{display:flex;justify-content:space-between;font-size:13px;margin:8px 0 3px}.track{height:6px;background:#f1f3f4;border-radius:3px}.fill{height:6px;border-radius:3px}
.fix{font-size:13px;line-height:1.45;margin:8px 0;padding-left:10px;border-left:3px solid #dadce0}
.fix.Critical,.fix.High{border-color:#d93025}.fix.Medium{border-color:#f9ab00}.fix.Low{border-color:#9aa0a6}
.fix b{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#5f6368;margin-right:4px}
.q{display:none}.q.on{display:block}.prod{display:none}.prod.on{display:block}
.psel{padding:14px 28px 0 180px;font-size:13px;color:#5f6368}.psel select{font-size:14px;padding:5px 8px;border-radius:6px;border:1px solid #dadce0;margin-left:8px}
.tgc{display:flex;align-items:center;gap:6px;margin-top:4px;font-size:11px;color:#4d5156;text-decoration:none;white-space:nowrap}.tgc img{width:14px;height:14px;border-radius:50%;flex:none}.tgc:hover span{text-decoration:underline}
.foot{padding:18px 28px 28px 180px;border-top:1px solid #ebebeb;background:#f2f2f2;font-size:12px;color:#70757a}.foot .tgc{margin:0}
@media(max-width:1100px){.foot{padding-left:28px}}
@media(max-width:1100px){.wrap,.tabs,.psel{padding-left:28px}.wrap{flex-direction:column}.main,.side{width:auto}.r .pos{display:none}.r.me{margin:0}}
"""

JS = """
function showProd(i){document.querySelectorAll('.prod').forEach((e,j)=>e.classList.toggle('on',j==i));}
function showQ(p,i){const P=document.querySelectorAll('.prod')[p];P.querySelectorAll('.q').forEach((e,j)=>e.classList.toggle('on',j==i));P.querySelectorAll('.tab').forEach((e,j)=>e.classList.toggle('on',j==i));P.querySelector('.box span').textContent=P.querySelectorAll('.tab')[i].dataset.q;}
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def results_html(brand, domain, date, results):
    E = esc
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>AEO check: {E(brand)}</title><style>{CSS}</style></head><body>"]
    if len(results) > 1:
        out.append("<div class='psel'>Product<select onchange='showProd(this.value)'>" +
                   "".join(f"<option value='{i}'>{E(r['facts']['title'])}</option>" for i, r in enumerate(results)) + "</select></div>")
    for pi, r in enumerate(results):
        s, facts = r["score"], r["facts"]
        out.append(f"<section class='prod{' on' if pi == 0 else ''}'>")
        out.append(f"<div class='top'><div class='logo'><b>A</b><b>E</b><b>O</b><b>·</b> <span style='color:#5f6368;font-weight:400;font-size:15px'>agent view</span>"
                   f"<a class='tgc' href='{TGC_URL}' target='_blank' rel='noopener'><img src='{TGC_LOGO}' alt='The Growth Chef'><span>by The Growth Chef Marketing Group</span></a></div>"
                   f"<div class='box'><svg width='20' height='20' viewBox='0 0 24 24'><path fill='#9aa0a6' d='M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14'/></svg>"
                   f"<span>{E(r['runs'][0]['query'])}</span></div></div>")
        out.append("<div class='tabs'>")
        for qi, q in enumerate(r["runs"]):
            p = q["position"]
            cls, lab = ("ok", f"#{p}") if p and p <= 3 else ("mid", f"#{p}") if p and p <= 10 else ("no", f"#{p}" if p else "—")
            out.append(f"<span class='tab{' on' if qi == 0 else ''}' data-q='{E(q['query'])}' onclick='showQ({pi},{qi})'>{E(q['query'])}<i class='{cls}'>{lab}</i></span>")
        out.append("</div><div class='wrap'><div class='main'>")
        for qi, q in enumerate(r["runs"]):
            out.append(f"<div class='q{' on' if qi == 0 else ''}'><div class='meta'>{len(q['products'])} products returned to agents · {E(q['kind'])} query</div>")
            if qi == 0:
                out.append("<div class='disc'>This is what Shopify's UCP catalog returns to an AI shopping agent for this query (anonymous tier). "
                           "ChatGPT, Gemini and other assistants may re-rank, blend other stores or use other sources. Treat it as the shelf agents start from.</div>")
            if not q["position"]:
                out.append(f"<div class='miss'><b>{E(facts['title'])}</b> is not in the top {TOP_N} results agents get for “{E(q['query'])}”.</div>")
            for i, p in enumerate(q["products"][:10], 1):
                me = p["handle"] == facts["handle"]
                img = p["media"][0]["url"] if p["media"] else ""
                host = urllib.parse.urlparse(p.get("url") or f"https://{domain}").netloc or domain
                path = " › ".join(x for x in urllib.parse.urlparse(p.get("url") or "").path.split("/") if x)
                thumb = f"<img class='th' src='{E(img)}' alt='{E(p['media'][0]['alt'])}' loading='lazy'>" if img else f"<div class='th'>{E((p['title'] or '?')[0])}</div>"
                lp = f"<s>{money(p['list_price'], p['currency'])}</s>" if p.get("list_price") and p["list_price"] > (p["price"] or 0) else ""
                out.append(f"<div class='r{' me' if me else ''}'><div class='pos'>{i}</div><div style='flex:1;min-width:0'>"
                           f"<div class='crumb'><div class='fav'>{E(brand[:1])}</div><div><div class='site'>{E(brand)}</div>{E(host)}{' › ' + E(path) if path else ''}</div></div>"
                           f"<a class='t' href='{E(p.get('url'))}' target='_blank' rel='noopener'>{E(p['title'])}{'<span class=badge>YOUR PRODUCT</span>' if me else ''}</a>"
                           f"<div class='s'>{E(p['description'][:170])}{'…' if len(p['description']) > 170 else ''}</div>"
                           f"<div class='price'>{money(p['price'], p['currency'])}{lp}{'' if p.get('available', True) else ' · <span style=color:#c5221f>Out of stock</span>'}</div></div>{thumb}</div>")
            if q["position"] and q["position"] > 10:
                out.append(f"<div class='meta'>… your product appears at #{q['position']}, past the first 10 results.</div>")
            out.append("</div>")
        col = lambda v: "#34a853" if v >= 80 else "#f9ab00" if v >= 60 else "#ea4335"
        out.append(f"</div><div class='side'><div class='card'><h3>{E(facts['title'])}</h3><div style='color:#5f6368;font-size:13px'>{E(domain)} · {E(date)}</div>"
                   f"<div class='big' style='color:{col(s['score'])}'>{s['score']}<span style='font-size:18px;color:#5f6368;font-weight:400'>/100 · {rating(s['score'])}</span></div>")
        for lab, v, w in (("Findability", s["findability"], "50%"), ("Data agents see", s["data"], "30%"), ("Page ↔ agent match", s["match"], "20%")):
            out.append(f"<div class='bar'><span>{lab} <span style='color:#9aa0a6'>{w}</span></span><b>{v}</b></div><div class='track'><div class='fill' style='width:{v}%;background:{col(v)}'></div></div>")
        out.append("</div><div class='card'><h3>Fixes</h3>")
        for sev, msg in s["fixes"] or [("Low", "No issues found")]:
            out.append(f"<div class='fix {sev}'><b>{sev}</b>{E(msg)}</div>")
        out.append("</div></div></div></section>")
    out.append(f"<div class='foot'><a class='tgc' href='{TGC_URL}' target='_blank' rel='noopener'><img src='{TGC_LOGO}' alt='The Growth Chef'><span>AEO check by The Growth Chef Marketing Group · thegrowthchef.com</span></a></div>")
    out.append(f"<script>{JS}</script></body></html>")
    return "".join(out)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True, help="Storefront domain, e.g. www.brand.com")
    ap.add_argument("--brand", help="Display name (default: from domain)")
    ap.add_argument("--product", action="append", default=[], help="Product URL; repeat for several")
    ap.add_argument("--query", action="append", default=[], help="Shopper question applied to every product; repeatable")
    ap.add_argument("--input", help="JSON file: {brand, domain, products:[{url, queries:[...]}], queries:[...]}")
    ap.add_argument("--fixtures", help="Replay recorded responses from this folder (offline)")
    ap.add_argument("--record", help="Save live responses to this folder for later offline replay")
    ap.add_argument("--out", default="aeo-output", help="Output folder for report.md and results.html")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--max-products", type=int, default=20)
    ap.add_argument("--broad", action="store_true", help="Show and score only the shopper questions; exact-name lookup runs hidden just to read the agent data")
    a = ap.parse_args()

    products = [{"url": u, "queries": []} for u in a.product]
    global_q = list(a.query)
    brand = a.brand
    if a.input:
        with open(a.input) as f:
            cfg = json.load(f)
        products += cfg.get("products", [])
        global_q += cfg.get("queries", [])
        brand = brand or cfg.get("brand")
    if not products:
        raise SystemExit("Add at least one product URL with --product or --input.")
    if len(products) > a.max_products:
        raise SystemExit(f"{len(products)} products given; the anonymous UCP tier is rate-limited, so run at most {a.max_products} per check.")
    brand = brand or a.domain.replace("www.", "").split(".")[0].title()

    src = Offline(a.fixtures) if a.fixtures else Live(a.domain, a.record)
    global TGC_LOGO
    if not a.fixtures:
        try:
            import base64
            raw = urllib.request.urlopen(urllib.request.Request(TGC_LOGO_URL, headers={"user-agent": UA}), timeout=15).read()
            TGC_LOGO = "data:image/png;base64," + base64.b64encode(raw).decode()
        except Exception:
            pass
    ep = src.endpoint(src.manifest())
    results = []
    for p in products:
        facts = src.page(p["url"])
        runs, hint = [], None
        for kind, q in build_queries(facts, (p.get("queries") or []) + global_q):
            res = src.search(ep, q)
            prods = res.get("products", [])
            pos = next((i for i, x in enumerate(prods, 1) if x.get("handle") == facts["handle"]), None)
            run = {"kind": kind, "query": q, "position": pos, "products": prods,
                   "seen": prods[pos - 1] if pos else None, "top3": [x["title"] for x in prods[:3]]}
            if a.broad and kind not in ("custom", "intent"):
                hint = hint or run["seen"]
                continue
            runs.append(run)
        if not runs:
            raise SystemExit("--broad needs at least one --query shopper question.")
        results.append({"facts": facts, "runs": runs, "score": score_product(facts, runs, hint)})

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "report.md"), "w") as f:
        f.write(report_md(brand, a.domain, a.date, results))
    with open(os.path.join(a.out, "results.html"), "w") as f:
        f.write(results_html(brand, a.domain, a.date, results))
    sys.stdout.write(text_summary(brand, a.domain, a.date, results))


if __name__ == "__main__":
    main()
