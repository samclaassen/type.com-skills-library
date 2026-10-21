#!/usr/bin/env python3
"""True contribution margin of a discount or promotion, and the breakeven discount depth per product.

Reads promotion orders, their line items, and a product cost file, then reports
per promo code: revenue at full price, discount given, COGS, net shipping,
payment fees, refunds already taken, expected further returns, and the
contribution margin left over. Then solves, per SKU, the deepest discount that
still clears a target unit margin, and lists the SKUs that should be carved out
of the next sitewide sale.

Stdlib only, offline, deterministic. "Today" is never read; every figure comes
from the data and the stated assumptions.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CENTS = Decimal("0.01")
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

ORDER_REQUIRED = ["order_id", "order_date", "promo_code", "gross_revenue", "discount_amount"]
LINE_REQUIRED = ["order_id", "sku", "units", "gross_line_revenue"]
PRODUCT_REQUIRED = ["sku", "unit_cost", "list_price"]
# This skill's own revenue definition, used when no finance definitions file is
# found: gross_revenue is product sales at full price, excluding tax and
# shipping; discounts and refunds are shown as separate deductions.
SKILL_NET_SALES = {
    "subtract_discounts": True,
    "subtract_refunds": True,
    "include_shipping_charged": False,
    "include_tax": False,
    "include_gift_card_sales": False,
}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", "").replace("$", "").replace("%", ""))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")


def parse_decimal(value: str, *, where: str, allow_blank: bool = True, default: Decimal = ZERO) -> Decimal:
    text = (value or "").strip().replace(",", "").replace("$", "")
    if text == "":
        if allow_blank:
            return default
        fail(f"{where}: value is blank but required")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        fail(f"{where}: {value!r} is not a number")
        raise


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def bridge_cents(bucket: "Bucket") -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Booked -> expected in whole cents that add up exactly.

    Each running subtotal is rounded, and each step is the difference of two
    rounded subtotals, so the row reconciles to the cent and both ends match
    the booked and expected margins printed everywhere else."""
    booked = cents(bucket.booked_margin)
    after_refunds = cents(bucket.booked_margin - bucket.expected_return_revenue)
    after_handling = cents(bucket.booked_margin - bucket.expected_return_revenue - bucket.future_return_shipping)
    expected = cents(bucket.expected_margin)
    return booked, booked - after_refunds, after_refunds - after_handling, expected - after_handling, expected


def money(value: Decimal) -> str:
    return f"{value.quantize(CENTS, rounding=ROUND_HALF_UP):,}"


def percent(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def share(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return ZERO
    return numerator / denominator


def parse_iso_date(value: str, *, where: str) -> date:
    text = value.strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        fail(f"{where}: {value!r} is not a YYYY-MM-DD date")
        raise


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
class Product:
    sku: str
    product_name: str
    unit_cost: Decimal
    list_price: Decimal
    return_rate: Decimal  # fraction, not percent


@dataclass
class Order:
    order_id: str
    order_date: str
    promo_code: str
    gross_revenue: Decimal
    discount_amount: Decimal
    shipping_charged: Decimal
    shipping_cost: Decimal
    payment_fee: Decimal | None
    refund_amount: Decimal
    line_gross_total: Decimal = ZERO
    fx_rate: Decimal = Decimal(1)


@dataclass
class Bucket:
    key: str
    orders: set[str] = field(default_factory=set)
    units: Decimal = ZERO
    gross_revenue: Decimal = ZERO
    discount: Decimal = ZERO
    cogs: Decimal = ZERO
    shipping_charged: Decimal = ZERO
    shipping_cost: Decimal = ZERO
    payment_fee: Decimal = ZERO
    # Returns already processed: refunded revenue, the stock recovered from
    # those units, and the handling they cost. These are in booked margin.
    refunds: Decimal = ZERO
    cogs_recovered: Decimal = ZERO
    return_shipping: Decimal = ZERO
    # Returns still to come, at the SKU return rate on the unrefunded share.
    # Only expected margin carries these.
    expected_return_revenue: Decimal = ZERO
    future_return_shipping: Decimal = ZERO
    future_cogs_recovered: Decimal = ZERO

    @property
    def net_revenue(self) -> Decimal:
        return self.gross_revenue - self.discount

    @property
    def booked_margin(self) -> Decimal:
        return (
            self.net_revenue
            - self.refunds
            + self.shipping_charged
            - self.shipping_cost
            - self.payment_fee
            - self.cogs
            + self.cogs_recovered
            - self.return_shipping
        )

    @property
    def expected_margin(self) -> Decimal:
        return (
            self.booked_margin
            - self.expected_return_revenue
            - self.future_return_shipping
            + self.future_cogs_recovered
        )

    @property
    def all_cogs_recovered(self) -> Decimal:
        return self.cogs_recovered + self.future_cogs_recovered

    @property
    def all_returns_cost(self) -> Decimal:
        """Refunds taken and to come plus all return handling (the P&L 'returns' column)."""
        return self.refunds + self.expected_return_revenue + self.return_shipping + self.future_return_shipping


def read_csv(path: str, required: list[str], label: str) -> list[dict[str, str]]:
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open {label} file {path}: {exc.strerror}")
        raise
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{path} is empty: expected a header row with {', '.join(required)}")
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            fail(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Required columns are: {', '.join(required)}"
            )
        rows = [
            {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
            for raw in reader
        ]
    if not rows:
        fail(f"{path} has a header but no {label} rows")
    return rows


def load_products(path: str, default_return_rate: Decimal) -> "OrderedDict[str, Product]":
    rows = read_csv(path, PRODUCT_REQUIRED, "product")
    products: "OrderedDict[str, Product]" = OrderedDict()
    for row_number, row in enumerate(rows, start=2):
        where = f"{path} row {row_number}"
        sku = row.get("sku", "")
        if not sku:
            fail(f"{where}: sku is empty")
        if sku in products:
            fail(f"{where}: sku {sku!r} appears more than once")
        unit_cost = parse_decimal(row.get("unit_cost", ""), where=f"{where} column unit_cost", allow_blank=False)
        list_price = parse_decimal(row.get("list_price", ""), where=f"{where} column list_price", allow_blank=False)
        if unit_cost < 0:
            fail(f"{where} column unit_cost: {unit_cost} is negative")
        if list_price <= 0:
            fail(f"{where} column list_price: {list_price} must be greater than zero")
        raw_rate = row.get("return_rate_pct", "")
        rate = (
            parse_decimal(raw_rate, where=f"{where} column return_rate_pct") / HUNDRED
            if raw_rate
            else default_return_rate
        )
        if not (ZERO <= rate < ONE):
            fail(f"{where} column return_rate_pct: {raw_rate!r} must be at least 0 and below 100")
        products[sku] = Product(
            sku=sku,
            product_name=row.get("product_name", "") or sku,
            unit_cost=unit_cost,
            list_price=list_price,
            return_rate=rate,
        )
    return products


@dataclass
class FinanceContext:
    """What the finance definitions did to this run, for the report."""

    definitions: Definitions | None
    exclusions: Exclusions
    skipped_lines: int = 0
    converted_currencies: list[str] = field(default_factory=list)
    has_currency_column: bool = False


def load_orders(
    path: str, args: argparse.Namespace, definitions: Definitions | None
) -> tuple["OrderedDict[str, Order]", FinanceContext]:
    rows = read_csv(path, ORDER_REQUIRED, "order")
    header = set(rows[0].keys())
    has_currency = "currency" in header
    fx = FxRates(definitions) if definitions else None
    exclusions = Exclusions()
    if definitions:
        exclusions.unchecked = unchecked_rules(definitions, header)
    currencies_seen: set[str] = set()
    seen_ids: set[str] = set()
    orders: "OrderedDict[str, Order]" = OrderedDict()
    for row_number, row in enumerate(rows, start=2):
        where = f"{path} row {row_number}"
        order_id = row.get("order_id", "")
        if not order_id:
            fail(f"{where}: order_id is empty")
        if order_id in seen_ids:
            fail(f"{where}: order_id {order_id!r} appears more than once")
        seen_ids.add(order_id)
        promo_code = row.get("promo_code", "") or args.no_promo_label
        gross = parse_decimal(row.get("gross_revenue", ""), where=f"{where} column gross_revenue", allow_blank=False)
        if gross < 0:
            fail(f"{where} column gross_revenue: {gross} is negative")
        discount = parse_decimal(row.get("discount_amount", ""), where=f"{where} column discount_amount", allow_blank=False)
        if discount < 0:
            fail(f"{where} column discount_amount: {discount} is negative; record discounts as positive amounts")
        if discount > gross:
            fail(f"{where}: discount_amount {discount} exceeds gross_revenue {gross} for order {order_id}")
        refund = parse_decimal(row.get("refund_amount", ""), where=f"{where} column refund_amount")
        if refund < 0:
            fail(f"{where} column refund_amount: {refund} is negative; record refunds as positive amounts")
        fee_raw = row.get("payment_fee", "")
        shipping_charged = parse_decimal(row.get("shipping_charged", ""), where=f"{where} column shipping_charged")
        shipping_cost = parse_decimal(row.get("shipping_cost", ""), where=f"{where} column shipping_cost")
        payment_fee = parse_decimal(fee_raw, where=f"{where} column payment_fee") if fee_raw else None

        rate = Decimal(1)
        if definitions is not None and fx is not None:
            # The only date this skill needs: the order's reporting-timezone
            # day, to pick a daily FX rate. Nothing is bucketed by date.
            local_date = parse_local_date(row.get("order_date", ""), where=f"{where} column order_date", tz=definitions.tz)
            currency = row_currency(row, has_currency, definitions, where=where)
            if currency is not None:
                rate = fx.rate(currency, local_date, where=f"{where} (order {order_id}, {currency})")
            elif has_currency and row.get("currency", ""):
                currencies_seen.add(row.get("currency", "").upper())
                if len(currencies_seen) > 1:
                    fail(
                        f"{where}: currency {row.get('currency', '')!r} differs from earlier rows and the "
                        "definitions set no reporting_currency to convert to"
                    )
        if rate != 1:
            gross *= rate
            discount *= rate
            refund *= rate
            shipping_charged *= rate
            shipping_cost *= rate
            if payment_fee is not None:
                payment_fee *= rate

        if definitions is not None:
            rule = exclusion_rule(row, header, definitions, gross - discount + shipping_charged, where=where)
            if rule is not None:
                exclusions.add(rule, order_id, gross - discount)
                continue

        orders[order_id] = Order(
            order_id=order_id,
            order_date=row.get("order_date", ""),
            promo_code=promo_code,
            gross_revenue=gross,
            discount_amount=discount,
            shipping_charged=shipping_charged,
            shipping_cost=shipping_cost,
            payment_fee=payment_fee,
            refund_amount=refund,
            fx_rate=rate,
        )
    if not orders:
        fail(f"{path}: every order was excluded by the finance excluded_orders rules")
    context = FinanceContext(
        definitions=definitions,
        exclusions=exclusions,
        converted_currencies=sorted(fx.used) if fx else [],
        has_currency_column=has_currency,
    )
    return orders, context


@dataclass
class Line:
    order_id: str
    sku: str
    units: Decimal
    gross_line_revenue: Decimal
    line_discount: Decimal | None


def load_lines(
    path: str,
    orders: "OrderedDict[str, Order]",
    products: "OrderedDict[str, Product]",
    finance: FinanceContext,
) -> list[Line]:
    rows = read_csv(path, LINE_REQUIRED, "line item")
    lines: list[Line] = []
    for row_number, row in enumerate(rows, start=2):
        where = f"{path} row {row_number}"
        order_id = row.get("order_id", "")
        if order_id in finance.exclusions.ids:
            finance.skipped_lines += 1
            continue
        if order_id not in orders:
            fail(f"{where}: order_id {order_id!r} is not in the orders file")
        sku = row.get("sku", "")
        if sku not in products:
            fail(f"{where}: sku {sku!r} is not in the products file")
        units = parse_decimal(row.get("units", ""), where=f"{where} column units", allow_blank=False)
        if units <= 0:
            fail(f"{where} column units: {units} must be greater than zero")
        gross = parse_decimal(row.get("gross_line_revenue", ""), where=f"{where} column gross_line_revenue", allow_blank=False)
        if gross < 0:
            fail(f"{where} column gross_line_revenue: {gross} is negative")
        raw_discount = row.get("line_discount", "")
        line_discount = parse_decimal(raw_discount, where=f"{where} column line_discount") if raw_discount else None
        rate = orders[order_id].fx_rate
        if rate != 1:
            gross *= rate
            if line_discount is not None:
                line_discount *= rate
        lines.append(Line(order_id=order_id, sku=sku, units=units, gross_line_revenue=gross, line_discount=line_discount))
        orders[order_id].line_gross_total += gross
    orphans = [order_id for order_id, order in orders.items() if order.line_gross_total == 0]
    if orphans:
        fail(
            f"{path}: no line items for order(s) {', '.join(orphans[:5])}"
            + (f" and {len(orphans) - 5} more" if len(orphans) > 5 else "")
        )
    return lines


@dataclass
class SkuStats:
    sku: str
    bucket: Bucket
    shipping_net_per_unit: Decimal = ZERO
    observed_discount_pct: Decimal = ZERO
    breakeven_discount_pct: Decimal | None = None
    breakeven_note: str = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="True margin of a promotion and breakeven discount depth per SKU.")
    parser.add_argument("--orders", required=True, help="promotion orders CSV")
    parser.add_argument("--lines", required=True, help="promotion order line items CSV")
    parser.add_argument("--products", required=True, help="product cost and price CSV")
    parser.add_argument("--payment-fee-pct", type=decimal_arg, default=Decimal("2.9"), help="payment processing percent (default 2.9)")
    parser.add_argument("--payment-fee-fixed", type=decimal_arg, default=Decimal("0.30"), help="payment processing fixed fee per order (default 0.30)")
    parser.add_argument("--fulfillment-cost", type=decimal_arg, default=ZERO, help="pick, pack, and box cost per order (default 0)")
    parser.add_argument("--return-rate-pct", type=decimal_arg, default=Decimal("0"), help="expected return rate used when a product has no return_rate_pct (default 0)")
    parser.add_argument("--return-shipping-cost", type=decimal_arg, default=ZERO, help="cost of handling one returned unit (default 0)")
    parser.add_argument("--restock-recovery-pct", type=decimal_arg, default=ZERO, help="percent of unit cost recovered when a unit comes back sellable (default 0)")
    parser.add_argument("--planned-discount-pct", type=decimal_arg, default=Decimal("20"), help="depth of the next sitewide sale to test against (default 20)")
    parser.add_argument("--min-unit-margin", type=decimal_arg, default=ZERO, help="unit contribution margin the breakeven solve must clear (default 0)")
    parser.add_argument("--no-promo-label", default="(no code)", help="bucket label for orders with an empty promo_code")
    parser.add_argument("--top", type=int, default=12, help="rows to show in the per-SKU tables (default 12)")
    parser.add_argument(
        "--definitions",
        help=f"finance revenue definitions JSON (default {DEFAULT_DEFINITIONS_PATH} if it exists)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if not (ZERO <= args.return_rate_pct < HUNDRED):
        fail("--return-rate-pct must be at least 0 and below 100")
    if not (ZERO <= args.restock_recovery_pct <= HUNDRED):
        fail("--restock-recovery-pct must be between 0 and 100")
    if not (ZERO <= args.planned_discount_pct < HUNDRED):
        fail("--planned-discount-pct must be at least 0 and below 100")
    if args.payment_fee_pct < 0 or args.payment_fee_pct >= HUNDRED:
        fail("--payment-fee-pct must be at least 0 and below 100")
    if args.top < 1:
        fail("--top must be at least 1")

    fee_rate = args.payment_fee_pct / HUNDRED
    recovery = args.restock_recovery_pct / HUNDRED

    definitions = load_definitions(args.definitions, SKILL_NET_SALES)
    products = load_products(args.products, args.return_rate_pct / HUNDRED)
    orders, finance = load_orders(args.orders, args, definitions)
    lines = load_lines(args.lines, orders, products, finance)
    args.finance = finance

    promos: "OrderedDict[str, Bucket]" = OrderedDict()
    skus: "OrderedDict[str, SkuStats]" = OrderedDict()
    sku_shipping_net: dict[str, Decimal] = {}
    mismatches: list[str] = []

    for order in orders.values():
        gap = order.line_gross_total - order.gross_revenue
        if order.gross_revenue > 0 and abs(gap) / order.gross_revenue > Decimal("0.01"):
            mismatches.append(
                f"{order.order_id}: lines total {money(order.line_gross_total)} vs order gross {money(order.gross_revenue)}"
            )

    for line in lines:
        order = orders[line.order_id]
        product = products[line.sku]
        weight = share(line.gross_line_revenue, order.line_gross_total)

        discount = line.line_discount if line.line_discount is not None else order.discount_amount * weight
        net_revenue = line.gross_line_revenue - discount
        shipping_charged = order.shipping_charged * weight
        shipping_cost = (order.shipping_cost + args.fulfillment_cost) * weight
        if order.payment_fee is not None:
            payment_fee = order.payment_fee * weight
        else:
            collected = (order.gross_revenue - order.discount_amount) + order.shipping_charged
            payment_fee = (collected * fee_rate + args.payment_fee_fixed) * weight
        refund = order.refund_amount * weight
        cogs = line.units * product.unit_cost

        refunded_share = min(share(refund, net_revenue), ONE) if net_revenue > 0 else ZERO
        expected_share = product.return_rate * (ONE - refunded_share)
        expected_return_revenue = net_revenue * expected_share
        cogs_recovered = cogs * refunded_share * recovery
        return_shipping = line.units * refunded_share * args.return_shipping_cost
        future_cogs_recovered = cogs * expected_share * recovery
        future_return_shipping = line.units * expected_share * args.return_shipping_cost

        bucket = promos.get(order.promo_code)
        if bucket is None:
            bucket = Bucket(key=order.promo_code)
            promos[order.promo_code] = bucket
        stats = skus.get(line.sku)
        if stats is None:
            stats = SkuStats(sku=line.sku, bucket=Bucket(key=line.sku))
            skus[line.sku] = stats

        for target in (bucket, stats.bucket):
            target.orders.add(order.order_id)
            target.units += line.units
            target.gross_revenue += line.gross_line_revenue
            target.discount += discount
            target.cogs += cogs
            target.shipping_charged += shipping_charged
            target.shipping_cost += shipping_cost
            target.payment_fee += payment_fee
            target.refunds += refund
            target.expected_return_revenue += expected_return_revenue
            target.cogs_recovered += cogs_recovered
            target.return_shipping += return_shipping
            target.future_cogs_recovered += future_cogs_recovered
            target.future_return_shipping += future_return_shipping

        sku_shipping_net[line.sku] = sku_shipping_net.get(line.sku, ZERO) + (shipping_cost - shipping_charged)

    # Breakeven discount depth per SKU.
    for sku, stats in skus.items():
        product = products[sku]
        units = stats.bucket.units
        stats.shipping_net_per_unit = share(sku_shipping_net.get(sku, ZERO), units)
        stats.observed_discount_pct = share(stats.bucket.discount, stats.bucket.gross_revenue) * HUNDRED

        rate = product.return_rate
        revenue_factor = (ONE - rate) - fee_rate
        cost_side = (
            args.min_unit_margin
            + product.unit_cost * (ONE - rate * recovery)
            + stats.shipping_net_per_unit
            + rate * args.return_shipping_cost
        )
        if revenue_factor <= 0:
            stats.breakeven_note = "return rate plus fees exceed revenue; no discount depth is profitable"
            stats.breakeven_discount_pct = None
        else:
            depth = (ONE - cost_side / (product.list_price * revenue_factor)) * HUNDRED
            stats.breakeven_discount_pct = depth
            if depth <= 0:
                stats.breakeven_note = "loses money at full price before any discount"

    if args.json:
        print(render_json(args, promos, skus, products, mismatches))
        return 0
    print(render_text(args, promos, skus, products, mismatches, fee_rate))
    return 0


def margin_pct(bucket: Bucket) -> Decimal:
    return share(bucket.expected_margin, bucket.net_revenue) * HUNDRED


def render_text(
    args: argparse.Namespace,
    promos: "OrderedDict[str, Bucket]",
    skus: "OrderedDict[str, SkuStats]",
    products: "OrderedDict[str, Product]",
    mismatches: list[str],
    fee_rate: Decimal,
) -> str:
    lines: list[str] = []
    total = Bucket(key="ALL PROMOTIONS")
    for bucket in promos.values():
        total.orders |= bucket.orders
        total.units += bucket.units
        total.gross_revenue += bucket.gross_revenue
        total.discount += bucket.discount
        total.cogs += bucket.cogs
        total.shipping_charged += bucket.shipping_charged
        total.shipping_cost += bucket.shipping_cost
        total.payment_fee += bucket.payment_fee
        total.refunds += bucket.refunds
        total.expected_return_revenue += bucket.expected_return_revenue
        total.cogs_recovered += bucket.cogs_recovered
        total.return_shipping += bucket.return_shipping
        total.future_cogs_recovered += bucket.future_cogs_recovered
        total.future_return_shipping += bucket.future_return_shipping

    finance: FinanceContext = args.finance
    lines.extend(definitions_header(finance))
    lines.append("")
    lines.append("PROMOTION MARGIN GUARD")
    lines.append(
        f"promo codes: {len(promos)}   orders: {len(total.orders)}   units: {total.units.quantize(Decimal('1'))}   "
        f"skus: {len(skus)}"
    )
    lines.append("=" * 104)
    lines.append("")

    lines.append("ASSUMPTIONS")
    lines.append("  contribution margin = net revenue - refunds - expected further returns + shipping charged")
    lines.append("                        - shipping cost - fulfillment - payment fees - COGS + recovered COGS")
    lines.append("                        - return handling")
    lines.append(f"  payment fee: {args.payment_fee_pct}% of the amount captured + {args.payment_fee_fixed} per order, allocated to lines by gross revenue")
    lines.append("  payment fees are NOT refunded when an order is returned, which is how most processors work")
    lines.append(f"  fulfillment: {args.fulfillment_cost} per order")
    lines.append(f"  return rate: per-SKU return_rate_pct where present, otherwise {args.return_rate_pct}%")
    lines.append(f"  return handling: {args.return_shipping_cost} per returned unit; {args.restock_recovery_pct}% of unit cost recovered")
    lines.append("  'booked' counts only returns already processed: their refunds, handling, and recovered stock")
    lines.append("  'expected' = booked - refunds still to come - their handling + stock they will recover")
    lines.append("")
    if finance.definitions is not None:
        lines.extend(render_exclusions(finance.exclusions, finance.definitions, "net revenue after discount"))
        if finance.skipped_lines:
            lines.append(f"  {finance.skipped_lines} line item(s) belonging to excluded orders were skipped.")
        lines.append("")

    lines.append("PROMOTION P&L (expected, after returns still to come)")
    header = (
        f"  {'promo':<14}{'orders':>7}{'units':>7}{'full price':>12}{'discount':>11}{'net rev':>11}"
        f"{'COGS':>11}{'ship net':>10}{'fees':>9}{'returns':>11}{'margin':>11}{'margin%':>9}"
    )
    lines.append(header)
    ordered = sorted(promos.values(), key=lambda bucket: (bucket.expected_margin, bucket.key))
    for bucket in ordered:
        lines.append(
            f"  {bucket.key:<14}{len(bucket.orders):>7}{bucket.units.quantize(Decimal('1')):>7}"
            f"{money(bucket.gross_revenue):>12}{money(bucket.discount):>11}{money(bucket.net_revenue):>11}"
            f"{money(bucket.cogs - bucket.all_cogs_recovered):>11}"
            f"{money(bucket.shipping_cost - bucket.shipping_charged):>10}{money(bucket.payment_fee):>9}"
            f"{money(bucket.all_returns_cost):>11}"
            f"{money(bucket.expected_margin):>11}{percent(margin_pct(bucket)):>9}"
        )
    lines.append(
        f"  {'TOTAL':<14}{len(total.orders):>7}{total.units.quantize(Decimal('1')):>7}"
        f"{money(total.gross_revenue):>12}{money(total.discount):>11}{money(total.net_revenue):>11}"
        f"{money(total.cogs - total.all_cogs_recovered):>11}"
        f"{money(total.shipping_cost - total.shipping_charged):>10}{money(total.payment_fee):>9}"
        f"{money(total.all_returns_cost):>11}"
        f"{money(total.expected_margin):>11}{percent(margin_pct(total)):>9}"
    )
    lines.append("  ship net = shipping cost and fulfillment minus shipping the customer paid; negative means shipping paid for itself.")
    lines.append("")

    lines.append("BOOKED TO EXPECTED BRIDGE (returns still to come)")
    lines.append(
        f"  {'promo':<14}{'booked':>12}{'- refunds':>12}{'- handling':>12}{'+ recovered':>13}{'= expected':>12}"
    )
    for bucket in ordered + [total]:
        booked_c, refunds_c, handling_c, recovered_c, expected_c = bridge_cents(bucket)
        if booked_c - refunds_c - handling_c + recovered_c != expected_c:
            fail(f"internal error: bridge for {bucket.key} does not reconcile")
        lines.append(
            f"  {'TOTAL' if bucket is total else bucket.key:<14}{money(booked_c):>12}{money(refunds_c):>12}"
            f"{money(handling_c):>12}{money(recovered_c):>13}{money(expected_c):>12}"
        )
    lines.append("  refunds = revenue expected back from returns not yet processed; handling = their return handling;")
    lines.append("  recovered = unit cost recovered by reselling them. Each row adds up: booked - refunds - handling + recovered.")
    lines.append("  Steps are rounded to cents cumulatively, so a component can differ from its own rounding by 0.01.")
    lines.append("")

    losers = [bucket for bucket in ordered if bucket.expected_margin < 0]
    lines.append("PROMOTIONS THAT LOST MONEY")
    if not losers:
        lines.append("  Every promo code cleared its variable costs.")
    for bucket in losers:
        per_order = share(bucket.expected_margin, Decimal(len(bucket.orders)))
        booked = bucket.booked_margin
        lines.append(
            f"  [LOSS] {bucket.key}: {money(bucket.expected_margin)} across {len(bucket.orders)} orders "
            f"({money(per_order)} per order, {percent(share(bucket.discount, bucket.gross_revenue) * HUNDRED)} average discount)"
        )
        lines.append(
            f"         booked margin before the returns still to come: {money(booked)}; "
            f"returns still to come cost {money(cents(bucket.booked_margin) - cents(bucket.expected_margin))} net of recovered stock"
        )
    thin = [
        bucket
        for bucket in ordered
        if bucket.expected_margin >= 0 and margin_pct(bucket) < Decimal("10")
    ]
    for bucket in thin:
        lines.append(
            f"  [THIN] {bucket.key}: {percent(margin_pct(bucket))} contribution margin, "
            f"{money(bucket.expected_margin)} on {money(bucket.net_revenue)} of net revenue"
        )
    lines.append("")

    lines.append("BREAKEVEN DISCOUNT DEPTH BY PRODUCT")
    lines.append(
        f"  {'sku':<16}{'list':>9}{'cost':>9}{'ret%':>7}{'ship/unit':>11}{'given':>9}{'breakeven':>11}{'headroom':>10}  product"
    )
    ranked = sorted(
        skus.values(),
        key=lambda stats: (
            stats.breakeven_discount_pct if stats.breakeven_discount_pct is not None else Decimal("-999"),
            stats.sku,
        ),
    )
    for stats in ranked[: args.top]:
        product = products[stats.sku]
        if stats.breakeven_discount_pct is None:
            breakeven = "none"
            headroom = "-"
        else:
            breakeven = percent(stats.breakeven_discount_pct)
            headroom = percent(stats.breakeven_discount_pct - stats.observed_discount_pct)
        lines.append(
            f"  {stats.sku:<16}{money(product.list_price):>9}{money(product.unit_cost):>9}"
            f"{percent(product.return_rate * HUNDRED):>7}{money(stats.shipping_net_per_unit):>11}"
            f"{percent(stats.observed_discount_pct):>9}{breakeven:>11}{headroom:>10}  {product.product_name}"
        )
    if len(ranked) > args.top:
        lines.append(f"  ... {len(ranked) - args.top} more SKU(s) with deeper headroom; use --top or --json for the full list.")
    lines.append(
        f"  breakeven = the deepest discount off list price that still clears {money(args.min_unit_margin)} of unit"
    )
    lines.append(
        "  contribution, after this SKU's return rate, its observed shipping economics, and payment fees."
    )
    lines.append("  given = the average discount this SKU actually received in the data. headroom = breakeven - given.")
    lines.append("")

    planned = args.planned_discount_pct
    lines.append(f"EXCLUDE FROM THE NEXT {planned}% SITEWIDE SALE")
    excluded = [
        stats
        for stats in ranked
        if stats.breakeven_discount_pct is None or stats.breakeven_discount_pct < planned
    ]
    if not excluded:
        lines.append(f"  Every SKU in this data can carry a {planned}% discount and stay contribution positive.")
    for stats in excluded:
        product = products[stats.sku]
        if stats.breakeven_discount_pct is None:
            lines.append(f"  {stats.sku:<16} exclude entirely — {stats.breakeven_note} ({product.product_name})")
        elif stats.breakeven_discount_pct <= 0:
            lines.append(
                f"  {stats.sku:<16} exclude entirely — {stats.breakeven_note or 'no profitable discount depth'} "
                f"({product.product_name})"
            )
        else:
            lines.append(
                f"  {stats.sku:<16} cap at {percent(stats.breakeven_discount_pct)} — at {planned}% each unit "
                f"contributes {money(unit_margin_at(stats, product, args, planned))} ({product.product_name})"
            )
    close = [
        stats
        for stats in ranked
        if stats.breakeven_discount_pct is not None
        and planned <= stats.breakeven_discount_pct < planned + Decimal("5")
    ]
    for stats in close:
        product = products[stats.sku]
        lines.append(
            f"  {stats.sku:<16} thin headroom — breakeven {percent(stats.breakeven_discount_pct)} is barely above "
            f"the planned {planned}%; any stacked code or free shipping tips it negative ({product.product_name})"
        )
    lines.append("")

    lines.append("SUMMARY")
    lines.append(f"  full-price value of everything sold on promotion: {money(total.gross_revenue)}")
    lines.append(f"  discount given away:                              {money(total.discount)} ({percent(share(total.discount, total.gross_revenue) * HUNDRED)})")
    lines.append(f"  booked contribution margin:                       {money(total.booked_margin)}")
    lines.append(f"  expected contribution margin after returns:       {money(total.expected_margin)} ({percent(margin_pct(total))} of net revenue)")
    lines.append(f"  promo codes losing money:                         {len(losers)} of {len(promos)}")
    lines.append(f"  skus to exclude or cap at {planned}%:{'':<20}{len(excluded)} of {len(skus)}")
    if finance.definitions is not None:
        value, formula = finance_net_sales(total, finance.definitions.net_sales)
        lines.append(f"  net sales, finance definition v{finance.definitions.version}:{'':<9}{money(value)} ({formula})")
        if finance.definitions.refund_basis == "refund_date":
            lines.append("  refunds on refund_date basis: this P&L has no periods, so every refund is counted in full")
            lines.append("  whenever it was issued; the basis changes a period report, not a promotion's result")
    if mismatches:
        lines.append("")
        lines.append("DATA QUALITY")
        lines.append("  Order gross revenue does not match the sum of its line items by more than 1%:")
        for note in mismatches[:5]:
            lines.append(f"    {note}")
        if len(mismatches) > 5:
            lines.append(f"    +{len(mismatches) - 5} more")
        lines.append("  Line-level allocations of shipping, fees, and refunds use the line share of the order total,")
        lines.append("  so these orders distort the per-SKU figures. Fix the export if the gap is large.")
    lines.append("")
    lines.append("=" * 104)
    lines.append(
        "Contribution margin only. Ad spend, overheads, and the value of any new customer the promotion "
        "acquired are not in these numbers."
    )
    _ = fee_rate
    return "\n".join(lines)


def finance_net_sales(total: Bucket, net_sales: dict[str, bool]) -> tuple[Decimal, str]:
    """Net sales for the included orders under finance's flags, from the P&L components."""
    value = total.gross_revenue
    formula = "full price"
    if net_sales["subtract_discounts"]:
        value -= total.discount
        formula += " - discount"
    if net_sales["subtract_refunds"]:
        value -= total.refunds
        formula += " - refunds"
    if net_sales["include_shipping_charged"]:
        value += total.shipping_charged
        formula += " + shipping charged"
    return value, formula


def definitions_header(finance: FinanceContext) -> list[str]:
    """First lines of the report: which revenue definitions produced these numbers."""
    definitions = finance.definitions
    if definitions is None:
        return [
            f"NOTE: no finance definitions file found (looked for {DEFAULT_DEFINITIONS_PATH}).",
            "These figures use this skill's own defaults, not finance's definitions:",
            "  timezone: not used; order_date is carried for reference only",
            "  currency: one currency assumed; nothing is converted",
            "  revenue: gross_revenue at full price less discount_amount, excluding tax and shipping; refunds shown separately",
            "  refunds: counted in full against the promotion, whenever they were issued",
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
            f"  timezone: {definitions.timezone_name}; used only to find each order's local date for its FX rate."
        )
        lines.append("    This report has no periods, so the timezone moves no order between buckets.")
    else:
        lines.append("  timezone: not set; order_date read as a plain date for FX lookups (skill default)")
    if definitions.currency is None:
        lines.append("  currency: not set; one currency assumed, nothing converted (skill default)")
    elif not finance.has_currency_column:
        lines.append(f"  currency: {definitions.currency}; the orders file has no currency column, so amounts are taken as {definitions.currency}")
    elif finance.converted_currencies:
        source = f" ({definitions.fx_source})" if definitions.fx_source else ""
        lines.append(
            f"  currency: {definitions.currency}; {', '.join(finance.converted_currencies)} orders and their lines converted with "
            f"{definitions.rates_file}{source} at the order date's rate"
        )
    else:
        lines.append(f"  currency: {definitions.currency}; every order is already in {definitions.currency}")
    if definitions.currency is not None:
        lines.append(
            f"    product costs, list prices and the cost flags must already be in {definitions.currency}"
        )
    formula = finance_net_sales(Bucket(key=""), net_sales)[1]
    lines.append(f"  net sales: {formula}; reported in SUMMARY")
    lines.append("    the P&L columns and margin% do not change with these flags: margin% stays on net revenue after discount")
    if net_sales["include_tax"]:
        lines.append("    tax: NOT APPLIED. Finance includes tax, but gross_revenue excludes it and this skill has no tax column")
    else:
        lines.append("    tax: excluded (gross_revenue excludes tax)")
    lines.append(
        "    gift card sales: cannot be identified by this skill; the orders file must already "
        + ("include" if net_sales["include_gift_card_sales"] else "exclude")
        + " them, as finance's definitions require"
    )
    lines.append(
        f"  refunds: {definitions.refund_basis} basis; no effect on a promotion's P&L, which has no periods"
    )
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
                "refundDateBasis": "none (no periods)",
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
        "refundDateBasisNote": "a promotion P&L has no periods; the basis does not change it",
        "exclusionRulesNotChecked": finance.exclusions.unchecked,
        "skippedLineItems": finance.skipped_lines,
        "unusedKeys": definitions.unused_keys,
    }


def unit_margin_at(stats: SkuStats, product: Product, args: argparse.Namespace, discount_pct: Decimal) -> Decimal:
    """Unit contribution margin at a given discount depth, using the same model as the breakeven solve."""
    fee_rate = args.payment_fee_pct / HUNDRED
    recovery = args.restock_recovery_pct / HUNDRED
    rate = product.return_rate
    net_price = product.list_price * (ONE - discount_pct / HUNDRED)
    return (
        net_price * (ONE - rate)
        - net_price * fee_rate
        - product.unit_cost * (ONE - rate * recovery)
        - stats.shipping_net_per_unit
        - rate * args.return_shipping_cost
    )


def render_json(
    args: argparse.Namespace,
    promos: "OrderedDict[str, Bucket]",
    skus: "OrderedDict[str, SkuStats]",
    products: "OrderedDict[str, Product]",
    mismatches: list[str],
) -> str:
    def dec(value: Decimal | None, places: str = "0.01") -> str | None:
        if value is None:
            return None
        return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))

    finance: FinanceContext = args.finance
    finance_sales = None
    if finance.definitions is not None:
        total = Bucket(key="ALL PROMOTIONS")
        for bucket in promos.values():
            total.gross_revenue += bucket.gross_revenue
            total.discount += bucket.discount
            total.refunds += bucket.refunds
            total.shipping_charged += bucket.shipping_charged
        value, formula = finance_net_sales(total, finance.definitions.net_sales)
        finance_sales = {"value": dec(value), "formula": formula}
    def bridge_json(bucket: Bucket) -> dict[str, str]:
        booked_c, refunds_c, handling_c, recovered_c, expected_c = bridge_cents(bucket)
        return {
            "bookedMargin": str(booked_c),
            "lessFutureRefunds": str(refunds_c),
            "lessFutureReturnHandling": str(handling_c),
            "plusFutureStockRecovered": str(recovered_c),
            "expectedMargin": str(expected_c),
            "note": (
                "Same figures as the text bridge: steps are rounded to cents cumulatively so "
                "bookedMargin - lessFutureRefunds - lessFutureReturnHandling + plusFutureStockRecovered "
                "= expectedMargin exactly; a step can differ from rounding its own component by 0.01."
            ),
        }

    payload = {
        "definitions": definitions_json(finance),
        "excludedOrders": exclusions_json(finance.exclusions) if finance.definitions else None,
        "promotions": [
            {
                "promoCode": bucket.key,
                "orders": len(bucket.orders),
                "units": dec(bucket.units, "0.001"),
                "grossRevenueAtFullPrice": dec(bucket.gross_revenue),
                "discount": dec(bucket.discount),
                "netRevenue": dec(bucket.net_revenue),
                "cogsNetOfRecovery": dec(bucket.cogs - bucket.all_cogs_recovered),
                "shippingNet": dec(bucket.shipping_cost - bucket.shipping_charged),
                "paymentFees": dec(bucket.payment_fee),
                "refundsTaken": dec(bucket.refunds),
                "bookedReturnHandling": dec(bucket.return_shipping),
                "bookedStockRecovered": dec(bucket.cogs_recovered),
                "bookedMargin": dec(bucket.booked_margin),
                "expectedFutureRefunds": dec(bucket.expected_return_revenue),
                "expectedFutureReturnHandling": dec(bucket.future_return_shipping),
                "expectedFutureStockRecovered": dec(bucket.future_cogs_recovered),
                "expectedMargin": dec(bucket.expected_margin),
                "expectedMarginPct": dec(margin_pct(bucket), "0.1"),
                "bookedToExpectedBridge": bridge_json(bucket),
            }
            for bucket in sorted(promos.values(), key=lambda bucket: (bucket.expected_margin, bucket.key))
        ],
        "skus": [
            {
                "sku": stats.sku,
                "productName": products[stats.sku].product_name,
                "listPrice": dec(products[stats.sku].list_price),
                "unitCost": dec(products[stats.sku].unit_cost),
                "returnRatePct": dec(products[stats.sku].return_rate * HUNDRED, "0.1"),
                "shippingNetPerUnit": dec(stats.shipping_net_per_unit),
                "averageDiscountGivenPct": dec(stats.observed_discount_pct, "0.1"),
                "breakevenDiscountPct": dec(stats.breakeven_discount_pct, "0.1"),
                "breakevenNote": stats.breakeven_note,
                "unitMarginAtPlannedDiscount": dec(
                    unit_margin_at(stats, products[stats.sku], args, args.planned_discount_pct)
                ),
                "expectedMargin": dec(stats.bucket.expected_margin),
            }
            for stats in sorted(
                skus.values(),
                key=lambda stats: (
                    stats.breakeven_discount_pct if stats.breakeven_discount_pct is not None else Decimal("-999"),
                    stats.sku,
                ),
            )
        ],
        "plannedDiscountPct": str(args.planned_discount_pct),
        "dataQuality": mismatches,
        "financeNetSales": finance_sales,
    }
    return json.dumps(payload, indent=2, sort_keys=False)


if __name__ == "__main__":
    sys.exit(main())
