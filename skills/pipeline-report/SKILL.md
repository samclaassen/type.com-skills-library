---
name: pipeline-report
description: Connect Google Ads spend to HubSpot CRM pipeline, demos, deal stages, closed revenue, and CAC with an AI summary. Use for weekly paid-search pipeline reporting, CRM quality checks, stage movement summaries, and revenue-informed Google Ads recommendations.
---

# Pipeline Report

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work connects Google Ads performance to CRM outcomes. Use the CRM source named by the customer, such as HubSpot or Salesforce.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, HubSpot or CRM, analytics, spreadsheet, and BI tools.
3. Confirm account, date range, timezone, currency, conversion actions, attribution model, CRM stages, and revenue rules.
4. Pull Google Ads spend, clicks, conversions, campaigns, ad groups, keywords, and tracking parameters.
5. Pull CRM leads, contacts, companies, deals, lifecycle stages, demos, pipeline value, close dates, and closed revenue where available.
6. Normalize source, medium, campaign, ad group, keyword, gclid, UTM, and CRM owner fields.
7. Separate leads, demos, qualified pipeline, open pipeline, closed-won revenue, and closed-lost outcomes.
8. Check for attribution gaps before drawing conclusions.
9. Summarize pipeline impact and recommend Ads actions tied to downstream quality.

For attribution handling, read `references/crm-attribution-guide.md`.

## Metrics to Report

Report only metrics supported by connected data:

- Google Ads spend
- leads or form fills
- demos or meetings booked
- MQL, SQL, opportunity, or qualified pipeline stages
- open pipeline value
- closed-won revenue
- closed-lost count or value
- CPL, cost per demo, cost per SQL, CAC, ROAS, or pipeline-to-spend ratio
- stage conversion rates
- sales-cycle lag

## Quality Checks

Before making recommendations, check:

- missing gclid or UTM values
- offline conversion import freshness
- duplicate contacts or deals
- demo source field consistency
- campaign renames that broke joins
- CRM stage definitions
- whether recent leads have had enough time to progress

## Recommendation Rules

- Scale campaigns that produce qualified pipeline, not just cheap leads.
- Flag campaigns with strong lead volume but poor stage progression.
- Apply the customer-approved strategic coverage and CAC rules; do not invent exceptions from closed-won deal value alone.
- Do not claim revenue attribution when the CRM link is incomplete or stale.
- Keep customer and deal names out of broad reports unless the channel is approved.

## Output Format

```text
Pipeline report: <account>, <date window>.
Spend: <amount>.
Funnel: <leads -> demos -> pipeline -> closed revenue>.
Best sources: <campaigns or themes>.
Weak sources: <campaigns or themes>.
Attribution gaps: <issues>.
Recommendation: <budget, keyword, landing page, or tracking action>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
