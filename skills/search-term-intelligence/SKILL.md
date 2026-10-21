---
name: search-term-intelligence
description: Analyze Google Ads search terms for waste detection, positive keyword mining, theme clustering, and draft negative keyword changes. Use for weekly search query reviews, irrelevant spend, new keyword opportunities, auto-negative proposals, and query intent summaries.
---

# Search Term Intelligence

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work is primarily search query analysis. The goal is to reduce waste, find useful demand, and turn raw search terms into safe keyword and negative keyword recommendations.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, analytics, CRM, and spreadsheet tools.
3. Confirm account, campaign scope, match types, timezone, currency, date range, and conversion actions.
4. Pull search term performance for the last 7 days, 30 days, and 90 days when available.
5. Preserve raw search queries, then group normalized variants for analysis. Negative keywords do not automatically match close variants; verify the exact proposed text and current platform match semantics before drafting each negative.
6. Cluster terms by intent theme.
7. Classify each cluster as waste, positive opportunity, ambiguous, protected brand, competitor, or informational.
8. Draft negatives and positive keyword candidates separately.
9. Apply negatives only when explicit authority exists and the match type is safe for the evidence.

## Waste Detection

Flag waste when search terms show:

- high spend with no qualified conversions
- intent that cannot buy or convert
- job, career, internship, login, support, refund, cancellation, free, template, or definition intent
- irrelevant industries, geographies, products, or audiences
- repeated poor downstream quality in CRM or analytics

Do not mark a term as waste solely because it has no conversion in a short window. Consider spend, clicks, query specificity, conversion lag, and CRM quality.

## Positive Mining

Find positive keyword candidates when terms show:

- repeated conversions or strong qualified pipeline
- high CTR and landing-page engagement
- valuable long-tail language not already covered by exact or phrase keywords
- clear product, problem, competitor, or segment intent
- low-cost discovery themes worth isolating in a new ad group

For each candidate, recommend the campaign or ad group, match type, landing page, and expected value.

## Negative Keyword Drafting

For every proposed negative, include:

- term or theme
- suggested match type
- campaign or ad group scope
- spend, clicks, conversions, and CPL or CPA
- reason
- risk of blocking useful traffic

Prefer exact negatives for narrow evidence. Use phrase or broad negatives only when the excluded concept is clearly invalid across the scoped campaign.

## Output Format

```text
Search term intelligence: <account>, <date window>.
Waste: <top terms or clusters with evidence>.
Positive mining: <keyword candidates>.
Draft negatives: <count and highest-impact examples>.
Ambiguous: <terms needing human review>.
Applied: <changes applied or none>.
```

If the user asks for a bulk upload, produce a reviewable table rather than applying changes silently.


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
