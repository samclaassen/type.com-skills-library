"""Render audit results as a client-ready DRAFT Markdown report or as JSON."""

from __future__ import annotations

import json

from . import llmstxt, schema_check
from .audit import LABELS, WEIGHTS

KIND_LABEL = {"search_engine": "Search index", "ai_search": "AI search / answers", "training": "Training / model use"}


def _score(value) -> str:
    return "n/a" if value is None else f"{value}/100"


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(client: dict, main: dict, competitors: list[dict], audit_date: str) -> str:
    name = client.get("name") or main["site_name"]
    out: list[str] = []
    w = out.append
    w(f"> **DRAFT for account-lead review.** Prepared for {name.rstrip('.')}. Not sent to the client. "
      "Check every finding before sharing.")
    w("")
    w(f"# AI Search Readiness (GEO) Audit: {name}")
    w("")
    w(f"- Site: {main['url']}")
    w(f"- Audit date: {audit_date}")
    w(f"- Fetch mode: {main['fetch_mode']}")
    w(f"- Pages analysed: {len([p for p in main['pages'] if p['status'] == 200])} of {len(main['pages'])} requested")
    for page in main["pages"]:
        w(f"  - {page['url']} (HTTP {page['status'] if page['status'] is not None else 'n/a'})")
    w("")
    w("## Summary")
    w("")
    w(f"**GEO readiness score: {_score(main['overall_score'])} ({main['rating']})** - a heuristic "
      "readiness score, not a measured AI ranking.")
    if main["not_assessed"]:
        w("")
        w("Not assessed in this run (excluded from the overall score): "
          + ", ".join(LABELS[k] for k in main["not_assessed"]) + ".")
    w("")
    w("| Area | Score | Weight |")
    w("|---|---|---|")
    for key in sorted(WEIGHTS, key=lambda k: -WEIGHTS[k]):
        w(f"| {LABELS[key]} | {_score(main['scores'][key])} | {WEIGHTS[key]}% |")
    w("")
    counts = {s: sum(1 for i in main["issues"] if i["severity"] == s) for s in ("Critical", "High", "Medium", "Low")}
    w("Issues found: " + ", ".join(f"{n} {s.lower()}" for s, n in counts.items()) + ".")
    w("")

    w("## Prioritized fix list")
    w("")
    if main["issues"]:
        w("Effort: S = under an hour, M = a day or less, L = a project.")
        w("")
        w("| # | Priority | Area | Finding | Recommended fix | Effort |")
        w("|---|---|---|---|---|---|")
        for n, issue in enumerate(main["issues"], 1):
            w(f"| {n} | {issue['severity']} | {issue['area']} | {_cell(issue['finding'])} | {_cell(issue['fix'])} | {issue['effort']} |")
    else:
        w("No issues found by the automated checks.")
    w("")

    if competitors:
        _benchmark(w, name, main, competitors)

    if main["_home"] is not None:
        _details(w, main)
        _appendices(w, main, name)

    w("## Method and limits")
    w("")
    w("- Scores are heuristics from the page HTML, robots.txt and llms.txt. They are not rankings, "
      "citation counts or traffic from ChatGPT, Perplexity, Gemini, Claude or Google AI features.")
    w("- Crawler access is inferred from robots.txt only. It shows what the site asks crawlers to do; "
      "firewalls, bot-management tools (e.g. CDN bot blocking) and crawler behaviour are not tested.")
    w("- Only the raw HTML is analysed. Content injected by JavaScript is not seen, which is also true for most AI crawlers.")
    w(f"- A sample of up to {len(main['pages'])} page(s) per site was analysed, not the whole site.")
    w("- Brand authority, third-party mentions, reviews and E-E-A-T are not measured by this audit.")
    w("- llms.txt is a community proposal; whether a given AI engine reads it is not confirmed.")
    w("")
    return "\n".join(out)


def _benchmark(w, name: str, main: dict, competitors: list[dict]) -> None:
    w("## Competitor benchmark")
    w("")
    w("Same checks, homepage-led sample, same day. Heuristic scores only.")
    w("")
    order = sorted(WEIGHTS, key=lambda k: -WEIGHTS[k])
    w("| Site | Overall | " + " | ".join(LABELS[k] for k in order) + " |")
    w("|---|---|" + "---|" * len(order))
    for label, site in [(f"{name} (client)", main)] + [(c["url"], c) for c in competitors]:
        w(f"| {_cell(label)} | {_score(site['overall_score'])} | "
          + " | ".join(_score(site["scores"][k]) for k in order) + " |")
    w("")
    gaps = []
    for key in order:
        mine = main["scores"][key]
        best = max(((c["scores"][key], c["url"]) for c in competitors if c["scores"][key] is not None), default=None)
        if mine is not None and best and best[0] > mine:
            gaps.append(f"- {LABELS[key]}: {best[1]} scores {best[0]} vs {mine} for {name}.")
    w("Where a competitor leads:" if gaps else "The client matches or leads every competitor on every assessed area.")
    if gaps:
        w("")
        for line in gaps:
            w(line)
    for comp in competitors:
        if comp["errors"]:
            w("")
            w(f"Note: {comp['errors'][0]}")
    w("")


def _details(w, main: dict) -> None:
    cats = main["categories"]
    w("## Details")
    w("")
    crawl = cats["crawler_access"]
    w("### AI crawler access (robots.txt)")
    w("")
    w(f"robots.txt: {crawl['robots_txt']}" + (f" ({crawl['note']})" if crawl["note"] else ""))
    if crawl["crawlers"]:
        w("")
        w("| Crawler | Operator | Used for | Type | Status | Rule |")
        w("|---|---|---|---|---|---|")
        for e in crawl["crawlers"]:
            w(f"| {e['crawler']} | {e['operator']} | {e['purpose']} | {KIND_LABEL[e['kind']]} | {e['status']} | {e['rule_source']} |")
        w("")
        blocked_training = [e["crawler"] for e in crawl["crawlers"] if e["kind"] == "training" and e["status"] == "blocked"]
        w("Training crawlers are not scored: blocking them is a policy choice for the client and does not remove the site from AI search results."
          + (f" Currently blocked: {', '.join(blocked_training)}." if blocked_training else ""))
    w("")

    cit = cats["citability"]
    w("### Content citability")
    w("")
    w(f"{cit['blocks_analyzed']} passage(s) scored; average {cit['average_score']}/100; "
      f"{cit['optimal_length_blocks']} in the 134-167 word range. Grades: "
      + ", ".join(f"{g} {n}" for g, n in cit["grade_distribution"].items()) + ".")
    for title, key in (("Strongest passages", "strongest"), ("Weakest passages (rewrite first)", "weakest")):
        if cit[key]:
            w("")
            w(f"{title}:")
            w("")
            for b in cit[key]:
                w(f"- {b['total_score']}/100 ({b['grade']}) \"{b['heading']}\" on {b['page']} - {b['word_count']} words: {b['preview']}")
    w("")

    sd = cats["structured_data"]
    w("### Structured data")
    w("")
    w(f"JSON-LD blocks: {sd['json_ld_blocks']} ({sd['invalid_json_ld_blocks']} invalid). "
      f"Microdata: {'yes' if sd['microdata'] else 'no'}. RDFa: {'yes' if sd['rdfa'] else 'no'}.")
    w("")
    if sd["types_found"]:
        for type_name, urls in sd["types_found"].items():
            w(f"- {type_name}: {', '.join(urls)}")
    else:
        w("- No schema.org types found.")
    w("")

    llms = cats["llms_txt"]
    w("### llms.txt")
    w("")
    w(f"/llms.txt: {llms['status']}; /llms-full.txt: {llms['llms_full_txt']}.")
    if llms["status"] == "present":
        w(f"Sections: {llms['section_count']}; links: {llms['link_count']}; format valid: {'yes' if llms['format_valid'] else 'no'}.")
        for s in llms["issues"] + llms["suggestions"]:
            w(f"- {s}")
    w("")

    tech = cats["technical"]
    w("### Technical / on-page (homepage)")
    w("")
    w(f"- Title: {tech['title'] or 'missing'}")
    w(f"- Meta description: {tech['meta_description'] or 'missing'}")
    w(f"- Words in raw HTML: {tech['homepage_word_count']}; H1 count: {tech['h1_count']}; "
      f"images with alt text: {tech['alt_text_coverage_pct']}% of {tech['image_count']}")
    for check, ok in tech["checks"].items():
        w(f"- {check.replace('_', ' ')}: {'pass' if ok else 'FAIL'}")
    w("")


def _appendices(w, main: dict, name: str) -> None:
    if main["categories"]["llms_txt"]["status"] != "present" or not main["categories"]["llms_txt"]["format_valid"]:
        w("## Appendix A: draft llms.txt")
        w("")
        w("Generated from homepage links. Replace every TODO before publishing at /llms.txt.")
        if main.get("_links_failed"):
            w("Left out because they did not load during this audit: "
              + ", ".join(f"{u} (HTTP {s if s is not None else 'n/a'})" for u, s in main["_links_failed"])
              + ". Links the audit did not sample were not checked; open each one before publishing.")
        w("")
        w("```markdown")
        w(llmstxt.generate_draft(name, main["description"], main["url"], main["_links"]).rstrip())
        w("```")
        w("")
    types = set(main["categories"]["structured_data"]["types_found"])
    if not types & schema_check.ORG_TYPES:
        w("## Appendix B: draft Organization JSON-LD")
        w("")
        w("Place in a <script type=\"application/ld+json\"> tag site-wide. Replace every TODO; "
          "sameAs was prefilled from profile links found on the site.")
        w("")
        w("```json")
        w(schema_check.draft_organization(name, main["url"], main["description"],
                                          main["categories"]["structured_data"]["social_links_on_site"]))
        w("```")
        w("")


def render_json(client: dict, main: dict, competitors: list[dict], audit_date: str) -> str:
    def clean(site: dict) -> dict:
        return {k: v for k, v in site.items() if not k.startswith("_")}

    payload = {
        "draft": True,
        "note": "DRAFT for account-lead review. Heuristic scores, not measured AI rankings.",
        "client": {"name": client.get("name"), "slug": client.get("_slug")},
        "audit_date": audit_date,
        "site": clean(main),
        "competitors": [clean(c) for c in competitors],
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)
