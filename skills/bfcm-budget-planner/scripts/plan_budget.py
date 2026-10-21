#!/usr/bin/env python3
"""
BFCM Holiday Budget Planner: planner.

Reads last year's Oct-Dec daily history plus this year's trailing days
(history.csv) and the brand's unit economics (economics.json), and prints a
Markdown plan: breakeven and target MER at the planned discount (sale days)
and at full price (the rest of the window), a daily paid-media budget for the
BFCM window (total and per channel), projected revenue, MER and contribution
per day with low/base/high scenarios, and each day's own pull-back / scale
guardrail lines (always below / above that day's plan MER; also on every row of
the --csv daily plan). Net sales = gross sales - discounts, before
returns/refunds, excluding tax and shipping.
See DATA_CONTRACT.md.

Plans from MER (store revenue / total ad spend) and contribution margin,
never from platform ROAS. Local files only. Never calls an API, never changes
an ad account.

    python3 scripts/plan_budget.py --history examples/data/history.csv \
        --economics examples/data/economics.json [--csv plan.csv] [--json plan.json] \
        [--as-of YYYY-MM-DD] [--starting-daily-budget N] [--accept-demand-index]

Python 3.8+, standard library only. Output is deterministic for a given input and --as-of
(which defaults to the system date and only affects the stale-history warning).
Exit codes: 0 plan written, 2 invalid input, 3 refused (target is platform ROAS).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys

# ---- begin shared strict number parser (copied verbatim from shared/strict_num.py) ----
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
# ---- end shared strict number parser ----

CHANNELS = ("meta", "google", "tiktok", "other")
CHANNEL_NAMES = {"meta": "Meta", "google": "Google", "tiktok": "TikTok", "other": "Other"}
PHASES = ("warm-up", "cyber week", "taper")
DEFAULT_ELASTICITY = 0.5      # used when a phase can't be fitted
ELASTICITY_MIN, ELASTICITY_MAX = 0.2, 0.7
MIN_FIT_DAYS = 5
MIN_FIT_R2 = 0.2
TRAILING_DAYS = 28            # this-year days used for the demand index
STALE_DAYS = 14               # warn when history ends this long before today / the plan start
MISSING_WARN_DAYS = 7         # warn when this many of the trailing days have no row
INDEX_SKIP_WARN = 0.20        # warn when more than this share of trailing days had no spend
INDEX_WARN = (0.5, 2.0)       # demand index outside this: warning
INDEX_FAIL = (0.25, 4.0)      # outside this: exit 2 unless --accept-demand-index
SENS_ELASTICITY = 0.4         # sensitivity line: the plan's spend on a flatter curve
RUN_RATE_DAYS = 14            # this-year days used for today's run rate
SHORT_HISTORY_DAYS = 28
SMOOTH_RADIUS = 3             # warm-up/taper anchors: 7-day centered average of last year
MIN_INDEX_DAYS = 7
EXTRAPOLATION_MULT = 2.0      # plan > 2x last year's spend on a day = extrapolated
ROAS_HINTS = ("roas", "attributed", "conversion_value", "purchase_value", "platform_revenue")
ROAS_TOKEN = re.compile(r"(?:^|[^a-z])roas(?:$|[^a-z])", re.I)
CHECK_HOURS = {"warm-up": (14,), "cyber week": (12, 18), "taper": (14,)}   # store time


class InputError(Exception):
    pass


class Refusal(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def money(x: float) -> str:
    return ("-$" if x < 0 else "$") + f"{abs(x):,.0f}"


def mer(x: float | None) -> str:
    return "—" if x is None or math.isinf(x) else f"{x:.2f}"


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_range(a: dt.date, b: dt.date) -> str:
    return f"{fmt_day(a)}–{fmt_day(b)}"


def fmt_span(a: dt.date, b: dt.date) -> str:
    return fmt_day(a) if a == b else fmt_range(a, b)


def round2(x: float) -> float:
    """The 2-decimal value the report and the --csv show."""
    return float(f"{x:.2f}")


def line_below(x: float, plan_mer: float) -> float:
    """x rounded down to cents, kept strictly below plan_mer."""
    v = math.floor(x * 100 + 1e-9) / 100
    return v if v < plan_mer else math.ceil(plan_mer * 100 - 1e-9) / 100 - 0.01


def line_above(x: float, plan_mer: float) -> float:
    """x rounded up to cents, kept strictly above plan_mer."""
    v = math.ceil(x * 100 - 1e-9) / 100
    return v if v > plan_mer else math.floor(plan_mer * 100 + 1e-9) / 100 + 0.01


def pricing(discount: float) -> str:
    return f"{discount * 100:.0f}% off" if discount > 0 else "full price"


def hour(h: int) -> str:
    return "noon" if h == 12 else (f"{h - 12} pm" if h > 12 else f"{h} am")


def check_txt(times: list[str]) -> str:
    """["12:00", "18:00"] -> "noon and 6 pm"."""
    return " and ".join(hour(int(t[:2])) for t in times)


def parse_date(s, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(s).strip())
    except ValueError:
        raise InputError(f"{what}: '{s}' is not a YYYY-MM-DD date")


def black_friday(year: int) -> dt.date:
    nov1 = dt.date(year, 11, 1)
    first_thu = nov1 + dt.timedelta(days=(3 - nov1.weekday()) % 7)
    return first_thu + dt.timedelta(days=22)          # 4th Thursday + 1


def num(v, what: str, lo: float | None = None, hi: float | None = None, src: str = "economics.json") -> float:
    """One economics number: a JSON number, or a string the shared strict parser accepts. Finite only."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise InputError(f"{src}: {what} must be a number (got {v!r})")
    if isinstance(v, str):
        try:
            v = strict_number(v, allow_negative=True, where=f"{src}, key {what}")
        except NumberFormatError as ex:
            raise InputError(str(ex))
    v = float(v)
    if not math.isfinite(v):
        raise InputError(f"{src}: {what} must be a finite number (got {v!r}); nan and infinity are rejected")
    if lo is not None and v < lo:
        raise InputError(f"{what} must be >= {lo:g} (got {v:g})")
    if hi is not None and v > hi:
        raise InputError(f"{what} must be <= {hi:g} (got {v:g})")
    return v


# --------------------------------------------------------------------------- loading
# Any header that looks like money spent on ads but is not one of the recognised *_spend names stops the run,
# e.g. Meta's own export header "Amount spent" or Google's "Cost". Dropping it would inflate MER.
SPEND_LIKE = re.compile(r"spen[dt]|cost|amount|budget|ad_?fees?")
# S-P1: a channel column named without a spend word ("Facebook Ads", "Meta", "Kosten") would also be dropped. Any
# other mostly-numeric column stops the run unless it is a common store metric that is clearly not ad spend.
KNOWN_NON_SPEND = {"gross_sales", "discounts", "returns", "refunds", "net_returns", "taxes", "tax", "shipping",
                   "total_sales", "sessions", "visits", "visitors", "users", "aov", "average_order_value", "orders",
                   "returning_customer_orders", "returning_customers", "new_customers", "customers", "units",
                   "units_sold", "items", "conversion_rate", "conversions", "add_to_cart", "checkouts", "day",
                   "week", "month", "year", "weekday", "day_of_week"}


def mostly_numeric(values: list) -> bool:
    vals = [v.strip() for v in values if v.strip()]
    if not vals:
        return False
    num = sum(1 for v in vals if re.fullmatch(r"[^\d\-.,]{0,3}-?[\d.,]*\d[\d.,]*[^\d.,]{0,3}", v))
    return num * 2 > len(vals)


def norm_header(c: str) -> str:
    """'Meta Spend' / ' meta-spend ' -> 'meta_spend'."""
    return re.sub(r"[\s\-]+", "_", (c or "").strip().lower())


def load_history(path: str) -> tuple[list[dict], list[str]]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            table = list(csv.reader(f))
    except OSError as e:
        raise InputError(f"cannot read {path}: {e}")
    except (UnicodeDecodeError, csv.Error) as e:
        raise InputError(f"cannot read {path} as a UTF-8 CSV: {e}")
    if not table:
        raise InputError(f"{path} is empty")
    cols = [norm_header(c) for c in table[0]]
    dup = sorted({c for c in cols if c and cols.count(c) > 1})
    if dup:
        raise InputError(f"{path}: column(s) {', '.join(dup)} appear more than once (after lower-casing and turning "
                         "spaces and hyphens into _)")
    need = ["date", "net_sales", "total_orders", "new_customer_orders"]
    missing = [c for c in need if c not in cols]
    if missing:
        raise InputError(f"{path} is missing column(s): {', '.join(missing)}")
    spend_cols = [f"{c}_spend" for c in CHANNELS]
    ignored = [c for c in cols if c and (ROAS_TOKEN.search(c) or any(h in c for h in ROAS_HINTS[1:]))]
    unknown = [table[0][j].strip() for j, c in enumerate(cols)
               if c not in spend_cols and c not in ignored and SPEND_LIKE.search(c)]
    if unknown:
        raise InputError(f"{path}: column(s) {', '.join(repr(u) for u in unknown)} look like ad spend but are not a "
                         f"recognised name, so they would be dropped. Rename each to one of {', '.join(spend_cols)} "
                         "(put any other paid channel in other_spend), or remove the column if it is not ad spend")
    known = set(need) | set(spend_cols) | set(ignored) | KNOWN_NON_SPEND
    numeric_extra = [table[0][j].strip() for j, c in enumerate(cols) if c and c not in known
                     and mostly_numeric([r[j] if j < len(r) else "" for r in table[1:]])]
    if numeric_extra:
        raise InputError(f"{path}: column(s) {', '.join(repr(u) for u in numeric_extra)} hold numbers but are not a "
                         f"recognised column, so they would be dropped. If a column is ad spend, rename it to one of "
                         f"{', '.join(spend_cols)} (put any other paid channel in other_spend); if it is not ad spend, "
                         "remove it")
    if not any(c in cols for c in spend_cols):
        raise InputError(f"{path} has no ad spend column (add at least one of {', '.join(spend_cols)})")
    need += [c for c in spend_cols if c in cols]          # a channel without a column counts as 0 spend
    pos = {c: cols.index(c) for c in need}
    body = [(i, r) for i, r in enumerate(table[1:], start=2) if any(x.strip() for x in r)]
    cell = lambda r, c: (r[pos[c]] if pos[c] < len(r) else "").strip()
    for i, r in body:
        if len(r) != len(cols):
            hint = ("; a comma inside an unquoted number (2255,74) splits it in two: " + _DECIMAL_HINT
                    if len(r) > len(cols) else "")
            raise InputError(f"{path} line {i} has {len(r)} cells but the header has {len(cols)}{hint}")
    for c in need[1:]:
        if looks_european([cell(r, c) for _, r in body]):
            raise InputError(f"{path}: column {c} (column {pos[c] + 1}) is in European number format (1.234,56); "
                             f"{_DECIMAL_HINT}")
    rows, seen = [], set()
    for i, r in body:
        d = parse_date(cell(r, "date"), f"{path} line {i}")
        if d in seen:
            raise InputError(f"{path} line {i}: duplicate date {d}")
        seen.add(d)
        row = {"date": d}
        for c in need[1:]:
            raw = cell(r, c)
            try:                                          # empty cells count as 0
                v = strict_number(raw, allow_negative=True, where=f"{path} line {i}, column {pos[c] + 1} ({c})") \
                    if raw else 0.0
            except NumberFormatError as ex:
                raise InputError(str(ex))
            if v < 0:
                raise InputError(f"{path} line {i}: negative {c} (column {pos[c] + 1})")
            row[c] = v
        if row["new_customer_orders"] > row["total_orders"]:
            raise InputError(f"{path} line {i}: new_customer_orders exceeds total_orders")
        for c in spend_cols:
            row.setdefault(c, 0.0)
        row["spend"] = sum(row[f"{c}_spend"] for c in CHANNELS)
        rows.append(row)
    if not rows:
        raise InputError(f"{path} has no data rows")
    return sorted(rows, key=lambda r: r["date"]), ignored


def find_roas_keys(obj, prefix: str = "") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            if ROAS_TOKEN.search(str(k)):
                hits.append(key)
            if k == "type" and isinstance(v, str) and ROAS_TOKEN.search(v):
                hits.append(f"{key}={v}")
            hits += find_roas_keys(v, key + ".")
    return hits


def load_economics(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            e = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        raise InputError(f"cannot read economics {path}: {ex}")
    if not isinstance(e, dict):
        raise InputError("economics.json must be a JSON object")
    roas = find_roas_keys(e)
    if roas:
        raise Refusal(", ".join(roas))
    for k in ("gross_margin_pct", "shipping_fulfillment_per_order", "payment_fee_pct",
              "return_rate_pct", "discount_pct", "target"):
        if k not in e:
            raise InputError(f"economics.json is missing required key '{k}'")
    out = {
        "brand": str(e.get("brand", "Your brand")),
        "currency": str(e.get("currency", "USD")),
        "gm": num(e["gross_margin_pct"], "gross_margin_pct", 0, 100, src=path) / 100,
        "ship": num(e["shipping_fulfillment_per_order"], "shipping_fulfillment_per_order", 0, src=path),
        "fee_pct": num(e["payment_fee_pct"], "payment_fee_pct", 0, 100, src=path) / 100,
        "fee_fixed": num(e.get("payment_fee_fixed", 0), "payment_fee_fixed", 0, src=path),
        "returns": num(e["return_rate_pct"], "return_rate_pct", 0, 100, src=path) / 100,
        "discount": num(e["discount_pct"], "discount_pct", 0, 99, src=path) / 100,
        "max_inc": num(e.get("max_daily_increase_pct", 30), "max_daily_increase_pct", 1, 200, src=path) / 100,
        "max_dec": num(e.get("max_daily_decrease_pct", 50), "max_daily_decrease_pct", 1, 100, src=path) / 100,
        "cap": None,
        "aov": None,
        "demand_index": None,
        "min_daily": None,
        "low": 0.85, "high": 1.10,
    }
    if e.get("total_budget_cap") is not None:
        out["cap"] = num(e["total_budget_cap"], "total_budget_cap", 1, src=path)
    if e.get("bfcm_aov") is not None:
        out["aov"] = num(e["bfcm_aov"], "bfcm_aov", 1, src=path)
    out["aov_full"] = None
    if e.get("full_price_aov") is not None:
        out["aov_full"] = num(e["full_price_aov"], "full_price_aov", 1, src=path)
    if e.get("demand_index") is not None:
        out["demand_index"] = num(e["demand_index"], "demand_index", 0.1, 10, src=path)
    if e.get("min_daily_spend") is not None:
        out["min_daily"] = num(e["min_daily_spend"], "min_daily_spend", 0, src=path)
    sc = e.get("scenarios") or {}
    if not isinstance(sc, dict):
        raise InputError("scenarios must be an object like {\"low\": 0.85, \"high\": 1.10}")
    out["low"] = num(sc.get("low", 0.85), "scenarios.low", 0.1, 1, src=path)
    out["high"] = num(sc.get("high", 1.10), "scenarios.high", 1, 5, src=path)

    t = e["target"]
    if not isinstance(t, dict) or "type" not in t:
        raise InputError("target must be an object with a 'type' of 'margin_floor' or 'mer'")
    if t["type"] == "margin_floor":
        out["target"] = ("margin_floor", num(t.get("min_contribution_pct"), "target.min_contribution_pct", 0, 99, src=path) / 100)
    elif t["type"] == "mer":
        out["target"] = ("mer", num(t.get("mer"), "target.mer", 0.5, 100, src=path))
    else:
        raise InputError(f"target.type must be 'margin_floor' or 'mer' (got {t['type']!r})")

    # Plan window and key dates.
    w = e.get("plan_window") or {}
    year = num(e.get("plan_year", 2026), "plan_year", 2000, 2100, src=path)
    if year != int(year):
        raise InputError(f"{path}: plan_year must be a whole year (got {e.get('plan_year')!r})")
    year = int(year)
    bf = parse_date(e["black_friday"], "black_friday") if e.get("black_friday") else black_friday(year)
    if bf.weekday() != 4:
        raise InputError(f"{path}: black_friday {bf.isoformat()} is a {bf.strftime('%A')}, not a Friday "
                         f"(Black Friday {bf.year} is {black_friday(bf.year).isoformat()}); last year's curve is "
                         "aligned on it, so a wrong date shifts every day of the plan")
    out["bf"] = bf
    out["cm"] = bf + dt.timedelta(days=3)
    if e.get("cyber_monday"):
        cmk = parse_date(e["cyber_monday"], "cyber_monday")
        if cmk != out["cm"]:
            raise InputError(f"{path}: cyber_monday {cmk.isoformat()} must be the Monday after black_friday "
                             f"({out['cm'].isoformat()})")
    out["start"] = parse_date(w.get("start", f"{bf.year}-11-01"), "plan_window.start")
    out["end"] = parse_date(w.get("end", f"{bf.year}-12-06"), "plan_window.end")
    cw = e.get("cyber_week") or {}
    out["cw_start"] = parse_date(cw.get("start", (bf - dt.timedelta(days=1)).isoformat()), "cyber_week.start")
    out["cw_end"] = parse_date(cw.get("end", (bf + dt.timedelta(days=4)).isoformat()), "cyber_week.end")
    if not out["start"] <= out["cw_start"] <= out["cw_end"] <= out["end"]:
        raise InputError("cyber_week must sit inside plan_window (start <= cyber week start <= end <= window end)")
    if not out["cw_start"] <= bf <= out["cw_end"]:
        raise InputError("black_friday must fall inside cyber_week")
    if (out["end"] - out["start"]).days > 92:
        raise InputError("plan_window is longer than 93 days")

    # Sale days: the discount applies only on these days; the rest of the window sells at full price.
    sw = e.get("sale_window")
    out["sale_set"] = sw is not None
    if sw is None:
        out["sale_start"], out["sale_end"] = out["start"], out["end"]
    else:
        if not isinstance(sw, dict) or "start" not in sw or "end" not in sw:
            raise InputError('sale_window must be an object like {"start": "2026-11-24", "end": "2026-12-01"}')
        s0, s1 = parse_date(sw["start"], "sale_window.start"), parse_date(sw["end"], "sale_window.end")
        if s0 > s1:
            raise InputError("sale_window.start is after sale_window.end")
        if s1 < out["start"] or s0 > out["end"]:
            raise InputError("sale_window does not overlap plan_window")
        out["sale_start"], out["sale_end"] = max(s0, out["start"]), min(s1, out["end"])

    # Channel share bounds.
    shares = e.get("channel_shares") or {}
    if not isinstance(shares, dict):
        raise InputError("channel_shares must be an object keyed by channel")
    lo, hi = {}, {}
    for c in shares:
        if c not in CHANNELS:
            raise InputError(f"channel_shares has unknown channel {c!r} (use {', '.join(CHANNELS)})")
    for c in CHANNELS:
        s = shares.get(c) or {}
        lo[c] = num(s.get("min", 0), f"channel_shares.{c}.min", 0, 100, src=path) / 100
        hi[c] = num(s.get("max", 100), f"channel_shares.{c}.max", 0, 100, src=path) / 100
        if lo[c] > hi[c]:
            raise InputError(f"channel_shares.{c}: min is above max")
    if sum(lo.values()) > 1 + 1e-9:
        raise InputError("channel_shares minimums add up to more than 100%")
    if sum(hi.values()) < 1 - 1e-9:
        raise InputError("channel_shares maximums add up to less than 100%")
    out["share_lo"], out["share_hi"] = lo, hi
    return out


# --------------------------------------------------------------------------- math
def fit_loglog(points: list[tuple[float, float]]) -> dict:
    """Fit log(revenue) = a + b*log(spend). Returns b, r2, n, spend range."""
    pts = [(s, r) for s, r in points if s > 0 and r > 0]
    n = len(pts)
    res = {"n": n, "lo": min((s for s, _ in pts), default=0.0), "hi": max((s for s, _ in pts), default=0.0),
           "b": DEFAULT_ELASTICITY, "r2": None, "fitted": False, "clamped": False, "raw_b": None, "note": ""}
    if n < MIN_FIT_DAYS:
        res["note"] = f"only {n} days; default used"
        return res
    xs = [math.log(s) for s, _ in pts]
    ys = [math.log(r) for _, r in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx < n * 0.05 ** 2:
        res["note"] = "spend barely varied; default used"
        return res
    b = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    res["r2"] = r2
    if r2 < MIN_FIT_R2:
        res["note"] = f"weak fit (r² {r2:.2f}); default used"
        return res
    res["raw_b"] = b
    if b > ELASTICITY_MAX or b < ELASTICITY_MIN:      # out of bounds: an assumption, not a fit
        res["clamped"] = True
        res["b"] = min(ELASTICITY_MAX, max(ELASTICITY_MIN, b))
        res["note"] = f"assumed {res['b']:.2f} (clamped from {b:.2f})"
        if b > ELASTICITY_MAX:
            res["note"] += "; demand rose with spend"
        return res
    res["fitted"] = True
    res["b"] = b
    return res


def unit_economics(e: dict, aov: float, d: float) -> dict:
    """Contribution per $1 of net sales (gross - discount d, before returns/refunds), before ads."""
    r = e["returns"]
    cogs = (1 - e["gm"]) / (1 - d)          # COGS per $1 of discounted sales
    lines = {
        "kept_sales": 1 - r,
        "cogs": -cogs * (1 - r),               # returned goods are restocked
        "payment_fees": -(e["fee_pct"] + e["fee_fixed"] / aov),
        "shipping_fulfillment": -e["ship"] / aov,
    }
    cm = sum(lines.values())
    be = 1 / cm if cm > 0 else math.inf
    kind, val = e["target"]
    if kind == "margin_floor":
        floor = val
        tgt = 1 / (cm - floor) if cm - floor > 0 else math.inf
    else:
        tgt = val
        floor = cm - 1 / val
    return {"lines": lines, "cm": cm, "breakeven_mer": be, "target_mer": tgt, "floor": floor,
            "target_kind": kind, "aov": aov, "discount": d}


def clamp_shares(base: dict, lo: dict, hi: dict) -> dict:
    total = sum(base.values())
    w = {c: (base[c] / total if total > 0 else 1 / len(CHANNELS)) for c in CHANNELS}
    fixed: dict = {}
    for _ in range(len(CHANNELS) + 1):
        free = [c for c in CHANNELS if c not in fixed]
        left = 1 - sum(fixed.values())
        wsum = sum(w[c] for c in free)
        cand = {c: (left * w[c] / wsum if wsum > 0 else left / len(free)) for c in free}
        bad = {c: (lo[c] if v < lo[c] else hi[c]) for c, v in cand.items() if v < lo[c] - 1e-12 or v > hi[c] + 1e-12}
        if not bad:
            fixed.update(cand)
            break
        fixed.update(bad)
    return {c: fixed[c] for c in CHANNELS}


def split_int(total: int, shares: dict) -> dict:
    exact = {c: total * shares[c] for c in CHANNELS}
    out = {c: int(math.floor(exact[c] + 1e-9)) for c in CHANNELS}
    rem = total - sum(out.values())
    order = sorted(CHANNELS, key=lambda c: (-(exact[c] - out[c]), CHANNELS.index(c)))
    for c in order[:rem]:
        out[c] += 1
    return out


# --------------------------------------------------------------------------- planning
def phase_of(d: dt.date, e: dict) -> str:
    if d < e["cw_start"]:
        return "warm-up"
    return "cyber week" if d <= e["cw_end"] else "taper"


def plan(history: list[dict], e: dict, ignored_cols: list[str], today: dt.date | None = None,
         start_budget: float | None = None, accept_index: bool = False) -> dict:
    warnings: list[str] = []
    if ignored_cols:
        warnings.append(f"Ignored platform-reported column(s) {', '.join(ignored_cols)}. This plan uses store revenue "
                        "and total ad spend only; every platform claims credit for the same sale.")
    by_date = {r["date"]: r for r in history}
    ly_offset = e["bf"] - black_friday(e["bf"].year - 1)
    days = [e["start"] + dt.timedelta(days=i) for i in range((e["end"] - e["start"]).days + 1)]

    # Last year's aligned curve.
    ly_rows, missing = [], []
    for d in days:
        r = by_date.get(d - ly_offset)
        if r is None:
            missing.append((d - ly_offset).isoformat())
        elif r["spend"] <= 0 or r["net_sales"] <= 0:
            raise InputError(f"last year's {r['date']} has zero spend or sales; the curve needs both on every aligned day")
        else:
            ly_rows.append(r)
    if missing:
        raise InputError(f"history is missing {len(missing)} of last year's aligned days "
                         f"({missing[0]} … {missing[-1]}); last year's Oct–Dec must be complete")
    ly_total_rev = sum(r["net_sales"] for r in ly_rows)

    # This year's trailing days (before the window).
    ty = [r for r in history if r["date"].year == e["start"].year and r["date"] < e["start"]]
    if any(r["date"] >= e["start"] for r in history if r["date"].year == e["start"].year):
        raise InputError("history contains dates inside or after the plan window; plan before the window starts")
    if not ty:
        raise InputError("history has no days from this year; add the trailing 90 days")
    as_of = ty[-1]["date"]
    ref = e["start"] if today is None else min(today, e["start"])
    if (ref - as_of).days > STALE_DAYS:
        what = "the plan window starts" if ref == e["start"] else f"today ({ref.isoformat()})"
        warnings.append(f"History is stale: it ends {as_of.isoformat()}, {(ref - as_of).days} days before {what}. "
                        "The run rate, channel mix and demand index describe that time, not now. Add the missing "
                        "days and re-run.")
    t0 = as_of - dt.timedelta(days=TRAILING_DAYS - 1)
    trail = [r for r in ty if r["date"] >= t0]
    gaps = (as_of - max(t0, ty[0]["date"])).days + 1 - len(trail)
    if gaps >= MISSING_WARN_DAYS:
        warnings.append(f"History is missing {gaps} of the last {TRAILING_DAYS} days ({fmt_range(t0, as_of)}). "
                        "The demand index, run rate and channel mix use only the days present; add the missing days.")
    span = (ty[-1]["date"] - ty[0]["date"]).days + 1
    if span < SHORT_HISTORY_DAYS or len(ty) < SHORT_HISTORY_DAYS:
        warnings.append(f"History too short: only {len(ty)} days from this year (90 recommended, "
                        f"{SHORT_HISTORY_DAYS} minimum). The demand index and run rate are less reliable; "
                        "treat the base scenario as a rough guide.")

    # Elasticity per phase (fitted on last year's aligned days) and October.
    fits = {}
    for p in PHASES:
        fits[p] = fit_loglog([(r["spend"], r["net_sales"]) for d, r in zip(days, ly_rows) if phase_of(d, e) == p])
    ly_oct = [r for r in history if r["date"].year == e["start"].year - 1 and r["date"].month == 10
              and r["spend"] > 0 and r["net_sales"] > 0]
    fits["october"] = fit_loglog([(r["spend"], r["net_sales"]) for r in ly_oct])

    # Demand index: this year's trailing days vs last October's fit.
    idx_note = ""
    idx_skipped = 0
    if e["demand_index"] is not None:
        idx = e["demand_index"]
        idx_note = "set in economics.json"
    elif len(ly_oct) < 14 or sum(1 for r in trail if r["spend"] > 0) < MIN_INDEX_DAYS:
        idx = 1.0
        idx_note = "default 1.00: not enough history to estimate"
        warnings.append("Demand index defaulted to 1.00 (needs 14+ days of last October and 7+ days with ad spend "
                        "from this year). Set demand_index in economics.json if you know your year-over-year trend.")
    else:
        b = fits["october"]["b"]
        a = math.exp(sum(math.log(r["net_sales"]) - b * math.log(r["spend"]) for r in ly_oct) / len(ly_oct))
        used = [r for r in trail if r["spend"] > 0]      # zero-spend days leave both sides of the ratio
        idx_skipped = len(trail) - len(used)
        pred = sum(a * r["spend"] ** b for r in used)
        idx = sum(r["net_sales"] for r in used) / pred
        idx_note = (f"estimated: {fmt_range(trail[0]['date'], trail[-1]['date'])} actual sales vs what last "
                    f"October's curve predicts at the same spend")
        if idx_skipped:
            idx_note += (f"; {idx_skipped} zero-spend day{'' if idx_skipped == 1 else 's'} left out of both sales "
                         "and prediction")
        if idx_skipped > INDEX_SKIP_WARN * len(trail):
            warnings.append(f"{idx_skipped} of the {len(trail)} trailing days ({idx_skipped / len(trail) * 100:.0f}%) "
                            "had no ad spend and were left out of the demand index. It rests on the remaining "
                            f"{len(used)} days; check whether ads were paused or spend is missing from the export.")
        lo_i, hi_i = INDEX_FAIL
        if not lo_i <= idx <= hi_i and not accept_index:
            raise InputError(f"estimated demand index {idx:.2f} is outside {lo_i:g}–{hi_i:g}: this year's sales vs "
                             "last October's curve is implausible, which usually means missing or mislabelled spend "
                             "or sales. Check the history; if the index is real, re-run with --accept-demand-index, "
                             "or set demand_index in economics.json")
        if not INDEX_WARN[0] <= idx <= INDEX_WARN[1]:
            warnings.append(f"Demand index {idx:.2f} is outside {INDEX_WARN[0]:.1f}–{INDEX_WARN[1]:.1f}: every "
                            "projection is scaled by it. Check the trailing history for missing spend or one-off "
                            "sales before trusting the plan.")

    rr_rows = [r for r in ty if r["date"] > as_of - dt.timedelta(days=RUN_RATE_DAYS)]
    run_rate = sum(r["spend"] for r in rr_rows) / len(rr_rows)
    start = run_rate
    if start_budget is not None:
        start = start_budget
    elif run_rate <= 0:
        nz = [r for r in ty if r["spend"] > 0]
        if not nz:
            raise InputError("this year's history has no ad spend at all, so there is no run rate to ramp from; "
                             "pass --starting-daily-budget")
        last = nz[-1]["date"]
        blk = [r for r in ty if last - dt.timedelta(days=RUN_RATE_DAYS - 1) <= r["date"] <= last]
        start = sum(r["spend"] for r in blk) / len(blk)
        warnings.append(f"Ads were paused in the last {RUN_RATE_DAYS} days (run rate $0/day). The ramp starts from "
                        f"{money(start)}/day, the average of the {RUN_RATE_DAYS} days to {fmt_day(last)}, the last "
                        "time ads ran. Pass --starting-daily-budget to set the starting budget yourself.")
    floor = e["min_daily"] if e["min_daily"] is not None else start
    g, dec = e["max_inc"], e["max_dec"]

    # AOV, unit economics (sale days and full-price days) and new-customer share.
    cw_ly = [r for d, r in zip(days, ly_rows) if phase_of(d, e) == "cyber week"]
    aov = e["aov"] or sum(r["net_sales"] for r in cw_ly) / max(1.0, sum(r["total_orders"] for r in cw_ly))
    on_sale = [e["discount"] > 0 and e["sale_start"] <= d <= e["sale_end"] for d in days]
    fp_ly = [r for r, s in zip(ly_rows, on_sale) if not s]   # last year's days aligned to this plan's full-price days
    aov_full = e["aov_full"] or (sum(r["net_sales"] for r in fp_ly) / sum(r["total_orders"] for r in fp_ly)
                                 if fp_ly and sum(r["total_orders"] for r in fp_ly) > 0 else aov)
    ue = unit_economics(e, aov, e["discount"])           # days inside sale_window
    ue_full = unit_economics(e, aov_full, 0.0)            # days outside it sell at full price, at a non-sale AOV
    cm, kind = ue["cm"], ue["target_kind"]
    cms = [ue["cm"] if s else ue_full["cm"] for s in on_sale]   # contribution before ads per $1 of net sales, by day
    newshare = {}
    for p in PHASES:
        rs = [r for d, r in zip(days, ly_rows) if phase_of(d, e) == p]
        tot = sum(r["total_orders"] for r in rs)
        newshare[p] = sum(r["new_customer_orders"] for r in rs) / tot if tot else 0.0

    # Day response: revenue(s) = idx * LYrev * (s / LYspend)^b
    bs = [fits[phase_of(d, e)]["b"] for d in days]
    phases = [phase_of(d, e) for d in days]

    def demand(elast: list[float]) -> list[float]:
        D_raw = [idx * r["net_sales"] / r["spend"] ** b for r, b in zip(ly_rows, elast)]
        out = []
        for i, p in enumerate(phases):                   # one lucky day must not steer the plan
            if p == "cyber week":
                out.append(D_raw[i])
                continue
            near = [D_raw[j] for j in range(max(0, i - SMOOTH_RADIUS), min(len(days), i + SMOOTH_RADIUS + 1))
                    if phases[j] == p]
            out.append(sum(near) / len(near))
        return out

    D = demand(bs)

    def project(s: list[int], factor: float = 1.0) -> list[float]:
        return [factor * Dd * (x ** b if x > 0 else 0.0) for Dd, x, b in zip(D, s, bs)]

    def project_at(s: list[int], elast: float) -> list[float]:   # the same spend on a different curve
        Ds = demand([elast] * len(days))
        return [Dd * (x ** elast if x > 0 else 0.0) for Dd, x in zip(Ds, s)]

    short: list[int] = []

    def build(k: float) -> list[int]:
        # Each day spends until its next dollar earns k dollars of contribution before ads
        # (marginal MER = k / that day's contribution rate). k = 1 is marginal breakeven.
        raw = [max(floor, (c * b * Dd / k) ** (1 / (1 - b))) if c > 0 else floor for c, Dd, b in zip(cms, D, bs)]
        for i in range(len(raw) - 2, -1, -1):           # start the ramp early enough
            raw[i] = max(raw[i], raw[i + 1] / (1 + g))
        out, prev = [], int(math.floor(max(start, floor)))
        short.clear()
        for x in raw:                                     # never jump more than g (or drop more than dec) a day
            lo_b = int(math.ceil(prev * (1 - dec) - 1e-9))   # whole dollars that keep both limits
            hi_b = int(math.floor(prev * (1 + g) + 1e-9))
            v = min(max(int(math.floor(x + 1e-9)), lo_b), hi_b)
            if v < x * 0.98 - 1:                          # (whole-dollar rounding alone stays within this)
                short.append(len(out))                    # the ramp limit, not the economics, set this day
            out.append(v)
            prev = v
        return out

    def kept_share(s: list[int], rev_days: list[float]) -> float:
        rev = sum(rev_days)
        return (sum(c * r for c, r in zip(cms, rev_days)) - sum(s)) / rev if rev > 0 else 0.0

    def meets_target(s: list[int], rev_days: list[float]) -> bool:
        tot = sum(s)
        if tot <= 0:
            return True
        if kind == "margin_floor":
            return sum(rev_days) > 0 and kept_share(s, rev_days) >= ue["floor"] - 1e-9
        return sum(rev_days) / tot >= ue["target_mer"] - 1e-9

    def ok(s: list[int]) -> bool:
        if e["cap"] is not None and sum(s) > e["cap"] + 1e-9:
            return False
        return meets_target(s, project(s))

    ramp_min = int(math.floor(floor)) * len(days)
    unreachable = all(c <= 0 for c in cms) or (kind == "margin_floor" and all(c <= ue["floor"] for c in cms))
    binding = ""
    hi_plan = build(1e9)
    if e["cap"] is not None and sum(hi_plan) > e["cap"]:
        per = int(math.floor(e["cap"] / len(days)))
        spend = [per] * len(days)
        binding = "budget cap below ramp minimum"
        warnings.append(f"Budget cap {money(e['cap'])} is below the ramp minimum {money(ramp_min)} "
                        f"(holding today's {money(floor)}/day for {len(days)} days). The plan runs a flat "
                        f"{money(per)}/day, below today's spend, with no room to ramp into Black Friday. "
                        "Raise the cap or shorten the window.")
    elif unreachable or not ok(hi_plan):
        spend = hi_plan
        binding = "ramp floor (min_daily_spend)" if e["min_daily"] is not None else "ramp floor (today's run rate)"
    elif ok(build(1.0)):
        spend = build(1.0)
        binding = "marginal breakeven (the next dollar would lose money)"
    else:
        lo, hi = 0.0, math.log(1e9)
        for _ in range(200):
            mid = (lo + hi) / 2
            if ok(build(math.exp(mid))):
                hi = mid
            else:
                lo = mid
        spend = build(math.exp(hi))
        cap_hit = e["cap"] is not None and sum(build(math.exp(lo))) > e["cap"]
        binding = "budget cap" if cap_hit else ("margin floor" if kind == "margin_floor" else "target MER")
    if not binding.startswith(("budget cap below", "ramp floor")):
        build_k = {"marginal breakeven (the next dollar would lose money)": 1.0}.get(binding)
        build(build_k if build_k is not None else math.exp(hi))     # refresh `short` for the chosen plan
        if short:
            binding = (f"ramp limit (+{g * 100:.0f}% a day from {money(max(start, floor))}/day can't reach the spend "
                       f"the economics support until after {fmt_day(days[short[-1]])})")
    if sum(spend) == 0:
        binding = "no spend (the ramp starts at $0/day)"

    off = f"{e['discount'] * 100:.0f}% off"
    if cm <= 0:
        warnings.append(f"Breakeven MER is unreachable: at {off}, each order loses money before "
                        f"any ad spend (contribution {pct(cm)} of net sales). No amount of efficiency fixes this. Reduce the "
                        "discount depth (run Promo Margin Guard for the breakeven depth per product). The plan holds "
                        "sale days at today's run rate to show the cost.")
    elif kind == "margin_floor" and math.isinf(ue["target_mer"]):
        warnings.append(f"Target is unreachable at {off}: contribution before ads is {pct(cm)} of net sales, below the "
                        f"{pct(ue['floor'])} margin floor even with zero ad spend. Lower the discount or the floor.")
    projected = project(spend)
    best = max(((c * rv / s, i) for i, (c, rv, s) in enumerate(zip(cms, projected, spend)) if s > 0 and c > 0),
               default=None)
    if best is not None and best[0] < 1:
        i = best[1]
        warnings.append(f"Breakeven MER {mer(1 / cms[i])} is unreachable: even the best planned day ({fmt_day(days[i])}) "
                        f"projects MER {mer(projected[i] / spend[i])}. Every day of this plan loses money after ads.")
    tot_s, tot_r = sum(spend), sum(projected)
    if not unreachable and binding.startswith("ramp floor") and not meets_target(spend, projected):
        if kind == "margin_floor":
            warnings.append(f"Even holding today's run rate ({money(floor)}/day), the plan keeps "
                            f"{pct(kept_share(spend, projected))} of net sales after ads, below the {pct(ue['floor'], 0)} "
                            "floor. Either accept a lower margin, lower min_daily_spend, or cut the discount.")
        else:
            warnings.append(f"Even holding today's run rate ({money(floor)}/day), projected MER {mer(tot_r / tot_s)} is "
                            f"below the target {mer(ue['target_mer'])}. Either accept a lower margin, lower "
                            "min_daily_spend, or cut the discount.")
    extrap = [d for d, s, r in zip(days, spend, ly_rows) if s > EXTRAPOLATION_MULT * r["spend"]]
    if extrap:
        warnings.append(f"On {len(extrap)} day(s) ({fmt_day(extrap[0])}"
                        f"{'' if len(extrap) == 1 else ' … ' + fmt_day(extrap[-1])}) the plan spends more than "
                        f"{EXTRAPOLATION_MULT:.0f}x last year. Revenue there is extrapolated beyond the fitted range.")
    assumed = []
    for p in ("october",) + PHASES:
        f = fits[p]
        label = "Last October's" if p == "october" else p.capitalize()
        if f["clamped"]:
            bound = ("the ceiling, the most optimistic response to extra spend allowed" if f["b"] >= ELASTICITY_MAX
                     else "the floor")
            warnings.append(f"{label} elasticity is assumed, not fitted: last year's raw fit was {f['raw_b']:.2f}, "
                            f"outside 0.20–0.70, so {f['b']:.2f} ({bound}) is used.")
        elif not f["fitted"] and p != "october":
            warnings.append(f"{label} diminishing-returns curve not fitted ({f['note']}); "
                            f"elasticity {DEFAULT_ELASTICITY:.2f} assumed.")
        if not f["fitted"] and p != "october":
            assumed.append(p)
    sens = project_at(spend, SENS_ELASTICITY)
    sens_c = sum(c * r for c, r in zip(cms, sens)) - sum(spend)
    sensitivity = {"elasticity": SENS_ELASTICITY, "sales": sum(sens), "mer": sum(sens) / sum(spend) if sum(spend) else None,
                   "contribution": sens_c, "margin": sens_c / sum(sens) if sum(sens) else None,
                   "black_friday_sales": sens[days.index(e["bf"])]}
    if "cyber week" in assumed:
        warnings.append(f"The Black Friday budget rests on an assumed cyber-week curve, not a fitted one. If the real "
                        f"elasticity were {SENS_ELASTICITY:.2f} in every phase, this same budget would bring "
                        f"{money(sensitivity['sales'])} net sales (MER {mer(sensitivity['mer'])}, "
                        f"{money(sens_c)} contribution after ads, {pct(sensitivity['margin'])} of net sales).")

    # Channel split. A channel with no spend in the trailing days (and no minimum share) gets no budget.
    base_mix = {c: sum(r[f"{c}_spend"] for r in trail) for c in CHANNELS}
    shares = clamp_shares(base_mix, e["share_lo"], e["share_hi"])
    active = [c for c in CHANNELS if shares[c] > 0]

    rows = []
    prev = None
    for i, (d, s) in enumerate(zip(days, spend)):
        p = phase_of(d, e)
        rv = projected[i]
        orders = rv / aov
        tag = "Black Friday" if d == e["bf"] else ("Cyber Monday" if d == e["cm"] else
                                                   ("Thanksgiving" if d == e["bf"] - dt.timedelta(days=1) else ""))
        rows.append({
            "date": d.isoformat(), "day": d.strftime("%a"), "phase": p, "tag": tag, "total": s,
            "channels": split_int(s, shares), "change": (s / prev - 1) if prev else None,
            "ly_date": (d - ly_offset).isoformat(), "ly_spend": ly_rows[i]["spend"], "ly_sales": ly_rows[i]["net_sales"],
            "ly_share": ly_rows[i]["net_sales"] / ly_total_rev, "ly_mer": ly_rows[i]["net_sales"] / ly_rows[i]["spend"],
            "sales": {"low": rv * e["low"], "base": rv, "high": rv * e["high"]},
            "on_sale": on_sale[i], "contribution_rate": cms[i],
            "mer": rv / s if s else None, "contribution": cms[i] * rv - s, "orders": orders,
            "new_customers": orders * newshare[p],
        })
        prev = s

    def summarize(rs: list[dict]) -> dict:
        s = sum(r["total"] for r in rs)
        out = {"spend": s, "days": len(rs), "new_customers": sum(r["new_customers"] for r in rs)}
        for k in ("low", "base", "high"):
            rev = sum(r["sales"][k] for r in rs)
            c = sum(r["contribution_rate"] * r["sales"][k] for r in rs) - s
            out[k] = {"sales": rev, "mer": rev / s if s else None, "contribution": c,
                      "margin": c / rev if rev else None}
        out["ncac"] = s / out["new_customers"] if out["new_customers"] else None
        return out

    phase_sum = {p: summarize([r for r in rows if r["phase"] == p]) for p in PHASES}
    total = summarize(rows)
    if total["low"]["contribution"] < 0:
        warnings.append(f"In the low scenario (demand {e['low']:.2f}x) the window loses "
                        f"{money(-total['low']['contribution'])} after ads. Use the guardrails to pull back early.")

    # Guardrails per day, from that day's own plan MER: pull back below max(breakeven, plan MER x low), scale above
    # max(target, plan MER x high). Invariant (at the 2-decimal values written out): pull back < plan MER < scale.
    # A day planned below breakeven gets its pull-back line below the plan and a warning, never a line above the plan.
    below_be = []
    for i in range(len(days)):
        u = ue if on_sale[i] else ue_full
        bev, tgv = u["breakeven_mer"], u["target_mer"]
        b0, t0 = (0.0 if math.isinf(bev) else bev), (0.0 if math.isinf(tgv) else tgv)
        pm = projected[i] / spend[i] if spend[i] else None
        if pm is None:
            pb, sc = b0, t0
        else:
            pb = max(b0, pm * e["low"])
            if round2(pb) >= pm:
                if b0 < pm:                               # breakeven just under the plan: round down to cents
                    pb = line_below(pb, pm)
                else:                                     # planned at or below breakeven: say so, keep the line below
                    below_be.append(i)
                    pb = line_below(pm * e["low"], pm)
            sc = max(t0, pm * e["high"])
            if round2(sc) <= pm:
                sc = line_above(pm * e["high"], pm)
        rows[i].update({"breakeven_mer": bev, "target_mer": tgv, "pull_back_mer": round2(pb), "scale_mer": round2(sc),
                        "check_times": [f"{h:02d}:00" for h in CHECK_HOURS[phases[i]]]})
    if below_be:
        d0, d1 = days[below_be[0]], days[below_be[-1]]
        warnings.append(f"On {len(below_be)} day(s) ({fmt_day(d0)}{'' if len(below_be) == 1 else ' … ' + fmt_day(d1)}) "
                        "the base-case MER is at or below breakeven MER: those days are planned to lose money after "
                        f"ads. Their pull-back line is plan MER x {e['low']:.2f}, below breakeven, not above the plan "
                        "(which would call PULL BACK on an on-plan day). Review those budgets.")

    # Phase summary: one row per run of days with the same phase and pricing (a sale that starts or ends inside a
    # phase splits it), showing the range of that run's daily lines.
    guard, runs = [], []
    for i in range(len(days)):
        if runs and phases[runs[-1][0]] == phases[i] and on_sale[runs[-1][0]] == on_sale[i]:
            runs[-1].append(i)
        else:
            runs.append([i])
    for run in runs:
        p, u = phases[run[0]], (ue if on_sale[run[0]] else ue_full)
        s = sum(spend[i] for i in run)
        pbs, scs = [rows[i]["pull_back_mer"] for i in run], [rows[i]["scale_mer"] for i in run]
        guard.append({"phase": p, "from": days[run[0]].isoformat(), "to": days[run[-1]].isoformat(), "days": len(run),
                      "on_sale": on_sale[run[0]], "discount": u["discount"],
                      "plan_mer": sum(projected[i] for i in run) / s if s else 0.0,
                      "breakeven_mer": u["breakeven_mer"], "target_mer": u["target_mer"],
                      "pull_back_min": min(pbs), "pull_back_max": max(pbs), "scale_min": min(scs), "scale_max": max(scs),
                      "check_times": rows[run[0]]["check_times"]})

    ly_phase = {}
    for p in PHASES:
        rs = [(d, r) for d, r in zip(days, ly_rows) if phase_of(d, e) == p]
        rev = sum(r["net_sales"] for _, r in rs)
        sp = sum(r["spend"] for _, r in rs)
        peak = max(rs, key=lambda x: x[1]["net_sales"])
        ly_phase[p] = {"from": rs[0][1]["date"], "to": rs[-1][1]["date"], "days": len(rs), "sales": rev,
                       "share": rev / ly_total_rev, "spend": sp, "mer": rev / sp,
                       "peak_day": peak[1]["date"], "peak_sales": peak[1]["net_sales"]}

    months: dict = {}
    for r in rows:
        m = r["date"][:7]
        months.setdefault(m, {"total": 0, **{c: 0 for c in CHANNELS}})
        months[m]["total"] += r["total"]
        for c in CHANNELS:
            months[m][c] += r["channels"][c]

    sale_days = sum(on_sale)
    return {
        "brand": e["brand"], "currency": e["currency"], "as_of": as_of.isoformat(),
        "window": {"start": e["start"].isoformat(), "end": e["end"].isoformat(), "days": len(days),
                   "cyber_week": [e["cw_start"].isoformat(), e["cw_end"].isoformat()],
                   "black_friday": e["bf"].isoformat(), "cyber_monday": e["cm"].isoformat(),
                   "last_year_offset_days": ly_offset.days,
                   "sale_window": [e["sale_start"].isoformat(), e["sale_end"].isoformat()],
                   "sale_window_set": e["sale_set"], "sale_days": sale_days},
        "economics": {**ue, "discount": e["discount"], "returns": e["returns"], "gross_margin": e["gm"],
                      "max_daily_increase": g, "max_daily_decrease": dec, "cap": e["cap"], "floor_daily": floor, "run_rate": run_rate,
                      "ramp_start": max(start, floor), "ramp_start_set": start_budget is not None,
                      "ramp_minimum": ramp_min, "split_pricing": 0 < sale_days < len(days),
                      "full_price": {k: ue_full[k] for k in ("lines", "cm", "breakeven_mer", "target_mer", "floor", "aov")}},
        "demand_index": idx, "demand_index_note": idx_note, "demand_index_skipped_days": idx_skipped,
        "sensitivity": sensitivity, "fits": fits, "ly_phase": ly_phase,
        "ly_total_sales": ly_total_rev, "binding": binding, "shares": shares, "active_channels": active, "rows": rows,
        "phases": phase_sum, "total": total, "guardrails": guard, "months": months,
        "scenarios": {"low": e["low"], "high": e["high"]}, "warnings": warnings,
    }

# --------------------------------------------------------------------------- rendering
def render(a: dict) -> str:
    w, ec, t = a["window"], a["economics"], a["total"]
    fp, split, act = ec["full_price"], ec["split_pricing"], a["active_channels"]
    start, end = dt.date.fromisoformat(w["start"]), dt.date.fromisoformat(w["end"])
    cw0, cw1 = (dt.date.fromisoformat(x) for x in w["cyber_week"])
    bf, cmd = dt.date.fromisoformat(w["black_friday"]), dt.date.fromisoformat(w["cyber_monday"])
    sale0, sale1 = (dt.date.fromisoformat(x) for x in w["sale_window"])
    off = f"{ec['discount'] * 100:.0f}% off"
    L = []
    L.append(f"## BFCM Budget Plan: {a['brand']}, {fmt_range(start, end)} {end.year}")
    L.append("")
    sale_txt = f", sale {fmt_span(sale0, sale1)} ({off})" if split else ""
    L.append(f"_History through {a['as_of']}. Black Friday {fmt_day(bf)}, Cyber Monday {fmt_day(cmd)}, "
             f"cyber week {fmt_range(cw0, cw1)}{sale_txt}. Amounts in {a['currency']}._")
    L.append("")
    b = t["base"]
    L.append(f"**Plan: {money(t['spend'])} ad spend over {w['days']} days. Base case {money(b['sales'])} net sales, "
             f"MER {mer(b['mer'])}, {money(b['contribution'])} contribution after ads ({pct(b['margin'])} of net sales).**")
    L.append("")
    if not split:
        tgt_txt = (f"MER ≥ {mer(ec['target_mer'])} ({pct(ec['floor'], 0)} contribution floor)"
                   if ec["target_kind"] == "margin_floor" else f"MER ≥ {mer(ec['target_mer'])} (contribution {pct(ec['floor'])})")
        L.append(f"- Target: {tgt_txt}. Breakeven MER at {off}: **{mer(ec['breakeven_mer'])}**.")
    else:
        def at_least(x: float, where: str) -> str:
            return f"out of reach {where}" if math.isinf(x) else f"≥ {mer(x)} {where}"

        def be_at(u: dict, where: str) -> str:
            return f"none {where}" if math.isinf(u["breakeven_mer"]) else f"**{mer(u['breakeven_mer'])}** {where}"

        tgt_txt = (f"keep {pct(ec['floor'], 0)} of net sales after ads across the window (MER "
                   f"{at_least(ec['target_mer'], 'on sale days')}, {at_least(fp['target_mer'], 'at full price')})"
                   if ec["target_kind"] == "margin_floor" else
                   f"MER ≥ {mer(ec['target_mer'])} across the window (contribution {pct(ec['floor'])} on sale days, "
                   f"{pct(fp['floor'])} at full price)")
        L.append(f"- Target: {tgt_txt}. Breakeven MER: {be_at(ec, 'at ' + off)}, {be_at(fp, 'at full price')}.")
    cap = money(ec["cap"]) if ec["cap"] is not None else "none"
    ramp = ("" if abs(ec["ramp_start"] - ec["run_rate"]) < 0.5 else
            f", ramp starts from {money(ec['ramp_start'])}/day" + (" (--starting-daily-budget)" if ec["ramp_start_set"] else ""))
    L.append(f"- Spend stops at: {a['binding']}. Budget cap: {cap}. Today's run rate {money(ec['run_rate'])}/day{ramp}; "
             f"day-over-day change capped at +{ec['max_daily_increase'] * 100:.0f}% / -{ec['max_daily_decrease'] * 100:.0f}%.")
    peak = max(a["rows"], key=lambda r: r["total"])
    L.append(f"- Peak budget {money(peak['total'])} on {fmt_day(dt.date.fromisoformat(peak['date']))} "
             f"({peak['tag'] or peak['phase']}). Projected new customers {t['new_customers']:,.0f}, "
             f"blended new-customer cost {money(t['ncac']) if t['ncac'] else '—'}.")
    L.append("")
    if a["warnings"]:
        L.append("**Warnings**")
        for x in a["warnings"]:
            L.append(f"- {x}")
        L.append("")

    L.append("### Unit economics (per $100 of net sales)" if split else
             f"### Unit economics at {off} (per $100 of net sales)")
    L.append("")
    if split:
        note = f" The {off} sale runs {fmt_span(sale0, sale1)}; the other days of the window sell at full price."
    elif ec["discount"] > 0 and not w["sale_window_set"]:
        note = f" The {off} price is applied to every day of the window; set sale_window if your sale runs on fewer days."
    else:
        note = ""
    aov_txt = (f"AOV {money(ec['aov'])}" if round(ec["aov"]) == round(fp["aov"]) or not split else
               f"AOV {money(ec['aov'])} on sale days, {money(fp['aov'])} at full price")
    L.append(f"Net sales = gross sales − discounts, before returns/refunds, excluding tax and shipping. {aov_txt}. Gross margin {pct(ec['gross_margin'], 0)} at full price, "
             f"returns {pct(ec['returns'], 0)} (goods restocked, shipping and fees not recovered).{note}")
    L.append("")
    names = {"kept_sales": "Sales kept after returns", "cogs": "COGS on kept goods",
             "payment_fees": "Payment fees", "shipping_fulfillment": "Shipping and fulfillment"}
    if split:
        L.append(f"| Line | {off} | Full price |")
        L.append("| --- | ---: | ---: |")
        for k, v in ec["lines"].items():
            L.append(f"| {names[k]} | {v * 100:,.2f} | {fp['lines'][k] * 100:,.2f} |")
        L.append(f"| **Contribution before ads** | **{ec['cm'] * 100:,.2f}** | **{fp['cm'] * 100:,.2f}** |")
    else:
        L.append("| Line | Per $100 |")
        L.append("| --- | ---: |")
        for k, v in ec["lines"].items():
            L.append(f"| {names[k]} | {v * 100:,.2f} |")
        L.append(f"| **Contribution before ads** | **{ec['cm'] * 100:,.2f}** |")
    L.append("")

    def be_eq(u: dict) -> str:
        return f"100 / {u['cm'] * 100:.2f} = **{mer(u['breakeven_mer'])}**"

    def tg_eq(u: dict) -> str:
        return f"100 / ({u['cm'] * 100:.2f} − {u['floor'] * 100:.2f}) = **{mer(u['target_mer'])}**"

    if not split and ec["cm"] > 0:
        L.append(f"Breakeven MER = {be_eq(ec)}. "
                 + (f"Target MER = {tg_eq(ec)}."
                    if ec["target_kind"] == "margin_floor" and not math.isinf(ec["target_mer"]) else
                    f"Target MER {mer(ec['target_mer'])} leaves {pct(ec['floor'])} contribution."))
        L.append("")
    elif split:
        both = ((ec, off), (fp, "full price"))
        txt = "Breakeven MER = " + ", ".join(f"{be_eq(u)} at {lbl}" for u, lbl in both if u["cm"] > 0) + "."
        if ec["target_kind"] == "margin_floor":
            tg = [f"{tg_eq(u)} at {lbl}" for u, lbl in both if not math.isinf(u["target_mer"])]
            txt += (" Target MER = " + ", ".join(tg) + ".") if tg else ""
        else:
            txt += (f" Target MER {mer(ec['target_mer'])} leaves {pct(ec['floor'])} contribution at {off}, "
                    f"{pct(fp['floor'])} at full price.")
        L.append(txt)
        L.append("")

    L.append("### Last year's curve (aligned to Black Friday)")
    L.append("")
    L.append(f"Last year's dates are shifted {w['last_year_offset_days']} days so Black Friday lines up with Black Friday.")
    L.append("")
    L.append("| Phase | Last year's dates | Days | Net sales | Share of window | Ad spend | MER | Best day |")
    L.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for p in PHASES:
        x = a["ly_phase"][p]
        L.append(f"| {p} | {fmt_range(x['from'], x['to'])} | {x['days']} | {money(x['sales'])} | {pct(x['share'])} | "
                 f"{money(x['spend'])} | {mer(x['mer'])} | {fmt_day(x['peak_day'])} ({money(x['peak_sales'])}) |")
    L.append("")
    L.append("**Diminishing returns (estimate).** Log-log fit of daily net sales on daily ad spend within each phase. "
             "Elasticity 0.5 means doubling spend lifts sales about 41%. Fits on last year's days mix demand with spend, "
             "so read them as rough. A fit outside 0.20–0.70 is clamped to the nearer bound and labelled assumed, not fitted. Warm-up and taper days use a 7-day centered average of "
             "last year so one lucky day doesn't steer the plan; cyber-week days are kept as they were.")
    L.append("")
    L.append("| Curve | Days | Fitted on daily spend | Elasticity | r² | Note |")
    L.append("| --- | ---: | --- | ---: | ---: | --- |")
    for p in ("october",) + PHASES:
        f = a["fits"][p]
        label = "last October" if p == "october" else p
        L.append(f"| {label} | {f['n']} | {money(f['lo'])}–{money(f['hi'])} | {f['b']:.2f} | "
                 f"{'—' if f['r2'] is None else format(f['r2'], '.2f')} | {f['note'] or '—'} |")
    L.append("")
    sv = a["sensitivity"]
    L.append(f"Sensitivity: at elasticity {sv['elasticity']:.2f} in every phase, the same budget projects "
             f"{money(sv['sales'])} net sales (MER {mer(sv['mer'])}, {money(sv['contribution'])} contribution after ads, "
             f"{pct(sv['margin'])} of net sales; Black Friday {money(sv['black_friday_sales'])}).")
    L.append("")
    L.append(f"**Demand index {a['demand_index']:.2f}** ({a['demand_index_note']}). Every day's projection is last year's "
             "same day × this index, adjusted along the curve for the planned spend.")
    L.append("")

    L.append("### Daily plan (base scenario)")
    L.append("")
    L.append("| Date | Day | Phase |" + (" Price |" if split else "") + " Budget | "
             + " | ".join(CHANNEL_NAMES[c] for c in act)
             + " | vs prior day | Proj. net sales | MER | Contribution | Pull back below | Scale above |")
    L.append("| --- | --- | --- |" + (" --- |" if split else "") + " ---: |" * (len(act) + 7))
    for r in a["rows"]:
        d = dt.date.fromisoformat(r["date"])
        ch = r["channels"]
        chg = "—" if r["change"] is None else f"{r['change'] * 100:+.0f}%"
        tag = f" **{r['tag']}**" if r["tag"] else ""
        price = (f" {off} |" if r["on_sale"] else " full price |") if split else ""
        L.append(f"| {fmt_day(d)}{tag} | {r['day']} | {r['phase']} |{price} {money(r['total'])} | "
                 + " | ".join(money(ch[c]) for c in act)
                 + f" | {chg} | {money(r['sales']['base'])} | {mer(r['mer'])} | {money(r['contribution'])} | "
                 f"{mer(r['pull_back_mer'])} | {mer(r['scale_mer'])} |")
    L.append("| **Total** | | |" + (" |" if split else "") + f" **{money(t['spend'])}** | " + " | ".join(
        f"**{money(sum(r['channels'][c] for r in a['rows']))}**" for c in act)
        + f" | | **{money(b['sales'])}** | **{mer(b['mer'])}** | **{money(b['contribution'])}** | | |")
    L.append("")
    sh = a["shares"]
    L.append("Channel split: " + ", ".join(f"{c} {pct(sh[c], 0)}" for c in act)
             + " (this year's trailing mix, held inside your min/max). MER is blended across all channels on purpose: "
               "no per-channel revenue is projected.")
    L.append("")

    L.append("### Scenarios")
    L.append("")
    L.append("| Phase | Spend | Low sales | Low MER | Base sales | Base MER | High sales | High MER | Base contribution | Base margin |")
    L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, x in [(p, a["phases"][p]) for p in PHASES] + [("**Window**", t)]:
        L.append(f"| {name} | {money(x['spend'])} | {money(x['low']['sales'])} | {mer(x['low']['mer'])} | "
                 f"{money(x['base']['sales'])} | {mer(x['base']['mer'])} | {money(x['high']['sales'])} | "
                 f"{mer(x['high']['mer'])} | {money(x['base']['contribution'])} | {pct(x['base']['margin'])} |")
    L.append("")
    L.append(f"Low = demand {a['scenarios']['low']:.2f}x base, high = {a['scenarios']['high']:.2f}x, same spend. "
             f"Window contribution after ads: low {money(t['low']['contribution'])}, base {money(t['base']['contribution'])}, "
             f"high {money(t['high']['contribution'])}.")
    L.append("")

    L.append("### Guardrails")
    L.append("")
    L.append("MER to date = Shopify net sales so far today ÷ total ad spend so far today across every platform. "
             "Never use a platform's ROAS for these calls. Each day has its own lines, set from that day's plan MER: "
             f"pull back below the higher of breakeven MER and plan MER x {a['scenarios']['low']:.2f}; scale above the "
             f"higher of target MER and plan MER x {a['scenarios']['high']:.2f}. Every pull-back line sits below its "
             "day's plan MER and every scale line above it. They are the last two columns of the daily plan and the "
             "`pull_back_mer` and `scale_mer` columns of the `--csv` plan. The table below summarises them by phase.")
    L.append("")
    L.append("| Phase | Dates | Pricing | Plan MER | Breakeven MER | Target MER | Pull back if MER to date is below | "
             "Scale if above | Check at (store time) |")
    L.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")

    def rng(lo: float, hi: float) -> str:
        return mer(lo) if mer(lo) == mer(hi) else f"{mer(lo)}–{mer(hi)}"

    for gd in a["guardrails"]:
        span = fmt_span(dt.date.fromisoformat(gd["from"]), dt.date.fromisoformat(gd["to"]))
        L.append(f"| {gd['phase']} | {span} | {pricing(gd['discount'])} | {mer(gd['plan_mer'])} | "
                 f"{mer(gd['breakeven_mer'])} | {mer(gd['target_mer'])} | {rng(gd['pull_back_min'], gd['pull_back_max'])} | "
                 f"{rng(gd['scale_min'], gd['scale_max'])} | {check_txt(gd['check_times'])} |")
    L.append("")
    inc = f"{ec['max_daily_increase'] * 100:.0f}%"
    for gd in a["guardrails"]:
        g0, g1 = dt.date.fromisoformat(gd["from"]), dt.date.fromisoformat(gd["to"])
        h = check_txt(gd["check_times"][:1])
        extra = f" Check again at {check_txt(gd['check_times'][1:])}." if len(gd["check_times"]) > 1 else ""
        if g0 <= bf <= g1 or g0 <= cmd <= g1:
            extra += " On Black Friday and Cyber Monday, judge from noon onward, not the first hours after a midnight launch."
        whose = "the" if g0 == g1 else "that day's"
        L.append(f"- **{gd['phase'].capitalize()}, {fmt_span(g0, g1)} ({pricing(gd['discount'])}):** if MER to date is "
                 f"below {whose} pull-back line ({rng(gd['pull_back_min'], gd['pull_back_max'])}) "
                 f"by {h}, cut the rest of today's budgets 20% and hold tomorrow at today's level. If it is above "
                 f"{whose} scale line ({rng(gd['scale_min'], gd['scale_max'])}) by {h} two days "
                 f"running, raise tomorrow's budget by up to {inc}, never more.{extra}")
    L.append(f"- Every morning, compare yesterday's full-day MER with the plan row. Re-run the plan with the new days if "
             f"actuals miss the base case by more than {(1 - a['scenarios']['low']) * 100:.0f}% for three days.")
    L.append("")

    L.append("### Hand-off")
    L.append("")
    for m, v in a["months"].items():
        L.append(f"- {m}: {money(v['total'])} (" + ", ".join(f"{c} {money(v[c])}" for c in act) + ")")
    L.append("- Budget Pacer: use each month's total as that month's committed media spend and the daily rows as campaign "
             "bounds. December above covers only the days in this window; add the rest of December yourself.")
    L.append("- Meta Budget Scaling: use the Meta column as the daily budget path and the "
             f"{inc} daily increase limit as its scaling guardrail.")
    L.append("")
    L.append("_Estimates from last year's curve, an elasticity per phase (fitted or assumed) and a demand index; not a forecast guarantee. "
             "There is no organic baseline: every projected sale is tied to ad spend, so the model projects $0 at $0 "
             "spend. Read-only: no ad account or budget was changed._")
    return "\n".join(L) + "\n"


CSV_COLUMNS = (["date", "phase", "tag", "total_budget"] + [f"{c}_budget" for c in CHANNELS]
               + ["proj_net_sales_low", "proj_net_sales_base", "proj_net_sales_high", "proj_mer_base",
                  "proj_contribution_base", "ly_date", "ly_net_sales", "ly_ad_spend",
                  "breakeven_mer", "target_mer", "pull_back_mer", "scale_mer", "check_times"])


def write_csv(a: dict, path: str) -> None:
    def m2(x: float | None) -> str:                      # same 2-decimal values as the report's guardrail table
        return "" if x is None or math.isinf(x) else f"{x:.2f}"

    with open(path, "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(CSV_COLUMNS)
        for r in a["rows"]:
            wr.writerow([r["date"], r["phase"], r["tag"], r["total"]] + [r["channels"][c] for c in CHANNELS]
                        + [f"{r['sales']['low']:.2f}", f"{r['sales']['base']:.2f}", f"{r['sales']['high']:.2f}",
                           f"{r['mer']:.4f}" if r["mer"] else "", f"{r['contribution']:.2f}",
                           r["ly_date"], f"{r['ly_sales']:.2f}", f"{r['ly_spend']:.2f}",
                           m2(r["breakeven_mer"]), m2(r["target_mer"]), m2(r["pull_back_mer"]), m2(r["scale_mer"]),
                           ";".join(r["check_times"])])


def jsonable(x):
    if isinstance(x, float):
        return None if math.isinf(x) or math.isnan(x) else x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, dt.date):
        return x.isoformat()
    return x


def refusal_text(keys: str) -> str:
    return ("## BFCM Budget Plan: refused\n\n"
            f"economics.json asks to plan from platform ROAS ({keys}). This planner will not do that. Meta, Google and "
            "TikTok each claim credit for the same sale, so platform ROAS adds up to more revenue than the store made.\n\n"
            "Replace the target with one of:\n"
            "- `{\"type\": \"margin_floor\", \"min_contribution_pct\": 12}` (recommended), or\n"
            "- `{\"type\": \"mer\", \"mer\": 4.0}` where MER = store net sales ÷ total ad spend.\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--history", required=True, help="daily history CSV (see DATA_CONTRACT.md)")
    ap.add_argument("--economics", required=True, help="unit economics and targets JSON (see DATA_CONTRACT.md)")
    ap.add_argument("--csv", help="also write the daily plan to this CSV path")
    ap.add_argument("--json", help="also write the structured plan to this JSON path")
    ap.add_argument("--as-of", help="today's date, YYYY-MM-DD (default: the system date); used only to warn when the "
                                    "history is stale")
    ap.add_argument("--starting-daily-budget", help="daily budget the ramp starts from (default: the last 14 days' "
                                                    "average spend, or the last 14 days with spend if ads were paused)")
    ap.add_argument("--accept-demand-index", action="store_true",
                    help="plan even when the estimated demand index is outside 0.25-4.0")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):              # the report uses non-ASCII signs; never crash on cp1252
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        today = parse_date(args.as_of, "--as-of") if args.as_of else dt.date.today()
        start_budget = None
        if args.starting_daily_budget is not None:
            try:
                start_budget = strict_number(args.starting_daily_budget, where="--starting-daily-budget")
            except NumberFormatError as ex:
                raise InputError(str(ex))
            if start_budget <= 0:
                raise InputError("--starting-daily-budget must be more than 0")
        e = load_economics(args.economics)
        hist, ignored = load_history(args.history)
        a = plan(hist, e, ignored, today=today, start_budget=start_budget, accept_index=args.accept_demand_index)
    except Refusal as r:
        sys.stdout.write(refusal_text(str(r)))
        print(f"refused: target uses platform ROAS ({r}); use a margin floor or MER", file=sys.stderr)
        return 3
    except InputError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    sys.stdout.write(render(a))
    if args.csv:
        write_csv(a, args.csv)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(jsonable(a), f, indent=2, sort_keys=True, allow_nan=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
