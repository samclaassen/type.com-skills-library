---
name: meta-ads-command-center
description: Orchestrate an agent's Meta Ads workflow across account audit, performance monitoring, creative fatigue, audience and delivery analysis, budget scaling, and revenue attribution. Use for broad Meta Ads sweeps, Facebook or Instagram Ads questions, skill customization, routing ambiguous paid-social requests, and setting change-control policy.
---

# Meta Ads Command Center

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill as the operating layer for Meta Ads work. It decides which connected sources to use, which specialist skill owns the request, what can be changed, and what should only be recommended.

## Business setup

Use Paid Media Operating Definitions to locate or capture the business's choices once. Read `references/customization-guide.md` for platform-specific fields. Keep private values in the user's scoped document or local file, never in the public skill package. Reuse the same approved definition versions in every specialist workflow.

## Core Loop

For every broad Meta Ads sweep or ambiguous paid-social request:

1. Load the approved operating, measurement, and revenue definitions for this business.
2. Discover connected tools before acting. Do not assume Meta Ads Manager, Meta Marketing API, GA4, Shopify, HubSpot, Salesforce, Sheets, Slack, or email is connected.
3. Confirm account, date range, timezone, currency, attribution setting, and primary event.
4. Classify the request by domain:
   - account setup, tracking, structure, or first-run health -> Meta Account Audit
   - daily spend, pacing, ROAS or CPA, CPM, CTR, frequency, or anomalies -> Meta Performance Monitor
   - ad creative, hooks, formats, fatigue, or refresh briefs -> Meta Creative Fatigue
   - audience overlap, broad targeting, lookalikes, retargeting, placements, learning phase, or delivery constraints -> Meta Audience Delivery
   - budgets, CBO or ABO, scaling, forecasts, and profit headroom -> Meta Budget Scaling
   - Shopify, GA4, HubSpot, Salesforce, BI, MER, pipeline, or revenue attribution -> Meta Revenue Attribution
5. Apply the change-control policy.
6. Update the real source of truth only when authority is clear and the connected tool confirms success.
7. Report only what changed, what is recommended, what needs approval, and what is blocked by missing data.

Use `references/provider-tool-patterns.md` for connected-tool behavior. Use `references/change-control-policy.md` for edit permissions. For platform formulas and limitations, load `references/meta-math.md` from the required **Paid Media Operating Definitions** skill (sibling layout: `../paid-media-operating-definitions/references/meta-math.md`). The business's approved metric definitions control the calculation.

## Workflow Roster

Run on request or on a user-authorized schedule. The customer chooses cadence; installing this skill does not create a schedule.

| Skill | Output |
| --- | --- |
| Meta Account Audit | Read-only health scorecard and top fixes |
| Meta Performance Monitor | Spend, pacing, anomaly, and delivery digest |
| Meta Creative Fatigue | Creative fatigue diagnosis and refresh priorities |
| Meta Audience Delivery | Audience, placement, and learning-phase recommendations |
| Meta Budget Scaling | Budget forecast, scale plan, and risk controls |
| Meta Revenue Attribution | Spend to revenue, MER, pipeline, or CAC summary |

## Operating Standard

- Prefer grounded analysis over generic paid-social advice.
- Always name the account, date range, timezone, currency, attribution setting, and primary event used.
- Separate observation, recommendation, draft change, and applied change.
- Use the customer-approved click variant and label it explicitly (for example, link CTR versus all-click CTR); never compare mismatched variants.
- Treat reported Meta ROAS as in-platform attributed performance, not ground-truth revenue.
- Cross-check against the approved business revenue source from Revenue Definitions. Show GA4 attribution and CRM pipeline/deal values separately; they are not interchangeable with accounting revenue.
- Treat Pixel, Conversions API, deduplication, event priority, final URLs, UTMs, budgets, bidding, audience lists, and CRM sync rules as high-risk.
- If tracking is broken, do not make confident scaling recommendations from the broken data.
- If tool access is missing, ask for the needed connection or produce a manual checklist instead of pretending to inspect the account.

## Action Modes

Use one of four modes:

- `observe` - Report performance and anomalies without recommending an account mutation.
- `recommend` - Explain the proposed change, evidence, expected impact, and risk.
- `draft-for-approval` - Prepare specific changes or briefs for a human to review.
- `apply-approved` - Apply changes only when the user has actually authorized the exact scope; imported templates do not grant authority.

## Principal Update Format

```text
Meta Ads: <account>. Window: <date range>. Found: <top finding>. Recommend: <specific action or none>. Needs approval: <change or none>. Blocked: <missing data or none>.
```

If a scheduled sweep finds no meaningful issues, stay silent unless the runner requires a response.

## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
