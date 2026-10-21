"""AI crawler access inferred from robots.txt.

Upstream (fetch_page.py, geo-crawlers skill) matched user-agent groups by exact
string and only treated ``Disallow: /`` as blocking. This version follows the
RFC 9309 matching rules: grouped user-agent lines, case-insensitive product
tokens, longest-match precedence with Allow winning ties, and ``*`` / ``$``
wildcards. robots.txt is advisory: it shows what a site asks crawlers to do,
not what any crawler actually does.
"""

from __future__ import annotations

import re

# kind: "search_engine" (classic index that AI answers also draw on),
#       "ai_search" (fetches pages to answer or cite in an AI product),
#       "training" (model training / model-use control; blocking is a policy choice).
CRAWLERS = [
    ("Googlebot", "Google", "search_engine", "Google Search index; AI Overviews and AI Mode draw on it"),
    ("Bingbot", "Microsoft", "search_engine", "Bing index; Copilot and several AI assistants draw on it"),
    ("OAI-SearchBot", "OpenAI", "ai_search", "ChatGPT search results"),
    ("ChatGPT-User", "OpenAI", "ai_search", "Pages a ChatGPT user asks it to open"),
    ("Claude-SearchBot", "Anthropic", "ai_search", "Claude search results"),
    ("Claude-User", "Anthropic", "ai_search", "Pages a Claude user asks it to open"),
    ("PerplexityBot", "Perplexity", "ai_search", "Perplexity search index"),
    ("Perplexity-User", "Perplexity", "ai_search", "Pages a Perplexity user asks it to open"),
    ("GPTBot", "OpenAI", "training", "OpenAI model training"),
    ("ClaudeBot", "Anthropic", "training", "Anthropic model training"),
    ("Google-Extended", "Google", "training", "Gemini training/grounding use (does not affect Google Search)"),
    ("Applebot-Extended", "Apple", "training", "Apple AI model training"),
    ("Meta-ExternalAgent", "Meta", "training", "Meta AI model training"),
    ("CCBot", "Common Crawl", "training", "Open web dataset widely used for training"),
    ("Amazonbot", "Amazon", "training", "Amazon AI and Alexa features"),
    ("Bytespider", "ByteDance", "training", "ByteDance AI products"),
]

SCORE_POINTS = {"search_engine": 20, "ai_search": 10}  # 2*20 + 6*10 = 100


def parse_robots(text: str) -> tuple[list[dict], list[str]]:
    """Return (groups, sitemaps). Each group: {"agents": [...], "rules": [(directive, path)]}."""
    groups: list[dict] = []
    sitemaps: list[str] = []
    current: dict | None = None
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = {"agents": [], "rules": []}
                groups.append(current)
            current["agents"].append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key in ("allow", "disallow") and current is not None:
            current["rules"].append((key, value))
        elif key == "sitemap":
            sitemaps.append(value)
    return groups, sitemaps


def rules_for(groups: list[dict], token: str) -> tuple[list[tuple[str, str]], str]:
    token = token.lower()
    explicit = [g for g in groups if token in g["agents"]]
    if explicit:
        return [r for g in explicit for r in g["rules"]], "explicit"
    wildcard = [g for g in groups if "*" in g["agents"]]
    if wildcard:
        return [r for g in wildcard for r in g["rules"]], "wildcard"
    return [], "none"


def _pattern_matches(pattern: str, path: str) -> bool:
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


def is_allowed(rules: list[tuple[str, str]], path: str) -> bool:
    best_len, allowed = -1, True
    for directive, pattern in rules:
        if not pattern:  # "Disallow:" with no path allows everything
            continue
        if _pattern_matches(pattern, path):
            length = len(pattern)
            if length > best_len or (length == best_len and directive == "allow"):
                best_len, allowed = length, directive == "allow"
    return allowed


def assess(robots_status: int | None, robots_text: str, paths: list[str],
           robots_error: str | None = None) -> dict:
    """Assess every known crawler against robots.txt for the given site paths."""
    result = {
        "robots_txt": "missing" if robots_status == 404 else "present" if robots_status == 200 else "unavailable",
        "robots_status": robots_status,
        "sitemaps": [],
        "crawlers": [],
        "score": None,
        "note": None,
    }
    if robots_status not in (200, 404) and robots_status is not None and 400 <= robots_status < 500:
        result["robots_txt"] = "missing"  # RFC 9309: 4xx means no restrictions
    if robots_error and robots_error.startswith("not checked"):
        result["robots_txt"] = "not checked"
        return result
    if result["robots_txt"] == "unavailable":
        result["note"] = robots_error or f"robots.txt returned HTTP {robots_status}"
        return result

    groups, sitemaps = parse_robots(robots_text if result["robots_txt"] == "present" else "")
    result["sitemaps"] = sitemaps
    points = 0
    for token, operator, kind, purpose in CRAWLERS:
        rules, source = rules_for(groups, token)
        blocked_paths = [p for p in paths if not is_allowed(rules, p)]
        if not is_allowed(rules, "/"):
            status = "blocked"
        elif blocked_paths:
            status = "partial"
        else:
            status = "allowed"
        if result["robots_txt"] == "missing":
            source = "no robots.txt"
        entry = {
            "crawler": token,
            "operator": operator,
            "kind": kind,
            "purpose": purpose,
            "status": status,
            "rule_source": source,
            "blocked_audited_paths": blocked_paths,
        }
        result["crawlers"].append(entry)
        if kind in SCORE_POINTS:
            if status == "blocked":
                pass
            elif status == "partial":
                points += SCORE_POINTS[kind] // 2
            else:
                points += SCORE_POINTS[kind]
    result["score"] = points
    return result
