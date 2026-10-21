# Data contract — saas-metrics-pack

One UTF-8 CSV with a header row: **one row per subscription**, including
subscriptions that have already ended. Column order does not matter; names
must match exactly (case-sensitive). A UTF-8 BOM is tolerated. Fully blank
lines are skipped; anything else malformed is a hard error naming the line and
column.

## Columns

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `customer_id` | yes | text | Stable identifier. Every row for one customer must use the same value — this is what groups plan changes into a single logo. |
| `customer_name` | no | text | Display only. Falls back to `customer_id`. |
| `subscription_id` | yes | text | Unique across the whole file. Duplicates are an error. |
| `plan` | no | text | Display only; carried for your own filtering. |
| `mrr` | yes | decimal >= 0 | **Monthly** recurring revenue for this subscription, in one reporting currency. Negative values are rejected. |
| `start_date` | yes | `YYYY-MM-DD` | First day of service. |
| `end_date` | no | `YYYY-MM-DD` | **First day of non-service** (exclusive). Blank means still active. Must be after `start_date`. |

`mrr` may carry thousands separators and a `$`. More than 2 decimal places is
allowed (annual/12 gives thirds of a cent) and is kept at full precision; only
the display is rounded.

## Sign and unit conventions

- `mrr` is **always positive**. Discounts, credits, and partial waivers must be
  netted into the subscription's own MRR before export; there is no separate
  discount row concept and a negative row is rejected rather than guessed at.
- `mrr` is **normalised to one month**. Annual contracts divide by 12, quarterly
  by 3. The script does not do this for you.
- One currency per file. Convert before exporting.

## Activity rule

A subscription is active on date `D` when:

```
start_date <= D  and  (end_date is blank or end_date > D)
```

This matches Stripe's `ended_at` semantics: a subscription with
`end_date = 2026-08-31` is **not** active on 31 August, so with
`--as-of 2026-08-31` that customer churns inside the period.

## Representing changes

| Event | How to express it |
| --- | --- |
| Upgrade / downgrade | End the old subscription row and start a new row on the same date. |
| Add-on purchased | A second row for the same `customer_id`, running alongside. |
| Cancellation | Set `end_date`. Keep the row in the file. |
| Reactivation | Keep the old ended row **and** add the new row. Without the old row the customer is classified as new. |
| Pause | Model as an end plus a later start. There is no pause state. |

Rows that mutate in place — one subscription whose `mrr` was edited when the
plan changed — make the customer look flat. Export history, not the current
state of the billing table.

## Upstream sources

| Field | Stripe | Chargebee / Recurly / Paddle | GL (QuickBooks / Xero / NetSuite) |
| --- | --- | --- | --- |
| `customer_id` | `customer` | customer id | customer record id |
| `subscription_id` | `id` | subscription id | contract or recurring invoice id |
| `mrr` | plan amount normalised to monthly, minus discount | plan amount normalised | recurring invoice line, normalised |
| `start_date` | `start_date` / `current_period_start` of the first period | activation date | contract start |
| `end_date` | `ended_at`, else `canceled_at` | cancellation date | contract end |

Decide explicitly whether usage overage, one-off fees, taxes, and $0 trials are
in scope, apply that filter during export, and state it alongside the output.

## Control totals and other arguments

| Argument | Meaning |
| --- | --- |
| `--period-start` | Opening measurement date. Required. |
| `--as-of` | Closing measurement date. Required; must be after `--period-start`. |
| `--expected-closing-mrr` | Closing MRR per the billing system. A mismatch fails the run. |
| `--sales-marketing-spend` | S&M cost for the acquisition period, from the GL, same currency. |
| `--gross-margin-pct` | Gross margin percent used for CAC payback. Default 100. |

## Rounding

All arithmetic uses `decimal.Decimal` at full precision. Display rounds to 2 dp
with `ROUND_HALF_UP`; percentages to 1 dp; ratios and payback months to 2 dp.
When rounding makes the printed waterfall column not add up, the report prints
an explicit note — the tie check itself never uses rounded values.
