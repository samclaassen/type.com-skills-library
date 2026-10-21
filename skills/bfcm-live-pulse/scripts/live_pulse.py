#!/usr/bin/env python3
"""
BFCM Live Pulse: hourly monitor.

Reads today's row of the BFCM Holiday Budget Planner's --csv daily plan
(plan.csv), today's Shopify numbers by hour (hourly.csv), last year's matching
day by hour (ly_hourly.csv), ad spend so far today per platform
(spend_today.csv) and, optionally, discount-code use by hour
(discount_usage.csv) and stock on hand (inventory.csv). Prints a short
Slack-ready update: sales so far against the plan and the pace to end of day,
MER to date against the plan's guardrails (judged only at its check times),
spend pacing, and alerts for stalled orders, collapsing checkout conversion,
falling AOV, leaked discount codes and SKUs about to sell out.
See DATA_CONTRACT.md.

MER = Shopify net sales / total ad spend across every platform, both cut off at
the earliest spend read time. Net sales = gross sales - discounts, before
returns/refunds, excluding tax and shipping. Platform ROAS is never used.
Budget calls are proposals that need approval. Local files only: never calls an API, never changes a budget,
discount, product or store setting, never posts anywhere.

    python3 scripts/live_pulse.py --plan plan.csv --hourly hourly.csv \
        --ly-hourly ly_hourly.csv --spend spend_today.csv \
        [--discounts discount_usage.csv] [--inventory inventory.csv] \
        [--config config.json] [--as-of "YYYY-MM-DD HH:MM"] [--state state.json] \
        [--quiet] [--json pulse.json]

Python 3.8+, standard library only. Output is deterministic for given inputs.
Exit codes: 0 update written (in quiet mode, possibly nothing), 2 invalid input.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys

PLANNER_COLUMNS = ["date", "phase", "tag", "total_budget", "meta_budget", "google_budget", "tiktok_budget",
                   "other_budget", "proj_net_sales_low", "proj_net_sales_base", "proj_net_sales_high",
                   "proj_mer_base", "proj_contribution_base", "ly_date", "ly_net_sales", "ly_ad_spend",
                   "breakeven_mer", "target_mer", "pull_back_mer", "scale_mer", "check_times"]
CHANNELS = ("meta", "google", "tiktok", "other")
CHANNEL_NAMES = {"meta": "Meta", "google": "Google", "tiktok": "TikTok", "other": "Other"}
PLATFORM_CHANNEL = {"meta": "meta", "meta_ads": "meta", "facebook": "meta", "facebook_ads": "meta", "fb": "meta",
                    "instagram": "meta", "ig": "meta", "google": "google", "google_ads": "google",
                    "adwords": "google", "youtube": "google", "tiktok": "tiktok", "tiktok_ads": "tiktok"}
FUNNEL = ("sessions", "sessions_with_add_to_cart", "sessions_reached_checkout")
HOURLY_ALIASES = {"sessions_with_cart_additions": "sessions_with_add_to_cart",
                  "sessions_that_reached_checkout": "sessions_reached_checkout"}
SPEND_ALIASES = {"amount_spent": "spend", "cost": "spend", "read_time": "read_at"}
CODE_ALIASES = {"discount_code": "code", "orders": "uses", "baseline": "baseline_uses_per_hour",
                "baseline_per_hour": "baseline_uses_per_hour"}
STOCK_ALIASES = {"available": "on_hand", "inventory_quantity": "on_hand", "units_sold_last_3h": "sold_last_3h",
                 "product_title": "title", "name": "title"}
ROAS_HINTS = ("attributed", "conversion_value", "purchase_value", "platform_revenue")
ROAS_TOKEN = re.compile(r"(?:^|[^a-z])roas(?:$|[^a-z])", re.I)

THRESHOLDS = {                  # config.json "thresholds"; see DATA_CONTRACT.md
    "orders_stall_pct": 40,           # orders in the last hour below this % of expected ...
    "orders_stall_min_expected": 8,   # ... when at least this many were expected
    "checkout_drop_pct": 30,          # checkout conversion down more than this % ...
    "checkout_baseline_hours": 3,     # ... vs this many hours before
    "checkout_min_sessions": 20,      # sessions that reached checkout needed in the hour
    "aov_drop_pct": 15,               # AOV down more than this % vs last year's same hours ...
    "aov_hours": 2,                   # ... over this many hours
    "aov_min_orders": 20,             # orders needed today in those hours
    "code_multiple": 10,              # code used more than this x its baseline ...
    "code_min_uses": 25,              # ... and at least this many times in the last hour
    "cover_hours": 6,                 # SKU stock lasts less than this many hours at the last-3-hour pace
    "resolve_after_hours": 2,         # clean hours before a problem is reported resolved (once)
    "max_alerts": 5,
    "spend_pace_band_pct": 20,        # spend within +/- this % of expected counts as on pace
    "stale_spend_minutes": 120,       # spend figures read longer ago than this are flagged
    "min_pace_share_pct": 10,         # no pace until last year had this % of its day
    "pull_back_cut_pct": 20,          # the planner's pull-back cut
    "max_daily_increase_pct": 30,     # the planner's max day-over-day increase (scale step)
    "max_chars": 1800,
}
INT_THRESHOLDS = ("checkout_baseline_hours", "aov_hours", "resolve_after_hours", "max_alerts", "max_chars")
CONFIG_KEYS = ("store", "timezone", "tz_label", "currency_symbol", "utc_offset", "as_of", "quiet_mode", "thresholds",
               "paused_channels", "spend_timestamps_tz")
ZERO_SPEND_SHARE = 0.02         # a budgeted channel below 2% of its expected spend reads as missing (QA 3.1)
MAX_SPEND_SPREAD_MIN = 30       # spend reads within this many minutes of each other for a budget call (QA 3.2, S-L2)
MAX_FUTURE_SKEW_MIN = 5         # spend read at most this long after the as-of time (QA 3.3)
QUIET_KEYS = ("enabled", "summary_times")
SEVERITY = {"critical": 0, "warning": 1, "resolved": 2}
TYPE_ORDER = {"orders_stall": 0, "checkout": 1, "code_leak": 2, "aov": 3, "stock": 4}
LY_MIN_AOV_ORDERS = 10          # last-year orders needed in the AOV window
GROWTH_ORDERS_NOTE = "last year's orders that hour x the plan's growth"


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- formatting
def norm_key(s: str) -> str:
    return re.sub(r"[\s\-]+", "_", (s or "").strip().lower())


def half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def money(x: float, sym: str) -> str:
    return ("-" if x < 0 else "") + sym + f"{abs(x):,.0f}"


def money2(x: float, sym: str) -> str:
    return ("-" if x < 0 else "") + sym + f"{abs(x):,.2f}"


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def spct(x: float, d: int = 1) -> str:
    return f"{x * 100:+.{d}f}%"


def clock(h: int) -> str:
    """Hour boundary as a clock time: 0 -> midnight, 12 -> noon, 14 -> 2 pm."""
    h %= 24
    if h == 0:
        return "midnight"
    if h == 12:
        return "noon"
    return f"{h} am" if h < 12 else f"{h - 12} pm"


def time_label(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if m == 0 and h % 12 == 0:
        return clock(h)
    return f"{h % 12 or 12}:{m:02d} {'am' if h % 24 < 12 else 'pm'}"


def span(h1: int, h2: int) -> str:
    """Hours h1..h2 inclusive: 12, 13 -> noon–2 pm."""
    return f"{clock(h1)}–{clock(h2 + 1)}"


def day_label(d: dt.date) -> str:
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_min(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


# --------------------------------------------------------------------------- parsing
def parse_date(s, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(s).strip())
    except ValueError:
        raise InputError(f"{what}: '{s}' is not a YYYY-MM-DD date")


# ---- canonical strict number parser (QA fix 0.1), copied verbatim from shared/strict_num.py ----
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
# ---- end of canonical parser ----


def parse_cell(v, what: str, integer: bool = False, minimum: float | None = 0.0) -> float:
    """One numeric input cell through strict_number(); `what` names the file, line and column.
    minimum=None allows negatives (inventory on_hand only)."""
    if v is None or str(v).strip() == "":
        raise InputError(f"{what} is blank (write 0 if there were none)")
    try:
        x = strict_number(v, allow_negative=minimum is None, where=what)
    except NumberFormatError as e:
        raise InputError(str(e))
    if minimum is not None and x < minimum:
        raise InputError(f"{what} is below {minimum:g} ({v})")
    if integer:
        if x != int(x):
            raise InputError(f"{what}: '{v}' is not a whole number")
        return int(x)
    return x


def reject_european(path: str, rows: list, cols) -> None:
    """Fail with a specific message when a numeric column is clearly 1.234,56-style (QA fix 0.1)."""
    for c in cols:
        vals = [r[c] for _, r in rows if r.get(c, "") != ""]
        if looks_european(vals):
            raise InputError(f"{path}: column {c} looks like European number format (1.234,56); "
                             f"{_DECIMAL_HINT}")


def parse_offset(s: str) -> int:
    """'-05:00' / '-0500' / 'Z' -> minutes east of UTC."""
    if s in ("Z", "z"):
        return 0
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", s)
    if not m:
        raise ValueError(s)
    return (1 if m.group(1) == "+" else -1) * (int(m.group(2)) * 60 + int(m.group(3)))


STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2})(?::(\d{2})(?::(\d{2})(?:\.\d+)?)?)?)?\s*(Z|z|[+-]\d{2}:?\d{2})?$")
CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")


def parse_stamp(value: str, what: str, store_offset: int | None, default_date: dt.date | None) -> dt.datetime:
    """Store-time datetime from 'HH:MM', 'H', 'YYYY-MM-DD HH:MM[:SS]', 'YYYY-MM-DDTHH[...]', with an
    optional UTC offset (converted with config utc_offset)."""
    v = str(value).strip()
    m = CLOCK_RE.match(v)
    if m and default_date is not None:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if h > 23 or mi > 59:
            raise InputError(f"{what}: '{value}' is not a time of day")
        return dt.datetime.combine(default_date, dt.time(h, mi))
    m = STAMP.match(v)
    if not m or m.group(2) is None:
        raise InputError(f"{what}: '{value}' is not a time (use HH:MM or YYYY-MM-DD HH:MM, store time)")
    try:
        d = dt.date.fromisoformat(m.group(1))
        stamp = dt.datetime.combine(d, dt.time(int(m.group(2)), int(m.group(3) or 0), int(m.group(4) or 0)))
    except ValueError:
        raise InputError(f"{what}: '{value}' is not a valid date and time")
    if m.group(5):
        if store_offset is None:
            raise InputError(f"{what}: '{value}' carries a UTC offset; set utc_offset in config.json "
                             "(for example \"-05:00\") or write store time")
        stamp = stamp - dt.timedelta(minutes=parse_offset(m.group(5))) + dt.timedelta(minutes=store_offset)
    return stamp


def parse_hour_cell(value: str, what: str, day: dt.date, store_offset: int | None) -> int:
    v = str(value).strip()
    if re.fullmatch(r"\d{1,2}", v):
        h = int(v)
        if h > 23:
            raise InputError(f"{what}: hour '{value}' is not 0–23")
        return h
    stamp = parse_stamp(v, what, store_offset, day)
    if stamp.minute or stamp.second:
        raise InputError(f"{what}: '{value}' does not start on the hour")
    if stamp.date() != day:
        raise InputError(f"{what}: '{value}' is not on {day}")
    return stamp.hour


def parse_clock_list(value, what: str) -> list[int]:
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[;,]", value) if p.strip()]
    elif isinstance(value, list):
        parts = [str(p).strip() for p in value]
    else:
        raise InputError(f"{what} must be a list of HH:MM times")
    out = []
    for p in parts:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", p)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise InputError(f"{what}: '{p}' is not an HH:MM time")
        out.append(int(m.group(1)) * 60 + int(m.group(2)))
    if not out:
        raise InputError(f"{what} is empty")
    return sorted(set(out))


def read_table(path: str, what: str, aliases: dict | None = None) -> tuple[list[str], list[tuple[int, dict]]]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            raw = list(csv.reader(f))
    except OSError as e:
        raise InputError(f"cannot read the {what} file {path}: {e.strerror or e}")
    except UnicodeDecodeError:
        raise InputError(f"{path} is not UTF-8 text")
    except csv.Error as e:
        raise InputError(f"{path}: not a readable CSV ({e})")
    if not raw or not any(c.strip() for c in raw[0]):
        raise InputError(f"{path} is empty (a header row is required)")
    header = [norm_key(c) for c in raw[0]]
    for alias, canon in (aliases or {}).items():
        if alias in header:
            if canon in header:
                raise InputError(f"{path} has both '{alias}' and '{canon}' columns; keep one")
            header[header.index(alias)] = canon
    seen = set()
    for c in header:
        if c and c in seen:
            raise InputError(f"{path} has two '{c}' columns")
        seen.add(c)
    rows = []
    for i, r in enumerate(raw[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        if len(r) > len(header) and any(c.strip() for c in r[len(header):]):
            raise InputError(f"{path} line {i}: more cells than header columns")
        rows.append((i, {header[j]: (r[j].strip() if j < len(r) else "") for j in range(len(header)) if header[j]}))
    return header, rows


def need_columns(path: str, header: list[str], cols) -> None:
    missing = [c for c in cols if c not in header]
    if missing:
        raise InputError(f"{path} is missing column(s): {', '.join(missing)}")


# --------------------------------------------------------------------------- loading
def load_config(path: str | None) -> dict:
    cfg = {"store": "Your store", "timezone": "", "tz_label": "store time", "currency_symbol": "$",
           "utc_offset": None, "as_of": None, "quiet_mode": {"enabled": False, "summary_times": None},
           "thresholds": dict(THRESHOLDS), "paused_channels": [], "spend_timestamps_tz": "offset"}
    if not path:
        return cfg
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except OSError as e:
        raise InputError(f"cannot read the config file {path}: {e.strerror or e}")
    except ValueError as e:
        raise InputError(f"{path} is not valid JSON ({e})")
    if not isinstance(raw, dict):
        raise InputError(f"{path} must hold a JSON object")
    unknown = [k for k in raw if k not in CONFIG_KEYS]
    if unknown:
        raise InputError(f"{path}: unknown key(s) {', '.join(sorted(unknown))} (allowed: {', '.join(CONFIG_KEYS)})")
    for k in ("store", "timezone", "tz_label", "currency_symbol"):
        if k in raw:
            if not isinstance(raw[k], str):
                raise InputError(f"{path}: {k} must be text")
            cfg[k] = raw[k]
    if raw.get("utc_offset") is not None:
        try:
            cfg["utc_offset"] = parse_offset(str(raw["utc_offset"]))
        except ValueError:
            raise InputError(f"{path}: utc_offset '{raw['utc_offset']}' is not like \"-05:00\"")
    if raw.get("as_of") is not None:
        cfg["as_of"] = str(raw["as_of"])
    pc = raw.get("paused_channels", [])
    if not isinstance(pc, list) or not all(isinstance(x, str) for x in pc):
        raise InputError(f"{path}: paused_channels must be a list of channel names, e.g. [\"meta\"]")
    for x in pc:
        k = norm_key(x)
        ch = k if k in CHANNELS else PLATFORM_CHANNEL.get(k)
        if ch is None:
            raise InputError(f"{path}: paused_channels entry '{x}' is not one of {', '.join(CHANNELS)}")
        if ch not in cfg["paused_channels"]:
            cfg["paused_channels"].append(ch)
    tzmode = raw.get("spend_timestamps_tz", "offset")
    if tzmode not in ("offset", "store"):
        raise InputError(f"{path}: spend_timestamps_tz must be \"offset\" (default: read_at carries a UTC offset "
                         "or Z) or \"store\" (read_at is already store time)")
    cfg["spend_timestamps_tz"] = tzmode
    q = raw.get("quiet_mode", {})
    if not isinstance(q, dict):
        raise InputError(f"{path}: quiet_mode must be an object")
    bad = [k for k in q if k not in QUIET_KEYS]
    if bad:
        raise InputError(f"{path}: unknown quiet_mode key(s) {', '.join(sorted(bad))} (allowed: {', '.join(QUIET_KEYS)})")
    if "enabled" in q:
        if not isinstance(q["enabled"], bool):
            raise InputError(f"{path}: quiet_mode.enabled must be true or false")
        cfg["quiet_mode"]["enabled"] = q["enabled"]
    if q.get("summary_times") is not None:
        cfg["quiet_mode"]["summary_times"] = parse_clock_list(q["summary_times"], f"{path}: quiet_mode.summary_times")
    t = raw.get("thresholds", {})
    if not isinstance(t, dict):
        raise InputError(f"{path}: thresholds must be an object")
    for k, v in t.items():
        if k not in THRESHOLDS:
            raise InputError(f"{path}: unknown threshold '{k}' (allowed: {', '.join(THRESHOLDS)})")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise InputError(f"{path}: threshold {k} must be a number >= 0 (got {v!r})")
        if k in INT_THRESHOLDS and (v != int(v) or v < 1):
            raise InputError(f"{path}: threshold {k} must be a whole number >= 1 (got {v!r})")
        cfg["thresholds"][k] = int(v) if k in INT_THRESHOLDS else float(v)
    if cfg["thresholds"]["max_chars"] < 300:
        raise InputError(f"{path}: threshold max_chars must be at least 300")
    return cfg


def opt_num(r: dict, col: str, what: str, minimum: float | None = 0.0) -> float | None:
    v = r.get(col, "")
    return None if v == "" else parse_cell(v, what, minimum=minimum)


def load_plan(path: str, day: dt.date, flags: dict) -> dict:
    header, rows = read_table(path, "plan")
    need_columns(path, header, ["date", "total_budget", "proj_net_sales_base"])
    by_date = {}
    for i, r in rows:
        d = parse_date(r["date"], f"{path} line {i}")
        if d in by_date:
            raise InputError(f"{path} line {i}: duplicate date {d}")
        by_date[d] = (i, r)
    if day not in by_date:
        span_txt = f" (it covers {min(by_date)} to {max(by_date)})" if by_date else ""
        raise InputError(f"{path} has no row for {day}, the as-of date{span_txt}")
    i, r = by_date[day]
    reject_european(path, rows, [c for c in header if c.endswith("_budget") or c.startswith("proj_")
                                 or c.endswith("_mer") or c in ("ly_net_sales", "ly_ad_spend")])
    w = f"{path} line {i}"
    p = {"date": day, "line": i, "tag": r.get("tag", ""), "phase": r.get("phase", ""),
         "total_budget": parse_cell(r["total_budget"], f"{w}, column total_budget"),
         "base": parse_cell(r["proj_net_sales_base"], f"{w}, column proj_net_sales_base"),
         "low": opt_num(r, "proj_net_sales_low", f"{w}, column proj_net_sales_low"),
         "high": opt_num(r, "proj_net_sales_high", f"{w}, column proj_net_sales_high"),
         "ly_net_sales": opt_num(r, "ly_net_sales", f"{w}, column ly_net_sales"),
         "breakeven_mer": opt_num(r, "breakeven_mer", f"{w}, column breakeven_mer"),
         "target_mer": opt_num(r, "target_mer", f"{w}, column target_mer"),
         "header": header, "is_planner": header == PLANNER_COLUMNS, "notes": [], "defaults": []}
    if p["base"] <= 0:
        raise InputError(f"{w}: proj_net_sales_base must be above 0")
    if p["low"] is not None and p["low"] > p["base"] or p["high"] is not None and p["high"] < p["base"]:
        raise InputError(f"{w}: expected proj_net_sales_low <= proj_net_sales_base <= proj_net_sales_high")
    budget_cols = [f"{c}_budget" for c in CHANNELS]
    if any(c in header for c in budget_cols):
        p["budgets"] = {c: (parse_cell(r[f"{c}_budget"], f"{w}, column {c}_budget") if r.get(f"{c}_budget", "") != "" else 0.0)
                        for c in CHANNELS}
        if abs(sum(p["budgets"].values()) - p["total_budget"]) > 1.0:
            p["notes"].append(f"plan channel budgets add up to {sum(p['budgets'].values()):,.0f}, "
                              f"not total_budget {p['total_budget']:,.0f}")
    else:
        p["budgets"] = None
    mer_plan = opt_num(r, "proj_mer_base", f"{w}, column proj_mer_base")
    if mer_plan is None and p["total_budget"] > 0:
        mer_plan = p["base"] / p["total_budget"]
    p["plan_mer"] = mer_plan

    # last year's day
    if flags.get("ly_date"):
        p["ly_date"] = parse_date(flags["ly_date"], "--ly-date")
    elif r.get("ly_date", ""):
        p["ly_date"] = parse_date(r["ly_date"], f"{w}: ly_date")
    else:
        p["ly_date"] = day - dt.timedelta(days=364)
        p["defaults"].append(f"last year's day {p['ly_date']} (same weekday, 364 days back)")

    # guardrails: flags win, then the plan's columns, then documented defaults
    def guard(col: str) -> float | None:
        if flags.get(col) is not None:
            v = parse_cell(flags[col], f"--{col.replace('_', '-')}")
            if r.get(col, "") != "":
                p["notes"].append(f"{col} {v:.2f} from the command line (plan says {r[col]})")
            return v
        return opt_num(r, col, f"{w}, column {col}")

    pb, sc = guard("pull_back_mer"), guard("scale_mer")
    if (pb is None or sc is None) and mer_plan is None:
        raise InputError(f"{w}: no pull_back_mer/scale_mer and no budget to work out a plan MER; "
                         "pass --pull-back-mer and --scale-mer")
    if pb is None:
        be = p["breakeven_mer"] or 0.0
        pb = round(max(be, 0.85 * mer_plan), 2)
        p["defaults"].append(f"pull back below {pb:.2f} (0.85 x plan MER {mer_plan:.2f}"
                             + (f", not below breakeven {be:.2f})" if be else ")"))
    if sc is None:
        tg = p["target_mer"] or 0.0
        sc = round(max(tg, 1.10 * mer_plan), 2)
        p["defaults"].append(f"scale above {sc:.2f} (1.10 x plan MER {mer_plan:.2f}"
                             + (f", not below target {tg:.2f})" if tg else ")"))
    if pb >= sc:
        raise InputError(f"{w}: pull-back MER {pb:.2f} must be below scale MER {sc:.2f}")
    p["pull_back_mer"], p["scale_mer"] = pb, sc
    if flags.get("check_times"):
        p["check_times"] = parse_clock_list(flags["check_times"], "--check-times")
    elif r.get("check_times", ""):
        p["check_times"] = parse_clock_list(r["check_times"], f"{w}: check_times")
    else:
        p["check_times"] = [12 * 60, 18 * 60]
        p["defaults"].append("checks at noon and 6 pm (the planner's cyber-week times)")

    # neighbours: yesterday's check times (previous check slot) and tomorrow's budget (scale step)
    y = by_date.get(day - dt.timedelta(days=1))
    p["yesterday_checks"] = p["check_times"]
    if y and y[1].get("check_times", "") and not flags.get("check_times"):
        p["yesterday_checks"] = parse_clock_list(y[1]["check_times"], f"{path} line {y[0]}: check_times")
    t = by_date.get(day + dt.timedelta(days=1))
    p["tomorrow"] = None
    if t and t[1].get("total_budget", "") != "":
        p["tomorrow"] = {"date": day + dt.timedelta(days=1),
                         "total_budget": parse_cell(t[1]["total_budget"], f"{path} line {t[0]}, column total_budget")}
    return p


def load_hourly(path: str, what: str, day: dt.date, hours: int, offset: int | None) -> dict:
    """hours = number of complete hours needed (24 for last year's day)."""
    header, rows = read_table(path, what, HOURLY_ALIASES)
    need_columns(path, header, ["hour", "orders", "net_sales"])
    funnel = [c for c in FUNNEL if c in header]
    reject_european(path, rows, ["orders", "net_sales"] + funnel)
    data, ignored = {}, 0
    for i, r in rows:
        w = f"{path} line {i}"
        if r.get("date", ""):
            d = parse_date(r["date"], w)
            if d != day:
                raise InputError(f"{w}: date {d} is not {day}" + (" (the plan's ly_date)" if hours == 24 else ""))
        h = parse_hour_cell(r["hour"], w, day, offset)
        if h in data:
            raise InputError(f"{w}: duplicate row for hour {h} ({clock(h)})")
        if h >= hours:
            data[h] = None
            ignored += 1
            continue
        rec = {"orders": parse_cell(r["orders"], f"{w}, column orders", integer=True),
               "sales": parse_cell(r["net_sales"], f"{w}, column net_sales", minimum=None)}
        if rec["sales"] < 0:
            raise InputError(f"{w}, column net_sales is negative ({r['net_sales']}): hourly net sales must be "
                             "gross sales minus discounts, before returns/refunds (excluding tax and shipping), "
                             "so they can't go below 0; export them without refunds netted out")
        for c in funnel:
            rec[c] = parse_cell(r[c], f"{w}, column {c}", integer=True)
        if "sessions" in rec:
            for c in ("sessions_with_add_to_cart", "sessions_reached_checkout"):
                if c in rec and rec[c] > rec["sessions"]:
                    raise InputError(f"{w}: {c} ({rec[c]}) is more than sessions ({rec['sessions']})")
        data[h] = rec
    missing = [h for h in range(hours) if h not in data]
    if missing:
        h = missing[0]
        raise InputError(f"{path} has no row for hour {h} ({clock(h)}–{clock(h + 1)})"
                         + (f" and {len(missing) - 1} other hour(s)" if len(missing) > 1 else "")
                         + "; every hour needs a row (write 0 orders and 0 net_sales if nothing sold)")
    out = {"orders": [data[h]["orders"] for h in range(hours)],
           "sales": [data[h]["sales"] for h in range(hours)], "ignored_rows": ignored}
    for c in FUNNEL:
        out[c] = [data[h][c] for h in range(hours)] if c in funnel else None
    return out


def load_spend(path: str, as_of: dt.datetime, offset: int | None,
               tz_mode: str = "offset") -> tuple[list[dict], list[str], list[str]]:
    header, rows = read_table(path, "spend", SPEND_ALIASES)
    need_columns(path, header, ["platform", "spend", "read_at"])
    reject_european(path, rows, ["spend"])
    warnings = []
    ignored = [c for c in header if c not in ("platform", "spend", "read_at", "date")
               and (ROAS_TOKEN.search(c) or any(k in c for k in ROAS_HINTS))]
    latest = {}
    limit = as_of + dt.timedelta(minutes=MAX_FUTURE_SKEW_MIN)
    for i, r in rows:
        w = f"{path} line {i}"
        name = r["platform"].strip()
        if not name:
            raise InputError(f"{w}: platform is blank")
        if tz_mode != "store":
            m = STAMP.match(r["read_at"].strip())
            if not (m and m.group(5)):
                raise InputError(f"{w}, column read_at: '{r['read_at']}' has no UTC offset. Ad platforms often "
                                 "report UTC; write it like 2026-11-27T13:50-05:00 or 2026-11-27T18:50Z, or set "
                                 "\"spend_timestamps_tz\": \"store\" in config.json if these times are already "
                                 "store time")
        stamp = parse_stamp(r["read_at"], f"{w}, column read_at", offset, as_of.date())
        if stamp.date() != as_of.date():
            raise InputError(f"{w}: spend read on {stamp.date()}; it must be today's ({as_of.date()}) spend so far")
        if stamp > limit:
            raise InputError(f"{w}: read at {hhmm(stamp.hour * 60 + stamp.minute)} store time, more than "
                             f"{MAX_FUTURE_SKEW_MIN} minutes after the as-of time {hhmm(as_of.hour * 60 + as_of.minute)} "
                             "(check the UTC offset)")
        rec = {"platform": name, "key": norm_key(name), "spend": parse_cell(r["spend"], f"{w}, column spend"),
               "read": stamp.hour * 60 + stamp.minute}
        rec["channel"] = PLATFORM_CHANNEL.get(rec["key"], "other")
        rec["line"] = i
        old = latest.get(rec["key"])
        if old and old["read"] == rec["read"]:
            raise InputError(f"{w}: {name} appears twice with the same read time (line {old['line']}); spend is "
                             "cumulative per platform, so sum separate accounts into one row or keep one read")
        if old is None or rec["read"] > old["read"]:
            latest[rec["key"]] = rec
    if not latest:
        raise InputError(f"{path} has no spend rows (write 0 for a platform that hasn't spent yet)")
    by_ch = {}
    for rec in latest.values():
        if rec["channel"] != "other":
            by_ch.setdefault(rec["channel"], []).append(rec["platform"])
    for ch in CHANNELS:
        if len(by_ch.get(ch, [])) > 1:
            warnings.append(f"{' and '.join(sorted(by_ch[ch]))} are both counted as {CHANNEL_NAMES[ch]}; if they are "
                            "the same ad account, its spend is counted twice and MER reads low")
    return (sorted(latest.values(), key=lambda p: (CHANNELS.index(p["channel"]), p["key"])), ignored, warnings)


def load_discounts(path: str, day: dt.date, hours: int, offset: int | None) -> dict:
    header, rows = read_table(path, "discount usage", CODE_ALIASES)
    need_columns(path, header, ["code", "hour", "uses", "baseline_uses_per_hour"])
    reject_european(path, rows, ["uses", "baseline_uses_per_hour"])
    codes, seen = {}, set()
    for i, r in rows:
        w = f"{path} line {i}"
        code = r["code"].strip().upper()
        if not code:
            continue                                    # orders without a code
        h = parse_hour_cell(r["hour"], w, day, offset)
        if (code, h) in seen:
            raise InputError(f"{w}: duplicate row for {code} at hour {h}")
        seen.add((code, h))
        base = parse_cell(r["baseline_uses_per_hour"], f"{w}, column baseline_uses_per_hour")
        c = codes.setdefault(code, {"uses": [0] * 24, "baseline": base, "line": i})
        if abs(c["baseline"] - base) > 1e-9:
            raise InputError(f"{w}: {code} has baseline {base:g} here but {c['baseline']:g} on line {c['line']}")
        if h < hours:
            c["uses"][h] = parse_cell(r["uses"], f"{w}, column uses", integer=True)
    return codes


def load_inventory(path: str) -> list[dict]:
    header, rows = read_table(path, "inventory", STOCK_ALIASES)
    need_columns(path, header, ["sku", "on_hand", "sold_last_3h"])
    reject_european(path, rows, ["on_hand", "sold_last_3h"])
    out, seen = [], set()
    for i, r in rows:
        w = f"{path} line {i}"
        sku = r["sku"].strip()
        if not sku:
            raise InputError(f"{w}: sku is blank")
        if sku in seen:
            raise InputError(f"{w}: duplicate sku {sku}")
        seen.add(sku)
        out.append({"sku": sku, "title": r.get("title", "").strip() or sku,
                    "on_hand": parse_cell(r["on_hand"], f"{w}, column on_hand", integer=True, minimum=None),
                    "sold": parse_cell(r["sold_last_3h"], f"{w}, column sold_last_3h", integer=True)})
    return out


def load_state(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {"version": 1, "checks": []}
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read the state file {path} ({e}); delete it to start fresh")
    if not isinstance(s, dict) or not isinstance(s.get("checks"), list):
        raise InputError(f"{path} is not a Live Pulse state file (no 'checks' list); delete it to start fresh")
    for c in s["checks"]:
        if not (isinstance(c, dict) and isinstance(c.get("date"), str) and isinstance(c.get("time"), str)
                and isinstance(c.get("mer"), (int, float)) and isinstance(c.get("scale_mer"), (int, float))):
            raise InputError(f"{path}: malformed check entry {c!r}; delete the file to start fresh")
    return s


# --------------------------------------------------------------------------- analysis
def analyze(cfg: dict, plan: dict, td: dict, ly: dict, spend: list[dict], spend_ignored: list[str],
            codes: dict | None, stock: list[dict] | None, state: dict, as_of: dt.datetime) -> dict:
    th, sym = cfg["thresholds"], cfg["currency_symbol"]
    M = as_of.hour * 60 + as_of.minute
    H = as_of.hour                                       # complete hours: 0 .. H-1
    day = as_of.date()
    notes = list(plan["notes"])
    if td["ignored_rows"]:
        notes.append(f"ignored {td['ignored_rows']} hourly row(s) at or after {clock(H)} (not complete yet)")
    ly_total, ly_orders_total = sum(ly["sales"]), sum(ly["orders"])
    if ly_total <= 0 or ly_orders_total <= 0:
        raise InputError("ly_hourly has no sales or no orders; it can't give an hourly curve")
    if plan["ly_net_sales"] and abs(ly_total / plan["ly_net_sales"] - 1) > 0.01:
        notes.append(f"ly_hourly sums to {money(ly_total, sym)} but the plan says last year was "
                     f"{money(plan['ly_net_sales'], sym)}")
    cum = [0.0]
    for s in ly["sales"]:
        cum.append(cum[-1] + s / ly_total)

    def share_at(minutes: int) -> float:
        h = minutes // 60
        if h >= 24:
            return 1.0
        return cum[h] + (minutes % 60) / 60 * (cum[h + 1] - cum[h])

    a = {"store": cfg["store"], "tz": cfg["tz_label"], "sym": sym, "date": day, "as_of_min": M, "hours": H,
         "tag": plan["tag"], "ly_date": plan["ly_date"], "th": th}
    a["plan"] = {"format": "bfcm-budget-planner" if plan["is_planner"] else "other", "line": plan["line"],
                 "phase": plan["phase"], "total_budget": plan["total_budget"], "budgets": plan["budgets"],
                 "low": plan["low"], "base": plan["base"], "high": plan["high"], "plan_mer": plan["plan_mer"],
                 "ly_date": plan["ly_date"], "breakeven_mer": plan["breakeven_mer"],
                 "target_mer": plan["target_mer"], "pull_back_mer": plan["pull_back_mer"],
                 "scale_mer": plan["scale_mer"], "check_times": [hhmm(t) for t in plan["check_times"]],
                 "defaults_applied": plan["defaults"], "tomorrow": plan["tomorrow"]}

    # ---- sales, orders, AOV, pace
    sales, orders = sum(td["sales"][:H]), sum(td["orders"][:H])
    ly_sales, ly_orders = sum(ly["sales"][:H]), sum(ly["orders"][:H])
    share = cum[H]
    proj = sales / share if share > 0 and share * 100 >= th["min_pace_share_pct"] else None
    band = None
    if proj is not None:
        lo, hi = plan["low"], plan["high"]
        if lo is not None and proj < lo:
            band = "below low"
        elif proj < plan["base"]:
            band = "between low and base" if lo is not None else "below base"
        elif hi is not None and proj > hi:
            band = "above high"
        else:
            band = "between base and high" if hi is not None else "above base"
    a["sales"] = {"to_date": sales, "ly_same_hours": ly_sales, "vs_ly": (sales / ly_sales - 1) if ly_sales else None,
                  "ly_share": share, "projected": proj, "base": plan["base"], "low": plan["low"], "high": plan["high"],
                  "vs_base": (proj / plan["base"] - 1) if proj is not None else None, "band": band}
    a["orders"] = {"to_date": orders, "ly_same_hours": ly_orders,
                   "vs_ly": (orders / ly_orders - 1) if ly_orders else None}
    aov = sales / orders if orders else None
    ly_aov = ly_sales / ly_orders if ly_orders else None
    a["aov"] = {"to_date": aov, "ly_same_hours": ly_aov, "vs_ly": (aov / ly_aov - 1) if aov and ly_aov else None}
    a["funnel"] = None
    if td["sessions"] is not None and ly["sessions"] is not None and H:
        s_td, s_ly = sum(td["sessions"][:H]), sum(ly["sessions"][:H])
        if s_td and s_ly:
            a["funnel"] = {"sessions": s_td, "conversion": orders / s_td, "ly_sessions": s_ly,
                           "ly_conversion": ly_orders / s_ly}

    # ---- spend pacing (expected share of each budget = last year's sales curve at the read time)
    band_pct = th["spend_pace_band_pct"] / 100
    by_ch = {}
    for p in spend:
        by_ch.setdefault(p["channel"], []).append(p)
    budgets = plan["budgets"]
    paused_cfg = cfg.get("paused_channels") or []
    first_check_passed = bool(plan["check_times"]) and M >= plan["check_times"][0]
    rows, exp_total, missing, unplanned, zero, paused = [], 0.0, [], [], [], []
    for c in CHANNELS:
        plats = by_ch.get(c, [])
        b = budgets[c] if budgets else None
        is_paused = c in paused_cfg
        if is_paused and b:
            paused.append(c)
        if not plats:
            if b and not is_paused:
                missing.append(c)
            continue
        actual = sum(p["spend"] for p in plats)
        read = max(p["read"] for p in plats)
        exp = b * share_at(read) if b and not is_paused else None
        if budgets and not b and actual > 0:
            unplanned.append(c)
        # QA 3.1: a budgeted channel reading $0 (or a sliver of its expected spend) after the first check is
        # treated as missing data, unless config.json says it is paused on purpose
        if exp and first_check_passed and actual < ZERO_SPEND_SHARE * exp:
            zero.append(c)
        early = share_at(read) * 100 < th["min_pace_share_pct"]
        ratio = actual / exp if exp and not early else None
        state_txt = None if ratio is None else ("ahead" if ratio > 1 + band_pct else
                                                ("behind" if ratio < 1 - band_pct else "on pace"))
        if is_paused:
            state_txt = "paused"
        rows.append({"channel": c, "platforms": [p["platform"] for p in plats], "spend": actual, "budget": b,
                     "read": hhmm(read), "expected": exp, "pace": ratio, "status": state_txt})
        if exp:
            exp_total += exp
    total = sum(p["spend"] for p in spend)
    latest_read = max(p["read"] for p in spend)
    if not budgets and plan["total_budget"]:
        exp_total = plan["total_budget"] * share_at(latest_read)
    early = share_at(latest_read) * 100 < th["min_pace_share_pct"]
    pace = total / exp_total if exp_total and not early else None
    a["spend"] = {"total": total, "budget": plan["total_budget"], "expected": exp_total or None, "pace": pace,
                  "status": None if pace is None else ("ahead" if pace > 1 + band_pct else
                                                       ("behind" if pace < 1 - band_pct else "on pace")),
                  "channels": rows, "missing": missing, "unplanned": unplanned, "zero": zero, "paused": paused,
                  "platforms": [{"platform": p["platform"], "channel": p["channel"], "spend": p["spend"],
                                 "read_at": hhmm(p["read"])} for p in spend]}
    for c in missing:
        notes.append(f"no spend figure for {CHANNEL_NAMES[c]} (budget {money(budgets[c], sym)}): MER to date "
                     "leaves it out and reads high")
    for c in zero:
        r0 = next(r for r in rows if r["channel"] == c)
        notes.append(f"{CHANNEL_NAMES[c]} reports {money(r0['spend'], sym)} spend at {time_label(int(r0['read'][:2]) * 60 + int(r0['read'][3:]))} "
                     f"against {money(r0['expected'], sym)} expected by then (budget {money(budgets[c], sym)}): "
                     "treated as missing data; if it is paused on purpose, add it to paused_channels in config.json")
    for c in paused:
        notes.append(f"{CHANNEL_NAMES[c]} marked paused in config.json (budget {money(budgets[c], sym)}): "
                     "left out of expected spend and budget actions")
    for c in unplanned:
        notes.append(f"{CHANNEL_NAMES[c]} spent {money(next(r['spend'] for r in rows if r['channel'] == c), sym)} "
                     "with no budget in the plan (counted in MER)")
    for p in spend:
        if M - p["read"] > th["stale_spend_minutes"]:
            notes.append(f"{p['platform']} spend was read at {time_label(p['read'])}, "
                         f"{(M - p['read']) // 60} h {(M - p['read']) % 60:02d} min before the as-of time; "
                         "MER may read high")
    if spend_ignored:
        notes.append(f"ignored platform-reported column(s) {', '.join(spend_ignored)} in the spend file: "
                     "MER uses Shopify net sales and spend only")

    # ---- MER and the guardrail call
    # QA 3.2 (fix A): MER compares spend and sales to the same minute. The cut-off is the earliest spend
    # read, capped at the end of the last complete hour; the hour it falls in is pro-rated. A platform read
    # later than the cut-off is scaled back to it along last year's curve (the same assumption as expected
    # spend), so later reads don't drag MER down; budget calls also need the reads themselves within
    # MAX_SPEND_SPREAD_MIN of each other (S-L2: measured between the reads, not from the capped cut-off, so
    # simultaneous reads in an incomplete hour are not "too far apart").
    earliest_read = min(p["read"] for p in spend)
    cutoff = min(earliest_read, H * 60)
    ch_, cm_ = divmod(cutoff, 60)
    sales_cut = sum(td["sales"][:ch_]) + (td["sales"][ch_] * cm_ / 60 if ch_ < H and cm_ else 0.0)
    spend_cut = 0.0
    for p in spend:
        sr = share_at(p["read"])
        spend_cut += p["spend"] * min(share_at(cutoff) / sr, 1.0) if sr > 0 else p["spend"]
    spread = latest_read - earliest_read
    mer = sales_cut / spend_cut if spend_cut > 0 and sales_cut > 0 else None
    # QA re-review: the scaling above can only remove spend, so it can only raise MER. A budget call is made
    # only when the raw MER (sales to the cut-off over every platform's spend as read, unscaled) points the
    # same way; otherwise the call is HOLD.
    spend_raw = sum(p["spend"] for p in spend)
    mer_raw = sales_cut / spend_raw if spend_raw > 0 and sales_cut > 0 else None
    mer_r = round(mer, 2) if mer is not None else None
    pb, sc, checks = plan["pull_back_mer"], plan["scale_mer"], plan["check_times"]
    passed = [t for t in checks if t <= M]
    nxt = [t for t in checks if t > M]
    g = {"mer": mer, "mer_raw": mer_raw, "spend_raw": spend_raw, "raw_disagrees": False,
         "cutoff": hhmm(cutoff), "sales_at_cutoff": sales_cut, "spend_at_cutoff": spend_cut,
         "read_spread_min": spread, "spend_behind": False, "spend_behind_reasons": [],
         "out_of_step": False, "pull_back_mer": pb, "scale_mer": sc, "plan_mer": plan["plan_mer"],
         "breakeven_mer": plan["breakeven_mer"], "check_times": [hhmm(t) for t in checks],
         "check": None, "next_check": hhmm(nxt[0]) if nxt else None, "previous_check": None,
         "above_scale": False, "actions": [], "state_reading": None}
    if not passed:
        g["call"] = "TOO EARLY"
    else:
        cur = passed[-1]
        g["check"] = hhmm(cur)
        if len(passed) > 1:
            prev_slot = (day, passed[-2])
        else:
            prev_slot = (day - dt.timedelta(days=1), plan["yesterday_checks"][-1])
        prev = next((c for c in state["checks"] if c["date"] == prev_slot[0].isoformat()
                     and c["time"] == hhmm(prev_slot[1])), None)
        g["previous_check"] = {"date": prev_slot[0].isoformat(), "time": hhmm(prev_slot[1]),
                               "mer": prev["mer"] if prev else None,
                               "above_scale": (prev["mer"] > prev["scale_mer"]) if prev else None}
        if total <= 0:
            g["call"] = "NO SPEND"
        elif mer is None:
            g["call"] = "NO SALES"
        elif mer_r < pb:
            g["call"] = "PULL BACK"
            cut = th["pull_back_cut_pct"] / 100
            for r in rows:
                if r["budget"] and r["channel"] not in paused:
                    left = max(r["budget"] - r["spend"], 0.0)
                    g["actions"].append({"channel": r["channel"], "budget": r["budget"], "spent": r["spend"],
                                         "new_daily_budget": half_up(r["spend"] + left * (1 - cut))})
        elif mer_r > sc:
            g["above_scale"] = True
            g["call"] = "SCALE" if prev and prev["mer"] > prev["scale_mer"] else "HOLD"
            if g["call"] == "SCALE" and plan["tomorrow"]:
                tb = plan["tomorrow"]["total_budget"]
                g["actions"].append({"tomorrow": plan["tomorrow"]["date"].isoformat(), "planned": tb,
                                     "max": half_up(tb * (1 + th["max_daily_increase_pct"] / 100))})
        else:
            g["call"] = "HOLD"
        # S-L1: a lagging spend read (common at peak) under-reads spend, so MER, scaled and raw alike, reads
        # high. Whenever MER is above the scale line (a SCALE call, or the first above-scale check, which would
        # otherwise be saved as the confirming check for the next one), total spend behind its pace band or any
        # budgeted, unpaused channel behind its own band makes the call HOLD and the reading is not saved.
        if g["above_scale"]:
            why = []
            if a["spend"]["status"] == "behind":
                why.append(f"total {money(total, sym)} is {pct(pace, 0)} of the {money(exp_total, sym)} "
                           "expected by now")
            for r in rows:
                if (r["budget"] and r["channel"] not in paused and r["pace"] is not None
                        and r["status"] == "behind"):
                    why.append(f"{CHANNEL_NAMES[r['channel']]} {money(r['spend'], sym)} is {pct(r['pace'], 0)} of "
                               f"the {money(r['expected'], sym)} expected by {time_label(hhmm_min(r['read']))}")
            if why:
                g["spend_behind"], g["spend_behind_reasons"] = True, why
                if g["call"] == "SCALE":
                    g["blocked_call"] = "SCALE"
                g["call"], g["actions"] = "HOLD", []
        if g["call"] in ("PULL BACK", "SCALE") and mer_raw is not None:
            raw_r = round(mer_raw, 2)
            agrees = raw_r < pb if g["call"] == "PULL BACK" else raw_r > sc
            if not agrees:
                g["raw_disagrees"] = True
                g["blocked_call"] = g["call"]
                g["call"], g["actions"] = "HOLD", []
        if g["call"] in ("PULL BACK", "SCALE") and spread > MAX_SPEND_SPREAD_MIN:
            g["out_of_step"] = True       # QA 3.2: reads too far apart for a budget call
            g["blocked_call"] = g["call"]
            g["call"], g["actions"] = "HOLD", []
        if mer is not None and not g["spend_behind"]:
            g["state_reading"] = {"date": day.isoformat(), "time": hhmm(cur), "mer": round(mer, 4),
                                  "pull_back_mer": pb, "scale_mer": sc, "call": g["call"],
                                  "as_of": f"{day.isoformat()} {hhmm(M)}"}
    stale = [p["platform"] for p in spend if M - p["read"] > th["stale_spend_minutes"]]
    g["incomplete_reasons"] = ([f"stale spend ({', '.join(stale)})"] if stale else []) + \
        ([f"missing spend ({', '.join(CHANNEL_NAMES[c] for c in missing)})"] if missing else []) + \
        ([f"$0 spend for a budgeted channel ({', '.join(CHANNEL_NAMES[c] for c in zero)})"] if zero else [])
    incomplete = bool(g["incomplete_reasons"])
    if incomplete:
        g["call"] = "DATA INCOMPLETE"
        g["actions"] = []
        g["state_reading"] = None
    a["mer"] = g
    a["mer_cutoff_min"] = cutoff

    # ---- alerts, hour by hour, so recovered problems can be reported once as resolved
    k_res = th["resolve_after_hours"]
    growth = plan["base"] / ly_total
    L = H - 1
    timelines = {}

    def stall(h):
        exp = ly["orders"][h] * growth
        if exp < th["orders_stall_min_expected"]:
            return None, None
        r = td["orders"][h] / exp
        return ("fire" if r < th["orders_stall_pct"] / 100 else "clear"), {
            "hour": h, "orders": td["orders"][h], "expected": exp, "ly_orders": ly["orders"][h], "ratio": r}

    def checkout(h):
        rc, k = td["sessions_reached_checkout"], th["checkout_baseline_hours"]
        if rc is None or h < k:
            return None, None
        base_rc, base_o = sum(rc[h - k:h]), sum(td["orders"][h - k:h])
        if rc[h] < th["checkout_min_sessions"] or base_rc < k * th["checkout_min_sessions"] or not base_o:
            return None, None
        conv, base = td["orders"][h] / rc[h], base_o / base_rc
        drop = 1 - conv / base
        return ("fire" if drop > th["checkout_drop_pct"] / 100 else "clear"), {
            "hour": h, "conversion": conv, "orders": td["orders"][h], "reached_checkout": rc[h],
            "baseline": base, "baseline_hours": k, "drop": drop}

    def aov_at(h):
        k = th["aov_hours"]
        if h < k - 1:
            return None, None
        o, s = sum(td["orders"][h - k + 1:h + 1]), sum(td["sales"][h - k + 1:h + 1])
        lo, ls = sum(ly["orders"][h - k + 1:h + 1]), sum(ly["sales"][h - k + 1:h + 1])
        if o < th["aov_min_orders"] or lo < LY_MIN_AOV_ORDERS:
            return None, None
        v, lv = s / o, ls / lo
        drop = 1 - v / lv
        return ("fire" if drop > th["aov_drop_pct"] / 100 else "clear"), {
            "hours": [h - k + 1, h], "aov": v, "ly_aov": lv, "orders": o, "drop": drop}

    def code_at(code, h):
        c = codes[code]
        u, b = c["uses"][h], c["baseline"]
        fire = u >= th["code_min_uses"] and (b == 0 or u > th["code_multiple"] * b)
        return ("fire" if fire else "clear"), {"hour": h, "uses": u, "baseline": b,
                                                "multiple": (u / b) if b else None}

    evaluators = [("orders_stall", "orders_stall", stall), ("checkout", "checkout", checkout), ("aov", "aov", aov_at)]
    if codes:
        for code in sorted(codes):
            evaluators.append(("code_leak", f"code:{code}", (lambda cd: lambda h: code_at(cd, h))(code)))
    active, resolved = [], []
    for typ, key, fn in evaluators:
        tl = [fn(h) for h in range(H)]
        timelines[key] = [s for s, _ in tl]
        if not H:
            continue
        st, info = tl[L]
        if st == "fire":
            active.append(make_alert(typ, key, info, a, td, ly, codes, M, growth))
        elif (L - k_res >= 0 and tl[L - k_res][0] == "fire"
              and all(tl[h][0] == "clear" for h in range(L - k_res + 1, L + 1))):
            end = L - k_res
            start = end
            while start - 1 >= 0 and tl[start - 1][0] == "fire":
                start -= 1
            episode = [tl[h][1] for h in range(start, end + 1)]
            after = [tl[h][1] for h in range(end + 1, L + 1)]
            resolved.append(make_resolved(typ, key, episode, after, a))
    if stock:
        for s in stock:
            if s["sold"] <= 0:
                continue
            rate = s["sold"] / 3
            cover = max(s["on_hand"], 0) / rate
            if cover < th["cover_hours"]:
                active.append(make_stock_alert(s, rate, cover, a, M))
    a["timelines"] = timelines
    a["not_checked"] = []
    if td["sessions_reached_checkout"] is None:
        a["not_checked"].append("checkout conversion (no sessions_reached_checkout column)")
    if codes is None:
        a["not_checked"].append("discount codes (no discount usage file)")
    if stock is None:
        a["not_checked"].append("stock cover (no inventory file)")

    for al in active:
        al["sort"] = (SEVERITY[al["severity"]], TYPE_ORDER[al["type"]], -al["magnitude"], al["key"])
    active.sort(key=lambda x: x["sort"])
    resolved.sort(key=lambda x: (TYPE_ORDER[x["type"]], x["key"]))
    a["alerts"], a["resolved"] = active, resolved
    # cross-reference: a code that dominates the AOV window
    for al in active:
        if al["type"] == "aov" and codes:
            h1, h2 = al["data"]["hours"]
            top = max(sorted(codes), key=lambda cd: sum(codes[cd]["uses"][h1:h2 + 1]))
            u = sum(codes[top]["uses"][h1:h2 + 1])
            if u and u >= 0.25 * al["data"]["orders"]:
                al["data"]["code"] = {"code": top, "uses": u}
                al["text"] += f" {top} was on {u} of these {al['data']['orders']} orders."
                al["next"] = f"check whether {top} stacks with the sale (BFCM Offer & Discount Config QA)."
    n_crit = sum(1 for x in active if x["severity"] == "critical")
    n_warn = sum(1 for x in active if x["severity"] == "warning")
    a["status"] = ("ACTION NEEDED" if n_crit or g["call"] == "PULL BACK" else
                   ("WATCH" if n_warn or incomplete else "ON TRACK"))
    a["counts"] = {"critical": n_crit, "warning": n_warn, "resolved": len(resolved)}
    a["notes"] = notes
    a["defaults"] = plan["defaults"]
    return a


def make_alert(typ: str, key: str, d: dict, a: dict, td: dict, ly: dict, codes: dict | None, M: int,
               growth: float) -> dict:
    sym = a["sym"]
    if typ == "orders_stall":
        text = (f"Orders stalled: {d['orders']} in the {clock(d['hour'])} hour vs {d['expected']:.0f} expected "
                f"({pct(d['ratio'], 0)}; last year {d['ly_orders']} x plan growth {growth:.2f}).")
        nxt = "place a test order now; check checkout, payments and shopifystatus.com."
        return {"type": typ, "key": key, "severity": "critical", "text": text, "next": nxt, "data": d,
                "magnitude": 1 - d["ratio"]}
    if typ == "checkout":
        text = (f"Checkout conversion {pct(d['conversion'])} in the {clock(d['hour'])} hour ({d['orders']} orders / "
                f"{d['reached_checkout']} sessions at checkout) vs {pct(d['baseline'])} the {d['baseline_hours']} "
                f"hours before ({spct(-d['drop'], 0)}).")
        nxt = "run a test checkout with a sale item, a code and each payment method; check checkout apps."
        return {"type": typ, "key": key, "severity": "critical", "text": text, "next": nxt, "data": d,
                "magnitude": d["drop"]}
    if typ == "aov":
        h1, h2 = d["hours"]
        text = (f"AOV {money2(d['aov'], sym)} over {span(h1, h2)} vs {money2(d['ly_aov'], sym)} last year, same hours "
                f"({spct(-d['drop'])}).")
        nxt = "look for discounts stacking or a shift to low-priced items (Promo Margin Guard)."
        return {"type": typ, "key": key, "severity": "warning", "text": text, "next": nxt, "data": d,
                "magnitude": d["drop"]}
    code = key.split(":", 1)[1]
    h = d["hour"]
    today_total = sum(codes[code]["uses"][:h + 1])
    base = f"its usual {d['baseline']:g}/hour"
    mult = f" ({d['multiple']:.0f}x)" if d["multiple"] else ""
    share = f" ({d['uses']} of {td['orders'][h]} orders)" if d["uses"] <= td["orders"][h] else ""
    text = (f"{code}: {d['uses']} uses in the {clock(h)} hour{share} vs {base}{mult}; {today_total} uses today. "
            "Likely leaked.")
    nxt = ("pause it or set a usage limit and once per customer now; check it can't combine with the sale "
           "(BFCM Offer & Discount Config QA).")
    return {"type": "code_leak", "key": key, "severity": "critical", "text": text, "next": nxt, "data": d,
            "magnitude": d["uses"]}


def make_stock_alert(s: dict, rate: float, cover: float, a: dict, M: int) -> dict:
    if s["on_hand"] <= 0:
        text = f"{s['title']} is sold out ({s['on_hand']} on hand) after {s['sold']} sold in the last 3 hours."
        nxt = "take it out of ads, emails and the sale collection now; reorder with Inventory Stockout Risk."
    else:
        out = M + int(round(cover * 60 / 5.0)) * 5
        when = time_label(out % 1440) + (" tomorrow" if out >= 1440 else "")
        text = (f"{s['title']}: {s['on_hand']} left, {s['sold']} sold in the last 3 hours ({rate:.0f}/hour), "
                f"{cover:.1f} hours of cover (out about {when}).")
        nxt = "pull it from ads and emails or accept the sell-out; reorder with Inventory Stockout Risk."
    return {"type": "stock", "key": f"sku:{s['sku']}", "severity": "warning", "text": text, "next": nxt,
            "data": {"sku": s["sku"], "title": s["title"], "on_hand": s["on_hand"], "sold_last_3h": s["sold"],
                     "per_hour": rate, "cover_hours": cover}, "magnitude": -cover}


def make_resolved(typ: str, key: str, episode: list[dict], after: list[dict], a: dict) -> dict:
    sym = a["sym"]
    if typ == "checkout":
        w = max(episode, key=lambda d: d["drop"])
        vals = " and ".join(f"{pct(d['conversion'])} at {clock(d['hour'])}" for d in after)
        text = (f"Checkout conversion fell to {pct(w['conversion'])} in the {clock(w['hour'])} hour "
                f"({pct(w['baseline'])} the {w['baseline_hours']} hours before); back to {vals}.")
    elif typ == "orders_stall":
        w = min(episode, key=lambda d: d["ratio"])
        vals = " and ".join(f"{d['orders']} at {clock(d['hour'])} ({d['expected']:.0f} expected)" for d in after)
        text = (f"Orders stalled in the {clock(w['hour'])} hour ({w['orders']} vs {w['expected']:.0f} expected); "
                f"back to {vals}.")
    elif typ == "aov":
        w = max(episode, key=lambda d: d["drop"])
        last = after[-1]
        text = (f"AOV recovered: {money2(last['aov'], sym)} over {span(*last['hours'])} "
                f"({spct(-last['drop'])} vs last year); it was {money2(w['aov'], sym)} ({spct(-w['drop'])}) "
                f"over {span(*w['hours'])}.")
    else:
        code = key.split(":", 1)[1]
        w = max(episode, key=lambda d: d["uses"])
        vals = " and ".join(f"{d['uses']} at {clock(d['hour'])}" for d in after)
        text = f"{code} use is back to normal: {vals} (peak {w['uses']} in the {clock(w['hour'])} hour)."
    return {"type": typ if not key.startswith("code:") else "code_leak", "key": key, "severity": "resolved",
            "text": text, "next": None, "data": {"episode": episode, "after": after}}


# --------------------------------------------------------------------------- message
def build_message(a: dict, level: int = 0) -> str:
    """level drops optional parts until the message fits: 1 funnel, 2 platform split, 3 assumption sentence,
    4 not-checked line, 5 notes, 6 next steps."""
    sym, th, g = a["sym"], a["th"], a["mer"]
    s = a["sales"]
    day_title = f"{a['tag']}, {day_label(a['date'])}" if a["tag"] else day_label(a["date"])
    L = [f"*BFCM Live Pulse · {a['store']} · {day_title} · {time_label(a['as_of_min'])} {a['tz']}*"]
    c = a["counts"]
    parts = []
    if c["critical"]:
        parts.append(f"{c['critical']} critical")
    if c["warning"]:
        parts.append(f"{c['warning']} warning{'s' if c['warning'] > 1 else ''}")
    if c["resolved"]:
        parts.append(f"{c['resolved']} resolved")
    status = f"*{a['status']}*: " + (", ".join(parts) if parts else "no alerts") + "."
    if s["projected"] is not None:
        diff = s["vs_base"]
        rel = (f"{pct(abs(diff))} {'under' if diff < 0 else 'over'} base {money(s['base'], sym)}"
               if abs(diff) >= 0.0005 else f"on base {money(s['base'], sym)}")
        rng = []
        if s["low"] is not None:
            rng.append(f"low {money(s['low'], sym)}")
        if s["high"] is not None:
            rng.append(f"high {money(s['high'], sym)}")
        status += f" Pacing to *{money(s['projected'], sym)}*, {rel}" + (f" ({', '.join(rng)})" if rng else "") + "."
    else:
        status += (f" Too early for a pace: last year had {pct(s['ly_share'])} of its day by now "
                   f"(plan base {money(s['base'], sym)}).")
    mer_txt = (f"MER to {time_label(a['mer_cutoff_min'])} *{g['mer']:.2f}*" if g["mer"] is not None
               else ("MER n/a (no sales yet)" if a["spend"]["total"] > 0 else "No ad spend yet"))
    first = check_name(g["check_times"][0])
    if g["call"] == "TOO EARLY":
        status += f" {mer_txt}: too early to call (first check at {first}; judge from {first} onward)."
    elif g["call"] == "DATA INCOMPLETE":
        status += (f" *DATA INCOMPLETE*: {'; '.join(g['incomplete_reasons'])}. Refresh spend before making a "
                   "budget decision.")
    elif g["call"] == "NO SPEND":
        status += " No ad spend recorded yet, so no MER call."
    elif g["call"] == "NO SALES":
        status += " MER n/a: no sales recorded yet, so no MER call."
    else:
        ck = check_name(g["check"])
        nxt = f"; next check {check_name(g['next_check'])}" if g["next_check"] else ""
        if g["out_of_step"]:
            r0 = min(hhmm_min(x["read_at"]) for x in a["spend"]["platforms"])
            status += (f" {mer_txt}: *HOLD* ({ck} check passed{nxt}). It reads {g['blocked_call']}, but spend reads "
                       f"span {time_label(r0)}–{time_label(r0 + g['read_spread_min'])}, "
                       f"more than {MAX_SPEND_SPREAD_MIN} min apart: too far apart for a budget call; refresh spend.")
        elif g["spend_behind"]:
            lead = ("It reads SCALE" if g.get("blocked_call") == "SCALE" else f"Above scale {g['scale_mer']:.2f}")
            status += (f" {mer_txt}: *HOLD* ({ck} check passed{nxt}). {lead}, but spend is behind "
                       f"({'; '.join(g['spend_behind_reasons'])}). A lagging spend read makes MER read high, so this "
                       "check doesn't count toward SCALE: refresh spend; if it really is behind, more budget won't help.")
        elif g["raw_disagrees"]:
            status += (f" {mer_txt}: *HOLD* ({ck} check passed{nxt}). It reads {g['blocked_call']} with spend scaled back "
                       f"to {time_label(a['mer_cutoff_min'])}, but {g['mer_raw']:.2f} with spend as read: the two "
                       "disagree, so no budget call until they agree.")
        elif g["call"] == "HOLD" and g["above_scale"]:
            pc = g["previous_check"]
            why = (f"last check {pc['mer']:.2f}" if pc["mer"] is not None else "no earlier check on record")
            status += (f" {mer_txt}: *HOLD* ({ck} check passed{nxt}). Above scale {g['scale_mer']:.2f}, but SCALE needs "
                       f"the previous check above it too ({why}).")
        else:
            status += f" {mer_txt}: *{g['call']}* ({ck} check passed{nxt})."
    L.append(status)
    if g["call"] == "PULL BACK":
        cut = th["pull_back_cut_pct"]
        moves = ", ".join(f"{CHANNEL_NAMES[x['channel']]} {money(x['budget'], sym)} → {money(x['new_daily_budget'], sym)}"
                          for x in g["actions"])
        line = (f"Proposed (needs approval): *PULL BACK*: MER {g['mer']:.2f} is below the pull-back line "
                f"{g['pull_back_mer']:.2f}. "
                f"Cut the rest of today's budgets "
                f"{cut:g}% (daily budget = spent + {100 - cut:g}% of what's left): {moves}. Hold tomorrow at or below "
                "today's level.")
        if any(x["type"] in ("orders_stall", "checkout", "stock", "code_leak") for x in a["alerts"]):
            line += " Check the alerts first: a broken checkout, leaked code or sold-out hero looks like an ad problem."
        L.append(line)
    elif g["call"] == "SCALE":
        pc = g["previous_check"]
        line = (f"Proposed (needs approval): *SCALE*: also above {g['scale_mer']:.2f} at the last check ({day_label(dt.date.fromisoformat(pc['date']))}, "
                f"{check_name(pc['time'])}: {pc['mer']:.2f}). Raise tomorrow's budget by up to "
                f"{th['max_daily_increase_pct']:g}%")
        if g["actions"]:
            x = g["actions"][0]
            line += f": {money(x['planned'], sym)} → at most {money(x['max'], sym)}"
        L.append(line + ", on the channel with the most headroom.")

    items = a["alerts"] + a["resolved"]
    shown = items[:th["max_alerts"]]
    if shown:
        L.append("")
        L.append("*Alerts*")
        for n, x in enumerate(shown, 1):
            line = f"{n}. *{x['severity'].upper()}* {x['text']}"
            if x["next"] and level < 6:
                line += f" Next: {x['next']}"
            L.append(line)
        if len(items) > len(shown):
            L.append(f"+{len(items) - len(shown)} more (lower priority) in the --json output.")

    L.append("")
    L.append(f"*Numbers to {time_label(a['as_of_min'])}* ({a['hours']} of 24 hours; LY = "
             f"{day_label(a['ly_date'])}, {a['ly_date'].year}, same hours)")
    o, v = a["orders"], a["aov"]
    blk = [f"Net sales {money(s['to_date'], sym)} · LY {money(s['ly_same_hours'], sym)}"
           + (f" ({spct(s['vs_ly'])})" if s["vs_ly"] is not None else "")]
    ln = f"Orders    {o['to_date']:,} · LY {o['ly_same_hours']:,}" + (f" ({spct(o['vs_ly'])})" if o["vs_ly"] is not None else "")
    if v["to_date"] is not None and v["ly_same_hours"] is not None:
        ln += f" · AOV {money2(v['to_date'], sym)} · LY {money2(v['ly_same_hours'], sym)} ({spct(v['vs_ly'])})"
    blk.append(ln)
    sp = a["spend"]
    ln = f"Spend     {money(sp['total'], sym)} of {money(sp['budget'], sym)}"
    if sp["budget"]:
        ln += f" ({pct(sp['total'] / sp['budget'])})"
    if sp["pace"] is not None:
        ln += f" · {pct(sp['pace'], 0)} of expected by now" + ("" if sp["status"] == "on pace" else f", {sp['status']}")
    blk.append(ln)
    if level < 2 and sp["channels"]:
        bits = []
        for r in sp["channels"]:
            b = f"{CHANNEL_NAMES[r['channel']]} {money(r['spend'], sym)}"
            if r["status"] == "paused":
                b += " paused"
            elif r["pace"] is not None:
                b += f" {pct(r['pace'], 0)}" + ("" if r["status"] == "on pace" else f" {r['status']}")
            bits.append(b)
        blk.append("          " + " · ".join(bits))
    ln = "MER       " + (f"{g['mer']:.2f} to {time_label(a['mer_cutoff_min'])}" if g["mer"] is not None else "n/a")
    ln += f" · pull back < {g['pull_back_mer']:.2f} · scale > {g['scale_mer']:.2f}"
    if g["plan_mer"] is not None:
        ln += f" · plan {g['plan_mer']:.2f}"
    blk.append(ln)
    f = a["funnel"]
    if f and level < 1:
        blk.append(f"Funnel    {pct(f['conversion'])} of {f['sessions']:,} sessions ordered · LY {pct(f['ly_conversion'])}")
    L.append("```\n" + "\n".join(blk) + "\n```")
    foot = "_Read-only: nothing was changed."
    if level < 3:
        foot += " Expected spend assumes budgets pace like last year's hourly sales."
    L.append(foot + "_")
    if a["not_checked"] and level < 4:
        L.append("_Not checked: " + "; ".join(a["not_checked"]) + "._")
    extra = []
    if a["defaults"]:
        extra.append("defaults used: " + "; ".join(a["defaults"]))
    if level < 5:
        extra += a["notes"]
    if extra:
        L.append("_Data: " + "; ".join(extra) + "._")
    return "\n".join(L) + "\n"


def check_name(hm: str | None) -> str:
    if hm is None:
        return ""
    h, m = int(hm[:2]), int(hm[3:])
    return clock(h) if m == 0 else time_label(h * 60 + m)


def render(a: dict) -> str:
    limit = a["th"]["max_chars"]
    for level in range(7):
        msg = build_message(a, level)
        if len(msg) <= limit:
            a["message_level"] = level
            return msg
    a["message_level"] = 7
    cut = "…\n_(trimmed to fit; full detail in --json)_\n"
    return msg[:limit - len(cut)] + cut


# --------------------------------------------------------------------------- output
def jsonable(x):
    if isinstance(x, float):
        return None if math.isinf(x) or math.isnan(x) else round(x, 6)
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items() if k != "sort"}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    return x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", required=True, help="the BFCM Holiday Budget Planner's --csv daily plan")
    ap.add_argument("--hourly", required=True, help="today's Shopify orders and net sales by hour")
    ap.add_argument("--ly-hourly", required=True, help="last year's matching day by hour")
    ap.add_argument("--spend", required=True, help="ad spend so far today per platform, with read times")
    ap.add_argument("--discounts", help="optional: discount code uses by hour today, with baselines")
    ap.add_argument("--inventory", help="optional: on hand and units sold in the last 3 hours per SKU")
    ap.add_argument("--config", help="optional: store, time zone label, as-of time, quiet mode, thresholds")
    ap.add_argument("--as-of", help="'YYYY-MM-DD HH:MM' store time; overrides config as_of")
    ap.add_argument("--state", help="optional JSON file that remembers MER at each check (read and rewritten)")
    ap.add_argument("--quiet", action="store_true", help="print nothing unless there is an alert or a summary time")
    ap.add_argument("--json", help="also write everything as JSON to this path")
    ap.add_argument("--pull-back-mer", help="pull-back MER line, if the plan has no pull_back_mer column")
    ap.add_argument("--scale-mer", help="scale MER line, if the plan has no scale_mer column")
    ap.add_argument("--check-times", help="check times, e.g. '12:00;18:00', if the plan has no check_times column")
    ap.add_argument("--ly-date", help="last year's matching day, if the plan has no ly_date column")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        raw_as_of = args.as_of or cfg["as_of"]
        if not raw_as_of:
            raise InputError("no as-of time: pass --as-of 'YYYY-MM-DD HH:MM' or set as_of in config.json")
        as_of = parse_stamp(raw_as_of, "as-of time", cfg["utc_offset"], None)
        if as_of.second:
            raise InputError("as-of time: use whole minutes")
        flags = {"pull_back_mer": args.pull_back_mer, "scale_mer": args.scale_mer,
                 "check_times": args.check_times, "ly_date": args.ly_date}
        plan = load_plan(args.plan, as_of.date(), flags)
        td = load_hourly(args.hourly, "hourly", as_of.date(), as_of.hour, cfg["utc_offset"])
        ly = load_hourly(args.ly_hourly, "last year's hourly", plan["ly_date"], 24, cfg["utc_offset"])
        spend, ignored, spend_warnings = load_spend(args.spend, as_of, cfg["utc_offset"], cfg["spend_timestamps_tz"])
        codes = load_discounts(args.discounts, as_of.date(), as_of.hour, cfg["utc_offset"]) if args.discounts else None
        stock = load_inventory(args.inventory) if args.inventory else None
        state = load_state(args.state)
        plan["notes"].extend(spend_warnings)
        a = analyze(cfg, plan, td, ly, spend, ignored, codes, stock, state, as_of)
    except InputError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    msg = render(a)
    quiet = args.quiet or cfg["quiet_mode"]["enabled"]
    summary_times = cfg["quiet_mode"]["summary_times"] or plan["check_times"]
    M = a["as_of_min"]
    summary = any(t <= M < t + 60 for t in summary_times)
    needs_post = bool(a["alerts"] or a["resolved"] or a["mer"]["call"] in ("PULL BACK", "SCALE", "DATA INCOMPLETE"))
    printed = (not quiet) or summary or needs_post
    a["quiet"] = {"enabled": quiet, "summary_times": [hhmm(t) for t in summary_times], "summary_time": summary,
                  "needs_post": needs_post, "printed": printed}
    a["message"], a["message_chars"] = msg, len(msg)
    if printed:
        sys.stdout.write(msg)
    if args.state and a["mer"]["state_reading"]:
        st = state
        r = a["mer"]["state_reading"]
        st["checks"] = [c for c in st["checks"] if not (c["date"] == r["date"] and c["time"] == r["time"])] + [r]
        st["checks"] = sorted(st["checks"], key=lambda c: (c["date"], c["time"]))[-30:]
        st["version"] = 1
        tmp = args.state + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, args.state)
    if args.json:
        out = {k: v for k, v in a.items() if k not in ("th", "sym")}
        out["thresholds"] = a["th"]
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(jsonable(out), fh, indent=2, sort_keys=True, allow_nan=False)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
