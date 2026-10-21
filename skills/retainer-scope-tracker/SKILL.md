---
name: retainer-scope-tracker
description: Compare delivered work against contracted retainer scope for a month, flagging scope creep and out-of-scope work, computing effective hourly rate and delivery margin per client, and ranking which retainers to renegotiate and which clients are being under-served.
---

# Retainer scope tracker

Answers the two questions every agency owner asks at month end: which clients
are consuming more than they bought, and which retainers are no longer worth
the team's time. Output is a portfolio table, a per-client scope variance
breakdown, a ranked renegotiation list with two concrete levers per client, and
a list of clients who were under-served.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `retainer-scope-tracker`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Month-end close, before invoicing and before the next month's capacity plan.
- Before a renewal conversation, to bring numbers instead of impressions.
- When the team feels underwater and the cause needs to be located: one client,
  one deliverable, or systemic underpricing.

## Gathering the inputs

Five CSVs. Full column specs are in `DATA_CONTRACT.md`. Everything is read from
local files; the script makes no network calls and never sees a credential.

1. **`contracts.csv`** — one row per client per month: retainer fee, contracted
   hours, renewal date. Source: the signed statement of work, or the billing
   system (the connected accounting or invoicing integration). Keep historical
   months in the file; only rows matching `--month` are read.
2. **`scope.csv`** — the contracted deliverables for that month, with quantity
   and budgeted hours each. Source: the SOW's scope table. If the SOW has no
   hour estimates per deliverable, allocate the contracted hours across
   deliverables with the account lead before running this; the comparison is
   only as good as that allocation.
3. **`time_entries.csv`** — logged time from the time-tracking or project tool
   (Harvest, Toggl, Clockify, Asana, Jira, Monday). One row per entry, with the
   client, person, deliverable and hours. The `deliverable` value must use the
   same names as `scope.csv` — reconcile the label sets before running, since
   an unmatched name is reported as out-of-scope work.
4. **`roster.csv`** — internal cost per hour by person. Source: payroll or the
   ops spreadsheet. Use fully loaded cost (salary plus employer burden) divided
   by productive hours, or the standard cost the finance lead already maintains.
   This file is sensitive; keep it out of anything client-facing.
5. **`deliverables.csv`** — what actually shipped, with quantities, from the
   project tool. This is what detects "we sent two reports for a
   one-report scope" even when the hours look fine.

## Running it

First run: `bash examples/run.sh` runs the tracker against the bundled sample
agency in `examples/data/` and prints the report you should expect. The
equivalent command is below; to run it on your own exports, replace each
`examples/data/` path with wherever you saved the five CSVs.

```bash
python3 scripts/track_scope.py \
  --contracts examples/data/contracts.csv \
  --scope examples/data/scope.csv \
  --time-entries examples/data/time_entries.csv \
  --roster examples/data/roster.csv \
  --deliverables examples/data/deliverables.csv \
  --month 2026-09 \
  --as-of 2026-10-05
```

- `--as-of` is required and is used only for renewal timing (how long until each
  contract renews, against `--lead-days`, default 45).
- `--target-margin` (default 55) is the delivery-margin bar. Set it to the
  agency's actual target; it drives both the MARGIN flag and the proposed fee.
- `--over-tolerance` (default 10) and `--under-tolerance` (default 15) are the
  percentages of hours variance that trigger SCOPE CREEP and UNDER-DELIVERED.
- `--rate-drop` (default 15) is how far the effective rate may fall below the
  contracted rate before the RATE flag fires.
- `--round-to` (default 100) rounds proposed fees up to a sane number.
- `--json` for machine output.

## Reading and presenting the output

- The **portfolio table** is the internal view. It contains cost and margin per
  client and must not be sent to a client.
- **Scope variance detail** is the evidence. The deliverable table for a single
  client is the right attachment for an internal renewal brief, with the cost
  column removed.
- **Clients to renegotiate** gives two levers per client and says which one is
  sufficient. Lever 1 holds scope at the contracted hours and shows the margin
  that would result; lever 2 reprices at what is actually being delivered. When
  lever 1 alone clears the target, the problem is delivery discipline, not
  price — lead with that internally before proposing an increase.
- **Clients to re-engage** matters as much as the creep list. A client paying
  for hours that were never used is a churn risk who currently looks profitable.
- Keep the **Method and caveats** section attached to any internal circulation
  so the margin figure is not mistaken for net profit.

## Limits

- Delivery margin only: logged hours at internal cost. It excludes overhead,
  software, freelancers billed outside the roster, media and other pass-through
  costs, so real account profitability is lower than what this reports.
- It trusts the time log. Unlogged, late-logged or misattributed hours flow
  straight into the wrong client's margin, and the skill cannot detect that.
- One month at a time. There is no trend, no seasonality adjustment, and no
  rollup across months; run it monthly and keep the outputs.
- Proposed fees are arithmetic (`cost ÷ (1 − target margin)`), not commercial
  advice. They ignore competitive position, switching costs, relationship value
  and the client's own budget cycle.
- Deliverable matching is exact string matching on names. Renaming a deliverable
  mid-month reads as out-of-scope work.
- Single currency, no allocation of shared or non-client internal time.
