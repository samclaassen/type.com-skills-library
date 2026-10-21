#!/usr/bin/env python3
"""Render a weekly customer feedback digest from one normalised JSON bundle.

Stdlib only, offline, deterministic, Python 3.8+. --as-of is the only source of
"now" in the digest (see the lock-file note below).

What the script does (the agent does the reading and judgement):
  * validates the bundle (see DATA_CONTRACT.md) and refuses bad input with exit 2
  * classifies meetings: internal-only (attendee domains vs --internal-domain),
    attendees unknown, external but not a customer/prospect, outside the period
  * merges the same meeting captured as two notes (same start, shared external attendees)
  * dedupes feedback items: same account + same theme + same meeting = one item
  * counts DISTINCT accounts per theme (never raw mentions) and ranks themes,
    optionally weighted by ARR or plan
  * compares with the previous digest in the state file: new / returning /
    recurring / growing, plus themes that went quiet
  * picks the best issue-tracker match per theme and drafts issues for
    unmatched high-signal themes (drafts only; never created here)
  * escapes every interpolated value for markdown (backslash first) and Slack,
    breaks autolinks inside values, and only links http(s) URLs it validated;
    replaces emails and phone numbers in displayed text with a pattern-based
    redaction that can miss unusual formats
  * scheduled runs use a two-phase commit per ISO week (pending, then
    --confirm-posted) under an exclusive lock file

Exit codes: 0 digest, one-line skip notice or confirmation printed; 1 stdout was
closed or the pipe broke (a scheduled week stays pending); 2 bad input,
config, state or path (nothing on stdout); 3 another run holds the state lock;
4 scheduled week is pending (rendered, never confirmed) and --resume-pending was
not given; 5 scheduled run whose meeting list is not ok: a one-line notice is
printed and nothing is recorded.

The wall clock is read only to decide whether a leftover lock file is stale.
"""

import argparse
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
from urllib.parse import quote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "digest.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCE_KEYS = ["meetings", "transcripts", "crm", "billing", "issue_tracker"]
SOURCE_STATUSES = ["ok", "partial", "unavailable", "denied", "not_connected", "not_configured", "not_requested"]
SOURCE_LABELS = {"meetings": "Meeting notes", "transcripts": "Transcripts", "crm": "CRM", "billing": "Billing",
                 "issue_tracker": "Issue tracker"}
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "manual": "uploaded transcripts", "hubspot": "HubSpot",
                   "attio": "Attio", "salesforce": "Salesforce", "stripe": "Stripe", "linear": "Linear",
                   "jira": "Jira", "github": "GitHub", "none": "none"}
INCLUDED_KINDS = ["customer", "prospect"]
EXCLUDED_KINDS = ["partner", "investor", "candidate", "vendor", "other"]
MEETING_KINDS = INCLUDED_KINDS + EXCLUDED_KINDS + ["unknown"]
ACCOUNT_STATUSES = ["customer", "prospect", "churned", "unknown"]
TRANSCRIPT_STATUSES = ["ok", "denied", "unavailable", "not_requested"]
NOTES_STATUSES = ["ok", "unavailable"]
EVIDENCE_KINDS = ["transcript", "note"]
SPEAKER_SIDES = ["customer", "unconfirmed"]
CONFIDENCES = ["high", "medium", "low"]
TRACKER_PROVIDERS = ["linear", "jira", "github", "other"]
SEARCH_STATUSES = ["searched", "not_searched", "unavailable"]
WEIGHTINGS = ["none", "arr", "plan"]
FORMATS = ["markdown", "slack", "json"]
CONFIG_KEYS = ["feedback_types", "type_priority", "draftable_types", "top_min_accounts", "max_top_themes",
               "max_quotes_per_theme", "max_single_mentions", "max_quieter_themes", "draft_min_accounts",
               "draft_if_customer_types", "max_quote_chars", "max_role_chars", "currency_symbol",
               "plan_weights", "default_plan_weight", "ignored_attendee_domains", "ignored_attendee_local_parts",
               "free_email_domains"]
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
WEEK_RE = re.compile(r"^[0-9]{4}-W[0-9]{2}$")
DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
DATETIME_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,6}))?)?"
                         r"(Z|[+-][0-9]{2}:[0-9]{2})$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]{1,5})?$")
EMAIL_RE = re.compile(r"[^\s@<>()\[\]{}\"',;:]{1,64}@(?:[^\s@<>()\[\]{}\"',;:.]+\.)+[^\s@<>()\[\]{}\"',;:.]+")
# Phone candidates: optional +country code, optional (area) group, then digit groups joined by one space, dot or
# hyphen. Never starts inside a word, a reference (#, /, -, .) or a time (10:30). phone_like() then decides.
PHONE_RE = re.compile(r"(?<![\w+#/.-])(?<![0-9]:)(?:\+ ?)?(?:\([0-9]{1,5}\)[ .-]?)?[0-9]+"
                      r"(?:(?:[ .-]|[ .-]?\([0-9]{1,5}\)[ .-]?)[0-9]+)*(?!\w)", re.ASCII)
PHONE_MIN_DIGITS, PHONE_MAX_DIGITS = 9, 15
PHONE_CUE_RE = re.compile(r"\b(?:call|phone|tel|telephone|mobile|cell|sms|text|whatsapp|fax|dial|ring)\b\D{0,12}$",
                          re.I)
THOUSANDS_RE = re.compile(r"^[0-9]{1,3}([ .])[0-9]{3}(?:\1[0-9]{3})*$")
NUMERIC_DATE_RE = re.compile(r"(?<![0-9])(?:[0-9]{4}[-./][0-9]{1,2}[-./][0-9]{1,2}|[0-9]{1,2}[-./][0-9]{1,2}[-./][0-9]{4})"
                             r"(?![0-9])")
REFERENCE_BEFORE_RE = re.compile(r"(?:\b(?:order|po|p\.o\.|invoice|inv|ticket|case|ref|reference|sku|serial|tracking|isbn|issn|zip|postcode|"
                                 r"build|version|account|acct|id)\s*(?:#|no\.?|nr\.?|number|:)?\s*|[#$€£¥]\s*)$", re.I)
MD_SPECIAL_RE = re.compile(r"([\\`*_~\[\]()|#@$=])")
MD_DOMAIN_DOT_RE = re.compile(r"(?<=[\w-])\.(?=[^\W\d_])|(?<=www)\.", re.I)
# Markdown: every dot inside a token is escaped (renders the same), so no renderer can see a domain or www. link.
MD_DOT_RE = re.compile(r"(?<=\S)\.(?=[\w-])")
SLACK_LOOKALIKES = {"*": "\u2217", "_": "\u02cd", "~": "\u223c", "`": "\u02cb", "@": "\uff20"}
URL_RE = re.compile(r"https?://|www\.", re.I)
URL_SAFE = "-._~!$&'*+,;=:@/?#%"
BIDI_CONTROLS = set(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)) + [0x200E, 0x200F])
# Invisible characters that only hide things (a link, a phone number, a duplicate name): dropped. Zero-width
# (non-)joiners are kept inside emoji and scripts that need them, and dropped next to ASCII text.
INVISIBLE = set(chr(c) for c in [0x00AD, 0x180E, 0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF])
ASCII_JOINER_RE = re.compile(r"(?<=[\x00-\x7f])[\u200c\u200d]+|[\u200c\u200d]+(?=[\x00-\x7f])")
# Email and phone matching run on a same-length ASCII stand-in: other scripts' digits, dash look-alikes and
# full-width forms count.
ASCII_LOOKALIKES = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
                    "\ufe63": "-", "\ufe6b": "@"}
LOCK_STALE_MINUTES = 15
MIN_YEAR, MAX_YEAR = 1900, 2999  # keeps every date and offset calculation in range
MAX_ARR, MAX_WEIGHT = 1e15, 1e6  # sums of these stay finite, so scores and JSON never become Infinity
EXIT_OUTPUT, EXIT_INPUT, EXIT_LOCKED, EXIT_PENDING, EXIT_INCOMPLETE = 1, 2, 3, 4, 5
QUOTE_PAIRS = [("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'"), ("«", "»")]
STATE_SKILL = "customer-feedback-digest"
TREND_LABELS = {"new": "new", "returning": "returning", "recurring": "recurring", "growing": "growing",
                "first": "first digest"}


class InputError(Exception):
    """Bad input; the message names the file and the JSON path."""


class StopRun(Exception):
    """A clean, documented non-zero exit (lock held, week pending, incomplete coverage)."""

    def __init__(self, code, message, stdout=None):
        Exception.__init__(self, message)
        self.code = code
        self.stdout = stdout


# --------------------------------------------------------------------------- text safety


def clean_text(value):
    """Collapse whitespace (incl. newlines) and drop control, lone-surrogate, bidi-override and invisible characters."""
    kept = "".join(" " if ch.isspace() else ch for ch in value
                   if ch.isspace() or (unicodedata.category(ch) not in ("Cc", "Cs") and ch not in BIDI_CONTROLS
                                       and ch not in INVISIBLE))
    return " ".join(ASCII_JOINER_RE.sub("", kept).split())


def name_key(name):
    """Compare company names ignoring case, full-width forms and invisible characters."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join("".join(ch for ch in folded if unicodedata.category(ch) != "Cf").split())


def phone_like(candidate, before):
    """Pattern-based phone test: 9-15 digits in phone-like groups, and not a date, IP, version, count or reference."""
    groups = re.findall(r"[0-9]+", candidate)
    digits = sum(len(g) for g in groups)
    plus = candidate.startswith("+")
    if not PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS or (len(groups) == 1 and not plus):
        return False
    if NUMERIC_DATE_RE.search(candidate) or REFERENCE_BEFORE_RE.search(before[-30:]):
        return False
    seps = set(re.findall(r"[ .-]", re.sub(r"\([0-9]+\)", "", candidate)))
    if not plus and all(THOUSANDS_RE.match(side) for side in re.split(r" ?- ?", candidate)):
        return False  # thousands: 12 500 000 000, or a range 100 000-200 000
    if not plus and seps == {"."} and (any(len(g) == 1 for g in groups) or
                          (len(groups) == 4 and all(len(g) <= 3 and int(g) <= 255 for g in groups))):
        return False  # version number or IPv4 address
    if not plus and "(" not in candidate and not PHONE_CUE_RE.search(before[-30:]):
        if "." in seps and len(seps) > 1:
            return False  # a list of decimals: 9.99 19.99 29.99
        if all(len(g) == 4 and 1900 <= int(g) <= 2099 for g in groups):
            return False  # a list or range of years: 2019 2020 2021
        nanp = [len(g) for g in groups] in ([3, 3, 4], [1, 3, 3, 4])
        rising = all(int(a) < int(b) for a, b in zip(groups, groups[1:]))
        falling = all(int(a) > int(b) for a, b in zip(groups, groups[1:]))
        if not nanp and groups[0][0] != "0" and (rising or (falling and len(groups) >= 3)):
            return False  # an ascending or descending list or range: 1200 1500 1800, 120-150-200, 5000 2000 1000
        if not nanp and len(groups) >= 3 and (rising or falling) and len(set(len(g) for g in groups)) == 1 \
                and len(groups[0]) >= 3:
            return False  # a zero-padded list: 0800 0900 1000
        if not nanp and all(g.endswith("00") for g in groups):
            return False  # round amounts: 40000 30000, 5000 2000 1000
    return True


def ascii_shadow(text):
    """Same length as text: other scripts' decimal digits, dash look-alikes and full-width forms (＠ ． ｅ …) as
    their ASCII characters, so redaction sees through them."""
    return "".join(ch if ord(ch) < 128 else
                   (str(unicodedata.decimal(ch)) if unicodedata.decimal(ch, None) is not None
                    else ASCII_LOOKALIKES.get(ch) or (chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch))
                   for ch in text)


def redact_phones(text):
    shadow = ascii_shadow(text)
    out, last = [], 0
    for m in PHONE_RE.finditer(shadow):
        start, end = m.span()
        out.append(text[last:start])
        last = end
        date = NUMERIC_DATE_RE.match(m.group(0))
        if date:  # a match starting with a date keeps the date; the rest is checked on its own
            out.append(text[start:start + date.end()] + redact_phones(text[start + date.end():end]))
        else:
            out.append("[phone removed]" if phone_like(m.group(0), shadow[:start]) else text[start:end])
    out.append(text[last:])
    return "".join(out)


def redact(text):
    """Replace email addresses and phone numbers in free text. Pattern-based: it can miss unusual formats."""
    shadow, out, last = ascii_shadow(text), [], 0
    for m in EMAIL_RE.finditer(shadow):
        out.extend([text[last:m.start()], "[email removed]"])
        last = m.end()
    return redact_phones("".join(out) + text[last:])


def has_phone(text):
    return redact_phones(text) != text


def safe_url(raw):
    """Return a percent-encoded absolute http(s) URL, or None when it is not one."""
    if not isinstance(raw, str) or any(0xD800 <= ord(ch) <= 0xDFFF for ch in raw):
        return None  # undecodable command-line bytes arrive as lone surrogates
    raw = raw.strip()
    if not re.match(r"^https?://", raw, re.I):
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    netloc = parts.netloc
    if parts.scheme.lower() not in ("http", "https") or not netloc or "@" in netloc or not HOST_RE.match(netloc) \
            or not parts.hostname or (port is not None and not 0 < port < 65536):
        return None
    url = "%s://%s%s" % (parts.scheme.lower(), netloc.lower(), quote(parts.path, safe=URL_SAFE))
    if parts.query:
        url += "?" + quote(parts.query, safe=URL_SAFE)
    if parts.fragment:
        url += "#" + quote(parts.fragment, safe=URL_SAFE)
    return url


def md(text):
    """Markdown-safe text: backslash first, then every character that can start a link, image, emphasis, code,
    strikethrough, table cell, heading, math ($) or highlight (==); entities and raw HTML are neutralised and autolinks are broken with
    escapes (scheme://, and every dot inside a word, which covers www. and bare domains), so the rendered text is
    unchanged but cannot link or format."""
    text = MD_SPECIAL_RE.sub(r"\\\1", text)
    text = re.sub(r"&(?=#?[0-9A-Za-z]+;)", r"\\&", text).replace("<", "&lt;").replace(">", "&gt;")
    text = MD_DOT_RE.sub(r"\\.", text.replace("://", "\\://"))
    lead = re.match(r"([0-9]+)([.)])", text)
    if lead:
        return lead.group(1) + "\\" + text[lead.end(1):]
    return "\\" + text if text[:1] in ("-", "+") else text


def md_link(label, url):
    return "[%s](%s)" % (md(label), url) if url else md(label)


def slack_url(url):
    """A URL the script validated: only &, < and > need escaping."""
    return url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack(text):
    """Slack mrkdwn-safe text. Slack has no backslash escapes, so <!channel>, <@U…>, <#C…> and <url|label> are
    entity-escaped, the formatting characters * _ ~ ` and @ become look-alike characters, and scheme:// and
    domain dots are broken so Slack cannot auto-link a value."""
    text = slack_url(text)
    text = re.sub(r"[*_~`@]", lambda m: SLACK_LOOKALIKES[m.group(0)], text)
    return MD_DOMAIN_DOT_RE.sub("\u2024", text.replace("://", ":\u2215\u2215"))


def slack_link(label, url):
    if not url:
        return slack(label)
    return "<%s|%s>" % (slack_url(url), slack(label.replace("|", " ")))


def fence_for(lines):
    """A code fence longer than any backtick run inside the block."""
    longest = max([len(run) for line in lines for run in re.findall(r"`+", line)] + [2])
    return "`" * (longest + 1)


# --------------------------------------------------------------------------- reading


class Reader:
    """Typed accessors that report errors as '<file>: <json.path>: problem'."""

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

    def lst(self, value, path, required=False):
        if value is None:
            if required:
                raise self.fail(path, "is required (use [] when there is nothing)")
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        return value

    def text(self, parent, key, path, required=True):
        value = parent.get(key)
        if isinstance(value, str):
            value = clean_text(value)
        if value is None or value == "":
            if required:
                raise self.fail("%s.%s" % (path, key), "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail("%s.%s" % (path, key), "must be a string, got %r" % (value,))
        return value

    def prose(self, parent, key, path, required=True):
        """Free text that is displayed: cleaned, with emails and phone numbers redacted."""
        value = self.text(parent, key, path, required)
        return None if value is None else redact(value)

    def url(self, parent, key, path, warnings):
        """Required link; an unsafe or non-http(s) value is dropped (None) and counted as a warning."""
        value = self.text(parent, key, path)
        url = safe_url(value)
        if url is None:
            warnings.append("%s.%s" % (path, key))
        return url

    def enum(self, parent, key, path, allowed, default=None):
        value = parent.get(key, default)
        if value is None:
            value = default
        if value not in allowed:
            raise self.fail("%s.%s" % (path, key), "must be one of %s, got %r" % (", ".join(allowed), value))
        return value

    def number(self, parent, key, path, required=True, maximum=None):
        value = parent.get(key)
        if value is None and not required:
            return None
        if not is_number(value, maximum):
            raise self.fail("%s.%s" % (path, key), "must be a non-negative finite number%s, got %r"
                            % (" up to %g" % maximum if maximum is not None else "", value))
        return float(value)

    def integer(self, parent, key, path, required=True, maximum=None):
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or \
                (maximum is not None and value > maximum):
            raise self.fail("%s.%s" % (path, key), "must be a non-negative integer%s, got %r"
                            % (" up to %d" % maximum if maximum is not None else "", value))
        return value

    def day(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        return parse_day(value, lambda problem: self.fail("%s.%s" % (path, key), problem))

    def moment(self, parent, key, path):
        value = self.text(parent, key, path)
        return parse_moment(value, lambda problem: self.fail("%s.%s" % (path, key), problem))

    def key(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        value = value.lower()
        if not KEY_RE.match(value):
            raise self.fail("%s.%s" % (path, key),
                            "%r must be lower-case letters, digits, '.', '_' or '-' (e.g. outfitters.example)" % (value,))
        return value


def is_number(value, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0 and (maximum is None or value <= maximum)
    except OverflowError:
        return False


def parse_day(value, fail):
    """Strict YYYY-MM-DD (ASCII digits only), identical on every Python version."""
    m = DATE_RE.match(value) if isinstance(value, str) else None
    if not m:
        raise fail("%r is not a YYYY-MM-DD date" % (value,))
    if not MIN_YEAR <= int(m.group(1)) <= MAX_YEAR:
        raise fail("%r is outside the years %d-%d" % (value, MIN_YEAR, MAX_YEAR))
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise fail("%r is not a real date" % (value,)) from None


def parse_moment(value, fail):
    """Strict YYYY-MM-DDTHH:MM[:SS[.ffffff]] with Z or ±HH:MM. Parsed by hand, so 3.8 and 3.14 agree."""
    m = DATETIME_RE.match(value.strip()) if isinstance(value, str) else None
    if not m:
        raise fail("%r is not a datetime like 2026-10-05T08:00:00-07:00 (YYYY-MM-DDTHH:MM[:SS[.ffffff]] with Z "
                   "or a ±HH:MM offset)" % (value,))
    year, month, day_, hour, minute, second, frac, offset = m.groups()
    if not MIN_YEAR <= int(year) <= MAX_YEAR:
        raise fail("%r is outside the years %d-%d" % (value, MIN_YEAR, MAX_YEAR))
    if offset == "Z":
        tz = timezone.utc
    else:
        oh, om = int(offset[1:3]), int(offset[4:6])
        if om > 59 or oh * 60 + om > 14 * 60:
            raise fail("%r has an impossible UTC offset (at most ±14:00, minutes below 60)" % (value,))
        delta = timedelta(hours=oh, minutes=om)
        tz = timezone(-delta if offset[0] == "-" else delta)
    try:
        return datetime(int(year), int(month), int(day_), int(hour), int(minute), int(second or 0),
                        int((frac or "0").ljust(6, "0")), tzinfo=tz)
    except ValueError:
        raise fail("%r is not a real date and time" % (value,)) from None


def reject_constant(name):
    raise InputError("%s is not allowed (numbers must be finite)" % name)


def finite_int(text):
    if len(text.lstrip("-")) > 16:
        raise InputError("%s... is too large (integers have at most 16 digits)" % text[:20])
    return int(text)


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise InputError("%s is not allowed (numbers must be finite)" % text)
    return value


def stat_path(path, label):
    """os.stat() of path, or None when nothing is there. A path the OS cannot check (name too long, no permission)
    is an InputError on every Python version (3.12+ Path.exists() would quietly say False)."""
    try:
        return os.stat(str(path))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise InputError("%s %s: cannot be checked (%s)" % (label, path, getattr(exc, "strerror", None) or exc)) from None


def load_json(path, label):
    st = stat_path(path, label)
    if st is None or not stat.S_ISREG(st.st_mode):
        raise InputError("%s: file not found: %s" % (label, path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant, parse_float=finite_float,
                          parse_int=finite_int)
        json.dumps(data, ensure_ascii=False).encode("utf-8")
        return data
    except UnicodeEncodeError:
        raise InputError("%s: %s contains a \\u escape that is not a character (lone surrogate); fix it"
                         % (label, path)) from None
    except InputError as exc:
        raise InputError("%s: %s: %s" % (label, path, exc)) from None
    except OSError as exc:
        raise InputError("%s: %s cannot be read (%s)" % (label, path, exc.strerror or exc)) from None
    except UnicodeDecodeError:
        raise InputError("%s: %s is not UTF-8 text" % (label, path)) from None
    except json.JSONDecodeError as exc:
        raise InputError("%s: %s is not valid JSON (line %d, column %d: %s)"
                         % (label, path, exc.lineno, exc.colno, exc.msg)) from None
    except (ValueError, RecursionError) as exc:
        raise InputError("%s: %s is not usable JSON (%s)" % (label, path, str(exc)[:120])) from None


def load_config(path):
    config = load_json(path, "config")
    if not isinstance(config, dict):
        raise InputError("config %s: $ must be an object" % path)
    missing = [k for k in CONFIG_KEYS if k not in config]
    if missing:
        raise InputError("config %s: missing key(s): %s" % (path, ", ".join(missing)))

    def string_list(key, pattern=None):
        value = config[key]
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise InputError("config %s: $.%s must be a list of non-empty strings" % (path, key))
        bad = [v for v in value if pattern is not None and not pattern.match(v)]
        if bad:
            raise InputError("config %s: $.%s has invalid value(s): %s" % (path, key, ", ".join(map(repr, bad))))
        return value

    types = config["feedback_types"]
    if not isinstance(types, dict) or not types:
        raise InputError("config %s: $.feedback_types must be a non-empty object" % path)
    for key, label in types.items():
        if not KEY_RE.match(key) or not isinstance(label, str) or not clean_text(label):
            raise InputError("config %s: $.feedback_types.%s must map a lower-case key to a non-empty label"
                             % (path, key))
    config["feedback_types"] = dict((k, clean_text(v)) for k, v in types.items())
    if sorted(string_list("type_priority")) != sorted(types):
        raise InputError("config %s: $.type_priority must list each feedback type exactly once" % path)
    for key in ["draftable_types", "draft_if_customer_types"]:
        unknown = [t for t in string_list(key) if t not in types]
        if unknown:
            raise InputError("config %s: $.%s has unknown type(s): %s" % (path, key, ", ".join(unknown)))
    r = Reader("config %s" % path)
    for key in ["top_min_accounts", "max_top_themes", "max_quotes_per_theme", "max_single_mentions",
                "max_quieter_themes", "draft_min_accounts", "max_quote_chars", "max_role_chars"]:
        r.integer(config, key, "$")
    r.number(config, "default_plan_weight", "$", maximum=MAX_WEIGHT)
    if not isinstance(config["currency_symbol"], str) or len(config["currency_symbol"]) > 5:
        raise InputError("config %s: $.currency_symbol must be a short string such as \"$\"" % path)
    if not isinstance(config["plan_weights"], dict):
        raise InputError("config %s: $.plan_weights must be an object" % path)
    for plan, weight in config["plan_weights"].items():
        if not is_number(weight, MAX_WEIGHT):
            raise InputError("config %s: $.plan_weights.%s must be a non-negative number up to %g" % (path, plan, MAX_WEIGHT))
    config["plan_weights"] = dict((plan.strip().lower(), weight) for plan, weight in config["plan_weights"].items())
    string_list("ignored_attendee_domains", DOMAIN_RE)
    string_list("ignored_attendee_local_parts", re.compile(r"^[a-z0-9._-]+$"))
    string_list("free_email_domains", DOMAIN_RE)
    return config


def clean_domain(domain):
    d = domain.strip().lower().lstrip("@").strip().rstrip(".")
    return d[4:] if d.startswith("www.") else d


def domain_of(email):
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def in_domains(domain, domains):
    return any(domain == d or domain.endswith("." + d) for d in domains)


def is_bot(email, config):
    """Note-taker bots: exact domain (or a subdomain of it), or a listed local part (ignoring +tags)."""
    local = email.rsplit("@", 1)[0].split("+", 1)[0]
    return in_domains(domain_of(email), config["ignored_attendee_domains"]) or \
        local in config["ignored_attendee_local_parts"]


def strip_outer_quotes(text):
    """Remove one pair of wrapping quote marks, only when neither mark appears anywhere inside."""
    t = text.strip()
    for left, right in QUOTE_PAIRS:
        if len(t) >= len(left) + len(right) and t.startswith(left) and t.endswith(right):
            inner = t[len(left):-len(right)]
            if left not in inner and right not in inner:
                return inner.strip()
            return t
    return t


def load_bundle(path, config):
    raw = load_json(path, "bundle")
    r = Reader("bundle %s" % path)
    r.obj(raw, "$")
    if raw.get("schema_version") != 1 or isinstance(raw.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1, got %r" % (raw.get("schema_version"),))
    b = {"link_warnings": [], "label": "bundle %s" % path}
    warn = b["link_warnings"]

    period = r.obj(raw.get("period"), "$.period")
    b["period_start"] = r.day(period, "start", "$.period")
    b["period_end"] = r.day(period, "end", "$.period")
    if b["period_end"] < b["period_start"]:
        raise r.fail("$.period.end", "is before $.period.start")
    if (b["period_end"] - b["period_start"]).days + 1 > 92:
        raise r.fail("$.period", "spans more than 92 days; run one digest per week or month")
    b["timezone_label"] = r.prose(period, "timezone_label", "$.period", required=False)

    sources_raw = r.obj(raw.get("sources"), "$.sources")
    sources = {}
    for key in SOURCE_KEYS:
        src = r.obj(sources_raw.get(key), "$.sources.%s" % key)
        sources[key] = {
            "provider": r.prose(src, "provider", "$.sources.%s" % key, required=False),
            "status": r.enum(src, "status", "$.sources.%s" % key, SOURCE_STATUSES),
            "detail": r.prose(src, "detail", "$.sources.%s" % key, required=False),
            "history_days": r.integer(src, "history_days", "$.sources.%s" % key, required=False, maximum=36500),
        }
    b["sources"] = sources

    accounts = {}
    owner_of = {}  # case-folded name or domain -> (account key, JSON path); one company = one account
    free = config["free_email_domains"]
    for i, a in enumerate(r.lst(raw.get("accounts"), "$.accounts", required=True)):
        p = "$.accounts[%d]" % i
        r.obj(a, p)
        key = r.key(a, "key", p)
        if key in accounts:
            raise r.fail("%s.key" % p, "duplicate account key %r" % key)
        domains = []
        for j, d in enumerate(r.lst(a.get("domains"), "%s.domains" % p)):
            if not isinstance(d, str) or not DOMAIN_RE.match(clean_domain(d)):
                raise r.fail("%s.domains[%d]" % (p, j), "must be a domain such as outfitters.example, got %r" % (d,))
            domains.append(clean_domain(d))
        name = r.prose(a, "name", p)
        # Free-mail domains (gmail.com…) say nothing about the company, so two accounts may both list one.
        marks = [("name", name_key(name))] + [("domain", d) for d in sorted(set(domains + ([key] if "." in key else [])))
                                               if not in_domains(d, free)]
        for kind, mark in marks:
            other = owner_of.get(mark)
            if other is not None and other[0] != key and kind == "name":
                raise r.fail(p, "name %r is already used by %s (%r); if these are two different companies, make the "
                             "names distinct (for example add the city or domain); if they are one company, merge them "
                             "into a single entry" % (mark, other[1], other[0]))
            if other is not None and other[0] != key:
                raise r.fail(p, "domain %r is already used by %s (%r); one company must be one account, so merge them "
                             "into a single entry (a personal email domain belongs in free_email_domains in the config)"
                             % (mark, other[1], other[0]))
            owner_of[mark] = (key, p)
        accounts[key] = {
            "key": key,
            "name": name,
            "domains": domains,
            "status": r.enum(a, "status", p, ACCOUNT_STATUSES, "unknown"),
            "segment": r.prose(a, "segment", p, required=False),
            "plan": r.prose(a, "plan", p, required=False),
            "arr": r.number(a, "arr", p, required=False, maximum=MAX_ARR),
            "arr_source": r.prose(a, "arr_source", p, required=False),
        }
    b["accounts"] = accounts

    meetings = []
    seen_ids = set()
    for i, m in enumerate(r.lst(raw.get("meetings"), "$.meetings", required=True)):
        p = "$.meetings[%d]" % i
        r.obj(m, p)
        mid = r.text(m, "meeting_id", p)
        if mid in seen_ids:
            raise r.fail("%s.meeting_id" % p, "duplicate meeting_id %r" % mid)
        seen_ids.add(mid)
        if "attendees" not in m:
            raise r.fail("%s.attendees" % p, "is required (use [] when the source lists nobody)")
        attendees = []
        for j, att in enumerate(r.lst(m.get("attendees"), "%s.attendees" % p)):
            q = "%s.attendees[%d]" % (p, j)
            r.obj(att, q)
            email = r.text(att, "email", q).lower()
            if " " in email or not DOMAIN_RE.match(domain_of(email)) or not email.rsplit("@", 1)[0]:
                raise r.fail("%s.email" % q, "%r is not an email address" % email)
            attendees.append({"email": email, "name": r.text(att, "name", q, required=False),
                              "org": r.text(att, "org", q, required=False)})
        start = r.moment(m, "start", p)
        meetings.append({
            "index": i,
            "meeting_id": mid,
            "title": r.prose(m, "title", p),
            "start": start,
            "url": r.url(m, "url", p, warn),
            "source": r.text(m, "source", p, required=False) or sources["meetings"]["provider"] or "notes",
            "attendees": attendees,
            "kind": r.enum(m, "kind", p, MEETING_KINDS, "unknown"),
            "account_key": r.key(m, "account_key", p, required=False),
            "transcript": r.enum(m, "transcript", p, TRANSCRIPT_STATUSES, "not_requested"),
            "notes": r.enum(m, "notes", p, NOTES_STATUSES, "ok"),
        })
    b["meetings"] = meetings
    by_id = dict((m["meeting_id"], m) for m in meetings)

    themes = {}
    for i, t in enumerate(r.lst(raw.get("themes"), "$.themes", required=True)):
        p = "$.themes[%d]" % i
        r.obj(t, p)
        key = r.key(t, "key", p)
        if key in themes:
            raise r.fail("%s.key" % p, "duplicate theme key %r" % key)
        matches = []
        for j, mt in enumerate(r.lst(t.get("issue_matches"), "%s.issue_matches" % p)):
            q = "%s.issue_matches[%d]" % (p, j)
            r.obj(mt, q)
            matches.append({
                "provider": r.enum(mt, "provider", q, TRACKER_PROVIDERS),
                "id": r.prose(mt, "id", q),
                "title": r.prose(mt, "title", q),
                "url": r.url(mt, "url", q, warn),
                "state": r.prose(mt, "state", q, required=False),
                "confidence": r.enum(mt, "confidence", q, CONFIDENCES),
                "reason": r.prose(mt, "reason", q),
            })
        search = r.obj(t.get("tracker_search"), "%s.tracker_search" % p, required=False) or {}
        themes[key] = {
            "key": key,
            "title": r.prose(t, "title", p),
            "description": r.prose(t, "description", p, required=False),
            "matches": matches,
            "search_status": r.enum(search, "status", "%s.tracker_search" % p, SEARCH_STATUSES, "not_searched"),
            "search_query": r.prose(search, "query", "%s.tracker_search" % p, required=False),
            "search_detail": r.prose(search, "detail", "%s.tracker_search" % p, required=False),
        }
    b["themes"] = themes

    items = []
    types = config["feedback_types"]
    for i, it in enumerate(r.lst(raw.get("items"), "$.items", required=True)):
        p = "$.items[%d]" % i
        r.obj(it, p)
        mid = r.text(it, "meeting_id", p)
        if mid not in by_id:
            raise r.fail("%s.meeting_id" % p, "%r is not in $.meetings; every item must cite a meeting" % mid)
        itype = r.enum(it, "type", p, list(types))
        theme = r.key(it, "theme", p)
        if theme not in themes:
            raise r.fail("%s.theme" % p, "%r is not defined in $.themes" % theme)
        ev = r.obj(it.get("evidence"), "%s.evidence" % p)
        ep = "%s.evidence" % p
        kind = r.enum(ev, "kind", ep, EVIDENCE_KINDS)
        text = strip_outer_quotes(r.text(ev, "text", ep))
        if not text:
            raise r.fail("%s.text" % ep, "is empty once surrounding quote marks are removed")
        if len(text) > config["max_quote_chars"]:
            raise r.fail("%s.text" % ep, "is %d characters; keep quotes to the relevant sentence or two "
                         "(max %d)" % (len(text), config["max_quote_chars"]))
        text = redact(text)
        side = ev.get("speaker_side", "customer")
        if side == "internal":
            raise r.fail("%s.speaker_side" % ep, "internal speakers are not customer feedback; drop this item")
        side = r.enum(ev, "speaker_side", ep, SPEAKER_SIDES, "customer")
        role = r.text(ev, "speaker_role", ep)
        if EMAIL_RE.search(role) or has_phone(role) or URL_RE.search(role):
            raise r.fail("%s.speaker_role" % ep, "must be a role such as 'Head of Ops', never an email, phone "
                         "number or link")
        if len(role) > config["max_role_chars"]:
            raise r.fail("%s.speaker_role" % ep, "is longer than %d characters; use a short role"
                         % config["max_role_chars"])
        meeting = by_id[mid]
        if kind == "transcript" and meeting["transcript"] != "ok":
            raise r.fail("%s.kind" % ep, "is 'transcript' but meeting %r has transcript status %r; use 'note' "
                         "for text copied from the notes" % (mid, meeting["transcript"]))
        competitor = r.prose(it, "competitor", p, required=(itype == "competitor"))
        items.append({
            "index": i,
            "id": r.text(it, "id", p, required=False) or "item-%d" % (i + 1),
            "meeting_id": mid,
            "type": itype,
            "theme": theme,
            "summary": r.prose(it, "summary", p, required=False),
            "competitor": competitor,
            "evidence": {"kind": kind, "text": text, "speaker_role": role, "speaker_side": side},
        })
    b["items"] = items

    gaps = []
    for i, g in enumerate(r.lst(raw.get("date_gaps"), "$.date_gaps")):
        p = "$.date_gaps[%d]" % i
        r.obj(g, p)
        gap = {"start": r.day(g, "start", p), "end": r.day(g, "end", p), "reason": r.prose(g, "reason", p)}
        if gap["end"] < gap["start"]:
            raise r.fail("%s.end" % p, "is before %s.start" % p)
        gaps.append(gap)
    b["date_gaps"] = gaps

    # Cross-checks that need the whole bundle.
    for m in meetings:
        p = "$.meetings[%d]" % m["index"]
        if m["account_key"] is not None and m["account_key"] not in accounts:
            raise r.fail("%s.account_key" % p, "%r is not defined in $.accounts" % m["account_key"])
    return b


# --------------------------------------------------------------------------- analysis


def short_date(d):
    return "%s %d" % (MONTHS[d.month - 1], d.day)


def long_date(d):
    return "%s %d, %d" % (MONTHS[d.month - 1], d.day, d.year)


def period_text(start, end):
    if start == end:
        return long_date(start)
    if start.year == end.year:
        return "%s – %s" % (short_date(start), long_date(end))
    return "%s – %s" % (long_date(start), long_date(end))


def iso_week(d):
    year, week, _ = d.isocalendar()
    return "%04d-W%02d" % (year, week)


def money(symbol, amount):
    return "%s%s" % (symbol, "{:,.0f}".format(amount))


def local_day(meeting):
    """The meeting's calendar date in its own UTC offset, as written in the bundle."""
    return meeting["start"].date()


def is_full_week(start, end):
    return start.weekday() == 0 and end - start == timedelta(days=6)


def period_key(start, end):
    """ISO week (2026-W40) for a Monday-Sunday period; otherwise start..end, so other ranges never share a key."""
    return iso_week(end) if is_full_week(start, end) else "%s..%s" % (start.isoformat(), end.isoformat())


def classify(b, config, internal_domains, internal_emails):
    """Split meetings into included and excluded buckets, merging duplicate notes."""
    for m in b["meetings"]:
        people, internal, external = [], [], []
        for a in m["attendees"]:
            if is_bot(a["email"], config):
                continue
            people.append(a)
            if a["email"] in internal_emails or in_domains(domain_of(a["email"]), internal_domains):
                internal.append(a)
            else:
                external.append(a)
        m["people"] = people
        m["external"] = external
        if not (b["period_start"] <= local_day(m) <= b["period_end"]):
            m["bucket"] = "outside_period"
        elif not external and len(people) <= 1:
            m["bucket"] = "attendees_unknown"
        elif not external:
            m["bucket"] = "internal_only"
        elif m["kind"] in INCLUDED_KINDS:
            m["bucket"] = "included"
        elif m["kind"] == "unknown":
            m["bucket"] = "unclassified"
        else:
            m["bucket"] = "not_customer"

    # Duplicate notes: same start instant and shared external attendees (two people captured one call).
    groups = {}
    for m in b["meetings"]:
        if m["bucket"] != "included":
            continue
        instant = m["start"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        groups.setdefault(instant, []).append(m)
    for instant in sorted(groups):
        group = sorted(groups[instant], key=lambda x: (0 if x["transcript"] == "ok" else 1, x["meeting_id"]))
        # Notes are one meeting when they share an external attendee, directly or through another note.
        emails = [set(a["email"] for a in m["external"]) for m in group]
        root = list(range(len(group)))
        for i in range(len(group)):
            for j in range(i):
                if emails[i] & emails[j]:
                    old_root, new_root = max(root[i], root[j]), min(root[i], root[j])
                    root = [new_root if r == old_root else r for r in root]
        for i, m in enumerate(group):
            target = group[root[i]]  # the first note of its set, in canonical order
            if target is m:
                m["canonical"] = m["meeting_id"]
                m["duplicates"] = []
            else:
                m["bucket"] = "duplicate_note"
                m["canonical"] = target["meeting_id"]
                target["duplicates"].append(m["meeting_id"])

    for m in b["meetings"]:
        if m["bucket"] == "included" and m["account_key"] is None:
            raise InputError("%s: $.meetings[%d].account_key: is required for a %s meeting; set it from the CRM "
                             "or the attendees' company (never guess)" % (b["label"], m["index"], m["kind"]))


def analyse(b, config, weighting, previous, week):
    by_id = dict((m["meeting_id"], m) for m in b["meetings"])
    accounts = b["accounts"]
    free = set(config["free_email_domains"])
    priority = dict((t, i) for i, t in enumerate(config["type_priority"]))

    # Duplicate notes point to their canonical meeting; their own account must agree.
    for m in b["meetings"]:
        if m["bucket"] == "duplicate_note":
            canon = by_id[m["canonical"]]
            if m["account_key"] is not None and m["account_key"] != canon["account_key"]:
                raise InputError("%s: $.meetings[%d].account_key: %r disagrees with %r on the other note of the "
                                 "same meeting (%s)" % (b["label"], m["index"], m["account_key"], canon["account_key"],
                                                        canon["meeting_id"]))

    dropped = []
    groups = {}
    order = []
    for it in b["items"]:
        m = by_id[it["meeting_id"]]
        if m["bucket"] == "duplicate_note":
            m = by_id[m["canonical"]]
        if m["bucket"] != "included":
            dropped.append((it, m))
            continue
        key = (m["account_key"], it["theme"], m["meeting_id"])
        if key not in groups:
            groups[key] = {"account": m["account_key"], "theme": it["theme"], "meeting": m, "items": [],
                           "own": 0, "copied": 0}
            order.append(key)
        groups[key]["items"].append(it)
        if it["meeting_id"] == m["meeting_id"]:
            groups[key]["own"] += 1
        else:
            groups[key]["copied"] += 1

    merged = []
    for key in order:
        g = groups[key]
        types = sorted(set(i["type"] for i in g["items"]), key=lambda t: priority[t])
        evidence = []
        seen = set()
        ordered = sorted(g["items"], key=lambda i: (0 if i["evidence"]["kind"] == "transcript" else 1, i["index"]))
        for it in ordered:
            norm = it["evidence"]["text"].lower()
            if norm in seen:
                continue
            seen.add(norm)
            evidence.append(it["evidence"])
        churn_evidence = [it["evidence"] for it in ordered if it["type"] == "churn_risk"]
        merged.append({
            "account": g["account"], "theme": g["theme"], "meeting": g["meeting"], "types": types,
            "type": types[0], "mentions": g["own"] or g["copied"], "evidence": evidence,
            "churn_evidence": churn_evidence[0] if churn_evidence else None,
            "competitors": sorted(set(i["competitor"] for i in g["items"] if i["competitor"])),
            "first_index": min(i["index"] for i in g["items"]),
        })

    def plan_weight(acc):
        plan = (acc.get("plan") or "").strip().lower()
        return float(config["plan_weights"].get(plan, config["default_plan_weight"]))

    themes = []
    for key, t in b["themes"].items():
        rows = [x for x in merged if x["theme"] == key]
        if not rows:
            continue
        acc_keys = sorted(set(x["account"] for x in rows), key=lambda k: (accounts[k]["name"].casefold(), k))
        type_counts = {}
        for x in rows:
            for ty in x["types"]:
                type_counts[ty] = type_counts.get(ty, 0) + 1
        primary = sorted(type_counts, key=lambda ty: (-type_counts[ty], priority[ty]))[0]
        arr_known = [accounts[k]["arr"] for k in acc_keys if accounts[k]["arr"] is not None]
        score = {"none": float(len(acc_keys)), "arr": float(sum(arr_known)),
                 "plan": sum(plan_weight(accounts[k]) for k in acc_keys)}[weighting]
        meetings = sorted(set(x["meeting"]["meeting_id"] for x in rows))
        best = None
        for mt in t["matches"]:
            if best is None or CONFIDENCES.index(mt["confidence"]) < CONFIDENCES.index(best["confidence"]):
                best = mt
        themes.append({
            "key": key, "title": t["title"], "description": t["description"], "rows": rows,
            "accounts": acc_keys, "account_count": len(acc_keys), "meeting_count": len(meetings),
            "mentions": sum(x["mentions"] for x in rows), "primary_type": primary, "type_counts": type_counts,
            "arr_sum": float(sum(arr_known)), "arr_known": len(arr_known), "score": score,
            "best_match": best, "theme": t,
            "customer_accounts": [k for k in acc_keys if accounts[k]["status"] == "customer"],
        })

    themes.sort(key=lambda th: (-th["score"], -th["account_count"], -th["meeting_count"], -th["mentions"],
                                priority[th["primary_type"]], th["key"]))

    # Trend versus the previous digest.
    prev_themes = previous["themes"] if previous else {}
    week_long = (b["period_end"] - b["period_start"]).days == 6  # growth against a week only means something for 7 days
    for th in themes:
        if previous is None:
            th["trend"] = "first"
            th["delta"] = None
        elif th["key"] in prev_themes:
            before = prev_themes[th["key"]].get("accounts", 0)
            th["delta"] = th["account_count"] - before if week_long else None
            th["trend"] = "growing" if week_long and th["account_count"] > before else "recurring"
        elif th["key"] in previous.get("ever_seen", set()):
            th["trend"] = "returning"
            th["delta"] = None
        else:
            th["trend"] = "new"
            th["delta"] = None
    current_keys = set(th["key"] for th in themes)
    quieter = []
    if previous:
        for key in sorted(prev_themes, key=lambda k: (-prev_themes[k].get("accounts", 0), k)):
            if key not in current_keys:
                quieter.append({"key": key, "title": prev_themes[key].get("title", key),
                                "accounts": prev_themes[key].get("accounts", 0)})

    top_all = [th for th in themes if th["account_count"] >= config["top_min_accounts"]]
    top = top_all[:config["max_top_themes"]]
    singles_all = [th for th in themes if th["account_count"] < config["top_min_accounts"]]
    singles_all.sort(key=lambda th: (priority[th["primary_type"]], -th["score"], -th["account_count"], th["key"]))
    singles = singles_all[:config["max_single_mentions"]]

    # Issue drafts for unmatched high-signal themes.
    drafts = []
    for th in themes:
        best = th["best_match"]
        if best is not None and best["confidence"] in ("high", "medium"):
            continue
        if th["primary_type"] not in config["draftable_types"]:
            continue
        customer_urgent = any(
            ty in config["draft_if_customer_types"] and accounts[x["account"]]["status"] == "customer"
            for x in th["rows"] for ty in x["types"])
        if th["account_count"] >= config["draft_min_accounts"] or customer_urgent:
            th["draft_id"] = "D%d" % (len(drafts) + 1)
            drafts.append(th)

    churn = [x for x in merged if "churn_risk" in x["types"]]
    churn.sort(key=lambda x: (x["meeting"]["start"].astimezone(timezone.utc), accounts[x["account"]]["name"].casefold(),
                              x["account"], x["theme"]))
    competitors = {}  # one row per competitor, whatever the case or width it was written in
    for x in sorted(merged, key=lambda r: r["first_index"]):
        for c in x["competitors"]:
            entry = competitors.setdefault(name_key(c), {"name": c, "accounts": set(), "meetings": set()})
            entry["accounts"].add(x["account"])
            entry["meetings"].add(x["meeting"]["meeting_id"])
    comp_rows = sorted(((e["name"], e) for e in competitors.values()),
                       key=lambda kv: (-len(kv[1]["accounts"]), kv[0].casefold(), kv[0]))

    included = [m for m in b["meetings"] if m["bucket"] == "included"]
    with_items = set(x["meeting"]["meeting_id"] for x in merged)
    attribution_warnings = []
    for m in included:
        acc = accounts[m["account_key"]]
        if not acc["domains"]:
            continue
        ext_domains = set(domain_of(a["email"]) for a in m["external"])
        business = [d for d in ext_domains if d not in free]
        if business and not any(in_domains(d, acc["domains"]) for d in business):
            attribution_warnings.append((m, acc, sorted(business)))
    # One company filed under two account keys (e.g. accounts without domains) would be counted twice.
    domain_accounts = {}
    for m in included:
        for d in set(domain_of(a["email"]) for a in m["external"]):
            if not in_domains(d, free):
                domain_accounts.setdefault(d, set()).add(m["account_key"])
    shared_domains = [(d, sorted(keys, key=lambda k: (accounts[k]["name"].casefold(), k)))
                      for d, keys in sorted(domain_accounts.items()) if len(keys) > 1]

    return {
        "week": week, "themes": themes, "top": top, "top_overflow": len(top_all) - len(top), "singles": singles,
        "singles_overflow": len(singles_all) - len(singles), "quieter": quieter[:config["max_quieter_themes"]],
        "quieter_overflow": max(0, len(quieter) - config["max_quieter_themes"]),
        "drafts": drafts, "churn": churn, "competitors": comp_rows, "merged": merged, "dropped": dropped,
        "included": included, "with_items": with_items, "attribution_warnings": attribution_warnings,
        "shared_domains": shared_domains,
        "previous": previous, "weighting": weighting,
        "raw_items": len(b["items"]) - len(dropped),
    }


# --------------------------------------------------------------------------- state (idempotency + trends)


def empty_state():
    return {"schema_version": 1, "skill": STATE_SKILL, "digests": []}


def read_state(path):
    """Missing file -> empty state. Unreadable, foreign or malformed -> InputError (never run blind)."""
    if path is None:
        return empty_state()
    st = stat_path(path, "state")
    if st is None:
        if os.path.islink(str(path)):
            raise InputError("state %s: is a link to a file that does not exist; refusing to run blind" % path)
        return empty_state()
    if not stat.S_ISREG(st.st_mode):
        raise InputError("state %s: is not a file" % path)
    try:
        data = load_json(path, "state")
    except InputError as exc:
        raise InputError("%s; refusing to run blind" % exc) from None
    if not isinstance(data, dict) or data.get("skill") != STATE_SKILL or data.get("schema_version") != 1 \
            or isinstance(data.get("schema_version"), bool) or not isinstance(data.get("digests"), list):
        raise InputError("state %s: not a customer-feedback-digest state file (expected skill %r, schema_version 1)"
                         % (path, STATE_SKILL))
    seen = set()
    for i, d in enumerate(data["digests"]):
        p = "$.digests[%d]" % i
        bad = InputError("state %s: %s is malformed (needs week YYYY-Www, period_start/period_end of that "
                         "Monday-Sunday week, status pending|posted, themes object); fix or restore it" % (path, p))
        if not isinstance(d, dict) or not isinstance(d.get("week"), str) or not WEEK_RE.match(d["week"]) \
                or d.get("status") not in ("pending", "posted") or not isinstance(d.get("themes"), dict) \
                or d["week"] in seen:
            raise bad
        seen.add(d["week"])
        start = parse_day(d.get("period_start"), lambda problem: bad)
        end = parse_day(d.get("period_end"), lambda problem: bad)
        if not is_full_week(start, end) or iso_week(end) != d["week"]:
            raise bad
        d["_start"], d["_end"] = start, end
        for field in ["rendered_at", "posted_at"]:
            if d.get(field) is not None:
                parse_moment(d[field], lambda problem: InputError("state %s: %s.%s: %s" % (path, p, field, problem)))
        if d.get("post_ref") is not None and not isinstance(d["post_ref"], str):
            raise InputError("state %s: %s.post_ref must be a string or null" % (path, p))
        for key, t in d["themes"].items():
            if not KEY_RE.match(key) or not isinstance(t, dict) or not isinstance(t.get("title"), str) \
                    or isinstance(t.get("accounts"), bool) or not isinstance(t.get("accounts"), int) \
                    or t["accounts"] < 0:
                raise InputError("state %s: %s.themes.%s must be {title: string, accounts: integer, mentions}"
                                 % (path, p, key))
            t["title"] = redact(clean_text(t["title"])) or key
    return data


def check_writable(path):
    """Before printing anything: the state file (and its lock and temp file) can be written."""
    st = stat_path(path, "state")
    if st is not None and not stat.S_ISREG(st.st_mode):
        raise InputError("state %s: is not a file" % path)
    parent = Path(os.path.abspath(str(path))).parent
    if not os.path.isdir(str(parent)):
        raise InputError("state %s: folder %s does not exist" % (path, parent))
    if not os.access(str(parent), os.W_OK | os.X_OK) or (st is not None and not os.access(str(path), os.W_OK)):
        raise InputError("state %s: not writable; fix permissions or pass a writable --state" % path)


def write_state(path, state):
    for d in state["digests"]:
        d.pop("_start", None)
        d.pop("_end", None)
    state["digests"].sort(key=lambda d: d["week"])
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as exc:
        raise InputError("state %s: cannot be written (%s); nothing was printed or recorded"
                         % (path, exc.strerror or exc)) from None


class StateLock(object):
    """Exclusive <state>.lock (O_CREAT|O_EXCL) around a state read-modify-write.

    The lock file holds this run's pid and a random token. A lock whose modification time is more than
    LOCK_STALE_MINUTES old, or more than a minute in the future, is treated as left over by a crashed run. It is
    broken at most once, under a short-lived <state>.lock.takeover guard, and only if it is still the same file
    (device, inode, mtime and contents) that was judged stale. A run only ever removes a lock holding its own token.
    A folder (or anything but a regular file) at the lock path is an input error."""

    def __init__(self, state_path, as_of):
        self.path = state_path.with_name(state_path.name + ".lock")
        self.guard = state_path.with_name(state_path.name + ".lock.takeover")
        self.note = ("%d %s %s\n" % (os.getpid(), as_of.isoformat(), os.urandom(8).hex())).encode("ascii")
        self.mine = None

    def _identity(self, path=None):
        """(device, inode, mtime_ns, contents) of the file at the lock path, or None when there is none."""
        path = str(path or self.path)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise InputError("state lock %s: cannot be read (%s)" % (path, exc.strerror or exc)) from None
        if not stat.S_ISREG(st.st_mode):
            raise InputError("state lock %s: exists but is not a regular file (a folder?); remove it, then rerun"
                             % path)
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise InputError("state lock %s: cannot be read (%s)" % (path, exc.strerror or exc)) from None
        try:
            st = os.fstat(fd)
            return (st.st_dev, st.st_ino, st.st_mtime_ns, os.read(fd, 4096))
        finally:
            os.close(fd)

    def _guarded(self, action):
        """Run action() while holding the takeover guard; None when another run holds it."""
        self._identity(self.guard)  # a folder at the guard path is an input error, not a silent hang
        try:
            fd = os.open(str(self.guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:  # the guard is held for milliseconds; one older than a minute was left by a crash
                if abs(time.time() - os.lstat(str(self.guard)).st_mtime) > 60:
                    os.remove(str(self.guard))
            except OSError:
                pass
            return None
        except OSError as exc:
            raise InputError("state lock %s: cannot be created (%s)" % (self.guard, exc.strerror or exc)) from None
        try:
            return action()
        finally:
            os.close(fd)
            try:
                os.remove(str(self.guard))
            except OSError:
                pass

    def _break_stale(self, seen):
        """Remove the stale lock `seen`, only if the lock path still holds exactly that file."""
        def action():
            if self._identity() != seen:
                return False
            try:
                os.remove(str(self.path))
            except OSError:
                return False
            return True
        return self._guarded(action) is True

    def __enter__(self):
        for attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                seen = self._identity()
                if seen is None:
                    continue
                age = time.time() - seen[2] / 1e9
                if attempt == 0 and (age > LOCK_STALE_MINUTES * 60 or age < -60) and self._break_stale(seen):
                    continue
                raise StopRun(EXIT_LOCKED, "another run is updating the state file (lock %s exists). Retry in a "
                              "minute; if no run is active, a lock is treated as stale after %d minutes, or delete "
                              "it." % (self.path, LOCK_STALE_MINUTES))
            except OSError as exc:
                raise InputError("state lock %s: cannot be created (%s)" % (self.path, exc.strerror or exc)) from None
            try:
                os.write(fd, self.note)
                st = os.fstat(fd)
            finally:
                os.close(fd)
            self.mine = (st.st_dev, st.st_ino)
            return self
        raise StopRun(EXIT_LOCKED, "could not take the state lock %s; retry in a minute" % self.path)

    def __exit__(self, *exc):
        if self.mine is None:
            return False

        def release():
            try:
                current = self._identity()
            except InputError:
                return True
            if current is not None and current[:2] == self.mine and current[3] == self.note:
                try:
                    os.remove(str(self.path))
                except OSError:
                    pass
            return True
        for _ in range(50):  # removals are serialised with stale takeovers through the guard
            try:
                if self._guarded(release):
                    break
            except InputError:  # the guard path is unusable (a folder): still remove our own lock
                release()
                break
            time.sleep(0.02)
        else:
            release()
        self.mine = None
        return False


def previous_digest(state, start):
    """The latest recorded week that ended before this period started."""
    earlier = [d for d in state["digests"] if d["_end"] < start]
    if not earlier:
        return None
    latest = sorted(earlier, key=lambda d: d["_end"])[-1]
    ever = set()
    for d in earlier:
        ever.update(d["themes"].keys())
    prev = dict(latest)
    prev["ever_seen"] = ever
    return prev


def snapshot(a):
    return dict((th["key"], {"title": th["title"], "accounts": th["account_count"], "mentions": th["mentions"]})
                for th in a["themes"])


# --------------------------------------------------------------------------- rendering


def provider_label(p):
    if not p:
        return ""
    return PROVIDER_LABELS.get(p, p)


def account_label(acc, with_details=True):
    """Plain text; escape for the output format at the call site."""
    bits = []
    if with_details:
        for value in [acc["plan"], acc["segment"], acc["status"] if acc["status"] in ("prospect", "churned") else None]:
            if value and value.lower() not in [x.lower() for x in bits]:
                bits.append(value)
    return "%s (%s)" % (acc["name"], ", ".join(bits)) if bits else acc["name"]


def meeting_cite(m):
    return md_link("%s, %s" % (m["title"], short_date(local_day(m))), m["url"])


def evidence_line(ev, acc, m):
    who = ev["speaker_role"]
    if ev["speaker_side"] == "unconfirmed":
        who += " (speaker unconfirmed)"
    if ev["kind"] == "transcript":
        return "“%s” — %s, %s · %s" % (md(ev["text"]), md(who), md(acc["name"]), meeting_cite(m))
    return "Note excerpt (AI meeting notes, not verbatim speech): _%s_ — %s · %s" % (
        md(ev["text"]), md(acc["name"]), meeting_cite(m))


def trend_text(th, previous):
    if th["trend"] == "first":
        return "first digest"
    if th["trend"] == "growing":
        return "growing (+%d vs %s)" % (th["delta"], previous["week"])
    if th["trend"] == "recurring":
        return "recurring (also in %s)" % previous["week"]
    if th["trend"] == "returning":
        return "returning (not in %s)" % previous["week"]
    return "new"


def theme_quotes(th, config):
    """Pick quotes: one per account first (transcript before note), then fill up to the cap."""
    picked, used_accounts = [], set()
    rows = sorted(th["rows"], key=lambda x: (0 if x["evidence"][0]["kind"] == "transcript" else 1,
                                             x["meeting"]["start"].astimezone(timezone.utc), x["first_index"]))
    for x in rows:
        if x["account"] not in used_accounts:
            picked.append((x["evidence"][0], x))
            used_accounts.add(x["account"])
    for x in rows:
        for ev in x["evidence"]:
            if len(picked) >= config["max_quotes_per_theme"]:
                break
            if all(ev is not p[0] for p in picked):
                picked.append((ev, x))
    return picked[:config["max_quotes_per_theme"]]


def match_lines(th, sources, config):
    t = th["theme"]
    best = th["best_match"]
    tracker = sources["issue_tracker"]
    lines = []
    if best is None and t["search_status"] == "not_searched" and th["primary_type"] not in config["draftable_types"]:
        return lines
    if best is not None:
        label = {"high": "match: high", "medium": "likely match (medium) — confirm before linking",
                 "low": "possible match (low confidence)"}[best["confidence"]]
        state = " — %s" % md(best["state"]) if best["state"] else ""
        lines.append("Issue: %s%s · %s — %s" % (md_link("%s %s" % (best["id"], best["title"]), best["url"]), state,
                                               label, md(best["reason"])))
        others = [mt for mt in t["matches"] if mt is not best]
        if others:
            lines.append("Other candidates: " + "; ".join(
                "%s (%s)" % (md_link(mt["id"], mt["url"]), mt["confidence"]) for mt in others))
    elif t["search_status"] == "searched":
        q = " for “%s”" % md(t["search_query"]) if t["search_query"] else ""
        lines.append("Issue: no matching issue found in %s%s." % (md(provider_label(tracker["provider"]))
                                                                  or "the tracker", q))
    elif tracker["status"] != "ok" or t["search_status"] == "unavailable":
        lines.append("Issue: tracker not searched (%s)." % md(t["search_detail"] or tracker["detail"] or
                                                              tracker["status"].replace("_", " ")))
    else:
        lines.append("Issue: not searched for this theme.")
    if th.get("draft_id"):
        lines.append("Draft: see %s below (not created)." % th["draft_id"])
    return lines


def weighting_note(th, a, config):
    if a["weighting"] == "arr":
        return " · ARR %s (known for %d of %d)" % (md(money(config["currency_symbol"], th["arr_sum"])), th["arr_known"],
                                                   th["account_count"])
    if a["weighting"] == "plan":
        return " · plan-weighted score %s" % ("{:g}".format(th["score"]))
    return ""


def source_status_text(key, src):
    label = SOURCE_LABELS[key]
    prov = md(provider_label(src["provider"]))
    if src["status"] == "ok":
        return "%s: %s ✓" % (label, prov) if prov else "%s ✓" % label
    status = src["status"].replace("_", " ")
    return "%s: %s %s" % (label, prov, status) if prov and src["provider"] != "none" else "%s: %s" % (label, status)


def coverage_lines(b, a, as_of, internal_domains, config):
    ms = b["meetings"]
    count = lambda bucket: [m for m in ms if m["bucket"] == bucket]
    included = a["included"]
    lines = []
    lines.append("- Period: %s (%s), as of %s." % (period_text(b["period_start"], b["period_end"]), a["week"],
                                                   as_of.isoformat()))
    dup = count("duplicate_note")
    dup_text = ", %d duplicate note%s of the same call merged" % (len(dup), "" if len(dup) == 1 else "s") if dup else ""
    lines.append("- Meetings listed: %d%s." % (len(ms), dup_text))
    acc_count = len(set(m["account_key"] for m in included))
    quiet = [m for m in included if m["meeting_id"] not in a["with_items"]]
    lines.append("- Scanned (external customer/prospect): %d meeting%s, %d account%s; %d with no feedback items."
                 % (len(included), "" if len(included) == 1 else "s", acc_count, "" if acc_count == 1 else "s",
                    len(quiet)))
    lines.append("- Skipped, internal-only (all attendees on %s): %d."
                 % (md(", ".join(internal_domains)), len(count("internal_only"))))
    nc = count("not_customer")
    if nc:
        kinds = {}
        for m in nc:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        lines.append("- Skipped, external but not a customer or prospect: %d (%s)."
                     % (len(nc), ", ".join("%s %d" % (k, kinds[k]) for k in sorted(kinds))))
    else:
        lines.append("- Skipped, external but not a customer or prospect: 0.")
    for bucket, text in [("unclassified", "External, relationship not classified (not scanned — classify and rerun)"),
                         ("attendees_unknown", "Attendees unknown (only the note taker listed; not scanned)")]:
        group = count(bucket)
        if group:
            lines.append("- %s: %s." % (text, "; ".join(meeting_cite(m) for m in group)))
    out = count("outside_period")
    if out:
        lines.append("- Outside the period (ignored): %d." % len(out))
    tstat = {}
    for m in included:
        tstat.setdefault(m["transcript"], []).append(m)
    parts = []
    for st, label in [("ok", "read"), ("denied", "denied"), ("unavailable", "unavailable"),
                      ("not_requested", "not requested")]:
        if tstat.get(st):
            parts.append("%d %s" % (len(tstat[st]), label))
    lines.append("- Transcripts: %s." % (", ".join(parts) if parts else "none"))
    denied = tstat.get("denied", []) + tstat.get("unavailable", [])
    if denied:
        lines.append("- Transcript denied or unavailable, so evidence there is note excerpts only (not \"not "
                     "discussed\"): %s." % "; ".join(meeting_cite(m) for m in denied))
    no_notes = [m for m in included if m["notes"] == "unavailable"]
    if no_notes:
        lines.append("- Notes unavailable: %s." % "; ".join(meeting_cite(m) for m in no_notes))
    gaps = ["%s – %s: %s" % (short_date(g["start"]), short_date(g["end"]), md(g["reason"])) for g in b["date_gaps"]]
    hist = b["sources"]["meetings"]["history_days"]
    if hist is not None:
        oldest = as_of.date() - timedelta(days=hist)
        if b["period_start"] < oldest:
            gaps.append("before %s: notes older than %d days may be unavailable on this plan"
                        % (short_date(oldest), hist))
    lines.append("- Date gaps: %s." % ("; ".join(gaps) if gaps else "none reported"))
    if a["dropped"]:
        reasons = {}
        for it, m in a["dropped"]:
            reasons[m["bucket"]] = reasons.get(m["bucket"], 0) + 1
        lines.append("- Items dropped because their meeting was not scanned: %s."
                     % ", ".join("%d (%s)" % (reasons[k], k.replace("_", " ")) for k in sorted(reasons)))
    for m, acc, doms in a["attribution_warnings"]:
        lines.append("- ⚠️ Check attribution: %s is filed under %s, but external attendees are on %s."
                     % (meeting_cite(m), md(acc["name"]), md(", ".join(doms))))
    for d, keys in a["shared_domains"]:
        lines.append("- ⚠️ Same company under two accounts? Attendees from %s are in meetings filed under %s; if they "
                     "are one company, merge the accounts, or each counts as a separate account."
                     % (md(d), md(" and ".join(b["accounts"][k]["name"] for k in keys))))
    if b["link_warnings"]:
        lines.append("- ⚠️ Links dropped (not an http(s) URL, or unsafe): %d (%s); the text is shown without a link."
                     % (len(b["link_warnings"]), ", ".join(b["link_warnings"])))
    return lines


def render_markdown(b, a, config, as_of, internal_domains):
    accounts = b["accounts"]
    out = []
    out.append("# Customer Feedback Digest — %s (%s)" % (a["week"], period_text(b["period_start"], b["period_end"])))
    out.append("")
    n_meet = len(a["included"])
    n_acc = len(set(m["account_key"] for m in a["included"]))
    listing = b["sources"]["meetings"]
    if listing["status"] != "ok":
        out.append("**⚠️ Coverage incomplete** — the meeting list (%s) is %s%s, so meetings may be missing. This is "
                   "not a complete record of the period." % (md(provider_label(listing["provider"]) or "recorder"),
                                                             listing["status"].replace("_", " "),
                                                             " (%s)" % md(listing["detail"]) if listing["detail"] else ""))
        out.append("")
    if n_meet == 0 and listing["status"] != "ok":
        out.append("**At a glance** — No external customer or prospect meetings could be read; the meeting list "
                   "is incomplete, so this does not mean there were none. See Coverage.")
    elif n_meet == 0:
        out.append("**At a glance** — No external customer or prospect meetings found in this period. "
                   "See Coverage for what was skipped and why.")
    else:
        bits = ["%d customer and prospect meeting%s with %d account%s scanned"
                % (n_meet, "" if n_meet == 1 else "s", n_acc, "" if n_acc == 1 else "s")]
        if a["top"]:
            lead = a["top"][0]
            bits.append("%d theme%s raised by %d+ accounts; top: %s — %d accounts, %s"
                        % (len(a["top"]), "" if len(a["top"]) == 1 else "s", config["top_min_accounts"], md(lead["title"]),
                           lead["account_count"], trend_text(lead, a["previous"])))
        elif a["themes"]:
            bits.append("no theme came up with more than one account")
        else:
            bits.append("no feedback items extracted")
        if a["churn"]:
            bits.append("%d churn-risk signal%s" % (len(a["churn"]), "" if len(a["churn"]) == 1 else "s"))
        if a["drafts"]:
            bits.append("%d issue draft%s awaiting approval" % (len(a["drafts"]), "" if len(a["drafts"]) == 1 else "s"))
        out.append("**At a glance** — " + ". ".join(bits) + ".")
    if a["previous"] is None:
        out.append("")
        out.append("_No previous digest on record, so every theme is marked “first digest”; trends start next time._")
    rank_note = {"none": "Ranked by distinct accounts, not raw mentions.",
                 "arr": "Ranked by ARR of distinct accounts (unknown ARR counts as 0), then distinct accounts.",
                 "plan": "Ranked by plan-weighted distinct accounts, then distinct accounts."}[a["weighting"]]

    out.append("")
    out.append("## Top themes")
    out.append("_%s One account mentioning something five times counts once._" % rank_note)
    out.append("")
    if not a["top"]:
        out.append("- None this period: no theme was raised by %d or more accounts." % config["top_min_accounts"])
    for n, th in enumerate(a["top"], 1):
        out.append("### %d. %s" % (n, md(th["title"])))
        out.append("%s · %d accounts · %d meeting%s · %d mention%s · %s%s" % (
            md(config["feedback_types"][th["primary_type"]]), th["account_count"], th["meeting_count"],
            "" if th["meeting_count"] == 1 else "s", th["mentions"], "" if th["mentions"] == 1 else "s",
            trend_text(th, a["previous"]), weighting_note(th, a, config)))
        if th["description"]:
            out.append("")
            out.append(md(th["description"]))
        out.append("")
        out.append("Accounts: " + "; ".join(md(account_label(accounts[k])) for k in th["accounts"]))
        for ev, x in theme_quotes(th, config):
            out.append("- " + evidence_line(ev, accounts[x["account"]], x["meeting"]))
        for line in match_lines(th, b["sources"], config):
            out.append(line)
        out.append("")
    if a["top_overflow"]:
        out.append("- …and %d more theme%s raised by %d+ accounts (see the JSON output)."
                   % (a["top_overflow"], "" if a["top_overflow"] == 1 else "s", config["top_min_accounts"]))
        out.append("")
    if out[-1] != "":
        out.append("")

    out.append("## Churn-risk signals")
    if not a["churn"]:
        out.append("- None captured in the meetings scanned.")
    for x in a["churn"]:
        acc = accounts[x["account"]]
        ev = x["churn_evidence"]
        out.append("- **%s** — %s" % (md(account_label(acc)), evidence_line(ev, acc, x["meeting"])))
    out.append("")

    out.append("## Competitors mentioned")
    if not a["competitors"]:
        out.append("- None captured in the meetings scanned.")
    else:
        out.append("| Competitor | Accounts | Meetings |")
        out.append("|---|---|---|")
        for name, entry in a["competitors"]:
            out.append("| %s | %d (%s) | %d |" % (md(name), len(entry["accounts"]), md(", ".join(
                sorted((accounts[k]["name"] for k in entry["accounts"]), key=lambda n: (n.casefold(), n)))),
                len(entry["meetings"])))
    out.append("")

    out.append("## New-issue drafts (not created — reply e.g. “approve D1” to create one)")
    if not a["drafts"]:
        out.append("- None: every high-signal theme already has a likely issue, or no theme met the bar "
                   "(%d+ accounts, or a bug from a paying customer)." % config["draft_min_accounts"])
    tracker = b["sources"]["issue_tracker"]
    for th in a["drafts"]:
        out.append("### %s. [%s] %s" % (th["draft_id"], md(config["feedback_types"][th["primary_type"]]),
                                        md(th["title"])))
        if tracker["status"] != "ok" or th["theme"]["search_status"] != "searched":
            out.append("_Not checked for duplicates — the issue tracker was not searched for this theme._")
        if th["best_match"] is not None:
            out.append("_Low-confidence candidate %s exists; check it before creating._" % md(th["best_match"]["id"]))
        out.append("")
        body = draft_body(th, b, a, config)
        out.append(fence_for(body) + "markdown")
        out.extend(body)
        out.append(fence_for(body))
        out.append("")
    if out[-1] != "":
        out.append("")

    out.append("## Notable single mentions")
    if not a["singles"]:
        out.append("- None.")
    for th in a["singles"]:
        x = sorted(th["rows"], key=lambda r: (0 if r["evidence"][0]["kind"] == "transcript" else 1, r["first_index"]))[0]
        acc = accounts[x["account"]]
        out.append("- **%s** (%s, %s) — %s" % (md(th["title"]), md(config["feedback_types"][th["primary_type"]]),
                                                trend_text(th, a["previous"]),
                                                evidence_line(x["evidence"][0], acc, x["meeting"])))
    if a["singles_overflow"]:
        out.append("- …and %d more single mentions (see the JSON output)." % a["singles_overflow"])
    out.append("")

    if a["previous"] is not None:
        out.append("## Quieter than %s" % a["previous"]["week"])
        if not a["quieter"]:
            out.append("- Every theme from the previous digest came up again.")
        for q in a["quieter"]:
            out.append("- %s (was %d account%s)" % (md(q["title"]), q["accounts"], "" if q["accounts"] == 1 else "s"))
        if a["quieter_overflow"]:
            out.append("- …and %d more theme%s from the previous digest did not come up."
                       % (a["quieter_overflow"], "" if a["quieter_overflow"] == 1 else "s"))
        out.append("")

    out.append("## Coverage")
    out.extend(coverage_lines(b, a, as_of, internal_domains, config))
    out.append("")
    out.append("Sources: " + " · ".join(source_status_text(k, b["sources"][k]) for k in SOURCE_KEYS))
    gaps = ["%s %s%s" % (SOURCE_LABELS[k], b["sources"][k]["status"].replace("_", " "),
                         " (%s)" % md(b["sources"][k]["detail"]) if b["sources"][k]["detail"] else "")
            for k in SOURCE_KEYS if b["sources"][k]["status"] != "ok"]
    out.append("Gaps: %s." % ("; ".join(gaps) if gaps else "none"))
    out.append("Digest key: %s · %d feedback items → %d after dedupe (same account + theme + meeting)"
               % (a["week"], a["raw_items"], len(a["merged"])))
    out.append("_Internal only — contains customer quotes. Quotes in “ ” are verbatim from transcripts; note excerpts "
               "are from AI meeting notes._")
    return "\n".join(out) + "\n"


def draft_body(th, b, a, config):
    accounts = b["accounts"]
    lines = []
    lines.append("Title: %s" % md(th["title"]))
    lines.append("Labels: customer-feedback, %s" % th["primary_type"].replace("_", "-"))
    lines.append("")
    lines.append("**Source:** Customer Feedback Digest %s — %d account%s, %d mention%s across %d meeting%s (%s)."
                 % (a["week"], th["account_count"], "" if th["account_count"] == 1 else "s", th["mentions"],
                    "" if th["mentions"] == 1 else "s", th["meeting_count"], "" if th["meeting_count"] == 1 else "s",
                    period_text(b["period_start"], b["period_end"])))
    if th["description"]:
        lines.append("")
        lines.append("**What customers are asking for:** %s" % md(th["description"]))
    lines.append("")
    lines.append("**Accounts:** " + "; ".join(md(account_label(accounts[k])) for k in th["accounts"]))
    lines.append("")
    lines.append("**Evidence:**")
    for x in sorted(th["rows"], key=lambda r: (r["meeting"]["start"].astimezone(timezone.utc), r["first_index"])):
        acc = accounts[x["account"]]
        for ev in x["evidence"][:1]:
            lines.append("- " + evidence_line(ev, acc, x["meeting"]))
    return lines


def render_slack(b, a, config, as_of, doc_url):
    accounts = b["accounts"]
    out = []
    out.append("*Customer feedback digest — %s (%s)*" % (a["week"], period_text(b["period_start"], b["period_end"])))
    listing = b["sources"]["meetings"]
    if listing["status"] != "ok":
        out.append("⚠️ Coverage incomplete: the meeting list is %s, so meetings may be missing."
                   % listing["status"].replace("_", " "))
    n_meet = len(a["included"])
    n_acc = len(set(m["account_key"] for m in a["included"]))
    out.append("%d customer/prospect meeting%s · %d account%s · %d churn-risk signal%s · %d issue draft%s awaiting approval"
               % (n_meet, "" if n_meet == 1 else "s", n_acc, "" if n_acc == 1 else "s", len(a["churn"]),
                  "" if len(a["churn"]) == 1 else "s", len(a["drafts"]), "" if len(a["drafts"]) == 1 else "s"))
    out.append("")
    out.append("*Top themes (distinct accounts)*")
    if not a["top"]:
        out.append("• None this period: no theme was raised by %d or more accounts." % config["top_min_accounts"])
    for n, th in enumerate(a["top"][:5], 1):
        best = th["best_match"]
        issue = ""
        if best is not None and best["confidence"] in ("high", "medium"):
            issue = " · %s%s" % (slack_link(best["id"], best["url"]), " (%s)" % slack(best["state"]) if best["state"]
                                 else "")
        elif th.get("draft_id"):
            issue = " · draft %s" % th["draft_id"]
        out.append("%d. %s — %d accounts · %s%s" % (n, slack(th["title"]), th["account_count"],
                                                     slack(trend_text(th, a["previous"])), issue))
    if a["churn"]:
        out.append("")
        out.append("*Churn risk*")
        for x in a["churn"]:
            m = x["meeting"]
            out.append("• %s — %s" % (slack(accounts[x["account"]]["name"]),
                                      slack_link("%s, %s" % (m["title"], short_date(local_day(m))), m["url"])))
    tstat = [m for m in a["included"] if m["transcript"] in ("denied", "unavailable")]
    out.append("")
    gaps = [SOURCE_LABELS[k] for k in SOURCE_KEYS if b["sources"][k]["status"] != "ok"]
    out.append("Coverage: %d internal-only skipped · %d transcript%s denied/unavailable · source gaps: %s"
               % (len([m for m in b["meetings"] if m["bucket"] == "internal_only"]), len(tstat),
                  "" if len(tstat) == 1 else "s", ", ".join(gaps) if gaps else "none"))
    out.append("Full digest with quotes and coverage: %s" % (slack_url(doc_url) if doc_url else "(link to the Type doc)"))
    return "\n".join(out) + "\n"


def json_ready(b, a, markdown):
    accounts = b["accounts"]

    def theme_obj(th):
        best = th["best_match"]
        return {
            "key": th["key"], "title": th["title"], "primary_type": th["primary_type"],
            "accounts": [accounts[k]["name"] for k in th["accounts"]], "account_count": th["account_count"],
            "meeting_count": th["meeting_count"], "mentions": th["mentions"], "trend": th["trend"],
            "delta": th["delta"], "score": th["score"], "arr_sum": th["arr_sum"],
            "issue": None if best is None else {k: best[k] for k in ["provider", "id", "title", "url", "state",
                                                                   "confidence"]},
            "draft_id": th.get("draft_id"),
            "evidence": [{"account": accounts[x["account"]]["name"], "meeting_id": x["meeting"]["meeting_id"],
                          "meeting_url": x["meeting"]["url"], "kind": e["kind"], "text": e["text"],
                          "speaker_role": e["speaker_role"]} for x in th["rows"] for e in x["evidence"]],
        }

    return {
        "status": "rendered", "week": a["week"],
        "period": {"start": b["period_start"].isoformat(), "end": b["period_end"].isoformat()},
        "top_themes": [theme_obj(th) for th in a["top"]],
        "all_themes": [theme_obj(th) for th in a["themes"]],
        "drafts": [{"id": th["draft_id"], "theme": th["key"], "title": th["title"]} for th in a["drafts"]],
        "churn_signals": len(a["churn"]),
        "coverage": {
            "meetings_listed": len(b["meetings"]),
            "scanned": len(a["included"]),
            "buckets": dict((k, len([m for m in b["meetings"] if m["bucket"] == k]))
                            for k in ["included", "internal_only", "not_customer", "unclassified",
                                      "attendees_unknown", "outside_period", "duplicate_note"]),
            "items_raw": a["raw_items"], "items_deduped": len(a["merged"]), "items_dropped": len(a["dropped"]),
        },
        "markdown": markdown,
    }


# --------------------------------------------------------------------------- main


def utf8_streams():
    """UTF-8 output whatever the locale says (PYTHONIOENCODING=ascii, LANG=C); never fails on odd streams."""
    for stream, errors in ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors=errors)
            except (ValueError, OSError):
                pass


def emit(stream, text):
    """Write and flush; False when the stream is closed or its reader went away (broken pipe)."""
    try:
        stream.write(text)
        stream.flush()
        return True
    except (AttributeError, OSError, ValueError):
        try:  # point the dead stream at devnull so the interpreter's final flush cannot fail again
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, stream.fileno())
        except (AttributeError, OSError, ValueError):
            pass
        return False


def main(argv=None):
    utf8_streams()
    parser = argparse.ArgumentParser(description="Render a customer feedback digest from one JSON bundle.")
    parser.add_argument("--bundle", type=Path, help="Bundle JSON (see DATA_CONTRACT.md).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="digest.json")
    parser.add_argument("--as-of", help="Run time, ISO datetime with UTC offset. The only 'now'.")
    parser.add_argument("--internal-domain", action="append", default=[],
                        help="Your company's email domain; repeat for each. Subdomains count as internal.")
    parser.add_argument("--internal-email", action="append", default=[],
                        help="A colleague's address outside the internal domains (e.g. a personal address).")
    parser.add_argument("--weighting", choices=WEIGHTINGS, default="none")
    parser.add_argument("--format", choices=FORMATS, default="markdown")
    parser.add_argument("--doc-url", help="Link to the full digest doc, used in --format slack.")
    parser.add_argument("--state", type=Path, help="State file for trends and the weekly two-phase post record.")
    parser.add_argument("--scheduled", action="store_true",
                        help="Scheduled run (Monday-Sunday period only): skip a posted week, refuse a pending one "
                             "(exit 4), else record the week as pending in --state before printing.")
    parser.add_argument("--resume-pending", action="store_true",
                        help="With --scheduled: re-render a week left pending by an earlier run.")
    parser.add_argument("--dry-run", action="store_true", help="Read the state file but never write it.")
    parser.add_argument("--confirm-posted", metavar="WEEK", help="Mark WEEK (YYYY-Www) as posted in --state.")
    parser.add_argument("--post-ref", help="Permalink of the post, stored with --confirm-posted.")
    parser.add_argument("--known-themes", action="store_true", help="Print theme keys already in --state and exit.")
    args = parser.parse_args(argv)
    if args.state is not None and os.path.islink(str(args.state)) and os.path.exists(str(args.state)):
        args.state = Path(os.path.realpath(str(args.state)))  # lock and update the file itself, never replace the link

    # Everything is validated, rendered and (for scheduled runs) recorded before anything is printed.
    notes = []
    staged = False
    try:
        if args.known_themes or args.confirm_posted:
            if args.state is None:
                raise InputError("--state is required with --known-themes and --confirm-posted")
            if args.confirm_posted and args.dry_run:
                raise InputError("--dry-run cannot be combined with --confirm-posted (it never writes the state file)")
            if args.known_themes:
                state = read_state(args.state)
                seen = {}
                for d in sorted(state["digests"], key=lambda d: d["week"]):
                    for key, t in d["themes"].items():
                        seen[key] = (t["title"], d["week"])
                lines = ["%s | %s | last seen %s" % (key, seen[key][0], seen[key][1]) for key in sorted(seen)]
                output = "\n".join(lines or ["No themes on record yet."]) + "\n"
            else:
                if not WEEK_RE.match(args.confirm_posted):
                    raise InputError("--confirm-posted must be an ISO week such as 2026-W40, got %r"
                                     % args.confirm_posted)
                if not args.as_of:
                    raise InputError("--as-of is required with --confirm-posted")
                as_of = parse_moment(args.as_of, lambda p: InputError("--as-of: " + p))
                post_ref = None
                if args.post_ref is not None:
                    post_ref = safe_url(args.post_ref)
                    if post_ref is None:
                        raise InputError("--post-ref must be an http(s) link to the post, got %r" % args.post_ref)
                check_writable(args.state)
                with StateLock(args.state, as_of):
                    state = read_state(args.state)
                    entry = [d for d in state["digests"] if d["week"] == args.confirm_posted]
                    if not entry:
                        raise InputError("state %s: no digest recorded for %s; run the scheduled digest first"
                                         % (args.state, args.confirm_posted))
                    if entry[0]["status"] == "posted":
                        output = "Already confirmed: %s was posted at %s.\n" % (args.confirm_posted,
                                                                                 entry[0].get("posted_at"))
                    else:
                        entry[0]["status"] = "posted"
                        entry[0]["posted_at"] = as_of.isoformat()
                        entry[0]["post_ref"] = post_ref
                        write_state(args.state, state)
                        output = "Confirmed: %s marked as posted.\n" % args.confirm_posted
        else:
            if args.bundle is None:
                raise InputError("--bundle is required")
            if not args.as_of:
                raise InputError("--as-of is required, e.g. --as-of 2026-10-05T08:00:00-07:00")
            as_of = parse_moment(args.as_of, lambda p: InputError("--as-of: " + p))
            internal = sorted(set(clean_domain(d) for d in args.internal_domain if d.strip()))
            if not internal:
                raise InputError("--internal-domain is required at least once; without it internal meetings "
                                 "cannot be told apart from customer calls")
            bad = [d for d in internal if not DOMAIN_RE.match(d)]
            if bad:
                raise InputError("--internal-domain must be a domain such as yourco.example, got %r" % bad[0])
            internal_emails = set(e.strip().lower() for e in args.internal_email if e.strip())
            bad = [e for e in internal_emails if not DOMAIN_RE.match(domain_of(e)) or " " in e]
            if bad:
                raise InputError("--internal-email must be an email address, got %r" % bad[0])
            doc_url = None
            if args.doc_url is not None:
                doc_url = safe_url(args.doc_url)
                if doc_url is None:
                    raise InputError("--doc-url must be an http(s) link to the digest doc, got %r" % args.doc_url)
            if args.scheduled and args.state is None:
                raise InputError("--scheduled needs --state so a week is never posted twice")
            if args.resume_pending and not args.scheduled:
                raise InputError("--resume-pending only applies to --scheduled runs")
            config = load_config(args.config)
            bundle = load_bundle(args.bundle, config)
            start, end = bundle["period_start"], bundle["period_end"]
            if end > as_of.date():
                raise InputError("bundle %s: $.period.end %s is after --as-of; a digest cannot cover the future"
                                 % (args.bundle, end.isoformat()))
            if args.scheduled and not is_full_week(start, end):
                raise InputError("bundle %s: $.period: --scheduled needs a Monday-Sunday week, got %s to %s; run other "
                                 "ranges without --scheduled (they are never recorded)"
                                 % (args.bundle, start.isoformat(), end.isoformat()))
            if args.scheduled and end >= as_of.date():
                raise InputError("bundle %s: $.period: the week %s to %s has not ended at --as-of %s; a scheduled run "
                                 "only records a finished week (run it on or after %s)"
                                 % (args.bundle, start.isoformat(), end.isoformat(), as_of.isoformat(),
                                    (end + timedelta(days=1)).isoformat()))
            key = period_key(start, end)
            writing = args.scheduled and not args.dry_run
            if writing:
                check_writable(args.state)
            with (StateLock(args.state, as_of) if writing else nullcontext()):
                state = read_state(args.state)
                existing = [d for d in state["digests"] if d["week"] == key] if args.scheduled else []
                listing = bundle["sources"]["meetings"]
                if existing and existing[0]["status"] == "posted":
                    output = "Skipped: customer feedback digest for %s already posted (%s).\n" % (
                        key, existing[0].get("posted_at") or "time not recorded")
                elif existing and not args.resume_pending:
                    raise StopRun(EXIT_PENDING, "the digest for %s was rendered at %s but never confirmed as posted. "
                                  "Check the destination for an existing post: if it is there, run --confirm-posted "
                                  "%s; if not, rerun with --resume-pending." % (key, existing[0].get("rendered_at"), key))
                elif args.scheduled and listing["status"] != "ok":
                    raise StopRun(EXIT_INCOMPLETE, "meeting list not ok; nothing recorded", stdout=(
                        "Not recorded: the customer feedback digest for %s is incomplete because the meeting list "
                        "(%s) is %s. Nothing was recorded; fix the connection and rerun.\n"
                        % (key, PROVIDER_LABELS.get(listing["provider"] or "", "recorder"),
                           listing["status"].replace("_", " "))))
                else:
                    if existing:
                        notes.append("Resuming pending %s (rendered at %s, never confirmed). Check the destination "
                                     "for an existing post before posting again." % (key, existing[0].get("rendered_at")))
                    classify(bundle, config, internal, internal_emails)
                    analysis = analyse(bundle, config, args.weighting, previous_digest(state, start), key)
                    markdown = render_markdown(bundle, analysis, config, as_of, internal)
                    if args.format == "markdown":
                        output = markdown
                    elif args.format == "slack":
                        output = render_slack(bundle, analysis, config, as_of, doc_url)
                    else:
                        output = json.dumps(json_ready(bundle, analysis, markdown), indent=2, ensure_ascii=False) + "\n"
                    if writing:
                        unconfirmed = sorted(d["week"] for d in state["digests"]
                                             if d["status"] == "pending" and d["week"] != key)
                        if unconfirmed:
                            notes.append("Earlier week(s) %s were rendered but never confirmed as posted. Check the "
                                         "destination; run --confirm-posted for each one that was posted."
                                         % ", ".join(unconfirmed))
                        entry = {"week": key, "period_start": start.isoformat(), "period_end": end.isoformat(),
                                 "status": "pending", "rendered_at": as_of.isoformat(), "posted_at": None,
                                 "post_ref": None, "themes": snapshot(analysis)}
                        state["digests"] = [d for d in state["digests"] if d["week"] != key] + [entry]
                        write_state(args.state, state)
                        staged = True
    except InputError as exc:
        emit(sys.stderr, "Error: %s\n" % exc)
        return EXIT_INPUT
    except StopRun as stop:
        if stop.stdout:
            if not emit(sys.stdout, stop.stdout):
                emit(sys.stderr, "Error: stdout is closed; %s\n" % stop)
        else:
            emit(sys.stderr, "Stopped: %s\n" % stop)
        return stop.code

    for note in notes:
        emit(sys.stderr, "Note: %s\n" % note)
    if not emit(sys.stdout, output):
        recorded = (" The week %s is recorded as pending, so the next scheduled run stops with exit 4; rerun with "
                    "--scheduled --resume-pending to print it again." % key) if staged else ""
        emit(sys.stderr, "Error: the output could not be written (stdout closed or broken pipe).%s\n" % recorded)
        return EXIT_OUTPUT
    return 0


if __name__ == "__main__":
    sys.exit(main())
