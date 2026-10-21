#!/usr/bin/env python3
"""Score deal qualification (MEDDICC, MEDDPICC, BANT, SPICED or your own framework) from one JSON bundle per deal.

Stdlib only, offline, deterministic, Python 3.8+. --as-of is the only "now"; the wall clock is never read.
The agent reads the meetings and the CRM and extracts evidence; this script does the checkable parts:

  * validates the framework, the config and each bundle (exit 2 names the file and the JSON path)
  * requires every finding to cite a meeting id that is in the bundle, and checks every quote against the meeting
    text it was given with granola_text.check_quote: one complete sentence, or several consecutive complete
    sentences, from a single speaker's turn (case and formatting ignored; partial sentences, ellipses, speaker
    labels and quotes that run across speakers are rejected). The check is mechanical; it doesn't judge whether a
    quote is representative, in context or correctly attributed. Quotes from a transcript that was not read are
    refused
  * classifies attendees (ours / the account / other), drops note-taker bots, excludes internal-only meetings
    and meetings with nobody from the account's domains, merges duplicate notes of the same meeting
  * computes each element's status (evidenced / partial / missing / contradicted) from the parts the
    customer side confirmed in fresh evidence, and flags: conflicting values across meetings, values the
    customer said changed, elements only our side asserted, unattributed evidence, stale evidence
    (older than stale_days) and CRM fields that disagree with the calls
  * picks 1-2 discovery questions for every element that is not evidenced (the agent's, or the framework's)
  * renders a one-page markdown scorecard, a CRM field-update DRAFT (never written anywhere), --json, or,
    with several bundles, one compact table across deals
  * redacts emails and phone numbers with granola_text.redact_contacts before any text is shortened (pattern-based;
    it can miss formats; phones only on a clear signal) and escapes every interpolated value with granola_text.md,
    so meeting text can't create links, mentions or formatting in the markdown output

It is a coverage checklist. It never computes a win probability, a forecast or a weighted score.

Exit codes: 0 output printed; 2 bad input (nothing on stdout); 3 single deal whose meeting-notes source is not
ok (nothing to score; nothing on stdout); 6 the output could not be written (closed pipe, full disk).
"""

import argparse
import importlib.util
import json
import os
import re
import stat
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
# granola_text.py is the shared quote-check, redaction and escaping module, shipped byte-for-byte next to this script.
# Load it from this folder whatever the working directory, and keep the folder flat (no __pycache__).
sys.dont_write_bytecode = True
_GT_SPEC = importlib.util.spec_from_file_location("granola_text", str(SCRIPT_DIR / "granola_text.py"))
gt = importlib.util.module_from_spec(_GT_SPEC)
_GT_SPEC.loader.exec_module(gt)
DEFAULT_CONFIG = SCRIPT_DIR / "scorecard.json"
DEFAULT_FRAMEWORK = SCRIPT_DIR / "framework_meddicc.json"

EXIT_BAD_INPUT, EXIT_SOURCE_NOT_OK, EXIT_OUTPUT = 2, 3, 6
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_configured"]
NOTES_PROVIDERS = ["granola", "gong", "fathom", "fireflies", "otter", "zoom", "pasted", "other"]
CRM_PROVIDERS = ["hubspot", "attio", "salesforce", "pipedrive", "other"]
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "pasted": "pasted transcripts", "other": "other",
                   "hubspot": "HubSpot", "attio": "Attio", "salesforce": "Salesforce", "pipedrive": "Pipedrive"}
PLANS = ["free", "basic", "plus", "business", "enterprise", "unknown"]
TRANSCRIPT_STATES = ["ok", "denied", "unavailable", "not_requested"]
TRANSCRIPT_RANK = {"ok": 0, "denied": 1, "unavailable": 2, "not_requested": 3}
MATCH_KINDS = ["domain", "title", "mention", "crm", "user"]
QUOTE_FROM = ["transcript", "notes"]
SIDES = ["customer", "ours", "unknown"]
FORMATS = ["scorecard", "crm-draft", "json"]
CONFIG_KEYS = ["schema_version", "stale_days", "max_quotes_per_element", "max_quote_chars", "min_quote_chars",
               "free_plan_window_days", "bot_email_patterns", "consumer_domains"]
STATUS_ORDER = ["contradicted", "missing", "partial", "evidenced"]
STATUS_LABEL = {"evidenced": "✅ Evidenced", "partial": "🟡 Partial", "missing": "❌ Missing",
                "contradicted": "⚠️ Contradicted"}
STATUS_WORD = {"evidenced": "Evidenced", "partial": "Partial", "missing": "Missing", "contradicted": "Contradicted"}
STATUS_ICON = {"evidenced": "✅", "partial": "🟡", "missing": "❌", "contradicted": "⚠️"}
PART_REASON = {"stale": "last confirmed {}, {} days ago", "unknown": "speaker not identified",
               "ours": "only our side said it", "none": "not heard"}
SIDE_RANK = {"customer": 0, "unknown": 1, "ours": 2}

# ASCII digits only; hours 00-23, minutes and seconds 00-59, offsets up to ±14:00. Checked before
# fromisoformat, so every Python from 3.8 to 3.14 accepts and rejects the same strings.
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DATETIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                         r"(?::[0-5][0-9](?:\.[0-9]{3}|\.[0-9]{6})?)?"
                         r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
MIN_YEAR, MAX_YEAR = 1900, 2999
MAX_CONFIG_INT = 3650
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:/_.-]{0,199}")
DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
EMAIL_ADDR_RE = re.compile(r"[^\s@]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
# Removed from the raw strings in --json: control characters, soft hyphen, zero-width space, word joiner, BOM, and the
# direction marks, embeddings, overrides and isolates that can reorder what a reader sees.
INVISIBLE_RE = re.compile("[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f\u00ad\u200b\u200e\u200f\u202a-\u202e"
                          "\u2060\u2066-\u2069\ufeff]")
URL_SAFE = "/:?#@!$&'*+,;=%~"
MAX_TEXT = 300            # titles, names, labels, claim values, questions
MAX_QUOTE = 2000          # a finding's quote (the scorecard shows up to max_quote_chars of it)
MAX_SOURCE_TEXT = 2000000  # notes_text / transcript_text per meeting
MAX_ITEMS = 2000          # meetings, findings, attendees per meeting
INTERNAL_FOOTER = ("_Internal deal review: a coverage checklist built from meeting evidence, not a forecast or "
                   "a win probability. Don't paste customer quotes into anything the customer will see._")






class InputError(Exception):
    """Bad input; the message names the file and the JSON path."""


# --------------------------------------------------------------------------- parsing helpers


def safe_url(value: Optional[str]) -> Optional[str]:
    """An absolute http(s) URL with a real host name and a valid port, percent-encoded; None otherwise."""
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port  # ValueError on a malformed or out-of-range port
        clean = quote(value, safe=URL_SAFE)
    except (ValueError, UnicodeError):
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or not DOMAIN_RE.fullmatch(host) or port == 0 \
            or parts.username is not None or parts.password is not None:
        return None
    return clean


def parse_day(value: str, fail) -> date:
    """Strict YYYY-MM-DD in ASCII digits, years 1900-2999."""
    if not DATE_RE.fullmatch(value):
        raise fail("{!r} is not a YYYY-MM-DD date".format(value))
    try:
        day = date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        raise fail("{!r} is not a real calendar date".format(value)) from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise fail("{!r} is outside the years {} to {}".format(value, MIN_YEAR, MAX_YEAR))
    return day


def parse_moment(value: str, fail) -> datetime:
    """Strict YYYY-MM-DDTHH:MM[:SS[.fff|.ffffff]] plus Z or ±HH:MM (up to ±14:00)."""
    if not DATETIME_RE.fullmatch(value):
        raise fail("{!r} is not an ISO-8601 datetime with an offset, like 2026-10-12T09:00:00-07:00".format(value))
    if not MIN_YEAR <= int(value[0:4]) <= MAX_YEAR:
        raise fail("{!r} is outside the years {} to {}".format(value, MIN_YEAR, MAX_YEAR))
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise fail("{!r} is not a real date and time".format(value)) from None


def path_kind(path: Path) -> str:
    """'file', 'folder', 'missing', 'other' or 'unreadable'; never raises."""
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
        json.dumps(data, ensure_ascii=False).encode("utf-8")  # an unpaired surrogate (\ud800) can't be printed
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
        raise InputError("{}: {} is not valid JSON ({})".format(label, path, str(exc)[:120])) from None
    except OSError as exc:
        raise InputError("{}: {} cannot be read ({})".format(label, path, exc.strerror or exc)) from None
    except RecursionError:
        raise InputError("{}: {} is nested too deeply".format(label, path)) from None


class Reader:
    """Typed accessors that report errors as '<label>: <json.path>: problem'."""

    def __init__(self, label: str):
        self.label = label

    def fail(self, path: str, problem: str) -> InputError:
        return InputError("{}: {}: {}".format(self.label, path, problem))

    def obj(self, value, path: str, required: bool = True) -> Optional[dict]:
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        return value

    def lst(self, value, path: str, required: bool = False, limit: int = MAX_ITEMS) -> list:
        if value is None:
            if required:
                raise self.fail(path, "is required (use [] when there are none)")
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        if len(value) > limit:
            raise self.fail(path, "has {} items; the limit is {}".format(len(value), limit))
        return value

    def text(self, parent: dict, key: str, path: str, required: bool = True, limit: int = MAX_TEXT) -> Optional[str]:
        value = parent.get(key)
        where = "{}.{}".format(path, key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise self.fail(where, "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail(where, "must be a string, got {}".format(type(value).__name__))
        if len(value) > limit:
            raise self.fail(where, "is {} characters long; the limit is {}".format(len(value), limit))
        return " ".join(value.split())

    def boolean(self, parent: dict, key: str, path: str, default: bool) -> bool:
        value = parent.get(key, default)
        if value is None:
            value = default
        if not isinstance(value, bool):
            raise self.fail("{}.{}".format(path, key), "must be true or false, got {!r}".format(value))
        return value

    def integer(self, parent: dict, key: str, path: str, minimum: int, maximum: int) -> int:
        value = parent.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise self.fail("{}.{}".format(path, key), "must be an integer from {} to {}, got {!r}".format(
                minimum, maximum, value if not isinstance(value, str) else value[:40]))
        return value

    def enum(self, parent: dict, key: str, path: str, allowed: List[str], default: Optional[str] = None) -> str:
        value = parent.get(key, default)
        if value is None:
            value = default
        if not isinstance(value, str) or value not in allowed:
            shown = value[:40] if isinstance(value, str) else value
            raise self.fail("{}.{}".format(path, key), "must be one of {}, got {!r}".format(", ".join(allowed), shown))
        return value

    def day(self, parent: dict, key: str, path: str, required: bool = True) -> Optional[date]:
        value = self.text(parent, key, path, required, limit=40)
        if value is None:
            return None
        return parse_day(value, lambda problem: self.fail("{}.{}".format(path, key), problem))

    def moment(self, parent: dict, key: str, path: str) -> datetime:
        value = self.text(parent, key, path, True, limit=40)
        return parse_moment(value, lambda problem: self.fail("{}.{}".format(path, key), problem))

    def url(self, parent: dict, key: str, path: str) -> Optional[str]:
        value = self.text(parent, key, path, required=False, limit=2000)
        if value is None:
            return None
        clean = safe_url(value)
        if clean is None:
            raise self.fail("{}.{}".format(path, key), "must be an http(s) URL with a real host name")
        return clean


def load_config(path: Path) -> dict:
    config = load_json(path, "config")
    r = Reader("config {}".format(path))
    r.obj(config, "$")
    missing = [key for key in CONFIG_KEYS if key not in config]
    if missing:
        raise InputError("config {}: missing key(s): {}".format(path, ", ".join(missing)))
    if config["schema_version"] != 1 or isinstance(config["schema_version"], bool):
        raise r.fail("$.schema_version", "must be 1")
    r.integer(config, "stale_days", "$", 1, MAX_CONFIG_INT)
    r.integer(config, "max_quotes_per_element", "$", 1, 20)
    r.integer(config, "max_quote_chars", "$", 40, MAX_QUOTE)
    r.integer(config, "min_quote_chars", "$", 1, 200)
    r.integer(config, "free_plan_window_days", "$", 1, MAX_CONFIG_INT)
    for key in ["bot_email_patterns", "consumer_domains"]:
        items = r.lst(config[key], "$." + key, required=True, limit=500)
        clean = []
        for i, item in enumerate(items):
            if not isinstance(item, str) or not item.strip() or len(item) > 200:
                raise r.fail("$.{}[{}]".format(key, i), "must be a non-empty string up to 200 characters")
            clean.append(item.strip().lower())
        config[key] = clean
    return config


def load_framework(path: Path) -> dict:
    fw = load_json(path, "framework")
    r = Reader("framework {}".format(path))
    r.obj(fw, "$")
    if fw.get("schema_version") != 1 or isinstance(fw.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1")
    out = {"key": r.text(fw, "key", "$", limit=40), "name": r.text(fw, "name", "$", limit=60), "elements": [],
           "crm_fields": []}
    if not KEY_RE.fullmatch(out["key"]):
        raise r.fail("$.key", "must be lower-case letters, digits and _ (up to 40), starting with a letter")
    seen, shorts = set(), set()
    elements = r.lst(fw.get("elements"), "$.elements", required=True, limit=12)
    if not elements:
        raise r.fail("$.elements", "must list at least one element")
    for i, el in enumerate(elements):
        p = "$.elements[{}]".format(i)
        r.obj(el, p)
        e = {"key": r.text(el, "key", p, limit=40), "short": r.text(el, "short", p, limit=4),
             "label": r.text(el, "label", p, limit=60), "description": r.text(el, "description", p),
             "parts": [], "topics": []}
        if not KEY_RE.fullmatch(e["key"]) or e["key"] in seen:
            raise r.fail(p + ".key", "must be a unique key of lower-case letters, digits and _")
        if e["short"] in shorts:
            raise r.fail(p + ".short", "must be unique, got {!r} twice".format(e["short"]))
        seen.add(e["key"])
        shorts.add(e["short"])
        part_keys = set()
        parts = r.lst(el.get("parts"), p + ".parts", required=True, limit=6)
        if not parts:
            raise r.fail(p + ".parts", "must list at least one part")
        for j, part in enumerate(parts):
            pp = "{}.parts[{}]".format(p, j)
            r.obj(part, pp)
            item = {"key": r.text(part, "key", pp, limit=40), "label": r.text(part, "label", pp, limit=80),
                    "question": r.text(part, "question", pp)}
            if not KEY_RE.fullmatch(item["key"]) or item["key"] in part_keys:
                raise r.fail(pp + ".key", "must be a unique key of lower-case letters, digits and _")
            part_keys.add(item["key"])
            e["parts"].append(item)
        folded = set()
        for j, topic in enumerate(r.lst(el.get("topics"), p + ".topics", required=True, limit=10)):
            if not isinstance(topic, str) or not topic.strip() or len(topic) > 60:
                raise r.fail("{}.topics[{}]".format(p, j), "must be a non-empty string up to 60 characters")
            topic = " ".join(topic.split())
            if fold(topic) in folded:
                raise r.fail("{}.topics[{}]".format(p, j), "repeats topic {!r}".format(topic))
            folded.add(fold(topic))
            e["topics"].append(topic)
        out["elements"].append(e)
    by_key = {e["key"]: e for e in out["elements"]}
    fields = set()
    for i, cf in enumerate(r.lst(fw.get("crm_fields"), "$.crm_fields", required=True, limit=20)):
        p = "$.crm_fields[{}]".format(i)
        r.obj(cf, p)
        item = {"field": r.text(cf, "field", p, limit=40), "label": r.text(cf, "label", p, limit=60),
                "element": r.text(cf, "element", p, limit=40), "topic": r.text(cf, "topic", p, limit=60)}
        if not KEY_RE.fullmatch(item["field"]) or item["field"] in fields:
            raise r.fail(p + ".field", "must be a unique key of lower-case letters, digits and _")
        fields.add(item["field"])
        if item["element"] not in by_key:
            raise r.fail(p + ".element", "is not an element of this framework")
        if item["topic"] not in by_key[item["element"]]["topics"]:
            raise r.fail(p + ".topic", "is not one of the topics of element {!r}".format(item["element"]))
        out["crm_fields"].append(item)
    return out


# --------------------------------------------------------------------------- text helpers


def fold(text: str) -> str:
    """Case- and punctuation-insensitive key used for matching titles and topics: Unicode NFKC, case-folded,
    letters and digits of any script kept (accents too). Text with no letters or digits keys on itself."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", text).split()) or " ".join(text.split())


def canon(value: str) -> str:
    """Comparison key for claim and CRM values: a YYYY-MM-DD date as itself; otherwise case-folded letters and
    digits in any script, so "Ines Marlowe" == "ines marlowe" and "48,000" == "48000"."""
    if DATE_RE.fullmatch(value):
        return "date:" + value
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def clean_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if domain.startswith("@"):
        domain = domain[1:]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def in_domains(domain: str, domains: List[str]) -> bool:
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


def is_bot(email: str, pattern: str) -> bool:
    """'bot@' must be the whole local part (talbot@ is a person); a domain pattern (read.ai) must be the address's
    domain or a parent of it (ann@bread.ai.example is a person); anything else is a substring match."""
    if pattern.endswith("@"):
        return email.startswith(pattern)
    if "@" not in pattern and DOMAIN_RE.fullmatch(pattern):
        return in_domains(domain_of(email), [pattern])
    return pattern in email


def short_date(d: date, as_of: date) -> str:
    text = "{} {}".format(MONTHS[d.month - 1], d.day)
    return text if d.year == as_of.year else "{}, {}".format(text, d.year)


def long_date(d: date) -> str:
    return "{} {}, {}".format(MONTHS[d.month - 1], d.day, d.year)


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return "{} {}".format(n, word if n == 1 else (many or word + "s"))


def join_words(items: List[str], last: str = "and") -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " {} ".format(last) + items[-1]


def redact(text: str) -> str:
    """A raw value for --json: the email addresses and phone numbers granola_text recognises removed (pattern-based;
    it can miss formats), then invisible and control characters dropped and whitespace collapsed to one line."""
    return " ".join(INVISIBLE_RE.sub("", gt.redact_contacts(text)).split())


def md(text: str) -> str:
    """A value for the markdown output: contact details removed first, then escaped by granola_text.md so it can't
    form a link, autolink, image, raw HTML, mention, math, highlight, emphasis, code, heading or table-cell break."""
    return gt.md(gt.redact_contacts(text))


def link(label_md: str, url: Optional[str]) -> str:
    """`label_md` is already escaped; `url` was checked by safe_url."""
    return "[{}]({})".format(label_md, url) if url else label_md


# --------------------------------------------------------------------------- bundle


def load_bundle(path: Path, internal: List[str], config: dict, fw: dict, as_of: datetime) -> dict:
    data = load_json(path, "bundle")
    r = Reader("bundle {}".format(path))
    r.obj(data, "$")
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1")
    b = {"path": str(path)}

    acc = r.obj(data.get("account"), "$.account")
    b["account"] = {"name": r.text(acc, "name", "$.account"), "domains": []}
    for i, d in enumerate(r.lst(acc.get("domains"), "$.account.domains", required=True, limit=20)):
        if not isinstance(d, str):
            raise r.fail("$.account.domains[{}]".format(i), "must be a string")
        dom = clean_domain(d)
        where = "$.account.domains[{}]".format(i)
        if not DOMAIN_RE.fullmatch(dom):
            raise r.fail(where, "{!r} is not a domain like ostrevane.example".format(d[:60]))
        if in_domains(dom, config["consumer_domains"]):
            raise r.fail(where, "{} is a personal email provider, not the account's domain".format(dom))
        if in_domains(dom, internal) or any(in_domains(x, [dom]) for x in internal):
            raise r.fail(where, "{} overlaps your --internal-domain".format(dom))
        if dom not in b["account"]["domains"]:
            b["account"]["domains"].append(dom)
    if not b["account"]["domains"]:
        raise r.fail("$.account.domains", "must list at least one email domain of the account")

    src = r.obj(data.get("sources"), "$.sources")
    notes = r.obj(src.get("meeting_notes"), "$.sources.meeting_notes")
    b["notes_source"] = {"provider": r.enum(notes, "provider", "$.sources.meeting_notes", NOTES_PROVIDERS),
                         "status": r.enum(notes, "status", "$.sources.meeting_notes", SOURCE_STATUSES),
                         "detail": r.text(notes, "detail", "$.sources.meeting_notes", required=False),
                         "plan": r.enum(notes, "plan", "$.sources.meeting_notes", PLANS, default="unknown")}
    crm = r.obj(src.get("crm"), "$.sources.crm")
    b["crm_source"] = {"status": r.enum(crm, "status", "$.sources.crm", SOURCE_STATUSES),
                       "detail": r.text(crm, "detail", "$.sources.crm", required=False)}
    prov = crm.get("provider")
    b["crm_source"]["provider"] = None if prov is None else r.enum(crm, "provider", "$.sources.crm", CRM_PROVIDERS)
    if b["crm_source"]["status"] == "ok" and prov is None:
        raise r.fail("$.sources.crm.provider", "is required when the CRM status is ok")

    window = r.obj(data.get("window"), "$.window", required=False)
    b["window"] = None
    if window is not None:
        start, end = r.day(window, "start", "$.window"), r.day(window, "end", "$.window")
        if end < start:
            raise r.fail("$.window.end", "is before window.start")
        b["window"] = (start, end)

    deal = r.obj(data.get("deal"), "$.deal", required=False)
    b["deal"] = None
    if deal is not None:
        if b["crm_source"]["status"] != "ok":
            raise r.fail("$.deal", "is set but sources.crm.status is {!r}; use null when the CRM was not read".format(
                b["crm_source"]["status"]))
        d = {"name": r.text(deal, "name", "$.deal"), "stage": r.text(deal, "stage", "$.deal", required=False),
             "record_ref": r.text(deal, "record_ref", "$.deal", required=False, limit=200),
             "url": r.url(deal, "url", "$.deal"), "fields": {}}
        if d["record_ref"] is not None and not REF_RE.fullmatch(d["record_ref"]):
            raise r.fail("$.deal.record_ref", "may only use letters, digits and : / _ . -")
        fields = r.obj(deal.get("fields"), "$.deal.fields", required=False) or {}
        if len(fields) > 100:
            raise r.fail("$.deal.fields", "has more than 100 fields")
        for key in sorted(fields):
            value = fields[key]
            if value is not None and not isinstance(value, str):
                raise r.fail("$.deal.fields.{}".format(key[:40]), "must be a string or null")
            d["fields"][key] = r.text(fields, key, "$.deal.fields", required=False)
        b["deal"] = d

    meetings, by_id = [], {}
    for i, m in enumerate(r.lst(data.get("meetings"), "$.meetings", required=True)):
        p = "$.meetings[{}]".format(i)
        r.obj(m, p)
        mid = r.text(m, "meeting_id", p, limit=100)
        if not REF_RE.fullmatch(mid):
            raise r.fail(p + ".meeting_id", "may only use letters, digits and : / _ . -")
        start = r.moment(m, "start", p)
        if start > as_of:
            raise r.fail(p + ".start", "is after --as-of; only meetings that already happened can be scored")
        meeting = {"id": mid, "index": i, "title": r.text(m, "title", p), "start": start, "url": r.url(m, "url", p),
                   "notes_read": r.boolean(m, "notes_read", p, True),
                   "transcript": r.enum(m, "transcript", p, TRANSCRIPT_STATES, default="not_requested"),
                   "attendees_incomplete": r.boolean(m, "attendees_incomplete", p, False),
                   "exclude_reason": r.text(m, "exclude_reason", p, required=False),
                   "matched_by": [], "attendees": [], "texts": {}, "bots": 0}
        for j, kind in enumerate(r.lst(m.get("matched_by"), p + ".matched_by", limit=5)):
            if kind not in MATCH_KINDS:
                raise r.fail("{}.matched_by[{}]".format(p, j), "must be one of {}".format(", ".join(MATCH_KINDS)))
            if kind not in meeting["matched_by"]:
                meeting["matched_by"].append(kind)
        for j, a in enumerate(r.lst(m.get("attendees"), p + ".attendees")):
            pa = "{}.attendees[{}]".format(p, j)
            r.obj(a, pa)
            name = r.text(a, "name", pa, required=False)
            email = r.text(a, "email", pa, required=False, limit=254)
            if name is None and email is None:
                raise r.fail(pa, "needs a name or an email")
            if email is not None:
                email = email.lower()
                if not EMAIL_ADDR_RE.fullmatch(email):
                    raise r.fail(pa + ".email", "is not an email address")
                if any(is_bot(email, pat) for pat in config["bot_email_patterns"]):
                    meeting["bots"] += 1
                    continue
            meeting["attendees"].append({"name": name, "email": email})
        for key, field in [("notes", "notes_text"), ("transcript", "transcript_text")]:
            value = m.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise r.fail("{}.{}".format(p, field), "must be a string or null")
            if len(value) > MAX_SOURCE_TEXT:
                raise r.fail("{}.{}".format(p, field), "is longer than {} characters".format(MAX_SOURCE_TEXT))
            if not value.strip():
                raise r.fail("{}.{}".format(p, field), "is empty; an empty {} usually means the fetch failed. Fetch "
                             "it again, or leave the key out so its quotes are reported as not checked".format(field))
            meeting["texts"][key] = value
        if "transcript" in meeting["texts"] and meeting["transcript"] != "ok":
            raise r.fail(p + ".transcript_text", "is set but transcript is {!r}, not 'ok'".format(meeting["transcript"]))
        if "notes" in meeting["texts"] and not meeting["notes_read"]:
            raise r.fail(p + ".notes_text", "is set but notes_read is false")
        if mid in by_id:
            prev = by_id[mid]
            if prev["start"] != start:
                raise r.fail(p + ".meeting_id", "repeats {!r} with a different start".format(mid))
            prev["repeats"] = prev.get("repeats", 0) + 1
            prev["notes_read"] = prev["notes_read"] or meeting["notes_read"]
            if TRANSCRIPT_RANK[meeting["transcript"]] < TRANSCRIPT_RANK[prev["transcript"]]:
                prev["transcript"] = meeting["transcript"]
            for k, v in meeting["texts"].items():
                prev["texts"][k] = prev["texts"][k] + "\n\n" + v if k in prev["texts"] else v  # never one turn
            prev["attendees"].extend(meeting["attendees"])
            for kind in meeting["matched_by"]:
                if kind not in prev["matched_by"]:
                    prev["matched_by"].append(kind)
            continue
        by_id[mid] = meeting
        meetings.append(meeting)
    b["meetings"] = meetings

    elements = {e["key"]: e for e in fw["elements"]}
    findings = []
    to_check = []  # quotes whose meeting text is in the bundle: (finding index, JSON path, meeting id, quote_from)
    for i, f in enumerate(r.lst(data.get("findings"), "$.findings", required=True)):
        p = "$.findings[{}]".format(i)
        r.obj(f, p)
        el_key = r.text(f, "element", p, limit=40)
        if el_key not in elements:
            raise r.fail(p + ".element", "{!r} is not an element of {} ({})".format(
                el_key, fw["name"], ", ".join(sorted(elements))))
        el = elements[el_key]
        part_keys = [pt["key"] for pt in el["parts"]]
        parts = []
        for j, pk in enumerate(r.lst(f.get("parts"), p + ".parts", required=True, limit=6)):
            if pk not in part_keys:
                raise r.fail("{}.parts[{}]".format(p, j), "must be one of {}".format(", ".join(part_keys)))
            if pk not in parts:
                parts.append(pk)
        if not parts:
            raise r.fail(p + ".parts", "must name at least one part of {}".format(el["label"]))
        mid = r.text(f, "meeting_id", p, limit=100)
        if mid not in by_id:
            raise r.fail(p + ".meeting_id", "{!r} is not a meeting in this bundle".format(mid))
        meeting = by_id[mid]
        qtext = r.text(f, "quote", p, limit=MAX_QUOTE)
        qfrom = r.enum(f, "quote_from", p, QUOTE_FROM)
        if qfrom == "transcript" and meeting["transcript"] != "ok":
            raise r.fail(p + ".quote_from", "is 'transcript' but meeting {!r} has transcript {!r}".format(
                mid, meeting["transcript"]))
        if qfrom == "notes" and not meeting["notes_read"]:
            raise r.fail(p + ".quote_from", "is 'notes' but meeting {!r} has notes_read false".format(mid))
        if len(gt.normalise(qtext)) < config["min_quote_chars"]:
            raise r.fail(p + ".quote", "is shorter than min_quote_chars ({}); quote enough words to check".format(
                config["min_quote_chars"]))
        ok, reason = gt.check_quote(qtext, qtext)  # what the quote alone fails: ellipsis, label, under 3 words
        if not ok and reason in (gt.REASONS["ellipsis"], gt.REASONS["label"], gt.REASONS["short"], gt.REASONS["empty"]):
            raise r.fail(p + ".quote", "{}; quote one or more complete sentences from one speaker turn, verbatim, "
                                       "without the speaker label or an ellipsis".format(reason))
        checked = qfrom in meeting["texts"]
        if checked:
            to_check.append((i, p, mid, qfrom))
        finding = {"index": i, "element": el_key, "parts": parts, "meeting_id": mid, "quote": qtext,
                   "quote_from": qfrom, "checked": checked,
                   "side": r.enum(f, "speaker_side", p, SIDES),
                   "speaker": r.text(f, "speaker", p, required=False, limit=100), "claim": None}
        claim = r.obj(f.get("claim"), p + ".claim", required=False)
        if claim is not None:
            topic = r.text(claim, "topic", p + ".claim", limit=60)
            match = [t for t in el["topics"] if fold(t) == fold(topic)]
            if not match:
                raise r.fail(p + ".claim.topic", "{!r} is not a topic of {} (topics: {})".format(
                    topic, el["label"], ", ".join(el["topics"]) or "none"))
            value = r.text(claim, "value", p + ".claim")
            if not canon(value):
                raise r.fail(p + ".claim.value", "has no letters or digits")
            supersedes = r.boolean(claim, "supersedes_earlier", p + ".claim", False)
            if supersedes and finding["side"] != "customer":
                raise r.fail(p + ".claim.supersedes_earlier", "is only for a change the customer stated (speaker_side "
                                                               "customer); our side can't settle a conflict")
            finding["claim"] = {"topic": match[0], "value": value, "supersedes": supersedes}
        findings.append(finding)
    # The whole-sentence check, grouped by meeting text so each text is split into turns and sentences once; the
    # first failing finding in bundle order is reported.
    failed = []
    for i, p, mid, qfrom in sorted(to_check, key=lambda c: (c[2], c[3], c[0])):
        ok, reason = gt.check_quote(findings[i]["quote"], by_id[mid]["texts"][qfrom])
        if not ok:
            failed.append((i, p, mid, qfrom, reason))
        elif findings[i]["side"] == "customer" and gt.quote_is_seller_only(findings[i]["quote"], by_id[mid]["texts"][qfrom]):
            failed.append((i, p, mid, qfrom, None))
    if failed:
        i, p, mid, qfrom, reason = min(failed, key=lambda f: f[0])
        if reason is None:
            raise r.fail(p + ".quote", "is from a Microphone/Me turn in the {} text of meeting {!r}: that is our side as "
                                       "Granola labels it, but speaker_side is customer. Quote the customer's turn, or "
                                       "set speaker_side to ours".format(qfrom, mid))
        raise r.fail(p + ".quote", "fails the quote check against the {} text of meeting {!r}: {}. Quote one or more "
                                   "complete sentences from one speaker turn, verbatim, without the speaker label".format(
                                       qfrom, mid, reason))
    b["findings"] = findings

    questions = r.obj(data.get("questions"), "$.questions", required=False) or {}
    b["questions"] = {}
    for key in sorted(questions):
        if key not in elements:
            raise r.fail("$.questions.{}".format(key[:40]), "is not an element of {}".format(fw["name"]))
        items = r.lst(questions[key], "$.questions.{}".format(key), limit=2)
        out = []
        for j, q in enumerate(items):
            if not isinstance(q, str) or not q.strip() or len(q) > MAX_TEXT:
                raise r.fail("$.questions.{}[{}]".format(key, j), "must be a non-empty string up to {} characters".format(
                    MAX_TEXT))
            out.append(" ".join(q.split()))
        if out:
            b["questions"][key] = out
    return b


# --------------------------------------------------------------------------- analysis


def same_meeting(a: dict, b: dict) -> bool:
    """Two notes of one meeting: same instant and the same title or a shared account attendee."""
    return a["start"] == b["start"] and (a["title_key"] == b["title_key"] or bool(a["account_emails"] & b["account_emails"]))


def analyse(b: dict, fw: dict, config: dict, as_of: datetime, internal: List[str]) -> dict:
    account = b["account"]["domains"]
    today = as_of.date()
    a = {"excluded": [], "groups": [], "repeats": 0, "bots": 0, "dropped_findings": 0}

    # 1. Duplicate notes of one meeting become one group; agent exclusions stay separate.
    groups, by_start = [], {}
    for m in sorted(b["meetings"], key=lambda m: (m["start"], m["index"])):
        m["title_key"] = fold(m["title"])
        m["account_emails"] = {x["email"] for x in m["attendees"] if x["email"] and in_domains(domain_of(x["email"]), account)}
        a["repeats"] += m.get("repeats", 0)
        a["bots"] += m["bots"]
        if m["exclude_reason"]:
            a["excluded"].append({"meeting": m, "reason": m["exclude_reason"], "kind": "agent"})
            continue
        candidates = by_start.setdefault(m["start"], [])
        for g in candidates:
            if any(same_meeting(m, other) for other in g["notes"]):
                g["notes"].append(m)
                break
        else:
            g = {"notes": [m]}
            groups.append(g)
            candidates.append(g)

    # 2. Classify each group.
    group_of = {}
    for g in groups:
        notes = g["notes"]
        first = notes[0]
        people = [x for n in notes for x in n["attendees"]]
        emails = [x["email"] for x in people if x["email"]]
        account_people = [x for x in people if x["email"] and in_domains(domain_of(x["email"]), account)]
        incomplete = any(n["attendees_incomplete"] for n in notes)
        matched = set(k for n in notes for k in n["matched_by"])
        g.update({"id": first["id"], "title": first["title"], "start": first["start"], "url": first["url"],
                  "date": first["start"].date(), "notes_read": any(n["notes_read"] for n in notes),
                  "transcript": min((n["transcript"] for n in notes), key=lambda s: TRANSCRIPT_RANK[s])})
        if account_people or (incomplete and matched & {"title", "mention", "crm", "user"}):
            g["included"] = True
            for n in notes:
                group_of[n["id"]] = g
            continue
        g["included"] = False
        if emails and all(in_domains(domain_of(e), internal) for e in emails) and len(emails) == len(people):
            reason, kind = "internal-only", "internal"
        else:
            reason, kind = "nobody from {} on the invite".format(join_words(account, "or")), "unclassified"
        a["excluded"].append({"meeting": first, "reason": reason, "kind": kind, "copies": len(notes)})
    a["groups"] = [g for g in groups if g["included"]]
    a["notes_in_groups"] = sum(len(g["notes"]) for g in a["groups"])

    # 3. Findings in included meetings, with their meeting date and age.
    usable = []
    for f in b["findings"]:
        g = group_of.get(f["meeting_id"])
        if g is None:
            a["dropped_findings"] += 1
            continue
        note = next(n for n in g["notes"] if n["id"] == f["meeting_id"])
        f2 = dict(f)
        f2.update({"group": g, "note": note, "date": g["date"], "age": (today - g["date"]).days})
        f2["stale"] = f2["age"] > config["stale_days"]
        usable.append(f2)
    usable.sort(key=lambda f: (f["group"]["start"], f["index"]))
    a["findings"] = usable

    # 4. Per element: parts, status, flags, topics.
    a["elements"] = []
    for el in fw["elements"]:
        ev = [f for f in usable if f["element"] == el["key"]]
        parts = []
        for pt in el["parts"]:
            pev = [f for f in ev if pt["key"] in f["parts"]]
            cust = [f for f in pev if f["side"] == "customer"]
            if any(not f["stale"] for f in cust):
                state, last = "ok", None
            elif cust:
                state, last = "stale", max(f["date"] for f in cust)
            elif any(f["side"] == "unknown" for f in pev):
                state, last = "unknown", None
            elif pev:
                state, last = "ours", None
            else:
                state, last = "none", None
            parts.append({"key": pt["key"], "label": pt["label"], "question": pt["question"], "state": state,
                          "last": last})
        topics = []
        for topic in el["topics"]:
            claims = [f for f in ev if f["claim"] and f["claim"]["topic"] == topic]
            if claims:
                topics.append(topic_state(el, topic, claims))
        sides = set(f["side"] for f in ev)
        flags = []
        if any(t["conflict"] for t in topics):
            status = "contradicted"
        elif not ev:
            status = "missing"
        elif all(p["state"] == "ok" for p in parts):
            status = "evidenced"
        else:
            status = "partial"
        if ev and sides == {"ours"}:
            flags.append("ours_only")
        elif ev and "customer" not in sides:
            flags.append("unattributed")
        newest = max((f["date"] for f in ev), default=None)
        if ev and all(f["stale"] for f in ev):
            flags.append("stale")
        a["elements"].append({"key": el["key"], "short": el["short"], "label": el["label"], "status": status,
                              "flags": flags, "parts": parts, "evidence": ev, "topics": topics, "newest": newest,
                              "questions": []})

    # 5. CRM fields vs calls.
    by_key = {e["key"]: e for e in a["elements"]}
    a["crm"] = []
    deal = b["deal"]
    if deal is not None:
        for cf in fw["crm_fields"]:
            if cf["field"] not in deal["fields"]:
                continue
            crm_value = deal["fields"][cf["field"]]
            t = next((t for t in by_key[cf["element"]]["topics"] if t["topic"] == cf["topic"]), None)
            if t is None:
                state = "crm_only" if crm_value else "both_empty"
            elif t["conflict"]:
                state = "calls_disagree"
            elif crm_value is None:
                state = "crm_empty"
            elif canon(crm_value) == t["current"][0]["canon"]:
                state = "match"
            else:
                state = "disagrees"
            if state == "both_empty":
                continue
            a["crm"].append({"field": cf, "crm_value": crm_value, "topic": t, "state": state})
            if state == "disagrees" and "crm_disagrees" not in by_key[cf["element"]]["flags"]:
                by_key[cf["element"]]["flags"].append("crm_disagrees")

    # 6. Questions for every element that is not evidenced.
    for e in a["elements"]:
        if e["status"] == "evidenced":
            continue
        if e["key"] in b["questions"]:
            e["questions"] = list(b["questions"][e["key"]])
            e["questions_from"] = "agent"
            continue
        qs = []
        for t in e["topics"]:
            if t["conflict"] and len(qs) < 2:
                qs.append("We've heard {} for the {}. Which is right today, and what changed?".format(
                    join_words(["“{}”".format(v["value"]) for v in t["current"]], "and"), t["topic"].lower()))
        for p in e["parts"]:
            if p["state"] != "ok" and len(qs) < 2:
                qs.append(p["question"])
        e["questions"] = qs
        e["questions_from"] = "framework"

    a["counts"] = {s: sum(1 for e in a["elements"] if e["status"] == s) for s in STATUS_ORDER}
    a["quotes_checked"] = sum(1 for f in usable if f["checked"])
    return a


def topic_state(el: dict, topic: str, claims: List[dict]) -> dict:
    """Walk the claims in meeting order. A claim marked supersedes_earlier replaces every earlier value
    (recorded as a change); otherwise each new value joins the current set, and two or more current
    values are a conflict."""
    current, changes = [], []
    for f in claims:
        key = canon(f["claim"]["value"])
        if f["claim"]["supersedes"]:
            earlier = [v for v in current if v["canon"] != key]
            if earlier:
                changes.append({"from": earlier, "to": f})
            current = [v for v in current if v["canon"] == key]
        hit = next((v for v in current if v["canon"] == key), None)
        if hit is None:
            current.append({"canon": key, "value": f["claim"]["value"], "claims": [f]})
        else:
            hit["claims"].append(f)
    for v in current:
        v["customer"] = any(f["side"] == "customer" for f in v["claims"])
        v["latest"] = v["claims"][-1]
    return {"element": el["key"], "topic": topic, "current": current, "changes": changes, "conflict": len(current) > 1}


# --------------------------------------------------------------------------- rendering


def side_words(f: dict) -> str:
    who = md(f["speaker"]) if f["speaker"] else ""
    if f["side"] == "customer":
        base = "customer" + (" ({})".format(who) if who else "")
    elif f["side"] == "ours":
        base = "our side" + (" ({})".format(who) if who else "")
    else:
        base = "speaker not identified"
    return base if f["quote_from"] == "transcript" else "notes, attributed to " + base


def non_customer(v: dict) -> str:
    """Who said a value the customer never said: "ours", "unknown" (speaker not identified) or "mixed"."""
    sides = set(f["side"] for f in v["claims"])
    return "ours" if sides == {"ours"} else "unknown" if sides == {"unknown"} else "mixed"


def cite(f: dict, as_of: date) -> str:
    """Link to the note the finding came from, titled with the meeting's title and date."""
    return link("{}, {}".format(md(f["group"]["title"]), short_date(f["date"], as_of)), f["note"]["url"])


def value_cite(v: dict, as_of: date) -> str:
    f = v["latest"]
    side = "customer" if f["side"] == "customer" else "our side" if f["side"] == "ours" else "speaker not identified"
    return "“{}” ({}, {})".format(md(v["value"]), side, cite(f, as_of))


def missing_text(p: dict, as_of: date, today: date) -> str:
    if p["state"] == "stale":
        reason = PART_REASON["stale"].format(short_date(p["last"], as_of), (today - p["last"]).days)
    else:
        reason = PART_REASON[p["state"]]
    return "{} ({})".format(md(p["label"]), reason)


def fix_first(a: dict, as_of: datetime, config: dict) -> List[str]:
    d = as_of.date()
    out = []
    for e in a["elements"]:
        for t in e["topics"]:
            if t["conflict"]:
                out.append("- ⚠️ **Conflict, {} ({}):** {}.".format(
                    md(e["label"]), md(t["topic"]), " vs ".join(value_cite(v, d) for v in t["current"])))
    for c in a["crm"]:
        if c["state"] == "disagrees":
            v = c["topic"]["current"][0]
            out.append("- ⚠️ **CRM disagrees, {}:** CRM says “{}”; the calls say {}.".format(
                md(c["field"]["label"]), md(c["crm_value"]), value_cite(v, d)))
    for e in a["elements"]:
        if "ours_only" in e["flags"]:
            out.append("- ⚠️ **Our side only, {}:** every piece of evidence is our side speaking; the customer "
                       "hasn't confirmed it on a call.".format(md(e["label"])))
        elif "unattributed" in e["flags"]:
            out.append("- ⚠️ **Not confirmed by the customer, {}:** no evidence is attributed to the customer "
                       "(speaker not identified).".format(md(e["label"])))
    for e in a["elements"]:
        if "stale" in e["flags"]:
            out.append("- ⏳ **Stale, {}:** newest evidence {} ({} days ago; stale after {} days).".format(
                md(e["label"]), short_date(e["newest"], d), (d - e["newest"]).days, config["stale_days"]))
    for e in a["elements"]:
        for t in e["topics"]:
            for ch in t["changes"]:
                f = ch["to"]
                side = "customer" if f["side"] == "customer" else "our side" if f["side"] == "ours" else \
                    "speaker not identified"
                out.append("- ↪️ **Changed, {} ({}):** from {} to “{}” ({}, {}).".format(
                    md(e["label"]), md(t["topic"]), join_words(["“{}”".format(md(v["value"])) for v in ch["from"]],
                                                               "and"),
                    md(f["claim"]["value"]), side, cite(f, d)))
    return out


def header_line(b: dict, a: dict, as_of: datetime) -> str:
    d = as_of.date()
    groups = a["groups"]
    bits = ["As of {}".format(long_date(d))]
    if groups:
        first, last = groups[0]["date"], groups[-1]["date"]
        span = short_date(first, d) if first == last else "{} – {}".format(short_date(first, d), short_date(last, d))
        bits.append("{} ({})".format(plural(len(groups), "customer meeting"), span))
    else:
        bits.append("0 customer meetings")
    bits.append(crm_phrase(b))
    bits.append("Coverage checklist, not a win prediction")
    return "_" + " · ".join(bits) + "._"


def crm_phrase(b: dict) -> str:
    src = b["crm_source"]
    if src["status"] == "not_configured":
        return "CRM: not configured"
    if src["status"] != "ok":
        return "CRM: {} {}".format(PROVIDER_LABELS.get(src["provider"] or "", "CRM"), src["status"])
    deal = b["deal"]
    name = PROVIDER_LABELS[src["provider"]]
    if deal is None:
        return "CRM: {}, no deal found".format(name)
    extra = []
    if deal["stage"]:
        extra.append(md(deal["stage"]))
    if deal["fields"].get("close_date"):
        extra.append("close date {}".format(md(deal["fields"]["close_date"])))
    return "CRM: {} deal {}{}".format(name, link("“{}”".format(md(deal["name"])), deal["url"]),
                                      " ({})".format(", ".join(extra)) if extra else "")


def coverage_lines(b: dict, a: dict, as_of: datetime, config: dict) -> List[str]:
    d = as_of.date()
    out = []
    n_groups = len(a["groups"])
    merged = a["notes_in_groups"] - n_groups
    line = "- Meetings scored: {}".format(plural(n_groups, "customer meeting"))
    if merged:
        line += " from {} ({} of the same meeting merged)".format(plural(a["notes_in_groups"], "note"),
                                                                plural(merged, "duplicate note"))
    if a["repeats"]:
        line += "; {} merged".format(plural(a["repeats"], "repeated meeting id"))
    out.append(line + ".")
    if a["excluded"]:
        items = ["{}, {}: {}".format(md(x["meeting"]["title"]), short_date(x["meeting"]["start"].date(), d),
                                     md(x["reason"])) for x in a["excluded"]]
        out.append("- Not scored: {}.".format("; ".join(items)))
    if a["bots"]:
        out.append("- {} dropped from attendee lists.".format(plural(a["bots"], "note-taker bot")))
    if a["dropped_findings"]:
        out.append("- {} not used: {} meetings that are not scored.".format(
            plural(a["dropped_findings"], "finding"), "they cite" if a["dropped_findings"] != 1 else "it cites"))
    ts = {}
    for g in a["groups"]:
        ts[g["transcript"]] = ts.get(g["transcript"], 0) + 1
    parts = ["{} read".format(ts.get("ok", 0))]
    if ts.get("denied"):
        parts.append("{} denied (notes used instead)".format(ts["denied"]))
    if ts.get("unavailable"):
        parts.append("{} unavailable".format(ts["unavailable"]))
    if ts.get("not_requested"):
        parts.append("{} not requested".format(ts["not_requested"]))
    out.append("- Transcripts: {}.".format(", ".join(parts)))
    n = len(a["findings"])
    unchecked = n - a["quotes_checked"]
    out.append("- Quotes: {} used; {} checked against the note or transcript text in the bundle{}.".format(
        n, a["quotes_checked"], ", {} not checked (that text was not in the bundle)".format(unchecked)
        if unchecked else ""))
    out.append("- Evidence counts as stale after {} days (`stale_days`).".format(config["stale_days"]))
    out.append("- Sources: {}.".format(sources_line(b)))
    out.append("- Gaps: {}.".format("; ".join(gaps(b, a, as_of, config)) or "none"))
    return out


def sources_line(b: dict) -> str:
    ns = b["notes_source"]
    notes = "Meeting notes: {} {}".format(PROVIDER_LABELS[ns["provider"]], "✓" if ns["status"] == "ok" else ns["status"])
    if ns["plan"] != "unknown":
        notes += " ({} plan)".format(ns["plan"])
    cs = b["crm_source"]
    if cs["status"] == "not_configured":
        crm = "CRM: not configured"
    elif cs["status"] == "ok":
        crm = "CRM: {} ✓ ({})".format(PROVIDER_LABELS[cs["provider"]],
                                      "deal found" if b["deal"] else "no deal found")
    else:
        crm = "CRM: {} {}".format(PROVIDER_LABELS.get(cs["provider"] or "", "CRM"), cs["status"])
    return notes + " · " + crm


def gaps(b: dict, a: dict, as_of: datetime, config: dict) -> List[str]:
    d = as_of.date()
    out = []
    ns, cs = b["notes_source"], b["crm_source"]
    if ns["detail"]:
        out.append("meeting notes: {}".format(md(ns["detail"])))
    if ns["plan"] in ("free", "basic"):
        window = config["free_plan_window_days"]
        start = b["window"][0] if b["window"] else None
        if start is None or (d - start).days > window:
            out.append("the {} Granola plan shows only the last {} days of notes, so earlier meetings may be "
                       "missing".format(ns["plan"], window))
    unread = [g for g in a["groups"] if not g["notes_read"]]
    if unread:
        out.append("notes not read for {}".format(", ".join("{} ({})".format(md(g["title"]), short_date(g["date"], d))
                                                           for g in unread)))
    if cs["status"] not in ("ok", "not_configured"):
        out.append("CRM {}{}: no CRM fields compared".format(cs["status"], ": " + md(cs["detail"]) if cs["detail"] else ""))
    elif cs["status"] == "ok" and b["deal"] is None:
        out.append("no CRM deal found: no CRM fields compared")
    elif cs["status"] == "ok" and not a["crm"]:
        out.append("the deal has none of the CRM fields this framework compares")
    if not a["groups"]:
        out.append("no customer meetings to score: every element below is missing as far as the meetings read show")
    return out


def render_scorecard(b: dict, a: dict, fw: dict, config: dict, as_of: datetime) -> str:
    d = as_of.date()
    today = d
    L = ["# Deal qualification: {} — {}".format(md(b["account"]["name"]), md(fw["name"])), ""]
    L.append(header_line(b, a, as_of))
    L.append("")
    c = a["counts"]
    L.append("**Coverage:** {} evidenced · {} partial · {} missing · {} contradicted, of {}.".format(
        c["evidenced"], c["partial"], c["missing"], c["contradicted"], plural(len(a["elements"]), "element")))
    L.append("")
    L.append("## Fix first")
    L.extend(fix_first(a, as_of, config) or ["- Nothing flagged: no conflicts, CRM disagreements, our-side-only or "
                                            "stale elements."])
    L.append("")
    L.append("## Scorecard")
    L.append("| | Element | Status | Evidence | Missing |")
    L.append("|---|---|---|---|---|")
    for e in a["elements"]:
        ev = e["evidence"]
        if ev:
            sides = [s for s in SIDES if any(f["side"] == s for f in ev)]
            words = {"customer": "customer", "ours": "our side", "unknown": "unattributed"}
            evtext = "{} · {}".format(plural(len(ev), "quote"), ", ".join(words[s] for s in sides))
        else:
            evtext = "—"
        miss = ["{} conflicts across calls".format(md(t["topic"])) for t in e["topics"] if t["conflict"]]
        miss += [missing_text(p, d, today) for p in e["parts"] if p["state"] != "ok"]
        L.append("| {} | {} | {} | {} | {} |".format(md(e["short"]), md(e["label"]), STATUS_LABEL[e["status"]], evtext,
                                                    "; ".join(miss) or "—"))
    L.append("")
    L.append("## By element")
    for e in a["elements"]:
        L.append("")
        L.append("### {} · {} — {}".format(md(e["short"]), md(e["label"]), STATUS_WORD[e["status"]]))
        ev = sorted(e["evidence"], key=lambda f: (SIDE_RANK[f["side"]], -f["date"].toordinal(), f["index"]))
        shown = ev[:config["max_quotes_per_element"]]
        for f in shown:
            L.append("- “{}” — {}, {}{}".format(gt.md(gt.shorten(f["quote"], config["max_quote_chars"])), side_words(f),
                                                cite(f, d), " ⏳" if f["stale"] else ""))
        if len(ev) > len(shown):
            L.append("- …and {} (all of them are in `--format json`).".format(plural(len(ev) - len(shown), "more quote")))
        if not ev:
            L.append("- No evidence in the meetings read.")
        miss = [missing_text(p, d, today) for p in e["parts"] if p["state"] != "ok"]
        if miss:
            L.append("- Missing: {}.".format("; ".join(miss)))
        if e["questions"]:
            L.append("- Ask next: {}".format(" · ".join(md(q) for q in e["questions"])))
    if a["crm"]:
        L.append("")
        L.append("## CRM vs calls")
        L.append("| CRM field | CRM | Calls | |")
        L.append("|---|---|---|---|")
        words = {"match": "✓ matches", "disagrees": "⚠️ disagrees", "crm_empty": "CRM empty",
                 "calls_disagree": "⚠️ calls disagree", "crm_only": "not heard on a call"}
        for c in a["crm"]:
            t = c["topic"]
            calls = " vs ".join(value_cite(v, d) for v in t["current"]) if t else "—"
            state = words[c["state"]]
            if c["state"] == "match" and not t["current"][0]["customer"]:
                state += {"ours": ", our side only", "unknown": ", speaker not identified",
                          "mixed": ", our side or speaker not identified"}[non_customer(t["current"][0])]
            L.append("| {} | {} | {} | {} |".format(md(c["field"]["label"]),
                                                   md(c["crm_value"]) if c["crm_value"] else "(empty)", calls, state))
    L.append("")
    L.append("## Coverage")
    L.extend(coverage_lines(b, a, as_of, config))
    L.append("")
    L.append(INTERNAL_FOOTER)
    return "\n".join(L)


def render_crm_draft(b: dict, a: dict, fw: dict, as_of: datetime) -> str:
    d = as_of.date()
    deal = b["deal"]
    ref = " · `{}`".format(deal["record_ref"]) if deal["record_ref"] else ""
    L = ["# CRM update DRAFT: {} — {}".format(md(b["account"]["name"]), link("“{}”".format(md(deal["name"])), deal["url"])),
         "", "_{} deal{} · as of {} · {}. Not written: nothing changes in the CRM until a person approves each "
             "line._".format(PROVIDER_LABELS[b["crm_source"]["provider"]], ref, long_date(d), md(fw["name"])), ""]
    proposals, held = [], []
    said = {"ours": "only our side said it", "unknown": "the speaker wasn't identified",
            "mixed": "only our side or an unidentified speaker said it"}
    for c in a["crm"]:
        label = md(c["field"]["label"])
        t = c["topic"]
        if c["state"] in ("disagrees", "crm_empty"):
            v = t["current"][0]
            if not v["customer"]:
                held.append("- {}: the calls say “{}”, but {}. Confirm with the customer first.".format(
                    label, md(v["value"]), said[non_customer(v)]))
                continue
            f = next(x for x in reversed(v["claims"]) if x["side"] == "customer")
            proposals.append("| {} | `{}` | {} | {} | “{}” — {}, {} |".format(
                label, c["field"]["field"], md(c["crm_value"]) if c["crm_value"] else "(empty)", md(v["value"]),
                gt.md(gt.shorten(f["quote"], 160)), side_words(f), cite(f, d)))
        elif c["state"] == "calls_disagree":
            held.append("- {}: the calls disagree ({}). Resolve it on the next call before changing the CRM.".format(
                label, " vs ".join("“{}”".format(md(v["value"])) for v in t["current"])))
        elif c["state"] == "match" and not t["current"][0]["customer"]:
            held.append("- {}: the CRM matches the calls (“{}”), but {}. Confirm with the customer."
                        .format(label, md(c["crm_value"]), said[non_customer(t["current"][0])]))
        elif c["state"] == "crm_only":
            held.append("- {}: CRM says “{}”, but no call mentions it. Nothing to propose; confirm on the next call."
                        .format(label, md(c["crm_value"])))
    if proposals:
        L.append("| Field | Key | CRM now | Proposed | Evidence |")
        L.append("|---|---|---|---|---|")
        L.extend(proposals)
    else:
        L.append("No field changes to propose: every compared field matches the calls, or is held below.")
    if held:
        L.append("")
        L.append("**Not proposed**")
        L.extend(held)
    L.append("")
    L.append("_Draft only. Each proposed value is the agent's reading of the cited customer quote. A person checks each "
             "line against the quote and the meeting, then approves it before anything is written to the CRM._")
    return "\n".join(L)


def next_gap(a: dict) -> str:
    for status in ["contradicted", "missing", "partial"]:
        for e in a["elements"]:
            if e["status"] != status:
                continue
            if status == "contradicted":
                t = next(t for t in e["topics"] if t["conflict"])
                return "{}: settle the {}".format(md(e["label"]), md(t["topic"].lower()))
            p = next((p for p in e["parts"] if p["state"] != "ok"), None)
            return "{}: {}".format(md(e["label"]), md(p["label"].lower()) if p else "evidence")
    return "—"


def render_batch(rows: List[Tuple[dict, Optional[dict]]], fw: dict, as_of: datetime) -> str:
    d = as_of.date()
    L = ["# Deal qualification: {} — {}".format(plural(len(rows), "deal"), md(fw["name"])), "",
         "_As of {} · Coverage checklist, not a win prediction._".format(long_date(d)), ""]
    shorts = [md(e["short"]) for e in fw["elements"]]
    L.append("| Deal | Stage | Meetings | " + " | ".join(shorts) + " | Flags | Next gap |")
    L.append("|---|---|---|" + "---|" * len(shorts) + "---|---|")
    for b, a in rows:
        name = md(b["account"]["name"])
        stage = md(b["deal"]["stage"]) if b["deal"] and b["deal"]["stage"] else (
            "no deal" if b["crm_source"]["status"] == "ok" else "CRM " + b["crm_source"]["status"].replace("_", " "))
        if a is None:
            ns = b["notes_source"]
            L.append("| {} | {} | — | {} | not scored: meeting notes {}{} | — |".format(
                name, stage, " | ".join("—" for _ in shorts), ns["status"].replace("_", " "),
                " ({})".format(md(ns["detail"])) if ns["detail"] else ""))
            continue
        cells = [STATUS_ICON[e["status"]] for e in a["elements"]]
        conflicts = sum(1 for e in a["elements"] for t in e["topics"] if t["conflict"])
        crm_bad = sum(1 for c in a["crm"] if c["state"] == "disagrees")
        ours = sum(1 for e in a["elements"] if "ours_only" in e["flags"])
        stale = sum(1 for e in a["elements"] if "stale" in e["flags"])
        flags = []
        if conflicts:
            flags.append(plural(conflicts, "conflict"))
        if crm_bad:
            flags.append("{} CRM ≠ calls".format(crm_bad))
        if ours:
            flags.append("{} our side only".format(ours))
        if stale:
            flags.append("{} stale".format(stale))
        L.append("| {} | {} | {} | {} | {} | {} |".format(name, stage, len(a["groups"]), " | ".join(cells),
                                                         " · ".join(flags) or "—", next_gap(a)))
    L.append("")
    L.append("✅ evidenced · 🟡 partial · ❌ missing · ⚠️ contradicted. Columns: {}.".format(
        ", ".join("{} {}".format(md(e["short"]), md(e["label"])) for e in fw["elements"])))
    L.append("Run a single deal for its quotes, missing parts and questions to ask next.")
    L.append("")
    L.append(INTERNAL_FOOTER)
    return "\n".join(L)


def json_deal(b: dict, a: Optional[dict], fw: dict) -> dict:
    """Raw values (emails and phones removed, nothing escaped) for one deal."""
    out = {"account": redact(b["account"]["name"]), "account_domains": b["account"]["domains"],
           "framework": fw["key"], "sources": {"meeting_notes": dict(b["notes_source"]), "crm": dict(b["crm_source"])}}
    for src in out["sources"].values():
        if src.get("detail"):
            src["detail"] = redact(src["detail"])
    if b["deal"]:
        out["deal"] = {"name": redact(b["deal"]["name"]), "stage": b["deal"]["stage"] and redact(b["deal"]["stage"]),
                       "record_ref": b["deal"]["record_ref"], "url": b["deal"]["url"]}
    if a is None:
        out["scored"] = False
        return out
    out["scored"] = True
    out["coverage"] = dict(a["counts"])
    out["elements"] = []
    for e in a["elements"]:
        out["elements"].append({
            "key": e["key"], "label": e["label"], "status": e["status"], "flags": e["flags"],
            "parts": [{"key": p["key"], "label": p["label"], "state": p["state"],
                       "last_confirmed": p["last"].isoformat() if p["last"] else None} for p in e["parts"]],
            "evidence": [{"meeting_id": f["meeting_id"], "date": f["date"].isoformat(), "url": f["note"]["url"],
                          "quote": redact(f["quote"]), "quote_from": f["quote_from"], "quote_checked": f["checked"],
                          "speaker_side": f["side"], "speaker": f["speaker"] and redact(f["speaker"]),
                          "parts": f["parts"], "stale": f["stale"],
                          "claim": {"topic": f["claim"]["topic"], "value": redact(f["claim"]["value"])}
                          if f["claim"] else None} for f in e["evidence"]],
            "topics": [{"topic": t["topic"], "conflict": t["conflict"],
                        "values": [{"value": redact(v["value"]), "customer_confirmed": v["customer"],
                                    "meeting_ids": [f["meeting_id"] for f in v["claims"]]} for v in t["current"]],
                        "changes": [{"from": [redact(v["value"]) for v in ch["from"]],
                                     "to": redact(ch["to"]["claim"]["value"]), "meeting_id": ch["to"]["meeting_id"]}
                                    for ch in t["changes"]]} for t in e["topics"]],
            "questions": [redact(q) for q in e["questions"]]})
    out["crm_comparison"] = [{"field": c["field"]["field"], "crm_value": c["crm_value"] and redact(c["crm_value"]),
                              "state": c["state"],
                              "call_values": [redact(v["value"]) for v in c["topic"]["current"]] if c["topic"] else []}
                             for c in a["crm"]]
    out["meetings"] = [{"meeting_id": g["id"], "title": redact(g["title"]), "date": g["date"].isoformat(),
                        "url": g["url"], "note_ids": [n["id"] for n in g["notes"]], "transcript": g["transcript"]}
                       for g in a["groups"]]
    out["excluded_meetings"] = [{"meeting_id": x["meeting"]["id"], "title": redact(x["meeting"]["title"]),
                                 "reason": redact(x["reason"])} for x in a["excluded"]]
    out["quotes"] = {"used": len(a["findings"]), "checked": a["quotes_checked"]}
    out["findings_not_used"] = a["dropped_findings"]
    return out


# --------------------------------------------------------------------------- main


def utf8_streams() -> None:
    for stream, errors in ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors=errors)
            except (ValueError, OSError):
                pass


def emit(text: str) -> int:
    """Print the result; a closed pipe or full disk is exit 6 with a message, never a traceback."""
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
        try:
            sys.stderr.write("error: the output could not be written ({}).\n".format(
                getattr(exc, "strerror", None) or exc))
        except (OSError, ValueError, AttributeError):
            pass
        return EXIT_OUTPUT


def main(argv: Optional[List[str]] = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(description="Score deal qualification from one JSON bundle per deal.")
    parser.add_argument("--bundle", action="append", type=Path, default=[],
                        help="Deal bundle (DATA_CONTRACT.md). Repeat for a batch table across deals.")
    parser.add_argument("--framework", type=Path, default=DEFAULT_FRAMEWORK,
                        help="Framework JSON (framework_meddicc.json, _meddpicc, _bant, _spiced or your own).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="scorecard.json")
    parser.add_argument("--as-of", help="Run time, ISO datetime with UTC offset. The only 'now'.")
    parser.add_argument("--internal-domain", action="append", default=[],
                        help="Your company's email domain; repeat for each. Subdomains count as internal.")
    parser.add_argument("--format", choices=FORMATS, default=None, help="scorecard (default), crm-draft or json")
    parser.add_argument("--json", action="store_true", help="Same as --format json.")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed its usage message to stderr
        return EXIT_BAD_INPUT if exc.code else 0
    fmt = "json" if args.json else (args.format or "scorecard")
    try:
        if args.json and args.format not in (None, "json"):
            raise InputError("--json and --format {} contradict each other; pass one".format(args.format))
        if not args.bundle:
            raise InputError("--bundle is required")
        if len(args.bundle) > 200:
            raise InputError("at most 200 bundles per run")
        if not args.as_of:
            raise InputError("--as-of is required, e.g. --as-of 2026-10-12T09:00:00-07:00")
        as_of = parse_moment(args.as_of, lambda p: InputError("--as-of: " + p))
        internal = []
        for raw in args.internal_domain:
            dom = clean_domain(raw)
            if not DOMAIN_RE.fullmatch(dom):
                raise InputError("--internal-domain: {!r} is not a domain like talvessa.example".format(raw[:60]))
            if dom not in internal:
                internal.append(dom)
        if not internal:
            raise InputError("--internal-domain is required (your company's email domain), so internal-only "
                             "meetings can be excluded")
        config = load_config(args.config)
        fw = load_framework(args.framework)
        for dom in internal:
            if in_domains(dom, config["consumer_domains"]):
                raise InputError("--internal-domain: {} is a personal email provider".format(dom))
        batch = len(args.bundle) > 1
        if batch and fmt == "crm-draft":
            raise InputError("--format crm-draft works on one deal at a time")
        rows = []
        for path in args.bundle:
            b = load_bundle(path, internal, config, fw, as_of)
            a = analyse(b, fw, config, as_of, internal) if b["notes_source"]["status"] == "ok" else None
            rows.append((b, a))
        if not batch:
            b, a = rows[0]
            if a is None:
                ns = b["notes_source"]
                sys.stderr.write("not scored: the meeting-notes source ({}) is {}{}. Nothing was scored, so nothing "
                                 "is reported as missing. Fix access or provide transcripts, then rerun.\n".format(
                                     PROVIDER_LABELS[ns["provider"]], ns["status"],
                                     " ({})".format(redact(ns["detail"])) if ns["detail"] else ""))
                return EXIT_SOURCE_NOT_OK
            if fmt == "crm-draft":
                if b["deal"] is None:
                    raise InputError("--format crm-draft needs a CRM deal (sources.crm.status ok and deal set)")
                text = render_crm_draft(b, a, fw, as_of)
            elif fmt == "json":
                card = render_scorecard(b, a, fw, config, as_of)
                deal = json_deal(b, a, fw)
                deal.update({"schema_version": 1, "as_of": as_of.isoformat(), "markdown": card})
                text = json.dumps(deal, ensure_ascii=False, indent=1)
            else:
                text = render_scorecard(b, a, fw, config, as_of)
        else:
            table = render_batch(rows, fw, as_of)
            if fmt == "json":
                text = json.dumps({"schema_version": 1, "as_of": as_of.isoformat(), "framework": fw["key"],
                                   "deals": [json_deal(b, a, fw) for b, a in rows], "markdown": table},
                                  ensure_ascii=False, indent=1)
            else:
                text = table
    except InputError as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return EXIT_BAD_INPUT
    except RecursionError:
        sys.stderr.write("error: input is nested too deeply\n")
        return EXIT_BAD_INPUT
    return emit(text)


if __name__ == "__main__":
    sys.exit(main())
