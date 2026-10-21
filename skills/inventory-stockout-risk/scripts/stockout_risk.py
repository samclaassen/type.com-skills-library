#!/usr/bin/env python3
"""Velocity-based days of cover, stockout exposure, reorder quantities, and overstock.

Reads a sales history, an on-hand inventory snapshot, and an optional supplier
table, then reports per SKU: demand velocity, days of cover, projected stockout
date, revenue exposure if nothing is ordered, a recommended order quantity
rounded to the supplier's case pack and MOQ, and the working capital sitting in
overstock.

Stdlib only, offline, deterministic. "Today" comes from the required --as-of.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from statistics import NormalDist

CENTS = Decimal("0.01")
UNITS = Decimal("0.001")
ZERO = Decimal("0")

SALES_REQUIRED = ["sku", "order_date", "units_sold"]
INVENTORY_REQUIRED = ["sku", "on_hand_units", "unit_cost", "unit_price"]
SUPPLIER_REQUIRED = ["supplier_id", "lead_time_days"]

STATUS_ORDER = [
    "OVERSOLD",
    "STOCKOUT BEFORE REPLENISHMENT",
    "AT RISK",
    "HEALTHY",
    "OVERSTOCK",
    "DEAD STOCK",
    "NO DEMAND SIGNAL",
]


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def parse_iso_date(value: str, *, where: str) -> date:
    text = (value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        fail(f"{where}: {value!r} is not a YYYY-MM-DD date")
        raise


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


def parse_int(value: str, *, where: str, allow_blank: bool = True, default: int = 0) -> int:
    text = (value or "").strip().replace(",", "")
    if text == "":
        if allow_blank:
            return default
        fail(f"{where}: value is blank but required")
    try:
        return int(Decimal(text))
    except InvalidOperation:
        fail(f"{where}: {value!r} is not a whole number")
        raise


def decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", "").replace("$", ""))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")


def sqrt_decimal(value: Decimal) -> Decimal:
    if value <= 0:
        return ZERO
    return value.sqrt()


@dataclass
class Supplier:
    supplier_id: str
    supplier_name: str
    lead_time_days: int
    moq_units: int
    case_pack_units: int


@dataclass
class Item:
    sku: str
    product_name: str
    on_hand: Decimal
    on_order: Decimal
    inbound_eta: date | None
    unit_cost: Decimal
    unit_price: Decimal
    supplier_id: str
    daily_units: dict[date, Decimal] = field(default_factory=dict)

    # computed
    velocity: Decimal = ZERO
    velocity_recent: Decimal = ZERO
    velocity_base: Decimal = ZERO
    demand_sigma: Decimal = ZERO
    window_revenue: Decimal = ZERO
    days_of_cover: Decimal | None = None
    stockout_date: date | None = None
    lead_time_days: int = 0
    cover_gap_days: Decimal = ZERO
    replenishment_date: date | None = None
    revenue_at_risk: Decimal = ZERO
    order_up_to: Decimal = ZERO
    raw_order_units: Decimal = ZERO
    recommended_units: int = 0
    recommended_cost: Decimal = ZERO
    excess_units: Decimal = ZERO
    capital_tied: Decimal = ZERO
    status: str = "HEALTHY"
    note: str = ""


def read_csv(path: str, required: list[str], label: str) -> tuple[list[dict[str, str]], list[str]]:
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
    return rows, header


def load_suppliers(path: str | None, default_lead_time: int) -> "OrderedDict[str, Supplier]":
    suppliers: "OrderedDict[str, Supplier]" = OrderedDict()
    if not path:
        return suppliers
    rows, _header = read_csv(path, SUPPLIER_REQUIRED, "supplier")
    for row_number, row in enumerate(rows, start=2):
        where = f"{path} row {row_number}"
        supplier_id = row.get("supplier_id", "")
        if not supplier_id:
            fail(f"{where}: supplier_id is empty")
        if supplier_id in suppliers:
            fail(f"{where}: supplier_id {supplier_id!r} appears more than once")
        lead_time = parse_int(row.get("lead_time_days", ""), where=f"{where} column lead_time_days", allow_blank=False)
        if lead_time < 0:
            fail(f"{where} column lead_time_days: {lead_time} is negative")
        suppliers[supplier_id] = Supplier(
            supplier_id=supplier_id,
            supplier_name=row.get("supplier_name", "") or supplier_id,
            lead_time_days=lead_time,
            moq_units=parse_int(row.get("moq_units", ""), where=f"{where} column moq_units"),
            case_pack_units=max(1, parse_int(row.get("case_pack_units", ""), where=f"{where} column case_pack_units", default=1)),
        )
    _ = default_lead_time
    return suppliers


def load_inventory(path: str) -> "OrderedDict[str, Item]":
    rows, _header = read_csv(path, INVENTORY_REQUIRED, "inventory")
    items: "OrderedDict[str, Item]" = OrderedDict()
    for row_number, row in enumerate(rows, start=2):
        where = f"{path} row {row_number}"
        sku = row.get("sku", "")
        if not sku:
            fail(f"{where}: sku is empty")
        if sku in items:
            fail(f"{where}: sku {sku!r} appears more than once in the inventory snapshot")
        eta_raw = row.get("inbound_eta", "")
        inbound_eta = parse_iso_date(eta_raw, where=f"{where} column inbound_eta") if eta_raw else None
        unit_cost = parse_decimal(row.get("unit_cost", ""), where=f"{where} column unit_cost", allow_blank=False)
        unit_price = parse_decimal(row.get("unit_price", ""), where=f"{where} column unit_price", allow_blank=False)
        if unit_cost < 0 or unit_price < 0:
            fail(f"{where}: unit_cost and unit_price must be 0 or greater")
        items[sku] = Item(
            sku=sku,
            product_name=row.get("product_name", "") or sku,
            on_hand=parse_decimal(row.get("on_hand_units", ""), where=f"{where} column on_hand_units", allow_blank=False),
            on_order=parse_decimal(row.get("on_order_units", ""), where=f"{where} column on_order_units"),
            inbound_eta=inbound_eta,
            unit_cost=unit_cost,
            unit_price=unit_price,
            supplier_id=row.get("supplier_id", ""),
        )
    return items


def load_sales(path: str, items: "OrderedDict[str, Item]", as_of: date) -> tuple[list[str], date, date]:
    rows, _header = read_csv(path, SALES_REQUIRED, "sales")
    unknown: list[str] = []
    seen_unknown: set[str] = set()
    first_date: date | None = None
    last_date: date | None = None
    for row_number, row in enumerate(rows, start=2):
        where = f"{path} row {row_number}"
        sku = row.get("sku", "")
        if not sku:
            fail(f"{where}: sku is empty")
        order_date = parse_iso_date(row.get("order_date", ""), where=f"{where} column order_date")
        units = parse_decimal(row.get("units_sold", ""), where=f"{where} column units_sold", allow_blank=False)
        if order_date > as_of:
            fail(f"{where} column order_date: {order_date.isoformat()} is after --as-of {as_of.isoformat()}")
        first_date = order_date if first_date is None or order_date < first_date else first_date
        last_date = order_date if last_date is None or order_date > last_date else last_date
        item = items.get(sku)
        if item is None:
            if sku not in seen_unknown:
                seen_unknown.add(sku)
                unknown.append(sku)
            continue
        item.daily_units[order_date] = item.daily_units.get(order_date, ZERO) + units
    if first_date is None or last_date is None:
        fail(f"{path} has a header but no usable sales rows")
    return unknown, first_date, last_date


def window_units(item: Item, start: date, end: date) -> Decimal:
    total = ZERO
    for day, units in item.daily_units.items():
        if start <= day <= end:
            total += units
    return total


def daily_series(item: Item, start: date, end: date) -> list[Decimal]:
    series: list[Decimal] = []
    day = start
    while day <= end:
        series.append(item.daily_units.get(day, ZERO))
        day += timedelta(days=1)
    return series


def days_until_empty(units: Decimal, velocity: Decimal) -> int:
    """Whole days until `units` run out at `velocity`; 0 when nothing is sellable."""
    if units <= 0:
        return 0
    return int((units / velocity).to_integral_value(rounding=ROUND_CEILING))


def shortage_note(item: Item, gaps: list[tuple[date, date]], po_eta: date | None, inbound_covers: bool) -> str:
    po = f"inbound PO of {format_units(item.on_order)} landing {po_eta.isoformat()}" if po_eta else ""
    if po_eta is None:
        return f"earliest units land {gaps[-1][1].isoformat()}"
    if inbound_covers:
        return (
            f"{po} only extends cover to {gaps[0][0].isoformat()}; "
            f"a new order lands {gaps[0][1].isoformat()}"
        )
    if len(gaps) == 1:
        return f"earliest units land {po_eta.isoformat()} (inbound PO)"
    return (
        f"empty {gaps[0][0].isoformat()} until the {po}, which runs out {gaps[1][0].isoformat()}; "
        f"a new order lands {gaps[1][1].isoformat()}"
    )


def compute(item: Item, args: argparse.Namespace, as_of: date, suppliers: "OrderedDict[str, Supplier]", z: Decimal) -> None:
    recent_start = as_of - timedelta(days=args.window_days - 1)
    base_start = as_of - timedelta(days=args.base_window_days - 1)

    recent_units = window_units(item, recent_start, as_of)
    base_units = window_units(item, base_start, as_of)
    item.velocity_recent = recent_units / Decimal(args.window_days)
    item.velocity_base = base_units / Decimal(args.base_window_days)
    weight = args.recent_weight / Decimal(100)
    item.velocity = item.velocity_recent * weight + item.velocity_base * (Decimal(1) - weight)
    item.window_revenue = recent_units * item.unit_price

    series = daily_series(item, base_start, as_of)
    count = Decimal(len(series))
    mean = sum(series, ZERO) / count
    variance = sum(((value - mean) ** 2 for value in series), ZERO) / count
    item.demand_sigma = sqrt_decimal(variance)

    supplier = suppliers.get(item.supplier_id)
    item.lead_time_days = supplier.lead_time_days if supplier else args.default_lead_time_days
    lead = Decimal(item.lead_time_days)
    review = Decimal(args.review_days)

    if item.velocity > 0:
        item.days_of_cover = item.on_hand / item.velocity
    else:
        item.days_of_cover = None

    safety_stock = z * item.demand_sigma * sqrt_decimal(lead)
    item.order_up_to = item.velocity * (lead + review) + safety_stock
    item.raw_order_units = item.order_up_to - (item.on_hand + item.on_order)

    # Earliest date a fresh order placed on the as-of date can land.
    fresh_order_arrival = as_of + timedelta(days=item.lead_time_days)
    # An open PO only matters if it has an ETA and lands before a fresh order
    # would. An ETA already in the past is treated as landing on the as-of date.
    po_eta: date | None = None
    if item.on_order > 0 and item.inbound_eta is not None and item.inbound_eta < fresh_order_arrival:
        po_eta = max(item.inbound_eta, as_of)
    item.replenishment_date = po_eta if po_eta is not None else fresh_order_arrival

    # Simulate the shelf day by day at the blended velocity: on-hand depletes,
    # the inbound PO adds its quantity on its ETA, and a fresh order ends any
    # remaining shortage on fresh_order_arrival. Short days are every empty day
    # before that point; a PO lands before the stockout only extends cover by
    # on_order / velocity days, it does not cover the SKU outright.
    gaps: list[tuple[date, date]] = []
    item.stockout_date = None
    inbound_covers = False
    if item.velocity > 0:
        first_empty = as_of + timedelta(days=days_until_empty(item.on_hand, item.velocity))
        if po_eta is not None and po_eta <= first_empty:
            # PO lands while stock remains: one combined pool.
            inbound_covers = True
            first_empty = as_of + timedelta(days=days_until_empty(item.on_hand + item.on_order, item.velocity))
            if first_empty < fresh_order_arrival:
                gaps.append((first_empty, fresh_order_arrival))
        elif po_eta is not None:
            # Shelf empties before the PO lands; the PO then nets any backorder
            # (negative on hand) and lasts on_order / velocity days.
            gaps.append((first_empty, po_eta))
            after_po = item.on_order + min(item.on_hand, ZERO)
            second_empty = po_eta + timedelta(days=days_until_empty(after_po, item.velocity))
            if second_empty < fresh_order_arrival:
                gaps.append((second_empty, fresh_order_arrival))
        elif first_empty < fresh_order_arrival:
            gaps.append((first_empty, fresh_order_arrival))
        item.stockout_date = first_empty
    if gaps:
        item.replenishment_date = gaps[-1][1]
    item.cover_gap_days = Decimal(sum((end - start).days for start, end in gaps))
    item.revenue_at_risk = item.cover_gap_days * item.velocity * item.unit_price

    if item.on_hand < 0:
        item.status = "OVERSOLD"
        item.note = "negative on hand; the storefront has sold units that do not exist"
    elif item.velocity == 0:
        if item.on_hand > 0:
            item.status = "DEAD STOCK" if item.on_hand * item.unit_cost >= args.dead_stock_value else "NO DEMAND SIGNAL"
            item.note = f"no units sold in the last {args.base_window_days} days"
        else:
            item.status = "NO DEMAND SIGNAL"
            item.note = f"no units sold in the last {args.base_window_days} days and nothing on hand"
    elif item.cover_gap_days > 0:
        item.status = "STOCKOUT BEFORE REPLENISHMENT"
        item.note = shortage_note(item, gaps, po_eta, inbound_covers)
    elif item.days_of_cover is not None and item.days_of_cover <= Decimal(item.lead_time_days + args.review_days):
        item.status = "AT RISK"
        item.note = (
            f"covered by the inbound PO landing {item.inbound_eta.isoformat()}"
            if inbound_covers and item.inbound_eta is not None
            else "cover runs out inside lead time plus the review period"
        )
    elif item.days_of_cover is not None and item.days_of_cover >= Decimal(args.overstock_days):
        item.status = "OVERSTOCK"
        item.note = f"more than {args.overstock_days} days of cover on hand"
    else:
        item.status = "HEALTHY"

    if item.status in ("OVERSTOCK", "DEAD STOCK", "NO DEMAND SIGNAL"):
        target_units = item.velocity * Decimal(args.overstock_days)
        item.excess_units = max(ZERO, item.on_hand - target_units)
        item.capital_tied = item.excess_units * item.unit_cost

    case_pack = supplier.case_pack_units if supplier else 1
    moq = supplier.moq_units if supplier else 0
    if item.raw_order_units > 0 and item.status not in ("OVERSTOCK", "DEAD STOCK", "NO DEMAND SIGNAL"):
        units = int(item.raw_order_units.to_integral_value(rounding=ROUND_CEILING))
        if case_pack > 1:
            units = -(-units // case_pack) * case_pack
        if moq and units < moq:
            units = moq
            if case_pack > 1:
                units = -(-units // case_pack) * case_pack
        item.recommended_units = units
        item.recommended_cost = Decimal(units) * item.unit_cost


def format_units(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}"


def money(value: Decimal) -> str:
    return f"{value.quantize(CENTS, rounding=ROUND_HALF_UP):,}"


def trend_label(item: Item) -> str:
    """Recent velocity against the baseline window, as a percent change."""
    if item.velocity_base == 0:
        return "n/a"
    change = (item.velocity_recent / item.velocity_base - Decimal(1)) * Decimal(100)
    return f"{change.quantize(Decimal('1'), rounding=ROUND_HALF_UP):+}%"


def status_rank(item: Item) -> int:
    return STATUS_ORDER.index(item.status)


def render_text(args: argparse.Namespace, as_of: date, items: list[Item], suppliers: "OrderedDict[str, Supplier]", unknown: list[str], sales_span: tuple[date, date], z: Decimal) -> str:
    lines: list[str] = []
    lines.append("INVENTORY STOCKOUT AND OVERSTOCK RISK")
    lines.append(
        f"skus: {len(items)}   sales history: {sales_span[0].isoformat()} to {sales_span[1].isoformat()}   "
        f"as of: {as_of.isoformat()}"
    )
    lines.append("=" * 118)
    lines.append("")
    lines.append("ASSUMPTIONS")
    lines.append(
        f"  velocity = {args.recent_weight}% of the trailing {args.window_days}-day rate "
        f"+ {Decimal(100) - args.recent_weight}% of the trailing {args.base_window_days}-day rate, in units per day"
    )
    lines.append(f"  days of cover = on_hand / velocity; a day with no sales counts as a zero, not a gap")
    lines.append(
        f"  order-up-to = velocity x (lead time + {args.review_days}-day review period) + safety stock"
    )
    lines.append(
        f"  safety stock = z({args.service_level}% service level) = {z.quantize(Decimal('0.0001'))} "
        f"x daily demand sigma x sqrt(lead time)"
    )
    lines.append(f"  default lead time when no supplier row exists: {args.default_lead_time_days} days")
    lines.append(f"  overstock threshold: more than {args.overstock_days} days of cover")
    lines.append("")

    at_risk = [item for item in items if item.status in ("OVERSOLD", "STOCKOUT BEFORE REPLENISHMENT", "AT RISK")]
    at_risk.sort(key=lambda item: (-item.revenue_at_risk, status_rank(item), item.sku))

    lines.append("STOCKOUT RISK, RANKED BY REVENUE EXPOSURE")
    lines.append(
        f"  {'sku':<12}{'on hand':>9}{'on order':>9}{'vel/day':>9}{'trend':>8}{'cover':>8}"
        f"{'stockout':>12}{'lead':>6}{'short':>7}{'rev at risk':>13}  status"
    )
    if not at_risk:
        lines.append("  Nothing is projected to run out inside its lead time.")
    for item in at_risk:
        cover = format_units(item.days_of_cover) if item.days_of_cover is not None else "n/a"
        stockout = item.stockout_date.isoformat() if item.stockout_date else "n/a"
        lines.append(
            f"  {item.sku:<12}{format_units(item.on_hand):>9}{format_units(item.on_order):>9}"
            f"{format_units(item.velocity):>9}{trend_label(item):>8}{cover:>8}{stockout:>12}"
            f"{item.lead_time_days:>6}{format_units(item.cover_gap_days):>7}{money(item.revenue_at_risk):>13}  {item.status}"
        )
    for item in at_risk:
        if item.note:
            lines.append(f"    {item.sku}: {item.note}")
    lines.append(
        "  trend = recent-window velocity against the baseline window."
    )
    lines.append(
        "  short = empty-shelf days before a new order placed today can land; inbound PO units count by quantity"
        " and ETA."
    )
    lines.append(
        "  rev at risk = short days x velocity x unit price, at current selling price and no substitution."
    )
    lines.append("")

    lines.append("RECOMMENDED REPLENISHMENT")
    ordered = [item for item in items if item.recommended_units > 0]
    if not ordered:
        lines.append("  No SKU is below its order-up-to level.")
    else:
        by_supplier: "OrderedDict[str, list[Item]]" = OrderedDict()
        for item in sorted(ordered, key=lambda item: (item.supplier_id, item.sku)):
            by_supplier.setdefault(item.supplier_id, []).append(item)
        grand_total = ZERO
        for supplier_id, group in by_supplier.items():
            supplier = suppliers.get(supplier_id)
            name = supplier.supplier_name if supplier else (supplier_id or "(no supplier on file)")
            detail = (
                f"lead {supplier.lead_time_days}d, MOQ {supplier.moq_units}, case pack {supplier.case_pack_units}"
                if supplier
                else f"lead {args.default_lead_time_days}d (default), no MOQ or case pack on file"
            )
            lines.append(f"  {name} [{supplier_id or '-'}] — {detail}")
            subtotal = ZERO
            for item in group:
                subtotal += item.recommended_cost
                lines.append(
                    f"    {item.sku:<12}{item.recommended_units:>7} units"
                    f"{money(item.recommended_cost):>12} at cost   "
                    f"(order-up-to {format_units(item.order_up_to)}, on hand {format_units(item.on_hand)}, "
                    f"on order {format_units(item.on_order)})"
                )
            grand_total += subtotal
            lines.append(f"    subtotal: {money(subtotal)}")
        lines.append(f"  total purchase order value: {money(grand_total)}")
        lines.append("  Quantities are rounded up to the case pack and raised to the MOQ where one exists.")
    lines.append("")

    lines.append("OVERSTOCK AND DEAD STOCK")
    slow = [item for item in items if item.status in ("OVERSTOCK", "DEAD STOCK", "NO DEMAND SIGNAL") and item.on_hand > 0]
    slow.sort(key=lambda item: (-item.capital_tied, item.sku))
    if not slow:
        lines.append("  Nothing is sitting on more than the overstock threshold.")
    else:
        lines.append(
            f"  {'sku':<12}{'on hand':>9}{'vel/day':>9}{'cover':>9}{'excess':>9}{'capital':>12}  status"
        )
        total_capital = ZERO
        for item in slow:
            cover = format_units(item.days_of_cover) if item.days_of_cover is not None else "no sales"
            total_capital += item.capital_tied
            lines.append(
                f"  {item.sku:<12}{format_units(item.on_hand):>9}{format_units(item.velocity):>9}"
                f"{cover:>9}{format_units(item.excess_units):>9}{money(item.capital_tied):>12}  {item.status}"
            )
        lines.append(f"  working capital in excess units: {money(total_capital)} at landed cost")
        lines.append(
            f"  excess = on hand minus {args.overstock_days} days of demand; SKUs with no sales show their whole on-hand value."
        )
    lines.append("")

    lines.append("SUMMARY")
    counts: "OrderedDict[str, int]" = OrderedDict((status, 0) for status in STATUS_ORDER)
    for item in items:
        counts[item.status] += 1
    for status, count in counts.items():
        if count:
            lines.append(f"  {status:<32}{count:>4} sku(s)")
    total_risk = sum((item.revenue_at_risk for item in items), ZERO)
    total_capital = sum((item.capital_tied for item in items), ZERO)
    lines.append(f"  revenue at risk over the next lead time: {money(total_risk)}")
    lines.append(f"  working capital in excess inventory:     {money(total_capital)}")
    if unknown:
        lines.append("")
        lines.append("DATA QUALITY")
        lines.append(
            f"  {len(unknown)} sku(s) appear in the sales history but not the inventory snapshot "
            f"and were skipped: {', '.join(unknown[:10])}"
            + (f", +{len(unknown) - 10} more" if len(unknown) > 10 else "")
        )
    lines.append("")
    lines.append("=" * 118)
    lines.append(
        "Velocity is history, not a forecast. Promotions, seasonality, and paid spend changes are not modelled; "
        "override velocity-driven calls for any SKU with a known event coming."
    )
    return "\n".join(lines)


def render_json(args: argparse.Namespace, as_of: date, items: list[Item], unknown: list[str], z: Decimal) -> str:
    def dec(value: Decimal | None, places: str = "0.01") -> str | None:
        if value is None:
            return None
        return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))

    payload = {
        "asOf": as_of.isoformat(),
        "assumptions": {
            "windowDays": args.window_days,
            "baseWindowDays": args.base_window_days,
            "recentWeightPct": str(args.recent_weight),
            "reviewDays": args.review_days,
            "serviceLevelPct": str(args.service_level),
            "z": str(z.quantize(Decimal("0.0001"))),
            "defaultLeadTimeDays": args.default_lead_time_days,
            "overstockDays": args.overstock_days,
        },
        "skus": [
            {
                "sku": item.sku,
                "productName": item.product_name,
                "status": item.status,
                "onHandUnits": dec(item.on_hand, "0.001"),
                "onOrderUnits": dec(item.on_order, "0.001"),
                "velocityPerDay": dec(item.velocity, "0.001"),
                "velocityRecentPerDay": dec(item.velocity_recent, "0.001"),
                "velocityBaselinePerDay": dec(item.velocity_base, "0.001"),
                "daysOfCover": dec(item.days_of_cover, "0.1"),
                "stockoutDate": item.stockout_date.isoformat() if item.stockout_date else None,
                "leadTimeDays": item.lead_time_days,
                "shortDays": dec(item.cover_gap_days, "0.1"),
                "earliestReplenishment": item.replenishment_date.isoformat() if item.replenishment_date else None,
                "revenueAtRisk": dec(item.revenue_at_risk),
                "orderUpToUnits": dec(item.order_up_to, "0.001"),
                "recommendedUnits": item.recommended_units,
                "recommendedCost": dec(item.recommended_cost),
                "excessUnits": dec(item.excess_units, "0.001"),
                "capitalTied": dec(item.capital_tied),
            }
            for item in sorted(items, key=lambda item: (status_rank(item), -item.revenue_at_risk, item.sku))
        ],
        "skusMissingFromInventory": unknown,
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Days of cover, stockout exposure, and reorder quantities per SKU.")
    parser.add_argument("--sales", required=True, help="sales history CSV (sku, order_date, units_sold)")
    parser.add_argument("--inventory", required=True, help="on-hand inventory snapshot CSV")
    parser.add_argument("--suppliers", help="supplier CSV with lead times, MOQ, and case pack")
    parser.add_argument("--as-of", required=True, help="snapshot date, YYYY-MM-DD; all windows end here")
    parser.add_argument("--window-days", type=int, default=28, help="recent velocity window (default 28)")
    parser.add_argument("--base-window-days", type=int, default=84, help="baseline velocity window (default 84)")
    parser.add_argument("--recent-weight", type=decimal_arg, default=Decimal("70"), help="percent weight on the recent window (default 70)")
    parser.add_argument("--review-days", type=int, default=14, help="days between purchase order reviews (default 14)")
    parser.add_argument("--service-level", type=decimal_arg, default=Decimal("95"), help="target in-stock service level percent (default 95)")
    parser.add_argument("--default-lead-time-days", type=int, default=30, help="lead time when a SKU has no supplier row (default 30)")
    parser.add_argument("--overstock-days", type=int, default=120, help="days of cover above which stock is overstock (default 120)")
    parser.add_argument("--dead-stock-value", type=decimal_arg, default=Decimal("500"), help="landed value above which a no-sales SKU is called dead stock (default 500)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if args.window_days < 1 or args.base_window_days < 1:
        fail("--window-days and --base-window-days must be 1 or greater")
    if args.base_window_days < args.window_days:
        fail("--base-window-days must be at least --window-days")
    if not (ZERO <= args.recent_weight <= Decimal(100)):
        fail("--recent-weight must be between 0 and 100")
    if not (Decimal("50") <= args.service_level < Decimal("100")):
        fail("--service-level must be at least 50 and below 100")
    if args.review_days < 0 or args.default_lead_time_days < 0:
        fail("--review-days and --default-lead-time-days must be 0 or greater")

    as_of = parse_iso_date(args.as_of, where="--as-of")
    z = Decimal(str(NormalDist().inv_cdf(float(args.service_level) / 100.0)))

    suppliers = load_suppliers(args.suppliers, args.default_lead_time_days)
    items = load_inventory(args.inventory)
    unknown, first_date, last_date = load_sales(args.sales, items, as_of)

    for item in items.values():
        if item.supplier_id and suppliers and item.supplier_id not in suppliers:
            fail(
                f"{args.inventory}: sku {item.sku} references supplier_id {item.supplier_id!r} "
                f"which is not in {args.suppliers}"
            )
        compute(item, args, as_of, suppliers, z)

    ordered_items = list(items.values())
    if args.json:
        print(render_json(args, as_of, ordered_items, unknown, z))
        return 0
    print(render_text(args, as_of, ordered_items, suppliers, unknown, (first_date, last_date), z))
    return 0


if __name__ == "__main__":
    sys.exit(main())
