#!/usr/bin/env python3
"""Invoice Generator & Chaser: build invoices, track a markdown ledger, draft
overdue follow-ups.

Stdlib only, offline, deterministic. "Today" only ever comes from --as-of or
--date; the wall clock is never read.

Subcommands:
  init-ledger  create an empty ledger with the contract header
  create       render an invoice from a structured brief (--write appends a draft row;
               with billing_source stripe it only renders a draft to enter in Stripe)
  mark-sent    draft -> sent (call only after the human approved and sent it)
  mark-paid    record a full or partial payment
  chase        list overdue invoices and draft one follow-up per invoice
  status       outstanding summary with aging buckets

This script never sends email, never talks to Stripe, and never reads or stores
bank details or payment links. Payment details appear only as a placeholder
naming the secure note configured in payment_instructions_ref.

Exit codes: 0 ok, 2 bad or ambiguous input (message names file, line/item, field).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import string
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR
DEFAULT_CONFIG = SKILL_DIR / "invoice.json"
DEFAULT_TEMPLATES = SKILL_DIR / "chase_templates.json"

LEDGER_COLUMNS = [
    "number", "client", "contact_name", "contact_email", "client_address",
    "issue_date", "terms_days", "due_date", "currency", "total", "amount_paid",
    "status", "sent_date", "paid_date", "last_chase_tier", "last_chase_date",
]
CHASE_LOG_COLUMNS = ["number", "last_chase_tier", "last_chase_date"]
STATUSES = ("draft", "sent", "partial", "paid", "void")
OPEN_STATUSES = ("sent", "partial")
TEMPLATE_FIELDS = {
    "number", "client", "greeting_name", "amount", "due", "overdue",
    "payment", "sender_name", "business_name",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONEY_RE = re.compile(r"^\d+(\.\d+)?$")
NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
INT_RE = re.compile(r"^\d+$")
EMAIL_RE = re.compile(r"^[^@\s|]+@[^@\s|]+\.[^@\s|]+$")
INVOICE_NO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
SEP_RE = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+$")
UNREADABLE = "ledger is unreadable — stop, don't guess."


class InputError(Exception):
    """Unusable input. The message names the file and the offending field."""


def fail(msg: str) -> None:
    raise InputError(msg)


# ----------------------------------------------------------------- primitives

def parse_date(value: str, where: str) -> date:
    v = (value or "").strip()
    if not DATE_RE.match(v):
        fail(f"{where}: expected a YYYY-MM-DD date, got {value!r}")
    try:
        return date.fromisoformat(v)
    except ValueError:
        fail(f"{where}: {value!r} is not a real calendar date")
    raise AssertionError  # unreachable


def fmt_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def fmt_date_short(d: date, as_of: date) -> str:
    return f"{d:%b} {d.day}" if d.year == as_of.year else fmt_date(d)


def to_decimal(value, where: str, what: str) -> Decimal:
    """Strictly positive number from JSON; missing or <= 0 means ask the user."""
    if value is None or (isinstance(value, str) and not value.strip()):
        fail(f"{where} is missing. Ask the user for the {what} — do not guess.")
    if isinstance(value, bool):
        fail(f"{where}: {value!r} is not a number")
    if isinstance(value, (int, Decimal)):
        d = Decimal(value)
    elif isinstance(value, str) and NUMBER_RE.match(value.strip()):
        d = Decimal(value.strip())
    else:
        fail(f"{where}: {value!r} is not a plain number (no currency symbols or commas)")
    if d <= 0:
        fail(f"{where} is {d}. A {what} must be greater than zero — ask the user, do not guess.")
    return d


def clean_text(value, where: str, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        fail(f"{where}: expected text, got {type(value).__name__}")
    v = " ".join(value.split())
    if "|" in v:
        fail(f"{where}: the '|' character is not allowed (it breaks the markdown ledger/table)")
    if required and not v:
        fail(f"{where} is missing")
    return v


def norm_client(name: str) -> str:
    return " ".join(name.lower().split())


# ----------------------------------------------------------------- config

class Config:
    def __init__(self, path: Path, data: dict, templates: dict, templates_path: Path):
        self.path = path
        self.d = data
        self.templates = templates
        self.templates_path = templates_path

    def __getitem__(self, key):
        return self.d[key]

    def minor_units(self, cur: str) -> int:
        cc = self.d["currencies"]
        if cur in cc["zero_decimal"]:
            return 0
        if cur in cc["three_decimal"]:
            return 3
        return 2

    def round(self, x: Decimal, cur: str) -> Decimal:
        return x.quantize(Decimal(1).scaleb(-self.minor_units(cur)), rounding=ROUND_HALF_UP)

    def money(self, x: Decimal, cur: str, fixed: bool = False) -> str:
        mu = self.minor_units(cur)
        if not fixed and x == x.to_integral_value():
            s = format(x.quantize(Decimal(1)), ",f")
        else:
            s = format(x.quantize(Decimal(1).scaleb(-mu)), f",.{mu}f")
        sym = self.d["currencies"]["symbols"].get(cur)
        return f"{sym}{s}" if sym else f"{s} {cur}"

    def payment_placeholder(self, stripe_number: str | None = None) -> str:
        if stripe_number:
            return f"[Payment link: Stripe hosted invoice page for {stripe_number}]"
        ref = self.d["payment_instructions_ref"]
        if not ref:
            return "[Payment details: not configured — set payment_instructions_ref]"
        return f"[Payment details: see {ref}]"

    def require(self, keys: list[str], purpose: str) -> None:
        missing = [k for k in keys if not self.d.get(k)]
        if missing:
            fail(f"{self.path}: {', '.join(missing)} must be set to {purpose}. "
                 "Ask the user for the value(s) and save them in the config.")

    def parse_money(self, s: str, cur: str, where: str, allow_zero: bool) -> Decimal:
        if not MONEY_RE.match(s or ""):
            fail(f"{where}: expected a plain amount like 1250.00, got {s!r}")
        d = Decimal(s)
        mu = self.minor_units(cur)
        if -d.as_tuple().exponent > mu:
            fail(f"{where}: {s} has more decimal places than {cur} allows ({mu})")
        if d == 0 and not allow_zero:
            fail(f"{where}: amount must be greater than zero")
        return d


def load_json(path: Path, label: str):
    if not path.is_file():
        fail(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except UnicodeDecodeError:
        fail(f"{path}: not UTF-8 text")
    except json.JSONDecodeError as e:
        fail(f"{path}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}")


def _int(v, where: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        fail(f"{where}: expected a whole number between {lo} and {hi}, got {v!r}")
    return v


def forbidden_re(phrase: str) -> re.Pattern:
    """Whole-word match for a banned phrase, allowing a plural ending: "interest" matches
    "interest" and "interest charges" but not "interested"; "late fee" also matches "late fees"."""
    words = r"\s+".join(re.escape(w) for w in phrase.split())
    return re.compile(rf"\b{words}(?:s|es)?\b", re.IGNORECASE)


def benign_re(phrase: str) -> re.Pattern:
    """Whole-phrase match for an allowed phrase such as "interest-free" or "0% interest". Spaces and
    hyphens are interchangeable. A phrase followed by charges/fees/rates/penalties is not benign
    ("without interest charges" is still checked)."""
    words = r"[\s-]+".join(re.escape(w) for w in re.split(r"[\s-]+", phrase.strip()) if w)
    return re.compile(rf"(?<!\w){words}(?!\w)(?![\s-]*(?:charge|fee|rate|penalt))", re.IGNORECASE)


_YEAR = r"(?:19|20)\d{2}"
_MON = r"(?:0?[1-9]|1[0-2])"
_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
# Dates (2026-10-02, 2026/10/02, 2026-10, 02-10-2026) and year ranges (2024-2025, FY2024-25)
# are harmless in a note name; they are blanked out before digit groups are joined.
SAFE_DATE_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?:"
    rf"{_YEAR}[-/.]{_MON}(?:[-/.]{_DAY})?"
    rf"|{_DAY}[-/.]{_MON}[-/.]{_YEAR}|{_MON}[-/.]{_DAY}[-/.]{_YEAR}"
    rf"|(?:FY)?(?P<y1>{_YEAR})\s*[-/–]\s*(?:FY)?(?P<y2>{_YEAR}|\d{{2}})"
    rf")(?![0-9])", re.IGNORECASE)
# A banking label next to a number is refused even when the number is short or date-shaped.
ACCOUNT_LABEL_RE = re.compile(
    r"\b(?:acct|account|a/c|sort\s*code|sort|routing|aba|bsb|iban|swift|bic)\b"
    r"\s*(?:no\.?|num(?:ber)?\.?|#)?\s*[:#]?\s*(?P<num>\d[\d\s./-]*)", re.IGNORECASE)


def _safe_date(m: re.Match) -> str:
    if m.group("y1"):
        y1, y2 = int(m.group("y1")), m.group("y2")
        y2 = int(y2) if len(y2) == 4 else y1 // 100 * 100 + int(y2)
        if not 0 < y2 - y1 <= 10:
            return m.group(0)  # not a plausible financial-year range: leave it for the digit check
    return " "


def looks_like_payment_details(ref: str) -> bool:
    """True when a payment_instructions_ref looks like a link, an email, an account/sort/routing
    number or an IBAN rather than the NAME of a secure note."""
    if re.search(r"(://|www\.|@)", ref.replace(" ", "")):
        return True
    for m in ACCOUNT_LABEL_RE.finditer(ref):
        if len(re.sub(r"\D", "", m.group("num"))) >= 4:
            return True
    # Join digit/letter groups split by spaces, hyphens, dots, slashes etc. ("1234-5678",
    # "GB29 NWBK 6016 ...") before looking for account-number-like runs; dates go first.
    joined = re.sub(r"(?<=[A-Za-z0-9])[^A-Za-z0-9]+(?=\d)", "", SAFE_DATE_RE.sub(_safe_date, ref))
    return bool(re.search(r"(\d{6,}|\b[A-Z]{2}\d{2}[A-Z0-9]{8,})", joined))


def load_config(path_str: str | None, templates_str: str | None) -> Config:
    path = Path(path_str) if path_str else DEFAULT_CONFIG
    d = load_json(path, "config file")
    if not isinstance(d, dict):
        fail(f"{path}: top level must be a JSON object")
    if "bank_details" in d:
        fail(f"{path}: `bank_details` is not accepted. Put bank details in a secure note and set "
             "`payment_instructions_ref` to that note's name — never the details themselves.")
    required = [
        "business_name", "business_address", "from_email", "sender_name", "billing_source",
        "currency", "payment_terms_days", "payment_instructions_ref", "invoice_prefix",
        "tax_rate", "tax_label", "late_fee", "chase_schedule", "follow_up", "ledger_file",
        "post_to", "aging_buckets_days", "forbidden_phrases_without_late_fee", "currencies",
    ]
    for k in required:
        if k not in d:
            fail(f"{path}: required key `{k}` is missing")
    for k in ("business_name", "business_address", "from_email", "sender_name",
              "payment_instructions_ref", "tax_label", "ledger_file", "post_to"):
        if not isinstance(d[k], str):
            fail(f"{path}: `{k}` must be a string")
    if "|" in d["business_name"] + d["business_address"]:
        fail(f"{path}: business_name/business_address may not contain '|'")
    if d["from_email"] and not EMAIL_RE.match(d["from_email"]):
        fail(f"{path}: from_email {d['from_email']!r} is not an email address")
    if d["billing_source"] not in ("manual", "stripe"):
        fail(f"{path}: billing_source is {d['billing_source']!r}. It must be `manual` or `stripe` — "
             "run Step 0 (confirm the billing source with the user) and save it.")
    if not isinstance(d["currency"], str) or not CURRENCY_RE.match(d["currency"]):
        fail(f"{path}: currency must be an ISO 4217 code like USD")
    _int(d["payment_terms_days"], f"{path}: payment_terms_days", 0, 365)
    if looks_like_payment_details(d["payment_instructions_ref"]):
        fail(f"{path}: payment_instructions_ref looks like actual payment details or a link. Store "
             "those in a secure note and put only the note's name here.")
    if not isinstance(d["invoice_prefix"], str) or not re.match(r"^[A-Z][A-Z0-9]{0,9}$", d["invoice_prefix"]):
        fail(f"{path}: invoice_prefix must be 1-10 uppercase letters/digits, e.g. INV")
    tr = d["tax_rate"]
    if tr is not None:
        if isinstance(tr, bool) or not isinstance(tr, (int, Decimal)) or not 0 < Decimal(tr) < 1:
            fail(f"{path}: tax_rate must be null or a fraction between 0 and 1 (0.0825 = 8.25%)")
        d["tax_rate"] = Decimal(tr)
    sched = d["chase_schedule"]
    if not isinstance(sched, list) or not sched:
        fail(f"{path}: chase_schedule must be a non-empty list")
    last = 0
    for i, s in enumerate(sched):
        w = f"{path}: chase_schedule[{i}]"
        if not isinstance(s, dict):
            fail(f"{w}: expected an object with days and tone")
        days = _int(s.get("days"), f"{w}.days", 1, 3650)
        if days <= last:
            fail(f"{w}.days: tiers must be in strictly increasing order of days")
        last = days
        if not isinstance(s.get("tone"), str):
            fail(f"{w}.tone: expected a template name")
    lf = d["late_fee"]
    if lf is not None:
        if not isinstance(lf, dict) or not isinstance(lf.get("note"), str) or not lf["note"].strip():
            fail(f"{path}: late_fee must be null or an object with a non-empty `note` (the exact "
                 "wording you agreed with clients) and `from_tier`")
        _int(lf.get("from_tier"), f"{path}: late_fee.from_tier", 1, len(sched))
    if d["follow_up"] not in ("draft", "send"):
        fail(f"{path}: follow_up must be `draft` or `send`")
    ab = d["aging_buckets_days"]
    if not isinstance(ab, list) or not ab or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in ab) \
            or ab != sorted(set(ab)):
        fail(f"{path}: aging_buckets_days must be increasing whole numbers, e.g. [30, 60, 90]")
    cc = d["currencies"]
    if not isinstance(cc, dict) or not all(k in cc for k in ("symbols", "zero_decimal", "three_decimal")):
        fail(f"{path}: currencies needs symbols, zero_decimal and three_decimal")
    fp = d["forbidden_phrases_without_late_fee"]
    if not isinstance(fp, list) or not all(isinstance(x, str) for x in fp):
        fail(f"{path}: forbidden_phrases_without_late_fee must be a list of strings")
    # Optional: benign phrases ("interest-free", "no interest") removed before the banned-term check.
    ap = d.setdefault("allowed_phrases_without_late_fee", [])
    if not isinstance(ap, list) or not all(isinstance(x, str) and x.strip() for x in ap):
        fail(f"{path}: allowed_phrases_without_late_fee must be a list of non-empty strings")
    allowed = [benign_re(p) for p in ap]

    tpath = Path(templates_str) if templates_str else DEFAULT_TEMPLATES
    t = load_json(tpath, "chase templates file")
    if not isinstance(t, dict) or not isinstance(t.get("signoff"), str) or not isinstance(t.get("tones"), dict):
        fail(f"{tpath}: expected an object with `signoff` (string) and `tones` (object)")
    texts = [("signoff", t["signoff"])]
    for name, tone in t["tones"].items():
        if not isinstance(tone, dict) or not isinstance(tone.get("label"), str) \
                or not isinstance(tone.get("subject"), str) or not isinstance(tone.get("body"), list) \
                or not tone["body"] or not all(isinstance(p, str) for p in tone["body"]):
            fail(f"{tpath}: tones.{name} needs label, subject and a non-empty body list of paragraphs")
        texts.append((f"tones.{name}.subject", tone["subject"]))
        texts += [(f"tones.{name}.body[{i}]", p) for i, p in enumerate(tone["body"])]
    for where, text in texts:
        for _, field_name, _, _ in string.Formatter().parse(text):
            if field_name is not None and field_name not in TEMPLATE_FIELDS:
                fail(f"{tpath}: {where} uses unknown placeholder {{{field_name}}}; allowed: "
                     + ", ".join(sorted(TEMPLATE_FIELDS)))
        if lf is None:
            checked = text
            for r in allowed:
                checked = r.sub(" ", checked)
            hit = next((p for p in fp if forbidden_re(p).search(checked)), None)
            if hit:
                fail(f"{tpath}: {where} contains {hit!r}. Late fees and legal language are only allowed "
                     "when late_fee is explicitly configured.")
    for i, s in enumerate(sched):
        if s["tone"] not in t["tones"]:
            fail(f"{path}: chase_schedule[{i}].tone {s['tone']!r} has no template in {tpath}")
    return Config(path, d, t, tpath)


# ----------------------------------------------------------------- tables

@dataclass
class Table:
    path: Path
    preamble: list[str]
    columns: list[str]
    rows: list[dict]
    lines: list[int]


def split_row(s: str):
    s = s.strip()
    if len(s) < 2 or not (s.startswith("|") and s.endswith("|")):
        return None
    return [c.strip() for c in s[1:-1].split("|")]


def header_line(columns: list[str]) -> str:
    return "| " + " | ".join(columns) + " |"


def read_table(path: Path, columns: list[str], prefix: str) -> Table:
    if not path.is_file():
        fail(f"{prefix} {path}: file not found. If this is the very first invoice, create it with "
             "`invoice.py init-ledger`.")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        fail(f"{prefix} {path}: not UTF-8 text")
    start = next((i for i, l in enumerate(lines) if l.lstrip().startswith("|")), None)
    if start is None:
        fail(f"{prefix} {path}: no markdown table found. Expected header: {header_line(columns)}")
    if split_row(lines[start]) != columns:
        fail(f"{prefix} {path}: line {start + 1}: header does not match the data contract. "
             f"Expected: {header_line(columns)}  Found: {lines[start].strip()}")
    sep = lines[start + 1].strip() if start + 1 < len(lines) else ""
    if not SEP_RE.match(sep) or len(split_row(sep) or []) != len(columns):
        fail(f"{prefix} {path}: line {start + 2}: expected the |---|---| separator row with "
             f"{len(columns)} cells")
    rows, nums, ended = [], [], False
    for i in range(start + 2, len(lines)):
        s = lines[i].strip()
        if not s:
            ended = True
            continue
        if ended or not s.startswith("|"):
            fail(f"{prefix} {path}: line {i + 1}: unexpected content in or after the table: {s[:60]!r}")
        cells = split_row(s)
        if cells is None or len(cells) != len(columns):
            fail(f"{prefix} {path}: line {i + 1}: expected {len(columns)} cells, found "
                 f"{len(cells) if cells else 'an unterminated row'}")
        rows.append(dict(zip(columns, cells)))
        nums.append(i + 1)
    return Table(path, lines[:start], columns, rows, nums)


def write_table(t: Table) -> None:
    out = list(t.preamble)
    out.append(header_line(t.columns))
    out.append("|" + "|".join("---" for _ in t.columns) + "|")
    for r in t.rows:
        out.append("| " + " | ".join(r[c] for c in t.columns) + " |")
    text = "\n".join(out) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(t.path.parent), prefix=".ledger-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, t.path)


# ----------------------------------------------------------------- invoices

@dataclass
class Inv:
    number: str
    client: str
    contact_name: str
    contact_email: str
    currency: str
    total: Decimal
    paid: Decimal
    status: str
    issue_date: date | None
    due_date: date | None
    last_tier: int
    last_chase_date: date | None
    row: dict | None = None

    @property
    def balance(self) -> Decimal:
        return self.total - self.paid


def validate_ledger(t: Table, cfg: Config) -> list[Inv]:
    out, seen = [], {}
    for r, ln in zip(t.rows, t.lines):
        num = r["number"]

        def where(col):
            return f"{UNREADABLE} {t.path}: line {ln} ({num or 'row'}), column '{col}'"

        if not num or not INVOICE_NO_RE.match(num):
            fail(f"{where('number')}: missing or invalid invoice number {num!r}")
        if num in seen:
            fail(f"{where('number')}: duplicate of line {seen[num]}")
        seen[num] = ln
        if not r["client"]:
            fail(f"{where('client')}: empty")
        if r["contact_email"] and not EMAIL_RE.match(r["contact_email"]):
            fail(f"{where('contact_email')}: {r['contact_email']!r} is not an email address")
        issue = parse_date(r["issue_date"], where("issue_date"))
        if not INT_RE.match(r["terms_days"]):
            fail(f"{where('terms_days')}: expected whole days, got {r['terms_days']!r}")
        due = parse_date(r["due_date"], where("due_date"))
        if due < issue:
            fail(f"{where('due_date')}: due date is before the issue date")
        cur = r["currency"]
        if not CURRENCY_RE.match(cur):
            fail(f"{where('currency')}: expected an ISO code like USD, got {cur!r}")
        total = cfg.parse_money(r["total"], cur, where("total"), allow_zero=False)
        paid = cfg.parse_money(r["amount_paid"], cur, where("amount_paid"), allow_zero=True)
        if paid > total:
            fail(f"{where('amount_paid')}: {paid} is more than the total {total}")
        st = r["status"]
        if st not in STATUSES:
            fail(f"{where('status')}: {st!r} is not one of {', '.join(STATUSES)}")
        if r["sent_date"]:
            parse_date(r["sent_date"], where("sent_date"))
        if r["paid_date"]:
            parse_date(r["paid_date"], where("paid_date"))
        if st == "paid" and (paid != total or not r["paid_date"]):
            fail(f"{where('status')}: `paid` requires amount_paid == total and a paid_date")
        if st == "partial" and not 0 < paid < total:
            fail(f"{where('status')}: `partial` requires 0 < amount_paid < total")
        if st in ("draft", "sent") and paid != 0:
            fail(f"{where('status')}: `{st}` with amount_paid {paid} — should it be `partial`?")
        if not INT_RE.match(r["last_chase_tier"]):
            fail(f"{where('last_chase_tier')}: expected a whole number (0 = never chased)")
        tier = int(r["last_chase_tier"])
        lcd = None
        if tier > 0:
            lcd = parse_date(r["last_chase_date"], where("last_chase_date"))
        elif r["last_chase_date"]:
            fail(f"{where('last_chase_date')}: set but last_chase_tier is 0")
        out.append(Inv(num, r["client"], r["contact_name"], r["contact_email"], cur, total, paid,
                       st, issue, due, tier, lcd, r))
    return out


def read_ledger(path: Path, cfg: Config) -> tuple[Table, list[Inv]]:
    t = read_table(path, LEDGER_COLUMNS, UNREADABLE)
    return t, validate_ledger(t, cfg)


def read_stripe(path: Path, cfg: Config) -> tuple[list[Inv], dict]:
    data = load_json(path, "Stripe invoices file")
    if isinstance(data, dict) and data.get("object") == "list":
        data = data.get("data")
    if not isinstance(data, list):
        fail(f"{path}: expected the `data` array from GET /v1/invoices (a JSON list), or the whole list object")
    invs, skipped, seen = [], {}, set()
    for i, o in enumerate(data):
        w = f"{path}: [{i}]"
        if not isinstance(o, dict):
            fail(f"{w}: expected an invoice object")
        st = o.get("status")
        if not isinstance(st, str):
            fail(f"{w}.status: missing")
        if st != "open":
            skipped[st] = skipped.get(st, 0) + 1
            continue
        num = o.get("number")
        if not isinstance(num, str) or not num.strip():
            fail(f"{w}.number: missing on an open invoice")
        if num in seen:
            fail(f"{w}.number: duplicate {num}")
        seen.add(num)
        cur = o.get("currency")
        if not isinstance(cur, str) or not CURRENCY_RE.match(cur.upper()):
            fail(f"{w}.currency: expected a 3-letter code, got {cur!r}")
        cur = cur.upper()
        amts = {}
        for k in ("amount_due", "amount_remaining"):
            v = o.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                fail(f"{w}.{k}: expected a non-negative integer in minor units, got {v!r}")
            amts[k] = v
        if amts["amount_remaining"] > amts["amount_due"]:
            fail(f"{w}.amount_remaining is greater than amount_due")
        if amts["amount_due"] == 0:
            skipped["open, nothing due"] = skipped.get("open, nothing due", 0) + 1
            continue
        scale = Decimal(10) ** cfg.minor_units(cur)
        total = Decimal(amts["amount_due"]) / scale
        balance = Decimal(amts["amount_remaining"]) / scale

        def ts(key):
            v = o.get(key)
            if v is None:
                return None
            if isinstance(v, bool) or not isinstance(v, int):
                fail(f"{w}.{key}: expected a unix timestamp in seconds, got {v!r}")
            return datetime.fromtimestamp(v, tz=timezone.utc).date()

        due, created = ts("due_date"), ts("created")
        name = o.get("customer_name") if isinstance(o.get("customer_name"), str) else ""
        email = o.get("customer_email") if isinstance(o.get("customer_email"), str) else ""
        paid = total - balance
        invs.append(Inv(num, name or "(no customer name in Stripe)", "", email, cur, total, paid,
                        "partial" if paid > 0 else "sent", created, due, 0, None))
    return invs, skipped


def read_chase_log(path: Path) -> tuple[Table, dict]:
    if not path.is_file():
        return Table(path, ["# Chase log (invoice-chaser)", ""], CHASE_LOG_COLUMNS, [], []), {}
    t = read_table(path, CHASE_LOG_COLUMNS, "chase log is unreadable — stop, don't guess.")
    out = {}
    for r, ln in zip(t.rows, t.lines):
        w = f"chase log is unreadable — stop, don't guess. {path}: line {ln}"
        if not r["number"] or r["number"] in out:
            fail(f"{w}, column 'number': missing or duplicate")
        if not INT_RE.match(r["last_chase_tier"]):
            fail(f"{w}, column 'last_chase_tier': expected a whole number")
        out[r["number"]] = (int(r["last_chase_tier"]),
                            parse_date(r["last_chase_date"], f"{w}, column 'last_chase_date'"))
    return t, out


# ----------------------------------------------------------------- create

def next_number(invs: list[Inv], prefix: str, year: int) -> tuple[str, str | None]:
    pat = re.compile(rf"^{re.escape(prefix)}-{year}-(\d+)$")
    best, best_no = 0, None
    for inv in invs:
        m = pat.match(inv.number)
        if m and int(m.group(1)) > best:
            best, best_no = int(m.group(1)), inv.number
    return f"{prefix}-{year}-{best + 1:03d}", best_no


def read_stripe_history(path: Path, cfg: Config) -> list[dict]:
    """Any-status Stripe invoice export, read only for create: client auto-fill, duplicate
    hints and the next invoice number. Fields that are missing or malformed are ignored."""
    data = load_json(path, "Stripe invoices file")
    if isinstance(data, dict) and data.get("object") == "list":
        data = data.get("data")
    if not isinstance(data, list):
        fail(f"{path}: expected the `data` array from GET /v1/invoices (a JSON list), or the whole list object")
    out = []
    for i, o in enumerate(data):
        if not isinstance(o, dict):
            fail(f"{path}: [{i}]: expected an invoice object")

        def text(k):
            v = o.get(k)
            return v.strip() if isinstance(v, str) else ""

        cur, amt, created = text("currency").upper(), o.get("amount_due"), o.get("created")
        total = None
        if CURRENCY_RE.match(cur) and isinstance(amt, int) and not isinstance(amt, bool):
            total = Decimal(amt) / Decimal(10) ** cfg.minor_units(cur)
        ok_ts = isinstance(created, int) and not isinstance(created, bool)
        out.append({"number": text("number"), "name": text("customer_name"), "email": text("customer_email"),
                    "status": text("status"), "currency": cur, "total": total,
                    "created": datetime.fromtimestamp(created, tz=timezone.utc).date() if ok_ts else None})
    return out


def next_stripe_number(entries: list[dict], path: Path) -> tuple[str, str]:
    found = [m for m in (re.match(r"^(.*?)(\d+)$", e["number"]) for e in entries if e["number"]) if m]
    if not found:
        fail(f"{path}: no numbered invoices to continue from. Ask the user for the next invoice number "
             "and pass --number, or leave both out and let Stripe assign it when the invoice is finalized.")
    prefixes = sorted({m.group(1) for m in found})
    if len(prefixes) > 1:
        fail(f"{path}: invoice numbers use several prefixes ({', '.join(prefixes)}), so the next number is "
             "ambiguous. Ask the user which number Stripe will use and pass --number.")
    digits = max((m.group(2) for m in found), key=int)
    return f"{prefixes[0]}{int(digits) + 1:0{len(digits)}d}", f"{prefixes[0]}{digits}"


def load_brief(path: Path, cfg: Config) -> dict:
    b = load_json(path, "brief file")
    if not isinstance(b, dict):
        fail(f"{path}: top level must be a JSON object")
    known = {"client", "contact_name", "contact_email", "client_address", "currency",
             "terms_days", "items", "notes"}
    extra = sorted(set(b) - known)
    if extra:
        fail(f"{path}: unknown field(s) {', '.join(extra)}; allowed: {', '.join(sorted(known))}")
    out = {"client": clean_text(b.get("client"), f"{path}: client", True)}
    for k in ("contact_name", "contact_email", "client_address", "notes"):
        out[k] = clean_text(b.get(k), f"{path}: {k}", False)
    if out["contact_email"] and not EMAIL_RE.match(out["contact_email"]):
        fail(f"{path}: contact_email {out['contact_email']!r} is not an email address")
    cur = b.get("currency") or cfg["currency"]
    if not isinstance(cur, str) or not CURRENCY_RE.match(cur):
        fail(f"{path}: currency must be an ISO code like USD, got {cur!r}")
    out["currency"] = cur
    tdays = b.get("terms_days")
    out["terms_days"] = None if tdays is None else _int(tdays, f"{path}: terms_days", 0, 365)
    items = b.get("items")
    if not isinstance(items, list) or not items:
        fail(f"{path}: items is missing or empty. Ask the user what the invoice is for — never invent line items.")
    out["items"] = []
    for i, it in enumerate(items, 1):
        w = f"{path}: items[{i}]"
        if not isinstance(it, dict):
            fail(f"{w}: expected an object with description, qty, rate")
        extra = sorted(set(it) - {"description", "qty", "rate", "service_date", "service_end_date", "currency"})
        if extra:
            fail(f"{w}: unknown field(s) {', '.join(extra)}")
        if it.get("currency") not in (None, cur):
            fail(f"{w}.currency is {it.get('currency')!r} but the invoice is in {cur}. One invoice "
                 "cannot mix currencies — ask the user whether to split it or convert at an agreed rate.")
        desc = clean_text(it.get("description"), f"{w}.description", True)
        qty = to_decimal(it.get("qty"), f"{w}.qty", "quantity")
        rate = to_decimal(it.get("rate"), f"{w}.rate", "rate")
        sd = parse_date(it["service_date"], f"{w}.service_date") if it.get("service_date") else None
        ed = parse_date(it["service_end_date"], f"{w}.service_end_date") if it.get("service_end_date") else None
        if ed and not sd:
            fail(f"{w}: service_end_date without service_date")
        if sd and ed and ed < sd:
            fail(f"{w}: service_end_date is before service_date")
        out["items"].append({"description": desc, "qty": qty, "rate": rate, "start": sd, "end": ed})
    return out


def fmt_qty(q: Decimal) -> str:
    return format(q.normalize(), "f")


def cmd_init_ledger(args) -> int:
    path = Path(args.ledger)
    if path.exists():
        fail(f"{path} already exists — not overwriting it")
    write_table(Table(path, ["# Invoice ledger", "",
                             "Source of truth for invoices when billing_source is manual. "
                             "Edit with care; keep the header exactly as is.", ""],
                      LEDGER_COLUMNS, [], []))
    print(f"Created empty ledger {path}")
    return 0


def cmd_create(args) -> int:
    cfg = load_config(args.config, args.templates)
    cfg.require(["business_name", "business_address", "from_email"], "create an invoice")
    as_of = parse_date(args.as_of, "--as-of")
    stripe = cfg["billing_source"] == "stripe"
    if args.write and stripe:
        fail("billing_source is stripe: this skill never creates Stripe invoices and the ledger is not "
             "the source of truth. Use the rendered draft to create the invoice in Stripe yourself.")
    history = []
    if stripe:
        if args.ledger:
            fail("billing_source is stripe: create does not read a ledger. Leave out --ledger; optionally pass "
                 "--stripe-invoices (an export of your Stripe invoices) and/or --number.")
        table, invs, ledger_path = None, [], None
        if args.number and not INVOICE_NO_RE.match(args.number):
            fail(f"--number: {args.number!r} is not a valid invoice number")
        if args.stripe_invoices:
            history = read_stripe_history(Path(args.stripe_invoices), cfg)
    else:
        if args.number or args.stripe_invoices:
            fail("--number and --stripe-invoices are only for billing_source: stripe. In manual mode the "
                 "ledger assigns the next number.")
        ledger_path = Path(args.ledger or cfg["ledger_file"])
        table, invs = read_ledger(ledger_path, cfg)
    b = load_brief(Path(args.brief), cfg)
    cur = b["currency"]
    notes = []

    prior = [x for x in invs if norm_client(x.client) == norm_client(b["client"])]
    prior.sort(key=lambda x: (x.issue_date, table.rows.index(x.row)))
    filled = {}
    for k in ("contact_name", "contact_email", "client_address"):
        if b[k]:
            prev = next((x.row[k] for x in reversed(prior) if x.row[k]), "")
            if prev and prev.lower() != b[k].lower():
                notes.append(f"{k.replace('_', ' ').capitalize()} {b[k]!r} differs from the ledger ({prev!r}). Confirm which is current.")
            continue
        src = next((x for x in reversed(prior) if x.row[k]), None)
        if src:
            b[k] = src.row[k]
            filled.setdefault(src.number, []).append(k.replace("_", " "))
    s_prior = sorted((e for e in history if e["name"] and norm_client(e["name"]) == norm_client(b["client"])),
                     key=lambda e: (e["created"] or date.min, e["number"]))
    if stripe and not b["contact_email"]:
        src_s = next((e for e in reversed(s_prior) if e["email"]), None)
        if src_s:
            b["contact_email"] = src_s["email"]
            filled.setdefault(src_s["number"] or "the Stripe export", []).append("contact email")
    if not b["contact_email"]:
        where = ("in the brief or in the Stripe export" if args.stripe_invoices else
                 "in the brief (no --stripe-invoices export was given to look it up)") if stripe \
            else "in the brief or in earlier ledger invoices"
        fail(f"{args.brief}: no contact email for {b['client']!r} {where}. "
             "Ask the user who should receive this invoice.")
    if b["terms_days"] is not None:
        terms, terms_src = b["terms_days"], "from the brief"
    else:
        src = next((x for x in reversed(prior) if x.row["terms_days"]), None)
        if src:
            terms, terms_src = int(src.row["terms_days"]), f"{b['client']}'s previous terms ({src.number})"
        else:
            terms, terms_src = cfg["payment_terms_days"], "config default"
    due = as_of + timedelta(days=terms)
    if not stripe:
        number, last_no = next_number(invs, cfg["invoice_prefix"], as_of.year)
        number_note = (f"next in the {as_of.year} sequence after {last_no}." if last_no
                       else f"first {cfg['invoice_prefix']} invoice of {as_of.year}.")
    elif args.number:
        number = args.number
        number_note = "given with --number. Stripe sets the real number when the invoice is finalized; check they match."
    elif args.stripe_invoices:
        number, last_no = next_stripe_number(history, Path(args.stripe_invoices))
        number_note = (f"next after {last_no}, the highest number in {Path(args.stripe_invoices).name}. Stripe sets "
                       "the real number when the invoice is finalized; check they match.")
    else:
        number = "(number assigned by Stripe)"
        number_note = ("Stripe assigns it when the invoice is finalized. To show it here, pass --number or "
                       "--stripe-invoices with an export of your invoices.")

    lines = []
    for it in b["items"]:
        amt = cfg.round(it["qty"] * it["rate"], cur)
        lines.append({**it, "amount": amt})
    subtotal = sum((ln["amount"] for ln in lines), Decimal(0))
    tax = cfg.round(subtotal * cfg["tax_rate"], cur) if cfg["tax_rate"] is not None else None
    total = subtotal + (tax or Decimal(0))
    values = [ln["rate"] for ln in lines] + [ln["amount"] for ln in lines] + [subtotal, total] + ([tax] if tax else [])
    fixed = any(v != v.to_integral_value() for v in values)

    def m(x):
        return cfg.money(x, cur, fixed)

    def desc(ln):
        d = ln["description"]
        if ln["start"] and ln["end"]:
            d += f" ({fmt_date_short(ln['start'], as_of)}–{fmt_date_short(ln['end'], as_of)})"
        elif ln["start"]:
            d += f" ({fmt_date_short(ln['start'], as_of)})"
        return d

    terms_txt = "due on receipt" if terms == 0 else f"NET {terms}"
    to_line = f"{b['client']} — {b['contact_email']}"
    if b["client_address"]:
        to_line += f" — {b['client_address']}"
    out = [f"#### Invoice {number}", "",
           f"**From:** {cfg['business_name']} — {cfg['business_address']} — {cfg['from_email']}",
           f"**To:** {to_line}",
           f"**Date:** {fmt_date(as_of)}",
           f"**Due:** {fmt_date(due)} ({terms_txt})", "",
           "| # | Description | Qty | Rate | Amount |", "|---|---|---|---|---|"]
    for i, ln in enumerate(lines, 1):
        out.append(f"| {i} | {desc(ln)} | {fmt_qty(ln['qty'])} | {m(ln['rate'])} | {m(ln['amount'])} |")
    out += ["", f"**Subtotal:** {m(subtotal)}"]
    if tax is not None:
        out.append(f"**{cfg['tax_label']} ({fmt_qty(cfg['tax_rate'] * 100)}%):** {m(tax)}")
        out.append(f"**Total:** {m(total)}")
    else:
        out.append(f"**Total:** {m(total)} (excluding tax)")
    if stripe:
        pay = cfg.payment_placeholder(number if args.number or args.stripe_invoices else "this invoice")
    else:
        pay = cfg.payment_placeholder()
    out += ["", f"Payment: {pay}"]
    if cfg["late_fee"]:
        out.append(f"Late payment: {cfg['late_fee']['note']}")
    if b["notes"]:
        out.append(f"Notes: {b['notes']}")

    out += ["", "---", "**Before this goes out** (review notes, not part of the invoice)"]
    out.append(f"- Number{'' if number.startswith('(') else ' ' + number}: {number_note}")
    if filled:
        out.append(f"- Auto-filled from {'the Stripe export' if stripe else 'the ledger'}: " + "; ".join(
            f"{', '.join(v)} from {k}" for k, v in filled.items()) + ".")
    elif not stripe and not prior:
        out.append(f"- New client: no earlier invoices to {b['client']} in the ledger.")
    elif stripe and args.stripe_invoices and not s_prior:
        out.append(f"- New client: no earlier invoices to {b['client']} in {Path(args.stripe_invoices).name}.")
    out.append(f"- Payment terms {terms_txt}: {terms_src}.")
    if tax is None:
        out.append("- Tax: no tax_rate configured, so the total is shown excluding tax.")
    else:
        out.append(f"- Tax: fixed rate {fmt_qty(cfg['tax_rate'] * 100)}% from config, applied to the subtotal.")
    if not b["client_address"]:
        out.append(f"- No billing address on file for {b['client']}. Add one if the client requires it.")
    if not stripe and not cfg["payment_instructions_ref"]:
        out.append("- Payment details are not configured (payment_instructions_ref is empty).")
    other_cur = sorted({x.currency for x in prior} - {cur})
    if other_cur:
        out.append(f"- Earlier invoices to this client were in {', '.join(other_cur)}; this one is in {cur}.")
    dups = [x for x in prior if x.status != "void" and x.currency == cur and x.total == total]
    for x in dups:
        out.append(f"- Possible duplicate: {x.number} ({fmt_date(x.issue_date)}, {x.status}) has the same client and total.")
    for e in s_prior:
        if e["status"] != "void" and e["currency"] == cur and e["total"] == total:
            out.append(f"- Possible duplicate: Stripe invoice {e['number'] or '(unnumbered draft)'} "
                       f"({e['status']}) has the same client and total.")
    out += [f"- {n}" for n in notes]

    row = {"number": number, "client": b["client"], "contact_name": b["contact_name"],
           "contact_email": b["contact_email"], "client_address": b["client_address"],
           "issue_date": as_of.isoformat(), "terms_days": str(terms), "due_date": due.isoformat(),
           "currency": cur, "total": format(total, "f"), "amount_paid": format(cfg.round(Decimal(0), cur), "f"),
           "status": "draft", "sent_date": "", "paid_date": "", "last_chase_tier": "0", "last_chase_date": ""}
    if stripe and number.startswith("("):
        row["number"] = None
    if stripe:
        out.append("- Stripe: nothing was written. Once the user approves, they create this invoice in Stripe "
                   "themselves from the details above; this skill never creates, finalizes or sends Stripe invoices.")
        out.append("- Sent and paid status then come from Stripe: `chase` and `status` read them from a Stripe export.")
    elif args.write:
        table.rows.append(row)
        validate_ledger(table, cfg)
        write_table(table)
        out.append(f"- Ledger: recorded {number} as `draft` in {ledger_path.name}.")
    else:
        out.append(f"- Ledger: not recorded yet (preview). Once the user approves, rerun with --write to add {number} as `draft`.")
    if not stripe:
        out.append(f"- After the user approves and the invoice is actually sent: `mark-sent --number {number} --date YYYY-MM-DD`.")

    if args.json:
        print(json.dumps({"invoice": {**row, "subtotal": str(subtotal), "tax": None if tax is None else str(tax),
                                      "lines": [{"description": desc(ln), "qty": str(ln["qty"]), "rate": str(ln["rate"]),
                                                 "amount": str(ln["amount"])} for ln in lines]},
                          "written": bool(args.write), "markdown": "\n".join(out)}, indent=2, ensure_ascii=False))
    else:
        print("\n".join(out))
    return 0


# ----------------------------------------------------------------- mark-sent / mark-paid

def find(invs: list[Inv], number: str) -> Inv:
    inv = next((x for x in invs if x.number == number), None)
    if inv is None:
        fail(f"invoice {number} is not in the ledger")
    return inv


def cmd_mark_sent(args) -> int:
    cfg = load_config(args.config, args.templates)
    when = parse_date(args.date, "--date")
    table, invs = read_ledger(Path(args.ledger or cfg["ledger_file"]), cfg)
    inv = find(invs, args.number)
    if inv.status != "draft":
        fail(f"{inv.number} is `{inv.status}`, not `draft` — nothing changed")
    if when < inv.issue_date:
        fail(f"--date {when} is before {inv.number}'s issue date {inv.issue_date}")
    inv.row["status"], inv.row["sent_date"] = "sent", when.isoformat()
    write_table(table)
    print(f"Marked {inv.number} as sent on {fmt_date(when)}. Due {fmt_date(inv.due_date)}; "
          f"{cfg.money(inv.total, inv.currency)} outstanding.")
    return 0


def cmd_mark_paid(args) -> int:
    cfg = load_config(args.config, args.templates)
    when = parse_date(args.date, "--date")
    table, invs = read_ledger(Path(args.ledger or cfg["ledger_file"]), cfg)
    inv = find(invs, args.number)
    if inv.status not in OPEN_STATUSES:
        fail(f"{inv.number} is `{inv.status}`; only sent or partial invoices can take a payment")
    if when < inv.issue_date:
        fail(f"--date {when} is before {inv.number}'s issue date {inv.issue_date}")
    amount = inv.balance if args.amount is None else cfg.parse_money(args.amount, inv.currency, "--amount", False)
    if amount > inv.balance:
        fail(f"--amount {amount} is more than the {inv.number} balance {inv.balance} {inv.currency}. "
             "Overpayments need a human decision (refund or credit) — nothing changed.")
    paid = inv.paid + amount
    inv.row["amount_paid"] = format(cfg.round(paid, inv.currency), "f")
    if paid == inv.total:
        inv.row["status"], inv.row["paid_date"] = "paid", when.isoformat()
        print(f"Marked {inv.number} as paid in full on {fmt_date(when)} ({cfg.money(inv.total, inv.currency)}).")
    else:
        inv.row["status"] = "partial"
        print(f"Recorded a partial payment of {cfg.money(amount, inv.currency)} on {inv.number} "
              f"({fmt_date(when)}). Balance: {cfg.money(inv.total - paid, inv.currency)} of "
              f"{cfg.money(inv.total, inv.currency)}.")
    validate_ledger(table, cfg)
    write_table(table)
    return 0


# ----------------------------------------------------------------- chase / status

def load_source(args, cfg: Config):
    if bool(args.ledger) == bool(args.stripe_invoices):
        fail("pass exactly one of --ledger or --stripe-invoices")
    if args.ledger:
        if cfg["billing_source"] != "manual":
            fail(f"config billing_source is {cfg['billing_source']!r} but --ledger was given")
        table, invs = read_ledger(Path(args.ledger), cfg)
        return {"kind": "ledger", "table": table, "invs": invs, "skipped": {},
                "label": f"ledger `{Path(args.ledger).name}`"}
    if cfg["billing_source"] != "stripe":
        fail(f"config billing_source is {cfg['billing_source']!r} but --stripe-invoices was given")
    invs, skipped = read_stripe(Path(args.stripe_invoices), cfg)
    src = {"kind": "stripe", "table": None, "invs": invs, "skipped": skipped,
           "label": f"Stripe open invoices `{Path(args.stripe_invoices).name}` (read-only)"}
    if getattr(args, "chase_log", None):
        log_table, log = read_chase_log(Path(args.chase_log))
        for inv in invs:
            if inv.number in log:
                inv.last_tier, inv.last_chase_date = log[inv.number]
        src["log_table"] = log_table
    return src


def overdue_words(days: int) -> str:
    if days % 7 == 0 and 1 <= days // 7 <= 4:
        return ["a week", "two weeks", "three weeks", "four weeks"][days // 7 - 1]
    return "1 day" if days == 1 else f"{days} days"


def plan_chase(invs: list[Inv], cfg: Config, as_of: date) -> list[dict]:
    sched = cfg["chase_schedule"]
    n = len(sched)
    plan = []
    for inv in invs:
        if inv.status not in OPEN_STATUSES or inv.due_date is None or inv.due_date >= as_of:
            continue
        days = (as_of - inv.due_date).days
        reached = sum(1 for s in sched if days >= s["days"])
        p = {"inv": inv, "days": days, "tier": reached, "action": "", "note": ""}
        if reached == 0:
            p["action"] = "wait"
            p["note"] = f"Before the first reminder (tier 1 at {sched[0]['days']} days, " \
                        f"{fmt_date_short(inv.due_date + timedelta(days=sched[0]['days']), as_of)})"
        elif inv.last_tier >= n:
            p["action"] = "human"
            p["note"] = f"All {n} scheduled reminders sent — needs a personal call or a decision"
        elif reached > inv.last_tier:
            p["action"] = "draft"
            p["note"] = "Draft below" + (" (first reminder for this invoice)" if inv.last_tier == 0 else "")
        else:
            nxt = sched[inv.last_tier]
            label = cfg.templates["tones"][nxt["tone"]]["label"]
            p["action"] = "skip"
            p["note"] = f"Skip — tier {inv.last_tier} already sent; {label} due " \
                        f"{fmt_date_short(inv.due_date + timedelta(days=nxt['days']), as_of)}"
        if p["action"] == "draft" and not inv.contact_email:
            p["note"] += "; no email on file — ask the user"
        plan.append(p)
    plan.sort(key=lambda p: (-p["days"], p["inv"].number))
    return plan


def draft_email(p: dict, cfg: Config, as_of: date, stripe: bool) -> dict:
    inv = p["inv"]
    tone_name = cfg["chase_schedule"][p["tier"] - 1]["tone"]
    tone = cfg.templates["tones"][tone_name]
    amount = cfg.money(inv.total, inv.currency)
    if inv.paid > 0:
        amount = f"{cfg.money(inv.balance, inv.currency)} outstanding of {amount}"
    first = inv.contact_name.split()[0] if inv.contact_name.strip() else "there"
    vals = {"number": inv.number, "client": inv.client, "greeting_name": first, "amount": amount,
            "due": fmt_date_short(inv.due_date, as_of), "overdue": overdue_words(p["days"]),
            "payment": cfg.payment_placeholder(inv.number if stripe else None),
            "sender_name": cfg["sender_name"], "business_name": cfg["business_name"]}
    paras = [x.format(**vals) for x in tone["body"]]
    lf = cfg["late_fee"]
    if lf and p["tier"] >= lf["from_tier"]:
        paras.append(lf["note"])
    paras.append(cfg.templates["signoff"].format(**vals))
    return {"number": inv.number, "to": inv.contact_email, "to_name": inv.contact_name,
            "tier": p["tier"], "tone": tone_name, "label": tone["label"],
            "subject": tone["subject"].format(**vals), "body": "\n\n".join(paras)}


def totals_by_currency(items, cfg: Config) -> str:
    acc = {}
    for inv in items:
        acc[inv.currency] = acc.get(inv.currency, Decimal(0)) + inv.balance
    out = []
    for c, v in sorted(acc.items()):
        s = cfg.money(v, c)
        # Symbol amounts get the code for clarity ("$1,200 USD"); symbol-less ones already end in it.
        out.append(s if s.endswith(f" {c}") else f"{s} {c}")
    return " · ".join(out) or "none"


def cmd_chase(args) -> int:
    cfg = load_config(args.config, args.templates)
    cfg.require(["sender_name", "business_name"], "sign follow-up emails")
    as_of = parse_date(args.as_of, "--as-of")
    src = load_source(args, cfg)
    stripe = src["kind"] == "stripe"
    plan = plan_chase(src["invs"], cfg, as_of)
    n = len(cfg["chase_schedule"])

    if args.record:
        wanted = [x.strip() for x in args.record.split(",") if x.strip()]
        if not wanted:
            fail("--record needs one or more invoice numbers, comma separated")
        if stripe and "log_table" not in src:
            fail("Stripe mode keeps chase history in a chase log: pass --chase-log path/to/chase-log.md")
        by_no = {p["inv"].number: p for p in plan}
        for w in wanted:
            p = by_no.get(w)
            if not p or p["action"] != "draft":
                fail(f"{w} has no drafted follow-up in this run as of {as_of} "
                     f"({p['note'] if p else 'not overdue or not open'}) — nothing recorded")
        for w in wanted:
            p = by_no[w]
            if stripe:
                t = src["log_table"]
                row = next((r for r in t.rows if r["number"] == w), None)
                if row is None:
                    row = {"number": w}
                    t.rows.append(row)
                row["last_chase_tier"], row["last_chase_date"] = str(p["tier"]), as_of.isoformat()
            else:
                p["inv"].row["last_chase_tier"] = str(p["tier"])
                p["inv"].row["last_chase_date"] = as_of.isoformat()
        if stripe:
            write_table(src["log_table"])
        else:
            validate_ledger(src["table"], cfg)
            write_table(src["table"])
        for w in wanted:
            p = by_no[w]
            label = cfg.templates["tones"][cfg["chase_schedule"][p["tier"] - 1]["tone"]]["label"]
            print(f"Recorded {w}: tier {p['tier']} of {n} ({label}) sent {fmt_date(as_of)}.")
        return 0

    drafts = [draft_email(p, cfg, as_of, stripe) for p in plan if p["action"] == "draft"]
    open_invs = [x for x in src["invs"] if x.status in OPEN_STATUSES]

    if args.json:
        print(json.dumps({
            "as_of": as_of.isoformat(), "source": src["label"], "follow_up": cfg["follow_up"],
            "overdue": [{"number": p["inv"].number, "client": p["inv"].client, "currency": p["inv"].currency,
                         "balance": str(p["inv"].balance), "total": str(p["inv"].total),
                         "due_date": p["inv"].due_date.isoformat(), "days_overdue": p["days"],
                         "tier_reached": p["tier"], "last_chase_tier": p["inv"].last_tier,
                         "action": p["action"], "note": p["note"]} for p in plan],
            "drafts": drafts}, indent=2, ensure_ascii=False))
        return 0

    out = [f"#### Overdue invoices — {fmt_date(as_of)}", "",
           f"**Source:** {src['label']} · **Follow-up mode:** {cfg['follow_up']}"
           + (" — nothing is sent without your approval" if cfg["follow_up"] == "draft" else ""), ""]
    if not plan:
        out.append(f"**Summary:** nothing overdue. {len(open_invs)} open invoice(s), none past due.")
        print("\n".join(out))
        return 0
    counts = {a: sum(1 for p in plan if p["action"] == a) for a in ("draft", "skip", "wait", "human")}
    summary = (f"**Summary:** {len(plan)} of {len(open_invs)} open invoices are overdue — "
               f"{totals_by_currency([p['inv'] for p in plan], cfg)}. "
               f"{counts['draft']} follow-up(s) drafted")
    if counts["skip"]:
        summary += f", {counts['skip']} already chased at their current tier"
    if counts["wait"]:
        summary += f", {counts['wait']} not yet at the first reminder"
    if counts["human"]:
        summary += f", {counts['human']} need a human decision"
    out += [summary + ".", "",
            "| Invoice | Client | Balance | Due | Days overdue | Tier reached | Last chased | Action |",
            "|---|---|---|---|---|---|---|---|"]
    for p in plan:
        inv = p["inv"]
        bal = cfg.money(inv.balance, inv.currency)
        if inv.paid > 0:
            bal += f" of {cfg.money(inv.total, inv.currency)}"
        tier = "—" if p["tier"] == 0 else \
            f"{p['tier']} · {cfg.templates['tones'][cfg['chase_schedule'][p['tier'] - 1]['tone']]['label']}"
        last = "never" if inv.last_tier == 0 else \
            f"tier {inv.last_tier} · {fmt_date_short(inv.last_chase_date, as_of)}"
        out.append(f"| {inv.number} | {inv.client} | {bal} | {fmt_date_short(inv.due_date, as_of)} | "
                   f"{p['days']} | {tier} | {last} | {p['note']} |")
    if stripe and "log_table" not in src:
        out += ["", "_No chase log given: earlier reminders can't be detected, so every overdue invoice is "
                    "treated as never chased. Pass --chase-log to avoid repeats._"]
    if src["skipped"]:
        out += ["", "_Not considered (not open in Stripe): " + ", ".join(
            f"{v} {k}" for k, v in sorted(src["skipped"].items())) + "._"]
    nodue = [x for x in open_invs if x.due_date is None]
    if nodue:
        out += ["", "_Open with no due date (auto-charge invoices, not chased): "
                + ", ".join(x.number for x in nodue) + "._"]
    for i, d in enumerate(drafts, 1):
        to = f"{d['to_name']} <{d['to']}>" if d["to_name"] and d["to"] else (d["to"] or "(no email on file)")
        out += ["", "---", "", f"**Draft {i} · {d['number']} → {to}** — tier {d['tier']} of {n} ({d['label']})",
                "", "```", f"Subject: {d['subject']}", "", d["body"], "```"]
    if drafts:
        nums = ",".join(d["number"] for d in drafts)
        out += ["", "---", ""]
        if cfg["follow_up"] == "draft":
            out.append("**Next:** reply with which drafts to send (for example \"send 1 and 3\") or what to change. "
                       "Nothing has been sent.")
        else:
            out.append("**Next:** follow_up is `send`: drafts below the final tier may be sent now; "
                       "the final notice still waits for your approval.")
        out.append(f"After sending, record them so next week's run moves on a tier: "
                   f"`chase ... --as-of {as_of.isoformat()} --record {nums}` (only list the ones actually sent).")
    print("\n".join(out))
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config, args.templates)
    as_of = parse_date(args.as_of, "--as-of")
    src = load_source(args, cfg)
    invs = src["invs"]
    open_invs = [x for x in invs if x.status in OPEN_STATUSES]
    drafts = [x for x in invs if x.status == "draft"]
    paid = [x for x in invs if x.status == "paid"]
    overdue = [x for x in open_invs if x.due_date and x.due_date < as_of]
    buckets = cfg["aging_buckets_days"]
    names = ["Not yet due"] + [f"{a + 1}–{b}" for a, b in zip([0] + buckets[:-1], buckets)] + [f"{buckets[-1]}+"]

    def bucket(inv):
        if inv.due_date is None or inv.due_date >= as_of:
            return 0
        days = (as_of - inv.due_date).days
        return next((i + 1 for i, b in enumerate(buckets) if days <= b), len(buckets) + 1)

    aging = {}
    for x in open_invs:
        row = aging.setdefault(x.currency, [Decimal(0)] * len(names))
        row[bucket(x)] += x.balance

    def days_over(x):
        return (as_of - x.due_date).days if x.due_date and x.due_date < as_of else 0

    ordered = sorted(open_invs, key=lambda x: (-days_over(x), x.due_date or date.max, x.number))
    if args.json:
        print(json.dumps({
            "as_of": as_of.isoformat(), "source": src["label"],
            "open": [{"number": x.number, "client": x.client, "status": x.status,
                      "overdue": days_over(x) > 0, "days_overdue": days_over(x), "currency": x.currency,
                      "balance": str(x.balance), "total": str(x.total),
                      "due_date": x.due_date.isoformat() if x.due_date else None} for x in ordered],
            "drafts": [{"number": x.number, "client": x.client, "currency": x.currency,
                        "total": str(x.total)} for x in drafts],
            "aging": {c: dict(zip(names, map(str, v))) for c, v in sorted(aging.items())},
        }, indent=2, ensure_ascii=False))
        return 0

    partial = sum(1 for x in open_invs if x.status == "partial")
    out = [f"#### Outstanding invoices — as of {fmt_date(as_of)}", "", f"**Source:** {src['label']}", "",
           f"**Summary:** {len(open_invs)} unpaid invoice(s) sent ({len(overdue)} overdue, {partial} partially paid)"
           + (f" · {len(drafts)} draft(s) not yet sent" if src["kind"] == "ledger" else "") + ".",
           f"**Outstanding:** {totals_by_currency(open_invs, cfg)}",
           f"**Overdue:** {totals_by_currency(overdue, cfg)}", ""]
    if ordered:
        out += ["| Invoice | Client | Status | Issued | Due | Balance | Days overdue | Last chased |",
                "|---|---|---|---|---|---|---|---|"]
        for x in ordered:
            st = ("overdue · " if days_over(x) else "") + x.status
            bal = cfg.money(x.balance, x.currency) + (f" of {cfg.money(x.total, x.currency)}" if x.paid else "")
            last = "never" if x.last_tier == 0 else f"tier {x.last_tier} · {fmt_date_short(x.last_chase_date, as_of)}"
            if src["kind"] == "stripe" and x.last_tier == 0:
                last = "—"
            out.append(f"| {x.number} | {x.client} | {st} | "
                       f"{fmt_date_short(x.issue_date, as_of) if x.issue_date else '—'} | "
                       f"{fmt_date_short(x.due_date, as_of) if x.due_date else 'none (auto-charge)'} | "
                       f"{bal} | {days_over(x) or '—'} | {last} |")
        out += ["", "**Aging** (unpaid balance of sent invoices, by days past due)", "",
                "| Currency | " + " | ".join(names) + " | Total |",
                "|---" * (len(names) + 2) + "|"]
        for c, v in sorted(aging.items()):
            out.append(f"| {c} | " + " | ".join(cfg.money(a, c) if a else "—" for a in v)
                       + f" | {cfg.money(sum(v, Decimal(0)), c)} |")
    if drafts:
        out += ["", "**Drafts — not sent yet, not counted as outstanding**", "",
                "| Invoice | Client | Issued | Total | Note |", "|---|---|---|---|---|"]
        for x in drafts:
            note = "due date already passed — re-date before sending" if x.due_date < as_of else ""
            out.append(f"| {x.number} | {x.client} | {fmt_date_short(x.issue_date, as_of)} | "
                       f"{cfg.money(x.total, x.currency)} | {note} |")
    if src["kind"] == "ledger":
        out += ["", f"{len(paid)} paid invoice(s) not shown."]
    elif src["skipped"]:
        out += ["", "Not open in Stripe (not shown): " + ", ".join(
            f"{v} {k}" for k, v in sorted(src["skipped"].items())) + "."]
    print("\n".join(out))
    return 0


# ----------------------------------------------------------------- cli

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, ledger_required=False):
        p.add_argument("--config", help="config JSON (default: invoice.json)")
        p.add_argument("--templates", help="chase templates JSON (default: chase_templates.json)")
        p.add_argument("--ledger", required=ledger_required, help="markdown ledger file")

    p = sub.add_parser("init-ledger", help="create an empty ledger")
    p.add_argument("--ledger", required=True)
    p.set_defaults(fn=cmd_init_ledger)

    p = sub.add_parser("create", help="render an invoice from a brief")
    common(p)
    p.add_argument("--brief", required=True)
    p.add_argument("--as-of", required=True, help="issue date, YYYY-MM-DD")
    p.add_argument("--write", action="store_true", help="append the invoice to the ledger as draft (manual only)")
    p.add_argument("--number", help="stripe mode: invoice number to show (default: derived from --stripe-invoices)")
    p.add_argument("--stripe-invoices", help="stripe mode: export of GET /v1/invoices (any status) for "
                                              "client auto-fill and the next number")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("mark-sent", help="draft -> sent, after approval")
    common(p)
    p.add_argument("--number", required=True)
    p.add_argument("--date", required=True, help="date it was sent, YYYY-MM-DD")
    p.set_defaults(fn=cmd_mark_sent)

    p = sub.add_parser("mark-paid", help="record a full or partial payment")
    common(p)
    p.add_argument("--number", required=True)
    p.add_argument("--date", required=True, help="payment date, YYYY-MM-DD")
    p.add_argument("--amount", help="amount received (default: full balance)")
    p.set_defaults(fn=cmd_mark_paid)

    for name, fn in (("chase", cmd_chase), ("status", cmd_status)):
        p = sub.add_parser(name)
        common(p)
        p.add_argument("--stripe-invoices", help="JSON from Stripe GET /v1/invoices?status=open")
        p.add_argument("--as-of", required=True, help="today, YYYY-MM-DD")
        p.add_argument("--json", action="store_true")
        if name == "chase":
            p.add_argument("--chase-log", help="Stripe mode: markdown chase history file")
            p.add_argument("--record", help="comma-separated invoice numbers whose drafts were approved and sent")
        p.set_defaults(fn=fn)

    args = ap.parse_args(argv)
    if args.cmd in ("chase", "status") and not args.ledger and not args.stripe_invoices:
        print("error: pass exactly one of --ledger or --stripe-invoices", file=sys.stderr)
        return 2
    try:
        return args.fn(args)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
