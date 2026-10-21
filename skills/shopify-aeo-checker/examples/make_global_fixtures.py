#!/usr/bin/env python3
"""Write fictional Global Catalog fixtures (Northfield Knit vs made-up competitors) for examples/run_global.sh."""
import json, os, re, sys

R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_fixtures")


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:80]


def dump(rel, obj):
    path = os.path.join(R, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)


SELLERS = {"nk": ("Northfield Knit", "northfield-knit.example", "northfield-knit.myshopify.com"),
           "ww": ("Woolworth Mills", "woolworthmills.example", "woolworth-mills.myshopify.com"),
           "cc": ("Coastline Cashmere", "coastline.example", "coastline-cashmere.myshopify.com"),
           "bb": ("Basics Bros", "basicsbros.example", "basics-bros.myshopify.com")}


def P(s, handle, title, price):
    name, dom, myd = SELLERS[s]
    return {"title": title, "url": f"https://{dom}/products/{handle}", "handle": handle, "description": f"{title} from {name}.",
            "price": price, "currency": "USD", "image": "", "alt": "", "seller": name, "seller_domain": myd,
            "seller_url": f"https://{dom}", "rating": 4.6, "reviews": 120}


dump("pdp.json", {"url": "https://northfield-knit.example/products/harbor-merino-crew-oatmeal", "handle": "harbor-merino-crew-oatmeal",
                  "domain": "northfield-knit.example", "title": "Harbor Merino Crew Oatmeal", "type": "Sweater", "vendor": "Northfield Knit",
                  "tags": ["merino"], "description": "Midweight merino crew.", "price": 9800, "options": [],
                  "shop_name": "Northfield Knit", "myshopify_domain": "northfield-knit.myshopify.com", "shop_domain": "northfield-knit.example"})
crew, navy = P("nk", "harbor-merino-crew-oatmeal", "Harbor Merino Crew Oatmeal", 9800), P("nk", "harbor-merino-crew-navy", "Harbor Merino Crew Navy", 9800)
w1, w2 = P("ww", "lambswool-crew", "Lambswool Crew", 8900), P("ww", "shetland-crew", "Shetland Crew", 9900)
c1, b1 = P("cc", "cashmere-crew", "Cashmere Crew", 19800), P("bb", "cotton-sweater", "Cotton Sweater", 4900)
dump("search/mens-merino-sweater.json", {"total": 412, "products": [w1, navy, crew, c1, w2, b1]})
dump("search/warm-crew-neck-sweater.json", {"total": 988, "products": [c1, w1, w2, b1, navy]})
dump("search/wool-sweater-gift.json", {"total": 530, "products": [b1, c1, w1, w2]})
