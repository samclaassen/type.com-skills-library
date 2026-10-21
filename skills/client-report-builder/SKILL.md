---
name: client-report-builder
description: Build a month-end client performance report from paid media, web analytics and budget exports, computing period-over-period deltas, budget pacing, blended CAC and ROAS, MER and new-customer CAC from finance's figures, per-platform claimed ROAS (never summed), and a ranked list of changes for the next cycle.
---

# Client report builder

Turns three exported files into a client-ready markdown report: headline metrics
with month-over-month deltas, per-channel paid performance, budget pacing against
committed spend, site-wide channel mix, and a "what we're changing next month"
section where every recommendation carries a dollar figure.

It follows the shared revenue contract in `REVENUE_DEFINITIONS.md`. In
particular, **platform-claimed revenue is never totalled**: Meta, Google and
TikTok each claim credit for overlapping sales, so their claimed revenue and ROAS
appear per platform only, labelled with the attribution window, and total sales
come only from analytics or finance.

## Shared skills and business definitions

Use [Marketing Measurement](https://type.com/library/skills/marketing-measurement)
and its prerequisite [Revenue Definitions](https://type.com/library/skills/revenue-definitions).
When installed as sibling folders, read ../marketing-measurement/SKILL.md and
../revenue-definitions/SKILL.md. In Type, load those skills by name. If missing,
get their complete packages from the linked library pages before finalizing a
client report.

Reuse the client's approved revenue policy and measurement definitions. Record
their version, reporting period, timezone, currency, spend scope, attribution
windows and new-customer rule. The bundled REVENUE_DEFINITIONS.md documents the
script's input format; map the approved definitions into it without creating a
competing policy. Verify export settings against the actual platform reports,
not just the labels in the configuration.

Use Marketing Measurement to validate ROAS, MER and new-customer CAC before
turning the figures into client commentary. The bundled script still computes
its documented formulas: analytics conversions are not necessarily new
customers, and its MER/CAC spend includes paid media only. If the agreed spend
scope includes fees, creative or other costs, do not present those script
metrics as the agreed MER/CAC. Use separately verified results from Marketing
Measurement with their evidence, or mark the metrics unavailable and explain
the missing inputs. Do not fabricate campaign rows to force extra costs into
the script's paid-media model.

Script defaults may be used to explore the supplied examples, but final client
metrics require agreed definitions. Keep missing-definition output provisional;
do not treat the script's fallback as client approval. Use finance-approved
sales as business revenue, label analytics revenue separately, and never add
platform-attributed revenues together. Preserve the existing report structure,
data notes and human review of recommendations.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `client-report-builder`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- The monthly reporting cycle has closed and a retainer client needs a
  performance recap.
- A mid-month check-in needs the same structure against a partial month (pass an
  `--as-of` inside the month and pacing prorates automatically).
- A QBR needs a consistent, arithmetic-identical recap for several months in a
  row.

Do not use it for a single-platform deep dive — `ad-account-audit` is the
account-hygiene skill. Do not use it for mid-flight budget reallocation —
`budget-pacer` projects month-end spend and recommends daily budgets.

## Gathering the inputs

All three files are exports the agent assembles before running the script. The
script never calls an API and never sees a credential. Column-by-column
requirements are in `DATA_CONTRACT.md`.

1. **`paid_performance.csv`** — one row per client / month / channel / campaign.
   Pull it from the user's connected ad platforms (Google Ads, Meta Ads, TikTok
   Ads, Microsoft Ads, LinkedIn Ads). Request the reporting month **and** the
   comparison month in the same pull so both periods use the same attribution
   settings. Normalize every platform to one `channel` label per platform and
   keep currency consistent — the script does no currency conversion.
2. **`analytics_channels.csv`** — one row per client / month / channel group from
   the web analytics property (GA4 default channel grouping, or the equivalent in
   whatever analytics tool is connected). This is the source of blended figures,
   so it must cover *all* traffic, not just paid.
3. **`budget.csv`** — committed budget per channel for the reporting month. This
   normally comes from the agency's own system of record: a spreadsheet, a
   project tool, or the media plan attached to the statement of work. Ask the
   user for it if no connected source holds it; do not infer budget from spend.

Two optional inputs make the report use finance's numbers and rules:

4. **`finance/revenue_definitions.json`** — finance's definitions file (format in
   `REVENUE_DEFINITIONS.md`). Read automatically from the working folder, or pass
   `--definitions <path>`. The skill uses its `marketing.attribution_windows`,
   `marketing.new_customer_definition`, `marketing.mer`,
   `marketing.new_customer_cac`, `reporting_currency` and `version`. Finance owns
   this file; never write or edit it on finance's behalf.
5. **`finance_totals.csv`** (`--finance-totals`) — finance's net sales and
   new-customer count per month. Needed for MER and new-customer CAC; without it
   those two metrics are not computed and the report says so.

If the client's name is spelled differently across systems, normalize it before
writing the files. The script matches on the exact `client` string and exits
non-zero when a file has no rows for it, which is the intended behavior: a silent
empty report is worse than a hard failure.

## Running it

```bash
python3 scripts/build_report.py \
  --paid data/paid_performance.csv \
  --analytics data/analytics_channels.csv \
  --budget data/budget.csv \
  --client "Northwind Coffee Co." \
  --month 2026-08 \
  --as-of 2026-09-02
```

- `--as-of` is required and is the only notion of "today" in the skill. Use the
  date the report is being produced. If it falls inside `--month`, pacing is
  prorated to the elapsed days and the report is labeled as in-flight.
- `--compare-to` defaults to the calendar month before `--month`. Override it for
  year-over-year comparisons (`--compare-to 2025-08`).
- `--target-roas` sets the contracted ROAS floor. Without it, the floor is half
  the paid portfolio's own ROAS, so the skill only flags channels that are
  materially worse than the rest of the account.
- `--tracking-floor` (default 250) and `--material-spend` (default 500) keep tiny
  channels out of the recommendations. Raise both for large accounts.
- `--definitions` points at finance's `revenue_definitions.json`. Without it the
  script looks for `finance/revenue_definitions.json` in the working folder. If
  neither exists it uses the skill's defaults and the report's first lines say
  so and list them. A `--definitions` path that does not exist, invalid JSON, a
  missing required key, or an `mer` / `new_customer_cac` formula other than the
  one this skill implements is a hard error, never a silent substitution.
- `--finance-totals` supplies net sales and new customers (see
  `DATA_CONTRACT.md`). With it, MER and new-customer CAC appear in the headline
  table and executive summary.
- `--json` emits the same analysis as structured JSON for a dashboard or a
  follow-up step. The definitions in force are under `definitions`; the
  fallback notice also goes to stderr.

## Reading and presenting the output

Markdown goes to stdout. Save it as a document, paste it into the client thread,
or attach it to the monthly email.

- **Definitions block** (the first lines) names the finance definitions file and
  version used, or says that none was found and lists the defaults in force.
  Keep it in anything sent onward; it is what makes the numbers auditable.
- **Executive summary** is written to stand alone. If the client reads five
  lines, these are the five.
- **Headline metrics** carries only figures from a single source of truth:
  paid spend, analytics conversions and revenue, *blended CAC* and *blended
  ROAS* (analytics revenue ÷ paid spend), and, when finance totals are supplied,
  *MER* (finance net sales ÷ total marketing spend) and *new-customer CAC*
  (total marketing spend ÷ finance's new customers). There is no summed
  "platform ROAS": adding each platform's claimed revenue double-counts sales
  that several platforms credit to themselves.
- **Paid channel performance** shows each platform's claimed conversions,
  claimed revenue and claimed ROAS next to its attribution window. The total row
  carries spend only. Do not add the claimed revenue column up in a deck or email.
- **Budget pacing** shows committed vs spent vs expected-to-date. A channel at
  0% with a committed budget means the plan and the account disagree — check
  before sending.
- **What we're changing next month** is ordered by priority (P1 urgent, P3
  opportunistic) and then by dollars at stake. Rewrite the prose in the agency's
  voice if needed, but keep the numbers exactly as produced.
- **Data notes** lists every place the inputs were incomplete. Resolve those
  before sending to a client; do not delete the section to make the report look
  cleaner.

Always confirm the recommendations against context the files cannot hold —
seasonality, a product launch, a client-side inventory constraint — before
presenting them as decisions.

## Limits

- It reports what the exports say. The attribution window shown per platform is
  finance's *definition*; the script cannot check that the export was actually
  pulled with that window. Without a definitions file the window is shown as
  "as exported (unverified)". It also cannot detect misconfigured conversion
  actions beyond "zero conversions on real spend", or reconcile
  platform-claimed revenue against the store's ledger.
- Platform-claimed revenue is never summed, so there is no paid-portfolio ROAS.
  The default ROAS floor and the "scale" test compare each channel with the
  median of the per-platform claimed ROAS figures, which were measured in
  different windows (for example 30-day click on Google vs 7-day click / 1-day
  view on Meta). Treat those comparisons as directional; pass `--target-roas`
  when the contract sets a floor.
- Blended CAC treats every analytics conversion as one acquisition. If the
  property counts repeat purchases or non-purchase events as conversions, the
  figure is a cost per conversion, not a true cost per new customer. Use
  new-customer CAC (finance totals) for that.
- "Total marketing spend" in MER and new-customer CAC is the paid media spend in
  the paid export. Agency fees, creative, influencer and other non-media costs
  are not included, so both metrics flatter the program if those costs are
  material.
- The skill does not compute net sales or count new customers; it takes both
  from `finance_totals.csv` as finance supplied them. Analytics revenue is never
  relabelled as net sales.
- Only the MER and new-customer CAC formulas in the shared contract are
  implemented. A definitions file naming a different formula stops the run.
- No incrementality, no media-mix modeling, no statistical significance testing.
  The period-over-period deltas are arithmetic, not causal.
- Single currency only, no FX conversion, and no cross-client rollups — run it
  once per client. `reporting_currency` from the definitions file labels the
  amounts (ISO code prefix for non-USD); it does not convert them.
- Only the marketing section of the definitions file is used. Net-sales rules,
  refunds basis, excluded orders, timezone and channel dedupe belong to the
  skill or system that produced `finance_totals.csv`.
- Recommendations come from fixed thresholds (see the rules in
  `scripts/build_report.py`). They are a starting agenda for a human strategist,
  not an auto-approved change list.
