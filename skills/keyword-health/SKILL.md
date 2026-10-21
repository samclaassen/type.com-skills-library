---
name: keyword-health
description: Review Google Ads keyword health across quality score, keyword-level CPL or CPA, conversion quality, bid recommendations, and pause candidates. Use for weekly keyword reviews, low quality score diagnosis, expensive keywords, bid changes, and low-performer pause recommendations.
---

# Keyword Health

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work is keyword-level diagnosis and action planning. The goal is to identify which keywords deserve more budget, lower bids, landing page work, match type changes, or pausing.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Google Ads, analytics, CRM, and spreadsheet tools.
3. Confirm account, campaign scope, timezone, currency, date range, conversion actions, and KPI target.
4. Pull keyword performance with quality score components when available.
5. Compare keyword CPL, CPA, ROAS, conversion rate, CTR, CPC, impression share, and CRM quality.
6. Separate brand, competitor, prospecting, remarketing, and experimental keywords.
7. Diagnose quality score issues before recommending bid cuts.
8. Check bidding strategy and supported controls before drafting bid changes; manual keyword bids may not apply under automated bidding. Draft compatible match type, landing page, negative, or pause recommendations.
9. Apply changes only when explicit authority exists.

## Health Checks

Quality score:

- expected CTR
- ad relevance
- landing page experience
- keyword to ad group theme fit
- search term mismatch

Efficiency:

- CPL or CPA versus target
- conversion value or ROAS versus target
- spend with no conversions
- assisted or lagged conversions when available
- CRM stage quality and closed revenue when connected

Coverage:

- impression share lost to budget
- impression share lost to rank
- top of page rate
- absolute top rate
- eligible but low-volume keywords

## Recommendation Types

- `increase-bid` - efficient keyword is rank-limited or losing impression share to rank.
- `decrease-bid` - keyword converts but above target and not strategically required.
- `pause-candidate` - repeated spend without conversions or poor CRM quality after enough data.
- `quality-work` - quality score issue likely needs copy, structure, or landing page work.
- `match-type-change` - query behavior suggests tighter or broader matching.
- `keep-watching` - signal is interesting but data is not sufficient.

## Pause Rules

Do not recommend pausing solely from a tiny sample. Consider:

- minimum spend or click threshold from the approved operating definitions
- conversion lag
- brand protection or strategic coverage
- whether negatives or match type tightening would solve the issue
- whether the landing page or tracking is broken

## Output Format

```text
Keyword health: <account>, <date window>.
Winners: <keywords to protect or scale>.
Issues: <quality score or efficiency problems>.
Recommendations: <bid, pause, match, landing page, or negative actions>.
Needs approval: <specific changes>.
Blocked: <missing data or none>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
