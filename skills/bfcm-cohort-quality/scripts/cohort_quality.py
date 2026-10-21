#!/usr/bin/env python3
"""
BFCM Cohort Quality: analyzer.

Answers "were the customers we bought during Black Friday / Cyber Monday
worth it?" from a local Shopify order export. New customers (first-ever order
inside the BFCM window) are compared with the customers first acquired in the
October before and the January-February after, at equal ages: repeat rate and
contribution margin per customer at 30/60/90/180/365 days (a horizon is shown
only once every customer in the cohort has reached it), first-order discount
depth, refunds, discount dependence, days to second order and payback against
blended new-customer cost. Inside the BFCM cohort it cuts by first-order
discount depth, acquisition source and entry product, then recommends this
year's discount depth, entry products, post-purchase timing and how to carry
the cohort into BFCM budget math.

Local files only. Never calls an API, never changes anything.

    python3 scripts/cohort_quality.py --orders orders.csv --economics economics.json \
        [--spend spend.csv] [--year 2025] [--as-of YYYY-MM-DD] \
        [--bfcm-window START:END] [--compare "LABEL=START:END" ...] \
        [--horizons 30,60,90,180,365] [--min-cell 30] [--timezone America/New_York] \
        [--history-since YYYY-MM-DD] [--account NAME] [--json analysis.json]

Python 3.8+, standard library only. Output is deterministic for a given input.
Exit codes: 0 report written, 2 invalid input (with the reason).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
from urllib.parse import parse_qs, urlsplit

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

DEFAULT_HORIZONS = (30, 60, 90, 180, 365)
DEFAULT_MIN_CELL = 30          # cuts with fewer customers show counts only, no rates
HISTORY_LOOKBACK_DAYS = 365    # warn when order history starts less than this before a cohort
RETURNS_SETTLE_DAYS = 60       # first orders younger than this may still be refunded
MIN_RETURNING_ORDERS = 50      # returning-customer BFCM orders needed before recommendation 2 cites them
MAX_CUT_ROWS = 12              # most rows printed per cut (largest first)
Z95 = 1.959963984540054        # two-sided 95% normal quantile, for Wilson ranges

BAND_LABELS = ("0%", "1–15%", "16–25%", "26–35%", ">35%")
BAND_TOP = (0, 15, 25, 35, 100)   # highest whole-percent depth in each band

# utm_source spellings that mean the same channel (same table as BFCM Tracking & Attribution Preflight).
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
NO_UTM = "No UTM (direct/unknown)"

# Canonical column -> accepted header names (headers are lower-cased; spaces and hyphens become "_").
# The aliases let a raw Shopify admin export ("Name", "Email", "Created at", "Subtotal", "Discount Code",
# "Lineitem name", "Cancelled at") and a Cohort LTV Analyzer export (order_date, net_revenue,
# discount, refund_amount) load without renaming.
COLUMN_ALIASES = (
    ("order_id", ("order_id", "name", "order_name", "order_number")),
    ("customer_id", ("customer_id", "customer", "email", "customer_email")),
    ("created_at", ("created_at", "order_date", "processed_at", "date")),
    ("net_sales", ("net_sales", "net_revenue", "subtotal", "subtotal_price")),
    ("discount_amount", ("discount_amount", "discount", "discounts", "total_discounts")),
    ("discount_codes", ("discount_codes", "discount_code")),
    ("refunded_amount", ("refunded_amount", "refund_amount", "total_refunded")),
    ("cogs", ("cogs",)),
    ("utm_source", ("utm_source",)),
    ("landing_site", ("landing_site", "landing_page", "landing_url")),
    ("products", ("products", "product_handles", "handles", "lineitem_name")),
    ("cancelled_at", ("cancelled_at",)),
)
REQUIRED_COLUMNS = ("order_id", "customer_id", "created_at", "net_sales", "discount_amount")

# Time zones with daylight-saving rules (US: 2nd Sunday of March to 1st Sunday of November, 2 am local;
# EU: last Sunday of March to last Sunday of October, 01:00 UTC). Standard offsets in minutes.
US_DST_ZONES = {"america/new_york": -300, "america/detroit": -300, "america/toronto": -300, "us/eastern": -300,
                "america/chicago": -360, "america/winnipeg": -360, "us/central": -360,
                "america/denver": -420, "america/edmonton": -420, "us/mountain": -420,
                "america/los_angeles": -480, "america/vancouver": -480, "us/pacific": -480,
                "america/anchorage": -540, "us/alaska": -540}
EU_DST_ZONES = {"europe/london": 0, "europe/dublin": 0, "europe/lisbon": 0,
                "europe/paris": 60, "europe/berlin": 60, "europe/amsterdam": 60, "europe/brussels": 60,
                "europe/madrid": 60, "europe/rome": 60, "europe/vienna": 60, "europe/stockholm": 60,
                "europe/copenhagen": 60, "europe/oslo": 60, "europe/zurich": 60, "europe/prague": 60,
                "europe/warsaw": 60, "europe/helsinki": 120, "europe/athens": 120}
FIXED_ZONES = {"utc": 0, "etc/utc": 0, "gmt": 0, "america/phoenix": -420, "pacific/honolulu": -600}


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- formatting
def fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_date(d: dt.date) -> str:
    return f"{fmt_day(d)}, {d.year}"


def fmt_range(a: dt.date, b: dt.date) -> str:
    if a == b:
        return fmt_date(a)
    if a.year != b.year:
        return f"{fmt_date(a)}–{fmt_date(b)}"
    if a.month == b.month:
        return f"{fmt_day(a)}–{b.day}, {a.year}"
    return f"{fmt_day(a)}–{fmt_day(b)}, {a.year}"


# Currency code (economics.json "currency") -> symbol printed before amounts. Unknown codes print as "SEK 12.00".
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "INR": "₹", "KRW": "₩",
                    "AUD": "A$", "CAD": "CA$", "NZD": "NZ$", "HKD": "HK$", "SGD": "S$", "MXN": "MX$",
                    "BRL": "R$", "CHF": "CHF ", "ZAR": "R "}
_SYMBOL = ["$"]   # set once per run by set_currency()


def set_currency(code: str) -> None:
    c = (code or "USD").strip().upper()
    _SYMBOL[0] = CURRENCY_SYMBOLS.get(c, c + " ")


def money0(x: float) -> str:
    """Whole units: $44,400."""
    s = f"{_SYMBOL[0]}{abs(x):,.0f}"
    return "−" + s if x < 0 and round(x) != 0 else s


def money2(x: float) -> str:
    """Units and cents: $31.42."""
    s = f"{_SYMBOL[0]}{abs(x):,.2f}"
    return "−" + s if x < 0 and round(x, 2) != 0 else s


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def signed_pct(x: float) -> str:
    s = f"{x * 100:+.0f}%"
    return s.replace("-", "−")


def plural(n: int, word: str) -> str:
    return f"{n:,} {word if n == 1 else word + 's'}"


def join_and(items: list) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def wilson(x: int, n: int) -> tuple:
    if n <= 0:
        return (None, None)
    p = x / n
    z2 = Z95 * Z95
    d = 1 + z2 / n
    c = (p + z2 / (2 * n)) / d
    h = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def differs(x1: int, n1: int, x2: int, n2: int) -> int:
    """+1 / -1 when rate 1 is clearly above / below rate 2 (their 95% Wilson ranges don't overlap), else 0."""
    lo1, hi1 = wilson(x1, n1)
    lo2, hi2 = wilson(x2, n2)
    if lo1 is None or lo2 is None:
        return 0
    return 1 if lo1 > hi2 else -1 if hi1 < lo2 else 0


def mean_ci(vals: list) -> tuple:
    """Mean and normal-approximation 95% interval for a mean (None bounds with fewer than 2 values)."""
    n = len(vals)
    if n == 0:
        return (None, None, None)
    m = sum(vals) / n
    if n < 2:
        return (m, None, None)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    h = Z95 * sd / math.sqrt(n)
    return (m, m - h, m + h)


def mean_differs(a: tuple, b: tuple) -> int:
    """+1 / -1 when mean a is clearly above / below mean b (their 95% intervals don't overlap), else 0."""
    if a[1] is None or b[1] is None:
        return 0
    return 1 if a[1] > b[2] else -1 if a[2] < b[1] else 0


def range_txt(lo: float | None, hi: float | None) -> str:
    if lo is None:
        return ""
    return f"{lo * 100:.0f}–{hi * 100:.0f}%"


def nearest_rank(sorted_vals: list, p: float):
    """Nearest-rank percentile of an ascending list (p in 0..1)."""
    if not sorted_vals:
        return None
    k = max(1, int(math.ceil(p * len(sorted_vals))))
    return sorted_vals[k - 1]


# --------------------------------------------------------------------------- dates and time zones
def nth_weekday(y: int, m: int, weekday: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def last_weekday(y: int, m: int, weekday: int) -> dt.date:
    d = (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def thanksgiving(y: int) -> dt.date:
    return nth_weekday(y, 11, 3, 4)


def default_bfcm_window(y: int) -> tuple:
    """Monday of Thanksgiving week through the Tuesday after Cyber Monday."""
    t = thanksgiving(y)
    return t - dt.timedelta(days=3), t + dt.timedelta(days=5)


class Zone:
    def __init__(self, name: str, std: int, rule: str | None):
        self.name, self.std, self.rule = name, std, rule

    def offset(self, utc: dt.datetime) -> int:
        y = utc.year
        if self.rule == "us":
            start = dt.datetime.combine(nth_weekday(y, 3, 6, 2), dt.time(2)) - dt.timedelta(minutes=self.std)
            end = dt.datetime.combine(nth_weekday(y, 11, 6, 1), dt.time(2)) - dt.timedelta(minutes=self.std + 60)
            return self.std + 60 if start <= utc < end else self.std
        if self.rule == "eu":
            start = dt.datetime.combine(last_weekday(y, 3, 6), dt.time(1))
            end = dt.datetime.combine(last_weekday(y, 10, 6), dt.time(1))
            return self.std + 60 if start <= utc < end else self.std
        return self.std


def parse_zone(s: str) -> Zone:
    key = s.strip().lower()
    if key in US_DST_ZONES:
        return Zone(s.strip(), US_DST_ZONES[key], "us")
    if key in EU_DST_ZONES:
        return Zone(s.strip(), EU_DST_ZONES[key], "eu")
    if key in FIXED_ZONES:
        return Zone(s.strip(), FIXED_ZONES[key], None)
    m = re.match(r"^(?:utc|gmt)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", key)
    if m and int(m.group(2)) <= 14 and int(m.group(3) or 0) < 60:
        mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
        return Zone(s.strip(), mins if m.group(1) == "+" else -mins, None)
    raise InputError(f"--timezone {s!r} is not supported: use a fixed offset like -05:00, or one of "
                     "America/New_York, America/Chicago, America/Denver, America/Phoenix, America/Los_Angeles, "
                     "Europe/London, Europe/Paris, Europe/Berlin, UTC (see DATA_CONTRACT.md)")


TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|z|[+-]\d{2}:?\d{2})?)?$")


def offset_minutes(s: str) -> int:
    if s in ("Z", "z"):
        return 0
    sign = 1 if s[0] == "+" else -1
    digits = s[1:].replace(":", "")
    return sign * (int(digits[:2]) * 60 + int(digits[2:]))


def parse_timestamp(s: str, zone: Zone | None) -> tuple | None:
    """Returns (store-local date, seconds into that day, written-in-UTC-and-not-converted) or None."""
    m = TS_RE.match(s.strip())
    if not m:
        return None
    try:
        day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if m.group(4) is None:
        return day, 0, False
    hh, mi, ss = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
    if hh > 23 or mi > 59 or ss > 60:
        return None
    local = dt.datetime.combine(day, dt.time(hh, mi, min(ss, 59)))
    off = m.group(7)
    if off is None or zone is None:
        utc_unconverted = off is not None and offset_minutes(off) == 0 and zone is None
        return local.date(), hh * 3600 + mi * 60 + ss, utc_unconverted
    utc = local - dt.timedelta(minutes=offset_minutes(off))
    loc = utc + dt.timedelta(minutes=zone.offset(utc))
    return loc.date(), loc.hour * 3600 + loc.minute * 60 + loc.second, False


def parse_iso_date(s: str, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s.strip())
    except ValueError:
        raise InputError(f"{what} must be a date YYYY-MM-DD (got {s!r})")


def parse_window(s: str, what: str) -> tuple:
    parts = s.split("..") if ".." in s else s.split(":")
    if len(parts) != 2:
        raise InputError(f"{what} must look like YYYY-MM-DD:YYYY-MM-DD (got {s!r})")
    a, b = parse_iso_date(parts[0], what), parse_iso_date(parts[1], what)
    if b < a:
        raise InputError(f"{what} ends before it starts ({s!r})")
    return a, b


# --------------------------------------------------------------------------- loading
def norm_header(h: str) -> str:
    return re.sub(r"[\s\-]+", "_", (h or "").strip().lower())


def read_rows(path: str, label: str) -> tuple:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.reader(f)
            rows = []
            for row in rd:
                rows.append((rd.line_num, row))
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e.strerror or e}")
    except (csv.Error, UnicodeDecodeError) as e:
        raise InputError(f"{label} {path} is not a valid UTF-8 CSV: {e}")
    rows = [(ln, r) for ln, r in rows if any(c.strip() for c in r)]
    if not rows:
        raise InputError(f"{label} {path} is empty")
    return [norm_header(h) for h in rows[0][1]], rows[1:]


# Columns where a negative written as -50.00 or (50.00) is valid: a refund exported with an accounting sign.
# Its size is what matters, so it is read as a positive refund. Everything else must be >= 0.
NEGATIVE_OK = ("refunded_amount",)


def parse_money(raw: str, path: str, line: int, col: str, blank: float | None) -> float | None:
    t = raw.strip()
    if t == "":
        if blank is None and col in ("net_sales",):
            raise InputError(f"{path} line {line}: {col} is blank")
        return blank
    try:
        v = strict_number(t, allow_negative=True, where=f"{path} line {line}, column '{col}'")
    except NumberFormatError as e:
        raise InputError(str(e))
    if v < 0:
        if col in NEGATIVE_OK:
            return -v
        raise InputError(f"{path} line {line}: {col} is negative ({t})")
    return v + 0.0


def check_not_european(rows: list, cols: list, path: str) -> None:
    """Fail with a specific message when a numeric column is clearly 1.234,56-formatted."""
    for name, i in cols:
        if looks_european([r[i] for _, r in rows if i < len(r)]):
            raise InputError(f"{path} column '{name}' looks European-formatted (1.234,56): more than half of its "
                             f"values use ',' as the decimal separator; {_DECIMAL_HINT}")


def split_products(s: str) -> list:
    return [p.strip() for p in re.split(r"[;|]", s or "") if p.strip()]


def split_codes(s: str) -> list:
    return [c.strip().upper() for c in re.split(r"[,;|]", s or "") if c.strip()]


def utm_from_landing(url: str) -> str:
    if not url:
        return ""
    try:
        q = urlsplit(url if ("://" in url or url.startswith("/")) else "/" + url).query
        vals = parse_qs(q).get("utm_source")
    except ValueError:
        return ""
    return vals[0].strip() if vals else ""


class Order:
    __slots__ = ("oid", "cust", "day", "secs", "net", "disc", "codes", "refund", "cogs", "src", "products",
                 "line", "kept", "cm")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def load_orders(path: str, zone: Zone | None) -> tuple:
    header, rows = read_rows(path, "orders file")
    cols = {}
    for canon, names in COLUMN_ALIASES:
        for n in names:
            if n in header:
                cols[canon] = header.index(n)
                break
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        raise InputError(f"orders file {path} is missing required column(s): {', '.join(missing)} "
                         "(see DATA_CONTRACT.md)")
    info = {"rows": len(rows), "item_rows": 0, "cancelled": 0, "no_customer": 0, "utc_unconverted": 0,
            "refund_capped": 0, "cogs_given": 0, "from_landing": 0,
            "has_refunds": "refunded_amount" in cols, "has_products": "products" in cols,
            "has_cogs": "cogs" in cols, "has_source": "utm_source" in cols or "landing_site" in cols,
            "columns": sorted(cols)}
    check_not_european(rows, [(header[cols[c]], cols[c]) for c in ("net_sales", "discount_amount", "refunded_amount", "cogs")
                              if c in cols], path)
    orders, seen = [], {}
    for ln, row in rows:
        def get(c: str) -> str:
            i = cols.get(c)
            return row[i].strip() if i is not None and i < len(row) else ""

        oid = get("order_id")
        if not oid:
            raise InputError(f"{path} line {ln}: order_id is blank")
        created = get("created_at")
        if oid in seen and not get("net_sales"):
            # Shopify admin export: one row per line item; only the first row carries the order fields.
            info["item_rows"] += 1
            if seen[oid][1] is not None:
                seen[oid][1].products.extend(p for p in split_products(get("products"))
                                             if p not in seen[oid][1].products)
            continue
        if oid in seen:
            raise InputError(f"{path} line {ln}: duplicate order_id {oid!r} (first on line {seen[oid][0]})")
        if not created:
            raise InputError(f"{path} line {ln}: created_at is blank")
        ts = parse_timestamp(created, zone)
        if ts is None:
            raise InputError(f"{path} line {ln}: created_at {created!r} is not a date "
                             "(use YYYY-MM-DD or an ISO 8601 timestamp)")
        net = parse_money(get("net_sales"), path, ln, "net_sales", None)
        disc = parse_money(get("discount_amount"), path, ln, "discount_amount", 0.0)
        refund = parse_money(get("refunded_amount"), path, ln, "refunded_amount", 0.0)
        cogs = parse_money(get("cogs"), path, ln, "cogs", None) if "cogs" in cols else None
        if get("cancelled_at"):
            info["cancelled"] += 1
            seen[oid] = (ln, None)
            continue
        cust = get("customer_id")
        if not cust:
            info["no_customer"] += 1
            seen[oid] = (ln, None)
            continue
        if ts[2]:
            info["utc_unconverted"] += 1
        if refund > net + 0.005:
            info["refund_capped"] += 1
        if cogs is not None:
            info["cogs_given"] += 1
        src = get("utm_source")
        if not src and get("landing_site"):
            src = utm_from_landing(get("landing_site"))
            if src:
                info["from_landing"] += 1
        prods = []
        for p in split_products(get("products")):
            if p not in prods:
                prods.append(p)
        o = Order(oid=oid, cust=cust, day=ts[0], secs=ts[1], net=net, disc=disc, codes=split_codes(get("discount_codes")),
                  refund=min(refund, net), cogs=cogs, src=src, products=prods, line=ln, kept=0.0, cm=0.0)
        orders.append(o)
        seen[oid] = (ln, o)
    if not orders:
        raise InputError(f"orders file {path} has no usable orders (all cancelled or without a customer_id)")
    return orders, info


def load_economics(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            e = json.load(f)
    except OSError as ex:
        raise InputError(f"cannot read economics file {path}: {ex.strerror or ex}")
    except json.JSONDecodeError as ex:
        raise InputError(f"economics file {path} is not valid JSON: {ex}")
    if not isinstance(e, dict):
        raise InputError("economics.json must be a JSON object")

    def num(key: str, lo: float, hi: float | None, default=None) -> float | None:
        if key not in e or e[key] is None:
            if default is not None:
                return default
            raise InputError(f"economics.json is missing required key '{key}'")
        v = e[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo or (hi is not None and v > hi):
            rng = f"from {lo:g} to {hi:g}" if hi is not None else f"of at least {lo:g}"
            raise InputError(f"economics.json: '{key}' must be a number {rng} (got {v!r})")
        return float(v)

    # *_pct keys are percents (64 = 64%). A value between 0 and 1 is almost always a fraction typed by mistake.
    # For margin and planned discount it can't be right, so stop; fees and returns can truly be under 1%, so warn.
    pct_warnings = []
    for key in ("gross_margin_pct", "discount_pct", "payment_fee_pct", "return_rate_pct"):
        v = e.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1:
            hint = f"'{key}' is {v:g}, which means {v:g}%, not {v * 100:g}% (did you mean {v * 100:g}?)"
            if key in ("gross_margin_pct", "discount_pct"):
                raise InputError(f"economics.json: {hint}. *_pct keys are percents from 0 to 100.")
            pct_warnings.append(hint)
    out = {
        "brand": str(e.get("brand") or ""),
        "pct_warnings": pct_warnings,
        "currency": str(e.get("currency") or "USD"),
        "gm": num("gross_margin_pct", 0, 100) / 100,
        "ship": num("shipping_fulfillment_per_order", 0, None),
        "fee_pct": num("payment_fee_pct", 0, 100) / 100,
        "fee_fixed": num("payment_fee_fixed", 0, None, 0.0),
        "returns": num("return_rate_pct", 0, 100) / 100,
        "planned_discount": None,
    }
    if e.get("discount_pct") is not None:
        out["planned_discount"] = num("discount_pct", 0, 99)
    return out


def load_spend(path: str) -> tuple:
    header, rows = read_rows(path, "spend file")
    if "date" not in header:
        raise InputError(f"spend file {path} is missing the 'date' column")
    spend_cols = [h for h in header if h.endswith("_spend")]
    if not spend_cols:
        raise InputError(f"spend file {path} has no spend columns (name them like meta_spend, google_spend)")
    if "total_spend" in spend_cols and len(spend_cols) > 1:
        raise InputError(f"spend file {path} has total_spend and per-channel columns; keep one so spend "
                         "isn't counted twice")
    di = header.index("date")
    idx = [(c, header.index(c)) for c in spend_cols]
    check_not_european(rows, idx, path)
    days = {}
    for ln, row in rows:
        raw = row[di].strip() if di < len(row) else ""
        try:
            d = dt.date.fromisoformat(raw[:10])
        except ValueError:
            raise InputError(f"{path} line {ln}: date must be YYYY-MM-DD, got {raw!r}")
        if d in days:
            raise InputError(f"{path} line {ln}: duplicate date {d.isoformat()}")
        total = 0.0
        for c, i in idx:
            total += parse_money(row[i] if i < len(row) else "", path, ln, c, 0.0)
        days[d] = total
    return days, spend_cols


# --------------------------------------------------------------------------- analysis
def depth_pct(net: float, disc: float) -> int:
    """Discount as a whole percent of the pre-discount price, half up (32.5 -> 33)."""
    if disc <= 0:
        return 0
    gross = net + disc
    return int(math.floor(100.0 * disc / gross + 0.5 + 1e-9))


def band_of_pct(p: float) -> int:
    for i, top in enumerate(BAND_TOP):
        if p <= top:
            return i
    return len(BAND_TOP) - 1


def band_of(net: float, disc: float) -> int:
    if disc <= 0:
        return 0
    return max(1, band_of_pct(depth_pct(net, disc)))


def source_key(raw: str) -> str:
    low = (raw or "").strip().lower()
    if not low:
        return ""
    return SOURCE_ALIASES.get(low, low)


def source_label(key: str) -> str:
    if not key:
        return NO_UTM
    return SOURCE_LABELS.get(key, key)


def price_orders(orders: list, econ: dict, have_refunds: bool) -> None:
    gm, ship, fp, ff, rr = econ["gm"], econ["ship"], econ["fee_pct"], econ["fee_fixed"], econ["returns"]
    for o in orders:
        if have_refunds:
            refund = o.refund
            keep = (o.net - refund) / o.net if o.net > 0 else 1.0
        else:
            refund = o.net * rr
            keep = 1.0 - rr
        cogs = o.cogs if o.cogs is not None else (1.0 - gm) * (o.net + o.disc)
        fees = fp * o.net + (ff if o.net > 0 else 0.0)
        o.kept = o.net - refund
        o.cm = o.kept - cogs * keep - fees - ship


class Cust:
    __slots__ = ("orders", "first", "fday", "ks", "repeat_day", "second", "src", "band", "depth", "same_day")

    def __init__(self, orders: list):
        self.orders = orders
        self.first = orders[0]
        self.fday = self.first.day
        self.ks = [(o.day - self.fday).days for o in orders]
        later = [i for i, k in enumerate(self.ks) if k >= 1]
        self.repeat_day = self.ks[later[0]] if later else None
        self.second = orders[later[0]] if later else None
        self.src = source_key(self.first.src)
        self.band = band_of(self.first.net, self.first.disc)
        self.depth = depth_pct(self.first.net, self.first.disc)
        self.same_day = sum(1 for k in self.ks[1:] if k == 0)

    def cm_by(self, h: int) -> float:
        return sum(o.cm for o, k in zip(self.orders, self.ks) if k <= h)

    def net_by(self, h: int) -> float:
        return sum(o.kept for o, k in zip(self.orders, self.ks) if k <= h)

    def later(self, h: int) -> list:
        return [o for o, k in zip(self.orders, self.ks) if 1 <= k <= h]


def cohort_stats(label: str, kind: str, start: dt.date, end: dt.date, members: list, as_of: dt.date,
                 horizons: tuple, spend: dict | None, have_refunds: bool) -> dict:
    n = len(members)
    complete = as_of >= end
    c = {"label": label, "kind": kind, "start": start, "end": end, "status": "complete" if complete else "acquiring",
         "new_customers": n, "members": members}
    days_open = (min(end, as_of) - start).days + 1
    c["days"] = days_open
    c["per_day"] = n / days_open if days_open > 0 else 0.0
    if n == 0:
        return c
    firsts = [m.first for m in members]
    net = sum(f.net for f in firsts)
    gross = sum(f.net + f.disc for f in firsts)
    c["first_order_aov"] = net / n
    c["first_order_depth"] = (gross - net) / gross if gross > 0 else 0.0
    c["first_orders_discounted"] = sum(1 for f in firsts if f.disc > 0)
    c["first_order_refund_rate"] = (sum(f.refund for f in firsts) / net if net > 0 else 0.0) if have_refunds else None
    c["first_orders_refunded"] = sum(1 for f in firsts if f.refund > 0) if have_refunds else None
    c["bands"] = [sum(1 for m in members if m.band == i) for i in range(len(BAND_LABELS))]
    c["same_day_customers"] = sum(1 for m in members if m.same_day)
    # Blended new-customer cost: every ad dollar in the window / new customers in the window.
    c["spend"], c["cost"], c["cost_note"] = None, None, None
    if spend is None:
        c["cost_note"] = "no --spend file"
    else:
        days = [start + dt.timedelta(days=i) for i in range(days_open)]
        missing = [d for d in days if d not in spend]
        if missing:
            c["cost_note"] = f"spend file is missing {len(missing)} of {len(days)} days"
        elif sum(spend[d] for d in days) <= 0:
            c["cost_note"] = f"spend file has 0 spend on all {len(days)} days, treated as missing"
        else:
            c["spend"] = sum(spend[d] for d in days)
            c["cost"] = c["spend"] / n
    # Horizons: a horizon is mature only when the cohort's last day has reached it.
    hz = {}
    for h in horizons:
        mature = complete and (as_of - end).days >= h
        e = {"mature": mature, "matures_on": end + dt.timedelta(days=h)}
        if mature:
            rep = sum(1 for m in members if m.repeat_day is not None and m.repeat_day <= h)
            e.update(repeaters=rep, repeat_rate=rep / n,
                     cm_per_customer=sum(m.cm_by(h) for m in members) / n,
                     net_sales_per_customer=sum(m.net_by(h) for m in members) / n)
        hz[h] = e
    c["horizons"] = hz
    # Payback curve over every day all members have reached.
    c["max_day"] = (as_of - end).days if complete else None
    c["payback_day"], c["cm_at_max_day"] = None, None
    if complete:
        md = c["max_day"]
        inc = [0.0] * (md + 1)
        for m in members:
            for o, k in zip(m.orders, m.ks):
                if k <= md:
                    inc[k] += o.cm
        run = 0.0
        for d in range(md + 1):
            run += inc[d]
            if c["cost"] is not None and c["payback_day"] is None and run / n >= c["cost"] - 1e-9:
                c["payback_day"] = d
        c["cm_at_max_day"] = run / n
        c["cm_day0"] = inc[0] / n
    return c


def cohort_at(c: dict, h: int, bfcm: tuple) -> None:
    """Statistics at the comparison horizon h (every member has reached it)."""
    mem = c["members"]
    gaps = sorted(m.repeat_day for m in mem if m.repeat_day is not None and m.repeat_day <= h)
    c["at"] = {"horizon": h, "repeaters": len(gaps),
               "p25": nearest_rank(gaps, 0.25), "median": nearest_rank(gaps, 0.5), "p75": nearest_rank(gaps, 0.75)}
    later = [o for m in mem for o in m.later(h)]
    c["at"]["later_orders"] = len(later)
    c["at"]["later_discounted"] = sum(1 for o in later if o.disc > 0)
    in_bfcm = [o for o in later if bfcm[0] <= o.day <= bfcm[1]]
    c["at"]["later_in_bfcm"] = len(in_bfcm)
    c["at"]["later_in_bfcm_discounted"] = sum(1 for o in in_bfcm if o.disc > 0)
    christmas = dt.date(c["start"].year, 12, 25)
    c["at"]["second_before_christmas"] = sum(
        1 for m in mem if m.repeat_day is not None and m.repeat_day <= h and m.second.day < christmas)
    c["at"]["cm_after_first_day"] = (sum(m.cm_by(h) for m in mem) - sum(m.cm_by(0) for m in mem)) / len(mem)


def cut_rows(members: list, keys_of, h: int | None, cohort_rate: float | None, ref_top: int,
             with_next: bool) -> list:
    groups = {}
    for m in members:
        for key in keys_of(m):
            groups.setdefault(key, []).append(m)
    total = len(members)
    rows = []
    for key, mem in groups.items():
        n = len(mem)
        r = {"key": key, "customers": n, "share": n / total if total else 0.0,
             "first_order_aov": sum(m.first.net for m in mem) / n,
             "deep_share": sum(1 for m in mem if m.depth > ref_top) / n}
        if h is not None:
            rep = [m for m in mem if m.repeat_day is not None and m.repeat_day <= h]
            lo, hi = wilson(len(rep), n)
            later = [o for m in mem for o in m.later(h)]
            # First-order CM (day 0) and CM after the first order (repeat orders, day 1..h) kept apart, so the
            # discount's own effect on the first order is visible and isn't read as customer quality.
            after = mean_ci([m.cm_by(h) - m.cm_by(0) for m in mem])
            r.update(repeaters=len(rep), repeat_rate=len(rep) / n, lo=lo, hi=hi,
                     cm_per_customer=sum(m.cm_by(h) for m in mem) / n,
                     first_cm=sum(m.cm_by(0) for m in mem) / n,
                     later_cm=after[0], later_cm_lo=after[1], later_cm_hi=after[2],
                     later_orders=len(later), later_discounted=sum(1 for o in later if o.disc > 0))
            r["vs_cohort"] = ("higher" if cohort_rate is not None and lo > cohort_rate else
                              "lower" if cohort_rate is not None and hi < cohort_rate else "unclear")
            if with_next:
                counts = {}
                for m in rep:
                    for p in m.second.products:
                        counts[p] = counts.get(p, 0) + 1
                if counts:
                    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                    r["next_product"], r["next_count"] = best[0], best[1]
        rows.append(r)
    return rows


def analyze(orders: list, info: dict, econ: dict, spend: tuple | None, cfg: dict) -> dict:
    as_of, horizons, min_cell = cfg["as_of"], cfg["horizons"], cfg["min_cell"]
    have_refunds = info["has_refunds"]
    price_orders(orders, econ, have_refunds)
    by = {}
    after = 0
    for o in orders:
        if o.day > as_of:
            after += 1
            continue
        by.setdefault(o.cust, []).append(o)
    for lst in by.values():
        lst.sort(key=lambda o: (o.day, o.secs, o.line))
    custs = [Cust(lst) for _, lst in sorted(by.items(), key=lambda kv: (kv[1][0].day, kv[1][0].secs, kv[1][0].line))]
    kept_orders = sum(len(c.orders) for c in custs)
    cogs_used = sum(1 for c in custs for o in c.orders if o.cogs is not None)
    first_day_all = min(c.fday for c in custs)
    last_day_all = max(o.day for c in custs for o in c.orders)

    spend_days = spend[0] if spend else None
    windows = [(cfg["bfcm_label"], "bfcm", cfg["bfcm"][0], cfg["bfcm"][1])] + \
              [(lab, "compare", a, b) for lab, a, b in cfg["compare"]]
    cohorts, skipped = [], []
    in_any = 0
    for lab, kind, a, b in windows:
        if a > as_of:
            skipped.append(f"{lab} ({fmt_range(a, b)}) starts after the as-of date.")
            continue
        mem = [c for c in custs if a <= c.fday <= min(b, as_of)]
        in_any += len(mem)
        if not mem:
            if kind == "bfcm":
                raise InputError(f"no customer's first order falls in the {lab} window ({a.isoformat()} to "
                                 f"{b.isoformat()}); check --year / --bfcm-window and that the export covers it")
            skipped.append(f"{lab} ({fmt_range(a, b)}) has no new customers in the file.")
            continue
        cohorts.append(cohort_stats(lab, kind, a, b, mem, as_of, horizons, spend_days, have_refunds))
    bf = cohorts[0]
    comps = cohorts[1:]

    # Comparison horizon: the longest horizon mature for BFCM and at least one comparison cohort.
    bf_mature = [h for h in horizons if bf["horizons"][h]["mature"]]
    ch = None
    for h in reversed(bf_mature):
        if any(c["status"] == "complete" and c["horizons"][h]["mature"] for c in comps):
            ch = h
            break
    if ch is None and bf_mature:
        ch = bf_mature[-1]
    cut_h = bf_mature[-1] if bf_mature else None
    for c in cohorts:
        if ch is not None and c["status"] == "complete" and c["horizons"][ch]["mature"]:
            cohort_at(c, ch, cfg["bfcm"])
    compared = [c["label"] for c in comps if ch is not None and "at" in c]

    # Cuts inside the BFCM cohort.
    planned = econ["planned_discount"]
    ref_band = band_of_pct(int(math.floor(planned + 0.5))) if planned is not None else \
        max(range(len(BAND_LABELS)), key=lambda i: (bf["bands"][i], -i))
    ref_top = BAND_TOP[ref_band]
    rate_all = bf["horizons"][cut_h]["repeat_rate"] if cut_h is not None else None
    mem = bf["members"]
    disc_rows = cut_rows(mem, lambda m: [m.band], cut_h, rate_all, ref_top, False)
    by_band = {r["key"]: r for r in disc_rows}
    disc_rows = [by_band.get(i) or {"key": i, "customers": 0} for i in range(len(BAND_LABELS))]
    deep_mem = [m for m in mem if m.band > ref_band]
    deep = cut_rows(deep_mem, lambda m: ["deep"], cut_h, rate_all, ref_top, False) if deep_mem else []
    deep = deep[0] if deep else None
    if deep:
        deep["share"] = deep["customers"] / len(mem)
    # Deep vs reference band, judged only on behaviour after the first order: the repeat rate (95% Wilson ranges)
    # or CM from repeat orders per customer (95% interval). Total CM is not used, because it includes the
    # discounted first order and so falls with any deeper discount even when later behaviour is identical.
    deep_vs_ref = None
    ref_row = by_band.get(ref_band)
    if cut_h is not None and deep and ref_row and deep.get("repeat_rate") is not None:
        rep_d = differs(deep["repeaters"], deep["customers"], ref_row["repeaters"], ref_row["customers"])
        lat_d = mean_differs((deep["later_cm"], deep["later_cm_lo"], deep["later_cm_hi"]),
                             (ref_row["later_cm"], ref_row["later_cm_lo"], ref_row["later_cm_hi"]))
        worse, better = -1 in (rep_d, lat_d), 1 in (rep_d, lat_d)
        deep_vs_ref = {"repeat": rep_d, "later_cm": lat_d,
                       "verdict": "weaker" if worse and not better else "stronger" if better and not worse else "unclear"}
    code_counts = {}
    for m in deep_mem:
        combo = "+".join(sorted(m.first.codes)) or "(no code)"
        code_counts[combo] = code_counts.get(combo, 0) + 1
    src_rows = cut_rows(mem, lambda m: [m.src], cut_h, rate_all, ref_top, False)
    spellings = {}
    for m in mem:
        if m.src:
            spellings.setdefault(m.src, {})
            raw = m.first.src.strip()
            spellings[m.src][raw] = spellings[m.src].get(raw, 0) + 1
    for r in src_rows:
        sp = spellings.get(r["key"], {})
        r["spellings"] = sorted(sp.items(), key=lambda kv: (-kv[1], kv[0]))
    src_rows.sort(key=lambda r: (r["key"] == "", -r["customers"], source_label(r["key"]).lower()))
    prod_rows = []
    if info["has_products"]:
        prod_rows = cut_rows(mem, lambda m: m.first.products or ["(no product listed)"], cut_h, rate_all, ref_top, True)
        prod_rows.sort(key=lambda r: (-r["customers"], r["key"]))

    history_start = cfg["history_since"] or first_day_all
    short_history = [c["label"] for c in cohorts if (c["start"] - history_start).days < HISTORY_LOOKBACK_DAYS]

    a = {
        "account": cfg["account"], "currency": econ["currency"], "year": cfg["year"], "as_of": as_of,
        "bfcm_window": cfg["bfcm"], "window_is_default": cfg["bfcm_default"], "horizons": list(horizons),
        "min_cell": min_cell, "compare_horizon": ch, "cut_horizon": cut_h, "cohorts": cohorts,
        "skipped": skipped, "econ": econ, "ref_band": ref_band, "ref_top": ref_top,
        "cuts": {"discount": disc_rows, "deep": deep, "deep_vs_ref": deep_vs_ref, "deep_codes": sorted(code_counts.items(), key=lambda kv: (-kv[1], kv[0])),
                 "source": src_rows, "product": prod_rows},
        "data": {"orders_in_file": len(orders), "orders_used": kept_orders, "customers": len(custs),
                 "customers_in_cohorts": in_any, "orders_after_as_of": after,
                 "first_day": first_day_all, "last_day": last_day_all, "history_start": history_start,
                 "history_given": cfg["history_since"] is not None, "short_history": short_history,
                 "spend_columns": spend[1] if spend else None, "timezone": cfg["timezone"], **info,
                 "cogs_used": cogs_used},
        "peers": compared,
    }
    a["recommendations"] = recommendations(a)
    return a


# --------------------------------------------------------------------------- findings and recommendations
def primary_peer(a: dict) -> dict | None:
    for c in a["cohorts"][1:]:
        if "at" in c:
            return c
    return None


def peers(a: dict) -> list:
    return [c for c in a["cohorts"][1:] if "at" in c]


def ok_row(r: dict | None, a: dict) -> bool:
    return bool(r) and r.get("customers", 0) >= a["min_cell"] and r.get("repeat_rate") is not None


def next_bfcm_year(as_of: dt.date) -> int:
    return as_of.year if default_bfcm_window(as_of.year)[0] > as_of else as_of.year + 1


def recommendations(a: dict) -> list:
    bf = a["cohorts"][0]
    h = a["cut_horizon"]
    ch = a["compare_horizon"]
    p = primary_peer(a)
    econ = a["econ"]
    recs = []
    ref_lab = BAND_LABELS[a["ref_band"]]
    ref = a["cuts"]["discount"][a["ref_band"]]
    deep = a["cuts"]["deep"]
    planned = econ["planned_discount"]
    cap = f"{planned:g}%" if planned is not None else f"{a['ref_top']}%"
    # 1. Discount depth for new customers. Judged on behaviour after the first order only (5.1): the first
    # order's lower CM at a deeper discount is mechanical and is reported separately.
    dv = a["cuts"]["deep_vs_ref"]
    if h is not None and ok_row(deep, a) and ok_row(ref, a) and dv:
        codes = [f"{c} ({n})" for c, n in a["cuts"]["deep_codes"] if c != "(no code)"][:3]
        after = (f"repeated {pct(deep['repeat_rate'])} within {h} days ({range_txt(deep['lo'], deep['hi'])}) vs "
                 f"{pct(ref['repeat_rate'])} ({range_txt(ref['lo'], ref['hi'])}) at {ref_lab}, and added "
                 f"{money2(deep['later_cm'])} vs {money2(ref['later_cm'])} of CM per customer after the first order")
        first = (f"First-order CM was {money2(deep['first_cm'])} vs {money2(ref['first_cm'])}; a deeper discount "
                 "lowers that by itself, so it isn't read as customer quality.")
        if dv["verdict"] == "weaker":
            t = (f"**New customers: test a first-order discount cap at {cap} with a holdout before setting it.** "
                 f"{bf['label']} customers acquired above {a['ref_top']}% off ({plural(deep['customers'], 'customer')}, "
                 f"{pct(deep['share'])} of the cohort) {after}. This may reflect who the discount attracts; confirm "
                 f"with a holdout test before setting a cap. {first}")
            if codes:
                t += f" Codes on those first orders: {join_and(codes)}."
            t += (f" Check that welcome and other codes can't combine with the BFCM {next_bfcm_year(a['as_of'])} "
                  "sale (BFCM Offer & Discount Config QA checks combinations).")
        else:
            t = (f"**New customers: last year's data doesn't show deeper first-order discounts buying weaker customers.** "
                 f"Customers acquired above {a['ref_top']}% off {after}; "
                 + ("the ranges overlap. " if dv["verdict"] == "unclear" else "they did better after the first order. ")
                 + f"{first} Set the depth on first-order margin and keep the planned {cap}; re-check in January.")
        recs.append(t)
    # 2. Existing customers.
    if ch is not None and p is not None and "at" in bf and bf["at"]["later_orders"] and p["at"]["later_orders"]:
        dd_b = bf["at"]["later_discounted"] / bf["at"]["later_orders"]
        dd_p = p["at"]["later_discounted"] / p["at"]["later_orders"]
        t = "**Existing customers: early access, not a deeper code.** "
        nb = p["at"]["later_in_bfcm"]
        if p["end"] < a["bfcm_window"][0] and nb >= MIN_RETURNING_ORDERS:
            # Only with enough returning orders, and stated as a test: every one of them may have used a discount.
            nd = p["at"]["later_in_bfcm_discounted"]
            lo, hi = wilson(nd, nb)
            t += (f"{p['label']}'s returning customers made {nb:,} orders during {bf['label']}; {pct(nd / nb, 0)} "
                  f"used a discount (95% range {range_txt(lo, hi)}). Test whether they'd buy without one. ")
        t += (f"Give them the same depth as new customers with an early-access window, and don't open the "
              f"post-purchase flow with another discount: {pct(dd_b, 0)} of {bf['label']} customers' repeat orders "
              f"already used one, vs {pct(dd_p, 0)} for {p['label']}.")
        recs.append(t)
    # 3. Entry products.
    prods = [r for r in a["cuts"]["product"] if ok_row(r, a) and r["key"] != "(no product listed)"]
    if h is not None and prods:
        best = sorted(prods, key=lambda r: (-r["repeat_rate"], -r["cm_per_customer"], r["key"]))[0]
        worst = sorted(prods, key=lambda r: (r["repeat_rate"], r["cm_per_customer"], r["key"]))[0]
        if best["vs_cohort"] == "higher":
            t = (f"**Prospect with `{best['key']}`.** {bf['label']} customers whose first order included it ordered "
                 f"again {pct(best['repeat_rate'])} of the time within {h} days ({range_txt(best['lo'], best['hi'])}) vs "
                 f"{pct(bf['horizons'][h]['repeat_rate'])} for the whole cohort, and were worth "
                 f"{money2(best['cm_per_customer'])} each.")
            if best.get("next_product"):
                t += (f" {pct(best['next_count'] / best['repeaters'], 0)} of those repeaters' next orders included "
                      f"`{best['next_product']}`: feature `{best['key']}` in BFCM prospecting creative and gift-guide "
                      f"placements, and cross-sell `{best['next_product']}` after it.")
            if worst["key"] != best["key"] and worst["vs_cohort"] == "lower":
                t += (f" `{worst['key']}` first orders came back least ({pct(worst['repeat_rate'])}): fine in the "
                      f"offer, weak as an acquisition hook.")
        else:
            t = (f"**Entry products: no clear winner yet.** `{best['key']}` first orders came back most "
                 f"({pct(best['repeat_rate'])}, range {range_txt(best['lo'], best['hi'])}), but the range overlaps the "
                 f"cohort's {pct(bf['horizons'][h]['repeat_rate'])}. Keep prospecting creative broad and re-check.")
        recs.append(t)
    # 4. Post-purchase timing.
    if ch is not None and "at" in bf and bf["at"]["repeaters"] >= 10:
        at = bf["at"]
        t = (f"**Time the post-purchase flow to day {at['p25']}–{at['median']}.** Of the {bf['label']} customers who "
             f"ordered again within {ch} days ({plural(at['repeaters'], 'customer')}), a quarter came back by day "
             f"{at['p25']}, half by day {at['median']} and three quarters by day {at['p75']}. Send the first cross-sell "
             f"(the entry product's natural next buy, at full price) by day {at['p25']}, a second touch before day "
             f"{at['median']}, and move non-buyers to the regular newsletter after day {at['p75']}.")
        if at["second_before_christmas"]:
            t += (f" {at['second_before_christmas']} of those second orders came before Dec 25: add a gift reminder "
                  f"before your ground-shipping cutoff (Holiday Shipping Cutoff Checker).")
        recs.append(t)
    # 5. Budget math.
    if ch is not None and "at" in bf:
        cmh = bf["horizons"][ch]["cm_per_customer"]
        t = (f"**Budget math: plan BFCM on first-order contribution, not on lifetime value.** By {ch} days a "
             f"{bf['label']} customer had added {money2(bf['at']['cm_after_first_day'])} of contribution after the "
             f"first day")
        if p is not None:
            t += f" ({p['label']}: {money2(p['at']['cm_after_first_day'])})"
        t += (". In the BFCM Holiday Budget Planner, keep the target MER on first-order contribution and don't lower "
              "it for expected repeat purchases")
        if bf.get("first_order_refund_rate") is not None and bf["first_order_refund_rate"] > econ["returns"] + 0.005:
            t += (f"; set return_rate_pct to {bf['first_order_refund_rate'] * 100:.1f} (what {bf['label']} first "
                  f"orders actually refunded) instead of {econ['returns'] * 100:g}")
        t += (f"; and treat {money2(cmh)} (contribution per {bf['label']} customer at {ch} days) as the most a BFCM "
              f"new customer can cost if it has to pay back within {ch} days.")
        if bf["cost"] is not None:
            t += (f" {bf['label']}'s blended cost was {money2(bf['cost'])}. If the plan's blended new-customer cost is above "
                  f"{money2(cmh)}, expect BFCM {next_bfcm_year(a['as_of'])} customers not to pay back within {ch} days at "
                  f"{bf['label']}'s quality.")
        recs.append(t)
    # 6. When to re-run.
    nxt = [x for x in a["horizons"] if not bf["horizons"][x]["mature"]]
    ny = next_bfcm_year(a["as_of"])
    parts = []
    if nxt:
        parts.append(f"on {fmt_date(bf['horizons'][nxt[0]]['matures_on'])}, when {bf['label']}'s {nxt[0]}-day horizon "
                     "matures")
    early_read = ny > a["year"] and a["compare_horizon"] is not None
    if early_read:
        w = default_bfcm_window(ny)
        dates = [f"{x} days on {fmt_date(w[1] + dt.timedelta(days=x))}" for x in a["horizons"][:2]]
        parts.append(f"in January for an early read on BFCM {ny} (first horizons: {join_and(dates)})")
    if parts:
        t = ("**Re-run twice:** " + "; and ".join(parts) + "." if len(parts) == 2 else f"**Re-run {parts[0]}.**")
        if early_read:
            t += (f" For the January read, compare like with like: `--year {ny} --compare \"BFCM {a['year']}="
                  f"{a['bfcm_window'][0].isoformat()}:{a['bfcm_window'][1].isoformat()}\" --compare \"October {ny}="
                  f"{ny}-10-01:{ny}-10-31\"`. Horizons that haven't matured print as not mature.")
        recs.append(t)
    return recs


# --------------------------------------------------------------------------- rendering
def cell_rate(r: dict, a: dict) -> str:
    if r.get("customers", 0) == 0:
        return "—"
    if r["customers"] < a["min_cell"]:
        return f"fewer than {plural(a['min_cell'], 'customer')}"
    if r.get("repeat_rate") is None:
        return "not mature"
    return f"{pct(r['repeat_rate'])} ({range_txt(r['lo'], r['hi'])})"


def cell_small(r: dict, a: dict, text: str) -> str:
    if r.get("customers", 0) == 0:
        return "—"
    if r["customers"] < a["min_cell"] or r.get("repeat_rate") is None:
        return "—"
    return text


def render(a: dict) -> str:
    L = []
    cohorts = a["cohorts"]
    bf = cohorts[0]
    comps = cohorts[1:]
    ch, h = a["compare_horizon"], a["cut_horizon"]
    econ = a["econ"]
    acct = a["account"]
    L.append(f"## BFCM Cohort Quality: {acct}, {bf['label']} (as of {a['as_of'].isoformat()})")
    L.append("")
    w = a["bfcm_window"]
    wdesc = " (the Monday of Thanksgiving week through the Tuesday after Cyber Monday)" if a["window_is_default"] else ""
    cmp_txt = join_and([f"{c['label']} ({fmt_range(c['start'], c['end'])})" for c in comps])
    L.append(f"_{bf['label']} = customers whose first-ever order fell on {fmt_range(w[0], w[1])}{wdesc}. "
             + (f"Compared with the customers first acquired in {cmp_txt}. " if comps else "")
             + "Contribution margin (CM) = net sales kept after refunds − COGS − payment fees − shipping and "
             "fulfillment, before ad spend. A horizon is shown only when every customer in the cohort has reached it. "
             f"Amounts in {a['currency']}._")
    L.append("")

    # Headline.
    if ch is None:
        first = a["horizons"][0]
        if bf["status"] == "acquiring":
            L.append(f"**{bf['label']} is still acquiring customers (its window ends {fmt_date(bf['end'])}). "
                     f"{plural(bf['new_customers'], 'new customer')} so far; the first horizon ({first} days) can be read "
                     f"from {fmt_date(bf['horizons'][first]['matures_on'])}.**")
        else:
            L.append(f"**Too early to judge {bf['label']}: its first horizon ({first} days) matures on "
                     f"{fmt_date(bf['horizons'][first]['matures_on'])}. So far: {plural(bf['new_customers'], 'new customer')}, "
                     f"first-order AOV {money2(bf['first_order_aov'])}, average first-order discount "
                     f"{pct(bf['first_order_depth'])}.**")
    else:
        parts = []
        for c in comps:
            if "at" in c:
                v, b = c["horizons"][ch]["cm_per_customer"], bf["horizons"][ch]["cm_per_customer"]
                parts.append(f"{money2(v)} for {c['label']}" + (f" ({signed_pct((b - v) / abs(v))})" if v else ""))
        L.append(f"**{bf['label']} new customers were worth {money2(bf['horizons'][ch]['cm_per_customer'])} in "
                 f"contribution each at {ch} days" + (", vs " + join_and(parts) if parts else "") + ".**")
        nxt = [x for x in a["horizons"] if not bf["horizons"][x]["mature"]]
        if nxt:
            labs = [bf["label"]] + [c["label"] for c in comps if "at" in c]
            who = (f"every {bf['label']} customer has" if len(labs) == 1 else
                   f"both {labs[0]} and {labs[1]} have fully" if len(labs) == 2 else f"{join_and(labs)} have all fully")
            L.append("")
            L.append(f"{ch} days is the longest horizon {who} reached. {bf['label']}'s {nxt[0]}-day horizon matures on "
                     f"{fmt_date(bf['horizons'][nxt[0]]['matures_on'])}"
                     + (": until then, treat this as an early read." if ch < 90 else "."))
    L.append("")

    # Findings.
    p = primary_peer(a)
    pe = peers(a)
    L.append("**What the data shows** (detected)")
    n_find = 0

    def find(text: str) -> None:
        nonlocal n_find
        n_find += 1
        L.append(f"{n_find}. {text}")

    if bf["cost"] is not None:
        if bf["payback_day"] is not None:
            pb = ("covered it with the first order" if bf["payback_day"] == 0 else
                  f"covered it on day {bf['payback_day']}")
            pb = f"The average {bf['label']} customer {pb}."
        elif bf["status"] == "complete":
            pb = (f"By day {bf['max_day']}, the latest day every {bf['label']} customer has reached, they had returned "
                  f"{money2(bf['cm_at_max_day'])} of CM each: {pct(bf['cm_at_max_day'] / bf['cost'], 0)} of that cost, "
                  f"not paid back.")
        else:
            pb = "Payback can't be read until the window closes."
        others = []
        for c in pe:
            if c["cost"] is None:
                continue
            if c["payback_day"] is not None:
                others.append(f"{c['label']} paid back on day {c['payback_day']}" if c["payback_day"] else
                              f"{c['label']} paid back with the first order")
            else:
                others.append(f"{c['label']} had recovered {pct(c['cm_at_max_day'] / c['cost'], 0)} by day {c['max_day']}")
        lead = "**Acquisition cost and payback.**"
        if p is not None and p["cost"] is not None and ch is not None:
            # A 5% gap is needed before calling one cohort cheaper/costlier or worth less/more.
            rc = bf["cost"] / p["cost"] if p["cost"] else 1.0
            pv = p["horizons"][ch]["cm_per_customer"]
            rv = (bf["horizons"][ch]["cm_per_customer"] - pv) / abs(pv) if pv else 0.0
            cost_w = "Cheaper to acquire" if rc < 0.95 else "Costlier to acquire" if rc > 1.05 else "About as costly to acquire"
            val_w = "worth less" if rv < -0.05 else "worth more" if rv > 0.05 else "about as valuable"
            lead = f"**{cost_w}, {val_w}.**"
        costs = [f"{c['label']} {money2(c['cost'])}" for c in pe if c["cost"] is not None]
        find(f"{lead} Blended new-customer cost {money2(bf['cost'])}" + (f" ({', '.join(costs)})" if costs else "")
             + f". {pb}" + (f" {join_and(others)}." if others else ""))
    if ch is not None and "at" in bf:
        r_b = bf["horizons"][ch]["repeat_rate"]
        vs = [f"{pct(c['horizons'][ch]['repeat_rate'])} ({c['label']})" for c in pe]
        if p is not None:
            dif = differs(bf["horizons"][ch]["repeaters"], bf["new_customers"], p["horizons"][ch]["repeaters"],
                          p["new_customers"])
            lead = {-1: "**Fewer came back.**", 1: "**More came back.**", 0: "**No clear difference in coming back.**"}[dif]
        else:
            lead = "**Repeat rate.**"
        find(f"{lead} {pct(r_b)} ordered again within {ch} days ({bf['horizons'][ch]['repeaters']:,} of "
             f"{bf['new_customers']:,})" + (f", vs {join_and(vs)}" if vs else "") + ".")
        if bf["at"]["later_orders"]:
            dd_b = bf["at"]["later_discounted"] / bf["at"]["later_orders"]
            vs = [f"{pct(c['at']['later_discounted'] / c['at']['later_orders'], 0)} ({c['label']})"
                  for c in pe if c["at"]["later_orders"]]
            if p is not None and p["at"]["later_orders"]:
                dif = differs(bf["at"]["later_discounted"], bf["at"]["later_orders"], p["at"]["later_discounted"],
                              p["at"]["later_orders"])
                lead = {1: "**More discount-dependent.**", -1: "**Less discount-dependent.**",
                        0: "**No clear difference in discount use.**"}[dif]
            else:
                lead = "**Discount dependence.**"
            find(f"{lead} {pct(dd_b, 0)} of their repeat orders within {ch} days used a discount "
                 f"({bf['at']['later_discounted']:,} of {bf['at']['later_orders']:,})" + (f", vs {join_and(vs)}" if vs else "") + ".")
    deep, ref = a["cuts"]["deep"], a["cuts"]["discount"][a["ref_band"]]
    dv = a["cuts"]["deep_vs_ref"]
    if h is not None and ok_row(deep, a) and ok_row(ref, a) and dv:
        # Only behaviour after the first order can call these customers weaker (or stronger); see analyze().
        if dv["verdict"] == "weaker":
            lead = ("**Customers acquired at deeper discounts repeated less.**" if dv["repeat"] == -1 else
                    "**Customers acquired at deeper discounts were worth less after their first order.**")
        elif dv["verdict"] == "stronger":
            lead = ("**Customers acquired at deeper discounts repeated more.**" if dv["repeat"] == 1 else
                    "**Customers acquired at deeper discounts were worth more after their first order.**")
        else:
            lead = "**Deeper first-order discounts: no clear difference after the first order.**"
        txt = (f"{lead} {plural(deep['customers'], 'customer')} ({pct(deep['share'])}) took more than {a['ref_top']}% off "
               f"their first order: {pct(deep['repeat_rate'])} ordered again within {h} days "
               f"({range_txt(deep['lo'], deep['hi'])}) vs {pct(ref['repeat_rate'])} ({range_txt(ref['lo'], ref['hi'])}) at "
               f"{BAND_LABELS[a['ref_band']]}, and CM from repeat orders was {money2(deep['later_cm'])} vs "
               f"{money2(ref['later_cm'])} per customer. First-order CM was {money2(deep['first_cm'])} vs "
               f"{money2(ref['first_cm'])}, lower by construction at a deeper discount.")
        if dv["verdict"] == "weaker":
            txt += " This may reflect who the discount attracts; confirm with a holdout test before setting a cap."
        elif dv["verdict"] == "unclear":
            txt += " The 95% ranges overlap, so this doesn't show the deeper discount bought weaker customers."
        top = a["cuts"]["discount"][-1]
        if a["ref_band"] < len(BAND_LABELS) - 2 and ok_row(top, a):
            txt += (f" Above 35% off ({top['customers']:,}): {pct(top['repeat_rate'])} ordered again, "
                    f"{money2(top['later_cm'])} of repeat-order CM.")
        find(txt)
    prods = [r for r in a["cuts"]["product"] if ok_row(r, a) and r["key"] != "(no product listed)"]
    if h is not None and prods:
        best = sorted(prods, key=lambda r: (-r["repeat_rate"], -r["cm_per_customer"], r["key"]))[0]
        if best["vs_cohort"] == "higher":
            txt = (f"**Best entry product: `{best['key']}`.** {pct(best['repeat_rate'])} ordered again within {h} days "
                   f"({range_txt(best['lo'], best['hi'])}) vs {pct(bf['horizons'][h]['repeat_rate'])} for the cohort")
            if best.get("next_product"):
                txt += (f"; {pct(best['next_count'] / best['repeaters'], 0)} of those repeaters' next orders included "
                        f"`{best['next_product']}`")
            find(txt + ".")
        else:
            find(f"**Entry products: no clear winner.** `{best['key']}` repeated most ({pct(best['repeat_rate'])}, "
                 f"{range_txt(best['lo'], best['hi'])}), within noise of the cohort's {pct(bf['horizons'][h]['repeat_rate'])}.")
    srcs = [r for r in a["cuts"]["source"] if ok_row(r, a) and r["key"]] if a["data"]["has_source"] else []
    if h is not None and len(srcs) >= 2:
        worst = sorted(srcs, key=lambda r: (r["repeat_rate"], r["cm_per_customer"], r["key"]))[0]
        all_deep = sum(1 for m in bf["members"] if m.depth > a["ref_top"]) / bf["new_customers"]
        if worst["vs_cohort"] == "lower":
            txt = (f"**Weakest source: {source_label(worst['key'])}.** {pct(worst['repeat_rate'])} ordered again within "
                   f"{h} days ({range_txt(worst['lo'], worst['hi'])}) vs {pct(bf['horizons'][h]['repeat_rate'])} for the "
                   f"cohort, worth {money2(worst['cm_per_customer'])} each.")
            if worst["deep_share"] > 1.5 * all_deep and worst["deep_share"] > 0:
                txt += (f" {pct(worst['deep_share'], 0)} of them took more than {a['ref_top']}% off their first order "
                        f"(cohort: {pct(all_deep, 0)}), so the two effects overlap.")
            find(txt)
        else:
            find(f"**Sources: none clearly weaker.** {source_label(worst['key'])} repeated least "
                 f"({pct(worst['repeat_rate'])}, {range_txt(worst['lo'], worst['hi'])}), within noise of the cohort.")
    if bf.get("first_order_refund_rate") is not None:
        vs = [f"{pct(c['first_order_refund_rate'])} for {c['label']}" for c in comps
              if c.get("first_order_refund_rate") is not None and c["status"] == "complete"]
        find(f"**Refunds.** {pct(bf['first_order_refund_rate'])} of {bf['label']} first-order net sales were refunded "
             f"({bf['first_orders_refunded']:,} of {bf['new_customers']:,} orders), vs " + join_and(vs + [
                 f"the {econ['returns'] * 100:g}% return rate in economics.json"]) + ".")
    if n_find == 0:
        L.append("Nothing can be compared yet; see the table below.")
    L.append("")

    # 1. Side by side.
    L.append("### 1. The cohorts side by side")
    L.append("")
    L.append("| | " + " | ".join(c["label"] + (" (still acquiring)" if c["status"] == "acquiring" else "") for c in cohorts) + " |")
    L.append("| --- | " + " | ".join("---:" for _ in cohorts) + " |")

    def row(name: str, vals: list) -> None:
        L.append(f"| {name} | " + " | ".join(vals) + " |")

    row("Window", [fmt_range(c["start"], c["end"]) for c in cohorts])
    row("New customers", [f"{c['new_customers']:,}" for c in cohorts])
    row("New customers per day", [f"{c['per_day']:.1f}" for c in cohorts])
    row("First-order AOV (net sales)", [money2(c["first_order_aov"]) for c in cohorts])
    row("First-order discount, average depth", [pct(c["first_order_depth"]) for c in cohorts])
    row("First orders with a discount", [pct(c["first_orders_discounted"] / c["new_customers"]) for c in cohorts])
    if bf.get("first_order_refund_rate") is not None:
        row("First-order net sales refunded", [pct(c["first_order_refund_rate"]) for c in cohorts])
    row("Ad spend in the window (all channels)", [money0(c["spend"]) if c["spend"] is not None else "—" for c in cohorts])
    row("Blended new-customer cost", [money2(c["cost"]) if c["cost"] is not None else "—" for c in cohorts])
    for x in a["horizons"]:
        row(f"Ordered again within {x} days", [pct(c["horizons"][x]["repeat_rate"]) if c["horizons"][x]["mature"]
                                               else "not mature" for c in cohorts])
    for x in a["horizons"]:
        row(f"CM per customer at {x} days", [money2(c["horizons"][x]["cm_per_customer"]) if c["horizons"][x]["mature"]
                                             else "not mature" for c in cohorts])
    if ch is not None:
        row(f"Days to 2nd order, median (repeaters within {ch} days)",
            [(str(c["at"]["median"]) if c["at"]["median"] is not None else "—") if "at" in c else "—" for c in cohorts])
        row(f"Repeat orders with a discount (within {ch} days)",
            [(f"{pct(c['at']['later_discounted'] / c['at']['later_orders'], 0)} of {c['at']['later_orders']:,}"
              if c["at"]["later_orders"] else "—") if "at" in c else "—" for c in cohorts])
    pbs = []
    for c in cohorts:
        if c["cost"] is None or c["status"] != "complete":
            pbs.append("—")
        elif c["payback_day"] is not None:
            pbs.append(f"day {c['payback_day']}")
        else:
            pbs.append(f"not by day {c['max_day']} ({pct(c['cm_at_max_day'] / c['cost'], 0)} recovered)")
    row("Payback (CM per customer ≥ blended cost)", pbs)
    L.append("")
    imm = {}
    for c in cohorts:
        for x in a["horizons"]:
            if not c["horizons"][x]["mature"]:
                imm.setdefault(x, []).append(f"{fmt_date(c['horizons'][x]['matures_on'])} ({c['label']})")
    if imm:
        L.append("Not mature yet: " + "; ".join(f"{x} days on {join_and(v)}" for x, v in imm.items()) + ".")
    if a["data"]["spend_columns"] is None:
        L.append("Blended new-customer cost and payback: not computed (no --spend file).")
    else:
        costs_missing = [f"{c['label']} ({c['cost_note']})" for c in cohorts if c["cost"] is None and c["cost_note"]]
        if costs_missing:
            L.append("Blended new-customer cost not computed for " + join_and(costs_missing) + ".")
    L.append("Blended new-customer cost = all ad spend in the window ÷ new customers in the window. It is not "
             "attribution: it charges retention and brand spend to new customers and gives repeat orders none. "
             "Payback = first day the average customer's cumulative CM reaches that cost, read only over days every "
             "customer has reached.")
    L.append("")

    if h is None:
        L.append("### 2–4. Inside the BFCM cohort")
        L.append("")
        L.append(f"Skipped: no horizon is mature for {bf['label']} yet, so repeat rates can't be cut. First-order mix "
                 f"so far: " + ", ".join(f"{BAND_LABELS[i]} {n:,}" for i, n in enumerate(bf["bands"])) + ".")
        L.append("")
    else:
        rate_all = bf["horizons"][h]["repeat_rate"]
        # 2. Discount depth.
        L.append(f"### 2. {bf['label']} by first-order discount ({h} days)")
        L.append("")
        L.append(f"| First-order discount | Customers | Share | First-order AOV | Ordered again within {h} days (95% range) "
                 f"| CM per customer at {h} days | of which first order | of which repeat orders | Repeat orders with a discount |")
        L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in a["cuts"]["discount"]:
            lab = BAND_LABELS[r["key"]] + (" (planned depth)" if r["key"] == a["ref_band"] and econ["planned_discount"] is not None else "")
            if r["customers"] == 0:
                L.append(f"| {lab} | 0 | — | — | — | — | — | — | — |")
                continue
            L.append(f"| {lab} | {r['customers']:,} | {pct(r['share'])} | {money2(r['first_order_aov'])} | {cell_rate(r, a)} "
                     f"| {cell_small(r, a, money2(r.get('cm_per_customer', 0)))} "
                     f"| {cell_small(r, a, money2(r.get('first_cm', 0)))} | {cell_small(r, a, money2(r.get('later_cm') or 0))} "
                     f"| {cell_small(r, a, (str(r.get('later_discounted', 0)) + ' of ' + str(r.get('later_orders', 0))) if r.get('later_orders') else '—')} |")
        L.append(f"| **All {bf['label']}** | **{bf['new_customers']:,}** | **100%** | **{money2(bf['first_order_aov'])}** "
                 f"| **{pct(rate_all)}** | **{money2(bf['horizons'][h]['cm_per_customer'])}** "
                 f"| **{money2(sum(m.cm_by(0) for m in bf['members']) / bf['new_customers'])}** "
                 f"| **{money2(sum(m.cm_by(h) - m.cm_by(0) for m in bf['members']) / bf['new_customers'])}** | — |")
        L.append("")
        if a["cuts"]["deep_codes"]:
            L.append(f"Discount codes on first orders above {a['ref_top']}% off: "
                     + ", ".join(f"{c} {n:,}" for c, n in a["cuts"]["deep_codes"]) + ". An order deeper than any single "
                     "code may mean a code stacked on an automatic sale discount.")
        L.append("Depth = discount ÷ (net sales + discount) on the first order, rounded to a whole percent. First-order "
                 "CM includes any other order placed the same day; a deeper discount lowers it by construction, so "
                 "compare the repeat rate and repeat-order CM to judge the customers. "
                 "Compare-at markdowns (clearance) aren't in the order's discount, so they don't count here.")
        L.append("")
        # 3. Source.
        L.append(f"### 3. {bf['label']} by acquisition source ({h} days)")
        L.append("")
        srows = a["cuts"]["source"]
        if not a["data"]["has_source"]:
            L.append("Skipped: the orders file has no utm_source or landing_site column.")
        else:
            L.append(f"| Source (first order's utm_source) | Customers | Spellings merged | First order >{a['ref_top']}% off "
                     f"| Ordered again within {h} days (95% range) | vs cohort ({pct(rate_all)}) | CM per customer at {h} days |")
            L.append("| --- | ---: | --- | ---: | ---: | --- | ---: |")
            for r in srows[:MAX_CUT_ROWS]:
                sp = ", ".join(f"`{s}` {n:,}" for s, n in r["spellings"]) if r["spellings"] else "—"
                vs = {"higher": "clearly higher", "lower": "clearly lower", "unclear": "within noise"}.get(r.get("vs_cohort"), "—")
                L.append(f"| {source_label(r['key'])} | {r['customers']:,} | {sp} | {pct(r['deep_share'], 0)} | {cell_rate(r, a)} "
                         f"| {cell_small(r, a, vs)} | {cell_small(r, a, money2(r.get('cm_per_customer', 0)))} |")
            if len(srows) > MAX_CUT_ROWS:
                L.append(f"| {len(srows) - MAX_CUT_ROWS} more sources | {sum(r['customers'] for r in srows[MAX_CUT_ROWS:]):,} | | | | | |")
            L.append("")
            merged = [r for r in srows if len(r["spellings"]) > 1]
            if merged:
                L.append(f"utm_source was spelled {sum(len(r['spellings']) for r in merged)} ways for "
                         f"{plural(len(merged), 'channel')}; spellings were merged before cutting.")
            L.append("Source is the first order's utm_source" + (" (from the landing URL when the column was blank)"
                     if a["data"]["from_landing"] else "") + ": where the first order came from, last click, not what "
                     "convinced the customer.")
        L.append("")
        # 4. Entry product.
        prows = a["cuts"]["product"]
        L.append(f"### 4. {bf['label']} by entry product ({h} days)")
        L.append("")
        if not prows:
            L.append("Skipped: the orders file has no products column.")
        else:
            L.append(f"| Product in the first order | Customers | Ordered again within {h} days (95% range) | vs cohort "
                     f"({pct(rate_all)}) | CM per customer at {h} days | Most common next product (share of repeaters) |")
            L.append("| --- | ---: | ---: | --- | ---: | --- |")
            for r in prows[:MAX_CUT_ROWS]:
                vs = {"higher": "clearly higher", "lower": "clearly lower", "unclear": "within noise"}.get(r.get("vs_cohort"), "—")
                nxt = (f"`{r['next_product']}` ({pct(r['next_count'] / r['repeaters'], 0)})" if r.get("next_product") else "—")
                key = r["key"] if r["key"] == "(no product listed)" else f"`{r['key']}`"
                L.append(f"| {key} | {r['customers']:,} | {cell_rate(r, a)} | {cell_small(r, a, vs)} "
                         f"| {cell_small(r, a, money2(r.get('cm_per_customer', 0)))} | {cell_small(r, a, nxt)} |")
            if len(prows) > MAX_CUT_ROWS:
                L.append(f"| {len(prows) - MAX_CUT_ROWS} more products | {sum(r['customers'] for r in prows[MAX_CUT_ROWS:]):,} | | | | |")
            L.append("")
            L.append("A first order with two products counts under both, so rows overlap. Next product = what the "
                     "repeaters' second order included.")
        L.append("")

    # 5. Recommendations.
    ny = next_bfcm_year(a["as_of"])
    L.append(f"### 5. Recommendations for BFCM {ny} (recommended; nothing has been changed)")
    L.append("")
    if a["recommendations"]:
        for i, r in enumerate(a["recommendations"], 1):
            L.append(f"{i}. {r}")
    else:
        L.append("None yet: no horizon is mature. Re-run when the first one is.")
    L.append("")

    # 6. Caveats and data checks.
    d = a["data"]
    L.append("### 6. Caveats and data checks")
    L.append("")
    notes = []
    out = d["customers"] - d["customers_in_cohorts"]
    s = (f"**Data:** {d['orders_used']:,} orders from {d['customers']:,} customers, {fmt_range(d['first_day'], d['last_day'])}. "
         + (f"{plural(d['customers_in_cohorts'], 'customer')} {'is' if d['customers_in_cohorts'] == 1 else 'are'} in an "
            f"analysed cohort; {plural(out, 'other customer')} first ordered outside the cohort windows and "
            f"{'is' if out == 1 else 'are'} left out." if out else "Every customer in the file is in an analysed cohort."))
    extra = []
    if d["orders_after_as_of"]:
        extra.append(f"{plural(d['orders_after_as_of'], 'order')} after the as-of date ignored")
    if d["cancelled"]:
        extra.append(f"{plural(d['cancelled'], 'cancelled order')} dropped")
    if d["no_customer"]:
        extra.append(f"{plural(d['no_customer'], 'order')} without a customer_id dropped")
    if d["item_rows"]:
        extra.append(f"{plural(d['item_rows'], 'extra line-item row')} merged into their orders")
    if extra:
        s += " Also: " + "; ".join(extra) + "."
    notes.append(s)
    if d["history_given"]:
        hs = (f"**New customers:** a customer counts as new only if the file holds no earlier order. The file is taken to "
              f"hold every order since {fmt_date(d['history_start'])} (--history-since).")
    else:
        hs = (f"**New customers:** a customer counts as new only if the file holds no earlier order. Order history in "
              f"the file starts {fmt_date(d['history_start'])}.")
    if d["short_history"]:
        hs += (f" **Warning:** that is less than a year before {join_and(d['short_history'])} "
               f"{'starts' if len(d['short_history']) == 1 else 'start'}, so a customer "
               f"who last bought before {fmt_date(d['history_start'])} and came back would be counted as new. Export "
               "full history, or pass --history-since if the file was filtered on each customer's first-ever order.")
    notes.append(hs)
    est = f"estimated at {100 - econ['gm'] * 100:g}% of the pre-discount price (gross margin {econ['gm'] * 100:g}% in economics.json)"
    if not d["has_cogs"]:
        cost_txt = f"COGS {est} for every order (no cogs column)"
    elif d["cogs_used"] == d["orders_used"]:
        cost_txt = "COGS from the cogs column"
    else:
        cost_txt = (f"COGS from the cogs column for {d['cogs_used']:,} orders and {est} for the other "
                    f"{d['orders_used'] - d['cogs_used']:,}")
    notes.append(f"**Costs:** {cost_txt}; shipping and fulfillment {money2(econ['ship'])} per order; payment fees "
                 f"{econ['fee_pct'] * 100:g}% + {money2(econ['fee_fixed'])} on net sales (fees on tax and shipping charged aren't "
                 "counted, so CM is slightly overstated); refunded goods restocked, shipping and fees "
                 "not recovered (the same assumptions as the BFCM Holiday Budget Planner)."
                 + ("" if d["has_refunds"] else f" No refunded_amount column: {econ['returns'] * 100:g}% of every "
                    "order's net sales treated as refunded.")
                 + (f" {d['refund_capped']:,} refunds larger than the order's net sales (tax or shipping refunded) were "
                    "capped at net sales." if d["refund_capped"] else ""))
    if econ["pct_warnings"]:
        notes.append("**Check economics.json:** " + "; ".join(econ["pct_warnings"]) + ".")
    sd = [f"{c['same_day_customers']:,} ({c['label']})" for c in cohorts if c.get("same_day_customers")]
    if sd:
        notes.append("**Same-day orders:** customers who placed another order on their first day: " + ", ".join(sd)
                     + ". Those orders count toward value, not as a repeat.")
    seas = []
    for c in comps:
        if "at" in c and c["end"] < a["bfcm_window"][0] and c["at"]["later_in_bfcm"]:
            seas.append(f"{c['at']['later_in_bfcm']:,} of {c['label']}'s {plural(c['at']['later_orders'], 'repeat order')} "
                        f"within {ch} days {'was' if c['at']['later_in_bfcm'] == 1 else 'were'} placed during "
                        f"{bf['label']} itself")
    if "at" in bf and bf["at"]["second_before_christmas"]:
        seas.append(f"{bf['at']['second_before_christmas']:,} of {bf['label']}'s {plural(bf['at']['repeaters'], 'second order')} "
                    f"within {ch} days came before Dec 25 (gift shopping)")
    if seas:
        notes.append("**Seasons differ:** " + "; ".join(seas) + ". Each cohort's first months fall in a different part "
                     "of the year, so compare them as what they are, not as a controlled test.")
    if h is not None:
        notes.append(f"**Small cells:** cuts with fewer than {plural(a['min_cell'], 'customer')} show no rates (--min-cell). Ranges "
                     "are 95% Wilson intervals; \"clearly higher/lower\" means the range excludes the cohort-wide rate. "
                     "A rough screen for noise, not proof.")
        notes.append("**Correlation, not causation:** the cuts show who came back, not why. Customers who chose a deep "
                     "code or came from a given channel may differ in ways the data can't see. Test a change (a holdout, "
                     "or one channel at a time) before treating a cut as the cause.")
    young = [c["label"] for c in cohorts if (a["as_of"] - c["end"]).days < RETURNS_SETTLE_DAYS]
    if young:
        notes.append(f"**Returns still open:** {join_and(young)} first orders are under {RETURNS_SETTLE_DAYS} days old; "
                     "refunds and CM can still move (holiday return windows often run to Jan 31).")
    if d["utc_unconverted"]:
        notes.append(f"**Time zone:** {plural(d['utc_unconverted'], 'timestamp')} written in UTC were read as written. Pass "
                     "--timezone with the store's time zone so orders placed late in the evening land on the right day.")
    elif d["timezone"]:
        notes.append(f"**Time zone:** timestamps converted to {d['timezone']} before taking the order date.")
    for s in a["skipped"]:
        notes.append(f"**Left out:** {s}")
    for n in notes:
        L.append(f"- {n}")
    L.append("")
    L.append("_States: findings are detected; section 5 is recommended. Nothing was applied: read-only, no store, ad "
             "account or email flow was changed._")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- JSON
def to_jsonable(x):
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items() if k != "members"}
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v) for v in x]
    if isinstance(x, float):
        return round(x, 6)
    return x


def json_view(a: dict) -> dict:
    out = dict(a)
    out["econ"] = dict(a["econ"])
    out["band_labels"] = list(BAND_LABELS)
    out["cuts"] = dict(a["cuts"])
    out["cuts"]["discount"] = [dict(r, band=BAND_LABELS[r["key"]]) for r in a["cuts"]["discount"]]
    out["cuts"]["source"] = [dict(r, label=source_label(r["key"])) for r in a["cuts"]["source"]]
    out["cohorts"] = [dict(c) for c in a["cohorts"]]
    return to_jsonable(out)


# --------------------------------------------------------------------------- main
def parse_horizons(s: str) -> tuple:
    try:
        hs = [int(x) for x in s.replace(" ", "").split(",") if x]
    except ValueError:
        raise InputError(f"--horizons must be whole numbers of days like 30,60,90,180,365 (got {s!r})")
    if not hs or any(x <= 0 for x in hs) or len(hs) > 8:
        raise InputError(f"--horizons must be 1 to 8 positive whole numbers of days (got {s!r})")
    return tuple(sorted(set(hs)))


def resolve_config(args, orders: list, econ: dict) -> dict:
    zone_name = args.timezone
    as_of = parse_iso_date(args.as_of, "--as-of") if args.as_of else max(o.day for o in orders)
    if args.year is not None:
        year = args.year
    elif args.bfcm_window:
        year = parse_window(args.bfcm_window, "--bfcm-window")[0].year
    else:
        year = None
        for y in range(as_of.year, as_of.year - 3, -1):
            if default_bfcm_window(y)[1] <= as_of:
                year = y
                break
        if year is None:
            raise InputError("can't pick a BFCM year from the as-of date: pass --year")
    if args.bfcm_window:
        bfcm = parse_window(args.bfcm_window, "--bfcm-window")
        is_default = bfcm == default_bfcm_window(year)
    else:
        bfcm, is_default = default_bfcm_window(year), True
    if as_of < bfcm[0]:
        raise InputError(f"as-of date {as_of.isoformat()} is before the BFCM {year} window starts "
                         f"({bfcm[0].isoformat()})")
    compare = []
    if args.compare:
        for s in args.compare:
            if "=" not in s:
                raise InputError(f"--compare must look like \"LABEL=YYYY-MM-DD:YYYY-MM-DD\" (got {s!r})")
            lab, win = s.rsplit("=", 1)
            if not lab.strip():
                raise InputError(f"--compare needs a label before '=' (got {s!r})")
            a, b = parse_window(win, f"--compare {lab.strip()!r}")
            compare.append((lab.strip(), a, b))
    else:
        feb_end = dt.date(year + 1, 3, 1) - dt.timedelta(days=1)
        compare = [(f"October {year}", dt.date(year, 10, 1), dt.date(year, 10, 31)),
                   (f"Jan–Feb {year + 1}", dt.date(year + 1, 1, 1), feb_end)]
    wins = [(f"BFCM {year}", bfcm[0], bfcm[1])] + compare
    labels = [w[0] for w in wins]
    if len(set(labels)) != len(labels):
        raise InputError("cohort labels must be unique")
    for i in range(len(wins)):
        for j in range(i + 1, len(wins)):
            if wins[i][1] <= wins[j][2] and wins[j][1] <= wins[i][2]:
                raise InputError(f"cohort windows overlap: {wins[i][0]} ({wins[i][1].isoformat()} to "
                                 f"{wins[i][2].isoformat()}) and {wins[j][0]} ({wins[j][1].isoformat()} to "
                                 f"{wins[j][2].isoformat()}); a customer can only be new once")
    hist = parse_iso_date(args.history_since, "--history-since") if args.history_since else None
    if args.min_cell < 1:
        raise InputError("--min-cell must be at least 1")
    return {"as_of": as_of, "year": year, "bfcm": bfcm, "bfcm_default": is_default, "bfcm_label": f"BFCM {year}",
            "compare": compare, "horizons": parse_horizons(args.horizons), "min_cell": args.min_cell,
            "history_since": hist, "timezone": zone_name,
            "account": args.account or econ["brand"] or "Store"}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--orders", required=True, help="orders CSV, one row per order, full history (DATA_CONTRACT.md §1)")
    ap.add_argument("--economics", required=True, help="economics.json, same file as the BFCM Holiday Budget Planner")
    ap.add_argument("--spend", help="optional daily ad spend CSV: date plus *_spend columns")
    ap.add_argument("--year", type=int, help="BFCM year to analyse (default: the latest one finished by --as-of)")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: last order date in the file)")
    ap.add_argument("--bfcm-window", help="START:END (default: Monday of Thanksgiving week to the Tuesday after Cyber Monday)")
    ap.add_argument("--compare", action="append", help="comparison cohort LABEL=START:END, repeatable "
                    "(default: October of --year and Jan–Feb of the next year)")
    ap.add_argument("--horizons", default="30,60,90,180,365", help="days since first order (default 30,60,90,180,365)")
    ap.add_argument("--min-cell", type=int, default=DEFAULT_MIN_CELL, help="smallest cut shown with rates (default 30)")
    ap.add_argument("--timezone", help="store time zone for timestamps written with a UTC offset, e.g. America/New_York")
    ap.add_argument("--history-since", help="date from which the file holds every order (default: its earliest order)")
    ap.add_argument("--account", help="store name for the title (default: economics.json brand)")
    ap.add_argument("--json", help="also write the structured analysis to this path")
    args = ap.parse_args(argv)
    try:
        zone = parse_zone(args.timezone) if args.timezone else None
        econ = load_economics(args.economics)
        set_currency(econ["currency"])
        orders, info = load_orders(args.orders, zone)
        spend = load_spend(args.spend) if args.spend else None
        cfg = resolve_config(args, orders, econ)
        a = analyze(orders, info, econ, spend, cfg)
        report = render(a)
        if args.json:
            try:
                with open(args.json, "w", encoding="utf-8") as f:
                    json.dump(json_view(a), f, indent=2, sort_keys=True, ensure_ascii=False)
                    f.write("\n")
            except OSError as e:
                raise InputError(f"cannot write --json {args.json}: {e.strerror or e}")
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = getattr(sys.stdout, "buffer", None)
    if out is not None:
        sys.stdout.flush()
        out.write(report.encode("utf-8"))
        out.flush()
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
