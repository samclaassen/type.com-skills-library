---
name: revenue-reporting
description: Build one reproducible revenue report across Shopify, Amazon, wholesale or a business's other sales channels using approved revenue definitions. Use for daily, weekly or monthly sales reporting, source mapping, channel consolidation, or preventing duplicate orders and retailer sell-through from inflating revenue.
---

# Revenue Reporting

## Shared definitions prerequisite

Read and apply the [Revenue Definitions skill](https://type.com/library/skills/revenue-definitions) before this workflow. If installed as sibling folders, read `../revenue-definitions/SKILL.md`; otherwise load the installed skill by name or obtain its published instructions. Reuse the business's existing approved definition file. If it is missing, establish it with Finance through that skill; never infer approval or overwrite another client's rules.

Assemble a traceable sales report using the business's approved definitions. Produce the calculation and evidence packet; Revenue Reconciliation determines whether the report is ready to publish.

## Load the reporting context

Read the approved revenue definition, preferably `finance/revenue_definitions.json`, and its version/effective date. Confirm entity, channels, accounts, markets, period, timezone, currency and as-of cutoff. If a material rule is unresolved, request the Finance decision and mark dependent results blocked.

Work from connected systems, a warehouse or supplied exports. Inspect actual schema, grain and report semantics before writing queries. Never invent table names or borrow another company's accounts, credentials or field mappings.

Create a source map with one authoritative sales source per channel and period. Record any cutover dates; do not combine a native source and its warehouse replica as separate revenue.

Examples to verify with Finance:
- Shopify order and refund records, with the agreed Finance report as control.
- Amazon sales and refund records for the correct seller/marketplace. Settlement deposits are cash after fees, not sales.
- Wholesale records at the approved invoice, shipment or other recognition event, plus applicable credit notes. Purchase orders and retailer sell-through stay outside the supplier revenue total.

## Extract and normalize

1. Save a query/script and its parameters in the business's approved workspace. Use read-only queries and deterministic transforms. Record its path/version/hash; do not claim reproducibility if the calculation cannot be retrieved.
2. Retrieve the complete half-open reporting window, all pages and all required accounts. Separately retrieve refund/update events affecting old orders. Apply the approved lookback/restatement policy.
3. Retain original snapshots where permitted. Build a manifest: source/account/report, request parameters, timezone, extract time, data-through watermark, pagination completion, row counts, date coverage and file/query references. A recent extraction does not prove fresh underlying data.
4. Normalize a documented event grain. Include entity, sales channel, account, market, original order ID, line ID where relevant, event type, stable event/refund ID, original-order link, event timestamp, business date, currency, financial components, status, source reference and extraction ID.
5. Keep sales, refunds, credit notes and edits distinguishable. Deduplicate a refund by its event identity, not its parent order: one order can have several legitimate refunds.
6. Apply source sign and minor-unit conventions explicitly; preserve exact decimal amounts through aggregation. Convert FX only once using the approved rate date and direction. Report missing rates as missing inputs.
7. Aggregate child records before joining to order-level amounts; check row counts and sums before/after joins. Never sum an order total once per line item.

## Count each sale once

Use a namespaced event key such as entity + source account + event type + native event ID, extended by line ID for line-grain data. Log exact replays removed. Quarantine conflicting copies instead of choosing the highest value or latest timestamp without a defined update rule.

Resolve cross-channel mirrors using external order/invoice identifiers and a documented ownership crosswalk. Preserve the retained and excluded record references and reason. Do not deduplicate unrelated orders because customer, date and amount happen to match.

Amazon MCF is a fulfillment service: retain a Shopify-origin sale fulfilled by Amazon unless an actual duplicate sales record is proved. Similarly, remove a wholesale Shopify mirror only when its authoritative wholesale counterpart is identified. Flag orphan or ambiguous mappings.

## Calculate and hand off

Apply the approved gross-to-net bridge per channel and original currency, then reporting currency. Show:
- Gross product sales, discounts, refunds/credits, included shipping/tax, named adjustments and final net sales.
- Excluded-order and duplicate counts/values, with overlapping exclusion rules assigned once or clearly labeled non-additive.
- Channel total, consolidated total and the arithmetic tie-out.
- Late refunds and prior-period changes, with affected dates and amounts.
- Comparison periods only when coverage, scope and definition versions are comparable.

Never substitute ad-platform attributed revenue, retailer sell-through, bank deposits or forecast revenue for sales. Do not add estimated refunds to actual refunds as if they were two realized events.

Deliver the report, normalized event ledger, exclusions/duplicate log, saved calculation, extraction manifest and independent control-report references. Label it **DRAFT — reconciliation pending** until a matching Revenue Reconciliation result is available. Missing channels are missing, not zero; show any partial subtotal with its exact coverage.

## Verify the calculation

Before a first real run or a mapping change, test synthetic cases: an order with two lines; a replayed order; two valid partial refunds; an excluded order with a refund; a verified cross-channel mirror; a Shopify sale fulfilled via MCF; a timezone boundary; and a missing FX rate. Require stable totals on rerun and no join fan-out.

Do not post to Slack or schedule distribution as part of this skill. Supply the report for the authorized publishing workflow.
