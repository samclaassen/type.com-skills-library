"""Run one site audit: fetch pages, run every check, score, and list issues."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from . import citability, crawlers, llmstxt, schema_check, technical
from .html_extract import extract

WEIGHTS = {
    "citability": 30,
    "crawler_access": 25,
    "structured_data": 20,
    "technical": 15,
    "llms_txt": 10,
}
LABELS = {
    "citability": "Content citability",
    "crawler_access": "AI crawler access",
    "structured_data": "Structured data",
    "technical": "Technical / on-page",
    "llms_txt": "llms.txt",
}
PRIORITY_PATHS = ("about", "pricing", "product", "service", "solution", "faq", "blog", "guide", "shop", "collections")
UTILITY_PATH = re.compile(r"/(cart|checkout|account|login|log-in|signin|sign-in|register|search|wishlist)(/|$)", re.I)
SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def rating(score: float | None) -> str:
    if score is None:
        return "Not assessed"
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    if score >= 40:
        return "Poor"
    return "Critical"


def normalize_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def content_links(home, robots_rules) -> list[tuple[str, str]]:
    """Internal links worth sampling or listing: crawlable, not the homepage, not cart/login pages."""
    links = [(u, t) for u, t in home.internal_links() if u.rstrip("/") != home.url.rstrip("/")]
    return [
        (u, t) for u, t in links
        if crawlers.is_allowed(robots_rules, urlparse(u).path or "/") and not UTILITY_PATH.search(urlparse(u).path)
    ]


def _pick_pages(home, max_pages: int, robots_rules) -> list[str]:
    links = content_links(home, robots_rules)

    def rank(item):
        path = urlparse(item[0]).path.lower()
        hits = [i for i, key in enumerate(PRIORITY_PATHS) if key in path]
        return hits[0] if hits else len(PRIORITY_PATHS)

    ordered = sorted(enumerate(links), key=lambda pair: (rank(pair[1]), pair[0]))
    return [u for _, (u, _) in ordered][: max(0, max_pages - 1)]


def audit_site(url: str, fetcher, max_pages: int = 5) -> dict:
    home_url = normalize_url(url)
    parsed = urlparse(home_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    result: dict = {"url": home_url, "fetch_mode": fetcher.mode, "pages": [], "errors": []}

    home_resp = fetcher.get(home_url)
    robots_resp = fetcher.get(f"{root}/robots.txt")
    llms_resp = fetcher.get(f"{root}/llms.txt")
    llms_full_resp = fetcher.get(f"{root}/llms-full.txt")

    groups, _ = crawlers.parse_robots(robots_resp.text if robots_resp.ok else "")
    our_rules, _ = crawlers.rules_for(groups, "GEO-AI-Search-Audit")

    home = None
    if home_resp.ok:
        home = extract(home_resp.text, home_resp.final_url)
        result["pages"].append({"url": home_resp.final_url, "status": 200})
    else:
        result["errors"].append(
            f"Homepage {home_url} could not be fetched: "
            + (home_resp.error or f"HTTP {home_resp.status}")
        )
        result["pages"].append({"url": home_url, "status": home_resp.status})

    pages = [home] if home else []
    headers = {home.url: home_resp.headers} if home else {}
    if home:
        for page_url in _pick_pages(home, max_pages, our_rules):
            resp = fetcher.get(page_url)
            result["pages"].append({"url": page_url, "status": resp.status})
            if resp.ok:
                page = extract(resp.text, resp.final_url)
                pages.append(page)
                headers[page.url] = resp.headers

    audited_paths = sorted({urlparse(p.url).path or "/" for p in pages} | {"/"})
    crawl = crawlers.assess(robots_resp.status, robots_resp.text, audited_paths, robots_resp.error)
    llms = llmstxt.validate(llms_resp.status, llms_resp.text, llms_full_resp.status)

    categories: dict[str, dict] = {"crawler_access": crawl, "llms_txt": llms}
    if home:
        categories["citability"] = citability.analyze_blocks(pages)
        categories["citability"]["score"] = round(categories["citability"]["average_score"])
        categories["structured_data"] = schema_check.assess(pages)
        categories["technical"] = technical.assess(home, home_resp.headers, pages, headers)
    else:
        for key in ("citability", "structured_data", "technical"):
            categories[key] = {"score": None}

    assessed = {k: v["score"] for k, v in categories.items() if v.get("score") is not None}
    total_weight = sum(WEIGHTS[k] for k in assessed)
    overall = round(sum(WEIGHTS[k] * s for k, s in assessed.items()) / total_weight) if total_weight else None

    # Pages this audit fetched and saw fail must not be offered in the draft llms.txt.
    failed = {p["url"].rstrip("/"): p["status"] for p in result["pages"][1:] if p["status"] != 200}
    links = content_links(home, our_rules) if home else []
    draft_links = [(u, t) for u, t in links if u.rstrip("/") not in failed]
    dropped = sorted({(u, failed[u.rstrip("/")]) for u, _ in links if u.rstrip("/") in failed},
                     key=lambda item: item[0])

    result.update({
        "site_name": _site_name(home, parsed.netloc),
        "description": home.meta.get("description") if home else None,
        "categories": categories,
        "scores": {k: categories[k].get("score") for k in WEIGHTS},
        "overall_score": overall,
        "rating": rating(overall),
        "not_assessed": [k for k in WEIGHTS if categories[k].get("score") is None],
        "_home": home,
        "_links": draft_links,
        "_links_failed": dropped,
        "_pages": pages,
    })
    result["issues"] = build_issues(result)
    return result


def _site_name(home, host: str) -> str:
    if home:
        for key in ("og:site_name", "application-name"):
            if home.meta.get(key):
                return home.meta[key].strip()
        if home.title:
            for sep in (" | ", " – ", " — ", " - ", " · "):
                if sep in home.title:
                    return home.title.split(sep)[0].strip()
            return home.title.strip()
    return host


def build_issues(result: dict) -> list[dict]:
    issues: list[dict] = []

    def add(severity, area, finding, fix, effort):
        issues.append({"severity": severity, "area": area, "finding": finding, "fix": fix, "effort": effort})

    home = result["_home"]
    cats = result["categories"]
    if home is None:
        add("Critical", "Technical / on-page", result["errors"][0],
            "Confirm the site is reachable without login, bot challenge, or geo-block; rerun the audit.", "S")
        return issues

    broken = [p for p in result["pages"][1:] if p["status"] != 200]
    if broken:
        add("Medium", "Technical / on-page",
            "Linked pages did not load: " + ", ".join(f"{p['url']} (HTTP {p['status'] if p['status'] is not None else 'n/a'})" for p in broken) + ".",
            "Fix or remove the broken links; AI crawlers and users hit the same dead ends.", "S")

    # Crawler access
    crawl = cats["crawler_access"]
    if crawl["robots_txt"] == "unavailable":
        add("High", "AI crawler access", f"robots.txt could not be read ({crawl['note']}). Many crawlers treat an erroring robots.txt as 'do not crawl'.",
            "Make /robots.txt return 200 (or 404 if intentionally absent).", "S")
    for entry in crawl["crawlers"]:
        if entry["kind"] == "search_engine" and entry["status"] == "blocked":
            add("Critical", "AI crawler access", f"{entry['crawler']} is blocked from the whole site in robots.txt ({entry['rule_source']} rule).",
                f"Remove the site-wide Disallow for {entry['crawler']}; it feeds {entry['purpose'].split(';')[-1].strip()}.", "S")
    blocked_ai = [e["crawler"] for e in crawl["crawlers"] if e["kind"] == "ai_search" and e["status"] == "blocked"]
    partial_ai = [e["crawler"] for e in crawl["crawlers"] if e["kind"] in ("ai_search", "search_engine") and e["status"] == "partial"]
    if blocked_ai:
        add("High", "AI crawler access", f"AI search/answer crawlers blocked site-wide: {', '.join(blocked_ai)}.",
            "Allow these user agents in robots.txt unless the client has decided to opt out of AI answers.", "S")
    if partial_ai:
        add("Medium", "AI crawler access", f"Some audited pages are disallowed for: {', '.join(partial_ai)}.",
            "Check the disallowed paths are intentional; key product, service and FAQ pages should be crawlable.", "S")

    # Technical
    tech = cats["technical"]
    checks = tech["checks"]
    if tech["noindex_pages"]:
        add("Critical", "Technical / on-page", f"noindex on {len(tech['noindex_pages'])} audited page(s), e.g. {tech['noindex_pages'][0]}.",
            "Remove noindex (meta robots or X-Robots-Tag) from pages that should appear in search and AI answers.", "S")
    if not checks["server_rendered_text"]:
        add("Critical", "Technical / on-page", f"Homepage looks client-rendered: only {tech['homepage_word_count']} words in the raw HTML.",
            "Server-render or prerender primary content; most AI crawlers do not execute JavaScript.", "L")
    if tech["nosnippet_pages"]:
        add("High", "Technical / on-page", f"nosnippet / max-snippet:0 on {len(tech['nosnippet_pages'])} audited page(s).",
            "Remove nosnippet where the client wants to be quoted in AI Overviews and answer engines.", "S")
    if not checks["meta_description"]:
        add("Medium", "Technical / on-page", "Homepage has no meta description.",
            "Add a factual 140-160 character description of what the business does and for whom.", "S")
    if not checks["single_h1"]:
        add("Medium", "Technical / on-page", f"Homepage has {tech['h1_count']} H1 headings (expected 1).",
            "Use exactly one H1 that names the core offering.", "S")
    if not checks["title"]:
        add("High", "Technical / on-page", "Homepage has no <title>.", "Add a descriptive title tag.", "S")
    if not checks["canonical"]:
        add("Low", "Technical / on-page", "Homepage has no canonical link.", "Add <link rel=\"canonical\">.", "S")
    if not checks["html_lang"]:
        add("Low", "Technical / on-page", "<html> has no lang attribute.", "Set lang (e.g. en-US).", "S")
    if tech["alt_text_coverage_pct"] < 90:
        add("Low", "Technical / on-page", f"Only {tech['alt_text_coverage_pct']}% of {tech['image_count']} images have alt text.",
            "Add descriptive alt text to content images.", "M")
    if not checks["open_graph"]:
        add("Low", "Technical / on-page", "Homepage is missing og:title or og:description.", "Add Open Graph tags.", "S")

    # Structured data
    sd = cats["structured_data"]
    types = set(sd["types_found"])
    if sd["invalid_json_ld_blocks"]:
        add("High", "Structured data", f"{sd['invalid_json_ld_blocks']} JSON-LD block(s) fail to parse.",
            "Fix the JSON syntax; invalid blocks are ignored entirely.", "S")
    if not types and not (sd["microdata"] or sd["rdfa"]):
        add("High", "Structured data", "No structured data found on any audited page.",
            "Add Organization and WebSite JSON-LD site-wide (draft in Appendix B), then page-type schema.", "M")
    elif not types:
        add("Medium", "Structured data", "Only microdata/RDFa found, no JSON-LD.",
            "Add JSON-LD equivalents; JSON-LD is the most reliably parsed format.", "M")
    if types and not types & schema_check.ORG_TYPES:
        add("High", "Structured data", "No Organization (or LocalBusiness) entity in structured data.",
            "Add an Organization block with name, url, logo and sameAs (draft in Appendix B).", "S")
    elif types & schema_check.ORG_TYPES and len(sd["organization_same_as"]) < 2:
        add("Medium", "Structured data", f"Organization has {len(sd['organization_same_as'])} sameAs link(s).",
            "List official profiles (LinkedIn, YouTube, Wikipedia/Wikidata, Crunchbase, social) in sameAs.", "S")
    for item in sd["missing_required"][:5]:
        add("Medium", "Structured data", f"{item['type']} is missing {', '.join(item['missing'])} on {len(item['pages'])} page(s), e.g. {item['pages'][0]}.",
            "Fill in the missing properties.", "S")
    if types and not types & schema_check.OFFERING_TYPES:
        add("Medium", "Structured data", "No Product, Service, SoftwareApplication, Article or FAQPage schema on audited pages.",
            "Mark up the main offering pages and any FAQ content (see templates/schema/).", "M")
    if types and "WebSite" not in types:
        add("Low", "Structured data", "No WebSite entity.", "Add WebSite JSON-LD with name and url.", "S")

    # Citability
    cit = cats["citability"]
    if cit["blocks_analyzed"] == 0:
        add("High", "Content citability", "No passages of 20+ words were found on audited pages.",
            "Add substantive, answer-first copy to key pages (what it is, who it is for, proof points).", "L")
    elif cit["average_score"] < 35:
        add("High", "Content citability", f"Average passage citability is {cit['average_score']}/100.",
            "Rewrite key sections answer-first: lead with a one-sentence definition or claim, add specific numbers and named sources.", "M")
    elif cit["average_score"] < 50:
        add("Medium", "Content citability", f"Average passage citability is {cit['average_score']}/100.",
            "Tighten the weakest passages (listed below) into self-contained, fact-rich answers.", "M")
    if tech["question_headings"] == 0:
        add("Low", "Content citability", "No question-style headings on audited pages.",
            "Add an FAQ or question headings that mirror how buyers ask AI assistants.", "M")

    # llms.txt
    llms = cats["llms_txt"]
    if llms["status"] == "missing":
        add("Low", "llms.txt", "No /llms.txt file.",
            "Optional, low-cost: publish the draft in Appendix A. Engine support for llms.txt is not confirmed.", "S")
    elif llms["status"] == "present" and llms["issues"]:
        add("Low", "llms.txt", "llms.txt format problems: " + "; ".join(llms["issues"]) + ".",
            "Follow the llmstxt.org format (title, > description, ## sections, - [page](url): note).", "S")

    area_rank = {LABELS[k]: i for i, k in enumerate(sorted(WEIGHTS, key=lambda k: -WEIGHTS[k]))}
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], area_rank.get(i["area"], 9)))
    return issues
