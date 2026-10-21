#!/usr/bin/env python3
"""Render a DRAFT interview scorecard (one interview) or panel debrief (all
interviews for one candidate) from a role rubric and one normalised JSON bundle.

Stdlib only, offline, deterministic, Python 3.8+. It never calls an API. The
only date it uses is --as-of; the only clock it reads is the age of the log's
lock file. What it enforces (rubric gate, no evidence -> no score, evidence
kinds, the fairness and hiring-decision scan, consent, personal data, ATS
draft, panel readiness, the two-phase log) is documented in SKILL.md and
DATA_CONTRACT.md; thresholds and term lists live in scorecard.json. The scan is
pattern-based: it catches common wording, not every phrasing.

Exit codes: 0 output (or a one-line "Skipped: ..." / confirmation), 2 bad
input (message names the file and JSON path; nothing is printed on stdout),
3 no usable rubric, 4 a scheduled run found the key pending, 5 another run
holds the log lock, 6 the output could not be written.
"""

import argparse
import copy
import hashlib
import html
import json
import os
import re
import stat
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "scorecard.json"

EXIT_INPUT = 2
EXIT_RUBRIC = 3
EXIT_PENDING = 4
EXIT_LOCKED = 5
EXIT_OUTPUT = 6  # stdout closed or broken pipe: nothing was delivered
LOCK_STALE_MINUTES = 15  # a lock file older than this was left by a crashed run and is removed
SCORES = ["1", "2", "3", "4"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SOURCE_STATUSES = ["ok", "unavailable", "denied", "not_configured", "not_requested"]
ACCESS_STATUSES = ["ok", "unavailable", "denied", "not_requested"]
EVIDENCE_KINDS = ["quote", "note", "summary"]
KIND_LABELS = {"quote": "transcript quote", "note": "interviewer note", "summary": "AI summary"}
# What an evidence item shows (evidence_kind, tagged by the agent). Anything else, or no tag, is never rendered.
EVIDENCE_TYPES = {"behaviour_example": "an example of past behaviour", "skill_demonstration": "a skill shown in the "
                  "interview", "planned_question_answer": "an answer to a planned question",
                  "work_output_observation": "the interviewer's observation of the candidate's work output"}
SCORE_STATUSES = ["suggested", "interviewer_confirmed"]
PROVIDER_LABELS = {"granola": "Granola", "gong": "Gong", "fathom": "Fathom", "fireflies": "Fireflies",
                   "otter": "Otter", "zoom": "Zoom", "upload": "uploaded file", "pasted": "pasted text",
                   "greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "Lever", "none": "none"}
CONFIG_INTS = ["max_quote_chars", "max_text_chars", "disagreement_spread", "min_competencies",
               "max_competencies", "max_evidence_single", "max_evidence_panel"]
CONFIG_KEYS = CONFIG_INTS + ["category_labels", "protected_terms", "proxy_terms", "decision_terms",
                             "forbidden_candidate_keys", "forbidden_bundle_keys", "ats_providers", "term_classes",
                             "job_related", "personal_context"]
NOT_JOB_RELATED = "not_job_related"  # fairness flag type: evidence the job-relatedness gate could not confirm
RATIONALE_NOT_JOB_RELATED = "rationale_not_job_related"  # a rationale the gate could not confirm: score withdrawn
GAP_NOT_JOB_RELATED = "gap_not_job_related"  # a gap note the gate could not confirm: dropped
DECISION = "hiring_decision"
NOT_A_TOPIC = {"compensation"}  # withdrawn from evidence like the others, but not a "question of concern" topic
DECISION_SOURCES = ["verdict_clauses", "any", "authored", "candidate_quote"]
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}\Z")
SAFE_KEY = re.compile(r"^(single|panel):[A-Za-z0-9:._-]{1,300}\Z")
WHITESPACE = re.compile(r"\s+")
TIMESTAMP = re.compile(r"^(?:[0-9]{1,2}:)?[0-9]{1,2}:[0-9]{2}\Z")
URL_SAFE = "/:?#@!$&'*+,;=%~"
LOG_TITLE = "# Interview scorecard log"
LOG_HEADER = "| key | kind | role_id | candidate_ref | interviews | drafted_at | status |"
LOG_SEPARATOR = "|---|---|---|---|---|---|---|"
LOG_STATUSES = ["pending", "delivered", "skipped"]
LISTED = re.compile(r"^Listed through: ([0-9]{4}-[0-9]{2}-[0-9]{2})$")
FIRST_LOOKBACK_DAYS = 7  # the first --next-window, with no completed listing in the log, looks back this far
WARN = "⚠️"


# R12 export contract. These are product vocabulary, not learned input words.
OBS_ACTIONS = {
    "reconcile": "Reconciled", "analyze": "Analyzed", "trace": "Traced",
    "validate": "Validated", "correct": "Corrected", "document": "Documented",
    "automate": "Automated", "explain": "Explained", "prioritize": "Prioritized",
    "test": "Tested", "design": "Designed", "implement": "Implemented",
    "review": "Reviewed", "plan": "Planned", "revise": "Revised",
    "coordinate": "Coordinated", "investigate": "Investigated",
}
OBS_OBJECTS = {
    "bank-reconciliation": "bank reconciliation",
    "accounts-payable": "accounts payable", "accounts-receivable": "accounts receivable",
    "ledger": "the ledger", "close-checklist": "the close checklist",
    "variance-report": "a variance report", "forecast-model": "a forecast model",
    "data-validation": "data validation", "reconciliation-control": "a reconciliation control",
    "stakeholder-update": "a stakeholder update", "feedback-plan": "a feedback plan",
    "work-plan": "a work plan", "workflow": "a workflow", "system-test": "a system test",
    "technical-design": "a technical design", "incident-response": "incident response",
    "research-plan": "a research plan", "customer-requirement": "a customer requirement",
    "delivery-plan": "a delivery plan", "quality-check": "a quality check",
}
OBS_RESULTS = {
    "completed": "completed", "issue-identified": "issue identified",
    "corrected": "corrected", "validated": "validated", "documented": "documented",
    "explained": "explained", "proposed": "proposed", "unresolved": "unresolved",
    "insufficient-detail": "insufficient detail",
}
# Only template text is copied into generated output. The source must still be reviewed.
STRUCTURED_ONLY = "structured_observation_required"

class InputError(Exception):
    """Bad input; the message names the file and JSON path."""


class RubricError(Exception):
    """No usable rubric; the agent must build or approve one first."""


class PendingError(Exception):
    """A scheduled run found its key drafted but not confirmed delivered."""


class LockedError(Exception):
    """Another run holds the log lock."""


# --------------------------------------------------------------------------- reading


class Reader:
    """Typed accessors that report '<label>: <json.path>: problem'."""

    def __init__(self, label, error=InputError):
        self.label = label
        self.error = error
        self.dropped_links = []  # JSON paths of links that were not http(s) URLs with a host

    def fail(self, path, problem):
        return self.error("{}: {}: {}".format(self.label, path, problem))

    def obj(self, value, path):
        if not isinstance(value, dict):
            raise self.fail(path, "required object is missing" if value is None else "must be an object")
        return value

    def lst(self, value, path, required=False):
        if value is None:
            if required:
                raise self.fail(path, "is required (a list)")
            return []
        if not isinstance(value, list):
            raise self.fail(path, "must be a list")
        return value

    def text(self, parent, key, path, required=True):
        value = parent.get(key)
        if isinstance(value, str):
            # One line; a run of 2+ whitespace characters stays a double space so the scanner still sees word
            # breaks in spaced-out text ("h i r e  h e r").
            value = WHITESPACE.sub(lambda m: "  " if len(m.group(0)) > 1 else " ", display_clean(value).strip())
        if value is None or value == "":
            if required:
                raise self.fail("{}.{}".format(path, key), "is required but missing or empty")
            return None
        if not isinstance(value, str):
            raise self.fail("{}.{}".format(path, key), "must be a string, got {}".format(type_name(value)))
        return value

    def slug(self, parent, key, path):
        value = self.text(parent, key, path)
        if not SLUG.match(value):
            raise self.fail("{}.{}".format(path, key), "{} must be lower-case letters, digits, '-' or '_'".format(
                shown(value)))
        return value

    def ident(self, parent, key, path, required=True):
        value = self.text(parent, key, path, required=required)
        if value is not None and not SAFE_ID.match(value):
            raise self.fail("{}.{}".format(path, key), "must be 1-128 characters of letters, digits, ':', '.', '_' "
                            "or '-' (no spaces or '|'), starting with a letter or digit")
        return value

    def enum(self, parent, key, path, allowed, default=None):
        value = parent.get(key, default)
        if not isinstance(value, str) or value not in allowed:
            raise self.fail("{}.{}".format(path, key), "must be one of {}, got {}".format(
                ", ".join(allowed), shown(value)))
        return value

    def boolean(self, parent, key, path, default=None, nullable=False):
        if key not in parent:
            if default is None:
                raise self.fail("{}.{}".format(path, key), "is required (true or false{})".format(
                    ", or null when unknown" if nullable else ""))
            return default
        value = parent[key]
        if value is None and nullable:
            return None
        if not isinstance(value, bool):
            raise self.fail("{}.{}".format(path, key), "must be true or false{}, got {}".format(
                " or null" if nullable else "", shown(value)))
        return value

    def integer(self, parent, key, path, low=0):
        value = parent.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < low:
            raise self.fail("{}.{}".format(path, key), "must be an integer >= {}, got {}".format(low, shown(value)))
        return value

    def day(self, parent, key, path):
        value = self.text(parent, key, path)
        parsed = parse_day(value)
        if parsed is None:
            raise self.fail("{}.{}".format(path, key), "{} is not a YYYY-MM-DD date".format(shown(value)))
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
            self.dropped_links.append("{}.{}".format(path, key))
        return clean


def type_name(value):
    return {dict: "an object", list: "a list", bool: "true/false", int: "a number", float: "a number"}.get(
        type(value), type(value).__name__)


ECHO_SAFE = re.compile(r"^[A-Za-z0-9 ._:/-]{1,40}$")
ECHO = {"scanner": None}  # set once the config is loaded


def shown(value):
    """A short, safe rendering of a bad value for an error message. A string is echoed only when it is short, plain
    (letters, digits, space . _ : / -) and the scanner finds nothing in it; otherwise only its length is given."""
    if isinstance(value, str):
        scanner = ECHO["scanner"]
        if ECHO_SAFE.match(value) and scanner is not None and not scanner.scan(value, "gap") and \
                not has_contacts(value):  # "gap": the authored checks plus the personal-context vocabularies
            return repr(value)
        return "a {}-character value (not shown)".format(len(value))
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    return type_name(value)


def display_clean(text):
    """Remove invisible format characters (bidi overrides, zero-width) and control characters from text that will be
    shown (line breaks and tabs become spaces)."""
    return "".join(" " if ch in "\t\n\r\x0b\x0c" else ch for ch in text
                   if ch == "\u200d" or ch in "\t\n\r\x0b\x0c" or unicodedata.category(ch) not in ("Cf", "Cc"))


def safe_url(value):
    """An absolute http(s) URL with a host, percent-encoded for markdown; None otherwise."""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or not re.match(r"^[A-Za-z0-9.-]+$", host):
        return None
    return quote(value, safe=URL_SAFE)


def parse_day(value):
    if not isinstance(value, str) or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", value):
        return None
    try:
        return date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return None


def reject_constant(name):
    raise ValueError("{} is not a valid JSON number".format(name))


def surrogate_path(data):
    """JSON path of the first string (value or key) holding an unpaired surrogate, or None. Iterative, so a deeply
    nested file can't overflow the stack."""
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
                shown_key = key if re.match(r"^[A-Za-z0-9_]{1,40}\Z", key) else "<key>"
                stack.append((value[key], "{}.{}".format(path, shown_key)))
        elif isinstance(value, list):
            stack.extend((item, "{}[{}]".format(path, i)) for i, item in enumerate(value))
    return None


def load_json(path, label, error=InputError):
    path = Path(path)
    if not path.is_file():
        raise error("{}: file not found: {}".format(label, path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
        bad = surrogate_path(data)
        if bad:
            raise error("{}: {}: {}: contains an unpaired surrogate escape (\\ud800-\\udfff), which is not text; fix "
                        "the export".format(label, path, bad))
        return data
    except json.JSONDecodeError as exc:
        raise error("{}: {} is not valid JSON (line {}, column {}: {})".format(
            label, path, exc.lineno, exc.colno, exc.msg)) from None
    except UnicodeDecodeError:
        raise error("{}: {} is not UTF-8 text".format(label, path)) from None
    except ValueError as exc:
        raise error("{}: {} is not valid JSON ({})".format(label, path, exc)) from None
    except RecursionError:
        raise error("{}: {} is nested too deeply".format(label, path)) from None
    except OSError as exc:
        raise error("{}: {} cannot be read ({})".format(label, path, exc.strerror or exc)) from None


def load_config(path):
    config = load_json(path, "config")
    r = Reader("config {}".format(path))
    r.obj(config, "$")
    missing = [k for k in CONFIG_KEYS if k not in config]
    if missing:
        raise InputError("config {}: missing key(s): {}".format(path, ", ".join(missing)))
    for key in CONFIG_INTS:
        r.integer(config, key, "$", low=1)
    labels = r.obj(config["category_labels"], "$.category_labels")
    for key in sorted(labels):
        r.text(labels, key, "$.category_labels")
    if DECISION not in labels:
        raise r.fail("$.category_labels." + DECISION, "is required")
    for key in ["forbidden_candidate_keys", "forbidden_bundle_keys"]:
        if not all(isinstance(x, str) for x in r.lst(config[key], "$." + key, required=True)):
            raise r.fail("$." + key, "must be a list of strings")
    ats = r.obj(config["ats_providers"], "$.ats_providers")
    for key in sorted(ats):
        r.text(ats, key, "$.ats_providers")
    groups = {}
    for group in ["protected_terms", "proxy_terms"]:
        r.obj(config[group], "$." + group)
        for category, terms in sorted(config[group].items()):
            if category not in labels or category == DECISION or category in groups:
                raise r.fail("$.{}.{}".format(group, category), "needs its own entry in $.category_labels "
                             "(and may not be {!r} or repeat another category)".format(DECISION))
            groups[category] = (group, term_list(r, terms, "$.{}.{}".format(group, category)))
    decision = r.obj(config["decision_terms"], "$.decision_terms")
    if sorted(decision) != sorted(DECISION_SOURCES):
        raise r.fail("$.decision_terms", "must have exactly the keys {}".format(", ".join(DECISION_SOURCES)))
    decision = {k: term_list(r, decision[k], "$.decision_terms." + k) for k in DECISION_SOURCES}
    classes = r.obj(config["term_classes"], "$.term_classes")
    CLASSES.clear()
    for name in sorted(classes):
        if not re.match(r"^[a-z_]+\Z", name):
            raise r.fail("$.term_classes", "class names use a-z and _ only")
        terms = term_list(r, classes[name], "$.term_classes." + name)
        CLASSES[name] = "(?:" + "|".join("(?:{})".format(t) for t in terms) + ")"
    for name, pattern in sorted(CLASSES.items()):
        if CLASS_REF.search(pattern):
            raise r.fail("$.term_classes." + name, "a class may not refer to another class")
        try:
            re.compile(pattern)
        except (re.error, OverflowError, RecursionError) as exc:
            raise r.fail("$.term_classes." + name, "bad pattern ({})".format(exc)) from None
    for group in ["protected_terms", "proxy_terms", "decision_terms"]:
        for key, terms in sorted(config[group].items()):
            for i, term in enumerate(terms if isinstance(terms, list) else []):
                for ref in CLASS_REF.findall(term) if isinstance(term, str) else []:
                    if ref not in CLASSES:
                        raise r.fail("$.{}.{}[{}]".format(group, key, i), "unknown term class {{c:{}}}".format(ref))
    personal = personal_context(r, r.obj(config["personal_context"], "$.personal_context"), labels)
    config["_gate"] = Gate(r, r.obj(config["job_related"], "$.job_related"))
    spelling = r.obj(config.get("spelling_words", {"words": [], "not": []}), "$.spelling_words")
    for key in ["words", "not"]:
        words = r.lst(spelling.get(key), "$.spelling_words." + key, required=True)
        if not all(isinstance(w, str) and re.match(r"^[a-z]+(?:'[a-z]+)?\Z", w) for w in words):
            raise r.fail("$.spelling_words." + key, "must be a list of lower-case words")
    config["_scanner"] = Scanner(groups, decision, r, spelling={k: spelling[k] for k in ["words", "not"]},
                                 personal=personal)
    config["_scanner"].gate = config["_gate"]  # the ranking check reads names against the work lexicon
    ECHO["scanner"] = config["_scanner"]
    config["_categories"] = sorted(c for c in groups if c not in NOT_A_TOPIC)
    return config


def term_list(r, terms, path):
    terms = r.lst(terms, path, required=True)
    for i, term in enumerate(terms):
        if not isinstance(term, str) or not term.strip():
            raise r.fail("{}[{}]".format(path, i), "must be a non-empty pattern string")
    return terms


def personal_context(r, value, labels):
    """$.personal_context: category-level vocabularies of the candidate's personal life (body and medical care,
    pregnancy and family, life stage, faith, origin, orientation and gender identity, service, union, offences,
    politics, pay, looks and personality), as {"terms": {category: [pattern, ...]}, "cased_terms": {...}}. Each
    pattern is a whole word or phrase (it can't match inside a longer word) and is matched anywhere in an evidence
    item, rationale or gap note, so every clause must be clean of it whatever the job-relatedness gate says. Unlike
    protected_terms, these are not sentence constructions: a single reference ("kidney transplant", "trimester",
    "pension age", "PAC") is enough. cased_terms are matched case-sensitively on the unfolded text ("Lent", "Mass").
    A match is read in its work sense instead (not withheld) when a work or organisation head follows it in the same
    clause ($.personal_context.work_heads: "maternity leave accruals", "an immigration law firm"; adjacent_heads only
    directly after it), when a third party ($.personal_context.third_party) is the subject of its clause ("Our
    controller got married", "while a colleague was on parental leave"), or when a trade verb governs it
    ($.personal_context.trade_verbs: "We sold insulin pumps"). Categories in no_exemption (politics) never take an
    exemption."""
    out = {"patterns": []}
    for key in ("work_heads", "adjacent_heads", "third_party", "third_party_things", "third_party_aux", "trade_verbs",
                "no_exemption"):
        words = r.lst(value.get(key), "$.personal_context." + key, required=True)
        for i, w in enumerate(words):
            if not isinstance(w, str) or not re.match(r"^[a-z0-9_]+(?:'[a-z]*)?\Z", w):
                raise r.fail("$.personal_context.{}[{}]".format(key, i), "must be a lower-case word")
        out[key] = set(words)
    for category in sorted(out["no_exemption"]):
        if category not in labels:
            raise r.fail("$.personal_context.no_exemption", "{!r} is not in $.category_labels".format(category))
    for key, cased in (("terms", False), ("cased_terms", True)):
        groups = r.obj(value.get(key), "$.personal_context." + key)
        for category in sorted(groups):
            path = "$.personal_context.{}.{}".format(key, category)
            if category not in labels or category == DECISION:
                raise r.fail(path, "needs its own entry in $.category_labels (and may not be {!r})".format(DECISION))
            terms = term_list(r, groups[category], path)
            for i, term in enumerate(terms):
                for ref in CLASS_REF.findall(term):
                    if ref not in CLASSES:
                        raise r.fail("{}[{}]".format(path, i), "unknown term class {{c:{}}}".format(ref))
            out["patterns"].append((category, cased, terms, path))
    return out


# --------------------------------------------------------------------------- scanning

# Applied before matching, so look-alike spellings hit the same terms. Lower-case targets: text is case-folded first.
CONFUSABLES = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "г": "r", "е": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "п": "n", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
    "ь": "b", "ѕ": "s", "і": "i", "ї": "i", "ј": "j", "һ": "h", "ԁ": "d",
    "ԛ": "q", "ԝ": "w", "ӏ": "l", "ү": "y", "ҳ": "x",
    # Greek
    "α": "a", "β": "b", "γ": "y", "δ": "d", "ε": "e", "ζ": "z", "η": "n",
    "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "σ": "o", "ς": "c",
    "τ": "t", "υ": "u", "χ": "x", "ω": "w", "ϲ": "c", "ϳ": "j", "μ": "u",
    # Armenian
    "օ": "o", "ս": "u", "ո": "n", "հ": "h", "ց": "g",
    # Latin look-alikes and small capitals
    "ı": "i", "ȷ": "j", "ɑ": "a", "ɡ": "g", "ɩ": "i", "ɪ": "i", "ʀ": "r",
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ɢ": "g", "ʜ": "h",
    "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p",
    "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
    # apostrophes and separators
    "’": "'", "‘": "'", "ʼ": "'", "′": "'", "`": "'", "−": "-",
})
INVISIBLE = {"ᅟ", "ᅠ", "ㅤ", "ﾠ", "⠀"}
# One letter or digit at a time with the same separator ("h i r e", "h.i.r.e", "1 9 8 5"). Runs of three or more
# end before the next letter; two-letter runs ("a b test") only join when no letter follows the next separator.
SPACED_LETTERS = re.compile(r"(?<![^\W_])(?:[^\W_]([ .*·•])(?:[^\W_]\1)+[^\W_](?![^\W_])"
                            r"|[^\W_]([ .*·•])[^\W_](?![^\W_]|[ .*·•][^\W_]))")
MARKUP = re.compile(r"[*_~`]")
SPACED_DASH = re.compile(r"\s+[\u2010-\u2015\u2212-]+\s+")
MINUS_ONE = re.compile(r"(?<![\w.])[\u2212\u2013-] ?1(?![0-9.,%])")
ARROW = re.compile(r"\s*(?:->|=>|[\u2192\u21d2\u27f6\u2794\u27a1])\s*")
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
LEET_WORD = re.compile(r"[a-z0-9@$]*[a-z][a-z0-9@$]*")
# Placeholders in scorecard.json patterns. {who}: the candidate or a person spoken about (names are masked to "the
# candidate" first); {cand}: the candidate as the object/subject of a hiring verb; {be}: a linking verb; {end}: end of
# a clause; {stage}: a later interview stage.
WHO = (r"(?:i|me|myself|she|he|they|her|him|them|the candidate|this candidate|candidate|the applicant|this applicant|"
       r"applicant|this person|the interviewee|interviewee)")
CAND = (r"(?:her|him|them|she|he|they|the candidate|this candidate|candidate|the applicant|this applicant|applicant|"
        r"this person|this one|the interviewee)")
BE = (r"(?:'s|'re|'m|m\b| is| are| am| was| were| has been| have been| had been| will be| would be| is now| was once|"
      r" seems| seemed| looks| looked| appears| appeared| became| becomes| has become| is still| was still| be|"
      r" being| been)")
END = (r"(?=\s?(?:[.,;:!?)\]]|$)|\s(?:and|but|so|if|because|as|since|though|although|overall|too|now|anyway|when|"
       r"while|before|after|given|despite|based|unless|until|imo|tbh|honestly)\b)")
STAGE = (r"(?:(?:next|final|second|third|last|later|another|in person|panel|onsite|on site|hiring manager|hm|exec|"
         r"executive|leadership|technical|case study|team) (?:round|rounds|interview|interviews|onsite|loop|stage|panel)"
         r"(?! of| in| for the (?:audit|close|project|migration))|round (?:two|three|four|2|3|4)\b|onsite|on site|"
         r"superday|super day|panel(?! (?:of|data|discussion|debrief|review|meeting|session|members?|vendors?|survey))|"
         r"final(?! rounds? (?:of|in|for)| (?:review|approval|draft|version|sign|signoff|numbers|close|reconciliation|"
         r"figures|report|balance|step|stage|answer|"
         r"invoice|payment|audit|pass))|offer stage|reference checks?|references(?! for))")
# {frag}: the start of a subject-less note fragment: the start of the text or of a sentence or clause (after . ; : ! ? (
# or a comma), plus optional linking words and intensifiers ("Diabetic.", "Was arrested in college.", "Very attractive.",
# "Recent grad, 22 years old."). Patterns using it end with an explicit end-of-predicate check.
FRAG = (r"(?:^|(?<=[.;:!?(,]) ?)(?:(?:was|is|has been|been|seems|seemed|looks|looked|very|quite|a bit|a little|kind of|"
        r"sort of|really|super|so|too|clearly|obviously|visibly|slightly|somewhat|rather|also|possibly|probably|maybe|"
        r"definitely|still|now|currently) )*")
# {money}: an amount that reads as money or pay (a currency, a k/grand/thousand suffix, 5+ digits or thousands
# separators, or a per-hour/per-year rate), optionally as a range ("90-100k"); {biz}: words that make a pay word part
# of a business figure ("bonus pool", "salary accrual", "equity dilution"), so it is not read as the candidate's pay.
MONEY = (r"(?:[0-9][0-9,.]*k? (?:to |or )?)?(?:(?:usd|gbp|eur|cad|aud|chf|inr|sgd|nzd) ?[0-9][0-9,.]*(?: ?(?:k|m|mm|"
         r"grand|thousand|million|lakhs?))?|[$£€¥₹] ?[0-9][0-9,.]*(?: ?(?:k|m|mm|grand|thousand|million))?|[0-9][0-9,.]*"
         r" ?(?:k|grand|thousand|lpa|lakhs?)\b|[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]{5,7}(?![0-9.,])|[0-9]+(?:\.[0-9]+)?"
         r" ?(?:/ ?(?:hr|hour|h|yr|year|annum)|an hour|per hour|a year|per year|per annum)\b)(?! (?:users?|customers?|"
         r"clients?|accounts?|invoices?|transactions?|entries|lines|items|units|seats|records|vendors?|employees|staff|"
         r"people|rows|skus?|orders))")
BIZ = (r"(?:accruals?|pools?|budgets?|expenses?|costs?|plans?|lines?|journals?|runs?|files?|reports?|ledgers?|"
       r"reversals?|savings|spend|benchmarks?|surveys?|data|for|to|from|by|in|on|the|our|their|"
       r"vendors?|suppliers?|customers?|clients?|team|teams|case|year|period|currency|layer|model|prices?|fees?|"
       r"dilution|financing|round|value|valuation|accounts?|ratio|balance|statement|payables?|receivables?|policy|"
       r"structure|scheme|program|programme|payouts?|liability|liabilities|terms|discounts?|deal|tax|taxes|"
       r"withholding|schedule|table|calc|calculation|formula|tracker|process|approvals?|codes?|cycle|rent|rents|"
       r"lease|leases|rates?|load|fare|freight)\b")
PLACEHOLDERS = {"{who}": WHO, "{cand}": CAND, "{be}": BE, "{end}": END, "{stage}": STAGE, "{frag}": FRAG,
                "{money}": MONEY, "{biz}": BIZ}
# Verdict clauses: lead-in words, then a verdict core (scorecard.json $.decision_terms.verdict_clauses), then optional
# trailing words. Only whole clauses of up to 12 words are tested.
LEAD = (r"(?:[0-9]{1,2} ?(?:/|out of) ?[0-9]{1,2}|she's|he's|she is|he is|the candidate is|candidate is|i think|"
        r"i'd say|id say|i say|i vote|we vote|i'm a|im a|i am a|it's a|its a|that's a|thats a|this is a|"
        r"overall|so|ok|okay|honestly|personally|frankly|bottom line is|bottom line|net net|final answer|final|my vote|"
        r"my call|my take|my verdict|my rec|my recommendation|our recommendation|recommendation|rec|verdict|"
        r"decision|call|vote|outcome|result|status|for me|imo|imho|tbh|then|just|still|definitely|absolutely|"
        r"probably|clearly|easily|a|an|the|is|it's|its|we|i|you|should|would|will|must|could|can|let's|lets|let us|"
        r"do|does|did|not|don't|dont|do not|never|won't|wont|wouldn't|wouldnt|shouldn't|shouldnt|can't|cant|cannot|"
        r"i'd|we'd|id|i would|we would|recommend|recommended|recommending|suggest|propose|please|time to|ready to|"
        r"ok to|okay to|happy to|go ahead and|going to|gonna|plan to|we're|we are|i'm|im|i am|yes|no|nope|yeah|"
        r"yep|sadly|unfortunately|regretfully|reluctantly|hence|thus|therefore|and|but|or|also|right now|for now|"
        r"at this point|in the end|ultimately|panel|the panel|hm)")
TAIL = (r"(?:{cand}|on {cand}|for {cand}|with {cand}|from me|for me|here|overall|for now|imo|imho|tbh|then|again|"
        r"please|for sure|from my side|for (?:the|this|that) (?:role|position|job|seat|team|level)|on this one|"
        r"this one|this candidate|now|today|asap|immediately|outright|anyway|sadly|unfortunately|100%|100 percent|"
        r"all the way|x2|x3|forward|ahead|through|on|definitely|absolutely|to (?:hire|offer|advance|reject|pass|"
        r"proceed|move forward|decline)|(?:on )?to (?:the |an |a )?{stage}|for (?:the |an |a )?(?:{stage}|offer)|at (?:the |this )?"
        r"(?:screen|phone screen|recruiter screen|screening|this stage|this point)|after (?:the )?screen|pending "
        r"(?:refs|references|reference checks?)|it|(?:from|by) (?:me|us|the panel|the "
        r"team)|unanimously|for us|for the team|at this level|at this time|right now)")
VERDICT_LABEL = re.compile(r"^(?:my |our |the panel's |final )?(?:vote|verdict|decision|recommendation|rec|call|"
                           r"outcome|result|answer|hire|hire or no hire|offer|advance|move forward|hiring decision|overall|"
                           r"overall rating|tl;?dr|final take|(?:the )?(?:next round|final round|onsite|on site|super ?day))$")
YES_NO = re.compile(r"^(?:(?:just|a|an|definitely|absolutely|probably|clearly|it's a|its a) )*(?:yes|no|y|n|yep|"
                    r"nope|yeah|nah)(?: (?:from me|for me|overall|imo|tbh))*$")
STAGE_LABEL = re.compile(r"^(?:the )?" + STAGE + r"(?: (?:interview|interviews|round|loop|call|session|stage|day|"
                         r"chat|prep|block|debrief|panel|with the candidate|with the interviewer))*$")
DISCOURSE = re.compile(r"^(?:moving|going|move|go|moved|went) (?:forward|ahead|on)$")
CLAUSE_SPLIT = re.compile(r"([,;:.!?]+)(?=\s|$)")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")
NON_ASCII = re.compile(r"[^\x00-\x7f]")
SURROGATE = re.compile("[\ud800-\udfff]")


def ascii_digits(text):
    """Every Unicode decimal digit (Arabic-Indic, Devanagari, ...) as its ASCII digit, so [0-9] sees them."""
    if not NON_ASCII.search(text):
        return text
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in text)


def normalise(text):
    """Scan form of text: HTML entities decoded, NFKC, digits made ASCII, invisible/format characters and accents
    removed, case-folded, look-alike letters mapped to Latin, spaced dashes made clause breaks, other dashes,
    underscores and whitespace collapsed to one space, and spaced-out letters joined."""
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if ch not in INVISIBLE and unicodedata.category(ch) not in ("Cf", "Mn", "Me", "Sk"))
    text = ascii_digits(unicodedata.normalize("NFC", text).casefold().translate(CONFUSABLES))
    text = ARROW.sub(" to ", text)
    text = MINUS_ONE.sub(" minusone", text)
    text = SPACED_DASH.sub(" ; ", text)
    text = "".join(" " if ch.isspace() or ch == "_" or unicodedata.category(ch) == "Pd" else ch for ch in text)
    # Join "h i r e" before collapsing whitespace, so a wider gap between spaced-out words stays a word break.
    text = SPACED_LETTERS.sub(lambda m: re.sub(r"[ .*·•]", "", m.group(0)), text)
    return " ".join(text.split())


# Character classes and lookbehinds keep their spaces literal (a lookbehind must stay fixed-width).
LITERAL_PARTS = re.compile(r"(\[(?:\\.|[^\]\\])*\]|\(\?<[!=](?:\\.|[^()\\])*\))")


# Term classes from scorecard.json $.term_classes ({c:party}, {c:pay_cue}, ...), so a pattern can combine a predicate
# class with an object class ("{c:affil_verb} {c:party}") instead of listing sentences. Set by load_config.
CLASSES = {}
CLASS_REF = re.compile(r"\{c:([a-z_]+)\}")


def expand(term):
    """A space in a pattern matches one optional separator ("culture fit" also hits "culturefit"); placeholders and
    term classes are filled in after that, so their own spaces stay required."""
    parts = LITERAL_PARTS.split(term)
    term = "".join(p if i % 2 else p.replace(" ", " ?") for i, p in enumerate(parts))
    for key, value in PLACEHOLDERS.items():
        term = term.replace(key, value)
    return CLASS_REF.sub(lambda m: CLASSES[m.group(1)], term)


# A note fragment with no subject and no verb ("Union member at her last job.", "Asian, probably good with numbers.",
# "Uses a wheelchair.") is also read with "the candidate is" (or "the candidate" before a verb) in front, so the
# person-context protected-term patterns apply to it. Sentences with a verb of their own, and clauses that start
# with a subject, a preposition or a past-tense verb ("Disabled the old rule", "Over 50 vendors were onboarded"),
# are left alone; the {frag} patterns in scorecard.json handle those shapes with their own end checks.
SUBJECT_START = re.compile(r"^(?:i|i'm|im|i've|i'd|i'll|we|we're|we've|we'd|we'll|you|you're|he|he's|she|she's|they|"
                           r"they're|it|it's|its|there|there's|here|the|a|an|this|that|these|those|our|my|his|her|"
                           r"their|your|one|each|every|all|both|some|most|no|none|nobody|someone|everyone|anyone|"
                           r"on|in|at|for|with|by|from|over|under|after|before|during|since|until|to|of|about|around|"
                           r"across|through|per|via|into|then|and|but|or|so|if|as|when|while|"
                           r"[a-z]+ed|paid|made|got|went|came|took|gave|led|built|kept|ran|set|cut|put|won|lost)\b"
                           r"|^[0-9$£€%#@(\[\"'“‘-]")
LINKING = re.compile(r"^(?:is|was|has|had|been|seems|seemed|looks|looked|appears|appeared|became|becomes|will|would|"
                     r"could|might|may|can|should|must|did|does|do|'s|[a-z]+[^s]s)\b")
FINITE = re.compile(r"\b(?:is|are|was|were|am|has|have|had|will|would|can|could|should|may|might|must|did|does|do|"
                    r"don't|didn't|won't|went|came|got|made|took|gave|became|left|sent|ran|led|built|kept|found|saw|"
                    r"said|told|paid|put|began|grew|fell|rose|wrote|brought|bought|thought|felt|held|met|spent|won|"
                    r"lost|knew)\b|\b[a-z]{3,}ed(?: (?:the|a|an|to|into|in|on|off|up|out|by|from|with|our|her|his|"
                    r"their|its|that|it|them|this|these|those|my|your|we|i|you|he|she|they|at|for|of|over|down|"
                    r"back)\b|[.!?;:]*\s*$)")
CLAUSE_START = re.compile(r"((?:^|,)\s*)")


def fragment_subject(text):
    """text with "the candidate is" (or "the candidate" before a verb) in front of each subject-less clause (up to 7
    words) of a verbless sentence that itself starts without a subject, or None when there is none."""
    out, changed = [], False
    for sentence in re.split(r"(?<=[.!?;:])\s+", text):
        words = sentence.split()
        if not words or len(words) > 12 or FINITE.search(" ".join(words[1:])) or SUBJECT_START.match(sentence):
            out.append(sentence)
            continue
        parts = CLAUSE_START.split(sentence)
        for i in range(2, len(parts), 2):
            body = parts[i]
            if body and not SUBJECT_START.match(body) and len(body.split()) <= 7:
                parts[i] = ("the candidate " if LINKING.match(body) else "the candidate is ") + body
                changed = True
        out.append("".join(parts))
    return " ".join(out) if changed else None


FOLD_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
FOLD_CACHE = {}


def near(a, b, limit, short):
    """Damerau-Levenshtein distance between a and b is at most limit. short (words of 4 letters or fewer): only a
    swapped pair or one extra letter counts, so "here" is never read as "hire"."""
    if short:
        if len(a) == len(b):
            diff = [i for i in range(len(a)) if a[i] != b[i]]
            return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
        return len(a) == len(b) + 1 and any(a[:i] + a[i + 1:] == b for i in range(len(a)))
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return False
        prev2, prev = prev, cur
    return prev[-1] <= limit


# --------------------------------------------------------------------------- job-relatedness gate

# Abbreviations whose full stop is not a sentence end ("e.g.", "vs.", "Dr."), and the gate's tokens: words and numbers
# ("125k", "4.000", "15%").
GATE_ABBREV = re.compile(r"\b(?:e\.g|i\.e|vs|etc|approx|incl|esp|dept|mr|mrs|ms|dr|prof|inc|ltd|co|corp|no|st|jr|sr|"
                         r"u\.s|u\.k|a\.m|p\.m)\.(?=\s)")
GATE_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# A further clause that starts with a past-tense action or a first-person narrator ("..., then checked their work",
# "...; picked the better one", "..., so we moved") narrates the work even without a lexicon word; a clause about the
# person (a copula, "she ...", a present-tense habit, a verbless fragment or reported speech) does not.
GATE_ACTION = re.compile(r"^(?:[a-z]{3,}ed|made|took|got|gave|sent|kept|found|set|cut|put|brought|caught|wrote|built|"
                         r"sold|led|ran|did|we|i|we'd|we've|we'll|i'd|i've|i'll)$")
GATE_REPORTING = {"talked", "mentioned", "noted", "described", "admitted", "revealed", "disclosed", "joked",
                  "complained", "shared", "confided", "volunteered", "bragged", "boasted", "claimed", "stated",
                  "added", "explained", "remarked", "commented", "raised", "flagged", "hinted", "implied", "suggested",
                  "argued", "insisted", "repeated", "stressed", "emphasised", "emphasized", "confessed", "announced",
                  "interned", "campaigned", "canvassed", "voted", "donated", "protested", "marched", "rallied",
                  "worked", "used", "retired", "divorced", "married", "separated", "diagnosed", "graduated",
                  "relocated", "moved", "emigrated", "immigrated", "converted", "served", "enlisted", "transitioned"}
# Clause breaks inside a sentence (normalise() has already turned spaced dashes into " ; ").
GATE_CLAUSE = re.compile(r"\s*[;:()]\s*|,\s*(?:and|but|though|although|while|whereas|plus|also|then|so|yet|or|"
                         r"except|apart from|outside work|outside of work)\s+|,\s*(?=(?:she|he|they|i|we)(?:'s|'d|'re|"
                         r"'ve)?\s)|\s+(?:but|though|although|whereas|plus|except)\s+|\s+and\s+(?=(?:she|he|they|"
                         r"is|was|has|had|also|mentioned|said|says|talked|seems|seemed|apparently|outside)\b)"
                         r"|(?<!not)(?<!n't)(?<!as)\s+yet\s+")
# Round 10: the words that end a circumstance adjunct's window, the possessive determiners and the prepositions after
# which a possessive noun phrase is read as being about the candidate's own life ("for her twins", "with his cast").
ADJUNCT_BREAK = {"and", "but", "so", "then", "yet", "or", "because", "which", "who", "whom", "where", "when", "that",
                 "as", "than", "if"}
POSSESSIVES = {"her", "his", "their", "my"}
YEAR = re.compile(r"(?:19|20)[0-9]{2}s?\Z")  # "2021", "early 2020s": a date, not a figure
GATE_DATE_TIME = re.compile(r"(?<![\w.:-])(?:[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?|"
                            r"[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?(?: ?[ap]\.?m\.?)?)(?![\w:-])", re.IGNORECASE)
ADJUNCT_PREPOSITIONS = {"with", "for", "to", "on", "in", "at", "by", "from", "into", "onto", "than", "via", "per"}
PREPOSITIONS = {"for", "with", "to", "of", "from", "on", "in", "at", "by", "about", "into", "without", "like", "around",
                "after", "before", "during", "since", "while", "whilst", "despite", "between", "following",
                "throughout", "over", "under", "near", "beside", "besides", "alongside", "amid", "until", "behind"}
GATE_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)*|[0-9][0-9.,]*[a-z%]*")
GATE_CONTACT_WORDS = {"gatecontact", "email", "e", "mail", "call", "text", "phone", "ring", "reach", "message",
                      "whatsapp", "linkedin", "number", "mobile", "cell", "contact", "details", "me", "us", "him", "her",
                      "them", "on", "at", "or", "via", "and", "is", "my", "here", "profile", "link", "website", "site",
                      "see", "visit", "find", "dm", "the", "a", "her", "his", "their"}


def word_forms(token):
    """token plus its likely base forms ("invoices" -> invoice, "reconciled" -> reconcile, "planned" -> plan,
    "running" -> run), so an allow-list word matches its inflections without a stemmer."""
    if token.endswith("'s"):
        token = token[:-2]
    forms = {token}
    for t in list(forms):
        if len(t) > 4 and t.endswith("ies"):
            forms.add(t[:-3] + "y")
        if len(t) > 4 and t.endswith("ied"):
            forms.add(t[:-3] + "y")
        if len(t) > 4 and t.endswith("es"):
            forms.add(t[:-2])
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            forms.add(t[:-1])
    for t in list(forms):
        for suffix, low in (("ing", 5), ("ed", 4), ("er", 4)):
            if len(t) > low and t.endswith(suffix):
                stem = t[:-len(suffix)]
                forms.update((stem, stem + "e"))
                if len(stem) > 2 and stem[-1] == stem[-2]:
                    forms.add(stem[:-1])
    return forms


class Gate:
    """The positive job-relatedness gate (scorecard.json $.job_related). An evidence item is used only when every
    sentence in it is a substantive clause (at least min_content_words words or numbers that are not function words)
    AND shows work substance: a work object from the allow list or from the competency's own rubric text (name,
    definition, anchors, planned questions), or two different work activities. Every clause of the sentence, the first
    included, must also carry a work word of its own (or narrate an action), and no clause may carry a personal
    circumstance: a time or circumstance adjunct ("while ...", "after ...", "during ...", "since ...") or a possessive
    noun phrase after a preposition or at the start of a clause ("for her ...", "with his ...", "Her ...") whose words
    are neither work nor neutral time, event or career words ($.job_related.adjunct_neutral). "Rebuilt the payroll
    reconciliation while seven months along." and "Built the model for her twins." are held for that reason, whatever
    the personal words are. Anything the gate can't positively see as work is held back, so a word missing from the
    allow list over-withholds instead of leaking. The gate runs after the term lists and the personal-context
    vocabularies, never instead of them. Rationales and gap notes go through the same gate (channel "rationale" or
    "gap"), with three differences: a short sentence is enough, only clauses of two or more content words must carry
    a work word of their own (a lone link label or code token is not an aside), and the words of
    $.job_related.authored_work_words ("anchor", "example", "question", "probe") count as work."""

    LISTS = ("work_objects", "work_activities", "never_work", "stopwords", "circumstance_connectors",
             "adjunct_neutral", "authored_work_words")

    def __init__(self, r, cfg):
        self.min_words = r.integer(cfg, "min_content_words", "$.job_related", low=1)
        lists = {}
        for key in self.LISTS:
            words = r.lst(cfg.get(key), "$.job_related." + key, required=True)
            for i, w in enumerate(words):
                if not isinstance(w, str) or not re.match(r"^[a-z0-9]+(?:'[a-z]+)?\*?\Z", w) or \
                        (w.endswith("*") and len(w) < 5):
                    raise r.fail("$.job_related.{}[{}]".format(key, i), "must be a lower-case word, or a prefix of "
                                 "at least 4 letters ending in *")
            lists[key] = words
        self.stop = set(lists["stopwords"])
        self.never = set(lists["never_work"])
        self.objects, self.object_prefixes = self.split(lists["work_objects"])
        self.activities, self.activity_prefixes = self.split(lists["work_activities"])
        self.connectors = set(lists["circumstance_connectors"])
        self.neutral, self.neutral_prefixes = self.split(lists["adjunct_neutral"])
        self.authored_words = set(lists["authored_work_words"])

    @staticmethod
    def split(words):
        return {w for w in words if not w.endswith("*")}, tuple(w[:-1] for w in words if w.endswith("*"))

    def is_never(self, forms):
        return bool(forms & self.never)

    def kind(self, word):
        """"object", "activity" or None for one content word. A negated or failed form of a work word is that work
        word ("unreconciled", "mismatched", "nonpayment")."""
        forms = word_forms(word)
        if self.is_never(forms):
            return None
        if forms & self.objects or word.startswith(self.object_prefixes):
            return "object"
        if forms & self.activities or word.startswith(self.activity_prefixes):
            return "activity"
        for prefix in ("un", "non", "mis"):
            if word.startswith(prefix) and len(word) - len(prefix) >= 5:
                return self.kind(word[len(prefix):])
        return None

    def rubric_words(self, rubric, cid):
        """Content words (5+ letters, not function or never_work words) of the competency's rubric text."""
        if cid is None:
            return set()
        cache = rubric.setdefault("_gate_words", {})  # per rubric: the words depend only on its text
        if cid not in cache:
            comp = rubric["by_id"][cid]
            texts = [comp["name"], comp["definition"]] + [comp["anchors"][k] for k in SCORES]
            texts += [q["text"] for q in rubric["questions"].values() if q["competency"] == cid]
            words = set()
            for text in texts:
                for w in GATE_TOKEN.findall(normalise(text or "")):
                    if len(w) >= 5 and w[0].isalpha() and w not in self.stop and not self.is_never(word_forms(w)):
                        words.add(w)
            cache[cid] = words
        return cache[cid]

    def rubric_hit(self, word, rubric_words):
        if self.is_never(word_forms(word)) or len(word) < 5 or not word[0].isalpha():
            return False
        for w in rubric_words:
            if w == word or word_forms(w) & word_forms(word):
                return True
            shorter = min(len(w), len(word))
            common = len(os.path.commonprefix([w, word]))
            if common >= 6 and common * 4 >= shorter * 3:
                return True
        return False

    def work(self, word, rubric_words, authored):
        return (authored and word in self.authored_words) or bool(self.kind(word)) or \
            self.rubric_hit(word, rubric_words)

    def is_neutral(self, word):
        return bool(word_forms(word) & self.neutral) or word.startswith(self.neutral_prefixes)

    def personal(self, words, rubric_words, authored):
        """True when words (an adjunct or a possessive noun phrase) name something that is neither work nor a
        neutral time, event or career word: "her kidney transplant", "seven months along", "her twins"."""
        content = [w for w in words if w not in self.stop and not w[0].isdigit() and len(w) >= 2]
        if any(self.work(w, rubric_words, authored) for w in content):
            return False
        return any(not self.is_neutral(w) for w in content)

    def personal_adjunct(self, clause, rubric_words, authored):
        """True when a clause carries a personal circumstance: a circumstance adjunct ("while ...", "after her ...")
        or a possessive noun phrase after a preposition or at the clause start ("for her twins", "Her toddler ...")
        that names neither work nor a neutral word."""
        for part in clause.split(","):
            tokens = GATE_TOKEN.findall(part)
            for i, t in enumerate(tokens):
                window = None
                if t in self.connectors:
                    # The adjunct's noun phrase, up to the next preposition or conjunction. A verb form ("before
                    # switching them on", "after resigning") is an action, not a circumstance: only its nouns count.
                    window = []
                    for u in tokens[i + 1:i + 7]:
                        if u in self.connectors or u in ADJUNCT_BREAK or u in ADJUNCT_PREPOSITIONS:
                            break
                        if not (len(u) > 5 and u.endswith("ing")):
                            window.append(u)
                elif t in POSSESSIVES and (i == 0 or tokens[i - 1] in PREPOSITIONS):
                    # The noun phrase, through an "of" complement: "her first choice of vendor" is about the vendor,
                    # "her choice of church" is not.
                    window = []
                    rest = tokens[i + 1:i + 7]
                    for j, u in enumerate(rest):
                        if u == "of" and j > 0 and j + 1 < len(rest) and rest[j - 1] not in self.stop:
                            continue
                        if u in self.stop or u in self.connectors:
                            break
                        window.append(u)
                        if len(window) == 3:
                            break
                if window and self.personal(window, rubric_words, authored):
                    return True
        return False

    def passes(self, text, rubric, cid, channel="evidence"):
        """True when every sentence of text is a substantive, visibly job-related clause. text is the raw item with
        contact details replaced by sentinels (strip_contacts): a sentence that only gives contact details ("Email me
        at <removed>.") is neither work nor a reason to hold the item, because the details are removed when printed.
        channel: "evidence", or "rationale" / "gap" for the interviewer's own rationale and gap notes."""
        authored = channel in ("rationale", "gap")
        min_words = 1 if authored else self.min_words
        text = GATE_DATE_TIME.sub(" ", text)  # "2026-10-15 10:00": neither work nor personal, and its colon is no clause
        text = text.translate({ord(k): " gatecontact " for k in SENTINELS})
        text = GATE_ABBREV.sub(lambda m: m.group(0).replace(".", ""), normalise(text))
        rubric_words = self.rubric_words(rubric, cid)
        seen = False
        for sentence in GATE_SENTENCE.split(text):
            tokens = GATE_TOKEN.findall(sentence)
            if "gatecontact" in tokens:
                rest = [t for t in tokens if t not in GATE_CONTACT_WORDS and t not in self.stop]
                if len(rest) < min_words:
                    continue  # a contact-details sentence ("Write to <removed> after the close."): removed when printed
                tokens = [t for t in tokens if t != "gatecontact"]
            if not tokens:
                continue  # punctuation or symbols only
            seen = True
            content = [t for t in tokens if t not in self.stop and (len(t) >= 2 or t[0].isdigit())]
            if len(content) < min_words:
                return False
            kinds = [self.kind(t) for t in content]
            if not ("object" in kinds or any(self.rubric_hit(t, rubric_words) for t in content) or
                    (authored and any(t in self.authored_words for t in content)) or
                    len({t for t, k in zip(content, kinds) if k == "activity"}) >= 2):
                return False
            # Every clause ("...; ...", "..., but ...", "... and she ..."), the first one included, must show at
            # least one work word of its own, so an aside can't ride on a work clause in either order ("Rebuilt the
            # AP checklist, and is diabetic.", "Came out last year; owns the audit prep."), and none may carry a
            # personal circumstance ("..., the week after her kidney transplant").
            for clause in GATE_CLAUSE.split(sentence):
                words = [t for t in GATE_TOKEN.findall(clause) if t not in self.stop and t != "gatecontact" and
                         (len(t) >= 2 or t[0].isdigit())]
                if words and (not authored or len(words) >= 2) and \
                        not any(self.work(t, rubric_words, authored) for t in words) and \
                        not all(self.is_neutral(t) or YEAR.match(t) for t in words):
                    first = GATE_TOKEN.findall(clause)[0]
                    if not (GATE_ACTION.match(first) and first not in GATE_REPORTING):
                        return False  # a clause about the person, not an action: needs a work word
                if self.personal_adjunct(clause, rubric_words, authored):
                    return False
        return seen


# --------------------------------------------------------------------------- candidate comparisons (structural)

# A comparison is candidate ranking when its other side is a person: another candidate named in the note, a
# reference to the rest of the candidate pool ("the other finalists", "anyone else we've seen", "either of the other
# two") or an object pronoun. The comparison side is any comparative or ranking construction ("-er than", "more ...
# than", "ahead of", "behind", "outshone", "pipped by", "trailing", "compared with", "as ... as"), or a person as the
# subject of a comparative ("Priya was sharper."). Names are found by shape (a capitalised word that is not a work
# word from the job-relatedness lexicon, a month or a weekday), after the candidate's and interviewers' names are
# masked. Comparisons with vendors, tools, models, budgets, periods or targets have a non-person other side and stay.
POOL_NOUN = (r"(?:candidates?|finalists?|applicants?|interviewees?|people|ones?|person|guy|gal|woman|man|lady|chap|"
             r"shortlist|field|pack|pool|bunch|lot|cohort|slate|competition)")
POOL_REF = (r"(?:(?:any|all|most|either|both|none|each|one|several|many|some) of )?(?:"
            r"(?:the |our |my |these |those |all the |all |any |every |each )?(?:other|remaining) "
            r"(?:two |three |four |five |six |)"
            + POOL_NOUN + r"|(?:the |all the |all )?others|the rest\b(?: of (?:the |them)?[a-z]*)?"
            r"|(?:the |our |my |all the |these |those )(?:two |three |four |five |)(?:candidates|finalists|applicants|"
            r"interviewees|shortlist|field|pack|pool|competition)"
            r"|(?:anyone|anybody|everyone|everybody|someone|somebody|no ?one|nobody) else"
            r"|(?:either|both|any|all|none|each) of (?:them|those two|the two|the three)"
            r"|(?:the )?other (?:two|three|four|five|six)"
            r"|(?:her|him|them)(?= ?(?:[.,;:!?)]|$)| (?:on|in|at|for|with|overall|across|and|but|when|by|today|"
            r"there|here|so far|in the)\b)"
            # Round 10: another candidate by letter or number ("candidate B", "applicant #3"), by source or timing
            # ("the referral", "the internal applicant", "yesterday's interviewee", "the morning candidate"), and the
            # rest of the pool by count or by who saw them ("the last three we saw", "everyone we interviewed").
            r"|(?:the )?(?:candidate|applicant|interviewee|finalist|person) "
            r"(?-i:#?(?:[A-HJ-Z]|[0-9]{1,2}))(?![\w'])"
            r"|(?:the |our |that |this )?(?:referral|referred (?:candidate|applicant|one)|internal (?:applicant|candidate|"
            r"one|hire)|external (?:applicant|candidate|one|hire)|agency (?:candidate|applicant))s?"
            r"|(?:the |that |this )?(?:[a-z]+day's|yesterday's|today's|this morning's|this afternoon's|last week's|"
            r"earlier|morning|afternoon|previous|last|first|second|third|next|later|other|prior) (?:candidate|applicant|"
            r"interviewee|finalist|person|guy|woman|man|lady|chap|hire)s?"
            r"|(?:the |all the |all )?(?:last|previous|first|other|earlier) (?:two|three|four|five|six|several|few|"
            r"[0-9]+)(?: (?:we|i)(?:'ve| have)? (?:saw|seen|met|interviewed|spoke to|spoken to|had in)| (?:candidates|"
            r"applicants|interviewees|finalists|people)|(?= ?(?:[.,;:!?)]|$)))"
            r"|(?:everyone|everybody|anyone|anybody|all|the people|the ones|those|others) (?:else )?(?:we|i)(?:'ve| have|"
            r"'d| had)? (?:saw|seen|met|interviewed|spoke to|spoken to|had in)"
            r"|(?:the |this |our |my |that )(?:cohort|intake)(?: of (?:candidates|applicants))?)")
NAME = r"(?-i:[A-Z][a-z]+(?:-[A-Z][a-z]+)?)(?:'s)?"
RANK_OTHER = r"(?P<other>" + POOL_REF + r"\b|" + NAME + r"\b)"
RANK_CMP = (r"\b(?:(?!(?:other|rather|whether|either|neither|never|ever|under|after|over|per|her|later)\b)[a-z]{2,}er|"
            r"better|worse|more [a-z]+|less [a-z]+|further|ahead)(?: (?!than\b)[a-z-]+){0,4} than "
            r"(?:(?:any|all|most|either|both|none) of )?")
RANK_REL = (r"\b(?:ahead of|in front of|behind|above|below|outperform(?:s|ed|ing)?|outpac(?:e|es|ed|ing)|outscor(?:e|es|ed|ing)|"
            r"outshone|outshines?|outshining|outclass(?:es|ed|ing)?|outdid|outdoes|outdo|outran|outworked|outthought|"
            r"beat|beats|beating|edg(?:e|es|ed|ing) out|edges|edged|pips|pipped|pipping|trail(?:s|ed|ing)?|"
            r"lag(?:s|ged|ging)? behind|(?:compared|comparison|comparing (?:her|him|them)) (?:to|with)|versus|vs|"
            r"as (?!soon\b|long\b|well\b|far\b|much\b|many\b|per\b)[a-z]+ as|on (?:a )?par with|level with|"
            r"(?:prefer(?:red|s)?|pick(?:ed|s)?|chose|choose|take|took|rate(?:d|s)?|rank(?:ed|s)?) "
            r"(?:her|him|them|the candidate) (?:over|to|above|ahead of)|"
            r"(?:pipped|beaten|outshone|outdone|outclassed|outperformed|edged out|bested|topped|eclipsed|"
            r"overshadowed|outscored) by) ")
RANK_SUBJECT = (r"(?:^|[.;:!?] |, (?:but|and|though|while|whereas) |\b(?:but|whereas|while) )" + r"(?P<other>" + NAME
                + r")(?: [a-z]+)? (?:was|is|seemed|seems|looked|looks|came across as|comes across as|felt|sounded|did|"
                r"answered|performed|presented|interviewed|went) (?:(?:much|far|a lot|way|clearly|noticeably|slightly|"
                r"a bit|somewhat|even|just|also|probably|definitely|marginally|a little) )?(?:the )?"
                r"(?:(?!(?:manager|controller|owner|partner|member|leader|player|reviewer|approver|preparer|officer|"
                r"engineer|developer|designer|other|over|under|after|never|ever|her)\b)[a-z]{2,}er|better|worse|"
                r"more [a-z]+|less [a-z]+|stronger|ahead|further along)(?= ?(?:[.,;:!?)]|$)| (?:than|on|at|with|overall|"
                r"across|throughout|today|too|though|but|and|imo|tbh|for me)\b)(?! than (?:expected|planned|budget|"
                r"forecast|last|before|usual)\b)")
RANK_POOL_SUPERLATIVE = (r"\b(?:best|strongest|weakest|worst|sharpest|brightest|smartest|cleanest|clearest|fastest|"
                         r"most [a-z]+|least [a-z]+|top|bottom)\b[^.;!?]{0,60}?\b(?:of|among|out of|in|from|across|than) "
                         r"(?:all |any |the |all the |these |those |our |my )?(?:other |remaining )?"
                         r"(?:candidates|finalists|applicants|interviewees|shortlist|people (?:we|i)(?:'ve| have)? "
                         r"(?:seen|met|interviewed)|(?:everyone|anyone) (?:we|i)(?:'ve| have)? (?:seen|met|interviewed))\b")
RANK_IDIOM = (r"\b(?:head and shoulders|(?:nobody|no ?one|none of the others|none of them) (?:else )?(?:came|comes|got|"
              r"gets|was|is|were|are) (?:close|near)|(?:nobody|no ?one) else (?:came|comes|got|gets) (?:close|near)|"
              r"(?:in|out of) a (?:different|higher|lower|other) league|second best|runner ?up"
              # Round 10: a place in the pool, however it is written ("Ranked #1 of 4", "ranked third out of five",
              # "Number one in this cohort", "Second only to Mei", "The pick of this round", "top three overall").
              r"|(?<!team )(?<!teams )(?<!company )(?<!firm )(?<!department )(?<!office )(?<!branch )(?<!region )"
              r"(?<!entity )(?<!fund )(?<!product )(?<!store )(?<!vendor )(?<!supplier )(?<!site )(?<!unit )"
              r"(?:ranked|ranks|ranking|rated|placed|finished|finishes|sits|sitting|slotted) "
              r"(?:#|no\.? ?|number )?(?:[0-9]+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|last|top|bottom)"
              r"(?: (?:of|out of|among|amongst) (?:[0-9]+|two|three|four|five|six|seven|eight|nine|ten|all|everyone|"
              r"everybody|them|the (?:candidates|applicants|finalists|interviewees|shortlist|pool|field|group|bunch|lot)|"
              r"those (?:we|i))| (?:in|on|across|from|within) (?:the |this |our |my )?(?:cohort|round|loop|intake|"
              r"(?:candidate |applicant )?pool|field|shortlist|slate|group of (?:candidates|applicants|finalists)|"
              r"panel'?s? (?:ranking|list|scores)))"
              r"|#[0-9]{1,2} (?:of|out of|among|amongst) (?:[0-9]+|the|these|all|everyone)"
              r"|number (?:one|1|two|2|three|3) (?:in|of|on|among|amongst|across|from) (?:the|this|these|our|my|all|"
              r"everyone)|second (?:only )?to (?-i:[A-Z][a-z]+)|second only to"
              r"|(?:pick|best|cream|star|standout|top) of (?:the|this|these|our|today's|the day's|the week's) "
              r"(?:bunch|round|loop|lot|litter|crop|cohort|pool|field|slate|intake|candidates|applicants|"
              r"finalists|interviews?)"
              r"|top (?:two|three|four|five|[0-9]+) (?:overall|so far|of (?:all|everyone|everybody|them|the "
              r"(?:candidates|applicants|finalists|interviewees)|this (?:cohort|round|loop|intake)))"
              r"|(?:shortlist|leaderboard|league table|stack rank(?:ing)?) (?:position|place|spot|slot))\b")
RANK_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (RANK_CMP + RANK_OTHER, RANK_REL + r"(?:(?:any|all|most|either|"
                 r"both|none) of )?" + RANK_OTHER, RANK_SUBJECT, RANK_POOL_SUPERLATIVE, RANK_IDIOM)]
NOT_NAMES = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "q1", "q2", "q3", "q4", "i", "the", "a", "an", "it", "this", "that", "we", "they", "he", "she", "you"}
NOT_NAME_SUFFIX = re.compile(r"(?:tion|sion|ment|ness|ity|ing|ance|ence|ure|ery|ory|ism|ist|ship|ics|ware|book|"
                             r"sheet|base|works|soft|force|desk|point|line|bank|pay|net|card|cloud|ledger)$")


# Campaigning verbs whose object is a capitalised name that isn't a work word ("canvassing for Reform", "leafleting
# for Plaid", "phone-banked for Harris"): political activity whatever the party or politician is called.
CAMPAIGN = re.compile(r"\b(?:canvass(?:es|ed|ing)?|leaflet(?:s|ed|ted|ing|ting)?|phone ?bank(?:s|ed|ing)?|"
                      r"door ?knock(?:s|ed|ing)?|knock(?:s|ed|ing)? on doors|campaign(?:s|ed|ing)?|electioneer(?:s|ed|"
                      r"ing)?|stump(?:s|ed|ing)?|marched|marching|votes?|voted|voting|stood) "
                      r"(?:for|with|on behalf of|in support of|against) "
                      r"(?:the |a |an )?(?P<other>" + NAME + r")", re.IGNORECASE)


# Round 10: a campaign, race or PAC named by a capitalised word that isn't a work word ("for the Mamdani campaign",
# "a Democratic congressional campaign", "the Harris PAC"), whatever the campaigning verb.
CAMPAIGN_NAMED = re.compile(r"\b(?:the|a|an|for|of|on|in|with|to|from|his|her|their|my)\s(?P<other>" + NAME +
                            r")(?: (?:[A-Za-z]+|[A-Z][a-z]+ [A-Z][a-z]+))? (?:campaign|campaigns|race|pac|super pac|"
                            r"caucus|candidacy|re ?election|election bid)\b", re.IGNORECASE)
CAMPAIGN_NOT = {"marketing", "email", "ad", "ads", "advert", "advertising", "sales", "collections", "collection",
                "brand", "product", "launch", "holiday", "christmas", "summer", "winter", "spring", "autumn", "fall",
                "recruitment", "recruiting", "hiring", "referral", "dunning", "renewal", "renewals", "promo",
                "promotional", "google", "facebook", "linkedin", "instagram", "tiktok", "social", "digital", "paid",
                "seo", "ppc", "outreach", "awareness", "retention", "winback", "upsell", "loyalty", "phishing", "test",
                "pilot", "rate", "rates", "arms", "rat", "price", "pricing", "discount"}


def campaigns_for_name(text, gate):
    """True when text describes campaigning for or against a capitalised name that isn't a work word, or names a
    campaign, race or PAC by such a word."""
    text = " ".join(unicodedata.normalize("NFKC", html.unescape(text)).replace("’", "'").replace("-", " ").split())
    for m in CAMPAIGN_NAMED.finditer(text):
        word = m.group("other").lower()
        word = word[:-2] if word.endswith("'s") else word
        if word in NOT_NAMES or word in CAMPAIGN_NOT or len(word) < 2:
            continue
        if gate is not None and (gate.kind(word) or word in gate.stop):
            continue
        return True
    for m in CAMPAIGN.finditer(text):
        word = m.group("other").lower()
        word = word[:-2] if word.endswith("'s") else word
        if word in NOT_NAMES or NOT_NAME_SUFFIX.search(word) or len(word) < 3:
            continue
        if gate is not None and (gate.kind(word) or word in gate.stop):
            continue
        return True
    return False


def ranks_people(text, gate):
    """True when text compares the candidate with another person (see the comment above). text keeps its case: the
    names of the candidate and interviewers are already masked."""
    text = " ".join(unicodedata.normalize("NFKC", html.unescape(text)).replace("’", "'").replace("‘", "'").split())
    for pattern in RANK_PATTERNS:
        for m in pattern.finditer(text):
            other = m.groupdict().get("other")
            if other is None or not re.match(r"[A-Z]", other):
                return True
            word = other.lower()
            word = word[:-2] if word.endswith("'s") else word
            if word in NOT_NAMES or NOT_NAME_SUFFIX.search(word) or len(word) < 3:
                continue
            if gate is not None and (gate.kind(word) or word in gate.stop or word in gate.never):
                continue
            return True
    return False


class Scanner:
    """Term lists from scorecard.json, compiled. Protected-characteristic and proxy terms are matched anywhere in the
    text; hiring-decision language is classified sentence by sentence: a clause that is only a verdict ("Reject.",
    "Do not advance.", "lean no", an emoji), or a hiring verb with the candidate or the role as its object ("give her
    the job", "advance to onsite"). Names in the text are masked first (candidate -> "the candidate", interviewers ->
    "the interviewer"), so a name is never read as a term."""

    def __init__(self, groups, decision, r, names=None, spelling=None, personal=None):
        self.groups, self.decision, self.r = groups, decision, r
        self.personal = []
        personal = personal or {"patterns": [], "work_heads": set(), "adjacent_heads": set(), "third_party": set(),
                                "third_party_things": set(), "third_party_aux": set(), "trade_verbs": set(),
                                "no_exemption": set()}
        self.work_heads, self.adjacent_heads = personal["work_heads"], personal["adjacent_heads"]
        self.no_exemption = personal["no_exemption"]
        words = lambda ws: "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True)) or "(?!)"
        # A third party as the subject of the clause, at its start or after a conjunction: "Our controller got",
        # "while a colleague was on", "The old ERP was". A person in a role may be followed directly by the match
        # ("Our controller retired"); any other noun needs an auxiliary or adverb first ("The team was very young"),
        # so an adjective use ("firm handshake") is never a third party.
        aux = r"(?:\s(?:" + words(personal["third_party_aux"]) + r"))"
        self.third_party = re.compile(
            r"(?:^|\b(?:while|when|whilst|after|because|since|as|so|and|but|until|before|then|once|where|whose)\s)"
            r"(?:(?:our|the|my|a|an|their|this|that|one|two|three|several|some|all|both|his|her|its)\s)?"
            r"(?:[a-z][a-z-]*\s)?(?:(?:" + words(personal["third_party"]) + r")(?:'s)?" + aux + r"*|(?:" +
            words(personal["third_party_things"]) + r")(?:'s)?" + aux + r"+)(?:\s[a-z-]+'s?)?\s*$")
        # A trade verb with the match as its object or the object's buyer: "sold insulin pumps", "sold insulin pumps
        # to hospitals", "billed the clinic".
        self.trade = re.compile(r"\b(?:" + words(personal["trade_verbs"]) + r")\s(?:(?:the|our|their|its|a|an|some|"
                                r"all)\s)?(?:[a-z][a-z-]*\s){0,2}(?:(?:to|for)\s(?:the\s)?)?$")
        for category, cased, terms, path in personal["patterns"]:
            paths = ["{}[{}]".format(path, i) for i in range(len(terms))]
            for term, p in zip(terms, paths):
                if "\\d" in term:
                    raise r.fail(p, "use [0-9] instead of \\d (only ASCII digits are matched)")
            body = "|".join("(?:{})".format(expand(t)) for t in terms) or "(?!)"
            pattern = r"(?<![^\W_])(?:" + body + r")(?![^\W_])"
            try:
                compiled = re.compile(pattern, 0 if cased else re.IGNORECASE)
            except (re.error, OverflowError, RecursionError):
                for term, p in zip(terms, paths):
                    try:
                        re.compile(expand(term))
                    except (re.error, OverflowError, RecursionError) as exc:
                        raise r.fail(p, "bad pattern ({})".format(exc)) from None
                raise r.fail(path, "bad pattern list") from None
            self.personal.append((category, cased, compiled))
        self.spelling_cfg = spelling or {"words": [], "not": []}
        self.spelling = list(self.spelling_cfg["words"])
        self.spelling_known = set(self.spelling) | set(self.spelling_cfg["not"])
        self.names = names or {}
        self.masks = []
        for token, repl in sorted(self.names.items(), key=lambda kv: -len(kv[0])):
            self.masks.append((re.compile(r"(?<![^\W_])" + re.escape(token) + r"(?![^\W_])"), repl))
        self.patterns = []
        for category in sorted(groups):
            group, terms = groups[category]
            paths = ["$.{}.{}[{}]".format(group, category, i) for i in range(len(terms))]
            self.patterns.append((category, self.compile(terms, paths)))
        compiled = {}
        for source in DECISION_SOURCES:
            terms = decision[source]
            paths = ["$.decision_terms.{}[{}]".format(source, i) for i in range(len(terms))]
            if source == "verdict_clauses":
                core = "|".join("(?:{})".format(expand(t)) for t in terms) or "(?!)"
                self.verdict = self.compile_raw(r"^(?:{lead} )*(?:{core})(?: (?:{core}|{tail}))*$".format(
                    lead=LEAD, core=core, tail=expand(TAIL)), paths)
                tight = lambda s: s.replace(" ?", "").replace(" ", "")
                self.verdict_tight = self.compile_raw(r"^(?:{lead})*(?:{core})(?:{core}|{tail})*$".format(
                    lead=tight(LEAD), core=tight(core), tail=tight(expand(TAIL))), paths)
            else:
                compiled[source] = self.compile(terms, paths)
        self.decision_any = compiled["any"]
        self.decision_authored = compiled["authored"]
        self.decision_quote = compiled["candidate_quote"]

    def work_sense(self, text, match, category):
        """True when a personal-context match is used in its work sense (see personal_context in load_config)."""
        if category in self.no_exemption:
            return False
        low = text.lower()
        before = re.split(r"[.,;:!?()\[\]\"]", low[:match.start()])[-1].strip()
        after = re.split(r"[.,;:!?()\[\]\"]", low[match.end():], maxsplit=1)[0].split()
        content = 0
        for word in after[:5]:
            word = word.strip("'")
            if word in ("and", "or", "&", "the", "a", "an", "of"):
                continue
            forms = word_forms(word)
            if forms & self.work_heads and (content == 0 or not forms & self.adjacent_heads):
                return True
            content += 1
            gate = getattr(self, "gate", None)
            if content >= 2 or (gate is not None and word in gate.stop):
                break
        if self.third_party.search(before + " " if before else ""):
            return True
        return bool(self.trade.search(before + " " if before else ""))

    def compile(self, terms, paths):
        """One regex per list. If the joined list doesn't compile, each term is compiled on its own so the error names
        its path."""
        parts = []
        for term, path in zip(terms, paths):
            if "\\d" in term:
                raise self.r.fail(path, "use [0-9] instead of \\d (only ASCII digits are matched)")
            parts.append("(?:{})".format(expand(term)))
        try:
            return re.compile("|".join(parts) or "(?!)", re.IGNORECASE)
        except (re.error, OverflowError, RecursionError):
            for part, path in zip(parts, paths):
                try:
                    re.compile(part)
                except (re.error, OverflowError, RecursionError) as exc:
                    raise self.r.fail(path, "bad pattern ({})".format(exc)) from None
            return self.compile_raw("|".join(parts), paths)

    def compile_raw(self, pattern, paths):
        try:
            return re.compile(pattern, re.IGNORECASE)
        except (re.error, OverflowError, RecursionError) as exc:
            raise self.r.fail(paths[0] if paths else "$", "bad pattern list ({})".format(exc)) from None

    def with_names(self, candidate, interviewers):
        """The same lists, with the candidate's and interviewers' name parts masked before matching (case-sensitive,
        so a capitalised name is masked and the same word in lower case is still read as a word)."""
        names = {}
        for name, repl in [(n, "the interviewer") for n in interviewers] + [(candidate, "the candidate")]:
            for token in re.split(r"[\s.,]+", name or ""):
                token = token.strip("'’-")
                if len(token) >= 2 and not token.isupper() and token[0].isalpha():
                    names[token] = repl
        other = copy.copy(self)  # the compiled lists are shared; only the name masks differ
        other.names = names
        other.masks = [(re.compile(r"(?<![^\W_])" + re.escape(token) + r"(?![^\W_])"), repl)
                       for token, repl in sorted(names.items(), key=lambda kv: -len(kv[0]))]
        return other

    def mask(self, text):
        for pattern, repl in self.masks:
            text = pattern.sub(repl, text)
        return text

    def forms(self, text):
        forms = [normalise(text)]
        if MARKUP.search(text):
            forms.append(normalise(MARKUP.sub("", text)))
        return forms

    def fold(self, text):
        """text with near-misspellings of the listed words (scorecard.json $.spelling_words) replaced by the word, or
        None when nothing changed: "hier", "ofer", "rejct", "pregnent", "wheelchiar", "mak" read as the word."""
        if not self.spelling:
            return None
        out = FOLD_WORD.sub(lambda m: self.spelling_word(m.group(0)), text)
        return out if out != text else None

    def spelling_word(self, token):
        if token in self.spelling_known or len(token) < 3:
            return token
        hit = FOLD_CACHE.get(token)
        if hit is None:
            hit = token
            for word in self.spelling:
                if len(token) == 3:  # a dropped letter from a 4-letter word, first letter kept: "mak", "hir"
                    if len(word) == 4 and word[0] == token[0] and token in (word[:i] + word[i + 1:] for i in (1, 2, 3)):
                        hit = word
                        break
                elif abs(len(word) - len(token)) <= 2 and near(token, word, 2 if len(word) >= 9 else 1,
                                                              len(word) <= 4):
                    hit = word
                    break
            FOLD_CACHE[token] = hit
        return hit

    def decision_hit(self, text, source):
        for sentence in SENTENCE_SPLIT.split(text):
            if self.decision_any.search(sentence):
                return True
            if source in ("authored", "name", "label", "evidence") and self.decision_authored.search(sentence):
                return True
            if source in ("candidate_quote", "evidence") and self.decision_quote.search(sentence):
                return True
            if self.verdict_clause(sentence, label=source == "label"):
                return True
        return False

    def verdict_clause(self, sentence, label=False):
        """True when a clause of the sentence is only a verdict. In a label (an interview title, a role, a rubric
        stage or competency name, an id), a clause that only names an interview stage ("Onsite", "Final round",
        "Superday interview") is the meeting's name, not a verdict."""
        pieces = CLAUSE_SPLIT.split(sentence)
        previous = None
        for i in range(0, len(pieces), 2):
            body = pieces[i].strip()
            term = pieces[i + 1][-1] if i + 1 < len(pieces) else ""
            if body and len(body.split()) <= 12:
                bare_stage = (term == ":" or label) and re.match(r"^(?:the )?" + STAGE + "$", body)
                if label and not bare_stage:
                    bare_stage = STAGE_LABEL.match(body)
                if not (term == "," and DISCOURSE.match(body)) and not bare_stage:
                    if self.verdict.match(body):
                        return True
                    if len(body.split()) <= 4 and self.verdict_tight.match(body.replace(" ", "")):
                        return True
                if previous is not None and previous[1] in (":", "?", "-") and VERDICT_LABEL.match(previous[0]) \
                        and YES_NO.match(body):
                    return True
            previous = (body, term)
        return False

    def scan(self, text, source="authored"):
        """Sorted categories found in text (never the matched words). source: "authored" (notes, rationales, gap
        notes, summaries, titles, rubric text), "candidate_quote" (the candidate's own words: "me" is the candidate),
        "name" (a person's name: hiring-decision language only), or "label" (titles, roles, rubric names and ids:
        like "authored", except that a clause naming only an interview stage, "Onsite", is not a verdict). Text with markdown emphasis characters is also
        scanned with them deleted, so "hi**re**" reads as it renders. "rationale" and "gap" are read like "authored";
        evidence, rationales and gap notes are also checked against the personal-context vocabularies
        ($.personal_context), in every clause."""
        item = source in ("evidence", "rationale", "gap")
        if source in ("rationale", "gap"):
            source = "authored"
        masked = self.forms(self.mask(text))
        unfolded = list(masked)  # the personal-context vocabularies are whole words: no spelling fold ("migration")
        masked += [f for f in (self.fold(m) for m in masked) if f]
        found = set()
        if item and self.personal:
            raw = self.mask(text)
            cased = [" ".join("".join(" " if unicodedata.category(ch) == "Pd" or ch == "_" else ch for ch in
                                      unicodedata.normalize("NFKC", html.unescape(f)).replace("’", "'")).split())
                     for f in [raw, MARKUP.sub("", raw)]]
            for category, is_cased, pattern in self.personal:
                if category in found:
                    continue
                for f in (cased if is_cased else unfolded):
                    if any(not self.work_sense(f, m, category) for m in pattern.finditer(f)):
                        found.add(category)
                        break
        if source != "name":
            protected = masked
            if source in ("authored", "evidence"):
                protected = masked + [f for f in (fragment_subject(m) for m in masked) if f]
            for category, pattern in self.patterns:
                if any(pattern.search(f) for f in protected):
                    found.add(category)
        forms = masked + [f for f in self.forms(text) if f not in masked]
        forms += [f for f in (self.fold(m) for m in forms) if f and f not in forms]
        forms += [LEET_WORD.sub(lambda m: m.group(0).translate(LEET) if re.search(r"[0-9@$]", m.group(0))
                                else m.group(0), f) for f in forms]
        if any(self.decision_hit(f, source) for f in forms):
            found.add(DECISION)
        elif source not in ("name", "label") and ranks_people(self.mask(text), getattr(self, "gate", None)):
            found.add(DECISION)
        if source not in ("name", "label") and "political_affiliation" not in found and \
                campaigns_for_name(self.mask(text), getattr(self, "gate", None)):
            found.add("political_affiliation")
        if "salary_history" in found:
            found.discard("compensation")  # one label per pay mention: salary history is the more specific one
        return sorted(found)


# --------------------------------------------------------------------------- personal data and escaping

# Phone numbers: phone-like grouping only. A match that starts an ISO date, or that follows an invoice / PO / order /
# reference / account word, is kept. Pattern-based: unusual formats can slip through, and a number shaped like a phone
# number can be removed.
PHONE = re.compile(
    r"(?<![\w+/.-])(?:\+?1[ .-]?)?(?:\([0-9]{3}\) ?|[0-9]{3}[ .-])[0-9]{3}[ .-][0-9]{4}(?![\w/-]|[.,][0-9])"
    r"|(?<![\w+/.-])\+[0-9]{1,3}(?:[ .-]?\(?[0-9]{1,4}\)?){2,5}(?![\w/-]|[.,][0-9])"
    r"|(?<![\w+/.-])0[0-9]{2,4}[ -][0-9]{3,4}(?:[ -]?[0-9]{3,4})?(?![\w/-]|[.,][0-9])"
    r"|(?P<cue>(?i:\b(?:phone|cell|mobile|tel|call|text|whatsapp|ring|reach)(?: (?:me|her|him|them|us))?(?: (?:on|at))?"
    r"|\bnumber is|\bnumber:)[:# .]*)\+?[0-9][0-9 .-]{5,14}[0-9](?![\w/-])")
SSN = re.compile(r"(?<![\w/.-])[0-9]{3}([ -])[0-9]{2}\1[0-9]{4}(?![\w/-]|[.,][0-9])"
                 r"|(?i:\b(?:ssn|social security(?: number| no\.?)?|sin|tax id|tin|itin))[:# ]*[0-9][0-9 -]{6,10}[0-9]"
                 r"(?![\w/-])"
                 r"|(?<![\w/.-])[A-Za-z]{2} ?[0-9]{2} ?[0-9]{2} ?[0-9]{2} ?[A-Da-d](?![\w/-])")
NOT_PHONE_BEFORE = re.compile(r"(?i)(?:invoice|inv|po|p\.o\.|order|ord|ref|reference|account|acct|a/c|ticket|case|"
                              r"claim|policy|batch|check|cheque|wire|ach|txn|transaction|id|no\.|number|#|sku|"
                              r"part|serial|vendor|customer|gl)[ :#.-]*(?:[0-9]+[ .-]+)*$")
ISO_START = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
EMAIL = re.compile(r"(?i:(?:mailto|xmpp):)?[A-Za-z0-9._%+-]+@[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
LINKEDIN = re.compile(r"(?i)(?:\b(?:https?://)?(?:[a-z0-9-]+\.)*(?:linkedin\.com|lnkd\.in)\b)\S*")
LINK = re.compile(r"(?i)(?:(?:https?|ftp)://|www\.)\S+")  # no \b: "_https://..." autolinks in GFM
CONTACT_REMOVED = "[contact detail removed]"
ID_REMOVED = "[ID number removed]"
LINK_REMOVED = "[link removed]"
# Placeholders survive markdown escaping and are swapped for the labels afterwards (control characters are removed
# from every input string, so input can't fake them).
SENTINELS = {"\x01": CONTACT_REMOVED, "\x02": ID_REMOVED, "\x03": LINK_REMOVED}


def phone_sub(text):
    def repl(m):
        cue = m.group("cue") or ""  # a cue word ("call me on") stays; only the number is removed
        before = text[max(0, m.start() - 14):m.start()]
        digits = sum(ch.isdigit() for ch in m.group(0))
        if ISO_START.match(text, m.start() + len(cue)) or NOT_PHONE_BEFORE.search(before) or not 7 <= digits <= 15:
            return m.group(0)
        return cue + "\x01"
    return PHONE.sub(repl, text)


def strip_contacts(text):
    """Raw text with e-mails, LinkedIn/other links, phone numbers and ID numbers replaced by sentinels."""
    cleaned = EMAIL.sub("\x01", text)
    cleaned = LINKEDIN.sub("\x01", cleaned)
    cleaned = LINK.sub("\x03", cleaned)
    cleaned = SSN.sub("\x02", cleaned)
    cleaned = phone_sub(cleaned)
    return cleaned, cleaned != text


def scan_form(text):
    """The text the fairness scan reads: contact details, links and ID numbers removed first (as they are when
    printed), so a phone number is never read as a salary figure or a date of birth."""
    return strip_contacts(text)[0].translate({ord(k): " " for k in SENTINELS})


def has_contacts(text):
    return bool(text) and strip_contacts(text)[1]


MD_SPECIAL = re.compile(r"([\\`*_~\[\]()|$=!])")


def md(text):
    """Escape interpolated text for markdown: backslash first, then every character that can start emphasis, code,
    strikethrough, links, images, table cells, math or highlights (` * _ ~ [ ] ( ) | $ = !); then '&', '<' and '>' as entities
    (&amp; &lt; &gt;, which Slack also decodes), so no character reference, raw HTML, autolink or <!channel>-style mention survives.
    Sentinels from strip_contacts become their labels."""
    if text is None:
        return None
    text = MD_SPECIAL.sub(r"\\\1", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for key, label in SENTINELS.items():
        text = text.replace(key, label)
    return text


def clean_text(text):
    """(markdown-safe text with contact details and links removed, whether anything was removed)."""
    stripped, changed = strip_contacts(text)
    return md(stripped), changed


def labels_for(categories, config):
    return ", ".join(config["category_labels"][c] for c in categories)


FIELD_MAX = 600  # longest title, role, source detail or name accepted (keeps the scan's run time bounded)


def checked(r, scanner, config, value, path, why, source="authored", limit=None):
    """Scan a field that is shown as-is (titles, roles, rubric text; names with source "name"): a contact detail or
    link, or a hit, stops the run without echoing the text. limit: the longest value accepted."""
    if value is None:
        return None
    if limit is not None and len(value) > limit:
        raise r.fail(path, "is longer than {} characters ({}); shorten it".format(limit, len(value)))
    if has_contacts(value):
        raise r.fail(path, "must not contain an e-mail address, phone number, ID number or link (text not shown)")
    hits = scanner.scan(value, source)
    if hits:
        raise r.fail(path, "refers to {} (text not shown). {}".format(labels_for(hits, config), why))
    return md(value)


NAME_TOKEN = re.compile(r"^(?:[^\W\d_](?:[^\W\d_]|['’.-](?=[^\W\d_]))*\.?|[^\W\d_]\.)$")
NAME_PARTICLES = {"de", "da", "del", "della", "der", "den", "di", "du", "dos", "das", "la", "le", "van", "von", "bin",
                  "binti", "bint", "al", "el", "y", "e", "ter", "ten", "st", "mc", "mac", "o'"}


def name_shaped(value):
    """1-5 words, each a capitalised name word, an initial or a particle (de, van, al...): 'Jordan R.', 'Ana de la
    Cruz', 'O'Neil-Park'. Anything else (descriptions, brackets, digits, markup) is not a name."""
    words = value.split()
    if not 1 <= len(words) <= 5 or len(value) > 60:
        return False
    for w in words:
        if not NAME_TOKEN.match(w):
            return False
        first = w[0]
        if not (first.isupper() or first.upper() == first.lower() or w.lower() in NAME_PARTICLES):
            return False
    return True


def checked_name(r, scanner, config, value, path, example):
    """A person's name is printed as given. It must be name-shaped and free of contact details and hiring-decision
    language; it is NOT scanned for protected-characteristic terms (so 'Gay T.' or 'Christian M.' are fine)."""
    if value is None:
        return None
    if len(value) > FIELD_MAX:
        raise r.fail(path, "is longer than {} characters ({}); shorten it".format(FIELD_MAX, len(value)))
    if not name_shaped(value):
        raise r.fail(path, "must be a short name such as '{}' (1-5 capitalised words or initials, no brackets, digits "
                     "or descriptions; text not shown)".format(example))
    return checked(r, scanner, config, value, path, "Use a short name such as '{}'".format(example), source="name",
                   limit=FIELD_MAX)


# --------------------------------------------------------------------------- rubric

RUBRIC_WHY = "Rubric text must describe observable, job-related behaviour only and never a hiring decision"


def load_rubric(path, config):
    if path is None:
        raise RubricError("rubric: no rubric file given (--rubric). Build one with the hiring team from "
                          "rubric_template.json, get it approved, then score.")
    data = load_json(path, "rubric {}".format(path), error=RubricError)
    r = Reader("rubric {}".format(path), error=RubricError)
    r.obj(data, "$")
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1")
    scanner = config["_scanner"]

    def field(parent, key, path, required=True, kind=None, why=RUBRIC_WHY):
        if kind == "slug":
            value = r.slug(parent, key, path)
            checked(r, scanner, config, value, "{}.{}".format(path, key), why, source="label")
            return value  # [a-z0-9_-] only: kept as is, so ids still match the bundle
        value = r.text(parent, key, path, required=required)
        return checked(r, scanner, config, value, "{}.{}".format(path, key), why,
                       source="label" if kind == "label" else "authored")

    rubric = {
        "role_id": field(data, "role_id", "$", kind="slug"),
        "role_title": field(data, "role_title", "$", kind="label"),
        "version": field(data, "version", "$"),
        "status": r.enum(data, "status", "$", ["draft", "approved"]),
        "approved_by": field(data, "approved_by", "$", required=False),
    }
    if rubric["status"] == "approved" and not rubric["approved_by"]:
        raise r.fail("$.approved_by", "is required when status is approved (who on the hiring team approved it)")
    labels = r.obj(data.get("scale_labels"), "$.scale_labels")
    if sorted(labels) != SCORES:
        raise r.fail("$.scale_labels", "must have exactly the keys 1, 2, 3, 4")
    rubric["scale_labels"] = {k: field(labels, k, "$.scale_labels") for k in SCORES}
    comps = r.lst(data.get("competencies"), "$.competencies", required=True)
    if not config["min_competencies"] <= len(comps) <= config["max_competencies"]:
        raise r.fail("$.competencies", "has {} competencies; use {} to {}".format(
            len(comps), config["min_competencies"], config["max_competencies"]))
    rubric["competencies"] = []
    seen = set()
    for i, c in enumerate(comps):
        p = "$.competencies[{}]".format(i)
        r.obj(c, p)
        cid = field(c, "id", p, kind="slug")
        if cid in seen:
            raise r.fail(p + ".id", "duplicate competency id {!r}".format(cid))
        seen.add(cid)
        anchors = r.obj(c.get("anchors"), p + ".anchors")
        if sorted(anchors) != SCORES:
            raise r.fail(p + ".anchors", "must have a behavioural anchor for each of 1, 2, 3, 4")
        rubric["competencies"].append({
            "id": cid,
            "name": field(c, "name", p, kind="label"),
            "definition": field(c, "definition", p),
            "anchors": {k: field(anchors, k, p + ".anchors") for k in SCORES},
            "must_have": r.boolean(c, "must_have", p, default=False),
        })
    rubric["by_id"] = {c["id"]: c for c in rubric["competencies"]}
    rubric["stages"] = []
    rubric["questions"] = {}
    stage_ids = set()
    for i, s in enumerate(r.lst(data.get("interview_plan"), "$.interview_plan")):
        p = "$.interview_plan[{}]".format(i)
        r.obj(s, p)
        sid = field(s, "stage_id", p, kind="slug")
        if sid in stage_ids:
            raise r.fail(p + ".stage_id", "duplicate stage id {!r}".format(sid))
        stage_ids.add(sid)
        owned = []
        for j, cid in enumerate(r.lst(s.get("competencies"), p + ".competencies", required=True)):
            if not isinstance(cid, str) or cid not in rubric["by_id"]:
                raise r.fail("{}.competencies[{}]".format(p, j), "unknown competency {}".format(shown(cid)))
            owned.append(cid)
        questions = []
        for j, q in enumerate(r.lst(s.get("questions"), p + ".questions")):
            qp = "{}.questions[{}]".format(p, j)
            r.obj(q, qp)
            qid = field(q, "id", qp, kind="slug")
            if qid in rubric["questions"]:
                raise r.fail(qp + ".id", "duplicate question id {!r}".format(qid))
            comp = r.text(q, "competency", qp)
            if comp not in owned:
                raise r.fail(qp + ".competency", "{} is not one of this stage's competencies".format(shown(comp)))
            text = field(q, "text", qp, why="Never plan a question on this topic")
            question = {"id": qid, "competency": comp, "text": text, "stage_id": sid}
            rubric["questions"][qid] = question
            questions.append(question)
        rubric["stages"].append({"stage_id": sid, "stage_name": field(s, "stage_name", p, kind="label"),
                                 "competencies": owned, "questions": questions})
    rubric["stage_by_id"] = {s["stage_id"]: s for s in rubric["stages"]}
    return rubric


def require_approved(rubric, path):
    if rubric["status"] != "approved":
        raise RubricError("rubric {}: status is draft. The hiring team must approve it (status: approved, "
                          "approved_by) before any interview is scored against it".format(path))


# --------------------------------------------------------------------------- bundle

FIELD_WHY = "Leave it out or restate it without that; reviewed metadata must describe the interview's job-related context"


def source_obj(r, value, path, required, scanner, config):
    if value is None and not required:
        return None
    r.obj(value, path)
    provider = value.get("provider")
    if provider is not None and (not isinstance(provider, str) or provider not in PROVIDER_LABELS):
        raise r.fail(path + ".provider", "must be one of {} or null, got {}".format(
            ", ".join(sorted(PROVIDER_LABELS)), shown(provider)))
    return {"provider": provider,
            "status": r.enum(value, "status", path, SOURCE_STATUSES),
            "detail": checked(r, scanner, config, r.text(value, "detail", path, required=False),
                              path + ".detail", FIELD_WHY, limit=FIELD_MAX)}


def reject_keys(r, obj, keys, path, why):
    for key in sorted(obj):
        if key in keys:
            raise r.fail("{}.{}".format(path, key), why)


def load_bundle(path, config, rubric):
    data = load_json(path, "bundle {}".format(path))
    r = Reader("bundle {}".format(path))
    r.obj(data, "$")
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise r.fail("$.schema_version", "must be 1")
    forbidden = set(config["forbidden_bundle_keys"])
    reject_keys(r, data, forbidden, "$", "is not allowed: bundles carry extracted evidence only (no transcripts) "
                "and never a hire/no-hire recommendation")
    b = {"mode": r.enum(data, "mode", "$", ["single", "panel"]),
         "role_id": r.slug(data, "role_id", "$")}
    if b["role_id"] != rubric["role_id"]:
        raise RubricError("bundle {}: $.role_id is {} but the rubric is for {!r}. Use that role's approved "
                          "rubric, or build one first".format(path, shown(b["role_id"]), rubric["role_id"]))
    scanner = config["_scanner"]
    cand = r.obj(data.get("candidate"), "$.candidate")
    reject_keys(r, cand, set(config["forbidden_candidate_keys"]), "$.candidate",
                "is not allowed: keep candidate data to a reference and a display name")
    ref = r.ident(cand, "ref", "$.candidate")
    name = r.text(cand, "display_name", "$.candidate")
    b["candidate"] = {"ref": ref, "display_name": checked_name(r, scanner, config, name, "$.candidate.display_name",
                                                               "Jordan R.")}
    b["candidate_raw"] = name
    src = r.obj(data.get("sources"), "$.sources")
    b["sources"] = {"notes": source_obj(r, src.get("notes"), "$.sources.notes", True, scanner, config),
                    "transcript": source_obj(r, src.get("transcript"), "$.sources.transcript", True, scanner, config),
                    "ats": source_obj(r, src.get("ats"), "$.sources.ats", False, scanner, config)}
    interviews = []
    for i, iv in enumerate(r.lst(data.get("interviews"), "$.interviews", required=True)):
        interviews.append(load_interview(r, iv, "$.interviews[{}]".format(i), config, rubric, forbidden))
    if not interviews:
        raise r.fail("$.interviews", "is empty: no interview to score. Find the interview notes first")
    b["interviews"] = interviews
    b["scanner"] = scanner.with_names(name, [iv["interviewer_raw"] for iv in interviews])
    b["dropped_links"] = list(r.dropped_links)
    return b


def load_interview(r, iv, p, config, rubric, forbidden):
    r.obj(iv, p)
    reject_keys(r, iv, forbidden, p, "is not allowed: bundles carry extracted evidence only (no transcripts) "
                "and never a hire/no-hire recommendation")
    scanner = config["_scanner"]
    title = r.text(iv, "title", p, required=False)
    out = {"interview_id": r.ident(iv, "interview_id", p),
           "meeting_id": r.ident(iv, "meeting_id", p, required=False),
           "url": r.url(iv, "url", p),
           "title": checked(r, scanner, config, title, p + ".title", FIELD_WHY, source="label", limit=FIELD_MAX),
           "date": r.day(iv, "date", p),
           "stage_id": r.text(iv, "stage_id", p, required=False),
           "recording_disclosed": r.boolean(iv, "recording_disclosed", p, nullable=True),
           "notes_status": r.enum(iv, "notes_status", p, ACCESS_STATUSES),
           "transcript_status": r.enum(iv, "transcript_status", p, ACCESS_STATUSES)}
    if out["stage_id"] is not None and out["stage_id"] not in rubric["stage_by_id"]:
        raise r.fail(p + ".stage_id", "unknown stage {}; use a stage_id from the rubric's interview_plan".format(
            shown(out["stage_id"])))
    who = r.obj(iv.get("interviewer"), p + ".interviewer")
    wp = p + ".interviewer"
    name, role = r.text(who, "name", wp), r.text(who, "role", wp, required=False)
    out["interviewer"] = {"name": checked_name(r, scanner, config, name, wp + ".name", "Amara O."),
                          "role": checked(r, scanner, config, role, wp + ".role", FIELD_WHY, source="label", limit=FIELD_MAX)}
    out["interviewer_raw"] = name
    asked = []
    for j, qid in enumerate(r.lst(iv.get("questions_asked"), p + ".questions_asked")):
        if not isinstance(qid, str) or qid not in rubric["questions"]:
            raise r.fail("{}.questions_asked[{}]".format(p, j), "unknown question id {}".format(shown(qid)))
        if qid not in asked:
            asked.append(qid)
    out["questions_asked"] = asked
    out["evidence"] = []
    for j, e in enumerate(r.lst(iv.get("evidence"), p + ".evidence")):
        ep = "{}.evidence[{}]".format(p, j)
        r.obj(e, ep)
        item = {"competency": competency_ref(r, e, ep, rubric),
                "kind": r.enum(e, "kind", ep, EVIDENCE_KINDS),
                "text": r.text(e, "text", ep),
                "evidence_kind": evidence_type(e.get("evidence_kind")),
                "timestamp": r.text(e, "timestamp", ep, required=False),
                "url": r.url(e, "url", ep)}
        if len(item["text"]) > config["max_quote_chars"]:
            raise r.fail(ep + ".text", "is {} characters (max {}). Quote only the sentence(s) that show the "
                         "behaviour; never paste transcripts".format(len(item["text"]), config["max_quote_chars"]))
        if item["timestamp"] is not None and not TIMESTAMP.match(item["timestamp"]):
            raise r.fail(ep + ".timestamp", "{} must look like 12:31 or 01:12:31".format(shown(item["timestamp"])))
        if item["kind"] == "quote":
            if e.get("speaker") != "candidate":
                raise r.fail(ep + ".speaker", "must be \"candidate\" for a transcript quote, got {}. If you can't "
                             "tell who said it, don't use it as a quote".format(shown(e.get("speaker"))))
            if out["transcript_status"] != "ok":
                raise r.fail(ep + ".kind", "is a transcript quote but transcript_status is {!r}".format(
                    out["transcript_status"]))
        elif out["notes_status"] != "ok":
            raise r.fail(ep + ".kind", "is from the notes but notes_status is {!r}".format(out["notes_status"]))
        out["evidence"].append(item)
    out["observations"] = []
    observation_ids = set()
    for j, ob in enumerate(r.lst(iv.get("observations"), p + ".observations")):
        op = "{}.observations[{}]".format(p, j)
        r.obj(ob, op)
        allowed = {"id", "competency", "source_index", "action", "object", "result"}
        if set(ob) - allowed:
            raise r.fail(op, "unknown fields; observations have no free-text slots")
        oid = ob.get("id")
        src = ob.get("source_index")
        if isinstance(oid, bool) or not isinstance(oid, int) or oid < 1 or oid in observation_ids:
            raise r.fail(op + ".id", "must be a unique positive integer")
        if isinstance(src, bool) or not isinstance(src, int) or not 0 <= src < len(out["evidence"]):
            raise r.fail(op + ".source_index", "must reference an evidence row by zero-based integer index")
        cid = competency_ref(r, ob, op, rubric)
        if out["evidence"][src]["competency"] != cid:
            raise r.fail(op + ".competency", "does not match the referenced evidence row")
        fields = {}
        for field, vocabulary in (("action", OBS_ACTIONS), ("object", OBS_OBJECTS), ("result", OBS_RESULTS)):
            value = ob.get(field)
            if not isinstance(value, str) or value not in vocabulary:
                raise r.fail(op + "." + field, "must be an exact code from the bundled observation vocabulary")
            fields[field] = value
        observation_ids.add(oid)
        out["observations"].append(dict(fields, id=oid, competency=cid, source_index=src))
    out["scores"] = []
    scored = set()
    for j, s in enumerate(r.lst(iv.get("scores"), p + ".scores")):
        sp = "{}.scores[{}]".format(p, j)
        r.obj(s, sp)
        cid = competency_ref(r, s, sp, rubric)
        if cid in scored:
            raise r.fail(sp + ".competency", "{!r} is scored twice in this interview".format(cid))
        scored.add(cid)
        score = s.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 4:
            raise r.fail(sp + ".score", "must be an integer 1-4, got {}".format(shown(score)))
        rationale = r.text(s, "rationale", sp)
        if len(rationale) > config["max_text_chars"]:
            raise r.fail(sp + ".rationale", "is longer than {} characters".format(config["max_text_chars"]))
        refs = s.get("observation_ids", [])
        if not isinstance(refs, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in refs):
            raise r.fail(sp + ".observation_ids", "must be a list of integer observation IDs")
        if len(set(refs)) != len(refs) or any(x not in observation_ids for x in refs):
            raise r.fail(sp + ".observation_ids", "must be unique IDs from this interview's observations")
        if any(ob["competency"] != cid for ob in out["observations"] if ob["id"] in refs):
            raise r.fail(sp + ".observation_ids", "all observations must belong to this competency")
        out["scores"].append({"competency": cid, "score": score, "rationale": rationale,
                              "observation_ids": refs,
                              "status": r.enum(s, "status", sp, SCORE_STATUSES, default="suggested")})
    out["filed"] = r.boolean(iv, "filed", p, default=False) if "filed" in iv else None
    if out["filed"]:
        for j, s in enumerate(out["scores"]):
            if s["status"] != "interviewer_confirmed":
                raise r.fail("{}.scores[{}].status".format(p, j), "is still \"suggested\" but filed is true. An "
                             "interviewer has filed only when every score they gave is interviewer_confirmed")
    out["gaps"] = []
    for j, g in enumerate(r.lst(iv.get("gaps"), p + ".gaps")):
        gp = "{}.gaps[{}]".format(p, j)
        r.obj(g, gp)
        cid = competency_ref(r, g, gp, rubric, required=False)
        note = r.text(g, "note", gp)
        if len(note) > config["max_text_chars"]:
            raise r.fail(gp + ".note", "is longer than {} characters".format(config["max_text_chars"]))
        out["gaps"].append({"competency": cid, "note": note})
    out["concerns"] = []
    for j, c in enumerate(r.lst(iv.get("concerns"), p + ".concerns")):
        cp = "{}.concerns[{}]".format(p, j)
        r.obj(c, cp)
        reject_keys(r, c, {"answer", "candidate_answer", "quote", "text", "response"}, cp,
                    "is not allowed: a concern records only the topic, never the question's wording or the answer")
        out["concerns"].append({"topic": r.enum(c, "topic", cp, config["_categories"]),
                                "timestamp": r.text(c, "timestamp", cp, required=False),
                                "url": r.url(c, "url", cp)})
        if out["concerns"][-1]["timestamp"] is not None and not TIMESTAMP.match(out["concerns"][-1]["timestamp"]):
            raise r.fail(cp + ".timestamp", "must look like 12:31 or 01:12:31")
    return out


def evidence_type(value):
    """The allowed evidence_kind for value ("behaviour_example", "Behavior example" and "behaviour-example" alike), or
    None: an untagged or unknown item is not rendered (and withdraws its competency's score), never an error."""
    if not isinstance(value, str):
        return None
    value = re.sub(r"[\s-]+", "_", value.strip().casefold()).replace("behavior", "behaviour")
    return value if value in EVIDENCE_TYPES else None


def competency_ref(r, obj, path, rubric, required=True):
    cid = r.text(obj, "competency", path, required=required)
    if cid is not None and cid not in rubric["by_id"]:
        raise r.fail(path + ".competency", "unknown competency {}; use an id from the rubric".format(shown(cid)))
    return cid


# --------------------------------------------------------------------------- analysis


def interview_key(iv):
    """One meeting, one key: the note's meeting id when known, otherwise the interview id."""
    return iv["meeting_id"] or iv["interview_id"]


def dedupe_interviews(interviews):
    """Drop repeated interview ids, and collapse two notes of the same meeting (same meeting_id, which
    happens when two people captured it) so one meeting never counts twice. Two different meetings are never
    merged, even with the same interviewer, date and stage."""
    notes = []
    groups = {}
    order = []
    seen_ids = set()
    for iv in interviews:
        if iv["interview_id"] in seen_ids:
            notes.append("Interview {} appeared twice in the bundle; counted once.".format(md(iv["interview_id"])))
            continue
        seen_ids.add(iv["interview_id"])
        key = interview_key(iv)
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(iv)
    kept = []
    for key in order:
        group = sorted(groups[key], key=lambda x: (-len(x["evidence"]), x["interview_id"]))
        kept.append(group[0])
        for other in group[1:]:
            notes.append("Two notes of the same meeting ({}): used {} and set aside {} so the meeting is counted "
                         "once.".format(md(key), md(group[0]["interview_id"]), md(other["interview_id"])))
    kept.sort(key=lambda x: (x["date"], x["interview_id"]))
    same_day = {}
    for iv in kept:
        same_day.setdefault((iv["interviewer"]["name"].casefold(), iv["date"], iv["stage_id"] or ""), []).append(iv)
    for group in same_day.values():
        if len(group) > 1:
            notes.append("{} has {} interviews on {}{} (different meetings: {}); each is counted. If they are notes "
                         "of one interview, keep only the interviewer's own note.".format(
                             group[0]["interviewer"]["name"], len(group), long_date(group[0]["date"]),
                             " for the same stage" if group[0]["stage_id"] else "",
                             ", ".join(md(interview_key(x)) for x in group)))
    return kept, notes


WITHDRAW_ORDER = ["rationale", "evidence", "consent", "no_evidence"]
WITHDRAWN_LABELS = {"rationale": "Withdrawn — rationale withdrawn", "evidence": "Withdrawn — evidence withdrawn",
                    "consent": "Withdrawn — recording consent", "no_evidence": "Withdrawn — no usable evidence"}
WITHDRAWN_HELP = {
    "rationale": "Its rationale was withdrawn (see Fairness & compliance). Score it yourself against the anchors, on "
                 "the evidence below only.",
    "evidence": "An evidence item for it was withdrawn (see Fairness & compliance), so its score was withdrawn too. It "
                "is not kept automatically: if the remaining evidence still supports a score, remove or restate the "
                "withdrawn item in the bundle and run again, or score it yourself against the anchors.",
    "consent": "It drew on transcript evidence that can't be used (recording not disclosed or not confirmed). Re-score "
               "from your own notes.",
    "no_evidence": "No usable evidence was quoted for it. Absence of evidence is not evidence of a weakness."}
NOT_ASSESSED = "No evidence — not assessed"
NOT_SCORED = "Not scored — evidence below"


WITHDRAWN_LABELS.update({
    "human_review": "Awaiting interviewer rating",
    "structured_source": "No valid structured source",
})
WITHDRAWN_HELP.update({
    "human_review": "The script does not turn extracted prose into a numeric rating. "
                   "Review the linked observations against the rubric and enter your own rating.",
    "structured_source": "An interviewer rating must reference usable observation IDs for this competency.",
})

def analyse_interview(iv, rubric, config, scanner):
    """Project source material into finite work-observation codes.

    This is NOT a semantic classifier. Raw prose is never exportable, even when
    the old scanner finds nothing or a caller marks it approved. An observation
    is a fallible extraction, not proof of relevance or source truth. Numeric
    scores come only from the interviewer's separately entered ratings.
    """
    a = {"iv": iv, "fairness": [], "removed": [], "contact_stripped": 0, "consent_dropped": 0}
    usable = {c["id"]: [] for c in rubric["competencies"]}
    accepted = set()
    rejected_comps = set()
    seen = set()
    referenced = set()
    for ob in iv.get("observations", []):
        source = iv["evidence"][ob["source_index"]]
        cid = ob["competency"]
        referenced.add(ob["source_index"])
        if iv["recording_disclosed"] is not True and source["kind"] in ("quote", "summary"):
            a["consent_dropped"] += 1
            rejected_comps.add(cid)
            continue
        # Source text is deliberately not classified here: the finite projection
        # cannot copy personal prose, and a keyword miss cannot authorize a score.
        identity = (cid, ob["source_index"], ob["action"], ob["object"], ob["result"])
        if identity in seen:
            continue
        seen.add(identity)
        accepted.add(ob["id"])
        sentence = "{} {}; result: {}.".format(OBS_ACTIONS[ob["action"]], OBS_OBJECTS[ob["object"]],
                                               OBS_RESULTS[ob["result"]])
        usable[cid].append({"competency": cid, "kind": source["kind"], "text": sentence,
                            "timestamp": source["timestamp"], "url": source["url"] or iv["url"],
                            "observation_id": ob["id"], "source_index": ob["source_index"],
                            "action": ob["action"], "object": ob["object"], "result": ob["result"]})
    # Legacy text is accepted for migration/source review, never silently treated
    # as a safe observation. A generic reason is reported without echoing the text.
    for idx, e in enumerate(iv["evidence"]):
        if idx not in referenced:
            a["fairness"].append({"type": STRUCTURED_ONLY, "competency": e["competency"], "categories": []})
            rejected_comps.add(e["competency"])
    a["evidence"] = usable
    scores = {}
    for item in iv["scores"]:
        cid = item["competency"]
        reason = None
        if item["status"] != "interviewer_confirmed":
            reason = "human_review"
        elif not item.get("observation_ids") or not set(item["observation_ids"]) <= accepted:
            reason = "structured_source"
        elif cid in rejected_comps:
            reason = "evidence"
        if reason:
            a["removed"].append({"competency": cid, "reason": reason, "status": item["status"]})
            continue
        # Do not copy the input rationale, even if "confirmed".
        scores[cid] = dict(item, rationale="Interviewer-entered rating linked to observations {}. "
                          "Review the original source and rubric before filing.".format(
                              ", ".join(str(x) for x in item["observation_ids"])),
                           weak=all(e["kind"] == "summary" for e in usable[cid]
                                    if e["observation_id"] in item["observation_ids"]))
    a["scores"] = scores
    a["withdrawn"] = {r["competency"]: r["reason"] for r in a["removed"]}
    a["gaps"] = []
    for g in iv["gaps"]:
        a["fairness"].append({"type": "free_text_gap", "competency": g["competency"], "categories": []})
    stage = rubric["stage_by_id"].get(iv["stage_id"]) if iv["stage_id"] else None
    a["stage"] = stage
    a["owned"] = list(stage["competencies"]) if stage else [c["id"] for c in rubric["competencies"]]
    a["extra"] = [c["id"] for c in rubric["competencies"]
                  if c["id"] not in a["owned"] and (c["id"] in scores or usable[c["id"]]
                                                    or c["id"] in a["withdrawn"])]
    a["unasked"] = [q for q in stage["questions"] if q["id"] not in iv["questions_asked"]] if stage else []
    # A caller's "filed" assertion cannot promote an auto-proposed or withheld score.
    a["filed"] = bool(iv["filed"]) and not a["removed"] if iv["filed"] is not None else \
        bool(scores) and not a["removed"]
    return a


def score_cell(a, cid, rubric):
    """The one label used for a competency everywhere (table, section heading, ATS draft)."""
    s = a["scores"].get(cid)
    if s:
        return score_label(s["score"], rubric)
    if cid in a["withdrawn"]:
        return WITHDRAWN_LABELS[a["withdrawn"][cid]]
    return NOT_SCORED if a["evidence"][cid] else NOT_ASSESSED


def unasked_question_for(cid, rubric, asked_ids):
    for stage in rubric["stages"]:
        for q in stage["questions"]:
            if q["competency"] == cid and q["id"] not in asked_ids:
                return q
    return None


def panel_analysis(analyses, rubric, config):
    rows = []
    for comp in rubric["competencies"]:
        cid = comp["id"]
        cells = []
        for a in analyses:
            s = a["scores"].get(cid)
            cells.append(s)
        values = [c["score"] for c in cells if c]
        spread = (max(values) - min(values)) if len(values) >= 2 else None
        rows.append({"competency": comp, "cells": cells, "values": values, "spread": spread,
                     "disagreement": spread is not None and spread >= config["disagreement_spread"],
                     "uncovered": not values,
                     "evidence_count": sum(len(a["evidence"][cid]) for a in analyses)})
    return rows


# --------------------------------------------------------------------------- rendering helpers


def long_date(d):
    return "{} {} {}, {}".format(WEEKDAYS[d.weekday()], MONTHS[d.month - 1], d.day, d.year)


def cell(text):
    return text.replace("|", "\\|")


def provider_label(src):
    if not src:
        return "not configured"
    return PROVIDER_LABELS.get(src["provider"] or "", src["provider"] or "unknown")


def score_label(score, rubric):
    return "{} — {}".format(score, rubric["scale_labels"][str(score)])


def evidence_line(e):
    ref = "{} source row {}".format(KIND_LABELS[e["kind"]], e["source_index"] + 1)
    if e["timestamp"]:
        ref += ", " + e["timestamp"]
    if e["url"]:
        ref += ", [review meeting]({})".format(e["url"])
    return "- Observation {}: {} ({})".format(e["observation_id"], e["text"], ref)


def interview_ref(iv):
    parts = [long_date(iv["date"])]
    if iv["url"]:
        parts.append("[meeting notes]({})".format(iv["url"]))
    return " · ".join(parts)


def interviewer_label(iv):
    who = iv["interviewer"]
    return "{} ({})".format(who["name"], who["role"]) if who["role"] else who["name"]


def comp_name(comp):
    return comp["name"] + (" ★" if comp["must_have"] else "")


def consent_lines(analyses):
    lines = []
    for a in analyses:
        iv = a["iv"]
        who = iv["interviewer"]["name"]
        if iv["recording_disclosed"] is False:
            lines.append("- {} Recording/transcription was **not disclosed** to the candidate in {}'s interview "
                         "({}). Transcript quotes and AI-summary evidence from it were not used ({} item(s)); only "
                         "the interviewer's own notes count. Tell the recruiting lead.".format(
                             WARN, who, long_date(iv["date"]), a["consent_dropped"]))
        elif iv["recording_disclosed"] is None:
            lines.append("- {} Recording disclosure not confirmed for {}'s interview ({}). Until it is, transcript "
                         "quotes and AI-summary evidence from it are not used ({} item(s)) and no ATS draft is made. "
                         "If the candidate was told, rerun with recording_disclosed: true; if not, with false.".format(
                             WARN, who, long_date(iv["date"]), a["consent_dropped"]))
    if not lines:
        lines.append("- Recording/transcription disclosure confirmed for every interview used.")
    return lines


def flag_reason(categories, config):
    """(is it hiring-decision language?, printed labels of the other categories) for a flagged item."""
    others = [c for c in categories if c != DECISION]
    return DECISION in categories, labels_for(others, config)


def fairness_lines(analyses, rubric, config, panel):
    lines = []
    for a in analyses:
        iv = a["iv"]
        prefix = "{}'s interview: ".format(iv["interviewer"]["name"]) if panel else ""
        for f in a["fairness"]:
            comp = rubric["by_id"][f["competency"]]["name"] if f["competency"] else "general"
            decision, cats = flag_reason(f["categories"], config)
            also = " It also mentioned {}.".format(cats) if decision and cats else ""
            if f["type"] == STRUCTURED_ONLY:
                lines.append("- {} {}A source item for {} has no structured observation and was not exported. "
                             "Its text stays in the source; the reviewer may extract a supported work observation."
                             .format(WARN, prefix, comp))
            elif f["type"] == "free_text_gap":
                lines.append("- {} {}A free-text gap note for {} was not exported. Use the rubric's planned "
                             "questions for follow-up.".format(WARN, prefix, comp))
            elif f["type"] == "untagged_evidence":
                lines.append("- {} {}One evidence item for {} was not shown: it wasn't tagged with an allowed "
                             "evidence_kind ({}). Evidence must be tagged as work-related; the interviewer must still review it.".format(
                                 WARN, prefix, comp, ", ".join(EVIDENCE_TYPES.values())))
            elif f["type"] == NOT_JOB_RELATED:
                lines.append("- {} {}One evidence item for {} was held back: it was not shown to be job-related "
                             "(every sentence must be a concrete clause about the candidate's work, such as what they "
                             "built, traced, changed or delivered). It was not used and is not reproduced here; the "
                             "interviewer must review it in the source notes.".format(WARN, prefix, comp))
            elif f["type"] == RATIONALE_NOT_JOB_RELATED:
                lines.append("- {} {}The suggested score for {} was withdrawn: its rationale was not shown to be "
                             "job-related (every sentence must be about the candidate's work or the evidence against "
                             "the anchors). It is not reproduced here. Score it again on job-related evidence "
                             "only.".format(WARN, prefix, comp))
            elif f["type"] == GAP_NOT_JOB_RELATED:
                lines.append("- {} {}A gap note ({}) was dropped: it was not shown to be job-related (it must name "
                             "work to probe, such as a skill, a task or a planned question). It is not reproduced "
                             "here.".format(WARN, prefix, comp))
            elif f["type"] == "redacted_evidence":
                if decision:
                    lines.append("- {} {}One evidence item for {} was withdrawn: hiring-decision language.{} It was "
                                 "not used and is not reproduced here.".format(WARN, prefix, comp, also))
                else:
                    lines.append("- {} {}One evidence item for {} mentioned {} and was withdrawn. It was not used and "
                                 "is not reproduced here.".format(WARN, prefix, comp, cats))
            elif f["type"] == "withdrawn_score":
                if decision:
                    lines.append("- {} {}The suggested score for {} was withdrawn: hiring-decision language in its "
                                 "rationale.{} Scores describe evidence against the anchors; the hire/no-hire call "
                                 "is made by people, outside this scorecard.".format(WARN, prefix, comp, also))
                else:
                    lines.append("- {} {}The suggested score for {} was withdrawn: its rationale referred to {}. "
                                 "Score it again on job-related evidence only.".format(WARN, prefix, comp, cats))
            else:
                if decision:
                    lines.append("- {} {}A gap note ({}) was withdrawn: hiring-decision language.{}".format(
                        WARN, prefix, comp, also))
                else:
                    lines.append("- {} {}A gap note ({}) referred to {} and was dropped.".format(
                        WARN, prefix, comp, cats))
        for rem in a["removed"]:
            if rem["reason"] == "evidence":
                lines.append("- {} {}The {} score for {} was withdrawn because one of its evidence items was "
                             "withdrawn. It is not kept automatically: if the remaining evidence still supports a "
                             "score, remove or restate the withdrawn item in the bundle and run again.".format(
                                 WARN, prefix, "interviewer's" if rem["status"] == "interviewer_confirmed" else
                                 "suggested", rubric["by_id"][rem["competency"]]["name"]))
        for c in iv["concerns"]:
            where = []
            if c["timestamp"]:
                where.append("at " + c["timestamp"])
            if c["url"] or iv["url"]:
                where.append("[source]({})".format(c["url"] or iv["url"]))
            lines.append("- {} {}A question in this interview touched on {}{}. This may be unlawful to ask. The "
                         "candidate's answer is not recorded and must not be considered. Raise it with the recruiting "
                         "lead or HR.".format(WARN, prefix, config["category_labels"][c["topic"]],
                                              (" (" + ", ".join(where) + ")") if where else ""))
        if a["contact_stripped"]:
            lines.append("- {}{} contact detail(s) or link(s) removed from evidence, rationales or gap notes.".format(
                prefix, a["contact_stripped"]))
    if not lines:
        lines.append("- Export contains controlled work observations only. Source relevance and accuracy require "
                     "interviewer review; absence of a keyword flag is not a fairness clearance.")
    return lines


def sources_lines(b, analyses):
    notes, tr = b["sources"]["notes"], b["sources"]["transcript"]
    parts = ["Notes: {} {}".format(provider_label(notes), "✓" if notes["status"] == "ok" else "✗"),
             "Transcript: {} {}".format(provider_label(tr), "✓" if tr["status"] == "ok" else "✗")]
    ats = b["sources"]["ats"]
    parts.append("ATS: {}".format("{} {}".format(provider_label(ats), "✓" if ats["status"] == "ok" else "✗")
                                  if ats else "not configured"))
    gaps = []
    for key, src in [("Notes", notes), ("Transcript", tr), ("ATS", ats)]:
        if src and src["status"] != "ok":
            gaps.append("{} {}{}".format(key, src["status"].replace("_", " "),
                                         ""))
    for a in analyses:
        iv = a["iv"]
        for key, status in [("notes", iv["notes_status"]), ("transcript", iv["transcript_status"])]:
            if status != "ok" and not (key == "transcript" and tr["status"] != "ok" and status == tr["status"]):
                gaps.append("{} {} for {}'s interview ({})".format(key, status.replace("_", " "),
                                                                    iv["interviewer"]["name"], long_date(iv["date"])))
    if b["dropped_links"]:
        gaps.append("{} link(s) dropped because they were not http(s) URLs".format(len(b["dropped_links"])))
    return ["Sources: " + " · ".join(parts), "Gaps: " + ("; ".join(gaps) + "." if gaps else "none.")]


def data_handling_line():
    return ("_Data handling: This draft exports controlled work observations; it does not verify that source material is accurate or job-related. The interviewer must review every source link. Delete the working bundle and any transcript "
            "copies once the scorecard is filed; keep the filed scorecard only as long as your hiring-records policy "
            "says. Candidates may ask to see or delete their data under applicable privacy law; route those requests "
            "to your recruiting lead._")


# --------------------------------------------------------------------------- single scorecard


def render_single(b, a, rubric, config, as_of, ats):
    iv = a["iv"]
    comps = rubric["competencies"]
    owned = a["owned"]
    assessed = [cid for cid in owned if cid in a["scores"]]
    missing = [cid for cid in owned if cid not in a["scores"] and cid not in a["withdrawn"]]
    withdrawn = [cid for cid in owned if cid in a["withdrawn"]]
    lines = ["## Interview scorecard (DRAFT): {} — {}".format(b["candidate"]["display_name"], rubric["role_title"]),
             "",
             "_Draft for {} to review. Work observations use a fixed vocabulary and link to their source. Ratings are "
             "entered by the interviewer; the script does not score unvalidated prose. Whether to hire is the "
             "interviewer's and panel's call._".format(
                 iv["interviewer"]["name"]),
             ""]
    stage = a["stage"]
    lines.append("**Interview:** {} · {} · {}".format(stage["stage_name"] if stage else "Interview (stage not assigned)",
                                                     interviewer_label(iv), interview_ref(iv)))
    lines.append("**Rubric:** {} v{} (approved by {}) · scale 1–4 · ★ = must-have".format(
        rubric["role_id"], rubric["version"], rubric["approved_by"]))
    lines.append("")
    scope = "in this stage's plan" if stage else "in the rubric"
    observed = [cid for cid in owned if a["evidence"][cid]]
    summary = "**Summary** — {} of {} competencies {} have structured observations; {} interviewer rating(s).".format(
        len(observed), len(owned), scope, len(assessed))
    for label, ids in [("Not assessed", missing), ("Score withdrawn", withdrawn)]:
        if ids:
            summary += " {}: {}.".format(label, ", ".join(
                rubric["by_id"][c]["name"] + (" (must-have)" if rubric["by_id"][c]["must_have"] else "") for c in ids))
    extras = []
    if a["unasked"]:
        extras.append("{} planned question(s) not asked".format(len(a["unasked"])))
    flag_count = len(a["fairness"]) + len(iv["concerns"])
    if flag_count:
        extras.append("{} review flag(s)".format(flag_count))
    if a["removed"]:
        extras.append("{} score(s) withdrawn".format(len(a["removed"])))
    if extras:
        summary += " " + "; ".join(extras)[0].upper() + "; ".join(extras)[1:] + "."
    lines += [summary, ""]
    shown_ids = owned + a["extra"]
    lines += ["| Competency | Interviewer rating | Work observations |", "|---|---|---|"]
    for cid in shown_ids:
        comp = rubric["by_id"][cid]
        ev = a["evidence"][cid]
        s = a["scores"].get(cid)
        score = score_cell(a, cid, rubric)
        if s:
            score += " (interviewer)" if s["status"] == "interviewer_confirmed" else " (draft)"
        if s and s["weak"]:
            score += " " + WARN
        name = comp_name(comp) + (" (outside this stage)" if cid not in owned else "")
        lines.append("| {} | {} | {} |".format(cell(name), cell(score), evidence_count(ev)))
    others = [c for c in comps if c["id"] not in shown_ids]
    if others:
        lines += ["", "Owned by other stages (not expected here): {}.".format(", ".join(
            "{} ({})".format(c["name"], stage_of(c["id"], rubric)) for c in others))]
    lines.append("")
    for cid in shown_ids:
        comp = rubric["by_id"][cid]
        lines.append("### {}{}{}".format(comp["name"], " (must-have)" if comp["must_have"] else "",
                                         " (outside this stage's plan)" if cid not in owned else ""))
        s = a["scores"].get(cid)
        ev = a["evidence"][cid]
        if s:
            lines.append("**{}: {}**{}".format("Interviewer score" if s["status"] == "interviewer_confirmed"
                                               else "Suggested", score_label(s["score"], rubric),
                                               "" if s["status"] == "interviewer_confirmed" else
                                               " (draft — edit or replace)"))
            lines.append("Anchor {}: {}".format(s["score"], comp["anchors"][str(s["score"])]))
            lines.append("Rationale: {}".format(s["rationale"]))
            if s["weak"]:
                lines.append("{} Backed only by the AI summary. Check it against the transcript or your own notes "
                             "before filing.".format(WARN))
        else:
            lines.append("**{}.** {}".format(score_cell(a, cid, rubric), WITHDRAWN_HELP.get(
                a["withdrawn"].get(cid), "Evidence was captured but no score was suggested; score it yourself "
                "against the anchors." if ev else "Absence of evidence is not evidence of a weakness.")))
        shown = ev[:config["max_evidence_single"]]
        if shown:
            lines.append("Evidence:")
            lines += [evidence_line(e) for e in shown]
            if len(ev) > len(shown):
                lines.append("- … {} more item(s) in the bundle.".format(len(ev) - len(shown)))
        lines.append("")
    lines.append("### Gaps to probe next round")
    gap_lines = []
    for cid in missing + withdrawn:
        comp = rubric["by_id"][cid]
        q = unasked_question_for(cid, rubric, iv["questions_asked"])
        gap_lines.append("{}{} — {}.{}".format(comp["name"], " (must-have)" if comp["must_have"] else "",
                                               "not assessed" if cid in missing else "score withdrawn",
                                               " Planned question: “{}”".format(q["text"]) if q else ""))
    for g in a["gaps"]:
        name = rubric["by_id"][g["competency"]]["name"] if g["competency"] else "General"
        gap_lines.append("{} — {}".format(name, g["note"]))
    lines += ["{}. {}".format(i + 1, t) for i, t in enumerate(gap_lines)] or ["None recorded."]
    lines += ["", "### Planned questions not asked"]
    if a["unasked"]:
        lines += ["- {} ({}): “{}”".format(q["id"], rubric["by_id"][q["competency"]]["name"], q["text"])
                  for q in a["unasked"]]
    elif stage:
        lines.append("None — every planned question for this stage was asked.")
    else:
        lines.append("Not checked: this interview has no stage in the rubric's interview plan.")
    lines += ["", "### Fairness & compliance"]
    lines += consent_lines([a])
    lines += fairness_lines([a], rubric, config, panel=False)
    if ats:
        lines += [""] + render_ats(b, a, rubric, config, ats)
    lines.append("")
    lines += sources_lines(b, [a])
    lines.append("Prepared {} from {} interview note(s).".format(as_of.isoformat(), 1))
    lines.append(data_handling_line())
    return "\n".join(lines) + "\n"


def evidence_count(ev):
    if not ev:
        return "—"
    counts = {}
    for e in ev:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    names = {"quote": "quote", "note": "note", "summary": "AI summary"}
    return ", ".join("{} {}{}".format(counts[k], names[k], "s" if counts[k] > 1 and k != "summary" else "")
                     for k in EVIDENCE_KINDS if k in counts)


def stage_of(cid, rubric):
    names = [s["stage_name"] for s in rubric["stages"] if cid in s["competencies"]]
    return ", ".join(names) if names else "no stage"


def render_ats(b, a, rubric, config, provider):
    """ATS draft: rubric competency, suggested score with the rubric's own scale label, and verbatim evidence
    (transcript quotes and interviewer notes) with links. Never agent-written text: no rationale, gap note or AI summary, and the
    recommendation field is left empty for the interviewer."""
    iv = a["iv"]
    label = config["ats_providers"][provider]
    lines = ["### ATS draft ({}) — not submitted".format(label),
             "Submit or paste only after {} has reviewed and approved this exact text. It carries rubric scores, the "
             "rubric's labels and controlled work observations only. Source prose is never included.".format(
                 iv["interviewer"]["name"]),
             "", "| Rubric competency | Interviewer rating |", "|---|---|"]
    ids = a["owned"] + a["extra"]
    for cid in ids:
        lines.append("| {} | {} |".format(rubric["by_id"][cid]["name"], score_cell(a, cid, rubric)))
    lines += ["", "Structured work observations (not verbatim):"]
    quoted = 0
    for cid in ids:
        for e in a["evidence"][cid]:
            if e["kind"] in ("quote", "note"):
                lines.append("- {}: {}".format(rubric["by_id"][cid]["name"], evidence_line(e)[2:]))
                quoted += 1
    if not quoted:
        lines.append("- None (no structured observations were usable).")
    lines += ["", "**Recommendation (interviewer writes this):** (empty)",
              "_Left empty on purpose: the hire/no-hire recommendation is the interviewer's own._"]
    return lines


# --------------------------------------------------------------------------- panel debrief


def full_stop(names):
    """'.' to end a sentence that ends with these names, unless the last one already ends with one ("Lena V.")."""
    return "" if names and names[-1].endswith(".") else "."


def panel_status(analyses):
    pending = [a["iv"]["interviewer"]["name"] for a in analyses if not a["filed"]]
    return not pending, pending


def render_panel(b, analyses, rows, rubric, config, as_of, dedupe_notes, preview=False):
    ready, pending = panel_status(analyses)
    show_scores = ready or preview
    filed = len(analyses) - len(pending)
    heading = "## Panel debrief (DRAFT{}): {} — {}".format("" if ready else ", NOT READY TO SHARE",
                                                          b["candidate"]["display_name"], rubric["role_title"])
    if ready:
        intro = ("_Draft for the hiring panel. Every interviewer has filed (confirmed) their own scores, so this can "
                 "be shared with the panel. Each column is one interviewer's filed scores. The hire/no-hire decision "
                 "is the panel's. Read the scores before discussing, so nobody anchors on the first opinion voiced._")
    elif preview:
        intro = ("**Not ready to share: hiring-manager preview.** {} of {} interviewer(s) have filed; still waiting "
                 "on {}{} Do not show these scores to anyone who hasn't filed theirs. * marks a score the interviewer "
                 "has not confirmed yet.".format(filed, len(analyses), ", ".join(pending), full_stop(pending)))
    else:
        intro = ("**Not ready to share.** {} of {} interviewer(s) have filed; still waiting on {}{} Every score is "
                 "hidden until all of them have confirmed their own scores, so nobody anchors on someone else's. "
                 "Rerun when they have. (An interviewer who filed with no scores counts once the bundle says "
                 "\"filed\": true.)".format(filed, len(analyses), ", ".join(pending), full_stop(pending)))
    legend = ("— = in this interviewer's stage but no evidence (not assessed) · n/a = not in this interviewer's stage"
              if show_scores else "✓ = scored (value hidden) · — = in this interviewer's stage but not assessed · "
              "n/a = not in this interviewer's stage")
    if any(a["withdrawn"] for a in analyses):
        legend += " · w/d = score withdrawn (see Fairness & compliance)"
    lines = [heading, "", intro, "",
             "**Rubric:** {} v{} (approved by {}) · scale 1–4 · ★ = must-have · {}".format(
                 rubric["role_id"], rubric["version"], rubric["approved_by"], legend),
             "**Interviews:** " + "; ".join("{} — {} · {}{}".format(
                 a["stage"]["stage_name"] if a["stage"] else "Interview (stage not assigned)",
                 interviewer_label(a["iv"]), interview_ref(a["iv"]), "" if ready or a["filed"] else " (not filed)")
                 for a in analyses),
             ""]
    disagreements = [r for r in rows if r["disagreement"]] if show_scores else []
    uncovered = [r for r in rows if r["uncovered"]]
    covered = len(rows) - len(uncovered)
    summary = "**Summary** — {} interview(s); {} of {} competencies assessed by at least one interviewer.".format(
        len(analyses), covered, len(rows))
    if disagreements:
        summary += " Disagreement on {}.".format(", ".join(r["competency"]["name"] for r in disagreements))
    if uncovered:
        summary += " Nobody assessed {}.".format(", ".join(
            r["competency"]["name"] + (" (must-have)" if r["competency"]["must_have"] else "") for r in uncovered))
    if len(analyses) == 1:
        summary += " Only one interview was found, so there is nothing to compare yet."
    lines += [summary, ""]
    header = "| Competency | " + " | ".join(cell("{} ({})".format(
        a["iv"]["interviewer"]["name"], a["stage"]["stage_name"] if a["stage"] else long_date(a["iv"]["date"])))
        for a in analyses) + (" | Spread | Flag |" if show_scores else " | Flag |")
    lines += [header, "|---|" + "---|" * len(analyses) + ("---|---|" if show_scores else "---|")]
    for r in rows:
        cells = []
        for a, s in zip(analyses, r["cells"]):
            if s and show_scores:
                cells.append("{}{}".format(s["score"], "" if s["status"] == "interviewer_confirmed" else "*"))
            elif s:
                cells.append("✓")
            elif r["competency"]["id"] in a["withdrawn"]:
                cells.append("w/d")
            else:
                cells.append("—" if r["competency"]["id"] in a["owned"] else "n/a")
        flag = ""
        if r["disagreement"] and show_scores:
            flag = WARN + " Disagreement"
        elif r["uncovered"]:
            flag = WARN + " Nobody assessed" + (" (must-have)" if r["competency"]["must_have"] else "")
        if show_scores:
            lines.append("| {} | {} | {} | {} |".format(cell(comp_name(r["competency"])), " | ".join(cells),
                                                        r["spread"] if r["spread"] is not None else "", flag))
        else:
            lines.append("| {} | {} | {} |".format(cell(comp_name(r["competency"])), " | ".join(cells), flag))
    lines.append("")
    if show_scores:
        lines.append("### Discuss first: disagreements")
        if disagreements:
            for r in disagreements:
                comp = r["competency"]
                lines.append("**{}** — spread {} (threshold {}).".format(comp["name"], r["spread"],
                                                                        config["disagreement_spread"]))
                for a, s in zip(analyses, r["cells"]):
                    if not s:
                        continue
                    lines.append("- {}: {}{}. {}{}".format(
                        a["iv"]["interviewer"]["name"], score_label(s["score"], rubric),
                        "" if s["status"] == "interviewer_confirmed" else " (suggested)", s["rationale"],
                        (" " + WARN + " AI summary only.") if s["weak"] else ""))
                    for e in a["evidence"][comp["id"]][:config["max_evidence_panel"]]:
                        lines.append("  " + evidence_line(e))
                lines.append("")
        else:
            lines += ["None at the threshold of {}.".format(config["disagreement_spread"]), ""]
    lines.append("### Not covered by anyone")
    if uncovered:
        for r in uncovered:
            comp = r["competency"]
            asked = set()
            for a in analyses:
                asked.update(a["iv"]["questions_asked"])
            q = unasked_question_for(comp["id"], rubric, asked)
            lines.append("- {}{} — no interviewer scored it{}. {}".format(
                comp["name"], " (must-have)" if comp["must_have"] else "",
                " ({} evidence item(s) captured but unscored)".format(r["evidence_count"]) if r["evidence_count"]
                else "", "Suggested follow-up question: “{}”".format(q["text"]) if q else "Plan a follow-up."))
    else:
        lines.append("Every competency was assessed by at least one interviewer.")
    if show_scores:
        lines += ["", "### Evidence by competency"]
    for r in rows if show_scores else []:
        comp = r["competency"]
        entries = []
        for a, s in zip(analyses, r["cells"]):
            if not s:
                continue
            if r["disagreement"]:
                continue
            first = a["evidence"][comp["id"]][:1]
            entries.append("- {}: {}{}. {}{}".format(
                a["iv"]["interviewer"]["name"], score_label(s["score"], rubric),
                "" if s["status"] == "interviewer_confirmed" else " (suggested)", s["rationale"],
                (" " + WARN + " AI summary only.") if s["weak"] else ""))
            entries += ["  " + evidence_line(e) for e in first]
        if entries:
            lines.append("**{}**".format(comp["name"]))
            lines += entries
    if show_scores and not any(not r["uncovered"] and not r["disagreement"] for r in rows):
        lines.append("Nothing beyond the disagreements above.")
    lines += ["", "### Gaps to probe and questions not asked"]
    gap_lines = []
    for a in analyses:
        who = a["iv"]["interviewer"]["name"]
        for cid in a["owned"]:
            if cid not in a["scores"] and cid not in a["withdrawn"] and a["stage"]:
                gap_lines.append("- {}: {} was in this stage's plan but not assessed.".format(
                    who, rubric["by_id"][cid]["name"]))
        for g in a["gaps"]:
            name = rubric["by_id"][g["competency"]]["name"] if g["competency"] else "General"
            gap_lines.append("- {}: {} — {}".format(who, name, g["note"]))
        for q in a["unasked"]:
            gap_lines.append("- {}: planned question {} not asked — “{}”".format(who, q["id"], q["text"]))
        for rem in a["removed"]:
            gap_lines.append("- {}: {} — {}. {}".format(who, rubric["by_id"][rem["competency"]]["name"],
                                                         WITHDRAWN_LABELS[rem["reason"]],
                                                         WITHDRAWN_HELP[rem["reason"]].replace(" below", "")))
    lines += gap_lines or ["None recorded."]
    lines += ["", "### Fairness & compliance"]
    lines += consent_lines(analyses)
    lines += fairness_lines(analyses, rubric, config, panel=True)
    for n in dedupe_notes:
        lines.append("- " + n)
    lines.append("")
    lines += sources_lines(b, analyses)
    lines.append("Prepared {} from {} interview note(s).".format(as_of.isoformat(), len(analyses)))
    lines.append(data_handling_line())
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- log


def run_key(b, analyses):
    if b["mode"] == "single":
        return "single:{}:{}".format(b["role_id"], interview_key(analyses[0]["iv"]))
    ids = ",".join(sorted(interview_key(a["iv"]) for a in analyses))
    digest = hashlib.sha1(ids.encode("utf-8")).hexdigest()[:12]
    return "panel:{}:{}:{}".format(b["role_id"], b["candidate"]["ref"], digest)


def check_log_path(path, write):
    """Fail before anything is printed if the log can't be read (or, for a run that writes, written)."""
    path = Path(path)
    if path.exists():
        if not path.is_file():
            raise InputError("log {}: is not a file".format(path))
        if not os.access(str(path), os.R_OK | (os.W_OK if write else 0)):
            raise InputError("log {}: is not {}".format(path, "writable" if write else "readable"))
        return
    folder = path.parent if str(path.parent) else Path(".")
    if not folder.is_dir():
        raise InputError("log {}: folder {} does not exist; create it or choose another --log".format(path, folder))
    if write and not os.access(str(folder), os.W_OK | os.X_OK):
        raise InputError("log {}: folder {} is not writable".format(path, folder))


def read_log(path):
    """{key: {"row": [...], "status": "pending"|"delivered"}} in file order. Any malformed row stops the run."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise InputError("log {}: cannot be read; refusing to run blind".format(path)) from None
    if not lines or lines[0].strip() != LOG_TITLE or LOG_HEADER not in [x.strip() for x in lines]:
        raise InputError("log {}: not a scorecard log (expected '{}' and the table header {})".format(
            path, LOG_TITLE, LOG_HEADER))
    start = [x.strip() for x in lines].index(LOG_HEADER)
    if start + 1 >= len(lines) or lines[start + 1].strip() != LOG_SEPARATOR:
        raise InputError("log {}: line {}: expected the table separator {}".format(path, start + 2, LOG_SEPARATOR))
    rows = {}
    for n, line in enumerate(lines[start + 2:], start + 3):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if not line.strip().startswith("|") or len(parts) != 7 or not SAFE_KEY.match(parts[0]) \
                or parts[6] not in LOG_STATUSES:
            raise InputError("log {}: line {} is not a valid log row (7 columns, a key and a status of {}). Fix or "
                             "remove it; refusing to guess what was delivered".format(
                                 path, n, " or ".join(LOG_STATUSES)))
        rows[parts[0]] = {"row": parts, "status": parts[6]}
    return rows


def read_listed(path):
    """The date of the last completed scheduled listing ("Listed through: YYYY-MM-DD" above the table), or None."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise InputError("log {}: cannot be read; refusing to run blind".format(path)) from None
    found = None
    for n, line in enumerate(lines, 1):
        if line.strip() == LOG_HEADER:
            break
        if line.startswith("Listed through:"):
            m = LISTED.match(line.strip())
            found = parse_day(m.group(1)) if m else None
            if found is None:
                raise InputError("log {}: line {}: expected 'Listed through: YYYY-MM-DD'".format(path, n))
    return found


def write_log(path, rows, listed=None):
    """Rewrite the whole log atomically (temp file + rename), keeping its 'Listed through' date unless one is given."""
    path = Path(path)
    listed = listed or read_listed(path)
    head = [LOG_TITLE, ""] + (["Listed through: " + listed.isoformat(), ""] if listed else [])
    body = head + [LOG_HEADER, LOG_SEPARATOR] + ["| " + " | ".join(r["row"]) + " |" for r in rows.values()]
    tmp = path.with_name("{}.tmp-{}".format(path.name, os.getpid()))
    try:
        tmp.write_text("\n".join(body) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise InputError("log {}: cannot be written ({}); nothing was printed or recorded".format(
            path, exc.strerror or exc)) from None


def lock_identity(path):
    """(device, inode, mtime_ns, contents) of the regular file at path, or None when there is none. Anything else at
    the path (a folder) is an input error rather than a lock that never clears."""
    try:
        st = os.lstat(str(path))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InputError("lock {}: cannot be read ({})".format(path, exc.strerror or exc)) from None
    if not stat.S_ISREG(st.st_mode):
        raise InputError("lock {}: exists but is not a regular file (a folder?); remove it, then rerun".format(path))
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InputError("lock {}: cannot be read ({})".format(path, exc.strerror or exc)) from None
    try:
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino, st.st_mtime_ns, os.read(fd, 4096))
    finally:
        os.close(fd)


def guarded(guard, action):
    """Run action() while holding the short-lived takeover guard file; None when another run holds it."""
    lock_identity(guard)  # a folder at the guard path is an input error
    try:
        fd = os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:  # the guard is held for milliseconds; one older than a minute was left by a crash
            if abs(time.time() - os.lstat(str(guard)).st_mtime) > 60:
                os.remove(str(guard))
        except OSError:
            pass
        return None
    except OSError as exc:
        raise InputError("lock {}: cannot be created ({})".format(guard, exc.strerror or exc)) from None
    try:
        return action()
    finally:
        os.close(fd)
        try:
            os.remove(str(guard))
        except OSError:
            pass


def acquire_lock(log_path):
    """Exclusive lock file next to the log (O_CREAT|O_EXCL) holding this run's pid and a random token. A lock older
    than LOCK_STALE_MINUTES, or dated more than a minute in the future, was left by a crashed run: it is removed once,
    under a takeover guard, and only if the lock path still holds exactly the file judged stale."""
    lock = Path(str(log_path) + ".lock")
    guard = Path(str(log_path) + ".lock.takeover")
    token = "{} {}\n".format(os.getpid(), os.urandom(8).hex()).encode("ascii")
    for attempt in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            seen = lock_identity(lock)
            if seen is None:
                continue
            age = time.time() - seen[2] / 1e9
            if attempt == 0 and (age > LOCK_STALE_MINUTES * 60 or age < -60):
                def take_over():
                    if lock_identity(lock) != seen:
                        return False
                    try:
                        os.remove(str(lock))
                    except OSError:
                        return False
                    return True
                if guarded(guard, take_over) is True:
                    continue
            raise LockedError("log {}: another run holds {}. Wait for it to finish; a lock older than {} minutes (or "
                              "dated in the future) is cleared automatically".format(
                                  log_path, lock, LOCK_STALE_MINUTES)) from None
        except OSError as exc:
            raise InputError("log {}: cannot create lock file {} ({})".format(log_path, lock, exc.strerror or exc)) \
                from None
        try:
            os.write(fd, token)
            st = os.fstat(fd)
        finally:
            os.close(fd)
        return {"path": lock, "guard": guard, "token": token, "id": (st.st_dev, st.st_ino)}
    raise LockedError("log {}: could not take the lock {}".format(log_path, lock))


def release_lock(lock):
    """Remove the lock only if it is still this run's file (same device, inode and token)."""
    if lock is None:
        return

    def release():
        try:
            current = lock_identity(lock["path"])
        except InputError:
            return True
        if current is not None and current[:2] == lock["id"] and current[3] == lock["token"]:
            try:
                os.remove(str(lock["path"]))
            except OSError:
                pass
        return True
    for _ in range(50):  # removals are serialised with stale takeovers through the guard
        try:
            if guarded(lock["guard"], release):
                return
        except InputError:
            return
        time.sleep(0.02)


def rerun_rows(rows):
    """Logged keys a scheduled run must try again: skipped (notes unreadable) and pending (delivery not confirmed)."""
    todo = [r["row"] for r in rows.values() if r["status"] in ("skipped", "pending")]
    return [{"key": x[0], "status": x[6], "recorded": x[5]} for x in sorted(todo, key=lambda x: (x[5], x[0]))]


def next_window(log_path, as_of, as_json):
    """What a scheduled run should list: interviews that ended from the last completed listing (inclusive; overlap is
    harmless because delivered keys are skipped) to the end of --as-of, plus every skipped or pending key in the log."""
    check_log_path(log_path, write=False)
    rows = read_log(log_path)
    since = read_listed(log_path)
    try:
        start = min(since, as_of) if since else as_of - timedelta(days=FIRST_LOOKBACK_DAYS)
        end = as_of + timedelta(days=1)
    except OverflowError:
        raise InputError("--as-of: {} is out of range".format(as_of.isoformat())) from None
    rerun = rerun_rows(rows)
    if as_json:
        return json.dumps({"as_of": as_of.isoformat(), "custom_start": start.isoformat(), "custom_end": end.isoformat(),
                           "since": since.isoformat() if since else None, "first_lookback_days": FIRST_LOOKBACK_DAYS,
                           "rerun": rerun}, indent=2, sort_keys=True) + "\n"
    why = ("since the last completed listing on {}".format(since.isoformat()) if since else
           "no completed listing in the log yet, so the first run looks back {} days".format(FIRST_LOOKBACK_DAYS))
    lines = ["Window: list interviews that ended from {} up to (not including) {}: list_meetings with time_range "
             "custom, custom_start {}, custom_end {} ({}).".format(start.isoformat(), end.isoformat(),
                                                                   start.isoformat(), end.isoformat(), why)]
    if since and (as_of - since).days > 30:
        lines.append("{} The listing is {} days old: notes older than 30 days may be unreadable on free Granola "
                     "plans.".format(WARN, (as_of - since).days))
    lines.append("Re-run from the log: {} key(s).".format(len(rerun)))
    for x in rerun:
        if x["status"] == "skipped":
            lines.append("- {} — skipped on {} (notes not available): read the meeting again and rerun with "
                         "--scheduled.".format(x["key"], x["recorded"]))
        else:
            lines.append("- {} — pending since {} (delivery never confirmed): if it reached the interviewer, run "
                         "--confirm-delivered; otherwise rerun with --scheduled --resume-pending.".format(
                             x["key"], x["recorded"]))
    lines.append("When every listed interview and every key above has been run, record the listing: --log {} "
                 "--as-of {} --mark-listed.".format(log_path, as_of.isoformat()))
    return "\n".join(lines) + "\n"


def mark_listed(log_path, as_of):
    """Record that every interview up to --as-of was listed and run. Never moves the date back."""
    check_log_path(log_path, write=True)
    lock = acquire_lock(log_path)
    try:
        rows = read_log(log_path)
        since = read_listed(log_path)
        if since and since >= as_of:
            return "Kept: the log is already listed through {0}; kept {0}.\n".format(since.isoformat())
        write_log(log_path, rows, listed=as_of)
    finally:
        release_lock(lock)
    return "Recorded: interviews listed through {}. The next --next-window starts there.\n".format(as_of.isoformat())


def confirm_delivered(log_path, key):
    if not SAFE_KEY.match(key):
        raise InputError("--confirm-delivered: {} is not a scorecard key (single:… or panel:…)".format(shown(key)))
    check_log_path(log_path, write=True)
    lock = acquire_lock(log_path)
    try:
        rows = read_log(log_path)
        if key not in rows:
            raise InputError("log {}: no row for {}; nothing to confirm. Only keys a scheduled run recorded as "
                             "pending can be confirmed".format(log_path, key))
        if rows[key]["status"] == "delivered":
            return "Already confirmed: {} was marked delivered earlier.\n".format(key)
        if rows[key]["status"] != "pending":
            raise InputError("log {}: {} is {}: its notes couldn't be read, so no scorecard was drafted and there is "
                             "nothing to confirm. It stays {}; a later --scheduled run drafts it once the notes can "
                             "be read".format(log_path, key, rows[key]["status"], rows[key]["status"]))
        rows[key]["row"][6] = "delivered"
        rows[key]["status"] = "delivered"
        write_log(log_path, rows)
    finally:
        release_lock(lock)
    return "Confirmed: {} marked delivered. Scheduled runs will skip it.\n".format(key)


# --------------------------------------------------------------------------- json


def json_ready(b, analyses, rows, rubric, markdown, key, show_scores=True):
    out = {"mode": b["mode"], "role_id": b["role_id"], "candidate_ref": b["candidate"]["ref"], "key": key,
           "draft": True, "recommendation": None, "interviews": [], "markdown": markdown}
    for a in analyses:
        out["interviews"].append({
            "interview_id": a["iv"]["interview_id"], "meeting_id": a["iv"]["meeting_id"],
            "interviewer": a["iv"]["interviewer"]["name"],
            "stage_id": a["iv"]["stage_id"], "date": a["iv"]["date"].isoformat(), "filed": a["filed"],
            "scores": {cid: {"score": s["score"] if show_scores else None, "status": s["status"], "weak": s["weak"]}
                       for cid, s in sorted(a["scores"].items())},
            "not_assessed": [cid for cid in a["owned"] if cid not in a["scores"] and cid not in a["withdrawn"]],
            "withdrawn_scores": {cid: WITHDRAWN_LABELS[reason] for cid, reason in sorted(a["withdrawn"].items())},
             "evidence_counts": {cid: len(ev) for cid, ev in sorted(a["evidence"].items())},
            "observations": {cid: [{k: e[k] for k in ("observation_id", "source_index", "action", "object",
                                                      "result", "kind", "timestamp", "url")} for e in ev]
                             for cid, ev in sorted(a["evidence"].items())},
            "export_policy": "controlled-work-observations-v1",
            "removed_scores": a["removed"],
            "fairness_flags": a["fairness"],
            "concerns": [c["topic"] for c in a["iv"]["concerns"]],
            "unasked_questions": [q["id"] for q in a["unasked"]],
            "recording_disclosed": a["iv"]["recording_disclosed"]})
    if rows is not None:
        ready, pending = panel_status(analyses)
        out["ready_to_share"] = ready
        out["pending_interviewers"] = pending
        out["panel"] = [{"competency": r["competency"]["id"],
                         "scores": [s["score"] if s and show_scores else None for s in r["cells"]],
                         "spread": r["spread"] if show_scores else None,
                         "disagreement": r["disagreement"] if show_scores else None, "uncovered": r["uncovered"]}
                        for r in rows]
    return out


# --------------------------------------------------------------------------- main


def rubric_summary(rubric, path):
    lines = ["Rubric {}: {} ({}) v{} — {}.".format(
        Path(path).name, rubric["role_title"], rubric["role_id"], rubric["version"],
        "approved by " + rubric["approved_by"] if rubric["status"] == "approved"
        else "DRAFT, must be approved by the hiring team before scoring")]
    lines.append("{} competencies ({} must-have), each with 1–4 behavioural anchors.".format(
        len(rubric["competencies"]), sum(1 for c in rubric["competencies"] if c["must_have"])))
    if rubric["stages"]:
        for s in rubric["stages"]:
            lines.append("- {}: {} ({} planned question(s))".format(
                s["stage_name"], ", ".join(rubric["by_id"][c]["name"] for c in s["competencies"]), len(s["questions"])))
        planned = set()
        for s in rubric["stages"]:
            planned.update(s["competencies"])
        unplanned = [c["name"] for c in rubric["competencies"] if c["id"] not in planned]
        if unplanned:
            lines.append("{} Not owned by any stage: {}.".format(WARN, ", ".join(unplanned)))
    else:
        lines.append("No interview plan: unasked-question checks are off.")
    return "\n".join(lines) + "\n"


def parse_as_of(value):
    parsed = parse_day(value)
    if parsed is None:
        raise InputError("--as-of: {} must be a YYYY-MM-DD date".format(shown(value)))
    return parsed


def build(args, config, rubric):
    """Validate, analyse and render; returns the text to print. Log writes happen here, before printing."""
    as_of = parse_as_of(args.as_of)
    if not args.bundle:
        raise InputError("--bundle is required")
    if args.scheduled and not args.log:
        raise InputError("--scheduled needs --log so the same interview is never drafted twice")
    if args.resume_pending and not args.scheduled:
        raise InputError("--resume-pending only applies to --scheduled runs")
    if args.ats_draft is not None and args.ats_draft not in config["ats_providers"]:
        raise InputError("--ats-draft must be one of {}".format(", ".join(sorted(config["ats_providers"]))))
    b = load_bundle(args.bundle, config, rubric)
    interviews, dedupe_notes = dedupe_interviews(b["interviews"])
    if b["mode"] == "single" and len(interviews) != 1:
        raise InputError("bundle {}: $.interviews: single mode takes exactly one interview, got {}; use mode "
                         "panel to combine interviews".format(args.bundle, len(interviews)))
    if args.ats_draft and b["mode"] != "single":
        raise InputError("--ats-draft works in single mode only: each interviewer files their own scorecard")
    if args.ats_draft and interviews[0]["recording_disclosed"] is None:
        raise InputError("--ats-draft: recording disclosure is not confirmed for this interview (recording_disclosed "
                         "is null). Confirm with the interviewer whether the candidate was told, then rerun")
    if args.hiring_manager_preview and b["mode"] != "panel":
        raise InputError("--hiring-manager-preview applies to panel debriefs only")
    analyses = [analyse_interview(iv, rubric, config, b["scanner"]) for iv in interviews]
    key = run_key(b, analyses)
    writes = args.scheduled and not args.dry_run
    if args.log:
        check_log_path(args.log, write=writes)
    lock = acquire_lock(args.log) if writes else None
    try:
        logged = read_log(args.log) if args.log else {}
        entry = logged.get(key)
        if args.scheduled and entry and entry["status"] == "delivered":
            return "Skipped: already delivered {} (drafted {}).\n".format(key, entry["row"][5])
        if args.scheduled and entry and entry["status"] == "pending" and not args.resume_pending:
            raise PendingError("log {}: {} was drafted on {} but its delivery was never confirmed. If the interviewer "
                               "received it, run --confirm-delivered {}; if not, rerun with --resume-pending to draft "
                               "it again".format(args.log, key, entry["row"][5], key))
        gone = [a["iv"]["interviewer"]["name"] for a in analyses if a["iv"]["notes_status"] != "ok"]
        if args.scheduled and gone:
            logs = writes and (entry is None or entry["status"] == "skipped")
            if logs and entry is None:
                logged[key] = {"row": [key, b["mode"], b["role_id"], b["candidate"]["ref"], str(len(analyses)),
                                       as_of.isoformat(), "skipped"], "status": "skipped"}
                write_log(args.log, logged)
            return ("Skipped: notes not available for {}'s interview ({}); nothing drafted. {}\n".format(
                ", ".join(gone), key, "Logged as skipped: --next-window lists it for a retry." if logs else
                "Nothing logged."))
        rows = None
        show_scores = True
        if b["mode"] == "single":
            markdown = render_single(b, analyses[0], rubric, config, as_of, args.ats_draft)
        else:
            rows = panel_analysis(analyses, rubric, config)
            ready = panel_status(analyses)[0]
            show_scores = ready or args.hiring_manager_preview
            markdown = render_panel(b, analyses, rows, rubric, config, as_of, dedupe_notes,
                                    preview=args.hiring_manager_preview)
        if args.json:
            output = json.dumps(json_ready(b, analyses, rows, rubric, markdown, key, show_scores), indent=2,
                                ensure_ascii=False, sort_keys=True) + "\n"
        else:
            output = markdown
        shareable = b["mode"] == "single" or panel_status(analyses)[0]
        if writes and (entry is None or entry["status"] == "skipped") and shareable:
            logged[key] = {"row": [key, b["mode"], b["role_id"], b["candidate"]["ref"], str(len(analyses)),
                                   as_of.isoformat(), "pending"], "status": "pending"}
            write_log(args.log, logged)
            args.staged_key = key
        return output
    finally:
        release_lock(lock)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Draft an interview scorecard or panel debrief from a rubric and bundle.")
    ap.add_argument("--rubric", help="the role's approved rubric (JSON)")
    ap.add_argument("--bundle", help="the evidence bundle (JSON); see DATA_CONTRACT.md")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--as-of", help="run date, YYYY-MM-DD (the only 'today' the script uses)")
    ap.add_argument("--check-rubric", action="store_true", help="validate the rubric only and summarise it")
    ap.add_argument("--ats-draft", help="also render an ATS draft block: greenhouse, ashby or lever (single mode)")
    ap.add_argument("--log", help="scorecard log (markdown); scheduled runs record each key as pending")
    ap.add_argument("--scheduled", action="store_true", help="skip keys delivered in --log; record new ones pending")
    ap.add_argument("--resume-pending", action="store_true", help="scheduled: draft a pending key again")
    ap.add_argument("--confirm-delivered", metavar="KEY", help="mark a pending key in --log as delivered, then exit")
    ap.add_argument("--next-window", action="store_true",
                    help="scheduled: print the dates to list (since the last --mark-listed) and the keys to re-run")
    ap.add_argument("--mark-listed", action="store_true",
                    help="scheduled: record that every interview up to --as-of was listed and run")
    ap.add_argument("--hiring-manager-preview", action="store_true",
                    help="panel: show scores before everyone has filed, for the hiring manager only")
    ap.add_argument("--dry-run", action="store_true", help="never write --log")
    ap.add_argument("--json", action="store_true", help="emit structured JSON instead of markdown")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.confirm_delivered is not None:
            if not args.log:
                raise InputError("--confirm-delivered needs --log")
            output = confirm_delivered(args.log, args.confirm_delivered)
        elif args.next_window or args.mark_listed:
            flag = "--next-window" if args.next_window else "--mark-listed"
            if args.next_window and args.mark_listed:
                raise InputError("use --next-window or --mark-listed, not both")
            if not args.log:
                raise InputError("{} needs --log".format(flag))
            as_of = parse_as_of(args.as_of)
            output = next_window(args.log, as_of, args.json) if args.next_window else mark_listed(args.log, as_of)
        else:
            config = load_config(args.config)
            rubric = load_rubric(args.rubric, config)
            if args.check_rubric:
                output = rubric_summary(rubric, args.rubric)
            else:
                require_approved(rubric, args.rubric)
                output = build(args, config, rubric)
    except RubricError as exc:
        say_error(exc)
        return EXIT_RUBRIC
    except InputError as exc:
        say_error(exc)
        return EXIT_INPUT
    except PendingError as exc:
        say_error(exc)
        return EXIT_PENDING
    except LockedError as exc:
        say_error(exc)
        return EXIT_LOCKED
    return emit(output, getattr(args, "staged_key", None) is not None)


def emit(text, staged):
    """Print the result. If stdout is gone (closed, broken pipe, full disk), say so on stderr and exit 6 without a
    traceback; a scheduled run's key stays pending, so the next run stops on it (exit 4) instead of losing it."""
    try:
        if sys.stdout is None:
            raise OSError("stdout is closed")
        sys.stdout.write(text)
        sys.stdout.flush()
        return 0
    except OSError as exc:
        try:  # send whatever is still buffered to /dev/null, so the flush at exit can't fail again
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, AttributeError, ValueError):
            pass
        hint = (" The key is recorded as pending and nothing was delivered: rerun with --resume-pending to print it "
                "again." if staged else "")
        say_error("the output could not be written ({}).{}".format(exc.strerror or exc, hint))
        return EXIT_OUTPUT


def say_error(message):
    """'error: …' on stderr; a closed stderr is not worth a traceback."""
    try:
        if sys.stderr is not None:
            sys.stderr.write("error: {}\n".format(message))
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    sys.exit(main())
