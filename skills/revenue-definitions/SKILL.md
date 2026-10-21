---
name: revenue-definitions
description: Define or review a business's finance-owned revenue rules before reporting sales. Use when gross and net sales disagree, when setting up revenue reporting, or when changing discounts, refunds, tax, shipping, exclusions, sale dates, timezone, currency, or channel ownership.
---

# Revenue Definitions

Produce a versioned revenue definition that Finance can approve and every report can reuse. This skill defines policy; Revenue Reporting applies it and Revenue Reconciliation validates the result.

## Start with the business's existing rules

Read existing Finance policies, metric definitions, relevant source reports and any `finance/revenue_definitions.json`. Preserve approved rules. Resolve conflicts with the named Finance owner; never choose the most convenient source's definition.

Ask for missing business decisions in small groups. Discover available reports and fields yourself. Do not require Shopify, Amazon or wholesale if the business does not use them. Support connected sources or supplied exports.

Confirm the entity, channels, markets and intended metric: operational net sales, recognized accounting revenue, cash collected or bookings. Keep these separate. For subscriptions, deferred revenue or complex recognition, use the Finance-approved accounting policy; order totals and MRR are not substitutes.

## Define the contract

Record each rule, its owner, supporting source and approval status:

- **Gross product sales:** quantity × pre-discount item price for eligible sales, with an explicit treatment of tax-inclusive prices, returns, edits and cancelled items.
- **Discounts:** line and order discounts, allocation to lines and treatment of promotional credits. Prevent double subtraction when a source field is already net.
- **Refunds and returns:** separate actual refunds, credit notes, physical returns and modeled return reserves. Define product, tax and shipping refund components; partial refunds; exchanges; chargebacks; and adjustments for orders already excluded.
- **Tax and shipping:** state inclusion separately. Distinguish shipping charged to buyers from shipping expense. If the metric includes sales tax, name that basis explicitly.
- **Gift cards and credits:** distinguish issuance from redemption so both are not treated as new revenue. Document store-credit treatment.
- **Exclusions:** exact status/tag/filter rules for test, internal, cancelled, voided, unpaid, replacement, free and other excluded orders. Do not assume every pending order should be excluded.
- **Time:** IANA timezone, fiscal/calendar period, sale event per channel, refund event date versus original-sale restatement, late-arrival lookback and closed-period correction policy. Preserve original timestamps. Use half-open reporting intervals: start inclusive, end exclusive.
- **Currency:** reporting currency, original currencies, approved FX provider/file, rate date, quote direction, precision, rounding and unavailable-rate policy.
- **Channels:** source of truth, account/store/market, report and export, record grain, event identity, authoritative copy and cross-system mapping. Fulfillment method alone does not determine sales channel.
- **Wholesale:** approved recognition event, credit-note treatment and evidence. Retailer sell-through is a separate metric; never add it to the supplier's wholesale revenue. Do not equate purchase orders, invoices and cash receipts without policy support.
- **Controls:** freshness/completeness expectations, independent control reports, tolerances and exact threshold logic, reviewers and exception handling.

Show the proposed bridge:
gross product sales − discounts − product refunds + included net shipping + included net tax + separately named approved adjustments = the named sales metric.
Define the signs and component composition so discounts or refund components are deducted exactly once.

## Save one authoritative definition

Use `finance/revenue_definitions.json` where the business already uses the Type library convention. Preserve the existing keys:
`version`, `owner`, `reporting_timezone`, `reporting_currency`, `fx`, `net_sales`, `refunds`, `sale_date_basis`, `excluded_orders`, `channels`, `cross_channel_duplicates`, `marketing` and `checks`.

For a new business, build this file from the decisions above. Explain fields in an accompanying approval record, including effective date, metric name, approval evidence, policy decisions and consumer compatibility. Keep unapproved choices visibly unresolved; a plausible value is not approval. Do not copy another business's thresholds, account IDs, currencies or attribution windows.

When an existing consumer cannot express an approved policy, flag the unsupported rule and required adapter change. Do not silently narrow the business policy to fit a script. In particular, the current Daily Revenue Check supports only its documented channel and date bases.

Finance approves revenue rules; Marketing and Finance jointly approve the marketing definitions. Record approval only from actual evidence.

## Validate before handoff

Walk through synthetic examples with Finance:

1. Gross product sales 100, discount 10, product refund 20, tax/shipping excluded → net product sales 70. A source already reporting 90 after discount must not lose another 10.
2. A refund issued next month changes next month's sales on refund-date basis, or identifies the prior-period restatement on original-sale basis.
3. A Shopify sale fulfilled using Amazon MCF is still a Shopify sale unless evidence identifies a separate mirrored sales record.
4. A wholesale invoice and a retailer's sell-through report never produce two sales for the supplier.

Use the approved currency and policies when adapting these examples.

## Deliver

Return the proposed/approved contract, a plain-language definition table, worked examples, unresolved decisions with owners, and an impact summary for any changed rule. Include version, effective date and approval status. Do not call reports using a draft policy Finance-approved.
