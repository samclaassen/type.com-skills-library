"""Schema.org structured-data checks for AI discoverability.

Condenses the geo-schema skill from zubair-trabzada/geo-seo-claude (MIT) into
deterministic checks: JSON-LD detection (including @graph), microdata/RDFa
detection, required-property checks for the entity types that matter most for
AI answers, sameAs coverage, and a draft Organization block. Full reference
templates live in templates/schema/ (copied verbatim from upstream).
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

ORG_TYPES = {
    "Organization", "Corporation", "LocalBusiness", "OnlineBusiness", "OnlineStore",
    "Store", "ProfessionalService", "NGO", "EducationalOrganization", "MedicalOrganization",
}
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "TechArticle"}
OFFERING_TYPES = {"Product", "SoftwareApplication", "WebApplication", "Service"} | ARTICLE_TYPES | {"FAQPage"}

REQUIRED = {
    "Organization": ["name", "url", "logo"],
    "LocalBusiness": ["name", "address", "telephone"],
    "WebSite": ["name", "url"],
    "Article": ["headline", "author", "datePublished", "dateModified", "publisher", "image"],
    "Product": ["name", "image", "description", "offers", "brand"],
    "SoftwareApplication": ["name", "offers", "applicationCategory", "operatingSystem"],
    "FAQPage": ["mainEntity"],
    "BreadcrumbList": ["itemListElement"],
}
SOCIAL_HOSTS = (
    "linkedin.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com",
    "tiktok.com", "pinterest.com", "github.com", "crunchbase.com", "wikipedia.org", "wikidata.org",
)


def _types(entity: dict) -> list[str]:
    raw = entity.get("@type", [])
    values = raw if isinstance(raw, list) else [raw]
    return [str(v).rsplit("/", 1)[-1] for v in values if v]


def _required_key(type_name: str) -> str | None:
    if type_name in ARTICLE_TYPES:
        return "Article"
    if type_name == "LocalBusiness":
        return "LocalBusiness"
    if type_name in ORG_TYPES:
        return "Organization"
    if type_name == "WebApplication":
        return "SoftwareApplication"
    return type_name if type_name in REQUIRED else None


def _social_links(pages: list) -> list[str]:
    found: list[str] = []
    for page in pages:
        for href, _ in page.links:
            host = urlparse(href).netloc.lower()
            if any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS) and href not in found:
                found.append(href)
    return found


def assess(pages: list) -> dict:
    types_found: dict[str, list[str]] = {}
    missing_props: list[dict] = []
    invalid_blocks = 0
    org_entity: dict | None = None
    microdata = any(p.has_microdata for p in pages)
    rdfa = any(p.has_rdfa for p in pages)
    json_ld_blocks = sum(len(p.json_ld_raw) for p in pages)

    for page in pages:
        entities, invalid = page.json_ld()
        invalid_blocks += invalid
        for entity in entities:
            for type_name in _types(entity):
                types_found.setdefault(type_name, [])
                if page.url not in types_found[type_name]:
                    types_found[type_name].append(page.url)
                key = _required_key(type_name)
                if key and org_entity is None and key in ("Organization", "LocalBusiness"):
                    org_entity = entity
                if key:
                    absent = [prop for prop in REQUIRED[key] if not entity.get(prop)]
                    if absent:
                        match = next((m for m in missing_props
                                      if m["type"] == type_name and m["missing"] == absent), None)
                        if match is None:
                            missing_props.append({"type": type_name, "pages": [page.url], "missing": absent})
                        elif page.url not in match["pages"]:
                            match["pages"].append(page.url)

    names = set(types_found)
    same_as = []
    if org_entity:
        raw = org_entity.get("sameAs", [])
        same_as = raw if isinstance(raw, list) else [raw]

    score = 0
    if json_ld_blocks - invalid_blocks > 0:
        score += 15
    elif microdata or rdfa:
        score += 10
    if names & ORG_TYPES:
        score += 25
        required = REQUIRED["Organization"]
        score += round(10 * sum(1 for p in required if org_entity and org_entity.get(p)) / len(required))
        score += 10 if len(same_as) >= 2 else 5 if same_as else 0
    if "WebSite" in names:
        score += 10
    if names & OFFERING_TYPES:
        score += 15
    if "BreadcrumbList" in names:
        score += 5
    if names and not invalid_blocks and not missing_props:
        score += 10

    return {
        "json_ld_blocks": json_ld_blocks,
        "invalid_json_ld_blocks": invalid_blocks,
        "microdata": microdata,
        "rdfa": rdfa,
        "types_found": {k: types_found[k] for k in sorted(types_found)},
        "organization_same_as": same_as,
        "missing_required": missing_props,
        "social_links_on_site": _social_links(pages),
        "score": min(score, 100),
    }


def draft_organization(name: str, home_url: str, description: str | None, same_as: list[str]) -> str:
    parsed = urlparse(home_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    block = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{root}/#organization",
        "name": name,
        "url": f"{root}/",
        "logo": f"{root}/TODO-logo.png",
        "description": description or "TODO: one factual sentence on what the business does.",
        "sameAs": same_as or ["TODO: LinkedIn, YouTube, Wikipedia/Wikidata and other official profiles"],
    }
    return json.dumps(block, indent=2)
