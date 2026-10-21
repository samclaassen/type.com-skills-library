#!/usr/bin/env python3
"""
Write small, hand-checkable test inputs into a folder for tests/run_tests.sh.

    python3 tests/make_cases.py OUT_DIR

hand_orders.csv     8 customers, worked out by hand in run_tests.sh (BFCM 2025 = Nov 24 - Dec 2, 2025):
                    A  Nov 28  $75 + $25 off (25%)          repeats Dec 20 (day 22) and Mar 1 (day 93)
                    B  Nov 29  $66 + $44 off (40%, MAYA20)  fully refunded, never returns
                    Q  Nov 30  $50 full price, TikTok       never returns
                    C  Dec 1   $100 + a $30 order the same day (not a repeat)
                    E  Dec 2   $80 + $20 off, $40 refunded  repeats Jan 31 (day 60) with 10% off
                    D  Sep 15 first order, then Nov 28      not new in BFCM
                    F  Oct 10 (October 2025)                repeats during BFCM, Nov 28 (day 49), 25% off
                    H  Jan 15 (Jan-Feb 2026)                never returns
hand_new_d.csv      the same without D's Sep 15 order, so D becomes a BFCM new customer
hand_econ.json      60% gross margin, $10 shipping, 2% + $0.50 fees, 5% returns, planned discount 25%
hand_spend.csv      $175 over Nov 24 - Dec 2, 2025 -> $35.00 per BFCM new customer
hand_spend_gap.csv  the same without Nov 30
variant_orders.csv  15 BFCM first orders with utm_source spelled many ways (one only in the landing URL)
tz_orders.csv       UTC timestamps near midnight (store time zone America/New_York)
shopify_export.csv  a raw Shopify admin export: one row per line item, a cancelled order
bad_*.csv/json      invalid inputs
equal_repeat.csv    QA 5.1 repro: 200 BFCM customers, 100 at 20% off and 100 at 40% off, identical 20% repeat
returning.csv       60 October customers who all reorder during BFCM (30 with a discount) + 40 BFCM customers
fmt_orders.csv      hand_orders with $75.00 / €100.00 money cells and refunds written (66.00) / -40.00
comma/nan/inf/euro/paren_orders.csv  one bad money cell (or an all-European net_sales column); must exit 2
pct_econ.json       gross_margin_pct 0.64 (must exit 2); fee_econ.json payment_fee_pct 0.029 (warning)
eur_econ.json       hand_econ with currency EUR
hand_spend_zero.csv every spend cell 0 (treated as missing spend)
"""
import csv
import datetime as dt
import json
import os
import sys

out = sys.argv[1]
HEAD = ["order_id", "customer_id", "created_at", "net_sales", "discount_amount", "discount_codes",
        "refunded_amount", "utm_source", "products"]


def write(name, header, rows):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


HAND = [
    ["#1001", "D", "2025-09-15", "50.00", "0.00", "", "0.00", "google", "linen-apron"],
    ["#1002", "F", "2025-10-10", "100.00", "0.00", "", "0.00", "facebook", "walnut-serving-board"],
    ["#1003", "A", "2025-11-28", "75.00", "25.00", "", "0.00", "facebook", "ceramic-pour-over-set"],
    ["#1004", "D", "2025-11-28", "75.00", "25.00", "", "0.00", "facebook", "linen-apron"],
    ["#1014", "F", "2025-11-28", "75.00", "25.00", "", "0.00", "klaviyo", "stoneware-mug-set"],
    ["#1005", "B", "2025-11-29", "66.00", "44.00", "MAYA20", "66.00", "tiktok", "linen-apron"],
    ["#1006", "Q", "2025-11-30", "50.00", "0.00", "", "0.00", "TikTok", "waxed-canvas-tote"],
    ["#1007", "C", "2025-12-01", "100.00", "0.00", "", "0.00", "", "merino-wool-throw"],
    ["#1008", "C", "2025-12-01", "30.00", "0.00", "", "0.00", "", "linen-apron"],
    ["#1009", "E", "2025-12-02", "80.00", "20.00", "", "40.00", "fb", "stoneware-mug-set"],
    ["#1010", "A", "2025-12-20", "100.00", "0.00", "", "0.00", "klaviyo", "stoneware-mug-set"],
    ["#1012", "H", "2026-01-15", "60.00", "0.00", "", "0.00", "google", "linen-apron"],
    ["#1011", "E", "2026-01-31", "90.00", "10.00", "THANKYOU10", "0.00", "klaviyo", "linen-apron"],
    ["#1013", "A", "2026-03-01", "50.00", "0.00", "", "0.00", "", "walnut-serving-board"],
]
write("hand_orders.csv", HEAD, HAND)
write("hand_new_d.csv", HEAD, [r for r in HAND if r[0] != "#1001"])

econ = {"brand": "Hand Case", "currency": "USD", "gross_margin_pct": 60, "shipping_fulfillment_per_order": 10,
        "payment_fee_pct": 2, "payment_fee_fixed": 0.5, "return_rate_pct": 5, "discount_pct": 25}
with open(os.path.join(out, "hand_econ.json"), "w") as f:
    json.dump(econ, f)
bad = dict(econ)
bad.pop("gross_margin_pct")
with open(os.path.join(out, "bad_econ.json"), "w") as f:
    json.dump(bad, f)

days = [dt.date(2025, 11, 24) + dt.timedelta(days=i) for i in range(9)]
spend = [[d.isoformat(), 20 if i < 8 else 15, 0] for i, d in enumerate(days)]
write("hand_spend.csv", ["date", "meta_spend", "google_spend"], spend)
write("hand_spend_gap.csv", ["date", "meta_spend", "google_spend"], [r for r in spend if r[0] != "2025-11-30"])

# utm_source spellings: Meta 11 (facebook 4, fb 3, FB 2, ig 1, Instagram 1 from the landing URL),
# Google 3 (adwords 2, Google 1), TikTok 1 (tt).
spell = ["facebook"] * 4 + ["fb"] * 3 + ["FB"] * 2 + ["ig"] + [""] + ["adwords"] * 2 + ["Google"] + ["tt"]
rows = []
for i, s in enumerate(spell):
    landing = "/products/linen-apron?utm_source=Instagram&utm_medium=paid_social" if s == "" else "/"
    rows.append([f"#{2000 + i}", f"V{i}", "2025-11-28", "60.00", "20.00", "", "0.00", s, "linen-apron", landing])
write("variant_orders.csv", HEAD + ["landing_site"], rows)

# Times written in UTC. In the store's time zone (America/New_York):
#   T1 2025-11-25 15:00Z -> Nov 25 10:00 (BFCM either way)
#   T2 2025-12-03 04:30Z -> Dec 2 23:30 EST (BFCM only when converted)
#   T3 2025-11-01 04:30Z -> Nov 1 00:30 EDT (no cohort); a fixed -05:00 would say Oct 31 (October 2025)
write("tz_orders.csv", HEAD, [
    ["#3001", "T1", "2025-11-25T15:00:00Z", "64.00", "16.00", "", "0.00", "google", "ceramic-pour-over-set"],
    ["#3002", "T2", "2025-12-03T04:30:00Z", "56.00", "0.00", "", "0.00", "google", "stoneware-mug-set"],
    ["#3003", "T3", "2025-11-01T04:30:00Z", "48.00", "0.00", "", "0.00", "google", "linen-apron"],
])

# A raw Shopify admin export: extra line items are extra rows with only the item fields filled in.
SH = ["Name", "Email", "Financial Status", "Subtotal", "Shipping", "Taxes", "Total", "Discount Code",
      "Discount Amount", "Created at", "Lineitem quantity", "Lineitem name", "Lineitem price", "Cancelled at",
      "Refunded Amount"]
write("shopify_export.csv", SH, [
    ["#5001", "ann@example.com", "paid", "90.00", "0.00", "5.40", "95.40", "", "30.00", "2025-11-28 09:14:00 -0500",
     "1", "Ceramic Pour-Over Set", "64.00", "", "0.00"],
    ["#5001", "ann@example.com", "", "", "", "", "", "", "", "2025-11-28 09:14:00 -0500", "1",
     "Stoneware Mug Set (4)", "56.00", "", ""],
    ["#5002", "bo@example.com", "voided", "54.00", "0.00", "3.24", "57.24", "", "18.00", "2025-11-29 10:00:00 -0500",
     "1", "Walnut Serving Board", "72.00", "2025-11-29 11:00:00 -0500", "0.00"],
    ["#5003", "cy@example.com", "paid", "105.00", "0.00", "6.30", "111.30", "", "35.00", "2025-11-30 20:41:00 -0500",
     "1", "Merino Wool Throw", "140.00", "", "0.00"],
    ["#5004", "ann@example.com", "paid", "48.00", "6.00", "2.88", "56.88", "", "0.00", "2025-12-20 12:00:00 -0500",
     "1", "Linen Apron", "48.00", "", "0.00"],
])

# Invalid inputs.
write("bad_nocol.csv", [h for h in HEAD if h != "net_sales"], [r[:3] + r[4:] for r in HAND])
neg = [list(r) for r in HAND]
neg[3][3] = "-5.00"
write("bad_negative.csv", HEAD, neg)
dup = [list(r) for r in HAND] + [list(HAND[2])]
write("bad_dup.csv", HEAD, dup)
dat = [list(r) for r in HAND]
dat[5][2] = "29/11/2025"
write("bad_date.csv", HEAD, dat)
write("bad_nobfcm.csv", HEAD, [r for r in HAND if not ("2025-11-24" <= r[2] <= "2025-12-02") or r[1] in ("D", "F")])


# QA 5.1 repro: identical repeat behaviour at 20% and 40% off. Every 5th customer in each group reorders
# the same $100 full-price order on Jan 15, 2026.
rows = []
for i in range(200):
    deep = i >= 100
    net, disc = ("60.00", "40.00") if deep else ("80.00", "20.00")
    rows.append([f"#6{i:03d}", f"X{i}", "2025-11-28", net, disc, "", "0.00", "google", "linen-apron"])
    if i % 5 == 0:
        rows.append([f"#7{i:03d}", f"X{i}", "2026-01-15", "100.00", "0.00", "", "0.00", "klaviyo", "linen-apron"])
write("equal_repeat.csv", HEAD, rows)

rows = []
for i in range(60):
    rows.append([f"#8{i:03d}", f"O{i}", "2025-10-10", "100.00", "0.00", "", "0.00", "google", "linen-apron"])
    net, disc = ("75.00", "25.00") if i < 30 else ("100.00", "0.00")
    rows.append([f"#9{i:03d}", f"O{i}", "2025-11-28", net, disc, "", "0.00", "klaviyo", "linen-apron"])
for i in range(40):
    rows.append([f"#4{i:03d}", f"B{i}", "2025-11-28", "80.00", "20.00", "", "0.00", "google", "linen-apron"])
    if i < 10:
        rows.append([f"#3{i:03d}", f"B{i}", "2026-01-15", "100.00", "0.00", "", "0.00", "klaviyo", "linen-apron"])
write("returning.csv", HEAD, rows)

fmt = [list(r) for r in HAND]
fmt[2][3] = "$75.00"           # A
fmt[7][3] = "\u20ac100.00"      # C
fmt[5][6] = "(66.00)"          # B's refund, accounting sign
fmt[9][6] = "-40.00"           # E's refund, minus sign
write("fmt_orders.csv", HEAD, fmt)


def bad_cell(name, row, col, value):
    rr = [list(r) for r in HAND]
    rr[row][col] = value
    write(name, HEAD, rr)


bad_cell("comma_orders.csv", 2, 3, "75,00")       # file line 4, net_sales
bad_cell("nan_orders.csv", 3, 4, "nan")           # file line 5, discount_amount
bad_cell("inf_orders.csv", 5, 6, "inf")           # file line 7, refunded_amount
bad_cell("paren_orders.csv", 4, 3, "(50.00)")     # file line 6, net_sales may not be negative
eu = [list(r) for r in HAND]
for r in eu:
    r[3] = f"1.{int(float(r[3])):03d},00"          # 75.00 -> 1.075,00
write("euro_orders.csv", HEAD, eu)

for name, key, val, extra in (("pct_econ.json", "gross_margin_pct", 0.64, {}),
                              ("fee_econ.json", "payment_fee_pct", 0.029, {}),
                              ("eur_econ.json", "currency", "EUR", {})):
    e2 = dict(econ)
    e2[key] = val
    with open(os.path.join(out, name), "w") as f:
        json.dump(e2, f)

write("hand_spend_zero.csv", ["date", "meta_spend", "google_spend"], [[d.isoformat(), 0, "0.00"] for d in days])
