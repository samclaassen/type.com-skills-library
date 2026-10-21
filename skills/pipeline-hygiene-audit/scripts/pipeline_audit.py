#!/usr/bin/env python3
"""Audit a CRM deal export for pipeline data-integrity problems.

Stdlib only, offline, deterministic. Every "today" comes from --as-of.

Checks performed on open deals:
  missing_owner      no owner on the record
  missing_amount     amount blank or below the configured floor
  close_date_past    close date is on or before --as-of while the deal is open
  stale_activity     no logged activity for longer than the stale threshold
  stuck_in_stage     days in current stage above the stage median x multiplier
  stage_skipped      stage history jumps forward past one or more stages
  duplicate_account  account name/domain collides with another account id

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
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config" / "rules.json"

DEAL_COLUMNS = [
    "deal_id",
    "account_id",
    "account_name",
    "owner_name",
    "stage",
    "amount_usd",
    "created_date",
    "close_date",
    "last_activity_date",
    "stage_entered_date",
    "status",
]
STAGE_COLUMNS = ["stage_name", "stage_order"]
HISTORY_COLUMNS = ["deal_id", "stage", "entered_date"]

FINDING_ORDER = [
    "missing_owner",
    "missing_amount",
    "close_date_past",
    "stale_activity",
    "stuck_in_stage",
    "stage_skipped",
    "duplicate_account",
]

ACTIONS = {
    "missing_owner": "Assign an owner before the next forecast call.",
    "missing_amount": "Ask the rep for a sized amount or mark the deal closed lost.",
    "close_date_past": "Re-date the close or move the deal out of the open forecast.",
    "stale_activity": "Log the last touch or schedule a next step this week.",
    "stuck_in_stage": "Run an exit-criteria check; advance, push, or disqualify.",
    "stage_skipped": "Backfill the skipped stage so cycle-time reporting stays usable.",
    "duplicate_account": "Merge the duplicate account records in the CRM.",
}


class AuditError(Exception):
    """Raised for any unusable input; the message names the offending field."""


@dataclass
class Finding:
    code: str
    detail: str
    points: int
    score: float


@dataclass
class Deal:
    row_no: int
    deal_id: str
    account_id: str
    account_name: str
    account_domain: str
    owner: str
    stage: str
    stage_order: int
    amount: Decimal | None
    created: date
    close: date | None
    last_activity: date | None
    stage_entered: date
    status: str
    days_in_stage: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def owner_label(self) -> str:
        return self.owner or "(unassigned)"

    @property
    def score(self) -> float:
        return round(sum(f.score for f in self.findings), 1)

    @property
    def amount_or_zero(self) -> Decimal:
        return self.amount if self.amount is not None else Decimal("0")


def read_rows(path: Path, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    if not path.is_file():
        raise AuditError(f"{label}: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AuditError(
                f"{label}: {path} is empty; expected a header row with: {', '.join(required)}"
            )
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise AuditError(
                f"{label}: {path} is missing required column(s): {', '.join(missing)}"
            )
        rows: list[tuple[dict[str, str], int]] = []
        for row_no, raw in enumerate(reader, start=2):
            clean = {
                (key or "").strip(): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            rows.append((clean, row_no))
    if not rows:
        raise AuditError(f"{label}: {path} has a header but no data rows")
    return rows


def parse_date(value: str, column: str, row_no: int, label: str, required: bool) -> date | None:
    if not value:
        if required:
            raise AuditError(f"{label} row {row_no}: column '{column}' is required but empty")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise AuditError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a YYYY-MM-DD date"
        ) from None


def parse_money(value: str, column: str, row_no: int, label: str) -> Decimal | None:
    if not value:
        return None
    cleaned = value.replace(",", "").replace("$", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise AuditError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a number"
        ) from None
    if amount < 0:
        raise AuditError(
            f"{label} row {row_no}: column '{column}' value {value!r} is negative"
        )
    return amount


def parse_int(value: str, column: str, row_no: int, label: str) -> int:
    if not value:
        raise AuditError(f"{label} row {row_no}: column '{column}' is required but empty")
    try:
        return int(value)
    except ValueError:
        raise AuditError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not an integer"
        ) from None


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise AuditError(f"config: file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuditError(f"config: {path} is not valid JSON ({exc})") from None
    required = [
        "stale_activity_days",
        "stuck_multiplier",
        "stuck_min_sample",
        "min_amount_usd",
        "amount_reference_usd",
        "amount_weight_cap",
        "severity_points",
        "legal_suffixes",
    ]
    missing = [key for key in required if key not in config]
    if missing:
        raise AuditError(f"config: {path} is missing key(s): {', '.join(missing)}")
    for code in FINDING_ORDER:
        if code not in config["severity_points"]:
            raise AuditError(f"config: severity_points is missing an entry for {code!r}")
        points = config["severity_points"][code]
        if not isinstance(points, int) or points < 0:
            raise AuditError(
                f"config: severity_points.{code} must be a non-negative integer, got {points!r}"
            )
    if config["amount_reference_usd"] <= 0:
        raise AuditError("config: amount_reference_usd must be greater than zero")
    if config["stuck_min_sample"] < 1:
        raise AuditError("config: stuck_min_sample must be at least 1")
    return config


def load_stages(path: Path) -> dict[str, int]:
    rows = read_rows(path, STAGE_COLUMNS, "stages")
    stages: dict[str, int] = {}
    for row, row_no in rows:
        name = row["stage_name"]
        if not name:
            raise AuditError(f"stages row {row_no}: column 'stage_name' is required but empty")
        if name in stages:
            raise AuditError(f"stages row {row_no}: stage {name!r} is listed twice")
        stages[name] = parse_int(row["stage_order"], "stage_order", row_no, "stages")
    return stages


def load_deals(path: Path, stages: dict[str, int], as_of: date) -> list[Deal]:
    rows = read_rows(path, DEAL_COLUMNS, "deals")
    deals: list[Deal] = []
    seen: dict[str, int] = {}
    for row, row_no in rows:
        deal_id = row["deal_id"]
        if not deal_id:
            raise AuditError(f"deals row {row_no}: column 'deal_id' is required but empty")
        if deal_id in seen:
            raise AuditError(
                f"deals row {row_no}: deal_id {deal_id!r} already appeared on row {seen[deal_id]}"
            )
        seen[deal_id] = row_no
        stage = row["stage"]
        if stage not in stages:
            raise AuditError(
                f"deals row {row_no}: column 'stage' value {stage!r} is not listed in the stages file"
            )
        status = row["status"].lower()
        if status not in {"open", "won", "lost"}:
            raise AuditError(
                f"deals row {row_no}: column 'status' value {row['status']!r} must be open, won or lost"
            )
        created = parse_date(row["created_date"], "created_date", row_no, "deals", True)
        stage_entered = parse_date(
            row["stage_entered_date"], "stage_entered_date", row_no, "deals", True
        )
        if stage_entered > as_of:
            raise AuditError(
                f"deals row {row_no}: column 'stage_entered_date' value {row['stage_entered_date']!r}"
                f" is after --as-of {as_of.isoformat()}"
            )
        deals.append(
            Deal(
                row_no=row_no,
                deal_id=deal_id,
                account_id=row["account_id"],
                account_name=row["account_name"],
                account_domain=row.get("account_domain", ""),
                owner=row["owner_name"],
                stage=stage,
                stage_order=stages[stage],
                amount=parse_money(row["amount_usd"], "amount_usd", row_no, "deals"),
                created=created,
                close=parse_date(row["close_date"], "close_date", row_no, "deals", False),
                last_activity=parse_date(
                    row["last_activity_date"], "last_activity_date", row_no, "deals", False
                ),
                stage_entered=stage_entered,
                status=status,
                days_in_stage=(as_of - stage_entered).days,
            )
        )
    return deals


def load_history(path: Path, stages: dict[str, int]) -> dict[str, list[tuple[date, int, str]]]:
    rows = read_rows(path, HISTORY_COLUMNS, "stage_history")
    history: dict[str, list[tuple[date, int, str]]] = {}
    for row, row_no in rows:
        stage = row["stage"]
        if stage not in stages:
            raise AuditError(
                f"stage_history row {row_no}: column 'stage' value {stage!r}"
                " is not listed in the stages file"
            )
        entered = parse_date(row["entered_date"], "entered_date", row_no, "stage_history", True)
        deal_id = row["deal_id"]
        if not deal_id:
            raise AuditError(
                f"stage_history row {row_no}: column 'deal_id' is required but empty"
            )
        history.setdefault(deal_id, []).append((entered, stages[stage], stage))
    for events in history.values():
        events.sort(key=lambda item: (item[0], item[1], item[2]))
    return history


def normalize_account(name: str, suffixes: list[str]) -> str:
    lowered = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name.lower())
    tokens = [token for token in lowered.split() if token]
    drop = {suffix.lower().strip(".") for suffix in suffixes}
    while tokens and tokens[-1] in drop:
        tokens.pop()
    return " ".join(tokens)


def normalize_domain(domain: str) -> str:
    cleaned = domain.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.strip("/")


def amount_weight(amount: Decimal | None, config: dict) -> float:
    if amount is None or amount <= 0:
        return 1.0
    reference = Decimal(str(config["amount_reference_usd"]))
    cap = float(config["amount_weight_cap"])
    return round(1.0 + min(float(amount / reference), cap), 4)


def add_finding(deal: Deal, code: str, detail: str, config: dict) -> None:
    points = int(config["severity_points"][code])
    score = round(points * amount_weight(deal.amount, config), 1)
    deal.findings.append(Finding(code=code, detail=detail, points=points, score=score))


def stage_medians(open_deals: list[Deal], config: dict) -> tuple[dict[str, float], float | None]:
    by_stage: dict[str, list[int]] = {}
    for deal in open_deals:
        by_stage.setdefault(deal.stage, []).append(deal.days_in_stage)
    global_median = statistics.median(
        [days for values in by_stage.values() for days in values]
    ) if by_stage else None
    medians: dict[str, float] = {}
    minimum = int(config["stuck_min_sample"])
    for stage, values in by_stage.items():
        if len(values) >= minimum:
            medians[stage] = float(statistics.median(values))
        elif global_median is not None:
            medians[stage] = float(global_median)
    return medians, (float(global_median) if global_median is not None else None)


def cluster_accounts(
    deals: list[Deal], config: dict
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Group account ids that share a normalized name or a domain."""
    names: dict[str, str] = {}
    domains: dict[str, str] = {}
    for deal in deals:
        names.setdefault(deal.account_id, deal.account_name)
        domain = normalize_domain(deal.account_domain)
        if domain:
            domains.setdefault(deal.account_id, domain)

    parent: dict[str, str] = {account_id: account_id for account_id in names}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        if left_root <= right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    buckets: dict[tuple[str, str], list[str]] = {}
    for account_id in sorted(names):
        key = normalize_account(names[account_id], config["legal_suffixes"])
        if key:
            buckets.setdefault(("name", key), []).append(account_id)
        domain = domains.get(account_id, "")
        if domain:
            buckets.setdefault(("domain", domain), []).append(account_id)
    for members in buckets.values():
        for other in members[1:]:
            union(members[0], other)

    clusters: dict[str, list[str]] = {}
    for account_id in sorted(names):
        clusters.setdefault(find(account_id), []).append(account_id)

    result: list[tuple[str, list[tuple[str, str]]]] = []
    for root in sorted(clusters):
        members = clusters[root]
        if len(members) < 2:
            continue
        label = normalize_account(names[members[0]], config["legal_suffixes"]) or names[members[0]]
        result.append((label, [(account_id, names[account_id]) for account_id in members]))
    result.sort(key=lambda item: item[0])
    return result


def run_checks(
    deals: list[Deal],
    history: dict[str, list[tuple[date, int, str]]] | None,
    config: dict,
    as_of: date,
) -> dict:
    open_deals = [deal for deal in deals if deal.status == "open"]
    medians, global_median = stage_medians(open_deals, config)
    stale_days = int(config["stale_activity_days"])
    multiplier = float(config["stuck_multiplier"])
    floor = Decimal(str(config["min_amount_usd"]))

    for deal in open_deals:
        if not deal.owner:
            add_finding(deal, "missing_owner", "owner_name is blank", config)
        if deal.amount is None:
            add_finding(deal, "missing_amount", "amount_usd is blank", config)
        elif deal.amount < floor:
            add_finding(
                deal,
                "missing_amount",
                f"amount_usd {format_usd(deal.amount)} is below the {format_usd(floor)} floor",
                config,
            )
        if deal.close is None:
            add_finding(deal, "close_date_past", "close_date is blank on an open deal", config)
        elif deal.close <= as_of:
            overdue = (as_of - deal.close).days
            add_finding(
                deal,
                "close_date_past",
                f"close_date {deal.close.isoformat()} is {overdue}d in the past",
                config,
            )
        if deal.last_activity is None:
            add_finding(deal, "stale_activity", "no activity has ever been logged", config)
        else:
            idle = (as_of - deal.last_activity).days
            if idle > stale_days:
                add_finding(
                    deal,
                    "stale_activity",
                    f"last activity {deal.last_activity.isoformat()} ({idle}d ago, threshold {stale_days}d)",
                    config,
                )
        median = medians.get(deal.stage)
        if median is not None and median > 0:
            limit = median * multiplier
            if deal.days_in_stage > limit:
                add_finding(
                    deal,
                    "stuck_in_stage",
                    f"{deal.days_in_stage}d in '{deal.stage}' vs stage median {median:.0f}d"
                    f" (limit {limit:.0f}d)",
                    config,
                )

    if history is not None:
        for deal in open_deals:
            events = history.get(deal.deal_id, [])
            skipped: list[str] = []
            for previous, current in zip(events, events[1:]):
                gap = current[1] - previous[1]
                if gap > 1:
                    skipped.append(f"{previous[2]} -> {current[2]}")
            if skipped:
                add_finding(
                    deal,
                    "stage_skipped",
                    "stage history jumps " + "; ".join(skipped),
                    config,
                )

    clusters = cluster_accounts(deals, config)
    cluster_by_account: dict[str, list[tuple[str, str]]] = {}
    for _, members in clusters:
        for account_id, _name in members:
            cluster_by_account[account_id] = members
    for deal in open_deals:
        members = cluster_by_account.get(deal.account_id)
        if members:
            others = [f"{name} ({account_id})" for account_id, name in members if account_id != deal.account_id]
            add_finding(
                deal,
                "duplicate_account",
                f"account {deal.account_name} ({deal.account_id}) looks like " + "; ".join(others),
                config,
            )

    return {"open_deals": open_deals, "medians": medians, "global_median": global_median, "clusters": clusters}


def format_usd(amount: Decimal) -> str:
    quantized = amount.quantize(Decimal("1"))
    return f"${quantized:,}"


def pct(numerator: float, denominator: float) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(100.0 * numerator / denominator):.1f}%"


def build_report(
    deals: list[Deal],
    analysis: dict,
    config: dict,
    as_of: date,
    paths: dict[str, str],
    history_supplied: bool,
) -> dict:
    open_deals: list[Deal] = analysis["open_deals"]
    flagged = [deal for deal in open_deals if deal.findings]
    open_value = sum((deal.amount_or_zero for deal in open_deals), Decimal("0"))
    flagged_value = sum((deal.amount_or_zero for deal in flagged), Decimal("0"))

    by_type: dict[str, dict] = {}
    for deal in open_deals:
        for finding in deal.findings:
            bucket = by_type.setdefault(
                finding.code, {"deals": 0, "value": Decimal("0"), "score": 0.0}
            )
            bucket["deals"] += 1
            bucket["value"] += deal.amount_or_zero
            bucket["score"] = round(bucket["score"] + finding.score, 1)

    owners: dict[str, list[Deal]] = {}
    for deal in flagged:
        owners.setdefault(deal.owner_label, []).append(deal)
    owner_blocks = []
    for owner, owner_deals in owners.items():
        owner_deals.sort(key=lambda item: (-item.score, item.deal_id))
        owner_blocks.append(
            {
                "owner": owner,
                "deal_count": len(owner_deals),
                "total_score": round(sum(deal.score for deal in owner_deals), 1),
                "value_at_risk": sum((deal.amount_or_zero for deal in owner_deals), Decimal("0")),
                "deals": owner_deals,
            }
        )
    owner_blocks.sort(key=lambda block: (-block["total_score"], block["owner"]))

    return {
        "as_of": as_of,
        "paths": paths,
        "config": config,
        "history_supplied": history_supplied,
        "total_deals": len(deals),
        "open_deals": open_deals,
        "closed_deals": len(deals) - len(open_deals),
        "flagged": flagged,
        "open_value": open_value,
        "flagged_value": flagged_value,
        "by_type": by_type,
        "owner_blocks": owner_blocks,
        "medians": analysis["medians"],
        "global_median": analysis["global_median"],
        "clusters": analysis["clusters"],
    }


def render_text(report: dict) -> str:
    lines: list[str] = []
    config = report["config"]
    open_deals = report["open_deals"]
    out = lines.append

    out(f"PIPELINE HYGIENE AUDIT — as of {report['as_of'].isoformat()}")
    out(f"deals: {report['paths']['deals']}   stages: {report['paths']['stages']}")
    out(f"rules: {report['paths']['config']}")
    out(
        "scoring: finding score = severity points x amount weight; "
        f"amount weight = 1 + min(amount / {int(config['amount_reference_usd']):,}, "
        f"{float(config['amount_weight_cap']):.2f})"
    )
    out("")

    out("SUMMARY")
    out(f"  Deals in export            {report['total_deals']} ({len(open_deals)} open, {report['closed_deals']} closed)")
    out(f"  Open pipeline value        {format_usd(report['open_value'])}")
    out(
        f"  Open deals with findings   {len(report['flagged'])} of {len(open_deals)}"
        f" ({pct(len(report['flagged']), len(open_deals))})"
    )
    out(
        f"  Pipeline value flagged     {format_usd(report['flagged_value'])}"
        f" ({pct(float(report['flagged_value']), float(report['open_value']))})"
    )
    out("")

    out("FINDINGS BY TYPE")
    if not report["by_type"]:
        out("  No findings. Every open deal passed all enabled checks.")
    else:
        out(f"  {'check':<18}{'deals':>7}{'value':>16}{'score':>10}")
        for code in FINDING_ORDER:
            bucket = report["by_type"].get(code)
            if not bucket:
                continue
            out(
                f"  {code:<18}{bucket['deals']:>7}{format_usd(bucket['value']):>16}"
                f"{bucket['score']:>10.1f}"
            )
    if not report["history_supplied"]:
        out("  stage_skipped      not evaluated (no --stage-history file supplied)")
    out("")

    out("DAYS-IN-STAGE MEDIANS (open deals, the basis for stuck_in_stage)")
    if not report["medians"]:
        out("  Not computed: no open deals in the export.")
    else:
        multiplier = float(config["stuck_multiplier"])
        for stage in sorted(report["medians"], key=lambda name: (report["medians"][name], name)):
            median = report["medians"][stage]
            count = sum(1 for deal in open_deals if deal.stage == stage)
            out(
                f"  {stage:<22}median {median:>5.0f}d   limit {median * multiplier:>5.0f}d"
                f"   open deals {count}"
            )
        if report["global_median"] is not None:
            out(
                f"  Stages with fewer than {int(config['stuck_min_sample'])} open deals fall back to the"
                f" all-stage median of {report['global_median']:.0f}d."
            )
    out("")

    out("DUPLICATE ACCOUNT CLUSTERS")
    if not report["clusters"]:
        out("  None detected by normalized name or domain.")
    else:
        for label, members in report["clusters"]:
            out(f"  {label}")
            for account_id, name in members:
                out(f"    - {account_id}  {name}")
    out("")

    out("REMEDIATION BY OWNER")
    if not report["owner_blocks"]:
        out("  Nothing to remediate.")
    for block in report["owner_blocks"]:
        out("")
        out(
            f"  {block['owner']} — score {block['total_score']:.1f},"
            f" {block['deal_count']} deal(s), {format_usd(block['value_at_risk'])} affected"
        )
        for deal in block["deals"]:
            amount = format_usd(deal.amount) if deal.amount is not None else "(no amount)"
            out(
                f"    [{deal.score:>6.1f}] {deal.deal_id}  {deal.account_name}"
                f"  {amount}  stage: {deal.stage} ({deal.days_in_stage}d)"
            )
            for finding in sorted(
                deal.findings, key=lambda item: (-item.score, FINDING_ORDER.index(item.code))
            ):
                out(f"        - {finding.code} (+{finding.score:.1f}): {finding.detail}")
                out(f"          action: {ACTIONS[finding.code]}")
    return "\n".join(lines) + "\n"


def render_json(report: dict) -> str:
    payload = {
        "as_of": report["as_of"].isoformat(),
        "inputs": report["paths"],
        "summary": {
            "deals_total": report["total_deals"],
            "deals_open": len(report["open_deals"]),
            "deals_closed": report["closed_deals"],
            "deals_flagged": len(report["flagged"]),
            "open_value_usd": str(report["open_value"].quantize(Decimal("1"))),
            "flagged_value_usd": str(report["flagged_value"].quantize(Decimal("1"))),
            "stage_skipped_evaluated": report["history_supplied"],
        },
        "findings_by_type": {
            code: {
                "deals": bucket["deals"],
                "value_usd": str(bucket["value"].quantize(Decimal("1"))),
                "score": bucket["score"],
            }
            for code, bucket in sorted(report["by_type"].items())
        },
        "stage_medians_days": {
            stage: report["medians"][stage] for stage in sorted(report["medians"])
        },
        "duplicate_clusters": [
            {
                "normalized_name": label,
                "accounts": [
                    {"account_id": account_id, "account_name": name} for account_id, name in members
                ],
            }
            for label, members in report["clusters"]
        ],
        "owners": [
            {
                "owner": block["owner"],
                "total_score": block["total_score"],
                "deal_count": block["deal_count"],
                "value_at_risk_usd": str(block["value_at_risk"].quantize(Decimal("1"))),
                "deals": [
                    {
                        "deal_id": deal.deal_id,
                        "account_id": deal.account_id,
                        "account_name": deal.account_name,
                        "stage": deal.stage,
                        "days_in_stage": deal.days_in_stage,
                        "amount_usd": None if deal.amount is None else str(deal.amount),
                        "score": deal.score,
                        "findings": [
                            {
                                "code": finding.code,
                                "detail": finding.detail,
                                "severity_points": finding.points,
                                "score": finding.score,
                                "action": ACTIONS[finding.code],
                            }
                            for finding in sorted(
                                deal.findings,
                                key=lambda item: (-item.score, FINDING_ORDER.index(item.code)),
                            )
                        ],
                    }
                    for deal in block["deals"]
                ],
            }
            for block in report["owner_blocks"]
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a CRM deal export for pipeline hygiene.")
    parser.add_argument("--deals", required=True, help="CSV export of deals/opportunities")
    parser.add_argument("--stages", required=True, help="CSV of stage_name,stage_order")
    parser.add_argument("--stage-history", help="optional CSV of deal_id,stage,entered_date")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="rules/weights JSON")
    parser.add_argument("--as-of", required=True, help="reporting date, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            raise AuditError(f"--as-of value {args.as_of!r} is not a YYYY-MM-DD date") from None
        config = load_config(Path(args.config))
        stages = load_stages(Path(args.stages))
        deals = load_deals(Path(args.deals), stages, as_of)
        history = load_history(Path(args.stage_history), stages) if args.stage_history else None
        analysis = run_checks(deals, history, config, as_of)
        report = build_report(
            deals,
            analysis,
            config,
            as_of,
            {
                "deals": args.deals,
                "stages": args.stages,
                "stage_history": args.stage_history or "(not supplied)",
                "config": args.config,
            },
            history is not None,
        )
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(render_json(report) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
