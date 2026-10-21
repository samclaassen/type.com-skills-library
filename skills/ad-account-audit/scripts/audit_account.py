#!/usr/bin/env python3
"""Hygiene audit of a paid search / paid social account export.

Six checks run against four local CSV exports: conversion-tracking coverage,
budget pacing outliers, ad approval status, naming-convention drift, negative
keyword coverage, and creative staleness. Findings are ranked by severity and
by the dollars each one puts at issue.

Stdlib only. No network access. Deterministic: "today" comes from --as-of.

Usage:
    python3 scripts/audit_account.py \
        --campaigns examples/data/campaigns.csv \
        --ads examples/data/ads.csv \
        --keywords examples/data/keywords.csv \
        --search-terms examples/data/search_terms.csv \
        --account "Aurora Bikeworks / Google Ads" \
        --period-start 2026-09-01 \
        --as-of 2026-09-21
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
WORD_RE = re.compile(r"[a-z0-9']+")

ZERO = Decimal("0")
CENT = Decimal("0.01")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

CAMPAIGN_COLUMNS = [
    "campaign_name",
    "status",
    "daily_budget",
    "spend",
    "impressions",
    "clicks",
    "conversions",
    "conversion_value",
]
AD_COLUMNS = ["campaign_name", "ad_group", "ad_id", "ad_status", "approval_status", "last_modified"]
KEYWORD_COLUMNS = ["campaign_name", "list_type", "keyword", "match_type"]
SEARCH_TERM_COLUMNS = ["campaign_name", "search_term", "spend", "clicks", "conversions"]

VALID_LIST_TYPES = {"targeting", "negative"}
VALID_MATCH_TYPES = {"exact", "phrase", "broad"}
ACCOUNT_SCOPE = "ACCOUNT"

IMPACT_KINDS = {
    "wasted": "Wasted spend",
    "unmeasured": "Unmeasured spend",
    "unspent": "Unspent capacity",
    "at_risk": "Spend at risk",
    "governance": "Unattributable by naming",
}

IMPACT_NOTES = {
    "wasted": "Spend on traffic that converted zero times and can be blocked outright.",
    "unmeasured": "Spend in campaigns with no recorded conversion. Not proven waste - it is unproven either way until tracking is fixed.",
    "unspent": "Budgeted capacity the account did not deliver against in the period.",
    "at_risk": "Spend exposed to a disapproval, an uncapped budget, or creative long past its refresh date.",
    "governance": "Spend in campaigns that break the naming convention, so it cannot be rolled up automatically.",
}


# --------------------------------------------------------------------------- io


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def read_table(
    path: str, required: list[str], label: str, allow_empty: bool = False
) -> list[tuple[dict[str, str], int]]:
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
        if not rows and not allow_empty:
            fail(f"{label}: {path} has a header but no data rows")
        return rows


def need_text(row: dict[str, str], column: str, path: str, line_no: int) -> str:
    value = row.get(column, "")
    if not value:
        fail(f"{path} line {line_no}: column '{column}' is empty")
    return value


def need_decimal(row: dict[str, str], column: str, path: str, line_no: int) -> Decimal:
    value = row.get(column, "")
    if value == "":
        fail(f"{path} line {line_no}: column '{column}' is empty, expected a number")
    try:
        number = Decimal(value.replace(",", "").replace("$", ""))
    except InvalidOperation:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a number")
    if number < ZERO:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected a value >= 0")
    return number


def need_date(row: dict[str, str], column: str, path: str, line_no: int) -> date:
    value = need_text(row, column, path, line_no)
    if not DATE_RE.match(value):
        fail(f"{path} line {line_no}: column '{column}' is '{value}', expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail(f"{path} line {line_no}: column '{column}' is '{value}', not a real calendar date")


# ---------------------------------------------------------------------- format


def money(value: Decimal) -> str:
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"-${abs(whole):,}" if whole < 0 else f"${whole:,}"


def money_cents(value: Decimal) -> str:
    cents = value.quantize(CENT, rounding=ROUND_HALF_UP)
    return f"-${abs(cents):,.2f}" if cents < 0 else f"${cents:,.2f}"


def percent(value: Decimal | None, places: str = "0.1") -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP)}%"


def divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    return numerator / denominator


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def plural(count: int, word: str, suffix: str = "s") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}{suffix}"


def tokens(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return True
    return False


# --------------------------------------------------------------------- loading


class Campaign:
    __slots__ = (
        "name",
        "status",
        "daily_budget",
        "spend",
        "impressions",
        "clicks",
        "conversions",
        "conversion_value",
        "line_no",
    )

    def __init__(self, name, status, daily_budget, spend, impressions, clicks, conversions, value, line_no):
        self.name = name
        self.status = status
        self.daily_budget = daily_budget
        self.spend = spend
        self.impressions = impressions
        self.clicks = clicks
        self.conversions = conversions
        self.conversion_value = value
        self.line_no = line_no

    @property
    def enabled(self) -> bool:
        return self.status == "ENABLED"


def load_campaigns(path: str) -> dict[str, Campaign]:
    rows = read_table(path, CAMPAIGN_COLUMNS, "campaigns")
    campaigns: dict[str, Campaign] = {}
    for row, line_no in rows:
        name = need_text(row, "campaign_name", path, line_no)
        if name in campaigns:
            fail(f"{path} line {line_no}: duplicate campaign_name '{name}'")
        status = need_text(row, "status", path, line_no).upper()
        if status not in {"ENABLED", "PAUSED", "REMOVED"}:
            fail(
                f"{path} line {line_no}: column 'status' is '{status}', "
                "expected ENABLED, PAUSED or REMOVED"
            )
        campaigns[name] = Campaign(
            name,
            status,
            need_decimal(row, "daily_budget", path, line_no),
            need_decimal(row, "spend", path, line_no),
            need_decimal(row, "impressions", path, line_no),
            need_decimal(row, "clicks", path, line_no),
            need_decimal(row, "conversions", path, line_no),
            need_decimal(row, "conversion_value", path, line_no),
            line_no,
        )
    return campaigns


def load_ads(path: str, campaigns: dict[str, Campaign]) -> list[dict]:
    rows = read_table(path, AD_COLUMNS, "ads")
    ads = []
    for row, line_no in rows:
        campaign = need_text(row, "campaign_name", path, line_no)
        if campaign not in campaigns:
            fail(
                f"{path} line {line_no}: campaign_name '{campaign}' does not appear in the "
                "campaigns export"
            )
        status = need_text(row, "ad_status", path, line_no).upper()
        if status not in {"ENABLED", "PAUSED", "REMOVED"}:
            fail(
                f"{path} line {line_no}: column 'ad_status' is '{status}', "
                "expected ENABLED, PAUSED or REMOVED"
            )
        approval = need_text(row, "approval_status", path, line_no).upper()
        if approval not in {"APPROVED", "LIMITED", "DISAPPROVED", "UNDER_REVIEW"}:
            fail(
                f"{path} line {line_no}: column 'approval_status' is '{approval}', expected "
                "APPROVED, LIMITED, DISAPPROVED or UNDER_REVIEW"
            )
        ads.append(
            {
                "campaign": campaign,
                "ad_group": need_text(row, "ad_group", path, line_no),
                "ad_id": need_text(row, "ad_id", path, line_no),
                "status": status,
                "approval": approval,
                "last_modified": need_date(row, "last_modified", path, line_no),
            }
        )
    return ads


def load_keywords(path: str, campaigns: dict[str, Campaign]) -> list[dict]:
    # Header-only is valid: paid-social accounts have no keywords.
    rows = read_table(path, KEYWORD_COLUMNS, "keywords", allow_empty=True)
    keywords = []
    for row, line_no in rows:
        campaign = need_text(row, "campaign_name", path, line_no)
        if campaign != ACCOUNT_SCOPE and campaign not in campaigns:
            fail(
                f"{path} line {line_no}: campaign_name '{campaign}' does not appear in the "
                f"campaigns export (use '{ACCOUNT_SCOPE}' for account-level negative lists)"
            )
        list_type = need_text(row, "list_type", path, line_no).lower()
        if list_type not in VALID_LIST_TYPES:
            fail(
                f"{path} line {line_no}: column 'list_type' is '{list_type}', expected "
                "targeting or negative"
            )
        match_type = need_text(row, "match_type", path, line_no).lower()
        if match_type not in VALID_MATCH_TYPES:
            fail(
                f"{path} line {line_no}: column 'match_type' is '{match_type}', expected "
                "exact, phrase or broad"
            )
        keyword = need_text(row, "keyword", path, line_no)
        keywords.append(
            {
                "campaign": campaign,
                "list_type": list_type,
                "keyword": keyword,
                "tokens": tokens(keyword),
                "match_type": match_type,
            }
        )
    return keywords


def load_search_terms(path: str, campaigns: dict[str, Campaign]) -> list[dict]:
    # Header-only is valid: paid-social accounts have no search terms report.
    rows = read_table(path, SEARCH_TERM_COLUMNS, "search terms", allow_empty=True)
    terms = []
    for row, line_no in rows:
        campaign = need_text(row, "campaign_name", path, line_no)
        if campaign not in campaigns:
            fail(
                f"{path} line {line_no}: campaign_name '{campaign}' does not appear in the "
                "campaigns export"
            )
        term = need_text(row, "search_term", path, line_no)
        terms.append(
            {
                "campaign": campaign,
                "term": term,
                "tokens": tokens(term),
                "spend": need_decimal(row, "spend", path, line_no),
                "clicks": need_decimal(row, "clicks", path, line_no),
                "conversions": need_decimal(row, "conversions", path, line_no),
            }
        )
    return terms


# ---------------------------------------------------------------------- checks


def negative_blocks(term_tokens: list[str], negative: dict) -> bool:
    """Would this negative keyword have blocked this search term?

    Mirrors how search engines apply negatives: exact blocks the identical
    query, phrase blocks any query containing the words in order, broad blocks
    any query containing all of the words in any order.
    """
    needle = negative["tokens"]
    if not needle:
        return False
    if negative["match_type"] == "exact":
        return term_tokens == needle
    if negative["match_type"] == "phrase":
        return contains_sequence(term_tokens, needle)
    return all(word in term_tokens for word in needle)


def check_tracking(campaigns, floor: Decimal) -> list[dict]:
    findings = []
    for campaign in campaigns:
        if not campaign.enabled or campaign.spend < floor or campaign.conversions > ZERO:
            continue
        findings.append(
            {
                "severity": "high",
                "category": "Conversion tracking",
                "campaign": campaign.name,
                "title": f"No conversions recorded on {money(campaign.spend)} of spend",
                "impact_kind": "unmeasured",
                "impact": campaign.spend,
                "evidence": [
                    f"{campaign.name} spent {money(campaign.spend)} across "
                    f"{campaign.clicks.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,} clicks "
                    "and reported zero conversions.",
                    "Every efficiency metric for this campaign is undefined until a conversion "
                    "action fires.",
                ],
                "fix": (
                    "Confirm the conversion action is enabled, set as a primary goal, and "
                    "included in this campaign's bidding. Re-audit after one full conversion "
                    "window before judging performance."
                ),
            }
        )
    return findings


def check_pacing(campaigns, days: int, tolerance: Decimal) -> tuple[list[dict], list[str]]:
    findings: list[dict] = []
    skipped: list[str] = []
    upper = Decimal("1") + tolerance
    lower = Decimal("1") - tolerance
    for campaign in campaigns:
        if not campaign.enabled:
            continue
        if campaign.daily_budget == ZERO:
            skipped.append(campaign.name)
            continue
        capacity = campaign.daily_budget * Decimal(days)
        pace = divide(campaign.spend, capacity)
        if pace is None:
            continue
        daily_rate = campaign.spend / Decimal(days)
        if pace > upper:
            findings.append(
                {
                    "severity": "medium",
                    "category": "Budget pacing",
                    "campaign": campaign.name,
                    "title": f"Delivering {percent(pace * Decimal('100'))} of its daily cap",
                    "impact_kind": "at_risk",
                    "impact": campaign.spend - capacity,
                    "evidence": [
                        f"Daily cap {money_cents(campaign.daily_budget)} x {days} days = "
                        f"{money(capacity)} of capacity; actual spend was {money(campaign.spend)} "
                        f"({money_cents(daily_rate)}/day).",
                        "Sustained over-delivery means the campaign draws from a shared budget, "
                        "the cap changed mid-period, or the export covers a different window.",
                    ],
                    "fix": (
                        "Reconcile the campaign against its shared budget or recent cap changes, "
                        "then reset the cap so the monthly commitment is enforceable."
                    ),
                }
            )
        elif pace < lower:
            findings.append(
                {
                    "severity": "medium" if campaign.conversions > ZERO else "high",
                    "category": "Budget pacing",
                    "campaign": campaign.name,
                    "title": f"Delivering only {percent(pace * Decimal('100'))} of its daily cap",
                    "impact_kind": "unspent",
                    "impact": capacity - campaign.spend,
                    "evidence": [
                        f"Daily cap {money_cents(campaign.daily_budget)} x {days} days = "
                        f"{money(capacity)} of capacity; actual spend was {money(campaign.spend)} "
                        f"({money_cents(daily_rate)}/day).",
                        f"{money(capacity - campaign.spend)} of committed capacity went "
                        "undelivered in the period.",
                    ],
                    "fix": (
                        "Check bid caps, auction eligibility and audience size. If the campaign "
                        "genuinely cannot absorb the budget, move the difference to a channel "
                        "that can rather than reporting it as delivered."
                    ),
                }
            )
    return findings, skipped


def check_approvals(campaigns_by_name, ads, days: int) -> list[dict]:
    findings = []
    by_campaign: dict[str, list[dict]] = {}
    for ad in ads:
        by_campaign.setdefault(ad["campaign"], []).append(ad)

    for name in sorted(by_campaign):
        campaign = campaigns_by_name[name]
        if not campaign.enabled:
            continue
        live = [ad for ad in by_campaign[name] if ad["status"] == "ENABLED"]
        if not live:
            findings.append(
                {
                    "severity": "high",
                    "category": "Ad approvals",
                    "campaign": name,
                    "title": "No enabled ads in an enabled campaign",
                    "impact_kind": "at_risk",
                    "impact": campaign.daily_budget * Decimal(days),
                    "evidence": [
                        f"{name} is ENABLED but every ad in the export is paused or removed."
                    ],
                    "fix": "Enable at least one reviewed ad or pause the campaign.",
                }
            )
            continue
        disapproved = sorted(ad["ad_id"] for ad in live if ad["approval"] == "DISAPPROVED")
        limited = sorted(ad["ad_id"] for ad in live if ad["approval"] == "LIMITED")
        under_review = sorted(ad["ad_id"] for ad in live if ad["approval"] == "UNDER_REVIEW")

        if disapproved and len(disapproved) == len(live):
            findings.append(
                {
                    "severity": "high",
                    "category": "Ad approvals",
                    "campaign": name,
                    "title": "Every enabled ad is disapproved - the campaign cannot serve",
                    "impact_kind": "at_risk",
                    "impact": campaign.daily_budget * Decimal(days),
                    "evidence": [
                        f"All {plural(len(live), 'enabled ad')} are DISAPPROVED: "
                        f"{', '.join(disapproved)}.",
                        f"The campaign holds {money_cents(campaign.daily_budget)}/day of budget "
                        f"({money(campaign.daily_budget * Decimal(days))} over {days} days) that "
                        f"cannot be delivered; actual spend was {money(campaign.spend)}.",
                    ],
                    "fix": (
                        "Fix the policy violation or rebuild the ads. Until then, move the "
                        "budget to a campaign that can serve instead of leaving it stranded."
                    ),
                }
            )
        elif disapproved:
            findings.append(
                {
                    "severity": "high",
                    "category": "Ad approvals",
                    "campaign": name,
                    "title": (
                        f"{len(disapproved)} of {plural(len(live), 'enabled ad')} "
                        f"{'is' if len(disapproved) == 1 else 'are'} disapproved"
                    ),
                    "impact_kind": "at_risk",
                    "impact": ZERO,
                    "evidence": [
                        f"Disapproved ad ids: {', '.join(disapproved)}.",
                        "Rotation is running on fewer ads than intended, which narrows the "
                        "auction and invalidates any in-flight creative test.",
                    ],
                    "fix": "Resolve the policy flag or replace the ads, then restart the test.",
                }
            )

        if limited:
            findings.append(
                {
                    "severity": "medium",
                    "category": "Ad approvals",
                    "campaign": name,
                    "title": (
                        f"{plural(len(limited), 'enabled ad')} serving with limited "
                        "approval"
                    ),
                    "impact_kind": "at_risk",
                    "impact": ZERO,
                    "evidence": [
                        f"Limited ad ids: {', '.join(limited)}.",
                        "Limited ads serve at reduced reach, so their measured performance "
                        "understates what the creative would do unrestricted.",
                    ],
                    "fix": (
                        "Read the specific policy restriction and edit the copy or landing page, "
                        "or request review if the restriction looks wrong."
                    ),
                }
            )

        if under_review:
            findings.append(
                {
                    "severity": "low",
                    "category": "Ad approvals",
                    "campaign": name,
                    "title": f"{plural(len(under_review), 'enabled ad')} still under review",
                    "impact_kind": "at_risk",
                    "impact": ZERO,
                    "evidence": [f"Ad ids under review: {', '.join(under_review)}."],
                    "fix": "Re-check after 24 hours; escalate if still pending.",
                }
            )
    return findings


def check_naming(campaigns, pattern: str) -> list[dict]:
    """Validate campaign names against a `{segment}_{segment}` style pattern."""
    delimiters = re.findall(r"\}(.+?)\{", pattern)
    placeholders = re.findall(r"\{([a-z0-9_]+)\}", pattern, flags=re.IGNORECASE)
    if not placeholders:
        fail(f"--naming-pattern '{pattern}' contains no {{segment}} placeholders")
    if not delimiters:
        fail(f"--naming-pattern '{pattern}' needs a delimiter between placeholders, e.g. '_'")
    if len(set(delimiters)) != 1:
        fail(f"--naming-pattern '{pattern}' mixes delimiters; use one consistent separator")
    delimiter = delimiters[0]
    expected = len(placeholders)

    findings = []
    for campaign in campaigns:
        if not campaign.enabled:
            continue
        segments = campaign.name.split(delimiter)
        problems = []
        if len(segments) != expected:
            problems.append(
                f"has {len(segments)} '{delimiter}'-separated segment(s), expected {expected} "
                f"({'/'.join(placeholders)})"
            )
        if any(segment == "" for segment in segments):
            problems.append("contains an empty segment")
        if any(segment != segment.strip() or " " in segment for segment in segments):
            problems.append("contains whitespace inside a segment")
        if not problems:
            continue
        findings.append(
            {
                "severity": "low",
                "category": "Naming convention",
                "campaign": campaign.name,
                "title": "Campaign name breaks the convention",
                "impact_kind": "governance",
                "impact": campaign.spend,
                "evidence": [
                    f"'{campaign.name}' " + "; ".join(problems) + ".",
                    f"{money(campaign.spend)} of spend cannot be rolled up by geo, funnel stage "
                    "or campaign type without a manual mapping.",
                ],
                "fix": (
                    f"Rename to match {pattern}. Renaming does not reset learning on the major "
                    "platforms, but do it between reporting periods so historical exports stay "
                    "joinable."
                ),
            }
        )
    return findings


def check_negatives(campaigns_by_name, keywords, search_terms, floor: Decimal, high_share: Decimal):
    findings = []
    negatives_by_campaign: dict[str, list[dict]] = {}
    account_negatives = []
    campaigns_with_targeting: set[str] = set()
    for keyword in keywords:
        if keyword["list_type"] == "targeting":
            campaigns_with_targeting.add(keyword["campaign"])
            continue
        if keyword["campaign"] == ACCOUNT_SCOPE:
            account_negatives.append(keyword)
        else:
            negatives_by_campaign.setdefault(keyword["campaign"], []).append(keyword)

    terms_by_campaign: dict[str, list[dict]] = {}
    for term in search_terms:
        terms_by_campaign.setdefault(term["campaign"], []).append(term)

    for name in sorted(terms_by_campaign):
        campaign = campaigns_by_name[name]
        if not campaign.enabled:
            continue
        negatives = negatives_by_campaign.get(name, []) + account_negatives
        uncovered = []
        for term in sorted(terms_by_campaign[name], key=lambda item: (-item["spend"], item["term"])):
            if term["conversions"] > ZERO or term["spend"] < floor:
                continue
            if any(negative_blocks(term["tokens"], negative) for negative in negatives):
                continue
            uncovered.append(term)
        if not uncovered:
            continue
        wasted = sum((term["spend"] for term in uncovered), ZERO)
        share = divide(wasted, campaign.spend)
        share_pct = None if share is None else share * Decimal("100")
        severity = "high" if share_pct is not None and share_pct >= high_share else "medium"
        evidence = [
            f"{plural(len(uncovered), 'search term')} above {money(floor)} converted zero "
            "times and "
            + ("is" if len(uncovered) == 1 else "are")
            + " not blocked by any negative on this campaign or the account list.",
        ]
        for term in uncovered[:5]:
            evidence.append(
                f"  - \"{term['term']}\": {money_cents(term['spend'])} across "
                f"{term['clicks'].quantize(Decimal('1'), rounding=ROUND_HALF_UP):,} clicks, "
                "0 conversions"
            )
        if len(uncovered) > 5:
            evidence.append(f"  - ...and {len(uncovered) - 5} more")
        evidence.append(
            f"That is {money(wasted)}, or {percent(share_pct)} of the campaign's "
            f"{money(campaign.spend)}."
        )
        findings.append(
            {
                "severity": severity,
                "category": "Negative keywords",
                "campaign": name,
                "title": f"{money(wasted)} on search terms no negative blocks",
                "impact_kind": "wasted",
                "impact": wasted,
                "evidence": evidence,
                "fix": (
                    "Add the shared intent tokens as phrase negatives at the campaign level, and "
                    "promote anything account-wide (jobs, salary, free, DIY repair) to the shared "
                    "negative list so new campaigns inherit it."
                ),
            }
        )

    for name in sorted(campaigns_with_targeting):
        if name == ACCOUNT_SCOPE or name not in campaigns_by_name:
            continue
        campaign = campaigns_by_name[name]
        if not campaign.enabled:
            continue
        if negatives_by_campaign.get(name) or account_negatives:
            continue
        findings.append(
            {
                "severity": "medium",
                "category": "Negative keywords",
                "campaign": name,
                "title": "No negative keywords configured",
                "impact_kind": "at_risk",
                "impact": campaign.spend,
                "evidence": [
                    f"{name} runs keyword targeting with zero negatives at the campaign or "
                    "account level.",
                    f"All {money(campaign.spend)} of its spend is exposed to unfiltered queries.",
                ],
                "fix": (
                    "Apply the account's shared negative list as a baseline, then mine this "
                    "campaign's own search-term report weekly for the first month."
                ),
            }
        )
    return findings


def check_stale_creative(campaigns_by_name, ads, as_of: date, stale_days: int, material: Decimal):
    findings = []
    by_campaign: dict[str, list[dict]] = {}
    for ad in ads:
        if ad["status"] == "ENABLED":
            by_campaign.setdefault(ad["campaign"], []).append(ad)

    for name in sorted(by_campaign):
        campaign = campaigns_by_name[name]
        if not campaign.enabled:
            continue
        live = by_campaign[name]
        newest = max(ad["last_modified"] for ad in live)
        age = (as_of - newest).days
        if age < stale_days:
            continue
        findings.append(
            {
                "severity": "medium" if campaign.spend >= material else "low",
                "category": "Creative freshness",
                "campaign": name,
                "title": f"Newest enabled ad is {age} days old",
                "impact_kind": "at_risk",
                "impact": campaign.spend,
                "evidence": [
                    f"All {plural(len(live), 'enabled ad')} were last modified on or before "
                    f"{newest.isoformat()}, {age} days before {as_of.isoformat()} "
                    f"(threshold {stale_days} days).",
                    f"{money(campaign.spend)} ran through creative no one has touched in that "
                    "window.",
                ],
                "fix": (
                    "Ship at least one new variant against the current control. Stale creative "
                    "usually shows up as rising frequency and falling click-through before it "
                    "shows up in conversions."
                ),
            }
        )
    return findings


# ---------------------------------------------------------------------- report


def render(ctx: dict) -> str:
    out: list[str] = []
    add = out.append
    add(f"# Paid account audit — {ctx['account']}")
    add("")
    add(
        f"Period {ctx['period_start']} to {ctx['as_of']} ({ctx['days']} days) | "
        f"{ctx['campaign_count']} campaigns ({ctx['enabled_count']} enabled) | "
        f"{money(ctx['total_spend'])} spend"
    )
    add("")

    add("## Spend at issue")
    add("")
    add("| Bucket | Amount | % of spend | What it means |")
    add("| --- | ---: | ---: | --- |")
    for kind in ("wasted", "unmeasured", "unspent", "at_risk", "governance"):
        amount = ctx["impact_totals"][kind]
        share = divide(amount, ctx["total_spend"])
        add(
            f"| {IMPACT_KINDS[kind]} | {money(amount)} | "
            f"{percent(None if share is None else share * Decimal('100'))} | "
            f"{IMPACT_NOTES[kind]} |"
        )
    add("")
    add(
        f"Conversion tracking coverage: {percent(ctx['tracking_coverage'])} of spend sits in "
        "campaigns with at least one recorded conversion."
    )
    add("")
    add(
        "Buckets overlap by design — one campaign can appear in several — so do not add them "
        "together. Only the wasted-spend bucket is money that can be stopped outright."
    )
    add("")

    add(f"## Findings ({len(ctx['findings'])})")
    add("")
    if not ctx["findings"]:
        add("No findings above threshold. The checks that ran are listed below.")
        add("")
    for index, finding in enumerate(ctx["findings"], start=1):
        impact = (
            ""
            if finding["impact"] == ZERO
            else f" — {money(finding['impact'])} {IMPACT_KINDS[finding['impact_kind']].lower()}"
        )
        add(
            f"### {index}. [{finding['severity'].upper()}] {finding['title']}{impact}"
        )
        add("")
        add(f"- Campaign: `{finding['campaign']}`")
        add(f"- Check: {finding['category']}")
        for line in finding["evidence"]:
            add(f"- {line}" if not line.startswith("  -") else line)
        add(f"- Fix: {finding['fix']}")
        add("")

    add("## Checks that passed")
    add("")
    if ctx["passed"]:
        for name in ctx["passed"]:
            add(f"- {name}")
    else:
        qualifier = "that ran " if ctx["skipped"] else ""
        add(f"- None; every check {qualifier}produced at least one finding.")
    add("")

    if ctx["notes"]:
        add("## Audit notes")
        add("")
        for note in ctx["notes"]:
            add(f"- {note}")
        add("")

    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------------ main


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Paid account hygiene audit.")
    parser.add_argument("--campaigns", required=True)
    parser.add_argument("--ads", required=True)
    parser.add_argument("--keywords", required=True)
    parser.add_argument("--search-terms", dest="search_terms", required=True)
    parser.add_argument("--account", required=True, help="label for the report header")
    parser.add_argument("--period-start", dest="period_start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--as-of", dest="as_of", required=True, help="last day of the export, YYYY-MM-DD")
    parser.add_argument(
        "--naming-pattern",
        dest="naming_pattern",
        default="{brand}_{geo}_{funnel}_{type}",
        help="campaign naming convention (default {brand}_{geo}_{funnel}_{type})",
    )
    parser.add_argument("--stale-days", dest="stale_days", type=int, default=90)
    parser.add_argument("--pace-tolerance", dest="pace_tolerance", default="0.25")
    parser.add_argument("--tracking-floor", dest="tracking_floor", default="300")
    parser.add_argument("--term-floor", dest="term_floor", default="50")
    parser.add_argument("--waste-share-high", dest="waste_share_high", default="5")
    parser.add_argument("--material-spend", dest="material_spend", default="500")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def positive_decimal(raw: str, flag: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        fail(f"{flag} is '{raw}', expected a number")
    if value < ZERO:
        fail(f"{flag} is '{raw}', expected a value >= 0")
    return value


def parse_day(raw: str, flag: str) -> date:
    if not DATE_RE.match(raw):
        fail(f"{flag} is '{raw}', expected YYYY-MM-DD")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        fail(f"{flag} is '{raw}', which is not a real calendar date")


def main(argv=None) -> int:
    args = parse_args(argv)

    period_start = parse_day(args.period_start, "--period-start")
    as_of = parse_day(args.as_of, "--as-of")
    if as_of < period_start:
        fail(f"--as-of {args.as_of} is before --period-start {args.period_start}")
    days = (as_of - period_start).days + 1

    if args.stale_days < 1:
        fail(f"--stale-days is {args.stale_days}, expected a positive number of days")
    tolerance = positive_decimal(args.pace_tolerance, "--pace-tolerance")
    if tolerance >= Decimal("1"):
        fail(f"--pace-tolerance is '{args.pace_tolerance}', expected a fraction below 1")
    tracking_floor = positive_decimal(args.tracking_floor, "--tracking-floor")
    term_floor = positive_decimal(args.term_floor, "--term-floor")
    waste_share_high = positive_decimal(args.waste_share_high, "--waste-share-high")
    material_spend = positive_decimal(args.material_spend, "--material-spend")

    campaigns_by_name = load_campaigns(args.campaigns)
    campaigns = [campaigns_by_name[name] for name in sorted(campaigns_by_name)]
    ads = load_ads(args.ads, campaigns_by_name)
    keywords = load_keywords(args.keywords, campaigns_by_name)
    search_terms = load_search_terms(args.search_terms, campaigns_by_name)

    findings: list[dict] = []
    check_results: dict[str, int] = {}

    def run(label: str, produced: list[dict]) -> None:
        check_results[label] = len(produced)
        findings.extend(produced)

    run("Conversion tracking", check_tracking(campaigns, tracking_floor))
    pacing_findings, no_budget = check_pacing(campaigns, days, tolerance)
    run("Budget pacing", pacing_findings)
    run("Ad approvals", check_approvals(campaigns_by_name, ads, days))
    run("Naming convention", check_naming(campaigns, args.naming_pattern))
    skipped: list[str] = []
    if keywords or search_terms:
        run(
            "Negative keywords",
            check_negatives(
                campaigns_by_name, keywords, search_terms, term_floor, waste_share_high
            ),
        )
    else:
        skipped.append("Negative keywords")
    run(
        "Creative freshness",
        check_stale_creative(campaigns_by_name, ads, as_of, args.stale_days, material_spend),
    )

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER[item["severity"]],
            -item["impact"],
            item["category"],
            item["campaign"],
        )
    )

    total_spend = sum((campaign.spend for campaign in campaigns), ZERO)
    tracked_spend = sum(
        (campaign.spend for campaign in campaigns if campaign.conversions > ZERO), ZERO
    )
    coverage = divide(tracked_spend, total_spend)

    impact_totals = {kind: ZERO for kind in IMPACT_KINDS}
    for finding in findings:
        impact_totals[finding["impact_kind"]] += finding["impact"]

    notes: list[str] = []
    if not keywords and not search_terms:
        notes.append(
            "Not applicable: keywords.csv and search_terms.csv have no data rows (expected for "
            "paid-social accounts), so the search-term waste and negative-keyword checks were "
            "skipped. Wasted spend is therefore $0 by omission, not by verification."
        )
    elif not search_terms:
        notes.append(
            "Not applicable: search_terms.csv has no data rows, so search-term waste could not "
            "be measured. Only the missing-negatives part of the negative-keyword check ran."
        )
    for name in sorted(no_budget):
        notes.append(
            f"`{name}` has no daily budget in the export (shared or portfolio budget); pacing "
            "was not evaluated for it."
        )
    paused = [campaign.name for campaign in campaigns if not campaign.enabled]
    if paused:
        notes.append(
            f"{plural(len(paused), 'non-enabled campaign')} contributed "
            f"{money(sum((campaigns_by_name[name].spend for name in paused), ZERO))} of spend, "
            "included in totals but excluded from every check: "
            + ", ".join(f"`{name}`" for name in sorted(paused))
            + "."
        )
    campaigns_without_ads = sorted(
        campaign.name
        for campaign in campaigns
        if campaign.enabled and not any(ad["campaign"] == campaign.name for ad in ads)
    )
    for name in campaigns_without_ads:
        notes.append(
            f"`{name}` has no rows in the ads export, so approval and creative-freshness checks "
            "could not run for it."
        )

    ctx = {
        "account": args.account,
        "period_start": args.period_start,
        "as_of": args.as_of,
        "days": days,
        "campaign_count": len(campaigns),
        "enabled_count": sum(1 for campaign in campaigns if campaign.enabled),
        "total_spend": total_spend,
        "tracking_coverage": None if coverage is None else coverage * Decimal("100"),
        "impact_totals": impact_totals,
        "findings": findings,
        "passed": [label for label in sorted(check_results) if check_results[label] == 0],
        "notes": notes,
        "skipped": skipped,
    }

    if args.json:
        payload = {
            "account": args.account,
            "period_start": args.period_start,
            "as_of": args.as_of,
            "days": days,
            "total_spend": as_float(total_spend),
            "tracking_coverage_pct": as_float(ctx["tracking_coverage"]),
            "impact_totals": {kind: as_float(value) for kind, value in impact_totals.items()},
            "checks": check_results,
            "skipped_checks": skipped,
            "findings": [
                {
                    "severity": finding["severity"],
                    "category": finding["category"],
                    "campaign": finding["campaign"],
                    "title": finding["title"],
                    "impact_kind": finding["impact_kind"],
                    "impact": as_float(finding["impact"]),
                    "evidence": [line.strip() for line in finding["evidence"]],
                    "fix": finding["fix"],
                }
                for finding in findings
            ],
            "notes": notes,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
