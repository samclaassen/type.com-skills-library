#!/usr/bin/env python3
"""
Regenerate the sample data in examples/data/.

The sample store, "Fernhill Goods", is fictional: the shared Shopify home and
kitchen brand used by every skill in the type.com BFCM collection (Gorgias
helpdesk, two support agents, ships from Columbus, Ohio). Its files carry what
a pre-holiday support review most often finds:

- tickets.csv: last season, Nov 15-Dec 31, 2025, about 1,150 Gorgias tickets.
  WISMO is the largest type and peaks after Black Friday (Nov 28, 2025) while
  the BFCM backlog ships. "Discount code not working" spikes over BFCM, mostly
  shoppers still using the VIPEARLY early-access code after it ended, and old
  codes like BF20. About 9% ask for a price adjustment.
  Returns jump after Christmas. A few tickets use wording the rules miss.
- policies.json: the 2026 holiday policies. Shipping dates match the Holiday
  Shipping Cutoff Checker sample (Dec 14 / Dec 15 / Dec 21 by carrier; customers
  pick a checkout rate, so Standard = Dec 14 and Express = Dec 21, for 95% of
  customers; backlog clears Dec 3). Weekend support only Nov 28-Dec 20. Holiday returns until Jan 31, 2027. One code per order, codes don't
  stack with the sale. There is NO price-adjustment policy.
- macros_existing.csv: "Holiday shipping" still says "Order by Dec 18, 2025"
  and promises delivery; the returns macro says 30 days with no holiday
  window; one macro pushes last year's code BF20 and another the old
  SUMMER15; free shipping is quoted at $50 (policy: $75); holiday hours are
  last year's.
- orders.csv: last season's orders by day (BFCM 2025 from the shared profile's
  daily orders, the same as the Budget Planner sample; Black Friday 2025: 321) and this
  season's projection for Nov 14-Dec 6, 2026, the kind of daily plan the BFCM
  Holiday Budget Planner gives. Dec 7-Dec 30 has no projection.

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

D = dt.date
LY_START, LY_END = D(2025, 11, 15), D(2025, 12, 31)
SHIFT = 364


def days(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def rand_int(lo, hi):
    """Uniform integer in [lo, hi]; R.random() only, so the output is the same on every Python 3."""
    return lo + int(R.random() * (hi - lo + 1))


def pick(seq):
    return seq[int(R.random() * len(seq))]


def pick_w(weights):
    x, acc = R.random() * sum(w for _, w in weights), 0.0
    for k, w in weights:
        acc += w
        if x < acc:
            return k
    return weights[-1][0]


def draw(lam):
    """Whole tickets with mean lam (randomized rounding, so the season's mix stays close to plan)."""
    whole = int(lam)
    return whole + (1 if R.random() < lam - whole else 0)


# ----------------------------------------------------------------------------- orders
# The same days as the BFCM Holiday Budget Planner sample's history.csv (total_orders), so the two samples agree.
BFCM_2025 = dict(zip(days(D(2025, 11, 24), D(2025, 12, 2)), [59, 66, 83, 139, 321, 198, 166, 215, 106]))
LATE_DEC = {D(2025, 12, 15): 44, D(2025, 12, 16): 40, D(2025, 12, 17): 36, D(2025, 12, 18): 33,
            D(2025, 12, 19): 30, D(2025, 12, 20): 27, D(2025, 12, 21): 25, D(2025, 12, 22): 19,
            D(2025, 12, 23): 15, D(2025, 12, 24): 11, D(2025, 12, 25): 8, D(2025, 12, 26): 31,
            D(2025, 12, 27): 35, D(2025, 12, 28): 30, D(2025, 12, 29): 27, D(2025, 12, 30): 25, D(2025, 12, 31): 22}


def last_year_orders():
    o = {}
    for d in days(D(2025, 10, 31), LY_END):
        if d in BFCM_2025:
            o[d] = BFCM_2025[d]
        elif d in LATE_DEC:
            o[d] = LATE_DEC[d] + rand_int(-3, 3)
        elif d < D(2025, 11, 24):
            o[d] = {5: 46, 6: 43}.get(d.weekday(), 40) + rand_int(-4, 4)
        else:  # Dec 3-14: gift season, easing off
            o[d] = int(round(80 - (d - D(2025, 12, 3)).days * 1.8)) + (5 if d.weekday() >= 5 else 0) + rand_int(-4, 4)
    return o


TY_FACTOR = {D(2026, 11, 24): 1.45, D(2026, 11, 25): 1.45, D(2026, 11, 26): 1.25, D(2026, 11, 27): 1.10,
             D(2026, 11, 28): 1.08, D(2026, 11, 29): 1.08, D(2026, 11, 30): 1.08, D(2026, 12, 1): 1.12}


def this_year_projection(ly):
    out = {}
    for d in days(D(2026, 11, 14), D(2026, 12, 6)):
        base = ly[d - dt.timedelta(days=SHIFT)]
        f = TY_FACTOR.get(d, 1.06 if d > D(2026, 12, 1) else 1.10)
        out[d] = int(round(base * f))
    return out


# ----------------------------------------------------------------------------- tickets
PRODUCTS = ["linen apron", "pour-over set", "canvas tote", "mug set", "merino throw", "walnut board"]
CITIES = ["Denver", "Austin", "Portland", "Chicago", "Atlanta", "Boston", "Phoenix", "Raleigh"]

TEXTS = {
    "wismo": [
        ("Where is my order?", "Ordered the {prod} on {od} and no tracking yet."),
        ("Order status {num}", "Can you tell me when this will ship?"),
        ("Has my order shipped?", "It still says unfulfilled on my account."),
        ("Tracking not updating", "Tracking for {num} hasn't moved in 4 days."),
        ("Still waiting on my package", "Ordered on Black Friday, still nothing."),
        ("When will my order arrive?", "No shipping email yet for {num}."),
        ("Package stuck", "USPS shows no movement since {d}."),
        ("{num} not shipped?", "Paid on {od} and it says not shipped."),
    ],
    "wismo_escalate": [
        ("Where is my order??", "Two weeks and no tracking. I will file a chargeback."),
        ("Order never came", "Tracking says delivered, nothing here. Opening a dispute with my bank."),
    ],
    "wismo_missed": [
        ("Haven't received my stuff", "Ordered {od}, nothing yet."),
        ("Any update?", "Just checking on {num}."),
    ],
    "shipping_deadline": [
        ("Will it arrive by Christmas?", "Ordering the {prod} today. Will it make it?"),
        ("Holiday shipping deadline", "What's the last day to order for Christmas?"),
        ("Christmas delivery", "If I order today with ground shipping, will it get here before Christmas?"),
        ("Need it by Dec 20", "Can the {prod} get to {city} by Dec 20?"),
        ("Gift for my mom", "Will the {prod} arrive in time for the 24th?"),
        ("Faster shipping?", "Do you have 2-day shipping so it arrives by the 23rd?"),
        ("Where's my order? Need it by Christmas", "Ordered {od}, will it arrive in time?"),
    ],
    "discount_code": [
        ("Discount code not working", "VIPEARLY says the code has expired."),
        ("Promo code error", "My code won't apply at checkout."),
        ("Early access code stopped working", "The VIPEARLY code from your text won't apply anymore."),
        ("Code says invalid", "BF20 shows invalid for me."),
        ("Can I use my code on sale items?", "Want to use WELCOME10 with the Black Friday sale."),
        ("Coupon not applied", "The coupon field rejects my code."),
        ("Cancel my order", "Please cancel it, my discount code didn't apply."),
    ],
    "price_adjustment": [
        ("Price adjustment", "Bought the {prod} on {pre} and it's 25% off now. Can you refund the difference?"),
        ("Bought last week, now on sale", "Can you honor the sale price on {num}?"),
        ("Price dropped after I ordered", "Paid full price on {pre}. Any chance of an adjustment?"),
        ("Refund the difference?", "My {prod} went on sale two days after I ordered."),
        ("Sale price for my order", "Ordered on {pre}, now cheaper. Can I get the difference back?"),
        ("Return or refund the difference?", "Bought the {prod} on {pre}, now on sale. Should I return it?"),
    ],
    "cancel_modify": [
        ("Cancel my order", "Please cancel {num}, I ordered by mistake."),
        ("Change my order", "Can I swap the mug set for the pour-over set?"),
        ("Add an item", "Can I add a second apron to {num}?"),
        ("Wrong color", "I picked the wrong color on the tote, can you fix it?"),
        ("Ordered twice", "I placed the same order twice, please cancel one."),
    ],
    "address_change": [
        ("Wrong shipping address", "I used my old address. Please update it to {city}."),
        ("Change address on {num}", "Need to change the shipping address."),
        ("Typo in my address", "Missing my apartment number on {num}."),
        ("Ship to my office instead?", "Can you change the address on my order?"),
        ("Change address and add a gift note", "Please update my address and add a gift note."),
    ],
    "gift_options": [
        ("Gift message", "Can I add a gift note to {num}?"),
        ("Gift wrap?", "Do you offer gift wrapping?"),
        ("Hide prices", "It's a present, please hide the price."),
        ("Gift receipt", "Can you include a gift receipt?"),
        ("Sending to my sister", "Can you include a message on the packing slip?"),
        ("Gift receipt so they can return it", "Can you include a gift receipt in case my dad wants to return it?"),
    ],
    "return_exchange": [
        ("Return request", "How do I return the {prod}?"),
        ("Exchange", "Can I exchange the throw for a different color?"),
        ("Got this as a gift", "I'd like to return it for store credit."),
        ("Refund status", "Sent my return back on {d}. When will I see the refund?"),
        ("Return label", "Need a return label for {num}."),
        ("Returning a mug set", "Didn't match my kitchen, want to send it back."),
    ],
    "damaged_defective": [
        ("Arrived broken", "One mug in the set was shattered."),
        ("Cracked pour-over", "The dripper arrived cracked."),
        ("Damaged in shipping", "Box was crushed and the board is split."),
        ("Tote strap ripped", "The strap ripped the first day."),
        ("Chipped mug, want a refund", "One mug is chipped. Can I get a refund?"),
    ],
    "damaged_escalate": [
        ("Broken glass", "The pour-over arrived broken and I cut myself on the glass."),
    ],
    "out_of_stock": [
        ("Linen apron sold out?", "Will the linen apron restock before Christmas?"),
        ("Back in stock?", "When will the merino throw be back in stock?"),
        ("Backorder", "Can I backorder the walnut board?"),
        ("Restock date", "Any restock date for the mug set?"),
        ("Notify me", "Signed up for notify me but no email yet."),
    ],
    "other": [
        ("Wholesale inquiry", "Do you sell to shops?"),
        ("Do you ship to Canada?", "Shipping to Toronto?"),
        ("Collab request", "I'm a creator and would love to work together."),
        ("Care instructions", "How do I oil the walnut board?"),
        ("Microwave safe?", "Are the mugs microwave safe?"),
        ("Can't log in", "My account login isn't working."),
        ("Unsubscribe", "Please stop the texts."),
        ("Corporate gifts", "Need 40 aprons for my team."),
        ("Black Friday deal?", "Can I still get the Black Friday deal?"),
    ],
}
TAGS = {"wismo": ("order-status", 0.35), "return_exchange": ("returns", 0.30), "damaged_defective": ("damaged", 0.25)}


def expected(o, d):
    """Expected tickets per type on last season's day d (Fernhill's support pattern)."""
    def lag(a, b):
        return sum(o[d - dt.timedelta(days=k)] for k in range(a, b + 1)) / (b - a + 1)

    def between(a, b):
        return a <= d <= b

    wk = {5: 0.8, 6: 0.85}.get(d.weekday(), 1.0)
    e = {}
    mult = 1.75 if between(D(2025, 12, 1), D(2025, 12, 5)) else \
        1.3 if between(D(2025, 11, 29), D(2025, 11, 30)) or between(D(2025, 12, 6), D(2025, 12, 9)) else \
        1.2 if between(D(2025, 12, 15), D(2025, 12, 23)) else 1.0
    e["wismo"] = 0.095 * lag(2, 7) * mult
    e["wismo_escalate"] = 0.35 if between(D(2025, 12, 1), D(2025, 12, 12)) else 0.0
    e["wismo_missed"] = 0.9 if between(D(2025, 12, 1), D(2025, 12, 10)) else 0.15
    e["shipping_deadline"] = (0.6 if d < D(2025, 12, 1) else 2.2 if d <= D(2025, 12, 6) else
                              5.5 if d <= D(2025, 12, 12) else 7.0 if d <= D(2025, 12, 20) else
                              3.8 if d <= D(2025, 12, 23) else 1.6 if d == D(2025, 12, 24) else 0.15)
    e["discount_code"] = 0.014 * o[d] + (0.058 * o[d] if between(D(2025, 11, 27), D(2025, 12, 1)) else 0.0)
    pre_sale = sum(o[x] for x in days(D(2025, 11, 13), D(2025, 11, 26)))
    pa_share = {D(2025, 11, 27): .13, D(2025, 11, 28): .19, D(2025, 11, 29): .14, D(2025, 11, 30): .10,
                D(2025, 12, 1): .14, D(2025, 12, 2): .11, D(2025, 12, 3): .07, D(2025, 12, 4): .05,
                D(2025, 12, 5): .04, D(2025, 12, 6): .03}
    e["price_adjustment"] = (0.16 * pre_sale * pa_share.get(d, 0.0) +
                             (0.25 if D(2025, 11, 27) <= d <= D(2025, 12, 15) else 0.0))
    e["cancel_modify"] = 0.024 * o[d]
    e["address_change"] = 0.014 * (o[d] + o[d - dt.timedelta(days=1)]) / 2
    post_xmas = {D(2025, 12, 26): 16, D(2025, 12, 27): 14, D(2025, 12, 28): 11, D(2025, 12, 29): 15,
                 D(2025, 12, 30): 13, D(2025, 12, 31): 11}
    e["return_exchange"] = 0.022 * lag(8, 15) + post_xmas.get(d, 0)
    e["damaged_defective"] = 0.019 * lag(3, 7)
    e["damaged_escalate"] = 0.06
    e["gift_options"] = (0.7 if d < D(2025, 11, 26) else 1.6 if d <= D(2025, 12, 20) else
                         0.8 if d <= D(2025, 12, 24) else 0.1)
    e["out_of_stock"] = (0.5 if d < D(2025, 11, 27) else 3.2 if d <= D(2025, 12, 2) else
                         0.9 if d <= D(2025, 12, 20) else 0.3)
    e["other"] = 0.7 + 0.004 * o[d]
    return {k: v * wk for k, v in e.items()}


MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fill(text, d):
    od = d - dt.timedelta(days=rand_int(3, 9))
    pre = D(2025, 11, rand_int(16, 24))
    seen = d - dt.timedelta(days=rand_int(2, 5))
    return text.format(prod=pick(PRODUCTS), od=f"{MON[od.month - 1]} {od.day}", pre=f"Nov {pre.day}",
                       d=f"{MON[seen.month - 1]} {seen.day}", num=f"#{rand_int(18240, 21980)}", city=pick(CITIES))


def allowed(kind, i, d):
    """Keep each ticket's wording consistent with its date."""
    after_bf, sale_on = d >= D(2025, 11, 29), d >= D(2025, 11, 27)
    rules = {("wismo", 4): after_bf, ("discount_code", 2): sale_on, ("discount_code", 4): sale_on,
             ("shipping_deadline", 3): d <= D(2025, 12, 18), ("shipping_deadline", 4): d <= D(2025, 12, 22),
             ("shipping_deadline", 5): d <= D(2025, 12, 20), ("out_of_stock", 0): d <= D(2025, 12, 20),
             ("other", 8): d <= D(2025, 12, 3)}
    return rules.get((kind, i), True)


def tickets(o):
    rows = []
    hours = [(h, w) for h, w in zip(range(7, 24), [2, 4, 6, 8, 8, 8, 7, 7, 7, 7, 6, 6, 7, 7, 6, 4, 2])]
    for d in days(LY_START, LY_END):
        lam = expected(o, d)
        for kind in sorted(lam):
            for _ in range(draw(lam[kind])):
                subj, body = pick([t for i, t in enumerate(TEXTS[kind]) if allowed(kind, i, d)])
                if kind == "discount_code" and D(2025, 11, 27) <= d <= D(2025, 12, 1) and R.random() < 0.7:
                    subj, body = TEXTS[kind][pick([0, 2, 4])]
                ts = dt.datetime(d.year, d.month, d.day, pick_w(hours), rand_int(0, 59), rand_int(0, 59))
                base = kind.replace("_escalate", "").replace("_missed", "")
                tags = []
                if kind in TAGS and R.random() < TAGS[kind][1]:
                    tags.append(TAGS[kind][0])
                if kind != "wismo_missed" and R.random() < 0.06:
                    tags.append(pick(["vip", "urgent", "bfcm"]))
                rows.append([ts, pick_w([("email", 52), ("chat", 30), ("contact_form", 10), ("instagram", 5),
                                         ("sms", 3)]), fill(subj, d), fill(body, d), ",".join(tags), base])
    rows.sort(key=lambda r: (r[0], r[2], r[3]))
    out, tid = [], 48217350
    for r in rows:
        tid += rand_int(1, 3)
        out.append([str(tid), r[0].strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"] + r[1:5])
    return out


# ----------------------------------------------------------------------------- policies and macros
POLICIES = {
    "store": "Fernhill Goods",
    "macros_live_by": "2026-11-20",
    "shipping": {
        "source": "Holiday Shipping Cutoff Checker, run Oct 6, 2026 (delivery by Dec 24 to 95% of customers)",
        "ships_from": "Columbus, Ohio",
        "same_day_cutoff": "1 pm ET",
        "ship_days": "Monday to Friday",
        "closed_dates": ["2026-11-26", "2026-12-25"],
        "deliver_by": "2026-12-24",
        "delivery_coverage_pct": 95,
        "services": [
            {"name": "USPS Ground Advantage", "last_order_date": "2026-12-14", "checkout_rate": "Standard"},
            {"name": "UPS Ground", "last_order_date": "2026-12-15", "checkout_rate": "Standard"},
            {"name": "UPS 2nd Day Air", "last_order_date": "2026-12-21", "checkout_rate": "Express"},
        ],
    },
    "bfcm_backlog": {"orders_from": "2026-11-26", "orders_to": "2026-11-30", "ships_within_business_days": 3,
                     "clears_by": "2026-12-03"},
    "returns": {
        "standard_days": 30,
        "counted_from": "delivery",
        "holiday_window": {"orders_from": "2026-11-01", "orders_to": "2026-12-24", "return_by": "2027-01-31"},
        "condition": "unused and in the original packaging",
        "start_url": "fernhillgoods.com/returns",
        "refund_to": "your original payment method",
        "return_label_fee": 0,
        "exchanges": True,
        "final_sale": ["Last Chance", "Gift Cards"],
        "gift_returns": "Received a gift from us? You can return it for store credit with the order number "
                        "from the packing slip, and we won't tell the sender.",
    },
    "price_adjustment": None,
    "discounts": {
        "one_code_per_order": True,
        "codes_combine_with_sale": False,
        "free_shipping_threshold": 75,
        "free_shipping_combines": True,
        "sale": {"name": "BFCM sale (25% off sitewide)", "starts": "2026-11-26", "ends": "2026-12-01",
                 "automatic": True, "excludes": ["Gift Cards", "New Arrivals", "Last Chance"]},
        "codes": [
            {"code": "VIPEARLY", "offer": "25% off sitewide for SMS and VIP subscribers",
             "starts": "2026-11-24", "ends": "2026-11-25"},
            {"code": "WELCOME10", "offer": "10% off a first order"},
            {"code": "MAYA20", "offer": "20% off, creator code, once per customer"},
        ],
    },
    "order_changes": {"window_minutes": 60,
                      "how": "Reply to your order confirmation email or message us with your order number."},
    "address_change": {"until": "your order ships", "after_shipping": "Once a package has shipped, we can't redirect it."},
    "gift_options": {"gift_message": True, "gift_receipt": True, "gift_wrap": False, "ship_to_another_address": True,
                     "e_gift_cards": "Our digital gift cards are emailed right away, or on a date you choose."},
    "out_of_stock": {"backorders": False, "restock_alerts": True},
    "damaged_defective": {"report_within_days": 14, "photo_required": True,
                          "resolution": "a free replacement, or a full refund if it's sold out",
                          "return_required": False},
    "international": {"ships_to": "the United States only, including APO/FPO addresses"},
    "support_hours": {"regular": "Monday to Friday, 9 am to 5 pm ET",
                      "holiday_note": "From November 28 to December 20 we also answer on weekends, 10 am to 2 pm ET.",
                      "closed_dates": ["2026-11-26", "2026-12-25", "2027-01-01"], "first_reply_hours": 24,
                      "weekend_coverage": {"from": "2026-11-28", "to": "2026-12-20"}},
    "escalation": {"order_value_over": 250, "refund_over": 150, "owner": "the support lead",
                   "keywords": ["chargeback", "dispute", "fraud", "lawyer", "attorney", "injured", "injury",
                                "cut myself", "allergic", "bbb"]},
}

EXISTING_MACROS = [
    ("Holiday shipping",
     "Hi {{ticket.customer.firstname}}, thanks for shopping with us! To get your gifts in time, order by Dec 18, "
     "2025 and they're guaranteed to arrive by Christmas. Orders placed by 1 pm ET ship the same business day.",
     "shipping,holiday"),
    ("Where is my order",
     "Hi {{ticket.customer.firstname}}, your order {{ticket.customer.integrations.shopify.orders[0].name}} is on "
     "its way! Track it here: {{ticket.customer.integrations.shopify.orders[0].fulfillments[0].tracking_url}}\n\n"
     "{{current_user.firstname}}",
     "order-status"),
    ("Returns & exchanges",
     "Hi {{ticket.customer.firstname}}, you can return or exchange unused items within 30 days of delivery. "
     "Start at fernhillgoods.com/returns and we'll email you a free return label.",
     "returns"),
    ("Black Friday code",
     "Use code BF20 at checkout for 20% off everything during our Black Friday sale!",
     "promo"),
    ("Discount code help",
     "Only one code works per order. If WELCOME10 isn't working, try SUMMER15 instead.",
     "promo"),
    ("Free shipping",
     "Free shipping on all orders over $50 in the US!",
     "shipping"),
    ("Holiday hours",
     "Our team is away from Dec 24, 2025 to Jan 1, 2026. We'll reply within 1 business day when we're back.",
     "hours"),
    ("Cancel order",
     "We can cancel or change your order within 1 hour of purchase. After that it's with our warehouse team.",
     "cancel"),
    ("Damaged item",
     "So sorry! Please send us a photo within 14 days of delivery and we'll send a free replacement.",
     "damaged"),
    ("Gift message",
     "You can add a gift message in the cart, and we leave prices off the packing slip.",
     "gift"),
]


def write_csv(name, header, rows):
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    ly = last_year_orders()
    ty = this_year_projection(ly)
    t = tickets(ly)
    write_csv("tickets.csv", ["id", "created_at", "channel", "subject", "body", "tags"], t)
    orders = [[d.isoformat(), ly[d], "shopify"] for d in days(LY_START, LY_END)]
    orders += [[d.isoformat(), n, "projection"] for d, n in sorted(ty.items())]
    write_csv("orders.csv", ["date", "orders", "source"], orders)
    write_csv("macros_existing.csv", ["name", "body", "tags"], EXISTING_MACROS)
    with open(os.path.join(OUT, "policies.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(POLICIES, f, indent=2)
        f.write("\n")
    print(f"tickets.csv: {len(t)} tickets; orders.csv: {len(orders)} days; "
          f"macros_existing.csv: {len(EXISTING_MACROS)} macros; policies.json")


if __name__ == "__main__":
    main()
