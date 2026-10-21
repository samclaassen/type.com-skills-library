---
name: meta-performance-monitor
description: Monitor Meta Ads daily or weekly performance across spend, pacing, ROAS or CPA, CPM, link CTR, frequency, delivery status, anomalies, and account health. Use for Facebook or Instagram Ads sweeps, spend checks, conversion drops, CPA spikes, ROAS declines, and delivery alerts.
---

# Meta Performance Monitor

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill for recurring Meta Ads performance checks. The goal is to catch material changes early and route root causes to the right follow-up skill.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Meta Ads, Events Manager, analytics, ecommerce, CRM, spreadsheet, and notification tools.
3. Confirm account, timezone, currency, date range, attribution setting, and primary event.
4. Pull yesterday, last 7 days, previous 7 days, month to date, and comparable prior period when available.
5. Compare spend, results, CPA, purchase value, ROAS, CPM, link CTR, CPC, reach, frequency, delivery status, and budget pacing.
6. Segment by campaign, ad set, and top-spend ads.
7. Check whether a tracking, landing page, or revenue-source issue explains performance.
8. Classify findings by severity.
9. Report material alerts, likely causes, and recommended next steps.

For platform formulas and limitations, load `references/meta-math.md` from the required **Paid Media Operating Definitions** skill (sibling layout: `../paid-media-operating-definitions/references/meta-math.md`). The business's approved metric definitions control the calculation.

## What to Monitor

Performance:

- spend and budget consumed
- purchases, leads, demos, or primary results
- CPA, ROAS, conversion value, and MER when available
- impressions, reach, CPM, link clicks, link CTR, CPC, and frequency
- landing page views and landing page CVR when available

Delivery:

- active, paused, rejected, limited, learning, or learning-limited states
- ad sets with too little event volume
- campaign or ad set budget concentration
- ads that stopped receiving delivery
- recent edits that may explain volatility

Pacing:

- month-to-date spend versus expected spend
- projected month-end spend
- overspending or underspending campaigns
- budget-limited campaigns with strong economics
- spending campaigns with weak downstream quality

## Severity

- `P0` - Tracking outage, spend runaway, account disabled, major rejection, or same-day budget risk.
- `P1` - Material CPA spike, ROAS drop, CPM spike, conversion drop, or delivery collapse.
- `P2` - Worth reviewing in the weekly optimization cycle.
- `P3` - Normal variance or too little data.

## Diagnosis Hints

- CPM up and frequency up often points to creative fatigue or audience saturation.
- CTR down before CPA rises often points to weak creative or message fatigue.
- CPA up with stable CTR can point to landing page, checkout, form, or attribution issues.
- ROAS down in Meta but MER stable can point to attribution mix, not necessarily bad media buying.
- Learning Limited often points to too little event volume per ad set.

## Output Format

```text
Meta performance monitor: <account>, <date window>.
P0/P1: <top alerts or none>.
Pacing: <overspend/underspend findings>.
Delivery: <learning, rejection, or status issues>.
Likely cause: <creative, audience, tracking, landing page, attribution, or normal variance>.
Recommended next step: <specific follow-up or none>.
Blocked: <missing data or none>.
```

If the sweep finds no meaningful movement, keep the update to one sentence or stay silent if the runner allows it.


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
