#!/usr/bin/env python3
"""
Holiday Shipping Cutoff Checker: analyzer.

Reads local files (see DATA_CONTRACT.md) and prints a Markdown report: the last
day a customer can order on each shipping service and still get the order by
the holiday, the BFCM fulfillment backlog, every delivery promise on the site,
at checkout and in email or SMS checked against those dates, a messaging
calendar and a dated fix list.

Local files only. Never calls an API, never changes anything.

    python3 scripts/check_cutoffs.py --carriers carriers.csv --warehouse warehouse.json \
        --destinations destinations.csv [--demand demand.csv [--aov N]] [--promises promises.csv] \
        [--account NAME] [--target YYYY-MM-DD] [--threshold 95] [--as-of YYYY-MM-DD] [--json out.json]

Python 3.8+, standard library only. Output is deterministic for a given input and --as-of.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import os
import re
import sys
from fractions import Fraction

DEFAULT_THRESHOLD = "95"    # % of customers (weighted by zone mix) who must get the order by the target date
CONTEXT_THRESHOLDS = (80, 99)
CONSERVATIVE_DAYS = 3       # a stated order-by date this many days or more before the last safe date: INFO
MESSAGE_LIVE_DAYS = 7       # an email/SMS shipping-time claim covers orders placed in the 7 days from its send date
CUTOFF_LEAD_DAYS = 7        # start holiday cutoff messaging this many days before the first cutoff
BACKLOG_WARN_DAYS = 3       # a backlog that adds this many business days (or more) is itself a WARNING
SEARCH_DAYS = 60            # last safe dates are searched from at least this many days before the target
ZONES = tuple(range(1, 10))      # USPS zones 1-9 (zone 9: US territories); zone_9 is optional
REQUIRED_ZONES = tuple(range(1, 9))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAY_INDEX = {w.lower(): i for i, w in enumerate(WEEKDAYS)}
TZ_LABELS = {
    "America/New_York": "ET", "America/Detroit": "ET", "America/Toronto": "ET",
    "America/Indiana/Indianapolis": "ET", "US/Eastern": "ET", "America/Chicago": "CT", "US/Central": "CT",
    "America/Denver": "MT", "America/Boise": "MT", "US/Mountain": "MT", "America/Phoenix": "MST",
    "America/Los_Angeles": "PT", "US/Pacific": "PT", "America/Anchorage": "AKT", "Pacific/Honolulu": "HT",
}
# Standard-time UTC offsets (hours) for the zone labels above; holiday cutoffs fall in standard time.
TZ_OFFSETS = {"ET": -5, "CT": -6, "MT": -7, "MST": -7, "PT": -8, "AKT": -9, "HT": -10}
STATED_ZONES = {"et": "ET", "est": "ET", "edt": "ET", "eastern": "ET", "ct": "CT", "cst": "CT", "cdt": "CT",
                "central": "CT", "mt": "MT", "mst": "MT", "mdt": "MT", "mountain": "MT", "pt": "PT", "pst": "PT",
                "pdt": "PT", "pacific": "PT", "akt": "AKT", "akst": "AKT", "akdt": "AKT", "ht": "HT", "hst": "HT"}
STATED_TIME_RE = re.compile(
    r"\b(?:(\d{1,2})(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])|(noon)|(midnight)|([01]?\d|2[0-3]):([0-5]\d))"
    r"(?:\s*\(?(" + "|".join(sorted(STATED_ZONES, key=len, reverse=True)) + r")\b)?", re.I)
SEV_ORDER = {"RISKY": 0, "WARNING": 1, "INFO": 2, "OK": 3, "PLAN": 4}
PART_RANK = {"before": 0, "all": 0, "after": 1}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RE = re.compile(r"^\d+(\.\d+)?$")
TRANSIT_RE = re.compile(r"^(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?$")
GUARANTEE_RE = re.compile(r"guarantee", re.I)
URL_RE = re.compile(r"^https?://", re.I)
ONE = dt.timedelta(days=1)
FTC = ("The FTC's Mail, Internet, or Telephone Order Merchandise Rule (16 CFR Part 435) requires a reasonable "
       "basis for any shipping time you state or imply, and when you can't ship on time, a notice that lets the "
       "customer agree to the delay or cancel for a prompt refund. Not legal advice.")


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- formatting
def fmt_day(d: dt.date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {MONTHS[d.month - 1]} {d.day}"


def fmt_md(d: dt.date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}"


def fmt_copy(d: dt.date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}"


def fmt_range(a: dt.date, b: dt.date, f=fmt_day) -> str:
    return f(a) if a == b else f"{f(a)} – {f(b)}"


def date_runs(dates) -> list:
    runs = []
    for d in sorted(set(dates)):
        if runs and (d - runs[-1][1]).days == 1:
            runs[-1][1] = d
        else:
            runs.append([d, d])
    return runs


def fmt_dates(dates, f=fmt_day) -> str:
    return ", ".join(fmt_range(a, b, f) for a, b in date_runs(dates))


def pct(x: Fraction) -> str:
    if x >= 1:
        return "100%"
    if x <= 0:
        return "0%"
    tenths = min(max(int(x * 1000 + Fraction(1, 2)), 1), 999)
    return f"{tenths // 10}.{tenths % 10}%"


def num(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{float(x):g}"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def join_and(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def time_text(h: int, m: int, short: bool) -> str:
    h12, suffix = (h % 12 or 12), ("am" if h < 12 else "pm")
    return f"{h12} {suffix}" if short and m == 0 else f"{h12}:{m:02d} {suffix}"


def stated_times(text: str, wh: dict) -> list:
    """Every clock time in a promise, as {'text', 'minutes'}: minutes after midnight in the warehouse zone
    (a time with no zone is read as warehouse time), or None when the zones can't be compared."""
    out = []
    for m in STATED_TIME_RE.finditer(text):
        if m.group(1):
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            if not 1 <= h <= 12:
                continue
            h = h % 12 + (12 if m.group(3).lower().startswith("p") else 0)
            mins = h * 60 + mi
        elif m.group(4):
            mins = 12 * 60
        elif m.group(5):
            mins = 24 * 60          # midnight at the end of the stated day
        else:
            mins = int(m.group(6)) * 60 + int(m.group(7))
        zone = STATED_ZONES.get((m.group(8) or "").lower())
        if zone and zone != wh["tz"]:
            if wh["tz"] in TZ_OFFSETS:
                mins += (TZ_OFFSETS[wh["tz"]] - TZ_OFFSETS[zone]) * 60
            else:
                mins = None
        out.append({"text": m.group(0).strip(), "minutes": mins})
    return out


def fmt_weekdays(days) -> str:
    runs = []
    for i in sorted(days):
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return ", ".join(WEEKDAYS[a] if a == b else f"{WEEKDAYS[a]}–{WEEKDAYS[b]}" for a, b in runs)


def fmt_transit(t: tuple) -> str:
    return str(t[0]) if t[0] == t[1] else f"{t[0]}–{t[1]}"


def by_text(d: dt.date | None, as_of: dt.date) -> str:
    if d is None or d == as_of:
        return "now"
    if d < as_of:
        return f"now ({fmt_md(d)} has passed)"
    return fmt_day(d)


# --------------------------------------------------------------------------- calendars
class Calendar:
    """Open days: a set of weekdays minus specific closed dates."""

    def __init__(self, weekdays, closed=None):
        self.weekdays = frozenset(weekdays)
        self.closed = dict(closed or {})

    def is_open(self, d: dt.date) -> bool:
        return d.weekday() in self.weekdays and d not in self.closed

    def next_open(self, d: dt.date) -> dt.date:
        d += ONE
        while not self.is_open(d):
            d += ONE
        return d

    def on_or_after(self, d: dt.date) -> dt.date:
        return d if self.is_open(d) else self.next_open(d)

    def on_or_before(self, d: dt.date) -> dt.date:
        while not self.is_open(d):
            d -= ONE
        return d

    def add(self, d: dt.date, n: int) -> dt.date:
        for _ in range(n):
            d = self.next_open(d)
        return d

    def between(self, a: dt.date, b: dt.date) -> int:
        """Open days w with a < w <= b."""
        n = 0
        while a < b:
            a += ONE
            n += self.is_open(a)
        return n


def thanksgiving(year: int) -> dt.date:
    d = dt.date(year, 11, 1)
    return d + dt.timedelta(days=(3 - d.weekday()) % 7 + 21)


# --------------------------------------------------------------------------- parsing
def read_csv(path: str, label: str) -> tuple:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            cols = [(c or "").strip() for c in (rd.fieldnames or [])]
            rows = []
            for i, r in enumerate(rd, start=2):
                row = {(k or "").strip(): (v if isinstance(v, str) else "").strip() for k, v in r.items() if k is not None}
                if any(row.values()):
                    rows.append((i, row))
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e.strerror or e}")
    except (csv.Error, UnicodeDecodeError) as e:
        raise InputError(f"{label} {path} is not a valid UTF-8 CSV: {e}")
    if not cols:
        raise InputError(f"{label} {path} is empty")
    return cols, rows


def parse_date(s: str, where: str, col: str) -> dt.date:
    s = (s or "").strip()
    if not DATE_RE.match(s):
        raise InputError(f"{where}: '{col}' must be a date YYYY-MM-DD, got {s!r}")
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise InputError(f"{where}: '{col}' is not a real date: {s!r}")


def parse_int(s: str, where: str, col: str, lo: int = 0, hi: int | None = None) -> int:
    t = (s or "").strip().replace(",", "")
    if t == "":
        raise InputError(f"{where}: '{col}' is blank")
    if not re.match(r"^-?\d+$", t):
        raise InputError(f"{where}: '{col}' must be a whole number, got {s!r}")
    v = int(t)
    if v < lo:
        raise InputError(f"{where}: '{col}' must be at least {lo}, got {v}")
    if hi is not None and v > hi:
        raise InputError(f"{where}: '{col}' must be at most {hi}, got {v}")
    return v


def parse_amount(s: str, where: str, col: str) -> Fraction:
    t = (s or "").strip().replace(",", "").replace("$", "").rstrip("%").strip()
    if not NUM_RE.match(t):
        raise InputError(f"{where}: '{col}' must be a number of 0 or more, got {s!r}")
    return Fraction(t)


def parse_weekdays(spec, where: str, col: str) -> frozenset:
    if isinstance(spec, list):
        spec = ";".join(str(x) for x in spec)
    if not isinstance(spec, str):
        raise InputError(f"{where}: '{col}' must be days such as Mon-Fri, got {spec!r}")
    out = set()
    for tok in re.split(r"[;,/\s]+", re.sub(r"\s*[-–]\s*", "-", spec.strip())):
        if not tok:
            continue
        idx = []
        for end in tok.split("-"):
            if not end.isalpha() or end[:3].lower() not in DAY_INDEX:
                raise InputError(f"{where}: '{col}' has an unknown day {tok!r} (use Mon, Tue, ... or a range like Mon-Fri)")
            idx.append(DAY_INDEX[end[:3].lower()])
        if len(idx) > 2:
            raise InputError(f"{where}: '{col}' has a bad range {tok!r}")
        i = idx[0]
        out.add(i)
        while i != idx[-1]:
            i = (i + 1) % 7
            out.add(i)
    if not out:
        raise InputError(f"{where}: '{col}' is empty")
    return frozenset(out)


def parse_date_list(spec: str, where: str, col: str) -> list:
    return [parse_date(t, where, col) for t in re.split(r"[;,\s]+", (spec or "").strip()) if t]


def parse_transit(s: str, where: str, col: str) -> tuple:
    m = TRANSIT_RE.match(s)
    if not m:
        raise InputError(f"{where}: '{col}' must be business days like 3 or 3-5, got {s!r}")
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if hi < lo or hi > 30:
        raise InputError(f"{where}: '{col}' must be a range from low to high, at most 30 days, got {s!r}")
    return lo, hi


# --------------------------------------------------------------------------- loading
def load_carriers(path: str) -> list:
    cols, rows = read_csv(path, "carriers file")
    for c in ("carrier", "service", "source"):
        if c not in cols:
            raise InputError(f"carriers file is missing required column '{c}'")
    req = [f"zone_{z}" for z in REQUIRED_ZONES]
    present = [c for c in req if c in cols]
    if (present or "zone_9" in cols) and len(present) != len(req):
        raise InputError("carriers file has some zone columns but not all of zone_1 ... zone_8")
    zcols = req + (["zone_9"] if "zone_9" in cols else [])
    if not present and "ship_by_date" not in cols:
        raise InputError("carriers file needs transit columns zone_1 ... zone_8, a ship_by_date column, or both")
    services, order = {}, []
    for line, r in rows:
        where = f"{path} line {line}"
        carrier, service, source = r.get("carrier", ""), r.get("service", ""), r.get("source", "")
        if not carrier or not service:
            raise InputError(f"{where}: 'carrier' and 'service' are required")
        if not source:
            raise InputError(f"{where}: 'source' is blank (give the URL the data came from, or 'sample')")
        cells = [r.get(c, "") for c in zcols] if present else []
        transit = {z: (parse_transit(c, where, f"zone_{z}") if c else None)
                   for z, c in zip(ZONES, cells + [""] * (len(ZONES) - len(cells)))} if any(cells) else None
        ship_by = parse_date(r["ship_by_date"], where, "ship_by_date") if r.get("ship_by_date") else None
        if transit is None and ship_by is None:
            raise InputError(f"{where}: {carrier} {service} has no transit days (zone_1 ... zone_8) and no ship_by_date")
        key = (carrier.lower(), service.lower())
        if key not in services:
            services[key] = {"carrier": carrier, "service": service, "name": f"{carrier} {service}", "rate": "",
                             "transit": None, "peak": 0, "days": None, "no_delivery": [], "transit_source": None,
                             "ship_by": None, "ship_by_source": None}
            order.append(key)
        s = services[key]
        rate = r.get("checkout_rate", "")
        if rate:
            if s["rate"] and s["rate"] != rate:
                raise InputError(f"{where}: {s['name']} has two checkout_rate values ({s['rate']!r} and {rate!r})")
            s["rate"] = rate
        if transit is not None:
            if s["transit"] is not None:
                raise InputError(f"{where}: {s['name']} has transit days on more than one row")
            if not r.get("delivery_days"):
                raise InputError(f"{where}: 'delivery_days' is required with transit days (for example Mon-Fri or Mon-Sat)")
            s["transit"], s["transit_source"] = transit, source
            s["days"] = parse_weekdays(r["delivery_days"], where, "delivery_days")
            s["no_delivery"] = sorted(set(parse_date_list(r.get("non_delivery_dates", ""), where, "non_delivery_dates")))
            s["peak"] = parse_int(r.get("peak_extra_days", "") or "0", where, "peak_extra_days", 0, 30)
        if ship_by is not None:
            if s["ship_by"] is not None:
                raise InputError(f"{where}: {s['name']} has a ship_by_date on more than one row")
            s["ship_by"], s["ship_by_source"] = ship_by, source
    if not services:
        raise InputError("carriers file has no rows")
    out = [services[k] for k in order]
    for s in out:
        s["rate"] = s["rate"] or s["name"]
        if s["transit"] is not None:
            s["cal"] = Calendar(s["days"], {d: "" for d in s["no_delivery"]})
    return out


def load_destinations(path: str) -> tuple:
    cols, rows = read_csv(path, "destinations file")
    if "zone" not in cols:
        raise InputError("destinations file is missing required column 'zone'")
    if ("orders" in cols) == ("share_pct" in cols):
        raise InputError("destinations file needs exactly one of the columns 'orders' or 'share_pct'")
    mode = "orders" if "orders" in cols else "share_pct"
    raw = {}
    for line, r in rows:
        where = f"{path} line {line}"
        z = parse_int(r.get("zone", ""), where, "zone", 1, 9)
        if z in raw:
            raise InputError(f"{where}: zone {z} appears twice")
        raw[z] = Fraction(parse_int(r.get(mode, ""), where, mode)) if mode == "orders" \
            else parse_amount(r.get(mode, ""), where, mode)
    total = sum(raw.values(), Fraction(0))
    if total <= 0:
        raise InputError("destinations file has no orders in any zone")
    if mode == "share_pct" and abs(total - 100) > Fraction(1, 2):
        raise InputError(f"destinations file: share_pct adds up to {num(total)}, not 100")
    return {z: raw.get(z, Fraction(0)) / total for z in ZONES}, mode, total


def load_warehouse(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            w = json.load(f)
    except OSError as e:
        raise InputError(f"cannot read warehouse file {path}: {e.strerror or e}")
    except ValueError as e:
        raise InputError(f"warehouse file {path} is not valid JSON: {e}")
    if not isinstance(w, dict):
        raise InputError(f"warehouse file {path} must be a JSON object")
    where = f"warehouse file {path}"

    def whole(v, key, lo, hi=None):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            raise InputError(f"{where}: '{key}' must be a whole number, got {v!r}")
        if v < lo or (hi is not None and v > hi):
            raise InputError(f"{where}: '{key}' must be between {lo} and {hi}, got {v}" if hi is not None
                             else f"{where}: '{key}' must be at least {lo}, got {v}")
        return int(v)

    cut = w.get("order_cutoff")
    if not isinstance(cut, str) or not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", cut.strip()):
        raise InputError(f"{where}: 'order_cutoff' must be a 24-hour time like \"13:00\", got {cut!r}")
    hh, mm = (int(x) for x in cut.strip().split(":"))
    after = w.get("after_cutoff_pct")
    if after is not None and (isinstance(after, bool) or not isinstance(after, (int, float)) or not 0 <= after <= 100):
        raise InputError(f"{where}: 'after_cutoff_pct' must be a number from 0 to 100, got {after!r}")
    if "daily_capacity" not in w:
        raise InputError(f"{where}: 'daily_capacity' is required (orders picked and packed per working day)")
    closed = {}
    for i, c in enumerate(w.get("closed_dates", []) or []):
        d, name = (c, "") if isinstance(c, str) else ((c.get("date"), c.get("name") or "") if isinstance(c, dict) else (None, ""))
        if not isinstance(d, str):
            raise InputError(f"{where}: closed_dates[{i}] must be \"YYYY-MM-DD\" or {{\"date\": ..., \"name\": ...}}")
        closed[parse_date(d, where, f"closed_dates[{i}]")] = str(name)
    peaks = []
    for i, p in enumerate(w.get("peak_capacity", []) or []):
        if not isinstance(p, dict) or not {"from", "to", "capacity"} <= set(p):
            raise InputError(f"{where}: peak_capacity[{i}] needs 'from', 'to' and 'capacity'")
        a, b = parse_date(str(p["from"]), where, f"peak_capacity[{i}].from"), parse_date(str(p["to"]), where, f"peak_capacity[{i}].to")
        if b < a:
            raise InputError(f"{where}: peak_capacity[{i}] ends before it starts")
        peaks.append({"from": a, "to": b, "capacity": whole(p["capacity"], f"peak_capacity[{i}].capacity", 1)})
    tz = w.get("timezone")
    if not isinstance(tz, str) or not tz.strip():
        raise InputError(f"{where}: 'timezone' is required, a name like America/New_York (customer copy shows the "
                         f"cutoff time with its zone)")
    tz = tz.strip()
    wd = parse_weekdays(w.get("working_days", "Mon-Fri"), where, "working_days")
    return {
        "name": str(w.get("name") or ""), "timezone": tz, "tz": TZ_LABELS.get(tz, tz),
        "cutoff": (hh, mm), "cutoff_raw": f"{hh:02d}:{mm:02d}",
        "after_share": Fraction(str(after if after is not None else 50)) / 100, "after_assumed": after is None,
        "handling_days": whole(w.get("handling_days", 0), "handling_days", 0, 10),
        "working_days": wd, "closed": closed, "cal": Calendar(wd, closed),
        "daily_capacity": whole(w["daily_capacity"], "daily_capacity", 1), "peak_capacity": peaks,
    }


def load_demand(path: str, aov: Fraction | None) -> tuple:
    cols, rows = read_csv(path, "demand file")
    if "date" not in cols:
        raise InputError("demand file is missing required column 'date'")
    if "orders" in cols:
        mode = "orders"
    elif "proj_net_sales_base" in cols:
        if aov is None:
            raise InputError("demand file looks like the Budget Planner's daily plan (proj_net_sales_base, no orders "
                             "column): pass --aov with your expected net sales per order to turn it into orders")
        mode = "planner"
    else:
        raise InputError("demand file needs an 'orders' column (or the Budget Planner's proj_net_sales_base with --aov)")
    days = {}
    for line, r in rows:
        where = f"{path} line {line}"
        d = parse_date(r.get("date", ""), where, "date")
        if d in days:
            raise InputError(f"{where}: duplicate date {d}")
        if mode == "orders":
            days[d] = parse_int(r.get("orders", ""), where, "orders")
        else:
            days[d] = int(parse_amount(r.get("proj_net_sales_base", ""), where, "proj_net_sales_base") / aov + Fraction(1, 2))
    if not days:
        raise InputError("demand file has no rows")
    ds = sorted(days)
    for a, b in zip(ds, ds[1:]):
        if (b - a).days != 1:
            raise InputError(f"demand file skips from {a} to {b}: one row per day, no gaps")
    return days, mode


def load_promises(path: str) -> list:
    cols, rows = read_csv(path, "promises file")
    for c in ("placement", "text"):
        if c not in cols:
            raise InputError(f"promises file is missing required column '{c}'")
    out = []
    for line, r in rows:
        where = f"{path} line {line}"
        if not r.get("placement") or not r.get("text"):
            raise InputError(f"{where}: 'placement' and 'text' are required")
        out.append({
            "line": line, "where": where, "placement": r["placement"], "text": r["text"], "service": r.get("service", ""),
            "order_by": parse_date(r["order_by"], where, "order_by") if r.get("order_by") else None,
            "ships_within": parse_int(r["ships_within"], where, "ships_within", 0, 30) if r.get("ships_within") else None,
            "send_date": parse_date(r["send_date"], where, "send_date") if r.get("send_date") else None,
        })
    return out


# --------------------------------------------------------------------------- fulfillment model
class Fulfillment:
    """Order date -> ship date: cutoff time, working days, closures, handling time and a FIFO backlog."""

    def __init__(self, wh: dict, demand: dict):
        self.cal, self.handling = wh["cal"], wh["handling_days"]
        self.capacity, self.peaks, self.after_share = wh["daily_capacity"], wh["peak_capacity"], wh["after_share"]
        self.buckets, self.index, self.days, self.carried = [], {}, [], {}
        for d in sorted(demand):
            n = demand[d]
            if self.cal.is_open(d):
                after = int(n * self.after_share + Fraction(1, 2))
                parts = (("before", n - after), ("after", after))
            else:
                parts = (("all", n),)
            for part, count in parts:
                b = {"date": d, "part": part, "count": count, "eligible": self.eligible(d, part), "first": None, "last": None}
                self.buckets.append(b)
                self.index[(d, part)] = b
        self.buckets.sort(key=lambda b: (b["eligible"], b["date"], PART_RANK[b["part"]]))

    def cap(self, d: dt.date) -> int:
        for p in self.peaks:
            if p["from"] <= d <= p["to"]:
                return p["capacity"]
        return self.capacity

    def eligible(self, d: dt.date, part: str) -> dt.date:
        """Earliest ship day: the order date before the cutoff on a working day, else the next working day; plus handling."""
        return self.cal.add(d if part == "before" else self.cal.next_open(d), self.handling)

    def run(self, end: dt.date) -> None:
        if not self.buckets:
            return
        queue, i, carry, x = collections.deque(), 0, 0, self.buckets[0]["eligible"]
        while True:
            new = 0
            while i < len(self.buckets) and self.buckets[i]["eligible"] <= x:
                b = self.buckets[i]
                i += 1
                if b["count"]:
                    queue.append([b, b["count"]])
                    new += b["count"]
            ready, cap = carry + new, self.cap(x)
            shipped = todo = min(cap, ready)
            while todo:
                entry = queue[0]
                take = min(entry[1], todo)
                entry[0]["first"] = entry[0]["first"] or x
                entry[0]["last"] = x
                entry[1] -= take
                todo -= take
                if not entry[1]:
                    queue.popleft()
            self.carried[x] = carry
            self.days.append({"date": x, "carried": carry, "new": new, "ready": ready, "capacity": cap,
                              "shipped": shipped, "left": ready - shipped})
            carry = ready - shipped
            if i >= len(self.buckets) and not carry and x >= end:
                break
            x = self.cal.next_open(x)

    def ship(self, d: dt.date, part: str) -> tuple:
        """(first, last) ship dates of orders placed on d in this part of the day. With no such orders,
        the ship date of one order placed just before the cutoff (behind every order already waiting)."""
        b = self.index.get((d, part))
        if b is not None and b["count"] and b["last"] is not None:
            return b["first"], b["last"]
        e, key = self.eligible(d, part), (d, PART_RANK[part])
        pos = self.carried.get(e, 0) + 1 + sum(c["count"] for c in self.buckets
                                                if c["eligible"] == e and (c["date"], PART_RANK[c["part"]]) <= key)
        while pos > self.cap(e):
            pos -= self.cap(e)
            e = self.cal.next_open(e)
        return e, e


def transit_coverage(s: dict, ship: dt.date, target: dt.date, mix: dict) -> Fraction:
    """Share of customers (zone mix) whose order arrives by the target when it ships on `ship`."""
    if s["transit"] is None:
        return Fraction(1)
    pickup = s["cal"].on_or_after(ship)
    if pickup > target:
        return Fraction(0)
    avail, total = s["cal"].between(pickup, target), Fraction(0)
    for z in ZONES:
        if mix[z]:
            lo, hi = (v + s["peak"] for v in s["transit"][z])
            if avail >= hi:
                total += mix[z]
            elif avail >= lo:
                total += mix[z] * Fraction(avail - lo + 1, hi - lo + 1)
    return total


# --------------------------------------------------------------------------- analysis
def evaluate_dates(services, model, cal, mix, start, target, thresholds) -> list:
    rows, d = [], start
    while d <= target:
        part = "before" if cal.is_open(d) else "all"
        ship = model.ship(d, part)[1]
        after = model.ship(d, "after")[1] if part == "before" else ship
        cells = [{"cov": transit_coverage(s, ship, target, mix), "past": bool(s["ship_by"] and ship > s["ship_by"]),
                  "cov_after": transit_coverage(s, after, target, mix),
                  "past_after": bool(s["ship_by"] and after > s["ship_by"])} for s in services]
        rows.append({"date": d, "open": part == "before", "ship": ship, "ship_after": after, "cells": cells})
        d += ONE
    for k, s in enumerate(services):
        s["last_safe"], s["transit_last_safe"] = {}, {}
        for t in thresholds:
            need = t / 100
            ok = [r["date"] for r in rows if r["cells"][k]["cov"] >= need and not r["cells"][k]["past"]]
            tok = [r["date"] for r in rows if r["cells"][k]["cov"] >= need]
            s["last_safe"][t] = max(ok) if ok else None
            s["transit_last_safe"][t] = (max(tok) if tok else None) if s["transit"] is not None else None
        cok = [r["date"] for r in rows if not r["cells"][k]["past"]]
        s["carrier_last_safe"] = (max(cok) if cok else None) if s["ship_by"] else None
    return rows


def service_basis(s: dict, t: Fraction) -> str:
    final, tl, cl = s["last_safe"][t], s["transit_last_safe"][t], s["carrier_last_safe"]
    if s["ship_by"] is None:
        return "transit"
    if s["transit"] is None:
        return "carrier"
    if tl is not None and tl == cl:
        return "both"
    return "carrier" if final is not None and final == cl else "transit"


def analyze_backlog(model: Fulfillment, cal: Calendar, bfcm: tuple, demand: dict) -> dict:
    if not model.buckets:
        return {"modeled": False, "periods": [], "stats": {}}
    stats = {}
    for b in model.buckets:
        if not b["count"]:
            continue
        st = stats.setdefault(b["date"], {"first": b["first"], "last": b["last"], "bd_min": 99, "bd_max": 0, "extra": 0})
        st["first"], st["last"] = min(st["first"], b["first"]), max(st["last"], b["last"])
        st["bd_min"] = min(st["bd_min"], cal.between(b["date"], b["first"]))
        st["bd_max"] = max(st["bd_max"], cal.between(b["date"], b["last"]))
        st["extra"] = max(st["extra"], cal.between(b["eligible"], b["last"]))
    periods, cur = [], None
    for day in model.days:
        if day["left"]:
            if cur is None:
                cur = {"start": day["date"], "peak": 0, "peak_day": None, "clear": None}
            if day["left"] > cur["peak"]:
                cur["peak"], cur["peak_day"] = day["left"], day["date"]
        elif cur is not None:
            cur["clear"] = day["date"]
            periods.append(cur)
            cur = None
    for p in periods:
        p["delayed"] = sorted(d for d, st in stats.items() if st["extra"] and p["start"] < st["last"] <= p["clear"])
        p["worst"] = max(stats[d]["extra"] for d in p["delayed"])
        p["worst_dates"] = [d for d in p["delayed"] if stats[d]["extra"] == p["worst"]]
        lo, hi = p["delayed"][0], p["delayed"][-1]
        p["bfcm"] = lo <= bfcm[1] and hi >= bfcm[0]
        a, b = (min(lo, bfcm[0]), max(hi, bfcm[1])) if p["bfcm"] else (lo, hi)
        p["note_window"] = (a, b)
        p["note_days"] = max(st["bd_max"] for d, st in stats.items() if a <= d <= b)
        p["table"] = (min(a, p["start"]) - ONE, p["clear"])
    window = [bfcm[0] + ONE * i for i in range((bfcm[1] - bfcm[0]).days + 1)]
    in_window = [d for d in window if d in stats]
    out = {"modeled": True, "periods": periods, "stats": stats, "bfcm_covered": any(d in demand for d in window),
           "bfcm_days": max((stats[d]["bd_max"] for d in in_window), default=None),
           "bfcm_delayed": any(stats[d]["extra"] for d in in_window), "bfcm_orders": bool(in_window)}
    out["bfcm_period"] = next((p for p in periods if p["bfcm"]), None)
    return out


def rate_label(rate: str) -> str:
    return rate if "shipping" in rate.lower() else f"{rate} shipping"


def by_phrase(d: dt.date, cal: Calendar, wh: dict) -> str:
    """'1 pm ET Mon, Dec 14' on a working day (orders after the cutoff ship later); just the date otherwise."""
    t = f"{time_text(*wh['cutoff'], short=True)} {wh['tz']}".strip()
    return f"{t} {fmt_copy(d)}" if cal.is_open(d) else fmt_copy(d)


def copy_line(groups: list, target: dt.date, cal: Calendar, wh: dict) -> str:
    """Plain customer-facing order-by line for the given checkout-rate groups."""
    parts = [f"by {by_phrase(g['cutoff'], cal, wh)} for {rate_label(g['rate'])}" for g in groups]
    if len(parts) == 1:
        return f"Order {parts[0]} to arrive by {fmt_copy(target)}."
    return "Order " + ", or ".join(parts) + f", to arrive by {fmt_copy(target)}."


def classify_promise(p: dict, ctx: dict) -> dict:
    services, groups, t, target = ctx["services"], ctx["groups"], ctx["threshold"], ctx["target"]
    spec = p["service"].strip()
    unnamed = ""
    if not spec or spec.lower() in ("any", "all"):
        chosen = list(services)
        dated = [g for g in groups if g["cutoff"]]
        label = min(dated, key=lambda g: g["cutoff"])["rate"] if dated else "any method"
        unnamed = " It names no shipping method, so the earliest one applies."
    else:
        chosen = []
        for tok in [x.strip() for x in spec.split(";") if x.strip()]:
            hit = [s for g in groups if g["rate"].lower() == tok.lower() for s in g["services"]] or \
                  [s for s in services if s["name"].lower() == tok.lower()]
            if not hit:
                hint = f"a service such as {services[0]['name']!r}" + (
                    f" or a checkout rate such as {groups[0]['rate']!r}" if groups[0]["rate"] != services[0]["name"] else "")
                raise InputError(f"{p['where']}: service {tok!r} is not in the carriers file (use {hint})")
            chosen += [s for s in hit if s not in chosen]
        exact = [g for g in groups if set(id(s) for s in g["services"]) == set(id(s) for s in chosen)]
        label = exact[0]["rate"] if exact else " + ".join(s["name"] for s in chosen)
    dates = [s["last_safe"][t] for s in chosen]
    binding = None if any(d is None for d in dates) else min(dates)
    open_groups = [g for g in groups if g["cutoff"]]
    findings = []
    ob, sw, send = p["order_by"], p["ships_within"], p["send_date"]
    need_word = f"{label} needs orders by {fmt_day(binding)}" if binding else f"{label} can't make {fmt_day(target)}"
    if ob is not None:
        if binding is None or ob > binding:
            late = (f"{fmt_md(ob)} is {plural((ob - binding).days, 'day')} after the last safe order date for {label} "
                    f"({fmt_day(binding)})." if binding else f"No date works for {label}.") + unnamed
            still = [g for g in open_groups if g["cutoff"] >= ob]
            if still:
                late += " " + join_and(f"{g['rate']} (by {fmt_day(g['cutoff'])})" for g in still) + \
                        f" still arrive{'s' if len(still) == 1 else ''} in time."
            findings.append(("RISKY", "late", late,
                             f"{p['placement']} says order by {fmt_md(ob)}; {need_word}"
                             + (f" ({plural((ob - binding).days, 'day')} earlier)." if binding else ".")))
        else:
            gap = (binding - ob).days
            last_needs_time = any(s["last_safe"][t] == binding and s["needs_time"] for s in chosen)
            times = stated_times(p["text"], ctx["wh"])
            cut_min = ctx["wh"]["cutoff"][0] * 60 + ctx["wh"]["cutoff"][1]
            late_t = [x for x in times if x["minutes"] is not None and x["minutes"] > cut_min]
            odd_t = [x for x in times if x["minutes"] is None]
            if gap == 0 and last_needs_time and (late_t or odd_t):
                nxt = ctx["model"].ship(binding, "after")[1]
                x = (late_t or odd_t)[0]
                if late_t:
                    findings.append(("RISKY", "late_time",
                                     f"Matches the last safe order date for {label} ({fmt_day(binding)}) but says "
                                     f"{x['text']}, later than the {ctx['cutoff']} cutoff: orders placed after "
                                     f"{ctx['cutoff']} that day ship {fmt_day(nxt)} and miss {fmt_day(target)}." + unnamed,
                                     f"{p['placement']} says {x['text']} {fmt_md(ob)}; the cutoff is {ctx['cutoff']}, so "
                                     f"orders placed after it that day miss {fmt_day(target)}."))
                else:
                    findings.append(("WARNING", "time",
                                     f"Matches the last safe order date for {label} ({fmt_day(binding)}) but says "
                                     f"{x['text']}, which can't be compared with the {ctx['cutoff']} cutoff (warehouse "
                                     f"time zone {ctx['wh']['timezone']}): orders placed after the cutoff that day ship "
                                     f"{fmt_day(nxt)} and miss {fmt_day(target)}." + unnamed,
                                     f"{p['placement']} says {x['text']} {fmt_md(ob)}; check it against the "
                                     f"{ctx['cutoff']} cutoff."))
            elif gap == 0 and last_needs_time and not times:
                nxt = ctx["model"].ship(binding, "after")[1]
                findings.append(("WARNING", "time",
                                 f"Matches the last safe order date for {label} ({fmt_day(binding)}) but gives no time: "
                                 f"orders placed after {ctx['cutoff']} that day ship {fmt_day(nxt)} and miss {fmt_day(target)}."
                                 + unnamed,
                                 f"{p['placement']} gives {fmt_md(ob)} without the {ctx['cutoff']} cutoff; later orders that day miss {fmt_day(target)}."))
            elif gap >= CONSERVATIVE_DAYS and binding >= ctx["as_of"]:
                findings.append(("INFO", "early",
                                 f"{plural(gap, 'day')} earlier than needed: {label} orders placed by {fmt_day(binding)} "
                                 f"still arrive in time, so this tells customers ordering {fmt_range(ob + ONE, binding, fmt_md)} "
                                 f"they are too late.",
                                 f"{p['placement']} says order by {fmt_md(ob)}, {plural(gap, 'day')} earlier than {label} needs ({fmt_day(binding)})."))
            else:
                findings.append(("OK", "ok", (f"Matches the last safe order date for {label} ({fmt_day(binding)})." if gap == 0
                                              else f"{plural(gap, 'day')} before the last safe order date for {label} ({fmt_day(binding)}).")
                                 + (" Gives the cutoff time." if any(x["minutes"] == cut_min for x in times) else
                                    f" Says {times[0]['text']}, before the {ctx['cutoff']} cutoff."
                                    if times and all(x["minutes"] is not None and x["minutes"] < cut_min for x in times)
                                    else ""), ""))
    if sw is not None:
        model, cal = ctx["model"], ctx["cal"]
        if send:
            window = [send + ONE * i for i in range(MESSAGE_LIVE_DAYS)]
        else:
            window = [ctx["start"] + ONE * i for i in range((target - ctx["start"]).days + 1)]
        window = [d for d in window if d >= ctx["as_of"]]      # only orders still to come
        bad = {}
        for d in window:
            for part in (("before", "after") if cal.is_open(d) else ("all",)):
                bd = cal.between(d, model.ship(d, part)[1])
                if bd > sw:
                    bad[d] = max(bad.get(d, 0), bd)
        if bad:
            worst = max(bad.values())
            cause = " (BFCM backlog)" if ctx["bfcm_period"] and any(ctx["bfcm_period"]["note_window"][0] <= d <= ctx["bfcm_period"]["note_window"][1] for d in bad) else ""
            findings.append(("WARNING", "backlog",
                             f"Says orders ship within {plural(sw, 'business day')}. Orders placed {fmt_dates(bad)} take up to "
                             f"{plural(worst, 'business day')}{cause}.",
                             f"{p['placement']} says orders ship within {plural(sw, 'business day')}; orders placed "
                             f"{fmt_dates(bad)} take up to {worst}{cause}."))
            p["bad_dates"] = sorted(bad)
        else:
            findings.append(("OK", "ok", f"Holds: every order ships within {plural(sw, 'business day')}.", ""))
    if ob is None and sw is None:
        if send and binding and send > binding:
            findings.append(("RISKY", "sent_late",
                             f"Sends {fmt_day(send)}, after the last safe order date for {label} ({fmt_day(binding)})."
                             + unnamed,
                             f"{p['placement']} sends {fmt_day(send)}, after {label}'s last safe order date ({fmt_day(binding)})."))
        else:
            what = "no order-by date or shipping method" if not spec else "no order-by date"
            guar = " and says guaranteed" if GUARANTEE_RE.search(p["text"]) else ""
            groups_txt = join_and(f"{g['rate']} needs orders by {fmt_day(g['cutoff'])}" for g in open_groups)
            findings.append(("WARNING", "unqualified", f"Promises holiday delivery with {what}{guar}. {groups_txt}."
                             if groups_txt else f"Promises holiday delivery with {what}{guar}.",
                             f"{p['placement']}" + (f" (sends {fmt_day(send)})" if send else "")
                             + f": \"{p['text']}\" has {what}."))
    status = min((f[0] for f in findings), key=lambda s: SEV_ORDER[s])
    implied = (label if spec else "any (none named)") if (ob is not None or sw is None) else (label if spec else "—")
    return {"status": status, "findings": findings, "label": implied, "binding": binding, "chosen": chosen}


def build_calendar(a: dict) -> list:
    wh, cal, target, groups = a["wh"], a["cal"], a["target"], [g for g in a["groups_sorted"] if g["cutoff"]]
    cut, items = a["cutoff_label"], []
    for p in a["backlog"]["periods"]:
        if p["note_days"] <= 1 + wh["handling_days"]:
            continue    # no slower than an ordinary after-cutoff order: nothing to tell customers
        lo, hi = p["note_window"]
        items.append({"date": lo, "when": fmt_range(lo, hi), "switch": f"{'BFCM shipping-time' if p['bfcm'] else 'Shipping-time'} note "
                      f"(take it down {fmt_day(p['clear'])})", "where": "Shipping FAQ, cart, order confirmation email",
                      "copy": f"Orders placed {fmt_range(lo, hi, fmt_md)} ship within {plural(p['note_days'], 'business day')}."})
    if groups:
        first = groups[0]["cutoff"]
        start = first - ONE * CUTOFF_LEAD_DAYS
        items.append({"date": start, "when": fmt_day(start), "switch": "Start holiday cutoff messaging",
                      "where": "Site banner, product pages, cart, emails", "copy": copy_line(groups, target, cal, wh)})
        for i, g in enumerate(groups):
            c, rest = g["cutoff"], groups[i + 1:]
            label = rate_label(g["rate"])
            cap_label = label[0].upper() + label[1:]
            timed = cal.is_open(c)
            items.append({"date": c, "when": f"{fmt_day(c)}, morning", "switch": f"Last day for {g['rate']}",
                          "where": "Email, SMS", "copy": f"Last day for {label} to arrive by {fmt_copy(target)}: "
                          + (f"order by {time_text(*wh['cutoff'], short=True)} {wh['tz']} today.".replace("  ", " ")
                             if timed else "order today.")})
            when = f"{fmt_day(c)}, {cut}" if timed else f"{fmt_day(c + ONE)}"
            if len(rest) == 1:
                switch = f"Switch to expedited only ({rest[0]['rate']})"
                copy = (f"{cap_label} will now arrive after {fmt_copy(target)}. For delivery by {fmt_copy(target)}, "
                        f"choose {rate_label(rest[0]['rate'])} and order by {by_phrase(rest[0]['cutoff'], cal, wh)}.")
            elif rest:
                switch = f"{g['rate']} closes"
                copy = f"{cap_label} will now arrive after {fmt_copy(target)}. " + copy_line(rest, target, cal, wh)
            else:
                switch = "Switch to e-gift cards"
                copy = f"Orders placed now will arrive after {fmt_copy(target)}. Gift cards are delivered by email right away."
            items.append({"date": c, "when": when, "switch": switch, "where": "Site banner, cart, checkout"
                          if rest else "Site banner, homepage, email", "copy": copy})
    # Warehouse closures around the holiday: tell customers when late orders ship.
    named = sorted(d for d in wh["closed"] if (groups[-1]["cutoff"] if groups else target - ONE * 14) < d <= target + ONE * 7)
    for run in date_runs(named):
        w = run[0] - ONE
        while not cal.is_open(w):
            w -= ONE
        nxt = a["model"].eligible(w, "after")
        closed = fmt_range(run[0], run[1], fmt_copy)
        items.append({"date": w, "when": f"{fmt_day(w)}, {cut}", "switch": "Closure notice", "where": "Site banner, cart",
                      "copy": f"We're closed {closed}. Orders placed after {time_text(*wh['cutoff'], short=True)} {wh['tz']} "
                      f"{fmt_copy(w)} ship {fmt_copy(nxt)}.".replace("  ", " ")})
    items.append({"date": target + ONE, "when": fmt_day(target + ONE), "switch": "Take down holiday delivery messaging "
                  "(schedule it ahead)", "where": "Everywhere above", "copy": "—"})
    for it in items:
        if it["date"] < a["as_of"]:
            it["when"] += " (passed)"
    return sorted(items, key=lambda it: it["date"])


def analyze(services, wh, mix, mix_mode, mix_total, demand, demand_mode, promises, account, target, threshold,
            as_of, aov, names) -> dict:
    cal = wh["cal"]
    for s in services:
        if s["transit"] is not None:
            for z in ZONES:
                if mix[z] and s["transit"][z] is None:
                    raise InputError(f"carriers file: {s['name']} has no transit days for zone {z}, but "
                                     f"{pct(mix[z])} of orders go there"
                                     + (" (add a zone_9 column)" if z == 9 else ""))
    model = Fulfillment(wh, demand or {})
    model.run(target)
    tg = thanksgiving(target.year if target.month >= 11 else target.year - 1)
    bfcm = (tg, tg + ONE * 4)
    thresholds = sorted(set([threshold] + [Fraction(t) for t in CONTEXT_THRESHOLDS]))
    start = min([target - ONE * SEARCH_DAYS, as_of] + ([min(demand)] if demand else []))
    rows = evaluate_dates(services, model, cal, mix, start, target, thresholds)
    by_date = {r["date"]: r for r in rows}
    for k, s in enumerate(services):
        s["basis"] = service_basis(s, threshold)
        ls = s["last_safe"][threshold]
        cell = by_date[ls]["cells"][k] if ls else None
        s["needs_time"] = bool(ls and by_date[ls]["open"] and not (cell["cov_after"] >= threshold / 100 and not cell["past_after"]))
    groups = collections.OrderedDict()
    for s in services:
        groups.setdefault(s["rate"], []).append(s)
    glist = []
    for rate, svcs in groups.items():
        dates = [s["last_safe"][threshold] for s in svcs]
        cutoff = None if any(d is None for d in dates) else min(dates)
        later = max(svcs, key=lambda s: s["last_safe"][threshold]) if cutoff and len(svcs) > 1 else None
        glist.append({"rate": rate, "services": svcs, "cutoff": cutoff,
                      "extend": later if later and later["last_safe"][threshold] > cutoff else None})
    groups_sorted = sorted(glist, key=lambda g: (g["cutoff"] is None, g["cutoff"] or target))
    backlog = analyze_backlog(model, cal, bfcm, demand or {})
    cutoff_label = f"{time_text(*wh['cutoff'], short=False)} {wh['tz']}".strip()
    ctx = {"services": services, "groups": glist, "threshold": threshold, "target": target, "cal": cal, "model": model,
           "start": min(demand) if demand else as_of, "cutoff": cutoff_label, "bfcm_period": backlog.get("bfcm_period"),
           "as_of": as_of, "wh": wh}
    checked = []
    for i, p in enumerate(promises or [], 1):
        c = classify_promise(p, ctx)
        c.update(p)
        c["n"] = i
        checked.append(c)
    a = {"account": account, "as_of": as_of, "target": target, "threshold": threshold, "wh": wh, "cal": cal,
         "mix": mix, "mix_mode": mix_mode, "mix_total": mix_total, "services": services, "groups": glist,
         "groups_sorted": groups_sorted, "rows": rows, "by_date": by_date, "model": model, "backlog": backlog,
         "bfcm": bfcm, "demand": demand, "demand_mode": demand_mode, "aov": aov, "promises": checked,
         "promises_given": promises is not None, "cutoff_label": cutoff_label, "start": start, "names": names}
    a["calendar"] = build_calendar(a)
    a["issues"], a["fixes"] = build_issues_and_fixes(a)
    n = collections.Counter(i["severity"] for i in a["issues"])
    a["counts"] = n
    a["verdict"] = "NOT READY" if n["RISKY"] else ("FIX BEFORE PEAK" if n["WARNING"] else "READY")
    a["all_cutoffs_passed"] = not any(g["cutoff"] and g["cutoff"] >= as_of for g in glist)
    return a


def build_issues_and_fixes(a: dict) -> tuple:
    cal, wh, target, as_of = a["cal"], a["wh"], a["target"], a["as_of"]
    open_groups = [g for g in a["groups_sorted"] if g["cutoff"]]
    live = [g for g in open_groups if g["cutoff"] >= as_of]     # rates customers can still use
    full_copy = copy_line(live, target, cal, wh) if live else \
        f"Orders placed now will arrive after {fmt_copy(target)}. Gift cards are delivered by email right away."
    cal_start = next((it["date"] for it in a["calendar"] if it["switch"] == "Start holiday cutoff messaging"), None)
    issues, fixes = [], []
    for p in a["promises"]:
        where = p["placement"] + (f" (sends {fmt_day(p['send_date'])})" if p["send_date"] else "")
        fix_by = (p["send_date"] - ONE) if p["send_date"] else None
        for sev, kind, why, short in p["findings"]:
            if sev == "OK":
                continue
            issues.append({"severity": sev, "text": short, "state": "detected", "promise": p["n"]})
            own = [g for g in open_groups if any(s in g["services"] for s in p["chosen"])]
            new, by = None, fix_by          # new = replacement copy for the promise, when there is one
            if kind in ("late", "unqualified"):
                new = full_copy
                text = f"{where}: replace \"{p['text']}\" with \"{new}\""
            elif kind == "sent_late":
                still = [g for g in open_groups if g["cutoff"] >= p["send_date"]]
                new = copy_line(still, target, cal, wh) if still else \
                    f"Orders placed now will arrive after {fmt_copy(target)}. Gift cards are delivered by email right away."
                text = f"{where}: don't send it as written; use \"{new}\""
            elif kind == "time":
                new = copy_line(own, target, cal, wh)
                text = f"{where}: add the cutoff time: \"{new}\""
            elif kind == "late_time":
                new = copy_line(own, target, cal, wh)
                text = f"{where}: change the time to the {a['cutoff_label']} cutoff: \"{new}\""
            elif kind == "early":
                new = copy_line(own, target, cal, wh)
                text = f"{where}: move the date to {fmt_md(p['binding'])}: \"{new}\""
                by = min([d for d in (fix_by, cal_start, p["order_by"] - ONE) if d])
            else:  # backlog
                bp = a["backlog"]["bfcm_period"] if a["backlog"].get("bfcm_period") else \
                    (a["backlog"]["periods"][0] if a["backlog"]["periods"] else None)
                if bp:
                    lo, hi = bp["note_window"]
                    line = f"Orders placed {fmt_range(lo, hi, fmt_md)} ship within {plural(bp['note_days'], 'business day')}."
                    text = (f"{where}: add \"{line}\" above the current text, and the same line in the order confirmation "
                            f"email for those orders. If orders fall further behind than you state, email the affected "
                            f"customers a delay notice that offers to cancel.")
                    by = min([d for d in (fix_by, lo - ONE) if d])
                else:
                    text = f"{where}: change the stated shipping time to one every order meets."
            if new and p["send_date"] and p["send_date"] < as_of:
                text = f"{where}: already sent, so it can't be changed. Use this in the next email or SMS: \"{new}\""
                by = None
            fixes.append({"severity": sev, "text": text, "state": "recommended", "by": by})
    for p in a["backlog"]["periods"]:
        if p["worst"] >= BACKLOG_WARN_DAYS:
            lo, hi = p["note_window"]
            issues.append({"severity": "WARNING", "state": "detected", "promise": None,
                           "text": f"Backlog: orders placed {fmt_dates(p['worst_dates'])} ship {plural(p['worst'], 'business day')} "
                                   f"later than usual; it clears {fmt_day(p['clear'])}."})
            fixes.append({"severity": "WARNING", "state": "recommended", "by": p["start"] - ONE,
                          "text": f"Add pick/pack capacity for {fmt_range(p['start'], p['clear'])} (temporary staff or a "
                                  f"weekend shift), or tell customers that orders placed {fmt_range(lo, hi, fmt_md)} ship "
                                  f"within {plural(p['note_days'], 'business day')}."})
    if any((s["transit_source"] or "").lower() == "sample" or (s["ship_by_source"] or "").lower() == "sample"
           for s in a["services"]):
        fixes.append({"severity": "PLAN", "state": "recommended", "by": None,
                      "text": "Replace the `sample` rows in carriers.csv with your own transit days by zone and each "
                              "carrier's published holiday ship-by dates, with the page URL in `source` "
                              "(references/playbook.md §1), then re-run. The dates above come from fictional sample data."})
    bp = a["backlog"].get("bfcm_period")
    rerun = [d for d in ((bp["clear"] if bp else None), (cal_start - ONE * 3) if cal_start else None) if d]
    fixes.append({"severity": "PLAN", "state": "recommended", "by": min(rerun) if rerun else None,
                  "text": "Re-run with actual orders to date and the carriers' latest holiday dates" +
                          (f", before the cutoff messaging starts {fmt_day(cal_start)}." if cal_start else ".")})
    first_switch = min((it["date"] for it in a["calendar"]), default=None)
    if first_switch:
        fixes.append({"severity": "PLAN", "state": "recommended", "by": first_switch - ONE,
                      "text": "Schedule every switch in the messaging calendar (section 4) in the site, cart, checkout, "
                              "email and SMS tools so each goes live on its date without anyone on call."})
    issues.sort(key=lambda i: SEV_ORDER[i["severity"]])
    fixes.sort(key=lambda f: SEV_ORDER[f["severity"]])
    for f in fixes:
        if f["by"] is not None:
            f["by"] = cal.on_or_before(f["by"])
        f["by_text"] = by_text(f["by"], as_of)
    return issues, fixes


# --------------------------------------------------------------------------- report
def source_text(src: str, kind: str, carrier: str) -> str:
    if src.lower() == "sample":
        return ("sample (fictional carrier data)" if kind == "transit"
                else f"sample (fictional, not a date {carrier} published)")
    if URL_RE.match(src):
        return src
    return f"{src} (as given; no URL)"


def render(a: dict) -> str:
    t, target, cal, wh, as_of = a["threshold"], a["target"], a["cal"], a["wh"], a["as_of"]
    tl, cut, services, bl = num(t), a["cutoff_label"], a["services"], a["backlog"]
    n = a["counts"]
    L = [f"## Holiday Shipping Cutoff Check: {a['account']}, as of {as_of.isoformat()}", ""]
    counts = [plural(n["RISKY"], "risky promise")] if n["RISKY"] else []
    counts += [plural(n["WARNING"], "warning")] if n["WARNING"] else []
    counts += [plural(n["INFO"], "note")] if n["INFO"] else []
    if counts:
        summary = join_and(counts) + "."
    elif a["all_cutoffs_passed"]:
        summary = (f"No promise or backlog issues, but all cutoffs have passed: orders placed now arrive after "
                   f"{fmt_day(target)}.")
    else:
        summary = "Nothing to fix."
    L.append(f"**Verdict: {a['verdict']}.** {summary} "
             f"Goal: delivery by {fmt_day(target)}, {target.year} for at least {tl}% of customers.")
    L.append("")
    L.append(f"**Last safe order dates** (orders placed by {cut}):")
    for s in services:
        ls = s["last_safe"][t]
        rate = f" ({s['rate']})" if s["rate"] != s["name"] else ""
        if ls is None:
            L.append(f"- {s['name']}{rate}: **none**: no order date in the window reaches {tl}%")
            continue
        basis = {"carrier": f"from the carrier ship-by date (source: {s['ship_by_source']})",
                 "transit": f"from transit by zone (source: {s['transit_source']})",
                 "both": "transit and the carrier ship-by date agree"}[s["basis"]]
        passed = " (passed)" if ls < as_of else ""
        L.append(f"- {s['name']}{rate}: **{fmt_day(ls)}**{passed}, {basis}")
    if any(len(g["services"]) > 1 for g in a["groups"]):
        L.append("- By checkout rate (a rate that uses several carriers gets the earliest date): "
                 + ", ".join(f"{g['rate']} **{fmt_day(g['cutoff']) if g['cutoff'] else 'none'}**"
                             for g in a["groups_sorted"]))
    L.append("")
    if not bl["modeled"]:
        L.append("**BFCM backlog:** not modeled (no --demand file).")
    elif not bl["bfcm_covered"]:
        L.append(f"**BFCM backlog:** not modeled: the demand file has no rows for {fmt_range(*a['bfcm'])}.")
    elif not bl["bfcm_orders"]:
        bp = bl["bfcm_period"]
        L.append(f"**BFCM backlog:** no orders forecast in the BFCM window ({fmt_range(*a['bfcm'])})"
                 + (f", but orders placed {fmt_dates(bp['delayed'])} build a backlog that clears {fmt_day(bp['clear'])}."
                    if bp else "."))
    elif bl["bfcm_delayed"]:
        L.append(f"**BFCM backlog:** orders placed {fmt_range(*a['bfcm'])} ship within "
                 f"{plural(bl['bfcm_days'], 'business day')}; the backlog clears {fmt_day(bl['bfcm_period']['clear'])}.")
    else:
        L.append(f"**BFCM backlog:** none. Orders placed {fmt_range(*a['bfcm'])} ship on the normal schedule.")
    L.append("")
    if a["issues"]:
        L.append("**Issues, most severe first** (all detected, none changed)")
        for i, it in enumerate(a["issues"], 1):
            L.append(f"{i}. **{it['severity']}**: {it['text']}")
    else:
        L.append("**Issues:** none found in the promises and backlog checked.")
    nc = []
    if not a["promises_given"]:
        nc.append("delivery promises (no --promises file)")
    if not bl["modeled"]:
        nc.append("the fulfillment backlog (no --demand file; ship dates assume the normal schedule)")
    if nc:
        L.append("")
        L.append("Not checked: " + "; ".join(nc) + ".")
    L.append("")

    # 1. last safe dates
    L.append(f"### 1. Last safe order dates (deliver by {fmt_day(target)}, {target.year})")
    L.append("")
    L.append(f"Safe means at least {tl}% of customers, weighted by where your orders go, get the order by "
             f"{fmt_day(target)}. Dates are for orders placed by {cut}; an order placed later counts as the next working "
             f"day's order.")
    L.append("")
    mix_src = (f"{a['names']['destinations']}, {int(a['mix_total']):,} orders" if a["mix_mode"] == "orders"
               else a["names"]["destinations"])
    L.append(f"Zone mix ({mix_src}): " + ", ".join(f"zone {z} {pct(a['mix'][z])}" for z in ZONES if a["mix"][z]) + ".")
    L.append("")
    ctx_t = [Fraction(x) for x in CONTEXT_THRESHOLDS]
    L.append(f"| Service | Checkout rate | Last safe (≥ {tl}%) | At {num(ctx_t[0])}% | At {num(ctx_t[1])}% | "
             "From transit | Carrier ship-by date | Based on |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    def f(d):
        return fmt_day(d) if d else "none"

    for s in services:
        tr = f(s["transit_last_safe"][t]) if s["transit"] is not None else "no transit table"
        sb = f"{fmt_day(s['ship_by'])} ({s['ship_by_source'] if s['ship_by_source'].lower() == 'sample' else 'source below'})" \
            if s["ship_by"] else "—"
        basis = {"carrier": "carrier ship-by date", "transit": "transit by zone", "both": "both agree"}[s["basis"]]
        L.append(f"| {s['name']} | {s['rate'] if s['rate'] != s['name'] else '—'} | **{f(s['last_safe'][t])}** | {f(s['last_safe'][ctx_t[0]])} | "
                 f"{f(s['last_safe'][ctx_t[1]])} | {tr} | {sb} | {basis} |")
    L.append("")
    multi = [g for g in a["groups_sorted"] if len(g["services"]) > 1]
    for g in multi:
        names = join_and(s["name"] for s in g["services"])
        when = fmt_day(g["cutoff"]) if g["cutoff"] else "none"
        if g["extend"]:
            L.append(f"{g['rate']} ships with {names}. Customers can't pick the carrier, so {g['rate']}'s last safe date "
                     f"is the earlier one, {when}. To keep {g['rate']} open until {fmt_day(g['extend']['last_safe'][t])}, "
                     f"ship every {g['rate']} order placed after {cut} {when} with {g['extend']['name']}.")
        else:
            L.append(f"{g['rate']} ships with {names}; its last safe date is {when}.")
        L.append("")
    L.append("Where each date comes from:")
    for s in services:
        bits = []
        if s["transit"] is not None:
            shown = [z for z in ZONES if z in REQUIRED_ZONES or s["transit"][z]]
            vals = [fmt_transit(s["transit"][z]) if s["transit"][z] else "—" for z in shown]
            tr = (f"{vals[0]} business day{'' if vals[0] == '1' else 's'} in every zone" if len(set(vals)) == 1
                  else f"{', '.join(vals)} business days for zones 1–{shown[-1]}")
            peak = f", plus {plural(s['peak'], 'peak day')} you added for December delays" if s["peak"] else ""
            nd = f"no delivery {', '.join(fmt_day(d) for d in s['no_delivery'])}" if s["no_delivery"] else "no non-delivery dates given"
            bits.append(f"transit {tr}{peak}; delivers {fmt_weekdays(s['days'])}; {nd}. "
                        f"Source: {source_text(s['transit_source'], 'transit', s['carrier'])}.")
        if s["ship_by"]:
            bits.append(f"Ship-by date {fmt_day(s['ship_by'])}. Source: {source_text(s['ship_by_source'], 'ship_by', s['carrier'])}.")
        L.append(f"- {s['name']}: " + " ".join(bits))
    L.append("")
    safe = [s["last_safe"][t] for s in services if s["last_safe"][t]]
    hi = target - ONE
    lo = max(a["start"], min((min(safe) - ONE * 4) if safe else hi, hi - ONE * 13))
    L.append(f"Share of customers who get an order by {fmt_day(target)}, by order date (orders placed by {cut}):")
    L.append("")
    L.append(f"| Order date | Ships (ordered by {time_text(*wh['cutoff'], short=False)}) | Ships (ordered later) | "
             + " | ".join(s["name"] for s in services) + " |")
    L.append("| --- | --- | --- | " + " | ".join("---:" for _ in services) + " |")
    for r in a["rows"]:
        if not lo <= r["date"] <= hi:
            continue
        cells = []
        for s, c in zip(services, r["cells"]):
            if c["past"]:
                cells.append("after ship-by")
            elif s["transit"] is None:
                cells.append("by ship-by")
            else:
                cells.append(pct(c["cov"]))
        name = wh["closed"].get(r["date"])
        closed = "" if r["open"] else (f" (closed: {name})" if name else " (closed)")
        L.append(f"| {fmt_day(r['date'])}{closed} | {fmt_day(r['ship'])} | {fmt_day(r['ship_after'])} | " + " | ".join(cells) + " |")
    L.append("")

    # 2. fulfillment and backlog
    L.append("### 2. Fulfillment and the BFCM backlog")
    L.append("")
    wname = f"{wh['name']} " if wh["name"] else ""
    tzs = f" ({wh['timezone']})" if wh["timezone"] else ""
    handling = ("ship the same day" if wh["handling_days"] == 0
                else f"ship {plural(wh['handling_days'], 'working day')} later")
    closures = ", ".join(f"{fmt_day(d)}" + (f" ({nm})" if nm else "") for d, nm in sorted(wh["closed"].items())) or "none"
    caps = f"Capacity {wh['daily_capacity']:,} orders per working day"
    if wh["peak_capacity"]:
        caps += " (" + "; ".join(f"{p['capacity']:,} on {fmt_range(p['from'], p['to'])}" for p in wh["peak_capacity"]) + ")"
    after_txt = (f"{num(wh['after_share'] * 100)}% of orders come in after the cutoff"
                 + (" (assumed; set after_cutoff_pct in warehouse.json)" if wh["after_assumed"] else ""))
    L.append(f"{wname}{tzs.strip()}: orders placed by {cut} on a working day ({fmt_weekdays(wh['working_days'])}) "
             f"{handling}; later orders and orders placed on a closed day count from the next working day. "
             f"Closed: {closures}. {caps}. {after_txt}.".replace(" :", ":").lstrip())
    if bl["modeled"]:
        dm = a["demand"]
        src = a["names"]["demand"] + ("" if a["demand_mode"] == "orders"
                                        else f" (Budget Planner plan, net sales ÷ ${num(a['aov'])} per order)")
        L.append(f"Demand: {src}, {fmt_range(min(dm), max(dm))} ({sum(dm.values()):,} orders).")
        if max(dm) < target:
            L.append(f"Ship dates for orders placed after {fmt_day(max(dm))} assume no new backlog (the demand file ends there).")
    L.append("")
    if not bl["modeled"]:
        L.append("Not modeled: no --demand file. Every ship date above assumes the normal schedule with no backlog.")
        L.append("")
    elif not bl["periods"]:
        L.append("No backlog: on every working day, the orders ready to ship fit within capacity, so every order "
                 "ships on the normal schedule.")
        L.append("")
    for p in bl["periods"]:
        lo2, hi2 = p["note_window"]
        if p["bfcm"] and bl["bfcm_days"] is None:
            L.append(f"**No orders are forecast in the BFCM window ({fmt_range(*a['bfcm'])}), but the backlog from orders "
                     f"placed {fmt_dates(p['delayed'])} runs through it and clears {fmt_day(p['clear'])}.** The worst extra "
                     f"delay is {plural(p['worst'], 'business day')} (orders placed {fmt_dates(p['worst_dates'])}). "
                     f"Peak backlog: {p['peak']:,} orders at close on {fmt_day(p['peak_day'])}.")
        elif p["bfcm"]:
            L.append(f"**Orders placed {fmt_range(*a['bfcm'])} ship within {plural(bl['bfcm_days'], 'business day')}; "
                     f"the backlog clears {fmt_day(p['clear'])}.** The worst extra delay is "
                     f"{plural(p['worst'], 'business day')} (orders placed {fmt_dates(p['worst_dates'])}).")
            outside = [d for d in p["delayed"] if not a["bfcm"][0] <= d <= a["bfcm"][1]]
            if outside:
                ext = max(bl["stats"][d]["extra"] for d in outside)
                L.append(f"Orders placed {fmt_dates(outside)} also wait, up to {plural(ext, 'extra business day')}. "
                         f"Peak backlog: {p['peak']:,} orders at close on {fmt_day(p['peak_day'])}.")
            else:
                L.append(f"Peak backlog: {p['peak']:,} orders at close on {fmt_day(p['peak_day'])}.")
        else:
            L.append(f"**Backlog {fmt_range(p['start'], p['clear'])}:** orders placed {fmt_dates(p['delayed'])} ship up to "
                     f"{plural(p['worst'], 'business day')} later than usual; it clears {fmt_day(p['clear'])}. "
                     f"Peak backlog: {p['peak']:,} orders at close on {fmt_day(p['peak_day'])}.")
        L.append("")
        L.append("| Date | Orders placed | Ready to ship | Shipped | Backlog at close | That day's orders ship | Business days to ship |")
        L.append("| --- | ---: | ---: | ---: | ---: | --- | --- |")
        days = {d["date"]: d for d in a["model"].days}
        d, last_left = p["table"][0], 0
        prev = [x for x in a["model"].days if x["date"] < d]
        last_left = prev[-1]["left"] if prev else 0
        while d <= p["table"][1]:
            label = fmt_day(d) + ("" if cal.is_open(d) else
                                  (f" (closed: {wh['closed'][d]})" if wh["closed"].get(d) else " (closed)"))
            placed = f"{a['demand'][d]:,}" if d in a["demand"] else "—"
            st = bl["stats"].get(d)
            ships = fmt_range(st["first"], st["last"]) if st else "—"
            bd = (str(st["bd_min"]) if st["bd_min"] == st["bd_max"] else f"{st['bd_min']}–{st['bd_max']}") if st else "—"
            if d in days:
                x = days[d]
                last_left = x["left"]
                L.append(f"| {label} | {placed} | {x['ready']:,} | {x['shipped']:,} | {x['left']:,} | {ships} | {bd} |")
            else:
                L.append(f"| {label} | {placed} | — | closed | {last_left:,} | {ships} | {bd} |")
            d += ONE
        L.append("")
    if bl["modeled"]:
        L.append("Business days to ship count working days after the order date: 0 means it ships the day it was "
                 "placed. Orders ship first in, first out.")
        L.append("")

    # 3. promises
    L.append("### 3. Delivery promises")
    L.append("")
    if not a["promises_given"]:
        L.append("Not checked: no --promises file. List every place a delivery date or shipping time appears "
                 "(DATA_CONTRACT.md §5) and re-run.")
        L.append("")
    else:
        L.append("| # | Where | Says | Implies | Order-by | Status | Why |")
        L.append("| ---: | --- | --- | --- | --- | --- | --- |")
        for p in a["promises"]:
            where = p["placement"] + (f" (sends {fmt_day(p['send_date'])})" if p["send_date"] else "")
            why = " ".join(f[2] for f in p["findings"] if f[0] == p["status"] or p["status"] == "OK")
            ob = fmt_day(p["order_by"]) if p["order_by"] else "—"
            text = p["text"].replace("|", "/")
            L.append(f"| {p['n']} | {where} | \"{text}\" | {p['label']} | {ob} | {p['status']} | {why} |")
        L.append("")
        ftc = [str(p["n"]) for p in a["promises"] if any(f[1] in ("unqualified", "backlog", "sent_late") for f in p["findings"])]
        if ftc:
            L.append(f"_Items {join_and(ftc)}: {FTC}_" if len(ftc) > 1 else f"_Item {ftc[0]}: {FTC}_")
            L.append("")

    # 4. messaging calendar
    L.append("### 4. Messaging calendar")
    L.append("")
    L.append("Plain copy you can paste; adjust names to your checkout. Delivery dates are estimates, not guarantees.")
    L.append("")
    L.append("| When | Switch | Where | Suggested copy |")
    L.append("| --- | --- | --- | --- |")
    for it in a["calendar"]:
        copy = it["copy"] if it["copy"] == "—" else f"\"{it['copy']}\""
        L.append(f"| {it['when']} | {it['switch']} | {it['where']} | {copy} |")
    L.append("")

    # 5. fixes
    L.append("### 5. Fix list (nothing has been changed)")
    L.append("")
    L.append("| # | Severity | What to do | State | By |")
    L.append("| ---: | --- | --- | --- | --- |")
    for i, f in enumerate(a["fixes"], 1):
        L.append(f"| {i} | {f['severity']} | {f['text']} | {f['state']} | {f['by_text']} |")
    L.append("")
    L.append(f"_Coverage is the share of customers, weighted by zone mix, whose order arrives by the target; a transit "
             f"range like 3–5 days is spread evenly across its days, and a carrier ship-by date overrides a later "
             f"transit-based date. Promises: RISKY = order-by date after the last safe date (or sent after it), or a stated time "
             f"later than the cutoff on the last safe day; WARNING "
             f"= no order-by date, no cutoff time on the last safe day, or a shipping time the backlog breaks; INFO = "
             f"order-by date {CONSERVATIVE_DAYS}+ days earlier than needed. Each day's coverage uses the latest ship date "
             f"of that day's orders (conservative). States: detected → recommended → "
             f"applied → verified. Read-only: no site, checkout, email, SMS or carrier setting was changed._")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- json
def iso(d):
    return d.isoformat() if d else None


def to_json(a: dict) -> dict:
    t = a["threshold"]
    bl = a["backlog"]
    out = {
        "account": a["account"], "as_of": iso(a["as_of"]), "target_date": iso(a["target"]),
        "threshold_pct": float(t), "verdict": a["verdict"], "all_cutoffs_passed": a["all_cutoffs_passed"],
        "counts": {k: a["counts"][k] for k in ("RISKY", "WARNING", "INFO")},
        "cutoff": {"time": a["wh"]["cutoff_raw"], "timezone": a["wh"]["timezone"], "label": a["cutoff_label"]},
        "zone_mix": {str(z): round(float(a["mix"][z]), 4) for z in ZONES if z in REQUIRED_ZONES or a["mix"][z]},
        "services": [{
            "name": s["name"], "carrier": s["carrier"], "service": s["service"], "checkout_rate": s["rate"],
            "last_safe_order_date": iso(s["last_safe"][t]),
            "last_safe_by_threshold": {num(k): iso(v) for k, v in sorted(s["last_safe"].items())},
            "transit_last_safe": iso(s["transit_last_safe"][t]), "ship_by_date": iso(s["ship_by"]),
            "basis": s["basis"], "needs_cutoff_time": s["needs_time"], "peak_extra_days": s["peak"],
            "transit_source": s["transit_source"], "ship_by_source": s["ship_by_source"],
        } for s in a["services"]],
        "checkout_rates": [{"rate": g["rate"], "last_safe_order_date": iso(g["cutoff"]),
                            "services": [s["name"] for s in g["services"]],
                            "extend_with": g["extend"]["name"] if g["extend"] else None,
                            "extend_to": iso(g["extend"]["last_safe"][t]) if g["extend"] else None}
                           for g in a["groups_sorted"]],
        "backlog": {
            "modeled": bl["modeled"], "bfcm_window": [iso(a["bfcm"][0]), iso(a["bfcm"][1])],
            "bfcm_ship_within_business_days": bl.get("bfcm_days"),
            "bfcm_orders_forecast": bl.get("bfcm_orders"),
            "bfcm_clears": iso(bl["bfcm_period"]["clear"]) if bl.get("bfcm_period") else None,
            "periods": [{
                "start": iso(p["start"]), "clears": iso(p["clear"]), "peak_backlog": p["peak"], "peak_day": iso(p["peak_day"]),
                "worst_extra_business_days": p["worst"], "worst_order_dates": [iso(d) for d in p["worst_dates"]],
                "delayed_order_dates": [iso(d) for d in p["delayed"]],
                "note_window": [iso(p["note_window"][0]), iso(p["note_window"][1])],
                "note_ship_within_business_days": p["note_days"],
            } for p in bl["periods"]],
            "ship_days": [{"date": iso(d["date"]), "ready": d["ready"], "capacity": d["capacity"], "shipped": d["shipped"],
                           "left": d["left"]} for d in a["model"].days if d["ready"] or d["left"]],
        },
        "promises": [{"n": p["n"], "placement": p["placement"], "text": p["text"], "service": p["service"],
                      "order_by": iso(p["order_by"]), "ships_within": p["ships_within"], "send_date": iso(p["send_date"]),
                      "status": p["status"], "why": [f[2] for f in p["findings"]], "last_safe_for_service": iso(p["binding"])}
                     for p in a["promises"]],
        "issues": [{"severity": i["severity"], "text": i["text"], "state": i["state"]} for i in a["issues"]],
        "calendar": [{"date": iso(i["date"]), "when": i["when"], "switch": i["switch"], "where": i["where"],
                      "copy": i["copy"]} for i in a["calendar"]],
        "fixes": [{"severity": f["severity"], "text": f["text"], "state": f["state"], "by": iso(f["by"]),
                   "by_text": f["by_text"]} for f in a["fixes"]],
        "coverage_by_order_date": [{
            "order_date": iso(r["date"]), "ships": iso(r["ship"]), "ships_if_after_cutoff": iso(r["ship_after"]),
            "services": {s["name"]: {"coverage": round(float(c["cov"]), 4), "after_ship_by": c["past"]}
                         for s, c in zip(a["services"], r["cells"])}} for r in a["rows"] if r["date"] >= a["target"] - ONE * 21],
    }
    return out


# --------------------------------------------------------------------------- main
def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--carriers", required=True, help="carriers.csv: transit days by zone and/or holiday ship-by dates")
    ap.add_argument("--warehouse", required=True, help="warehouse.json: cutoff, working days, closures, capacity")
    ap.add_argument("--destinations", required=True, help="destinations.csv: orders (or share) by carrier zone")
    ap.add_argument("--demand", help="optional demand.csv: projected orders per day")
    ap.add_argument("--promises", help="optional promises.csv: every delivery promise and where it appears")
    ap.add_argument("--account", default="Store", help="store name for the report title")
    ap.add_argument("--target", help="deliver-by date YYYY-MM-DD (default: Dec 24 of the --as-of year)")
    ap.add_argument("--threshold", default=DEFAULT_THRESHOLD, help="share of customers that must get it in time, %% (default 95)")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: today)")
    ap.add_argument("--aov", help="net sales per order, to convert a Budget Planner daily plan into orders")
    ap.add_argument("--json", help="also write the structured result to this path")
    args = ap.parse_args(argv)
    try:
        as_of = parse_date(args.as_of, "--as-of", "as-of") if args.as_of else dt.date.today()
        if args.target:
            target = parse_date(args.target, "--target", "target")
        else:
            target = dt.date(as_of.year, 12, 24)
            if target <= as_of:
                target = dt.date(as_of.year + 1, 12, 24)
        if target <= as_of:
            raise InputError(f"--target ({target}) must be after --as-of ({as_of})")
        thr = args.threshold.strip().rstrip("%").strip()
        if not NUM_RE.match(thr) or not 0 < Fraction(thr) <= 100:
            raise InputError(f"--threshold must be a percentage above 0 and at most 100, got {args.threshold!r}")
        aov = None
        if args.aov is not None:
            aov = parse_amount(args.aov, "--aov", "aov")
            if aov <= 0:
                raise InputError("--aov must be more than 0")
        services = load_carriers(args.carriers)
        for s in services:
            if s["ship_by"] and s["ship_by"] > target:
                raise InputError(f"carriers file: {s['name']} has ship_by_date {s['ship_by']}, after the target date "
                                 f"{target}. A ship-by date is the last day to hand orders to the carrier for delivery "
                                 f"by the target, so it can't be later; check the date (or --target).")
        wh = load_warehouse(args.warehouse)
        mix, mix_mode, mix_total = load_destinations(args.destinations)
        demand, demand_mode = load_demand(args.demand, aov) if args.demand else (None, None)
        promises = load_promises(args.promises) if args.promises else None
        names = {"destinations": os.path.basename(args.destinations),
                 "demand": os.path.basename(args.demand) if args.demand else None}
        a = analyze(services, wh, mix, mix_mode, mix_total, demand, demand_mode, promises, args.account, target,
                    Fraction(thr), as_of, aov, names)
        text = render(a)
        if args.json:
            try:
                with open(args.json, "w", encoding="utf-8") as f:
                    json.dump(to_json(a), f, indent=2, sort_keys=True, ensure_ascii=False)
                    f.write("\n")
            except OSError as e:
                raise InputError(f"cannot write --json file {args.json}: {e.strerror or e}")
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
