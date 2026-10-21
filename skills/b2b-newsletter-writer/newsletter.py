#!/usr/bin/env python3
"""Deterministic tooling for the b2b-newsletter-writer skill.

Stdlib only, offline, deterministic. Never reads the wall clock and never sends
anything: it lints, renders and validates a newsletter draft the agent wrote.

Subcommands:
  lint         Check draft.json against config + rules (length, sections, subject
               lines, opener, banned phrases, sources, unverified figures/quotes, CTA).
  render       Render draft.json as email-safe HTML, review markdown, or plain text.
  check-html   Validate an HTML file for email safety (tables, inline styles only,
               no div/style/link/script, no flex/grid, width <= 600px, size limit).
  voice-stats  Measure voice signals in past issues (markdown files) to ground the
               voice profile: sentence length, person, contractions, structures, sign-off.

Exit codes: 0 no FAIL, 1 at least one FAIL, 2 bad input (message names file and field).
"""

from __future__ import annotations

import argparse
import html
import itertools
import json
import re
import statistics
import sys
import textwrap
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RULES = SCRIPT_DIR / "rules.json"

CTA_TYPES = ("product", "content", "event", "reply")
FORMATS = ("html", "markdown")
SEVERITY_ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Typographic characters, built with chr() so this file stays pure ASCII and
# survives copy/paste or transfer tools that mangle non-ASCII text or escapes.
NUL = chr(0)            # placeholder used while splitting sentences
SECT = chr(0xA7)        # section sign
MIDDOT = chr(0xB7)      # middle dot separator
EM_DASH = chr(0x2014)   # em dash
RARROW = chr(0x2192)    # rightwards arrow
LSQUO = chr(0x2018)     # left single quote
RSQUO = chr(0x2019)     # right single quote / curly apostrophe
LDQUO = chr(0x201C)     # left double quote
RDQUO = chr(0x201D)     # right double quote


class InputError(Exception):
    """Bad input: reported on stderr with exit code 2."""


# --------------------------------------------------------------------------- loading


def load_json(path: str) -> object:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"{path}: file not found")
    except UnicodeDecodeError:
        raise InputError(f"{path}: not valid UTF-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")


def load_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"{path}: file not found")
    except UnicodeDecodeError:
        raise InputError(f"{path}: not valid UTF-8")


def _expect(cond: bool, path: str, field: str, msg: str) -> None:
    if not cond:
        raise InputError(f"{path}: {field}: {msg}")


def _str(obj: dict, key: str, path: str, where: str, required: bool = True, nonempty: bool = False) -> str:
    field = f"{where}{key}"
    if key not in obj:
        _expect(not required, path, field, "missing")
        return ""
    val = obj[key]
    _expect(isinstance(val, str), path, field, f"must be a string, got {type(val).__name__}")
    if nonempty:
        _expect(val.strip() != "", path, field, "must not be empty")
    return val


def _date(val: str, path: str, field: str) -> date:
    try:
        return date.fromisoformat(val)
    except ValueError:
        raise InputError(f"{path}: {field}: '{val}' is not a YYYY-MM-DD date")


def load_rules(path: str) -> dict:
    rules = load_json(path)
    _expect(isinstance(rules, dict), path, "(root)", "must be a JSON object")
    required = [
        "section_words_min", "section_words_max", "opener_sentences_min", "opener_sentences_max",
        "subject_max_chars", "preheader_max_chars", "headline_max_chars", "cta_max_words",
        "quote_min_words", "verbatim_ngram_words", "ignore_years", "voice_issue_count", "acronyms",
        "banned_phrases", "banned_openers", "spam_trigger_words", "false_urgency_words",
        "clickbait_patterns", "fake_reply_prefixes", "attribution_cues", "stance_markers",
        "placeholder_patterns", "unverified_marker", "generic_button_labels",
        "sentence_abbreviations", "render", "html",
    ]
    for key in required:
        _expect(key in rules, path, key, "missing")
    for pat in rules["placeholder_patterns"]:
        try:
            re.compile(pat)
        except re.error as exc:
            raise InputError(f"{path}: placeholder_patterns: invalid regex {pat!r}: {exc}")
    return rules


def load_config(path: str) -> dict:
    cfg = load_json(path)
    _expect(isinstance(cfg, dict), path, "(root)", "must be a JSON object")
    for key in ("sections", "max_word_count", "subject_line_count"):
        _expect(key in cfg, path, key, "missing")
        val = cfg[key]
        _expect(isinstance(val, int) and not isinstance(val, bool), path, key, f"must be an integer, got {val!r}")
    _expect(1 <= cfg["sections"] <= 10, path, "sections", f"must be between 1 and 10, got {cfg['sections']}")
    _expect(cfg["max_word_count"] >= 50, path, "max_word_count", f"must be at least 50, got {cfg['max_word_count']}")
    _expect(1 <= cfg["subject_line_count"] <= 10, path, "subject_line_count",
            f"must be between 1 and 10, got {cfg['subject_line_count']}")
    cta = _str(cfg, "cta_type", path, "")
    _expect(cta.strip() != "", path, "cta_type",
            "is blank. Set it to product, content, event or reply (Step 0 asks the user)")
    _expect(cta in CTA_TYPES, path, "cta_type", f"'{cta}' is not one of {', '.join(CTA_TYPES)}")
    fmt = cfg.get("format", "html")
    _expect(fmt in FORMATS, path, "format", f"'{fmt}' is not one of {', '.join(FORMATS)}")
    for key in ("newsletter_name", "from_name", "audience", "voice_file", "past_issues"):
        if key in cfg:
            _str(cfg, key, path, "")
    if "allow_emoji" in cfg:
        _expect(isinstance(cfg["allow_emoji"], bool), path, "allow_emoji", "must be true or false")
    for key in ("extra_banned_phrases", "extra_acronyms"):
        val = cfg.get(key, [])
        _expect(isinstance(val, list) and all(isinstance(v, str) for v in val), path, key,
                "must be a list of strings")
    brand = cfg.get("brand", {})
    _expect(isinstance(brand, dict), path, "brand", "must be an object")
    for k, v in brand.items():
        _expect(isinstance(v, str), path, f"brand.{k}", "must be a string")
        if k.endswith("_color"):
            _expect(re.fullmatch(r"#[0-9a-fA-F]{6}", v) is not None, path, f"brand.{k}",
                    f"'{v}' is not a #RRGGBB hex colour")
    return cfg


def load_draft(path: str) -> dict:
    d = load_json(path)
    _expect(isinstance(d, dict), path, "(root)", "must be a JSON object")
    issue = _str(d, "issue_date", path, "", nonempty=True)
    d["_issue_date"] = _date(issue, path, "issue_date")
    _expect("subject_lines" in d, path, "subject_lines", "missing")
    subs = d["subject_lines"]
    _expect(isinstance(subs, list) and len(subs) >= 1, path, "subject_lines", "must be a non-empty list")
    for i, s in enumerate(subs, 1):
        _expect(isinstance(s, str) and s.strip() != "", path, f"subject_lines[{i}]", "must be a non-empty string")
    d["preheader"] = _str(d, "preheader", path, "", required=False)
    _str(d, "opener", path, "", nonempty=True)
    _expect("sections" in d, path, "sections", "missing")
    secs = d["sections"]
    _expect(isinstance(secs, list) and len(secs) >= 1, path, "sections", "must be a non-empty list")
    for i, s in enumerate(secs, 1):
        where = f"sections[{i}]."
        _expect(isinstance(s, dict), path, f"sections[{i}]", "must be an object")
        _str(s, "headline", path, where, nonempty=True)
        _str(s, "body", path, where, nonempty=True)
        s["source_url"] = _str(s, "source_url", path, where, required=False).strip()
        s["source_title"] = _str(s, "source_title", path, where, required=False).strip()
    _expect("cta" in d, path, "cta", "missing")
    cta = d["cta"]
    _expect(isinstance(cta, dict), path, "cta", "must be an object")
    _str(cta, "type", path, "cta.", nonempty=True)
    _str(cta, "text", path, "cta.", nonempty=True)
    cta["url"] = _str(cta, "url", path, "cta.", required=False).strip()
    cta["button_label"] = _str(cta, "button_label", path, "cta.", required=False).strip()
    dl = _str(cta, "deadline", path, "cta.", required=False).strip()
    cta["deadline"] = dl
    if dl:
        _date(dl, path, "cta.deadline")
    d["sign_off"] = _str(d, "sign_off", path, "", required=False)
    facts = d.get("facts", [])
    _expect(isinstance(facts, list), path, "facts", "must be a list")
    for i, f in enumerate(facts, 1):
        _expect(isinstance(f, dict), path, f"facts[{i}]", "must be an object")
        _str(f, "quote", path, f"facts[{i}].", nonempty=True)
        _str(f, "source", path, f"facts[{i}].", nonempty=True)
    d["facts"] = facts
    btc = d.get("brief_topic_count")
    if btc is not None:
        _expect(isinstance(btc, int) and not isinstance(btc, bool) and btc >= 1, path, "brief_topic_count",
                "must be a positive integer")
    return d


def merged_brand(rules: dict, cfg: dict | None) -> dict:
    brand = dict(rules["render"]["brand"])
    if cfg:
        brand.update(cfg.get("brand", {}))
    return brand


# --------------------------------------------------------------------------- text helpers

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
URL_RE = re.compile(r"https?://\S+")


def norm_apos(text: str) -> str:
    return (text.replace(RSQUO, "'").replace(LSQUO, "'")
            .replace(LDQUO, '"').replace(RDQUO, '"'))


def plain(text: str) -> str:
    """Markdown-light to plain text: drop link targets and emphasis markers."""
    text = LINK_RE.sub(lambda m: m.group(1), text)
    text = URL_RE.sub(" ", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?<!\w)\*(?!\s)([^*]+)(?<!\s)\*(?!\w)", r"\1", text)
    return text


def count_words(text: str) -> int:
    return sum(1 for tok in plain(text).split() if re.search(r"[A-Za-z0-9]", tok))


def split_sentences(text: str, abbreviations: list[str]) -> list[str]:
    t = " ".join(plain(text).split())
    for abbr in sorted(abbreviations, key=len, reverse=True):
        t = re.sub(r"(?<![A-Za-z])" + re.escape(abbr), lambda m: m.group(0).replace(".", NUL), t,
                   flags=re.IGNORECASE)
    parts = re.split(r"(?<=[.!?])\s+|(?<=[.!?][\"" + RDQUO + RSQUO + r"')])\s+", t)
    return [p.replace(NUL, ".").strip() for p in parts if re.search(r"[A-Za-z0-9]", p)]


def phrase_regex(phrase: str) -> re.Pattern:
    p = norm_apos(phrase.lower()).strip()
    prefix = p.endswith("*")
    core = p.rstrip("*")
    pattern = r"\s+".join(re.escape(part) for part in core.split())
    start = r"(?<![a-z0-9])" if core[:1].isalnum() else ""
    end = r"[a-z0-9'-]*" if prefix else (r"(?![a-z0-9])" if core[-1:].isalnum() else "")
    return re.compile(start + pattern + end)


def find_phrases(text: str, phrases: list[str]) -> list[str]:
    """Matched phrases in list order; a match overlapping an earlier one is dropped."""
    low = norm_apos(plain(text).lower())
    hits: list[str] = []
    spans: list[tuple[int, int]] = []
    for ph in phrases:
        m = phrase_regex(ph).search(low)
        if m and not any(m.start() < e and s < m.end() for s, e in spans):
            spans.append(m.span())
            hits.append(m.group(0))
    return hits


NUM_RE = re.compile(
    r"(?<![A-Za-z0-9.])(\$?)(\d(?:[\d,]*\d)?(?:\.\d+)?(?::\d\d)?)(%|x(?![a-z])|k(?![a-z])|m(?![a-z])|bn(?![a-z]))?",
    re.IGNORECASE,
)


def canon_number(raw: str) -> str:
    if ":" in raw:
        return raw
    try:
        val = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return raw
    s = format(val.normalize(), "f")
    return s


def extract_numbers(text: str) -> list[tuple[str, str]]:
    """Return (display_token, canonical_value) for every figure in the text."""
    out = []
    for m in NUM_RE.finditer(plain(text)):
        token = m.group(0)
        out.append((token, canon_number(m.group(2))))
    return out


def is_year(token: str, canon: str) -> bool:
    return bool(re.fullmatch(r"(19|20)\d\d", token)) and canon == token


def norm_quote(text: str) -> str:
    t = norm_apos(plain(text)).lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip(" .,;:!?\"'")


QUOTE_RE = re.compile(LDQUO + "([^" + RDQUO + "]+)" + RDQUO + r"|\"([^\"]+)\"")


def find_quotes(text: str) -> list[str]:
    return [(m.group(1) or m.group(2)) for m in QUOTE_RE.finditer(plain(text))]


def word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", norm_apos(plain(text)).lower())


EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0xFE0F, 0xFE0F), (0x200D, 0x200D),
                (0x1F1E6, 0x1F1FF)]


def has_emoji(text: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in EMOJI_RANGES) for ch in text)


def caps_words(text: str, acronyms: set[str]) -> list[str]:
    out = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9'" + RSQUO + "]*", text):
        letters = re.sub(r"[^A-Za-z]", "", tok)
        if len(letters) >= 2 and letters.isupper() and tok not in acronyms:
            out.append(tok)
    return out


def is_http_url(url: str) -> bool:
    return re.fullmatch(r"https?://[^\s/$.?#][^\s]*", url) is not None


def fmt_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def short(text: str, n: int = 48) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 3].rstrip() + "..."


def md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------- lint

LINT_CHECKS = [
    ("config", "settings"),
    ("subject_count", "subject line count"),
    ("subject_lines", "subject lines"),
    ("preheader", "preheader"),
    ("opener", "opener"),
    ("section_count", "section count"),
    ("section_length", "section length"),
    ("headlines", "headlines"),
    ("source_links", "source links"),
    ("commentary", "commentary has a take"),
    ("verbatim", "no verbatim lifting"),
    ("banned_phrases", "banned phrases"),
    ("placeholders", "placeholders"),
    ("figures", "figures verified"),
    ("quotes", "quotes verified"),
    ("fact_sources", "fact sources"),
    ("cta", "CTA"),
    ("sign_off", "sign-off"),
    ("word_budget", "word budget"),
]


def text_fields(d: dict) -> list[tuple[str, str]]:
    fields = [(f"subject {i}", s) for i, s in enumerate(d["subject_lines"], 1)]
    fields.append(("preheader", d["preheader"]))
    fields.append(("opener", d["opener"]))
    for i, s in enumerate(d["sections"], 1):
        fields.append((f"{SECT}{i} headline", s["headline"]))
        fields.append((f"{SECT}{i} body", s["body"]))
    fields.append(("cta.text", d["cta"]["text"]))
    fields.append(("cta.button_label", d["cta"]["button_label"]))
    fields.append(("sign_off", d["sign_off"]))
    return [(w, t) for w, t in fields if t.strip()]


def word_budget(d: dict) -> dict:
    opener = count_words(d["opener"])
    secs = [count_words(s["headline"]) + count_words(s["body"]) for s in d["sections"]]
    bodies = [count_words(s["body"]) for s in d["sections"]]
    cta = count_words(d["cta"]["text"])
    sign = count_words(d["sign_off"])
    return {"opener": opener, "sections": secs, "section_bodies": bodies, "cta": cta, "sign_off": sign,
            "total": opener + sum(secs) + cta + sign}


def cut_plan(section_words: list[int], total: int, limit: int) -> dict | None:
    """Fewest sections first, then least words lost, then prefer later sections."""
    n = len(section_words)
    for k in range(1, n):
        cands = []
        for combo in itertools.combinations(range(n), k):
            removed = sum(section_words[i] for i in combo)
            if total - removed <= limit:
                cands.append((removed, tuple(-i for i in sorted(combo, reverse=True)), combo))
        if cands:
            removed, _, combo = min(cands)
            return {"sections": [i + 1 for i in combo], "removed": removed, "new_total": total - removed}
    return None


def lightest_cut(section_words: list[int], total: int) -> dict | None:
    if len(section_words) < 2:
        return None
    idx = min(range(len(section_words)), key=lambda i: (section_words[i], -i))
    return {"sections": [idx + 1], "removed": section_words[idx], "new_total": total - section_words[idx]}


def lint(d: dict, cfg: dict, rules: dict, brief: str) -> dict:
    findings: list[dict] = []
    pass_notes: dict[str, str] = {}

    def add(sev: str, check: str, where: str, detail: str) -> None:
        findings.append({"severity": sev, "check": check, "where": where, "detail": detail})

    acronyms = set(rules["acronyms"]) | set(cfg.get("extra_acronyms", []))
    allow_emoji = cfg.get("allow_emoji", False)
    has_deadline = bool(d["cta"]["deadline"])
    marker = rules["unverified_marker"].lower()

    # config
    if not cfg.get("from_name", "").strip():
        add("WARN", "config", "config.from_name", "blank; the render has no sender line")
    pass_notes["config"] = f"cta_type {cfg['cta_type']}, {cfg['sections']} sections, {cfg['max_word_count']} words max"

    # subject lines
    subs = d["subject_lines"]
    want = cfg["subject_line_count"]
    if len(subs) != want:
        add("FAIL", "subject_count", "subject_lines", f"{len(subs)} options; config asks for exactly {want}")
    pass_notes["subject_count"] = f"{len(subs)} of {want}"
    seen: dict[str, int] = {}
    for i, s in enumerate(subs, 1):
        w = f"subject {i}"
        n = len(s)
        if n > rules["subject_max_chars"]:
            add("FAIL", "subject_lines", w, f"{n} chars; must be under {rules['subject_max_chars'] + 1}: \"{s}\"")
        prefix_tokens = {pre.rstrip(":").upper() for pre in rules["fake_reply_prefixes"]}
        caps = [c for c in caps_words(s, acronyms) if not (c.upper() in prefix_tokens and
                                                             s.strip().upper().startswith(c.upper() + ":"))]
        if caps:
            add("FAIL", "subject_lines", w, f"ALL-CAPS word(s) {', '.join(caps)} (not in the acronym list)")
        if has_emoji(s) and not allow_emoji:
            add("FAIL", "subject_lines", w, "contains emoji; allow_emoji is false")
        spam = find_phrases(s, rules["spam_trigger_words"])
        for hit in spam:
            add("FAIL", "subject_lines", w, f"spam-trigger word \"{hit}\"")
        for hit in find_phrases(s, rules["false_urgency_words"]):
            if hit in spam:
                continue
            if has_deadline:
                add("WARN", "subject_lines", w, f"urgency word \"{hit}\"; confirm cta.deadline {d['cta']['deadline']} is real")
            else:
                add("FAIL", "subject_lines", w, f"false urgency \"{hit}\" with no cta.deadline in the brief")
        low = s.strip().lower()
        for pre in rules["fake_reply_prefixes"]:
            if low.startswith(pre):
                add("FAIL", "subject_lines", w, f"fake reply/forward prefix \"{s.strip()[:len(pre)]}\" is misleading")
        for hit in find_phrases(s, rules["clickbait_patterns"]):
            add("WARN", "subject_lines", w, f"clickbait pattern \"{hit}\"")
        if "!" in s:
            add("WARN", "subject_lines", w, "exclamation mark reads as promotional")
        key = " ".join(low.split())
        if key in seen:
            add("FAIL", "subject_lines", w, f"duplicate of subject {seen[key]}")
        else:
            seen[key] = i
    pass_notes["subject_lines"] = (f"all under {rules['subject_max_chars'] + 1} chars, no caps/emoji/spam/urgency "
                                   f"(longest {max(len(s) for s in subs)})")

    # preheader
    ph = d["preheader"].strip()
    if not ph:
        add("WARN", "preheader", "preheader", "missing; inbox preview will show the first body line")
    else:
        if len(ph) > rules["preheader_max_chars"]:
            add("WARN", "preheader", "preheader", f"{len(ph)} chars; most clients show about {rules['preheader_max_chars']}")
        if ph.lower() in seen:
            add("WARN", "preheader", "preheader", "repeats a subject line; use it to add information")
        spam = find_phrases(ph, rules["spam_trigger_words"])
        for hit in spam:
            add("FAIL", "preheader", "preheader", f"spam-trigger word \"{hit}\"")
        for hit in find_phrases(ph, rules["false_urgency_words"]):
            if hit in spam:
                continue
            add("WARN" if has_deadline else "FAIL", "preheader", "preheader", f"urgency word \"{hit}\"")
    pass_notes["preheader"] = f"{len(ph)} chars"

    # opener
    sents = split_sentences(d["opener"], rules["sentence_abbreviations"])
    lo, hi = rules["opener_sentences_min"], rules["opener_sentences_max"]
    if not lo <= len(sents) <= hi:
        add("FAIL", "opener", "opener", f"{len(sents)} sentences; must be {lo}-{hi}")
    op_low = norm_apos(plain(d["opener"]).strip().lower())
    for bad in rules["banned_openers"]:
        if op_low.startswith(norm_apos(bad.lower())):
            add("FAIL", "opener", "opener", f"starts with \"{bad}\"; open with the issue's point instead")
            break
    pass_notes["opener"] = f"{len(sents)} sentences, no greeting opener"

    # sections
    secs = d["sections"]
    budget = word_budget(d)
    want_secs = cfg["sections"]
    btc = d.get("brief_topic_count")
    if len(secs) > want_secs:
        add("FAIL", "section_count", "sections", f"{len(secs)} sections; config allows {want_secs}")
    if btc is not None and len(secs) > btc:
        add("FAIL", "section_count", "sections",
            f"padding: {len(secs)} sections for {btc} brief topics; cut to {btc} (one section per brief topic)")
    elif len(secs) < want_secs:
        if btc is not None and btc == len(secs):
            pass_notes["section_count"] = (f"{len(secs)} of {want_secs}: reduced to match {btc} brief topic(s), "
                                           f"no padding")
        elif btc is not None:
            add("WARN", "section_count", "sections",
                f"{len(secs)} sections for {btc} brief topics; cover each topic or say which ones were merged/dropped")
        else:
            add("WARN", "section_count", "sections",
                f"{len(secs)} of {want_secs} and no brief_topic_count; add \"brief_topic_count\": <number of "
                f"topics in the brief> to draft.json (e.g. \"brief_topic_count\": {len(secs)} if the brief had "
                f"{len(secs)} topics)")
    pass_notes.setdefault("section_count", f"{len(secs)} of {want_secs}")

    smin, smax = rules["section_words_min"], rules["section_words_max"]
    all_links = set()
    for s in secs:
        if s["source_url"]:
            all_links.add(s["source_url"])
        for m in LINK_RE.finditer(s["body"]):
            all_links.add(m.group(2))
    if d["cta"]["url"]:
        all_links.add(d["cta"]["url"])
    for m in LINK_RE.finditer(d["opener"]):
        all_links.add(m.group(2))

    fact_quotes = [f["quote"] for f in d["facts"]]
    fact_text_norm = [norm_quote(q) for q in fact_quotes]
    brief_norm = norm_quote(brief) if brief else ""
    ngram = rules["verbatim_ngram_words"]
    fact_shingles = []
    for q in fact_quotes:
        toks = word_tokens(q)
        fact_shingles.append({tuple(toks[j:j + ngram]) for j in range(0, max(0, len(toks) - ngram + 1))})

    for i, s in enumerate(secs, 1):
        w = f"{SECT}{i}"
        bw = budget["section_bodies"][i - 1]
        if bw < smin or bw > smax:
            add("FAIL", "section_length", w, f"body is {bw} words; target {smin}-{smax}")
        hl = s["headline"].strip()
        if len(hl) > rules["headline_max_chars"]:
            add("WARN", "headlines", w, f"{len(hl)} chars; keep headlines scannable (<= {rules['headline_max_chars']})")
        for hit in find_phrases(hl, rules["clickbait_patterns"]):
            add("WARN", "headlines", w, f"clickbait pattern \"{hit}\"; describe the point instead")
        if hl.endswith("!"):
            add("WARN", "headlines", w, "ends with '!'")
        hc = caps_words(hl, acronyms)
        if hc:
            add("WARN", "headlines", w, f"ALL-CAPS word(s) {', '.join(hc)}")
        cues = find_phrases(s["body"], rules["attribution_cues"])
        references_source = bool(cues) or bool(find_quotes(s["body"])) or bool(
            [n for n in extract_numbers(s["body"]) if not (rules["ignore_years"] and is_year(*n))])
        if s["source_url"]:
            if not is_http_url(s["source_url"]):
                add("FAIL", "source_links", w, f"source_url \"{s['source_url']}\" is not an http(s) URL")
            if not s["source_title"]:
                add("WARN", "source_links", w, "source_title missing; link text falls back to the domain")
        elif references_source:
            why = f"\"{cues[0]}\"" if cues else "a figure or quote"
            add("FAIL", "source_links", w, f"body references a source ({why}) but has no source_url")
        if not find_phrases(s["body"], rules["stance_markers"]):
            add("WARN", "commentary", w, "no stance marker found; this may read as a summary, not a take")
        outside = QUOTE_RE.sub(" ", plain(s["body"]))
        toks = word_tokens(outside)
        body_sh = [tuple(toks[j:j + ngram]) for j in range(0, max(0, len(toks) - ngram + 1))]
        for fi, sh in enumerate(fact_shingles, 1):
            hit = next((g for g in body_sh if g in sh), None)
            if hit:
                add("WARN", "verbatim", w, f"{ngram}+ words copied from facts[{fi}] without quote marks: \"{' '.join(hit)}\"")
                break
    pass_notes["section_length"] = f"all bodies {smin}-{smax} words"
    pass_notes["headlines"] = "descriptive, no clickbait"
    pass_notes["source_links"] = "every section that cites a source links it"
    pass_notes["commentary"] = "every section states a position"
    pass_notes["verbatim"] = f"no {ngram}-word run copied from a fact quote"

    # banned phrases / placeholders anywhere
    banned = list(rules["banned_phrases"]) + list(cfg.get("extra_banned_phrases", []))
    ph_res = [re.compile(p, re.IGNORECASE) for p in rules["placeholder_patterns"]]
    for where, text in text_fields(d):
        for hit in find_phrases(text, banned):
            add("FAIL", "banned_phrases", where, f"\"{hit}\"")
        for rx in ph_res:
            m = rx.search(text)
            if m:
                add("FAIL", "placeholders", where, f"unfinished placeholder \"{m.group(0)}\"")
    for i, s in enumerate(secs, 1):
        for rx in ph_res:
            m = rx.search(s["source_url"])
            if m:
                add("FAIL", "placeholders", f"{SECT}{i} source_url", f"unfinished placeholder \"{m.group(0)}\"")
    pass_notes["banned_phrases"] = f"none of {len(banned)} banned phrases used"
    pass_notes["placeholders"] = "none"

    # figures and quotes
    verified_nums = set()
    for q in fact_quotes:
        verified_nums.update(c for _, c in extract_numbers(q))
    if brief:
        verified_nums.update(c for _, c in extract_numbers(brief))
    checked = 0
    for where, text in text_fields(d):
        sentences = split_sentences(text, rules["sentence_abbreviations"]) if where != "cta.button_label" else [text]
        for sent in sentences:
            marked = marker in sent.lower()
            for token, canon in extract_numbers(sent):
                if rules["ignore_years"] and is_year(token, canon):
                    continue
                checked += 1
                if canon in verified_nums:
                    continue
                if marked:
                    add("WARN", "figures", where, f"\"{token}\" marked {rules['unverified_marker']}; verify or remove before sending")
                else:
                    add("FAIL", "figures", where, f"unverified figure \"{token}\": not in facts[] or the brief")
            for q in find_quotes(sent):
                if len(word_tokens(q)) < rules["quote_min_words"]:
                    continue
                nq = norm_quote(q)
                if any(nq in f for f in fact_text_norm) or (brief_norm and nq in brief_norm):
                    continue
                sev = "WARN" if marked else "FAIL"
                add(sev, "quotes", where, f"unverified quote \"{short(q, 60)}\": not verbatim in facts[] or the brief")
    pass_notes["figures"] = f"{checked} figure(s) all traced to facts[] or the brief"
    pass_notes["quotes"] = "every direct quote is verbatim in facts[] or the brief"

    for i, f in enumerate(d["facts"], 1):
        src = f["source"].strip()
        if src.lower() == "brief":
            continue
        if not is_http_url(src):
            add("FAIL", "fact_sources", f"facts[{i}]", f"source \"{src}\" must be an http(s) URL or \"brief\"")
        elif src not in all_links:
            add("WARN", "fact_sources", f"facts[{i}]", f"{src} is not linked anywhere in the newsletter; attribute it")
    pass_notes["fact_sources"] = f"{len(d['facts'])} fact(s), all sources linked"

    # CTA
    cta = d["cta"]
    if cta["type"] != cfg["cta_type"]:
        add("FAIL", "cta", "cta.type", f"\"{cta['type']}\" but config cta_type is \"{cfg['cta_type']}\"")
    ctype = cfg["cta_type"]
    if ctype == "reply":
        if "?" not in cta["text"]:
            add("FAIL", "cta", "cta.text", "reply CTA must ask the reader a genuine question")
        if cta["url"]:
            add("WARN", "cta", "cta.url", "reply CTA should not link away; the action is hitting reply")
    else:
        if not cta["url"]:
            add("FAIL", "cta", "cta.url", f"{ctype} CTA needs a URL")
        elif not is_http_url(cta["url"]):
            add("FAIL", "cta", "cta.url", f"\"{cta['url']}\" is not an http(s) URL")
        label = cta["button_label"].strip().lower().rstrip(" .!" + RARROW + ">")
        if label in rules["generic_button_labels"]:
            add("WARN", "cta", "cta.button_label", f"\"{cta['button_label']}\" is generic; name the action")
        if ctype == "event" and not re.search(
                r"\d|monday|tuesday|wednesday|thursday|friday|saturday|sunday|" + "|".join(m.lower() for m in MONTHS),
                cta["text"].lower()):
            add("WARN", "cta", "cta.text", "event CTA should state the date")
    cw = count_words(cta["text"])
    if cw > rules["cta_max_words"]:
        add("WARN", "cta", "cta.text", f"{cw} words; keep the CTA under {rules['cta_max_words']}")
    pass_notes["cta"] = f"{ctype} CTA with " + ("a question" if ctype == "reply" else "a link")

    if not d["sign_off"].strip():
        add("WARN", "sign_off", "sign_off", "missing; use the sign-off from the voice profile")
    pass_notes["sign_off"] = f"\"{short(d['sign_off'], 30)}\""

    # word budget
    limit = cfg["max_word_count"]
    total = budget["total"]
    plan = None
    if total > limit:
        plan = cut_plan(budget["sections"], total, limit)
        if plan:
            secs_txt = ", ".join(f"{SECT}{n}" for n in plan["sections"])
            add("FAIL", "word_budget", "body",
                f"{total} words; limit {limit}. Cut {secs_txt} (-{plan['removed']} words -> {plan['new_total']})")
        else:
            add("FAIL", "word_budget", "body",
                f"{total} words; limit {limit}. Cutting sections alone cannot fit; tighten the prose")
    else:
        plan = lightest_cut(budget["sections"], total)
    pass_notes["word_budget"] = f"{total} / {limit} words"

    order = {c: n for n, (c, _) in enumerate(LINT_CHECKS)}
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], order[f["check"]]))
    failed_checks = {f["check"] for f in findings}
    passes = [{"severity": "PASS", "check": c, "where": "", "detail": pass_notes.get(c, "")}
              for c, _ in LINT_CHECKS if c not in failed_checks]
    n_fail = sum(1 for f in findings if f["severity"] == "FAIL")
    n_warn = sum(1 for f in findings if f["severity"] == "WARN")
    result = "FAIL" if n_fail else ("WARN" if n_warn else "PASS")
    return {"result": result, "fail": n_fail, "warn": n_warn, "passed_checks": len(passes),
            "word_budget": {**budget, "limit": limit}, "cut": plan, "over_budget": total > limit,
            "findings": findings, "passes": passes}


def lint_markdown(rep: dict, d: dict, cfg: dict, rules: dict) -> str:
    name = cfg.get("newsletter_name", "").strip() or "Newsletter"
    wb = rep["word_budget"]
    out = [f"#### Newsletter lint {EM_DASH} {name}, {fmt_date(d['_issue_date'])}", ""]
    out.append(f"**Result: {rep['result']}** {MIDDOT} {rep['fail']} FAIL {MIDDOT} {rep['warn']} WARN {MIDDOT} "
               f"{rep['passed_checks']} checks passed")
    out.append("")
    out.append(f"**Word budget:** {wb['total']} / {wb['limit']} words (opener {wb['opener']} {MIDDOT} sections "
               f"{sum(wb['sections'])} {MIDDOT} CTA {wb['cta']} {MIDDOT} sign-off {wb['sign_off']}; subject lines, "
               f"preheader and button label excluded)")
    out.append("")
    out.append(f"| {SECT} | Headline | Body words | Target | Source link |")
    out.append("|---|---|---|---|---|")
    for i, s in enumerate(d["sections"], 1):
        link = "yes" if s["source_url"] else "none"
        out.append(f"| {i} | {md_cell(short(s['headline']))} | {wb['section_bodies'][i - 1]} | "
                   f"{rules['section_words_min']}-{rules['section_words_max']} | {link} |")
    out.append("")
    cut = rep["cut"]
    if rep["over_budget"]:
        if cut:
            names = "; ".join(f"{SECT}{n} \"{short(d['sections'][n - 1]['headline'], 40)}\"" for n in cut["sections"])
            out.append(f"**Over budget by {wb['total'] - wb['limit']} words.** Cut {names} "
                       f"(-{cut['removed']} words -> {cut['new_total']} / {wb['limit']}). Rule: fewest sections, "
                       f"then least words lost, then the later section.")
        else:
            out.append(f"**Over budget by {wb['total'] - wb['limit']} words.** No combination of section cuts fits; "
                       f"tighten the prose.")
    elif cut:
        n = cut["sections"][0]
        out.append(f"Trim option if needed: cut {SECT}{n} \"{short(d['sections'][n - 1]['headline'], 40)}\" "
                   f"(-{cut['removed']} words -> {cut['new_total']} / {wb['limit']}).")
    out.append("")
    out.append("| Status | Check | Where | Detail |")
    out.append("|---|---|---|---|")
    labels = dict(LINT_CHECKS)
    for f in rep["findings"] + rep["passes"]:
        out.append(f"| {f['severity']} | {labels[f['check']]} | {md_cell(f['where']) or 'all'} | {md_cell(f['detail'])} |")
    out.append("")
    if rep["fail"]:
        out.append("Fix every FAIL and re-run lint before showing the draft. Figures and quotes are checked against "
                   "facts[] and the brief only; the tool cannot confirm the quotes match the live source.")
    else:
        out.append("No FAIL. Review WARN rows, then render. Figures and quotes were matched against facts[] and the "
                   "brief only; the tool cannot confirm the quotes match the live source.")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- render


def inline_html(text: str, brand: dict) -> str:
    out = []
    pos = 0
    pattern = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*")
    for m in pattern.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=True))
        if m.group(1) is not None:
            out.append(f'<a href="{html.escape(m.group(2), quote=True)}" target="_blank" '
                       f'style="color:{brand["accent_color"]};text-decoration:underline;">'
                       f'{html.escape(m.group(1), quote=True)}</a>')
        else:
            out.append(f'<strong>{html.escape(m.group(3), quote=True)}</strong>')
        pos = m.end()
    out.append(html.escape(text[pos:], quote=True))
    return "".join(out)


def inline_text(text: str) -> str:
    text = LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    return text.replace("**", "")


def paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


def button_label(d: dict, rules: dict) -> str:
    return d["cta"]["button_label"] or rules["render"]["default_button_labels"].get(d["cta"]["type"], "Open")


def budget_line(d: dict, cfg: dict | None) -> str:
    if not cfg:
        return ""
    b = word_budget(d)
    line = f"*Body: {b['total']} / {cfg['max_word_count']} words."
    cut = cut_plan(b["sections"], b["total"], cfg["max_word_count"]) if b["total"] > cfg["max_word_count"] \
        else lightest_cut(b["sections"], b["total"])
    if cut:
        secs = ", ".join(f"{SECT}{n}" for n in cut["sections"])
        verb = "Cut" if b["total"] > cfg["max_word_count"] else f"Sections are modular {EM_DASH} cut"
        line += f" {verb} {secs} (-{cut['removed']} words -> {cut['new_total']}) if you need it shorter.*"
    else:
        line += "*"
    return line


def render_markdown(d: dict, cfg: dict | None, rules: dict) -> str:
    out = [f"#### Newsletter draft {EM_DASH} Week of {fmt_date(d['_issue_date'])}", ""]
    if cfg:
        meta = []
        if cfg.get("newsletter_name"):
            meta.append(f"**Newsletter:** {cfg['newsletter_name']}")
        if cfg.get("from_name"):
            meta.append(f"**From:** {cfg['from_name']}")
        if cfg.get("audience"):
            meta.append(f"**Audience:** {cfg['audience']}")
        if meta:
            out.append(f" {MIDDOT} ".join(meta))
            out.append("")
    out.append("**Subject line options:**")
    for i, s in enumerate(d["subject_lines"], 1):
        out.append(f"{i}. \"{s}\" ({len(s)} chars)")
    out.append("")
    if d["preheader"].strip():
        out.append(f"**Preheader:** {d['preheader'].strip()}")
        out.append("")
    out.append("---")
    out.append("")
    out.extend(p + "\n" for p in paragraphs(d["opener"]))
    for i, s in enumerate(d["sections"], 1):
        out.append(f"### {s['headline'].strip()}")
        out.append("")
        out.extend(p + "\n" for p in paragraphs(s["body"]))
        if s["source_url"]:
            title = s["source_title"] or domain_of(s["source_url"])
            out.append(f"{RARROW} [{title}]({s['source_url']})")
            out.append("")
    out.append("---")
    out.append("")
    out.extend(p + "\n" for p in paragraphs(d["cta"]["text"]))
    if d["cta"]["type"] != "reply" and d["cta"]["url"]:
        out.append(f"**[{button_label(d, rules)} {RARROW}]({d['cta']['url']})**")
        out.append("")
    if d["sign_off"].strip():
        out.append(d["sign_off"].strip())
        out.append("")
    bl = budget_line(d, cfg)
    if bl:
        out.append("---")
        out.append("")
        out.append(bl)
    return "\n".join(out).rstrip("\n") + "\n"


def render_text(d: dict, cfg: dict | None, rules: dict) -> str:
    width = rules["render"]["text_wrap_width"]
    rule = "-" * min(width, 40)
    out = []
    name = (cfg or {}).get("newsletter_name", "").strip()
    out.append(f"{name} {EM_DASH} {fmt_date(d['_issue_date'])}" if name else fmt_date(d["_issue_date"]))
    out.append("")
    for p in paragraphs(d["opener"]):
        out.append(textwrap.fill(inline_text(p), width))
        out.append("")
    for s in d["sections"]:
        out.append(rule)
        out.append(s["headline"].strip())
        out.append("")
        for p in paragraphs(s["body"]):
            out.append(textwrap.fill(inline_text(p), width, break_long_words=False, break_on_hyphens=False))
            out.append("")
        if s["source_url"]:
            out.append(f"Source: {s['source_title'] or domain_of(s['source_url'])}")
            out.append(s["source_url"])
            out.append("")
    out.append(rule)
    for p in paragraphs(d["cta"]["text"]):
        out.append(textwrap.fill(inline_text(p), width))
        out.append("")
    if d["cta"]["type"] != "reply" and d["cta"]["url"]:
        out.append(f"{button_label(d, rules)}: {d['cta']['url']}")
        out.append("")
    if d["sign_off"].strip():
        out.append(d["sign_off"].strip())
    return "\n".join(out).rstrip("\n") + "\n"


def render_html(d: dict, cfg: dict | None, rules: dict) -> str:
    b = merged_brand(rules, cfg)
    font = html.escape(b["font_family"], quote=True)
    txt = b["text_color"]
    p_style = f"margin:0 0 16px 0;font-family:{font};font-size:16px;line-height:24px;color:{txt};"
    name = (cfg or {}).get("newsletter_name", "").strip()
    from_name = (cfg or {}).get("from_name", "").strip()
    pad = "&#8204;&nbsp;" * rules["render"]["preheader_padding_entities"]
    L: list[str] = []
    a = L.append
    a('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
      '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">')
    a('<html xmlns="http://www.w3.org/1999/xhtml" lang="en">')
    a("<head>")
    a('<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />')
    a('<meta name="viewport" content="width=device-width, initial-scale=1.0" />')
    a('<meta name="x-apple-disable-message-reformatting" />')
    a(f"<title>{html.escape(d['subject_lines'][0], quote=True)}</title>")
    a("</head>")
    a(f'<body style="margin:0;padding:0;background-color:{b["background_color"]};">')
    a(f'<span style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;'
      f'mso-hide:all;color:{b["background_color"]};">{html.escape(d["preheader"].strip(), quote=True)}{pad}</span>')
    a(f'<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" '
      f'bgcolor="{b["background_color"]}" style="background-color:{b["background_color"]};">')
    a("<tr>")
    a('<td align="center" style="padding:24px 12px;">')
    a('<!--[if mso]><table role="presentation" width="600" border="0" cellpadding="0" cellspacing="0"><tr><td><![endif]-->')
    a(f'<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" '
      f'bgcolor="{b["content_background_color"]}" '
      f'style="width:100%;max-width:600px;background-color:{b["content_background_color"]};">')
    # header
    header_bits = []
    if name:
        header_bits.append(f'<p style="margin:0;font-family:{font};font-size:20px;line-height:28px;font-weight:bold;'
                           f'color:{txt};">{html.escape(name, quote=True)}</p>')
    meta = fmt_date(d["_issue_date"]) + (f" {MIDDOT} {from_name}" if from_name else "")
    header_bits.append(f'<p style="margin:4px 0 0 0;font-family:{font};font-size:13px;line-height:20px;'
                       f'color:{b["muted_color"]};">{html.escape(meta, quote=True)}</p>')
    a("<tr>")
    a(f'<td style="padding:28px 32px 20px 32px;border-bottom:3px solid {b["accent_color"]};">'
      + "".join(header_bits) + "</td>")
    a("</tr>")

    def divider() -> None:
        a("<tr>")
        a(f'<td style="padding:0 32px;"><table role="presentation" width="100%" border="0" cellpadding="0" '
          f'cellspacing="0"><tr><td style="border-top:1px solid {b["rule_color"]};font-size:0;line-height:0;">'
          f'&nbsp;</td></tr></table></td>')
        a("</tr>")

    a("<tr>")
    a('<td style="padding:24px 32px 8px 32px;">'
      + "".join(f'<p style="{p_style}">{inline_html(p, b)}</p>' for p in paragraphs(d["opener"])) + "</td>")
    a("</tr>")
    for s in d["sections"]:
        divider()
        cell = [f'<h2 style="margin:0 0 12px 0;font-family:{font};font-size:20px;line-height:28px;font-weight:bold;'
                f'color:{txt};">{html.escape(s["headline"].strip(), quote=True)}</h2>']
        cell += [f'<p style="{p_style}">{inline_html(p, b)}</p>' for p in paragraphs(s["body"])]
        if s["source_url"]:
            title = s["source_title"] or domain_of(s["source_url"])
            cell.append(f'<p style="margin:0 0 8px 0;font-family:{font};font-size:14px;line-height:22px;">'
                        f'<a href="{html.escape(s["source_url"], quote=True)}" target="_blank" '
                        f'style="color:{b["accent_color"]};text-decoration:underline;font-weight:bold;">'
                        f'{html.escape(title, quote=True)} &rarr;</a></p>')
        a("<tr>")
        a('<td style="padding:24px 32px 8px 32px;">' + "".join(cell) + "</td>")
        a("</tr>")
    divider()
    cta = d["cta"]
    cell = [f'<p style="{p_style}">{inline_html(p, b)}</p>' for p in paragraphs(cta["text"])]
    if cta["type"] != "reply" and cta["url"]:
        cell.append(
            '<table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin:8px 0 16px 0;">'
            f'<tr><td align="center" bgcolor="{b["accent_color"]}" '
            f'style="border-radius:6px;background-color:{b["accent_color"]};">'
            f'<a href="{html.escape(cta["url"], quote=True)}" target="_blank" '
            f'style="display:inline-block;padding:12px 24px;font-family:{font};font-size:16px;line-height:20px;'
            f'font-weight:bold;color:{b["button_text_color"]};text-decoration:none;border-radius:6px;">'
            f'{html.escape(button_label(d, rules), quote=True)}</a></td></tr></table>')
    if d["sign_off"].strip():
        cell.append(f'<p style="margin:8px 0 0 0;font-family:{font};font-size:16px;line-height:24px;color:{txt};">'
                    f'{html.escape(d["sign_off"].strip(), quote=True)}</p>')
    a("<tr>")
    a('<td style="padding:24px 32px 32px 32px;">' + "".join(cell) + "</td>")
    a("</tr>")
    a("</table>")
    a("<!--[if mso]></td></tr></table><![endif]-->")
    a("</td>")
    a("</tr>")
    a("</table>")
    a("</body>")
    a("</html>")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- check-html


class EmailHTMLChecker(HTMLParser):
    def __init__(self, rules: dict):
        super().__init__(convert_charrefs=True)
        self.r = rules["html"]
        self.findings: list[dict] = []
        self.stack: list[tuple[str, int]] = []
        self.doctype = False
        self.charset = False
        self.tables = 0
        self.tables_no_role = 0
        self.links = 0
        self.images = 0
        self.preheader = False
        self.css_res = [(re.compile(c["pattern"], re.IGNORECASE), c["severity"], c["reason"])
                        for c in self.r["forbidden_css"]]
        self.merge_res = [re.compile(p) for p in self.r["merge_tag_patterns"]]
        self.void = set(self.r["void_tags"])
        self.forbidden = set(self.r["forbidden_tags"])

    def add(self, sev: str, check: str, detail: str) -> None:
        self.findings.append({"severity": sev, "check": check, "line": self.getpos()[0], "detail": detail})

    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype"):
            self.doctype = True

    def _width_px(self, val: str) -> int | None:
        m = re.fullmatch(r"\s*(\d+)\s*(px)?\s*", val)
        return int(m.group(1)) if m else None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._element(tag, attrs)
        if tag not in self.void:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._element(tag, attrs)

    def _element(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        maxw = self.r["max_width_px"]
        if tag in self.forbidden:
            self.add("FAIL", "forbidden_tags", f"<{tag}> is not email-safe")
        for k in a:
            if k.startswith("on"):
                self.add("FAIL", "event_handlers", f"<{tag} {k}=...> event handler attribute")
        if "class" in a:
            self.add("WARN", "css", f"<{tag} class=\"{a['class']}\"> has no effect without a <style> block")
        if tag == "meta" and ("charset" in a or "charset=" in a.get("content", "").lower()):
            self.charset = True
        if tag == "table":
            self.tables += 1
            if a.get("role") != "presentation":
                self.tables_no_role += 1
        if tag in ("table", "td", "th", "img") and "width" in a:
            px = self._width_px(a["width"])
            if px is not None and px > maxw:
                self.add("FAIL", "layout_width", f"<{tag} width=\"{a['width']}\"> exceeds {maxw}px")
        style = a.get("style", "")
        if style:
            for rx, sev, reason in self.css_res:
                m = rx.search(style)
                if m:
                    self.add(sev, "css", f"<{tag}> style \"{m.group(0).strip(' ;')}\": {reason}")
            for decl in style.split(";"):
                if ":" not in decl:
                    continue
                prop, val = (x.strip().lower() for x in decl.split(":", 1))
                if prop in ("width", "min-width"):
                    px = self._width_px(val)
                    if px is not None and px > maxw:
                        self.add("FAIL", "layout_width", f"<{tag}> style {prop}:{val} exceeds {maxw}px")
            sl = style.replace(" ", "").lower()
            if "display:none" in sl and "mso-hide:all" in sl:
                self.preheader = True
        if tag == "a":
            self.links += 1
            href = a.get("href", "").strip()
            if not href:
                self.add("FAIL", "links", "<a> without an href")
            elif any(rx.match(href) for rx in self.merge_res):
                pass
            else:
                scheme = href.split(":", 1)[0].lower() if ":" in href else ""
                if scheme not in self.r["allowed_href_schemes"]:
                    self.add("FAIL", "links", f"href \"{href[:60]}\" is not an absolute "
                                              f"{'/'.join(self.r['allowed_href_schemes'])} link")
                elif scheme == "http":
                    self.add("WARN", "links", f"href \"{href[:60]}\" is not https")
        if tag == "img":
            self.images += 1
            if "alt" not in a:
                self.add("FAIL", "images", "<img> without alt text")
            src = a.get("src", "")
            if not src.lower().startswith("https://"):
                self.add("WARN", "images", f"<img src=\"{src[:60]}\"> is not an absolute https URL")
            if "width" not in a:
                self.add("WARN", "images", "<img> without a width attribute (Outlook renders it full size)")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.void:
            return
        if not self.stack:
            self.add("FAIL", "nesting", f"</{tag}> closes nothing")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        names = [t for t, _ in self.stack]
        if tag in names:
            while self.stack and self.stack[-1][0] != tag:
                t, line = self.stack.pop()
                self.findings.append({"severity": "FAIL", "check": "nesting", "line": line,
                                      "detail": f"<{t}> is never closed (closed implicitly by </{tag}>)"})
            self.stack.pop()
        else:
            self.add("FAIL", "nesting", f"</{tag}> does not match open <{self.stack[-1][0]}>")


HTML_CHECKS = [
    ("doctype", "doctype"),
    ("charset", "charset declared"),
    ("size", "size (Gmail clipping)"),
    ("tables", "table-based layout"),
    ("layout_width", f"width <= max"),
    ("forbidden_tags", "no unsafe tags"),
    ("css", "inline CSS email-safe"),
    ("event_handlers", "no script handlers"),
    ("links", "links absolute"),
    ("images", "images have alt"),
    ("nesting", "tags balanced"),
    ("preheader", "hidden preheader"),
    ("presentation_role", "layout tables role=presentation"),
]


def check_html(text: str, rules: dict) -> dict:
    hr = rules["html"]
    p = EmailHTMLChecker(rules)
    p.feed(text)
    p.close()
    for tag, line in reversed(p.stack):
        p.findings.append({"severity": "FAIL", "check": "nesting", "line": line, "detail": f"<{tag}> is never closed"})
    size = len(text.encode("utf-8"))
    extra = []
    if not p.doctype:
        extra.append(("WARN", "doctype", "no <!DOCTYPE>; some clients fall back to quirks mode"))
    if not p.charset:
        extra.append(("WARN", "charset", "no charset meta; non-ASCII text may garble"))
    if size > hr["max_bytes"]:
        extra.append(("FAIL", "size", f"{size} bytes; Gmail clips messages over {hr['max_bytes']}"))
    elif size > hr["warn_bytes"]:
        extra.append(("WARN", "size", f"{size} bytes; close to Gmail's {hr['max_bytes']}-byte clip"))
    if p.tables == 0:
        extra.append(("FAIL", "tables", "no <table>; email layout must be table-based"))
    if p.tables_no_role:
        extra.append(("WARN", "presentation_role", f"{p.tables_no_role} table(s) without role=\"presentation\""))
    if not p.preheader:
        extra.append(("WARN", "preheader", "no hidden preheader element (display:none + mso-hide:all)"))
    for sev, check, detail in extra:
        p.findings.append({"severity": sev, "check": check, "line": 0, "detail": detail})
    order = {c: n for n, (c, _) in enumerate(HTML_CHECKS)}
    p.findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], order[f["check"]], f["line"]))
    notes = {
        "doctype": "present", "charset": "UTF-8 declared", "size": f"{size} bytes (limit {hr['max_bytes']})",
        "tables": f"{p.tables} tables", "layout_width": f"nothing wider than {hr['max_width_px']}px",
        "forbidden_tags": "no style/link/div/script/form/media tags",
        "css": "no flex/grid/positioning/at-rules/variables", "event_handlers": "none",
        "links": f"{p.links} links, all absolute https/mailto", "images": f"{p.images} images",
        "nesting": "every tag closed in order", "preheader": "present",
        "presentation_role": "all layout tables",
    }
    failed = {f["check"] for f in p.findings}
    passes = [{"severity": "PASS", "check": c, "line": 0, "detail": notes[c]} for c, _ in HTML_CHECKS if c not in failed]
    n_fail = sum(1 for f in p.findings if f["severity"] == "FAIL")
    n_warn = sum(1 for f in p.findings if f["severity"] == "WARN")
    return {"result": "FAIL" if n_fail else ("WARN" if n_warn else "PASS"), "fail": n_fail, "warn": n_warn,
            "passed_checks": len(passes), "bytes": size, "findings": p.findings, "passes": passes}


def check_html_markdown(rep: dict, name: str) -> str:
    labels = dict(HTML_CHECKS)
    out = [f"#### Email HTML check {EM_DASH} {name}", ""]
    out.append(f"**Result: {rep['result']}** {MIDDOT} {rep['fail']} FAIL {MIDDOT} {rep['warn']} WARN {MIDDOT} "
               f"{rep['passed_checks']} checks passed")
    out.append("")
    out.append("| Status | Check | Line | Detail |")
    out.append("|---|---|---|---|")
    for f in rep["findings"] + rep["passes"]:
        line = str(f["line"]) if f["line"] else "-"
        out.append(f"| {f['severity']} | {labels[f['check']]} | {line} | {md_cell(f['detail'])} |")
    out.append("")
    out.append("Static check only. It does not render in real clients; send a test to Gmail and Outlook "
               "from your ESP before scheduling.")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- voice-stats


def parse_issue(text: str) -> dict:
    lines = text.splitlines()
    subject = ""
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0].split(":", 1)[1].strip()
        lines = lines[1:]
    headings, body_lines = [], []
    for ln in lines:
        s = ln.strip()
        if re.match(r"#{1,6}\s", s):
            headings.append(re.sub(r"^#{1,6}\s+", "", s))
        else:
            body_lines.append(s)
    nonempty = [s for s in body_lines if s]
    sign_off = nonempty[-1] if nonempty else ""
    paras = [p for p in re.split(r"\n\s*\n", "\n".join(body_lines)) if p.strip()]
    opener = paras[0] if paras else ""
    labels = re.findall(r"^\*\*([^*]{2,40}?:?)\*\*", "\n".join(body_lines), flags=re.MULTILINE)
    return {"subject": subject, "headings": headings, "body": "\n".join(body_lines), "sign_off": sign_off,
            "opener": opener, "labels": labels}


def voice_stats(paths: list[str], rules: dict) -> dict:
    files = sorted(paths, key=lambda p: Path(p).name)
    n = rules["voice_issue_count"]
    used = files[-n:]
    skipped = files[:-n] if len(files) > n else []
    issues = []
    for path in used:
        text = load_text(path)
        if not text.strip():
            raise InputError(f"{path}: file is empty")
        iss = parse_issue(text)
        iss["path"] = Path(path).name
        issues.append(iss)
    sents, words_all = [], []
    for iss in issues:
        body_wo_sign = iss["body"]
        if iss["sign_off"]:
            body_wo_sign = body_wo_sign.rsplit(iss["sign_off"], 1)[0]
        sents += split_sentences(body_wo_sign, rules["sentence_abbreviations"])
        words_all += word_tokens(body_wo_sign)
    lens = [count_words(s) for s in sents] or [0]
    total = len(words_all) or 1

    def per_k(count: int) -> str:
        return f"{count * 1000 / total:.1f}"

    first = sum(1 for w in words_all if w in ("i", "me", "my", "i'm", "i've", "i'll", "i'd"))
    we = sum(1 for w in words_all if w in ("we", "our", "us", "we're", "we've", "we'll", "ours"))
    you = sum(1 for w in words_all if w in ("you", "your", "you're", "you've", "you'll", "yours"))
    contractions = sum(1 for w in words_all if "'" in w)
    bodies = "\n".join(i["body"] for i in issues)
    excl = bodies.count("!")
    quest = bodies.count("?")
    headings = [h for i in issues for h in i["headings"]]
    sentence_case = sum(1 for h in headings if sum(1 for w in h.split()[1:] if w[:1].isupper() and
                                                   w.upper() != w) <= 1)
    label_counts: dict[str, int] = {}
    for iss in issues:
        for lab in set(iss["labels"]):
            label_counts[lab] = label_counts.get(lab, 0) + 1
    recurring = sorted(lab for lab, c in label_counts.items() if c >= 2)
    signs = [i["sign_off"] for i in issues]
    opener_sents = [len(split_sentences(i["opener"], rules["sentence_abbreviations"])) for i in issues]
    banned_found = sorted({h for i in issues for h in find_phrases(i["body"], rules["banned_phrases"])})
    subj = [i["subject"] for i in issues if i["subject"]]
    return {
        "used": [i["path"] for i in issues], "skipped": [Path(p).name for p in skipped],
        "words": [count_words(i["body"]) for i in issues],
        "sentences": len(lens) if sents else 0,
        "avg_sentence": round(statistics.mean(lens), 1), "median_sentence": statistics.median(lens),
        "short_share": round(100 * sum(1 for x in lens if x <= 8) / len(lens)),
        "long_share": round(100 * sum(1 for x in lens if x >= 25) / len(lens)),
        "longest": max(lens),
        "per_k": {"I/me/my": per_k(first), "we/our/us": per_k(we), "you/your": per_k(you),
                  "contractions": per_k(contractions), "exclamation marks": per_k(excl),
                  "questions": per_k(quest)},
        "headings": len(headings), "heading_avg_words": round(statistics.mean([len(h.split()) for h in headings]), 1)
        if headings else 0, "heading_sentence_case": sentence_case,
        "recurring_labels": recurring, "sign_offs": signs, "opener_sentences": opener_sents,
        "banned_found": banned_found, "subjects": subj,
    }


def voice_markdown(v: dict) -> str:
    out = ["#### Voice stats (measured from past issues)", ""]
    out.append(f"Issues analysed: {', '.join(v['used'])}" + (f" (older, not used: {', '.join(v['skipped'])})"
                                                             if v["skipped"] else ""))
    out.append(f"Body words per issue: {', '.join(str(w) for w in v['words'])}")
    out.append("")
    out.append("| Signal | Measured |")
    out.append("|---|---|")
    out.append(f"| Sentences analysed | {v['sentences']} |")
    out.append(f"| Average / median sentence length | {v['avg_sentence']} / {v['median_sentence']} words |")
    out.append(f"| Short (<= 8 words) / long (>= 25 words) sentences | {v['short_share']}% / {v['long_share']}% |")
    out.append(f"| Longest sentence | {v['longest']} words |")
    for k, val in v["per_k"].items():
        out.append(f"| {k} per 1,000 words | {val} |")
    out.append(f"| Section headings | {v['headings']} (avg {v['heading_avg_words']} words, "
               f"{v['heading_sentence_case']} in sentence case) |")
    out.append(f"| Opener length (sentences per issue) | {', '.join(str(x) for x in v['opener_sentences'])} |")
    recurring = ", ".join(f"\"{r}\"" for r in v["recurring_labels"]) or "none found"
    out.append(f"| Recurring bold labels (in 2+ issues) | {md_cell(recurring)} |")
    signs = v["sign_offs"]
    sign_txt = f"\"{signs[0]}\" (all {len(signs)} issues)" if signs and len(set(signs)) == 1 else \
        "; ".join(f"\"{s}\"" for s in signs)
    out.append(f"| Sign-off | {md_cell(sign_txt)} |")
    if v["subjects"]:
        lens = [len(s) for s in v["subjects"]]
        out.append(f"| Past subject lines | {len(lens)}, {min(lens)}-{max(lens)} chars |")
    banned = ", ".join(f"\"{b}\"" for b in v["banned_found"]) or "none"
    out.append(f"| Banned phrases already in past issues | {md_cell(banned)} |")
    out.append("")
    out.append("These are measurements, not the voice profile. Write the profile from them with "
               "voice-profile-template.md, and show it to the user.")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_l = sub.add_parser("lint", help="lint a draft.json")
    p_l.add_argument("--draft", required=True)
    p_l.add_argument("--config", required=True)
    p_l.add_argument("--brief", help="the brief (text/markdown); its figures and quotes count as verified")
    p_l.add_argument("--rules", default=str(DEFAULT_RULES))
    p_l.add_argument("--json", action="store_true")

    p_r = sub.add_parser("render", help="render a draft.json")
    p_r.add_argument("--draft", required=True)
    p_r.add_argument("--config", help="newsletter config (name, sender, brand, word budget)")
    p_r.add_argument("--format", choices=["html", "markdown", "text"],
                     help="defaults to the config's format, else html")
    p_r.add_argument("--rules", default=str(DEFAULT_RULES))
    p_r.add_argument("--out", help="write to this file instead of stdout")

    p_c = sub.add_parser("check-html", help="validate email-safe HTML")
    p_c.add_argument("--file", required=True)
    p_c.add_argument("--rules", default=str(DEFAULT_RULES))
    p_c.add_argument("--json", action="store_true")

    p_v = sub.add_parser("voice-stats", help="measure voice signals in past issues")
    p_v.add_argument("--issues", nargs="+", required=True, help="markdown files, one per past issue")
    p_v.add_argument("--rules", default=str(DEFAULT_RULES))
    p_v.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    try:
        rules = load_rules(args.rules)
        if args.cmd == "lint":
            cfg = load_config(args.config)
            d = load_draft(args.draft)
            brief = load_text(args.brief) if args.brief else ""
            rep = lint(d, cfg, rules, brief)
            if args.json:
                print(json.dumps(rep, indent=2, ensure_ascii=False))
            else:
                sys.stdout.write(lint_markdown(rep, d, cfg, rules))
            return 1 if rep["fail"] else 0
        if args.cmd == "render":
            cfg = load_config(args.config) if args.config else None
            d = load_draft(args.draft)
            fmt = args.format or (cfg.get("format", "html") if cfg else "html")
            if d["cta"]["type"] not in CTA_TYPES:
                raise InputError(f"{args.draft}: cta.type: '{d['cta']['type']}' is not one of {', '.join(CTA_TYPES)}")
            out = {"html": render_html, "markdown": render_markdown, "text": render_text}[fmt](d, cfg, rules)
            if args.out:
                Path(args.out).write_text(out, encoding="utf-8")
            else:
                sys.stdout.write(out)
            return 0
        if args.cmd == "check-html":
            text = load_text(args.file)
            rep = check_html(text, rules)
            if args.json:
                print(json.dumps(rep, indent=2, ensure_ascii=False))
            else:
                sys.stdout.write(check_html_markdown(rep, Path(args.file).name))
            return 1 if rep["fail"] else 0
        if args.cmd == "voice-stats":
            v = voice_stats(args.issues, rules)
            if args.json:
                print(json.dumps(v, indent=2, ensure_ascii=False))
            else:
                sys.stdout.write(voice_markdown(v))
            return 0
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
