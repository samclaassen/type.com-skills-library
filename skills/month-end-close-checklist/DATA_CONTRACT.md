# Data contract — month-end-close-checklist

Two UTF-8 CSV files with a header row. Column order does not matter; column
names must match exactly (case-sensitive). A UTF-8 BOM is tolerated. Fully
blank lines are skipped. Any other malformed row is a hard error naming the
file, line number, and column.

## 1. Tasks file (`--tasks`, required)

Source: the team's close checklist — Linear / Asana / Notion / Google Sheets
export, or a hand-maintained CSV.

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `task_id` | yes | text | Unique within the file. Used by `depends_on`. Duplicates are an error. |
| `task_name` | yes | text | Human label shown in every table. |
| `owner` | yes | text | Person accountable. Blank becomes `unassigned`. |
| `depends_on` | yes (may be empty) | text | Zero or more `task_id` values separated by `;` or `\|`. Every id must exist; self-reference and cycles are errors. |
| `estimate_days` | yes | decimal >= 0 | Effort in **business days**. Fractions allowed (`0.5`). Used for the progress percentage. |
| `remaining_days` | no | decimal >= 0 | Work left. Defaults to `estimate_days` for incomplete tasks and is forced to `0` for `complete`. Set this for `in_progress` tasks or the projection will overstate the close. |
| `status` | yes | enum | One of `not_started`, `in_progress`, `blocked`, `complete`. Any other value is an error. |
| `completed_on` | conditional | `YYYY-MM-DD` | Required when `status` is `complete`. Ignored otherwise. |
| `blocker_note` | no | text | Why the task is blocked. Printed verbatim in the blocked table and in the next-action line. |
| `category` | no | text | Free-text grouping (`cash`, `revenue`, ...). Carried for your own filtering; not used in the calculation. |

Units: `estimate_days` and `remaining_days` are business days, not calendar
days and not hours.

## 2. Tie-out file (`--tieouts`, optional)

Source: `gl_balance` from the period trial balance (QuickBooks / Xero /
NetSuite). `evidence_balance` from the independent support for that account —
bank statement or bank CSV, AR ageing, AP ageing, Stripe balance report,
payroll provider report, prepaid or fixed-asset schedule.

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `account_code` | yes | text | GL account number. |
| `account_name` | yes | text | GL account description. |
| `gl_balance` | yes | decimal | Closing balance per the GL for the period. |
| `evidence_balance` | no | decimal | Balance per the supporting evidence. **Blank means not yet verified** and fails the run with `NO EVIDENCE`. |
| `evidence_source` | yes (may be empty) | text | What the evidence is, e.g. `Meridian Bank statement`. |
| `evidence_ref` | yes (may be empty) | text | Identifier for the exact document, e.g. `MB-STMT-2026-08`. |
| `verified_by` | no | text | Who performed the tie-out. |
| `verified_on` | no | `YYYY-MM-DD` | When it was verified. |
| `note` | no | text | Carried for context; not used in the calculation. |

### Sign convention (read this before you export)

Balances use **natural GL signs: debits positive, credits negative.**

- Cash, AR, prepaids, fixed assets: positive.
- AP, accruals, deferred revenue, and every other liability: **negative**.
- Equity and revenue: negative. Expenses: positive.

`evidence_balance` must use the same convention as `gl_balance`. AP ageing and
bank exports frequently publish liabilities as positive amounts — flip the sign
on import, otherwise a perfectly reconciled AP account reports a difference of
twice its balance.

`difference = gl_balance - evidence_balance`. A positive difference means the
GL carries more debit (or less credit) than the evidence supports.

## Number and date formats

- Amounts may include thousands separators, a leading `$`, and accounting
  parentheses for negatives: `(1,204.55)` parses as `-1204.55`.
- All dates are ISO `YYYY-MM-DD`. No other format is accepted.
- Amounts are parsed as `decimal.Decimal` with no precision loss and are
  rounded only for display, to 2 decimal places, `ROUND_HALF_UP`.

## Arguments that affect the period

- `--period-end` — last day of the accounting period. Business day 1 is the
  first working day after it.
- `--as-of` — the status date. Required; the script never reads the clock.
- `--holidays` — comma-separated ISO dates excluded from business days. There
  is no built-in holiday calendar.
