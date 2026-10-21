#!/usr/bin/env python3
"""Render a sales-to-CS handoff from one normalised JSON bundle.

Stdlib only, offline, deterministic, Python 3.8+. --as-of is the only "now";
the wall clock is never read. The agent does the reading and extraction
(meetings, CRM, email); this script does the checkable parts:

  * validates the bundle (exit 2 names the file and the JSON path)
  * classifies meeting attendees (ours / the account / other) and drops
    note-taker bots
  * excludes internal-only meetings, merges duplicate notes of the same
    meeting (same start, same title or overlapping customer attendees)
  * orders meetings chronologically and resolves every finding to its
    meeting title, date and note link
  * merges stakeholders (by email, then by name), adds unlisted customer
    attendees, computes first/last meeting seen, flags appeared-once,
    went-quiet and never-in-a-meeting people and missing key roles
  * detects promise conflicts (same topic, different terms, across
    meetings or email) and contract flags (not in contract, roadmap,
    non-standard, not checked)
  * computes coverage: meetings read, date range, transcript access,
    exclusions, merges, plan-window and sales-cycle gaps, source status
  * renders the handoff doc, a promises-only view, a CRM-note draft or a
    Slack-post draft; drafts are never sent by this script
  * escapes every interpolated value for its output: markdown (backslash
    first, then [ ] * _ ` ~ |, and & < > as entities), Slack (& < > as
    entities; * _ ~ ` @ as full-width look-alikes) or the CRM note, so a value
    can't form a link, autolink, raw HTML, mention or formatting; keeps only
    http(s) links; removes the email addresses and phone numbers it
    recognises (pattern-based, so unusual formats can be missed). A web
    address written out in a value stays text, but a viewer may still show
    it as a clickable address.
  * scheduled mode, two-phase: a closed-won deal not yet in the log is
    printed and recorded as `pending`; after the doc is saved (and the post
    sent) the agent runs --confirm-delivered KEY, which marks it `delivered`.
    Only scheduled runs and --confirm-delivered write the log, under a lock.

Exit codes: 0 output, one-line skip notice or confirmation printed; 2 bad
input (nothing on stdout); 3 scheduled run while the meeting-notes source is
not ok (nothing recorded, retry later); 4 the handoff is pending delivery
confirmation; 5 another run holds the log lock; 6 the output could not be
written (closed pipe, full disk; a scheduled handoff stays pending).
"""

import argparse
import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import unicodedata
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "handoff.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCE_KEYS = ["meeting_notes", "crm", "email"]
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_configured"]
PROVIDER_LABELS = {
    "granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies", "otter": "Otter",
    "zoom": "Zoom", "chorus": "Chorus", "upload": "uploaded transcripts", "hubspot": "HubSpot",
    "attio": "Attio", "salesforce": "Salesforce", "gmail": "Gmail", "outlook": "Outlook", "none": "none",
}
SOURCE_LABELS = {"meeting_notes": "Meeting notes", "crm": "CRM", "email": "Email"}
TRANSCRIPT_STATES = ["ok", "denied", "unavailable", "not_requested"]
TRANSCRIPT_RANK = {"ok": 0, "denied": 1, "unavailable": 2, "not_requested": 3}
MATCH_KINDS = ["domain", "title", "mention", "crm", "user"]
QUOTE_FROM = ["transcript", "notes", "email", "crm", "slack", "doc"]
SOURCE_TYPES = ["meeting", "email", "crm_note", "crm_field", "slack", "doc"]
ROLES = ["champion", "economic_buyer", "signer", "executive_sponsor", "day_to_day", "user",
         "technical_owner", "data_owner", "procurement", "legal_security", "influencer", "detractor", "unknown"]
PROMISE_CATEGORIES = ["feature", "integration", "custom_work", "pricing", "discount", "sla", "support",
                      "training", "timeline", "roadmap", "security", "other"]
REQUIREMENT_CATEGORIES = ["integration", "data", "migration", "security", "sso", "compliance",
                          "infrastructure", "other"]
OBJECTION_STATES = ["open", "partly_resolved", "resolved"]
COMPETITOR_STATES = ["incumbent", "evaluated", "replacing", "chosen_over", "mentioned"]
IN_CONTRACT = ["yes", "no", "unknown"]
QUESTION_OWNERS = ["us", "customer", "unknown"]
PERIODS = {"yr": "/yr", "mo": "/mo", "one_time": " one-time"}
PLANS = ["free", "basic", "plus", "business", "enterprise", "unknown"]
FORMATS = ["doc", "promises", "crm-note", "slack"]
CONFIG_KEYS = [
    "quiet_days", "quiet_meetings", "single_appearance_min_meetings", "milestone_soon_days",
    "free_plan_window_days", "cycle_gap_days", "thin_meetings", "key_roles", "role_labels",
    "promise_category_labels", "requirement_category_labels", "bot_email_patterns", "consumer_domains",
    "checklist",
]
LOG_TITLE = "# Sales-to-CS handoff log"
LOG_COLUMNS = ["handoff_key", "account", "close_date", "status", "written_at", "delivered_at",
               "meetings_read", "promises", "conflicts"]
LOG_HEADER = "| " + " | ".join(LOG_COLUMNS) + " |"
LOG_SEPARATOR = "|" + "---|" * len(LOG_COLUMNS)
# Logs written before the two-phase confirm step: every row counts as delivered.
LEGACY_LOG_HEADER = "| handoff_key | account | close_date | written_at | meetings_read | promises | conflicts |"
LOG_STATUSES = ["pending", "delivered"]
LOCK_STALE_MINUTES = 15  # a lock file older than this is from a crashed run and is removed
LOCK_FUTURE_SECONDS = 60  # a lock dated further ahead than this (clock change, copied file) is stale too
EXIT_BAD_INPUT, EXIT_SOURCE_NOT_OK, EXIT_PENDING, EXIT_LOCKED, EXIT_OUTPUT = 2, 3, 4, 5, 6
# ASCII digits only; hours 00-23, minutes and seconds 00-59, offsets up to ±14:00. Checked before fromisoformat,
# so every Python from 3.8 agrees (3.11+ alone would accept T24:00; all accept offsets such as +00:60 or +23:59).
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DATETIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                         r"(?::[0-5][0-9](?:\.[0-9]{3}|\.[0-9]{6})?)?"
                         r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))?")
MIN_YEAR, MAX_YEAR = 1900, 2999
MAX_CONFIG_INT = 3650
# Handoff keys (deal record_ref, --confirm-delivered) are printed and stored in the log: plain characters only.
KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:/_.-]*")
DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
# Letters and digits in any script (josé@empresa.example, info@münchen.example), and the full-width ＠. The local
# part is capped at 64 characters (the RFC limit), so a very long token without an @ is scanned in linear time.
EMAIL_RE = re.compile(r"(?:mailto:)?[\w.%+-]{1,64}[@\uff20\ufe6b][\w-]+(?:\.[\w-]+)+")
# Invisible characters removed from printed text: control characters, soft hyphen, zero-width space, word joiner,
# BOM, and the direction marks, embeddings, overrides and isolates that can reorder what a reader sees.
INVISIBLE_RE = re.compile("[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f\u00ad\u200b\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
# Phone candidates: E.164 (+14155550134), a bare run of 10 to 15 digits, or digit groups split by one space,
# dot or hyphen, with an optional country code (+33, 0049) and an optional bracketed area or trunk code
# ((555), (0)). A candidate never starts
# inside a word, number, path, amount or time, and never ends right before another digit group, so it is
# always a whole token. is_phone() then decides; see redact().
PHONE_RE = re.compile(
    r"(?<![\w+#/.,$€£¥\\\u2013-])(?<!\d:)"
    r"(?:\+\d{8,15}|\d{10,15}"
    r"|(?:\+\d{1,3}[ .\u2013-]?|00\d{1,3}[ .\u2013-])?(?:\(\d{1,5}\)[ .\u2013-]?)?\d{1,10}(?:[ .\u2013-]\d{1,10}){0,6})"
    r"(?![\w@]|[ .,:/\u2013-]?\d)")
# Words right before a number that make it something other than a phone number (PO 4500 0123 45, Order No. …).
NOT_PHONE_CONTEXT = re.compile(
    r"(?i)(?:\b(?:po|p\.o|purchase orders?|orders?|invoices?|inv|tickets?|cases?|accounts?|acct|contracts?|ref|"
    r"reference|serial|sku|tracking|id|iban|version|ver|build|release|seats?|licen[cs]es?|users?)\b\.?\s*"
    r"(?:no\.?|nr\.?|number|num|id)?\s*[:#]*\s*)$")
# A word right before a number that says it is a phone number (Call 4155550134, mobile: 07700900123, text me on …).
# Only short connecting words and punctuation may sit between the cue and the number, so "the call" a sentence
# earlier does not turn a price list into a phone number.
PHONE_CUE = re.compile(
    r"(?im)(?:\b(?:call|phone|tel|telephone|mobile|cell|cellphone|sms|text|whatsapp|fax|dial|ring)|"
    r"\b(?:(?:my|his|her|their|[a-z][a-z'’ -]*['’]s) +(?:(?:personal|direct|best|work|home) +)?|(?:personal|direct|best|work|home) +)number|^[ \t]*number)\b"
    r"(?:[\s:.#()-]|['’]s|\b(?:me|us|him|her|them|on|at|no|nr|number|num|via|is|was)\b){0,8}$")
# Phone formats recognised without a + or a cue word, with the same separator between every group (see is_phone):
# (555) 123-4567; 555-123-4567, 555.123.4567, 1-800-555-0199; 06 12 34 56 78 (five 2-digit groups after a 0);
# 07700 900123, 07700-900123, 020 7946 0958, 030 12345678 (a trunk 0, then 10 or 11 digits in 2 or 3 groups).
PHONE_FORMATS = [re.compile(r) for r in (
    r"\(\d{3}\)([ .\u2013-]?)\d{3}[ .\u2013-]\d{4}",
    r"(?:1([.\u2013-]))?\d{3}([.\u2013-])\d{3}\2\d{4}",
    r"0[1-9]([ .\u2013-])\d{2}(?:\1\d{2}){3}",
    r"0\d{1,4}([ .\u2013-])\d{2,8}(?:\1\d{2,8})?",
)]
# Dates inside a digit string (2026-10-15, 15.10.2026, 10/15/2026); is_date_in() checks the month and day, so
# +81-90-1234-5678 is a phone number, not day 81 of month 90.
DATE_IN_NUMBER = re.compile(r"(?<!\d)(?:(\d{4})[./-](\d{1,2})[./-](\d{1,2})|(\d{1,2})[./-](\d{1,2})[./-](\d{4}))(?!\d)")
URL_SAFE = "/:?#@!$&'*+,;=%~"
THOUSANDS_RE = re.compile(r"(?<=\d)[,.' ](?=\d{3}(?!\d))")
# Escaping tables (str.translate replaces every character in one pass, so the backslash added for one
# character is never escaped again, and the backslash itself is always escaped).
# Markdown: no raw HTML or entities, no link, image, autolink or reference syntax, no emphasis, code or
# strikethrough, and no table-cell break. Parentheses are left alone: they only form a link right after an
# unescaped "]", which a value can no longer produce.
MD_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\\": "\\\\", "[": "\\[", "]": "\\]",
                            "*": "\\*", "_": "\\_", "`": "\\`", "~": "\\~", "|": "\\|", "$": "\\$", "=": "\\="})
# Autolinks: after escaping, scheme:// becomes scheme\:// and every dot inside a word is backslash-escaped in
# markdown (renders the same), so no renderer sees a URL, www. or bare-domain link in a value. Slack and the CRM
# note have no backslash escape: :// becomes look-alike slashes and a dot before a letter (or after www) a
# look-alike dot (U+2024).
MD_DOT_RE = re.compile(r"(?<=\S)\.(?=[\w-])")
DOMAIN_DOT_RE = re.compile(r"(?<=[\w-])\.(?=[^\W\d_])|(?<=www)\.", re.IGNORECASE)
# Slack mrkdwn has no backslash escape: &, < and > become entities, so <!channel>, <@U…>, <#C…> and <url|label>
# are inert, and the formatting and mention characters (and brackets, so a tool that converts markdown links for
# Slack finds none) become full-width look-alikes.
SLACK_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", "*": "＊", "_": "＿", "~": "～",
                               "`": "｀", "@": "＠", "[": "［", "]": "］"})
# CRM note (plain text or HTML, sometimes rendered as markdown): no raw HTML or entities, no markdown link syntax.
CRM_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\\": "\\\\", "[": "\\[", "]": "\\]"})
# Keys whose string values are never display text (links are rendered by the script only).
NON_TEXT_KEYS = {"url", "urls", "email", "path", "start_raw", "ref"}
# Enum codes are looked up after escaping (role_labels["economic_buyer"], status == "not_configured"), so the
# escaped view leaves exactly these values alone. They are plain words joined by "_", which can't form markup.
ENUM_CODES = frozenset(v for values in [SOURCE_STATUSES, TRANSCRIPT_STATES, MATCH_KINDS, QUOTE_FROM, SOURCE_TYPES,
                                        ROLES, PROMISE_CATEGORIES, REQUIREMENT_CATEGORIES, OBJECTION_STATES,
                                        COMPETITOR_STATES, IN_CONTRACT, QUESTION_OWNERS, list(PERIODS), PLANS]
                       for v in values if "_" in v)
WARN = "⚠️"
DOC_PLACEHOLDER = "(link to the handoff doc: rerun with --doc-url)"
INTERNAL_FOOTER = ("_Internal handoff: it holds verbatim customer quotes and commercial terms. "
                   "Review before sharing anything from it outside the account team._")


class InputError(Exception):
    """Bad input; the message names the file and the JSON path. `code` is the exit code."""

    def __init__(self, message: str, code: int = EXIT_BAD_INPUT):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- parsing helpers


def safe_url(value: Optional[str]) -> Optional[str]:
    """An absolute http(s) URL with a host, percent-encoded for markdown and Slack; None otherwise."""
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port  # raises ValueError on a malformed port
        clean = quote(value, safe=URL_SAFE)  # UnicodeEncodeError on undecodable command-line bytes
    except (ValueError, UnicodeError):
        return None
    # A real host name or IPv4 address; no user name or password, so https://notes.example\@evil.example (which
    # goes to evil.example) and links that carry credentials are dropped.
    if parts.scheme.lower() not in ("http", "https") or not host or not DOMAIN_RE.fullmatch(host) or port == 0 \
            or parts.username is not None or parts.password is not None:
        return None
    return clean


class Reader:
    """Typed accessors that report errors as '<label>: <json.path>: problem'."""

    def __init__(self, label: str):
        self.label = label
        self.dropped_links = []  # JSON paths of links that were not http(s) URLs

    def fail(self, path: str, problem: str) -> InputError:
        return InputError("{}: {}: {}".format(self.label, path, problem))

    def obj(self, value, path: str, required: bool = True) -> Optional[dict]:
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        return value

    def lst(self, value, path: str, required: bool = False) -> list:
        if value is None:
            if required:
                raise self.fail(path, "is required (use [] when there are none)")
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        return value

    def url(self, parent: dict, key: str, path: str) -> Optional[str]:
        """Optional link: kept only when it is an http(s) URL; anything else is dropped and counted."""
        value = self.text(parent, key, path, required=False)
        if value is None:
            return None
        clean = safe_url(value)
        if clean is None:
            self.dropped_links.append("{}.{}".format(path, key))
        return clean

    def text(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[str]:
        value = parent.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise self.fail("{}.{}".format(path, key), "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail("{}.{}".format(path, key), "must be a string, got {!r}".format(value))
        return " ".join(value.split())

    def strings(self, parent: dict, key: str, path: str, required: bool = False) -> List[str]:
        out = []
        for i, item in enumerate(self.lst(parent.get(key), "{}.{}".format(path, key), required)):
            if not isinstance(item, str) or not item.strip():
                raise self.fail("{}.{}[{}]".format(path, key, i), "must be a non-empty string")
            out.append(" ".join(item.split()))
        return out

    def boolean(self, parent: dict, key: str, path: str, default: Optional[bool] = None) -> bool:
        value = parent.get(key, default)
        if not isinstance(value, bool):
            raise self.fail("{}.{}".format(path, key), "must be true or false, got {!r}".format(value))
        return value

    def integer(self, parent: dict, key: str, path: str, required: bool = True,
                maximum: int = 10 ** 6) -> Optional[int]:
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
            raise self.fail("{}.{}".format(path, key), "must be an integer from 0 to {}, got {!r}".format(
                maximum, value))
        return value

    def number(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[float]:
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value >= 1e15 \
                or not math.isfinite(value):
            raise self.fail("{}.{}".format(path, key), "must be a finite non-negative number below 1e15, got {!r}".format(
                value))
        return float(value)

    def enum(self, parent: dict, key: str, path: str, allowed: List[str], default: Optional[str] = None) -> str:
        value = parent.get(key, default)
        if value not in allowed:
            raise self.fail("{}.{}".format(path, key),
                            "must be one of {}, got {!r}".format(", ".join(allowed), value))
        return value

    def day(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[date]:
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        where = "{}.{}".format(path, key)
        if DATETIME_RE.fullmatch(value):  # a datetime where a date is expected: its local date as written
            return parse_moment(value, lambda problem: self.fail(where, problem)).date()
        return parse_day(value, lambda problem: self.fail(where, problem))

    def moment(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[datetime]:
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        return parse_moment(value, lambda problem: self.fail("{}.{}".format(path, key), problem))


def parse_day(value: str, fail) -> date:
    """Strict YYYY-MM-DD, so every Python from 3.8 accepts and rejects the same strings."""
    if not DATE_RE.fullmatch(value):
        raise fail("{!r} is not a YYYY-MM-DD date".format(value))
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise fail("{!r} is not a real calendar date".format(value)) from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise fail("{!r} is outside the years {} to {}".format(value, MIN_YEAR, MAX_YEAR))
    return day


def parse_moment(value: str, fail) -> datetime:
    """Strict YYYY-MM-DDTHH:MM[:SS[.fff|.ffffff]] plus Z or ±HH:MM, checked before fromisoformat."""
    if not DATETIME_RE.fullmatch(value):
        raise fail("{!r} is not an ISO-8601 datetime like 2026-10-01T09:00:00-07:00".format(value))
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise fail("{!r} is not a real date and time".format(value)) from None
    if moment.tzinfo is None:
        raise fail("{!r} has no UTC offset; use e.g. 2026-10-01T09:00:00-07:00".format(value))
    if not MIN_YEAR <= moment.year <= MAX_YEAR:
        raise fail("{!r} is outside the years {} to {}".format(value, MIN_YEAR, MAX_YEAR))
    return moment


def path_kind(path: Path) -> str:
    """'file', 'folder', 'missing', 'other' or 'unreadable'. Never raises: on Python 3.8, Path.is_file()
    raises PermissionError inside an unreadable folder instead of returning False."""
    try:
        mode = os.stat(str(path)).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except (OSError, ValueError):
        return "unreadable"
    return "file" if stat.S_ISREG(mode) else "folder" if stat.S_ISDIR(mode) else "other"


def reject_constant(name: str):
    raise ValueError("{} is not a valid JSON number".format(name))


def load_json(path: Path, label: str):
    kind = path_kind(path)
    if kind == "missing":
        raise InputError("{}: file not found: {}".format(label, path))
    if kind != "file":
        raise InputError("{}: {} is not a readable file ({})".format(label, path, kind))
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
        json.dumps(data, ensure_ascii=False).encode("utf-8")  # a lone surrogate (\ud800) can't be printed or logged
        return data
    except UnicodeEncodeError:
        raise InputError("{}: {} is not valid Unicode (it has an unpaired surrogate escape such as \\ud800)".format(
            label, path)) from None
    except json.JSONDecodeError as exc:
        raise InputError("{}: {} is not valid JSON (line {}, column {}: {})".format(
            label, path, exc.lineno, exc.colno, exc.msg)) from None
    except UnicodeDecodeError:
        raise InputError("{}: {} is not UTF-8 text".format(label, path)) from None
    except ValueError as exc:  # NaN / Infinity, or an integer too long to parse
        raise InputError("{}: {} is not valid JSON ({})".format(label, path, exc)) from None
    except OSError as exc:
        raise InputError("{}: {} cannot be read ({})".format(label, path, exc.strerror or exc)) from None
    except RecursionError:
        raise InputError("{}: {} is nested too deeply".format(label, path)) from None


def load_config(path: Path) -> dict:
    config = load_json(path, "config")
    r = Reader("config {}".format(path))
    r.obj(config, "$")
    missing = [key for key in CONFIG_KEYS if key not in config]
    if missing:
        raise InputError("config {}: missing key(s): {}".format(path, ", ".join(missing)))
    for key in ["quiet_days", "quiet_meetings", "single_appearance_min_meetings", "milestone_soon_days",
                "free_plan_window_days", "cycle_gap_days", "thin_meetings"]:
        r.integer(config, key, "$", maximum=MAX_CONFIG_INT)
    for i, role in enumerate(r.lst(config["key_roles"], "$.key_roles", required=True)):
        if role not in ROLES:
            raise r.fail("$.key_roles[{}]".format(i), "unknown role {!r}".format(role))
    for key, allowed in [("role_labels", ROLES), ("promise_category_labels", PROMISE_CATEGORIES),
                         ("requirement_category_labels", REQUIREMENT_CATEGORIES)]:
        labels = r.obj(config[key], "$." + key)
        absent = [code for code in allowed if not isinstance(labels.get(code), str)]
        if absent:
            raise r.fail("$." + key, "needs a label for: {}".format(", ".join(absent)))
    r.strings(config, "bot_email_patterns", "$", required=True)
    r.strings(config, "consumer_domains", "$", required=True)
    for i, item in enumerate(r.lst(config["checklist"], "$.checklist", required=True)):
        r.obj(item, "$.checklist[{}]".format(i))
        r.text(item, "label", "$.checklist[{}]".format(i))
        if not r.strings(item, "satisfied_by", "$.checklist[{}]".format(i)):
            raise r.fail("$.checklist[{}].satisfied_by".format(i), "must list at least one key")
    return config


# --------------------------------------------------------------------------- text helpers


def fold(text: str) -> str:
    """Lower-case, accent-folded, punctuation-free key used for matching only."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split())


def clean_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if domain.startswith("@"):
        domain = domain[1:]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_bot(email: str, pattern: str) -> bool:
    """A pattern ending in '@' (bot@) must be the whole local part, so talbot@ is a person. A pattern that is a
    domain (read.ai) must be the address's domain or a parent of it, so ann@bread.ai.example is a person."""
    if pattern.endswith("@"):
        return email.startswith(pattern)
    if "@" not in pattern and DOMAIN_RE.fullmatch(pattern):
        return in_domains(domain_of(email), [pattern])
    return pattern in email


def in_domains(domain: str, domains: List[str]) -> bool:
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


def short_date(d: date, as_of: date) -> str:
    text = "{} {}".format(MONTHS[d.month - 1], d.day)
    return text if d.year == as_of.year else "{}, {}".format(text, d.year)


def long_date(d: date) -> str:
    return "{} {}, {}".format(MONTHS[d.month - 1], d.day, d.year)


def link(label: str, url: Optional[str]) -> str:
    """Markdown link. `label` is already escaped (see md_text); `url` was checked by safe_url."""
    return "[{}]({})".format(label, url) if url else label


def is_phone(text: str, start: int, number: str) -> bool:
    """Whether a PHONE_RE candidate is a phone number rather than a date, time, IP, version, amount, code or list.

    Only clear signals count: a leading + (country code), a cue word such as "call" or "mobile" right before it,
    or a standard phone format (PHONE_FORMATS). A run of numbers without one of those (prices, tiers, years,
    repeated values, ranges, in any order) is never a phone number.
    """
    groups = re.findall(r"\d+", number.replace("(0)", ""))  # +33 (0)1 …: the bracketed trunk 0 is not a group
    digits = sum(len(g) for g in groups)
    before = text[max(0, start - 40):start]
    intl = number.startswith(("+", "00"))
    cue = bool(PHONE_CUE.search(before))
    if digits > 15 or NOT_PHONE_CONTEXT.search(before):
        return False
    if len(groups) == 1:  # E.164, or a bare run of digits right after "call", "phone", "mobile"…
        return (number.startswith("+") and digits >= 8) or (digits >= 10 and cue)
    if digits < (8 if intl else 10):
        return False
    if DATE_RE.match(number) or is_date_in(number):
        return False
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", number):  # IPv4, or 1.250.000.000
        return False
    if any(len(g) == 1 for g in groups[2:]):  # only a country or trunk code is a single digit
        return False
    if all(len(g) == 4 and g[:2] in ("19", "20") for g in groups):  # a list of years
        return False
    if number.startswith("+"):
        return True
    if cue:
        # "call 1 200 000 000 times" is still a count; anything else phone-shaped after a cue is a phone number.
        thousands = not groups[0].startswith("0") and len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])
        return not thousands
    if len(set(re.findall(r"[^\d()]+", number))) > 1 and not number.startswith(("(", "00")):
        return False  # mixed separators: 9.99 19.99 29.99, 1.5 2.5-3.5
    if re.fullmatch(r"(?:1 )?[2-9]\d{2} [2-9]\d{2} \d{4}", number) and len(set(groups)) == len(groups):
        return True  # standard spaced US number; repeated groups remain ambiguous lists
    if any(f.fullmatch(number) for f in PHONE_FORMATS[:3]):  # (555) 123-4567, 555-123-4567, 06 12 34 56 78
        return True
    if len(set(groups)) < len(groups) or (len(set(len(g) for g in groups)) == 1 and len(groups[0]) >= 3):
        return False  # repeated values (36000 36000 38000) or a same-width list (0800 0900 1000, 0010 0020 0030)
    if number.startswith("00"):  # 0049 30 12345678: an international prefix written with 00
        return True
    return digits <= 11 and bool(PHONE_FORMATS[3].fullmatch(number))  # 07700 900123, 020 7946 0958


def is_date_in(number: str) -> bool:
    """Whether a digit string holds a plausible date (year 1900-2099, month 1-12, day 1-31, either order)."""
    for m in DATE_IN_NUMBER.finditer(number):
        if m.group(1):
            y, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1900 <= y <= 2099 and 1 <= a <= 12 and 1 <= b <= 31:
                return True
        else:
            a, b, y = int(m.group(4)), int(m.group(5)), int(m.group(6))
            if 1900 <= y <= 2099 and 1 <= a <= 31 and 1 <= b <= 31 and min(a, b) <= 12:
                return True
    return False


def redact(text: str) -> str:
    """One line of printed text with the email addresses and phone numbers it recognises removed.

    Pattern-based, on clear signals only: a leading + (country code), a cue word such as "call" or "mobile"
    right before the number, or a standard phone format with the same separator between groups ((555) 123-4567,
    555-123-4567, 07700 900123, 020 7946 0958, 06 12 34 56 78). Dates, times, date ranges, versions, IPs,
    amounts, seat counts, PO, order or invoice numbers and number lists (prices, tiers, years, repeated values)
    are left alone, and unusual phone formats can be missed.
    """
    # Preserve line starts for Number cues before the existing single-line output normalization.
    text = EMAIL_RE.sub("(email removed)", "\n".join(" ".join(line.split()) for line in INVISIBLE_RE.sub("", text).splitlines()))
    text = PHONE_RE.sub(lambda m: "(phone removed)" if is_phone(text, m.start(), m.group(0)) else m.group(0), text)
    text = " ".join(text.split())
    return PHONE_RE.sub(lambda m: "(phone removed)" if is_phone(text, m.start(), m.group(0)) else m.group(0), text)


def md_text(text: str) -> str:
    """Markdown output: a value can't form a link, image, autolink (URL, www. or bare domain), raw HTML, emphasis,
    math ($), highlight (==) or a table-cell break."""
    return MD_DOT_RE.sub(r"\\.", redact(text).translate(MD_ESCAPES).replace("://", "\\://"))


def no_autolink(text: str) -> str:
    """Plain-text outputs (Slack, CRM note): look-alike slashes and dots, so a URL, www. or domain is not linked."""
    return DOMAIN_DOT_RE.sub("\u2024", text.replace("://", ":\u2215\u2215"))


def html_text(text: str) -> str:
    """CRM-note draft (CRM note bodies are plain text, HTML or markdown): no raw HTML, no link syntax, no autolink."""
    return no_autolink(redact(text).translate(CRM_ESCAPES))


def slack_text(text: str) -> str:
    """Slack mrkdwn: no mention, channel ping, <url|label> link, autolink or bold/italic/strike/code from a value."""
    return no_autolink(redact(text).translate(SLACK_ESCAPES))


def escaped_view(objects: list, esc) -> list:
    """Deep copy of [bundle, analysis, config] with every display string passed through `esc`.

    The copy keeps shared references, and each dict or list is visited once, so nothing is
    escaped twice and matching done in analyse() still holds. Links, emails and internal paths
    (NON_TEXT_KEYS) are left alone: they are never printed as text.
    """
    view = copy.deepcopy(objects)
    seen = set()
    stack = [(view, None)]
    while stack:
        item, key = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        # A list inherits its parent's key, so the strings in "urls" stay links.
        slots = [(k, k) for k in item] if isinstance(item, dict) else [(i, key) for i in range(len(item))]
        for k, text_key in slots:
            v = item[k]
            if isinstance(v, str):
                if text_key not in NON_TEXT_KEYS and v not in ENUM_CODES:
                    item[k] = esc(v)
            elif isinstance(v, (dict, list)):
                stack.append((v, text_key))
    return view


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return "{} {}".format(n, word if n == 1 else (many or word + "s"))


def due_in(days: int) -> str:
    return "today" if days == 0 else "in " + plural(days, "day")


def join_words(items: List[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def money(amount: float, currency: str) -> str:
    whole = "{:,.0f}".format(amount) if float(amount).is_integer() else "{:,.2f}".format(amount)
    return "${}".format(whole) if currency == "USD" else "{} {}".format(whole, currency)


def provider_label(provider: Optional[str]) -> str:
    if not provider:
        return "unknown"
    return PROVIDER_LABELS.get(provider, provider)


# --------------------------------------------------------------------------- bundle loading


def load_bundle(path: Path, internal: List[str], config: dict) -> dict:
    raw = load_json(path, "bundle")
    r = Reader("bundle {}".format(path))
    r.obj(raw, "$")
    if raw.get("schema_version") != 1:
        raise r.fail("$.schema_version", "must be 1, got {!r}".format(raw.get("schema_version")))

    account = r.obj(raw.get("account"), "$.account")
    name = r.text(account, "name", "$.account")
    r.lst(account.get("domains"), "$.account.domains", required=True)
    domains = []
    for d in r.strings(account, "domains", "$.account"):
        d = clean_domain(d)
        if not DOMAIN_RE.fullmatch(d):
            raise r.fail("$.account.domains", "{!r} is not an email domain like customer.example "
                                              "(letters, digits, - and .; use the xn-- form for accents)".format(d))
        if in_domains(d, internal):
            raise r.fail("$.account.domains", "{} is one of your internal domains; list the customer's domain".format(d))
        if d in config["consumer_domains"]:
            raise r.fail("$.account.domains", "{} is a personal email provider, not the account's domain".format(d))
        if d not in domains:
            domains.append(d)
    aliases = r.strings(account, "aliases", "$.account")

    sources_raw = r.obj(raw.get("sources"), "$.sources")
    sources = {}
    for key in SOURCE_KEYS:
        src = r.obj(sources_raw.get(key), "$.sources." + key)
        entry = {
            "provider": r.text(src, "provider", "$.sources." + key, required=False),
            "status": r.enum(src, "status", "$.sources." + key, SOURCE_STATUSES),
            "detail": r.text(src, "detail", "$.sources." + key, required=False),
        }
        if key == "meeting_notes":
            entry["plan"] = r.enum(src, "plan", "$.sources.meeting_notes", PLANS, default="unknown")
            scopes = r.strings(src, "scopes", "$.sources.meeting_notes")
            entry["scopes"] = scopes
            entry["scopes_given"] = "scopes" in src
        sources[key] = entry

    search = r.obj(raw.get("search"), "$.search")
    search_from = r.day(search, "from", "$.search")
    search_to = r.day(search, "to", "$.search")
    if search_to < search_from:
        raise r.fail("$.search.to", "is before $.search.from")
    search_info = {"from": search_from, "to": search_to,
                   "methods": r.strings(search, "methods", "$.search"),
                   "terms": r.strings(search, "terms", "$.search")}

    deal = None
    if raw.get("deal") is not None:
        d = r.obj(raw.get("deal"), "$.deal")
        deal = {
            "record_ref": r.text(d, "record_ref", "$.deal", required=False),
            "url": r.url(d, "url", "$.deal"),
            "name": r.text(d, "name", "$.deal", required=False),
            "stage": r.text(d, "stage", "$.deal", required=False),
            "closed_won": r.boolean(d, "closed_won", "$.deal"),
            "amount": r.number(d, "amount", "$.deal", required=False),
            "currency": (r.text(d, "currency", "$.deal", required=False) or "USD").upper(),
            "amount_period": r.enum(d, "amount_period", "$.deal", list(PERIODS), default="yr"),
            "close_date": r.day(d, "close_date", "$.deal", required=False),
            "created_date": r.day(d, "created_date", "$.deal", required=False),
            "owner": r.text(d, "owner", "$.deal", required=False),
            "contract_start": r.day(d, "contract_start", "$.deal", required=False),
            "term_months": r.integer(d, "term_months", "$.deal", required=False),
            "products": r.strings(d, "products", "$.deal"),
            "contract_read": r.boolean(d, "contract_read", "$.deal", default=False),
        }
        if deal["record_ref"] and not KEY_RE.fullmatch(deal["record_ref"]):
            raise r.fail("$.deal.record_ref", "{!r} is the handoff log key: use only letters, digits and : / _ . - "
                                              "(no spaces or |), e.g. deal/48213".format(deal["record_ref"]))

    email_info = r.obj(raw.get("email"), "$.email", required=False) or {}
    threads_read = r.integer(email_info, "threads_read", "$.email", required=False)

    bots = [p.lower() for p in config["bot_email_patterns"]]
    meetings = []
    seen_ids = {}
    exact_dupes = 0
    for i, m in enumerate(r.lst(raw.get("meetings"), "$.meetings", required=True)):
        p = "$.meetings[{}]".format(i)
        r.obj(m, p)
        mid = r.text(m, "meeting_id", p)
        attendees = []
        dropped_bots = 0
        for j, a in enumerate(r.lst(m.get("attendees"), p + ".attendees")):
            ap = "{}.attendees[{}]".format(p, j)
            r.obj(a, ap)
            email = (r.text(a, "email", ap, required=False) or "").lower()
            aname = r.text(a, "name", ap, required=False)
            if not email and not aname:
                raise r.fail(ap, "needs an email or a name")
            if email and "@" not in email:
                raise r.fail(ap + ".email", "{!r} is not an email address".format(email))
            if email and any(is_bot(email, b) for b in bots):
                dropped_bots += 1
                continue
            attendees.append({"email": email, "name": aname or email.split("@")[0], "name_given": bool(aname)})
        matched_by = []
        for j, kind in enumerate(r.lst(m.get("matched_by"), p + ".matched_by")):
            if kind not in MATCH_KINDS:
                raise r.fail("{}.matched_by[{}]".format(p, j),
                              "must be one of {}, got {!r}".format(", ".join(MATCH_KINDS), kind))
            if kind not in matched_by:
                matched_by.append(kind)
        if not matched_by:
            raise r.fail(p + ".matched_by", "list how this meeting was matched to the account "
                                            "(domain, title, mention, crm or user)")
        meeting = {
            "ids": [mid],
            "id": mid,
            "url": r.url(m, "url", p),
            "urls": [],
            "title": r.text(m, "title", p),
            "start": r.moment(m, "start", p),
            "source": r.text(m, "source", p, required=False) or sources["meeting_notes"]["provider"],
            "attendees": attendees,
            "bots": dropped_bots,
            "attendees_incomplete": r.boolean(m, "attendees_incomplete", p, default=False),
            "matched_by": matched_by,
            "notes_read": r.boolean(m, "notes_read", p, default=True),
            "transcript": r.enum(m, "transcript", p, TRANSCRIPT_STATES, default="not_requested"),
        }
        if mid in seen_ids:
            prior = seen_ids[mid]
            if prior["start"] != meeting["start"] or prior["title"] != meeting["title"]:
                raise r.fail(p + ".meeting_id", "{!r} is listed twice with a different title or start".format(mid))
            exact_dupes += 1
            continue
        seen_ids[mid] = meeting
        meetings.append(meeting)

    excluded = []
    for i, x in enumerate(r.lst(raw.get("excluded_meetings"), "$.excluded_meetings")):
        p = "$.excluded_meetings[{}]".format(i)
        r.obj(x, p)
        excluded.append({"id": r.text(x, "meeting_id", p), "title": r.text(x, "title", p),
                         "start": r.moment(x, "start", p), "reason": r.text(x, "reason", p)})
        if excluded[-1]["id"] in seen_ids:
            raise r.fail(p + ".meeting_id", "{!r} is also in $.meetings; list a meeting in one or the other".format(
                excluded[-1]["id"]))

    stakeholders = []
    for i, s in enumerate(r.lst(raw.get("stakeholders"), "$.stakeholders")):
        p = "$.stakeholders[{}]".format(i)
        r.obj(s, p)
        roles = []
        for j, role in enumerate(r.lst(s.get("roles"), p + ".roles")):
            if role not in ROLES:
                raise r.fail("{}.roles[{}]".format(p, j), "must be one of {}, got {!r}".format(", ".join(ROLES), role))
            if role not in roles:
                roles.append(role)
        cares = []
        for j, c in enumerate(r.lst(s.get("cares_about"), p + ".cares_about")):
            cp = "{}.cares_about[{}]".format(p, j)
            r.obj(c, cp)
            item = {"text": r.text(c, "text", cp)}
            item.update(read_quote(r, c, cp, required=False))
            item["source"] = read_source(r, c, cp)
            cares.append(item)
        email = (r.text(s, "email", p, required=False) or "").lower()
        if email and "@" not in email:
            raise r.fail(p + ".email", "{!r} is not an email address".format(email))
        if in_domains(domain_of(email), internal):
            raise r.fail(p + ".email", "is on one of your internal domains; stakeholders are customer people (the "
                                       "script lists colleagues under 'Our side' from the meeting attendees)")
        stakeholders.append({"name": r.text(s, "name", p), "email": email,
                             "title": r.text(s, "title", p, required=False),
                             "roles": roles or ["unknown"], "cares_about": cares,
                             "note": r.text(s, "note", p, required=False), "listed": True})

    findings = read_findings(r, r.obj(raw.get("findings"), "$.findings", required=False) or {})

    return {"account_name": name, "domains": domains, "aliases": aliases, "sources": sources,
            "search": search_info, "deal": deal, "threads_read": threads_read, "meetings": meetings,
            "exact_dupes": exact_dupes, "excluded": excluded, "stakeholders": stakeholders,
            "findings": findings, "path": str(path), "dropped_links": r.dropped_links}


def read_quote(r: Reader, item: dict, path: str, required: bool) -> dict:
    quote = r.text(item, "quote", path, required=required)
    if quote is None:
        if item.get("quote_from") is not None:
            raise r.fail(path + ".quote_from", "is set but there is no quote")
        return {}
    if len(quote) >= 2 and quote[0] in "\"“" and quote[-1] in "\"”" and not any(c in quote[1:-1] for c in "\"“”"):
        quote = quote[1:-1].strip()  # only a surrounding pair; "Fast" is better than "cheap" stays verbatim
    if not quote:
        raise r.fail(path + ".quote", "is empty once quotation marks are removed")
    return {"quote": quote, "quote_from": r.enum(item, "quote_from", path, QUOTE_FROM),
            "speaker": r.text(item, "speaker", path, required=False)}


def read_source(r: Reader, item: dict, path: str, required: bool = True) -> Optional[dict]:
    src = item.get("source")
    if src is None and not required:
        return None
    r.obj(src, path + ".source")
    sp = path + ".source"
    stype = r.enum(src, "type", sp, SOURCE_TYPES)
    out = {"type": stype, "url": r.url(src, "url", sp), "ref": r.text(src, "ref", sp, required=False)}
    if stype == "meeting":
        out["meeting_id"] = r.text(src, "meeting_id", sp)
        out["date"] = None
    else:
        out["meeting_id"] = None
        out["date"] = r.day(src, "date", sp)
        out["label"] = r.text(src, "label", sp, required=False)
    out["path"] = sp
    return out


def read_findings(r: Reader, f: dict) -> dict:
    out = {}

    def each(key):
        for i, item in enumerate(r.lst(f.get(key), "$.findings." + key)):
            p = "$.findings.{}[{}]".format(key, i)
            r.obj(item, p)
            yield p, item

    out["pains"] = []
    out["triggers"] = []
    for key in ["pains", "triggers"]:
        for p, it in each(key):
            entry = {"text": r.text(it, "text", p)}
            entry.update(read_quote(r, it, p, required=True))
            entry["source"] = read_source(r, it, p)
            out[key].append(entry)

    out["success_criteria"] = []
    for p, it in each("success_criteria"):
        entry = {"goal": r.text(it, "goal", p), "metric": r.text(it, "metric", p, required=False),
                 "target": r.text(it, "target", p, required=False),
                 "measured_by": r.text(it, "measured_by", p, required=False)}
        entry.update(read_quote(r, it, p, required=False))
        entry["source"] = read_source(r, it, p)
        out["success_criteria"].append(entry)

    out["milestones"] = []
    for p, it in each("milestones"):
        entry = {"label": r.text(it, "label", p), "due": r.day(it, "due", p, required=False)}
        entry.update(read_quote(r, it, p, required=False))
        entry["source"] = read_source(r, it, p)
        out["milestones"].append(entry)

    out["promises"] = []
    for p, it in each("promises"):
        flags = r.obj(it.get("flags"), p + ".flags", required=False) or {}
        entry = {"category": r.enum(it, "category", p, PROMISE_CATEGORIES),
                 "topic": r.text(it, "topic", p), "terms": r.text(it, "terms", p),
                 "non_standard": r.boolean(flags, "non_standard", p + ".flags", default=False),
                 "roadmap": r.boolean(flags, "roadmap", p + ".flags", default=False),
                 "in_contract": r.enum(it, "in_contract", p, IN_CONTRACT, default="unknown")}
        if entry["category"] == "roadmap":
            entry["roadmap"] = True
        entry.update(read_quote(r, it, p, required=True))
        entry["source"] = read_source(r, it, p)
        out["promises"].append(entry)

    out["technical_requirements"] = []
    for p, it in each("technical_requirements"):
        entry = {"category": r.enum(it, "category", p, REQUIREMENT_CATEGORIES), "text": r.text(it, "text", p)}
        entry.update(read_quote(r, it, p, required=False))
        entry["source"] = read_source(r, it, p)
        out["technical_requirements"].append(entry)

    out["objections"] = []
    for p, it in each("objections"):
        entry = {"text": r.text(it, "text", p), "status": r.enum(it, "status", p, OBJECTION_STATES),
                 "resolution": r.text(it, "resolution", p, required=False)}
        entry.update(read_quote(r, it, p, required=True))
        entry["source"] = read_source(r, it, p)
        out["objections"].append(entry)

    out["risks"] = []
    for p, it in each("risks"):
        entry = {"text": r.text(it, "text", p)}
        entry.update(read_quote(r, it, p, required=False))
        entry["source"] = read_source(r, it, p)
        out["risks"].append(entry)

    out["competitors"] = []
    for p, it in each("competitors"):
        entry = {"name": r.text(it, "name", p), "status": r.enum(it, "status", p, COMPETITOR_STATES),
                 "context": r.text(it, "context", p, required=False)}
        entry.update(read_quote(r, it, p, required=True))
        entry["source"] = read_source(r, it, p)
        out["competitors"].append(entry)

    out["answered_questions"] = []
    for p, it in each("answered_questions"):
        entry = {"question": r.text(it, "question", p), "answer": r.text(it, "answer", p)}
        entry.update(read_quote(r, it, p, required=False))
        entry["source"] = read_source(r, it, p)
        out["answered_questions"].append(entry)

    out["open_questions"] = []
    for p, it in each("open_questions"):
        entry = {"question": r.text(it, "question", p),
                 "owner": r.enum(it, "owner", p, QUESTION_OWNERS, default="unknown")}
        entry["source"] = read_source(r, it, p, required=False)
        out["open_questions"].append(entry)
    return out


# --------------------------------------------------------------------------- analysis


def classify_attendee(email: str, internal: List[str], account_domains: List[str], consumer: List[str]) -> str:
    dom = domain_of(email)
    if not dom:
        return "unknown"
    if in_domains(dom, internal):
        return "ours"
    if in_domains(dom, account_domains):
        return "account"
    if dom in consumer:
        return "personal"
    return "other"


def same_meeting(a: dict, b: dict) -> bool:
    if a["start"].astimezone(timezone.utc) != b["start"].astimezone(timezone.utc):
        return False
    if fold(a["title"]) == fold(b["title"]):
        return True
    ext_a = {x["email"] for x in a["attendees"] if x["side"] != "ours" and x["email"]}
    ext_b = {x["email"] for x in b["attendees"] if x["side"] != "ours" and x["email"]}
    return bool(ext_a & ext_b)


def merge_meeting(keep: dict, other: dict) -> None:
    keep["ids"].append(other["id"])
    keep["urls"].append(other["url"])
    have = {(x["email"] or fold(x["name"])) for x in keep["attendees"]}
    for x in other["attendees"]:
        key = x["email"] or fold(x["name"])
        if key not in have:
            keep["attendees"].append(x)
            have.add(key)
    keep["bots"] += other["bots"]
    for kind in other["matched_by"]:
        if kind not in keep["matched_by"]:
            keep["matched_by"].append(kind)
    keep["notes_read"] = keep["notes_read"] or other["notes_read"]
    if TRANSCRIPT_RANK[other["transcript"]] < TRANSCRIPT_RANK[keep["transcript"]]:
        keep["transcript"] = other["transcript"]
    keep["attendees_incomplete"] = keep["attendees_incomplete"] and other["attendees_incomplete"]


def analyse(b: dict, config: dict, as_of: datetime, internal: List[str]) -> dict:
    as_of_day = as_of.date()
    consumer = [d.lower() for d in config["consumer_domains"]]
    for m in b["meetings"]:
        for x in m["attendees"]:
            x["side"] = classify_attendee(x["email"], internal, b["domains"], consumer)
    # Colleagues listed by name only, or from a personal address, are ours when the same name appears
    # with an internal address in any meeting (or is the deal owner).
    internal_names = {fold(x["name"]) for m in b["meetings"] for x in m["attendees"]
                      if x["side"] == "ours" and x["name_given"]}
    if b["deal"] and b["deal"]["owner"]:
        internal_names.add(fold(b["deal"]["owner"]))
    for m in b["meetings"]:
        for x in m["attendees"]:
            if x["side"] in ("unknown", "personal") and x["name_given"] and fold(x["name"]) in internal_names:
                x["side"] = "ours"

    # 1. A meeting is a customer meeting when someone from the account's (or another company's)
    #    domain is listed. Every listed attendee ours → internal-only. Only ours plus name-only or
    #    personal-email attendees → unclassified: neither is counted as a customer meeting unless the
    #    agent marked the attendee list incomplete and matched it by title, mention, CRM or user.
    kept, internal_only, unclassified = [], [], []
    for m in b["meetings"]:
        if not m["attendees"]:
            m["attendees_incomplete"] = True  # nobody listed (uploaded transcript, phone call): not internal-only
            kept.append(m)
            continue
        customer = [x for x in m["attendees"] if x["side"] in ("account", "other")]
        unclear = [x for x in m["attendees"] if x["side"] in ("unknown", "personal")]
        vouched = m["attendees_incomplete"] and bool({"title", "mention", "crm", "user"} & set(m["matched_by"]))
        if customer or vouched:
            kept.append(m)
        elif unclear:
            unclassified.append(m)
        else:
            internal_only.append(m)

    # 2. Merge duplicate notes of the same meeting (two teammates captured it). The note that was
    #    read in full and has the best transcript access is kept as primary; ties break on id.
    kept.sort(key=lambda m: (m["start"].astimezone(timezone.utc), not m["notes_read"],
                             TRANSCRIPT_RANK[m["transcript"]], m["id"]))
    merged = []
    duplicates = 0
    for m in kept:
        target = next((x for x in merged if same_meeting(x, m)), None)
        if target is None:
            merged.append(m)
        else:
            merge_meeting(target, m)
            duplicates += 1
    merged.sort(key=lambda m: (m["start"].astimezone(timezone.utc), m["id"]))
    id_map = {}
    for n, m in enumerate(merged, 1):
        m["n"] = n
        m["day"] = m["start"].date()  # the local date as written
        for mid in m["ids"]:
            id_map[mid] = m
    excluded_ids = {}
    for m in internal_only:
        excluded_ids[m["id"]] = "an internal-only meeting"
    for m in unclassified:
        excluded_ids[m["id"]] = ("a meeting with no customer attendee identified (set attendees_incomplete: true "
                                 "if the customer was there)")

    def listed_once(meetings: List[dict]) -> List[dict]:
        """Two teammates' notes of the same excluded meeting are listed once under Coverage."""
        meetings.sort(key=lambda m: (m["start"].astimezone(timezone.utc), m["id"]))
        shown = []
        for m in meetings:
            if not any(x["start"].astimezone(timezone.utc) == m["start"].astimezone(timezone.utc)
                       and fold(x["title"]) == fold(m["title"]) for x in shown):
                shown.append(m)
        return shown
    internal_shown = listed_once(internal_only)
    unclassified_shown = listed_once(unclassified)
    for x in b["excluded"]:
        excluded_ids[x["id"]] = "listed in excluded_meetings ({})".format(x["reason"])

    # 3. Resolve every finding source to its meeting, and check transcript quotes.
    def resolve(src: Optional[dict], quote_from: Optional[str], path: str) -> Optional[dict]:
        if src is None:
            return None
        if src["type"] == "meeting":
            mid = src["meeting_id"]
            if mid in excluded_ids:
                raise InputError("bundle {}: {}.meeting_id: {!r} is {}; findings must come from customer "
                                 "meetings, email or the CRM".format(b["path"], path, mid, excluded_ids[mid]))
            if mid not in id_map:
                raise InputError("bundle {}: {}.meeting_id: unknown meeting {!r}; add it to $.meetings or fix "
                                 "the id".format(b["path"], path, mid))
            m = id_map[mid]
            if quote_from == "transcript" and m["transcript"] != "ok":
                raise InputError("bundle {}: {}: quote_from is transcript but the transcript for meeting {!r} is "
                                 "{}; quote the notes instead (quote_from: notes)".format(
                                     b["path"], path, mid, m["transcript"]))
            src["meeting"] = m
            src["day"] = m["day"]
            src["sort"] = (m["start"].astimezone(timezone.utc).isoformat(), m["n"])
        else:
            if quote_from == "transcript":
                raise InputError("bundle {}: {}: quote_from is transcript but the source is not a meeting".format(
                    b["path"], path))
            src["meeting"] = None
            src["day"] = src["date"]
            # the end of its date in the run's offset, so an email sent after an evening call sorts after it
            src["sort"] = (datetime(src["date"].year, src["date"].month, src["date"].day, 23, 59, 59,
                                    tzinfo=as_of.tzinfo).astimezone(timezone.utc).isoformat(), 0)
        return src

    f = b["findings"]
    for key, items in f.items():
        for item in items:
            if item.get("source") is not None:
                resolve(item["source"], item.get("quote_from"), item["source"]["path"])
    for s in b["stakeholders"]:
        for c in s["cares_about"]:
            if c.get("source") is not None:
                resolve(c["source"], c.get("quote_from"), c["source"]["path"])

    def by_time(items):
        return sorted(items, key=lambda it: it["source"]["sort"] if it.get("source") else ("9999", 0))

    for key in f:
        if key != "open_questions":
            f[key] = by_time(f[key])
    f["milestones"].sort(key=lambda ms: (ms["due"] is None, ms["due"] or date.max))

    # 4. Stakeholders: merge by email, then by folded name; add unlisted customer attendees.
    people = []
    by_email, by_name = {}, {}

    def find(email: str, name: str) -> Optional[dict]:
        if email and email in by_email:
            return by_email[email]
        key = fold(name)
        hit = by_name.get(key)
        if hit is not None and (not email or not hit["email"] or hit["email"] == email):
            return hit
        return None

    def index(p: dict) -> None:
        if p["email"]:
            by_email[p["email"]] = p
        by_name.setdefault(fold(p["name"]), p)

    merged_people = 0
    for s in b["stakeholders"]:
        hit = find(s["email"], s["name"])
        if hit is None:
            people.append(s)
            index(s)
            continue
        merged_people += 1
        if not hit["email"] and s["email"]:
            hit["email"] = s["email"]
        hit["title"] = hit["title"] or s["title"]
        hit["roles"] = [x for x in hit["roles"] if x != "unknown"] + [x for x in s["roles"] if x not in hit["roles"] and x != "unknown"]
        hit["roles"] = hit["roles"] or ["unknown"]
        texts = {fold(c["text"]) for c in hit["cares_about"]}
        hit["cares_about"] += [c for c in s["cares_about"] if fold(c["text"]) not in texts]
        hit["note"] = hit["note"] or s["note"]
        index(hit)

    ours: Dict[str, dict] = {}
    ours_by_email: Dict[str, str] = {}
    for m in merged:
        counted = set()
        for x in m["attendees"]:
            if x["side"] == "ours":
                # By email first, then by name, so a colleague also listed by name only or from a
                # personal address is one person, counted once per meeting.
                key = ours_by_email.get(x["email"]) or fold(x["name"])
                if x["email"]:
                    ours_by_email.setdefault(x["email"], key)
                entry = ours.setdefault(key, {"name": x["name"], "count": 0})
                if x["name_given"]:
                    entry["name"] = entry["name"] if entry.get("named") else x["name"]
                    entry["named"] = True
                if key not in counted:
                    entry["count"] += 1
                    counted.add(key)
                continue
            hit = find(x["email"], x["name"])
            if hit is None:
                hit = {"name": x["name"], "email": x["email"], "title": None, "roles": ["unknown"],
                       "cares_about": [], "note": None, "listed": False, "side": x["side"]}
                people.append(hit)
            elif not hit["email"] and x["email"]:
                hit["email"] = x["email"]
            index(hit)
            hit.setdefault("meetings", [])
            if m not in hit["meetings"]:
                hit["meetings"].append(m)
    total = len(merged)
    last_day = merged[-1]["day"] if merged else None
    for p in people:
        p.setdefault("meetings", [])
        p["meetings"].sort(key=lambda m: m["n"])
        p["flags"] = []
        seen = p["meetings"]
        if not seen:
            p["flags"].append("never")
        elif len(seen) == 1 and total >= config["single_appearance_min_meetings"]:
            p["flags"].append("once")
        elif len(seen) >= 2:
            after = total - seen[-1]["n"]
            gap = (last_day - seen[-1]["day"]).days
            if after >= config["quiet_meetings"] and gap >= config["quiet_days"]:
                p["flags"].append("quiet")
                p["quiet_gap"] = gap
                p["quiet_after"] = after
    role_order = {role: i for i, role in enumerate(ROLES)}
    people.sort(key=lambda p: (min(role_order[x] for x in p["roles"]), -len(p["meetings"]),
                               p["meetings"][0]["n"] if p["meetings"] else 10 ** 6, fold(p["name"])))
    equivalent = {"economic_buyer": {"economic_buyer", "signer"}, "signer": {"signer", "economic_buyer"}}
    missing_roles = [role for role in config["key_roles"]
                     if not any(equivalent.get(role, {role}) & set(p["roles"]) for p in people)]
    signer_absent = [p for p in people if ({"signer", "economic_buyer"} & set(p["roles"])) and not p["meetings"]]

    # 5. Promise conflicts: same topic, different terms.
    groups: Dict[str, List[dict]] = {}
    for pr in f["promises"]:
        groups.setdefault(fold(pr["topic"]), []).append(pr)
    conflicts = []
    for key, items in groups.items():
        terms = []
        for pr in items:
            t = fold(THOUSANDS_RE.sub("", pr["terms"]))  # $1,500 and $1500 are the same terms
            if t not in terms:
                terms.append(t)
        if len(terms) > 1:
            for pr in items:
                pr["conflict"] = True
            conflicts.append({"topic": items[0]["topic"], "items": items})
    conflicts.sort(key=lambda c: c["items"][0]["source"]["sort"])
    for pr in f["promises"]:
        pr.setdefault("conflict", False)

    # 6. Milestones relative to as-of.
    for ms in f["milestones"]:
        if ms["due"] is None:
            ms["state"] = "no date"
        else:
            delta = (ms["due"] - as_of_day).days
            if delta < 0:
                ms["state"] = "passed"
            elif delta <= config["milestone_soon_days"]:
                ms["state"] = "soon"
            else:
                ms["state"] = "later"
            ms["delta"] = delta

    # 7. Coverage gaps.
    gaps = []
    notes_src = b["sources"]["meeting_notes"]
    if notes_src["status"] != "ok":
        gaps.append("Meeting notes ({}) {}{}: meetings could not be read, so this handoff rests on the "
                    "other sources only.".format(provider_label(notes_src["provider"]),
                                                 notes_src["status"].replace("_", " "),
                                                 " ({})".format(notes_src["detail"]) if notes_src["detail"] else ""))
    denied = [m for m in merged if m["transcript"] == "denied"]
    unavailable = [m for m in merged if m["transcript"] == "unavailable"]
    if denied:
        gaps.append("Transcript access denied for {}: quotes from {} come from the notes, not verbatim "
                    "speech. Notes can omit details, so absence there does not mean it was not discussed.".format(
                        plural(len(denied), "meeting"), "it" if len(denied) == 1 else "them"))
    if unavailable:
        gaps.append("No transcript available for {}.".format(plural(len(unavailable), "meeting")))
    unread = [m for m in merged if not m["notes_read"]]
    if unread:
        gaps.append("{} matched but not read: {}.".format(
            plural(len(unread), "meeting"),
            ", ".join("{} ({})".format(m["title"], short_date(m["day"], as_of_day)) for m in unread)))
    if notes_src["provider"] == "granola" and notes_src["plan"] in ("free", "basic"):
        window_start = as_of_day - timedelta(days=config["free_plan_window_days"])
        if b["search"]["from"] < window_start:
            gaps.append("Granola free (Basic) plan: only notes from the last {} days ({} onward) are available "
                        "to this connection, but the sales cycle search started {}. Earlier meetings may be "
                        "missing.".format(config["free_plan_window_days"], short_date(window_start, as_of_day),
                                          short_date(b["search"]["from"], as_of_day)))
    if notes_src["provider"] == "granola" and notes_src["scopes_given"] and "public" not in notes_src["scopes"]:
        gaps.append("Only personal Granola notes were searchable: teammates' notes that are not shared with "
                    "you or in a shared folder are not visible here.")
    deal = b["deal"]
    if deal and deal["created_date"] and merged:
        lead = (merged[0]["day"] - deal["created_date"]).days
        if lead > config["cycle_gap_days"]:
            gaps.append("The CRM deal was created {} but the first meeting found is {} ({} days later). "
                        "Earlier calls may not have been recorded, or were recorded by someone whose notes "
                        "you cannot see.".format(short_date(deal["created_date"], as_of_day),
                                                 short_date(merged[0]["day"], as_of_day), lead))
    for key in ["crm", "email"]:
        src = b["sources"][key]
        if src["status"] in ("unavailable", "denied"):
            gaps.append("{} ({}) {}{}.".format(SOURCE_LABELS[key], provider_label(src["provider"]),
                                               "unreachable" if src["status"] == "unavailable" else "access denied",
                                               ": " + src["detail"] if src["detail"] else ""))
    if b["sources"]["crm"]["status"] == "ok" and deal is None:
        gaps.append("No CRM deal matched this account, so deal terms and the contract were not checked.")
    if b["dropped_links"]:
        gaps.append("{} dropped because {} not an http(s) URL; the text is kept without a link.".format(
            plural(len(b["dropped_links"]), "link"), "it was" if len(b["dropped_links"]) == 1 else "they were"))
    unchecked = [pr for pr in f["promises"] if pr["in_contract"] == "unknown"]

    # 8. Checklist (what a CSM needs on day one).
    present = {
        "deal": deal is not None,
        "products": bool(deal and deal["products"]),
        "pains": bool(f["pains"]),
        "triggers": bool(f["triggers"]),
        "success_criteria": bool(f["success_criteria"]),
        "milestones": bool(f["milestones"]),
        "promises": bool(f["promises"]),
        "champion": any("champion" in p["roles"] for p in people),
        "economic_buyer": any({"economic_buyer", "signer"} & set(p["roles"]) for p in people),
        "technical_owner": any("technical_owner" in p["roles"] for p in people),
        "technical_requirements": bool(f["technical_requirements"]),
        "objections": bool(f["objections"]),
        "competitors": bool(f["competitors"]),
        "meetings": bool(merged),
    }
    checklist = []
    for item in config["checklist"]:
        unknown = [k for k in item["satisfied_by"] if k not in present]
        if unknown:
            raise InputError("config: $.checklist: unknown key(s) {}; use {}".format(
                ", ".join(unknown), ", ".join(sorted(present))))
        checklist.append({"label": item["label"], "ok": any(present[k] for k in item["satisfied_by"])})

    return {"meetings": merged, "internal_only": internal_shown, "unclassified": unclassified_shown,
            "duplicates": duplicates,
            "exact_dupes": b["exact_dupes"], "people": people, "merged_people": merged_people,
            "ours": sorted(ours.values(), key=lambda o: (-o["count"], fold(o["name"]))),
            "missing_roles": missing_roles, "signer_absent": signer_absent, "conflicts": conflicts,
            "unchecked": unchecked, "gaps": gaps, "checklist": checklist,
            "transcripts": {state: sum(1 for m in merged if m["transcript"] == state) for state in TRANSCRIPT_STATES}}


# --------------------------------------------------------------------------- rendering helpers


def cite(src: Optional[dict], as_of: date) -> str:
    if src is None:
        return ""
    m = src.get("meeting")
    if m is not None:
        return link("{}, {}".format(m["title"], short_date(m["day"], as_of)), m["url"])
    label = src.get("label") or {"email": "email", "crm_note": "CRM note", "crm_field": "CRM",
                                 "slack": "Slack", "doc": "document"}[src["type"]]
    return link("{}, {}".format(label, short_date(src["day"], as_of)), src.get("url"))


def quoted(item: dict, as_of: date, speaker_titles: Dict[str, str]) -> str:
    """Render a quote with its attribution. Quotes taken from notes are labelled as notes, not speech."""
    where = cite(item.get("source"), as_of)
    if not item.get("quote"):
        return "({})".format(where) if where else ""
    q = "“{}”".format(item["quote"])
    qf = item["quote_from"]
    if qf == "notes":
        return "notes: {} ({})".format(q, where) if where else "notes: " + q
    who = item.get("speaker")
    if who:
        title = speaker_titles.get(fold(who))
        who = "{} ({})".format(who, title) if title else who
    via = {"transcript": "transcript", "email": "email", "crm": "CRM", "slack": "Slack", "doc": "document"}[qf]
    attribution = ", ".join(x for x in [who, where] if x)
    return "{} — {} · {}".format(q, attribution, via) if attribution else "{} · {}".format(q, via)


def deal_owner(b: dict, a: dict) -> str:
    if b["deal"] and b["deal"]["owner"]:
        return b["deal"]["owner"]
    if a["ours"]:
        return a["ours"][0]["name"]
    return "the account executive"


def role_text(p: dict, config: dict) -> str:
    return ", ".join(config["role_labels"][x] for x in p["roles"])


def person_flag(p: dict, a: dict, as_of: date) -> str:
    if "never" in p["flags"]:
        return "not in any meeting read"
    if "once" in p["flags"]:
        return "appeared once ({})".format(short_date(p["meetings"][0]["day"], as_of))
    if "quiet" in p["flags"]:
        return "went quiet: last seen {}, missed the last {}".format(
            short_date(p["meetings"][-1]["day"], as_of), plural(p["quiet_after"], "meeting"))
    return ""


def heads_up(b: dict, a: dict, config: dict, as_of: date) -> List[str]:
    out = []
    f = b["findings"]
    deal = b["deal"]
    owner = deal_owner(b, a)
    if not a["meetings"]:
        out.append("No sales meetings with {} were found in the notes searched ({} to {}). Everything below "
                   "comes from the CRM and email only; ask {} for the history before kickoff.".format(
                       b["account_name"], short_date(b["search"]["from"], as_of),
                       short_date(b["search"]["to"], as_of), owner))
    elif len(a["meetings"]) < config["thin_meetings"]:
        out.append("Thin record: only {} found. Treat this handoff as partial and walk it through with {}.".format(
            plural(len(a["meetings"]), "sales meeting"), owner))
    if deal is None:
        if b["sources"]["crm"]["status"] == "not_configured":
            out.append("No CRM configured: contract terms were not checked against what sales promised.")
        else:
            out.append("No CRM deal found: contract terms were not checked against what sales promised.")
    elif not deal["closed_won"]:
        out.append("The CRM deal is not marked closed-won (stage: {}). This is a pre-close draft.".format(
            deal["stage"] or "not recorded"))
    for c in a["conflicts"]:
        parts = ["{} ({})".format(pr["terms"], cite(pr["source"], as_of)) for pr in c["items"]]
        out.append("{} **Conflicting terms on {}**: {}. Confirm which is binding with {} before kickoff.".format(
            WARN, c["topic"], " vs ".join(parts), owner))
    not_in = [pr for pr in f["promises"] if pr["in_contract"] == "no"]
    if not_in:
        out.append("{} Promised but **not in the contract**: {}.".format(
            WARN, join_words(["{} ({})".format(pr["topic"], pr["terms"]) for pr in not_in])))
    roadmap = [pr for pr in f["promises"] if pr["roadmap"]]
    if roadmap:
        out.append("Roadmap items sold: {}. Check dates with product before the customer asks.".format(
            join_words([pr["topic"] for pr in roadmap])))
    non_std = [pr for pr in f["promises"] if pr["non_standard"]]
    if non_std:
        out.append("Non-standard terms: {}.".format(join_words([pr["topic"] for pr in non_std])))
    if a["unchecked"]:
        reason = ("the contract was not read" if not (deal and deal["contract_read"])
                  else "the contract does not settle them either way")
        out.append("Not checked against the contract ({}): {}.".format(
            reason, join_words([pr["topic"] for pr in a["unchecked"]])))
    open_obj = [o for o in f["objections"] if o["status"] != "resolved"]
    if open_obj:
        out.append("Unresolved objections: {}.".format(join_words([o["text"] for o in open_obj])))
    for role in a["missing_roles"]:
        out.append("No {} identified in the meetings or CRM.".format(config["role_labels"][role]))
    for p in a["signer_absent"]:
        out.append("{} ({}) is the {} but was not in any meeting read.".format(
            p["name"], p["title"] or "title not recorded", role_text(p, config)))
    quiet = [p for p in a["people"] if "quiet" in p["flags"]]
    if quiet:
        out.append("Went quiet: {}.".format(join_words(["{} ({}, last seen {})".format(
            p["name"], role_text(p, config), short_date(p["meetings"][-1]["day"], as_of)) for p in quiet])))
    once = [p for p in a["people"] if "once" in p["flags"] and p["roles"] != ["unknown"]]
    if once:
        out.append("Appeared once: {}.".format(join_words(["{} ({})".format(p["name"], role_text(p, config))
                                                           for p in once])))
    for ms in f["milestones"]:
        if ms["state"] == "passed":
            out.append("{} Milestone date already passed: {} (was due {}).".format(
                WARN, ms["label"], short_date(ms["due"], as_of)))
        elif ms["state"] == "soon":
            out.append("Milestone due {}: {} ({}).".format(due_in(ms["delta"]), ms["label"],
                                                           short_date(ms["due"], as_of)))
    if a["transcripts"]["denied"]:
        out.append("Transcript access was denied for {}; see Coverage.".format(
            plural(a["transcripts"]["denied"], "meeting")))
    return out


# --------------------------------------------------------------------------- the handoff doc


def render_doc(b: dict, a: dict, config: dict, as_of_dt: datetime, key: str) -> str:
    as_of = as_of_dt.date()
    f = b["findings"]
    deal = b["deal"]
    titles = {fold(p["name"]): p["title"] for p in a["people"] if p.get("title")}
    lines = []
    meetings = a["meetings"]
    span = ("{} – {}".format(short_date(meetings[0]["day"], as_of), short_date(meetings[-1]["day"], as_of))
            if meetings else "no meetings found")
    lines.append("# Sales-to-CS handoff: {}".format(b["account_name"]))
    lines.append("")
    lines.append("_As of {} · {} ({}) · CRM: {} · Internal, for the account team._".format(
        long_date(as_of), plural(len(meetings), "sales meeting"), span, crm_phrase(b)))
    lines.append("")

    lines.append("## Read this first")
    items = heads_up(b, a, config, as_of)
    if items:
        lines.extend("- " + x for x in items)
    else:
        lines.append("- Nothing flagged. Still read the promises section line by line.")
    lines.append("")

    lines.append("## Deal snapshot")
    if deal is None:
        lines.append("No CRM deal {}.".format("configured" if b["sources"]["crm"]["status"] == "not_configured"
                                               else "found for this account"))
    else:
        rows = [("Deal", link(deal["name"] or b["account_name"], deal["url"])), ("Stage", deal["stage"] or "not recorded")]
        if deal["amount"] is not None:
            amount = money(deal["amount"], deal["currency"])  # the currency is already escaped; the $ is not
            rows.append(("Amount", ("\\" + amount if amount.startswith("$") else amount) + PERIODS[deal["amount_period"]]))
        rows.append(("Close date", short_date(deal["close_date"], as_of) if deal["close_date"] else "not recorded"))
        if deal["contract_start"] or deal["term_months"]:
            parts = []
            if deal["contract_start"]:
                parts.append("starts " + short_date(deal["contract_start"], as_of))
            if deal["term_months"]:
                parts.append("{}-month term".format(deal["term_months"]))
            rows.append(("Contract", ", ".join(parts)))
        rows.append(("Products", ", ".join(deal["products"]) if deal["products"] else "not recorded"))
        rows.append(("Owner (AE)", deal["owner"] or "not recorded"))
        rows.append(("Contract read", "yes" if deal["contract_read"] else "no"))
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.extend("| {} | {} |".format(k, v) for k, v in rows)
    lines.append("")

    empty = none_found(b, a)
    lines.append("## Why they bought")
    lines.append("**Pains, in their words**")
    if f["pains"]:
        lines.extend("- **{}**: {}".format(x["text"], quoted(x, as_of, titles)) for x in f["pains"])
    else:
        lines.append("- " + empty)
    lines.append("")
    lines.append("**Trigger event**")
    if f["triggers"]:
        lines.extend("- **{}**: {}".format(x["text"], quoted(x, as_of, titles)) for x in f["triggers"])
    else:
        lines.append("- " + empty)
    lines.append("")

    lines.append("## Success criteria and how they'll measure them")
    if f["success_criteria"]:
        for x in f["success_criteria"]:
            detail = []
            if x["metric"]:
                detail.append("metric: " + x["metric"])
            if x["target"]:
                detail.append("target: " + x["target"])
            if x["measured_by"]:
                detail.append("measured by: " + x["measured_by"])
            text = "- **{}**".format(x["goal"])
            if detail:
                text += " ({})".format("; ".join(detail))
            lines.append(text + ". " + quoted(x, as_of, titles))
    else:
        lines.append("- {} Agree success criteria at kickoff.".format(empty))
    lines.append("")

    lines.append("## Timeline and milestones")
    if f["milestones"]:
        lines.append("| Milestone | Target | Status | Source |")
        lines.append("|---|---|---|---|")
        for ms in f["milestones"]:
            status = {"passed": WARN + " passed", "soon": "due " + due_in(ms.get("delta", 0)),
                      "later": "due " + due_in(ms.get("delta", 0)), "no date": "no date given"}[ms["state"]]
            target = short_date(ms["due"], as_of) if ms["due"] else "—"
            lines.append("| {} | {} | {} | {} |".format(ms["label"], target, status, quoted(ms, as_of, titles)))
    else:
        lines.append("- {} Agree a timeline at kickoff.".format(empty))
    lines.append("")

    lines.extend(render_promises(b, a, config, as_of, titles, heading="##"))

    lines.append("## Stakeholder map")
    if a["people"]:
        for p in a["people"]:
            seen = p["meetings"]
            if seen:
                when = "{} ({} – {})".format(plural(len(seen), "meeting"), short_date(seen[0]["day"], as_of),
                                            short_date(seen[-1]["day"], as_of)) if len(seen) > 1 else \
                    "1 meeting ({})".format(short_date(seen[0]["day"], as_of))
            else:
                when = "0 meetings"
            head = "- **{}**{} — {}. {}.".format(p["name"], ", " + p["title"] if p["title"] else "",
                                                 role_text(p, config), when)
            flag = person_flag(p, a, as_of)
            if flag:
                head += " {} {}.".format(WARN, flag[0].upper() + flag[1:])
            if not p["listed"]:
                head += " Not described in the notes."
                if p.get("side") in ("other", "personal"):
                    head += " Email domain is not the account's."
            lines.append(head)
            if p["cares_about"]:
                cares = []
                for c in p["cares_about"]:
                    sep = ": " if c.get("quote") else " "
                    cares.append(c["text"] + sep + quoted(c, as_of, titles))
                lines.append("  - Cares about: " + "; ".join(cares))
            if p["note"]:
                lines.append("  - Note (CRM or user): " + p["note"])
    else:
        lines.append("- No customer stakeholders identified.")
    if a["ours"]:
        lines.append("- Our side (ask them for context): " + ", ".join(
            "{} ({})".format(o["name"], plural(o["count"], "meeting")) for o in a["ours"]))
    lines.append("")

    lines.append("## Technical requirements, integrations, data and security")
    if f["technical_requirements"]:
        for cat in REQUIREMENT_CATEGORIES:
            group = [x for x in f["technical_requirements"] if x["category"] == cat]
            for x in group:
                lines.append("- **{}**: {} {}".format(config["requirement_category_labels"][cat], x["text"],
                                                      quoted(x, as_of, titles)).rstrip())
    else:
        lines.append("- " + empty)
    lines.append("")

    lines.append("## Objections, risks and competitors")
    open_obj = [o for o in f["objections"] if o["status"] != "resolved"]
    done_obj = [o for o in f["objections"] if o["status"] == "resolved"]
    lines.append("**Unresolved objections**")
    if open_obj:
        for o in open_obj:
            extra = " Partly addressed: {}.".format(o["resolution"]) if o["status"] == "partly_resolved" and o["resolution"] else ""
            lines.append("- **{}**: {}{}".format(o["text"], quoted(o, as_of, titles), extra))
    else:
        lines.append("- " + empty)
    if done_obj:
        lines.append("")
        lines.append("**Resolved during the sale (context)**")
        for o in done_obj:
            lines.append("- **{}**: {}{}".format(o["text"], o["resolution"] + ". " if o["resolution"] else "",
                                                 quoted(o, as_of, titles)))
    lines.append("")
    lines.append("**Risks**")
    if f["risks"]:
        lines.extend("- {} {}".format(x["text"], quoted(x, as_of, titles)).rstrip() for x in f["risks"])
    else:
        lines.append("- " + empty)
    lines.append("")
    lines.append("**Competitors evaluated**")
    if f["competitors"]:
        labels = {"incumbent": "incumbent", "evaluated": "evaluated", "replacing": "being replaced",
                  "chosen_over": "chosen over", "mentioned": "mentioned"}
        for x in f["competitors"]:
            ctx = " ({})".format(x["context"]) if x["context"] else ""
            lines.append("- **{}**, {}{}: {}".format(x["name"], labels[x["status"]], ctx, quoted(x, as_of, titles)))
    else:
        lines.append("- " + empty)
    lines.append("")

    lines.append("## Don't ask them again")
    if f["answered_questions"]:
        for x in f["answered_questions"]:
            lines.append("- **{}** {} {}".format(x["question"], x["answer"], quoted(x, as_of, titles)).rstrip())
    else:
        lines.append("- " + empty)
    lines.append("")
    lines.append("## Open questions")
    if f["open_questions"]:
        owner_label = {"us": "we owe the answer", "customer": "customer to answer", "unknown": "owner unclear"}
        for x in f["open_questions"]:
            src = cite(x["source"], as_of) if x["source"] else ""
            lines.append("- {} ({}{})".format(x["question"], owner_label[x["owner"]], "; " + src if src else ""))
    else:
        lines.append("- None recorded.")
    lines.append("")

    lines.append("## Handoff checklist")
    covered = sum(1 for c in a["checklist"] if c["ok"])
    lines.append("{} of {} covered. ".format(covered, len(a["checklist"])) +
                 ("Fill the gaps at the internal handoff call." if covered < len(a["checklist"]) else ""))
    lines[-1] = lines[-1].rstrip()
    for c in a["checklist"]:
        lines.append("- [{}] {}".format("x" if c["ok"] else " ", c["label"]))
    lines.append("")

    lines.extend(render_coverage(b, a, config, as_of_dt, key))
    lines.append("")
    lines.append(INTERNAL_FOOTER)
    return "\n".join(lines)


def crm_phrase(b: dict) -> str:
    src = b["sources"]["crm"]
    if src["status"] == "not_configured":
        return "not configured"
    if src["status"] != "ok":
        return "{} {}".format(provider_label(src["provider"]), "unreachable" if src["status"] == "unavailable" else "access denied")
    return "{} deal {}".format(provider_label(src["provider"]), "found" if b["deal"] else "not found")


def none_found(b: dict, a: dict) -> str:
    read = []
    if a["meetings"]:
        read.append(plural(len(a["meetings"]), "meeting"))
    if b["sources"]["email"]["status"] == "ok":
        read.append("email" if b["threads_read"] is None else plural(b["threads_read"], "email thread"))
    if b["sources"]["crm"]["status"] == "ok" and b["deal"]:
        read.append("the CRM deal")
    if not read:
        return "Nothing captured: no source could be read."
    return "None captured in the {} read.".format(join_words(read))


def render_promises(b: dict, a: dict, config: dict, as_of: date, titles: Dict[str, str], heading: str) -> List[str]:
    f = b["findings"]
    lines = ["{} What sales promised".format(heading)]
    prs = f["promises"]
    if not prs:
        lines.append("- {} Ask {} directly whether anything was promised outside the contract.".format(
            none_found(b, a), deal_owner(b, a)))
        lines.append("")
        return lines
    counts = [plural(len(prs), "promise")]
    for label, n in [("conflicting", sum(1 for p in prs if p["conflict"])),
                     ("not in contract", sum(1 for p in prs if p["in_contract"] == "no")),
                     ("roadmap", sum(1 for p in prs if p["roadmap"])),
                     ("non-standard", sum(1 for p in prs if p["non_standard"])),
                     ("not checked against the contract", len(a["unchecked"]))]:
        if n:
            counts.append("{} {}".format(n, label))
    lines.append(" · ".join(counts))
    lines.append("")
    for cat in PROMISE_CATEGORIES:
        group = [p for p in prs if p["category"] == cat]
        if not group:
            continue
        lines.append("**{}**".format(config["promise_category_labels"][cat]))
        for p in group:
            flags = []
            if p["conflict"]:
                flags.append(WARN + " conflicting terms")
            if p["in_contract"] == "no":
                flags.append(WARN + " not in contract")
            if p["roadmap"]:
                flags.append("roadmap")
            if p["non_standard"]:
                flags.append("non-standard")
            if p["in_contract"] == "unknown":
                flags.append("contract not checked")
            if p["in_contract"] == "yes":
                flags.append("in contract")
            lines.append("- **{}**: {}. {}".format(p["topic"], p["terms"], "[" + "; ".join(flags) + "]" if flags else "").rstrip())
            lines.append("  - " + quoted(p, as_of, titles))
        lines.append("")
    if a["conflicts"]:
        lines.append("**Conflicts between meetings**")
        for c in a["conflicts"]:
            lines.append("- **{}**".format(c["topic"]))
            for p in c["items"]:
                lines.append("  - {}: {}".format(p["terms"], quoted(p, as_of, titles)))
            lines.append("  - Latest stated: {} ({}). Do not pick one yourself; confirm with {}.".format(
                c["items"][-1]["terms"], cite(c["items"][-1]["source"], as_of), deal_owner(b, a)))
        lines.append("")
    return lines


def render_coverage(b: dict, a: dict, config: dict, as_of_dt: datetime, key: str) -> List[str]:
    as_of = as_of_dt.date()
    lines = ["## Coverage"]
    meetings = a["meetings"]
    notes_total = sum(len(m["ids"]) for m in meetings)
    if meetings:
        lines.append("**Meetings read**: {} from {} ({} – {}).".format(
            plural(len(meetings), "meeting"), plural(notes_total, "note"),
            short_date(meetings[0]["day"], as_of), short_date(meetings[-1]["day"], as_of)))
        lines.append("")
        lines.append("| # | Date | Meeting | Matched by | Notes | Transcript |")
        lines.append("|---|---|---|---|---|---|")
        for m in meetings:
            title = link(m["title"], m["url"])
            extra = [link("copy {}".format(i + 2), u) for i, u in enumerate(m["urls"])]
            if extra:
                title += " (also captured as " + ", ".join(extra) + ")"
            tr = {"ok": "✓", "denied": "denied", "unavailable": "none", "not_requested": "not requested"}[m["transcript"]]
            matched = ", ".join(m["matched_by"])
            if m["attendees_incomplete"]:
                matched += "; attendee list incomplete"
            lines.append("| {} | {} | {} | {} | {} | {} |".format(
                m["n"], short_date(m["day"], as_of), title, matched, "✓" if m["notes_read"] else "not read", tr))
        lines.append("")
    else:
        lines.append("**Meetings read**: none.")
    s = b["search"]
    how = "; ".join(s["methods"]) if s["methods"] else "method not recorded"
    terms = ", ".join(s["terms"] + b["domains"]) if (s["terms"] or b["domains"]) else "none recorded"
    lines.append("- Searched {} to {} ({}). Terms: {}.".format(short_date(s["from"], as_of), short_date(s["to"], as_of), how, terms))
    excl = []
    if a["internal_only"]:
        excl.append("{} ({})".format(plural(len(a["internal_only"]), "internal-only meeting"), "; ".join(
            "{}, {}".format(m["title"], short_date(m["start"].date(), as_of)) for m in a["internal_only"])))
    if a["unclassified"]:
        excl.append("{} with no customer attendee identified, only name-only or personal-email outside "
                    "attendees ({})".format(plural(len(a["unclassified"]), "meeting"), "; ".join(
                        "{}, {}".format(m["title"], short_date(m["start"].date(), as_of)) for m in a["unclassified"])))
    for x in b["excluded"]:
        excl.append("{}, {}: {}".format(x["title"], short_date(x["start"].date(), as_of), x["reason"]))
    lines.append("- Excluded: {}.".format("; ".join(excl) if excl else "none"))
    merged = []
    if a["duplicates"]:
        merged.append(plural(a["duplicates"], "duplicate note") + " of the same meeting")
    if a["exact_dupes"]:
        merged.append(plural(a["exact_dupes"], "repeated meeting id"))
    if a["merged_people"]:
        merged.append(plural(a["merged_people"], "duplicate stakeholder entry", "duplicate stakeholder entries"))
    bots = sum(m["bots"] for m in meetings)
    if bots:
        merged.append(plural(bots, "note-taker bot") + " dropped from attendee lists")
    lines.append("- Merged: {}.".format("; ".join(merged) if merged else "nothing"))
    t = a["transcripts"]
    lines.append("- Transcript access: {} ok, {} denied, {} unavailable, {} not requested.".format(
        t["ok"], t["denied"], t["unavailable"], t["not_requested"]))
    lines.append("- Sources: " + sources_line(b))
    lines.append("- Gaps: " + (" ".join(a["gaps"]) if a["gaps"] else "none found."))
    lines.append("")
    lines.append("Handoff key: {} · as of {}".format(key, as_of_dt.isoformat()))
    return lines


def sources_line(b: dict) -> str:
    parts = []
    for k in SOURCE_KEYS:
        src = b["sources"][k]
        label = SOURCE_LABELS[k]
        if src["status"] == "not_configured":
            parts.append("{}: not configured".format(label))
            continue
        status = {"ok": "✓", "unavailable": "unreachable", "denied": "access denied"}[src["status"]]
        extra = ""
        if k == "meeting_notes" and src["status"] == "ok":
            bits = []
            if src["plan"] != "unknown":
                bits.append("{} plan".format(src["plan"]))
            if src["scopes"]:
                bits.append(" + ".join(src["scopes"]) + " notes")
            extra = " ({})".format(", ".join(bits)) if bits else ""
        if k == "crm" and src["status"] == "ok":
            extra = " ({})".format("deal found" + (", contract read" if b["deal"] and b["deal"]["contract_read"] else "")
                                   if b["deal"] else "no deal found")
        if k == "email" and src["status"] == "ok" and b["threads_read"] is not None:
            extra = " ({})".format(plural(b["threads_read"], "thread"))
        parts.append("{}: {} {}{}".format(label, provider_label(src["provider"]), status, extra))
    return " · ".join(parts)


# --------------------------------------------------------------------------- drafts


def render_promises_only(b: dict, a: dict, config: dict, as_of_dt: datetime, key: str) -> str:
    as_of = as_of_dt.date()
    titles = {fold(p["name"]): p["title"] for p in a["people"] if p.get("title")}
    lines = ["# What sales promised {}".format(b["account_name"]), ""]
    lines.append("_As of {} · from {} · CRM: {}._".format(long_date(as_of), plural(len(a["meetings"]), "sales meeting"),
                                                         crm_phrase(b)))
    lines.append("")
    lines.extend(render_promises(b, a, config, as_of, titles, heading="##"))
    lines.append("Gaps: " + (" ".join(a["gaps"]) if a["gaps"] else "none found."))
    lines.append("Handoff key: {} · as of {}".format(key, as_of_dt.isoformat()))
    return "\n".join(lines)


def promise_line(p: dict) -> str:
    tags = []
    if p["conflict"]:
        tags.append("CONFLICTING")
    if p["in_contract"] == "no":
        tags.append("NOT IN CONTRACT")
    if p["roadmap"]:
        tags.append("ROADMAP")
    if p["non_standard"]:
        tags.append("NON-STANDARD")
    return "{}: {}{}".format(p["topic"], p["terms"], " [" + ", ".join(tags) + "]" if tags else "")


def render_crm_note(b: dict, a: dict, config: dict, as_of_dt: datetime, key: str, doc_url: Optional[str]) -> str:
    as_of = as_of_dt.date()
    f = b["findings"]
    lines = ["DRAFT CRM note: not saved. Show it to the user and write it only after they approve.", ""]
    lines.append("Sales-to-CS handoff: {} (as of {})".format(b["account_name"], long_date(as_of)))
    if f["pains"]:
        lines.append("Why they bought: " + "; ".join(x["text"] for x in f["pains"]) + ".")
    if f["triggers"]:
        lines.append("Trigger: " + "; ".join(x["text"] for x in f["triggers"]) + ".")
    if f["success_criteria"]:
        lines.append("Success criteria: " + "; ".join(
            x["goal"] + (" ({})".format(x["target"]) if x["target"] else "") for x in f["success_criteria"]) + ".")
    if f["milestones"]:
        lines.append("Milestones: " + "; ".join(
            "{} ({})".format(ms["label"], short_date(ms["due"], as_of) if ms["due"] else "no date") for ms in f["milestones"]) + ".")
    if f["promises"]:
        lines.append("Promises ({}):".format(len(f["promises"])))
        lines.extend("- " + promise_line(p) for p in f["promises"])
    else:
        lines.append("Promises: none captured.")
    if a["people"]:
        lines.append("Stakeholders: " + "; ".join(
            "{} ({}){}".format(p["name"], role_text(p, config), ", " + person_flag(p, a, as_of) if person_flag(p, a, as_of) else "")
            for p in a["people"]) + ".")
    open_obj = [o for o in f["objections"] if o["status"] != "resolved"]
    lines.append("Open: {}, {}.".format(plural(len(open_obj), "objection"), plural(len(f["open_questions"]), "question")))
    if a["meetings"]:
        lines.append("Based on {} ({} – {}); transcripts: {} ok, {} denied.".format(
            plural(len(a["meetings"]), "meeting"), short_date(a["meetings"][0]["day"], as_of),
            short_date(a["meetings"][-1]["day"], as_of), a["transcripts"]["ok"], a["transcripts"]["denied"]))
    else:
        lines.append("Based on no meeting notes (none found).")
    lines.append("Full handoff: {}".format(doc_url or DOC_PLACEHOLDER))
    lines.append("Handoff key: {}".format(key))
    return "\n".join(lines)


def render_slack(b: dict, a: dict, config: dict, as_of_dt: datetime, key: str, doc_url: Optional[str],
                 post_to: Optional[str]) -> str:
    as_of = as_of_dt.date()
    f = b["findings"]
    deal = b["deal"]
    lines = ["DRAFT Slack post for {}: not posted. Post only after the user approves.".format(post_to or "(no channel chosen)"), ""]
    head = "*New customer handoff: {}*".format(b["account_name"])
    bits = []
    if deal and deal["close_date"]:
        bits.append("closed {}".format(short_date(deal["close_date"], as_of)) if deal["closed_won"]
                    else "expected close {}".format(short_date(deal["close_date"], as_of)))
    if deal and deal["amount"] is not None:
        bits.append(money(deal["amount"], deal["currency"]) + PERIODS[deal["amount_period"]])
    if deal and deal["owner"]:
        bits.append("AE " + deal["owner"])
    lines.append(head + (" · " + " · ".join(bits) if bits else ""))
    if f["pains"]:
        lines.append("*Why they bought:* " + "; ".join(x["text"] for x in f["pains"][:3]) + ".")
    if f["success_criteria"]:
        lines.append("*Success looks like:* " + "; ".join(
            x["goal"] + (" ({})".format(x["target"]) if x["target"] else "") for x in f["success_criteria"][:3]) + ".")
    watch = []
    for c in a["conflicts"]:
        watch.append("Conflicting terms on {}: {}".format(c["topic"], " vs ".join(p["terms"] for p in c["items"])))
    not_in = [p for p in f["promises"] if p["in_contract"] == "no"]
    if not_in:
        watch.append("Promised, not in contract: " + join_words(["{} ({})".format(p["topic"], p["terms"]) for p in not_in]))
    roadmap = [p for p in f["promises"] if p["roadmap"]]
    if roadmap:
        watch.append("Roadmap sold: " + join_words([p["topic"] for p in roadmap]))
    open_obj = [o for o in f["objections"] if o["status"] != "resolved"]
    if open_obj:
        watch.append("Open objections: " + join_words([o["text"] for o in open_obj]))
    for flag in ["never", "quiet", "once"]:
        for p in a["people"]:
            if flag in p["flags"] and p["roles"] != ["unknown"]:
                watch.append("{} ({}): {}".format(p["name"], role_text(p, config), person_flag(p, a, as_of)))
    if watch:
        lines.append("*Watch-outs:*")
        lines.extend("• " + w for w in watch[:6])
        if len(watch) > 6:
            lines.append("• +{} more in the doc".format(len(watch) - 6))
    else:
        lines.append("*Watch-outs:* none flagged.")
    if a["meetings"]:
        lines.append("_From {} ({} – {}). {}_".format(
            plural(len(a["meetings"]), "sales meeting"), short_date(a["meetings"][0]["day"], as_of),
            short_date(a["meetings"][-1]["day"], as_of),
            "Gaps: see Coverage." if a["gaps"] else "No coverage gaps found."))
    else:
        lines.append("_No sales meetings found in the notes searched._")
    lines.append("Full handoff: {}".format(doc_url or DOC_PLACEHOLDER))
    return "\n".join(lines)


# --------------------------------------------------------------------------- key and log


def slug(text: str, empty: str = "account") -> str:
    return "-".join(fold(text).split()) or empty


def handoff_key(b: dict) -> str:
    deal = b["deal"]
    provider = b["sources"]["crm"]["provider"]
    if deal and deal["record_ref"]:
        ref = deal["record_ref"]
        return ref if ":" in ref else "{}:{}".format(slug(provider or "", "crm"), ref)
    if b["domains"]:
        return "account:" + b["domains"][0]
    name = b["account_name"]
    # A name in another script folds to little or no ASCII (株式会社…, ООО …): add a hash of the whole name, so
    # two such accounts never share a key.
    bare = "".join(c for c in unicodedata.normalize("NFKD", name) if unicodedata.category(c) != "Mn")
    if any(c.isalnum() and not c.isascii() for c in bare):
        digest = hashlib.sha256(" ".join(unicodedata.normalize("NFC", name).casefold().split()).encode("utf-8"))
        return "account:{}-{}".format(slug(name, "name"), digest.hexdigest()[:10])
    return "account:" + slug(name)


def read_log(path: Path) -> List[dict]:
    """Rows of the handoff log, oldest first. A missing log is empty; anything unreadable is an error.

    Every row must parse (an indented row counts; a row that can't be read stops the run), so a handoff
    is never posted twice because its row was skipped, and nothing printed from the log is unchecked.
    """
    kind = path_kind(path)
    if kind == "missing":
        return []
    if kind != "file":
        raise InputError("--log {}: is not a readable file ({})".format(path, kind))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError("--log {}: cannot be read ({})".format(path, exc)) from None
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line.startswith("|"):
            rows.append((n, line))
        elif "|" in line:
            raise InputError("--log {}: line {} looks like a table row but does not start with |; fix it so the "
                             "run can tell which deals were handed off".format(path, n))
    if rows and rows[0][1] == LEGACY_LOG_HEADER:
        columns = [c.strip() for c in LEGACY_LOG_HEADER.strip("|").split("|")]
    elif rows and rows[0][1] == LOG_HEADER:
        columns = LOG_COLUMNS
    else:
        raise InputError("--log {}: not a handoff log (expected the header row {!r}); refusing to run blind".format(
            path, LOG_HEADER))
    entries = []
    for n, row in rows[1:]:
        if re.fullmatch(r"\|(?:\s*:?-+:?\s*\|)+", row):
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) != len(columns):
            raise InputError("--log {}: line {} has {} columns, expected {}".format(path, n, len(cells), len(columns)))
        entry = dict(zip(columns, cells))
        if columns is not LOG_COLUMNS:  # written before the confirm step: those handoffs were delivered
            entry["status"], entry["delivered_at"] = "delivered", entry["written_at"]
        if entry["status"] not in LOG_STATUSES:
            raise InputError("--log {}: line {} has status {!r}, expected pending or delivered".format(
                path, n, entry["status"]))
        checks = [("handoff_key", KEY_RE.fullmatch(entry["handoff_key"])),
                  ("close_date", not entry["close_date"] or DATE_RE.fullmatch(entry["close_date"])),
                  ("written_at", DATETIME_RE.fullmatch(entry["written_at"])),
                  ("delivered_at", DATETIME_RE.fullmatch(entry["delivered_at"]) if entry["status"] == "delivered"
                   else not entry["delivered_at"])]
        checks += [(c, re.fullmatch(r"[0-9]+", entry[c])) for c in ["meetings_read", "promises", "conflicts"]]
        bad = [c for c, good in checks if not good]
        if bad:
            raise InputError("--log {}: line {} has an invalid {} ({!r}); fix the row so the run can trust "
                             "the log".format(path, n, bad[0], entry[bad[0]]))
        entries.append(entry)
    return entries


def check_log_writable(path: Path) -> None:
    """Fail before anything is printed if the log (and its lock file) cannot be written."""
    folder = path.parent
    if path_kind(folder) != "folder":
        raise InputError("--log {}: folder {} does not exist or cannot be read".format(path, folder))
    if not os.access(str(folder), os.W_OK | os.X_OK):
        raise InputError("--log {}: folder {} is not writable".format(path, folder))
    if path_kind(path) not in ("missing", "file"):
        raise InputError("--log {}: is not a file".format(path))
    lock = LogLock(path).path
    if path_kind(lock) not in ("missing", "file"):
        raise InputError("--log {}: the lock path {} exists but is not a file; remove it".format(path, lock))


def write_log(path: Path, entries: List[dict]) -> None:
    """Rewrite the whole log atomically (temp file + rename), in the current format."""
    lines = [LOG_TITLE, "", LOG_HEADER, LOG_SEPARATOR]
    for e in entries:
        lines.append("| " + " | ".join(" ".join(e.get(c, "").replace("|", "/").split()) for c in LOG_COLUMNS) + " |")
    tmp = path.with_name("{}.tmp-{}".format(path.name, os.getpid()))
    try:
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise InputError("--log {}: cannot be written ({}); nothing was printed or recorded".format(
            path, exc.strerror or exc)) from None


class LogLock:
    """Exclusive lock file next to the log (O_CREAT|O_EXCL), so two runs cannot both hand off a deal.

    A lock older than LOCK_STALE_MINUTES, or dated more than LOCK_FUTURE_SECONDS ahead, is left by a crashed
    run (or a clock change) and is removed, but only if it is still the same file (inode) that was judged
    stale. On exit a run removes the lock only if it is the one it created (same inode, same token). These
    age checks are the only place the script reads the clock.
    """

    def __init__(self, log: Path):
        self.path = log.with_name(log.name + ".lock")
        self.token = "{} {}".format(os.getpid(), os.urandom(8).hex())
        self.held = None  # (st_dev, st_ino) of the lock file this run created

    def remove_stale(self, seen: os.stat_result) -> bool:
        """Remove the lock only if it is still the file that was judged stale; True when removed."""
        try:
            now = os.lstat(str(self.path))
        except OSError:
            return False
        if (now.st_dev, now.st_ino, now.st_mtime_ns) != (seen.st_dev, seen.st_ino, seen.st_mtime_ns):
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def __enter__(self):
        for attempt in (1, 2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    seen = os.lstat(str(self.path))
                except FileNotFoundError:
                    continue  # released between the two calls
                except OSError as exc:
                    raise InputError("--log: cannot check the lock file {} ({})".format(
                        self.path, exc.strerror or exc)) from None
                if not stat.S_ISREG(seen.st_mode):
                    raise InputError("--log: the lock path {} exists but is not a file; remove it".format(
                        self.path)) from None
                age = time.time() - seen.st_mtime
                if attempt == 1 and (age > LOCK_STALE_MINUTES * 60 or age < -LOCK_FUTURE_SECONDS):
                    self.remove_stale(seen)
                    continue
                raise InputError("--log: another run holds the lock {} ({} min old). Wait for it to finish; a lock "
                                 "older than {} minutes is treated as stale".format(
                                     self.path, max(0, int(age // 60)), LOCK_STALE_MINUTES), code=EXIT_LOCKED) from None
            except OSError as exc:
                raise InputError("--log: cannot create the lock file {} ({})".format(
                    self.path, exc.strerror or exc)) from None
            try:
                os.write(fd, self.token.encode("ascii"))
                held = os.fstat(fd)
            except OSError as exc:
                try:
                    self.path.unlink()
                except OSError:
                    pass
                raise InputError("--log: cannot write the lock file {} ({})".format(
                    self.path, exc.strerror or exc)) from None
            finally:
                os.close(fd)
            self.held = (held.st_dev, held.st_ino)
            return self
        raise InputError("--log: could not take the lock {}; retry".format(self.path), code=EXIT_LOCKED)

    def __exit__(self, *exc) -> bool:
        try:
            now = os.lstat(str(self.path))
            if (now.st_dev, now.st_ino) == self.held and self.path.read_text(encoding="ascii") == self.token:
                self.path.unlink()
        except (OSError, ValueError):
            pass
        return False


# --------------------------------------------------------------------------- main


def json_ready(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def confirm_delivered(log: Path, key: str, as_of: datetime) -> str:
    """Second phase of a scheduled handoff: the doc was saved (and the post sent), so mark it delivered."""
    check_log_writable(log)
    with LogLock(log):
        entries = read_log(log)
        entry = next((e for e in reversed(entries) if e["handoff_key"] == key), None)
        if entry is None:
            raise InputError("--confirm-delivered: {!r} is not in {}; only a scheduled run records a handoff "
                             "(as pending)".format(key, log))
        if entry["status"] == "delivered":
            return "Already confirmed: {} was marked delivered {}.".format(key, entry["delivered_at"])
        entry["status"], entry["delivered_at"] = "delivered", as_of.isoformat()
        write_log(log, entries)
    return "Confirmed: {} marked delivered.".format(key)


def render(fmt: str, bundle: dict, analysis: dict, config: dict, as_of: datetime, key: str,
           doc_url: Optional[str], post_to: Optional[str]) -> str:
    """Render one format from an escaped copy of the data, so every interpolated value is inert."""
    if fmt in ("doc", "promises"):
        b, a, c = escaped_view([bundle, analysis, config], md_text)
        render_fn = render_doc if fmt == "doc" else render_promises_only
        return render_fn(b, a, c, as_of, "`{}`".format(key))  # KEY_RE characters only: copied exactly
    if fmt == "crm-note":
        b, a, c = escaped_view([bundle, analysis, config], html_text)
        return render_crm_note(b, a, c, as_of, key, doc_url)
    b, a, c = escaped_view([bundle, analysis, config], slack_text)
    return render_slack(b, a, c, as_of, slack_text(key), doc_url.replace("&", "&amp;") if doc_url else None,
                        slack_text(post_to) if post_to else None)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a sales-to-CS handoff from a normalised JSON bundle.")
    parser.add_argument("--bundle", type=Path, help="The JSON bundle. Required except with --confirm-delivered.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--as-of", required=True, help="ISO datetime with UTC offset; the only 'now'.")
    parser.add_argument("--internal-domain", action="append", default=[],
                        help="Your company's email domain(s); repeatable. Required.")
    parser.add_argument("--format", choices=FORMATS, default="doc",
                        help="doc (default), promises, crm-note (draft) or slack (draft).")
    parser.add_argument("--doc-url", help="Link to the saved handoff doc (http or https), used in the drafts.")
    parser.add_argument("--post-to", help="Channel the Slack draft is meant for (shown on the draft only).")
    parser.add_argument("--scheduled", action="store_true",
                        help="Automation run: skip deals that are not closed-won or already delivered, and record "
                             "the printed handoff in --log as pending.")
    parser.add_argument("--log", type=Path, help="Handoff log (markdown). Only scheduled runs and "
                                                 "--confirm-delivered write it.")
    parser.add_argument("--confirm-delivered", metavar="KEY",
                        help="After a scheduled handoff was saved (and posted): mark KEY delivered in --log.")
    parser.add_argument("--resume-pending", action="store_true",
                        help="Scheduled run: print a handoff that is still pending confirmation again.")
    parser.add_argument("--dry-run", action="store_true", help="Scheduled run that never writes the log.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON with the rendered text.")
    args = parser.parse_args(argv)
    for stream, errors in ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        if hasattr(stream, "reconfigure"):  # print UTF-8 even when the locale says ASCII
            stream.reconfigure(encoding="utf-8", errors=errors)

    log_status = None
    try:
        as_of = parse_moment(args.as_of, lambda problem: InputError("--as-of: {}".format(problem)))
        if args.confirm_delivered is not None:
            if not args.log:
                raise InputError("--confirm-delivered needs --log")
            key = args.confirm_delivered.strip()
            if not KEY_RE.fullmatch(key):
                raise InputError("--confirm-delivered: {!r} is not a handoff key (letters, digits and : / _ . -, as "
                                 "printed on the handoff's 'Handoff key' line)".format(key))
            return emit(confirm_delivered(args.log, key, as_of), None)
        if args.bundle is None:
            raise InputError("--bundle is required")
        internal = sorted({clean_domain(d) for d in args.internal_domain if d.strip()})
        for d in internal:
            if not DOMAIN_RE.fullmatch(d):
                raise InputError("--internal-domain: {!r} is not an email domain like corvalto.example; pass one "
                                 "domain per --internal-domain".format(d))
        if not internal:
            raise InputError("--internal-domain: at least one internal domain is required, otherwise "
                             "internal-only meetings cannot be recognised and excluded")
        if args.scheduled and not args.log:
            raise InputError("--scheduled needs --log, which records each handoff so it is not posted twice")
        if args.resume_pending and not args.scheduled:
            raise InputError("--resume-pending only applies to --scheduled runs")
        doc_url = None
        if args.doc_url is not None:
            doc_url = safe_url(" ".join(args.doc_url.split()))
            if doc_url is None:
                raise InputError("--doc-url: {!r} is not an http(s) URL".format(args.doc_url))
        config = load_config(args.config)
        bundle = load_bundle(args.bundle, internal, config)
        key = handoff_key(bundle)
        writes = args.scheduled and not args.dry_run
        if writes:
            check_log_writable(args.log)
        with LogLock(args.log) if writes else nullcontext():
            entries = read_log(args.log) if args.log else []
            entry = next((e for e in reversed(entries) if e["handoff_key"] == key), None)
            log_status = entry["status"] if entry else None
            text = None
            if args.scheduled:
                deal = bundle["deal"]
                if deal is None or not deal["closed_won"]:
                    text = "Skipped: {} is not a closed-won deal; scheduled runs only hand off closed-won deals.".format(key)
                elif not deal["record_ref"]:
                    raise InputError("bundle {}: $.deal.record_ref is required for --scheduled runs: the log is keyed "
                                     "on the CRM deal, so every run must name the same deal".format(args.bundle))
                elif log_status == "delivered":
                    text = "Skipped: already handed off {} (delivered {}).".format(key, entry["delivered_at"])
                elif log_status == "pending" and not args.resume_pending:
                    raise InputError(
                        "{} is pending since {}: a scheduled run printed this handoff but its delivery was never "
                        "confirmed. If the doc was saved (and the post sent), run --confirm-delivered {}; if not, "
                        "rerun with --resume-pending to print it again.".format(key, entry["written_at"], key),
                        code=EXIT_PENDING)
                elif bundle["sources"]["meeting_notes"]["status"] != "ok":
                    raise InputError(
                        "sources.meeting_notes is {}: a scheduled run does not hand off a deal whose meetings could "
                        "not be read. Nothing was recorded; the next run retries, or a person can ask for the "
                        "handoff on request.".format(bundle["sources"]["meeting_notes"]["status"]),
                        code=EXIT_SOURCE_NOT_OK)
            if text is not None:
                if args.json:
                    text = json.dumps({"status": "skipped", "handoff_key": key, "reason": text[len("Skipped: "):]},
                                      ensure_ascii=False, indent=2)
                skipped = True
            else:
                skipped = False
                analysis = analyse(bundle, config, as_of, internal)
                text = render(args.format, bundle, analysis, config, as_of, key, doc_url, args.post_to)
                if writes and entry is None:
                    deal = bundle["deal"]
                    entries.append({"handoff_key": key, "account": md_text(bundle["account_name"].replace("|", "/")),
                                    "close_date": deal["close_date"].isoformat() if deal["close_date"] else "",
                                    "status": "pending", "written_at": as_of.isoformat(), "delivered_at": "",
                                    "meetings_read": str(len(analysis["meetings"])),
                                    "promises": str(len(bundle["findings"]["promises"])),
                                    "conflicts": str(len(analysis["conflicts"]))})
                    write_log(args.log, entries)  # recorded before anything is printed
                    log_status = "pending"
        if not skipped and args.json:
            jb, ja = escaped_view([bundle, analysis], redact)
            f = jb["findings"]
            payload = {
                "status": "written",
                "format": args.format,
                "handoff_key": key,
                "as_of": as_of.isoformat(),
                "account": jb["account_name"],
                "meetings": [{"n": m["n"], "date": m["day"], "title": m["title"], "url": m["url"],
                              "note_ids": m["ids"], "transcript": m["transcript"]} for m in ja["meetings"]],
                "excluded_internal_only": len(ja["internal_only"]),
                "excluded_unclassified": len(ja["unclassified"]),
                "duplicate_notes_merged": ja["duplicates"],
                "promises": len(f["promises"]),
                "conflicts": [{"topic": c["topic"], "terms": [p["terms"] for p in c["items"]]} for c in ja["conflicts"]],
                "stakeholder_flags": [{"name": p["name"], "flags": p["flags"]} for p in ja["people"] if p["flags"]],
                "missing_roles": ja["missing_roles"],
                "gaps": ja["gaps"],
                "dropped_links": jb["dropped_links"],
                "log_status": log_status,
                "markdown": text,
            }
            text = json.dumps(json_ready(payload), ensure_ascii=False, indent=2)
    except InputError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return exc.code
    return emit(text, log_status if args.scheduled and not args.dry_run and not skipped else None)


def emit(text: str, log_status: Optional[str]) -> int:
    """Print the result. If stdout is gone (closed pipe, full disk), say so on stderr and exit 6, no traceback."""
    try:
        if sys.stdout is None:
            raise OSError("stdout is closed")
        print(text)
        sys.stdout.flush()
        return 0
    except OSError as exc:
        try:  # send whatever is still buffered to /dev/null, so the exit flush can't fail again
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, AttributeError, ValueError):
            pass
        hint = (" The handoff is recorded as pending and was not delivered: rerun with --resume-pending to print "
                "it again." if log_status == "pending" else "")
        print("error: the output could not be written ({}).{}".format(exc.strerror or exc, hint), file=sys.stderr)
        return EXIT_OUTPUT


if __name__ == "__main__":
    sys.exit(main())
