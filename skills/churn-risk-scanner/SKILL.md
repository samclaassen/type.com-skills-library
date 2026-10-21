---
name: churn-risk-scanner
description: Scores renewal risk for every account from usage decline, license utilization, support burden, CSAT sentiment, champion departure, and invoice history, then ranks the book by weighted ARR at risk with the specific evidence behind each score.
---

# Churn Risk Scanner

Turns five operational exports into a ranked list of accounts by how much ARR is
actually at risk, with the reasons attached. Seven weighted factors, all defined
in a config file, all showing their arithmetic. Nothing is a black box, so a CSM
can argue with a score instead of ignoring it.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `churn-risk-scanner`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Weekly or monthly renewal-risk review for a CSM book or a whole region.
- Before a renewal forecast, to separate "large account, small problem" from
  "small account, terminal problem" — they look identical in a health-score
  dashboard and completely different when ranked by ARR at risk.
- After a support incident or a champion's departure, to see which accounts the
  change actually moves.

For one account in depth, use `qbr-deck-builder`. This skill is the portfolio
view.

## Inputs

`DATA_CONTRACT.md` has the exact columns, types, and units. You need five files:

1. `accounts.csv` — ARR, segment, renewal date, CSM.
2. `usage_monthly.csv` — active users, licensed seats, key actions per month.
3. `support.csv` — tickets with priority, escalation flag, and CSAT.
4. `contacts.csv` — contacts with roles and departure status.
5. `invoices.csv` — invoice, due, and paid dates.

### Gathering the inputs through Type integrations

- **Accounts**: Salesforce, HubSpot, or Attio for ARR, renewal date, segment,
  and CSM owner. Use billing (Stripe, NetSuite, Chargebee) if CRM ARR drifts.
- **Usage**: the product warehouse (Snowflake, BigQuery, Postgres) or product
  analytics (Amplitude, PostHog). Export at least `history_months` months so
  both trend windows are complete.
- **Support**: Zendesk, Intercom, Plain, or Jira Service Management, including
  the escalation flag and CSAT responses.
- **Contacts**: the CRM contact object. The `role` and `status` fields are what
  make champion departure detectable — if nobody maintains them, say so in the
  readout instead of pretending the factor is clean.
- **Invoices**: billing or the finance warehouse. Due date and paid date are
  what matter; amount is used only to size overdue balances.

Every file may cover the whole book; the tool joins on `account_id` and fails
loudly if a row references an account that is not in `accounts.csv`.

## Running it

```bash
python3 scripts/churn_scanner.py \
  --accounts path/to/accounts.csv \
  --usage path/to/usage_monthly.csv \
  --support path/to/support.csv \
  --contacts path/to/contacts.csv \
  --invoices path/to/invoices.csv \
  --config config/risk_model.json \
  --as-of 2026-09-22
```

- `--as-of` is required and is the only source of "today".
- `--renewal-within N` scores only accounts renewing in the next N days.
- `--top N` prints only the N riskiest accounts; the summary still covers all.
- `--json` emits every factor, score, contribution, and evidence string.
- Bad input exits `2` naming the file, row, and column.

See `examples/run.sh` for a complete working invocation.

## How the score works

Each factor produces a 0-100 subscore where 100 is worst, from bands defined in
`config/risk_model.json`:

| Factor | Default weight | Input |
| --- | --- | --- |
| `usage_decline` | 25 | trailing vs prior 3-month average of `key_actions` |
| `user_attrition` | 15 | trailing vs prior 3-month average of `active_users` |
| `license_utilization` | 10 | latest `active_users / licensed_seats` |
| `support_burden` | 15 | tickets per 100 seats + weighted P1s and escalations in 90 days |
| `sentiment` | 15 | average CSAT over 180 days |
| `champion_departure` | 10 | champion / economic-buyer contact status |
| `invoice_history` | 10 | average days late plus weighted overdue invoices |

```
risk score        = sum(subscore x weight) / sum(weight of factors with data)
weighted ARR risk = ARR x risk score / 100
```

**Missing data is excluded, not scored zero.** If an account has no CSAT
responses, the sentiment weight is dropped and the remaining weights are
renormalized; the report prints `NO DATA` for that factor and states how much
weight the score was computed over. This is the single most important behaviour
in the tool: silently treating "no survey responses" as "happy customer" is how
health scores lose credibility.

Bands (`critical`, `high`, `medium`, `low`) are thresholds on the final score,
also in the config.

## Tuning

Everything is in `config/risk_model.json`: factor weights, every band boundary
and its label, the support and invoice index multipliers, the windows
(`support_window_days`, `csat_window_days`, `invoice_window_days`,
`champion_departure_window_days`), the roles that count as a champion, and the
risk band thresholds. Change weights to match what has actually predicted churn
in your business; if you have no evidence yet, start with the defaults and
revisit after two renewal cycles.

## Reading and presenting the output

- **Book summary** is the number for the forecast call: total weighted ARR at
  risk and how it splits across bands.
- **Factor prevalence** tells you whether you have an account problem or a
  systemic one. Five accounts flagged on `support_burden` is a product or
  staffing issue, not five separate save plays.
- **Ranked book** is sorted by weighted ARR at risk, not by score, so a $520k
  account at 18 outranks a $45k account at 35. Each account shows its top three
  contributing factors, then the full factor table with evidence.
- Take the top of the list into the save-play conversation with the evidence
  lines — they are written to be pasted into a CRM note.
- Read the `NO DATA` lines before trusting a low score; an account scored on 60
  of 100 weight points is a data gap, not a healthy account.

## Limits

- **Correlational, not predictive.** These are the patterns that usually precede
  a non-renewal. Nothing here is fitted to your historical churn, and the
  default weights are a starting point, not a trained model.
- It cannot see relationship, pricing, or competitive risk. An account can score
  0 and still churn because a new CTO consolidated vendors.
- Sentiment is CSAT only — no NPS, no call transcripts, no email tone.
- Champion detection depends entirely on CRM hygiene. Accounts with no contact
  roles maintained score the "no champion on record" value, which is a data
  finding as much as a risk finding.
- Monthly usage granularity. A sharp drop mid-month is invisible until the month
  closes, and the trend needs two complete windows before it reports anything.
- ARR at risk is a linear scaling of the score, not an expected value. It ranks
  the book; it is not a forecast of dollars you will lose.
- Single currency (USD by contract). Convert before exporting.
- No write-back: no CRM updates, no alerts, no tasks created.
