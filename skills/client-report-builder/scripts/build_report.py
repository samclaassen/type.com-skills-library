#!/usr/bin/env python3
"""Build a month-end client performance report from exported paid, analytics and budget files.

Stdlib only. No network access. Deterministic: every "today" comes from --as-of.

Usage:
    python3 scripts/build_report.py \
        --paid data/paid_performance.csv \
        --analytics data/analytics_channels.csv \
        --budget data/budget.csv \
        --client "Northwind Coffee Co." \
        --month 2026-08 \
        --as-of 2026-09-02 \
        [--definitions finance/revenue_definitions.json] \
        [--finance-totals data/finance_totals.csv]

Platform-claimed revenue is shown per platform only. It is never summed:
Meta, Google and TikTok each claim credit for overlapping sales.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PAID_COLUMNS = [
    "client",
    "month",
    "channel",
    "spend",
    "impressions",
    "clicks",
    "conversions",
    "revenue",
]
ANALYTICS_COLUMNS = [
    "client",
    "month",
    "channel_group",
    "sessions",
    "new_users",
    "conversions",
    "revenue",
]
BUDGET_COLUMNS = ["client", "month", "channel", "committed_budget"]
FINANCE_COLUMNS = ["client", "month", "net_sales", "new_customers"]

DEFAULT_DEFINITIONS_PATH = "finance/revenue_definitions.json"
# The only formulas this skill implements. A definitions file that names a
# different formula is rejected rather than silently computed another way.
SUPPORTED_MER = "net_sales / total_marketing_spend"
SUPPORTED_NEW_CUSTOMER_CAC = "total_marketing_spend / new_customers"
CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")
# Display prefix for money. "$" unless finance names a non-USD reporting
# currency, in which case the ISO code is used ("EUR 1,250").
CURRENCY = {"prefix": "$"}

ZERO = Decimal("0")
CENT = Decimal("0.01")


# --------------------------------------------------------------------------- io


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def read_table(path: str, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        fail(f"{label}: cannot read {path} ({exc.strerror or exc})")
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{label}: {path} is empty (no header row)")
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            fail(f"{label}: {path} is missing required column(s): {', '.join(missing)}")
        rows: list[tuple[dict[str, str], int]] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {
                (key or "").strip(): (value.strip() if isinstance(value, str) else "")
                for key, value in raw.items()
            }
            rows.append((row, line_no))
        if not rows:
            fail(f"{label}: {path} has a header but no data rows")
        return rows


def need_text(row: dict[str, str], column: str, path: str, line_no: int) -> str:
    value = row.get(column, "")
    if not value:
        fail(f"{path} line {line_no}: column '{column}' is empty")
    return value


def need_month(row: dict[str, str], column: str, path: str, line_no: int) -> str:
    value = need_text(row, column, path, line_no)
    if not MONTH_RE.match(value):
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected YYYY-MM")
    return value


def need_decimal(row: dict[str, str], column: str, path: str, line_no: int) -> Decimal:
    value = row.get(column, "")
    if value == "":
        fail(f"{path} line {line_no}: column '{column}' is empty, expected a number")
    cleaned = value.replace(",", "").replace("$", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a number")
    if number < ZERO:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a value >= 0")
    return number


# ---------------------------------------------------------------------- helpers


def divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    try:
        return numerator / denominator
    except (DivisionByZero, InvalidOperation):
        return None


def pct_change(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None or prior == ZERO:
        return None
    return (current - prior) / prior * Decimal("100")


def money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    prefix = CURRENCY["prefix"]
    if whole < 0:
        return f"-{prefix}{abs(whole):,}"
    return f"{prefix}{whole:,}"


def money_cents(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    cents = value.quantize(CENT, rounding=ROUND_HALF_UP)
    prefix = CURRENCY["prefix"]
    if cents < 0:
        return f"-{prefix}{abs(cents):,.2f}"
    return f"{prefix}{cents:,.2f}"


def ratio(value: Decimal | None, places: str = "0.01") -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP)}"


def times(value: Decimal | None) -> str:
    """A ratio shown as a multiple, e.g. 6.02x; n/a (no suffix) when undefined."""
    return "n/a" if value is None else f"{ratio(value)}x"


def percent(value: Decimal | None, places: str = "0.01") -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP)}%"


def signed_percent(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{'+' if rounded >= 0 else ''}{rounded}%"


def signed_money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    prefix = CURRENCY["prefix"]
    if whole < 0:
        return f"-{prefix}{abs(whole):,}"
    return f"+{prefix}{whole:,}"


def number(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def previous_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


def month_label(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    return f"{calendar.month_name[mon]} {year}"


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# --------------------------------------------------------------------- loading


class ChannelTotals:
    """Accumulator for one channel in one month.

    Analytics rows reuse the same shape: sessions accumulate into `impressions`
    and new users into `clicks`, which keeps the rate helpers below meaningful
    (session -> conversion rate instead of click -> conversion rate).
    """

    __slots__ = ("spend", "impressions", "clicks", "conversions", "revenue")

    def __init__(self) -> None:
        self.spend = ZERO
        self.impressions = ZERO
        self.clicks = ZERO
        self.conversions = ZERO
        self.revenue = ZERO

    def add(self, spend, impressions, clicks, conversions, revenue) -> None:
        self.spend += spend
        self.impressions += impressions
        self.clicks += clicks
        self.conversions += conversions
        self.revenue += revenue

    @property
    def sessions(self) -> Decimal:
        return self.impressions

    @property
    def cpa(self) -> Decimal | None:
        return divide(self.spend, self.conversions)

    @property
    def roas(self) -> Decimal | None:
        return divide(self.revenue, self.spend)

    @property
    def cpc(self) -> Decimal | None:
        return divide(self.spend, self.clicks)

    @property
    def ctr(self) -> Decimal | None:
        rate = divide(self.clicks, self.impressions)
        return None if rate is None else rate * Decimal("100")

    @property
    def cvr(self) -> Decimal | None:
        rate = divide(self.conversions, self.clicks)
        return None if rate is None else rate * Decimal("100")


def load_paid(path: str, client: str, months: set[str]) -> dict[str, dict[str, ChannelTotals]]:
    rows = read_table(path, PAID_COLUMNS, "paid performance")
    by_month: dict[str, dict[str, ChannelTotals]] = {month: {} for month in months}
    matched = False
    for row, line_no in rows:
        row_client = need_text(row, "client", path, line_no)
        month = need_month(row, "month", path, line_no)
        if row_client != client or month not in months:
            continue
        matched = True
        channel = need_text(row, "channel", path, line_no)
        bucket = by_month[month].setdefault(channel, ChannelTotals())
        bucket.add(
            need_decimal(row, "spend", path, line_no),
            need_decimal(row, "impressions", path, line_no),
            need_decimal(row, "clicks", path, line_no),
            need_decimal(row, "conversions", path, line_no),
            need_decimal(row, "revenue", path, line_no),
        )
    if not matched:
        fail(
            f"paid performance: {path} has no rows for client '{client}' in "
            f"{', '.join(sorted(months))}"
        )
    return by_month


def load_analytics(path: str, client: str, months: set[str]) -> dict[str, dict[str, ChannelTotals]]:
    rows = read_table(path, ANALYTICS_COLUMNS, "analytics")
    by_month: dict[str, dict[str, ChannelTotals]] = {month: {} for month in months}
    matched = False
    for row, line_no in rows:
        row_client = need_text(row, "client", path, line_no)
        month = need_month(row, "month", path, line_no)
        if row_client != client or month not in months:
            continue
        matched = True
        group = need_text(row, "channel_group", path, line_no)
        bucket = by_month[month].setdefault(group, ChannelTotals())
        bucket.add(
            ZERO,
            need_decimal(row, "sessions", path, line_no),
            need_decimal(row, "new_users", path, line_no),
            need_decimal(row, "conversions", path, line_no),
            need_decimal(row, "revenue", path, line_no),
        )
    if not matched:
        fail(
            f"analytics: {path} has no rows for client '{client}' in "
            f"{', '.join(sorted(months))}"
        )
    return by_month


def load_budget(path: str, client: str, month: str) -> dict[str, Decimal]:
    rows = read_table(path, BUDGET_COLUMNS, "budget")
    budgets: dict[str, Decimal] = {}
    for row, line_no in rows:
        if need_text(row, "client", path, line_no) != client:
            continue
        if need_month(row, "month", path, line_no) != month:
            continue
        channel = need_text(row, "channel", path, line_no)
        if channel in budgets:
            fail(f"{path} line {line_no}: duplicate budget row for channel '{channel}' in {month}")
        budgets[channel] = need_decimal(row, "committed_budget", path, line_no)
    if not budgets:
        fail(f"budget: {path} has no rows for client '{client}' in {month}")
    return budgets


def optional_decimal(row: dict[str, str], column: str, path: str, line_no: int) -> Decimal | None:
    """Blank means "not supplied"; anything else must be a number >= 0."""
    if row.get(column, "") == "":
        return None
    return need_decimal(row, column, path, line_no)


def load_finance_totals(
    path: str, client: str, month: str, compare_to: str
) -> dict[str, dict[str, Decimal | None]]:
    """Finance's own net sales and new-customer count per month.

    Returns {month: {"net_sales": ..., "new_customers": ...}}. A blank cell means
    finance did not supply that figure, and the dependent metric is not computed.
    """
    rows = read_table(path, FINANCE_COLUMNS, "finance totals")
    months = {month, compare_to}
    totals: dict[str, dict[str, Decimal | None]] = {}
    for row, line_no in rows:
        if need_text(row, "client", path, line_no) != client:
            continue
        row_month = need_month(row, "month", path, line_no)
        if row_month not in months:
            continue
        if row_month in totals:
            fail(f"{path} line {line_no}: duplicate finance totals row for {client} in {row_month}")
        totals[row_month] = {
            "net_sales": optional_decimal(row, "net_sales", path, line_no),
            "new_customers": optional_decimal(row, "new_customers", path, line_no),
        }
    if month not in totals:
        fail(f"finance totals: {path} has no row for client '{client}' in {month}")
    return totals


def definitions_key(data: dict, dotted: str, path: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            fail(f"definitions: {path} is missing required key '{dotted}'")
        node = node[part]
    return node


def definitions_text(data: dict, dotted: str, path: str) -> str:
    value = definitions_key(data, dotted, path)
    if not isinstance(value, str) or not value.strip():
        fail(f"definitions: {path} key '{dotted}' must be a non-empty string")
    return value.strip()


def load_definitions(explicit_path: str | None) -> dict:
    """Read finance's revenue_definitions.json, or fall back to this skill's defaults.

    An explicit --definitions path that does not exist is an error. The default
    path being absent is not, but the fallback is announced at the top of the
    report so nobody mistakes the defaults for finance's definitions.
    """
    path = explicit_path or DEFAULT_DEFINITIONS_PATH
    if not os.path.isfile(path):
        if explicit_path is not None:
            fail(f"--definitions {path} does not exist or is not a file")
        return {
            "source": "defaults",
            "path": DEFAULT_DEFINITIONS_PATH,
            "version": None,
            "owner": None,
            "reporting_currency": None,
            "attributed_revenue_basis": "platform_claimed_in_platform_window",
            "attribution_windows": {},
            "new_customer_definition": None,
            "mer": SUPPORTED_MER,
            "new_customer_cac": SUPPORTED_NEW_CUSTOMER_CAC,
        }
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except OSError as exc:
        fail(f"definitions: cannot read {path} ({exc.strerror or exc})")
    except json.JSONDecodeError as exc:
        fail(f"definitions: {path} is not valid JSON ({exc})")
    if not isinstance(data, dict):
        fail(f"definitions: {path} must contain a JSON object")

    version = definitions_key(data, "version", path)
    if not isinstance(version, (str, int)) or str(version).strip() == "":
        fail(f"definitions: {path} key 'version' must be a non-empty string")
    currency = definitions_text(data, "reporting_currency", path)
    if not CURRENCY_CODE_RE.match(currency):
        fail(f"definitions: {path} key 'reporting_currency' is '{currency}', expected an ISO code like USD")

    windows = definitions_key(data, "marketing.attribution_windows", path)
    if not isinstance(windows, dict) or not windows:
        fail(f"definitions: {path} key 'marketing.attribution_windows' must be a non-empty object")
    clean_windows: dict[str, str] = {}
    for platform, window in windows.items():
        if not isinstance(window, str) or not window.strip():
            fail(
                f"definitions: {path} key 'marketing.attribution_windows.{platform}' "
                "must be a non-empty string"
            )
        clean_windows[str(platform).strip().lower()] = window.strip()

    formulas = {}
    for key, supported in (("mer", SUPPORTED_MER), ("new_customer_cac", SUPPORTED_NEW_CUSTOMER_CAC)):
        formula = " ".join(definitions_text(data, f"marketing.{key}", path).split())
        if formula != supported:
            fail(
                f"definitions: {path} defines marketing.{key} as '{formula}'; this skill only "
                f"implements '{supported}'. Ask finance, or compute it outside this skill."
            )
        formulas[key] = formula

    owner = data.get("owner")
    basis = data.get("marketing", {}).get("attributed_revenue_basis")
    return {
        "source": "file",
        "path": path,
        "version": str(version).strip(),
        "owner": owner.strip() if isinstance(owner, str) and owner.strip() else None,
        "reporting_currency": currency,
        "attributed_revenue_basis": (
            basis.strip() if isinstance(basis, str) and basis.strip() else None
        ),
        "attribution_windows": clean_windows,
        "new_customer_definition": definitions_text(
            data, "marketing.new_customer_definition", path
        ),
        "mer": formulas["mer"],
        "new_customer_cac": formulas["new_customer_cac"],
    }


def platform_key(channel: str) -> str:
    """Map a channel label to a definitions key: 'Meta Ads' -> 'meta'."""
    key = channel.strip().lower()
    for suffix in (" advertising", " ads"):
        if key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    return key


def attribution_window(channel: str, definitions: dict) -> str | None:
    """Finance's window for this channel, or None when finance has not defined one."""
    windows = definitions["attribution_windows"]
    return windows.get(platform_key(channel), windows.get(channel.strip().lower()))


def window_label(channel: str, definitions: dict) -> str:
    if definitions["source"] == "defaults":
        return "as exported (unverified)"
    return attribution_window(channel, definitions) or "not defined by finance"


def median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


# -------------------------------------------------------------------- analysis


def pacing_fraction(as_of: date, month: str) -> tuple[Decimal, int, int, bool]:
    """Return (elapsed_fraction, days_elapsed, days_in_month, month_complete)."""
    year, mon = int(month[:4]), int(month[5:])
    days_in_month = calendar.monthrange(year, mon)[1]
    month_start = date(year, mon, 1)
    month_end = date(year, mon, days_in_month)
    if as_of < month_start:
        fail(
            f"--as-of {as_of.isoformat()} is before the reporting month {month}; "
            "a month-end report must be produced on or after the first day of the month"
        )
    if as_of >= month_end:
        return Decimal("1"), days_in_month, days_in_month, True
    days_elapsed = as_of.day
    return (
        Decimal(days_elapsed) / Decimal(days_in_month),
        days_elapsed,
        days_in_month,
        False,
    )


def build_channel_rows(
    current: dict[str, ChannelTotals],
    prior: dict[str, ChannelTotals],
    budgets: dict[str, Decimal],
    elapsed: Decimal,
) -> list[dict]:
    names = sorted(set(current) | set(prior) | set(budgets))
    rows = []
    for name in names:
        now = current.get(name, ChannelTotals())
        was = prior.get(name, ChannelTotals())
        budget = budgets.get(name)
        expected = None if budget is None else budget * elapsed
        pace = None if expected in (None, ZERO) else divide(now.spend, expected)
        rows.append(
            {
                "channel": name,
                "spend": now.spend,
                "prior_spend": was.spend,
                "spend_delta": now.spend - was.spend,
                "spend_delta_pct": pct_change(now.spend, was.spend),
                "conversions": now.conversions,
                "prior_conversions": was.conversions,
                "conversions_delta_pct": pct_change(now.conversions, was.conversions),
                "revenue": now.revenue,
                "cpa": now.cpa,
                "prior_cpa": was.cpa,
                "cpa_delta_pct": pct_change(now.cpa, was.cpa),
                "roas": now.roas,
                "prior_roas": was.roas,
                "roas_delta_pct": pct_change(now.roas, was.roas),
                "cpc": now.cpc,
                "ctr": now.ctr,
                "cvr": now.cvr,
                "budget": budget,
                "expected_spend": expected,
                "pace": pace,
                "budget_remaining": None if budget is None else budget - now.spend,
                "in_paid_data": name in current,
            }
        )
    return rows


def totals_of(buckets: dict[str, ChannelTotals]) -> ChannelTotals:
    total = ChannelTotals()
    for bucket in buckets.values():
        total.add(
            bucket.spend, bucket.impressions, bucket.clicks, bucket.conversions, bucket.revenue
        )
    return total


def build_recommendations(
    channel_rows: list[dict],
    benchmark_roas: Decimal | None,
    target_roas: Decimal | None,
    tracking_floor: Decimal,
    material_spend: Decimal,
) -> list[dict]:
    """Turn the channel table into ranked, dollar-quantified actions.

    Each channel's platform-claimed ROAS is compared against the median of the
    per-platform claimed ROAS figures. It is deliberately not compared against
    a portfolio ROAS built from summed platform revenue: platforms' credit
    overlaps, so that sum double-counts sales. Nor is it compared against the
    site-blended figure: blended ROAS includes organic and direct revenue, so
    using it as a per-channel bar would condemn every paid channel.
    """
    recs: list[dict] = []
    for row in channel_rows:
        if not row["in_paid_data"]:
            continue
        channel = row["channel"]
        spend = row["spend"]
        cut_flagged = False

        if spend >= tracking_floor and row["conversions"] == ZERO:
            recs.append(
                {
                    "priority": 1,
                    "channel": channel,
                    "action": "Fix conversion tracking before spending further",
                    "detail": (
                        f"{channel} spent {money(spend)} and reported zero conversions. "
                        "Every efficiency number for this channel is unusable until the "
                        "conversion action fires, so treat the spend as unmeasured, not as "
                        "proven waste."
                    ),
                    "dollar_impact": spend,
                }
            )
            continue

        roas = row["roas"]
        floor = target_roas if target_roas is not None else (
            None if benchmark_roas is None else benchmark_roas / Decimal("2")
        )
        if (
            roas is not None
            and floor is not None
            and roas < floor
            and spend >= material_spend
        ):
            cut_flagged = True
            cut = spend * Decimal("0.30")
            recs.append(
                {
                    "priority": 1,
                    "channel": channel,
                    "action": f"Cut {channel} budget by 30% and rebuild targeting",
                    "detail": (
                        f"Platform-claimed ROAS of {ratio(roas)}x is below the "
                        f"{ratio(floor)}x floor on "
                        f"{money(spend)} of spend. Pulling {money(cut)} out funds the "
                        "channels that are already clearing the floor."
                    ),
                    "dollar_impact": cut,
                }
            )

        cpa_delta = row["cpa_delta_pct"]
        if (
            cpa_delta is not None
            and cpa_delta >= Decimal("20")
            and spend >= material_spend
            and row["conversions"] > ZERO
            and not cut_flagged
        ):
            recs.append(
                {
                    "priority": 2,
                    "channel": channel,
                    "action": f"Rebuild the worst-performing {channel} segments",
                    "detail": (
                        f"Cost per conversion moved from {money_cents(row['prior_cpa'])} to "
                        f"{money_cents(row['cpa'])} ({signed_percent(cpa_delta)}). Prune the "
                        "bottom-decile ad sets and refresh creative before raising spend."
                    ),
                    "dollar_impact": spend,
                }
            )

        pace = row["pace"]
        budget = row["budget"]
        if pace is not None and budget is not None:
            if pace < Decimal("0.90") and not cut_flagged:
                shortfall = budget - spend
                recs.append(
                    {
                        "priority": 2,
                        "channel": channel,
                        "action": f"Recover {money(shortfall)} of unspent {channel} budget",
                        "detail": (
                            f"{channel} delivered {percent(pace * Decimal('100'), '0.1')} of its "
                            f"committed {money(budget)}. Raise daily caps or widen targeting so "
                            "the committed budget actually ships."
                        ),
                        "dollar_impact": shortfall,
                    }
                )
            elif pace > Decimal("1.10"):
                overage = spend - budget
                recs.append(
                    {
                        "priority": 2,
                        "channel": channel,
                        "action": f"Cap {channel} daily budgets",
                        "detail": (
                            f"{channel} ran {money(overage)} over its committed {money(budget)} "
                            f"({percent(pace * Decimal('100'), '0.1')} of plan). Reset daily caps "
                            "so the next cycle reconciles to the committed total."
                        ),
                        "dollar_impact": overage,
                    }
                )

        if (
            roas is not None
            and benchmark_roas is not None
            and roas >= benchmark_roas * Decimal("1.25")
            and spend >= material_spend
        ):
            increase = spend * Decimal("0.20")
            recs.append(
                {
                    "priority": 3,
                    "channel": channel,
                    "action": f"Scale {channel} by {money(increase)}",
                    "detail": (
                        f"{channel} claims {ratio(roas)}x against a median platform-claimed "
                        f"ROAS of {ratio(benchmark_roas)}x across paid channels. "
                        "A 20% increase is the largest step that historically holds efficiency "
                        "within one reporting cycle."
                    ),
                    "dollar_impact": increase,
                }
            )

    recs.sort(key=lambda item: (item["priority"], -item["dollar_impact"], item["channel"]))
    return recs


# ---------------------------------------------------------------------- report


def definitions_lines(definitions: dict) -> list[str]:
    """The definitions in force. Always the first lines of the report."""
    if definitions["source"] == "defaults":
        return [
            f"**No finance definitions file found** (looked for `{definitions['path']}`; "
            "pass `--definitions <path>` to use one). This report uses the skill's own "
            "defaults, which are not finance's definitions:",
            "- Attributed revenue: each platform's claimed conversion value as exported, "
            "shown per platform and never summed.",
            "- Attribution windows: whatever each platform export used; not verified.",
            f"- MER: {SUPPORTED_MER}, with net sales from `--finance-totals`; not computed "
            "without it.",
            f"- New-customer CAC: {SUPPORTED_NEW_CUSTOMER_CAC}, with new customers as counted "
            "in `--finance-totals` (no finance definition of a new customer); not computed "
            "without it.",
            "- Total marketing spend: paid media spend in the paid export.",
            "- Blended CAC: paid spend / every analytics conversion. Blended ROAS: analytics "
            "revenue / paid spend.",
            "- Currency: one currency as exported, shown as $; no FX conversion.",
        ]
    windows = "; ".join(
        f"{platform} {window}"
        for platform, window in sorted(definitions["attribution_windows"].items())
    )
    owner = f", owner {definitions['owner']}" if definitions["owner"] else ""
    basis = definitions["attributed_revenue_basis"] or "not stated"
    return [
        f"**Finance definitions:** `{definitions['path']}`, version {definitions['version']}"
        f"{owner}.",
        f"- Attributed revenue basis: {basis}; shown per platform and never summed.",
        f"- Attribution windows: {windows}.",
        f"- New customer: {definitions['new_customer_definition']}.",
        f"- MER: {definitions['mer']}. New-customer CAC: {definitions['new_customer_cac']}.",
        "- Total marketing spend: paid media spend in the paid export.",
        f"- Reporting currency: {definitions['reporting_currency']}; amounts are taken as "
        "exported in that currency (this skill does no FX conversion).",
    ]


def render_markdown(ctx: dict) -> str:
    out: list[str] = []
    add = out.append

    for line in definitions_lines(ctx["definitions"]):
        add(f"> {line}")
    add("")

    add(f"# {ctx['client']} — Performance Report, {month_label(ctx['month'])}")
    add("")
    add(
        f"Prepared {ctx['as_of']} | Comparison period: {month_label(ctx['compare_to'])} | "
        f"Reporting window: {ctx['days_elapsed']} of {ctx['days_in_month']} days"
        + ("" if ctx["month_complete"] else " (month still in flight)")
    )
    add("")

    add("## Executive summary")
    add("")
    for line in ctx["summary_lines"]:
        add(f"- {line}")
    add("")

    add("## Headline metrics")
    add("")
    add("| Metric | This month | Last month | Change |")
    add("| --- | ---: | ---: | ---: |")
    for label, now, was, change in ctx["headline_rows"]:
        add(f"| {label} | {now} | {was} | {change} |")
    add("")
    add(
        "Site revenue and conversions come from analytics, the source of truth for blended "
        "figures in this report. Blended CAC divides total paid media spend by every "
        "conversion the analytics property recorded, including organic and direct, so it "
        "measures what the whole program costs per conversion rather than what any single "
        "platform claims."
    )
    add("")
    for line in ctx["headline_notes"]:
        add(line)
        add("")

    add("## Paid channel performance")
    add("")
    add(
        "| Channel | Attribution window | Spend | vs LM | Claimed conv. | CPA | vs LM | "
        "Claimed revenue | Claimed ROAS | vs LM |"
    )
    add("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in ctx["channel_rows"]:
        if not row["in_paid_data"] and row["spend"] == ZERO:
            continue
        add(
            f"| {row['channel']} | {row['attribution_window']} | {money(row['spend'])} | "
            f"{signed_percent(row['spend_delta_pct'])} | {number(row['conversions'])} | "
            f"{money_cents(row['cpa'])} | {signed_percent(row['cpa_delta_pct'])} | "
            f"{money(row['revenue'])} | "
            f"{ratio(row['roas'])}x | {signed_percent(row['roas_delta_pct'])} |"
        )
    totals = ctx["totals"]
    add(
        f"| **Total spend** | | **{money(totals['spend'])}** | "
        f"**{signed_percent(totals['spend_delta_pct'])}** | — | — | — | — | — | — |"
    )
    add("")
    add(
        "Claimed conversions, revenue and ROAS are each platform's own credit in its own "
        "attribution window. Platforms' credit overlaps, so they are not additive and no "
        "total is shown; total sales come only from analytics or finance."
    )
    add("")

    add("## Budget pacing")
    add("")
    add("| Channel | Committed | Spent | Expected to date | Pace | Variance |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    for row in ctx["channel_rows"]:
        if row["budget"] is None:
            continue
        variance = row["spend"] - (row["expected_spend"] or ZERO)
        add(
            f"| {row['channel']} | {money(row['budget'])} | {money(row['spend'])} | "
            f"{money(row['expected_spend'])} | "
            f"{percent(None if row['pace'] is None else row['pace'] * Decimal('100'), '0.1')} | "
            f"{signed_money(variance)} |"
        )
    pacing = ctx["pacing_totals"]
    add(
        f"| **Total** | **{money(pacing['budget'])}** | **{money(pacing['spend'])}** | "
        f"**{money(pacing['expected'])}** | **{percent(pacing['pace_pct'], '0.1')}** | "
        f"**{signed_money(pacing['variance'])}** |"
    )
    add("")

    add("## Site-wide channel mix")
    add("")
    add("| Channel group | Sessions | Conversions | Revenue | Revenue vs LM |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for row in ctx["analytics_rows"]:
        add(
            f"| {row['channel_group']} | {number(row['sessions'])} | "
            f"{number(row['conversions'])} | {money(row['revenue'])} | "
            f"{signed_percent(row['revenue_delta_pct'])} |"
        )
    add("")

    add("## What we're changing next month")
    add("")
    if not ctx["recommendations"]:
        add(
            "- No threshold-breaking issues surfaced this month. Hold the current allocation "
            "and keep testing creative inside the existing budget."
        )
    else:
        for index, rec in enumerate(ctx["recommendations"], start=1):
            add(f"{index}. **{rec['action']}** (P{rec['priority']}) — {rec['detail']}")
    add("")

    if ctx["notes"]:
        add("## Data notes")
        add("")
        for note in ctx["notes"]:
            add(f"- {note}")
        add("")

    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------------ main


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Month-end client performance report builder.")
    parser.add_argument("--paid", required=True, help="paid_performance.csv export")
    parser.add_argument("--analytics", required=True, help="analytics_channels.csv export")
    parser.add_argument("--budget", required=True, help="budget.csv for the reporting month")
    parser.add_argument("--client", required=True, help="client name exactly as it appears in the files")
    parser.add_argument("--month", required=True, help="reporting month, YYYY-MM")
    parser.add_argument("--as-of", required=True, dest="as_of", help="report date, YYYY-MM-DD")
    parser.add_argument("--compare-to", dest="compare_to", help="comparison month, defaults to the prior month")
    parser.add_argument("--target-roas", dest="target_roas", help="contracted ROAS floor, e.g. 2.5")
    parser.add_argument(
        "--tracking-floor",
        dest="tracking_floor",
        default="250",
        help="spend above which a zero-conversion channel is flagged (default 250)",
    )
    parser.add_argument(
        "--material-spend",
        dest="material_spend",
        default="500",
        help="minimum channel spend for efficiency recommendations (default 500)",
    )
    parser.add_argument(
        "--definitions",
        help=(
            "finance's revenue_definitions.json (optional; defaults to "
            f"{DEFAULT_DEFINITIONS_PATH} when that file exists)"
        ),
    )
    parser.add_argument(
        "--finance-totals",
        dest="finance_totals",
        help="finance_totals.csv with net_sales and new_customers per month (optional; enables MER and new-customer CAC)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of markdown")
    return parser.parse_args(argv)


def positive_decimal(raw: str, flag: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        fail(f"{flag} is '{raw}', expected a number")
    if value < ZERO:
        fail(f"{flag} is '{raw}', expected a value >= 0")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not MONTH_RE.match(args.month):
        fail(f"--month is '{args.month}', expected YYYY-MM")
    if not DATE_RE.match(args.as_of):
        fail(f"--as-of is '{args.as_of}', expected YYYY-MM-DD")
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        fail(f"--as-of is '{args.as_of}', which is not a real calendar date")
    compare_to = args.compare_to or previous_month(args.month)
    if not MONTH_RE.match(compare_to):
        fail(f"--compare-to is '{compare_to}', expected YYYY-MM")
    if compare_to == args.month:
        fail("--compare-to must be a different month than --month")

    tracking_floor = positive_decimal(args.tracking_floor, "--tracking-floor")
    material_spend = positive_decimal(args.material_spend, "--material-spend")
    target_roas = None
    if args.target_roas is not None:
        target_roas = positive_decimal(args.target_roas, "--target-roas")

    definitions = load_definitions(args.definitions)
    currency = definitions["reporting_currency"]
    if currency is not None and currency != "USD":
        CURRENCY["prefix"] = f"{currency} "

    months = {args.month, compare_to}
    paid = load_paid(args.paid, args.client, months)
    analytics = load_analytics(args.analytics, args.client, months)
    budgets = load_budget(args.budget, args.client, args.month)
    finance = (
        load_finance_totals(args.finance_totals, args.client, args.month, compare_to)
        if args.finance_totals
        else {}
    )

    elapsed, days_elapsed, days_in_month, month_complete = pacing_fraction(as_of, args.month)

    channel_rows = build_channel_rows(
        paid[args.month], paid[compare_to], budgets, elapsed
    )
    for row in channel_rows:
        row["attribution_window"] = window_label(row["channel"], definitions)

    now_total = totals_of(paid[args.month])
    prior_total = totals_of(paid[compare_to])
    now_site = totals_of(analytics[args.month])
    prior_site = totals_of(analytics[compare_to])

    blended_cac = divide(now_total.spend, now_site.conversions)
    prior_blended_cac = divide(prior_total.spend, prior_site.conversions)
    blended_roas = divide(now_site.revenue, now_total.spend)
    prior_blended_roas = divide(prior_site.revenue, prior_total.spend)

    # MER and new-customer CAC use finance's own figures and are computed only
    # when finance supplied them. Total marketing spend is the paid media spend
    # in the paid export; this skill has no other cost input.
    now_fin = finance.get(args.month, {})
    prior_fin = finance.get(compare_to, {})
    net_sales = now_fin.get("net_sales")
    prior_net_sales = prior_fin.get("net_sales")
    new_customers = now_fin.get("new_customers")
    prior_new_customers = prior_fin.get("new_customers")
    mer = None if net_sales is None else divide(net_sales, now_total.spend)
    prior_mer = None if prior_net_sales is None else divide(prior_net_sales, prior_total.spend)
    new_customer_cac = None if new_customers is None else divide(now_total.spend, new_customers)
    prior_new_customer_cac = (
        None if prior_new_customers is None else divide(prior_total.spend, prior_new_customers)
    )

    # Benchmark for per-channel recommendations: the median of per-platform
    # claimed ROAS, never a ROAS built from summed platform revenue. Channels
    # with no claimed conversions are left out (0.00x there is a tracking gap).
    benchmark_roas = median(
        [
            row["roas"]
            for row in channel_rows
            if row["in_paid_data"] and row["roas"] is not None and row["conversions"] > ZERO
        ]
    )

    total_budget = sum(budgets.values(), ZERO)
    expected_total = total_budget * elapsed
    pace_total = divide(now_total.spend, expected_total)

    analytics_rows = []
    for group in sorted(set(analytics[args.month]) | set(analytics[compare_to])):
        now = analytics[args.month].get(group, ChannelTotals())
        was = analytics[compare_to].get(group, ChannelTotals())
        analytics_rows.append(
            {
                "channel_group": group,
                "sessions": now.sessions,
                "conversions": now.conversions,
                "revenue": now.revenue,
                "revenue_delta_pct": pct_change(now.revenue, was.revenue),
            }
        )

    recommendations = build_recommendations(
        channel_rows, benchmark_roas, target_roas, tracking_floor, material_spend
    )

    notes: list[str] = []
    if definitions["source"] == "file":
        undefined = sorted(
            row["channel"]
            for row in channel_rows
            if row["in_paid_data"] and attribution_window(row["channel"], definitions) is None
        )
        if undefined:
            notes.append(
                f"{', '.join(undefined)} {'has' if len(undefined) == 1 else 'have'} no attribution "
                f"window in {definitions['path']}; claimed figures for "
                f"{'it' if len(undefined) == 1 else 'them'} use whatever window the export used. "
                "Ask finance to add one."
            )
    if args.finance_totals:
        for label, value, metric in (
            ("net_sales", net_sales, "MER"),
            ("new_customers", new_customers, "new-customer CAC"),
        ):
            if value is None:
                notes.append(
                    f"{label} is blank for {month_label(args.month)} in {args.finance_totals}; "
                    f"{metric} is not computed."
                )
        if new_customers == ZERO:
            notes.append(
                f"new_customers is 0 for {month_label(args.month)} in {args.finance_totals}; "
                "new-customer CAC is undefined and not computed."
            )
        if now_total.spend == ZERO:
            notes.append(
                f"Paid spend is 0 for {month_label(args.month)}; MER is undefined and not computed."
            )
        if compare_to not in finance:
            notes.append(
                f"{args.finance_totals} has no row for {month_label(compare_to)}; MER and "
                "new-customer CAC have no comparison."
            )
    for row in channel_rows:
        if row["budget"] is not None and not row["in_paid_data"]:
            notes.append(
                f"{row['channel']} carries a committed budget of {money(row['budget'])} but has no "
                f"delivery rows in the paid export for {month_label(args.month)}."
            )
        if row["in_paid_data"] and row["budget"] is None:
            notes.append(
                f"{row['channel']} spent {money(row['spend'])} with no committed budget on file; "
                "pacing for this channel is unmeasured."
            )
    if not month_complete:
        notes.append(
            f"The month is incomplete as of {args.as_of}. Pacing compares spend to "
            f"{days_elapsed}/{days_in_month} of each committed budget; period-over-period "
            "deltas compare a partial month to a full one."
        )

    summary_lines = [
        f"Paid media spend was {money(now_total.spend)} against a committed "
        f"{money(total_budget)} ({percent(None if pace_total is None else pace_total * Decimal('100'), '0.1')} of plan), "
        f"{signed_percent(pct_change(now_total.spend, prior_total.spend))} versus "
        f"{month_label(compare_to)}.",
        f"The site recorded {number(now_site.conversions)} conversions "
        f"({signed_percent(pct_change(now_site.conversions, prior_site.conversions))}) and "
        f"{money(now_site.revenue)} of revenue "
        f"({signed_percent(pct_change(now_site.revenue, prior_site.revenue))}).",
        f"Blended CAC was {money_cents(blended_cac)} "
        f"({signed_percent(pct_change(blended_cac, prior_blended_cac))}) and blended ROAS was "
        f"{ratio(blended_roas)}x ({signed_percent(pct_change(blended_roas, prior_blended_roas))}).",
    ]
    finance_bits = []
    if mer is not None:
        finance_bits.append(
            f"MER was {ratio(mer)}x on {money(net_sales)} of finance net sales "
            f"({signed_percent(pct_change(mer, prior_mer))})"
        )
    if new_customer_cac is not None:
        finance_bits.append(
            f"new-customer CAC was {money_cents(new_customer_cac)} across "
            f"{number(new_customers)} new customers "
            f"({signed_percent(pct_change(new_customer_cac, prior_new_customer_cac))})"
        )
    if finance_bits:
        joined = " and ".join(finance_bits)
        summary_lines.append(joined[0].upper() + joined[1:] + ".")
    # Channels with no recorded conversions are excluded from the best/worst
    # callout: a 0.00x ROAS there is a measurement failure, not performance.
    paid_channels = [
        row
        for row in channel_rows
        if row["in_paid_data"] and row["roas"] is not None and row["conversions"] > ZERO
    ]
    if paid_channels:
        best = max(paid_channels, key=lambda row: (row["roas"], row["channel"]))
        worst = min(paid_channels, key=lambda row: (row["roas"], row["channel"]))
        if best["channel"] != worst["channel"]:
            summary_lines.append(
                f"By each platform's own claimed ROAS, {best['channel']} was the strongest "
                f"channel at {ratio(best['roas'])}x on {money(best['spend'])}; "
                f"{worst['channel']} was the weakest at {ratio(worst['roas'])}x on "
                f"{money(worst['spend'])}."
            )
    if recommendations:
        summary_lines.append(
            f"{len(recommendations)} changes are queued for next month, "
            f"{sum(1 for rec in recommendations if rec['priority'] == 1)} of them urgent."
        )

    headline_rows = [
        (
            "Paid spend",
            money(now_total.spend),
            money(prior_total.spend),
            signed_percent(pct_change(now_total.spend, prior_total.spend)),
        ),
        (
            "Site conversions",
            number(now_site.conversions),
            number(prior_site.conversions),
            signed_percent(pct_change(now_site.conversions, prior_site.conversions)),
        ),
        (
            "Site revenue",
            money(now_site.revenue),
            money(prior_site.revenue),
            signed_percent(pct_change(now_site.revenue, prior_site.revenue)),
        ),
        (
            "Blended CAC",
            money_cents(blended_cac),
            money_cents(prior_blended_cac),
            signed_percent(pct_change(blended_cac, prior_blended_cac)),
        ),
        (
            "Blended ROAS",
            times(blended_roas),
            times(prior_blended_roas),
            signed_percent(pct_change(blended_roas, prior_blended_roas)),
        ),
    ]
    if mer is not None:
        headline_rows.append(
            (
                "MER (net sales / marketing spend)",
                times(mer),
                times(prior_mer),
                signed_percent(pct_change(mer, prior_mer)),
            )
        )
    if new_customer_cac is not None:
        headline_rows.append(
            (
                "New-customer CAC",
                money_cents(new_customer_cac),
                money_cents(prior_new_customer_cac),
                signed_percent(pct_change(new_customer_cac, prior_new_customer_cac)),
            )
        )

    headline_notes: list[str] = []
    if mer is not None or new_customer_cac is not None:
        headline_notes.append(
            f"MER and new-customer CAC use finance's net sales and new-customer counts from "
            f"`{args.finance_totals}` over the paid media spend in the paid export "
            f"({money(now_total.spend)}); costs outside that export are not included."
        )
    missing = [
        name
        for name, value in (("MER", mer), ("new-customer CAC", new_customer_cac))
        if value is None
    ]
    if missing:
        reason = (
            "no finance totals file (`--finance-totals`) was supplied"
            if not args.finance_totals
            else "an input is blank or zero (see Data notes)"
        )
        subject = " and ".join(missing)
        headline_notes.append(f"{subject[0].upper()}{subject[1:]}: not computed; {reason}.")

    # Spend is additive across platforms; claimed conversions and revenue are not.
    totals_block = {
        "spend": now_total.spend,
        "spend_delta_pct": pct_change(now_total.spend, prior_total.spend),
    }
    pacing_totals = {
        "budget": total_budget,
        "spend": now_total.spend,
        "expected": expected_total,
        "pace_pct": None if pace_total is None else pace_total * Decimal("100"),
        "variance": now_total.spend - expected_total,
    }

    ctx = {
        "client": args.client,
        "month": args.month,
        "compare_to": compare_to,
        "as_of": args.as_of,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "month_complete": month_complete,
        "summary_lines": summary_lines,
        "headline_rows": headline_rows,
        "headline_notes": headline_notes,
        "definitions": definitions,
        "channel_rows": channel_rows,
        "totals": totals_block,
        "pacing_totals": pacing_totals,
        "analytics_rows": analytics_rows,
        "recommendations": recommendations,
        "notes": notes,
    }

    if args.json:
        payload = {
            "client": args.client,
            "month": args.month,
            "compare_to": compare_to,
            "as_of": args.as_of,
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "month_complete": month_complete,
            "totals": {
                "paid_spend": as_float(now_total.spend),
                "paid_spend_prior": as_float(prior_total.spend),
                "committed_budget": as_float(total_budget),
                "pace_pct": as_float(pacing_totals["pace_pct"]),
                "site_conversions": as_float(now_site.conversions),
                "site_revenue": as_float(now_site.revenue),
                "blended_cac": as_float(blended_cac),
                "blended_roas": as_float(blended_roas),
                "total_marketing_spend": as_float(now_total.spend),
                "net_sales": as_float(net_sales),
                "new_customers": as_float(new_customers),
                "mer": as_float(mer),
                "new_customer_cac": as_float(new_customer_cac),
                "median_platform_claimed_roas": as_float(benchmark_roas),
            },
            "definitions": {
                "source": definitions["source"],
                "path": definitions["path"],
                "version": definitions["version"],
                "reporting_currency": definitions["reporting_currency"],
                "attributed_revenue_basis": definitions["attributed_revenue_basis"],
                "attribution_windows": definitions["attribution_windows"],
                "new_customer_definition": definitions["new_customer_definition"],
                "mer": definitions["mer"],
                "new_customer_cac": definitions["new_customer_cac"],
                "note": definitions_lines(definitions)[0],
            },
            "channels": [
                {
                    "channel": row["channel"],
                    "spend": as_float(row["spend"]),
                    "spend_delta_pct": as_float(row["spend_delta_pct"]),
                    "attribution_window": row["attribution_window"],
                    "conversions": as_float(row["conversions"]),
                    "claimed_revenue": as_float(row["revenue"]),
                    "cpa": as_float(row["cpa"]),
                    "cpa_delta_pct": as_float(row["cpa_delta_pct"]),
                    "roas": as_float(row["roas"]),
                    "roas_delta_pct": as_float(row["roas_delta_pct"]),
                    "committed_budget": as_float(row["budget"]),
                    "pace_pct": as_float(
                        None if row["pace"] is None else row["pace"] * Decimal("100")
                    ),
                }
                for row in channel_rows
            ],
            "recommendations": [
                {
                    "priority": rec["priority"],
                    "channel": rec["channel"],
                    "action": rec["action"],
                    "detail": rec["detail"],
                    "dollar_impact": as_float(rec["dollar_impact"]),
                }
                for rec in recommendations
            ],
            "notes": notes,
        }
        if definitions["source"] == "defaults":
            print(f"note: {definitions_lines(definitions)[0]}", file=sys.stderr)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_markdown(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
