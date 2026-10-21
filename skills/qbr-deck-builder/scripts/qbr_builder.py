#!/usr/bin/env python3
"""Assemble a customer QBR from usage, goal, support, and commercial data.

Stdlib only, offline, deterministic. Every "today" comes from --as-of.

Sections produced for one account:
  1 executive summary
  2 adoption trend (trailing window vs the window before it)
  3 value delivered against the customer's stated goals
  4 support health
  5 open risks, ranked by severity
  6 expansion opportunities, ranked by estimated ARR x confidence
  7 data coverage (what was missing, so nobody presents a hole as a fact)

Exit codes: 0 ok, 2 bad input (message names the file, row and column).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config" / "qbr.json"

USAGE_COLUMNS = ["account_id", "month", "metric_name", "value"]
GOAL_COLUMNS = [
    "account_id",
    "goal_id",
    "goal_text",
    "metric_name",
    "baseline_value",
    "target_value",
    "target_date",
]
SUPPORT_COLUMNS = [
    "ticket_id",
    "account_id",
    "created_date",
    "resolved_date",
    "priority",
    "status",
    "subject",
    "csat_score",
]
COMMERCIAL_COLUMNS = [
    "account_id",
    "account_name",
    "arr_usd",
    "contract_start",
    "renewal_date",
    "licensed_seats",
    "products",
    "csm_owner",
]
PRODUCT_COLUMNS = [
    "product_code",
    "product_name",
    "list_price_per_seat_usd",
    "signal_metric",
    "signal_threshold",
    "value_statement",
]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class QbrError(Exception):
    """Raised for any unusable input; the message names the offending field."""


@dataclass
class Account:
    account_id: str
    account_name: str
    arr: Decimal
    contract_start: date
    renewal: date
    licensed_seats: int
    products: list[str]
    csm_owner: str


@dataclass
class Goal:
    goal_id: str
    text: str
    metric: str
    baseline: float
    target: float
    target_date: date


@dataclass
class Ticket:
    ticket_id: str
    created: date
    resolved: date | None
    priority: str
    status: str
    subject: str
    csat: float | None


@dataclass
class Product:
    code: str
    name: str
    price_per_seat: Decimal
    signal_metric: str
    signal_threshold: float
    value_statement: str


@dataclass
class Risk:
    code: str
    severity: str
    headline: str
    evidence: str


@dataclass
class Opportunity:
    name: str
    kind: str
    estimated_arr: Decimal
    confidence: float
    rationale: str
    value_statement: str = ""

    @property
    def weighted(self) -> Decimal:
        return (self.estimated_arr * Decimal(str(self.confidence))).quantize(Decimal("1"))


def read_rows(path: Path, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    if not path.is_file():
        raise QbrError(f"{label}: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise QbrError(
                f"{label}: {path} is empty; expected a header row with: {', '.join(required)}"
            )
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise QbrError(f"{label}: {path} is missing required column(s): {', '.join(missing)}")
        rows = [
            (
                {
                    (key or "").strip(): (value or "").strip()
                    for key, value in raw.items()
                    if key is not None
                },
                row_no,
            )
            for row_no, raw in enumerate(reader, start=2)
        ]
    if not rows:
        raise QbrError(f"{label}: {path} has a header but no data rows")
    return rows


def parse_float(value: str, column: str, row_no: int, label: str) -> float:
    if not value:
        raise QbrError(f"{label} row {row_no}: column '{column}' is required but empty")
    try:
        return float(value.replace(",", "").replace("$", ""))
    except ValueError:
        raise QbrError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a number"
        ) from None


def parse_int(value: str, column: str, row_no: int, label: str) -> int:
    if not value:
        raise QbrError(f"{label} row {row_no}: column '{column}' is required but empty")
    try:
        return int(value.replace(",", ""))
    except ValueError:
        raise QbrError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a whole number"
        ) from None


def parse_money(value: str, column: str, row_no: int, label: str) -> Decimal:
    if not value:
        raise QbrError(f"{label} row {row_no}: column '{column}' is required but empty")
    try:
        amount = Decimal(value.replace(",", "").replace("$", ""))
    except InvalidOperation:
        raise QbrError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a number"
        ) from None
    if amount < 0:
        raise QbrError(f"{label} row {row_no}: column '{column}' value {value!r} is negative")
    return amount


def parse_date_value(value: str, column: str, row_no: int, label: str, required: bool) -> date | None:
    if not value:
        if required:
            raise QbrError(f"{label} row {row_no}: column '{column}' is required but empty")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise QbrError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a YYYY-MM-DD date"
        ) from None


def parse_month(value: str, row_no: int, label: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise QbrError(
            f"{label} row {row_no}: column 'month' value {value!r} is not a YYYY-MM month"
        ) from None
    return value


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise QbrError(f"config: file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QbrError(f"config: {path} is not valid JSON ({exc})") from None
    for key in (
        "metrics",
        "history_months",
        "trend_window_months",
        "support_window_days",
        "goal_status_thresholds",
        "risk_rules",
        "risk_severity",
        "expansion",
    ):
        if key not in config:
            raise QbrError(f"config: {path} is missing key {key!r}")
    for key in ("adoption", "licensed", "engagement"):
        if key not in config["metrics"]:
            raise QbrError(f"config: metrics is missing key {key!r}")
    if int(config["trend_window_months"]) < 1:
        raise QbrError("config: trend_window_months must be at least 1")
    if int(config["history_months"]) < 2 * int(config["trend_window_months"]):
        raise QbrError(
            "config: history_months must cover at least two trend windows"
            f" ({2 * int(config['trend_window_months'])})"
        )
    return config


def load_usage(path: Path) -> dict[tuple[str, str], dict[str, float]]:
    rows = read_rows(path, USAGE_COLUMNS, "usage")
    series: dict[tuple[str, str], dict[str, float]] = {}
    for row, row_no in rows:
        account_id = row["account_id"]
        metric = row["metric_name"]
        if not account_id or not metric:
            raise QbrError(
                f"usage row {row_no}: columns 'account_id' and 'metric_name' are both required"
            )
        month = parse_month(row["month"], row_no, "usage")
        value = parse_float(row["value"], "value", row_no, "usage")
        key = (account_id, metric)
        bucket = series.setdefault(key, {})
        if month in bucket:
            raise QbrError(
                f"usage row {row_no}: duplicate row for account {account_id!r},"
                f" metric {metric!r}, month {month}"
            )
        bucket[month] = value
    return series


def load_commercial(path: Path) -> dict[str, Account]:
    rows = read_rows(path, COMMERCIAL_COLUMNS, "commercial")
    accounts: dict[str, Account] = {}
    for row, row_no in rows:
        account_id = row["account_id"]
        if not account_id:
            raise QbrError(f"commercial row {row_no}: column 'account_id' is required but empty")
        if account_id in accounts:
            raise QbrError(f"commercial row {row_no}: account_id {account_id!r} appears twice")
        seats = parse_int(row["licensed_seats"], "licensed_seats", row_no, "commercial")
        if seats <= 0:
            raise QbrError(
                f"commercial row {row_no}: column 'licensed_seats' must be greater than zero"
            )
        accounts[account_id] = Account(
            account_id=account_id,
            account_name=row["account_name"] or account_id,
            arr=parse_money(row["arr_usd"], "arr_usd", row_no, "commercial"),
            contract_start=parse_date_value(
                row["contract_start"], "contract_start", row_no, "commercial", True
            ),
            renewal=parse_date_value(row["renewal_date"], "renewal_date", row_no, "commercial", True),
            licensed_seats=seats,
            products=[item.strip() for item in row["products"].split(";") if item.strip()],
            csm_owner=row["csm_owner"] or "(unassigned)",
        )
    return accounts


def load_goals(path: Path, account_id: str) -> list[Goal]:
    rows = read_rows(path, GOAL_COLUMNS, "goals")
    goals: list[Goal] = []
    seen: set[str] = set()
    for row, row_no in rows:
        if row["account_id"] != account_id:
            continue
        goal_id = row["goal_id"]
        if not goal_id:
            raise QbrError(f"goals row {row_no}: column 'goal_id' is required but empty")
        if goal_id in seen:
            raise QbrError(f"goals row {row_no}: goal_id {goal_id!r} appears twice for this account")
        seen.add(goal_id)
        baseline = parse_float(row["baseline_value"], "baseline_value", row_no, "goals")
        target = parse_float(row["target_value"], "target_value", row_no, "goals")
        if baseline == target:
            raise QbrError(
                f"goals row {row_no}: 'baseline_value' and 'target_value' are both {baseline};"
                " attainment would divide by zero"
            )
        if not row["metric_name"]:
            raise QbrError(f"goals row {row_no}: column 'metric_name' is required but empty")
        goals.append(
            Goal(
                goal_id=goal_id,
                text=row["goal_text"],
                metric=row["metric_name"],
                baseline=baseline,
                target=target,
                target_date=parse_date_value(
                    row["target_date"], "target_date", row_no, "goals", True
                ),
            )
        )
    goals.sort(key=lambda goal: goal.goal_id)
    return goals


def load_support(path: Path, account_id: str, as_of: date) -> list[Ticket]:
    rows = read_rows(path, SUPPORT_COLUMNS, "support")
    tickets: list[Ticket] = []
    seen: set[str] = set()
    for row, row_no in rows:
        if row["account_id"] != account_id:
            continue
        ticket_id = row["ticket_id"]
        if ticket_id in seen:
            raise QbrError(f"support row {row_no}: ticket_id {ticket_id!r} appears twice")
        seen.add(ticket_id)
        created = parse_date_value(row["created_date"], "created_date", row_no, "support", True)
        resolved = parse_date_value(row["resolved_date"], "resolved_date", row_no, "support", False)
        if resolved is not None and resolved < created:
            raise QbrError(
                f"support row {row_no}: 'resolved_date' {resolved.isoformat()} is before"
                f" 'created_date' {created.isoformat()}"
            )
        if created > as_of:
            raise QbrError(
                f"support row {row_no}: 'created_date' {created.isoformat()} is after"
                f" --as-of {as_of.isoformat()}"
            )
        csat_raw = row["csat_score"]
        csat = None
        if csat_raw:
            csat = parse_float(csat_raw, "csat_score", row_no, "support")
            if not 1.0 <= csat <= 5.0:
                raise QbrError(
                    f"support row {row_no}: column 'csat_score' value {csat_raw!r}"
                    " is outside the 1-5 scale"
                )
        tickets.append(
            Ticket(
                ticket_id=ticket_id,
                created=created,
                resolved=resolved,
                priority=row["priority"].lower(),
                status=row["status"].lower(),
                subject=row["subject"],
                csat=csat,
            )
        )
    tickets.sort(key=lambda ticket: (ticket.created, ticket.ticket_id))
    return tickets


def load_products(path: Path) -> list[Product]:
    rows = read_rows(path, PRODUCT_COLUMNS, "products")
    products: list[Product] = []
    seen: set[str] = set()
    for row, row_no in rows:
        code = row["product_code"]
        if not code:
            raise QbrError(f"products row {row_no}: column 'product_code' is required but empty")
        if code in seen:
            raise QbrError(f"products row {row_no}: product_code {code!r} appears twice")
        seen.add(code)
        threshold = parse_float(row["signal_threshold"], "signal_threshold", row_no, "products")
        if threshold <= 0:
            raise QbrError(
                f"products row {row_no}: column 'signal_threshold' must be greater than zero"
            )
        if not row["signal_metric"]:
            raise QbrError(f"products row {row_no}: column 'signal_metric' is required but empty")
        products.append(
            Product(
                code=code,
                name=row["product_name"] or code,
                price_per_seat=parse_money(
                    row["list_price_per_seat_usd"], "list_price_per_seat_usd", row_no, "products"
                ),
                signal_metric=row["signal_metric"],
                signal_threshold=threshold,
                value_statement=row["value_statement"],
            )
        )
    products.sort(key=lambda product: product.code)
    return products


def month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def months_back(anchor: str, count: int) -> list[str]:
    """Calendar months ending at `anchor`, oldest first."""
    year, month = (int(part) for part in anchor.split("-"))
    result: list[str] = []
    for _ in range(count):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(result))


def window_average(series: dict[str, float], months: list[str]) -> tuple[float | None, list[str]]:
    present = [month for month in months if month in series]
    if not present:
        return None, [month for month in months if month not in series]
    values = [series[month] for month in present]
    return sum(values) / len(values), [month for month in months if month not in series]


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def build_trend(series: dict[str, float], months: list[str], window: int) -> dict:
    recent_months = months[-window:]
    prior_months = months[-2 * window : -window]
    recent, missing_recent = window_average(series, recent_months)
    prior, missing_prior = window_average(series, prior_months)
    latest_month = next((month for month in reversed(months) if month in series), None)
    return {
        "recent_months": recent_months,
        "prior_months": prior_months,
        "recent_avg": recent,
        "prior_avg": prior,
        "change_pct": pct_change(recent, prior),
        "missing_months": sorted(set(missing_recent) | set(missing_prior)),
        "latest_month": latest_month,
        "latest_value": series.get(latest_month) if latest_month else None,
    }


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def build_support(tickets: list[Ticket], as_of: date, config: dict) -> dict:
    window = int(config["support_window_days"])
    recent_start = date.fromordinal(as_of.toordinal() - window + 1)
    prior_start = date.fromordinal(as_of.toordinal() - 2 * window + 1)

    recent = [ticket for ticket in tickets if recent_start <= ticket.created <= as_of]
    prior = [ticket for ticket in tickets if prior_start <= ticket.created < recent_start]
    open_tickets = [ticket for ticket in tickets if ticket.resolved is None]
    resolution_days = [
        float((ticket.resolved - ticket.created).days) for ticket in recent if ticket.resolved
    ]
    csat_scores = [ticket.csat for ticket in recent if ticket.csat is not None]
    return {
        "window_days": window,
        "recent_start": recent_start,
        "recent": recent,
        "prior": prior,
        "recent_count": len(recent),
        "prior_count": len(prior),
        "volume_change_pct": pct_change(float(len(recent)), float(len(prior))),
        "p1_recent": [ticket for ticket in recent if ticket.priority == "p1"],
        "open_tickets": sorted(open_tickets, key=lambda ticket: (ticket.created, ticket.ticket_id)),
        "median_resolution_days": median_or_none(resolution_days),
        "resolved_count": len(resolution_days),
        "avg_csat": (sum(csat_scores) / len(csat_scores)) if csat_scores else None,
        "csat_responses": len(csat_scores),
    }


def goal_status(attainment: float, config: dict) -> str:
    thresholds = config["goal_status_thresholds"]
    if attainment >= float(thresholds["achieved"]):
        return "achieved"
    if attainment >= float(thresholds["on_track"]):
        return "on track"
    if attainment >= float(thresholds["at_risk"]):
        return "at risk"
    return "off track"


def build_goals(
    goals: list[Goal],
    usage: dict[tuple[str, str], dict[str, float]],
    account_id: str,
    months: list[str],
    config: dict,
) -> list[dict]:
    built: list[dict] = []
    for goal in goals:
        series = usage.get((account_id, goal.metric), {})
        latest_month = next((month for month in reversed(months) if month in series), None)
        if latest_month is None:
            built.append(
                {
                    "goal": goal,
                    "current": None,
                    "current_month": None,
                    "attainment": None,
                    "status": "no data",
                }
            )
            continue
        current = series[latest_month]
        attainment = (current - goal.baseline) / (goal.target - goal.baseline) * 100.0
        built.append(
            {
                "goal": goal,
                "current": current,
                "current_month": latest_month,
                "attainment": attainment,
                "status": goal_status(attainment, config),
            }
        )
    return built


def build_risks(
    account: Account,
    adoption: dict,
    engagement: dict,
    utilization: float | None,
    goals: list[dict],
    support: dict,
    as_of: date,
    config: dict,
) -> list[Risk]:
    rules = config["risk_rules"]
    severity = config["risk_severity"]
    risks: list[Risk] = []

    if adoption["change_pct"] is not None and adoption["change_pct"] <= -float(rules["adoption_decline_pct"]):
        risks.append(
            Risk(
                "adoption_decline",
                severity["adoption_decline"],
                "Active users are declining",
                f"{adoption['change_pct']:+.1f}% trailing {len(adoption['recent_months'])}-month average"
                f" ({adoption['prior_avg']:.0f} -> {adoption['recent_avg']:.0f})",
            )
        )
    if engagement["change_pct"] is not None and engagement["change_pct"] <= -float(rules["engagement_decline_pct"]):
        risks.append(
            Risk(
                "engagement_decline",
                severity["engagement_decline"],
                "Core usage volume is declining",
                f"{engagement['change_pct']:+.1f}% trailing average"
                f" ({engagement['prior_avg']:.0f} -> {engagement['recent_avg']:.0f})",
            )
        )
    if utilization is not None and utilization < float(rules["utilization_floor"]):
        risks.append(
            Risk(
                "low_utilization",
                severity["low_utilization"],
                "Licensed seats are underused",
                f"{utilization * 100:.0f}% of {account.licensed_seats} seats active,"
                f" floor {float(rules['utilization_floor']) * 100:.0f}%",
            )
        )
    for item in goals:
        if item["status"] == "off track":
            risks.append(
                Risk(
                    "goal_off_track",
                    severity["goal_off_track"],
                    f"Goal {item['goal'].goal_id} is off track",
                    f"{item['attainment']:.0f}% attained, target {item['goal'].target:g}"
                    f" by {item['goal'].target_date.isoformat()}",
                )
            )
        elif item["status"] == "at risk":
            risks.append(
                Risk(
                    "goal_at_risk",
                    severity["goal_at_risk"],
                    f"Goal {item['goal'].goal_id} is at risk",
                    f"{item['attainment']:.0f}% attained, target {item['goal'].target:g}"
                    f" by {item['goal'].target_date.isoformat()}",
                )
            )
    aging = [
        ticket
        for ticket in support["open_tickets"]
        if ticket.priority == "p1" and (as_of - ticket.created).days > int(rules["open_p1_age_days"])
    ]
    for ticket in aging:
        risks.append(
            Risk(
                "aging_p1",
                severity["aging_p1"],
                f"Open P1 {ticket.ticket_id} is aging",
                f"opened {ticket.created.isoformat()} ({(as_of - ticket.created).days}d),"
                f" threshold {int(rules['open_p1_age_days'])}d: {ticket.subject}",
            )
        )
    if support["volume_change_pct"] is not None and support["volume_change_pct"] >= float(
        rules["support_volume_increase_pct"]
    ):
        risks.append(
            Risk(
                "support_volume_spike",
                severity["support_volume_spike"],
                "Support volume is climbing",
                f"{support['prior_count']} -> {support['recent_count']} tickets"
                f" ({support['volume_change_pct']:+.0f}%) across {support['window_days']}-day windows",
            )
        )
    if support["avg_csat"] is not None and support["avg_csat"] < float(rules["csat_floor"]):
        risks.append(
            Risk(
                "low_csat",
                severity["low_csat"],
                "Support satisfaction is below target",
                f"average CSAT {support['avg_csat']:.1f} over {support['csat_responses']} response(s),"
                f" floor {float(rules['csat_floor']):.1f}",
            )
        )
    days_to_renewal = (account.renewal - as_of).days
    if 0 <= days_to_renewal <= int(rules["renewal_horizon_days"]):
        risks.append(
            Risk(
                "renewal_near",
                severity["renewal_near"],
                "Renewal is inside the decision window",
                f"renews {account.renewal.isoformat()} in {days_to_renewal}d",
            )
        )
    risks.sort(key=lambda risk: (SEVERITY_ORDER.get(risk.severity, 3), risk.code, risk.headline))
    return risks


def build_opportunities(
    account: Account,
    usage: dict[tuple[str, str], dict[str, float]],
    products: list[Product],
    months: list[str],
    adoption_latest: float | None,
    config: dict,
) -> list[Opportunity]:
    settings = config["expansion"]
    floor = float(settings["confidence_floor"])
    cap = float(settings["confidence_cap"])
    trigger = float(settings["seat_utilization_trigger"])
    target_utilization = float(settings["target_utilization"])
    opportunities: list[Opportunity] = []

    if account.licensed_seats <= 0:
        raise QbrError(
            f"commercial: account {account.account_id!r} has licensed_seats <= 0;"
            " seat maths would divide by zero"
        )
    arr_per_seat = account.arr / Decimal(account.licensed_seats)

    if adoption_latest is not None:
        utilization = adoption_latest / account.licensed_seats
        if utilization >= trigger and target_utilization > 0:
            needed = math.ceil(adoption_latest / target_utilization)
            additional = needed - account.licensed_seats
            if additional > 0:
                headroom = (
                    (utilization - trigger) / (1 - trigger) if trigger < 1 else 1.0
                )
                confidence = round(min(cap, floor + headroom), 2)
                opportunities.append(
                    Opportunity(
                        name=f"Seat expansion (+{additional} seats)",
                        kind="seats",
                        estimated_arr=(arr_per_seat * additional).quantize(Decimal("1")),
                        confidence=confidence,
                        rationale=(
                            f"{adoption_latest:.0f} active users on {account.licensed_seats} seats"
                            f" = {utilization * 100:.0f}% utilization (trigger {trigger * 100:.0f}%);"
                            f" {needed} seats needed to sit at {target_utilization * 100:.0f}%"
                            f" at ${arr_per_seat.quantize(Decimal('1')):,}/seat"
                        ),
                        value_statement="Buying seats ahead of the next hiring wave avoids mid-term amendments.",
                    )
                )

    owned = set(account.products)
    for product in products:
        if product.code in owned:
            continue
        series = usage.get((account.account_id, product.signal_metric), {})
        latest_month = next((month for month in reversed(months) if month in series), None)
        if latest_month is None:
            continue
        value = series[latest_month]
        if value < product.signal_threshold:
            continue
        ratio = value / product.signal_threshold
        confidence = round(min(cap, floor + (ratio - 1.0)), 2)
        estimated = (product.price_per_seat * account.licensed_seats).quantize(Decimal("1"))
        opportunities.append(
            Opportunity(
                name=f"{product.name} ({product.code})",
                kind="cross-sell",
                estimated_arr=estimated,
                confidence=confidence,
                rationale=(
                    f"{product.signal_metric} = {value:g} in {latest_month},"
                    f" {ratio:.2f}x the {product.signal_threshold:g} trigger;"
                    f" ${product.price_per_seat.quantize(Decimal('1')):,}/seat x"
                    f" {account.licensed_seats} seats"
                ),
                value_statement=product.value_statement,
            )
        )

    opportunities.sort(key=lambda item: (-item.weighted, item.name))
    return opportunities


def format_usd(amount: Decimal) -> str:
    return f"${amount.quantize(Decimal('1')):,}"


def format_number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural_form or singular + 's'}"


def format_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def render_text(report: dict) -> str:
    lines: list[str] = []
    out = lines.append
    account: Account = report["account"]
    config = report["config"]
    adoption = report["adoption"]
    engagement = report["engagement"]
    support = report["support"]

    out(f"QUARTERLY BUSINESS REVIEW — {account.account_name} ({account.account_id})")
    out(f"as of {report['as_of'].isoformat()}   CSM: {account.csm_owner}")
    out(
        f"ARR {format_usd(account.arr)}   licensed seats {account.licensed_seats:,}"
        f"   renews {account.renewal.isoformat()} ({report['days_to_renewal']}d)"
        f"   products: {', '.join(account.products) or '(none listed)'}"
    )
    out("")

    out("SECTION 1. EXECUTIVE SUMMARY")
    for bullet in report["summary_bullets"]:
        out(f"  - {bullet}")
    out("")

    out("SECTION 2. ADOPTION TREND")
    out(
        f"  Metric: {config['metrics']['adoption']} (monthly), utilization against"
        f" {config['metrics']['licensed']} where reported"
    )
    out(f"  {'month':<10}{'active users':>14}{'seats':>9}{'utilization':>13}{'key actions':>14}")
    for month in report["months"]:
        active = report["adoption_series"].get(month)
        seats = report["licensed_series"].get(month, float(account.licensed_seats))
        util = (active / seats) if (active is not None and seats) else None
        actions = report["engagement_series"].get(month)
        out(
            f"  {month:<10}{format_number(active):>14}{format_number(seats):>9}"
            f"{(f'{util * 100:.0f}%' if util is not None else 'n/a'):>13}"
            f"{format_number(actions):>14}"
        )
    adoption_change = format_pct(adoption["change_pct"])
    engagement_change = format_pct(engagement["change_pct"])
    out(
        f"  Trailing {len(adoption['recent_months'])} months vs prior"
        f" {len(adoption['prior_months'])}: active users"
        f" {format_number(adoption['prior_avg'])} -> {format_number(adoption['recent_avg'])}"
        f" ({adoption_change});"
        f" key actions {format_number(engagement['prior_avg'])} ->"
        f" {format_number(engagement['recent_avg'])} ({engagement_change})"
    )
    out("")

    out("SECTION 3. VALUE DELIVERED AGAINST STATED GOALS")
    if not report["goals"]:
        out("  No goals on file for this account. Capture them in this QBR.")
    for item in report["goals"]:
        goal = item["goal"]
        if item["attainment"] is None:
            out(f"  [{'no data':^9}] {goal.goal_id}  {goal.text}")
            out(
                f"      metric {goal.metric} has no readings in the reporting window;"
                " the goal cannot be scored"
            )
            continue
        out(f"  [{item['status']:^9}] {goal.goal_id}  {goal.text}")
        out(
            f"      {goal.metric}: baseline {goal.baseline:g} -> current {item['current']:g}"
            f" ({item['current_month']}) -> target {goal.target:g} by"
            f" {goal.target_date.isoformat()} = {item['attainment']:.0f}% attained"
        )
    out("")

    out("SECTION 4. SUPPORT HEALTH")
    out(
        f"  Window: {support['recent_start'].isoformat()} to {report['as_of'].isoformat()}"
        f" ({support['window_days']} days)"
    )
    volume_change = format_pct(support["volume_change_pct"], digits=0)
    out(
        f"  Tickets {support['recent_count']} (prior window {support['prior_count']},"
        f" {volume_change})"
        f"   P1 {len(support['p1_recent'])}   open now {len(support['open_tickets'])}"
    )
    out(
        f"  Median time to resolve {format_number(support['median_resolution_days'], 1)} days"
        f" across {support['resolved_count']} resolved ticket(s)"
    )
    out(
        f"  Average CSAT {format_number(support['avg_csat'], 1)}"
        f" from {support['csat_responses']} response(s)"
    )
    if support["open_tickets"]:
        out("  Open tickets:")
        for ticket in support["open_tickets"]:
            out(
                f"    {ticket.ticket_id}  {ticket.priority.upper()}"
                f"  opened {ticket.created.isoformat()}"
                f" ({(report['as_of'] - ticket.created).days}d)  {ticket.subject}"
            )
    out("")

    out("SECTION 5. OPEN RISKS")
    if not report["risks"]:
        out("  No rule-based risks fired this period.")
    for risk in report["risks"]:
        out(f"  [{risk.severity:^6}] {risk.headline}")
        out(f"      evidence: {risk.evidence}")
    out("")

    out("SECTION 6. EXPANSION OPPORTUNITIES (ranked by estimated ARR x confidence)")
    if not report["opportunities"]:
        out("  No expansion signals cleared their thresholds this period.")
    for opportunity in report["opportunities"]:
        out(
            f"  {format_usd(opportunity.weighted):>10} weighted"
            f"  |  {format_usd(opportunity.estimated_arr)} ARR x {opportunity.confidence:.2f}"
            f"  |  {opportunity.name} [{opportunity.kind}]"
        )
        out(f"      signal: {opportunity.rationale}")
        if opportunity.value_statement:
            out(f"      talk track: {opportunity.value_statement}")
    out("")

    out("SECTION 7. DATA COVERAGE")
    out(
        f"  Reporting window: {report['months'][0]} to {report['months'][-1]}"
        f" ({len(report['months'])} months)"
    )
    for label, missing in report["coverage"].items():
        if missing:
            out(f"  {label}: missing {', '.join(missing)}")
        else:
            out(f"  {label}: complete")
    out("  Every figure above is computed only from the months actually present.")
    return "\n".join(lines) + "\n"


def render_json(report: dict) -> str:
    account: Account = report["account"]
    payload = {
        "as_of": report["as_of"].isoformat(),
        "inputs": report["paths"],
        "account": {
            "account_id": account.account_id,
            "account_name": account.account_name,
            "arr_usd": str(account.arr.quantize(Decimal("1"))),
            "licensed_seats": account.licensed_seats,
            "renewal_date": account.renewal.isoformat(),
            "days_to_renewal": report["days_to_renewal"],
            "csm_owner": account.csm_owner,
            "products": account.products,
        },
        "executive_summary": report["summary_bullets"],
        "adoption": {
            "months": report["months"],
            "series": report["adoption_series"],
            "engagement_series": report["engagement_series"],
            "recent_average": report["adoption"]["recent_avg"],
            "prior_average": report["adoption"]["prior_avg"],
            "change_pct": report["adoption"]["change_pct"],
            "latest_utilization": report["utilization"],
        },
        "goals": [
            {
                "goal_id": item["goal"].goal_id,
                "goal_text": item["goal"].text,
                "metric": item["goal"].metric,
                "baseline": item["goal"].baseline,
                "current": item["current"],
                "current_month": item["current_month"],
                "target": item["goal"].target,
                "target_date": item["goal"].target_date.isoformat(),
                "attainment_pct": item["attainment"],
                "status": item["status"],
            }
            for item in report["goals"]
        ],
        "support": {
            "window_days": report["support"]["window_days"],
            "tickets_recent": report["support"]["recent_count"],
            "tickets_prior": report["support"]["prior_count"],
            "volume_change_pct": report["support"]["volume_change_pct"],
            "p1_recent": len(report["support"]["p1_recent"]),
            "open_tickets": [
                {
                    "ticket_id": ticket.ticket_id,
                    "priority": ticket.priority,
                    "created_date": ticket.created.isoformat(),
                    "age_days": (report["as_of"] - ticket.created).days,
                    "subject": ticket.subject,
                }
                for ticket in report["support"]["open_tickets"]
            ],
            "median_resolution_days": report["support"]["median_resolution_days"],
            "average_csat": report["support"]["avg_csat"],
            "csat_responses": report["support"]["csat_responses"],
        },
        "risks": [
            {
                "code": risk.code,
                "severity": risk.severity,
                "headline": risk.headline,
                "evidence": risk.evidence,
            }
            for risk in report["risks"]
        ],
        "opportunities": [
            {
                "name": opportunity.name,
                "kind": opportunity.kind,
                "estimated_arr_usd": str(opportunity.estimated_arr),
                "confidence": opportunity.confidence,
                "weighted_arr_usd": str(opportunity.weighted),
                "signal": opportunity.rationale,
                "talk_track": opportunity.value_statement,
            }
            for opportunity in report["opportunities"]
        ],
        "data_coverage": report["coverage"],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def build_report(
    account: Account,
    usage: dict[tuple[str, str], dict[str, float]],
    goals: list[Goal],
    tickets: list[Ticket],
    products: list[Product],
    config: dict,
    as_of: date,
    paths: dict[str, str],
) -> dict:
    months = months_back(month_key(as_of), int(config["history_months"]))
    window = int(config["trend_window_months"])
    metrics = config["metrics"]

    adoption_series = usage.get((account.account_id, metrics["adoption"]), {})
    engagement_series = usage.get((account.account_id, metrics["engagement"]), {})
    licensed_series = usage.get((account.account_id, metrics["licensed"]), {})
    if not adoption_series:
        raise QbrError(
            f"usage: no rows for account {account.account_id!r} and metric"
            f" {metrics['adoption']!r}; the adoption section cannot be built"
        )

    adoption = build_trend(adoption_series, months, window)
    engagement = build_trend(engagement_series, months, window)
    latest_seats = None
    if adoption["latest_month"] is not None:
        latest_seats = licensed_series.get(adoption["latest_month"], float(account.licensed_seats))
    utilization = (
        adoption["latest_value"] / latest_seats
        if adoption["latest_value"] is not None and latest_seats
        else None
    )
    goal_rows = build_goals(goals, usage, account.account_id, months, config)
    support = build_support(tickets, as_of, config)
    risks = build_risks(
        account, adoption, engagement, utilization, goal_rows, support, as_of, config
    )
    opportunities = build_opportunities(
        account, usage, products, months, adoption["latest_value"], config
    )
    days_to_renewal = (account.renewal - as_of).days

    achieved = sum(1 for item in goal_rows if item["status"] in ("achieved", "on track"))
    summary_bullets = [
        f"Adoption: {format_number(adoption['latest_value'])} active users in"
        f" {adoption['latest_month']}, "
        + (
            "trend unavailable"
            if adoption["change_pct"] is None
            else f"{adoption['change_pct']:+.1f}% vs the prior {window}-month average"
        )
        + (f", {utilization * 100:.0f}% seat utilization" if utilization is not None else ""),
        f"Goals: {achieved} of {len(goal_rows)} on track or achieved"
        if goal_rows
        else "Goals: none on file for this account",
        f"Support: {plural(support['recent_count'], 'ticket')} in the last"
        f" {support['window_days']} days ({len(support['p1_recent'])} P1),"
        f" {len(support['open_tickets'])} still open",
        f"Risks: {sum(1 for risk in risks if risk.severity == 'high')} high,"
        f" {sum(1 for risk in risks if risk.severity == 'medium')} medium",
        f"Expansion: {plural(len(opportunities), 'opportunity', 'opportunities')} worth"
        f" {format_usd(sum((item.weighted for item in opportunities), Decimal('0')))} weighted",
        f"Renewal: {account.renewal.isoformat()} ({days_to_renewal}d), {format_usd(account.arr)} ARR",
    ]

    coverage = {
        metrics["adoption"]: [month for month in months if month not in adoption_series],
        metrics["engagement"]: [month for month in months if month not in engagement_series],
        metrics["licensed"]: [month for month in months if month not in licensed_series],
    }

    return {
        "account": account,
        "config": config,
        "as_of": as_of,
        "paths": paths,
        "months": months,
        "adoption": adoption,
        "engagement": engagement,
        "adoption_series": {m: adoption_series[m] for m in months if m in adoption_series},
        "engagement_series": {m: engagement_series[m] for m in months if m in engagement_series},
        "licensed_series": {m: licensed_series[m] for m in months if m in licensed_series},
        "utilization": utilization,
        "goals": goal_rows,
        "support": support,
        "risks": risks,
        "opportunities": opportunities,
        "days_to_renewal": days_to_renewal,
        "summary_bullets": summary_bullets,
        "coverage": coverage,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble a customer QBR from operational data.")
    parser.add_argument("--account-id", required=True, help="account to report on")
    parser.add_argument("--usage", required=True, help="CSV of monthly usage metrics (long format)")
    parser.add_argument("--goals", required=True, help="CSV of customer goals")
    parser.add_argument("--support", required=True, help="CSV of support tickets")
    parser.add_argument("--commercial", required=True, help="CSV of contract and ARR data")
    parser.add_argument("--products", required=True, help="CSV of the cross-sell catalog")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="QBR config JSON")
    parser.add_argument("--as-of", required=True, help="reporting date, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            raise QbrError(f"--as-of value {args.as_of!r} is not a YYYY-MM-DD date") from None
        config = load_config(Path(args.config))
        accounts = load_commercial(Path(args.commercial))
        account = accounts.get(args.account_id)
        if account is None:
            raise QbrError(
                f"commercial: no row for account_id {args.account_id!r};"
                f" known ids: {', '.join(sorted(accounts)) or '(none)'}"
            )
        usage = load_usage(Path(args.usage))
        goals = load_goals(Path(args.goals), args.account_id)
        tickets = load_support(Path(args.support), args.account_id, as_of)
        products = load_products(Path(args.products))
        report = build_report(
            account,
            usage,
            goals,
            tickets,
            products,
            config,
            as_of,
            {
                "usage": args.usage,
                "goals": args.goals,
                "support": args.support,
                "commercial": args.commercial,
                "products": args.products,
                "config": args.config,
            },
        )
    except QbrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(render_json(report) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
