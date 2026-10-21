---
name: bfcm-live-pulse
description: Turn hourly Shopify sales, blended ad spend and the BFCM Holiday Budget Planner into a concise BFCM status update. Flag stalled orders, checkout drops, unusual code use, AOV changes and stock risk. Produces a Slack-ready draft; never posts or changes budgets automatically.
---

# BFCM Live Pulse

Use during BFCM to answer “are sales on plan, and what needs attention now?” This package runs on local exports. It does not install an hourly automation, fetch live data or post to Slack. The accompanying sample uses fictional Fernhill Goods data.

## Setup

Get every package file with its original relative path and verify published hashes when available. Never reconstruct scripts from an abbreviated skill description. Run from this folder with Python 3.8+ and Bash; no Python dependencies. Try `bash examples/run.sh`, compare with `examples/expected_output.txt`, then run `bash tests/run_tests.sh`. Runtime verification details are in QA.md.

Read DATA_CONTRACT.md before gathering data. Use read-only connected tools where available, or ask for exports. Confirm the store, currency, time zone, plan, covered advertising accounts and data freshness. Never ask for API keys in chat. Do not substitute platform-attributed revenue for Shopify net sales.

## Run

```bash
python3 scripts/live_pulse.py --plan plan.csv --hourly hourly.csv \
  --ly-hourly ly_hourly.csv --spend spend_today.csv \
  --discounts discount_usage.csv --inventory inventory.csv \
  --config config.json --as-of "2026-11-27 14:00" \
  --state pulse-state.json --json pulse.json
```

Discounts, inventory, config, state and JSON output are optional. Supply an explicit as-of date/time through the flag or config. All CSVs use the same store currency, with `.` as the decimal separator: comma decimals, `nan`, `inf` and European-format files exit 2 naming the file, line and column. Spend read_at needs a UTC offset or `Z` unless config sets `"spend_timestamps_tz": "store"`. MER compares sales and spend to the same minute, the earliest spend read time; budget calls also need the spend reads within 30 minutes of each other, and need the raw MER (spend as read, not scaled back to the cut-off) to point the same way; if the two disagree the call is HOLD. Whenever MER is above the scale line, total spend behind its pace band or any budgeted channel behind its own pace band makes the call HOLD ("spend is behind"), because a lagging spend read makes MER read high; that reading is not saved as a check, so it can never confirm a later SCALE. Spend that lags by less than the band (20% by default, `spend_pace_band_pct`) on every channel can't be told apart from genuinely slow spending and still counts as on pace, so refresh spend before acting on a SCALE proposal.

Net sales everywhere means gross sales − discounts, before returns/refunds, excluding tax and shipping (the Planner's definition). Hourly sales are therefore never negative; a negative hour exits 2. Shopify's own *Net sales* report column is after returns, so don't export it: export gross sales minus discounts per hour.

1. Gather fresh inputs, reconcile totals and ensure every spending channel is represented. Missing channels outside the plan cannot be detected automatically.
2. Run the script, retain JSON for full detail, and inspect `notes`, `not_checked` and `defaults` before sharing the draft.
3. Lead with status and exceptions, then the numbers and recommended investigation. Treat code-use spikes as a hypothesis, not proof of leakage. Confirm whether promotions or planned sends explain them before proposing a code change.
4. Budget calls are recommendations, printed as "Proposed (needs approval):". Missing planned channels, stale spend, or a budgeted channel reading $0 after the first check yield DATA INCOMPLETE and block budget actions and checkpoint writes. If a channel is paused on purpose, list it in config `paused_channels`. Never apply suggested changes without authorization for the specific change.
5. To automate, first agree on cadence, destination, time zone, covered dates and who owns responses. Configure retrieval and posting separately using available type.com tools; a local script is not a deployed monitor. Post only when that workflow is authorized. Retry safely and deduplicate by store/date/hour.

## Interpretation and limitations

Pace extrapolates today's sales using last year's matching day's hourly revenue distribution. It is a scenario, not a forecast confidence interval; changing offer timing or send schedules can invalidate it. Last year's date comes from the planner or defaults to 364 days earlier; confirm holiday alignment. MER is net sales divided by all recorded ad spend, not profit or platform ROAS.

Guardrails come from the planner. When absent, the script derives pull-back at max(breakeven MER, 0.85 × plan MER) and scale at max(target MER, 1.10 × plan MER); these are heuristics. Checks default to noon and 6 pm. At or after a check, the latest snapshot is evaluated and replaces that checkpoint's state. SCALE requires this and the immediately preceding checkpoint above their respective thresholds. State is local, retains 30 checks and has no cross-worker locking; use one writer per store and preserve it between scheduled runs. It is not an exact historical reconstruction of a missed check.

Use `--quiet` to suppress clean updates outside the hour following each summary time. Active/resolved alerts, budget actions and incomplete spend still print. Quiet mode is not delivery deduplication: repeated runs can print the same alert. The message targets 1,800 characters and may omit notes; always examine JSON before sending.

Thresholds in config are tunable rules of thumb, not industry benchmarks. Stock cover assumes the last three hours' sales rate continues and excludes replenishment. Discount input omits zero-use rows, so incomplete exports can appear normal; validate completeness upstream. Inventory has no timestamp column; verify freshness upstream. The timezone name is descriptive: provide an explicit utc_offset correct for that day; DST is not inferred, and a 25-hour DST-change export must be merged to 24 rows.

Use BFCM Offer & Discount Config QA to investigate stacking, Inventory Stockout Risk for stock planning, and BFCM Holiday Budget Planner to revise the plan. Return a draft report plus input coverage, freshness and proposed actions; clearly distinguish detected, recommended, applied and verified. Nothing is applied by this package.
