#!/usr/bin/env python3
"""
BFCM Tracking & Attribution Preflight: analyzer.

Reads local exports (see DATA_CONTRACT.md) and prints a Markdown report:
whether purchase events reach Meta, Google Ads and TikTok completely and
without double counting, whether UTMs are clean enough to attribute BFCM
sales, how far last year's platform claims were from what Shopify actually
booked, and a dated fix list.

Local files only. Never calls an API, never changes anything.

    python3 scripts/preflight.py --account "Store" --events events_daily.csv \
        [--orders orders_utm.csv] [--last-bfcm last_bfcm.csv] \
        [--as-of YYYY-MM-DD] [--json out.json]

Python 3.8+, standard library only. Output is deterministic for a given input.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import re
import statistics
import sys
from urllib.parse import parse_qs, unquote_plus, urlsplit

# ---- begin canonical strict number parser (shared/strict_num.py, verbatim) ----
# Canonical strict number parser for the BFCM batch (QA fix 0.1).
# Copy this block verbatim into each script that reads numbers (no cross-package imports).
# Python 3.8 compatible, stdlib only.
import math
import re

_CURRENCY_CHARS = "$€£¥₹₩₽¢"
_THOUSANDS_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_PLAIN_RE = re.compile(r"^(\d+(\.\d*)?|\.\d+)$")
_EURO_RE = re.compile(r"^\d{1,3}(\.\d{3})*,\d+$")
_DECIMAL_HINT = "export with '.' as the decimal separator and ',' only for thousands (e.g. 1,234.56)"


class NumberFormatError(ValueError):
    """Raised for a cell that is not a plain, finite number. Callers turn it into exit 2."""


def strict_number(raw, allow_negative=False, where=""):
    """Parse one numeric cell strictly.

    Accepts: 1234.56, 1,234.56, $1,234.56, €1,234.56, 1234 USD-style symbols at
    either end, leading '-' or (123.45) when allow_negative is True.
    Rejects: comma decimals (2255,74 / 28.320,00), nan, inf, exponents (1e9),
    stray text, and negatives where they are not allowed.
    Returns a float. Raises NumberFormatError with a message naming `where`.
    """
    loc = (" at " + where) if where else ""
    if raw is None:
        raise NumberFormatError("missing number%s" % loc)
    s = str(raw).strip()
    if s == "":
        raise NumberFormatError("empty number%s" % loc)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    if s.startswith("-"):
        if neg:
            raise NumberFormatError("not a number %r%s" % (raw, loc))
        neg, s = True, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.strip(_CURRENCY_CHARS).strip()
    if s.startswith("-") and not neg:  # e.g. $-12.00
        neg, s = True, s[1:].strip()
    if _THOUSANDS_RE.match(s):
        s = s.replace(",", "")
    elif _EURO_RE.match(s) or re.match(r"^\d+,\d+$", s):
        raise NumberFormatError("%r looks like a comma-decimal number%s; %s" % (raw, loc, _DECIMAL_HINT))
    elif "," in s:
        raise NumberFormatError("%r has commas that are not thousands separators%s; %s" % (raw, loc, _DECIMAL_HINT))
    if not _PLAIN_RE.match(s):
        raise NumberFormatError("not a plain number %r%s (nan, inf, exponents and text are rejected)" % (raw, loc))
    v = float(s)
    if not math.isfinite(v):
        raise NumberFormatError("non-finite number %r%s" % (raw, loc))
    if neg:
        if not allow_negative:
            raise NumberFormatError("negative value %r not allowed%s" % (raw, loc))
        v = -v
    return v


def looks_european(values):
    """True when more than half of the non-empty values are clearly 1.234,56-style."""
    vals = [str(v).strip().strip(_CURRENCY_CHARS).strip() for v in values if str(v).strip()]
    if not vals:
        return False
    hits = sum(1 for v in vals
               if _EURO_RE.match(v.lstrip("-(").rstrip(")")) and not _THOUSANDS_RE.match(v.lstrip("-(").rstrip(")")))
    return hits * 2 > len(vals)
# ---- end canonical strict number parser ----

WINDOW_DAYS = 28          # the event check uses the last 28 days of the file
MIN_DAYS = 14             # fewer days than this is not enough to judge
RECENT_DAYS = 7           # "current state" = the last 7 days
MATCH_OK = (0.85, 1.10)   # site-wide purchase events per Shopify order: OK band
MATCH_WARN = (0.70, 1.25)  # outside this band -> CRITICAL
VALUE_OK = (0.85, 1.15)   # platform value per event / Shopify net sales per order: OK band
VALUE_CRIT = (0.50, 2.00)  # outside this band -> CRITICAL
DEDUP_MIN = 0.50          # a day is "dedup broken" if Meta merged < 50% of possible duplicates
DEDUP_OVERCOUNT = 1.25    # ... and counted > 1.25 purchases per Shopify order
CAPI_PARTIAL = 0.60       # server events per order below this -> partial CAPI
ZERO_MIN_ORDERS = 5       # a zero-event day only counts if Shopify had >= 5 orders
ZERO_MIN_MEDIAN = 3       # ... and the stream normally sends >= 3 a day
NO_UTM_WARN = 0.20        # share of orders without utm_source
NO_UTM_CRIT = 0.40
CLICKID_WARN_SHARE = 0.02  # click IDs without UTMs on >= 2% of orders (and >= 10) -> WARNING
CLICKID_WARN_MIN = 10

# Days before Black Friday for each dated step.
FIX_LEAD, RECHECK_LEAD, BUDGET_LEAD, FREEZE_LEAD, FINAL_LEAD = 17, 14, 10, 7, 3

PLATFORMS = (
    # key, label, count column, value column, counts every site purchase (not only ad-attributed)
    ("meta", "Meta", "meta_purchases", "meta_purchase_value", True),
    ("google", "Google Ads", "google_conversions", "google_conversion_value", False),
    ("tiktok", "TikTok", "tiktok_purchases", "tiktok_purchase_value", True),
)
PLATFORM_LABELS = {"meta": "Meta", "google": "Google Ads", "tiktok": "TikTok", "bing": "Microsoft Ads",
                   "microsoft": "Microsoft Ads", "pinterest": "Pinterest", "snapchat": "Snapchat"}

SOURCE_ALIASES = {
    "facebook": "facebook", "fb": "facebook", "facebook.com": "facebook", "m.facebook.com": "facebook",
    "l.facebook.com": "facebook", "lm.facebook.com": "facebook", "meta": "facebook", "ig": "facebook",
    "instagram": "facebook", "instagram.com": "facebook", "l.instagram.com": "facebook",
    "an": "facebook", "msg": "facebook", "threads": "facebook",
    "google": "google", "google.com": "google", "adwords": "google", "googleads": "google",
    "google_ads": "google", "google-ads": "google",
    "tiktok": "tiktok", "tiktok.com": "tiktok", "tt": "tiktok", "tiktokads": "tiktok",
    "bing": "bing", "microsoft": "bing", "pinterest": "pinterest", "pin": "pinterest",
    "snapchat": "snapchat", "snap": "snapchat", "klaviyo": "klaviyo",
}
SOURCE_LABELS = {"facebook": "Meta", "google": "Google", "tiktok": "TikTok", "bing": "Microsoft",
                 "pinterest": "Pinterest", "snapchat": "Snapchat", "klaviyo": "Klaviyo"}
CLICK_IDS = (("fbclid", "Meta"), ("gclid", "Google Ads"), ("gbraid", "Google Ads"),
             ("wbraid", "Google Ads"), ("ttclid", "TikTok"), ("msclkid", "Microsoft Ads"))
MACRO_RE = re.compile(r"\{\{[^}]*\}\}|\{[A-Za-z_]+(?:\.[A-Za-z_]+)*\}|__[A-Z][A-Z_]*__")
SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2, "PLAN": 3}


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- formatting
def fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_range(a: dt.date, b: dt.date) -> str:
    return fmt_day(a) if a == b else f"{fmt_day(a)}–{fmt_day(b)}"


def money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 1000 or float(x).is_integer() else f"${x:,.2f}"


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def ratio(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}x"


def div(a: float, b: float) -> float | None:
    return a / b if b else None


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


# --------------------------------------------------------------------------- loading
def read_csv(path: str, label: str) -> tuple:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            cols = [(c or "").strip() for c in (rd.fieldnames or [])]
            rows = []
            for i, r in enumerate(rd, start=2):
                rows.append((i, {(k or "").strip(): (v if isinstance(v, str) else "").strip()
                                 for k, v in r.items() if k is not None}))
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e}")
    except csv.Error as e:
        raise InputError(f"{label} {path} is not a valid CSV: {e}")
    if not cols:
        raise InputError(f"{label} {path} is empty")
    return cols, rows


_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?"
                       r"(Z|[+-](\d{2}):?(\d{2}))?$")


def parse_date(s: str, path: str, line: int, allow_time: bool = False) -> dt.date:
    """Parse the whole cell. YYYY-MM-DD always; with allow_time also an ISO timestamp with an optional
    offset (Z or ±HH:MM), whose calendar date is taken as written in that offset. Trailing text is rejected."""
    want = "YYYY-MM-DD or an ISO timestamp such as 2026-09-08T14:03:00-05:00" if allow_time else "YYYY-MM-DD"
    s = (s or "").strip()
    m = _DATE_RE.match(s)
    mt = None if m or not allow_time else _STAMP_RE.match(s)
    try:
        if m:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if mt:
            g = mt.groups()
            dt.time(int(g[3]), int(g[4]), int(g[5] or 0))           # validates hour/minute/second
            if g[7] is not None and (int(g[7]) > 14 or int(g[8]) > 59):
                raise ValueError("bad offset")
            return dt.date(int(g[0]), int(g[1]), int(g[2]))
    except ValueError:
        pass
    raise InputError(f"{path} line {line}: date must be {want}, got {s!r}")


def parse_num(s: str, path: str, line: int, col: str, integer: bool) -> float:
    if s == "":
        raise InputError(f"{path} line {line}: '{col}' is blank (use 0 for none)")
    try:
        v = strict_number(s, allow_negative=True, where=f"{path} line {line}, column '{col}'")
    except NumberFormatError as e:
        raise InputError(str(e))
    if v < 0:
        raise InputError(f"{path} line {line}: '{col}' is negative ({s})")
    if integer:
        if not v.is_integer():
            raise InputError(f"{path} line {line}: '{col}' must be a whole number, got {s!r}")
        return int(v)
    return v


def check_not_european(raw: list, cols: list, path: str) -> None:
    """Fail with a specific message when a numeric column is clearly 1.234,56-formatted."""
    for c in cols:
        if looks_european([r.get(c, "") for _, r in raw]):
            raise InputError(f"{path} column '{c}' looks European-formatted (1.234,56): more than half of its "
                             f"values use ',' as the decimal separator; {_DECIMAL_HINT}")


def load_events(path: str) -> tuple:
    cols, raw = read_csv(path, "events file")
    for c in ("date", "shopify_orders", "shopify_net_sales"):
        if c not in cols:
            raise InputError(f"events file is missing required column '{c}'")
    if not any(p[2] in cols for p in PLATFORMS):
        raise InputError("events file has no platform columns: need at least one of "
                         "meta_purchases, google_conversions, tiktok_purchases")
    for c in ("meta_browser_events", "meta_server_events", "meta_purchase_value"):
        if c in cols and "meta_purchases" not in cols:
            raise InputError(f"events file has '{c}' but not 'meta_purchases' (the deduplicated total)")
    ints = ["shopify_orders", "meta_browser_events", "meta_server_events"] + [p[2] for p in PLATFORMS]
    floats = ["shopify_net_sales"] + [p[3] for p in PLATFORMS]
    check_not_european(raw, [c for c in ints + floats if c in cols], path)
    days, seen = [], set()
    for line, r in raw:
        d = {"date": parse_date(r.get("date", ""), path, line)}
        if d["date"] in seen:
            raise InputError(f"{path} line {line}: duplicate date {d['date']}")
        seen.add(d["date"])
        for c in ints + floats:
            if c in cols:
                d[c] = parse_num(r.get(c, ""), path, line, c, c in ints)
        days.append(d)
    days.sort(key=lambda d: d["date"])
    for a, b in zip(days, days[1:]):
        if (b["date"] - a["date"]).days != 1:
            raise InputError(f"events file skips from {a['date']} to {b['date']}: one row per day, no gaps")
    days = days[-WINDOW_DAYS:]
    if len(days) < MIN_DAYS:
        raise InputError(f"events file has {len(days)} days; at least {MIN_DAYS} are needed ({WINDOW_DAYS} recommended)")
    if sum(d["shopify_orders"] for d in days) == 0:
        raise InputError("events file has 0 Shopify orders in the window")
    return days, cols


def load_orders(path: str | None) -> list | None:
    if not path:
        return None
    cols, raw = read_csv(path, "orders file")
    for c in ("order_id", "date", "landing_site"):
        if c not in cols:
            raise InputError(f"orders file is missing required column '{c}'")
    out, seen = [], {}
    for line, r in raw:
        oid = r.get("order_id", "")
        if oid and oid in seen:
            raise InputError(f"{path} line {line}: duplicate order_id {oid!r} (first on line {seen[oid]}); "
                             f"one row per order")
        seen[oid] = line
        out.append({"order_id": oid, "date": parse_date(r.get("date", ""), path, line, allow_time=True),
                    "landing_site": r.get("landing_site", ""), "referring_site": r.get("referring_site", ""),
                    "utm_source": r.get("utm_source", ""), "utm_medium": r.get("utm_medium", ""),
                    "utm_campaign": r.get("utm_campaign", "")})
    if not out:
        raise InputError("orders file has no rows")
    return out


def load_last_bfcm(path: str | None) -> tuple | None:
    if not path:
        return None
    cols, raw = read_csv(path, "last-BFCM file")
    for c in ("date", "shopify_net_sales"):
        if c not in cols:
            raise InputError(f"last-BFCM file is missing required column '{c}'")
    plats = [c[:-len("_claimed_revenue")] for c in cols if c.endswith("_claimed_revenue")]
    if not plats:
        raise InputError("last-BFCM file needs at least one '<platform>_claimed_revenue' column")
    num_cols = ["shopify_net_sales"] + [c for c in cols if c.endswith(("_claimed_revenue", "_spend", "_utm_sales"))]
    check_not_european(raw, num_cols, path)
    days, seen = [], set()
    for line, r in raw:
        d = {"date": parse_date(r.get("date", ""), path, line)}
        if d["date"].month not in (11, 12):
            raise InputError(f"{path} line {line}: last-BFCM date {d['date']} is not in November or December; "
                             f"export last year's BFCM week")
        if d["date"] in seen:
            raise InputError(f"{path} line {line}: duplicate date {d['date']}")
        seen.add(d["date"])
        for c in num_cols:
            d[c] = parse_num(r.get(c, ""), path, line, c, False)
        days.append(d)
    days.sort(key=lambda d: d["date"])
    if not days or sum(d["shopify_net_sales"] for d in days) == 0:
        raise InputError("last-BFCM file has no Shopify net sales")
    return days, plats, cols


# --------------------------------------------------------------------------- calendar
def bfcm_dates(as_of: dt.date) -> dict:
    def for_year(y: int) -> dict:
        nov1 = dt.date(y, 11, 1)
        thanksgiving = nov1 + dt.timedelta(days=(3 - nov1.weekday()) % 7 + 21)
        return {"thanksgiving": thanksgiving, "black_friday": thanksgiving + dt.timedelta(days=1),
                "cyber_monday": thanksgiving + dt.timedelta(days=4)}
    cal = for_year(as_of.year)
    if as_of > cal["cyber_monday"]:
        cal = for_year(as_of.year + 1)
    bf = cal["black_friday"]
    cal.update({"fix_by": bf - dt.timedelta(days=FIX_LEAD), "recheck": bf - dt.timedelta(days=RECHECK_LEAD),
                "budget_by": bf - dt.timedelta(days=BUDGET_LEAD), "freeze": bf - dt.timedelta(days=FREEZE_LEAD),
                "final": bf - dt.timedelta(days=FINAL_LEAD), "freeze_end": cal["cyber_monday"] + dt.timedelta(days=1),
                "days_to_bf": (bf - as_of).days})
    return cal


# --------------------------------------------------------------------------- 1. events
def finding(sev: str, section: str, key: str, title: str, detail: list, fix: str | None) -> dict:
    return {"severity": sev, "section": section, "key": key, "title": title, "detail": detail,
            "fix": fix, "state": "detected"}


def zero_runs(days: list, col: str) -> list:
    vals = [d[col] for d in days]
    if statistics.median(vals) < ZERO_MIN_MEDIAN:
        return []
    runs, cur = [], None
    for i, d in enumerate(days):
        if d[col] == 0 and d["shopify_orders"] >= ZERO_MIN_ORDERS:
            cur = cur or {"start": i}
            cur["end"] = i
        elif cur:
            runs.append(cur)
            cur = None
    if cur:
        runs.append(cur)
    for r in runs:
        r.update({"from": days[r["start"]]["date"], "to": days[r["end"]]["date"], "days": r["end"] - r["start"] + 1,
                  "orders": sum(d["shopify_orders"] for d in days[r["start"]:r["end"] + 1]),
                  "ongoing": r["end"] == len(days) - 1,
                  "recovered": None if r["end"] == len(days) - 1 else days[r["end"] + 1]["date"]})
    return runs


def dedup_rate(d: dict) -> float | None:
    b, s, p = d.get("meta_browser_events", 0), d.get("meta_server_events", 0), d["meta_purchases"]
    if b == 0 or s == 0 or d["shopify_orders"] < ZERO_MIN_ORDERS:
        return None
    return max(0, b + s - p) / min(b, s)


def match_severity(r: float | None) -> str:
    if r is None:
        return "CRITICAL"
    if MATCH_OK[0] <= r <= MATCH_OK[1]:
        return "OK"
    if MATCH_WARN[0] <= r <= MATCH_WARN[1]:
        return "WARNING"
    return "CRITICAL"


def value_severity(r: float | None) -> str:
    if r is None:
        return "OK"
    if VALUE_OK[0] <= r <= VALUE_OK[1]:
        return "OK"
    if VALUE_CRIT[0] <= r <= VALUE_CRIT[1]:
        return "WARNING"
    return "CRITICAL"


def worst(*sevs: str) -> str:
    order = ("CRITICAL", "WARNING", "INFO", "OK")
    return min(sevs, key=order.index)


def analyze_events(days: list, cols: list) -> dict:
    S = "events"
    recent = days[-RECENT_DAYS:]
    orders = sum(d["shopify_orders"] for d in days)
    sales = sum(d["shopify_net_sales"] for d in days)
    orders7 = sum(d["shopify_orders"] for d in recent)
    aov = sales / orders
    out = {"from": days[0]["date"], "to": days[-1]["date"], "days": len(days), "orders": orders,
           "net_sales": sales, "aov": aov, "orders_last7": orders7, "streams": [], "findings": [],
           "platforms": [p[1] for p in PLATFORMS if p[2] in cols]}
    F = out["findings"]
    out["streams"].append({"source": "Shopify orders", "last7": orders7, "per_order": None, "value_ratio": None,
                           "zero_days": None, "status": "—", "note": f"{money(aov)} net sales per order"})

    def value_ratio(count_col: str, value_col: str) -> tuple:
        if value_col not in cols:
            return None, None
        cnt = sum(d[count_col] for d in days)
        val = sum(d[value_col] for d in days)
        per = div(val, cnt)
        return (per / aov if per is not None else None), per

    def zero_findings(col: str, label: str, extra=None) -> tuple:
        runs = zero_runs(days, col)
        sev = "OK"
        if runs:
            r = runs[-1]
            sev = "CRITICAL" if r["ongoing"] else "WARNING"
            when = fmt_range(r["from"], r["to"])
            title = (f"{label} sent 0 purchase events on {when} ({plural(r['days'], 'day')}) "
                     f"while Shopify booked {plural(r['orders'], 'order')}.")
            detail = []
            if r["ongoing"]:
                title += " It is still at 0."
            else:
                title += f" It came back on {fmt_day(r['recovered'])}."
            if extra:
                detail.append(extra(r))
            if len(runs) > 1:
                detail.append("Earlier zero-event days in the window: "
                              + ", ".join(fmt_range(x["from"], x["to"]) for x in runs[:-1]) + ".")
            first = fmt_day(r["from"])
            fix = (f"Find what changed on or just before {first} (theme publish, app install or update, "
                   f"checkout or consent-banner change) and fix the {label} purchase event. "
                   f"Place a real test order and confirm it arrives." if r["ongoing"] else
                   f"Find what changed on or just before {first} (theme publish, app install or update, checkout "
                   f"change). Place a test order after every theme or app change from now on.")
            F.append(finding(sev, S, f"zero:{col}", title, detail, fix))
        return sev, sum(x["days"] for x in runs)

    # ---- Meta
    if "meta_purchases" in cols:
        p7 = sum(d["meta_purchases"] for d in recent)
        b7 = sum(d.get("meta_browser_events", 0) for d in recent)
        s7 = sum(d.get("meta_server_events", 0) for d in recent)
        match7 = div(p7, orders7)
        has_browser = "meta_browser_events" in cols
        has_server = "meta_server_events" in cols
        vr, per = value_ratio("meta_purchases", "meta_purchase_value")
        meta_sev = "OK"
        dedup_handled = False

        # Deduplication
        if has_browser and has_server:
            rates = [dedup_rate(d) for d in days]
            broken = [r is not None and r < DEDUP_MIN and days[i]["meta_purchases"] >= DEDUP_OVERCOUNT * days[i]["shopify_orders"]
                      for i, r in enumerate(rates)]
            runs, cur = [], None
            for i, bad in enumerate(broken):
                if bad:
                    cur = cur or [i, i]
                    cur[1] = i
                elif rates[i] is not None and cur:
                    runs.append(cur)
                    cur = None
            if cur:
                runs.append(cur)
            runs = [r for r in runs if r[1] - r[0] >= 1 or r[1] == len(days) - 1]
            out["dedup_rate_last7"] = div(sum(max(0, d.get("meta_browser_events", 0) + d.get("meta_server_events", 0)
                                                  - d["meta_purchases"]) for d in recent), min(b7, s7))
            if runs:
                a, z = runs[-1]
                ongoing = z == len(days) - 1
                start = days[a]["date"]
                before = [d for d in days[:a] if dedup_rate(d) is not None]
                bb = sum(d["meta_browser_events"] for d in before)
                bs = sum(d["meta_server_events"] for d in before)
                bp = sum(d["meta_purchases"] for d in before)
                bo = sum(d["shopify_orders"] for d in before)
                prior = (f"Before {fmt_day(start)} it merged {pct(div(max(0, bb + bs - bp), min(bb, bs)), 0)} of possible duplicates "
                         f"and counted {ratio(div(bp, bo))} Shopify orders." if before and min(bb, bs) else None)
                if ongoing:
                    merged = max(0, b7 + s7 - p7)
                    title = (f"Meta deduplication appears to have failed since {fmt_day(start)} ({plural(z - a + 1, 'day')}; "
                             f"server and browser events both counted). "
                             f"In the last 7 days Meta counted {p7:,} purchases for {orders7:,} Shopify orders ({ratio(match7)}).")
                    detail = [f"Browser pixel {b7:,} + Conversions API {s7:,} = {b7 + s7:,} events. Meta merged "
                              f"{'none' if merged == 0 else 'only ' + format(merged, ',')} as duplicates ({pct(div(merged, min(b7, s7)), 0)} of the {min(b7, s7):,} it could have)."]
                    if prior:
                        detail.append(prior)
                    detail.append("These aggregate counts suggest duplicate delivery; verify matching event IDs. Reported metrics may be "
                                  "distorted; counts alone do not establish the effect on attributed ROAS or CPA.")
                    fix = (f"Restore Meta deduplication: the browser and server Purchase events must carry the same "
                           f"event_name and event_id. Look for what changed on {fmt_day(start)} (a second server-side "
                           f"app, a GTM server container, or a pixel/app reinstall) and keep one CAPI source. "
                           f"Confirm in Events Manager → Purchase → Event deduplication.")
                    F.append(finding("CRITICAL", S, "dedup", title, detail, fix))
                    meta_sev = "CRITICAL"
                    dedup_handled = True
                else:
                    title = (f"Meta deduplication appears to have failed on {fmt_range(start, days[z]['date'])} "
                             f"({plural(z - a + 1, 'day')}; server and browser events both counted) and has since recovered.")
                    detail = [f"Reports and ROAS for those days likely overstate Meta purchases. Last 7 days: "
                              f"{p7:,} purchases for {orders7:,} orders ({ratio(match7)})."]
                    fix = (f"Find what changed on {fmt_day(start)} and again on {fmt_day(days[z + 1]['date'])} so it can't "
                           f"happen during BFCM. Keep one CAPI source and one event_id scheme.")
                    F.append(finding("WARNING", S, "dedup", title, detail, fix))
                    meta_sev = "WARNING"

        # Conversions API presence
        server_runs_ongoing = False
        if not has_server or s7 == 0:
            if has_server:
                runs = zero_runs(days, "meta_server_events")
                server_runs_ongoing = bool(runs and runs[-1]["ongoing"])
            title = ("Meta receives browser-pixel events only: no Conversions API (server) purchase events "
                     "in the last 7 days." if has_server else
                     "Meta receives browser-pixel events only: the export has no Conversions API (server) column.")
            fix = ("Review Conversions API support and merchant-approved consent settings in the Shopify Meta app. "
                   "Do not raise sharing automatically. Confirm permitted server events and deduplication in Events Manager.")
            F.append(finding("CRITICAL", S, "capi", title,
                             ["Browser-only tracking loses purchases to ad blockers, iOS and browser privacy limits, "
                              "and it gives Meta less to match on."], fix))
            meta_sev = "CRITICAL"
        elif div(s7, orders7) is not None and s7 / orders7 < CAPI_PARTIAL:
            F.append(finding("WARNING", S, "capi",
                             f"Meta Conversions API sent {s7:,} purchase events for {orders7:,} Shopify orders in the last "
                             f"7 days ({ratio(div(s7, orders7))}). Server events should cover almost every order.",
                             [], "Check the CAPI connection's event coverage in Events Manager and whether the "
                                 "server integration filters some orders (consent, sales channel, test mode)."))
            meta_sev = worst(meta_sev, "WARNING")

        # Match rate not explained by broken dedup
        msev = match_severity(match7)
        if not dedup_handled and msev != "OK" and p7 > 0:
            if match7 > MATCH_OK[1]:
                title = (f"Meta counted {p7:,} purchases for {orders7:,} Shopify orders in the last 7 days "
                         f"({ratio(match7)}) although deduplication looks normal.")
                fix = ("Look for a second Meta pixel, a duplicate Purchase event from a custom pixel or GTM, or "
                       "test orders. One pixel, one Purchase event per order.")
            else:
                title = (f"Meta saw only {p7:,} purchases for {orders7:,} Shopify orders in the last 7 days "
                         f"({ratio(match7)}). About {orders7 - p7:,} fewer purchases reached Meta than Shopify recorded.")
                fix = ("Review permitted Conversions API coverage without raising sharing automatically; check that a consent banner "
                       "isn't blocking the pixel for consenting shoppers, and check for checkouts outside the "
                       "Shopify checkout (headless, third-party).")
            F.append(finding(msev, S, "match:meta", title, [], fix))
            meta_sev = worst(meta_sev, msev)

        vsev = value_severity(vr)
        if vsev != "OK":
            F.append(finding(vsev, S, "value:meta",
                             f"Meta purchase value is {ratio(vr)} Shopify net sales per order "
                             f"({money(per)} per purchase vs {money(aov)}).", [],
                             "Reconcile matched orders, currency, refunds and revenue definitions before changing purchase values. "
                             "Aggregate averages cannot diagnose a value error or supply a ROAS correction factor."))
            meta_sev = worst(meta_sev, vsev)
        z_total = 0
        browser_sev = server_sev = "OK"
        if has_browser:
            browser_sev, z = zero_findings(
                "meta_browser_events", "Meta browser pixel",
                lambda r: (f"Conversions API events kept arriving on those days "
                           f"({sum(d.get('meta_server_events', 0) for d in days[r['start']:r['end'] + 1]):,}), "
                           f"so only the browser pixel broke.") if has_server else "")
            z_total += z
        if has_server and not server_runs_ongoing and s7 > 0:
            server_sev, z = zero_findings("meta_server_events", "Meta Conversions API")
            z_total += z
        out["streams"].append({"source": "Meta purchases (after dedup)", "last7": p7, "per_order": match7,
                               "value_ratio": vr, "zero_days": None,
                               "status": worst(meta_sev, msev if not dedup_handled else "OK"), "note": ""})
        if has_browser:
            out["streams"].append({"source": "Meta browser pixel", "last7": b7, "per_order": div(b7, orders7),
                                   "value_ratio": None, "zero_days": sum(r["days"] for r in zero_runs(days, "meta_browser_events")),
                                   "status": browser_sev, "note": ""})
        out["streams"].append({"source": "Meta Conversions API", "last7": s7 if has_server else None,
                               "per_order": div(s7, orders7) if has_server else None, "value_ratio": None,
                               "zero_days": (sum(r["days"] for r in zero_runs(days, "meta_server_events"))
                                             if has_server else None),
                               "status": "CRITICAL" if (not has_server or s7 == 0) else
                               worst(server_sev, "WARNING" if s7 / orders7 < CAPI_PARTIAL else "OK"),
                               "note": "" if has_server else "not in export"})
        out["meta_match_last7"] = match7

    # ---- Google Ads and TikTok
    for key, label, ccol, vcol, sitewide in PLATFORMS[1:]:
        if ccol not in cols:
            continue
        c7 = sum(d[ccol] for d in recent)
        per_order = div(c7, orders7)
        sev = "OK"
        if sitewide:
            msev = match_severity(per_order)
            if msev != "OK":
                more = per_order is not None and per_order > MATCH_OK[1]
                F.append(finding(msev, S, f"match:{key}",
                                 f"{label} counted {c7:,} purchases for {orders7:,} Shopify orders in the last 7 days "
                                 f"({ratio(per_order)}).", [],
                                 f"Check for duplicate {label} pixels or purchase events." if more else
                                 f"Turn on {label}'s server-side events (Events API) and check the pixel fires on "
                                 f"the Shopify thank-you page."))
                sev = msev
        vr, per = value_ratio(ccol, vcol)
        vsev = value_severity(vr)
        if vsev != "OK":
            F.append(finding(vsev, S, f"value:{key}",
                             f"{label} reports {money(per)} per purchase conversion against Shopify net sales of "
                             f"{money(aov)} per order ({ratio(vr)}).",
                             ["Aggregate averages can differ because attributed customers and revenue definitions differ. "
                              "This ratio cannot establish a tax/shipping error or a ROAS correction factor."],
                             f"Reconcile matched {label} orders, currency, discounts, tax, shipping, refunds and "
                             "attribution/reporting dates before proposing any purchase-value change. "
                             "Do not divide ROAS by the aggregate ratio."))
            sev = worst(sev, vsev)
        zsev, z = zero_findings(ccol, label)
        sev = worst(sev, zsev)
        out["streams"].append({"source": label + ("" if sitewide else " (attributed only)"), "last7": c7,
                               "per_order": per_order, "value_ratio": vr,
                               "zero_days": sum(r["days"] for r in zero_runs(days, ccol)),
                               "status": sev, "note": "" if sitewide else "attributed"})
    return out


# --------------------------------------------------------------------------- 2. UTMs
def order_attrs(o: dict) -> dict:
    parts = urlsplit(o["landing_site"])
    params = {k.lower(): v for k, v in parse_qs(parts.query, keep_blank_values=True).items()}

    def p(name: str) -> str:
        explicit = o.get(name, "")
        return unquote_plus(explicit).strip() if explicit else (params.get(name, [""])[0] or "").strip()

    clicks = sorted({lab for k, lab in CLICK_IDS if (params.get(k, [""])[0] or "").strip()})
    return {"path": parts.path or "/", "utm_source": p("utm_source"), "utm_medium": p("utm_medium"),
            "utm_campaign": p("utm_campaign"), "clicks": clicks, "referrer": bool(o.get("referring_site"))}


def analyze_utms(orders: list, window: tuple | None = None) -> dict:
    S = "utm"
    rows = [order_attrs(o) for o in orders]
    n = len(rows)
    no_utm = [r for r in rows if not r["utm_source"]]
    direct = [r for r in no_utm if not r["clicks"] and not r["referrer"]]
    out = {"orders": n, "from": min(o["date"] for o in orders), "to": max(o["date"] for o in orders),
           "no_utm": len(no_utm), "no_utm_share": len(no_utm) / n, "direct": len(direct),
           "direct_share": len(direct) / n, "findings": []}
    F = out["findings"]
    if window:
        outside = sorted(o["date"] for o in orders if not window[0] <= o["date"] <= window[1])
        out["orders_outside_window"] = len(outside)
        if outside:
            F.append(finding("INFO", S, "dates",
                             f"{plural(len(outside), 'order')} ({pct(len(outside) / n)}) are dated outside the events window "
                             f"({fmt_range(window[0], window[1])}), from {outside[0].isoformat()} to {outside[-1].isoformat()}.",
                             ["UTM shares below include them. Export orders for the same dates as the events file "
                              "so both checks describe the same period."], None))
    share = len(no_utm) / n
    sev = "CRITICAL" if share >= NO_UTM_CRIT else "WARNING" if share >= NO_UTM_WARN else "OK"
    if sev != "OK":
        F.append(finding(sev, S, "no_utm",
                         f"{len(no_utm):,} of {n:,} orders ({pct(share)}) have no utm_source.",
                         [f"{len(direct):,} of them ({pct(len(direct) / n)} of all orders) also had no referrer and no "
                          f"click ID, so every report will call them direct."],
                         "Put UTMs on every paid ad, email and SMS link using the naming standard in "
                         "references/playbook.md §4 (Meta URL parameters at ad level, Google final URL suffix, "
                         "TikTok URL parameters, Klaviyo UTM tracking on)."))

    # Click IDs without UTMs
    by_click = collections.Counter(c for r in no_utm for c in r["clicks"])
    out["click_ids_without_utm"] = dict(sorted(by_click.items()))
    non_google = {k: v for k, v in by_click.items() if k != "Google Ads"}
    tot = sum(non_google.values())
    if tot and tot >= CLICKID_WARN_MIN and tot / n >= CLICKID_WARN_SHARE:
        parts = ", ".join(f"{k} {v:,}" for k, v in sorted(non_google.items(), key=lambda kv: (-kv[1], kv[0])))
        detail = []
        if "Meta" in non_google:
            detail.append(f"{non_google['Meta']:,} carry a Meta click ID (fbclid), from ads or organic posts: Meta adds "
                          f"fbclid to organic Facebook/Instagram link clicks too, so these are not all untagged ads.")
        ad_ids = sorted(k for k in non_google if k != "Meta")
        if ad_ids:
            detail.append(f"{' and '.join(ad_ids)} click IDs come from ad clicks, so those ads appear to be live without "
                          f"URL parameters and Shopify can't credit the platform or campaign.")
        detail.append("Catalog, Advantage+, Spark and boosted-post ads are the usual gaps.")
        F.append(finding("WARNING", S, "clickid",
                         f"{tot:,} orders ({pct(tot / n)}) carry an ad-platform click ID in the landing URL "
                         f"but no UTMs: {parts}.",
                         detail,
                         f"Add URL parameters to every {' and '.join(sorted(non_google))} ad, including catalog, "
                         f"Advantage+, Spark and boosted-post ads. The click ID alone is not enough for Shopify reports."))
    if by_click.get("Google Ads"):
        F.append(finding("INFO", S, "gclid",
                         f"{by_click['Google Ads']:,} orders have a Google click ID (gclid/gbraid/wbraid) and no UTMs.",
                         ["Fine for Google Ads and GA4 with auto-tagging. Shopify's own reports need UTMs: add a final "
                          "URL suffix if you report from Shopify (references/playbook.md §4)."], None))

    # Source spelling variants
    groups: dict = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["utm_source"]:
            low = r["utm_source"].strip().lower()
            groups[SOURCE_ALIASES.get(low, low)][r["utm_source"]] += 1
    variants = []
    for canon, cnt in groups.items():
        if len(cnt) > 1:
            variants.append({"canonical": canon, "label": SOURCE_LABELS.get(canon, canon), "orders": sum(cnt.values()),
                             "spellings": sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))})
    variants.sort(key=lambda v: (-v["orders"], v["canonical"]))
    out["source_variants"] = variants
    if variants:
        detail = [f"{v['label']}: {len(v['spellings'])} spellings across {v['orders']:,} orders: "
                  + ", ".join(f"`{s}` {c:,}" for s, c in v["spellings"]) + f". Use `{v['canonical']}`."
                  for v in variants]
        F.append(finding("WARNING", S, "variants",
                         f"utm_source is spelled inconsistently for {plural(len(variants), 'channel')}, so reports "
                         f"split one channel into several rows.", detail,
                         "Standardise utm_source to one lowercase value per platform (" +
                         ", ".join(f"`{v['canonical']}`" for v in variants) +
                         ") in every ad template. Put placement (fb/ig) in utm_content, not utm_source."))

    # Unfilled macros
    macro = collections.Counter()
    macro_src = collections.Counter()
    for r in rows:
        hit = False
        for field in ("utm_source", "utm_medium", "utm_campaign"):
            for m in MACRO_RE.findall(r[field]):
                macro[(field, m)] += 1
                hit = True
        if hit:
            macro_src[(r["utm_source"] or "(none)").lower()] += 1
    n_macro = sum(1 for r in rows if any(MACRO_RE.search(r[f]) for f in ("utm_source", "utm_medium", "utm_campaign")))
    out["unfilled_macros"] = n_macro
    if n_macro:
        what = ", ".join(f"`{m}` in {f} ({c:,})" for (f, m), c in sorted(macro.items(), key=lambda kv: (-kv[1], kv[0])))
        srcs = ", ".join(f"{s} {c:,}" for s, c in sorted(macro_src.items(), key=lambda kv: (-kv[1], kv[0])))
        detail = [f"By utm_source: {srcs}."]
        if any(m.startswith("{{") for (_, m) in macro) and any(s in ("tiktok", "google") for s in macro_src):
            detail.append("`{{…}}` is Meta's macro syntax. TikTok uses `__CAMPAIGN_NAME__` and Google uses "
                          "`{campaignid}`, so a template copied from Meta arrives unfilled.")
        F.append(finding("WARNING", S, "macros",
                         f"{n_macro:,} orders carry an unfilled ad-platform macro in their UTMs: {what}.", detail,
                         "Fix the URL templates so each platform uses its own macro syntax "
                         "(references/playbook.md §4), then click a live ad and check the landing URL."))

    # Top landing pages for orders without UTMs
    paths = collections.Counter(r["path"] for r in no_utm)
    clicks_by_path = collections.Counter(r["path"] for r in no_utm if r["clicks"])
    out["top_no_utm_pages"] = [{"path": p, "orders": c, "with_click_id": clicks_by_path.get(p, 0)}
                               for p, c in sorted(paths.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    return out


# --------------------------------------------------------------------------- 3. last BFCM
def platform_label(key: str) -> str:
    return PLATFORM_LABELS.get(key, key.replace("_", " ").title())


def analyze_last_bfcm(data: tuple) -> dict:
    days, plats, cols = data
    actual = sum(d["shopify_net_sales"] for d in days)
    claims = {p: sum(d[f"{p}_claimed_revenue"] for d in days) for p in plats}
    total_claims = sum(claims.values())
    spend_cols = [c for c in cols if c.endswith("_spend")]
    spend = sum(d[c] for d in days for c in spend_cols)
    cal = bfcm_dates(days[0]["date"] - dt.timedelta(days=1)) if days else {}
    names = {}
    if cal:
        names = {cal["thanksgiving"]: "Thanksgiving", cal["black_friday"]: "Black Friday",
                 cal["cyber_monday"]: "Cyber Monday"}
    daily = []
    for d in days:
        c = sum(d[f"{p}_claimed_revenue"] for p in plats)
        daily.append({"date": d["date"], "name": names.get(d["date"], ""), "actual": d["shopify_net_sales"],
                      "claims": {p: d[f"{p}_claimed_revenue"] for p in plats}, "total_claims": c,
                      "ratio": div(c, d["shopify_net_sales"])})
    per = []
    for p in plats:
        row = {"platform": p, "label": platform_label(p), "claimed": claims[p], "share_of_actual": claims[p] / actual}
        if f"{p}_utm_sales" in cols:
            u = sum(d[f"{p}_utm_sales"] for d in days)
            row["utm_sales"] = u
            row["over_claim"] = div(claims[p], u)
        if f"{p}_spend" in cols:
            s = sum(d[f"{p}_spend"] for d in days)
            row["spend"] = s
            row["reported_roas"] = div(claims[p], s)
            if "utm_sales" in row:
                row["matched_roas"] = div(row["utm_sales"], s)
        per.append(row)
    peak = max((x for x in daily if x["ratio"] is not None), key=lambda x: (x["ratio"], x["date"]), default=None)
    # Platforms that reported attributed sales but have no spend in the file: MER would be overstated.
    missing_spend = [p["label"] for p in per if p["claimed"] > 0 and not p.get("spend")] if spend_cols else []
    return {"from": days[0]["date"], "to": days[-1]["date"], "actual": actual, "claims": claims,
            "total_claims": total_claims, "claim_ratio": total_claims / actual, "spend": spend if spend_cols else None,
            "mer": div(actual, spend) if spend_cols else None, "missing_spend": missing_spend,
            "mer_complete": bool(spend_cols) and not missing_spend,
            "daily": daily, "platforms": per, "peak": peak}


# --------------------------------------------------------------------------- verdict and fix list
def build_fixes(findings: list, cal: dict, as_of: dt.date, bfcm: dict | None, have_orders: bool) -> list:
    def by(d: dt.date) -> str:
        return fmt_day(d) if d >= as_of else f"now ({fmt_day(d)} has passed)"
    fixes = []
    for f in sorted(findings, key=lambda f: SEV_ORDER[f["severity"]]):
        if f["fix"] and f["severity"] in ("CRITICAL", "WARNING"):
            fixes.append({"severity": f["severity"], "text": f["fix"], "by": by(cal["fix_by"]), "state": "recommended"})
    bf = cal["black_friday"]
    if not have_orders:
        fixes.append({"severity": "PLAN", "by": by(cal["fix_by"]), "state": "recommended",
                      "text": "Export orders with landing site and UTMs (DATA_CONTRACT.md §2) and re-run to check "
                              "UTM hygiene."})
    fixes.append({"severity": "PLAN", "by": by(cal["recheck"]), "state": "recommended",
                  "text": f"Re-run this preflight. If it says READY, {fmt_day(cal['recheck'])}–"
                          f"{fmt_day(bf - dt.timedelta(days=1))} is your two weeks of clean data before Black Friday."})
    if bfcm and bfcm.get("mer") is not None and bfcm.get("missing_spend"):
        txt = (f"Add last year's spend for {' and '.join(bfcm['missing_spend'])} to the last-BFCM file and re-run. "
               f"Without it blended MER is overstated, so don't set budgets from it yet. Then set budgets off "
               f"blended MER, not platform ROAS.")
    elif bfcm and bfcm.get("mer") is not None:
        txt = (f"Set BFCM budgets and scaling rules off blended MER ({bfcm['mer']:.2f} last year), not platform "
               f"ROAS. Combine MER with contribution margin and cash constraints; attribution-gap ratios are not ROAS correction factors.")
    elif bfcm:
        txt = ("Set BFCM budgets off blended MER (Shopify net sales ÷ total ad spend), not platform ROAS. Add "
               "spend columns to the last-BFCM file to get last year's MER.")
    else:
        txt = ("Pull last year's BFCM week (Shopify net sales, each platform's claimed revenue and spend) and set "
               "budgets off blended MER, not platform ROAS.")
    fixes.append({"severity": "PLAN", "by": by(cal["budget_by"]), "state": "recommended", "text": txt})
    fixes.append({"severity": "PLAN", "by": by(cal["freeze"]), "state": "recommended",
                  "text": f"Change freeze until {fmt_day(cal['freeze_end'])}: no theme publishes, new apps, pixel, "
                          f"consent-banner or checkout changes. Any change after this needs a test order."})
    fixes.append({"severity": "PLAN", "by": by(cal["final"]), "state": "recommended",
                  "text": "Final preflight run. Then check Meta, Google Ads and TikTok purchases against Shopify "
                          "orders every morning through Cyber Monday."})
    return fixes


def analyze(days: list, cols: list, orders: list | None, last: tuple | None,
            account: str, as_of: dt.date | None) -> dict:
    as_of = as_of or days[-1]["date"]
    cal = bfcm_dates(as_of)
    ev = analyze_events(days, cols)
    ut = analyze_utms(orders, (ev["from"], ev["to"])) if orders is not None else None
    lb = analyze_last_bfcm(last) if last is not None else None
    findings = ev["findings"] + (ut["findings"] if ut else [])
    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], ("events", "utm").index(f["section"])))
    crit = sum(f["severity"] == "CRITICAL" for f in findings)
    warn = sum(f["severity"] == "WARNING" for f in findings)
    verdict = "NOT READY" if crit else "FIX BEFORE BFCM" if warn else "READY"
    not_checked = []
    if ut is None:
        not_checked.append("UTM hygiene (no --orders file)")
    if lb is None:
        not_checked.append("last BFCM claim gap (no --last-bfcm file)")
    return {"account": account, "as_of": as_of, "calendar": cal, "verdict": verdict, "critical": crit,
            "warnings": warn, "findings": findings, "events": ev, "utm": ut, "last_bfcm": lb,
            "not_checked": not_checked,
            "fixes": build_fixes(findings, cal, as_of, lb, ut is not None)}


# --------------------------------------------------------------------------- render
def render(a: dict) -> str:
    cal, ev, ut, lb = a["calendar"], a["events"], a["utm"], a["last_bfcm"]
    L = [f"## BFCM Tracking Preflight: {a['account']}, as of {a['as_of'].isoformat()}", ""]
    counts = []
    if a["critical"]:
        counts.append(plural(a["critical"], "critical issue"))
    if a["warnings"]:
        counts.append(plural(a["warnings"], "warning"))
    bf, cm = cal["black_friday"], cal["cyber_monday"]
    head = f"**Verdict: {a['verdict']}.** " + (" and ".join(counts) + ". " if counts else "No tracking issues found. ")
    head += (f"Black Friday is {fmt_day(bf)}, {bf.year} ({plural(cal['days_to_bf'], 'day')} away) "
             f"and Cyber Monday is {fmt_day(cm)}.")
    L.append(head)
    if a["verdict"] != "READY":
        L.append(f"Fix the items below by **{fmt_day(cal['fix_by'])}** so you have two weeks of clean data "
                 f"before Black Friday.")
    if a["not_checked"]:
        L.append("Not checked: " + "; ".join(a["not_checked"]) + ".")
    L.append("")
    ranked = [f for f in a["findings"] if f["severity"] in ("CRITICAL", "WARNING")]
    if ranked:
        L.append("**Issues, most severe first** (all detected, none changed)")
        for i, f in enumerate(ranked, 1):
            L.append(f"{i}. **{f['severity']}**: {f['title']}")
        L.append("")

    # 1. events
    L.append(f"### 1. Event coverage and deduplication ({fmt_range(ev['from'], ev['to'])}, {ev['days']} days)")
    L.append("")
    L.append(f"Shopify booked {ev['orders']:,} orders and {money(ev['net_sales'])} net sales "
             f"({ev['orders_last7']:,} orders in the last 7 days). Meta and TikTok count every site purchase, so "
             f"they should land at {MATCH_OK[0]:.2f}–{MATCH_OK[1]:.2f}x Shopify orders. Google Ads counts only "
             f"ad-attributed conversions, so it is checked for gaps and value, not count.")
    L.append("")
    for f in ev["findings"]:
        if f["severity"] in ("CRITICAL", "WARNING", "INFO"):
            L.append(f"- **{f['severity']}**: {f['title']}")
            for d in f["detail"]:
                if d:
                    L.append(f"  {d}")
    if not ev["findings"]:
        L.append("- All purchase streams are complete, deduplicated and valued in line with Shopify.")
    L.append("")
    L.append("| Source | Last 7 days | Per Shopify order | Value per event vs Shopify | Zero-event days | Status |")
    L.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for s in ev["streams"]:
        last7 = "—" if s["last7"] is None else f"{s['last7']:,}"
        per = ratio(s["per_order"]) + (" attributed" if s["note"] == "attributed" and s["per_order"] is not None else "")
        if s["source"] == "Shopify orders":
            per, val, zd = "—", s["note"], "—"
        else:
            val = ratio(s["value_ratio"]) if s["value_ratio"] is not None else "—"
            zd = "—" if s["zero_days"] is None else str(s["zero_days"])
        if s["note"] == "not in export":
            last7 = "not in export"
        L.append(f"| {s['source']} | {last7} | {per} | {val} | {zd} | {s['status']} |")
    L.append("")

    # 2. UTMs
    if ut is None:
        L.append("### 2. UTM hygiene")
        L.append("")
        L.append("Skipped: no orders export (--orders). UTM hygiene was not checked.")
        L.append("")
    else:
        L.append(f"### 2. UTM hygiene ({plural(ut['orders'], 'order')}, {fmt_range(ut['from'], ut['to'])})")
        L.append("")
        L.append(f"{ut['orders'] - ut['no_utm']:,} of {ut['orders']:,} orders ({pct(1 - ut['no_utm_share'])}) have a "
                 f"utm_source. {ut['direct']:,} ({pct(ut['direct_share'])}) have no UTM, no referrer and no click ID.")
        L.append("")
        for f in ut["findings"]:
            L.append(f"- **{f['severity']}**: {f['title']}")
            for d in f["detail"]:
                L.append(f"  {d}")
        if not [f for f in ut["findings"] if f["severity"] != "INFO"]:
            L.append("- UTMs are consistent: no spelling variants, unfilled macros or untagged ad clicks above threshold.")
        if ut["top_no_utm_pages"]:
            L.append("")
            L.append("| Landing page (orders without UTMs) | Orders | With an ad click ID |")
            L.append("| --- | ---: | ---: |")
            for p in ut["top_no_utm_pages"]:
                L.append(f"| {p['path']} | {p['orders']:,} | {p['with_click_id']:,} |")
        L.append("")

    # 3. last BFCM
    if lb is None:
        L.append("### 3. Last BFCM: platform claims vs Shopify")
        L.append("")
        L.append("Skipped: no last-BFCM export (--last-bfcm). Pull it before setting budgets; the gap between what "
                 "platforms claim and what Shopify books is common year to year.")
        L.append("")
    else:
        plats = [p["platform"] for p in lb["platforms"]]
        L.append(f"### 3. Last BFCM: platform claims vs Shopify ({fmt_range(lb['from'], lb['to'])}, {lb['from'].year})")
        L.append("")
        L.append(f"Platforms together reported **{money(lb['total_claims'])}** of attributed sales vs "
                 f"**{money(lb['actual'])}** in Shopify net sales ({ratio(lb['claim_ratio'])}). This is a reporting gap, "
                 f"not a return.")
        L.append("**Summing platform claims is not total sales.** Each platform counts every sale it touched, so one "
                 "order is claimed by several platforms at once. View-through credit, modelled conversions, "
                 "different attribution windows and gross-vs-net revenue definitions widen the gap further.")
        L.append("")
        hdr = "| Date | Shopify net sales | " + " | ".join(platform_label(p) + " claimed" for p in plats) + \
              " | All claims | Claims ÷ actual |"
        L.append(hdr)
        L.append("| --- | ---: | " + " | ".join("---:" for _ in plats) + " | ---: | ---: |")
        for d in lb["daily"]:
            label = f"{d['date'].strftime('%a')} {fmt_day(d['date'])}" + (f" ({d['name']})" if d["name"] else "")
            L.append(f"| {label} | {money(d['actual'])} | " + " | ".join(money(d["claims"][p]) for p in plats)
                     + f" | {money(d['total_claims'])} | {ratio(d['ratio'])} |")
        L.append(f"| **Total** | **{money(lb['actual'])}** | " + " | ".join(f"**{money(lb['claims'][p])}**" for p in plats)
                 + f" | **{money(lb['total_claims'])}** | **{ratio(lb['claim_ratio'])}** |")
        L.append("")
        if lb["peak"]:
            pk = lb["peak"]
            L.append(f"The aggregate attribution gap peaked on {fmt_day(pk['date'])}{' (' + pk['name'] + ')' if pk['name'] else ''} "
                     f"at {ratio(pk['ratio'])}.")
            L.append("")
        for p in lb["platforms"]:
            s = f"- **{p['label']}** claimed {money(p['claimed'])} ({pct(p['share_of_actual'])} of all Shopify sales on its own)."
            if p.get("over_claim") is not None:
                s += (f" Shopify booked {money(p['utm_sales'])} from orders with {p['label']} UTMs, so {p['label']} "
                      f"attribution-gap ratio is **{ratio(p['over_claim'])}** (platform revenue / UTM revenue).")
            if p.get("reported_roas") is not None:
                s += f" Reported ROAS {p['reported_roas']:.2f} on {money(p['spend'])} spend"
                s += (f"; Shopify UTM (last-click) ROAS {p['matched_roas']:.2f}." if p.get("matched_roas") is not None else ".")
            L.append(s)
        if any(p.get("over_claim") is not None for p in lb["platforms"]):
            L.append("- Shopify UTM sales and platform-attributed revenue use different attribution methods. Each ratio is descriptive, "
                     "not a planning discount, correction multiplier or measure of incremental contribution.")
        if lb["mer"] is not None and lb["missing_spend"]:
            have = [p["label"] for p in lb["platforms"] if p.get("spend")]
            L.append(f"- **Blended MER: {lb['mer']:.2f}, overstated** ({money(lb['actual'])} Shopify net sales ÷ "
                     f"{money(lb['spend'])} ad spend from {', '.join(have) if have else 'the spend columns'} only). "
                     f"Spend is missing for {', '.join(lb['missing_spend'])}, which reported attributed sales, so "
                     f"true MER is lower. Don't use this MER for budgets until that spend is added.")
        elif lb["mer"] is not None:
            L.append(f"- **Blended MER: {lb['mer']:.2f}** ({money(lb['actual'])} Shopify net sales ÷ "
                     f"{money(lb['spend'])} total ad spend).")
        L.append("")
        L.append("Future gaps may differ. Use blended MER with margin and cash constraints; it includes non-ad-driven sales and is not causal ad ROI.")
        L.append("")

    # 4. fix list
    L.append("### 4. Fix list and timeline (nothing has been changed)")
    L.append("")
    L.append("| # | Severity | What to do | State | By |")
    L.append("| ---: | --- | --- | --- | --- |")
    for i, f in enumerate(a["fixes"], 1):
        L.append(f"| {i} | {f['severity']} | {f['text']} | {f['state']} | {f['by']} |")
    L.append("")
    L.append(f"_Thresholds: Meta/TikTok purchases {MATCH_OK[0]:.2f}–{MATCH_OK[1]:.2f}x Shopify orders (critical "
             f"outside {MATCH_WARN[0]:.2f}–{MATCH_WARN[1]:.2f}x); dedup broken when under {DEDUP_MIN * 100:.0f}% of "
             f"duplicates merge; value per event {VALUE_OK[0]:.2f}–{VALUE_OK[1]:.2f}x Shopify net sales per order; "
             f"no-UTM orders warning ≥ {NO_UTM_WARN * 100:.0f}%, critical ≥ {NO_UTM_CRIT * 100:.0f}%. "
             f"States: detected → recommended → applied → verified. Read-only: no pixels, ad accounts or Shopify "
             f"settings were changed._")
    return "\n".join(L) + "\n"


def to_jsonable(x):
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v) for v in x]
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, float):
        return round(x, 4)
    return x


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--events", required=True, help="daily Shopify orders vs platform purchase events CSV")
    ap.add_argument("--orders", help="optional orders CSV with landing_site / UTMs")
    ap.add_argument("--last-bfcm", help="optional CSV of last year's BFCM days with platform claims")
    ap.add_argument("--account", default="Store", help="store name for the report title")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: last date in --events)")
    ap.add_argument("--json", help="also write the structured analysis to this path")
    args = ap.parse_args(argv)
    try:
        as_of = None
        if args.as_of:
            try:
                as_of = dt.date.fromisoformat(args.as_of)
            except ValueError:
                raise InputError("--as-of must be YYYY-MM-DD")
        days, cols = load_events(args.events)
        a = analyze(days, cols, load_orders(args.orders), load_last_bfcm(args.last_bfcm), args.account, as_of)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(to_jsonable(a), f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as e:
            print(f"error: cannot write --json file {args.json}: {e.strerror or e}", file=sys.stderr)
            return 2
    sys.stdout.write(render(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
