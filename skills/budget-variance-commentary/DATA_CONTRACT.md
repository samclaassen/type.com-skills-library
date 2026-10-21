# Data contract — budget-variance-commentary

One UTF-8 CSV with a header row: **one row per department and account for the
period**. Column order does not matter; names must match exactly
(case-sensitive). A UTF-8 BOM is tolerated. Fully blank lines are skipped;
anything else malformed is a hard error naming the line and column.

Rows must already be aggregated: a repeated `department` + `account_code` pair
is rejected rather than summed, so the script never combines lines behind your
back.

## Required columns

| Column | Type | Notes |
| --- | --- | --- |
| `department` | text | Cost centre / class / location, as your GL calls it. Drives the department summary. |
| `account_code` | text | GL account number. Unique within a department. |
| `account_name` | text | GL account description. Used verbatim in the commentary. |
| `account_type` | enum | `revenue`, `cogs`, or `opex`. Anything else is an error. This is what makes a variance favourable or unfavourable. |
| `actual` | decimal | Actual for the period. |
| `budget` | decimal | Budget for the same period. May be `0` — the percentage then reports `n/m` and only the absolute threshold applies. |

## Optional columns (these are what enable classification)

| Column | Type | Notes |
| --- | --- | --- |
| `ytd_actual`, `ytd_budget` | decimal | Year to date through the same period end. Supply both or neither. Required for timing vs permanent. |
| `prior_actual`, `prior_budget` | decimal | The immediately preceding period. Used to confirm persistence, and as a fallback when there is no YTD. |
| `actual_units`, `budget_units` | decimal | The driver volume for this account: headcount, customers, leads, orders. Supply both or neither. |
| `unit_name` | text | What the units are, e.g. `paying customers`. Defaults to `units`. Appears in the commentary. |
| `owner` | text | Budget owner. Named in the drafted sentence. |
| `classification` | enum | `volume`, `timing`, `permanent`, or `unclassified`. Overrides the rules; reported as `stated in the file`. |
| `note` | text | Preparer context. Appended to the drafted commentary, and used as the evidence text when `classification` is stated. |

## Sign convention (read before exporting)

**Enter all amounts as positive magnitudes and let `account_type` carry the
direction.** Revenue of 486,300 is `486300.00`, not `-486300.00`. Costs are
positive too.

- `variance = actual - budget` for every row.
- For `revenue`, a positive variance is favourable.
- For `cogs` and `opex`, a negative variance is favourable.
- `operating income effect` is `+variance` for revenue and `-variance` for cost
  lines, so a positive effect always means "good for profit".
- `Operating income = revenue - cogs - opex`.

Most GL exports publish revenue as a credit (negative). Flip the sign on
revenue rows during export. If revenue arrives negative, every revenue variance
will be labelled backwards — the totals will still add up, which is exactly why
it is worth checking the revenue line against the P&L before you believe the
commentary.

Genuine contra accounts (refunds, rebates, credit notes) may be negative. They
are handled correctly as long as actual and budget use the same convention.

Amounts accept thousands separators, `$`, and accounting parentheses:
`(1,204.55)` is `-1204.55`.

## Upstream sources

| Field | Where it comes from |
| --- | --- |
| `actual`, `ytd_actual`, `prior_actual` | P&L by class/department at account level: QuickBooks, Xero, or NetSuite. |
| `budget`, `ytd_budget`, `prior_budget` | Budget-vs-actual report from the same system, or the planning spreadsheet. |
| `actual_units` / `budget_units` | HRIS or payroll (headcount), billing system (customers, seats), marketing platform (leads), commerce platform (orders). |
| `owner` | Your budget-owner matrix. |

`actual` and `budget` must cover the same period, and the YTD pair must end on
the same date as `--as-of`.

## Control total

`--control-operating-variance` is the operating income variance per the
published P&L (`actual operating income - budget operating income`, same sign
convention as above). A mismatch fails the run, and it is the only check that
tests the file against something outside itself — the internal reconciliation
only proves the script's own aggregation.

## Thresholds and rounding

| Argument | Default | Meaning |
| --- | --- | --- |
| `--materiality-abs` | 5000 | Absolute variance threshold. |
| `--materiality-pct` | 10 | Variance as a percent of `abs(budget)`. |
| `--materiality-rule` | `and` | `and` requires both tests; `or` requires either. |
| `--volume-share` | 60 | Percent of the variance the volume effect must explain to classify as volume. |
| `--timing-share` | 50 | YTD variance below this percent of the period variance reads as timing. |

All arithmetic uses `decimal.Decimal`. Display rounds to 2 dp for money and
1 dp for percentages, `ROUND_HALF_UP`. The volume effect is computed at the
budget rate and the rate/mix effect is taken as the residual, so the two always
sum to the variance exactly.
