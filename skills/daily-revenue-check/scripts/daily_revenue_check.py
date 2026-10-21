#!/usr/bin/env python3
"""Daily revenue check: one trusted net-sales number, or a HOLD that says why.

Combines Shopify, Amazon and wholesale exports into one daily net-sales figure
using only finance's revenue_definitions.json, then runs the pre-publication
checks (freshness, completeness, duplicates, reconciliation to the source
reports, late refunds, exclusions, currency conversion) and prints a verdict,
the per-channel numbers, and a DRAFT Slack message.

Stdlib only, offline, deterministic. "Now" comes only from the required --as-of.
Exit codes: 0 PUBLISH, 1 HOLD, 2 bad input or missing definition key.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EXIT_PUBLISH = 0
EXIT_HOLD = 1
EXIT_BAD_INPUT = 2

CENTS = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")

SUPPORTED_CHANNELS = ("shopify", "amazon", "wholesale")
AMOUNT_COLUMNS = (
    "product_amount",
    "discount_amount",
    "shipping_amount",
    "tax_amount",
    "gift_card_amount",
)
BASE_COLUMNS = (
    "record_type",
    "refund_id",
    "created_at",
    "refunded_at",
    "currency",
    *AMOUNT_COLUMNS,
    "tags",
    "financial_status",
    "cancelled",
    "extracted_at",
)
SOURCE_TOTAL_COLUMNS = ("date", "channel", "currency", "net_sales", "scope")
SOURCE_SCOPES = ("all_records", "after_exclusions")
FX_COLUMNS = ("date", "from_currency", "to_currency", "rate")
SPEND_COLUMNS = ("date", "platform", "currency", "spend")
SELL_THROUGH_COLUMNS = ("date", "retailer", "units", "retail_sales", "currency")
SUPPORTED_MER = "net_sales / total_marketing_spend"
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
TRUE_VALUES = {"true", "yes", "1", "y"}
FALSE_VALUES = {"false", "no", "0", "n", ""}


# ---------------------------------------------------------------- basics


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(EXIT_BAD_INPUT)


def money(value: Decimal) -> str:
    return f"{value.quantize(CENTS, rounding=ROUND_HALF_UP):,}"


def parse_timestamp(text: str, *, where: str) -> datetime:
    value = (text or "").strip()
    if not value:
        fail(f"{where}: timestamp is blank")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        fail(f"{where}: {text!r} is not an ISO 8601 timestamp")
        raise
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail(
            f"{where}: {text!r} has no UTC offset. Naive timestamps cannot be placed on a "
            "reporting day; export with an offset (e.g. 2026-09-22T14:05:00-04:00 or ...Z)"
        )
    return parsed


def parse_day(text: str, *, where: str) -> date:
    value = (text or "").strip()
    try:
        if len(value) != 10:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        fail(f"{where}: {text!r} is not a YYYY-MM-DD date")
        raise


def parse_money(text: str, *, where: str, allow_blank: bool, allow_negative: bool) -> Decimal:
    value = (text or "").strip().replace(",", "")
    if value == "":
        if allow_blank:
            return ZERO
        fail(f"{where}: value is blank but required")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        fail(f"{where}: {text!r} is not a number")
        raise
    if not amount.is_finite():
        fail(f"{where}: {text!r} is not a finite number")
    if amount < 0 and not allow_negative:
        fail(
            f"{where}: {text!r} is negative. Amounts are positive; record_type "
            "(sale or refund) decides the sign"
        )
    return amount


def parse_currency(text: str, *, where: str) -> str:
    value = (text or "").strip()
    if not CURRENCY_PATTERN.match(value):
        fail(f"{where}: {text!r} is not a three-letter upper-case ISO 4217 currency code")
    return value


def read_csv(path: Path, required: tuple[str, ...], label: str) -> list[tuple[int, dict[str, str]]]:
    if not path.is_file():
        fail(f"{path}: {label} not found")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        if not header:
            fail(f"{path}: {label} is empty (no header row)")
        stripped = [name.strip() for name in header]
        for column in required:
            if column not in stripped:
                fail(f"{path}: {label} is missing required column `{column}`")
        rows: list[tuple[int, dict[str, str]]] = []
        for raw in reader:
            line = reader.line_num
            if None in raw:
                fail(f"{path} row {line}: more fields than the header has columns")
            rows.append((line, {k.strip(): (v or "") for k, v in raw.items()}))
    return rows


# ---------------------------------------------------------------- definitions


@dataclass
class ChannelDef:
    name: str
    export: str
    dedupe_key: str
    source_of_truth: str


@dataclass
class Definitions:
    path: Path
    version: str
    owner: str
    tz_name: str
    tz: ZoneInfo
    currency: str
    fx_source: str
    fx_rates_file: str
    include_gift: bool
    include_shipping: bool
    include_tax: bool
    subtract_discounts: bool
    subtract_refunds: bool
    refund_basis: str
    late_refund_days: int
    sale_date_basis: str
    excluded_tags: list[str]
    excluded_statuses: list[str]
    exclude_cancelled: bool
    exclude_zero_value: bool
    channels: list[ChannelDef]
    mirror_rules: list[tuple[str, str]]
    staleness_hours: Decimal
    tolerance_pct: Decimal
    tolerance_abs: Decimal
    raw: dict


def load_definitions(path: Path) -> Definitions:
    if not path.is_file():
        fail(
            f"{path}: revenue definitions file not found. This skill requires finance's "
            "revenue_definitions.json (see REVENUE_DEFINITIONS.md) and never falls back to defaults"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        fail(f"{path}: not valid JSON ({exc})")
        raise
    if not isinstance(data, dict):
        fail(f"{path}: top level must be a JSON object")

    def get(dotted: str, kind: str):
        node = data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                fail(
                    f"{path}: missing required key `{dotted}`. Finance owns this file; "
                    "add the key there. This skill does not pick a definition on finance's behalf"
                )
            node = node[part]
        ok = {
            "str": isinstance(node, str) and node.strip() != "",
            "bool": isinstance(node, bool),
            "num": isinstance(node, (int, float)) and not isinstance(node, bool) and node >= 0,
            "int": isinstance(node, int) and not isinstance(node, bool) and node >= 0,
            "list": isinstance(node, list),
            "dict": isinstance(node, dict),
        }[kind]
        if not ok:
            expected = {
                "str": "a non-empty string",
                "bool": "true or false",
                "num": "a non-negative number",
                "int": "a non-negative whole number",
                "list": "a list",
                "dict": "an object",
            }[kind]
            fail(f"{path}: key `{dotted}` must be {expected}, got {node!r}")
        return node

    def unsupported(dotted: str, value: object, allowed: str) -> None:
        fail(f"{path}: key `{dotted}` is {value!r}; this skill supports only {allowed}")

    version = get("version", "str")
    owner = get("owner", "str")
    tz_name = get("reporting_timezone", "str")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        fail(f"{path}: key `reporting_timezone` is {tz_name!r}, not a known IANA time zone")
        raise
    currency = get("reporting_currency", "str")
    if not CURRENCY_PATTERN.match(currency):
        fail(f"{path}: key `reporting_currency` is {currency!r}, not an ISO 4217 code")
    fx_source = get("fx.source", "str")
    fx_rates_file = get("fx.rates_file", "str")

    starts_from = get("net_sales.starts_from", "str")
    if starts_from != "gross_product_sales":
        unsupported("net_sales.starts_from", starts_from, "'gross_product_sales'")
    subtract_discounts = get("net_sales.subtract_discounts", "bool")
    subtract_refunds = get("net_sales.subtract_refunds", "bool")
    include_shipping = get("net_sales.include_shipping_charged", "bool")
    include_tax = get("net_sales.include_tax", "bool")
    include_gift = get("net_sales.include_gift_card_sales", "bool")

    refund_basis = get("refunds.date_basis", "str")
    if refund_basis not in ("refund_date", "order_date"):
        unsupported("refunds.date_basis", refund_basis, "'refund_date' or 'order_date'")
    late_refund_days = get("refunds.late_refund_days", "int")
    sale_date_basis = get("sale_date_basis", "str")
    if sale_date_basis != "order_created":
        unsupported("sale_date_basis", sale_date_basis, "'order_created'")

    tags = get("excluded_orders.tags", "list")
    statuses = get("excluded_orders.financial_statuses", "list")
    for dotted, items in (("excluded_orders.tags", tags), ("excluded_orders.financial_statuses", statuses)):
        for item in items:
            if not isinstance(item, str) or not item.strip():
                fail(f"{path}: key `{dotted}` must list non-empty strings, got {item!r}")
    exclude_cancelled = get("excluded_orders.cancelled", "bool")
    exclude_zero_value = get("excluded_orders.zero_value", "bool")

    channel_map = get("channels", "dict")
    if not channel_map:
        fail(f"{path}: key `channels` configures no channels")
    for name in channel_map:
        if name not in SUPPORTED_CHANNELS:
            unsupported(f"channels.{name}", name, "channels " + ", ".join(SUPPORTED_CHANNELS))
    channels: list[ChannelDef] = []
    for name in SUPPORTED_CHANNELS:
        if name not in channel_map:
            continue
        channels.append(
            ChannelDef(
                name=name,
                export=get(f"channels.{name}.export", "str"),
                dedupe_key=get(f"channels.{name}.dedupe_key", "str"),
                source_of_truth=get(f"channels.{name}.source_of_truth", "str"),
            )
        )
        if channels[-1].dedupe_key in BASE_COLUMNS:
            fail(f"{path}: key `channels.{name}.dedupe_key` cannot be the shared column {channels[-1].dedupe_key!r}")
    if "wholesale" in channel_map:
        event = get("channels.wholesale.revenue_event", "str")
        if event != "invoice":
            unsupported("channels.wholesale.revenue_event", event, "'invoice'")
        if get("channels.wholesale.exclude_retailer_sell_through", "bool") is not True:
            fail(
                f"{path}: key `channels.wholesale.exclude_retailer_sell_through` is false. "
                "Rule 5 of the revenue definitions says retailer sell-through is never wholesale "
                "revenue; this skill will not add it"
            )

    mirror_rules: list[tuple[str, str]] = []
    for index, rule in enumerate(get("cross_channel_duplicates", "list")):
        if not isinstance(rule, dict):
            fail(f"{path}: key `cross_channel_duplicates[{index}]` must be an object")
        if set(rule) - {"shopify_tag", "description"}:
            extra = sorted(set(rule) - {"shopify_tag", "description"})
            unsupported(f"cross_channel_duplicates[{index}]", extra, "`shopify_tag` + `description` rules")
        tag = rule.get("shopify_tag")
        if not isinstance(tag, str) or not tag.strip():
            fail(f"{path}: missing required key `cross_channel_duplicates[{index}].shopify_tag`")
        description = rule.get("description")
        if not isinstance(description, str) or not description.strip():
            fail(f"{path}: missing required key `cross_channel_duplicates[{index}].description`")
        if "shopify" not in channel_map:
            fail(f"{path}: `cross_channel_duplicates[{index}]` names a Shopify tag but `channels.shopify` is not configured")
        mirror_rules.append((tag.strip().lower(), description))

    staleness = Decimal(str(get("checks.max_feed_staleness_hours", "num")))
    tolerance_pct = Decimal(str(get("checks.reconciliation_tolerance_pct", "num")))
    tolerance_abs = Decimal(str(get("checks.reconciliation_tolerance_abs", "num")))

    return Definitions(
        path=path, version=version, owner=owner, tz_name=tz_name, tz=tz, currency=currency,
        fx_source=fx_source, fx_rates_file=fx_rates_file, include_gift=include_gift,
        include_shipping=include_shipping, include_tax=include_tax,
        subtract_discounts=subtract_discounts, subtract_refunds=subtract_refunds,
        refund_basis=refund_basis, late_refund_days=late_refund_days,
        sale_date_basis=sale_date_basis,
        excluded_tags=[t.strip().lower() for t in tags],
        excluded_statuses=[s.strip().lower() for s in statuses],
        exclude_cancelled=exclude_cancelled, exclude_zero_value=exclude_zero_value,
        channels=channels, mirror_rules=mirror_rules, staleness_hours=staleness,
        tolerance_pct=tolerance_pct, tolerance_abs=tolerance_abs, raw=data,
    )


def definition_get(defs: Definitions, dotted: str, kind: str):
    """Late lookup for keys needed only by optional inputs (marketing)."""
    node = defs.raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            fail(
                f"{defs.path}: missing required key `{dotted}` (needed because this input was "
                "supplied). Finance owns this file; add the key there"
            )
        node = node[part]
    if kind == "str" and not (isinstance(node, str) and node.strip()):
        fail(f"{defs.path}: key `{dotted}` must be a non-empty string, got {node!r}")
    if kind == "dict" and not isinstance(node, dict):
        fail(f"{defs.path}: key `{dotted}` must be an object, got {node!r}")
    return node


# ---------------------------------------------------------------- records


@dataclass
class Record:
    channel: str
    kind: str  # "sale" or "refund"
    key: str  # dedupe_key value: the sale's id (refund rows carry the original sale's id)
    refund_id: str
    sale_at: datetime
    refund_at: datetime | None
    currency: str
    product: Decimal
    discount: Decimal
    shipping: Decimal
    tax: Decimal
    gift: Decimal
    tags: tuple[str, ...]
    financial_status: str
    cancelled: bool
    mirror_of: str
    extracted_at: datetime
    where: str
    identity: tuple = field(default=())

    @property
    def dedupe(self) -> tuple:
        return (self.kind, self.key, self.refund_id if self.kind == "refund" else "")

    @property
    def label(self) -> str:
        return f"{self.key} refund {self.refund_id}" if self.kind == "refund" else self.key


def load_export(workdir: Path, channel: ChannelDef, as_of: datetime) -> tuple[Path, list[Record]]:
    path = workdir / channel.export
    required = (channel.dedupe_key, *BASE_COLUMNS)
    rows = read_csv(path, required, f"{channel.name} export (channels.{channel.name}.export)")
    records: list[Record] = []
    for line, row in rows:
        where = f"{path} row {line}"

        def col(name: str) -> str:
            return row.get(name, "").strip()

        kind = col("record_type").lower()
        if kind not in ("sale", "refund"):
            fail(f"{where} column record_type: {row.get('record_type')!r} must be 'sale' or 'refund'")
        key = col(channel.dedupe_key)
        if not key:
            fail(f"{where} column {channel.dedupe_key}: blank dedupe key")
        sale_at = parse_timestamp(col("created_at"), where=f"{where} column created_at")
        extracted_at = parse_timestamp(col("extracted_at"), where=f"{where} column extracted_at")
        refund_id = col("refund_id")
        refund_at: datetime | None = None
        amounts = {
            name: parse_money(row.get(name, ""), where=f"{where} column {name}",
                              allow_blank=name != "product_amount", allow_negative=False)
            for name in AMOUNT_COLUMNS
        }
        if kind == "refund":
            if not refund_id:
                fail(f"{where} column refund_id: refund rows need a refund_id")
            refund_at = parse_timestamp(col("refunded_at"), where=f"{where} column refunded_at")
            if refund_at < sale_at:
                fail(f"{where} column refunded_at: refund {col('refunded_at')} is before the sale's created_at {col('created_at')}")
            if amounts["discount_amount"] != 0:
                fail(f"{where} column discount_amount: must be 0 or blank on refund rows (refund the net amount in product_amount)")
        elif col("refunded_at"):
            fail(f"{where} column refunded_at: must be blank on sale rows; refunds are separate rows with record_type=refund")
        cancelled_text = col("cancelled").lower()
        if cancelled_text in TRUE_VALUES:
            cancelled = True
        elif cancelled_text in FALSE_VALUES:
            cancelled = False
        else:
            fail(f"{where} column cancelled: {row.get('cancelled')!r} must be true/false (blank = false)")
            raise AssertionError
        for label, stamp in (("created_at", sale_at), ("refunded_at", refund_at), ("extracted_at", extracted_at)):
            if stamp is not None and stamp > as_of:
                fail(f"{where} column {label}: {stamp.isoformat()} is after --as-of {as_of.isoformat()}; --as-of must be when you run the check")
        tags = tuple(sorted({t.strip().lower() for t in col("tags").split(",") if t.strip()}))
        record = Record(
            channel=channel.name, kind=kind, key=key, refund_id=refund_id if kind == "refund" else "",
            sale_at=sale_at, refund_at=refund_at,
            currency=parse_currency(col("currency"), where=f"{where} column currency"),
            product=amounts["product_amount"], discount=amounts["discount_amount"],
            shipping=amounts["shipping_amount"], tax=amounts["tax_amount"], gift=amounts["gift_card_amount"],
            tags=tags, financial_status=col("financial_status").lower(), cancelled=cancelled,
            mirror_of=col("mirror_of"), extracted_at=extracted_at, where=where,
        )
        record.identity = (
            kind, key, record.refund_id, sale_at, refund_at, record.currency,
            *(amounts[n] for n in AMOUNT_COLUMNS), tags, record.financial_status, cancelled, record.mirror_of,
        )
        records.append(record)
    return path, records


# ---------------------------------------------------------------- fx


class Fx:
    def __init__(self, path: Path, reporting: str):
        self.path = path
        self.reporting = reporting
        self.exists = path.is_file()
        self.rates: dict[tuple[date, str], Decimal] = {}
        self.missing: set[tuple[str, date]] = set()
        self.used: set[tuple[str, date, Decimal]] = set()
        if not self.exists:
            return
        for line, row in read_csv(path, FX_COLUMNS, "FX rates file (fx.rates_file)"):
            where = f"{path} row {line}"
            day = parse_day(row["date"], where=f"{where} column date")
            source = parse_currency(row["from_currency"].strip(), where=f"{where} column from_currency")
            target = parse_currency(row["to_currency"].strip(), where=f"{where} column to_currency")
            rate = parse_money(row["rate"], where=f"{where} column rate", allow_blank=False, allow_negative=False)
            if rate == 0:
                fail(f"{where} column rate: rate is zero")
            if target != reporting:
                continue
            previous = self.rates.get((day, source))
            if previous is not None and previous != rate:
                fail(f"{where}: second, different {source}->{target} rate for {day} ({previous} vs {rate})")
            self.rates[(day, source)] = rate

    def convert(self, amount: Decimal, currency: str, day: date) -> Decimal | None:
        if currency == self.reporting:
            return amount
        rate = self.rates.get((day, currency))
        if rate is None:
            self.missing.add((currency, day))
            return None
        self.used.add((currency, day, rate))
        return amount * rate


# ---------------------------------------------------------------- totals


@dataclass
class Totals:
    gross: Decimal = ZERO
    discounts: Decimal = ZERO
    refunds: Decimal = ZERO
    sales: int = 0
    refund_rows: int = 0
    unconverted: dict[str, Decimal] = field(default_factory=dict)

    @property
    def net(self) -> Decimal:
        return self.gross - self.discounts - self.refunds

    @property
    def records(self) -> int:
        return self.sales + self.refund_rows


class Engine:
    def __init__(self, defs: Definitions, fx: Fx):
        self.defs = defs
        self.fx = fx

    def local_day(self, stamp: datetime) -> date:
        return stamp.astimezone(self.defs.tz).date()

    def effective_day(self, record: Record) -> date:
        if record.kind == "sale" or self.defs.refund_basis == "order_date":
            return self.local_day(record.sale_at)
        return self.local_day(record.refund_at)

    def fx_day(self, record: Record) -> date:
        return self.local_day(record.sale_at if record.kind == "sale" else record.refund_at)

    def native(self, record: Record) -> tuple[Decimal, Decimal, Decimal]:
        d = self.defs
        extra = (record.gift if d.include_gift else ZERO) + (record.shipping if d.include_shipping else ZERO) + (
            record.tax if d.include_tax else ZERO
        )
        if record.kind == "sale":
            return record.product + extra, (record.discount if d.subtract_discounts else ZERO), ZERO
        return ZERO, ZERO, (record.product + extra if d.subtract_refunds else ZERO)

    def add(self, totals: Totals, record: Record) -> Decimal | None:
        gross, discounts, refunds = self.native(record)
        day = self.fx_day(record)
        converted = [self.fx.convert(v, record.currency, day) for v in (gross, discounts, refunds)]
        if record.kind == "sale":
            totals.sales += 1
        else:
            totals.refund_rows += 1
        if any(v is None for v in converted):
            totals.unconverted[record.currency] = totals.unconverted.get(record.currency, ZERO) + gross - discounts - refunds
            return None
        totals.gross += converted[0]
        totals.discounts += converted[1]
        totals.refunds += converted[2]
        return converted[0] - converted[1] - converted[2]

    def net_of(self, record: Record) -> Decimal | None:
        return self.add(Totals(), record)


# ---------------------------------------------------------------- checks


@dataclass
class Check:
    number: int
    name: str
    status: str = "PASS"  # PASS, FAIL or NOTE
    lines: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (why, what to do)

    def fail(self, why: str, action: str) -> None:
        self.status = "FAIL"
        self.failures.append((why, action))
        self.lines.append(f"FAIL: {why}")

    def note(self, text: str) -> None:
        if self.status == "PASS":
            self.status = "NOTE"
        self.lines.append(text)


def hours(delta: timedelta) -> Decimal:
    return (Decimal(int(delta.total_seconds())) / Decimal(3600)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def fmt_local(stamp: datetime, tz: ZoneInfo) -> str:
    return stamp.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def short_day(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def pretty_day(day: date) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b %Y')}"


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", required=True, help="brand working folder holding finance/ and the channel exports")
    parser.add_argument("--as-of", required=True, help="ISO 8601 timestamp WITH offset: when the check is run")
    parser.add_argument("--date", help="report day YYYY-MM-DD in the reporting timezone (default: the day before --as-of)")
    parser.add_argument("--definitions", help="path to revenue_definitions.json (default: <workdir>/finance/revenue_definitions.json)")
    parser.add_argument("--source-totals", help="agreed source-report totals CSV (default: <workdir>/source_totals.csv)")
    parser.add_argument("--spend", help="optional marketing spend CSV; enables MER")
    parser.add_argument("--sell-through", help="optional retailer sell-through CSV; context only, never added")
    parser.add_argument("--max-shopify-gap-hours", type=int, default=4,
                        help="longest run of whole local hours with no Shopify records before completeness fails (default 4)")
    parser.add_argument("--allow-empty-channel", action="append", default=[], metavar="CHANNEL",
                        help="channel confirmed to have genuinely had no records on the report day (repeatable)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = parse_timestamp(args.as_of, where="--as-of")
    if args.max_shopify_gap_hours < 1:
        fail("--max-shopify-gap-hours must be at least 1")
    workdir = Path(args.workdir)
    if not workdir.is_dir():
        fail(f"--workdir {workdir}: not a directory")
    defs_path = Path(args.definitions) if args.definitions else workdir / "finance" / "revenue_definitions.json"
    defs = load_definitions(defs_path)
    tz = defs.tz
    configured = [c.name for c in defs.channels]
    for name in args.allow_empty_channel:
        if name not in configured:
            fail(f"--allow-empty-channel {name}: not a configured channel ({', '.join(configured)})")
    report_day = (
        parse_day(args.date, where="--date") if args.date else as_of.astimezone(tz).date() - timedelta(days=1)
    )
    day_start = datetime.combine(report_day, time(0), tzinfo=tz)
    day_end = datetime.combine(report_day + timedelta(days=1), time(0), tzinfo=tz)
    if as_of < day_start:
        fail(f"--as-of {args.as_of} is before the report day {report_day} begins")

    fx = Fx(workdir / defs.fx_rates_file, defs.currency)
    engine = Engine(defs, fx)

    # -- load and dedupe within each channel
    exports: dict[str, tuple[Path, list[Record]]] = {}
    for channel in defs.channels:
        exports[channel.name] = load_export(workdir, channel, as_of)

    kept: dict[str, list[Record]] = {}
    exact_dupes: list[tuple[Record, Record]] = []
    conflict_dupes: list[tuple[Record, Record]] = []
    for channel in defs.channels:
        seen: dict[tuple, Record] = {}
        rows: list[Record] = []
        for record in exports[channel.name][1]:
            first = seen.get(record.dedupe)
            if first is None:
                seen[record.dedupe] = record
                rows.append(record)
            elif first.identity == record.identity:
                exact_dupes.append((first, record))
            else:
                conflict_dupes.append((first, record))
        kept[channel.name] = rows

    sales_index: dict[str, dict[str, Record]] = {
        name: {r.key: r for r in rows if r.kind == "sale"} for name, rows in kept.items()
    }
    for name, rows in kept.items():
        for record in rows:
            sale = sales_index[name].get(record.key) if record.kind == "refund" else None
            if sale is not None and sale.sale_at != record.sale_at:
                fail(
                    f"{record.where} column created_at: refund says the sale was created "
                    f"{record.sale_at.isoformat()} but {sale.where} says {sale.sale_at.isoformat()}"
                )

    mirror_tags = [tag for tag, _ in defs.mirror_rules]

    def attrs(record: Record) -> Record:
        """Refunds inherit exclusion/mirror attributes from their sale when it is in the export."""
        if record.kind == "refund":
            sale = sales_index[record.channel].get(record.key)
            if sale is not None:
                return sale
        return record

    def classify(record: Record) -> str:
        source = attrs(record)
        for tag in defs.excluded_tags:
            if tag in source.tags:
                return f"tag:{tag}"
        if source.financial_status and source.financial_status in defs.excluded_statuses:
            return f"financial_status:{source.financial_status}"
        if defs.exclude_cancelled and source.cancelled:
            return "cancelled"
        if defs.exclude_zero_value and source.kind == "sale":
            charged = source.product - source.discount + source.gift + source.shipping + source.tax
            if charged == 0:
                return "zero_value"
        if record.channel == "shopify":
            for tag in mirror_tags:
                if tag in source.tags:
                    return f"mirror:{tag}"
        return "counted"

    # -- per-channel buckets for the report day
    platform: dict[str, Totals] = {}
    counted: dict[str, Totals] = {}
    excluded_by_channel: dict[str, Totals] = {}
    mirrored: dict[str, Totals] = {}
    exclusion_rules: dict[str, Totals] = {}
    mirror_rows: list[tuple[Record, Decimal | None]] = []
    on_day: dict[str, list[Record]] = {}
    for channel in defs.channels:
        name = channel.name
        platform[name], counted[name], excluded_by_channel[name], mirrored[name] = Totals(), Totals(), Totals(), Totals()
        on_day[name] = [r for r in kept[name] if engine.effective_day(r) == report_day]
        for record in on_day[name]:
            engine.add(platform[name], record)
            status = classify(record)
            if status == "counted":
                engine.add(counted[name], record)
            elif status.startswith("mirror:"):
                mirror_rows.append((record, engine.add(mirrored[name], record)))
            else:
                engine.add(excluded_by_channel[name], record)
                engine.add(exclusion_rules.setdefault(f"{name} {status}", Totals()), record)

    total = Totals()
    for name in configured:
        total.gross += counted[name].gross
        total.discounts += counted[name].discounts
        total.refunds += counted[name].refunds
        total.sales += counted[name].sales
        total.refund_rows += counted[name].refund_rows
        for cur, amount in counted[name].unconverted.items():
            total.unconverted[cur] = total.unconverted.get(cur, ZERO) + amount

    sell_lines: list[str] = []
    sell_by_currency: dict[str, Decimal] = {}
    if args.sell_through:
        st_path = Path(args.sell_through)
        for line, row in read_csv(st_path, SELL_THROUGH_COLUMNS, "sell-through file"):
            where = f"{st_path} row {line}"
            if parse_day(row["date"], where=f"{where} column date") != report_day:
                continue
            units_text = row["units"].strip()
            if not units_text.isdigit():
                fail(f"{where} column units: {row['units']!r} is not a whole number")
            currency = parse_currency(row["currency"].strip(), where=f"{where} column currency")
            amount = parse_money(row["retail_sales"], where=f"{where} column retail_sales", allow_blank=False,
                                 allow_negative=False)
            sell_by_currency[currency] = sell_by_currency.get(currency, ZERO) + amount
            sell_lines.append(f"  {row['retailer'].strip()}: {int(units_text)} units, {money(amount)} {currency} at retail")
        if not sell_lines:
            sell_lines.append(f"  no rows for {report_day}")
        sell_lines.append("  NOT added to wholesale revenue: wholesale revenue is what was invoiced "
                          "(channels.wholesale.exclude_retailer_sell_through = true; definitions rule 5).")

    channel_by_name = {c.name: c for c in defs.channels}
    checks: list[Check] = []

    # 1. freshness
    fresh = Check(1, "Freshness")
    limit = defs.staleness_hours
    for channel in defs.channels:
        path, records = exports[channel.name]
        if not records:
            fresh.fail(f"{channel.name}: {path} has no rows, so it has no extract timestamp",
                       f"Re-export {channel.name} from {channel.source_of_truth} and re-run.")
            continue
        extracted = max(r.extracted_at for r in records)
        latest = max(r.refund_at if r.kind == "refund" else r.sale_at for r in records)
        age = hours(as_of - extracted)
        text = (f"{channel.name}: extracted {fmt_local(extracted, tz)} ({age}h before as-of; limit {limit}h); "
                f"latest record {fmt_local(latest, tz)}")
        if age > limit:
            fresh.fail(text, f"Re-export {channel.name} from {channel.source_of_truth} and re-run.")
        else:
            fresh.lines.append(text)
    checks.append(fresh)

    # 2. completeness
    complete = Check(2, "Completeness")
    for channel in defs.channels:
        name = channel.name
        path, records = exports[name]
        day_sales = [r for r in on_day[name] if r.kind == "sale"]
        if records:
            extracted = max(r.extracted_at for r in records)
            if extracted < day_end:
                complete.fail(
                    f"{name}: extracted {fmt_local(extracted, tz)}, before {report_day} ended ({fmt_local(day_end, tz)})",
                    f"Re-export {name} after the reporting day has closed in {defs.tz_name}.")
        if day_sales:
            complete.lines.append(f"{name}: {len(day_sales)} sale records in the export for {report_day}")
        elif name in args.allow_empty_channel:
            complete.note(f"{name}: no sale records on {report_day}; accepted via --allow-empty-channel")
        else:
            complete.fail(f"{name}: no sale records on {report_day}",
                          f"Confirm with the {name} owner that there were genuinely no sales, then re-run with "
                          f"--allow-empty-channel {name}; otherwise re-export {name} from {channel.source_of_truth}.")
    if "shopify" in configured:
        slots = int((day_end - day_start).total_seconds() // 3600)
        busy = {int((r.sale_at - day_start).total_seconds() // 3600)
                for r in on_day["shopify"] if r.kind == "sale" and day_start <= r.sale_at < day_end}
        longest, run, run_start, best_start = 0, 0, 0, 0
        for slot in range(slots):
            if slot in busy:
                run = 0
                continue
            if run == 0:
                run_start = slot
            run += 1
            if run > longest:
                longest, best_start = run, run_start
        if longest:
            gap_from = day_start + timedelta(hours=best_start)
            gap_to = gap_from + timedelta(hours=longest)
            gap_text = (f"shopify: longest run with no orders is {longest}h "
                        f"({gap_from.astimezone(tz).strftime('%H:%M')}-{gap_to.astimezone(tz).strftime('%H:%M')} local); "
                        f"limit {args.max_shopify_gap_hours}h")
        else:
            gap_text = f"shopify: orders in every hour; limit {args.max_shopify_gap_hours}h"
        if longest > args.max_shopify_gap_hours:
            complete.fail(gap_text, "Check the Shopify export for a missing page or time window and re-export; if the "
                                    "quiet period is genuine, re-run with a larger --max-shopify-gap-hours and say so.")
        else:
            complete.lines.append(gap_text)
    checks.append(complete)

    # 3. duplicates
    dupes = Check(3, "Duplicates")
    for first, again in exact_dupes:
        dupes.lines.append(f"{first.channel}: {first.label} appears twice ({first.where}; {again.where}); "
                           f"counted once")
    for first, again in conflict_dupes:
        key = channel_by_name[first.channel].dedupe_key
        dupes.fail(f"{first.channel}: {first.label} appears twice with different values ({first.where}; {again.where})",
                   f"Re-export {first.channel}; two rows share {key} {first.key} but disagree, so neither can be trusted.")
    other_keys: dict[str, list[tuple[str, date]]] = {}
    for name, rows in kept.items():
        if name == "shopify":
            continue
        for record in rows:
            if record.kind == "sale":
                other_keys.setdefault(record.key, []).append((name, engine.local_day(record.sale_at)))
    for record, value in mirror_rows:
        tag = classify(record).split(":", 1)[1]
        amount = f"{money(value)} {defs.currency}" if value is not None else f"(unconverted {record.currency})"
        if record.kind == "refund":
            dupes.lines.append(f"shopify: {record.label} belongs to a {tag} mirror; removed with it ({amount})")
            continue
        matches = other_keys.get(record.mirror_of, []) if record.mirror_of else []
        if record.mirror_of and not matches:
            dupes.fail(f"shopify: {record.key} is tagged {tag} and mirrors {record.mirror_of}, which is in no other export",
                       f"Find {record.mirror_of} in the Amazon or wholesale export (it may be missing, so its revenue "
                       f"would vanish) or correct the tag/mirror_of on Shopify order {record.key}.")
        elif matches:
            where = ", ".join(f"{ch} {record.mirror_of} dated {d}" for ch, d in matches)
            dupes.lines.append(f"shopify: {record.key} tagged {tag} mirrors {where}; removed from Shopify ({amount})")
        else:
            dupes.lines.append(f"shopify: {record.key} tagged {tag}; removed from Shopify per cross_channel_duplicates, "
                               f"counterpart not referenced (no mirror_of) ({amount})")
    for record in on_day.get("shopify", []):
        if record.kind == "sale" and record.mirror_of and classify(record) == "counted":
            dupes.fail(f"shopify: {record.key} has mirror_of {record.mirror_of} but no cross-channel tag, so it would be "
                       f"counted in Shopify and again in the other channel",
                       f"Tag Shopify order {record.key} with the matching cross_channel_duplicates tag (or clear "
                       f"mirror_of if it is a genuine Shopify sale) and re-export.")
    if not dupes.lines:
        dupes.lines.append("none found")
    checks.append(dupes)

    # 4. reconciliation
    recon = Check(4, "Reconciliation to source reports")
    totals_path = Path(args.source_totals) if args.source_totals else workdir / "source_totals.csv"
    source: dict[str, dict[str, Decimal]] = {}
    scopes: dict[str, str] = {}
    for line, row in read_csv(totals_path, SOURCE_TOTAL_COLUMNS, "source totals file"):
        where = f"{totals_path} row {line}"
        day = parse_day(row["date"], where=f"{where} column date")
        if day != report_day:
            continue
        name = row["channel"].strip().lower()
        if name not in configured:
            fail(f"{where} column channel: {row['channel']!r} is not a configured channel ({', '.join(configured)})")
        currency = parse_currency(row["currency"].strip(), where=f"{where} column currency")
        amount = parse_money(row["net_sales"], where=f"{where} column net_sales", allow_blank=False, allow_negative=True)
        scope = row["scope"].strip()
        if scope not in SOURCE_SCOPES:
            fail(f"{where} column scope: {row['scope']!r} must be one of {', '.join(SOURCE_SCOPES)}")
        if scopes.setdefault(name, scope) != scope:
            fail(f"{where} column scope: {name} rows mix scopes {scopes[name]} and {scope}")
        if currency in source.setdefault(name, {}):
            fail(f"{where}: second {name} {currency} row for {report_day}")
        source[name][currency] = amount
    recon_table: dict[str, tuple[Decimal | None, Decimal | None, str]] = {}
    for channel in defs.channels:
        name = channel.name
        if name not in source:
            recon.fail(f"{name}: {totals_path} has no {report_day} total for {name}",
                       f"Add the {report_day} figure from {channel.source_of_truth} to {totals_path}.")
            recon_table[name] = (None, None, "")
            continue
        src_total = ZERO
        src_ok = True
        for currency, amount in sorted(source[name].items()):
            converted = fx.convert(amount, currency, report_day)
            if converted is None:
                src_ok = False
            else:
                src_total += converted
        ours = platform[name].net if scopes[name] == "all_records" else platform[name].net - excluded_by_channel[name].net
        ours_ok = not platform[name].unconverted
        if not (src_ok and ours_ok):
            recon.fail(f"{name}: cannot compare, a currency amount has no {defs.currency} rate (see check 7)",
                       "Add the missing rate(s) listed under check 7 and re-run.")
            recon_table[name] = (None, None, scopes[name])
            continue
        diff = ours - src_total
        pct = (abs(diff) / abs(src_total) * HUNDRED) if src_total != 0 else None
        pct_text = f"{pct.quantize(CENTS, rounding=ROUND_HALF_UP)}%" if pct is not None else "n/a"
        within = abs(diff) <= defs.tolerance_abs or (pct is not None and pct <= defs.tolerance_pct)
        text = (f"{name}: export {money(ours)} vs {channel.source_of_truth} {money(src_total)} "
                f"({scopes[name]}); diff {money(diff)} ({pct_text})")
        recon_table[name] = (ours, src_total, scopes[name])
        if within:
            recon.lines.append(text + " - within tolerance")
        else:
            hint = ""
            if name == "wholesale" and sell_by_currency and sum(
                    (fx.rates.get((report_day, c), Decimal(1) if c == defs.currency else ZERO) * v
                     for c, v in sell_by_currency.items()), ZERO) == src_total:
                hint = (" The source figure equals the retailer sell-through for the day, which is not revenue; "
                        "use the invoice total.")
            recon.fail(text + f" - outside {defs.tolerance_pct}% and {money(defs.tolerance_abs)} {defs.currency}",
                       f"Re-pull the {report_day} total from {channel.source_of_truth} ({defs.tz_name} day, "
                       f"{scopes[name]}) and compare it with the {name} export row by row; do not post until they "
                       f"agree.{hint}")
    recon.lines.append(f"tolerance: {defs.tolerance_pct}% OR {money(defs.tolerance_abs)} {defs.currency} "
                       "(checks.reconciliation_tolerance_pct / _abs)")
    checks.append(recon)

    # 5. late refunds
    late = Check(5, "Late refunds")
    moves: dict[date, Decimal] = {}
    late_count = 0
    for channel in defs.channels:
        for record in kept[channel.name]:
            if record.kind != "refund" or engine.local_day(record.refund_at) != report_day:
                continue
            sale_day = engine.local_day(record.sale_at)
            age = (report_day - sale_day).days
            status = classify(record)
            value = engine.net_of(record)
            amount = money(-value) if value is not None else f"(unconverted {record.currency})"
            if defs.refund_basis == "order_date" and sale_day < report_day and status == "counted" and value is not None:
                moves[sale_day] = moves.get(sale_day, ZERO) + value
            if age <= defs.late_refund_days:
                continue
            late_count += 1
            if status != "counted":
                effect = f"not counted ({status})"
            elif defs.refund_basis == "refund_date":
                effect = f"counted in {report_day} (refund_date basis); no prior day moves"
            else:
                effect = f"restates {sale_day} (order_date basis)"
            late.note(f"{channel.name}: {record.label} issued {report_day} against sale of {sale_day} "
                      f"({age} days; limit {defs.late_refund_days}): {amount} {defs.currency}; {effect}")
    if defs.refund_basis == "order_date":
        if moves:
            late.note("prior days that move (order_date basis, refunds issued today, all ages):")
            for day in sorted(moves):
                late.lines.append(f"  {day}: {money(moves[day])} {defs.currency}")
        else:
            late.lines.append("no prior day moves")
    if not late_count:
        late.lines.insert(0, f"no refunds issued on {report_day} against sales older than {defs.late_refund_days} days")
    checks.append(late)

    # 6. exclusions
    excl = Check(6, "Excluded orders")
    if exclusion_rules:
        for rule in sorted(exclusion_rules):
            t = exclusion_rules[rule]
            value = f"{money(t.net)} {defs.currency}" + (" (+ unconverted amounts)" if t.unconverted else "")
            excl.lines.append(f"{rule}: {t.sales} sale(s), {t.refund_rows} refund(s), value {value}")
    else:
        excl.lines.append("none")
    checks.append(excl)

    # 7. currency conversion
    conv = Check(7, "Currency conversion")
    fx_label = f"{fx.path} ({defs.fx_source})"
    for currency, day in sorted(fx.missing):
        missing_file = "" if fx.exists else " (file not found)"
        conv.fail(f"no {currency}->{defs.currency} rate for {day} in {fx.path}{missing_file}",
                  f"Add the {day} {currency}->{defs.currency} rate from {defs.fx_source} to {fx.path}; never estimate one.")
    for currency, day, rate in sorted(fx.used):
        conv.lines.append(f"{currency}->{defs.currency} {day}: {rate} from {fx_label}")
    if not fx.missing and not fx.used:
        conv.lines.append(f"all amounts already in {defs.currency}")
    checks.append(conv)

    # -- optional marketing (MER) and sell-through context
    mer_lines: list[str] = []
    mer_value: Decimal | None = None
    spend_total = ZERO
    if args.spend:
        mer_def = definition_get(defs, "marketing.mer", "str")
        if " ".join(mer_def.split()) != SUPPORTED_MER:
            fail(f"{defs.path}: key `marketing.mer` is {mer_def!r}; this skill supports only {SUPPORTED_MER!r}")
        spend_path = Path(args.spend)
        spend_rows = read_csv(spend_path, SPEND_COLUMNS, "spend file")
        claimed: list[tuple[str, Decimal, str, str]] = []
        platforms: dict[str, Decimal] = {}
        spend_ok = True
        for line, row in spend_rows:
            where = f"{spend_path} row {line}"
            if parse_day(row["date"], where=f"{where} column date") != report_day:
                continue
            platform_name = row["platform"].strip().lower()
            if not platform_name:
                fail(f"{where} column platform: blank")
            currency = parse_currency(row["currency"].strip(), where=f"{where} column currency")
            spend = parse_money(row["spend"], where=f"{where} column spend", allow_blank=False, allow_negative=False)
            converted = fx.convert(spend, currency, report_day)
            if converted is None:
                spend_ok = False
            else:
                platforms[platform_name] = platforms.get(platform_name, ZERO) + converted
            claimed_text = row.get("platform_claimed_revenue", "").strip()
            if claimed_text:
                window = definition_get(defs, f"marketing.attribution_windows.{platform_name}", "str")
                amount = parse_money(claimed_text, where=f"{where} column platform_claimed_revenue",
                                     allow_blank=False, allow_negative=False)
                claimed.append((platform_name, amount, currency, window))
        spend_total = sum(platforms.values(), ZERO)
        mer_lines.append(f"MER definition (marketing.mer): {SUPPORTED_MER}")
        for name in sorted(platforms):
            mer_lines.append(f"  spend {name}: {money(platforms[name])} {defs.currency}")
        mer_lines.append(f"  total marketing spend: {money(spend_total)} {defs.currency}")
        if not spend_ok or total.unconverted:
            mer_lines.append("  MER: not computed (missing FX rate; see check 7)")
        elif spend_total == 0:
            mer_lines.append(f"  MER: not computed (no spend rows for {report_day})")
        else:
            mer_value = total.net / spend_total
            mer_lines.append(f"  MER = {money(total.net)} / {money(spend_total)} = "
                             f"{mer_value.quantize(CENTS, rounding=ROUND_HALF_UP)}")
        if claimed:
            mer_lines.append("  platform-claimed revenue (each platform's own window; overlapping; never summed, not sales):")
            for name, amount, currency, window in claimed:
                mer_lines.append(f"    {name}: {money(amount)} {currency} ({window})")

    # -- render
    failures = [(c, why, action) for c in checks for why, action in c.failures]
    verdict = "HOLD" if failures else "PUBLISH"
    cur = defs.currency
    out: list[str] = []
    out.append(f"DAILY REVENUE CHECK - {report_day} ({defs.tz_name})")
    out.append(f"as of {as_of.isoformat()}   workdir {workdir}")
    out.append("=" * 96)
    out.append("")
    out.append(f"DEFINITIONS  version {defs.version}  owner {defs.owner}  ({defs.path})")
    parts = ["gross product sales"]
    if defs.subtract_discounts:
        parts.append("discounts")
    if defs.subtract_refunds:
        parts.append("refunds")
    out.append("  net sales = " + " - ".join(parts))
    out.append(f"  shipping charged {'included' if defs.include_shipping else 'excluded'}; tax "
               f"{'included' if defs.include_tax else 'excluded'}; gift-card sales "
               f"{'included' if defs.include_gift else 'excluded'}")
    out.append(f"  sales dated by {defs.sale_date_basis}; refunds on {defs.refund_basis} basis; "
               f"late refund = older than {defs.late_refund_days} days")
    out.append(f"  reporting currency {cur}; rates from {defs.fx_rates_file} ({defs.fx_source})")
    out.append(f"  excluded: tags {', '.join(defs.excluded_tags) or '-'}; financial status "
               f"{', '.join(defs.excluded_statuses) or '-'}; cancelled {'yes' if defs.exclude_cancelled else 'no'}; "
               f"zero value {'yes' if defs.exclude_zero_value else 'no'}")
    if defs.mirror_rules:
        out.append("  cross-channel duplicates removed from Shopify: " +
                   "; ".join(f"tag {tag} ({desc})" for tag, desc in defs.mirror_rules))
    out.append("")
    out.append(f"VERDICT: {verdict}")
    if failures:
        for index, (check, why, action) in enumerate(failures, 1):
            out.append(f"  {index}. [{check.name}] {why}")
            out.append(f"     do: {action}")
    else:
        out.append("  every check passed; the number below can be posted after a human reads the draft")
    out.append("")

    out.append(f"NET SALES - {report_day} ({cur})")
    header = f"  {'channel':<11}{'records':>8}{'gross':>12}{'discounts':>12}{'refunds':>11}{'net sales':>13}   source of truth"
    out.append(header)
    for channel in defs.channels:
        t = counted[channel.name]
        out.append(f"  {channel.name:<11}{t.records:>8}{money(t.gross):>12}{money(t.discounts):>12}"
                   f"{money(t.refunds):>11}{money(t.net):>13}   {channel.source_of_truth}")
    out.append(f"  {'TOTAL':<11}{total.records:>8}{money(total.gross):>12}{money(total.discounts):>12}"
               f"{money(total.refunds):>11}{money(total.net):>13}")
    if total.unconverted:
        out.append("  INCOMPLETE: excludes unconverted " +
                   ", ".join(f"{money(v)} {c}" for c, v in sorted(total.unconverted.items())))
    out.append("")

    out.append("BRIDGE: export -> net sales")
    out.append(f"  {'channel':<11}{'export':>12}{'excluded':>12}{'mirrors':>12}{'net sales':>13}{'source':>12}{'diff':>11}")
    for channel in defs.channels:
        name = channel.name
        ours, theirs, _ = recon_table[name]
        src = money(theirs) if theirs is not None else "-"
        diff = money(ours - theirs) if ours is not None and theirs is not None else "-"
        out.append(f"  {name:<11}{money(platform[name].net):>12}{money(-excluded_by_channel[name].net):>12}"
                   f"{money(-mirrored[name].net):>12}{money(counted[name].net):>13}{src:>12}{diff:>11}")
    out.append("  source is compared with 'export' (scope all_records) or 'export' + 'excluded' (scope after_exclusions)")
    out.append("")

    out.append("CHECKS")
    for check in checks:
        out.append(f"  [{check.status}] {check.number}. {check.name}")
        for line in check.lines:
            out.append(f"         {line}")
    out.append("")

    if sell_lines:
        out.append(f"RETAILER SELL-THROUGH - context only, not revenue ({args.sell_through})")
        out.extend(sell_lines)
        out.append("")
    if mer_lines:
        out.append(f"MARKETING EFFICIENCY ({args.spend})")
        out.extend(f"  {line}" if not line.startswith("  ") else line for line in mer_lines)
        out.append("")

    # -- Slack draft
    slack: list[str] = []
    if verdict == "PUBLISH":
        slack.append("[DRAFT - not posted. A person reviews this and posts it.]")
        headline = f"Net sales {pretty_day(report_day)} ({defs.tz_name}): {money(total.net)} {cur}"
    else:
        slack.append("[DRAFT - ON HOLD. Do not post until the HOLD reasons above are resolved.]")
        headline = f"Net sales {pretty_day(report_day)} ({defs.tz_name}): {money(total.net)} {cur} (provisional, on hold)"
    slack.append(headline)
    slack.append(" | ".join(f"{c.name.capitalize()} {money(counted[c.name].net)}" for c in defs.channels))
    basis_bits = []
    if defs.subtract_discounts or defs.subtract_refunds:
        net_of = " and ".join(x for x, on in (("discounts", defs.subtract_discounts), ("refunds", defs.subtract_refunds)) if on)
        basis_bits.append(f"net of {net_of} ({defs.refund_basis.replace('_', '-')} basis)")
    excluded_parts = [x for x, inc in (("tax", defs.include_tax), ("shipping", defs.include_shipping),
                                       ("gift-card sales", defs.include_gift)) if not inc]
    if excluded_parts:
        basis_bits.append("excludes " + ", ".join(excluded_parts))
    if "wholesale" in configured:
        basis_bits.append("wholesale = invoiced")
    slack.append(f"Definitions v{defs.version}: " + "; ".join(basis_bits) + ".")
    if mer_value is not None:
        slack.append(f"MER {mer_value.quantize(CENTS, rounding=ROUND_HALF_UP)} "
                     f"(net sales / total marketing spend {money(spend_total)} {cur})")
    caveats: list[str] = []
    if failures:
        caveats.append(f"On hold: {len(failures)} check(s) failed; see the HOLD reasons.")
    for channel in defs.channels:
        for record in kept[channel.name]:
            if record.kind != "refund" or engine.local_day(record.refund_at) != report_day:
                continue
            sale_day = engine.local_day(record.sale_at)
            if (report_day - sale_day).days <= defs.late_refund_days or classify(record) != "counted":
                continue
            value = engine.net_of(record)
            amount = f"{money(-value)} {cur}" if value is not None else f"an unconverted {record.currency} amount"
            if defs.refund_basis == "refund_date":
                caveats.append(f"Includes a {amount} late refund on a {channel.name.capitalize()} order "
                               f"from {short_day(sale_day)} (refund-date basis, so it lands today).")
            else:
                caveats.append(f"A {amount} late refund on a {channel.name.capitalize()} order restates "
                               f"{short_day(sale_day)} (order-date basis).")
    if defs.refund_basis == "order_date" and moves:
        caveats.append("Prior days restated by today's refunds: " +
                       ", ".join(f"{short_day(d)} {money(v)}" for d, v in sorted(moves.items())) + ".")
    if exclusion_rules:
        ex_total = Totals()
        for t in exclusion_rules.values():
            ex_total.gross += t.gross
            ex_total.discounts += t.discounts
            ex_total.refunds += t.refunds
            ex_total.sales += t.sales
            ex_total.refund_rows += t.refund_rows
        caveats.append(f"Excluded {ex_total.records} record(s) worth {money(ex_total.net)} {cur} per finance rules (" +
                       ", ".join(f"{r.split(' ', 1)[1]} {exclusion_rules[r].records}" for r in sorted(exclusion_rules)) + ").")
    for currency, day, rate in sorted(fx.used):
        caveats.append(f"{currency} converted at {rate} ({defs.fx_source}, {day}).")
    for channel in defs.channels:
        if channel.name in args.allow_empty_channel and not any(r.kind == "sale" for r in on_day[channel.name]):
            caveats.append(f"No {channel.name} sales recorded for the day (confirmed).")
    if caveats:
        slack.append("Caveats:")
        slack.extend(f"- {c}" for c in caveats)
    out.append("SLACK MESSAGE (DRAFT)")
    out.extend(f"  {line}" for line in slack)

    print("\n".join(out))
    return EXIT_PUBLISH if verdict == "PUBLISH" else EXIT_HOLD


if __name__ == "__main__":
    sys.exit(main())
