#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/ and examples/quiet/data/.

The sample store, "Fernhill Goods", is fictional: the Shopify home and kitchen
goods brand that every type.com BFCM skill uses (store time America/New_York,
~320 orders and $28,320 net sales on Black Friday 2025).

plan.csv (both samples) is the BFCM Holiday Budget Planner's sample --csv
daily plan, verbatim. Black Friday 2026: budget $6,672 (Meta $3,693, Google
$2,110, TikTok $869), base $31,393 (low $26,684, high $34,533), plan MER
4.71, pull back below MER 4.00, scale above 5.18, checks at noon and 6 pm.
Cyber Monday 2026: budget $3,954 (Meta $2,188, Google $1,251, TikTok $515),
base $18,599 (low $15,809, high $20,459), plan MER 4.70, pull back below
4.00, scale above 5.17, checks at noon and 6 pm.

Main sample: Black Friday, Fri Nov 27, 2026, as of 2:00 pm ET (midnight to
2 pm complete).
- ly_hourly.csv: Black Friday 2025 (Nov 28) by hour, 320 orders and exactly
  $28,320.00 net sales, with sessions and checkout funnel.
- hourly.csv: regular demand runs about 5% above last year's same hours,
  short of the plan's +11% (base $31,393 vs last year's $28,320). Creator
  code MAYA20 leaks at noon: 12 uses in the noon hour and 40 in the 1 pm hour
  (usual 0.6/hour), mostly deal-hunter orders at ~25% lower order value. They
  lift the pace to just under base (pacing $31,128, 0.8% under) and pull AOV
  over noon-2 pm ~17% below last year. Checkout conversion collapses in the
  11 am hour (13 orders from 35 sessions at checkout) and is back at noon.
- spend_today.csv: Meta, Google and TikTok spend so far, read 1:20-1:50 pm,
  1-3% ahead of the expected pace ($3,004 of $6,672). MER to 1:20 pm (the
  earliest read) is 4.32, between the plan's pull-back (4.00) and scale
  (5.18) lines, so the call is HOLD.
- discount_usage.csv: MAYA20, WELCOME10 and SUMMER15 uses by hour with their
  last-7-day baselines.
- inventory.csv: on hand now and units sold 11 am-2 pm for six products.
  Linen Apron has 46 left after 30 sold: 4.6 hours of cover.

Quiet sample: Cyber Monday, Mon Nov 30, 2026, as of 9:00 am ET (it reads
examples/data/plan.csv). Nothing is wrong: pacing $19,461, 4.6% over base and
under high, spend 1% ahead of pace, MER 4.79 before the first (noon) check,
no alerts. So quiet mode prints nothing.

    python3 examples/make_fixtures.py   # rewrites examples/data/* and examples/quiet/data/*
"""
import csv
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
R = random.Random(20261127)

# The BFCM Holiday Budget Planner's sample --csv output, byte for byte.
PLAN_CSV = """\
date,phase,tag,total_budget,meta_budget,google_budget,tiktok_budget,other_budget,proj_net_sales_low,proj_net_sales_base,proj_net_sales_high,proj_mer_base,proj_contribution_base,ly_date,ly_net_sales,ly_ad_spend,breakeven_mer,target_mer,pull_back_mer,scale_mer,check_times
2026-11-01,warm-up,,951,526,301,124,0,2471.69,2907.87,3198.66,3.0577,320.90,2025-11-02,2393.44,824.06,2.29,3.15,2.60,3.36,14:00
2026-11-02,warm-up,,951,526,301,124,0,2452.00,2884.71,3173.18,3.0333,310.77,2025-11-03,2303.21,774.09,2.29,3.15,2.58,3.34,14:00
2026-11-03,warm-up,,951,526,301,124,0,2487.13,2926.03,3218.63,3.0768,328.84,2025-11-04,2872.58,1131.74,2.29,3.15,2.62,3.38,14:00
2026-11-04,warm-up,,951,526,301,124,0,2518.41,2962.84,3259.12,3.1155,344.94,2025-11-05,2548.66,896.22,2.29,3.15,2.65,3.43,14:00
2026-11-05,warm-up,,951,526,301,124,0,2521.37,2966.31,3262.94,3.1192,346.46,2025-11-06,3076.01,1263.02,2.29,3.15,2.65,3.43,14:00
2026-11-06,warm-up,,951,526,301,124,0,2527.89,2973.98,3271.38,3.1272,349.82,2025-11-07,3006.51,1037.11,2.29,3.15,2.66,3.44,14:00
2026-11-07,warm-up,,951,526,301,124,0,2573.01,3027.08,3329.78,3.1830,373.04,2025-11-08,3085.13,1051.50,2.29,3.15,2.71,3.50,14:00
2026-11-08,warm-up,,951,526,301,124,0,2603.68,3063.15,3369.46,3.2210,388.82,2025-11-09,2494.65,864.03,2.29,3.15,2.74,3.54,14:00
2026-11-09,warm-up,,951,526,301,124,0,2643.40,3109.89,3420.87,3.2701,409.26,2025-11-10,2516.96,856.33,2.29,3.15,2.78,3.60,14:00
2026-11-10,warm-up,,951,526,301,124,0,2679.76,3152.66,3467.93,3.3151,427.97,2025-11-11,2444.74,752.96,2.29,3.15,2.82,3.65,14:00
2026-11-11,warm-up,,951,526,301,124,0,2673.27,3145.02,3459.52,3.3071,424.63,2025-11-12,3076.51,1042.55,2.29,3.15,2.81,3.64,14:00
2026-11-12,warm-up,,951,526,301,124,0,2704.46,3181.72,3499.89,3.3457,440.68,2025-11-13,2640.38,866.80,2.29,3.15,2.84,3.68,14:00
2026-11-13,warm-up,,951,526,301,124,0,2719.01,3198.84,3518.72,3.3637,448.17,2025-11-14,2723.11,790.25,2.29,3.15,2.86,3.70,14:00
2026-11-14,warm-up,,951,526,301,124,0,2698.29,3174.46,3491.91,3.3380,437.51,2025-11-15,3320.51,1196.57,2.29,3.15,2.84,3.67,14:00
2026-11-15,warm-up,,951,526,301,124,0,2701.66,3178.42,3496.26,3.3422,439.24,2025-11-16,2658.67,840.03,2.29,3.15,2.84,3.68,14:00
2026-11-16,warm-up,,951,526,301,124,0,2712.78,3191.50,3510.65,3.3559,444.96,2025-11-17,2571.65,834.96,2.29,3.15,2.85,3.69,14:00
2026-11-17,warm-up,,951,526,301,124,0,2705.15,3182.53,3500.78,3.3465,441.04,2025-11-18,3113.47,1150.61,2.29,3.15,2.84,3.68,14:00
2026-11-18,warm-up,,951,526,301,124,0,2751.93,3237.57,3561.33,3.4044,465.11,2025-11-19,3222.07,1100.07,2.29,3.15,2.89,3.74,14:00
2026-11-19,warm-up,,951,526,301,124,0,2817.94,3315.23,3646.75,3.4860,499.08,2025-11-20,3769.50,1383.08,2.29,3.15,2.96,3.83,14:00
2026-11-20,warm-up,,1190,659,376,155,0,3559.41,4187.54,4606.29,3.5189,641.62,2025-11-21,4126.38,1469.21,2.29,3.15,2.99,3.87,14:00
2026-11-21,warm-up,,1423,788,450,185,0,4255.50,5006.47,5507.12,3.5183,766.83,2025-11-22,4825.53,1729.17,2.29,3.15,2.99,3.87,14:00
2026-11-22,warm-up,,1798,995,569,234,0,5349.99,6294.11,6923.52,3.5006,955.04,2025-11-23,4595.81,1468.31,2.29,3.15,2.98,3.85,14:00
2026-11-23,warm-up,,2337,1294,739,304,0,6645.56,7818.30,8600.13,3.3454,1082.72,2025-11-24,5280.00,1200.00,2.29,3.15,2.84,3.68,14:00
2026-11-24,warm-up,,3038,1682,961,395,0,8290.72,9753.79,10729.17,3.2106,152.97,2025-11-25,5760.00,1620.00,3.06,4.83,3.06,4.83,14:00
2026-11-25,warm-up,,3949,2186,1249,514,0,10428.04,12268.29,13495.11,3.1067,64.60,2025-11-26,7440.00,1940.00,3.06,4.83,3.06,4.83,14:00
2026-11-26,cyber week,Thanksgiving,5133,2841,1624,668,0,16103.06,18944.78,20839.26,3.6908,1064.83,2025-11-27,12480.00,3270.00,3.06,4.83,3.14,4.83,12:00;18:00
2026-11-27,cyber week,Black Friday,6672,3693,2110,869,0,26684.43,31393.45,34532.79,4.7053,3598.44,2025-11-28,28320.00,6660.00,3.06,4.83,4.00,5.18,12:00;18:00
2026-11-28,cyber week,,3336,1847,1055,434,0,13089.28,15399.16,16939.07,4.6161,1701.87,2025-11-29,17760.00,4730.00,3.06,4.83,3.92,5.08,12:00;18:00
2026-11-29,cyber week,,3390,1876,1073,441,0,13554.01,15945.89,17540.48,4.7038,1826.73,2025-11-30,14640.00,3470.00,3.06,4.83,4.00,5.17,12:00;18:00
2026-11-30,cyber week,Cyber Monday,3954,2188,1251,515,0,15809.31,18599.19,20459.11,4.7039,2130.77,2025-12-01,18960.00,4700.00,3.06,4.83,4.00,5.17,12:00;18:00
2026-12-01,cyber week,,1977,1094,626,257,0,7668.07,9021.26,9923.38,4.5631,974.32,2025-12-02,9360.00,2410.00,3.06,4.83,3.88,5.02,12:00;18:00
2026-12-02,taper,,989,547,313,129,0,4335.30,5100.35,5610.38,5.1571,1241.89,2025-12-03,6248.48,1693.42,2.29,3.15,4.38,5.67,14:00
2026-12-03,taper,,951,526,301,124,0,4160.40,4894.59,5384.05,5.1468,1189.89,2025-12-04,5173.08,1394.05,2.29,3.15,4.37,5.66,14:00
2026-12-04,taper,,951,526,301,124,0,4160.40,4894.59,5384.05,5.1468,1189.89,2025-12-05,5618.70,1489.84,2.29,3.15,4.37,5.66,14:00
2026-12-05,taper,,951,526,301,124,0,4160.40,4894.59,5384.05,5.1468,1189.89,2025-12-06,6241.37,1954.19,2.29,3.15,4.37,5.66,14:00
2026-12-06,taper,,951,526,301,124,0,4079.21,4799.07,5278.98,5.0463,1148.11,2025-12-07,5296.01,1717.09,2.29,3.15,4.29,5.55,14:00
"""

FUNNEL = ["sessions", "sessions_with_add_to_cart", "sessions_reached_checkout"]
THRESHOLDS = {
    "orders_stall_pct": 40, "orders_stall_min_expected": 8,
    "checkout_drop_pct": 30, "checkout_baseline_hours": 3, "checkout_min_sessions": 20,
    "aov_drop_pct": 15, "aov_hours": 2, "aov_min_orders": 20,
    "code_multiple": 10, "code_min_uses": 25,
    "cover_hours": 6, "resolve_after_hours": 2, "max_alerts": 5,
    "spend_pace_band_pct": 20, "stale_spend_minutes": 120, "min_pace_share_pct": 10,
    "pull_back_cut_pct": 20, "max_daily_increase_pct": 30, "max_chars": 1800,
}

# Black Friday 2025 orders by hour (sum 320) and Cyber Monday 2025 (sum 214).
LY_BF_ORDERS = [10, 6, 3, 2, 2, 2, 5, 9, 13, 17, 19, 20, 20, 20, 18, 18, 17, 17, 17, 20, 21, 20, 15, 9]
LY_CM_ORDERS = [7, 4, 2, 1, 1, 1, 3, 6, 10, 12, 13, 14, 14, 13, 11, 11, 11, 11, 13, 14, 14, 13, 9, 6]


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def last_year(day, orders, total_sales, aov):
    """Hourly rows for last year's day: sales sum exactly to total_sales."""
    rows = []
    for h, o in enumerate(orders):
        cr = (0.024 if h < 7 else 0.031) * R.uniform(0.92, 1.08)
        reached = max(o, round(o / (0.58 * R.uniform(0.95, 1.05))))
        atc = round(reached * 1.8 * R.uniform(0.95, 1.05))
        rows.append([day, h, o, round(o * aov * R.uniform(0.95, 1.05), 2), round(o / cr), atc, reached])
    gap = round(total_sales - sum(r[3] for r in rows), 2)
    peak = max(range(24), key=lambda h: orders[h])
    rows[peak][3] = round(rows[peak][3] + gap, 2)
    assert abs(sum(r[3] for r in rows) - total_sales) < 0.005 and sum(r[2] for r in rows) == sum(orders)
    for r in rows:
        r[3] = f"{r[3]:.2f}"
    return rows


def config(as_of):
    return {"store": "Fernhill Goods", "timezone": "America/New_York", "tz_label": "ET", "currency_symbol": "$",
            "utc_offset": "-05:00", "as_of": as_of,
            "quiet_mode": {"enabled": False, "summary_times": ["12:00", "18:00"]}, "thresholds": THRESHOLDS}


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def black_friday(out):
    day = "2026-11-27"
    ly = last_year("2025-11-28", LY_BF_ORDERS, 28320.00, 88.50)
    write_csv(os.path.join(out, "ly_hourly.csv"), ["date", "hour", "orders", "net_sales"] + FUNNEL, ly)

    regular = [11, 6, 3, 2, 2, 2, 5, 9, 14, 18, 20, 21, 21, 21]      # ~4% over last year, hours 0-13
    maya = [1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 12, 40]               # MAYA20 uses by hour
    extra = [0] * 12 + [6, 22]                                        # deal-hunter orders the leak added
    welcome = [1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1]
    summer = [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0]
    rows = []
    for h in range(14):
        orders = regular[h] + extra[h]
        if h == 11:
            orders = 13                                               # checkout broke for most of the hour
        coded = maya[h]
        sales = sum(88.5 * R.uniform(0.95, 1.05) for _ in range(orders - coded))
        sales += sum(68.0 * R.uniform(0.97, 1.03) for _ in range(coded))
        traffic = regular[h] + extra[h]                               # sessions follow demand, not the broken hour
        rate = 0.035 if extra[h] else 0.031 * (0.85 if h < 7 else 1.0)
        sessions = round(traffic / (rate * R.uniform(0.95, 1.05)))
        reached = round(traffic / ((0.62 if extra[h] else 0.60) * R.uniform(0.95, 1.05)))
        atc = round(reached * 1.8 * R.uniform(0.95, 1.05))
        rows.append([day, h, orders, f"{sales:.2f}", sessions, atc, reached])
    write_csv(os.path.join(out, "hourly.csv"), ["date", "hour", "orders", "net_sales"] + FUNNEL, rows)

    # Spend so far: each platform a little ahead of last year's sales curve at its read time.
    total = 28320.00
    cum = [0.0]
    for r in ly:
        cum.append(cum[-1] + float(r[3]) / total)

    def share(minutes):
        h = minutes // 60
        return cum[h] + (minutes % 60) / 60 * (cum[h + 1] - cum[h])

    spend = []
    for name, budget, read, factor in (("Meta", 3693, 13 * 60 + 50, 1.03), ("Google Ads", 2110, 13 * 60 + 20, 1.01),
                                       ("TikTok", 869, 13 * 60 + 45, 1.02)):
        spend.append([name, f"{budget * share(read) * factor:.2f}", f"{day}T{read // 60:02d}:{read % 60:02d}-05:00"])
    write_csv(os.path.join(out, "spend_today.csv"), ["platform", "spend", "read_at"], spend)

    codes = []
    for code, uses, base in (("MAYA20", maya, 0.6), ("WELCOME10", welcome, 1.1), ("SUMMER15", summer, 0.1)):
        for h in range(14):
            codes.append([code, h, uses[h], base])
    write_csv(os.path.join(out, "discount_usage.csv"), ["code", "hour", "uses", "baseline_uses_per_hour"], codes)

    write_csv(os.path.join(out, "inventory.csv"), ["sku", "title", "on_hand", "sold_last_3h"], [
        ["linen-apron", "Linen Apron", 46, 30],
        ["ceramic-pour-over-set", "Ceramic Pour-Over Set", 212, 16],
        ["waxed-canvas-tote", "Waxed Canvas Tote", 154, 11],
        ["stoneware-mug-set", "Stoneware Mug Set (4)", 188, 19],
        ["merino-wool-throw", "Merino Wool Throw", 61, 7],
        ["walnut-serving-board", "Walnut Serving Board", 97, 9],
    ])
    write_json(os.path.join(out, "config.json"), config(f"{day} 14:00"))
    with open(os.path.join(out, "plan.csv"), "w", newline="") as f:
        f.write(PLAN_CSV)


def cyber_monday(out):
    day = "2026-11-30"
    ly = last_year("2025-12-01", LY_CM_ORDERS, 18960.00, 88.60)
    write_csv(os.path.join(out, "ly_hourly.csv"), ["date", "hour", "orders", "net_sales"] + FUNNEL, ly)
    rows = []
    for h in range(9):
        orders = LY_CM_ORDERS[h] + (1 if h == 8 else 0)
        sales = sum(88.6 * R.uniform(0.95, 1.05) for _ in range(orders))
        sessions = round(orders / (0.031 * (0.85 if h < 7 else 1.0) * R.uniform(0.95, 1.05)))
        reached = round(orders / (0.60 * R.uniform(0.95, 1.05)))
        atc = round(reached * 1.8 * R.uniform(0.95, 1.05))
        rows.append([day, h, orders, f"{sales:.2f}", sessions, atc, reached])
    write_csv(os.path.join(out, "hourly.csv"), ["date", "hour", "orders", "net_sales"] + FUNNEL, rows)
    total = 18960.00
    cum = [0.0]
    for r in ly:
        cum.append(cum[-1] + float(r[3]) / total)

    def share(minutes):
        h = minutes // 60
        return cum[h] + (minutes % 60) / 60 * (cum[h + 1] - cum[h])

    spend = []
    for name, budget, read, factor in (("Meta", 2188, 8 * 60 + 50, 1.03), ("Google Ads", 1251, 8 * 60 + 20, 0.96),
                                       ("TikTok", 515, 8 * 60 + 45, 1.01)):
        spend.append([name, f"{budget * share(read) * factor:.2f}", f"{day}T{read // 60:02d}:{read % 60:02d}-05:00"])
    write_csv(os.path.join(out, "spend_today.csv"), ["platform", "spend", "read_at"], spend)
    codes = []
    for code, uses, base in (("MAYA20", [0] * 9, 0.6), ("WELCOME10", [0, 1, 0, 0, 0, 0, 0, 1, 1], 1.1),
                             ("SUMMER15", [0] * 9, 0.1)):
        for h in range(9):
            codes.append([code, h, uses[h], base])
    write_csv(os.path.join(out, "discount_usage.csv"), ["code", "hour", "uses", "baseline_uses_per_hour"], codes)
    write_csv(os.path.join(out, "inventory.csv"), ["sku", "title", "on_hand", "sold_last_3h"], [
        ["linen-apron", "Linen Apron", 118, 4],
        ["ceramic-pour-over-set", "Ceramic Pour-Over Set", 171, 5],
        ["waxed-canvas-tote", "Waxed Canvas Tote", 129, 3],
        ["stoneware-mug-set", "Stoneware Mug Set (4)", 150, 4],
        ["merino-wool-throw", "Merino Wool Throw", 48, 2],
        ["walnut-serving-board", "Walnut Serving Board", 80, 2],
    ])
    write_json(os.path.join(out, "config.json"), config(f"{day} 09:00"))


def main():
    main_dir = os.path.join(HERE, "data")
    quiet_dir = os.path.join(HERE, "quiet", "data")
    os.makedirs(main_dir, exist_ok=True)
    os.makedirs(quiet_dir, exist_ok=True)
    black_friday(main_dir)
    cyber_monday(quiet_dir)


if __name__ == "__main__":
    main()
