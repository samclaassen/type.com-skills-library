---
name: saas-metrics-pack
description: Compute the full MRR movement waterfall (new, expansion, contraction, churn, reactivation) from a subscription export plus NRR, GRR, logo churn, quick ratio, ARPA, CAC and CAC payback, reconciling opening plus movements to closing exactly and failing if it does not tie.
---

# SaaS metrics pack

One command turns a subscription export into the numbers a board deck, an
investor update, or a monthly business review actually needs — and refuses to
produce them if the waterfall does not tie.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `saas-metrics-pack`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Monthly or quarterly MRR movement reporting.
- Board pack prep: NRR and GRR with an auditable derivation behind them.
- Diligence: someone asks "how did you calculate churn?" and you need to show
  the per-customer classification, not a spreadsheet formula.
- Sanity-checking the billing system's own dashboard. Pass its reported closing
  MRR as `--expected-closing-mrr` and see whether your definition agrees.

## Inputs

One CSV, one row per subscription (not per invoice, not per month). Required
columns: `customer_id`, `subscription_id`, `mrr`, `start_date`, `end_date`.
Optional: `customer_name`, `plan`. Full specification in `DATA_CONTRACT.md`.

The file must contain history, not just currently active subscriptions: a plan
change is two rows (the old one ending, the new one starting), and a
reactivation is only detectable if the customer's earlier, cancelled
subscription is present.

### Gathering the inputs through Type integrations

- **Stripe**: list subscriptions including cancelled ones. Map `mrr` to the
  normalised monthly amount (annual plans divided by 12, quarterly by 3),
  `start_date` to the subscription start, and `end_date` to `ended_at` /
  `canceled_at`. Exclude one-off invoice items, taxes, and usage overage unless
  you intend them to be recurring — and say which choice you made.
- **Chargebee / Recurly / Paddle**: the subscription export already carries
  plan amount, activation date, and cancellation date.
- **From the GL**: if billing lives in QuickBooks/Xero/NetSuite, export the
  recurring invoice lines per customer and derive one row per contract period.
- **Sales and marketing spend** for CAC comes from the GL: the S&M department
  total for the acquisition period (usually the prior month or quarter).
- Export first, then run the script on the file. No credentials in the CSV.

## Running it

```bash
python3 scripts/mrr_waterfall.py \
  --subscriptions examples/subscriptions_august.csv \
  --period-start 2026-08-01 \
  --as-of 2026-08-31 \
  --sales-marketing-spend 18500 \
  --gross-margin-pct 80 \
  --expected-closing-mrr 24380.00 \
  --top 6
```

- `--period-start` is the opening measurement date, `--as-of` the closing one.
  Both are required; nothing reads the clock.
- `--sales-marketing-spend` and `--gross-margin-pct` (default 100) drive CAC
  and CAC payback. Omit the spend and those two rows report `n/a`.
- `--expected-closing-mrr` is the control total from your billing system. When
  it is supplied and does not match, the run fails.
- `--top` sets how many movers to list. `--json` emits everything for reuse.

Exit codes: `0` ties, `1` does not tie, `2` bad input with the offending line
and column named. `examples/run.sh` runs four scenarios including both failures.

## Definitions used

- A subscription is **active on a date** when `start_date <= date` and
  `end_date` is blank or `end_date > date`. `end_date` is the first day of
  non-service, which is how Stripe's `ended_at` behaves.
- Customer MRR is the sum of their active subscriptions on the measurement date.
- **New**: zero opening MRR, positive closing MRR, no subscription that started
  before the opening date.
- **Reactivation**: zero opening MRR, positive closing MRR, and an earlier
  subscription that started before the opening date.
- **Churn**: positive opening MRR, zero closing MRR (the full opening amount).
- **Expansion / contraction**: positive on both dates, the difference.
- Customers at zero on both dates (signed and cancelled inside the period)
  appear in no bucket and are listed separately, because they quietly distort
  logo counts.
- `NRR = (opening + expansion - contraction - churn) / opening`
- `GRR = (opening - contraction - churn) / opening`
- `Logo churn = churned logos / opening logos`
- `Quick ratio = (new + expansion + reactivation) / (contraction + churn)`
- `CAC = spend / new logos` (reactivations excluded)
- `CAC payback = spend / (new MRR x gross margin)`, in months

## Reading the output

Work down the report: the waterfall gives the story (what grew, what leaked),
the reconciliation block proves it, retention metrics contextualise it, and the
movers table names the accounts behind the biggest swings.

The reconciliation block is the part to look at first. It compares the
waterfall build-up against opening and closing balances summed independently
from the raw rows, and against your billing system's number if you supplied it.
All residuals must be `0.00`.

When presenting to a human: lead with closing MRR and net new MRR, then NRR,
then the one or two accounts that moved the number. Always state the date pair
the measurement used — "August" means nothing without it. Never quote NRR from
this pack without saying whether reactivations were in scope (they are not).

## Limits

- Single currency. There is no FX conversion; convert to your reporting
  currency before exporting or the totals are meaningless.
- MRR must already be normalised to a monthly amount. The script does not
  amortise annual contracts, prorate mid-month changes, or model usage-based
  billing. It measures at two instants; it does not compute revenue earned.
- A plan change expressed as a single mutated row (rather than an end plus a
  start) is invisible: the customer will look flat. Export history.
- Trials, discounts, taxes, and one-off fees are whatever your export says they
  are. The script cannot tell a $0 trial from a churned account beyond the
  rules above.
- It does not compute LTV, cohort curves, or payback by channel, and the CAC it
  reports is blended: one spend number over all new logos, with no attribution
  and no lag between spend and signature beyond the period you choose.
- Logo churn is unweighted; one $50 logo counts the same as a $50k logo.
- Amounts are rounded to 2 dp only for display. If displayed movements do not
  visually add up because of that rounding, the report says so explicitly; the
  tie check always uses unrounded values.
