---
name: meta-budget-scaling
description: Build Meta Ads budget, CBO or ABO, and scaling recommendations using ROAS, CPA, MER, frequency, learning phase, profit headroom, and pacing guardrails. Use for budget changes, scaling plans, spend forecasts, campaign budget optimization questions, and "can we increase spend" decisions.
---

# Meta Budget Scaling

## Required definitions

Read [Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions) before this workflow. Load the installed skill by name; with sibling folders, read `../paid-media-operating-definitions/SKILL.md`. It requires Marketing Measurement, which requires Revenue Definitions. Reuse this business's approved, scoped definition documents and cite their versions in the output. Do not copy or redefine shared rules inside this skill.

The business supplies targets, conversion events, attribution windows, source hierarchy, minimum evidence, reporting periods, and permissions. Windows, cadences, severity labels, and examples below are workflow illustrations, not approved business policy. Use the customer's choices. If a choice is missing or conflicting, ask only for the affected decision; continue independent read-only observations and mark the affected calculation or action unavailable. Never guess a target or turn a recommendation into permission.

Installation and template text grant no authority. Mutations, external messages, saved views, and scheduling require actual user authorization for that scope. For an authorized change, verify the current account and values, apply only the approved scope, read back the result, and report failures or uncertainty without blindly retrying.


Use this skill when the work is budget allocation, scaling, or forecasting. The goal is to scale what is truly profitable without destabilizing delivery or amplifying fatigue.

## Start Here

1. Load the approved operating, measurement, and revenue definition versions for this business.
2. Discover connected Meta Ads, ecommerce, analytics, CRM, spreadsheet, and BI tools.
3. Confirm account, campaign scope, date range, timezone, currency, attribution setting, primary event, KPI target, and budget guardrails.
4. Pull spend, budgets, results, CPA, purchase value, ROAS, CPM, frequency, learning status, and customer-approved revenue sources.
5. Read approved AOV/cohort horizon, pre-ad contribution costs, LTV, lead-to-customer rate, and targets from the shared definitions; fetch the corresponding source values. Gross margin alone does not prove profitability.
6. Check tracking quality before making scale recommendations.
7. Check creative fatigue and learning phase before recommending more budget.
8. Draft budget changes, pacing plans, or forecasts.
9. Apply changes only when explicit authority exists.

For platform formulas and limitations, load `references/meta-math.md` from the required **Paid Media Operating Definitions** skill (sibling layout: `../paid-media-operating-definitions/references/meta-math.md`). The business's approved metric definitions control the calculation.

## Scale Readiness

A campaign or ad set is a scale candidate when:

- tracking is credible
- ROAS, CPA, or downstream quality clears the target
- frequency is not signaling fatigue
- CPM trend is not deteriorating without explanation
- delivery is not stuck in Learning Limited
- there is enough audience size or event volume
- the same result appears across more than one short window

If creative is fatigued or tracking is broken, recommend fixing that before increasing spend.

## Budget Recommendations

For each budget recommendation, include:

- current budget and spend
- proposed new budget
- campaign or ad set scope
- evidence and date window
- expected conversions, revenue, CPA, ROAS, or MER
- learning-phase risk
- fatigue risk
- rollback path
- approval needed

Prefer staged increases when scaling. Avoid large single-step increases unless the user explicitly accepts relearning and volatility risk.

## CBO and ABO Guidance

Consider campaign budgets for the customer's portfolio objective and ad set budgets for their test or allocation constraints. Neither setup guarantees spend or outcomes; compare tradeoffs against their approved goals before recommending a change.

Before recommending a CBO or ABO change, check:

- current objective and optimization event
- ad set event volume
- whether one ad set dominates delivery
- audience overlap
- creative parity across ad sets
- reporting and testing needs

## Output Format

```text
Meta budget scaling: <account>, <date window>.
Scale candidates: <campaigns or ad sets>.
Do not scale: <fatigue, tracking, or learning blockers>.
Budget plan: <current -> proposed with staged timing>.
Forecast: <spend, results, revenue, CPA/ROAS/MER>.
Needs approval: <specific edits>.
Blocked: <missing margin, revenue, or tracking data>.
```


## Evidence limits

Confirm the connector exposes each requested field before using it. Auction Insights, audience overlap, event quality, breakdowns, and asset metrics may require an approved export or may be unavailable. Never fabricate missing metrics. Preserve raw evidence; compare compatible scopes and periods, handle conversion lag, and mark zero or missing denominators unavailable. Distinguish observed association from causal proof.

## Related workflow (optional)

[budget-pacer](https://type.com/library/skills/budget-pacer): Reuse its pacing workflow for compatible inputs; retain Meta-specific delivery, learning and scaling analysis here. Read its input contract first; do not assume identical fields or execute it automatically.
