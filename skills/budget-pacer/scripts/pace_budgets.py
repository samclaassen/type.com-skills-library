#!/usr/bin/env python3
"""Mid-flight budget pacing across campaigns and clients.

Projects month-end spend from a trailing run rate, flags over- and
under-delivery beyond a tolerance, and recommends a daily budget per campaign
that reconciles exactly to each client's committed total.

Stdlib only. No network access. Deterministic: "today" comes from --as-of.

Usage:
    python3 scripts/pace_budgets.py \
        --campaigns data/campaigns.csv \
        --daily-spend data/daily_spend.csv \
        --commitments data/commitments.csv \
        --month 2026-09 \
        --as-of 2026-09-18
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")

CAMPAIGN_COLUMNS = [
    "client",
    "campaign",
    "channel",
    "status",
    "current_daily_budget",
    "min_daily_budget",
    "max_daily_budget",
]
SPEND_COLUMNS = ["client", "campaign", "date", "spend"]
COMMITMENT_COLUMNS = ["client", "month", "committed_spend"]

STATUS_ACTIVE = "ACTIVE"
STATUS_PAUSED = "PAUSED"


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


def need_text(row, column, path, line_no) -> str:
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


def signed_money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if whole == ZERO:
        return "$0"
    if whole < ZERO:
        return f"-${abs(whole):,}"
    return f"+${whole:,}"


def signed_money_cents(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    cents = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if cents == ZERO:
        return "$0.00"
    if cents < ZERO:
        return f"-${abs(cents):,.2f}"
    return f"+${cents:,.2f}"


def percent(value: Decimal | None, places: str = "0.1") -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP)}%"


def signed_percent(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{'+' if rounded >= 0 else ''}{rounded}%"


def divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    return numerator / denominator


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def month_label(month: str) -> str:
    return f"{calendar.month_name[int(month[5:])]} {month[:4]}"


# --------------------------------------------------------------------- loading


def load_campaigns(path: str) -> dict[tuple[str, str], dict]:
    rows = read_table(path, CAMPAIGN_COLUMNS, "campaigns")
    campaigns: dict[tuple[str, str], dict] = {}
    for row, line_no in rows:
        client = need_text(row, "client", path, line_no)
        name = need_text(row, "campaign", path, line_no)
        key = (client, name)
        if key in campaigns:
            fail(f"{path} line {line_no}: duplicate campaign '{name}' for client '{client}'")
        status = need_text(row, "status", path, line_no).upper()
        if status not in {STATUS_ACTIVE, STATUS_PAUSED}:
            fail(
                f"{path} line {line_no}: column 'status' is '{status}', expected ACTIVE or PAUSED"
            )
        minimum = need_decimal(row, "min_daily_budget", path, line_no)
        maximum = need_decimal(row, "max_daily_budget", path, line_no)
        if maximum < minimum:
            fail(
                f"{path} line {line_no}: max_daily_budget {maximum} is below min_daily_budget "
                f"{minimum} for campaign '{name}'"
            )
        campaigns[key] = {
            "client": client,
            "campaign": name,
            "channel": need_text(row, "channel", path, line_no),
            "status": status,
            "current": need_decimal(row, "current_daily_budget", path, line_no),
            "min": minimum,
            "max": maximum,
            "spend_to_date": ZERO,
            "recent_spend": ZERO,
        }
    return campaigns


def load_commitments(path: str, month: str) -> dict[str, Decimal]:
    rows = read_table(path, COMMITMENT_COLUMNS, "commitments")
    commitments: dict[str, Decimal] = {}
    for row, line_no in rows:
        row_month = need_text(row, "month", path, line_no)
        if not MONTH_RE.match(row_month):
            fail(f"{path} line {line_no}: column 'month' is '{row_month}', expected YYYY-MM")
        if row_month != month:
            continue
        client = need_text(row, "client", path, line_no)
        if client in commitments:
            fail(f"{path} line {line_no}: duplicate commitment row for '{client}' in {month}")
        commitments[client] = need_decimal(
            row, "committed_spend", path, line_no, allow_zero=False
        )
    if not commitments:
        fail(f"commitments: {path} has no rows for month {month}")
    return commitments


def load_spend(
    path: str,
    campaigns: dict[tuple[str, str], dict],
    month: str,
    as_of: date,
    window_start: date,
) -> None:
    rows = read_table(path, SPEND_COLUMNS, "daily spend")
    seen_in_month = False
    for row, line_no in rows:
        spend_date = need_date(row, "date", path, line_no)
        client = need_text(row, "client", path, line_no)
        campaign = need_text(row, "campaign", path, line_no)
        key = (client, campaign)
        if key not in campaigns:
            fail(
                f"{path} line {line_no}: campaign '{campaign}' for client '{client}' does not "
                "appear in the campaigns file"
            )
        amount = need_decimal(row, "spend", path, line_no)
        if spend_date.strftime("%Y-%m") != month:
            continue
        if spend_date > as_of:
            fail(
                f"{path} line {line_no}: date {spend_date.isoformat()} is after --as-of "
                f"{as_of.isoformat()}; the spend export must stop at the pacing date"
            )
        seen_in_month = True
        campaigns[key]["spend_to_date"] += amount
        if spend_date >= window_start:
            campaigns[key]["recent_spend"] += amount
    if not seen_in_month:
        fail(f"daily spend: {path} has no rows dated inside {month} on or before {as_of.isoformat()}")


# ------------------------------------------------------------------- allocation


def allocate(required_daily: Decimal, campaigns: list[dict]) -> tuple[dict[str, Decimal], str | None]:
    """Split a required daily total across active campaigns.

    Proportional to current daily budgets, clamped to each campaign's min and
    max, with the clamped remainder redistributed across the campaigns that are
    still free to move. Returns the allocation and an optional reconciliation
    problem.
    """
    if not campaigns:
        return {}, "no active campaigns"

    floor_total = sum((campaign["min"] for campaign in campaigns), ZERO)
    ceiling_total = sum((campaign["max"] for campaign in campaigns), ZERO)
    if required_daily > ceiling_total:
        return (
            {campaign["campaign"]: campaign["max"] for campaign in campaigns},
            "ceiling",
        )
    if required_daily < floor_total:
        return (
            {campaign["campaign"]: campaign["min"] for campaign in campaigns},
            "floor",
        )

    allocation: dict[str, Decimal] = {}
    free = list(campaigns)
    remaining = required_daily

    # Clamp iteratively: every pass fixes the campaigns that hit a bound and
    # re-splits what is left across the rest. Terminates because each pass that
    # changes anything removes at least one campaign from `free`.
    while free:
        weights = sum((campaign["current"] for campaign in free), ZERO)
        clamped_any = False
        if weights == ZERO:
            share = remaining / Decimal(len(free))
            proposed = {campaign["campaign"]: share for campaign in free}
        else:
            proposed = {
                campaign["campaign"]: remaining * campaign["current"] / weights
                for campaign in free
            }
        still_free = []
        for campaign in free:
            value = proposed[campaign["campaign"]]
            if value < campaign["min"]:
                allocation[campaign["campaign"]] = campaign["min"]
                remaining -= campaign["min"]
                clamped_any = True
            elif value > campaign["max"]:
                allocation[campaign["campaign"]] = campaign["max"]
                remaining -= campaign["max"]
                clamped_any = True
            else:
                still_free.append(campaign)
        if not clamped_any:
            for campaign in still_free:
                allocation[campaign["campaign"]] = proposed[campaign["campaign"]]
            free = []
        else:
            free = still_free
            if not free:
                break

    # Round to cents, then push the rounding residual onto the campaign with the
    # most headroom so the daily budgets sum exactly to the required total.
    rounded = {
        name: value.quantize(CENT, rounding=ROUND_HALF_UP) for name, value in allocation.items()
    }
    residual = required_daily.quantize(CENT, rounding=ROUND_HALF_UP) - sum(rounded.values(), ZERO)
    if residual != ZERO:
        adjustable = sorted(
            (
                campaign
                for campaign in campaigns
                if campaign["min"] <= rounded[campaign["campaign"]] + residual <= campaign["max"]
            ),
            key=lambda campaign: (-campaign["current"], campaign["campaign"]),
        )
        if adjustable:
            target = adjustable[0]["campaign"]
            rounded[target] = rounded[target] + residual
    return rounded, None


# -------------------------------------------------------------------- analysis


def analyze_client(
    client: str,
    committed: Decimal,
    campaigns: list[dict],
    days_elapsed: int,
    days_remaining: int,
    days_in_month: int,
    run_rate_days: int,
    tolerance: Decimal,
) -> dict:
    spend_to_date = sum((campaign["spend_to_date"] for campaign in campaigns), ZERO)
    recent_spend = sum((campaign["recent_spend"] for campaign in campaigns), ZERO)
    run_rate = recent_spend / Decimal(run_rate_days)
    projected = spend_to_date + run_rate * Decimal(days_remaining)
    variance = projected - committed
    variance_pct = divide(variance, committed)
    variance_pct = None if variance_pct is None else variance_pct * HUNDRED
    expected_to_date = committed * Decimal(days_elapsed) / Decimal(days_in_month)
    delivered_pct = divide(spend_to_date, committed)
    delivered_pct = None if delivered_pct is None else delivered_pct * HUNDRED

    if variance_pct is None:
        status = "NO COMMITMENT"
    elif variance_pct > tolerance:
        status = "OVER"
    elif variance_pct < -tolerance:
        status = "UNDER"
    else:
        status = "ON PACE"

    active = [campaign for campaign in campaigns if campaign["status"] == STATUS_ACTIVE]
    current_daily_total = sum((campaign["current"] for campaign in active), ZERO)
    remaining_budget = committed - spend_to_date
    required_daily = None
    allocation: dict[str, Decimal] = {}
    problem = None
    if days_remaining > 0:
        required_daily = remaining_budget / Decimal(days_remaining)
        if required_daily < ZERO:
            required_daily = ZERO
        allocation, problem = allocate(required_daily, active)

    allocated_daily = sum(allocation.values(), ZERO) if allocation else ZERO
    reconciled_total = spend_to_date + allocated_daily * Decimal(days_remaining)

    return {
        "client": client,
        "committed": committed,
        "spend_to_date": spend_to_date,
        "expected_to_date": expected_to_date,
        "delivered_pct": delivered_pct,
        "run_rate": run_rate,
        "projected": projected,
        "variance": variance,
        "variance_pct": variance_pct,
        "status": status,
        "campaigns": campaigns,
        "active": active,
        "current_daily_total": current_daily_total,
        "remaining_budget": remaining_budget,
        "required_daily": required_daily,
        "allocation": allocation,
        "problem": problem,
        "allocated_daily": allocated_daily,
        "reconciled_total": reconciled_total,
    }


def client_actions(result: dict, days_remaining: int, tolerance: Decimal) -> list[str]:
    actions: list[str] = []
    client = result["client"]
    if days_remaining == 0:
        actions.append(
            f"{client}: the month is closed as of the pacing date. Final spend "
            f"{money(result['spend_to_date'])} against {money(result['committed'])} committed "
            f"({signed_money(result['spend_to_date'] - result['committed'])}). No daily budget "
            "change can affect this month."
        )
        return actions

    if result["problem"] == "no active campaigns":
        actions.append(
            f"{client}: {money(result['remaining_budget'])} is unspent with {days_remaining} days "
            "left and no active campaigns to carry it. Reactivate a campaign or release the "
            "commitment."
        )
        return actions

    if result["problem"] == "ceiling":
        shortfall = result["required_daily"] - result["allocated_daily"]
        actions.append(
            f"{client}: cannot reconcile. Hitting {money(result['committed'])} needs "
            f"{money_cents(result['required_daily'])}/day but every campaign is already at its "
            f"maximum, totalling {money_cents(result['allocated_daily'])}/day — a shortfall of "
            f"{money_cents(shortfall)}/day ({money(shortfall * Decimal(days_remaining))} over the "
            "remaining days). Raise the caps, add a campaign, or agree a reduced commitment now "
            "rather than at month end."
        )
    elif result["problem"] == "floor":
        overage = result["allocated_daily"] - result["required_daily"]
        if result["remaining_budget"] <= ZERO:
            actions.append(
                f"{client}: the commitment is exhausted — {money(result['spend_to_date'])} spent "
                f"against {money(result['committed'])} committed, with {days_remaining} days "
                f"still to run. Campaign minimums total {money_cents(result['allocated_daily'])}"
                f"/day, so pausing is the only lever that stops a further "
                f"{money(overage * Decimal(days_remaining))} of overspend."
            )
        else:
            actions.append(
                f"{client}: cannot reconcile. Only {money(result['remaining_budget'])} remains "
                f"({money_cents(result['required_daily'])}/day) but the campaign minimums total "
                f"{money_cents(result['allocated_daily'])}/day, overshooting by "
                f"{money_cents(overage)}/day ({money(overage * Decimal(days_remaining))} by "
                "month end). Pause the lowest-value campaign instead of trimming every budget "
                "below its floor."
            )
    elif result["status"] == "OVER":
        actions.append(
            f"{client}: projected {money(result['projected'])} against {money(result['committed'])} "
            f"committed ({signed_percent(result['variance_pct'])}). Cut the daily total from "
            f"{money_cents(result['current_daily_total'])} to "
            f"{money_cents(result['allocated_daily'])} to land on the commitment."
        )
    elif result["status"] == "UNDER":
        actions.append(
            f"{client}: projected {money(result['projected'])} against {money(result['committed'])} "
            f"committed ({signed_percent(result['variance_pct'])}). Raise the daily total from "
            f"{money_cents(result['current_daily_total'])} to "
            f"{money_cents(result['allocated_daily'])}, and check whether the campaigns can "
            "actually absorb it before promising delivery."
        )
    else:
        actions.append(
            f"{client}: on pace at {signed_percent(result['variance_pct'])}, inside the "
            f"{percent(tolerance, '0.1')} tolerance. Hold budgets; the recommended daily total of "
            f"{money_cents(result['allocated_daily'])} is a minor trim, not a required change."
        )

    if result["spend_to_date"] > result["committed"] and result["problem"] != "floor":
        actions.append(
            f"{client}: already {money(result['spend_to_date'] - result['committed'])} over the "
            "committed total with the month still running. Escalate before making any further "
            "budget change."
        )
    return actions


# ---------------------------------------------------------------------- report


def render(ctx: dict) -> str:
    out: list[str] = []
    add = out.append

    add(f"# Budget pacing — {month_label(ctx['month'])}, as of {ctx['as_of']}")
    add("")
    add(
        f"Day {ctx['days_elapsed']} of {ctx['days_in_month']} "
        f"({percent(ctx['elapsed_pct'])} elapsed, {ctx['days_remaining']} days remaining) | "
        f"{len(ctx['results'])} clients | {money(ctx['total_committed'])} committed | "
        f"{money(ctx['total_spend'])} spent | projected {money(ctx['total_projected'])} "
        f"({signed_percent(ctx['total_variance_pct'])})"
    )
    add("")
    add(
        f"Run rate is the trailing {ctx['run_rate_days']}-day average "
        f"({ctx['window_start']} to {ctx['as_of']}). Tolerance is "
        f"{percent(ctx['tolerance'], '0.1')} of the committed total."
    )
    add("")

    add("## Client pacing")
    add("")
    add(
        "| Client | Committed | Spent | % delivered | Expected to date | Run rate/day | "
        "Projected | Variance | Status |"
    )
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for result in ctx["results"]:
        add(
            f"| {result['client']} | {money(result['committed'])} | "
            f"{money(result['spend_to_date'])} | {percent(result['delivered_pct'])} | "
            f"{money(result['expected_to_date'])} | {money_cents(result['run_rate'])} | "
            f"{money(result['projected'])} | {signed_money(result['variance'])} "
            f"({signed_percent(result['variance_pct'])}) | {result['status']} |"
        )
    add(
        f"| **Total** | **{money(ctx['total_committed'])}** | **{money(ctx['total_spend'])}** | "
        f"**{percent(ctx['total_delivered_pct'])}** | "
        f"**{money(ctx['total_expected'])}** | **{money_cents(ctx['total_run_rate'])}** | "
        f"**{money(ctx['total_projected'])}** | **{signed_money(ctx['total_variance'])}** "
        f"(**{signed_percent(ctx['total_variance_pct'])}**) | |"
    )
    add("")

    add("## Recommended daily budgets")
    add("")
    if ctx["days_remaining"] == 0:
        add(
            "The pacing date is the last day of the month, so there are no remaining days to "
            "reallocate. The table above is a final reconciliation."
        )
        add("")
    else:
        for result in ctx["results"]:
            add(f"### {result['client']}")
            add("")
            if not result["active"]:
                add("No active campaigns. Nothing can be reallocated.")
                add("")
                continue
            add("| Campaign | Channel | Current/day | Recommended/day | Change | Bound |")
            add("| --- | --- | ---: | ---: | ---: | --- |")
            for campaign in result["active"]:
                recommended = result["allocation"].get(campaign["campaign"], ZERO)
                change = recommended - campaign["current"]
                change_pct = divide(change, campaign["current"])
                change_pct = None if change_pct is None else change_pct * HUNDRED
                if recommended == campaign["max"]:
                    bound = "at max"
                elif recommended == campaign["min"]:
                    bound = "at min"
                else:
                    bound = "—"
                add(
                    f"| {campaign['campaign']} | {campaign['channel']} | "
                    f"{money_cents(campaign['current'])} | {money_cents(recommended)} | "
                    f"{signed_money(change)} ({signed_percent(change_pct)}) | {bound} |"
                )
            add(
                f"| **Total** | | **{money_cents(result['current_daily_total'])}** | "
                f"**{money_cents(result['allocated_daily'])}** | "
                f"**{signed_money(result['allocated_daily'] - result['current_daily_total'])}** | |"
            )
            add("")
            delta = result["reconciled_total"] - result["committed"]
            reconciliation = (
                f"Reconciliation: {money_cents(result['spend_to_date'])} spent + "
                f"{money_cents(result['allocated_daily'])}/day x {ctx['days_remaining']} days = "
                f"{money_cents(result['reconciled_total'])} against "
                f"{money_cents(result['committed'])} committed "
                f"({signed_money_cents(delta)})."
            )
            if abs(delta) <= Decimal("1"):
                reconciliation += (
                    " Daily budgets are set in whole cents, so the remainder never lands on "
                    "exactly zero."
                )
            else:
                reconciliation += " See the actions below: this client cannot be reconciled by "
                reconciliation += "budget changes alone."
            add(reconciliation)
            add("")

    add("## Actions")
    add("")
    for action in ctx["actions"]:
        add(f"- {action}")
    add("")

    if ctx["notes"]:
        add("## Notes")
        add("")
        for note in ctx["notes"]:
            add(f"- {note}")
        add("")

    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------------ main


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Mid-flight budget pacer.")
    parser.add_argument("--campaigns", required=True)
    parser.add_argument("--daily-spend", dest="daily_spend", required=True)
    parser.add_argument("--commitments", required=True)
    parser.add_argument("--month", required=True, help="pacing month, YYYY-MM")
    parser.add_argument("--as-of", dest="as_of", required=True, help="pacing date, YYYY-MM-DD")
    parser.add_argument("--tolerance", default="5", help="percent variance allowed, default 5")
    parser.add_argument(
        "--run-rate-days",
        dest="run_rate_days",
        type=int,
        default=7,
        help="trailing window for the run rate, default 7",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


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
    if as_of.strftime("%Y-%m") != args.month:
        fail(f"--as-of {args.as_of} is not inside --month {args.month}")
    if args.run_rate_days < 1:
        fail(f"--run-rate-days is {args.run_rate_days}, expected 1 or more")
    try:
        tolerance = Decimal(args.tolerance)
    except InvalidOperation:
        fail(f"--tolerance is '{args.tolerance}', expected a number")
    if tolerance < ZERO:
        fail(f"--tolerance is '{args.tolerance}', expected a value >= 0")

    year, month_number = int(args.month[:4]), int(args.month[5:])
    days_in_month = calendar.monthrange(year, month_number)[1]
    days_elapsed = as_of.day
    days_remaining = days_in_month - days_elapsed

    # The trailing window never reaches before the first of the month: a
    # part-month average is honest, a window padded with zeros is not.
    run_rate_days = min(args.run_rate_days, days_elapsed)
    window_start = max(date(year, month_number, 1), as_of - timedelta(days=run_rate_days - 1))

    campaigns = load_campaigns(args.campaigns)
    commitments = load_commitments(args.commitments, args.month)
    load_spend(args.daily_spend, campaigns, args.month, as_of, window_start)

    by_client: dict[str, list[dict]] = {}
    for key in sorted(campaigns):
        campaign = campaigns[key]
        by_client.setdefault(campaign["client"], []).append(campaign)

    notes: list[str] = []
    for client in sorted(by_client):
        if client not in commitments:
            fail(
                f"commitments: client '{client}' appears in the campaigns file but has no "
                f"committed_spend row for {args.month}"
            )
    for client in sorted(commitments):
        if client not in by_client:
            notes.append(
                f"{client} has a {money(commitments[client])} commitment but no campaigns in the "
                "campaigns file; it is excluded from the totals."
            )

    results = []
    for client in sorted(by_client):
        results.append(
            analyze_client(
                client,
                commitments[client],
                by_client[client],
                days_elapsed,
                days_remaining,
                days_in_month,
                run_rate_days,
                tolerance,
            )
        )

    for result in results:
        paused = [
            campaign for campaign in result["campaigns"] if campaign["status"] == STATUS_PAUSED
        ]
        for campaign in paused:
            if campaign["spend_to_date"] > ZERO:
                notes.append(
                    f"{result['client']} / {campaign['campaign']} is paused but spent "
                    f"{money(campaign['spend_to_date'])} this month; its spend counts toward the "
                    "commitment and it receives no recommended budget."
                )
            else:
                notes.append(
                    f"{result['client']} / {campaign['campaign']} is paused and excluded from "
                    "the reallocation."
                )

    if run_rate_days < args.run_rate_days:
        notes.append(
            f"Only {run_rate_days} day(s) of the month have elapsed, so the run rate uses that "
            f"window instead of the requested {args.run_rate_days} days."
        )

    total_committed = sum((result["committed"] for result in results), ZERO)
    total_spend = sum((result["spend_to_date"] for result in results), ZERO)
    total_projected = sum((result["projected"] for result in results), ZERO)
    total_run_rate = sum((result["run_rate"] for result in results), ZERO)
    total_expected = sum((result["expected_to_date"] for result in results), ZERO)
    total_variance = total_projected - total_committed
    total_variance_pct = divide(total_variance, total_committed)
    total_delivered_pct = divide(total_spend, total_committed)

    actions: list[str] = []
    for result in sorted(
        results,
        key=lambda item: (
            -abs(item["variance_pct"] or ZERO),
            item["client"],
        ),
    ):
        actions.extend(client_actions(result, days_remaining, tolerance))

    ctx = {
        "month": args.month,
        "as_of": args.as_of,
        "window_start": window_start.isoformat(),
        "run_rate_days": run_rate_days,
        "tolerance": tolerance,
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "elapsed_pct": Decimal(days_elapsed) / Decimal(days_in_month) * HUNDRED,
        "results": results,
        "total_committed": total_committed,
        "total_spend": total_spend,
        "total_projected": total_projected,
        "total_run_rate": total_run_rate,
        "total_expected": total_expected,
        "total_variance": total_variance,
        "total_variance_pct": None if total_variance_pct is None else total_variance_pct * HUNDRED,
        "total_delivered_pct": None if total_delivered_pct is None else total_delivered_pct * HUNDRED,
        "actions": actions,
        "notes": notes,
    }

    if args.json:
        payload = {
            "month": args.month,
            "as_of": args.as_of,
            "days_in_month": days_in_month,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "run_rate_days": run_rate_days,
            "window_start": window_start.isoformat(),
            "tolerance_pct": as_float(tolerance),
            "totals": {
                "committed": as_float(total_committed),
                "spend_to_date": as_float(total_spend),
                "projected": as_float(total_projected),
                "variance": as_float(total_variance),
                "variance_pct": as_float(ctx["total_variance_pct"]),
            },
            "clients": [
                {
                    "client": result["client"],
                    "committed": as_float(result["committed"]),
                    "spend_to_date": as_float(result["spend_to_date"]),
                    "run_rate": as_float(result["run_rate"]),
                    "projected": as_float(result["projected"]),
                    "variance": as_float(result["variance"]),
                    "variance_pct": as_float(result["variance_pct"]),
                    "status": result["status"],
                    "required_daily": as_float(result["required_daily"]),
                    "recommended_daily_total": as_float(result["allocated_daily"]),
                    "reconciliation_problem": result["problem"],
                    "campaigns": [
                        {
                            "campaign": campaign["campaign"],
                            "channel": campaign["channel"],
                            "status": campaign["status"],
                            "spend_to_date": as_float(campaign["spend_to_date"]),
                            "current_daily_budget": as_float(campaign["current"]),
                            "recommended_daily_budget": as_float(
                                result["allocation"].get(campaign["campaign"])
                            ),
                        }
                        for campaign in result["campaigns"]
                    ],
                }
                for result in results
            ],
            "actions": actions,
            "notes": notes,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
