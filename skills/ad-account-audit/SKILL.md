---
name: ad-account-audit
description: Audit a paid search or paid social account export for conversion-tracking gaps, budget pacing outliers, disapproved and limited ads, naming-convention drift, negative-keyword coverage and stale creative, returning severity-ranked findings with the dollars each one puts at issue.
---

# Ad account audit

Runs six hygiene checks against four local exports and returns a ranked findings
list. Every finding names the campaign, shows the evidence it was derived from,
attaches a dollar figure, and states the fix.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `ad-account-audit`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Onboarding a new account and needing a defensible first-week findings list.
- A recurring (monthly or quarterly) hygiene pass on accounts under management.
- Performance dropped and the first question is whether the account is broken
  rather than the market.
- Preparing a pitch: run it on a prospect's export to ground the conversation in
  their numbers. (`new-business-pitch-builder` turns the opportunity into a
  proposal; this skill finds the problems.)

## Gathering the inputs

Four CSVs, all for the same account and the same date range. Exact columns are
in `DATA_CONTRACT.md`. The script reads local files only — it never calls an ad
platform and never handles credentials.

1. **`campaigns.csv`** — campaign report for the period, with status, daily
   budget, spend and conversions. Google Ads: Campaigns view with the date range
   set. Meta Ads: Campaigns tab with Amount Spent, Results and Budget columns.
2. **`ads.csv`** — ad-level export including `ad_status`, `approval_status` and
   `last_modified`. In Google Ads the policy column is "Status details"/"Policy
   details"; map it to `APPROVED`, `LIMITED`, `DISAPPROVED` or `UNDER_REVIEW`.
   In Meta, map "Active", "Rejected" and "In Review" to the same four values.
3. **`keywords.csv`** — targeting keywords and negative keywords, with match
   type. Use `list_type=negative` for negatives and the literal campaign name
   `ACCOUNT` for shared/account-level negative lists.
4. **`search_terms.csv`** — search terms report for the same period, with spend,
   clicks and conversions per term.

**Paid-social accounts (Meta, TikTok, LinkedIn) have no keywords or search
terms.** Pass `keywords.csv` and `search_terms.csv` containing only their header
rows (copy the first line of the files in `examples/data/`). The audit then
skips the search-term waste and negative-keyword checks and lists them under
"Audit notes" as not applicable; the other four checks run normally. Wasted
spend will read `$0` because it was not measured, not because none exists.

Set `--period-start` and `--as-of` to the exact first and last day of the export
window. Pacing arithmetic uses that day count, so a mismatch between the export
window and the flags produces wrong pacing findings.

## Running it

First run: `bash examples/run.sh` audits the bundled sample account in
`examples/data/`. To reproduce it by hand, or to point it at your own exports
(replace the `examples/data/` paths with wherever you saved them):

```bash
python3 scripts/audit_account.py \
  --campaigns examples/data/campaigns.csv \
  --ads examples/data/ads.csv \
  --keywords examples/data/keywords.csv \
  --search-terms examples/data/search_terms.csv \
  --account "Aurora Bikeworks — Google Ads (US/CA)" \
  --period-start 2026-09-01 \
  --as-of 2026-09-21
```

Tuning flags, all with defaults that suit a mid-size account:

- `--naming-pattern` (default `{brand}_{geo}_{funnel}_{type}`) — set it to the
  agency's actual convention. Ask the user rather than guessing; a wrong pattern
  turns every campaign into a false positive.
- `--pace-tolerance` (default `0.25`) — fraction above or below the daily cap
  before pacing is flagged.
- `--tracking-floor` (default `300`) — minimum campaign spend before a
  zero-conversion campaign is reported.
- `--term-floor` (default `50`) and `--waste-share-high` (default `5` percent) —
  minimum per-term spend to count as waste, and the share of campaign spend at
  which uncovered waste escalates to high severity.
- `--stale-days` (default `90`) and `--material-spend` (default `500`).
- `--json` — same analysis, machine-readable.

## Reading and presenting the output

- **Spend at issue** splits dollars into five buckets that deliberately do not
  sum: wasted, unmeasured, unspent, at risk, and unattributable by naming. Only
  *wasted* is money that can be stopped outright. Quote that number in client
  conversations and describe the others in their own terms — presenting the sum
  as "money we found" is the fastest way to lose credibility on the follow-up.
- **Findings** are sorted by severity, then by dollars. Work them top-down. Each
  finding's evidence lines are quotable in a client email as-is.
- **Checks that passed** exists so the audit reads as a complete pass rather
  than a list of complaints. Keep it in the deliverable.
- **Audit notes** records what could not be checked (campaigns with no ad rows,
  shared budgets, paused campaigns, and search-term checks skipped for
  paid-social accounts). Close those gaps before claiming full
  coverage.

Before sending, sanity-check the highest-value finding against the live account.
Exports go stale, and a disapproval that was fixed yesterday still appears in
yesterday's file.

## Limits

- It audits structure and delivery hygiene, not strategy. It has no opinion on
  offer, landing page, audience choice or creative quality.
- Negative-keyword matching approximates the platforms' own matching (exact,
  phrase, broad) on tokenized text. It ignores close variants, plurals, stemming
  and misspellings, so it can report a term as uncovered that a platform would
  in practice have blocked.
- Shared budgets and portfolio bid strategies are not modeled. Campaigns with no
  daily budget are skipped for pacing and listed in the notes.
- Wasted spend is measured against the exported period only. It is not
  annualized, and it is not a savings guarantee — blocking a query moves budget,
  it does not always remove it.
- Zero conversions on real spend is reported as *unmeasured*, never as proven
  waste. The skill cannot tell a broken tag from a genuinely unproductive
  campaign.
- One account per run. There is no cross-account rollup.
