#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional. Its numbers are modeled on
a real October 2026 incident: bot checkouts on Shopify began on Sep 26 and
pushed the first abandoned-checkout email to about 84% bounces and the first
welcome email to about 65%. The ten weeks before were 0–3%.

    python3 examples/make_fixtures.py   # rewrites examples/data/*
"""
import csv
import datetime as dt
import json
import os
import random
import string

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
R = random.Random(20260926)

AS_OF = dt.date(2026, 10, 4)                 # a Sunday
FIRST_WEEK = dt.date(2026, 7, 13)            # 12 Monday-start weeks
ONSET = dt.date(2026, 9, 26)
DAYS = [FIRST_WEEK + dt.timedelta(days=i) for i in range(84)]
DAILY_FROM = AS_OF - dt.timedelta(days=20)   # 21 daily buckets

FLOWS = [
    {"id": "RtA9cK", "name": "Abandoned Checkout", "status": "live", "trigger_type": "Metric"},
    {"id": "Wl3mQx", "name": "Welcome Series", "status": "live", "trigger_type": "Added to List"},
    {"id": "Br7vNp", "name": "Browse Abandonment", "status": "live", "trigger_type": "Metric"},
    {"id": "Pp2hTz", "name": "Post-Purchase Thank You", "status": "live", "trigger_type": "Metric"},
    {"id": "Wb5kLs", "name": "Winback 120 Days", "status": "manual", "trigger_type": "Segment"},
]
MESSAGES = [
    {"id": "AC1msg", "flow_id": "RtA9cK", "name": "Abandoned Checkout: Email 1"},
    {"id": "AC2msg", "flow_id": "RtA9cK", "name": "Abandoned Checkout: Email 2"},
    {"id": "WL1msg", "flow_id": "Wl3mQx", "name": "Welcome: Email 1"},
    {"id": "WL2msg", "flow_id": "Wl3mQx", "name": "Welcome: Email 2"},
    {"id": "BR1msg", "flow_id": "Br7vNp", "name": "Browse Abandonment: Email 1"},
    {"id": "PP1msg", "flow_id": "Pp2hTz", "name": "Thank You"},
    {"id": "WB1msg", "flow_id": "Wb5kLs", "name": "Winback: We miss you"},
]

# Exact daily values for the abandoned-checkout and welcome email 1 around the
# onset, so the sample output carries round, checkable numbers.
AC1_CLEAN_SEP21_25 = [20, 21, 19, 22, 19]                          # 101 sends, 0 bounces
AC1_ATTACK = {ONSET: (285, 242), ONSET + dt.timedelta(days=1): (262, 223)}
AC1_LAST_WEEK = [(231, 195), (228, 192), (226, 190), (234, 197), (229, 193), (222, 186), (227, 191)]  # 1,597 / 1,344
WL1_LAST_WEEK = [(124, 81), (119, 77), (122, 79), (121, 79), (123, 80), (120, 78), (122, 78)]         #   851 /   552


def clean(base, lo, hi, rate_hi):
    n = base + R.randint(lo, hi)
    return n, sum(1 for _ in range(n) if R.random() < rate_hi * R.random())


def daily_values():
    v = {m["id"]: {} for m in MESSAGES}
    for d in DAYS:
        week_idx = (d - FIRST_WEEK).days // 7
        attack = d >= ONSET
        # Abandoned checkout email 1
        if dt.date(2026, 9, 21) <= d <= dt.date(2026, 9, 25):
            v["AC1msg"][d] = (AC1_CLEAN_SEP21_25[(d - dt.date(2026, 9, 21)).days], 0)
        elif d in AC1_ATTACK:
            v["AC1msg"][d] = AC1_ATTACK[d]
        elif d >= dt.date(2026, 9, 28):
            v["AC1msg"][d] = AC1_LAST_WEEK[(d - dt.date(2026, 9, 28)).days]
        else:
            v["AC1msg"][d] = clean(17, 0, 7, 0.06 if week_idx == 4 else 0.025)
        # Email 2: bots that bounced on email 1 are already suppressed
        n2 = 12 + R.randint(0, 6) + (14 if attack else 0)
        v["AC2msg"][d] = (n2, (R.randint(0, 2) if attack else (1 if R.random() < 0.05 else 0)))
        # Welcome email 1
        if d >= dt.date(2026, 9, 28):
            v["WL1msg"][d] = WL1_LAST_WEEK[(d - dt.date(2026, 9, 28)).days]
        elif attack:
            v["WL1msg"][d] = (131, 84) if d == ONSET else (118, 75)
        else:
            v["WL1msg"][d] = clean(34, 0, 10, 0.03)
        v["WL2msg"][d] = (30 + R.randint(0, 8), 1 if R.random() < 0.06 else 0)
        v["BR1msg"][d] = (40 + R.randint(0, 12), 1 if R.random() < 0.18 else 0)
        v["PP1msg"][d] = (33 + R.randint(0, 9), 1 if R.random() < 0.08 else 0)
        v["WB1msg"][d] = (1 if R.random() < 0.25 else 0, 0)
    return v


def series(values, dates, bucket):
    rows = []
    for m in MESSAGES:
        rec = [0] * len(dates)
        bnc = [0] * len(dates)
        for d, (r, b) in values[m["id"]].items():
            i = bucket(d)
            if i is not None:
                rec[i] += r
                bnc[i] += b
        rows.append({"flow_id": m["flow_id"], "flow_message_id": m["id"], "recipients": rec, "bounced": bnc})
    return rows


def bot_email():
    kind = R.random()
    letters = string.ascii_lowercase
    if kind < 0.35:
        local = "".join(R.choice(letters + string.digits) for _ in range(R.randint(11, 15)))
        return f"{local}@gmail.com"
    if kind < 0.55:
        base = "".join(R.choice(letters) for _ in range(R.randint(7, 9)))
        dotted = ".".join(base[i:i + 2] for i in range(0, len(base), 2))
        return f"{dotted}@gmail.com"
    if kind < 0.75:
        first = R.choice(["mike", "anna", "john", "lisa", "david", "emma", "chris", "sara"])
        dom = "".join(R.choice("bcdfghjklmnpqrstvwxz") for _ in range(6)) + R.choice([".com", ".net", ".shop"])
        return f"{first}{R.randint(10000, 99999)}@{dom}"
    first = R.choice(["james", "olivia", "noah", "ava", "liam", "mia", "lucas", "zoe"])
    last = R.choice(["smith", "brown", "lee", "walker", "young", "hall", "king", "wright"])
    return f"{first}.{last}{R.randint(1, 99)}@{R.choice(['yahoo.com', 'outlook.com', 'hotmail.com'])}"


def real_bounce_email():
    first = R.choice(["kate", "tom", "rachel", "ben", "nina", "omar", "priya", "sam"])
    last = R.choice(["harper", "nguyen", "ortiz", "patel", "reyes", "shaw", "turner", "vance"])
    return f"{first}.{last}@{R.choice(['gmial.com', 'oldcompany.co', 'hotmial.com', 'acme-retail.com'])}"


def bounces(values):
    events = []
    for d in sorted(DAYS, reverse=True):
        if d < DAILY_FROM:
            break
        for mid, flow_id in (("AC1msg", "RtA9cK"), ("WL1msg", "Wl3mQx"), ("BR1msg", "Br7vNp"), ("PP1msg", "Pp2hTz")):
            _, b = values[mid][d]
            for k in range(b):
                attack = d >= ONSET and mid in ("AC1msg", "WL1msg")
                sent = dt.datetime.combine(d, dt.time(23, 59)) - dt.timedelta(minutes=R.randint(0, 1400))
                if attack and R.random() < 0.97:
                    email = bot_email()
                    created = sent - dt.timedelta(minutes=R.randint(30, 70) if mid == "AC1msg" else R.randint(1, 5))
                    aos = False
                else:
                    email = real_bounce_email()
                    created = sent - dt.timedelta(days=R.randint(40, 900))
                    aos = True
                events.append({
                    "datetime": sent.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "email": email,
                    "profile_created": created.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "bounce_type": "hard" if (attack or R.random() < 0.6) else "soft",
                    "flow_id": flow_id,
                    "campaign": None,
                    "active_on_site": aos,
                })
    events.sort(key=lambda e: e["datetime"], reverse=True)
    events = events[:300]                     # the most recent N, as the fetcher returns them
    for i, e in enumerate(events):            # the fetcher samples Active on Site for the first 50 only
        if i >= 50:
            e["active_on_site"] = None
    return events


def main():
    os.makedirs(OUT, exist_ok=True)
    values = daily_values()
    weekly_dates = [(FIRST_WEEK + dt.timedelta(weeks=i)).strftime("%Y-%m-%dT00:00:00+00:00") for i in range(12)]
    daily_days = [DAILY_FROM + dt.timedelta(days=i) for i in range(21)]
    daily_dates = [d.strftime("%Y-%m-%dT00:00:00+00:00") for d in daily_days]
    bundle = {
        "account": "Fernhill Goods",
        "as_of": AS_OF.isoformat(),
        "source": "klaviyo",
        "generated_by": "examples/make_fixtures.py",
        "flows": FLOWS,
        "flow_messages": MESSAGES,
        "weekly": {"date_times": weekly_dates,
                   "rows": series(values, weekly_dates, lambda d: (d - FIRST_WEEK).days // 7)},
        "daily": {"date_times": daily_dates,
                  "rows": series(values, daily_dates,
                                 lambda d: (d - DAILY_FROM).days if d >= DAILY_FROM else None)},
        "bounces": bounces(values),
    }
    with open(os.path.join(OUT, "bundle.json"), "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=1)
        f.write("\n")
    with open(os.path.join(OUT, "shopify_daily.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "abandoned_checkouts", "orders"])
        for d in daily_days:
            attack = d >= ONSET
            w.writerow([d.isoformat(), (255 + R.randint(-20, 25)) if attack else (24 + R.randint(-4, 5)),
                        34 + R.randint(-5, 6)])
    print("wrote examples/data/bundle.json and examples/data/shopify_daily.csv")


if __name__ == "__main__":
    main()
