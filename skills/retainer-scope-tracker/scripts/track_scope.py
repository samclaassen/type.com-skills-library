#!/usr/bin/env python3
"""Track delivered work against contracted retainer scope for one month.

Computes hours variance, deliverable-level scope creep, out-of-scope work,
effective hourly rate, delivery cost and margin per client, then ranks which
retainers to renegotiate and which clients are being under-served.

Stdlib only. No network access. Deterministic: the reporting period comes from
--month and renewal timing from --as-of.

Usage:
    python3 scripts/track_scope.py \
        --contracts examples/data/contracts.csv \
        --scope examples/data/scope.csv \
        --time-entries examples/data/time_entries.csv \
        --roster examples/data/roster.csv \
        --deliverables examples/data/deliverables.csv \
        --month 2026-09 \
        --as-of 2026-10-05
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")

CONTRACT_COLUMNS = ["client", "month", "retainer_fee", "contracted_hours", "renewal_date"]
SCOPE_COLUMNS = ["client", "month", "deliverable", "contracted_qty", "contracted_hours"]
TIME_COLUMNS = ["client", "date", "person", "deliverable", "hours"]
ROSTER_COLUMNS = ["person", "role", "cost_per_hour"]
DELIVERABLE_COLUMNS = ["client", "date", "deliverable", "qty_delivered"]

FLAG_LABELS = {
    "scope_creep": "SCOPE CREEP",
    "under_delivered": "UNDER-DELIVERED",
    "margin": "MARGIN",
    "rate": "RATE",
    "unscoped": "UNSCOPED WORK",
    "no_delivery": "NO DELIVERY",
}


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
            rows.append(
                (
                    {
                        (key or "").strip(): (value.strip() if isinstance(value, str) else "")
                        for key, value in raw.items()
                    },
                    line_no,
                )
            )
        if not rows:
            fail(f"{label}: {path} has a header but no data rows")
        return rows


def need_text(row: dict[str, str], column: str, path: str, line_no: int) -> str:
    value = row.get(column, "")
    if not value:
        fail(f"{path} line {line_no}: column '{column}' is empty")
    return value


def need_decimal(row, column, path, line_no, allow_zero: bool = True) -> Decimal:
    value = row.get(column, "")
    if value == "":
        fail(f"{path} line {line_no}: column '{column}' is empty, expected a number")
    try:
        number = Decimal(value.replace(",", "").replace("$", ""))
    except InvalidOperation:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a number")
    if number < ZERO:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a value >= 0")
    if not allow_zero and number == ZERO:
        fail(f"{path} line {line_no}: column '{column}' is 0, expected a value above 0")
    return number


def need_date(row, column, path, line_no) -> date:
    value = need_text(row, column, path, line_no)
    if not DATE_RE.match(value):
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', not a real calendar date")


# ---------------------------------------------------------------------- format


def money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"-${abs(whole):,}" if whole < 0 else f"${whole:,}"


def money_cents(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    cents = value.quantize(CENT, rounding=ROUND_HALF_UP)
    return f"-${abs(cents):,.2f}" if cents < 0 else f"${cents:,.2f}"


def hours(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return f"{quantized.to_integral_value()}"
    return f"{quantized}"


def percent(value: Decimal | None, places: str = "0.1") -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP)}%"


def signed_percent(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{'+' if rounded >= 0 else ''}{rounded}%"


def signed_hours(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded == ZERO:
        return "0"
    return f"{'+' if rounded > ZERO else ''}{hours(value)}"


def divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    return numerator / denominator


def round_up_to(value: Decimal, step: Decimal) -> Decimal:
    if step <= ZERO:
        return value
    return (value / step).quantize(Decimal("1"), rounding=ROUND_CEILING) * step


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def month_label(month: str) -> str:
    return f"{calendar.month_name[int(month[5:])]} {month[:4]}"


# --------------------------------------------------------------------- loading


def load_roster(path: str) -> dict[str, dict]:
    rows = read_table(path, ROSTER_COLUMNS, "roster")
    roster: dict[str, dict] = {}
    for row, line_no in rows:
        person = need_text(row, "person", path, line_no)
        if person in roster:
            fail(f"{path} line {line_no}: duplicate person '{person}'")
        roster[person] = {
            "role": need_text(row, "role", path, line_no),
            "cost_per_hour": need_decimal(row, "cost_per_hour", path, line_no, allow_zero=False),
        }
    return roster


def load_contracts(path: str, month: str) -> dict[str, dict]:
    rows = read_table(path, CONTRACT_COLUMNS, "contracts")
    contracts: dict[str, dict] = {}
    for row, line_no in rows:
        row_month = need_text(row, "month", path, line_no)
        if not MONTH_RE.match(row_month):
            fail(f"{path} line {line_no}: column 'month' is '{row_month}', expected YYYY-MM")
        if row_month != month:
            continue
        client = need_text(row, "client", path, line_no)
        if client in contracts:
            fail(f"{path} line {line_no}: duplicate contract row for '{client}' in {month}")
        contracts[client] = {
            "retainer_fee": need_decimal(row, "retainer_fee", path, line_no, allow_zero=False),
            "contracted_hours": need_decimal(row, "contracted_hours", path, line_no),
            "renewal_date": need_date(row, "renewal_date", path, line_no),
            "line_no": line_no,
        }
    if not contracts:
        fail(f"contracts: {path} has no rows for month {month}")
    return contracts


def load_scope(path: str, month: str, contracts: dict[str, dict]) -> dict[str, dict[str, dict]]:
    rows = read_table(path, SCOPE_COLUMNS, "scope")
    scope: dict[str, dict[str, dict]] = {client: {} for client in contracts}
    for row, line_no in rows:
        row_month = need_text(row, "month", path, line_no)
        if not MONTH_RE.match(row_month):
            fail(f"{path} line {line_no}: column 'month' is '{row_month}', expected YYYY-MM")
        if row_month != month:
            continue
        client = need_text(row, "client", path, line_no)
        if client not in contracts:
            fail(
                f"{path} line {line_no}: client '{client}' has no contract row for {month} in the "
                "contracts file"
            )
        deliverable = need_text(row, "deliverable", path, line_no)
        if deliverable in scope[client]:
            fail(
                f"{path} line {line_no}: duplicate scope row for '{client}' / '{deliverable}' "
                f"in {month}"
            )
        scope[client][deliverable] = {
            "contracted_qty": need_decimal(row, "contracted_qty", path, line_no),
            "contracted_hours": need_decimal(row, "contracted_hours", path, line_no),
        }
    return scope


def load_time_entries(path: str, month: str, contracts: dict[str, dict], roster: dict[str, dict]):
    rows = read_table(path, TIME_COLUMNS, "time entries")
    entries = []
    for row, line_no in rows:
        entry_date = need_date(row, "date", path, line_no)
        if entry_date.strftime("%Y-%m") != month:
            continue
        client = need_text(row, "client", path, line_no)
        if client not in contracts:
            fail(
                f"{path} line {line_no}: client '{client}' has no contract row for {month} in the "
                "contracts file"
            )
        person = need_text(row, "person", path, line_no)
        if person not in roster:
            fail(f"{path} line {line_no}: person '{person}' is not in the roster file")
        entries.append(
            {
                "client": client,
                "date": entry_date,
                "person": person,
                "deliverable": need_text(row, "deliverable", path, line_no),
                "hours": need_decimal(row, "hours", path, line_no),
            }
        )
    if not entries:
        fail(f"time entries: {path} has no rows dated inside {month}")
    return entries


def load_deliverables(path: str, month: str, contracts: dict[str, dict]) -> dict[str, dict[str, Decimal]]:
    rows = read_table(path, DELIVERABLE_COLUMNS, "deliverables")
    delivered: dict[str, dict[str, Decimal]] = {client: {} for client in contracts}
    for row, line_no in rows:
        entry_date = need_date(row, "date", path, line_no)
        if entry_date.strftime("%Y-%m") != month:
            continue
        client = need_text(row, "client", path, line_no)
        if client not in contracts:
            fail(
                f"{path} line {line_no}: client '{client}' has no contract row for {month} in the "
                "contracts file"
            )
        deliverable = need_text(row, "deliverable", path, line_no)
        qty = need_decimal(row, "qty_delivered", path, line_no)
        delivered[client][deliverable] = delivered[client].get(deliverable, ZERO) + qty
    return delivered


# -------------------------------------------------------------------- analysis


def analyze_client(
    client: str,
    contract: dict,
    scope: dict[str, dict],
    entries: list[dict],
    delivered: dict[str, Decimal],
    roster: dict[str, dict],
    settings: dict,
) -> dict:
    fee = contract["retainer_fee"]
    contracted_hours = contract["contracted_hours"]

    hours_by_deliverable: dict[str, Decimal] = {}
    cost_by_deliverable: dict[str, Decimal] = {}
    hours_by_person: dict[str, Decimal] = {}
    total_hours = ZERO
    total_cost = ZERO
    for entry in entries:
        rate = roster[entry["person"]]["cost_per_hour"]
        cost = entry["hours"] * rate
        hours_by_deliverable[entry["deliverable"]] = (
            hours_by_deliverable.get(entry["deliverable"], ZERO) + entry["hours"]
        )
        cost_by_deliverable[entry["deliverable"]] = (
            cost_by_deliverable.get(entry["deliverable"], ZERO) + cost
        )
        hours_by_person[entry["person"]] = hours_by_person.get(entry["person"], ZERO) + entry["hours"]
        total_hours += entry["hours"]
        total_cost += cost

    margin_dollars = fee - total_cost
    margin_pct = divide(margin_dollars, fee)
    margin_pct = None if margin_pct is None else margin_pct * HUNDRED
    effective_rate = divide(fee, total_hours)
    contracted_rate = divide(fee, contracted_hours)
    blended_cost_rate = divide(total_cost, total_hours)
    hours_variance = total_hours - contracted_hours
    hours_variance_pct = divide(hours_variance, contracted_hours)
    hours_variance_pct = None if hours_variance_pct is None else hours_variance_pct * HUNDRED

    lines = []
    for name in sorted(set(scope) | set(hours_by_deliverable) | set(delivered)):
        contracted = scope.get(name)
        lines.append(
            {
                "deliverable": name,
                "in_scope": contracted is not None,
                "contracted_qty": None if contracted is None else contracted["contracted_qty"],
                "delivered_qty": delivered.get(name, ZERO),
                "contracted_hours": None if contracted is None else contracted["contracted_hours"],
                "actual_hours": hours_by_deliverable.get(name, ZERO),
                "actual_cost": cost_by_deliverable.get(name, ZERO),
            }
        )

    unscoped = [line for line in lines if not line["in_scope"] and line["actual_hours"] > ZERO]
    unscoped_hours = sum((line["actual_hours"] for line in unscoped), ZERO)
    unscoped_cost = sum((line["actual_cost"] for line in unscoped), ZERO)
    over_qty = [
        line
        for line in lines
        if line["in_scope"]
        and line["contracted_qty"] is not None
        and line["delivered_qty"] > line["contracted_qty"]
    ]
    undelivered = [
        line
        for line in lines
        if line["in_scope"]
        and line["contracted_qty"] is not None
        and line["delivered_qty"] < line["contracted_qty"]
    ]

    flags: list[str] = []
    if total_hours == ZERO:
        flags.append("no_delivery")
    if (
        hours_variance_pct is not None
        and hours_variance_pct > settings["over_tolerance"]
        and total_hours > ZERO
    ):
        flags.append("scope_creep")
    if (
        hours_variance_pct is not None
        and hours_variance_pct < -settings["under_tolerance"]
        and total_hours > ZERO
    ):
        flags.append("under_delivered")
    if margin_pct is not None and margin_pct < settings["target_margin"]:
        flags.append("margin")
    if (
        effective_rate is not None
        and contracted_rate is not None
        and effective_rate
        < contracted_rate * (Decimal("1") - settings["rate_drop"] / HUNDRED)
    ):
        flags.append("rate")
    if unscoped_hours > ZERO:
        flags.append("unscoped")

    # Two independent levers, reported side by side so the recommendation is a
    # choice rather than a reflex price increase.
    scope_back_cost = (
        None if blended_cost_rate is None else blended_cost_rate * contracted_hours
    )
    scope_back_margin_pct = None
    if scope_back_cost is not None:
        scope_back_margin = divide(fee - scope_back_cost, fee)
        scope_back_margin_pct = None if scope_back_margin is None else scope_back_margin * HUNDRED
    target_fraction = Decimal("1") - settings["target_margin"] / HUNDRED
    repriced_fee = (
        None
        if target_fraction <= ZERO
        else round_up_to(total_cost / target_fraction, settings["round_to"])
    )
    target_margin_dollars = fee * settings["target_margin"] / HUNDRED
    monthly_gap = target_margin_dollars - margin_dollars

    return {
        "client": client,
        "fee": fee,
        "contracted_hours": contracted_hours,
        "delivered_hours": total_hours,
        "hours_variance": hours_variance,
        "hours_variance_pct": hours_variance_pct,
        "cost": total_cost,
        "margin_dollars": margin_dollars,
        "margin_pct": margin_pct,
        "effective_rate": effective_rate,
        "contracted_rate": contracted_rate,
        "blended_cost_rate": blended_cost_rate,
        "hours_by_person": hours_by_person,
        "lines": lines,
        "unscoped": unscoped,
        "unscoped_hours": unscoped_hours,
        "unscoped_cost": unscoped_cost,
        "over_qty": over_qty,
        "undelivered": undelivered,
        "flags": flags,
        "scope_back_cost": scope_back_cost,
        "scope_back_margin_pct": scope_back_margin_pct,
        "repriced_fee": repriced_fee,
        "monthly_gap": monthly_gap,
        "annual_gap": monthly_gap * Decimal("12"),
        "renewal_date": contract["renewal_date"],
    }


def renegotiation_note(result: dict, settings: dict, as_of: date) -> list[str]:
    notes = []
    notes.append(
        f"Current deal: {money(result['fee'])} for {hours(result['contracted_hours'])}h "
        f"({money_cents(result['contracted_rate'])}/h contracted). Delivered "
        f"{hours(result['delivered_hours'])}h at an effective "
        f"{money_cents(result['effective_rate'])}/h."
    )
    notes.append(
        f"Margin {money(result['margin_dollars'])} ({percent(result['margin_pct'])}) against a "
        f"{percent(settings['target_margin'], '0.1')} target — {money(result['monthly_gap'])} per "
        f"month, {money(result['annual_gap'])} annualized."
    )
    if result["scope_back_margin_pct"] is not None:
        sufficient = result["scope_back_margin_pct"] >= settings["target_margin"]
        notes.append(
            f"Lever 1, hold the line on scope: delivering exactly "
            f"{hours(result['contracted_hours'])}h at this team's "
            f"{money_cents(result['blended_cost_rate'])}/h blended cost costs "
            f"{money(result['scope_back_cost'])} and returns "
            f"{percent(result['scope_back_margin_pct'])} margin — "
            + (
                "enough on its own, so fix delivery discipline before touching price."
                if sufficient
                else "still short of target, so the contract itself is underpriced."
            )
        )
    if result["repriced_fee"] is not None:
        notes.append(
            f"Lever 2, reprice at current delivery: {money(result['repriced_fee'])} per month "
            f"holds a {percent(settings['target_margin'], '0.1')} margin on "
            f"{hours(result['delivered_hours'])}h."
        )
    if result["unscoped_hours"] > ZERO:
        names = ", ".join(sorted(line["deliverable"] for line in result["unscoped"]))
        notes.append(
            f"Out of scope this month: {hours(result['unscoped_hours'])}h "
            f"({money(result['unscoped_cost'])} of cost) on {names}. Either add it to the scope "
            "at a price or stop doing it."
        )
    lead = as_of + timedelta(days=settings["lead_days"])
    renewal = result["renewal_date"]
    if renewal <= as_of:
        timing = f"The contract renewed or lapsed on {renewal.isoformat()} — raise this now."
    elif renewal <= lead:
        timing = (
            f"Renewal {renewal.isoformat()} is inside the {settings['lead_days']}-day notice "
            "window — this conversation is already late."
        )
    else:
        timing = (
            f"Renewal {renewal.isoformat()}; open the conversation by "
            f"{(renewal - timedelta(days=settings['lead_days'])).isoformat()}."
        )
    notes.append(timing)
    return notes


# ---------------------------------------------------------------------- report


def render(ctx: dict) -> str:
    out: list[str] = []
    add = out.append
    settings = ctx["settings"]

    add(f"# Retainer scope and margin — {month_label(ctx['month'])}")
    add("")
    add(
        f"{len(ctx['results'])} clients | {money(ctx['total_fee'])} in fees | "
        f"{hours(ctx['total_hours'])} of {hours(ctx['total_contracted'])} contracted hours "
        f"delivered ({signed_percent(ctx['total_hours_variance_pct'])}) | blended margin "
        f"{percent(ctx['blended_margin_pct'])} against a "
        f"{percent(settings['target_margin'], '0.1')} target"
    )
    add("")

    add("## Portfolio")
    add("")
    add(
        "| Client | Fee | Scoped h | Delivered h | Var | Effective rate | Cost | Margin | Margin % | Flags |"
    )
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for result in ctx["results"]:
        flags = ", ".join(FLAG_LABELS[flag] for flag in result["flags"]) or "—"
        add(
            f"| {result['client']} | {money(result['fee'])} | "
            f"{hours(result['contracted_hours'])} | {hours(result['delivered_hours'])} | "
            f"{signed_hours(result['hours_variance'])} | "
            f"{money_cents(result['effective_rate'])} | {money(result['cost'])} | "
            f"{money(result['margin_dollars'])} | {percent(result['margin_pct'])} | {flags} |"
        )
    add(
        f"| **Portfolio** | **{money(ctx['total_fee'])}** | "
        f"**{hours(ctx['total_contracted'])}** | **{hours(ctx['total_hours'])}** | "
        f"**{signed_hours(ctx['total_hours'] - ctx['total_contracted'])}** | "
        f"**{money_cents(ctx['portfolio_effective_rate'])}** | **{money(ctx['total_cost'])}** | "
        f"**{money(ctx['total_margin'])}** | **{percent(ctx['blended_margin_pct'])}** | |"
    )
    add("")

    add("## Scope variance detail")
    add("")
    flagged = [result for result in ctx["results"] if result["flags"]]
    if not flagged:
        add("Every client delivered inside tolerance on hours, scope and margin.")
        add("")
    for result in flagged:
        add(f"### {result['client']}")
        add("")
        add("| Deliverable | Scoped qty | Delivered qty | Scoped h | Actual h | Var |")
        add("| --- | ---: | ---: | ---: | ---: | ---: |")
        for line in result["lines"]:
            if line["in_scope"]:
                variance = signed_hours(line["actual_hours"] - line["contracted_hours"])
                add(
                    f"| {line['deliverable']} | {hours(line['contracted_qty'])} | "
                    f"{hours(line['delivered_qty'])} | {hours(line['contracted_hours'])} | "
                    f"{hours(line['actual_hours'])} | {variance} |"
                )
            else:
                add(
                    f"| {line['deliverable']} (out of scope) | — | "
                    f"{hours(line['delivered_qty'])} | — | {hours(line['actual_hours'])} | "
                    f"{signed_hours(line['actual_hours'])} |"
                )
        add("")
        for note in result["detail_notes"]:
            add(f"- {note}")
        add("")

    add("## Clients to renegotiate")
    add("")
    if not ctx["renegotiate"]:
        add("None. No retainer is below the margin target or outside its hours tolerance.")
        add("")
    for index, result in enumerate(ctx["renegotiate"], start=1):
        add(f"### {index}. {result['client']} — {money(result['annual_gap'])} annualized gap")
        add("")
        for note in result["renegotiation"]:
            add(f"- {note}")
        add("")

    add("## Clients to re-engage")
    add("")
    if not ctx["reengage"]:
        add("None. No client is materially under-served against contracted hours.")
        add("")
    for result in ctx["reengage"]:
        add(
            f"- **{result['client']}** delivered {hours(result['delivered_hours'])}h against "
            f"{hours(result['contracted_hours'])}h contracted "
            f"({signed_percent(result['hours_variance_pct'])}). The margin looks healthy at "
            f"{percent(result['margin_pct'])}, but the client is paying for work that did not "
            f"happen — deploy the remaining {hours(-result['hours_variance'])}h before renewal on "
            f"{result['renewal_date'].isoformat()}."
        )
    add("")

    healthy = [result for result in ctx["results"] if not result["flags"]]
    add("## Healthy retainers")
    add("")
    if healthy:
        for result in healthy:
            add(
                f"- **{result['client']}** — {percent(result['margin_pct'])} margin, "
                f"{hours(result['delivered_hours'])}h delivered against "
                f"{hours(result['contracted_hours'])}h contracted, effective rate "
                f"{money_cents(result['effective_rate'])}/h."
            )
    else:
        add("- None this month.")
    add("")

    add("## Method and caveats")
    add("")
    add(
        f"- Margin is `(retainer fee - delivery cost) / retainer fee`, where delivery cost is "
        "logged hours priced at each person's internal cost per hour. It excludes overhead, "
        "software, media and pass-through costs, so true account margin is lower."
    )
    add(
        f"- Flags fire at: hours over scope by more than "
        f"{percent(settings['over_tolerance'], '0.1')}, hours under scope by more than "
        f"{percent(settings['under_tolerance'], '0.1')}, margin below "
        f"{percent(settings['target_margin'], '0.1')}, or effective rate more than "
        f"{percent(settings['rate_drop'], '0.1')} below the contracted rate."
    )
    add(
        "- Only time entries dated inside the reporting month are counted. Work logged late "
        "lands in the month it was logged."
    )
    if ctx["notes"]:
        for note in ctx["notes"]:
            add(f"- {note}")
    add("")

    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------------ main


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Retainer scope and margin tracker.")
    parser.add_argument("--contracts", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--time-entries", dest="time_entries", required=True)
    parser.add_argument("--roster", required=True)
    parser.add_argument("--deliverables", required=True)
    parser.add_argument("--month", required=True, help="reporting month, YYYY-MM")
    parser.add_argument("--as-of", dest="as_of", required=True, help="review date, YYYY-MM-DD")
    parser.add_argument("--target-margin", dest="target_margin", default="55", help="percent, default 55")
    parser.add_argument("--over-tolerance", dest="over_tolerance", default="10", help="percent, default 10")
    parser.add_argument("--under-tolerance", dest="under_tolerance", default="15", help="percent, default 15")
    parser.add_argument("--rate-drop", dest="rate_drop", default="15", help="percent, default 15")
    parser.add_argument("--lead-days", dest="lead_days", type=int, default=45)
    parser.add_argument("--round-to", dest="round_to", default="100", help="rounding for proposed fees")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def positive_decimal(raw: str, flag: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        fail(f"{flag} is '{raw}', expected a number")
    if value < ZERO:
        fail(f"{flag} is '{raw}', expected a value >= 0")
    return value


def main(argv=None) -> int:
    args = parse_args(argv)

    if not MONTH_RE.match(args.month):
        fail(f"--month is '{args.month}', expected YYYY-MM")
    if not DATE_RE.match(args.as_of):
        fail(f"--as-of is '{args.as_of}', expected YYYY-MM-DD")
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        fail(f"--as-of is '{args.as_of}', which is not a real calendar date")
    if args.lead_days < 0:
        fail(f"--lead-days is {args.lead_days}, expected 0 or more")

    settings = {
        "target_margin": positive_decimal(args.target_margin, "--target-margin"),
        "over_tolerance": positive_decimal(args.over_tolerance, "--over-tolerance"),
        "under_tolerance": positive_decimal(args.under_tolerance, "--under-tolerance"),
        "rate_drop": positive_decimal(args.rate_drop, "--rate-drop"),
        "round_to": positive_decimal(args.round_to, "--round-to"),
        "lead_days": args.lead_days,
    }
    if settings["target_margin"] >= HUNDRED:
        fail(f"--target-margin is '{args.target_margin}', expected a percent below 100")

    roster = load_roster(args.roster)
    contracts = load_contracts(args.contracts, args.month)
    scope = load_scope(args.scope, args.month, contracts)
    entries = load_time_entries(args.time_entries, args.month, contracts, roster)
    delivered = load_deliverables(args.deliverables, args.month, contracts)

    entries_by_client: dict[str, list[dict]] = {client: [] for client in contracts}
    for entry in entries:
        entries_by_client[entry["client"]].append(entry)

    results = []
    for client in sorted(contracts):
        results.append(
            analyze_client(
                client,
                contracts[client],
                scope[client],
                entries_by_client[client],
                delivered[client],
                roster,
                settings,
            )
        )

    for result in results:
        notes = []
        if "scope_creep" in result["flags"]:
            notes.append(
                f"Delivered {hours(result['delivered_hours'])}h against "
                f"{hours(result['contracted_hours'])}h contracted "
                f"({signed_percent(result['hours_variance_pct'])}); the extra "
                f"{hours(result['hours_variance'])}h cost "
                f"{money(result['hours_variance'] * (result['blended_cost_rate'] or ZERO))} to "
                "deliver and was not billed."
            )
        if result["over_qty"]:
            for line in result["over_qty"]:
                notes.append(
                    f"'{line['deliverable']}': {hours(line['delivered_qty'])} delivered against "
                    f"{hours(line['contracted_qty'])} contracted, using "
                    f"{hours(line['actual_hours'])}h versus {hours(line['contracted_hours'])}h "
                    "scoped."
                )
        if result["unscoped"]:
            for line in result["unscoped"]:
                notes.append(
                    f"'{line['deliverable']}' is not in the contracted scope at all: "
                    f"{hours(line['actual_hours'])}h, {money(line['actual_cost'])} of delivery "
                    "cost."
                )
        if result["undelivered"]:
            names = ", ".join(
                f"'{line['deliverable']}' ({hours(line['delivered_qty'])} of "
                f"{hours(line['contracted_qty'])})"
                for line in result["undelivered"]
            )
            notes.append(f"Contracted but not fully delivered: {names}.")
        if "margin" in result["flags"]:
            notes.append(
                f"Margin {percent(result['margin_pct'])} is below the "
                f"{percent(settings['target_margin'], '0.1')} target."
            )
        if "rate" in result["flags"]:
            notes.append(
                f"Effective rate {money_cents(result['effective_rate'])}/h is "
                f"{percent((result['contracted_rate'] - result['effective_rate']) / result['contracted_rate'] * HUNDRED)} "
                f"below the contracted {money_cents(result['contracted_rate'])}/h."
            )
        result["detail_notes"] = notes
        result["renegotiation"] = renegotiation_note(result, settings, as_of)

    renegotiate = sorted(
        (
            result
            for result in results
            if "margin" in result["flags"] or "scope_creep" in result["flags"]
        ),
        key=lambda item: (-item["annual_gap"], item["client"]),
    )
    reengage = [result for result in results if "under_delivered" in result["flags"]]

    total_fee = sum((result["fee"] for result in results), ZERO)
    total_cost = sum((result["cost"] for result in results), ZERO)
    total_hours = sum((result["delivered_hours"] for result in results), ZERO)
    total_contracted = sum((result["contracted_hours"] for result in results), ZERO)
    total_margin = total_fee - total_cost
    blended_margin = divide(total_margin, total_fee)
    hours_variance_pct = divide(total_hours - total_contracted, total_contracted)

    notes = []
    clients_without_scope = [
        result["client"] for result in results if not any(line["in_scope"] for line in result["lines"])
    ]
    for client in clients_without_scope:
        notes.append(
            f"{client} has no deliverable rows in the scope file for {args.month}; every hour "
            "logged is treated as out of scope."
        )

    ctx = {
        "month": args.month,
        "as_of": args.as_of,
        "settings": settings,
        "results": results,
        "renegotiate": renegotiate,
        "reengage": reengage,
        "total_fee": total_fee,
        "total_cost": total_cost,
        "total_hours": total_hours,
        "total_contracted": total_contracted,
        "total_margin": total_margin,
        "blended_margin_pct": None if blended_margin is None else blended_margin * HUNDRED,
        "total_hours_variance_pct": None if hours_variance_pct is None else hours_variance_pct * HUNDRED,
        "portfolio_effective_rate": divide(total_fee, total_hours),
        "notes": notes,
    }

    if args.json:
        payload = {
            "month": args.month,
            "as_of": args.as_of,
            "portfolio": {
                "fees": as_float(total_fee),
                "cost": as_float(total_cost),
                "margin": as_float(total_margin),
                "margin_pct": as_float(ctx["blended_margin_pct"]),
                "delivered_hours": as_float(total_hours),
                "contracted_hours": as_float(total_contracted),
            },
            "clients": [
                {
                    "client": result["client"],
                    "fee": as_float(result["fee"]),
                    "contracted_hours": as_float(result["contracted_hours"]),
                    "delivered_hours": as_float(result["delivered_hours"]),
                    "hours_variance_pct": as_float(result["hours_variance_pct"]),
                    "cost": as_float(result["cost"]),
                    "margin": as_float(result["margin_dollars"]),
                    "margin_pct": as_float(result["margin_pct"]),
                    "effective_rate": as_float(result["effective_rate"]),
                    "contracted_rate": as_float(result["contracted_rate"]),
                    "unscoped_hours": as_float(result["unscoped_hours"]),
                    "flags": result["flags"],
                    "proposed_fee": as_float(result["repriced_fee"]),
                    "scope_back_margin_pct": as_float(result["scope_back_margin_pct"]),
                    "annual_gap": as_float(result["annual_gap"]),
                    "renewal_date": result["renewal_date"].isoformat(),
                }
                for result in results
            ],
            "renegotiate": [result["client"] for result in renegotiate],
            "reengage": [result["client"] for result in reengage],
            "notes": notes,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
