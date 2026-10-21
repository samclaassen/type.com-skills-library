---
name: bfcm-budget-planner
description: Plan a DTC brand's paid-media budget for Black Friday / Cyber Monday from MER (store revenue ÷ total ad spend) and contribution margin, not platform ROAS. Computes breakeven and target MER at the planned discount, shapes a daily budget per channel from last year's BFCM curve with a learning-safe ramp and a hard cap, projects revenue, MER and contribution per day in low/base/high scenarios, and sets pull-back and scale guardrails for every day. Read-only.
---

# BFCM Holiday Budget Planner

Black Friday 2026 is **Nov 27** and Cyber Monday is **Nov 30**. Most BFCM budgets get set by asking each ad platform what it returned last year. That is the wrong number. Meta, Google and TikTok each claim credit for the same sale, so their ROAS figures add up to more revenue than the store actually made. A widely shared r/FacebookAds BFCM prep thread ("BFCM Prep you should do THIS WEEK", https://www.reddit.com/r/FacebookAds/comments/1waqvp4/) makes the same point: plan off total revenue against total spend.

This skill plans the whole window from two numbers the business can verify: **MER** (Shopify net sales ÷ total ad spend across every platform) and **contribution margin** (what is left after COGS, the discount, shipping, payment fees and returns). It tells you the MER you need to break even at your discount depth, how much to spend each day and on which channel, what that should bring in, and when to pull back or push harder.

It sits next to three other library skills and does not repeat them:
- **Promo Margin Guard** settles how deep a discount each product can carry. Run it first if the discount depth is still open.
- **Budget Pacer** tracks a month's committed spend once it is live. This plan's monthly totals are its commitments.
- **Meta Budget Scaling** handles Meta campaign structure and day-to-day scaling. This plan's Meta column is its daily budget path.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In type.com, read them with the skill-file tools. Anywhere else, the type.com Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `bfcm-budget-planner`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite.

## When to use this

- Someone asks "how much should we spend on BFCM?", "what MER do we need at 25% off?", or "how do we ramp into Black Friday?"
- A founder or finance lead wants the BFCM ad budget tied to margin, not to platform ROAS.
- An agency is setting BFCM budgets for each brand it manages.
- October and early November: plan once, then re-run weekly with the new days of history. During cyber week, re-run if actuals miss the base case for three days.

## Operating rules

1. **Read-only.** The script reads two local files and prints a plan. It never touches an ad account. Never change a budget, campaign or bid unless the user approves that specific change. When they do, make it, read the setting back, and compare the next day's MER with the plan row.
2. **MER and contribution, never platform ROAS.** If someone asks to plan from platform ROAS, explain why not and convert the goal into a margin floor or a target MER. The script refuses a ROAS target (exit 3) and ignores ROAS columns in the history.
3. **Say what is estimated.** Elasticities, the demand index and every projected number are estimates fitted on last year's days. Quote them with the data range they came from, and give the low/base/high spread rather than a single number.
4. **Use the business's definitions.** If the business has approved revenue and spend definitions, use them for `net_sales` and spend. The planner's own definition is net sales = gross sales − discounts, before returns/refunds, excluding tax and shipping. If the business's revenue is already net of returns, set the return rate to 0.
5. **Never invent economics.** Gross margin, shipping cost, fees, return rate, discount depth and sale dates come from the user or their finance data. If one is missing, ask. Don't guess.
6. Say which state each item is in: **planned**, **approved**, **applied**, or **verified against actuals**.

## Gathering the inputs

The planner reads two files. Their exact formats are in `DATA_CONTRACT.md`.

**1. `history.csv`: one row per day.** Last year's October 1 – December 31, plus this year's trailing 90 days up to today. Columns: `date, net_sales, total_orders, new_customer_orders`, plus one spend column per paid channel you run: `meta_spend`, `google_spend`, `tiktok_spend`, `other_spend` (leave out any you don't run).
- Sales and orders come from Shopify: Analytics → Reports → *Sales over time* by day (net sales = gross sales − discounts, before returns/refunds, excluding tax and shipping), and *Customers over time* or the first-time/returning orders report for new-customer orders. With the Shopify Admin API, sum orders by `processed_at` day in the store's time zone.
- Numbers must use `.` as the decimal separator (`1,234.56`, `$1,234.56` and `€1,234.56` are fine). Comma decimals (`2255,74`), `nan`, `inf` and exponents are rejected (exit 2, naming the file, line and column) rather than guessed. Headers are matched case-insensitively, with spaces and hyphens read as `_` (`Meta Spend` = `meta_spend`); a column that looks like spend or cost under any other name stops the run so it is never silently dropped.
- Spend comes from each ad platform's daily report: Meta Ads Manager (*Amount spent*), Google Ads (*Cost*), TikTok Ads Manager (*Cost*); rename each export column to its `*_spend` name (for example *Amount spent* → `meta_spend`), because the platforms' own header names are rejected rather than guessed. Any other column of numbers the planner doesn't recognise (for example a channel exported as *Facebook Ads*) also stops the run. Put affiliate, Pinterest, Snap and anything else in `other_spend`. Use the store's time zone everywhere.
- In type.com, pull these through the connected Shopify and ad-platform integrations. Read-only scopes are enough. Never ask the user to paste a key into chat.

**2. `economics.json`: unit economics and the goal.** Gross margin %, shipping and fulfillment cost per order, payment fee %, expected return rate, the planned BFCM discount depth and the days it runs (`sale_window`, early access included), and a target: a **contribution-margin floor** (recommended, for example "keep at least 12% of net sales after ads") or a **target MER**. Days outside the sale window are priced at full price; without a sale window every day of the plan is priced at the discount, which understates November margin if the sale is shorter. Optional: total budget cap, channel min/max shares, max day-over-day increase (default 30%, for learning-phase safety), the plan window (default Nov 1 – Dec 6) and cyber week (default Nov 26 – Dec 1).

## Running it

```bash
python3 scripts/plan_budget.py --history history.csv --economics economics.json [--csv plan.csv] [--json plan.json] \
    [--as-of YYYY-MM-DD] [--starting-daily-budget N] [--accept-demand-index]
```

`--as-of` is today's date (default: the system date), used only to warn when the history is stale; the sample run pins it. `--starting-daily-budget` sets the budget the ramp starts from (default: the last 14 days' average spend). `--accept-demand-index` plans even when the estimated demand index is outside 0.25–4.0, which otherwise stops the run.

`bash examples/run.sh` runs it on the bundled sample store, Fernhill Goods (fictional): a Shopify home and kitchen brand doing about $1.05M a year, spending about $950 a day on Meta, Google and TikTok at a blended MER of about 3.0, with a 25% off sale Nov 24 – Dec 1. Last BFCM it ran at a blended MER of 4.00 ($120,000 net sales on $30,000 of ads). The sample plan holds Black Friday spend about level with last year and keeps 12% of net sales after ads across the window.

What it does:

1. **Prices one dollar of sales.** Contribution per $100 of net sales after COGS, payment fees, shipping and returns, at the planned discount on sale days and at full price on the other days. **Breakeven MER** = 100 ÷ that. **Target MER** = 100 ÷ (that − your margin floor).
2. **Reads last year's curve.** It shifts last year's days so Black Friday lines up with Black Friday (364 days for 2025 → 2026), then shows each phase's share of the window's revenue, its MER and its best day.
3. **Estimates diminishing returns per phase.** A log-log fit of daily sales on daily spend (last October, warm-up, cyber week, taper), printed with the days and spend range it was fitted on. Weak fits fall back to 0.5 and are flagged. A fit outside 0.20–0.70 is clamped to the bound and labelled **assumed (clamped from X)**, not fitted, and listed under Warnings; if the cyber-week curve is assumed, the report says the Black Friday budget rests on it. A sensitivity line shows the same budget at elasticity 0.40. A **demand index** compares this year's last 28 days against last October's curve, so the plan scales with this year's growth. Days with no ad spend are left out of both sides of that ratio and counted; the report warns when more than 20% were left out or the index is outside 0.5–2.0, and stops (exit 2) outside 0.25–4.0 unless you pass `--accept-demand-index`.
4. **Builds the daily plan.** Spend goes where the next dollar earns the most contribution (a full-price dollar of sales is worth more than a sale-day one), until the next dollar would lose money, the window's margin hits your floor, or the cap runs out. Then: never below today's run rate, never more than the max daily increase (so the ramp starts days before Thanksgiving), never more than a 50% drop in a day (whole dollars, rounded so both limits hold), and split across channels by this year's mix held inside your min/max.
5. **Projects every day** in low (0.85x demand), base and high (1.10x) scenarios: net sales, MER and contribution after ads, plus per-phase and window totals and new-customer cost.
6. **Sets guardrails for every day** from that day's own plan MER: pull back if MER to date is below the higher of breakeven MER and plan MER × 0.85 by the check time; scale if above the higher of target MER and plan MER × 1.10 two days running. Every pull-back line sits below its day's plan MER and every scale line above it, so an on-plan day never trips a pull back. If a day is planned at or below breakeven, its pull-back line stays below the plan and the report warns that the day is planned to lose money. The daily table and `--csv` carry each day's lines (`pull_back_mer`, `scale_mer`, with breakeven and target MER and check times); the phase table shows their range.
7. **Warns** when the plan can't be trusted or can't work: platform-ROAS columns ignored, history too short, stale (ending more than 14 days before today or the plan start) or missing 7+ of the last 28 days, ads paused in the last 14 days (the ramp then starts from the last 14 days with spend, or `--starting-daily-budget`), zero-spend days left out of the demand index, an assumed elasticity, breakeven MER unreachable at this discount, target unreachable, days planned below breakeven, budget cap below the ramp minimum, or days where spend runs past twice last year's (extrapolated).

Limits: there is no organic baseline. Every projected sale is tied to ad spend, so the model projects $0 of sales at $0 spend; read the projection as paid-driven demand, not total store revenue. Full-price days use last year's non-sale AOV on the matching days (or `full_price_aov`); sale days use last year's cyber-week AOV (or `bfcm_aov`).

Exit codes: 0 means the plan was written, 2 means invalid input (with the reason: unparseable or non-finite number, unrecognised spend column, Black Friday not a Friday, implausible demand index, and the checks in `DATA_CONTRACT.md`), 3 means the target was platform ROAS and the plan was refused.

## Acting on the report

Present the headline numbers, the warnings, and the daily table. Then, each with the user's approval:

1. **Settle the discount and the goal.** If breakeven MER is above what last year's cyber week delivered, last year's peak spend loses money at this discount: the plan cuts it back, and the discount may be too deep. Run Promo Margin Guard and re-plan. Agree the margin floor with whoever owns the P&L.
2. **Prospect in October.** Keep spend at or a little above today's run rate through October and early November to fill retargeting pools and email/SMS lists cheaply. The plan holds today's run rate as its floor for this reason.
3. **Load the plan into the pacing tools.** Use the monthly totals as Budget Pacer commitments and the Meta column as Meta Budget Scaling's daily budget path. Set budgets in the ad platforms only after approval, and read them back.
4. **Run the guardrails daily.** At the check time, compute MER to date from Shopify net sales and the sum of every platform's spend. Pull back or scale by that day's lines in the daily table. Never use a platform's own ROAS for the call. A monitor such as BFCM Live Pulse can read the same lines from today's row of the `--csv` plan (`pull_back_mer`, `scale_mer`, `check_times`).
5. **Re-plan with actuals.** Add the new days to `history.csv` and re-run weekly in November, and any time actuals miss the base case by more than 15% for three days running.

Full phase strategy, the MER-vs-ROAS argument, data-pull steps and the hand-off formats are in `references/playbook.md`.

## Handoffs

Exactly what each downstream skill takes from this plan:

| To | File | Notes |
| --- | --- | --- |
| BFCM Live Pulse | the `--csv` daily plan | Used unchanged. It reads today's row: the budgets, projected net sales and plan MER, and the guardrail columns (`breakeven_mer`, `target_mer`, `pull_back_mer`, `scale_mer`, `check_times`). |
| Holiday Shipping Cutoffs | the `--csv` daily plan, plus `--aov <planner AOV>` | It turns `proj_net_sales_base` into orders with the AOV printed under "Unit economics"; without `--aov` it exits 2. |
| Holiday Support Macros | the `--json` plan | Its `rows` give each day's projected orders. Pass **last season's** orders only as history; remove this season's dates. |
| BFCM Cohort Quality | `economics.json` | Optional; reuses the margin inputs. |
| Budget Pacer | the monthly totals under "Hand-off" | Each month's total is that month's committed spend; the daily rows are campaign bounds. December covers only the window's days. |
| Meta Budget Scaling | the Meta column of the daily plan | Its daily budget path, with the max daily increase as the step limit. |

## Files

- `scripts/plan_budget.py`: the planner. Local files only, deterministic output.
- `DATA_CONTRACT.md`: the `history.csv` and `economics.json` formats, validation rules, every formula and every `--csv` column.
- `references/playbook.md`: MER vs ROAS, pulling history from Shopify and the ad platforms, phase strategy (October prospecting, warm-up, cyber week, taper), guardrail operation, and hand-off to Budget Pacer and Meta Budget Scaling.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`: sample run. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`: 22 end-to-end checks. No network needed.
