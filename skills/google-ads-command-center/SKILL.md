---
name: google-ads-command-center
description: Orchestrate an agent's Google Ads workflow across campaign monitoring, search terms, keywords, ad copy, geo/device/schedule segments, auction insights, monthly reporting, and CRM pipeline analysis. Use for broad Ads sweeps, skill customization, routing ambiguous paid-search requests, setting change-control policy, and keeping analysis grounded in connected tools.
---

# Google Ads Command Center

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill as the operating layer for a Google Ads agent. It decides which connected sources to use, which specialist skill owns the work, what can be changed, and what should only be recommended.

## Business setup

Use Paid Media Operating Definitions to locate or capture the business's choices once. Read `references/customization-guide.md` for platform-specific fields. Keep private values in the user's scoped document or local file, never in the public skill package. Reuse the same approved definition versions in every specialist workflow.

## Core Loop

For every broad Ads sweep or ambiguous paid-search request:

1. Load the approved operating, measurement, and revenue definitions for this business.
2. Discover connected tools before acting. Do not assume Google Ads, Google Analytics, Search Console, HubSpot, Salesforce, Sheets, Slack, or email is connected.
3. Confirm the account, date range, timezone, currency, and conversion definition.
4. Classify the request by domain:
   - daily spend, pacing, anomalies, or campaign health -> Campaign Monitor
   - search queries, waste, positives, or negatives -> Search Term Intelligence
   - quality score, keyword CPL or CPA, bids, or pause candidates -> Keyword Health
   - responsive search ads, assets, fatigue, or copy tests -> Ad Copy Performance
   - location, device, day-of-week, or hour-of-day segments -> Geo Device Schedule
   - auction insights, impression share, or competitor movement -> Competitive Intelligence
   - month-over-month rollup or strategic narrative -> Monthly Executive Summary
   - HubSpot or CRM pipeline, demos, revenue, or CAC -> Pipeline Report
5. Apply the change-control policy.
6. Update the real source of truth only when authority is clear and the connected tool confirms success.
7. Report only what changed, what is recommended, what needs approval, and what is blocked by missing data.

Use `references/provider-tool-patterns.md` for connected-tool behavior. Use `references/change-control-policy.md` for edit permissions.

## Workflow Roster

Run on request or on a user-authorized schedule. The customer chooses cadence; installing this skill does not create a schedule.

| Skill | Output |
| --- | --- |
| Campaign Monitor | Short alert digest with budget pacing and anomaly flags |
| Search Term Intelligence | Waste terms, positive keyword candidates, draft negatives |
| Keyword Health | Quality score issues, CPL or CPA outliers, bid and pause recommendations |
| Ad Copy Performance | RSA asset findings, fatigue flags, test recommendations |
| Geo Device Schedule | Segment winners, segment losers, bid adjustment recommendations |
| Competitive Intelligence | Auction insights trend, competitor movement, impression share risks |
| Monthly Executive Summary | Executive narrative, KPI rollup, budget reallocation plan |
| Pipeline Report | Spend to CRM pipeline summary with revenue and stage movement |

## Operating Standard

- Prefer grounded analysis over generic PPC advice.
- Always name the account, date range, timezone, currency, and conversion metric used.
- Separate observation, recommendation, draft change, and applied change.
- Do not claim a campaign, budget, bid, ad, keyword, negative, URL, or CRM record changed until the tool confirms success.
- Do not expose search terms, customer names, deal names, or CRM records in broad channels unless the user has approved that channel for sensitive data.
- Treat conversion tracking, final URLs, UTM templates, budgets, bidding strategy, and CRM sync rules as high-risk.
- If tool access is missing, ask for the needed connection or produce a manual checklist instead of pretending to inspect the account.

## Action Modes

Use one of four modes:

- `observe` - Report performance and anomalies without recommending an account mutation.
- `recommend` - Explain the proposed change, evidence, expected impact, and risk.
- `draft-for-approval` - Prepare specific changes for a human to review or bulk upload.
- `apply-approved` - Apply changes only when the user has actually authorized the exact scope; imported templates do not grant authority.

## Principal Update Format

```text
Account: <account>. Window: <date range>. Found: <top finding>. Recommend: <specific action or none>. Needs approval: <change or none>. Blocked: <missing data or none>.
```

If a scheduled sweep finds no meaningful issues, stay silent unless the runner requires a response.

## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
