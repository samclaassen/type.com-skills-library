#!/usr/bin/env python3
"""Render a private 1:1 prep doc from one normalised JSON bundle (see DATA_CONTRACT.md).

Stdlib only, offline, deterministic, Python 3.8+. It never calls an API, never writes a file and keeps no state.
The only "now" it uses is --as-of.

The agent reads the previous 1:1s in Granola (plus, optionally, Linear/Jira issues and Slack threads) and extracts
items; this script does the mechanical, checkable parts:

  validate     every field: types, enums, ids, strict ISO dates (identical on Python 3.8 and 3.14), http(s) links
               only, no NaN/Infinity, no unpaired surrogates, size caps. Keys that would rate a person or record
               mood or health ("rating", "score", "sentiment", "mood", ...) are refused. Bad input: exit 2, nothing
               printed on stdout.
  meetings     only meetings that started before --as-of, list both you and the other person as attendees, have
               no one else on the invite (configurable), match the series title (unless match is attendee_only)
               and fall inside the lookback window are read; the newest N are kept.
  items        each item must cite one of those meetings and carry a quote; when the bundle includes the meeting's
               text, the quote must appear in it. Items with the same kind, owner and key (or the same summary)
               are merged across meetings, so a commitment raised in three 1:1s is one line "came up in 3".
  status       "likely_done"/"done" needs dated evidence on or after the last 1:1 where the item came up open;
               otherwise it is shown as open with a note.
  dates        relative due phrases ("by Friday", "end of month", "before our next 1:1") are resolved against the
               date of the 1:1 where they were said; overdue counts use --as-of.
  withholding  every text field of an item (summary, quote, evidence) is normalised (NFKC, digits made ASCII,
               invisible characters removed, case-folded, look-alike letters mapped, the two people's names masked)
               and checked against pattern lists in prep.json. A personal or sensitive topic (health, family,
               protected characteristics, wellbeing, confidential HR matters, ...) withholds the whole item: the
               doc says "1 item withheld as personal" and never prints its text, category or meeting. Wording
               that rates or judges a person, or describes their mood, leaves the item out too. Pattern-based: it
               catches common wording, not every phrasing, and errs toward withholding.
  personal     e-mail addresses, links, phone numbers (phone-like grouping) and ID-like numbers are removed from
  data         printed text (pattern-based; unusual formats can slip through).
  render       markdown (or --json), escaped so no input text can form a link, image, raw HTML, mention or
               formatting. The prep is private to the person running it; the script never posts anything.

Exit codes: 0 prep printed; 2 bad input (message names the file and JSON path; nothing on stdout);
6 the output could not be written (closed stdout or broken pipe).
"""

import argparse
import html
import json
import hashlib
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlsplit

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# granola_text.py sits next to this script (a byte-for-byte copy of the shared helper). Import it from here whatever
# the current directory is, and don't leave a __pycache__ folder in the skill folder.
sys.dont_write_bytecode = True
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import granola_text as gt  # noqa: E402
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "prep.json")
SCHEMA_VERSION = 1
EXIT_INPUT = 2
EXIT_OUTPUT = 6

ROLES = ["manager", "report"]
SIDES = ["user", "counterpart"]
KINDS = ["commitment", "feedback", "question", "goal", "topic"]
# The allowlist. The agent gives every item (and every work item) one of these work topics. Anything else, a missing
# topic, "personal" or "other", is never rendered, only counted as withheld.
TOPICS = ["project", "goal", "process", "customer", "team-logistics", "work-feedback", "career-growth",
          "work-question"]
BLOCKED_TOPICS = ["personal", "other"]
STATUSES = ["open", "likely_done", "done"]
QUOTE_FROM = ["transcript", "notes", "summary"]
SPEAKERS = ["user", "counterpart", "unknown"]
EVIDENCE_SOURCES = ["granola", "linear", "jira", "slack", "user"]
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_configured"]
MEETING_PROVIDERS = ["granola", "gong", "fathom", "fireflies", "otter", "zoom", "pasted"]
ISSUE_PROVIDERS = ["linear", "jira"]
SLACK_PROVIDERS = ["slack"]
NOTES_STATES = ["ok", "unavailable", "denied"]
TRANSCRIPT_STATES = ["ok", "denied", "unavailable", "not_requested"]
WORK_STATES = ["open", "in_progress", "done", "canceled"]
MATCH_MODES = ["title_and_attendee", "attendee_only"]
NOTE_OWNERS = ["user", "counterpart", "unknown"]
LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies", "otter": "Otter",
          "zoom": "Zoom", "pasted": "pasted notes", "linear": "Linear", "jira": "Jira", "slack": "Slack",
          "user": "you said so"}
STATUS_WORDS = {"unavailable": "unavailable", "denied": "access denied", "not_configured": "not configured"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
               "november", "december"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WARN = "⚠️"

CONFIG_INTS = {"lookback_meetings": (1, 12), "lookback_days": (7, 730), "recurring_min_meetings": (2, 12),
               "max_per_section": (1, 50), "agenda_max": (1, 20), "max_other_attendees": (0, 10),
               "max_summary_chars": (20, 1000), "max_quote_chars": (20, 2000), "max_items": (1, 1000),
               "max_meetings": (1, 500), "max_work_items": (0, 500)}
CONFIG_KEYS = list(CONFIG_INTS) + ["schema_version", "ignored_attendee_domains", "forbidden_keys", "category_labels",
                                   "sensitive_terms", "judgement_terms", "work_modifier_nouns", "fuzzy_terms", "fuzzy_ignore",
                                   "person_terms", "separation_concepts", "non_person_words"]
JUDGEMENT_SOURCES = ["any", "authored"]

# Strict dates: ASCII digits only; hours 00-23, minutes/seconds 00-59, offsets up to +/-14:00. Checked before
# fromisoformat, so every Python from 3.8 accepts and rejects the same strings.
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DATETIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                         r"(?::[0-5][0-9](?:\.[0-9]{3}|\.[0-9]{6})?)?"
                         r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
MIN_YEAR, MAX_YEAR = 1900, 2999
MAX_DUE_DAYS = 3650
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}")
EMAIL_ADDR = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
URL_SAFE = "/:?#@!$&'*+,;=%~"
SURROGATE = re.compile("[\ud800-\udfff]")
WHITESPACE = re.compile(r"\s+")


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- reading


def type_name(value):
    return {dict: "an object", list: "a list", bool: "true/false", int: "a number", float: "a number",
            str: "a string"}.get(type(value), type(value).__name__)


ECHO = {"scanner": None}  # set once the config is loaded


def shown(value):
    """A bad value for an error message. A string is echoed only when it is one short token (no spaces) of plain
    characters that the scanner finds nothing in, so an error never repeats free text; otherwise only its length is
    given."""
    if isinstance(value, str):
        scanner = ECHO["scanner"]
        if re.fullmatch(r"[A-Za-z0-9._:/+-]{1,40}", value) and scanner is not None and not scanner.check(value, True):
            return repr(value)
        return "a {}-character value (not shown)".format(len(value))
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    return type_name(value)


def display_clean(text):
    """Invisible format characters (bidi overrides, zero-width) and control characters removed; line breaks and
    tabs become spaces. The zero-width joiner is kept (emoji sequences)."""
    return "".join(" " if ch in "\t\n\r\x0b\x0c\x85\u2028\u2029" else ch for ch in text
                   if ch == "\u200d" or ch in "\t\n\r\x0b\x0c\x85\u2028\u2029"
                   or unicodedata.category(ch) not in ("Cf", "Cc"))


def parse_day(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return None
    try:
        day = date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return None
    return day if MIN_YEAR <= day.year <= MAX_YEAR else None


def parse_moment(value):
    """Aware datetime for a strict ISO-8601 datetime with Z or +/-HH:MM, else None."""
    if not isinstance(value, str) or not DATETIME_RE.fullmatch(value):
        return None
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if moment.tzinfo is None or not MIN_YEAR <= moment.year <= MAX_YEAR:
        return None
    return moment


def safe_url(value):
    """An absolute http(s) URL with a real host name, no credentials and a valid port, percent-encoded; else None."""
    if not isinstance(value, str) or not value or len(value) > 2000:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        clean = quote(value, safe=URL_SAFE)
    except (ValueError, UnicodeError):
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or not DOMAIN_RE.fullmatch(host) \
            or parts.username is not None or parts.password is not None or port == 0:
        return None
    return clean


class Reader:
    """Typed accessors that report '<label>: <json.path>: problem'."""

    def __init__(self, label):
        self.label = label
        self.dropped_links = 0

    def fail(self, path, problem):
        return InputError("{}: {}: {}".format(self.label, path, problem))

    def obj(self, value, path, required=True):
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object, got {}"
                            .format(type_name(value)))
        return value

    def lst(self, parent, key, path, required=False, cap=None):
        value = parent.get(key)
        where = "{}.{}".format(path, key)
        if value is None:
            if required:
                raise self.fail(where, "is required (a list)")
            return []
        if not isinstance(value, list):
            raise self.fail(where, "must be a list, got {}".format(type_name(value)))
        if cap is not None and len(value) > cap:
            raise self.fail(where, "has {} entries; the limit is {}".format(len(value), cap))
        return value

    def text(self, parent, key, path, required=True, cap=None):
        value = parent.get(key)
        where = "{}.{}".format(path, key)
        if value is not None and not isinstance(value, str):
            raise self.fail(where, "must be a string, got {}".format(type_name(value)))
        if isinstance(value, str):
            value = WHITESPACE.sub(" ", display_clean(value)).strip()
        if not value:
            if required:
                raise self.fail(where, "is required but missing or empty")
            return None
        if cap is not None and len(value) > cap:
            raise self.fail(where, "is {} characters; keep it under {}".format(len(value), cap))
        return value

    def enum(self, parent, key, path, allowed, default=None):
        value = parent.get(key, default)
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("{}.{}".format(path, key), "must be one of {}, got {}".format(
                ", ".join(allowed), shown(value)))
        return value

    def ident(self, parent, key, path, required=True):
        value = parent.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise self.fail("{}.{}".format(path, key), "must be 1-128 letters, digits, ':', '.', '_' or '-' "
                            "starting with a letter or digit, got {}".format(shown(value)))
        return value

    def email(self, parent, key, path):
        value = parent.get(key) if isinstance(parent, dict) else parent[key]
        where = "{}.{}".format(path, key) if isinstance(parent, dict) else "{}[{}]".format(path, key)
        if not isinstance(value, str) or not EMAIL_ADDR.fullmatch(value.strip()):
            raise self.fail(where, "must be an e-mail address (value not shown)")
        return value.strip().lower()

    def boolean(self, parent, key, path, default):
        value = parent.get(key, default)
        if not isinstance(value, bool):
            raise self.fail("{}.{}".format(path, key), "must be true or false, got {}".format(shown(value)))
        return value

    def integer(self, parent, key, path, low, high, required=True):
        value = parent.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise self.fail("{}.{}".format(path, key), "must be a whole number from {} to {}, got {}".format(
                low, high, shown(value)))
        return value

    def day(self, parent, key, path, required=True):
        """YYYY-MM-DD, or a full ISO datetime with an offset (its own local date is used)."""
        value = parent.get(key)
        if value is None and not required:
            return None
        parsed = parse_day(value)
        if parsed is None:
            moment = parse_moment(value)
            parsed = moment.date() if moment is not None else None
        if parsed is None:
            raise self.fail("{}.{}".format(path, key), "{} is not a YYYY-MM-DD date or an ISO datetime with an "
                            "offset (years {}-{})".format(shown(value), MIN_YEAR, MAX_YEAR))
        return parsed

    def moment(self, parent, key, path, required=True):
        value = parent.get(key)
        if value is None and not required:
            return None
        parsed = parse_moment(value)
        if parsed is None:
            raise self.fail("{}.{}".format(path, key), "{} is not an ISO-8601 datetime with an offset, like "
                            "2026-10-13T10:00:00-07:00 (years {}-{})".format(shown(value), MIN_YEAR, MAX_YEAR))
        return parsed

    def url(self, parent, key, path):
        """Optional link: kept only when it is an http(s) URL with a host; anything else is dropped and counted."""
        value = parent.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str):
            raise self.fail("{}.{}".format(path, key), "must be a string URL, got {}".format(type_name(value)))
        clean = safe_url(value.strip())
        if clean is None:
            self.dropped_links += 1
        return clean


def reject_constant(name):
    raise ValueError("{} is not a valid JSON number".format(name))


def surrogate_path(data):
    """JSON path of the first string holding an unpaired surrogate, or None (iterative: deep files can't overflow)."""
    stack = [(data, "$")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, str):
            if SURROGATE.search(value):
                return path
        elif isinstance(value, dict):
            for key in value:
                if SURROGATE.search(key):
                    return path
                stack.append((value[key], "{}.{}".format(path, key if re.fullmatch(r"[A-Za-z0-9_]{1,40}", key)
                                                          else "<key>")))
        elif isinstance(value, list):
            stack.extend((item, "{}[{}]".format(path, i)) for i, item in enumerate(value))
    return None


def load_json(path, label):
    try:
        if not os.path.isfile(path):
            raise InputError("{}: file not found: {}".format(label, path))
        with open(path, "rb") as fh:
            raw = fh.read()
        data = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
    except InputError:
        raise
    except json.JSONDecodeError as exc:
        raise InputError("{}: {} is not valid JSON (line {}, column {}: {})".format(
            label, path, exc.lineno, exc.colno, exc.msg)) from None
    except UnicodeDecodeError:
        raise InputError("{}: {} is not UTF-8 text".format(label, path)) from None
    except ValueError as exc:
        raise InputError("{}: {} is not valid JSON ({})".format(label, path, exc)) from None
    except RecursionError:
        raise InputError("{}: {} is nested too deeply".format(label, path)) from None
    except OSError as exc:
        raise InputError("{}: {} cannot be read ({})".format(label, path, exc.strerror or exc)) from None
    bad = surrogate_path(data)
    if bad:
        raise InputError("{}: {}: {}: contains an unpaired surrogate escape (\\ud800-\\udfff), which is not text"
                         .format(label, path, bad))
    return data


def key_paths(data, wanted):
    """JSON path of the first object key (case-insensitive) in `wanted`, or None."""
    stack = [(data, "$")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, dict):
            for key in sorted(value, key=str):
                if key.lower() in wanted:
                    return "{}.{}".format(path, key)
                stack.append((value[key], "{}.{}".format(path, key if re.fullmatch(r"[A-Za-z0-9_]{1,40}", key)
                                                          else "<key>")))
        elif isinstance(value, list):
            stack.extend((item, "{}[{}]".format(path, i)) for i, item in enumerate(value))
    return None


# --------------------------------------------------------------------------- scanning

# Applied before matching, so look-alike spellings hit the same terms (text is case-folded first).
CONFUSABLES = str.maketrans({
    "а": "a", "в": "b", "г": "r", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "п": "n", "р": "p", "с": "c",
    "т": "t", "у": "y", "х": "x", "ь": "b", "ѕ": "s", "і": "i", "ї": "i", "ј": "j", "һ": "h", "ԁ": "d", "ԛ": "q",
    "ԝ": "w", "ӏ": "l", "ү": "y", "ҳ": "x",
    "α": "a", "β": "b", "γ": "y", "δ": "d", "ε": "e", "ζ": "z", "η": "n", "ι": "i", "κ": "k", "ν": "v", "ο": "o",
    "ρ": "p", "σ": "o", "ς": "c", "τ": "t", "υ": "u", "χ": "x", "ω": "w", "ϲ": "c", "ϳ": "j", "μ": "u",
    "օ": "o", "ս": "u", "ո": "n", "հ": "h", "ց": "g",
    "ı": "i", "ȷ": "j", "ɑ": "a", "ɡ": "g", "ɩ": "i", "ɪ": "i", "ʀ": "r", "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d",
    "ᴇ": "e", "ɢ": "g", "ʜ": "h", "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ꜱ": "s",
    "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
    "’": "'", "‘": "'", "ʼ": "'", "′": "'", "`": "'", "−": "-",
})
INVISIBLE = {"ᅟ", "ᅠ", "ㅤ", "ﾠ", "⠀"}
# One letter at a time with the same separator ("s i c k", "s.i.c.k") is joined before matching.
SPACED_LETTERS = re.compile(r"(?<![^\W_])(?:[^\W_]([ .*·•])(?:[^\W_]\1)+[^\W_](?![^\W_])"
                            r"|[^\W_]([ .*·•])[^\W_](?![^\W_]|[ .*·•][^\W_]))")
MARKUP = re.compile(r"[*_~`]")
SPACED_DASH = re.compile(r"\s+[\u2010-\u2015\u2212-]+\s+")
ARROW = re.compile(r"\s*(?:->|=>|[\u2192\u21d2\u27f6\u2794\u27a1])\s*")
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
LEET_WORD = re.compile(r"[a-z0-9@$]*[a-z][a-z0-9@$]*")
NON_ASCII = re.compile(r"[^\x00-\x7f]")

# Placeholders in prep.json patterns. Names of the two people are masked to "the person" before matching.
# {who}: a person (either side, or someone spoken about); {other}: a person other than the speaker (for judgement
# wording in quotes, where "I" talking about their own work is fine); {be}: a linking verb; {end}: end of a clause;
# {frag}: the start of a subject-less note fragment ("Out sick.", "Pregnant, due in May.").
WHO = (r"(?:i|me|myself|you|yourself|she|he|they|her|him|them|the person|this person|my report|the report|"
       r"my manager|the manager|my boss|my partner|my wife|my husband|my spouse)")
OTHER = (r"(?:you|yourself|she|he|they|her|him|them|the person|this person|my report|the report|my manager|"
         r"the manager|my boss|everyone|the whole team|my colleague|my coworker|my teammate)")
BE = (r"(?:'s|'re|'m|m\b|'ve been|'d been| is| are| am| was| were| has been| have been| had been| will be| would be|"
      r" is now| was once| seems| seemed| looks| looked| appears| appeared| became| becomes| has become| is still|"
      r" was still| be| being| been| feel| feels| felt| feeling| keep| keeps| kept| get| gets| got| getting)")
END = (r"(?=\s?(?:[.,;:!?)\]]|$)|\s(?:and|but|so|if|because|as|since|though|although|overall|too|now|anyway|when|"
       r"while|before|after|given|despite|lately|recently|these days|right now|at the moment|this week|this month|"
       r"again|still|today)\b)")
FRAG = (r"(?:^|(?<=[.;:!?(,]) ?)(?:(?:was|is|has been|been|seems|seemed|looks|looked|feels|felt|very|quite|a bit|"
        r"a little|kind of|sort of|really|super|so|too|clearly|obviously|visibly|slightly|somewhat|rather|also|"
        r"possibly|probably|maybe|definitely|still|now|currently|recently|lately) )*")
FUNCTION_WORDS = (r"(?:and|or|but|so|nor|the|a|an|my|our|your|his|her|their|its|to|for|with|at|in|on|of|from|by|as|"
                  r"because|since|after|before|while|when|if|then|not|no|is|was|are|were|am|be|been|being|has|have|"
                  r"had|i|we|you|he|she|they|it|that|this|these|those|who|which|me|him|them|us|about|care|support)")
# "my IVF clinic invoices", "her visa renewal FAQ": a personal possessive right before the match keeps it personal.
POSSESSIVE = re.compile(r"(?:my|his|her|their|your)\b")
POSSESSIVE_BEFORE = re.compile(r"\b(?:my|his|her|their|your) (?:own )?$")
# A name works like a possessive: "Riley PIP documentation" is read as "their PIP documentation" too (the two
# people's names are masked to "the person" first; other people's names are capitalised words, see NAMED).
NAME_AS_POSSESSIVE = re.compile(r"\bthe person(?:'s|s)? (?!(?:is|was|are|were|has|had|have|will|would|can|could|should|"
                                r"said|says|told|asked|and|or|to|be|been|being|did|does|do|got|gets|seems|seemed|"
                                r"looks|looked|keeps|kept|feels|felt|might|may|must|also|just|never|always|still|wants|"
                                r"wanted|needs|needed|asks|thinks|thought|knows|knew|went|goes|left|leaves|plans|planned|"
                                r"expects|expected|hopes|hoped|likes|liked|owns|owned|leads|led|runs|ran|shipped|ships)\b)")
# Other people's names: a capitalised word (not a common sentence opener) right before a linking verb, or right
# after a verb aimed at a person ("terminate Sam", "an investigation into Sam"), is read as "the person" in an
# extra scan form, so "Sam is lazy and incompetent" counts like "the person is lazy and incompetent".
NOT_NAMES = ("The|This|That|These|Those|It|Its|There|Here|What|When|Where|Why|How|Which|Who|We|Our|You|Your|They|"
             "Their|He|She|His|Her|I|My|And|But|So|If|Can|Could|Would|Should|Will|Let|Please|Also|Honestly|Okay|Ok|"
             "Yes|No|Not|Just|Maybe|Then|Now|Today|Tomorrow|Yesterday|Next|Last|Every|Everything|Nothing|All|Any|"
             "Some|Most|One|Each|Everyone|Nobody|Somebody|Someone|Anyone|Everybody|Overall|Q[1-4]|"
             "Mon|Monday|Tue|Tuesday|Wed|Wednesday|Thu|Thursday|Fri|Friday|Sat|Saturday|Sun|Sunday|Jan|January|Feb|"
             "February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|"
             "November|Dec|December")
NAMED = re.compile(r"(?<![\w'’])(?<!\bon )(?<!\bin )(?<!\bat )(?<!\bwith )(?<!\bfrom )(?<!\bfor )(?<!\bto )(?<!\bby )"
                   r"(?<!\bof )(?<!\bthe )(?<!\bour )(?<!\bvs )(?<!\bversus )(?<!\bthan )(?<!\bvia )(?<!\bon the )"
                   r"(?!(?:" + NOT_NAMES + r")\b)[A-Z][a-z]{1,20}(?: [A-Z][a-z]{1,20})?"
                   r"(?=(?:['’]s)? (?:is|was|has been|had been|seems|seemed|looks|looked|keeps|kept|brings|brought|comes across|came "
                   r"across|always|never|really|honestly|can't|can’t|cannot|can not|doesn't|doesn’t|does not|didn't|"
                   r"didn’t|did not|won't|won’t|isn't|isn’t|is not)\b"
                   # "Morgan's a nightmare", "Jordan's lazy": 's read as "is" before an article or a judgement-like word
                   r"| = |['’]s (?:a|an|the|so|such|very|really|totally|completely|just|not|being|been|lazy|useless|"
                   r"hopeless|"
                   r"clueless|incompetent|unreliable|toxic|rude|awful|terrible|brilliant|amazing|great|slow|weak)\b)")
NAMED_AFTER = re.compile(r"(?i:\b(terminate|terminating|terminated|fire|firing|fired|discipline|disciplining|against|"
                         r"investigate|investigating|investigated|into|replace|replacing|manage|managing|let|letting|get rid of|getting rid of) )(?!(?:" + NOT_NAMES + r")\b)[A-Z][a-z]{1,20}\b"
                         r"(?! [A-Z])")
# The work-noun exception ("fertility clinic client") never applies when the match is about leave, sickness,
# medical matters or an HR process: "PIP documentation" or "medical leave handbook section" is still withheld.
NO_WORK_EXCEPTION = re.compile(r"\b(?:leave|leaves|sick|sickness|medical|illness|diagnos\w*|disabilit\w*|surgery|"
                               r"injur\w*|pip|performance improvement plans?|investigat\w*|disciplinary|discipline|"
                               r"terminat\w*|fired|grievances?|accommodations?|adjustments?|warnings?|complaints?|"
                               r"misconduct|harass\w*|suicid\w*|self harm\w*|rehab|hiv|stis?|stds?)\b")
FUZZY_WORD = re.compile(r"(?<![a-z])[a-z]{4,30}(?![a-z])")

# Name-agnostic person detection, for `person_terms` and `separation_concepts` only. In an extra scan form every
# capitalised name-like word that is read as a person (anyone: the two people, whose names are masked first, or a
# third party) becomes "the person". A capitalised word is NOT read as a person when it is a common sentence word or
# month/day (NOT_NAMES), listed in prep.json `non_person_words`, all capitals (API, EC2), right after an article or
# possessive ("the Redis cache"), right before a system noun ("Salesforce integration"), or at the start of a
# sentence unless "'s" or a verb follows ("Exit Sam by Q1": "Exit" is not a person; "Sam goes…": "Sam" is).
# {p} then matches a person reference and {pp} a person's possessive.
PERSON = (r"(?:the person|this person|that person|him|her|them|he|she|they|you|my report|the report|your report|"
          r"our report|my direct report|the employee|this employee|the new hire|my teammate|my colleague)")
PERSON_POSS = r"(?:the person(?:'s|s)?|his|her|their|your|my report's|the report's|the employee's)"
NAME_TOKEN = re.compile(r"(?<![\w'’-])(?:[A-Z]['’])?(?:Mc|Mac)?[A-Z][a-z]+(?:-[A-Z][a-z]+)?(?![\w-])")
SENTENCE_START = re.compile(r"(?:^|[.!?;:\n•(\[\"“]|\s[-*]|\s[-–—]\s)\s*$")
DETERMINER_BEFORE = re.compile(r"(?i)\b(?:the|a|an|our|their|this|that|these|those|its|your|my|his|her|every|each|any|"
                               r"some|no|which|whose|new|old|legacy|current)\s+$")
SYSTEM_NOUN_AFTER = re.compile(
    r"(?i)\s+(?:cache|caches|cluster|clusters|instances?|integrations?|jobs?|pipelines?|services?|servers?|apis?|"
    r"endpoints?|databases?|db|dashboards?|accounts?|apps?|plugins?|bots?|scripts?|tables?|queues?|buckets?|repos?|"
    r"projects?|office|offices|contracts?|licen[cs]es?|vendors?|features?|modules?|reports?|workspaces?|channels?|"
    r"boards?|models?|migrations?|deployments?|rollouts?|campaigns?|tickets?|teams?|orgs?|environments?|stacks?|"
    r"tools?|platforms?|subscriptions?|tiers?|connectors?|syncs?|workflows?|deals?|renewals?|launch|launches|"
    r"releases?|beta|tenants?|configs?|sdks?|clis?|backend|frontend|sites?|pages?|forms?|docs?|decks?|sheets?|"
    r"spreadsheets?|calendars?|invites?|account|instance|nodes?|keys?|tokens?|webhooks?|feeds?|exports?|imports?|"
    r"plan|plans|pricing|seats?|users?|customers?|clients?|partners?|region|regions|zone|zones|version|versions|"
    r"v[0-9]+)\b")
VERB_AFTER = re.compile(
    r"(?:['’]s\b|['’](?:ll|d|re|ve)\b|,|\s+(?:and|or|is|was|were|are|will|won['’]?t|would|wouldn['’]?t|has|hasn['’]?t|"
    r"had|hadn['’]?t|have|goes|went|gets|got|needs|asked|asks|wants|said|says|told|knows|does|doesn['’]?t|did|"
    r"didn['’]?t|isn['’]?t|wasn['’]?t|can|can['’]?t|cannot|could|couldn['’]?t|should|shouldn['’]?t|might|may|must|"
    r"keeps|kept|leaves|left|starts|started|joined|joins|seems|seemed|looks|looked|feels|felt|also|just|still|never|"
    r"always|really|already|recently|probably|definitely|requested|requests|requires|required|uses|relies|deserves|"
    r"expects|failed|fails|missed|misses|plans|planned|took|takes|reports|reported|quit|quits|resigned|owes|"
    r"finishes|finished|exits|departs|is|ends|ended|stays|stayed|works|worked|lives|moved|moves|returns|returned|"
    r"has|isnt|wont|doesnt|didnt|cant|probably|likely|won|hates|loves|wanted|needed|brought|raised)\b)")

VERB_LIKE_AFTER = re.compile(r" (?:[a-z]+ly )?(?!(?:is|was|has|does|this|thus|plus|us|bus|gas|yes|less|ness|news|access|"
                             r"process|status|focus|bonus|campus|census|virus|series|basis|analysis|alias|canvas|"
                             r"chaos|address|business|progress|success|congress|class|pass|glass|cross|boss|loss|"
                             r"miss|kiss|press|stress|dress|express|mess|guess|across|unless|regardless|always|"
                             r"perhaps|towards|afterwards|sometimes|nevertheless|whereas|its|his|hers|ours|theirs|"
                             r"yours|ones|others|ads|docs|ops|specs|stats|metrics|needed|red|bed|shed|seed|need|"
                             r"feed|speed|bled|bred|fled|led|wed|fed|sled|hundred|kindred|sacred|naked|wicked)\b)"
                             r"[a-z]{2,}(?:[^aeious]s|[aeo]s|ies|ed)\b")
# Sentence-initial capitalised words that are verbs or common words, never read as a name by VERB_LIKE_AFTER.
START_VERB = re.compile(r"(?:exit|offboard|fire|terminate|dismiss|sack|axe|cut|push|move|transition|replace|backfill|"
                        r"plan|start|stop|begin|have|get|let|make|keep|put|take|give|tell|ask|line|post|hire|find|"
                        r"remove|drop|frame|prep|draft|write|schedule|decide|book|send|share|set|check|review|follow|"
                        r"talk|loop|ping|sync|meet|call|chase|confirm|update|close|open|add|run|ship|fix|build|"
                        r"track|document|log|note|flag|raise|escalate|discuss|agree|align|clarify|cover|own|lead|"
                        r"pair|hand|swap|rotate|reassign|assign|announce|quietly|maybe|probably|definitely|"
                        r"apparently|honestly|basically|already|still|only|even|both|either|neither|after|before|"
                        r"once|until|since|because|although|though|while|whether|per|re|fyi|note|todo|action)")


def tag_people(text, non_person):
    """The text with every capitalised word read as a person replaced by "the person" (a run of such words, like a
    first and last name, becomes one "the person")."""
    out, last, prev_person_end = [], 0, None
    for m in NAME_TOKEN.finditer(text):
        word = m.group(0)
        before, after = text[:m.start()], text[m.end():]
        person = True
        if re.fullmatch(NOT_NAMES, word) or word.casefold() in non_person:
            person = False
        elif DETERMINER_BEFORE.search(before):
            person = False
        elif SYSTEM_NOUN_AFTER.match(after):
            person = False
        elif SENTENCE_START.search(before) and not VERB_AFTER.match(after):
            nxt = NAME_TOKEN.match(after.lstrip()) if after[:1] == " " else None
            # "Jordan Lee will…": a sentence-initial first name followed by a surname and a verb
            rest = after.lstrip()[nxt.end():] if nxt else ""
            person = bool(nxt) and bool(VERB_AFTER.match(rest)) and not rest.startswith(("'s", "’s"))
            # "Sam stops being on payroll", "Sam quietly interviews": a lower-case word that reads as a present or
            # past verb (-s, -ed, optionally after an -ly adverb) follows, and the capitalised word is not itself a
            # verb we know ("Exit", "Offboard") or a common sentence word. Imperatives ("Plan Sam's exit", "Have
            # the talk") are followed by a name, a determiner or a particle, never by such a verb.
            if not person and not nxt and not START_VERB.fullmatch(word.casefold()):
                person = bool(VERB_LIKE_AFTER.match(after))
        if not person:
            continue
        if prev_person_end is not None and text[prev_person_end:m.start()] == " ":
            out.append(text[last:prev_person_end])  # the run continues: drop the space and this word
        else:
            out.append(text[last:m.start()] + "the person")
        last = prev_person_end = m.end()
    out.append(text[last:])
    return "".join(out)


# --------------------------------------------------------------------------- round 10: any person, any topic
# The person forms (people_forms) read every way of referring to a person as "the person", whatever its case:
#   - the two people's names in any case ("riley", "RILEY");
#   - capitalised name-like words (tag_people above);
#   - initials ("J.", "T.R.", "J's");
#   - a lower-case word with a possessive "'s" that is not a common word ("priya's replacement");
#   - a lower-case word at the start of a sentence followed by a verb ("priya is leaving");
#   - a role noun after a determiner or possessive ("our designer", "the new contractor", "my QA lead", "an intern").
# Then, on every work topic, an item whose text has a separation concept (prep.json `separation_concepts`) and any
# person reference is withheld (fail-closed), unless that concept is plainly about a piece of work: it acts on, or
# modifies, a work object that no person holds ("replace the flaky integration tests", "the exit survey results",
# "end date for the pilot contract"). A separation verb followed by a bare word ("offboarding priya") also counts as
# a person reference. Unknown nouns are not work objects, so the rule errs toward withholding.
OBJ_STOP = (r"(?:and|or|but|so|nor|to|for|with|at|in|on|of|from|by|as|because|since|after|before|while|when|if|then|"
            r"not|no|is|was|are|were|am|be|been|being|has|have|had|i|we|you|he|she|they|it|that|this|these|those|"
            r"who|which|me|him|them|us|about|the|a|an|my|our|your|his|her|their|its|person\w*|people|anyone|"
            r"someone|everyone|will|would|should|can|could|must|may|might)")
STOP_MOD = r"(?!" + OBJ_STOP + r"\b)"
ROLE_NOUN = (r"(?:designer|engineer|developer|contractor|consultant|freelancer|analyst|scientist|researcher|"
             r"architect|tester|intern|trainee|apprentice|hire|recruiter|coordinator|assistant|admin|"
             r"administrator|rep|ae|sdr|bdr|csm|marketer|copywriter|writer|editor|specialist|associate|officer|"
             r"receptionist|technician|accountant|bookkeeper|paralegal|counsel|lawyer|attorney|nurse|cashier|clerk|"
             r"staffer|employee|teammate|colleague|coworker|co worker|direct report|manager|supervisor|director|vp|"
             r"svp|evp|cto|cfo|ceo|coo|cmo|cpo|ciso|pm|tpm|founder|cofounder|co founder|team member|staff member|"
             r"joiner|new starter|hiring manager|head of [a-z]+|chief [a-z]+ officer)")
INITIAL_RE = re.compile(r"(?<![\w.'’&/-])(?!I\b)[A-Z](?:\.[A-Z])*(?:\.(?!\w)|(?=['’]s\b))")
INITIAL_NOT_AFTER = re.compile(r"(?i)\b(?:plan|option|phase|step|type|grade|class|section|tier|part|group|version|"
                               r"appendix|exhibit|line|item|wave|stage|level|track|column|row|building|block|vitamin|"
                               r"floor|room|gate|terminal|series|round|batch|cohort|model|size|the|a|an|our|its)\s+$")
LOWER_WORDS = set((
    "it that what there here let he she who where how when why this these those everyone everybody someone somebody "
    "anyone anybody nobody one today tomorrow yesterday tonight week month year quarter weekend team company customer "
    "client user vendor partner project product world people other others last next nothing everything something "
    "name time day morning night afternoon evening company's year's i we you they me us them him her his its our "
    "their your my and or but so then also just still now maybe probably yes no not okay ok hi hey well oh which "
    "whose each every all any some both either neither none much many more most less few several such same own "
    "the a an to of in on at for with from by as if because since after before while until once though although "
    "about into over under again further very too can could would should will shall may might must do does did "
    "is was are were be been being has have had get got go goes going make made take took see saw know knew think "
    "thought say said tell told ask asked need needs want wants like please thanks thank sure right left back "
    "monday tuesday wednesday thursday friday saturday sunday january february march april may june july august "
    "september october november december q1 q2 q3 q4 fy h1 h2 eod eow eom asap tbd fyi re note todo action "
    "sales legal finance marketing product engineering design support security ops leadership management "
    "recruiting hiring payroll procurement platform data infra board staff exec execs hr").split())
LOWER_POSSESSIVE = re.compile(r"(?<![\w'’.-])([a-z][a-z-]{1,20})(?=['’]s\b)")
LOWER_SUBJECT = re.compile(r"(?:^|(?<=[.!?;:\n])|(?<=\bwhen )|(?<=\bif )|(?<=\bonce )|(?<=\buntil )|(?<=\bbefore )|"
                           r"(?<=\bafter )|(?<=\bsince )|(?<=\bbecause )|(?<=\bso )|(?<=\band )|(?<=\bbut )|"
                           r"(?<=\bwhile )|(?<=\bthat ))\s*([a-z][a-z-]{1,20})(?= (?:is|was|will|won't|"
                           r"wont|has|had|isn't|wasn't|hasn't|needs|wants|asked|said|told|says|leaves|left|quits|"
                           r"quit|resigned|goes|went|gets|got|can't|cannot|should|would|might|must|'s|'ll|knows|"
                           r"doesn't|didn't|does|did|stays|starts|ends|exits|departs|stops|stopped|moves|moved|drops|dropped|"
                           r"joins|joined|transitions|returns|signs|signed|hands|handed)\b)")
# Verbs that take the person being separated as their object: a bare word after one of them is read as a person.
SEPARATION_VERB = re.compile(
    r"\b(?:offboard\w*|off boarding|fire|fired|firing|terminat(?:e|es|ed|ing)|dismiss(?:es|ed|ing)?|sack(?:s|ed|ing)?|"
    r"axe|axed|axing|lay(?:ing)? off|laid off|lays off|let(?:s|ting)? go(?: of)?|part(?:s|ing|ed)? ways with|"
    r"part(?:s|ing|ed)? with|mov(?:e|es|ed|ing) on from|manag(?:e|es|ed|ing) out|push(?:es|ed|ing)? out|eas(?:e|es|ed|ing) out|"
    r"forc(?:e|es|ed|ing) out|phas(?:e|es|ed|ing) out|cut(?:s|ting)? ties with|replac(?:e|es|ed|ing)|"
    r"replacements? for|backfill(?:s|ed|ing)?(?: for)?|exit(?:s|ed|ing)?|successors? (?:to|for)|departure of|"
    r"severance for|notice (?:for|to)|get(?:ting)? rid of|got rid of|demot(?:e|es|ed|ing)|pip|pipped|"
    r"writ(?:e|es|ing) up|wrote up|transition(?:s|ed|ing)? out|walk(?:s|ed|ing)? out|boot(?:s|ed|ing)?|"
    r"downsiz(?:e|es|ed|ing)|rif|riffed|separat(?:e|es|ed|ing) from) ([a-z][a-z'-]{1,20})\b")
# A lower-case word as the object of a verb that takes a person ("let priya go", "give priya notice", "telling priya
# the role is going away") is read as a person when it is not a common word or a work noun (see Scanner.bare_word).
PERSON_OBJECT = re.compile(
    r"\b(?:tell|tells|telling|told|give|gives|giving|gave|let|lets|letting|write up|writes up|writing up|wrote up|"
    r"warn|warns|warning|warned|inform|informs|informing|informed|notify|notifies|notifying|notified|remind|"
    r"reminds|reminding|thank|thanks|thanking|thanked|coach|coaching|coached|pip|demote|demoted|promote|"
    r"promoted|fire|fires|firing|fired|terminate|terminates|terminating|terminated|offboard|offboards|"
    r"offboarding|offboarded|replace|replaces|replacing|replaced|against) ([a-z][a-z'-]{1,20})"
    r"(?= (?:the|a|an|his|her|their|go|goes|notice|know|that|this|twice|again|about|up|loose|aside)\b|['’]s\b|[.,;!?]|$)"
    r"|\b(?:move|moves|moving|moved|transition|transitions|transitioning|transitioned|push|pushes|pushing|pushed|"
    r"ease|eases|easing|eased|walk|walks|walking|walked|usher|ushers|ushering|ushered|keep|keeps|keeping|kept|cut|"
    r"cuts|cutting|pull|pulls|pulling|pulled|see|sees|seeing|ship|ships|shipping|shipped|put|puts|putting) "
    r"([a-z][a-z'-]{1,20})(?= (?:out|off|loose|aside|go|on|through)\b)")
BARE_AFTER_CONCEPT = re.compile(r"(?: [a-z0-9'-]+){0,3}? (?:with|for|of|about|to|from|on) ([a-z][a-z'-]{1,20})\b")
# Verb forms that follow get/have/keep ("gets cut", "got let go"): never a name.
PARTICIPLES = set(("cut put set let hit done gone made paid sent told left lost built run shut quit laid said seen "
                   "taken given written broken chosen known shown hurt cast spent kept held found brought bought "
                   "caught taught thought fired axed sacked canned dumped booted pipped riffed bumped moved").split())
BARE_NOT_PERSON = LOWER_WORDS | set((
    "off out up down away early late soon later ahead first quickly quietly properly cleanly gracefully fully "
    "entirely altogether completely permanently immediately eventually already instead anyway together "
    "person persons person's plan plans date dates day days strategy criteria survey surveys interview interviews "
    "process program ramp code status sign row rows door doors lane lanes path paths it's that's legacy old new "
    "existing current unused stale broken flaky deprecated manual nightly weekly daily monthly internal external "
    "all both any some every each").split())
# Work objects: nouns that name a piece of work. A concept that acts on or modifies one is about the work.
WORK_NOUN = (
    r"(?:modes?|updates?|responses?|warnings?|events?|macros?|rooms?|polling|locks?|connections?|caches?|clusters?|instances?|integrations?|jobs?|pipelines?|services?|servers?|apis?|endpoints?|databases?|"
    r"dbs?|dashboards?|accounts?|apps?|applications?|plugins?|bots?|scripts?|tables?|queues?|buckets?|repos?|"
    r"repositor(?:y|ies)|projects?|offices?|contracts?|licen[cs]es?|vendors?|suppliers?|features?|modules?|reports?|"
    r"workspaces?|channels?|boards?|models?|migrations?|deployments?|rollouts?|campaigns?|tickets?|environments?|"
    r"stacks?|tools?|tooling|platforms?|subscriptions?|tiers?|connectors?|syncs?|workflows?|deals?|renewals?|"
    r"launch(?:es)?|releases?|betas?|pilots?|tenants?|configs?|configurations?|sdks?|clis?|backend|frontend|sites?|"
    r"websites?|pages?|forms?|docs?|documents?|documentation|decks?|sheets?|spreadsheets?|calendars?|invites?|"
    r"nodes?|keys?|tokens?|webhooks?|feeds?|exports?|imports?|plans?|pricing|prices?|users?|customers?|clients?|"
    r"partners?|regions?|zones?|versions?|v[0-9]+|tests?|suites?|surveys?|criteria|results?|rows?|records?|"
    r"fields?|columns?|branch(?:es)?|builds?|hotfix(?:es)?|patch(?:es)?|fix(?:es)?|certificates?|certs?|flags?|"
    r"sequences?|emails?|newsletters?|digests?|cron|leases?|programs?|programmes?|process(?:es)?|steps?|"
    r"checklists?|runbooks?|playbooks?|polic(?:y|ies)|clauses?|sections?|templates?|copy|content|assets?|images?|"
    r"files?|folders?|drives?|vms?|containers?|systems?|products?|skus?|catalogu?es?|budgets?|forecasts?|quotas?|"
    r"invoices?|orders?|shipments?|inventory|warehouses?|stores?|routes?|scopes?|backlogs?|sprints?|roadmaps?|"
    r"milestones?|specs?|specifications?|designs?|mockups?|prototypes?|wireframes?|components?|libraries|library|"
    r"packages?|dependenc(?:y|ies)|frameworks?|stacks?|logs?|alerts?|monitors?|metrics?|kpis?|segments?|cohorts?|"
    r"experiments?|a b tests?|ab tests?|variants?|funnels?|queries|query|indexes|index|schemas?|views?|"
    r"reports?|exports?|datasets?|data|fees?|rates? limits?|policies|terms|slas?|agreements? with (?:the|our|a) "
    r"(?:vendor|supplier|customer|client|partner)s?|support tickets?|cases?|issues?|bugs?|incidents?|outages?|"
    r"meetings?|standups?|retros?|syncs?|rituals?|cadences?|reviews?|audits?|questionnaires?|interviews? (?:data|"
    r"results|themes|notes|feedback|responses|answers|questions|template|templates|process|program|guide)|"
    r"brands?|markets?|verticals?|landing pages?|faqs?|handbooks?|guidelines?|creatives?|ads?|prospects?|leads? "
    r"(?:table|list|lists|data)|proposals?|rfps?|wikis?|articles?|chains?|firms?|networks?|facilit(?:y|ies)|"
    r"equipment|hardware|devices?|laptops?|machines?|printers?|routers?|ssl|tls|dns|domains?|urls?|links?|owners?|"
    r"maintainers?|duties|concerns|providers?|destinations?|sources?|dumps?|snapshots?|sandbox(?:es)?|"
    r"illustrations?|icons?|logos?|banners?|screens?|screenshots?|videos?|recordings?)")
# Some work heads also denote people or employment. For dismissal verbs they
# require explicit technical context, or an inanimate final head ("pilot project").
AMBIGUOUS_WORK_HEAD = re.compile(r"(?:pilots?|agents?|drivers?|jobs?|owners?|maintainers?|providers?|users?|roles?|positions?)")
TECHNICAL_WORK_OBJECT = re.compile(
    r"(?:[a-z0-9'-]+ ){0,2}(?:database|jdbc|odbc|gpu|graphics|network|kernel|device|software|virtual|"
    r"build|ci|monitoring|deployment|test|automation|browser|coding|api|telemetry|backup|logging|"
    r"cron|batch|scheduled|background|workflow|queue|ingestion|etl|service|ssh|oauth|saml|laptop|device|server|vehicle) "
    r"(?:[a-z0-9'-]+ ){0,2}(?:agents?|drivers?|jobs?|users?|providers?|fleets?)")

# A position (seat, role, req) named by its role rather than by a person: "a backfill for the open analyst role".
POSITION_NOUN = r"(?:seats?|slots?|roles?|positions?|reqs?|requisitions?|headcount|openings?|vacanc(?:y|ies))"
POSITION_BEFORE = re.compile(r"(?:\b(?:" + ROLE_NOUN + r")s? |\b" + POSITION_NOUN + r" (?:(?:is|was|are|were|being|been|be|"
                             r"will|gets?|got|getting) )*)$")
POSITION_AFTER = re.compile(r"(?: (?:for|of|on|in|to))? (?:the|a|an|our|this|that|its) (?:[a-z0-9]+ ){0,2}?" + POSITION_NOUN +
                            r"\b")
FILL_CONCEPT = re.compile(r"backfill\w*|replacements?")
ROLE_RE = re.compile(
    r"\b(?:our|the|my|your|their|his|her|this|that|these|those|a|an|one of (?:our|the|my|your|their)) "
    r"(?:(?:" + STOP_MOD + r"[a-z0-9]+ ){0,2}?" + ROLE_NOUN + r"(?:s|es)?"
    r"|(?:" + STOP_MOD + r"[a-z0-9]+ ){1,2}?leads?)\b"
    r"(?! (?:" + WORK_NOUN + r"|" + POSITION_NOUN + r"|panels?|consoles?|rights|permissions?|settings|mode|views?|salary|salaries|pay|comp|compensation|bands?|levels?|"
    r"ladders?|rubrics?|benchmarks?|interviews?|loops?|hiring|onboarding|training|titles?|careers?|market|"
    r"ui|software|"
    r"portals?|guides?|tools?|time|list|lists|gen|generation|magnets?|scores?|scoring|form)\b)")
WORK_DET = (r"(?:the person(?:'s|s)|his|her|their|your|my|the|a|an|our|this|that|these|those|its|all(?: (?:the|our))?|"
            r"any|some|every|each|both|unused|old|legacy|existing|current|new)")
NON_EXEMPT = re.compile(r"(?:severance|pip|pipped|performance (?:improvement )?plan|probation\w*|lay ?offs?|"
                        r"laying off|laid off|lay offs|rifs?|riffed|reduction in force|redundan\w*|resign\w*|notice period|"
                        r"garden leave|demot\w*|dismissals?)")
# The head noun of an object that is not a known work noun: person-like endings ("auditor", "technician",
# "accountant") read as a person; other heads ("logo", "connection") as a piece of work.
PERSON_LIKE = re.compile(r"[a-z]{2,}(?:er|or|ist|ant|ian|ee|man|men|woman|women|ess|ent)s?")
NOT_PERSON_LIKE = re.compile(
    r"[a-z]*(?:ments?|ness|less|cess|dress|gress|ness)|(?:number|order|letter|paper|layer|filter|folder|header|footer|"
    r"banner|border|buffer|cluster|container|counter|cursor|error|factor|floor|door|colou?r|monitor|sensor|vector|"
    r"sector|mirror|motor|parameter|character|chapter|quarter|matter|power|tower|water|weather|winter|summer|"
    r"center|centre|corner|offer|answer|cover|poster|sticker|timer|tracker|ticker|adapter|wrapper|helper|loader|"
    r"parser|scheduler|compiler|browser|player|viewer|reader|editor|printer|router|server|scanner|marker|trigger|"
    r"slider|spinner|handler|listener|runner|worker|builder|provider|supplier|vendor|tenant|variant|constant|"
    r"client|component|content|event|incident|agent|patent|percent|segment|calendar|dollar|meter|metre|amplifier|"
    r"identifier|modifier|tier|carrier|barrier|courier|ledger|register|roster|master|other|either|neither|"
    r"whether|together|rather|further|never|ever|however|whatever|after|later|over|under|super|sooner|faster|"
    r"better|earlier|bigger|smaller|higher|lower|newer|older|larger|longer|shorter|easier|harder|cleaner|"
    r"clearer|simpler|stronger|weaker|safer|deeper|wider|lighter|quicker|slower|closer|transfer|filler|"
    r"converter|connector|selector|generator|operator|processor|simulator|validator|indicator|calculator|"
    r"detector|projector|elevator|accelerator|refactor|backer|anchor|vendor|sponsor|investor|errand|"
    r"assessment|garment|segment|current|recent|frequent|urgent|different|present|absent|consistent|"
    r"significant|important|relevant|pending|spent|sent|went|meant|lent|bent|rent|tent|dent|scent|intent|"
    r"extent|talent|parent ?company|portion)s?")
# Groups of people: as the object of a separation concept they are people ("downsize the team").
PEOPLE_GROUP = set("team teams staff headcount org orgs organisation organization department departments workforce "
                   "crew crews squad squads pod pods group groups function unit units office offices people folks employees".split())
SEPARATION_VERB_SPAN = re.compile(r"(?:downsiz|right ?siz|lay|laid|cut|eliminat|terminat|fire|dismiss|offboard|reduc|"
                                  r"restructur|axe|sack|rif|riff|exit|replac|backfill|phas|push|forc|eas|transition|let|"
                                  r"manag|get rid|got rid|clear)")
# A dismissal verb requires positive evidence of a work object. Unknown occupational nouns are
# never inferred to be work merely because they follow a determiner ("fire the SRE").
STRICT_SEPARATION = re.compile(r"(?:fire(?:s|d)?|firing|terminat(?:e|es|ed|ing)|dismiss(?:es|ed|ing)?|"
                               r"sack(?:s|ed|ing)?|lay(?:s|ing)? off|laid off|let(?:s|ting)? go(?: of)?|"
                               r"rescind(?:s|ed|ing)?|term(?:s|ed|ing)?|swap(?:s|ped|ping)? out|"
                               r"replac\w*|offboard\w*|backfill\w*|downsiz\w*|right ?siz\w*|"
                               r"phas\w* out|eas\w* out|push\w* out|forc\w* out|manag\w* out|exit\w*|"
                               r"eliminat\w*|axe(?:s|d)?|axing|cut loose|cut ties(?: with)?)")
# The departments that handle an employment ending ("payroll", "HR"): plainly about the work when they describe the next
# noun ("payroll timing", "HR product") or follow "on", "in", "via" or "through" ("wrong on payroll").
INSTITUTION = re.compile(r"hr|human resources|people ops|people team|employee relations|payroll")
INSTITUTION_ADJUNCT = re.compile(r"\b(?:on|in|via|through) (?:the )?$")
# A work noun right before a concept makes the concept its modifier: "vendor exit", "contract end date".
WORK_BEFORE = re.compile(r"\b(?:" + WORK_NOUN + r") $")
# A concept right before (at most one word and) a work noun is a modifier: "exit survey", "layoff impact model".
WORK_MODIFIER = re.compile(r" (?:" + STOP_MOD + r"[a-z0-9][a-z0-9'-]* )?(?:" + WORK_NOUN + r")(?![\w'])")
HEAD_END = (r"(?= (?:" + OBJ_STOP + r"|next|this|last|today|tomorrow|tonight|asap|soon|now|early|later|once|until|"
            r"during|before|after|by|[a-z]+ly|(?:mon|tues|wednes|thurs|fri|satur|sun)day|week|month|quarter|year|"
            r"[a-z]+ed)\b|[.,;:!?)]|$)")
# A determiner-led object whose head is not a known work noun: the head is the last word before a function word,
# punctuation or the end ("replace the logo on the pricing page", "terminate the old SFTP connection after").
WORK_USE_GENERIC = re.compile(
    r"(?: (?P<prep>off|out|up|down|of|for|on|to))? (?:(?P<det>" + WORK_DET + r") )?(?P<np>(?:" + STOP_MOD + r"[a-z0-9][a-z0-9'.-]* ){0,3}?(?P<noun>" + STOP_MOD + r"[a-z][a-z0-9'-]*))" + HEAD_END)
# "the termination date for our night auditor": a person-like head after a concept and a preposition.
PERSON_PHRASE_AFTER = re.compile(
    r"(?: [a-z0-9'-]+){0,3}? (?:with|for|of|about|to|from|on) (?:our|my|your|the|a|an|their|his|her|this|that) "
    r"(?P<np>(?:[a-z0-9'-]+ ){0,2}?[a-z]+)" + HEAD_END)
# A bare word is read as a name only when a function word, a time word or punctuation follows it ("offboarding priya
# on the 15th"), not another noun ("exit fullscreen mode").
NAME_FOLLOW = re.compile(r"(?:[.,;:!?)]|$| (?:" + OBJ_STOP + r"|[a-z]+ly|go|goes|off|out|notice|know|twice|again|today|"
                         r"tomorrow|tonight|now|next|soon|asap|quietly|early|later|until|once|during|over|into|onto|"
                         r"up|down)\b)")
PERSON_DET = re.compile(r"(?:the person(?:'s|s)|his|her|their|your|my)$")
WORK_USE = re.compile(r"(?: (?P<prep>off|out|up|down|of|for|on|to))?"
                      r" (?:(?P<det>" + WORK_DET + r") )?(?:" + STOP_MOD + r"[a-z0-9][a-z0-9'.-]* ){0,3}?"
                      r"(?P<noun>" + WORK_NOUN + r")(?![\w'])")
# Nouns that a person holds: when "the person's"/his/her/their come before them, the object is the person's.
PERSON_HELD = re.compile(r"(?:contracts?|access|accounts?|laptops?|devices?|phones?|emails?|inbox(?:es)?|reports?|"
                         r"projects?|clients?|customers?|tickets?|workload|duties|calendars?|payroll|equipment|"
                         r"hardware|desks?|offices?|keys?|tokens?|licen[cs]es?|subscriptions?|users?|logins?|"
                         r"credentials|portfolios?|territor(?:y|ies)|pipelines?|deals?|renewals?|jobs?|" + POSITION_NOUN + r"|"
                         r"files?|folders?|drives?|machines?|records?|reviews?|meetings?|plans?|terms|agreements?)")
PERSON_AFTER = re.compile(r"(?:'s)? (?:(?:[a-z0-9]+ ){0,2}(?:for|of|with|from|to|by|at|on|about) )?"
                          r"(?:the person|him|her|them|my report|the report|your report|the employee)\b")
PERSON_POSS_BEFORE = re.compile(r"(?:the person(?:'s|s)|\bhis|\bher|\btheir|\byour|\bmy) (?:[a-z0-9]+ )?$")
PERSON_REF = re.compile(r"\b(?:the person|this person|that person|him|her|hers|he|she|his|them|they|their|"
                        r"my report|the report|your report|our report|the employee|this employee)\b")


def tag_any_case(text, names, non_person):
    """people_forms helper: initials, lower-case names (possessive or sentence subject) and the two people's names in
    any case become "the person"; capitalised names are left to tag_people."""
    for pattern in names:
        text = pattern.sub("the person", text)

    def initial(m):
        if INITIAL_NOT_AFTER.search(text[:m.start()]):
            return m.group(0)
        return "the person"
    text = INITIAL_RE.sub(initial, text)

    def lower(m):
        word = m.group(1)
        commercial = COMMERCIAL_OBJECT.search(m.string[:m.end(1)])
        if commercial and commercial.end() == m.end(1):
            return m.group(0)
        if word in LOWER_WORDS or word.casefold() in non_person or (re.fullmatch(WORK_NOUN, word) and not AMBIGUOUS_WORK_HEAD.fullmatch(word)) or DETERMINER_BEFORE.search(m.string[:m.start(1)]):
            return m.group(0)
        return m.group(0)[:m.start(1) - m.start()] + "the person"
    text = LOWER_POSSESSIVE.sub(lower, text)
    return LOWER_SUBJECT.sub(lower, text)


def edit_distance(a, b, limit):
    """True when a and b are at most `limit` edits apart (insert, delete, substitute, or swap two neighbouring
    letters). Bounded: words whose lengths differ by more than the limit are never compared."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return False
        prev2, prev = prev, cur
    return prev[len(b)] <= limit


PLACEHOLDERS = {"{who}": WHO, "{other}": OTHER, "{be}": BE, "{end}": END, "{frag}": FRAG, "{pp}": PERSON_POSS,
                "{p}": PERSON}
LITERAL_PARTS = re.compile(r"(\[(?:\\.|[^\]\\])*\]|\(\?<[!=](?:\\.|[^()\\])*\))")


def ascii_digits(text):
    if not NON_ASCII.search(text):
        return text
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in text)


def normalise(text):
    """Scan form: HTML entities decoded, NFKC, digits made ASCII, invisible/format characters and accents removed,
    case-folded, look-alike letters mapped to Latin, dashes/underscores/whitespace collapsed, spaced-out letters
    joined."""
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if ch not in INVISIBLE and unicodedata.category(ch) not in ("Cf", "Mn", "Me", "Sk"))
    text = ascii_digits(unicodedata.normalize("NFC", text).casefold().translate(CONFUSABLES))
    text = ARROW.sub(" to ", text)
    text = SPACED_DASH.sub(" ; ", text)
    text = "".join(" " if ch.isspace() or ch == "_" or unicodedata.category(ch) == "Pd" else ch for ch in text)
    text = SPACED_LETTERS.sub(lambda m: re.sub(r"[ .*·•]", "", m.group(0)), text)
    return " ".join(text.split())


CONTRACTIONS = [(re.compile(r"\bi'?m\b"), "i am"), (re.compile(r"\bi'?ve\b"), "i have"),
                (re.compile(r"\b(you|we|they)'re\b"), r"\1 are"),
                (re.compile(r"\b(he|she|it|that|there|who|what|the person)'s\b"), r"\1 is"),
                (re.compile(r"\b(\w+)'ve\b"), r"\1 have"), (re.compile(r"\b(\w+)'ll\b"), r"\1 will"),
                (re.compile(r"\b(\w+)'d\b"), r"\1 would"), (re.compile(r"\bcan'?t\b"), "cannot"),
                (re.compile(r"\bwon'?t\b"), "will not"), (re.compile(r"\b(\w+)n't\b"), r"\1 not")]


def expand_contractions(text):
    """A second scan form with common contractions written out ("i'm seeing" -> "i am seeing")."""
    for pattern, repl in CONTRACTIONS:
        text = pattern.sub(repl, text)
    return text


def expand(term):
    """A space in a pattern matches one optional separator ("sick leave" also hits "sickleave"); placeholders are
    filled in after that, so their own spaces stay required."""
    parts = LITERAL_PARTS.split(term)
    term = "".join(p if i % 2 else p.replace(" ", " ?") for i, p in enumerate(parts))
    for key, value in PLACEHOLDERS.items():
        term = term.replace(key, value)
    return term


# A request to cover or move work does not make its attached reason work-related.
# Evaluate the reason/temporal clause independently, before unrelated work nouns
# in a later clause can make an unknown personal reason appear safe.
WORK_CONTEXT = re.compile(r"\b(?:" + WORK_NOUN + r"|calls?|demos?|checks?|sign off|handovers?|"
                          r"announcements?|offsites?|kickoff|go live|freezes?|upgrades?|changes?|refits?|"
                          r"retrains?|reorgs?|mergers?|cycles?|all hands|usage|onboarding|redirects?|"
                          r"observability|architecture|training|billing|qa|qbrs?|ebrs?|uat|soc2|deployments?|deployed|deploying|compiles?|builds?)\b")
TASK_REQUEST = re.compile(r"^(?:please )?(?:cover|schedule|reschedule|block|handle|take|move|swap|shift|"
                          r"cancel|postpone|delay|miss|skip|join|attend|send|prepare|finish|complete|publish)\b")
CONTEXT_CUE = re.compile(r"\b(?:during|while|because(?: of)?|due to|after|before|until) ")
CALENDAR_CONTEXT = re.compile(
    r"(?:the |our )?(?:(?:next|this|last|peak) )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"spring|summer|autumn|fall|winter|season|holidays|q[1-4]|[0-9]+(?:st|nd|rd|th)?|"
    r"month end|year end|weekend|business hours|office hours|[0-9]+ days are up|1:1)[.!?]?")
PASSIVE_SEPARATION = re.compile(
    r"(?:^|[.;!?] )(?P<subject>[^.;!?]{1,120}?) "
    r"(?:(?:will|would|should|may|might|must|can|could) be|is|are|was|were|has been|have been|being) "
    r"(?:quietly |formally |finally )?(?:fired|terminated|dismissed|sacked|laid off|let go|"
    r"shown the door|made redundant|offboarded)\b")
BUYOUT_VERB = re.compile(r"(?:buy|buys|buying|bought) out|buyouts?")
COMMERCIAL_OBJECT = re.compile(
    r"(?:(?:the|our|a|an|this|that|old|new|existing|enterprise) )*"
    r"(?:vendor|supplier|customer|commercial|licen[cs]e|service|software|subscription) "
    r"(?:contracts?|agreements?|leases?)(?:'s|s')?"
    r"(?: (?:(?:remaining|outstanding|unexpired|current|last) )?(?:terms?|period|duration|balance))?")


class Scanner:
    """Compiled pattern lists from prep.json: sensitive personal topics (withhold the item) and wording that rates
    or judges a person or describes their mood (leave the item out). The two people's names are masked first, so
    a name ("Hope", "Christian") is never read as a term."""

    def __init__(self, sensitive, judgement, r, names=None, work_nouns=(), fuzzy=None, person_terms=None,
                 concepts=(), non_person=()):
        self.sensitive_lists, self.judgement_lists, self.r = sensitive, judgement, r
        fuzzy = fuzzy or {}
        self.fuzzy_exact = set(fuzzy.get("fuzzy_terms", ())) | set(fuzzy.get("fuzzy_ignore", ()))
        self.fuzzy_terms = {}
        for term in fuzzy.get("fuzzy_terms", ()):
            self.fuzzy_terms.setdefault(term[0], []).append(term)
        self.fuzzy_seen = {}
        self.masks = []
        for token in sorted(names or [], key=lambda t: (-len(t), t)):
            self.masks.append(re.compile(r"(?<![^\W_])" + re.escape(token) + r"(?![^\W_])"))
        parts = []
        for category in sorted(sensitive):
            parts.extend((t, "$.sensitive_terms.{}[{}]".format(category, i)) for i, t in enumerate(sensitive[category]))
        self.sensitive = self.compile(parts)
        # Each term on its own too, to look at every place a term matches (only used once the combined list hits).
        self.sensitive_each = [re.compile(expand(t), re.IGNORECASE) for t, _ in parts]
        # A match that is only a modifier of a work noun ("fertility clinic client", "visa sponsorship FAQ",
        # "bereavement leave section of the handbook") is about the work, not a person. At most one word may sit in
        # between, and it may not be a function word ("pregnant and the client..." still counts).
        nouns = self.compile([(t, "$.work_modifier_nouns[{}]".format(i)) for i, t in enumerate(work_nouns)]) \
            if work_nouns else None
        self.work_after = None if nouns is None else re.compile(
            r"(?:'s)? (?:room booking )?(?:(?!" + FUNCTION_WORDS + r"\b)[a-z0-9']+ )?(?:" + nouns.pattern + r")\b", re.IGNORECASE)
        # ...or a description of a work noun: "our client is a fertility clinic chain".
        self.work_before = None if nouns is None else re.compile(
            r"\b(?:" + nouns.pattern + r") (?:is|are|was|were|includes?|included) (?:an? |the |one of the )?"
            r"(?:(?!" + FUNCTION_WORDS + r"\b)[a-z0-9']+ ){0,2}$", re.IGNORECASE)
        # Person-linked patterns ({p}, {pp}), matched on the name-agnostic person form only; and the separation
        # concepts used for the people-topic rule (see check).
        person_terms = person_terms or {}
        pparts = []
        for category in sorted(person_terms):
            pparts.extend((t, "$.person_terms.{}[{}]".format(category, i)) for i, t in enumerate(person_terms[category]))
        self.person = self.compile(pparts)
        self.person_each = [re.compile(expand(t), re.IGNORECASE) for t, _ in pparts]
        self.concepts = self.compile([(t, "$.separation_concepts[{}]".format(i)) for i, t in enumerate(concepts)])
        self.non_person = set(w.casefold() for w in non_person)
        self.person_ref = PERSON_REF
        self.masks_any_case = []
        self.judge_any = self.compile([(t, "$.judgement_terms.any[{}]".format(i))
                                       for i, t in enumerate(judgement["any"])])
        self.judge_authored = self.compile([(t, "$.judgement_terms.authored[{}]".format(i))
                                            for i, t in enumerate(judgement["authored"])])

    def compile(self, terms):
        pieces = []
        for term, path in terms:
            if "\\d" in term:
                raise self.r.fail(path, "use [0-9] instead of \\d (only ASCII digits are matched)")
            pattern = expand(term)
            try:
                re.compile(pattern)
            except (re.error, OverflowError, RecursionError) as exc:
                raise self.r.fail(path, "bad pattern ({})".format(exc)) from None
            pieces.append("(?:{})".format(pattern))
        try:
            return re.compile("|".join(pieces) or "(?!)", re.IGNORECASE)
        except (re.error, OverflowError, RecursionError) as exc:
            raise self.r.fail(terms[0][1] if terms else "$", "bad pattern list ({})".format(exc)) from None

    def with_names(self, names):
        """The same lists with these people's name parts masked (case-sensitive: a capitalised name is masked, the
        same word in lower case is still read as a word)."""
        tokens = set()
        for name in names:
            for token in re.split(r"[\s.,]+", name or ""):
                token = token.strip("'’-")
                if len(token) >= 2 and not token.isupper() and token[0].isalpha():
                    tokens.add(token)
        clone = Scanner.__new__(Scanner)
        clone.__dict__.update(self.__dict__)
        clone.masks = [re.compile(r"(?<![^\W_])" + re.escape(t) + r"(?![^\W_])")
                       for t in sorted(tokens, key=lambda t: (-len(t), t))]
        # For the person forms only: the same names in any case ("riley", "RILEY").
        clone.masks_any_case = [re.compile(r"(?<![^\W_])" + re.escape(t) + r"(?![^\W_])", re.IGNORECASE)
                                for t in sorted(tokens, key=lambda t: (-len(t), t))]
        return clone

    def mask(self, text):
        for pattern in self.masks:
            text = pattern.sub("the person", text)
        return text

    def forms(self, text):
        base = [normalise(self.mask(text))]
        if MARKUP.search(text):
            base.append(normalise(self.mask(MARKUP.sub("", text))))
        # Words in prep.json `non_person_words` ("Replace Jenkins with…") are never read as a name.
        named = NAMED_AFTER.sub(lambda m: m.group(0) if m.group(0)[len(m.group(1)) + 1:].casefold() in self.non_person
                                else m.group(1) + " the person",
                                NAMED.sub(lambda m: m.group(0) if m.group(0).casefold() in self.non_person else "the person",
                                          self.mask(text)))
        if named != self.mask(text):
            base.append(normalise(named))
        for f in list(base):
            possessive = NAME_AS_POSSESSIVE.sub("their ", f)
            if possessive != f:
                base.append(possessive)
        for f in list(base):
            expanded = expand_contractions(f)
            if expanded != f:
                base.append(expanded)
        leet = [LEET_WORD.sub(lambda m: m.group(0).translate(LEET) if re.search(r"[0-9@$]", m.group(0))
                              else m.group(0), f) for f in base]
        return base + [f for f in leet if f not in base]

    def people_forms(self, text):
        """Scan forms for `person_terms`, the separation rule and (with forms) `sensitive_terms`: names masked, then
        every other way of naming a person read as "the person" (the two names in any case, initials, lower-case
        names, capitalised name-like words, role nouns; see tag_any_case, tag_people, ROLE_RE); markup removed. The
        plain form (only the two names masked) comes last."""
        base = self.mask(MARKUP.sub("", text))
        tagged = normalise(tag_people(tag_any_case(base, self.masks_any_case, self.non_person), self.non_person))
        tagged = ROLE_RE.sub("the person", tagged)
        tagged = PERSON_OBJECT.sub(lambda m: self.tag_object(m), tagged)
        forms = [tagged]
        plain = normalise(base)
        return forms + ([plain] if plain not in forms else [])

    def person_like(self, noun):
        return bool(PERSON_LIKE.fullmatch(noun)) and not NOT_PERSON_LIKE.fullmatch(noun) and \
            not re.fullmatch(WORK_NOUN, noun) and noun not in BARE_NOT_PERSON

    def work_use(self, form, m):
        """(exempt, person) for the separation concept at m. exempt: the concept is plainly about a piece of work:
        it modifies a work noun ("exit survey", "layoff impact model"), a work noun modifies it ("vendor exit",
        "contract end date"), or it acts on an object that is not a person and is not held by one ("replace the
        flaky integration tests", "replace the logo", "end date for the pilot contract"). Never after a person's
        possessive ("the person's exit plan"), for a concept that names a person, or when a person follows ("the
        contract for the person"); concepts that only name an HR process (severance, PIP, probation, layoffs, ...)
        are exempt only as a modifier. person: the object's head noun reads as a person ("our night auditor")."""
        span = m.group(0)
        if BUYOUT_VERB.fullmatch(span):
            obj = WORK_USE_GENERIC.match(form, m.end())
            commercial = bool(obj and COMMERCIAL_OBJECT.fullmatch(obj.group("np")))
            return commercial, not commercial
        # In split phrasal verbs the object precedes "go". An unknown occupation
        # still denotes a person; a complete technical object can remain work.
        split_let = re.fullmatch(r"let(?:s|ting)? (?P<np>(?:[a-z0-9']+ ){0,5}[a-z0-9']+) go", span)
        if split_let:
            head = split_let.group("np").split()[-1]
            work = self.unambiguous_work_object(split_let.group("np"), head) and not PERSON_REF.search(split_let.group("np"))
            return work, not work
        if re.fullmatch(r"(?:eliminat|reduc|cut|remov|terminat)\w*", span) and re.match(
                r" (?:(?:the|our|one|two|three|four|five|[0-9]+) )?(?:roles?|positions?|headcount)"
                r"(?= (?:on|in|from|by|before|after|at|next|this)\b|[.!?]|$)", form[m.end():]):
            return False, True
        # Positive onboarding/provisioning and work attendance are not separation predicates.
        if span == "package" and re.search(r"\b(?:welcome|onboarding) $", form[:m.start()]):
            return True, False
        if span == "be with us" and re.match(r" at (?:the |our |a )?(?:q[1-4] )?(?:offsite|workshop|demo|meeting)\b", form[m.end():]) and not re.search(r"\b(?:not|never|won't|wouldn't|cannot|can't)\b", form[:m.start()]):
            return True, False
        if re.fullmatch(r"the person's (?:badge|access)", span) and re.search(r"\b(?:set up|provision|configure) $", form[:m.start()]):
            return True, False
        if re.fullmatch(r"cancel(?:ing|ling)? the person's trial account", span):
            return True, False
        if PERSON_REF.search(span):
            # A person SUBJECT releasing a technical object is not an employment ending.
            # The old subject-bearing concept swallowed "Mateo will let go" before looking at "the lock".
            if re.fullmatch(r"the person (?:will |can |should |must |would |may |might )?let(?:s|ting)? go", span):
                obj = WORK_USE_GENERIC.match(form, m.end())
                if obj and self.unambiguous_work_object(obj.group("np"), obj.group("noun")) and obj.group("noun") not in ("contracts", "contract", "accounts", "account", "access"):
                    return self.work_adjunct(form, obj.end(), obj.group("noun")), False
            return False, False
        if PERSON_POSS_BEFORE.search(form, 0, m.start()):
            return False, False
        modifier = WORK_MODIFIER.match(form, m.end())
        if modifier and not STRICT_SEPARATION.fullmatch(span) and not PERSON_AFTER.match(form, modifier.end()):
            return True, False
        if NON_EXEMPT.fullmatch(span) and not STRICT_SEPARATION.fullmatch(span):
            return False, False
        if INSTITUTION.fullmatch(span):
            head = re.match(r" ([a-z][a-z0-9'-]*)" + HEAD_END, form[m.end():])
            if INSTITUTION_ADJUNCT.search(form, 0, m.start()) or (head and not self.person_like(head.group(1)) and
                                                                  head.group(1) not in BARE_NOT_PERSON):
                return not PERSON_AFTER.match(form, m.end()), False
        if WORK_BEFORE.search(form, 0, m.start()) and not PERSON_AFTER.match(form, m.end()):
            return True, False
        if FILL_CONCEPT.fullmatch(span):
            # Filling a position named by its role: "analyst backfill", "the analyst seat being backfilled", "a backfill
            # for the open analyst role". Never "the person's seat" or "the role the person is leaving".
            before = POSITION_BEFORE.search(form, 0, m.start())
            if before and not re.search(r"(?:the person(?:'s|s)|\bhis|\bher|\btheir) (?:[a-z0-9]+ ){0,2}$",
                                        form[:before.start()]):
                return not PERSON_AFTER.match(form, m.end()), False
            after = POSITION_AFTER.match(form, m.end())
            if after:
                return not PERSON_AFTER.match(form, after.end()), False
        # For risky verbs parse the COMPLETE noun phrase. A prefix such as "workflow" in
        # "the workflow chief" is not evidence that the object is a piece of work.
        w = WORK_USE_GENERIC.match(form, m.end()) if STRICT_SEPARATION.fullmatch(span) else \
            (WORK_USE.match(form, m.end()) or WORK_USE_GENERIC.match(form, m.end()))
        if not w or (w.group("prep") in ("of", "for", "on", "to") and span.endswith("ed")):
            return False, False  # "eliminated in the Q4 plan", "replaced on the project": not an object of the concept
        noun, det = w.group("noun"), w.group("det") or ""
        if STRICT_SEPARATION.fullmatch(span) and noun in ("trial", "trials", "pilot", "pilots") and re.match(
                r" for (?:[a-z0-9'-]+ ){0,4}(?:sandbox|app|software|service|feature|workspace|integration)\b", form[w.end():]):
            return True, False
        if STRICT_SEPARATION.fullmatch(span) and AMBIGUOUS_WORK_HEAD.fullmatch(noun):
            if not self.unambiguous_work_object(w.group("np"), noun):
                return False, True
            return self.work_adjunct(form, w.end(), noun, w.group("np")), False
        if STRICT_SEPARATION.fullmatch(span) and not re.fullmatch(WORK_NOUN, noun):
            if self.unambiguous_work_object(w.group("np"), noun):
                return self.work_adjunct(form, w.end(), noun), False
            # The unmasked form retains a colleague's name after the complete object.
            # Parse the whole object before that relative clause, rather than accepting
            # a work-noun prefix in an unknown role ("workflow chief").
            relative = re.search(r" [a-z][a-z'-]* (?:flagged|reported|identified|documented|reviewed|requested)\b", form[m.end():])
            if relative:
                prefix_text = form[:m.end() + relative.start()]
                prefix = WORK_USE_GENERIC.match(prefix_text, m.end())
                if prefix and prefix.end() == len(prefix_text) and self.unambiguous_work_object(prefix.group("np"), prefix.group("noun")):
                    return True, False
        if not re.fullmatch(WORK_NOUN, noun):
            if STRICT_SEPARATION.fullmatch(span):
                return False, True
            words = w.group("np").split()
            if PERSON_DET.match(det):
                return False, False  # "her badge", "the person's old desk": held by a person
            if any(self.person_like(x) or (x in PEOPLE_GROUP and SEPARATION_VERB_SPAN.match(span)) for x in words):
                return False, True  # "terminate our night auditor", "downsize the team"
            if not det and len(words) < 2:
                return False, False  # one bare word ("offboarding priya") is never shown to be a piece of work
        elif PERSON_DET.match(det) and PERSON_HELD.fullmatch(noun):
            return False, False
        return self.work_adjunct(form, w.end(), noun), False

    def unambiguous_work_object(self, phrase, noun):
        """A complete technical object, not a human occupation disguised by a work head."""
        if TECHNICAL_WORK_OBJECT.fullmatch(phrase):
            return True
        return bool(re.fullmatch(WORK_NOUN, noun)) and not AMBIGUOUS_WORK_HEAD.fullmatch(noun)

    def work_adjunct(self, form, end, noun, phrase=""):
        """A colleague assisting with a work object, or identifying it, is not its target.
        Employment-bearing objects retain the conservative person-adjunct rule."""
        if not PERSON_AFTER.match(form, end):
            return True
        if noun in ("contract", "contracts", "agreement", "agreements", "account", "accounts", "access",
                    "role", "roles", "position", "positions", "job", "jobs") and not TECHNICAL_WORK_OBJECT.fullmatch(phrase):
            return False
        return bool(re.match(r" with the person\b", form[end:]) or
                    re.match(r" the person (?:flagged|reported|identified|documented|reviewed|requested)\b",
                             form[end:]))

    def tag_object(self, m):
        group = 1 if m.group(1) else 2
        if self.bare_word(m.group(group), m.string[m.end():]):
            return m.group(0)
        return m.group(0)[:m.start(group) - m.start()] + "the person"

    def bare_word(self, word, rest):
        """True when word is not read as a person: a common word, a work noun, a configured non-person word, an -ly
        adverb, or a word followed by a work noun."""
        two = re.match(r" ([a-z]{2,20})", rest)  # a two-part name: "ines marie"
        if not NAME_FOLLOW.match(rest) and two and two.group(1) not in BARE_NOT_PERSON and \
                not re.fullmatch(WORK_NOUN, two.group(1)) and NAME_FOLLOW.match(rest, two.end()):
            rest = rest[two.end():]
        return not NAME_FOLLOW.match(rest) or \
            word in BARE_NOT_PERSON or word in self.non_person or word.endswith("ly") or word in PARTICIPLES or \
            (len(word) >= 5 and word.endswith(("ed", "ing", "en"))) or bool(self.concepts.fullmatch(word)) or \
            bool(re.fullmatch(WORK_NOUN, word)) or bool(re.match(r"(?: [a-z0-9'-]+){0,2} " + WORK_NOUN + r"\b", rest))

    def bare_person(self, form, m):
        """True when the word captured by m (after a separation verb, "offboarding priya", or after a separation
        concept and a preposition, "the termination talk with kwame") reads as a person: not a common word, a work
        noun or a configured non-person word, and not followed by a work noun."""
        return not self.bare_word(m.group(1), form[m.end():])

    def unknown_personal_context(self, text):
        """An unknown reason attached to a person's work request is held for review."""
        form = normalise(self.mask(text))
        for match in CONTEXT_CUE.finditer(form):
            before = form[:match.start()]
            if not WORK_CONTEXT.search(before) or not (TASK_REQUEST.search(before) or re.search(r"\b(?:i|my|me|we|our|the person)\b", before)):
                continue
            clause = re.split(r"[.;!?]|,|\b(?:and|but|so)\b", form[match.end():], maxsplit=1)[0].strip()
            if CALENDAR_CONTEXT.fullmatch(clause) or re.fullmatch(r"it (?:expires?|compiles?|builds?|deploys?)(?: [a-z]+){0,2}", clause) or re.fullmatch(
                    r"(?:[a-z][a-z'-]* ){1,3}(?:presents?|ships?|signs?|compiles?|deploys?|publishes?|demos?)(?: (?:it|them|v[0-9]+))?", clause):
                continue
            primary = re.split(r"\b(?:for|with|on|at|in|from|about)\b", clause, maxsplit=1)[0].strip()
            if not WORK_CONTEXT.search(primary):
                return True
        return False

    def separation(self, pforms):
        """The fail-closed rule, on every topic: some separation concept that is not plainly about a piece of work,
        plus any person reference (in any of the person forms of the same text)."""
        concept = person = False
        for f in pforms:
            for passive in PASSIVE_SEPARATION.finditer(f):
                subject = passive.group("subject")
                head = subject.split()[-1]
                if PERSON_REF.search(subject) or not self.unambiguous_work_object(subject, head):
                    return True
            if any(self.bare_person(f, m) for m in SEPARATION_VERB.finditer(f)):
                return True  # "offboarding priya", "let go of devon": the verb is the concept
            person = person or bool(self.person_ref.search(f))
            for m in self.concepts.finditer(f):
                exempt, object_person = self.work_use(f, m)
                if not exempt:
                    concept = True
                    after = BARE_AFTER_CONCEPT.match(f, m.end())
                    phrase = PERSON_PHRASE_AFTER.match(f, m.end())
                    person = person or object_person or bool(after and self.bare_person(f, after)) or \
                        bool(phrase and any(self.person_like(x) for x in phrase.group("np").split()))
            if concept and person:
                return True
        return False

    def personal_in(self, form, each=None):
        """True when some sensitive term matches somewhere that is not just a modifier of a work noun (one that
        follows a personal possessive always counts)."""
        if self.work_after is None:
            return True
        for pattern in (self.sensitive_each if each is None else each):
            for m in pattern.finditer(form):
                # Restrict the exception to this matched domain phrase and a complete work adjunct.
                # Other sensitive matches in the same sentence are still evaluated.
                if re.search(r"having a (?:hard|rough|tough|difficult) time$", m.group(0)) and re.fullmatch(
                        r" with (?:the |our |a )?(?:[a-z0-9]+ ){0,2}" + WORK_NOUN + r"[.!]?", form[m.end():]):
                    continue
                if re.fullmatch(r"voice dictation(?: for (?:the person's|the person|their|his|her))?", m.group(0)) and re.fullmatch(
                        r"(?: for (?:the person's|the person|their|his|her))? (?:is )?(?:demo|presentation|recording)[.!]?", form[m.end():]):
                    continue
                if re.fullmatch(r"(?:mat|maternity|pat|paternity|parental|adoption|bereavement|compassionate|medical|sick) leave", m.group(0)) and not POSSESSIVE_BEFORE.search(form, 0, m.start()):
                    if re.match(r" (?:accrual|eligibility|accounting|reconciliation) (?:polic(?:y|ies)|rules?|models?|formulas?)\b", form[m.end():]) or re.search(
                            r"\b(?:payroll|benefits) (?:accrual|accounting|reconciliation|forecasting) for $", form[:m.start()]):
                        continue
                if re.search(r"\b(?:raise|increase|bump)$", m.group(0)) and re.fullmatch(
                        r" in (?:the |our |a )?(?:[a-z0-9'-]+ ){0,3}(?:threshold|quota|limit|coverage|throughput|bandwidth|price)[.!]?", form[m.end():]):
                    continue
                work = self.work_after.match(form, m.end()) or self.work_before.search(form, 0, m.start())
                if m.end() == m.start() or not work or NO_WORK_EXCEPTION.search(m.group(0)) \
                        or re.search(r"\b(?:asked|needs?|requested|let|letting|manage|managing|replace|replacing)\b", m.group(0)) \
                        or POSSESSIVE.match(m.group(0)) or POSSESSIVE_BEFORE.search(form, 0, m.start()):
                    return True
        return False

    def fuzzy_in(self, form):
        """True when a word is a near-miss spelling of a fuzzy term: same first letter, at most 1 edit (2 for terms
        of 10+ letters; a swap of two neighbouring letters is 1 edit). Exact terms and fuzzy_ignore words don't
        count (the patterns handle exact words with their context)."""
        for word in FUZZY_WORD.findall(form):
            if word in self.fuzzy_exact:
                continue
            hit = self.fuzzy_seen.get(word)
            if hit is None:
                hit = any(edit_distance(word, t, 1 if len(t) < 10 else 2)
                          for t in self.fuzzy_terms.get(word[0], ()))
                if len(self.fuzzy_seen) < 50000:
                    self.fuzzy_seen[word] = hit
            if hit:
                return True
        return False

    def check(self, text, authored):
        """'personal', 'judgement' or None for one text. authored: written by the agent (summaries, evidence
        notes), where any rating, judgement or mood wording counts; in a quote only wording aimed at a person
        other than the speaker does (the "any" list). `sensitive_terms` are matched on the scan forms and on the
        person forms (so a pay or health term about anyone counts the same as one about the two people);
        `person_terms` and the separation rule on the person forms. The topic plays no part here."""
        if not text:
            return None
        forms = self.forms(text)
        pforms = self.people_forms(text)
        extra = []
        for f in pforms:
            for g in (f, NAME_AS_POSSESSIVE.sub("their ", f)):
                for h in (g, expand_contractions(g)):
                    if h not in forms and h not in extra:
                        extra.append(h)
        if any(self.sensitive.search(f) and self.personal_in(f) for f in forms + extra) or \
                any(self.fuzzy_in(f) for f in forms):
            return "personal"
        if any(self.person.search(f) and self.personal_in(f, self.person_each) for f in pforms + extra):
            return "personal"
        if self.separation(pforms) or self.unknown_personal_context(text):
            return "personal"
        if any(self.judge_any.search(f) for f in forms):
            return "judgement"
        if authored and any(self.judge_authored.search(f) for f in forms):
            return "judgement"
        return None


# --------------------------------------------------------------------------- personal data and escaping

# E-mails and phone numbers: granola_text.redact_contacts (pattern-based; phones only on a clear signal). This
# package also removes links and ID-like numbers (SSN-style 123-45-6789, or a number after "SSN" / "tax id") first.
LINK = re.compile(r"(?i)(?:(?:https?|ftp)://|www\.)\S+")
ID_NUMBER = re.compile(r"(?<![\w/.-])[0-9]{3}([ -])[0-9]{2}\1[0-9]{4}(?![\w/-]|[.,][0-9])"
                       r"|(?i:\b(?:ssn|social security(?: number| no\.?)?|sin|tax id|tin|itin))[:# ]*[0-9][0-9 -]{6,10}"
                       r"[0-9](?![\w/-])")


def redact(text):
    """Plain text with links, ID-like numbers, e-mails and phone numbers replaced by placeholders."""
    if text is None:
        return None
    return gt.redact_contacts(ID_NUMBER.sub("[ID number removed]", LINK.sub("[link removed]", text)))


def md(text):
    """Printed markdown: redacted, then escaped by granola_text.md (no link, image, HTML, mention, math or
    formatting can form)."""
    return "" if text is None else gt.md(redact(text))


# --------------------------------------------------------------------------- config


def load_config(path):
    config = load_json(path, "config")
    r = Reader("config {}".format(path))
    r.obj(config, "$")
    missing = [k for k in CONFIG_KEYS if k not in config]
    if missing:
        raise InputError("config {}: missing key(s): {}".format(path, ", ".join(missing)))
    if config.get("schema_version") != SCHEMA_VERSION:
        raise r.fail("$.schema_version", "must be {}".format(SCHEMA_VERSION))
    for key, (low, high) in CONFIG_INTS.items():
        r.integer(config, key, "$", low, high)
    for key in ["ignored_attendee_domains", "forbidden_keys"]:
        values = r.lst(config, key, "$", required=True)
        if not all(isinstance(v, str) and v.strip() for v in values):
            raise r.fail("$." + key, "must be a list of non-empty strings")
    config["ignored_attendee_domains"] = [v.strip().lower() for v in config["ignored_attendee_domains"]]
    config["_forbidden"] = set(v.strip().lower() for v in config["forbidden_keys"])
    labels = r.obj(config["category_labels"], "$.category_labels")
    for key in sorted(labels):
        r.text(labels, key, "$.category_labels")
    sensitive = r.obj(config["sensitive_terms"], "$.sensitive_terms")
    if not sensitive:
        raise r.fail("$.sensitive_terms", "must name at least one category")
    for category in sorted(sensitive):
        if category not in labels:
            raise r.fail("$.sensitive_terms." + category, "needs an entry in $.category_labels")
        term_list(r, sensitive, category, "$.sensitive_terms")
    judgement = r.obj(config["judgement_terms"], "$.judgement_terms")
    if sorted(judgement) != sorted(JUDGEMENT_SOURCES):
        raise r.fail("$.judgement_terms", "must have exactly the keys {}".format(", ".join(JUDGEMENT_SOURCES)))
    for key in JUDGEMENT_SOURCES:
        term_list(r, judgement, key, "$.judgement_terms")
    work_nouns = term_list(r, config, "work_modifier_nouns", "$")
    fuzzy = {}
    for key in ["fuzzy_terms", "fuzzy_ignore"]:
        values = r.lst(config, key, "$", required=True)
        if not all(isinstance(v, str) and re.fullmatch(r"[a-z]{4,30}", v) for v in values):
            raise r.fail("$." + key, "must be a list of lower-case words (a-z, 4 to 30 letters)")
        fuzzy[key] = values
    person_terms = r.obj(config["person_terms"], "$.person_terms")
    for category in sorted(person_terms):
        if category not in labels:
            raise r.fail("$.person_terms." + category, "needs an entry in $.category_labels")
        term_list(r, person_terms, category, "$.person_terms")
    concepts = term_list(r, config, "separation_concepts", "$")
    non_person = r.lst(config, "non_person_words", "$", required=True)
    if not all(isinstance(v, str) and re.fullmatch(r"[A-Z][A-Za-z]{1,30}", v) for v in non_person):
        raise r.fail("$.non_person_words", "must be a list of capitalised words (A-Z then letters)")
    config["_scanner"] = Scanner(sensitive, judgement, r, work_nouns=work_nouns, fuzzy=fuzzy, person_terms=person_terms,
                                 concepts=concepts, non_person=non_person)
    ECHO["scanner"] = config["_scanner"]
    return config


def term_list(r, parent, key, path):
    terms = r.lst(parent, key, path, required=True)
    for i, term in enumerate(terms):
        if not isinstance(term, str) or not term.strip():
            raise r.fail("{}.{}[{}]".format(path, key, i), "must be a non-empty pattern string")
    return terms


# --------------------------------------------------------------------------- bundle


def read_source(r, sources, key, providers, required):
    path = "$.sources"
    value = sources.get(key)
    if value is None:
        if required:
            raise r.fail("{}.{}".format(path, key), "is required ({provider, status, detail})")
        return {"provider": None, "status": "not_configured", "history_days": None}
    r.obj(value, "{}.{}".format(path, key))
    out = {"status": r.enum(value, "status", "{}.{}".format(path, key), SOURCE_STATUSES)}
    out["provider"] = r.enum(value, "provider", "{}.{}".format(path, key), providers) \
        if value.get("provider") is not None or out["status"] != "not_configured" else None
    r.text(value, "detail", "{}.{}".format(path, key), required=False, cap=300)  # validated, never printed
    out["history_days"] = r.integer(value, "history_days", "{}.{}".format(path, key), 1, 3650, required=False) \
        if key == "meetings" else None
    return out


def read_person(r, b, key):
    p = r.obj(b.get(key), "$." + key)
    person = {"name": r.text(p, "name", "$." + key, cap=80), "email": r.email(p, "email", "$." + key)}
    return person, p


def load_bundle(path, config):
    raw = load_json(path, "bundle")
    r = Reader("bundle {}".format(path))
    r.obj(raw, "$")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise r.fail("$.schema_version", "must be {}, got {}".format(SCHEMA_VERSION, shown(raw.get("schema_version"))))
    bad_key = key_paths(raw, config["_forbidden"])
    if bad_key:
        raise r.fail(bad_key, "this skill never rates people or records mood, sentiment or health; remove this key "
                     "(a prep lists what was said and agreed, with quotes)")
    b = {"r": r}
    b["user"], u = read_person(r, raw, "user")
    b["user"]["role"] = r.enum(u, "role", "$.user", ROLES)
    b["counterpart"], _ = read_person(r, raw, "counterpart")
    if b["user"]["email"] == b["counterpart"]["email"]:
        raise r.fail("$.counterpart.email", "is the same as $.user.email; a 1:1 needs two people")
    series = r.obj(raw.get("series"), "$.series")
    b["match"] = r.enum(series, "match", "$.series", MATCH_MODES, default="title_and_attendee")
    titles = r.lst(series, "titles", "$.series", required=b["match"] == "title_and_attendee", cap=20)
    b["titles"] = []
    for i, _ in enumerate(titles):
        b["titles"].append(title_key(r.text({"t": titles[i]}, "t", "$.series.titles[{}]".format(i), cap=200)))
    if b["match"] == "title_and_attendee" and not b["titles"]:
        raise r.fail("$.series.titles", "needs at least one title when match is title_and_attendee")
    up = r.obj(raw.get("upcoming"), "$.upcoming", required=False)
    b["upcoming"] = None
    if up is not None:
        b["upcoming"] = {"start": r.moment(up, "start", "$.upcoming"), "url": r.url(up, "url", "$.upcoming")}
        r.text(up, "title", "$.upcoming", required=False, cap=200)  # validated, never printed
        r.ident(up, "event_id", "$.upcoming", required=False)
    sources = r.obj(raw.get("sources"), "$.sources")
    unknown = sorted(set(sources) - {"meetings", "transcripts", "issues", "slack"})
    if unknown:
        raise r.fail("$.sources", "unknown source key(s): {} (use meetings, transcripts, issues, slack)".format(
            ", ".join(shown(k) for k in unknown)))
    b["sources"] = {"meetings": read_source(r, sources, "meetings", MEETING_PROVIDERS, True),
                    "transcripts": read_source(r, sources, "transcripts", MEETING_PROVIDERS, False),
                    "issues": read_source(r, sources, "issues", ISSUE_PROVIDERS, False),
                    "slack": read_source(r, sources, "slack", SLACK_PROVIDERS, False)}
    b["meetings"] = []
    seen = set()
    for i, m in enumerate(r.lst(raw, "meetings", "$", required=True, cap=config["max_meetings"])):
        p = "$.meetings[{}]".format(i)
        r.obj(m, p)
        mid = r.ident(m, "id", p)
        if mid in seen:
            raise r.fail(p + ".id", "duplicate meeting id {}".format(shown(mid)))
        seen.add(mid)
        start = r.moment(m, "start", p)
        attendees = []
        for j, _ in enumerate(r.lst(m, "attendee_emails", p, required=True, cap=200)):
            attendees.append(r.email(m["attendee_emails"], j, p + ".attendee_emails"))
        text = m.get("text")
        if text is not None and not isinstance(text, str):
            raise r.fail(p + ".text", "must be a string (the notes/transcript text read), got {}".format(
                type_name(text)))
        if isinstance(text, str) and len(text) > 2000000:
            raise r.fail(p + ".text", "is over 2,000,000 characters")
        b["meetings"].append({
            "id": mid, "title": r.text(m, "title", p, cap=300), "start": start, "url": r.url(m, "url", p),
            "attendees": attendees, "notes": r.enum(m, "notes", p, NOTES_STATES, default="ok"),
            "transcript": r.enum(m, "transcript", p, TRANSCRIPT_STATES, default="not_requested"),
            "notes_owner": r.enum(m, "notes_owner", p, NOTE_OWNERS, default="user"),
            "text": text if isinstance(text, str) else None})
    b["items"] = []
    ids = set()
    for i, it in enumerate(r.lst(raw, "items", "$", cap=config["max_items"])):
        b["items"].append(read_item(r, it, "$.items[{}]".format(i), config, ids))
    b["work_items"] = []
    for i, w in enumerate(r.lst(raw, "work_items", "$", cap=config["max_work_items"])):
        p = "$.work_items[{}]".format(i)
        r.obj(w, p)
        b["work_items"].append({
            "source": r.enum(w, "source", p, ISSUE_PROVIDERS), "ref": r.ident(w, "ref", p),
            "topic": read_topic(r, w, p),
            "title": r.text(w, "title", p, cap=300), "state": r.enum(w, "state", p, WORK_STATES),
            "status": r.text(w, "status", p, required=False, cap=60), "updated": r.day(w, "updated", p),
            "url": r.url(w, "url", p)})
    b["withheld_by_agent"] = r.integer(raw, "withheld_by_agent", "$", 0, 1000, required=False) or 0
    b["dropped_links"] = r.dropped_links
    return b


def read_topic(r, obj, p):
    """The agent's work topic. Missing (or null) is allowed and means "uncategorised": the item is withheld."""
    topic = obj.get("topic")
    if topic is None:
        return None
    if not isinstance(topic, str) or topic not in TOPICS + BLOCKED_TOPICS:
        raise r.fail(p + ".topic", "must be one of {} (or {}, which are withheld), got {}".format(
            ", ".join(TOPICS), " / ".join(BLOCKED_TOPICS), shown(topic)))
    return topic


def read_item(r, it, p, config, ids):
    r.obj(it, p)
    item = {"id": r.ident(it, "id", p), "path": p}
    if item["id"] in ids:
        raise r.fail(p + ".id", "duplicate item id {}".format(shown(item["id"])))
    ids.add(item["id"])
    item["kind"] = r.enum(it, "kind", p, KINDS)
    item["topic"] = read_topic(r, it, p)
    item["owner"] = r.enum(it, "owner", p, SIDES)
    item["summary"] = r.text(it, "summary", p, cap=config["max_summary_chars"])
    key = it.get("key")
    if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", key)):
        raise r.fail(p + ".key", "must be a short lower-case slug like 'q4-forecast', got {}".format(shown(key)))
    item["key"] = key
    item["meeting_id"] = r.ident(it, "meeting_id", p)
    item["quote"] = r.text(it, "quote", p, cap=5000)
    item["quote_from"] = r.enum(it, "quote_from", p, QUOTE_FROM)
    item["speaker"] = r.enum(it, "speaker", p, SPEAKERS, default="unknown")
    item["status"] = r.enum(it, "status", p, STATUSES, default="open")
    item["due"] = r.day(it, "due", p, required=False)
    item["due_phrase"] = r.text(it, "due_phrase", p, required=False, cap=60)
    item["evidence"] = []
    for j, ev in enumerate(r.lst(it, "evidence", p, cap=20)):
        q = "{}.evidence[{}]".format(p, j)
        r.obj(ev, q)
        e = {"source": r.enum(ev, "source", q, EVIDENCE_SOURCES), "summary": r.text(ev, "summary", q, cap=200),
             "date": r.day(ev, "date", q), "url": r.url(ev, "url", q),
             "meeting_id": r.ident(ev, "meeting_id", q, required=False), "ref": r.ident(ev, "ref", q, required=False)}
        if e["source"] == "granola" and e["meeting_id"] is None:
            raise r.fail(q + ".meeting_id", "is required when source is granola (the 1:1 where it was confirmed)")
        if e["source"] in ("linear", "jira", "slack") and e["url"] is None and e["ref"] is None:
            raise r.fail(q, "needs a url or ref when source is {}".format(e["source"]))
        item["evidence"].append(e)
    return item


def title_key(title):
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", title).casefold().replace("_", " ")).split())


def quote_key(text):
    """Form used to spot repeated quotes (granola_text's normalised form, case-folded)."""
    return gt.normalise(text).casefold()


def quote_ok(item, meeting):
    """granola_text's whole-sentence rule against the meeting's text. Without text, the quote is checked against
    itself, which still rejects speaker labels, ellipses and quotes under 3 words."""
    source = meeting["text"] if meeting["text"] is not None else item["quote"]
    return gt.check_quote(item["quote"], source)[0]


STOPWORDS = {"the", "a", "an", "to", "for", "of", "our", "your", "their", "my", "his", "her", "its", "them", "him",
             "us", "me", "and", "with", "on", "by", "this", "that", "we", "i", "you", "will", "please", "just", "about"}


def summary_key(text):
    words = [w for w in re.findall(r"[^\W_]+", normalise(text)) if w not in STOPWORDS]
    return " ".join(words)


# --------------------------------------------------------------------------- meetings


def other_attendees(m, b, config):
    pair = {b["user"]["email"], b["counterpart"]["email"]}
    count = 0
    for email in set(m["attendees"]):
        domain = email.rsplit("@", 1)[1]
        if email in pair or any(domain == d or domain.endswith("." + d) for d in config["ignored_attendee_domains"]):
            continue
        count += 1
    return count


def select_meetings(b, config, as_of, want):
    excluded = {"after_as_of": 0, "not_both_attendees": 0, "other_attendees": 0, "other_title": 0,
                "older_than_window": 0, "beyond_count": 0}
    keep = []
    oldest = as_of.date() - timedelta(days=config["lookback_days"])
    for m in b["meetings"]:
        if m["start"] >= as_of:
            excluded["after_as_of"] += 1
        elif b["user"]["email"] not in m["attendees"] or b["counterpart"]["email"] not in m["attendees"]:
            excluded["not_both_attendees"] += 1
        elif other_attendees(m, b, config) > config["max_other_attendees"]:
            excluded["other_attendees"] += 1
        elif b["match"] == "title_and_attendee" and title_key(m["title"]) not in b["titles"]:
            excluded["other_title"] += 1
        elif local_day(m["start"]) < oldest:
            excluded["older_than_window"] += 1
        else:
            keep.append(m)
    keep.sort(key=lambda m: (m["start"], m["id"]), reverse=True)
    excluded["beyond_count"] = max(0, len(keep) - want)
    keep = keep[:want]
    keep.reverse()  # oldest first
    return keep, excluded


def local_day(moment):
    return moment.date()


# --------------------------------------------------------------------------- due dates


WEEKDAY_RE = (r"(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|"
              r"sat(?:urday)?|sun(?:day)?)")
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10}
NEXT_ONE_ON_ONE = re.compile(r"(?:the |our |my |your )?(?:next|following) (?:1 ?: ?1|1 ?on ?1|1 ?- ?1|one on one|"
                             r"one-on-one|1on1|sync|check in|check-in|catch up|catch-up|chat|meeting|session|time)"
                             r"(?: together)?")


def weekday_index(word):
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(word[:3])


def clean_phrase(phrase):
    p = unicodedata.normalize("NFKC", phrase).casefold()
    p = re.sub(r"[\"(),.!?;]", " ", p)
    p = " ".join(p.split())
    leading = ["no later than", "at the latest", "by the", "by", "before", "until", "till", "on", "due", "for",
               "around", "sometime", "some time", "latest"]
    changed = True
    while changed:
        changed = False
        for word in leading:
            if p.startswith(word + " "):
                p = p[len(word) + 1:]
                changed = True
    for word in [" at the latest", " latest"]:
        if p.endswith(word):
            p = p[:-len(word)]
    return p.strip()


def month_end(year, month):
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def resolve_due(phrase, said_on, later_meetings, upcoming_day):
    """(date or None, note). Calendar days; 'by Friday' = the next Friday after the day it was said."""
    p = clean_phrase(phrase)
    d = said_on
    if re.fullmatch(r"today|tonight|end of (?:the )?day|eod|cob|close of business|end of business", p):
        return d, None
    if p == "tomorrow":
        return d + timedelta(days=1), None
    m = re.fullmatch(r"(?:this )?" + WEEKDAY_RE, p)
    if m:
        delta = (weekday_index(m.group(1)) - d.weekday()) % 7
        if delta == 0:
            return None, "said on a {}, so the week is unclear".format(WEEKDAYS[d.weekday()])
        return d + timedelta(days=delta), None
    m = re.fullmatch(r"next " + WEEKDAY_RE, p)
    if m:
        monday = d - timedelta(days=d.weekday()) + timedelta(days=7)
        return monday + timedelta(days=weekday_index(m.group(1))), None
    if re.fullmatch(r"(?:the )?(?:end of (?:the |this )?week|eow|this week|later this week|week'?s end)", p):
        friday = d - timedelta(days=d.weekday()) + timedelta(days=4)
        return (friday if friday >= d else friday + timedelta(days=7)), None
    if re.fullmatch(r"next week|end of next week|the end of next week", p):
        return d - timedelta(days=d.weekday()) + timedelta(days=11), None
    if re.fullmatch(r"early next week|beginning of next week|start of next week", p):
        return d - timedelta(days=d.weekday()) + timedelta(days=8), None
    m = re.fullmatch(r"(?:in|within) ([0-9]{1,12}|a|an|one|two|three|four|five|six|seven|eight|nine|ten) "
                     r"(day|days|week|weeks)(?: time)?", p)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBER_WORDS[m.group(1)]
        days = n * (7 if m.group(2).startswith("week") else 1)
        if days > MAX_DUE_DAYS:
            return None, "more than {} days out".format(MAX_DUE_DAYS)
        target = d + timedelta(days=days)
        return (target, None) if target.year <= MAX_YEAR else (None, "past the supported years")
    if re.fullmatch(r"(?:the )?(?:end of (?:the |this )?month|eom|this month|month end|month-end)", p):
        return month_end(d.year, d.month), None
    if re.fullmatch(r"next month|end of next month|the end of next month", p):
        year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return month_end(year, month), None
    if re.fullmatch(r"(?:the )?(?:end of (?:the |this )?quarter|eoq|this quarter|quarter end|quarter-end)", p):
        q_month = ((d.month - 1) // 3) * 3 + 3
        return month_end(d.year, q_month), None
    if NEXT_ONE_ON_ONE.fullmatch(p):
        if later_meetings:
            return later_meetings[0], None
        if upcoming_day is not None and upcoming_day > d:
            return upcoming_day, None
        return None, "no later 1:1 found"
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", p)
    if m:
        day = parse_day(p)
        return (day, None) if day is not None else (None, "not a real date")
    m = (re.fullmatch(r"(?:" + WEEKDAY_RE + r" )?([a-z]+) ([0-9]{1,2})(?:st|nd|rd|th)?", p)
         or re.fullmatch(r"(?:" + WEEKDAY_RE + r" )?(?:the )?([0-9]{1,2})(?:st|nd|rd|th)? (?:of )?([a-z]+)", p))
    if m:
        a, c = m.group(2), m.group(3)
        month_word, day_word = (a, c) if not a.isdigit() else (c, a)
        month = next((i + 1 for i, name in enumerate(MONTH_NAMES)
                      if len(month_word) >= 3 and name.startswith(month_word)), None)
        if month is not None:
            for year in (d.year, d.year + 1):
                try:
                    candidate = date(year, month, int(day_word))
                except ValueError:
                    return None, "not a real date"
                if candidate >= d and candidate.year <= MAX_YEAR:
                    return candidate, None
    return None, "phrase not recognised"


# --------------------------------------------------------------------------- analysis


RANK = {"open": 0, "likely_done": 1, "done": 2}


def manager_side(b):
    return "user" if b["user"]["role"] == "manager" else "counterpart"


def report_side(b):
    return "counterpart" if b["user"]["role"] == "manager" else "user"


def analyse(b, config, as_of, want):
    meetings, excluded = select_meetings(b, config, as_of, want)
    by_id = dict((m["id"], m) for m in meetings)
    all_ids = set(m["id"] for m in b["meetings"])
    as_of_day = as_of.date()
    upcoming_day = b["upcoming"]["start"].date() if b["upcoming"] else None
    scanner = config["_scanner"].with_names([b["user"]["name"], b["counterpart"]["name"]])
    dropped = {"outside_meetings": 0, "unknown_meeting": 0, "quote_not_found": 0, "duplicate": 0}
    for it in b["items"]:
        if it["kind"] == "feedback" and it["owner"] != manager_side(b):
            raise b["r"].fail(it["path"] + ".owner", "feedback items are what the manager promised to give or "
                              "follow up on, so owner must be {!r} here".format(manager_side(b)))
        for e in it["evidence"]:
            if e["meeting_id"] is not None and e["meeting_id"] not in all_ids:
                raise b["r"].fail(it["path"] + ".evidence", "cites meeting {} which is not in $.meetings".format(
                    shown(e["meeting_id"])))
    groups = {}
    order = []
    seen_quotes = set()
    for it in b["items"]:
        if it["meeting_id"] not in all_ids:
            dropped["unknown_meeting"] += 1
            continue
        m = by_id.get(it["meeting_id"])
        if m is None:
            dropped["outside_meetings"] += 1
            continue
        if it["quote_from"] != "summary" and not quote_ok(it, m):
            dropped["quote_not_found"] += 1
            continue
        gkey = (it["kind"], it["owner"], it["key"] or summary_key(it["summary"]) or it["id"])
        qkey = (gkey, it["meeting_id"], quote_key(it["quote"]).casefold())
        if qkey in seen_quotes:
            dropped["duplicate"] += 1
            continue
        seen_quotes.add(qkey)
        if gkey not in groups:
            groups[gkey] = []
            order.append(gkey)
        groups[gkey].append(dict(it, meeting=m))
    withheld, left_out, shown_items = [], [], []
    by_topic = 0
    for gkey in order:
        occ = sorted(groups[gkey], key=lambda o: (o["meeting"]["start"], o["id"]))
        # Gate 1, the allowlist: every occurrence needs one of the work topics. Nothing else is looked at.
        if any(o["topic"] not in TOPICS for o in occ):
            withheld.append([o["id"] for o in occ])
            by_topic += 1
            continue
        # Gate 2, the backstop: the pattern check on every text field.
        verdict = None
        for o in occ:
            for text, authored in [(o["summary"], True), (o["quote"], o["quote_from"] == "summary")] + \
                    [(e["summary"], True) for e in o["evidence"]] + [(e["ref"], True) for e in o["evidence"]] + \
                    [(o["due_phrase"], True)]:
                hit = scanner.check(text, authored)
                if hit == "personal":
                    verdict = "personal"
                    break
                if hit == "judgement" and verdict is None:
                    verdict = "judgement"
            if verdict == "personal":
                break
        ids = [o["id"] for o in occ]
        if verdict == "personal":
            withheld.append(ids)
            continue
        if verdict == "judgement":
            left_out.append(ids)
            continue
        shown_items.append(merge_group(occ, b, as_of_day, meetings, upcoming_day))
    work = []
    for w in b["work_items"]:
        if w["topic"] not in TOPICS:
            withheld.append([w["ref"]])
            by_topic += 1
            continue
        hit = scanner.check(w["title"], True) or scanner.check(w["status"], True) or scanner.check(w["ref"], True)
        if hit == "personal":
            withheld.append([w["ref"]])
        elif hit == "judgement":
            left_out.append([w["ref"]])
        else:
            work.append(w)
    return {"meetings": meetings, "excluded": excluded, "dropped": dropped, "items": shown_items,
            "withheld": withheld, "by_topic": by_topic, "left_out": left_out, "work": work, "as_of_day": as_of_day, "want": want}


def merge_group(occ, b, as_of_day, meetings, upcoming_day):
    first, latest = occ[0], occ[-1]
    raised_days = []
    for o in occ:
        d = local_day(o["meeting"]["start"])
        if d not in raised_days:
            raised_days.append(d)
    last_raised = raised_days[-1]
    status, note, best = "open", None, None
    evidence = []
    for o in occ:
        evidence.extend(o["evidence"])
    evidence.sort(key=lambda e: (e["date"], e["source"], e["summary"]))
    valid = [e for e in evidence if last_raised <= e["date"] <= as_of_day]
    claimed = max((o["status"] for o in occ), key=lambda s: RANK[s])
    if claimed != "open":
        if valid:
            status, best = claimed, valid[-1]
        else:
            note = "marked done, but no evidence dated on or after {}, when it last came up".format(
                short_day(last_raised, as_of_day))
    latest_evidence = evidence[-1] if evidence and evidence[-1]["date"] <= as_of_day else None
    due, due_note, due_from = None, None, None
    for o in reversed(occ):
        if o["due"] is not None:
            due, due_from = o["due"], None
            break
        if o["due_phrase"]:
            said = local_day(o["meeting"]["start"])
            later = [local_day(m["start"]) for m in meetings if local_day(m["start"]) > said]
            due, due_note = resolve_due(o["due_phrase"], said, later, upcoming_day)
            due_from = (o["due_phrase"], said)
            break
    overdue = (as_of_day - due).days if due is not None and status == "open" and due < as_of_day else 0
    return {"kind": first["kind"], "owner": first["owner"], "summary": latest["summary"], "first": first,
            "latest": latest, "raised_days": raised_days, "status": status, "status_note": note,
            "evidence": best, "latest_evidence": latest_evidence, "due": due, "due_note": due_note,
            "due_from": due_from, "overdue": overdue, "ids": [o["id"] for o in occ]}


# --------------------------------------------------------------------------- rendering


def short_day(d, as_of_day):
    text = "{} {}".format(MONTHS[d.month - 1], d.day)
    return text if d.year == as_of_day.year else "{}, {}".format(text, d.year)


def weekday_day(d, as_of_day):
    return "{} {}".format(WEEKDAYS[d.weekday()], short_day(d, as_of_day))


def offset_label(moment):
    offset = moment.utcoffset()
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "UTC{}{:02d}:{:02d}".format(sign, minutes // 60, minutes % 60)


def plural(n, word, many=None):
    return "{} {}".format(n, word if n == 1 else (many or word + "s"))


def first_name(name):
    return name.split()[0]


def link(label, url):
    return "[{}]({})".format(label, url) if url else label


class View:
    """Names and wording for one perspective (the user is the manager or the report)."""

    def __init__(self, b):
        self.b = b
        self.them = md(first_name(b["counterpart"]["name"]))
        self.them_full = md(b["counterpart"]["name"])
        self.me = md(first_name(b["user"]["name"]))

    def who(self, side, possessive=False):
        if side == "user":
            return "your" if possessive else "you"
        return self.them + "'s" if possessive else self.them

    def Who(self, side, possessive=False):
        text = self.who(side, possessive)
        return text[0].upper() + text[1:]


def cite(o, view, as_of_day):
    m = o["meeting"]
    when = short_day(local_day(m["start"]), as_of_day)
    text = gt.shorten(redact(o["quote"]), view.max_quote)  # contact details removed before shortening
    ref = link("notes", m["url"])
    if o["quote_from"] == "summary":
        return "AI summary, {}: {} · {}".format(when, md(text), ref)
    speaker = {"user": "you", "counterpart": view.them, "unknown": None}[o["speaker"]]
    owner = o["meeting"]["notes_owner"]
    source = "transcript" if o["quote_from"] == "transcript" else \
        {"user": "your notes", "counterpart": view.them + "'s notes", "unknown": "notes, author unknown"}[owner]
    who = "{}, {} ({})".format(speaker, when, source) if speaker else "{} ({})".format(when, source)
    return "“{}” — {} · {}".format(md(text), who, ref)


def evidence_text(e, as_of_day):
    label = LABELS[e["source"]]
    when = short_day(e["date"], as_of_day)
    if e["source"] == "user":
        return "{} ({}): {}".format(label, when, md(e["summary"]))
    link_label = md(e["ref"]) if e["ref"] else {"granola": "notes", "slack": "thread"}.get(e["source"], "issue")
    tail = " · " + link(link_label, e["url"]) if e["url"] else (" · " + link_label if e["ref"] else "")
    return "{}, {}: {}{}".format(label, when, md(e["summary"]), tail)


def due_text(it, as_of_day):
    if it["due"] is None:
        if it["due_from"]:
            return "due “{}” (no date: {})".format(md(it["due_from"][0]), it["due_note"])
        return None
    text = "due {}".format(weekday_day(it["due"], as_of_day))
    if it["due_from"]:
        text += " (from “{}”, said {})".format(md(it["due_from"][0]), weekday_day(it["due_from"][1], as_of_day))
    return text


def raised_text(it, as_of_day, total):
    days = it["raised_days"]
    if len(days) == 1:
        return "raised {}".format(short_day(days[0], as_of_day))
    return "came up in {} of {} 1:1s ({})".format(len(days), total, ", ".join(short_day(d, as_of_day) for d in days))


def item_line(it, view, as_of_day, total):
    flag = WARN + " " if it["overdue"] else ""
    head = "{}**{}**".format(flag, md(it["summary"]))
    status = it["status"]
    if status == "likely_done":
        state = "likely done — confirm: " + evidence_text(it["evidence"], as_of_day)
    elif status == "done":
        state = "done: " + evidence_text(it["evidence"], as_of_day)
    else:
        state = "open"
        due = due_text(it, as_of_day)
        if due:
            state += ", " + due
        if it["overdue"]:
            state += ", {} overdue".format(plural(it["overdue"], "day"))
        elif it["due"] is not None and it["due"] == as_of_day:
            state += " (today)"
        if it["status_note"]:
            state += " ({})".format(it["status_note"])
    lines = ["- {} — {} · {}".format(head, state, raised_text(it, as_of_day, total))]
    lines.append("  - " + cite(it["first"], view, as_of_day))
    if it["latest"] is not it["first"] and it["latest"]["quote"] != it["first"]["quote"]:
        lines.append("  - Latest: " + cite(it["latest"], view, as_of_day))
    if it["kind"] in ("goal", "topic") and status == "open" and it["latest_evidence"] is not None:
        lines.append("  - Latest status: " + evidence_text(it["latest_evidence"], as_of_day))
    return "\n".join(lines)


def sort_key(it):
    due = it["due"].toordinal() if it["due"] is not None else 10 ** 7
    return (-it["overdue"], RANK[it["status"]], due, it["raised_days"][0].toordinal(), it["summary"].casefold(),
            it["ids"][0])


def section(title, items, view, a, config, lines):
    if not items:
        return
    total = len(a["meetings"])
    limit = config["max_per_section"]
    lines.append("")
    lines.append("**{}**".format(title))
    for it in items[:limit]:
        lines.append(item_line(it, view, a["as_of_day"], total))
    if len(items) > limit:
        lines.append("- +{} more (run with --json for the full list)".format(len(items) - limit))


def agenda(a, b, view, config):
    R, M = report_side(b), manager_side(b)
    items = [it for it in a["items"] if it["status"] != "done"]
    rec = config["recurring_min_meetings"]

    def pick(pred):
        out = [it for it in items if pred(it) and id(it) not in used]
        out.sort(key=sort_key)
        for it in out:
            used.add(id(it))
        return out

    used = set()
    lines = []
    whose = lambda it: view.who(it["owner"], possessive=True)
    owner_of = lambda it: "yours" if it["owner"] == "user" else view.them + "'s"
    for it in pick(lambda it: it["owner"] == R and it["kind"] == "topic" and len(it["raised_days"]) >= rec):
        lines.append("{} topic: {} (came up in {} 1:1s)".format(view.Who(R, True), md(it["summary"]),
                                                                  len(it["raised_days"])))
    for it in pick(lambda it: it["owner"] == R and it["kind"] == "question"):
        lines.append(("Follow up on your question: {}" if it["owner"] == "user" else "Answer {}'s question: {}".format(
            view.them, "{}")).format(md(it["summary"])))
    for it in pick(lambda it: it["owner"] == R and it["kind"] == "topic"):
        lines.append("{} topic: {}".format(view.Who(R, True), md(it["summary"])))
    for it in pick(lambda it: it["owner"] == R and it["kind"] == "goal"):
        lines.append("Check in on {} goal: {}".format(whose(it), md(it["summary"])))
    for it in pick(lambda it: it["kind"] == "feedback"):
        verb = "Give feedback promised to {}".format(view.them) if M == "user" else \
            "Ask {} for the feedback promised".format(view.them)
        lines.append("{}: {}".format(verb, md(it["summary"])))
    for it in pick(lambda it: it["kind"] == "commitment" and it["overdue"]):
        lines.append("Overdue ({}, {}): {}".format(owner_of(it), plural(it["overdue"], "day"), md(it["summary"])))
    for it in pick(lambda it: it["kind"] == "commitment" and it["status"] == "likely_done"):
        lines.append("Confirm done ({}): {}".format(owner_of(it), md(it["summary"])))
    for it in pick(lambda it: it["kind"] == "commitment"):
        lines.append("Check on {} commitment: {}".format(whose(it), md(it["summary"])))
    for it in pick(lambda it: True):
        label = {"question": "Ask", "topic": "Raise", "goal": "Check in on"}.get(it["kind"], "Discuss")
        lines.append("{} ({}): {}".format(label, owner_of(it), md(it["summary"])))
    limit = config["agenda_max"]
    out = ["{}. {}".format(i + 1, line) for i, line in enumerate(lines[:limit])]
    if len(lines) > limit:
        out.append("…and {} more in the sections below.".format(len(lines) - limit))
    return out


def source_word(src):
    return LABELS.get(src["provider"], src["provider"] or "") if src["provider"] else ""


def sources_line(b, a):
    s = b["sources"]
    parts = []
    mt = s["meetings"]
    label = source_word(mt) or "Meeting notes"
    if mt["status"] == "ok":
        extra = a["excluded"]["beyond_count"]
        parts.append("{} ✓ ({} read{})".format(label, plural(len(a["meetings"]), "1:1"), ", the newest {} of {} found"
                                               .format(len(a["meetings"]), len(a["meetings"]) + extra) if extra else ""))
    else:
        parts.append("{}: {}".format(label, STATUS_WORDS[mt["status"]]))
    tr = s["transcripts"]
    parts.append("Transcripts ✓" if tr["status"] == "ok" else "Transcripts: {}".format(STATUS_WORDS[tr["status"]]))
    iss = s["issues"]
    if iss["status"] == "ok":
        n_open = len([w for w in a["work"] if w["state"] in ("open", "in_progress")])
        parts.append("{} ✓ ({} open {})".format(source_word(iss), n_open, "issue" if n_open == 1 else "issues"))
    else:
        parts.append("{}: {}".format(source_word(iss) or "Issues (Linear/Jira)", STATUS_WORDS[iss["status"]]))
    sl = s["slack"]
    parts.append("Slack ✓" if sl["status"] == "ok" else "Slack: {}".format(STATUS_WORDS[sl["status"]]))
    return "Sources: " + " · ".join(parts)


def gaps(b, a, as_of_day):
    out = []
    mt = b["sources"]["meetings"]
    if mt["status"] != "ok":
        out.append("the meeting notes source was {}, so this prep has no 1:1 history".format(STATUS_WORDS[mt["status"]]))
    n = len(a["meetings"])
    if mt["status"] == "ok" and n < a["want"]:
        out.append("found {} of the {} 1:1s asked for in the last {} days".format(n, a["want"], a["lookback_days"]))
    if mt["history_days"] is not None:
        out.append("your notes plan shows only the last {} days, so older 1:1s may be missing".format(
            mt["history_days"]))
    missing = [m for m in a["meetings"] if m["notes"] != "ok"]
    if missing:
        out.append("notes not readable for the {} {}".format(", ".join(
            short_day(local_day(m["start"]), as_of_day) for m in missing), "1:1" if len(missing) == 1 else "1:1s"))
    if b["sources"]["transcripts"]["status"] in ("denied", "unavailable"):
        out.append("transcripts {} — quotes come from notes and summaries".format(
            STATUS_WORDS[b["sources"]["transcripts"]["status"]]))
    for key, what in [("issues", "open work and issue status not checked"),
                      ("slack", "Slack threads not checked")]:
        src = b["sources"][key]
        if src["status"] in ("denied", "unavailable"):
            out.append("{} {} — {}".format(source_word(src) or key, STATUS_WORDS[src["status"]], what))
    ex = a["excluded"]
    skipped = []
    if ex["other_attendees"]:
        skipped.append("{} with other people on the invite".format(plural(ex["other_attendees"], "meeting")))
    if ex["other_title"]:
        skipped.append("{} outside the series title".format(plural(ex["other_title"], "meeting")))
    if ex["not_both_attendees"]:
        skipped.append("{} without both of you listed".format(plural(ex["not_both_attendees"], "meeting")))
    if skipped:
        out.append("not read: " + ", ".join(skipped))
    d = a["dropped"]
    notes = []
    if d["outside_meetings"] or d["unknown_meeting"]:
        notes.append("{} citing a meeting outside the 1:1s read".format(
            plural(d["outside_meetings"] + d["unknown_meeting"], "item")))
    if d["quote_not_found"]:
        notes.append("{} whose quote isn't one or more whole sentences from one speaker's turn in its meeting's "
                     "text (or has a speaker label, an ellipsis or under 3 words)".format(
                         plural(d["quote_not_found"], "item")))
    if notes:
        out.append("not shown: " + ", ".join(notes))
    if b["dropped_links"]:
        out.append("{} dropped (not an http(s) URL)".format(plural(b["dropped_links"], "link")))
    return out


def render(b, a, config, as_of):
    view = View(b)
    view.max_quote = config["max_quote_chars"]
    as_of_day = a["as_of_day"]
    R, M = report_side(b), manager_side(b)
    lines = []
    head = "#### 1:1 prep: {}".format(view.them_full)
    if b["upcoming"]:
        st = b["upcoming"]["start"]
        head += " · {}, {:02d}:{:02d} ({})".format(weekday_day(st.date(), as_of_day), st.hour, st.minute,
                                                    offset_label(st))
    lines.append(head)
    lines.append("")
    lines.append("_Private prep for {} only — not posted or shared anywhere. Review before sharing any of it with "
                 "{}._".format(md(b["user"]["name"]), view.them))
    mt = b["sources"]["meetings"]
    lines.append("")
    if mt["status"] != "ok":
        lines.append("**{} Incomplete** — couldn't read your meeting notes ({}). Nothing below means \"nothing "
                     "open\"; check your notes before the 1:1.".format(WARN, STATUS_WORDS[mt["status"]]))
        lines.append("")
    meetings = a["meetings"]
    items = a["items"]
    visible = [it for it in items if it["status"] != "done"]
    done = [it for it in items if it["status"] == "done"]
    if meetings:
        span = short_day(local_day(meetings[0]["start"]), as_of_day)
        if len(meetings) > 1:
            span += " – " + short_day(local_day(meetings[-1]["start"]), as_of_day)
        mine = [it for it in visible if it["kind"] == "commitment" and it["owner"] == "user"]
        theirs = [it for it in visible if it["kind"] == "commitment" and it["owner"] == "counterpart"]
        counts = ["{} of yours{}".format(len(mine), " ({} overdue)".format(sum(1 for it in mine if it["overdue"]))
                                         if any(it["overdue"] for it in mine) else ""),
                  "{} of {}'s{}".format(len(theirs), view.them, " ({} overdue)".format(
                      sum(1 for it in theirs if it["overdue"])) if any(it["overdue"] for it in theirs) else "")]
        rec = [it for it in visible if it["kind"] == "topic" and len(it["raised_days"]) >= config["recurring_min_meetings"]]
        qs = [it for it in visible if it["kind"] == "question"]
        glance = "**At a glance** — read {} with {} ({}). Open commitments: {}.".format(
            plural(len(meetings), "1:1"), view.them, span, ", ".join(counts))
        extra = []
        if rec:
            extra.append(plural(len(rec), "topic keeps", "topics keep") + " coming back")
        if qs:
            extra.append(plural(len(qs), "question") + " not answered yet")
        if done:
            extra.append("{} done since raised (not listed)".format(plural(len(done), "item")))
        if extra:
            glance += " " + "; ".join(extra)[0].upper() + "; ".join(extra)[1:] + "."
        lines.append(glance)
        lines.append("")
        ag = agenda(a, b, view, config)
        lines.append("**Suggested agenda** ({})".format(
            "{}'s topics first — it's their meeting".format(view.them) if R == "counterpart"
            else "your topics first — it's your meeting"))
        lines.extend(ag if ag else ["Nothing carried over from the 1:1s read. Ask what's on {}'s mind.".format(
            view.them) if R == "counterpart" else "Nothing carried over from the 1:1s read. Bring what's on your mind."])
        for side in (["user", "counterpart"]):
            group = [it for it in visible if it["kind"] == "commitment" and it["owner"] == side]
            section("{} commitments".format(view.Who(side, True)), sorted(group, key=sort_key), view, a, config, lines)
        recurring = [it for it in visible if it["kind"] == "topic"
                     and len(it["raised_days"]) >= config["recurring_min_meetings"]]
        section("Keeps coming back (no resolution yet)", sorted(recurring, key=sort_key), view, a, config, lines)
        once = [it for it in visible if it["kind"] == "topic" and it not in recurring]
        section("Other open topics", sorted(once, key=sort_key), view, a, config, lines)
        goals = [it for it in visible if it["kind"] == "goal"]
        section("Goals and projects raised", sorted(goals, key=lambda it: (it["owner"] != R,) + sort_key(it)),
                view, a, config, lines)
        fb = [it for it in visible if it["kind"] == "feedback"]
        section("Feedback you promised {}".format(view.them) if M == "user"
                else "Feedback {} promised you".format(view.them), sorted(fb, key=sort_key), view, a, config, lines)
        qs_sorted = sorted(qs, key=lambda it: (it["owner"] != R,) + sort_key(it))
        section("Questions not answered yet", qs_sorted, view, a, config, lines)
        if not visible:
            lines.append("")
            lines.append("Nothing open found in the {} read.".format(plural(len(meetings), "1:1")))
    elif mt["status"] == "ok":
        lines.append("No previous 1:1s with {} found in the last {} days (matched on both of your e-mail "
                     "addresses{}). Nothing to carry over; ask what's on {}.".format(
                         view.them, a["lookback_days"], " and the series title" if b["match"] == "title_and_attendee"
                         else "", "their mind" if R == "counterpart" else "your mind"))
    open_work = [w for w in a["work"] if w["state"] in ("open", "in_progress")]
    if open_work:
        open_work.sort(key=lambda w: (-w["updated"].toordinal(), w["ref"]))
        lines.append("")
        lines.append("**Open work assigned to {}** ({})".format(view.them, " / ".join(
            sorted(set(LABELS[w["source"]] for w in open_work)))))
        for w in open_work[:config["max_per_section"]]:
            status = md(w["status"]) if w["status"] else w["state"].replace("_", " ")
            lines.append("- {} {} — {}, updated {}".format(link(md(w["ref"]), w["url"]), md(w["title"]), status,
                                                          short_day(w["updated"], as_of_day)))
        if len(open_work) > config["max_per_section"]:
            lines.append("- +{} more (run with --json for the full list)".format(
                len(open_work) - config["max_per_section"]))
    lines.append("")
    withheld = len(a["withheld"]) + b["withheld_by_agent"]
    if withheld:
        lines.append("Withheld: {} withheld as personal or uncategorised (not shown; check your own notes if you "
                     "need it).".format(
            plural(withheld, "item")))
    if a["left_out"]:
        lines.append("Left out: {} worded as a rating or judgement of a person, or their mood (not shown).".format(
            plural(len(a["left_out"]), "item")))
    lines.append(sources_line(b, a))
    g = gaps(b, a, as_of_day)
    lines.append("Gaps: " + ("; ".join(g) + "." if g else "none."))
    lines.append("As of {} · {}".format(as_of.isoformat(), "you are " + ("the manager" if b["user"]["role"] ==
                                                                          "manager" else "the report")))
    lines.append("_Not a performance review. A private draft: only items filed under a work topic are shown, and a "
                 "pattern-based check that can miss wording leaves out personal, rating, judgement and mood wording. "
                 "Read it before sharing any of it._")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- JSON


def json_occurrence(o):
    return {"item_id": o["id"], "meeting_id": o["meeting"]["id"], "date": local_day(o["meeting"]["start"]).isoformat(),
            "url": o["meeting"]["url"], "quote": redact(o["quote"]), "quote_from": o["quote_from"],
            "speaker": o["speaker"]}


def json_evidence(e):
    if e is None:
        return None
    return {"source": e["source"], "summary": redact(e["summary"]), "date": e["date"].isoformat(), "url": e["url"],
            "ref": e["ref"], "meeting_id": e["meeting_id"]}


def to_json(b, a, config, as_of, markdown):
    items = []
    for it in a["items"]:
        items.append({"kind": it["kind"], "topic": it["latest"]["topic"], "owner": it["owner"],
                      "summary": redact(it["summary"]),
                      "status": it["status"], "status_note": it["status_note"],
                      "due": it["due"].isoformat() if it["due"] else None,
                      "due_phrase": redact(it["due_from"][0]) if it["due_from"] else None, "due_note": it["due_note"],
                      "overdue_days": it["overdue"], "raised": [d.isoformat() for d in it["raised_days"]],
                      "recurring": it["kind"] == "topic" and len(it["raised_days"]) >= config["recurring_min_meetings"],
                      "first": json_occurrence(it["first"]), "latest": json_occurrence(it["latest"]),
                      "evidence": json_evidence(it["evidence"]), "latest_evidence": json_evidence(it["latest_evidence"]),
                      "item_ids": it["ids"]})
    out = {"schema_version": SCHEMA_VERSION, "as_of": as_of.isoformat(), "user_role": b["user"]["role"],
           "counterpart": redact(b["counterpart"]["name"]),
           "meetings_read": [{"id": m["id"], "date": local_day(m["start"]).isoformat(), "url": m["url"],
                              "notes": m["notes"], "transcript": m["transcript"]} for m in a["meetings"]],
           "excluded_meetings": a["excluded"], "dropped_items": a["dropped"], "dropped_links": b["dropped_links"],
           "items": items, "agenda": agenda(a, b, View(b), config),
           "work_items": [{"source": w["source"], "ref": w["ref"], "topic": w["topic"], "title": redact(w["title"]),
                           "state": w["state"],
                           "status": redact(w["status"]), "updated": w["updated"].isoformat(), "url": w["url"]}
                          for w in a["work"]],
           "withheld_personal": {"count": len(a["withheld"]) + b["withheld_by_agent"], "item_ids": [
                                     [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in group]
                                     for group in a["withheld"]],
                                 "by_agent": b["withheld_by_agent"], "by_topic": a["by_topic"],
                                 "by_pattern": len(a["withheld"]) - a["by_topic"]},
           "left_out_judgement": {"count": len(a["left_out"]), "item_ids": [
               [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in group] for group in a["left_out"]]},
           "sources": {k: {"provider": v["provider"], "status": v["status"]} for k, v in sorted(b["sources"].items())},
           "gaps": gaps(b, a, a["as_of_day"]), "markdown": markdown}
    return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------- main


def build(args):
    config = load_config(args.config)
    as_of = parse_moment(args.as_of) if args.as_of else None
    if as_of is None:
        raise InputError("--as-of is required: an ISO-8601 datetime with an offset, like 2026-10-13T09:30:00-07:00 "
                         "(the run time; the only 'now' the script uses)")
    want = config["lookback_meetings"] if args.meetings is None else args.meetings
    if not 1 <= want <= 12:
        raise InputError("--meetings must be from 1 to 12, got {}".format(want))
    if not args.bundle:
        raise InputError("--bundle is required (see DATA_CONTRACT.md)")
    b = load_bundle(args.bundle, config)
    a = analyse(b, config, as_of, want)
    a["lookback_days"] = config["lookback_days"]
    markdown = render(b, a, config, as_of)
    return to_json(b, a, config, as_of, markdown) if args.json else markdown


def say_error(message):
    try:
        if sys.stderr is not None:
            sys.stderr.write("error: {}\n".format(message))
    except (OSError, ValueError):
        pass


def emit(text):
    try:
        if sys.stdout is None:
            raise OSError("stdout is closed")
        sys.stdout.write(text)
        sys.stdout.flush()
        return 0
    except (OSError, ValueError) as exc:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, AttributeError, ValueError):
            pass
        say_error("the output could not be written ({})".format(getattr(exc, "strerror", None) or exc))
        return EXIT_OUTPUT


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("{} (see --help)".format(message))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    ap = Parser(description="Render a private 1:1 prep doc from a bundle of previous 1:1s.")
    ap.add_argument("--bundle", help="the bundle (JSON); see DATA_CONTRACT.md")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="thresholds and pattern lists (default: prep.json)")
    ap.add_argument("--as-of", help="run time, ISO-8601 with offset (the only 'now' the script uses)")
    ap.add_argument("--meetings", type=int, help="how many previous 1:1s to read (1-12; default from prep.json)")
    ap.add_argument("--json", action="store_true", help="emit structured JSON (with the markdown inside)")
    try:
        args = ap.parse_args(argv)
        output = build(args)
    except InputError as exc:
        say_error(exc)
        return EXIT_INPUT
    except SystemExit as exc:  # --help
        return exc.code if isinstance(exc.code, int) else 0
    return emit(output)


if __name__ == "__main__":
    sys.exit(main())
