#!/usr/bin/env python3
"""13-week direct cash forecast.

Builds a weekly direct cash flow from an opening balance, an AR ageing, an AP
ageing, a payroll schedule, and recurring commitments. Reports the weekly
closing balance, runway in weeks, the first week that breaches a minimum cash
covenant, and supports collection, payment, and spend scenario adjustments.

Every input row is either scheduled inside the horizon, reported as falling
outside it, or reported as an expected shortfall. The three buckets are
reconciled back to the file totals; if they do not agree exactly the script
exits non-zero.

Stdlib only, offline, deterministic. The forecast starts on --as-of; nothing
reads the clock.

Exit codes:
    0  the forecast reconciles to the input files
    1  it does not reconcile
    2  bad input (missing column, unparseable value, bad frequency, empty file)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

MONEY = Decimal("0.01")
WEEK = timedelta(days=7)

AR_REQUIRED = ("invoice_id", "customer", "due_date", "amount")
AP_REQUIRED = ("bill_id", "vendor", "due_date", "amount")
PAYROLL_REQUIRED = ("pay_date", "description", "amount")
RECURRING_REQUIRED = ("description", "amount", "frequency", "start_date", "direction")
FREQUENCIES = ("weekly", "biweekly", "monthly", "quarterly", "one_off")
DIRECTIONS = ("inflow", "outflow")


class InputError(Exception):
    """Raised for any malformed input; carries a message naming the field."""


@dataclass
class Item:
    kind: str           # ar | ap | payroll | recurring_in | recurring_out
    label: str
    amount: Decimal     # always positive
    inflow: bool
    when: date
    source_id: str


@dataclass
class Week:
    index: int
    start: date
    end: date
    opening: Decimal = Decimal(0)
    closing: Decimal = Decimal(0)
    items: list[Item] = field(default_factory=list)

    def total(self, kind: str) -> Decimal:
        return sum((item.amount for item in self.items if item.kind == kind), Decimal(0))

    @property
    def inflows(self) -> Decimal:
        return sum((item.amount for item in self.items if item.inflow), Decimal(0))

    @property
    def outflows(self) -> Decimal:
        return sum((item.amount for item in self.items if not item.inflow), Decimal(0))

    @property
    def net(self) -> Decimal:
        return self.inflows - self.outflows


def money(value: Decimal) -> str:
    """Display form: thousands separators, 2 dp, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):,.2f}"


def plain(value: Decimal) -> str:
    """Machine form: 2 dp, no separators, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


def iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InputError(f"{label} value {value!r} is not an ISO date (YYYY-MM-DD)") from None


def decimal_arg(value: str, label: str, *, minimum: Decimal | None = None) -> Decimal:
    try:
        parsed = Decimal(value.replace(",", "").replace("$", ""))
    except InvalidOperation:
        raise InputError(f"{label} value {value!r} is not a number") from None
    if not parsed.is_finite():
        raise InputError(f"{label} value {value!r} is not finite")
    if minimum is not None and parsed < minimum:
        raise InputError(f"{label} must be at least {minimum}")
    return parsed


def read_rows(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputError(f"{path}: file is empty (no header row)")
        header = [name.strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise InputError(f"{path}: missing required column(s): {', '.join(missing)}")
        rows: list[dict[str, str]] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {
                (key.strip() if key else key): (value.strip() if isinstance(value, str) else "")
                for key, value in raw.items()
                if key is not None
            }
            if not any(row.get(name) for name in required):
                continue
            row["__line__"] = str(line_no)
            rows.append(row)
    if not rows:
        raise InputError(f"{path}: no data rows found below the header")
    return rows


def row_decimal(row: dict[str, str], column: str, path: Path, *, positive: bool = True) -> Decimal:
    raw = (row.get(column) or "").strip()
    if raw == "":
        raise InputError(f"{path} line {row['__line__']}: column '{column}' is empty")
    cleaned = raw.replace(",", "").replace("$", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise InputError(
            f"{path} line {row['__line__']}: column '{column}' value {raw!r} is not a number"
        ) from None
    if not value.is_finite():
        raise InputError(f"{path} line {row['__line__']}: column '{column}' value {raw!r} is not finite")
    value = -value if negative else value
    if positive and value < 0:
        raise InputError(
            f"{path} line {row['__line__']}: column '{column}' is negative ({raw}); "
            "enter every amount as a positive magnitude"
        )
    if positive and value == 0:
        raise InputError(f"{path} line {row['__line__']}: column '{column}' is zero; drop the row instead")
    return value


def row_date(row: dict[str, str], column: str, path: Path, *, allow_blank: bool = False) -> date | None:
    raw = (row.get(column) or "").strip()
    if raw == "":
        if allow_blank:
            return None
        raise InputError(f"{path} line {row['__line__']}: column '{column}' is empty")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise InputError(
            f"{path} line {row['__line__']}: column '{column}' value {raw!r} is not an ISO date (YYYY-MM-DD)"
        ) from None


def add_months(anchor: date, months: int) -> date:
    """Same day-of-month, clamped to the end of the target month."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start - timedelta(days=1)).day
    return date(year, month, min(anchor.day, last_day))


# --------------------------------------------------------------------------
# scheduling
# --------------------------------------------------------------------------


@dataclass
class Bucket:
    """Where an input row's money ended up."""

    total: Decimal = Decimal(0)
    in_horizon: Decimal = Decimal(0)
    after_horizon: Decimal = Decimal(0)
    before_horizon: Decimal = Decimal(0)
    not_expected: Decimal = Decimal(0)
    rows: int = 0
    deferred: list[str] = field(default_factory=list)

    @property
    def residual(self) -> Decimal:
        return self.total - (
            self.in_horizon + self.after_horizon + self.before_horizon + self.not_expected
        )


def schedule_ledger(
    rows: list[dict[str, str]],
    path: Path,
    required: tuple[str, ...],
    id_column: str,
    party_column: str,
    kind: str,
    inflow: bool,
    lag_days: int,
    rate: Decimal,
    horizon_start: date,
    horizon_end: date,
) -> tuple[list[Item], Bucket]:
    items: list[Item] = []
    bucket = Bucket()
    seen: dict[str, str] = {}
    for row in rows:
        row_id = row.get(id_column) or ""
        if not row_id:
            raise InputError(f"{path} line {row['__line__']}: column '{id_column}' is empty")
        if row_id in seen:
            raise InputError(
                f"{path} line {row['__line__']}: duplicate {id_column} {row_id!r} "
                f"(first seen on line {seen[row_id]})"
            )
        seen[row_id] = row["__line__"]
        amount = row_decimal(row, "amount", path)
        bucket.total += amount
        bucket.rows += 1

        expected = row_date(row, "expected_date", path, allow_blank=True)
        if expected is None:
            expected = row_date(row, "due_date", path) + timedelta(days=lag_days)
        elif lag_days:
            expected = expected + timedelta(days=lag_days)

        collected = (amount * rate).quantize(MONEY, rounding=ROUND_HALF_UP)
        shortfall = amount - collected
        bucket.not_expected += shortfall
        if collected == 0:
            continue

        if expected < horizon_start:
            # Already due: assume it settles in the first week of the forecast.
            expected = horizon_start
        if expected > horizon_end:
            bucket.after_horizon += collected
            bucket.deferred.append(
                f"{row_id} {row.get(party_column, '')} {money(collected)} expected {expected.isoformat()}"
            )
            continue
        bucket.in_horizon += collected
        items.append(
            Item(
                kind=kind,
                label=f"{row.get(party_column, '')} ({row_id})",
                amount=collected,
                inflow=inflow,
                when=expected,
                source_id=row_id,
            )
        )
    return items, bucket


def schedule_payroll(
    rows: list[dict[str, str]], path: Path, horizon_start: date, horizon_end: date
) -> tuple[list[Item], Bucket]:
    items: list[Item] = []
    bucket = Bucket()
    for row in rows:
        amount = row_decimal(row, "amount", path)
        pay_date = row_date(row, "pay_date", path)
        bucket.total += amount
        bucket.rows += 1
        if pay_date < horizon_start:
            bucket.before_horizon += amount
            continue
        if pay_date > horizon_end:
            bucket.after_horizon += amount
            bucket.deferred.append(
                f"{row.get('description', '')} {money(amount)} on {pay_date.isoformat()}"
            )
            continue
        bucket.in_horizon += amount
        items.append(
            Item("payroll", row.get("description", "payroll"), amount, False, pay_date, row["__line__"])
        )
    return items, bucket


def occurrences(
    frequency: str, start: date, end: date | None, horizon_start: date, horizon_end: date, path: Path, line: str
) -> list[date]:
    dates: list[date] = []
    limit = horizon_end if end is None else min(end, horizon_end)
    if frequency == "one_off":
        return [start] if horizon_start <= start <= limit else []
    step_days = {"weekly": 7, "biweekly": 14}.get(frequency)
    guard = 0
    if step_days is not None:
        cursor = start
        while cursor <= limit and guard < 1000:
            if cursor >= horizon_start:
                dates.append(cursor)
            cursor += timedelta(days=step_days)
            guard += 1
    else:
        step_months = 1 if frequency == "monthly" else 3
        index = 0
        while guard < 1000:
            cursor = add_months(start, index * step_months)
            if cursor > limit:
                break
            if cursor >= horizon_start:
                dates.append(cursor)
            index += 1
            guard += 1
    if guard >= 1000:
        raise InputError(f"{path} line {line}: recurring item generated too many occurrences; check start_date")
    return dates


def schedule_recurring(
    rows: list[dict[str, str]],
    path: Path,
    horizon_start: date,
    horizon_end: date,
    spend_change: Decimal,
) -> tuple[list[Item], Bucket, Bucket]:
    inflow_bucket = Bucket()
    outflow_bucket = Bucket()
    items: list[Item] = []
    for row in rows:
        amount = row_decimal(row, "amount", path)
        frequency = (row.get("frequency") or "").lower()
        if frequency not in FREQUENCIES:
            raise InputError(
                f"{path} line {row['__line__']}: column 'frequency' value {row.get('frequency')!r} "
                f"is not one of {', '.join(FREQUENCIES)}"
            )
        direction = (row.get("direction") or "").lower()
        if direction not in DIRECTIONS:
            raise InputError(
                f"{path} line {row['__line__']}: column 'direction' value {row.get('direction')!r} "
                f"is not one of {', '.join(DIRECTIONS)}"
            )
        start = row_date(row, "start_date", path)
        end = row_date(row, "end_date", path, allow_blank=True)
        if end is not None and end < start:
            raise InputError(f"{path} line {row['__line__']}: 'end_date' is before 'start_date'")

        inflow = direction == "inflow"
        applied = amount
        if not inflow and spend_change != 0:
            applied = (amount * (1 + spend_change)).quantize(MONEY, rounding=ROUND_HALF_UP)

        dates = occurrences(frequency, start, end, horizon_start, horizon_end, path, row["__line__"])
        bucket = inflow_bucket if inflow else outflow_bucket
        bucket.rows += 1
        bucket.total += applied * len(dates)
        bucket.in_horizon += applied * len(dates)
        for occurrence in dates:
            items.append(
                Item(
                    "recurring_in" if inflow else "recurring_out",
                    row.get("description", "recurring"),
                    applied,
                    inflow,
                    occurrence,
                    row["__line__"],
                )
            )
    return items, inflow_bucket, outflow_bucket


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def render_table(headers: list[str], rows: list[list[str]], aligns: str) -> list[str]:
    widths = [len(head) for head in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def fmt(cells: list[str]) -> str:
        parts = [
            cell.rjust(widths[index]) if aligns[index] == "r" else cell.ljust(widths[index])
            for index, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    return [fmt(headers), "  ".join("-" * width for width in widths)] + [fmt(row) for row in rows]


def build_report(
    weeks: list[Week],
    buckets: dict[str, Bucket],
    opening: Decimal,
    minimum_cash: Decimal,
    as_of: date,
    scenario: dict[str, str],
    detail: bool,
) -> tuple[list[str], dict, bool]:
    out: list[str] = []
    horizon_end = weeks[-1].end
    closing = weeks[-1].closing
    total_in = sum((week.inflows for week in weeks), Decimal(0))
    total_out = sum((week.outflows for week in weeks), Decimal(0))

    out.append("13-WEEK DIRECT CASH FORECAST" if len(weeks) == 13 else f"{len(weeks)}-WEEK DIRECT CASH FORECAST")
    out.append("=" * 104)
    out.append(f"Forecast start   : {as_of.isoformat()} (week 1 runs {as_of.isoformat()} to {weeks[0].end.isoformat()})")
    out.append(f"Horizon ends     : {horizon_end.isoformat()}")
    out.append(f"Opening balance  : {money(opening)}")
    out.append(f"Minimum cash     : {money(minimum_cash)}")
    out.append(
        "Scenario         : "
        + ", ".join(f"{key} {value}" for key, value in scenario.items())
    )
    out.append("")

    out.append("WEEKLY CASH FLOW")
    out.append("-" * 104)
    rows = []
    for week in weeks:
        rows.append(
            [
                str(week.index),
                week.start.isoformat(),
                money(week.opening),
                money(week.total("ar")),
                money(week.total("recurring_in")),
                money(week.total("payroll")),
                money(week.total("ap")),
                money(week.total("recurring_out")),
                money(week.net),
                money(week.closing),
                "BREACH" if week.closing < minimum_cash else ("" if week.closing >= 0 else "NEGATIVE"),
            ]
        )
    out.extend(
        render_table(
            ["wk", "start", "opening", "ar in", "other in", "payroll", "ap out", "other out", "net", "closing", "flag"],
            rows,
            "llrrrrrrrrl",
        )
    )
    out.append("")

    if detail:
        out.append("SCHEDULED ITEMS BY WEEK")
        out.append("-" * 104)
        for week in weeks:
            out.append(f"Week {week.index} ({week.start.isoformat()} to {week.end.isoformat()})")
            ordered = sorted(week.items, key=lambda item: (item.when, item.kind, item.label, item.source_id))
            if not ordered:
                out.append("  (no scheduled movements)")
            for item in ordered:
                sign = "+" if item.inflow else "-"
                out.append(f"  {item.when.isoformat()}  {sign}{money(item.amount):>14}  {item.kind:<13}  {item.label}")
            out.append("")

    out.append("RUNWAY AND COVENANT")
    out.append("-" * 104)
    breach = next((week for week in weeks if week.closing < minimum_cash), None)
    negative = next((week for week in weeks if week.closing < 0), None)
    lowest = min(weeks, key=lambda week: (week.closing, week.index))

    summary_rows = [
        ["Closing balance at end of horizon", money(closing)],
        ["Total inflows over horizon", money(total_in)],
        ["Total outflows over horizon", money(total_out)],
        ["Net movement over horizon", money(total_in - total_out)],
        ["Lowest weekly closing", f"{money(lowest.closing)} in week {lowest.index} ({lowest.start.isoformat()})"],
    ]
    if breach is not None:
        summary_rows.append(
            [
                "First covenant breach",
                f"week {breach.index} ({breach.start.isoformat()}), short by "
                f"{money(minimum_cash - breach.closing)}",
            ]
        )
    else:
        summary_rows.append(["First covenant breach", f"none within {len(weeks)} weeks"])
    if negative is not None:
        summary_rows.append(
            ["Cash runs out", f"week {negative.index} ({negative.start.isoformat()})"]
        )
        runway_weeks = str(negative.index - 1)
        runway_basis = "weeks of positive cash inside the horizon"
    else:
        tail = weeks[-4:] if len(weeks) >= 4 else weeks
        tail_net = sum((week.net for week in tail), Decimal(0)) / len(tail)
        if tail_net < 0:
            extra = closing / -tail_net
            runway_weeks = f"{len(weeks)} + {extra.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} (extrapolated)"
            runway_basis = (
                f"last {len(tail)} weeks average net {money(tail_net)} per week, assumed to repeat"
            )
            summary_rows.append(
                [
                    "Cash runs out",
                    f"not inside the horizon; extrapolated at week "
                    f"{(Decimal(len(weeks)) + extra).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}",
                ]
            )
        else:
            runway_weeks = "beyond the horizon"
            runway_basis = "net cash flow over the last weeks of the horizon is not negative"
            summary_rows.append(["Cash runs out", "not inside the horizon; recent weeks are cash positive"])
    summary_rows.append(["Runway", f"{runway_weeks} ({runway_basis})"])
    out.extend(render_table(["measure", "value"], summary_rows, "ll"))
    out.append("")

    out.append("COMPLETENESS AND RECONCILIATION")
    out.append("-" * 104)
    rows = []
    for name in ("ar", "ap", "payroll", "recurring_in", "recurring_out"):
        bucket = buckets[name]
        rows.append(
            [
                name,
                str(bucket.rows),
                money(bucket.total),
                money(bucket.in_horizon),
                money(bucket.after_horizon),
                money(bucket.before_horizon),
                money(bucket.not_expected),
                money(bucket.residual),
            ]
        )
    out.extend(
        render_table(
            ["source", "rows", "file total", "in horizon", "after horizon", "before start", "not expected", "residual"],
            rows,
            "lrrrrrrr",
        )
    )
    out.append("")
    walk_rows = [
        ["opening balance", money(opening)],
        ["plus inflows scheduled in horizon", money(total_in)],
        ["less outflows scheduled in horizon", money(-total_out)],
        ["= closing balance at end of horizon", money(opening + total_in - total_out)],
        ["closing balance per the weekly table", money(closing)],
        ["residual (must be 0.00)", money(opening + total_in - total_out - closing)],
    ]
    out.extend(render_table(["check", "amount"], walk_rows, "lr"))
    out.append("")

    deferred_lines: list[str] = []
    for name in ("ar", "ap", "payroll"):
        for entry in buckets[name].deferred:
            deferred_lines.append(f"  {name}: {entry}")
    if deferred_lines:
        out.append("ITEMS OUTSIDE THE HORIZON (not in the forecast above)")
        out.append("-" * 104)
        out.extend(deferred_lines)
        out.append("")

    if buckets["ar"].not_expected != 0:
        out.append(
            f"Expected AR shortfall from the collection rate: {money(buckets['ar'].not_expected)} "
            "is assumed never to arrive."
        )
        out.append("")

    out.append("VERDICT")
    out.append("-" * 104)
    failures = []
    for name, bucket in buckets.items():
        if bucket.residual != 0:
            failures.append(f"{name} residual {money(bucket.residual)}")
    walk_residual = opening + total_in - total_out - closing
    if walk_residual != 0:
        failures.append(f"cash walk residual {money(walk_residual)}")
    if failures:
        out.append("DOES NOT RECONCILE: " + "; ".join(failures) + ".")
        out.append("Some input rows are unaccounted for. Do not circulate this forecast. Exit status 1.")
    else:
        out.append(
            "Every input row is scheduled, deferred beyond the horizon, or reported as not expected. "
            "Exit status 0."
        )
        if breach is not None:
            out.append(
                f"Action required: the minimum cash covenant of {money(minimum_cash)} is breached in week "
                f"{breach.index} ({breach.start.isoformat()})."
            )

    payload = {
        "asOf": as_of.isoformat(),
        "horizonEnd": horizon_end.isoformat(),
        "openingBalance": plain(opening),
        "minimumCash": plain(minimum_cash),
        "scenario": scenario,
        "weeks": [
            {
                "week": week.index,
                "start": week.start.isoformat(),
                "end": week.end.isoformat(),
                "opening": plain(week.opening),
                "arIn": plain(week.total("ar")),
                "otherIn": plain(week.total("recurring_in")),
                "payroll": plain(week.total("payroll")),
                "apOut": plain(week.total("ap")),
                "otherOut": plain(week.total("recurring_out")),
                "net": plain(week.net),
                "closing": plain(week.closing),
                "belowMinimum": week.closing < minimum_cash,
            }
            for week in weeks
        ],
        "closingBalance": plain(closing),
        "lowestWeek": {"week": lowest.index, "closing": plain(lowest.closing)},
        "firstBreachWeek": breach.index if breach is not None else None,
        "cashOutWeek": negative.index if negative is not None else None,
        "runway": runway_weeks,
        "runwayBasis": runway_basis,
        "completeness": {
            name: {
                "rows": bucket.rows,
                "fileTotal": plain(bucket.total),
                "inHorizon": plain(bucket.in_horizon),
                "afterHorizon": plain(bucket.after_horizon),
                "beforeStart": plain(bucket.before_horizon),
                "notExpected": plain(bucket.not_expected),
                "residual": plain(bucket.residual),
            }
            for name, bucket in buckets.items()
        },
        "reconciles": not failures,
    }
    return out, payload, not failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="13-week direct cash forecast with runway and covenant tests.")
    parser.add_argument("--opening-balance", required=True, help="cash on hand at --as-of")
    parser.add_argument("--as-of", required=True, help="first day of week 1, YYYY-MM-DD")
    parser.add_argument("--ar", help="AR ageing CSV")
    parser.add_argument("--ap", help="AP ageing CSV")
    parser.add_argument("--payroll", help="payroll schedule CSV")
    parser.add_argument("--recurring", help="recurring commitments CSV")
    parser.add_argument("--weeks", type=int, default=13, help="forecast horizon in weeks (default 13)")
    parser.add_argument("--minimum-cash", default="0", help="minimum cash covenant (default 0)")
    parser.add_argument("--collection-lag-days", type=int, default=0, help="days added to every AR due date")
    parser.add_argument("--collection-rate", default="100", help="percent of AR expected to be collected (default 100)")
    parser.add_argument("--payment-lag-days", type=int, default=0, help="days added to every AP due date")
    parser.add_argument(
        "--spend-change-pct", default="0", help="percent change applied to recurring outflows (default 0)"
    )
    parser.add_argument("--detail", action="store_true", help="list every scheduled item under its week")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the report")
    args = parser.parse_args(argv)

    try:
        as_of = iso_date(args.as_of, "--as-of")
        if args.weeks < 1 or args.weeks > 104:
            raise InputError("--weeks must be between 1 and 104")
        opening = decimal_arg(args.opening_balance, "--opening-balance")
        minimum_cash = decimal_arg(args.minimum_cash, "--minimum-cash")
        collection_rate = decimal_arg(args.collection_rate, "--collection-rate", minimum=Decimal(0)) / 100
        if collection_rate > 1:
            raise InputError("--collection-rate must be between 0 and 100")
        spend_change = decimal_arg(args.spend_change_pct, "--spend-change-pct", minimum=Decimal(-100)) / 100
        if not any((args.ar, args.ap, args.payroll, args.recurring)):
            raise InputError("supply at least one of --ar, --ap, --payroll, --recurring")

        horizon_start = as_of
        horizon_end = as_of + WEEK * args.weeks - timedelta(days=1)

        items: list[Item] = []
        buckets: dict[str, Bucket] = {}

        if args.ar:
            ar_items, ar_bucket = schedule_ledger(
                read_rows(Path(args.ar), AR_REQUIRED), Path(args.ar), AR_REQUIRED, "invoice_id", "customer",
                "ar", True, args.collection_lag_days, collection_rate, horizon_start, horizon_end,
            )
            items.extend(ar_items)
            buckets["ar"] = ar_bucket
        else:
            buckets["ar"] = Bucket()

        if args.ap:
            ap_items, ap_bucket = schedule_ledger(
                read_rows(Path(args.ap), AP_REQUIRED), Path(args.ap), AP_REQUIRED, "bill_id", "vendor",
                "ap", False, args.payment_lag_days, Decimal(1), horizon_start, horizon_end,
            )
            items.extend(ap_items)
            buckets["ap"] = ap_bucket
        else:
            buckets["ap"] = Bucket()

        if args.payroll:
            payroll_items, payroll_bucket = schedule_payroll(
                read_rows(Path(args.payroll), PAYROLL_REQUIRED), Path(args.payroll), horizon_start, horizon_end
            )
            items.extend(payroll_items)
            buckets["payroll"] = payroll_bucket
        else:
            buckets["payroll"] = Bucket()

        if args.recurring:
            recurring_items, in_bucket, out_bucket = schedule_recurring(
                read_rows(Path(args.recurring), RECURRING_REQUIRED), Path(args.recurring),
                horizon_start, horizon_end, spend_change,
            )
            items.extend(recurring_items)
            buckets["recurring_in"] = in_bucket
            buckets["recurring_out"] = out_bucket
        else:
            buckets["recurring_in"] = Bucket()
            buckets["recurring_out"] = Bucket()

        weeks = [
            Week(index=index + 1, start=as_of + WEEK * index, end=as_of + WEEK * (index + 1) - timedelta(days=1))
            for index in range(args.weeks)
        ]
        for item in items:
            offset = (item.when - as_of).days // 7
            weeks[offset].items.append(item)

        balance = opening
        for week in weeks:
            week.opening = balance
            balance = balance + week.net
            week.closing = balance

        scenario = {
            "collection lag": f"{args.collection_lag_days}d",
            "collection rate": f"{args.collection_rate}%",
            "payment lag": f"{args.payment_lag_days}d",
            "recurring spend change": f"{args.spend_change_pct}%",
        }
        report, payload, reconciles = build_report(
            weeks, buckets, opening, minimum_cash, as_of, scenario, args.detail
        )
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("\n".join(report))
    sys.stdout.flush()
    return 0 if reconciles else 1


if __name__ == "__main__":
    sys.exit(main())
