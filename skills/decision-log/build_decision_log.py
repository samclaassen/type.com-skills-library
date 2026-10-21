#!/usr/bin/env python3
"""Keep a team decision log from meeting notes: validate, merge, flag and render.

Reads one normalised JSON bundle (meetings plus the decisions and proposals the agent extracted from them, and the
user's replies) and the decision log file (JSON) carried across runs. Stdlib only, offline, deterministic. --as-of
is the only source of "now"; the wall clock is read only to decide whether a leftover lock file is stale.

What it does, mechanically (the agent does the extraction and judgement):
  validate   bundle, config and log shape, enums, strict dates (YYYY-MM-DD, datetimes with UTC offsets), every
             decision cites a meeting in the bundle whose quoted source was read, a quote must be one complete
             sentence, or several consecutive ones, from one speaker turn of the meeting text when that text is in
             the bundle (granola_text.check_quote; speaker turns found heuristically), http(s) links only
  ids        stable id = "dl-" + sha256(meeting_id | normalised quote | topic)[:10]; an id never changes once logged
  merge      re-extractions (same id, or same meeting and quote) change nothing; repeats (same_as, or same kind,
             topic and normalised statement as a live entry) are linked to the logged entry; everything else is new
  flags      reversals (explicit `supersedes`), possible conflicts (same topic key as a live decision, not linked),
             decisions with no owner, revisit dates on or before --as-of, open proposals (never promoted without a
             `confirm` reply)
  state      two-phase for scheduled weekly runs: a report is staged as `pending`, and --confirm-posted <period>
             marks its entries reported. One report per Monday-Sunday week. A window whose meeting list was not ok,
             that starts before the plan's note history, or whose meetings were all unreadable is Incomplete: nothing
             is merged and nothing is staged; --confirm-posted on it records that its notice was posted, and later
             Incomplete runs that week print Skipped. Dismiss and restore re-run the conflict check
  render     period report, full log and search results as markdown or Slack mrkdwn; every interpolated value is
             escaped (granola_text.md / granola_text.slack) so it can't form a link, autolink, mention, raw HTML or
             formatting

Exit codes: 0 report, log, search result, "Skipped: ..." or a command result printed; 2 bad input, config, log or
arguments, or stdout could not be written (nothing is printed on stdout; the message names the file and JSON path);
3 another run holds the log lock; 4 this week's report is pending confirmation (see --confirm-posted /
--resume-pending), also when a staged report could not be written to stdout; 5 coverage incomplete: an Incomplete
report is printed (or "Skipped: ..." when this week's Incomplete notice was already recorded) and nothing was merged
or staged. Never edit this script to get past an error.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as url_quote

SCRIPT_DIR = Path(__file__).resolve().parent
# The shared text helpers (quote check, escaping) sit next to this script as granola_text.py, copied byte-for-byte.
sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__
sys.path.insert(0, str(SCRIPT_DIR))
import granola_text as gt  # noqa: E402
DEFAULT_CONFIG = SCRIPT_DIR / "decision_log.json"
SCHEMA_VERSION = 1
LOG_VERSION = 1
LOCK_STALE_MINUTES = 15
LOCK_FUTURE_SECONDS = 60     # a lock dated further in the future than this is treated as stale too
EXIT_INPUT, EXIT_LOCKED, EXIT_PENDING, EXIT_INCOMPLETE = 2, 3, 4, 5

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
KINDS = ["decision", "proposal"]
CADENCES = ["weekly", "on_request"]
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_configured"]
NOTES_STATUSES = ["ok", "denied", "unavailable"]
TRANSCRIPT_STATUSES = ["ok", "denied", "unavailable", "not_requested"]
QUOTE_SOURCES = ["transcript", "notes", "summary"]
QUOTE_TEXT_KEYS = {"transcript": "transcript_text", "notes": "notes_text", "summary": "summary_text"}
QUOTE_LABELS = {"transcript": "transcript", "notes": "meeting notes", "summary": "Granola AI summary, not verbatim speech"}
ACTIONS = ["dismiss", "restore", "set_owner", "set_revisit", "confirm", "resolve"]
RESOLUTIONS = ["supersedes", "both_stand"]
RUN_STATUSES = ["pending", "posted", "empty", "superseded", "incomplete"]
DONE_RUN_STATUSES = ["posted", "empty"]
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "pasted": "pasted transcripts"}
CONFIG_KEYS = ["plan_history_days", "max_quote_chars", "max_runs_logged", "max_mentions_kept", "search_max_results"]

ID_RE = re.compile(r"dl-[0-9a-f]{10}")
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MEETING_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+=-]{0,127}")
TOPIC_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*){0,3}")
AREA_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
EMAIL_RE = re.compile(r"[\w.%+-]+[@\uff20\ufe6b][\w-]+(?:\.[\w-]+)+")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# ASCII digits only; hours 00-23, minutes and seconds 00-59, offsets -14:00..+14:00 (so 3.8 and 3.14 agree)
MOMENT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                       r"(?::[0-5][0-9](?:\.(?:[0-9]{3}|[0-9]{6}))?)?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
PERIOD_RE = re.compile(r"weekly:([0-9]{4}-[0-9]{2}-[0-9]{2})\.\.([0-9]{4}-[0-9]{2}-[0-9]{2})")
YEARS = (1900, 2999)
MAX_INT = 36500
LIMITS = {"statement": 300, "quote": 1000, "rationale": 600, "name": 120, "alternative": 200, "title": 300,
          "note": 300, "detail": 300, "folder": 120, "plan": 40, "provider": 40, "url": 2000, "text": 5000000,
          "search": 300}
MAX_LIST = 10
STOPWORDS = {"the", "a", "an", "to", "for", "of", "our", "your", "their", "my", "his", "her", "its", "them", "him",
             "us", "me", "and", "or", "with", "on", "in", "at", "by", "this", "that", "these", "those", "we", "i",
             "you", "they", "it", "is", "are", "be", "was", "were", "will", "ll", "going", "go", "get", "just",
             "also", "all", "any", "from", "as", "so", "then", "than", "now", "into", "about", "s"}
SEARCH_STOPWORDS = STOPWORDS | {"when", "why", "what", "who", "which", "how", "did", "do", "does", "done", "decide",
                                "decided", "deciding", "decision", "decisions", "agree", "agreed", "choose", "chose",
                                "chosen", "there", "have", "has", "had", "ever", "last", "time", "make", "made",
                                "settle", "settled", "please", "tell", "show", "find", "log"}


class InputError(Exception):
    """Bad input; the message names the file and the JSON path."""


class StopRun(Exception):
    """A clean stop with its own exit code (lock held, period pending)."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code


# --------------------------------------------------------------------------- reading helpers


def in_years(d, value, fail):
    if not YEARS[0] <= d.year <= YEARS[1]:
        raise fail("%r is outside the years %d-%d" % (value, YEARS[0], YEARS[1]))
    return d


def parse_moment(value, fail):
    """Strict ISO-8601 datetime with a UTC offset; identical on Python 3.8 to 3.14."""
    if not isinstance(value, str) or not MOMENT_RE.fullmatch(value):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?",
                                                   value):
            raise fail("%r has no UTC offset; use e.g. 2026-10-09T16:00:00-07:00" % (value,))
        raise fail("%r is not an ISO-8601 datetime with a UTC offset (YYYY-MM-DDTHH:MM[:SS[.fff]]+HH:MM or Z; "
                   "hours 00-23, offset within +/-14:00)" % (clip_text(value),))
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise fail("%r is not a valid datetime" % (value,)) from None
    return in_years(moment, value, fail)


def parse_day(value, fail):
    """Strict YYYY-MM-DD."""
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise fail("%r is not a YYYY-MM-DD date" % (clip_text(value),))
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise fail("%r is not a valid date" % (value,)) from None
    return in_years(d, value, fail)


def clip_text(value, limit=60):
    """Error messages repeat at most this much of a bad value."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value if isinstance(value, str) else type(value).__name__


class Reader:
    """Typed accessors that report errors as '<label>: <json.path>: problem'."""

    def __init__(self, label):
        self.label = label

    def fail(self, path, problem):
        return InputError("%s: %s: %s" % (self.label, path, problem))

    def obj(self, value, path, required=True):
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        return value

    def lst(self, parent, key, path, required=False, limit=None):
        """A list. An absent optional key is []; a key set to null or another type is an error."""
        if key not in parent:
            if required:
                raise self.fail("%s.%s" % (path, key), "is required (use [] when there is nothing)")
            return []
        value = parent[key]
        if not isinstance(value, list):
            raise self.fail("%s.%s" % (path, key), "must be a list, got %s"
                            % ("null" if value is None else type(value).__name__))
        if limit is not None and len(value) > limit:
            raise self.fail("%s.%s" % (path, key), "has %d entries; at most %d allowed" % (len(value), limit))
        return value

    def text(self, parent, key, path, required=True, limit="text"):
        value = parent.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise self.fail("%s.%s" % (path, key), "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail("%s.%s" % (path, key), "must be a string, got %s" % type(value).__name__)
        value = value.strip()
        if len(value) > LIMITS[limit]:
            raise self.fail("%s.%s" % (path, key), "is %d characters; at most %d allowed"
                            % (len(value), LIMITS[limit]))
        return value

    def pattern(self, parent, key, path, regex, what, required=True, limit=128):
        value = self.text(parent, key, path, required, "text")
        if value is None:
            return None
        if len(value) > limit or not regex.fullmatch(value):
            raise self.fail("%s.%s" % (path, key), "%r is not %s" % (clip_text(value), what))
        return value

    def name(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required, "name")
        if value is not None and EMAIL_RE.search(value):
            raise self.fail("%s.%s" % (path, key), "must be a person's name, not an email address")
        return value

    def names(self, parent, key, path):
        out = []
        for i, value in enumerate(self.lst(parent, key, path, limit=MAX_LIST)):
            if not isinstance(value, str) or not value.strip():
                raise self.fail("%s.%s[%d]" % (path, key, i), "must be a non-empty string")
            name = value.strip()
            if len(name) > LIMITS["name"]:
                raise self.fail("%s.%s[%d]" % (path, key, i), "is longer than %d characters" % LIMITS["name"])
            if EMAIL_RE.search(name):
                raise self.fail("%s.%s[%d]" % (path, key, i), "must be a person's name, not an email address")
            if fold(name) not in [fold(x) for x in out]:
                out.append(name)
        return out

    def texts(self, parent, key, path, limit):
        out = []
        for i, value in enumerate(self.lst(parent, key, path, limit=MAX_LIST)):
            if not isinstance(value, str) or not value.strip():
                raise self.fail("%s.%s[%d]" % (path, key, i), "must be a non-empty string")
            if len(value.strip()) > LIMITS[limit]:
                raise self.fail("%s.%s[%d]" % (path, key, i), "is longer than %d characters" % LIMITS[limit])
            out.append(value.strip())
        return out

    def integer(self, parent, key, path, required=True, minimum=0, maximum=MAX_INT):
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise self.fail("%s.%s" % (path, key), "must be an integer from %d to %d, got %s"
                            % (minimum, maximum, clip_text(json.dumps(value)) if not isinstance(value, str)
                               else repr(clip_text(value))))
        return value

    def boolean(self, parent, key, path, default=None):
        value = parent.get(key, default)
        if not isinstance(value, bool):
            raise self.fail("%s.%s" % (path, key), "must be true or false")
        return value

    def enum(self, parent, key, path, allowed, default=None):
        value = parent.get(key, default)
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("%s.%s" % (path, key), "must be one of %s, got %s"
                            % (", ".join(allowed), repr(clip_text(value)) if isinstance(value, str)
                               else clip_text(json.dumps(value))))
        return value

    def day(self, parent, key, path, required=True):
        value = parent.get(key)
        if value is None and not required:
            return None
        return parse_day(value, lambda problem: self.fail("%s.%s" % (path, key), problem))

    def moment(self, parent, key, path, required=True):
        value = parent.get(key)
        if value is None and not required:
            return None
        return parse_moment(value, lambda problem: self.fail("%s.%s" % (path, key), problem))


SURROGATE_RE = re.compile("[\ud800-\udfff]")


def reject_surrogates(data, label):
    """A \\ud800-style escape decodes to a lone surrogate that can't be written as UTF-8: refuse it up front."""
    stack = [(data, "$")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if SURROGATE_RE.search(key):
                    raise InputError("%s: %s: a key contains a lone surrogate (\\ud800-\\udfff escape); not valid text"
                                     % (label, path))
                stack.append((value, "%s.%s" % (path, key)))
        elif isinstance(node, list):
            stack.extend((value, "%s[%d]" % (path, i)) for i, value in enumerate(node))
        elif isinstance(node, str) and SURROGATE_RE.search(node):
            raise InputError("%s: %s: contains a lone surrogate (\\ud800-\\udfff escape); not valid text" % (label, path))


def reject_constant(name):
    raise ValueError("%s is not allowed; use a number or null" % name)


def load_json(path, label):
    if not path.is_file():
        raise InputError("%s: file not found: %s" % (label, path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except OSError as exc:
        raise InputError("%s: %s cannot be read (%s)" % (label, path, exc.strerror or exc)) from None
    except UnicodeDecodeError:
        raise InputError("%s: %s is not UTF-8 text" % (label, path)) from None
    except json.JSONDecodeError as exc:
        raise InputError("%s: %s is not valid JSON (line %d, column %d: %s)"
                         % (label, path, exc.lineno, exc.colno, exc.msg)) from None
    except ValueError as exc:
        raise InputError("%s: %s is not valid JSON (%s)" % (label, path, exc)) from None
    except RecursionError:
        raise InputError("%s: %s is nested too deeply to be a valid file" % (label, path)) from None
    reject_surrogates(data, "%s %s" % (label, path))
    return data


def load_config(path):
    config = load_json(path, "config")
    r = Reader("config %s" % path)
    r.obj(config, "$")
    missing = [key for key in CONFIG_KEYS if key not in config]
    if missing:
        raise InputError("config %s: missing key(s): %s" % (path, ", ".join(missing)))
    r.integer(config, "max_quote_chars", "$", minimum=40, maximum=LIMITS["quote"])
    r.integer(config, "max_runs_logged", "$", minimum=4, maximum=10000)
    r.integer(config, "max_mentions_kept", "$", minimum=1, maximum=1000)
    r.integer(config, "search_max_results", "$", minimum=1, maximum=200)
    plans = r.obj(config["plan_history_days"], "$.plan_history_days")
    for plan in plans:
        r.integer(plans, plan, "$.plan_history_days", minimum=1)
    config["plan_history_days"] = dict((k.lower(), v) for k, v in plans.items())
    return config


# --------------------------------------------------------------------------- normalisation


def fold(value):
    value = unicodedata.normalize("NFKC", value)
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"),
                 ("\u2014", "-"), ("\u2026", "...")):
        value = value.replace(a, b)
    return value.casefold()


def norm_text(value):
    """Casefolded, punctuation-free, single-spaced text (used for ids)."""
    return " ".join(re.sub(r"[^\w]+", " ", fold(value)).split())


def norm_statement(value):
    words = [w for w in norm_text(value).split() if w not in STOPWORDS]
    return " ".join(words) or norm_text(value)


def make_id(meeting_id, quote, topic):
    raw = "%s|%s|%s" % (meeting_id, norm_text(quote), topic)
    return "dl-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


# --------------------------------------------------------------------------- links and escaping

URL_RE = re.compile(r"(https?)://([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?([/?#].*)?", re.IGNORECASE)
LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
TLD_RE = re.compile(r"[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{1,59}", re.IGNORECASE)
# Removed from printed text: control characters, soft hyphen, zero-width space, word joiner, BOM, and the
# direction marks, embeddings, overrides and isolates that can reorder what a reader sees.
INVISIBLE_RE = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x84\x86-\x9f\u00ad\u180e\u200b\u200e\u200f\u202a-\u202e"
                          "\u2060-\u2064\u2066-\u2069\ufeff]")


def safe_url(value):
    """An absolute http(s) URL with a DNS host name (letter/digit/hyphen labels, alphabetic TLD) and an optional
    port 1-65535, unsafe characters percent-encoded; None if not acceptable (IP literals and localhost included)."""
    if not isinstance(value, str) or len(value) > LIMITS["url"] or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    m = URL_RE.fullmatch(value.strip())
    if not m:
        return None
    labels = m.group(2).split(".")
    if len(labels) < 2 or len(m.group(2)) > 253 or not all(LABEL_RE.fullmatch(x) for x in labels) \
            or not TLD_RE.fullmatch(labels[-1]):
        return None
    port = m.group(3)
    if port is not None and not 1 <= int(port) <= 65535:
        return None
    try:
        rest = url_quote(m.group(4) or "", safe="/?#&=+:;,@!$'*~%-._")
    except UnicodeError:
        return None
    return "%s://%s%s%s" % (m.group(1).lower(), m.group(2).lower(), ":" + port if port else "", rest)


def clean(value):
    """One line of plain text: whitespace collapsed, control, bidi and invisible characters removed."""
    return " ".join(INVISIBLE_RE.sub("", value).split())


class Markdown:
    """Markdown for Type docs and Spaces. Values go through granola_text.md: one line; the backslash escaped first,
    then ` * _ ~ [ ] ( ) | # @ ! $ = ^ : ; & < > as entities; every dot inside a word escaped; a leading list,
    ordered-list, heading or quote marker neutralised. So a value can't form a link, image, autolink, raw HTML, math,
    highlight, emphasis, code, table, heading or mention. Backslash escapes render as the character itself."""
    name = "markdown"

    def text(self, value):
        return gt.md(value)

    def bold(self, inner):
        return "**%s**" % inner

    def heading(self, level, inner):
        return "#" * level + " " + inner

    def code(self, safe):
        return "`%s`" % safe

    def link(self, label, url):
        url = safe_url(url) if url else None
        return "[%s](%s)" % (self.text(label), url) if url else self.text(label)


class Slack(Markdown):
    """Slack mrkdwn via granola_text.slack. Slack has no escape character: & < > become entities (so <!channel>,
    <@U...>, <#C...> and <url|label> are inert), * _ ~ ` @ [ ] become look-alikes (so values can't open or close
    formatting or mention anyone), and scheme://, domain dots and IPv4 dots are broken (so Slack can't auto-link a
    value)."""
    name = "slack"

    def text(self, value):
        return gt.slack(value)

    def bold(self, inner):
        return "*%s*" % inner

    def heading(self, level, inner):
        return "*%s*" % inner

    def link(self, label, url):
        url = safe_url(url) if url else None
        if not url:
            return self.text(label)
        url = url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "<%s|%s>" % (url, self.text(label).replace("|", " "))


FORMATS = {"markdown": Markdown(), "slack": Slack()}


# --------------------------------------------------------------------------- bundle


def load_bundle(path, as_of):
    raw = load_json(path, "bundle")
    r = Reader("bundle %s" % path)
    r.obj(raw, "$")
    if raw.get("schema_version") != SCHEMA_VERSION or isinstance(raw.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be %d" % SCHEMA_VERSION)
    today = as_of.date()

    scan_raw = r.obj(raw.get("scan"), "$.scan")
    scan = {"from": r.day(scan_raw, "from", "$.scan"), "to": r.day(scan_raw, "to", "$.scan"),
            "cadence": r.enum(scan_raw, "cadence", "$.scan", CADENCES),
            "folder": r.text(scan_raw, "folder", "$.scan", required=False, limit="folder"),
            "workspace_only": r.boolean(scan_raw, "workspace_only", "$.scan", default=False)}
    if scan["from"] > scan["to"]:
        raise r.fail("$.scan", "from (%s) is after to (%s)" % (scan["from"], scan["to"]))
    if scan["to"] > today:
        raise r.fail("$.scan.to", "%s is after --as-of (%s)" % (scan["to"], today))

    sources_raw = r.obj(raw.get("sources"), "$.sources")
    s = r.obj(sources_raw.get("meetings"), "$.sources.meetings")
    source = {"provider": r.text(s, "provider", "$.sources.meetings", limit="provider").lower(),
              "status": r.enum(s, "status", "$.sources.meetings", SOURCE_STATUSES),
              "detail": r.text(s, "detail", "$.sources.meetings", required=False, limit="detail"),
              "plan": r.text(s, "plan", "$.sources.meetings", required=False, limit="plan"),
              "history_limit_days": r.integer(s, "history_limit_days", "$.sources.meetings", required=False,
                                              minimum=1)}

    url_warnings = []
    meetings = {}
    order = []
    for i, m in enumerate(r.lst(raw, "meetings", "$")):
        p = "$.meetings[%d]" % i
        r.obj(m, p)
        mid = r.pattern(m, "meeting_id", p, MEETING_ID_RE, "a meeting id (letters, digits, _ . : / + = -)")
        if mid in meetings:
            raise r.fail(p + ".meeting_id", "duplicate meeting_id %r" % mid)
        start = r.moment(m, "start", p)
        if not (scan["from"] <= start.date() <= scan["to"]):
            raise r.fail(p + ".start", "%s is outside the scan window %s..%s" % (start.date(), scan["from"], scan["to"]))
        if start.astimezone(timezone.utc) > as_of.astimezone(timezone.utc):
            raise r.fail(p + ".start", "is after --as-of")
        url = r.text(m, "url", p, required=False, limit="url")
        if url is not None and safe_url(url) is None:
            url_warnings.append(p + ".url")
        meeting = {"meeting_id": mid, "title": r.text(m, "title", p, limit="title"), "start": start,
                   "day": start.date(), "url": safe_url(url) if url else None,
                   "notes_status": r.enum(m, "notes_status", p, NOTES_STATUSES),
                   "transcript_status": r.enum(m, "transcript_status", p, TRANSCRIPT_STATUSES), "index": i}
        for source_name, key in sorted(QUOTE_TEXT_KEYS.items()):
            if isinstance(m.get(key), str) and not m[key].strip():
                raise r.fail("%s.%s" % (p, key), "is empty; an empty %s usually means the fetch failed. Fetch it "
                             "again, or leave the key out so its quotes are reported as not checked" % key)
            value = r.text(m, key, p, required=False, limit="text")
            status = meeting["transcript_status"] if source_name == "transcript" else meeting["notes_status"]
            if value is not None and status != "ok":
                raise r.fail("%s.%s" % (p, key), "is given but %s_status is %s"
                             % ("transcript" if source_name == "transcript" else "notes", status))
            meeting[key] = value
        meetings[mid] = meeting
        order.append(mid)

    decisions = []
    refs = set()
    for i, d in enumerate(r.lst(raw, "decisions", "$")):
        p = "$.decisions[%d]" % i
        r.obj(d, p)
        ref = r.pattern(d, "ref", p, REF_RE, "a ref (letters, digits, _ . -; up to 64)", limit=64)
        if ref in refs:
            raise r.fail(p + ".ref", "duplicate ref %r" % ref)
        refs.add(ref)
        mid = r.text(d, "meeting_id", p, limit="title")
        if mid not in meetings:
            raise r.fail(p + ".meeting_id", "%r is not in $.meetings; every decision must cite a meeting in this "
                         "bundle" % clip_text(mid))
        meeting = meetings[mid]
        kind = r.enum(d, "kind", p, KINDS)
        quote = r.text(d, "quote", p, limit="quote")
        quote_source = r.enum(d, "quote_source", p, QUOTE_SOURCES)
        if quote_source == "transcript" and meeting["transcript_status"] != "ok":
            raise r.fail(p + ".quote_source", "is 'transcript' but the transcript of %r was not read (status %s); "
                         "quote the notes instead" % (mid, meeting["transcript_status"]))
        if quote_source in ("notes", "summary") and meeting["notes_status"] != "ok":
            raise r.fail(p + ".quote_source", "is %r but the notes of %r were not read (status %s)"
                         % (quote_source, mid, meeting["notes_status"]))
        text = meeting[QUOTE_TEXT_KEYS[quote_source]]
        checked = False
        if text is not None:
            ok, reason = gt.check_quote(quote, text)
            if not ok:
                raise r.fail(p + ".quote", "%s (checked against the meeting's %s, %s, in this bundle); quote one "
                             "complete sentence, or several consecutive ones, from a single speaker's turn, word for "
                             "word, without a speaker label or an ellipsis"
                             % (reason, QUOTE_TEXT_KEYS[quote_source], clip_text(mid)))
            checked = True
        topic = r.pattern(d, "topic", p, TOPIC_RE, "a topic key like pricing/annual-discount (lowercase a-z, 0-9, "
                          "hyphens, up to 4 parts joined by /, at most 80 characters)", limit=80)
        area = r.pattern(d, "area", p, AREA_RE, "an area tag like customer-success (lowercase a-z, 0-9, hyphens, "
                         "at most 40 characters)", limit=40)
        revisit = r.day(d, "revisit_on", p, required=False)
        if revisit is not None and revisit < meeting["day"]:
            raise r.fail(p + ".revisit_on", "%s is before the meeting (%s)" % (revisit, meeting["day"]))
        same_as = r.text(d, "same_as", p, required=False, limit="title")
        supersedes = r.texts(d, "supersedes", p, "title")
        if kind == "proposal" and supersedes:
            raise r.fail(p + ".supersedes", "a proposal can't supersede anything; only a decision can")
        if same_as is not None and supersedes:
            raise r.fail(p, "give same_as (a repeat) or supersedes (a change), not both")
        owner = r.name(d, "owner", p, required=False)
        if kind == "proposal" and owner is not None:
            raise r.fail(p + ".owner", "proposals have no owner; put who proposed it in decided_by")
        decisions.append({
            "ref": ref, "index": i, "path": p, "meeting_id": mid, "kind": kind,
            "statement": r.text(d, "statement", p, limit="statement"), "topic": topic, "area": area,
            "decided_by": r.names(d, "decided_by", p), "owner": owner,
            "rationale": r.text(d, "rationale", p, required=False, limit="rationale"),
            "alternatives": r.texts(d, "alternatives_rejected", p, "alternative"),
            "revisit_on": revisit, "quote": quote, "quote_source": quote_source, "quote_checked": checked,
            "same_as": same_as, "supersedes": supersedes})

    actions = []
    for i, a in enumerate(r.lst(raw, "user_actions", "$")):
        p = "$.user_actions[%d]" % i
        r.obj(a, p)
        action = r.enum(a, "action", p, ACTIONS)
        entry = {"index": i, "path": p, "action": action,
                 "decision_id": r.pattern(a, "decision_id", p, ID_RE, "a logged id (dl- and 10 hex digits)", limit=13),
                 "date": r.day(a, "date", p), "note": r.text(a, "note", p, required=False, limit="note")}
        if entry["date"] > today:
            raise r.fail(p + ".date", "%s is after --as-of" % entry["date"])
        allowed = {"set_owner": ["owner"], "set_revisit": ["revisit_on"], "resolve": ["other_id", "resolution"]}
        for key in ["owner", "revisit_on", "other_id", "resolution"]:
            if key in a and key not in allowed.get(action, []):
                raise r.fail("%s.%s" % (p, key), "is not used by action %s" % action)
        if action == "set_owner":
            entry["owner"] = r.name(a, "owner", p)
        if action == "set_revisit":
            if "revisit_on" not in a:
                raise r.fail(p + ".revisit_on", "is required (a YYYY-MM-DD date, or null to clear it)")
            entry["revisit_on"] = r.day(a, "revisit_on", p, required=False)
        if action == "resolve":
            entry["other_id"] = r.pattern(a, "other_id", p, ID_RE, "a logged id (dl- and 10 hex digits)", limit=13)
            entry["resolution"] = r.enum(a, "resolution", p, RESOLUTIONS)
            if entry["other_id"] == entry["decision_id"]:
                raise r.fail(p + ".other_id", "must differ from decision_id")
        actions.append(entry)

    return {"scan": scan, "source": source, "meetings": meetings, "order": order, "decisions": decisions,
            "actions": actions, "url_warnings": url_warnings}


# --------------------------------------------------------------------------- the log file


def empty_log():
    return {"log_version": LOG_VERSION, "records": {}, "runs": [], "updated_at": None}


class LogCheck(Reader):
    """Validates every field of a log file; any problem stops the run before anything is printed."""

    def __init__(self, path):
        Reader.__init__(self, "log %s" % path)

    def fail(self, path, problem):
        return InputError("%s: %s: %s; refusing to run blind" % (self.label, path, problem))

    def value(self, parent, key, path, kind, nullable=False):
        if key not in parent:
            raise self.fail("%s.%s" % (path, key), "is missing")
        value = parent[key]
        if value is None and nullable:
            return None
        if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
            raise self.fail("%s.%s" % (path, key), "has the wrong type")
        return value

    def stored_text(self, parent, key, path, limit, nullable=False):
        value = self.value(parent, key, path, str, nullable)
        if value is None:
            return None
        if not value.strip() or len(value) > LIMITS[limit]:
            raise self.fail("%s.%s" % (path, key), "is empty or too long")
        return value

    def stored_day(self, parent, key, path, nullable=False):
        value = self.value(parent, key, path, str, nullable)
        return None if value is None else self.day(parent, key, path)

    def stored_moment(self, parent, key, path, nullable=False):
        value = self.value(parent, key, path, str, nullable)
        return None if value is None else self.moment(parent, key, path)

    def stored_names(self, parent, key, path):
        values = self.value(parent, key, path, list)
        if len(values) > MAX_LIST or any(not isinstance(v, str) or not v.strip() or len(v) > LIMITS["name"]
                                         for v in values):
            raise self.fail("%s.%s" % (path, key), "must be a list of up to %d names" % MAX_LIST)
        return values

    def stored_url(self, parent, key, path):
        value = self.value(parent, key, path, str, nullable=True)
        if value is not None and safe_url(value) != value:
            raise self.fail("%s.%s" % (path, key), "is not a link this script would have stored")
        return value

    def mention(self, m, path):
        self.obj(m, path)
        self.pattern(m, "meeting_id", path, MEETING_ID_RE, "a meeting id")
        self.stored_text(m, "title", path, "title")
        self.stored_day(m, "date", path)
        self.stored_url(m, "url", path)
        self.stored_text(m, "quote", path, "quote")
        self.enum(m, "quote_source", path, QUOTE_SOURCES)

    def record(self, rid, rec, path, ids):
        self.obj(rec, path)
        if not ID_RE.fullmatch(rid) or rec.get("id") != rid:
            raise self.fail(path + ".id", "must equal its key and look like dl- plus 10 hex digits")
        kind = self.enum(rec, "kind", path, KINDS)
        self.stored_text(rec, "statement", path, "statement")
        self.pattern(rec, "topic", path, TOPIC_RE, "a topic key", limit=80)
        self.pattern(rec, "area", path, AREA_RE, "an area tag", limit=40)
        self.stored_names(rec, "decided_by", path)
        self.stored_text(rec, "owner", path, "name", nullable=True)
        self.stored_day(rec, "owner_set", path, nullable=True)
        self.stored_text(rec, "rationale", path, "rationale", nullable=True)
        alts = self.value(rec, "alternatives", path, list)
        if len(alts) > MAX_LIST or any(not isinstance(v, str) or not v.strip() or len(v) > LIMITS["alternative"]
                                       for v in alts):
            raise self.fail(path + ".alternatives", "must be a list of up to %d short texts" % MAX_LIST)
        self.stored_day(rec, "revisit_on", path, nullable=True)
        self.stored_day(rec, "revisit_set", path, nullable=True)
        origin = self.value(rec, "origin", path, dict)
        self.mention(origin, path + ".origin")
        self.stored_moment(origin, "start", path + ".origin")
        self.value(origin, "quote_checked", path + ".origin", bool)
        if origin["start"][:10] != origin["date"]:
            raise self.fail(path + ".origin.date", "does not match origin.start")
        mentions = self.value(rec, "mentions", path, list)
        for i, m in enumerate(mentions):
            self.mention(m, "%s.mentions[%d]" % (path, i))
        count = self.value(rec, "mention_count", path, int)
        if not len(mentions) <= count <= 10 ** 9:
            raise self.fail(path + ".mention_count", "is smaller than the mentions kept")
        sup = self.value(rec, "supersedes", path, list)
        if len(sup) > 100 or any(not isinstance(x, str) or x not in ids or x == rid for x in sup) \
                or len(set(sup)) != len(sup):
            raise self.fail(path + ".supersedes", "must list other logged ids, once each")
        if sup and kind == "proposal" and rec.get("confirmed") is None:
            raise self.fail(path + ".supersedes", "an unconfirmed proposal can't supersede anything")
        conflicts = self.value(rec, "conflicts", path, list)
        seen = set()
        for i, c in enumerate(conflicts):
            cp = "%s.conflicts[%d]" % (path, i)
            self.obj(c, cp)
            other = self.value(c, "with", cp, str)
            if other not in ids or other == rid or other in seen:
                raise self.fail(cp + ".with", "must be another logged id, once")
            seen.add(other)
            if self.value(c, "resolution", cp, str, nullable=True) is not None:
                self.enum(c, "resolution", cp, RESOLUTIONS)
                self.stored_day(c, "date", cp)
            elif c.get("date") is not None:
                raise self.fail(cp + ".date", "must be null while unresolved")
        for key in ["dismissed", "confirmed"]:
            entry = self.value(rec, key, path, dict, nullable=True)
            if entry is not None:
                self.stored_day(entry, "date", "%s.%s" % (path, key))
                self.stored_text(entry, "note", "%s.%s" % (path, key), "note", nullable=True)
        if rec["confirmed"] is not None and kind != "proposal":
            raise self.fail(path + ".confirmed", "only a proposal can be confirmed")
        self.stored_moment(rec, "logged_at", path)
        self.stored_moment(rec, "reported_at", path, nullable=True)
        history = self.value(rec, "history", path, list)
        for i, h in enumerate(history):
            hp = "%s.history[%d]" % (path, i)
            self.obj(h, hp)
            self.enum(h, "action", hp, ACTIONS)
            self.stored_day(h, "date", hp)
            for key, check in [("note", lambda k: self.stored_text(h, k, hp, "note", nullable=True)),
                               ("owner", lambda k: self.stored_text(h, k, hp, "name", nullable=True)),
                               ("revisit_on", lambda k: self.stored_day(h, k, hp, nullable=True)),
                               ("other_id", lambda k: self.pattern(h, k, hp, ID_RE, "a logged id", required=False)),
                               ("resolution", lambda k: h.get(k) is None or self.enum(h, k, hp, RESOLUTIONS))]:
                if key in h:
                    check(key)

    def run(self, run, path, ids):
        self.obj(run, path)
        key = self.value(run, "period_key", path, str)
        if period_bounds(key) is None:
            raise self.fail(path + ".period_key", "is not a weekly period key")
        self.stored_moment(run, "as_of", path)
        status = self.enum(run, "status", path, RUN_STATUSES)
        staged = self.value(run, "staged", path, list, nullable=True)
        if (staged is None) != (status not in ("pending", "posted", "superseded")):
            raise self.fail(path + ".staged", "does not fit status %s" % status)
        if staged is not None and any(not isinstance(x, str) or x not in ids for x in staged):
            raise self.fail(path + ".staged", "must list logged ids")
        self.stored_moment(run, "confirmed_at", path, nullable=True)
        if "notice_posted" in run:
            if not isinstance(run["notice_posted"], bool) or (run["notice_posted"] and status != "incomplete"):
                raise self.fail(path + ".notice_posted", "must be true or false, and true only on an incomplete run")
        counts = self.value(run, "counts", path, dict)
        for k in counts:
            self.integer(counts, k, path + ".counts")


def load_log(path):
    """Read and fully validate the log file."""
    if not path.exists():
        return empty_log()
    if not path.is_file():
        raise InputError("log %s: is not a regular file; refusing to run blind" % path)
    try:
        log = load_json(path, "log")
    except InputError as exc:
        raise InputError("%s; refusing to run blind" % exc) from None
    check = LogCheck(path)
    if not isinstance(log, dict) or log.get("log_version") != LOG_VERSION or isinstance(log.get("log_version"), bool):
        raise InputError("log %s: not a decision-log file (log_version %d expected); refusing to run blind"
                         % (path, LOG_VERSION))
    records = check.value(log, "records", "$", dict)
    runs = check.value(log, "runs", "$", list)
    check.stored_moment(log, "updated_at", "$", nullable=True)
    ids = set(records)
    for rid in sorted(records):
        check.record(rid, records[rid], "$.records.%s" % rid, ids)
    for i, run in enumerate(runs):
        check.run(run, "$.runs[%d]" % i, ids)
    cycle = find_cycle(records)
    if cycle:
        raise check.fail("$.records.%s.supersedes" % cycle, "forms a cycle")
    return log


def find_cycle(records):
    state = {}
    for start in sorted(records):
        if state.get(start):
            continue
        stack = [(start, iter(records[start]["supersedes"]))]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return node
            elif not state.get(nxt):
                state[nxt] = 1
                stack.append((nxt, iter(records[nxt]["supersedes"])))
    return None


def check_log_path(path, writing):
    """Fail before anything is printed if the log could not be read or written."""
    if path.exists() and not path.is_file():
        raise InputError("log %s: is not a regular file" % path)
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.is_dir():
        raise InputError("log %s: folder %s does not exist" % (path, parent))
    if writing and not os.access(str(parent), os.W_OK):
        raise InputError("log %s: folder %s is not writable, so the run could not be recorded" % (path, parent))


def write_log(path, log):
    text = json.dumps(log, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    directory = str(path.parent) if str(path.parent) else "."
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".decision-log-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, str(path))
    except OSError as exc:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
        raise InputError("log %s: could not be written (%s); nothing was printed or recorded"
                         % (path, exc.strerror or exc)) from None


def remove_if_same(path, before):
    """Remove a stale lock only if it is still the very file that was judged stale (device, inode and mtime)."""
    try:
        now = os.lstat(str(path))
        if (now.st_dev, now.st_ino, now.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_mtime_ns):
            return False
        os.unlink(str(path))
    except OSError:
        return False
    return True


class LogLock:
    """Exclusive <log>.lock (O_CREAT|O_EXCL) around read-modify-write. A lock older than LOCK_STALE_MINUTES, or dated
    more than LOCK_FUTURE_SECONDS in the future, is stale; release removes only a lock holding this run's token."""

    def __init__(self, log_path):
        self.path = Path(str(log_path) + ".lock")
        self.token = None

    def not_a_file(self):
        return InputError("log lock %s: is not a regular file; remove it (only if no run is active) and rerun"
                          % self.path)

    def acquire(self):
        token = "%d-%s" % (os.getpid(), os.urandom(8).hex())
        for attempt in (1, 2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    found = os.lstat(str(self.path))
                except OSError:
                    continue
                if not stat.S_ISREG(found.st_mode):
                    raise self.not_a_file() from None
                age = time.time() - found.st_mtime
                if attempt == 1 and (age > LOCK_STALE_MINUTES * 60 or age < -LOCK_FUTURE_SECONDS):
                    remove_if_same(self.path, found)
                    continue
                raise StopRun(EXIT_LOCKED, "%s exists: another run is updating this decision log (lock is %d s old; "
                              "it counts as stale after %d minutes). Try again later; delete the lock only if no run "
                              "is active." % (self.path, max(age, 0), LOCK_STALE_MINUTES))
            except OSError as exc:
                if os.path.isdir(str(self.path)):
                    raise self.not_a_file() from None
                raise InputError("log lock %s: cannot be created (%s)" % (self.path, exc.strerror or exc)) from None
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("pid %d token %s\n" % (os.getpid(), token))
            self.token = token
            return
        raise StopRun(EXIT_LOCKED, "%s: could not take the log lock; try again later." % self.path)

    def release(self):
        token, self.token = self.token, None
        if token is None:
            return
        try:
            with open(str(self.path), encoding="utf-8") as handle:
                mine = handle.read().split() == ["pid", str(os.getpid()), "token", token]
            if mine:
                os.unlink(str(self.path))
        except (OSError, UnicodeDecodeError):
            pass


# --------------------------------------------------------------------------- periods


def iso(d):
    return d.isoformat() if d is not None else None


def instant(value):
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def to_day(value):
    return date.fromisoformat(value) if value else None


def period_bounds(key):
    """(from, to) of a period key such as weekly:2026-10-05..2026-10-09, or None if malformed."""
    m = PERIOD_RE.fullmatch(key) if isinstance(key, str) else None
    if not m:
        return None
    try:
        start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    except ValueError:
        return None
    if start > end or not YEARS[0] <= start.year <= end.year <= YEARS[1]:
        return None
    return start, end


def week_of(day):
    return day - timedelta(days=day.weekday())


def covered_to(run):
    """First day the next window must include after this run: the last day of its window, or the day before when a
    meeting in a zone behind the run's (offsets down to -14:00) could still carry that earlier date."""
    behind = (instant(run["as_of"]).astimezone(timezone.utc) - timedelta(hours=14)).date()
    return min(period_bounds(run["period_key"])[1], behind)


def last_done_run(log):
    done = [r for r in log["runs"] if r["status"] in DONE_RUN_STATUSES]
    return max(done, key=lambda r: (covered_to(r), instant(r["as_of"]))) if done else None


def earliest_unposted(log):
    starts = [period_bounds(r["period_key"])[0] for r in log["runs"] if r["status"] not in DONE_RUN_STATUSES]
    return min(starts) if starts else None


def posted_in_week(log, day):
    """The confirmed (or empty) run whose window ends in the same Monday-Sunday week as day, if any."""
    week = week_of(day)
    done = [r for r in log["runs"] if r["status"] in DONE_RUN_STATUSES
            and week_of(period_bounds(r["period_key"])[1]) == week]
    return max(done, key=lambda r: instant(r["as_of"])) if done else None


def notice_in_week(log, day):
    """The incomplete run whose Incomplete notice was recorded as posted, with a window ending in day's week."""
    week = week_of(day)
    done = [r for r in log["runs"] if r["status"] == "incomplete" and r.get("notice_posted")
            and week_of(period_bounds(r["period_key"])[1]) == week]
    return max(done, key=lambda r: instant(r["as_of"])) if done else None


def next_window(log, as_of):
    today = as_of.date()
    last = last_done_run(log)
    unposted = earliest_unposted(log)
    posted = posted_in_week(log, today)
    notice = notice_in_week(log, today)
    if last is not None:
        start = min(covered_to(last), today)
    elif unposted is not None:
        start = min(unposted, today)
    else:
        start = week_of(today)
    return {"from": iso(start), "to": iso(today), "cadence": "weekly",
            "period_key": "weekly:%s..%s" % (iso(start), iso(today)),
            "last_posted": {"period_key": last["period_key"], "as_of": last["as_of"]} if last else None,
            "already_posted": {"period_key": posted["period_key"], "as_of": posted["as_of"]} if posted else None,
            "incomplete_notice_posted": {"period_key": notice["period_key"], "as_of": notice["as_of"]} if notice else None,
            "pending": [r["period_key"] for r in log["runs"] if r["status"] == "pending"]}


# --------------------------------------------------------------------------- status


def eff_kind(rec):
    return "decision" if rec["kind"] == "decision" or rec["confirmed"] is not None else "proposal"


def eff_date(rec):
    """The day an entry became a decision: its meeting, or the day you confirmed a proposal."""
    if rec["kind"] == "proposal" and rec["confirmed"] is not None:
        return to_day(rec["confirmed"]["date"])
    return to_day(rec["origin"]["date"])


def superseders(records):
    """id -> ids of the live (not dismissed) decisions that supersede it."""
    out = {}
    for rid in sorted(records):
        rec = records[rid]
        if rec["dismissed"] is None and eff_kind(rec) == "decision":
            for target in rec["supersedes"]:
                out.setdefault(target, []).append(rid)
    return out


def status_of(rec, sup_by):
    if rec["dismissed"] is not None:
        return "dismissed"
    if sup_by.get(rec["id"]):
        return "superseded" if eff_kind(rec) == "decision" else "decided"
    return "active" if eff_kind(rec) == "decision" else "open"


def statuses(records):
    sup_by = superseders(records)
    return dict((rid, status_of(rec, sup_by)) for rid, rec in records.items()), sup_by


def reaches(records, start, goal):
    """Whether start supersedes goal directly or through a chain."""
    seen, stack = set(), [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(records[node]["supersedes"])
    return False


def open_conflicts(records, status):
    """(newer, older, topic) for flagged pairs where both are still live decisions and nothing resolved them."""
    out = []
    for rid in sorted(records):
        rec = records[rid]
        for c in rec["conflicts"]:
            other = c["with"]
            if c["resolution"] is None and status.get(rid) == "active" and status.get(other) == "active":
                out.append((rid, other))
    return out


# --------------------------------------------------------------------------- merge


def mention_of(d, meeting):
    return {"meeting_id": meeting["meeting_id"], "title": meeting["title"], "date": iso(meeting["day"]),
            "url": meeting["url"], "quote": d["quote"], "quote_source": d["quote_source"]}


def add_mention(rec, d, meeting, config):
    rec["mentions"].append(mention_of(d, meeting))
    rec["mentions"].sort(key=lambda m: (m["date"], m["meeting_id"]))
    rec["mention_count"] += 1
    keep = config["max_mentions_kept"]
    if len(rec["mentions"]) > keep:
        rec["mentions"] = rec["mentions"][-keep:]


def find_logged(records, d, cid, claimed, same_quote):
    """A re-extraction of something already logged: same id, or the same meeting and quote (as its origin or as a
    linked repeat). When that meeting and quote gave exactly one logged entry and this bundle has exactly one
    decision from it, they are the same entry even if the topic key or wording changed. When either side has more
    than one, a match also needs the same topic or the same wording, so two decisions taken from one quote stay two.
    An entry already matched by another decision in this bundle (or created by it) is not matched again."""
    if cid in records:
        return cid
    qn = norm_text(d["quote"])
    every = [rid for rid in sorted(records) if records[rid]["origin"]["meeting_id"] == d["meeting_id"]
             and norm_text(records[rid]["origin"]["quote"]) == qn]
    origin = [rid for rid in every if rid not in claimed]
    if not (len(every) == 1 and same_quote == 1):
        ns = norm_statement(d["statement"])
        origin = [rid for rid in origin if records[rid]["topic"] == d["topic"]
                  or norm_statement(records[rid]["statement"]) == ns]
    if len(origin) == 1:
        return origin[0]
    repeats = [rid for rid in sorted(records) if any(m["meeting_id"] == d["meeting_id"] and norm_text(m["quote"]) == qn
                                                    for m in records[rid]["mentions"])]
    if len(repeats) > 1:
        repeats = [rid for rid in repeats if records[rid]["topic"] == d["topic"]]
    return repeats[0] if len(repeats) == 1 else None


def differs(rec, d, refs, records):
    """Whether a re-extraction asks for links the logged entry doesn't have (they are not applied)."""
    def target(value):
        return refs.get(value, value if value in records else None)
    if d["same_as"] is not None and target(d["same_as"]) != rec["id"]:
        return True
    return any(target(v) not in rec["supersedes"] for v in d["supersedes"])


def resolve_link(value, refs, records, r, path):
    if value in refs:
        return refs[value]
    if ID_RE.fullmatch(value):
        if value not in records:
            raise r.fail(path, "%r is not in the decision log" % value)
        return value
    raise r.fail(path, "%r is neither a logged id nor the ref of an earlier decision in this bundle (refs only point "
                 "back in time: order is by meeting date, then start time, then position)" % clip_text(value))


def apply_actions(bundle, log, r, stats):
    records = log["records"]
    for a in sorted(bundle["actions"], key=lambda a: (a["date"], a["index"])):
        p = a["path"]
        rec = records.get(a["decision_id"])
        if rec is None:
            raise r.fail(p + ".decision_id", "%r is not in the decision log" % a["decision_id"])
        if a["date"] < to_day(rec["origin"]["date"]):
            raise r.fail(p + ".date", "%s is before the entry was made (%s)" % (a["date"], rec["origin"]["date"]))
        entry = {"action": a["action"], "date": iso(a["date"]), "note": a["note"]}
        for key in ["owner", "other_id", "resolution"]:
            if key in a:
                entry[key] = a[key]
        if "revisit_on" in a:
            entry["revisit_on"] = iso(a["revisit_on"])
        if entry in rec["history"]:
            stats["actions_repeat"] += 1
            continue
        status, _ = statuses(records)
        kind = eff_kind(rec)
        action = a["action"]
        if action == "dismiss":
            if rec["dismissed"] is None:
                rec["dismissed"] = {"date": iso(a["date"]), "note": a["note"]}
            recheck_conflicts(records)
        elif action == "restore":
            rec["dismissed"] = None
            recheck_conflicts(records)
        elif action in ("set_owner", "set_revisit"):
            if kind != "decision":
                raise r.fail(p + ".action", "%s applies to decisions; %s is an unconfirmed proposal (confirm it "
                             "first, only if it was actually decided)" % (action, rec["id"]))
            if action == "set_owner":
                rec["owner"] = a["owner"]
                rec["owner_set"] = iso(a["date"])
            else:
                if a["revisit_on"] is not None and a["revisit_on"] < to_day(rec["origin"]["date"]):
                    raise r.fail(p + ".revisit_on", "is before the decision (%s)" % rec["origin"]["date"])
                rec["revisit_on"] = iso(a["revisit_on"])
                rec["revisit_set"] = iso(a["date"])
        elif action == "confirm":
            if rec["kind"] != "proposal":
                raise r.fail(p + ".action", "%s is already a decision" % rec["id"])
            if status[rec["id"]] != "open":
                raise r.fail(p + ".action", "%s is %s, not an open proposal" % (rec["id"], status[rec["id"]]))
            rec["confirmed"] = {"date": iso(a["date"]), "note": a["note"]}
            add_conflicts(records, rec)
        elif action == "resolve":
            other = records.get(a["other_id"])
            if other is None:
                raise r.fail(p + ".other_id", "%r is not in the decision log" % a["other_id"])
            if kind != "decision":
                raise r.fail(p + ".decision_id", "%s is an unconfirmed proposal; only a decision can supersede or "
                             "stand beside another" % rec["id"])
            pair = [c for c in rec["conflicts"] if c["with"] == other["id"]]
            if a["resolution"] == "both_stand":
                if not pair:
                    raise r.fail(p + ".other_id", "%s has no flagged conflict with %s (the conflict is listed on "
                                 "the newer entry)" % (rec["id"], other["id"]))
                pair[0].update({"resolution": "both_stand", "date": iso(a["date"])})
            else:
                if status[other["id"]] not in ("active", "open"):
                    raise r.fail(p + ".other_id", "%s is %s; only a live decision or open proposal can be superseded"
                                 % (other["id"], status[other["id"]]))
                if eff_date(other) > eff_date(rec):
                    raise r.fail(p + ".other_id", "%s (%s) is newer than %s (%s); the newer decision supersedes the "
                                 "older" % (other["id"], eff_date(other), rec["id"], eff_date(rec)))
                if reaches(records, other["id"], rec["id"]):
                    raise r.fail(p + ".other_id", "%s already supersedes %s" % (other["id"], rec["id"]))
                if other["id"] not in rec["supersedes"]:
                    rec["supersedes"].append(other["id"])
                if pair:
                    pair[0].update({"resolution": "supersedes", "date": iso(a["date"])})
        rec["history"].append(entry)
        stats["actions_applied"] += 1


def reaches_live(records, start, goal):
    """Whether start supersedes goal directly or through a chain of entries that are not dismissed."""
    seen, stack = set(), [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(t for t in records[node]["supersedes"] if t == goal or records[t]["dismissed"] is None)
    return False


def recheck_conflicts(records):
    """After a dismiss or restore: flag every pair of live decisions on one topic that nothing links, so two
    contradictory live decisions never stand side by side without a flag."""
    status, _ = statuses(records)
    by_topic = {}
    for rid in sorted(records):
        if status[rid] == "active":
            by_topic.setdefault(records[rid]["topic"], []).append(rid)
    for ids in by_topic.values():
        if len(ids) > 1:
            for rid in ids:
                add_conflicts(records, records[rid], status)


def add_conflicts(records, rec, status=None):
    """Flag every live decision on the same topic that this one neither repeats nor supersedes (directly or through
    entries that aren't dismissed). The flag is kept on the newer of the two (by decision date; this one on a tie),
    which is the one a reply resolves."""
    if status is None:
        status, _ = statuses(records)
    for rid in sorted(records):
        other = records[rid]
        if rid == rec["id"] or status[rid] != "active" or other["topic"] != rec["topic"]:
            continue
        if reaches_live(records, rec["id"], rid) or reaches_live(records, rid, rec["id"]):
            continue
        if any(c["with"] == rid for c in rec["conflicts"]) or any(c["with"] == rec["id"] for c in other["conflicts"]):
            continue
        holder, older = (other, rec) if eff_date(other) > eff_date(rec) else (rec, other)
        holder["conflicts"].append({"with": older["id"], "resolution": None, "date": None})


def merge(bundle, log, as_of, config, r, stats):
    records = log["records"]
    meetings = bundle["meetings"]
    refs = {}
    # Oldest first: by the meeting's local date (the decision date shown everywhere), then its start, then bundle order.
    ordered = sorted(bundle["decisions"], key=lambda d: (meetings[d["meeting_id"]]["day"],
                                                         meetings[d["meeting_id"]]["start"].astimezone(timezone.utc), d["index"]))
    claimed = set(make_id(d["meeting_id"], d["quote"], d["topic"]) for d in ordered) & set(records)
    quote_uses = {}
    for d in ordered:
        key = (d["meeting_id"], norm_text(d["quote"]))
        quote_uses[key] = quote_uses.get(key, 0) + 1
    for d in ordered:
        p = d["path"]
        meeting = meetings[d["meeting_id"]]
        cid = make_id(d["meeting_id"], d["quote"], d["topic"])
        logged = find_logged(records, d, cid, claimed, quote_uses[(d["meeting_id"], norm_text(d["quote"]))])
        if logged is not None:
            claimed.add(logged)
            refs[d["ref"]] = logged
            stats["already"] += 1
            if records[logged]["kind"] != d["kind"]:
                stats["kind_kept"] += 1
            if differs(records[logged], d, refs, records):
                stats["links_ignored"] += 1
            continue
        if d["same_as"] is not None:
            target = resolve_link(d["same_as"], refs, records, r, p + ".same_as")
            trec = records[target]
            if eff_kind(trec) != d["kind"]:
                raise r.fail(p + ".same_as", "%s is a %s, this is a %s; a decision that settles a proposal uses "
                             "supersedes" % (target, eff_kind(trec), d["kind"]))
            if trec["dismissed"] is not None:
                raise r.fail(p + ".same_as", "%s was dismissed; restore it first or log this as a new entry" % target)
            add_mention(trec, d, meeting, config)
            refs[d["ref"]] = target
            stats["repeats"] += 1
            continue
        status, _ = statuses(records)
        ns = norm_statement(d["statement"])
        twins = [rid for rid in sorted(records) if eff_kind(records[rid]) == d["kind"]
                 and records[rid]["topic"] == d["topic"] and status[rid] in ("active", "open")
                 and norm_statement(records[rid]["statement"]) == ns]
        if twins:
            twin = max(twins, key=lambda rid: (records[rid]["origin"]["date"], rid))
            add_mention(records[twin], d, meeting, config)
            refs[d["ref"]] = twin
            stats["repeats"] += 1
            continue
        rec = {"id": cid, "kind": d["kind"], "statement": d["statement"], "topic": d["topic"], "area": d["area"],
               "decided_by": d["decided_by"], "owner": d["owner"], "owner_set": None, "rationale": d["rationale"],
               "alternatives": d["alternatives"], "revisit_on": iso(d["revisit_on"]), "revisit_set": None,
               "origin": {"meeting_id": meeting["meeting_id"], "title": meeting["title"], "date": iso(meeting["day"]),
                          "start": meeting["start"].isoformat(), "url": meeting["url"], "quote": d["quote"],
                          "quote_source": d["quote_source"], "quote_checked": d["quote_checked"]},
               "mentions": [], "mention_count": 0, "supersedes": [], "conflicts": [], "dismissed": None,
               "confirmed": None, "logged_at": as_of.isoformat(), "reported_at": None, "history": []}
        for j, value in enumerate(d["supersedes"]):
            target = resolve_link(value, refs, records, r, "%s.supersedes[%d]" % (p, j))
            if target in rec["supersedes"]:
                continue
            if status[target] not in ("active", "open"):
                raise r.fail("%s.supersedes[%d]" % (p, j), "%s is %s; only a live decision or open proposal can be "
                             "superseded (supersede the newer one instead)" % (target, status[target]))
            if eff_date(records[target]) > meeting["day"]:
                raise r.fail("%s.supersedes[%d]" % (p, j), "%s is dated after this decision" % target)
            rec["supersedes"].append(target)
        records[cid] = rec
        claimed.add(cid)
        if d["kind"] == "decision":
            add_conflicts(records, rec)
        refs[d["ref"]] = cid
        stats["new"].append(cid)


# --------------------------------------------------------------------------- rendering helpers


def short_day(d, year=False):
    text = "%s %s %d" % (WEEKDAYS[d.weekday()], MONTHS[d.month - 1], d.day)
    return text + (", %d" % d.year if year else "")


def day_text(value, today):
    d = to_day(value) if isinstance(value, str) else value
    return short_day(d, d.year != today.year)


def window_text(start, end):
    if start == end:
        return short_day(start, True)
    if start.year == end.year:
        return "%s to %s" % (short_day(start), short_day(end, True))
    return "%s to %s" % (short_day(start, True), short_day(end, True))


def plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


def clip_quote(quote, limit):
    quote = clean(quote)
    if len(quote) <= limit:
        return quote
    cut = quote[:limit].rsplit(" ", 1)[0] if " " in quote[:limit] else quote[:limit]
    return cut + " …"


def provider_label(provider):
    return PROVIDER_LABELS.get(provider, provider)


def stamp(as_of):
    offset = as_of.strftime("%z")
    return "%s %s UTC%s:%s" % (short_day(as_of.date(), True), as_of.strftime("%H:%M"), offset[:3], offset[3:])


def cite(f, rec, ctx, verb=None):
    """`dl-…` “statement” (decided Tue Oct 6 in [Meeting](url))"""
    o = rec["origin"]
    verb = verb or ("decided" if rec["kind"] == "decision" else "proposed")
    return "%s “%s” (%s %s in %s)" % (f.code(rec["id"]), f.text(rec["statement"]), verb, day_text(o["date"], ctx["today"]),
                                      f.link(o["title"], o["url"]))


def item_lines(f, n, rec, ctx, marker=None, show_topic=False):
    records, status, sup_by, today = ctx["records"], ctx["status"], ctx["sup_by"], ctx["today"]
    st = status[rec["id"]]
    kind = eff_kind(rec)
    head = "%d. %s · %s · %s" % (n, f.bold(f.text(rec["statement"])), f.text(rec["area"]), f.code(rec["id"]))
    tags = []
    if marker:
        tags.append(marker)
    if st == "superseded":
        tags.append("superseded")
    elif st == "decided":
        tags.append("closed by a decision")
    elif st == "dismissed":
        tags.append("dismissed")
    if tags:
        head += " · " + ", ".join(tags)
    out = [head]
    o = rec["origin"]
    verb = "Decided" if rec["kind"] == "decision" else "Proposed"
    line = "    - %s %s in %s" % (verb, day_text(o["date"], today), f.link(o["title"], o["url"]))
    if rec["decided_by"]:
        line += " by %s" % ", ".join(f.text(x) for x in rec["decided_by"])
    if kind == "decision":
        if rec["owner"]:
            line += " · owner: %s" % f.text(rec["owner"])
            if rec["owner_set"]:
                line += " (set by you %s)" % day_text(rec["owner_set"], today)
        else:
            line += " · owner: none named ⚠️"
    out.append(line)
    if rec["confirmed"] is not None:
        c = rec["confirmed"]
        out.append("    - Confirmed as decided by you on %s%s." % (day_text(c["date"], today),
                                                                 ": " + f.text(c["note"]) if c["note"] else ""))
    out.append("    - “%s” — %s" % (f.text(clip_quote(o["quote"], ctx["config"]["max_quote_chars"])),
                                 QUOTE_LABELS[o["quote_source"]]))
    if rec["rationale"]:
        out.append("    - Why: %s" % f.text(rec["rationale"]))
    elif kind == "decision":
        out.append("    - Why: no reason recorded in the meeting.")
    if rec["alternatives"]:
        out.append("    - Rejected: %s" % "; ".join(f.text(x) for x in rec["alternatives"]))
    if rec["revisit_on"]:
        due = kind == "decision" and st == "active" and to_day(rec["revisit_on"]) <= today
        out.append("    - Revisit: %s%s%s" % (day_text(rec["revisit_on"], today),
                                           " (set by you %s)" % day_text(rec["revisit_set"], today) if rec["revisit_set"] else "",
                                           " ⚠️ date passed" if due else ""))
    if rec["mentions"]:
        shown = ["%s in %s" % (day_text(m["date"], today), f.link(m["title"], m["url"])) for m in rec["mentions"][-3:]]
        more = rec["mention_count"] - len(shown)
        out.append("    - Repeated: %s%s" % ("; ".join(shown), " (and %d earlier)" % more if more > 0 else ""))
    for target in rec["supersedes"]:
        t = records[target]
        if t["dismissed"] is not None:
            continue
        if eff_kind(t) == "decision":
            out.append("    - ⚠️ Reverses %s" % cite(f, t, ctx))
        else:
            out.append("    - Settles proposal %s" % cite(f, t, ctx))
    for newer in sup_by.get(rec["id"], []):
        n_rec = records[newer]
        out.append("    - %s %s" % ("Superseded by" if kind == "decision" else "Decided in", cite(f, n_rec, ctx)))
    for c in rec["conflicts"]:
        if c["resolution"] is None and st == "active" and status.get(c["with"]) == "active":
            out.append("    - ⚠️ Possible conflict with %s · same topic %s" % (cite(f, records[c["with"]], ctx),
                                                                            f.code(rec["topic"])))
    if kind == "proposal" and st == "open":
        for rid in sorted(records, key=lambda x: (eff_date(records[x]), x)):
            other = records[rid]
            if rid != rec["id"] and status[rid] == "active" and other["topic"] == rec["topic"]:
                if eff_date(other) >= eff_date(rec):
                    out.append("    - A later decision has the same topic: %s. If it settles this proposal, log it with "
                               "supersedes." % cite(f, other, ctx))
                else:
                    out.append("    - Would change %s" % cite(f, other, ctx))
    if show_topic:
        out.append("    - Topic: %s" % f.code(rec["topic"]))
    if rec["dismissed"] is not None:
        d = rec["dismissed"]
        out.append("    - Dismissed by you on %s%s." % (day_text(d["date"], today), ": " + f.text(d["note"]) if d["note"] else ""))
    return out


def flag_sets(ctx, shown_ids):
    records, status = ctx["records"], ctx["status"]
    today = ctx["today"]
    live = [records[rid] for rid in sorted(records) if status[rid] == "active"]
    reversals = [(rid, t) for rid in shown_ids for t in records[rid]["supersedes"]
                 if eff_kind(records[t]) == "decision" and records[rid]["dismissed"] is None and records[t]["dismissed"] is None]
    return {
        "reversals": reversals,
        "conflicts": open_conflicts(records, status),
        "no_owner": [r["id"] for r in sorted(live, key=lambda r: (eff_date(r), r["id"])) if not r["owner"]],
        "revisit": [r["id"] for r in sorted(live, key=lambda r: (r["revisit_on"] or "", r["id"]))
                    if r["revisit_on"] and to_day(r["revisit_on"]) <= today],
        "proposals": [records[rid]["id"] for rid in sorted(records, key=lambda x: (records[x]["origin"]["date"], x))
                      if status[rid] == "open"],
    }


def flag_lines(f, ctx, flags):
    records, today = ctx["records"], ctx["today"]
    out = []

    def group(title):
        if out:
            out.append("")
        out.append(f.bold(title))
    if flags["reversals"]:
        group("Reversals (%d)" % len(flags["reversals"]))
        for newer, older in flags["reversals"]:
            out.append("- %s reverses %s" % (cite(f, records[newer], ctx), cite(f, records[older], ctx)))
    if flags["conflicts"]:
        group("Possible conflicts (%d): same topic, not linked" % len(flags["conflicts"]))
        for newer, older in flags["conflicts"]:
            out.append("- %s vs %s · topic %s. Reply “%s supersedes %s” or “both stand”." % (
                cite(f, records[newer], ctx), cite(f, records[older], ctx), f.code(records[newer]["topic"]),
                newer, older))
    if flags["no_owner"]:
        group("No owner (%d)" % len(flags["no_owner"]))
        for rid in flags["no_owner"]:
            out.append("- %s. Reply “owner of %s is …”." % (cite(f, records[rid], ctx), rid))
    if flags["revisit"]:
        group("Revisit date passed (%d)" % len(flags["revisit"]))
        for rid in flags["revisit"]:
            out.append("- %s · revisit was due %s." % (cite(f, records[rid], ctx), day_text(records[rid]["revisit_on"], today)))
    if not out:
        out.append("None.")
    return out


def counts_line(f, flags, new_count, one, many):
    bits = [plural(new_count, one, many)]
    if flags["reversals"]:
        bits.append(plural(len(flags["reversals"]), "reversal"))
    if flags["conflicts"]:
        bits.append(plural(len(flags["conflicts"]), "possible conflict"))
    if flags["no_owner"]:
        bits.append("%d without an owner" % len(flags["no_owner"]))
    if flags["revisit"]:
        bits.append(plural(len(flags["revisit"]), "revisit date passed", "revisit dates passed"))
    bits.append(plural(len(flags["proposals"]), "open proposal"))
    return f.bold(" · ".join(bits))


def log_totals(records, status):
    decisions = [rid for rid in records if eff_kind(records[rid]) == "decision" and status[rid] != "dismissed"]
    active = sum(1 for rid in decisions if status[rid] == "active")
    proposals = sum(1 for rid in records if status[rid] == "open")
    dismissed = sum(1 for rid in records if status[rid] == "dismissed")
    return "%s (%d active, %d superseded) · %s · %d dismissed" % (
        plural(len(decisions), "decision"), active, len(decisions) - active, plural(proposals, "open proposal"), dismissed)


# --------------------------------------------------------------------------- report


def done_word(run):
    return "already posted" if run["status"] == "posted" else "already recorded with nothing to report:"


def history_limit(src, config):
    if src["history_limit_days"] is not None:
        return src["history_limit_days"]
    return config["plan_history_days"].get(src["plan"].lower()) if src["plan"] else None


def build(args, log):
    as_of = parse_as_of(args.as_of)
    today = as_of.date()
    config = load_config(args.config)
    bundle = load_bundle(args.bundle, as_of)
    scan, src = bundle["scan"], bundle["source"]
    f = FORMATS[args.format]
    period_key = "weekly:%s..%s" % (scan["from"], scan["to"]) if scan["cadence"] == "weekly" else None
    unapplied = ""
    if bundle["actions"]:
        unapplied = " %s in this bundle (user_actions) %s not applied; apply %s with an on-request run." % (
            plural(len(bundle["actions"]), "reply", "replies"), "was" if len(bundle["actions"]) == 1 else "were",
            "it" if len(bundle["actions"]) == 1 else "them")
    gap = None
    if args.scheduled:
        if scan["cadence"] != "weekly":
            raise InputError("bundle %s: $.scan.cadence: must be weekly for a --scheduled run" % args.bundle)
        if scan["to"] != today:
            raise InputError("bundle %s: $.scan.to: a scheduled window ends on the --as-of date (%s); take from and to "
                             "from --next-window" % (args.bundle, today))
        for run in log["runs"]:
            if run["period_key"] != period_key:
                continue
            if run["status"] in DONE_RUN_STATUSES:
                return {"status": "skipped", "period_key": period_key, "reason": "%s %s (as of %s); not "
                        "posting it twice.%s" % (done_word(run), period_key, run["as_of"], unapplied)}, None
            if run["status"] == "superseded":
                raise InputError("%s was superseded by a later report that repeated its entries, so it is not staged "
                                 "again; run the current window from --next-window instead" % period_key)
            if run["status"] == "pending" and not args.resume_pending:
                raise StopRun(EXIT_PENDING, "pending: a report for %s was produced at %s but not confirmed posted. If "
                              "it was posted, run --confirm-posted %s. If posting failed (or you are not sure, check "
                              "the destination first), rerun with --resume-pending to print it again, post it, then "
                              "confirm." % (period_key, run["as_of"], period_key))
        rivals = [r["period_key"] for r in log["runs"] if r["status"] == "pending" and r["period_key"] != period_key
                  and week_of(period_bounds(r["period_key"])[1]) == week_of(scan["to"])]
        if rivals:
            raise StopRun(EXIT_PENDING, "pending: %s is still pending for the same week, so %s is not printed. If it "
                          "was posted, run --confirm-posted %s. If posting failed (check the destination first), rerun "
                          "that period with --resume-pending, post it, then confirm." % (rivals[-1], period_key,
                                                                                         rivals[-1]))
        posted = posted_in_week(log, scan["to"])
        if posted is not None:
            return {"status": "skipped", "period_key": period_key, "reason": "%s %s (as of %s), the weekly "
                    "report for the week of %s; not posting %s as well.%s" % (
                        done_word(posted), posted["period_key"], posted["as_of"], short_day(week_of(scan["to"])),
                        period_key, unapplied)}, None
        expected = to_day(next_window(log, as_of)["from"])
        if scan["from"] > expected:
            gap = (expected, scan["from"] - timedelta(days=1))

    incomplete = []
    if src["status"] != "ok":
        incomplete.append(("meeting_source", "the meeting list could not be read (%s %s%s), so this window's decisions "
                           "are unknown." % (f.text(provider_label(src["provider"])), src["status"].replace("_", " "),
                                            ": " + f.text(src["detail"]) if src["detail"] else "")))
    limit = history_limit(src, config)
    if limit is not None and scan["from"] < today - timedelta(days=limit):
        earliest = today - timedelta(days=limit)
        incomplete.append(("history_limit", "this window starts %s, but the %s%d-day note history only reaches back to "
                           "%s. Start the window on %s; the days before it are a gap." % (
                               short_day(scan["from"], True), ("%s plan's " % f.text(src["plan"])) if src["plan"] else "",
                               limit, short_day(earliest, True), short_day(earliest, True))))
    unread = [m for m in bundle["meetings"].values() if m["notes_status"] != "ok" and m["transcript_status"] != "ok"]
    if bundle["meetings"] and len(unread) == len(bundle["meetings"]):
        incomplete.append(("no_meeting_readable", "none of the %s in this window could be read (notes and transcripts "
                           "denied or unavailable)." % plural(len(unread), "meeting")))

    r = Reader("bundle %s" % args.bundle)
    stats = {"new": [], "already": 0, "repeats": 0, "kind_kept": 0, "links_ignored": 0, "actions_applied": 0,
             "actions_repeat": 0}
    if not incomplete:
        apply_actions(bundle, log, r, stats)
        merge(bundle, log, as_of, config, r, stats)
    records = log["records"]
    status, sup_by = statuses(records)
    ctx = {"records": records, "status": status, "sup_by": sup_by, "today": today, "config": config, "fmt": f}

    if args.scheduled:
        shown = [rid for rid in sorted(records, key=lambda x: (eff_date(records[x]), records[x]["origin"]["start"], x))
                 if records[rid]["reported_at"] is None and status[rid] != "dismissed"] if not incomplete else []
    else:
        shown = [rid for rid in sorted(records, key=lambda x: (eff_date(records[x]), records[x]["origin"]["start"], x))
                 if status[rid] != "dismissed" and scan["from"] <= eff_date(records[rid]) <= scan["to"]]
    shown_decisions = [rid for rid in shown if eff_kind(records[rid]) == "decision"]
    shown_proposals = set(rid for rid in shown if eff_kind(records[rid]) == "proposal")
    flags = flag_sets(ctx, shown_decisions)
    nothing = (not shown and not flags["conflicts"] and not flags["no_owner"] and not flags["revisit"] and gap is None
               and not unread)

    lines = render_report(f, ctx, bundle, args, as_of, shown_decisions, shown_proposals, flags, stats, unread,
                          incomplete, gap, period_key, log)
    run_entry = None
    notice = notice_in_week(log, scan["to"]) if args.scheduled and incomplete else None
    if notice is not None:
        result = {"status": "incomplete", "notice_already_posted": {"period_key": notice["period_key"],
                                                                    "as_of": notice["as_of"]},
                  "markdown": "Skipped: the Incomplete notice for the week of %s was already posted (%s, as of %s); "
                  "not posting it again. Coverage is still incomplete: %s Nothing from this window was added to the "
                  "log; the next run's window covers it again." % (
                      short_day(week_of(scan["to"])), notice["period_key"], notice["as_of"],
                      " Also, ".join(text for _, text in incomplete))}
        run_entry = {"status": "incomplete", "staged": None}
    elif incomplete:
        result = {"status": "incomplete", "markdown": "\n".join(lines)}
        if args.scheduled:
            run_entry = {"status": "incomplete", "staged": None}
    elif args.scheduled and nothing:
        result = {"status": "skipped", "reason": "nothing to report for %s (no new decisions or proposals, no "
                  "conflicts, ownerless decisions or passed revisit dates; every meeting read)." % period_key}
        run_entry = {"status": "empty", "staged": None}
    else:
        result = {"status": "reported", "markdown": "\n".join(lines)}
        if args.scheduled and not args.dry_run:
            result["confirm_with"] = "--confirm-posted %s" % period_key
            run_entry = {"status": "pending", "staged": sorted(shown)}
    result["period_key"] = period_key
    if run_entry is not None:
        if run_entry["status"] != "incomplete":
            for run in log["runs"]:
                if run["status"] == "pending":
                    run["status"] = "superseded"
        log["runs"] = [x for x in log["runs"]
                       if not (x["period_key"] == period_key and x["status"] in ("superseded", "incomplete")
                               and not x.get("notice_posted"))]
        run_entry.update({"period_key": period_key, "as_of": as_of.isoformat(), "confirmed_at": None,
                          "counts": {"decisions": len(shown_decisions), "proposals": len(shown_proposals)}})
        log["runs"].append(run_entry)
        log["runs"] = log["runs"][-config["max_runs_logged"]:]
    log["updated_at"] = as_of.isoformat()
    result.update({
        "decisions": [summary(records[rid], status) for rid in shown_decisions],
        "proposals": [summary(records[rid], status) for rid in flags["proposals"]],
        "flags": {"reversals": [{"id": a, "reverses": b} for a, b in flags["reversals"]],
                  "conflicts": [{"id": a, "with": b} for a, b in flags["conflicts"]],
                  "no_owner": flags["no_owner"], "revisit_passed": flags["revisit"]},
        "coverage": {"meetings": len(bundle["meetings"]), "meetings_not_read": len(unread),
                     "meeting_source": src["status"], "new_to_log": len(stats["new"]), "already_logged": stats["already"],
                     "repeats_linked": stats["repeats"], "replies_applied": stats["actions_applied"],
                     "links_dropped": len(bundle["url_warnings"]), "incomplete": [code for code, _ in incomplete]}})
    return result, log


def render_report(f, ctx, bundle, args, as_of, shown_decisions, shown_proposals, flags, stats, unread, incomplete, gap,
                  period_key, log):
    records = ctx["records"]
    scan = bundle["scan"]
    lines = [f.heading(2, "Decision log — %s" % window_text(scan["from"], scan["to"]))]
    meta = "As of %s · %s" % (stamp(as_of), "scheduled weekly run" if args.scheduled else "on-request run")
    lines.append(meta)
    if incomplete:
        warning = "Incomplete: " + " Also, ".join(text for _, text in incomplete)
        warning += " Nothing from this window was added to the log%s." % (
            " and the week was not recorded; the next run's window covers it again" if args.scheduled else "")
        lines.append(f.bold(warning))
    lines.append("")
    if args.scheduled:
        lines.append(counts_line(f, flags, len(shown_decisions), "new decision", "new decisions"))
    else:
        lines.append(counts_line(f, flags, len(shown_decisions), "decision in this window", "decisions in this window"))
    lines.append("")
    if args.scheduled:
        lines.append(f.heading(3, "New decisions (%d)" % len(shown_decisions)))
    else:
        lines.append(f.heading(3, "Decisions in this window (%d)" % len(shown_decisions)))
    if incomplete:
        lines.append("Not listed: this window could not be fully read (see Incomplete above).")
    elif not shown_decisions:
        lines.append("None." if args.scheduled else "No decision in the log is dated in this window.")
    new_ids = set(stats["new"])
    for n, rid in enumerate(shown_decisions, 1):
        marker = "new to the log" if (not args.scheduled and rid in new_ids) else None
        lines.extend(item_lines(f, n, records[rid], ctx, marker))
    lines.append("")
    lines.append(f.heading(3, "Needs attention"))
    lines.extend(flag_lines(f, ctx, flags))
    lines.append("")
    lines.append(f.heading(3, "Open proposals (%d): discussed, not decided" % len(flags["proposals"])))
    if not flags["proposals"]:
        lines.append("None.")
    for n, rid in enumerate(flags["proposals"], 1):
        lines.extend(item_lines(f, n, records[rid], ctx, "new" if rid in shown_proposals else None))
    if flags["proposals"]:
        lines.append("")
        lines.append("A proposal stays here until a later meeting decides it (logged with supersedes) or you reply "
                     "that it was decided; it is never promoted on its own.")
    lines.append("")
    lines.extend(coverage_lines(f, ctx, bundle, args, stats, unread, gap, log, incomplete))
    if args.scheduled and period_key and not incomplete:
        lines.append("")
        lines.append("Period key: %s · after posting, confirm it." % f.code(period_key))
    elif args.scheduled and period_key:
        lines.append("")
        lines.append("Notice key: %s · after posting this notice, confirm it; the week stays open." % f.code(period_key))
    return lines


def coverage_lines(f, ctx, bundle, args, stats, unread, gap, log, incomplete):
    records, status, today = ctx["records"], ctx["status"], ctx["today"]
    scan, src = bundle["scan"], bundle["source"]
    meetings = [bundle["meetings"][mid] for mid in bundle["order"]]
    out = [f.heading(3, "Coverage")]
    scope = "all meetings this account can read"
    if scan["folder"]:
        scope = "folder “%s”" % f.text(scan["folder"])
    if scan["workspace_only"]:
        scope += " · Team Space (workspace-visible) notes only"
    line = "- Window: %s (%s) · Source: %s" % (window_text(scan["from"], scan["to"]), scan["cadence"].replace("_", "-"),
                                               f.text(provider_label(src["provider"])))
    if src["status"] != "ok":
        line += " — %s%s" % (f.bold(src["status"].replace("_", " ")), ": " + f.text(src["detail"]) if src["detail"] else "")
    out.append(line + " · Scope: " + scope)
    if gap is not None:
        out.append("- ⚠️ Gap: %s were not scanned by any posted report." % (
            short_day(gap[0], True) if gap[0] == gap[1] else "%s to %s" % (short_day(gap[0]), short_day(gap[1], True))))
    limit = history_limit(src, ctx["config"])
    if limit is not None:
        out.append("- Note history: %d days%s; meetings before %s can't be read." % (
            limit, " (%s plan)" % f.text(src["plan"]) if src["plan"] else "", short_day(today - timedelta(days=limit), True)))
    notes_ok = sum(1 for m in meetings if m["notes_status"] == "ok")
    tr = dict((k, [m for m in meetings if m["transcript_status"] == k]) for k in TRANSCRIPT_STATUSES)
    line = "- Meetings: %s · notes read %d of %d · transcripts read %d" % (plural(len(meetings), "meeting"), notes_ok,
                                                                           len(meetings), len(tr["ok"]))
    if tr["denied"]:
        line += ", denied %d (%s)" % (len(tr["denied"]), "; ".join(
            "%s, %s" % (f.text(m["title"]), short_day(m["day"])) for m in tr["denied"]))
    if tr["unavailable"]:
        line += ", unavailable %d" % len(tr["unavailable"])
    if tr["not_requested"]:
        line += ", not requested %d" % len(tr["not_requested"])
    out.append(line + ".")
    seen = {}
    for m in meetings:
        key = (norm_text(m["title"]), m["start"].astimezone(timezone.utc))
        seen[key] = seen.get(key, 0) + 1
    dupes = sum(n - 1 for n in seen.values())
    if dupes:
        out.append("- Duplicate notes: %s with the same title and start as another (two people captured one meeting); "
                   "a decision extracted from both is one decision (same_as, or the same topic and wording)." % plural(dupes, "note"))
    if unread:
        out.append("- ⚠️ Not readable (notes and transcript): %s." % "; ".join(
            "%s, %s" % (f.link(m["title"], m["url"]), short_day(m["day"])) for m in sorted(unread, key=lambda m: m["index"])))
    decisions = [d for d in bundle["decisions"] if d["kind"] == "decision"]
    checked = sum(1 for d in bundle["decisions"] if d["quote_checked"])
    if not incomplete:
        out.append("- Extracted: %s and %s · %d new to the log · %s linked · %d already logged · quotes checked against "
                   "meeting text in the bundle: %d of %d." % (
                       plural(len(decisions), "decision"), plural(len(bundle["decisions"]) - len(decisions), "proposal"),
                       len(stats["new"]), plural(stats["repeats"], "repeat"), stats["already"], checked,
                       len(bundle["decisions"])))
    if stats["kind_kept"]:
        out.append("- %s re-extracted with a different kind (decision vs proposal) kept as logged; a proposal becomes a "
                   "decision only with a confirm reply or a later decision that supersedes it." % plural(stats["kind_kept"], "entry", "entries"))
    if stats["links_ignored"]:
        out.append("- %s re-extracted with same_as or supersedes kept as logged; link logged entries with a resolve "
                   "reply." % plural(stats["links_ignored"], "entry", "entries"))
    if bundle["actions"] and incomplete:
        out.append("- Replies: %d not applied (nothing changes while coverage is incomplete); they apply on the next "
                   "complete run." % len(bundle["actions"]))
    elif bundle["actions"]:
        out.append("- Replies: %d applied%s." % (stats["actions_applied"], ", %d already applied earlier" % stats["actions_repeat"]
                                                 if stats["actions_repeat"] else ""))
    if bundle["url_warnings"]:
        out.append("- Links dropped (not http(s) with a real host name): %d." % len(bundle["url_warnings"]))
    this_key = "weekly:%s..%s" % (scan["from"], scan["to"])
    pending = [x["period_key"] for x in log["runs"] if x["status"] == "superseded"
               or (x["status"] == "pending" and x["period_key"] != this_key)]
    if args.scheduled and pending:
        out.append("- Earlier reports never confirmed as posted: %s. Their entries are included above." % ", ".join(
            f.code(k) for k in pending[-3:]))
    out.append("- Log: %s." % log_totals(records, status))
    return out


def summary(rec, status):
    o = rec["origin"]
    return {"id": rec["id"], "kind": eff_kind(rec), "status": status[rec["id"]], "statement": rec["statement"],
            "topic": rec["topic"], "area": rec["area"], "date": iso(eff_date(rec)), "owner": rec["owner"],
            "decided_by": rec["decided_by"], "meeting_title": o["title"], "meeting_url": o["url"],
            "meeting_date": o["date"], "quote": o["quote"], "quote_source": o["quote_source"],
            "supersedes": rec["supersedes"], "revisit_on": rec["revisit_on"]}


# --------------------------------------------------------------------------- log, topics, search


def render_log(log, as_of, config, f):
    records = log["records"]
    today = as_of.date()
    status, sup_by = statuses(records)
    ctx = {"records": records, "status": status, "sup_by": sup_by, "today": today, "config": config, "fmt": f}
    flags = flag_sets(ctx, [])
    lines = [f.heading(1, "Decision log")]
    updated = instant(log["updated_at"]) if log["updated_at"] else None
    lines.append("As of %s · %s%s" % (stamp(as_of), log_totals(records, status),
                                       " · last updated %s" % stamp(updated) if updated else ""))
    attention = []
    if flags["conflicts"]:
        attention.append(plural(len(flags["conflicts"]), "possible conflict"))
    if flags["no_owner"]:
        attention.append("%d without an owner" % len(flags["no_owner"]))
    if flags["revisit"]:
        attention.append(plural(len(flags["revisit"]), "revisit date passed", "revisit dates passed"))
    if attention:
        lines.append(f.bold("Needs attention: " + " · ".join(attention)))
    order = lambda rid: (records[rid]["area"], eff_date(records[rid]), records[rid]["origin"]["start"], rid)
    active = sorted([rid for rid in records if status[rid] == "active"], key=order)
    lines.append("")
    lines.append(f.heading(2, "Active decisions (%d)" % len(active)))
    if not active:
        lines.append("None yet.")
    area, n = None, 0
    for rid in active:
        if records[rid]["area"] != area:
            area = records[rid]["area"]
            n = 0
            lines.append("")
            lines.append(f.heading(3, f.text(area)))
        n += 1
        lines.extend(item_lines(f, n, records[rid], ctx, show_topic=True))
    proposals = sorted([rid for rid in records if status[rid] == "open"], key=order)
    lines.append("")
    lines.append(f.heading(2, "Open proposals (%d): discussed, not decided" % len(proposals)))
    if not proposals:
        lines.append("None.")
    for n, rid in enumerate(proposals, 1):
        lines.extend(item_lines(f, n, records[rid], ctx, show_topic=True))
    closed = sorted([rid for rid in records if status[rid] in ("superseded", "decided")],
                    key=lambda rid: (eff_date(records[rid]), rid))
    lines.append("")
    lines.append(f.heading(2, "Superseded and closed (%d)" % len(closed)))
    if not closed:
        lines.append("None.")
    for n, rid in enumerate(closed, 1):
        lines.extend(item_lines(f, n, records[rid], ctx, show_topic=True))
    dismissed = sorted([rid for rid in records if status[rid] == "dismissed"], key=lambda rid: (eff_date(records[rid]), rid))
    if dismissed:
        lines.append("")
        lines.append(f.heading(2, "Dismissed (%d)" % len(dismissed)))
        for rid in dismissed:
            d = records[rid]["dismissed"]
            lines.append("- %s “%s” · dismissed %s%s" % (f.code(rid), f.text(records[rid]["statement"]),
                                                        day_text(d["date"], today), ": " + f.text(d["note"]) if d["note"] else ""))
    lines.append("")
    lines.append("Generated from the decision log file. Edits made to this page are not read back; reply with "
                 "changes instead (dismiss, owner, revisit date, confirm a proposal, resolve a conflict).")
    return "\n".join(lines)


def topics(log):
    records = log["records"]
    status, _ = statuses(records)
    out = []
    for rid in sorted(records, key=lambda x: (records[x]["topic"], eff_date(records[x]), x)):
        if status[rid] in ("active", "open"):
            rec = records[rid]
            out.append({"id": rid, "kind": eff_kind(rec), "status": status[rid], "topic": rec["topic"],
                        "area": rec["area"], "statement": rec["statement"], "date": iso(eff_date(rec)),
                        "meeting_title": rec["origin"]["title"]})
    return out


def stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def search(log, query, config, f, as_of):
    q = clean(query)
    if not q or len(q) > LIMITS["search"]:
        raise InputError("--search: give 1 to %d characters" % LIMITS["search"])
    ids = set(ID_RE.findall(q.lower()))
    tokens = sorted(set(stem(w) for w in norm_text(q).split() if w not in SEARCH_STOPWORDS
                        and not ID_RE.fullmatch(w) and not re.fullmatch(r"dl|[0-9a-f]{10}", w)))
    if not tokens and not ids:
        raise InputError("--search: %r has no searchable words (question words like when, why and decided are "
                         "ignored); name the subject, e.g. \"annual discount\"" % clip_text(q))
    records = log["records"]
    status, sup_by = statuses(records)
    today = as_of.date()
    ctx = {"records": records, "status": status, "sup_by": sup_by, "today": today, "config": config, "fmt": f}
    need = max(1, (len(tokens) + 1) // 2) if tokens else 0
    hits = []
    for rid in sorted(records):
        rec = records[rid]
        if status[rid] == "dismissed" and rid not in ids:
            continue
        hay = " ".join([rec["statement"], rec["topic"].replace("/", " ").replace("-", " "), rec["area"].replace("-", " "),
                        rec["rationale"] or "", " ".join(rec["alternatives"]), rec["origin"]["quote"],
                        rec["origin"]["title"], " ".join(rec["decided_by"]), rec["owner"] or ""]
                       + [m["quote"] + " " + m["title"] for m in rec["mentions"]])
        words = set(stem(w) for w in norm_text(hay).split())
        score = sum(1 for t in tokens if t in words)
        if rid in ids:
            hits.append((1000, rid))
        elif tokens and score >= need:
            hits.append((score, rid))
    hits.sort(key=lambda h: (-h[0], {"active": 0, "open": 1}.get(status[h[1]], 2), -eff_date(records[h[1]]).toordinal(), h[1]))
    hits = hits[:config["search_max_results"]]
    lines = [f.heading(2, "Decision log search: “%s”" % f.text(q))]
    span = sorted(to_day(r["origin"]["date"]) for r in records.values())
    covers = ("Logged entries date from %s to %s." % (short_day(span[0], True), short_day(span[-1], True))) if span else "The log is empty."
    if not hits:
        lines.append("No logged decision or proposal matches. %s Not being in the log doesn't mean it was never "
                     "decided; only that no logged meeting recorded it." % covers)
        return "\n".join(lines), []
    lines.append("%s in a log of %s. %s Answer only from these entries, citing their meetings." % (
        plural(len(hits), "match", "matches"), log_totals(records, status), covers))
    lines.append("")
    for n, (_, rid) in enumerate(hits, 1):
        lines.extend(item_lines(f, n, records[rid], ctx, show_topic=True))
    return "\n".join(lines), [summary(records[rid], status) for _, rid in hits]


# --------------------------------------------------------------------------- confirm and main


def confirm_posted(log, period_key, confirmed_at):
    runs = [r for r in log["runs"] if r["period_key"] == period_key]
    if any(r["status"] in DONE_RUN_STATUSES for r in runs):
        return "Already confirmed: %s." % period_key
    pending = [r for r in runs if r["status"] == "pending"]
    if not pending:
        if any(r["status"] == "superseded" for r in runs):
            raise InputError("--confirm-posted: %s was superseded by a later report that repeated its entries; confirm "
                             "that one instead" % period_key)
        if runs:   # only incomplete runs: record that the Incomplete notice was posted, so it isn't posted again
            if any(r.get("notice_posted") for r in runs):
                return ("Already recorded: the Incomplete notice for %s was posted; the week is still open."
                        % period_key)
            runs[-1]["notice_posted"] = True
            runs[-1]["confirmed_at"] = confirmed_at
            return ("Recorded: the Incomplete notice for %s was posted. Nothing was marked reported: the week stays "
                    "open and the next run's window covers it again." % period_key)
        raise InputError("--confirm-posted: no pending report for %r in the log" % clip_text(period_key))
    run = pending[-1]
    for rid in run["staged"]:
        rec = log["records"].get(rid)
        if rec is not None and rec["reported_at"] is None:
            rec["reported_at"] = run["as_of"]
    run["status"] = "posted"
    run["confirmed_at"] = confirmed_at
    return "Confirmed posted: %s (%s marked reported)." % (period_key, plural(len(run["staged"]), "entry", "entries"))


def parse_as_of(value):
    if value is None:
        raise InputError("--as-of: is required (ISO datetime with UTC offset)")
    return parse_moment(value, lambda problem: InputError("--as-of: %s" % problem))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Keep a team decision log from meeting notes.")
    parser.add_argument("--log", type=Path, help="The decision log file (JSON), carried across runs. Required.")
    parser.add_argument("--bundle", type=Path, help="Normalised JSON bundle (see DATA_CONTRACT.md) for an update run.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--as-of", help="ISO datetime with UTC offset; the only 'now'.")
    parser.add_argument("--scheduled", action="store_true",
                        help="Weekly scheduled run: stage the report as pending until --confirm-posted.")
    parser.add_argument("--resume-pending", action="store_true",
                        help="With --scheduled: print a pending week's report again after a failed post.")
    parser.add_argument("--dry-run", action="store_true", help="Read the log but never write it.")
    parser.add_argument("--next-window", action="store_true", help="Print the next scheduled window (JSON). "
                        "Needs --log and --as-of.")
    parser.add_argument("--confirm-posted", metavar="PERIOD_KEY", help="Record that the pending report for this "
                        "period was posted. Needs --log.")
    parser.add_argument("--topics", action="store_true", help="Print live decisions and open proposals with their "
                        "topic keys (JSON). Needs --log.")
    parser.add_argument("--search", metavar="TEXT", help="Find logged decisions about TEXT. Needs --log.")
    parser.add_argument("--render-log", action="store_true", help="Print the whole log. Needs --log and --as-of.")
    parser.add_argument("--format", choices=sorted(FORMATS), default="markdown",
                        help="markdown (default; Type docs and Spaces) or slack (Slack mrkdwn).")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of markdown.")
    args = parser.parse_args(argv)
    utf8_stdio()

    lock = None
    try:
        commands = [name for name, on in [("--next-window", args.next_window), ("--confirm-posted", args.confirm_posted),
                                          ("--topics", args.topics), ("--search", args.search is not None),
                                          ("--render-log", args.render_log), ("--bundle", args.bundle is not None)] if on]
        if len(commands) > 1:
            raise InputError("%s cannot be combined" % " and ".join(commands))
        if not commands:
            raise InputError("nothing to do: give --bundle (update run), --search, --topics, --render-log, "
                             "--next-window or --confirm-posted")
        if args.log is None:
            raise InputError("%s: needs --log (the decision log file)" % commands[0])
        if commands[0] != "--bundle" and (args.scheduled or args.resume_pending or args.dry_run):
            raise InputError("--scheduled, --resume-pending and --dry-run only apply to a --bundle run")
        if args.resume_pending and not args.scheduled:
            raise InputError("--resume-pending: only applies to --scheduled runs")
        config = None
        if commands[0] in ("--search", "--render-log", "--bundle"):
            config = load_config(args.config)
        if commands[0] in ("--topics", "--search", "--render-log", "--next-window"):
            check_log_path(args.log, writing=False)
        if args.topics:
            if not args.log.exists():
                return emit("No decision log at %s yet: nothing is logged. The first update run creates it." % args.log)
            return emit(json.dumps(topics(load_log(args.log)), ensure_ascii=False, indent=2))
        if args.next_window:
            as_of = parse_as_of(args.as_of)
            return emit(json.dumps(next_window(load_log(args.log), as_of), indent=2))
        if args.search is not None:
            as_of = parse_as_of(args.as_of)
            if not args.log.exists():
                return emit("No decision log at %s yet: nothing is logged, so there is nothing to search." % args.log)
            text, hits = search(load_log(args.log), args.search, config, FORMATS[args.format], as_of)
            return emit(json.dumps({"query": args.search, "matches": hits, "markdown": text}, ensure_ascii=False,
                                   indent=2) if args.json else text)
        if args.render_log:
            as_of = parse_as_of(args.as_of)
            if not args.log.exists():
                return emit("No decision log at %s yet: nothing is logged. The first update run creates it." % args.log)
            return emit(render_log(load_log(args.log), as_of, config, FORMATS[args.format]))
        if args.confirm_posted:
            confirmed_at = parse_as_of(args.as_of).isoformat() if args.as_of else None
            check_log_path(args.log, writing=True)
            if not args.log.exists():
                raise InputError("--confirm-posted: log %s does not exist" % args.log)
            lock = LogLock(args.log)
            lock.acquire()
            log = load_log(args.log)
            message = confirm_posted(log, args.confirm_posted, confirmed_at)
            write_log(args.log, log)
            lock.release()
            return emit(message)

        parse_as_of(args.as_of)
        writing = not args.dry_run
        check_log_path(args.log, writing)
        if writing:
            lock = LogLock(args.log)
            lock.acquire()
        result, log = build(args, load_log(args.log))
        # Persist BEFORE printing: if the log can't be written, nothing has been shown or posted.
        if log is not None and writing:
            write_log(args.log, log)
    except InputError as exc:
        note("error: %s" % exc)
        return EXIT_INPUT
    except StopRun as exc:
        note("%s" % exc)
        return exc.code
    finally:
        if lock is not None:
            lock.release()

    if args.json:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    elif result["status"] == "skipped":
        text = "Skipped: %s" % result["reason"]
    else:
        text = result["markdown"]
    staged = result["status"] == "reported" and args.scheduled and not args.dry_run
    if staged:
        code = emit(text, pending=result["period_key"])
        if code:
            return code
        note("pending: post the report above, then run --confirm-posted %s" % result["period_key"])
        return 0
    code = emit(text)
    if code:
        return code
    if result["status"] == "incomplete" and args.scheduled and "notice_already_posted" not in result:
        note("incomplete: post the notice above once, then run --confirm-posted %s (the week stays open)"
             % result["period_key"])
    return EXIT_INCOMPLETE if result["status"] == "incomplete" else 0


def utf8_stdio():
    """Reports contain non-ASCII text (—, “ ”, ⚠️); write UTF-8 whatever the locale says (Python 3.7+)."""
    for stream, errors in ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors=errors)
            except (OSError, ValueError):
                pass


def note(message):
    """A line on stderr; a closed or broken stderr is ignored (never falls back to stdout)."""
    if sys.stderr is None:
        return
    try:
        print(message, file=sys.stderr)
    except (OSError, ValueError, AttributeError):
        pass


def emit(text, pending=None):
    """Write the result to stdout. Returns 0, or the exit code to use when stdout is closed or the pipe broke
    (nothing is retried): 4 if this run staged a pending report (rerun with --resume-pending), otherwise 2."""
    try:
        if sys.stdout is None:
            raise OSError("stdout is closed")
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        return 0
    except (OSError, ValueError) as exc:
        try:  # keep the interpreter's final flush from failing on the same broken pipe
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
        if pending:
            note("pending: the report for %s was staged but could not be written to stdout (%s). Nothing was posted. "
                 "Rerun with --resume-pending to print it again, post it, then run --confirm-posted %s."
                 % (pending, reason, pending))
            return EXIT_PENDING
        note("error: could not write the output to stdout (%s)" % reason)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
