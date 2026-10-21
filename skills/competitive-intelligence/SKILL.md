---
name: competitive-intelligence
description: Review Google Ads auction insights, impression share trends, competitor movement, overlap, outranking share, and defensive opportunities. Use for weekly auction reviews, competitor activity summaries, share-of-voice changes, and budget or messaging recommendations tied to competitive pressure.
---

# Competitive Intelligence

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work is competitive analysis from Google Ads and connected market context. The goal is to explain auction pressure and recommend practical responses without overstating what auction insights can prove.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, analytics, CRM, search results, spreadsheet, and document tools.
3. Confirm account, campaign scope, timezone, currency, date range, competitor list, and protected brand terms.
4. Pull auction insights by campaign, ad group, keyword, or keyword theme when available.
5. Compare impression share, absolute top impression share, top impression share, overlap rate, position above rate, outranking share, and lost impression share.
6. Segment brand, non-brand, competitor, and category campaigns separately.
7. Look for competitor movements that coincide with CPC, CTR, conversion rate, or impression share shifts.
8. Draft defensive, offensive, budget, bid, copy, or landing page recommendations.

## What to Watch

- sudden impression share loss on strategic terms
- competitor overlap increases
- competitors moving above the account more often
- brand campaigns losing absolute top share
- non-brand CPC inflation linked to new auction entrants
- competitor ad copy or offer changes when search result tools are available
- budget-limited campaigns losing efficient share

## Interpretation Rules

- Auction insights show auction behavior, not exact competitor budgets.
- Do not claim a competitor launched a campaign unless the connected evidence supports it.
- Separate brand-defense issues from normal non-brand auction volatility.
- If impression share fell because budget was intentionally reduced, say that plainly.
- If conversion quality also fell, do not recommend bidding up without checking landing page and CRM quality.

## Response Types

- `defend-brand` - protect brand terms when brand share or absolute top share drops materially.
- `increase-coverage` - recommend more budget or rank improvement where efficient non-brand demand is being lost.
- `copy-response` - draft message tests that answer competitor offers or claims.
- `landing-page-response` - recommend page updates when competitor positioning exposes a gap.
- `watchlist` - monitor movement without action when evidence is thin.

## Output Format

```text
Competitive intelligence: <account>, <date window>.
Auction movement: <top changes>.
Competitors: <notable domains and evidence>.
Impact: <CPC, CTR, conversion, impression share, or revenue effect>.
Recommendation: <defend, scale, test, or watch>.
Needs approval: <specific changes>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
