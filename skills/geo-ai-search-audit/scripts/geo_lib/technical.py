"""On-page and rendering checks that decide whether AI systems can read a page.

Condensed from the technical checks in fetch_page.py and the geo-technical
guidance in zubair-trabzada/geo-seo-claude (MIT).
"""

from __future__ import annotations

SSR_MIN_WORDS = 200


def robots_directives(page, headers: dict[str, str]) -> set[str]:
    values = [page.meta.get("robots", ""), page.meta.get("googlebot", ""), headers.get("x-robots-tag", "")]
    tokens: set[str] = set()
    for value in values:
        for part in value.lower().replace(";", ",").split(","):
            part = part.strip()
            if part:
                tokens.add(part.replace(" ", ""))
    return tokens


def client_rendered(page) -> bool:
    return any(chars < 50 for _, chars in page.app_roots) and page.word_count < SSR_MIN_WORDS


def assess(home, home_headers: dict[str, str], pages: list, headers_by_url: dict[str, dict]) -> dict:
    directives = {p.url: sorted(robots_directives(p, headers_by_url.get(p.url, {}))) for p in pages}
    noindex = [u for u, d in directives.items() if "noindex" in d or "none" in d]
    nosnippet = [u for u, d in directives.items() if "nosnippet" in d or "max-snippet:0" in d]
    images = [img for p in pages for img in p.images]
    with_alt = sum(1 for img in images if img["alt"] is not None and img["alt"].strip())
    alt_coverage = round(100 * with_alt / len(images)) if images else 100
    question_headings = sum(1 for p in pages for _, text in p.headings if text.endswith("?"))

    checks = {
        "server_rendered_text": not client_rendered(home),
        "title": bool(home.title),
        "meta_description": bool(home.meta.get("description")),
        "canonical": bool(home.canonical),
        "single_h1": len(home.h1s) == 1,
        "html_lang": bool(home.lang),
        "indexable_and_snippetable": not noindex and not nosnippet,
        "open_graph": bool(home.meta.get("og:title") and home.meta.get("og:description")),
    }
    weights = {
        "server_rendered_text": 25, "title": 10, "meta_description": 10, "canonical": 5,
        "single_h1": 10, "html_lang": 5, "indexable_and_snippetable": 20, "open_graph": 5,
    }
    score = sum(weights[k] for k, ok in checks.items() if ok)
    score += round(10 * alt_coverage / 100)
    return {
        "homepage_word_count": home.word_count,
        "title": home.title,
        "meta_description": home.meta.get("description"),
        "h1_count": len(home.h1s),
        "checks": checks,
        "noindex_pages": noindex,
        "nosnippet_pages": nosnippet,
        "image_count": len(images),
        "alt_text_coverage_pct": alt_coverage,
        "question_headings": question_headings,
        "score": score,
    }
