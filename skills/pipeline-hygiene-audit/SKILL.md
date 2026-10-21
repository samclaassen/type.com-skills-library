---
name: pipeline-hygiene-audit
description: Audits a CRM deal export for stale deals, past close dates, missing amounts or owners, skipped stages, deals stuck beyond their stage median, and duplicate accounts, then emits a prioritized per-owner remediation list.
---

# Pipeline Hygiene Audit

Turns a raw opportunity export into a short, ranked list of records each rep has
to fix before the next forecast call. Every finding is a deterministic rule with
a visible severity weight, so a manager can defend the list line by line.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `pipeline-hygiene-audit`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Before a weekly forecast, QBR, or board pipeline review.
- After a CRM migration, a territory change, or a bulk import, when nobody
  trusts stage or close-date data.
- When "the pipeline number" and the reps' own view of their deals disagree and
  you need to know how much of the gap is just bad data.

Do not use it to forecast, score deal quality, or predict close probability. It
judges the *record*, not the deal.

## Inputs

`DATA_CONTRACT.md` has the exact columns, types, and units. You need:

1. `deals.csv` — one row per opportunity (open and closed).
2. `stages.csv` — the pipeline's stage names and their order.
3. `stage_history.csv` — optional; enables the stage-skip check.
4. `config/rules.json` — thresholds and severity weights (ships with defaults).

### Gathering the inputs through Type integrations

Use whichever CRM the user has connected; do not ask them to build the file by
hand if a connector can produce it.

- **Salesforce**: query `Opportunity` for `Id, AccountId, Account.Name,
  Account.Website, Owner.Name, StageName, Amount, CreatedDate, CloseDate,
  LastActivityDate, IsClosed, IsWon`. Stage order comes from the
  `OpportunityStage` object (`SortOrder`). Stage history comes from
  `OpportunityHistory` (`OpportunityId, StageName, CreatedDate`).
- **HubSpot**: export deals with `hs_object_id`, `dealstage`, `amount`,
  `createdate`, `closedate`, `notes_last_updated`, `hubspot_owner_id`, and the
  associated company name/domain. Stage order comes from the deal pipeline
  definition; stage history from the `dealstage` property history.
- **Attio**: export the Deals object with the linked Company record's name and
  domain, plus the stage attribute's historical values.
- Anything else (BigQuery/Snowflake mirror of the CRM, a spreadsheet the RevOps
  team maintains): map the columns to the contract before running.

Normalize the export to the contract's column names and `YYYY-MM-DD` dates.
`stage_entered_date` must be the date the deal entered its *current* stage; if
the CRM does not expose it, derive it from the last stage-history event.

## Running it

```bash
python3 scripts/pipeline_audit.py \
  --deals path/to/deals.csv \
  --stages path/to/stages.csv \
  --stage-history path/to/stage_history.csv \
  --config config/rules.json \
  --as-of 2026-09-22
```

- `--as-of` is required and is the only source of "today". Use the user's
  reporting date, not the wall clock.
- `--stage-history` is optional. Without it the stage-skip check is reported as
  not evaluated rather than silently passing.
- `--json` emits the same analysis as structured JSON for downstream use.
- Bad input exits `2` with a message naming the file, row, and column. Fix the
  export and rerun; never patch around it by editing the script.

See `examples/run.sh` for a complete working invocation.

## Tuning

All thresholds live in `config/rules.json`, nothing is buried in the code:

| Key | Meaning |
| --- | --- |
| `stale_activity_days` | Days without logged activity before a deal is stale. |
| `stuck_multiplier` | Multiple of the stage median that counts as stuck. |
| `stuck_min_sample` | Minimum open deals in a stage before its own median is trusted; below this the all-stage median is used. |
| `min_amount_usd` | Amount floor; below it the deal counts as unsized. |
| `amount_reference_usd`, `amount_weight_cap` | Amount weight = `1 + min(amount / reference, cap)`. |
| `severity_points` | Points per finding type. |
| `legal_suffixes` | Suffix tokens stripped before duplicate-name comparison. |

A finding's score is `severity points x amount weight`, so a $500k deal with a
past close date outranks a $5k deal with the same problem. Raise
`stale_activity_days` for long enterprise cycles; lower it for transactional
teams.

## Reading and presenting the output

The report has five parts:

1. **Summary** — how much open pipeline value carries at least one finding. This
   is the number to lead with: "$1.17M of $1.65M open pipeline has a data
   problem."
2. **Findings by type** — where the systemic problem is. A large
   `stale_activity` count is a process failure, not 30 individual mistakes.
3. **Days-in-stage medians** — the basis for `stuck_in_stage`, printed so the
   threshold is auditable. Quote it when a rep disputes a flag.
4. **Duplicate account clusters** — hand these to whoever owns CRM admin; reps
   usually cannot merge accounts themselves.
5. **Remediation by owner** — the deliverable. Send each owner only their block.
   Every line carries the finding, its score contribution, the evidence, and a
   suggested action.

When presenting, lead with the summary, name the top two finding types, and give
each owner a count and a deadline. Do not paste the whole report into a channel;
it is a work list, not a dashboard.

## Limits

- It cannot tell a genuinely slow enterprise deal from a neglected one. Stuck
  and stale flags are prompts for a human judgment call, not verdicts.
- Duplicate detection is normalized-name and domain matching only. It will miss
  rebrands and acquisitions ("Acme" vs "Acme Industrial Holdings" after a name
  change) and will flag legitimate subsidiaries that share a domain.
- The stage-skip check needs a stage-history export. Without one it is reported
  as not evaluated.
- Medians are computed from the deals in the file. A small or filtered export
  produces an unreliable median; `stuck_min_sample` limits but does not remove
  that risk.
- It does not write back to the CRM, close or re-date anything, or contact
  owners. It produces the list; a human acts on it.
- Amounts are treated as a single currency (USD by contract). Multi-currency
  pipelines must be converted before export.
