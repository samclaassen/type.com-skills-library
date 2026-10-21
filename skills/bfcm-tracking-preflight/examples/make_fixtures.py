#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional. Its exports carry the
problems a pre-BFCM audit most often finds on Shopify DTC brands:

- Meta deduplication broke on Sep 24 (a second server-side source with its
  own event_id), so Meta counts every purchase about twice.
- The Meta browser pixel sent nothing on Sep 15–16 after a theme publish.
- Google Ads conversion value includes tax and shipping (about 1.3x net sales).
- 31% of orders have no utm_source; Meta catalog ads run without URL
  parameters; utm_source is spelled five ways for Meta; TikTok ads copied a
  Meta URL template, so `{{campaign.name}}` arrives unfilled.
- Last BFCM, Meta claimed 1.6x what landed in Shopify with Meta UTMs, and the
  platforms together claimed 2.1x total Shopify net sales.

    python3 examples/make_fixtures.py   # rewrites examples/data/*
"""
import csv
import datetime as dt
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
R = random.Random(20261127)

AS_OF = dt.date(2026, 10, 5)
DAYS = [AS_OF - dt.timedelta(days=27 - i) for i in range(28)]   # Sep 8 .. Oct 5
PIXEL_DOWN = {dt.date(2026, 9, 15), dt.date(2026, 9, 16)}        # theme publish broke the browser pixel
DEDUP_BROKEN_FROM = dt.date(2026, 9, 24)                          # second CAPI source added
NO_UTM_SHARE = 0.31

PAGES = ["/", "/products/linen-apron", "/products/ceramic-pour-over-set", "/collections/kitchen",
         "/products/waxed-canvas-tote", "/pages/gift-guide", "/collections/new-arrivals"]


def pick(weights):
    x, acc = R.random() * sum(weights.values()), 0.0
    for k, w in weights.items():
        acc += w
        if x < acc:
            return k
    return k


def clid(n=22):
    return "".join(R.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") for _ in range(n))


def orders_and_events():
    per_day = {}
    for d in DAYS:
        base = 36 if d.weekday() in (5, 6) else 31
        per_day[d] = base + R.randint(-5, 6)
    orders = []
    num = 18240
    for d in DAYS:
        for _ in range(per_day[d]):
            num += 1
            orders.append({"order_id": f"#{num}", "date": d, "net": round(R.uniform(38, 140) * R.uniform(0.85, 1.15), 2)})

    # UTM assignment: exactly 31% of orders get no utm_source.
    idx = list(range(len(orders)))
    R.shuffle(idx)
    k = round(len(orders) * NO_UTM_SHARE)
    no_utm, tagged = idx[:k], idx[k:]
    for n, i in enumerate(no_utm):
        o = orders[i]
        if n < 64:        # Meta catalog / Advantage+ ads with no URL parameters
            o["path"] = R.choice(["/products/linen-apron", "/products/linen-apron", "/pages/gift-guide",
                                  "/products/waxed-canvas-tote"])
            o["query"] = f"fbclid=IwAR{clid()}"
            o["ref"] = R.choice(["https://l.facebook.com/", "https://l.instagram.com/", ""])
        elif n < 73:      # TikTok Spark Ads without parameters
            o["path"], o["query"], o["ref"] = "/products/ceramic-pour-over-set", f"ttclid={clid(30)}", ""
        elif n < 114:     # Google Ads auto-tagging only
            o["path"] = R.choice(["/", "/collections/kitchen", "/products/ceramic-pour-over-set"])
            o["query"], o["ref"] = f"gclid=Cj0K{clid(30)}", "https://www.google.com/"
        elif n < 160:     # organic referrals
            o["path"] = R.choice(PAGES)
            o["query"], o["ref"] = "", R.choice(["https://www.google.com/", "https://www.instagram.com/",
                                                  "https://www.pinterest.com/", "https://duckduckgo.com/"])
        else:             # direct
            o["path"], o["query"], o["ref"] = R.choice(["/", "/", "/collections/new-arrivals", "/pages/gift-guide",
                                                        "/products/linen-apron"]), "", ""
    tiktok_macro = 0
    for i in tagged:
        o = orders[i]
        ch = pick({"meta": 0.48, "google": 0.20, "klaviyo": 0.20, "tiktok": 0.08, "pinterest": 0.04})
        o["path"] = R.choice(PAGES)
        o["ref"] = ""
        if ch == "meta":
            src = pick({"facebook": 0.55, "fb": 0.15, "Facebook": 0.12, "ig": 0.10, "instagram": 0.08})
            camp = R.choice(["fall_prospecting", "retargeting_30d", "gift_guide_launch"])
            o["query"] = (f"utm_source={src}&utm_medium=paid_social&utm_campaign={camp}"
                          f"&utm_content=ad_{R.randint(1, 9)}&fbclid=IwAR{clid()}")
            o["ref"] = R.choice(["https://l.facebook.com/", "https://l.instagram.com/"])
        elif ch == "google":
            src = pick({"google": 0.85, "Google": 0.10, "adwords": 0.05})
            o["query"] = f"utm_source={src}&utm_medium=cpc&utm_campaign=search_brand&gclid=Cj0K{clid(30)}"
            o["ref"] = "https://www.google.com/"
        elif ch == "tiktok":
            if tiktok_macro < 12:
                tiktok_macro += 1
                camp = "%7B%7Bcampaign.name%7D%7D"
            else:
                camp = "fall_ugc"
            o["query"] = f"utm_source=tiktok&utm_medium=paid_social&utm_campaign={camp}&ttclid={clid(30)}"
        elif ch == "klaviyo":
            o["query"] = (f"utm_source=klaviyo&utm_medium=email&utm_campaign="
                          f"{R.choice(['welcome_series', 'abandoned_checkout', 'fall_newsletter'])}")
        else:
            o["query"] = "utm_source=pinterest&utm_medium=paid_social&utm_campaign=fall_pins"

    events = []
    for d in DAYS:
        day_orders = [o for o in orders if o["date"] == d]
        n = len(day_orders)
        net = round(sum(o["net"] for o in day_orders), 2)
        aov = net / n
        browser = 0 if d in PIXEL_DOWN else round(n * R.uniform(0.86, 0.93))
        server = round(n * R.uniform(0.93, 0.99))
        if d >= DEDUP_BROKEN_FROM:
            purchases = browser + server - R.randint(0, 2)
        elif browser == 0:
            purchases = server
        else:
            purchases = min(browser + server, max(browser, server) + round(n * R.uniform(0.01, 0.04)))
        g = round(n * R.uniform(0.22, 0.30))
        t = round(n * R.uniform(0.86, 0.94))
        events.append({
            "date": d.isoformat(), "shopify_orders": n, "shopify_net_sales": f"{net:.2f}",
            "meta_browser_events": browser, "meta_server_events": server, "meta_purchases": purchases,
            "meta_purchase_value": f"{purchases * aov * R.uniform(0.98, 1.03):.2f}",
            "google_conversions": g, "google_conversion_value": f"{g * aov * R.uniform(1.28, 1.34):.2f}",
            "tiktok_purchases": t, "tiktok_purchase_value": f"{t * aov * R.uniform(0.96, 1.02):.2f}",
        })
    return orders, events


def spread(total, weights, step=10):
    """Split total over days in proportion to weights, rounded to `step`, exact sum."""
    s = sum(weights)
    vals = [round(total * w / s / step) * step for w in weights]
    vals[-1] += total - sum(vals)
    return vals


def last_bfcm():
    days = [dt.date(2025, 11, 24) + dt.timedelta(days=i) for i in range(9)]     # Mon Nov 24 .. Tue Dec 2, 2025
    sales = [5280, 5760, 7440, 12480, 28320, 17760, 14640, 18960, 9360]          # $120,000
    lift = [0.85, 0.85, 0.9, 1.0, 1.25, 1.15, 1.0, 0.95, 0.9]                     # view-through peaks on BF weekend
    cols = {"date": [d.isoformat() for d in days], "shopify_net_sales": sales}
    plan = {  # platform: (claimed, spend, Shopify sales with that platform's UTMs)
        "meta": (108000, 16200, 67500),       # 1.60x Meta UTM sales, reported ROAS 6.67
        "google": (90000, 9720, 36000),
        "tiktok": (54000, 4080, 10800),       # together 252,000 = 2.10x actual; MER 120,000 / 30,000 = 4.00
    }
    for p, (claimed, spend, utm) in plan.items():
        cols[f"{p}_spend"] = spread(spend, [s * R.uniform(0.8, 1.2) for s in sales])
        cols[f"{p}_claimed_revenue"] = spread(claimed, [s * k * R.uniform(0.92, 1.08) for s, k in zip(sales, lift)])
        cols[f"{p}_utm_sales"] = spread(utm, [s * R.uniform(0.9, 1.1) for s in sales])
    header = ["date", "shopify_net_sales"] + [f"{p}_{c}" for p in plan for c in ("spend", "claimed_revenue", "utm_sales")]
    return header, [[cols[h][i] for h in header] for i in range(len(days))]


def main():
    os.makedirs(OUT, exist_ok=True)
    orders, events = orders_and_events()
    with open(os.path.join(OUT, "events_daily.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(events[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(events)
    with open(os.path.join(OUT, "orders_utm.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["order_id", "date", "landing_site", "referring_site"])
        for o in orders:
            w.writerow([o["order_id"], o["date"].isoformat(),
                        o["path"] + ("?" + o["query"] if o["query"] else ""), o["ref"]])
    header, rows = last_bfcm()
    with open(os.path.join(OUT, "last_bfcm.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    print("wrote examples/data/events_daily.csv, orders_utm.csv and last_bfcm.csv")


if __name__ == "__main__":
    main()
