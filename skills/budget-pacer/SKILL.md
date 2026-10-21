---
name: budget-pacer
description: Project month-end spend from a trailing run rate across campaigns and clients, flag over- and under-delivery beyond a tolerance, and recommend per-campaign daily budgets that reconcile exactly to each client's committed total.
---

# Budget pacer

A mid-flight pacing check. It sums spend to date, computes a trailing run rate,
projects where the month lands, and — for every client outside tolerance —
recommends a new daily budget per campaign that adds up to exactly what is left
of the commitment. Where the math cannot work inside the campaigns' own floors
and caps, it says so instead of producing a number nobody can implement.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `budget-pacer`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- The mid-month (or weekly) pacing review across a book of clients.
- Before promising a client that their committed budget will be delivered.
- When one client's spend spikes and the reallocation needs to be defensible.
- End of month, as a final reconciliation (run it with `--as-of` on the last
  day; it reports the final variance and skips recommendations).

For a month-end client-facing recap, use `client-report-builder`. For account
hygiene, use `ad-account-audit`.

## Gathering the inputs

Three CSVs, described column by column in `DATA_CONTRACT.md`. The script reads
local files only and never touches an ad platform directly.

1. **`campaigns.csv`** — one row per client/campaign with status, the current
   daily budget, and the minimum and maximum daily budget the agency is willing
   to run. Pull status and current budget from the connected ad platforms; the
   floor and ceiling are agency judgment. Ask the user for them — a missing
   floor produces recommendations that starve a campaign out of learning, and a
   missing ceiling produces recommendations the platform will never spend.
2. **`daily_spend.csv`** — daily cost per campaign for the month to date. Export
   with a day dimension from each connected platform. **Stop the export at the
   pacing date**: a row dated after `--as-of` is a hard error, because it means
   the export window and the pacing date disagree.
3. **`commitments.csv`** — the committed monthly media spend per client, from
   the media plan or insertion order. Never infer it from budgets.

Every client in `campaigns.csv` must have a commitment row for the month.

## Running it

```bash
python3 scripts/pace_budgets.py \
  --campaigns data/campaigns.csv \
  --daily-spend data/daily_spend.csv \
  --commitments data/commitments.csv \
  --month 2026-09 \
  --as-of 2026-09-18
```

- `--as-of` is required and must fall inside `--month`. It defines days elapsed,
  days remaining and the end of the run-rate window.
- `--run-rate-days` (default 7) is the trailing window. The window never reaches
  before the first of the month; early in the month it shortens automatically
  and the report says so.
- `--tolerance` (default 5) is the percent variance against the commitment that
  counts as on pace.
- `--json` for machine output.

## Reading and presenting the output

- **Client pacing** is the triage table. `% delivered` versus `expected to date`
  shows where the client stands now; `projected` versus `committed` shows where
  the current run rate lands.
- **Recommended daily budgets** allocates the remaining budget across active
  campaigns in proportion to their current daily budgets, clamped to each
  campaign's floor and ceiling, with the clamped remainder redistributed across
  the campaigns still free to move. The `Bound` column marks campaigns pinned at
  a limit.
- **Reconciliation** under each client is the proof line: spend to date plus the
  recommended daily total times the days remaining, against the commitment. A
  residual of a few cents is rounding; anything larger is flagged and explained.
- **Actions** is ordered by how far each client is from its commitment. Two
  failure modes get their own language: *ceiling* (the caps cannot absorb the
  remaining budget — raise caps, add campaigns, or renegotiate the commitment
  now) and *floor* (the minimums overshoot what is left — pause something).
- Apply the changes in the platforms, then re-run the next pacing cycle. The
  recommendation assumes the new daily budget is applied on the first of the
  remaining days; applying it late leaves a shortfall the next run will pick up.

## Limits

- Pacing only. It has no view of performance, so it will happily recommend
  scaling a campaign that is losing money. Pair it with `ad-account-audit` or
  `client-report-builder` before acting on a large increase.
- The projection is a straight-line trailing average. It does not model
  weekday/weekend shape, seasonality, auction competition, learning phases,
  promotional spikes, or platform pacing behavior. A short `--run-rate-days`
  tracks recent changes faster but is noisier.
- It assumes a campaign can actually spend the budget it is given. Platforms
  routinely under-deliver against a raised cap; the skill flags the ceiling
  case but cannot predict delivery.
- Daily budgets are recommended in whole cents; platform-specific rounding and
  over-delivery allowances (some platforms spend up to twice a daily cap on any
  one day) are not modeled.
- Paused campaigns never receive budget. Their spend still counts toward the
  commitment, and the report notes it.
- Single currency, one month per run, and no fee or management-cost modeling —
  `committed_spend` is media only.
