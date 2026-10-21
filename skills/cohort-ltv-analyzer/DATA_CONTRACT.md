# Data contract — cohort-ltv-analyzer

Two input files, both local, plus finance's optional revenue definitions file.
No network access, no credentials in the script.

## File 1: order export (`--orders`, required)

- Format: CSV, UTF-8, header row, one row per **order**.
- Not one row per line item. If the export is line-item level, aggregate to
  order level first (sum revenue and COGS, keep one shipping and fee value).
- Without a definitions file: exclude cancelled and test orders yourself. With
  one: keep them and supply `tags` / `financial_status` / `cancelled` so they
  are excluded and reported under finance's rules. Keep refunded orders and
  record the refund in `refund_amount`.
- Single currency, unless the definitions file sets `reporting_currency` and
  the file has a `currency` column (see below).
- Upstream systems: Shopify order export, a warehouse `orders` table, or an
  ERP sales export; cost columns often come from a 3PL or ERP instead.

### Required columns

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `order_id` | string | — | Unique across the file. A duplicate exits 2 naming the row. |
| `customer_id` | string | — | **Must be stable across a customer's orders.** Per-order guest ids silently destroy the analysis. |
| `order_date` | date or timestamp | `YYYY-MM-DD`, or ISO 8601 with offset, e.g. `2026-02-01T03:30:00Z`, `2026-02-01 03:30:00 -0500` | Order placed date, not ship date. Without a reporting timezone only the first 10 characters are read. With one, a timestamp is converted to that timezone before its month is taken; a plain date is taken as already local; a timestamp without an offset is an error. |
| `net_revenue` | money | decimal, e.g. `64.90` | Product revenue **after discounts**, **excluding** tax and shipping charged. Must be >= 0; record refunds in `refund_amount`, not as negative revenue. |

### Optional columns

Absent columns are treated as 0 or fall back to a flag, as noted.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `cogs` | money | decimal | Landed product cost for the order: unit cost plus inbound freight and duty. If absent, `--cogs-pct` is **required** and COGS is computed as that percent of `net_revenue`. |
| `shipping_charged` | money | decimal | What the customer paid for shipping. Added to margin. |
| `shipping_cost` | money | decimal | What the carrier charged you. Subtracted from margin. |
| `payment_fee` | money | decimal | Actual processor fee. When present it overrides `--payment-fee-pct` / `--payment-fee-fixed`. |
| `refund_amount` | money | decimal, positive | Amount refunded against this order, in the currency of `net_revenue`. Positive numbers only. Netted against the original order date unless finance's `refunds.date_basis` is `refund_date`. |
| `discount` | money | decimal | Informational unless finance sets `net_sales.subtract_discounts: false`, in which case it is added back to revenue (and is then required). Never affects margin. |
| `refund_date` | date or timestamp | as `order_date` | Used only when finance sets `refunds.date_basis: refund_date`; then required for every row with a refund. Must not be before `order_date`. |
| `currency` | string | ISO code, e.g. `CAD` | Used only when the definitions set `reporting_currency`; then required on every row. Without a definitions file this column is ignored, as before. |
| `tags` | string | comma-separated, e.g. `"test, vip"` | Matched case-insensitively against `excluded_orders.tags`. |
| `financial_status` | string | e.g. `paid`, `voided` | Matched case-insensitively against `excluded_orders.financial_statuses`. |
| `cancelled` | boolean | `true/false/yes/no/1/0`/blank | Excluded when `excluded_orders.cancelled` is true. Any other value is an error. |

Any other column in the file is ignored.

### Example

```csv
order_id,customer_id,order_date,net_revenue,discount,cogs,shipping_charged,shipping_cost,payment_fee,refund_amount
KC-100039,KC-202601-0030,2026-01-01,74.97,18.74,39.00,0.00,8.77,2.47,0.00
KC-100045,KC-202601-0035,2026-01-01,44.64,11.16,23.26,6.95,8.19,1.80,0.00
```

## File 2: acquisition spend by month (`--spend`, optional)

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `cohort_month` | string | `YYYY-MM` | Calendar month the spend ran in. |
| `marketing_spend` | money | decimal | Acquisition spend for that month in the same currency as revenue. |

CAC for a cohort is `marketing_spend / new customers first ordering that
month`. Months absent from the file get no CAC and are reported as such rather
than assumed zero.

Sources: Meta Ads and Google Ads spend for the month, plus any other spend the
brand considers acquisition (affiliate payouts, influencer fees, agency
retainer share). Decide once whether retention email and SMS spend counts and
state it in the writeup — the script does not know.

```csv
cohort_month,marketing_spend
2026-01,1260.00
2026-02,1530.00
```

Alternatively pass a single blended `--cac 42.00`, applied to every cohort.

## Finance revenue definitions (`--definitions`, optional)

Format and ownership: `REVENUE_DEFINITIONS.md` (a verbatim copy of the shared
contract). Default path `finance/revenue_definitions.json` in the working
folder; `--definitions PATH` overrides it, and an explicit path that does not
exist is an error. `version` is required and printed at the top of the report.
Invalid JSON, an unknown timezone, a non-boolean flag, or an unsupported
`net_sales.starts_from` or `refunds.date_basis` stops the run naming the key.
Any key finance leaves out falls back to the skill default below, and the
report lists which ones did.

When no definitions file is found, the output's first lines say so and list
these defaults, and every figure is identical to earlier versions.

| definition | how this skill applies it | default when absent |
| --- | --- | --- |
| `reporting_timezone` | Converts offset timestamps in `order_date` and `refund_date` before assigning a month. | none; first 10 characters read as a date |
| `reporting_currency`, `fx.rates_file` | Rows whose `currency` differs are converted, every money column, at the rate for the order's local date. No `currency` column: amounts are taken as the reporting currency and the report says so. | one currency assumed |
| `net_sales.starts_from` | Must be `gross_product_sales`; `net_revenue` is product sales after discounts. | — |
| `net_sales.subtract_discounts` | `false` adds the `discount` column back to revenue. | true (`net_revenue` is after discounts) |
| `net_sales.subtract_refunds` | `false` leaves refunds out of revenue. They still reduce contribution margin. | true |
| `net_sales.include_shipping_charged` | `true` adds `shipping_charged` to revenue. | false |
| `net_sales.include_tax` | **Cannot apply**: `net_revenue` excludes tax and there is no tax column. If true, the report prints NOT APPLIED; revenue still excludes tax. | false |
| `net_sales.include_gift_card_sales` | **Cannot apply**: gift-card sales are not identifiable in this export. `net_revenue` must already follow finance's flag; the report says so. | false |
| `refunds.date_basis` | `refund_date` moves each refund (its revenue and its margin effect, `-refund_amount + recovered COGS`) to the month-since-first-order bucket of `refund_date`. `order_date` nets it on the order. | order_date |
| `refunds.late_refund_days` | Not used. | — |
| `sale_date_basis` | Printed; `order_date` must be that date. | order placed |
| `excluded_orders.cancelled`, `.financial_statuses`, `.tags`, `.zero_value` | Matching orders are removed before cohorts are built and reported under EXCLUDED ORDERS with count and `net_revenue` per rule. First match wins, in the order cancelled, financial_status, tag, zero value. Zero value means `net_revenue + shipping_charged = 0`. A rule whose column is missing is reported as not checked. | nothing excluded |
| `channels`, `cross_channel_duplicates`, `marketing`, `checks` | Not used; listed in the report. | — |

"Revenue" above means the figures labelled revenue: rev/cust, AOV and the
cumulative revenue triangle. Contribution margin never changes with the
net sales flags.

### FX rates file (named by `fx.rates_file`)

Path relative to the working folder. Loaded only when a row needs converting.

| column | notes |
| --- | --- |
| `currency` | ISO code of the row currency. |
| `rate_to_reporting` | Reporting-currency units per one unit of `currency`, > 0. |
| `date` | Optional `YYYY-MM-DD`. If present, rates are daily and the order's local date must have a row. |

A missing rate stops the run naming the order row and the missing date; a rate
is never guessed or carried forward.

## Margin definition

```
contribution_margin =
      net_revenue
    + shipping_charged
    - refund_amount
    - cogs
    + cogs * (refund_amount / net_revenue) * restock_recovery_pct
    - shipping_cost
    - payment_fee
    - fulfillment_cost
```

Defaults when the corresponding column is absent:

| input | default | flag |
| --- | --- | --- |
| payment fee | 2.9% of (net revenue + shipping charged) + 0.30 per order | `--payment-fee-pct`, `--payment-fee-fixed` |
| fulfillment (pick, pack, box) | 0.00 per order | `--fulfillment-cost` |
| COGS recovered on a refund | 0% (goods assumed unsellable) | `--restock-recovery-pct` |
| COGS | none — `--cogs-pct` is required if the column is missing | `--cogs-pct` |

Marketing spend is **not** subtracted from contribution margin; it is compared
against it as CAC.

## Money handling

All monetary values are parsed and accumulated with `decimal.Decimal` and
quantized to cents only at print time. No float arithmetic on currency.
Parentheses (`(12.50)`) and leading `$` are tolerated on input.
