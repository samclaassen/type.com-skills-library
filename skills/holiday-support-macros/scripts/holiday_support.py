#!/usr/bin/env python3
"""
Holiday Support Macros: analyzer and macro generator.

Reads last season's helpdesk tickets, this season's written holiday policies
and, optionally, existing macros and daily orders (see DATA_CONTRACT.md).
Prints a Markdown report: last season's ticket mix, this season's ticket
forecast and staffing, policy gaps, stale or contradictory existing macros,
the holiday macros generated from the policies, an escalation map and a dated
checklist. With --out-dir it also writes macros.csv, macros_preview.md,
forecast.csv and tickets_classified.csv.

Every date, number, price, code and promise in a generated macro comes from
policies.json (or a Holiday Shipping Cutoff Checker --json result, whose
computed dates win where they disagree). When a macro needs a policy that is
missing, the macro is not generated and the gap is reported. Templates may not
carry facts or promise words of their own.

Local files only. Never calls an API, never sends a reply, never edits a helpdesk.

    python3 scripts/holiday_support.py --policies policies.json --tickets tickets.csv \
        [--macros macros_existing.csv] [--helpdesk NAME] [--orders orders.csv ...] [--prefer-plan] \
        [--cutoffs cutoffs.json] [--date-format mdy|dmy] \
        [--agents N] [--weekend-agents N] [--tickets-per-agent N] [--shift-hours H] \
        [--growth X] [--as-of YYYY-MM-DD] [--out-dir DIR] [--json analysis.json]

Python 3.8+, standard library only. Output is deterministic for a given input.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES = os.path.join(ROOT, "references", "ticket_rules.json")
DEFAULT_TEMPLATES = os.path.join(ROOT, "references", "macro_templates.json")

GAP_SHARE = 0.03           # a ticket type with >= 3% of last season's tickets needs a written policy
UNCLASSIFIED_WARN = 0.05   # more than this share unclassified -> tune the rules
BIG_TICKETS = 100_000      # above this many tickets, warn about run time and memory
NEGATORS = {"not", "no", "isn't", "wasn't", "never", "aren't", "weren't", "isnt", "wasnt"}
NEGATION_WORDS = 3         # a negator this many words before a keyword cancels the match
ALIGN_DAYS = 364           # same weekday last year, so Black Friday lines up with Black Friday
ORDER_WINDOW = 7           # tickets follow the orders of the week before
MAX_SPAN_DAYS = 184        # one holiday season of tickets, not a whole year
LIVE_LEAD = 7              # macros live by Black Friday - 7 days (Nov 20 in 2026) unless policies say otherwise
DECIDE_LEAD = 7            # missing policies decided this many days before the macros go live
REVIEW_LEAD = 3            # drafts reviewed this many days before the macros go live
FINAL_LEAD = 3             # final re-run: Black Friday - 3 days
FIX_NOW_DAYS = 7           # existing macros with wrong facts: fix within a week of the run
TOP_DAYS = 5
PLACEHOLDERS = ("customer_first_name", "order_number", "tracking_url", "agent_first_name")
SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2, "PLAN": 3}
SECTION_RANK = {"handoff": 0, "gaps": 1, "audit": 2, "staffing": 3, "tickets": 4}
# Generic placeholders each helpdesk can fill by itself (references/playbook.md §4). Anything else must be typed in.
HELPDESK_NAMES = {"gorgias": "Gorgias", "zendesk": "Zendesk", "help-scout": "Help Scout",
                  "shopify-inbox": "Shopify Inbox"}
HELPDESK_FILLS = {
    "gorgias": {"customer_first_name", "agent_first_name", "order_number", "tracking_url"},
    "zendesk": {"customer_first_name", "agent_first_name", "order_number", "tracking_url"},
    "help-scout": {"customer_first_name", "agent_first_name"},
    "shopify-inbox": set(),
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")

TYPE_LABELS = {
    "wismo": "Where is my order (WISMO)", "shipping_deadline": "Shipping deadline question",
    "discount_code": "Discount code not working", "price_adjustment": "Price adjustment request",
    "cancel_modify": "Cancel or change an order", "address_change": "Address change",
    "return_exchange": "Return or exchange", "damaged_defective": "Damaged or defective",
    "gift_options": "Gift options", "out_of_stock": "Out of stock or backorder",
}
TICKET_COLS = {
    "id": ("id", "ticket id", "ticket", "conversation id", "conversation", "number"),
    "created_at": ("created at", "created", "created date", "created datetime", "date", "requested", "created on"),
    "channel": ("channel", "via", "source", "type"),
    "subject": ("subject", "title"),
    "body": ("body", "message", "first message", "description", "preview", "text", "content"),
    "tags": ("tags", "tag", "labels"),
}
MACRO_COLS = {
    "name": ("name", "title", "macro", "macro name"),
    "body": ("body", "text", "content", "message", "response", "body text"),
    "tags": ("tags", "tag", "labels"),
}
ORDER_COLS = {"date": ("date", "day"), "orders": ("orders", "total orders", "order count", "projected orders")}


class InputError(Exception):
    pass


# ----------------------------------------------------------------------------- formatting
def fmt_day(d: dt.date) -> str:
    return f"{MONTHS[d.month - 1][:3]} {d.day}"


def fmt_wday(d: dt.date) -> str:
    return f"{WEEKDAYS[d.weekday()][:3]} {fmt_day(d)}"


def fmt_full(d: dt.date) -> str:
    return f"{fmt_day(d)}, {d.year}"


def fmt_range(a: dt.date, b: dt.date) -> str:
    return fmt_day(a) if a == b else f"{fmt_day(a)}–{fmt_day(b)}"


def fmt_span(a: dt.date, b: dt.date) -> str:
    """'Nov 15–Dec 31, 2025', or 'Nov 1, 2025–Jan 31, 2026' across a new year."""
    return f"{fmt_range(a, b)}, {a.year}" if a.year == b.year else f"{fmt_full(a)}–{fmt_full(b)}"


def short_label(label: str, prose: bool = False) -> str:
    """'Where is my order (WISMO)' -> 'WISMO'; in prose, other labels start lowercase."""
    m = re.search(r"\(([^)]+)\)$", label)
    if m:
        return m.group(1)
    return label[:1].lower() + label[1:] if prose else label


def sections_of(sources: list) -> str:
    out = []
    for s in sources:
        top = s.split(".")[0]
        if top not in out:
            out.append(top)
    return ", ".join(out)


def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def fmt_long(d: dt.date, season_year: int) -> str:
    """Customer-facing date: 'Monday, December 14' (the year is added outside the season year)."""
    s = f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}"
    return s if d.year == season_year else f"{s}, {d.year}"


def money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def rnd(x: float) -> int:
    return int(math.floor(x + 0.5))


def join_words(items: list, conj: str = "and") -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def duration(minutes: int) -> str:
    return plural(minutes // 60, "hour") if minutes % 60 == 0 else plural(minutes, "minute")


def norm_col(c: str) -> str:
    return re.sub(r"[\s_]+", " ", (c or "").strip().lower())


def norm_text(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def phrase_re(phrase: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(norm_text(phrase)) + r"(?![a-z0-9])")


def day_shift(d: dt.date, n: int) -> dt.date:
    return d + dt.timedelta(days=n)


def black_friday(year: int) -> dt.date:
    nov1 = dt.date(year, 11, 1)
    return nov1 + dt.timedelta(days=(3 - nov1.weekday()) % 7 + 22)


def by_date(d: dt.date, as_of: dt.date) -> str:
    if d < as_of:
        return f"now ({fmt_day(d)} has passed)"
    return fmt_day(d) if d.year == as_of.year else fmt_full(d)


# ----------------------------------------------------------------------------- loading
def load_json(path: str, label: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e.strerror or e}")
    except json.JSONDecodeError as e:
        raise InputError(f"{label} {path} is not valid JSON: line {e.lineno} column {e.colno}: {e.msg}")
    except UnicodeDecodeError:
        raise InputError(f"{label} {path} is not UTF-8 text")


def read_table(path: str, label: str) -> tuple:
    try:
        f = open(path, encoding="utf-8-sig", newline="")
    except OSError as e:
        raise InputError(f"cannot read {label} {path}: {e.strerror or e}")
    header, rows = None, []
    with f:
        try:
            rd = csv.reader(f)
            start = 1
            for rec in rd:
                line, start = start, rd.line_num + 1
                if header is None:
                    header = [norm_col(c) for c in rec]
                    continue
                if not any(c.strip() for c in rec):
                    continue
                rows.append((line, {header[j]: rec[j].strip() for j in range(min(len(header), len(rec)))}))
        except (csv.Error, UnicodeDecodeError) as e:
            raise InputError(f"{label} {path} is not a valid UTF-8 CSV: {e}")
    if not header or not any(header):
        raise InputError(f"{label} {path} is empty")
    return header, rows


def pick_cols(header: list, spec: dict) -> dict:
    return {k: next((a for a in aliases if a in header), None) for k, aliases in spec.items()}


def parse_iso(s, where: str) -> dt.date:
    if isinstance(s, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip()):
        try:
            return dt.date.fromisoformat(s.strip())
        except ValueError:
            pass
    raise InputError(f"{where} must be a date YYYY-MM-DD, got {s!r}")


TS_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:$|[T ])")
TS_US_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:$|[ ,T])")


def parse_ts(s: str, where: str, date_format: str | None = None) -> tuple:
    """(date, slash) where slash is (first, second) for a 12/05/2025-style date, else None."""
    m = TS_ISO_RE.match(s)
    slash = None
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = TS_US_RE.match(s)
        if not m:
            raise InputError(f"{where}: created_at must start with YYYY-MM-DD (or MM/DD/YYYY), got {s!r}")
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        mo, d = (b, a) if date_format == "dmy" else (a, b)
        slash = (a, b)
    try:
        return dt.date(y, mo, d), slash
    except ValueError:
        raise InputError(f"{where}: {s!r} is not a real date")


def load_tickets(path: str, date_format: str | None = None) -> list:
    header, rows = read_table(path, "tickets file")
    cols = pick_cols(header, TICKET_COLS)
    if not cols["created_at"]:
        raise InputError(f"tickets file {path}: no created_at column "
                         f"(also accepted: {', '.join(TICKET_COLS['created_at'][1:])})")
    if not cols["subject"] and not cols["body"]:
        raise InputError(f"tickets file {path}: needs a subject or a body column")
    tickets, seen, slashes = [], {}, []
    for line, r in rows:
        where = f"tickets file {path} line {line}"
        d, slash = parse_ts(r.get(cols["created_at"], ""), where, date_format)
        if slash:
            slashes.append((line, slash))
        tid = r.get(cols["id"], "") if cols["id"] else ""
        if tid and tid in seen:
            raise InputError(f"{where}: duplicate ticket id {tid!r} (first on line {seen[tid]})")
        if tid:
            seen[tid] = line
        raw_tags = r.get(cols["tags"], "") if cols["tags"] else ""
        tickets.append({
            "id": tid or f"line {line}", "date": d, "created_at": r.get(cols["created_at"], ""),
            "channel": r.get(cols["channel"], "") if cols["channel"] else "",
            "subject": r.get(cols["subject"], "") if cols["subject"] else "",
            "body": r.get(cols["body"], "") if cols["body"] else "",
            "tags": {t.strip().lower() for t in re.split(r"[,;|]", raw_tags) if t.strip()},
        })
    if not tickets:
        raise InputError(f"tickets file {path} has no tickets")
    if slashes and date_format is None and all(a <= 12 and b <= 12 for _, (a, b) in slashes):
        raise InputError(f"tickets file {path} line {slashes[0][0]}: every date like 12/05/2025 in this file could be "
                         "month/day or day/month. Pass --date-format mdy (12/05 = Dec 5) or --date-format dmy "
                         "(12/05 = May 12), or export dates as YYYY-MM-DD")
    first, last = min(t["date"] for t in tickets), max(t["date"] for t in tickets)
    if (last - first).days > MAX_SPAN_DAYS:
        raise InputError(f"tickets file {path} spans {fmt_full(first)} to {fmt_full(last)}; export one holiday "
                         "season (for example Nov 1 to Jan 31), not a whole year")
    return tickets


ORDERS_NUM_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def add_order(days: dict, src: dict, d: dt.date, n, where: str) -> None:
    if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
        raise InputError(f"{where}: orders must be a number of 0 or more, got {n!r}")
    if d in days:
        raise InputError(f"{where}: {d.isoformat()} already appears in {src[d]}. A date may appear only once: pass "
                         "last season's daily orders plus this season's projection (for example the BFCM Holiday "
                         "Budget Planner's --json plan), and remove this season's projection rows from the orders "
                         "file, or add --prefer-plan to let the planner's plan replace them")
    days[d], src[d] = float(n), where


def load_orders(paths: list, prefer_plan: bool = False) -> tuple:
    """(orders by day or None, notes). With prefer_plan, a planner --json plan replaces CSV rows on the same dates."""
    if not paths:
        return None, []
    csv_days, csv_src, plan_days, plan_src = {}, {}, {}, {}
    for path in paths:
        if path.lower().endswith(".json"):
            data = load_json(path, "orders file")
            rows = data.get("rows") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                raise InputError(f"orders file {path}: expected the BFCM Holiday Budget Planner's --json result "
                                 "(a \"rows\" list with \"date\" and \"orders\")")
            for k, r in enumerate(rows):
                where = f"orders file {path} rows[{k}]"
                if not isinstance(r, dict):
                    raise InputError(f"{where} must be an object")
                add_order(plan_days, plan_src, parse_iso(r.get("date"), f"{where}.date"), r.get("orders"), where)
            continue
        header, rows = read_table(path, "orders file")
        cols = pick_cols(header, ORDER_COLS)
        if not cols["date"] or not cols["orders"]:
            if "proj net sales base" in header:
                raise InputError(f"orders file {path}: this looks like the BFCM Holiday Budget Planner's --csv plan, "
                                 "which has net sales but no orders. Pass the planner's --json plan instead (it has "
                                 "orders per day), together with last season's daily orders")
            raise InputError(f"orders file {path}: needs date and orders columns")
        for line, r in rows:
            where = f"orders file {path} line {line}"
            raw = r.get(cols["orders"], "")
            if not ORDERS_NUM_RE.match(raw):
                raise InputError(f"{where}: orders must be a number such as 1234 or 1,234.5, got {raw!r}")
            add_order(csv_days, csv_src, parse_iso(r.get(cols["date"], ""), f"{where} date"),
                      float(raw.replace(",", "")), where)
    notes = []
    overlap = sorted(set(csv_days) & set(plan_days))
    if overlap:
        if not prefer_plan:
            d = overlap[0]
            add_order(csv_days, csv_src, d, plan_days[d], plan_src[d])  # raises the duplicate-date error
        for d in overlap:
            del csv_days[d]
        notes.append(f"--prefer-plan: the planner's plan replaced {plural(len(overlap), 'day')} of the orders CSV "
                     f"({fmt_range(overlap[0], overlap[-1])}).")
    csv_days.update(plan_days)
    return csv_days, notes


def load_existing_macros(path: str | None) -> list | None:
    if not path:
        return None
    header, rows = read_table(path, "macros file")
    cols = pick_cols(header, MACRO_COLS)
    if not cols["name"] or not cols["body"]:
        raise InputError(f"macros file {path}: needs name and body columns")
    out = []
    for line, r in rows:
        name = r.get(cols["name"], "")
        if not name:
            raise InputError(f"macros file {path} line {line}: macro name is blank")
        out.append({"name": name, "body": r.get(cols["body"], ""),
                    "tags": r.get(cols["tags"], "") if cols["tags"] else "", "line": line})
    return out


def load_rules(path: str) -> tuple:
    data = load_json(path, "rules file")
    where = f"rules file {path}"
    if not isinstance(data, dict) or not isinstance(data.get("types"), list) or not data["types"]:
        raise InputError(f"{where}: needs a non-empty \"types\" list")
    fallback = data.get("fallback", "other")
    if not isinstance(fallback, str) or not SLUG_RE.match(fallback):
        raise InputError(f"{where}: \"fallback\" must be a lowercase slug such as \"other\"")
    rules, seen = [], set()
    for i, t in enumerate(data["types"], start=1):
        if not isinstance(t, dict):
            raise InputError(f"{where}: types[{i}] must be an object")
        ty = t.get("type")
        if not isinstance(ty, str) or not SLUG_RE.match(ty):
            raise InputError(f"{where}: types[{i}].type must be a lowercase slug such as \"wismo\"")
        if ty in seen or ty == fallback:
            raise InputError(f"{where}: type {ty!r} is listed twice (or is the fallback)")
        seen.add(ty)
        lists = {}
        for key, need in (("keywords", True), ("first", False), ("not", False), ("tags", False)):
            v = t.get(key, [])
            if not isinstance(v, list) or (need and not v) or not all(isinstance(x, str) and x.strip() for x in v):
                raise InputError(f"{where}: {ty}.{key} must be a {'non-empty ' if need else ''}list of phrases")
            lists[key] = v
        flags = {}
        for key, default in (("negation", True), ("escalate", False)):
            v = t.get(key, default)
            if not isinstance(v, bool):
                raise InputError(f"{where}: {ty}.{key} must be true or false")
            flags[key] = v
        label = t.get("label") if isinstance(t.get("label"), str) and t.get("label").strip() else \
            TYPE_LABELS.get(ty, ty.replace("_", " ").capitalize())
        rules.append({"type": ty, "label": label, "kw": [(k, phrase_re(k)) for k in lists["keywords"]],
                      "first": [(k, phrase_re(k)) for k in lists["first"]],
                      "not": [phrase_re(k) for k in lists["not"]], "tags": {x.strip().lower() for x in lists["tags"]},
                      "negation": flags["negation"], "escalate": flags["escalate"]})
    return rules, fallback


# ----------------------------------------------------------------------------- policies
S, I, N, B, D, DL, SL = "str", "int", "num", "bool", "date", "dates", "strs"
SCHEMA = {
    "store": S + "!",
    "macros_live_by": D,
    "shipping": {
        "source": S, "ships_from": S, "same_day_cutoff": S, "ship_days": S, "closed_dates": DL, "deliver_by": D,
        "delivery_coverage_pct": N,
        "services": [{"name": S + "!", "last_order_date": D + "!", "checkout_rate": S}],
    },
    "bfcm_backlog": {"orders_from": D + "!", "orders_to": D + "!", "ships_within_business_days": I + "!",
                     "clears_by": D + "!"},
    "returns": {
        "standard_days": I, "counted_from": "enum:delivery|order",
        "holiday_window": {"orders_from": D + "!", "orders_to": D + "!", "return_by": D + "!"},
        "condition": S, "start_url": S, "refund_to": S, "return_label_fee": N, "exchanges": B, "final_sale": SL,
        "gift_returns": S,
    },
    "price_adjustment": {"offered": B + "!", "window_days": I, "how": S, "excludes": SL, "alternative": S},
    "discounts": {
        "one_code_per_order": B, "codes_combine_with_sale": B, "free_shipping_threshold": N,
        "free_shipping_combines": B,
        "sale": {"name": S + "!", "starts": D, "ends": D, "automatic": B, "excludes": SL},
        "codes": [{"code": S + "!", "offer": S, "starts": D, "ends": D}],
    },
    "order_changes": {"window_minutes": I, "how": S},
    "address_change": {"until": S, "after_shipping": S},
    "gift_options": {"gift_message": B, "gift_receipt": B, "gift_receipt_hides_prices": B, "gift_wrap": B,
                     "gift_wrap_price": N,
                     "ship_to_another_address": B, "e_gift_cards": S},
    "out_of_stock": {"backorders": B, "restock_alerts": B},
    "damaged_defective": {"report_within_days": I, "photo_required": B, "resolution": S, "return_required": B},
    "international": {"ships_to": S},
    "support_hours": {"regular": S, "holiday_note": S, "closed_dates": DL, "first_reply_hours": I,
                      "weekend_coverage": {"from": D + "!", "to": D + "!"}},
    "escalation": {"order_value_over": N, "refund_over": N, "owner": S, "keywords": SL},
}


def check_value(v, spec, path: str):
    where = f"policies: {path}"
    if isinstance(spec, dict):
        if not isinstance(v, dict):
            raise InputError(f"{where} must be an object")
        out = {}
        for k, sub in spec.items():
            req = isinstance(sub, str) and sub.endswith("!")
            sub_path = f"{path}.{k}" if path else k
            if v.get(k) is None:
                if req:
                    raise InputError(f"policies: {sub_path} is required")
                continue
            out[k] = check_value(v[k], sub.rstrip("!") if isinstance(sub, str) else sub, sub_path)
        return out
    if isinstance(spec, list):
        if not isinstance(v, list):
            raise InputError(f"{where} must be a list")
        return [check_value(x, spec[0], f"{path}[{i}]") for i, x in enumerate(v)]
    if spec == S:
        if not isinstance(v, str) or not v.strip():
            raise InputError(f"{where} must be non-empty text")
        return v.strip()
    if spec in (I, N):
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
        if not ok or (spec == I and not float(v).is_integer()):
            raise InputError(f"{where} must be a {'whole number' if spec == I else 'number'} of 0 or more, got {v!r}")
        return int(v) if spec == I else v
    if spec == B:
        if not isinstance(v, bool):
            raise InputError(f"{where} must be true or false, got {v!r}")
        return v
    if spec == D:
        return parse_iso(v, where)
    if spec in (DL, SL):
        if not isinstance(v, list):
            raise InputError(f"{where} must be a list")
        return [check_value(x, D if spec == DL else S, f"{path}[{i}]") for i, x in enumerate(v)]
    opts = spec.split(":", 1)[1].split("|")
    if v not in opts:
        raise InputError(f"{where} must be one of {', '.join(opts)}, got {v!r}")
    return v


def check_policy_logic(p: dict, season_year: int) -> None:
    def order(a, b, label_a, label_b):
        if a > b:
            raise InputError(f"policies: {label_a} ({a.isoformat()}) is after {label_b} ({b.isoformat()})")

    def in_season(d, label, years):
        if d.year not in years:
            raise InputError(f"policies: {label} is {d.isoformat()}, but the tickets point to the {season_year} "
                             f"holiday season; update policies.json for this season")

    sh = p.get("shipping", {})
    deliver = sh.get("deliver_by")
    if deliver:
        in_season(deliver, "shipping.deliver_by", (season_year,))
    names = set()
    for i, s in enumerate(sh.get("services", [])):
        in_season(s["last_order_date"], f"shipping.services[{i}].last_order_date", (season_year,))
        if deliver:
            order(s["last_order_date"], deliver, f"shipping.services[{i}].last_order_date", "shipping.deliver_by")
        if s["name"].lower() in names:
            raise InputError(f"policies: shipping.services lists {s['name']!r} twice")
        names.add(s["name"].lower())
    with_rate = [bool(s.get("checkout_rate")) for s in sh.get("services", [])]
    if any(with_rate) and not all(with_rate):
        raise InputError("policies: give every shipping.services entry a checkout_rate (the shipping option customers "
                         "pick at checkout, e.g. \"Standard\"), or leave it out of all of them")
    cov = sh.get("delivery_coverage_pct")
    if cov is not None and not 0 < cov <= 100:
        raise InputError(f"policies: shipping.delivery_coverage_pct must be more than 0 and at most 100, got {cov!r}")
    wc = p.get("support_hours", {}).get("weekend_coverage")
    if wc:
        order(wc["from"], wc["to"], "support_hours.weekend_coverage.from", "support_hours.weekend_coverage.to")
    bl = p.get("bfcm_backlog")
    if bl:
        in_season(bl["orders_from"], "bfcm_backlog.orders_from", (season_year,))
        order(bl["orders_from"], bl["orders_to"], "bfcm_backlog.orders_from", "bfcm_backlog.orders_to")
        order(bl["orders_to"], bl["clears_by"], "bfcm_backlog.orders_to", "bfcm_backlog.clears_by")
    hw = p.get("returns", {}).get("holiday_window")
    if hw:
        in_season(hw["orders_from"], "returns.holiday_window.orders_from", (season_year,))
        order(hw["orders_from"], hw["orders_to"], "returns.holiday_window.orders_from", "returns.holiday_window.orders_to")
        order(hw["orders_to"], hw["return_by"], "returns.holiday_window.orders_to", "returns.holiday_window.return_by")
    pa = p.get("price_adjustment")
    if pa and pa["offered"] and "window_days" not in pa:
        raise InputError("policies: price_adjustment.window_days is required when price_adjustment.offered is true")
    disc = p.get("discounts", {})
    sale = disc.get("sale")
    if sale and "starts" in sale and "ends" in sale:
        in_season(sale["starts"], "discounts.sale.starts", (season_year,))
        order(sale["starts"], sale["ends"], "discounts.sale.starts", "discounts.sale.ends")
    codes = set()
    for i, c in enumerate(disc.get("codes", [])):
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", c["code"]):
            raise InputError(f"policies: discounts.codes[{i}].code {c['code']!r} must be letters, digits, - or _")
        if c["code"].upper() in codes:
            raise InputError(f"policies: discounts.codes lists {c['code']!r} twice")
        codes.add(c["code"].upper())
        if "starts" in c and "ends" in c:
            order(c["starts"], c["ends"], f"discounts.codes[{i}].starts", f"discounts.codes[{i}].ends")


CUTOFF_TOP = {"account", "all_cutoffs_passed", "as_of", "backlog", "calendar", "checkout_rates", "counts",
              "coverage_by_order_date", "cutoff", "fixes", "issues", "promises", "services", "target_date",
              "threshold_pct", "verdict", "zone_mix"}
CUTOFF_REQUIRED = ("services", "checkout_rates", "target_date", "threshold_pct", "backlog")
CUTOFF_SVC_KEYS = {"name", "carrier", "service", "checkout_rate", "last_safe_order_date", "last_safe_by_threshold",
                   "transit_last_safe", "ship_by_date", "basis", "needs_cutoff_time", "peak_extra_days",
                   "transit_source", "ship_by_source"}
CUTOFF_RATE_KEYS = {"rate", "last_safe_order_date", "services", "extend_with", "extend_to"}
CUTOFF_BACKLOG_KEYS = {"modeled", "bfcm_window", "bfcm_ship_within_business_days", "bfcm_clears",
                       "bfcm_orders_forecast", "periods", "ship_days"}


def load_cutoffs(path: str) -> dict:
    """A Holiday Shipping Cutoff Checker --json result (schema in DATA_CONTRACT.md section 7).

    Strict on purpose: a missing or unknown field exits 2 instead of being dropped, so a changed checker schema
    can't silently put wrong dates in customer macros."""
    data = load_json(path, "cutoffs file")
    where = f"cutoffs file {path}"
    if not isinstance(data, dict):
        raise InputError(f"{where}: expected the Holiday Shipping Cutoff Checker's --json result (a JSON object)")

    def unknown_keys(obj, known, label):
        extra = sorted(k for k in obj if k not in known)
        if extra:
            hint = ""
            if label == "" and ({"deliver_by", "cutoffs", "results"} & set(extra)):
                hint = (" This looks like a hand-written cutoffs file; put those dates in policies.json, or pass the "
                        "Cutoff Checker's own --json file unchanged.")
            raise InputError(f"{where}: unknown field{'s' if len(extra) > 1 else ''} "
                             f"{', '.join((label + '.' if label else '') + k for k in extra)}. This skill reads the "
                             f"Holiday Shipping Cutoff Checker --json schema and won't guess at other fields.{hint}")

    def opt_date(v, label):
        return None if v is None else parse_iso(v, f"{where}: {label}")

    unknown_keys(data, CUTOFF_TOP, "")
    missing = [k for k in CUTOFF_REQUIRED if data.get(k) is None]
    if missing:
        raise InputError(f"{where}: missing {', '.join(missing)}. Run the Cutoff Checker with --json and pass that "
                         "file unchanged")
    target = parse_iso(data["target_date"], f"{where}: target_date")
    thr = data["threshold_pct"]
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not math.isfinite(thr) or not 0 < thr <= 100:
        raise InputError(f"{where}: threshold_pct must be a number above 0 and at most 100, got {thr!r}")

    svcs = data["services"]
    if not isinstance(svcs, list) or not svcs:
        raise InputError(f"{where}: services must be a non-empty list")
    services, by_name, no_date = [], {}, []
    for i, s in enumerate(svcs):
        lab = f"services[{i}]"
        if not isinstance(s, dict):
            raise InputError(f"{where}: {lab} must be an object")
        unknown_keys(s, CUTOFF_SVC_KEYS, lab)
        name = s.get("name")
        if name is None and isinstance(s.get("carrier"), str) and isinstance(s.get("service"), str):
            name = f"{s['carrier'].strip()} {s['service'].strip()}"
        if name is None:
            name = s.get("service")
        if not isinstance(name, str) or not name.strip():
            raise InputError(f"{where}: {lab}.name is missing (the full service name, e.g. \"UPS Ground\")")
        name = name.strip()
        rate = s.get("checkout_rate")
        if not isinstance(rate, str) or not rate.strip():
            raise InputError(f"{where}: {lab}.checkout_rate is missing (the shipping option customers pick for "
                             f"{name!r})")
        if "last_safe_order_date" not in s:
            raise InputError(f"{where}: {lab}.last_safe_order_date is missing")
        if name.lower() in by_name:
            raise InputError(f"{where}: services lists {name!r} twice")
        svc = {"name": name, "checkout_rate": rate.strip(),
               "last_order_date": opt_date(s["last_safe_order_date"], f"{lab}.last_safe_order_date")}
        by_name[name.lower()] = svc
        if svc["last_order_date"] is None:
            no_date.append(name)
        else:
            services.append(svc)

    rates_in = data["checkout_rates"]
    if not isinstance(rates_in, list) or not rates_in:
        raise InputError(f"{where}: checkout_rates must be a non-empty list")
    rates, placed = [], {}
    for i, g in enumerate(rates_in):
        lab = f"checkout_rates[{i}]"
        if not isinstance(g, dict):
            raise InputError(f"{where}: {lab} must be an object")
        unknown_keys(g, CUTOFF_RATE_KEYS, lab)
        rate = g.get("rate")
        if not isinstance(rate, str) or not rate.strip():
            raise InputError(f"{where}: {lab}.rate is missing")
        members = g.get("services")
        if not isinstance(members, list) or not members or not all(isinstance(x, str) for x in members):
            raise InputError(f"{where}: {lab}.services must list the service names in {rate!r}")
        if "last_safe_order_date" not in g:
            raise InputError(f"{where}: {lab}.last_safe_order_date is missing")
        gdate = opt_date(g["last_safe_order_date"], f"{lab}.last_safe_order_date")
        opt_date(g.get("extend_to"), f"{lab}.extend_to")
        if g.get("extend_with") is not None and not isinstance(g["extend_with"], str):
            raise InputError(f"{where}: {lab}.extend_with must be a service name or null")
        dates = []
        for n in members:
            svc = by_name.get(n.strip().lower())
            if not svc:
                raise InputError(f"{where}: {lab} lists {n!r}, which is not in services")
            if svc["checkout_rate"] != rate.strip():
                raise InputError(f"{where}: {n!r} is under checkout rate {rate!r} in checkout_rates but "
                                 f"{svc['checkout_rate']!r} in services")
            if n.strip().lower() in placed:
                raise InputError(f"{where}: {n!r} is listed under two checkout rates")
            placed[n.strip().lower()] = rate
            if svc["last_order_date"]:
                dates.append(svc["last_order_date"])
        if gdate != (min(dates) if dates else None):
            raise InputError(f"{where}: {lab} ({rate}) has last_safe_order_date {g['last_safe_order_date']!r}, but "
                             f"the earliest date of its services is {min(dates).isoformat() if dates else None!r}")
        rates.append({"rate": rate.strip(), "last_order_date": gdate, "services": members})
    unplaced = [s["name"] for k, s in by_name.items() if k not in placed]
    if unplaced:
        raise InputError(f"{where}: {join_words(unplaced)} {'is' if len(unplaced) == 1 else 'are'} in services but "
                         "under no checkout rate in checkout_rates")

    bl = data["backlog"]
    if not isinstance(bl, dict):
        raise InputError(f"{where}: backlog must be an object")
    unknown_keys(bl, CUTOFF_BACKLOG_KEYS, "backlog")
    if not isinstance(bl.get("modeled"), bool):
        raise InputError(f"{where}: backlog.modeled must be true or false")
    win = bl.get("bfcm_window")
    if not isinstance(win, list) or len(win) != 2:
        raise InputError(f"{where}: backlog.bfcm_window must be [first order date, last order date]")
    w0, w1 = parse_iso(win[0], f"{where}: backlog.bfcm_window[0]"), parse_iso(win[1], f"{where}: backlog.bfcm_window[1]")
    days = bl.get("bfcm_ship_within_business_days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 0):
        raise InputError(f"{where}: backlog.bfcm_ship_within_business_days must be a whole number or null, got {days!r}")
    clears = opt_date(bl.get("bfcm_clears"), "backlog.bfcm_clears")
    if bl["modeled"] is False and (days is not None or clears is not None):
        raise InputError(f"{where}: backlog.modeled is false but the BFCM backlog has values")
    backlog = None
    if not bl["modeled"]:
        state = "not modeled"
    elif clears is None:
        state = "no backlog"
    elif days is None:
        state = "partial"
    else:
        state = "backlog"
        backlog = {"orders_from": w0, "orders_to": w1, "ships_within_business_days": days, "clears_by": clears}

    label = None
    cut = data.get("cutoff")
    if cut is not None:
        if not isinstance(cut, dict) or not isinstance(cut.get("label"), str) or not cut["label"].strip():
            raise InputError(f"{where}: cutoff must be an object with a label such as \"1:00 pm ET\"")
        label = cut["label"].strip()
    as_of = opt_date(data.get("as_of"), "as_of")
    return {"services": services, "rates": rates, "no_date": no_date, "deliver_by": target, "coverage": thr,
            "backlog": backlog, "backlog_state": state, "clears": clears, "window": (w0, w1),
            "cutoff_label": label, "as_of": as_of}


def cutoff_key(label):
    """Comparable (minutes after midnight, time-zone word) for a same-day cutoff such as "1 pm ET", "1:00 pm ET" or
    "13:00 ET"; None when the text has no recognisable time."""
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?|noon)?(?![\w:])\s*([A-Za-z]{1,5})?", label or "",
                  re.I)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    ap = (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    tz = (m.group(4) or "").upper()
    tz = {"EST": "ET", "EDT": "ET", "CST": "CT", "CDT": "CT", "MST": "MT", "MDT": "MT", "PST": "PT",
          "PDT": "PT"}.get(tz, tz)
    return h * 60 + mi, tz


def rate_view(services: list) -> list:
    """What customers see: one (option, last order date) per checkout rate (the earliest date of its carriers), or
    per service when the policy names no checkout rates."""
    best = {}
    for s in services:
        key = s.get("checkout_rate") or s["name"]
        if key not in best or s["last_order_date"] < best[key]:
            best[key] = s["last_order_date"]
    return sorted(best.items(), key=lambda kv: (kv[1], kv[0]))


def backlog_text(b: dict) -> str:
    return (f"orders {fmt_range(b['orders_from'], b['orders_to'])} ship within "
            f"{plural(b['ships_within_business_days'], 'business day')}, caught up by {fmt_wday(b['clears_by'])}")


def load_policies(path: str, cutoffs_path: str | None, season_year: int) -> tuple:
    """(policies, notes, conflicts). With a cutoffs file, its computed dates win over policies.json, and every
    disagreement becomes a conflict (a warning in the report that lists both values)."""
    raw = load_json(path, "policies file")
    if not isinstance(raw, dict):
        raise InputError(f"policies file {path} must be a JSON object")
    p = check_value(raw, SCHEMA, "")
    notes, conflicts = [], []
    if cutoffs_path:
        c = load_cutoffs(cutoffs_path)
        fname = os.path.basename(cutoffs_path)
        sh = p.setdefault("shipping", {})

        def conflict(what, pol, chk, fix):
            conflicts.append({"what": what, "policy": pol, "checker": chk, "fix": fix, "file": fname})

        def view_text(v):
            return ", ".join(f"{k} {fmt_wday(d)}" for k, d in v)

        old, new = rate_view(sh.get("services", [])), rate_view(c["services"])
        if old and old != new:
            conflict("last order dates", view_text(old), view_text(new),
                     "Update shipping.services in policies.json (and your published shipping page) to the Cutoff "
                     "Checker's dates")
        sh["services"] = c["services"]
        if sh.get("deliver_by") and sh["deliver_by"] != c["deliver_by"]:
            conflict("delivery date", fmt_wday(sh["deliver_by"]), fmt_wday(c["deliver_by"]),
                     "Update shipping.deliver_by in policies.json")
        sh["deliver_by"] = c["deliver_by"]
        cov = sh.get("delivery_coverage_pct")
        if cov is not None and float(cov) != float(c["coverage"]):
            conflict("delivery coverage", f"{cov:g}% of customers", f"{c['coverage']:g}% of customers",
                     "Update shipping.delivery_coverage_pct in policies.json")
        sh["delivery_coverage_pct"] = c["coverage"]
        # QA re-review S-S1: the same-day cutoff time is reconciled like the dates. The checker's time wins; a
        # policies.json time that reads differently (after normalising "1 pm ET" = "1:00 pm ET") is a WARNING.
        if c["cutoff_label"]:
            pol_cut = sh.get("same_day_cutoff")
            agrees = bool(pol_cut) and cutoff_key(pol_cut) is not None and cutoff_key(pol_cut) == cutoff_key(
                c["cutoff_label"])
            if pol_cut and not agrees:
                conflict("same-day cutoff", f"orders placed by {pol_cut} ship the same day",
                         f"a {c['cutoff_label']} cutoff",
                         "Update shipping.same_day_cutoff in policies.json (and your published shipping page) to "
                         "the Cutoff Checker's time, or fix the warehouse cutoff the checker was given")
            if not agrees:
                sh["same_day_cutoff"] = c["cutoff_label"]
        if c["no_date"]:
            notes.append(f"{fname} has no safe order date for {join_words(c['no_date'])}; "
                         f"{'it is' if len(c['no_date']) == 1 else 'they are'} left out of the macros.")
        pol = p.get("bfcm_backlog")
        fix_bl = ("Update bfcm_backlog in policies.json and the shipping note on your site to the Cutoff Checker's "
                  "numbers (they are computed from your order forecast and warehouse capacity)")
        if c["backlog_state"] == "backlog":
            cb = c["backlog"]
            if pol and pol != cb:
                conflict("BFCM backlog", backlog_text(pol), backlog_text(cb), fix_bl)
            elif not pol:
                notes.append(f"The BFCM backlog macro uses the backlog computed in {fname} (policies.json has no "
                             "bfcm_backlog).")
            p["bfcm_backlog"] = cb
        elif c["backlog_state"] == "partial":
            if pol and pol["clears_by"] < c["clears"]:
                conflict("BFCM backlog clear date", fmt_wday(pol["clears_by"]), fmt_wday(c["clears"]), fix_bl)
                pol["clears_by"] = c["clears"]
            elif not pol:
                notes.append(f"{fname} says the BFCM backlog clears {fmt_wday(c['clears'])} but gives no ship-within "
                             "days; add bfcm_backlog to policies.json to generate the BFCM backlog macro.")
        elif c["backlog_state"] == "no backlog":
            notes.append(f"The Cutoff Checker found no BFCM shipping backlog"
                         + ("; the BFCM backlog macro still uses policies.json's more cautious dates." if pol else "."))
        else:
            notes.append(f"{fname} has no backlog model (the Cutoff Checker ran without a demand file), so the BFCM "
                         "backlog comes from policies.json.")
        src = f" (Holiday Shipping Cutoff Checker, as of {c['as_of'].isoformat()})" if c["as_of"] else ""
        notes.insert(0, f"Shipping dates, the delivery date and coverage come from {fname}{src}; where policies.json "
                        "disagrees, the macros use the Cutoff Checker's values.")
    check_policy_logic(p, season_year)
    return p, notes, conflicts


# ----------------------------------------------------------------------------- facts
def build_facts(p: dict, season_year: int) -> tuple:
    """Policy facts that templates may use. value None = missing (a gap); na=True = not applicable."""
    F, DF = {}, {}

    def put(name, sources, value, na=False):
        F[name] = {"value": value, "sources": sources, "na": na and value is None}

    def L(d):
        return fmt_long(d, season_year)

    def yes_no(v, yes, no):
        return None if v is None else (yes if v else no)

    sh = p.get("shipping", {})
    put("same_day_cutoff", ["shipping.same_day_cutoff"], sh.get("same_day_cutoff"))
    closed = sh.get("closed_dates")
    put("closed_dates", ["shipping.closed_dates"], join_words([L(d) for d in sorted(closed)]) if closed else None)
    deliver = sh.get("deliver_by")
    put("deliver_by", ["shipping.deliver_by"], L(deliver) if deliver else None)
    DF["deliver_by_date"] = deliver
    # Customers pick a checkout rate, not a carrier: each rate's date is the earliest of its carriers' dates.
    opts = rate_view(sh.get("services", []))
    src = ["shipping.services"]
    if opts:
        last = opts[-1][1]
        earlier = [(k, d) for k, d in opts if d < last]
        put("cutoff_lines", src, "\n".join(f"- {k}: order by {L(d)}" for k, d in opts))
        put("checkout_options", src, join_words([k for k, _ in opts], "or"))
        put("last_service", src, join_words([k for k, d in opts if d == last], "or"))
        put("last_cutoff", src, L(last))
        put("earlier_services", src, join_words([k for k, _ in earlier]) if earlier else None, na=True)
        DF["last_cutoff_date"] = last
        DF["before_last_cutoff_date"] = earlier[-1][1] if earlier else None
        DF["first_cutoff_date"] = opts[0][1]
    else:
        for n in ("cutoff_lines", "checkout_options", "last_service", "last_cutoff", "earlier_services"):
            put(n, src, None)
        DF.update(last_cutoff_date=None, before_last_cutoff_date=None, first_cutoff_date=None)
    cov = sh.get("delivery_coverage_pct")
    put("delivery_coverage", ["shipping.delivery_coverage_pct"], f"{cov:g}%" if cov is not None else None)

    bl = p.get("bfcm_backlog")
    src = ["bfcm_backlog"]
    put("backlog_orders", src, f"from {L(bl['orders_from'])} to {L(bl['orders_to'])}" if bl else None)
    put("backlog_ship_days", src, plural(bl["ships_within_business_days"], "business day") if bl else None)
    put("backlog_clears", src, L(bl["clears_by"]) if bl else None)
    DF["backlog_from_date"] = bl["orders_from"] if bl else None
    DF["backlog_clears_date"] = bl["clears_by"] if bl else None
    # The same-day promise never stands alone while a BFCM backlog exists (QA re-review B-3): orders placed in the
    # backlog window ship within the backlog's business days, not the same day.
    sdc = sh.get("same_day_cutoff")
    if sdc and bl:
        # Orders placed after the window but before the backlog clears also wait, so same-day shipping is only
        # promised again from the clear date.
        rule = (f"Orders placed by {sdc} on a business day usually ship the same day, but not during our Black Friday "
                f"rush: orders placed from {L(bl['orders_from'])} to {L(bl['orders_to'])} ship within "
                f"{plural(bl['ships_within_business_days'], 'business day')}, and same-day shipping resumes once "
                f"we're caught up, which we expect by {L(bl['clears_by'])}")
    elif sdc:
        rule = f"Orders placed by {sdc} on a business day ship the same day"
    else:
        rule = None
    put("same_day_rule", ["shipping.same_day_cutoff", "bfcm_backlog"], rule)

    r = p.get("returns", {})
    days = r.get("standard_days")
    frm = {"delivery": " of delivery", "order": " of the order date"}.get(r.get("counted_from"), "")
    put("return_window", ["returns.standard_days"], plural(days, "day") + frm if days is not None else None)
    hw = r.get("holiday_window")
    src = ["returns.holiday_window"]
    put("has_holiday_window", src, bool(hw) if "returns" in p else None)
    put("holiday_from", src, L(hw["orders_from"]) if hw else None)
    put("holiday_to", src, L(hw["orders_to"]) if hw else None)
    put("holiday_return_by", src, L(hw["return_by"]) if hw else None)
    DF["holiday_return_by_date"] = hw["return_by"] if hw else None
    put("return_condition", ["returns.condition"], r.get("condition"))
    put("return_start", ["returns.start_url"], r.get("start_url"))
    put("refund_to", ["returns.refund_to"], r.get("refund_to"))
    put("refund_rule", ["returns.refund_to"], f"Refunds go back to {r['refund_to']}." if r.get("refund_to") else None)
    fee = r.get("return_label_fee")
    put("return_shipping", ["returns.return_label_fee"],
        None if fee is None else ("Return shipping is free." if fee == 0 else f"Return shipping costs {money(fee)}."))
    put("exchange_rule", ["returns.exchanges"], yes_no(
        r.get("exchanges"), "Want something else instead? We're happy to exchange it.",
        "We don't do direct exchanges, so please return the item and place a new order."))
    fs = r.get("final_sale")
    put("final_sale", ["returns.final_sale"], f"Items from {join_words(fs)} are final sale." if fs else None)
    put("gift_returns", ["returns.gift_returns"], r.get("gift_returns"))

    pa = p.get("price_adjustment")
    src = ["price_adjustment"]
    put("pa_offered", ["price_adjustment.offered"], pa["offered"] if pa else None)
    wd = pa.get("window_days") if pa else None
    put("pa_window", ["price_adjustment.window_days"], plural(wd, "day") if wd is not None else None)
    put("pa_how", ["price_adjustment.how"], pa.get("how") if pa else None)
    put("pa_excludes", ["price_adjustment.excludes"], join_words(pa["excludes"]) if pa and pa.get("excludes") else None)
    put("pa_alternative", ["price_adjustment.alternative"], pa.get("alternative") if pa else None)

    disc = p.get("discounts", {})
    sale = disc.get("sale")
    put("one_code_rule", ["discounts.one_code_per_order"], yes_no(disc.get("one_code_per_order"),
        "Only one discount code can be used per order.", "You can use more than one discount code on an order."))
    comb = disc.get("codes_combine_with_sale")
    target = f"the {sale['name']}" if sale else "sale prices"
    put("sale_stack_rule", ["discounts.codes_combine_with_sale"],
        yes_no(comb, f"Discount codes can be combined with {target}.", f"Discount codes can't be combined with {target}."))
    put("sale_name", ["discounts.sale"], sale["name"] if sale else None)
    have_dates = bool(sale and "starts" in sale and "ends" in sale)
    put("sale_dates", ["discounts.sale.starts", "discounts.sale.ends"],
        f"from {L(sale['starts'])} to {L(sale['ends'])}" if have_dates else None)
    put("sale_auto_rule", ["discounts.sale.automatic"], yes_no(sale.get("automatic") if sale else None,
        "The sale price comes off automatically at checkout, so you don't need a code for it.",
        "Use the sale code at checkout to get the sale price."))
    put("sale_excludes", ["discounts.sale.excludes"], join_words(sale["excludes"]) if sale and sale.get("excludes") else None)
    DF["sale_start_date"] = sale.get("starts") if sale else None
    DF["sale_end_date"] = sale.get("ends") if sale else None
    th = disc.get("free_shipping_threshold")
    fc = disc.get("free_shipping_combines")
    if th is None:
        fsr = None
    elif fc is None:
        fsr = f"Orders of {money(th)} or more ship free."
    elif fc:
        fsr = f"Free shipping on orders of {money(th)} or more still applies on top of a code or the sale."
    else:
        fsr = f"Free shipping on orders of {money(th)} or more can't be combined with a code or the sale."
    put("free_shipping_rule", ["discounts.free_shipping_threshold"], fsr)

    oc = p.get("order_changes", {})
    wm = oc.get("window_minutes")
    put("edit_window", ["order_changes.window_minutes"], duration(wm) if wm else None)
    put("edit_how", ["order_changes.how"], oc.get("how"))
    ac = p.get("address_change", {})
    put("address_until", ["address_change.until"], ac.get("until"))
    put("address_after", ["address_change.after_shipping"], ac.get("after_shipping"))

    g = p.get("gift_options", {})
    put("gift_message_rule", ["gift_options.gift_message"], yes_no(g.get("gift_message"),
        "You can add a gift message to your order.", "We can't include gift messages."))
    hides = " Gift receipts don't show prices." if g.get("gift_receipt_hides_prices") else ""
    put("gift_receipt_rule", ["gift_options.gift_receipt"], yes_no(g.get("gift_receipt"),
        "We can include a gift receipt." + hides, "We don't offer gift receipts."))
    gw = g.get("gift_wrap")
    if gw is None:
        gwr = None
    elif not gw:
        gwr = "We don't offer gift wrapping."
    elif g.get("gift_wrap_price") is None:
        gwr = "Gift wrapping is available at checkout."
    elif g["gift_wrap_price"] == 0:
        gwr = "Gift wrapping is free at checkout."
    else:
        gwr = f"Gift wrapping is available at checkout for {money(g['gift_wrap_price'])}."
    put("gift_wrap_rule", ["gift_options.gift_wrap"], gwr)
    put("ship_to_other_rule", ["gift_options.ship_to_another_address"], yes_no(g.get("ship_to_another_address"),
        "You can ship to a different address from your billing address: just enter the recipient's address at checkout.",
        "We can only ship to the billing address."))
    put("e_gift_cards", ["gift_options.e_gift_cards"], g.get("e_gift_cards"))

    oos = p.get("out_of_stock", {})
    put("backorder_rule", ["out_of_stock.backorders"], yes_no(oos.get("backorders"),
        "You can still order sold-out items, and they ship as soon as they're back in stock.",
        "When an item sells out, we can't take orders for it until it's back in stock."))
    put("restock_rule", ["out_of_stock.restock_alerts"], yes_no(oos.get("restock_alerts"),
        "You can sign up for a restock alert, and we'll let you know when it's back.",
        "We don't send restock alerts."))

    dd = p.get("damaged_defective", {})
    rd = dd.get("report_within_days")
    put("damage_days", ["damaged_defective.report_within_days"], plural(rd, "day") if rd is not None else None)
    ph = dd.get("photo_required")
    put("damage_photo", ["damaged_defective.photo_required"],
        "Could you reply with a photo of the item and the box it came in?" if ph else None, na=ph is False)
    put("damage_resolution", ["damaged_defective.resolution"], dd.get("resolution"))
    put("damage_return", ["damaged_defective.return_required"], yes_no(
        dd.get("return_required"), "We'll need the damaged item back, and we'll send you instructions.",
        "You don't need to send the damaged item back."))

    esc = p.get("escalation", {})
    ro = esc.get("refund_over")
    put("refund_over", ["escalation.refund_over"], money(ro) if ro is not None else None)
    put("escalation_owner", ["escalation.owner"], esc.get("owner"))
    put("ships_to", ["international.ships_to"], p.get("international", {}).get("ships_to"))
    su = p.get("support_hours", {})
    put("support_regular", ["support_hours.regular"], su.get("regular"))
    put("support_holiday_note", ["support_hours.holiday_note"], su.get("holiday_note"))
    sc = su.get("closed_dates")
    put("support_closed", ["support_hours.closed_dates"], join_words([L(d) for d in sorted(sc)]) if sc else None)
    fr = su.get("first_reply_hours")
    put("first_reply", ["support_hours.first_reply_hours"], plural(fr, "hour") if fr is not None else None)
    return F, DF


# ----------------------------------------------------------------------------- templates
FACT_RE = re.compile(r"\[\[\s*([A-Za-z0-9_]+)\s*\]\]")
PH_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
FACTLIKE_RE = re.compile(r"[0-9$%€£]")
MONTH_WORD_RE = re.compile(r"\b(January|February|March|April|June|July|August|September|October|November|December|"
                           r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b")
USE_RE = re.compile(r"^([a-z_]+)([+-]\d+)?$")
# Promise words a template line may not carry by itself: they must come from a policy fact.
PROMISE_WORD_RE = re.compile(r"\b(guarantee[sd]?|guaranteeing|refund\w*|free|always|never|christmas|holiday\w*|"
                             r"arrive\s+by|in\s+full)\b", re.I)


def load_templates(path: str, facts: dict, date_facts: dict, types: set) -> list:
    data = load_json(path, "templates file")
    lst = data.get("templates") if isinstance(data, dict) else None
    if not isinstance(lst, list) or not lst:
        raise InputError(f"templates file {path}: needs a non-empty \"templates\" list")
    out, ids = [], set()
    for i, t in enumerate(lst, start=1):
        where = f"templates file {path}: template {i}"
        if not isinstance(t, dict):
            raise InputError(f"{where} must be an object")
        tid = t.get("id")
        if not isinstance(tid, str) or not SLUG_RE.match(tid) or tid in ids:
            raise InputError(f"{where}: id must be a unique lowercase slug")
        ids.add(tid)
        where = f"templates file {path}: template {tid!r}"
        name, ttype, body = t.get("name"), t.get("ticket_type"), t.get("body")
        if not isinstance(name, str) or not name.strip():
            raise InputError(f"{where}: name is required")
        if ttype not in types:
            raise InputError(f"{where}: ticket_type {ttype!r} is not a type in the rules file")
        if not isinstance(body, list) or not body or not all(isinstance(x, str) for x in body):
            raise InputError(f"{where}: body must be a non-empty list of lines")
        tags = t.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(x, str) and re.match(r"^[a-z0-9-]+$", x) for x in tags):
            raise InputError(f"{where}: tags must be lowercase words joined by hyphens")
        for k, text in [("name", name)] + [(f"body line {n}", x) for n, x in enumerate(body, start=1)]:
            for m in PH_RE.finditer(text):
                if m.group(1) not in PLACEHOLDERS:
                    raise InputError(f"{where}: {k} uses {{{{{m.group(1)}}}}}; allowed placeholders: "
                                     + ", ".join("{{%s}}" % x for x in PLACEHOLDERS))
            for m in FACT_RE.finditer(text):
                if m.group(1) not in facts:
                    raise InputError(f"{where}: {k} uses unknown fact [[{m.group(1)}]] (see DATA_CONTRACT.md section 5)")
            bare = FACT_RE.sub(" ", PH_RE.sub(" ", text))
            hit = FACTLIKE_RE.search(bare) or MONTH_WORD_RE.search(bare)
            if hit:
                raise InputError(f"{where}: {k} contains {hit.group(0)!r}. Templates hold wording only: put dates, "
                                 "numbers, prices and codes in policies.json and use them as [[fact]]")
            hit = PROMISE_WORD_RE.search(bare) if k != "name" else None
            if hit:
                raise InputError(f"{where}: {k} contains the promise {hit.group(0)!r}. Templates hold wording only: "
                                 "a promise (guarantee, refund, free, always, never, Christmas, holiday, arrive by, "
                                 "in full) must come from policies.json as a [[fact]]")
        when = t.get("when")
        if when is not None and (not isinstance(when, str) or when.lstrip("!") not in facts):
            raise InputError(f"{where}: when must name a fact, optionally with ! in front")
        req_any = t.get("requires_any", [])
        if not isinstance(req_any, list) or any(x not in facts for x in req_any):
            raise InputError(f"{where}: requires_any must list known facts")
        if_missing = t.get("if_missing", "info")
        if if_missing not in ("info", "warning"):
            raise InputError(f"{where}: if_missing must be \"info\" or \"warning\"")
        replaces = t.get("replaces", [])
        if not isinstance(replaces, list) or not all(isinstance(x, str) and x.strip() for x in replaces):
            raise InputError(f"{where}: replaces must be a list of words or phrases")
        use = {}
        for key in ("use_from", "use_until"):
            v = t.get(key)
            cands = [] if v is None else ([v] if isinstance(v, str) else v)
            if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
                raise InputError(f"{where}: {key} must be a date fact name or a list of them")
            parsed = []
            for c in cands:
                m = USE_RE.match(c)
                if not m or m.group(1) not in date_facts:
                    raise InputError(f"{where}: {key} {c!r} is not a date fact (optionally +N/-N days)")
                parsed.append((m.group(1), int(m.group(2) or 0)))
            use[key] = parsed
        out.append({"id": tid, "name": name.strip(), "type": ttype, "tags": tags, "when": when,
                    "requires_any": req_any, "if_missing": if_missing, "replaces": [phrase_re(x) for x in replaces],
                    "use_from": use["use_from"], "use_until": use["use_until"], "body": body})
    return out


def first_date(cands: list, date_facts: dict) -> dt.date | None:
    for name, off in cands:
        if date_facts.get(name):
            return day_shift(date_facts[name], off)
    return None


def render_templates(templates: list, facts: dict, date_facts: dict, season_year: int, fallback: str) -> list:
    out = []
    for t in templates:
        type_tag = "general" if t["type"] == fallback else t["type"].replace("_", "-")
        m = {"id": t["id"], "template_name": t["name"], "name": f"Holiday {season_year} - {t['name']}",
             "type": t["type"], "tags": [f"holiday-{season_year}", type_tag] + t["tags"],
             "status": "generated", "missing": [], "body": "", "sources": [], "use_from": None, "use_until": None,
             "if_missing": t["if_missing"], "replaces": t["replaces"]}
        used = []
        if t["when"]:
            fname, neg = t["when"].lstrip("!"), t["when"].startswith("!")
            f = facts[fname]
            if f["value"] is None:
                m["status"] = "na" if f["na"] else "missing"
                m["missing"] = [] if f["na"] else list(f["sources"])
                out.append(m)
                continue
            if bool(f["value"]) == neg:
                m["status"] = "na"
                out.append(m)
                continue
            used.append(fname)
        lines, missing, na = [], [], False
        for raw in t["body"]:
            optional = raw.startswith("?")
            line = raw[1:] if optional else raw
            names = FACT_RE.findall(line)
            absent = [n for n in names if facts[n]["value"] is None]
            if absent:
                if not optional:
                    for n in absent:
                        if facts[n]["na"]:
                            na = True
                        else:
                            missing.extend(s for s in facts[n]["sources"] if s not in missing)
                continue
            used.extend(n for n in names if n not in used)
            lines.append(FACT_RE.sub(lambda mm: facts[mm.group(1)]["value"], line))
        if t["requires_any"] and not any(facts[n]["value"] is not None for n in t["requires_any"]):
            for n in t["requires_any"]:
                missing.extend(s for s in facts[n]["sources"] if s not in missing)
        if missing:
            m["status"], m["missing"] = "missing", missing
        elif na:
            m["status"] = "na"
        else:
            body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
            m["body"] = body
            srcs = []
            for n in used:
                for s in facts[n]["sources"]:
                    if s not in srcs:
                        srcs.append(s)
            m["sources"] = srcs
            m["use_from"] = first_date(t["use_from"], date_facts)
            m["use_until"] = first_date(t["use_until"], date_facts)
        out.append(m)
    return out


def missing_sections(paths: list, p: dict) -> list:
    """Name a whole missing policy section once instead of each of its fields."""
    out = []
    for s in paths:
        top = s.split(".")[0]
        name = top if top not in p else s
        if name not in out:
            out.append(name)
    return out


def season_day(d: dt.date, season_year: int) -> str:
    return fmt_day(d) if d.year == season_year else fmt_full(d)


def use_text(m: dict, season_year: int) -> str:
    def f(d):
        return season_day(d, season_year)
    a, b = m["use_from"], m["use_until"]
    if a and b:
        return f(a) if a == b else f"{f(a)}–{f(b)}"
    if a:
        return f"from {f(a)}"
    if b:
        return f"until {f(b)}"
    return "all season"


# ----------------------------------------------------------------------------- tickets
def negated(tx: str, start: int) -> bool:
    """True when 'not', 'no', 'isn't', 'wasn't'... appears within NEGATION_WORDS words before position start."""
    before = re.findall(r"[a-z0-9']+", tx[:start])[-NEGATION_WORDS:]
    return any(w in NEGATORS for w in before)


def kw_match(tx: str, pattern, negation: bool) -> bool:
    return any(not (negation and negated(tx, m.start())) for m in pattern.finditer(tx))


def classify(t: dict, rules: list, fallback: str) -> tuple:
    """Escalation types' keywords first, then every type's 'first' phrases, then keywords and tags, in rule order."""
    text = norm_text(t["subject"] + " \n " + t["body"])
    cleaned = {}
    for r in rules:
        tx = text
        for p in r["not"]:
            tx = p.sub(" ", tx)
        cleaned[r["type"]] = tx
    for r in [x for x in rules if x["escalate"]]:
        for kw, p in r["kw"]:
            if kw_match(cleaned[r["type"]], p, r["negation"]):
                return r["type"], kw
    for r in rules:
        for kw, p in r["first"]:
            if kw_match(cleaned[r["type"]], p, r["negation"]):
                return r["type"], kw
    for r in rules:
        tx = cleaned[r["type"]]
        for kw, p in r["kw"]:
            if kw_match(tx, p, r["negation"]):
                return r["type"], kw
        hit = sorted(r["tags"] & t["tags"])
        if hit:
            return r["type"], "tag:" + hit[0]
    return fallback, ""


CUE_NAME = r"(?:[A-Z][a-z]+(?:[ -][A-Z][a-z]+)?)"
STREET_WORDS = (r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|place|pl|terrace|ter|"
                r"parkway|pkwy|highway|hwy|circle|cir|square|sq|trail|trl)")
MASKS = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"(?<![\w#])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-][\d#]{4}(?![\w#])"), "[phone]"),
    (re.compile(r"(?<![\w#])\d{1,6}\s+(?:[A-Za-z][A-Za-z.'-]*\s+){1,3}" + "(?i:" + STREET_WORDS + r")\b\.?"),
     "[address]"),
    (re.compile(r"(?i:\b(my name is|this is|i am)\s+)" + CUE_NAME), r"\1 [name]"),
    (re.compile(r"(?i:\b(thanks|thank you|regards|best|cheers|sincerely|love))([,!.]?\s*[-–—]?\s*)" + CUE_NAME
                + r"\s*$"), r"\1\2[name]"),
    (re.compile(r"(\s[-–—]\s*)[A-Z][a-z]+ [A-Z][a-z]+(?=\s|$|[,.])"), r"\1[name]"),
)


def mask(s: str) -> str:
    """Mask emails, phone numbers, street addresses, names after 'my name is' / 'this is' / a sign-off, long numbers."""
    for rx, rep in MASKS:
        s = rx.sub(rep, s)
    return re.sub(r"\d{4,}", lambda m: "#" * len(m.group(0)), s)


def csv_safe(v) -> str:
    """Stop a spreadsheet from running ticket text as a formula."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def ticket_stats(tickets: list, types: list, fallback: str, ly_orders: dict | None) -> dict:
    first, last = min(t["date"] for t in tickets), max(t["date"] for t in tickets)
    span = [day_shift(first, i) for i in range((last - first).days + 1)]
    by_day = {d: collections.Counter() for d in span}
    for t in tickets:
        by_day[t["date"]][t["type"]] += 1
    total = len(tickets)
    counts = collections.Counter(t["type"] for t in tickets)
    orders_total = sum(ly_orders[d] for d in span) if ly_orders else None
    rows = []
    for ty in types + [fallback]:
        n = counts.get(ty, 0)
        peak = max(span, key=lambda d: (by_day[d][ty], -d.toordinal())) if n else None
        rows.append({"type": ty, "tickets": n, "share": n / total,
                     "per_100_orders": (n / orders_total * 100) if orders_total else None,
                     "peak_day": peak, "peak_count": by_day[peak][ty] if peak else 0})
    busiest = sorted([d for d in span if by_day[d]], key=lambda d: (-sum(by_day[d].values()), d))[:TOP_DAYS]
    unc = [t for t in tickets if t["type"] == fallback]
    groups = collections.OrderedDict()
    for t in unc:
        key = re.sub(r"\d+", "#", norm_text(t["subject"] or t["body"][:60]))
        if key not in groups:
            groups[key] = [mask((t["subject"] or t["body"][:60]).strip()), 0]
        groups[key][1] += 1
    examples = sorted(groups.values(), key=lambda g: (-g[1], g[0].lower()))[:TOP_DAYS]
    return {"first": first, "last": last, "days": span, "by_day": by_day, "total": total, "rows": rows,
            "orders_total": orders_total, "busiest": busiest, "unclassified": len(unc), "examples": examples}


# ----------------------------------------------------------------------------- forecast
def forecast(stats: dict, types: list, orders: dict | None, growth: float | None) -> dict:
    ly_days = stats["days"]
    ty_days = [day_shift(d, ALIGN_DAYS) for d in ly_days]
    notes, filled = [], []
    if orders is None:
        g = 1.0 if growth is None else growth
        ratio = {d: g for d in ty_days}
        proj, ly_o = None, None
        notes.append(f"No orders file: forecast = last season's tickets × {g:.2f}"
                     + (" (--growth)." if growth is not None else ". Pass --orders or --growth to scale it."))
    else:
        have = [d for d in ty_days if d in orders]
        base = sum(orders[day_shift(d, -ALIGN_DAYS)] for d in have)
        g_auto = sum(orders[d] for d in have) / base if have and base else None
        g = growth if growth is not None else (g_auto if g_auto is not None else 1.0)
        proj = {}
        for d in ty_days:
            if d in orders:
                proj[d] = orders[d]
            else:
                proj[d] = orders[day_shift(d, -ALIGN_DAYS)] * g
                filled.append(d)
        if not have:
            notes.append(f"No projection for this season in the orders file: used last season's orders × {g:.2f}"
                         + (" (--growth)." if growth is not None else "."))
        elif filled:
            how = "--growth" if growth is not None else (f"this season's projection ÷ last season over "
                                                         f"{fmt_range(have[0], have[-1])}")
            notes.append(f"{fmt_range(filled[0], filled[-1])} {'has' if len(filled) == 1 else 'have'} no projection: "
                         f"used last season's orders × {g:.2f} ({how}).")
        ly_o = {d: orders[d] for d in ly_days}
        ratio = {}
        start = ty_days[0]
        for d in ty_days:
            win = [day_shift(d, -k) for k in range(ORDER_WINDOW) if day_shift(d, -k) >= start]
            den = sum(ly_o[day_shift(x, -ALIGN_DAYS)] for x in win)
            ratio[d] = sum(proj[x] for x in win) / den if den else g
    rows = []
    for ly, d in zip(ly_days, ty_days):
        per = {ty: stats["by_day"][ly].get(ty, 0) * ratio[d] for ty in types}
        rows.append({"date": d, "ly_date": ly, "ly_tickets": sum(stats["by_day"][ly].values()),
                     "ly_orders": ly_o[ly] if ly_o else None, "orders": proj[d] if proj else None,
                     "filled": d in filled, "ratio": ratio[d], "types": per, "total": sum(per.values())})
    total = sum(r["total"] for r in rows)
    return {"rows": rows, "total": total, "notes": notes, "growth": g, "start": ty_days[0], "end": ty_days[-1],
            "orders_total": sum(proj.values()) if proj else None,
            "ly_orders_total": sum(ly_o.values()) if ly_o else None}


def staffing(fc: dict, closed: set, agents: int | None, weekend: int | None, tpa: float | None, hours: float,
             weekend_window: tuple | None = None) -> dict:
    """Agents needed cover the day's forecast plus the tickets still waiting from the day before (when capacity is
    known). Weekends outside weekend_window (support_hours.weekend_coverage) have no one on shift."""
    if not tpa:
        return {"checked": False}
    rows = fc["rows"]
    carry, peak_carry, peak_day, cleared = 0.0, 0.0, None, None
    for r in rows:
        f = r["total"]
        work = f + (carry if agents is not None else 0.0)
        r["waiting_before"] = carry if agents is not None else None
        r["agents_needed"] = math.ceil(work / tpa - 1e-9) if work > 0 else 0
        r["agent_hours"] = work * hours / tpa
        if agents is None:
            r["on_shift"], r["capacity"], r["short"] = None, None, None
            continue
        d = r["date"]
        if d in closed:
            on = 0
        elif d.weekday() >= 5:
            on = weekend if weekend_window is None or weekend_window[0] <= d <= weekend_window[1] else 0
        else:
            on = agents
        r["on_shift"], r["capacity"] = on, on * tpa
        r["short"] = max(0.0, f - r["capacity"])
        carry = max(0.0, carry + f - r["capacity"])
        r["backlog"] = carry
        if carry > peak_carry + 1e-9:
            peak_carry, peak_day, cleared = carry, d, None
        elif peak_day and cleared is None and carry < 0.5:
            cleared = d
    if agents is None:
        return {"checked": False, "tpa": tpa, "hours": hours}
    short_days = [r for r in rows if r["short"] >= 0.5]
    return {"checked": True, "agents": agents, "weekend": weekend, "tpa": tpa, "hours": hours,
            "short_days": short_days, "peak_backlog": peak_carry, "peak_backlog_day": peak_day, "cleared": cleared,
            "closed": sorted(d for d in closed if fc["start"] <= d <= fc["end"]), "weekend_window": weekend_window}


# ----------------------------------------------------------------------------- existing macros audit
MONTH_NUM = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
             "nov": 11, "dec": 12}
TEXT_DATE_RE = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
                          r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b"
                          r"(?:,?\s+(\d{4})\b)?", re.I)
NUM_DATE_RE = re.compile(r"(?<![\d/$.])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")
ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
YEAR_RE = re.compile(r"\b(20\d{2})\b")
ABBR_DOT_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Mr|Mrs|Ms|Dr|St)\.", re.I)
# Words that make a date or a promise about delivery deadlines (plain "shipping" alone is not enough:
# "free shipping during the sale" is about discounts).
DEADLINE_CTX = re.compile(r"\b(order by|ship by|ships by|arrive|arrives|arrival|deliver|delivery|delivered|christmas|"
                          r"cutoff|cut-off|in time|last day to order|caught up)\b", re.I)
RET_CTX = re.compile(r"\b(return|returns|returned|returning)\b", re.I)
REPLY_CTX = re.compile(r"\b(reply|replies|respond|responds|response|get back)\b", re.I)
DMG_CTX = re.compile(r"\b(damage|damaged|broken|cracked|chipped|defective|shattered)\b", re.I)
EDIT_CTX = re.compile(r"\b(cancel|cancell?ed|cancellation|change|edit|modify)\b", re.I)
FS_CTX = re.compile(r"\bfree shipping\b|\bships? free\b|\bship for free\b", re.I)
RET_NUM = re.compile(r"\b(?:within|up to)\s+(\d+)\s+(?:calendar\s+)?days?\b|\b(\d+)[- ]day\s+returns?\b|"
                     r"\b(\d+)\s+days\s+(?:of|from|after)\s+(?:delivery|purchase|receipt|receiving|the order|"
                     r"your order|ordering)\b", re.I)
PROCESSING_RE = re.compile(r"\b(process\w*|refunds?|refunded|issued|credited|inspect\w*)\b", re.I)
WITHIN_DAYS = re.compile(r"\bwithin\s+(\d+)\s+(?:calendar\s+)?days?\b", re.I)
EDIT_NUM = re.compile(r"\bwithin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b", re.I)
REPLY_NUM = re.compile(r"\bwithin\s+(\d+)\s*(hours?|hrs?|business\s+days?|days?)\b", re.I)
MONEY_NUM = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
PROMISE_RE = re.compile(r"\b(guarantee[sd]?|promise[sd]?|definitely|for sure|always arrives?|never late)\b|\b100\s?%", re.I)
NEGATION_RE = re.compile(r"\b(not|no|never|can't|cannot|can not|don't|do not|won't|will not|isn't|aren't)\W+(\w+\W+)?$", re.I)
CODE_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{3,19}\b")
CODE_CUE_RE = re.compile(r"\b(code|coupon|promo)\W{0,3}$", re.I)
STOP_TOKENS = {"USPS", "FEDEX", "BFCM", "HTTP", "HTTPS", "HTML", "FAQ", "FAQS", "ASAP", "COVID", "USA", "SMS", "VIP",
               "VIPS", "UTC", "EST", "EDT", "PST", "PDT", "CST", "CDT", "MST", "MDT", "APO", "FPO", "SKU", "SKUS",
               "NOTE", "FREE", "SALE", "THANKS", "PLEASE", "TODAY", "NOW"}


def clean_for_scan(body: str) -> str:
    s = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\[\[.*?\]\]", " ", body)
    return re.sub(r"https?://\S+|\bwww\.\S+", " ", s)


def sentences(text: str) -> list:
    t = ABBR_DOT_RE.sub(lambda m: m.group(1), text)
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", t) if p.strip()]


def find_dates(s: str, season_year: int) -> list:
    found, spans = [], []

    def add(m, y, mo, d, explicit):
        if any(a <= m.start() < b for a, b in spans):
            return
        if not explicit:
            y = season_year if mo >= 7 else season_year + 1
        try:
            day = dt.date(y, mo, d)
        except ValueError:
            return
        spans.append((m.start(), m.end()))
        found.append({"date": day, "explicit": explicit, "start": m.start(), "end": m.end(), "text": m.group(0)})

    for m in ISO_DATE_RE.finditer(s):
        add(m, int(m.group(1)), int(m.group(2)), int(m.group(3)), True)
    for m in TEXT_DATE_RE.finditer(s):
        y = m.group(3)
        add(m, int(y) if y else 0, MONTH_NUM[m.group(1).lower()[:3]], int(m.group(2)), bool(y))
    for m in NUM_DATE_RE.finditer(s):
        y = m.group(3)
        yy = (2000 + int(y) if len(y) == 2 else int(y)) if y else 0
        add(m, yy, int(m.group(1)), int(m.group(2)), bool(y))
    return sorted(found, key=lambda x: x["start"])


def quote_at(s: str, start: int, end: int, before: int = 2, after: int = 0) -> str:
    pre = s[:start].split()[-before:] if before else []
    post = s[end:].split()[:after] if after else []
    q = " ".join(pre + [s[start:end]] + post).strip(" ,;:")
    return re.sub(r"[.,;:!?]+$", "", q)


def audit_existing(existing: list, p: dict, season_year: int, as_of: dt.date, rules: list, fallback: str,
                   macros: list) -> list:
    sh = p.get("shipping", {})
    svcs = rate_view(sh.get("services", []))  # what customers can pick: a checkout rate's date, not a carrier's
    ship_dates = {d for _, d in svcs}
    if sh.get("deliver_by"):
        ship_dates.add(sh["deliver_by"])
    bl = p.get("bfcm_backlog")
    if bl:
        ship_dates |= {bl["orders_from"], bl["orders_to"], bl["clears_by"]}
    cut_text = join_words([f"{fmt_day(d)} ({k})" for k, d in svcs])
    r = p.get("returns", {})
    hw = r.get("holiday_window")
    ret_dates = {hw["orders_from"], hw["orders_to"], hw["return_by"]} if hw else set()
    hw_text = (f"orders {fmt_range(hw['orders_from'], hw['orders_to'])} can be returned until "
               f"{fmt_full(hw['return_by'])}") if hw else ""
    disc = p.get("discounts", {})
    codes = {c["code"].upper() for c in disc.get("codes", [])} if "codes" in disc else None
    code_list = join_words(sorted(codes)) if codes else "none"
    def before(ctx):
        """The context word must come before the number: 'return ... within 14 days', not '... of your return'."""
        return lambda s, m: bool(ctx.search(s[:m.start()]))

    def return_window(s, m):
        hits = list(RET_CTX.finditer(s[:m.start()]))
        return bool(hits) and not PROCESSING_RE.search(s[hits[-1].end():m.start()]) and not REPLY_CTX.search(s)

    checks = [
        ("returns.standard_days", r.get("standard_days"), "return window", return_window,
         RET_NUM, lambda m: int(next(g for g in m.groups() if g)), lambda v: plural(v, "day")),
        ("damaged_defective.report_within_days", p.get("damaged_defective", {}).get("report_within_days"),
         "damage-report window", before(DMG_CTX), WITHIN_DAYS, lambda m: int(m.group(1)), lambda v: plural(v, "day")),
        ("order_changes.window_minutes", p.get("order_changes", {}).get("window_minutes"), "change/cancel window",
         before(EDIT_CTX), EDIT_NUM, lambda m: int(m.group(1)) * (60 if m.group(2).lower().startswith("h") else 1),
         duration),
        ("discounts.free_shipping_threshold", disc.get("free_shipping_threshold"), "free-shipping threshold",
         lambda s, m: bool(FS_CTX.search(s)), MONEY_NUM, lambda m: float(m.group(1)), money),
        ("support_hours.first_reply_hours", p.get("support_hours", {}).get("first_reply_hours"), "first-reply time",
         before(REPLY_CTX), REPLY_NUM, lambda m: int(m.group(1)) * (1 if m.group(2).lower().startswith("h") else 24),
         lambda v: plural(v, "hour")),
    ]
    by_type = collections.defaultdict(list)
    for m in macros:
        if m["status"] == "generated" and m["type"] != fallback:
            by_type[m["type"]].append(m["name"])
    results = []
    for em in existing:
        mtype, _ = classify({"subject": em["name"], "body": em["body"], "tags": set()}, rules, fallback)
        named = norm_text(em["name"])
        own = any(norm_text(m["name"]) == named for m in macros if m["status"] == "generated")
        repl = [m["name"] for m in macros if m["status"] == "generated" and any(rx.search(named) for rx in m["replaces"])]
        repl = repl or by_type.get(mtype, [])
        use = f"use “{repl[0]}”" if repl else ""
        retire = ("Retire it and " + use) if use else ""
        if own:
            retire = "Replace its text with the current draft in macros.csv"
        text = clean_for_scan(em["body"])
        issues, past, all_dates = [], [], []
        mentions_window, window_quote = False, ""
        for s in sentences(text):
            dates = find_dates(s, season_year)
            all_dates.extend(x["date"] for x in dates)
            ship_ctx = bool(DEADLINE_CTX.search(s)) and not FS_CTX.search(s)
            ret_ctx = bool(RET_CTX.search(s))
            for x in dates:
                reasons = []
                if x["explicit"] and x["date"] < as_of:
                    reasons.append("that date has passed")
                if ship_ctx and not ret_ctx and svcs and x["date"] not in ship_dates:
                    reasons.append(f"this year's last order dates are {cut_text}")
                elif ret_ctx and hw and x["date"] not in ret_dates:
                    reasons.append(f"this year's holiday returns deadline is {fmt_full(hw['return_by'])}")
                if reasons == ["that date has passed"]:
                    past.append(x["text"])
                elif reasons:
                    issues.append({"sev": "CRITICAL", "kind": "date",
                                   "text": f"says “{quote_at(s, x['start'], x['end'])}”: {', and '.join(reasons)}",
                                   "fix": retire or "Correct the date or retire it"})
            spans = [(x["start"], x["end"]) for x in dates]
            for ym in YEAR_RE.finditer(s):
                if any(a <= ym.start() < b for a, b in spans):
                    continue
                if int(ym.group(1)) < season_year:
                    issues.append({"sev": "WARNING", "kind": "year", "text": f"mentions {ym.group(1)}",
                                   "fix": "Update the year or retire it"})
            for path, value, label, ctx, rx, conv, show in checks:
                if value is None:
                    continue
                for nm in rx.finditer(s):
                    if not ctx(s, nm):
                        continue
                    said = conv(nm)
                    if path == "returns.standard_days":
                        mentions_window, window_quote = True, quote_at(s, nm.start(), nm.end(), 1, 4)
                    if said != value:
                        issues.append({"sev": "CRITICAL", "kind": "number",
                                       "text": f"says “{quote_at(s, nm.start(), nm.end(), 1)}” but the {label} is "
                                               f"{show(value)} ({path})",
                                       "fix": f"Change it to {show(value)}"})
            if DEADLINE_CTX.search(s):
                for pm in PROMISE_RE.finditer(s):
                    if NEGATION_RE.search(s[:pm.start()]):
                        continue
                    issues.append({"sev": "WARNING", "kind": "promise",
                                   "text": f"promises “{quote_at(s, pm.start(), pm.end(), 0, 8)}”",
                                   "fix": "Remove the promise and give the last order dates instead"})
        if past:
            issues.insert(0, {"sev": "CRITICAL", "kind": "past",
                              "text": "mentions " + ("a date that has passed" if len(past) == 1 else "dates that have passed")
                                      + ": " + ", ".join(f"“{x}”" for x in past),
                              "fix": retire or "Update the dates or retire it"})
        if mentions_window and hw and hw["return_by"] not in all_dates:
            issues.append({"sev": "WARNING", "kind": "holiday_window",
                           "text": f"says “{window_quote}” and doesn't mention the holiday window ({hw_text})",
                           "fix": ("Add the holiday window, or " + use) if use else "Add the holiday window"})
        if codes is not None:
            seen = set()
            for cm in CODE_TOKEN_RE.finditer(text):
                tok = cm.group(0)
                if tok in STOP_TOKENS or tok in seen or tok in codes:
                    continue
                if not (re.search(r"\d", tok) or CODE_CUE_RE.search(text[max(0, cm.start() - 12):cm.start()])):
                    continue
                seen.add(tok)
                issues.append({"sev": "WARNING", "kind": "code",
                               "text": f"mentions code {tok}, which isn't one of this year's codes ({code_list})",
                               "fix": f"Remove {tok}" + (f", or {use}" if use else "")})
        issues.sort(key=lambda x: SEV_ORDER[x["sev"]])
        results.append({"name": em["name"], "type": mtype, "issues": issues, "dates": all_dates, "body": em["body"]})
    return results


def facts_kept(generated_body: str, existing_body: str, season_year: int) -> list:
    """Dates and numbers in a generated macro that are missing from the live (edited) copy."""
    gen = clean_for_scan(generated_body)
    live = clean_for_scan(existing_body)
    live_dates = set()
    for s in sentences(live):
        live_dates |= {x["date"] for x in find_dates(s, season_year)}
    lost = []
    for s in sentences(gen):
        ds = find_dates(s, season_year)
        for x in ds:
            if x["date"] not in live_dates:
                lost.append(fmt_day(x["date"]))
        rest = s
        for x in reversed(ds):
            rest = rest[:x["start"]] + " " + rest[x["end"]:]
        for nm in re.finditer(r"\$?\d+(?:\.\d+)?", rest):
            if not re.search(r"(?<![\d.])" + re.escape(nm.group(0)) + r"(?![\d.])", live):
                lost.append(nm.group(0))
    out = []
    for x in lost:
        if x not in out:
            out.append(x)
    return out


# ----------------------------------------------------------------------------- analysis
def finding(sev: str, section: str, text: str, fix: str | None, by: dt.date | None) -> dict:
    return {"sev": sev, "section": section, "text": text, "fix": fix, "by": by}


def analyze(args, tickets, rules, fallback, policies_path, cutoffs_path, orders, existing, as_of) -> dict:
    first = min(t["date"] for t in tickets)
    season_year = (first.year if first.month >= 7 else first.year - 1) + 1
    p, notes, conflicts = load_policies(policies_path, cutoffs_path, season_year)
    notes = list(getattr(args, "order_notes", [])) + notes
    facts, date_facts = build_facts(p, season_year)
    types = [r["type"] for r in rules]
    labels = {r["type"]: r["label"] for r in rules}
    labels[fallback] = "Other (unclassified)"
    templates = load_templates(args.templates, facts, date_facts, set(types) | {fallback})
    esc_types = {r["type"] for r in rules if r["escalate"]}
    esc_rx = [phrase_re(k) for k in p.get("escalation", {}).get("keywords", [])]
    for t in tickets:
        t["type"], t["matched"] = classify(t, rules, fallback)
        txt = norm_text(t["subject"] + " " + t["body"])
        t["escalate"] = t["type"] in esc_types or any(rx.search(txt) for rx in esc_rx)
    if orders is not None:
        last = max(t["date"] for t in tickets)
        missing = [day_shift(first, i) for i in range((last - first).days + 1) if day_shift(first, i) not in orders]
        if missing:
            raise InputError(f"orders file has no row for {plural(len(missing), 'day')} of last season (first: "
                             f"{missing[0].isoformat()}); it needs every day from {first.isoformat()} to "
                             f"{last.isoformat()}, the dates your tickets cover. Last season's daily orders come from "
                             "Shopify Analytics; the BFCM Holiday Budget Planner's plan covers only this season, so "
                             "pass both (--orders last_season.csv --orders plan.json)")
    stats = ticket_stats(tickets, types, fallback, orders)
    fc = forecast(stats, types + [fallback], orders, args.growth)
    bf = black_friday(season_year)
    live_by = p.get("macros_live_by") or day_shift(bf, -LIVE_LEAD)
    decide_by, review_by = day_shift(live_by, -DECIDE_LEAD), day_shift(live_by, -REVIEW_LEAD)
    final_by = day_shift(bf, -FINAL_LEAD)
    fix_by = day_shift(as_of, FIX_NOW_DAYS)
    closed = set(p.get("support_hours", {}).get("closed_dates", []))
    weekend = args.weekend_agents if args.weekend_agents is not None else args.agents
    wc = p.get("support_hours", {}).get("weekend_coverage")
    staff = staffing(fc, closed, args.agents, weekend, args.tickets_per_agent, args.shift_hours,
                     (wc["from"], wc["to"]) if wc else None)
    macros = render_templates(templates, facts, date_facts, season_year, fallback)

    findings = []
    # policies.json disagrees with the Holiday Shipping Cutoff Checker result: the checker's values are used.
    for cf in conflicts:
        findings.append(finding("WARNING", "handoff",
                                f"policies.json and {cf['file']} disagree on the {cf['what']}: policies.json says "
                                f"{cf['policy']}; the Cutoff Checker computed {cf['checker']}. The macros use the "
                                "Cutoff Checker's values.", cf["fix"] + ".", decide_by))
    # Policy gaps and macros that could not be generated.
    gaps = []
    share = {r["type"]: r for r in stats["rows"]}
    for ty in types:
        row = share[ty]
        tmpl = [m for m in macros if m["type"] == ty]
        gen = [m for m in tmpl if m["status"] == "generated"]
        miss = [m for m in tmpl if m["status"] == "missing"]
        fields = missing_sections([s for m in miss for s in m["missing"]], p)
        status = "covered"
        if not tmpl:
            status = "no-templates"
        elif not gen and miss:
            status = "gap" if row["share"] >= GAP_SHARE else "low-volume gap"
        elif miss:
            status = "partial"
        applicable = [m for m in tmpl if m["status"] != "na"]
        gaps.append({"type": ty, "label": labels[ty], "tickets": row["tickets"], "share": row["share"],
                     "status": status, "missing": fields, "generated": len(gen), "templates": len(applicable)})
        quoted = join_words([f"`{f}`" for f in fields])
        if status == "gap":
            findings.append(finding("CRITICAL", "gaps",
                                    f"No written policy for “{labels[ty]}” tickets ({quoted} missing in policies.json), "
                                    f"but they were {row['tickets']:,} of {stats['total']:,} tickets ({pct(row['share'])}) "
                                    f"last season. Decide it by {fmt_day(decide_by)}.",
                                    f"Decide the policy for “{labels[ty]}” tickets and publish it, add {quoted} to "
                                    f"policies.json and re-run so the macro{'s' if len(tmpl) > 1 else ''} can be "
                                    f"generated (options: references/playbook.md §3).", decide_by))
        elif status == "low-volume gap":
            findings.append(finding("INFO", "gaps", f"No policy for “{labels[ty]}” tickets ({quoted} missing); they were "
                                                    f"only {row['tickets']:,} tickets ({pct(row['share'])}) last season.",
                                    None, None))
        elif status == "partial":
            for m in miss:
                sev = "WARNING" if m["if_missing"] == "warning" and row["share"] >= GAP_SHARE else "INFO"
                mf = join_words([f"`{f}`" for f in missing_sections(m["missing"], p)])
                findings.append(finding(sev, "gaps", f"Macro “{m['name']}” not generated: needs {mf} in policies.json.",
                                        f"Add {mf} to policies.json and re-run.", decide_by if sev == "WARNING" else None))

    # Staffing.
    if staff.get("checked") and staff["short_days"]:
        sd = staff["short_days"]
        days_txt = ", ".join(fmt_wday(r["date"]) + (" (support closed)" if r["on_shift"] == 0 else "")
                             for r in sd[:8]) + (", …" if len(sd) > 8 else "")
        tail = ""
        if staff["peak_backlog_day"]:
            tail = (f" Unanswered tickets peak at {rnd(staff['peak_backlog'])} on {fmt_wday(staff['peak_backlog_day'])}"
                    + (f" and clear by {fmt_wday(staff['cleared'])}." if staff["cleared"] else
                       " and don't clear before the season ends."))
        open_short = [r for r in sd if r["on_shift"]]
        closed_short = [r for r in sd if not r["on_shift"]]
        parts = []
        if open_short:
            parts.append("add cover on " + join_words([f"{fmt_wday(r['date'])} ({rnd(r['total'])} tickets forecast, "
                                                       f"{rnd(r['capacity'])} capacity)" for r in open_short]))
        if closed_short:
            parts.append("plan who clears the tickets that arrive while support is closed on "
                         + join_words([f"{fmt_wday(r['date'])} ({rnd(r['total'])})" for r in closed_short]))
        fix = "; ".join(parts)
        fix = fix[0].upper() + fix[1:] + (". Options: a second agent on those days, longer shifts, outside help or a "
                                          "trained teammate from another role.")
        findings.append(finding("WARNING", "staffing",
                                f"The forecast is above capacity on {plural(len(sd), 'day')}: {days_txt}.{tail}",
                                fix, live_by))

    # Classification quality.
    unc_share = stats["unclassified"] / stats["total"]
    if unc_share > UNCLASSIFIED_WARN:
        findings.append(finding("WARNING", "tickets", f"{stats['unclassified']:,} of {stats['total']:,} tickets "
                                f"({pct(unc_share)}) matched no rule, so the mix and forecast by type are incomplete.",
                                "Add keywords for the most common unclassified subjects to references/ticket_rules.json "
                                "and re-run. The rules are in English: if many tickets are in another language, add "
                                "keywords in that language too.", review_by))

    audit = None
    if existing is not None:
        audit = audit_existing(existing, p, season_year, as_of, rules, fallback, macros)
        for a in audit:
            for x in a["issues"]:
                findings.append(finding(x["sev"], "audit", f"Existing macro “{a['name']}” {x['text']}.", x["fix"],
                                        fix_by if x["sev"] == "CRITICAL" else review_by))
        names = {norm_text(e["name"]): e for e in existing}
        for m in macros:
            if m["status"] != "generated":
                m["state"] = None
                continue
            live = names.get(norm_text(m["name"]))
            if not live:
                m["state"] = "recommended"
                continue
            a = next(x for x in audit if x["name"] == live["name"])
            lost = facts_kept(m["body"], live["body"], season_year)
            used_ph = [x for x in PLACEHOLDERS if x in PH_RE.findall(m["body"])]
            generic_left = [x for x in used_ph if re.search(r"\{\{\s*" + x + r"\s*\}\}", live["body"])]
            hd = getattr(args, "helpdesk", None)
            no_var = [x for x in used_ph if hd and x not in HELPDESK_FILLS[hd]]
            m["manual"] = [x for x in used_ph if x in no_var or x in generic_left]
            m["manual_why"] = ("no variable in " + HELPDESK_NAMES[hd] if no_var else "") if hd else ""
            m["generic_left"] = generic_left
            if a["issues"] or lost:
                m["state"] = "applied"
            elif m["manual"]:
                m["state"] = "needs manual fill"
            else:
                m["state"] = "verified"
            m["lost"] = lost
    else:
        for m in macros:
            m["state"] = "recommended" if m["status"] == "generated" else None

    # Escalation.
    esc = p.get("escalation", {})
    kw_counts = []
    for k in esc.get("keywords", []):
        rx = phrase_re(k)
        n = sum(1 for t in tickets if rx.search(norm_text(t["subject"] + " " + t["body"])))
        kw_counts.append((k, n))
    esc_tickets = 0
    if esc.get("keywords"):
        rxs = [phrase_re(k) for k in esc["keywords"]]
        esc_tickets = sum(1 for t in tickets if any(rx.search(norm_text(t["subject"] + " " + t["body"])) for rx in rxs))

    findings.sort(key=lambda f: (SEV_ORDER[f["sev"]], SECTION_RANK[f["section"]]))
    crit = sum(1 for f in findings if f["sev"] == "CRITICAL")
    warn = sum(1 for f in findings if f["sev"] == "WARNING")
    verdict = "NOT READY" if crit else (f"FIX BEFORE {fmt_day(live_by).upper()}" if warn else "READY")

    return {"store": p["store"], "as_of": as_of, "season_year": season_year, "black_friday": bf,
            "cyber_monday": day_shift(bf, 3), "live_by": live_by, "decide_by": decide_by, "review_by": review_by,
            "final_by": final_by, "fix_by": fix_by, "policies": p, "notes": notes, "labels": labels,
            "types": types, "fallback": fallback, "stats": stats, "forecast": fc, "staff": staff, "macros": macros,
            "gaps": gaps, "audit": audit, "findings": findings, "critical": crit, "warnings": warn,
            "verdict": verdict, "escalation": esc, "kw_counts": kw_counts, "esc_tickets": esc_tickets,
            "tickets": tickets, "have_orders": orders is not None, "esc_types": esc_types,
            "cutoffs_file": os.path.basename(cutoffs_path) if cutoffs_path else None, "conflicts": conflicts,
            "rules_label": "references/ticket_rules.json" if os.path.abspath(args.rules) == DEFAULT_RULES
            else os.path.basename(args.rules)}


# ----------------------------------------------------------------------------- report
def checklist(a: dict) -> list:
    """Rows of [severity, what to do, state, by-date]."""
    rows = []
    per_macro = collections.OrderedDict()
    for f in a["findings"]:
        if f["sev"] not in ("CRITICAL", "WARNING") or not f["fix"]:
            continue
        if f["section"] == "audit":
            name = f["text"].split("“", 1)[1].split("”", 1)[0]
            per_macro.setdefault(name, []).append(f)
        else:
            rows.append([f["sev"], f["fix"], "recommended", f["by"]])
    for name, fs in per_macro.items():
        sev = min((f["sev"] for f in fs), key=lambda s: SEV_ORDER[s])
        retire = [f["fix"] for f in fs if f["fix"].startswith("Retire it")]
        fixes = retire[:1] if retire else list(collections.OrderedDict.fromkeys(f["fix"] for f in fs))
        rows.append([sev, f"Existing macro “{name}”: " + "; ".join(fixes) + ".", "recommended",
                     min(f["by"] for f in fs)])
    gen = [m for m in a["macros"] if m["status"] == "generated"]
    states = collections.Counter(m["state"] for m in gen)
    live_state = ("verified" if gen and states.get("verified") == len(gen) else
                  "applied" if states.get("applied") or states.get("verified") or states.get("needs manual fill")
                  else "recommended")
    rows.append(["PLAN", f"Review the {len(gen)} draft macros in macros_preview.md: check each fact against the "
                         "policy, adapt the tone to your brand voice without changing any fact, and map the "
                         "placeholders to your helpdesk.", "recommended", a["review_by"]])
    rows.append(["PLAN", "Put the macros live in your helpdesk with their tags and test each one on a test ticket. "
                         "Then export your macros and re-run this skill with --macros to mark them verified.",
                 live_state, a["live_by"]])
    rows.append(["PLAN", "Final re-run with the final policies, cutoff dates and macros export. Brief the team on the "
                         "escalation map.", "recommended", a["final_by"]])
    windowed = [m for m in gen if m["use_from"] or m["use_until"]]
    taken = set()
    for m in windowed:
        if not m["use_until"]:
            continue
        nxt = next((x for x in windowed if x["type"] == m["type"] and x["use_from"] == day_shift(m["use_until"], 1)),
                   None)
        if nxt:
            taken.add(nxt["id"])
            rows.append(["PLAN", f"Switch from “{m['name']}” to “{nxt['name']}”.", "recommended", nxt["use_from"]])
        else:
            last_day = season_day(m["use_until"], a["season_year"])
            rows.append(["PLAN", f"Stop using “{m['name']}” (its last day is {last_day}).",
                         "recommended", day_shift(m["use_until"], 1)])
    for m in windowed:
        if m["use_from"] and m["id"] not in taken:
            until = f" through {season_day(m['use_until'], a['season_year'])}" if m["use_until"] else ""
            rows.append(["PLAN", f"Start using “{m['name']}”{until}.", "recommended", m["use_from"]])
    rows.sort(key=lambda r: (SEV_ORDER[r[0]], r[3]))
    return rows


def render(a: dict) -> str:
    st, fc, staff = a["stats"], a["forecast"], a["staff"]
    L = []
    w = L.append
    days_to_bf = (a["black_friday"] - a["as_of"]).days
    when_bf = (f"{days_to_bf} days away" if days_to_bf > 0 else ("today" if days_to_bf == 0 else
               f"{-days_to_bf} days ago"))
    w(f"## Holiday Support Macros: {a['store']}, as of {a['as_of'].isoformat()}")
    w("")
    if a["verdict"] == "READY":
        lead = "**Verdict: READY.** No policy gaps" + (" or stale macros" if a["audit"] is not None else "") + " found."
    else:
        parts = []
        if a["critical"]:
            parts.append(plural(a["critical"], "critical issue"))
        if a["warnings"]:
            parts.append(plural(a["warnings"], "warning"))
        lead = f"**Verdict: {a['verdict']}.** {' and '.join(parts)}."
    w(f"{lead} Black Friday is {fmt_full(a['black_friday'])} ({when_bf}) and Cyber Monday is "
      f"{fmt_day(a['cyber_monday'])}; have the holiday macros live by **{fmt_day(a['live_by'])}**.")
    peak = max(fc["rows"], key=lambda r: (r["total"], -r["date"].toordinal()))
    w(f"Last season: {st['total']:,} tickets ({fmt_span(st['first'], st['last'])}). "
      f"This season's forecast: {rnd(fc['total']):,} tickets ({fmt_span(fc['start'], fc['end'])}), "
      f"busiest on {fmt_wday(peak['date'])} with {rnd(peak['total'])}.")
    w("")
    shown = [f for f in a["findings"] if f["sev"] in ("CRITICAL", "WARNING")]
    if shown:
        w("**Issues, most severe first** (all detected; nothing was changed in your helpdesk or store)")
        for i, f in enumerate(shown, start=1):
            w(f"{i}. **{f['sev']}**: {f['text']}")
        w("")
    not_checked = []
    if a["audit"] is None:
        not_checked.append("existing macros (no --macros file)")
    if not a["have_orders"]:
        not_checked.append("tickets per order (no --orders file)")
    if not staff.get("checked"):
        not_checked.append("staffing against capacity (pass --agents and --tickets-per-agent)")
    if not_checked:
        w("Not checked: " + "; ".join(not_checked) + ".")
        w("")

    # 1. Ticket mix
    w(f"### 1. Last season's tickets ({fmt_span(st['first'], st['last'])})")
    w("")
    head = f"{st['total']:,} tickets over {plural(len(st['days']), 'day')}"
    if st["orders_total"]:
        head += (f" and {rnd(st['orders_total']):,} orders ({st['total'] / st['orders_total'] * 100:.1f} tickets per "
                 f"100 orders)")
    w(head + f". Classified with {a['rules_label']}: the first matching rule wins.")
    w("")
    if st["orders_total"]:
        w("| Ticket type | Tickets | Share | Per 100 orders | Peak day |")
        w("| --- | ---: | ---: | ---: | --- |")
    else:
        w("| Ticket type | Tickets | Share | Peak day |")
        w("| --- | ---: | ---: | --- |")
    rows = sorted([r for r in st["rows"] if r["type"] != a["fallback"]], key=lambda r: (-r["tickets"], r["type"]))
    rows += [r for r in st["rows"] if r["type"] == a["fallback"]]
    for r in rows:
        pk = f"{fmt_wday(r['peak_day'])} ({r['peak_count']})" if r["peak_day"] else "—"
        cells = [a["labels"][r["type"]], f"{r['tickets']:,}", pct(r["share"])]
        if st["orders_total"]:
            cells.append(f"{r['per_100_orders']:.1f}")
        cells.append(pk)
        w("| " + " | ".join(cells) + " |")
    w("")
    busy = []
    for d in st["busiest"]:
        top = st["by_day"][d].most_common(1)[0]
        busy.append(f"{fmt_wday(d)} ({sum(st['by_day'][d].values())}, mostly {short_label(a['labels'][top[0]], True)})")
    w("Busiest days: " + "; ".join(busy) + ".")
    unc = st["unclassified"]
    if unc:
        ex = ", ".join(f"“{s}” ({n})" for s, n in st["examples"])
        w(f"Unclassified: {unc:,} of {st['total']:,} ({pct(unc / st['total'])}). Most common: {ex}. "
          "Add keywords for the ones that belong to a type and re-run.")
    else:
        w("Unclassified: none.")
    w("")

    # 2. Forecast
    w(f"### 2. This season's forecast and staffing ({fmt_span(fc['start'], fc['end'])})")
    w("")
    if a["have_orders"]:
        w(f"Each day = last season's tickets on the same weekday ({ALIGN_DAYS} days earlier, so Black Friday lines up "
          f"with Black Friday) × this season's projected orders ÷ last season's orders, over the {ORDER_WINDOW} days "
          "up to that day.")
        change = fc["orders_total"] / fc["ly_orders_total"] - 1 if fc["ly_orders_total"] else 0
        tchange = fc["total"] / st["total"] - 1
        w(f"Projected orders {rnd(fc['orders_total']):,} vs {rnd(fc['ly_orders_total']):,} last season "
          f"({'+' if change >= 0 else ''}{pct(change)}). Forecast {rnd(fc['total']):,} tickets "
          f"({'+' if tchange >= 0 else ''}{pct(tchange)}).")
    for n in fc["notes"]:
        w(n)
    if staff.get("checked"):
        wk = staff["weekend"]
        ww = staff.get("weekend_window")
        wk_txt = f" and {wk} on weekends" if wk != staff["agents"] else " and weekends"
        if ww:
            wk_txt += f" from {fmt_wday(ww[0])} to {fmt_wday(ww[1])} only (support_hours.weekend_coverage)"
        cap = (f"Capacity: {plural(staff['agents'], 'agent')} on weekdays" + wk_txt
               + f", {staff['tpa']:g} tickets per agent per {staff['hours']:g}-hour shift")
        if staff["closed"]:
            cap += f"; support closed {join_words([fmt_wday(d) for d in staff['closed']])}"
        w(cap + ". Agents needed and agent-hours cover the day's forecast plus the tickets still waiting from the "
          "day before.")
    w("")
    top = sorted(fc["rows"], key=lambda r: (-r["total"], r["date"]))[:TOP_DAYS]
    if staff.get("tpa"):
        if staff.get("checked"):
            w("| Busiest days | Projected orders | Forecast tickets | Main types | Agent-hours needed | Agents needed "
              "| On shift | Short by | Waiting at day end |")
            w("| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        else:
            w("| Busiest days | Projected orders | Forecast tickets | Main types | Agent-hours needed | Agents needed |")
            w("| --- | ---: | ---: | --- | ---: | ---: |")
    else:
        w("| Busiest days | Projected orders | Forecast tickets | Main types |")
        w("| --- | ---: | ---: | --- |")
    for r in top:
        main = sorted(r["types"].items(), key=lambda kv: (-kv[1], kv[0]))[:2]
        mt = ", ".join(f"{short_label(a['labels'][k])} {rnd(v)}" for k, v in main if v >= 0.5)
        cells = [fmt_wday(r["date"]), f"{rnd(r['orders']):,}" if r["orders"] is not None else "—", f"{rnd(r['total'])}", mt]
        if staff.get("tpa"):
            cells += [f"{r['agent_hours']:.1f}", f"{r['agents_needed']}"]
            if staff.get("checked"):
                cells += [f"{r['on_shift']}", f"{rnd(r['short'])}" if r["short"] >= 0.5 else "—",
                          f"{rnd(r['backlog'])}" if r["backlog"] >= 0.5 else "—"]
        w("| " + " | ".join(cells) + " |")
    w("")
    if staff.get("checked"):
        sd = staff["short_days"]
        if sd:
            w(f"Over capacity on {plural(len(sd), 'day')} of {len(fc['rows'])}: "
              + ", ".join(f"{fmt_wday(r['date'])} (short {rnd(r['short'])})" for r in sd) + ".")
            if staff["peak_backlog_day"]:
                clr = (f"cleared by {fmt_wday(staff['cleared'])}" if staff["cleared"] else
                       "not cleared by the end of the season")
                w(f"With carry-over, unanswered tickets peak at {rnd(staff['peak_backlog'])} on "
                  f"{fmt_wday(staff['peak_backlog_day'])} and are {clr}.")
        else:
            w("The forecast stays within capacity every day.")
        w("")
    weeks = collections.OrderedDict()
    for r in fc["rows"]:
        wk_start = day_shift(r["date"], -r["date"].weekday())
        weeks.setdefault(wk_start, []).append(r)
    if staff.get("checked"):
        w("| Week of | Forecast tickets | Busiest day | Capacity | Days short |")
        w("| --- | ---: | --- | ---: | ---: |")
    else:
        w("| Week of | Forecast tickets | Busiest day |")
        w("| --- | ---: | --- |")
    for wk, rs in weeks.items():
        b = max(rs, key=lambda r: (r["total"], -r["date"].toordinal()))
        cells = [fmt_day(wk), f"{rnd(sum(r['total'] for r in rs)):,}", f"{fmt_wday(b['date'])} ({rnd(b['total'])})"]
        if staff.get("checked"):
            cells += [f"{rnd(sum(r['capacity'] for r in rs)):,}", str(sum(1 for r in rs if r["short"] >= 0.5))]
        w("| " + " | ".join(cells) + " |")
    w("")

    # 3. Policy coverage
    w(f"### 3. Policy coverage (a ticket type with at least {pct(GAP_SHARE, 0)} of last season's tickets needs a "
      "written policy)")
    w("")
    w("| Ticket type | Share | Macros generated | Status |")
    w("| --- | ---: | ---: | --- |")
    for g in sorted(a["gaps"], key=lambda g: (-g["tickets"], g["type"])):
        if g["status"] == "covered":
            stt = "OK"
        elif g["status"] == "gap":
            stt = f"**GAP**: {join_words(['`' + f + '`' for f in g['missing']])} missing. Decide by {fmt_day(a['decide_by'])}"
        elif g["status"] == "low-volume gap":
            stt = f"No policy ({join_words(['`' + f + '`' for f in g['missing']])}), low volume"
        elif g["status"] == "partial":
            stt = f"Partly covered: {join_words(['`' + f + '`' for f in g['missing']])} missing"
        elif g["type"] in a["esc_types"]:
            stt = "Goes to a person (escalation map), not a macro"
        else:
            stt = "No templates for this type"
        w(f"| {g['label']} | {pct(g['share'])} | {g['generated']} of {g['templates']} | {stt} |")
    w("")
    gap_rows = [g for g in a["gaps"] if g["status"] == "gap"]
    if gap_rows:
        for g in gap_rows:
            w(f"- **{g['label']}**: customers will ask, and without a written rule each agent will improvise. Decide "
              f"the rule, publish it where customers can see it, add it to policies.json and re-run. Options and "
              f"trade-offs: references/playbook.md §3.")
    else:
        w("Every ticket type above the threshold has a written policy.")
    w("")

    # 4. Existing macros
    if a["audit"] is not None:
        au = a["audit"]
        flagged = [x for x in au if x["issues"]]
        w(f"### 4. Existing macros ({plural(len(au), 'macro')} checked)")
        w("")
        if flagged:
            w("| Macro | Severity | Problem | What to do |")
            w("| --- | --- | --- | --- |")
            for x in flagged:
                for i in x["issues"]:
                    w(f"| {cell(x['name'])} | {i['sev']} | {cell(i['text'][0].upper() + i['text'][1:])} | {cell(i['fix'])} |")
            w("")
        ok = [x["name"] for x in au if not x["issues"]]
        if ok:
            w(f"No problems found in {len(ok)}: {', '.join(ok)}.")
        if not flagged:
            w("No stale dates, contradicted numbers, unknown codes or delivery promises found.")
        w("Checks: dates that have passed or don't match this year's policy, return / damage / change windows, "
          "free-shipping threshold and reply time against policies.json, codes that aren't this year's, and "
          "delivery promises.")
        w("")

    # 5. Generated macros
    ms = a["macros"]
    gen = [m for m in ms if m["status"] == "generated"]
    skipped = [m for m in ms if m["status"] == "missing"]
    na = [m for m in ms if m["status"] == "na"]
    sec = 5 if a["audit"] is not None else 4
    w(f"### {sec}. Holiday macros generated from your policies (drafts for review)")
    w("")
    line = f"{len(gen)} of {len(ms)} templates generated"
    if skipped:
        line += f"; {len(skipped)} not generated because a policy is missing"
    if na:
        line += f"; {len(na)} not applicable to your policies"
    w(line + ". Every date, number and rule in them comes from policies.json"
      + (f" and {a['cutoffs_file']} (the Cutoff Checker's dates win where they disagree)." if a.get("cutoffs_file")
         else "."))
    w("")
    w("| Macro | Ticket type | Use | Facts from | State |")
    w("| --- | --- | --- | --- | --- |")
    for m in gen:
        srcs = sections_of(m["sources"]) or "none (no policy facts)"
        tl = "General" if m["type"] == a["fallback"] else short_label(a["labels"][m["type"]])
        w(f"| {cell(m['name'])} | {tl} | {use_text(m, a['season_year'])} | {srcs} | {m['state']} |")
    w("")
    groups = collections.OrderedDict()
    for m in skipped:
        groups.setdefault(tuple(missing_sections(m["missing"], a["policies"])), []).append(m["name"])
    for fields, names in groups.items():
        w(f"- Not generated: {join_words(['“' + n + '”' for n in names])}: "
          f"need{'s' if len(names) == 1 else ''} {join_words(['`' + f + '`' for f in fields])} in policies.json.")
    for m in na:
        w(f"- Not needed with your policies: “{m['name']}”.")
    manual = [m for m in gen if m.get("state") == "needs manual fill"]
    for m in manual:
        parts = []
        if m.get("generic_left"):
            parts.append(join_words([f"`{{{{{x}}}}}`" for x in m["generic_left"]]) + " still in generic form, not "
                         "mapped to your helpdesk")
        rest = [x for x in m["manual"] if x not in m.get("generic_left", [])]
        if rest or (m.get("manual_why") and not parts):
            parts.append(join_words([f"`{{{{{x}}}}}`" for x in (rest or m["manual"])]) + f": {m['manual_why']}, so "
                         "the agent must type it in")
        w(f"- Needs manual fill: “{m['name']}”: {'; '.join(parts)}.")
    if skipped or na or manual:
        w("")
    w("Placeholders `{{customer_first_name}}`, `{{order_number}}`, `{{tracking_url}}` and `{{agent_first_name}}` are "
      "left for your helpdesk to fill. Map them to your helpdesk's syntax before saving (table in macros_preview.md "
      "and references/playbook.md §4): Gorgias leaves a variable it can't fill blank, and Help Scout and Shopify Inbox "
      "have no order-number or tracking-link variable, so agents type those in. A live macro that still needs a "
      "placeholder filled by hand is marked “needs manual fill”, never verified.")
    w("")

    # 6. Escalation
    sec += 1
    esc = a["escalation"]
    owner = esc.get("owner", "your support lead")
    w(f"### {sec}. Escalation map (these go to a person, not a macro)")
    w("")
    w("| Situation | Route to |")
    w("| --- | --- |")
    if esc.get("order_value_over") is not None:
        w(f"| Any order over {money(esc['order_value_over'])} with a problem | {owner} |")
    if esc.get("refund_over") is not None:
        w(f"| A refund, credit or replacement worth more than {money(esc['refund_over'])} | {owner}, for approval |")
    w(f"| Chargeback, payment dispute or suspected fraud | {owner}, same day; never answer with a macro |")
    w(f"| Injury, burn, allergic reaction or any safety concern | {owner}, same day; never answer with a macro |")
    w(f"| Legal threats, press or influencer complaints | {owner} |")
    for g in gap_rows:
        w(f"| “{g['label']}” tickets, until the policy is decided | {owner}, so every customer gets the same answer |")
    w(f"| A customer's third message about the same order | {owner} |")
    w("")
    if a["kw_counts"]:
        hits = [f"{k} {n}" for k, n in a["kw_counts"] if n]
        w(f"Last season, {a['esc_tickets']:,} of {st['total']:,} tickets ({pct(a['esc_tickets'] / st['total'])}) contained "
          f"an escalation word from policies.json" + (f": {', '.join(hits)}." if hits else "."))
    else:
        w("Add `escalation.keywords` to policies.json to count last season's tickets that needed a person.")
    w("")

    # 7. Checklist
    sec += 1
    w(f"### {sec}. Checklist (nothing has been changed)")
    w("")
    w("| # | Severity | What to do | State | By |")
    w("| ---: | --- | --- | --- | --- |")
    for i, r in enumerate(checklist(a), start=1):
        w(f"| {i} | {r[0]} | {r[1]} | {r[2]} | {by_date(r[3], a['as_of'])} |")
    w("")
    for n in a["notes"]:
        w(n)
    w(f"_Gap threshold: ticket types with ≥ {pct(GAP_SHARE, 0)} of last season's tickets. Forecast: weekday-aligned "
      f"({ALIGN_DAYS} days), scaled by projected orders over {ORDER_WINDOW} days. States: detected → recommended → "
      "applied → verified. Read-only: no replies sent and no helpdesk, store or macro changed._")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------- output files
HELPDESK_TABLE = """| Placeholder | Gorgias | Zendesk | Help Scout (saved replies) | Shopify Inbox (quick replies) |
| --- | --- | --- | --- | --- |
| `{{customer_first_name}}` | `{{ticket.customer.firstname}}` (confirmed) | `{{ticket.requester.first_name}}` (confirmed) | `{%customer.firstName,fallback=there%}` (confirmed) | No variables documented: type it in |
| `{{agent_first_name}}` | `{{current_user.firstname}}` (confirmed) | `{{current_user.first_name}}` (confirmed) | `{%user.firstName%}` (confirmed) | Type it in |
| `{{order_number}}` | Not in Gorgias' list (`orders[0].id` is Shopify's internal ID, not the order number). Commonly `{{ticket.customer.integrations.shopify.orders[0].name}}`: verify in your helpdesk | No built-in placeholder. A custom ticket field `{{ticket.ticket_field_<field ID>}}` filled by your Shopify app: verify in your helpdesk | No variable: type it in | Type it in |
| `{{tracking_url}}` | Not in Gorgias' list. Commonly `{{ticket.customer.integrations.shopify.orders[0].fulfillments[0].tracking_url}}`: verify in your helpdesk | Custom ticket field, as above: verify in your helpdesk | No variable: type it in | Type it in |

"Confirmed" = shown in the helpdesk's own documentation on Oct 7, 2026:
Gorgias https://helpcenter.gorgias.com/en-US/macro-variables-101-81845 ·
Zendesk https://support.zendesk.com/hc/en-us/articles/4408886858138-Zendesk-Support-placeholders-reference ·
Help Scout https://docs.helpscout.com/article/468-work-with-variables ·
Shopify Inbox https://help.shopify.com/en/manual/inbox/configure-inbox/quick-replies
Gorgias `orders[0]` is the customer's most recent order, which may not be the one the ticket is about, and Gorgias
replaces a variable it can't fill with a blank. Any other helpdesk: verify in your helpdesk.
"""


def write_outputs(a: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    gen = [m for m in a["macros"] if m["status"] == "generated"]
    with open(os.path.join(out_dir, "macros.csv"), "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(["name", "tags", "body"])
        for m in gen:
            wr.writerow([m["name"], ",".join(m["tags"]), m["body"]])
    P = [f"# Holiday {a['season_year']} macros: {a['store']} (drafts for review)", "",
         f"Generated {a['as_of'].isoformat()} from policies.json. {len(gen)} macros. Facts come only from the "
         "policies; review the tone, not the facts. If a fact is wrong, fix policies.json and re-run.", "",
         "## Map the placeholders to your helpdesk", "", HELPDESK_TABLE.rstrip("\n"), ""]
    for m in gen:
        P += [f"## {m['name']}", "",
              f"Ticket type: {'General' if m['type'] == a['fallback'] else a['labels'][m['type']]} · "
              f"Tags: {', '.join(m['tags'])} · Use: {use_text(m, a['season_year'])} · "
              f"Facts from: {', '.join(m['sources']) or 'none'}", "", "```text", m["body"], "```", ""]
    skipped = [m for m in a["macros"] if m["status"] == "missing"]
    if skipped:
        P += ["## Not generated (policy missing)", ""]
        groups = collections.OrderedDict()
        for m in skipped:
            groups.setdefault(tuple(missing_sections(m["missing"], a["policies"])), []).append(m["name"])
        for fields, names in groups.items():
            P.append(f"- {join_words(names)}: need{'s' if len(names) == 1 else ''} {join_words(list(fields))} in "
                     "policies.json. Decide the policy, add it and re-run.")
        P.append("")
    with open(os.path.join(out_dir, "macros_preview.md"), "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(P).rstrip("\n") + "\n")
    types = a["types"] + [a["fallback"]]
    with open(os.path.join(out_dir, "forecast.csv"), "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(["date", "weekday", "ly_date", "ly_orders", "projected_orders", "orders_filled", "ly_tickets",
                     "forecast_tickets"] + types + ["agents_needed", "agent_hours", "agents_on_shift", "capacity",
                                                    "short_by", "backlog_end_of_day"])
        for r in a["forecast"]["rows"]:
            def num(x, nd=1):
                return "" if x is None else f"{x:.{nd}f}"
            wr.writerow([r["date"].isoformat(), WEEKDAYS[r["date"].weekday()][:3], r["ly_date"].isoformat(),
                         num(r["ly_orders"], 0), num(r["orders"]), "yes" if r["filled"] else "no", r["ly_tickets"],
                         num(r["total"])] + [num(r["types"][t]) for t in types]
                        + [r.get("agents_needed", ""), num(r.get("agent_hours")), r.get("on_shift", "") if
                           r.get("on_shift") is not None else "", num(r.get("capacity"), 0), num(r.get("short")),
                           num(r.get("backlog"))])
    with open(os.path.join(out_dir, "tickets_classified.csv"), "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(["id", "date", "type", "escalate", "matched", "subject", "body"])
        for t in a["tickets"]:
            wr.writerow([csv_safe(t["id"]), t["date"].isoformat(), t["type"], "yes" if t["escalate"] else "no",
                         csv_safe(t["matched"]), csv_safe(t["subject"]), csv_safe(t["body"])])


def to_jsonable(x):
    if isinstance(x, dict):
        return {str(k.isoformat() if isinstance(k, dt.date) else k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v) for v in x]
    if isinstance(x, set):
        return sorted(to_jsonable(v) for v in x)
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, collections.Counter):
        return dict(x)
    if isinstance(x, float):
        return round(x, 4)
    return x


def json_summary(a: dict) -> dict:
    st, fc = a["stats"], a["forecast"]
    return to_jsonable({
        "store": a["store"], "as_of": a["as_of"], "season_year": a["season_year"], "verdict": a["verdict"],
        "critical": a["critical"], "warnings": a["warnings"], "black_friday": a["black_friday"],
        "live_by": a["live_by"], "decide_by": a["decide_by"],
        "tickets": {"total": st["total"], "first": st["first"], "last": st["last"], "orders": st["orders_total"],
                    "unclassified": st["unclassified"],
                    "by_type": {r["type"]: {"tickets": r["tickets"], "share": r["share"],
                                            "per_100_orders": r["per_100_orders"]} for r in st["rows"]}},
        "forecast": {"total": fc["total"], "start": fc["start"], "end": fc["end"], "growth": fc["growth"],
                     "orders": fc["orders_total"], "ly_orders": fc["ly_orders_total"], "notes": fc["notes"],
                     "days": [{"date": r["date"], "orders": r["orders"], "tickets": r["total"],
                               "by_type": r["types"], "agents_needed": r.get("agents_needed"),
                               "capacity": r.get("capacity"), "short": r.get("short")} for r in fc["rows"]]},
        "staffing": {k: v for k, v in a["staff"].items() if k != "short_days"},
        "short_days": [r["date"] for r in a["staff"].get("short_days", [])],
        "gaps": a["gaps"],
        "audit": [{"name": x["name"], "type": x["type"], "issues": x["issues"]} for x in a["audit"]]
        if a["audit"] is not None else None,
        "macros": [{k: m.get(k) for k in ("id", "name", "type", "tags", "status", "missing", "sources", "use_from",
                                          "use_until", "state", "manual", "body")} for m in a["macros"]],
        "findings": a["findings"], "checklist": checklist(a), "notes": a["notes"], "cutoffs_conflicts": a["conflicts"],
    })


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--policies", required=True, help="this season's holiday policies (JSON)")
    ap.add_argument("--tickets", required=True, help="last season's helpdesk tickets (CSV)")
    ap.add_argument("--macros", help="optional export of your existing macros (CSV: name, body, tags)")
    ap.add_argument("--orders", action="append", default=[],
                    help="daily orders CSV (date, orders) for last season and/or this season's projection, or the "
                         "BFCM Holiday Budget Planner's --json result; repeatable")
    ap.add_argument("--prefer-plan", action="store_true",
                    help="when a planner --json plan and an orders CSV give the same date, use the plan's orders")
    ap.add_argument("--cutoffs", help="optional Holiday Shipping Cutoff Checker --json result")
    ap.add_argument("--helpdesk", choices=sorted(HELPDESK_FILLS),
                    help="your helpdesk, to flag placeholders it can't fill (agents must type them in)")
    ap.add_argument("--date-format", choices=("mdy", "dmy"),
                    help="how to read slash dates in the tickets file: mdy = 12/05/2025 is Dec 5, dmy = May 12")
    ap.add_argument("--rules", default=DEFAULT_RULES, help="ticket rules JSON (default: references/ticket_rules.json)")
    ap.add_argument("--templates", default=DEFAULT_TEMPLATES,
                    help="macro templates JSON (default: references/macro_templates.json)")
    ap.add_argument("--agents", type=int, help="agents on shift on weekdays")
    ap.add_argument("--weekend-agents", type=int, help="agents on shift on Saturdays and Sundays (default: --agents)")
    ap.add_argument("--tickets-per-agent", type=float, help="tickets one agent handles in a shift")
    ap.add_argument("--shift-hours", type=float, default=8.0, help="hours in a shift (default 8)")
    ap.add_argument("--growth", type=float, help="scale last season by this factor where no projection exists")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: today)")
    ap.add_argument("--out-dir", help="write macros.csv, macros_preview.md, forecast.csv, tickets_classified.csv here")
    ap.add_argument("--json", help="also write the structured analysis to this path")
    args = ap.parse_args(argv)
    try:
        as_of = parse_iso(args.as_of, "--as-of") if args.as_of else dt.date.today()
        for k in ("agents", "weekend_agents"):
            v = getattr(args, k)
            if v is not None and v < 0:
                raise InputError(f"--{k.replace('_', '-')} must be 0 or more")
        if args.tickets_per_agent is not None and args.tickets_per_agent <= 0:
            raise InputError("--tickets-per-agent must be more than 0")
        if args.agents is not None and args.tickets_per_agent is None:
            raise InputError("--agents needs --tickets-per-agent")
        if args.shift_hours <= 0 or args.shift_hours > 24:
            raise InputError("--shift-hours must be between 0 and 24")
        if args.growth is not None and (not math.isfinite(args.growth) or args.growth <= 0):
            raise InputError("--growth must be more than 0")
        rules, fallback = load_rules(args.rules)
        tickets = load_tickets(args.tickets, args.date_format)
        if len(tickets) > BIG_TICKETS:
            print(f"warning: {len(tickets):,} tickets; above {BIG_TICKETS:,} the run can take minutes and several "
                  "hundred MB of memory. One holiday season (Nov 1 to Jan 31) is enough.", file=sys.stderr)
        orders, args.order_notes = load_orders(args.orders, args.prefer_plan)
        existing = load_existing_macros(args.macros)
        a = analyze(args, tickets, rules, fallback, args.policies, args.cutoffs, orders, existing, as_of)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(a))
    if args.out_dir:
        write_outputs(a, args.out_dir)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(json_summary(a), f, indent=2, sort_keys=True)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
