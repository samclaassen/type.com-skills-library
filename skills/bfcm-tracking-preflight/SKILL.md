---
name: bfcm-tracking-preflight
description: Pre-BFCM audit for Shopify DTC brands of purchase tracking and attribution. Compares Meta, Google Ads and TikTok purchase events with Shopify orders to catch broken pixels, missing Conversions API and failed deduplication, checks UTM hygiene, shows how far last year's platform claims were from real Shopify sales, and gives a READY / FIX BEFORE BFCM / NOT READY verdict with a dated fix list. Read-only.
---

# BFCM Tracking & Attribution Preflight

Black Friday 2026 is **Nov 27** and Cyber Monday is **Nov 30**. The worst time to find out a pixel is broken is on Black Friday, when every hour of bad data steers budget and the ad platforms' algorithms in the wrong direction. A widely shared r/FacebookAds checklist ("BFCM Prep you should do THIS WEEK") puts it simply: audit your pixel, server-side events and UTMs now; pull last year's numbers and compare what Meta said with what actually landed in Shopify, to describe differing attribution methods; and set budgets off MER, not platform ROAS, because several platforms can credit the same sale.

This skill does that audit from local exports and puts a date on every fix: **events complete and deduplicated → UTMs clean → last year's claim gap known → budgets set off MER → change freeze.**

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In type.com, read them with the skill-file tools. Anywhere else, the type.com Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `bfcm-tracking-preflight`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite.

## When to use this

- October to mid-November, before BFCM, on a Shopify store running Meta, Google Ads or TikTok ads. Or on each store an agency manages.
- Someone asks "is our tracking ready for Black Friday?", "why does Meta show more purchases than Shopify?", or "how should we set BFCM budgets?".
- After a theme publish, new app, checkout change or tracking-app install, to make sure nothing broke.
- On a schedule: weekly from October, then on the re-check and final-run dates in the report. Stay silent when the verdict is READY and nothing changed.

## Operating rules

1. **Read-only.** The script reads local files only. Never change pixels, Conversions API settings, conversion actions, ad URL parameters, ad accounts or Shopify settings unless the user approves that specific change. When they do, make it, read it back, place a test order, and re-run the preflight once 3–7 days of new data exist.
2. **Recommend, don't fix silently.** Some fixes move reported numbers (dedup repair can reduce event counts; a value-definition change can alter reported ROAS). Say so before anyone changes anything, and never change conversion settings in the middle of BFCM.
3. **Platform claims are not sales.** Never add platform-reported revenue together and call it revenue, and never divide summed claims by total spend (that is not a ROAS). Shopify net sales is the actual; MER is the budget metric. **Net sales** throughout this skill means gross sales − discounts, before returns/refunds, excluding tax and shipping.
4. **Quote exact numbers**: X of Y, the ratio or percentage, and the date something started. Those are what the team and the platforms' support need.
5. **Keep exports local.** The orders file has order numbers and landing URLs with click IDs. Don't paste it into chat; delete it when done.
6. Say which state each item is in: **detected**, **recommended**, **applied**, or **verified**.

## Gathering the inputs

Exact columns and sources are in `DATA_CONTRACT.md`. All three are CSVs you can export by hand in under 30 minutes.

1. **Daily events, required** (`events_daily.csv`): 28 days of Shopify online-store orders and net sales, next to Meta Events Manager Purchase counts (total after dedup, plus Browser and Server), Google Ads purchase conversions, and TikTok Complete Payment events, with values where available.
2. **Orders with landing site, optional** (`orders_utm.csv`): one row per order with landing URL (query string included) and referrer, from the Shopify Admin API or an export app. Without it, UTM hygiene is not checked.
3. **Last BFCM, optional** (`last_bfcm.csv`): last year's Monday-before-Thanksgiving to Tuesday-after-Cyber-Monday, by day: Shopify net sales, each platform's claimed revenue, and optionally spend and Shopify sales by UTM source. Without it, there is no claim gap or MER baseline.

If the user has API access to these platforms through connected tools, you may pull the same numbers that way, but write them into these CSVs and run the analyzer on the files. Never ask the user to paste API keys or tokens into chat.

## Running it

```bash
python3 scripts/preflight.py --account "Store name" --events events_daily.csv \
  [--orders orders_utm.csv] [--last-bfcm last_bfcm.csv] [--as-of YYYY-MM-DD] [--json analysis.json]
```

`bash examples/run.sh` runs it on the bundled sample store (Fernhill Goods, fictional).

What it does:

1. **Event coverage and dedup.** Meta and TikTok site-event coverage is screened against a configurable 0.85–1.10x Shopify order band over the last 7 days. Consent, reporting scope and latency affect coverage; this band is a heuristic, not proof of correct tracking. It flags Meta deduplication that appears to have failed (browser + server events both counted, with the start date; aggregate counts suggest this, matching event IDs confirm it), missing or partial Conversions API, streams that dropped to zero on days Shopify had orders (ongoing or recovered), and purchase values that don't match Shopify net sales per order (a flag for matched-order reconciliation, not proof of a tax/shipping error). Google Ads counts only ad-attributed conversions, so it is checked for gaps and value, not count.
2. **UTM hygiene.** Share of orders with no `utm_source` and the share that look "direct", click IDs arriving without UTMs (ttclid and msclkid come from ad clicks; fbclid is also added to organic Facebook/Instagram link clicks, so it is reported as "ads or organic posts"), `utm_source` spelling variants (`facebook` / `fb` / `Facebook` / `ig`), unfilled macros such as a literal `{{campaign.name}}`, and the top landing pages for untagged orders.
3. **Last BFCM claim gap.** Sum of platform claims vs Shopify net sales, per day and overall; each platform's claim vs Shopify sales with its UTMs; reported vs Shopify UTM (last-click) ROAS; blended MER. Summed platform ROAS is never printed. If a platform reported attributed sales but has no spend column, the report names it, marks MER as overstated, and does not use it for budgets.
4. **Verdict and dated fix list.** `NOT READY` (any critical), `FIX BEFORE BFCM` (warnings only) or `READY`. Fixes are due 17 days before Black Friday (Nov 10 in 2026) so two weeks of clean data follow; then the re-check (Nov 13), budgets from MER (Nov 17), change freeze (Nov 20) and final run (Nov 24).

Exit codes: 0 means the report was written, 2 means invalid input (with the file, line, column and reason). Numbers are parsed strictly: comma decimals (`2255,74`), European-formatted files (`1.234,56`), `nan`, `inf`, exponents, negatives and duplicate order IDs are rejected rather than guessed; `$1,234.56`, `€1,234.56` and `1234.56` are fine.

## Acting on the report

Present the verdict and the ranked issues, then walk through the fixes in this order. Each needs the user's approval. Exact UI paths and templates are in `references/playbook.md`.

1. **Meta deduplication and Conversions API.** One server source per dataset, the same `event_id` on browser and server Purchase events, data sharing consistent with merchant-approved consent/privacy settings; never automatically raise it. Verify with a test order in Events Manager → Test events. Explain that reported event counts may change; aggregate counts do not establish true attributed ROAS.
2. **Streams at zero.** Find the change on the start date (theme publish, app, checkout or consent banner), fix it, place a test order.
3. **Purchase value.** Reconcile matched orders and agree on revenue definitions before proposing changes. Aggregate purchase-value ratios cannot diagnose a tax/shipping error or correct ROAS. Avoid unreviewed changes during BFCM.
4. **UTMs.** Apply the naming standard to every ad, including catalog, Advantage+, Spark and boosted posts; fix macros per platform; standardise `utm_source` spellings.
5. **Budgets off MER.** Use last year's blended MER with contribution margin and cash constraints to inform budgets and scaling rules. Attribution-gap ratios are descriptive comparisons, never ROAS correction multipliers or evidence of incremental channel contribution. Present this as a plan, not a change to ad accounts.
6. **Freeze and re-check.** Agree the change freeze, then re-run on the re-check and final dates. Mark items **verified** only when a later run shows them fixed.

## Files

- `scripts/preflight.py`: the analyzer. Local files only, deterministic output, optional `--json`.
- `DATA_CONTRACT.md`: the three CSV formats, where each column comes from, and every threshold.
- `references/playbook.md`: Meta dedup and Event Match Quality, Shopify customer events and web pixels, Google Ads value and enhanced conversions, the UTM naming standard with per-platform macros, TikTok, budgeting off MER, the BFCM 2026 timeline, and a status-update template.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`: sample run. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`, `tests/make_cases.py`: 16 end-to-end checks on hand-checkable inputs. No network needed.
