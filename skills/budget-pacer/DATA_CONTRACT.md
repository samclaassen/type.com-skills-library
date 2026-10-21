# Data contract — budget-pacer

Three CSV files, UTF-8, comma separated, with a header row (a BOM is tolerated).
Extra columns are ignored. All three describe one pacing month, in one currency.

Numeric fields accept `$` and thousands separators. Negative values are rejected
everywhere; net out credits and refunds upstream.

Join keys: `client` + `campaign` joins `daily_spend.csv` to `campaigns.csv`, and
`client` joins `commitments.csv`. Both joins are literal string matches. A
campaign in the spend file that is missing from the campaigns file is a hard
error, as is a client with campaigns but no commitment row.

---

## 1. `campaigns.csv` — campaign inventory and budget bounds

One row per client/campaign. Duplicate client+campaign rows are an error.

**Source:** the connected ad platforms for `status` and `current_daily_budget`
(Google Ads campaign settings, Meta Ads Manager campaign or ad-set budgets,
TikTok Ads, LinkedIn Campaign Manager). `min_daily_budget` and
`max_daily_budget` are agency decisions, not platform fields — ask the account
lead and record them.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Join key; must have a commitment row. |
| `campaign` | text | — | yes | Platform campaign name. |
| `channel` | text | — | yes | Display label, e.g. `Google Search`, `Meta Ads`. |
| `status` | text | — | yes | `ACTIVE` or `PAUSED` (case-insensitive). Paused campaigns keep their spend in the totals but receive no recommended budget. |
| `current_daily_budget` | number | currency/day | yes | Today's daily cap. Used as the allocation weight, so a campaign at `0` receives an equal share only when every campaign for that client is at `0`. |
| `min_daily_budget` | number | currency/day | yes | The lowest daily budget worth running (learning-phase floor, or `0` if the campaign can be starved). |
| `max_daily_budget` | number | currency/day | yes | The highest daily budget the campaign should be trusted with. Must be `>= min_daily_budget`. |

---

## 2. `daily_spend.csv` — spend by day

One row per client/campaign/day. Multiple rows for the same day are summed.
Rows dated outside `--month` are ignored; a row dated **after** `--as-of` is a
hard error.

**Source:** each connected ad platform's report with a day dimension, for the
first of the month through the pacing date. Use cost/amount spent, not billed
amount, and use the same time zone across platforms.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | |
| `campaign` | text | — | yes | Must exist in `campaigns.csv` for that client. |
| `date` | date | `YYYY-MM-DD` | yes | One calendar day. |
| `spend` | number | currency | yes | Cost for that campaign on that day. `0` rows are fine. |

Missing days are treated as zero-spend days. That is deliberate — a gap in the
export pulls the run rate down, which is the conservative direction for an
under-pacing client and the dangerous direction for an over-pacing one, so
confirm the export is complete before trusting an `UNDER` verdict.

---

## 3. `commitments.csv` — the committed media budget

One row per client per month. Duplicate client rows within a month are an error.

**Source:** the insertion order, media plan or statement of work. This is media
spend only, excluding agency fees.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | |
| `month` | text | `YYYY-MM` | yes | Only rows matching `--month` are read. |
| `committed_spend` | number | currency | yes | Full-month committed media spend. Must be above 0. |

A client with a commitment but no campaigns is listed in the report's notes and
excluded from the totals.

---

## Derived metrics

| Metric | Formula |
| --- | --- |
| Days elapsed | `--as-of` day of month |
| Days remaining | `days in month - days elapsed` |
| Run-rate window | `--run-rate-days` ending on `--as-of`, truncated at the first of the month |
| Run rate | `spend inside the window / days in the window` |
| Projected month-end | `spend to date + run rate × days remaining` |
| Expected to date | `committed × days elapsed / days in month` |
| Variance | `projected - committed`, also as a percent of committed |
| Status | `OVER`, `UNDER` or `ON PACE` at `±--tolerance` percent |
| Required daily | `max(0, committed - spend to date) / days remaining` |
| Recommended daily budget | Required daily, split across active campaigns in proportion to `current_daily_budget`, clamped to `[min, max]`, with the clamped remainder redistributed across unclamped campaigns and the cent-level residual assigned to the largest campaign that can absorb it |

Two allocations cannot reconcile and are reported as such rather than forced:

- **ceiling** — `required daily > Σ max_daily_budget`; every campaign is set to
  its maximum and the shortfall is quantified.
- **floor** — `required daily < Σ min_daily_budget`; every campaign is set to
  its minimum and the projected overshoot is quantified.

## Failure modes

The script exits `2` and names the file, line and column when:

- a required column is missing, a file is empty, or it has no data rows;
- a numeric field is empty, non-numeric or negative, or `committed_spend` is 0;
- `status` is not `ACTIVE` or `PAUSED`, or `max_daily_budget < min_daily_budget`;
- a date is not a real `YYYY-MM-DD` date, or `month` is not `YYYY-MM`;
- `--as-of` is not inside `--month`;
- a spend row is dated after `--as-of`;
- a spend row references a campaign absent from `campaigns.csv`;
- a client has campaigns but no `committed_spend` row for the month;
- `commitments.csv` has no rows for `--month`, or `daily_spend.csv` has no rows
  inside the month on or before `--as-of`;
- `campaigns.csv` or `commitments.csv` contains a duplicate key row.
