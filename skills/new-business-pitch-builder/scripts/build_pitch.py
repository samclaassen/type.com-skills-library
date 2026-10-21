#!/usr/bin/env python3
"""Turn an audit of a prospect's paid, SEO and funnel surface into a pitch.

Produces a prioritized gap list with sized opportunity, an opportunity summary
by area, and a 90-day engagement outline built from the gaps that actually
scored highest.

Stdlib only. No network access. Deterministic: "today" comes from --as-of.

Usage:
    python3 scripts/build_pitch.py \
        --paid examples/data/paid_snapshot.csv \
        --seo examples/data/seo_pages.csv \
        --funnel examples/data/funnel_steps.csv \
        --benchmarks examples/data/benchmarks.csv \
        --prospect "Tidewater Outfitters" \
        --aov 85 \
        --gross-margin 55 \
        --as-of 2026-09-22
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")
MONTHS = Decimal("12")

PAID_COLUMNS = ["channel", "monthly_spend", "impressions", "clicks", "conversions", "revenue"]
SEO_COLUMNS = ["url", "keyword", "monthly_search_volume", "current_position", "monthly_clicks"]
FUNNEL_COLUMNS = ["step_order", "step_name", "users"]
BENCHMARK_COLUMNS = ["metric", "scope", "value"]

VALID_METRICS = {"paid_conversion_rate", "funnel_step_rate", "organic_ctr"}

# Organic click-through rate by position. Public position-CTR studies cluster
# around these values; override any of them with an `organic_ctr` benchmark row.
DEFAULT_CTR_CURVE = {
    1: Decimal("27.6"),
    2: Decimal("15.8"),
    3: Decimal("11.0"),
    4: Decimal("8.0"),
    5: Decimal("7.2"),
    6: Decimal("5.1"),
    7: Decimal("4.0"),
    8: Decimal("3.2"),
    9: Decimal("2.8"),
    10: Decimal("2.5"),
}
DEEP_POSITION_CTR = Decimal("1.0")

# Confidence haircut and delivery effort per gap type. These are the skill's
# opinion, stated openly in the output so a prospect can argue with them.
GAP_PROFILES = {
    "paid_tracking": {"confidence": Decimal("0.60"), "effort_weeks": 1, "area": "Paid media"},
    "paid_efficiency": {"confidence": Decimal("0.40"), "effort_weeks": 2, "area": "Paid media"},
    "paid_conversion_rate": {"confidence": Decimal("0.50"), "effort_weeks": 4, "area": "Paid media"},
    "funnel_step": {"confidence": Decimal("0.35"), "effort_weeks": 6, "area": "Funnel"},
    "seo_position": {"confidence": Decimal("0.30"), "effort_weeks": 12, "area": "Organic search"},
}

# Gap values come in two units that must never be added together:
# gross profit from extra orders, and paid spend that can be moved or cut.
GROSS_PROFIT = "gross profit"
RECOVERED_SPEND = "recovered spend"
SUMMARY_GROUPS = {
    "funnel_step": ("Funnel", GROSS_PROFIT),
    "paid_conversion_rate": ("Paid media — conversion rate", GROSS_PROFIT),
    "seo_position": ("Organic search", GROSS_PROFIT),
    "paid_efficiency": ("Paid media — spend efficiency and tracking", RECOVERED_SPEND),
    "paid_tracking": ("Paid media — spend efficiency and tracking", RECOVERED_SPEND),
}
# Funnel fixes and paid conversion-rate fixes both lift conversion of the same
# visitors, so only the larger of the two groups counts toward the headline.
OVERLAPPING_GROUPS = ("Funnel", "Paid media — conversion rate")

WORKSTREAMS = {
    "paid_tracking": [
        "Rebuild conversion tracking and verify every channel fires the same purchase event",
        "Reconcile platform-reported conversions against the analytics property and the store",
    ],
    "paid_efficiency": [
        "Reallocate spend from the weakest cost-per-acquisition channels to the strongest",
        "Set channel-level efficiency floors and a weekly reallocation cadence",
    ],
    "paid_conversion_rate": [
        "Restructure campaigns and audiences around the highest-intent segments",
        "Refresh creative and ad copy against the channels furthest from benchmark",
        "Align landing pages with the ad promise for the highest-spend campaigns",
    ],
    "funnel_step": [
        "Instrument the funnel step by step and confirm the drop-off is real, not measurement",
        "Run a prioritized test roadmap against the weakest step",
        "Ship the winning variant and re-baseline the funnel",
    ],
    "seo_position": [
        "Technical and on-page fixes for the highest-volume near-miss pages",
        "Content refresh and internal linking for the target keyword cluster",
        "Digital PR and link acquisition for the money pages",
    ],
}


# --------------------------------------------------------------------------- io


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def read_table(path: str, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        fail(f"{label}: cannot read {path} ({exc.strerror or exc})")
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{label}: {path} is empty (no header row)")
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            fail(f"{label}: {path} is missing required column(s): {', '.join(missing)}")
        rows: list[tuple[dict[str, str], int]] = []
        for line_no, raw in enumerate(reader, start=2):
            rows.append(
                (
                    {
                        (key or "").strip(): (value.strip() if isinstance(value, str) else "")
                        for key, value in raw.items()
                    },
                    line_no,
                )
            )
        if not rows:
            fail(f"{label}: {path} has a header but no data rows")
        return rows


def need_text(row, column, path, line_no) -> str:
    value = row.get(column, "")
    if not value:
        fail(f"{path} line {line_no}: column '{column}' is empty")
    return value


def need_decimal(row, column, path, line_no, allow_zero: bool = True) -> Decimal:
    value = row.get(column, "")
    if value == "":
        fail(f"{path} line {line_no}: column '{column}' is empty, expected a number")
    try:
        number = Decimal(value.replace(",", "").replace("$", "").replace("%", ""))
    except InvalidOperation:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a number")
    if number < ZERO:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a value >= 0")
    if not allow_zero and number == ZERO:
        fail(f"{path} line {line_no}: column '{column}' is 0, expected a value above 0")
    return number


def need_int(row, column, path, line_no, minimum: int = 0) -> int:
    value = need_text(row, column, path, line_no)
    try:
        number = int(value)
    except ValueError:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a whole number")
    if number < minimum:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected {minimum} or more")
    return number


# ---------------------------------------------------------------------- format


def money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"-${abs(whole):,}" if whole < 0 else f"${whole:,}"


def money_cents(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    cents = value.quantize(CENT, rounding=ROUND_HALF_UP)
    return f"-${abs(cents):,.2f}" if cents < 0 else f"${cents:,.2f}"


def percent(value: Decimal | None, places: str = "0.01") -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP)}%"


def count(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    return numerator / denominator


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# --------------------------------------------------------------------- loading


def load_benchmarks(path: str) -> dict[str, dict[str, Decimal]]:
    rows = read_table(path, BENCHMARK_COLUMNS, "benchmarks")
    benchmarks: dict[str, dict[str, Decimal]] = {metric: {} for metric in VALID_METRICS}
    for row, line_no in rows:
        metric = need_text(row, "metric", path, line_no)
        if metric not in VALID_METRICS:
            fail(
                f"{path} line {line_no}: column 'metric' is '{metric}', expected one of "
                f"{', '.join(sorted(VALID_METRICS))}"
            )
        scope = need_text(row, "scope", path, line_no)
        if scope in benchmarks[metric]:
            fail(f"{path} line {line_no}: duplicate benchmark for {metric} / {scope}")
        value = need_decimal(row, "value", path, line_no)
        if metric in {"paid_conversion_rate", "funnel_step_rate", "organic_ctr"} and value > HUNDRED:
            fail(
                f"{path} line {line_no}: column 'value' is '{value}', expected a percentage "
                "between 0 and 100"
            )
        if metric == "organic_ctr":
            try:
                int(scope)
            except ValueError:
                fail(
                    f"{path} line {line_no}: organic_ctr scope is '{scope}', expected a whole "
                    "search position such as 3"
                )
        benchmarks[metric][scope] = value
    return benchmarks


def load_paid(path: str) -> list[dict]:
    rows = read_table(path, PAID_COLUMNS, "paid snapshot")
    channels = []
    seen = set()
    for row, line_no in rows:
        channel = need_text(row, "channel", path, line_no)
        if channel in seen:
            fail(f"{path} line {line_no}: duplicate channel '{channel}'")
        seen.add(channel)
        clicks = need_decimal(row, "clicks", path, line_no)
        impressions = need_decimal(row, "impressions", path, line_no)
        if clicks > impressions:
            fail(
                f"{path} line {line_no}: clicks {clicks} exceed impressions {impressions} for "
                f"channel '{channel}'"
            )
        conversions = need_decimal(row, "conversions", path, line_no)
        if conversions > clicks:
            fail(
                f"{path} line {line_no}: conversions {conversions} exceed clicks {clicks} for "
                f"channel '{channel}'"
            )
        channels.append(
            {
                "channel": channel,
                "spend": need_decimal(row, "monthly_spend", path, line_no),
                "impressions": impressions,
                "clicks": clicks,
                "conversions": conversions,
                "revenue": need_decimal(row, "revenue", path, line_no),
            }
        )
    channels.sort(key=lambda item: item["channel"])
    return channels


def load_seo(path: str) -> list[dict]:
    rows = read_table(path, SEO_COLUMNS, "seo pages")
    pages = []
    for row, line_no in rows:
        position = need_int(row, "current_position", path, line_no, minimum=1)
        pages.append(
            {
                "url": need_text(row, "url", path, line_no),
                "keyword": need_text(row, "keyword", path, line_no),
                "volume": need_decimal(row, "monthly_search_volume", path, line_no),
                "position": position,
                "clicks": need_decimal(row, "monthly_clicks", path, line_no),
                "line_no": line_no,
            }
        )
    pages.sort(key=lambda item: (item["url"], item["keyword"]))
    return pages


def load_funnel(path: str) -> list[dict]:
    rows = read_table(path, FUNNEL_COLUMNS, "funnel steps")
    steps = []
    for row, line_no in rows:
        steps.append(
            {
                "order": need_int(row, "step_order", path, line_no, minimum=1),
                "name": need_text(row, "step_name", path, line_no),
                "users": need_decimal(row, "users", path, line_no),
                "line_no": line_no,
            }
        )
    steps.sort(key=lambda item: item["order"])
    if len(steps) < 2:
        fail(f"funnel steps: {path} needs at least two steps to measure a drop-off")
    for index, step in enumerate(steps, start=1):
        if step["order"] != index:
            fail(
                f"{path} line {step['line_no']}: step_order {step['order']} breaks the sequence; "
                f"expected {index}. Steps must be numbered 1..n with no gaps or duplicates"
            )
    if steps[0]["users"] == ZERO:
        fail(f"{path} line {steps[0]['line_no']}: the first funnel step has 0 users")
    for previous, current in zip(steps, steps[1:]):
        if current["users"] > previous["users"]:
            fail(
                f"{path} line {current['line_no']}: step '{current['name']}' has "
                f"{current['users']} users, more than the preceding step '{previous['name']}' "
                f"({previous['users']}). A funnel cannot widen"
            )
    return steps


# -------------------------------------------------------------------- analysis


def ctr_for_position(position: int, overrides: dict[str, Decimal]) -> Decimal:
    override = overrides.get(str(position))
    if override is not None:
        return override
    return DEFAULT_CTR_CURVE.get(position, DEEP_POSITION_CTR)


def make_gap(gap_type: str, title: str, evidence: list[str], monthly_value: Decimal, scale: Decimal) -> dict:
    profile = GAP_PROFILES[gap_type]
    confidence = profile["confidence"] * scale
    adjusted = monthly_value * confidence
    effort = Decimal(profile["effort_weeks"])
    annual = adjusted * MONTHS
    return {
        "type": gap_type,
        "area": profile["area"],
        "title": title,
        "evidence": evidence,
        "monthly_value": monthly_value,
        "confidence": confidence,
        "adjusted_monthly": adjusted,
        "adjusted_annual": annual,
        "effort_weeks": profile["effort_weeks"],
        "score": annual / effort,
    }


def paid_gaps(channels, benchmarks, value_per_conversion: Decimal, settings) -> list[dict]:
    gaps: list[dict] = []
    measured = [
        channel
        for channel in channels
        if channel["conversions"] > ZERO and channel["spend"] > ZERO
    ]
    best_cpa = None
    best_channel = None
    for channel in measured:
        cpa = divide(channel["spend"], channel["conversions"])
        if cpa is None:
            continue
        if best_cpa is None or cpa < best_cpa:
            best_cpa, best_channel = cpa, channel["channel"]

    for channel in channels:
        name = channel["channel"]
        spend = channel["spend"]
        clicks = channel["clicks"]
        conversions = channel["conversions"]

        if spend >= settings["tracking_floor"] and conversions == ZERO:
            gaps.append(
                make_gap(
                    "paid_tracking",
                    f"{name} reports no conversions on {money(spend)} of monthly spend",
                    [
                        f"{name} spent {money(spend)} last month across {count(clicks)} clicks "
                        "with zero recorded conversions.",
                        "Either the channel genuinely produces nothing, or its conversion "
                        "tracking is broken. Both are worth a month of spend to find out.",
                    ],
                    spend,
                    settings["scale"],
                )
            )
            continue

        # A channel gets at most one gap. A conversion-rate gap and an
        # efficiency gap describe the same fix from two directions, so adding
        # both would bill the prospect twice for one improvement.
        candidates: list[dict] = []

        cvr = divide(conversions, clicks)
        cvr = None if cvr is None else cvr * HUNDRED
        target = benchmarks["paid_conversion_rate"].get(name)
        if cvr is not None and target is not None and cvr < target:
            incremental = clicks * (target - cvr) / HUNDRED
            candidates.append(
                make_gap(
                    "paid_conversion_rate",
                    f"{name} converts at {percent(cvr)} against a {percent(target)} benchmark",
                    [
                        f"{count(clicks)} monthly clicks at {percent(cvr)} produce "
                        f"{count(conversions)} conversions; at {percent(target)} the same traffic "
                        f"produces {count(conversions + incremental)}.",
                        f"That is {count(incremental)} additional conversions per month at "
                        f"{money_cents(value_per_conversion)} of gross profit each.",
                    ],
                    incremental * value_per_conversion,
                    settings["scale"],
                )
            )

        if best_cpa is not None and conversions > ZERO and spend > ZERO:
            cpa = spend / conversions
            savings = spend - conversions * best_cpa
            share = divide(savings, spend)
            share_pct = None if share is None else share * HUNDRED
            if (
                name != best_channel
                and savings > ZERO
                and share_pct is not None
                and share_pct >= settings["reallocation_threshold"]
            ):
                candidates.append(
                    make_gap(
                        "paid_efficiency",
                        f"{name} costs {money_cents(cpa)} per conversion against "
                        f"{money_cents(best_cpa)} on {best_channel}",
                        [
                            f"{name} spends {money(spend)} for {count(conversions)} conversions.",
                            f"At {best_channel}'s {money_cents(best_cpa)} cost per conversion, "
                            f"the same volume costs {money(conversions * best_cpa)} — "
                            f"{money(savings)} per month of spend to redeploy "
                            f"({percent(share_pct, '0.1')} of the channel).",
                        ],
                        savings,
                        settings["scale"],
                    )
                )

        if not candidates:
            continue
        candidates.sort(key=lambda gap: (-gap["adjusted_monthly"], gap["title"]))
        chosen = candidates[0]
        for alternative in candidates[1:]:
            chosen["evidence"].append(
                f"Read another way — {alternative['title']} "
                f"— the same channel is worth {money(alternative['monthly_value'])} a month. "
                "The two framings describe one fix, so only the larger is counted."
            )
        gaps.append(chosen)
    return gaps


def seo_gaps(pages, benchmarks, site_cvr: Decimal, value_per_conversion: Decimal, settings):
    gaps: list[dict] = []
    skipped: list[str] = []
    target = settings["target_position"]
    target_ctr = ctr_for_position(target, benchmarks["organic_ctr"])
    for page in pages:
        if page["position"] <= target:
            continue
        if page["position"] > settings["max_position"]:
            skipped.append(
                f"'{page['keyword']}' ({page['url']}) sits at position {page['position']}, beyond "
                f"the position {settings['max_position']} cutoff for a credible 90-day estimate."
            )
            continue
        potential_clicks = page["volume"] * target_ctr / HUNDRED
        incremental = potential_clicks - page["clicks"]
        if incremental <= ZERO:
            continue
        conversions = incremental * site_cvr / HUNDRED
        gaps.append(
            make_gap(
                "seo_position",
                f"'{page['keyword']}' ranks {page['position']} with "
                f"{count(page['volume'])} monthly searches",
                [
                    f"{page['url']} earns {count(page['clicks'])} monthly clicks at position "
                    f"{page['position']}.",
                    f"At position {target} ({percent(target_ctr, '0.1')} click-through) the same "
                    f"keyword earns about {count(potential_clicks)} clicks — "
                    f"{count(incremental)} more per month.",
                    f"At the site's {percent(site_cvr)} conversion rate that is "
                    f"{count(conversions)} additional orders per month.",
                ],
                conversions * value_per_conversion,
                settings["scale"],
            )
        )
    return gaps, skipped


def funnel_gaps(steps, benchmarks, value_per_conversion: Decimal, settings):
    gaps: list[dict] = []
    rates = []
    for previous, current in zip(steps, steps[1:]):
        rate = divide(current["users"], previous["users"])
        rates.append(ZERO if rate is None else rate * HUNDRED)

    for index in range(len(rates)):
        step = steps[index + 1]
        previous = steps[index]
        actual = rates[index]
        target = benchmarks["funnel_step_rate"].get(step["name"])
        if target is None or actual >= target:
            continue
        recovered_users = previous["users"] * (target - actual) / HUNDRED
        downstream = Decimal("1")
        for later in rates[index + 1 :]:
            downstream *= later / HUNDRED
        conversions = recovered_users * downstream
        if conversions <= ZERO:
            continue
        gaps.append(
            make_gap(
                "funnel_step",
                f"{previous['name']} to {step['name']} converts at {percent(actual)} "
                f"against a {percent(target)} benchmark",
                [
                    f"{count(previous['users'])} users reach {previous['name']} and "
                    f"{count(step['users'])} continue to {step['name']} ({percent(actual)}).",
                    f"At {percent(target)} that step passes {count(recovered_users)} more users "
                    "per month.",
                    (
                        f"Carried through the current downstream rates "
                        f"({percent(downstream * HUNDRED)}), that is {count(conversions)} "
                        "additional orders per month."
                    )
                    if downstream < Decimal("1")
                    else f"That is {count(conversions)} additional orders per month.",
                ],
                conversions * value_per_conversion,
                settings["scale"],
            )
        )
    return gaps, rates


def build_plan(gaps: list[dict]) -> list[dict]:
    """Three 30-day phases, populated from the highest-scoring gaps."""
    phases = [
        {
            "title": "Days 1-30 — stop the leaks",
            "focus": "Fixes that need no new budget and pay back inside the engagement.",
            "items": [],
        },
        {
            "title": "Days 31-60 — convert the traffic already arriving",
            "focus": "Conversion work against the weakest measured step.",
            "items": [],
        },
        {
            "title": "Days 61-90 — compound the gains",
            "focus": "Slower-burn work started now so it lands in months 4 to 6.",
            "items": [],
        },
    ]
    seen: set[str] = set()
    for gap in gaps:
        if gap["type"] in seen:
            continue
        seen.add(gap["type"])
        weeks = gap["effort_weeks"]
        index = 0 if weeks <= 4 else (1 if weeks <= 8 else 2)
        for workstream in WORKSTREAMS[gap["type"]]:
            phases[index]["items"].append(
                {
                    "workstream": workstream,
                    "driver": gap["title"],
                    "area": gap["area"],
                }
            )
        if index == 2:
            phases[0]["items"].append(
                {
                    "workstream": (
                        f"Scope and baseline the {gap['area'].lower()} work so month 3 delivery "
                        "starts from evidence"
                    ),
                    "driver": gap["title"],
                    "area": gap["area"],
                }
            )
    return phases


# ---------------------------------------------------------------------- report


def render(ctx: dict) -> str:
    out: list[str] = []
    add = out.append

    add(f"# New business pitch — {ctx['prospect']}")
    add("")
    add(
        f"Prepared {ctx['as_of']} | Average order value {money_cents(ctx['aov'])} | "
        f"Gross margin {percent(ctx['gross_margin'], '0.1')} | Gross profit per order "
        f"{money_cents(ctx['value_per_conversion'])}"
    )
    add("")

    add("## What we found")
    add("")
    for line in ctx["summary"]:
        add(f"- {line}")
    add("")

    add("## Prioritized gaps")
    add("")
    add("| # | Area | Gap | Monthly (raw) | Monthly (adj.) | Annual (adj.) | Conf. | Effort |")
    add("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for index, gap in enumerate(ctx["gaps"], start=1):
        add(
            f"| {index} | {gap['area']} | {gap['title']} | {money(gap['monthly_value'])} | "
            f"{money(gap['adjusted_monthly'])} | {money(gap['adjusted_annual'])} | "
            f"{percent(gap['confidence'] * HUNDRED, '0.1')} | {gap['effort_weeks']}w |"
        )
    add("")
    add(
        "Ranked by adjusted annual value per week of delivery effort, so a small fix that lands "
        "in week one outranks a larger one that takes a quarter."
    )
    add("")

    for index, gap in enumerate(ctx["gaps"], start=1):
        add(f"### {index}. {gap['title']}")
        add("")
        for line in gap["evidence"]:
            add(f"- {line}")
        add(
            f"- Sized at {money(gap['monthly_value'])} per month before a "
            f"{percent(gap['confidence'] * HUNDRED, '0.1')} confidence haircut, "
            f"{money(gap['adjusted_monthly'])} after — {money(gap['adjusted_annual'])} annualized."
        )
        add("")

    add("## Opportunity summary")
    add("")
    add("| Area | Gaps | Valued as | Monthly (adjusted) | Annual (adjusted) |")
    add("| --- | ---: | --- | ---: | ---: |")
    for group in ctx["group_order"]:
        totals = ctx["group_totals"][group]
        add(
            f"| {group} | {totals['count']} | {totals['unit']} | "
            f"{money(totals['adjusted'])} | {money(totals['annual'])} |"
        )
    add("")
    headline = ctx["headline"]
    reasons = []
    if len({totals["unit"] for totals in ctx["group_totals"].values()}) > 1:
        reasons.append("the rows are in two different units")
    if headline["gp_excluded"]:
        reasons.append("the funnel and paid conversion-rate rows work on the same visitors")
    if reasons:
        add("There is no total row: " + ", and ".join(reasons) + ".")
        add("")
    if headline["gp_groups"]:
        line = (
            f"- **Gross profit, non-overlapping:** {money(headline['gp_monthly'])} per month "
            f"({money(headline['gp_annual'])} annualized) after haircuts, from "
            + " + ".join(headline["gp_groups"])
            + "."
        )
        if headline["gp_excluded"]:
            excluded = headline["gp_excluded"]
            line += (
                f" {excluded} ({money(ctx['group_totals'][excluded]['adjusted'])} per month) is "
                "left out because it lifts conversion of the same visitors; count one or the "
                "other, not both."
            )
        add(line)
    if headline["spend_count"]:
        add(
            f"- **Paid spend to redeploy or verify:** {money(headline['spend_monthly'])} per "
            f"month after haircuts. This is budget that can be moved or cut, not profit, and "
            "is not added to the gross-profit figure."
        )
    add("")
    if ctx["retainer"] is not None:
        if headline["gp_monthly"] > ZERO:
            ratio = headline["gp_monthly"] / ctx["retainer"]
            share = ctx["retainer"] / headline["gp_monthly"] * HUNDRED
            if ratio >= Decimal("1"):
                comparison = (
                    f"the non-overlapping gross-profit estimate is {ratio.quantize(CENT)}x the "
                    f"fee, so the fee is matched if roughly {percent(share, '0.1')} of that "
                    "estimate is realized"
                )
            else:
                comparison = (
                    f"the non-overlapping gross-profit estimate is {ratio.quantize(CENT)}x the "
                    "fee and does not cover it on its own"
                )
        else:
            comparison = "this analysis found no gross-profit opportunity to set against the fee"
        add(
            f"Against a proposed {money(ctx['retainer'])} monthly retainer, {comparison}. These "
            "are benchmark-based estimates, not a forecast, and recovered spend is not counted "
            "toward the fee."
        )
        add("")

    add("## Proposed 90-day engagement")
    add("")
    for phase in ctx["plan"]:
        add(f"### {phase['title']}")
        add("")
        add(f"{phase['focus']}")
        add("")
        if not phase["items"]:
            add("- No work scheduled in this phase from the gaps we found.")
        for item in phase["items"]:
            add(f"- {item['workstream']} — *{item['driver']}*")
        add("")

    add("## How these numbers were built")
    add("")
    for line in ctx["method"]:
        add(f"- {line}")
    add("")

    if ctx["caveats"]:
        add("## What this analysis cannot see")
        add("")
        for line in ctx["caveats"]:
            add(f"- {line}")
        add("")

    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------------ main


def parse_args(argv):
    parser = argparse.ArgumentParser(description="New business pitch builder.")
    parser.add_argument("--paid", required=True)
    parser.add_argument("--seo", required=True)
    parser.add_argument("--funnel", required=True)
    parser.add_argument("--benchmarks", required=True)
    parser.add_argument("--prospect", required=True)
    parser.add_argument("--aov", required=True, help="average order value")
    parser.add_argument("--gross-margin", dest="gross_margin", required=True, help="percent")
    parser.add_argument("--as-of", dest="as_of", required=True, help="YYYY-MM-DD")
    parser.add_argument("--target-position", dest="target_position", type=int, default=3)
    parser.add_argument("--max-position", dest="max_position", type=int, default=20)
    parser.add_argument("--tracking-floor", dest="tracking_floor", default="500")
    parser.add_argument(
        "--reallocation-threshold",
        dest="reallocation_threshold",
        default="25",
        help="percent of a channel's spend that must be recoverable before it is a gap",
    )
    parser.add_argument(
        "--confidence-scale",
        dest="confidence_scale",
        default="1.0",
        help="multiplier on every confidence haircut, e.g. 0.8 for a more conservative pitch",
    )
    parser.add_argument("--proposed-retainer", dest="proposed_retainer")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def positive_decimal(raw: str, flag: str, allow_zero: bool = True) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        fail(f"{flag} is '{raw}', expected a number")
    if value < ZERO:
        fail(f"{flag} is '{raw}', expected a value >= 0")
    if not allow_zero and value == ZERO:
        fail(f"{flag} is '{raw}', expected a value above 0")
    return value


def main(argv=None) -> int:
    args = parse_args(argv)

    if not DATE_RE.match(args.as_of):
        fail(f"--as-of is '{args.as_of}', expected YYYY-MM-DD")
    try:
        date.fromisoformat(args.as_of)
    except ValueError:
        fail(f"--as-of is '{args.as_of}', which is not a real calendar date")

    aov = positive_decimal(args.aov, "--aov", allow_zero=False)
    gross_margin = positive_decimal(args.gross_margin, "--gross-margin", allow_zero=False)
    if gross_margin > HUNDRED:
        fail(f"--gross-margin is '{args.gross_margin}', expected a percent between 0 and 100")
    if args.target_position < 1:
        fail(f"--target-position is {args.target_position}, expected 1 or more")
    if args.max_position < args.target_position:
        fail(
            f"--max-position {args.max_position} is below --target-position "
            f"{args.target_position}"
        )
    confidence_scale = positive_decimal(args.confidence_scale, "--confidence-scale", allow_zero=False)
    if confidence_scale > Decimal("1"):
        fail(
            f"--confidence-scale is '{args.confidence_scale}'; values above 1 would inflate the "
            "estimate beyond the raw arithmetic"
        )
    retainer = (
        None
        if args.proposed_retainer is None
        else positive_decimal(args.proposed_retainer, "--proposed-retainer", allow_zero=False)
    )

    settings = {
        "target_position": args.target_position,
        "max_position": args.max_position,
        "tracking_floor": positive_decimal(args.tracking_floor, "--tracking-floor"),
        "reallocation_threshold": positive_decimal(
            args.reallocation_threshold, "--reallocation-threshold"
        ),
        "scale": confidence_scale,
    }

    benchmarks = load_benchmarks(args.benchmarks)
    channels = load_paid(args.paid)
    pages = load_seo(args.seo)
    steps = load_funnel(args.funnel)

    value_per_conversion = aov * gross_margin / HUNDRED
    site_cvr = steps[-1]["users"] / steps[0]["users"] * HUNDRED

    gaps = paid_gaps(channels, benchmarks, value_per_conversion, settings)
    seo_found, seo_skipped = seo_gaps(pages, benchmarks, site_cvr, value_per_conversion, settings)
    gaps.extend(seo_found)
    funnel_found, step_rates = funnel_gaps(steps, benchmarks, value_per_conversion, settings)
    gaps.extend(funnel_found)

    gaps.sort(key=lambda gap: (-gap["score"], gap["area"], gap["title"]))

    group_totals: dict[str, dict] = {}
    for gap in gaps:
        group, unit = SUMMARY_GROUPS[gap["type"]]
        bucket = group_totals.setdefault(
            group, {"count": 0, "unit": unit, "adjusted": ZERO, "annual": ZERO}
        )
        bucket["count"] += 1
        bucket["adjusted"] += gap["adjusted_monthly"]
        bucket["annual"] += gap["adjusted_annual"]
    group_order = sorted(
        group_totals,
        key=lambda group: (group_totals[group]["unit"] != GROSS_PROFIT, -group_totals[group]["annual"], group),
    )

    # Headline gross profit: every gross-profit group except the smaller of the
    # two overlapping ones. Recovered spend is reported separately, never added.
    present_overlap = [group for group in OVERLAPPING_GROUPS if group in group_totals]
    gp_excluded = None
    if len(present_overlap) == 2:
        keep = max(present_overlap, key=lambda group: group_totals[group]["adjusted"])
        gp_excluded = next(group for group in present_overlap if group != keep)
    gp_groups = [
        group
        for group in group_order
        if group_totals[group]["unit"] == GROSS_PROFIT and group != gp_excluded
    ]
    spend_groups = [group for group in group_order if group_totals[group]["unit"] == RECOVERED_SPEND]
    headline = {
        "gp_groups": gp_groups,
        "gp_excluded": gp_excluded,
        "gp_monthly": sum((group_totals[group]["adjusted"] for group in gp_groups), ZERO),
        "gp_annual": sum((group_totals[group]["annual"] for group in gp_groups), ZERO),
        "spend_count": sum(group_totals[group]["count"] for group in spend_groups),
        "spend_monthly": sum((group_totals[group]["adjusted"] for group in spend_groups), ZERO),
        "spend_annual": sum((group_totals[group]["annual"] for group in spend_groups), ZERO),
    }

    total_spend = sum((channel["spend"] for channel in channels), ZERO)
    total_conversions = sum((channel["conversions"] for channel in channels), ZERO)
    total_revenue = sum((channel["revenue"] for channel in channels), ZERO)
    paid_roas = divide(total_revenue, total_spend)

    summary = [
        f"{money(total_spend)} of monthly paid media across {len(channels)} channels returns "
        f"{count(total_conversions)} conversions and {money(total_revenue)} of "
        f"platform-reported revenue ({'n/a' if paid_roas is None else f'{paid_roas.quantize(CENT)}x'}).",
        f"The site converts {percent(site_cvr)} of traffic end to end: "
        f"{count(steps[0]['users'])} at '{steps[0]['name']}' become "
        f"{count(steps[-1]['users'])} at '{steps[-1]['name']}'.",
    ]
    worth: list[str] = []
    if headline["gp_groups"]:
        worth.append(
            f"{money(headline['gp_monthly'])} a month in gross profit "
            f"({money(headline['gp_annual'])} annualized), counting only gaps that do not overlap"
        )
    if headline["spend_count"]:
        worth.append(
            f"{money(headline['spend_monthly'])} a month of paid spend that could be redeployed "
            "or verified (budget, not profit, so not added in)"
        )
    if worth:
        summary.append(
            f"We found {len(gaps)} gaps. After confidence haircuts they are worth "
            + ", plus ".join(worth)
            + "."
        )
    else:
        summary.append(f"We found {len(gaps)} gaps.")
    if gaps:
        top = gaps[0]
        summary.append(
            f"The highest-return first move is \"{top['title']}\" — "
            f"{money(top['adjusted_annual'])} a year for about {top['effort_weeks']} weeks of "
            "work."
        )

    method = [
        f"Every gap is valued in gross profit: {money_cents(aov)} average order value x "
        f"{percent(gross_margin, '0.1')} gross margin = {money_cents(value_per_conversion)} per "
        "additional order. Paid-efficiency gaps are valued as recovered spend, not as profit.",
        "Each gap carries a confidence haircut reflecting how reliably that class of work lands: "
        + ", ".join(
            f"{gap_type.replace('_', ' ')} {percent(profile['confidence'] * HUNDRED * confidence_scale, '0.1')}"
            for gap_type, profile in sorted(GAP_PROFILES.items())
        )
        + ".",
        f"Organic estimates assume a move to position {args.target_position} "
        f"({percent(ctr_for_position(args.target_position, benchmarks['organic_ctr']), '0.1')} "
        f"click-through) and the site's current {percent(site_cvr)} conversion rate. Keywords "
        f"beyond position {args.max_position} are excluded.",
        "Funnel gaps are each computed with the other steps held at today's rates. Fixing more "
        "than one compounds, so the combined effect is larger than the sum shown here — we size "
        "them separately to keep each claim independently checkable.",
        "Each paid channel contributes at most one gap. Where a conversion-rate gap and an "
        "efficiency gap describe the same channel, only the larger is counted and the other is "
        "shown as an alternative framing.",
        "Benchmarks come from the benchmark file supplied with this analysis, not from the "
        "prospect's own history.",
    ]

    caveats = [
        "Platform-reported conversions and revenue are used as given. Attribution overlap between "
        "channels is not removed, so channel-level revenue double counts to an unknown degree.",
        "No incrementality testing: none of these figures prove the paid channels caused the "
        "conversions they claim.",
        "Seasonality, inventory, pricing, competitive response and brand demand are all outside "
        "the data provided.",
    ]
    caveats.extend(seo_skipped)

    plan = build_plan(gaps)

    ctx = {
        "prospect": args.prospect,
        "as_of": args.as_of,
        "aov": aov,
        "gross_margin": gross_margin,
        "value_per_conversion": value_per_conversion,
        "summary": summary,
        "gaps": gaps,
        "group_totals": group_totals,
        "group_order": group_order,
        "headline": headline,
        "plan": plan,
        "method": method,
        "caveats": caveats,
        "retainer": retainer,
    }

    if args.json:
        payload = {
            "prospect": args.prospect,
            "as_of": args.as_of,
            "aov": as_float(aov),
            "gross_margin_pct": as_float(gross_margin),
            "gross_profit_per_order": as_float(value_per_conversion),
            "site_conversion_rate_pct": as_float(site_cvr),
            "funnel_step_rates_pct": [as_float(rate) for rate in step_rates],
            "headline": {
                "gross_profit_monthly_adjusted": as_float(headline["gp_monthly"]),
                "gross_profit_annual_adjusted": as_float(headline["gp_annual"]),
                "gross_profit_groups": headline["gp_groups"],
                "gross_profit_excluded_overlap": headline["gp_excluded"],
                "recovered_spend_monthly_adjusted": as_float(headline["spend_monthly"]),
                "recovered_spend_annual_adjusted": as_float(headline["spend_annual"]),
            },
            "groups": {
                group: {
                    "count": totals["count"],
                    "unit": totals["unit"],
                    "monthly_adjusted": as_float(totals["adjusted"]),
                    "annual_adjusted": as_float(totals["annual"]),
                }
                for group, totals in group_totals.items()
            },
            "gaps": [
                {
                    "type": gap["type"],
                    "area": gap["area"],
                    "title": gap["title"],
                    "evidence": gap["evidence"],
                    "monthly_value": as_float(gap["monthly_value"]),
                    "confidence": as_float(gap["confidence"]),
                    "adjusted_monthly": as_float(gap["adjusted_monthly"]),
                    "adjusted_annual": as_float(gap["adjusted_annual"]),
                    "effort_weeks": gap["effort_weeks"],
                }
                for gap in gaps
            ],
            "plan": [
                {
                    "title": phase["title"],
                    "focus": phase["focus"],
                    "items": phase["items"],
                }
                for phase in plan
            ],
            "excluded": seo_skipped,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
