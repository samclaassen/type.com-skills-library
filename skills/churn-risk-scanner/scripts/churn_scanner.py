#!/usr/bin/env python3
"""Score renewal risk across a book of business and rank it by ARR at risk.

Stdlib only, offline, deterministic. Every "today" comes from --as-of.

Seven weighted factors, each scored 0-100 where 100 is the worst:
  usage_decline        trailing vs prior average of key_actions
  user_attrition       trailing vs prior average of active_users
  license_utilization  latest active users against licensed seats
  support_burden       ticket load per 100 seats, weighted for P1s and escalations
  sentiment            average CSAT in the sentiment window
  champion_departure   champion / economic buyer status
  invoice_history      average days late plus overdue invoices

    risk score = sum(factor score x weight) / sum(weight of available factors)

A factor with no data is excluded and the remaining weights are renormalized;
the report states which factors were unavailable rather than scoring them zero.

    weighted ARR at risk = ARR x risk score / 100

Exit codes: 0 ok, 2 bad input (message names the file, row and column).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config" / "risk_model.json"

ACCOUNT_COLUMNS = [
    "account_id",
    "account_name",
    "arr_usd",
    "segment",
    "contract_start",
    "renewal_date",
    "csm_owner",
]
USAGE_COLUMNS = ["account_id", "month", "active_users", "licensed_seats", "key_actions"]
SUPPORT_COLUMNS = [
    "ticket_id",
    "account_id",
    "created_date",
    "resolved_date",
    "priority",
    "escalated",
    "csat_score",
]
CONTACT_COLUMNS = ["contact_id", "account_id", "full_name", "role", "status", "departure_date"]
INVOICE_COLUMNS = ["invoice_id", "account_id", "invoice_date", "due_date", "paid_date", "amount_usd"]

FACTOR_ORDER = [
    "usage_decline",
    "user_attrition",
    "license_utilization",
    "support_burden",
    "sentiment",
    "champion_departure",
    "invoice_history",
]
TRUE_VALUES = {"yes", "y", "true", "t", "1"}
FALSE_VALUES = {"no", "n", "false", "f", "0", ""}


class ChurnError(Exception):
    """Raised for any unusable input; the message names the offending field."""


@dataclass
class Account:
    row_no: int
    account_id: str
    account_name: str
    arr: Decimal
    segment: str
    contract_start: date
    renewal: date
    csm_owner: str


@dataclass
class UsageRow:
    month: str
    active_users: float
    licensed_seats: float
    key_actions: float


@dataclass
class Ticket:
    ticket_id: str
    created: date
    resolved: date | None
    priority: str
    escalated: bool
    csat: float | None


@dataclass
class Contact:
    contact_id: str
    full_name: str
    role: str
    status: str
    departure: date | None


@dataclass
class Invoice:
    invoice_id: str
    invoice_date: date
    due_date: date
    paid_date: date | None
    amount: Decimal


@dataclass
class FactorResult:
    code: str
    weight: float
    score: float | None
    label: str
    evidence: str

    @property
    def available(self) -> bool:
        return self.score is not None

    contribution: float = 0.0


@dataclass
class AccountResult:
    account: Account
    factors: list[FactorResult]
    risk_score: float
    available_weight: float
    band: str
    arr_at_risk: Decimal
    days_to_renewal: int
    unavailable: list[str] = field(default_factory=list)


def read_rows(path: Path, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    if not path.is_file():
        raise ChurnError(f"{label}: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ChurnError(
                f"{label}: {path} is empty; expected a header row with: {', '.join(required)}"
            )
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise ChurnError(f"{label}: {path} is missing required column(s): {', '.join(missing)}")
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
        raise ChurnError(f"{label}: {path} has a header but no data rows")
    return rows


def parse_float(value: str, column: str, row_no: int, label: str) -> float:
    if not value:
        raise ChurnError(f"{label} row {row_no}: column '{column}' is required but empty")
    try:
        return float(value.replace(",", "").replace("$", ""))
    except ValueError:
        raise ChurnError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a number"
        ) from None


def parse_money(value: str, column: str, row_no: int, label: str) -> Decimal:
    if not value:
        raise ChurnError(f"{label} row {row_no}: column '{column}' is required but empty")
    try:
        amount = Decimal(value.replace(",", "").replace("$", ""))
    except InvalidOperation:
        raise ChurnError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a number"
        ) from None
    if amount < 0:
        raise ChurnError(f"{label} row {row_no}: column '{column}' value {value!r} is negative")
    return amount


def parse_date_value(value: str, column: str, row_no: int, label: str, required: bool) -> date | None:
    if not value:
        if required:
            raise ChurnError(f"{label} row {row_no}: column '{column}' is required but empty")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ChurnError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a YYYY-MM-DD date"
        ) from None


def parse_flag(value: str, column: str, row_no: int, label: str) -> bool:
    lowered = value.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ChurnError(f"{label} row {row_no}: column '{column}' value {value!r} is not yes/no")


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise ChurnError(f"config: file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ChurnError(f"config: {path} is not valid JSON ({exc})") from None
    for key in (
        "history_months",
        "trend_window_months",
        "support_window_days",
        "csat_window_days",
        "invoice_window_days",
        "champion_departure_window_days",
        "champion_roles",
        "support_index",
        "invoice_index",
        "bands",
        "factors",
    ):
        if key not in config:
            raise ChurnError(f"config: {path} is missing key {key!r}")
    for code in FACTOR_ORDER:
        if code not in config["factors"]:
            raise ChurnError(f"config: factors is missing an entry for {code!r}")
        factor = config["factors"][code]
        if float(factor["weight"]) < 0:
            raise ChurnError(f"config: factors.{code}.weight must not be negative")
    total_weight = sum(float(config["factors"][code]["weight"]) for code in FACTOR_ORDER)
    if total_weight <= 0:
        raise ChurnError("config: the factor weights sum to zero; nothing could be scored")
    if int(config["history_months"]) < 2 * int(config["trend_window_months"]):
        raise ChurnError(
            "config: history_months must cover at least two trend windows"
            f" ({2 * int(config['trend_window_months'])})"
        )
    if not config["bands"]:
        raise ChurnError("config: bands must list at least one risk band")
    return config


def load_accounts(path: Path) -> dict[str, Account]:
    rows = read_rows(path, ACCOUNT_COLUMNS, "accounts")
    accounts: dict[str, Account] = {}
    for row, row_no in rows:
        account_id = row["account_id"]
        if not account_id:
            raise ChurnError(f"accounts row {row_no}: column 'account_id' is required but empty")
        if account_id in accounts:
            raise ChurnError(f"accounts row {row_no}: account_id {account_id!r} appears twice")
        accounts[account_id] = Account(
            row_no=row_no,
            account_id=account_id,
            account_name=row["account_name"] or account_id,
            arr=parse_money(row["arr_usd"], "arr_usd", row_no, "accounts"),
            segment=row["segment"] or "(unsegmented)",
            contract_start=parse_date_value(
                row["contract_start"], "contract_start", row_no, "accounts", True
            ),
            renewal=parse_date_value(row["renewal_date"], "renewal_date", row_no, "accounts", True),
            csm_owner=row["csm_owner"] or "(unassigned)",
        )
    return accounts


def load_usage(path: Path, accounts: dict[str, Account]) -> dict[str, dict[str, UsageRow]]:
    rows = read_rows(path, USAGE_COLUMNS, "usage")
    usage: dict[str, dict[str, UsageRow]] = {}
    for row, row_no in rows:
        account_id = row["account_id"]
        if account_id not in accounts:
            raise ChurnError(
                f"usage row {row_no}: account_id {account_id!r} is not present in the accounts file"
            )
        month = row["month"]
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise ChurnError(
                f"usage row {row_no}: column 'month' value {month!r} is not a YYYY-MM month"
            ) from None
        bucket = usage.setdefault(account_id, {})
        if month in bucket:
            raise ChurnError(
                f"usage row {row_no}: duplicate row for account {account_id!r}, month {month}"
            )
        seats = parse_float(row["licensed_seats"], "licensed_seats", row_no, "usage")
        if seats <= 0:
            raise ChurnError(
                f"usage row {row_no}: column 'licensed_seats' must be greater than zero"
                " (utilization would divide by zero)"
            )
        bucket[month] = UsageRow(
            month=month,
            active_users=parse_float(row["active_users"], "active_users", row_no, "usage"),
            licensed_seats=seats,
            key_actions=parse_float(row["key_actions"], "key_actions", row_no, "usage"),
        )
    return usage


def load_support(
    path: Path, accounts: dict[str, Account], as_of: date
) -> dict[str, list[Ticket]]:
    rows = read_rows(path, SUPPORT_COLUMNS, "support")
    tickets: dict[str, list[Ticket]] = {}
    seen: set[str] = set()
    for row, row_no in rows:
        account_id = row["account_id"]
        if account_id not in accounts:
            raise ChurnError(
                f"support row {row_no}: account_id {account_id!r} is not present in the accounts file"
            )
        ticket_id = row["ticket_id"]
        if ticket_id in seen:
            raise ChurnError(f"support row {row_no}: ticket_id {ticket_id!r} appears twice")
        seen.add(ticket_id)
        created = parse_date_value(row["created_date"], "created_date", row_no, "support", True)
        if created > as_of:
            raise ChurnError(
                f"support row {row_no}: 'created_date' {created.isoformat()} is after"
                f" --as-of {as_of.isoformat()}"
            )
        resolved = parse_date_value(row["resolved_date"], "resolved_date", row_no, "support", False)
        if resolved is not None and resolved < created:
            raise ChurnError(
                f"support row {row_no}: 'resolved_date' {resolved.isoformat()} is before"
                f" 'created_date' {created.isoformat()}"
            )
        csat = None
        if row["csat_score"]:
            csat = parse_float(row["csat_score"], "csat_score", row_no, "support")
            if not 1.0 <= csat <= 5.0:
                raise ChurnError(
                    f"support row {row_no}: column 'csat_score' value {row['csat_score']!r}"
                    " is outside the 1-5 scale"
                )
        tickets.setdefault(account_id, []).append(
            Ticket(
                ticket_id=ticket_id,
                created=created,
                resolved=resolved,
                priority=row["priority"].lower(),
                escalated=parse_flag(row["escalated"], "escalated", row_no, "support"),
                csat=csat,
            )
        )
    for values in tickets.values():
        values.sort(key=lambda ticket: (ticket.created, ticket.ticket_id))
    return tickets


def load_contacts(path: Path, accounts: dict[str, Account]) -> dict[str, list[Contact]]:
    rows = read_rows(path, CONTACT_COLUMNS, "contacts")
    contacts: dict[str, list[Contact]] = {}
    seen: set[str] = set()
    for row, row_no in rows:
        account_id = row["account_id"]
        if account_id not in accounts:
            raise ChurnError(
                f"contacts row {row_no}: account_id {account_id!r} is not present in the accounts file"
            )
        contact_id = row["contact_id"]
        if contact_id in seen:
            raise ChurnError(f"contacts row {row_no}: contact_id {contact_id!r} appears twice")
        seen.add(contact_id)
        status = row["status"].lower()
        if status not in {"active", "departed"}:
            raise ChurnError(
                f"contacts row {row_no}: column 'status' value {row['status']!r}"
                " must be active or departed"
            )
        departure = parse_date_value(
            row["departure_date"], "departure_date", row_no, "contacts", False
        )
        if status == "departed" and departure is None:
            raise ChurnError(
                f"contacts row {row_no}: 'departure_date' is required when status is departed"
            )
        contacts.setdefault(account_id, []).append(
            Contact(
                contact_id=contact_id,
                full_name=row["full_name"],
                role=row["role"].lower(),
                status=status,
                departure=departure,
            )
        )
    for values in contacts.values():
        values.sort(key=lambda contact: contact.contact_id)
    return contacts


def load_invoices(path: Path, accounts: dict[str, Account]) -> dict[str, list[Invoice]]:
    rows = read_rows(path, INVOICE_COLUMNS, "invoices")
    invoices: dict[str, list[Invoice]] = {}
    seen: set[str] = set()
    for row, row_no in rows:
        account_id = row["account_id"]
        if account_id not in accounts:
            raise ChurnError(
                f"invoices row {row_no}: account_id {account_id!r} is not present in the accounts file"
            )
        invoice_id = row["invoice_id"]
        if invoice_id in seen:
            raise ChurnError(f"invoices row {row_no}: invoice_id {invoice_id!r} appears twice")
        seen.add(invoice_id)
        invoice_date = parse_date_value(row["invoice_date"], "invoice_date", row_no, "invoices", True)
        due_date = parse_date_value(row["due_date"], "due_date", row_no, "invoices", True)
        if due_date < invoice_date:
            raise ChurnError(
                f"invoices row {row_no}: 'due_date' {due_date.isoformat()} is before"
                f" 'invoice_date' {invoice_date.isoformat()}"
            )
        invoices.setdefault(account_id, []).append(
            Invoice(
                invoice_id=invoice_id,
                invoice_date=invoice_date,
                due_date=due_date,
                paid_date=parse_date_value(row["paid_date"], "paid_date", row_no, "invoices", False),
                amount=parse_money(row["amount_usd"], "amount_usd", row_no, "invoices"),
            )
        )
    for values in invoices.values():
        values.sort(key=lambda invoice: (invoice.due_date, invoice.invoice_id))
    return invoices


def months_back(anchor: str, count: int) -> list[str]:
    year, month = (int(part) for part in anchor.split("-"))
    result: list[str] = []
    for _ in range(count):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(result))


def band_score(value: float, bands: list[dict], code: str) -> tuple[float, str]:
    for band in bands:
        if "at_or_below" in band:
            threshold = band["at_or_below"]
            if threshold is None or value <= float(threshold):
                return float(band["score"]), band["label"]
        elif "at_or_above" in band:
            threshold = band["at_or_above"]
            if threshold is None or value >= float(threshold):
                return float(band["score"]), band["label"]
        else:
            raise ChurnError(
                f"config: factors.{code} has a band with neither 'at_or_below' nor 'at_or_above'"
            )
    raise ChurnError(
        f"config: factors.{code} has no catch-all band; add one with a null threshold"
    )


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def trend_factor(
    code: str,
    series: dict[str, float],
    months: list[str],
    window: int,
    config: dict,
    unit: str,
) -> FactorResult:
    factor = config["factors"][code]
    weight = float(factor["weight"])
    recent_months = months[-window:]
    prior_months = months[-2 * window : -window]
    recent = average([series[month] for month in recent_months if month in series])
    prior = average([series[month] for month in prior_months if month in series])
    if recent is None or prior is None:
        return FactorResult(
            code,
            weight,
            None,
            "no data",
            f"needs {window} months of {unit} in both windows"
            f" ({prior_months[0]}-{prior_months[-1]} and {recent_months[0]}-{recent_months[-1]})",
        )
    if prior == 0:
        return FactorResult(
            code, weight, None, "no data", f"prior-window {unit} is zero; percent change undefined"
        )
    change = (recent - prior) / prior * 100.0
    score, label = band_score(change, factor["bands"], code)
    return FactorResult(
        code,
        weight,
        score,
        label,
        f"{unit} {prior:,.0f} -> {recent:,.0f} ({change:+.1f}%) across"
        f" {window}-month windows",
    )


def utilization_factor(
    series: dict[str, UsageRow], months: list[str], config: dict
) -> FactorResult:
    factor = config["factors"]["license_utilization"]
    weight = float(factor["weight"])
    latest_month = next((month for month in reversed(months) if month in series), None)
    if latest_month is None:
        return FactorResult(
            "license_utilization", weight, None, "no data", "no usage rows in the reporting window"
        )
    row = series[latest_month]
    utilization = row.active_users / row.licensed_seats
    score, label = band_score(utilization, factor["bands"], "license_utilization")
    return FactorResult(
        "license_utilization",
        weight,
        score,
        label,
        f"{row.active_users:,.0f} of {row.licensed_seats:,.0f} seats active in {latest_month}"
        f" ({utilization * 100:.0f}%)",
    )


def support_factor(
    tickets: list[Ticket],
    series: dict[str, UsageRow],
    months: list[str],
    as_of: date,
    config: dict,
) -> FactorResult:
    factor = config["factors"]["support_burden"]
    weight = float(factor["weight"])
    window = int(config["support_window_days"])
    start = date.fromordinal(as_of.toordinal() - window + 1)
    latest_month = next((month for month in reversed(months) if month in series), None)
    if latest_month is None:
        return FactorResult(
            "support_burden", weight, None, "no data", "no seat count available to normalize tickets"
        )
    seats = series[latest_month].licensed_seats
    recent = [ticket for ticket in tickets if start <= ticket.created <= as_of]
    p1_count = sum(1 for ticket in recent if ticket.priority == "p1")
    escalations = sum(1 for ticket in recent if ticket.escalated)
    per_hundred = len(recent) / seats * 100.0
    index = (
        per_hundred
        + p1_count * float(config["support_index"]["p1_points"])
        + escalations * float(config["support_index"]["escalation_points"])
    )
    score, label = band_score(index, factor["bands"], "support_burden")
    return FactorResult(
        "support_burden",
        weight,
        score,
        label,
        f"{len(recent)} ticket(s) in {window}d = {per_hundred:.1f}/100 seats,"
        f" {p1_count} P1, {escalations} escalation(s); index {index:.1f}",
    )


def sentiment_factor(tickets: list[Ticket], as_of: date, config: dict) -> FactorResult:
    factor = config["factors"]["sentiment"]
    weight = float(factor["weight"])
    window = int(config["csat_window_days"])
    start = date.fromordinal(as_of.toordinal() - window + 1)
    scores = [
        ticket.csat
        for ticket in tickets
        if ticket.csat is not None and start <= ticket.created <= as_of
    ]
    if not scores:
        return FactorResult(
            "sentiment", weight, None, "no data", f"no CSAT responses in the last {window}d"
        )
    mean = statistics.fmean(scores)
    score, label = band_score(mean, factor["bands"], "sentiment")
    return FactorResult(
        "sentiment",
        weight,
        score,
        label,
        f"average CSAT {mean:.2f} across {len(scores)} response(s) in {window}d",
    )


def champion_factor(contacts: list[Contact], as_of: date, config: dict) -> FactorResult:
    factor = config["factors"]["champion_departure"]
    weight = float(factor["weight"])
    scores = factor["scores"]
    roles = {role.lower() for role in config["champion_roles"]}
    window = int(config["champion_departure_window_days"])
    champions = [contact for contact in contacts if contact.role in roles]
    if not champions:
        return FactorResult(
            "champion_departure",
            weight,
            float(scores["no_champion_on_record"]),
            "no champion on record",
            "no contact carries a champion or economic-buyer role",
        )
    active = [contact for contact in champions if contact.status == "active"]
    departed = [contact for contact in champions if contact.status == "departed"]
    recent_departures = [
        contact
        for contact in departed
        if contact.departure is not None and (as_of - contact.departure).days <= window
    ]
    if not active and departed:
        latest = max(contact.departure for contact in departed if contact.departure)
        days = (as_of - latest).days
        if recent_departures:
            return FactorResult(
                "champion_departure",
                weight,
                float(scores["all_champions_departed_recently"]),
                "every champion has left",
                f"{len(departed)} champion contact(s) departed, most recently"
                f" {latest.isoformat()} ({days}d ago); no active replacement",
            )
        return FactorResult(
            "champion_departure",
            weight,
            float(scores["all_champions_departed_earlier"]),
            "no active champion",
            f"last champion departed {latest.isoformat()} ({days}d ago), outside the {window}d window",
        )
    if recent_departures:
        names = ", ".join(
            f"{contact.full_name} ({contact.departure.isoformat()})" for contact in recent_departures
        )
        return FactorResult(
            "champion_departure",
            weight,
            float(scores["partial_departure"]),
            "a champion left, one remains",
            f"departed: {names}; {len(active)} active champion contact(s) remain",
        )
    return FactorResult(
        "champion_departure",
        weight,
        float(scores["champion_active"]),
        "champion in place",
        f"{len(active)} active champion contact(s), no departures in {window}d",
    )


def invoice_factor(invoices: list[Invoice], as_of: date, config: dict) -> FactorResult:
    factor = config["factors"]["invoice_history"]
    weight = float(factor["weight"])
    window = int(config["invoice_window_days"])
    start = date.fromordinal(as_of.toordinal() - window + 1)
    relevant = [invoice for invoice in invoices if start <= invoice.due_date <= as_of]
    if not relevant:
        return FactorResult(
            "invoice_history", weight, None, "no data", f"no invoices due in the last {window}d"
        )
    paid = [invoice for invoice in relevant if invoice.paid_date is not None]
    late_days = [max(0, (invoice.paid_date - invoice.due_date).days) for invoice in paid]
    overdue = [invoice for invoice in relevant if invoice.paid_date is None]
    mean_late = average([float(days) for days in late_days]) or 0.0
    index = mean_late + len(overdue) * float(config["invoice_index"]["overdue_points"])
    score, label = band_score(index, factor["bands"], "invoice_history")
    overdue_amount = sum((invoice.amount for invoice in overdue), Decimal("0"))
    evidence = (
        f"{len(paid)} paid invoice(s), average {mean_late:.1f}d late;"
        f" {len(overdue)} unpaid past due"
    )
    if overdue:
        evidence += f" ({format_usd(overdue_amount)} outstanding)"
    evidence += f"; index {index:.1f}"
    return FactorResult("invoice_history", weight, score, label, evidence)


def band_for(score: float, config: dict) -> str:
    for band in sorted(config["bands"], key=lambda item: -float(item["min_score"])):
        if score >= float(band["min_score"]):
            return band["name"]
    return config["bands"][-1]["name"]


def score_account(
    account: Account,
    usage: dict[str, UsageRow],
    tickets: list[Ticket],
    contacts: list[Contact],
    invoices: list[Invoice],
    months: list[str],
    config: dict,
    as_of: date,
) -> AccountResult:
    window = int(config["trend_window_months"])
    factors = [
        trend_factor(
            "usage_decline",
            {month: row.key_actions for month, row in usage.items()},
            months,
            window,
            config,
            "key actions",
        ),
        trend_factor(
            "user_attrition",
            {month: row.active_users for month, row in usage.items()},
            months,
            window,
            config,
            "active users",
        ),
        utilization_factor(usage, months, config),
        support_factor(tickets, usage, months, as_of, config),
        sentiment_factor(tickets, as_of, config),
        champion_factor(contacts, as_of, config),
        invoice_factor(invoices, as_of, config),
    ]
    factors.sort(key=lambda item: FACTOR_ORDER.index(item.code))

    available = [item for item in factors if item.available]
    available_weight = sum(item.weight for item in available)
    if available_weight <= 0:
        raise ChurnError(
            f"account {account.account_id!r}: no factor had usable data; supply usage,"
            " support, contact or invoice rows before scoring this account"
        )
    for item in available:
        item.contribution = round(item.score * item.weight / available_weight, 2)
    risk_score = round(sum(item.contribution for item in available), 1)
    arr_at_risk = (account.arr * Decimal(str(risk_score)) / Decimal("100")).quantize(Decimal("1"))
    return AccountResult(
        account=account,
        factors=factors,
        risk_score=risk_score,
        available_weight=available_weight,
        band=band_for(risk_score, config),
        arr_at_risk=arr_at_risk,
        days_to_renewal=(account.renewal - as_of).days,
        unavailable=[item.code for item in factors if not item.available],
    )


def format_usd(amount: Decimal) -> str:
    return f"${amount.quantize(Decimal('1')):,}"


def pct(numerator: Decimal, denominator: Decimal) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(numerator / denominator * 100):.1f}%"


def render_text(results: list[AccountResult], config: dict, as_of: date, paths: dict, limit: int) -> str:
    lines: list[str] = []
    out = lines.append
    total_arr = sum((item.account.arr for item in results), Decimal("0"))
    total_at_risk = sum((item.arr_at_risk for item in results), Decimal("0"))

    out(f"CHURN RISK SCAN — as of {as_of.isoformat()}")
    out(f"accounts: {paths['accounts']}   usage: {paths['usage']}   support: {paths['support']}")
    out(f"contacts: {paths['contacts']}   invoices: {paths['invoices']}   model: {paths['config']}")
    weights = ", ".join(
        f"{code} {float(config['factors'][code]['weight']):g}" for code in FACTOR_ORDER
    )
    out(f"weights: {weights}")
    out("scoring: sum(factor score x weight) / weight of available factors; ARR at risk = ARR x score/100")
    out("")

    out("BOOK SUMMARY")
    out(f"  Accounts scored            {len(results)}")
    out(f"  Book ARR                   {format_usd(total_arr)}")
    out(f"  Weighted ARR at risk       {format_usd(total_at_risk)} ({pct(total_at_risk, total_arr)} of book)")
    for band in config["bands"]:
        bucket = [item for item in results if item.band == band["name"]]
        if not bucket:
            continue
        band_arr = sum((item.arr_at_risk for item in bucket), Decimal("0"))
        threshold = f"(score >= {float(band['min_score']):g})"
        out(
            f"  {band['name']:<12}{threshold:<18}{len(bucket):>3} account(s)"
            f"   {format_usd(band_arr)} at risk"
        )
    out("")

    out("FACTOR PREVALENCE (accounts scoring 50 or higher on each factor)")
    for code in FACTOR_ORDER:
        flagged = [
            item
            for item in results
            for factor in item.factors
            if factor.code == code and factor.available and factor.score >= 50
        ]
        unavailable = [
            item
            for item in results
            for factor in item.factors
            if factor.code == code and not factor.available
        ]
        out(
            f"  {code:<20}{len(flagged):>3} flagged"
            f"   {len(unavailable):>3} without data"
        )
    out("")

    shown = results[:limit] if limit > 0 else results
    out(f"RANKED BOOK — by weighted ARR at risk ({len(shown)} of {len(results)} shown)")
    for rank, item in enumerate(shown, start=1):
        account = item.account
        out("")
        out(
            f"  {rank}. [{item.band:^8} {item.risk_score:>5.1f}] {account.account_id}"
            f"  {account.account_name}"
        )
        out(
            f"      ARR {format_usd(account.arr)} -> {format_usd(item.arr_at_risk)} at risk"
            f"   renews {account.renewal.isoformat()} ({item.days_to_renewal}d)"
            f"   segment {account.segment}   CSM {account.csm_owner}"
        )
        ranked = sorted(
            (factor for factor in item.factors if factor.available),
            key=lambda factor: (-factor.contribution, FACTOR_ORDER.index(factor.code)),
        )
        top = [factor for factor in ranked if factor.contribution > 0][:3]
        if top:
            out(
                "      top reasons: "
                + "; ".join(f"{factor.code} ({factor.contribution:.1f} pts)" for factor in top)
            )
        for factor in ranked:
            out(
                f"        {factor.code:<20} score {factor.score:>5.1f}"
                f"  weight {factor.weight:>4.0f}  contributes {factor.contribution:>5.1f}"
                f"  {factor.label}"
            )
            out(f"          {factor.evidence}")
        for factor in item.factors:
            if not factor.available:
                out(
                    f"        {factor.code:<20} NO DATA — weight {factor.weight:g} redistributed"
                    f"  ({factor.evidence})"
                )
        if item.unavailable:
            out(
                f"      scored on {item.available_weight:g} of"
                f" {sum(float(config['factors'][code]['weight']) for code in FACTOR_ORDER):g}"
                " available weight points"
            )
    return "\n".join(lines) + "\n"


def render_json(results: list[AccountResult], config: dict, as_of: date, paths: dict) -> str:
    total_arr = sum((item.account.arr for item in results), Decimal("0"))
    total_at_risk = sum((item.arr_at_risk for item in results), Decimal("0"))
    payload = {
        "as_of": as_of.isoformat(),
        "inputs": paths,
        "weights": {
            code: float(config["factors"][code]["weight"]) for code in FACTOR_ORDER
        },
        "summary": {
            "accounts": len(results),
            "book_arr_usd": str(total_arr.quantize(Decimal("1"))),
            "weighted_arr_at_risk_usd": str(total_at_risk.quantize(Decimal("1"))),
            "by_band": {
                band["name"]: {
                    "accounts": len([item for item in results if item.band == band["name"]]),
                    "arr_at_risk_usd": str(
                        sum(
                            (item.arr_at_risk for item in results if item.band == band["name"]),
                            Decimal("0"),
                        ).quantize(Decimal("1"))
                    ),
                }
                for band in config["bands"]
            },
        },
        "accounts": [
            {
                "account_id": item.account.account_id,
                "account_name": item.account.account_name,
                "segment": item.account.segment,
                "csm_owner": item.account.csm_owner,
                "arr_usd": str(item.account.arr.quantize(Decimal("1"))),
                "renewal_date": item.account.renewal.isoformat(),
                "days_to_renewal": item.days_to_renewal,
                "risk_score": item.risk_score,
                "band": item.band,
                "arr_at_risk_usd": str(item.arr_at_risk),
                "available_weight": item.available_weight,
                "factors": [
                    {
                        "code": factor.code,
                        "weight": factor.weight,
                        "score": factor.score,
                        "contribution": factor.contribution if factor.available else None,
                        "label": factor.label,
                        "evidence": factor.evidence,
                        "available": factor.available,
                    }
                    for factor in item.factors
                ],
            }
            for item in results
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank a book of business by renewal risk.")
    parser.add_argument("--accounts", required=True, help="CSV of accounts, ARR and renewal dates")
    parser.add_argument("--usage", required=True, help="CSV of monthly usage per account")
    parser.add_argument("--support", required=True, help="CSV of support tickets")
    parser.add_argument("--contacts", required=True, help="CSV of account contacts and roles")
    parser.add_argument("--invoices", required=True, help="CSV of invoices")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="risk model JSON")
    parser.add_argument("--as-of", required=True, help="scoring date, YYYY-MM-DD")
    parser.add_argument(
        "--renewal-within",
        type=int,
        default=0,
        help="only score accounts renewing within N days (0 = whole book)",
    )
    parser.add_argument(
        "--top", type=int, default=0, help="print only the top N accounts (0 = all)"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            raise ChurnError(f"--as-of value {args.as_of!r} is not a YYYY-MM-DD date") from None
        if args.renewal_within < 0:
            raise ChurnError("--renewal-within must not be negative")
        config = load_config(Path(args.config))
        accounts = load_accounts(Path(args.accounts))
        usage = load_usage(Path(args.usage), accounts)
        support = load_support(Path(args.support), accounts, as_of)
        contacts = load_contacts(Path(args.contacts), accounts)
        invoices = load_invoices(Path(args.invoices), accounts)

        anchor = f"{as_of.year:04d}-{as_of.month:02d}"
        months = months_back(anchor, int(config["history_months"]))
        selected = [
            account
            for account in accounts.values()
            if args.renewal_within == 0
            or 0 <= (account.renewal - as_of).days <= args.renewal_within
        ]
        if not selected:
            raise ChurnError(
                f"no account renews within {args.renewal_within} days of {as_of.isoformat()};"
                " widen --renewal-within or drop the filter"
            )
        results = [
            score_account(
                account,
                usage.get(account.account_id, {}),
                support.get(account.account_id, []),
                contacts.get(account.account_id, []),
                invoices.get(account.account_id, []),
                months,
                config,
                as_of,
            )
            for account in sorted(selected, key=lambda item: item.account_id)
        ]
        results.sort(key=lambda item: (-item.arr_at_risk, -item.risk_score, item.account.account_id))
    except ChurnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    paths = {
        "accounts": args.accounts,
        "usage": args.usage,
        "support": args.support,
        "contacts": args.contacts,
        "invoices": args.invoices,
        "config": args.config,
    }
    sys.stdout.write(
        render_json(results, config, as_of, paths)
        if args.json
        else render_text(results, config, as_of, paths, args.top)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
