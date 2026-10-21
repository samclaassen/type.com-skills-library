---
name: meta-revenue-attribution
description: Connect Meta Ads spend to Shopify, GA4, HubSpot, Salesforce, BI, pipeline, closed revenue, MER, CAC, and downstream quality. Use for revenue-informed Meta reporting, attribution gap checks, ecommerce performance, lead quality, pipeline summaries, and blended efficiency analysis.
---

# Meta Revenue Attribution

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work connects Meta Ads performance to off-platform outcomes. The goal is to separate platform-attributed performance from approved business revenue, pipeline, and customer quality.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Meta Ads, Shopify or ecommerce, GA4, HubSpot, Salesforce, warehouse, spreadsheet, and BI tools.
3. Confirm account, date range, timezone, currency, attribution setting, primary event, revenue source, and CRM stages.
4. Pull Meta spend, campaigns, ad sets, ads, conversion value, results, and tracking parameters.
5. Pull ecommerce orders, refunds, revenue, new versus returning customers, or CRM leads, demos, pipeline, and closed revenue where available.
6. Normalize campaign, source, medium, content, term, click ID, order, contact, company, and deal fields.
7. Separate lead volume, qualified pipeline, closed revenue, ecommerce revenue, blended revenue, and sales-cycle lag.
8. Check attribution gaps before drawing conclusions.
9. Summarize revenue impact and recommend media actions tied to downstream quality.

For platform formulas and limitations, load `references/meta-math.md` from the required **Paid Media Operating Definitions** skill (sibling layout: `../paid-media-operating-definitions/references/meta-math.md`). The business's approved metric definitions control the calculation.

## Source hierarchy

Use the revenue source approved in Revenue Definitions. GA4 attribution, platform claims, CRM pipeline, closed-won deal value, and recognized revenue are distinct measures. Reconcile compatible totals without treating one as interchangeable with another. Preserve unmatched records and quantify join coverage; deduplicate using stable IDs at the appropriate grain before summing.

## Metrics to Report

Report only metrics supported by connected data:

- Meta Ads spend
- purchases, leads, demos, or primary results
- platform-reported conversion value and ROAS
- ecommerce revenue and order count
- refunds and cancellations when available
- new customer revenue
- MER
- CRM MQL, SQL, opportunity, open pipeline, closed-won revenue, and closed-lost outcomes
- CAC, cost per demo, cost per SQL, pipeline-to-spend ratio, or LTV:CAC
- stage conversion rates and sales-cycle lag

## Quality Checks

Before making recommendations, check:

- missing UTMs or click IDs
- inconsistent UTM casing or naming
- campaign renames that broke joins
- pixel and CAPI freshness
- duplicate orders, contacts, or deals
- refunds, cancellations, and subscription churn
- recent leads that have not had time to progress
- attribution setting changes during the reporting window

## Recommendation Rules

- Scale campaigns that produce profitable revenue or qualified pipeline, not just cheap platform conversions.
- Flag campaigns with strong in-platform ROAS but weak MER, refund-adjusted revenue, or CRM quality.
- Protect campaigns with weak short-window ROAS if cohort-matured revenue is strong.
- Do not claim revenue attribution when the join is incomplete or stale.
- Keep customer, order, and deal names out of broad reports unless the channel is approved.

## Output Format

```text
Meta revenue attribution: <account>, <date window>.
Spend: <amount>.
Platform: <Meta results and ROAS/CPA>.
Approved business revenue: <source, basis, and amount or unavailable>.
Analytics attribution: <GA4 or platform-attributed outcomes, separately labeled>.
CRM pipeline: <open pipeline and closed-won value, separately labeled>.
Efficiency: <MER, CAC, ROAS, or pipeline-to-spend>.
Best sources: <campaigns, ad sets, or creative themes>.
Weak sources: <campaigns, ad sets, or creative themes>.
Attribution gaps: <issues>.
Recommendation: <budget, creative, audience, landing page, or tracking action>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
