# Data contract: daily-revenue-check

All inputs are local CSV (UTF-8, header row) or JSON files. The script is
Python 3.11+ standard library only, makes no network calls and reads no clock.

## Conventions for every file

- **Timestamps**: ISO 8601 **with a UTC offset**, e.g.
  `2026-09-22T14:05:00-04:00` or `2026-09-23T03:30:00Z`. A timestamp without
  an offset (including a bare date where a timestamp is expected) exits 2:
  a naive time cannot be placed on a reporting day. The script converts every
  timestamp to `reporting_timezone` with `zoneinfo` before assigning a day.
- **Dates** (`date` columns): `YYYY-MM-DD`, meaning a day in the reporting
  timezone.
- **Money**: plain decimals (`1234.50`; thousands commas are tolerated). Read
  with `decimal.Decimal`, never float; rounded to cents only when printed.
- **Currency**: three-letter upper-case ISO 4217 code.
- **Sign convention** in channel exports: every amount is **zero or positive**.
  `record_type` decides the sign: a `sale` adds, a `refund` subtracts. A
  negative amount exits 2. The only signed column is `source_totals.net_sales`
  (a day can net negative).
- Unknown extra columns are ignored.

## Finance definitions (`finance/revenue_definitions.json`, required)

Format in `REVENUE_DEFINITIONS.md`. Keys this skill requires (a missing key
exits 2 and names it):

`version`, `owner`, `reporting_timezone`, `reporting_currency`, `fx.source`,
`fx.rates_file`, `net_sales.starts_from`, `net_sales.subtract_discounts`,
`net_sales.subtract_refunds`, `net_sales.include_shipping_charged`,
`net_sales.include_tax`, `net_sales.include_gift_card_sales`,
`refunds.date_basis`, `refunds.late_refund_days`, `sale_date_basis`,
`excluded_orders.tags`, `excluded_orders.financial_statuses`,
`excluded_orders.cancelled`, `excluded_orders.zero_value`, `channels`, and for
each configured channel `channels.<name>.export`, `.dedupe_key`,
`.source_of_truth`; for wholesale also `channels.wholesale.revenue_event`
(`invoice`) and `channels.wholesale.exclude_retailer_sell_through` (must be
`true`); `cross_channel_duplicates` (list; each item needs `shopify_tag` and
`description`); `checks.max_feed_staleness_hours`,
`checks.reconciliation_tolerance_pct`, `checks.reconciliation_tolerance_abs`.

Only when `--spend` is given: `marketing.mer` (must be
`net_sales / total_marketing_spend`), and
`marketing.attribution_windows.<platform>` for any platform with
`platform_claimed_revenue`.

## Channel exports (required, one per configured channel)

File name comes from `channels.<name>.export`, relative to `--workdir`. The
three channels share one layout; only the id column differs.

| column | required | notes |
| --- | --- | --- |
| *dedupe key* | yes | Named by `channels.<name>.dedupe_key`: `order_id` (Shopify), `amazon_order_id` (Amazon), `invoice_id` (wholesale). On refund rows it holds the **original sale's** id. |
| `record_type` | yes | `sale` (order or invoice) or `refund` (refund or credit note). |
| `refund_id` | yes (column) | Required on refund rows; blank on sales. Refunds dedupe on (dedupe key, refund_id). |
| `created_at` | yes | Sale timestamp: order created (Shopify), purchase date (Amazon), invoice issued (wholesale). On refund rows, the original sale's timestamp; if that sale is also in the file the two must agree. |
| `refunded_at` | yes (column) | Refund timestamp on refund rows; must be blank on sales and not before `created_at`. |
| `currency` | yes | Currency of the amounts on this row. |
| `product_amount` | yes | Sale: gross product sales **before** discounts, **excluding** gift cards, shipping and tax. Refund: product value refunded, net of the discount given. |
| `discount_amount` | yes (column) | Sale: discounts on product. Must be 0/blank on refund rows. |
| `shipping_amount` | yes (column) | Shipping charged (sale) or refunded (refund). |
| `tax_amount` | yes (column) | Tax charged or refunded. |
| `gift_card_amount` | yes (column) | Gift cards **sold** (not redeemed) or refunded. |
| `tags` | yes (column) | Comma-separated, case-insensitive. Matched against `excluded_orders.tags` and, for Shopify, `cross_channel_duplicates[].shopify_tag`. |
| `financial_status` | yes (column) | Matched against `excluded_orders.financial_statuses`, e.g. `voided`. |
| `cancelled` | yes (column) | `true`/`false`/`yes`/`no`/`1`/`0`; blank = false. |
| `extracted_at` | yes | When the export was pulled. The latest value in the file is the feed's extract time. |
| `mirror_of` | Shopify, optional | For orders carrying a cross-channel tag: the Amazon order id or invoice id they copy. Verified against the other exports. |

Blank amount cells other than `product_amount` read as 0. Refund rows are
checked for exclusion and mirror rules using their sale's row when the sale is
in the file, otherwise using their own `tags`, `financial_status` and
`cancelled` (so repeat the original order's values on refund rows).

Rules applied: a sale's day is the reporting-timezone date of `created_at`. A
refund's day is the date of `refunded_at` (`refund_date` basis) or of
`created_at` (`order_date` basis). Zero value = product - discount + gift
cards + shipping + tax equals 0. Exclusion rules are checked in the order
tags, financial status, cancelled, zero value; the first match is the rule
reported. Excluded orders are never also treated as mirrors.

Example (Shopify):

```csv
record_type,order_id,refund_id,created_at,refunded_at,currency,product_amount,discount_amount,shipping_amount,tax_amount,gift_card_amount,tags,financial_status,cancelled,mirror_of,extracted_at
sale,FH-1002,,2026-09-22T05:10:00-04:00,,USD,60.00,6.00,5.00,3.24,0.00,,paid,false,,2026-09-23T06:00:00-04:00
refund,FH-0921,RF-5501,2026-09-08T14:12:00-04:00,2026-09-22T09:30:00-04:00,USD,70.00,0.00,0.00,4.20,0.00,,refunded,false,,2026-09-23T06:00:00-04:00
```

## `source_totals.csv` (required; default `<workdir>/source_totals.csv`)

| column | notes |
| --- | --- |
| `date` | Report day. Rows for other days are ignored, so one running file works. |
| `channel` | A configured channel name. |
| `currency` | Currency the report shows. Several rows per channel are allowed, one per currency (e.g. Amazon US and DE). Each is converted with the FX file at the report day's rate. |
| `net_sales` | The report's net-sales figure for the day on the same basis as the definitions (product sales less discounts and refunds, excluding shipping, tax and gift cards unless finance includes them). May be negative. |
| `scope` | `all_records`: the report includes everything in the export (it knows nothing of finance's exclusions). `after_exclusions`: the report already leaves out orders matching finance's exclusion rules (e.g. cancelled or voided). One scope per channel. |

Cross-channel mirrors are never removed from the source side: the platform
does not know about them. A channel with no row for the day FAILs
reconciliation.

## FX rates (`fx.rates_file`; needed only for non-reporting currencies)

| column | notes |
| --- | --- |
| `date` | Rate date. |
| `from_currency` | Currency being converted. |
| `to_currency` | Rows whose `to_currency` is not `reporting_currency` are ignored. |
| `rate` | Units of `to_currency` per 1 unit of `from_currency`. Two different rates for the same date and pair exit 2. |

Each amount uses the rate for the reporting-timezone date of its own event. A
missing rate (or a missing file when one is needed) is a HOLD, never a guess.

## `spend.csv` (optional, `--spend`)

| column | notes |
| --- | --- |
| `date` | Only rows for the report day are used. |
| `platform` | e.g. `meta`, `google`, `tiktok`. |
| `currency` | Converted with the FX file. |
| `spend` | Media spend for the day. Summed into total marketing spend. |
| `platform_claimed_revenue` | Optional. Shown per platform with its window from `marketing.attribution_windows`; never summed and never sales. |

MER = total net sales / total marketing spend.

## `sell_through.csv` (optional, `--sell-through`)

| column | notes |
| --- | --- |
| `date` | Only rows for the report day are shown. |
| `retailer` | Retailer name. |
| `units` | Whole units sold through. |
| `retail_sales` | Retailer's sales value at retail. |
| `currency` | As reported. |

Context only. It is **never** added to wholesale revenue.

## Exit codes

| code | meaning |
| --- | --- |
| 0 | PUBLISH: every check passed. |
| 1 | HOLD: at least one check failed; the output lists each reason and what to do. |
| 2 | Bad input: missing file, missing or malformed column, naive timestamp, negative amount, missing or unsupported definition key. The message on stderr names the file and row and column, or the key. |
