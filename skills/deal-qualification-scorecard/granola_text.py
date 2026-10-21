"""granola_text: shared text helpers for the Granola skill packages.

Copied byte-for-byte into each package that checks quotes against meeting text, redacts contact details or renders
markdown / Slack text. Stdlib only; same behaviour on Python 3.8-3.14 (for characters assigned in Unicode 12.1).

Public API
    normalise(text)                     -> str     one comparable line of plain text
    split_units(source_text)            -> [(turn_index, speaker_or_None, [sentences])]
    check_quote(quote, source_text)     -> (ok, reason)   whole-sentence rule
    redact_contacts(text)               -> str     emails always, phones only on clear signals
    shorten(text, n)                    -> str     redact first, then cut to at most n characters
    md(text) / slack(text)              -> str     inert markdown / Slack mrkdwn text
    is_question_or_conditional(sentence)-> bool    heuristic

Everything here is pattern-based and heuristic. See README.md for the claims a skill may make about it.
"""

import bisect
import functools
import html
import re
import unicodedata

__all__ = ["normalise", "split_units", "check_quote", "redact_contacts", "shorten", "md", "slack",
           "is_question_or_conditional", "REASONS", "VERSION"]

VERSION = "1.6.0"

REASONS = {
    "ok": "ok",
    "not_text": "quote and meeting text must be text",
    "empty": "empty quote",
    "short": "too short (fewer than 3 words)",
    "ellipsis": "contains an ellipsis",
    "label": "contains a speaker label",
    "partial": "not a complete sentence in the meeting text",
    "spans": "spans two speakers",
    "question": "changes the sentence's final question mark",
    "missing": "not found in the meeting text",
}

MIN_QUOTE_WORDS = 3
MAX_LABEL_LOOKBACK = 120

# --------------------------------------------------------------------------- normalisation

_NEWLINES = {"\r\n": "\n", "\r": "\n", "\x0b": "\n", "\x0c": "\n", "\x85": "\n", "\u2028": "\n", "\u2029": "\n"}
_NEWLINE_RE = re.compile("\r\n|[\r\x0b\x0c\x85\u2028\u2029]")
# Fixed set (not unicodedata categories alone) so 3.8 and 3.14 drop the same characters.
_INVISIBLE = set(chr(c) for c in [0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D,
                                  0x180E, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D,
                                  0x202E, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x2066, 0x2067, 0x2068, 0x2069,
                                  0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F, 0x3164, 0xFE00, 0xFE01, 0xFE02,
                                  0xFE03, 0xFE04, 0xFE05, 0xFE06, 0xFE07, 0xFE08, 0xFE09, 0xFE0A, 0xFE0B, 0xFE0C,
                                  0xFE0D, 0xFE0E, 0xFE0F, 0xFEFF, 0xFFA0, 0xFFF9, 0xFFFA, 0xFFFB])
# Apostrophe look-alikes mapped BEFORE NFKC: NFKC turns U+00B4 and U+1FFD into a space plus a combining accent, which
# would split "Didn´t"; U+02B9/U+02BD/U+02C8/U+02CA and friends are left alone by NFKC.
_PRE_NFKC_MAP = {ord(_c): "'" for _c in "\u00b4\u02b9\u02bd\u02be\u02bf\u02c8\u02ca\u02cb\u02ee\u05f3\u275c\u2e42\u055a\u1fbd\u1fbf\u1ffd\ua78c"}
_PUNCT_MAP = {}
for _c in "\u2018\u2019\u201a\u201b\u2032\u2035\u02bc\u02bb\u2039\u203a\uff07":
    _PUNCT_MAP[ord(_c)] = "'"
for _c in "\u201c\u201d\u201e\u201f\u2033\u2036\u00ab\u00bb\u301d\u301e\u301f\uff02":
    _PUNCT_MAP[ord(_c)] = '"'
for _c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2e3a\u2e3b\ufe58\ufe63\uff0d\u2043\u2e17\u30a0":
    _PUNCT_MAP[ord(_c)] = "-"
for _c in "\u3002\uff61":
    _PUNCT_MAP[ord(_c)] = "."
_PUNCT_MAP[ord("\u22ef")] = "..."
_PUNCT_MAP[ord("\t")] = " "

_MD_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_MD_LINK_RE = re.compile(r"!?\[([^\[\]\n]*)\](?:\([^()\n]*\)|\[[^\[\]\n]*\])")
_MD_AUTOLINK_RE = re.compile(r"<((?:https?|ftp|mailto):[^<>\s]*|www\.[^<>\s]*|[^<>\s@]{1,64}@[^<>\s@]+)>",
                             re.IGNORECASE)
_MD_EMPHASIS_RE = re.compile(r"(?<![^\s(\[\"'])[*_]+(?=[^\s*_])|(?<=[^\s*_])[*_]+(?![^\s)\].,;:!?\"'])")
_LINE_MARKER_RE = re.compile(
    r"^(?:>\s?)+|^#{1,6}(?=\s|$)|^[-*+\u2022\u2023\u25aa\u25ab\u25e6\u25cf\u25cb\u25a0\u25a1\u2219\u00b7\u2027\u25b8"
    r"\u25ba\u2794\u27a2](?=\s)|^[0-9]{1,3}[.)](?=\s)|^\[[ xX]\](?=\s)")
_RULE_LINE_RE = re.compile(r"^[\s\-*_=~#|+]*$")


def _prep(text):
    """Entities, newlines, NFKC, invisible/control characters, quote and dash look-alikes, Granola backslash escapes,
    links, autolinks, code and emphasis markers. Line breaks are kept."""
    text = html.unescape(text)
    text = _NEWLINE_RE.sub("\n", text).translate(_PRE_NFKC_MAP)
    text = unicodedata.normalize("NFKC", text).translate(_PRE_NFKC_MAP)
    text = "".join(ch for ch in text if ch == "\n" or not (ch in _INVISIBLE or unicodedata.category(ch) in ("Cc", "Cs")))
    text = text.translate(_PUNCT_MAP)
    text = _MD_ESCAPE_RE.sub(r"\1", text)
    text = _MD_AUTOLINK_RE.sub(r"\1", _MD_LINK_RE.sub(r"\1", text))
    for marker in ("**", "__", "~~", "==", "`"):
        text = text.replace(marker, "")
    return text


def _clean_line(line):
    """(text, is_bullet) for one prepared line: list, heading, quote-block and task markers stripped, emphasis
    removed, whitespace collapsed. is_bullet is "heading" for a markdown heading."""
    line = line.strip()
    bullet = "heading" if line.startswith("#") and _LINE_MARKER_RE.match(line) else False
    for _ in range(4):
        m = _LINE_MARKER_RE.match(line)
        if not m:
            break
        bullet = bullet or True
        line = line[m.end():].strip()
    if _RULE_LINE_RE.match(line):
        return "", bullet
    line = _MD_EMPHASIS_RE.sub("", line)
    return " ".join(line.split()), bullet


def normalise(text):
    """One line of comparable plain text: NFKC; HTML entities, Granola backslash escapes, markdown emphasis
    (** __ * _ ~~ ==), backticks, [label](url) -> label, <url> -> url, list/heading markers removed; curly quotes,
    apostrophes and dashes unified; invisible characters dropped; whitespace collapsed. Case is kept."""
    if not isinstance(text, str):
        raise TypeError("normalise() needs a str")
    parts = (_clean_line(line)[0] for line in _prep(text).split("\n"))
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------- speaker labels

_FIXED_ANY_CASE = re.compile(r"(?:microphone|system audio|speaker ?[0-9]{1,3}|speaker [a-z]|unknown speaker|"
                             r"interviewer|interviewee|participant(?: ?[0-9]{1,3})?|p[0-9]{1,3}|moderator|respondent)",
                             re.IGNORECASE)
_FIXED_LINE_START = re.compile(r"(?:me|them|you|q|a|customer|candidate|host|guest)", re.IGNORECASE)
_FIXED_TITLE = re.compile(r"(?:Me|Them|Q|A)")
_CONNECTORS = {"of", "the", "and", "&", "de", "da", "di", "du", "del", "der", "van", "von", "la", "le", "y", "for", "at",
               }
# Words that turn what follows into its opposite, a quantifier or a condition. They are never part of a label, and
# a label is not a clean turn boundary while one of them is in scope (see _scope_clean), so "No Dana: we renew"
# cannot become a quotable "we renew". _negates() adds rules on top of this set: every n't contraction, auxiliary +
# "nt" typed without the apostrophe (didnt, cant), clitics with or without the apostrophe (nobody's, nobodys) and
# inflections of the negative verbs in _NEGATIVE_VERBS.
_NEGATORS = {"no", "not", "nobody", "none", "never", "neither", "nor", "nothing", "nowhere", "noone", "no-one",
             "zero", "few", "little", "hardly", "barely", "scarcely", "rarely", "seldom", "without", "except",
             "unless", "if", "only", "cannot", "nope", "nah", "all", "every", "everyone", "everybody", "any", "anyone",
             "anybody", "almost", "nearly", "assuming", "provided", "suppose", "supposing", "whether", "maybe",
             "perhaps", "non", "anti", "dis", "un", "in", "im", "ir", "il", "mis", "less", "against", "ex", "former",
             "previously", "used", "nary", "zilch", "nada", "nil", "naught", "nought", "nothin", "nobodies",
             "unlikely", "doubtful", "barring", "absent", "sans", "lest", "excepting", "unable", "unwilling",
             "unsure", "unclear", "uncertain", "unconfirmed", "unsigned", "unapproved", "untrue", "false",
             "impossible", "supposedly", "allegedly", "hypothetically", "theoretically", "instead", "minus", "apart",
             "contrary"}
# Hosts of a clitic typed without its apostrophe: "nobodys" (nobody's), "nothingll", "nevers".
_CLITIC_HOSTS = {"nobody", "nothing", "noone", "none", "neither", "never", "nowhere", "everyone", "everybody",
                 "anyone", "anybody", "nothin"}
# Auxiliaries whose negative contraction can be typed without the apostrophe: dont, didnt, cant, wont, aint, shant.
_NT_STEMS = {"do", "does", "did", "ca", "can", "wo", "is", "are", "was", "were", "could", "would", "should", "has",
             "have", "had", "must", "need", "might", "dare", "ought", "ai", "sha", "may"}
_NEGATIVE_VERBS = ("deny", "refuse", "reject", "decline", "doubt", "fail", "lack", "stop", "quit", "cease", "oppose",
                   "dispute", "veto", "forbid", "prohibit", "exclude", "withdraw", "retract", "cancel")
_WORD_SPLIT_RE = re.compile(r"(?:[^\w']|_)+")
_CLITIC_RE = re.compile(r"(.+?)'(?:s|ll|d|re|ve|m)$")


def _inflections(stem):
    out = {stem, stem + "s", stem + "es", stem + "ed", stem + "d", stem + "ing", stem + "n"}  # withdrawn
    if stem.endswith("e"):
        out.add(stem[:-1] + "ing")
    if stem.endswith("y"):
        out.update((stem[:-1] + "ies", stem[:-1] + "ied"))
    if len(stem) <= 4 and stem[-1] not in "aeiouwy" and stem[-2] in "aeiou":
        out.update((stem + stem[-1] + "ed", stem + stem[-1] + "ing"))  # stopped, quitting
    if stem.endswith("l"):
        out.update((stem + "led", stem + "ling"))  # cancelled
    return out


for _verb in _NEGATIVE_VERBS:
    _NEGATORS.update(_inflections(_verb))


@functools.lru_cache(maxsize=4096)
def _negating_part(part):
    """One apostrophe-normalised, case-folded word with no separators."""
    if part in _NEGATORS or part.endswith("n't"):
        return True
    clitic = _CLITIC_RE.match(part)
    if clitic and clitic.group(1) in _NEGATORS:
        return True
    if part.endswith("nt") and part[:-2] in _NT_STEMS:
        return True
    return any(part.endswith(s) and part[:-len(s)] in _CLITIC_HOSTS for s in ("s", "ll", "d", "re", "ve"))


# Quantifier, condition and prefix words in _NEGATORS: they block labels like any negator, but on their own they
# don't keep a sentence open across an ambiguous full stop ("All of us met in Leeds. We renew" splits).
_WEAK_NEGATORS = {"all", "every", "everyone", "everybody", "any", "anyone", "anybody", "almost", "nearly", "only",
                  "if", "whether", "maybe", "perhaps", "assuming", "provided", "suppose", "supposing", "in", "im", "ir",
                  "il", "un", "dis", "mis", "non", "anti", "ex", "former", "previously", "used", "less", "instead"}


@functools.lru_cache(maxsize=8192)
def _negation_kind(token):
    """2 if any word of the token (split at every non-word character and "_": "Nobody/Team", "Nobody,Team",
    "{Nobody}", "Nobody_Team", "Nobody'Team") is a negation proper; 1 if the only such words are quantifier,
    condition or prefix words (_WEAK_NEGATORS); 0 if none."""
    kind = 0
    # Combining marks are dropped for the lookup only ("Nóbody", "No\u0301body" -> "Nobody"); the text is unchanged.
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", token.replace("’", "'"))
                     if unicodedata.category(ch) not in ("Mn", "Me"))
    for part in _WORD_SPLIT_RE.split(folded.casefold()):
        part = part.strip("'")
        if not part:
            continue
        # the split at "'" catches an apostrophe used as a joiner: "Nobody'Team"
        for piece in [part] + ([x for x in part.split("'") if x] if "'" in part else []):
            if _negating_part(piece):
                if piece not in _WEAK_NEGATORS:
                    return 2
                kind = 1
    return kind


def _negates(token):
    """True if any word of the token is a negation, quantifier or condition word (see _negation_kind)."""
    return _negation_kind(token) > 0
# Lower-case words allowed after the first word of a line-start label ("Ops lead:", "Sales rep (Acme):").
_ROLE_WORDS = {"lead", "leads", "manager", "head", "director", "rep", "representative", "engineer", "founder",
               "owner", "admin", "administrator", "buyer", "champion", "customer", "user", "team", "side", "contact",
               "sponsor", "analyst", "consultant", "partner", "recruiter", "candidate", "interviewer", "designer",
               "person", "guy", "folks", "officer", "president", "chief", "exec", "executive", "member", "agent",
               "coordinator", "specialist", "architect", "developer", "advisor", "adviser", "investor", "board",
               "counsel", "assistant", "associate", "principal", "staff", "intern", "trainee", "operator", "planner",
               "dispatcher", "driver", "nurse", "doctor", "teacher", "student", "participant", "speaker", "host",
               "guest", "moderator", "interviewee", "respondent", "caller", "audio", "voice", "colleague", "boss",
               "report", "mentor", "coach", "ae", "sdr", "bdr", "csm", "pm", "em", "eng", "ops", "finance",
               "legal", "procurement", "security", "it", "support", "success", "sales", "marketing", "product",
               "engineering", "hr", "people", "ceo", "cto", "cfo", "coo", "cro", "cmo", "vp", "svp", "evp"}
_FUNCTION_FIRST = {"the", "a", "an", "this", "that", "these", "those", "our", "my", "your", "their", "his", "her",
                   "its", "we", "i", "it", "they", "he", "she", "so", "and", "but", "or", "then", "also", "plus",
                   "what", "why", "how", "when", "where", "who", "which", "well", "okay", "ok", "yes", "yeah",
                   "there", "here", "now", "today", "tomorrow", "yesterday", "in", "on", "at", "for", "to", "with",
                   "from", "by", "as", "per", "via", "because", "since", "after", "before", "until", "while", "once"}
_NOT_SPEAKERS = {"decision", "decisions", "decided", "action", "actions", "action item", "action items", "next step",
                 "next steps", "note", "notes", "update", "updates", "summary", "agenda", "question", "questions",
                 "answer", "answers", "todo", "to do", "todos", "outcome", "outcomes", "result", "results", "owner",
                 "owners", "status", "context", "background", "example", "examples", "reason", "reasons", "why",
                 "takeaway", "takeaways", "follow-up", "follow-ups", "follow up", "follow ups", "fyi", "ps", "pps",
                 "important", "reminder", "rationale", "conclusion", "proposal", "risk", "risks", "issue", "issues",
                 "goal", "goals", "topic", "topics", "date", "time", "deadline", "revisit", "timeline", "plan",
                 "problem", "problems", "solution", "solutions", "option", "options", "pros", "cons", "quote",
                 "quotes", "tl;dr", "tldr", "re", "fwd", "subject", "budget", "pricing", "price", "cost", "costs",
                 "feedback", "concern", "concerns", "blocker", "blockers", "win", "wins", "learning", "learnings",
                 "metric", "metrics", "kpi", "kpis", "ask", "asks", "objection", "objections", "competitor",
                 "competitors", "attendees", "recap", "key points", "highlights", "lowlights", "open questions",
                 "deliverables", "scope", "requirements", "pain points", "pain point", "champion", "decision maker",
                 "warning", "caveat", "edit", "edited", "nb", "n.b", "update", "tbd", "eta", "url", "link", "links",
                 "email", "phone", "address", "location", "where", "when", "who", "what", "how", "attendee",
                 "participants", "speakers", "transcript", "source", "sources", "verdict", "score", "rating",
                 "recommendation", "recommendations", "assessment", "strengths", "weaknesses", "evidence"}
_NOT_SPEAKER_RE = re.compile(r"(?:q[1-4]|h[12]|fy ?[0-9]{2,4}|v[0-9]+|[0-9].*)", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"^[\[(]?[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?[\])]?\s+")
_TERMINATOR_RE = re.compile(r"[.!?][\"')\]]*\s")


def _is_word(token):
    return bool(token) and token[0].isalpha() and all(ch.isalnum() or ch in "'-.&" for ch in token)


def _is_title(token):
    """Mid-line name token: capital first letter, then at least one lower-case letter (or a single letter)."""
    return (_is_word(token) and token[0].isupper()
            and (len(token) == 1 or any(ch.islower() for ch in token[1:])))


def _label_ok(text):
    key = text.casefold()
    return key not in _NOT_SPEAKERS and not _NOT_SPEAKER_RE.fullmatch(key)


# A negator that is also a plausible surname may end a two-word line-start name ("Nikita Never:"); these may not.
_NEVER_SURNAMES = {"no", "not", "nobody", "none", "noone", "nothing", "neither", "nor", "nowhere", "nope", "nah", "nary",
                   "nada", "zilch", "cannot", "without", "except", "unless", "if", "whether", "non", "un"}


def _surname(token):
    return token.isalpha() and token[0].isupper() and token[1:].islower() and token.casefold() not in _NEVER_SURNAMES


def _boundary_label(prefix):
    """Whole text between a boundary (line start, sentence end, previous label) and the colon: is it a label?"""
    if not prefix or len(prefix) > 60:
        return False
    if _FIXED_ANY_CASE.fullmatch(prefix) or _FIXED_LINE_START.fullmatch(prefix):
        return True
    tokens = prefix.split(" ")
    names = [t for t in tokens if t.casefold() not in _CONNECTORS]
    if not 1 <= len(names) <= 3 or len(tokens) > 5 or not all(_is_word(t) for t in tokens):
        return False
    if not tokens[0][0].isupper() or tokens[0].casefold() in _CONNECTORS or tokens[0].casefold() in _FUNCTION_FIRST:
        return False
    if "'" in tokens[0] or any(not (t[0].isupper() or t.casefold() in _CONNECTORS or t.casefold() in _ROLE_WORDS)
                               for t in tokens[1:]):
        return False  # "We're changing it:" is not a label; "Ops lead:" and "Head of Logistics:" are
    negated = [i for i, t in enumerate(tokens) if _negates(t)]
    if negated and not (negated == [1] and len(tokens) == 2 and _surname(tokens[1])):
        return False
    return _label_ok(prefix)


def _midline_label(tokens):
    """Number of trailing tokens that form a mid-line label (0 if none)."""
    for n in (3, 2, 1):
        if len(tokens) >= n:
            cand = " ".join(tokens[-n:])
            if _FIXED_ANY_CASE.fullmatch(cand) or (n == 1 and _FIXED_TITLE.fullmatch(cand)):
                return n
    n = names = 0
    for tok in reversed(tokens):
        if _is_title(tok) and names < 3:
            n += 1
            names += 1
        elif tok.casefold() in _CONNECTORS and n:
            n += 1
        else:
            break
    while n and (tokens[-n].casefold() in _CONNECTORS or tokens[-n].casefold() in _FUNCTION_FIRST):
        n -= 1
    if not n:
        return 0
    label = tokens[-n:]
    if any(_negates(t) for t in label) or not _label_ok(" ".join(label)):
        return 0
    return n


# --------------------------------------------------------------------------- negation scope (one invariant)
#
# Every label path (line start, after a sentence end, after an abbreviation, mid-line, after a wrapped line) goes
# through _scope_clean, which enforces two rules:
#  1. Negation scope. A label is not a clean turn boundary while a negation, quantifier or condition word is in
#     scope. The scope is the whole turn so far: from the turn's real start (the previous accepted label, the start
#     of the line, or an earlier unfinished line, see _units) up to the label. Only a clear sentence end right before
#     the label (optionally followed by abbreviation tokens such as "Dr.") closes it early (_end_tier tier 2: ! ?, or
#     a full stop after a negator, a number or an ordinary lower-case word). Every other full stop is ambiguous
#     (tier 1), since the word before it may be an abbreviation nobody listed, and closes only a scope that holds no
#     negation proper: any capitalised word ("Pvt.", "Pharma.", "SVP.", "Ambass."), the next word of an abbreviation
#     run ("asst. mgr."), a lower-case word with no vowel ("mgr."), and a short lower-case word in the phrase after
#     "but / except / even / than / from" ("except our dir."). "Not sure. Dana: we renew" is a clean turn; "Not even
#     Dr. Okafor: we renew", "Nobody except the SVP. Okafor: we renew" and "Not even our asst. mgr. Okafor: we
#     renew" are not. On that ambiguous path a named label is also refused right after a short title-case word ending
#     in a consonant that follows "but / except / than / short of"... ("Anything but Dir. Okafor:"): it may be an
#     honorific the name completes.
#  2. Sentence start (fail-closed, independent of any word list). A named label that does not begin its turn must
#     begin a sentence. "Dana: will you renew Riley: no" and "Dana: Far from Team Atlas: we renew" have no sentence
#     end before the label, so neither "Riley:" nor "Team Atlas:" starts a turn there. Granola's own transcript
#     labels (Speaker 2, P3, Me, Them, Q, A) are exempt from this rule only at the start of a line (or right after
#     another label): mid-line, "We dropped Plan A: we renew" is one turn. They are never read as a surname, so an
#     ambiguous full stop before them closes the scope.
# _sentences applies rule 1's notion of an ambiguous end too: it does not split at a full stop after a capitalised
# word that follows another capitalised word while a negator is in the sentence so far, so "Nobody at Acme Pharma.
# We renew" stays one sentence.

_TOKEN_RE = re.compile(r"\S+")
_END_CHARS = set(".!?\"')]")
# Words a clause cannot end on: an unfinished line ending in one of them continues onto the next line.
_DANGLING = {"of", "the", "and", "&", "at", "from", "on", "in", "to", "with", "by", "for", "about", "as", "than",
             "like", "even", "but", "or", "nor", "a", "an", "this", "that", "these", "those", "our", "my", "your",
             "their", "his", "her", "its", "into", "onto", "over", "under", "between", "among", "including", "via",
             "per", "plus", "toward", "towards", "regarding", "re", "vs", "versus", "said", "says", "told", "tells",
             "heard", "hear", "asked", "according", "nobody", "noone", "none", "nothing", "neither", "every", "all",
             "any", "without", "except", "unless", "if", "whether", "barring", "absent", "sans", "lest"}
# Words that leave a negated clause open ("Nobody signed off except maybe", "besides", "save", "bar", "let alone") but
# that ordinary clauses also end on ("at the bar", "do it maybe"): the line carries only while a negator from its last
# turn is in scope before the word.
_DANGLING_IF_NEGATED = {"maybe", "possibly", "perhaps", "besides", "save", "bar", "alone"}


_VOWELS = set("aeiouy")
# The particles of "nobody but / except / not even / other than / heard from <someone>": they introduce the person a
# negation is about, so a short lower-case word in the noun phrase after one of them may be an abbreviated title that
# the following name completes ("except our dir. Okafor", "not even the eng. Okafor").
_EXCEPTIVES = {"but", "except", "even", "besides", "save", "bar", "including", "excluding", "without", "barring",
               "sans", "than", "from"}
# Short words that do end such a phrase: pronouns, demonstratives, adverbs and auxiliaries ("but me.", "from them.",
# "but here it is.").
_CLAUSE_FINAL = {"it", "me", "us", "him", "her", "them", "you", "one", "ones", "this", "that", "mine", "ours", "his",
                 "hers", "its", "all", "both", "each", "any", "some", "more", "most", "less", "much", "many", "few",
                 "too", "yet", "now", "then", "here", "back", "away", "out", "up", "down", "off", "on", "in", "over",
                 "so", "else", "once", "ago", "well", "soon", "late", "ever", "also", "own", "who", "what", "is", "was",
                 "are", "were", "be", "been", "am", "do", "does", "did", "has", "have", "had", "can", "could", "will",
                 "would", "shall", "should", "may", "might", "must"}


def _end_tier(token, line, end):
    """How firmly this whitespace-delimited token (ending at `end` in `line`) ends a sentence: 0 not at all (no
    terminator, an ellipsis, or a full stop after a known abbreviation or initial, the test _sentences uses);
    1 a full stop that is not clearly a sentence end, because the word before it may be an abbreviation nobody
    listed: any capitalised word ("Dr.", "Lieut.", "SVP.", "Ambass.", "Pharma.", but also "Dana." or "Friday."); the
    next word of an abbreviation run ("asst. mgr.", "acct. dir."); a lower-case word with no vowel ("mgr.", "mktg.");
    or a lower-case word of at most five letters at most three plain words after "but / except / even / than / from"
    ("except our dir.", "not even the eng.", "heard from the new coord.") unless it is a pronoun or adverb ("but
    me.", "from them.", "but here it is.");
    2 a clear sentence end: !, ?, a full stop after a negator ("No.", "Nope.", "Didnt."), after a number, or after
    any other lower-case word ("Not sure.", "Nobody agreed.", "the pilot.", "doubted it.")."""
    core = token.rstrip(_CLOSERS)
    if not core or core[-1] not in ".!?":
        return 0
    if core[-1] in "!?":
        return 2
    if core.endswith("..") or _abbreviation_before(line, end - (len(token) - len(core)) - 1):
        return 0
    word = core[:-1].lstrip("\"'([")
    if not word[:1].isalpha() or _negates(word):
        return 2
    prev = _previous_token(line, end - len(token))
    if word[:1].isupper() or prev.rstrip(_CLOSERS)[-1:] == "." and not prev.rstrip(_CLOSERS).endswith(".."):
        return 1
    key = word.casefold()
    if not _VOWELS.intersection(key):
        return 1
    if len(key.rsplit("-", 1)[-1]) <= 5 and key not in _CLAUSE_FINAL and (
            _after_exceptive(line, end - len(token)) or _after_determiner(line, end - len(token))):
        return 1
    return 2


def _after_exceptive(line, token_start):
    """True if one of the three plain words before the token starting at `token_start` is in _EXCEPTIVES. Abbreviation
    tokens in between ("except our asst. dir. of eng.") don't count and don't end the phrase."""
    plain, following = 0, line[token_start:token_start + 1]
    for word in reversed(line[max(0, token_start - 120):token_start].split()[-9:]):
        if word.casefold() in _EXCEPTIVES:
            return True
        if word[-1:] == "." and word[:-1].isalpha() and following.islower():
            following = word[:1]
            continue  # an abbreviation inside the phrase: it is followed by a lower-case word
        following = word[:1]
        word = word.casefold()
        plain += 1
        if plain == 3 or not word.isalpha():
            return False  # three plain words, or punctuation, end the phrase
    return False


def _after_determiner(line, token_start):
    """A determiner-led noun phrase can end in an unlisted abbreviated role.
    Its period alone is insufficient to erase an earlier negation. No vocabulary
    of job titles is needed; hyphenated titles use their final segment.
    """
    determiners = {"the", "our", "their", "a", "an", "my", "your", "his", "her", "its"}
    for token in reversed(line[max(0, token_start - 120):token_start].split()[-4:]):
        key = token.casefold().lstrip("\"'([")
        if key in determiners:
            return True
        if not key.replace("-", "").isalpha():
            break
    return False


def _previous_token(line, token_start):
    before = line[:token_start].rstrip()
    return before[before.rfind(" ") + 1:]


def _after_capitalised(line, token_start):
    """True if the token before the one starting at `token_start` is capitalised ("Acme" in "Acme Pharma.")."""
    return _previous_token(line, token_start).lstrip("\"'([")[:1].isupper()


def _dangling(token):
    if token[-1:] in ",;:-/&(+" or token.endswith(("'s", "s'", "\u2019s")):
        return True
    word = token.replace("\u2019", "'").strip("\"'()[]{}").casefold()
    return word in _DANGLING or any(word.endswith(s) and word[:-len(s)] in _CLITIC_HOSTS - {"never", "nowhere"}
                                    for s in ("s", "ll", "d", "re", "ve"))  # "nobodys", "nobody'll"


@functools.lru_cache(maxsize=64)
def _scope_table(line):
    """Per whitespace token of one cleaned line: start offsets and the prefix counts _scope_clean uses."""
    starts, negc, corec, badc, tiers, toks = [], [0], [0], [0], [], []
    last = {1: [-1], 2: [-1]}
    for m in _TOKEN_RE.finditer(line):
        tok = m.group()
        kind = _negation_kind(tok)
        neg = kind > 0
        tier = _end_tier(tok, line, m.end()) if tok[-1] in _END_CHARS else 0
        if toks and tiers[-1] and toks[-1].rstrip(_CLOSERS).endswith(".") and tok.lstrip("\"'([")[:1].islower():
            tiers[-1] = 0  # a full stop before a lower-case word ends no sentence ("asst. mgr."), as in _sentences
        toks.append(tok)
        starts.append(m.start())
        tiers.append(tier)
        negc.append(negc[-1] + neg)
        corec.append(corec[-1] + (kind == 2))
        badc.append(badc[-1] + (neg or not tok.rstrip(_CLOSERS).endswith(".")))
    for i, tier in enumerate(tiers):
        for level in (1, 2):
            last[level].append(i if tier >= level else last[level][-1])
    return starts, negc, badc, corec, last, toks, tiers


# "short of", "instead of", "except for"...: the second word introduces the person like an _EXCEPTIVES word does.
_EXCEPTIVE_PAIRS = {"short", "instead", "except", "save", "but", "outside"}


def _honorific_shaped(toks, i, a):
    """True if token i (an ambiguous full stop) may be an honorific that the following name completes: a title-case
    word of at most five letters ending in a consonant ("Dir.", "Cllr.") right after a word that introduces the person
    a negation is about ("Anything but Cllr. Okafor:", "Rather than Dir. Okafor:", "Short of Dir. Okafor:"). Not after
    "from", which ordinary sentences end on ("Notes from Sam. Dana:"), nor after any other word ("We met with Sam.
    Dana:", "Thanks, Raj. Okafor:", "Acme Svcs. Ops:"), and not "Dana." (vowel)."""
    word = toks[i].rstrip(_CLOSERS)[:-1].lstrip("\"'([")
    if (not (word.isalpha() and len(word) <= 5 and word[:1].isupper() and word[1:].islower()) or i <= a
            or word[-1] in _VOWELS):
        return False  # truncated titles end in a consonant: "Dir.", "Cllr.", "Supt.", not "Dana."
    prev = toks[i - 1].lstrip("\"'([").casefold()
    if prev in _EXCEPTIVES and prev != "from":
        return True
    return prev in ("of", "for") and i - 1 > a and toks[i - 2].lstrip("\"'([").casefold() in _EXCEPTIVE_PAIRS


def _scope_clean(line, turn_start, start, carry, machine=False, fixed=False):
    """The two invariants above for a label starting at `start`. `carry`: the turn began on an earlier, unfinished
    line (see _open_negation). `fixed`: a Granola transcript label (Speaker 2, P3, Me, Q), never a surname and never
    the object of a negation, so any full stop that is not a known abbreviation closes the scope before it.
    `machine`: such a label at the start of a line or right after another label; only there is it exempt from rule 2,
    so mid-line it must begin a sentence like a name ("We dropped Plan A: we renew" is one turn)."""
    starts, negc, badc, corec, last, toks, tiers = _scope_table(line)
    a = bisect.bisect_left(starts, turn_start)
    b = bisect.bisect_left(starts, start)
    ls = last[2][b]
    if ls >= a and badc[b] == badc[ls + 1]:
        return True  # right after a definite sentence end (with only "Dr."-style tokens in between)
    lw = last[1][b]
    if (lw >= a and badc[b] == badc[lw + 1] and (fixed or corec[lw + 1] == corec[a])
            and (fixed or not _honorific_shaped(toks, lw, a))):
        return True  # after an ambiguous full stop ("Pharma.") that ends no negation proper and is no honorific
    if a == b:
        # the label begins the turn: at the start of a line, or (machine labels only) right after another label
        return machine or (not carry and turn_start == 0)
    if carry or negc[b] != negc[a]:
        return False  # rule 1: a negator is in scope
    return badc[b] == badc[a]  # rule 2: a named label must begin a sentence (only "Dr."-style tokens before it)


def _open_negation(line, labels, carry):
    """True if a label at the start of the next line is not a clean boundary, because this line stops mid-clause:
    (a) its last word is a word a clause can't end on ("at", "from", "the", "with", "nobody"), whatever else the
    line says (fail-closed); (b) it ends on a comma, a possessive or a full stop after a known abbreviation ("Inc.")
    while a negator from its last turn is still in scope, or on a full stop that is not a clear sentence end
    ("Pharma.", "SVP.", "except our dir.", see _end_tier) while a negation proper is; or
    (c) its last word is one of _DANGLING_IF_NEGATED ("except maybe", "besides") with a negator from its last turn in
    scope before it. A clear sentence end closes the scope."""
    if labels and labels[-1][1] >= len(line.rstrip()):
        return False  # the line ends with a label: it hands its speaker to the next line
    tail = line[line.rfind(" ") + 1:]
    tier = _end_tier(tail, line, len(line)) if tail[-1:] in _END_CHARS else 0
    if tier == 2:
        return False  # quick exits before the per-token table: the line ends a sentence ...
    word = tail.replace("\u2019", "'").strip("\"'()[]{}").casefold()
    if tier == 0 and word in _DANGLING:
        return True  # (a)
    soft = tier == 0 and (word in _DANGLING_IF_NEGATED or word.endswith("ly") or word in {"less", "mind", "guess"})
    if tier == 0 and not soft and not tail.rstrip(_CLOSERS).endswith(".") and not _dangling(tail):
        return False  # ... or ends on a word a clause can end on
    starts, negc, _, corec, last = _scope_table(line)[:5]
    if tier == 1:
        negc = corec  # an ambiguous full stop ends no quantifier or condition, as mid-line ("We launch in May.")
    n = len(starts)
    a = bisect.bisect_left(starts, labels[-1][1]) if labels else 0
    if a == n:
        return False
    ls = last[2][n]
    if soft:  # (c): a negator before the last word ("maybe" is itself a weak negator, so it does not count)
        return (carry and not labels and ls < a) or negc[n - 1] > negc[max(a, ls + 1)]
    if ls >= a:
        return negc[n] > negc[ls + 1]
    return (carry and not labels) or negc[n] > negc[a]


def _find_labels(line, carry=False, scoped=True):
    """[(start, end, label)] for every speaker label in one cleaned line. `end` is just after the colon. With
    scoped=False every label-shaped span counts, negation scope or not (used to reject quotes that contain one)."""
    out = []
    prev_end = 0
    pos = line.find(":")
    while pos != -1:
        c = pos
        pos = line.find(":", c + 1)
        if c == 0 or line[c - 1] == " " or (c + 1 < len(line) and line[c + 1] != " ") or c < prev_end:
            continue
        j, role = c, ""
        if line[j - 1] == ")":
            k = line.rfind("(", max(prev_end, j - 42), j - 1)
            inner = line[k + 1:j - 1] if k != -1 else ""
            if k == -1 or not inner.strip() or "(" in inner or ")" in inner:
                continue
            role = line[k:j]
            j = k
            if j > 0 and line[j - 1] == " ":
                j -= 1
        window_start = max(prev_end, j - MAX_LABEL_LOOKBACK)
        window = line[window_start:j]
        if not window or window[-1] == " ":
            continue
        boundary = None
        last = None
        for last in _TERMINATOR_RE.finditer(window):
            pass
        if last is not None:
            boundary = window_start + last.end()
        elif window_start == prev_end:
            boundary = prev_end
        start = None
        if boundary is not None:
            prefix = line[boundary:j].strip()
            if _boundary_label(prefix):
                start = boundary + (len(line[boundary:j]) - len(line[boundary:j].lstrip()))
        if start is None:
            region = line[boundary if boundary is not None else window_start:j]
            tokens = region.split(" ")
            if boundary is None and len(tokens) > 1:
                tokens = tokens[1:]  # the first token may be cut by the window
            tokens = tokens[-7:]
            n = _midline_label(tokens)
            if n:
                start = j - len(" ".join(tokens[-n:]))
        if start is None:
            continue
        label = line[start:j].strip() + ((" " + role) if role and line[j:j + 1] == " " else role)
        name = line[start:j].strip()
        fixed = bool(_FIXED_ANY_CASE.fullmatch(name) or _FIXED_TITLE.fullmatch(name))
        machine = fixed and not line[prev_end:start].strip()  # at the start of the line or right after a label
        if scoped and not _scope_clean(line, prev_end, start, carry and not out, machine, fixed):
            continue
        if not scoped and not fixed:
            kinds = [_negation_kind(t) for t in line[prev_end:start].split()]
            if 1 in kinds and 2 not in kinds:
                continue  # as in 1.2.0: a name-shaped span after "in", "every"... ("Due in March:") is not a label
        out.append((start, c + 1, label))
        prev_end = c + 1
    return out


# --------------------------------------------------------------------------- sentences and units

_ABBREVIATIONS = {"mr", "mrs", "ms", "mx", "dr", "prof", "sr", "jr", "st", "vs", "v", "etc", "e.g", "i.e", "eg", "ie",
                  "cf", "al", "approx", "inc", "ltd", "co", "corp", "dept", "est", "fig", "vol", "p",
                  "pp", "ca", "u.s", "u.k", "a.m", "p.m", "am", "pm", "jan", "feb", "mar", "apr", "jun", "jul",
                  "aug", "sep", "sept", "oct", "nov", "dec", "mon", "tue", "tues", "wed", "thu", "thur", "thurs",
                  "fri", "sat", "sun", "mt", "ft", "min", "max", "avg", "govt", "gen", "col", "lt", "sgt", "capt",
                  "rev", "hon", "esp", "incl", "excl", "dept", "div", "ext", "tel", "attn", "re", "ref", "nr",
                  "llc", "llp", "gmbh", "plc", "ag", "pty", "bv", "srl", "sa", "pte", "ab", "oy", "sas", "bhd", "nv", "kk",
                  "op", "ed", "eds", "trans", "misc", "sq", "ave", "blvd", "rd", "ste", "apt", "bldg", "univ",
                  "pvt", "bros", "intl", "ltda", "pllc", "sarl", "oyj", "kft", "tbk", "sdn", "hldgs", "grp", "assn",
                  "assoc", "mfg", "natl", "mgmt", "cos", "sen", "supt", "insp", "adm", "atty", "cmdr", "msgr", "lda",
                  "sae", "zrt", "sro", "ehf", "messrs", "lieut"}
# Abbreviations that are also ordinary words: only in this exact case ("Acme AS.", "Sen. Ruiz", but "as." stays a word).
_CASED_ABBREVIATIONS = {"US", "AS", "KG", "LP", "SpA", "ApS", "Rep", "Gov", "Pres", "Fr", "Det", "Maj", "Sec", "Ald",
                        "Br", "Sis", "Hosp", "SE", "AB", "NV"}
_CLOSERS = "\"')]"


def _token_start(text, end):
    start = end
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return start


def _abbreviation_before(text, dot):
    start = _token_start(text, dot)
    raw_token = text[start:dot].lstrip("\"'([")
    token = raw_token.casefold()
    if not token:
        return False
    if token in ("no", "nos"):
        return text[dot + 1:dot + 3].lstrip()[:1].isdigit()  # "No. 5" is an abbreviation; "No. We will" is a reply
    if raw_token in _CASED_ABBREVIATIONS or token in _ABBREVIATIONS or (len(token) == 1 and token.isalpha()):
        return True
    return bool(re.fullmatch(r"(?:[^\W\d_]\.)+[^\W\d_]", token))


def _sentences(text):
    """Split one turn into sentences at . ! ? followed by space and a character that is not a lower-case letter.
    Never inside numbers (3.5), after known abbreviations or initials, or at ellipses (.. ...), and not at a full
    stop after a capitalised word that follows another capitalised word (a possible unlisted company suffix:
    "Acme Pharma.", "Acme SARL.") while a negation proper is in the sentence so far, so "Nobody at Acme Pharma. We
    renew" stays one sentence."""
    out, start, i, n = [], 0, 0, len(text)
    negs = None
    while i < n:
        ch = text[i]
        if ch not in ".!?":
            i += 1
            continue
        k = i
        while k < n and text[k] in ".!?":
            k += 1
        run = text[i:k]
        end = k
        while end < n and text[end] in _CLOSERS:
            end += 1
        if end < n and not text[end].isspace():
            i = k
            continue
        if run.count(".") >= 2 and "?" not in run and "!" not in run:
            i = k
            continue
        if run == "." and _abbreviation_before(text, i):
            i = k
            continue
        if (run == "." and _end_tier(text[_token_start(text, i):end], text, end) == 1
                and _after_capitalised(text, _token_start(text, i))):
            if negs is None:
                negs = [m.start() for m in _TOKEN_RE.finditer(text) if _negation_kind(m.group()) == 2]
            pos = bisect.bisect_left(negs, start)
            if pos < len(negs) and negs[pos] < i:
                i = k
                continue
        nxt = end
        while nxt < n and text[nxt].isspace():
            nxt += 1
        if nxt < n and text[nxt].islower():
            i = k
            continue
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        start = end
        i = end
    piece = text[start:].strip()
    if piece:
        out.append(piece)
    return out


@functools.lru_cache(maxsize=16)
def _units(source_text):
    lines = []  # [text, is_bullet, ends_with_label, carry]
    gap, open_neg = True, False
    for raw in _prep(source_text).split("\n"):
        stripped = raw.strip()
        raw = _TIMESTAMP_RE.sub("", stripped)
        timestamped = len(raw) != len(stripped)
        # Blockquote markers are presentation, not independent evidence boundaries.
        raw = re.sub(r"^(?:>\s*)+", "", raw)
        text, bullet = _clean_line(raw)
        if not text:
            gap = True
            continue
        # An unfinished line whose last clause holds a negator keeps that scope open into this line ("Nobody at\n
        # Team Atlas: we renew"), unless this line clearly starts a new turn: after a blank line or heading, or
        # with a bullet or a timestamp. (A Granola transcript label such as "Speaker 2:" also starts a new turn.)
        carry = open_neg and not gap and not bullet and not timestamped and lines[-1][1] != "heading"
        found = _find_labels(text, carry)
        starts_label = bool(found) and found[0][0] == 0
        joined = (lines and (not gap or text[:1].islower()) and not bullet and not starts_label
                  and lines[-1][1] != "heading" and lines[-1][0][-1:] not in (".", "!", "?", '"', "'")
                  and not lines[-1][2])
        if joined:
            # A line after an unfinished line continues it (markdown's lazy continuation): "Nobody\nWill renew"
            # is one sentence, so "Will renew" can't be quoted on its own.
            lines[-1][0] += " " + text
        else:
            lines.append([text, bullet, False, carry])
        # a line that ends in a speaker label ("Speaker 1:") hands its speaker to the next line instead
        lines[-1][2] = bool(found) and not text[found[-1][1]:].strip()
        open_neg = bullet != "heading" and _open_negation(text, found, open_neg if joined else carry)
        gap = False
    units, pending = [], None
    for text, _bullet, _ends_label, carry in lines:
        labels = _find_labels(text, carry)
        segments = []
        pos, speaker = 0, None
        if pending and not (labels and labels[0][0] == 0):
            speaker = pending
        pending = None
        for start, end, label in labels:
            segments.append((speaker, text[pos:start]))
            pos, speaker = end, label
        segments.append((speaker, text[pos:]))
        for spk, seg in segments:
            seg = seg.strip()
            if seg:
                units.append((len(units), spk, _sentences(seg)))
        if labels and not text[labels[-1][1]:].strip():
            pending = labels[-1][2]
    return tuple((i, s, tuple(sents)) for i, s, sents in units)


def split_units(source_text):
    """[(turn_index, speaker_label_or_None, [sentences])]. A turn starts at every line (a lower-case line that
    continues an unfinished line is joined to it) and at every speaker label, at the start of a line or mid-line.
    Sentences are normalised text (case kept)."""
    if not isinstance(source_text, str):
        raise TypeError("split_units() needs a str")
    return [(i, s, list(sents)) for i, s, sents in _units(source_text)]


# --------------------------------------------------------------------------- the whole-sentence quote check

_ELLIPSIS_RE = re.compile(r"\.\s?\.|\u2026|\u22ef|\[\s*\.\s*\.|\(\s*\.\s*\.")


def _term_split(text):
    """(body, final run of . ! ?). String methods, not a regex: a `[.!?]+$` search is quadratic on long dot runs."""
    body = text.rstrip(".!?")
    return body.rstrip(), text[len(body):]


@functools.lru_cache(maxsize=16)
def _match_index(source_text):
    """Per turn: (casefolded sentences joined by one space, sentence start offsets, {body end offset: final
    punctuation}). A quote matches when it starts at a sentence start and its body ends where a sentence body ends."""
    out = []
    for _, _, sents in _units(source_text):
        keys = [x.casefold() for x in sents]
        starts, ends, pos = [], {}, 0
        for key in keys:
            starts.append(pos)
            body, term = _term_split(key)
            ends.setdefault(pos + len(body), term)
            pos += len(key) + 1
        out.append((" ".join(keys), tuple(starts), ends))
    return tuple(out)


def check_quote(quote, source_text):
    """(ok, reason). ok only if, after normalise() on both sides, the quote equals one sentence or a run of
    consecutive whole sentences of one speaker turn (case-insensitive; a final . or ! may be dropped or swapped, a
    final ? may not). Rejects ellipses, speaker labels, quotes under 3 words, partial sentences and quotes that span
    two turns."""
    if not isinstance(quote, str) or not isinstance(source_text, str):
        return False, REASONS["not_text"]
    prepared = _prep(quote)
    if _ELLIPSIS_RE.search(prepared):
        return False, REASONS["ellipsis"]
    q = normalise(quote)
    if len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'":
        q = q[1:-1].strip()
    if not q:
        return False, REASONS["empty"]
    if sum(1 for w in q.split() if any(ch.isalnum() for ch in w)) < MIN_QUOTE_WORDS:
        return False, REASONS["short"]
    if _find_labels(q, scoped=False):
        return False, REASONS["label"]
    qk = q.casefold()
    q_body, q_term = _term_split(qk)
    if not q_body:
        return False, REASONS["empty"]
    question_only = False
    for text, starts, ends in _match_index(source_text):
        for s in starts:
            if not text.startswith(q_body, s):
                continue
            term = ends.get(s + len(q_body))
            if term is None:
                continue
            if ("?" in term) == ("?" in q_term):
                return True, REASONS["ok"]
            question_only = True
    if question_only:
        return False, REASONS["question"]
    texts = [text for text, _, _ in _match_index(source_text)]
    if any(q_body in t for t in texts):
        return False, REASONS["partial"]
    for a, b in zip(texts, texts[1:]):
        if q_body in a + " " + b:
            return False, REASONS["spans"]
    return False, REASONS["missing"]


_RECORDING_LABEL = re.compile(r"(?:microphone|me|system audio|them)(?: ?\([^()]*\))?", re.I)
_RECORDING_OWNER = re.compile(r"(?:microphone|me)(?: ?\([^()]*\))?", re.I)


def quote_is_seller_only(quote_text, source_text):
    """In recorder-labelled text, prose labels cannot change the recording side.
    This prevents a seller's 'Look:' continuation from being relabelled customer.
    Manual name-only turns following recorder labels remain ambiguous and retain
    the recorder side; use the actual recorder label from the source.
    """
    units = split_units(source_text)
    recorder_mode = any(label is not None and _RECORDING_LABEL.fullmatch(label) for _, label, _ in units)
    labels, speaker = [], None
    for _, label, sentences in units:
        if label is not None and (not recorder_mode or _RECORDING_LABEL.fullmatch(label)):
            speaker = label
        if sentences and check_quote(quote_text, " ".join(sentences))[0]:
            labels.append(speaker)
    return bool(labels) and all(s is not None and _RECORDING_OWNER.fullmatch(s) for s in labels)


_QUESTION_START = ("if ", "unless ", "assuming ", "suppose ", "supposing ", "provided ", "in case ", "what if ",
                   "only if ", "as long as ", "whether ", "will we ", "will you ", "will they ", "will it ",
                   "would ", "could ", "should ", "might ", "may we ", "may i ", "can we ", "can you ", "can i ",
                   "do we ", "do you ", "do they ", "does ", "did ", "is it ", "is there ", "are we ", "are you ",
                   "are they ", "shall ", "hypothetically", "in theory", "were we ", "were they ", "were you ",
                   "were it ", "were i ", "had we ", "had they ", "had you ", "had i ", "should ")
_CONDITIONAL_RE = re.compile(r"\b(?:if|unless|assuming|provided that|as long as|in case|supposing|whether|"
                             r"hypothetically|in theory|on condition that|only when|in the event)\b")


_PROVIDED_SUBJECTS = {"we", "they", "i", "you", "he", "she", "it", "was", "were", "is", "are", "has", "have", "had",
                      "be", "been", "being", "already", "and", "just", "who", "that", "which", "then", "also",
                      "kindly", "has", "never", "not"}


def is_question_or_conditional(sentence):
    """Heuristic: True for questions (ends with ?, or starts like one) and conditionals (if, unless, assuming,
    as long as, ... anywhere). For callers that accept statements only."""
    if not isinstance(sentence, str):
        return False
    s = normalise(sentence).casefold().strip(" \"'")
    if not s:
        return False
    if s.rstrip(" \"')").endswith("?"):
        return True
    if s.startswith(_QUESTION_START) or s in ("if", "unless"):
        return True
    if _CONDITIONAL_RE.search(s):
        return True
    for m in re.finditer(r"\bprovided\b", s):
        before = s[:m.start()].split()
        if not before or before[-1] not in _PROVIDED_SUBJECTS:
            return True  # "renew provided SSO ships", not "we provided the docs"
    return False


# --------------------------------------------------------------------------- contact redaction

_ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_DASH_LIKE = set("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d")
_BRACKET_WORDS = {"at": "@", "dot": "."}
_EMAIL_RE = re.compile(r"(?:\"[^\"\n]{1,64}\"|[\w!#$%&'*+/=?^`{|}~.-]{1,64}) ?@ ?"
                       r"(?:[\w-]{1,63}(?: *\. *[\w-]{1,63}){0,10} *\. *[^\W\d_]{2,63}(?![\w-])|\[[0-9]{1,3}(?:\.[0-9]{1,3}){3}\])")
_SPELLED_EMAIL_RE = re.compile(r"[\w.+-]{1,64} at [\w-]{1,63}(?: dot [\w-]{1,63}){0,5} dot [^\W\d_]{2,24}\b",
                               re.IGNORECASE)
_LOCAL_CHARS = set("!#$%&'*+/=?^`{|}~.-")
_US_RES = [re.compile(r"\(([2-9][0-9]{2})\)[ .-]?[0-9]{3}[ .-][0-9]{4}"),
           re.compile(r"(?:1([.-]))?([2-9][0-9]{2})([.-])[0-9]{3}\3[0-9]{4}"),
           re.compile(r"(?:1 )?[2-9][0-9]{2} [2-9][0-9]{2} [0-9]{4}")]
_UK_RES = [re.compile(r"0[1-9][0-9]{3}[ -][0-9]{6}"), re.compile(r"0[1-9][0-9]{3}([ -])[0-9]{3}\1[0-9]{3}"),
           re.compile(r"0[1-9][0-9]([ -])[0-9]{4}\1[0-9]{4}"), re.compile(r"0[1-9][0-9]{2}([ -])[0-9]{3}\1[0-9]{4}")]
_FR_RE = re.compile(r"0[1-9]([ .-])[0-9]{2}(?:\1[0-9]{2}){3}")
_PLUS_RE = re.compile(r"(?:\(\+ ?[0-9]{1,4}\)[ .-]?[0-9]{1,12}|(?:\+ ?|00(?=[1-9]))[0-9]{1,15})"
                      r"(?:[ .-]?\([0-9]{1,5}\)[ .-]?[0-9]{1,12})?(?:[ .-][0-9]{1,8}){0,7}")
_REFERENCE_WORDS = (r"order|orders|po|p\.o\.|purchase order|invoice|inv|ticket|case|ref|reference|sku|serial|tracking|"
                    r"isbn|issn|zip|postcode|post code|build|version|ver|account|acct|a/c|id|iban|sort code|routing|"
                    r"swift|bic|vat|ein|tax|ssn|policy|claim|contract|license|licence|batch|lot|item|part|model|release|"
                    r"patch|room|flight|seat|seats|tier|tiers|plan|plans|price|prices|budget|quote|quoted|rev|revenue|"
                    r"arr|mrr|nps|eta")
# Words that, between a possessive and "number", make it an identifier rather than a phone: "her case number".
_ID_WORDS = (_REFERENCE_WORDS + r"|customer|member|membership|employee|staff|badge|registration|company|vendor|"
             r"supplier|confirmation|booking|reservation|record|file|loyalty|project|job|task|issue|bug|pr|jira|deal|"
             r"opportunity|lead|lucky|favou?rite|jersey|shoe|house|street|unit|suite|apartment|apt|floor|gate|"
             r"platform|bus|route|table|page|slide|row|sequence|check|cheque|card|pin|passport|social|insurance|"
             r"student|patient|tracking|shipment|parcel|package|container|reg")
_CUE_NAME = (r"(?:(?-i:Dr|Mr|Mrs|Ms|Mx|Prof)\.? )?(?-i:[A-Z][a-z'-]+)(?: (?-i:[A-Z][a-z'-]+))?")  # "Priya Shah", "Dr. Okafor"
_CUE_KIND = r"(?:cell|mobile|mob|work|home|office|direct|personal|landline|whats ?app)"  # "Number (cell):"
_CUE_LINE_START = r"(?m:^[ \t]*(?:(?:[-*+•]|[0-9]{1,3}[.)]|>+)[ \t]+)?)"  # optionally after a bullet, "1." or ">"
_CUE_RE = re.compile(
    r"(?:[☎☏✆\U0001f4de\U0001f4f1\U0001f4f2\U0001f4e0]|(?<!\w)" + _CUE_NAME + r" *\((?:cell|mobile|mob|m|c)\)|"
    r"(?<!\w)(?:my|his|her|their|our|your) +#(?= *(?:is\b|:))|(?<!\w)(?:"
    # a contact verb with a person: call Dana on, reach Priya at, text Priya Shah, ring him
    r"(?:call(?:ing)?|reach(?: out to)?|text(?:ing)?|txt|ring|phone|contact|whats ?app|message|sms|dial|try|ping)"
    r" +(?:me|him|her|us|them|" + _CUE_NAME + r")(?= *(?:(?:at|on|via)\b|[+(0-9]))|"
    r"hit (?:me|him|her|us|them) up(?= *(?:at|on|via)\b)|(?:can|could|may) be reached|"
    r"(?:best|direct|work|home|personal|mobile|cell) (?:number|no\.?|line) for (?:me|him|her|us|them|" + _CUE_NAME
    + r")(?= *(?::|-|is\b|,))|"
    r"call(?:ing)?|phone(?: number| no\.?)?|ph|tel(?:ephone)?|mobile(?: number)?|mob|cell(?: ?phone)?"
    r"(?: number)?|fax|whats ?app|telegram|imessage|sms|text|txt|dial|landline|direct line|direct dial|hotline|"
    r"contact number|"
    r"(?:office|desk|work|home|main|direct|after-hours) +(?:line|phone|number|no\.?|cell|mobile|landline)|"
    # a possessive and at most one other word: my number, Dana's new number, his office number, her digits
    r"(?:(?:my|his|her|their|our|your|[a-z][a-z'-]*'s) +(?:(?!(?:" + _ID_WORDS + r")\b)[a-z][a-z-]* +)?|"
    r"(?:personal|direct|best|work|home) +|" + _CUE_LINE_START + r")(?:number|digits|line)"
    r"(?= *(?:is\b|was\b|are\b|'s|:|-|,|\())|"
    r"number(?= *\(" + _CUE_KIND + r"\))|"
    + _CUE_LINE_START + r"(?:[mtpcwho]|office|desk|home|work|cell|mobile|direct|contact|signal)(?= *:)|"
    r"reach (?:me|him|her|us|them)|contact (?:me|him|her|us|them))\b)"
    r"(?: (?:me|him|her|us|them|back|directly))?(?: (?:at|on|via))?(?: (?:number|no\.?|nr|#))?(?: (?:is|was|are))?"
    r"(?:\'s)?(?: *\(" + _CUE_KIND + r"\))?[ \t]*[:#.=,-]?\s*", re.IGNORECASE)
_CUE_CAND_RE = re.compile(r"\+? ?(?:\([0-9]{1,5}\)[ .-]?)?[0-9]{1,15}(?:[ .-][0-9]{1,8}){0,7}")
_REFERENCE_BEFORE_RE = re.compile(
    r"(?:\b(?:" + _REFERENCE_WORDS + r")s?\s*"
    r"(?:#|no\.?|nr\.?|number|num|:|=)?\s*|[#$€£¥₹%]\s*)$", re.IGNORECASE)
_NUMERIC_DATE_RE = re.compile(r"(?:[0-9]{4}[-./][0-9]{1,2}[-./][0-9]{1,2}|[0-9]{1,2}[-./][0-9]{1,2}[-./][0-9]{2,4})$")
_GROUP_RE = re.compile(r"[0-9]+")


def _raw_view(text):
    """(view, index): a same-order ASCII stand-in for redaction matching. Granola backslash escapes and invisible
    characters are skipped, full-width and other-script digits/letters map to ASCII, dash look-alikes to '-',
    [at]/(at)/{at} to '@' and [dot]/(dot)/{dot} to '.'. index[i] is view[i]'s position in text."""
    chars, index = [], []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in _ASCII_PUNCT:
            i += 1
            continue
        if ch in _INVISIBLE or (ch != "\n" and ch != "\t" and unicodedata.category(ch) in ("Cc", "Cf", "Cs")):
            i += 1
            continue
        if ch == "%" and text[i + 1:i + 3] == "40":
            chars.append("@")
            index.append(i)
            i += 3
            continue
        if ch in "[({" and i + 3 < n:
            close = {"[": "]", "(": ")", "{": "}"}[ch]
            for word, repl in _BRACKET_WORDS.items():
                end = i + 1 + len(word)
                if text[i + 1:end].lower() == word and end < n and text[end] == close:
                    if chars and chars[-1] == " ":
                        chars.pop()
                        index.pop()
                    chars.append(repl)
                    index.append(i)
                    i = end + 1
                    if i < n and text[i] == " ":
                        i += 1
                    break
            else:
                word = None
            if word is not None:
                continue
        if ord(ch) < 128:
            out = ch
        elif ch in _DASH_LIKE:
            out = "-"
        elif ch in "’‘":
            out = "'"
        else:
            d = unicodedata.decimal(ch, None)
            if d is not None:
                out = str(d)
            else:
                folded = unicodedata.normalize("NFKC", ch)
                out = folded if len(folded) == 1 and ord(folded) < 128 else ch
                if ch in "\u3002\uff61":
                    out = "."
        chars.append(out)
        index.append(i)
        i += 1
    return "".join(chars), index


def _view(text):
    # Decode entities only for matching, retaining spans into the exact original.
    if "&" not in text:
        view, positions = _raw_view(text)
        return view, positions, [i + 1 for i in positions]
    chunks, starts, ends = [], [], []
    cursor = 0
    for match in re.finditer(r"&(?:\#[0-9]{1,8}|\#[xX][0-9a-fA-F]{1,8}|[A-Za-z][A-Za-z0-9]{1,31});?", text):
        for i in range(cursor, match.start()):
            chunks.append(text[i]); starts.append(i); ends.append(i + 1)
        decoded = html.unescape(match.group(0))
        for ch in decoded:
            chunks.append(ch); starts.append(match.start()); ends.append(match.end())
        cursor = match.end()
    for i in range(cursor, len(text)):
        chunks.append(text[i]); starts.append(i); ends.append(i + 1)
    view, positions = _raw_view("".join(chunks))
    return view, [starts[i] for i in positions], [ends[i] for i in positions]


def _groups(candidate):
    return _GROUP_RE.findall(candidate.replace("(0)", ""))


def _arithmetic(values):
    if len(values) < 3:
        return False
    step = values[1] - values[0]
    return step != 0 and all(b - a == step for a, b in zip(values, values[1:]))


def _list_like(groups, strict):
    """Number lists that a phone format or cue must not swallow."""
    values = [int(g) for g in groups]
    if len(groups) >= (3 if strict else 4) and _arithmetic(values):
        return True
    if len(groups) >= 2 and all(len(g) == 4 and 1900 <= int(g) <= 2099 for g in groups):
        return True  # years
    rising = all(a < b for a, b in zip(values, values[1:]))
    falling = all(a > b for a, b in zip(values, values[1:]))
    if len(groups) >= 3 and all(g.endswith("00") for g in groups):
        return True  # round amounts
    if strict and (rising or falling) and all(g.endswith("0") for g in groups) and not groups[0].startswith("0"):
        return True  # tiers: 250-500-1000
    return False


def _phone_ok(view, start, end, kind, cue_start=None):
    """cue_start: where the cue word begins; the reference-word test then looks before the cue, so the cue's own
    "#" ("Ph# 4155550199") is not mistaken for a reference marker."""
    cand = view[start:end]
    groups = _groups(cand)
    digits = sum(len(g) for g in groups)
    ref_end = start if cue_start is None else cue_start
    before = view[max(0, ref_end - 40):ref_end]
    if _REFERENCE_BEFORE_RE.search(before) and kind != "plus":
        return False
    if end < len(view) and (view[end].isalnum() or view[end] in "%@") or (
            end + 1 < len(view) and view[end] in ".-/" and view[end + 1].isdigit()):
        return False
    if start > 0 and (view[start - 1].isalnum() or view[start - 1] in "@#$/.-_" or
                      (view[start - 1] == ":" and start > 1 and view[start - 2].isdigit())):
        return False
    if kind == "plus" and start > 0 and view[start - 1] == "+":
        return False
    if _NUMERIC_DATE_RE.match(cand.lstrip("+ ")) or re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}", cand):
        return False
    if kind == "plus":
        return 8 <= digits <= 15 and not _list_like(groups, not cand.lstrip("(").startswith("+"))
    if kind == "cue":
        if not 7 <= digits <= 15 or (len(groups) == 1 and digits < 9):
            return False
        if "." in cand and " " in cand:
            return False  # decimals: 9.99 19.99
        if (len(groups) >= 3 and len(groups[0]) <= 3 and groups[0][0] != "0" and groups[-1] == "000"
                and all(len(g) == 3 for g in groups[1:])):
            return False  # a thousands-grouped amount: "call 1 200 000 000 times"
        if (re.fullmatch(r"[1-9][0-9]? [0-9]{3}(?: [0-9]{3})+", cand)
                and re.match(r" +[^\W\d_]", view[end:end + 3])):
            return False  # a space-grouped count followed by a word: "Mobile: 2 451 380 sessions"
        return not _list_like(groups, False)
    return not _list_like(groups, True)


def _phone_spans(view):
    spans = []
    for regex in _US_RES + _UK_RES + [_FR_RE]:
        for m in regex.finditer(view):
            if _phone_ok(view, m.start(), m.end(), "format"):
                spans.append((m.start(), m.end()))
    for m in _PLUS_RE.finditer(view):
        s, e = m.start(), m.end()
        text = view[s:e]
        # Too many digits: drop trailing groups (a phone followed by an unrelated number).
        while sum(len(g) for g in _groups(text)) > 15 and re.search(r"[ .-][0-9]+$", text):
            text = re.sub(r"[ .-][0-9]+$", "", text)
            e = s + len(text)
        if _phone_ok(view, s, e, "plus"):
            spans.append((s, e))
    for m in _CUE_RE.finditer(view):
        if _REFERENCE_BEFORE_RE.search(view[max(0, m.start() - 40):m.start()]):
            continue
        c = _CUE_CAND_RE.match(view, m.end())
        if c and c.end() > c.start():
            s, e = c.start(), c.end()
            text = view[s:e]
            while sum(len(g) for g in _groups(text)) > 15 and re.search(r"[ .-][0-9]+$", text):
                text = re.sub(r"[ .-][0-9]+$", "", text)
                e = s + len(text)
            kind = "plus" if text.startswith("+") else "cue"
            if _phone_ok(view, s, e, kind, m.start()):
                spans.append((s, e))
    return spans


def _merge(spans):
    out = []
    for s, e, label in sorted(spans, key=lambda x: (x[0], -x[1])):
        if out and s < out[-1][1]:
            if e > out[-1][1] and out[-1][2] == label:
                out[-1] = (out[-1][0], e, label)
            continue
        out.append((s, e, label))
    return out


def redact_contacts(text):
    """Emails -> "[email removed]" (also with Granola escapes, full-width @, zero-width characters, [at]/[dot]);
    phone numbers -> "[phone removed]" only on a clear signal: a leading + (or 00) country code, a phone cue word
    just before (call, phone, tel, mobile, cell, fax, WhatsApp, Telegram, text, reach out to Dana at, can be reached
    at, Dana (cell)...), or a standard US / UK / French grouping with separators. Plain number lists, prices, tiers, ranges, years, dates, times, versions, IPs
    and order/PO/invoice numbers are left alone. Pattern-based: it can miss formats."""
    if not isinstance(text, str):
        raise TypeError("redact_contacts() needs a str")
    if not text:
        return text
    view, index, end_index = _view(text)
    spans = []
    for m in _SPELLED_EMAIL_RE.finditer(view):
        spans.append((m.start(), m.end(), "[email removed]"))
    for m in _EMAIL_RE.finditer(view):
        s = m.start()
        while s > 0 and (view[s - 1].isalnum() or view[s - 1] in _LOCAL_CHARS or view[s - 1] == "_"):
            s -= 1  # a local part longer than 64 characters is removed whole
        spans.append((s, m.end(), "[email removed]"))
    email_spans = [(s, e) for s, e, _ in spans]
    for s, e in _phone_spans(view):
        if not any(s < ee and e > es for es, ee in email_spans):
            spans.append((s, e, "[phone removed]"))
    if not spans:
        return text
    out, last = [], 0
    for s, e, label in _merge(spans):
        start, end = index[s], end_index[e - 1]
        if start > 0 and text[start - 1] == "\\" and (s == 0 or index[s - 1] < start - 1):
            start -= 1  # the Granola escape in front of a removed value goes too
        if start < last:
            continue
        out.append(text[last:start])
        out.append(label)
        last = end
    out.append(text[last:])
    return "".join(out)


_PLACEHOLDER_RE = re.compile(r"\[(?:email|phone) removed\]")


def shorten(text, n):
    """redact_contacts(text), then cut to at most n characters at a word boundary, ending with an ellipsis.
    Redaction runs first, so a cut can never leave part of an address or number behind."""
    if not isinstance(text, str):
        raise TypeError("shorten() needs a str")
    text = redact_contacts(text)
    if n <= 0:
        return ""
    if len(text) <= n:
        return text
    if n == 1:
        return "\u2026"
    cut = text[:n - 1]
    for m in _PLACEHOLDER_RE.finditer(text):
        if m.start() < len(cut) < m.end():
            cut = cut[:m.start()]
            break
    space = cut.rfind(" ")
    if space > len(cut) // 2:
        cut = cut[:space]
    return cut.rstrip() + "\u2026"


# --------------------------------------------------------------------------- escaping

_MD_SPECIAL_RE = re.compile(r"([\\`*_~\[\]()|#@!$=^:])")
_MD_DOT_RE = re.compile(r"(?<=\S)\.(?=[\w-])")
_DOMAIN_DOT_RE = re.compile(r"(?<=[\w-])\.(?=[^\W\d_])|(?<=www)\.", re.IGNORECASE)
_IPV4_RE = re.compile(r"(?<![0-9.])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9])")
_SLACK_LOOKALIKES = {"*": "\u2217", "_": "\u02cd", "~": "\u223c", "`": "\u02cb", "@": "\uff20", "[": "\uff3b",
                     "]": "\uff3d"}


def _one_line(text):
    text = _NEWLINE_RE.sub("\n", text)
    kept = "".join(" " if ch.isspace() else ch for ch in text
                   if ch.isspace() or not (ch in _INVISIBLE or unicodedata.category(ch) in ("Cc", "Cs")))
    return " ".join(kept.split())


def md(text):
    """Inert markdown for Type docs and Spaces: one line; backslash escaped first, then ` * _ ~ [ ] ( ) | # @ ! $ = ^ :;
    & < > as entities; every dot inside a word escaped (with the colon escape: no URL, scheme:, www. or bare-domain
    autolink, no :emoji: shortcode);
    a leading list / ordered-list / heading / table / quote marker neutralised. Renders as the same characters."""
    if not isinstance(text, str):
        raise TypeError("md() needs a str")
    text = _MD_SPECIAL_RE.sub(r"\\\1", _one_line(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _MD_DOT_RE.sub(r"\\.", text)
    lead = re.match(r"([0-9]+)([.)])", text)
    if lead:
        text = lead.group(1) + "\\" + text[lead.end(1):]
    if text[:1] in ("-", "+"):
        text = "\\" + text
    return text


def slack(text):
    """Inert Slack mrkdwn: one line; & < > as entities (so <!channel>, <@U...>, <#C...> and <url|label> are
    plain text); * _ ~ ` @ [ ] as look-alike characters; scheme:// and domain dots broken so Slack can't link."""
    if not isinstance(text, str):
        raise TypeError("slack() needs a str")
    text = _one_line(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"^&gt;", "›", text)
    text = re.sub(r":([A-Za-z0-9_+-]+):", r"：\1：", text)
    text = "".join(_SLACK_LOOKALIKES.get(ch, ch) for ch in text)
    text = _IPV4_RE.sub(lambda m: m.group(0).replace(".", "\u2024"), text)
    return _DOMAIN_DOT_RE.sub("\u2024", text.replace("://", ":\u2215\u2215"))
