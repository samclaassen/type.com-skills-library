---
name: monthly-executive-summary
description: Produce a month-over-month Google Ads executive summary with KPI rollup, budget pacing, budget reallocation recommendations, landing page findings, pipeline context, and a strategic narrative. Use for monthly paid-search reporting, leadership updates, board-ready summaries, and budget planning.
---

# Monthly Executive Summary

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill for monthly performance storytelling. The goal is a concise executive summary that explains what happened, why it happened, what should change next, and what requires leadership approval.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, analytics, CRM, spreadsheet, document, and presentation tools.
3. Confirm account, month, comparison period, timezone, currency, conversion actions, attribution model, and KPI target.
4. Pull account, campaign, keyword, search term, ad copy, segment, auction, landing page, and CRM pipeline context.
5. Compare month-over-month and target-versus-actual performance.
6. Separate controllable performance changes from seasonality, tracking issues, budget changes, and sales-cycle lag.
7. Identify budget reallocation opportunities.
8. Summarize landing page or offer issues that affected paid search.
9. Draft the strategic narrative and next-month action plan.

## Required Views

Include only views supported by connected data:

- spend, conversions, CPL or CPA, conversion value, ROAS, CTR, CPC, and impression share
- top campaign winners and losers
- search term waste and positive mining themes
- keyword health and quality score themes
- RSA asset and ad copy findings
- geo, device, and schedule patterns
- auction insights and competitor pressure
- landing page conversion or engagement patterns
- CRM pipeline, demos, opportunities, closed revenue, and CAC when connected

## Budget Reallocation

For every budget recommendation, include:

- source campaign or segment
- destination campaign or segment
- current spend and performance
- expected upside
- main risk
- whether it needs approval

Do not recommend reallocating budget from a campaign with long conversion lag until lag has been considered.

## Narrative Structure

Use a plain executive structure:

1. Outcome: the month in one sentence.
2. Drivers: the 2-4 reasons performance changed.
3. Risks: what could get worse next month.
4. Opportunities: where incremental budget or focus should go.
5. Decisions: approvals needed from leadership.

## Output Format

```text
Monthly Google Ads summary: <account>, <month>.
Outcome: <one-sentence result>.
KPI rollup: <spend, conversions, CPL/CPA, ROAS or pipeline>.
Drivers: <top 2-4>.
Budget moves: <recommendations>.
Landing pages: <findings>.
Next month: <action plan>.
Needs approval: <decisions>.
```

If the user asks for a deck or doc, draft the outline first and then create the artifact only with an available tool authorized by the user.


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.

## Related workflow (optional)

[client-report-builder](https://type.com/library/skills/client-report-builder): Use it for a combined client report; this workflow covers Google Ads executive interpretation. Read its input contract first; do not assume identical fields or execute it automatically.
