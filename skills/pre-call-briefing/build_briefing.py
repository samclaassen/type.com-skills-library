#!/usr/bin/env python3
"""Render a pre-call briefing from one normalised JSON bundle.

Stdlib only, offline, deterministic. --as-of is the only source of "now";
the wall clock is never read.

Rules (thresholds in briefing.json):
  stale_record          days since CRM last activity >= stale_days
  budget_conflict       a captured budget statement is below the CRM amount
  close_date_past       CRM close date is before the as-of date
  close_date_unclear    the prospect stated a timeline with no concrete date
  close_date_conflict   the prospect stated a date that differs from the CRM
  missing_close_date    open deal with no close date
  unanswered_question   inbound question with answered=false
  overdue_commitment    our commitment (or CRM task) due before as-of, not done
  new_stakeholder       external attendee with no prior touchpoints
  signer_first_meeting  signer/decision maker with no prior meetings
  no_crm_record / crm_empty
  no_history_found      no CRM record (or crm: none) AND no emails, Slack mentions,
                        call notes, contact touchpoints or signals in what was read
Competitor mentions are surfaced as landmines with their source quote.

Exit codes: 0 briefing or skip notice printed, 2 bad input (including a manual
run with no external attendee email and no external company domain: the agent
must ask the user for one rather than invent attendees).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "briefing.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SOURCE_KEYS = ["calendar", "crm", "email", "slack", "enrichment"]
OPTIONAL_SOURCE_KEYS = ["call_notes"]  # call recorders / note takers (Granola, Gong, Fireflies, ...)
SOURCE_STATUSES = ["ok", "unavailable", "not_connected", "not_configured", "not_requested"]
SOURCE_LABELS = {"calendar": "Calendar", "crm": "CRM", "email": "Email", "slack": "Slack", "enrichment": "Enrichment",
                 "call_notes": "Call notes"}
PROVIDER_LABELS = {"google": "Google", "outlook": "Outlook", "manual": "manual", "hubspot": "HubSpot",
                   "attio": "Attio", "salesforce": "Salesforce", "gmail": "Gmail", "slack": "Slack",
                   "web": "web search", "granola": "Granola", "gong": "Gong", "fireflies": "Fireflies",
                   "otter": "Otter", "fathom": "Fathom", "chorus": "Chorus", "zoom": "Zoom"}
SIGNAL_SOURCE_TYPES = ["email", "call_note", "call_transcript", "crm_note", "meeting", "slack", "crm_field"]
PERIODS = {"yr": 1, "mo": 12, "one_time": None}
QUALIFIERS = ["exact", "approx", "ceiling", "approx_ceiling"]
ROLES = ["signer", "decision_maker", "influencer", "champion", "user", "technical", "unknown"]
CONFIG_KEYS = [
    "stale_days", "outdated_days", "slack_lookback_days", "max_email_threads",
    "max_slack_mentions", "max_agenda_items", "budget_tolerance_pct",
    "signer_roles", "role_labels", "flag_priority",
]
FLAG_CODES = [
    "no_history_found", "no_crm_record", "crm_empty", "close_date_past", "close_date_unclear",
    "close_date_conflict", "budget_conflict", "unanswered_question",
    "overdue_commitment", "signer_first_meeting", "new_stakeholder",
    "missing_close_date", "stale_record",
]
# Legal-form suffixes dropped from the end of a company name when building a manual event key.
LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "gmbh", "co", "corp", "corporation",
                  "plc", "llp", "lp", "ag", "sa", "sas", "bv", "nv", "pty", "oy", "ab", "srl", "spa"}
LOG_TITLE = "# Pre-call briefing log"
LOG_HEADER = "| event_key | company | meeting_start | briefed_at | crm_status |"
LOG_SEPARATOR = "|---|---|---|---|---|"
WARN = "⚠️"


class InputError(Exception):
    """Bad input; message names the file and the JSON path."""


# --------------------------------------------------------------------------- parsing


class Reader:
    """Typed accessors that report errors as '<file>: <json.path>: problem'."""

    def __init__(self, label: str):
        self.label = label

    def fail(self, path: str, problem: str) -> InputError:
        return InputError(f"{self.label}: {path}: {problem}")

    def obj(self, value, path: str, required: bool = True) -> dict | None:
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        return value

    def lst(self, value, path: str) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        return value

    def text(self, parent: dict, key: str, path: str, required: bool = True) -> str | None:
        value = parent.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise self.fail(f"{path}.{key}", "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail(f"{path}.{key}", f"must be a string, got {value!r}")
        return value.strip()

    def boolean(self, parent: dict, key: str, path: str) -> bool:
        value = parent.get(key)
        if not isinstance(value, bool):
            raise self.fail(f"{path}.{key}", f"must be true or false, got {value!r}")
        return value

    def integer(self, parent: dict, key: str, path: str, required: bool = True) -> int | None:
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise self.fail(f"{path}.{key}", f"must be a non-negative integer, got {value!r}")
        return value

    def number(self, parent: dict, key: str, path: str, required: bool = True) -> float | None:
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise self.fail(f"{path}.{key}", f"must be a non-negative number, got {value!r}")
        return float(value)

    def enum(self, parent: dict, key: str, path: str, allowed: list[str], default: str | None = None) -> str:
        value = parent.get(key, default)
        if value not in allowed:
            raise self.fail(f"{path}.{key}", f"must be one of {', '.join(allowed)}, got {value!r}")
        return value

    def day(self, parent: dict, key: str, path: str, required: bool = True) -> date | None:
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        try:
            return date.fromisoformat(value[:10]) if len(value) > 10 else date.fromisoformat(value)
        except ValueError:
            raise self.fail(f"{path}.{key}", f"{value!r} is not a YYYY-MM-DD date") from None

    def moment(self, parent: dict, key: str, path: str, required: bool = True) -> datetime | None:
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        return parse_moment(value, lambda problem: self.fail(f"{path}.{key}", problem))


def parse_moment(value: str, fail) -> datetime:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise fail(f"{value!r} is not an ISO-8601 datetime") from None
    if moment.tzinfo is None:
        raise fail(f"{value!r} has no UTC offset; use e.g. 2026-10-06T09:35:00-07:00")
    return moment


def load_json(path: Path, label: str):
    if not path.is_file():
        raise InputError(f"{label}: file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: {path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})") from None


def load_config(path: Path) -> dict:
    config = load_json(path, "config")
    r = Reader(f"config {path}")
    r.obj(config, "$")
    missing = [key for key in CONFIG_KEYS if key not in config]
    if missing:
        raise InputError(f"config {path}: missing key(s): {', '.join(missing)}")
    for key in ["stale_days", "outdated_days", "slack_lookback_days", "max_email_threads",
                "max_slack_mentions", "max_agenda_items"]:
        r.integer(config, key, "$")
    r.number(config, "budget_tolerance_pct", "$")
    unknown = [code for code in config["flag_priority"] if code not in FLAG_CODES]
    absent = [code for code in FLAG_CODES if code not in config["flag_priority"]]
    if unknown or absent:
        raise InputError(
            f"config {path}: $.flag_priority must list each flag code once"
            f" (unknown: {', '.join(unknown) or 'none'}; missing: {', '.join(absent) or 'none'})"
        )
    for role in ROLES:
        if role not in config["role_labels"]:
            raise InputError(f"config {path}: $.role_labels.{role} is missing")
    return config


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def in_domains(domain: str, domains: list[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def company_slug(name: str) -> str:
    """Lowercase company name, accents folded, legal suffixes (inc, llc, ltd, gmbh, co, ...) dropped
    from the end, every run of non-alphanumerics turned into one '-'.
    'Northpeak Outfitters' and 'Northpeak Outfitters, Inc.' both give 'northpeak-outfitters'."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", folded) if t]
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return "-".join(tokens) or "company"


def clean_domain(domain: str) -> str:
    d = domain.strip().lower().lstrip("@").strip().rstrip(".")
    return d[4:] if d.startswith("www.") else d


def load_bundle(path: Path) -> dict:
    raw = load_json(path, "bundle")
    r = Reader(f"bundle {path}")
    r.obj(raw, "$")
    if raw.get("schema_version") != 1:
        raise r.fail("$.schema_version", f"must be 1, got {raw.get('schema_version')!r}")

    company = r.obj(raw.get("company"), "$.company")
    b: dict = {
        "company_name": r.text(company, "name", "$.company"),
        "company_domain": clean_domain(r.text(company, "domain", "$.company", required=False) or ""),
    }

    sources_raw = r.obj(raw.get("sources"), "$.sources")
    sources = {}
    for key in SOURCE_KEYS + OPTIONAL_SOURCE_KEYS:
        if key in OPTIONAL_SOURCE_KEYS and sources_raw.get(key) is None:
            continue
        src = r.obj(sources_raw.get(key), f"$.sources.{key}")
        sources[key] = {
            "provider": r.text(src, "provider", f"$.sources.{key}", required=False),
            "status": r.enum(src, "status", f"$.sources.{key}", SOURCE_STATUSES),
            "detail": r.text(src, "detail", f"$.sources.{key}", required=False),
        }
    b["sources"] = sources

    m = r.obj(raw.get("meeting"), "$.meeting")
    calendar_source = r.enum(m, "calendar_source", "$.meeting", ["google", "outlook", "manual"])
    manual = calendar_source == "manual"
    meeting = {
        # Manual runs: event_id and title are optional; the event id is derived below so two runs agree.
        "event_id": r.text(m, "event_id", "$.meeting", required=not manual),
        "calendar_source": calendar_source,
        "title": r.text(m, "title", "$.meeting", required=not manual) or f"Call with {b['company_name']}",
        "start": r.moment(m, "start", "$.meeting"),
        "start_raw": r.text(m, "start", "$.meeting"),
        "end": r.moment(m, "end", "$.meeting", required=False),
        "timezone_label": r.text(m, "timezone_label", "$.meeting", required=False),
        "status": r.enum(m, "status", "$.meeting", ["confirmed", "tentative", "cancelled"], "confirmed"),
        "attendees": [],
    }
    if "attendees" not in m:
        raise r.fail("$.meeting.attendees", "is required (use [] for a manual run that only knows the company domain)")
    attendees = r.lst(m.get("attendees"), "$.meeting.attendees")
    if not attendees and meeting["calendar_source"] != "manual":
        raise r.fail("$.meeting.attendees", "must list at least one attendee for a calendar event")
    external_domains = []
    for i, d in enumerate(r.lst(m.get("external_domains"), "$.meeting.external_domains")):
        if not isinstance(d, str) or not d.strip().lstrip("@").strip():
            raise r.fail(f"$.meeting.external_domains[{i}]", f"must be a domain such as outfitters.example, got {d!r}")
        d = clean_domain(d)
        if "@" in d or "." not in d:
            raise r.fail(f"$.meeting.external_domains[{i}]", f"{d!r} is not a domain (no '@', e.g. outfitters.example)")
        external_domains.append(d)
    meeting["external_domains"] = external_domains
    for i, a in enumerate(attendees):
        p = f"$.meeting.attendees[{i}]"
        r.obj(a, p)
        email = r.text(a, "email", p).lower()
        if "@" not in email:
            raise r.fail(f"{p}.email", f"{email!r} is not an email address")
        meeting["attendees"].append({
            "email": email,
            "name": r.text(a, "name", p, required=False) or email,
            "response": r.enum(a, "response", p, ["accepted", "tentative", "declined", "needs_action"], "needs_action"),
        })
    if manual:
        # Derived, never taken from the bundle: domain-based when a company domain is known (most stable),
        # otherwise the normalised company-name slug; plus the call's local date as written.
        basis = b["company_domain"] or (sorted(external_domains)[0] if external_domains else "") \
            or company_slug(b["company_name"])
        meeting["event_id"] = f"{basis}-{meeting['start_raw'][:10]}"
    b["meeting"] = meeting

    crm = r.obj(raw.get("crm"), "$.crm", required=False) or {}
    crm_out = {"record_found": False, "deal": None, "contacts": {}, "open_tasks": [], "notes_count": 0,
               "record_ref": None}
    if crm:
        crm_out["record_found"] = r.boolean(crm, "record_found", "$.crm")
        crm_out["record_ref"] = r.text(crm, "record_ref", "$.crm", required=False)
        crm_out["notes_count"] = r.integer(crm, "notes_count", "$.crm", required=False) or 0
        deal = r.obj(crm.get("deal"), "$.crm.deal", required=False)
        if deal is not None:
            amount = r.number(deal, "amount", "$.crm.deal", required=False)
            crm_out["deal"] = {
                "name": r.text(deal, "name", "$.crm.deal", required=False),
                "stage": r.text(deal, "stage", "$.crm.deal", required=False),
                "amount": amount,
                "amount_period": r.enum(deal, "amount_period", "$.crm.deal", list(PERIODS), "yr"),
                "close_date": r.day(deal, "close_date", "$.crm.deal", required=False),
                "last_activity": r.day(deal, "last_activity_at", "$.crm.deal", required=False),
                "owner": r.text(deal, "owner", "$.crm.deal", required=False),
            }
        for i, c in enumerate(r.lst(crm.get("contacts"), "$.crm.contacts")):
            p = f"$.crm.contacts[{i}]"
            r.obj(c, p)
            lt = r.obj(c.get("last_touchpoint"), f"{p}.last_touchpoint", required=False)
            email = r.text(c, "email", p).lower()
            crm_out["contacts"][email] = {
                "name": r.text(c, "name", p, required=False),
                "title": r.text(c, "title", p, required=False),
                "role": r.enum(c, "role", p, ROLES, "unknown"),
                "prior_meetings": r.integer(c, "prior_meetings", p),
                "prior_emails": r.integer(c, "prior_emails", p),
                "last_touchpoint": None if lt is None else {
                    "date": r.day(lt, "date", f"{p}.last_touchpoint"),
                    "summary": r.text(lt, "summary", f"{p}.last_touchpoint"),
                },
            }
        for i, t in enumerate(r.lst(crm.get("open_tasks"), "$.crm.open_tasks")):
            p = f"$.crm.open_tasks[{i}]"
            r.obj(t, p)
            crm_out["open_tasks"].append({
                "id": r.text(t, "id", p),
                "subject": r.text(t, "subject", p),
                "owner": r.text(t, "owner", p, required=False),
                "due": r.day(t, "due", p, required=False),
                "done": r.boolean(t, "done", p),
            })
    b["crm"] = crm_out

    email = r.obj(raw.get("email"), "$.email", required=False) or {}
    threads = []
    for i, t in enumerate(r.lst(email.get("threads"), "$.email.threads")):
        p = f"$.email.threads[{i}]"
        r.obj(t, p)
        threads.append({
            "thread_id": r.text(t, "thread_id", p),
            "subject": r.text(t, "subject", p, required=False),
            "last_message_at": r.moment(t, "last_message_at", p),
            "last_message_from": r.text(t, "last_message_from", p).lower(),
        })
    b["threads"] = threads

    slack = r.obj(raw.get("slack"), "$.slack", required=False) or {}
    mentions = []
    for i, s in enumerate(r.lst(slack.get("mentions"), "$.slack.mentions")):
        p = f"$.slack.mentions[{i}]"
        r.obj(s, p)
        mentions.append({
            "channel": r.text(s, "channel", p),
            "author": r.text(s, "author", p),
            "posted_at": r.moment(s, "posted_at", p),
            "text": r.text(s, "text", p),
        })
    b["slack"] = mentions

    cn = r.obj(raw.get("call_notes"), "$.call_notes", required=False) or {}
    calls = []
    for i, c in enumerate(r.lst(cn.get("calls"), "$.call_notes.calls")):
        p = f"$.call_notes.calls[{i}]"
        r.obj(c, p)
        calls.append({"date": r.day(c, "date", p), "title": r.text(c, "title", p, required=False)})
    b["past_calls"] = calls

    sig = r.obj(raw.get("signals"), "$.signals", required=False) or {}

    def source(obj: dict, p: str) -> dict:
        s = r.obj(obj.get("source"), f"{p}.source")
        return {
            "type": r.enum(s, "type", f"{p}.source", SIGNAL_SOURCE_TYPES),
            "date": r.day(s, "date", f"{p}.source"),
            "ref": r.text(s, "ref", f"{p}.source", required=False),
        }

    budgets = []
    for i, s in enumerate(r.lst(sig.get("budget_statements"), "$.signals.budget_statements")):
        p = f"$.signals.budget_statements[{i}]"
        r.obj(s, p)
        budgets.append({
            "amount": r.number(s, "amount", p),
            "period": r.enum(s, "period", p, list(PERIODS)),
            "qualifier": r.enum(s, "qualifier", p, QUALIFIERS),
            "quote": r.text(s, "quote", p),
            "speaker": r.text(s, "speaker", p, required=False),
            "source": source(s, p),
        })
    timelines = []
    for i, s in enumerate(r.lst(sig.get("timeline_statements"), "$.signals.timeline_statements")):
        p = f"$.signals.timeline_statements[{i}]"
        r.obj(s, p)
        timelines.append({
            "label": r.text(s, "label", p),
            "quote": r.text(s, "quote", p),
            "stated_date": r.day(s, "stated_date", p, required=False),
            "speaker": r.text(s, "speaker", p, required=False),
            "source": source(s, p),
        })
    competitors = []
    for i, s in enumerate(r.lst(sig.get("competitor_mentions"), "$.signals.competitor_mentions")):
        p = f"$.signals.competitor_mentions[{i}]"
        r.obj(s, p)
        competitors.append({
            "competitor": r.text(s, "competitor", p),
            "context": r.text(s, "context", p, required=False),
            "quote": r.text(s, "quote", p),
            "speaker": r.text(s, "speaker", p, required=False),
            "source": source(s, p),
        })
    questions = []
    for i, s in enumerate(r.lst(sig.get("questions"), "$.signals.questions")):
        p = f"$.signals.questions[{i}]"
        r.obj(s, p)
        questions.append({
            "asked_by": r.text(s, "asked_by", p),
            "topic": r.text(s, "topic", p),
            "asked_at": r.day(s, "asked_at", p),
            "channel": r.enum(s, "channel", p, ["email", "call", "slack", "meeting"]),
            "answered": r.boolean(s, "answered", p),
        })
    commitments = []
    for i, s in enumerate(r.lst(sig.get("commitments"), "$.signals.commitments")):
        p = f"$.signals.commitments[{i}]"
        r.obj(s, p)
        commitments.append({
            "side": r.enum(s, "side", p, ["ours", "theirs"]),
            "owner": r.text(s, "owner", p),
            "what": r.text(s, "what", p),
            "due": r.day(s, "due", p, required=False),
            "done": r.boolean(s, "done", p),
            "crm_task_id": r.text(s, "crm_task_id", p, required=False),
            "source": source(s, p),
        })
    b["signals"] = {"budgets": budgets, "timelines": timelines, "competitors": competitors,
                    "questions": questions, "commitments": commitments}

    enr = r.obj(raw.get("enrichment"), "$.enrichment", required=False) or {}
    items = []
    for i, s in enumerate(r.lst(enr.get("items"), "$.enrichment.items")):
        p = f"$.enrichment.items[{i}]"
        r.obj(s, p)
        items.append({
            "kind": r.enum(s, "kind", p, ["news", "funding", "hiring", "tech_stack", "leadership", "other"]),
            "summary": r.text(s, "summary", p),
            "source_url": r.text(s, "source_url", p),
            "date": r.day(s, "date", p),
        })
    b["enrichment"] = items

    if meeting["end"] is not None and meeting["end"] < meeting["start"]:
        raise r.fail("$.meeting.end", "is before $.meeting.start")
    return b


# --------------------------------------------------------------------------- helpers


def short_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}"


def money(amount: float) -> str:
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def period_suffix(period: str) -> str:
    return {"yr": "/yr", "mo": "/mo", "one_time": " one-time"}[period]


def annualised(amount: float, period: str) -> float | None:
    factor = PERIODS[period]
    return None if factor is None else amount * factor


def first_name(name: str) -> str:
    return name.split()[0] if name else name


def meeting_time(meeting: dict) -> str:
    start = meeting["start"]
    label = meeting["timezone_label"]
    if not label:
        offset = start.utcoffset() or timedelta(0)
        minutes = int(offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        minutes = abs(minutes)
        label = f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"
    return f"{WEEKDAYS[start.weekday()]} {short_date(start.date())}, {start.hour:02d}:{start.minute:02d} {label}"


def source_phrase(src: dict) -> str:
    kind = {"email": "email", "call_note": "call note", "call_transcript": "call transcript",
            "crm_note": "CRM note", "meeting": "meeting",
            "slack": "Slack", "crm_field": "CRM field"}[src["type"]]
    return f"{kind} {short_date(src['date'])}"


def event_key(meeting: dict) -> str:
    """<calendar_source>:<event_id>@<start>. For manual runs the event id was derived in
    load_bundle (company domain or company-name slug + date), so it never carries a prefix.
    A calendar id that already carries the '<calendar_source>:' prefix is not prefixed twice."""
    prefix = f"{meeting['calendar_source']}:"
    event_id = meeting["event_id"]
    if event_id.startswith(prefix):
        event_id = event_id[len(prefix):]
    return f"{prefix}{event_id}@{meeting['start_raw']}"


# --------------------------------------------------------------------------- briefing log


def read_log(path: Path) -> dict[str, str]:
    """Return {event_key: briefed_at}. Missing file -> empty. Unreadable -> InputError."""
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"log {path}: cannot be read ({exc}); refusing to run blind") from None
    if LOG_HEADER not in [line.strip() for line in lines]:
        raise InputError(f"log {path}: header row '{LOG_HEADER}' not found; not a briefing log")
    seen: dict[str, str] = {}
    in_table = False
    for line_no, line in enumerate(lines, start=1):
        s = line.strip()
        if s == LOG_HEADER:
            in_table = True
            continue
        if not in_table or not s or s == LOG_SEPARATOR:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) != 5:
            raise InputError(f"log {path}: line {line_no}: expected 5 columns, found {len(cells)}")
        seen[cells[0]] = cells[3]
    return seen


def append_log(path: Path, row: list[str]) -> None:
    lines = []
    if not path.exists():
        lines += [LOG_TITLE, "", LOG_HEADER, LOG_SEPARATOR]
    lines.append("| " + " | ".join(cell.replace("|", "/") for cell in row) + " |")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- analysis


def analyse(b: dict, config: dict, as_of: datetime, internal: list[str], enrichment_mode: str) -> dict:
    today = as_of.date()
    meeting = b["meeting"]
    crm = b["crm"]
    deal = crm["deal"]
    sig = b["signals"]
    crm_status = b["sources"]["crm"]["status"]
    crm_provider = b["sources"]["crm"]["provider"]
    flags: list[dict] = []

    def flag(code: str, **data) -> None:
        flags.append({"code": code, **data})

    # ---- attendees / stakeholders
    ours, theirs = [], []
    for a in meeting["attendees"]:
        (ours if in_domains(domain_of(a["email"]), internal) else theirs).append(a)
    on_call_signers = []
    people = []
    for a in theirs:
        c = crm["contacts"].get(a["email"])
        person = {"email": a["email"], "name": (c or {}).get("name") or a["name"], "response": a["response"],
                  "in_crm": c is not None}
        if c:
            person.update({k: c[k] for k in ["title", "role", "prior_meetings", "prior_emails", "last_touchpoint"]})
        else:
            person.update({"title": None, "role": "unknown", "prior_meetings": 0, "prior_emails": 0,
                           "last_touchpoint": None})
        person["is_new"] = person["prior_meetings"] == 0 and person["prior_emails"] == 0
        person["is_signer"] = person["role"] in config["signer_roles"]
        person["signer_first"] = person["is_signer"] and person["prior_meetings"] == 0
        dom = domain_of(a["email"])
        person["other_org"] = dom if b["company_domain"] and not in_domains(dom, [b["company_domain"]]) else None
        if person["is_signer"] and a["response"] != "declined":
            on_call_signers.append(person)
        if person["response"] != "declined":
            if person["signer_first"]:
                flag("signer_first_meeting", person=person)
            elif person["is_new"]:
                flag("new_stakeholder", person=person)
        people.append(person)

    # ---- CRM record state
    crm_state = "ok"
    if crm_status == "not_configured" or crm_provider == "none":
        crm_state = "not_configured"  # crm: none — nothing to read, not a failure
    elif crm_status != "ok" or crm_provider is None:
        crm_state = "unavailable"
    elif not crm["record_found"] or deal is None:
        crm_state = "missing"
        flag("no_crm_record")
    elif deal["stage"] is None and deal["amount"] is None and deal["close_date"] is None:
        crm_state = "empty"
        flag("crm_empty")

    days_idle = None
    latest_email = max((t["last_message_at"] for t in b["threads"]), default=None)
    if crm_state == "ok":
        if deal["last_activity"] is not None:
            days_idle = (today - deal["last_activity"]).days
            if days_idle >= config["stale_days"]:
                flag("stale_record", days=days_idle, outdated=days_idle >= config["outdated_days"])
        else:
            flag("stale_record", days=None, outdated=True)

        cd = deal["close_date"]
        if cd is None:
            flag("missing_close_date")
        elif cd < today:
            flag("close_date_past", close_date=cd, days=(today - cd).days)
        if sig["timelines"]:
            t = max(sig["timelines"], key=lambda x: x["source"]["date"])
            if t["stated_date"] is None:
                flag("close_date_unclear", statement=t)
            elif cd is not None and t["stated_date"] != cd:
                flag("close_date_conflict", statement=t, close_date=cd)

        if deal["amount"] is not None and sig["budgets"]:
            bs = max(sig["budgets"], key=lambda x: x["source"]["date"])
            amt = annualised(deal["amount"], deal["amount_period"])
            bud = annualised(bs["amount"], bs["period"])
            if amt is not None and bud is not None:
                if amt > bud * (1 + config["budget_tolerance_pct"] / 100):
                    flag("budget_conflict", statement=bs, signers=on_call_signers)

    # ---- open threads
    for q in sig["questions"]:
        if not q["answered"]:
            flag("unanswered_question", question=q)
    linked_tasks = {c["crm_task_id"] for c in sig["commitments"] if c["crm_task_id"]}
    commitments = list(sig["commitments"])
    for t in crm["open_tasks"]:
        if t["id"] in linked_tasks:
            continue
        commitments.append({"side": "ours", "owner": t["owner"] or "Unassigned", "what": t["subject"],
                            "due": t["due"], "done": t["done"], "crm_task_id": t["id"], "source": None,
                            "from_task": True})
    for c in commitments:
        c.setdefault("from_task", False)
        c["overdue_days"] = (today - c["due"]).days if c["due"] is not None and c["due"] < today and not c["done"] else None
        if c["side"] == "ours" and c["overdue_days"] is not None:
            flag("overdue_commitment", commitment=c)

    window_start = as_of - timedelta(days=config["slack_lookback_days"])
    slack = [s for s in b["slack"] if window_start <= s["posted_at"] <= as_of]
    slack.sort(key=lambda s: s["posted_at"], reverse=True)
    threads = sorted(b["threads"], key=lambda t: t["last_message_at"], reverse=True)

    # ---- no history anywhere: no CRM record (or no CRM) and nothing in the comms sources read
    src = b["sources"]
    call_dates = {x["date"] for x in b["past_calls"]}
    call_dates |= {s["source"]["date"] for key in ("budgets", "timelines", "competitors", "commitments")
                   for s in sig[key] if s["source"]["type"] in ("call_note", "call_transcript")}
    any_signal = any(sig[key] for key in sig)
    touchpoints = any(p["prior_meetings"] or p["prior_emails"] for p in people) or \
        any(c["prior_meetings"] or c["prior_emails"] for c in crm["contacts"].values())
    if crm_state in ("missing", "not_configured") and not threads and not slack and not call_dates \
            and not any_signal and not touchpoints:
        read = [k for k in ("email", "slack", "call_notes") if k in src and src[k]["status"] == "ok"]
        unread = [k for k in ("email", "slack", "call_notes") if k in src and src[k]["status"] != "ok"]
        flag("no_history_found", read=read, unread=unread)

    priority = {code: i for i, code in enumerate(config["flag_priority"])}
    flags.sort(key=lambda f: priority[f["code"]])  # stable: keeps input order within a code
    return {
        "today": today, "ours": ours, "people": people, "crm_state": crm_state, "deal": deal,
        "days_idle": days_idle, "latest_email": latest_email, "flags": flags,
        "commitments": commitments, "slack": slack[: config["max_slack_mentions"]],
        "slack_total": len(slack), "threads_used": threads[: config["max_email_threads"]],
        "threads_total": len(threads), "on_call_signers": on_call_signers,
        "past_calls_total": len(call_dates),
        "enrichment_mode": enrichment_mode,
        "domain_only": [] if theirs else [d for d in meeting["external_domains"] if not in_domains(d, internal)],
    }


# --------------------------------------------------------------------------- rendering


def budget_text(bs: dict) -> str:
    approx = "~" if bs["qualifier"] in ("approx", "approx_ceiling") else ""
    ceiling = " ceiling" if bs["qualifier"] in ("ceiling", "approx_ceiling") else ""
    return f"{approx}{money(bs['amount'])}{period_suffix(bs['period'])}{ceiling}"


def deal_amount(deal: dict) -> str:
    return money(deal["amount"]) + period_suffix(deal["amount_period"])


def agenda_and_goals(a: dict, config: dict) -> tuple[list[str], list[str]]:
    agenda, goals = [], []
    merged_signers: set[str] = set()
    for f in a["flags"]:
        code = f["code"]
        if code == "no_history_found":
            agenda.append("Open by confirming who's joining, their roles and why they took the call")
            goals.append("treat this as a cold first call")
        elif code == "no_crm_record":
            agenda.append("Qualify from scratch: need, budget, timeline and who signs")
            goals.append("qualify from scratch (no CRM record)")
        elif code == "crm_empty":
            agenda.append("Fill the CRM gaps live: stage, amount and close date")
            goals.append("establish stage, amount and close date")
        elif code == "close_date_past":
            agenda.append(f"Reset the close date: CRM still says {short_date(f['close_date'])}, which has passed")
            goals.append("reset the close date")
        elif code == "close_date_unclear":
            agenda.append(f"Confirm {f['statement']['label']} timeline → pin the real deadline")
            goals.append("pin the real deadline")
        elif code == "close_date_conflict":
            agenda.append(f"Reconcile the close date: CRM {short_date(f['close_date'])} vs prospect's "
                          f"{short_date(f['statement']['stated_date'])}")
            goals.append("reconcile the close date")
        elif code == "budget_conflict":
            gap = f"{deal_amount(a['deal'])} vs {budget_text(f['statement'])}"
            if f["signers"]:
                s = f["signers"][0]
                merged_signers.add(s["email"])
                agenda.append(f"Walk {first_name(s['name'])} through the payback case "
                              f"(address the {budget_text(f['statement'])})")
                extra = ", signer, first meeting" if s["signer_first"] else ", signer"
                goals.append(f"close the budget gap ({gap}) with {s['name']} ({extra[2:]})")
            else:
                agenda.append(f"Address the budget gap ({gap}) and ask who can approve the difference")
                goals.append(f"close the budget gap ({gap})")
        elif code == "unanswered_question":
            q = f["question"]
            agenda.append(f"Answer the {q['topic']} question ({first_name(q['asked_by'])}, {short_date(q['asked_at'])})")
            goals.append(f"answer the open {q['topic']} question")
        elif code == "overdue_commitment":
            c = f["commitment"]
            agenda.append(f"Deliver {c['what']} (due {short_date(c['due'])}) and confirm it was received")
            goals.append(f"recover the overdue {c['what']}")
        elif code == "signer_first_meeting":
            p = f["person"]
            if p["email"] in merged_signers:
                continue
            title = f", {p['title']}" if p["title"] else ""
            agenda.append(f"Get {first_name(p['name'])}'s decision criteria and sign-off process (first meeting{title})")
            goals.append(f"earn {p['name']}'s trust as signer in a first meeting")
        elif code == "new_stakeholder":
            p = f["person"]
            agenda.append(f"Intro for {first_name(p['name'])}: their role in the decision and what they need to see")
            goals.append(f"bring {p['name']} up to speed")
        elif code == "missing_close_date":
            agenda.append("Ask for a target decision or go-live date")
            goals.append("get a decision date")
        elif code == "stale_record":
            days = f"{f['days']} days" if f["days"] is not None else "the record's history"
            agenda.append(f"Re-establish the next step (no logged activity in {days})")
            goals.append("restart a deal with no recent logged activity")
    if not agenda:
        agenda.append("Confirm the next step, its owner and its date")
    return agenda[: config["max_agenda_items"]], goals


def sources_read(b: dict, a: dict, config: dict, purpose: str) -> list[str]:
    """Human list of the sources actually read (status ok), for the empty-state lines."""
    src, out = b["sources"], []
    label = lambda k: PROVIDER_LABELS.get(src[k]["provider"] or "", src[k]["provider"])  # noqa: E731
    if src["crm"]["status"] == "ok" and a["crm_state"] not in ("not_configured", "unavailable"):
        if purpose == "threads":
            if b["crm"]["record_found"]:
                out.append("open CRM tasks")
        else:
            out.append(f"CRM ({label('crm')})" if label("crm") else "CRM")
    if src["email"]["status"] == "ok":
        n = a["threads_total"]
        out.append(f"email ({len(a['threads_used'])} thread{'s' if len(a['threads_used']) != 1 else ''}"
                   f"{' of ' + str(n) if n > len(a['threads_used']) else ''})")
    if src["slack"]["status"] == "ok":
        out.append(f"Slack (last {config['slack_lookback_days']} days)")
    if "call_notes" in src and src["call_notes"]["status"] == "ok":
        out.append(f"call notes ({label('call_notes')})" if label("call_notes") else "call notes")
    return out


def history_sentence(b: dict, a: dict, f: dict) -> str:
    src = b["sources"]
    parts = ["no CRM configured" if a["crm_state"] == "not_configured" else "no CRM record"]
    if "email" in f["read"]:
        parts.append("0 emails")
    if "slack" in f["read"]:
        parts.append("0 Slack mentions")
    if "call_notes" in f["read"]:
        parts.append("0 call notes")
    if f["unread"]:
        not_read = ", ".join(f"{SOURCE_LABELS[k]} {src[k]['status'].replace('_', ' ')}" for k in f["unread"])
        head = f"No history found in the sources read ({', '.join(parts)}; not read: {not_read})"
    else:
        head = f"No history found anywhere ({', '.join(parts)})"
    return f"{head} — treat as a cold first call; confirm who's joining and why."


def render(b: dict, a: dict, config: dict, as_of: datetime, key: str) -> str:
    meeting, deal, flags = b["meeting"], a["deal"], a["flags"]
    codes = {f["code"] for f in flags}
    by_code: dict[str, list[dict]] = {}
    for f in flags:
        by_code.setdefault(f["code"], []).append(f)
    crm_name = PROVIDER_LABELS.get(b["sources"]["crm"]["provider"] or "", b["sources"]["crm"]["provider"]) or "none"
    out: list[str] = []
    out.append(f"#### Briefing: {b['company_name']} — {meeting['title']} at {meeting_time(meeting)}")
    if meeting["calendar_source"] == "manual":
        out.append("Not on your calendar — time supplied by you.")
    out.append("")

    agenda, goals = agenda_and_goals(a, config)
    if a["crm_state"] == "ok":
        parts = [deal["stage"] or "Stage not set",
                 deal_amount(deal) if deal["amount"] is not None else "no amount",
                 f"close {short_date(deal['close_date'])}" if deal["close_date"] else "no close date"]
        lead = " · ".join(parts) + "."
        if any(f.get("outdated") for f in by_code.get("stale_record", [])):
            lead += " The CRM record looks outdated, so treat these figures as unconfirmed."
    elif a["crm_state"] == "missing":
        lead = f"No CRM record found for {b['company_name'].rstrip('.')}."
    elif a["crm_state"] == "empty":
        lead = f"The CRM record for {b['company_name']} is empty."
    elif a["crm_state"] == "not_configured":
        lead = "Deal data not available (CRM not configured)."
    else:
        why = {"not_connected": "CRM not connected"}.get(b["sources"]["crm"]["status"], "CRM unreachable")
        lead = f"Deal data not available ({why})."
    if "no_history_found" in codes:
        tldr = history_sentence(b, a, by_code["no_history_found"][0])
    elif goals:
        need = goals[0] if len(goals) == 1 else f"{goals[0]} and {goals[1]}"
        tldr = f"{lead} This call needs to {need}."
    elif a["crm_state"] != "ok":
        tldr = f"{lead} No flags could be raised from the data that was read — this call needs to confirm " \
               "the next step, owner and date."
    else:
        tldr = f"{lead} No open flags — this call needs to confirm the next step, owner and date."
    out.append(f"**TL;DR** — {tldr}")
    out.append("")

    # ---- deal
    out.append("**The deal**")
    if a["crm_state"] == "not_configured":
        out.append("Not available — no CRM is configured for this skill (crm: none). "
                   "No deal data was read; nothing below comes from a CRM.")
    elif a["crm_state"] == "unavailable":
        detail = b["sources"]["crm"]["detail"]
        out.append(f"Not available — CRM: {crm_name} ({b['sources']['crm']['status']}"
                   f"{'; ' + detail if detail else ''}). No deal data was read; nothing below comes from a CRM.")
    elif a["crm_state"] == "missing":
        out.append(f"No CRM record found in {crm_name} for "
                   f"{b['company_name'] if b['company_domain'] else b['company_name'].rstrip('.')}"
                   f"{' (' + b['company_domain'] + ')' if b['company_domain'] else ''}. "
                   "This meeting is not in the pipeline — log it after the call.")
    elif a["crm_state"] == "empty":
        out.append(f"The {crm_name} record exists but is empty: no stage, amount or close date. "
                   "Treat everything about this deal as unknown.")
    else:
        rows = [("Stage", deal["stage"] or "not set", "" if deal["stage"] else f"{WARN} Not set")]
        amt_flag = ""
        if deal["amount"] is None:
            amt_value = "not set"
            amt_flag = f"{WARN} No amount"
        else:
            amt_value = deal_amount(deal)
            if "budget_conflict" in codes:
                bs = by_code["budget_conflict"][0]["statement"]
                who = first_name(bs["speaker"]) if bs["speaker"] else "Prospect"
                amt_flag = f"{WARN} {who} said {budget_text(bs)} ({source_phrase(bs['source'])})"
        rows.append(("Amount", amt_value, amt_flag))
        cd = deal["close_date"]
        cd_flag = ""
        if "close_date_past" in codes:
            cd_flag = f"{WARN} Passed {by_code['close_date_past'][0]['days']} days ago"
        if "close_date_unclear" in codes:
            st = by_code["close_date_unclear"][0]["statement"]
            cd_flag = (cd_flag + "; " if cd_flag else f"{WARN} ") + f"“{st['quote']}” — date unclear"
        if "close_date_conflict" in codes:
            st = by_code["close_date_conflict"][0]["statement"]
            cd_flag = (cd_flag + "; " if cd_flag else f"{WARN} ") + \
                f"Prospect said {short_date(st['stated_date'])}: “{st['quote']}”"
        if cd is None:
            cd_flag = f"{WARN} No close date"
        rows.append(("Close date", short_date(cd) if cd else "not set", cd_flag))
        idle_flag = ""
        if "stale_record" in codes:
            f = by_code["stale_record"][0]
            idle_flag = f"{WARN} Stale (≥{config['stale_days']} days)" if f["days"] is not None else f"{WARN} No activity logged"
            if a["latest_email"] is not None and (deal["last_activity"] is None or
                                                  a["latest_email"].date() > deal["last_activity"]):
                idle_flag += f"; last email {short_date(a['latest_email'].date())} — activity not logged in CRM"
        rows.append(("Days since last activity", str(a["days_idle"]) if a["days_idle"] is not None else "unknown", idle_flag))
        out.append("| Field | Value | Flag |")
        out.append("|---|---|---|")
        for field, value, fl in rows:
            out.append(f"| {field} | {value} | {fl} |" if fl else f"| {field} | {value} | |")
    out.append("")

    # ---- people
    out.append("**Who's on the call**")
    if not a["people"] and a["domain_only"]:
        out.append(f"- External attendees not named — only the company domain ({', '.join(a['domain_only'])}) was given. "
                   f"Ask who is joining; new-stakeholder and first-meeting-signer checks could not run. {WARN}")
    elif not a["people"]:
        out.append("- No external attendees.")
    for p in a["people"]:
        title = f", {p['title']}" if p["title"] else ""
        bits = []
        if not p["in_crm"]:
            bits.append("not in CRM — title and history unknown")
        else:
            bits.append(config["role_labels"][p["role"]])
        line = f"- **{p['name']}**{title} — " + ", ".join(bits)
        if p["response"] == "declined":
            line += " (declined the invite)"
        elif p["response"] in ("tentative", "needs_action"):
            line += f" ({'tentative' if p['response'] == 'tentative' else 'has not responded'})"
        if p["other_org"]:
            line += f" (other org: {p['other_org']})"
        if p["is_new"]:
            line += ". First meeting. No prior touchpoints."
        elif p["prior_meetings"] == 0:
            line += f". First meeting ({p['prior_emails']} prior emails)."
        else:
            calls = f"{p['prior_meetings']} prior call{'s' if p['prior_meetings'] != 1 else ''}"
            emails = f", {p['prior_emails']} email{'s' if p['prior_emails'] != 1 else ''}" if p["prior_emails"] else ""
            line += f", {calls}{emails}."
        if p["last_touchpoint"]:
            lt = p["last_touchpoint"]
            line += f" Last ({short_date(lt['date'])}): {lt['summary']}."
        if (p["is_new"] or p["signer_first"]) and p["response"] != "declined":
            line += f" {WARN}"
        out.append(line)
    if a["ours"]:
        out.append("- Our side: " + ", ".join(x["name"] for x in a["ours"]))
    out.append("")

    # ---- open threads
    out.append("**Open threads**")
    thread_lines = []
    for f in by_code.get("unanswered_question", []):
        q = f["question"]
        thread_lines.append(f"- {first_name(q['asked_by'])} asked about {q['topic']} "
                            f"({short_date(q['asked_at'])} {q['channel']}) — unanswered.")
    for c in a["commitments"]:
        if c["done"]:
            continue
        due = f" by {short_date(c['due'])}" if c["due"] else " (no due date)"
        if c["side"] == "ours":
            prefix = f"- CRM task: {c['what']} ({c['owner']}{', due ' + short_date(c['due']) if c['due'] else ''})" \
                if c["from_task"] else f"- {first_name(c['owner'])} committed to {c['what']}{due}"
            if c["overdue_days"] is not None:
                thread_lines.append(f"{prefix} — not done yet ({c['overdue_days']} day"
                                    f"{'s' if c['overdue_days'] != 1 else ''} overdue). {WARN}")
            else:
                thread_lines.append(f"{prefix} — open.")
        else:
            state = (f"{c['overdue_days']} day{'s' if c['overdue_days'] != 1 else ''} late; ask for it"
                     if c["overdue_days"] is not None else "waiting on them")
            thread_lines.append(f"- {first_name(c['owner'])} owes {c['what']}{due} — {state}.")
    for s in a["slack"]:
        thread_lines.append(f"- Slack {s['channel']} — {s['author']} ({short_date(s['posted_at'].date())}): "
                            f"“{s['text']}”")
    if not thread_lines:
        read = sources_read(b, a, config, "threads")
        thread_lines.append(f"- None found in {', '.join(read)}." if read else
                            "- None found — no email, CRM task, Slack or call-note source could be read.")
    out += thread_lines
    out.append("")

    # ---- risks
    out.append("**Risks & landmines**")
    risks = []
    if "no_history_found" in codes:
        risks.append("- No history with this company in any source read — need, budget, timeline and who "
                     "decides are all unknown.")
    if "no_crm_record" in codes:
        risks.append("- Not in the pipeline: no CRM record, so no history to lean on. Don't assume anything was agreed.")
    if "crm_empty" in codes:
        risks.append("- CRM record is empty — stage, amount and close date are unknown.")
    for f in by_code.get("stale_record", []):
        if f["outdated"]:
            risks.append("- CRM record looks outdated "
                         f"({'no activity logged' if f['days'] is None else str(f['days']) + ' days since last activity'})"
                         " — treat stage, amount and close date as unconfirmed.")
    for f in by_code.get("close_date_past", []):
        risks.append(f"- Close date {short_date(f['close_date'])} has passed — the forecast is wrong until it is reset.")
    for f in by_code.get("budget_conflict", []):
        bs = f["statement"]
        who = f"{bs['speaker']}, " if bs["speaker"] else ""
        risks.append(f"- Budget conflict: CRM says {deal_amount(deal)}, prospect said {budget_text(bs)} — "
                     f"“{bs['quote']}” ({who}{source_phrase(bs['source'])}).")
    for c in b["signals"]["competitors"]:
        who = f"{c['speaker']}, " if c["speaker"] else ""
        ctx = f"{c['context']} — " if c["context"] else "mentioned — "
        risks.append(f"- {c['competitor']}: {ctx}“{c['quote']}” ({who}{source_phrase(c['source'])}).")
    for f in by_code.get("signer_first_meeting", []):
        p = f["person"]
        risks.append(f"- Signer in the room for the first time: {p['name']}"
                     f"{' (' + p['title'] + ')' if p['title'] else ''} — no relationship yet.")
    if not risks:
        read = sources_read(b, a, config, "risks")
        risks.append(f"- None evidenced in the data read for this briefing: {', '.join(read)}." if read else
                     "- None evidenced — no CRM, email, Slack or call-note data could be read.")
    out += risks
    out.append("")

    # ---- company context
    if a["enrichment_mode"] == "basic":
        out.append("Company context: not available (enrichment: basic).")
    else:
        es = b["sources"]["enrichment"]
        if es["status"] != "ok":
            out.append(f"Company context: not available — enrichment sources {es['status'].replace('_', ' ')}"
                       f"{' (' + es['detail'] + ')' if es['detail'] else ''}. Nothing here was guessed.")
        elif not b["enrichment"]:
            out.append("Company context: enrichment ran but found nothing with a citable source.")
        else:
            out.append("**Company context** (enrichment: full; each item cited)")
            for item in sorted(b["enrichment"], key=lambda x: x["date"], reverse=True):
                out.append(f"- {item['summary']} ({item['kind'].replace('_', ' ')}, {short_date(item['date'])} — "
                           f"{item['source_url']})")
    out.append("")

    out.append("**Suggested agenda**")
    for i, item in enumerate(agenda, start=1):
        out.append(f"{i}. {item}")
    out.append("")

    # ---- sources / gaps
    src = b["sources"]
    parts, gaps = [], []
    for k in SOURCE_KEYS + [k for k in OPTIONAL_SOURCE_KEYS if k in src]:
        s = src[k]
        provider = PROVIDER_LABELS.get(s["provider"] or "", s["provider"])
        name = SOURCE_LABELS[k] + (f": {provider}" if provider and provider not in ("none", SOURCE_LABELS[k]) else "")
        if k == "enrichment" and a["enrichment_mode"] == "basic":
            parts.append("Enrichment: off (basic)")
            continue
        if s["status"] == "ok":
            extra = ""
            if k == "email":
                extra = f" ({len(a['threads_used'])} threads"
                if a["threads_total"] > len(a["threads_used"]):
                    extra += f" of {a['threads_total']}"
                extra += ")"
            elif k == "slack":
                extra = f" ({a['slack_total']} mention{'s' if a['slack_total'] != 1 else ''} in {config['slack_lookback_days']} days)"
            elif k == "crm":
                extra = f" (record {'found' if b['crm']['record_found'] else 'not found'})"
            elif k == "call_notes":
                n = a["past_calls_total"]
                extra = f" ({n} past call{'s' if n != 1 else ''})"
            parts.append(f"{name} ✓{extra}")
        else:
            label = s["status"].replace("_", " ")
            parts.append(f"{name} ✗ {label}")
            gaps.append(f"{SOURCE_LABELS[k]} {label}{' (' + s['detail'] + ')' if s['detail'] else ''}")
    if a["domain_only"]:
        gaps.append("Attendees not named (company domain only)")
    out.append("Sources: " + " · ".join(parts))
    out.append("Gaps: " + ("; ".join(gaps) if gaps else "none") + ".")
    out.append(f"Event key: {key} · as of {as_of.isoformat()}")
    out.append("_Internal prep only — never paste any of this into a customer-facing email, deck or shared doc._")
    return "\n".join(out)


def json_ready(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a pre-call briefing from a normalised JSON bundle.")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--as-of", required=True, help="ISO datetime with UTC offset; the only 'now'.")
    parser.add_argument("--internal-domain", action="append", default=[],
                        help="Your company's email domain(s); repeatable. Required.")
    parser.add_argument("--enrichment", choices=["basic", "full"], default="basic")
    parser.add_argument("--scheduled", action="store_true",
                        help="Scheduled run: skip unless the meeting starts within --lookahead-minutes after "
                             "--as-of, and skip events already in --log. Without it the briefing is always printed.")
    parser.add_argument("--lookahead-minutes", type=int, default=30)
    parser.add_argument("--log", type=Path, help="briefing_log markdown file to check and append to.")
    parser.add_argument("--dry-run", action="store_true", help="Read the log but never append to it.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of markdown.")
    args = parser.parse_args(argv)

    try:
        as_of = parse_moment(args.as_of, lambda problem: InputError(f"--as-of: {problem}"))
        internal = sorted({d.strip().lower().lstrip("@") for d in args.internal_domain if d.strip()})
        if not internal:
            raise InputError("--internal-domain: at least one internal domain is required, "
                             "otherwise internal meetings cannot be recognised and skipped")
        if args.lookahead_minutes <= 0:
            raise InputError("--lookahead-minutes: must be a positive integer")
        config = load_config(args.config)
        bundle = load_bundle(args.bundle)
        meeting = bundle["meeting"]
        key = event_key(meeting)

        def skip(reason: str) -> int:
            if args.json:
                print(json.dumps({"status": "skipped", "event_key": key, "reason": reason}, ensure_ascii=False, indent=2))
            else:
                print(f"Skipped: {reason}")
            return 0

        if meeting["status"] == "cancelled":
            return skip(f"{key} is cancelled.")
        externals = [x for x in meeting["attendees"] if not in_domains(domain_of(x["email"]), internal)]
        internal_ext = [d for d in meeting["external_domains"] if in_domains(d, internal)]
        if internal_ext:
            raise InputError(f"bundle {args.bundle}: $.meeting.external_domains: {', '.join(internal_ext)} is one of your "
                             "internal domains; list the other company's domain")
        if not externals and not meeting["external_domains"]:
            if meeting["calendar_source"] == "manual" or not meeting["attendees"]:
                raise InputError(
                    f"bundle {args.bundle}: $.meeting.attendees: no external attendee information supplied — "
                    "ask the user for the external attendees' email addresses (add them to $.meeting.attendees) "
                    "or their company's email domain (add it to $.meeting.external_domains), then rerun. "
                    "Never invent attendees.")
            return skip(f"internal-only meeting “{meeting['title']}” — no briefing.")
        if args.scheduled:
            until = as_of + timedelta(minutes=args.lookahead_minutes)
            if not (as_of < meeting["start"] <= until):
                return skip(f"{key} does not start within the next {args.lookahead_minutes} minutes.")
        seen = read_log(args.log) if args.log else {}
        # The log stops a schedule from posting the same call twice. A person asking again
        # (no --scheduled) gets the briefing again; the log is not given a second row.
        if key in seen and args.scheduled:
            return skip(f"already briefed {key} (logged {seen[key]}).")

        analysis = analyse(bundle, config, as_of, internal, args.enrichment)
        markdown = render(bundle, analysis, config, as_of, key)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        agenda, _ = agenda_and_goals(analysis, config)
        payload = {
            "status": "briefed",
            "event_key": key,
            "as_of": as_of.isoformat(),
            "company": bundle["company_name"],
            "crm_state": analysis["crm_state"],
            "flags": [{"code": f["code"]} | {k: v for k, v in f.items() if k not in ("code", "person", "signers")}
                      | ({"person": f["person"]["name"]} if "person" in f else {}) for f in analysis["flags"]],
            "agenda": agenda,
            "already_logged": key in seen,
            "briefing_markdown": markdown,
        }
        print(json.dumps(json_ready(payload), ensure_ascii=False, indent=2))
    else:
        print(markdown)

    if args.log and not args.dry_run and key not in seen:
        crm_state = analysis["crm_state"]
        append_log(args.log, [key, bundle["company_name"], meeting["start_raw"], as_of.isoformat(),
                              {"ok": "record found", "not_configured": "not configured"}.get(crm_state, crm_state)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
