#!/usr/bin/env python3
"""Score and route inbound leads with an explainable, tunable rubric.

Stdlib only, offline, deterministic. Every "today" comes from --as-of.

total = fit_weight x fit_score + intent_weight x (intent_score x recency)
Each component is 0-100 and every point is attributed to a named rule, so the
rationale printed next to a lead fully reconstructs its score.

Exit codes: 0 ok, 2 bad input (message names the file, row and column).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config" / "rubric.json"

LEAD_COLUMNS = [
    "lead_id",
    "full_name",
    "email",
    "job_title",
    "company_name",
    "employee_count",
    "annual_revenue_usd",
    "industry",
    "country",
    "lead_source",
    "created_date",
    "last_activity_date",
    "website_sessions_30d",
    "pricing_page_views_30d",
    "content_downloads_30d",
    "demo_requested",
    "trial_started",
    "webinar_attended",
]
ROUTING_COLUMNS = [
    "rule_id",
    "priority",
    "tier",
    "segment",
    "region",
    "assignee",
    "queue",
    "sla_hours",
]

TRUE_VALUES = {"yes", "y", "true", "t", "1"}
FALSE_VALUES = {"no", "n", "false", "f", "0", ""}


class TriageError(Exception):
    """Raised for any unusable input; the message names the offending field."""


@dataclass
class Contribution:
    label: str
    points: float
    detail: str = ""


@dataclass
class Rule:
    rule_id: str
    priority: int
    tier: str
    segment: str
    region: str
    assignee: str
    queue: str
    sla_hours: str
    row_no: int


@dataclass
class Lead:
    row_no: int
    lead_id: str
    full_name: str
    email: str
    job_title: str
    company_name: str
    employee_count: int | None
    annual_revenue: Decimal | None
    industry: str
    country: str
    lead_source: str
    created: date
    last_activity: date | None
    counts: dict[str, int]
    flags: dict[str, bool]
    notes: list[str] = field(default_factory=list)


def read_rows(path: Path, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    if not path.is_file():
        raise TriageError(f"{label}: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise TriageError(
                f"{label}: {path} is empty; expected a header row with: {', '.join(required)}"
            )
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise TriageError(
                f"{label}: {path} is missing required column(s): {', '.join(missing)}"
            )
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
        raise TriageError(f"{label}: {path} has a header but no data rows")
    return rows


def parse_date_value(value: str, column: str, row_no: int, label: str, required: bool) -> date | None:
    if not value:
        if required:
            raise TriageError(f"{label} row {row_no}: column '{column}' is required but empty")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise TriageError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a YYYY-MM-DD date"
        ) from None


def parse_count(value: str, column: str, row_no: int, label: str) -> int:
    if not value:
        return 0
    try:
        number = int(value.replace(",", ""))
    except ValueError:
        raise TriageError(
            f"{label} row {row_no}: column '{column}' value {value!r} is not a whole number"
        ) from None
    if number < 0:
        raise TriageError(
            f"{label} row {row_no}: column '{column}' value {value!r} is negative"
        )
    return number


def parse_flag(value: str, column: str, row_no: int, label: str) -> bool:
    lowered = value.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise TriageError(
        f"{label} row {row_no}: column '{column}' value {value!r} is not yes/no"
    )


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise TriageError(f"config: file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TriageError(f"config: {path} is not valid JSON ({exc})") from None
    for key in ("fit_weight", "intent_weight", "fit", "intent", "tiers", "segments", "regions", "disqualifiers"):
        if key not in config:
            raise TriageError(f"config: {path} is missing key {key!r}")
    total_weight = float(config["fit_weight"]) + float(config["intent_weight"])
    if abs(total_weight - 1.0) > 1e-9:
        raise TriageError(
            f"config: fit_weight + intent_weight must equal 1.0, got {total_weight:g}"
        )
    if float(config["fit"]["max_points"]) <= 0 or float(config["intent"]["max_points"]) <= 0:
        raise TriageError("config: fit.max_points and intent.max_points must be greater than zero")
    if not config["tiers"]:
        raise TriageError("config: tiers must list at least one tier")
    for signal in config["intent"]["signals"]:
        if signal["kind"] not in {"flag", "count"}:
            raise TriageError(
                f"config: intent signal {signal.get('field')!r} has unknown kind {signal.get('kind')!r}"
            )
    return config


def load_leads(path: Path, as_of: date) -> list[Lead]:
    rows = read_rows(path, LEAD_COLUMNS, "leads")
    leads: list[Lead] = []
    seen: dict[str, int] = {}
    for row, row_no in rows:
        lead_id = row["lead_id"]
        if not lead_id:
            raise TriageError(f"leads row {row_no}: column 'lead_id' is required but empty")
        if lead_id in seen:
            raise TriageError(
                f"leads row {row_no}: lead_id {lead_id!r} already appeared on row {seen[lead_id]}"
            )
        seen[lead_id] = row_no
        notes: list[str] = []

        employees_raw = row["employee_count"]
        employees: int | None = None
        if employees_raw:
            employees = parse_count(employees_raw, "employee_count", row_no, "leads")
        else:
            notes.append("employee_count is blank: firmographic size scored 0 and segment unknown")

        revenue_raw = row["annual_revenue_usd"]
        revenue: Decimal | None = None
        if revenue_raw:
            cleaned = revenue_raw.replace(",", "").replace("$", "")
            try:
                revenue = Decimal(cleaned)
            except InvalidOperation:
                raise TriageError(
                    f"leads row {row_no}: column 'annual_revenue_usd' value {revenue_raw!r} is not a number"
                ) from None
            if revenue < 0:
                raise TriageError(
                    f"leads row {row_no}: column 'annual_revenue_usd' value {revenue_raw!r} is negative"
                )
        else:
            notes.append("annual_revenue_usd is blank: revenue band scored 0")

        if not row["email"]:
            notes.append("email is blank: free-domain disqualifier could not be evaluated")

        last_activity = parse_date_value(
            row["last_activity_date"], "last_activity_date", row_no, "leads", False
        )
        if last_activity is not None and last_activity > as_of:
            raise TriageError(
                f"leads row {row_no}: column 'last_activity_date' value {row['last_activity_date']!r}"
                f" is after --as-of {as_of.isoformat()}"
            )

        leads.append(
            Lead(
                row_no=row_no,
                lead_id=lead_id,
                full_name=row["full_name"],
                email=row["email"],
                job_title=row["job_title"],
                company_name=row["company_name"],
                employee_count=employees,
                annual_revenue=revenue,
                industry=row["industry"],
                country=row["country"],
                lead_source=row["lead_source"],
                created=parse_date_value(row["created_date"], "created_date", row_no, "leads", True),
                last_activity=last_activity,
                counts={
                    name: parse_count(row[name], name, row_no, "leads")
                    for name in (
                        "website_sessions_30d",
                        "pricing_page_views_30d",
                        "content_downloads_30d",
                    )
                },
                flags={
                    name: parse_flag(row[name], name, row_no, "leads")
                    for name in ("demo_requested", "trial_started", "webinar_attended")
                },
                notes=notes,
            )
        )
    return leads


def load_routing(path: Path, config: dict) -> list[Rule]:
    rows = read_rows(path, ROUTING_COLUMNS, "routing")
    tier_names = {tier["name"] for tier in config["tiers"]} | {"*"}
    segment_names = {segment["name"] for segment in config["segments"]} | {"*", "unknown"}
    region_names = set(config["regions"].values()) | {"*"}
    rules: list[Rule] = []
    seen: dict[str, int] = {}
    for row, row_no in rows:
        rule_id = row["rule_id"]
        if not rule_id:
            raise TriageError(f"routing row {row_no}: column 'rule_id' is required but empty")
        if rule_id in seen:
            raise TriageError(
                f"routing row {row_no}: rule_id {rule_id!r} already appeared on row {seen[rule_id]}"
            )
        seen[rule_id] = row_no
        if row["tier"] not in tier_names:
            raise TriageError(
                f"routing row {row_no}: column 'tier' value {row['tier']!r} is not a configured tier or '*'"
            )
        if row["segment"] not in segment_names:
            raise TriageError(
                f"routing row {row_no}: column 'segment' value {row['segment']!r} is not a configured segment or '*'"
            )
        if row["region"] not in region_names:
            raise TriageError(
                f"routing row {row_no}: column 'region' value {row['region']!r} is not a configured region or '*'"
            )
        if not row["queue"]:
            raise TriageError(f"routing row {row_no}: column 'queue' is required but empty")
        try:
            priority = int(row["priority"])
        except ValueError:
            raise TriageError(
                f"routing row {row_no}: column 'priority' value {row['priority']!r} is not an integer"
            ) from None
        rules.append(
            Rule(
                rule_id=rule_id,
                priority=priority,
                tier=row["tier"],
                segment=row["segment"],
                region=row["region"],
                assignee=row["assignee"] or "(unassigned)",
                queue=row["queue"],
                sla_hours=row["sla_hours"],
                row_no=row_no,
            )
        )
    rules.sort(key=lambda rule: (rule.priority, rule.rule_id))
    return rules


def title_tokens(text: str) -> list[str]:
    """Lower-case alphanumeric tokens; '/' separates tokens, other punctuation is dropped."""
    parts = text.lower().replace("/", " ").split()
    return [token for token in ("".join(ch for ch in part if ch.isalnum()) for part in parts) if token]


def title_matches(title: str, keyword: str) -> bool:
    """Whole-word title matching.

    A single-word keyword must match a whole token, so 'cto' does not match
    'Director'. A multi-word keyword must match a run of consecutive whole
    tokens, so 'head of' matches 'Head of Sales' but not 'Forehead Office
    Manager'.
    """
    tokens = title_tokens(title)
    wanted = title_tokens(keyword)
    if not wanted:
        return False
    width = len(wanted)
    return any(tokens[start:start + width] == wanted for start in range(len(tokens) - width + 1))


def score_fit(lead: Lead, config: dict) -> tuple[float, list[Contribution]]:
    fit = config["fit"]
    contributions: list[Contribution] = []

    if lead.employee_count is None:
        contributions.append(Contribution("company size unknown", 0.0, "employee_count blank"))
    else:
        for band in fit["employee_bands"]:
            if lead.employee_count >= band["min"]:
                contributions.append(
                    Contribution(band["label"], float(band["points"]), f"{lead.employee_count:,} employees")
                )
                break

    if lead.annual_revenue is None:
        contributions.append(Contribution("revenue unknown", 0.0, "annual_revenue_usd blank"))
    else:
        for band in fit["revenue_bands"]:
            if lead.annual_revenue >= Decimal(str(band["min"])):
                contributions.append(
                    Contribution(
                        band["label"],
                        float(band["points"]),
                        f"${lead.annual_revenue.quantize(Decimal('1')):,}",
                    )
                )
                break

    industry_points = fit["industry_points"].get(
        lead.industry, fit["industry_points"]["_default"]
    )
    industry_label = lead.industry or "(blank)"
    default_industry_points = fit["industry_points"]["_default"]
    if lead.industry not in fit["industry_points"]:
        industry_note = "not an explicitly scored industry"
    elif industry_points > default_industry_points:
        industry_note = "listed ICP industry"
    elif industry_points == default_industry_points:
        industry_note = f"listed industry, scored at the +{default_industry_points} unlisted default"
    else:
        industry_note = f"listed low-fit industry, below the +{default_industry_points} unlisted default"
    contributions.append(Contribution(f"industry {industry_label}", float(industry_points), industry_note))

    country_points = fit["country_points"].get(lead.country, fit["country_points"]["_default"])
    contributions.append(
        Contribution(
            f"country {lead.country or '(blank)'}",
            float(country_points),
            "priority market" if lead.country in fit["country_points"] else "default market",
        )
    )

    title_contribution = Contribution(
        f"title band {fit['title_default']['label']}",
        float(fit["title_default"]["points"]),
        lead.job_title or "(blank)",
    )
    for band in fit["title_bands"]:
        if any(title_matches(lead.job_title, keyword) for keyword in band["keywords"]):
            title_contribution = Contribution(
                f"title band {band['label']}", float(band["points"]), lead.job_title
            )
            break
    contributions.append(title_contribution)

    raw = sum(item.points for item in contributions)
    max_points = float(fit["max_points"])
    capped = min(raw, max_points)
    return round(100.0 * capped / max_points, 1), contributions


def score_intent(lead: Lead, config: dict, as_of: date) -> tuple[float, float, str, list[Contribution]]:
    intent = config["intent"]
    contributions: list[Contribution] = []
    for signal in intent["signals"]:
        field_name = signal["field"]
        if signal["kind"] == "flag":
            if lead.flags.get(field_name):
                contributions.append(Contribution(signal["label"], float(signal["points"]), "yes"))
        else:
            value = lead.counts.get(field_name, 0)
            if value <= 0:
                continue
            raw = value * float(signal["points_per_unit"])
            capped = min(raw, float(signal["cap"]))
            detail = f"{value} x {signal['points_per_unit']:g}"
            if capped < raw:
                detail += f", capped at {signal['cap']:g}"
            contributions.append(Contribution(signal["label"], capped, detail))

    raw_total = sum(item.points for item in contributions)
    max_points = float(intent["max_points"])
    intent_score = round(100.0 * min(raw_total, max_points) / max_points, 1)

    if lead.last_activity is None:
        multiplier = float(intent["missing_recency"]["multiplier"])
        recency_label = intent["missing_recency"]["label"]
    else:
        idle = (as_of - lead.last_activity).days
        multiplier = float(intent["recency_bands"][-1]["multiplier"])
        recency_label = intent["recency_bands"][-1]["label"]
        for band in intent["recency_bands"]:
            if band["max_days"] is not None and idle <= int(band["max_days"]):
                multiplier = float(band["multiplier"])
                recency_label = band["label"]
                break
        recency_label = f"{recency_label} (last activity {lead.last_activity.isoformat()}, {idle}d ago)"
    effective = round(intent_score * multiplier, 1)
    return intent_score, effective, f"{recency_label} x{multiplier:g}", contributions


def disqualify(lead: Lead, config: dict) -> list[str]:
    rules = config["disqualifiers"]
    reasons: list[str] = []
    domain = lead.email.split("@")[-1].lower() if "@" in lead.email else ""
    if domain and domain in {item.lower() for item in rules["free_email_domains"]}:
        reasons.append(f"personal email domain ({domain})")
    if lead.country in rules["blocked_countries"]:
        reasons.append(f"blocked country ({lead.country})")
    if lead.industry in rules["blocked_industries"]:
        reasons.append(f"blocked industry ({lead.industry})")
    minimum = int(rules["min_employee_count"])
    if lead.employee_count is not None and lead.employee_count < minimum:
        reasons.append(f"{lead.employee_count} employees is below the {minimum} floor")
    return reasons


def segment_for(lead: Lead, config: dict) -> str:
    if lead.employee_count is None:
        return "unknown"
    for segment in config["segments"]:
        if lead.employee_count >= int(segment["min_employees"]):
            return segment["name"]
    return "unknown"


def tier_for(score: float, config: dict) -> dict:
    ordered = sorted(config["tiers"], key=lambda tier: -float(tier["min_score"]))
    for tier in ordered:
        if score >= float(tier["min_score"]):
            return tier
    return ordered[-1]


def route(tier_name: str, segment: str, region: str, rules: list[Rule]) -> Rule | None:
    for rule in rules:
        if rule.tier not in ("*", tier_name):
            continue
        if rule.segment not in ("*", segment):
            continue
        if rule.region not in ("*", region):
            continue
        return rule
    return None


def evaluate(
    leads: list[Lead], rules: list[Rule], config: dict, as_of: date, fallback_queue: str
) -> list[dict]:
    results: list[dict] = []
    for lead in leads:
        fit_score, fit_contributions = score_fit(lead, config)
        intent_score, effective_intent, recency_label, intent_contributions = score_intent(
            lead, config, as_of
        )
        total = round(
            float(config["fit_weight"]) * fit_score + float(config["intent_weight"]) * effective_intent,
            1,
        )
        reasons = disqualify(lead, config)
        segment = segment_for(lead, config)
        region = config["regions"].get(lead.country, config["regions"]["_default"])
        if reasons:
            tier_name = "DQ"
            tier_description = "disqualified by rubric"
            matched = None
            assignment = {
                "queue": config["disqualifiers"]["queue"],
                "assignee": config["disqualifiers"]["assignee"],
                "sla_hours": str(config["disqualifiers"]["sla_hours"]),
                "rule_id": "(disqualifier)",
            }
        else:
            tier = tier_for(total, config)
            tier_name = tier["name"]
            tier_description = tier["description"]
            matched = route(tier_name, segment, region, rules)
            if matched is None:
                assignment = {
                    "queue": fallback_queue,
                    "assignee": "(unassigned)",
                    "sla_hours": "",
                    "rule_id": "(no matching rule)",
                }
            else:
                assignment = {
                    "queue": matched.queue,
                    "assignee": matched.assignee,
                    "sla_hours": matched.sla_hours,
                    "rule_id": matched.rule_id,
                }
        results.append(
            {
                "lead": lead,
                "fit_score": fit_score,
                "fit_contributions": fit_contributions,
                "intent_score": intent_score,
                "effective_intent": effective_intent,
                "recency_label": recency_label,
                "intent_contributions": intent_contributions,
                "total": total,
                "tier": tier_name,
                "tier_description": tier_description,
                "segment": segment,
                "region": region,
                "disqualified_reasons": reasons,
                "assignment": assignment,
                "unrouted": reasons == [] and matched is None,
            }
        )
    return results


def contribution_text(items: list[Contribution]) -> str:
    parts = []
    for item in items:
        detail = f" [{item.detail}]" if item.detail else ""
        parts.append(f"{item.label} +{item.points:g}{detail}")
    return "; ".join(parts) if parts else "no scoring signals present"


def render_text(results: list[dict], config: dict, as_of: date, paths: dict[str, str]) -> str:
    lines: list[str] = []
    out = lines.append
    tier_order = [tier["name"] for tier in sorted(config["tiers"], key=lambda t: -float(t["min_score"]))]
    tier_order.append("DQ")

    out(f"LEAD TRIAGE AND ROUTING — as of {as_of.isoformat()}")
    out(f"leads: {paths['leads']}   routing: {paths['routing']}   rubric: {paths['config']}")
    out(
        f"scoring: total = {float(config['fit_weight']):.2f} x fit + "
        f"{float(config['intent_weight']):.2f} x (intent x recency); each component is 0-100"
    )
    out("")

    out("SUMMARY")
    out(f"  Leads scored               {len(results)}")
    for tier_name in tier_order:
        bucket = [item for item in results if item["tier"] == tier_name]
        if not bucket:
            continue
        average = sum(item["total"] for item in bucket) / len(bucket)
        label = "DQ (disqualified)" if tier_name == "DQ" else f"Tier {tier_name}"
        threshold = next(
            (f" (>= {float(t['min_score']):g})" for t in config["tiers"] if t["name"] == tier_name),
            "",
        )
        out(f"  {label + threshold:<27}{len(bucket):>3}  avg score {average:.1f}")
    unrouted = [item for item in results if item["unrouted"]]
    if unrouted:
        out(f"  Unrouted (no rule matched) {len(unrouted):>3}")
    out("")

    out("ROUTING LOAD")
    load: dict[tuple[str, str], int] = {}
    for item in results:
        key = (item["assignment"]["queue"], item["assignment"]["assignee"])
        load[key] = load.get(key, 0) + 1
    for (queue, assignee), count in sorted(load.items(), key=lambda entry: (-entry[1], entry[0])):
        out(f"  {queue:<24}{assignee:<22}{count:>3} lead(s)")
    out("")

    for tier_name in tier_order:
        bucket = [item for item in results if item["tier"] == tier_name]
        if not bucket:
            continue
        bucket.sort(key=lambda item: (-item["total"], item["lead"].lead_id))
        header = (
            "DISQUALIFIED"
            if tier_name == "DQ"
            else f"TIER {tier_name} — {bucket[0]['tier_description']}"
        )
        out(f"{header} ({len(bucket)})")
        for item in bucket:
            lead = item["lead"]
            employees = f"{lead.employee_count:,} employees" if lead.employee_count is not None else "size unknown"
            out(
                f"  [{item['total']:>5.1f}] {lead.lead_id}  {lead.full_name} — {lead.job_title or '(no title)'},"
                f" {lead.company_name}"
            )
            out(
                f"      firmographics: {employees} | {lead.industry or '(no industry)'} |"
                f" {lead.country or '(no country)'} ({item['region']}) | segment {item['segment']}"
                f" | source {lead.lead_source or '(unknown)'}"
            )
            out(f"      fit {item['fit_score']:.1f}: {contribution_text(item['fit_contributions'])}")
            out(
                f"      intent {item['intent_score']:.1f} x {item['recency_label']}"
                f" = {item['effective_intent']:.1f}: {contribution_text(item['intent_contributions'])}"
            )
            if item["disqualified_reasons"]:
                out(f"      disqualified: {'; '.join(item['disqualified_reasons'])}")
            assignment = item["assignment"]
            sla = f", SLA {assignment['sla_hours']}h" if assignment["sla_hours"] else ""
            out(
                f"      route -> {assignment['queue']} / {assignment['assignee']}"
                f" (rule {assignment['rule_id']}{sla})"
            )
            for note in lead.notes:
                out(f"      data note: {note}")
        out("")

    notes = [(item["lead"].lead_id, note) for item in results for note in item["lead"].notes]
    out("DATA QUALITY")
    if not notes:
        out("  No missing firmographic fields.")
    else:
        out(f"  {len(notes)} field(s) missing across {len({lead_id for lead_id, _ in notes})} lead(s);")
        out("  missing fields score zero rather than guessing, which drags fit scores down.")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_json(results: list[dict], config: dict, as_of: date, paths: dict[str, str]) -> str:
    payload = {
        "as_of": as_of.isoformat(),
        "inputs": paths,
        "weights": {
            "fit": float(config["fit_weight"]),
            "intent": float(config["intent_weight"]),
        },
        "leads": [
            {
                "lead_id": item["lead"].lead_id,
                "full_name": item["lead"].full_name,
                "company_name": item["lead"].company_name,
                "segment": item["segment"],
                "region": item["region"],
                "tier": item["tier"],
                "total_score": item["total"],
                "fit_score": item["fit_score"],
                "fit_contributions": [
                    {"label": c.label, "points": c.points, "detail": c.detail}
                    for c in item["fit_contributions"]
                ],
                "intent_score": item["intent_score"],
                "intent_after_recency": item["effective_intent"],
                "recency": item["recency_label"],
                "intent_contributions": [
                    {"label": c.label, "points": c.points, "detail": c.detail}
                    for c in item["intent_contributions"]
                ],
                "disqualified_reasons": item["disqualified_reasons"],
                "routing": item["assignment"],
                "data_notes": item["lead"].notes,
            }
            for item in sorted(results, key=lambda item: (-item["total"], item["lead"].lead_id))
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score and route inbound leads.")
    parser.add_argument("--leads", required=True, help="CSV export of inbound leads")
    parser.add_argument("--routing", required=True, help="CSV of routing rules")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="rubric JSON")
    parser.add_argument("--as-of", required=True, help="scoring date, YYYY-MM-DD")
    parser.add_argument(
        "--fallback-queue",
        default="unrouted",
        help="queue used when no routing rule matches (default: unrouted)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            raise TriageError(f"--as-of value {args.as_of!r} is not a YYYY-MM-DD date") from None
        config = load_config(Path(args.config))
        leads = load_leads(Path(args.leads), as_of)
        rules = load_routing(Path(args.routing), config)
        results = evaluate(leads, rules, config, as_of, args.fallback_queue)
    except TriageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    paths = {"leads": args.leads, "routing": args.routing, "config": args.config}
    sys.stdout.write(
        render_json(results, config, as_of, paths)
        if args.json
        else render_text(results, config, as_of, paths)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
