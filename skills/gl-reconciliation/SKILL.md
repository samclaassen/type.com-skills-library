---
name: gl-reconciliation
description: Reconcile a bank statement or payment-processor payout report against the general ledger using staged matching — exact amount and date, amount within a date tolerance, and many-to-one batch matching for aggregated payouts — and bridge the unmatched items back to the control totals.
---

# GL reconciliation

Matches two ledgers, lists what is left over on each side, and proves that the
leftovers explain the whole difference between the two control totals. If they
do not, the script says so and exits non-zero rather than rounding the gap away.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `gl-reconciliation`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Monthly bank reconciliation: bank statement vs the GL cash account.
- Stripe / PayPal / Shopify Payments payout reconciliation: the payout that
  hits the bank is one line; the GL has the individual settled charges. That is
  what the batch stage is for.
- Clearing and suspense account clean-up: two lists that should agree.
- Any "these two reports should tie and they don't" question, where the answer
  needs to be an itemised bridge rather than a plugged number.

## Inputs

Two CSVs with the same four required columns: `id`, `date`, `amount`,
`description` (plus optional `reference`). Full specification, including the
sign convention, is in `DATA_CONTRACT.md` — **read the sign convention before
exporting**, because a flipped sign on one side matches nothing.

### Gathering the inputs through Type integrations

- **Ledger A (bank)**: download the statement CSV from the bank, or pull it via
  the connected banking / Plaid-style integration. Keep one account per file.
- **Ledger A (processor)**: use the connected Stripe integration's payout list
  (`amount` = the net payout that lands in the bank, `date` = arrival date). If
  you reconcile gross charges instead, the fees become unmatched items — decide
  which you want before you export.
- **Ledger B (GL)**: export the account transaction detail for the same period
  and the same account from QuickBooks, Xero, or NetSuite. Use the account
  register / general ledger detail report, not the trial balance.
- **Control totals**: closing balance per the bank statement (`--balance-a`) and
  closing balance per the GL account (`--balance-b`). Supply both, or neither.
- Both exports must cover exactly the same date range.
- Credentials never go into these files or into this script. Export first,
  reconcile locally.

## Running it

```bash
python3 scripts/reconcile.py \
  --ledger-a examples/bank_august.csv \
  --ledger-b examples/gl_cash_1000_august.csv \
  --label-a "Meridian Bank 8841" \
  --label-b "GL 1000 Cash - operating" \
  --as-of 2026-08-31 \
  --balance-a 1081449.52 \
  --balance-b 1083430.75 \
  --date-tolerance 5 \
  --batch-window 5 \
  --amount-tolerance 0.05
```

The last three flags are not optional for this example, and they are not the
defaults. The August data contains a check (10241) that cleared the bank five
days after it was booked, and a Stripe payout that is $0.02 off the four GL
charge postings it covers. With the defaults (`--date-tolerance 3`,
`--amount-tolerance 0.00`) the bridge still ties, but the check and the payout
fall out of matching and the run reports 12 open items instead of 5. Pass
tolerances deliberately for your own data rather than copying these values.

Key arguments:

- `--as-of` labels the reconciliation. Required; nothing reads the clock.
- `--date-tolerance` (default 3) is the stage 2 window for same-amount items
  that cleared on a different day.
- `--batch-window` (default 5) and `--amount-tolerance` (default `0.00`) govern
  stage 3. A small amount tolerance (a few cents) absorbs rounding inside an
  aggregated payout; anything larger starts hiding real differences.
- `--batch-direction` is `b-to-a` (many GL rows to one bank row), `a-to-b`,
  `both` (default), or `off`.
- `--batch-max-items` (default 25) caps how many rows may form one group.
- `--max-rows` truncates the matched-items display only; totals are unaffected.
- `--json` emits the full result, including the bridge, for automation.

### Choosing tolerances

The defaults are deliberately strict: nothing is matched on a different day
beyond three days, and nothing is matched unless it agrees to the cent. Loosen
them per account, based on how that account actually behaves, and record the
values used in the reconciliation working paper.

- **Bank vs GL cash**: `--date-tolerance` should cover the longest normal
  clearing lag you accept — typically 3-5 business days for checks and ACH.
  Keep `--amount-tolerance` at `0.00`; a bank line either equals the GL
  posting or it does not.
- **Processor payouts (Stripe, PayPal, Shopify Payments)**: set
  `--batch-window` to the payout schedule (e.g. 2-7 days for a rolling
  payout) and allow a few cents of `--amount-tolerance` (`0.01`-`0.05`) to
  absorb per-charge rounding when the GL is posted from gross or summarised
  charges. Every batch group's `diff` is shown in the matched-items table and
  carried into the bridge, so the rounding stays visible.
- **Never raise `--amount-tolerance` to make a payout match.** If a payout is
  off by more than rounding, it is a fee, refund, or chargeback that belongs
  in the GL — reconcile net payouts to net postings, or book the fees, rather
  than widening the tolerance.
- If a loosened setting changes the result, rerun at the defaults and compare
  the open-item counts before signing off.

Exit codes: `0` the bridge ties, `1` it does not (the residual is printed),
`2` bad input, with the file, line, and column named.

`examples/run.sh` runs four scenarios end to end, including both failure paths.

## How the matching works

1. **Exact** — same `amount` and same `date`. Duplicates are consumed in
   `(date, amount, id)` order, so a month with two identical payroll runs pairs
   them predictably.
2. **Date tolerance** — same `amount`, date within `--date-tolerance`. The
   closest date wins; ties break on the earlier date, then on `id`.
3. **Batch** — one row on one side against two or more rows on the other side
   inside `--batch-window`, with the same sign, summing to the target within
   `--amount-tolerance`. Solved with a bounded, deterministic subset-sum search.

Each row is used at most once, and later stages only see what earlier stages
left behind.

## Reading the output

- **Matching stages** — how much was explained by each stage. Heavy reliance on
  stage 2 or 3 is itself a finding: the GL is being posted on different dates,
  or gross rather than net.
- **Unmatched -- ledger A** — on the bank but not in the GL: fees, interest,
  chargebacks, unrecorded transfers. These usually need a journal entry.
- **Unmatched -- ledger B** — in the GL but not on the bank: outstanding checks,
  deposits in transit. These usually need nothing but time — unless they are
  stale, which you should call out by age.
- **Reconciling items** — the bridge: control total A, plus GL-only items, less
  bank-only items, less differences inside matched groups, equals control
  total B. The residual must be `0.00`.

A non-zero residual is almost always one of: opening balances that never
agreed (a prior-period break), exports covering different date ranges, or rows
filtered out upstream. Say which one you suspect, and never adjust the data to
force a tie.

When presenting to a human: lead with the residual (tied or not), then the
count and value of open items on each side, then name the individual items that
need a journal entry, with the ones needing action listed by amount.

## Limits

- It does not post journals, void checks, or write anything back to the GL.
- Matching is amount-and-date driven. It does not read descriptions, reference
  numbers, or memo fields, so it cannot tell two same-day, same-amount items
  apart — it just pairs them in a fixed order. Reference-based matching is not
  implemented.
- Batch matching finds *a* subset that sums to the target, not necessarily the
  economically correct one. Where several subsets fit, it takes the smallest
  gap, then the fewest rows, then the earliest rows. Always eyeball a batch
  group before relying on it.
- The subset search is bounded (`--batch-max-items`, and an internal state cap).
  Very large same-window candidate pools may leave a payout unmatched rather
  than searching forever. That is a deliberate, visible failure.
- One currency per run. No FX translation, no multi-currency payouts.
- Amounts must carry at most 2 decimal places; 3-decimal currencies and 4-dp FX
  rates are rejected rather than silently rounded.
- The bridge assumes the two exports cover the same period and that opening
  balances agreed. It surfaces a break, it does not attribute it.
- It does not age open items or flag stale outstanding checks.
