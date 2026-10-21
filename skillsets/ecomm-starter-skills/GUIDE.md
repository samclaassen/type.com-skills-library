---
name: ecomm-starter-skills
description: A collection of four ordinary skills for establishing revenue definitions, reporting sales, measuring marketing and reconciling revenue against source reports.
---

# Ecomm Starter Skills

Use these four skills together with your own business definitions and data.
Each remains an ordinary, independently usable skill.

## Included skills

1. [Revenue Definitions](https://type.com/library/skills/revenue-definitions):
   establish finance-approved rules for gross and net sales, discounts, refunds,
   tax, shipping, excluded orders, dates, timezone and currency.
2. [Revenue Reporting](https://type.com/library/skills/revenue-reporting):
   combine Shopify, Amazon and wholesale using an agreed source of truth and
   saved query or script, without counting an order twice or adding retailer
   sell-through to wholesale revenue.
3. [Marketing Measurement](https://type.com/library/skills/marketing-measurement):
   calculate attributed ROAS, blended marketing efficiency and new-customer CAC
   using agreed attribution windows, spend scope and customer definitions.
4. [Revenue Reconciliation](https://type.com/library/skills/revenue-reconciliation):
   check feed freshness and completeness, duplicates, late refunds and
   differences against source totals before a report is published.

Revenue Definitions is shared by the other three skills and is installed once.

## Set up for your business

Identify the finance owner and find the business's existing approved revenue
policy. Use Revenue Definitions to record missing decisions and obtain approval.
Record the policy version and reuse it across all reports. Never replace an
approved policy with sample defaults.

Use Revenue Reporting to map each channel's authoritative source, extraction
window, order identity and query/script version. Distinguish Amazon marketplace
sales from Shopify orders fulfilled by Amazon MCF. Count a sale once in its
agreed channel. Keep wholesale revenue separate from retailer sell-through.

Use Marketing Measurement to agree attribution windows, new-customer identity
and spend scope. Match periods and currency, keep platform-attributed claims
separate, and never sum Meta, Google and TikTok claimed revenue as total sales.
Report unavailable metrics when their required inputs are missing.

Use Revenue Reconciliation to check the resulting revenue report against source
reports and apply agreed freshness thresholds, tolerances and restatement rules.
Hold publication for unexplained differences or incomplete data under that policy.

## A reporting routine

Read the business's approved definitions first. Build the revenue report, then
calculate marketing metrics using the appropriate verified revenue and spend.
Reconcile revenue before presenting the final report as ready to publish.
If reconciliation changes revenue, recompute affected marketing metrics before
finalizing them. Include the policy version, source timestamps, calculation
version and unresolved exceptions.

Prepare a report or Slack message as a draft. Installing this collection does
not create a schedule or authorize sending messages or changing source data.

## Installation and use

In Type, add the skillset to install the four member skills together. For Claude
Code or Codex, download the bundle and follow its README. Keep each skill in its
own sibling folder with all supporting files. Read its SKILL.md when applying
that workflow; this collection guide is orientation material.

Start with: "Use these four skills to prepare a checked revenue and marketing
report for my business. Find our approved definitions, identify the source data,
and list missing decisions or inputs before calculating."

No Type-specific runtime is required. Connections and business data must be
supplied through the user's agent or supported exports. Sample data is only an
example, never evidence about the user's business.
