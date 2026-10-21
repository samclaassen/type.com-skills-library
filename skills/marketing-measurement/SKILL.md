---
name: marketing-measurement
description: Configure and calculate ROAS, marketing efficiency ratio (MER), and new-customer CAC using a business's approved revenue, spend, customer and attribution definitions. Use when marketing dashboards disagree, when setting attribution windows, or when comparing platform claims with actual sales.
---

# Marketing Measurement

## Shared definitions prerequisite

Read and apply the [Revenue Definitions skill](https://type.com/library/skills/revenue-definitions) before this workflow. If installed as sibling folders, read `../revenue-definitions/SKILL.md`; otherwise load the installed skill by name or obtain its published instructions. Reuse the business's existing approved definition file. If it is missing, establish it with Finance through that skill; never infer approval or overwrite another client's rules.

Build a reusable measurement specification and a source-backed metric table. Keep revenue accounting separate from attribution. Leave monthly client narrative, budget pacing, cohort LTV and media execution to their respective workflows.

## Agree the measurement basis

Read the Finance-approved revenue definition and any existing `marketing` section of `finance/revenue_definitions.json`. Confirm the period, entity, markets, channels, timezone, reporting currency and revenue version.

Resolve missing choices with Marketing and Finance:
- Which sales measure and business scope belong in each numerator?
- Which spend sources and costs belong in each denominator?
- Which purchase/conversion event, attribution model, click/view windows and date basis apply per platform?
- What counts as a new customer, at what identity grain, over what history?
- Which periods are mature enough to compare, and when do late conversions/refunds restate results?

Record the agreed specification, approvers, effective date and source settings evidence. Reuse existing definitions rather than creating a conflicting contract. Retain established library marketing keys and document additional detail separately where consumer support is unverified.

## Build trusted inputs

Use connected ad platforms, approved warehouse models, financial cost records or supplied exports. Save the query/script, mapping and parameters.

**Revenue:** use the approved deduplicated sales report for business totals. Record its reconciliation status. Platform conversion values are separate claimed revenue and may use different refund, tax and shipping treatments.

**Spend:** define a cost taxonomy and one authoritative source per cost category: media, agency, creative, influencer, affiliate or other approved marketing costs. Specify cash versus accrual, currency and allocation period. Do not count both an ad-platform cost and the invoice paying for it. Deduplicate overlapping campaign/account totals. Missing networks/costs are unknown, never zero.

**Customers:** count distinct identities whose first qualifying purchase falls in the period under the approved rule. Do not substitute orders, platform conversions or first purchases visible in a short export. Record historical coverage, identity matching, guest/merged customer handling, cancelled/refunded first purchases and marketplace visibility. If identities cannot be joined across channels, report a scoped denominator or withhold enterprise-wide CAC; do not assume every marketplace buyer is new.

**Attribution:** capture actual account/report settings or request parameters, including click and view windows, model, conversion action, conversion-versus-interaction date, timezone and extraction time. A label typed into a configuration file does not prove the export used it. Mark unverified settings and avoid direct comparisons where settings differ.

## Calculate explicit formulas

| Metric | Formula | Required label |
|---|---|---|
| Platform ROAS | That platform's attributed purchase value ÷ that platform's ad spend | Platform, conversion event, model/window, date basis and value treatment |
| First-party attributed ROAS, if available | Deduplicated revenue credited by the agreed first-party model ÷ matched ad spend | Model, coverage, identity gaps and allocation policy |
| MER | Approved business net sales ÷ approved total marketing spend | Included sales channels and cost categories; display as x |
| Paid-media efficiency, if only media is available | Approved business net sales ÷ paid-media spend | Media-only; do not label fully loaded MER |
| New-customer CAC | Approved acquisition/marketing cost pool ÷ distinct qualifying new customers | Exact cost pool, customer scope and history coverage |

Never sum Meta, Google and TikTok claimed revenue and call it total sales. Do not average daily/channel ratios: aggregate aligned numerators and denominators, then divide. First-party attribution must conserve the eligible revenue total, including unattributed revenue, with each order allocated no more than once in total.

If the business calls spend ÷ revenue “MER,” preserve the original label in the mapping but display the formula prominently and label the normalized output **marketing spend as % of revenue**. Do not silently invert it.

Use N/A with a reason for missing or zero denominators. Flag negative revenue periods and explain their effect. A new-customer count of zero does not imply zero CAC. With incomplete inputs, show supported components and label affected ratios unavailable or explicitly partial.

## Interpret with care

Platform ROAS measures attributed performance, not incremental causal lift or profit. MER includes organic and returning-customer revenue; it is not channel attribution. Period CAC is not automatically cohort CAC or CAC payback.

Align scope and maturity before period comparisons. Split or flag changes in attribution settings, revenue rules, FX policy and cost coverage. Do not infer that a campaign caused a revenue change from correlation alone.

## Deliver and verify

Return the measurement specification and a table with metric, value, numerator, denominator, period/scope, source references, definition version, attribution settings, data-through date and completeness status. Summarize measurement gaps with owners and next checks. Reuse these outputs in Client Report Builder rather than duplicating its presentation workflow.

Synthetic check: business net sales 100,000; Meta spend 20,000 and claimed revenue 70,000; Google spend 10,000 and claimed revenue 50,000; other approved marketing cost 10,000; 500 qualified new customers. Platform ROAS is 3.50x and 5.00x; media-only efficiency is 3.33x; fully loaded MER is 2.50x; CAC using that same approved 40,000 cost pool is 80. Claimed revenue of 120,000 is not sales. These figures are illustrative, never a benchmark.

Do not change budgets, campaigns, attribution settings or customer records.
