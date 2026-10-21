---
name: budget-variance-commentary
description: Compare actual to budget by department and account, apply absolute and percentage materiality thresholds, classify each material variance as volume, timing, or permanent where the data supports it, and draft reviewable commentary for the material lines only.
---

# Budget variance commentary

The monthly "why is marketing over?" exercise, done consistently. It applies
your materiality policy rather than eyeballing the big numbers, classifies each
material variance from evidence in the file, and drafts the sentence a budget
owner will edit rather than a blank box they will ignore.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `budget-variance-commentary`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Monthly or quarterly management accounts: the variance section of the pack.
- Budget-owner review meetings: one page per department, with the immaterial
  noise aggregated instead of debated.
- Reforecasting: the timing vs permanent split is what tells you which
  variances to roll forward.
- Board reporting, where every commented number has to trace to a line.

## Inputs

One CSV, one row per department and account for the period. Required columns:
`department`, `account_code`, `account_name`, `account_type`, `actual`,
`budget`. The optional columns are what make classification possible:
`ytd_actual`, `ytd_budget`, `prior_actual`, `prior_budget`, `actual_units`,
`budget_units`, `unit_name`, `owner`, `classification`, `note`. Full
specification in `DATA_CONTRACT.md`.

### Gathering the inputs through Type integrations

- **Actuals**: export the period P&L by class/department from QuickBooks, Xero,
  or NetSuite at account level. Do not use a summarised report — the commentary
  is only as granular as the export.
- **Budget**: the budget-vs-actual report from the same system if budgets are
  loaded there, otherwise the planning sheet from the connected Google Sheets
  or Excel source. Match on `department` + `account_code`.
- **Year to date and prior period**: the same reports run for the wider range
  and the preceding period. These two pairs of columns are what separate a
  timing variance from a run-rate change; without them most lines come back
  `unclassified`, which is the honest answer.
- **Units**: headcount from the HRIS or payroll provider, customers from the
  billing system, MQLs from the marketing platform, orders from the commerce
  platform. Whatever genuinely drives the account.
- **Owner**: the budget owner. It goes into the drafted sentence so the review
  request has a name on it.

## Running it

```bash
python3 scripts/variance_commentary.py \
  --actuals examples/august_actuals.csv \
  --as-of 2026-08-31 \
  --materiality-abs 5000 \
  --materiality-pct 10 \
  --control-operating-variance -93420.00
```

- `--materiality-abs` and `--materiality-pct` set the thresholds;
  `--materiality-rule and` (default) requires both, `or` requires either. Use
  `and` for a short, senior-audience pack; `or` for a controller's working
  review. A line with a zero budget is judged on the absolute test alone.
- `--volume-share` (default 60) is how much of a variance the volume effect
  must explain before the line is called volume-driven.
- `--timing-share` (default 50) is how small the year-to-date variance must be,
  relative to the period variance, to read as phasing.
- `--control-operating-variance` is the operating income variance from the P&L.
  Supply it: it is the only external check in this skill.
- `--as-of` is required and sets the period label; nothing reads the clock.
- `--json` emits every material line with its evidence and drafted commentary.

Exit codes: `0` ties, `1` the detail does not reconcile to the control total,
`2` bad input naming the line and column. `examples/run.sh` runs four scenarios.

## How classification works

Evaluated in this order; the first rule that fits wins:

1. **Stated** — a `classification` value in the file always wins, and the
   report labels the basis `stated in the file`.
2. **Volume** — needs `actual_units` and `budget_units`. The volume effect is
   `(actual units - budget units) x budget rate`; the rate/mix effect is the
   residual, so the two always sum to the variance exactly. If the volume
   effect explains at least `--volume-share` of the variance, the line is
   volume-driven.
3. **Timing** — needs YTD columns. If the YTD variance runs the other way, or
   is smaller than `--timing-share` of the period variance, the swing is not
   carrying through: phasing.
4. **Permanent** — the YTD variance is at least as large and in the same
   direction as the period variance (optionally confirmed by the prior period),
   or, with no YTD data, the prior period ran the same way.
5. **Unclassified** — the file does not support a claim. The drafted sentence
   says so and asks the owner, rather than inventing a cause. This includes a
   line whose YTD variance runs the same way at between `--timing-share` and
   100% of the period variance: part of the swing is carrying through and part
   is not, so it is neither clean phasing nor a clean run-rate change. The
   prior-period fallback in rule 4 is never used when YTD data is present.

## Reading the output

- **P&L summary and by-department** — where the operating income variance came
  from. `op income effect` flips the sign for cost lines, so a positive number
  always means "good for profit".
- **Material variances** — sorted by effect on operating income, with the
  classification and the basis it rests on.
- **Immaterial lines** — count, net variance, and the three biggest, so nobody
  can say a line was quietly dropped. Watch this block: under the `and` rule a
  large variance on a large budget can sit here. That is the rule working, but
  check it before the meeting.
- **Reconciliation** — material effect plus immaterial effect equals the
  operating income variance, and equals your control total.
- **Draft commentary** — one paragraph per material line. It is a draft. Edit
  it, and never issue the `unclassified` ones without an answer from the owner.

## Limits

- It explains variances arithmetically; it does not know *why* anything
  happened. Classification is an inference from the columns you supplied, and
  the report always states which column drove it.
- Volume analysis is a two-factor split (volume and rate/mix) against a single
  driver per line. It is not a full price-volume-mix bridge across a product
  portfolio.
- Timing vs permanent uses year-to-date and prior-period comparisons only.
  There is no forecast, no seasonality model, and no full-year projection.
- One period per run, one currency, and no consolidation or intercompany
  elimination.
- Materiality here is a variance-size rule, not an audit materiality framework,
  and it is applied per line, not to aggregated risk.
- Rows must be pre-aggregated to one line per department and account —
  duplicates are rejected rather than summed, so that nothing is combined
  behind your back.
- The internal reconciliation is an invariant check on the script's own
  aggregation. Only `--control-operating-variance` tests the data against
  something outside the file, so supply it.
- Draft commentary is not approved commentary. Nothing here should reach a
  board pack without a named human reviewing it.
