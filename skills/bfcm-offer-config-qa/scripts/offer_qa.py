#!/usr/bin/env python3
"""
BFCM Offer & Discount Config QA: analyzer.

Reads the offer plan, a normalized export of the store's live Shopify
discounts, the product catalog and (optionally) price history, and prints a
Markdown report: planned offers vs live discounts, sale windows in store time,
who gets each discount, how discounts stack and the worst-case price per SKU,
code limits and free-shipping minimums, compare-at reference prices, a
test-checkout script and a dated fix list.

Local files only. Never calls an API, never changes a discount.

    python3 scripts/offer_qa.py --plan offer_plan.json --discounts discounts.json \
        --products products.csv [--price-history price_history.csv] \
        [--as-of YYYY-MM-DD] [--margin-floor PCT] [--json analysis.json]

Python 3.8+, standard library only. Output is deterministic for a given input.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import json
import math
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

UTC = dt.timezone.utc
CENT, ZERO, HUNDRED = Decimal("0.01"), Decimal("0"), Decimal("100")
EARLIEST = dt.datetime(2000, 1, 1, tzinfo=UTC)     # start of an "always on" plan offer
LATEST = dt.datetime(2100, 1, 1, tzinfo=UTC)       # "no end date"
TOL = Decimal("0.0005")                            # depth tolerance against the plan max

GAP_TOLERANCE = dt.timedelta(minutes=1)   # phase gaps/overlaps up to a minute are ignored
FIX_LEAD, RECHECK_LEAD, FREEZE_LEAD, FINAL_LEAD = 14, 7, 3, 1   # days before launch
LOOKBACK_DAYS = 90        # reference-price lookback before a compare-at raise
REFERENCE_MIN_DAYS = 30   # days the new compare-at must have been the selling price
MAX_CODES = 5             # Shopify: up to 5 product/order codes and 1 shipping code per order
NEAR_LIMIT = Decimal("0.8")
SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2, "PLAN": 3}
SECTIONS = ("eligibility", "stacking", "codes", "plan", "windows", "reference")
CLASSES = ("product", "order", "shipping")
COMBINE_KEYS = {"product": "productDiscounts", "order": "orderDiscounts", "shipping": "shippingDiscounts"}


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- time zones
# Current POSIX rules (the footer of each IANA tzdata file), built in so that Python 3.8,
# which has no zoneinfo, gives the same answers as newer versions.
_US = {k: f"{k}ST{n}{k}DT,M3.2.0,M11.1.0" for k, n in (("E", 5), ("C", 6), ("M", 7), ("P", 8))}
_CET, _EET, _AU = "CET-1CEST,M3.5.0,M10.5.0/3", "EET-2EEST,M3.5.0/3,M10.5.0/4", "AEST-10AEDT,M10.1.0,M4.1.0/3"
TZ_RULES = {
    "America/New_York": _US["E"], "America/Detroit": _US["E"], "America/Toronto": _US["E"],
    "America/Indiana/Indianapolis": _US["E"], "US/Eastern": _US["E"], "America/Chicago": _US["C"],
    "America/Winnipeg": _US["C"], "US/Central": _US["C"], "America/Denver": _US["M"], "America/Edmonton": _US["M"],
    "America/Boise": _US["M"], "US/Mountain": _US["M"], "America/Phoenix": "MST7", "America/Los_Angeles": _US["P"],
    "America/Vancouver": _US["P"], "US/Pacific": _US["P"], "America/Anchorage": "AKST9AKDT,M3.2.0,M11.1.0",
    "Pacific/Honolulu": "HST10", "America/Halifax": "AST4ADT,M3.2.0,M11.1.0",
    "America/St_Johns": "NST3:30NDT,M3.2.0,M11.1.0", "America/Mexico_City": "CST6", "America/Sao_Paulo": "<-03>3",
    "Europe/London": "GMT0BST,M3.5.0/1,M10.5.0", "Europe/Dublin": "GMT0IST,M3.5.0/1,M10.5.0",
    "Europe/Lisbon": "WET0WEST,M3.5.0/1,M10.5.0", "Europe/Helsinki": _EET, "Europe/Athens": _EET,
    "Australia/Sydney": _AU, "Australia/Melbourne": _AU, "Australia/Brisbane": "AEST-10",
    "Australia/Adelaide": "ACST-9:30ACDT,M10.1.0,M4.1.0/3", "Australia/Perth": "AWST-8",
    "Pacific/Auckland": "NZST-12NZDT,M9.5.0,M4.1.0/3", "Asia/Singapore": "<+08>-8", "Asia/Hong_Kong": "HKT-8",
    "Asia/Tokyo": "JST-9", "Asia/Kolkata": "IST-5:30", "Asia/Dubai": "<+04>-4", "UTC": "UTC0", "Etc/UTC": "UTC0",
}
for _z in ("Paris", "Berlin", "Amsterdam", "Brussels", "Madrid", "Rome", "Stockholm", "Copenhagen", "Oslo", "Vienna",
           "Zurich", "Prague", "Warsaw"):
    TZ_RULES["Europe/" + _z] = _CET
TZ_LABELS = {"E": "ET", "C": "CT", "M": "MT", "P": "PT"}
TZ_LABELS = {z: TZ_LABELS[r[0]] for z, r in TZ_RULES.items() if r in _US.values()}
TZ_LABELS.update({"America/Phoenix": "MST", "America/Anchorage": "AKT", "Pacific/Honolulu": "HT",
                  "America/Halifax": "AT", "America/St_Johns": "NT", "UTC": "UTC", "Etc/UTC": "UTC"})
# Zones tried when a live time doesn't match the plan, to name the slip.
SLIP_ZONES = (("UTC", "UTC"), ("America/Los_Angeles", "Pacific time"), ("America/Chicago", "Central time"),
              ("America/Denver", "Mountain time"), ("America/New_York", "Eastern time"), ("Europe/London", "UK time"))
_TZ_NAME = re.compile(r"<[^>]+>|[A-Za-z]{3,}")
_TZ_OFF = re.compile(r"([+-]?)(\d{1,3})(?::(\d{1,2}))?(?::(\d{1,2}))?")
_TZ_RULE = re.compile(r"M(\d{1,2})\.(\d)\.(\d)(?:/([+-]?)(\d{1,3})(?::(\d{1,2}))?(?::(\d{1,2}))?)?$")


def _nth_weekday(year: int, month: int, week: int, posix_wday: int) -> dt.date:
    first = dt.date(year, month, 1)
    day = 1 + ((posix_wday - 1) % 7 - first.weekday()) % 7 + (week - 1) * 7
    last = ((dt.date(year + (month == 12), month % 12 + 1, 1)) - dt.timedelta(days=1)).day
    while day > last:
        day -= 7
    return dt.date(year, month, day)


class PosixTZ:
    """A time zone from a POSIX TZ rule such as EST5EDT,M3.2.0,M11.1.0 (offsets west of UTC)."""

    def __init__(self, spec: str, name: str, label: str):
        self.spec, self.name, self.label = spec, name, label
        self.dst, self.rules = None, None

        def offset_at(i):
            m = _TZ_OFF.match(spec, i)
            if not m or m.end() == i:
                raise ValueError(spec)
            s = int(m.group(2)) * 3600 + int(m.group(3) or 0) * 60 + int(m.group(4) or 0)
            return (s if m.group(1) == "-" else -s), m.end()     # seconds east of UTC

        m = _TZ_NAME.match(spec)
        if not m:
            raise ValueError(spec)
        self.std, i = offset_at(m.end())
        if i == len(spec):
            return
        m = _TZ_NAME.match(spec, i)
        if not m:
            raise ValueError(spec)
        i = m.end()
        if i < len(spec) and spec[i] != ",":
            self.dst, i = offset_at(i)
        else:
            self.dst = self.std + 3600
        parts = spec[i:].split(",")
        if len(parts) != 3 or parts[0]:
            raise ValueError(spec)
        self.rules = []
        for r in parts[1:]:
            rm = _TZ_RULE.match(r)
            if not rm:
                raise ValueError(spec)
            t = 7200 if rm.group(5) is None else (int(rm.group(5)) * 3600 + int(rm.group(6) or 0) * 60
                                                  + int(rm.group(7) or 0)) * (-1 if rm.group(4) == "-" else 1)
            self.rules.append((int(rm.group(1)), int(rm.group(2)), int(rm.group(3)), t))

    def offset(self, utc_naive: dt.datetime) -> int:
        if self.dst is None:
            return self.std
        (m1, w1, d1, t1), (m2, w2, d2, t2) = self.rules
        y = utc_naive.year
        on = dt.datetime.combine(_nth_weekday(y, m1, w1, d1), dt.time()) + dt.timedelta(seconds=t1 - self.std)
        off = dt.datetime.combine(_nth_weekday(y, m2, w2, d2), dt.time()) + dt.timedelta(seconds=t2 - self.dst)
        dst = on <= utc_naive < off if on < off else not (off <= utc_naive < on)
        return self.dst if dst else self.std

    def to_local(self, t: dt.datetime) -> dt.datetime:
        u = t.astimezone(UTC).replace(tzinfo=None)
        return u + dt.timedelta(seconds=self.offset(u))

    def to_utc(self, local: dt.datetime) -> dt.datetime:
        """Store wall-clock time to UTC; an ambiguous time takes the first occurrence."""
        cands = [local - dt.timedelta(seconds=o) for o in {self.std, self.dst} - {None}
                 if self.offset(local - dt.timedelta(seconds=o)) == o]
        return (min(cands) if cands else local - dt.timedelta(seconds=self.std)).replace(tzinfo=UTC)


def load_tz(name: str, label: str | None) -> PosixTZ:
    spec = TZ_RULES.get(name)
    if spec is None and re.match(r"^(<[^>]+>|[A-Za-z]{3,})[+-]?\d", name):
        spec = name
    if spec is None:
        raise InputError(f"offer plan: unknown timezone '{name}'. Use an IANA name such as America/New_York, "
                         f"America/Chicago, America/Los_Angeles or Europe/London, or a POSIX TZ rule such as "
                         f"'EST5EDT,M3.2.0,M11.1.0'")
    try:
        return PosixTZ(spec, name, label or TZ_LABELS.get(name, "store time"))
    except ValueError:
        raise InputError(f"offer plan: cannot parse timezone rule '{spec}'")


# --------------------------------------------------------------------------- formatting
WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WORDS = ("None", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")
MO = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_day(d: dt.date) -> str:
    return f"{MO[d.month - 1]} {d.day}"


def fmt_wday(d: dt.date, year: int | None = None) -> str:
    return f"{WD[d.weekday()]} {fmt_day(d)}" + (f", {d.year}" if year is not None and d.year != year else "")


def fmt_clock(t: dt.datetime) -> str:
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def fmt_local(t: dt.datetime, year: int | None = None) -> str:
    return f"{fmt_wday(t.date(), year)} {fmt_clock(t)}"


def fmt_span(a: dt.date, b: dt.date) -> str:
    return fmt_day(a) if a == b else f"{fmt_day(a)} – {fmt_day(b)}"


def fmt_dur(td: dt.timedelta, adjective: bool = False) -> str:
    mins = int(round(td.total_seconds() / 60))
    d, rem = divmod(mins, 1440)
    h, m = divmod(rem, 60)
    if adjective and not d and (not h or not m):
        return f"{h}-hour" if h else f"{m}-minute"
    parts = ([f"{d} day" + ("s" if d != 1 else "")] if d else []) + ([f"{h} h"] if h else [])
    return " ".join(parts + ([f"{m} min"] if m or not parts else []))


def money(x: Decimal) -> str:
    return ("−" if x < 0 else "") + f"${abs(x):,.2f}"


def money_s(x: Decimal) -> str:
    """Money in prose: whole amounts without cents."""
    return f"${x:,.0f}" if x == x.to_integral_value() else money(x)


def money0(x: Decimal) -> str:
    return f"${x.quantize(Decimal('1'), ROUND_HALF_UP):,}"


def num(x: Decimal) -> str:
    return f"{x.normalize():f}"


def pct(frac: Decimal, keep_one: bool = False, digits: str = "0.1") -> str:
    v = (frac * HUNDRED).quantize(Decimal(digits), ROUND_HALF_UP)
    s = f"{abs(v):f}"
    if not keep_one and "." in s:
        s = s.rstrip("0").rstrip(".")
    return ("−" if v < 0 else "") + s + "%"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} " + (word if n == 1 else (many or word + "s"))


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


def join_and(items) -> str:
    items = list(items)
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").casefold()).strip("-")


# --------------------------------------------------------------------------- loading
def dec(v, where: str, allow_none: bool = False, minimum: Decimal | None = ZERO) -> Decimal | None:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if allow_none:
            return None
        raise InputError(f"{where} is missing")
    try:
        if isinstance(v, bool):
            raise InvalidOperation
        x = Decimal(str(v).replace(",", "").replace("$", "").strip())
        if not x.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise InputError(f"{where} must be a number, got {v!r}")
    if minimum is not None and x < minimum:
        raise InputError(f"{where} is negative ({v})")
    return x


def whole(v, where: str) -> int | None:
    x = dec(v, where, allow_none=True)
    if x is not None and x != x.to_integral_value():
        raise InputError(f"{where} must be a whole number, got {v!r}")
    return None if x is None else int(x)


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def parse_time(s, where: str, tz: PosixTZ | None, end_of_day: bool = False) -> dt.datetime:
    """ISO date-time. With an offset it is absolute; without one it is store time (needs tz)."""
    m = _ISO.match(s.strip()) if isinstance(s, str) else None
    if not m:
        raise InputError(f"{where}: {s!r} is not a date-time like 2026-11-26T05:00:00Z or 2026-11-26 00:00")
    try:
        hm = (int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)) if m.group(4) else \
            ((23, 59, 0) if end_of_day else (0, 0, 0))
        t = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), *hm)
    except ValueError:
        raise InputError(f"{where}: {s!r} is not a valid date-time")
    off = m.group(7)
    if off is None:
        if tz is None:
            raise InputError(f"{where}: {s!r} has no UTC offset. Use the time as Shopify returns it, "
                             f"e.g. 2026-11-26T05:00:00Z")
        return tz.to_utc(t)
    digits = off[1:].replace(":", "") if off != "Z" else "0000"
    secs = (int(digits[:2]) * 3600 + int(digits[2:]) * 60) * (-1 if off[0] == "-" else 1)
    return (t - dt.timedelta(seconds=secs)).replace(tzinfo=UTC)


def parse_date(s: str, where: str) -> dt.date:
    try:
        return dt.date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        raise InputError(f"{where}: date must be YYYY-MM-DD, got {s!r}")


def read_json(path: str, label: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e.strerror or e}")
    except json.JSONDecodeError as e:
        raise InputError(f"{label} {path} is not valid JSON: {e.msg} at line {e.lineno}")


def read_csv(path: str, label: str) -> tuple:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            cols = [(c or "").strip() for c in (rd.fieldnames or [])]
            rows = [(i, {(k or "").strip(): (v if isinstance(v, str) else "").strip() for k, v in r.items()
                         if k is not None}) for i, r in enumerate(rd, start=2)]
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e.strerror or e}")
    except csv.Error as e:
        raise InputError(f"{label} {path} is not a valid CSV: {e}")
    if not cols:
        raise InputError(f"{label} {path} is empty")
    return cols, rows


def load_products(path: str) -> list:
    cols, rows = read_csv(path, "products file")
    for c in ("handle", "sku", "price"):
        if c not in cols:
            raise InputError(f"products file is missing required column '{c}'")
    out, seen = [], set()
    for line, r in rows:
        w = f"{path} line {line}"
        sku, handle = r.get("sku", ""), r.get("handle", "")
        if not sku or not handle:
            raise InputError(f"{w}: '{'sku' if not sku else 'handle'}' is blank (every variant needs a unique SKU)")
        if sku in seen:
            raise InputError(f"{w}: duplicate sku {sku!r}")
        seen.add(sku)
        price = dec(r.get("price"), f"{w}: 'price'")
        if price == 0:
            raise InputError(f"{w}: 'price' is 0")
        compare = dec(r.get("compare_at_price"), f"{w}: 'compare_at_price'", allow_none=True)
        colls = [c.strip() for c in r.get("collections", "").split(";") if c.strip()]
        title, vt = r.get("title") or handle, r.get("variant_title", "")
        if r.get("gift_card", "") != "":
            gift = r["gift_card"].casefold() in ("1", "true", "yes", "y")
        else:
            gift = handle.startswith("gift-card") or any(norm_key(c) in ("gift-card", "gift-cards") for c in colls)
        out.append({"sku": sku, "handle": handle, "title": title,
                    "name": title + (f" — {vt}" if vt and vt.casefold() != "default title" else ""),
                    "price": price, "compare_at": compare or None, "collections": colls,
                    "cost": dec(r.get("unit_cost"), f"{w}: 'unit_cost'", allow_none=True),
                    "coll_keys": {norm_key(c) for c in colls}, "gift_card": gift,
                    "inventory": whole(r.get("inventory"), f"{w}: 'inventory'"),
                    "sold90": whole(r.get("units_sold_90d"), f"{w}: 'units_sold_90d'")})
    if not out:
        raise InputError("products file has no rows")
    return out


def load_history(path: str | None, catalog: list) -> dict | None:
    if not path:
        return None
    cols, rows = read_csv(path, "price history file")
    for c in ("sku", "date", "price"):
        if c not in cols:
            raise InputError(f"price history file is missing required column '{c}'")
    known = {p["sku"] for p in catalog}
    hist, ignored = {}, 0
    for line, r in rows:
        w = f"{path} line {line}"
        d = parse_date(r.get("date", ""), w)
        price = dec(r.get("price"), f"{w}: 'price'")
        compare = dec(r.get("compare_at_price"), f"{w}: 'compare_at_price'", allow_none=True) or ZERO
        sku = r.get("sku", "")
        if sku not in known:
            ignored += 1
            continue
        if any(e[0] == d for e in hist.get(sku, [])):
            raise InputError(f"{w}: duplicate row for {sku} on {d}")
        hist.setdefault(sku, []).append((d, price, compare))
    if not hist:
        raise InputError("price history file has no rows for SKUs in the products file")
    for v in hist.values():
        v.sort()
    return {"rows": hist, "ignored": ignored, "from": min(v[0][0] for v in hist.values())}


def norm_target(v, where: str) -> dict:
    if v is None:
        return {"type": "all"}
    if not isinstance(v, dict) or v.get("type") not in ("all", "collections", "products", "unknown"):
        raise InputError(f"{where}: 'target.type' must be all, collections, products or unknown")
    trunc = {"truncated": True} if v.get("truncated") else {}
    if v["type"] == "unknown":
        return {"type": "unknown"}
    if v["type"] == "collections":
        return dict({"type": "collections", "collections": [str(c) for c in v.get("collections") or []]}, **trunc)
    if v["type"] == "products":
        return dict({"type": "products", "products": [str(p) for p in v.get("products") or []],
                     "variants": [str(s) for s in v.get("variants") or []]}, **trunc)
    return {"type": "all"}


def load_discounts(path: str, catalog: list) -> tuple:
    raw = read_json(path, "discounts file")
    if not isinstance(raw, dict) or not isinstance(raw.get("discounts"), list) or not raw["discounts"]:
        raise InputError("discounts file must be an object with a non-empty 'discounts' list "
                         "(run scripts/normalize_shopify_discounts.py on the GraphQL response)")
    exported = parse_time(raw["exportedAt"], "discounts file exportedAt", None) if raw.get("exportedAt") else None
    out = []
    for i, r in enumerate(raw["discounts"]):
        if not isinstance(r, dict):
            raise InputError(f"discounts[{i}] must be an object")
        codes = [str(c) for c in r.get("codes") or []]
        w = f"discounts[{i}] ({(codes or [r.get('title') or '?'])[0]})"
        method, typ, cls = r.get("method"), r.get("type"), r.get("class")
        if method not in ("code", "automatic"):
            raise InputError(f"{w}: 'method' must be code or automatic")
        if method == "code" and not codes:
            raise InputError(f"{w}: a code discount needs at least one entry in 'codes'")
        if typ not in ("percentage", "fixed_amount", "free_shipping", "bxgy", "app", "unknown"):
            raise InputError(f"{w}: 'type' must be percentage, fixed_amount, free_shipping, bxgy, app or unknown")
        if cls not in CLASSES + ("multi",):
            raise InputError(f"{w}: 'class' must be product, order, shipping or multi")
        value, bx = None, None
        if typ in ("percentage", "fixed_amount"):
            value = dec(r.get("value"), f"{w}: 'value'")
            if typ == "percentage" and value > HUNDRED:
                raise InputError(f"{w}: percentage 'value' {value} is over 100")
            if typ == "percentage" and ZERO < value <= 1 and raw.get("source") != "Shopify Admin GraphQL discountNodes":
                print(f"warning: {w}: percentage 'value' {num(value)} is read as {num(value)}%. This file uses percent "
                      f"(25 = 25%); if you copied Shopify's fraction (0.25), multiply by 100.", file=sys.stderr)
        if typ == "bxgy":
            b = r.get("bxgy") or {}
            gets, buys = b.get("gets") or {}, b.get("buys") or {}
            bx = {"buy_qty": whole(buys.get("quantity"), f"{w}: bxgy.buys.quantity") or 0,
                  "buy_amt": dec(buys.get("amount"), f"{w}: bxgy.buys.amount", allow_none=True),
                  "get_qty": whole(gets.get("quantity"), f"{w}: bxgy.gets.quantity") or 1,
                  "get_pct": dec(gets.get("percentage"), f"{w}: bxgy.gets.percentage", allow_none=True),
                  "get_amt": dec(gets.get("amount"), f"{w}: bxgy.gets.amount", allow_none=True)}
            value = bx["get_pct"]
        mn = r.get("minimum")
        if mn is not None and not isinstance(mn, dict):
            raise InputError(f"{w}: 'minimum' must be null, {{\"subtotal\": n}} or {{\"quantity\": n}}")
        cw = r.get("combinesWith")
        if not isinstance(cw, dict) or any(not isinstance(cw.get(k, False), bool) for k in COMBINE_KEYS.values()):
            raise InputError(f"{w}: 'combinesWith' must be an object of productDiscounts/orderDiscounts/"
                             f"shippingDiscounts true or false")
        if not r.get("startsAt"):
            raise InputError(f"{w}: 'startsAt' is missing")
        starts = parse_time(r["startsAt"], f"{w}: startsAt", None)
        ends = parse_time(r["endsAt"], f"{w}: endsAt", None) if r.get("endsAt") else None
        if ends is not None and ends <= starts:
            raise InputError(f"{w}: endsAt is not after startsAt")
        status = str(r.get("status") or "").upper()
        if status not in ("ACTIVE", "SCHEDULED", "EXPIRED"):
            raise InputError(f"{w}: 'status' must be ACTIVE, SCHEDULED or EXPIRED")
        el = r.get("eligibility") or {"type": "all"}
        if not isinstance(el, dict) or el.get("type") not in ("all", "segments", "customers", "markets", "unknown"):
            raise InputError(f"{w}: 'eligibility.type' must be all, segments, customers, markets or unknown")
        n_codes = whole(r.get("codesCount"), f"{w}: codesCount") or len(codes)
        if method == "code":
            label = codes[0] if n_codes == 1 else f"{r.get('title') or codes[0]} [{n_codes:,} codes]"
        else:
            label = str(r.get("title") or f"automatic discount {i + 1}")
        d = {"key": f"live:{i}", "label": label, "title": str(r.get("title") or label), "method": method,
             "codes": codes, "cls": cls, "type": typ, "value": value, "bxgy": bx,
             "each_item": r.get("appliesOnEachItem", True) is not False,
             "target": norm_target(r.get("target"), w),
             "min_subtotal": dec((mn or {}).get("subtotal"), f"{w}: minimum.subtotal", allow_none=True),
             "min_qty": whole((mn or {}).get("quantity"), f"{w}: minimum.quantity"),
             "elig": {"type": el["type"], "names": sorted(str(n) for n in el.get("names") or [])},
             "usage_limit": whole(r.get("usageLimit"), f"{w}: usageLimit"), "once": bool(r.get("oncePerCustomer")),
             "used": whole(r.get("timesUsed"), f"{w}: timesUsed") or 0,
             "combines": {c: bool(cw.get(k, False)) for c, k in COMBINE_KEYS.items()},
             "same_line_tags": [str(t) for t in cw.get("sameLineTags") or []],
             "tags": [str(t) for t in r.get("tags") or []], "starts": starts, "ends": ends, "status": status,
             "updated": parse_time(r["updatedAt"], f"{w}: updatedAt", None) if r.get("updatedAt") else None,
             "modeled": typ in ("percentage", "fixed_amount", "free_shipping") or (typ == "bxgy" and value is not None)}
        d["modeled"] = d["modeled"] and cls != "multi"
        d["skus"] = resolve_skus(d, catalog)
        out.append(d)
    return out, exported


def resolve_target(target: dict, catalog: list) -> set:
    """SKUs a product discount reaches. Gift cards only when named (Shopify rule)."""
    t = target["type"]
    if t == "unknown":
        return set()
    if t == "all":
        return {p["sku"] for p in catalog if not p["gift_card"]}
    if t == "collections":
        keys = {norm_key(c) for c in target["collections"]}
        return {p["sku"] for p in catalog if not p["gift_card"] and p["coll_keys"] & keys}
    hs, vs = set(target.get("products", [])), set(target.get("variants", []))
    return {p["sku"] for p in catalog if p["handle"] in hs or p["sku"] in vs}


def resolve_skus(d: dict, catalog: list) -> set:
    if d["cls"] == "order":
        return {p["sku"] for p in catalog if not p["gift_card"]}
    return resolve_target(d["target"], catalog) if d["cls"] == "product" else set()


def load_plan(path: str, catalog: list) -> dict:
    raw = read_json(path, "offer plan")
    if not isinstance(raw, dict):
        raise InputError("offer plan must be a JSON object")
    if not raw.get("timezone"):
        raise InputError("offer plan: 'timezone' is required (the store time zone, e.g. America/New_York)")
    tz = load_tz(str(raw["timezone"]), raw.get("timezone_label"))
    mx = dec(raw.get("max_item_discount_pct"), "offer plan: 'max_item_discount_pct'")
    if not ZERO < mx <= HUNDRED:
        raise InputError("offer plan: 'max_item_discount_pct' must be between 0 and 100")
    floor = dec(raw.get("margin_floor_pct", 0), "offer plan: 'margin_floor_pct'", minimum=None)
    if floor >= HUNDRED:
        raise InputError("offer plan: 'margin_floor_pct' must be under 100")
    econ = raw.get("economics") or {}
    if not isinstance(econ, dict):
        raise InputError("offer plan: 'economics' must be an object")
    e = "offer plan: economics."
    economics = {"ship_cost": dec(econ.get("shipping_cost_per_order"), e + "shipping_cost_per_order", True),
                 "last_orders": whole(econ.get("last_bfcm_orders"), e + "last_bfcm_orders"),
                 "last_under": whole(econ.get("last_bfcm_orders_under_free_shipping_min"),
                                     e + "last_bfcm_orders_under_free_shipping_min"),
                 "per_day": dec(econ.get("orders_per_day_now"), e + "orders_per_day_now", True)}
    if (economics["last_under"] or 0) > (economics["last_orders"] or 0) and economics["last_orders"] is not None:
        raise InputError("offer plan: economics.last_bfcm_orders_under_free_shipping_min is more than last_bfcm_orders")
    if not isinstance(raw.get("offers"), list) or not raw["offers"]:
        raise InputError("offer plan: 'offers' must be a non-empty list")
    offers = [parse_offer(o, i, tz, catalog) for i, o in enumerate(raw["offers"])]
    keys = [o["match"] for o in offers]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise InputError(f"offer plan: two offers use the same code or title: {dup[0][1]}")
    mnba = raw.get("must_not_be_active") or []
    if not isinstance(mnba, list) or not all(isinstance(x, str) and x.strip() for x in mnba):
        raise InputError("offer plan: 'must_not_be_active' must be a list of codes or titles")
    return {"store": str(raw.get("store") or "Store"), "tz": tz, "max": mx / HUNDRED, "floor_pct": floor,
            "launch": parse_date(raw["launch"], "offer plan: 'launch'") if raw.get("launch") else None,
            "economics": economics, "offers": offers, "must_not": [x.strip() for x in mnba],
            "plus": bool(raw.get("shopify_plus", False))}


def parse_offer(o, i: int, tz: PosixTZ, catalog: list) -> dict:
    if not isinstance(o, dict):
        raise InputError(f"offer plan: offers[{i}] must be an object")
    method, typ, cls = o.get("method"), o.get("type"), o.get("class")
    w = f"offer plan: offers[{i}] ({o.get('code') or o.get('title') or '?'})"
    if method not in ("code", "automatic"):
        raise InputError(f"{w}: 'method' must be code or automatic")
    if not o.get("code" if method == "code" else "title"):
        raise InputError(f"{w}: a {method} offer needs '{'code' if method == 'code' else 'title'}'"
                         + ("" if method == "code" else " (the discount title in Shopify)"))
    if typ not in ("percentage", "fixed_amount", "free_shipping", "bxgy"):
        raise InputError(f"{w}: 'type' must be percentage, fixed_amount, free_shipping or bxgy")
    fixed_cls = {"free_shipping": "shipping", "bxgy": "product"}.get(typ)
    if fixed_cls:
        if cls not in (None, fixed_cls):
            raise InputError(f"{w}: a {typ} offer has class {fixed_cls}")
        cls = fixed_cls
    elif cls not in ("product", "order"):
        raise InputError(f"{w}: 'class' must be product (amount off products) or order (amount off order)")
    value, bx = None, None
    if typ in ("percentage", "fixed_amount"):
        value = dec(o.get("value"), f"{w}: 'value'")
        if value == 0 or (typ == "percentage" and value > HUNDRED):
            raise InputError(f"{w}: 'value' must be above 0" + (" and at most 100" if typ == "percentage" else ""))
    elif typ == "bxgy":
        b = o.get("bxgy") or {}
        bx = {"buy_qty": whole(b.get("buy_quantity"), f"{w}: bxgy.buy_quantity") or 1,
              "get_qty": whole(b.get("get_quantity"), f"{w}: bxgy.get_quantity") or 1,
              "get_pct": dec(b.get("get_percentage", 100), f"{w}: bxgy.get_percentage"), "get_amt": None}
        value = bx["get_pct"]
    el = o.get("eligible", "all")
    if el == "all":
        target = {"type": "all"}
    elif isinstance(el, dict) and isinstance(el.get("collections"), list):
        target = {"type": "collections", "collections": [str(c) for c in el["collections"]]}
    elif isinstance(el, dict) and (isinstance(el.get("products"), list) or isinstance(el.get("skus"), list)):
        target = {"type": "products", "products": [str(c) for c in el.get("products") or []],
                  "variants": [str(c) for c in el.get("skus") or []]}
    else:
        raise InputError(f"{w}: 'eligible' must be \"all\", {{\"collections\": [...]}} or {{\"products\": [...]}}")
    excl = {k: [str(c) for c in o.get(k) or []] for k in ("exclude_collections", "exclude_products", "exclude_skus")}
    ck = {norm_key(c) for c in excl["exclude_collections"]}
    planned = {p["sku"] for p in catalog if p["sku"] in resolve_target(target, catalog) and not (
        p["coll_keys"] & ck or p["handle"] in excl["exclude_products"] or p["sku"] in excl["exclude_skus"])}
    ce = o.get("customer_eligibility", "all")
    first_order = isinstance(ce, dict) and bool(ce.get("first_order"))
    if ce in ("all", None):
        elig = {"type": "all", "names": []}
    elif first_order:
        elig = {"type": "segments", "names": [str(ce["segment"])] if ce.get("segment") else []}
    elif isinstance(ce, dict) and any(isinstance(ce.get(k), list) for k in ("segments", "markets")):
        k = "segments" if isinstance(ce.get("segments"), list) else "markets"
        elig = {"type": k, "names": sorted(str(s) for s in ce[k])}
    elif isinstance(ce, dict) and ce.get("customers"):
        elig = {"type": "customers", "names": []}
    else:
        raise InputError(f"{w}: 'customer_eligibility' must be \"all\", {{\"segments\": [...]}}, "
                         f"{{\"first_order\": true, \"segment\": ...}}, {{\"markets\": [...]}} or {{\"customers\": true}}")
    cw = o.get("combines_with") or []
    if not isinstance(cw, list) or any(c not in CLASSES for c in cw):
        raise InputError(f"{w}: 'combines_with' must be a list of product, order, shipping")
    starts = parse_time(o["starts"], f"{w}: 'starts'", tz) if o.get("starts") else None
    ends = parse_time(o["ends"], f"{w}: 'ends'", tz, end_of_day=True) if o.get("ends") else None
    if starts and ends and ends <= starts:
        raise InputError(f"{w}: 'ends' is not after 'starts'")
    if o.get("phase") and not (starts and ends):
        raise InputError(f"{w}: a phase needs both 'starts' and 'ends'")
    code = o.get("code")
    label = code if method == "code" else o["title"]
    d = {"key": f"plan:{i}", "label": label, "title": o.get("title") or label, "method": method,
         "codes": [code] if code else [], "cls": cls, "type": typ, "value": value, "bxgy": bx,
         "each_item": o.get("applies_on_each_item", True) is not False,
         "target": {"type": "products", "products": [], "variants": sorted(planned)},
         "min_subtotal": dec(o.get("min_subtotal"), f"{w}: 'min_subtotal'", allow_none=True),
         "min_qty": whole(o.get("min_quantity"), f"{w}: 'min_quantity'"), "elig": elig,
         "usage_limit": whole(o.get("usage_limit"), f"{w}: 'usage_limit'"), "once": bool(o.get("once_per_customer")),
         "used": 0, "combines": {c: c in cw for c in CLASSES}, "same_line_tags": [], "tags": [],
         "starts": starts, "ends": ends, "status": "PLAN", "updated": None, "modeled": True}
    d["skus"] = set(planned) if cls == "product" else resolve_skus(d, catalog)
    return {"index": i, "name": str(o.get("name") or label), "phase": o.get("phase"), "method": method,
            "code": code, "title": o.get("title"), "match": (method, label.casefold()), "first_order": first_order,
            "public": bool(o.get("public")), "target": target, "excl": excl, "disc": d}


# --------------------------------------------------------------------------- discount model
def eff_end(d: dict, tz: PosixTZ) -> dt.datetime:
    """End instant. An end at hh:59:00 store time counts through that minute (an "11:59 pm" end)."""
    if d["ends"] is None:
        return LATEST
    local = tz.to_local(d["ends"])
    return d["ends"] + dt.timedelta(minutes=1) if (local.minute, local.second) == (59, 0) else d["ends"]


def start_of(d: dict) -> dt.datetime:
    return d["starts"] or EARLIEST


def is_active(d: dict, t: dt.datetime, tz: PosixTZ) -> bool:
    return d["status"] != "EXPIRED" and start_of(d) <= t < eff_end(d, tz)


def combinable(a: dict, b: dict) -> bool:
    """Shopify combines two discounts only if each allows the other's class; never two shipping discounts."""
    if a["cls"] not in CLASSES or b["cls"] not in CLASSES or a["cls"] == b["cls"] == "shipping":
        return False
    return a["combines"][b["cls"]] and b["combines"][a["cls"]]


def same_line_ok(a: dict, b: dict) -> bool:
    return bool(set(a["same_line_tags"]) & set(b["tags"])) and bool(set(b["same_line_tags"]) & set(a["tags"]))


def product_amount(d: dict, unit: Decimal) -> Decimal:
    if d["type"] == "percentage":
        return q2(unit * d["value"] / HUNDRED)
    if d["type"] == "fixed_amount":
        m = d["min_subtotal"]
        if not d["each_item"] and m and unit < m:
            # split across the cart, which must reach the minimum: this unit's share is at most value x unit / minimum
            return min(q2(d["value"] * unit / m), unit)
        return min(d["value"], unit)
    if d["type"] == "bxgy":       # spread over the bundle, buy and get items at the same price (approximation)
        bx = d["bxgy"]
        if bx["get_pct"] is None:
            return ZERO           # fixed-amount "get": not modeled (reported as check manually)
        return q2(unit * bx["get_pct"] / HUNDRED * bx["get_qty"] / (bx["buy_qty"] + bx["get_qty"]))
    return ZERO


def describe(d: dict) -> str:
    if d["type"] == "percentage":
        return f"{num(d['value'])}% off " + ("products" if d["cls"] == "product" else "order")
    if d["type"] == "fixed_amount":
        on = "order" if d["cls"] == "order" else ("each item" if d["each_item"] else "products")
        return f"{money_s(d['value'])} off {on}"
    if d["type"] == "free_shipping":
        return "free shipping"
    if d["type"] == "bxgy":
        bx = d["bxgy"]
        head = (f"buy {bx['buy_qty']}" if bx["buy_qty"] else f"spend {money_s(bx['buy_amt'])}" if bx.get("buy_amt")
                else "buy X") + f" get {bx['get_qty']}"
        if bx["get_pct"] is not None:
            return f"{head} " + ("free" if bx["get_pct"] == HUNDRED else f"{num(bx['get_pct'])}% off")
        if bx.get("get_amt") is not None:
            return f"{head}, {money_s(bx['get_amt'])} off the get item"
        return f"{head}, get value not given"
    return "app discount" if d["type"] == "app" else "unknown type"


def elig_text(e: dict, first_order: bool = False) -> str:
    if first_order:
        return "first-order customers" + (f", segment {', '.join(e['names'])}" if e["names"] else "")
    return {"all": "all customers", "customers": "specific customers"}.get(
        e["type"], f"{e['type'][:-1] if len(e['names']) == 1 else e['type']} {', '.join(e['names'])}".strip())


def target_text(t: dict) -> str:
    if t["type"] == "unknown":
        return "targets not visible via the API"
    if t["type"] == "all":
        return "All products"
    if t["type"] == "collections":
        return ("collection " if len(t["collections"]) == 1 else "collections ") + ", ".join(t["collections"])
    return plural(len(t.get("products", [])) + len(t.get("variants", [])), "product")


def classes_text(d: dict, plan: dict | None = None) -> str:
    out = [f"**{c}**" if plan is not None and not plan["combines"][c] else c for c in CLASSES if d["combines"][c]]
    return ", ".join(out) or "nothing"


# --------------------------------------------------------------------------- worst case per SKU
def best_for_item(item: dict, active: list, plus: bool) -> tuple:
    """Lowest price one unit of item can reach with these product/order discounts.

    Shopify applies product discounts first (one per item unless Plus same-line tags allow more),
    then order discounts on the reduced subtotal (each percentage on that subtotal), and gives the
    customer the best valid combination. Minimums are assumed met and eligibility is ignored."""
    price = item["price"]
    P = [d for d in active if d["cls"] == "product" and item["sku"] in d["skus"]]
    O = [] if item["gift_card"] else [d for d in active if d["cls"] == "order"]
    groups = [[]] + [[p] for p in P]
    if plus:
        groups += [list(g) for r in (2, 3) for g in itertools.combinations(P, r)
                   if all(combinable(a, b) and same_line_ok(a, b) for a, b in itertools.combinations(g, 2))]
    best = (price, [])
    for g in groups:
        p1 = price - min(price, q2(price * min(sum((d["value"] for d in g if d["type"] == "percentage"), ZERO),
                                                HUNDRED) / HUNDRED))
        for d in g:
            if d["type"] != "percentage":
                p1 -= min(p1, product_amount(d, p1))
        ok = [o for o in O if all(combinable(o, p) for p in g)]
        options = [[]] + [[o] for o in ok]
        multi = [o for o in ok if o["combines"]["order"]]
        if len(multi) > 1:
            room = MAX_CODES - sum(d["method"] == "code" for d in g)
            codes = sorted((o for o in multi if o["method"] == "code"), key=lambda o: (-depth_of(o, p1), o["label"]))
            options.append([o for o in multi if o["method"] == "automatic"] + codes[:max(0, room)])
        for os_ in options:
            final = apply_order(p1, os_)
            if final < best[0] or (final == best[0] and best[1] and len(g) + len(os_) < len(best[1])):
                best = (final, g + os_)
    return best


def depth_of(o: dict, base: Decimal) -> Decimal:
    if o["type"] == "percentage":
        return o["value"] / HUNDRED
    return min(o["value"], base) / base if o["type"] == "fixed_amount" and base else ZERO


def apply_order(p1: Decimal, os_: list) -> Decimal:
    """One unit at p1 after product discounts; a fixed amount with a minimum is shared by a cart at that minimum."""
    rest = max(ZERO, p1 - sum((q2(p1 * o["value"] / HUNDRED) for o in os_ if o["type"] == "percentage"), ZERO))
    for o in os_:
        if o["type"] == "fixed_amount":
            m = o["min_subtotal"]
            rest -= min(rest, o["value"] if not m or p1 >= m else q2(o["value"] * p1 / m))
    return rest


def worst_case(discs: list, catalog: list, spans: list, tz: PosixTZ, plus: bool) -> dict:
    res = {}
    for a, b in spans:
        active = [d for d in discs if d["modeled"] and d["cls"] in ("product", "order") and is_active(d, a, tz)]
        for item in catalog:
            final, used = best_for_item(item, active, plus)
            used = sorted(used, key=lambda d: (CLASSES.index(d["cls"]), d["label"]))
            key = tuple(sorted(d["key"] for d in used))
            cur = res.get(item["sku"])
            if cur is None or final < cur["final"]:
                res[item["sku"]] = {"final": final, "used": used, "key": key, "from": a, "to": b}
            elif final == cur["final"] and key == cur["key"] and cur["to"] == a:
                cur["to"] = b
    for item in catalog:
        r = res[item["sku"]]
        r["depth"] = (item["price"] - r["final"]) / item["price"]
        r["margin"] = None if item["cost"] is None or not r["final"] else (r["final"] - item["cost"]) / r["final"]
    return res


# --------------------------------------------------------------------------- cart simulation
def simulate(discs: list, cat: dict, lines: list, t: dt.datetime, segs: set, codes: list, tz: PosixTZ) -> dict:
    """What Shopify would charge for a cart: the best valid combination for the customer."""
    entered = {c.casefold() for c in codes}
    cands = [d for d in discs if d["modeled"] and d["type"] != "bxgy" and is_active(d, t, tz)
             and (d["elig"]["type"] != "segments" or set(d["elig"]["names"]) & segs or not d["elig"]["names"])
             and d["elig"]["type"] != "customers"
             and (d["method"] == "automatic" or entered & {c.casefold() for c in d["codes"]})]
    items = [(cat[s], q) for s, q in lines]
    best = None
    for r in range(len(cands) + 1):
        for S in itertools.combinations(cands, r):
            if any(not combinable(a, b) for a, b in itertools.combinations(S, 2)) or \
                    sum(d["cls"] == "shipping" for d in S) > 1 or \
                    sum(d["method"] == "code" and d["cls"] != "shipping" for d in S) > MAX_CODES:
                continue
            res = cart_total(S, items)
            if res is not None:
                score = (res["savings"], res["free_ship"], -len(S))
                if best is None or score > best[0]:
                    best = (score, res, S)
    res, S = best[1], best[2]
    got = {c.casefold() for d in S for c in d["codes"]}
    order = {c: i for i, c in enumerate(CLASSES)}
    S = sorted(S, key=lambda d: (order[d["cls"]], d["label"]))
    return {"total": res["total"], "free_ship": res["free_ship"],
            "applied": [d["label"] for d in S if d["cls"] != "shipping"],
            "applied_ship": [d["label"] for d in S if d["cls"] == "shipping"],
            "refused": [c for c in codes if c.casefold() not in got]}


def cart_total(S, items) -> dict | None:
    P, O, H = ([d for d in S if d["cls"] == c] for c in CLASSES)
    prod, used, after = ZERO, set(), []
    for p, q in items:
        best_amt, best_d = ZERO, None
        for d in P:
            ent = [(x, qq) for x, qq in items if x["sku"] in d["skus"]]
            if p["sku"] not in d["skus"] or (d["min_subtotal"] and sum(x["price"] * qq for x, qq in ent) < d["min_subtotal"]) \
                    or (d["min_qty"] and sum(qq for _, qq in ent) < d["min_qty"]):
                continue
            if d["type"] == "percentage":
                amt = q2(p["price"] * q * d["value"] / HUNDRED)
            elif d["type"] == "fixed_amount" and not d["each_item"]:
                tot = sum(x["price"] * qq for x, qq in ent)
                amt = min(p["price"] * q, q2(d["value"] * p["price"] * q / tot))
            else:
                amt = product_amount(d, p["price"]) * q
            if amt > best_amt:
                best_amt, best_d = amt, d
        if best_d is not None:
            used.add(best_d["key"])
        prod += best_amt
        after.append((p, p["price"] * q - best_amt))
    if any(d["key"] not in used for d in P):
        return None               # a product discount that reaches nothing: the same cart as without it
    after_prod = sum((v for _, v in after), ZERO)
    base = sum((v for p, v in after if not p["gift_card"]), ZERO)
    before_ngc = sum((p["price"] * q for p, q in items if not p["gift_card"]), ZERO)
    qty = sum(q for _, q in items)
    for d in O:      # an order minimum must be met before product discounts (no gift cards) and after them
        if (d["min_subtotal"] and min(before_ngc, after_prod) < d["min_subtotal"]) or (d["min_qty"] and qty < d["min_qty"]):
            return None
    rest = max(ZERO, base - sum((q2(base * d["value"] / HUNDRED) for d in O if d["type"] == "percentage"), ZERO))
    for d in O:
        if d["type"] == "fixed_amount":
            rest -= min(rest, d["value"])
    total = after_prod - (base - rest)
    for d in H:      # assumption: the shipping minimum counts prices after product and order discounts
        if (d["min_subtotal"] and total < d["min_subtotal"]) or (d["min_qty"] and qty < d["min_qty"]):
            return None
    return {"total": total, "savings": prod + base - rest, "free_ship": bool(H)}


# --------------------------------------------------------------------------- analysis
def finding(sev: str, section: str, key: str, title: str, detail: list, fix: str | None, **extra) -> dict:
    f = {"severity": sev, "section": section, "key": key, "title": title, "detail": [x for x in detail if x],
         "fix": fix, "live_now": False, "state": "detected", "test": None}
    f.update(extra)
    return f


def guessable(code: str) -> str | None:
    words = {"WELCOME", "SAVE", "SALE", "SUMMER", "SPRING", "FALL", "AUTUMN", "WINTER", "HOLIDAY", "XMAS", "BF",
             "BFCM", "BLACKFRIDAY", "CYBER", "CYBERMONDAY", "VIP", "FREESHIP", "FREESHIPPING", "SHIP", "TAKE", "EXTRA",
             "THANKS", "THANKYOU", "SORRY", "NEW", "FIRST", "DEAL", "GIFT", "LOVE", "HELLO", "SHOP", "OFF", "DISCOUNT",
             "PROMO", "SPECIAL", "TEST", "STAFF", "FRIENDS", "FAMILY", "EMAIL", "SMS", "TEXT"}
    m = re.match(r"^([A-Za-z]{2,15})[-_]?(\d{1,3})$", code)
    if not m:
        return None
    w = m.group(1).upper()
    return f"{'a common word' if w in words else 'a name'} plus a number ({w} + {m.group(2)})"


def local_date(t: dt.datetime, tz: PosixTZ) -> dt.date:
    return tz.to_local(t).date()


def last_day(end: dt.datetime, tz: PosixTZ) -> dt.date:
    return tz.to_local(end - dt.timedelta(seconds=1)).date()


def span_text(A: dict, a: dt.datetime, b: dt.datetime) -> str:
    if b >= A["horizon"]:
        return "now on" if a <= A["now"] else f"from {fmt_day(local_date(a, A['tz']))} on"
    return fmt_span(local_date(a, A["tz"]), last_day(b, A["tz"]))


def when_phrase(A: dict, a: dt.datetime, b: dt.datetime) -> str:
    s = span_text(A, a, b)
    return "from now on" if s == "now on" else s if s.startswith("from") else (
        f"during {s}" if "–" in s else f"on {s}")


def slip_reason(live: dt.datetime, planned_local: dt.datetime) -> str | None:
    for zone, label in SLIP_ZONES:
        if PosixTZ(TZ_RULES[zone], zone, label).to_local(live) == planned_local:
            return f"{planned_local:%H:%M} UTC" if zone == "UTC" else f"{fmt_clock(planned_local)} {label}"
    return None


def analyze(plan: dict, catalog: list, live: list, exported: dt.datetime | None, history: dict | None,
            as_of: dt.date | None, floor_override: Decimal | None) -> dict:
    tz = plan["tz"]
    if exported is None and as_of is None:
        raise InputError("discounts file has no exportedAt; pass --as-of YYYY-MM-DD")
    if as_of is None or (exported is not None and local_date(exported, tz) == as_of):
        now, as_of = exported, local_date(exported, tz)
    else:
        now = tz.to_utc(dt.datetime.combine(as_of, dt.time()))
    phases = sorted([o for o in plan["offers"] if o["phase"]], key=lambda o: o["disc"]["starts"])
    launch = plan["launch"] or (local_date(phases[0]["disc"]["starts"], tz) if phases else None)
    if launch is None:
        raise InputError("offer plan: set 'launch' (YYYY-MM-DD) or give at least one offer a 'phase' with dates")
    end = max((eff_end(o["disc"], tz) for o in phases), default=tz.to_utc(dt.datetime.combine(launch, dt.time()))
              + dt.timedelta(days=14))
    A = {"store": plan["store"], "tz": tz, "now": now, "as_of": as_of, "exported": exported, "launch": launch, "year": launch.year,
         "max": plan["max"], "floor": (floor_override if floor_override is not None else plan["floor_pct"]) / HUNDRED,
         "phases": phases, "offers": plan["offers"], "horizon": end + dt.timedelta(days=1), "plus": plan["plus"],
         "catalog": catalog, "cat": {p["sku"]: p for p in catalog}, "live": live, "economics": plan["economics"],
         "findings": [], "not_checked": [], "attach": {}, "win": {}}
    match_plan(A, plan["must_not"])
    check_settings(A)
    check_windows(A)
    check_eligibility(A)
    check_stacking(A)
    check_codes(A)
    check_reference(A, history)
    F = A["findings"]
    F.sort(key=lambda f: (SEV_ORDER[f["severity"]], SECTIONS.index(f["section"])))
    A["critical"] = sum(f["severity"] == "CRITICAL" for f in F)
    A["warnings"] = sum(f["severity"] == "WARNING" for f in F)
    A["verdict"] = "NOT READY" if A["critical"] else "FIX BEFORE LAUNCH" if A["warnings"] else "READY"
    A["dates"] = {k: launch - dt.timedelta(days=n) for k, n in
                  (("fix_by", FIX_LEAD), ("recheck", RECHECK_LEAD), ("freeze", FREEZE_LEAD), ("final", FINAL_LEAD))}
    A["tests"] = build_tests(A)
    A["fixes"] = build_fixes(A)
    return A


def match_plan(A: dict, must_not: list) -> None:
    tz, now = A["tz"], A["now"]
    used, pairs = set(), []
    for o in A["offers"]:
        title = " ".join((o["title"] or "").split()).casefold()
        cands = sorted((d for d in A["live"] if d["method"] == o["method"] and (
            (o["code"] or "").casefold() in {c.casefold() for c in d["codes"]}
            or (title and title == " ".join(d["title"].split()).casefold()))),
            key=lambda d: (d["status"] == "EXPIRED", d["key"]))
        used.update(d["key"] for d in cands[:1])
        pairs.append({"offer": o, "plan": o["disc"], "live": cands[0] if cands else None, "devs": [],
                      "dupes": [d for d in cands[1:] if d["status"] != "EXPIRED"]})
    names = lambda d: {c.casefold() for c in d["codes"]} | {d["title"].casefold()}   # noqa: E731
    mn = {x.casefold() for x in must_not}
    gone = lambda d: d["status"] == "EXPIRED" or eff_end(d, tz) <= now    # noqa: E731
    A["pairs"] = pairs
    A["plan_of"] = {p["live"]["key"]: p for p in pairs if p["live"] is not None}
    A["must_hits"] = [d for d in A["live"] if d["key"] not in used and names(d) & mn and not gone(d)]
    A["extras"] = [d for d in A["live"] if d["key"] not in used and not names(d) & mn and not gone(d)
                   and start_of(d) < A["horizon"]] + [d for p in pairs for d in p["dupes"]]
    A["ignored"] = [d for d in A["live"] if d["key"] not in used and gone(d)]
    A["must_missing"] = [x for x in must_not if not any(x.casefold() in names(d) for d in A["live"])]
    A["live_set"] = [d for d in A["live"] if not gone(d) and start_of(d) < A["horizon"]]
    A["plan_set"] = [o["disc"] for o in A["offers"]]


def check_settings(A: dict) -> None:
    """Type, value, class, minimum, eligibility, limits of each planned offer vs its live discount."""
    tz, F = A["tz"], A["findings"]
    for p in A["pairs"]:
        o, pd, ld = p["offer"], p["plan"], p["live"]
        if ld is None:
            F.append(finding("CRITICAL" if start_of(pd) < A["horizon"] else "WARNING", "plan", f"missing:{o['index']}",
                             f"Planned offer {o['name']} ({pd['label']}) isn't in the export: no "
                             + (f"code {o['code']}." if o["method"] == "code" else f"automatic discount titled "
                                                                                   f"'{o['title']}'."),
                             ["Create it, or check that the export has every page of discounts."],
                             f"Create {pd['label']} as planned ({describe(pd)})."))
            continue
        if ld["status"] == "EXPIRED" or eff_end(ld, tz) <= A["now"]:
            F.append(finding("CRITICAL", "plan", f"expired:{o['index']}",
                             f"{ld['label']} has expired, but the plan needs it.", [],
                             f"Reactivate {ld['label']} and set its dates to the plan.", live_now=True))
            continue
        devs = p["devs"]
        if ld["type"] != pd["type"] or ld["cls"] != pd["cls"]:
            devs.append(("CRITICAL" if ld["type"] != pd["type"] else "WARNING", "type",
                         f"is set up as {describe(ld)} (plan: {describe(pd)})"))
        elif ld["value"] is not None and pd["value"] is not None and ld["value"] != pd["value"]:
            devs.append(("CRITICAL" if ld["value"] > pd["value"] else "WARNING", "value",
                         f"is {describe(ld)} (plan: {describe(pd)})"))
        elif ld["type"] == "bxgy" and (ld["value"] is None) != (pd["value"] is None):
            devs.append(("WARNING", "value", f"is {describe(ld)} (plan: {describe(pd)}; depth not compared, "
                                             f"check manually)"))
        if ld["type"] == pd["type"] == "fixed_amount" and ld["cls"] == pd["cls"] == "product" \
                and ld["each_item"] != pd["each_item"]:
            devs.append(("CRITICAL" if ld["each_item"] else "WARNING", "each",
                         ("applies the amount to each item" if ld["each_item"] else "splits the amount across the items")
                         + f" (plan: {'each item' if pd['each_item'] else 'split across the items'})"))
        if pd["cls"] != "shipping" and (ld["min_subtotal"] or ZERO) != (pd["min_subtotal"] or ZERO):
            devs.append(("WARNING", "min", f"has a minimum purchase of {money_s(ld['min_subtotal'] or ZERO)} "
                                           f"(plan: {money_s(pd['min_subtotal'] or ZERO)})"))
        le, pe = ld["elig"], pd["elig"]
        if pe["type"] != "all" and le["type"] == "all":
            devs.append(("WARNING", "elig", f"is open to all customers (plan: {elig_text(pe, o['first_order'])})"))
        elif (pe["type"] == "all" and le["type"] != "all") or (pe["type"] == le["type"] == "segments" and pe["names"]
                                                               and not set(pe["names"]) <= set(le["names"])):
            devs.append(("WARNING", "elig", f"is limited to {elig_text(le)} (plan: {elig_text(pe, o['first_order'])})"))
        if pd["usage_limit"] is not None and (ld["usage_limit"] or pd["usage_limit"] + 1) > pd["usage_limit"]:
            have = "no usage limit" if ld["usage_limit"] is None else f"a usage limit of {ld['usage_limit']:,}"
            devs.append(("WARNING", "usage", f"has {have} (plan: {pd['usage_limit']:,} in total)"))
        if pd["once"] and not ld["once"]:
            devs.append(("WARNING", "once", "isn't limited to one use per customer (plan: once per customer)"))
        if ld["usage_limit"] and ld["used"] >= ld["usage_limit"] * NEAR_LIMIT:
            devs.append(("WARNING", "near", f"has used {ld['used']:,} of its {ld['usage_limit']:,} uses"))


def check_windows(A: dict) -> None:
    tz, F, year, lab = A["tz"], A["findings"], A["year"], A["tz"].label
    win = A["win"]
    for p in A["pairs"]:
        o, pd, ld = p["offer"], p["plan"], p["live"]
        if ld is None or ld["status"] == "EXPIRED":
            continue
        issues = []
        for kind in ("start", "end"):
            pv, lv = pd[kind + "s"], ld[kind + "s"]
            if pv is None:
                if kind == "start" and lv > A["now"]:
                    issues.append({"kind": "late", "sev": "WARNING", "text": f"doesn't start until "
                                   f"{fmt_local(tz.to_local(lv), year)} {lab} (plan: always on)"})
                if kind == "end" and lv is not None and eff_end(ld, tz) < A["horizon"]:
                    issues.append({"kind": "end", "sev": "WARNING", "text": f"ends {fmt_local(tz.to_local(lv), year)} "
                                   f"{lab} (plan: no end date)"})
                continue
            if lv is None:
                issues.append({"kind": "noend", "sev": "CRITICAL" if o["phase"] else "WARNING",
                               "text": f"has no end date (plan: ends {fmt_local(tz.to_local(pv), year)} {lab})"})
                continue
            if lv == pv:
                continue
            pl, ll = tz.to_local(pv), tz.to_local(lv)
            early = lv < pv
            why = slip_reason(lv, pl)
            delta = abs((eff_end(ld, tz) - eff_end(pd, tz)) if kind == "end" else (lv - pv))
            day = f" {fmt_wday(pl.date())}" if pl.date() != ll.date() else ""
            sev = "CRITICAL" if kind == "start" and early and lv <= A["now"] else "WARNING"
            issues.append({"kind": kind, "sev": sev, "live": ll, "plan": pl, "why": why, "early": early,
                           "delta": delta, "text": f"{kind}s {fmt_wday(ll.date())} at {fmt_clock(ll)} {lab}"
                           + (f" ({why})" if why else "") + f" instead of {fmt_clock(pl)} {lab}{day}"})
        if issues:
            win[o["index"]] = issues
    chain = []
    for a, b in zip(A["phases"], A["phases"][1:]):
        pa, pb = (next(p for p in A["pairs"] if p["offer"] is x) for x in (a, b))
        if pa["live"] is None or pb["live"] is None:
            continue
        c = {"a": pa, "b": pb, "plan_gap": start_of(pb["plan"]) - eff_end(pa["plan"], tz),
             "gap": start_of(pb["live"]) - eff_end(pa["live"], tz)}
        chain.append(c)
        g0, g1 = tz.to_local(eff_end(pa["live"], tz)), tz.to_local(start_of(pb["live"]))
        owner = a["index"] if a["index"] in win or b["index"] not in win else b["index"]
        if c["gap"] > GAP_TOLERANCE and c["gap"] - c["plan_gap"] > GAP_TOLERANCE:
            c["kind"] = "gap"
            mid = "midnight" if (g1.hour, g1.minute) == (0, 0) and g1.date() == g0.date() + dt.timedelta(days=1) \
                else f"{fmt_clock(g1)} {fmt_wday(g1.date())}"
            win.setdefault(owner, []).append({"kind": "gap", "sev": "WARNING", "text": (
                f"a {fmt_dur(c['gap'], adjective=True)} gap ({fmt_wday(g0.date())}, {fmt_clock(g0)} to {mid}) "
                f"before {pb['live']['label']} starts")})
        elif c["gap"] < -GAP_TOLERANCE and c["gap"] < c["plan_gap"] - GAP_TOLERANCE:
            c["kind"] = "overlap"
            win.setdefault(owner, []).append({"kind": "overlap", "sev": "WARNING", "text": (
                f"{pa['live']['label']} and {pb['live']['label']} overlap "
                + (f"for all of {pb['live']['label']}" if pa["live"]["ends"] is None else f"for {fmt_dur(-c['gap'])}"))})
    A["chain"] = chain
    for p in A["pairs"]:
        issues, ld, pd = win.get(p["offer"]["index"]), p["live"], p["plan"]
        if not issues or ld is None:
            continue
        main = [i for i in issues if i["kind"] not in ("gap", "overlap")]
        tail = [i for i in issues if i["kind"] in ("gap", "overlap")]
        if main:
            title = f"{ld['label']} " + join_and(i["text"] for i in main)
            if not (tail and tail[0]["kind"] == "gap") and main[-1].get("delta"):
                title += f" ({fmt_dur(main[-1]['delta'])} {'early' if main[-1]['early'] else 'late'})"
            title += "".join((", leaving " if i["kind"] == "gap" else ", so ") + i["text"] for i in tail) + "."
        else:
            title = "The live phases leave " + join_and(i["text"] for i in tail) + "."
        detail = []
        whys = [i.get("why") for i in main if i.get("why")]
        if whys:
            detail.append("The time looks like it was entered in " + ("UTC (by an app, an import or an API script)"
                          if "UTC" in whys[0] else "another time zone") + ", not in the store's time zone.")
        if tail and tail[0]["kind"] == "gap":
            detail.append("Shoppers who try the code in that window are told it isn't valid, and no sale discount "
                          "applies.")
        fixes = []
        for i in main:
            k = "starts" if i["kind"] in ("start", "late") else "ends"
            when = pd[k]
            fixes.append(f"{k[:-1]} to {fmt_wday(tz.to_local(when).date())}, {fmt_clock(tz.to_local(when))} {lab}"
                         if when else ("start date to now" if k == "starts" else "no end date"))
        api = f", or {pd['ends']:%Y-%m-%dT%H:%M:%SZ} through the API or an app" \
            if pd["ends"] is not None and any(i["kind"] in ("end", "noend") for i in main) else ""
        fix = (f"Set {ld['label']}'s {join_and(fixes)} in the admin (store time){api}." if fixes
               else "Change the phase dates so the next phase starts as the previous one ends.")
        F.append(finding("CRITICAL" if any(i["sev"] == "CRITICAL" for i in issues) else "WARNING", "windows",
                         f"window:{p['offer']['index']}", title, detail, fix, pair=p))


def check_eligibility(A: dict) -> None:
    cat, F = A["cat"], A["findings"]
    A["elig_rows"] = []
    for p in A["pairs"]:
        o, pd, ld = p["offer"], p["plan"], p["live"]
        if pd["cls"] != "product" or ld is None or ld["status"] == "EXPIRED":
            continue
        if ld["type"] in ("app", "unknown") or ld["cls"] == "multi" or ld["target"]["type"] == "unknown":
            F.append(finding("INFO", "eligibility", f"app:{o['index']}",
                             f"{ld['label']}: {'App' if ld['type'] == 'app' else 'Multi-class or unknown'} discount: "
                             f"targeting not visible via API; check manually.", [], None, pair=p))
            continue
        if ld["cls"] != "product":
            continue
        if ld["target"].get("truncated"):
            A["not_checked"].append(f"eligibility for {ld['label']} (its product or collection list was cut off at 50 "
                                    f"in the export; check Applies to in the admin)")
            continue
        extra, missing = ld["skus"] - pd["skus"], pd["skus"] - ld["skus"]
        A["elig_rows"].append({"pair": p, "extra": extra, "missing": missing})
        ck = {norm_key(c): c for c in o["excl"]["exclude_collections"]}
        if not ld["skus"]:
            F.append(finding("WARNING", "eligibility", f"empty:{o['index']}",
                             f"{ld['label']}'s target ({target_text(ld['target'])}) matches no SKU in the products file.",
                             ["Check the collection or product names against the products file."], None))
        if extra:
            groups = {}
            for s in extra:
                hit = [ck[k] for k in sorted(cat[s]["coll_keys"] & set(ck))]
                groups.setdefault(hit[0] if hit else "Other", []).append(s)
            order = sorted(groups, key=lambda k: (k == "Other", -len(groups[k]), k))
            depth = num(ld["value"]) + "%" if ld["type"] == "percentage" else describe(ld)
            detail = []
            for k in order:
                parts = []
                for s in sorted(groups[k], key=lambda s: (-cat[s]["price"], s)):
                    it = cat[s]
                    cost = f" (unit cost {money(it['cost'])})" if it["cost"] is not None and it["compare_at"] else ""
                    parts.append(f"{it['name']} {money(it['price'])} → {money(it['price'] - product_amount(ld, it['price']))}{cost}")
                detail.append(f"{k}: " + ", ".join(parts) + ".")
            marked = sorted((cat[s]["compare_at"] - cat[s]["price"]) / cat[s]["compare_at"] for s in extra
                            if cat[s]["compare_at"] and cat[s]["compare_at"] > cat[s]["price"])
            if marked:
                lo, hi = pct(marked[0], digits="1"), pct(marked[-1], digits="1")
                detail.append(f"{plural(len(marked), 'of them is', 'of them are')} already marked down "
                              f"{lo if lo == hi else lo[:-1] + '–' + hi} from compare-at.")
            alt = next((d for d in sorted(A["live"], key=lambda d: d["key"]) if d is not ld and d["cls"] == "product"
                        and d["target"]["type"] == "collections" and len(d["target"]["collections"]) == 1
                        and d["skus"] == pd["skus"]), None)
            leave = join_and(o["excl"]["exclude_collections"]) or "the excluded items"
            to = (f"the {alt['target']['collections'][0]} collection (the one {alt['label']} uses), which leaves out "
                  f"{leave}." if alt else f"collections or products that leave out {leave}.")
            F.append(finding(
                "CRITICAL" if set(groups) - {"Other"} else "WARNING", "eligibility", f"target:{o['index']}",
                f"{ld['label']} applies to {target_text(ld['target'])}, so {plural(len(extra), 'SKU')} the plan leaves "
                f"out get {depth} off: " + join_and(f"{k} ({len(groups[k])})" for k in order) + ".", detail,
                f"Change {ld['label']}'s Applies to from {target_text(ld['target'])} to {to}", pair=p, extra=extra))
        if missing and ld["skus"]:
            F.append(finding("WARNING", "eligibility", f"untargeted:{o['index']}",
                             f"{plural(len(missing), 'planned SKU')} don't get {ld['label']}: "
                             + ", ".join(cat[s]["name"] for s in sorted(missing)[:6]) + ".", [],
                             f"Add them to {ld['label']}'s Applies to (or to its collection).", pair=p))


def pair_worst(a: dict, b: dict, catalog: list) -> Decimal | None:
    best = None
    for item in catalog:
        final, used = best_for_item(item, [a, b], True)
        if len(used) == 2:
            dpt = (item["price"] - final) / item["price"]
            best = dpt if best is None or dpt > best else best
    return best


def check_stacking(A: dict) -> None:
    tz, cat, F, now = A["tz"], A["cat"], A["findings"], A["now"]
    pts = sorted({now, A["horizon"]} | {t for d in A["live_set"] + A["plan_set"] for t in (start_of(d), eff_end(d, tz))
                                        if now < t < A["horizon"]})
    spans = list(zip(pts, pts[1:]))
    A["worst_live"] = wl = worst_case(A["live_set"], A["catalog"], spans, tz, A["plus"])
    A["worst_plan"] = wp = worst_case(A["plan_set"], A["catalog"], spans, tz, A["plus"])
    plan_of, order = A["plan_of"], {c: i for i, c in enumerate(CLASSES)}
    rows = []
    for a, b in itertools.combinations(A["live_set"], 2):
        a, b = sorted((a, b), key=lambda d: (order.get(d["cls"], 3), d["label"]))
        lo, hi = max(start_of(a), start_of(b), now), min(eff_end(a, tz), eff_end(b, tz), A["horizon"])
        if not combinable(a, b) or lo >= hi:
            continue
        pa, pb = plan_of.get(a["key"]), plan_of.get(b["key"])
        allowed = bool(pa and pb and combinable(pa["plan"], pb["plan"]))
        kinds = {a["cls"], b["cls"]}
        worst = None
        if "shipping" in kinds:
            how = "free shipping on top"
        elif kinds == {"product", "order"}:
            how, worst = "product discount first, then the order discount on the reduced price", pair_worst(a, b, A["catalog"])
        elif kinds == {"order"}:
            how, worst = "both on the same subtotal (percentages add up, fixed amounts after)", pair_worst(a, b, A["catalog"])
        elif A["plus"] and same_line_ok(a, b):
            how, worst = "on the same item (Plus same-line tags)", pair_worst(a, b, A["catalog"])
        else:
            how = "separate items only (one product discount per item)"
            worst = max((d["value"] / HUNDRED for d in (a, b) if d["type"] == "percentage"), default=None)
        rows.append({"a": a, "b": b, "from": lo, "to": hi, "how": how, "allowed": allowed, "worst": worst,
                     "plan": "allowed" if allowed else "not allowed" if pa and pb else "not in plan"})
    rows.sort(key=lambda r: (r["allowed"], -(r["worst"] or 0), r["from"], r["a"]["label"], r["b"]["label"]))
    A["pair_rows"] = rows
    paired = {(r[x]["key"], r[y]["cls"]) for r in rows for x, y in (("a", "b"), ("b", "a"))}
    for p in A["pairs"]:
        ld, pd = p["live"], p["plan"]
        if ld is None or ld not in A["live_set"] or ld["cls"] not in CLASSES:
            continue
        for c in CLASSES:
            if ld["combines"][c] and not pd["combines"][c] and (ld["key"], c) not in paired and \
                    not (c == "shipping" == ld["cls"]):
                F.append(finding("INFO", "stacking", f"latent:{ld['key']}:{c}",
                                 f"{ld['label']} is set to combine with {c} discounts (plan: no). No live {c} discount "
                                 f"allows {ld['cls']} discounts back, so nothing stacks today; one that did would.",
                                 [], f"Untick {c.capitalize()} discounts in {ld['label']}'s Combinations."))
    allowed_pairs = {frozenset((r["a"]["key"], r["b"]["key"])) for r in rows if r["allowed"]}
    over = {s: r for s, r in wl.items() if r["depth"] > A["max"] + TOL}
    stacks, covered = {}, set()
    for s in sorted(over):
        used = over[s]["used"]
        if len(used) > 1 and any(frozenset((x["key"], y["key"])) not in allowed_pairs
                                 for x, y in itertools.combinations(used, 2)):
            stacks.setdefault(over[s]["key"], []).append(s)
            covered.add(s)
    n_all = sum(not p["gift_card"] for p in A["catalog"])
    for key, skus in sorted(stacks.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        used = sorted(wl[skus[0]]["used"], key=lambda d: (order[d["cls"]], d["label"]))
        depths = sorted(wl[s]["depth"] for s in skus)
        r0 = wl[skus[0]]
        settings = [f"{d['label']}{' is set to combine' if i == 0 else ''} with " + join_and(
            sorted({x["cls"] + " discounts" for x in used if x is not d})) for i, d in enumerate(used)]
        rng = pct(depths[-1]) if pct(depths[0]) == pct(depths[-1]) else f"{pct(depths[0])}–{pct(depths[-1])}"
        title = (f"{join_and(d['label'] for d in used)} stack: {join_and(settings)}, so "
                 f"{f'all {n_all} discountable SKUs' if len(skus) == n_all else plural(len(skus), 'SKU')} can go "
                 f"{rng} off {when_phrase(A, r0['from'], r0['to'])} (plan max {pct(A['max'])}).")
        detail = []
        if len(used) == 2 and [d["cls"] for d in used] == ["product", "order"] and all(
                d["type"] == "percentage" for d in used):
            k1, k2 = ((HUNDRED - d["value"]) / HUNDRED for d in used)
            detail.append(f"Shopify takes the {num(used[0]['value'])}% product discount first, then "
                          f"{num(used[1]['value'])}% off the reduced subtotal: 1 − {num(k1)} × {num(k2)} = "
                          f"{pct(1 - k1 * k2)}.")
        if sum(d["cls"] == "product" for d in used) > 1:
            detail.append("Their tags let them share a line (Shopify Plus), so both product discounts apply to the same "
                          "item; percentages on one line are added here as the worst case.")
        if all(d["key"] in plan_of for d in used):
            detail.append("The plan says they must not combine.")
        untick = []
        for d in used:
            pd = plan_of[d["key"]]["plan"] if d["key"] in plan_of else None
            wrong = [c for c in sorted({x["cls"] for x in used if x is not d}) if not (pd and pd["combines"][c])]
            if wrong:
                untick.append(f"on {d['label']} untick {join_and(c.capitalize() + ' discounts' for c in wrong)}")
        fix = "In Combinations, " + "; ".join(untick) + "." + (" Either one stops the stack; change both so a later "
                                                                 "edit can't reopen it." if len(untick) > 1 else "")
        F.append(finding("CRITICAL", "stacking", "stack:" + "+".join(key), title, detail, fix, used=used))
    for r in rows:     # forbidden pairs past the max that tie with (or hide behind) a reported stack
        a, b = r["a"], r["b"]
        if r["allowed"] or r["worst"] is None or r["worst"] <= A["max"] + TOL or "separate" in r["how"] or any(
                {a["key"], b["key"]} <= {d["key"] for d in f.get("used", [])} for f in F):
            continue
        skus = [p["sku"] for p in A["catalog"] if (p["price"] - best_for_item(p, [a, b], A["plus"])[0]) / p["price"]
                > A["max"] + TOL and len(best_for_item(p, [a, b], A["plus"])[1]) == 2]
        F.append(finding("CRITICAL", "stacking", f"stack:{a['key']}+{b['key']}",
                         f"{a['label']} and {b['label']} stack: {a['label']} is set to combine with {b['cls']} discounts "
                         f"and {b['label']} with {a['cls']} discounts, so {plural(len(skus), 'SKU')} can go up to "
                         f"{pct(r['worst'])} off {when_phrase(A, r['from'], r['to'])} (plan max {pct(A['max'])}).",
                         ["Neither is allowed to combine with the other in the plan." if a["key"] in plan_of and
                          b["key"] in plan_of else "At least one of them isn't in the plan."],
                         f"In Combinations, on {a['label']} untick {b['cls'].capitalize()} discounts; on {b['label']} "
                         f"untick {a['cls'].capitalize()} discounts.", used=[a, b]))
    for r in rows:     # combinations the plan forbids that don't go past the max: report with the code's settings
        a, b = r["a"], r["b"]
        if r["allowed"] or not (a["key"] in plan_of and b["key"] in plan_of) or any(
                {a["key"], b["key"]} <= {d["key"] for d in f.get("used", [])} for f in F):
            continue
        owner, other = (b, a) if b["method"] == "code" and a["method"] != "code" else (a, b)
        A["attach"].setdefault(owner["key"], []).append((other, r))
    single = {}
    for s in sorted(set(over) - covered):
        single.setdefault(wl[s]["key"], []).append(s)
    for key, skus in sorted(single.items()):
        used = wl[skus[0]]["used"]
        if len(used) == 1 and used[0]["key"] in plan_of and any(
                dv[1] in ("value", "type") for dv in plan_of[used[0]["key"]]["devs"]):
            continue
        F.append(finding("WARNING", "stacking", "over:" + "+".join(key),
                         f"{plural(len(skus), 'SKU')} can go {pct(max(wl[s]['depth'] for s in skus))} off through "
                         f"{join_and(d['label'] for d in used)} (plan max {pct(A['max'])}): "
                         + ", ".join(cat[s]["name"] for s in skus[:5]) + ".",
                         ["The plan itself allows this; raise the max or change the offer."], None))
    real = [p for p in A["catalog"] if not p["gift_card"] and p["cost"] is not None]
    A["below"] = below = [p for p in real if wl[p["sku"]]["final"] < p["cost"]]
    # A $0.00 worst case has no margin (None); at a positive unit cost it is already below cost.
    A["thin"] = thin = [p for p in real if p not in below and wl[p["sku"]]["margin"] is not None
                        and wl[p["sku"]]["margin"] < A["floor"]]
    stack_fix = [f for f in F if f["key"].startswith("stack:")]
    for group, sev, what in ((below, "CRITICAL", "below unit cost"),
                             (thin, "WARNING", f"below the {pct(A['floor'])} margin floor")):
        if not group:
            continue
        group = sorted(group, key=lambda p: (-math.inf if wl[p["sku"]]["margin"] is None else wl[p["sku"]]["margin"],
                                             p["name"]))
        # One date window per stack: SKUs reached by different stacks can hit their worst case on different days.
        by_via = {}
        for p in group:
            w = wl[p["sku"]]
            by_via.setdefault(" + ".join(d["label"] for d in w["used"]), []).append(span_text(A, w["from"], w["to"]))
        stacks = "; ".join(f"{v}, {' / '.join(dict.fromkeys(spans))}" for v, spans in sorted(by_via.items()))
        title = (f"{plural(len(group), 'SKU')} sell{'s' if len(group) == 1 else ''} {what} at the worst-case "
                 f"stack{'s' if len(by_via) > 1 else ''} ({stacks}): "
                 + join_and(f"{p['name']} at {money(wl[p['sku']]['final'])} (unit cost {money(p['cost'])})"
                            for p in group) + ".")
        stock = sum(p["inventory"] or 0 for p in group)
        detail = [f"{stock:,} units in stock. That is before shipping and payment fees." if stock else ""]
        plan_sets = {" + ".join(d["label"] for d in wp[p["sku"]]["used"]) for p in group}
        prices = join_and(money(wp[p["sku"]]["final"]) for p in group)
        detail.append(f"Configured as planned, {'its' if len(group) == 1 else 'their'} worst case is "
                      + (f"{plan_sets.pop() or 'no discount'} alone: {prices}." if len(plan_sets) == 1 else f"{prices}."))
        F.append(finding(sev, "stacking", "below" if sev == "CRITICAL" else "thin", title, detail,
                         "No separate change: the targeting and stacking fixes bring these back to the planned worst "
                         f"case ({prices}). Re-run to confirm. To keep them out of every code, exclude them there too; "
                         "Promo Margin Guard sets per-SKU floors." if stack_fix else
                         "Exclude these SKUs from the discounts that reach them, or reprice; Promo Margin Guard sets "
                         "per-SKU floors.", skus=[p["sku"] for p in group]))


def check_codes(A: dict) -> None:
    tz, F, econ, year, plan_of = A["tz"], A["findings"], A["economics"], A["year"], A["plan_of"]
    for p in A["pairs"]:
        o, pd, ld = p["offer"], p["plan"], p["live"]
        if ld is None or ld["status"] == "EXPIRED" or pd["cls"] != "shipping":
            continue
        pm, lm = pd["min_subtotal"] or ZERO, ld["min_subtotal"] or ZERO
        if lm >= pm:
            continue
        live_now = is_active(ld, A["now"], tz)
        title = (f"{ld['label']} has {'no minimum purchase' if not lm else 'a minimum of ' + money_s(lm)} "
                 f"({money_s(lm)} instead of {money_s(pm)}), so "
                 f"{'every order ships free' if not lm else 'orders from ' + money_s(lm) + ' ship free'}.")
        if econ["ship_cost"] is not None and econ["last_orders"] and econ["last_under"] is not None:
            share = Decimal(econ["last_under"]) / Decimal(econ["last_orders"])
            line = (f"Estimate, not a measurement: if this BFCM looks like last year's ({econ['last_orders']:,} orders, "
                    f"{econ['last_under']:,} under {money_s(pm)}), that is about "
                    f"{money0(econ['ship_cost'] * econ['last_under'])} of shipping on orders the plan wouldn't ship "
                    f"free ({econ['last_under']:,} × {money(econ['ship_cost'])} per order).")
            if live_now and econ["per_day"]:
                line += (f" It is live now: at about {num(econ['per_day'])} orders a day and the same {pct(share)} "
                         f"share, roughly {money0(econ['per_day'] * share * econ['ship_cost'])} a day.")
        else:
            line = "Add shipping cost and last BFCM's order counts to the plan's economics for a cost estimate."
        upd = f"Last edited {fmt_wday(local_date(ld['updated'], tz), year)}." if ld["updated"] else ""
        F.append(finding("CRITICAL", "codes", f"shipmin:{o['index']}", title, [line, upd],
                         f"Set {ld['label']}'s minimum purchase amount to {money_s(pm)}."
                         + (" It is live now." if live_now else ""), live_now=live_now, pair=p))
    for p in A["pairs"]:
        o, pd, ld = p["offer"], p["plan"], p["live"]
        if ld is None or ld["status"] == "EXPIRED":
            continue
        others = A["attach"].get(ld["key"], [])
        devs = p["devs"] + [("WARNING", "combo", f"can combine with {x['label']} (plan: must not)") for x, _ in others]
        g = guessable(ld["codes"][0]) if ld["method"] == "code" and len(ld["codes"]) == 1 else None
        open_uncapped = ld["method"] == "code" and ld["elig"]["type"] == "all" and ld["usage_limit"] is None
        if not devs:
            if g and open_uncapped and not o["public"] and (ld["type"] == "free_shipping" or (ld["value"] or 0) >= 15):
                F.append(finding("WARNING", "codes", f"guess:{o['index']}",
                                 f"{ld['label']} ({describe(ld)}) is open to everyone with no usage limit, and the code is "
                                 f"{g}, the kind coupon sites and browser extensions try.", [],
                                 f"Add a usage limit to {ld['label']}, or mark the offer \"public\": true in the plan.",
                                 pair=p))
            continue
        detail = [f"{plural(ld['used'], 'use')} so far." if ld["used"] else ""]
        if g and open_uncapped:
            detail.append(f"The code is {g}: easy to guess, and coupon sites repost codes like it, so an uncapped code "
                          f"can run far past its planned uses.")
        for x, r in others:
            if r["how"].startswith("separate"):
                detail.append(f"Shopify gives each item only one product discount, so {ld['label']} doesn't deepen any "
                              f"one item during {x['label']}, but it discounts the items {x['label']} leaves out in "
                              f"the same cart.")
        fixes, also = [], []
        for dv in devs:
            if dv[1] == "usage":
                left = f" ({pd['usage_limit'] - ld['used']:,} left after the {ld['used']:,} so far)" if ld["used"] else ""
                fixes.append(f"set Maximum discount uses to {pd['usage_limit']:,} in total{left}")
            elif dv[1] == "once":
                fixes.append("tick Limit to one use per customer")
            elif dv[1] == "elig":
                fixes.append(f"set Eligibility to {elig_text(pd['elig'], o['first_order'])}")
            elif dv[1] in ("value", "type"):
                fixes.append(f"make it {describe(pd)}")
            elif dv[1] == "min":
                fixes.append(f"set the minimum purchase to {money_s(pd['min_subtotal'] or ZERO)}")
            elif dv[1] == "near":
                fixes.append("raise the usage limit if the offer must run through BFCM")
            elif dv[1] == "each":
                fixes.append("tick Only apply discount once per order" if pd["each_item"] is False
                             else "untick Only apply discount once per order")
        for x, _ in others:
            if ld["combines"][x["cls"]] and not pd["combines"][x["cls"]]:
                fixes.append(f"untick {x['cls'].capitalize()} discounts in Combinations")
            xp = plan_of[x["key"]]["plan"]
            if x["combines"][ld["cls"]] and not xp["combines"][ld["cls"]]:
                also.append(f"On {x['label']}, untick {ld['cls'].capitalize()} discounts too.")
        fix = (f"On {ld['label']}, " + join_and(sorted(set(fixes), key=fixes.index)) + "." + "".join(
            " " + a for a in sorted(set(also)))) if fixes else None
        F.append(finding("CRITICAL" if any(dv[0] == "CRITICAL" for dv in devs) else "WARNING", "codes",
                         f"settings:{o['index']}", f"{ld['label']}"
                         + ("" if any(dv[1] in ("value", "type") for dv in devs) else f" ({describe(ld)})")
                         + " " + join_and(dv[2] for dv in devs) + ".", detail, fix, pair=p, devs=devs))
    for d in A["must_hits"]:
        live_now = is_active(d, A["now"], tz)
        g = guessable(d["codes"][0]) if d["method"] == "code" else None
        when = "with no end date" if d["ends"] is None else f"until {fmt_wday(last_day(eff_end(d, tz), tz), year)}"
        first = (f"Active since {fmt_wday(local_date(d['starts'], tz), year)}" if live_now else
                 f"Starts {fmt_wday(local_date(d['starts'], tz), year)}") + f", {plural(d['used'], 'use')}" + (
            f", last edited {fmt_wday(local_date(d['updated'], tz), year)}." if d["updated"] else ".")
        detail = [first]
        if d["ends"] is None and d["updated"] and d["updated"] > d["starts"]:
            detail.append("Shopify clears the end date when an expired discount is reactivated, a common way old "
                          "codes come back.")
        combos = [x["label"] for x in A["live_set"] if x is not d and combinable(d, x)]
        reach = "everything" if d["target"]["type"] == "all" else "items in " + target_text(d["target"])
        marked = any(A["cat"][s]["compare_at"] and A["cat"][s]["compare_at"] > A["cat"][s]["price"] for s in d["skus"])
        if combos:
            detail.append(f"It can combine with {join_and(combos)}.")
        elif d["type"] in ("percentage", "fixed_amount"):
            detail.append(f"It doesn't combine with other discounts, so it can't stack, but anyone with the code gets "
                          f"{describe(d).rsplit(' off', 1)[0]} off {reach}" + (", marked-down items included" if marked
                                                                                else "") + ".")
        if g:
            detail[-1] += f" The code is {g}, the kind coupon sites and browser extensions try first."
        state = "still active" if live_now else f"scheduled to start {fmt_wday(local_date(d['starts'], tz), year)}"
        F.append(finding("CRITICAL", "plan", f"mustnot:{d['key']}",
                         f"{d['label']} ({describe(d)}) is {state} {when}; the plan says it must not be active.", detail,
                         f"Deactivate {d['label']} (Discounts → {d['label']} → Deactivate)."
                         + (" It is live now." if live_now else ""), live_now=live_now, disc=d))
    for d in A["extras"]:
        combos = [x["label"] for x in A["live_set"] if x is not d and combinable(d, x)]
        F.append(finding("WARNING", "plan", f"unplanned:{d['key']}",
                         f"{d['label']} ({describe(d)}) is {'active' if is_active(d, A['now'], tz) else 'scheduled'} "
                         f"but isn't in the plan.", [f"It can combine with {join_and(combos)}." if combos else ""],
                         f"Add {d['label']} to the plan if it should run, or deactivate it.", disc=d))
    odd = [d for d in A["live_set"] if not d["modeled"]]
    if odd:
        A["not_checked"].append("stacking and below-cost for " + join_and(f"{d['label']} ({describe(d)})" for d in odd)
                                + ": not modeled (app and multi-class discounts run their own logic, and a buy X get Y "
                                "with a fixed-amount get isn't modeled), so check them manually")


def check_reference(A: dict, history: dict | None) -> None:
    cat, F, tz, year = A["cat"], A["findings"], A["tz"], A["year"]
    A["ref"] = ref = {"below": [p for p in A["catalog"] if p["compare_at"] and p["compare_at"] < p["price"]],
                      "raises": [], "ok": [], "short": [], "history": history}
    if ref["below"]:
        F.append(finding("WARNING", "reference", "cmp-below",
                         f"{plural(len(ref['below']), 'SKU')} {'has a compare-at' if len(ref['below']) == 1 else 'have compare-at prices'}"
                         f" below the selling price: " + ", ".join(f"{p['name']} ({money(p['compare_at'])} vs "
                                                                   f"{money(p['price'])})" for p in ref["below"]) + ".",
                         ["Storefronts usually hide it, but feeds and apps may show it; Product Feed Auditor checks "
                          "the feed side."], "Clear or correct these compare-at prices."))
    if history is None:
        A["not_checked"].append("compare-at raises (no --price-history file)")
        return
    ref["never"] = []
    first = A["phases"][0]["disc"]["starts"] if A["phases"] else tz.to_utc(dt.datetime.combine(A["launch"], dt.time()))
    for sku in sorted(history["rows"]):
        rows = history["rows"][sku]
        for (_, p0, c0), (d1, p1, c1) in zip(rows, rows[1:]):
            if not (c1 > c0 and c1 > p1 and (cat[sku]["compare_at"] or ZERO) >= c1):
                continue
            known = days = 0
            for k in range(1, LOOKBACK_DAYS + 1):
                st = next((r for r in reversed(rows) if r[0] <= d1 - dt.timedelta(days=k)), None)
                if st is not None:
                    known += 1
                    days += st[1] >= c1
            rec = {"sku": sku, "date": d1, "old": c0, "new": c1, "price": p1, "prev": p0, "days": days, "known": known,
                   "lead": (local_date(first, tz) - d1).days}
            ref["ok" if days >= REFERENCE_MIN_DAYS else "short" if known < REFERENCE_MIN_DAYS else "raises"].append(rec)
    for r in ref["raises"]:
        it = cat[r["sku"]]
        sale = next((o["disc"] for o in A["phases"] if r["sku"] in o["disc"]["skus"]
                     and o["disc"]["type"] == "percentage" and o["disc"]["cls"] == "product"), None)
        old = f"from {money(r['old'])} " if r["old"] else ""
        title = (f"{it['name']}: compare-at raised {old}to {money(r['new'])} on {fmt_wday(r['date'], year)}, but in "
                 f"the {r['known']} days of history before that it sold at {money(r['prev'])} and "
                 + (f"never at {money(r['new'])}" if not r["days"] else
                    f"was at {money(r['new'])} or more on only {r['days']} of those days")
                 + ". Reference-price risk to review, not a legal conclusion.")
        detail = [f"Today the product page shows {money(r['new'])} crossed out next to {money(it['price'])} "
                  f"({pct((r['new'] - it['price']) / r['new'])} off)."]
        if sale is not None:
            sp = it["price"] - product_amount(sale, it["price"])
            detail[0] += (f" In the sale it ends at {money(sp)}, which reads as {pct((r['new'] - sp) / r['new'])} off "
                          f"{money(r['new'])} for a {num(sale['value'])}% discount.")
        if 0 <= r["lead"] < REFERENCE_MIN_DAYS:
            detail.append(f"It was raised {plural(r['lead'], 'day')} before the sale starts.")
        detail.append("The FTC's Guides Against Deceptive Pricing (16 CFR 233.1) treat a former price as genuine when "
                      "the item was openly and actively offered at it for a reasonably substantial period in the "
                      f"recent, regular course of business. This check flags a compare-at the SKU wasn't sold at on at "
                      f"least {REFERENCE_MIN_DAYS} of the {LOOKBACK_DAYS} days before it was set. State laws can be "
                      f"stricter.")
        F.append(finding("WARNING", "reference", f"raise:{r['sku']}", title, detail,
                         f"Put {it['name']}'s compare-at back to {money(r['old']) if r['old'] else 'blank'}, or keep a "
                         f"record that {money(r['new'])} was a real former price; have whoever owns pricing compliance "
                         f"review it before launch."))
    covered = {r["sku"] for k in ("raises", "ok", "short") for r in ref[k] if r["new"] == cat[r["sku"]]["compare_at"]}
    for sku in sorted(history["rows"]):
        it, rows = cat[sku], history["rows"][sku]
        c = it["compare_at"]
        if not c or c <= it["price"] or sku in covered or any(r[1] >= c for r in rows):
            continue
        ref["never"].append(sku)
        prices = sorted({r[1] for r in rows})
        F.append(finding(
            "WARNING", "reference", f"never:{sku}",
            f"{it['name']}: compare-at {money(c)} never appears as a selling price in its price history (from "
            f"{fmt_wday(rows[0][0], year)}); it sold at {join_and(money(x) for x in prices)}. Reference-price risk to "
            f"review, not a legal conclusion.",
            [f"Today the product page shows {money(c)} crossed out next to {money(it['price'])} "
             f"({pct((c - it['price']) / c)} off).",
             "The FTC's Guides Against Deceptive Pricing (16 CFR 233.1) treat a former price as genuine when the item "
             "was openly and actively offered at it for a reasonably substantial period in the recent, regular course "
             "of business. If it sold at this price before the history starts, keep a record of that."],
            f"Clear {it['name']}'s compare-at or set it to a price it actually sold at, or keep a record that "
            f"{money(c)} was a real former price; have whoever owns pricing compliance review it before launch."))


# --------------------------------------------------------------------------- test checkouts
def build_tests(A: dict) -> list:
    """Carts that show, at checkout, whether each problem is fixed: expected total vs the total with the bug."""
    tz, cat, now, plan_set, plan_of = A["tz"], A["cat"], A["now"], A["plan_set"], A["plan_of"]
    rows = []

    def segs_for(discs):
        return sorted({d["elig"]["names"][0] for d in discs if d["elig"]["type"] == "segments" and d["elig"]["names"]})

    def at(discs):
        t = max([now] + [d["starts"] for d in discs if d["starts"] is not None])
        return t + dt.timedelta(minutes=5) if t > now else t

    def patched(changes):
        cfg = [dict(d) for d in plan_set]
        for key, fields in changes:
            idx = next((i for i, d in enumerate(cfg) if d["key"] == key), None)
            if idx is None:
                cfg.append(fields)
            else:
                cfg[idx] = dict(cfg[idx], **fields)
        return cfg

    def add(kind, t, lines, codes, discs, bug_cfg, meaning, f=None, ship=False):
        if any(d["type"] == "bxgy" for d in discs) or (f is not None and any(
                d["type"] == "bxgy" for d in f.get("used", []))):
            return
        segs = segs_for(discs)
        exp = simulate(plan_set, cat, lines, t, set(segs), codes, tz)
        bug = simulate(bug_cfg, cat, lines, t, set(segs), codes, tz)
        same = exp["total"] == bug["total"] and exp["refused"] == bug["refused"]
        if same and (not ship or exp["free_ship"] == bug["free_ship"]):
            return
        rows.append({"kind": kind, "t": t, "lines": lines, "codes": codes, "segs": segs, "exp": exp, "bug": bug,
                     "meaning": meaning, "ship": ship})
        if f is not None and f["test"] is None:
            f["test"] = len(rows)

    worst = A["below"] or A["thin"]
    if worst:
        p = worst[0]
        r = A["worst_live"][p["sku"]]
        cols = [c for c in p["collections"] if norm_key(c) in ("last-chance", "new-arrivals", "clearance")]
        add("worst", r["from"] + dt.timedelta(minutes=5) if r["from"] > now else now, [(p["sku"], 1)],
            [d["codes"][0] for d in r["used"] if d["method"] == "code"], r["used"], A["live_set"],
            "as exported, " + join_and(d["label"] for d in r["used"]) + f" both apply to this "
            f"{cols[0] + ' ' if cols else ''}item: {money(p['cost'] - r['final'])} below its {money(p['cost'])} unit cost"
            if r["final"] < p["cost"] else f"as exported, it lands under the {pct(A['floor'])} margin floor")
    for f in A["findings"]:
        k = f["key"]
        if f["severity"] not in ("CRITICAL", "WARNING"):
            continue
        if k.startswith("target:"):
            pd, ld, o = f["pair"]["plan"], f["pair"]["live"], f["pair"]["offer"]
            ck = {norm_key(c) for c in o["excl"]["exclude_collections"]}
            pick, seen = [], set()
            for s in sorted(f["extra"], key=lambda s: (-cat[s]["price"], s)):
                grp = min(cat[s]["coll_keys"] & ck, default="other")
                if grp not in seen:
                    seen.add(grp)
                    pick.append((s, 1))
            add("target", at([pd]), pick, ld["codes"][:1], [pd], patched([(pd["key"], {"skus": ld["skus"]})]),
                f"{ld['label']} reaches items the plan leaves out", f)
        elif k.startswith("stack:"):
            used = f["used"]
            if not all(d["key"] in plan_of for d in used):
                continue
            pds = [plan_of[d["key"]]["plan"] for d in used]
            prods = [set(x["skus"]) for x in pds if x["cls"] == "product"]
            pool = set.intersection(*prods) if prods else {p["sku"] for p in A["catalog"] if not p["gift_card"]}
            if not pool:
                continue
            s = min(pool, key=lambda s: (-cat[s]["price"], s))
            add("stack", at(pds), [(s, 1)], [x["codes"][0] for x in pds if x["method"] == "code"], pds,
                patched([(x["key"], {"combines": dict(d["combines"])}) for x, d in zip(pds, used)]),
                f"{join_and(d['label'] for d in used)} stack", f)
        elif k.startswith("settings:"):
            pd, ld = f["pair"]["plan"], f["pair"]["live"]
            codes = ld["codes"][:1] if ld["method"] == "code" else []
            for x, _ in A["attach"].get(ld["key"], []):
                xp = plan_of[x["key"]]["plan"]
                cfg = patched([(pd["key"], {"combines": dict(ld["combines"])}), (xp["key"], {"combines": dict(x["combines"])})])
                t, best = at([xp, pd]), None
                segs = set(segs_for([pd, xp]))
                for a in sorted(xp["skus"]):
                    for b in sorted(pd["skus"] - xp["skus"]):
                        lines = [(a, 1), (b, 1)]
                        diff = simulate(plan_set, cat, lines, t, segs, codes, tz)["total"] - \
                            simulate(cfg, cat, lines, t, segs, codes, tz)["total"]
                        cand = (diff, -(cat[a]["price"] + cat[b]["price"]), a, b)
                        if diff > 0 and (best is None or cand > best):
                            best = cand
                if best:
                    add("combo", t, [(best[2], 1), (best[3], 1)], codes, [pd, xp], cfg,
                        f"{ld['label']} combines with {x['label']} (as planned they can't, so Shopify applies only "
                        f"the better of the two)", f)
            if any(dv[1] == "once" for dv in f["devs"]) and codes:
                rows.append({"kind": "once", "t": now, "code": codes[0], "segs": segs_for([pd]),
                             "meaning": f"{ld['label']} isn't limited to one use per customer"})
                f["test"] = f["test"] or len(rows)
        elif k.startswith("window:"):
            pd, ld = f["pair"]["plan"], f["pair"]["live"]
            pe, le = eff_end(pd, tz), eff_end(ld, tz)
            if ld["ends"] is not None and pd["ends"] is not None and le < pe:
                t, want = le + min(dt.timedelta(minutes=30), (pe - le) / 2), "end"
            elif pd["starts"] is not None and ld["starts"] > pd["starts"]:
                t, want = pd["starts"] + min(dt.timedelta(minutes=30), (ld["starts"] - pd["starts"]) / 2), "start"
            elif pd["starts"] is not None and ld["starts"] < pd["starts"]:
                t, want = max(now, ld["starts"]) + dt.timedelta(minutes=5), "start"
            elif pd["ends"] is not None and le > pe:
                t, want = pe + dt.timedelta(minutes=30), "end"
            else:
                continue
            pool = pd["skus"] or {p["sku"] for p in A["catalog"] if not p["gift_card"]}
            ends = [i for i in A["win"].get(f["pair"]["offer"]["index"], []) if i.get("live") and i["kind"] == want]
            add("window", t, [(min(pool, key=lambda s: (-cat[s]["price"], s)), 1)], pd["codes"][:1], [pd],
                patched([(pd["key"], {"starts": ld["starts"], "ends": ld["ends"]})]),
                f"{ld['label']} {ends[0]['kind']}s at {fmt_clock(ends[0]['live'])} {tz.label}" if ends else
                f"{ld['label']}'s dates are off", f)
        elif k.startswith("shipmin:"):
            pd, ld = f["pair"]["plan"], f["pair"]["live"]
            under = [p for p in A["catalog"] if not p["gift_card"] and p["price"] < (pd["min_subtotal"] or ZERO)]
            if under:
                s = min(under, key=lambda p: (-p["price"], p["sku"]))["sku"]
                add("ship", now, [(s, 1)], [], [], patched([(pd["key"], {"min_subtotal": ld["min_subtotal"],
                                                                        "min_qty": ld["min_qty"]})]),
                    f"{ld['label']} has no minimum" if not ld["min_subtotal"] else f"{ld['label']}'s minimum is too low",
                    f, ship=True)
        elif k.startswith(("mustnot:", "unplanned:")):
            d = f["disc"]
            if d["method"] != "code" or not d["modeled"] or d["cls"] == "shipping":
                continue
            mins = [x["min_subtotal"] or ZERO for x in plan_set if x["cls"] == "shipping"]
            pool = sorted((p for p in A["catalog"] if p["sku"] in d["skus"]), key=lambda p: (-p["price"], p["sku"]))
            pool = [p for p in pool if not mins or p["price"] < min(mins)] or pool
            if pool:
                add("code", max(now, d["starts"]), [(pool[0]["sku"], 1)], d["codes"][:1], [d], plan_set + [d],
                    f"{d['label']} is still live", f)
    return rows


def build_fixes(A: dict) -> list:
    dates, as_of = A["dates"], A["as_of"]

    def by(d: dt.date) -> str:
        return fmt_day(d) if d >= as_of else f"now ({fmt_day(d)} has passed)"

    fixes = [{"severity": f["severity"], "text": f["fix"], "state": "recommended", "key": f["key"],
              "by": f"{fmt_day(as_of)} (today)" if f["live_now"] else by(dates["fix_by"])}
             for f in A["findings"] if f["severity"] in ("CRITICAL", "WARNING") and f["fix"]]
    refs = [str(i) for i, x in enumerate(fixes, 1) if x["key"].startswith(("target:", "stack:"))]
    for x in fixes:
        if refs and "the targeting and stacking fixes" in x["text"]:
            x["text"] = x["text"].replace("the targeting and stacking fixes", f"fix{'es' if len(refs) > 1 else ''} "
                                          f"{join_and(refs)}")
    tz = A["tz"]
    starts = join_and(f"{fmt_clock(tz.to_local(o['disc']['starts']) + dt.timedelta(minutes=5))} "
                      f"{fmt_wday(tz.to_local(o['disc']['starts']).date())}" for o in A["phases"])
    end = last_day(max(eff_end(o["disc"], tz) for o in A["phases"]), tz) if A["phases"] else None
    fixes += [
        {"severity": "PLAN", "state": "recommended", "key": "recheck", "by": by(dates["recheck"]),
         "text": "Re-run this check on a fresh export; it should say READY." + (
             " Then run the test checkouts in §7 and mark a fix verified only when its row shows the expected total."
             if A["tests"] else "")},
        {"severity": "PLAN", "state": "recommended", "key": "freeze", "by": by(dates["freeze"]),
         "text": "Discount freeze" + (f" through {fmt_day(end)}" if end else "") + ": no new or edited discounts "
                 "unless you re-run this check after the change."},
        {"severity": "PLAN", "state": "recommended", "key": "final", "by": by(dates["final"]),
         "text": "Final export and run." + (f" At {starts} {tz.label}, place one order (or test order) in each "
                                            f"phase and read the discount lines." if starts else "")}]
    return fixes


# --------------------------------------------------------------------------- render
def render(A: dict) -> str:
    tz, year, cat, lab = A["tz"], A["year"], A["cat"], A["tz"].label
    F = A["findings"]
    launch = A["launch"]
    days = (launch - A["as_of"]).days
    away = f"{plural(days, 'day')} away" if days > 0 else "today" if days == 0 else f"{plural(-days, 'day')} ago"
    counts = [plural(A[k], w) for k, w in (("critical", "critical issue"), ("warnings", "warning")) if A[k]]
    ph = A["phases"]
    head = f"**Verdict: {A['verdict']}.** " + (" and ".join(counts) + ". " if counts else
                                                "No configuration issues found. ")
    head += (f"{ph[0]['phase']} opens {fmt_wday(launch)}, {launch.year} ({away})" if ph else
             f"Launch is {fmt_wday(launch)}, {launch.year} ({away})")
    head += "".join(f"; {o['phase']} runs {fmt_wday(local_date(o['disc']['starts'], tz))} – "
                    f"{fmt_wday(last_day(eff_end(o['disc'], tz), tz))}" for o in ph[1:]) + "."
    L = [f"## BFCM Offer & Discount Config QA: {A['store']}, as of {A['as_of'].isoformat()}", "", head]
    if A["verdict"] != "READY":
        n = sum(f["live_now"] for f in F if f["severity"] in ("CRITICAL", "WARNING"))
        due = lambda d: f"by **{fmt_day(d)}**" if d >= A["as_of"] else f"**now** ({fmt_day(d)} has passed)"  # noqa
        L.append(f"Fix the items below {due(A['dates']['fix_by'])}, then re-run this check and the test checkouts "
                 f"{due(A['dates']['recheck'])}."
                 + (f" {WORDS[n] if n < len(WORDS) else n} {'is' if n == 1 else 'are'} live today: fix "
                    f"{'it' if n == 1 else 'those'} now." if n else ""))
    exp = A["exported"] or A["now"]
    L.append(f"Checked {plural(len(A['live']), 'discount')} (export of {fmt_local(tz.to_local(exp), year)} {lab}) "
             f"against {plural(len(A['offers']), 'planned offer')} and {plural(len(A['catalog']), 'SKU')}. Times are "
             f"store time, {tz.name} ({lab}).")
    old = (A["as_of"] - local_date(exp, tz)).days
    if old > 0:
        L.append(f"The export is {plural(old, 'day')} older than the report date: statuses and usage counts may have "
                 f"changed, so re-export before acting.")
    if A["not_checked"]:
        L.append("Not checked: " + "; ".join(A["not_checked"]) + ".")
    L.append("")
    ranked = [f for f in F if f["severity"] in ("CRITICAL", "WARNING")]
    if ranked:
        L.append("**Issues, most severe first** (all detected, none changed)")
        L += [f"{i}. **{f['severity']}**: {f['title']}" for i, f in enumerate(ranked, 1)]
        L.append("")

    def findings_of(sec, empty=None):
        out = []
        for f in F:
            if f["section"] == sec:
                out.append(f"- **{f['severity']}**: {f['title']}")
                out += [f"  {d}" for d in f["detail"]]
        return out or ([empty] if empty else [])

    L += ["### 1. Planned offers vs live discounts", "", "| Planned offer | Live discount | Status | Matches the plan? |",
          "| --- | --- | --- | --- |"]
    for p in A["pairs"]:
        o, pd, ld = p["offer"], p["plan"], p["live"]
        what = f"{o['name']}: {'code ' + o['code'] if o['method'] == 'code' else 'automatic'}, {describe(pd)}"
        if ld is None:
            L.append(f"| {what} | **not found** | — | **No** |")
            continue
        i = o["index"]
        probs = [t for t, hit in (
            ("dates (§2)", i in A["win"]),
            ("who gets it (§3)", any(f["key"] in (f"target:{i}", f"untargeted:{i}") for f in F)),
            ("stacking (§4)", any(ld in f.get("used", []) for f in F)),
            ("settings (§5)", any(f["key"] in (f"settings:{i}", f"shipmin:{i}") for f in F)
             or any(ld is x for v in A["attach"].values() for x, _ in v))) if hit]
        st = ld["status"].lower() + (f", {plural(ld['used'], 'use')}" if ld["used"] else "")
        L.append(f"| {what} | {ld['label']} | {st} | " + ("**No**: " + ", ".join(probs) if probs else "yes") + " |")
    L.append("")
    sec = findings_of("plan")
    sec += [f"- Must not be active: {x} isn't in the export (good)." for x in A["must_missing"]]
    if A["ignored"]:
        sec.append("- Ignored (expired): " + ", ".join(d["label"] for d in A["ignored"]) + ".")
    L += sec + ([""] if sec else [])

    L += [f"### 2. Sale windows in store time ({lab})", ""]
    if ph:
        L += [f"| Phase | Discount | Starts ({lab}) | Ends ({lab}) | vs plan |", "| --- | --- | --- | --- | --- |"]
        for o in ph:
            p = next(x for x in A["pairs"] if x["offer"] is o)
            ld = p["live"]
            if ld is None:
                L.append(f"| {o['phase']} | {p['plan']['label']} | — | — | **missing** |")
                continue
            cells = []
            for i in A["win"].get(o["index"], []):
                if i.get("live"):
                    cells.append(f"**{i['kind']}s {fmt_dur(i['delta'])} {'early' if i['early'] else 'late'}**: plan "
                                 f"{fmt_clock(i['plan'])}" + (f", set as {i['why']}" if i["why"] else ""))
                elif i["kind"] in ("noend", "late", "end"):
                    cells.append(f"**{i['text']}**")
            L.append(f"| {o['phase']} | {ld['label']} | {fmt_local(tz.to_local(ld['starts']), year)} | "
                     f"{fmt_local(tz.to_local(ld['ends']), year) if ld['ends'] else 'no end'} | "
                     f"{'; '.join(cells) or 'matches'} |")
            c = next((c for c in A["chain"] if c["a"]["offer"] is o and c.get("kind")), None)
            if c:
                g0, g1 = sorted((tz.to_local(eff_end(c["a"]["live"], tz)), tz.to_local(start_of(c["b"]["live"]))))
                if c["kind"] == "overlap" and c["a"]["live"]["ends"] is None:
                    L.append(f"| — | *both live* | {fmt_local(g0, year)} | no end date | **overlap: "
                             f"{c['a']['live']['label']} has no end date** (plan: none) |")
                else:
                    L.append(f"| — | *{'no sale discount' if c['kind'] == 'gap' else 'both live'}* | {fmt_local(g0, year)} | "
                             f"{fmt_local(g1, year)} | **{fmt_dur(abs(c['gap']))} {c['kind']}** (plan: none) |")
        L.append("")
    others = []
    phase_live = {next(x for x in A["pairs"] if x["offer"] is o)["live"]["key"] for o in ph
                  if next(x for x in A["pairs"] if x["offer"] is o)["live"] is not None}
    for d in sorted(A["live_set"], key=lambda d: (start_of(d), d["label"])):
        if d["key"] in phase_live:
            continue
        p = A["plan_of"].get(d["key"])
        if d["ends"] is None:
            win = f"since {fmt_wday(local_date(d['starts'], tz), year)}, no end"
        else:
            win = fmt_span(local_date(d["starts"], tz), last_day(eff_end(d, tz), tz))
        if p is None:
            note = "must not be active" if d in A["must_hits"] else "not in the plan"
        elif p["plan"]["starts"] is None and p["plan"]["ends"] is None:
            note = "plan: always on"
        else:
            note = "differs from the plan" if p["offer"]["index"] in A["win"] else "matches the plan"
        others.append(f"{d['label']} ({win}; {note})")
    if others:
        L += ["Always on or longer-running: " + "; ".join(others) + ".", ""]
    L += findings_of("windows", "- Every phase starts and ends when the plan says, with no gaps or overlaps."
                     if ph else None) + [""]

    L += ["### 3. Who gets each discount", ""]
    if A["elig_rows"]:
        L += ["| Discount | Applies to | SKUs | Plan | Extra (shouldn't get it) | Missing |",
              "| --- | --- | ---: | --- | --- | --- |"]
        for r in A["elig_rows"]:
            o, ld, pd = r["pair"]["offer"], r["pair"]["live"], r["pair"]["plan"]
            pl = "everything" if o["target"]["type"] == "all" else target_text(o["target"])
            ex = o["excl"]["exclude_collections"] + o["excl"]["exclude_products"] + o["excl"]["exclude_skus"]
            pl += (" except " + join_and(ex) if ex else "") + f" ({len(pd['skus'])})"
            extra = "—"
            if r["extra"]:
                ck = {norm_key(c): c for c in o["excl"]["exclude_collections"]}
                cnt = {}
                for s in r["extra"]:
                    g = min((ck[k] for k in cat[s]["coll_keys"] & set(ck)), default="Other")
                    cnt[g] = cnt.get(g, 0) + 1
                extra = f"**{len(r['extra'])}**: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items()))
            L.append(f"| {ld['label']} | {target_text(ld['target'])} | {len(ld['skus'])} | {pl} | {extra} | "
                     f"{'**' + str(len(r['missing'])) + '**' if r['missing'] else '—'} |")
        L.append("")
    notes = []
    whole_cart = [d["label"] for d in A["live_set"] if d["cls"] in ("order", "shipping")]
    if whole_cart:
        notes.append(f"Order and shipping discounts ({join_and(whole_cart)}) apply to the whole cart.")
    gifts = [p for p in A["catalog"] if p["gift_card"]]
    if gifts:
        notes.append(f"Gift cards ({plural(len(gifts), 'SKU')}) are only discounted by a discount that names the gift "
                     f"card product, never by All products or a collection (Shopify rule).")
    if notes:
        L += [" ".join(notes), ""]
    L += findings_of("eligibility", "- Every product discount reaches exactly the SKUs the plan intends.") + [""]

    L += ["### 4. Stacking and worst-case price per SKU", ""]
    if A["pair_rows"]:
        L += ["Pairs that can combine (each discount must allow the other's class):", "",
              f"| Pair | Both live ({lab}) | How they combine | Plan | Worst case on one item |",
              "| --- | --- | --- | --- | --- |"]
        for r in A["pair_rows"]:
            span = span_text(A, r["from"], r["to"])
            w = "—" if r["worst"] is None else pct(r["worst"])
            w = f"**{w}**" if r["worst"] is not None and r["worst"] > A["max"] + TOL else w
            L.append(f"| {r['a']['label']} + {r['b']['label']} | {span} | {r['how']} | "
                     f"{r['plan'] if r['allowed'] else '**' + r['plan'] + '**'} | {w} |")
        L.append("")
    else:
        L += ["No two live discounts can combine.", ""]
    sec = findings_of("stacking")
    L += sec + ([""] if sec else [])
    wl, wp = A["worst_live"], A["worst_plan"]
    items = sorted((p for p in A["catalog"] if not p["gift_card"] or wl[p["sku"]]["used"] or wp[p["sku"]]["used"]),
                   key=lambda p: (wl[p["sku"]]["margin"] if wl[p["sku"]]["margin"] is not None else HUNDRED, p["name"]))
    L += [f"Worst case per SKU, {fmt_day(A['as_of'])} – {fmt_day(last_day(A['horizon'] - dt.timedelta(days=1), tz))} "
          f"(plan max {pct(A['max'])}, margin floor {pct(A['floor'])}):", "",
          "| Item | Price | Unit cost | Planned worst case | Live worst case | Live via | When | Margin |",
          "| --- | ---: | ---: | --- | --- | --- | --- | ---: |"]
    for p in items:
        r, rp = wl[p["sku"]], wp[p["sku"]]
        lw = f"{money(r['final'])} ({pct(r['depth'])})"
        bad = r["depth"] > A["max"] + TOL or (p["cost"] is not None and r["final"] < p["cost"])
        when = span_text(A, r["from"], r["to"]) if r["used"] else "—"
        mg = "—" if r["margin"] is None else pct(r["margin"], keep_one=True)
        tag = [c for c in p["collections"] if norm_key(c) in ("last-chance", "new-arrivals", "clearance")]
        L.append(f"| {p['name']}{' (' + tag[0] + ')' if tag else ''} | {money(p['price'])} | "
                 f"{money(p['cost']) if p['cost'] is not None else '—'} | {money(rp['final'])} ({pct(rp['depth'])}) | "
                 f"{'**' + lw + '**' if bad else lw} | {' + '.join(d['label'] for d in r['used']) or '—'} | {when} | "
                 f"{'**' + mg + '**' if r['margin'] is not None and r['margin'] < A['floor'] else mg} |")
    L += ["", "_Model (Shopify Help Center, Combining discounts): two discounts combine only if each allows the other's "
              "class; product discounts apply first, then order discounts on the reduced subtotal (each percentage on "
              "that subtotal), then one shipping discount; one product discount per item unless both are tagged to "
              "share a line (Shopify Plus); if discounts can't combine, the customer gets the best one. The worst case "
              "assumes minimums are met and ignores who is eligible. Margin is on the discounted price, before shipping "
              "and payment fees._", ""]

    L += ["### 5. Codes, limits and free shipping", ""]
    codes = sorted((d for d in A["live_set"] if d["method"] == "code"), key=lambda d: d["label"])
    if codes:
        L += ["| Code | Offer | Uses | Usage limit (plan) | Once per customer (plan) | Who can use it | Combines with |",
              "| --- | --- | ---: | --- | --- | --- | --- |"]
        for d in codes:
            p = A["plan_of"].get(d["key"])
            ul = "none" if d["usage_limit"] is None else f"{d['usage_limit']:,}"
            once = "yes" if d["once"] else "no"
            if p is None:
                ul += " (must not be active)" if d in A["must_hits"] else " (not in plan)"
                cw = classes_text(d)
            else:
                pd = p["plan"]
                lim = pd["usage_limit"]
                ul = (f"**{ul}**" if lim is not None and (d["usage_limit"] or lim + 1) > lim else ul) + \
                    f" ({'none' if lim is None else f'{lim:,}'})"
                once = (f"**{once}**" if pd["once"] and not d["once"] else once) + f" ({'yes' if pd['once'] else 'no'})"
                cw = classes_text(d, pd)
            L.append(f"| {d['label']} | {describe(d)} | {d['used']:,} | {ul} | {once} | {elig_text(d['elig'])} | {cw} |")
        L.append("")
    ships = []
    for d in A["live_set"]:
        if d["cls"] == "shipping":
            p = A["plan_of"].get(d["key"])
            lm = d["min_subtotal"] or ZERO
            pm = p["plan"]["min_subtotal"] or ZERO if p else None
            ships.append(f"{d['label']} ({d['method']}): minimum {money_s(lm)}"
                         + ("" if p is None else " (matches the plan)" if pm == lm else f" (plan {money_s(pm)})"))
    if ships:
        L += ["Shipping discounts: " + "; ".join(ships) + ".", ""]
    L += findings_of("codes", "- Usage limits, once-per-customer settings and free-shipping minimums match the plan.")
    L.append("")

    L += ["### 6. Reference prices (compare-at)", ""]
    ref = A["ref"]
    below = plural(len(ref["below"]), "SKU") if ref["below"] else "none"
    if ref["history"] is None:
        L.append(f"Compare-at below price: {below}. Skipped: no price history (--price-history), so compare-at "
                 f"raises were not checked.")
    else:
        h = ref["history"]
        L.append(f"Compare-at below price: {below}. Compare-at prices checked against price history (raises, and "
                 f"compare-ats never seen as a selling price) for {plural(len(h['rows']), 'SKU')} from "
                 f"{fmt_day(h['from'])}"
                 + (f" ({plural(h['ignored'], 'row')} for unknown SKUs ignored)" if h["ignored"] else "") + ".")
    L.append("This check follows US (FTC 16 CFR 233.1) guidance. It does not apply the EU/UK rule that the reference "
             "price must be the lowest price in the prior 30 days.")
    L.append("")
    sec = findings_of("reference")
    if ref["ok"]:
        groups = {}
        for r in ref["ok"]:
            groups.setdefault(r["date"], []).append(cat[r["sku"]]["name"])
        sec.append(f"- OK: {plural(len(ref['ok']), 'compare-at')} set to a price the SKU had sold at for "
                   f"{REFERENCE_MIN_DAYS}+ days (markdowns on " + "; ".join(
                       f"{fmt_wday(d, year)}: {', '.join(sorted(v))}" for d, v in sorted(groups.items())) + ").")
    sec += [f"- INFO: {cat[r['sku']]['name']}: compare-at raised to {money(r['new'])} on {fmt_wday(r['date'], year)}, "
            f"with only {r['known']} days of history before it, so it can't be judged." for r in ref["short"]]
    if not sec and ref["history"] is not None:
        sec = ["- No compare-at raises in the price history, and every compare-at was seen as a selling price."]
    L += sec + ([""] if sec else [])

    L += ["### 7. Test-checkout script", ""]
    if A["tests"]:
        L += ["Run these before launch. A scheduled discount can't be applied before it starts, so for sale-phase rows "
              "use a staff-only copy of each discount (same settings, eligibility limited to your test customer, "
              "starting now; delete it after) or test in the first minutes of the phase. Totals are merchandise after "
              "discounts, before shipping and tax; Shopify may round a cent differently. "
              + ("Row 1 is the worst case as exported; each other row isolates one problem and assumes the other fixes "
                 "are in." if A["tests"][0]["kind"] == "worst" else "Each row isolates one problem and assumes the "
                                                                     "other fixes are in."), "",
              f"| # | When ({lab}) | Customer | Cart | Code | Expect (as planned) | If you see | It means |",
              "| ---: | --- | --- | --- | --- | --- | --- | --- |"]
        for i, r in enumerate(A["tests"], 1):
            cust = "any" if not r["segs"] else "segment " + join_and(r["segs"])
            if r["kind"] == "once":
                L.append(f"| {i} | today | test customer | any item, then a second order with the same email | "
                         f"{r['code']} | second order: code refused | code accepted again | {r['meaning']} |")
                continue
            when = "today" if r["t"] == A["now"] else fmt_local(tz.to_local(r["t"]), year)
            cart = " + ".join(f"{cat[s]['name']} ({money(cat[s]['price'])})" for s, _ in r["lines"])
            L.append(f"| {i} | {when} | {cust} | {cart} | {', '.join(r['codes']) or '—'} | "
                     f"{outcome(r['exp'], r['ship'])} | {outcome(r['bug'], r['ship'])} | {r['meaning']} |")
        L.append("")
    else:
        L += ["No test carts needed: the live config behaves as planned in every check above. Still place one test "
              "order in each phase when it starts.", ""]

    L += ["### 8. Fix list and timeline (nothing has been changed)", "", "| # | Severity | What to do | State | By |",
          "| ---: | --- | --- | --- | --- |"]
    L += [f"| {i} | {f['severity']} | {f['text']} | {f['state']} | {f['by']} |" for i, f in enumerate(A["fixes"], 1)]
    L += ["", f"_CRITICAL can lose margin or sell below cost; WARNING breaks the plan or the shopper's experience. Fixes "
              f"are due {FIX_LEAD} days before launch ({fmt_day(A['dates']['fix_by'])}); items live today, now. "
              f"Free-shipping costs are estimates. Reference-price notes are risks to review, not legal conclusions. "
              f"States: detected → recommended → applied → verified. Read-only: no discounts, prices or Shopify "
              f"settings were changed._"]
    return "\n".join(L) + "\n"


def outcome(r: dict, ship: bool) -> str:
    applied = r["applied"] + (r["applied_ship"] if ship else [])
    bits = [" + ".join(applied)] if applied else []
    bits += [f"{', '.join(r['refused'])} refused"] if r["refused"] else []
    s = f"{money(r['total'])} ({'; '.join(bits) if bits else 'no discount'})"
    return s + ((", free shipping" if r["free_ship"] else ", shipping charged") if ship else "")


# --------------------------------------------------------------------------- JSON
def to_jsonable(x):
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [to_jsonable(v) for v in (sorted(x) if isinstance(x, set) else x)]
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    return x


def public_json(A: dict) -> dict:
    tz = A["tz"]
    worst = [{"sku": p["sku"], "name": p["name"], "price": p["price"], "unit_cost": p["cost"],
              "live_worst_price": A["worst_live"][p["sku"]]["final"], "live_worst_depth": A["worst_live"][p["sku"]]["depth"],
              "live_via": [d["label"] for d in A["worst_live"][p["sku"]]["used"]],
              "live_margin": A["worst_live"][p["sku"]]["margin"],
              "planned_worst_price": A["worst_plan"][p["sku"]]["final"],
              "planned_worst_depth": A["worst_plan"][p["sku"]]["depth"]} for p in A["catalog"]]
    tests = [{"kind": r["kind"], "code": r["code"], "meaning": r["meaning"]} if r["kind"] == "once" else
             {"kind": r["kind"], "when": tz.to_local(r["t"]).isoformat(), "segments": r["segs"], "codes": r["codes"],
              "cart": [{"sku": s, "qty": q} for s, q in r["lines"]], "expect_total": r["exp"]["total"],
              "expect_free_shipping": r["exp"]["free_ship"], "bug_total": r["bug"]["total"],
              "bug_free_shipping": r["bug"]["free_ship"], "meaning": r["meaning"]} for r in A["tests"]]
    return to_jsonable({"store": A["store"], "as_of": A["as_of"], "launch": A["launch"], "verdict": A["verdict"],
                        "critical": A["critical"], "warnings": A["warnings"], "max_item_discount": A["max"],
                        "findings": [{k: f[k] for k in ("severity", "section", "key", "title", "detail", "fix", "state")}
                                     for f in A["findings"]],
                        "worst_case": worst, "tests": tests, "fixes": A["fixes"], "not_checked": A["not_checked"]})


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", required=True, help="offer_plan.json: the offers the team intends to run")
    ap.add_argument("--discounts", required=True, help="discounts.json: normalized live Shopify discounts")
    ap.add_argument("--products", required=True, help="products.csv: one row per variant with price and cost")
    ap.add_argument("--price-history", help="optional price_history.csv for compare-at checks")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: the export time in discounts.json)")
    ap.add_argument("--margin-floor", help="gross-margin floor in percent (default: the plan's margin_floor_pct, else 0)")
    ap.add_argument("--json", help="also write the structured analysis to this path")
    args = ap.parse_args(argv)
    try:
        as_of = None
        if args.as_of:
            try:
                as_of = dt.date.fromisoformat(args.as_of)
            except ValueError:
                raise InputError("--as-of must be YYYY-MM-DD")
        floor = dec(args.margin_floor, "--margin-floor", allow_none=True, minimum=None)
        catalog = load_products(args.products)
        plan = load_plan(args.plan, catalog)
        live, exported = load_discounts(args.discounts, catalog)
        A = analyze(plan, catalog, live, exported, load_history(args.price_history, catalog), as_of, floor)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(A))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(public_json(A), f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
