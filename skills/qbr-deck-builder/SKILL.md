---
name: qbr-deck-builder
description: Assembles one customer's quarterly business review from usage, goal, support, and commercial data into deck-ready sections covering adoption trend, value delivered against stated goals, open risks, and ranked expansion opportunities.
---

# QBR Deck Builder

Builds the analytical half of a quarterly business review for a single account:
what adoption did, whether the customer got what they said they wanted, what is
currently going wrong, and what to sell next — each with the arithmetic shown.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `qbr-deck-builder`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Preparing a QBR or EBR and you want the numbers assembled before anyone opens
  the template.
- Renewal prep: the same sections answer "is this account healthy and what is
  the expansion case?"
- Account handover between CSMs, where a written, evidence-backed state of the
  account beats a conversation.

Run it per account. Batch it by looping over account ids if you need a portfolio
view; for ranking a whole book by risk, use `churn-risk-scanner` instead.

## Inputs

`DATA_CONTRACT.md` has the exact columns, types, and units. You need five files:

1. `usage_monthly.csv` — long format: account, month, metric name, value.
2. `goals.csv` — what the customer said success looks like, with baselines and
   targets.
3. `support.csv` — tickets with priority, dates, and CSAT.
4. `commercial.csv` — ARR, seats, renewal date, owned products, CSM.
5. `products.csv` — the cross-sell catalog with its trigger signals.

### Gathering the inputs through Type integrations

- **Usage**: your product warehouse (Snowflake, BigQuery, Postgres) or product
  analytics (Amplitude, PostHog). One row per account/month/metric. Include the
  goal metrics — the goals section is scored from the same table.
- **Goals**: usually the success plan in the CRM, a Notion or Google Doc, or
  the onboarding kickoff notes. Someone has to translate them into a metric,
  baseline, and target; that translation is the valuable part of this skill's
  input, and it is not automatable.
- **Support**: Zendesk, Intercom, Plain, or Jira Service Management. Export
  created/resolved dates, priority, status, subject, and CSAT for the last two
  support windows (180 days at the default settings).
- **Commercial**: Salesforce/HubSpot/Attio for ARR, renewal date, and owned
  products; billing (Stripe, NetSuite) if the CRM's ARR is not trustworthy.
- **Products**: your own catalog. Pick one usage metric per product that
  genuinely predicts need, and a threshold you would defend to a customer.

## Running it

```bash
python3 scripts/qbr_builder.py \
  --account-id ACC-1001 \
  --usage path/to/usage_monthly.csv \
  --goals path/to/goals.csv \
  --support path/to/support.csv \
  --commercial path/to/commercial.csv \
  --products path/to/products.csv \
  --config config/qbr.json \
  --as-of 2026-09-22
```

- `--as-of` is required. The reporting window is the `history_months` calendar
  months ending with the month containing `--as-of`.
- `--json` emits every section as structured data for a deck generator.
- Bad input exits `2` naming the file, row, and column. An unknown
  `--account-id` lists the ids that are present.

See `examples/run.sh` for a complete working invocation.

## What it computes

- **Adoption trend**: the monthly series, seat utilization per month, and the
  trailing `trend_window_months` average against the window before it. Averages
  use only months that exist; missing months are listed in section 7 rather
  than imputed.
- **Goals**: `attainment = (current - baseline) / (target - baseline)`, which
  works for both increase and reduction goals. Status bands come from
  `goal_status_thresholds`. A goal whose metric has no readings is reported as
  "no data", not as zero.
- **Support health**: ticket volume in the last `support_window_days` against
  the window before it, P1 count, currently open tickets with ages, median time
  to resolve, and average CSAT with the response count (so a 5.0 from one
  survey cannot masquerade as a trend).
- **Risks**: rule-based, each with severity and the evidence that triggered it
  — adoption or engagement decline, low utilization, goals at risk or off
  track, aging open P1s, support volume spikes, low CSAT, and a renewal inside
  the decision window.
- **Expansion**: seat expansion when utilization clears the trigger
  (`additional seats = ceil(active / target_utilization) - licensed`, priced at
  the account's own ARR per seat), plus cross-sell for any catalogue product the
  account does not own whose trigger metric is over threshold. Ranked by
  `estimated ARR x confidence`, where confidence rises with how far the signal
  clears its threshold and is capped by `expansion.confidence_cap`.

All thresholds are in `config/qbr.json`.

## Presenting the output

- Section 1 is the "state of the account" slide. Each bullet already carries its
  number.
- Section 2 is the adoption chart; paste the table straight into the deck, then
  say the trailing-average line out loud.
- Section 3 is the slide that earns the renewal: the customer's own goals, with
  the delta. Lead with the achieved ones, then the off-track one and what you
  are doing about it.
- Sections 4 and 5 belong to the internal pre-read, not the customer deck.
  Bring only the risks you are prepared to discuss, with the fix.
- Section 6 is the expansion recommendation. Bring the top one or two with the
  signal, never the whole list.
- Section 7 keeps everyone honest about what the data did not cover. Read it
  before presenting; do not paste it into the customer deck.

## Limits

- **One account per run.** No portfolio rollup, no cohort comparison.
- It does not produce slides. Output is structured text or JSON; a human or a
  deck tool does the layout.
- Risk rules are thresholds, not a model. They flag patterns that usually matter
  and will miss a relationship problem that never shows up in usage or tickets.
- Expansion estimates use list price per seat and the account's current seat
  count. They are conversation starters, not quotes, and they ignore discounts,
  contract terms, and procurement reality.
- Confidence is a transparent function of how far a signal clears its threshold.
  It is not a probability and should never be presented as one.
- Sentiment is CSAT only. No call transcripts, no email tone, no NPS.
- Goal attainment is linear between baseline and target, with no expected pace
  for elapsed time. A goal at 70% with one week left is not the same as 70% with
  a quarter left; the target date is printed so a human can judge that.
- Monthly granularity. A collapse in the last two weeks of the month is invisible
  until the month closes.
