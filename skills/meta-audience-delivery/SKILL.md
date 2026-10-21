---
name: meta-audience-delivery
description: Review Meta Ads audience strategy, audience overlap, broad targeting, lookalikes, retargeting, placements, learning phase, and delivery constraints. Use for ad set diagnosis, Learning Limited issues, audience saturation, placement waste, prospecting versus retargeting structure, and delivery troubleshooting.
---

# Meta Audience Delivery

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work is audience, placement, or delivery diagnosis. The goal is to decide whether performance is limited by targeting structure, audience saturation, learning phase, placements, or event volume.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Meta Ads, Events Manager, analytics, ecommerce, CRM, and spreadsheet tools.
3. Confirm account, campaign scope, date range, timezone, currency, attribution setting, and primary event.
4. Pull campaigns, ad sets, targeting summaries, placements, delivery status, learning-stage context, event volume, and breakdowns.
5. Separate prospecting, retargeting, retention, and special-purpose campaigns.
6. Check learning phase before judging steady-state performance.
7. Check audience overlap, concentration, and placement mix.
8. Draft consolidation, exclusion, placement, or testing recommendations.
9. Apply changes only when explicit authority exists.

## Delivery Checks

Review:

- active ad sets with low event volume
- Learning or Learning Limited status
- recent significant edits
- optimization event fit
- ad set count relative to budget and event volume
- one ad set carrying most campaign spend
- campaign budget optimization versus ad set budgets
- rejected, limited, or policy-affected ads

Avoid stacking edits while an ad set is still learning unless the goal is to fix the learning problem.

## Audience Checks

Review:

- broad prospecting
- lookalikes and seed quality
- interest or behavior stacks
- custom audiences
- retargeting windows
- purchaser or customer exclusions
- geography, age, gender, and language constraints
- audience overlap between sibling ad sets
- special ad category limits

Do not recommend narrow audience layering just because a segment is intuitively appealing. Meta delivery often needs enough audience size and event density to learn.

## Placement Checks

Review placement performance by:

- Facebook feed
- Instagram feed
- Reels
- Stories
- Marketplace
- Audience Network
- Messenger or other connected placements

Recommend placement exclusions only when evidence is strong enough and the bidding strategy supports manual placement control. Otherwise, draft a creative-format fix first.

## Output Format

```text
Meta audience delivery: <account>, <date window>.
Learning: <ad sets in learning or learning limited>.
Audience: <overlap, saturation, or structure findings>.
Placements: <winners, losers, or inconclusive>.
Recommendations: <consolidation, exclusion, test, or watch>.
Needs approval: <specific edits>.
Blocked: <missing delivery or breakdown data>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.
