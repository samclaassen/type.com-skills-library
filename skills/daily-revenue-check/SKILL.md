---
name: daily-revenue-check
description: Before a DTC brand posts its daily revenue number, combine Shopify, Amazon and wholesale invoices into one net-sales figure using only finance's revenue_definitions.json, run freshness, completeness, duplicate, reconciliation, late-refund and exclusion checks, and return PUBLISH or HOLD with a draft Slack message.
---

# Daily Revenue Check

Answers the two questions that come before a daily revenue post:

- **Where does the number come from?** One net-sales figure built from each
  channel's source of truth (Shopify, Amazon Seller Central, invoices in the
  accounting system) using only the rules in finance's
  `finance/revenue_definitions.json`, with a bridge from each export to the
  number.
- **Can we trust today's report?** Seven checks, then a verdict: **PUBLISH**
  or **HOLD**, with every reason for a HOLD and what to do about it.

It never posts anything. It prints a draft Slack message for a person to read
and send.

## Shared skills and business definitions

Use [Revenue Reporting](https://type.com/library/skills/revenue-reporting) and
[Revenue Reconciliation](https://type.com/library/skills/revenue-reconciliation),
which both require [Revenue Definitions](https://type.com/library/skills/revenue-definitions).
When installed as sibling folders, read ../revenue-reporting/SKILL.md,
../revenue-reconciliation/SKILL.md and ../revenue-definitions/SKILL.md.
In Type, load those skills by name. If a prerequisite is unavailable, get its
complete package from the linked library page before finalizing the report.

Revenue Definitions establishes one finance-approved policy for the business.
Reuse that policy and its version on every run. The bundled
REVENUE_DEFINITIONS.md describes this script's configuration format; it is an
adapter reference, not a second source of business definitions. Have finance
approve its mapping into finance/revenue_definitions.json. Do not replace the
approved policy with example values.

Use Revenue Reporting to confirm each channel's source, extraction window,
saved query or script, and cross-channel identity rules. Use Revenue
Reconciliation to assess completeness and differences before presenting the
daily result. The bundled script remains a supported local implementation;
loading the shared skills does not expand the script's supported rules.

Before running on real data, compare the approved policy with the script's
Limits below. In particular, Shopify orders fulfilled by Amazon MCF are not
automatically Amazon marketplace sales. Exclude a Shopify mirror only when a
matching sale is counted in the chosen source of truth. If the configured
tag-based exclusion, sale date, refund date, FX basis or channel model does not
match the approved policy, HOLD and use a validated query or script under
Revenue Reporting. Do not change the policy to make this script pass.

The script's PUBLISH result is one input to the final verdict. If shared
reconciliation checks find stale or incomplete coverage, unexplained
differences, or a restatement requiring review, the final result remains HOLD
until resolved or handled under the agreed publication policy. Retain its
supporting evidence and draft-only Slack workflow.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `daily-revenue-check`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Every morning before the daily revenue number goes to Slack or leadership.
- When the posted number and a channel's own dashboard disagree.
- After a change to the revenue definitions, to see the new number next to
  its source reports.
- When a feed broke overnight and you need to know whether today's number is
  safe to send.

Out of scope: forecasting, margin, attribution. Marketing appears only as MER
when a spend file is supplied (see below).

## Required: finance's definitions file

The skill **requires** `finance/revenue_definitions.json` in the working folder
(or `--definitions <path>`). Its format and rules are in
`REVENUE_DEFINITIONS.md` (a copy of the shared, finance-owned contract). If a
key the skill needs is missing, it exits 2 and names the key, for example
`missing required key checks.max_feed_staleness_hours`. It never chooses a
definition on finance's behalf and has no built-in defaults. If the brand does
not have the file yet, ask finance to fill it in from the template in
`REVENUE_DEFINITIONS.md`; do not write one yourself and call it finance's.

What the skill implements from the file:

| rule | keys |
| --- | --- |
| net sales = gross product sales - discounts - refunds; shipping, tax, gift cards in or out | `net_sales.*` |
| every timestamp converted to the reporting timezone before it is assigned to a day | `reporting_timezone` |
| every amount converted to one currency with finance's named rates file; a missing rate is a HOLD, never a guess | `reporting_currency`, `fx.source`, `fx.rates_file` |
| sales dated by order creation; refunds on the refund date or back on the order date | `sale_date_basis`, `refunds.date_basis` |
| late refunds flagged | `refunds.late_refund_days` |
| excluded orders reported by rule with count and value | `excluded_orders.*` |
| each channel counted once from its source of truth, deduped on its key | `channels.*` |
| Shopify copies of Amazon MCF and wholesale orders removed | `cross_channel_duplicates` |
| wholesale = invoices; retailer sell-through never added | `channels.wholesale.revenue_event`, `exclude_retailer_sell_through` |
| MER = net sales / total marketing spend; platform-claimed revenue never totalled | `marketing.mer`, `marketing.attribution_windows` |
| check thresholds | `checks.*` |

## Gathering the inputs

All inputs are local files; the script never touches the network. Pull them
with the user's connected integrations, then shape them to `DATA_CONTRACT.md`.
Every export needs ISO 8601 timestamps **with a UTC offset**; naive
timestamps are rejected rather than guessed.

1. **Shopify** (`channels.shopify.export`, e.g. `shopify_orders.csv`). From the
   Shopify integration, pull orders created in a window that covers the whole
   reporting day in the reporting timezone (take the UTC day before and after
   too; the script assigns days itself), plus refunds *issued* in that window,
   including refunds on older orders. One `sale` row per order and one
   `refund` row per refund. Keep tags, financial status and cancelled state.
   Put the Amazon order id or invoice id in `mirror_of` on MCF and wholesale
   draft orders when you can see it.
2. **Amazon** (`channels.amazon.export`). From Seller Central (the Orders API
   or the All Orders report), orders by purchase date over the same window, in
   the marketplace currency, with refunds as separate rows.
3. **Wholesale** (`channels.wholesale.export`). From the accounting system
   (QuickBooks, Xero, NetSuite), invoices by invoice date, including voided
   ones, with credit notes as `refund` rows. Not purchase orders, not retailer
   sell-through.
4. **Source totals** (`source_totals.csv`). The agreed number for the day from
   each channel's source-of-truth report named in the definitions file, read
   off the report itself, one row per channel and currency. Say whether the
   report already leaves out finance's excluded orders (`scope`).
5. **FX rates** (the file named in `fx.rates_file`), maintained by finance. Only
   needed when something is not in the reporting currency.
6. Optional: **marketing spend** for MER, and **retailer sell-through** for
   context.

Record in the conversation which integration and report each file came from
and when it was pulled; that is the extract timestamp the freshness check uses.

## Running it

```bash
python3 scripts/daily_revenue_check.py \
  --workdir /path/to/brand \
  --as-of 2026-09-23T07:00:00-04:00 \
  --date 2026-09-22 \
  --source-totals /path/to/brand/source_totals.csv \
  --spend /path/to/spend.csv \
  --sell-through /path/to/sell_through.csv
```

- `--workdir` holds `finance/revenue_definitions.json` and the channel exports
  named in it. Export and rates-file paths are relative to it.
- `--as-of` (required, with offset) is the only "now". Freshness is measured
  against it; nothing reads the clock, so reruns are reproducible.
- `--date` is the reporting day. Default: the day before `--as-of` in the
  reporting timezone.
- `--max-shopify-gap-hours` (default 4) is the longest run of whole local hours
  with no Shopify orders before completeness fails. This is a skill setting,
  not a finance definition; raise it for small stores with quiet nights and
  say so.
- `--allow-empty-channel wholesale` accepts a day with genuinely no records for
  that channel (common for wholesale) after someone has confirmed it. It shows
  up as a caveat.

Exit codes: `0` PUBLISH, `1` HOLD, `2` bad input or missing definition key
(the message names the file, row, column or key).

`examples/run.sh` runs the bundled synthetic brand twice: a HOLD (the wholesale
source total was keyed from retailer sell-through) and a PUBLISH after the
invoice total is re-pulled.

## The checks

1. **Freshness**: each export's latest `extracted_at` is within
   `checks.max_feed_staleness_hours` of `--as-of`.
2. **Completeness**: every configured channel has sale records on the day; each
   export was extracted after the day closed; no Shopify gap longer than
   `--max-shopify-gap-hours`.
3. **Duplicates**: identical repeated rows (same dedupe key) are counted once
   and listed. Two rows with the same key but different values FAIL. Shopify
   orders carrying a `cross_channel_duplicates` tag are removed from Shopify
   and listed with their counterpart; a `mirror_of` that points at nothing,
   or a `mirror_of` on an untagged order, FAILs.
4. **Reconciliation**: each channel's export total vs the source report,
   passing within `reconciliation_tolerance_pct` OR
   `reconciliation_tolerance_abs`. A missing source total FAILs.
5. **Late refunds**: refunds issued on the day against sales older than
   `late_refund_days`. On `refund_date` basis they reduce today's number and no
   prior day moves; on `order_date` basis the section lists every prior day
   that moves and by how much. This is a NOTE and a caveat, not a HOLD.
6. **Excluded orders**: count and value per rule and channel.
7. **Currency conversion**: every rate used; a missing rate FAILs.

## Reading and presenting the output

Read top to bottom:

1. **DEFINITIONS**: the version and rules used. Every number below depends on
   them; quote the version whenever you quote the number.
2. **VERDICT**: PUBLISH, or HOLD with numbered reasons and a `do:` line each.
   On HOLD, give the user the reasons and actions, not the number.
3. **NET SALES**: per channel and total, with gross, discounts and refunds.
4. **BRIDGE**: export total, minus excluded orders, minus cross-channel
   mirrors, equals net sales; source report and difference alongside. This
   answers "where do we get the number?".
5. **CHECKS**: detail for each check.
6. **RETAILER SELL-THROUGH** (context only) and **MARKETING EFFICIENCY** (if
   supplied).
7. **SLACK MESSAGE (DRAFT)**: the number, definitions version and caveats.

How to present it:

- On PUBLISH, show the draft and let a person post it. Do not post it yourself
  and do not describe it as posted.
- On HOLD, lead with what failed and the fix. If the user wants to post
  anyway, that is their call; the draft stays marked as on hold and
  provisional.
- Never quote a channel dashboard number, retailer sell-through, or
  platform-claimed revenue as sales. Only the NET SALES table is sales.
- When a late refund restates a prior day (order-date basis), say which day
  and by how much so the earlier post can be corrected.

## Limits

- **Only as good as the exports.** The skill checks their shape, freshness,
  coverage and agreement with the source reports; it cannot see orders missing
  from both the export and the report.
- **Reconciliation compares totals, not rows.** An export can match its source
  total by accident when a missing order and an extra order cancel out.
- **Source totals are typed in by a person.** Pull them in the reporting
  timezone. Shopify and Amazon reports use the store or marketplace timezone;
  if that differs from `reporting_timezone`, the report's day is a different
  day and reconciliation will FAIL until the report is re-run for the right
  window.
- **Supported definitions only.** `net_sales.starts_from` must be
  `gross_product_sales`, `sale_date_basis` must be `order_created`,
  `refunds.date_basis` `refund_date` or `order_date`, wholesale
  `revenue_event` `invoice`, and the channels `shopify`, `amazon` and
  `wholesale`. Anything else exits 2 naming the key rather than being
  approximated.
- **Cross-channel duplicates follow the definitions file literally.** Tagged
  Shopify orders are the copy that is removed. Untagged mirrors cannot be
  detected unless `mirror_of` is filled in.
- **FX**: amounts convert at the rate for the reporting-timezone date of the
  event (sale date for sales, refund date for refunds). Weekend and holiday
  rates must be in the file explicitly; the skill does not carry rates
  forward.
- **Marketing**: MER only. No CAC, ROAS, or attribution modelling;
  platform-claimed revenue is listed per platform with its window and never
  summed.
- The Shopify gap check is a heuristic for a broken export, not proof of one.
