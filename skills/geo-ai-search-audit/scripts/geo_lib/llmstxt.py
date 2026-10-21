"""llms.txt validation and draft generation.

Adapted from llmstxt_generator.py in zubair-trabzada/geo-seo-claude (MIT).
Changes: works on already-fetched content (no network calls of its own), the
draft generator no longer invents a contact@<domain> address, and the
llms-full.txt generator (which fetched every linked page) was removed.
llms.txt is a community proposal (llmstxt.org); support by AI engines varies
and is not confirmed for most of them.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

SECTION_RULES = [
    ("Products & Services", ["/pricing", "/feature", "/product", "/solution", "/demo", "/shop", "/collections", "/service"]),
    ("Resources & Blog", ["/blog", "/article", "/resource", "/guide", "/learn", "/docs", "/documentation", "/journal"]),
    ("Company", ["/about", "/team", "/career", "/contact", "/press", "/partner", "/story"]),
    ("Support", ["/help", "/support", "/faq", "/status", "/shipping", "/returns"]),
]
SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js", ".zip")


def validate(status: int | None, content: str, full_status: int | None = None) -> dict:
    result = {
        "status": "present" if status == 200 else "missing" if status is not None else "not checked",
        "format_valid": False,
        "has_title": False,
        "has_description": False,
        "section_count": 0,
        "link_count": 0,
        "issues": [],
        "suggestions": [],
        "llms_full_txt": "present" if full_status == 200 else "missing" if full_status is not None else "not checked",
        "score": None,
    }
    if status != 200:
        result["score"] = 0 if status is not None else None
        return result
    lines = content.strip().splitlines()
    result["has_title"] = bool(lines) and lines[0].startswith("# ")
    if not result["has_title"]:
        result["issues"].append("First line is not a '# Site Name' title")
    result["has_description"] = any(line.startswith("> ") for line in lines)
    if not result["has_description"]:
        result["issues"].append("No '> short description' blockquote")
    sections = [line for line in lines if line.startswith("## ")]
    result["section_count"] = len(sections)
    if not sections:
        result["issues"].append("No '## Section' headings")
    result["link_count"] = len(re.findall(r"- \[.+?\]\(.+?\)", content))
    if not result["link_count"]:
        result["issues"].append("No '- [Page](url): description' links")
    result["format_valid"] = not result["issues"]
    if result["link_count"] < 5:
        result["suggestions"].append("List more key pages (aim for 10-20)")
    if result["section_count"] < 2:
        result["suggestions"].append("Group pages into at least two sections")
    described = len(re.findall(r"- \[.+?\]\(.+?\):\s*\S", content))
    if result["link_count"] and described < result["link_count"]:
        result["suggestions"].append(
            f"{result['link_count'] - described} of {result['link_count']} links have no ': description'"
        )
    score = 40
    score += 15 if result["has_title"] else 0
    score += 15 if result["has_description"] else 0
    score += 10 if result["section_count"] else 0
    score += 10 if result["link_count"] >= 5 else 5 if result["link_count"] else 0
    score += 10 if result["link_count"] and described == result["link_count"] else 0
    result["score"] = score
    return result


def generate_draft(site_name: str, description: str | None, home_url: str,
                   internal_links: list[tuple[str, str]], max_links: int = 30) -> str:
    """Build a draft llms.txt from the homepage's internal links."""
    base = urlparse(home_url)
    sections: dict[str, list[tuple[str, str]]] = {"Main Pages": []}
    for name, _ in SECTION_RULES:
        sections[name] = []
    count = 0
    for url, text in internal_links:
        if not text or len(text) < 2 or url.lower().endswith(SKIP_EXTENSIONS):
            continue
        path = urlparse(url).path.lower()
        if path in ("", "/"):
            continue
        for name, keywords in SECTION_RULES:
            if any(k in path for k in keywords):
                sections[name].append((text, url))
                break
        else:
            sections["Main Pages"].append((text, url))
        count += 1
        if count >= max_links:
            break
    lines = [f"# {site_name}", "", f"> {description or 'TODO: one factual sentence on what the business does and for whom.'}", ""]
    for name, entries in sections.items():
        if not entries:
            continue
        lines.append(f"## {name}")
        lines.append("")
        for text, url in entries[:10]:
            lines.append(f"- [{text}]({url}): TODO one-line description")
        lines.append("")
    lines += ["## Contact", "", f"- [Website]({base.scheme}://{base.netloc}/): TODO add the public contact route", ""]
    return "\n".join(lines)
