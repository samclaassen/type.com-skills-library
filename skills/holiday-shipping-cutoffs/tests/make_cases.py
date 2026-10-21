#!/usr/bin/env python3
"""
Write small, hand-checkable test inputs into a folder for tests/run_tests.sh.

    python3 tests/make_cases.py OUT_DIR

Cutoff and closures (target Thu Dec 24, 2026, no demand file)
  courier.csv, wh_cutoff.json, dest_one.csv
    One courier, 1 business day everywhere, Mon-Fri delivery. Cutoff 3:30 pm ET,
    warehouse closed Wed Dec 23 (inventory count). Last safe: Tue Dec 22 by 3:30 pm.

FIFO backlog (capacity 100, every order before the cutoff)
  wh_fifo.json, demand_fifo.csv, promises_fifo.csv, demand_planner.csv
    Thu Dec 3: 50, Fri: 180, Sat: 40, Sun: 10, Mon Dec 7: 90, Tue: 30, Wed: 20, Thu: 10.
    Fri ships 100 (80 left); Mon ships 80 Fri + 20 Sat (120 left); Tue ships 20 Sat + 10 Sun
    + 70 Mon (50 left); Wed ships 20 Mon + 30 Tue + 20 Wed: clears Wed Dec 9.
    demand_planner.csv is the same days as a Budget Planner plan ($85 per order).

Peak capacity and handling days
  wh_peak.json          the FIFO case with 150 a day on Fri Dec 4 and Mon Dec 7: Fri ships 150 (30 left), Mon ships
                        30 Fri + 40 Sat + 10 Sun + 70 Mon (20 left), Tue ships 20 + 30: clears Tue Dec 8.
  wh_handling.json      the cutoff case with 1 handling day: Mon Dec 21 orders ship Tue Dec 22 (in time); Tue Dec 22
                        orders ship Thu Dec 24 (Wed closed) and miss. Last safe: Mon Dec 21.

Coverage on a hand-computed zone mix (zone 1 50%, zone 3 30%, zone 8 20%)
  acme.csv, dest_cov.csv, wh_cov.json
    Acme Ground: zone 1 one day, zone 3 two to four days (a third each), zone 8 five days.
    Ships Fri Dec 18 (4 delivery days left): 50 + 30 = 80%. Mon Dec 21 (3): 50 + 20 = 70%.
    Tue Dec 22 (2): 50 + 10 = 60%. Wed Dec 23 (1): 50%. Thu Dec 17 (5): 100%.
  acme_shipby.csv       + ship-by Tue Dec 15 with a URL source: overrides the transit date.
  acme_late_shipby.csv  + ship-by Tue Dec 22 with a text source: transit date (Thu Dec 17) stays.
  acme_holiday.csv      no Acme delivery or pickup Mon Dec 21: Thu Dec 17 has 4 delivery days left (80%),
                        Fri Dec 18 has 3 (70%), Mon Dec 21 is picked up Tue (60%). Last safe: Wed Dec 16.
  promises_cls.csv      one promise of each class against Acme Ground.

Zero backlog
  demand_flat.csv       40 orders every day, Nov 1 - Dec 24, 2026 (use with the sample files).

BFCM window with no orders (regression: used to crash)
  demand_bfcm_gap.csv, wh_bfcm_gap.json
    Wed Nov 25: 400 orders, Thu Nov 26 - Mon Nov 30: 0, Tue Dec 1: 5; capacity 20 a day. The backlog from
    Nov 25 runs through the whole BFCM window, but no orders are forecast inside it.

Stated times against the cutoff (Acme Ground, warehouse 12:00 pm PT, last safe Thu Dec 17)
  promises_time.csv     11:59 pm PT and 5 pm ET (2 pm PT) are after the cutoff: RISKY. 9 am ET (6 am PT) is
                        before it and noon PT is the cutoff: OK.

USPS zone 9
  acme_zone9.csv, dest_zone9.csv   Acme Ground with zone_9 = 5 days; 10% of orders go to zone 9.

Invalid inputs (each must exit 2 with the reason)
  bad_nosource.csv, bad_dest_sum.csv, bad_demand_gap.csv, bad_demand_neg.csv,
  bad_promise_service.csv, bad_wh_cutoff.json, bad_zone_gap.csv,
  bad_shipby_late.csv (ship-by Dec 31, after the Dec 24 target), bad_wh_notz.json (no timezone)
"""
import csv
import datetime as dt
import json
import os
import sys

out = sys.argv[1]
ZONES = [f"zone_{z}" for z in range(1, 9)]
CH = ["carrier", "service", "checkout_rate"] + ZONES + ["peak_extra_days", "delivery_days", "non_delivery_dates",
                                                         "ship_by_date", "source"]
PH = ["placement", "text", "service", "order_by", "ships_within", "send_date"]


def write_csv(name, header, rows):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_json(name, obj):
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def wh(**kw):
    base = {"name": "Test warehouse", "timezone": "America/New_York", "order_cutoff": "12:00", "after_cutoff_pct": 50,
            "handling_days": 0, "working_days": "Mon-Fri", "closed_dates": [], "daily_capacity": 1000}
    base.update(kw)
    return base


def days(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


# Cutoff and closures
write_csv("courier.csv", CH, [["Test", "Courier", ""] + ["1"] * 8 + ["0", "Mon-Fri", "", "", "sample"]])
write_json("wh_cutoff.json", wh(order_cutoff="15:30", after_cutoff_pct=40,
                                closed_dates=[{"date": "2026-12-23", "name": "Inventory count"}]))
write_csv("dest_one.csv", ["zone", "orders"], [[4, 100]])

# FIFO backlog
write_json("wh_fifo.json", wh(timezone="America/Chicago", order_cutoff="14:00", after_cutoff_pct=0, daily_capacity=100))
FIFO = [("2026-12-03", 50), ("2026-12-04", 180), ("2026-12-05", 40), ("2026-12-06", 10), ("2026-12-07", 90),
        ("2026-12-08", 30), ("2026-12-09", 20), ("2026-12-10", 10)]
write_csv("demand_fifo.csv", ["date", "orders"], FIFO)
write_csv("demand_planner.csv", ["date", "phase", "proj_net_sales_base"], [[d, "taper", f"{n * 85:.2f}"] for d, n in FIFO])
write_csv("promises_fifo.csv", PH, [["FAQ", "Orders ship the next business day.", "", "", "1", ""]])

# Peak capacity and handling days
write_json("wh_peak.json", wh(timezone="America/Chicago", order_cutoff="14:00", after_cutoff_pct=0, daily_capacity=100,
                             peak_capacity=[{"from": "2026-12-04", "to": "2026-12-07", "capacity": 150}]))
write_json("wh_handling.json", wh(order_cutoff="15:30", after_cutoff_pct=40, handling_days=1,
                                  closed_dates=[{"date": "2026-12-23", "name": "Inventory count"}]))

# Coverage on a hand-computed zone mix
acme = ["Acme", "Ground", "", "1", "", "2-4", "", "", "", "", "5", "0", "Mon-Fri", "", ""]
write_csv("acme.csv", CH, [acme + ["https://example.com/acme-transit-map"]])
write_csv("acme_shipby.csv", CH, [acme + ["https://example.com/acme-transit-map"],
                                  ["Acme", "Ground", ""] + [""] * 8 + ["", "", "", "2026-12-15", "https://example.com/acme-holiday-dates"]])
write_csv("acme_late_shipby.csv", CH, [acme + ["https://example.com/acme-transit-map"],
                                       ["Acme", "Ground", ""] + [""] * 8 + ["", "", "", "2026-12-22", "Acme holiday flyer"]])
write_csv("acme_holiday.csv", CH, [acme[:13] + ["2026-12-21", "", "https://example.com/acme-transit-map"]])
write_csv("dest_cov.csv", ["zone", "share_pct"], [[1, 50], [3, 30], [8, 20]])
write_json("wh_cov.json", wh(timezone="America/Los_Angeles"))
write_csv("promises_cls.csv", PH, [
    ["Banner A", "Order by Dec 18 for Christmas", "Acme Ground", "2026-12-18", "", ""],
    ["Banner B", "Order by Dec 17 for Christmas", "Acme Ground", "2026-12-17", "", ""],
    ["Banner C", "Order by 12 pm PT Dec 17 for Christmas", "Acme Ground", "2026-12-17", "", ""],
    ["Product page", "Order by Dec 14 for Christmas", "Acme Ground", "2026-12-14", "", ""],
    ["Cart note", "Order by Dec 15 for Christmas", "", "2026-12-15", "", ""],
    ["SMS", "Still time for Christmas delivery!", "", "", "", "2026-12-19"],
    ["Email", "Guaranteed delivery by Christmas.", "", "", "", "2026-12-10"],
])

# Zero backlog
write_csv("demand_flat.csv", ["date", "orders"], [[d.isoformat(), 40] for d in days(dt.date(2026, 11, 1), dt.date(2026, 12, 24))])

# Invalid inputs
write_csv("bad_nosource.csv", CH, [["Test", "Courier", ""] + ["1"] * 8 + ["0", "Mon-Fri", "", "", ""]])
write_csv("bad_dest_sum.csv", ["zone", "share_pct"], [[1, 50], [3, 30]])
write_csv("bad_demand_gap.csv", ["date", "orders"], [["2026-12-03", 10], ["2026-12-05", 10]])
write_csv("bad_demand_neg.csv", ["date", "orders"], [["2026-12-03", 10], ["2026-12-04", -5]])
write_csv("bad_promise_service.csv", PH, [["Banner", "Order by Dec 15", "FedEx Ground", "2026-12-15", "", ""]])
write_json("bad_wh_cutoff.json", wh(order_cutoff="1pm"))
write_csv("bad_zone_gap.csv", CH, [["Test", "Courier", ""] + ["1"] * 7 + ["", "0", "Mon-Fri", "", "", "sample"]])

# BFCM window with no orders
write_csv("demand_bfcm_gap.csv", ["date", "orders"], [["2026-11-25", 400]] +
          [[d.isoformat(), 0] for d in days(dt.date(2026, 11, 26), dt.date(2026, 11, 30))] + [["2026-12-01", 5]])
write_json("wh_bfcm_gap.json", wh(order_cutoff="13:00", after_cutoff_pct=60, daily_capacity=20,
                                  closed_dates=[{"date": "2026-11-26", "name": "Thanksgiving"}]))

# Stated times against the cutoff
write_csv("promises_time.csv", PH, [
    ["Banner late PT", "Order by 11:59 pm PT Dec 17 for Christmas", "Acme Ground", "2026-12-17", "", ""],
    ["Banner late ET", "Order by 5 pm ET Dec 17 for Christmas", "Acme Ground", "2026-12-17", "", ""],
    ["Banner early ET", "Order by 9 am ET Dec 17 for Christmas", "Acme Ground", "2026-12-17", "", ""],
    ["Banner noon", "Order by noon PT Dec 17 for Christmas", "Acme Ground", "2026-12-17", "", ""],
])

# USPS zone 9
write_csv("acme_zone9.csv", CH + ["zone_9"], [acme + ["https://example.com/acme-transit-map", "5"]])
write_csv("dest_zone9.csv", ["zone", "share_pct"], [[1, 45], [3, 25], [8, 20], [9, 10]])

# More invalid inputs
write_csv("bad_shipby_late.csv", CH, [acme + ["https://example.com/acme-transit-map"],
                                      ["Acme", "Ground", ""] + [""] * 8 + ["", "", "", "2026-12-31", "Acme flyer"]])
_notz = wh()
del _notz["timezone"]
write_json("bad_wh_notz.json", _notz)
