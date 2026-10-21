#!/usr/bin/env python3
"""Render a user research synthesis for one study from one normalised JSON bundle.

Stdlib only, offline, deterministic, Python 3.8+. --as-of is the only "now". The script never reads the clock,
never calls an API and never writes a file: it prints one report to stdout.

What the script does (the agent does the reading, extraction and coding):
  * validates the bundle (see DATA_CONTRACT.md) and refuses bad input with exit 2: every finding cites evidence,
    every piece of evidence cites a participant and a meeting in the bundle, and the participant must be listed on
    that meeting; when the meeting's transcript or notes text is in the bundle, each quote (and interviewer question)
    must be one complete sentence, or a run of consecutive complete sentences, from a single speaker turn of it
    (granola_text.check_quote: mechanical, ignores case and formatting; speaker turns found heuristically). Ellipses,
    speaker labels and quotes under 3 words are rejected with or without source text
  * applies consent: `declined` participants are dropped entirely; only `granted` participants are quoted
  * counts DISTINCT participants supporting and contradicting each finding and computes a confidence band with the
    thresholds in synthesis.json; answers to leading questions count as weaker evidence; a finding supported by
    fewer than min_pattern_participants participants is never shown as a pattern; statements whose wording states a
    count or share ('most admins', '4 out of 5', '3/5', 'nearly everyone') are refused (a wording heuristic)
  * compares support between participant segments, ranks opportunities by the evidence behind them, and lists
    what the study still does not show
  * pseudonymises: participants appear only as P1..Pn and meetings as M1..Mn; names, organisations and emails the
    bundle lists, plus email addresses, phone numbers (granola_text.redact_contacts, before any shortening) and web
    addresses found by pattern, are replaced in every displayed field. Pattern-based: anything nobody listed and no
    pattern matches can slip through.
  * escapes every displayed value for markdown (granola_text.md) and CSV (formula guard); links are built only
    from http(s) URLs the script validated

Exit codes: 0 report printed; 1 stdout was closed or the pipe broke; 2 bad input, config or arguments (nothing on
stdout).
"""

import argparse
import csv
import importlib.util
import io
import json
import math
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
# granola_text.py is the shared quote-check, contact-redaction and escaping module, shipped byte-for-byte next to this
# script. Load it from this folder whatever the working directory, and keep the folder flat (no __pycache__).
sys.dont_write_bytecode = True
_GT_SPEC = importlib.util.spec_from_file_location("granola_text", str(SCRIPT_DIR / "granola_text.py"))
gt = importlib.util.module_from_spec(_GT_SPEC)
_GT_SPEC.loader.exec_module(gt)
DEFAULT_CONFIG = SCRIPT_DIR / "synthesis.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCE_KEYS = ["meetings", "transcripts"]
SOURCE_STATUSES = ["ok", "partial", "unavailable", "denied", "not_connected", "not_configured", "not_requested"]
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "manual": "pasted or uploaded transcripts"}
CONSENTS = ["granted", "no_quotes", "unknown", "declined"]
CONSENT_LABELS = {"granted": "quotes OK", "no_quotes": "no quotes", "unknown": "unknown, so no quotes"}
TEXT_STATUSES = ["ok", "denied", "unavailable", "not_requested"]
EVIDENCE_KINDS = ["said", "did"]
EVIDENCE_SOURCES = ["transcript", "notes"]
FINDING_TYPES = ["finding", "job", "pain", "workaround", "desired_outcome"]
TYPE_TITLES = {"job": "Jobs to be done", "pain": "Pains", "workaround": "Current workarounds",
               "desired_outcome": "Desired outcomes"}
METHODS = ["interviews", "usability_tests", "mixed"]
METHOD_LABELS = {"interviews": "interviews", "usability_tests": "usability tests", "mixed": "interviews and tests"}
FORMATS = ["markdown", "json", "csv"]
BANDS = ["high", "medium", "low", "contested", "single"]
BAND_LABELS = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence",
               "contested": "Contested", "single": "Single participant (not a pattern)"}
DATE_PRECISIONS = ["day", "month", "none"]
CONFIG_KEYS = ["min_pattern_participants", "medium_min_participants", "high_min_participants",
               "high_min_share_of_participants", "high_max_contradicting_ratio", "segment_min_participants",
               "segment_gap_min_share", "max_quotes_per_finding", "max_contradicting_quotes", "max_quote_chars",
               "max_statement_chars", "max_single_signals", "date_precision", "leading_question_patterns",
               "free_email_domains"]

PARTICIPANT_RE = re.compile(r"^P[1-9][0-9]{0,2}$")
RQ_RE = re.compile(r"^RQ[1-9][0-9]{0,2}$")
FINDING_RE = re.compile(r"^F[1-9][0-9]{0,3}$")
EVIDENCE_RE = re.compile(r"^E[1-9][0-9]{0,4}$")
OPPORTUNITY_RE = re.compile(r"^O[1-9][0-9]{0,3}$")
MEETING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RECORDER_ID_RE = re.compile(r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|"
                            r"not_[A-Za-z0-9]{14})$")
RECORDER_ID_IN_TEXT_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|"
                                    r"not_[A-Za-z0-9]{14}")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
DATETIME_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,6}))?)?"
                         r"(Z|[+-][0-9]{2}:[0-9]{2})$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]{1,5})?$")
# Web addresses: scheme:// or www. links, and bare domains ending in a common top-level domain.
WEB_LINK_RE = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]{1,15}://|www\.)\S+")
WEB_DOMAIN_RE = re.compile(r"(?i)(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                           r"(?:com|net|org|io|ai|co|app|dev|example|info|biz|xyz|tech|cloud|so|ly|gg|tv|me|us|uk|ca|"
                           r"au|de|fr|nl|se|ch|es|ie|eu|nz|in|jp)(?:/[^\s\[\]()<>\"']*)?(?![\w-])")
URL_SAFE = "-._~!$&'*+,;=:@/?#%"
BIDI_CONTROLS = set(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)) + [0x200E, 0x200F])
INVISIBLE = set(chr(c) for c in [0x00AD, 0x180E, 0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF])
ASCII_JOINER_RE = re.compile(r"(?<=[\x00-\x7f])[\u200c\u200d]+|[\u200c\u200d]+(?=[\x00-\x7f])")
QUOTE_PAIRS = [("\u201c", "\u201d"), ('"', '"'), ("\u2018", "\u2019"), ("'", "'"), ("\u00ab", "\u00bb")]
# Apostrophes and dashes inside a listed identifier match any of their typographic forms when redacting.
_APOSTROPHES = "'\u2018\u2019\u201b\u2032\u02bc`"
_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2212"
LOOKALIKE_CLASSES = dict([(ch, "[%s]" % re.escape(_APOSTROPHES)) for ch in _APOSTROPHES] +
                         [(ch, "[%s]" % re.escape(_DASHES)) for ch in _DASHES])
# Statements must not assert counts or shares about participants; the script computes them from the evidence.
PEOPLE = r"(?:participants?|users?|people|interviewees?|respondents?|reviewers?|operators?|members?|customers?|admins?|administrators?|testers?)"
GROUPS = (r"(?:teams|companies|organi[sz]ations|orgs|accounts|businesses|firms|clinics|practices|stores|shops|"
          r"sessions|interviews|groups|departments|workspaces|households|families|schools|hospitals|agencies|brands)")
NUMWORD = (r"(?:[0-9]+(?:\.[0-9]+)?|(?:a |one )?hundred|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
           r"twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
           r"(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?")
SHARE = (r"(?:" + NUMWORD + r" ?(?:%|percent\b|per cent\b|pct\b)|(?:a|one|two|three|four)[- ](?:half|third|thirds|"
         r"quarter|quarters|fifth|fifths)\b|half\b)")
DET_ADJ = r"(?:the\s+|our\s+|these\s+|those\s+)?(?:[^\W\d_]+\s+)?"
APPROX = r"(?:nearly|almost|virtually|practically|basically|pretty much|just about)"
THEM = r"(?:them|those|us|these)"
# After "3 of 5" or "all five": what makes it a head count rather than "one of two templates".
COUNT_TAIL = (r"(?:\s*(?:[,.;:!?)]|$)|\s+(?:[^\W\d_]+\s+)?(?:" + PEOPLE + "|" + GROUPS + "|" + THEM + r"|said|says|say|"
              r"told|mentioned|reported|described|wanted|wants?|asked|agreed|disagreed|preferred|prefers?|chose|"
              r"skipped|struggled|used|uses?|had|have|has|were|are|was|did|does|do|needed|needs?|would|could|felt|"
              r"feels?|found|named|raised|liked|disliked|hated|loved|got|gave|expected|expects?|stalled|asked)\b)")
# Heuristic only: wording that states a count or share of participants (or of their teams/companies/sessions).
QUANTITY_RE = re.compile(
    r"\b(?:everyone|everybody|nobody|no[- ]?one)\b(?!-)|\b(?:unanimous(?:ly)?|majority|minority|handful)\b|"
    r"^(?:most|nearly all|almost all|virtually all|practically all)\b(?! of (?:the )?(?:elapsed |export |processing )*(?:time|cost|budget|work|code|data)\b)|^" + SHARE + r"\s+(?:of\b|[^\W\d_])|"
    r"\b(?:all|most|many|several|few|none|some|half|both|each|every|no|not one|not a single|only one|a single|"
    r"a few|a couple|" + APPROX + r" (?:all|every|none))\s+(?:of\s+)?" + DET_ADJ + PEOPLE + r"\b|"
    r"\b(?:most|many|several|nearly all|almost all|virtually all|practically all|all but (?:one|two|three|a few))\s+"
    r"(?:of\s+)?" + DET_ADJ + GROUPS + r"\b|"
    r"\b(?:all|most|many|several|few|none|half|both|each|every one|every single one|neither|a few|a couple|the rest|"
    r"not one|only one|" + APPROX + r" all|all but (?:one|two|three|a few))\s+of\s+" + THEM + r"\b|"
    r"\ball but (?:one|two|three|four|five|a few|a couple)\b|"
    r"\b" + NUMWORD + r"(?:\s+|-)(?:out(?:\s+|-)of|in)(?:\s+|-)(?:the\s+)?" + NUMWORD + r"\b|"
    r"(?<![\w.])" + SHARE + r"\s*(?:of\s+)?" + DET_ADJ + r"(?:" + PEOPLE + "|" + GROUPS + r")\b|"
    r"\b(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:[^\W\d_]+\s+)?"
    r"(?:participants?|interviewees?|respondents?)\b", re.I)
# "3 of 5 said", "all five said": counted by states_quantity unless a word like "step" or "page" comes first.
N_OF_M_RE = re.compile(r"\b(?:" + NUMWORD + r"\s+of|all)\s+(?:the\s+)?" + NUMWORD + COUNT_TAIL, re.I)
POSITION_WORDS = set(["step", "steps", "page", "pages", "part", "parts", "phase", "stage", "level", "round", "question",
                      "item", "items", "day", "week", "month", "version", "chapter", "slide", "screen", "task", "tasks",
                      "module", "lesson", "section", "episode", "row", "line", "number", "no", "#"])
# "3/5", "4 / 5": a fraction whose top is not larger than its bottom (so "24/7" is not one).
FRACTION_RE = re.compile(r"(?<![\w./])([0-9]{1,4})\s?/\s?([0-9]{1,4})(?![\w/]|\.[0-9])")
NAME_PART_STOPWORDS = set(["van", "von", "der", "den", "del", "della", "los", "las", "the", "and", "mrs", "miss",
                           "mr", "ms", "dr", "jr", "sr", "iii"])
MIN_YEAR, MAX_YEAR = 1900, 2999
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 2000000
LIMITS = {"participants": 999, "meetings": 2000, "evidence": 20000, "findings": 2000, "opportunities": 500,
          "research_questions": 50, "segments": 50, "open_questions": 200, "identifiers": 100}
EXIT_OUTPUT, EXIT_INPUT = 1, 2


class InputError(Exception):
    """Bad input; the message names the file and the JSON path."""


# --------------------------------------------------------------------------- text safety


def clean_text(value):
    """Collapse whitespace (incl. newlines) and drop control, lone-surrogate, bidi-override and invisible characters;
    NFKC-normalise so full-width and other compatibility forms compare and redact like plain letters."""
    kept = "".join(" " if ch.isspace() else ch for ch in value
                   if ch.isspace() or (unicodedata.category(ch) not in ("Cc", "Cs") and ch not in BIDI_CONTROLS
                                       and ch not in INVISIBLE))
    kept = unicodedata.normalize("NFKC", ASCII_JOINER_RE.sub("", kept))
    return " ".join(kept.split())


def fold(text):
    return unicodedata.normalize("NFKC", text).casefold()


def states_quantity(text):
    """Heuristic: True when the wording states a count or share of participants ('most admins', '4 out of 5',
    '3/5', 'nearly everyone', 'two in three', 'all of them')."""
    def counted(m):
        before = text[:m.start()].split()
        return not before or before[-1].casefold().strip(",:;(") not in POSITION_WORDS

    # Population prevalence differs from a property of a workflow or a numeric
    # range of seats/time. These English patterns are a conservative backstop.
    population = r"(?:" + PEOPLE + "|" + GROUPS + r")"
    attitude = r"(?:demand|request|want|need|prefer|ask|report|say|said|agree|disagree|choose|chose|like|dislike|struggle|expect)\w*"
    adverb = r"(?:overwhelmingly|universally|mostly|largely|generally|predominantly|mainly|chiefly|typically|consistently|commonly|frequently|almost always|tend to)"
    extra = (
        r"\b(?:more than|less than|over|under) half\b|"
        r"\b(?:roughly|about|approximately|around)?\s*" + SHARE + r"\s+" + attitude + r"\b|"
        r"\b" + population + r"\s+(?:all but )?" + adverb + r"\s+" + attitude + r"\b|"
        r"\b(?:across the board,?\s+" + population + r"|to a person|to a man|without exception(?! handling)|the bulk of|lion['’]s share of|plurality of)\b|"
        r"\bn\s*=\s*\d+\b|"
        r"\b" + NUMWORD + r"\s+to\s+" + NUMWORD + r"\b(?!\s+(?:weeks?|days?|months?|years?|hours?|minutes?|seconds?|seats?|screens?|tools?|steps?|pages?|invites?|projects?|reports?)\b)|"
        r"\b" + NUMWORD + r"\s+out of every\s+" + NUMWORD + r"\b|"
        r"\b" + NUMWORD + r"\s+" + PEOPLE + r"\b" + COUNT_TAIL + r"|"
        r"\b" + PEOPLE + r"\s+all\b|"
        r"\bevery one of (?:the )?" + population + r"\b"
    )
    if re.search(extra, text, re.I):
        return True
    if re.search(r"\b" + population + r"\b", text, re.I):
        if re.search(r"\b(?:with (?:one|a single) exception|by a wide margin|without fail|as a group|more often than not)\b", text, re.I):
            return True
        if re.search(r"\b" + attitude + r"\b[^.;!?]*?\b[0-9]+\s*:\s*[0-9]+\b", text, re.I):
            return True
    # Preserve the previous fragment contract without treating the same words
    # embedded in a technical statement as population prevalence.
    if text.strip().casefold().rstrip(".") in ("across the board", "without exception"):
        return True
    if QUANTITY_RE.search(text):
        return True
    if any(counted(m) for m in N_OF_M_RE.finditer(text)):
        return True
    return any(0 < int(m.group(2)) and int(m.group(1)) <= int(m.group(2)) and counted(m)
               for m in FRACTION_RE.finditer(text))


def variants(key):
    """A folded identifier plus its accent-free form, and for multi-word values the handle-style joins
    (mayaokonjo, maya.okonjo, maya_okonjo, maya-okonjo)."""
    out = [key]
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", key) if not unicodedata.combining(ch))
    if plain != key:
        out.append(plain)
    for base in list(out):
        words = base.split()
        if len(words) > 1:
            out += [sep.join(words) for sep in ("", ".", "_", "-")]
    return out


class Redactor(object):
    """Pseudonymisation for displayed text. Email addresses and phone numbers are replaced first
    (granola_text.redact_contacts: emails by pattern, phones only on a clear signal), then web addresses by pattern,
    then listed names and organisations (and the parts of multi-word names) case-insensitively as whole words.
    Pattern-based: unlisted names, nicknames, misspellings and look-alike letters can slip through."""

    def __init__(self, names, orgs):
        self.terms = []
        seen = set()
        for kind, values in (("[name]", names), ("[organisation]", orgs)):
            for value in values:
                for key in variants(fold(value)):
                    if key in seen:
                        continue
                    seen.add(key)
                    self.terms.append((key, kind))
        self.terms.sort(key=lambda t: (-len(t[0]), t[0], t[1]))
        self.patterns = []
        for key, kind in self.terms:
            body = r"\s+".join("".join(LOOKALIKE_CLASSES.get(ch, re.escape(ch)) for ch in part) for part in key.split())
            # Whole words: not touching another letter (a digit or _ next to it still counts as a boundary).
            self.patterns.append((re.compile(r"(?<![^\W\d_])" + body + r"(?![^\W\d_])", re.I), kind))

    def __call__(self, text):
        if text is None:
            return None
        text = gt.redact_contacts(text)
        text = WEB_LINK_RE.sub("[web address removed]", text)
        text = WEB_DOMAIN_RE.sub("[web address removed]", text)
        for pattern, kind in self.patterns:
            text = pattern.sub(kind, text)
        return text

    def found_in(self, text):
        """True when a listed identifier appears in text as whole words (any separators), or squashed together
        (mayaokafor) for identifiers of 5+ letters. Recorder ids (UUIDs, not_...) are ignored."""
        tokens = re.findall(r"[^\W_]+", fold(RECORDER_ID_IN_TEXT_RE.sub(" ", text)))
        joined = " " + " ".join(tokens) + " "
        for key, _ in self.terms:
            parts = re.findall(r"[^\W_]+", key)
            if not parts:
                continue
            if " " + " ".join(parts) + " " in joined:
                return True
            squashed = "".join(parts)
            if len(squashed) >= 5 and any(squashed in t for t in tokens):
                return True
        return False


def safe_url(raw):
    """Return a percent-encoded absolute http(s) URL, or None when it is not one."""
    if not isinstance(raw, str):
        return None
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
            or not parts.hostname or "." not in parts.hostname.strip(".") or parts.hostname.startswith(".") \
            or (port is not None and not 0 < port < 65536):
        return None
    url = "%s://%s%s" % (parts.scheme.lower(), netloc.lower(), quote(parts.path, safe=URL_SAFE))
    if parts.query:
        url += "?" + quote(parts.query, safe=URL_SAFE)
    if parts.fragment:
        url += "#" + quote(parts.fragment, safe=URL_SAFE)
    return url


def md_link(label, url):
    return "[%s](%s)" % (gt.md(label), url) if url else gt.md(label)


def csv_cell(value):
    """Plain text for a CSV cell; a leading formula character is neutralised with an apostrophe."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r", "|", "\uff1d", "\uff0b", "\uff0d", "\uff20"):
        return "'" + text
    return text


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


def quote_problem(text, source_text):
    """None when text passes granola_text's whole-sentence rule against source_text (one complete sentence, or a run
    of consecutive complete sentences, from one speaker turn; a line is a turn of its own, so a participant's line
    and the interviewer's line can't be joined), else the reason. Without source text only the checks that need none
    run (ellipsis, speaker label, fewer than 3 words)."""
    ok, reason = gt.check_quote(text, text if source_text is None else source_text)
    return None if ok else reason


def natural_key(ident):
    m = re.match(r"^([A-Z]+)([0-9]+)$", ident)
    return (m.group(1), int(m.group(2))) if m else (ident, 0)


# --------------------------------------------------------------------------- reading


class Reader(object):
    """Typed accessors that report errors as '<file>: <json.path>: problem'."""

    def __init__(self, label):
        self.label = label

    def fail(self, path, problem):
        return InputError("%s: %s: %s" % (self.label, path, problem))

    def obj(self, value, path, required=True):
        if value is None and not required:
            return {}
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        return value

    def lst(self, value, path, required=False, limit=None):
        if value is None:
            if required:
                raise self.fail(path, "is required (use [] when there is nothing)")
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        if limit is not None and len(value) > limit:
            raise self.fail(path, "has %d entries; at most %d are supported" % (len(value), limit))
        return value

    def text(self, parent, key, path, required=True, max_chars=None):
        value = parent.get(key)
        if value is not None and not isinstance(value, str):
            raise self.fail("%s.%s" % (path, key), "must be a string, got %s" % type(value).__name__)
        if isinstance(value, str):
            if len(value) > MAX_TEXT_CHARS:
                raise self.fail("%s.%s" % (path, key), "is too long (%d characters)" % len(value))
            if has_surrogate(value):
                raise self.fail("%s.%s" % (path, key), "contains an unpaired surrogate character; fix the encoding")
            value = clean_text(value)
        if value is None or value == "":
            if required:
                raise self.fail("%s.%s" % (path, key), "is required but missing or empty")
            return None
        if max_chars is not None and len(value) > max_chars:
            raise self.fail("%s.%s" % (path, key), "is %d characters; keep it to %d" % (len(value), max_chars))
        return value

    def ident(self, parent, key, path, pattern, example):
        value = parent.get(key)
        if not isinstance(value, str) or not pattern.match(value):
            raise self.fail("%s.%s" % (path, key), "must be an id like %s" % example)
        return value

    def enum(self, parent, key, path, allowed, default=None, required=False):
        value = parent.get(key, None)
        if value is None:
            if required:
                raise self.fail("%s.%s" % (path, key), "is required: one of %s" % ", ".join(allowed))
            value = default
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("%s.%s" % (path, key), "must be one of %s" % ", ".join(allowed))
        return value

    def boolean(self, parent, key, path, allow_null=True):
        value = parent.get(key)
        if value is None and allow_null:
            return None
        if not isinstance(value, bool):
            raise self.fail("%s.%s" % (path, key), "must be true or false%s" % (" or null" if allow_null else ""))
        return value

    def day(self, parent, key, path, required=True):
        value = parent.get(key)
        if value is None and not required:
            return None
        return parse_day(value, lambda problem: self.fail("%s.%s" % (path, key), problem))

    def strings(self, parent, key, path, limit, min_chars=1):
        out = []
        for n, item in enumerate(self.lst(parent.get(key), "%s.%s" % (path, key), limit=limit)):
            if not isinstance(item, str):
                raise self.fail("%s.%s[%d]" % (path, key, n), "must be a string")
            if has_surrogate(item):
                raise self.fail("%s.%s[%d]" % (path, key, n), "contains an unpaired surrogate character")
            item = clean_text(item)
            if len(item) < min_chars:
                raise self.fail("%s.%s[%d]" % (path, key, n), "must have at least %d characters" % min_chars)
            if len(item) > 200:
                raise self.fail("%s.%s[%d]" % (path, key, n), "is longer than 200 characters")
            out.append(item)
        return out


def parse_day(value, fail):
    """Strict YYYY-MM-DD (ASCII digits only), identical on every Python version."""
    m = DATE_RE.match(value) if isinstance(value, str) else None
    if not m:
        raise fail("must be a YYYY-MM-DD date")
    if not MIN_YEAR <= int(m.group(1)) <= MAX_YEAR:
        raise fail("is outside the years %d-%d" % (MIN_YEAR, MAX_YEAR))
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise fail("is not a real date") from None


def parse_as_of(value):
    """--as-of: a YYYY-MM-DD date or a strict ISO datetime with Z or ±HH:MM; the local date is used."""
    if not isinstance(value, str):
        raise InputError("--as-of is required, e.g. --as-of 2026-10-02")
    value = value.strip()
    if DATE_RE.match(value):
        return parse_day(value, lambda p: InputError("--as-of %s" % p))
    m = DATETIME_RE.match(value)
    if not m:
        raise InputError("--as-of must be a date like 2026-10-02 or a datetime like 2026-10-02T09:00:00-07:00")
    year, month, day_, hour, minute, second, _frac, offset = m.groups()
    if offset != "Z" and (int(offset[1:3]) > 14 or int(offset[4:6]) > 59 or
                          (int(offset[1:3]) == 14 and int(offset[4:6]) > 0)):
        raise InputError("--as-of has an impossible UTC offset")
    if int(hour) > 23 or int(minute) > 59 or int(second or 0) > 59:
        raise InputError("--as-of has an impossible time")
    return parse_day("%s-%s-%s" % (year, month, day_), lambda p: InputError("--as-of %s" % p))


def reject_constant(name):
    raise InputError("%s is not allowed (numbers must be finite)" % name)


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise InputError("%s is not allowed (numbers must be finite)" % text)
    return value


def load_json(path, label):
    try:
        if not path.is_file():
            raise InputError("%s: file not found: %s" % (label, path))
        if path.stat().st_size > MAX_BUNDLE_BYTES:
            raise InputError("%s: %s is larger than %d MB" % (label, path, MAX_BUNDLE_BYTES // (1024 * 1024)))
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant, parse_float=finite_float)
    except InputError as exc:
        if str(exc).startswith(label + ":"):
            raise
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
    label = "config %s" % path
    if not isinstance(config, dict):
        raise InputError("%s: $ must be an object" % label)
    missing = [k for k in CONFIG_KEYS if k not in config]
    if missing:
        raise InputError("%s: missing key(s): %s" % (label, ", ".join(missing)))

    def integer(key, low, high):
        v = config[key]
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise InputError("%s: $.%s must be a whole number from %d to %d" % (label, key, low, high))
        return v

    def share(key):
        v = config[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise InputError("%s: $.%s must be a number from 0 to 1" % (label, key))
        return float(v)

    low = integer("min_pattern_participants", 2, 100)
    medium = integer("medium_min_participants", low, 100)
    integer("high_min_participants", medium, 100)
    share("high_min_share_of_participants")
    share("high_max_contradicting_ratio")
    integer("segment_min_participants", 1, 100)
    share("segment_gap_min_share")
    integer("max_quotes_per_finding", 0, 20)
    integer("max_contradicting_quotes", 0, 20)
    integer("max_quote_chars", 40, 5000)
    integer("max_statement_chars", 40, 2000)
    integer("max_single_signals", 0, 500)
    if config["date_precision"] not in DATE_PRECISIONS:
        raise InputError("%s: $.date_precision must be one of %s" % (label, ", ".join(DATE_PRECISIONS)))
    patterns = config["leading_question_patterns"]
    if not isinstance(patterns, list) or not all(isinstance(p, str) and p.strip() for p in patterns):
        raise InputError("%s: $.leading_question_patterns must be a list of non-empty strings" % label)
    compiled = []
    for n, p in enumerate(patterns):
        try:
            compiled.append(re.compile(p, re.I))
        except (re.error, RecursionError, OverflowError):
            raise InputError("%s: $.leading_question_patterns[%d] is not a valid regular expression" % (label, n)) \
                from None
    config["_leading"] = compiled
    free = config["free_email_domains"]
    if not isinstance(free, list) or not all(isinstance(d, str) and DOMAIN_RE.match(d) for d in free):
        raise InputError("%s: $.free_email_domains must be a list of domains such as gmail.com" % label)
    return config


def email_parts(email):
    """Domain and its first label for a listed email address (used for redaction only)."""
    if "@" not in email:
        return None, None
    domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    if not DOMAIN_RE.match(domain):
        return None, None
    return domain, domain.split(".")[0]


def name_parts(names):
    """Parts of multi-word names (3+ characters, not particles such as van or de), redacted on their own too."""
    parts = []
    for name in names:
        if len(name.split()) > 1:
            # Whole words (O'Brien, Jean-Luc) and the pieces inside them (Brien, Jean, Luc).
            words = re.split(r"[\s.,]+", name)
            inner = "[%s%s]+" % (re.escape(_APOSTROPHES), re.escape(_DASHES))
            words += [piece for w in words for piece in re.split(inner, w)]
            parts += [w for w in words if len(w) >= 3 and w.casefold() not in NAME_PART_STOPWORDS]
    return parts


def load_bundle(path, config):
    raw = load_json(path, "bundle")
    r = Reader("bundle %s" % path)
    b = r.obj(raw, "$")
    if b.get("schema_version") != 1 or isinstance(b.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1")

    # ---- private identifiers first, so every other check can use the redactor
    names, orgs = [], []
    participants_raw = r.lst(b.get("participants"), "$.participants", required=True, limit=LIMITS["participants"])
    study = r.obj(b.get("study"), "$.study")
    study_private = r.obj(study.get("private"), "$.study.private", required=False)
    study_names = r.strings(study_private, "names", "$.study.private", LIMITS["identifiers"], 2)
    names += study_names + name_parts(study_names)
    orgs += r.strings(study_private, "organisations", "$.study.private", LIMITS["identifiers"], 2)
    private_by_pid = {}
    for n, p in enumerate(participants_raw):
        pp = "$.participants[%d]" % n
        r.obj(p, pp)
        priv = r.obj(p.get("private"), pp + ".private", required=False)
        pn = r.strings(priv, "names", pp + ".private", LIMITS["identifiers"], 2)
        po = r.strings(priv, "organisations", pp + ".private", LIMITS["identifiers"], 2)
        pe = r.strings(priv, "emails", pp + ".private", LIMITS["identifiers"], 3)
        for e in pe:
            if "@" not in e:
                raise r.fail(pp + ".private.emails", "must hold email addresses")
            domain, label = email_parts(e)
            if domain and domain not in config["free_email_domains"]:
                po = po + [domain] + ([label] if len(label) >= 4 else [])
        names += pn + name_parts(pn)
        orgs += po
        private_by_pid[n] = (set(fold(x) for x in pn if len(x.split()) > 1), set(fold(e) for e in pe))
    red = Redactor(names, orgs)

    def prose(parent, key, pth, required=True, max_chars=None):
        value = r.text(parent, key, pth, required, max_chars)
        return None if value is None else red(value)

    def statement(parent, key, pth):
        value = r.text(parent, key, pth, True, config["max_statement_chars"])
        if states_quantity(value):
            raise r.fail("%s.%s" % (pth, key), "states a count or share (e.g. 'most participants', '5 of 8', "
                         "'3/5', 'everyone'); write the claim only, the script computes the counts from the evidence")
        return red(value)

    # ---- study
    out = {"warnings": {"links_dropped": 0}}
    out["study"] = {"name": prose(study, "name", "$.study", max_chars=200),
                    "method": r.enum(study, "method", "$.study", METHODS, default="interviews")}
    rqs = []
    rq_ids = set()
    for n, q in enumerate(r.lst(study.get("research_questions"), "$.study.research_questions", required=True,
                                limit=LIMITS["research_questions"])):
        qp = "$.study.research_questions[%d]" % n
        r.obj(q, qp)
        qid = r.ident(q, "id", qp, RQ_RE, "RQ1")
        if qid in rq_ids:
            raise r.fail(qp + ".id", "duplicate research question id %s" % qid)
        rq_ids.add(qid)
        rqs.append({"id": qid, "text": prose(q, "text", qp, max_chars=400)})
    if not rqs:
        raise r.fail("$.study.research_questions", "list at least one research question; findings answer them")
    rqs.sort(key=lambda q: natural_key(q["id"]))
    out["research_questions"] = rqs
    segments = {}
    for n, s in enumerate(r.lst(study.get("segments"), "$.study.segments", limit=LIMITS["segments"])):
        sp = "$.study.segments[%d]" % n
        r.obj(s, sp)
        key = s.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise r.fail(sp + ".key", "must be a lower-case key such as self-serve")
        if key in segments:
            raise r.fail(sp + ".key", "duplicate segment key")
        segments[key] = {"key": key, "label": prose(s, "label", sp, max_chars=80), "order": n}
    labels = [fold(s["label"]) for s in segments.values()]
    if len(set(labels)) != len(labels):
        raise r.fail("$.study.segments", "two segments have the same label; make labels distinct")
    out["segments"] = segments
    out["open_questions"] = []
    for n, q in enumerate(r.lst(b.get("open_questions"), "$.open_questions", limit=LIMITS["open_questions"])):
        qp = "$.open_questions[%d]" % n
        r.obj(q, qp)
        rq = q.get("rq")
        if rq is not None and (not isinstance(rq, str) or rq not in rq_ids):
            raise r.fail(qp + ".rq", "is not a research question id in $.study.research_questions")
        out["open_questions"].append({"rq": rq, "text": prose(q, "text", qp, max_chars=400)})

    # ---- sources
    sources = r.obj(b.get("sources"), "$.sources")
    out["sources"] = {}
    for key in SOURCE_KEYS:
        sp = "$.sources.%s" % key
        s = r.obj(sources.get(key), sp)
        provider = r.text(s, "provider", sp, required=False, max_chars=40)
        if provider is not None and (provider.lower() not in PROVIDER_LABELS):
            raise r.fail(sp + ".provider", "must be one of %s or null" % ", ".join(sorted(PROVIDER_LABELS)))
        out["sources"][key] = {"provider": provider.lower() if provider else None,
                               "status": r.enum(s, "status", sp, SOURCE_STATUSES, required=True),
                               "detail": prose(s, "detail", sp, required=False, max_chars=300),
                               "folder": prose(s, "folder", sp, required=False, max_chars=120)}

    # ---- participants
    participants = {}
    for n, p in enumerate(participants_raw):
        pp = "$.participants[%d]" % n
        pid = r.ident(p, "id", pp, PARTICIPANT_RE, "P1")
        if pid in participants:
            raise r.fail(pp + ".id", "duplicate participant id %s" % pid)
        seg = p.get("segment")
        if seg is not None and (not isinstance(seg, str) or seg not in segments):
            raise r.fail(pp + ".segment", "is not a key in $.study.segments")
        participants[pid] = {"id": pid, "segment": seg,
                             "consent": r.enum(p, "consent", pp, CONSENTS, required=True),
                             "index": n, "meetings": []}
    pids = sorted(participants, key=natural_key)
    for i in range(len(participants_raw)):
        for j in range(i + 1, len(participants_raw)):
            fi, ei = private_by_pid[i]
            fj, ej = private_by_pid[j]
            if fi & fj or ei & ej:
                raise r.fail("$.participants", "%s and %s share a full name or email in private; if they are the "
                             "same person, merge them into one participant"
                             % (participants_raw[i]["id"], participants_raw[j]["id"]))

    # ---- meetings
    meetings = {}
    excluded_meetings = []
    for n, m in enumerate(r.lst(b.get("meetings"), "$.meetings", required=True, limit=LIMITS["meetings"])):
        mp = "$.meetings[%d]" % n
        r.obj(m, mp)
        mid = m.get("meeting_id")
        if not isinstance(mid, str) or not MEETING_ID_RE.match(mid):
            raise r.fail(mp + ".meeting_id", "must be the recorder's id (letters, digits, '_' or '-', up to 64)")
        if mid in meetings or mid in [x["meeting_id"] for x in excluded_meetings]:
            raise r.fail(mp + ".meeting_id", "duplicate meeting id")
        if not RECORDER_ID_RE.match(mid) and red.found_in(mid):
            raise r.fail(mp + ".meeting_id", "contains a name, organisation or email listed in private; use the "
                         "recorder's id or a neutral one such as pasted-1")
        reason = prose(m, "excluded_reason", mp, required=False, max_chars=120)
        day = r.day(m, "date", mp)
        if reason is not None:
            excluded_meetings.append({"meeting_id": mid, "reason": reason, "date": day})
            continue
        url_raw = r.text(m, "url", mp, required=False, max_chars=2000)
        url = None
        if url_raw is not None:
            url = safe_url(url_raw)
            if url is None or red.found_in(url):
                url = None
                out["warnings"]["links_dropped"] += 1
        plist = r.lst(m.get("participant_ids"), mp + ".participant_ids", required=True, limit=50)
        if not plist:
            raise r.fail(mp + ".participant_ids", "list the participants interviewed in this meeting")
        for k, pid in enumerate(plist):
            if not isinstance(pid, str) or pid not in participants:
                raise r.fail("%s.participant_ids[%d]" % (mp, k), "is not a participant id in $.participants")
        if len(set(plist)) != len(plist):
            raise r.fail(mp + ".participant_ids", "lists a participant twice")
        texts = {}
        for key in EVIDENCE_SOURCES:
            tp = "%s.%s" % (mp, key)
            t = r.obj(m.get(key), tp)
            status = r.enum(t, "status", tp, TEXT_STATUSES, required=True)
            text = r.text(t, "text", tp, required=False)
            if isinstance(t.get("text"), str) and (text is None or not any(
                    unicodedata.category(ch)[0] in "LNPS" and ch not in "\u115f\u1160\u3164\uffa0\u2800"
                    for ch in t["text"])):
                raise r.fail(tp + ".text", "is empty; an empty %s text usually means the fetch failed. Fetch it again, "
                             "or leave the text out (null) so its quotes are reported as not checked" % key)
            if text is not None and status != "ok":
                raise r.fail(tp + ".text", "is present but status is %s; set status ok or remove the text" % status)
            # The raw text (line breaks kept: each line is a speaker turn of its own) is used only to check quotes.
            texts[key] = {"status": status, "text": t["text"] if text is not None else None}
        meetings[mid] = {"meeting_id": mid, "date": day, "url": url, "participant_ids": list(plist),
                         "transcript": texts["transcript"], "notes": texts["notes"], "position": n}
        for pid in plist:
            participants[pid]["meetings"].append(mid)
    for pid in pids:
        if not participants[pid]["meetings"] and participants[pid]["consent"] != "declined":
            raise r.fail("$.participants", "%s is not listed on any included meeting" % pid)

    # ---- evidence
    evidence = {}
    dropped_declined = 0
    raw_evidence = r.lst(b.get("evidence"), "$.evidence", required=True, limit=LIMITS["evidence"])
    raw_evidence_ids = set(x.get("id") for x in raw_evidence if isinstance(x, dict) and isinstance(x.get("id"), str))
    checks = []
    for n, e in enumerate(raw_evidence):
        ep = "$.evidence[%d]" % n
        r.obj(e, ep)
        eid = r.ident(e, "id", ep, EVIDENCE_RE, "E1")
        if eid in evidence:
            raise r.fail(ep + ".id", "duplicate evidence id %s" % eid)
        pid = e.get("participant_id")
        if not isinstance(pid, str) or pid not in participants:
            raise r.fail(ep + ".participant_id", "is not a participant id in $.participants")
        mid = e.get("meeting_id")
        if not isinstance(mid, str) or mid not in meetings:
            if isinstance(mid, str) and mid in [x["meeting_id"] for x in excluded_meetings]:
                raise r.fail(ep + ".meeting_id", "cites a meeting marked excluded_reason")
            raise r.fail(ep + ".meeting_id", "is not a meeting id in $.meetings")
        mpath = "$.meetings[%d]" % meetings[mid]["position"]
        if pid not in meetings[mid]["participant_ids"]:
            raise r.fail(ep + ".participant_id", "%s is not listed in %s.participant_ids" % (pid, mpath))
        kind = r.enum(e, "kind", ep, EVIDENCE_KINDS, required=True)
        source = r.enum(e, "source", ep, EVIDENCE_SOURCES, required=True)
        raw_text = r.text(e, "text", ep)
        text = strip_outer_quotes(raw_text)
        if not text:
            raise r.fail(ep + ".text", "is empty after removing wrapping quote marks")
        src = meetings[mid][source]
        if src["status"] in ("denied", "unavailable", "not_requested"):
            raise r.fail(ep + ".source", "is %s but that meeting's %s status is %s" % (source, source, src["status"]))
        checked = src["text"] is not None
        checks.append((n, ep + ".text", text, mid, source))
        leading = r.boolean(e, "leading_question", ep)
        question = r.text(e, "question", ep, required=False, max_chars=600)
        if question is not None:
            question = strip_outer_quotes(question)
            checks.append((n, ep + ".question", question, mid, "transcript"))
            if gt.normalise(question).casefold().strip(" \"'.!?") == gt.normalise(text).casefold().strip(" \"'.!?"):
                raise r.fail(ep + ".text", "is the interviewer's question itself; quote the participant's answer")
        by_pattern = False
        if question is not None and leading is not True and any(p.search(question) for p in config["_leading"]):
            leading, by_pattern = True, True
        if participants[pid]["consent"] == "declined":
            dropped_declined += 1
            continue
        evidence[eid] = {"id": eid, "participant_id": pid, "meeting_id": mid, "kind": kind, "source": source,
                         "text": red(text), "checked": checked, "leading": leading, "leading_by_pattern": by_pattern,
                         "question": red(question) if question is not None else None}
    # Quote checks run grouped by meeting and source (the shared module caches the last few source texts); the first
    # failure in bundle order is reported.
    failures = []
    for n, path, quote_text, mid, source in sorted(checks, key=lambda c: (c[3], c[4], c[0], c[1])):
        problem = quote_problem(quote_text, meetings[mid][source]["text"])
        if problem:
            failures.append((n, path.endswith(".question"), path, problem, mid, source))
    if failures:
        n, _, path, problem, mid, source = min(failures)
        where = ("$.meetings[%d].%s.text" % (meetings[mid]["position"], source)
                 if meetings[mid][source]["text"] is not None else "a quote")
        raise r.fail(path, "is not a whole-sentence quote (%s; checked against %s). Copy one or more complete "
                     "sentences from one speaker turn, word for word, without the speaker label or cuts"
                     % (problem, where))
    out["warnings"]["evidence_dropped_declined"] = dropped_declined
    # A session whose participants all declined is excluded; then pseudonymous refs M1..Mn by date and recorder id.
    for mid in sorted(meetings):
        if all(participants[pid]["consent"] == "declined" for pid in meetings[mid]["participant_ids"]):
            excluded_meetings.append({"meeting_id": mid, "reason": "all participants declined consent",
                                      "date": meetings[mid]["date"]})
            del meetings[mid]
    for k, mid in enumerate(sorted(meetings, key=lambda x: (meetings[x]["date"], x))):
        meetings[mid]["ref"] = "M%d" % (k + 1)
    for pid in pids:
        participants[pid]["meetings"] = [mid for mid in participants[pid]["meetings"] if mid in meetings]

    # ---- findings
    findings = {}
    for n, f in enumerate(r.lst(b.get("findings"), "$.findings", required=True, limit=LIMITS["findings"])):
        fp = "$.findings[%d]" % n
        r.obj(f, fp)
        fid = r.ident(f, "id", fp, FINDING_RE, "F1")
        if fid in findings:
            raise r.fail(fp + ".id", "duplicate finding id %s" % fid)
        ftype = r.enum(f, "type", fp, FINDING_TYPES, default="finding")
        rq = f.get("rq")
        if ftype == "finding" and rq is None:
            raise r.fail(fp + ".rq", "is required for type finding (which research question does it answer?)")
        if rq is not None and (not isinstance(rq, str) or rq not in rq_ids):
            raise r.fail(fp + ".rq", "is not a research question id in $.study.research_questions")
        lists = {}
        for key in ("supporting", "contradicting"):
            ids = r.lst(f.get(key), "%s.%s" % (fp, key), required=(key == "supporting"), limit=LIMITS["evidence"])
            for k, eid in enumerate(ids):
                if not isinstance(eid, str) or not EVIDENCE_RE.match(eid):
                    raise r.fail("%s.%s[%d]" % (fp, key, k), "must be an evidence id like E1")
                if eid not in raw_evidence_ids:
                    raise r.fail("%s.%s[%d]" % (fp, key, k), "is not an evidence id in $.evidence")
            lists[key] = sorted(set(ids), key=natural_key)
        if not lists["supporting"]:
            raise r.fail(fp + ".supporting", "cite at least one piece of evidence")
        both = set(lists["supporting"]) & set(lists["contradicting"])
        if both:
            raise r.fail(fp, "evidence %s is listed as both supporting and contradicting"
                         % sorted(both, key=natural_key)[0])
        surprise = f.get("surprise")
        assumption = None
        if surprise is not None:
            r.obj(surprise, fp + ".surprise")
            assumption = prose(surprise, "assumption", fp + ".surprise", max_chars=300)
        findings[fid] = {"id": fid, "type": ftype, "rq": rq, "statement": statement(f, "statement", fp),
                         "supporting": [x for x in lists["supporting"] if x in evidence],
                         "contradicting": [x for x in lists["contradicting"] if x in evidence],
                         "assumption": assumption}

    # ---- opportunities
    opportunities = {}
    for n, o in enumerate(r.lst(b.get("opportunities"), "$.opportunities", limit=LIMITS["opportunities"])):
        op = "$.opportunities[%d]" % n
        r.obj(o, op)
        oid = r.ident(o, "id", op, OPPORTUNITY_RE, "O1")
        if oid in opportunities:
            raise r.fail(op + ".id", "duplicate opportunity id %s" % oid)
        fids = r.lst(o.get("findings"), op + ".findings", required=True, limit=LIMITS["findings"])
        if not fids:
            raise r.fail(op + ".findings", "base each opportunity on at least one finding")
        for k, fid in enumerate(fids):
            if not isinstance(fid, str) or fid not in findings:
                raise r.fail("%s.findings[%d]" % (op, k), "is not a finding id in $.findings")
        opportunities[oid] = {"id": oid, "statement": statement(o, "statement", op),
                              "findings": sorted(set(fids), key=natural_key)}

    out.update({"participants": participants, "pids": pids, "meetings": meetings,
                "excluded_meetings": excluded_meetings, "evidence": evidence, "findings": findings,
                "opportunities": opportunities})
    return out


# --------------------------------------------------------------------------- analysis


def band_for(strong, weak, contra, included, config):
    total = len(strong | weak)
    if total < config["min_pattern_participants"]:
        return "single"
    if len(contra) >= total:
        return "contested"
    if len(strong) >= config["high_min_participants"] and \
            len(strong) >= config["high_min_share_of_participants"] * included and \
            len(contra) <= config["high_max_contradicting_ratio"] * len(strong):
        return "high"
    if len(strong) >= config["medium_min_participants"]:
        return "medium"
    return "low"


def analyse(b, config):
    participants, evidence = b["participants"], b["evidence"]
    included = [p for p in b["pids"] if participants[p]["consent"] != "declined"]
    n_included = len(included)
    seg_members = {}
    for pid in included:
        seg = participants[pid]["segment"]
        if seg is not None:
            seg_members.setdefault(seg, set()).add(pid)
    rank = dict((band, n) for n, band in enumerate(BANDS))
    results = {}
    for fid, f in b["findings"].items():
        sup = [evidence[e] for e in f["supporting"]]
        con = [evidence[e] for e in f["contradicting"]]
        strong = set(e["participant_id"] for e in sup if e["leading"] is not True)
        anyone = set(e["participant_id"] for e in sup)
        weak = anyone - strong
        contra = set(e["participant_id"] for e in con)
        if not anyone:
            results[fid] = None  # every piece of its evidence came from participants who declined
            continue
        band = band_for(strong, weak, contra, n_included, config)
        by_seg = []
        for seg in sorted(seg_members, key=lambda s: b["segments"][s]["order"]):
            members = seg_members[seg]
            by_seg.append({"segment": seg, "label": b["segments"][seg]["label"], "members": len(members),
                           "supporting": len(anyone & members), "contradicting": len(contra & members)})
        results[fid] = {
            "id": fid, "band": band, "strong": strong, "weak": weak, "supporters": anyone, "contra": contra,
            "mixed": anyone & contra,
            "said": set(e["participant_id"] for e in sup if e["kind"] == "said"),
            "did": set(e["participant_id"] for e in sup if e["kind"] == "did"),
            "by_segment": by_seg,
            "sort": (rank[band], -len(strong), -len(anyone), len(contra), natural_key(fid)),
        }
    dropped_findings = sorted([fid for fid, a in results.items() if a is None], key=natural_key)
    live = dict((fid, a) for fid, a in results.items() if a is not None)

    # Segment differences: share of each segment's participants supporting, among segments big enough to compare.
    seg_diffs = []
    for fid, a in live.items():
        if a["band"] == "single":
            continue
        rows = [s for s in a["by_segment"] if s["members"] >= config["segment_min_participants"]]
        if len(rows) < 2:
            continue
        shares = [s["supporting"] / float(s["members"]) for s in rows]
        gap = max(shares) - min(shares)
        if gap >= config["segment_gap_min_share"] - 1e-9:
            seg_diffs.append({"finding": fid, "gap": gap, "rows": rows})
    seg_diffs.sort(key=lambda d: (-d["gap"], live[d["finding"]]["sort"]))

    # Opportunities: ranked only by the evidence behind them.
    opps = []
    for oid, o in b["opportunities"].items():
        linked = [fid for fid in o["findings"] if fid in live]
        strong, anyone, contra = set(), set(), set()
        for fid in linked:
            strong |= live[fid]["strong"]
            anyone |= live[fid]["supporters"]
            contra |= live[fid]["contra"]
        best = min([live[fid]["sort"][0] for fid in linked] or [len(BANDS)])
        pattern = len(anyone) >= config["min_pattern_participants"] and \
            any(live[fid]["band"] != "single" for fid in linked)
        opps.append({"id": oid, "statement": o["statement"], "findings": o["findings"], "linked": linked,
                     "strong": strong, "supporters": anyone, "weak": anyone - strong, "contra": contra,
                     "best": BANDS[best] if best < len(BANDS) else None, "pattern": pattern,
                     "sort": (0 if pattern else 1, -len(strong), -len(anyone), len(contra), best, -len(linked),
                              natural_key(oid))})
    opps.sort(key=lambda o: o["sort"])

    # What the study does not show yet.
    gaps = []
    for q in b["research_questions"]:
        mine = [live[fid] for fid, f in b["findings"].items() if f["rq"] == q["id"] and f["type"] == "finding"
                and fid in live]
        patterns = [a for a in mine if a["band"] != "single"]
        if not patterns:
            gaps.append("%s: no finding is supported by %d or more participants yet." % (
                q["id"], config["min_pattern_participants"]))
        elif all(a["band"] in ("low", "contested") for a in patterns):
            gaps.append("%s: evidence is thin; every finding is low confidence or contested." % q["id"])
    for seg in sorted(b["segments"], key=lambda s: b["segments"][s]["order"]):
        size = len(seg_members.get(seg, set()))
        if size < config["segment_min_participants"]:
            gaps.append("Segment %s has %d participant%s, so differences for it cannot be assessed." % (
                b["segments"][seg]["label"], size, "" if size == 1 else "s"))
    unsegmented = [p for p in included if participants[p]["segment"] is None]
    if unsegmented and b["segments"]:
        gaps.append("%d participant%s no segment, so %s left out of segment comparisons." % (
            len(unsegmented), " has" if len(unsegmented) == 1 else "s have", "is" if len(unsegmented) == 1 else "are"))
    leading_only = sorted([fid for fid, a in live.items() if a["band"] != "single" and not a["strong"]],
                          key=natural_key)
    if leading_only:
        gaps.append("%s rest%s only on answers to leading questions; ask neutrally before relying on %s." % (
            ", ".join(leading_only), "s" if len(leading_only) == 1 else "", "it" if len(leading_only) == 1 else "them"))
    return {"included": included, "n_included": n_included, "findings": live, "dropped_findings": dropped_findings,
            "seg_members": seg_members, "seg_diffs": seg_diffs, "opportunities": opps, "gaps": gaps}


# --------------------------------------------------------------------------- rendering helpers


def show_date(d, precision):
    if d is None or precision == "none":
        return None
    if precision == "month":
        return "%s %d" % (MONTHS[d.month - 1], d.year)
    return "%s %d, %d" % (MONTHS[d.month - 1], d.day, d.year)


def short_date(d, precision):
    if d is None or precision == "none":
        return None
    if precision == "month":
        return "%s %d" % (MONTHS[d.month - 1], d.year)
    return "%s %d" % (MONTHS[d.month - 1], d.day)


def ids_text(pids):
    return ", ".join(sorted(pids, key=natural_key)) if pids else "none"


def plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


def pick_quotes(b, ids, limit):
    """Up to `limit` evidence items, one per participant first: non-leading before leading, transcript before notes,
    then participant and evidence order. Evidence from participants without quote consent is never picked."""
    ev = [b["evidence"][e] for e in ids]
    ev.sort(key=lambda e: (e["leading"] is True, e["source"] != "transcript", natural_key(e["participant_id"]),
                           natural_key(e["id"])))
    chosen, seen_p, seen_t = [], set(), set()
    for rnd in (0, 1):
        for e in ev:
            if len(chosen) >= limit:
                break
            if b["participants"][e["participant_id"]]["consent"] != "granted" or e["text"] in seen_t or e in chosen:
                continue
            if rnd == 0 and e["participant_id"] in seen_p:
                continue
            chosen.append(e)
            seen_p.add(e["participant_id"])
            seen_t.add(e["text"])
    return chosen


def withheld(b, ids):
    return set(b["evidence"][e]["participant_id"] for e in ids
               if b["participants"][b["evidence"][e]["participant_id"]]["consent"] != "granted")


class Ctx(object):
    def __init__(self, b, a, config, as_of, links):
        self.b, self.a, self.config, self.as_of, self.links = b, a, config, as_of, links
        self.precision = config["date_precision"]
        self.has_did = any(e["kind"] == "did" for e in b["evidence"].values())

    def url(self, mid):
        return self.b["meetings"][mid]["url"] if self.links else None

    def cite(self, e):
        m = self.b["meetings"][e["meeting_id"]]
        d = short_date(m["date"], self.precision)
        return "%s%s \u00b7 %s" % (e["participant_id"], ", " + d if d else "", md_link(m["ref"], self.url(m["meeting_id"])))

    def evidence_line(self, e):
        limit = self.config["max_quote_chars"]
        text = gt.md(gt.shorten(e["text"], limit))
        if e["source"] == "transcript":
            body = "\u201c%s\u201d" % text
            if e["kind"] == "did":
                body = "Observed (transcript): " + body
        else:
            body = ("Observed (notes, not verbatim speech): _%s_" if e["kind"] == "did"
                    else "Note excerpt (notes, not verbatim speech): _%s_") % text
        line = "%s \u2014 %s" % (body, self.cite(e))
        flags = []
        if e["leading"] is True:
            flags.append("\u26a0 answer to a leading question%s%s; weaker evidence" % (
                " (\u201c%s\u201d)" % gt.md(gt.shorten(e["question"], 200)) if e["question"] else "",
                ", flagged by pattern" if e["leading_by_pattern"] else ""))
        if not e["checked"]:
            flags.append("not checked against source text")
        if flags:
            line += " \u2014 " + "; ".join(flags)
        return line


def finding_counts(ctx, a):
    n = ctx.a["n_included"]
    parts = ["Support: %d of %d participants (%s)" % (len(a["supporters"]), n, ids_text(a["supporters"]))]
    if a["weak"]:
        parts.append("%d only via leading questions (%s)" % (len(a["weak"]), ids_text(a["weak"])))
    parts.append("Contradict: %d (%s)" % (len(a["contra"]), ids_text(a["contra"])) if a["contra"] else "Contradict: 0")
    parts.append("Said: %s" % ids_text(a["said"]))
    parts.append("Did (observed): %s" % (ids_text(a["did"]) if a["did"] else "none"))
    line = " \u00b7 ".join(parts)
    if a["mixed"]:
        line += " \u00b7 Mixed (both sides): %s" % ids_text(a["mixed"])
    return line


def segment_line(a):
    if not a["by_segment"]:
        return None
    cells = []
    for s in a["by_segment"]:
        cell = "%s %d/%d" % (gt.md(s["label"]), s["supporting"], s["members"])
        if s["contradicting"]:
            cell += " (%d contradict)" % s["contradicting"]
        cells.append(cell)
    return "By segment (supporting/participants): " + " \u00b7 ".join(cells)


def finding_notes(a, ctx):
    notes = []
    if a["band"] != "single" and len(a["strong"]) < ctx.config["min_pattern_participants"]:
        notes.append("rests %s on answers to leading questions" % ("only" if not a["strong"] else "partly"))
    if a["supporters"] and not a["did"] and ctx.has_did:
        notes.append("stated only; no observed behaviour")
    return notes


def render_finding_block(ctx, f, a, out, heading="####"):
    b, config = ctx.b, ctx.config
    out.append("%s %s. %s \u2014 %s" % (heading, f["id"], gt.md(f["statement"]), BAND_LABELS[a["band"]]))
    out.append("")
    out.append(finding_counts(ctx, a))
    seg = segment_line(a)
    if seg:
        out.append("")
        out.append(seg)
    notes = finding_notes(a, ctx)
    if notes:
        out.append("")
        out.append("_Note: %s._" % "; ".join(notes))
    out.append("")
    quotes = pick_quotes(b, f["supporting"], config["max_quotes_per_finding"])
    for e in quotes:
        out.append("- " + ctx.evidence_line(e))
    contra_q = pick_quotes(b, f["contradicting"], config["max_contradicting_quotes"])
    for e in contra_q:
        out.append("- Contradicting: " + ctx.evidence_line(e))
    held = sorted(withheld(b, f["supporting"] + f["contradicting"]), key=natural_key)
    if held:
        out.append("- Quotes withheld (no quote consent): %s; their evidence still counts. Sources: %s" % (
            ", ".join(held), ", ".join(sorted(set(
                md_link(b["meetings"][b["evidence"][e]["meeting_id"]]["ref"],
                        ctx.url(b["evidence"][e]["meeting_id"]))
                for e in f["supporting"] + f["contradicting"]
                if b["evidence"][e]["participant_id"] in held), key=lambda s: s))))
    if not quotes and not contra_q and not held:
        out.append("- No quotable evidence.")
    out.append("")


def sorted_findings(ctx, fids):
    live = ctx.a["findings"]
    return sorted([fid for fid in fids if fid in live], key=lambda fid: live[fid]["sort"])


# --------------------------------------------------------------------------- markdown


def coverage(ctx):
    b, a = ctx.b, ctx.a
    ms = list(b["meetings"].values())
    t_ok = len([m for m in ms if m["transcript"]["status"] == "ok"])
    t_denied = len([m for m in ms if m["transcript"]["status"] == "denied"])
    t_unavail = len([m for m in ms if m["transcript"]["status"] == "unavailable"])
    t_not = len([m for m in ms if m["transcript"]["status"] == "not_requested"])
    ev = list(b["evidence"].values())
    dup = []
    seen = {}
    for m in sorted(ms, key=lambda m: m["ref"]):
        key = (m["date"], tuple(sorted(m["participant_ids"])))
        if key in seen:
            dup.append("%s and %s" % (seen[key], m["ref"]))
        else:
            seen[key] = m["ref"]
    declined = len([p for p in b["pids"] if b["participants"][p]["consent"] == "declined"])
    return {
        "meetings_included": len(ms), "meetings_excluded": len(b["excluded_meetings"]),
        "excluded_reasons": sorted(set(x["reason"] for x in b["excluded_meetings"])),
        "possible_duplicate_notes": dup,
        "participants_included": a["n_included"], "participants_excluded_declined": declined,
        "transcripts_ok": t_ok, "transcripts_denied": t_denied, "transcripts_unavailable": t_unavail,
        "transcripts_not_requested": t_not,
        "evidence_items": len(ev), "evidence_from_transcripts": len([e for e in ev if e["source"] == "transcript"]),
        "evidence_from_notes": len([e for e in ev if e["source"] == "notes"]),
        "observed_behaviour_items": len([e for e in ev if e["kind"] == "did"]),
        "leading_answers": len([e for e in ev if e["leading"] is True]),
        "leading_flagged_by_pattern": len([e for e in ev if e["leading_by_pattern"]]),
        "question_wording_unknown": len([e for e in ev if e["leading"] is None and e["question"] is None]),
        "quotes_withheld_consent": len([e for e in ev if b["participants"][e["participant_id"]]["consent"] != "granted"]),
        "quotes_not_checked": len([e for e in ev if not e["checked"]]),
        "evidence_dropped_declined": b["warnings"]["evidence_dropped_declined"],
        "findings_dropped_no_evidence": a["dropped_findings"],
        "links_dropped": b["warnings"]["links_dropped"],
    }


def source_line(b):
    parts, gaps = [], []
    for key, label in (("meetings", "Meeting list"), ("transcripts", "Transcripts")):
        s = b["sources"][key]
        name = PROVIDER_LABELS.get(s["provider"] or "", "not set")
        text = "%s: %s, %s" % (label, name, s["status"].replace("_", " "))
        if s["folder"]:
            text += " (folder: %s)" % gt.md(s["folder"])
        parts.append(text)
        if s["status"] != "ok":
            gaps.append("%s %s%s" % (label.lower(), s["status"].replace("_", " "),
                                     " (%s)" % gt.md(s["detail"]) if s["detail"] else ""))
    return "; ".join(parts) + ".", gaps


def unknown_lines(ctx, cov):
    b, a = ctx.b, ctx.a
    lines = list(a["gaps"])
    unread = cov["transcripts_denied"] + cov["transcripts_unavailable"]
    if unread:
        lines.append("%s had no readable transcript; evidence from %s comes from notes, which paraphrase." % (
            plural(unread, "session"), "it" if unread == 1 else "them"))
    if not cov["observed_behaviour_items"]:
        lines.append("No observed behaviour was recorded; every finding rests on what participants said.")
    for q in b["open_questions"]:
        lines.append("%s%s" % (q["rq"] + ": " if q["rq"] else "", q["text"]))
    return lines


def render_markdown(ctx):
    b, a, config = ctx.b, ctx.a, ctx.config
    live, findings = a["findings"], b["findings"]
    cov = coverage(ctx)
    out = ["# User Research Synthesis \u2014 %s" % gt.md(b["study"]["name"]), ""]
    dates = sorted(m["date"] for m in b["meetings"].values())
    span = ""
    if dates and ctx.precision != "none":
        first, last = show_date(dates[0], ctx.precision), show_date(dates[-1], ctx.precision)
        span = " \u00b7 %s" % (first if first == last else "%s \u2013 %s" % (first, last))
    out.append("_Internal research report \u00b7 %s \u00b7 %s%s \u00b7 synthesised %s \u00b7 participants are "
               "pseudonymous (P1\u2026); redaction is pattern-based, so read it before sharing._" % (
                   plural(a["n_included"], "participant"),
                   plural(cov["meetings_included"], METHOD_LABELS[b["study"]["method"]].rstrip("s"),
                          METHOD_LABELS[b["study"]["method"]]) if b["study"]["method"] != "mixed"
                   else plural(cov["meetings_included"], "session"),
                   span, show_date(ctx.as_of, "day")))
    out.append("")
    if b["sources"]["meetings"]["status"] != "ok":
        out.append("> **Coverage incomplete** \u2014 the meeting list (%s) is %s. Findings below cover only the "
                   "interviews that could be read; this is not the whole study." % (
                       PROVIDER_LABELS.get(b["sources"]["meetings"]["provider"] or "", "recorder"),
                       b["sources"]["meetings"]["status"].replace("_", " ")))
        out.append("")
    patterns = [fid for fid in live if live[fid]["band"] != "single" and findings[fid]["type"] == "finding"]
    bands = dict((band, len([f for f in patterns if live[f]["band"] == band])) for band in BANDS)
    singles = [fid for fid in live if live[fid]["band"] == "single"]
    glance = "**At a glance** \u2014 %s; %s supported by %d+ participants (%d high, %d medium, %d low confidence%s)" % (
        plural(len(b["research_questions"]), "research question"), plural(len(patterns), "finding"),
        config["min_pattern_participants"], bands["high"], bands["medium"], bands["low"],
        ", %d contested" % bands["contested"] if bands["contested"] else "")
    pattern_opps = [o for o in a["opportunities"] if o["pattern"]]
    if pattern_opps:
        top = pattern_opps[0]
        glance += "; top opportunity by evidence: %s (%s): %s" % (
            top["id"], plural(len(top["supporters"]), "participant"), gt.md(top["statement"].rstrip(".")))
    glance += ". %s kept separate as single-participant signals." % plural(len(singles), "item")
    out.append(glance)
    out.append("")

    out.append("## Research questions")
    out.append("")
    for q in b["research_questions"]:
        mine = [fid for fid, f in findings.items() if f["rq"] == q["id"] and f["type"] == "finding" and fid in live]
        n_pat = len([fid for fid in mine if live[fid]["band"] != "single"])
        out.append("- **%s** %s \u2014 %s" % (q["id"], gt.md(q["text"]), plural(n_pat, "finding")))
    out.append("")

    out.append("## Findings by research question")
    out.append("")
    out.append("_Confidence counts distinct participants, never mentions. Answers to leading questions are weaker "
               "and do not count towards medium or high. See Method for the thresholds._")
    out.append("")
    for q in b["research_questions"]:
        out.append("### %s \u2014 %s" % (q["id"], gt.md(q["text"])))
        out.append("")
        mine = sorted_findings(ctx, [fid for fid, f in findings.items() if f["rq"] == q["id"]
                                     and f["type"] == "finding" and live.get(fid) and live[fid]["band"] != "single"])
        if not mine:
            out.append("No finding for this question is supported by %d or more participants." %
                       config["min_pattern_participants"])
            out.append("")
        for fid in mine:
            render_finding_block(ctx, findings[fid], live[fid], out)

    out.append("## Jobs, pains, workarounds and desired outcomes")
    out.append("")
    any_jtbd = False
    for ftype in ("job", "pain", "workaround", "desired_outcome"):
        mine = sorted_findings(ctx, [fid for fid, f in findings.items() if f["type"] == ftype])
        mine = [fid for fid in mine if live[fid]["band"] != "single"]
        if not mine:
            continue
        any_jtbd = True
        out.append("### %s" % TYPE_TITLES[ftype])
        out.append("")
        for fid in mine:
            f, x = findings[fid], live[fid]
            line = "- **%s** %s \u2014 %s \u00b7 %s (%s)" % (
                fid, gt.md(f["statement"]), BAND_LABELS[x["band"]], plural(len(x["supporters"]), "participant"),
                ids_text(x["supporters"]))
            if x["weak"]:
                line += ", %d only via leading questions" % len(x["weak"])
            if x["contra"]:
                line += " \u00b7 contradict: %d (%s)" % (len(x["contra"]), ids_text(x["contra"]))
            out.append(line)
            q = pick_quotes(b, f["supporting"], 1)
            if q:
                out.append("  - " + ctx.evidence_line(q[0]))
        out.append("")
    if not any_jtbd:
        out.append("None supported by %d or more participants." % config["min_pattern_participants"])
        out.append("")

    out.append("## Single-participant signals (not patterns)")
    out.append("")
    singles_sorted = sorted(singles, key=lambda fid: (FINDING_TYPES.index(findings[fid]["type"]), natural_key(fid)))
    if not singles_sorted:
        out.append("None.")
    for fid in singles_sorted[:config["max_single_signals"]]:
        f, x = findings[fid], live[fid]
        kind = "finding" if f["type"] == "finding" else TYPE_TITLES[f["type"]].lower().rstrip("s")
        if f["type"] == "job":
            kind = "job"
        line = "- **%s** (%s%s) %s \u2014 %s only" % (fid, kind, ", " + f["rq"] if f["rq"] else "",
                                                       gt.md(f["statement"]), ids_text(x["supporters"]))
        if x["contra"]:
            line += "; contradicted by %s" % ids_text(x["contra"])
        out.append(line)
        q = pick_quotes(b, f["supporting"], 1)
        if q:
            out.append("  - " + ctx.evidence_line(q[0]))
    if len(singles_sorted) > config["max_single_signals"]:
        out.append("- \u2026and %d more (see --format json)." % (len(singles_sorted) - config["max_single_signals"]))
    out.append("")

    out.append("## Differences by segment")
    out.append("")
    if not b["segments"]:
        out.append("No segments defined for this study.")
    elif not a["seg_diffs"]:
        out.append("No finding differs by %d+ percentage points between segments with %d+ participants each." % (
            int(round(config["segment_gap_min_share"] * 100)), config["segment_min_participants"]))
    else:
        segs = sorted(a["seg_members"], key=lambda s: b["segments"][s]["order"])
        segs = [s for s in segs if len(a["seg_members"][s]) >= config["segment_min_participants"]]
        out.append("_Share of each segment's participants supporting the finding. Listed when the gap is %d+ "
                   "percentage points; segments with fewer than %d participants are left out._" % (
                       int(round(config["segment_gap_min_share"] * 100)), config["segment_min_participants"]))
        out.append("")
        out.append("| Finding | %s |" % " | ".join("%s (n=%d)" % (gt.md(b["segments"][s]["label"]),
                                                                  len(a["seg_members"][s])) for s in segs))
        out.append("| --- | %s |" % " | ".join("---" for _ in segs))
        for d in a["seg_diffs"]:
            rows = dict((r_["segment"], r_) for r_ in d["rows"])
            cells = []
            for s in segs:
                r_ = rows.get(s)
                cells.append("%d/%d" % (r_["supporting"], r_["members"]) if r_ else "\u2014")
            out.append("| %s %s | %s |" % (d["finding"], gt.md(findings[d["finding"]]["statement"]), " | ".join(cells)))
    out.append("")

    out.append("## Surprises")
    out.append("")
    surprises = sorted_findings(ctx, [fid for fid, f in findings.items() if f["assumption"]])
    if not surprises:
        out.append("None recorded.")
    for fid in surprises:
        f, x = findings[fid], live[fid]
        out.append("- **%s** %s \u2014 %s, %s. We assumed: %s" % (
            fid, gt.md(f["statement"]), BAND_LABELS[x["band"]].lower() if x["band"] != "single"
            else "single participant (not a pattern)", plural(len(x["supporters"]), "participant"),
            gt.md(f["assumption"])))
    out.append("")

    out.append("## Opportunities, ranked by evidence")
    out.append("")
    if not a["opportunities"]:
        out.append("None recorded.")
    else:
        out.append("_Ranked by distinct participants supporting the linked findings (not counting answers to "
                   "leading questions), then all supporters, then fewest contradicting, then the strongest linked "
                   "confidence. Not ranked by anyone's opinion of value or effort._")
        out.append("")
        out.append("| # | Opportunity | Based on | Support | Contradict | Best confidence |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for n, o in enumerate(a["opportunities"]):
            support = "%d (%s)" % (len(o["supporters"]), ids_text(o["supporters"]))
            if o["weak"]:
                support += ", %d only via leading questions" % len(o["weak"])
            if not o["pattern"]:
                support += ", not a pattern"
            out.append("| %d | %s %s | %s | %s | %s | %s |" % (
                n + 1, o["id"], gt.md(o["statement"]), ", ".join(o["findings"]), support,
                "%d (%s)" % (len(o["contra"]), ids_text(o["contra"])) if o["contra"] else "0",
                BAND_LABELS[o["best"]].replace(" (not a pattern)", "") if o["best"] else "no usable evidence"))
    out.append("")

    out.append("## What we still don't know")
    out.append("")
    gap_lines = unknown_lines(ctx, cov)
    if not gap_lines:
        out.append("Nothing recorded.")
    for g in gap_lines:
        out.append("- " + gt.md(g))
    out.append("")

    out.append("## Participants")
    out.append("")
    out.append("| ID | Segment | %s | Evidence from | Consent | Evidence items |" % (
        "Interview" if ctx.precision == "none" else "Interview date"))
    out.append("| --- | --- | --- | --- | --- | --- |")
    for pid in a["included"]:
        p = b["participants"][pid]
        ms = sorted([b["meetings"][mid] for mid in p["meetings"]], key=lambda m: m["ref"])
        when = ", ".join("%s%s" % (show_date(m["date"], ctx.precision) + " " if ctx.precision != "none" else "",
                                   md_link(m["ref"], ctx.url(m["meeting_id"]))) for m in ms)
        src = []
        for m in ms:
            src.append("transcript" if m["transcript"]["status"] == "ok" else
                       ("notes (transcript %s)" % m["transcript"]["status"].replace("_", " ")))
        n_ev = len([e for e in b["evidence"].values() if e["participant_id"] == pid])
        seg = b["segments"][p["segment"]]["label"] if p["segment"] else "\u2014"
        out.append("| %s | %s | %s | %s | %s | %d |" % (pid, gt.md(seg), when, ", ".join(sorted(set(src))),
                                                      CONSENT_LABELS[p["consent"]], n_ev))
    if cov["participants_excluded_declined"]:
        out.append("")
        out.append("%s excluded because consent was declined (not listed; %s dropped)." % (
            plural(cov["participants_excluded_declined"], "participant"),
            plural(cov["evidence_dropped_declined"], "evidence item")))
    out.append("")

    out.append("## Method and coverage")
    out.append("")
    out.append("- **Interviews**: %d included%s%s." % (
        cov["meetings_included"],
        "; %d listed but excluded (%s)" % (cov["meetings_excluded"],
                                           "; ".join(gt.md(x) for x in cov["excluded_reasons"]))
        if cov["meetings_excluded"] else "",
        "; possible duplicate notes of one session: %s (counted once per participant either way)" %
        ", ".join(cov["possible_duplicate_notes"]) if cov["possible_duplicate_notes"] else ""))
    out.append("- **Transcripts**: %d read, %d denied, %d unavailable, %d not requested." % (
        cov["transcripts_ok"], cov["transcripts_denied"], cov["transcripts_unavailable"],
        cov["transcripts_not_requested"]))
    out.append("- **Evidence**: %s (%d from transcripts, %d from notes; %d observed behaviour). %s to leading "
               "questions (%d flagged by the question-wording pattern); %d without the question's wording. %s "
               "withheld for consent; %d not checked against source text because the text was not in the bundle." % (
                   plural(cov["evidence_items"], "item"), cov["evidence_from_transcripts"], cov["evidence_from_notes"],
                   cov["observed_behaviour_items"], plural(cov["leading_answers"], "answer"),
                   cov["leading_flagged_by_pattern"], cov["question_wording_unknown"],
                   plural(cov["quotes_withheld_consent"], "quote"), cov["quotes_not_checked"]))
    if cov["findings_dropped_no_evidence"] or cov["links_dropped"]:
        out.append("- **Dropped**: %s%s." % (
            "findings with no evidence left after consent: %s" % ", ".join(cov["findings_dropped_no_evidence"])
            if cov["findings_dropped_no_evidence"] else "",
            ("; " if cov["findings_dropped_no_evidence"] else "") + "%s not shown (unsafe, or containing a listed "
            "name, organisation or email)" % plural(cov["links_dropped"], "link") if cov["links_dropped"] else ""))
    out.append("- **Confidence** (distinct participants; answers to leading questions only count towards low): "
               "High = %d+ supporting and at least %d%% of the %d participants, with contradicting participants at "
               "most %d%% of supporting; Medium = %d+; Low = %d+ in total; Contested = at least as many contradict as "
               "support; Single = fewer than %d, never shown as a pattern." % (
                   config["high_min_participants"], int(round(config["high_min_share_of_participants"] * 100)),
                   a["n_included"], int(round(config["high_max_contradicting_ratio"] * 100)),
                   config["medium_min_participants"], config["min_pattern_participants"],
                   config["min_pattern_participants"]))
    out.append("- **Anonymity**: participants appear as P1, P2\u2026 and sessions as M1, M2\u2026; names, organisations and "
               "emails listed privately in the bundle (and the parts of listed full names) are replaced with [name] or "
               "[organisation], and email addresses, phone numbers and web addresses with a placeholder, in every "
               "displayed field. This is pattern-based and can miss unlisted names, nicknames, misspellings, "
               "look-alike letters and unusual formats; it can also blank ordinary words that match a listed name. "
               "%s" % ("Meeting links open the original notes, which are not anonymised." if ctx.links
                       else "Meeting links are left out (--no-links)."))
    srcs, gaps = source_line(b)
    out.append("- **Sources**: %s" % srcs)
    out.append("- **Gaps**: %s." % ("; ".join(gaps) if gaps else "none reported"))
    out.append("")
    out.append("_Internal research document. The agent extracted and coded the evidence; the script validated "
               "citations and quotes, counted participants, computed confidence and pseudonymised the text._")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- json and csv


def json_ready(ctx, markdown):
    b, a = ctx.b, ctx.a

    def ev(e):
        consent = b["participants"][e["participant_id"]]["consent"]
        m = b["meetings"][e["meeting_id"]]
        return {"evidence_id": e["id"], "participant_id": e["participant_id"], "meeting_ref": m["ref"],
                "meeting_id": e["meeting_id"], "meeting_url": ctx.url(e["meeting_id"]), "kind": e["kind"],
                "source": e["source"], "text": e["text"] if consent == "granted" else None,
                "withheld": consent != "granted", "checked_against_source": e["checked"],
                "leading_question": e["leading"], "leading_by_pattern": e["leading_by_pattern"],
                "question": e["question"] if consent == "granted" else None}

    findings = []
    for fid in sorted(a["findings"], key=lambda fid: a["findings"][fid]["sort"]):
        f, x = b["findings"][fid], a["findings"][fid]
        findings.append({
            "id": fid, "type": f["type"], "rq": f["rq"], "statement": f["statement"], "confidence": x["band"],
            "is_pattern": x["band"] != "single",
            "supporting_participants": sorted(x["supporters"], key=natural_key),
            "supporting_not_leading": sorted(x["strong"], key=natural_key),
            "supporting_only_via_leading": sorted(x["weak"], key=natural_key),
            "contradicting_participants": sorted(x["contra"], key=natural_key),
            "said": sorted(x["said"], key=natural_key), "did": sorted(x["did"], key=natural_key),
            "by_segment": [{"segment": s["label"], "participants": s["members"], "supporting": s["supporting"],
                            "contradicting": s["contradicting"]} for s in x["by_segment"]],
            "meeting_refs": sorted(set(b["meetings"][b["evidence"][e]["meeting_id"]]["ref"]
                                       for e in f["supporting"] + f["contradicting"]), key=natural_key),
            "surprise_assumption": f["assumption"],
            "evidence": {"supporting": [ev(b["evidence"][e]) for e in f["supporting"]],
                         "contradicting": [ev(b["evidence"][e]) for e in f["contradicting"]]}})
    return {
        "schema": "user-research-synthesis/1", "as_of": ctx.as_of.isoformat(),
        "study": {"name": b["study"]["name"], "method": b["study"]["method"],
                  "research_questions": b["research_questions"]},
        "participants": [{"id": pid, "segment": (b["segments"][b["participants"][pid]["segment"]]["label"]
                                                 if b["participants"][pid]["segment"] else None),
                          "consent": b["participants"][pid]["consent"],
                          "meetings": [{"ref": b["meetings"][mid]["ref"],
                                        "date": (b["meetings"][mid]["date"].isoformat()
                                                 if ctx.precision == "day" else show_date(b["meetings"][mid]["date"],
                                                                                          ctx.precision)),
                                        "url": ctx.url(mid)}
                                       for mid in sorted(b["participants"][pid]["meetings"],
                                                         key=lambda x: b["meetings"][x]["ref"])]}
                         for pid in a["included"]],
        "findings": findings,
        "segment_differences": [{"finding": d["finding"], "gap": round(d["gap"], 4),
                                 "segments": [{"segment": r_["label"], "participants": r_["members"],
                                               "supporting": r_["supporting"]} for r_ in d["rows"]]}
                                for d in a["seg_diffs"]],
        "opportunities": [{"rank": n + 1, "id": o["id"], "statement": o["statement"], "findings": o["findings"],
                           "supporting_participants": sorted(o["supporters"], key=natural_key),
                           "supporting_not_leading": sorted(o["strong"], key=natural_key),
                           "contradicting_participants": sorted(o["contra"], key=natural_key),
                           "best_confidence": o["best"], "is_pattern": o["pattern"]}
                          for n, o in enumerate(a["opportunities"])],
        "still_unknown": unknown_lines(ctx, coverage(ctx)),
        "coverage": coverage(ctx),
        "sources": dict((k, {"provider": v["provider"], "status": v["status"], "detail": v["detail"],
                             "folder": v["folder"]}) for k, v in b["sources"].items()),
        "markdown": markdown,
    }


CSV_COLUMNS = ["study", "card_id", "type", "research_question", "statement", "confidence", "supporting_count",
               "supporting_participants", "contradicting_count", "contradicting_participants",
               "only_via_leading_questions", "segments", "quote_1", "quote_1_participant", "quote_1_source",
               "quote_2", "quote_2_participant", "quote_2_source", "meeting_refs", "meeting_links",
               "surprise_assumption"]


def evidence_source(e):
    """CSV label: transcript quote, or a notes excerpt (not verbatim speech); observed behaviour and
    leading-question answers are marked."""
    label = "transcript quote" if e["source"] == "transcript" else "notes excerpt, not verbatim"
    if e["kind"] == "did":
        label = "observed, " + label
    if e["leading"] is True:
        label += ", answer to a leading question"
    return label


def render_csv(ctx):
    """Insight cards: one row per finding supported by min_pattern_participants or more. Plain text, redacted,
    formula characters neutralised. Single-participant signals are not exported as cards."""
    b, a = ctx.b, ctx.a
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(CSV_COLUMNS)
    rq_text = dict((q["id"], q["text"]) for q in b["research_questions"])
    order = sorted([fid for fid in a["findings"] if a["findings"][fid]["band"] != "single"],
                   key=lambda fid: (FINDING_TYPES.index(b["findings"][fid]["type"]), a["findings"][fid]["sort"]))
    for fid in order:
        f, x = b["findings"][fid], a["findings"][fid]
        quotes = pick_quotes(b, f["supporting"], 2)
        q = [(gt.shorten(e["text"], ctx.config["max_quote_chars"]), e["participant_id"], evidence_source(e))
             for e in quotes]
        q += [("", "", "")] * (2 - len(q))
        mids = sorted(set(b["evidence"][e]["meeting_id"] for e in f["supporting"] + f["contradicting"]),
                      key=lambda m: b["meetings"][m]["ref"])
        segs = "; ".join("%s %d/%d" % (s["label"], s["supporting"], s["members"]) for s in x["by_segment"])
        row = [b["study"]["name"], fid, f["type"], ("%s %s" % (f["rq"], rq_text[f["rq"]])) if f["rq"] else "",
               f["statement"], x["band"], len(x["supporters"]), " ".join(sorted(x["supporters"], key=natural_key)),
               len(x["contra"]), " ".join(sorted(x["contra"], key=natural_key)),
               " ".join(sorted(x["weak"], key=natural_key)), segs, q[0][0], q[0][1], q[0][2], q[1][0], q[1][1], q[1][2],
               " ".join(b["meetings"][m]["ref"] for m in mids),
               " ".join(u for u in (ctx.url(m) for m in mids) if u), f["assumption"] or ""]
        w.writerow([csv_cell(c) for c in row])
    return buf.getvalue()


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
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, stream.fileno())
        except (AttributeError, OSError, ValueError):
            pass
        return False


def has_surrogate(text):
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("arguments: %s (see --help)" % message)


def main(argv=None):
    utf8_streams()
    parser = Parser(description="Render a user research synthesis from one JSON bundle (see DATA_CONTRACT.md).")
    parser.add_argument("--bundle", type=Path, help="Bundle JSON.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="synthesis.json")
    parser.add_argument("--as-of", help="Run date (YYYY-MM-DD) or ISO datetime with offset. The only 'now'.")
    parser.add_argument("--format", choices=FORMATS, default="markdown",
                        help="markdown report (default), json (structured, includes the markdown) or csv "
                             "(insight cards).")
    parser.add_argument("--date-precision", choices=DATE_PRECISIONS,
                        help="Override date_precision from the config: day, month or none.")
    parser.add_argument("--no-links", action="store_true",
                        help="Leave out meeting links (they open the original, non-anonymised notes).")
    try:
        args = parser.parse_args(argv)
        if args.bundle is None:
            raise InputError("--bundle is required")
        if not args.as_of:
            raise InputError("--as-of is required, e.g. --as-of 2026-10-02")
        as_of = parse_as_of(args.as_of)
        config = load_config(args.config)
        if args.date_precision:
            config["date_precision"] = args.date_precision
        bundle = load_bundle(args.bundle, config)
        late = sorted(m["meeting_id"] for m in list(bundle["meetings"].values()) + bundle["excluded_meetings"]
                      if m["date"] > as_of)
        if late:
            raise InputError("bundle %s: $.meetings: meeting %s is dated after --as-of; a synthesis cannot cite the "
                             "future" % (args.bundle, late[0]))
        analysis = analyse(bundle, config)
        ctx = Ctx(bundle, analysis, config, as_of, not args.no_links)
        markdown = render_markdown(ctx)
        if args.format == "markdown":
            output = markdown
        elif args.format == "json":
            output = json.dumps(json_ready(ctx, markdown), indent=2, ensure_ascii=False) + "\n"
        else:
            output = render_csv(ctx)
        if has_surrogate(output):
            raise InputError("bundle %s: contains an unpaired surrogate character; fix the text encoding"
                             % args.bundle)
    except InputError as exc:
        emit(sys.stderr, "Error: %s\n" % exc)
        return EXIT_INPUT
    if not emit(sys.stdout, output):
        emit(sys.stderr, "Error: the output could not be written (stdout closed or broken pipe).\n")
        return EXIT_OUTPUT
    return 0


if __name__ == "__main__":
    sys.exit(main())
