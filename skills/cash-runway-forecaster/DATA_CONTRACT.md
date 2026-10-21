# Data contract — cash-runway-forecaster

Up to four UTF-8 CSV files, each with a header row. At least one is required.
Column order does not matter; names must match exactly (case-sensitive). A
UTF-8 BOM is tolerated. Fully blank lines are skipped; anything else malformed
is a hard error naming the file, line, and column.

## Universal sign convention

**Every amount in every file is a positive magnitude.** Direction comes from
the file (`--ar` is always an inflow, `--ap` and `--payroll` are always
outflows) or, for recurring items, from the `direction` column. A negative or
zero amount is rejected rather than reinterpreted.

If your AP export publishes credits as negative, deal with credit notes before
the export: net them against the bill, or drop them and note the adjustment.
The forecaster does not net across rows.

Amounts accept thousands separators, `$`, and accounting parentheses. All
dates are ISO `YYYY-MM-DD`.

## 1. AR ageing (`--ar`)

Source: open invoice / AR ageing detail from QuickBooks, Xero, NetSuite, or
Stripe Billing.

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `invoice_id` | yes | text | Unique in the file. |
| `customer` | yes | text | Shown in the detail view. |
| `invoice_date` | no | date | Context only; not used in scheduling. |
| `due_date` | yes | date | Contractual due date. |
| `amount` | yes | decimal > 0 | **Outstanding balance**, not the original invoice value. Exclude anything already paid. |
| `expected_date` | no | date | Overrides `due_date` when you have better information (a promised-to-pay date). |

Scheduling: expected date = (`expected_date` or `due_date`) +
`--collection-lag-days`. The amount is multiplied by `--collection-rate`; the
remainder is reported as an expected shortfall and never appears in any week.
Anything still earlier than `--as-of` is assumed to settle in week 1.

## 2. AP ageing (`--ap`)

Source: unpaid bills / AP ageing detail from the same system. Include approved
but unpaid bills.

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `bill_id` | yes | text | Unique in the file. |
| `vendor` | yes | text | Shown in the detail view. |
| `bill_date` | no | date | Context only. |
| `due_date` | yes | date | |
| `amount` | yes | decimal > 0 | Outstanding balance. |
| `expected_date` | no | date | Overrides `due_date` (e.g. an agreed payment run date). |

Scheduling: expected date = (`expected_date` or `due_date`) +
`--payment-lag-days`. No haircut is applied. Overdue bills are assumed paid in
week 1.

## 3. Payroll schedule (`--payroll`)

Source: the pay calendar from the payroll provider.

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `pay_date` | yes | date | The date cash leaves the account, not the period end. |
| `description` | yes | text | e.g. `Payroll - October first half`. |
| `amount` | yes | decimal > 0 | **Total employer cash cost**: net pay, employee and employer taxes, benefits, and pension in one number, unless you list them as separate rows. |

Rows dated before `--as-of` are reported as "before start" and excluded; rows
after the horizon are listed as outside it. Payroll is never flexed by
`--spend-change-pct`.

## 4. Recurring commitments (`--recurring`)

Source: contracts, the lease, the loan amortisation schedule, tax instalment
notices, and your expected new-billings run rate.

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `description` | yes | text | Shown in the detail view. |
| `amount` | yes | decimal > 0 | Amount per occurrence. |
| `frequency` | yes | enum | `weekly`, `biweekly`, `monthly`, `quarterly`, `one_off`. Anything else is an error. |
| `start_date` | yes | date | First occurrence. Occurrences before `--as-of` are skipped, not pulled forward. |
| `end_date` | no | date | Last possible occurrence. Blank means it runs to the end of the horizon. |
| `direction` | yes | enum | `inflow` or `outflow`. |
| `category` | no | text | Your own grouping; carried, not used. |

`monthly` and `quarterly` keep the day of month from `start_date`, clamped to
the last day of shorter months (the 31st becomes the 30th in November).
`weekly` and `biweekly` step 7 and 14 days from `start_date`.

Use an `inflow` row for expected new billings collected — this is the only
forward revenue in the model, and it is entirely your assumption.

## Arguments

| Argument | Default | Meaning |
| --- | --- | --- |
| `--opening-balance` | required | **Cleared** bank balance at `--as-of`, summed across accounts. Not the GL cash balance. |
| `--as-of` | required | Day one of week 1. Weeks are 7 days from here. |
| `--weeks` | 13 | Horizon length, 1 to 104. |
| `--minimum-cash` | 0 | Covenant or floor tested every week. |
| `--collection-lag-days` | 0 | Days added to every AR date. Set it from actual DSO behaviour, not the payment terms. |
| `--collection-rate` | 100 | Percent of AR expected to arrive at all. |
| `--payment-lag-days` | 0 | Days added to every AP date. |
| `--spend-change-pct` | 0 | Percent change applied to recurring **outflows** only. |

## Completeness guarantee

Every row of every file lands in exactly one of: scheduled inside the horizon,
after the horizon, before the start, or not expected (the AR haircut). The
report prints that split per file with a residual that must be `0.00`, and the
cash walk proves `opening + inflows - outflows = closing`. A non-zero residual
exits 1 — the forecast is not safe to circulate.

## Rounding

All arithmetic uses `decimal.Decimal`. The collection-rate and spend-change
adjustments are quantized to 2 dp per row, `ROUND_HALF_UP`, before scheduling,
so that the weekly totals are exactly the sum of the items shown by `--detail`.
All other rounding happens only at display.
