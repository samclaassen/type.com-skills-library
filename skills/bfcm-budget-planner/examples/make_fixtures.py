#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional: the Shopify home and kitchen
goods brand that every type.com BFCM skill uses. Off-peak it takes about 32
orders a day at about $89 net sales each (about $2,850 a day, $1.05M a year)
and spends about $950 a day on Meta (~55%), Google (~32%) and TikTok (~13%),
a blended MER of about 3.0. It runs no other paid channels, so history.csv has
no other_spend column. Its 2026 sale is 25% off: VIP early access Nov 24-25,
then sitewide Nov 26 - Dec 1 (economics.json sale_window).

Pinned to the shared store profile:
- Nov 24 - Dec 2, 2025 (last BFCM): net sales and Meta/Google/TikTok spend
  exactly as in the profile ($120,000 net sales on $30,000 spend, MER 4.00).
  Orders at about $88.50 each (Black Friday about 320), about 62% from new
  customers.
- Sep 8 - Oct 5, 2026: net sales and orders exactly as in the BFCM Tracking &
  Attribution Preflight sample (901 orders, $79,959 net sales).

Every other day (the rest of Oct 1 - Dec 31, 2025 and Jul 9 - Oct 6, 2026)
comes from a simple model:

    daily net sales = K x demand(day) x weekday x spend^0.55 x small noise

with a holiday demand curve and about 11% year-over-year growth at the same
spend. Last year's October and early-November budgets wobble day to day and
Dec 3-7 runs a flat post-cyber budget, so the fits see spend move apart from
demand. (The pinned cyber-week spend tracks demand, as real budgets do, so that
fit is confounded and the planner clamps it; the report says so.)

    python3 examples/make_fixtures.py   # rewrites examples/data/*
"""
import csv
import datetime as dt
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
R = random.Random(20261127)

B = 0.55                       # true spend elasticity in the generator
K = 60.0                       # scale: ~$2,575/day sales at ~$930/day spend (October 2025)
LY_BF = dt.date(2025, 11, 28)
LY_DAYS = [dt.date(2025, 10, 1) + dt.timedelta(days=i) for i in range(92)]   # Oct 1 - Dec 31, 2025
TY_DAYS = [dt.date(2026, 7, 9) + dt.timedelta(days=i) for i in range(90)]    # Jul 9 - Oct 6, 2026
GROWTH = 1.11
DOW = [0.97, 0.98, 1.00, 1.01, 1.03, 1.02, 0.99]                              # Mon..Sun
CHANNELS = ("meta", "google", "tiktok")
MIX = {"meta": 0.55, "google": 0.32, "tiktok": 0.13}

# Last BFCM, exactly as in the shared profile: date, net sales, Meta, Google, TikTok spend.
LAST_BFCM = """\
2025-11-24,5280,640,360,200
2025-11-25,5760,890,510,220
2025-11-26,7440,1120,540,280
2025-11-27,12480,1600,1210,460
2025-11-28,28320,3700,2180,780
2025-11-29,17760,2890,1170,670
2025-11-30,14640,1730,1250,490
2025-12-01,18960,2340,1640,720
2025-12-02,9360,1290,860,260"""
BFCM_AOV = 88.5
BFCM_NEW_SHARE = 0.62

# Sep 8 - Oct 5, 2026, exactly as in the Tracking Preflight sample: date, orders, net sales.
PREFLIGHT = """\
2026-09-08,26,2255.74
2026-09-09,35,3084.14
2026-09-10,30,2557.40
2026-09-11,30,2626.96
2026-09-12,39,3386.37
2026-09-13,33,2713.97
2026-09-14,28,2345.19
2026-09-15,30,2534.62
2026-09-16,32,3060.19
2026-09-17,29,2690.72
2026-09-18,30,2852.70
2026-09-19,32,2860.00
2026-09-20,33,3262.26
2026-09-21,33,2935.73
2026-09-22,26,2311.10
2026-09-23,28,2381.54
2026-09-24,37,3572.21
2026-09-25,29,2595.35
2026-09-26,31,2834.33
2026-09-27,40,3662.43
2026-09-28,37,3583.78
2026-09-29,36,3075.97
2026-09-30,33,2880.49
2026-10-01,28,2356.19
2026-10-02,37,3460.57
2026-10-03,42,3235.29
2026-10-04,31,2801.07
2026-10-05,26,2043.16"""


def season(rel: int) -> float:
    """Demand multiplier by days from Black Friday (last year), for the days not pinned."""
    if rel <= -28:                                  # October
        return 1.0
    if rel <= -12:                                  # Nov 1 - Nov 16: gift interest, offset by CPM inflation
        return 1.0 + 0.10 * (rel + 27) / 15         # and shoppers waiting for the sale
    if rel <= -5:                                   # Nov 17 - Nov 23: gift-guide and early-deal buying
        return 1.10 + 0.25 * (rel + 11) / 6
    if rel <= 9:                                    # Dec 3 - Dec 7: post-Cyber Monday taper
        return 1.70 - 0.06 * (rel - 5)
    return max(0.80, 1.40 - 0.03 * (rel - 10))      # gift buying until the shipping cutoffs, then quiet


def ly_spend(rel: int) -> float:
    if rel <= -28:
        base, wobble = 930, 0.25                    # October: budgets moved around day to day
    elif rel <= -12:
        base, wobble = 1000, 0.25                   # Nov 1 - Nov 16: held near the run rate
    elif rel <= -5:
        base, wobble = 1000 + 100 * (rel + 11), 0.15   # Nov 17 - Nov 23: ramp toward early access
    elif rel <= 9:
        base, wobble = 1750, 0.25                   # Dec 3 - Dec 7: a flat post-cyber December budget
    elif rel <= 17:
        base, wobble = 1400, 0.15
    else:
        base, wobble = 1100 - 20 * (rel - 18), 0.15
    return base * R.uniform(1 - wobble, 1 + wobble)


def split(total: float) -> dict:
    return {c: round(total * MIX[c] * R.uniform(0.9, 1.1), 2) for c in CHANNELS}


def row(d: dt.date, sales: float, orders: int, new_share: float, spend: dict) -> dict:
    return {"date": d.isoformat(), "net_sales": f"{sales:.2f}", "total_orders": orders,
            "new_customer_orders": min(orders, round(orders * new_share * R.uniform(0.95, 1.05))),
            **{f"{c}_spend": f"{spend[c]:.2f}" for c in CHANNELS}}


def modeled(d: dt.date, spend: dict, demand: float, aov: float, new_share: float, noise: float) -> dict:
    s = sum(spend.values())
    sales = K * demand * DOW[d.weekday()] * s ** B * R.uniform(1 - noise, 1 + noise)
    orders = max(1, round(sales / (aov * R.uniform(0.96, 1.04))))
    return row(d, sales, orders, new_share, spend)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    bfcm = {}
    for line in LAST_BFCM.splitlines():
        d, sales, meta, google, tiktok = line.split(",")
        bfcm[dt.date.fromisoformat(d)] = (float(sales), {"meta": float(meta), "google": float(google),
                                                         "tiktok": float(tiktok)})
    preflight = {}
    for line in PREFLIGHT.splitlines():
        d, orders, sales = line.split(",")
        preflight[dt.date.fromisoformat(d)] = (int(orders), float(sales))

    rows = []
    for d in LY_DAYS:
        rel = (d - LY_BF).days
        if d in bfcm:
            sales, spend = bfcm[d]
            orders = round(sales / (BFCM_AOV * R.uniform(0.985, 1.015)))
            rows.append(row(d, sales, orders, BFCM_NEW_SHARE, spend))
            continue
        aov, new_share = (87.0, 0.52) if rel <= -28 else (87.5, 0.55)
        rows.append(modeled(d, split(ly_spend(rel)), season(rel), aov, new_share, 0.05))
    for d in TY_DAYS:
        spend = split(950 * R.uniform(0.8, 1.2))
        if d in preflight:
            orders, sales = preflight[d]
            rows.append(row(d, sales, orders, 0.50, spend))
            continue
        summer = 0.97 if d.month in (7, 8) else 1.0
        rows.append(modeled(d, spend, GROWTH * summer, 89.0, 0.50, 0.10))
    cols = ["date", "net_sales", "total_orders", "new_customer_orders"] + [f"{c}_spend" for c in CHANNELS]
    with open(os.path.join(OUT, "history.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    economics = {
        "brand": "Fernhill Goods",
        "currency": "USD",
        "plan_year": 2026,
        "plan_window": {"start": "2026-11-01", "end": "2026-12-06"},
        "cyber_week": {"start": "2026-11-26", "end": "2026-12-01"},
        "sale_window": {"start": "2026-11-24", "end": "2026-12-01"},
        "gross_margin_pct": 64,
        "shipping_fulfillment_per_order": 11.0,
        "payment_fee_pct": 2.9,
        "payment_fee_fixed": 0.30,
        "return_rate_pct": 7,
        "discount_pct": 25,
        "target": {"type": "margin_floor", "min_contribution_pct": 12},
        "total_budget_cap": 87000,             # last year's Nov 1 - Dec 6 spend ($75,323) x ~1.15
        "max_daily_increase_pct": 30,
        "channel_shares": {
            "meta": {"min": 45, "max": 65},
            "google": {"min": 25, "max": 40},
            "tiktok": {"min": 8, "max": 18},
        },
    }
    with open(os.path.join(OUT, "economics.json"), "w") as f:
        json.dump(economics, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
