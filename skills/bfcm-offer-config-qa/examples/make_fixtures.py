#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional. Its discount setup, as found
on Oct 6, 2026, carries the mistakes this skill is built to catch:

- the main sale ("BFCM 25% off sitewide", automatic) targets All products, so
  Last Chance clearance and New Arrivals get an extra 25%;
- the sale allows order discounts and WELCOME10 allows product discounts, so
  they stack to 32.5% (plan max 25%); two Last Chance items (already 45% and
  50% below compare-at) then sell below unit cost;
- MAYA20, a creator code, has no usage limit, isn't once per customer, and
  combines with the sale;
- the free-shipping automatic discount has no minimum ($0 instead of $75);
- VIPEARLY ends Nov 25 at 23:59 UTC (6:59 pm ET), a 5-hour gap before the main
  sale starts at midnight ET;
- the old code SUMMER15 (15% off) is still active with no end date;
- Walnut Serving Board's compare-at was raised from $72 to $96 on Oct 2.

It writes the raw GraphQL response (shopify_discount_nodes.json) and, from the
same table, the normalized discounts.json, so tests can check that
scripts/normalize_shopify_discounts.py turns one into the other.

    python3 examples/make_fixtures.py   # rewrites examples/data/*
"""
import csv
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
R = random.Random(20261124)

EXPORTED_AT = "2026-10-06T14:05:00-04:00"

PLAN = {
    "store": "Fernhill Goods",
    "timezone": "America/New_York",
    "currency": "USD",
    "launch": "2026-11-24",
    "max_item_discount_pct": 25,
    "margin_floor_pct": 15,
    "shopify_plus": False,
    "economics": {
        "shipping_cost_per_order": 11.00,
        "last_bfcm_orders": 1356,
        "last_bfcm_orders_under_free_shipping_min": 556,
        "orders_per_day_now": 32,
    },
    "offers": [
        {"name": "VIP early access", "phase": "VIP early access", "method": "code", "code": "VIPEARLY",
         "type": "percentage", "value": 25, "class": "product", "eligible": "all",
         "exclude_collections": ["Gift Cards", "New Arrivals", "Last Chance"],
         "customer_eligibility": {"segments": ["SMS subscribers"]}, "usage_limit": None,
         "once_per_customer": False, "combines_with": ["shipping"],
         "starts": "2026-11-24 00:00", "ends": "2026-11-25 23:59"},
        {"name": "Main sale", "phase": "Main sale", "method": "automatic", "title": "BFCM 25% off sitewide",
         "type": "percentage", "value": 25, "class": "product", "eligible": "all",
         "exclude_collections": ["Gift Cards", "New Arrivals", "Last Chance"], "combines_with": ["shipping"],
         "starts": "2026-11-26 00:00", "ends": "2026-12-01 23:59"},
        {"name": "Free shipping $75+", "method": "automatic", "title": "Free shipping on orders $75+",
         "type": "free_shipping", "min_subtotal": 75, "combines_with": ["product", "order"]},
        {"name": "Welcome offer", "method": "code", "code": "WELCOME10", "type": "percentage", "value": 10,
         "class": "order", "customer_eligibility": {"first_order": True, "segment": "Never purchased"},
         "once_per_customer": True, "combines_with": ["shipping"]},
        {"name": "Creator code (Maya)", "method": "code", "code": "MAYA20", "type": "percentage", "value": 20,
         "class": "product", "eligible": "all", "usage_limit": 300, "once_per_customer": True,
         "combines_with": ["shipping"], "starts": "2026-09-01 00:00", "ends": "2026-12-31 23:59"},
    ],
    "must_not_be_active": ["SUMMER15"],
}

# handle, title, variant_title, sku, price, compare_at, unit_cost, collections, inventory, sold_90d
PRODUCTS = [
    ("linen-apron", "Linen Apron", "Natural", "FH-APR-NAT", "48.00", "", "14.00", "Textiles;BFCM Eligible", 240, 820),
    ("ceramic-pour-over-set", "Ceramic Pour-Over Set", "Default Title", "FH-POS-WHT", "64.00", "", "21.00",
     "Kitchen;BFCM Eligible", 150, 540),
    ("waxed-canvas-tote", "Waxed Canvas Tote", "Olive", "FH-TOT-OLV", "78.00", "", "26.00", "Bags;BFCM Eligible",
     120, 360),
    ("stoneware-mug-set", "Stoneware Mug Set (4)", "Speckled", "FH-MUG-SPK", "56.00", "", "19.00",
     "Kitchen;BFCM Eligible", 180, 610),
    ("merino-wool-throw", "Merino Wool Throw", "Charcoal", "FH-THR-CHA", "140.00", "", "52.00",
     "Textiles;BFCM Eligible", 70, 230),
    ("walnut-serving-board", "Walnut Serving Board", "Default Title", "FH-WSB-STD", "72.00", "96.00", "24.00",
     "Kitchen;BFCM Eligible", 110, 380),
    ("linen-napkins", "Linen Napkins (Set of 4)", "Default Title", "FH-NAP-NAT", "38.00", "", "11.00",
     "Textiles;New Arrivals", 140, 95),
    ("stoneware-pasta-bowls", "Stoneware Pasta Bowls (2)", "Default Title", "FH-PBW-SPK", "52.00", "", "18.00",
     "Kitchen;New Arrivals", 96, 64),
    ("merino-wool-throw-oat", "Merino Wool Throw — Oat", "Default Title", "FH-THR-OAT", "77.00", "140.00", "52.00",
     "Textiles;Last Chance", 18, 41),
    ("stoneware-mug-set-slate", "Stoneware Mug Set (4) — Slate", "Default Title", "FH-MUG-SLT", "28.00", "56.00",
     "19.00", "Kitchen;Last Chance", 26, 58),
    ("linen-apron-sage", "Linen Apron — Sage", "Default Title", "FH-APR-SGE", "29.00", "48.00", "14.00",
     "Textiles;Last Chance", 33, 66),
    ("gift-card", "Gift Card", "$25", "FH-GC-25", "25.00", "", "", "Gift Cards", "", 40),
    ("gift-card", "Gift Card", "$50", "FH-GC-50", "50.00", "", "", "Gift Cards", "", 120),
    ("gift-card", "Gift Card", "$100", "FH-GC-100", "100.00", "", "", "Gift Cards", "", 85),
    ("gift-card", "Gift Card", "$200", "FH-GC-200", "200.00", "", "", "Gift Cards", "", 20),
]

# Price history: one row per change ("from this date on"). 90 days: Jul 9 – Oct 6, 2026.
HISTORY_START = "2026-07-09"
MARKDOWN_DAY = "2026-08-17"          # Last Chance markdowns (compare-at = the old price)
NEW_ARRIVALS_DAY = "2026-09-14"
COMPARE_RAISE_DAY = "2026-10-02"     # Walnut Serving Board compare-at raised from $72 to $96

# One table drives both the raw API response and the normalized file.
# kind, title, codes, class, pct, items, context, minimum, usage_limit, once, used, combines, tags,
# startsAt, endsAt, status, updatedAt
DISCOUNTS = [
    ("CodeBasic", "WELCOME10", ["WELCOME10"], "ORDER", 0.1, None, ("segments", ["Never purchased"]), None, None,
     True, 1734, (True, False, True), ["welcome", "popup"], "2025-02-01T05:00:00Z", None, "ACTIVE",
     "2026-03-18T15:22:09Z"),
    ("AutomaticFreeShipping", "Free shipping on orders $75+", [], "SHIPPING", None, None, ("all", None), None, None,
     False, 7412, (True, True, False), [], "2025-03-03T05:00:00Z", None, "ACTIVE", "2026-09-29T20:41:37Z"),
    ("CodeBasic", "SUMMER15", ["SUMMER15"], "PRODUCT", 0.15, ("all", None), ("all", None), None, None, False, 23,
     (False, False, False), ["summer-2026"], "2026-06-01T04:00:00Z", None, "ACTIVE", "2026-08-14T13:07:52Z"),
    ("CodeBasic", "MAYA20", ["MAYA20"], "PRODUCT", 0.2, ("all", None), ("all", None), None, None, False, 118,
     (True, False, True), ["creator"], "2026-09-01T04:00:00Z", "2027-01-01T04:59:00Z", "ACTIVE",
     "2026-08-28T17:30:00Z"),
    ("AutomaticBasic", "BFCM 25% off sitewide", [], "PRODUCT", 0.25, ("all", None), ("all", None), None, None, False,
     0, (True, True, True), ["bfcm-2026"], "2026-11-26T05:00:00Z", "2026-12-02T04:59:00Z", "SCHEDULED",
     "2026-09-30T18:45:12Z"),
    ("CodeBasic", "VIPEARLY", ["VIPEARLY"], "PRODUCT", 0.25, ("collections", ["BFCM Eligible"]),
     ("segments", ["SMS subscribers"]), None, None, False, 0, (False, False, True), ["bfcm-2026", "vip"],
     "2026-11-24T05:00:00Z", "2026-11-25T23:59:00Z", "SCHEDULED", "2026-09-30T19:02:31Z"),
]


def ids():
    base = R.randint(1_420_000_000_000, 1_430_000_000_000)
    out = []
    for _ in DISCOUNTS:
        base += R.randint(10_000, 9_000_000)
        out.append(f"gid://shopify/DiscountNode/{base}")
    return out


def raw_node(nid, d):
    kind, title, codes, cls, pctv, items, ctx, minimum, usage, once, used, comb, tags, starts, ends, status, upd = d
    disc = {"__typename": "Discount" + kind, "title": title, "status": status, "startsAt": starts, "endsAt": ends,
            "updatedAt": upd, "discountClasses": [cls], "asyncUsageCount": used}
    if kind.startswith("Code"):
        disc["usageLimit"] = usage
        disc["appliesOncePerCustomer"] = once
    disc["tags"] = tags
    if kind.startswith("Code"):
        disc["codes"] = {"nodes": [{"code": c} for c in codes]}
        disc["codesCount"] = {"count": len(codes)}
    disc["combinesWith"] = {"productDiscounts": comb[0], "orderDiscounts": comb[1], "shippingDiscounts": comb[2],
                            "productDiscountsWithTagsOnSameCartLine": None}
    if ctx[0] == "all":
        disc["context"] = {"__typename": "DiscountBuyerSelectionAll"}
    else:
        disc["context"] = {"__typename": "DiscountCustomerSegments", "segments": [{"name": n} for n in ctx[1]]}
    disc["minimumRequirement"] = None if minimum is None else {
        "__typename": "DiscountMinimumSubtotal", "greaterThanOrEqualToSubtotal": {"amount": f"{minimum:.1f}"}}
    if kind.endswith("Basic"):
        if items is None or items[0] == "all":
            it = {"__typename": "AllDiscountItems"}
        else:
            it = {"__typename": "DiscountCollections", "collections": {"nodes": [{"title": t} for t in items[1]]}}
        disc["customerGets"] = {"value": {"__typename": "DiscountPercentage", "percentage": pctv}, "items": it}
    if kind.endswith("FreeShipping"):
        disc["maximumShippingPrice"] = None
    return {"id": nid, "discount": disc}


def normalized(nid, d):
    kind, title, codes, cls, pctv, items, ctx, minimum, usage, once, used, comb, tags, starts, ends, status, upd = d
    out = {"id": nid, "title": title, "method": "code" if kind.startswith("Code") else "automatic", "codes": codes,
           "class": cls.lower()}
    if kind.endswith("FreeShipping"):
        out["type"], out["value"] = "free_shipping", None
    else:
        out["type"] = "percentage"
        out["value"] = round(pctv * 100)
    if cls == "PRODUCT" and items is not None and items[0] == "collections":
        out["target"] = {"type": "collections", "collections": items[1]}
    else:
        out["target"] = {"type": "all"}
    out["minimum"] = None if minimum is None else {"subtotal": minimum}
    out["eligibility"] = {"type": "all"} if ctx[0] == "all" else {"type": "segments", "names": sorted(ctx[1])}
    out["usageLimit"] = usage
    out["oncePerCustomer"] = once
    out["timesUsed"] = used
    out["combinesWith"] = {"productDiscounts": comb[0], "orderDiscounts": comb[1], "shippingDiscounts": comb[2]}
    out["tags"] = tags
    out["startsAt"], out["endsAt"], out["status"], out["updatedAt"] = starts, ends, status, upd
    if kind.endswith("FreeShipping"):
        out["maxShippingPrice"] = None
    return out


def history_rows():
    rows = []
    for handle, title, vt, sku, price, compare, cost, colls, inv, sold in PRODUCTS:
        if "New Arrivals" in colls:
            rows.append((sku, NEW_ARRIVALS_DAY, price, ""))
        elif "Last Chance" in colls:
            rows.append((sku, HISTORY_START, compare, ""))
            rows.append((sku, MARKDOWN_DAY, price, compare))
        elif sku == "FH-WSB-STD":
            rows.append((sku, HISTORY_START, price, price))
            rows.append((sku, COMPARE_RAISE_DAY, price, compare))
        else:
            rows.append((sku, HISTORY_START, price, compare))
    return sorted(rows, key=lambda r: (r[0], r[1]))


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "offer_plan.json"), "w", encoding="utf-8") as f:
        json.dump(PLAN, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(OUT, "products.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["handle", "title", "variant_title", "sku", "price", "compare_at_price", "unit_cost", "collections",
                    "inventory", "units_sold_90d", "gift_card"])
        for p in PRODUCTS:
            w.writerow(list(p) + ["true" if p[0] == "gift-card" else "false"])
    with open(os.path.join(OUT, "price_history.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["sku", "date", "price", "compare_at_price"])
        w.writerows(history_rows())
    node_ids = ids()
    raw = {"data": {"discountNodes": {
        "pageInfo": {"hasNextPage": False, "endCursor": "eyJsYXN0X2lkIjoxNDI2NzM4MDQ1NTEyLCJsYXN0X3ZhbHVlIjoxfQ=="},
        "nodes": [raw_node(i, d) for i, d in zip(node_ids, DISCOUNTS)]}},
        "extensions": {"cost": {"requestedQueryCost": 386, "actualQueryCost": 41,
                                "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": 1959,
                                                   "restoreRate": 100.0}}}}
    with open(os.path.join(OUT, "shopify_discount_nodes.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
        f.write("\n")
    doc = {"source": "Shopify Admin GraphQL discountNodes", "shop": "Fernhill Goods", "exportedAt": EXPORTED_AT,
           "discounts": [normalized(i, d) for i, d in zip(node_ids, DISCOUNTS)]}
    with open(os.path.join(OUT, "discounts.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print("wrote examples/data/offer_plan.json, discounts.json, shopify_discount_nodes.json, products.csv "
          "and price_history.csv")


if __name__ == "__main__":
    main()
