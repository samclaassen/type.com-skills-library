---
name: meta-account-audit
description: Run a read-only Meta Ads account audit across tracking, campaign structure, delivery, creative, audiences, placements, attribution, policy risk, and measurement quality. Use for first-run Meta Ads setup, health checks, monthly audits, Facebook or Instagram Ads account reviews, and "what should we fix first" questions.
---

# Meta Account Audit

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill for a read-only Meta Ads health check. The goal is to identify the highest-leverage fixes before the agent recommends ongoing optimizations.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Meta Ads, Events Manager, analytics, ecommerce, CRM, spreadsheet, and notification tools.
3. Confirm account, timezone, currency, date range, attribution setting, and primary event.
4. Pull account, campaign, ad set, ad, creative, delivery, breakdown, Pixel, CAPI, and revenue context when available.
5. Stop and report if a critical source errors or tracking is too broken to support performance judgment.
6. Use the customer-approved audit rubric if one exists; otherwise report evidence and unknowns without inventing numeric scores.
7. Lead with the verdict and the top three fixes.
8. Recommend next specialist skills or follow-up tasks.
9. Do not mutate the account.

For platform formulas and limitations, load `references/meta-math.md` from the required **Paid Media Operating Definitions** skill (sibling layout: `../paid-media-operating-definitions/references/meta-math.md`). The business's approved metric definitions control the calculation.

## Audit Dimensions

Review these dimensions against the approved operating definitions:

- tracking and event quality
- campaign structure
- learning phase and delivery health
- creative coverage and fatigue risk
- audience and placement strategy
- measurement and attribution quality
- policy and account risk

If the customer supplies a scoring rubric, name its version and show the supporting evidence. Otherwise use descriptive findings. Mark unsupported dimensions `unknown` and identify the missing connection or export.

## Tracking Checks

Tracking is upstream of every optimization decision. Review:

- Pixel activity
- Conversions API status
- deduplication
- event match quality source
- primary event volume
- offline conversion import freshness
- event priority and attribution setting
- discrepancy between Meta, ecommerce, analytics, and CRM sources

If Pixel or CAPI signal is missing or stale, recommend fixing measurement before scaling.

## Structure Checks

Review:

- objective and optimization-event alignment
- CBO versus ABO use
- prospecting, retargeting, and retention separation
- ad set count and event volume per ad set
- active ads per ad set
- special ad category classification
- naming conventions
- budget concentration and single-point-of-failure campaigns

## Creative Checks

Review:

- active creative concepts
- format mix: image, video, carousel, Reels, Stories, feed
- aspect ratio coverage
- hook strength and video retention when available
- fatigued ads
- repeated assets across ad sets
- policy-sensitive copy or visuals

## Output Format

```text
Meta account audit: <account>, <date window>.
Verdict: <one-sentence account health>.
Top fixes: <1-3 prioritized fixes>.
Scorecard: <dimension scores or unknowns>.
Tracking: <health and blockers>.
Structure: <main issue>.
Creative: <main issue>.
Measurement: <Meta attribution vs approved business revenue caveat>.
Next: <specialist skill or approval request>.
```

Keep the report focused. Cite specific campaigns, ad sets, ads, events, and dollar amounts when connected data provides them.

## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.

## Related workflow (optional)

[ad-account-audit](https://type.com/library/skills/ad-account-audit): Reuse its export-based hygiene checks when relevant; this skill retains Meta-specific tracking, delivery and creative diagnosis. Read its input contract first; do not assume identical fields or execute it automatically.
