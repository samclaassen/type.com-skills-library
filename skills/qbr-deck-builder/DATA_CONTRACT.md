# Data contract — qbr-deck-builder

Five CSV files plus one JSON config. UTF-8, comma separated, one header row.
Dates are `YYYY-MM-DD`, months are `YYYY-MM`, money is plain USD. Every file may
contain many accounts; the script filters to `--account-id`.

## 1. `usage_monthly.csv` — required

Long format so any metric can be added without a schema change.

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `account_id` | string | Must match `commercial.account_id`. | Warehouse dimension table |
| `month` | `YYYY-MM` | Calendar month the value covers. | Warehouse |
| `metric_name` | string | Free text. Must include the three metrics named in `config.metrics` plus any metric a goal or product trigger references. | Warehouse / product analytics |
| `value` | number | The metric value for that account and month. Non-numeric is an error. | Warehouse |

An `(account_id, metric_name, month)` triple must be unique; duplicates are an
error, because silently summing or overwriting them would misstate the trend.

Default metrics (rename in the config if yours differ):

- `monthly_active_users` — the adoption metric.
- `licensed_seats` — entitled seats for that month; falls back to
  `commercial.licensed_seats` for months with no row.
- `key_actions` — the core value action (shipments created, tickets resolved,
  invoices processed — whatever your product actually does).

Sources: Snowflake/BigQuery/Postgres product warehouse, Amplitude or PostHog
exports, or an internal usage API dumped to CSV.

## 2. `goals.csv` — required

One row per customer-stated goal. Rows for other accounts are ignored.

| Column | Type | Notes |
| --- | --- | --- |
| `account_id` | string | Matches the account under review. |
| `goal_id` | string | Unique within the account. |
| `goal_text` | string | The goal in the customer's words. Quoted if it contains commas. |
| `metric_name` | string | Must exist in `usage_monthly.csv` for this account, otherwise the goal is reported as "no data". |
| `baseline_value` | number | Where the customer started. |
| `target_value` | number | Where they want to be. May be lower than baseline for reduction goals. Equal to baseline is an error (division by zero). |
| `target_date` | date | When they want it by. Printed, not enforced. |

Source: the success plan in the CRM, the kickoff or onboarding doc in Google
Drive or Notion, or the mutual action plan. Someone has to convert prose into a
metric, baseline, and target — do that once and keep the file.

## 3. `support.csv` — required

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `ticket_id` | string | Unique per account. | Zendesk, Intercom, Plain, Jira SM |
| `account_id` | string | Matches the account. | Same |
| `created_date` | date | Must not be after `--as-of`. | Same |
| `resolved_date` | date or empty | Empty means still open. Must not precede `created_date`. | Same |
| `priority` | string | `p1`-`p4`, case insensitive. Only `p1` is special-cased. | Same |
| `status` | string | Free text (`open`, `closed`, `pending`). Open/closed is decided by `resolved_date`, not this column. | Same |
| `subject` | string | Printed for open tickets and aging P1s. | Same |
| `csat_score` | number or empty | 1-5. Outside that range is an error. Empty means no survey response. | Same |

Export at least `2 x support_window_days` (180 days by default) so the
period-over-period comparison has a prior window.

## 4. `commercial.csv` — required

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `account_id` | string | Unique. | CRM |
| `account_name` | string | Display name. | CRM |
| `arr_usd` | number | Current annual recurring revenue. Drives ARR per seat. | CRM or billing |
| `contract_start` | date | Current term start. | CRM |
| `renewal_date` | date | Next renewal. Drives the renewal risk. | CRM |
| `licensed_seats` | integer | Contracted seats. Must be greater than zero. | CRM or billing |
| `products` | string | Semicolon separated product codes the account already owns (`CORE;TRACKING`). Matched against `products.product_code` to suppress cross-sell of things they already have. | CRM |
| `csm_owner` | string | Named CSM. Empty becomes `(unassigned)`. | CRM |

## 5. `products.csv` — required

The cross-sell catalog. One row per sellable module.

| Column | Type | Notes |
| --- | --- | --- |
| `product_code` | string | Unique; must use the same vocabulary as `commercial.products`. |
| `product_name` | string | Display name. |
| `list_price_per_seat_usd` | number | Estimated ARR is `price x licensed_seats`. |
| `signal_metric` | string | A `metric_name` from `usage_monthly.csv`. The account's latest value drives the trigger. |
| `signal_threshold` | number | Must be greater than zero. At or above it, the opportunity is raised. |
| `value_statement` | string | One sentence the CSM can actually say. Printed as the talk track. |

## 6. `config/qbr.json` — required (defaults ship with the skill)

| Key | Default | Meaning |
| --- | --- | --- |
| `metrics.adoption` / `.licensed` / `.engagement` | `monthly_active_users`, `licensed_seats`, `key_actions` | Which metric names carry those roles. |
| `history_months` | 7 | Months shown in the trend table. Must be at least two trend windows. |
| `trend_window_months` | 3 | Size of the trailing and prior comparison windows. |
| `support_window_days` | 90 | Support comparison window. |
| `goal_status_thresholds` | 100 / 70 / 30 | Attainment percentages for achieved, on track, at risk. Below the last is off track. |
| `risk_rules.adoption_decline_pct`, `.engagement_decline_pct` | 10 | Percentage decline that fires a risk. |
| `risk_rules.utilization_floor` | 0.6 | Utilization below this fires a risk. |
| `risk_rules.open_p1_age_days` | 7 | Age at which an open P1 becomes a risk. |
| `risk_rules.support_volume_increase_pct` | 50 | Period-over-period ticket growth that fires a risk. |
| `risk_rules.csat_floor` | 4.0 | Average CSAT below this fires a risk. |
| `risk_rules.renewal_horizon_days` | 90 | Days to renewal that fires the renewal risk. |
| `risk_severity` | see file | `high` / `medium` / `low` per risk code; drives ordering. |
| `expansion.seat_utilization_trigger` | 0.85 | Utilization at which seat expansion is raised. |
| `expansion.target_utilization` | 0.8 | Utilization the proposed seat count would restore. |
| `expansion.confidence_floor` / `.confidence_cap` | 0.4 / 0.95 | Confidence starts at the floor and rises with how far the signal clears its threshold, capped at the cap. |

## Failure modes

The script exits `2` and names the file, row, and column for: a missing or
empty file, a missing required column, an unknown `--account-id`, a duplicate
account/metric/month row, a duplicate `ticket_id`, `goal_id`, or
`product_code`, a non-numeric or negative money value, a malformed date or
month, a `resolved_date` before its `created_date`, a `created_date` after
`--as-of`, a CSAT outside 1-5, `licensed_seats` at or below zero, a goal whose
baseline equals its target, a `signal_threshold` at or below zero, a config
missing a required key, and an account with no rows for the adoption metric.
