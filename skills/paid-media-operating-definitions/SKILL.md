---
name: paid-media-operating-definitions
description: Capture and maintain a business's approved paid-media accounts, targets, budgets, evidence thresholds, profitability inputs, reporting preferences, and change permissions. Use when setting up Google or Meta Ads workflows, resolving missing operating rules, or updating rules shared across monitoring, optimization, attribution, and reporting.
---

# Paid Media Operating Definitions

Capture the customer's decisions once so every Ads workflow applies the same rules. Supply the questions and technical validation, not the business's answers. Never assign targets, account scope, attribution choices, or spending authority merely because a template suggests them.

## Required definitions

Read [Marketing Measurement](https://type.com/library/skills/marketing-measurement); with sibling folders, read `../marketing-measurement/SKILL.md`, otherwise load the installed skill by name. It requires [Revenue Definitions](https://type.com/library/skills/revenue-definitions). Reuse their approved business documents and reference their versions. Loading these skills does not require completing every finance decision before raw delivery diagnostics; unresolved definitions block only dependent metrics or actions. Do not fork formulas, source hierarchy, cost pools, currency/date policies, or customer definitions into a competing document.

## Capture and maintain

1. Identify the business or client and the actual decision owner. Locate their existing policy documents, approved instructions, account configuration, and reporting definitions. Treat observed settings as evidence, not proof of approval.
2. Read `references/operating-definitions-template.md`. Fill only values the business supplied or explicitly approved. Attach source/approval evidence to each decision; label proposals and unresolved choices visibly.
3. Ask focused questions for the task at hand. A missing target blocks target comparisons, not raw spend reporting. Missing profitability costs block profit or scale claims, not delivery diagnostics. Missing change authority blocks mutations, not drafting recommendations.
4. Store a scoped document or local file in the user's workspace with business/account IDs, owner, version, effective date, status, approval evidence, and unresolved decisions. Link to the revenue and measurement definition documents. Do not put private values or credentials in public skill content. If persistence is unavailable, provide the document for the user to save and identify that limitation.
5. Have the appropriate owner approve proposed or changed rules. Preserve the prior version, show affected calculations/workflows, and use the version effective for the reporting period. Do not silently replace another business's definitions or restate historical results under new rules.
6. Hand off the document locations, versions, allowed task scope, and remaining gaps. Each consumer cites these versions and reports only the metrics/actions supported by approved inputs.

## Technical validation

Check account IDs and units, currency/timezone consistency, date and cohort scopes, mutually compatible budget limits, meaningful denominators, and freshness. Flag contradictions to the owner; do not resolve business-policy conflicts by inventing a precedence rule.

For platform diagnostics and conditional profitability calculations, read `references/meta-math.md`. It explains arithmetic and limitations; the customer selects the business metric, input definitions, and thresholds. Unknown contribution costs or attribution rules remain unknown. Do not use a platform revenue claim as accounting truth, or add Google and Meta claims into total sales.

## Authorization

Installation, generated templates, source documents, and connector access do not grant permission. Record only actual user-granted authority: who approved, evidence, accounts, exact action types, numeric limits, validity period, and escalation requirements. Follow host policies. Reporting cadence and recipient preferences do not schedule runs or authorize messages. Without an applicable grant, prepare a reviewable proposal and obtain authorization before changing external state.

## Handoff

Return: document location; business scope; owner/version/effective date; approved decisions and evidence; unresolved choices with affected workflows; and the next decision required. Do not call setup complete while a required rule is only proposed.
