#!/usr/bin/env python3
"""Monthly acquisition cohorts, retention economics, and CAC payback from an order export.

Builds cohorts from each customer's first order month, then reports cumulative
revenue per customer and cumulative contribution margin per customer by month
since acquisition, payback against CAC, and which cohorts are tracking below a
reference cohort at the same age.

Stdlib only, offline, deterministic. "Today" comes from the required --as-of.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from calendar import monthrange
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CENTS = Decimal("0.01")
ZERO = Decimal("0")

REQUIRED_ORDER_COLUMNS = ["order_id", "customer_id", "order_date", "net_revenue"]
OPTIONAL_ORDER_COLUMNS = [
    "cogs",
    "shipping_charged",
    "shipping_cost",
    "payment_fee",
    "refund_amount",
    "discount",
    "refund_date",
    "currency",
    "tags",
    "financial_status",
    "cancelled",
]
# This skill's own revenue definition, used when no finance definitions file is
# found: net_revenue is after discounts and excludes shipping and tax; refunds
# are subtracted on the original order's date.
SKILL_NET_SALES = {
    "subtract_discounts": True,
    "subtract_refunds": True,
    "include_shipping_charged": False,
    "include_tax": False,
    "include_gift_card_sales": False,
}
SPEND_COLUMNS = ["cohort_month", "marketing_spend"]


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def money(value: Decimal) -> str:
    return f"{value.quantize(CENTS, rounding=ROUND_HALF_UP):,}"


def pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return ZERO
    return (numerator / denominator) * Decimal(100)


def parse_iso_date(value: str, *, where: str) -> date:
    text = value.strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        fail(f"{where}: {value!r} is not a YYYY-MM-DD date")
        raise


def parse_decimal(value: str, *, where: str, allow_blank: bool = True) -> Decimal:
    text = (value or "").strip().replace(",", "").replace("$", "")
    if text in ("", "-"):
        if allow_blank:
            return ZERO
        fail(f"{where}: value is blank but required")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        fail(f"{where}: {value!r} is not a number")
        raise


def decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", "").replace("$", ""))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")


def month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def month_ordinal(value: date) -> int:
    return value.year * 12 + (value.month - 1)


def ordinal_to_key(ordinal: int) -> str:
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def month_end(ordinal: int) -> date:
    year = ordinal // 12
    month = ordinal % 12 + 1
    return date(year, month, monthrange(year, month)[1])


# --------------------------------------------------------------------------
# Finance revenue definitions (REVENUE_DEFINITIONS.md). Finance owns the file;
# this skill applies the rules it can and says plainly which it cannot.
# --------------------------------------------------------------------------

DEFAULT_DEFINITIONS_PATH = "finance/revenue_definitions.json"
NET_SALES_FLAGS = (
    "subtract_discounts",
    "subtract_refunds",
    "include_shipping_charged",
    "include_tax",
    "include_gift_card_sales",
)
KNOWN_TOP_LEVEL_KEYS = {
    "version",
    "owner",
    "reporting_timezone",
    "reporting_currency",
    "fx",
    "net_sales",
    "refunds",
    "sale_date_basis",
    "excluded_orders",
}
TRUE_WORDS = {"true", "yes", "y", "1"}
FALSE_WORDS = {"false", "no", "n", "0", ""}
OFFSET_WITHOUT_COLON = re.compile(r"\s*([+-])(\d{2})(\d{2})$")


@dataclass
class Definitions:
    path: str
    version: str
    owner: str | None
    timezone_name: str | None
    tz: ZoneInfo | None
    currency: str | None
    rates_file: str | None
    fx_source: str | None
    net_sales: dict[str, bool]
    net_sales_set: set[str]
    refund_basis: str
    refund_basis_set: bool
    sale_date_basis: str | None
    excluded_set: bool
    excluded_tags: list[str]
    excluded_statuses: list[str]
    exclude_cancelled: bool
    exclude_zero_value: bool
    unused_keys: list[str]


def load_definitions(explicit_path: str | None, skill_net_sales: dict[str, bool]) -> Definitions | None:
    """Return the finance definitions, or None when no file exists at the default path."""
    path = explicit_path or DEFAULT_DEFINITIONS_PATH
    if not os.path.isfile(path):
        if explicit_path:
            fail(f"--definitions {explicit_path}: file not found")
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except OSError as exc:
        fail(f"cannot open definitions file {path}: {exc.strerror}")
        raise
    except json.JSONDecodeError as exc:
        fail(f"definitions file {path} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}")
        raise
    where = f"definitions file {path}"
    if not isinstance(data, dict):
        fail(f"{where}: top level must be a JSON object")

    def text_key(container: dict, key: str, label: str, *, required: bool = False) -> str | None:
        value = container.get(key)
        if value is None:
            if required:
                fail(f"{where}: '{label}' is missing; finance must version the definitions")
            return None
        if not isinstance(value, str) or not value.strip():
            fail(f"{where}: '{label}' must be a non-empty string")
        return value.strip()

    def section(key: str) -> dict:
        value = data.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            fail(f"{where}: '{key}' must be an object")
        return value

    version = text_key(data, "version", "version", required=True)
    assert version is not None
    owner = text_key(data, "owner", "owner")

    timezone_name = text_key(data, "reporting_timezone", "reporting_timezone")
    tz: ZoneInfo | None = None
    if timezone_name is not None:
        try:
            tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            fail(f"{where}: reporting_timezone {timezone_name!r} is not an IANA time zone (e.g. America/New_York)")

    currency = text_key(data, "reporting_currency", "reporting_currency")
    if currency is not None:
        currency = currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            fail(f"{where}: reporting_currency {currency!r} is not a 3-letter currency code")

    fx = section("fx")
    rates_file = text_key(fx, "rates_file", "fx.rates_file")
    fx_source = text_key(fx, "source", "fx.source")

    net_sales_section = section("net_sales")
    starts_from = net_sales_section.get("starts_from")
    if starts_from is not None and starts_from != "gross_product_sales":
        fail(
            f"{where}: net_sales.starts_from {starts_from!r} is not supported; "
            "this skill's revenue starts from product sales (gross_product_sales)"
        )
    net_sales = dict(skill_net_sales)
    net_sales_set: set[str] = set()
    for flag in NET_SALES_FLAGS:
        if flag in net_sales_section:
            value = net_sales_section[flag]
            if not isinstance(value, bool):
                fail(f"{where}: net_sales.{flag} must be true or false, not {value!r}")
            net_sales[flag] = value
            net_sales_set.add(flag)

    refunds = section("refunds")
    refund_basis = "order_date"
    refund_basis_set = False
    if "date_basis" in refunds:
        refund_basis = refunds["date_basis"]
        if refund_basis not in ("refund_date", "order_date"):
            fail(f"{where}: refunds.date_basis must be 'refund_date' or 'order_date', not {refund_basis!r}")
        refund_basis_set = True

    sale_date_basis = text_key(data, "sale_date_basis", "sale_date_basis")

    excluded = section("excluded_orders")

    def word_list(key: str) -> list[str]:
        value = excluded.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            fail(f"{where}: excluded_orders.{key} must be a list of non-empty strings")
        return [item.strip().lower() for item in value]

    def flag(key: str) -> bool:
        value = excluded.get(key, False)
        if not isinstance(value, bool):
            fail(f"{where}: excluded_orders.{key} must be true or false, not {value!r}")
        return value

    unused = sorted(key for key in data if key not in KNOWN_TOP_LEVEL_KEYS)
    unused += [f"refunds.{key}" for key in sorted(refunds) if key != "date_basis"]
    return Definitions(
        path=path,
        version=version,
        owner=owner,
        timezone_name=timezone_name,
        tz=tz,
        currency=currency,
        rates_file=rates_file,
        fx_source=fx_source,
        net_sales=net_sales,
        net_sales_set=net_sales_set,
        refund_basis=refund_basis,
        refund_basis_set=refund_basis_set,
        sale_date_basis=sale_date_basis,
        excluded_set="excluded_orders" in data,
        excluded_tags=word_list("tags"),
        excluded_statuses=word_list("financial_statuses"),
        exclude_cancelled=flag("cancelled"),
        exclude_zero_value=flag("zero_value"),
        unused_keys=unused,
    )


def parse_local_date(value: str, *, where: str, tz: ZoneInfo | None) -> date:
    """Day an event belongs to.

    Without a reporting timezone this is the historical behaviour: the first
    ten characters are read as a date. With one, a plain date is taken as
    already local and a timestamp must carry a UTC offset, which is converted
    to the reporting timezone before the date is taken.
    """
    if tz is None:
        return parse_iso_date(value, where=where)
    text = value.strip()
    if len(text) == 10:
        return parse_iso_date(text, where=where)
    normalised = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    normalised = OFFSET_WITHOUT_COLON.sub(lambda m: f"{m.group(1)}{m.group(2)}:{m.group(3)}", normalised)
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError:
        fail(f"{where}: {value!r} is not a YYYY-MM-DD date or an ISO 8601 timestamp")
        raise
    if moment.tzinfo is None:
        fail(
            f"{where}: timestamp {value!r} has no UTC offset, so it cannot be placed in {tz.key}. "
            "Export timestamps with their offset, or plain YYYY-MM-DD dates if they are already local."
        )
    return moment.astimezone(tz).date()


class FxRates:
    """Rates to reporting currency, loaded lazily from finance's named rates file.

    File columns: currency, rate_to_reporting (reporting-currency units per one
    unit of `currency`), and optionally date (YYYY-MM-DD) for daily rates.
    """

    def __init__(self, definitions: Definitions) -> None:
        self.definitions = definitions
        self.rates: dict[tuple[date | None, str], Decimal] | None = None
        self.dated = False
        self.used: set[str] = set()

    def _load(self, where: str, currency: str) -> None:
        path = self.definitions.rates_file
        if path is None:
            fail(
                f"{where}: amount is in {currency} but reporting currency is {self.definitions.currency} "
                f"and {self.definitions.path} names no fx.rates_file; a rate is never guessed"
            )
        assert path is not None
        try:
            handle = open(path, "r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            fail(f"cannot open fx rates file {path} (named in {self.definitions.path}): {exc.strerror}")
            raise
        rates: dict[tuple[date | None, str], Decimal] = {}
        with handle:
            reader = csv.DictReader(handle)
            header = [(name or "").strip() for name in (reader.fieldnames or [])]
            missing = [name for name in ("currency", "rate_to_reporting") if name not in header]
            if missing:
                fail(f"fx rates file {path} is missing column(s): {', '.join(missing)} (optional: date)")
            self.dated = "date" in header
            for row_number, raw in enumerate(reader, start=2):
                row = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
                rate_where = f"fx rates file {path} row {row_number}"
                code = row.get("currency", "").upper()
                if not code:
                    fail(f"{rate_where}: currency is empty")
                rate_date = parse_iso_date(row.get("date", ""), where=f"{rate_where} column date") if self.dated else None
                rate = parse_decimal(row.get("rate_to_reporting", ""), where=f"{rate_where} column rate_to_reporting", allow_blank=False)
                if rate <= 0:
                    fail(f"{rate_where}: rate_to_reporting must be greater than zero")
                if (rate_date, code) in rates:
                    fail(f"{rate_where}: duplicate rate for {code}" + (f" on {rate_date.isoformat()}" if rate_date else ""))
                rates[(rate_date, code)] = rate
        self.rates = rates

    def rate(self, currency: str, local_date: date, *, where: str) -> Decimal:
        reporting = self.definitions.currency
        if reporting is None or currency == reporting:
            return Decimal(1)
        if self.rates is None:
            self._load(where, currency)
        assert self.rates is not None
        key = (local_date if self.dated else None, currency)
        rate = self.rates.get(key)
        if rate is None:
            when = f" for {local_date.isoformat()}" if self.dated else ""
            fail(
                f"{where}: no {currency}->{reporting} rate{when} in {self.definitions.rates_file}; "
                "add it to finance's rates file (a rate is never guessed)"
            )
        assert rate is not None
        self.used.add(currency)
        return rate


def row_currency(row: dict[str, str], has_column: bool, definitions: Definitions | None, *, where: str) -> str | None:
    if definitions is None or definitions.currency is None or not has_column:
        return None
    code = row.get("currency", "").upper()
    if not code:
        fail(f"{where}: currency is empty; every row needs one when reporting_currency is set")
    return code


def truthy(value: str, *, where: str) -> bool:
    text = value.strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    fail(f"{where}: {value!r} is not true/false (accepted: true, false, yes, no, 1, 0, blank)")
    raise AssertionError


def exclusion_rule(
    row: dict[str, str], header: set[str], definitions: Definitions, value_charged: Decimal, *, where: str
) -> str | None:
    """First finance exclusion rule the row matches, in a fixed order, or None."""
    if definitions.exclude_cancelled and "cancelled" in header:
        if truthy(row.get("cancelled", ""), where=f"{where} column cancelled"):
            return "cancelled"
    if definitions.excluded_statuses and "financial_status" in header:
        status = row.get("financial_status", "").strip().lower()
        if status in definitions.excluded_statuses:
            return f"financial_status = {status}"
    if definitions.excluded_tags and "tags" in header:
        tags = [tag.strip().lower() for tag in row.get("tags", "").split(",")]
        for wanted in definitions.excluded_tags:
            if wanted in tags:
                return f"tag = {wanted}"
    if definitions.exclude_zero_value and value_charged == 0:
        return "zero value"
    return None


@dataclass
class Exclusions:
    """Orders removed under finance's excluded_orders rules, kept for reporting."""

    counts: "OrderedDict[str, int]" = field(default_factory=OrderedDict)
    values: dict[str, Decimal] = field(default_factory=dict)
    ids: set[str] = field(default_factory=set)
    unchecked: list[str] = field(default_factory=list)

    def add(self, rule: str, order_id: str, value: Decimal) -> None:
        self.counts[rule] = self.counts.get(rule, 0) + 1
        self.values[rule] = self.values.get(rule, ZERO) + value
        self.ids.add(order_id)


def unchecked_rules(definitions: Definitions, header: set[str]) -> list[str]:
    notes = []
    if definitions.exclude_cancelled and "cancelled" not in header:
        notes.append("cancelled: not checked, the export has no cancelled column (pre-filter cancelled orders)")
    if definitions.excluded_statuses and "financial_status" not in header:
        notes.append(
            f"financial_status in {', '.join(definitions.excluded_statuses)}: not checked, "
            "the export has no financial_status column (pre-filter them)"
        )
    if definitions.excluded_tags and "tags" not in header:
        notes.append(
            f"tags {', '.join(definitions.excluded_tags)}: not checked, the export has no tags column (pre-filter them)"
        )
    return notes


def render_exclusions(exclusions: Exclusions, definitions: Definitions, value_label: str) -> list[str]:
    lines = [f"EXCLUDED ORDERS (finance excluded_orders rules; value = {value_label})"]
    if not definitions.excluded_set:
        lines.append("  Definitions set no excluded_orders rules; nothing was excluded.")
    elif not exclusions.counts:
        lines.append("  No order matched an exclusion rule.")
    total_count = 0
    total_value = ZERO
    if exclusions.counts:
        lines.append(f"  {'rule':<32}{'orders':>7}{'value':>14}")
    for rule, count in exclusions.counts.items():
        lines.append(f"  {rule:<32}{count:>7}{money(exclusions.values[rule]):>14}")
        total_count += count
        total_value += exclusions.values[rule]
    if exclusions.counts:
        lines.append(f"  {'total excluded':<32}{total_count:>7}{money(total_value):>14}")
        lines.append("  An order matching several rules is counted once, under the first: cancelled,")
        lines.append("  financial_status, tag, zero value.")
    for note in exclusions.unchecked:
        lines.append(f"  {note}")
    return lines


def exclusions_json(exclusions: Exclusions) -> list[dict[str, object]]:
    return [
        {"rule": rule, "orders": count, "value": str(exclusions.values[rule].quantize(CENTS, rounding=ROUND_HALF_UP))}
        for rule, count in exclusions.counts.items()
    ]


@dataclass
class Order:
    order_id: str
    customer_id: str
    order_date: date
    net_revenue: Decimal
    contribution_margin: Decimal
    # Set only when refunds land on the refund date: the refund's effect on
    # revenue and margin, booked in the month it was issued instead.
    refund_date: date | None = None
    refund_revenue: Decimal = ZERO
    refund_margin: Decimal = ZERO


@dataclass
class Cohort:
    key: str
    ordinal: int
    customers: set[str] = field(default_factory=set)
    orders_by_index: "OrderedDict[int, int]" = field(default_factory=OrderedDict)
    revenue_by_index: dict[int, Decimal] = field(default_factory=dict)
    margin_by_index: dict[int, Decimal] = field(default_factory=dict)
    orders_per_customer: dict[str, int] = field(default_factory=dict)
    gross_revenue: Decimal = ZERO
    order_count: int = 0
    observable_horizon: int = 0
    cac: Decimal | None = None


@dataclass
class FinanceContext:
    """What the finance definitions did to this run, for the report."""

    definitions: Definitions | None
    exclusions: Exclusions
    moved_refunds: int = 0
    converted_currencies: list[str] = field(default_factory=list)
    has_currency_column: bool = False


def read_orders(
    path: str, margin_args: argparse.Namespace, definitions: Definitions | None
) -> tuple[list[Order], bool, FinanceContext]:
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open orders file {path}: {exc.strerror}")
        raise
    exclusions = Exclusions()
    moved_refunds = 0
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{path} is empty: expected a header row with {', '.join(REQUIRED_ORDER_COLUMNS)}")
        header = [(name or "").strip() for name in reader.fieldnames]
        header_set = set(header)
        missing = [name for name in REQUIRED_ORDER_COLUMNS if name not in header]
        if missing:
            fail(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Required columns are: {', '.join(REQUIRED_ORDER_COLUMNS)}"
            )
        has_cogs = "cogs" in header
        if not has_cogs and margin_args.cogs_pct is None:
            fail(
                f"{path} has no 'cogs' column, so --cogs-pct is required "
                "(e.g. --cogs-pct 38 for a 38% cost of goods)"
            )
        has_payment_fee = "payment_fee" in header

        net_sales = definitions.net_sales if definitions else SKILL_NET_SALES
        if not net_sales["subtract_discounts"] and "discount" not in header_set:
            fail(
                f"{path} has no 'discount' column, but the finance definitions say net sales do not "
                "subtract discounts, so discounts must be added back to net_revenue"
            )
        tz = definitions.tz if definitions else None
        refund_on_refund_date = definitions is not None and definitions.refund_basis == "refund_date"
        has_currency = "currency" in header_set
        fx = FxRates(definitions) if definitions else None
        if definitions:
            exclusions.unchecked = unchecked_rules(definitions, header_set)
        currencies_seen: set[str] = set()

        orders: list[Order] = []
        seen_order_ids: set[str] = set()
        for row_number, raw in enumerate(reader, start=2):
            row = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
            where = f"{path} row {row_number}"

            order_id = row.get("order_id", "")
            if not order_id:
                fail(f"{where}: order_id is empty")
            if order_id in seen_order_ids:
                fail(f"{where}: order_id {order_id!r} appears more than once; de-duplicate the export first")
            seen_order_ids.add(order_id)

            order_date = parse_local_date(row.get("order_date", ""), where=f"{where} column order_date", tz=tz)
            net_revenue = parse_decimal(
                row.get("net_revenue", ""), where=f"{where} column net_revenue", allow_blank=False
            )
            if net_revenue < 0:
                fail(f"{where}: net_revenue is negative ({net_revenue}); record refunds in refund_amount instead")

            shipping_charged = parse_decimal(row.get("shipping_charged", ""), where=f"{where} column shipping_charged")
            shipping_cost = parse_decimal(row.get("shipping_cost", ""), where=f"{where} column shipping_cost")
            refund_amount = parse_decimal(row.get("refund_amount", ""), where=f"{where} column refund_amount")
            if refund_amount < 0:
                fail(f"{where}: refund_amount is negative ({refund_amount}); record refunds as positive amounts")
            discount = ZERO
            if not net_sales["subtract_discounts"]:
                discount = parse_decimal(row.get("discount", ""), where=f"{where} column discount")

            rate = Decimal(1)
            if fx is not None:
                currency = row_currency(row, has_currency, definitions, where=where)
                if currency is not None:
                    rate = fx.rate(currency, order_date, where=f"{where} (order {order_id}, {currency})")
                elif has_currency and row.get("currency", ""):
                    currencies_seen.add(row.get("currency", "").upper())
                    if len(currencies_seen) > 1:
                        fail(
                            f"{where}: currency {row.get('currency', '')!r} differs from earlier rows and the "
                            f"definitions set no reporting_currency to convert to"
                        )
            if rate != 1:
                net_revenue *= rate
                shipping_charged *= rate
                shipping_cost *= rate
                refund_amount *= rate
                discount *= rate

            if definitions is not None:
                rule = exclusion_rule(row, header_set, definitions, net_revenue + shipping_charged, where=where)
                if rule is not None:
                    exclusions.add(rule, order_id, net_revenue)
                    continue

            customer_id = row.get("customer_id", "")
            if not customer_id:
                fail(f"{where}: customer_id is empty for order {order_id}")

            if has_cogs:
                cogs = parse_decimal(row.get("cogs", ""), where=f"{where} column cogs") * rate
            else:
                cogs = net_revenue * (margin_args.cogs_pct / Decimal(100))

            collected = net_revenue + shipping_charged
            if has_payment_fee:
                payment_fee = parse_decimal(row.get("payment_fee", ""), where=f"{where} column payment_fee") * rate
            else:
                payment_fee = collected * (margin_args.payment_fee_pct / Decimal(100)) + margin_args.payment_fee_fixed

            refunded_share = ZERO
            if net_revenue > 0:
                refunded_share = min(refund_amount / net_revenue, Decimal(1))
            recovered_cogs = cogs * refunded_share * (margin_args.restock_recovery_pct / Decimal(100))

            contribution = (
                net_revenue
                + shipping_charged
                - refund_amount
                - cogs
                + recovered_cogs
                - shipping_cost
                - payment_fee
                - margin_args.fulfillment_cost
            )

            # Revenue follows the net sales definition; contribution margin is
            # always the cash the order left behind, whatever the definition.
            revenue = net_revenue
            if not net_sales["subtract_discounts"]:
                revenue += discount
            if net_sales["include_shipping_charged"]:
                revenue += shipping_charged
            refund_revenue = -refund_amount if net_sales["subtract_refunds"] else ZERO

            order = Order(
                order_id=order_id,
                customer_id=customer_id,
                order_date=order_date,
                net_revenue=revenue + refund_revenue,
                contribution_margin=contribution,
            )
            if refund_on_refund_date and refund_amount > 0:
                if "refund_date" not in header_set:
                    fail(
                        f"{where}: order {order_id} has a refund, and the finance definitions put refunds on "
                        "the date they were issued, but the export has no refund_date column"
                    )
                raw_refund_date = row.get("refund_date", "")
                if not raw_refund_date:
                    fail(f"{where}: order {order_id} has a refund_amount but refund_date is empty")
                refund_date = parse_local_date(raw_refund_date, where=f"{where} column refund_date", tz=tz)
                if refund_date < order_date:
                    fail(f"{where}: refund_date {refund_date.isoformat()} is before order_date {order_date.isoformat()}")
                refund_margin = -refund_amount + recovered_cogs
                order.net_revenue = revenue
                order.contribution_margin = contribution - refund_margin
                order.refund_date = refund_date
                order.refund_revenue = refund_revenue
                order.refund_margin = refund_margin
                if month_ordinal(refund_date) != month_ordinal(order_date):
                    moved_refunds += 1
            orders.append(order)

    if not orders:
        if exclusions.counts:
            fail(f"{path}: every order was excluded by the finance excluded_orders rules")
        fail(f"{path} has a header but no order rows")
    context = FinanceContext(
        definitions=definitions,
        exclusions=exclusions,
        moved_refunds=moved_refunds,
        converted_currencies=sorted(fx.used) if fx else [],
        has_currency_column=has_currency,
    )
    return orders, has_cogs, context


def read_spend(path: str) -> "OrderedDict[str, Decimal]":
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open spend file {path}: {exc.strerror}")
        raise
    spend: "OrderedDict[str, Decimal]" = OrderedDict()
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{path} is empty: expected a header row with {', '.join(SPEND_COLUMNS)}")
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in SPEND_COLUMNS if name not in header]
        if missing:
            fail(f"{path} is missing required column(s): {', '.join(missing)}")
        for row_number, raw in enumerate(reader, start=2):
            row = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
            where = f"{path} row {row_number}"
            key = row.get("cohort_month", "")
            if len(key) != 7 or key[4] != "-" or not key[:4].isdigit() or not key[5:].isdigit():
                fail(f"{where} column cohort_month: {key!r} is not YYYY-MM")
            spend[key] = parse_decimal(
                row.get("marketing_spend", ""), where=f"{where} column marketing_spend", allow_blank=False
            )
    if not spend:
        fail(f"{path} has a header but no spend rows")
    return spend


def build_cohorts(orders: list[Order], as_of: date) -> "OrderedDict[str, Cohort]":
    first_order: dict[str, tuple[date, str]] = {}
    for order in sorted(orders, key=lambda item: (item.order_date, item.order_id)):
        current = first_order.get(order.customer_id)
        if current is None or (order.order_date, order.order_id) < current:
            first_order[order.customer_id] = (order.order_date, order.order_id)

    cohorts: dict[str, Cohort] = {}
    for order in sorted(orders, key=lambda item: (item.order_date, item.order_id)):
        acquisition_date = first_order[order.customer_id][0]
        cohort_ordinal = month_ordinal(acquisition_date)
        key = ordinal_to_key(cohort_ordinal)
        cohort = cohorts.get(key)
        if cohort is None:
            complete_months = 0
            while month_end(cohort_ordinal + complete_months + 1) <= as_of:
                complete_months += 1
            cohort = Cohort(key=key, ordinal=cohort_ordinal, observable_horizon=complete_months)
            cohorts[key] = cohort
        index = month_ordinal(order.order_date) - cohort_ordinal
        cohort.customers.add(order.customer_id)
        cohort.orders_by_index[index] = cohort.orders_by_index.get(index, 0) + 1
        cohort.revenue_by_index[index] = cohort.revenue_by_index.get(index, ZERO) + order.net_revenue
        cohort.margin_by_index[index] = cohort.margin_by_index.get(index, ZERO) + order.contribution_margin
        cohort.orders_per_customer[order.customer_id] = cohort.orders_per_customer.get(order.customer_id, 0) + 1
        cohort.gross_revenue += order.net_revenue
        if order.refund_date is not None:
            refund_index = month_ordinal(order.refund_date) - cohort_ordinal
            cohort.revenue_by_index[refund_index] = cohort.revenue_by_index.get(refund_index, ZERO) + order.refund_revenue
            cohort.margin_by_index[refund_index] = cohort.margin_by_index.get(refund_index, ZERO) + order.refund_margin
            cohort.gross_revenue += order.refund_revenue
        cohort.order_count += 1
    return OrderedDict(sorted(cohorts.items(), key=lambda pair: pair[1].ordinal))


def cumulative(series: dict[int, Decimal], horizon: int) -> list[Decimal]:
    running = ZERO
    out: list[Decimal] = []
    for index in range(horizon + 1):
        running += series.get(index, ZERO)
        out.append(running)
    return out


def repeat_rate_within(cohort: Cohort, orders: list[Order], horizon: int) -> Decimal:
    counts: dict[str, int] = {}
    for order in orders:
        if order.customer_id not in cohort.customers:
            continue
        index = month_ordinal(order.order_date) - cohort.ordinal
        if index <= horizon:
            counts[order.customer_id] = counts.get(order.customer_id, 0) + 1
    repeaters = sum(1 for count in counts.values() if count >= 2)
    return pct(Decimal(repeaters), Decimal(len(cohort.customers)))


def render_triangle(title: str, cohorts: "OrderedDict[str, Cohort]", series_name: str, horizon: int) -> list[str]:
    lines = [title]
    width = 10
    header = f"  {'cohort':<9}{'size':>6}  " + "".join(f"{('M' + str(i)):>{width}}" for i in range(horizon + 1))
    lines.append(header)
    for cohort in cohorts.values():
        series = cohort.revenue_by_index if series_name == "revenue" else cohort.margin_by_index
        values = cumulative(series, horizon)
        size = Decimal(len(cohort.customers))
        cells = []
        for index in range(horizon + 1):
            if index > cohort.observable_horizon:
                cells.append(f"{'.':>{width}}")
            else:
                per_customer = values[index] / size if size else ZERO
                cells.append(f"{per_customer.quantize(CENTS, rounding=ROUND_HALF_UP):>{width}}")
        lines.append(f"  {cohort.key:<9}{len(cohort.customers):>6}  " + "".join(cells))
    lines.append("  '.' = month has not fully elapsed as of the reporting date.")
    return lines


def payback_month(cohort: Cohort, cac: Decimal | None) -> tuple[str, int | None, int | None]:
    """First month cumulative margin per customer reached CAC, and the month it fell
    back below CAC if it is still below at the last observed month (a refund booked
    in a later month, or loss-making repeat orders, can pull it back down)."""
    if cac is None:
        return "no CAC", None, None
    size = Decimal(len(cohort.customers))
    if size == 0:
        return "no customers", None, None
    running = ZERO
    paid_back: int | None = None
    lost: int | None = None
    for index in range(cohort.observable_horizon + 1):
        running += cohort.margin_by_index.get(index, ZERO)
        if paid_back is None:
            if running / size >= cac:
                paid_back = index
        elif lost is None and running / size < cac:
            lost = index
    if paid_back is None:
        return f"none thru M{cohort.observable_horizon}", None, None
    if lost is not None and running / size < cac:
        return f"M{paid_back}, lost M{lost}", paid_back, lost
    return f"M{paid_back}", paid_back, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cohort LTV, retention, and CAC payback from an order export.")
    parser.add_argument("--orders", required=True, help="path to the order export CSV")
    parser.add_argument("--as-of", required=True, help="reporting date, YYYY-MM-DD; months ending after it are unobserved")
    parser.add_argument("--horizon", type=int, default=3, help="month index to compare cohorts at (default 3)")
    parser.add_argument("--cac", type=decimal_arg, help="blended CAC per new customer, used when --spend is not given")
    parser.add_argument("--spend", help="CSV of cohort_month,marketing_spend to derive CAC per cohort")
    parser.add_argument("--reference-cohort", help="cohort key YYYY-MM to compare against (default: oldest mature cohort)")
    parser.add_argument("--tolerance-pct", type=decimal_arg, default=Decimal("10"), help="flag cohorts this far below reference (default 10)")
    parser.add_argument("--cogs-pct", type=decimal_arg, help="cost of goods as a percent of net revenue, when the export has no cogs column")
    parser.add_argument("--payment-fee-pct", type=decimal_arg, default=Decimal("2.9"), help="payment processing percent (default 2.9)")
    parser.add_argument("--payment-fee-fixed", type=decimal_arg, default=Decimal("0.30"), help="payment processing fixed fee per order (default 0.30)")
    parser.add_argument("--fulfillment-cost", type=decimal_arg, default=ZERO, help="pick, pack, and box cost per order (default 0)")
    parser.add_argument("--restock-recovery-pct", type=decimal_arg, default=ZERO, help="percent of COGS recovered on a refunded order (default 0, the conservative assumption)")
    parser.add_argument(
        "--definitions",
        help=f"finance revenue definitions JSON (default {DEFAULT_DEFINITIONS_PATH} if it exists)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if args.horizon < 0:
        fail("--horizon must be 0 or greater")
    if args.cogs_pct is not None and args.cogs_pct < 0:
        fail("--cogs-pct must be 0 or greater")

    as_of = parse_iso_date(args.as_of, where="--as-of")
    definitions = load_definitions(args.definitions, SKILL_NET_SALES)
    orders, has_cogs_column, finance = read_orders(args.orders, args, definitions)
    args.finance = finance
    spend = read_spend(args.spend) if args.spend else None

    cohorts = build_cohorts(orders, as_of)
    if not cohorts:
        fail(f"{args.orders} produced no cohorts; check order_date values")

    for cohort in cohorts.values():
        if spend is not None:
            month_spend = spend.get(cohort.key)
            cohort.cac = (month_spend / Decimal(len(cohort.customers))) if month_spend is not None and cohort.customers else None
        elif args.cac is not None:
            cohort.cac = args.cac

    horizon = args.horizon
    mature = [cohort for cohort in cohorts.values() if cohort.observable_horizon >= horizon]

    reference: Cohort | None = None
    if args.reference_cohort:
        reference = cohorts.get(args.reference_cohort)
        if reference is None:
            fail(f"--reference-cohort {args.reference_cohort} is not a cohort in {args.orders}")
        if reference.observable_horizon < horizon:
            fail(
                f"--reference-cohort {args.reference_cohort} is only observed to M{reference.observable_horizon}, "
                f"which cannot support a horizon of M{horizon}"
            )
    elif mature:
        reference = mature[0]

    max_index = max(cohort.observable_horizon for cohort in cohorts.values())
    display_horizon = min(max(horizon, max_index), 12)

    rows: list[dict[str, object]] = []
    for cohort in cohorts.values():
        size = Decimal(len(cohort.customers))
        repeat_all = pct(
            Decimal(sum(1 for count in cohort.orders_per_customer.values() if count >= 2)),
            size,
        )
        repeat_h = repeat_rate_within(cohort, orders, horizon)
        cum_rev = cumulative(cohort.revenue_by_index, horizon)[horizon]
        cum_cm = cumulative(cohort.margin_by_index, horizon)[horizon]
        label, index, lost_index = payback_month(cohort, cohort.cac)
        rows.append(
            {
                "cohort": cohort.key,
                "customers": len(cohort.customers),
                "orders": cohort.order_count,
                "aov": (cohort.gross_revenue / Decimal(cohort.order_count)) if cohort.order_count else ZERO,
                "repeatRateAllTime": repeat_all,
                "repeatRateAtHorizon": repeat_h,
                "revenuePerCustomerAtHorizon": (cum_rev / size) if size else ZERO,
                "marginPerCustomerAtHorizon": (cum_cm / size) if size else ZERO,
                "cac": cohort.cac,
                "paybackLabel": label,
                "paybackIndex": index,
                "paybackLostIndex": lost_index,
                "observableHorizon": cohort.observable_horizon,
                "mature": cohort.observable_horizon >= horizon,
            }
        )

    flags: list[dict[str, object]] = []
    if reference is not None:
        reference_row = next(row for row in rows if row["cohort"] == reference.key)
        reference_margin = reference_row["marginPerCustomerAtHorizon"]
        assert isinstance(reference_margin, Decimal)
        for row in rows:
            if row["cohort"] == reference.key or not row["mature"]:
                continue
            value = row["marginPerCustomerAtHorizon"]
            assert isinstance(value, Decimal)
            if reference_margin == 0:
                continue
            delta = pct(value - reference_margin, abs(reference_margin))
            if delta < -args.tolerance_pct:
                flags.append(
                    {
                        "cohort": row["cohort"],
                        "marginPerCustomerAtHorizon": value,
                        "referenceMarginPerCustomer": reference_margin,
                        "deltaPct": delta,
                        "repeatRateAtHorizon": row["repeatRateAtHorizon"],
                        "referenceRepeatRateAtHorizon": reference_row["repeatRateAtHorizon"],
                    }
                )

    if args.json:
        print(render_json(args, as_of, orders, cohorts, rows, flags, reference, horizon))
        return 0

    print(
        render_text(
            args, as_of, orders, cohorts, rows, flags, reference, horizon,
            display_horizon, spend is not None, has_cogs_column,
        )
    )
    return 0


def definitions_header(finance: FinanceContext) -> list[str]:
    """First lines of the report: which revenue definitions produced these numbers."""
    definitions = finance.definitions
    if definitions is None:
        return [
            f"NOTE: no finance definitions file found (looked for {DEFAULT_DEFINITIONS_PATH}).",
            "These figures use this skill's own defaults, not finance's definitions:",
            "  timezone: none; order_date is read as a plain date (its first 10 characters), no conversion",
            "  currency: one currency assumed; nothing is converted",
            "  revenue: net_revenue as supplied (after discounts, excluding tax and shipping) minus refund_amount",
            "  refunds: counted in the month of the original order, not the month they were issued",
            "  excluded orders: none removed or reported; the export must already exclude cancelled and test orders",
            "Add finance/revenue_definitions.json or pass --definitions <path> to use finance's definitions.",
        ]
    net_sales = definitions.net_sales
    lines = ["FINANCE DEFINITIONS"]
    lines.append(
        f"  file: {definitions.path}   version: {definitions.version}"
        + (f"   owner: {definitions.owner}" if definitions.owner else "")
    )
    if definitions.tz is not None:
        lines.append(
            f"  timezone: {definitions.timezone_name}; timestamps with a UTC offset are converted before an order "
            "or refund is given a month; plain dates are taken as already local"
        )
    else:
        lines.append("  timezone: not set; order_date read as a plain date (skill default)")
    if definitions.currency is None:
        lines.append("  currency: not set; one currency assumed, nothing converted (skill default)")
    elif not finance.has_currency_column:
        lines.append(f"  currency: {definitions.currency}; the export has no currency column, so amounts are taken as {definitions.currency}")
    elif finance.converted_currencies:
        source = f" ({definitions.fx_source})" if definitions.fx_source else ""
        lines.append(
            f"  currency: {definitions.currency}; {', '.join(finance.converted_currencies)} converted with "
            f"{definitions.rates_file}{source} at the order date's rate, refunds included"
        )
    else:
        lines.append(f"  currency: {definitions.currency}; every order is already in {definitions.currency}")
    revenue = "net_revenue + discount" if not net_sales["subtract_discounts"] else "net_revenue (after discounts)"
    if net_sales["subtract_refunds"]:
        revenue += " - refunds"
    if net_sales["include_shipping_charged"]:
        revenue += " + shipping charged"
    lines.append(f"  net sales (revenue, rev/cust, AOV): {revenue}")
    if not net_sales["subtract_refunds"]:
        lines.append("    refunds are not subtracted from revenue; they still reduce contribution margin")
    if net_sales["include_tax"]:
        lines.append("    tax: NOT APPLIED. Finance includes tax, but net_revenue excludes it and this skill has no tax column")
    else:
        lines.append("    tax: excluded (net_revenue excludes tax)")
    lines.append(
        "    gift card sales: cannot be identified by this skill; net_revenue must already "
        + ("include" if net_sales["include_gift_card_sales"] else "exclude")
        + " them, as finance's definitions require"
    )
    lines.append("    contribution margin is unchanged by these flags: it is always the cash the order left behind")
    if definitions.refund_basis == "refund_date":
        lines.append(
            "  refunds: refund_date basis; a refund lands in the month it was issued "
            f"({finance.moved_refunds} landed in a later month than their order)"
        )
    else:
        lines.append("  refunds: order_date basis; a refund lands in the month of the original order")
    if definitions.sale_date_basis:
        lines.append(f"  sale date: order_date column, which must be the {definitions.sale_date_basis} date")
    defaults = [f"net_sales.{flag}" for flag in NET_SALES_FLAGS if flag not in definitions.net_sales_set]
    if not definitions.refund_basis_set:
        defaults.append("refunds.date_basis")
    if not definitions.excluded_set:
        defaults.append("excluded_orders")
    if defaults:
        lines.append(f"  not set by finance, skill default used: {', '.join(defaults)}")
    if definitions.unused_keys:
        lines.append(f"  not used by this skill: {', '.join(definitions.unused_keys)}")
    return lines


def definitions_json(finance: FinanceContext) -> dict[str, object]:
    definitions = finance.definitions
    if definitions is None:
        return {
            "found": False,
            "lookedFor": DEFAULT_DEFINITIONS_PATH,
            "note": "no finance definitions file found; skill defaults used",
            "defaults": {
                "timezone": None,
                "currency": None,
                "netSales": dict(SKILL_NET_SALES),
                "refundDateBasis": "order_date",
                "excludedOrders": None,
            },
        }
    return {
        "found": True,
        "path": definitions.path,
        "version": definitions.version,
        "owner": definitions.owner,
        "timezone": definitions.timezone_name,
        "currency": definitions.currency,
        "convertedCurrencies": finance.converted_currencies,
        "netSales": definitions.net_sales,
        "refundDateBasis": definitions.refund_basis,
        "refundsMovedToLaterMonth": finance.moved_refunds,
        "exclusionRulesNotChecked": finance.exclusions.unchecked,
        "unusedKeys": definitions.unused_keys,
    }


def assumption_lines(args: argparse.Namespace, has_cogs_column: bool) -> list[str]:
    lines = [
        "  contribution margin = net_revenue + shipping_charged - refund_amount - COGS",
        "                        + recovered COGS - shipping_cost - payment_fee - fulfillment_cost",
    ]
    if has_cogs_column:
        lines.append("  COGS: from the cogs column on each order")
    else:
        lines.append(f"  COGS: {args.cogs_pct}% of net revenue (no cogs column in the export)")
    lines.append(
        f"  payment fee: {args.payment_fee_pct}% + {args.payment_fee_fixed} per order "
        "(overridden by a payment_fee column when present)"
    )
    lines.append(f"  fulfillment: {args.fulfillment_cost} per order")
    lines.append(f"  refunded COGS recovered: {args.restock_recovery_pct}%")
    lines.append("  Marketing spend is NOT deducted from contribution margin; it is compared as CAC.")
    return lines


def render_text(
    args: argparse.Namespace,
    as_of: date,
    orders: list[Order],
    cohorts: "OrderedDict[str, Cohort]",
    rows: list[dict[str, object]],
    flags: list[dict[str, object]],
    reference: Cohort | None,
    horizon: int,
    display_horizon: int,
    spend_from_file: bool,
    has_cogs_column: bool,
) -> str:
    lines: list[str] = []
    customers = sum(len(cohort.customers) for cohort in cohorts.values())
    first_date = min(order.order_date for order in orders)
    last_date = max(order.order_date for order in orders)

    finance: FinanceContext = args.finance
    lines.extend(definitions_header(finance))
    lines.append("")
    lines.append("COHORT LTV AND CAC PAYBACK")
    lines.append(
        f"orders: {len(orders)}   customers: {customers}   cohorts: {len(cohorts)}   "
        f"order dates: {first_date.isoformat()} to {last_date.isoformat()}   as of: {as_of.isoformat()}"
    )
    lines.append("=" * 96)
    lines.append("")
    lines.append("ASSUMPTIONS")
    lines.extend(assumption_lines(args, has_cogs_column))
    lines.append(
        "  CAC source: " + ("per-cohort marketing spend file" if spend_from_file else ("blended --cac" if args.cac is not None else "none supplied"))
    )
    lines.append("")
    if finance.definitions is not None:
        lines.extend(render_exclusions(finance.exclusions, finance.definitions, "net_revenue in reporting currency"))
        lines.append("")

    lines.append(f"COHORT SUMMARY (horizon M{horizon})")
    lines.append(
        f"  {'cohort':<9}{'custs':>6}{'orders':>8}{'obs':>5}{'AOV':>10}{'repeat%':>9}{'rev/cust':>11}"
        f"{'CM/cust':>10}{'CAC':>9}{'payback':>15}"
    )
    for row in rows:
        cac = row["cac"]
        cac_text = money(cac) if isinstance(cac, Decimal) else "-"
        marker = "" if row["mature"] else "  (immature)"
        lines.append(
            f"  {row['cohort']:<9}{row['customers']:>6}{row['orders']:>8}"
            f"{('M' + str(row['observableHorizon'])):>5}"
            f"{money(row['aov']):>10}"
            f"{float(row['repeatRateAtHorizon']):>8.1f}%"
            f"{money(row['revenuePerCustomerAtHorizon']):>11}"
            f"{money(row['marginPerCustomerAtHorizon']):>10}"
            f"{cac_text:>9}{row['paybackLabel']:>15}{marker}"
        )
    lines.append(
        f"  obs = last fully elapsed month for that cohort. repeat% and per-customer figures are"
        f" cumulative through M{horizon},"
    )
    lines.append(
        "  or through obs when a cohort is younger than the horizon; those rows are marked immature"
    )
    lines.append("  and are excluded from the reference comparison below.")
    lines.append("")

    lines.extend(render_triangle("CUMULATIVE REVENUE PER CUSTOMER", cohorts, "revenue", display_horizon))
    lines.append("")
    lines.extend(render_triangle("CUMULATIVE CONTRIBUTION MARGIN PER CUSTOMER", cohorts, "margin", display_horizon))
    lines.append("")

    lines.append("PAYBACK AGAINST CAC")
    if reference is None:
        lines.append("  No mature cohort at this horizon; widen the export or lower --horizon.")
    any_cac = any(isinstance(row["cac"], Decimal) for row in rows)
    if not any_cac:
        lines.append("  No CAC supplied. Pass --cac or --spend to get payback months.")
    else:
        for row in rows:
            cac = row["cac"]
            if not isinstance(cac, Decimal):
                lines.append(f"  {row['cohort']}: no marketing spend for this month")
                continue
            margin = row["marginPerCustomerAtHorizon"]
            assert isinstance(margin, Decimal)
            gap = margin - cac
            index = row["paybackIndex"]
            if index is None:
                state = f"not paid back through M{row['observableHorizon']}"
            elif row["paybackLostIndex"] is not None:
                state = (
                    f"paid back at M{index}, fell back below CAC at M{row['paybackLostIndex']} "
                    f"and still below at M{row['observableHorizon']}"
                )
            else:
                state = f"paid back at {row['paybackLabel']}"
            lines.append(
                f"  {row['cohort']}: CM/cust at M{horizon} {money(margin)} vs CAC {money(cac)} "
                f"-> {money(gap)} ({state})"
            )
    lines.append("")

    lines.append("COHORTS TRENDING BELOW REFERENCE")
    if reference is None:
        lines.append("  Skipped: no reference cohort.")
    else:
        lines.append(
            f"  reference cohort {reference.key}: CM/cust at M{horizon} "
            f"{money(next(row['marginPerCustomerAtHorizon'] for row in rows if row['cohort'] == reference.key))}, "
            f"repeat {float(next(row['repeatRateAtHorizon'] for row in rows if row['cohort'] == reference.key)):.1f}%"
        )
        if not flags:
            lines.append(f"  No mature cohort is more than {args.tolerance_pct}% below the reference.")
        for flag in flags:
            lines.append(
                f"  [BELOW] {flag['cohort']}: CM/cust {money(flag['marginPerCustomerAtHorizon'])} "
                f"vs {money(flag['referenceMarginPerCustomer'])} ({float(flag['deltaPct']):+.1f}%), "
                f"repeat {float(flag['repeatRateAtHorizon']):.1f}% vs {float(flag['referenceRepeatRateAtHorizon']):.1f}%"
            )
    lines.append("")
    lines.append("=" * 96)
    lines.append(
        "Cohorts are assigned by first order month and never move. Figures at a given month index "
        "are only comparable between cohorts that have fully lived through that month."
    )
    return "\n".join(lines)


def render_json(
    args: argparse.Namespace,
    as_of: date,
    orders: list[Order],
    cohorts: "OrderedDict[str, Cohort]",
    rows: list[dict[str, object]],
    flags: list[dict[str, object]],
    reference: Cohort | None,
    horizon: int,
) -> str:
    def dec(value: object) -> object:
        if isinstance(value, Decimal):
            return str(value.quantize(CENTS, rounding=ROUND_HALF_UP))
        return value

    finance: FinanceContext = args.finance
    payload = {
        "definitions": definitions_json(finance),
        "excludedOrders": exclusions_json(finance.exclusions) if finance.definitions else None,
        "asOf": as_of.isoformat(),
        "horizon": horizon,
        "orderCount": len(orders),
        "customerCount": sum(len(cohort.customers) for cohort in cohorts.values()),
        "referenceCohort": reference.key if reference else None,
        "cohorts": [{key: dec(value) for key, value in row.items()} for row in rows],
        "triangles": {
            "revenuePerCustomer": {
                cohort.key: [
                    dec(value / Decimal(len(cohort.customers)))
                    for value in cumulative(cohort.revenue_by_index, cohort.observable_horizon)
                ]
                for cohort in cohorts.values()
            },
            "marginPerCustomer": {
                cohort.key: [
                    dec(value / Decimal(len(cohort.customers)))
                    for value in cumulative(cohort.margin_by_index, cohort.observable_horizon)
                ]
                for cohort in cohorts.values()
            },
        },
        "flags": [{key: dec(value) for key, value in flag.items()} for flag in flags],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


if __name__ == "__main__":
    sys.exit(main())
