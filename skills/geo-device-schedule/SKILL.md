---
name: geo-device-schedule
description: Analyze Google Ads performance by location, device, day of week, and hour of day, then recommend bid adjustments, exclusions, or scheduling changes. Use for biweekly segment reviews, regional waste, mobile or desktop issues, and ad schedule optimization.
---

# Geo Device Schedule

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work is segment-level optimization across geography, device, day of week, or hour of day. The goal is to find repeatable pockets of efficiency and waste without slicing data so thin that the conclusion becomes noise.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, analytics, CRM, and spreadsheet tools.
3. Confirm account, campaign scope, timezone, currency, date range, conversion actions, and KPI target.
4. Pull segmented performance for location, device, day of week, and hour of day.
5. Compare spend, conversions, CPL or CPA, conversion rate, ROAS, impression share, and CRM quality.
6. Check whether campaigns use automated bidding, manual bidding, or another strategy that limits bid adjustment usefulness.
7. Identify segment winners, segment losers, and inconclusive segments.
8. Draft bid adjustment, exclusion, budget split, schedule, or landing page recommendations.
9. Apply changes only when explicit authority exists.

## Segment Rules

Avoid overfitting:

- require minimum spend, clicks, or conversions from the approved operating definitions
- compare multiple windows when possible
- treat recent partial days carefully
- account for conversion lag
- compare segment share of spend against segment share of conversions or revenue
- check CRM quality before scaling cheap but low-quality lead sources

## Location Analysis

Look for:

- high-spend locations above target CPL or CPA
- efficient locations constrained by budget or rank
- excluded or low-bid locations that now show demand
- countries, states, regions, or metros that should be split into their own campaigns
- sales coverage or service-area constraints

## Device Analysis

Look for:

- mobile conversion-rate issues that may indicate landing page or form friction
- desktop or tablet pockets with stronger revenue quality
- device-specific CPA gaps
- call conversion behavior, if call tracking is connected
- automated bidding constraints before recommending device bid modifiers

## Schedule Analysis

Look for:

- high-cost hours with weak conversion or CRM quality
- strong weekday or weekend patterns
- overnight spend that sales cannot support
- time zones across target locations
- enough data before recommending narrow schedules

## Output Format

```text
Geo/device/schedule: <account>, <date window>.
Winners: <segments to protect or scale>.
Losers: <segments to reduce or review>.
Recommendations: <bid adjustment, exclusion, schedule, or budget changes>.
Needs approval: <specific edits>.
Blocked: <missing data or automation limits>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
