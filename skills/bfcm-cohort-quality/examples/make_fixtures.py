#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional: a Shopify home & kitchen
brand in Columbus, Ohio (store time zone America/New_York), about $1M of net
sales a year. The files hold three acquisition cohorts and every order those
customers placed through Oct 6, 2026:

- October 2025 (Oct 1-31): about 12 new customers a day, mostly full price
  or WELCOME10.
- BFCM 2025 (Mon Nov 24 - Tue Dec 2): 843 first orders, 62% of the store's
  1,360 BFCM orders (the other 517 came from earlier customers, who are not in
  this file). VIPEARLY ran Nov 25-26; the automatic "25% off sitewide" ran
  Nov 27 - Dec 2. WELCOME10 and the creator code MAYA20 were able to stack on
  the sale (32.5% and 40% off), the same misconfiguration the BFCM Offer &
  Discount Config QA sample finds in this year's setup.
- Jan-Feb 2026 (Jan 1 - Feb 28): about 9 new customers a day.

Planted patterns (what the report should find):
- BFCM new customers come back less and lean on discounts more.
- First orders above 25% off (stacked codes) are the worst customers.
- Ceramic Pour-Over Set first orders repeat best, often into the Stoneware
  Mug Set.
- TikTok-acquired BFCM customers repeat least (and used MAYA20 most).
- utm_source is spelled several ways (facebook / fb / Facebook / ig /
  instagram, google / Google / adwords, tiktok / TikTok).

A real export holds every customer; the analyzer finds each customer's first
order itself. To keep the file under 256 KB, this sample holds only customers
whose first-ever order falls in one of the three cohorts, so examples/run.sh
passes --history-since with the store's (fictional) first order date.

Daily ad spend for Oct 1 - Dec 31, 2025 is the same as the BFCM Holiday Budget
Planner's sample (rounded to whole dollars), so Nov 24 - Dec 2, 2025 is last BFCM
exactly as the other skills in this collection use it ($30,000: Meta $16,200,
Google $9,720, TikTok $4,080). Jan - Feb 2026 spend is generated here.

    python3 examples/make_fixtures.py   # rewrites examples/data/*
"""
import csv
import datetime as dt
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
R = random.Random(20251128)

AS_OF = dt.date(2026, 10, 6)
D = dt.date
DAY = dt.timedelta(days=1)

PRODUCTS = {"linen-apron": 48, "ceramic-pour-over-set": 64, "waxed-canvas-tote": 78,
            "stoneware-mug-set": 56, "merino-wool-throw": 140, "walnut-serving-board": 72}

OCT = (D(2025, 10, 1), D(2025, 10, 31))
BFCM = (D(2025, 11, 24), D(2025, 12, 2))
JF = (D(2026, 1, 1), D(2026, 2, 28))
BFCM_NEW = [30, 34, 46, 88, 214, 132, 104, 134, 61]          # first orders per day, 843 in all
BFCM_STORE = [60, 65, 84, 142, 320, 202, 166, 215, 106]      # all store orders per day, 1,360 in all
SPEND_2025 = """\
2025-10-01,374,220,99
2025-10-02,415,245,97
2025-10-03,657,345,157
2025-10-04,524,309,126
2025-10-05,414,241,86
2025-10-06,568,327,144
2025-10-07,597,307,141
2025-10-08,366,244,98
2025-10-09,473,261,120
2025-10-10,585,359,132
2025-10-11,371,247,99
2025-10-12,626,338,129
2025-10-13,482,252,109
2025-10-14,467,243,100
2025-10-15,568,357,136
2025-10-16,536,316,119
2025-10-17,628,369,153
2025-10-18,425,237,102
2025-10-19,634,374,148
2025-10-20,446,270,108
2025-10-21,444,241,94
2025-10-22,497,306,111
2025-10-23,597,355,120
2025-10-24,372,224,98
2025-10-25,571,308,148
2025-10-26,660,349,152
2025-10-27,492,284,125
2025-10-28,388,268,107
2025-10-29,527,310,132
2025-10-30,386,226,88
2025-10-31,558,301,125
2025-11-01,566,353,150
2025-11-02,441,275,107
2025-11-03,417,246,111
2025-11-04,626,350,156
2025-11-05,485,283,128
2025-11-06,725,367,172
2025-11-07,560,331,146
2025-11-08,613,310,129
2025-11-09,450,294,120
2025-11-10,501,256,99
2025-11-11,405,250,98
2025-11-12,556,344,143
2025-11-13,475,279,113
2025-11-14,453,242,95
2025-11-15,658,387,151
2025-11-16,465,265,110
2025-11-17,437,276,121
2025-11-18,623,359,169
2025-11-19,620,350,131
2025-11-20,784,435,164
2025-11-21,776,506,188
2025-11-22,982,521,226
2025-11-23,833,460,176
2025-11-24,640,360,200
2025-11-25,890,510,220
2025-11-26,1120,540,280
2025-11-27,1600,1210,460
2025-11-28,3700,2180,780
2025-11-29,2890,1170,670
2025-11-30,1730,1250,490
2025-12-01,2340,1640,720
2025-12-02,1290,860,260
2025-12-03,915,562,217
2025-12-04,778,447,170
2025-12-05,771,513,206
2025-12-06,1029,668,257
2025-12-07,998,499,220
2025-12-08,814,523,204
2025-12-09,777,420,199
2025-12-10,870,489,181
2025-12-11,759,418,174
2025-12-12,623,346,153
2025-12-13,765,465,169
2025-12-14,763,389,183
2025-12-15,841,464,206
2025-12-16,690,416,154
2025-12-17,698,393,146
2025-12-18,570,346,134
2025-12-19,587,344,153
2025-12-20,490,289,116
2025-12-21,501,299,122
2025-12-22,490,299,131
2025-12-23,504,326,137
2025-12-24,533,288,108
2025-12-25,489,295,124
2025-12-26,494,322,112
2025-12-27,512,280,106
2025-12-28,505,299,115
2025-12-29,424,225,100
2025-12-30,504,303,118
2025-12-31,433,245,101"""
SPEND_2025 = {line.split(",")[0]: tuple(int(v) for v in line.split(",")[1:]) for line in SPEND_2025.splitlines()}
VIP_DAYS = (D(2025, 11, 25), D(2025, 11, 26))
SALE_DAYS = (D(2025, 11, 27), D(2025, 12, 2))

# Repeat-purchase model: daily hazard = base x customer frailty x source x first-order band x entry product
# x season, damped for 14 days after each order.
BASE = {"oct": 0.0024, "bfcm": 0.0017, "jf": 0.0021}
FRAILTY = [(0.10, 0.58), (0.80, 0.32), (2.3, 0.10)]
SRC_MULT = {"meta": 1.0, "google": 1.15, "tiktok": 0.25, "klaviyo": 1.3, "pinterest": 1.0, "none": 1.15}
BAND_MULT = {0: 1.25, 10: 1.15, 15: 1.15, 20: 1.0, 25: 1.0, 32.5: 0.55, 40: 0.30, 46: 0.30}
PROD_MULT = {"ceramic-pour-over-set": 2.1, "stoneware-mug-set": 1.15, "walnut-serving-board": 0.95,
             "waxed-canvas-tote": 0.9, "merino-wool-throw": 0.85, "linen-apron": 0.6}


def pick(weights):
    x, acc = R.random() * sum(weights.values()), 0.0
    for k, w in weights.items():
        acc += w
        if x < acc:
            return k
    return k


def season(d):
    if VIP_DAYS[0] <= d <= SALE_DAYS[1]:
        return 3.0
    if D(2025, 12, 3) <= d <= D(2025, 12, 20):
        return 1.7
    if D(2025, 12, 21) <= d <= D(2026, 1, 10):
        return 0.6
    if D(2026, 5, 1) <= d <= D(2026, 5, 10):
        return 1.4
    if D(2026, 6, 15) <= d <= D(2026, 7, 31):
        return 1.3
    return 1.0


# Store orders per day for Sep 8 - Oct 5, 2026, as in the BFCM Tracking & Attribution Preflight sample
# (901 orders, #18241-#19141).
PREFLIGHT_DAYS = [26, 35, 30, 30, 39, 33, 28, 30, 32, 29, 30, 32, 33, 33, 26, 28, 37, 29, 31, 40, 37, 36, 33, 28,
                  37, 42, 31, 26]


def store_orders(d):
    """All orders the store took that day (only some of them are in this file)."""
    if BFCM[0] <= d <= BFCM[1]:
        return BFCM_STORE[(d - BFCM[0]).days]
    if D(2026, 9, 8) <= d <= D(2026, 10, 5):
        return PREFLIGHT_DAYS[(d - D(2026, 9, 8)).days]
    if d.year == 2025 and d.month == 10:
        return 29
    if d.year == 2025 and d.month == 11:
        return 31
    if d.year == 2025 and d.month == 12:
        return {24: 22, 25: 6}.get(d.day, 40 if d.day <= 23 else 28)
    if d <= D(2026, 2, 28):
        return 25
    return 29 if d <= D(2026, 8, 31) else 32


def eastern_offset(d):
    second_sun_mar = D(d.year, 3, 8) + dt.timedelta(days=(6 - D(d.year, 3, 8).weekday()) % 7)
    first_sun_nov = D(d.year, 11, 1) + dt.timedelta(days=(6 - D(d.year, 11, 1).weekday()) % 7)
    return "-04:00" if second_sun_mar <= d < first_sun_nov else "-05:00"


HOURS = [2, 1, 1, 0.5, 0.5, 0.8, 1.5, 2.5, 3.5, 4.5, 5, 5, 5.5, 5.5, 5, 5, 5, 5, 5.5, 6.5, 7, 6.5, 5, 3.5]


def order_time(d):
    w = list(HOURS)
    if d == SALE_DAYS[0]:          # midnight launch of the main sale
        w[0], w[1] = 9, 5
    hour = pick({h: x for h, x in enumerate(w)})
    return hour * 3600 + R.randrange(3600)


def basket(n_items, weights, first=None):
    items = []
    if first:
        items.append(first)
    while len(items) < n_items:
        p = pick(weights)
        if p not in items:
            items.append(p)
    gross = 0
    for p in items:
        qty = 2 if R.random() < 0.06 else 1
        gross += PRODUCTS[p] * qty
    return items, gross


def priced(gross, depth):
    net = round(gross * (1 - depth / 100.0), 2)
    return net, round(gross - net, 2)


# ----------------------------------------------------------------------------- first orders
SRC_MIX = {
    "oct": {"meta": 0.50, "google": 0.27, "tiktok": 0.09, "klaviyo": 0.04, "pinterest": 0.03, "none": 0.07},
    "bfcm": {"meta": 0.53, "google": 0.26, "tiktok": 0.13, "klaviyo": 0.02, "pinterest": 0.01, "none": 0.05},
    "jf": {"meta": 0.48, "google": 0.29, "tiktok": 0.08, "klaviyo": 0.04, "pinterest": 0.03, "none": 0.08},
}
SPELL = {"meta": {"facebook": 0.60, "fb": 0.14, "Facebook": 0.11, "ig": 0.09, "instagram": 0.06},
         "google": {"google": 0.86, "Google": 0.09, "adwords": 0.05}, "tiktok": {"tiktok": 0.84, "TikTok": 0.16},
         "klaviyo": {"klaviyo": 1.0}, "pinterest": {"pinterest": 1.0}, "none": {"": 1.0}}
FIRST_PRODUCTS = {
    "oct": {"linen-apron": 17, "ceramic-pour-over-set": 17, "waxed-canvas-tote": 17, "stoneware-mug-set": 17,
            "merino-wool-throw": 16, "walnut-serving-board": 16},
    "bfcm": {"linen-apron": 18, "ceramic-pour-over-set": 15, "waxed-canvas-tote": 15, "stoneware-mug-set": 17,
             "merino-wool-throw": 17, "walnut-serving-board": 18},
    "jf": {"linen-apron": 15, "ceramic-pour-over-set": 19, "waxed-canvas-tote": 15, "stoneware-mug-set": 19,
           "merino-wool-throw": 14, "walnut-serving-board": 18},
}
ITEMS = {"oct": {1: 0.82, 2: 0.16, 3: 0.02}, "bfcm": {1: 0.64, 2: 0.28, 3: 0.08}, "jf": {1: 0.80, 2: 0.17, 3: 0.03}}
OFFPEAK_CODES = {("", 0): 0.57, ("WELCOME10", 10): 0.33, ("MAYA20", 20): 0.07, ("SUMMER15", 15): 0.03}


def first_discount(cohort, d, src):
    if cohort != "bfcm":
        return pick(OFFPEAK_CODES)
    if d < VIP_DAYS[0]:
        return pick({("", 0): 0.52, ("WELCOME10", 10): 0.40, ("MAYA20", 20): 0.05, ("SUMMER15", 15): 0.03})
    if d <= VIP_DAYS[1]:
        return pick({("VIPEARLY", 25): 0.50, ("WELCOME10", 10): 0.25, ("", 0): 0.13,
                     ("VIPEARLY;WELCOME10", 32.5): 0.08, ("MAYA20", 20): 0.04})
    if src == "tiktok":
        w = {("", 25): 0.47, ("WELCOME10", 32.5): 0.08, ("MAYA20", 40): 0.43, ("MAYA20;WELCOME10", 46): 0.02}
    elif src == "meta":
        w = {("", 25): 0.76, ("WELCOME10", 32.5): 0.11, ("MAYA20", 40): 0.12, ("MAYA20;WELCOME10", 46): 0.01}
    elif src == "google":
        w = {("", 25): 0.79, ("WELCOME10", 32.5): 0.10, ("MAYA20", 40): 0.10, ("MAYA20;WELCOME10", 46): 0.01}
    else:
        w = {("", 25): 0.80, ("WELCOME10", 32.5): 0.12, ("MAYA20", 40): 0.07, ("MAYA20;WELCOME10", 46): 0.01}
    return pick(w)


def new_customers(cohort, d):
    if cohort == "bfcm":
        return BFCM_NEW[(d - BFCM[0]).days]
    base = 11 if cohort == "oct" else 8
    if d.weekday() >= 5:
        base += 2
    return max(4, base + R.randint(-3, 3))


# ----------------------------------------------------------------------------- repeat orders
REPEAT_PRODUCTS = {"linen-apron": 17, "ceramic-pour-over-set": 15, "waxed-canvas-tote": 14, "stoneware-mug-set": 20,
                   "merino-wool-throw": 14, "walnut-serving-board": 20}
REPEAT_SRC = {"klaviyo": 0.46, "": 0.30, "facebook": 0.12, "google": 0.09, "tiktok": 0.03}


def repeat_discount(cohort, depth0, d):
    if SALE_DAYS[0] <= d <= SALE_DAYS[1]:
        return ("MAYA20", 40) if R.random() < 0.10 else ("", 25)
    if VIP_DAYS[0] <= d <= VIP_DAYS[1]:
        return ("VIPEARLY", 25) if R.random() < 0.85 else ("", 0)
    if cohort == "bfcm":
        p = 0.72 if depth0 > 25 else 0.25 if depth0 == 0 else 0.52
    else:
        p = 0.22 if cohort == "oct" else 0.20
    if R.random() >= p:
        return ("", 0)
    if d >= D(2026, 6, 15):
        return pick({("SUMMER15", 15): 0.55, ("THANKYOU15", 15): 0.30, ("MAYA20", 20): 0.15})
    return pick({("THANKYOU15", 15): 0.65, ("SUMMER15", 15): 0.15, ("MAYA20", 20): 0.20})


def refund_for(items_net, p):
    if R.random() >= p:
        return 0.0
    total = round(sum(items_net), 2)
    if len(items_net) == 1 or R.random() < 0.6:
        return total
    return round(R.choice(items_net), 2)


def line_nets(items, gross, net):
    """Split an order's net sales across its products in proportion to price (for partial refunds)."""
    prices = [PRODUCTS[p] for p in items]
    s = sum(prices)
    return [net * x / s for x in prices]


def build():
    orders = []          # dicts: day, secs, cust, net, disc, codes, refund, src, items
    cid = 0              # temporary key; assign_ids() turns it into a Shopify-style customer ID
    cohorts = [("oct", OCT), ("bfcm", BFCM), ("jf", JF)]
    for cohort, (a, b) in cohorts:
        d = a
        while d <= b:
            for _ in range(new_customers(cohort, d)):
                cid += 1
                src = pick(SRC_MIX[cohort])
                raw_src = pick(SPELL[src])
                code, depth0 = first_discount(cohort, d, src)
                items, gross = basket(pick(ITEMS[cohort]), FIRST_PRODUCTS[cohort])
                net, disc = priced(gross, depth0)
                p_ref = {"oct": 0.050, "jf": 0.055}.get(cohort, 0.15 if depth0 > 25 else 0.10)
                secs = order_time(d)
                orders.append({"day": d, "secs": secs, "cust": cid, "net": net, "disc": disc, "codes": code,
                               "refund": refund_for(line_nets(items, gross, net), p_ref), "src": raw_src,
                               "items": items})
                # A second order the same day (a forgotten item): counts toward value, not as a repeat.
                if R.random() < (0.025 if cohort == "bfcm" else 0.012):
                    extra, g2 = basket(1, REPEAT_PRODUCTS)
                    n2, d2 = priced(g2, depth0 if depth0 in (25, 32.5, 40, 46) else 0)
                    orders.append({"day": d, "secs": min(86399, secs + R.randint(300, 5400)), "cust": cid,
                                   "net": n2, "disc": d2, "codes": code if depth0 in (32.5, 40, 46) else "",
                                   "refund": 0.0, "src": raw_src, "items": extra})
                # Repeat purchases, day by day, through the as-of date.
                frail = pick({f: w for f, w in FRAILTY})
                mult = (BASE[cohort] * frail * SRC_MULT[src] * BAND_MULT[depth0]
                        * max(PROD_MULT[p] for p in items))
                quiet_until = d + dt.timedelta(days=14)
                n_rep = 0
                day = d + DAY
                while day <= AS_OF:
                    hz = mult * season(day) * (0.2 if day <= quiet_until else 1.0)
                    if R.random() < hz:
                        n_rep += 1
                        nitems = pick({1: 0.76, 2: 0.20, 3: 0.04})
                        first_item = None
                        if n_rep == 1 and "ceramic-pour-over-set" in items and "stoneware-mug-set" not in items \
                                and R.random() < 0.55:
                            first_item = "stoneware-mug-set"
                        ritems, rgross = basket(nitems, REPEAT_PRODUCTS, first_item)
                        rcode, rdepth = repeat_discount(cohort, depth0, day)
                        rnet, rdisc = priced(rgross, rdepth)
                        orders.append({"day": day, "secs": order_time(day), "cust": cid, "net": rnet, "disc": rdisc,
                                       "codes": rcode, "refund": refund_for(line_nets(ritems, rgross, rnet), 0.04),
                                       "src": pick(REPEAT_SRC), "items": ritems})
                        quiet_until = day + dt.timedelta(days=14)
                    day += DAY
            d += DAY
    return orders


def number_orders(orders):
    """Give each order the store-wide order number it would have had (#18241 is the first order of Sep 8, 2026)."""
    start = D(2025, 10, 1)
    before_sep8 = 0
    d = start
    while d < D(2026, 9, 8):
        before_sep8 += store_orders(d)
        d += DAY
    first_num = 18241 - before_sep8
    by_day = {}
    for o in orders:
        by_day.setdefault(o["day"], []).append(o)
    num = first_num
    d = start
    while d <= AS_OF:
        cap = store_orders(d)
        todays = sorted(by_day.get(d, []), key=lambda o: (o["secs"], o["cust"]))
        assert len(todays) <= cap, (d, len(todays), cap)
        for j, o in enumerate(todays):
            o["num"] = num + (j * cap) // max(1, len(todays))
        num += cap
        d += DAY


def assign_ids(orders):
    """Customer IDs grow with each customer's first purchase, as Shopify's do."""
    first = {}
    for o in orders:
        k = (o["day"], o["secs"], o["num"])
        if o["cust"] not in first or k < first[o["cust"]]:
            first[o["cust"]] = k
    ids, cur = {}, 5823019000
    for c in sorted(first, key=lambda c: first[c]):
        cur += R.randint(400, 2600)
        ids[c] = cur
    for o in orders:
        o["cust"] = ids[o["cust"]]


def spend_rows():
    rows = []
    d = D(2025, 10, 1)
    while d <= D(2026, 2, 28):
        if d.isoformat() in SPEND_2025:
            m, g, t = SPEND_2025[d.isoformat()]
        else:
            if d.month == 10:
                base = 690.0
            elif d.month == 11:
                base = 800 + 350 * (d.day - 1) / 22.0
            elif d.month == 12 and d.day <= 20:
                base = 1150 - 350 * (d.day - 3) / 17.0
            elif d.month == 12:
                base = 520.0
            else:
                base = 560.0
            base *= (1.06 if d.weekday() >= 5 else 0.98) * R.uniform(0.92, 1.08)
            m, g, t = round(base * 0.55), round(base * 0.32), round(base * 0.13)
        rows.append([d.isoformat(), m, g, t])
        d += DAY
    return rows


def fmt_money(x):
    return f"{x:.2f}"


def main():
    os.makedirs(OUT, exist_ok=True)
    orders = build()
    number_orders(orders)
    assign_ids(orders)
    orders.sort(key=lambda o: (o["day"], o["secs"], o["num"]))
    with open(os.path.join(OUT, "orders.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["order_id", "customer_id", "created_at", "net_sales", "discount_amount", "discount_codes",
                    "refunded_amount", "utm_source", "products"])
        for o in orders:
            s = o["secs"]
            ts = f"{o['day'].isoformat()}T{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}{eastern_offset(o['day'])}"
            w.writerow([f"#{o['num']}", o["cust"], ts, fmt_money(o["net"]), fmt_money(o["disc"]), o["codes"],
                        fmt_money(o["refund"]), o["src"], ";".join(o["items"])])
    with open(os.path.join(OUT, "spend.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "meta_spend", "google_spend", "tiktok_spend"])
        w.writerows(spend_rows())
    econ = {"brand": "Fernhill Goods", "currency": "USD", "gross_margin_pct": 64, "shipping_fulfillment_per_order": 11.0,
            "payment_fee_pct": 2.9, "payment_fee_fixed": 0.3, "return_rate_pct": 7, "discount_pct": 25,
            "target": {"type": "margin_floor", "min_contribution_pct": 12}}
    with open(os.path.join(OUT, "economics.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(econ, f, indent=2)
        f.write("\n")
    print(f"wrote {len(orders)} orders, spend and economics to {OUT}")


if __name__ == "__main__":
    main()
