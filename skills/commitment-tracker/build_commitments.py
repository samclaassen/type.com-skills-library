#!/usr/bin/env python3
"""Track meeting commitments across runs and render a commitment digest.

Reads one normalised JSON bundle (meetings, extracted commitments, completion
evidence, user actions) plus an optional state file carried across runs.
Stdlib only, offline, deterministic. --as-of is the only source of "now"; the
wall clock is read only to decide whether a leftover lock file is stale.

What it does, mechanically:
  validate     bundle, config and state shape, enums, strict dates (YYYY-MM-DD,
               datetimes with UTC offsets), verbatim quotes, quote sources that
               were actually read, evidence dated after the commitment, evidence
               only from sources that were read, http(s) links only
  ids          stable id = "ct-" + sha256(meeting_id | quote | owner | deliverable)[:10]
               (all normalised); ids from older state files are kept
  dedupe       same commitment repeated across meetings (same direction, owner,
               counterpart and normalised deliverable), explicit same_as links,
               and known_id links. A repeat merges only into an OPEN item; a new
               promise matching a done or dismissed item becomes a new item
  due dates    explicit dates as given; relative phrases ("by Friday", "next week",
               "tomorrow", "end of month", "in 3 business days") resolved against
               the meeting's local date and business days, marked inferred;
               vague or ambiguous phrases get no date
  status       done (strong evidence), likely done (weak evidence, with why),
               open, overdue, dismissed (user); "against" evidence or a user
               "reopen" reopens
  state        two-phase for scheduled runs: a digest is staged as `pending`,
               and --confirm-posted <period> commits it (new markers, done
               drop-off). On-request runs never change reporting state. One
               digest per cadence slot (Mon-Sun week, or day). A period is never
               staged when coverage was partial (meeting source not ok, window
               past the plan's note history, a meeting whose read failed, no
               meeting readable); a scheduled run dated before the last posted
               run is refused
  render       Overdue -> Due in the next N days -> Due later -> Open, no date ->
               They owe us -> Done since last run -> (Soft mentions) -> Coverage,
               as markdown or Slack mrkdwn; every interpolated value is escaped
               (bare URLs inside values stay text, but renderers may auto-link them)

Exit codes: 0 digest, "Skipped: ..." or a command result printed; 2 bad input,
config or state (the message names the file and the JSON path; nothing is
printed on stdout); 3 another run holds the state lock; 4 the period is already
pending confirmation (see --confirm-posted / --resume-pending), also when a staged
digest could not be written to stdout; 5 scheduled run with incomplete coverage:
an Incomplete digest is printed and the period is NOT staged. A stdout that can't
be written otherwise exits 2. Never edit this script to get past an error.
"""

from __future__ import annotations

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
DEFAULT_CONFIG = SCRIPT_DIR / "commitments.json"
SCHEMA_VERSION = 1
STATE_VERSION = 2
STATE_VERSIONS_READ = [1, 2]
LOCK_STALE_MINUTES = 15
LOCK_FUTURE_SECONDS = 60     # a lock dated further in the future than this is treated as stale too
EXIT_INPUT, EXIT_LOCKED, EXIT_PENDING, EXIT_INCOMPLETE = 2, 3, 4, 5

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
               "september", "october", "november", "december"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_RE = (r"(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|"
              r"sat(?:urday)?|sun(?:day)?)")
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}

SOURCE_KEYS = ["meetings", "email", "slack", "tracker", "calendar"]
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_connected", "not_configured"]
SOURCE_LABELS = {"meetings": "Meeting notes", "email": "Email", "slack": "Slack", "tracker": "Tracker",
                 "calendar": "Calendar"}
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "pasted": "pasted transcripts", "gmail": "Gmail",
                   "outlook": "Outlook", "slack": "Slack", "linear": "Linear", "jira": "Jira",
                   "asana": "Asana", "google": "Google Calendar", "google_calendar": "Google Calendar",
                   "outlook_calendar": "Outlook Calendar"}
READ_STATUSES = ["ok", "denied", "unavailable", "not_requested"]
QUOTE_SOURCES = ["transcript", "notes", "summary"]
DIRECTIONS = ["we_owe", "they_owe"]
KINDS = ["commitment", "soft_mention"]
DUE_KINDS = ["explicit", "relative", "none"]
EVIDENCE_KINDS = {"sent_email": "email", "received_email": "email", "slack_message": "slack",
                  "tracker_issue": "tracker", "calendar_event": "calendar", "later_meeting": "meetings"}
STRENGTHS = ["strong", "weak", "against"]
USER_ACTIONS = ["dismiss", "restore", "mark_done", "reopen", "set_due"]
AUDIENCES = ["external", "internal"]
CADENCES = ["daily", "weekly", "on_request"]
CONFIG_KEYS = ["due_soon_days", "stale_days", "business_days", "holidays", "plan_history_days",
               "max_quote_chars", "prune_after_days", "max_runs_logged"]
CONFIG_OPTIONAL = {"prune_open_after_days": 180, "unreadable_retry_runs": 2, "unreadable_retry_days": 3}
RUN_STATUSES = ["pending", "posted", "empty", "superseded", "incomplete"]
DONE_RUN_STATUSES = ["posted", "empty"]
STATE_DUE_KINDS = ["explicit", "inferred", "none", "ambiguous"]
ID_RE = re.compile(r"ct-[0-9a-f]{10}")
DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
# ASCII digits only; hours 00-23, minutes and seconds 00-59, offsets -14:00..+14:00 (so 3.8 and 3.14 agree)
MOMENT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                       r"(?::[0-5][0-9](?:\.(?:[0-9]{3}|[0-9]{6}))?)?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
SLOT_RE = re.compile(r"(daily|weekly):[0-9]{4}-[0-9]{2}-[0-9]{2}")
PERIOD_RE = re.compile(r"(daily|weekly|on_request):([0-9]{4}-[0-9]{2}-[0-9]{2})\.\.([0-9]{4}-[0-9]{2}-[0-9]{2})")
YEARS = (1900, 2999)
MAX_DAYS = 36500
MAX_DUE_DAYS = 3650             # a relative phrase resolving further out than this is treated as "no date"
FAR_NOTE = "more than %d days out" % MAX_DUE_DAYS
STOPWORDS = {"the", "a", "an", "to", "for", "of", "our", "your", "their", "my", "his", "her", "its",
             "them", "him", "us", "me", "over", "across", "out", "up", "and", "with", "on", "by",
             "this", "that", "these", "those", "some", "we", "i", "you", "will", "ll", "gonna",
             "going", "get", "back", "please", "just", "also"}


class InputError(Exception):
    """Bad input; the message names the file and the JSON path."""


class StopRun(Exception):
    """A clean stop with its own exit code (lock held, period pending)."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code


# --------------------------------------------------------------------------- reading helpers


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

    def lst(self, value, path):
        """A list; an absent optional list is passed in as [] by the caller, so null is always an error."""
        if not isinstance(value, list):
            raise self.fail(path, "must be a list, got %s" % ("null" if value is None else type(value).__name__))
        return value

    def text(self, parent, key, path, required=True):
        value = parent.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise self.fail("%s.%s" % (path, key), "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail("%s.%s" % (path, key), "must be a string, got %r" % (value,))
        return value.strip()

    def integer(self, parent, key, path, required=True, minimum=0, maximum=MAX_DAYS):
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise self.fail("%s.%s" % (path, key), "must be an integer from %d to %d, got %r"
                            % (minimum, maximum, value))
        return value

    def enum(self, parent, key, path, allowed, default=None):
        value = parent.get(key, default)
        if value not in allowed:
            raise self.fail("%s.%s" % (path, key), "must be one of %s, got %r" % (", ".join(allowed), value))
        return value

    def day(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        return parse_day(value, lambda problem: self.fail("%s.%s" % (path, key), problem), allow_moment=True)

    def moment(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        return parse_moment(value, lambda problem: self.fail("%s.%s" % (path, key), problem))


def in_years(d, value, fail):
    if not YEARS[0] <= d.year <= YEARS[1]:
        raise fail("%r is outside the years %d-%d" % (value, YEARS[0], YEARS[1]))
    return d


def parse_moment(value, fail):
    """Strict ISO-8601 datetime with a UTC offset; identical on Python 3.8 to 3.14."""
    if not isinstance(value, str) or not MOMENT_RE.fullmatch(value):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?", value):
            raise fail("%r has no UTC offset; use e.g. 2026-10-09T16:00:00-07:00" % (value,))
        raise fail("%r is not an ISO-8601 datetime with a UTC offset (YYYY-MM-DDTHH:MM[:SS[.fff]]+HH:MM or Z; "
                   "hours 00-23, offset within +/-14:00)" % (value,))
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise fail("%r is not a valid datetime" % (value,)) from None
    return in_years(moment, value, fail)


def parse_day(value, fail, allow_moment=False):
    """Strict YYYY-MM-DD (or, where allowed, a datetime with offset, using its own local date)."""
    if isinstance(value, str) and allow_moment and "T" in value:
        return parse_moment(value, fail).date()
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise fail("%r is not a YYYY-MM-DD date" % (value,))
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise fail("%r is not a valid date" % (value,)) from None
    return in_years(d, value, fail)


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


SURROGATE_RE = re.compile("[\ud800-\udfff]")


def reject_surrogates(data, label):
    """A \\ud800-style escape decodes to a lone surrogate that can't be written as UTF-8: refuse it up front."""
    stack = [(data, "$")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if SURROGATE_RE.search(key):
                    raise InputError("%s: %s: a key contains a lone surrogate (\\ud800-\\udfff escape); "
                                     "not valid text" % (label, path))
                stack.append((value, "%s.%s" % (path, key)))
        elif isinstance(node, list):
            stack.extend((value, "%s[%d]" % (path, i)) for i, value in enumerate(node))
        elif isinstance(node, str) and SURROGATE_RE.search(node):
            raise InputError("%s: %s: contains a lone surrogate (\\ud800-\\udfff escape); not valid text" % (label, path))


def reject_constant(name):
    raise ValueError("%s is not allowed; use a number or null" % name)


def load_config(path):
    config = load_json(path, "config")
    r = Reader("config %s" % path)
    r.obj(config, "$")
    missing = [key for key in CONFIG_KEYS if key not in config]
    if missing:
        raise InputError("config %s: missing key(s): %s" % (path, ", ".join(missing)))
    for key, default in sorted(CONFIG_OPTIONAL.items()):
        config.setdefault(key, default)
    for key in ["due_soon_days", "stale_days", "max_quote_chars", "prune_after_days", "max_runs_logged",
                "prune_open_after_days"]:
        r.integer(config, key, "$", minimum=1)
    for key in ["unreadable_retry_runs", "unreadable_retry_days"]:
        r.integer(config, key, "$", minimum=0)
    days = r.lst(config["business_days"], "$.business_days")
    if not days or any(not isinstance(d, str) or d not in WEEKDAY_NAMES for d in days):
        raise r.fail("$.business_days", "must list weekday names from %s" % ", ".join(WEEKDAY_NAMES))
    config["_business"] = sorted({WEEKDAY_NAMES.index(d) for d in days})
    holidays = set()
    for i, h in enumerate(r.lst(config["holidays"], "$.holidays")):
        holidays.add(parse_day(h, lambda problem: r.fail("$.holidays[%d]" % i, problem)))
    config["_holidays"] = holidays
    plans = r.obj(config["plan_history_days"], "$.plan_history_days")
    for plan, days_ in plans.items():
        r.integer(plans, plan, "$.plan_history_days", minimum=1)
    return config


# --------------------------------------------------------------------------- normalisation


def fold(value):
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return value.casefold()


def norm_text(value):
    """Casefolded, punctuation-free, single-spaced text (used for ids)."""
    return " ".join(re.sub(r"[^\w]+", " ", fold(value)).split())


def norm_deliverable(value):
    """Order-preserving deliverable key with filler words dropped ("Send over the deck" == "send deck")."""
    words = [w for w in norm_text(value).split() if w not in STOPWORDS]
    return " ".join(words) or norm_text(value)


def norm_owner(value):
    return " ".join(fold(value).split())


def commitment_id(meeting_id, quote, owner, deliverable):
    """One id per (meeting, quote, owner, deliverable): two deliverables in one sentence stay two items."""
    raw = "%s|%s|%s|%s" % (meeting_id, norm_text(quote), norm_owner(owner), norm_deliverable(deliverable))
    return "ct-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]



# --------------------------------------------------------------------------- links and escaping

URL_RE = re.compile(r"(https?)://([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?([/?#].*)?", re.IGNORECASE)
LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
TLD_RE = re.compile(r"[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{1,59}", re.IGNORECASE)
BIDI_RE = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def safe_url(value):
    """An absolute http(s) URL with a DNS host name (letter/digit/hyphen labels, alphabetic TLD) and an optional
    port 1-65535, unsafe characters percent-encoded; None if not acceptable (IP literals and localhost included)."""
    if not isinstance(value, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
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
    rest = url_quote(m.group(4) or "", safe="/?#&=+:;,@!$'*~%-._")
    return "%s://%s%s%s" % (m.group(1).lower(), m.group(2).lower(), ":" + port if port else "", rest)


def clean(value):
    """One line of plain text: whitespace collapsed, bidi controls removed."""
    return " ".join(BIDI_RE.sub("", value).split())


MD_DOT_RE = re.compile(r"(?<=\S)\.(?=[\w-])")                                  # a dot inside a word or domain
SLACK_DOMAIN_DOT_RE = re.compile(r"(?<=[\w-])\.(?=[^\W\d_])|(?<=www)\.", re.IGNORECASE)


class Markdown:
    """Markdown for Type Spaces and docs. Values are escaped so their characters can't form links, autolinks, HTML,
    math or formatting: & < > as entities, then (in one pass, so the backslash is escaped too) \\ ` * [ ] | ~ $ =
    with a backslash ($...$ is math and ==...== a highlight in Type); then :// and every dot inside a word get a backslash too (rendered unchanged), so a bare URL, www.
    name or domain in a value can't be auto-linked."""
    name = "markdown"

    def text(self, value):
        value = clean(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        value = re.sub(r"([\\`*\[\]|~$=])", r"\\\1", value)
        value = re.sub(r"(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])", r"\\_", value)  # snake_case words stay readable
        return MD_DOT_RE.sub(r"\\.", value.replace("://", "\\://"))

    def bold(self, inner):
        return "**%s**" % inner

    def italic(self, inner):
        return "_%s_" % inner

    def heading(self, level, inner):
        return "#" * level + " " + inner

    def link(self, label, url):
        url = safe_url(url)
        return "[%s](%s)" % (self.text(label), url) if url else self.text(label)


SLACK_LOOKALIKES = {"*": "\u2217", "_": "\uff3f", "~": "\u223c", "`": "\u02cb"}


class Slack(Markdown):
    """Slack mrkdwn: & < > escaped in every value, links built only here as <url|label>. Slack has no escape
    character for * _ ~ `, so inside values they are shown as look-alikes (∗ ＿ ∼ ˋ) and can't open or close
    bold, italic, strike or code around them; @ before a name becomes ＠, and :// and the dots of a domain or www.
    name become ∕∕ and ․ so Slack can't auto-link a bare URL or domain in a value."""
    name = "slack"

    def text(self, value):
        value = clean(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        value = re.sub(r"@(?=\w)", "\uff20", value)  # no @channel or @name mention, even if the post sets link_names
        value = "".join(SLACK_LOOKALIKES.get(ch, ch) for ch in value)
        return SLACK_DOMAIN_DOT_RE.sub("\u2024", value.replace("://", ":\u2215\u2215"))

    def bold(self, inner):
        return "*%s*" % inner

    def heading(self, level, inner):
        return "*%s*" % inner

    def link(self, label, url):
        url = safe_url(url)
        return "<%s|%s>" % (url, self.text(label).replace("|", "")) if url else self.text(label)


FORMATS = {"markdown": Markdown(), "slack": Slack()}


def domain_of(email):
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def in_domains(domain, domains):
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


# --------------------------------------------------------------------------- business days and due dates


class Calendar:
    def __init__(self, config):
        self.business = config["_business"]
        self.holidays = config["_holidays"]

    def is_business(self, d):
        return d.weekday() in self.business and d not in self.holidays

    def next_business_after(self, d):
        d = d + timedelta(days=1)
        for _ in range(60):
            if self.is_business(d):
                return d
            d += timedelta(days=1)
        return None

    def add_business(self, d, n):
        for _ in range(n):
            d = self.next_business_after(d)
            if d is None:
                return None
        return d

    def last_business_in(self, start, end):
        d = end
        while d >= start:
            if self.is_business(d):
                return d
            d -= timedelta(days=1)
        return None

    def nth_business_in(self, start, end, n):
        d, seen = start, 0
        while d <= end:
            if self.is_business(d):
                seen += 1
                if seen == n:
                    return d
            d += timedelta(days=1)
        return None


def week_bounds(d, weeks_ahead=0):
    monday = d - timedelta(days=d.weekday()) + timedelta(days=7 * weeks_ahead)
    return monday, monday + timedelta(days=6)


def month_bounds(d, months_ahead=0):
    month0 = d.month - 1 + months_ahead
    year, month = d.year + month0 // 12, month0 % 12 + 1
    first = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first, nxt - timedelta(days=1)


def quarter_bounds(d):
    q_first_month = 3 * ((d.month - 1) // 3) + 1
    first = date(d.year, q_first_month, 1)
    _, last = month_bounds(first, 2)
    return first, last


def weekday_index(token):
    return WEEKDAY_NAMES.index(token[:3])


def clean_phrase(phrase):
    p = fold(phrase)
    p = re.sub(r"[\"(),.!?;:]", " ", p)
    p = " ".join(p.split())
    leading = ["no later than", "by the", "by", "before", "until", "till", "til", "on", "due", "for",
               "around", "sometime", "some time", "first thing", "latest", "at the latest"]
    changed = True
    while changed:
        changed = False
        for word in leading:
            if p.startswith(word + " "):
                p = p[len(word) + 1:]
                changed = True
    trailing = [r"\s+at the latest$", r"\s+latest$", r"\s+at \d{1,2}(:\d\d)?\s*(am|pm)?$", r"\s+\d{1,2}(:\d\d)?\s*(am|pm)$",
                r"\s+(eod|cob|end of day|end of the day|close of business|morning|afternoon|evening|night|noon|midday)$",
                r"\s+(in the )?(morning|afternoon|evening)$", r"\s+at noon$"]
    changed = True
    while changed:
        changed = False
        for pattern in trailing:
            new = re.sub(pattern, "", p)
            if new != p and new.strip():
                p = new.strip()
                changed = True
    return p


def resolve_due(due, meeting_day, cal):
    """Return {date, kind, phrase, note}. kind: explicit | inferred | none | ambiguous. A relative phrase that
    lands more than MAX_DUE_DAYS after the meeting (or past the supported years) gives no date, with a note."""
    out = resolve_phrase(due, meeting_day, cal)
    if out["date"] is not None and due["kind"] == "relative":
        if (out["date"] - meeting_day).days > MAX_DUE_DAYS or out["date"].year > YEARS[1]:
            return {"date": None, "kind": "none", "phrase": due.get("phrase"), "note": FAR_NOTE}
        if out["date"] < meeting_day:
            return {"date": None, "kind": "none", "phrase": due.get("phrase"), "note": "phrase not recognised"}
    return out


def resolve_phrase(due, meeting_day, cal):
    kind = due["kind"]
    phrase = due.get("phrase")
    if kind == "explicit":
        return {"date": due["date"], "kind": "explicit", "phrase": phrase, "note": None}
    if kind == "none":
        return {"date": None, "kind": "none", "phrase": phrase, "note": None}
    p = clean_phrase(phrase)
    d = meeting_day

    def inferred(value):
        if value is None:
            return {"date": None, "kind": "none", "phrase": phrase, "note": "no business day found"}
        return {"date": value, "kind": "inferred", "phrase": phrase, "note": None}

    if re.fullmatch(r"(today|tonight|end of (the )?day|eod|cob|close of business|end of business|eob)", p):
        return inferred(d)
    if re.fullmatch(r"tomorrow", p):
        return inferred(cal.next_business_after(d))
    m = re.fullmatch(r"(this )?" + WEEKDAY_RE, p)
    if m:
        target = weekday_index(m.group(2))
        delta = (target - d.weekday()) % 7
        if delta == 0:
            return {"date": None, "kind": "ambiguous", "phrase": phrase,
                    "note": "said on a %s" % WEEKDAYS[d.weekday()]}
        return inferred(d + timedelta(days=delta))
    m = re.fullmatch(r"next " + WEEKDAY_RE, p)
    if m:
        monday, _ = week_bounds(d, 1)
        return inferred(monday + timedelta(days=weekday_index(m.group(1))))
    if re.fullmatch(r"(the )?(end of (the |this )?week|eow|this week|later this week|end of week|week'?s end)", p):
        start, end = week_bounds(d)
        value = cal.last_business_in(max(start, d), end)
        if value is None:
            start, end = week_bounds(d, 1)
            value = cal.last_business_in(start, end)
        return inferred(value)
    if re.fullmatch(r"(next week|end of next week|the end of next week|eonw)", p):
        start, end = week_bounds(d, 1)
        return inferred(cal.last_business_in(start, end))
    if re.fullmatch(r"(early next week|beginning of next week|start of next week)", p):
        start, end = week_bounds(d, 1)
        return inferred(cal.nth_business_in(start, end, 2))
    m = re.fullmatch(r"(in|within) ([0-9]{1,12}|a|an|one|two|three|four|five|six|seven|eight|nine|ten) "
                     r"(business |working )?(day|days|week|weeks)( time)?", p)
    if m:
        n = int(m.group(2)) if m.group(2).isdigit() else NUMBER_WORDS[m.group(2)]
        unit = m.group(4)
        if n * (7 if unit.startswith("week") else 1) > MAX_DUE_DAYS:  # checked before any date arithmetic
            return {"date": None, "kind": "none", "phrase": phrase, "note": FAR_NOTE}
        if m.group(3) and unit.startswith("day"):
            return inferred(cal.add_business(d, n))
        if m.group(3):
            return {"date": None, "kind": "none", "phrase": phrase, "note": "phrase not recognised"}
        return inferred(d + timedelta(days=n * (7 if unit.startswith("week") else 1)))
    if re.fullmatch(r"(the )?(end of (the |this )?month|eom|this month|month end|month-end|end of month)", p):
        start, end = month_bounds(d)
        value = cal.last_business_in(max(start, d), end)
        if value is None:  # said after the month's last business day (e.g. on the 31st, a Saturday)
            start, end = month_bounds(d, 1)
            value = cal.last_business_in(start, end)
        return inferred(value)
    if re.fullmatch(r"(next month|end of next month|the end of next month)", p):
        start, end = month_bounds(d, 1)
        return inferred(cal.last_business_in(start, end))
    if re.fullmatch(r"(the )?(end of (the |this )?quarter|eoq|this quarter|quarter end|end of quarter)", p):
        start, end = quarter_bounds(d)
        value = cal.last_business_in(max(start, d), end)
        if value is None:
            start, end = quarter_bounds(end + timedelta(days=1))
            value = cal.last_business_in(start, end)
        return inferred(value)
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", p)
    if m:
        try:
            return {"date": date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "kind": "explicit",
                    "phrase": phrase, "note": None}
        except ValueError:
            return {"date": None, "kind": "none", "phrase": phrase, "note": "phrase not recognised"}
    m = (re.fullmatch(r"(?:" + WEEKDAY_RE + r" )?([a-z]+) ([0-9]{1,2})(?:st|nd|rd|th)?", p)
         or re.fullmatch(r"(?:" + WEEKDAY_RE + r" )?(?:the )?([0-9]{1,2})(?:st|nd|rd|th)? (?:of )?([a-z]+)", p))
    if m:
        groups = [g for g in m.groups()[1:]]
        month_word, day_word = (groups[0], groups[1]) if not groups[0].isdigit() else (groups[1], groups[0])
        month = next((i + 1 for i, name in enumerate(MONTH_NAMES)
                      if len(month_word) >= 3 and name.startswith(month_word)), None)
        if month is not None:
            for year in (d.year, d.year + 1):
                try:
                    candidate = date(year, month, int(day_word))
                except ValueError:
                    return {"date": None, "kind": "none", "phrase": phrase, "note": "phrase not recognised"}
                if candidate >= d:
                    return {"date": candidate, "kind": "explicit", "phrase": phrase, "note": "year inferred"}
    return {"date": None, "kind": "none", "phrase": phrase, "note": "phrase not recognised"}


# --------------------------------------------------------------------------- bundle


def load_bundle(path, internal):
    raw = load_json(path, "bundle")
    r = Reader("bundle %s" % path)
    r.obj(raw, "$")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise r.fail("$.schema_version", "must be %d, got %r" % (SCHEMA_VERSION, raw.get("schema_version")))

    scan_raw = r.obj(raw.get("scan"), "$.scan")
    scan = {"from": r.day(scan_raw, "from", "$.scan"), "to": r.day(scan_raw, "to", "$.scan"),
            "cadence": r.enum(scan_raw, "cadence", "$.scan", CADENCES)}
    if scan["from"] > scan["to"]:
        raise r.fail("$.scan", "from (%s) is after to (%s)" % (scan["from"], scan["to"]))

    sources_raw = r.obj(raw.get("sources"), "$.sources")
    sources = {}
    for key in SOURCE_KEYS:
        s = r.obj(sources_raw.get(key), "$.sources.%s" % key)
        status = r.enum(s, "status", "$.sources.%s" % key, SOURCE_STATUSES)
        provider = r.text(s, "provider", "$.sources.%s" % key, required=(key == "meetings" or status == "ok"))
        entry = {"provider": (provider or "").lower() or None, "status": status,
                 "detail": r.text(s, "detail", "$.sources.%s" % key, required=False)}
        if key == "meetings":
            entry["plan"] = r.text(s, "plan", "$.sources.meetings", required=False)
            entry["history_limit_days"] = r.integer(s, "history_limit_days", "$.sources.meetings",
                                                    required=False, minimum=1)
        sources[key] = entry

    url_warnings = []

    def link_field(parent, key, path, required):
        value = r.text(parent, key, path, required)
        if value is None:
            return None
        url = safe_url(value)
        if url is None:
            url_warnings.append("%s.%s" % (path, key))
        return url

    meetings = {}
    order = []
    for i, m in enumerate(r.lst(raw.get("meetings", []), "$.meetings")):
        p = "$.meetings[%d]" % i
        r.obj(m, p)
        mid = r.text(m, "meeting_id", p)
        if mid in meetings:
            raise r.fail(p + ".meeting_id", "duplicate meeting_id %r" % mid)
        start = r.moment(m, "start", p)
        local_day = start.date()
        reread = m.get("reread", False)
        if not isinstance(reread, bool):
            raise r.fail(p + ".reread", "must be true or false, got %r" % (reread,))
        # A meeting given up on earlier (listed under reread by --next-window) may come from before the window.
        if not (scan["from"] <= local_day <= scan["to"]) and not (reread and local_day <= scan["to"]):
            raise r.fail(p + ".start", "%s is outside the scan window %s..%s" % (local_day, scan["from"], scan["to"]))
        attendees = []
        for j, a in enumerate(r.lst(m.get("attendees", []), p + ".attendees")):
            ap = "%s.attendees[%d]" % (p, j)
            r.obj(a, ap)
            attendees.append({"email": r.text(a, "email", ap).lower(), "name": r.text(a, "name", ap, required=False)})
        audience_override = m.get("audience")
        if audience_override is not None:
            r.enum(m, "audience", p, AUDIENCES)
            r.text(m, "audience_reason", p)
        external_people = [a for a in attendees if not in_domains(domain_of(a["email"]), internal)]
        computed = "external" if external_people else ("internal" if attendees else "unknown")
        meetings[mid] = {
            "meeting_id": mid, "title": r.text(m, "title", p), "start": start,
            "day": local_day,
            "url": link_field(m, "url", p, False), "account": r.text(m, "account", p, required=False),
            "attendees": attendees, "audience": audience_override or computed,
            "audience_overridden": audience_override is not None and audience_override != computed,
            "external_domains": sorted({domain_of(a["email"]) for a in external_people if domain_of(a["email"])}),
            "notes_status": r.enum(m, "notes_status", p, ["ok", "denied", "unavailable"]),
            "transcript_status": r.enum(m, "transcript_status", p, READ_STATUSES),
            "reread": reread, "index": i,
        }
        order.append(mid)

    commitments = []
    refs = {}
    for i, c in enumerate(r.lst(raw.get("commitments", []), "$.commitments")):
        p = "$.commitments[%d]" % i
        r.obj(c, p)
        ref = r.text(c, "ref", p)
        if ref in refs:
            raise r.fail(p + ".ref", "duplicate ref %r" % ref)
        mid = r.text(c, "meeting_id", p)
        if mid not in meetings:
            raise r.fail(p + ".meeting_id", "%r is not in $.meetings" % mid)
        meeting = meetings[mid]
        kind = r.enum(c, "kind", p, KINDS, default="commitment")
        quote = r.text(c, "quote", p)
        quote_source = r.enum(c, "quote_source", p, QUOTE_SOURCES)
        if quote_source == "transcript" and meeting["transcript_status"] != "ok":
            raise r.fail(p + ".quote_source", "is 'transcript' but the transcript of %r was not read (status %s); "
                         "quote the notes instead" % (mid, meeting["transcript_status"]))
        if quote_source in ("notes", "summary") and meeting["notes_status"] != "ok":
            raise r.fail(p + ".quote_source", "is %r but the notes of %r were not read (status %s)"
                         % (quote_source, mid, meeting["notes_status"]))
        item = {"ref": ref, "meeting_id": mid, "kind": kind, "quote": quote, "quote_source": quote_source,
                "index": i}
        if kind == "soft_mention":
            item["owner"] = r.text(c, "owner", p, required=False)
            commitments.append(item)
            refs[ref] = item
            continue
        direction = r.enum(c, "direction", p, DIRECTIONS)
        owner = r.text(c, "owner", p)
        owner_email = r.text(c, "owner_email", p, required=False)
        if owner_email:
            owner_email = owner_email.lower()
            is_internal = in_domains(domain_of(owner_email), internal)
            if direction == "we_owe" and not is_internal:
                raise r.fail(p + ".direction", "is we_owe but owner_email %s is not on an internal domain" % owner_email)
            if direction == "they_owe" and is_internal:
                raise r.fail(p + ".direction", "is they_owe but owner_email %s is on an internal domain" % owner_email)
        due_raw = r.obj(c.get("due"), p + ".due", required=False) or {"kind": "none"}
        due_kind = r.enum(due_raw, "kind", p + ".due", DUE_KINDS)
        due = {"kind": due_kind, "phrase": r.text(due_raw, "phrase", p + ".due", required=(due_kind == "relative"))}
        if due_kind == "explicit":
            due["date"] = r.day(due_raw, "date", p + ".due")
            if due["date"] < meeting["day"]:
                raise r.fail(p + ".due.date", "%s is before the meeting it was promised in (%s)"
                             % (due["date"], meeting["day"]))
        elif due_raw.get("date") is not None:
            raise r.fail(p + ".due.date", "only allowed when kind is explicit; give relative phrases in phrase "
                         "and let the script resolve them")
        item.update({
            "direction": direction, "owner": owner, "owner_email": owner_email,
            "counterpart": r.text(c, "counterpart", p, required=False),
            "counterpart_email": (r.text(c, "counterpart_email", p, required=False) or "").lower() or None,
            "deliverable": r.text(c, "deliverable", p), "due": due,
            "same_as": r.text(c, "same_as", p, required=False),
            "known_id": r.text(c, "known_id", p, required=False),
        })
        commitments.append(item)
        refs[ref] = item
    for i, c in enumerate(commitments):
        if c.get("same_as"):
            target = refs.get(c["same_as"])
            if target is None or target is c or target["kind"] != "commitment":
                raise r.fail("$.commitments[%d].same_as" % c["index"], "%r is not another commitment ref" % c["same_as"])
            if target["direction"] != c["direction"]:
                raise r.fail("$.commitments[%d].same_as" % c["index"], "links commitments with different directions")
            if norm_owner(target["owner"]) != norm_owner(c["owner"]):
                raise r.fail("$.commitments[%d].same_as" % c["index"], "links commitments with different owners")

    evidence = []
    for i, e in enumerate(r.lst(raw.get("evidence", []), "$.evidence")):
        p = "$.evidence[%d]" % i
        r.obj(e, p)
        ref = r.text(e, "ref", p, required=False)
        cid = r.text(e, "commitment_id", p, required=False)
        if bool(ref) == bool(cid):
            raise r.fail(p, "give exactly one of ref (this bundle) or commitment_id (from the state file)")
        if ref and (ref not in refs or refs[ref]["kind"] != "commitment"):
            raise r.fail(p + ".ref", "%r is not a commitment ref in this bundle" % ref)
        kind = r.enum(e, "kind", p, sorted(EVIDENCE_KINDS))
        source_key = EVIDENCE_KINDS[kind]
        if sources[source_key]["status"] != "ok":
            raise r.fail(p + ".kind", "%s evidence comes from sources.%s, whose status is %s"
                         % (kind, source_key, sources[source_key]["status"]))
        strength = r.enum(e, "strength", p, STRENGTHS)
        evidence.append({
            "ref": ref, "commitment_id": cid, "kind": kind, "strength": strength,
            "date": r.day(e, "date", p), "url": link_field(e, "url", p, True), "summary": r.text(e, "summary", p),
            "why": r.text(e, "why", p, required=(strength == "weak")), "index": i,
        })

    actions = []
    for i, a in enumerate(r.lst(raw.get("user_actions", []), "$.user_actions")):
        p = "$.user_actions[%d]" % i
        r.obj(a, p)
        action = r.enum(a, "action", p, USER_ACTIONS)
        due_date = r.day(a, "due_date", p, required=(action == "set_due"))
        if due_date is not None and action != "set_due":
            raise r.fail(p + ".due_date", "only allowed with action set_due")
        actions.append({"commitment_id": r.text(a, "commitment_id", p), "action": action,
                        "date": r.day(a, "date", p), "due_date": due_date,
                        "note": r.text(a, "note", p, required=False), "index": i})

    return {"scan": scan, "sources": sources, "meetings": meetings, "meeting_order": order,
            "commitments": commitments, "refs": refs, "evidence": evidence, "actions": actions,
            "url_warnings": url_warnings}


# --------------------------------------------------------------------------- state


def empty_state():
    return {"state_version": STATE_VERSION, "commitments": {}, "runs": [], "unread_meetings": {}}


class StateCheck:
    """Strict state validation: any wrong type, null or missing field stops the run with exit 2."""

    def __init__(self, path):
        self.label = "state %s" % path

    def fail(self, where, problem):
        return InputError("%s: %s: %s; refusing to run blind (restore a backup or move the file aside "
                          "and start a new one)" % (self.label, where, problem))

    def value(self, obj, key, where, kinds, nullable=False, required=True):
        where = "%s.%s" % (where, key)
        if not isinstance(obj, dict):
            raise self.fail(where.rsplit(".", 1)[0], "must be an object")
        if key not in obj:
            if required:
                raise self.fail(where, "is missing")
            return None
        v = obj[key]
        if v is None:
            if nullable:
                return None
            raise self.fail(where, "must not be null")
        if isinstance(v, bool) or not isinstance(v, kinds):
            raise self.fail(where, "has the wrong type (%s)" % type(v).__name__)
        return v

    def text(self, obj, key, where, nullable=False, required=True):
        v = self.value(obj, key, where, str, nullable, required)
        if v is not None and not v.strip():
            raise self.fail("%s.%s" % (where, key), "must not be empty")
        return v

    def enum(self, obj, key, where, allowed, nullable=False, required=True):
        v = self.value(obj, key, where, str, nullable, required)
        if v is not None and v not in allowed:
            raise self.fail("%s.%s" % (where, key), "must be one of %s" % ", ".join(allowed))
        return v

    def day(self, obj, key, where, nullable=False, required=True):
        v = self.value(obj, key, where, str, nullable, required)
        if v is not None:
            parse_day(v, lambda problem: self.fail("%s.%s" % (where, key), problem))
        return v

    def moment(self, obj, key, where, nullable=False, required=True):
        v = self.value(obj, key, where, str, nullable, required)
        if v is not None:
            parse_moment(v, lambda problem: self.fail("%s.%s" % (where, key), problem))
        return v

    def url(self, obj, key, where):
        v = self.value(obj, key, where, str, nullable=True)
        obj[key] = safe_url(v) if v is not None else None

    def ident(self, value, where):
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise self.fail(where, "is not a commitment id (ct- plus 10 hex characters)")
        return value

    def meeting_ref(self, m, where, mention=False):
        for key in ["meeting_id", "title"]:
            self.text(m, key, where)
        self.moment(m, "start", where)
        self.day(m, "day", where)
        self.url(m, "url", where)
        if mention:
            self.text(m, "quote", where)
            self.enum(m, "quote_source", where, QUOTE_SOURCES)
            due = self.value(m, "due", where, dict)
            self.due(due, where + ".due", STATE_DUE_KINDS)

    def due(self, due, where, kinds):
        self.day(due, "date", where, nullable=True)
        kind = self.enum(due, "kind", where, kinds)
        self.text(due, "phrase", where, nullable=True)
        self.text(due, "note", where, nullable=True)
        return kind

    def record(self, cid, rec, where):
        self.ident(cid, where)
        if not isinstance(rec, dict):
            raise self.fail(where, "must be an object")
        if rec.get("id") != cid:
            raise self.fail(where + ".id", "must equal its key %r" % cid)
        self.enum(rec, "direction", where, DIRECTIONS)
        for key in ["owner", "deliverable", "dedupe_key"]:
            self.text(rec, key, where)
        for key in ["owner_email", "counterpart", "counterpart_email"]:
            self.text(rec, key, where, nullable=True)
        self.meeting_ref(self.value(rec, "origin", where, dict), where + ".origin")
        mentions = self.value(rec, "mentions", where, list)
        if not mentions:
            raise self.fail(where + ".mentions", "must not be empty")
        for i, m in enumerate(mentions):
            if not isinstance(m, dict):
                raise self.fail("%s.mentions[%d]" % (where, i), "must be an object")
            self.meeting_ref(m, "%s.mentions[%d]" % (where, i), mention=True)
        due = self.value(rec, "due", where, dict)
        kind = self.due(due, where + ".due", STATE_DUE_KINDS + ["set_by_user"])
        self.day(due, "was", where + ".due", nullable=True, required=False)
        self.text(due, "repromised_in", where + ".due", nullable=True, required=False)
        self.day(due, "repromised_day", where + ".due", nullable=True, required=False)
        if bool(due.get("was")) != bool(due.get("repromised_day")):
            raise self.fail(where + ".due", "was and repromised_day must be set together")
        self.day(due, "set_on", where + ".due", nullable=(kind != "set_by_user"), required=(kind == "set_by_user"))
        for i, e in enumerate(self.value(rec, "evidence", where, list)):
            w = "%s.evidence[%d]" % (where, i)
            if not isinstance(e, dict):
                raise self.fail(w, "must be an object")
            self.enum(e, "kind", w, sorted(EVIDENCE_KINDS) + ["user"])
            self.enum(e, "strength", w, STRENGTHS)
            self.day(e, "date", w)
            self.url(e, "url", w)
            self.text(e, "summary", w)
            self.text(e, "why", w, nullable=True)
            self.text(e, "order", w, nullable=True, required=False)
        dismissed = self.value(rec, "dismissed", where, dict, nullable=True)
        if dismissed is not None:
            self.day(dismissed, "date", where + ".dismissed")
            self.text(dismissed, "note", where + ".dismissed", nullable=True)
        override = self.value(rec, "due_override", where, dict, nullable=True)
        if override is not None:
            self.day(override, "date", where + ".due_override")
            self.day(override, "set_on", where + ".due_override")
            self.text(override, "note", where + ".due_override", nullable=True)
        self.moment(rec, "first_reported", where, nullable=True)
        self.moment(rec, "done_reported", where, nullable=True)
        self.enum(rec, "last_status", where, ["open", "overdue", "likely_done", "done", "dismissed"], nullable=True)
        self.day(rec, "restored_on", where, nullable=True, required=False)
        earlier = self.value(rec, "earlier", where, str, nullable=True, required=False)
        if earlier is not None:
            self.ident(earlier, where + ".earlier")

    def unread(self, mid, rec, where):
        """A meeting whose read failed: retried by scheduled runs, then given up (and listed as not covered)."""
        self.text({"id": mid}, "id", where)
        if not isinstance(rec, dict):
            raise self.fail(where, "must be an object")
        self.text(rec, "title", where)
        self.day(rec, "day", where)
        self.url(rec, "url", where)
        attempts = self.value(rec, "attempts", where, int)
        if attempts < 1:
            raise self.fail(where + ".attempts", "must be at least 1")
        self.moment(rec, "first_failed", where)
        self.moment(rec, "last_failed", where)
        slot = self.text(rec, "last_slot", where)
        if not SLOT_RE.fullmatch(slot):
            raise self.fail(where + ".last_slot", "is not <daily|weekly>:<YYYY-MM-DD>")
        self.moment(rec, "given_up", where, nullable=True)
        self.day(rec, "reread_until", where)

    def run(self, run, where, version):
        if not isinstance(run, dict):
            raise self.fail(where, "must be an object")
        key = self.text(run, "period_key", where)
        if period_bounds(key) is None:
            raise self.fail(where + ".period_key", "is not <cadence>:<YYYY-MM-DD>..<YYYY-MM-DD>")
        self.moment(run, "as_of", where)
        if version == 1:
            mode = self.enum(run, "mode", where, ["scheduled", "on_request"])
            result = self.enum(run, "result", where, ["reported", "skipped"])
            if mode != "scheduled":
                return None
            return {"period_key": run["period_key"], "as_of": run["as_of"], "mode": "scheduled",
                    "status": "posted" if result == "reported" else "empty", "staged": None}
        self.enum(run, "mode", where, ["scheduled"])
        if period_bounds(key)[0] not in ("daily", "weekly"):
            raise self.fail(where + ".period_key", "a scheduled run must be daily or weekly")
        status = self.enum(run, "status", where, RUN_STATUSES)
        staged = self.value(run, "staged", where, dict, nullable=(status != "pending"))
        if staged is not None:
            for key in ["first_reported", "done_reported"]:
                for i, cid in enumerate(self.value(staged, key, where + ".staged", list)):
                    self.ident(cid, "%s.staged.%s[%d]" % (where, key, i))
            for i, mid in enumerate(self.value(staged, "read_again", where + ".staged", list, required=False) or []):
                self.text({"id": mid}, "id", "%s.staged.read_again[%d]" % (where, i))
        self.moment(run, "confirmed_at", where, nullable=True, required=False)
        return run


def load_state(path):
    """Read and fully validate the state file; state_version 1 files are migrated in memory."""
    if path is None or not path.exists():
        return empty_state()
    if not path.is_file():
        raise InputError("state %s: is not a regular file; refusing to run blind" % path)
    try:
        state = load_json(path, "state")
    except InputError as exc:
        raise InputError("%s; refusing to run blind" % exc) from None
    check = StateCheck(path)
    if not isinstance(state, dict) or state.get("state_version") not in STATE_VERSIONS_READ \
            or isinstance(state.get("state_version"), bool):
        raise InputError("state %s: not a commitment-tracker state file (state_version %s expected); "
                         "refusing to run blind" % (path, " or ".join(str(v) for v in STATE_VERSIONS_READ)))
    version = state["state_version"]
    commitments = check.value(state, "commitments", "$", dict)
    runs = check.value(state, "runs", "$", list)
    check.moment(state, "updated_at", "$", nullable=True, required=False)
    unread = check.value(state, "unread_meetings", "$", dict, required=False)
    state["unread_meetings"] = unread if unread is not None else {}
    for mid in sorted(state["unread_meetings"], key=str):
        check.unread(mid, state["unread_meetings"][mid], "$.unread_meetings[%s]" % json.dumps(mid))
    for cid in sorted(commitments, key=str):
        check.record(cid, commitments[cid], "$.commitments.%s" % cid)
    state["runs"] = [r for r in (check.run(run, "$.runs[%d]" % i, version) for i, run in enumerate(runs))
                     if r is not None]
    state["state_version"] = STATE_VERSION
    return state


def check_state_path(path, writing):
    """Fail before anything is printed if the state file could not be read or written."""
    if path is None:
        return
    if path.exists() and not path.is_file():
        raise InputError("state %s: is not a regular file" % path)
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.is_dir():
        raise InputError("state %s: folder %s does not exist" % (path, parent))
    if writing and not os.access(str(parent), os.W_OK):
        raise InputError("state %s: folder %s is not writable, so the run could not be recorded" % (path, parent))


def write_state(path, state):
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    directory = str(path.parent) if str(path.parent) else "."
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".commitment-state-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, str(path))
    except OSError as exc:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
        raise InputError("state %s: could not be written (%s); nothing was printed or recorded"
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


class StateLock:
    """Exclusive <state>.lock (O_CREAT|O_EXCL) around read-modify-write. A lock older than LOCK_STALE_MINUTES, or
    dated more than LOCK_FUTURE_SECONDS in the future, is stale; release removes only a lock holding this run's token."""

    def __init__(self, state_path):
        self.path = Path(str(state_path) + ".lock")
        self.token = None

    def not_a_file(self):
        return InputError("state lock %s: is not a regular file; remove it (only if no run is active) and rerun"
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
                raise StopRun(EXIT_LOCKED, "%s exists: another run is updating this state file (lock is %d s old; "
                              "it counts as stale after %d minutes). Try again later; delete the lock only if no "
                              "run is active." % (self.path, max(age, 0), LOCK_STALE_MINUTES))
            except OSError as exc:
                if os.path.isdir(str(self.path)):
                    raise self.not_a_file() from None
                raise InputError("state lock %s: cannot be created (%s)" % (self.path, exc.strerror or exc)) from None
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("pid %d token %s\n" % (os.getpid(), token))
            self.token = token
            return
        raise StopRun(EXIT_LOCKED, "%s: could not take the state lock; try again later." % self.path)

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


def iso(d):
    return d.isoformat() if d is not None else None


def instant(start):
    """Stored meeting starts keep their own UTC offsets; compare them as instants, never as strings."""
    return datetime.fromisoformat(start[:-1] + "+00:00" if start.endswith("Z") else start)


def to_day(value):
    return date.fromisoformat(value) if value else None


def is_closed(record, today):
    """Done (strong evidence not overtaken) or dismissed: never merge a new promise into it."""
    return evaluate(record, today, 1)[0] in ("done", "dismissed")


def open_on(record, day, today):
    """Whether a closed item was still open on day (YYYY-MM-DD): made by then, and closed after it (or that day, when
    evidence dated that day closed it). A repeat said then is the same promise, even if it is read only after the item
    closed. Your own replies are applied when a run reads them, so a repeat on the day you dismissed or marked the
    item done is taken as said after your reply: a new promise."""
    if record["origin"]["day"] > day or (record.get("dismissed") and day >= record["dismissed"]["date"]):
        return False
    bucket, done_on = evaluate(dict(record, dismissed=None), today, 1)  # done before it was dismissed?
    if bucket == "done":
        by_you = all(e["kind"] == "user" for e in record["evidence"] if e["strength"] == "strong" and e["date"] == done_on)
        return day < done_on if by_you else day <= done_on
    return bool(record.get("dismissed"))


def covered_to(run):
    """First day the next window must include after this run: the last day of its window, or the day before when a
    meeting in a zone behind the run's (offsets down to -14:00) could still carry that earlier date after its as-of.
    Never the run's own date: a digest resumed days later still covered only its window."""
    behind = (instant(run["as_of"]).astimezone(timezone.utc) - timedelta(hours=14)).date()
    return min(period_bounds(run["period_key"])[2], behind)


def last_done_run(state):
    """The confirmed (or empty) run whose window reaches furthest."""
    done = [r for r in state["runs"] if r["status"] in DONE_RUN_STATUSES]
    return max(done, key=lambda r: (covered_to(r), instant(r["as_of"]))) if done else None


def period_bounds(key):
    """(cadence, from, to) of a period key such as weekly:2026-10-05..2026-10-09, or None if malformed."""
    m = PERIOD_RE.fullmatch(key) if isinstance(key, str) else None
    if not m:
        return None
    try:
        start, end = date.fromisoformat(m.group(2)), date.fromisoformat(m.group(3))
    except ValueError:
        return None
    if start > end or not YEARS[0] <= start.year <= end.year <= YEARS[1]:
        return None
    return m.group(1), start, end


def earliest_unposted(state):
    """With no posted run yet: the earliest `from` of a pending, superseded or incomplete scheduled run."""
    starts = [period_bounds(r["period_key"])[1] for r in state["runs"] if r["status"] not in DONE_RUN_STATUSES]
    return min(starts) if starts else None


def cadence_slot(cadence, day):
    """A weekly schedule posts once per Monday-Sunday week, a daily one once per day."""
    return day - timedelta(days=day.weekday()) if cadence == "weekly" else day


def posted_in_slot(state, cadence, day):
    """The confirmed (or empty) run of this cadence whose window ends in the same slot as day, if any (by window end,
    not run time: a digest resumed and posted on Monday still belongs to the week it covered)."""
    slot = cadence_slot(cadence, day)
    done = [r for r in state["runs"] if r["status"] in DONE_RUN_STATUSES and period_bounds(r["period_key"])[0] == cadence
            and cadence_slot(cadence, period_bounds(r["period_key"])[2]) == slot]
    return max(done, key=lambda r: instant(r["as_of"])) if done else None


# --------------------------------------------------------------------------- merge


class Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def dedupe_key(direction, owner, counterpart, deliverable):
    return "|".join([direction, norm_owner(owner), norm_owner(counterpart or ""), norm_deliverable(deliverable)])


def merge(bundle, state, config, scope, cal, label, today, stamp):
    """stamp (the run's as-of in UTC) orders your replies: a later reply wins over an earlier one on the same day."""
    meetings = bundle["meetings"]
    items = state["commitments"]
    r = Reader(label)
    stats = {"soft": [], "out_of_scope": 0, "merged_repeats": 0, "touched": set()}

    # Replies that reopen an item also go first, so a promise repeated in this bundle joins the reopened item
    # instead of starting a second open one.
    for a in bundle["actions"]:
        if a["action"] in ("reopen", "restore") and a["commitment_id"] in items:
            apply_action(items[a["commitment_id"]], a, stamp, r, today)

    # Every mention already tracked, so a re-extraction finds its item even when that item's id came from
    # another mention, a merge, or the state_version 1 id scheme (which had no deliverable in the id).
    mention_index = {}
    quote_index = {}
    for cid, rec in sorted(items.items()):
        for x in rec["mentions"]:
            mention_index.setdefault((x["meeting_id"], norm_text(x["quote"]), norm_owner(rec["owner"]),
                                      rec["dedupe_key"]), cid)
            quote_index.setdefault((x["meeting_id"], norm_text(x["quote"]), norm_owner(rec["owner"])), set()).add(cid)
    # Fallback for a re-extraction whose deliverable was worded differently (e.g. in the one-day window overlap):
    # same meeting + same verbatim quote + same owner is the same promise, unless that sentence holds two of them.
    quote_count = {}
    for c in bundle["commitments"]:
        if c["kind"] == "commitment":
            qkey = (c["meeting_id"], norm_text(c["quote"]), norm_owner(c["owner"]))
            quote_count[qkey] = quote_count.get(qkey, 0) + 1

    active = []
    for c in bundle["commitments"]:
        m = meetings[c["meeting_id"]]
        if scope == "external" and m["audience"] == "internal":
            stats["out_of_scope"] += 1
            continue
        if c["kind"] == "soft_mention":
            stats["soft"].append(c)
            continue
        counterpart = c["counterpart"] or m["account"] or ", ".join(m["external_domains"]) or None
        c["counterpart_resolved"] = counterpart
        c["key"] = dedupe_key(c["direction"], c["owner"], counterpart, c["deliverable"])
        c["raw_id"] = commitment_id(c["meeting_id"], c["quote"], c["owner"], c["deliverable"])
        c["tracked_id"] = c["raw_id"] if c["raw_id"] in items else mention_index.get(
            (c["meeting_id"], norm_text(c["quote"]), norm_owner(c["owner"]), c["key"]))
        qkey = (c["meeting_id"], norm_text(c["quote"]), norm_owner(c["owner"]))
        if c["tracked_id"] is None and len(quote_index.get(qkey, ())) == 1 and quote_count[qkey] == 1:
            c["tracked_id"] = next(iter(quote_index[qkey]))
        if c["known_id"]:
            if c["known_id"] not in items:
                raise r.fail("$.commitments[%d].known_id" % c["index"], "%r is not in the state file" % c["known_id"])
            known = items[c["known_id"]]
            if is_closed(known, today) and all(x["meeting_id"] != c["meeting_id"] for x in known["mentions"]) \
                    and not open_on(known, iso(m["day"]), today):
                raise r.fail("$.commitments[%d].known_id" % c["index"], "%r is done or dismissed; a new promise "
                             "becomes a new item, so leave known_id out (or reopen the old item first)" % c["known_id"])
        c["due_resolved"] = resolve_due(c["due"], m["day"], cal)
        active.append(c)

    uf = Union()
    key_owner = {}
    open_items = {}
    closed_key = {}
    closed_items = {}
    # Oldest first, so with two open items of the same wording (one reopened after it was promised again) a repeat
    # joins the newest one.
    for cid, rec in sorted(items.items(), key=lambda kv: (instant(kv[1]["origin"]["start"]), kv[0])):
        node = "s:" + cid
        uf.find(node)
        if is_closed(rec, today):
            closed_items.setdefault(rec["dedupe_key"], []).append(cid)
            prior = closed_key.get(rec["dedupe_key"])
            if prior is None or instant(items[prior]["origin"]["start"]) <= instant(rec["origin"]["start"]):
                closed_key[rec["dedupe_key"]] = cid
        else:
            key_owner[rec["dedupe_key"]] = node
            open_items.setdefault(rec["dedupe_key"], []).append(cid)
    refs_active = {c["ref"]: c for c in active}
    for c in active:
        node = "b:" + c["ref"]
        uf.find(node)
        for cid in [c["known_id"], c["tracked_id"]]:
            if cid:
                uf.join(node, "s:" + cid)
        if not c["known_id"] and not c["tracked_id"]:
            # A repeat merges only into an item that was open when it was said: an open item, or a done or dismissed
            # one that closed on or after that day. A promise made after an item closed is a new item.
            # A re-extraction from a meeting the closed item already cites is that promise again, whatever the dates.
            # Either way, an item that already cites this meeting comes first: it is that promise read again.
            day = iso(meetings[c["meeting_id"]]["day"])
            closed = closed_items.get(c["key"], [])
            cites = [cid for cid in open_items.get(c["key"], []) + closed
                     if any(x["meeting_id"] == c["meeting_id"] for x in items[cid]["mentions"])]
            was_open = cites or [cid for cid in closed if open_on(items[cid], day, today)]
            # A promise read late (e.g. a meeting re-read after it was given up) can't repeat an item made after it.
            made_by = [cid for cid in open_items.get(c["key"], []) if items[cid]["origin"]["day"] <= day]
            if was_open:
                uf.join(node, "s:" + was_open[0])
            elif made_by:
                uf.join(node, "s:" + made_by[-1])
            elif c["key"] in key_owner:
                uf.join(node, key_owner[c["key"]])
            else:
                key_owner[c["key"]] = node
        if c["same_as"] and c["same_as"] in refs_active:
            uf.join(node, "b:" + c["same_as"])
    for c in active:
        for other in active:
            if other is not c and other["raw_id"] == c["raw_id"]:
                uf.join("b:" + c["ref"], "b:" + other["ref"])

    groups = {}
    for node in list(uf.parent):
        groups.setdefault(uf.find(node), []).append(node)

    alias = {}
    for members in groups.values():
        state_ids = sorted(n[2:] for n in members if n.startswith("s:"))
        bundle_members = sorted((refs_active[n[2:]] for n in members if n.startswith("b:")),
                                key=lambda c: (meetings[c["meeting_id"]]["start"], c["index"]))
        if len({is_closed(items[cid], today) for cid in state_ids}) > 1:
            raise r.fail("$.commitments[%d]" % bundle_members[0]["index"], "would merge a done or dismissed item "
                         "with an open one (%s); remove same_as/known_id or reopen the old item first"
                         % ", ".join(state_ids))
        if state_ids:
            state_ids.sort(key=lambda cid: (instant(items[cid]["origin"]["start"]), cid))
            canonical = state_ids[0]
            record = items[canonical]
            for other in state_ids[1:]:
                absorb(record, items.pop(other))
                alias[other] = canonical
        else:
            first = bundle_members[0]
            canonical = first["raw_id"]
            record = new_record(canonical, first, meetings[first["meeting_id"]])
            record["earlier"] = closed_key.get(first["key"])
            items[canonical] = record
        for c in bundle_members:
            alias[c["raw_id"]] = canonical
            if c["known_id"]:
                alias[c["known_id"]] = canonical
            c["canonical"] = canonical
            stats["touched"].add(canonical)
            add_mention(record, c, meetings[c["meeting_id"]])
        if len({instant(m["start"]) for m in record["mentions"]}) > 1 and bundle_members:
            stats["merged_repeats"] += 1
        recompute_due(record)

    for e in bundle["evidence"]:
        if e["ref"]:
            if e["ref"] not in refs_active:
                raise r.fail("$.evidence[%d].ref" % e["index"], "%r was excluded from this run (scope %s)"
                             % (e["ref"], scope))
            cid = refs_active[e["ref"]]["canonical"]
        else:
            cid = alias.get(e["commitment_id"], e["commitment_id"])
            if cid not in items:
                raise r.fail("$.evidence[%d].commitment_id" % e["index"], "%r is not in the state file or this bundle"
                             % e["commitment_id"])
        record = items[cid]
        stats["touched"].add(cid)
        made = to_day(record["origin"]["day"])
        if e["date"] < made:
            raise r.fail("$.evidence[%d].date" % e["index"], "%s is before the commitment was made (%s)"
                         % (e["date"], made))
        add_evidence(record, {"kind": e["kind"], "strength": e["strength"], "date": iso(e["date"]),
                              "url": e["url"], "summary": e["summary"], "why": e["why"]})

    # Every reply is applied here in bundle order (re-applying an early reopen or restore changes nothing), so
    # "dismiss, then restore" ends restored and "restore, then dismiss" ends dismissed.
    for a in bundle["actions"]:
        cid = alias.get(a["commitment_id"], a["commitment_id"])
        if cid not in items:
            raise r.fail("$.user_actions[%d].commitment_id" % a["index"], "%r is not a tracked commitment"
                         % a["commitment_id"])
        stats["touched"].add(cid)
        apply_action(items[cid], a, stamp, r, today)

    # Anything no longer done ("still waiting" evidence, a reopen) is open again: off the reported-done list,
    # back in --list-open, and not prunable as a done item.
    for rec in items.values():
        if rec.get("done_reported") and evaluate(rec, today, 1)[0] not in ("done", "dismissed"):
            rec["done_reported"] = None
    return stats


def apply_action(record, a, stamp, r, today):
    order = "%s#%04d" % (stamp, a["index"])
    if a["action"] == "dismiss":
        record["dismissed"] = {"date": iso(a["date"]), "note": a["note"]}
    elif a["action"] == "restore":
        record["dismissed"] = None
        record["restored_on"] = iso(a["date"])  # counts as activity, so pruning doesn't drop it again at once
    elif a["action"] == "set_due":
        made = to_day(record["origin"]["day"])
        if a["due_date"] < made:
            raise r.fail("$.user_actions[%d].due_date" % a["index"], "%s is before the commitment was made (%s)"
                         % (a["due_date"], made))
        record["due_override"] = {"date": iso(a["due_date"]), "set_on": iso(a["date"]), "note": a["note"]}
        recompute_due(record)
    elif a["action"] == "reopen":
        add_evidence(record, {"kind": "user", "strength": "against", "date": iso(a["date"]), "url": None,
                              "summary": a["note"] or "reopened by you", "why": None, "order": order})
        if evaluate(record, today, 1)[0] != "done":
            record["done_reported"] = None
    else:
        add_evidence(record, {"kind": "user", "strength": "strong", "date": iso(a["date"]), "url": None,
                              "summary": a["note"] or "marked done by you", "why": None, "order": order})


def new_record(cid, c, meeting):
    return {
        "id": cid, "direction": c["direction"], "owner": c["owner"], "owner_email": c["owner_email"],
        "counterpart": c["counterpart_resolved"], "counterpart_email": c["counterpart_email"],
        "deliverable": c["deliverable"], "dedupe_key": c["key"],
        "origin": mention_meeting(meeting), "mentions": [], "due": None, "evidence": [],
        "dismissed": None, "due_override": None, "first_reported": None, "done_reported": None,
        "last_status": None, "earlier": None,
    }


def mention_meeting(meeting):
    return {"meeting_id": meeting["meeting_id"], "title": meeting["title"], "start": meeting["start"].isoformat(),
            "day": iso(meeting["day"]), "url": meeting["url"]}


def add_mention(record, c, meeting):
    due = c["due_resolved"]
    entry = dict(mention_meeting(meeting))
    entry.update({"quote": c["quote"], "quote_source": c["quote_source"],
                  "due": {"date": iso(due["date"]), "kind": due["kind"], "phrase": due["phrase"], "note": due["note"]}})
    before = [m for m in record["mentions"] if m["meeting_id"] == meeting["meeting_id"]]
    if before:  # a re-extraction keeps the quote first stored, so later re-extractions can still find it by quote
        entry.update({"quote": before[0]["quote"], "quote_source": before[0]["quote_source"]})
    record["mentions"] = [m for m in record["mentions"] if m["meeting_id"] != meeting["meeting_id"]] + [entry]
    record["mentions"].sort(key=lambda m: (instant(m["start"]), m["meeting_id"]))
    if instant(entry["start"]) < instant(record["origin"]["start"]):
        record["origin"] = mention_meeting(meeting)
    if not record.get("counterpart_email") and c.get("counterpart_email"):
        record["counterpart_email"] = c["counterpart_email"]
    if not record.get("owner_email") and c.get("owner_email"):
        record["owner_email"] = c["owner_email"]


def absorb(record, other):
    for m in other["mentions"]:
        if all(m["meeting_id"] != x["meeting_id"] for x in record["mentions"]):
            record["mentions"].append(m)
    record["mentions"].sort(key=lambda m: (instant(m["start"]), m["meeting_id"]))
    if instant(other["origin"]["start"]) < instant(record["origin"]["start"]):
        record["origin"] = other["origin"]
    for e in other["evidence"]:
        add_evidence(record, e)
    record["dismissed"] = record["dismissed"] or other["dismissed"]
    record["first_reported"] = record["first_reported"] or other["first_reported"]
    if (other.get("restored_on") or "") > (record.get("restored_on") or ""):
        record["restored_on"] = other["restored_on"]


def evidence_rank(e):
    """Order of evidence: its date, then (for your replies) the run and position the reply was applied in."""
    return (e["date"], e.get("order") or "")


def add_evidence(record, entry):
    key = (entry["kind"], entry["strength"], entry["date"], entry["url"], entry["summary"])
    same = [e for e in record["evidence"] if (e["kind"], e["strength"], e["date"], e["url"], e["summary"]) == key]
    if not same:
        record["evidence"].append(entry)
    elif (entry.get("order") or "") > (same[0].get("order") or ""):
        same[0]["order"] = entry["order"]  # the same reply given again counts from its latest position
    record["evidence"].sort(key=lambda e: evidence_rank(e) + (e["kind"], e["url"] or "", e["summary"]))


def recompute_due(record):
    dated = [m for m in record["mentions"] if m["due"]["date"]]
    if dated:
        current = dated[-1]
        due = dict(current["due"])
        earlier = [m for m in dated if instant(m["start"]) < instant(current["start"]) and m["due"]["date"] != due["date"]]
        due["was"] = earlier[0]["due"]["date"] if earlier else None
        due["repromised_in"] = current["title"] if earlier else None
        due["repromised_day"] = current["day"] if earlier else None
    else:
        due = dict(record["mentions"][-1]["due"])
        due.update({"was": None, "repromised_in": None, "repromised_day": None})
    override = record.get("due_override")
    if override:
        due = {"date": override["date"], "kind": "set_by_user", "phrase": None, "note": None, "was": None,
               "repromised_in": None, "repromised_day": None, "set_on": override["set_on"]}
    record["due"] = due


# --------------------------------------------------------------------------- status


def latest_promise(record):
    return max(m["day"] for m in record["mentions"])


def evaluate(record, today, due_soon_days):
    if record.get("dismissed"):
        return "dismissed", None
    # A promise repeated after some evidence means that evidence did not deliver it: only evidence dated on or after
    # the latest promise counts towards done or likely done.
    since = latest_promise(record)
    evidence = [e for e in record["evidence"] if e["date"] >= since or e["strength"] == "against"]
    strong = [e for e in evidence if e["strength"] == "strong"]
    weak = [e for e in evidence if e["strength"] == "weak"]
    against = [e for e in evidence if e["strength"] == "against"]
    # Same-day ties: "against" wins over found evidence; between your own replies the later one wins.
    latest_against = max([evidence_rank(e) for e in against], default=None)
    if strong:
        last_strong = max(evidence_rank(e) for e in strong)
        if latest_against is None or latest_against < last_strong:
            done_on = min(e["date"] for e in strong if latest_against is None or evidence_rank(e) > latest_against)
            return "done", done_on
    likely = None
    if weak:
        last_weak = max(evidence_rank(e) for e in weak)
        if latest_against is None or latest_against < last_weak:
            likely = "likely_done"
    due = to_day(record["due"]["date"])
    if due is None:
        bucket = "no_date"
    elif due < today:
        bucket = "overdue"
    elif (due - today).days <= due_soon_days:
        bucket = "due_soon"
    else:
        bucket = "later"
    return bucket, likely


# --------------------------------------------------------------------------- rendering


def short_day(d):
    return "%s %s %d" % (WEEKDAYS[d.weekday()], MONTHS[d.month - 1], d.day)


def window_text(start, end):
    if start == end:
        return "%s, %d" % (short_day(start), start.year)
    if start.year == end.year:
        return "%s to %s, %d" % (short_day(start), short_day(end), end.year)
    return "%s, %d to %s, %d" % (short_day(start), start.year, short_day(end), end.year)


def plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


def provider_label(provider):
    return PROVIDER_LABELS.get(provider or "", provider or "unknown")


def clip_quote(quote, limit):
    quote = " ".join(quote.split())
    if len(quote) <= limit:
        return quote
    cut = quote[:limit].rsplit(" ", 1)[0]
    return cut + " …"


def due_text(f, due, today):
    d = to_day(due["date"])
    phrase = f.text(due.get("phrase") or "")
    if d is not None:
        text = "due %s" % short_day(d)
        if due["kind"] == "inferred":
            text += " (inferred from “%s”)" % phrase
        elif due["kind"] == "set_by_user":
            text += " (set by you %s)" % short_day(to_day(due["set_on"]))
        elif due.get("note") == "year inferred":
            text += " (year inferred from “%s”)" % phrase
        if due.get("was"):
            text += " · re-promised %s, was due %s" % (short_day(to_day(due["repromised_day"])),
                                                           short_day(to_day(due["was"])))
        return text
    if due["kind"] == "ambiguous":
        return "date unclear (“%s” %s) — confirm" % (phrase, f.text(due.get("note") or ""))
    if phrase and due.get("note") == FAR_NOTE:
        return "no firm date (“%s” is %s — confirm)" % (phrase, FAR_NOTE)
    if phrase:
        return "no firm date (“%s”)" % phrase
    return "no date"


def lateness(f, due, today):
    d = to_day(due["date"])
    if d is None:
        return None
    days = (today - d).days
    if days > 0:
        return f.bold("%s overdue" % plural(days, "day"))
    if days == 0:
        return "due today"
    return None


QUOTE_SOURCE_LABEL = {"transcript": "transcript", "notes": "your notes", "summary": "Granola AI summary, not verbatim speech"}


def quote_line(f, record, limit):
    origin_mentions = record["mentions"]
    first = origin_mentions[0]
    line = "   > “%s” (%s) — %s, %s" % (
        f.text(clip_quote(first["quote"], limit)), QUOTE_SOURCE_LABEL[first["quote_source"]],
        f.link(first["title"], first["url"]), short_day(to_day(first["day"])))
    lines = [line]
    for m in origin_mentions[1:]:
        if instant(m["start"]) == instant(first["start"]):
            continue
        lines.append("   Repeated in %s, %s: “%s” (%s)" % (
            f.link(m["title"], m["url"]), short_day(to_day(m["day"])), f.text(clip_quote(m["quote"], limit)),
            QUOTE_SOURCE_LABEL[m["quote_source"]]))
    return lines


def evidence_text(f, e):
    if e["kind"] == "user":
        return "%s (%s)" % (f.text(e["summary"]), short_day(to_day(e["date"])))
    return "%s (%s)" % (f.link(e["summary"], e["url"]), short_day(to_day(e["date"])))


def checked_sources(f, sources, direction):
    names = []
    for key in ["email", "slack", "tracker", "calendar"]:
        s = sources[key]
        if s["status"] == "ok":
            label = f.text(provider_label(s["provider"]))
            if key == "email":
                label += " (sent mail)" if direction == "we_owe" else " (received mail)"
            names.append(label)
    if sources["meetings"]["status"] == "ok":
        names.append("later %s meetings" % f.text(provider_label(sources["meetings"]["provider"])))
    return names


def item_lines(f, n, record, info, ctx, they=False):
    today = ctx["today"]
    who = f.text(record["owner"])
    counterpart = f.text(record["counterpart"] or "internal")
    if they:
        head = "%d. %s · %s (%s) owes us" % (n, f.bold(f.text(record["deliverable"])), who, counterpart)
    else:
        head = "%d. %s · %s → %s" % (n, f.bold(f.text(record["deliverable"])), who, counterpart)
    parts = [due_text(f, record["due"], today)]
    late = lateness(f, record["due"], today) if info["bucket"] in ("overdue", "due_soon") else None
    if late:
        parts.append(late)
    if info["stale_days"] is not None:
        parts.append("open %d days — still on your radar?" % info["stale_days"])
    if info["new"]:
        parts.append(f.bold("new"))
    earlier = ctx["all_items"].get(record.get("earlier") or "")
    if earlier is not None:
        parts.append("promised again; the earlier `%s` was %s" % (
            earlier["id"], "dismissed" if earlier.get("dismissed") else "done"))
    lines = [head + " · " + " · ".join(parts)]
    lines += quote_line(f, record, ctx["config"]["max_quote_chars"])
    against = [e for e in record["evidence"] if e["strength"] == "against"]
    weak = [e for e in record["evidence"] if e["strength"] == "weak"]
    if info["likely"]:
        e = weak[-1]
        lines.append("   Likely done: %s — %s Confirm, or tell me it's still open." % (evidence_text(f, e),
                                                                                       f.text(e["why"] or "")))
    elif against and against[-1]["kind"] == "user":
        lines.append("   Reopened by you: %s." % evidence_text(f, against[-1]))
    elif against:
        lines.append("   Still outstanding per %s." % evidence_text(f, against[-1]))
    elif any(e["date"] < latest_promise(record) for e in record["evidence"]):
        older = [e for e in record["evidence"] if e["date"] < latest_promise(record)][-1]
        lines.append("   Not counted: %s is older than the latest promise (%s)." % (
            evidence_text(f, older), short_day(to_day(latest_promise(record)))))
    else:
        checked = checked_sources(f, ctx["bundle"]["sources"], record["direction"])
        if checked:
            lines.append("   No completion evidence in %s." % ", ".join(checked))
        else:
            lines.append("   No completion sources could be checked.")
    lines.append("   `%s`" % record["id"])
    return lines


def render(ctx):
    f = ctx["fmt"]
    b = ctx["bundle"]
    today = ctx["today"]
    limit = ctx["config"]["max_quote_chars"]
    g = ctx["groups"]
    scan = b["scan"]
    lines = []
    lines.append(f.heading(2, "Commitments — %s" % window_text(scan["from"], scan["to"])))
    offset = ctx["as_of"].strftime("%z")
    meta = "As of %s %s UTC%s:%s" % (short_day(today), ctx["as_of"].strftime("%H:%M"), offset[:3], offset[3:])
    meta += " · %s run" % ("scheduled %s" % scan["cadence"] if ctx["scheduled"] else "on-request")
    if ctx["owners"]:
        meta += " · our side filtered to: %s" % ", ".join(f.text(o) for o in ctx["owners"])
    lines.append(meta)
    if ctx["incomplete"]:
        warning = "Incomplete: " + " Also, ".join(ctx["incomplete"])
        if ctx["scheduled"]:
            warning += " This period was not recorded; the next run's window covers it again."
        lines.append(f.bold(warning))
    lines.append("")
    summary = [plural(len(g["overdue"]), "overdue", "overdue"),
               "%d due in the next %s" % (len(g["due_soon"]), plural(ctx["due_soon_days"], "day")),
               "%d open with no date" % len(g["no_date"]),
               "%d they owe us" % len(g["they"]),
               "%d done since last run" % len(g["done"])]
    if g["later"]:
        summary.insert(2, "%d due later" % len(g["later"]))
    lines.append(f.bold(" · ".join(summary)))

    def section(title, key, they=False, empty="None."):
        lines.append("")
        lines.append(f.heading(3, "%s (%d)" % (title, len(g[key]))))
        if not g[key]:
            lines.append(empty)
            return
        for n, (record, info) in enumerate(g[key], 1):
            lines.extend(item_lines(f, n, record, info, ctx, they))

    section("Overdue", "overdue")
    section("Due in the next %s" % plural(ctx["due_soon_days"], "day"), "due_soon")
    if g["later"]:
        section("Due later", "later")
    section("Open, no date", "no_date")
    section("They owe us", "they", they=True, empty="Nothing outstanding from the other side.")

    lines.append("")
    lines.append(f.heading(3, "Done since last run (%d)" % len(g["done"])))
    if not g["done"]:
        lines.append("None.")
    for record, info in g["done"]:
        strong = [e for e in record["evidence"] if e["strength"] == "strong" and e["date"] >= info["done_on"]]
        owner, counterpart = f.text(record["owner"]), f.text(record["counterpart"] or "internal")
        arrow = "%s (%s)" % (owner, counterpart) if record["direction"] == "they_owe" \
            else "%s → %s" % (owner, counterpart)
        lines.append("- ✓ %s · %s · %s · `%s`" % (
            f.bold(f.text(record["deliverable"])), arrow, evidence_text(f, strong[0]), record["id"]))

    if ctx["show_soft"]:
        lines.append("")
        lines.append(f.heading(3, "Soft mentions (%d, not tracked)" % len(ctx["soft"])))
        if not ctx["soft"]:
            lines.append("None.")
        for c in ctx["soft"]:
            m = b["meetings"][c["meeting_id"]]
            lines.append("- “%s” — %s, %s" % (f.text(clip_quote(c["quote"], limit)), f.link(m["title"], m["url"]),
                                                         short_day(m["day"])))

    lines.append("")
    lines.extend(coverage_lines(ctx))

    nudges = ctx["nudges"]
    lines.append("")
    if nudges:
        ours = sum(1 for n in nudges if n["direction"] == "we_owe")
        theirs = len(nudges) - ours
        bits = []
        if ours:
            bits.append("%s on our side (status update or delivery)" % plural(ours, "overdue item"))
        if theirs:
            bits.append("%s from the other side (polite nudge)" % plural(theirs, "overdue item"))
        lines.append(f.italic("Follow-up drafts available for %s. Say “draft the nudges” to review them. "
                              "Nothing is drafted in Gmail or sent without your OK." % " and ".join(bits)))
    lines.append("Period key: `%s` · %s tracked in state." % (ctx["period_key"], plural(ctx["tracked"], "commitment")))
    return "\n".join(lines)


def coverage_lines(ctx):
    f = ctx["fmt"]
    b = ctx["bundle"]
    sources = b["sources"]
    meetings = [b["meetings"][mid] for mid in b["meeting_order"]]
    scan = b["scan"]
    src = sources["meetings"]
    out = [f.heading(3, "Coverage")]
    window = "- Window: %s (%s)" % (window_text(scan["from"], scan["to"]), scan["cadence"].replace("_", "-"))
    window += " · Source: %s" % f.text(provider_label(src["provider"]))
    if src["status"] != "ok":
        window += " — %s%s" % (f.bold(src["status"].replace("_", " ")), ": " + f.text(src["detail"]) if src["detail"] else "")
    out.append(window)
    gap = ctx["gap"]
    limit = history_limit(src, ctx["config"])
    if gap is not None:
        text = "- Gap: %s, but this window starts %s, so meetings from %s were not scanned." % (
            ("the window after the last posted digest starts %s" if gap["posted"] else
             "no digest has been confirmed posted yet; the earliest unposted window started %s")
            % short_day(gap["from"]), short_day(scan["from"]), window_text(gap["from"], scan["from"] - timedelta(days=1)))
        earliest = ctx["today"] - timedelta(days=limit) if limit is not None else None
        if earliest is not None and gap["from"] < earliest:
            # A window starting before the note history would be Incomplete (exit 5): those days are lost for good.
            history = "%s%d-day note history" % ("%s plan's " % f.text(src["plan"]) if src["plan"] else "", limit)
            if earliest < scan["from"]:
                text += " Meetings before %s are older than the %s and can't be recovered; start the window on %s " \
                        "to scan the rest (Step 2)." % (short_day(earliest), history, short_day(earliest))
            else:
                text += " They are older than the %s (it reaches back to %s), so they can't be recovered." % (
                    history, short_day(earliest))
        else:
            text += " Start the window on %s (Step 2)." % short_day(gap["from"])
        out.append(text)
    if limit is not None:
        earliest = ctx["today"] - timedelta(days=limit)
        plan = "%s plan, " % f.text(src["plan"]) if src["plan"] else ""
        if scan["from"] < earliest:
            out.append("- History limit: %s — meetings before %s may be missing from this scan. Upgrade the plan or "
                       "narrow the window." % (f.bold("%s%d-day note history" % (plan, limit)), short_day(earliest)))
        else:
            out.append("- History limit: %s%d-day note history; this window is inside it." % (plan, limit))
    elif src["plan"]:
        out.append("- History limit: %s plan, no history limit known." % f.text(src["plan"]))
    ext = [m for m in meetings if m["audience"] == "external"]
    internal = [m for m in meetings if m["audience"] == "internal"]
    unknown = [m for m in meetings if m["audience"] == "unknown"]
    text = "- Meetings scanned: %d — %d external, %d internal" % (len(meetings), len(ext), len(internal))
    if unknown:
        text += ", %d with no attendee list (treated as external)" % len(unknown)
    if internal:
        text += " (internal-only meetings %s; scope: %s)" % (
            "excluded" if ctx["scope"] == "external" else "included", ctx["scope"])
    overridden = [m for m in meetings if m["audience_overridden"]]
    if overridden:
        text += "; %s set by hand because attendee metadata was incomplete" % plural(len(overridden), "audience")
    out.append(text)
    dup = duplicate_note_count(meetings)
    if dup:
        out.append("- Duplicate notes: %s of the same meeting (captured by more than one person); "
                   "their commitments were merged." % plural(dup, "extra note"))
    if meetings:
        notes_ok = sum(1 for m in meetings if m["notes_status"] == "ok")
        t = {}
        for m in meetings:
            t.setdefault(m["transcript_status"], []).append(m)
        text = "- Notes read: %d/%d · Transcripts: %d read" % (notes_ok, len(meetings), len(t.get("ok", [])))
        for status, label in [("denied", "denied"), ("unavailable", "unavailable"), ("not_requested", "not requested")]:
            if t.get(status):
                text += ", %d %s" % (len(t[status]), label)
        out.append(text)
        for status in ["denied", "unavailable"]:
            for m in t.get(status, []):
                out.append("  - Transcript %s: %s, %s — quotes come from the notes; a commitment the notes "
                           "omit would be missed." % (status, f.link(m["title"], m["url"]), short_day(m["day"])))
        for m in meetings:
            if m["notes_status"] != "ok":
                out.append("  - Notes %s: %s, %s — %s" % (
                    m["notes_status"], f.link(m["title"], m["url"]), short_day(m["day"]),
                    "read from the transcript only." if m["transcript_status"] == "ok" else "not read at all."))
    tracking = ctx["tracking"]
    if ctx["scheduled"]:
        budget = ctx["config"]["unreadable_retry_runs"] + 1
        for x in tracking["retrying"]:
            out.append("  - Read failed: %s, %s — attempt %d of up to %d%s; the next scheduled run retries it." % (
                f.link(x["title"], x["url"]), short_day(to_day(x["day"])), x["attempts"], budget,
                " (not in this run's meeting list)" if x["missing"] else ""))
    for x in tracking["not_covered"]:
        out.append("- Meeting %s, %s could not be read after %s — not covered: promises made in it are missing from "
                   "this digest%s." % (f.text(x["title"]), short_day(to_day(x["day"])), plural(x["attempts"], "attempt"),
                                       "" if not ctx["scheduled"] else "; it is not retried, but re-read if it "
                                       "becomes readable (--next-window lists it under reread)"))
    for x in tracking["read_again"]:
        out.append("- Meeting %s, %s was read again (listed as not covered since %s); its promises are included." % (
            f.text(x["title"]), short_day(to_day(x["day"])), short_day(instant(x["given_up"]).date())))
    checked = []
    gaps = []
    for key in ["email", "slack", "tracker", "calendar"]:
        s = sources[key]
        label = f.text(provider_label(s["provider"]) if s["provider"] else SOURCE_LABELS[key])
        if s["status"] == "ok":
            checked.append(label + " ✓")
        else:
            gaps.append("%s: %s%s" % (label, s["status"].replace("_", " "),
                                       " (%s)" % f.text(s["detail"]) if s["detail"] else ""))
    if src["status"] == "ok":
        checked.append("later %s meetings ✓" % f.text(provider_label(src["provider"])))
    out.append("- Completion evidence checked: %s" % (" · ".join(checked) if checked else "none"))
    out.append("- Gaps: %s" % ("; ".join(gaps) + "." if gaps else "none."))
    if b["url_warnings"]:
        out.append("- Links dropped: %s (only absolute http/https links are shown): %s." % (
            plural(len(b["url_warnings"]), "link"), ", ".join("`%s`" % w for w in b["url_warnings"])))
    notes = []
    if ctx["superseded"]:
        notes.append("the digest for %s was never confirmed posted, so its items are repeated here"
                     % ", ".join("`%s`" % k for k in ctx["superseded"]))
    if ctx["soft"] and not ctx["show_soft"]:
        notes.append("%s not listed (vague, no clear owner or deliverable)" % plural(len(ctx["soft"]), "soft mention"))
    if ctx["stats"]["out_of_scope"]:
        notes.append("%s from internal-only meetings skipped" % plural(ctx["stats"]["out_of_scope"], "commitment"))
    if ctx["dismissed"]:
        notes.append("%d dismissed by you (hidden)" % ctx["dismissed"])
    if ctx["filtered"]:
        notes.append("%d of ours hidden by the owner filter" % ctx["filtered"])
    if ctx["carried"]:
        notes.append("%s carried over from earlier runs" % plural(ctx["carried"], "item"))
    if ctx["stats"]["merged_repeats"]:
        notes.append("%s repeated across meetings, merged" % plural(ctx["stats"]["merged_repeats"], "commitment"))
    if ctx["pruned_open"]:
        notes.append("%s open more than %d days with no new mention dropped from tracking" % (
            plural(ctx["pruned_open"], "item"), ctx["config"]["prune_open_after_days"]))
    if notes:
        out.append("- Also: " + " · ".join(notes) + ".")
    return out


def history_limit(src, config):
    """Days of note history the meeting source exposes: history_limit_days, else the plan's entry in the config."""
    if src["history_limit_days"] is not None:
        return src["history_limit_days"]
    return config["plan_history_days"].get(src["plan"].lower()) if src["plan"] else None


def duplicate_note_count(meetings):
    seen = {}
    for m in meetings:
        key = (norm_text(m["title"]), m["start"])
        seen[key] = seen.get(key, 0) + 1
    return sum(n - 1 for n in seen.values())


# --------------------------------------------------------------------------- main


def track_unread(state, bundle, config, as_of, scheduled):
    """Bounded retry for meetings whose read failed (unavailable). A scheduled run counts one attempt per cadence slot
    for each such meeting (and for one that failed before and is missing from a bundle whose window holds its day).
    While a meeting is within unreadable_retry_runs retries and unreadable_retry_days days of its first failure it
    makes the run Incomplete; after that it is given up: listed as not covered, recorded in state so it never blocks a
    run again, and offered for re-reading (--next-window) until reread_until. A tracked meeting that reads is dropped
    from tracking (read_again when it had been given up). On-request runs only report, they change nothing here."""
    today = as_of.date()
    scan = bundle["scan"]
    tracked = state["unread_meetings"]
    meetings = bundle["meetings"]
    src = bundle["sources"]["meetings"]
    out = {"retrying": [], "not_covered": [], "read_again": [], "settled": []}

    def info(mid, rec, missing=False):
        return {"meeting_id": mid, "title": rec["title"], "day": rec["day"], "url": rec["url"],
                "attempts": rec["attempts"], "missing": missing, "given_up": rec["given_up"]}

    failed = [m for m in meetings.values() if m["notes_status"] != "ok" and m["transcript_status"] != "ok"
              and "unavailable" in (m["notes_status"], m["transcript_status"])]
    if not scheduled:
        for m in failed:
            rec = tracked.get(m["meeting_id"]) or {"title": m["title"], "day": iso(m["day"]), "url": m["url"],
                                                   "attempts": 0, "given_up": None}
            out["not_covered" if rec["given_up"] else "retrying"].append(info(m["meeting_id"], rec))
        return out
    slot = "%s:%s" % (scan["cadence"], iso(cadence_slot(scan["cadence"], today)))
    stamp = as_of.isoformat()
    limit = history_limit(src, config)
    keep_days = limit if limit is not None else config["prune_after_days"]
    missing = [mid for mid in sorted(tracked) if mid not in meetings and src["status"] == "ok"
               and scan["from"] <= to_day(tracked[mid]["day"]) <= scan["to"]]
    seen = set()
    for m, mid in [(m, m["meeting_id"]) for m in failed] + [(None, mid) for mid in missing]:
        rec = tracked.get(mid)
        if rec is None:
            rec = tracked[mid] = {"title": m["title"], "day": iso(m["day"]), "url": m["url"], "attempts": 0,
                                  "first_failed": stamp, "last_failed": stamp, "last_slot": slot, "given_up": None}
        if m is not None:
            rec.update({"title": m["title"], "day": iso(m["day"]), "url": m["url"]})
        if rec["attempts"] == 0 or rec["last_slot"] != slot:  # the schedule firing again in one slot is one attempt
            rec.update({"attempts": rec["attempts"] + 1, "last_slot": slot, "last_failed": stamp})
        rec["reread_until"] = iso(to_day(rec["day"]) + timedelta(days=keep_days))
        if rec["given_up"] is None and (rec["attempts"] > config["unreadable_retry_runs"] or (
                today - instant(rec["first_failed"]).date()).days >= config["unreadable_retry_days"]):
            rec["given_up"] = stamp
        out["not_covered" if rec["given_up"] else "retrying"].append(info(mid, rec, m is None))
        seen.add(mid)
    for mid in sorted(tracked):
        if mid in meetings and mid not in seen:  # read now (or denied: listed in Coverage as such)
            m = meetings[mid]
            if tracked[mid]["given_up"]:
                # Kept (and still offered for re-reading) until a digest that read it is confirmed posted: the
                # window has moved past it, so an unposted digest must not be the only one that listed it.
                out["settled"].append(mid)
                if m["notes_status"] == "ok" or m["transcript_status"] == "ok":
                    out["read_again"].append(info(mid, tracked[mid]))
            else:
                del tracked[mid]
        elif mid not in seen and to_day(tracked[mid]["reread_until"]) < today:
            del tracked[mid]
    return out


def parse_as_of(value):
    return parse_moment(value, lambda problem: InputError("--as-of: %s" % problem))


def build(args, state):
    as_of = parse_as_of(args.as_of)
    today = as_of.date()
    internal = sorted({d.strip().lower().lstrip("@") for d in args.internal_domain if d.strip()})
    if not internal:
        raise InputError("--internal-domain: at least one internal domain is required, otherwise our "
                         "commitments cannot be told apart from theirs")
    config = load_config(args.config)
    due_soon_days = args.due_soon_days if args.due_soon_days is not None else config["due_soon_days"]
    if not 1 <= due_soon_days <= MAX_DAYS:
        raise InputError("--due-soon-days: must be an integer from 1 to %d" % MAX_DAYS)
    bundle = load_bundle(args.bundle, internal)
    scan = bundle["scan"]
    if scan["from"] > today:
        raise InputError("bundle %s: $.scan.from: %s is after --as-of" % (args.bundle, scan["from"]))
    if args.scheduled and scan["cadence"] == "on_request":
        raise InputError("bundle %s: $.scan.cadence: on_request bundles cannot be run with --scheduled" % args.bundle)
    if args.scheduled and scan["to"] > today:
        raise InputError("bundle %s: $.scan.to: %s is after --as-of (%s); a scheduled window ends on the run's date "
                         "(take it from --next-window with the same --as-of)" % (args.bundle, scan["to"], today))
    period_key = "%s:%s..%s" % (scan["cadence"], scan["from"], scan["to"])
    # A skipped run changes nothing, so say so when the bundle carried the user's replies.
    unapplied = (" %s in this bundle (user_actions) %s not applied; apply %s with an on-request run." % (
        plural(len(bundle["actions"]), "reply", "replies"), "was" if len(bundle["actions"]) == 1 else "were",
        "it" if len(bundle["actions"]) == 1 else "them")) if bundle["actions"] else ""
    if args.scheduled:
        # A stale trigger (fired late, or replayed) must not stage an older period once a later run was posted.
        done = [r for r in state["runs"] if r["status"] in DONE_RUN_STATUSES]
        latest = max(done, key=lambda r: instant(r["as_of"])) if done else None
        resuming = args.resume_pending and any(r["period_key"] == period_key and r["status"] == "pending"
                                               for r in state["runs"])
        if latest is not None and as_of < instant(latest["as_of"]) and not resuming:
            raise InputError("--as-of %s is earlier than the last posted run (%s, as of %s); a stale scheduled run is "
                             "not staged. Post nothing: the next scheduled run, with the current time, covers this "
                             "window" % (as_of.isoformat(), latest["period_key"], latest["as_of"]))
        for run in state["runs"]:
            if run["period_key"] != period_key:
                continue
            if run["status"] in DONE_RUN_STATUSES:
                return {"status": "skipped", "period_key": period_key,
                        "reason": "already posted %s (as of %s); not posting it twice.%s" % (
                            period_key, run["as_of"], unapplied)}, None
            if run["status"] == "superseded":
                newer = [r["period_key"] for r in state["runs"] if r["status"] in ("pending",) + tuple(DONE_RUN_STATUSES)
                         and instant(r["as_of"]) > instant(run["as_of"])]
                raise InputError("%s was superseded by a later digest (%s) that repeated its items, so it is not staged "
                                 "again; run the current window from --next-window instead"
                                 % (period_key, ", ".join(newer) or "since replaced"))
            if run["status"] == "pending" and not args.resume_pending:
                raise StopRun(EXIT_PENDING, "pending: a digest for %s was produced at %s but not confirmed posted. "
                              "If it was posted, run --confirm-posted %s. If posting failed (or you are not sure, "
                              "check the destination first), rerun with --resume-pending to print it again, post it, "
                              "then confirm." % (period_key, run["as_of"], period_key))
        # A digest for the same week (or day), or a later one, that is still pending is being posted by another run,
        # or its post failed: it is confirmed or resumed, never superseded by a second digest for that slot or by an
        # older period.
        slot = cadence_slot(scan["cadence"], scan["to"])
        rivals = [r["period_key"] for r in state["runs"] if r["status"] == "pending" and r["period_key"] != period_key
                  and period_bounds(r["period_key"])[0] == scan["cadence"]
                  and cadence_slot(scan["cadence"], period_bounds(r["period_key"])[2]) >= slot]
        if rivals:
            raise StopRun(EXIT_PENDING, "pending: %s is still pending for the same or a later %s, so %s is not printed. "
                          "If it was posted, run --confirm-posted %s. If posting failed (check the destination first), "
                          "rerun that period with --resume-pending, post it, then confirm." % (
                              rivals[-1], "week" if scan["cadence"] == "weekly" else "day", period_key, rivals[-1]))
        # One digest per cadence slot: a second weekly run in the same Monday-Sunday week (or a second daily run on
        # the same day) is the schedule firing again, even though its window (from the last posted window) differs.
        posted = posted_in_slot(state, scan["cadence"], scan["to"])
        if posted is not None:
            return {"status": "skipped", "period_key": period_key,
                    "reason": "already posted %s (as of %s), the %s digest for %s; not posting %s as well.%s" % (
                        posted["period_key"], posted["as_of"], scan["cadence"],
                        ("the week of %s" % short_day(cadence_slot("weekly", scan["to"]))) if scan["cadence"] == "weekly"
                        else short_day(scan["to"]), period_key, unapplied)}, None

    for e in bundle["evidence"]:
        if e["date"] > today:
            raise InputError("bundle %s: $.evidence[%d].date: %s is after --as-of" % (args.bundle, e["index"], e["date"]))
    for a in bundle["actions"]:
        if a["date"] > today:
            raise InputError("bundle %s: $.user_actions[%d].date: %s is after --as-of" % (args.bundle, a["index"], a["date"]))
    label = "bundle %s" % args.bundle
    cal = Calendar(config)
    previously_tracked = set(state["commitments"])
    all_items = dict(state["commitments"])
    stats = merge(bundle, state, config, args.scope, cal, label, today,
                  as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    all_items.update(state["commitments"])
    pruned_open = prune_open(state, today, config, stats["touched"])
    owners = [norm_owner(o) for o in args.owner if o.strip()]

    groups = {"overdue": [], "due_soon": [], "later": [], "no_date": [], "they": [], "done": []}
    dismissed = filtered = carried = 0
    nudges = []
    shown_new, shown_done = [], []
    for cid in sorted(state["commitments"]):
        record = state["commitments"][cid]
        bucket, extra = evaluate(record, today, due_soon_days)
        likely = extra if bucket != "done" else None
        record["last_status"] = "dismissed" if bucket == "dismissed" else (
            "done" if bucket == "done" else ("likely_done" if likely else ("overdue" if bucket == "overdue" else "open")))
        if bucket == "dismissed":
            dismissed += 1
            continue
        ours = record["direction"] == "we_owe"
        if ours and owners and norm_owner(record["owner"]) not in owners and \
                (record.get("owner_email") or "") not in owners:
            filtered += 1
            continue
        if bucket == "done":
            if record.get("done_reported"):
                continue
            groups["done"].append((record, {"done_on": extra}))
            shown_done.append(cid)
            if not record.get("first_reported"):
                shown_new.append(cid)
            continue
        if cid in previously_tracked and record.get("first_reported"):
            carried += 1
        stale = None
        if bucket == "no_date":
            age = (today - to_day(record["origin"]["day"])).days
            if age >= config["stale_days"]:
                stale = age
        info = {"bucket": bucket, "likely": likely, "new": not record.get("first_reported"), "stale_days": stale}
        groups["they" if not ours else bucket].append((record, info))
        if info["new"]:
            shown_new.append(cid)
        if bucket == "overdue" and not likely:
            nudges.append({"id": cid, "direction": record["direction"], "deliverable": record["deliverable"],
                           "owner": record["owner"], "owner_email": record.get("owner_email"),
                           "counterpart": record["counterpart"], "counterpart_email": record.get("counterpart_email"),
                           "due": record["due"]["date"], "days_overdue": (today - to_day(record["due"]["date"])).days,
                           "quote": record["mentions"][0]["quote"], "meeting_title": record["origin"]["title"],
                           "meeting_url": record["origin"]["url"], "meeting_day": record["origin"]["day"]})

    def due_sort(pair):
        record = pair[0]
        return (record["due"]["date"] or "9999-12-31", instant(record["origin"]["start"]), record["id"])

    for key in ["overdue", "due_soon", "later"]:
        groups[key].sort(key=due_sort)
    groups["no_date"].sort(key=lambda pair: (instant(pair[0]["origin"]["start"]), pair[0]["id"]))
    rank = {"overdue": 0, "due_soon": 1, "later": 2, "no_date": 3}
    groups["they"].sort(key=lambda pair: (rank[pair[1]["bucket"]],) + due_sort(pair))
    groups["done"].sort(key=lambda pair: (pair[1]["done_on"], pair[0]["id"]))

    gap = None
    superseded = []
    if args.scheduled:
        last = last_done_run(state)
        start = covered_to(last) if last is not None else earliest_unposted(state)
        if start is not None and scan["from"] > start:
            gap = {"from": start, "posted": last is not None}
        superseded = [r["period_key"] for r in state["runs"] if r["status"] == "pending" and r["period_key"] != period_key]
    src = bundle["sources"]["meetings"]
    unread = [m for m in bundle["meetings"].values() if m["notes_status"] != "ok" and m["transcript_status"] != "ok"]
    # Partial coverage is never complete: any of these makes the digest Incomplete (exit 5 on a schedule).
    incomplete = []
    if src["status"] != "ok":
        incomplete.append(("meeting_source", "meeting notes could not be read (%s %s%s). Items below come from the "
                           "state file and evidence only; promises made in this window are missing." % (
                               FORMATS[args.format].text(provider_label(src["provider"])), src["status"].replace("_", " "),
                               ": " + FORMATS[args.format].text(src["detail"]) if src["detail"] else "")))
    limit = history_limit(src, config)
    if limit is not None and scan["from"] < today - timedelta(days=limit):
        earliest = today - timedelta(days=limit)
        incomplete.append(("history_limit", "this window starts %s, but the %s%d-day note history only reaches back "
                           "to %s; meetings before that could not be read. Start the window on %s; Coverage then lists "
                           "the days before it as a Gap." % (
                               short_day(scan["from"]),
                               ("%s plan's " % FORMATS[args.format].text(src["plan"])) if src["plan"] else "",
                               limit, short_day(earliest), short_day(earliest))))
    # A read that failed (unavailable, not denied) can succeed later: it makes the run Incomplete (nothing is staged,
    # so the next window starts before it and reads it again), but only for a bounded number of runs and days (C4).
    # After that the meeting is given up: listed as not covered, and the digest is posted without it.
    tracking = track_unread(state, bundle, config, as_of, args.scheduled)
    given_up = {x["meeting_id"] for x in tracking["not_covered"]}
    rest = [m for m in bundle["meetings"].values() if m["meeting_id"] not in given_up]
    all_unread = bool(rest) and all(m in unread for m in rest)
    retrying = tracking["retrying"]
    if retrying and not all_unread:
        lost = [x for x in retrying if x["missing"]]
        why = []
        if len(lost) < len(retrying):
            why.append("the notes or transcript read failed: unavailable")
        if lost:
            why.append("%s earlier and %s not in this run's meeting list" % (
                "it failed" if len(retrying) == 1 else "%d failed" % len(lost), "is" if len(lost) == 1 else "are"))
        text = "%s could not be read (%s); promises made in %s are missing until %s read again." % (
            plural(len(retrying), "meeting"), "; ".join(why), "it" if len(retrying) == 1 else "them",
            "it is" if len(retrying) == 1 else "they are")
        if args.scheduled:
            text += " Scheduled runs retry %s for up to %s or %s after the first failure; then %s listed as " \
                    "not covered and the digest is posted without %s." % (
                        "it" if len(retrying) == 1 else "each", plural(config["unreadable_retry_runs"], "more run"),
                        plural(config["unreadable_retry_days"], "day"), "it is" if len(retrying) == 1 else "each is",
                        "it" if len(retrying) == 1 else "them")
        incomplete.append(("meeting_unread", text))
    if all_unread:
        incomplete.append(("no_meeting_readable", "none of the %s in this window could be read (notes and "
                           "transcripts denied or unavailable); promises made in them are missing." % (
                               plural(len(rest), "meeting"))))

    ctx = {"bundle": bundle, "config": config, "today": today, "as_of": as_of, "groups": groups, "fmt": FORMATS[args.format],
           "scheduled": args.scheduled, "owners": [o for o in args.owner if o.strip()], "due_soon_days": due_soon_days,
           "scope": args.scope, "show_soft": args.soft_mentions, "soft": stats["soft"], "stats": stats,
           "dismissed": dismissed, "filtered": filtered, "carried": carried, "nudges": nudges,
           "period_key": period_key, "tracked": len(state["commitments"]), "all_items": all_items,
           "gap": gap, "superseded": superseded, "pruned_open": pruned_open,
           "incomplete": [text for _, text in incomplete], "tracking": tracking}
    total = sum(len(v) for v in groups.values())
    run_entry = None
    if incomplete:
        # Never stage, post or mark anything reported for a window that was not fully read; a scheduled run only
        # notes the attempt, so --next-window still starts from it.
        result = {"status": "incomplete", "period_key": period_key, "markdown": render(ctx)}
        if args.scheduled:
            # Its digest is posted once; confirming that post records the items it announced (C4), so a later digest
            # doesn't flag them new again. The period itself stays open.
            run_entry = {"status": "incomplete", "staged": None}
            if not args.dry_run:
                run_entry["staged"] = {"first_reported": sorted(set(shown_new)), "done_reported": sorted(shown_done)}
                result["confirm_with"] = "--confirm-posted %s" % period_key
    elif args.scheduled and total == 0 and not unread and not tracking["not_covered"] and gap is None:
        result = {"status": "skipped", "period_key": period_key,
                  "reason": "nothing to report for %s (no open, due, owed or newly done commitments)." % period_key}
        run_entry = {"status": "empty", "staged": None}
        for mid in tracking["settled"]:  # an empty run counts as posted at once
            state["unread_meetings"].pop(mid, None)
    else:
        result = {"status": "reported", "period_key": period_key, "markdown": render(ctx)}
        if args.scheduled and not args.dry_run:
            result["confirm_with"] = "--confirm-posted %s" % period_key
            run_entry = {"status": "pending",
                         "staged": {"first_reported": sorted(set(shown_new)), "done_reported": sorted(shown_done)}}
            if tracking["settled"]:  # a given-up meeting read again stops being offered once this is posted
                run_entry["staged"]["read_again"] = tracking["settled"]

    if run_entry is not None:
        # A resumed digest keeps the time it was first staged: its meetings were listed then, so the next window
        # (covered_to) must not assume that anything which started later was read.
        stamp = as_of.isoformat()
        for run in state["runs"]:
            if run["period_key"] == period_key and run["status"] == "pending" and instant(run["as_of"]) < instant(stamp):
                stamp = run["as_of"]
        for run in state["runs"]:
            # A staged digest supersedes every pending one; an Incomplete one only a pending digest of its own period
            # (that period was resumed and printed again, Incomplete this time), so one period has one live entry.
            if run["status"] == "pending" and (run_entry["status"] != "incomplete" or run["period_key"] == period_key):
                run["status"] = "superseded"
        state["runs"] = [r for r in state["runs"]
                         if not (r["period_key"] == period_key and r["status"] in ("superseded", "incomplete"))]
        run_entry.update({"period_key": period_key, "as_of": stamp, "mode": "scheduled",
                          "counts": {k: len(v) for k, v in sorted(groups.items())}})
        state["runs"].append(run_entry)
        state["runs"] = state["runs"][-config["max_runs_logged"]:]
    prune(state, today, config, stats["touched"])
    state["updated_at"] = as_of.isoformat()
    result["counts"] = {k: len(v) for k, v in groups.items()}
    result["groups"] = {k: [summary_item(rec, info) for rec, info in v] for k, v in groups.items()}
    result["nudge_candidates"] = nudges
    result["coverage"] = {"meetings": len(bundle["meetings"]), "soft_mentions": len(stats["soft"]),
                          "dismissed": dismissed, "out_of_scope": stats["out_of_scope"],
                          "meeting_source": bundle["sources"]["meetings"]["status"],
                          "meetings_not_read": len(unread), "links_dropped": len(bundle["url_warnings"]),
                          "incomplete": [code for code, _ in incomplete],
                          "retrying": tracking["retrying"], "not_covered": tracking["not_covered"],
                          "read_again": tracking["read_again"]}
    return result, state


def summary_item(record, info):
    return {"id": record["id"], "direction": record["direction"], "owner": record["owner"],
            "counterpart": record["counterpart"], "deliverable": record["deliverable"],
            "due": record["due"]["date"], "due_kind": record["due"]["kind"], "status": record["last_status"],
            "new": bool(info.get("new")), "meeting_title": record["origin"]["title"],
            "meeting_url": record["origin"]["url"], "meeting_day": record["origin"]["day"]}


def list_open(state):
    """Tracked items whose completion still needs checking: not dismissed, not already reported done."""
    out = []
    for cid in sorted(state["commitments"]):
        record = state["commitments"][cid]
        if record.get("dismissed") or record.get("done_reported"):
            continue
        out.append({"commitment_id": cid, "direction": record["direction"], "owner": record["owner"],
                    "owner_email": record.get("owner_email"), "counterpart": record["counterpart"],
                    "counterpart_email": record.get("counterpart_email"), "deliverable": record["deliverable"],
                    "made_on": record["origin"]["day"], "meeting_title": record["origin"]["title"],
                    "meeting_url": record["origin"]["url"], "due": record["due"]["date"],
                    "last_status": record.get("last_status")})
    return out


def last_activity(record):
    """The latest day the item was promised, given evidence, dismissed or restored."""
    days = [m["day"] for m in record["mentions"]] + [e["date"] for e in record["evidence"]]
    if record.get("dismissed"):
        days.append(record["dismissed"]["date"])
    if record.get("restored_on"):
        days.append(record["restored_on"])
    return to_day(max(days))


def prune(state, today, config, keep):
    """Drop old items that are dismissed, or done and reported done (never an item that is open again). Old means no
    mention, evidence, dismissal or restore in the last prune_after_days: a promise repeated recently and then
    dismissed is remembered, so re-reading that recent meeting doesn't track it again as new. Items this run's bundle
    refers to (keep) are never dropped by that run, so the same bundle can be run again (--resume-pending)."""
    horizon = today - timedelta(days=config["prune_after_days"])
    for cid in sorted(state["commitments"]):
        record = state["commitments"][cid]
        if cid in keep:
            continue
        old = last_activity(record) < horizon
        reported_done = record.get("done_reported") and evaluate(record, today, 1)[0] == "done"
        if old and (reported_done or record.get("dismissed")):
            del state["commitments"][cid]


def prune_open(state, today, config, keep):
    """Drop open items with no mention, due date, evidence or restore in the last prune_open_after_days, except those
    this run's bundle refers to (keep); return the count."""
    horizon = today - timedelta(days=config["prune_open_after_days"])
    dropped = 0
    for cid in sorted(state["commitments"]):
        record = state["commitments"][cid]
        if cid in keep or record.get("dismissed") or record.get("done_reported") or evaluate(record, today, 1)[0] == "done":
            continue
        days = [last_activity(record)]
        if record["due"]["date"]:
            days.append(to_day(record["due"]["date"]))
        if max(days) < horizon:
            del state["commitments"][cid]
            dropped += 1
    return dropped


def mark_reported(state, run):
    """Commit a posted digest's new-item and done-item marks; returns how many of each it staged."""
    day = instant(run["as_of"]).date()
    items = state["commitments"]
    for cid in run["staged"]["first_reported"]:
        if cid in items and not items[cid]["first_reported"]:
            items[cid]["first_reported"] = run["as_of"]
    for cid in run["staged"]["done_reported"]:
        if cid in items and not items[cid]["done_reported"] and evaluate(items[cid], day, 1)[0] == "done":
            items[cid]["done_reported"] = run["as_of"]
    return len(run["staged"]["first_reported"]), len(run["staged"]["done_reported"])


def confirm_posted(state, period_key, confirmed_at):
    """Second phase: the digest for period_key was posted, so commit its new-item and done-item marks."""
    runs = [r for r in state["runs"] if r["period_key"] == period_key]
    if any(r["status"] in DONE_RUN_STATUSES for r in runs):
        return "Already confirmed: %s." % period_key
    pending = [r for r in runs if r["status"] == "pending"]
    if not pending:
        if any(r["status"] == "superseded" for r in runs):
            raise InputError("--confirm-posted: %s was superseded by a later digest that repeated its items; "
                             "confirm that one instead" % period_key)
        staged = [r for r in runs if r["status"] == "incomplete" and r["staged"] is not None]
        if staged:
            # The Incomplete digest was posted: record what it announced (so nothing is flagged new twice), but the
            # period stays open, so the next run's window still covers it.
            run = staged[-1]
            if run.get("confirmed_at"):
                return "Already recorded: the Incomplete digest for %s (the period stays open)." % period_key
            new, done = mark_reported(state, run)
            run["confirmed_at"] = confirmed_at or run["as_of"]
            return ("Recorded the Incomplete digest for %s as posted (%s new, %s reported done); the period stays open: "
                    "the next run's window covers it again." % (period_key, new, done))
        if runs:
            raise InputError("--confirm-posted: %s was incomplete (its meetings could not all be read), so nothing "
                             "was staged to confirm; the next run's window covers it again" % period_key)
        raise InputError("--confirm-posted: no pending digest for %r in the state file" % period_key)
    run = pending[-1]
    mark_reported(state, run)
    for mid in run["staged"].get("read_again", []):
        state["unread_meetings"].pop(mid, None)
    run["status"] = "posted"
    run["confirmed_at"] = confirmed_at
    return "Confirmed posted: %s (%s new, %s reported done)." % (
        period_key, len(run["staged"]["first_reported"]), len(run["staged"]["done_reported"]))


def next_window(state, as_of, cadence, config):
    """The scan window for the next scheduled run: from where the last posted window ended (covered_to) through today
    (with no posted run yet: from the earliest unposted attempt). already_posted is set when this cadence slot already
    has its digest."""
    today = as_of.date()
    last = last_done_run(state)
    unposted = earliest_unposted(state)
    posted = posted_in_slot(state, cadence, today)
    if last is not None:
        start = min(covered_to(last), today)
    elif unposted is not None:
        start = min(unposted, today)
    elif cadence == "weekly":
        start = today - timedelta(days=today.weekday())
    else:
        start = Calendar(config).last_business_in(today - timedelta(days=14), today - timedelta(days=1)) or today
    return {"from": iso(start), "to": iso(today), "cadence": cadence,
            "period_key": "%s:%s..%s" % (cadence, iso(start), iso(today)),
            "last_posted": {"period_key": last["period_key"], "as_of": last["as_of"]} if last else None,
            "already_posted": {"period_key": posted["period_key"], "as_of": posted["as_of"]} if posted else None,
            "pending": [r["period_key"] for r in state["runs"] if r["status"] == "pending"],
            "unconfirmed_incomplete": [r["period_key"] for r in state["runs"] if r["status"] == "incomplete"
                                       and r["staged"] is not None and not r.get("confirmed_at")],
            "reread": [{"meeting_id": mid, "title": rec["title"], "day": rec["day"], "url": rec["url"],
                        "attempts": rec["attempts"], "given_up": rec["given_up"]}
                       for mid, rec in sorted(state["unread_meetings"].items())
                       if rec["given_up"] and to_day(rec["reread_until"]) >= today]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Track meeting commitments and render a commitment digest.")
    parser.add_argument("--bundle", type=Path, help="Normalised JSON bundle (see DATA_CONTRACT.md). Required "
                        "for a digest.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--as-of", help="ISO datetime with UTC offset; the only 'now'. Required for a digest "
                        "and --next-window.")
    parser.add_argument("--list-open", action="store_true",
                        help="Print the tracked items that still need a completion check (JSON) and exit. "
                             "Needs --state only.")
    parser.add_argument("--next-window", choices=["daily", "weekly"],
                        help="Print the scan window for the next scheduled run (JSON) and exit. "
                             "Needs --state and --as-of.")
    parser.add_argument("--confirm-posted", metavar="PERIOD_KEY",
                        help="Second phase of a scheduled run: record that the pending digest for this period "
                             "was posted. Needs --state.")
    parser.add_argument("--resume-pending", action="store_true",
                        help="With --scheduled: print the digest of a period that is still pending again "
                             "(after a failed post) instead of stopping with exit 4.")
    parser.add_argument("--internal-domain", action="append", default=[],
                        help="Your company's email domain(s); repeatable. Required.")
    parser.add_argument("--state", type=Path, help="State file carried across runs (created if missing).")
    parser.add_argument("--scheduled", action="store_true",
                        help="Scheduled run: stage the digest as pending until --confirm-posted, skip a period "
                             "already posted, skip when there is nothing to report.")
    parser.add_argument("--dry-run", action="store_true", help="Read the state file but never write it.")
    parser.add_argument("--scope", choices=["external", "all"], default="external",
                        help="external (default) skips commitments from internal-only meetings.")
    parser.add_argument("--owner", action="append", default=[],
                        help="Show only our commitments owned by this name or email; repeatable.")
    parser.add_argument("--due-soon-days", type=int, help="Override due_soon_days from the config.")
    parser.add_argument("--soft-mentions", action="store_true", help="List soft mentions in their own section.")
    parser.add_argument("--format", choices=sorted(FORMATS), default="markdown",
                        help="markdown (default; Type Spaces and docs) or slack (Slack mrkdwn).")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of markdown.")
    args = parser.parse_args(argv)
    utf8_stdio()

    lock = None
    try:
        commands = [name for name, on in [("--list-open", args.list_open), ("--next-window", args.next_window),
                                          ("--confirm-posted", args.confirm_posted)] if on]
        if len(commands) > 1:
            raise InputError("%s cannot be combined" % " and ".join(commands))
        if commands and args.state is None:
            raise InputError("%s: needs --state" % commands[0])
        if args.list_open:
            check_state_path(args.state, writing=False)
            if not args.state.exists():
                return emit("No state file at %s yet: nothing is tracked. The first digest run creates it." % args.state)
            return emit(json.dumps(list_open(load_state(args.state)), ensure_ascii=False, indent=2))
        if args.next_window:
            if args.as_of is None:
                raise InputError("--next-window: needs --as-of")
            as_of = parse_as_of(args.as_of)
            config = load_config(args.config)
            check_state_path(args.state, writing=False)
            return emit(json.dumps(next_window(load_state(args.state), as_of, args.next_window, config), indent=2))
        if args.confirm_posted:
            confirmed_at = parse_as_of(args.as_of).isoformat() if args.as_of else None
            check_state_path(args.state, writing=True)
            if not args.state.exists():
                raise InputError("--confirm-posted: state %s does not exist" % args.state)
            lock = StateLock(args.state)
            lock.acquire()
            state = load_state(args.state)
            message = confirm_posted(state, args.confirm_posted, confirmed_at)
            write_state(args.state, state)
            lock.release()
            return emit(message)

        if args.bundle is None or args.as_of is None:
            raise InputError("--bundle and --as-of are required (unless --list-open, --next-window or "
                             "--confirm-posted)")
        if args.scheduled and args.state is None:
            raise InputError("--scheduled: needs --state, otherwise the same period could be reported twice")
        if args.resume_pending and not args.scheduled:
            raise InputError("--resume-pending: only applies to --scheduled runs")
        writing = args.state is not None and not args.dry_run
        check_state_path(args.state, writing)
        if writing:
            lock = StateLock(args.state)
            lock.acquire()
        result, state = build(args, load_state(args.state))
        # Persist BEFORE printing: if the state can't be written, nothing has been shown or posted.
        if state is not None and writing:
            write_state(args.state, state)
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
        note("pending: post the digest above, then run --confirm-posted %s" % result["period_key"])
        return 0
    code = emit(text)
    if code:
        return code
    if result.get("confirm_with") and result["status"] == "incomplete":
        note("incomplete: post the digest above once, then run %s (the period stays open; the next run covers it "
             "again)" % result["confirm_with"])
    return EXIT_INCOMPLETE if result["status"] == "incomplete" and args.scheduled else 0


def utf8_stdio():
    """Digests contain non-ASCII text (—, →, quotes); write UTF-8 whatever the locale says (Python 3.7+)."""
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
    (nothing is retried): 4 if this run staged a pending digest (rerun with --resume-pending), otherwise 2."""
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
            note("pending: the digest for %s was staged but could not be written to stdout (%s). Nothing was "
                 "posted. Rerun with --resume-pending to print it again, post it, then run --confirm-posted %s."
                 % (pending, reason, pending))
            return EXIT_PENDING
        note("error: could not write the output to stdout (%s)" % reason)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
