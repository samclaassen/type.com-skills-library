#!/usr/bin/env python3
"""Analyze Google Search Console performance exports period over period.

Stdlib only, offline, deterministic. Every "today" comes from --as-of.

Sections produced (see DATA_CONTRACT.md for the input files):
  summary             clicks, impressions, impressions-weighted position, deltas
  declining pages     clicks down more than decline_min_drop_pct, ranked by loss
  CTR opportunities   positions 1-5 with CTR below the expected-CTR curve
  rising queries      queries gaining impressions or position, or new queries
  cannibalization     queries where several URLs each hold a real impression share
  page-1 opportunities  queries at positions 8-12, est. gain if moved to position 5
  device & country    split for the top queries (optional inputs)

Observed numbers come straight from the export. Anything projected (click gains)
is labelled "est.". "Likely cause" is a data observation, never a causal claim.

Exit codes: 0 ok, 2 bad input (message names the file, row and column).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "settings.json"
DEFAULT_CURVE = SCRIPT_DIR / "ctr_curve.json"

MINUS = "−"
ARROW = "→"

ALIASES = {
    "query": {"query", "queries", "top queries", "top query"},
    "page": {"page", "pages", "top pages", "top page", "url", "landing page"},
    "clicks": {"clicks", "url clicks"},
    "impressions": {"impressions"},
    "ctr": {"ctr", "url ctr"},
    "position": {"position", "avg. position", "average position", "avg position"},
    "device": {"device"},
    "country": {"country"},
    "period": {"period"},
}

FINDING_TYPES = [
    "declining_page",
    "ctr_opportunity",
    "rising_query",
    "cannibalization",
    "page1_opportunity",
]
PRIORITY_METRICS = {
    "declining_page": "click_loss",
    "ctr_opportunity": "est_click_gain",
    "rising_query": "impression_gain",
    "cannibalization": "query_impressions",
    "page1_opportunity": "est_click_gain",
}
CAUSE_CODES = ["position_drop", "impressions_drop", "ctr_drop", "absent", "unexplained"]

REQUIRED_CONFIG = {
    "gsc_property": str,
    "compare_period": int,
    "data_lag_days": int,
    "min_impressions": (int, float),
    "min_position_change": (int, float),
    "ctr_benchmark": str,
    "ctr_curve_custom": dict,
    "cannibalization_threshold": int,
    "cannibalization_min_impression_share": (int, float),
    "low_volume_click_threshold": (int, float),
    "max_rows_per_section": int,
    "max_priority_actions": int,
    "decline_min_drop_pct": (int, float),
    "decline_min_impressions": (int, float),
    "decline_min_click_loss": (int, float),
    "cause_min_position_worsening": (int, float),
    "cause_min_impression_drop_pct": (int, float),
    "cause_min_ctr_drop_pct": (int, float),
    "ctr_max_position": (int, float),
    "ctr_min_gap_pp": (int, float),
    "rising_min_impression_growth_pct": (int, float),
    "rising_min_impression_gain": (int, float),
    "rising_new_query_min_impressions": (int, float),
    "page1_min_position": (int, float),
    "page1_max_position": (int, float),
    "page1_target_position": (int, float),
    "breakdown_top_queries": int,
    "breakdown_top_countries": int,
    "priority_rules": dict,
    "section_actions": dict,
    "finding_actions": dict,
}


class InputError(Exception):
    """Unusable input; the message names the file, row and column."""


class NoDataError(InputError):
    """A metrics file has a header but no data rows."""


OPTIONAL_CONFIG_DEFAULTS = {
    "breakdown_min_country_share": 0.01,
    "exclude_query_patterns": [],
    "brand_terms": [],
}


# --------------------------------------------------------------------------- config


def load_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise InputError(f"{label}: file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: {path}: invalid JSON at line {exc.lineno}: {exc.msg}")
    if not isinstance(data, dict):
        raise InputError(f"{label}: {path}: top level must be a JSON object")
    return data


def load_config(path: Path) -> dict:
    cfg = load_json(path, "config")
    for key, typ in REQUIRED_CONFIG.items():
        if key not in cfg:
            raise InputError(f"config: {path}: missing key '{key}'")
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, typ):
            raise InputError(f"config: {path}: key '{key}' has the wrong type ({type(val).__name__})")
    if cfg["compare_period"] < 1:
        raise InputError(f"config: {path}: 'compare_period' must be >= 1")
    if cfg["data_lag_days"] < 0:
        raise InputError(f"config: {path}: 'data_lag_days' must be >= 0")
    if cfg["cannibalization_threshold"] < 2:
        raise InputError(f"config: {path}: 'cannibalization_threshold' must be >= 2")
    if not 0 <= cfg["cannibalization_min_impression_share"] <= 1:
        raise InputError(f"config: {path}: 'cannibalization_min_impression_share' must be between 0 and 1")
    if cfg["page1_min_position"] > cfg["page1_max_position"]:
        raise InputError(f"config: {path}: 'page1_min_position' is greater than 'page1_max_position'")
    bench = cfg["ctr_benchmark"].strip().lower() or "auto"
    if bench not in ("auto", "custom"):
        raise InputError(f"config: {path}: 'ctr_benchmark' must be auto or custom, got '{cfg['ctr_benchmark']}'")
    cfg["ctr_benchmark"] = bench
    for ftype in FINDING_TYPES:
        rule = cfg["priority_rules"].get(ftype)
        if not isinstance(rule, dict):
            raise InputError(f"config: {path}: priority_rules.{ftype} is missing")
        if rule.get("metric") != PRIORITY_METRICS[ftype]:
            raise InputError(
                f"config: {path}: priority_rules.{ftype}.metric must be '{PRIORITY_METRICS[ftype]}'"
            )
        for level in ("critical", "high"):
            v = rule.get(level, "missing")
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise InputError(f"config: {path}: priority_rules.{ftype}.{level} must be a number or null")
        if ftype not in cfg["section_actions"]:
            raise InputError(f"config: {path}: section_actions.{ftype} is missing")
    for key in [f"declining_page:{c}" for c in CAUSE_CODES] + FINDING_TYPES[1:]:
        if key not in cfg["finding_actions"]:
            raise InputError(f"config: {path}: finding_actions.{key} is missing")
    # Optional keys: older settings copies without them keep working.
    for key, default in OPTIONAL_CONFIG_DEFAULTS.items():
        cfg.setdefault(key, default)
    share = cfg["breakdown_min_country_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1:
        raise InputError(f"config: {path}: 'breakdown_min_country_share' must be a number between 0 and 1")
    for key in ("exclude_query_patterns", "brand_terms"):
        val = cfg[key]
        if not isinstance(val, list) or not all(isinstance(x, str) and x.strip() for x in val):
            raise InputError(f"config: {path}: '{key}' must be a list of non-empty strings")
    compiled = []
    for pat in cfg["exclude_query_patterns"]:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error as exc:
            raise InputError(f"config: {path}: exclude_query_patterns entry '{pat}' is not a valid regex: {exc}")
    cfg["_exclude_res"] = compiled
    cfg["_brand_res"] = [brand_term_re(t) for t in cfg["brand_terms"]]
    return cfg


def brand_term_re(term: str) -> re.Pattern:
    """Whole-word, case-insensitive match for a brand term. Anything that is not a letter or digit
    (space, '.', '-', ...) counts as a word boundary, so "type" matches "type.com" and "type com"
    but not "typeform". Spaces inside a term match any run of separators ("harbor and vine"
    also matches "harbor-and-vine")."""
    words = [re.escape(w) for w in re.split(r"[^\w]+", term.strip().lower()) if w]
    return re.compile(r"(?<![^\W_])" + r"[\W_]+".join(words) + r"(?![^\W_])", re.IGNORECASE)


def query_excluded(cfg: dict, query: str) -> bool:
    """True when the query matches brand_terms (whole words) or exclude_query_patterns (regex)."""
    if any(r.search(query) for r in cfg["_brand_res"]):
        return True
    return any(r.search(query) for r in cfg["_exclude_res"])


def parse_curve(points: dict, label: str) -> list[tuple[float, float]]:
    curve = []
    for k, v in points.items():
        try:
            pos = float(k)
        except ValueError:
            raise InputError(f"{label}: position key '{k}' is not a number")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise InputError(f"{label}: CTR for position {k} must be a fraction between 0 and 1")
        if pos < 1:
            raise InputError(f"{label}: position key '{k}' must be >= 1")
        curve.append((pos, float(v)))
    if len(curve) < 2:
        raise InputError(f"{label}: needs at least 2 position/CTR points")
    curve.sort()
    return curve


def load_curve(cfg: dict, curve_path: Path) -> tuple[list[tuple[float, float]], str]:
    if cfg["ctr_benchmark"] == "custom":
        return parse_curve(cfg["ctr_curve_custom"], "config: ctr_curve_custom"), "custom curve (config)"
    data = load_json(curve_path, "ctr curve")
    if not isinstance(data.get("positions"), dict):
        raise InputError(f"ctr curve: {curve_path}: missing 'positions' object")
    return parse_curve(data["positions"], f"ctr curve: {curve_path}"), "auto curve (ctr_curve.json)"


def expected_ctr(curve: list[tuple[float, float]], pos: float) -> float:
    if pos <= curve[0][0]:
        return curve[0][1]
    for (p0, c0), (p1, c1) in zip(curve, curve[1:]):
        if pos <= p1:
            return c0 + (c1 - c0) * (pos - p0) / (p1 - p0)
    return curve[-1][1]


# --------------------------------------------------------------------------- csv


def canon(name: str) -> str | None:
    n = " ".join(name.strip().lower().split())
    for key, names in ALIASES.items():
        if n in names:
            return key
    return None


def parse_count(raw: str, where: str) -> int:
    s = raw.strip().replace(",", "")
    if s == "":
        raise InputError(f"{where}: value is missing")
    try:
        f = float(s)
    except ValueError:
        raise InputError(f"{where}: '{raw}' is not a number")
    if f < 0 or f != int(f):
        raise InputError(f"{where}: '{raw}' must be a non-negative whole number")
    return int(f)


def parse_position(raw: str, where: str) -> float:
    s = raw.strip().replace(",", "")
    if s == "":
        raise InputError(f"{where}: value is missing")
    try:
        f = float(s)
    except ValueError:
        raise InputError(f"{where}: '{raw}' is not a number")
    if f < 1:
        raise InputError(f"{where}: '{raw}' must be >= 1 (Search Console positions start at 1)")
    return f


def parse_ctr(raw: str, where: str) -> None:
    s = raw.strip()
    if s == "":
        return
    try:
        float(s[:-1] if s.endswith("%") else s)
    except ValueError:
        raise InputError(f"{where}: '{raw}' is not a CTR (use 0.032 or 3.2%)")


def read_metrics(path: Path, label: str, dims_allowed: list[tuple[str, ...]]) -> tuple[tuple[str, ...], list[dict]]:
    """Read a metrics CSV. Returns (dimensions, rows). Rows carry dims + clicks/impressions/position."""
    if not path.is_file():
        raise InputError(f"{label}: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise InputError(f"{label}: {path}: file is empty")
        cols: dict[str, int] = {}
        for i, h in enumerate(header):
            c = canon(h)
            if c is None:
                continue
            if c in cols:
                raise InputError(f"{label}: {path}: header has two '{c}' columns")
            cols[c] = i
        for need in ("clicks", "impressions", "position"):
            if need not in cols:
                raise InputError(f"{label}: {path}: missing required column '{need}'")
        dims = tuple(d for d in ("query", "page", "device", "country") if d in cols)
        if dims not in dims_allowed:
            want = " or ".join("+".join(d) for d in dims_allowed)
            got = "+".join(dims) or "none"
            raise InputError(f"{label}: {path}: dimension columns are {got}; expected {want}")
        rows = []
        seen: dict[tuple, int] = {}
        for line_no, rec in enumerate(reader, start=2):
            if not rec or all(not x.strip() for x in rec):
                continue
            if len(rec) < len(header):
                rec = rec + [""] * (len(header) - len(rec))

            def at(col: str) -> str:
                return rec[cols[col]]

            row: dict = {}
            for d in dims:
                v = at(d).strip()
                if v == "":
                    raise InputError(f"{label}: {path}: row {line_no}, column '{d}': value is missing")
                row[d] = v.lower() if d in ("device", "country") else v
            row["clicks"] = parse_count(at("clicks"), f"{label}: {path}: row {line_no}, column 'clicks'")
            row["impressions"] = parse_count(at("impressions"), f"{label}: {path}: row {line_no}, column 'impressions'")
            row["position"] = parse_position(at("position"), f"{label}: {path}: row {line_no}, column 'position'")
            if "ctr" in cols:
                parse_ctr(at("ctr"), f"{label}: {path}: row {line_no}, column 'ctr'")
            if row["clicks"] > row["impressions"]:
                raise InputError(
                    f"{label}: {path}: row {line_no}, column 'clicks': {row['clicks']} clicks exceed "
                    f"{row['impressions']} impressions"
                )
            key = tuple(row[d] for d in dims)
            if key in seen:
                raise InputError(
                    f"{label}: {path}: row {line_no}, columns '{'+'.join(dims)}': duplicate of row {seen[key]}"
                )
            seen[key] = line_no
            row["_line"] = line_no
            rows.append(row)
    if not rows:
        raise NoDataError(f"{label}: {path}: no data rows")
    return dims, rows


def read_totals_file(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise InputError(f"totals: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputError(f"totals: {path}: file is empty")
        names = {canon(h): h for h in reader.fieldnames if canon(h)}
        for need in ("period", "clicks", "impressions", "position"):
            if need not in names:
                raise InputError(f"totals: {path}: missing required column '{need}'")
        out: dict[str, dict] = {}
        for line_no, rec in enumerate(reader, start=2):
            period = (rec[names["period"]] or "").strip().lower()
            if period not in ("current", "prior"):
                raise InputError(f"totals: {path}: row {line_no}, column 'period': must be current or prior")
            if period in out:
                raise InputError(f"totals: {path}: row {line_no}, column 'period': duplicate '{period}'")
            w = f"totals: {path}: row {line_no}, column"
            clicks = parse_count(rec[names["clicks"]] or "", f"{w} 'clicks'")
            impr = parse_count(rec[names["impressions"]] or "", f"{w} 'impressions'")
            pos = parse_position(rec[names["position"]] or "", f"{w} 'position'")
            if clicks > impr:
                raise InputError(f"{w} 'clicks': {clicks} clicks exceed {impr} impressions")
            out[period] = Agg(clicks, impr, pos * impr)
    for p in ("current", "prior"):
        if p not in out:
            raise InputError(f"totals: {path}: no row for period '{p}'")
    return out


# --------------------------------------------------------------------------- aggregation


class Agg:
    __slots__ = ("clicks", "impressions", "pos_weight")

    def __init__(self, clicks: int = 0, impressions: int = 0, pos_weight: float = 0.0):
        self.clicks = clicks
        self.impressions = impressions
        self.pos_weight = pos_weight

    def add(self, row: dict) -> None:
        self.clicks += row["clicks"]
        self.impressions += row["impressions"]
        self.pos_weight += row["position"] * row["impressions"]

    @property
    def position(self) -> float | None:
        # Impressions-weighted mean of the rows' average positions.
        return self.pos_weight / self.impressions if self.impressions else None

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0


def group(rows: list[dict], keys: tuple[str, ...]) -> dict[tuple, Agg]:
    out: dict[tuple, Agg] = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        out.setdefault(k, Agg()).add(r)
    return out


def total(rows: list[dict]) -> Agg:
    a = Agg()
    for r in rows:
        a.add(r)
    return a


# --------------------------------------------------------------------------- formatting


def fint(n: float) -> str:
    s = f"{abs(round(n)):,}"
    return (MINUS + s) if round(n) < 0 else s


def fpct_change(now: float, prior: float) -> str:
    if prior == 0:
        return "new" if now else "0%"
    ch = (now - prior) / prior * 100
    r = round(ch)
    return ("+" if r >= 0 else MINUS) + f"{abs(r)}%"


def fctr(x: float) -> str:
    return f"{x * 100:.1f}%"


def fpos(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}"


def fpp(x: float) -> str:
    v = round(x * 100, 1)
    return ("+" if v >= 0 else MINUS) + f"{abs(v):.1f}pp"


def fdelta1(x: float) -> str:
    v = round(x, 1)
    return ("+" if v >= 0 else MINUS) + f"{abs(v):.1f}"


def fdate(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def frange(a: date, b: date) -> str:
    return f"{fdate(a)}–{fdate(b)}"


def md_cell(s: str) -> str:
    return s.replace("|", "\\|")


def host_of(prop: str) -> str:
    p = prop.strip()
    if p.startswith("sc-domain:"):
        return p[len("sc-domain:"):].strip("/").lower()
    for scheme in ("https://", "http://"):
        if p.lower().startswith(scheme):
            p = p[len(scheme):]
    return p.strip("/").lower()


def short_url(url: str, host: str) -> str:
    u = url
    for scheme in ("https://", "http://"):
        if u.lower().startswith(scheme):
            rest = u[len(scheme):]
            h, _, path = rest.partition("/")
            hl = h.lower()
            if hl == host or hl == "www." + host or (host and hl.endswith("." + host) and hl.startswith("www.")):
                return "/" + path
            return u
    return u


# --------------------------------------------------------------------------- periods


def windows(as_of: date, cfg: dict) -> dict:
    n = cfg["compare_period"]
    cur_end = as_of - timedelta(days=cfg["data_lag_days"])
    cur_start = cur_end - timedelta(days=n - 1)
    pri_end = cur_start - timedelta(days=1)
    pri_start = pri_end - timedelta(days=n - 1)
    return {"current": (cur_start, cur_end), "prior": (pri_start, pri_end)}


def largest_period(as_of: date, cfg: dict, first_data: date) -> int:
    """Largest compare_period whose prior window starts on or after first_data (0 if none fits)."""
    cur_end = as_of - timedelta(days=cfg["data_lag_days"])
    return max(0, ((cur_end - first_data).days + 1) // 2)


def full_window_date(cfg: dict, first_data: date) -> date:
    """First --as-of date at which the configured compare_period fits entirely after first_data."""
    return first_data + timedelta(days=2 * cfg["compare_period"] - 1 + cfg["data_lag_days"])


def no_prior_data_help(args, cfg: dict, win: dict, partial: bool = False) -> str:
    n = cfg["compare_period"]
    ps, pe = win["prior"]
    if partial:
        msg = (
            f"the prior period ({ps.isoformat()} to {pe.isoformat()}) starts before the first date with data "
            f"({args.first_data_date.isoformat()}), so it would be partly or wholly empty and every change would "
            "look inflated. Nothing was analyzed. "
        )
    else:
        msg = (
            f"the prior period ({ps.isoformat()} to {pe.isoformat()}) has no Search Console data, so there is "
            "nothing to compare against. This is normal for a new or recently verified property. "
            "Nothing was analyzed. "
        )
    first = getattr(args, "first_data_date", None)
    if first is not None:
        fit = largest_period(args.as_of, cfg, first)
        when = full_window_date(cfg, first).isoformat()
        if fit >= 1:
            msg += (f"With data starting {first.isoformat()}, the largest compare_period that fits is {fit} days: "
                    f"set compare_period to {fit} in your settings file and pull the data again, "
                    f"or wait until --as-of {when} for the full {n}-day comparison.")
        else:
            msg += (f"Data starts {first.isoformat()}, which is too recent for any comparison; "
                    f"wait until --as-of {when} for the full {n}-day comparison.")
    else:
        msg += ("Options: use a shorter compare_period that fits the data you have (run again with "
                "--first-data-date YYYY-MM-DD to compute it; the earliest date with data comes from the "
                f"first_data_date_probe request in --plan), or wait until the property has {2 * n} days of data.")
    return msg


def api_plan(prop: str, win: dict) -> dict:
    endpoint = (
        "https://searchconsole.googleapis.com/webmasters/v3/sites/"
        + quote(prop, safe="")
        + "/searchAnalytics/query"
    )

    def body(period: str, dims: list[str]) -> dict:
        s, e = win[period]
        b = {
            "startDate": s.isoformat(),
            "endDate": e.isoformat(),
            "dimensions": dims,
            "type": "web",
            "dataState": "final",
        }
        if dims:
            b["rowLimit"] = 25000
            b["startRow"] = 0
        return b

    reqs = []
    for period in ("current", "prior"):
        reqs.append({"name": f"{period}_query_page", "output_csv": f"{period}.csv",
                     "body": body(period, ["query", "page"])})
    for period in ("current", "prior"):
        reqs.append({"name": f"{period}_totals", "output_csv": "totals.csv",
                     "body": body(period, [])})
    reqs.append({"name": "current_query_device", "output_csv": "devices.csv",
                 "body": body("current", ["query", "device"])})
    reqs.append({"name": "current_query_country", "output_csv": "countries.csv",
                 "body": body("current", ["query", "country"])})
    probe_end = win["current"][1]
    return {
        "property": prop,
        "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        "method": "POST",
        "endpoint": endpoint,
        "periods": {k: {"startDate": v[0].isoformat(), "endDate": v[1].isoformat()} for k, v in win.items()},
        "paging": "Repeat each request with startRow += 25000 until a response returns fewer than 25000 rows (or no 'rows' key).",
        "requests": reqs,
        "first_data_date_probe": {
            "purpose": "Optional. Only if the prior period has no data (new property): the earliest keys[0] "
                       "in the response is the first date with data; pass it as --first-data-date.",
            "body": {"startDate": (probe_end - timedelta(days=499)).isoformat(),
                     "endDate": probe_end.isoformat(), "dimensions": ["date"],
                     "type": "web", "dataState": "final", "rowLimit": 25000},
        },
    }


# --------------------------------------------------------------------------- analysis


def priority_of(cfg: dict, ftype: str, value: float, low_volume: bool) -> str:
    if low_volume:
        return "unrated"
    rule = cfg["priority_rules"][ftype]
    if rule.get("critical") is not None and value >= rule["critical"]:
        return "critical"
    if rule.get("high") is not None and value >= rule["high"]:
        return "high"
    return "medium"


def fill(template: str, fallback: tuple[str, ...] = (), **kw) -> str:
    """Fill {placeholders}. Keys listed in fallback hold a descriptive phrase, not a real
    query or URL, so any quotes the template puts around them are dropped."""
    out = template
    for k, v in kw.items():
        if k in fallback:
            out = out.replace('"{' + k + '}"', str(v))
        out = out.replace("{" + k + "}", str(v))
    return out


def analyze(args, cfg: dict, curve: list[tuple[float, float]], curve_label: str) -> dict:
    prop = args.property or cfg["gsc_property"]
    host = host_of(prop)
    win = windows(args.as_of, cfg)

    full_dims = [("query", "page"), ("query",)]
    try:
        cur_dims, cur_rows = read_metrics(Path(args.current), "current", full_dims)
    except NoDataError as exc:
        cs, ce = win["current"]
        raise InputError(
            f"{exc}: the current period ({cs.isoformat()} to {ce.isoformat()}) has no Search Console data. "
            "Check the property is verified and the export covers these dates. Nothing was analyzed."
        )
    try:
        pri_dims, pri_rows = read_metrics(Path(args.prior), "prior", full_dims)
    except NoDataError as exc:
        raise InputError(f"{exc}: {no_prior_data_help(args, cfg, win)}")
    if cur_dims != pri_dims:
        raise InputError(
            f"prior: {args.prior}: has dimensions {'+'.join(pri_dims)} but current has {'+'.join(cur_dims)}; "
            "both periods must use the same export shape"
        )
    full = cur_dims == ("query", "page")
    mode = "full (query x page)" if full else "reduced (Search Console UI export: queries and pages separately)"

    cur_pages_rows = pri_pages_rows = None
    if not full:
        if bool(args.current_pages) != bool(args.prior_pages):
            raise InputError("pages: pass both --current-pages and --prior-pages, or neither")
        if args.current_pages:
            _, cur_pages_rows = read_metrics(Path(args.current_pages), "current-pages", [("page",)])
            try:
                _, pri_pages_rows = read_metrics(Path(args.prior_pages), "prior-pages", [("page",)])
            except NoDataError as exc:
                raise InputError(f"{exc}: {no_prior_data_help(args, cfg, win)}")
    elif args.current_pages or args.prior_pages:
        raise InputError("pages: --current-pages/--prior-pages are only for reduced mode; full exports already contain pages")

    dev_rows = ctry_rows = None
    if args.devices:
        _, dev_rows = read_metrics(Path(args.devices), "devices", [("query", "device")])
        for r in dev_rows:
            if r["device"] not in ("desktop", "mobile", "tablet"):
                raise InputError(
                    f"devices: {args.devices}: row {r['_line']}, column 'device': "
                    f"'{r['device']}' is not DESKTOP, MOBILE or TABLET"
                )
    if args.countries:
        _, ctry_rows = read_metrics(Path(args.countries), "countries", [("query", "country")])
    totals = read_totals_file(Path(args.totals)) if args.totals else None

    low_volume_threshold = cfg["low_volume_click_threshold"]
    min_impr = cfg["min_impressions"]

    # ---- per-query aggregates (both modes)
    q_cur = {k[0]: v for k, v in group(cur_rows, ("query",)).items()}
    q_pri = {k[0]: v for k, v in group(pri_rows, ("query",)).items()}

    # ---- per-page aggregates
    if full:
        p_cur = {k[0]: v for k, v in group(cur_rows, ("page",)).items()}
        p_pri = {k[0]: v for k, v in group(pri_rows, ("page",)).items()}
        qp_cur = group(cur_rows, ("page", "query"))
        qp_pri = group(pri_rows, ("page", "query"))
    elif cur_pages_rows is not None:
        p_cur = {k[0]: v for k, v in group(cur_pages_rows, ("page",)).items()}
        p_pri = {k[0]: v for k, v in group(pri_pages_rows, ("page",)).items()}
        qp_cur = qp_pri = None
    else:
        p_cur = p_pri = None
        qp_cur = qp_pri = None

    # ---- summary
    if totals:
        s_cur, s_pri, s_source = totals["current"], totals["prior"], "property totals (--totals)"
    elif full:
        s_cur, s_pri, s_source = total(cur_rows), total(pri_rows), "sum of query x page rows"
    elif cur_pages_rows is not None:
        s_cur, s_pri, s_source = total(cur_pages_rows), total(pri_pages_rows), "sum of Pages export rows"
    else:
        s_cur, s_pri, s_source = total(cur_rows), total(pri_rows), "sum of Queries export rows"
    low_volume = s_cur.clicks < low_volume_threshold

    findings: list[dict] = []

    # ---- declining pages
    declining = None
    if p_cur is not None:
        declining = []
        for page in sorted(set(p_pri)):
            pr = p_pri[page]
            cu = p_cur.get(page, Agg())
            if pr.clicks <= 0:
                continue
            loss = pr.clicks - cu.clicks
            drop_pct = loss / pr.clicks * 100
            if drop_pct <= cfg["decline_min_drop_pct"]:
                continue
            if max(pr.impressions, cu.impressions) <= cfg["decline_min_impressions"]:
                continue
            if loss < cfg["decline_min_click_loss"]:
                continue
            obs, code = [], None
            if cu.impressions == 0:
                obs.append("No impressions this period")
                code = "absent"
            else:
                pos_w = cu.position - pr.position
                impr_ch = (cu.impressions - pr.impressions) / pr.impressions * 100 if pr.impressions else 0.0
                ctr_ch = (cu.ctr - pr.ctr) / pr.ctr * 100 if pr.ctr else 0.0
                if pos_w >= cfg["cause_min_position_worsening"]:
                    obs.append(f"Avg position {fpos(pr.position)}{ARROW}{fpos(cu.position)}")
                    code = "position_drop"
                    if -impr_ch >= cfg["cause_min_impression_drop_pct"]:
                        obs.append(f"impressions {fpct_change(cu.impressions, pr.impressions)}")
                elif -impr_ch >= cfg["cause_min_impression_drop_pct"]:
                    obs.append(
                        f"Impressions {fpct_change(cu.impressions, pr.impressions)} at stable position "
                        "(consistent with lower demand)"
                    )
                    code = "impressions_drop"
                if code is None and -ctr_ch >= cfg["cause_min_ctr_drop_pct"]:
                    obs.append(f"CTR {fctr(pr.ctr)}{ARROW}{fctr(cu.ctr)} at similar position")
                    code = "ctr_drop"
                if code is None:
                    obs.append("No single metric shift; inspect manually")
                    code = "unexplained"
            top_query = None
            if qp_pri is not None:
                best = None
                for (pg, q), a in qp_pri.items():
                    if pg != page:
                        continue
                    c = qp_cur.get((pg, q), Agg()).clicks
                    cand = (a.clicks - c, a.clicks, q)
                    if best is None or (cand[0], cand[1]) > (best[0], best[1]) or (
                        (cand[0], cand[1]) == (best[0], best[1]) and q < best[2]
                    ):
                        best = cand
                top_query = best[2] if best else None
            item = {
                "page": page,
                "clicks_now": cu.clicks,
                "clicks_prior": pr.clicks,
                "click_loss": loss,
                "change_pct": round(-drop_pct, 1),
                "impressions_now": cu.impressions,
                "impressions_prior": pr.impressions,
                "position_now": None if cu.position is None else round(cu.position, 2),
                "position_prior": round(pr.position, 2),
                "top_query": top_query,
                "likely_cause": "; ".join(obs),
                "cause_code": code,
                "priority": priority_of(cfg, "declining_page", loss, low_volume),
            }
            declining.append(item)
        declining.sort(key=lambda x: (-x["click_loss"], x["page"]))
        for d in declining:
            findings.append({
                "type": "declining_page", "priority": d["priority"], "value": d["click_loss"],
                "label": f"{short_url(d['page'], host)} lost {fint(d['click_loss'])} clicks (observed)",
                "action": fill(cfg["finding_actions"][f"declining_page:{d['cause_code']}"],
                               fallback=() if d["top_query"] else ("query",),
                               page=short_url(d["page"], host), query=d["top_query"] or "its top query"),
            })

    # best (lowest position) page per query, used for action text in full mode
    def ranking_page(query: str) -> str | None:
        if qp_cur is None:
            return None
        best = None
        for (pg, q), a in qp_cur.items():
            if q != query or a.position is None:
                continue
            cand = (a.position, -a.clicks, pg)
            if best is None or cand < best:
                best = cand
        return best[2] if best else None

    # ---- cannibalization
    cannibal = None
    if full:
        cannibal = []
        by_query: dict[str, list[tuple[str, Agg]]] = {}
        for (pg, q), a in qp_cur.items():
            by_query.setdefault(q, []).append((pg, a))
        for q in sorted(by_query):
            qa = q_cur[q]
            if qa.impressions < min_impr:
                continue
            pages = [
                (pg, a) for pg, a in by_query[q]
                if a.impressions / qa.impressions >= cfg["cannibalization_min_impression_share"]
            ]
            if len(pages) < cfg["cannibalization_threshold"]:
                continue
            pages.sort(key=lambda x: (x[1].position, -x[1].clicks, x[0]))
            best_pg, best_a = pages[0]
            cannibal.append({
                "query": q,
                "query_impressions": qa.impressions,
                "urls": [{"page": pg, "impressions": a.impressions, "clicks": a.clicks,
                          "share": round(a.impressions / qa.impressions, 3),
                          "position": round(a.position, 2)} for pg, a in pages],
                "best_page": best_pg,
                "best_position": round(best_a.position, 2),
                "priority": priority_of(cfg, "cannibalization", qa.impressions, low_volume),
            })
        cannibal.sort(key=lambda x: (-x["query_impressions"], x["query"]))
        for c in cannibal:
            findings.append({
                "type": "cannibalization", "priority": c["priority"], "value": c["query_impressions"],
                "label": f"\"{c['query']}\" split across {len(c['urls'])} URLs (observed)",
                "action": fill(cfg["finding_actions"]["cannibalization"], query=c["query"],
                               page=short_url(c["best_page"], host)),
            })

    cannibalized = {c["query"] for c in cannibal} if cannibal else set()
    excluded: dict[str, list[str]] = {"ctr_opportunity": [], "page1_opportunity": []}
    excluded_by_pattern: dict[str, list[str]] = {"ctr_opportunity": [], "page1_opportunity": []}

    # ---- CTR opportunities
    ctr_ops = []
    for q in sorted(q_cur):
        a = q_cur[q]
        if a.impressions < min_impr or a.position is None or a.position > cfg["ctr_max_position"]:
            continue
        if query_excluded(cfg, q):
            excluded_by_pattern["ctr_opportunity"].append(q)
            continue
        if q in cannibalized:
            excluded["ctr_opportunity"].append(q)
            continue
        exp = expected_ctr(curve, a.position)
        gap = a.ctr - exp
        if gap > -cfg["ctr_min_gap_pp"] / 100:
            continue
        gain = a.impressions * (exp - a.ctr)
        ctr_ops.append({
            "query": q, "position": round(a.position, 2), "impressions": a.impressions,
            "clicks": a.clicks, "ctr": round(a.ctr, 4), "expected_ctr": round(exp, 4),
            "gap_pp": round(gap * 100, 1), "est_click_gain": round(gain),
            "priority": priority_of(cfg, "ctr_opportunity", round(gain), low_volume),
        })
    ctr_ops.sort(key=lambda x: (-x["est_click_gain"], x["query"]))
    for c in ctr_ops:
        findings.append({
            "type": "ctr_opportunity", "priority": c["priority"], "value": c["est_click_gain"],
            "label": f"\"{c['query']}\" est. +{fint(c['est_click_gain'])} clicks if CTR met benchmark",
            "action": fill(cfg["finding_actions"]["ctr_opportunity"], query=c["query"],
                           position=fpos(c["position"]), gap=fpp(c["gap_pp"] / 100)),
        })

    # ---- rising queries
    rising = []
    for q in sorted(q_cur):
        a = q_cur[q]
        if a.impressions < min_impr:
            continue
        p = q_pri.get(q)
        if p is None or p.impressions == 0:
            if a.impressions >= cfg["rising_new_query_min_impressions"]:
                rising.append({"query": q, "impressions": a.impressions, "impressions_prior": 0,
                               "impression_gain": a.impressions, "position_now": round(a.position, 2),
                               "position_prior": None, "trend": "New this period"})
            continue
        gain = a.impressions - p.impressions
        growth = gain / p.impressions * 100
        pos_gain = p.position - a.position
        by_impr = growth >= cfg["rising_min_impression_growth_pct"] and gain >= cfg["rising_min_impression_gain"]
        by_pos = pos_gain >= cfg["min_position_change"]
        if not (by_impr or by_pos):
            continue
        parts = []
        if gain:
            parts.append(f"Impr {fpct_change(a.impressions, p.impressions)}")
        if abs(pos_gain) >= 0.05:
            arrow = "↑" if pos_gain > 0 else "↓"
            parts.append(f"pos {arrow}{abs(pos_gain):.1f}")
        rising.append({"query": q, "impressions": a.impressions, "impressions_prior": p.impressions,
                       "impression_gain": gain, "position_now": round(a.position, 2),
                       "position_prior": round(p.position, 2), "trend": ", ".join(parts)})
    rising.sort(key=lambda x: (-x["impression_gain"], x["query"]))
    for r in rising:
        r["priority"] = priority_of(cfg, "rising_query", r["impression_gain"], low_volume)
        findings.append({
            "type": "rising_query", "priority": r["priority"], "value": r["impression_gain"],
            "label": f"\"{r['query']}\" +{fint(r['impression_gain'])} impressions (observed)",
            "action": fill(cfg["finding_actions"]["rising_query"], query=r["query"]),
        })

    # ---- page-1 opportunities
    page1 = []
    target = cfg["page1_target_position"]
    target_ctr = expected_ctr(curve, target)
    for q in sorted(q_cur):
        a = q_cur[q]
        if a.impressions < min_impr or a.position is None:
            continue
        if not cfg["page1_min_position"] <= a.position <= cfg["page1_max_position"]:
            continue
        if query_excluded(cfg, q):
            excluded_by_pattern["page1_opportunity"].append(q)
            continue
        if q in cannibalized:
            excluded["page1_opportunity"].append(q)
            continue
        gain = a.impressions * (target_ctr - a.ctr)
        if round(gain) <= 0:
            continue
        page1.append({"query": q, "position": round(a.position, 2), "impressions": a.impressions,
                      "clicks": a.clicks, "ctr": round(a.ctr, 4), "target_ctr": round(target_ctr, 4),
                      "est_click_gain": round(gain), "ranking_page": ranking_page(q),
                      "priority": priority_of(cfg, "page1_opportunity", round(gain), low_volume)})
    page1.sort(key=lambda x: (-x["est_click_gain"], x["query"]))
    for p in page1:
        findings.append({
            "type": "page1_opportunity", "priority": p["priority"], "value": p["est_click_gain"],
            "label": f"\"{p['query']}\" est. +{fint(p['est_click_gain'])} clicks if moved to position {target:g}",
            "action": fill(cfg["finding_actions"]["page1_opportunity"], query=p["query"],
                           position=fpos(p["position"]),
                           fallback=() if p["ranking_page"] else ("page",),
                           page=short_url(p["ranking_page"], host) if p["ranking_page"] else "the ranking page"),
        })

    # ---- device / country breakdown for top queries
    top_q = sorted(q_cur, key=lambda q: (-q_cur[q].clicks, q))[: cfg["breakdown_top_queries"]]
    breakdown = None
    if dev_rows is not None or ctry_rows is not None:
        breakdown = []
        dev = group(dev_rows, ("query", "device")) if dev_rows is not None else None
        ctry = group(ctry_rows, ("query", "country")) if ctry_rows is not None else None
        for q in top_q:
            entry = {"query": q, "clicks": q_cur[q].clicks, "devices": None, "countries": None, "note": ""}
            if dev is not None:
                ds = {d: a for (qq, d), a in dev.items() if qq == q}
                if ds:
                    tot = sum(a.clicks for a in ds.values())
                    entry["devices"] = {
                        d: {"clicks": a.clicks, "share": round(a.clicks / tot, 3) if tot else 0.0,
                            "position": round(a.position, 2) if a.position else None}
                        for d, a in sorted(ds.items())
                    }
                    poss = [(a.position, d) for d, a in ds.items() if a.position is not None and a.impressions >= min_impr]
                    if len(poss) >= 2:
                        lo, hi = min(poss), max(poss)
                        if hi[0] - lo[0] >= cfg["min_position_change"]:
                            entry["note"] = f"{hi[1].capitalize()} pos {hi[0]:.1f} vs {lo[1]} {lo[0]:.1f}"
            if ctry is not None:
                cs = sorted(((a.clicks, c) for (qq, c), a in ctry.items() if qq == q), key=lambda x: (-x[0], x[1]))
                if cs:
                    tot = sum(x[0] for x in cs)
                    # Zero-click countries and those under breakdown_min_country_share are left out.
                    entry["countries"] = [
                        {"country": c, "clicks": n, "share": round(n / tot, 3)}
                        for n, c in cs
                        if n > 0 and n / tot >= cfg["breakdown_min_country_share"]
                    ][: cfg["breakdown_top_countries"]] if tot else []
            breakdown.append(entry)

    order = {"critical": 0, "high": 1, "medium": 2, "unrated": 3}
    findings.sort(key=lambda f: (order[f["priority"]], FINDING_TYPES.index(f["type"]), -f["value"]))
    counts = {k: sum(1 for f in findings if f["priority"] == k) for k in ("critical", "high", "medium", "unrated")}

    return {
        "property": prop, "host": host, "as_of": args.as_of.isoformat(), "mode": mode, "full": full,
        "periods": {k: {"start": v[0].isoformat(), "end": v[1].isoformat()} for k, v in win.items()},
        "win": win,
        "ctr_benchmark": curve_label,
        "page1_target_ctr": round(target_ctr, 4),
        "summary": {
            "source": s_source,
            "clicks": s_cur.clicks, "clicks_prior": s_pri.clicks,
            "impressions": s_cur.impressions, "impressions_prior": s_pri.impressions,
            "position": round(s_cur.position, 2) if s_cur.position else None,
            "position_prior": round(s_pri.position, 2) if s_pri.position else None,
            "low_volume": low_volume, "low_volume_threshold": low_volume_threshold,
        },
        "counts": counts,
        "declining_pages": declining,
        "ctr_opportunities": ctr_ops,
        "rising_queries": rising,
        "cannibalization": cannibal,
        "page1_opportunities": page1,
        "breakdown": breakdown,
        "excluded_as_cannibalized": excluded,
        "excluded_by_query_filter": excluded_by_pattern,
        "findings": findings,
    }


# --------------------------------------------------------------------------- rendering


def render(res: dict, cfg: dict) -> str:
    host = res["host"]
    lim = cfg["max_rows_per_section"]
    win = res["win"]
    L: list[str] = []
    add = L.append
    s = res["summary"]

    add(f"#### Search Console report: {host} — {frange(*win['current'])} vs. {frange(*win['prior'])}")
    add("")
    pos_txt = "n/a"
    if s["position"] is not None and s["position_prior"] is not None:
        d = s["position"] - s["position_prior"]
        direction = "worse" if round(d, 1) > 0 else ("better" if round(d, 1) < 0 else "flat")
        pos_txt = f"{fpos(s['position'])} ({fdelta1(d)}, {direction})"
    if s["low_volume"]:
        tail = "Priorities not assigned (low data volume)."
    else:
        tail = f"{res['counts']['critical']} critical, {res['counts']['high']} high-priority findings."
    add(
        f"**Summary:** {fint(s['clicks'])} clicks ({fpct_change(s['clicks'], s['clicks_prior'])}), "
        f"{fint(s['impressions'])} impressions ({fpct_change(s['impressions'], s['impressions_prior'])}), "
        f"avg position {pos_txt}. {tail}"
    )
    add(
        f"_As of {res['as_of']}; periods end {cfg['data_lag_days']} days before as-of for Search Console data lag. "
        f"Mode: {res['mode']}. Totals: {s['source']}. Expected CTR: {res['ctr_benchmark']}. "
        "Observed = from the export; est. = projection._"
    )
    if s["low_volume"]:
        add("")
        add(
            f"⚠️ **Low data volume:** {fint(s['clicks'])} clicks this period (under "
            f"{fint(s['low_volume_threshold'])}). That is too little data for reliable pattern detection; "
            "treat everything below as anecdotal."
        )

    def more(n: int) -> None:
        if n > lim:
            add(f"_…and {n - lim} more (see --json)._")

    def excluded_note(ftype: str) -> None:
        ex = res["excluded_as_cannibalized"].get(ftype) or []
        if ex:
            names = ", ".join(f"\"{q}\"" for q in ex)
            add(f"_Left out because they are split across URLs (see 🔀 Cannibalization): {md_cell(names)}._")
        n = len(res["excluded_by_query_filter"].get(ftype) or [])
        if n:
            add(f"_{n} {'query' if n == 1 else 'queries'} left out by brand_terms/exclude_query_patterns "
                "(see --json)._")

    def action(ftype: str) -> None:
        excluded_note(ftype)
        add(f"{ARROW} Action: {cfg['section_actions'][ftype]}")

    # declining
    add("")
    add("**\U0001f53b Declining pages** (sorted by click loss)")
    dp = res["declining_pages"]
    if dp is None:
        add("Not evaluated: no page-level data (pass --current-pages/--prior-pages with a UI export).")
    elif not dp:
        add(f"None: no page lost more than {cfg['decline_min_drop_pct']:g}% of clicks with over "
            f"{fint(cfg['decline_min_impressions'])} impressions.")
    else:
        add("| Page | Clicks now | Clicks prior | Change | Top query | Likely cause |")
        add("|---|---|---|---|---|---|")
        for d in dp[:lim]:
            tq = f"\"{d['top_query']}\"" if d["top_query"] else "n/a (no query x page data)"
            add(f"| {md_cell(short_url(d['page'], host))} | {fint(d['clicks_now'])} | {fint(d['clicks_prior'])} | "
                f"{fpct_change(d['clicks_now'], d['clicks_prior'])} | {md_cell(tq)} | {md_cell(d['likely_cause'])} |")
        more(len(dp))
        add("_Likely cause lists what the data shows changed; it is an observation, not a confirmed cause._")
        action("declining_page")

    # ctr
    add("")
    add(f"**\U0001f3af CTR opportunities** (position 1-{cfg['ctr_max_position']:g}, CTR below benchmark)")
    co = res["ctr_opportunities"]
    if not co:
        add("None: every qualifying query is within "
            f"{cfg['ctr_min_gap_pp']:g}pp of its expected CTR.")
        excluded_note("ctr_opportunity")
    else:
        add("| Query | Position | Impressions | CTR | Expected CTR | Gap | Est. click gain |")
        add("|---|---|---|---|---|---|---|")
        for c in co[:lim]:
            add(f"| \"{md_cell(c['query'])}\" | {fpos(c['position'])} | {fint(c['impressions'])} | {fctr(c['ctr'])} | "
                f"{fctr(c['expected_ctr'])} | {fpp(c['gap_pp'] / 100)} | est. +{fint(c['est_click_gain'])} |")
        more(len(co))
        action("ctr_opportunity")

    # rising
    add("")
    add("**\U0001f4c8 Rising queries**")
    rq = res["rising_queries"]
    if not rq:
        add("None: no query gained enough impressions or position to qualify.")
    else:
        add("| Query | Impressions | Position change | Trend |")
        add("|---|---|---|---|")
        for r in rq[:lim]:
            pc = (f"new at {fpos(r['position_now'])}" if r["position_prior"] is None
                  else f"{fpos(r['position_prior'])}{ARROW}{fpos(r['position_now'])}")
            add(f"| \"{md_cell(r['query'])}\" | {fint(r['impressions'])} | {pc} | {r['trend']} |")
        more(len(rq))
        action("rising_query")

    # cannibalization
    add("")
    add("**\U0001f500 Cannibalization**")
    cb = res["cannibalization"]
    if cb is None:
        add("Not evaluated: the Search Console UI export has no query x page data. Use the API export (DATA_CONTRACT.md).")
    elif not cb:
        add(f"None: no query has {cfg['cannibalization_threshold']}+ URLs each holding "
            f"{cfg['cannibalization_min_impression_share'] * 100:g}%+ of its impressions.")
    else:
        add("| Query | URLs competing | Best position | Recommendation |")
        add("|---|---|---|---|")
        for c in cb[:lim]:
            urls = ", ".join(f"{short_url(u['page'], host)} ({u['share'] * 100:.0f}%)" for u in c["urls"])
            best = short_url(c["best_page"], host)
            others = [short_url(u["page"], host) for u in c["urls"] if u["page"] != c["best_page"]]
            rec = f"Consolidate to {best}, redirect or de-optimize {', '.join(others)}"
            add(f"| \"{md_cell(c['query'])}\" | {md_cell(urls)} | {fpos(c['best_position'])} | {md_cell(rec)} |")
        more(len(cb))
        add("_Percentages are each URL's share of the query's impressions; URLs under "
            f"{cfg['cannibalization_min_impression_share'] * 100:g}% are ignored as noise._")
        action("cannibalization")

    # page 1
    add("")
    add(f"**\U0001f3c1 Page-1 opportunities** (positions {cfg['page1_min_position']:g}-{cfg['page1_max_position']:g})")
    p1 = res["page1_opportunities"]
    days = cfg["compare_period"]
    if not p1:
        add("None: no qualifying query sits on the edge of page 1.")
        excluded_note("page1_opportunity")
    else:
        add(f"| Query | Position | Impressions | Est. click gain if pos {cfg['page1_target_position']:g} |")
        add("|---|---|---|---|")
        for p in p1[:lim]:
            add(f"| \"{md_cell(p['query'])}\" | {fpos(p['position'])} | {fint(p['impressions'])} | "
                f"est. +{fint(p['est_click_gain'])} clicks/{days}d |")
        more(len(p1))
        add(f"_Est. gain = impressions x (expected CTR at position {cfg['page1_target_position']:g}, "
            f"{fctr(res['page1_target_ctr'])}, − current CTR), assuming impressions hold._")
        action("page1_opportunity")

    # breakdown
    add("")
    add("**\U0001f4f1 Top queries by device & country** (current period)")
    bd = res["breakdown"]
    if bd is None:
        add("Not evaluated: pass --devices and/or --countries (query x device / query x country exports).")
    else:
        add("| Query | Clicks | Desktop | Mobile | Tablet | Top countries | Note |")
        add("|---|---|---|---|---|---|---|")
        for b in bd:
            cells = []
            for dname in ("desktop", "mobile", "tablet"):
                if b["devices"] is None:
                    cells.append("n/a")
                elif dname in b["devices"]:
                    dv = b["devices"][dname]
                    cells.append(f"{dv['share'] * 100:.0f}% · pos {fpos(dv['position'])}")
                else:
                    cells.append("0%")
            ct = ("n/a" if b["countries"] is None else
                  ", ".join(f"{c['country'].upper()} {c['share'] * 100:.0f}%" for c in b["countries"]) or "none")
            add(f"| \"{md_cell(b['query'])}\" | {fint(b['clicks'])} | {' | '.join(cells)} | {ct} | {b['note']} |")
        add("_Device and country cells are each query's share of clicks._")

    # prioritized actions
    add("")
    add("**✅ Prioritized actions**")
    if res["summary"]["low_volume"]:
        add("Not ranked: data volume is too low for reliable prioritization. Review the tables above manually.")
    else:
        top = [f for f in res["findings"] if f["priority"] in ("critical", "high")]
        if not top:
            add("No critical or high-priority findings this period.")
        cap = cfg["max_priority_actions"]
        for i, f in enumerate(top[:cap], start=1):
            add(f"{i}. **[{f['priority']}]** {f['label']} — {f['action']}")
        if len(top) > cap:
            add(f"_…and {len(top) - cap} more (see --json)._")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- main


def parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' is not a YYYY-MM-DD date")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze Search Console performance exports period over period.")
    ap.add_argument("--current", help="current-period CSV (query,page,... or Queries export)")
    ap.add_argument("--prior", help="prior-period CSV, same shape as --current")
    ap.add_argument("--current-pages", help="reduced mode: current-period Pages export")
    ap.add_argument("--prior-pages", help="reduced mode: prior-period Pages export")
    ap.add_argument("--devices", help="current-period query x device CSV")
    ap.add_argument("--countries", help="current-period query x country CSV")
    ap.add_argument("--totals", help="property totals CSV (period,clicks,impressions,ctr,position)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--ctr-curve", default=str(DEFAULT_CURVE))
    ap.add_argument("--property", default="", help="Search Console property; overrides gsc_property")
    ap.add_argument("--as-of", required=True, type=parse_date, help="reporting date YYYY-MM-DD")
    ap.add_argument("--first-data-date", type=parse_date, default=None,
                    help="earliest date with Search Console data (new properties); with --plan or when the "
                         "prior period is empty, reports the largest compare_period the data supports")
    ap.add_argument("--json", action="store_true", help="emit structured JSON instead of markdown")
    ap.add_argument("--plan", action="store_true",
                    help="print the date windows and Search Analytics API request bodies, then exit")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(Path(args.config))
        prop = (args.property or cfg["gsc_property"]).strip()
        if not prop:
            raise InputError("property: set gsc_property in the config or pass --property")
        args.property = prop
        win = windows(args.as_of, cfg)
        if args.first_data_date is not None and win["prior"][0] < args.first_data_date:
            # The prior window would be partly or wholly empty; comparing it would inflate every change.
            raise InputError(f"first-data-date: {no_prior_data_help(args, cfg, win, partial=True)}")
        if args.plan:
            sys.stdout.write(json.dumps(api_plan(prop, win), indent=2) + "\n")
            return 0
        if not args.current or not args.prior:
            raise InputError("arguments: --current and --prior are required (or use --plan)")
        curve, curve_label = load_curve(cfg, Path(args.ctr_curve))
        res = analyze(args, cfg, curve, curve_label)
    except InputError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if args.json:
        out = {k: v for k, v in res.items() if k not in ("win", "full")}
        sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(render(res, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
