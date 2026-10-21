#!/usr/bin/env python3
"""
Write small, hand-checkable test inputs into a folder for tests/run_tests.sh.

    python3 tests/make_cases.py OUT_DIR

labeled.csv          39 tickets with the type a person would give them (column "expected"), incl. precedence,
                     'not' phrases, whole-word matching, curly apostrophes and tag-only tickets
hand_tickets.csv     36 tickets over 3 days (Nov 28-30, 2025); hand_orders.csv: orders for those days and the
                     aligned days of 2026 (Nov 27-29). Numbers are worked out by hand in run_tests.sh
policies_full.json   the sample policies plus a price-adjustment policy (offered, 14 days)
policies_gap.json    policies_full without returns, damaged_defective and gift_options
policies_nobacklog.json  policies_full without bfcm_backlog
policies_lastyear.json   policies_full with last season's shipping dates (must be refused)
policies_badtype.json    policies_full with returns.standard_days = "thirty" (must be refused)
stale_macros.csv     existing macros with one problem each, plus clean ones that must not be flagged
cutoffs_legacy.json  an old hand-written cutoffs shape (must be refused: the real Cutoff Checker never wrote it)
cutoffs_checker.json a Holiday Shipping Cutoff Checker --json result (real schema) as run with the Budget
                     Planner's demand: Standard Dec 14, Express Dec 21, backlog 4 business days, clears Dec 7
cutoffs_unknown.json / cutoffs_missing.json  the same with an unknown field / without checkout_rates
promise_templates.json   a template line promising "guaranteed to arrive before Christmas" (must be refused)
policies_storecredit.json  policies_full with price_adjustment.how = "store credit, not a refund"
policies_noweekend.json    policies_full with weekend cover only Dec 5-20 (the hand case's weekend is closed)
plan.json, planner_plan.csv  a Budget Planner style --json plan for Nov 14-Dec 6, 2026, and its --csv shape
pii_tickets.csv      unclassified tickets with a phone number, a name, a street address and a formula
ambiguous_dates.csv  tickets whose MM/DD dates could all be DD/MM
bad_dates.csv, dup_ids.csv, gap_orders.csv, bad_rules.json, digit_templates.json, bad_placeholder.json
"""
import copy
import csv
import datetime as dt
import json
import os
import sys

out = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(here)


def write_csv(name, header, rows):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_json(name, data):
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# ----------------------------------------------------------------------------- classification
LABELED = [
    ("Where is my order?", "No tracking yet.", "", "wismo"),
    ("Order status", "", "", "wismo"),
    ("Package stuck", "No movement in 4 days.", "", "wismo"),
    ("Will it arrive by Christmas?", "", "", "shipping_deadline"),
    ("Where’s my order? Need it by Christmas", "", "", "shipping_deadline"),
    ("Need it by Dec 20", "For my sister in Denver.", "", "shipping_deadline"),
    ("Discount code not working", "", "", "discount_code"),
    ("Wrong zip code", "I typed the wrong zip code in my shipping address.", "", "address_change"),
    ("Tracking code doesn't work", "The tracking code shows nothing.", "", "wismo"),
    ("Promotional emails", "Please stop the promotional emails.", "", "other"),
    ("Price adjustment", "Bought it Nov 20 and it's 25% off now.", "", "price_adjustment"),
    ("Return or refund the difference?", "", "", "price_adjustment"),
    ("Cancel my order", "My discount code didn't apply.", "", "discount_code"),
    ("Cancel order", "Please cancel #18452.", "", "cancel_modify"),
    ("Ordered twice", "Please cancel one.", "", "cancel_modify"),
    ("Change address", "", "", "address_change"),
    ("Change address and add a gift note", "", "", "address_change"),
    ("Gift receipt so they can return it", "", "", "gift_options"),
    ("Gift wrap?", "Do you offer gift wrapping?", "", "gift_options"),
    ("Return label", "Need a return label.", "", "return_exchange"),
    ("Exchange", "Can I exchange the throw for another color?", "", "return_exchange"),
    ("Arrived broken, want a refund", "", "", "damaged_defective"),
    ("Split shipment?", "Only got part of my package.", "", "wismo"),
    ("Sold out?", "Will the apron restock?", "", "out_of_stock"),
    ("Back in stock before Christmas?", "", "", "out_of_stock"),
    ("Wholesale inquiry", "Do you sell to shops?", "", "other"),
    ("Haven't received my stuff", "Ordered last week.", "", "other"),
    ("Quick question", "Help please", "ORDER-STATUS", "wismo"),
    ("Discount code", "It says invalid.", "order-status", "discount_code"),
    ("Shipping label", "Can you print a QR code on it?", "", "other"),
    # QA review cases: negation, whole words, specific types before general ones
    ("Order arrived", "The box was not damaged.", "", "other"),
    ("Wrong item", "Not broken, just the wrong color.", "", "cancel_modify"),
    ("Payment", "Can I split payment across two cards?", "", "other"),
    ("Student discount?", "I'm broke lol. Do you have a student discount?", "", "discount_code"),
    ("Sale question", "Is the sale price applied automatically?", "", "discount_code"),
    ("Order question", "I'm a return customer, where is my order?", "", "wismo"),
    ("Phone order", "I placed my order by phone last week.", "", "other"),
    ("Help", "My package hasn't arrived. Can I get a refund?", "", "wismo"),
    ("Last warning", "I will file a chargeback if the package doesn't show up.", "", "escalation"),
]
write_csv("labeled.csv", ["id", "created_at", "subject", "body", "tags", "expected"],
          [[f"L{i:02d}", "2025-11-28T10:00:00-05:00", s, b, t, e] for i, (s, b, t, e) in enumerate(LABELED, start=1)])

rules = json.load(open(os.path.join(root, "references", "ticket_rules.json"), encoding="utf-8"))
swapped = copy.deepcopy(rules)
ret = next(t for t in swapped["types"] if t["type"] == "return_exchange")
swapped["types"].remove(ret)
swapped["types"].insert(0, ret)
write_json("rules_swapped.json", swapped)
bad_rules = copy.deepcopy(rules)
bad_rules["types"][0]["keywords"] = []
write_json("bad_rules.json", bad_rules)

# ----------------------------------------------------------------------------- forecast / gaps
#   Fri Nov 28, 2025: wismo 10, return 4, discount 2, shipping 2  (18)
#   Sat Nov 29, 2025: wismo 6, gift 1, other 1                     (8)
#   Sun Nov 30, 2025: wismo 2, damaged 4, discount 2, shipping 1, other 1  (10)
TEXT = {"wismo": ("Where is my order?", "No tracking yet."), "return": ("Return label", "How do I return it?"),
        "discount": ("Discount code not working", "It says invalid."),
        "shipping": ("Will it arrive by Christmas?", "Ordering today."),
        "gift": ("Gift wrap?", "Do you offer gift wrapping?"), "other": ("Wholesale inquiry", "Do you sell to shops?"),
        "damaged": ("Arrived broken", "The mug is cracked.")}
DAYS = [("2025-11-28", {"wismo": 10, "return": 4, "discount": 2, "shipping": 2}),
        ("2025-11-29", {"wismo": 6, "gift": 1, "other": 1}),
        ("2025-11-30", {"wismo": 2, "damaged": 4, "discount": 2, "shipping": 1, "other": 1})]
rows, n = [], 0
for day, mix in DAYS:
    for kind, count in mix.items():
        for _ in range(count):
            n += 1
            rows.append([f"H{n:03d}", f"{day}T{9 + n % 8:02d}:00:00-05:00", "email", TEXT[kind][0], TEXT[kind][1], ""])
write_csv("hand_tickets.csv", ["id", "created_at", "channel", "subject", "body", "tags"], rows)
dup = [r[:] for r in rows]
dup[5][0] = dup[4][0]
write_csv("dup_ids.csv", ["id", "created_at", "channel", "subject", "body", "tags"], dup)
bad = [r[:] for r in rows]
bad[2][1] = "28/11/2025 10:00"
write_csv("bad_dates.csv", ["id", "created_at", "channel", "subject", "body", "tags"], bad)
#   last season 100, 50, 50; this season (aligned, 364 days later) 150, 30, 120
write_csv("hand_orders.csv", ["date", "orders"], [["2025-11-28", 100], ["2025-11-29", 50], ["2025-11-30", 50],
                                                  ["2026-11-27", 150], ["2026-11-28", 30], ["2026-11-29", 120]])
write_csv("gap_orders.csv", ["date", "orders"], [["2025-11-28", 100], ["2025-11-30", 50]])

pol = json.load(open(os.path.join(root, "examples", "data", "policies.json"), encoding="utf-8"))
full = copy.deepcopy(pol)
full["price_adjustment"] = {"offered": True, "window_days": 14, "excludes": ["Last Chance"],
                            "how": "Reply with your order number and we'll refund the difference to your card."}
write_json("policies_full.json", full)
gap = copy.deepcopy(full)
for k in ("returns", "damaged_defective", "gift_options"):
    del gap[k]
write_json("policies_gap.json", gap)
nob = copy.deepcopy(full)
del nob["bfcm_backlog"]
write_json("policies_nobacklog.json", nob)
last = copy.deepcopy(full)
last["shipping"]["deliver_by"] = "2025-12-24"
for s in last["shipping"]["services"]:
    s["last_order_date"] = s["last_order_date"].replace("2026", "2025")
write_json("policies_lastyear.json", last)
badtype = copy.deepcopy(full)
badtype["returns"]["standard_days"] = "thirty"
write_json("policies_badtype.json", badtype)

write_json("cutoffs_legacy.json", {
    "deliver_by": "2026-12-24",
    "services": [{"service": "USPS Ground Advantage", "last_safe_order_date": "2026-12-12"},
                 {"service": "UPS Ground", "last_safe_order_date": "2026-12-14"},
                 {"service": "UPS Next Day Air", "last_safe_order_date": "2026-12-22"}],
    "backlog": {"orders_from": "2026-11-26", "orders_to": "2026-11-30", "ships_within_business_days": 2,
                "clears_by": "2026-12-02"},
})

# ----------------------------------------------------------------------------- existing macros
write_csv("stale_macros.csv", ["name", "body", "tags"], [
    ("Old shipping", "Order by Dec 18, 2025 for Christmas delivery.", ""),
    ("Ground cutoff", "Order by Dec 17 for ground shipping.", ""),
    ("Returns 14", "Return unused items within 14 days of delivery.", ""),
    ("Returns ok", "Orders placed Nov 1 to Dec 24 can be returned until Jan 31, 2027. Other orders can be "
                   "returned within 30 days of delivery.", ""),
    ("Promo", "Use code OLDCODE15 for 15% off, or use code FREESHIP.", ""),
    ("Promise", "Your gifts will definitely arrive in time for the holidays.", ""),
    ("Year", "Our 2024 holiday collection is back!", ""),
    ("Free ship", "Free shipping on orders over $50.", ""),
    ("Damage 7", "Report damage within 7 days of delivery.", ""),
    ("Edit 2h", "We can cancel within 2 hours of purchase.", ""),
    ("Reply 48", "We reply within 48 hours.", ""),
    ("Clean", "Thanks for reaching out! We're on it. Use WELCOME10 on your first order.", ""),
    ("Refund timing", "Once we receive your return, refunds take 5 business days.", ""),
])

# ----------------------------------------------------------------------------- templates that carry facts
tpl = json.load(open(os.path.join(root, "references", "macro_templates.json"), encoding="utf-8"))
digits = copy.deepcopy(tpl)
digits["templates"][0]["body"][2] = "You can return items within 30 days."
write_json("digit_templates.json", digits)
badph = copy.deepcopy(tpl)
badph["templates"][0]["body"][0] = "Hi {{ticket.customer.firstname}},"
write_json("bad_placeholder.json", badph)
promise = copy.deepcopy(tpl)
promise["templates"][0]["body"][2] = ("Your order is guaranteed to arrive before Christmas, and we'll refund you in "
                                      "full for any delay.")
write_json("promise_templates.json", promise)

sc = copy.deepcopy(full)
sc["price_adjustment"]["how"] = "We'll give you the difference as store credit, not a refund."
write_json("policies_storecredit.json", sc)
nw = copy.deepcopy(full)
nw["support_hours"]["weekend_coverage"] = {"from": "2026-12-05", "to": "2026-12-20"}
write_json("policies_noweekend.json", nw)

# ----------------------------------------------------------------------------- Cutoff Checker --json (real schema)
CHECKER = {
    "account": "Fernhill Goods", "all_cutoffs_passed": False, "as_of": "2026-10-07", "target_date": "2026-12-24",
    "threshold_pct": 95.0, "verdict": "NOT READY", "counts": {"RISKY": 1, "WARNING": 3, "INFO": 1},
    "cutoff": {"time": "13:00", "timezone": "America/New_York", "label": "1:00 pm ET"},
    "zone_mix": {"1": 0.0188, "2": 0.0693, "3": 0.1012, "4": 0.3001, "5": 0.2208, "6": 0.0999, "7": 0.0498,
                 "8": 0.14},
    "services": [
        {"name": "USPS Ground Advantage", "carrier": "USPS", "service": "Ground Advantage", "checkout_rate": "Standard",
         "last_safe_order_date": "2026-12-14",
         "last_safe_by_threshold": {"80": "2026-12-14", "95": "2026-12-14", "99": "2026-12-14"},
         "transit_last_safe": "2026-12-16", "ship_by_date": "2026-12-14", "basis": "carrier",
         "needs_cutoff_time": True, "peak_extra_days": 2, "transit_source": "sample", "ship_by_source": "sample"},
        {"name": "UPS Ground", "carrier": "UPS", "service": "Ground", "checkout_rate": "Standard",
         "last_safe_order_date": "2026-12-15",
         "last_safe_by_threshold": {"80": "2026-12-16", "95": "2026-12-15", "99": "2026-12-15"},
         "transit_last_safe": "2026-12-15", "ship_by_date": None, "basis": "transit",
         "needs_cutoff_time": True, "peak_extra_days": 2, "transit_source": "sample", "ship_by_source": None},
        {"name": "UPS 2nd Day Air", "carrier": "UPS", "service": "2nd Day Air", "checkout_rate": "Express",
         "last_safe_order_date": "2026-12-21",
         "last_safe_by_threshold": {"80": "2026-12-21", "95": "2026-12-21", "99": "2026-12-21"},
         "transit_last_safe": "2026-12-21", "ship_by_date": "2026-12-22", "basis": "transit",
         "needs_cutoff_time": True, "peak_extra_days": 1, "transit_source": "sample", "ship_by_source": "sample"},
    ],
    "checkout_rates": [
        {"rate": "Standard", "last_safe_order_date": "2026-12-14", "services": ["USPS Ground Advantage", "UPS Ground"],
         "extend_with": "UPS Ground", "extend_to": "2026-12-15"},
        {"rate": "Express", "last_safe_order_date": "2026-12-21", "services": ["UPS 2nd Day Air"],
         "extend_with": None, "extend_to": None},
    ],
    "backlog": {"modeled": True, "bfcm_window": ["2026-11-26", "2026-11-30"], "bfcm_ship_within_business_days": 4,
                "bfcm_clears": "2026-12-07", "bfcm_orders_forecast": 1456, "periods": [], "ship_days": []},
    "promises": [], "issues": [], "calendar": [], "fixes": [], "coverage_by_order_date": [],
}
write_json("cutoffs_checker.json", CHECKER)
unk = copy.deepcopy(CHECKER)
unk["backlog"]["bfcm_note"] = "new field"
write_json("cutoffs_unknown.json", unk)
miss = copy.deepcopy(CHECKER)
del miss["checkout_rates"]
write_json("cutoffs_missing.json", miss)

# ----------------------------------------------------------------------------- planner handoff
plan_rows = []
d0 = dt.date(2026, 11, 14)
for i in range(23):
    d = d0 + dt.timedelta(days=i)
    plan_rows.append({"date": d.isoformat(), "orders": round(50 + 10 * (i % 7) + (200 if d == dt.date(2026, 11, 27)
                                                                                  else 0) + 0.25, 2)})
write_json("plan.json", {"brand": "Fernhill Goods", "rows": plan_rows})
write_csv("planner_plan.csv", ["date", "phase", "proj_net_sales_base"],
          [[r["date"], "bfcm", round(r["orders"] * 88.66, 2)] for r in plan_rows])

# ----------------------------------------------------------------------------- PII, formulas, ambiguous dates
pii = [r[:] for r in rows]
pii.append(["P001", "2025-11-29T10:00:00-05:00", "email", "Call me 555-123-4567 - John Smith 12 Elm St",
            "Hi, my name is Jane Doe, jane@example.com", ""])
pii.append(["P002", "2025-11-29T11:00:00-05:00", "email", "=HYPERLINK(\"http://x\")", "+1 then @me", ""])
write_csv("pii_tickets.csv", ["id", "created_at", "channel", "subject", "body", "tags"], pii)
amb = [[f"A{i}", f"12/0{i}/2025 10:00", "email", "Where is my order?", "", ""] for i in range(1, 6)]
write_csv("ambiguous_dates.csv", ["id", "created_at", "channel", "subject", "body", "tags"], amb)
print(f"wrote test cases to {out}")
