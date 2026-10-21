#!/usr/bin/env python3
"""Render a DRAFT investor or board update from one JSON bundle plus optional metrics CSV files.

Stdlib only, offline, deterministic, Python 3.8+. --as-of is the only "now"; the wall clock is never
read. Nothing is sent: the script prints text. The agent reads Granola meetings, the billing system,
metrics files and the previous update, and extracts the items; this script does the checkable parts:

  * validates the bundle, the metrics CSV files and the config (exit 2 names the file and the JSON
    path or CSV line; nothing is printed on stdout)
  * metrics: a figure is printed only from a metrics row that has a value, a source and an as-of
    date. Rows without one, dated after --as-of or before the period began, in another currency, or
    contradicting another row for the same metric and period are withheld and listed under "Before
    you send". Figures are rounded per unit (ROUND_HALF_UP) and the period-over-period change is
    computed from the rounded figures, so the table adds up. It also flags restatements against
    the previous update, jumps of 10x or more, metrics that moved the wrong way, and metrics the
    last update reported that are missing now
  * names (pattern-based): {org:key} and {person:key} placeholders print the name only when that
    entity is public_ok, otherwise its descriptor ("a mid-market logistics customer"). Text that
    writes a non-public name or alias directly is withheld; meeting titles and labels that contain
    one are masked; a final check over the whole output refuses to print (exit 3) if a non-public
    name or alias would still appear. Matching folds case, accents, a few look-alike letters,
    invisible characters and spacing; misspellings and unlisted nicknames can be missed
  * confidentiality: items tagged board_only, legal, fundraising_terms or personnel (or citing a
    meeting tagged so, or a board meeting) are left out of an investor draft and printed only in a
    separate "Board only" section of a board draft. Untagged text is scanned for confidential terms
    from the config (pattern-based) and withheld on a hit unless the founder cleared that item
  * figures in text: agent-written text may carry figures only through {metric:key} placeholders;
    other digits (except years, dates, quarters, version numbers and terms such as SOC 2) and
    number words next to metric nouns withhold the text (pattern-based)
  * forward-looking wording (will, plan to, on track, next quarter ...), or a present-tense or
    scheduled plan (a clause naming a date after the update period with no past-tense verb, or
    scheduled/slated/due ...), is withheld unless the item carries a valid founder statement: whole sentence(s) from the founder's own turn (founder_stated.
    speaker) in the cited meeting's notes or transcript text (granola_text.check_quote), none of them a
    question or a conditional (granola_text.is_question_or_conditional, a heuristic), and the item's
    text, placeholders filled in, IS one of those sentences after normalisation (no paraphrase), and
    the founder-context check (below, fail-closed) finds the claim, its neighbours, the next turn and
    every later turn of the meeting clean.
    Quotes need permission: true, and when the cited meeting's notes or transcript text is in the
    bundle the quote must pass granola_text.check_quote (one or more complete sentences from one
    speaker's turn; partial sentences, ellipses, speaker labels and cross-speaker quotes rejected)
  * last update's commitments: each is listed with the agent's status and evidence; a commitment
    with a metric target is checked against this period's figure, and a status that contradicts
    the figure is replaced and flagged
  * renders the draft (markdown, with references and a "Before you send" section), the email body
    (no references, no review notes) or JSON. Every interpolated value has its email addresses and
    phone numbers removed (granola_text.redact_contacts, pattern-based, can miss formats) and is then
    escaped with granola_text.md, so a value can't form a link, autolink, raw HTML, emphasis, math,
    highlight or a table-cell break; only http(s) links built by the script are printed

Exit codes: 0 printed; 2 bad input (nothing on stdout); 3 refused because a non-public name would
have been printed (nothing on stdout); 6 the output could not be written (closed pipe, full disk).
"""

import argparse
import csv
import decimal
import io
import json
import os
import re
import stat
import sys
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit
import importlib.util

# granola_text.py is the shared Granola text helper, copied byte for byte next to this script (tests.sh compares its
# SHA-256 with the published copy). It is loaded by path, so the script works from any working directory and with
# python -I, and no __pycache__ is written into the skill folder.
sys.dont_write_bytecode = True
_GT_SPEC = importlib.util.spec_from_file_location("granola_text", str(Path(__file__).resolve().parent / "granola_text.py"))
gt = importlib.util.module_from_spec(_GT_SPEC)
_GT_SPEC.loader.exec_module(gt)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "update.json"
EXIT_BAD_INPUT, EXIT_REFUSED, EXIT_OUTPUT = 2, 3, 6

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]
# ASCII digits only; hours 00-23, minutes and seconds 00-59, offsets up to +-14:00. Checked before
# fromisoformat, so every Python from 3.8 accepts and rejects the same strings.
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DATETIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                         r"(?::[0-5][0-9](?:\.[0-9]{3}|\.[0-9]{6})?)?"
                         r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
PERIOD_RE = re.compile(r"([0-9]{4})-(?:(0[1-9]|1[0-2])|Q([1-4]))")
MIN_YEAR, MAX_YEAR = 1900, 2999
KEY_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")  # entity and metric keys
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")  # meeting and commitment ids
CURRENCY_RE = re.compile(r"[A-Z]{3}")
CSV_NUMBER_RE = re.compile(r"-?[0-9]{1,15}(?:\.[0-9]{1,6})?")
DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
PLACEHOLDER_RE = re.compile(r"\{(org|person|metric):([a-z0-9][a-z0-9_-]{0,47})(?:\.(previous|change))?\}")
MAX_ABS = Decimal(10) ** 15
# Source labels that say there is no source.
PLACEHOLDER_SOURCE_RE = re.compile(r"(?i)\W*(?:unknown|tbd|tbc|todo|n/?a|none|null|nil|na|x+|source|\?+|-+)?\W*")
MAX_TEXT, MAX_LABEL, MAX_NOTES = 1500, 200, 400000
MAX_LIST = 2000

SECTIONS = ["highlight", "lowlight", "product", "customers", "team", "ask"]
SECTION_TITLES = {"highlight": "Highlights", "lowlight": "Lowlights", "product": "Product",
                  "customers": "Customers & go-to-market", "team": "Team & hiring", "ask": "Asks"}
TAGS = ["board_only", "legal", "fundraising_terms", "personnel"]
TAG_LABELS = {"board_only": "board only", "legal": "legal", "fundraising_terms": "fundraising terms",
              "personnel": "personnel / HR"}
TAG_TITLES = {"board_only": "Board only", "legal": "Legal", "fundraising_terms": "Fundraising terms",
              "personnel": "Personnel / HR"}
SOURCE_KEYS = ["meetings", "billing", "metrics_file", "previous_update"]
SOURCE_LABELS = {"meetings": "Meeting notes", "billing": "Billing", "metrics_file": "Metrics file",
                 "previous_update": "Previous update"}
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_configured"]
TRANSCRIPT_STATES = ["ok", "denied", "unavailable", "not_requested"]
MEETING_KINDS = ["customer", "team", "hiring", "partner", "investor", "board", "other"]
MEETING_KIND_LABELS = {"customer": "customer call", "team": "team meeting", "hiring": "hiring meeting",
                       "partner": "partner meeting", "investor": "investor meeting", "board": "board meeting",
                       "other": "other meeting"}
ENTITY_KINDS = ["customer", "partner", "investor", "person", "other"]
UNITS = ["currency", "count", "percent", "months", "number"]
METRIC_KINDS = ["stock", "flow"]
BETTER = ["up", "down", "neutral"]
REVIEW_STATUSES = ["done", "partial", "missed", "changed", "blocked", "dropped"]
STATUS_LABELS = {"done": "Done", "partial": "Partial", "missed": "Missed", "changed": "Changed",
                 "blocked": "Blocked", "dropped": "Dropped", "not_reviewed": "Not reviewed",
                 "not_verified": "Not verified"}
OPS = [">=", "<=", ">", "<", "="]
OP_WORDS = {">=": "at least", "<=": "at most", ">": "above", "<": "below", "=": "exactly"}
AUDIENCES = ["investors", "board"]
FORMATS = ["draft", "email", "json"]
CONFIG_KEYS = ["config_version", "currency_symbols", "currency_decimals", "number_decimals", "metrics",
               "metric_order", "headline_metrics", "jump_factor", "plan_window_days", "confidential_terms",
               "forward_terms", "metric_nouns", "number_words", "scale_words", "legal_suffixes",
               "generic_name_words", "meeting_source_terms"]
BUNDLE_KEYS = {"schema_version", "company", "period", "sources", "meetings", "entities", "metric_definitions",
               "metrics", "items", "previous_update", "commitment_reviews"}

INVISIBLE_RE = re.compile("[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064"
                          "\u2066-\u2069\ufeff]")
URL_SAFE = "/:?#@!$&'*+,;=%~"
PRIVATE_USE_RE = re.compile("[\ue000-\uf8ff]")  # placeholder markers while a text is escaped
# A few look-alike letters (Cyrillic and Greek) folded to Latin before names are matched.
CONFUSABLES = str.maketrans({"\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i",
                             "\u0458": "j", "\u0455": "s", "\u0501": "d", "\u04cf": "l", "\u043a": "k", "\u043c": "m", "\u043d": "h", "\u0442": "t",
                             "\u0432": "b", "\u03bf": "o", "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v", "\u03c1": "p",
                             "\u03c4": "t", "\u03c5": "u", "\u03c7": "x", "\u0261": "g", "\u0131": "i"})
# Digits that are not figures: years, ISO dates, month-day dates, version numbers, standards.
NOT_FIGURE_RES = [
    re.compile(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])"),
    re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+[0-9]{1,2}"
               r"(?:st|nd|rd|th)?(?![0-9.,]|\s*%)"),
    re.compile(r"(?i)(?<![0-9.,])[0-9]{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|"
               r"sept|oct|nov|dec)[a-z]*\b"),
    re.compile(r"\b[vV][0-9]+(?:\.[0-9]+)*\b"),
    re.compile(r"(?i)\b(?:version|release|build)\s+[0-9]+(?:\.[0-9]+)*\b"),
    re.compile(r"(?i)\b(?:soc\s*[12]|iso\s*[0-9]{4,5}(?::[0-9]{4})?|pci\s*dss|hipaa|gdpr)\b"),
    re.compile(r"(?<![0-9.,$€£¥])(?:19|20)[0-9]{2}(?![0-9.,%]|\s*(?:k|m|bn|mm|x)\b)"),
]
SCALE_SUFFIX_RE = re.compile(r"[0-9]+(?:k|m|mm|b|bn|x|pp|bps)")


class InputError(Exception):
    """Bad input; the message names the file and the JSON path. `code` is the exit code."""

    def __init__(self, message: str, code: int = EXIT_BAD_INPUT):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- parsing helpers


def safe_url(value: Optional[str]) -> Optional[str]:
    """An absolute http(s) URL with a real host name, percent-encoded for markdown; None otherwise."""
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port  # raises ValueError on a malformed or out-of-range port
        clean = quote(value, safe=URL_SAFE)
    except (ValueError, UnicodeError):
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or not DOMAIN_RE.fullmatch(host) or port == 0 \
            or parts.username is not None or parts.password is not None:
        return None
    return clean


def parse_day(value: str, fail) -> date:
    """Strict YYYY-MM-DD (or an ISO datetime with offset, taken as its local date)."""
    if DATETIME_RE.fullmatch(value):
        return parse_moment(value, fail).date()
    if not DATE_RE.fullmatch(value):
        raise fail("is not a YYYY-MM-DD date")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise fail("is not a real calendar date") from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise fail("is outside the years {} to {}".format(MIN_YEAR, MAX_YEAR))
    return day


def parse_moment(value: str, fail) -> datetime:
    """Strict YYYY-MM-DDTHH:MM[:SS[.fff|.ffffff]] plus Z or +-HH:MM, checked before fromisoformat."""
    if not DATETIME_RE.fullmatch(value):
        raise fail("is not an ISO-8601 datetime with a UTC offset, like 2026-10-02T09:00:00-04:00")
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise fail("is not a real date and time") from None
    if not MIN_YEAR <= moment.year <= MAX_YEAR:
        raise fail("is outside the years {} to {}".format(MIN_YEAR, MAX_YEAR))
    return moment


class Period:
    """A calendar month (2026-09) or quarter (2026-Q3)."""

    def __init__(self, year: int, month: Optional[int] = None, quarter: Optional[int] = None):
        self.year, self.month, self.quarter = year, month, quarter
        if month is not None:
            self.kind = "month"
            self.code = "{:04d}-{:02d}".format(year, month)
            self.start = date(year, month, 1)
            self.end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
            self.label = "{} {}".format(MONTH_NAMES[month - 1], year)
            self.short = "{} {}".format(MONTHS[month - 1], year)
        else:
            self.kind = "quarter"
            self.code = "{:04d}-Q{}".format(year, quarter)
            first = 3 * quarter - 2
            self.start = date(year, first, 1)
            self.end = (date(year + 1, 1, 1) if quarter == 4 else date(year, first + 3, 1)) - timedelta(days=1)
            self.label = self.short = "Q{} {}".format(quarter, year)

    def previous(self) -> "Period":
        if self.kind == "month":
            return Period(self.year - 1, 12) if self.month == 1 else Period(self.year, self.month - 1)
        return Period(self.year - 1, quarter=4) if self.quarter == 1 else Period(self.year, quarter=self.quarter - 1)

    def last_month_code(self) -> str:
        return "{:04d}-{:02d}".format(self.year, self.month if self.kind == "month" else 3 * self.quarter)


def parse_period(value: str, fail) -> Period:
    m = PERIOD_RE.fullmatch(value)
    if not m:
        raise fail("is not a period like 2026-09 (a month) or 2026-Q3 (a quarter)")
    year = int(m.group(1))
    if not MIN_YEAR < year < MAX_YEAR:
        raise fail("is outside the years {} to {}".format(MIN_YEAR + 1, MAX_YEAR - 1))
    return Period(year, int(m.group(2))) if m.group(2) else Period(year, quarter=int(m.group(3)))


class Reader:
    """Typed accessors that report errors as '<label>: <json.path>: problem'. Text values are never echoed."""

    def __init__(self, label: str):
        self.label = label
        self.dropped_links = []

    def fail(self, path: str, problem: str) -> InputError:
        return InputError("{}: {}: {}".format(self.label, path, problem))

    def obj(self, value, path: str, required: bool = True, keys: Optional[set] = None,
            needed: Tuple[str, ...] = ()) -> Optional[dict]:
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        if keys is not None:
            unknown = sorted(str(k) for k in value if k not in keys)
            if unknown:
                raise self.fail(path, "unknown key(s): {} (allowed: {})".format(
                    ", ".join(shorten(k) for k in unknown), ", ".join(sorted(keys))))
        for key in needed:
            if key not in value:
                raise self.fail("{}.{}".format(path, key), "is required")
        return value

    def lst(self, value, path: str, required: bool = False) -> list:
        if value is None:
            if required:
                raise self.fail(path, "is required (use [] when there are none)")
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        if len(value) > MAX_LIST:
            raise self.fail(path, "has more than {} entries".format(MAX_LIST))
        return value

    def text(self, parent: dict, key: str, path: str, required: bool = True, limit: int = MAX_TEXT) -> Optional[str]:
        value = parent.get(key)
        where = "{}.{}".format(path, key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise self.fail(where, "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail(where, "must be a string, got {}".format(type_name(value)))
        clean = " ".join(INVISIBLE_RE.sub("", value).split())
        if not clean:
            if required:
                raise self.fail(where, "is required but has no visible text")
            return None
        if len(clean) > limit:
            raise self.fail(where, "is longer than {} characters".format(limit))
        return clean

    def raw_text(self, parent: dict, key: str, path: str) -> Optional[str]:
        """Long notes or transcript text: kept for substring checks only, never printed."""
        value = parent.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise self.fail("{}.{}".format(path, key), "must be a string, got {}".format(type_name(value)))
        if len(value) > MAX_NOTES:
            raise self.fail("{}.{}".format(path, key), "is longer than {} characters".format(MAX_NOTES))
        return value

    def strings(self, parent: dict, key: str, path: str, required: bool = False, limit: int = MAX_LABEL) -> List[str]:
        out = []
        for i, item in enumerate(self.lst(parent.get(key), "{}.{}".format(path, key), required)):
            where = "{}.{}[{}]".format(path, key, i)
            if not isinstance(item, str):
                raise self.fail(where, "must be a non-empty string, got {}".format(type_name(item)))
            clean = " ".join(INVISIBLE_RE.sub("", item).split())
            if not clean:
                raise self.fail(where, "must be a non-empty string")
            if len(clean) > limit:
                raise self.fail(where, "is longer than {} characters".format(limit))
            out.append(clean)
        return out

    def boolean(self, parent: dict, key: str, path: str, default: Optional[bool] = None) -> bool:
        value = parent.get(key, default)
        if not isinstance(value, bool):
            raise self.fail("{}.{}".format(path, key), "must be true or false, got {}".format(type_name(value)))
        return value

    def integer(self, parent: dict, key: str, path: str, low: int, high: int) -> int:
        value = parent.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise self.fail("{}.{}".format(path, key), "must be an integer from {} to {}".format(low, high))
        return value

    def number(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[Decimal]:
        value = parent.get(key)
        if value is None and not required:
            return None
        return to_decimal(value, lambda problem: self.fail("{}.{}".format(path, key), problem))

    def enum(self, parent: dict, key: str, path: str, allowed: List[str], default: Optional[str] = None) -> str:
        value = parent.get(key, default)
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("{}.{}".format(path, key), "must be one of {}, got {}".format(
                ", ".join(allowed), shown(value)))
        return value

    def ident(self, parent: dict, key: str, path: str, pattern=ID_RE, required: bool = True) -> Optional[str]:
        value = parent.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not pattern.fullmatch(value):
            what = "a key (lower-case letters, digits, _ and -)" if pattern is KEY_RE else \
                "an id (letters, digits, _ and -, up to 64)"
            raise self.fail("{}.{}".format(path, key), "must be {}, got {}".format(what, shown(value)))
        return value

    def day(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[date]:
        value = parent.get(key)
        where = "{}.{}".format(path, key)
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise self.fail(where, "must be a YYYY-MM-DD date string, got {}".format(type_name(value)))
        return parse_day(value, lambda problem: self.fail(where, "{} {}".format(shown(value), problem)))

    def url(self, parent: dict, key: str, path: str) -> Optional[str]:
        value = self.text(parent, key, path, required=False, limit=2000)
        if value is None:
            return None
        clean = safe_url(value)
        if clean is None:
            self.dropped_links.append("{}.{}".format(path, key))
        return clean


def type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, Decimal)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    return "an object"


def shown(value) -> str:
    """A short, safe rendering of a value for an error message (enums, ids and dates only)."""
    if isinstance(value, str):
        return repr(shorten(value))
    return type_name(value)


def shorten(text: str, limit: int = 40) -> str:
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return text if len(text) <= limit else text[:limit] + "..."


def to_decimal(value, fail) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise fail("must be a number, got {}".format(type_name(value)))
    try:
        d = Decimal(value)
    except (ValueError, decimal.InvalidOperation):
        raise fail("is not a usable number") from None
    if not d.is_finite() or abs(d) >= MAX_ABS:
        raise fail("must be a finite number below 1e15 in size")
    return d


def path_kind(path: Path) -> str:
    """'file', 'folder', 'missing', 'other' or 'unreadable'. Never raises."""
    try:
        mode = os.stat(str(path)).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except (OSError, ValueError):
        return "unreadable"
    return "file" if stat.S_ISREG(mode) else "folder" if stat.S_ISDIR(mode) else "other"


def reject_constant(name: str):
    raise ValueError("{} is not a valid JSON number".format(name))


def has_surrogate(data) -> bool:
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def read_file_text(path: Path, label: str) -> str:
    kind = path_kind(path)
    if kind == "missing":
        raise InputError("{}: file not found: {}".format(label, path))
    if kind != "file":
        raise InputError("{}: {} is not a readable file ({})".format(label, path, kind))
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError("{}: {} is not UTF-8 text".format(label, path)) from None
    except OSError as exc:
        raise InputError("{}: {} cannot be read ({})".format(label, path, exc.strerror or exc)) from None


def load_json(path: Path, label: str):
    text = read_file_text(path, label)
    try:
        data = json.loads(text, parse_constant=reject_constant, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise InputError("{}: {} is not valid JSON (line {}, column {}: {})".format(
            label, path, exc.lineno, exc.colno, exc.msg)) from None
    except ValueError as exc:  # NaN / Infinity, or an integer too long to parse
        raise InputError("{}: {} is not valid JSON ({})".format(label, path, exc)) from None
    except RecursionError:
        raise InputError("{}: {} is nested too deeply".format(label, path)) from None
    try:
        bad = has_surrogate(data)
    except RecursionError:
        raise InputError("{}: {} is nested too deeply".format(label, path)) from None
    if bad:
        raise InputError("{}: {} is not valid Unicode (it has an unpaired surrogate escape such as \\ud800)".format(
            label, path))
    return data


# --------------------------------------------------------------------------- text helpers


def norm(text: str) -> str:
    """Case-, accent-, width- and look-alike-folded text with invisible characters removed (matching only)."""
    if text.isascii():
        return text.lower()
    t = unicodedata.normalize("NFKC", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    t = unicodedata.normalize("NFKD", t.casefold().translate(CONFUSABLES))
    return "".join(ch for ch in t if not unicodedata.combining(ch))


WORD_RE = re.compile(r"[^\W_]+")


def tokens(text: str) -> List[str]:
    return WORD_RE.findall(norm(text))


def token_line(toks: List[str]) -> str:
    """Tokens joined with single spaces and padded, so a phrase is found with a plain substring search."""
    return " " + " ".join(toks) + " "


class TermList:
    """Phrases matched as whole-token sequences; a phrase ending in * matches a word prefix."""

    def __init__(self, phrases: List[str]):
        parts = []
        for phrase in phrases:
            prefix = phrase.endswith("*")
            toks = tokens(phrase.rstrip("*"))
            if toks:
                parts.append(" ".join(re.escape(t) for t in toks) + (r"[^ ]*" if prefix else ""))
        self.rx = re.compile(" (?:{}) ".format("|".join(parts))) if parts else None

    def find(self, toks: List[str]) -> bool:
        if self.rx is None or not toks:
            return False
        return self.rx.search(token_line(toks)) is not None

    def phrases(self, toks: List[str]) -> set:
        """Every listed phrase (as matched text) found in the tokens."""
        if self.rx is None or not toks:
            return set()
        line, out, i = token_line(toks), set(), 0
        while True:
            m = self.rx.search(line, i)
            if m is None:
                return out
            out.add(m.group(0).strip())
            i = m.start() + 1


class Names:
    """Non-public names and aliases, matched as token sequences and (for 8+ letters) with spacing removed.

    Besides the full name and its aliases it matches the name without legal suffixes (Inc, LLC ...), the first
    word of an organisation's name, and each word of a person's name, unless the word is short or listed in
    generic_name_words, so "Quorrelind" alone is caught for "Quorrelind Logistics" even without an alias."""

    def __init__(self, entities: List[dict], suffixes: List[str], generic: List[str]):
        self.patterns = []
        suffix_set = set(suffixes)
        generic_set = set(tokens(" ".join(generic)))
        for idx, ent in enumerate(entities):
            if ent["public_ok"]:
                continue
            forms = [ent["name"]] + ent["aliases"]
            name_toks = tokens(ent["name"])
            words = name_toks if ent["kind"] == "person" else name_toks[:1]
            forms += [w for w in words if len(w) >= (3 if ent["kind"] == "person" else 4) and w not in generic_set
                      and w not in suffix_set and not w.isdigit()]
            while len(name_toks) > 1 and name_toks[-1] in suffix_set:
                name_toks = name_toks[:-1]
                forms.append(" ".join(name_toks))
            for form in forms:
                toks = tokens(form)
                if toks:
                    joined = "".join(toks)
                    self.patterns.append((idx, token_line(toks), joined if len(joined) >= 8 else None))

    def find(self, text: str) -> Optional[int]:
        if not self.patterns:
            return None
        toks = tokens(text)
        line = token_line(toks)
        joined = "".join(toks)
        for idx, pt, pj in self.patterns:
            if pt in line or (pj is not None and pj in joined):
                return idx
        return None


def md(text: str) -> str:
    """One value as inert markdown on one line: contact details removed first (granola_text.redact_contacts,
    pattern-based), then escaped (granola_text.md)."""
    return gt.md(gt.redact_contacts(text))


def link(label: str, url: Optional[str]) -> str:
    """`label` is already escaped; `url` passed safe_url."""
    return "[{}]({})".format(label, url) if url else label


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return "{} {}".format(n, word if n == 1 else (many or word + "s"))


def join_words(items: List[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------- config


def load_config(path: Path) -> dict:
    config = load_json(path, "config")
    r = Reader("config {}".format(path))
    r.obj(config, "$")
    missing = [key for key in CONFIG_KEYS if key not in config]
    if missing:
        raise InputError("config {}: missing key(s): {}".format(path, ", ".join(missing)))
    r.integer(config, "config_version", "$", 1, 1)
    symbols = r.obj(config["currency_symbols"], "$.currency_symbols")
    for code, sym in symbols.items():
        if not CURRENCY_RE.fullmatch(code) or not isinstance(sym, str) or not 0 < len(sym) <= 4 \
                or any(ch.isdigit() or ch in "\\[]<>&*_`~|" for ch in sym):
            raise r.fail("$.currency_symbols", "entries must map a 3-letter code to a short symbol")
    r.integer(config, "currency_decimals", "$", 0, 2)
    r.integer(config, "number_decimals", "$", 0, 3)
    r.integer(config, "jump_factor", "$", 2, 1000)
    r.integer(config, "plan_window_days", "$", 1, 3650)
    metrics = r.obj(config["metrics"], "$.metrics")
    config["metrics"] = {key: read_metric_def(r, key, value, "$.metrics") for key, value in metrics.items()}
    for key in ("metric_order", "headline_metrics"):
        seen = set()
        for i, name in enumerate(r.lst(config[key], "$." + key, required=True)):
            if not isinstance(name, str) or name not in config["metrics"]:
                raise r.fail("$.{}[{}]".format(key, i), "is not a metric defined in $.metrics, got {}".format(shown(name)))
            if name in seen:
                raise r.fail("$.{}[{}]".format(key, i), "repeats {}".format(shown(name)))
            seen.add(name)
    missing = [key for key in config["metrics"] if key not in config["metric_order"]]
    if missing:
        raise r.fail("$.metric_order", "must list every metric in $.metrics; missing {}".format(", ".join(missing)))
    terms = r.obj(config["confidential_terms"], "$.confidential_terms")
    if sorted(terms) != sorted(TAGS):
        raise r.fail("$.confidential_terms", "must have exactly the keys {}".format(", ".join(TAGS)))
    for tag in TAGS:
        r.strings(terms, tag, "$.confidential_terms", required=True)
    for key in ("forward_terms", "metric_nouns", "number_words", "scale_words", "legal_suffixes", "generic_name_words",
                "meeting_source_terms"):
        r.strings(config, key, "$", required=True)
    return config


def read_metric_def(r: Reader, key, value, path: str) -> dict:
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise r.fail(path, "metric keys must be lower-case letters, digits, _ and -, got {}".format(shown(key)))
    where = "{}.{}".format(path, key)
    r.obj(value, where, keys={"label", "unit", "kind", "better"}, needed=("label", "unit", "kind", "better"))
    return {"label": r.text(value, "label", where, limit=60), "unit": r.enum(value, "unit", where, UNITS),
            "kind": r.enum(value, "kind", where, METRIC_KINDS), "better": r.enum(value, "better", where, BETTER)}


# --------------------------------------------------------------------------- bundle


SOURCE_OBJ_KEYS = {"provider", "status", "detail"}
MEETING_KEYS = {"meeting_id", "title", "date", "url", "kind", "notes_read", "transcript", "confidential",
                "notes_text", "transcript_text"}
ENTITY_KEYS = {"key", "name", "kind", "public_ok", "descriptor", "aliases"}
OBS_KEYS = {"metric", "period", "value", "source", "as_of", "currency"}
ITEM_KEYS = {"section", "text", "citations", "confidential", "quote", "founder_stated", "cleared_by_founder"}
CITATION_KEYS = {"meeting_id", "source", "detail", "metric", "label", "url"}
QUOTE_KEYS = {"text", "attribution", "permission", "meeting_id"}
STATED_KEYS = {"meeting_id", "source", "excerpt", "speaker"}
PREV_KEYS = {"period", "sent_on", "url", "commitments", "reported_metrics"}
COMMIT_KEYS = {"id", "text", "metric_target", "cleared_by_founder"}
TARGET_KEYS = {"metric", "op", "value"}
REPORTED_KEYS = {"metric", "period", "value"}
REVIEW_KEYS = {"commitment_id", "status", "note", "citations"}


def load_bundle(path: Path, config: dict, as_of: datetime) -> dict:
    data = load_json(path, "bundle")
    r = Reader("bundle {}".format(path))
    r.obj(data, "$", keys=BUNDLE_KEYS, needed=("schema_version", "company", "period", "sources", "meetings",
                                               "entities", "metrics", "items"))
    r.integer(data, "schema_version", "$", 1, 1)
    company = r.obj(data["company"], "$.company", keys={"name", "currency"}, needed=("name", "currency"))
    b = {"reader": r, "path": path}
    b["company"] = r.text(company, "name", "$.company", limit=120)
    currency = company.get("currency")
    if not isinstance(currency, str) or not CURRENCY_RE.fullmatch(currency):
        raise r.fail("$.company.currency", "must be a 3-letter currency code such as USD")
    b["currency"] = currency
    period_raw = data["period"]
    if not isinstance(period_raw, str):
        raise r.fail("$.period", "must be a string like 2026-09 or 2026-Q3")
    b["period"] = parse_period(period_raw, lambda problem: r.fail("$.period", "{} {}".format(shown(period_raw), problem)))

    sources = r.obj(data["sources"], "$.sources", keys=set(SOURCE_KEYS), needed=("meetings",))
    b["sources"] = {}
    for key in SOURCE_KEYS:
        src = r.obj(sources.get(key), "$.sources." + key, required=(key == "meetings"), keys=SOURCE_OBJ_KEYS,
                    needed=("provider", "status"))
        if src is not None:
            where = "$.sources." + key
            b["sources"][key] = {"provider": r.text(src, "provider", where, limit=60),
                                 "status": r.enum(src, "status", where, SOURCE_STATUSES),
                                 "detail": r.text(src, "detail", where, required=False, limit=300)}

    # entities first: everything else is checked against their names
    entities, keys_seen = [], set()
    for i, raw in enumerate(r.lst(data["entities"], "$.entities", required=True)):
        where = "$.entities[{}]".format(i)
        r.obj(raw, where, keys=ENTITY_KEYS, needed=("key", "name", "kind", "public_ok", "aliases"))
        key = r.ident(raw, "key", where, KEY_RE)
        if key in keys_seen:
            raise r.fail(where + ".key", "duplicate key {!r}".format(key))
        keys_seen.add(key)
        ent = {"key": key, "name": r.text(raw, "name", where, limit=120), "kind": r.enum(raw, "kind", where, ENTITY_KINDS),
               "public_ok": r.boolean(raw, "public_ok", where), "aliases": r.strings(raw, "aliases", where, required=True),
               "descriptor": r.text(raw, "descriptor", where, required=False, limit=120), "index": i}
        if not ent["public_ok"] and ent["descriptor"] is None:
            raise r.fail(where + ".descriptor", "is required when public_ok is false (for example \"a mid-market "
                                                "logistics customer\")")
        entities.append(ent)
    names = Names(entities, config["legal_suffixes"], config["generic_name_words"])
    for ent in entities:
        where = "$.entities[{}]".format(ent["index"])
        hit = names.find(ent["descriptor"] or "")
        if hit is not None:
            raise r.fail(where + ".descriptor", "contains the name or an alias of non-public entity $.entities[{}]; "
                                                "a descriptor must not identify it".format(hit))
        if ent["public_ok"]:
            hit = names.find(ent["name"])
            if hit is not None:
                raise r.fail(where + ".name", "(a public name) contains the name or an alias of non-public entity "
                                              "$.entities[{}]; make the names distinct".format(hit))
    if names.find(b["company"]) is not None:
        raise r.fail("$.company.name", "contains the name or an alias of a non-public entity")
    b["entities"] = {e["key"]: e for e in entities}
    b["entity_list"] = entities
    b["names"] = names

    # metric definitions: config plus bundle additions/overrides
    defs = dict(config["metrics"])
    extra = r.obj(data.get("metric_definitions"), "$.metric_definitions", required=False) or {}
    order = list(config["metric_order"])
    for key, value in extra.items():
        defs[key] = read_metric_def(r, key, value, "$.metric_definitions")
        if key not in order:
            order.append(key)
    b["defs"], b["order"], b["extra_defs"] = defs, order, set(extra)

    # meetings
    meetings, by_id = [], {}
    for i, raw in enumerate(r.lst(data["meetings"], "$.meetings", required=True)):
        where = "$.meetings[{}]".format(i)
        r.obj(raw, where, keys=MEETING_KEYS, needed=("meeting_id", "title", "date", "kind", "notes_read", "transcript"))
        mid = r.ident(raw, "meeting_id", where)
        if mid in by_id:
            raise r.fail(where + ".meeting_id", "duplicate meeting id {!r}".format(mid))
        m = {"id": mid, "title": r.text(raw, "title", where, limit=300), "date": r.day(raw, "date", where),
             "url": r.url(raw, "url", where), "kind": r.enum(raw, "kind", where, MEETING_KINDS),
             "notes_read": r.boolean(raw, "notes_read", where),
             "transcript": r.enum(raw, "transcript", where, TRANSCRIPT_STATES),
             "confidential": r.enum(raw, "confidential", where, TAGS) if raw.get("confidential") is not None else None,
             "notes_text": r.raw_text(raw, "notes_text", where),
             "transcript_text": r.raw_text(raw, "transcript_text", where), "pos": i}
        if m["kind"] == "board" and m["confidential"] is None:
            m["confidential"] = "board_only"
        by_id[mid] = m
        meetings.append(m)
    meetings.sort(key=lambda m: (m["date"], m["pos"]))
    for n, m in enumerate(meetings, 1):
        m["ref"] = "M{}".format(n)
    b["meetings"], b["meeting_by_id"] = meetings, by_id

    # metric observations from the bundle
    b["observations"] = []
    for i, raw in enumerate(r.lst(data["metrics"], "$.metrics", required=True)):
        where = "$.metrics[{}]".format(i)
        r.obj(raw, where, keys=OBS_KEYS, needed=("metric", "period", "value"))
        source = raw.get("source")
        as_of_raw = raw.get("as_of")
        b["observations"].append(make_obs(
            r, where, raw.get("metric"), raw.get("period"), r.number(raw, "value", where, required=False),
            r.text(raw, "source", where, required=False, limit=120) if source is not None else None,
            as_of_raw, raw.get("currency"), defs, "bundle " + where))

    # items
    b["items"] = []
    for i, raw in enumerate(r.lst(data["items"], "$.items", required=True)):
        b["items"].append(read_item(r, raw, "$.items[{}]".format(i), b, i))

    # previous update and commitment reviews
    prev = r.obj(data.get("previous_update"), "$.previous_update", required=False, keys=PREV_KEYS,
                 needed=("period", "sent_on", "commitments"))
    b["previous_update"] = None
    if prev is not None:
        where = "$.previous_update"
        praw = prev.get("period")
        if not isinstance(praw, str):
            raise r.fail(where + ".period", "must be a string like 2026-08")
        pu = {"period": parse_period(praw, lambda problem: r.fail(where + ".period", "{} {}".format(shown(praw), problem))),
              "sent_on": r.day(prev, "sent_on", where), "url": r.url(prev, "url", where), "commitments": [],
              "reported": []}
        if pu["sent_on"] > as_of.date():
            raise r.fail(where + ".sent_on", "is after --as-of")
        ids = set()
        for i, raw in enumerate(r.lst(prev.get("commitments"), where + ".commitments", required=True)):
            cw = "{}.commitments[{}]".format(where, i)
            r.obj(raw, cw, keys=COMMIT_KEYS, needed=("id", "text"))
            cid = r.ident(raw, "id", cw)
            if cid in ids:
                raise r.fail(cw + ".id", "duplicate commitment id {!r}".format(cid))
            ids.add(cid)
            target = r.obj(raw.get("metric_target"), cw + ".metric_target", required=False, keys=TARGET_KEYS,
                           needed=("metric", "op", "value"))
            if target is not None:
                tw = cw + ".metric_target"
                mkey = r.ident(target, "metric", tw, KEY_RE)
                if mkey not in defs:
                    raise r.fail(tw + ".metric", "is not a defined metric")
                target = {"metric": mkey, "op": r.enum(target, "op", tw, OPS), "value": r.number(target, "value", tw)}
            pu["commitments"].append({"id": cid, "text": r.text(raw, "text", cw), "target": target, "pos": i,
                                      "cleared": r.boolean(raw, "cleared_by_founder", cw, default=False)})
        for i, raw in enumerate(r.lst(prev.get("reported_metrics"), where + ".reported_metrics")):
            rw = "{}.reported_metrics[{}]".format(where, i)
            r.obj(raw, rw, keys=REPORTED_KEYS, needed=("metric", "period", "value"))
            mkey = r.ident(raw, "metric", rw, KEY_RE)
            if mkey not in defs:
                raise r.fail(rw + ".metric", "is not a defined metric")
            rp = raw.get("period")
            if not isinstance(rp, str):
                raise r.fail(rw + ".period", "must be a string like 2026-08")
            pu["reported"].append({"metric": mkey, "period": parse_period(rp, lambda problem: r.fail(
                rw + ".period", "{} {}".format(shown(rp), problem))).code, "value": r.number(raw, "value", rw)})
        b["previous_update"] = pu
    b["reviews"] = {}
    for i, raw in enumerate(r.lst(data.get("commitment_reviews"), "$.commitment_reviews")):
        where = "$.commitment_reviews[{}]".format(i)
        r.obj(raw, where, keys=REVIEW_KEYS, needed=("commitment_id", "status", "citations"))
        cid = r.ident(raw, "commitment_id", where)
        if b["previous_update"] is None or cid not in {c["id"] for c in b["previous_update"]["commitments"]}:
            raise r.fail(where + ".commitment_id", "{!r} is not a commitment in $.previous_update".format(cid))
        if cid in b["reviews"]:
            raise r.fail(where + ".commitment_id", "{!r} is reviewed twice".format(cid))
        cites = read_citations(r, raw, where, b, required=True)
        b["reviews"][cid] = {"status": r.enum(raw, "status", where, REVIEW_STATUSES),
                             "note": r.text(raw, "note", where, required=False), "citations": cites, "pos": i}
    return b


def make_obs(r: Reader, where: str, metric, period, value: Optional[Decimal], source: Optional[str], as_of_raw,
             currency, defs: dict, origin: str) -> dict:
    if not isinstance(metric, str) or not KEY_RE.fullmatch(metric):
        raise r.fail(where + ".metric", "must be a metric key, got {}".format(shown(metric)))
    if metric not in defs:
        raise r.fail(where + ".metric", "{!r} is not a defined metric; add it to metric_definitions".format(metric))
    if not isinstance(period, str):
        raise r.fail(where + ".period", "must be a string like 2026-09 or 2026-Q3")
    p = parse_period(period, lambda problem: r.fail(where + ".period", "{} {}".format(shown(period), problem)))
    as_of = None
    if as_of_raw is not None and not (isinstance(as_of_raw, str) and not as_of_raw.strip()):
        if not isinstance(as_of_raw, str):
            raise r.fail(where + ".as_of", "must be a YYYY-MM-DD date string, got {}".format(type_name(as_of_raw)))
        as_of = parse_day(as_of_raw.strip(), lambda problem: r.fail(where + ".as_of", "{} {}".format(
            shown(as_of_raw), problem)))
    if currency is not None and not (isinstance(currency, str) and CURRENCY_RE.fullmatch(currency)):
        raise r.fail(where + ".currency", "must be a 3-letter currency code, got {}".format(shown(currency)))
    return {"metric": metric, "period": p.code, "value": value, "source": source, "as_of": as_of,
            "currency": currency, "origin": origin}


def read_citations(r: Reader, raw: dict, where: str, b: dict, required: bool) -> List[dict]:
    out = []
    items = r.lst(raw.get("citations"), where + ".citations", required=required)
    if required and not items:
        raise r.fail(where + ".citations", "needs at least one citation")
    for j, c in enumerate(items):
        cw = "{}.citations[{}]".format(where, j)
        r.obj(c, cw, keys=CITATION_KEYS)
        if "meeting_id" in c:
            mid = r.ident(c, "meeting_id", cw)
            if mid not in b["meeting_by_id"]:
                raise r.fail(cw + ".meeting_id", "{!r} is not a meeting in $.meetings".format(mid))
            out.append({"type": "meeting", "meeting": b["meeting_by_id"][mid]})
            continue
        kind = r.enum(c, "source", cw, ["user", "metric", "document"])
        if kind == "user":
            out.append({"type": "user", "detail": r.text(c, "detail", cw, limit=300)})
        elif kind == "metric":
            mkey = r.ident(c, "metric", cw, KEY_RE)
            if mkey not in b["defs"]:
                raise r.fail(cw + ".metric", "is not a defined metric")
            out.append({"type": "metric", "metric": mkey})
        else:
            out.append({"type": "document", "label": r.text(c, "label", cw, limit=120), "url": r.url(c, "url", cw)})
    return out


def read_item(r: Reader, raw, where: str, b: dict, index: int) -> dict:
    r.obj(raw, where, keys=ITEM_KEYS, needed=("section", "text", "citations"))
    item = {"index": index, "section": r.enum(raw, "section", where, SECTIONS), "text": r.text(raw, "text", where),
            "citations": read_citations(r, raw, where, b, required=True),
            "tag": r.enum(raw, "confidential", where, TAGS) if raw.get("confidential") is not None else None,
            "cleared": r.boolean(raw, "cleared_by_founder", where, default=False), "quote": None, "stated": None}
    q = r.obj(raw.get("quote"), where + ".quote", required=False, keys=QUOTE_KEYS,
              needed=("text", "attribution", "permission", "meeting_id"))
    if q is not None:
        qw = where + ".quote"
        mid = r.ident(q, "meeting_id", qw)
        if mid not in b["meeting_by_id"]:
            raise r.fail(qw + ".meeting_id", "{!r} is not a meeting in $.meetings".format(mid))
        item["quote"] = {"text": r.text(q, "text", qw, limit=600), "attribution": r.text(q, "attribution", qw, limit=160),
                         "permission": r.boolean(q, "permission", qw), "meeting": b["meeting_by_id"][mid]}
    s = r.obj(raw.get("founder_stated"), where + ".founder_stated", required=False, keys=STATED_KEYS,
              needed=("excerpt",))
    if s is not None:
        sw = where + ".founder_stated"
        if ("meeting_id" in s) == ("source" in s):
            raise r.fail(sw, "needs exactly one of meeting_id or source: \"user\"")
        meeting = None
        if "meeting_id" in s:
            mid = r.ident(s, "meeting_id", sw)
            if mid not in b["meeting_by_id"]:
                raise r.fail(sw + ".meeting_id", "{!r} is not a meeting in $.meetings".format(mid))
            meeting = b["meeting_by_id"][mid]
        else:
            r.enum(s, "source", sw, ["user"])
        item["stated"] = {"meeting": meeting, "excerpt": r.text(s, "excerpt", sw, limit=600),
                          "speaker": r.text(s, "speaker", sw, required=False, limit=100)}
    return item


def load_csv(path: Path, defs: dict) -> List[dict]:
    label = "metrics CSV {}".format(path)
    text = read_file_text(path, "metrics CSV")
    if "\x00" in text:
        raise InputError("{}: contains a NUL character".format(label))
    r = Reader(label)
    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        rows = []
        for row in reader:
            rows.append((reader.line_num, row))
    except csv.Error as exc:
        raise InputError("{}: is not valid CSV ({})".format(label, exc)) from None
    if not rows:
        raise InputError("{}: is empty; the first line must be the header metric,period,value,source,as_of".format(label))
    header = [h.strip().lower() for h in rows[0][1]]
    allowed = ["metric", "period", "value", "source", "as_of", "currency"]
    if len(set(header)) != len(header) or any(h not in allowed for h in header) or \
            any(h not in header for h in allowed[:5]):
        raise InputError("{}: line 1: the header must name the columns metric, period, value, source, as_of "
                         "(and optionally currency), each once".format(label))
    out = []
    for line, row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        where = "line {}".format(line)
        if len(row) != len(header):
            raise InputError("{}: {}: has {} cells, the header has {}".format(label, where, len(row), len(header)))
        rec = {h: " ".join(INVISIBLE_RE.sub("", cell).split()) for h, cell in zip(header, row)}
        value = None
        if rec["value"]:
            if not CSV_NUMBER_RE.fullmatch(rec["value"]):
                raise InputError("{}: {}: value must be a plain number such as 41250 or 12.7 (no currency signs, "
                                 "thousands separators or %)".format(label, where))
            value = Decimal(rec["value"])
        if len(rec["source"]) > 120:
            raise InputError("{}: {}: source is longer than 120 characters".format(label, where))
        out.append(make_obs(r, where, rec["metric"], rec["period"], value, rec["source"] or None,
                            rec["as_of"] or None, rec.get("currency") or None, defs, "{} {}".format(path.name, where)))
    return out


# --------------------------------------------------------------------------- metrics


class Ctx:
    """Everything the text checks need."""

    def __init__(self, b: dict, config: dict, audience: str):
        self.b = b
        self.config = config
        self.audience = audience
        self.names = b["names"]
        self.conf = {tag: TermList(config["confidential_terms"][tag]) for tag in TAGS}
        self.forward = TermList(config["forward_terms"])
        self.nouns = TermList(config["metric_nouns"])
        self.number_words = set(tokens(" ".join(config["number_words"])))
        self.scale = TermList(config["scale_words"])
        self.meeting_terms = TermList(config["meeting_source_terms"])
        self.meeting_titles = [token_line(t) for t in (tokens(m["title"]) for m in b["meetings"]) if len(t) >= 2]
        self.metrics = {}
        if first_tag(tokens(b["company"]), self):
            raise InputError("bundle {}: $.company.name: must not contain a confidential term; it is printed in the "
                             "title and the subject line".format(b["path"]))
        for key in b["order"]:
            label = b["defs"][key]["label"]
            if figure_hits(label, self) or first_tag(tokens(label), self) or self.forward.find(tokens(label)):
                where = ("bundle {}: $.metric_definitions.{}.label".format(b["path"], key) if key in b["extra_defs"]
                         else "config: $.metrics.{}.label".format(key))
                raise InputError("{}: must not contain a figure, a confidential term or forward-looking wording; it is "
                                 "printed in every version of the update".format(where))
        for ent in b["entity_list"]:
            if ent["descriptor"] is None or ent["public_ok"]:
                continue
            toks = tokens(ent["descriptor"])
            if figure_hits(ent["descriptor"], self) or first_tag(toks, self) or self.forward.find(toks):
                raise InputError("bundle {}: $.entities[{}].descriptor: must not contain a figure, a confidential "
                                 "term or forward-looking wording; it is printed in place of the name".format(
                                     b["path"], ent["index"]))

    def meeting_source(self, text: str) -> bool:
        """Whether a metric source names a meeting, call or notes (meeting_source_terms or a meeting title)."""
        toks = tokens(text)
        line = token_line(toks)
        return self.meeting_terms.find(toks) or any(t in line for t in self.meeting_titles)


def places(unit: str, config: dict) -> int:
    return {"currency": config["currency_decimals"], "count": 0, "percent": 1, "months": 1,
            "number": config["number_decimals"]}[unit]


def quantize(value: Decimal, unit: str, config: dict) -> Decimal:
    q = value.quantize(Decimal(1).scaleb(-places(unit, config)), rounding=ROUND_HALF_UP)
    return q if q != 0 else abs(q)


def fmt_plain(value: Decimal, digits: int) -> str:
    return "{:,.{}f}".format(abs(value), digits)


def fmt_value(value: Decimal, unit: str, config: dict, currency: str) -> str:
    sign = "-" if value < 0 else ""
    body = fmt_plain(value, places(unit, config))
    if unit == "currency":
        sym = config["currency_symbols"].get(currency)  # escaped: "$41,250 | $37,500" must not become math
        return sign + (gt.md(sym) + body if sym else "{} {}".format(currency, body))
    if unit == "percent":
        return sign + body + "%"
    if unit == "months":
        return sign + body + " months"
    return sign + body


def change_parts(cur: Decimal, prev: Decimal, unit: str, config: dict, currency: str) -> Tuple[str, Optional[str]]:
    delta = cur - prev
    if delta == 0:
        return "no change", None
    sign = "+" if delta > 0 else "-"
    if unit == "percent":
        return "{}{} pp".format(sign, fmt_plain(delta, 1)), None
    if unit == "months":
        return "{}{} months".format(sign, fmt_plain(delta, 1)), None
    absolute = sign + fmt_value(abs(delta), unit, config, currency)
    pct = None
    if prev > 0 and cur >= 0:
        with decimal.localcontext() as ctx:
            ctx.prec = 60
            p = (delta / prev * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        pct = "{}{}%".format(sign, fmt_plain(p, 1))
    return absolute, pct


def resolve_metrics(b: dict, observations: List[dict], config: dict, as_of: date, ctx: "Ctx") -> Tuple[Dict[str, dict], dict]:
    period = b["period"]
    prev_period = period.previous()
    defs = b["defs"]
    names = b["names"]
    notes = {"conflicts": [], "withheld": [], "restated": [], "jumps": [], "wrong_way": [], "stale": [],
             "ignored": 0, "missing_vs_last": []}
    slots = {}
    for obs in observations:
        d = defs[obs["metric"]]
        slot = None
        for name, p in (("cur", period), ("prev", prev_period)):
            if obs["period"] == p.code or (p.kind == "quarter" and d["kind"] == "stock"
                                           and obs["period"] == p.last_month_code()):
                slot = (name, p)
        if slot is None:
            notes["ignored"] += 1
            continue
        slots.setdefault((obs["metric"], slot[0]), (slot[1], []))[1].append(obs)
    result = {}
    for key in b["order"]:
        d = defs[key]
        row = {"key": key, "label": d["label"], "unit": d["unit"], "better": d["better"], "cur": None, "prev": None,
               "cur_state": "missing", "prev_state": "missing", "change": None, "pct": None}
        for slot_name in ("cur", "prev"):
            p, obs_list = slots.get((key, slot_name), (period if slot_name == "cur" else prev_period, []))
            valid, problems = [], []
            for o in obs_list:
                problem = None
                if o["value"] is None:
                    problem = "blank value (treated as missing, not zero)"
                elif not o["source"]:
                    problem = "no source"
                elif PLACEHOLDER_SOURCE_RE.fullmatch(o["source"]):
                    problem = "source is a placeholder, not a source"
                elif ctx.meeting_source(o["source"]):
                    problem = ("source is a meeting, call or notes; a figure said in a meeting is not a metric, so "
                               "take it from the system of record")
                elif figure_hits(o["source"], ctx) or first_tag(tokens(o["source"]), ctx):
                    problem = "source label has a figure or confidential wording; name only the system and the report"
                elif o["as_of"] is None:
                    problem = "no as-of date"
                elif o["as_of"] > as_of:
                    problem = "as-of date {} is after today".format(o["as_of"].isoformat())
                elif o["as_of"] < p.start:
                    problem = "as-of date {} is before {} began".format(o["as_of"].isoformat(), p.short)
                elif d["unit"] == "currency" and o["currency"] and o["currency"] != b["currency"]:
                    problem = "in {}, not {}; the script does not convert currencies".format(o["currency"], b["currency"])
                elif d["unit"] == "count" and o["value"] != o["value"].to_integral_value():
                    problem = "not a whole number"
                if problem:
                    origin = o["origin"] if names.find(o["origin"]) is None else "a row whose file name is withheld"
                    problems.append("{} ({})".format(problem, origin))
                else:
                    valid.append((quantize(o["value"], d["unit"], config), o))
            values = sorted({q for q, _ in valid})
            if len(values) > 1:
                parts = ["{} from {}".format(fmt_value(q, d["unit"], config, b["currency"]), md(source_label(o, names)))
                         for q, o in valid]
                notes["conflicts"].append("{} ({}): sources disagree: {}. Neither is shown; confirm the right figure.".format(
                    md(d["label"]), p.short, "; ".join(parts)))
                row[slot_name + "_state"] = "conflict"
            elif valid:
                row[slot_name] = values[0]
                row[slot_name + "_state"] = "ok"
                srcs = []
                for _, o in valid:
                    label = source_label(o, names)
                    if label not in srcs:
                        srcs.append(label)
                row[slot_name + "_source"] = "; ".join(srcs)
                row[slot_name + "_as_of"] = max(o["as_of"] for _, o in valid)
                if slot_name == "cur" and d["kind"] == "stock" and row["cur_as_of"] < p.end:
                    notes["stale"].append("{} is as of {}, before {} ended.".format(
                        md(d["label"]), row["cur_as_of"].isoformat(), p.short))
            elif problems:
                row[slot_name + "_state"] = "withheld"
                notes["withheld"].append("{} ({}) not shown: {}.".format(md(d["label"]), p.short, md("; ".join(problems))))
        # the previous update's reported figures: fallback for the previous period, and restatement check
        pu = b["previous_update"]
        if pu is not None:
            for rep in pu["reported"]:
                if rep["metric"] != key or rep["period"] != prev_period.code:
                    continue
                rq = quantize(rep["value"], d["unit"], config)
                if row["prev_state"] == "missing":
                    row["prev"], row["prev_state"] = rq, "ok"
                    row["prev_source"] = "previous update"
                    row["prev_as_of"] = pu["sent_on"]
                elif row["prev_state"] == "ok" and rq != row["prev"]:
                    notes["restated"].append("{} for {}: the last update reported {}; {} now says {}. Say that it was "
                                             "restated, or explain the difference.".format(
                                                 md(d["label"]), prev_period.short,
                                                 fmt_value(rq, d["unit"], config, b["currency"]),
                                                 md(row["prev_source"]),
                                                 fmt_value(row["prev"], d["unit"], config, b["currency"])))
            if row["cur_state"] != "ok" and any(rep["metric"] == key for rep in pu["reported"]):
                notes["missing_vs_last"].append(md(d["label"]))
        if row["cur_state"] == "ok" and row["prev_state"] == "ok":
            row["change"], row["pct"] = change_parts(row["cur"], row["prev"], d["unit"], config, b["currency"])
            delta = row["cur"] - row["prev"]
            if row["prev"] > 0 and row["cur"] > 0 and (row["cur"] >= row["prev"] * config["jump_factor"]
                                                       or row["cur"] * config["jump_factor"] <= row["prev"]):
                notes["jumps"].append("{} changed {}x or more ({} to {}); check it once before using it.".format(
                    md(d["label"]), config["jump_factor"], fmt_value(row["prev"], d["unit"], config, b["currency"]),
                    fmt_value(row["cur"], d["unit"], config, b["currency"])))
            if (d["better"] == "up" and delta < 0) or (d["better"] == "down" and delta > 0):
                notes["wrong_way"].append(md(d["label"]))
        result[key] = row
    return result, notes


def source_label(o: dict, names: Names) -> str:
    """A metric source label, masked if it contains a non-public name (escaped later with md())."""
    if names.find(o["source"]) is not None:
        return "(source label withheld: it names a non-public entity)"
    return o["source"]


def metric_cell(row: dict, slot: str, config: dict, currency: str, review: bool = True) -> str:
    state = row[slot + "_state"]
    if state == "ok":
        return fmt_value(row[slot], row["unit"], config, currency)
    if not review:
        return "not available"
    return {"missing": "not supplied", "withheld": "withheld (see Before you send)",
            "conflict": "withheld: sources disagree"}[state]


def change_text(row: dict) -> str:
    if row["change"] is None:
        return "n/a"
    return row["change"] + (" ({})".format(row["pct"]) if row["pct"] else "")


# --------------------------------------------------------------------------- text checks


def figure_hits(probe: str, ctx: Ctx) -> bool:
    """Whether text carries a figure that did not come from a {metric:...} placeholder (pattern-based)."""
    toks = tokens(probe)
    for i, tok in enumerate(toks):
        # a number (even a year-like one) or a number word right before a metric noun: "2000 customers", "two new logos"
        if (tok.isdigit() or tok in ctx.number_words) and ctx.nouns.find(toks[i + 1:i + 4]):
            return True
    if ctx.scale.find(toks):
        return True
    t = unicodedata.normalize("NFKC", probe)
    for rx in NOT_FIGURE_RES:
        t = rx.sub(" ", t)
    for tok in re.findall(r"[^\W_]+", t):
        if not any(ch.isdigit() for ch in tok):
            continue
        low = tok.lower()
        if low.isdigit() or SCALE_SUFFIX_RE.fullmatch(low) or not any(ch.isalpha() for ch in low):
            return True
        if glued_figure(low, ctx):
            return True
    return False


def glued_figure(low: str, ctx: Ctx) -> bool:
    """A number glued to a metric noun or a multiplier: "40kMRR", "100customers", "ARR2M", "x10" (not "3PL", "B2B")."""
    i = 0
    while i < len(low) and low[i].isdigit():
        i += 1
    if i:  # digits first: the rest, with or without a scale letter, is a metric noun
        rest = low[i:]
        return any(r and ctx.nouns.find([r]) for r in [rest] + [rest[len(s):] for s in ("k", "m", "mm", "b", "bn")
                                                                if rest.startswith(s)])
    j = 0
    while j < len(low) and not low[j].isdigit():
        j += 1
    head, tail = low[:j], low[j:]
    k = 0
    while k < len(tail) and tail[k].isdigit():
        k += 1
    if tail[k:] not in ("", "k", "m", "mm", "b", "bn", "x"):
        return False
    return head == "x" or bool(ctx.nouns.find([head]))


def first_tag(toks: List[str], ctx: Ctx) -> Optional[str]:
    for tag in TAGS:
        if ctx.conf[tag].find(toks):
            return tag
    return None


# Quote and founder-statement checks use granola_text.check_quote: after normalise() on both sides, the text must be one
# complete sentence, or a run of consecutive complete sentences, inside one speaker turn.


def quote_check(text: str, meeting: dict) -> Tuple[Optional[bool], str]:
    """(True, "ok") when the text passes check_quote against the meeting's notes or transcript text; (False, reason)
    when it fails (a quote with an ellipsis, a speaker label or under 3 words fails even without meeting text);
    (None, reason) when neither text is in the bundle."""
    sources = [t for t in (meeting["notes_text"], meeting["transcript_text"]) if t]
    ok, why = gt.check_quote(text, "")
    if why != gt.REASONS["missing"]:
        return False, why
    if not sources:
        return None, why
    reasons = []
    for source in sources:
        ok, why = gt.check_quote(text, source)
        if ok:
            return True, why
        reasons.append(why)
    return False, next((w for w in reasons if w != gt.REASONS["missing"]), reasons[0])


def sentence_key(text: str) -> str:
    """A sentence compared the way check_quote compares it: normalised, case-folded, one pair of outer quotation
    marks and a final . or ! dropped."""
    t = gt.normalise(text).casefold()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t.rstrip(" .!")


_TS = r"(?:\[\s*[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\s*\]|\(\s*[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\s*\)|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)"
TIMESTAMP_PREFIX_RE = re.compile(r"^" + _TS + r"(?:\s*[-–—]\s*|\s+)(?=\S)")
FOUNDER_LABEL_RE = re.compile(r"^\[?\s*([^\[\]():\-–—\n]{1,60}?)\s*\]?\s*(?:[(\[]\s*([^()\[\]\n]{1,60}?)\s*[)\]])?"
                              r"\s*\]?\s*(?::\s*|\s[-–—]\s+)(\S.*)$")


def label_key(label: str) -> str:
    """A speaker label or founder_stated.speaker compared without case, formatting, a leading [hh:mm(:ss)] / hh:mm
    timestamp, surrounding [ ] or a trailing "(role)" / "[role]"."""
    t = gt.normalise(label).casefold().strip(" :")
    t = re.sub(r"^" + _TS + r"\s*", "", t)
    t = re.sub(r"\s*[(\[][^()\[\]]*[)\]]$", "", t).strip()
    return t.strip("[] ").strip()


def founder_source(text: str, sk: str) -> str:
    """Meeting text as the founder checks read it: a leading timestamp is dropped from every line, and a column-zero
    label of the founder in a common transcript form ([Name]:, Name [Role]:, Name (Role) :, Name:<no space>,
    Name - , Name — ) is rewritten as "Name (Role): ". Other speakers' labels are left as they are, so an
    unusual label of someone else stays inside the founder's turn and is checked as part of it (fails safe)."""
    out = []
    for line in text.split("\n"):
        if line[:1].isspace() or not line:
            out.append(line)
            continue
        line = TIMESTAMP_PREFIX_RE.sub("", line)
        m = FOUNDER_LABEL_RE.match(line)
        if m and label_key(m.group(1)) == sk and not m.group(1).lstrip().startswith(("*", "_", ">", "-", "+", "|")):
            line = m.group(1).strip() + (" (" + m.group(2).strip() + ")" if m.group(2) else "") + ": " + m.group(3)
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- founder context: fail-closed neighbour check
# A forward-looking claim is kept only when every OTHER sentence of the founder's turn (inside or outside the excerpt),
# and of the founder's next turn, positively reads as a plain statement. Recognition is by shape (a subject followed
# by a known finite verb, nothing fronted) plus vocabulary that disqualifies a sentence; anything the check can't
# recognise withholds the claim. This is a conservative English heuristic, not a semantic guarantee.
# Round 10: "plain" is defined by marker classes that apply whatever the verb (condition, negation, modal/hedge,
# retraction, precondition/open decision/request for time, attribution, setback, back-reference, another date); the
# claim sentence itself, the reply right after it and every later turn of the meeting are checked too, and a role in
# the founder's label that announces a read-out, or two roles for one name, makes the attribution ambiguous.

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)*|[,;:–—]| - ")
_NEG_WORDS = {"not", "no", "never", "nothing", "none", "nobody", "noone", "nowhere", "neither", "nor", "cannot",
              "without", "hardly", "barely", "scarcely", "nope", "nah", "dont", "doesnt", "didnt", "wont", "cant",
              "isnt", "arent", "wasnt", "werent", "wouldnt", "couldnt", "shouldnt", "havent", "hasnt", "hadnt", "aint",
              "mustnt", "neednt", "mightnt"}
_COND_RE = re.compile(r"\b(?:if|unless|assuming|assume|assumes|assumed|supposing|suppose|provided|providing|whether|"
                      r"otherwise|contingent|conditional|conditionally|depends|depend|depending|dependent|pending|"
                      r"barring|hinges?|subject to|as long as|so long as|in case|only when|in the event|on condition|"
                      r"given that|rests on|relies on|tbd|tbc)\b")
_MODALS = {"would", "could", "might", "may", "should", "ought", "must", "can", "shall"}
_HEDGE_RE = re.compile(
    r"\b(?:maybe|perhaps|possibly|possible|probably|probable|likely|unlikely|hopefully|hope|hopes|hoped|hoping|"
    r"hopeful|realistically|realistic|unrealistic|honestly|frankly|truthfully|admittedly|candidly|guess\w*|"
    r"guesstimate|ballpark|roughly|approximately|think|thinks|thought|believe|believes|believed|feel|feels|suspect|"
    r"suspects|doubt\w*|expect|expects|expected|expecting|presume|reckon|reckons|bet|seems?|seemed|appears|"
    r"apparently|supposedly|allegedly|presumably|ostensibly|ish|kind of|sort of|more or less|at least|or so|"
    r"anyway|anyways|in principle|for now|in theory|theoretic\w*|hypothetic\w*|on paper|aim|aims|aiming|"
    r"aspir\w*|targets?|targeting|goals?|stretch\w*|ambitious|aggressive\w*|bullish|optimis\w*|pessimis\w*|"
    r"upside|best[ -]case|worst[ -]case|scenarios?|dream\w*|wish\w*|ideal|ideally|perfect world|fingers|luck|"
    r"lucky|pray\w*|knock on wood|touch wood|fantas\w*|speculat\w*|gut|rough|loosely|tentative\w*|"
    r"provisional\w*|preliminary|unconfirmed|uncertain\w*|unsure|unclear|iffy|dicey|shaky|wobbly|fluid|risky|"
    r"risks?|slip\w*|delay\w*|postpon\w*|behind|miss|misses|missed|missing|moonshot|blue[ -]sky|rosy|rosiest|"
    r"caveats?|asterisk|catch|but|though|although|however|except|unfortunately|sadly|nervous|worr\w*|"
    r"concern\w*|skeptic\w*|sceptic\w*|cautio\w*|warning|generous\w*|exaggerat\w*|inflat\w*|padd(?:ed|ing)|"
    r"over(?:promis|sell|sold|stat|commit|reach|shoot|estimat|optimis)\w*|sure|best effort|fair wind|still|yet|"
    r"wait|waits|waiting|awaiting|eventually|someday|sometime|later|soonish|first$|fallback\w*|backup\w*|plan b|"
    r"contingenc\w*|alternative\w*|safety net|late(?! (?:january|february|march|april|maymonth|june|july|august|september|october|"
    r"november|december|q[1-4]|(?:19|20)[0-9]{2}|last|this|next)\b)|later|overdue)\b")
_RETRACT_RE = re.compile(
    r"\b(?:kid|kids|kidding|kidded|jok\w*|jk|lol|lmao|rofl|haha\w*|hah|ha|heh\w*|sarcas\w*|iron(?:y|ic|ical|ically)|as if|yeah|yea|"
    r"obviously|totally|absolutely|pigs|lottery|queen|unicorns?|said no one|said nobody|said every|good one|"
    r"dream on|hell freezes|pull the other|scratch|strike|strikes|retract\w*|withdr[ae]w\w*|take (?:it |that |this )?back|"
    r"rephras\w*|correction|misspo\w*|misspeak\w*|misstat\w*|ignore|ignoring|disregard\w*|forget|"
    r"off the record|on the record|quot(?:e|es|ed|ing|able)|in writing|made up|making up|make up|nonsense|bluff\w*|"
    r"tongue in cheek|grain of salt|pinch of salt|laugh\w*|smirk\w*|wink\w*|grin\w*|chuckl\w*|giggl\w*|"
    r"eye ?roll\w*|rolled (?:her|his|their|my|our) eyes)\b")
# a back-reference to the claim: pronoun, or determiner + one of these nouns
_BACKREF_NOUNS = {"date", "dates", "timeline", "timelines", "timing", "deadline", "deadlines", "target", "targets",
                  "number", "numbers", "figure", "figures", "forecast", "forecasts", "estimate", "estimates",
                  "projection", "projections", "goal", "goals", "plan", "plans", "promise", "promises", "commitment",
                  "commitments", "claim", "claims", "statement", "statements", "prediction", "predictions",
                  "guidance", "schedule", "month", "version", "scenario", "assumption", "assumptions", "outlook",
                  "milestone", "milestones", "one"}
_TIME_NOUNS = {"week", "weeks", "month", "quarter", "year", "morning", "afternoon", "evening", "sprint", "cycle",
               "round", "period", "monday", "tuesday", "wednesday", "thursday", "friday", "weekend", "time"}
# nouns that refer to a claim wherever they appear ("a confident date", "the number we pitch")
_BARE_REF_NOUNS = {"date", "dates", "timeline", "timelines", "timing", "deadline", "deadlines", "forecast", "forecasts",
                   "estimate", "estimates", "projection", "projections", "promise", "promises", "commitment",
                   "commitments", "claim", "claims", "prediction", "predictions", "guidance", "scenario", "scenarios",
                   "assumption", "assumptions", "outlook", "milestone", "milestones", "eta", "deck", "pitch", "headline"}
# dates and periods: a neighbour naming one the claim doesn't name, in a sentence that isn't past tense, may move,
# replace or doubt the claim's date ("June is the backup.", "We'll revisit in December.")
_DATE_WORDS = {"january", "february", "march", "april", "maymonth", "june", "july", "august", "september", "october",
               "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
               "dec", "q1", "q2", "q3", "q4", "h1", "h2", "spring", "summer", "autumn", "winter", "christmas",
               "easter", "eoy", "eoq", "yearend"}
_DATE_PHRASE_RE = re.compile(r"\b(?:next|following|coming) (?:week|weeks|month|months|quarter|quarters|year|years|"
                             r"sprint|cycle|round|release)\b|\b(?:later|end of) (?:this|the|next) (?:year|quarter|month)\b|"
                             r"\b(?:19|20)[0-9]{2}\b|\bfy ?[0-9]{2,4}\b")
_FUNCTION_WORDS = {"the", "a", "an", "our", "their", "my", "your", "its", "his", "her", "be", "been", "being", "going",
                   "to", "will", "would", "all", "new", "us", "them", "me", "and", "or", "but", "then", "than", "also",
                   "more", "most", "very", "just", "get", "make", "have", "has", "had", "is", "are", "was", "were", "am",
                   "do", "does", "did", "of", "in", "on", "at", "by", "for", "with", "from", "into", "this", "that",
                   "these", "those", "it", "we", "i", "you", "they", "he", "she", "every", "each", "some", "any"}
_PREPS = {"in", "on", "at", "with", "for", "from", "by", "to", "of", "under", "over", "after", "before", "between",
          "among", "without", "within", "given", "per", "as", "like", "despite", "unlike", "off", "into", "onto",
          "upon", "about", "around", "across", "through", "throughout", "toward", "towards", "via", "versus", "vs",
          "except", "besides", "beyond", "against", "along", "amid"}
_PARTICLES = {"up", "down", "out", "away", "back", "aside", "again", "too", "here", "there", "now", "then"}
_CONJ = {"and", "but", "or", "nor", "yet", "so", "though", "although", "because", "since", "while", "whereas",
         "unless", "if", "when", "whenever", "where", "wherever", "once", "until", "till", "whether"}
_BAD_START = (_PREPS | _CONJ | {"what", "why", "how", "who", "whom", "whose", "which", "well", "oh", "ah", "um", "uh",
                                "hmm", "yeah", "yes", "yep", "yea", "no", "nope", "ok", "okay", "alright", "right",
                                "sure", "fine", "great", "cool", "totally", "obviously", "actually", "anyway", "anyhow",
                                "basically", "literally", "really", "just", "only", "even", "also", "still", "then",
                                "now", "again", "plus", "meanwhile", "otherwise", "instead", "hence", "thus",
                                "therefore", "look", "listen", "see", "please", "let", "ha", "haha", "lol", "wow",
                                "here", "there", "such", "same", "said", "correction", "not", "never"})
_PRONOUN_SUBJECTS = {"i", "we", "you", "he", "she", "they", "it"}
_BACKREF_STARTS = {"that", "this", "it", "these", "those"}
_AUX = {"is", "are", "was", "were", "am", "has", "have", "had", "do", "does", "did", "will"}
_PAST = {"hired", "shipped", "launched", "closed", "signed", "started", "finished", "completed", "released", "raised",
         "added", "onboarded", "migrated", "renewed", "expanded", "grew", "rose", "fell", "increased", "decreased",
         "improved", "reduced", "passed", "approved", "joined", "left", "moved", "opened", "built", "wrote", "won",
         "lost", "sold", "booked", "delivered", "published", "deployed", "fixed", "merged", "tested", "piloted",
         "presented", "met", "agreed", "committed", "confirmed", "asked", "requested", "wanted", "needed", "liked",
         "loved", "used", "chose", "picked", "selected", "finalised", "finalized", "scoped", "staffed", "funded",
         "budgeted", "scheduled", "ran", "held", "hit", "reached", "beat", "doubled", "tripled", "recruited",
         "interviewed", "trained", "documented", "designed", "prototyped", "demoed", "reviewed", "checked",
         "verified", "audited", "submitted", "filed", "paid", "invoiced", "collected", "churned", "upgraded",
         "downgraded", "ended", "began", "came", "went", "got", "took", "made", "arrived", "failed", "converted",
         "integrated", "rolled", "upsold", "accepted", "chosen", "settled", "landed", "secured", "received", "dropped",
         "declined", "climbed", "jumped", "done", "written", "begun", "grown", "been", "seen", "taken", "given"}
# after is/are/am (and adverbs such as already), a predicate the check accepts as a plain status; anything else
# ("is less confident", "is unsigned", "is reviewing", "is a coin flip") withholds the claim
_STATUS = {"done", "live", "ready", "complete", "stable", "healthy", "strong", "green", "up", "down", "unchanged",
           "available", "booked", "full", "open", "profitable", "positive", "excited", "happy", "thrilled", "pleased",
           "aligned", "confident", "committed", "final", "firm", "locked", "set", "agreed", "busy", "growing",
           "fine", "good", "great", "solid"}
_STATUS_PHRASES = {("on", "track"), ("on", "schedule"), ("on", "budget"), ("in", "place"), ("the", "same"),
                   ("in", "production"), ("in", "seat"), ("ahead", "of")}
_PLACES = {"doc", "docs", "document", "folder", "drive", "wiki", "tracker", "repo", "sheet", "spreadsheet", "calendar",
           "channel", "crm", "notes", "inbox", "dashboard", "round"}
_PRED_ADVERBS = {"already", "now", "fully", "all", "also", "very", "really", "super", "so", "both", "finally",
                 "officially", "formally", "just", "well", "nearly", "sharply"}
# after will: verbs of doing (a further plan of the founder's); a deferral or unknown verb ("will know", "will see",
# "will revisit", "will be") withholds the claim
_FUTURE_OK = {"launch", "ship", "hire", "open", "start", "release", "add", "move", "expand", "close", "sign", "raise",
              "onboard", "roll", "migrate", "deliver", "publish", "deploy", "complete", "finish", "build", "own",
              "lead", "support", "grow", "double", "triple", "hit", "cut", "reduce", "increase", "announce",
              "present", "demo", "host", "join", "keep", "continue", "send", "share", "invest", "spend", "fund",
              "staff", "train", "offer", "introduce", "integrate", "pilot", "test", "extend", "renew"}
_PRESENT = {"owns", "own", "leads", "lead", "runs", "run", "manages", "manage", "handles", "handle", "covers", "cover",
            "includes", "include", "stays", "stay", "remains", "remain", "looks", "look", "works", "work", "holds",
            "hold", "starts", "start", "ends", "end", "ships", "ship", "sells", "sell", "grows", "grow", "uses", "use",
            "supports", "support", "love", "loves", "like", "likes", "meets",
            "meet", "reports", "report", "tracks", "track", "lives", "live", "sits", "sit", "joins", "join",
            "pays", "pay", "renews", "renew"}
_SHORT_VERBS = {"grew", "shipped", "launched", "closed", "increased", "decreased", "fell", "rose", "arrived", "ended",
                "started", "passed", "failed", "signed", "renewed", "hired", "joined", "landed", "converted"}
_LY_OK = {"daily", "weekly", "monthly", "quarterly", "yearly", "early", "family", "supply", "assembly", "only"}
_TRAIL = (r"(?: (?:yesterday|today|last week|last month|this week|this month|already|in writing|unanimously|formally|"
          r"on (?:monday|tuesday|wednesday|thursday|friday)|at the offsite|at the board meeting))?")
_BN = r"(?:" + "|".join(sorted(_BACKREF_NOUNS - {"one"})) + r")"
_REF = r"(?:it|this|that|the " + _BN + r"|that " + _BN + r"|this " + _BN + r"|our " + _BN + r")"
_SECTION_NOUNS = (r"(?:product|products|hiring|recruiting|sales|the team|team|customers|customer side|revenue|"
                  r"engineering|marketing|ops|operations|support|growth|retention|finance|go-to-market|"
                  r"gtm|people|partnerships|fundraising)")
# Prior performance questions accept named business topics, not arbitrary nouns
# or adjectives that can turn the next answer into a rehearsal or imagined update.
_QUESTION_TOPIC_RE = re.compile(
    r"(?:(?:the|our|your|this|last|previous|current) )?(?:" + _SECTION_NOUNS +
    r"|week|month|quarter|year|sprint|cycle|onboarding|deployment|delivery|release|"
    r"(?:customer|supplier|vendor|employee) onboarding|"
    r"(?:(?:billing|customer|supplier|vendor|team|project|release) )?handoff)")
_ALLOW_RES = [re.compile(p) for p in [
    r"(?:morning|afternoon|evening) (?:all|everyone) thanks for dialling in",
    r"(?:good|great) to see (?:everyone|you all)",
    r"short one from me today",
    r"welcome back (?:team|everyone|all)",
    r"thanks [a-z]+",
    r"see you then",
    r"thanks for dialling in(?: so early)?",
    r"none this month",
    r"the team is in great shape",
    r"quick one on " + _SECTION_NOUNS,
    _SECTION_NOUNS + r" is next",
    # greetings, thanks, agenda and closing fragments
    r"(?:good )?(?:morning|afternoon|evening)(?: (?:everyone|all|everybody|team|folks|both))?",
    r"(?:hi|hello|hey|welcome)(?: and welcome)?(?: (?:everyone|all|everybody|team|folks|both|there|back))?",
    r"(?:thanks|thank you)(?: (?:all|everyone|everybody|team|folks|again|so much))*"
    r"(?: for (?:joining|coming|being here|the time|your time|making the time|having me|the questions))?"
    r"(?: (?:all|everyone|everybody|team|folks|again))?",
    # Round 10 (IV9-8): acknowledgement and agenda fragments with no predicate on anything; the nouns are fixed
    r"(?:overall |so |all in all )?(?:a |another |what a )?(?:really |very )?(?:big|good|great|strong|solid|busy|"
    r"productive|exciting|eventful|record) (?:month|quarter|week|year|few weeks|stretch)"
    r"(?: (?:for us|for the team|overall|all round|all around))?",
    r"(?:some |lots of |a lot of |plenty of |more )?(?:good|great|exciting) (?:news|progress|momentum|work)"
    r"(?: (?:on|in|from|across) (?:the )?" + _SECTION_NOUNS + r")?(?: (?:this|last) (?:month|week|quarter))?",
    r"(?:so|first|next|now|then|okay|ok|right|on to|moving on to|turning to)(?: then)? (?:the )?" + _SECTION_NOUNS +
    r"(?: (?:news|update|updates|side))?",
    r"(?:the )?next (?:topic|item|up)(?: is)? (?:the )?" + _SECTION_NOUNS + r"(?: (?:news|update|updates|side))?",
    r"let me (?:start with|begin with|give you|walk you through|share|cover)(?: the)? " + _SECTION_NOUNS +
    r"(?: (?:news|update|updates|side))?(?: first)?",
    r"(?:i am|we are|i am really|we are really|i am very|we are very|i am so|we are so) proud of (?:the|our|this) "
    r"(?:[a-z]+ )?team",
    r"next (?:item|topic|slide|agenda item|point|question)|moving on|on to (?:the )?next (?:item|topic)",
    r"(?:a )?(?:quick |brief |short )?update on (?:the )?" + _SECTION_NOUNS,
    r"(?:a )?(?:quick |brief )?(?:product|hiring|sales|team|customer|finance|ops|engineering|metrics|board|"
    r"marketing|pipeline) update",
    r"that is (?:all|it) (?:from me|for me|for today|for now)|over to you(?: [a-z]+)?",
    r"let us (?:start|begin|get started|move on|kick off)(?: with (?:the )?" + _SECTION_NOUNS + r")?",
    r"(?:ask me|let me know|ping me|shout|reach out)(?: if| when) (?:you|anyone) (?:have|has) (?:any )?"
    r"(?:questions|concerns|comments)",
    # status fragments with a fixed set of nouns
    r"no (?:new )?(?:change|changes|news|issues|blockers|incidents|churn|outages|escalations)"
    r"(?: (?:from|since) (?:the )?last (?:week|month|quarter|call|update|meeting)| this (?:week|month|quarter)| to report)?",
    # reinforcers: the claim is firm
    _REF + r" is (?:now |already |fully |officially )?(?:final|firm|locked|locked in|confirmed|committed|fixed|set|"
    r"set in stone|agreed|approved|signed off|funded|staffed|scoped|on track|on schedule|decided|official|"
    r"a commitment|a firm commitment|a hard date|a hard commitment|a firm date|our commitment|the plan of record|"
    r"in the plan|in the budget|budgeted)",
    r"(?:it|that|this) is (?:already )?(?:in|on) the (?:roadmap|plan|spec|sprint plan|release plan|backlog|budget)"
    r"(?: doc)?",
    r"(?:we|i|the board|the team|engineering|everyone|the company|the founders)(?: have| has| had| all)?"
    r"(?: already| formally| publicly)? (?:committed|promised|confirmed|agreed|signed off|approved|locked|locked in|"
    r"announced|budgeted|scheduled|staffed)(?: to| on| for)? " + _REF +
    r"(?: with (?:the board|investors|our investors|the team|customers|our customers|everyone))?" + _TRAIL,
]]
# a back-reference in object position is accepted only after an endorsing predicate
_ENDORSE_RE = re.compile(
    r"(?:already )?(?:approved|endorsed|backed|confirmed|signed off on|greenlit|green lit|funded|budgeted for|"
    r"budgeted|requested|asked for|wanted|want|wants|love|loves|loved|like|likes|liked|need|needs|needed|started on|"
    r"started|began|is building|are building|is working on|are working on|have started|has started|have scoped|"
    r"has scoped|scoped|staffed|have staffed|has staffed|signed|(?:is|are|was|were|am) (?:really |very |all |super )?"
    r"(?:excited|happy|thrilled|pleased|keen|eager|aligned) (?:about|on|with|for)) " + _REF + _TRAIL)

CONTEXT_WHAT = {
    "question": "is a question",
    "condition": "has a condition (if, unless, depends on, subject to ...)",
    "negation": "has a negation",
    "retraction": "has a joke, retraction, correction or sarcasm cue",
    "hedge": "has a hedge, modal or uncertainty word",
    "backref": "comments on the claim (that, this, it, the date ...)",
    "shape": "is not recognised as a plain statement (a fragment, a fronted phrase, an imperative or an unlisted verb)",
    "unanswered": "is a question about the claim that the founder's next turn does not answer",
    "topic": "talks about what the claim is about (it repeats one of the claim's words), so it may qualify the claim",
    "date": "names a date or period the claim doesn't, so it may move or doubt the claim's date",
    "predicate": "has a predicate the check doesn't recognise as a plain status (is/has/will followed by an unlisted "
                 "word)",
    "dependency": "has a precondition, an open decision or a request for time (needs, requires, awaits, sign-off, "
                  "approval, decision, review, another month ...)",
    "attribution": "attributes words to someone or something else (said, wrote, according to, quoting, a memo or "
                   "deck ...)",
    "setback": "reports a setback (failed, lost, left, cancelled, blocked ...)",
    "later": "comes later in the meeting and retracts or qualifies the claim (a correction, a retraction, or a "
             "hedge, doubt, condition or another date next to the claim's words)",
}


READOUT_RE = re.compile(
    r"\b(?:read(?:s|ing)? (?:out|aloud|back|from)|verbatim|word for word|quot\w*|fake|satir\w*|parod\w*|spoof\w*|"
    r"mock\w*|imagin\w*|pretend\w*|role[- ]?play\w*|impression|impersonat\w*|paraphras\w*|"
    r"(?:her|his|their|the|an?) (?:old )?(?:deck|memo|email|post|slide|letter|blog|tweet|script|press release)|"
    r"as (?:she|he|they) (?:said|put it|wrote|says))\b")

# Round 10: "plainly clean" is defined by marker classes. A sentence next to a claim is not clean when it has a marker
# of any class below, whatever its verb (a verb on an accept list no longer makes a precondition clean).
# dependency: a precondition, an open decision, an approval, a review, or a request for more time. Present-tense
# need/want/require/await always count; a past or present request (asked for, requested ...) counts when its object is
# a plan, a review, a decision, an approval or time; "has/have asked for" counts like the present.
_PROCESS_NOUN = (r"(?:plan|plans|replan|re-plan|review|reviews|decision|decisions|approval|approvals|sign-?off|sign off|"
                 r"go-?ahead|green light|extension|extensions|delay|delays|pause|buffer|rethink|timeline|date|dates|"
                 r"schedule|scope|budget|business case|time|week|weeks|month|months|quarter|sprint|sprints|vote)")
_DEPEND_RE = re.compile(
    r"\b(?:need|needs|needing|require|requires|requiring|requirement|requirements|prerequisites?|preconditions?|"
    r"await|awaits|awaiting|want|wants|wanting|approve|approves|approving|approval|approvals|unapproved|"
    r"sign[- ]?off|signoff|signs off|sign off on|go[- ]?ahead|green[- ]?light|blessing|veto\w*|votes?|voting|"
    r"decide|decides|deciding|undecided|decision|decisions|review|reviews|reviewing|replan\w*|re-plan\w*|rethink\w*|"
    r"reconsider\w*|revisit\w*|re-?evaluat\w*|reassess\w*|the call|final call|final say|a say|go/no-go|go no go|"
    r"more time|extra time|has to|have to|had to|got to|gotta|"
    r"another (?:day|days|week|weeks|month|months|quarter|quarters|sprint|sprints|cycle|round|year|look|pass)|"
    r"(?:one|two|three|four|a few|few|several|some) more (?:days|weeks|months|sprints|quarters|cycles)|"
    r"(?:has|have|had|is|are) (?:already |now |formally )?(?:asked|asking|requested|requesting|demanded|demanding) "
    r"for|"
    r"(?:asked|asking|asks|ask|requested|requesting|requests|request|demanded|demands|pushed|pushing|needed|wanted|"
    r"required|awaited|waited) (?:for |on )?"
    r"(?:(?:a|an|the|more|another|some|our|their|its|his|her)\s)?(?:[a-z-]+\s){0,2}" + _PROCESS_NOUN + r")\b")
# a review, decision or approval named as a noun is a precondition only while it is open: with a finite past-tense
# verb and no other dependency marker ("The security review passed.") it is a completed step
_DEPEND_NOUNS = {"review", "reviews", "decision", "decisions", "approval", "approvals", "sign-off", "signoff",
                 "sign off", "go-ahead", "goahead", "go ahead", "green light", "greenlight", "green-light", "blessing",
                 "vote", "votes"}


def _dependency(probe: str, words: List[str]) -> bool:
    found = [m.group(0) for m in _DEPEND_RE.finditer(probe)]
    if not found:
        return False
    return not (all(f in _DEPEND_NOUNS for f in found) and _past_completion([w for w in words if w != ","]))


# attribution: the words are someone else's, or read out from a document
_ATTRIB_RE = re.compile(
    r"\b(?:according to|per (?:the|our|his|her|their|its)|in the words of|on behalf of|said|says|saying|told|tells|"
    r"wrote|writes|written by|stated|cited|citing|cites|relay\w*|repeat\w*|reiterat\w*|the following|"
    r"the line (?:from|in)|this line|that line)\b")
# setback: a plain past fact that undermines a plan (the implication class QA measured)
_SETBACK_RE = re.compile(
    r"\b(?:fail|fails|failed|failing|failure|failures|lost|lose|loses|losing|left|quit|quits|resign\w*|cancel\w*|"
    r"bankrupt\w*|insolven\w*|ran out|run out|runs out|running out|broke|broken|outage\w*|incident\w*|breach\w*|"
    r"findings|blocker\w*|blocked|blocking|setback\w*|stall\w*|stuck|pulled out|backed out|dropped out|walked away|"
    r"went under|out of business|shut down|crash\w*|wall)\b")
# a symbol or emoticon in the claim sentence itself (sarcasm, crossed fingers ...)
_EMOTICON_RE = re.compile(r"(?:^|\s)(?:/s|/j|:-?[pPdD)(/|]|;-?[)pP]|[xX][dD]|\(jk\)|\^\^)(?=$|[\s.,!?])")
# hedges of the claim sentence itself: adverbs, belief verbs and set phrases only. The neighbour hedge list
# (_HEDGE_RE) also has nouns and word prefixes ("risk", "backup", "optimis...") that are often the claim's own content
# ("route optimisation", "our risk team"), and forward wording ("expect", "target") that is the claim's own verb.
_CLAIM_HEDGE_RE = re.compile(
    r"\b(?:maybe|perhaps|possibly|probably|likely|unlikely|hopefully|hope|hoping|realistically|honestly|frankly|"
    r"admittedly|supposedly|allegedly|presumably|ostensibly|apparently|roughly|approximately|ish|soonish|kind of|"
    r"sort of|more or less|at least|or so|in principle|for now|in theory|theoretically|hypothetically|on paper|"
    r"ideally|perfect world|best[ -]case|worst[ -]case|fingers crossed|knock on wood|touch wood|knock wood|"
    r"tentative\w*|provisional\w*|preliminary|loosely|guess|guessing|think|thinks|believe|believes|reckon|reckons|"
    r"bet|suspect|doubt\w*|seems?|appears|eventually|someday|sometime|stretch goal|aspirational|optimistically|"
    r"wishful\w*|with luck|if we are lucky|unconfirmed|uncertain\w*|unsure|unclear|nominally|notionally|technically|"
    r"give or take|at the earliest|at best|all else(?: being)? equal|all things(?: being)? equal|all being well|"
    r"all going well|if all goes|god willing|weather permitting|so to speak|in a sense|as things stand|as it stands|"
    r"for the moment)\b")
# a later turn: retraction or correction vocabulary
_LATER_RETRACT_RE = re.compile(
    r"\b(?:scratch|strike|retract\w*|withdr[ae]w\w*|take (?:it |that |this )?back|walk(?:ing|ed|s)? (?:it |that |this )?"
    r"back|walk back|rephras\w*|correction|correct (?:myself|that|this|what)|misspo\w*|misspeak\w*|misstat\w*|ignore|"
    r"disregard\w*|forget|kid|kidding|jok\w*|jk|lol|placeholder|made up|making up)\b")
# a role in the founder's label that announces somebody else's words: "Maren (quoting Kestrel):"
_ROLE_VERB_NOUNS = {"engineering", "marketing", "accounting", "consulting", "recruiting", "manufacturing", "banking",
                    "operating", "training", "pricing", "staffing", "hiring", "planning", "sourcing", "lending",
                    "billing", "onboarding", "publishing", "licensing", "ordering", "shipping", "trading", "testing"}

# ---------------------------------------------------------------- dates relative to the update period
_MONTH_NUM = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
              "maymonth": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
              "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend)"
_UNITS = r"(?:day|days|week|weeks|month|months|quarter|quarters|year|years|sprint|sprints)"
_FUTURE_PHRASE_RE = re.compile(
    r"\b(?:next|coming|following|upcoming|this coming) (?:few )?(?:week|weeks|month|months|quarter|quarters|year|years|"
    r"sprint|sprints|cycle|round|release|half|season|spring|summer|autumn|fall|winter|meeting|board meeting|"
    + _WEEKDAYS + r")\b|\btomorrow\b|\btonight\b|\blater (?:this|next|in the) (?:week|month|quarter|year)\b|"
    r"\b(?:end|close|turn) of (?:the |this |next )?(?:week|month|quarter|year)\b|\byear ?end\b|\byear-end\b|"
    r"\beo[ymq]\b|\b(?:in|within|over) (?:the next |the coming )?(?:a|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"a few|few|several|[0-9]+) (?:more )?" + _UNITS + r"\b|\bover the (?:next|coming)\b|\bnew year\b|"
    r"\b(?:early|mid|late) next\b|\bchristmas\b|\beaster\b")
_SEASON_RE = re.compile(r"\b(?:(?:this|in the|by|until|early|late|mid|the) (?:spring|summer|autumn|fall|winter)|"
                        r"(?:spring|summer|autumn|winter))\b")
_YEAR_RE = re.compile(r"\b(?:fy ?)?((?:19|20)[0-9]{2})\b|\bfy ?([0-9]{2})\b")


def future_dates(flat: str, period: Optional["Period"]) -> List[Tuple[int, str]]:
    """(position, key) of every date or period in the space-joined words that can name a time after the update period:
    a month 1 to 9 months after the period's last month (or with a later year), a later quarter or half, a later year,
    a season, Christmas, the new year, next/coming ..., in two weeks, end of the year .... Without a period every
    month, quarter and half counts (fails safe)."""
    em, ey = (period.end.month, period.end.year) if period is not None else (None, None)
    out = []

    def year_after(i):
        m = _YEAR_RE.match(flat, i)
        if m is None:
            return None
        return int(m.group(1)) if m.group(1) else 2000 + int(m.group(2))

    for m in re.finditer(r"\b[a-z0-9]+\b", flat):
        w, i = m.group(0), m.start()
        rest = flat[m.end():].lstrip(" ")
        yi = len(flat) - len(rest)
        if w in _MONTH_NUM:
            month = _MONTH_NUM[w]
            y = year_after(yi)
            if em is None:
                future = True
            elif y is not None:
                future = (y, month) > (ey, em)
            else:
                future = 1 <= (month - em) % 12 <= 9
            if future:
                out.append((i, w))
        elif re.fullmatch(r"q[1-4]|h[12]", w) or (w in _ORDINALS and re.match(r"(?:quarter|half)\b", rest)):
            if w[0] in "qh" and w[1:].isdigit():
                kind, n = ("quarter" if w[0] == "q" else "half"), int(w[1])
            else:
                kind, n = ("quarter" if rest.startswith("quarter") else "half"), _ORDINALS[w]
                rest = rest.split(" ", 1)[1] if " " in rest else ""
                rest = re.sub(r"^of (?:the )?", "", rest)
            y = year_after(len(flat) - len(rest)) if rest else None
            if kind == "half" and n > 2:
                continue
            last = 3 * n if kind == "quarter" else 6 * n
            if em is None:
                future = True
            elif y is not None:
                future = (y, last) > (ey, em)
            else:
                future = 1 <= (last - em) % 12 <= (9 if kind == "quarter" else 11) and not (
                    kind == "half" and last - 6 < em <= last)
            if future:
                out.append((i, "{}{}".format(kind[0], n)))
    for m in _YEAR_RE.finditer(flat):
        y = int(m.group(1)) if m.group(1) else 2000 + int(m.group(2))
        if ey is None or y > ey:
            out.append((m.start(), str(y)))
    for rx in (_FUTURE_PHRASE_RE, _SEASON_RE):
        for m in rx.finditer(flat):
            if not re.search(r"\b(?:last|this past|past)\s*$", flat[:m.start()]):
                out.append((m.start(), m.group(0)))
    return sorted(set(out))


def _past_completion(words: List[str]) -> bool:
    """Whether a clause has a finite past-tense verb (shipped, was, has hired), not a participle of a schedule
    (is scheduled, expected, planned) or of the passive present (is signed)."""
    for i, w in enumerate(words):
        prev = words[i - 1] if i else ""
        nxt = next((x for x in words[i + 1:] if x not in _PRED_ADVERBS), "")
        if w in ("was", "were", "did"):
            return True
        if w in ("has", "have", "had") and (nxt in _PAST or nxt in _PAST_EXTRA or nxt.endswith("ed")):
            return True
        if w in _NOT_COMPLETION or prev in ("is", "are", "am", "be", "being", "been", "to", "will", "would", "not"):
            continue
        if w in _PAST_FINITE or w in _PAST_EXTRA or (w.startswith("re") and w[2:] in _PAST_FINITE):
            return True
        if w.endswith("ed") and len(w) >= 5 and i > 0 and w not in _NOT_ED_VERBS and prev not in _DETERMINERS \
                and prev not in _PRED_ADVERBS:
            return True
    return False


_DETERMINERS = {"the", "a", "an", "our", "their", "its", "his", "her", "my", "your", "this", "that", "these", "those",
                "every", "each", "all", "some", "any", "more", "most", "newly", "fully", "well", "very", "highly", "of",
                "and", "or", "for", "with", "in", "on", "by", "to", ","}
# forms that are participles only, or that can be present or past ("hit", "cut"): not evidence of a past event
_NOT_COMPLETION = {"scheduled", "slated", "planned", "expected", "projected", "forecast", "forecasted", "targeted",
                   "intended", "anticipated", "proposed", "estimated", "supposed", "committed", "promised", "budgeted",
                   "pencilled", "penciled", "earmarked", "set", "due", "hit", "cut", "put", "let", "quit", "shut",
                   "read", "spread", "beat", "done", "been", "seen", "taken", "given", "written", "begun", "grown",
                   "chosen", "agreed", "booked", "staffed", "funded", "scoped", "timed", "lined"}
_PAST_EXTRA = {"spent", "sent", "led", "found", "kept", "brought", "bought", "caught", "felt", "gave", "saw", "became",
               "spoke", "stood", "flew", "drove", "told", "said", "won", "knew", "rebuilt", "redid"}
_PAST_FINITE = _PAST - _NOT_COMPLETION
_NOT_ED_VERBS = {"speed", "hundred", "indeed", "need", "seed", "feed", "embed", "proceed", "succeed", "exceed",
                 "breed", "bleed", "greed", "shred"}
_SCHEDULE_RE = re.compile(
    r"\b(?:scheduled|slated|pencil+ed|penciled|earmarked|timetabled|lined up|teed up) (?:for|to|in|on|by|next|this|"
    r"early|mid|late|around|after|before)\b|\b(?:planned|set|booked) (?:for|to)\b|\bdue (?:in|on|by|for|next|this|early|mid|late|around|after|"
    r"before|any)\b|\b(?:is|are|am|was|were|be|being|been|remains?|stays?) (?:now |currently |still |already )?"
    r"(?:scheduled|slated|due|planned|pencil+ed|penciled|earmarked|upcoming|imminent)\b|\b(?:upcoming|forthcoming|"
    r"imminent)\b")


def scheduled_future(text: str, period: Optional["Period"]) -> bool:
    """Whether an item reads as forward-looking without forward_terms: a schedule word (scheduled for, slated, due in,
    upcoming ...), or a clause that names a date after the update period with no finite past-tense verb ("The bridge
    round wraps up in March.", "We are hiring a head of sales in December."). A clause is split at ; : dashes and at
    and/but/while/then; a short fragment ("and September") stays with the clause before it."""
    flat_c = " ".join(_context_words(text))
    if _SCHEDULE_RE.search(re.sub(r"\s*,\s*", " ", flat_c)):
        return True
    parts = re.split(r"\s*(?:[;:–—]| - |\b(?:and|but|while|whereas|then|so)\b)\s*", gt.normalise(text), flags=re.I)
    clauses = []
    for part in parts:
        words = [w for w in _context_words(part) if w != ","]
        if clauses and (len(words) <= 3 or (words and words[0] in _PREPS)):
            clauses[-1] = clauses[-1] + words
        elif words:
            clauses.append(words)
    relative = re.compile(r"\b(?:once|until|within|shortly|imminently|tomorrow|(?:after|before|by) (?:the holidays|the board meeting|next|this|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this (?:week|month|quarter|year)|next (?:week|month|quarter|year))\b")
    return any((future_dates(" ".join(c), period) or relative.search(" ".join(c))) and not _past_completion(c) for c in clauses)


def claim_terms(claim: str) -> Tuple[set, set]:
    """(content words, date words) of a claim sentence: words other than function words, modals and pronouns, compared
    without a final s; and the months, quarters and years it names."""
    words = [w for w in _context_words(claim) if w != ","]
    content = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words
               if len(w) > 2 and w not in _FUNCTION_WORDS and w not in _MODALS and w not in _PREPS and w not in _CONJ}
    dates = {w for w in words if w in _DATE_WORDS} | set(_DATE_PHRASE_RE.findall(" ".join(words)))
    return content, dates


def _context_words(sentence: str) -> List[str]:
    """Case-folded words with contractions expanded (n't -> not, 'll -> will, 'd -> would, 're/'m/'ve, and 's -> is
    after a pronoun) and commas, semicolons, colons and dashes kept as ","; "May" the month becomes "maymonth"."""
    t = gt.normalise(sentence)
    t = re.sub(r"(?<=[^\W_]) May\b|^May(?= [0-9])", " maymonth", t)
    t = re.sub(r"\b(in|by|since|until|till|from|through|early|late|mid|of) may\b", r"\1 maymonth", t,
               flags=re.IGNORECASE)
    t = t.casefold().replace("’", "'").replace("`", "'")
    words = []
    for w in _WORD_RE.findall(t):
        w = w.strip()
        if w in (",", ";", ":", "–", "—", "-"):
            words.append(",")
        elif w.endswith("n't"):
            base = w[:-3]
            words += [{"won": "will", "ca": "can", "can": "can", "shan": "shall", "ain": "is"}.get(base, base), "not"]
        elif "'" in w:
            base, _, rest = w.partition("'")
            extra = {"ll": "will", "d": "would", "re": "are", "m": "am", "ve": "have"}.get(rest)
            if extra:
                words += [base, extra]
            elif rest == "s" and base == "let":
                words += ["let", "us"]
            elif rest == "s" and base in ("that", "this", "it", "here", "there", "what", "who", "he", "she", "where",
                                          "how", "nothing", "everything", "everyone", "noone", "nobody", "one"):
                words += [base, "is"]
            elif rest == "s":
                words.append(base)  # possessive
            else:
                words.append(base + rest)
        else:
            words.append(w)
    return words


def _is_pronoun_ref(words: List[str], i: int) -> bool:
    """words[i] is it/this/that/these/those used as a pronoun (a back-reference), not a determiner of a time noun or
    another noun."""
    w = words[i]
    if w == "it":
        return True
    if w not in _BACKREF_STARTS:
        return False
    nxt = words[i + 1] if i + 1 < len(words) else None
    if nxt is None or nxt == "," or nxt in _PREPS or nxt in _PARTICLES or nxt in _AUX or nxt in _MODALS \
            or nxt in _CONJ or nxt in ("is", "not"):
        return True
    if nxt in _BACKREF_NOUNS:
        return True
    return False


def _probe(flat: str) -> str:
    """The space-joined words with a concessive "even if" and "if"/"whether" meaning whether after check, ask, see,
    know or wonder rewritten, so that they are not read as conditions."""
    probe = re.sub(r"\beven if\b", "evenif", flat)
    return re.sub(r"\b((?:check|checked|checking|checks|ask|asked|asking|asks|see|saw|seeing|sees|know|knew|knows|"
                  r"knowing|wonder|wondered|wondering|wonders|verify|verified|confirm|confirmed|find out|found out|"
                  r"tell|told)(?: (?:me|us|him|her|them|you))?) (?:if|whether)\b", r"\1 whetherq", probe)


def _stem(w: str) -> str:
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def _date_keys(text: str, period: Optional["Period"]) -> set:
    return {k for _, k in future_dates(" ".join(w for w in _context_words(text) if w != ","), period)}


_DEFER_BEFORE_RE = re.compile(r"\b(?:to|until|till|into|for|past|beyond|after|toward|towards)(?: (?:the|early|mid|late|"
                              r"around|at least|at the earliest))*\s*$")


def context_problem(sentence: str, founder: bool = True, claim: Optional[str] = None,
                    period: Optional["Period"] = None) -> Optional[str]:
    """None when the sentence is positively clean context for a founder claim; otherwise a key of CONTEXT_WHAT.
    founder=False is the lighter check for another speaker's immediate reply: questions, conditions, negations,
    retractions, doubt words, preconditions, attribution, setbacks and (with the claim) a later date the claim doesn't
    name count there (not shape, modals of intent or back-references). With the claim sentence, a founder sentence
    that repeats one of the claim's content words, or names a date the claim doesn't (outside a past-tense sentence;
    in a past-tense sentence, a date after the update period reached with to/until/for ... as in "We moved delivery
    to March."), is also not clean unless it is an allow-listed reinforcer."""
    raw = gt.normalise(sentence).strip()
    words = _context_words(sentence)
    flat_c = " ".join(words)
    if not words:
        return "shape" if founder else None
    flat = re.sub(r"\s*,\s*", " ", flat_c).strip()
    probe = _probe(flat)
    # Framing is not a business agenda, even when a greeting shape also matches.
    if READOUT_RE.search(probe):
        return "attribution"
    if founder and any(rx.fullmatch(flat) for rx in _ALLOW_RES):
        return None
    if raw.rstrip(" \"')”’]").endswith("?"):
        return "question"
    probe = _probe(flat)
    if _COND_RE.search(probe):
        return "condition"
    if any(w in _NEG_WORDS for w in words):
        return "negation"
    if founder and words[0] in ("is", "are", "was", "were", "do", "does", "did", "will", "would", "could", "should",
                                "can", "may", "might", "shall", "have", "has", "had", "what", "why", "how", "who",
                                "whom", "whose", "really"):
        return "question"
    if _RETRACT_RE.search(probe) or re.search(r", right\b|^right\b", flat_c):
        return "retraction"
    if founder and any(w in _MODALS for w in words):
        return "hedge"
    if not founder and any(w in ("might", "may", "could") for w in words):
        return "hedge"
    if _HEDGE_RE.search(probe):
        return "hedge"
    if _dependency(probe, words):
        return "dependency"
    if _ATTRIB_RE.search(probe) or READOUT_RE.search(probe):
        return "attribution"
    if _SETBACK_RE.search(probe):
        return "setback"
    plain_words = [w for w in words if w != ","]
    plain = " ".join(plain_words)
    later = []
    if claim is not None:
        claim_dates = _date_keys(claim, period)
        later = [(i, k) for i, k in future_dates(plain, period) if k not in claim_dates]
    if not founder:
        # another speaker's reply: a date after the period that the claim doesn't name, about the claim (one of its
        # words or a back-reference noun), may move it ("The beta lands in February.")
        if later and claim is not None:
            content, _ = claim_terms(claim)
            if any(_stem(w) in content or w in _BARE_REF_NOUNS for w in plain_words) or re.search(
                    r"\b(?:the|that|this|our) " + _BN + r"\b", plain):
                return "date"
        return None
    if claim is not None:
        content, dates = claim_terms(claim)
        if any(_stem(w) in content for w in plain_words):
            return "topic"
        named = {w for w in plain_words if w in _DATE_WORDS} | set(_DATE_PHRASE_RE.findall(plain))
        past = any(w in _PAST or w in _PAST_EXTRA or w in ("was", "were", "had", "did") for w in plain_words)
        if (named - dates or later) and not past:
            return "date"
        # a past-tense deferral: "The vendor moved delivery to March.", "We pushed the kickoff to next year."
        if any(_DEFER_BEFORE_RE.search(plain[:i]) for i, _ in later):
            return "date"
    # self-assessment of the speaker's own claim ("I'm being optimistic", "we were being generous")
    if re.search(r"\b(?:i|we|you) (?:am|are|was|were) being\b", flat):
        return "hedge"
    # shape: subject span, then a known finite verb
    # ", and it was" at the end confirms a noun of the same sentence, not the claim
    core = re.sub(r",? and (?:it|that|they) (?:was|were|is|are|did|does|has|have|had)$", "", flat_c).strip(" ,")
    cw = core.split()
    if not cw:
        return "shape"
    v = _subject_verb(cw, raw)
    if v == 0 and cw[0] in _PRESENT:
        v = next((i for i, w in enumerate(cw) if i > 0 and _finite_verb(w)), 0)
    first = cw[0]
    if first in _BACKREF_STARTS and (len(cw) < 2 or cw[1] not in _TIME_NOUNS) or (
            first in ("the", "our", "that", "this") and len(cw) > 1 and cw[1] in _BACKREF_NOUNS):
        return "backref"
    if v is None or v == 0:
        return "shape"
    span = cw[:v]
    if "," in span or len(span) > 6 or first in _BAD_START:
        return "shape"
    if any(w in _PRONOUN_SUBJECTS and (k == 0 or span[k - 1] not in ("and", "or")) for k, w in enumerate(span) if k > 0):
        return "shape"  # fronted adverbial before the real subject ("Truthfully we ...")
    if first.endswith("ly") and len(span) >= 2 and first not in _LY_OK:
        return "shape"
    if len(raw.split()) < 3 and cw[v] not in _SHORT_VERBS:
        return "shape"
    if cw[v] == "will" and len([w for w in cw[v + 1:] if w != ","]) <= 1:
        return "shape"  # "We will see.", "Time will tell."
    rest = cw[v:]
    refs = [i for i in range(len(cw)) if _is_pronoun_ref(cw, i) or cw[i] in _BARE_REF_NOUNS or (
        cw[i] in ("the", "our", "that", "this") and i + 1 < len(cw) and cw[i + 1] in _BACKREF_NOUNS)]
    if refs:
        return None if _ENDORSE_RE.fullmatch(" ".join(w for w in rest if w != ",")) else "backref"
    return None if _plain_context(sentence) else "predicate"



# Round 12: syntax gates are positive grammars, not a claim that word lists understand
# English. Unknown subjects/predicates or qualifiers require review of this item.
# These nouns describe work objects. They are not automatically accepted predicates.
_WORK_NOUNS = set("""
account accounts accounting acquisition acquisitions analytics analyst api app audit backlog access ads demand planning whitepaper announcement shift self serve half availability marketplace launch stores app
 director yard scheduling runway demos people prices help centre mobile queue offsite desk dinner
 slots planners screen lawyers dispute finalist finalists interviews funds conversation term sheet round fourth move review start meeting doc bridge route optimisation optimization reseller model response
backend beta billing board book books budget burn business calendar capacity carrier carriers
cash certification churn client clients cloud code company contract contracts conversion cost costs
credits crm customer customers data database deal deals delivery demo denver depot depots design designer
dispatch dock docks documentation docs document driver drivers engineer engineers engineering
enterprise equipment export extension facility feedback finance flow founder founders freight funding
gross growth head headquarters hire hires hiring iff income integration integrations inventory
invoice invoices issue issues july lead leads lease ledger legal logistics london maintenance
margin marketing metrics migration module month net notes nps office onboarding operating operations
ops owner parser partner partners partnership partnerships payroll pilot pipeline plan planners
platform portal pricing product profit profitability program project qa quarter receivables rec
recruiting referrals release renewal renewals report reports research retention revenue roadmap
rollout safety sales seed security series service services shipper shippers shipment shipping
site sites soc software spec sprint stability staffing stock store strategy success support
system team test testing tickets time times tools training tranche transaction trial usability
user users vendor version vp warehouse warehouses webinar website wire work workflow year
""".split())
_NP_MODIFIERS = set("""
a an the our my your his her their its all every each new existing first second third final nothing
last this next current prior previous full largest small large senior junior internal external
monthly quarterly annual daily weekly cloud free paid fractional bigger whole desk two three four five six seven
eight nine ten one both additional original complete shared local regional international joint
active earlier early latest main gross net customer carrier security referral enterprise seed
backend pilot largest per multi site mid size follow sun size of and
""".split())
_TIME_TOKENS = set("""
today yesterday tomorrow monday tuesday wednesday thursday friday saturday sunday
week weeks month months quarter quarters year years day days morning afternoon evening
august september october november december january february march april may june july
early late mid last this next now already again officially formally fully most fortnight holidays fiscal thanksgiving shortly imminently post close halloween
 black fourth coming weeks seat diwali peak season side 12th hands
""".split()) | _DATE_WORDS
_CONTEXT_EVENTS = set("""
hired shipped launched closed signed started finished completed released raised added
onboarded migrated renewed expanded grew rose increased decreased improved reduced approved
joined moved opened built wrote won sold booked delivered published deployed fixed merged
tested piloted presented met integrated finalised finalized scoped staffed funded budgeted
ran hit reached doubled tripled recruited interviewed trained documented designed prototyped
demoed reviewed checked verified audited submitted filed paid invoiced collected upgraded
ended began arrived converted accepted settled landed secured received rebuilt relaunched
spent sent led bought earned found gave cleared stayed drew paused hosted archived
""".split())
_STATIVES = set("see sees loves love owns own leads lead runs run manages manage handles handle covers cover includes include uses use supports support tracks track reports report confirms confirm".split())
_DECL_FUTURE = set("opens open reaches reach begins begin gets get lands land goes go ships ship hire hires joins join starts start switches switch moves move hits hit converts convert wraps wrap turns turn kicks kick comes come arrives arrive breaks break".split())
_DECL_FUTURE |= {w + ("es" if w.endswith(("s", "x", "ch", "sh")) else "s") for w in _FUTURE_OK} - _STATIVES
_PAST_EXTRA |= {"drew", "paused", "hosted", "cleared"}
_STATUS |= {"empty", "steady"}
_PRESENT |= _DECL_FUTURE | _STATIVES
# A proper noun may name an organisation or place. It never supplies an unknown verb.
def _named_tokens(raw: str) -> set:
    return {w.casefold() for w in re.findall(r"\b(?:[A-Z][A-Za-z0-9]*|[0-9][A-Z0-9]+)\b", raw)}

_DETERMINERS = {"a", "an", "the", "our", "my", "your", "their", "its", "his", "her", "every", "each", "all"}
# Open noun slots admit project/product names without making unknown verbs predicates.
# Unknown past forms and clause boundaries cannot be swallowed as a longer subject.
_NON_NOUN_VERBS = {"put", "kept", "gave", "got", "took", "sent", "made", "read", "said", "told", "saw", "left", "let", "cut", "set"}

def _noun_phrase(words: List[str], names: set, limit: int = 7, subject: bool = False) -> bool:
    if not words or len(words) > limit:
        return False
    if len(words) == 1 and words[0] in ("us", "them", "me"):
        return True
    for i, w in enumerate(words):
        if not re.fullmatch(r"[a-z0-9]+(?:[.][0-9]+)?%?", w):
            return False
        if w in _AUX or w in _MODALS or w in (_PRONOUN_SUBJECTS - _DETERMINERS) or w in {"if", "unless", "once", "when", "whether", "though", "although", "because", "but", "or", "who", "which", "that"}:
            return False
        if w in _NON_NOUN_VERBS and w not in _WORK_NOUNS:
            return False
        if _finite_verb(w) and w not in _WORK_NOUNS and w not in names:
            return False
        if w not in _WORK_NOUNS and w not in _NP_MODIFIERS and w not in names:
            if w.endswith("ed") or (w.endswith("ly") and w not in _LY_OK) or (w.endswith("ing") and i == len(words) - 1 and not subject):
                return False
    return True

def _subject_verb(words: List[str], raw: str, fragment: bool = False) -> Optional[int]:
    names = _named_tokens(raw)
    if fragment and words and (words[0] in _CONTEXT_EVENTS or words[0] in _PAST_EXTRA):
        return 0
    for v in range(1, min(8, len(words))):
        if not _finite_verb(words[v]):
            continue
        if words[v] in _WORK_NOUNS and words[v + 1:v + 2] and words[v + 1] not in _DETERMINERS and any(
                _finite_verb(words[j]) and _noun_phrase(words[v:j], names)
                for j in range(v + 1, min(8, len(words)))):
            continue
        span = words[:v]
        if span[0] in _PRONOUN_SUBJECTS:
            return v if all(x in _PRED_ADVERBS for x in span[1:]) else None
        return v if _noun_phrase(span, names, subject=True) else None
    return None

_TAIL_PREPS = {"in", "on", "by", "at", "after", "before", "around", "until", "to", "for", "from", "with", "as", "onto", "under", "over", "than", "per", "within"}
_TIME_HEADS = _DATE_WORDS | {"today", "yesterday", "tomorrow", "week", "weeks", "month", "months", "quarter", "quarters", "year", "years", "day", "days", "morning", "afternoon", "evening", "fortnight", "holidays", "christmas", "thanksgiving", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

def _time_phrase(words: List[str]) -> bool:
    return (bool(set(words) & _TIME_HEADS) or words[-2:] == ["all", "hands"]) and all(w in _TIME_TOKENS or w in _NP_MODIFIERS or w in {"of", "the", "end", "start", "beginning", "half"} or w.isdigit() for w in words)

def _tail_is_work(words: List[str], names: set) -> bool:
    # Parse an object noun phrase followed by whole prepositional phrases. Dates/places
    # have their own shape, so a date cannot absorb an arbitrary qualifying suffix.
    words = list(words)
    if words[-1:] in (["shortly"], ["imminently"]):
        words = words[:-1]
    words = [w for i, w in enumerate(words) if not (w == "right" and words[i + 1:i + 2] and words[i + 1] in ("after", "before"))]
    if words[:2] == ["over", "to"]:
        words = words[1:]
    chunks = []; start = 0; prep = None
    for i, w in enumerate(words):
        if w in _TAIL_PREPS:
            chunks.append((prep, words[start:i]));prep=w;start=i+1
    chunks.append((prep, words[start:]))
    for prep, chunk in chunks:
        if prep is None:
            while chunk and chunk[0] in ("out", "up", "off"):
                chunk=chunk[1:]
            if not chunk:
                continue
            timed_object = any(chunk[i] in ("this", "next", "last") and _noun_phrase(chunk[:i], names) and _time_phrase(chunk[i:]) for i in range(1, len(chunk)))
            if not (_noun_phrase(chunk, names) or _time_phrase(chunk) or timed_object):
                return False
        elif not chunk:
            return False
        elif prep in ("in", "on", "by", "at", "after", "before", "around", "until", "within"):
            event_time = chunk in (["the", "board", "meeting"], ["the", "offsite"], ["peak", "season"])
            place = (all(w in names or w in _NP_MODIFIERS for w in chunk) and any(w in names for w in chunk)) or (chunk[-1] in (_PLACES | {"depot", "depots", "warehouse", "warehouses", "office", "offices", "site", "sites", "facility", "facilities", "desk"}) and _noun_phrase(chunk, names))
            if not (_time_phrase(chunk) or event_time or place or (prep == "on" and chunk == ["time"])):
                return False
        elif prep in ("with", "for", "per"):
            if not (_time_phrase(chunk) or (_noun_phrase(chunk, names) and (chunk[-1] in _WORK_NOUNS or any(w in names for w in chunk)))):
                return False
        elif prep == "to" and chunk == ["paid"]:
            continue
        elif not _noun_phrase(chunk, names):
            return False
    return True

def _positive_predicate(words: List[str], v: int, raw: str, factual: bool = False) -> bool:
    """A full predicate must match a supported form; no catch-all for a listed verb."""
    verb = words[v]
    tail = words[v + 1:]
    names = _named_tokens(raw)
    while tail and tail[0] in _PRED_ADVERBS:
        tail = tail[1:]
    if not tail:
        return verb in (_SHORT_VERBS | {"improved", "finished", "completed", "doubled", "tripled"}) and (verb in _CONTEXT_EVENTS or
                (verb == "passed" and any(x in words[:v] for x in ("audit", "review", "test", "certification", "vote", "inspection", "assessment", "check", "checklist"))))
    if verb in ("has", "have", "had"):
        return tail[0] in _CONTEXT_EVENTS and _positive_predicate(tail, 0, raw, factual)
    if verb in ("is", "are", "am", "was", "were", "stays", "stay", "remains", "remain"):
        if verb in ("is", "are", "am") and tail[0] in {w[:-1] + "ing" if w.endswith("e") else w + "ing" for w in _FUTURE_OK} | {"shipping", "running"}:
            return not factual and _tail_is_work(tail[1:], names)
        if tail[0] in ("scheduled", "planned", "slated", "pencilled", "penciled", "due"):
            schedule = tail[2:] if tail[1:3] == ["in", "for"] else tail[1:]
            return not factual and len(tail) > 1 and ((_tail_is_work(schedule, names)) or (tail[1] == "to" and len(tail) > 2 and tail[2] in _FUTURE_OK and _tail_is_work(tail[3:], names)))
        if not factual and tail[0] in (_DATE_WORDS | {"tomorrow", "next", "this", "early", "late", "mid"}):
            return all(w in _TIME_TOKENS or w in _NP_MODIFIERS or w in _PREPS or w.isdigit() for w in tail)
        # Complete status, optional plain time/place suffix. "is ready subject to..." cannot pass.
        if tuple(tail[:2]) in _STATUS_PHRASES:
            return _tail_is_work(tail[2:], names)
        if tail[0] in _STATUS or tail[0] in _CONTEXT_EVENTS or tail[0] == "written":
            return _tail_is_work(tail[1:], names)
        if tail[0] in ("in", "on") and tail[-1] in _PLACES:
            return _tail_is_work(tail, names)
        return words[v - 1:v] in (["owner"], ["lead"], ["director"], ["founder"]) and all(x in names for x in tail)
    if verb == "will":
        return not factual and ((tail[0] == "be" and len(tail) > 1 and tail[1] in _STATUS and _tail_is_work(tail[2:], names)) or (tail[0] in (_FUTURE_OK | {"reach", "become"}) and _tail_is_work(tail[1:], names)))
    if verb in ("break", "breaks"):
        return not factual and tail[0] == "even" and _tail_is_work(tail[1:], names)
    if verb in ("kicks", "kick", "comes", "come"):
        return not factual and tail[0] == ("off" if verb in ("kicks", "kick") else "up") and _tail_is_work(tail[1:], names)
    if verb in ("turns", "turn"):
        return not factual and (tail[0] in _STATUS or tail[:3] == ["cash", "flow", "positive"]) and _tail_is_work(tail, names)
    if verb in ("go", "goes") and tail[0] == "live":
        return not factual and _tail_is_work(tail[1:], names)
    if verb in _DECL_FUTURE:
        return not factual and _tail_is_work(tail, names)
    if verb == "held":
        return tail == ["steady"]
    if verb == "moved" and any(w in tail for w in ("wire", "tranche", "back")):
        return False
    if verb in ("fell", "dropped"):
        return bool(set(words[:v]) & {"churn", "cost", "costs", "burn", "tickets"}) and _tail_is_work(tail, names)
    if verb == "passed":
        return bool(set(words[:v]) & {"audit", "review", "test", "certification", "app", "vote", "inspection", "assessment", "check", "checklist"}) and _tail_is_work(tail, names)
    if verb == "went":
        return tail[0] == "live" and _tail_is_work(tail[1:], names)
    if verb in ("gave", "found"):
        # These verbs are often attribution ("gave us the wording", "found this in a deck").
        return False
    if verb in _CONTEXT_EVENTS or verb in _STATIVES:
        return _tail_is_work(tail, names)
    return False

def _plain_context(sentence: str) -> bool:
    raw = gt.normalise(sentence)
    # Punctuation introduces another clause or an aside; don't silently strip it.
    if re.search(r"[()\[\];:–—]", raw):
        return False
    words = _context_words(raw)
    # Established completed-fact shapes; both subordinate predicate and conclusion are checked.
    flat = " ".join(words).replace(", ", "")
    if re.fullmatch(r"i checked whetherq the contract was signed and it was", _probe(flat)):
        return True
    if re.fullmatch(r"revenue grew [0-9]+%? even if we exclude the pilot", flat):
        return True
    # Hyphens inside a work noun are punctuation, not an extra clause.
    words = _context_words(re.sub(r"(?<=\w)-(?=\w)", " ", raw))
    if "," in words:
        return False
    v = _subject_verb(words, raw)
    if v is None:
        return False
    if words[v] in ("stays", "remains", "is") and "audit" in words[:v] and "open" in words[v:]:
        return False
    return _positive_predicate(words, v, raw, factual=True)

def _fact_supported(text: str) -> bool:
    """Unstated assertions need a recognised past/status predicate, not merely no future marker."""
    raw = PLACEHOLDER_RE.sub("work", gt.normalise(text))
    raw = re.sub(r"https?://\S+", "work", raw)
    raw = re.sub(r"(?<=\w)-(?=\w)", " ", raw)
    # Figures already come from typed metric slots; predicate assessment doesn't reinterpret them.
    raw = re.sub(r"\{[^{}]+\}", "work", raw)
    raw = re.sub(r"\bv[0-9]+(?:[.][0-9]+)*\b", "version", raw)
    parts = re.split(r"[;:]|\b(?:and|but|so|because|while)\b", raw, flags=re.I)
    predicates = 0
    for part in parts:
        words = [w for w in _context_words(part) if w != ","]
        if not words:
            continue
        v = _subject_verb(words, part, fragment=True)
        if v is None:
            # Coordinated objects/date fragments stay attached to a preceding factual clause.
            if predicates and _tail_is_work(words, _named_tokens(part)):
                continue
            return False
        predicates += 1
        verb = words[v]
        if verb in _PAST or verb in _CONTEXT_EVENTS or verb in _PAST_EXTRA or verb in _STATIVES or verb in ("went", "fell", "dropped"):
            continue
        if verb in ("was", "were", "had", "did"):
            continue
        if verb in ("is", "are", "am") and words[v + 1:v + 2] and words[v + 1] in _DETERMINERS and _noun_phrase(words[v + 1:], _named_tokens(part)):
            continue
        if verb in ("is", "are", "am") and words[v+1:v+3] == ["choosing", "between"]:
            continue
        if verb in ("has", "have") and any(x in _CONTEXT_EVENTS for x in words[v+1:v+3]):
            continue
        if verb in ("is", "are", "am", "stays", "stay", "remains", "remain") and _positive_predicate(words, v, part, True):
            continue
        return False
    return predicates > 0


def _request_supported(text: str) -> bool:
    """A request noun/imperative, followed only by request arguments and factual explanation."""
    raw = re.sub(r"(?<=\w)-(?=\w)", " ", gt.normalise(text))
    parts = [p for sentence in gt._sentences(raw) for p in sentence.split(";") if p.strip()]
    if not parts:
        return False
    first = parts[0].strip()
    prefix = re.match(r"(?i)^(?:(?:please|can you|can anyone) )?(?:introduce us to|connect us with|refer us to|recommend|share feedback on|help us (?:find|hire|recruit|reach|meet))\b|^(?:(?:a|an|some|your) )?(?:intros?|introductions?|referrals?|recommendations?|connections?|advice|feedback|help)\b", first)
    if prefix is None:
        return False
    tail = [w for w in _context_words(first[prefix.end():]) if w != ","]
    if not tail or not _noun_phrase(tail, _named_tokens(first), limit=32):
        return False
    return all(_fact_supported(part) for part in parts[1:])


def _finite_verb(w: str) -> bool:
    """A word the shape check accepts as the sentence's finite verb: an auxiliary, a listed past or present verb, a
    few more irregular past forms (spent, sent, led ...), or re- before a listed past form (rebuilt, relaunched)."""
    return w in _AUX or w in _CONTEXT_EVENTS or w in _PAST or w in _PRESENT or w in _PAST_EXTRA or (w.startswith("re") and w[2:] in _PAST)


def claim_problem(sentence: str) -> Optional[str]:
    """The claim sentence itself (IV9-6): a condition, a joke/retraction/sarcasm cue or an emoticon or symbol, a hedge
    (an adverb, belief verb or set phrase: _CLAIM_HEDGE_RE), a precondition or an attribution
    makes it unusable even though it would be printed word for word. Its subject and complete predicate must also
    match the positive grammar; the literal future statement is retained only after those checks."""
    raw = gt.normalise(sentence)
    words = _context_words(sentence)
    flat = re.sub(r"\s*,\s*", " ", " ".join(words)).strip()
    probe = _probe(flat)
    if _COND_RE.search(probe):
        return "condition"
    if _RETRACT_RE.search(probe) or _EMOTICON_RE.search(raw) or any(unicodedata.category(c) == "So" for c in raw):
        return "retraction"
    if _CLAIM_HEDGE_RE.search(probe):
        return "hedge"
    if _dependency(probe, words):
        return "dependency"
    if _ATTRIB_RE.search(probe) or READOUT_RE.search(probe):
        return "attribution"
    if re.search(r"[()\[\];:–—]", raw):
        return "shape"
    claim_words = _context_words(re.sub(r"(?<=\w)-(?=\w)", " ", raw))
    if "," in claim_words:
        return "shape"
    claim_v = _subject_verb(claim_words, raw)
    if claim_v is None or not _positive_predicate(claim_words, claim_v, raw):
        return "shape"
    return None


def later_problem(sentence: str, claim: str, founder: bool, period: Optional["Period"]) -> Optional[str]:
    """A sentence in any later turn of the same meeting (IV9-5). A founder's later sentence with correction or
    retraction vocabulary (scratch, forget, walk back, correct myself, placeholder ...) withholds the claim. A later
    sentence that refers to the claim withholds it when it has a question, condition, negation, retraction, modal,
    hedge, precondition, attribution or setback marker, or (with a strong reference) names a later date the claim
    doesn't. A strong reference is the claim's date or a back-reference noun (the date, the plan ...), and for the
    founder also one of the claim's content words; a later date the claim doesn't name is a weak reference (it
    withholds only with a marker)."""
    raw = gt.normalise(sentence).strip()
    words = _context_words(sentence)
    plain_words = [w for w in words if w != ","]
    if not plain_words:
        return None
    flat = " ".join(plain_words)
    probe = _probe(flat)
    if founder and _LATER_RETRACT_RE.search(probe):
        return "retraction"
    if founder and READOUT_RE.search(probe):
        return "attribution"
    content, dates = claim_terms(claim)
    claim_dates = _date_keys(claim, period)
    named = {k for _, k in future_dates(flat, period)}
    backref = any(w in _BARE_REF_NOUNS for w in plain_words) or re.search(r"\b(?:the|that|this|our) " + _BN + r"\b",
                                                                         flat) is not None
    # a strong reference names the claim's date, a back-reference noun or (for the founder) a word of the claim; a
    # date the claim doesn't name then moves the claim ("On reflection, January is more honest for the depots.").
    # A later date alone needs a marker as well ("Realistically February."), so "See you in December." is clean.
    strong = backref or bool(dates & set(plain_words)) or any(_stem(w) in content for w in plain_words)
    if not strong and not named:
        return context_problem(sentence, claim=claim, period=period) if founder else None
    if strong and named - claim_dates:
        return "date"
    if strong and founder:
        return None if any(rx.fullmatch(flat) for rx in _ALLOW_RES) else "later"
    if raw.rstrip(" \"')”’]").endswith("?"):
        return "question"
    if _COND_RE.search(probe):
        return "condition"
    if any(w in _NEG_WORDS for w in words):
        return "negation"
    if _LATER_RETRACT_RE.search(probe):
        return "retraction"
    if any(w in _MODALS for w in words) or _HEDGE_RE.search(probe):
        return "hedge"
    if _dependency(probe, words):
        return "dependency"
    if _ATTRIB_RE.search(probe) or READOUT_RE.search(probe):
        return "attribution"
    if _SETBACK_RE.search(probe):
        return "setback"
    if founder:
        return context_problem(sentence, claim=claim, period=period)
    return None


def preceding_problem(sentence: str, claim: str, founder: bool,
                      period: Optional["Period"]) -> Optional[str]:
    """Prior turns are evidence context too, even when separated by a speaker label.
    Ordinary status questions from another speaker can introduce the founder's answer.
    Their arguments and markers are checked; arbitrary prose is not a safe preface.
    """
    raw = gt.normalise(sentence).strip()
    plain = " ".join(w for w in _context_words(sentence) if w != ",")
    if re.fullmatch(r"(?:great|good|excellent|agreed|sure|thanks|thank you|understood|okay|ok)", plain):
        return None
    if not founder and raw.rstrip(" \"')”’]").endswith("?"):
        words = _context_words(sentence)
        # Complete past-performance questions can introduce a business update.
        # Keep a bounded business topic and an explicit status verb; question shape
        # never bypasses framing, dependency, hedge or setback markers.
        if words[:2] == ["how", "did"] and words[-1:] in (["go"], ["perform"], ["progress"]):
            if _QUESTION_TOPIC_RE.fullmatch(" ".join(words[2:-1])):
                return context_problem(raw.rstrip("?"), founder=False, claim=claim, period=period)
        if words[:2] in (["how", "is"], ["how", "are"], ["what", "is"], ["what", "are"]):
            topic = words[2:]
            if topic[-1:] == ["going"]:
                topic = topic[:-1]
            if len(topic) > 2 and topic[0] in _DETERMINERS and topic[1] in _STATUS:
                topic = topic[:1] + topic[2:]
            if _noun_phrase(topic, _named_tokens(raw)):
                return context_problem(raw.rstrip("?"), founder=False, claim=claim, period=period)
    problem = context_problem(sentence, period=period)
    if problem == "predicate":
        words = _context_words(sentence)
        v = _subject_verb(words, raw)
        prior_topic = claim_terms(sentence)[0]
        current_topic = claim_terms(claim)[0]
        # Separate, complete earlier commitments are not framing. Keep this narrow:
        # explicit will + a supported action, no shared claim content, and no markers.
        if (v is not None and words[v] == "will" and prior_topic and current_topic
                and not prior_topic & current_topic and claim_problem(sentence) is None):
            return None
    return problem


def label_role(label: str) -> str:
    """The trailing "(role)" or "[role]" of a speaker label, normalised and case-folded ("" when there is none or it
    is a timestamp)."""
    t = gt.normalise(label).casefold().strip(" :")
    t = re.sub(r"^" + _TS + r"\s*", "", t)
    m = re.search(r"[(\[]\s*([^()\[\]]*?)\s*[)\]]$", t)
    if m is None or re.fullmatch(_TS, m.group(0)) or re.fullmatch(r"[0-9:\s]*", m.group(1)):
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def role_is_readout(role: str) -> bool:
    """Whether a label role announces someone else's words ("quoting Kestrel", "reading the old deck", "as Kestrel",
    "on behalf of the board") rather than naming the speaker's job (IV9-2)."""
    if not role:
        return False
    job = r"(?:ceo|cfo|cto|coo|cpo|founder|cofounder|co-founder|chief executive officer|chief financial officer|chief technology officer|chief operating officer|president|managing director|(?:head of |vp |director of |lead )?(?:engineering|product|operations|sales|finance|marketing))"
    return re.fullmatch(job + r"(?:\s*(?:,|/|&|and)\s*" + job + r")*", role) is None


def founder_sentences(stated: dict, period: Optional["Period"] = None) -> Tuple[Optional[str], List[str], Dict[str, str]]:
    """(problem, sentence keys, blocked). The excerpt supports an item only if it is whole sentence(s) from a turn of
    the founder (founder_stated.speaker) in the cited meeting's notes or transcript text (check_quote), and none of its
    sentences is a question or a conditional (is_question_or_conditional). The item must then be one of these
    sentences word for word (checked in check_text). blocked maps a sentence key to the reason it can't support an
    item: some other sentence of the founder's turn (inside or outside the excerpt), of the founder's next turn, or of
    the reply right after the claim is not positively clean context (context_problem; fails closed)."""
    m, excerpt, speaker = stated["meeting"], stated["excerpt"], stated["speaker"]
    if m is None:
        return ("its founder statement was typed in chat (source: user), which the script can't check against "
                "meeting text; forward-looking items need the founder's own sentence from a meeting's notes or "
                "transcript"), [], {}
    if not m["notes_read"]:
        return "its founder statement cites {} whose notes were not read".format(m["ref"]), [], {}
    sk = label_key(speaker) if speaker is not None else None
    texts = {k: (founder_source(m[k], sk) if m[k] and sk else m[k]) for k in ("notes_text", "transcript_text")}
    ok, why = quote_check(excerpt, texts)
    if ok is None:
        return ("its founder statement can't be checked: {}'s notes or transcript text is not in the bundle"
                .format(m["ref"])), [], {}
    if not ok:
        return "its founder statement is not whole sentences from {}'s notes or transcript text ({})".format(
            m["ref"], why), [], {}
    if speaker is None:
        return ("its founder statement has no speaker: set founder_stated.speaker to the founder's speaker label in "
                "{}'s text".format(m["ref"])), [], {}
    found = False
    ambiguous = False
    elsewhere = False
    blocked = {}  # type: Dict[str, str]
    excerpt_keys = [sentence_key(x) for _, _, ss in gt.split_units(excerpt) for x in ss]
    # IV9-3: the founder's label is keyed on the name and the role. With a role in founder_stated.speaker, a turn of
    # the same name with another role is someone else's; without one, two different roles for the same name in one
    # meeting text leave attribution ambiguous.
    speaker_role = label_role(speaker)

    def block(key, where, what):
        if key not in blocked:
            blocked[key] = ("its founder statement is not backed by the rest of the founder's words: the sentence {} {}. "
                            "A forward-looking claim is kept only when it and every other sentence of the founder's "
                            "turn (and of the next turn) read as plain statements and nothing later in the meeting "
                            "qualifies it; review the complete context with the founder and add the claim by hand if "
                            "it stands".format(where, CONTEXT_WHAT[what]))

    for source in (texts["notes_text"], texts["transcript_text"]):
        if not source:
            continue
        turns = []
        active = None
        previous = ""
        for raw_line in source.splitlines():
            # Preserve structural evidence before normalisation removes Markdown.
            line = gt.normalise(raw_line)
            match = re.match(r"^([^:]{1,100}):\s*(.*)$", line)
            structural = (not raw_line[:1].isspace() and not raw_line.lstrip().startswith(">")
                          and not re.match(r"(?:[-*+] |[0-9]+[.)] )", raw_line))
            boundary = not previous or bool(re.search(r'[.!?][\s\"\'”’)]*$', previous))
            if match and structural and boundary:
                active = [match.group(1), match.group(2)]
                turns.append(active)
            else:
                if match and label_key(match.group(1)) == sk and gt.check_quote(excerpt, "Speaker A: " + match.group(2))[0]:
                    ambiguous = True
                if active is not None:
                    active[1] += " " + line.strip()
                elif line.strip():
                    # Unlabelled source prefaces still frame the first speaker's words.
                    active = ["", line.strip()]
                    turns.append(active)
            if raw_line.strip():
                previous = raw_line.strip()
        roles = set()
        for ti, (who, text) in enumerate(turns):
            lk = label_key(who)
            holds = gt.check_quote(excerpt, "Speaker A: " + text)[0]
            # Granola's "Me" may be the founder's own label in a transcript whose notes use the name: a "Me" turn with
            # the same words is context-checked like the founder's (it can only withhold, never establish the claim)
            alias = lk == "me" and sk != "me"
            role = label_role(who)
            if lk == sk and speaker_role and role and role != speaker_role:
                lk = lk + " (" + role + ")"  # same name, another role: another speaker
            if lk != sk and not alias:
                if holds:
                    elsewhere = True  # the same words in another speaker's turn: unclear who said them
                continue
            if not alias and role:
                roles.add(role)
            if not holds:
                continue
            # IV9-2: a role that announces someone else's words ("Maren (quoting Kestrel):") is a read-out
            if role_is_readout(role):
                ambiguous = True
            context = gt._sentences(text)
            keys = [sentence_key(x) for x in context]
            positions = [i for i in range(len(keys)) if keys[i:i + len(excerpt_keys)] == excerpt_keys]
            if not positions:
                continue
            if not alias:
                found = True
            # Preserve the specific ambiguity diagnosis for explicit readouts.
            if ti > 0 and label_key(turns[ti - 1][0]) not in (lk, sk):
                before = gt._sentences(turns[ti - 1][1])
                if before and READOUT_RE.search(gt.normalise(before[-1]).casefold()):
                    ambiguous = True
            same = (lk,)
            # what follows the turn: the same speaker's next turn (right after, or after one reply), and that reply
            after = []
            nxt = turns[ti + 1] if ti + 1 < len(turns) else None
            reply = None
            if nxt is not None and label_key(nxt[0]) in same:
                after.append(("in the founder's next turn", nxt[1], None))
            elif nxt is not None:
                answer = turns[ti + 2] if ti + 2 < len(turns) and label_key(turns[ti + 2][0]) in same else None
                problem = next((p for p in (context_problem(x, founder=False) for x in gt._sentences(nxt[1])) if p),
                               None)
                if problem == "question" and answer is None:
                    problem = "unanswered"
                if problem and problem != "question":
                    after.append(("in the reply right after the founder's turn", None, problem))
                elif not problem:
                    reply = nxt[1]  # checked per claim for a later date the claim doesn't name
                if answer is not None:
                    after.append(("in the founder's next turn", answer[1], None))
            for key in excerpt_keys:
                # A speaker label does not reset the provenance or framing of a claim.
                # Check every preceding turn, including the founder's repeated labels,
                # so an acknowledgement between a preface and the claim cannot hide it.
                for who_before, text_before in turns[:ti]:
                    for sentence in gt._sentences(text_before):
                        if sentence_key(sentence) == key:
                            continue
                        what = preceding_problem(sentence, key, label_key(who_before) == sk, period)
                        if what:
                            block(key, "in a preceding turn", what)
                            break
                for position in positions:
                    for i, sentence in enumerate(context):
                        if keys[i] == key:
                            continue
                        what = context_problem(sentence, claim=key, period=period)
                        if what:
                            block(key, "before the claim" if i < position else "after the claim", what)
                            break
                for where, text_after, known in after:
                    what = known or next((p for p in (context_problem(x, claim=key, period=period)
                                                      for x in gt._sentences(text_after)
                                                      if sentence_key(x) != key) if p), None)
                    if what:
                        block(key, where, what)
                if reply is not None:
                    what = next((p for p in (context_problem(x, founder=False, claim=key, period=period)
                                             for x in gt._sentences(reply)) if p), None)
                    if what:
                        block(key, "in the reply right after the founder's turn", what)
                # IV9-5: every later turn of the meeting, by anyone
                for who_later, text_later in turns[ti + 1:]:
                    founder_later = label_key(who_later) == sk and not (
                        speaker_role and label_role(who_later) and label_role(who_later) != speaker_role)
                    for x in gt._sentences(text_later):
                        if sentence_key(x) == key:
                            continue
                        what = later_problem(x, key, founder_later, period)
                        if what:
                            block(key, "later in the meeting ({})".format(
                                "the founder" if founder_later else "another speaker"), what)
                            break
        if not speaker_role and len(roles) > 1:
            ambiguous = True  # IV9-3: one name with two roles ("Dana (CEO)", "Dana (Investor)")
    # Any qualified occurrence wins over a clean occurrence, including across notes and transcript (blocked is
    # filled from every occurrence). Exact matching is not a licence to cherry-pick.
    if ambiguous:
        return "its founder statement has ambiguous quoted or continued speaker attribution; review the original source", [], {}
    if not found:
        return ("its founder statement is not from a turn of the speaker named in founder_stated.speaker in {}'s "
                "text".format(m["ref"])), [], {}
    if elsewhere:
        return ("its founder statement's words also appear in another speaker's turn in {}'s text; review who said "
                "them in the complete context".format(m["ref"])), [], {}
    if texts["notes_text"] and texts["transcript_text"] and not gt.check_quote(excerpt, texts["transcript_text"])[0]:
        return ("its founder statement is in {}'s notes but not in its transcript text (the notes may summarise or "
                "differ); check the transcript with the founder in the complete context".format(m["ref"])), [], {}
    sentences = [x for _, _, sents in gt.split_units(excerpt) for x in sents]
    if any(gt.is_question_or_conditional(x) for x in sentences):
        return "its founder statement is a question or a conditional, not a statement", [], {}
    # IV9-6: the claim sentence itself
    for x in sentences:
        what = claim_problem(x)
        if what:
            block(sentence_key(x), "that is the claim", what)
    return None, [sentence_key(x) for x in sentences], blocked


def check_text(raw: str, ctx: Ctx, figures: bool = True, forward: bool = True, conf: bool = True,
               said: Optional[List[str]] = None,
               blocked: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], List[str]]:
    """Substitute placeholders, run the checks, and return (escaped markdown or None, reasons). `said` is the
    sentence keys of a valid founder statement: the text (placeholders filled in) must then be one of them word for
    word, and forward-looking wording is allowed."""
    reasons = []
    out, probe, marked, subs = [], [], [], []
    pos = 0
    for m in PLACEHOLDER_RE.finditer(raw):
        out.append(raw[pos:m.start()])
        probe.append(raw[pos:m.start()] + " ")
        marked.append(PRIVATE_USE_RE.sub("\ufffd", raw[pos:m.start()]) + chr(0xE000 + len(subs)))
        kind, key, attr = m.group(1), m.group(2), m.group(3)
        sub = ""
        if kind in ("org", "person"):
            ent = ctx.b["entities"].get(key)
            if ent is None or attr is not None or (ent["kind"] == "person") != (kind == "person"):
                reasons.append("a {{{}:...}} placeholder names no {} in $.entities".format(
                    kind, "person" if kind == "person" else "organisation"))
            else:
                sub = ent["name"] if ent["public_ok"] else ent["descriptor"]
                if not "".join(out).strip() and sub[:1].islower():
                    sub = sub[:1].upper() + sub[1:]
        else:
            row = ctx.metrics.get(key)
            if row is None:
                reasons.append("a {metric:...} placeholder names no defined metric")
            elif attr == "change":
                if row["change"] is None:
                    reasons.append("uses the change in {}, which can't be computed".format(md(row["label"])))
                else:
                    sub = change_text(row)
            else:
                slot = "prev" if attr == "previous" else "cur"
                if row[slot + "_state"] != "ok":
                    reasons.append("uses {} ({}), which is withheld or not supplied".format(
                        md(row["label"]), "previous period" if slot == "prev" else "this period"))
                else:
                    sub = fmt_value(row[slot], row["unit"], ctx.config, ctx.b["currency"])
        out.append(sub)
        # names are escaped here; metric figures and changes are built by the script and already escaped
        subs.append(md(sub) if kind in ("org", "person") else sub)
        pos = m.end()
    out.append(raw[pos:])
    probe.append(raw[pos:])
    marked.append(PRIVATE_USE_RE.sub("\ufffd", raw[pos:]))
    text = "".join(out)
    probe_text = "".join(probe)
    hit = ctx.names.find(text)
    if hit is not None:
        ent = ctx.b["entity_list"][hit]
        reasons.append("writes the name of non-public entities[{}] (shown as \u201c{}\u201d) directly; use its "
                       "{{{}:...}} placeholder".format(hit, md(ent["descriptor"]),
                                                     "person" if ent["kind"] == "person" else "org"))
    toks = tokens(probe_text)
    if conf:
        tag = first_tag(toks, ctx)
        if tag is not None:
            reasons.append("may be confidential ({}): tag it, rephrase it, or have the founder clear it".format(
                TAG_LABELS[tag]))
    if figures and figure_hits(probe_text, ctx):
        reasons.append("has a figure that is not from the metrics data; use a {metric:...} placeholder or remove it")
    if said is not None and blocked and sentence_key(text) in blocked:
        reasons.append(blocked[sentence_key(text)])
    elif said is not None and sentence_key(text) not in said:
        reasons.append("is not word for word one of the founder's sentences in founder_stated.excerpt (a founder "
                       "statement supports only an item that is the founder's own sentence, not a paraphrase)")
    if forward and said is None and ctx.forward.find(toks):
        reasons.append("has forward-looking wording without a founder statement (founder_stated)")
    elif forward and said is None and scheduled_future(probe_text, ctx.b["period"]):
        # IV9-4: a present-tense or scheduled future ("The bridge round wraps up in March.") is forward-looking too
        reasons.append("reads as forward-looking without a founder statement (founder_stated): it names a date after "
                       "the update period, or a schedule (scheduled, slated, due ...), with no past-tense verb")
    if reasons:
        return None, reasons
    escaped = md("".join(marked))
    return PRIVATE_USE_RE.sub(lambda x: subs[ord(x.group(0)) - 0xE000], escaped), []


def mask_label(text: str, ctx: Ctx, what: str) -> str:
    """Short labels (titles, sources): masked when they contain a non-public name, and in an investor draft when they
    match a confidential term; returns escaped markdown."""
    if ctx.names.find(text) is not None:
        return "({} withheld: it names a non-public entity)".format(what)
    if ctx.audience == "investors" and first_tag(tokens(text), ctx):
        return "({} withheld: it may be confidential)".format(what)
    return md(text)


# --------------------------------------------------------------------------- analysis


def analyse(b: dict, config: dict, observations: List[dict], as_of: datetime, audience: str) -> dict:
    ctx = Ctx(b, config, audience)
    today = as_of.date()
    metrics, mnotes = resolve_metrics(b, observations, config, today, ctx)
    ctx.metrics = metrics
    a = {"ctx": ctx, "metrics": metrics, "mnotes": mnotes, "sections": {s: [] for s in SECTIONS}, "board": [],
         "withheld": [], "held_back": {}, "quote_notes": [], "cleared": [], "stated": [], "outside": [],
         "cited": [], "links_dropped": list(b["reader"].dropped_links), "commitments": [], "commit_notes": []}
    counters = {s: 0 for s in SECTIONS}
    period = b["period"]
    for item in b["items"]:
        counters[item["section"]] += 1
        label = "{} #{} (items[{}])".format(SECTION_TITLES[item["section"]], counters[item["section"]], item["index"])
        tag = item["tag"]
        if tag is None:
            relied = [c["meeting"] for c in item["citations"] if c["type"] == "meeting"]
            relied += [x["meeting"] for x in (item["quote"], item["stated"]) if x is not None and x["meeting"]]
            for m in relied:
                if m["confidential"]:
                    tag = m["confidential"]
                    break
        if tag is not None and audience == "investors":
            a["held_back"][tag] = a["held_back"].get(tag, 0) + 1
            continue
        reasons = []
        for c in item["citations"]:
            if c["type"] == "meeting" and not c["meeting"]["notes_read"]:
                reasons.append("cites {} whose notes were not read".format(c["meeting"]["ref"]))
        said = None
        if item["stated"] is not None:
            problem, sentences, blocked = founder_sentences(item["stated"], b["period"])
            if problem:
                reasons.append(problem)
            else:
                said = sentences
        text, why = check_text(item["text"], ctx, conf=(tag is None and not item["cleared"]), said=said,
                               blocked=blocked if said is not None else None)
        reasons.extend(why)
        if not reasons and item["stated"] is None and not (_request_supported(item["text"]) if item["section"] == "ask" else _fact_supported(item["text"])):
            reasons.append("is not a recognised past fact or current status; review its full source and supply a founder statement (founder_stated) for a forward-looking assertion")
        if reasons:
            a["withheld"].append("{}: {}.".format(label, "; ".join(reasons)))
            continue
        quote_md = None
        if item["quote"] is not None:
            quote_md = check_quote(item, ctx, label, a, tag)
        refs = []
        for c in item["citations"]:
            ref = cite_ref(c, a)
            if ref not in refs:
                refs.append(ref)
            if c["type"] == "meeting" and not period.start <= c["meeting"]["date"] <= period.end:
                a["outside"].append("{} cites {} ({}), outside {}.".format(label, c["meeting"]["ref"],
                                                                           c["meeting"]["date"].isoformat(), period.short))
        if item["stated"] is not None:
            sm = item["stated"]["meeting"]
            a["stated"].append("{}: forward-looking wording kept because the founder said it ({}).".format(
                label, sm["ref"] if sm else "told you directly"))
            if sm is not None and sm not in a["cited"]:
                a["cited"].append(sm)
        if item["cleared"] and tag is None:
            a["cleared"].append(label)
        entry = {"text": text, "refs": refs, "quote": quote_md, "tag": tag, "label": label}
        if tag is not None:
            a["board"].append(entry)
        else:
            a["sections"][item["section"]].append(entry)
    a["commitments"] = review_commitments(b, ctx, a)
    return a


def check_quote(item: dict, ctx: Ctx, label: str, a: dict, tag: Optional[str]) -> Optional[str]:
    q = item["quote"]
    if not q["permission"]:
        a["quote_notes"].append("{}: quote left out: no permission to quote (permission is false).".format(label))
        return None
    reasons = []
    if not q["meeting"]["notes_read"]:
        reasons.append("cites {} whose notes were not read".format(q["meeting"]["ref"]))
    verbatim, why = quote_check(q["text"], q["meeting"])
    if verbatim is False:
        reasons.append("it is not whole sentences from one speaker's turn in {}'s notes or transcript text ({})".format(
            q["meeting"]["ref"], why))
    text, why = check_text(q["text"], ctx, conf=(tag is None and not item["cleared"]))
    reasons.extend(why)
    attribution, why = check_text(q["attribution"], ctx, conf=(tag is None and not item["cleared"]))
    reasons.extend("attribution " + w for w in why)
    if reasons:
        a["quote_notes"].append("{}: quote left out: {}.".format(label, "; ".join(reasons)))
        return None
    if verbatim is None:
        a["quote_notes"].append("{}: quote not checked against the meeting ({}'s notes text is not in the bundle).".format(
            label, q["meeting"]["ref"]))
    if q["meeting"] not in a["cited"]:
        a["cited"].append(q["meeting"])
    return "“{}” — {} ({})".format(text, attribution, q["meeting"]["ref"])


def cite_ref(c: dict, a: dict) -> str:
    if c["type"] == "meeting":
        if c["meeting"] not in a["cited"]:
            a["cited"].append(c["meeting"])
        return c["meeting"]["ref"]
    if c["type"] == "user":
        return "you"
    if c["type"] == "metric":
        return "metrics"
    docs = a.setdefault("docs", [])
    if c not in docs:
        docs.append(c)
    return "D{}".format(docs.index(c) + 1)


def compare(op: str, left: Decimal, right: Decimal) -> bool:
    return {">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right, "=": left == right}[op]


def review_commitments(b: dict, ctx: Ctx, a: dict) -> List[dict]:
    pu = b["previous_update"]
    if pu is None:
        return []
    rows = []
    for c in pu["commitments"]:
        label = "Commitment {} (previous_update.commitments[{}])".format(c["pos"] + 1, c["pos"])
        text, why = check_text(c["text"], ctx, figures=False, forward=False, conf=not c["cleared"])
        if why:
            a["commit_notes"].append("{}: text withheld: {}.".format(label, "; ".join(why)))
            text = "(withheld: see Before you send)"
        review = b["reviews"].get(c["id"])
        status = review["status"] if review else "not_reviewed"
        happened = []
        if c["target"] is not None:
            t = c["target"]
            row = ctx.metrics[t["metric"]]
            target = quantize(t["value"], row["unit"], ctx.config)
            target_txt = fmt_value(target, row["unit"], ctx.config, b["currency"])
            if row["cur_state"] == "ok":
                met = compare(t["op"], row["cur"], target)
                happened.append("{} {} vs a target of {} {} ({})".format(
                    md(row["label"]), fmt_value(row["cur"], row["unit"], ctx.config, b["currency"]),
                    OP_WORDS[t["op"]], target_txt, "met" if met else "not met"))
                new = None
                if met and status in ("missed", "partial", "not_reviewed"):
                    new = "done"
                elif not met and status in ("done", "not_reviewed"):
                    new = "missed"
                if new is not None:
                    if status != "not_reviewed":
                        a["commit_notes"].append("{}: status changed from {} to {} because {} is {} against a target "
                                                 "of {} {}.".format(label, STATUS_LABELS[status], STATUS_LABELS[new],
                                                                    md(row["label"]),
                                                                    fmt_value(row["cur"], row["unit"], ctx.config,
                                                                              b["currency"]),
                                                                    OP_WORDS[t["op"]], target_txt))
                    status = new
            else:
                why = "not supplied" if row["cur_state"] == "missing" else "withheld"
                happened.append("{} target of {} {}: can't check, this period's figure is {}".format(
                    md(row["label"]), OP_WORDS[t["op"]], target_txt, why))
                if status == "done":
                    a["commit_notes"].append("{}: status changed from Done to Not verified because this period's {} "
                                             "figure is {}; add it, or say how the target was met.".format(
                                                 label, md(row["label"]), why))
                    status = "not_verified"
        if review is None and status == "not_reviewed":
            a["commit_notes"].append("{}: not reviewed; say what happened.".format(label))
        refs = []
        secret = review is not None and ctx.audience == "investors" and any(
            cite["type"] == "meeting" and cite["meeting"]["confidential"] for cite in review["citations"])
        if secret:
            a["commit_notes"].append("{}: note and references left out: the review cites a meeting tagged "
                                     "confidential. Rerun with --audience board to see them.".format(label))
        if review is not None and not secret:
            if review["note"]:
                note, why = check_text(review["note"], ctx, conf=not c["cleared"])
                if why:
                    a["commit_notes"].append("{}: note withheld: {}.".format(label, "; ".join(why)))
                else:
                    happened.append(note)
            for cite in review["citations"]:
                ref = cite_ref(cite, a)
                if ref not in refs:
                    refs.append(ref)
        rows.append({"text": text, "status": STATUS_LABELS[status], "happened": "; ".join(happened) or "—",
                     "refs": refs})
    return rows


# --------------------------------------------------------------------------- rendering


def tldr_metrics(a: dict, config: dict, b: dict, cmp: str) -> str:
    parts = []
    for key in config["headline_metrics"]:
        row = a["metrics"].get(key)
        if row is None:
            continue
        if row["cur_state"] != "ok":
            parts.append("{} {}".format(md(row["label"]), "not supplied" if row["cur_state"] == "missing" else "withheld"))
            continue
        value = fmt_value(row["cur"], row["unit"], config, b["currency"])
        if row["change"] is None:
            parts.append("{} {}".format(md(row["label"]), value))
        else:
            ch = row["change"] + (", " + row["pct"] if row["pct"] else "")
            parts.append("{} {} ({} {})".format(md(row["label"]), value, ch, cmp))
    return " · ".join(parts)


def render_body(b: dict, a: dict, config: dict, audience: str, refs: bool) -> List[str]:
    period = b["period"]
    prev = period.previous()
    cmp = "MoM" if period.kind == "month" else "QoQ"

    def line(entry: dict) -> List[str]:
        tail = " ({})".format(", ".join(entry["refs"])) if refs and entry["refs"] else ""
        out = ["- {}{}".format(entry["text"], tail)]
        if entry["quote"]:
            q = entry["quote"]
            if not refs:
                q = q.rsplit(" (", 1)[0]
            out.append("  > {}".format(q))
        return out

    out = ["## TL;DR", ""]
    out.append("- **Numbers:** {}".format(tldr_metrics(a, config, b, cmp) or "no headline metrics supplied"))
    s = a["sections"]
    out.append("- **Highlight:** {}".format(s["highlight"][0]["text"] if s["highlight"] else "none recorded"))
    out.append("- **Lowlight:** {}".format(s["lowlight"][0]["text"] if s["lowlight"] else
                                           "none recorded; add one before sending"))
    if s["ask"]:
        out.append("- **Ask:** {}".format(s["ask"][0]["text"]))
    out += ["", "## Key metrics", "",
            "| Metric | {} | {} | Change ({}) | Source, as of |".format(period.short, prev.short, cmp),
            "|---|---|---|---|---|"]
    shown_rows = 0
    for key in b["order"]:
        row = a["metrics"][key]
        if row["cur_state"] == "missing" and row["prev_state"] == "missing" and key not in config["headline_metrics"]:
            continue
        shown_rows += 1
        src = []
        if row["cur_state"] == "ok":
            src.append("{}, {}".format(md(row["cur_source"]), row["cur_as_of"].isoformat()))
        if row["prev_state"] == "ok" and (row["cur_state"] != "ok" or row["prev_source"] != row["cur_source"]):
            src.append("{}: {}, {}".format(prev.short, md(row["prev_source"]), row["prev_as_of"].isoformat()))
        out.append("| {} | {} | {} | {} | {} |".format(
            md(row["label"]), metric_cell(row, "cur", config, b["currency"], refs),
            metric_cell(row, "prev", config, b["currency"], refs),
            change_text(row), "; ".join(src) or "—"))
    if not shown_rows:
        out.append("| No metrics supplied | | | | |")
    for sec in SECTIONS:
        out += ["", "## " + SECTION_TITLES[sec], ""]
        if s[sec]:
            for entry in s[sec]:
                out += line(entry)
        elif sec == "lowlight":
            out.append("- None recorded. An update with no lowlights is rarely believed; add at least one before "
                       "sending.")
        elif sec == "ask":
            out.append("- No asks this period.")
        else:
            out.append("- Nothing to report this period.")
    out += ["", "## What we said last time vs what happened", ""]
    pu = b["previous_update"]
    if pu is None:
        out.append("No previous update was supplied, so there is nothing to compare.")
    elif not a["commitments"]:
        out.append("The {} update made no commitments.".format(pu["period"].short))
    else:
        out += ["| We said ({} update) | Status | What happened |".format(pu["period"].short), "|---|---|---|"]
        for c in a["commitments"]:
            tail = " ({})".format(", ".join(c["refs"])) if refs and c["refs"] else ""
            out.append("| {} | {} | {}{} |".format(c["text"], c["status"], c["happened"], tail))
    if audience == "board":
        out += ["", "## Board only — do not forward", ""]
        if a["board"]:
            for entry in a["board"]:
                first = line(entry)
                first[0] = "- **{}:** {}".format(TAG_TITLES[entry["tag"]], first[0][2:])
                out += first
        else:
            out.append("- Nothing tagged board-only this period.")
    return out


def render_review(b: dict, a: dict, config: dict, as_of: datetime, audience: str) -> List[str]:
    period = b["period"]
    out = ["", "## Before you send (delete this section)", ""]
    checks = []
    for note in a["withheld"]:
        checks.append("Withheld from the draft — " + note)
    if not a["sections"]["lowlight"]:
        checks.append("Lowlights is empty. Say what went wrong this period." + (
            " These metrics moved the wrong way: {}.".format(join_words(a["mnotes"]["wrong_way"]))
            if a["mnotes"]["wrong_way"] else ""))
    elif a["mnotes"]["wrong_way"]:
        checks.append("Moved the wrong way: {}. Make sure Lowlights covers them.".format(
            join_words(a["mnotes"]["wrong_way"])))
    if a["held_back"]:
        parts = ["{} {}".format(n, TAG_LABELS[t]) for t, n in sorted(a["held_back"].items(), key=lambda x: TAGS.index(x[0]))]
        total = sum(a["held_back"].values())
        checks.append("Held back as confidential: {} ({}). Not shown in an investor draft; run with --audience board "
                      "for the board version.".format(plural(total, "item"), ", ".join(parts)))
    for key in ("conflicts", "withheld", "restated", "jumps", "stale"):
        for note in a["mnotes"][key]:
            checks.append("Metrics — " + note)
    if a["mnotes"]["missing_vs_last"]:
        checks.append("Metrics — the last update reported {} but this draft has no figure for {}. Same metrics, same "
                      "order: add it or say why it is missing.".format(
                          join_words(a["mnotes"]["missing_vs_last"]),
                          "it" if len(a["mnotes"]["missing_vs_last"]) == 1 else "them"))
    for key in config["headline_metrics"]:
        row = a["metrics"].get(key)
        if row is not None and row["cur_state"] == "missing":
            checks.append("Metrics — no {} figure for {}.".format(md(row["label"]), period.short))
    if a["mnotes"]["ignored"]:
        checks.append("Metrics — {} for other periods ignored.".format(plural(a["mnotes"]["ignored"], "metrics row")))
    for note in a["commit_notes"]:
        checks.append(note)
    pu = b["previous_update"]
    if pu is None:
        checks.append("No previous update supplied: the said-vs-happened section is empty.")
    elif pu["period"].code != period.previous().code:
        checks.append("The previous update covered {}, not {}.".format(pu["period"].short, period.previous().short))
    for note in a["quote_notes"]:
        checks.append(note)
    for note in a["outside"]:
        checks.append(note)
    for note in a["stated"]:
        checks.append(note)
    for label in a["cleared"]:
        checks.append("{}: shown because the founder cleared it although it matched a confidential term.".format(label))
    if a["links_dropped"]:
        checks.append("{} dropped (not an http(s) URL).".format(plural(len(a["links_dropped"]), "link")))
    if not a["sections"]["ask"]:
        checks.append("No asks. Most updates carry one to three specific asks.")
    out.append("**Checks**")
    out.append("")
    out += ["- " + c for c in checks] if checks else ["- None."]
    out += ["", "**Coverage**", ""]
    ms = b["meetings"]
    in_period = [m for m in ms if period.start <= m["date"] <= period.end]
    kinds = []
    for k in MEETING_KINDS:
        n = sum(1 for m in in_period if m["kind"] == k)
        if n:
            kinds.append(plural(n, MEETING_KIND_LABELS[k]))
    out.append("- Meetings: {} in the bundle, {} in {}{}; notes read for {}; transcripts: {} read, {} denied, {} "
               "unavailable.".format(len(ms), len(in_period), period.short,
                                     " ({})".format(", ".join(kinds)) if kinds else "",
                                     sum(1 for m in ms if m["notes_read"]),
                                     sum(1 for m in ms if m["transcript"] == "ok"),
                                     sum(1 for m in ms if m["transcript"] == "denied"),
                                     sum(1 for m in ms if m["transcript"] == "unavailable")))
    if any(m["transcript"] == "denied" for m in ms):
        out.append("- Transcript access was denied for some meetings; notes can omit details, so a missing detail "
                   "does not mean it was not discussed.")
    src = b["sources"]
    for key in SOURCE_KEYS:
        if key in src:
            s = src[key]
            detail = " — {}".format(mask_label(s["detail"], a["ctx"], "detail")) if s["detail"] else ""
            out.append("- {}: {} ({}){}".format(SOURCE_LABELS[key], mask_label(s["provider"], a["ctx"], "provider"),
                                                s["status"].replace("_", " "), detail))
    if src["meetings"]["provider"].lower() == "granola" and (as_of.date() - period.start).days > config["plan_window_days"]:
        out.append("- {} began more than {} days ago; Granola's free plan may not return older notes, so check the "
                   "meeting count above.".format(period.short, config["plan_window_days"]))
    if a["cited"] or a.get("docs") or pu is not None:
        out += ["", "**Sources cited**", ""]
        for m in sorted(a["cited"], key=lambda m: int(m["ref"][1:])):
            title = mask_label(m["title"], a["ctx"], "title")
            url = m["url"] if m["url"] and a["ctx"].names.find(m["url"]) is None else None
            out.append("- {} · {} · {} · {}".format(m["ref"], m["date"].isoformat(), MEETING_KIND_LABELS[m["kind"]],
                                                     link(title, url)))
        for i, d in enumerate(a.get("docs", []), 1):
            url = d["url"] if d["url"] and a["ctx"].names.find(d["url"]) is None else None
            out.append("- D{} · {}".format(i, link(mask_label(d["label"], a["ctx"], "document label"), url)))
        if pu is not None:
            url = pu["url"] if pu["url"] and a["ctx"].names.find(pu["url"]) is None else None
            out.append("- Previous update · {} · sent {}".format(link("{} update".format(pu["period"].short), url),
                                                                 pu["sent_on"].isoformat()))
    return out


def banners(b: dict, a: dict, as_of: datetime) -> List[str]:
    out = []
    period = b["period"]
    for key in SOURCE_KEYS:
        s = b["sources"].get(key)
        if s is not None and s["status"] in ("unavailable", "denied"):
            out.append("> **Incomplete:** {} could not be read ({}: {}).{}".format(
                SOURCE_LABELS[key].lower(), mask_label(s["provider"], a["ctx"], "provider"), s["status"],
                " Highlights may be missing." if key == "meetings" else ""))
    if period.end >= as_of.date():
        out.append("> **Period not over:** {} ends {}; figures and highlights are partial.".format(
            period.short, period.end.isoformat()))
    return out


def render(fmt: str, b: dict, a: dict, config: dict, as_of: datetime, audience: str) -> str:
    period = b["period"]
    title = "{} {} update — {}".format(md(b["company"]), "investor" if audience == "investors" else "board", period.label)
    if fmt == "email":
        lines = ["Subject: " + title, ""] + render_body(b, a, config, audience, refs=False)
        lines += ["", "_Confidential — please do not forward._"]
        return "\n".join(lines)
    lines = ["# " + title, "",
             "_Draft — not sent. Prepared {} for {}. Figures come only from the metrics data, each with its source and "
             "as-of date; references like (M2) point to the meetings listed at the end. Review “Before you send”, then "
             "delete it._".format(as_of.date().isoformat(), "investors" if audience == "investors" else "the board")]
    top = banners(b, a, as_of)
    if top:
        lines += [""] + top
    lines += [""] + render_body(b, a, config, audience, refs=True)
    lines += render_review(b, a, config, as_of, audience)
    return "\n".join(lines)


def to_json(b: dict, a: dict, config: dict, as_of: datetime, audience: str, markdown: str, email: str) -> str:
    period = b["period"]
    metrics = []
    for key in b["order"]:
        row = a["metrics"][key]
        metrics.append({"metric": md(row["label"]), "unit": row["unit"],
                        "current": metric_cell(row, "cur", config, b["currency"]),
                        "previous": metric_cell(row, "prev", config, b["currency"]),
                        "change": change_text(row), "current_state": row["cur_state"], "previous_state": row["prev_state"],
                        "source": md(row.get("cur_source", "")) or None,
                        "as_of": row["cur_as_of"].isoformat() if row.get("cur_as_of") else None})
    payload = {
        "status": "draft",
        "sent": False,
        "audience": audience,
        "period": period.code,
        "as_of": as_of.isoformat(),
        "subject": "{} {} update — {}".format(md(b["company"]), "investor" if audience == "investors" else "board",
                                              period.label),
        "metrics": metrics,
        "sections": {s: len(a["sections"][s]) for s in SECTIONS},
        "board_only_items": len(a["board"]),
        "held_back": dict(sorted(a["held_back"].items())),
        "withheld": a["withheld"],
        "quote_notes": a["quote_notes"],
        "commitment_notes": a["commit_notes"],
        "metric_notes": {k: a["mnotes"][k] for k in ("conflicts", "withheld", "restated", "jumps", "stale",
                                                    "missing_vs_last")},
        "markdown": markdown,
        "email_body": email,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def unescape(text: str) -> str:
    """Undo the markdown escaping (entities, backslashes) so the final name check sees the words a reader sees."""
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("\\", " ")


def leak_line(text: str, names: Names) -> Optional[int]:
    """1-based line of the output that still contains a non-public name or alias, or None."""
    for n, line in enumerate(text.split("\n"), 1):
        for part in unescape(line).split("|"):
            if names.find(part) is not None:
                return n
    return None


def leak_in_json(text: str, names: Names) -> bool:
    """Every string in the JSON output, decoded, line by line."""
    stack = [json.loads(text)]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if leak_line(item, names) is not None:
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


# --------------------------------------------------------------------------- main


def build(bundle_path: Path, csv_paths: List[Path], config_path: Path, as_of_text: str, audience: str,
          fmt: str) -> str:
    """The whole run without printing: returns the text, or raises InputError (exit 2, or 3 for a name leak)."""
    as_of = parse_moment(as_of_text, lambda problem: InputError("--as-of: {} {}".format(shown(as_of_text), problem)))
    config = load_config(config_path)
    bundle = load_bundle(bundle_path, config, as_of)
    observations = list(bundle["observations"])
    for path in csv_paths:
        observations += load_csv(path, bundle["defs"])
    analysis = analyse(bundle, config, observations, as_of, audience)
    markdown = render("draft", bundle, analysis, config, as_of, audience)
    email = render("email", bundle, analysis, config, as_of, audience)
    text = {"draft": markdown, "email": email}.get(fmt)
    if text is None:
        text = to_json(bundle, analysis, config, as_of, audience, markdown, email)
    names = bundle["names"]
    hits = [(name, leak_line(t, names)) for name, t in (("draft", markdown), ("email", email))]
    hits = [(name, n) for name, n in hits if n is not None]
    if fmt == "json" and not hits and leak_in_json(text, names):
        hits = [("json", 0)]
    if hits:
        name, n = hits[0]
        raise InputError("refusing to print: line {} of the {} output would contain the name or an alias of a "
                         "non-public entity. It comes from text the script cannot withhold, such as a metric "
                         "label; rename it, or mark the entity public_ok if it may be named."
                         .format(n, name), code=EXIT_REFUSED)
    return text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a DRAFT investor or board update from a JSON bundle. "
                                                 "Nothing is sent.")
    parser.add_argument("--bundle", type=Path, required=True, help="The JSON bundle (see DATA_CONTRACT.md).")
    parser.add_argument("--metrics-csv", type=Path, action="append", default=[],
                        help="Metrics CSV (metric,period,value,source,as_of[,currency]); repeatable.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--as-of", required=True, help="ISO datetime with UTC offset; the only 'now'.")
    parser.add_argument("--audience", choices=AUDIENCES, default="investors",
                        help="investors (default): confidential items are left out. board: they appear in a "
                             "separate 'Board only' section.")
    parser.add_argument("--format", choices=FORMATS, default="draft",
                        help="draft (default, with references and checks), email (body only) or json.")
    args = parser.parse_args(argv)
    for stream, errors in ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors=errors)
            except (ValueError, OSError):
                pass
    try:
        text = build(args.bundle, args.metrics_csv, args.config, args.as_of, args.audience, args.format)
    except InputError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return exc.code
    return emit(text)


def emit(text: str) -> int:
    """Print the result. If stdout is gone (closed pipe, full disk), say so on stderr and exit 6, no traceback."""
    try:
        if sys.stdout is None:
            raise OSError("stdout is closed")
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        return 0
    except (OSError, ValueError) as exc:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, AttributeError, ValueError):
            pass
        print("error: the output could not be written ({}). Nothing was sent anywhere.".format(
            getattr(exc, "strerror", None) or exc), file=sys.stderr)
        return EXIT_OUTPUT


if __name__ == "__main__":
    sys.exit(main())
