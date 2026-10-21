# Data contract — churn-risk-scanner

Five CSV files plus one JSON config. UTF-8, comma separated, one header row.
Dates are `YYYY-MM-DD`, months are `YYYY-MM`, money is plain USD. Every file may
cover the whole book. Rows in the usage, support, contacts, and invoices files
must reference an `account_id` that exists in `accounts.csv`, otherwise the run
fails — a silent orphan row is a silently wrong score.

## 1. `accounts.csv` — required

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `account_id` | string | Unique. The join key for every other file. | Salesforce `Account.Id`, HubSpot company id, Attio record id |
| `account_name` | string | Display name. Empty falls back to the id. | CRM |
| `arr_usd` | number | Current annual recurring revenue. Drives ARR at risk. Negative is an error. | CRM or billing (Stripe, NetSuite, Chargebee) |
| `segment` | string | Free text (`enterprise`, `mid-market`, `smb`). Reported, not scored. | CRM |
| `contract_start` | date | Current term start. | CRM |
| `renewal_date` | date | Next renewal. Drives `--renewal-within` and the days-to-renewal column. | CRM |
| `csm_owner` | string | Named owner. Empty becomes `(unassigned)`. | CRM |

## 2. `usage_monthly.csv` — required

One row per account per month. Wide format.

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `account_id` | string | Must exist in `accounts.csv`. | Warehouse |
| `month` | `YYYY-MM` | Unique per account; duplicates are an error. | Warehouse |
| `active_users` | number | Distinct users active in the month. | Product warehouse, Amplitude, PostHog |
| `licensed_seats` | number | Entitled seats that month. Must be greater than zero (utilization divides by it). | Billing or provisioning |
| `key_actions` | number | The core value action your product measures — shipments created, invoices processed, tickets resolved. Pick one and keep it stable. | Product warehouse |

Export at least `history_months` (6 by default) complete months. With fewer, the
trend factors report "no data" and their weight is redistributed.

## 3. `support.csv` — required

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `ticket_id` | string | Unique across the file. | Zendesk, Intercom, Plain, Jira SM |
| `account_id` | string | Must exist in `accounts.csv`. | Same |
| `created_date` | date | Must not be after `--as-of`. | Same |
| `resolved_date` | date or empty | Empty means still open. Must not precede `created_date`. | Same |
| `priority` | string | `p1`-`p4`, case insensitive. Only `p1` carries extra weight. | Same |
| `escalated` | boolean | `yes/no/true/false/1/0`; empty means no. Anything else is an error. | Same, or the escalation tag |
| `csat_score` | number or empty | 1-5. Outside the range is an error. Empty means no response, which is not the same as a bad score. | Survey tool |

Export at least `csat_window_days` (180 by default) of history.

## 4. `contacts.csv` — required

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `contact_id` | string | Unique across the file. | CRM contact id |
| `account_id` | string | Must exist in `accounts.csv`. | CRM |
| `full_name` | string | Printed in the evidence line for departures. | CRM |
| `role` | string | Lowercased and matched against `champion_roles` (`champion`, `economic_buyer` by default). Other roles (`admin`, `user`) are loaded but do not affect the factor. | CRM contact role / buying role |
| `status` | enum | `active` or `departed`. Anything else is an error. | CRM, or an enrichment job that detects job changes |
| `departure_date` | date | Required when `status` is `departed`; empty otherwise. | CRM |

Champion factor outcomes (values configurable under
`factors.champion_departure.scores`):

| Situation | Default score |
| --- | --- |
| All champion-role contacts departed, most recent within the window | 100 |
| All departed, but longer ago than the window | 60 |
| No contact carries a champion role at all | 60 |
| One departed within the window, another champion still active | 40 |
| Champion active, no recent departures | 0 |

## 5. `invoices.csv` — required

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `invoice_id` | string | Unique across the file. | Stripe, NetSuite, QuickBooks, Chargebee |
| `account_id` | string | Must exist in `accounts.csv`. | Same |
| `invoice_date` | date | Issue date. | Same |
| `due_date` | date | Must not precede `invoice_date`. Only invoices due within `invoice_window_days` are scored. | Same |
| `paid_date` | date or empty | Empty means unpaid; if the due date has passed it counts as overdue. | Same |
| `amount_usd` | number | Used to size the outstanding balance in the evidence line. | Same |

## 6. `config/risk_model.json` — required (defaults ship with the skill)

| Key | Default | Meaning |
| --- | --- | --- |
| `history_months` | 6 | Months of usage considered. Must cover two trend windows. |
| `trend_window_months` | 3 | Size of the trailing and prior comparison windows. |
| `support_window_days` | 90 | Window for the support burden factor. |
| `csat_window_days` | 180 | Window for the sentiment factor. |
| `invoice_window_days` | 365 | Window for the invoice factor, applied to `due_date`. |
| `champion_departure_window_days` | 180 | How recent a departure has to be to count as recent. |
| `champion_roles` | `champion`, `economic_buyer` | Roles that count as a champion. |
| `support_index.p1_points` / `.escalation_points` | 5 / 3 | Points added per P1 and per escalation on top of tickets per 100 seats. |
| `invoice_index.overdue_points` | 15 | Points added per unpaid overdue invoice on top of average days late. |
| `bands[]` | critical 70, high 50, medium 30, low 0 | Final-score bands. |
| `factors.<code>.weight` | see table in SKILL.md | Relative weight. Weights need not sum to 100; the score is normalized by the weight of the factors that had data. |
| `factors.<code>.bands[]` | see file | Ordered bands. Each entry has `at_or_below` (for metrics where lower is worse) or `at_or_above` (where higher is worse), plus `score` and `label`. The last entry must use a `null` threshold as the catch-all. |
| `factors.champion_departure.scores` | see table above | Rule-based scores instead of bands. |

## Units and conventions

- Factor scores are 0-100 where 100 is the worst. Contributions are
  `score x weight / available weight` and sum to the risk score.
- `weighted ARR at risk = ARR x risk score / 100`, rounded to whole dollars.
- Percent changes are `(recent average - prior average) / prior average x 100`.
  If the prior window average is zero, the factor reports "no data" rather than
  dividing by zero.
- The support index is `tickets / seats x 100 + P1s x p1_points + escalations x escalation_points`.
- The invoice index is `average days late on paid invoices + overdue count x overdue_points`.

## Failure modes

The script exits `2` and names the file, row, and column for: a missing or empty
file, a missing required column, a duplicate `account_id`, `ticket_id`,
`contact_id`, `invoice_id`, or account/month usage row, an `account_id` that is
not in `accounts.csv`, a non-numeric or negative money value, `licensed_seats`
at or below zero, a malformed date or month, a `resolved_date` before its
`created_date`, a `created_date` after `--as-of`, a `due_date` before its
`invoice_date`, a CSAT outside 1-5, a `status` outside active/departed, a
departed contact with no `departure_date`, an `escalated` value that is not a
boolean, a config missing a required key or a catch-all band, a negative
`--renewal-within`, a renewal filter that matches no account, and an account for
which no factor has usable data.
