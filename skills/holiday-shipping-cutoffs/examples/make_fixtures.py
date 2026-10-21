#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional. So is every carrier number in
it: transit days, delivery days and holiday ship-by dates are labeled `sample`
and are not real USPS or UPS figures.

- Ships from Columbus, Ohio: orders by 1 pm ET on a weekday ship the same day,
  closed weekends, Thanksgiving (Nov 26) and Dec 25, 250 orders a day.
- Checkout: Standard (USPS Ground Advantage or UPS Ground, whichever is
  cheaper) and Express (UPS 2nd Day Air). The store adds 2 peak days to ground
  transit and 1 to 2nd Day Air for December delays.
- 2026 demand: last year's BFCM curve (Black Friday 2025 was about 320 orders)
  with about 5% growth, the VIPEARLY early-access sale pulling some orders into
  Nov 24-25, then December gift orders that stay under capacity.
- Promises: last year's "Order by Dec 18" banner, a "guaranteed by Christmas"
  email with no date, a "next business day" FAQ line, a correct Express rate
  name and a gift guide date that is earlier than it needs to be.

    python3 examples/make_fixtures.py   # rewrites examples/data/*
"""
import csv
import datetime as dt
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
R = random.Random(20261224)
ONE = dt.timedelta(days=1)

HOLIDAYS = "2026-11-26;2026-12-25"
ZONE_COLS = [f"zone_{z}" for z in range(1, 9)]
CARRIER_HEADER = (["carrier", "service", "checkout_rate"] + ZONE_COLS
                  + ["peak_extra_days", "delivery_days", "non_delivery_dates", "ship_by_date", "source"])
CARRIERS = [
    # transit rows: business days by zone from Columbus, OH (fictional), the store's peak allowance, delivery days
    ["USPS", "Ground Advantage", "Standard", "2", "2", "2-3", "3", "3-4", "4-5", "4-5", "5", "2", "Mon-Sat", HOLIDAYS, ""],
    ["UPS", "Ground", "Standard", "1", "1", "1-2", "2", "3", "3-4", "4", "4-5", "2", "Mon-Fri", HOLIDAYS, ""],
    ["UPS", "2nd Day Air", "Express"] + ["2"] * 8 + ["1", "Mon-Fri", HOLIDAYS, ""],
    # holiday ship-by rows (fictional dates, not published by USPS or UPS)
    ["USPS", "Ground Advantage", ""] + [""] * 8 + ["", "", "", "2026-12-14"],
    ["UPS", "2nd Day Air", ""] + [""] * 8 + ["", "", "", "2026-12-22"],
]

# Orders shipped Nov 1 - Dec 24, 2025 by carrier zone from Columbus (ShipStation shipments export).
ZONE_ORDERS = [62, 228, 333, 988, 727, 329, 164, 461]

# Fixed days of the 2026 forecast: BFCM week and the shipping-cutoff days.
FIXED = {
    "2026-11-23": 52, "2026-11-24": 140, "2026-11-25": 120, "2026-11-26": 132, "2026-11-27": 330,
    "2026-11-28": 185, "2026-11-29": 150, "2026-11-30": 205, "2026-12-01": 94, "2026-12-02": 52,
    "2026-12-03": 44, "2026-12-14": 104, "2026-12-15": 86, "2026-12-21": 70, "2026-12-22": 36,
    "2026-12-23": 28, "2026-12-24": 20,
}

WAREHOUSE = {
    "name": "Columbus, OH warehouse",
    "timezone": "America/New_York",
    "order_cutoff": "13:00",
    "after_cutoff_pct": 60,
    "handling_days": 0,
    "working_days": "Mon-Fri",
    "closed_dates": [
        {"date": "2026-11-26", "name": "Thanksgiving"},
        {"date": "2026-12-25", "name": "Christmas Day"},
    ],
    "daily_capacity": 250,
    "peak_capacity": [],
}

PROMISE_HEADER = ["placement", "text", "service", "order_by", "ships_within", "send_date"]
PROMISES = [
    ["Site banner", "Order by Dec 18 for Christmas delivery", "Standard", "2026-12-18", "", ""],
    ["Checkout rate name", "Express (UPS 2nd Day Air): order by 1 pm ET Dec 21 for delivery by Dec 24",
     "Express", "2026-12-21", "", ""],
    ["Email: Holiday gift guide", "Guaranteed delivery by Christmas on every order.", "", "", "", "2026-12-03"],
    ["FAQ: Shipping", "Orders placed by 1 pm ET on a business day ship the same day. "
     "All other orders ship the next business day.", "", "", "1", ""],
    ["Gift guide page", "Order by Dec 10 for Christmas delivery with Standard shipping.", "Standard", "2026-12-10", "", ""],
]


def demand_rows():
    rows, d = [], dt.date(2026, 11, 1)
    while d <= dt.date(2026, 12, 24):
        n = FIXED.get(d.isoformat())
        if n is None:
            if d < dt.date(2026, 11, 16):
                base = 36
            elif d < dt.date(2026, 11, 23):
                base = 41
            elif d < dt.date(2026, 12, 14):
                base = 46 + 2 * (d - dt.date(2026, 12, 4)).days      # gift orders ramp toward the cutoffs
            else:
                base = 58 - 3 * (d - dt.date(2026, 12, 16)).days     # Express only after the Standard cutoff
            n = base + (4 if d.weekday() >= 5 else 0) + R.randint(-3, 3)
        rows.append([d.isoformat(), n])
        d += ONE
    return rows


def write_csv(name, header, rows):
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    write_csv("carriers.csv", CARRIER_HEADER, [r + ["sample"] for r in CARRIERS])
    write_csv("destinations.csv", ["zone", "orders"], [[z, n] for z, n in zip(range(1, 9), ZONE_ORDERS)])
    write_csv("demand.csv", ["date", "orders"], demand_rows())
    write_csv("promises.csv", PROMISE_HEADER, PROMISES)
    with open(os.path.join(OUT, "warehouse.json"), "w", encoding="utf-8") as f:
        json.dump(WAREHOUSE, f, indent=2)
        f.write("\n")
    print("wrote examples/data/carriers.csv, destinations.csv, demand.csv, promises.csv and warehouse.json")


if __name__ == "__main__":
    main()
