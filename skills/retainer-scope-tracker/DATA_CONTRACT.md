# Data contract — retainer-scope-tracker

Five CSV files, UTF-8, comma separated, with a header row (a BOM is tolerated).
Extra columns are ignored, so exports can keep their own extra fields.

Numeric fields accept `$` and thousands separators. Negative numbers are
rejected everywhere: a credit note or a time-log correction must be applied
upstream, not expressed as a negative row.

Join keys:

- `client` joins `contracts.csv`, `scope.csv`, `time_entries.csv` and
  `deliverables.csv`. It is matched literally, so spelling must be identical
  across systems.
- `person` joins `time_entries.csv` to `roster.csv`.
- `deliverable` joins `scope.csv`, `time_entries.csv` and `deliverables.csv`.
  A deliverable that appears in time or delivery but not in scope is treated as
  out-of-scope work, which is a finding rather than an error.

A client in `time_entries.csv` or `deliverables.csv` with no contract row for
the reporting month is a hard error.

---

## 1. `contracts.csv` — the commercial terms

One row per client per month. Duplicate client rows within one month are an
error.

**Source:** the signed statement of work, or the billing/invoicing system.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Join key. |
| `month` | text | `YYYY-MM` | yes | Only rows matching `--month` are read; keep history in the file. |
| `retainer_fee` | number | currency | yes | The monthly fee, excluding pass-through media spend. Must be above 0. |
| `contracted_hours` | number | hours | yes | Hours the fee buys. `0` is accepted for deliverable-priced retainers; hours variance is then reported as `n/a`. |
| `renewal_date` | date | `YYYY-MM-DD` | yes | Next renewal or notice date. Drives renegotiation timing against `--as-of` and `--lead-days`. |

---

## 2. `scope.csv` — contracted deliverables

One row per client / deliverable for the month. Duplicate client+deliverable
rows within one month are an error.

**Source:** the scope table in the SOW. If the SOW prices deliverables rather
than hours, agree an hour allocation with the account lead first — the variance
column is only as trustworthy as that allocation.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Must have a contract row for the month. |
| `month` | text | `YYYY-MM` | yes | |
| `deliverable` | text | — | yes | Exact name, reused by the other two files. |
| `contracted_qty` | number | count | yes | How many of this deliverable the fee buys (e.g. `1` monthly report, `6` creative concepts). |
| `contracted_hours` | number | hours | yes | Budgeted hours for the whole quantity, not per unit. |

The sum of `contracted_hours` across deliverables does not have to equal the
contract's `contracted_hours`; the contract figure is authoritative for totals
and the per-deliverable figures are used for line-level variance.

---

## 3. `time_entries.csv` — logged time

One row per time entry. Only entries dated inside `--month` are counted.

**Source:** the time-tracking or project tool (Harvest, Toggl, Clockify, Asana,
Jira, Monday). Export the month with client, person, task/deliverable and hours.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Must have a contract row for the month. |
| `date` | date | `YYYY-MM-DD` | yes | Entries outside `--month` are ignored, not errors. |
| `person` | text | — | yes | Must exist in `roster.csv`. |
| `deliverable` | text | — | yes | Matched against `scope.csv` by exact name. |
| `hours` | number | hours | yes | Fractional hours are fine (`1.5`). |
| `notes` | text | — | no | Carried for auditability only. |

---

## 4. `roster.csv` — internal cost rates

One row per person. Duplicate people are an error.

**Source:** payroll or the ops cost sheet. Use fully loaded cost (salary plus
employer burden) divided by productive hours. This file is confidential — it
must never be included in a client deliverable.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `person` | text | — | yes | Must match `time_entries.person` exactly. |
| `role` | text | — | yes | Recorded for context. |
| `cost_per_hour` | number | currency/hour | yes | Internal cost, not the client billing rate. Must be above 0. |

---

## 5. `deliverables.csv` — what shipped

One row per shipped item (or per batch, with a quantity). Only rows dated inside
`--month` are counted. Multiple rows for the same client and deliverable are
summed.

**Source:** the project tool's completed-task export, or the account manager's
delivery log.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Must have a contract row for the month. |
| `date` | date | `YYYY-MM-DD` | yes | Delivery date. |
| `deliverable` | text | — | yes | Matched against `scope.csv` by exact name. |
| `qty_delivered` | number | count | yes | Quantity shipped in this row. |

---

## Derived metrics

| Metric | Formula |
| --- | --- |
| Delivered hours | Sum of `hours` for the client, inside the month |
| Hours variance | `delivered hours - contracted hours`, also expressed as a percent |
| Delivery cost | `Σ (hours × cost_per_hour of the person)` |
| Margin | `retainer_fee - delivery cost`; margin % divides by the fee |
| Effective hourly rate | `retainer_fee / delivered hours` |
| Contracted hourly rate | `retainer_fee / contracted hours` |
| Blended cost rate | `delivery cost / delivered hours` |
| Scope-back margin | `(fee - blended cost rate × contracted hours) / fee` — margin if the team had delivered exactly the contracted hours at the same staffing mix |
| Proposed fee | `delivery cost ÷ (1 - target margin)`, rounded up to `--round-to` |
| Annualized gap | `(fee × target margin - actual margin) × 12` |

## Flags

| Flag | Condition |
| --- | --- |
| `SCOPE CREEP` | Hours variance above `+--over-tolerance` percent |
| `UNDER-DELIVERED` | Hours variance below `−--under-tolerance` percent |
| `MARGIN` | Margin percent below `--target-margin` |
| `RATE` | Effective rate more than `--rate-drop` percent below the contracted rate |
| `UNSCOPED WORK` | Any logged hours against a deliverable absent from `scope.csv` |
| `NO DELIVERY` | Zero hours logged for a client with a live contract |

## Failure modes

The script exits `2` and names the file, line and column when:

- a required column is missing, the file is empty, or it has no data rows;
- a numeric field is empty, non-numeric or negative;
- `retainer_fee` or `cost_per_hour` is `0`;
- a date is not a real `YYYY-MM-DD` date, or `month` is not `YYYY-MM`;
- `contracts.csv` has no rows for `--month`, or `time_entries.csv` has no rows
  dated inside it;
- a `client` has no contract row for the month, or a `person` is missing from
  the roster;
- `contracts.csv`, `scope.csv` or `roster.csv` contains a duplicate key row.
