---
name: cash-runway-forecaster
description: Build a 13-week direct cash forecast from an opening balance, AR ageing, AP ageing, payroll schedule, and recurring commitments — projecting weekly closing balances, runway in weeks, the first week a minimum cash covenant breaks, and collection or spend scenarios.
---

# Cash runway forecaster

The 13-week direct cash flow that treasury, a lender, or a board asks for:
week-by-week closing balances built from real receivables and payables rather
than a burn-rate average, with the covenant test and the scenario levers
attached.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `cash-runway-forecaster`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Weekly or monthly treasury cycle: refresh the 13-week and see what changed.
- Covenant monitoring: find the first week a minimum cash balance breaks,
  before the bank does.
- Scenario planning: "what if collections slip three weeks?", "what if we cut
  discretionary spend 10%?", "what if we stretch payables two weeks?".
- Fundraising and board reporting: a runway number with an auditable schedule
  behind it instead of `cash / average monthly burn`.

## Inputs

Up to four CSVs; supply at least one. Full specification in
`DATA_CONTRACT.md`.

1. `--ar` AR ageing: `invoice_id`, `customer`, `due_date`, `amount`, optional
   `expected_date`.
2. `--ap` AP ageing: `bill_id`, `vendor`, `due_date`, `amount`, optional
   `expected_date`.
3. `--payroll` payroll schedule: `pay_date`, `description`, `amount` — the full
   cash cost of each run (net pay plus taxes and benefits).
4. `--recurring` recurring commitments: `description`, `amount`, `frequency`,
   `start_date`, `direction`, optional `end_date`, `category`. This is where
   rent, cloud, tooling, loan repayments, tax instalments, and expected new
   billings live.

### Gathering the inputs through Type integrations

- **Opening balance**: today's cleared bank balance from the banking
  integration or the bank portal — cleared, not GL, and not book balance with
  uncleared checks in it.
- **AR ageing**: open invoice report from QuickBooks, Xero, or NetSuite, or the
  open invoice list from Stripe Billing. One row per open invoice with the
  outstanding balance, not the original invoice value.
- **AP ageing**: unpaid bills report from the same system, including anything
  already approved but unpaid.
- **Payroll**: the pay calendar from the payroll provider (Gusto, Rippling,
  ADP, Deel) with the employer total cost per run, not gross wages.
- **Recurring commitments**: vendor contracts, the lease, the loan amortisation
  schedule, tax instalment dates, and the expected new billings run rate.
  Cross-check against last quarter's bank statement so nothing regular is
  missing — the most common error in a 13-week is an omitted recurring payment,
  not a wrong one.
- Export files, then run the script. No credentials anywhere near it.

## Running it

```bash
python3 scripts/cash_forecast.py \
  --opening-balance 1284905.22 \
  --as-of 2026-09-07 \
  --ar examples/ar_ageing.csv \
  --ap examples/ap_ageing.csv \
  --payroll examples/payroll_schedule.csv \
  --recurring examples/recurring_commitments.csv \
  --minimum-cash 750000 \
  --collection-lag-days 7 \
  --collection-rate 97
```

- `--as-of` is day one of week 1. Required; nothing reads the clock. Weeks are
  7 days from that date, so pick a Monday if you want calendar weeks.
- `--weeks` sets the horizon (default 13).
- `--minimum-cash` is the covenant or the board's floor.
- Scenario levers: `--collection-lag-days`, `--collection-rate` (percent of AR
  expected to arrive at all), `--payment-lag-days` (stretch payables), and
  `--spend-change-pct` (applies to recurring **outflows** only; payroll and
  contracted AP are not flexed).
- `--detail` lists every scheduled item under its week — use it when someone
  challenges a number.
- `--json` emits the weekly series and the completeness figures.

Exit codes: `0` the forecast reconciles to the input files, `1` it does not
(some input row is unaccounted for), `2` bad input naming the file, line, and
column. `examples/run.sh` runs a base case, a downside scenario, an item-level
view, and a rejected file.

## Scheduling rules

- An AR invoice is expected on `expected_date` if present, otherwise
  `due_date`, then `--collection-lag-days` is added. `--collection-rate` scales
  the amount; the remainder is reported as an expected shortfall and never
  appears in a week.
- AP works the same way with `--payment-lag-days` and no haircut.
- Anything still dated before `--as-of` after the lag — genuinely overdue — is
  assumed to settle in **week 1**. That is a choice; if an overdue invoice is
  disputed or the customer is gone, set its `expected_date` or exclude it.
- Recurring items generate occurrences from `start_date` until `end_date` or
  the end of the horizon. Monthly and quarterly keep the day of month, clamped
  to the last day of shorter months.
- Anything falling after the horizon is listed under "items outside the
  horizon" rather than dropped.

## Reading the output

- **Weekly cash flow** — opening, the five movement columns, net, closing, and
  a flag when the closing balance is under the covenant. Read the flag column
  first.
- **Runway and covenant** — the lowest week, the first breach with its
  shortfall, and runway. When cash does not run out inside the horizon, the
  runway is extrapolated from the average net of the last four weeks and is
  labelled as an extrapolation. Treat that number as indicative: late-horizon
  weeks are usually the thinnest part of the AR book.
- **Completeness and reconciliation** — every input file's total split across
  in horizon, after horizon, before the start, and not expected. Residuals must
  be `0.00`. This is the proof that nothing was dropped.

When presenting to a human: lead with the first breach week and the shortfall,
then the lowest balance and when it occurs, then the two or three items driving
that week. Give the scenario assumptions every time — a 13-week without its
collection assumptions stated is not a forecast, it is a guess.

## Limits

- Direct method only: it schedules known receipts and payments. It does not
  derive cash from a P&L or balance sheet, and it does not forecast new sales
  beyond what you put in the recurring file.
- The collection rate is a flat haircut across all AR. It is not a bad-debt
  model and does not vary by customer or ageing bucket. Per-invoice control is
  via `expected_date` and by excluding rows.
- Overdue items land in week 1. That is optimistic for a stressed book; check
  the oldest invoices before you believe week 1.
- No interest, no FX, no revolver or facility draws, no debt covenant beyond
  the single minimum-cash test, and no tax computation — tax payments must be
  entered as recurring or one-off items.
- Single currency and a single cash pool. Multiple bank accounts must be summed
  into one opening balance, or run separately.
- Weeks are 7 days from `--as-of`; there is no banking-day or holiday logic, so
  a payment dated on a weekend stays in that week.
- `--spend-change-pct` applies uniformly to recurring outflows. It does not
  know which of them are actually discretionary; review the recurring file
  before quoting a savings scenario.
- The extrapolated runway beyond the horizon assumes the last four weeks
  repeat. If you need more than the horizon, extend `--weeks` with real data
  rather than relying on that number.
