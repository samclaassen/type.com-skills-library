---
name: campaign-monitor
description: Monitor Google Ads campaign performance for daily health, week-over-week metric changes, budget pacing, anomalies, and search term flags. Use for daily paid-search sweeps, spend checks, conversion drops, CPL or CPA spikes, underspend, overspend, and campaign-level alerting.
---

# Campaign Monitor

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill for daily Google Ads account health. The goal is a short alert digest that catches meaningful changes early without drowning the user in normal variance.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, analytics, CRM, spreadsheet, and notification tools.
3. Confirm account, timezone, currency, date range, and primary conversion actions.
4. Pull yesterday, last 7 days, previous 7 days, month to date, and comparable prior period when available.
5. Compare campaign-level spend, conversions, cost per conversion, conversion value, ROAS, CTR, CPC, impression share, and budget status.
6. Check budget pacing against daily and monthly guardrails.
7. Scan recent search terms for obvious waste or brand-safety flags.
8. Classify findings by severity.
9. Report only material changes, recommended follow-ups, and missing data.

## What to Monitor

Campaign metrics:

- spend and budget consumed
- conversions, CPL, CPA, conversion value, and ROAS
- clicks, impressions, CTR, CPC, and search impression share
- limited-by-budget, disapproved, learning, or policy states
- sudden changes in eligible campaigns, ad groups, ads, or keywords

Pacing:

- month-to-date spend versus expected spend
- projected month-end spend
- campaigns that are overspending or underspending
- campaigns constrained by budget with strong conversion efficiency
- campaigns spending despite poor conversion quality

Search term flags:

- terms with high spend and no conversions
- irrelevant intent
- competitor or employment intent when not desired
- support, login, cancel, refund, or existing-customer intent
- sudden new query clusters that need review

## Severity

- `P0` - Major tracking outage, spend runaway, campaigns offline unexpectedly, or same-day budget risk.
- `P1` - Material CPL or CPA spike, conversion drop, budget constraint, or disapproval affecting meaningful spend.
- `P2` - Worth reviewing in the weekly optimization cycle.
- `P3` - Normal variance or too little data.

## Change Rules

- Do not change budgets, bids, status, negatives, or assets unless explicit authority exists.
- If a change is likely needed, draft the recommendation with scope, evidence, expected impact, risk, and rollback.
- If a tracking or data-quality issue is suspected, escalate before making optimization recommendations from bad data.

## Output Format

```text
Campaign monitor: <account>, <date window>.
P0/P1: <top alerts or none>.
Pacing: <overspend/underspend/budget-limited findings>.
Search terms: <flags or none>.
Recommended next step: <specific follow-up or none>.
Blocked: <missing data or none>.
```

If the daily sweep finds no meaningful movement, keep the update to one sentence or stay silent if the runner allows it.


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.

## Related workflow (optional)

[budget-pacer](https://type.com/library/skills/budget-pacer): Reuse its pacing workflow when the user has compatible exports and approved budget assumptions. Read its input contract first; do not assume identical fields or execute it automatically.
