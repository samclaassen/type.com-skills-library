#!/usr/bin/env python3
"""Write the fictional Northfield Knit fixtures used by examples/run.sh (offline demo)."""
import json, os, re, sys

R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
D = "northfield-knit.example"
CAT = "gid://shopify/TaxonomyCategory/aa-1-13-12"


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:80]


def dump(rel, obj):
    path = os.path.join(R, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)


def P(h, t, price, desc, alts, tags, cats=True):
    return {"handle": h, "title": t, "url": f"https://{D}/products/{h}", "description": desc, "price": price,
            "currency": "USD", "list_price": 0, "media": [{"url": "", "alt": a} for a in alts],
            "media_count": len(alts), "categories": [CAT] if cats else [], "tags": tags, "available": True}


CREW_DESC = ("Midweight 100% merino crew knit in Maine. Soft enough to wear next to skin, warm without bulk, "
             "and machine washable on wool cycle. Relaxed fit; size down for a closer cut.")
crew = P("harbor-merino-crew-oatmeal", "Harbor Merino Crew Oatmeal", 9800, CREW_DESC,
         ["Oatmeal merino crew sweater, front", ""], ["winter", "merino", "klaviyo_flow_b", "BFCM24", "feed-exclude-meta"])
navy = P("harbor-merino-crew-navy", "Harbor Merino Crew Navy", 9800, CREW_DESC,
         ["Navy merino crew, front", "Navy crew, back", "Detail of rib cuff"], ["merino"])
card = P("ridgeline-cardigan-charcoal", "Ridgeline Cardigan Charcoal", 14800, "Chunky shawl-collar cardigan.",
         ["Charcoal cardigan"], ["cardigan", "gift"], cats=False)
fish = P("fisherman-rollneck-cream", "Fisherman Rollneck Cream", 13200,
         "Heritage fisherman rollneck in undyed cream wool with cable panels. Dense knit that blocks wind on the water and softens with wear.",
         ["Cream fisherman sweater", "Cable detail", "On model"], ["heritage"])
qzip = P("trailhead-quarter-zip-moss", "Trailhead Quarter Zip Moss", 11200,
         "Merino-blend quarter zip with a stand collar and YKK zip. Breathable for hikes, polished enough for the office, packs flat for travel.",
         ["Moss quarter zip", "Collar detail", "On model"], ["travel"])
beanie = P("dockside-beanie-rust", "Dockside Beanie Rust", 3800, "Ribbed merino beanie with a fold-over cuff.",
           ["Rust beanie", "On model", "Flat lay"], ["accessories"])
vneck = P("lambswool-vneck-heather", "Lambswool V-Neck Heather", 8800,
          "Lightweight lambswool v-neck for layering over shirts. Fully fashioned shoulders and a trim fit.",
          ["Heather v-neck", "On model", "Detail"], ["layering"])

dump("manifest.json", {"ucp": {"version": "2026-08-25", "services": {"dev.ucp.shopping": [
    {"version": "2026-08-25", "transport": "mcp", "endpoint": f"https://{D}/api/ucp/mcp"}]}}})

dump("pages/harbor-merino-crew-oatmeal.json", {
    "handle": crew["handle"], "url": crew["url"], "title": crew["title"], "type": "Sweater", "vendor": "Northfield Knit",
    "tags": crew["tags"], "price": 9800, "compare_at": 12800, "available": True, "images": 7,
    "options": [{"name": "Size", "values": ["XS", "S", "M", "L", "XL"]}, {"name": "Color", "values": ["Oatmeal", "Navy"]}]})
dump("pages/ridgeline-cardigan-charcoal.json", {
    "handle": card["handle"], "url": card["url"], "title": card["title"], "type": "Cardigan", "vendor": "Northfield Knit",
    "tags": card["tags"], "price": 15800, "compare_at": 0, "available": True, "images": 5,
    "options": [{"name": "Size", "values": ["S", "M", "L"]}, {"name": "Color", "values": ["Charcoal"]}]})

searches = {
    "Harbor Merino Crew Oatmeal": [crew, navy, qzip, beanie],
    "Harbor Merino Crew": [navy, crew, qzip, beanie],
    "sweater": [fish, vneck, qzip, navy, beanie, crew],
    "merino sweater for travel": [qzip, navy, beanie, vneck, crew, fish],
    "Ridgeline Cardigan Charcoal": [card, fish, vneck],
    "Ridgeline Cardigan": [card, fish, vneck],
    "cardigan": [vneck, fish, qzip, navy, beanie],
}
for q, ps in searches.items():
    dump(f"search/{slug(q)}.json", {"products": ps})

dump("../input.json", {"brand": "Northfield Knit", "domain": D, "queries": ["merino sweater for travel"],
                       "products": [{"url": crew["url"], "queries": []}, {"url": card["url"], "queries": []}]})
