---
name: month-end-close-checklist
description: Drive a month-end close from a task definition file — sequence tasks by dependency, track owner and status, measure days-to-close against target, surface the critical path and what is blocking it, and produce a tie-out log proving which balances were verified against which evidence.
---

# Month-end close checklist

Turns a close checklist into a status report a controller can act on in the
morning stand-up: who owes what, what the close is actually waiting on, whether
it will hit the target close day, and which balances are still unevidenced.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `month-end-close-checklist`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Daily during close: "where are we, and what is blocking day 5?"
- Post-close retro: which tasks pushed the close out, and by how many days.
- Audit prep: the tie-out log is the evidence index for the balance sheet.
- Handover between close owners, so nothing sits unassigned.

Do not use it for the reconciliations themselves — use `gl-reconciliation` for
bank and processor matching and feed its results into the tie-out file.

## Inputs

Two CSVs. Full column specification in `DATA_CONTRACT.md`.

1. `tasks.csv` — the close checklist: `task_id`, `task_name`, `owner`,
   `depends_on`, `estimate_days`, `status`, and optionally `remaining_days`,
   `completed_on`, `blocker_note`, `category`.
2. `tieouts.csv` (optional but strongly recommended) — one row per balance
   being proved: `account_code`, `account_name`, `gl_balance`,
   `evidence_balance`, `evidence_source`, `evidence_ref`, and optionally
   `verified_by`, `verified_on`, `note`.

### Gathering the inputs through Type integrations

- **Checklist**: most teams already keep it in a task tracker or a sheet. Pull
  the current list through the connected Linear / Asana / Notion / Google
  Sheets integration and map its fields onto the columns above. If there is no
  tracker, write `tasks.csv` once from the team's close narrative and keep it
  in the repo or workspace — it is the reusable asset here.
- **GL balances**: export the trial balance for the period from the connected
  QuickBooks, Xero, or NetSuite integration and use the closing balance per
  account as `gl_balance`.
- **Evidence balances**: bank closing balance from the bank CSV or banking
  integration; `Accounts receivable` from the AR ageing export; `Accounts
  payable` from the AP ageing export; processor balances from the Stripe
  integration's balance/payout report; payroll accruals from the payroll
  provider's period report. Record where each number came from in
  `evidence_source` and `evidence_ref` — that pairing is what an auditor asks
  for, and it is the whole point of the tie-out log.
- Never paste credentials into the CSVs or the script. The agent exports files,
  the script reads files.

## Running it

```bash
python3 scripts/close_status.py \
  --tasks examples/sample_tasks.csv \
  --tieouts examples/sample_tieouts.csv \
  --period-end 2026-08-31 \
  --as-of 2026-09-08 \
  --target-close-days 8 \
  --holidays 2026-09-07
```

- `--period-end` is the accounting period end; business day 1 is the first
  working day after it.
- `--as-of` is the status date. It is required because the script never reads
  the wall clock — the same inputs always produce the same output.
- `--target-close-days` is the close target in business days (default 5).
- `--holidays` takes comma-separated ISO dates that are not working days.
- `--tieout-tolerance` sets a per-account tolerance (default `0.00`).
- `--json` emits the same analysis as JSON for downstream automation.

Exit codes: `0` clean, `1` the close does not tie (a difference over tolerance
or a balance with no evidence), `2` bad input — the message names the file,
line, and column.

`examples/run.sh` runs three scenarios end to end, including the failing ones.

## Reading the output

- **Header** — business day of the close, target vs projected close day, and
  the variance in business days. Projected close is the finish of the last task
  under current dependencies and remaining estimates.
- **Critical path** — the chain that determines the close date. Shortening
  anything off this path does not move the close. The `Next action on the path`
  line is the single most useful sentence in the report.
- **Blocked and at-risk** — tasks flagged `blocked` plus every task whose
  predecessors are still open, with the note explaining why.
- **Sequence** — the full dependency-ordered checklist with projected finish
  dates, suitable for pasting into the stand-up.
- **Tie-out log** — per account: GL balance, evidence balance, difference,
  where the evidence came from, who verified it, and a verdict of `tied`,
  `DIFFERENCE`, or `NO EVIDENCE`.

When you present this to a human: lead with projected vs target close and the
one blocking task, then list the `DIFFERENCE` / `NO EVIDENCE` accounts by name
with their owner. Do not summarise the tie-out log as "reconciled" unless every
row says `tied`.

## Conventions

- Money uses `decimal.Decimal` end to end; rounding happens only at print time,
  to 2 decimal places, `ROUND_HALF_UP`.
- Balances use natural signs: debits positive, credits negative. A liability
  balance is negative. Keep the evidence balance on the same convention as the
  GL balance or every liability will show a difference of twice the balance.
- Durations are in business days and may be fractional (`0.5`).
- Business days are Monday-Friday minus `--holidays`. There is no built-in
  holiday calendar.

## Limits

- It does not post journals, read your GL, or change anything upstream. It
  reports on files you exported.
- It does not know your holiday calendar unless you pass `--holidays`, and it
  has no concept of part-time owners, working hours, or capacity: two tasks
  owned by the same person on the same day are assumed to run in parallel.
  Model contention by adding an explicit dependency between them.
- Projected close uses `remaining_days` as supplied. It does not estimate
  progress from history; garbage estimates give a garbage projection.
- It checks that a GL balance agrees with one evidence balance. It does not
  test whether the evidence itself is right, does not sample transactions, and
  is not a substitute for review.
- Tie-out tolerance is absolute per account, not a materiality framework.
- No multi-entity consolidation logic, no currency translation, and no
  sub-day scheduling.
