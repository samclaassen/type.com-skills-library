#!/usr/bin/env python3
"""
Write small, hand-checkable test inputs into a folder for tests/run_tests.sh.

    python3 tests/make_cases.py OUT_DIR

clean_events.csv   28 healthy days: Meta browser + CAPI deduplicated, values match Shopify
dedup_events.csv   clean, but from Sep 26 Meta stops merging browser and server events
zero_events.csv    clean, but TikTok sends nothing on the last 3 days (Oct 3–5)
bad_events.csv     clean, with a negative order count on one line
gap_events.csv     clean, with one day missing
clean_orders.csv   100 orders, consistent lowercase UTMs, 10 direct
variant_orders.csv 40 orders, utm_source given as facebook / Facebook / fb / FB / ig (explicit columns)
hand_bfcm.csv      3 days, numbers worked out by hand in run_tests.sh
comma/nan/inf_events.csv  clean, with one comma-decimal / nan / inf money cell (must exit 2)
euro_events.csv    clean, with every shopify_net_sales cell written 1.234,56-style (must exit 2)
paren_events.csv   clean, with one (50.00) net-sales cell (negatives are not valid here; must exit 2)
fmt_events.csv     clean, with net sales written $2,700.00 / €2,790.00 / 2730.00 (must parse like clean)
dup_orders.csv     clean_orders plus one repeated order_id
window_orders.csv  clean_orders with 3 orders dated 2019 and one ISO timestamp with an offset
junkdate_orders.csv clean_orders with a date of 2026-09-08garbage
march_bfcm.csv     hand_bfcm with one date moved to March
metaonly_bfcm.csv  hand_bfcm without google_spend (Google claimed revenue but has no spend)
"""
import csv
import datetime as dt
import os
import sys

out = sys.argv[1]
START = dt.date(2026, 9, 8)
HEADER = ["date", "shopify_orders", "shopify_net_sales", "meta_browser_events", "meta_server_events",
          "meta_purchases", "meta_purchase_value", "google_conversions", "google_conversion_value",
          "tiktok_purchases", "tiktok_purchase_value"]


def clean_rows():
    rows = []
    for i in range(28):
        o = 30 + (i % 3)
        rows.append({"date": (START + dt.timedelta(days=i)).isoformat(), "shopify_orders": o,
                     "shopify_net_sales": o * 90, "meta_browser_events": o - 3, "meta_server_events": o - 1,
                     "meta_purchases": o, "meta_purchase_value": o * 90, "google_conversions": 8,
                     "google_conversion_value": 8 * 92, "tiktok_purchases": o - 2, "tiktok_purchase_value": (o - 2) * 89})
    return rows


def write(name, header, rows):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


write("clean_events.csv", HEADER, clean_rows())

rows = clean_rows()
for r in rows[18:]:                                      # Sep 26 onward
    r["meta_purchases"] = r["meta_browser_events"] + r["meta_server_events"]
    r["meta_purchase_value"] = r["meta_purchases"] * 90
write("dedup_events.csv", HEADER, rows)

rows = clean_rows()
for r in rows[-3:]:
    r["tiktok_purchases"], r["tiktok_purchase_value"] = 0, 0
write("zero_events.csv", HEADER, rows)

rows = clean_rows()
rows[5]["shopify_orders"] = -4
write("bad_events.csv", HEADER, rows)

rows = clean_rows()
del rows[10]
write("gap_events.csv", HEADER, rows)

OH = ["order_id", "date", "landing_site", "referring_site"]
orders = []
for i in range(100):
    d = (START + dt.timedelta(days=i % 28)).isoformat()
    if i < 10:
        orders.append({"order_id": f"#{1000 + i}", "date": d, "landing_site": "/", "referring_site": ""})
    else:
        src = ("facebook", "google", "klaviyo")[i % 3]
        orders.append({"order_id": f"#{1000 + i}", "date": d, "referring_site": "",
                       "landing_site": f"/products/mug?utm_source={src}&utm_medium=x&utm_campaign=fall"})
write("clean_orders.csv", OH, orders)

VH = OH + ["utm_source", "utm_medium", "utm_campaign"]
spell = ["facebook"] * 20 + ["Facebook"] * 8 + ["fb"] * 6 + ["FB"] * 4 + ["ig"] * 2
orders = [{"order_id": f"#{2000 + i}", "date": "2026-10-01", "referring_site": "",
           "landing_site": "/?utm_source=ignored-because-explicit-column-wins",
           "utm_source": s, "utm_medium": "paid_social", "utm_campaign": "fall"} for i, s in enumerate(spell)]
write("variant_orders.csv", VH, orders)

BH = ["date", "shopify_net_sales", "meta_claimed_revenue", "google_claimed_revenue", "meta_spend", "google_spend",
      "meta_utm_sales"]
write("hand_bfcm.csv", BH, [
    dict(zip(BH, ["2025-11-28", 1000, 1200, 600, 300, 100, 750])),
    dict(zip(BH, ["2025-11-29", 2000, 1800, 900, 500, 200, 1250])),
    dict(zip(BH, ["2025-12-01", 1000, 600, 300, 200, 100, 500])),
])


def variant(name, line, col, value):
    rows = clean_rows()
    rows[line - 2][col] = value
    write(name, HEADER, rows)


variant("comma_events.csv", 5, "shopify_net_sales", "2255,74")
variant("nan_events.csv", 6, "meta_purchase_value", "nan")
variant("inf_events.csv", 4, "google_conversion_value", "inf")
variant("paren_events.csv", 3, "shopify_net_sales", "(50.00)")

rows = clean_rows()
for r in rows:
    v = r["shopify_net_sales"]
    r["shopify_net_sales"] = f"{v // 1000}.{v % 1000:03d},00"
write("euro_events.csv", HEADER, rows)

rows = clean_rows()
for i, r in enumerate(rows):
    v = r["shopify_net_sales"]
    r["shopify_net_sales"] = (f"${v:,.2f}", f"\u20ac{v:,.2f}", f"{v:.2f}")[i % 3]
write("fmt_events.csv", HEADER, rows)

with open(os.path.join(out, "clean_orders.csv"), encoding="utf-8", newline="") as f:
    base = list(csv.DictReader(f))
write("dup_orders.csv", OH, base + [dict(base[7])])
win = [dict(o) for o in base]
for o in win[20:23]:
    o["date"] = "2019-11-29"
win[30]["date"] = "2026-09-10T14:03:00-05:00"
write("window_orders.csv", OH, win)
junk = [dict(o) for o in base]
junk[4]["date"] = "2026-09-08garbage"
write("junkdate_orders.csv", OH, junk)

hand = [dict(zip(BH, r)) for r in (["2025-11-28", 1000, 1200, 600, 300, 100, 750],
                                    ["2025-11-29", 2000, 1800, 900, 500, 200, 1250],
                                    ["2025-12-01", 1000, 600, 300, 200, 100, 500])]
march = [dict(r) for r in hand]
march[1]["date"] = "2025-03-29"
write("march_bfcm.csv", BH, march)
MH = [c for c in BH if c != "google_spend"]
write("metaonly_bfcm.csv", MH, [{k: v for k, v in r.items() if k != "google_spend"} for r in hand])
