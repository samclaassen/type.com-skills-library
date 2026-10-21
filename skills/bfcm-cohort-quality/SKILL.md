---
name: bfcm-cohort-quality
description: Find out whether the new customers a Shopify DTC brand bought during Black Friday / Cyber Monday were worth it. Compares them with the customers bought the October before and the January–February after, at equal ages (repeat rate and contribution margin per customer at 30–365 days, immature horizons marked), plus refunds, discount dependence and payback on blended new-customer cost. Cuts BFCM buyers by discount depth, source and entry product, and sets this year's offer and budget. Read-only.
---

# BFCM Cohort Quality

Black Friday 2026 is **Nov 27** and Cyber Monday is **Nov 30**. BFCM is often the cheapest week of the year to buy a new customer: conversion rates jump, so ad spend per new customer falls. But a customer bought at 25–40% off may not behave like one bought in October at full price. A working hypothesis worth testing on your own data: some came for the deal, wait for the next code, and send more back. A budget that values every BFCM customer at the store's average lifetime value overpays for them.

This skill answers one question from the order history: **were the customers we bought during BFCM actually worth it?** Run it now on last year's BFCM to shape this year's offer and budget, and again in January for an early read on this year's. A horizon is only reported once every customer in the cohort has reached it; anything younger prints as "not mature" with the date it matures.

It specializes the library's **Cohort LTV Analyzer**, which builds calendar-month cohorts, so BFCM buyers are blended into November and December with everyone else. Here the BFCM window (Monday of Thanksgiving week through the Tuesday after Cyber Monday) is its own cohort, measured against the October before and the January–February after at the same ages, then cut by first-order discount depth, acquisition source and entry product. Use Cohort LTV Analyzer for the month-by-month LTV triangle; use this skill for the BFCM decision. It hands its numbers to two collection skills: **BFCM Holiday Budget Planner** (same `economics.json`; this report says which return rate and new-customer cost to plan with) and **BFCM Offer & Discount Config QA** (checks that codes can't stack on the sale in ways the plan didn't intend).

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In type.com, read them with the skill-file tools. Anywhere else, the type.com Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `bfcm-cohort-quality`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite.

## When to use this

- October, before the BFCM offer and budget are locked: run it on last year's BFCM.
- January, for an early read on this year's BFCM (30 days matures about a month after Cyber Monday, 60 days a month later). Re-run at 90 and 180 days.
- Someone asks "were our Black Friday customers any good?", "should we go deeper than 25%?", "what lifetime value should we assume for BFCM customers?" or "which products bring people back?"
- An agency reviewing each brand's last BFCM before planning the next one.

## Operating rules

1. **Read-only.** The script reads local files and prints a report. Never change a discount, flow, campaign or budget unless the user approves that specific change. When they do, make it, read the setting back, and re-run this report when the next horizon matures.
2. **Equal ages only.** Compare cohorts at the same horizon, never a 180-day number with a 365-day one. Never present a "not mature" horizon as a result; quote the date it matures instead.
3. **Contribution, not revenue; blended, not attributed.** Value is contribution margin after COGS, discount, refunds, payment fees and shipping, before ads. Cost per new customer is all ad spend in the window ÷ new customers in the window. Don't swap in platform-reported CAC or ROAS.
4. **Correlation, not causation.** The cuts show who came back, not why. Respect the small-cell suppression and the 95% ranges, say "within noise" when a difference is, and propose a holdout test before calling a cut the cause.
5. **Never invent economics.** Gross margin, shipping, fees and return rate come from the user or the Budget Planner's `economics.json`. If one is missing, ask.
6. **Keep exports local.** The orders file holds customer IDs or emails. The report never prints them; don't paste the file into chat, and delete it when done.
7. Say which state each item is in: **detected**, **recommended**, **applied**, or **verified**.

## Gathering the inputs

Exact columns, aliases and validation are in `DATA_CONTRACT.md`.

**1. `orders.csv`, required: one row per order, full history for every customer.** The script finds each customer's first-ever order itself, so the export must reach back before the cohorts (ideally to the store's first order); `--history-since` states where complete history starts if you pre-filter. Columns: `order_id, customer_id, created_at, net_sales, discount_amount`, plus `discount_codes, refunded_amount, cogs, utm_source` (or `landing_site`) and `products` (handles separated by `;`) when you have them.
- **In type.com**, pull orders through the connected Shopify integration with read-only scopes (`read_orders`, plus `read_all_orders` for anything older than 60 days). The GraphQL fields are listed in `DATA_CONTRACT.md` §1. Never ask the user to paste a key into chat.
- **Shopify admin export** (Orders → Export → All orders, CSV) loads as it is: `Name`, `Email`, `Created at`, `Subtotal`, `Discount Code`, `Discount Amount`, `Refunded Amount`, `Lineitem name` and `Cancelled at` are recognised, extra line-item rows are merged and cancelled orders dropped. It has no UTM data, so the source cut is skipped.
- A **Cohort LTV Analyzer** order export also loads (`order_date`, `net_revenue`, `discount`, `refund_amount`). If its `net_revenue` is already net of refunds, drop or rename `refund_amount` (and set `return_rate_pct` to 0) so refunds aren't subtracted twice; see `DATA_CONTRACT.md` §1.

**2. `economics.json`, required.** The same file as the BFCM Holiday Budget Planner: `gross_margin_pct`, `shipping_fulfillment_per_order`, `payment_fee_pct`, `payment_fee_fixed`, `return_rate_pct`, and `discount_pct` (this year's planned depth, used as the reference band). Every other key is ignored, so the planner's file works unchanged.

**3. `spend.csv`, optional.** Daily ad spend: `date` plus one `*_spend` column per channel (`meta_spend`, `google_spend`, `tiktok_spend`, `other_spend`, …), store time zone, covering every day of each cohort window. The Budget Planner's `history.csv` has the same columns and works too. Without it the report skips cost and payback.

**4. Settings** (flags, all optional): `--year` (default: the latest BFCM finished by the as-of date), `--as-of` (default: the last order date), `--bfcm-window START:END`, `--compare "LABEL=START:END"` (repeatable; replaces the October and January–February defaults), `--horizons` (default `30,60,90,180,365`), `--min-cell` (default 30), `--timezone` (the store's, for timestamps written in UTC) and `--history-since`.

## Running it

```bash
python3 scripts/cohort_quality.py --orders orders.csv --economics economics.json [--spend spend.csv] \
  [--year 2025] [--as-of YYYY-MM-DD] [--timezone America/New_York] [--json analysis.json]
```

`bash examples/run.sh` runs it on the bundled sample store (Fernhill Goods, fictional): BFCM 2025 against October 2025 and January–February 2026, as of Oct 6, 2026.

For the January read on this year's BFCM, compare like with like:

```bash
python3 scripts/cohort_quality.py --orders orders.csv --economics economics.json --spend spend.csv \
  --year 2026 --compare "BFCM 2025=2025-11-24:2025-12-02" --compare "October 2026=2026-10-01:2026-10-31"
```

What it does:

1. **Finds new customers.** A customer is new in a cohort when their first-ever order falls in its window. Anyone with an earlier order is not new, however big their BFCM order.
2. **Measures each cohort at equal ages**: new customers, first-order AOV and discount depth, refunds, blended new-customer cost, then repeat rate and cumulative CM per customer at each horizon. A horizon is computed only when the cohort's last day has reached it (right-censoring); otherwise it prints "not mature" and the date. A second order on the same day as the first counts toward value, not as a repeat.
3. **Compares at the longest horizon the cohorts share**, and adds days to second order (median and quartiles), the share of repeat orders that used a discount, and the payback day: the first day the average customer's cumulative CM reaches the blended cost, read only over days every customer has reached.
4. **Cuts the BFCM cohort** by first-order discount depth (0, 1–15, 16–25, 26–35, >35% of the pre-discount price, with the codes behind the deep bands), by acquisition source (`utm_source` spellings such as `fb` / `Facebook` / `ig` merged) and by entry product (repeat rate and the next product bought). Cells under `--min-cell` customers show no rates; repeat rates carry a 95% range, and "clearly higher/lower" is used only when the range excludes the cohort-wide rate.
5. **Judges deep discounts on later behaviour only.** A deeper first-order discount lowers first-order CM by construction, so first-order CM is reported separately and the "customers acquired at deeper discounts repeated less" finding appears only when the deep group's repeat rate (95% Wilson range) or CM from repeat orders (95% interval) is clearly lower than the planned-depth band's. It is worded as an association to confirm with a holdout, not a cause.
6. **Recommends** this year's discount depth for new and existing customers, entry products to prospect with, post-purchase flow timing, the numbers to carry into the BFCM Holiday Budget Planner, and when to re-run.

Exit codes: 0 means the report was written, 2 means invalid input (with the file, line, column and reason). Numbers are parsed strictly: comma decimals (`75,00`), European-formatted columns (`1.234,56`), `nan`, `inf`, exponents and negative amounts are rejected, not guessed; `$1,234.56`, `€1,234.56` and `1234.56` are fine, and a refund written `(50.00)` or `-50.00` is read as a 50.00 refund. A `*_pct` key in economics.json between 0 and 1 (`gross_margin_pct: 0.64`) exits 2 for margin and planned discount and is flagged for fees and returns. **Net sales** means gross sales − discounts, before returns/refunds, excluding tax and shipping.

## Acting on the report

Present the headline and the numbered findings first, then walk through the recommendations. Each change needs the user's approval. Settings, flow layouts and templates are in `references/playbook.md`.

1. **Offer depth for new customers.** If customers acquired above the planned depth repeated clearly less (or added clearly less CM after their first order), propose testing a cap at the planned depth with a holdout before setting it; the gap may reflect who the discount attracts rather than the discount itself. Lower first-order CM alone is not evidence. Either way, check that codes can't stack on the sale unintentionally. Run BFCM Offer & Discount Config QA to check combinations and per-customer limits, and Promo Margin Guard if the depth itself is still open.
2. **Existing customers.** The report cites returning customers' BFCM orders only when there are at least 50, with the share discounted and a 95% range; test whether they'd buy without a discount. Consider early access at the same depth rather than a deeper code, and keep discounts out of the first post-purchase emails to BFCM buyers.
3. **Entry products.** Lead BFCM prospecting creative and gift-guide placements with the product whose buyers came back most, and cross-sell its usual next product.
4. **Post-purchase timing.** Set the cross-sell and follow-up delays in the post-purchase flow from the report's quartile days, and add a gift reminder before the ground-shipping cutoff (Holiday Shipping Cutoff Checker).
5. **Budget math.** In the BFCM Holiday Budget Planner, set `return_rate_pct` to the BFCM first-order refund rate if it is higher, keep the target MER on first-order contribution, and compare the plan's blended new-customer cost with the report's CM per BFCM customer at the comparison horizon.
6. **Re-run.** When the next horizon matures, and in January for the early read on this year's BFCM. Mark a recommendation **verified** only when a later run shows the change worked.

## Files

- `scripts/cohort_quality.py`: the analyzer. Local files only, deterministic output, optional `--json`.
- `DATA_CONTRACT.md`: the three input formats, column aliases, settings, every definition and formula, and the output.
- `references/playbook.md`: pulling orders from Shopify, reading the report, setting the BFCM offer, post-purchase flow timing, budget hand-off, the January read, and a summary template.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`: sample run. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`, `tests/make_cases.py`: 17 end-to-end checks on hand-checkable inputs. No network needed.
