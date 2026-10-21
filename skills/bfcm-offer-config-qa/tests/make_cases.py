#!/usr/bin/env python3
"""
Write small, hand-checkable test inputs into a folder for tests/run_tests.sh.

    python3 tests/make_cases.py OUT_DIR

clean/       the sample store with every finding fixed: should be READY
utc_start/   clean, but the main sale starts at 00:00 UTC (7 pm ET the day before)
gap/         clean, but VIPEARLY ends at 9:00 pm ET: a 3-hour gap before the main sale
oneside/     clean, but only the sale allows order discounts: nothing stacks
bothsides/   oneside, and WELCOME10 also allows product discounts: they stack to 32.5%
limits/      clean, but MAYA20 has a 500-use limit (plan 300) and isn't once per customer
hand/        hand-computed stack: 20% product, 10% + 5% order, $10 off $100+ order (Chicago time)
cost/        a 25% sale that puts one SKU below unit cost and one under the 15% margin floor
split/       $20 off products split across items with a $100 minimum: a $30 item's share is $6 ($24.00)
app/         clean, but the main sale is an app discount: targeting not visible, checked manually (INFO)
never/       clean, but Walnut's $96 compare-at was never a selling price in the history
noend/       clean, but VIPEARLY has no end date and overlaps the whole main sale
vipboth/     clean, but VIPEARLY starts and ends early: the test cart names the early end
truncated/   clean, but VIPEARLY's target list was cut off at 50 in the export
fraction/    clean, but MAYA20's value is 0.2 in a hand-built file (read as 0.2%, with a warning)
bad_*        invalid inputs that must exit 2 with the reason
gql_*        GraphQL responses for the normalizer: edge cases, API errors, an unfinished page, a buy X get Y with a
             $5-off "get" (used to crash the analyzer), a non-numeric percentage, a truncated target list
"""
import copy
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "examples", "data")
out = sys.argv[1]


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else list(csv.reader(f))


def write(path, obj):
    path = os.path.join(out, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        if isinstance(obj, (dict, list)) and not (isinstance(obj, list) and obj and isinstance(obj[0], list)):
            json.dump(obj, f, indent=2)
            f.write("\n")
        else:
            csv.writer(f, lineterminator="\n").writerows(obj)


PLAN, DISC, PRODUCTS, HISTORY = load("offer_plan.json"), load("discounts.json"), load("products.csv"), load("price_history.csv")


def by_title(doc, title):
    return next(d for d in doc["discounts"] if d["title"] == title)


def clean_discounts():
    doc = copy.deepcopy(DISC)
    doc["discounts"] = [d for d in doc["discounts"] if d["title"] != "SUMMER15"]
    by_title(doc, "WELCOME10")["combinesWith"]["productDiscounts"] = False
    by_title(doc, "Free shipping on orders $75+")["minimum"] = {"subtotal": 75}
    maya = by_title(doc, "MAYA20")
    maya["usageLimit"], maya["oncePerCustomer"] = 300, True
    maya["combinesWith"]["productDiscounts"] = False
    sale = by_title(doc, "BFCM 25% off sitewide")
    sale["target"] = {"type": "collections", "collections": ["BFCM Eligible"]}
    sale["combinesWith"] = {"productDiscounts": False, "orderDiscounts": False, "shippingDiscounts": True}
    by_title(doc, "VIPEARLY")["endsAt"] = "2026-11-26T04:59:00Z"
    return doc


clean_products = [r if r[3] != "FH-WSB-STD" else r[:5] + [""] + r[6:] for r in PRODUCTS]
clean_history = [r for r in HISTORY if not (r[0] == "FH-WSB-STD" and r[1] == "2026-10-02")]
clean_history = [r if r[0] != "FH-WSB-STD" else r[:3] + [""] for r in clean_history]


def case(name, doc, plan=PLAN, products=clean_products, history=clean_history):
    write(f"{name}/plan.json", plan)
    write(f"{name}/discounts.json", doc)
    write(f"{name}/products.csv", products)
    write(f"{name}/history.csv", history)


case("clean", clean_discounts())
doc = clean_discounts()
by_title(doc, "BFCM 25% off sitewide")["startsAt"] = "2026-11-26T00:00:00Z"
case("utc_start", doc)
doc = clean_discounts()
by_title(doc, "VIPEARLY")["endsAt"] = "2026-11-26T02:00:00Z"
case("gap", doc)
doc = clean_discounts()
by_title(doc, "BFCM 25% off sitewide")["combinesWith"]["orderDiscounts"] = True
case("oneside", doc)
by_title(doc, "WELCOME10")["combinesWith"]["productDiscounts"] = True
case("bothsides", doc)
doc = clean_discounts()
by_title(doc, "MAYA20")["usageLimit"] = 500
by_title(doc, "MAYA20")["oncePerCustomer"] = False
case("limits", doc)


IDS = []


def disc(title, cls, typ, value, codes=(), combines=(False, False, False), starts="2026-01-01T06:00:00Z", ends=None,
         minimum=None, status="ACTIVE", target=None):
    IDS.append(title)
    return {"id": f"gid://shopify/DiscountNode/{900 + len(IDS)}", "title": title,
            "method": "code" if codes else "automatic", "codes": list(codes), "class": cls, "type": typ,
            "value": value, "target": target or {"type": "all"}, "minimum": minimum, "eligibility": {"type": "all"},
            "usageLimit": None, "oncePerCustomer": False, "timesUsed": 0,
            "combinesWith": dict(zip(("productDiscounts", "orderDiscounts", "shippingDiscounts"), combines)),
            "tags": [], "startsAt": starts, "endsAt": ends, "status": status, "updatedAt": None}


# Hand-computed stack (store in America/Chicago, export Nov 1, 2026 noon CT):
#   HAND-1 $50: 20% -> $40.00; 10% and 5% of $40 = $4.00 + $2.00 -> $34.00;
#               $10 off $100+ shared by a $100 cart: $10 x 40/100 = $4.00 -> $30.00 (40% off).
#   HAND-2 $200: 20% -> $160.00; $16.00 + $8.00 -> $136.00; $10 -> $126.00 (37% off).
hand_plan = {"store": "Hand Case", "timezone": "America/Chicago", "launch": "2026-11-24", "max_item_discount_pct": 50,
             "offers": [
                 {"name": "Sitewide", "phase": "Sale", "method": "automatic", "title": "Sitewide 20",
                  "type": "percentage", "value": 20, "class": "product", "combines_with": ["order"],
                  "starts": "2026-11-24 00:00", "ends": "2026-11-30 23:59"},
                 {"method": "code", "code": "TENOFF", "type": "percentage", "value": 10, "class": "order",
                  "combines_with": ["product", "order"]},
                 {"method": "code", "code": "FIVEOFF", "type": "percentage", "value": 5, "class": "order",
                  "combines_with": ["product", "order"]},
                 {"method": "code", "code": "SPEND100", "type": "fixed_amount", "value": 10, "class": "order",
                  "min_subtotal": 100, "combines_with": ["product", "order"]}]}
hand_disc = {"exportedAt": "2026-11-01T12:00:00-06:00", "discounts": [
    disc("Sitewide 20", "product", "percentage", 20, combines=(False, True, False), starts="2026-11-24T06:00:00Z",
         ends="2026-12-01T05:59:00Z", status="SCHEDULED"),
    disc("TENOFF", "order", "percentage", 10, codes=["TENOFF"], combines=(True, True, False)),
    disc("FIVEOFF", "order", "percentage", 5, codes=["FIVEOFF"], combines=(True, True, False)),
    disc("SPEND100", "order", "fixed_amount", 10, codes=["SPEND100"], combines=(True, True, False),
         minimum={"subtotal": 100})]}
P_HEAD = ["handle", "title", "variant_title", "sku", "price", "compare_at_price", "unit_cost", "collections",
          "inventory", "units_sold_90d"]
write("hand/plan.json", hand_plan)
write("hand/discounts.json", hand_disc)
write("hand/products.csv", [P_HEAD, ["hand-one", "Hand One", "", "HAND-1", "50.00", "", "20.00", "Shop", "10", "5"],
                            ["hand-two", "Hand Two", "", "HAND-2", "200.00", "", "60.00", "Shop", "10", "5"]])

# 25% sale: COST-1 $30 -> $22.50 under its $25 cost; COST-2 $40 -> $30.00, margin (30-26)/30 = 13.3% < 15%.
cost_plan = {"store": "Cost Case", "timezone": "America/New_York", "max_item_discount_pct": 25,
             "margin_floor_pct": 15, "offers": [
                 {"name": "Sale", "phase": "Sale", "method": "automatic", "title": "Sale 25", "type": "percentage",
                  "value": 25, "class": "product", "starts": "2026-11-27 00:00", "ends": "2026-11-30 23:59"}]}
cost_disc = {"exportedAt": "2026-11-02T09:00:00-05:00", "discounts": [
    disc("Sale 25", "product", "percentage", 25, starts="2026-11-27T05:00:00Z", ends="2026-12-01T04:59:00Z",
         status="SCHEDULED")]}
write("cost/plan.json", cost_plan)
write("cost/discounts.json", cost_disc)
write("cost/products.csv", [P_HEAD, ["cost-one", "Cost One", "", "COST-1", "30.00", "", "25.00", "Shop", "40", "9"],
                            ["cost-two", "Cost Two", "", "COST-2", "40.00", "", "26.00", "Shop", "12", "9"],
                            ["cost-three", "Cost Three", "", "COST-3", "100.00", "", "20.00", "Shop", "12", "9"]])

# Invalid inputs.
doc = clean_discounts()
doc["discounts"][0]["startsAt"] = "2025-02-01T05:00:00"
write("bad_offset.json", doc)
write("bad_products.csv", [PRODUCTS[0], PRODUCTS[1][:4] + ["-48.00"] + PRODUCTS[1][5:]] + PRODUCTS[2:])
plan = copy.deepcopy(PLAN)
plan["timezone"] = "Mars/Olympus"
write("bad_tz.json", plan)
plan = copy.deepcopy(PLAN)
del plan["offers"][0]["class"]
write("bad_class.json", plan)

# GraphQL responses for the normalizer.
edge_page1 = {"data": {"discountNodes": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "edges": [
    {"node": {"id": "gid://shopify/DiscountNode/1", "discount": {
        "__typename": "DiscountCodeBxgy", "title": "Buy 2 get 1 free mugs", "status": "ACTIVE",
        "startsAt": "2026-10-01T04:00:00Z", "endsAt": None, "updatedAt": None, "discountClasses": ["PRODUCT"],
        "asyncUsageCount": 4, "usageLimit": 100, "appliesOncePerCustomer": True, "usesPerOrderLimit": 1, "tags": [],
        "codes": {"edges": [{"node": {"code": "MUGS3"}}]}, "codesCount": {"count": 1},
        "combinesWith": {"productDiscounts": False, "orderDiscounts": True, "shippingDiscounts": True},
        "context": {"__typename": "DiscountBuyerSelectionAll"},
        "customerBuys": {"value": {"__typename": "DiscountQuantity", "quantity": "2"},
                         "items": {"__typename": "DiscountCollections", "collections": {"nodes": [{"title": "Kitchen"}]}}},
        "customerGets": {"value": {"__typename": "DiscountOnQuantity", "quantity": {"quantity": "1"},
                                   "effect": {"__typename": "DiscountPercentage", "percentage": 1.0}},
                         "items": {"__typename": "DiscountProducts", "products": {"nodes": [{"handle": "stoneware-mug-set"}]},
                                   "productVariants": {"nodes": []}}}}}},
    {"node": {"id": "gid://shopify/DiscountNode/2", "discount": {
        "__typename": "DiscountAutomaticBasic", "title": "$10 off $100", "status": "SCHEDULED",
        "startsAt": "2026-11-27T05:00:00Z", "endsAt": "2026-11-28T05:00:00Z", "discountClass": "ORDER",
        "usageCount": 12, "combinesWith": {"productDiscounts": True, "orderDiscounts": False, "shippingDiscounts": True},
        "customerSelection": {"__typename": "DiscountCustomerAll", "allCustomers": True},
        "minimumRequirement": {"__typename": "DiscountMinimumSubtotal", "greaterThanOrEqualToSubtotal": {"amount": "100.0"}},
        "customerGets": {"value": {"__typename": "DiscountAmount", "amount": {"amount": "10.0"}, "appliesOnEachItem": False},
                         "items": {"__typename": "AllDiscountItems"}}}}}]}}}
edge_page2 = {"data": {"discountNodes": {"pageInfo": {"hasNextPage": False, "endCursor": "c2"}, "nodes": [
    {"id": "gid://shopify/DiscountNode/3", "discount": {
        "__typename": "DiscountCodeApp", "title": "Tiered app discount", "status": "ACTIVE",
        "startsAt": "2026-09-01T04:00:00Z", "endsAt": None, "discountClasses": ["PRODUCT", "ORDER"],
        "asyncUsageCount": 0, "usageLimit": None, "appliesOncePerCustomer": False, "tags": [],
        "codes": {"nodes": [{"code": "TIERS"}]}, "codesCount": {"count": 1},
        "combinesWith": {"productDiscounts": False, "orderDiscounts": False, "shippingDiscounts": False},
        "context": {"__typename": "DiscountBuyerSelectionAll"}, "appDiscountType": {"title": "Volume tiers"}}},
    {"id": "gid://shopify/DiscountNode/4", "discount": {
        "__typename": "DiscountCodeBasic", "title": "Canada loyalty", "status": "ACTIVE",
        "startsAt": "2026-09-01T04:00:00Z", "endsAt": None, "updatedAt": "2026-09-02T10:00:00Z",
        "discountClasses": ["PRODUCT"], "asyncUsageCount": 7, "usageLimit": None, "appliesOncePerCustomer": False,
        "tags": ["seasonal"], "codes": {"nodes": [{"code": "LOYAL-A"}, {"code": "LOYAL-B"}]},
        "codesCount": {"count": 1200},
        "combinesWith": {"productDiscounts": True, "orderDiscounts": False, "shippingDiscounts": False,
                         "productDiscountsWithTagsOnSameCartLine": ["loyalty"]},
        "context": {"__typename": "DiscountMarkets", "markets": {"nodes": [{"name": "Canada"}]}},
        "minimumRequirement": {"__typename": "DiscountMinimumQuantity", "greaterThanOrEqualToQuantity": "2"},
        "customerGets": {"value": {"__typename": "DiscountPercentage", "percentage": 0.15},
                         "items": {"__typename": "DiscountProducts",
                                   "products": {"nodes": [{"handle": "linen-apron"}]},
                                   "productVariants": {"nodes": [{"sku": "FH-THR-CHA"}]}}}}}]}}}
write("gql_edge.json", [edge_page1, edge_page2])
write("gql_errors.json", {"errors": [{"message": "Field 'productDiscountsWithTagsOnSameCartLine' doesn't exist on "
                                                 "type 'DiscountCombinesWith'"}]})
write("gql_partial.json", edge_page1)

# ---- QA fixes (Oct 2026) -----------------------------------------------------------------------------------------
# bxgy_amount: the sample GraphQL response with SUMMER15 turned into a buy X get Y whose "get" is $5 off
# (DiscountAmount effect). The analyzer used to crash on it (regression for QA 6.1).
NODES = load("shopify_discount_nodes.json")
gql = copy.deepcopy(NODES)
for n in gql["data"]["discountNodes"]["nodes"]:
    if n["discount"]["title"] == "SUMMER15":
        n["discount"]["__typename"] = "DiscountCodeBxgy"
        n["discount"]["customerGets"]["value"] = {
            "__typename": "DiscountOnQuantity", "quantity": {"quantity": 1},
            "effect": {"__typename": "DiscountAmount", "amount": {"amount": "5.0"}}}
write("gql_bxgy_amount.json", gql)

# gql_bad_pct: a non-numeric percentage must exit 2, not crash.
gql = copy.deepcopy(NODES)
gql["data"]["discountNodes"]["nodes"][2]["discount"]["customerGets"]["value"]["percentage"] = "fifteen"
write("gql_bad_pct.json", gql)

# gql_truncated: a product target with more than 50 products (pageInfo.hasNextPage) is flagged as truncated.
gql = copy.deepcopy(NODES)
gql["data"]["discountNodes"]["nodes"][2]["discount"]["customerGets"]["items"] = {
    "__typename": "DiscountProducts",
    "products": {"pageInfo": {"hasNextPage": True}, "nodes": [{"handle": "linen-apron"}]},
    "productVariants": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
write("gql_truncated.json", gql)

# truncated/: clean, but VIPEARLY's target list was cut off in the export.
doc = clean_discounts()
by_title(doc, "VIPEARLY")["target"]["truncated"] = True
case("truncated", doc)

# split/: $20 off products split across the items (appliesOnEachItem false) with a $100 minimum, plan identical.
#   SPLIT-1 $30 (cost $20): the cart must reach $100, so its share is at most 20 x 30/100 = $6 -> $24.00 (not $10).
split_plan = {"store": "Split Case", "timezone": "America/New_York", "max_item_discount_pct": 25, "margin_floor_pct": 10,
              "offers": [{"name": "Spend 100", "phase": "Sale", "method": "automatic", "title": "Twenty off 100",
                          "type": "fixed_amount", "value": 20, "class": "product", "min_subtotal": 100,
                          "applies_on_each_item": False, "starts": "2026-11-27 00:00", "ends": "2026-11-30 23:59"}]}
split_d = disc("Twenty off 100", "product", "fixed_amount", 20, starts="2026-11-27T05:00:00Z",
               ends="2026-12-01T04:59:00Z", minimum={"subtotal": 100}, status="SCHEDULED")
split_d["appliesOnEachItem"] = False
write("split/plan.json", split_plan)
write("split/discounts.json", {"exportedAt": "2026-11-02T09:00:00-05:00", "discounts": [split_d]})
write("split/products.csv", [P_HEAD, ["split-one", "Split One", "", "SPLIT-1", "30.00", "", "20.00", "Shop", "40", "9"],
                             ["split-two", "Split Two", "", "SPLIT-2", "120.00", "", "30.00", "Shop", "40", "9"]])

# app/: clean, but the main sale is an app discount (target not visible via the API, as the normalizer writes it).
doc = clean_discounts()
sale = by_title(doc, "BFCM 25% off sitewide")
sale.update({"type": "app", "value": None, "target": {"type": "unknown"}, "appTitle": "Tiered sale"})
case("app", doc)

# never/: clean, but Walnut's compare-at is $96 from the first history row and it never sold at $96.
never_products = [r if r[3] != "FH-WSB-STD" else r[:5] + ["96.00"] + r[6:] for r in clean_products]
never_history = [r if r[0] != "FH-WSB-STD" else r[:3] + ["96.00"] for r in clean_history]
case("never", clean_discounts(), products=never_products, history=never_history)

# noend/: clean, but VIPEARLY has no end date, so it overlaps the whole main sale.
doc = clean_discounts()
by_title(doc, "VIPEARLY")["endsAt"] = None
case("noend", doc)

# vipboth/: clean, but VIPEARLY starts 3 hours early (9 pm ET Nov 23) and ends 5 hours early (6:59 pm ET Nov 25).
#   The test cart sits in the early-end gap, so its reason must name the end time, not the start.
doc = clean_discounts()
by_title(doc, "VIPEARLY")["startsAt"] = "2026-11-24T02:00:00Z"
by_title(doc, "VIPEARLY")["endsAt"] = "2026-11-25T23:59:00Z"
case("vipboth", doc)

# fraction/: clean, but MAYA20's value was copied as Shopify's fraction (0.2) into a hand-built file.
doc = clean_discounts()
doc.pop("source", None)
by_title(doc, "MAYA20")["value"] = 0.2
case("fraction", doc)
print(f"wrote test cases to {out}")
