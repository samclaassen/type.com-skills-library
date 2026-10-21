#!/usr/bin/env python3
"""Render a win/loss report from one normalised JSON bundle of closed deals, meetings and cited evidence.

Stdlib only, offline, deterministic, Python 3.8+. --as-of is the only "now"; the wall clock is never read.
The agent does the reading and judgement (which CRM deals closed, which meetings belong to them, what each
customer said and which taxonomy category it fits). This script does the mechanical, checkable parts:

  * validates the bundle, the taxonomy and the config; bad input is exit 2 with the file and JSON path,
    and nothing on stdout
  * optionally reads the deal list from a CSV export (--deals-csv) instead of the bundle
  * links meetings to deals by external attendee email domain (or an explicit deal_ids list), drops
    note-taker bots, excludes internal-only meetings, merges two notes of the same call
  * checks every evidence item cites a deal in the bundle and either a meeting linked to that deal or a CRM
    field present on that deal; when the cited text (transcript_text, notes_text or the CRM field value) is in
    the bundle, the quote must be one or more complete sentences of one speaker turn of it (granola_text.py's
    whole-sentence rule; a CRM-field quote may also be the whole field value)
  * derives each lost deal's loss type (competitive, no decision, other, no recorded reason) from cited
    evidence only
  * counts reasons, objections and competitors by DISTINCT deals (optionally amount-weighted), computes win
    rates and shares with a small-sample rule (cuts below min_sample_for_percent show counts, no percentages)
  * renders markdown, a short Slack summary draft, or JSON; it never posts anything
  * escapes every interpolated value for its output format with granola_text.md() / slack() and only links
    http(s) URLs it validated; replaces emails and phone numbers in displayed text with
    granola_text.redact_contacts(), which is pattern-based and can miss formats

Exit codes: 0 report printed; 1 stdout was closed or the pipe broke; 2 bad input, config, taxonomy, CSV or
arguments (nothing on stdout).
"""

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
# granola_text.py (shared quote check, contact redaction and escaping) sits next to this script; import it from
# here whatever the working directory, and never write a __pycache__ folder into the package.
sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import granola_text as gt  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR / "winloss.json"
DEFAULT_TAXONOMY = SCRIPT_DIR / "reason_taxonomy.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCE_KEYS = ["deals", "meetings", "transcripts"]
SOURCE_STATUSES = ["ok", "partial", "unavailable", "denied", "not_connected", "not_configured", "not_requested"]
SOURCE_LABELS = {"deals": "Deals", "meetings": "Meeting notes", "transcripts": "Transcripts"}
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "manual": "pasted transcripts", "hubspot": "HubSpot",
                   "attio": "Attio", "salesforce": "Salesforce", "pipedrive": "Pipedrive", "csv": "CSV export",
                   "none": "none"}
OUTCOMES = ["won", "lost"]
TRANSCRIPT_STATUSES = ["ok", "denied", "unavailable", "not_requested"]
NOTES_STATUSES = ["ok", "unavailable"]
EVIDENCE_KINDS = ["loss_reason", "win_reason", "objection", "competitor_mention"]
EVIDENCE_SOURCES = ["transcript", "note", "crm_field"]
SPEAKER_SIDES = ["customer", "unconfirmed"]
WEIGHTINGS = ["none", "amount"]
FORMATS = ["markdown", "slack", "json"]
LOSS_TYPES = ["competitive", "no_decision", "other", "unknown"]
LOSS_TYPE_TITLES = {"competitive": "Competitive losses", "no_decision": "No-decision losses",
                    "other": "Other losses", "unknown": "Losses with no recorded reason"}
LOSS_TYPE_SHORT = {"competitive": "competitive", "no_decision": "no decision", "other": "other",
                   "unknown": "no recorded reason"}
TAXONOMY_SECTIONS = {"loss_reason": "loss_reasons", "win_reason": "win_reasons", "objection": "objections"}
CONFIG_KEYS = ["min_sample_for_percent", "recurring_min_deals", "max_quotes_per_reason", "max_quotes_per_competitor",
               "max_quote_chars", "max_role_chars", "currency_symbol", "stage_order", "ignored_attendee_domains",
               "ignored_attendee_local_parts", "free_email_domains"]
CSV_CORE = ["deal_id", "account", "domains", "outcome", "close_date", "amount", "currency", "segment",
            "stage_lost_at", "competitors", "lost_to", "crm_url"]

MAX_LEN = {"account": 200, "title": 300, "segment": 80, "stage": 80, "competitor": 80, "label": 120,
           "crm_field_name": 80, "crm_field_value": 2000, "detail": 300, "period_label": 80}
DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
# ASCII digits only; hours 00-23, minutes and seconds 00-59, offsets up to +/-14:00; parsed by hand so every
# Python from 3.8 agrees (3.11+ alone would accept T24:00 or compact forms through fromisoformat).
DATETIME_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([01][0-9]|2[0-3]):([0-5][0-9])"
                         r"(?::([0-5][0-9])(?:\.([0-9]{1,6}))?)?"
                         r"(Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
MIN_YEAR, MAX_YEAR = 1900, 2999
MAX_AMOUNT = 1e15
MAX_PERIOD_DAYS = 731
DEAL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:/_.#-]{0,79}")
KEY_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,59}")
CURRENCY_RE = re.compile(r"[A-Z]{3}")
DOMAIN_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]{1,5})?")
URL_RE = re.compile(r"https?://|www\.", re.I)
URL_SAFE = "-._~!$&'*+,;=:@/?#%"
BIDI_CONTROLS = set(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)) + [0x200E, 0x200F])
# Invisible characters that only hide things (a link, a phone number, a duplicate name): dropped. Zero-width
# (non-)joiners are kept inside emoji and scripts that need them, and dropped next to ASCII text.
INVISIBLE = set(chr(c) for c in [0x00AD, 0x180E, 0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF])
ASCII_JOINER_RE = re.compile(r"(?<=[\x00-\x7f])[\u200c\u200d]+|[\u200c\u200d]+(?=[\x00-\x7f])")
QUOTE_PAIRS = [("\u201c", "\u201d"), ('"', '"'), ("\u2018", "\u2019"), ("'", "'"), ("\u00ab", "\u00bb")]
SURROGATE_RE = re.compile("[\ud800-\udfff]")
EXIT_OUTPUT, EXIT_INPUT = 1, 2


class InputError(Exception):
    """Bad input; the message names the file and the JSON path."""


# --------------------------------------------------------------------------- text safety


def clean_text(value):
    """Collapse whitespace (incl. newlines) and drop control, bidi-override and invisible characters."""
    kept = "".join(" " if ch.isspace() else ch for ch in value
                   if ch.isspace() or (unicodedata.category(ch) not in ("Cc", "Cs") and ch not in BIDI_CONTROLS
                                       and ch not in INVISIBLE))
    return " ".join(ASCII_JOINER_RE.sub("", kept).split())


def name_key(name):
    """Compare names ignoring case, full-width forms, invisible characters and spacing."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join("".join(ch for ch in folded if unicodedata.category(ch) != "Cf").split())


def field_key(name):
    """CRM field names match ignoring case, spacing, '_' and '-' ('Closed Lost Reason' = 'closed_lost_reason')."""
    return " ".join(re.sub(r"[_-]+", " ", name_key(name)).split())


def quote_key(text):
    """Two quotes are the same text when granola_text.normalise() makes them equal, ignoring case."""
    return gt.normalise(text).casefold()


# With no meeting text to check against, only the rules that need no text apply.
TEXTLESS_REASONS = (gt.REASONS["empty"], gt.REASONS["short"], gt.REASONS["ellipsis"], gt.REASONS["label"])


def quote_problem(quote_text, source_text):
    """None when the quote passes granola_text's whole-sentence rule against source_text (one sentence, or a run of
    consecutive sentences, of one speaker turn); otherwise the reason. When source_text is None the quote is checked
    against itself, so only ellipses, speaker labels and quotes under 3 words are caught."""
    ok, reason = gt.check_quote(quote_text, quote_text if source_text is None else source_text)
    if source_text is None:
        return reason if reason in TEXTLESS_REASONS else None
    return None if ok else reason





def crm_quote_problem(quote_text, value):
    """A CRM-field quote is the whole field value (case, formatting and a final . or ! aside), or whole sentences of
    it under the same rule as call quotes."""
    if quote_key(quote_text).rstrip(".! ") == quote_key(value).rstrip(".! "):
        return None
    return quote_problem(quote_text, value)


def safe_url(raw):
    """Return a percent-encoded absolute http(s) URL with a real host and port 1-65535, or None."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not re.match(r"https?://", raw, re.I):
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    netloc = parts.netloc
    if parts.scheme.lower() not in ("http", "https") or not netloc or "@" in netloc or \
            not HOST_RE.fullmatch(netloc) or not parts.hostname or "." not in parts.hostname.strip(".") or \
            parts.hostname.startswith(".") or (port is not None and not 0 < port < 65536):
        return None
    url = "%s://%s%s" % (parts.scheme.lower(), netloc.lower(), quote(parts.path, safe=URL_SAFE))
    if parts.query:
        url += "?" + quote(parts.query, safe=URL_SAFE)
    if parts.fragment:
        url += "#" + quote(parts.fragment, safe=URL_SAFE)
    return url


def md_link(label, url):
    return "[%s](%s)" % (gt.md(label), url) if url else gt.md(label)


def slack_url(url):
    """A URL the script validated: only &, < and > need escaping."""
    return url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_link(label, url):
    if not url:
        return gt.slack(label)
    return "<%s|%s>" % (slack_url(url), gt.slack(label.replace("|", " ")))


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


# --------------------------------------------------------------------------- reading


def is_number(value, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0 and (maximum is None or value <= maximum)
    except OverflowError:
        return False


def parse_day(value, fail):
    """Strict YYYY-MM-DD (ASCII digits only), identical on every Python version."""
    m = DATE_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise fail("%r is not a YYYY-MM-DD date" % (value,))
    if not MIN_YEAR <= int(m.group(1)) <= MAX_YEAR:
        raise fail("%r is outside the years %d-%d" % (value, MIN_YEAR, MAX_YEAR))
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise fail("%r is not a real date" % (value,)) from None


def parse_moment(value, fail):
    """Strict YYYY-MM-DDTHH:MM[:SS[.ffffff]] with Z or a +/-HH:MM offset up to 14:00."""
    m = DATETIME_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise fail("%r is not a datetime like 2026-10-05T09:00:00-07:00 (YYYY-MM-DDTHH:MM[:SS[.ffffff]] with Z "
                   "or a +/-HH:MM offset up to 14:00)" % (value,))
    year, month, day_, hour, minute, second, frac, offset = m.groups()
    if not MIN_YEAR <= int(year) <= MAX_YEAR:
        raise fail("%r is outside the years %d-%d" % (value, MIN_YEAR, MAX_YEAR))
    if offset == "Z":
        tz = timezone.utc
    else:
        delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6]))
        tz = timezone(-delta if offset[0] == "-" else delta)
    try:
        return datetime(int(year), int(month), int(day_), int(hour), int(minute), int(second or 0),
                        int((frac or "0").ljust(6, "0")), tzinfo=tz)
    except ValueError:
        raise fail("%r is not a real date and time" % (value,)) from None


def reject_constant(name):
    raise InputError("%s is not allowed (numbers must be finite)" % name)


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise InputError("%s is not allowed (numbers must be finite)" % text[:40])
    return value


def strict_int(text):
    if len(text.lstrip("-")) > 30:
        raise InputError("an integer with %d digits is not allowed" % len(text.lstrip("-")))
    return int(text)


def reject_surrogates(data, label):
    """A \\ud800-style escape decodes to a lone surrogate that can't be written as UTF-8: refuse it up front."""
    stack = [(data, "$")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if SURROGATE_RE.search(key):
                    raise InputError("%s: %s: a key contains a lone surrogate (\\ud800-\\udfff escape); not valid "
                                     "text" % (label, path))
                stack.append((value, "%s.%s" % (path, key)))
        elif isinstance(node, list):
            stack.extend((value, "%s[%d]" % (path, i)) for i, value in enumerate(node))
        elif isinstance(node, str) and SURROGATE_RE.search(node):
            raise InputError("%s: %s: contains a lone surrogate (\\ud800-\\udfff escape); not valid text"
                             % (label, path))


def read_text_file(path, label, encoding="utf-8"):
    if not path.exists():
        raise InputError("%s: file not found: %s" % (label, path))
    if not path.is_file():
        raise InputError("%s: %s is not a regular file" % (label, path))
    try:
        return path.read_bytes().decode(encoding)
    except OSError as exc:
        raise InputError("%s: %s cannot be read (%s)" % (label, path, exc.strerror or exc)) from None
    except UnicodeDecodeError:
        raise InputError("%s: %s is not UTF-8 text" % (label, path)) from None


def load_json(path, label):
    text = read_text_file(path, label)
    try:
        data = json.loads(text, parse_constant=reject_constant, parse_float=finite_float, parse_int=strict_int)
    except InputError as exc:
        raise InputError("%s: %s: %s" % (label, path, exc)) from None
    except json.JSONDecodeError as exc:
        raise InputError("%s: %s is not valid JSON (line %d, column %d: %s)"
                         % (label, path, exc.lineno, exc.colno, exc.msg)) from None
    except RecursionError:
        raise InputError("%s: %s is nested too deeply" % (label, path)) from None
    except (ValueError, OverflowError) as exc:
        raise InputError("%s: %s is not usable JSON (%s)" % (label, path, str(exc)[:120])) from None
    reject_surrogates(data, "%s %s" % (label, path))
    return data


class Reader(object):
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

    def text(self, parent, key, path, required=True, max_len=None):
        value = parent.get(key)
        if isinstance(value, str):
            value = clean_text(value)
        if value is None or value == "":
            if required:
                raise self.fail("%s.%s" % (path, key), "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail("%s.%s" % (path, key), "must be a string, got %s" % type(value).__name__)
        if max_len is not None and len(value) > max_len:
            raise self.fail("%s.%s" % (path, key), "is %d characters; the limit is %d" % (len(value), max_len))
        return value

    def prose(self, parent, key, path, required=True, max_len=None):
        """Free text that is displayed: cleaned, with emails and phone numbers redacted (pattern-based)."""
        value = self.text(parent, key, path, required, max_len)
        return None if value is None else gt.redact_contacts(value)

    def url(self, parent, key, path, warnings, required=True):
        """A link; an unsafe or non-http(s) value is dropped (None) and counted as a warning."""
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        url = safe_url(value)
        if url is None:
            warnings.append("%s.%s" % (path, key))
        return url

    def enum(self, parent, key, path, allowed, default=None):
        value = parent.get(key, default)
        if value is None:
            value = default
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("%s.%s" % (path, key), "must be one of %s, got %s"
                            % (", ".join(allowed), short_repr(value)))
        return value

    def number(self, parent, key, path, required=True, maximum=None):
        value = parent.get(key)
        if value is None and not required:
            return None
        if not is_number(value, maximum):
            raise self.fail("%s.%s" % (path, key), "must be a non-negative finite number%s, got %s"
                            % (" up to %g" % maximum if maximum is not None else "", short_repr(value)))
        return float(value)

    def integer(self, parent, key, path, required=True, minimum=0, maximum=None):
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum or \
                (maximum is not None and value > maximum):
            raise self.fail("%s.%s" % (path, key), "must be an integer from %d%s, got %s"
                            % (minimum, " to %d" % maximum if maximum is not None else "", short_repr(value)))
        return value

    def day(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required)
        if value is None:
            return None
        return parse_day(value, lambda problem: self.fail("%s.%s" % (path, key), problem))

    def moment(self, parent, key, path):
        value = self.text(parent, key, path)
        return parse_moment(value, lambda problem: self.fail("%s.%s" % (path, key), problem))

    def strings(self, value, path, required=False, max_len=None):
        out = []
        for i, item in enumerate(self.lst(value, path, required)):
            if not isinstance(item, str) or not clean_text(item):
                raise self.fail("%s[%d]" % (path, i), "must be a non-empty string")
            item = clean_text(item)
            if max_len is not None and len(item) > max_len:
                raise self.fail("%s[%d]" % (path, i), "is %d characters; the limit is %d" % (len(item), max_len))
            out.append(item)
        return out


def short_repr(value):
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def load_config(path):
    config = load_json(path, "config")
    r = Reader("config %s" % path)
    r.obj(config, "$")
    missing = [k for k in CONFIG_KEYS if k not in config]
    if missing:
        raise InputError("config %s: missing key(s): %s" % (path, ", ".join(missing)))
    r.integer(config, "min_sample_for_percent", "$", minimum=1, maximum=1000)
    r.integer(config, "recurring_min_deals", "$", minimum=1, maximum=1000)
    for key in ["max_quotes_per_reason", "max_quotes_per_competitor"]:
        r.integer(config, key, "$", minimum=0, maximum=50)
    r.integer(config, "max_quote_chars", "$", minimum=20, maximum=5000)
    r.integer(config, "max_role_chars", "$", minimum=5, maximum=200)
    symbol = config["currency_symbol"]
    if not isinstance(symbol, str) or len(symbol) > 5 or clean_text(symbol) != symbol or has_markup(symbol):
        raise InputError("config %s: $.currency_symbol must be a short plain string such as \"$\" or \"EUR \"" % path)
    config["stage_order"] = r.strings(config["stage_order"], "$.stage_order", required=True,
                                      max_len=MAX_LEN["stage"])

    def domain_list(key):
        values = r.strings(config[key], "$." + key, required=True)
        bad = [v for v in values if not DOMAIN_RE.fullmatch(v)]
        if bad:
            raise InputError("config %s: $.%s has invalid domain(s): %s" % (path, key, ", ".join(map(repr, bad))))
        return values

    config["ignored_attendee_domains"] = domain_list("ignored_attendee_domains")
    config["free_email_domains"] = domain_list("free_email_domains")
    locals_ = r.strings(config["ignored_attendee_local_parts"], "$.ignored_attendee_local_parts", required=True)
    bad = [v for v in locals_ if not re.fullmatch(r"[a-z0-9._-]+", v)]
    if bad:
        raise InputError("config %s: $.ignored_attendee_local_parts has invalid value(s): %s"
                         % (path, ", ".join(map(repr, bad))))
    config["ignored_attendee_local_parts"] = locals_
    return config


def has_markup(text):
    return bool(re.search(r"[\\`*_~\[\]()|#@<>&!]", text))


def load_taxonomy(path):
    tax = load_json(path, "taxonomy")
    r = Reader("taxonomy %s" % path)
    r.obj(tax, "$")
    if tax.get("version") != 1 or isinstance(tax.get("version"), bool):
        raise r.fail("$.version", "must be 1, got %s" % short_repr(tax.get("version")))
    out = {}
    for section in ["loss_reasons", "win_reasons", "objections"]:
        entries = r.obj(tax.get(section), "$." + section)
        if not entries:
            raise r.fail("$." + section, "must define at least one category")
        cats = {}
        labels = {}
        for key in sorted(entries):
            p = "$.%s.%s" % (section, key)
            if not KEY_RE.fullmatch(key):
                raise r.fail(p, "category keys must be lower-case letters, digits or '_' (max 60), got %s"
                             % short_repr(key))
            entry = r.obj(entries[key], p)
            label = r.text(entry, "label", p, max_len=MAX_LEN["label"])
            if name_key(label) in labels:
                raise r.fail(p + ".label", "%r is already the label of %s" % (label, labels[name_key(label)]))
            labels[name_key(label)] = key
            no_decision = entry.get("no_decision", False)
            if not isinstance(no_decision, bool):
                raise r.fail(p + ".no_decision", "must be true or false")
            if no_decision and section != "loss_reasons":
                raise r.fail(p + ".no_decision", "only loss_reasons can be marked no_decision")
            cats[key] = {"key": key, "label": label, "no_decision": no_decision,
                         "description": r.text(entry, "description", p, required=False)}
        out[section] = cats
    return out


# --------------------------------------------------------------------------- deals (bundle or CSV)


def clean_domain(domain):
    d = domain.strip().lower().lstrip("@").strip().rstrip(".")
    return d[4:] if d.startswith("www.") else d


def domain_of(email):
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def in_domains(domain, domains):
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


def is_bot(email, config):
    """Note-taker bots: exact domain (or a subdomain of it), or a listed local part (ignoring +tags)."""
    local = email.rsplit("@", 1)[0].split("+", 1)[0]
    return in_domains(domain_of(email), config["ignored_attendee_domains"]) or \
        local in config["ignored_attendee_local_parts"]


AMOUNT_RE = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?")


def read_deals_csv(path):
    """Read a deals CSV export into the same shape as $.deals. Values stay strings; load_deals types them."""
    label = "deals CSV %s" % path
    text = read_text_file(path, label, encoding="utf-8-sig")
    if SURROGATE_RE.search(text):
        raise InputError("%s: contains a lone surrogate; not valid text" % label)
    if "\x00" in text:
        raise InputError("%s: contains NUL bytes; export it again as plain UTF-8 CSV" % label)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise InputError("%s: is not valid CSV (%s)" % (label, exc)) from None
    numbered = [(n, row) for n, row in enumerate(rows, start=1) if any(cell.strip() for cell in row)]
    if not numbered:
        raise InputError("%s: is empty; it needs a header row and one row per closed deal" % label)
    original = [clean_text(h) for h in numbered[0][1]]
    header = [re.sub(r"[\s-]+", "_", h.lower()) for h in original]
    seen = set()
    for i, h in enumerate(header):
        if not h:
            raise InputError("%s: header column %d is empty" % (label, i + 1))
        if h in seen:
            raise InputError("%s: header %r appears twice" % (label, h))
        seen.add(h)
    for col in ["deal_id", "account", "outcome", "close_date"]:
        if col not in seen:
            raise InputError("%s: required column %r is missing (header: %s)" % (label, col, ", ".join(header)))
    deals = []
    for n, row in numbered[1:]:
        if len(row) != len(header):
            raise InputError("%s: row %d has %d cells but the header has %d" % (label, n, len(row), len(header)))
        deal = {"_row": n, "crm_fields": {}}
        for col, name, value in zip(header, original, [clean_text(c) for c in row]):
            if col in CSV_CORE:
                if col in ("domains", "competitors"):
                    deal[col] = [v.strip() for v in value.split(";") if v.strip()]
                elif col == "amount":
                    raw = re.sub(r"^(?:[A-Za-z]{3}|[$€£¥])", "", value.replace(" ", "")) if value else ""
                    if raw and not AMOUNT_RE.fullmatch(raw):
                        raise InputError("%s: row %d: amount %r is not a plain number such as 24000 or 24,000.00"
                                         % (label, n, value))
                    deal[col] = float(raw.replace(",", "")) if raw else None
                elif col == "outcome":
                    deal[col] = re.sub(r"^closed[ _-]*", "", value.lower()) if value else None
                else:
                    deal[col] = value or None
            elif value:
                deal["crm_fields"][name] = value
        deals.append(deal)
    return deals


def load_deals(raw_deals, r, base, config, warn, csv_mode):
    deals = {}
    order = []
    domain_owner = {}
    free = config["free_email_domains"]
    for i, d in enumerate(raw_deals):
        p = "row %d" % d["_row"] if csv_mode else "%s[%d]" % (base, i)
        r.obj(d, p)
        deal_id = r.text(d, "deal_id", p)
        if not DEAL_ID_RE.fullmatch(deal_id):
            raise r.fail(p + ".deal_id", "%s must be letters, digits and : / _ . # - only (max 80)"
                         % short_repr(deal_id))
        if deal_id in deals:
            raise r.fail(p + ".deal_id", "duplicate deal_id %r; one row per deal" % deal_id)
        account = r.prose(d, "account", p, max_len=MAX_LEN["account"])
        domains = []
        for j, dom in enumerate(r.lst(d.get("domains"), p + ".domains")):
            if not isinstance(dom, str) or not DOMAIN_RE.fullmatch(clean_domain(dom)):
                raise r.fail("%s.domains[%d]" % (p, j), "must be a domain such as outfitters.example, got %s"
                             % short_repr(dom))
            cd = clean_domain(dom)
            if in_domains(cd, free):
                raise r.fail("%s.domains[%d]" % (p, j), "%r is a personal email domain (free_email_domains) and "
                             "cannot identify a company; leave it out and link those meetings with deal_ids" % cd)
            if cd not in domains:
                domains.append(cd)
        for cd in domains:
            other = domain_owner.get(cd)
            if other is not None and name_key(other[0]) != name_key(account):
                raise r.fail(p + ".account", "%r shares the domain %r with %r (%s); use one account name for one "
                             "company" % (account, cd, other[0], other[1]))
            domain_owner[cd] = (account, p)
        outcome = r.enum(d, "outcome", p, OUTCOMES)
        currency = r.text(d, "currency", p, required=False)
        if currency is not None and not CURRENCY_RE.fullmatch(currency):
            raise r.fail(p + ".currency", "must be a 3-letter code such as USD, got %s" % short_repr(currency))
        stage = r.prose(d, "stage_lost_at", p, required=False, max_len=MAX_LEN["stage"])
        lost_to = r.prose(d, "lost_to", p, required=False, max_len=MAX_LEN["competitor"])
        if outcome == "won" and stage is not None:
            raise r.fail(p + ".stage_lost_at", "is set on a won deal")
        if outcome == "won" and lost_to is not None:
            raise r.fail(p + ".lost_to", "is set on a won deal")
        competitors = [gt.redact_contacts(c) for c in r.strings(d.get("competitors"), p + ".competitors",
                                                    max_len=MAX_LEN["competitor"])]
        fields_raw = r.obj(d.get("crm_fields"), p + ".crm_fields", required=False) or {}
        fields = {}
        field_keys = {}
        for fname in sorted(fields_raw):
            fp = "%s.crm_fields.%s" % (p, fname)
            cname = clean_text(fname)
            if not cname or len(cname) > MAX_LEN["crm_field_name"]:
                raise r.fail(fp, "field names must be 1-%d characters" % MAX_LEN["crm_field_name"])
            if field_key(cname) in field_keys:
                raise r.fail(fp, "duplicates the field %r" % field_keys[field_key(cname)])
            value = fields_raw[fname]
            if value is None:
                continue
            if not isinstance(value, str):
                raise r.fail(fp, "must be a string (the field value as read from the CRM), got %s"
                             % type(value).__name__)
            value = clean_text(value)
            if len(value) > MAX_LEN["crm_field_value"]:
                raise r.fail(fp, "is %d characters; the limit is %d" % (len(value), MAX_LEN["crm_field_value"]))
            if value:
                field_keys[field_key(cname)] = cname
                fields[cname] = value
        deals[deal_id] = {
            "deal_id": deal_id,
            "index": i,
            "path": p,
            "account": account,
            "domains": domains,
            "outcome": outcome,
            "close_date": r.day(d, "close_date", p),
            "amount": r.number(d, "amount", p, required=False, maximum=MAX_AMOUNT),
            "currency": currency,
            "segment": r.prose(d, "segment", p, required=False, max_len=MAX_LEN["segment"]),
            "stage_lost_at": stage,
            "competitors": competitors,
            "lost_to": lost_to,
            "lost_to_in_csv": csv_mode and lost_to is not None,
            "crm_url": r.url(d, "crm_url", p, warn, required=False),
            "crm_fields": fields,
        }
        order.append(deal_id)
    return deals, order


# --------------------------------------------------------------------------- bundle


def load_bundle(path, config, taxonomy, csv_deals):
    raw = load_json(path, "bundle")
    r = Reader("bundle %s" % path)
    r.obj(raw, "$")
    if raw.get("schema_version") != 1 or isinstance(raw.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1, got %s" % short_repr(raw.get("schema_version")))
    b = {"link_warnings": [], "label": "bundle %s" % path}
    warn = b["link_warnings"]

    period = r.obj(raw.get("period"), "$.period")
    b["period_start"] = r.day(period, "start", "$.period")
    b["period_end"] = r.day(period, "end", "$.period")
    if b["period_end"] < b["period_start"]:
        raise r.fail("$.period.end", "is before $.period.start")
    if (b["period_end"] - b["period_start"]).days + 1 > MAX_PERIOD_DAYS:
        raise r.fail("$.period", "spans more than %d days; run one report per quarter or year" % MAX_PERIOD_DAYS)
    b["period_label"] = r.prose(period, "label", "$.period", required=False, max_len=MAX_LEN["period_label"])
    currency = r.text(raw, "currency", "$", required=False)
    if currency is not None and not CURRENCY_RE.fullmatch(currency):
        raise r.fail("$.currency", "must be a 3-letter code such as USD, got %s" % short_repr(currency))

    sources_raw = r.obj(raw.get("sources"), "$.sources")
    sources = {}
    for key in SOURCE_KEYS:
        sp = "$.sources.%s" % key
        src = r.obj(sources_raw.get(key), sp)
        sources[key] = {
            "provider": r.text(src, "provider", sp, required=False, max_len=40),
            "status": r.enum(src, "status", sp, SOURCE_STATUSES),
            "detail": r.prose(src, "detail", sp, required=False, max_len=MAX_LEN["detail"]),
            "history_days": r.integer(src, "history_days", sp, required=False, minimum=1, maximum=36500),
        }
    b["sources"] = sources

    if csv_deals is not None:
        if raw.get("deals"):
            raise r.fail("$.deals", "must be absent or [] when --deals-csv is given (one deal list per run)")
        rc = Reader("deals CSV")
        deals, order = load_deals(csv_deals, rc, "", config, warn, True)
    else:
        deals, order = load_deals(r.lst(raw.get("deals"), "$.deals", required=True), r, "$.deals", config, warn,
                                  False)
    currencies = sorted(set(d["currency"] for d in deals.values() if d["currency"]))
    expected = currency or (currencies[0] if currencies else None)
    for deal_id in order:
        d = deals[deal_id]
        if d["currency"] is not None and d["currency"] != expected:
            raise InputError("%s: %s.currency: %r differs from %r; the report uses one currency, so convert "
                             "amounts first" % (b["label"], d["path"], d["currency"], expected))
    b["currency"] = expected
    b["deals"] = deals
    b["deal_order"] = order

    meetings = []
    by_id = {}
    for i, m in enumerate(r.lst(raw.get("meetings"), "$.meetings", required=True)):
        p = "$.meetings[%d]" % i
        r.obj(m, p)
        mid = r.text(m, "meeting_id", p, max_len=200)
        if mid in by_id:
            raise r.fail(p + ".meeting_id", "duplicate meeting_id %r" % mid)
        if "attendees" not in m:
            raise r.fail(p + ".attendees", "is required (use [] when the source lists nobody)")
        attendees = []
        for j, att in enumerate(r.lst(m.get("attendees"), p + ".attendees")):
            q = "%s.attendees[%d]" % (p, j)
            r.obj(att, q)
            email = r.text(att, "email", q, max_len=320).lower()
            if " " in email or "@" not in email or not DOMAIN_RE.fullmatch(domain_of(email)) or \
                    not email.rsplit("@", 1)[0]:
                raise r.fail(q + ".email", "%s is not an email address" % short_repr(email))
            attendees.append(email)
        deal_ids = []
        for j, did in enumerate(r.lst(m.get("deal_ids"), p + ".deal_ids")):
            if not isinstance(did, str) or did not in deals:
                raise r.fail("%s.deal_ids[%d]" % (p, j), "%s is not a deal_id in the deal list" % short_repr(did))
            if did not in deal_ids:
                deal_ids.append(did)
        meeting = {
            "index": i,
            "meeting_id": mid,
            "title": r.prose(m, "title", p, max_len=MAX_LEN["title"]),
            "start": r.moment(m, "start", p),
            "url": r.url(m, "url", p, warn),
            "attendees": attendees,
            "explicit_deals": deal_ids,
            "transcript": r.enum(m, "transcript", p, TRANSCRIPT_STATUSES, "not_requested"),
            "notes": r.enum(m, "notes", p, NOTES_STATUSES, "ok"),
            "notes_text": None,
            "transcript_text": None,
        }
        for key in ["notes_text", "transcript_text"]:
            value = m.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise r.fail("%s.%s" % (p, key), "must be a string")
                meeting[key] = value
        if meeting["transcript_text"] is not None and meeting["transcript"] != "ok":
            raise r.fail(p + ".transcript_text", "is present but transcript status is %r" % meeting["transcript"])
        if meeting["notes_text"] is not None and meeting["notes"] != "ok":
            raise r.fail(p + ".notes_text", "is present but notes status is %r" % meeting["notes"])
        meetings.append(meeting)
        by_id[mid] = meeting
    b["meetings"] = meetings
    b["meeting_by_id"] = by_id

    evidence = []
    seen = set()
    for i, e in enumerate(r.lst(raw.get("evidence"), "$.evidence", required=True)):
        p = "$.evidence[%d]" % i
        r.obj(e, p)
        eid = r.text(e, "id", p, required=False, max_len=80) or "e%d" % (i + 1)
        if eid in seen:
            raise r.fail(p + ".id", "duplicate evidence id %r" % eid)
        seen.add(eid)
        deal_id = r.text(e, "deal_id", p)
        if deal_id not in deals:
            raise r.fail(p + ".deal_id", "%s is not in the deal list; every evidence item must cite a deal"
                         % short_repr(deal_id))
        deal = deals[deal_id]
        kind = r.enum(e, "kind", p, EVIDENCE_KINDS)
        category = None
        if kind in TAXONOMY_SECTIONS:
            section = TAXONOMY_SECTIONS[kind]
            category = r.text(e, "category", p)
            if category not in taxonomy[section]:
                raise r.fail(p + ".category", "%s is not a key in the taxonomy's %s (run --list-categories)"
                             % (short_repr(category), section))
        elif e.get("category") is not None:
            raise r.fail(p + ".category", "is only used for loss_reason, win_reason and objection evidence")
        if kind == "loss_reason" and deal["outcome"] != "lost":
            raise r.fail(p + ".kind", "a loss_reason cites deal %r, which was won" % deal_id)
        if kind == "win_reason" and deal["outcome"] != "won":
            raise r.fail(p + ".kind", "a win_reason cites deal %r, which was lost" % deal_id)
        competitor = r.prose(e, "competitor", p, required=(kind == "competitor_mention"),
                             max_len=MAX_LEN["competitor"])
        source = r.enum(e, "source", p, EVIDENCE_SOURCES)
        raw_quote = r.text(e, "quote", p)
        quote_text = strip_outer_quotes(raw_quote)
        if not quote_text:
            raise r.fail(p + ".quote", "is empty once surrounding quote marks are removed")
        if len(quote_text) > config["max_quote_chars"]:
            raise r.fail(p + ".quote", "is %d characters; keep quotes to the relevant sentence or two (max %d)"
                         % (len(quote_text), config["max_quote_chars"]))
        item = {"index": i, "id": eid, "deal_id": deal_id, "kind": kind, "category": category,
                "competitor": competitor, "source": source, "meeting_id": None, "crm_field": None,
                "speaker_role": None, "speaker_side": None, "verified": False}
        if source == "crm_field":
            if kind == "objection":
                raise r.fail(p + ".source", "objections must cite a meeting (a CRM field has no date to show it "
                             "came before the close)")
            if e.get("meeting_id") is not None:
                raise r.fail(p + ".meeting_id", "is not used with source crm_field; cite the field in crm_field")
            fname = r.text(e, "crm_field", p)
            match = [k for k in deal["crm_fields"] if field_key(k) == field_key(fname)]
            if not match:
                raise r.fail(p + ".crm_field", "%s is not a field in deal %r's crm_fields%s"
                             % (short_repr(fname), deal_id,
                                " (fields: %s)" % ", ".join(sorted(deal["crm_fields"]))
                                if deal["crm_fields"] else " (it has none)"))
            item["crm_field"] = match[0]
            problem = crm_quote_problem(quote_text, deal["crm_fields"][match[0]])
            if problem:
                raise r.fail(p + ".quote", "is not deal %r's CRM field %r as read (%s); quote the whole field value, "
                             "or whole sentences of it" % (deal_id, match[0], problem))
            item["verified"] = True
        else:
            mid = r.text(e, "meeting_id", p)
            if mid not in by_id:
                raise r.fail(p + ".meeting_id", "%s is not in $.meetings; every quote must cite a meeting"
                             % short_repr(mid))
            if e.get("crm_field") is not None:
                raise r.fail(p + ".crm_field", "is only used with source crm_field")
            meeting = by_id[mid]
            item["meeting_id"] = mid
            if source == "transcript":
                if meeting["transcript"] != "ok":
                    raise r.fail(p + ".source", "is 'transcript' but meeting %r has transcript status %r; use "
                                 "'note' for text copied from the notes" % (mid, meeting["transcript"]))
                if e.get("speaker_side") == "internal":
                    raise r.fail(p + ".speaker_side", "internal speakers are not the customer's reasons; drop "
                                 "this item")
                item["speaker_side"] = r.enum(e, "speaker_side", p, SPEAKER_SIDES, "customer")
                role = r.text(e, "speaker_role", p)
            else:
                if meeting["notes"] != "ok":
                    raise r.fail(p + ".source", "is 'note' but meeting %r has notes status 'unavailable'" % mid)
                role = r.text(e, "speaker_role", p, required=False)
            if role is not None:
                if gt.redact_contacts(role) != role or URL_RE.search(role):
                    raise r.fail(p + ".speaker_role", "must be a role such as 'Head of Ops', never an email, phone "
                                 "number or link")
                if len(role) > config["max_role_chars"]:
                    raise r.fail(p + ".speaker_role", "is longer than %d characters; use a short role"
                                 % config["max_role_chars"])
            item["speaker_role"] = role
            field = "transcript_text" if source == "transcript" else "notes_text"
            problem = quote_problem(quote_text, meeting[field])
            if problem:
                raise r.fail(p + ".quote", "fails the whole-sentence check against meeting %r's %s (%s); quote one "
                             "or more complete sentences from one speaker turn, exactly as written (no ellipses, no "
                             "cut sentences, no speaker labels, no paraphrase)"
                             % (mid, field if meeting[field] is not None else field + " (not in the bundle)",
                                problem))
            if (source == "transcript" and item["speaker_side"] == "customer" and meeting[field] is not None
                    and gt.quote_is_seller_only(quote_text, meeting[field])):
                raise r.fail(p + ".quote", "is from a Microphone/Me turn in meeting %r's transcript_text: that is your "
                             "own side as Granola labels it, not the customer; quote the customer's turn (System "
                             "audio / Them / their name), or drop this item" % mid)
            item["verified"] = meeting[field] is not None
        item["quote"] = gt.redact_contacts(quote_text)
        evidence.append(item)
    b["evidence"] = evidence

    # lost_to must be backed by the CRM or by the customer's own words.
    for deal_id in order:
        d = deals[deal_id]
        if d["lost_to"] is None:
            continue
        key = name_key(d["lost_to"])
        crm_ok = any(name_key(v) == key for v in d["crm_fields"].values())
        crm_ok = crm_ok or d["lost_to_in_csv"]  # a CSV export's Lost To column is the CRM's own record
        quote_ok = any(e["deal_id"] == deal_id and e["kind"] == "loss_reason" and e["competitor"] is not None
                       and name_key(e["competitor"]) == key for e in evidence)
        if not (crm_ok or quote_ok):
            raise InputError("%s: %s.lost_to: %r is not backed by a CRM field with that value or a loss_reason "
                             "evidence item naming it as competitor; leave lost_to out unless the CRM or the "
                             "customer says so" % (b["label"], d["path"], d["lost_to"]))
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


def money(symbol, amount):
    """Markdown amount: the symbol is escaped, so '$51,000 | $72,000' in one table row is not a math span."""
    return "%s%s" % (gt.md(symbol), "{:,.0f}".format(amount))


def percent(n, d):
    """Whole percent, rounded half up, with integer arithmetic (identical on every Python)."""
    return (200 * n + d) // (2 * d)


def ratio(n, d, min_sample, noun="deals"):
    """'3 of 7 deals (43%)', or '3 of 4 deals' when the cut is smaller than min_sample."""
    text = "%d of %d" % (n, d) + (" " + noun if noun else "")
    if d >= min_sample:
        text += " (%d%%)" % percent(n, d)
    return text


def rate_cell(n, d, min_sample):
    if d == 0:
        return "—"
    if d < min_sample:
        return "n<%d" % min_sample
    return "%d%%" % percent(n, d)


def classify(b, config, internal_domains, internal_emails, as_of):
    """Bucket meetings, merge duplicate notes, and link meetings to deals."""
    deals = b["deals"]
    in_period = set(k for k, d in deals.items() if b["period_start"] <= d["close_date"] <= b["period_end"])
    b["in_period"] = in_period
    domain_deals = {}
    for deal_id in b["deal_order"]:
        for dom in deals[deal_id]["domains"]:
            domain_deals.setdefault(dom, []).append(deal_id)
    free = config["free_email_domains"]

    def deals_for_domain(dom):
        out = []
        for d, ids in sorted(domain_deals.items()):
            if dom == d or dom.endswith("." + d):
                out.extend(ids)
        return out

    for m in b["meetings"]:
        if m["start"] > as_of:
            raise InputError("%s: $.meetings[%d].start: %s is after --as-of" % (b["label"], m["index"],
                                                                                 m["start"].isoformat()))
        people = [a for a in m["attendees"] if not is_bot(a, config)]
        external = [a for a in people if a not in internal_emails and not in_domains(domain_of(a), internal_domains)]
        m["external"] = external
        auto = []
        for a in external:
            dom = domain_of(a)
            if in_domains(dom, free):
                continue
            for did in deals_for_domain(dom):
                if did not in auto:
                    auto.append(did)
        m["auto_deals"] = auto
        m["linked"] = list(auto) + [d for d in m["explicit_deals"] if d not in auto]
        m["unmatched_explicit"] = [d for d in m["explicit_deals"] if d not in auto]
        if not external and len(people) <= 1:
            m["bucket"] = "attendees_unknown"
        elif not external:
            m["bucket"] = "internal_only"
        elif not m["linked"]:
            m["bucket"] = "unlinked"
        elif not [d for d in m["linked"] if d in in_period]:
            m["bucket"] = "other_period"
        else:
            m["bucket"] = "linked"
        m["canonical"] = m["meeting_id"]
        m["duplicates"] = []

    # Duplicate notes: same start minute and a shared external attendee (directly or through a third note).
    groups = {}
    for m in b["meetings"]:
        if m["bucket"] in ("linked", "other_period", "unlinked"):
            groups.setdefault(m["start"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"), []).append(m)
    for instant in sorted(groups):
        group = sorted(groups[instant], key=lambda x: (0 if x["transcript"] == "ok" else 1,
                                                       0 if x["notes"] == "ok" else 1, x["meeting_id"]))
        emails = [set(m["external"]) for m in group]
        root = list(range(len(group)))
        for i in range(len(group)):
            for j in range(i):
                if emails[i] & emails[j]:
                    old_root, new_root = max(root[i], root[j]), min(root[i], root[j])
                    root = [new_root if x == old_root else x for x in root]
        for i, m in enumerate(group):
            target = group[root[i]]
            if target is not m:
                m["canonical"] = target["meeting_id"]
                target["duplicates"].append(m["meeting_id"])
                for did in m["linked"]:
                    if did not in target["linked"]:
                        target["linked"].append(did)
                if target["bucket"] != "linked" and [d for d in target["linked"] if d in in_period]:
                    target["bucket"] = "linked"
                m["bucket"] = "duplicate_note"

    by_deal = dict((d, []) for d in deals)
    for m in b["meetings"]:
        if m["bucket"] in ("linked", "other_period"):
            for did in m["linked"]:
                by_deal[did].append(m)
    for did in by_deal:
        by_deal[did].sort(key=lambda x: (x["start"].astimezone(timezone.utc), x["meeting_id"]))
    b["meetings_by_deal"] = by_deal


def linked_to(b, meeting, deal_id):
    canonical = b["meeting_by_id"][meeting["canonical"]]
    return deal_id in canonical["linked"] or deal_id in meeting["linked"]


def analyse(b, config, taxonomy, weighting):
    deals = b["deals"]
    in_period = b["in_period"]
    min_sample = config["min_sample_for_percent"]
    by_id = b["meeting_by_id"]
    dropped = {"deal_outside_period": 0, "internal_meeting": 0, "objection_after_close": 0}

    # Evidence: drop what is out of scope, check links, remap duplicates, dedupe.
    kept = []
    seen = set()
    for e in b["evidence"]:
        deal = deals[e["deal_id"]]
        if e["deal_id"] not in in_period:
            dropped["deal_outside_period"] += 1
            continue
        if e["meeting_id"] is not None:
            m = by_id[e["meeting_id"]]
            if m["bucket"] in ("internal_only", "attendees_unknown"):
                dropped["internal_meeting"] += 1
                continue
            if not linked_to(b, m, e["deal_id"]):
                raise InputError("%s: $.evidence[%d].meeting_id: meeting %r is not linked to deal %r (no external "
                                 "attendee on the deal's domains and the deal is not in the meeting's deal_ids)"
                                 % (b["label"], e["index"], e["meeting_id"], e["deal_id"]))
            canonical = by_id[m["canonical"]]
            e["meeting"] = canonical
            if e["kind"] == "objection" and canonical["start"].date() > deal["close_date"]:
                dropped["objection_after_close"] += 1
                continue
        else:
            e["meeting"] = None
        comp = name_key(e["competitor"]) if e["competitor"] else ""
        key = (e["deal_id"], e["kind"], e["category"] or "", comp,
               e["meeting"]["meeting_id"] if e["meeting"] else "crm:" + (e["crm_field"] or ""), quote_key(e["quote"]))
        if key in seen:
            continue
        seen.add(key)
        kept.append(e)

    # Competitor display names: first spelling met (deals in order, then evidence in order).
    comp_names = {}

    def comp_name(name):
        k = name_key(name)
        if k not in comp_names:
            comp_names[k] = name
        return comp_names[k]

    period_deals = [deals[d] for d in b["deal_order"] if d in in_period]
    for d in period_deals:
        d["comp_keys"] = []
        for c in d["competitors"] + ([d["lost_to"]] if d["lost_to"] else []):
            comp_name(c)
            if name_key(c) not in d["comp_keys"]:
                d["comp_keys"].append(name_key(c))
        d["lost_to_key"] = name_key(d["lost_to"]) if d["lost_to"] else None
        d["evidence"] = [e for e in kept if e["deal_id"] == d["deal_id"]]
        d["mentioned_keys"] = []
        for e in d["evidence"]:
            if e["competitor"]:
                comp_name(e["competitor"])
                if name_key(e["competitor"]) not in d["comp_keys"] and \
                        name_key(e["competitor"]) not in d["mentioned_keys"]:
                    d["mentioned_keys"].append(name_key(e["competitor"]))
        meetings = b["meetings_by_deal"][d["deal_id"]]
        d["meetings"] = meetings
        d["readable_meetings"] = [m for m in meetings if m["notes"] == "ok" or m["transcript"] == "ok"]
        if d["outcome"] == "lost":
            loss_reasons = [e for e in d["evidence"] if e["kind"] == "loss_reason"]
            nd = [e for e in loss_reasons if taxonomy["loss_reasons"][e["category"]]["no_decision"]]
            if d["lost_to"]:
                d["loss_type"] = "competitive"
            elif loss_reasons and len(nd) == len(loss_reasons):
                d["loss_type"] = "no_decision"
            elif loss_reasons:
                d["loss_type"] = "other"
            else:
                d["loss_type"] = "unknown"
            d["loss_type_conflict"] = bool(nd) and (bool(d["lost_to"]) or len(nd) < len(loss_reasons))
        else:
            d["loss_type"] = None
            d["loss_type_conflict"] = False

    won = [d for d in period_deals if d["outcome"] == "won"]
    lost = [d for d in period_deals if d["outcome"] == "lost"]

    def amount_of(ds):
        known = [d["amount"] for d in ds if d["amount"] is not None]
        return sum(known), len(ds) - len(known)

    def rank_categories(kind, section, pool):
        cats = {}
        for d in pool:
            for e in d["evidence"]:
                if e["kind"] != kind:
                    continue
                c = cats.setdefault(e["category"], {"key": e["category"],
                                                    "label": taxonomy[section][e["category"]]["label"],
                                                    "deals": [], "evidence": []})
                if d not in c["deals"]:
                    c["deals"].append(d)
                c["evidence"].append(e)
        out = []
        for c in cats.values():
            c["amount"], c["amount_unknown"] = amount_of(c["deals"])
            out.append(c)
        if weighting == "amount":
            out.sort(key=lambda c: (-c["amount"], -len(c["deals"]), c["label"].casefold(), c["key"]))
        else:
            out.sort(key=lambda c: (-len(c["deals"]), -c["amount"], c["label"].casefold(), c["key"]))
        return out

    loss_groups = []
    for lt in LOSS_TYPES:
        pool = [d for d in lost if d["loss_type"] == lt]
        loss_groups.append({"type": lt, "deals": pool, "reasons": rank_categories("loss_reason", "loss_reasons", pool)
                            if lt != "unknown" else []})
    all_loss_reasons = rank_categories("loss_reason", "loss_reasons", lost)
    win_reasons = rank_categories("win_reason", "win_reasons", won)
    won_no_reason = [d for d in won if not [e for e in d["evidence"] if e["kind"] == "win_reason"]]

    # Competitors: head-to-head = CRM-listed or lost to; mentions on calls alone are shown, not rated.
    comps = {}
    for d in period_deals:
        for k in d["comp_keys"]:
            comps.setdefault(k, {"key": k, "deals": [], "mentioned_only": []})["deals"].append(d)
        for k in d["mentioned_keys"]:
            comps.setdefault(k, {"key": k, "deals": [], "mentioned_only": []})["mentioned_only"].append(d)
    comp_list = []
    for k, c in comps.items():
        c["name"] = comp_names[k]
        c["won"] = [d for d in c["deals"] if d["outcome"] == "won"]
        c["lost_to_them"] = [d for d in c["deals"] if d["outcome"] == "lost" and d["lost_to_key"] == k]
        c["lost_other"] = [d for d in c["deals"] if d["outcome"] == "lost" and d["lost_to_key"] != k]
        c["quotes"] = [e for d in c["deals"] + c["mentioned_only"] for e in d["evidence"]
                       if e["competitor"] and name_key(e["competitor"]) == k]
        c["amount"], c["amount_unknown"] = amount_of(c["deals"])
        comp_list.append(c)
    comp_list.sort(key=lambda c: (-len(c["deals"]), -len(c["mentioned_only"]), c["name"].casefold(), c["key"]))

    # Stage of loss.
    stage_order = [name_key(s) for s in config["stage_order"]]
    stages = {}
    for d in lost:
        label = d["stage_lost_at"] or "(stage not recorded)"
        st = stages.setdefault(name_key(label), {"label": label, "deals": [], "recorded": d["stage_lost_at"] is not None})
        st["deals"].append(d)
    stage_list = list(stages.values())
    for st in stage_list:
        st["amount"], st["amount_unknown"] = amount_of(st["deals"])

    def stage_rank(st):
        k = name_key(st["label"])
        if not st["recorded"]:
            return (2, 0, "")
        if k in stage_order:
            return (0, stage_order.index(k), "")
        return (1, -len(st["deals"]), st["label"].casefold())
    stage_list.sort(key=stage_rank)

    # Segments.
    segs = {}
    for d in period_deals:
        label = d["segment"] or "(no segment)"
        sg = segs.setdefault(name_key(label), {"label": label, "deals": [], "recorded": d["segment"] is not None})
        sg["deals"].append(d)
    seg_list = list(segs.values())
    for sg in seg_list:
        sg["won"] = [d for d in sg["deals"] if d["outcome"] == "won"]
        sg["lost"] = [d for d in sg["deals"] if d["outcome"] == "lost"]
        sg["amount_won"], sg["unknown_won"] = amount_of(sg["won"])
        sg["amount_lost"], sg["unknown_lost"] = amount_of(sg["lost"])
    seg_list.sort(key=lambda s: (0 if s["recorded"] else 1, -len(s["deals"]), s["label"].casefold()))

    # Objections raised before the close.
    objs = {}
    for d in period_deals:
        for e in d["evidence"]:
            if e["kind"] != "objection":
                continue
            o = objs.setdefault(e["category"], {"key": e["category"],
                                                "label": taxonomy["objections"][e["category"]]["label"],
                                                "lost": [], "won": [], "evidence": []})
            bucket = o["lost"] if d["outcome"] == "lost" else o["won"]
            if d not in bucket:
                bucket.append(d)
            o["evidence"].append(e)
    obj_list = list(objs.values())
    for o in obj_list:
        o["amount"], o["amount_unknown"] = amount_of(o["lost"])
    if weighting == "amount":
        obj_list.sort(key=lambda o: (-o["amount"], -len(o["lost"]), len(o["won"]), o["label"].casefold()))
    else:
        obj_list.sort(key=lambda o: (-len(o["lost"]), len(o["won"]), o["label"].casefold()))

    no_coverage = [d for d in period_deals if not d["readable_meetings"]]

    buckets = {}
    for m in b["meetings"]:
        buckets[m["bucket"]] = buckets.get(m["bucket"], 0) + 1
    transcripts = {}
    for m in b["meetings"]:
        if m["bucket"] == "linked":
            transcripts[m["transcript"]] = transcripts.get(m["transcript"], 0) + 1
    quotes_meeting = [e for e in kept if e["source"] != "crm_field"]
    a = {
        "period_deals": period_deals, "won": won, "lost": lost,
        "outside_period": [deals[d] for d in b["deal_order"] if d not in in_period],
        "loss_groups": loss_groups, "all_loss_reasons": all_loss_reasons, "win_reasons": win_reasons,
        "won_no_reason": won_no_reason, "competitors": comp_list, "stages": stage_list, "segments": seg_list,
        "objections": obj_list, "no_coverage": no_coverage, "dropped": dropped, "buckets": buckets,
        "transcripts": transcripts, "evidence": kept, "evidence_raw": len(b["evidence"]),
        "verified": len([e for e in quotes_meeting if e["verified"]]), "quotes_meeting": len(quotes_meeting),
        "crm_evidence": len(kept) - len(quotes_meeting),
        "explicit_links": [m for m in b["meetings"] if m["bucket"] in ("linked", "other_period")
                           and [d for d in m["unmatched_explicit"] if d in in_period]],
        "conflicts": [d for d in lost if d["loss_type_conflict"]],
        "competitor_names": comp_names,
        "weighting": weighting,
    }
    return a


# --------------------------------------------------------------------------- rendering helpers


def provider_label(p):
    if not p:
        return None
    return PROVIDER_LABELS.get(p.lower(), p)


def meeting_cite(m):
    return md_link("%s, %s" % (m["title"], short_date(m["start"].date())), m["url"])


def deal_label(d, symbol, with_details=True):
    """'**Account** (D-1002 · $60,000 · lost at Proposal · to Countwhistle)'"""
    bits = [gt.md(d["deal_id"])]
    if with_details:
        if d["amount"] is not None:
            bits.append(money(symbol, d["amount"]))
        if d["outcome"] == "lost":
            if d["stage_lost_at"]:
                bits.append("lost at %s" % gt.md(d["stage_lost_at"]))
            if d["lost_to"]:
                bits.append("we lost to %s" % gt.md(d["lost_to"]))
    return "**%s** (%s)" % (gt.md(d["account"]), " · ".join(bits))


def evidence_text(e, deal):
    """One evidence line body with its source label."""
    if e["source"] == "transcript":
        role = gt.md(e["speaker_role"])
        if e["speaker_side"] == "unconfirmed":
            role += " \\(speaker unconfirmed\\)"
        return "“%s” — %s · %s" % (gt.md(e["quote"]), role, meeting_cite(e["meeting"]))
    if e["source"] == "note":
        role = " — %s" % gt.md(e["speaker_role"]) if e["speaker_role"] else ""
        return "Note excerpt (AI meeting notes, not verbatim speech): _%s_%s · %s" % (
            gt.md(e["quote"]), role, meeting_cite(e["meeting"]))
    field = md_link("CRM field “%s”" % e["crm_field"], deal["crm_url"])
    return "%s: “%s”" % (field, gt.md(e["quote"]))


def pick_quotes(evidence, deals_by_id, limit):
    """Up to `limit` evidence items: one per deal first (in ranking order), then the rest; identical text once."""
    first, rest, texts = [], [], set()
    seen_deals = set()
    for e in evidence:
        t = quote_key(e["quote"])
        if (e["deal_id"], t) in texts:
            continue
        texts.add((e["deal_id"], t))
        if e["deal_id"] not in seen_deals:
            seen_deals.add(e["deal_id"])
            first.append(e)
        else:
            rest.append(e)
    return (first + rest)[:limit]


def evidence_order(e):
    m = e["meeting"]
    source_rank = {"transcript": 0, "note": 1, "crm_field": 2}[e["source"]]
    when = m["start"].astimezone(timezone.utc).isoformat() if m else ""
    return (source_rank, when, e["index"])


def amount_note(total, unknown, symbol, n):
    if n and unknown == n:
        return "unknown"
    text = money(symbol, total)
    if unknown:
        text += " (amount unknown for %d of %d)" % (unknown, n)
    return text


def deal_list_inline(ds):
    return "; ".join("%s \\(%s\\)" % (gt.md(d["account"]), gt.md(d["deal_id"])) for d in ds)


def source_status_text(key, src):
    label = SOURCE_LABELS[key]
    prov = provider_label(src["provider"])
    name = "%s: %s" % (label, gt.md(prov)) if prov else label
    if src["status"] == "ok":
        return "%s ✓" % name
    return "%s %s" % (name, src["status"].replace("_", " "))


def gaps(b):
    out = []
    for key in SOURCE_KEYS:
        src = b["sources"][key]
        if src["status"] != "ok":
            detail = " (%s)" % gt.md(src["detail"]) if src["detail"] else ""
            out.append("%s %s%s" % (SOURCE_LABELS[key], src["status"].replace("_", " "), detail))
    return out


def top_phrase(items, label_of, count_of, noun):
    """'Price / value (3 lost deals)' or 'tied: A, B (2 lost deals each)'. Always by deal count, even when the
    list itself is ranked by amount (--weighting amount)."""
    if not items:
        return None
    best = max(count_of(i) for i in items)
    tied = [i for i in items if count_of(i) == best]
    plural = noun if best == 1 else noun + "s"
    if len(tied) == 1:
        return "%s (%d %s)" % (label_of(tied[0]), best, plural)
    names = ", ".join(label_of(i) for i in tied[:3]) + (" and %d more" % (len(tied) - 3) if len(tied) > 3 else "")
    return "tied: %s (%d %s each)" % (names, best, plural)


# --------------------------------------------------------------------------- markdown


def title_text(b):
    when = period_text(b["period_start"], b["period_end"])
    if b["period_label"]:
        return "Win/Loss Analysis — %s" % gt.md(b["period_label"]), when
    return "Win/Loss Analysis — %s" % when, None


def render_markdown(b, a, config, as_of):
    symbol = config["currency_symbol"]
    ms = config["min_sample_for_percent"]
    weighted = a["weighting"] == "amount"
    won, lost, total = a["won"], a["lost"], len(a["period_deals"])
    head, sub = title_text(b)
    out = ["# %s" % head, ""]
    if sub:
        out += ["%s · closed deals, as of %s" % (sub, long_date(as_of.date())), ""]
    else:
        out += ["Closed deals, as of %s" % long_date(as_of.date()), ""]
    if b["sources"]["deals"]["status"] != "ok":
        out += ["> ⚠️ **Deal list incomplete** — the deal source is %s, so counts and rates below cover only the "
                "deals that were read." % b["sources"]["deals"]["status"].replace("_", " "), ""]
    if b["sources"]["meetings"]["status"] != "ok":
        out += ["> ⚠️ **Call coverage incomplete** — the meeting list is %s, so deals listed under \"No call "
                "coverage\" may have had calls that were not read, and missing reasons are not evidence of "
                "anything." % b["sources"]["meetings"]["status"].replace("_", " "), ""]

    # At a glance
    if total == 0:
        glance = "No closed deals in this period in the deal list that was read."
    else:
        glance = "%d closed deal%s: %d won, %d lost" % (total, "" if total == 1 else "s", len(won), len(lost))
        glance += " (win rate %d%%)." % percent(len(won), total) if total >= ms else \
            " (win rate not shown: fewer than %d deals)." % ms
        if lost:
            parts = []
            for g in a["loss_groups"]:
                if g["deals"]:
                    parts.append("%d %s" % (len(g["deals"]), LOSS_TYPE_SHORT[g["type"]]))
            glance += " Losses: %s." % ", ".join(parts)
        tl = top_phrase(a["all_loss_reasons"], lambda c: gt.md(c["label"]), lambda c: len(c["deals"]), "lost deal")
        if tl:
            glance += " Top loss reason: %s." % tl
        tw = top_phrase(a["win_reasons"], lambda c: gt.md(c["label"]), lambda c: len(c["deals"]), "won deal")
        if tw:
            glance += " Top win reason: %s." % tw
        if a["competitors"] and a["competitors"][0]["deals"]:
            c = a["competitors"][0]
            glance += " Most-seen competitor: %s, in %d deal%s; we won %s." % (
                gt.md(c["name"]), len(c["deals"]), "" if len(c["deals"]) == 1 else "s",
                ratio(len(c["won"]), len(c["deals"]), ms, ""))
        if a["no_coverage"]:
            n = len(a["no_coverage"])
            glance += " %d deal%s %s no call coverage." % (n, "" if n == 1 else "s", "has" if n == 1 else "have")
    out += ["**At a glance** — %s" % glance, ""]
    out += ["_Counted by distinct deals: a deal with three calls that all mention price counts once. Percentages "
            "appear only when the group has at least %d deals; smaller groups show counts. Every reason cites "
            "a customer quote from a call transcript, a labelled note excerpt, or a CRM field._" % ms, ""]

    # Why we lose
    out += ["## Why we lose", ""]
    if not lost:
        out += ["- No lost deals in this period.", ""]
    for g in a["loss_groups"]:
        if not g["deals"]:
            continue
        n = len(g["deals"])
        out += ["### %s (%d deal%s)" % (LOSS_TYPE_TITLES[g["type"]], n, "" if n == 1 else "s"), ""]
        if g["type"] == "competitive":
            out += ["_Lost to a named competitor, as recorded in a CRM field or in a cited loss reason._", ""]
        elif g["type"] == "no_decision":
            out += ["_Every cited reason is a no-decision category in the taxonomy (for example they stayed with "
                    "the status quo)._", ""]
        elif g["type"] == "other":
            out += ["_Lost with a cited reason, but not to a named competitor and not as no decision._", ""]
        if g["type"] == "unknown":
            out += ["_No loss reason in the CRM fields read and none stated on the calls read. Not counted in any "
                    "reason below; never read silence as a reason._", ""]
            for d in g["deals"]:
                calls = len(d["readable_meetings"])
                out.append("- %s — %s" % (deal_label(d, symbol), "%d call%s read" % (calls, "" if calls == 1 else "s")
                                          if calls else "no call coverage"))
            out.append("")
            continue
        for rank, c in enumerate(g["reasons"], 1):
            line = "%d. **%s** — %s" % (rank, gt.md(c["label"]), ratio(len(c["deals"]), n, ms, "%s %s" % (
                LOSS_TYPE_SHORT[g["type"]].replace("no decision", "no-decision"), "loss" if n == 1 else "losses")))
            if weighted:
                line += " · amount %s" % amount_note(c["amount"], c["amount_unknown"], symbol, len(c["deals"]))
            out.append(line)
            deals_by_id = dict((d["deal_id"], d) for d in c["deals"])
            ev = sorted(c["evidence"], key=lambda e: (c["deals"].index(deals_by_id[e["deal_id"]]),) + evidence_order(e))
            for e in pick_quotes(ev, deals_by_id, config["max_quotes_per_reason"]):
                out.append("   - %s: %s" % (deal_label(deals_by_id[e["deal_id"]], symbol),
                                             evidence_text(e, deals_by_id[e["deal_id"]])))
            out.append("   - Deals: %s" % deal_list_inline(c["deals"]))
        if g["type"] == "competitive":
            nores = [d for d in g["deals"] if not [e for e in d["evidence"] if e["kind"] == "loss_reason"]]
            if nores:
                out.append("- Lost to a competitor with no further reason cited: %s" % deal_list_inline(nores))
        out.append("")
    if a["conflicts"]:
        out += ["- ⚠️ Mixed loss-type evidence (check the CRM): %s." % "; ".join(
            "%s \\(%s\\): %s" % (gt.md(d["account"]), gt.md(d["deal_id"]),
                                 "we lost to %s but also a no-decision reason" % gt.md(d["lost_to"]) if d["lost_to"]
                                 else "no-decision and other reasons both cited") for d in a["conflicts"]), ""]

    # Why we win
    out += ["## Why we win", ""]
    if not won:
        out += ["- No won deals in this period.", ""]
    else:
        n = len(won)
        for rank, c in enumerate(a["win_reasons"], 1):
            line = "%d. **%s** — %s" % (rank, gt.md(c["label"]), ratio(len(c["deals"]), n, ms,
                                                                     "won deal" if n == 1 else "won deals"))
            if weighted:
                line += " · amount %s" % amount_note(c["amount"], c["amount_unknown"], symbol, len(c["deals"]))
            out.append(line)
            deals_by_id = dict((d["deal_id"], d) for d in c["deals"])
            ev = sorted(c["evidence"], key=lambda e: (c["deals"].index(deals_by_id[e["deal_id"]]),) + evidence_order(e))
            for e in pick_quotes(ev, deals_by_id, config["max_quotes_per_reason"]):
                out.append("   - %s: %s" % (deal_label(deals_by_id[e["deal_id"]], symbol),
                                             evidence_text(e, deals_by_id[e["deal_id"]])))
            out.append("   - Deals: %s" % deal_list_inline(c["deals"]))
        if a["won_no_reason"]:
            out.append("- Won with no reason cited in the CRM fields or calls read: %s" %
                       deal_list_inline(a["won_no_reason"]))
        out.append("")

    # Competitors
    out += ["## Competitor head-to-head", ""]
    rated = [c for c in a["competitors"] if c["deals"]]
    if not a["competitors"]:
        out += ["- No competitor recorded in the CRM or mentioned in the evidence.", ""]
    else:
        if rated:
            out += ["_Every figure is from our side: deals = deals where the CRM lists the competitor or we lost "
                    "to them; \"We won\" = deals we won against them. Calls where a customer only mentioned them "
                    "are counted separately and not rated._", ""]
            out += ["| Competitor | Deals | We won | We lost to them | We lost otherwise | Our win rate | "
                    "Mentioned on calls only |",
                    "|---|---|---|---|---|---|---|"]
            for c in rated:
                n = len(c["deals"])
                out.append("| %s | %d | %d | %d | %d | %s | %d |" % (
                    gt.md(c["name"]), n, len(c["won"]), len(c["lost_to_them"]), len(c["lost_other"]),
                    rate_cell(len(c["won"]), n, ms), len(c["mentioned_only"])))
            out.append("")
        for c in a["competitors"]:
            n = len(c["deals"])
            if n:
                head_line = "**%s** — %d deal%s against them; we won %s" % (gt.md(c["name"]), n, "" if n == 1 else "s",
                                                                ratio(len(c["won"]), n, ms, ""))
                if c["lost_to_them"]:
                    head_line += "; we lost to them: %s" % deal_list_inline(c["lost_to_them"])
            else:
                head_line = "**%s** — mentioned on calls in %d deal%s (not in the CRM's competitor field)" % (
                    gt.md(c["name"]), len(c["mentioned_only"]), "" if len(c["mentioned_only"]) == 1 else "s")
            out.append("- " + head_line)
            deals_by_id = dict((d["deal_id"], d) for d in c["deals"] + c["mentioned_only"])
            ordered = c["deals"] + c["mentioned_only"]
            ev = sorted(c["quotes"], key=lambda e: (ordered.index(deals_by_id[e["deal_id"]]),) + evidence_order(e))
            quotes = pick_quotes([e for e in ev if e["source"] != "crm_field"], deals_by_id,
                                 config["max_quotes_per_competitor"])
            if quotes:
                for e in quotes:
                    out.append("   - %s: %s" % (deal_label(deals_by_id[e["deal_id"]], symbol, False),
                                                 evidence_text(e, deals_by_id[e["deal_id"]])))
            else:
                out.append("   - No customer quote about them in the calls read.")
        out.append("")

    # Stage
    out += ["## Where losses happen (stage)", ""]
    if not lost:
        out += ["- No lost deals in this period.", ""]
    else:
        nl = len(lost)
        out += ["| Stage lost at | Lost deals | Share of losses | Amount |", "|---|---|---|---|"]
        for st in a["stages"]:
            k = len(st["deals"])
            out.append("| %s | %d | %s | %s |" % (gt.md(st["label"]), k, rate_cell(k, nl, ms),
                                                 amount_note(st["amount"], st["amount_unknown"], symbol, k)))
        out.append("")

    # Segment
    out += ["## By segment", ""]
    if not a["period_deals"]:
        out += ["- No closed deals in this period.", ""]
    else:
        out += ["| Segment | Deals | Won | Lost | Win rate | Won amount | Lost amount |", "|---|---|---|---|---|---|---|"]
        for sg in a["segments"]:
            n = len(sg["deals"])
            out.append("| %s | %d | %d | %d | %s | %s | %s |" % (
                gt.md(sg["label"]), n, len(sg["won"]), len(sg["lost"]), rate_cell(len(sg["won"]), n, ms),
                amount_note(sg["amount_won"], sg["unknown_won"], symbol, len(sg["won"])),
                amount_note(sg["amount_lost"], sg["unknown_lost"], symbol, len(sg["lost"]))))
        out.append("")

    # Objections
    out += ["## Objections raised before a loss", ""]
    rec = config["recurring_min_deals"]
    recurring = [o for o in a["objections"] if len(o["lost"]) >= rec]
    single = [o for o in a["objections"] if 0 < len(o["lost"]) < rec]
    won_only = [o for o in a["objections"] if not o["lost"]]
    if not a["objections"]:
        out += ["- No objections cited from calls before the close.", ""]
    else:
        out += ["_From calls on or before each deal's close date. Recurring = raised in %d or more lost deals._" % rec,
                ""]
        if not recurring:
            out.append("- None raised in %d or more lost deals." % rec)
        for o in recurring:
            line = "- **%s** — before the loss in %d lost deal%s; also raised in %d won deal%s" % (
                gt.md(o["label"]), len(o["lost"]), "" if len(o["lost"]) == 1 else "s", len(o["won"]),
                "" if len(o["won"]) == 1 else "s")
            if weighted:
                line += " · lost amount %s" % amount_note(o["amount"], o["amount_unknown"], symbol, len(o["lost"]))
            out.append(line)
            deals_by_id = dict((d["deal_id"], d) for d in o["lost"] + o["won"])
            ordered = o["lost"] + o["won"]
            ev = sorted([e for e in o["evidence"] if deals_by_id[e["deal_id"]] in o["lost"]],
                        key=lambda e: (ordered.index(deals_by_id[e["deal_id"]]),) + evidence_order(e))
            for e in pick_quotes(ev, deals_by_id, config["max_quotes_per_reason"]):
                out.append("   - %s: %s" % (deal_label(deals_by_id[e["deal_id"]], symbol, False),
                                             evidence_text(e, deals_by_id[e["deal_id"]])))
        if single:
            out.append("- %s: %s" % ("Raised before a loss in one lost deal" if rec == 2 else
                                     "Raised in fewer than %d lost deals" % rec,
                                     "; ".join("%s \\(%s\\)" % (gt.md(o["label"]), deal_list_inline(o["lost"]))
                                               for o in single)))
        if won_only:
            out.append("- Raised only in won deals: %s" % "; ".join(
                "%s \\(%d won deal%s\\)" % (gt.md(o["label"]), len(o["won"]), "" if len(o["won"]) == 1 else "s")
                for o in won_only))
        out.append("")

    # No coverage
    out += ["## Deals with no call coverage", ""]
    if not a["no_coverage"]:
        out += ["- None: every closed deal has at least one readable call.", ""]
    else:
        hist = b["sources"]["meetings"]["history_days"]
        cutoff = as_of.date() - timedelta(days=hist) if hist else None
        out += ["_No readable meeting with an attendee on the deal's domains (or linked to the deal). Reasons for "
                "these deals come from the CRM only, if at all._", ""]
        for d in a["no_coverage"]:
            note = "no meetings found"
            if d["meetings"]:
                note = "%d meeting%s found but notes and transcript unavailable" % (
                    len(d["meetings"]), "" if len(d["meetings"]) == 1 else "s")
            elif not d["domains"]:
                note = "no meetings found (no email domain on the deal, so none could be matched)"
            if cutoff and d["close_date"] < cutoff:
                note += "; closed before %s, older than the notes plan's %d-day history" % (short_date(cutoff), hist)
            crm = [e for e in d["evidence"] if e["source"] == "crm_field"]
            note += "; CRM reason cited" if crm else "; no reason cited"
            out.append("- %s, %s, closed %s — %s" % (deal_label(d, symbol, False), d["outcome"],
                                                     short_date(d["close_date"]), note))
        out.append("")

    # Deal list
    out += ["## Deals", ""]
    if not a["period_deals"]:
        out += ["- None.", ""]
    else:
        out += ["| Deal | Account | Outcome | Amount | Segment | Lost at | Competitors | Calls | Reasons cited |",
                "|---|---|---|---|---|---|---|---|---|"]
        for d in a["period_deals"]:
            kinds = "loss_reason" if d["outcome"] == "lost" else "win_reason"
            reasons = []
            for e in sorted(d["evidence"], key=lambda e: e["index"]):
                if e["kind"] == kinds and e["category"] not in reasons:
                    reasons.append(e["category"])
            section = "loss_reasons" if d["outcome"] == "lost" else "win_reasons"
            names = [gt.md(load_label(b, section, k)) for k in reasons]
            comps = [gt.md(a["competitor_names"][k]) for k in d["comp_keys"]]
            outcome = d["outcome"]
            if d["outcome"] == "lost":
                outcome += " \\(%s\\)" % LOSS_TYPE_SHORT[d["loss_type"]]
            deal_cell = md_link(d["deal_id"], d["crm_url"])
            out.append("| %s | %s | %s | %s | %s | %s | %s | %d | %s |" % (
                deal_cell, gt.md(d["account"]), outcome,
                money(symbol, d["amount"]) if d["amount"] is not None else "—",
                gt.md(d["segment"]) if d["segment"] else "—", gt.md(d["stage_lost_at"]) if d["stage_lost_at"] else "—",
                ", ".join(comps) or "—", len(d["readable_meetings"]), ", ".join(names) or "—"))
        out.append("")

    out += coverage_lines(b, a, config, as_of)
    out += ["", "_Internal only — contains customer quotes. Quotes in “ ” are verbatim from call transcripts or CRM "
                "fields; note excerpts are from AI meeting notes._"]
    return "\n".join(out) + "\n"


def load_label(b, section, key):
    return b["taxonomy"][section][key]["label"]


def coverage_lines(b, a, config, as_of):
    bk = a["buckets"]
    out = ["## Coverage", ""]
    out.append("- Period: %s (closed deals), as of %s." % (period_text(b["period_start"], b["period_end"]),
                                                           as_of.isoformat()))
    n_all = len(b["deal_order"])
    out.append("- Deals read: %d; in the period: %d; closed outside the period (ignored): %d." % (
        n_all, len(a["period_deals"]), len(a["outside_period"])))
    out.append("- Meetings listed: %d; linked to a deal in the period: %d; duplicate notes of one call merged: %d." % (
        len(b["meetings"]), bk.get("linked", 0), bk.get("duplicate_note", 0)))
    out.append("- Not used: internal-only %d (title not shown); only the note taker listed %d; external but on no "
               "deal's domains %d; linked only to deals outside the period %d." % (
                   bk.get("internal_only", 0), bk.get("attendees_unknown", 0), bk.get("unlinked", 0),
                   bk.get("other_period", 0)))
    t = a["transcripts"]
    out.append("- Transcripts for linked meetings: %d read, %d denied, %d unavailable, %d not requested. A denied "
               "transcript means notes only, never \"not discussed\"." % (
                   t.get("ok", 0), t.get("denied", 0), t.get("unavailable", 0), t.get("not_requested", 0)))
    unchecked = a["quotes_meeting"] - a["verified"]
    out.append("- Evidence items: %d in the bundle, %d used after dropping out-of-scope items and duplicates. Call "
               "quotes checked word for word against transcript or notes text in the bundle: %d of %d%s. CRM-field "
               "items checked against the field value: %d." % (
                   a["evidence_raw"], len(a["evidence"]), a["verified"], a["quotes_meeting"],
                   " (%d not checkable: that text was not in the bundle)" % unchecked if unchecked else "",
                   a["crm_evidence"]))
    dr = a["dropped"]
    if any(dr.values()):
        out.append("- Dropped: %d for deals outside the period; %d citing an internal-only meeting; %d "
                   "objection%s from calls after the deal closed." % (
                       dr["deal_outside_period"], dr["internal_meeting"], dr["objection_after_close"],
                       "" if dr["objection_after_close"] == 1 else "s"))
    if a["explicit_links"]:
        out.append("- ⚠️ Linked by the agent with no attendee on the deal's domains (check): %s." % "; ".join(
            "%s → %s" % (meeting_cite(m), ", ".join(gt.md(b["deals"][d]["account"]) for d in m["unmatched_explicit"]))
            for m in a["explicit_links"]))
    hist = b["sources"]["meetings"]["history_days"]
    if hist:
        out.append("- Notes plan history: %d days, so calls before %s may be missing." % (
            hist, short_date(as_of.date() - timedelta(days=hist))))
    if b["link_warnings"]:
        out.append("- Links dropped (not a valid http(s) URL): %d." % len(b["link_warnings"]))
    out.append("")
    out.append("Sources: %s" % " · ".join(source_status_text(k, b["sources"][k]) for k in SOURCE_KEYS))
    g = gaps(b)
    out.append("Gaps: %s." % ("; ".join(g) if g else "none"))
    return out


# --------------------------------------------------------------------------- slack and json


def slack_top(items, weighted, limit=3):
    """'A (2) · B (1, tied) · C (1, tied) · 3 more tied at 1': the first `limit` reasons in ranking order, each
    with its distinct-deal count. A reason that shares its ranking value (deal count, or summed amount with
    --weighting amount) with another is marked tied; tied reasons cut off by the limit are counted."""
    def rank(c):
        return c["amount"] if weighted else len(c["deals"])
    shown = items[:limit]
    parts = ["%s (%d%s)" % (gt.slack(c["label"]), len(c["deals"]),
                            ", tied" if sum(1 for x in items if rank(x) == rank(c)) > 1 else "") for c in shown]
    more = len([x for x in items[limit:] if rank(x) == rank(shown[-1])])
    if more:
        parts.append("%d more tied %s" % (more, "on amount" if weighted else "at %d" % rank(shown[-1])))
    return " · ".join(parts)


def render_slack(b, a, config, as_of, doc_url):
    ms = config["min_sample_for_percent"]
    head = "Win/Loss Analysis — %s" % (b["period_label"] or period_text(b["period_start"], b["period_end"]))
    lines = ["*%s* (%s)" % (gt.slack(head), period_text(b["period_start"], b["period_end"]))]
    total, won, lost = len(a["period_deals"]), len(a["won"]), len(a["lost"])
    if total == 0:
        lines.append("No closed deals in this period in the deal list that was read.")
    else:
        rate = " (win rate %d%%)" % percent(won, total) if total >= ms else ""
        lines.append("%d closed deals: %d won, %d lost%s." % (total, won, lost, rate))
        if lost:
            lines.append("Losses: " + " · ".join("%d %s" % (len(g["deals"]), LOSS_TYPE_SHORT[g["type"]])
                                                 for g in a["loss_groups"] if g["deals"]))
        weighted = a["weighting"] == "amount"
        if a["all_loss_reasons"]:
            lines.append("Top loss reasons: " + slack_top(a["all_loss_reasons"], weighted))
        if a["win_reasons"]:
            lines.append("Top win reasons: " + slack_top(a["win_reasons"], weighted))
        rated = [c for c in a["competitors"] if c["deals"]][:3]
        if rated:
            lines.append("Head-to-head (our record): " + " · ".join(
                "vs %s: we won %d of %d deal%s%s" % (
                    gt.slack(c["name"]), len(c["won"]), len(c["deals"]), "" if len(c["deals"]) == 1 else "s",
                    " (%d%%)" % percent(len(c["won"]), len(c["deals"])) if len(c["deals"]) >= ms else "")
                for c in rated))
        if a["no_coverage"]:
            lines.append("No call coverage: %d deal%s" % (len(a["no_coverage"]), "" if len(a["no_coverage"]) == 1 else "s"))
    if b["sources"]["deals"]["status"] != "ok" or b["sources"]["meetings"]["status"] != "ok":
        lines.append("⚠️ Coverage incomplete: " + "; ".join(gt.slack(g) for g in gaps_plain(b)))
    if doc_url:
        lines.append("Full report: %s" % slack_link(head, doc_url))
    lines.append("_Counts are distinct deals; percentages only for groups of %d or more._" % ms)
    return "\n".join(lines) + "\n"


def gaps_plain(b):
    out = []
    for key in SOURCE_KEYS:
        src = b["sources"][key]
        if src["status"] != "ok":
            out.append("%s %s" % (SOURCE_LABELS[key], src["status"].replace("_", " ")))
    return out


def json_ready(b, a, config, as_of, markdown):
    def deal_json(d):
        return {"deal_id": d["deal_id"], "account": d["account"], "outcome": d["outcome"],
                "close_date": d["close_date"].isoformat(), "amount": d["amount"], "segment": d["segment"],
                "stage_lost_at": d["stage_lost_at"], "lost_to": d["lost_to"], "loss_type": d["loss_type"],
                "competitors": [c for c in d["competitors"]], "calls_read": len(d["readable_meetings"]),
                "crm_url": d["crm_url"]}

    def ev_json(e):
        return {"id": e["id"], "deal_id": e["deal_id"], "kind": e["kind"], "category": e["category"],
                "competitor": e["competitor"], "source": e["source"],
                "meeting_id": e["meeting"]["meeting_id"] if e["meeting"] else None,
                "meeting_url": e["meeting"]["url"] if e["meeting"] else None,
                "crm_field": e["crm_field"], "quote": e["quote"], "speaker_role": e["speaker_role"],
                "speaker_side": e["speaker_side"], "verified_against_text": e["verified"]}

    def cat_json(c, pool_size):
        return {"key": c["key"], "label": c["label"], "deal_count": len(c["deals"]), "of": pool_size,
                "deal_ids": [d["deal_id"] for d in c["deals"]], "amount": c["amount"],
                "amount_unknown_deals": c["amount_unknown"],
                "percent": percent(len(c["deals"]), pool_size) if pool_size >= config["min_sample_for_percent"] else None,
                "evidence_ids": [e["id"] for e in c["evidence"]]}

    ms = config["min_sample_for_percent"]
    total = len(a["period_deals"])
    return {
        "period": {"start": b["period_start"].isoformat(), "end": b["period_end"].isoformat(),
                   "label": b["period_label"]},
        "as_of": as_of.isoformat(),
        "weighting": a["weighting"],
        "min_sample_for_percent": ms,
        "currency": b["currency"],
        "totals": {"deals": total, "won": len(a["won"]), "lost": len(a["lost"]),
                   "win_rate_percent": percent(len(a["won"]), total) if total >= ms else None,
                   "outside_period": len(a["outside_period"])},
        "loss_types": dict((g["type"], {"deal_ids": [d["deal_id"] for d in g["deals"]],
                                        "reasons": [cat_json(c, len(g["deals"])) for c in g["reasons"]]})
                           for g in a["loss_groups"]),
        "loss_reasons": [cat_json(c, len(a["lost"])) for c in a["all_loss_reasons"]],
        "win_reasons": [cat_json(c, len(a["won"])) for c in a["win_reasons"]],
        "won_without_reason": [d["deal_id"] for d in a["won_no_reason"]],
        "competitors": [{"name": c["name"], "deals": len(c["deals"]), "we_won": len(c["won"]),
                         "we_lost_to_them": len(c["lost_to_them"]), "we_lost_otherwise": len(c["lost_other"]),
                         "our_win_rate_percent": percent(len(c["won"]), len(c["deals"]))
                         if len(c["deals"]) >= ms else None,
                         "deal_ids": [d["deal_id"] for d in c["deals"]],
                         "mentioned_only_deal_ids": [d["deal_id"] for d in c["mentioned_only"]],
                         "evidence_ids": [e["id"] for e in c["quotes"]]} for c in a["competitors"]],
        "stages": [{"stage": st["label"] if st["recorded"] else None, "lost_deals": len(st["deals"]),
                    "percent_of_losses": percent(len(st["deals"]), len(a["lost"])) if len(a["lost"]) >= ms else None,
                    "amount": st["amount"], "deal_ids": [d["deal_id"] for d in st["deals"]]} for st in a["stages"]],
        "segments": [{"segment": sg["label"] if sg["recorded"] else None, "deals": len(sg["deals"]),
                      "won": len(sg["won"]), "lost": len(sg["lost"]),
                      "win_rate_percent": percent(len(sg["won"]), len(sg["deals"])) if len(sg["deals"]) >= ms else None,
                      "amount_won": sg["amount_won"], "amount_lost": sg["amount_lost"]} for sg in a["segments"]],
        "objections": [{"key": o["key"], "label": o["label"], "lost_deal_ids": [d["deal_id"] for d in o["lost"]],
                        "won_deal_ids": [d["deal_id"] for d in o["won"]],
                        "recurring": len(o["lost"]) >= config["recurring_min_deals"],
                        "evidence_ids": [e["id"] for e in o["evidence"]]} for o in a["objections"]],
        "no_call_coverage": [d["deal_id"] for d in a["no_coverage"]],
        "deals": [deal_json(d) for d in a["period_deals"]],
        "evidence": [ev_json(e) for e in sorted(a["evidence"], key=lambda e: e["index"])],
        "coverage": {"meetings_listed": len(b["meetings"]), "buckets": dict(sorted(a["buckets"].items())),
                     "transcripts": dict(sorted(a["transcripts"].items())), "evidence_in_bundle": a["evidence_raw"],
                     "evidence_used": len(a["evidence"]), "call_quotes": a["quotes_meeting"],
                     "call_quotes_verified": a["verified"], "crm_field_items": a["crm_evidence"],
                     "dropped": a["dropped"], "links_dropped": len(b["link_warnings"]),
                     "mixed_loss_type_deal_ids": [d["deal_id"] for d in a["conflicts"]]},
        "sources": b["sources"],
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


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("arguments: %s" % message)


def main(argv=None):
    utf8_streams()
    parser = Parser(description="Render a win/loss report from one JSON bundle (see DATA_CONTRACT.md).")
    parser.add_argument("--bundle", type=Path, help="Bundle JSON.")
    parser.add_argument("--deals-csv", type=Path, help="Read the deal list from this CSV instead of $.deals.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="winloss.json")
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY, help="reason_taxonomy.json")
    parser.add_argument("--as-of", help="Run time, ISO datetime with UTC offset. The only 'now'.")
    parser.add_argument("--internal-domain", action="append", default=[],
                        help="Your company's email domain; repeat for each. Subdomains count as internal.")
    parser.add_argument("--internal-email", action="append", default=[],
                        help="A colleague's address outside the internal domains (e.g. a personal address).")
    parser.add_argument("--weighting", choices=WEIGHTINGS, default="none")
    parser.add_argument("--format", choices=FORMATS, default="markdown")
    parser.add_argument("--doc-url", help="Link to the full report, used by --format slack.")
    parser.add_argument("--list-categories", action="store_true", help="Print the taxonomy's category keys and exit.")
    try:
        args = parser.parse_args(argv)
        taxonomy = load_taxonomy(args.taxonomy)
        if args.list_categories:
            lines = []
            for section in ["loss_reasons", "win_reasons", "objections"]:
                lines.append("%s:" % section)
                for key in sorted(taxonomy[section]):
                    c = taxonomy[section][key]
                    lines.append("  %s | %s%s" % (key, c["label"], " | no decision" if c["no_decision"] else ""))
            output = "\n".join(lines) + "\n"
        else:
            if args.bundle is None:
                raise InputError("--bundle is required")
            if not args.as_of:
                raise InputError("--as-of is required, e.g. --as-of 2026-10-05T09:00:00-07:00")
            as_of = parse_moment(args.as_of.strip(), lambda p: InputError("--as-of: " + p))
            internal = []
            for raw in args.internal_domain:
                d = clean_domain(raw)
                if not DOMAIN_RE.fullmatch(d):
                    raise InputError("--internal-domain: %s is not a domain such as yourcompany.example"
                                     % short_repr(raw))
                if d not in internal:
                    internal.append(d)
            if not internal:
                raise InputError("--internal-domain is required (your company's email domain), so internal "
                                 "meetings can be told apart from customer calls")
            internal_emails = []
            for raw in args.internal_email:
                e = clean_text(raw).lower()
                if "@" not in e or " " in e or not DOMAIN_RE.fullmatch(domain_of(e)):
                    raise InputError("--internal-email: %s is not an email address" % short_repr(raw))
                internal_emails.append(e)
            doc_url = None
            if args.doc_url is not None:
                doc_url = safe_url(args.doc_url)
                if doc_url is None:
                    raise InputError("--doc-url must be an http(s) link, got %s" % short_repr(args.doc_url))
            config = load_config(args.config)
            csv_deals = read_deals_csv(args.deals_csv) if args.deals_csv is not None else None
            b = load_bundle(args.bundle, config, taxonomy, csv_deals)
            b["taxonomy"] = taxonomy
            if b["period_end"] > as_of.date():
                raise InputError("%s: $.period.end: %s is after --as-of; a report cannot cover the future"
                                 % (b["label"], b["period_end"].isoformat()))
            for deal_id in b["deal_order"]:
                d = b["deals"][deal_id]
                if d["close_date"] > as_of.date():
                    raise InputError("%s: %s.close_date: %s is after --as-of; only closed deals belong here"
                                     % (b["label"], d["path"], d["close_date"].isoformat()))
            classify(b, config, internal, internal_emails, as_of)
            a = analyse(b, config, taxonomy, args.weighting)
            markdown = render_markdown(b, a, config, as_of)
            if args.format == "markdown":
                output = markdown
            elif args.format == "slack":
                output = render_slack(b, a, config, as_of, doc_url)
            else:
                output = json.dumps(json_ready(b, a, config, as_of, markdown), ensure_ascii=False, indent=2,
                                    sort_keys=False) + "\n"
    except InputError as exc:
        emit(sys.stderr, "error: %s\n" % exc)
        return EXIT_INPUT
    if not emit(sys.stdout, output):
        emit(sys.stderr, "error: could not write the report to stdout (closed or broken pipe)\n")
        return EXIT_OUTPUT
    return 0


if __name__ == "__main__":
    sys.exit(main())
