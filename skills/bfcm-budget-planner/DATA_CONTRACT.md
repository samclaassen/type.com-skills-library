# Data contract: bfcm-budget-planner

Two local files. UTF-8 (a BOM is tolerated). One currency and one time zone (the store's) throughout.

## 1. Daily history (`--history`, CSV, required)

Header row, then one row per day, every row with the same number of cells as the header. Column order doesn't matter; extra columns are ignored unless their name contains `spend` or `cost` (see below). Header names are matched after lower-casing, trimming, and turning spaces and hyphens into `_`, so `Meta Spend` and `meta-spend` both read as `meta_spend`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `date` | `YYYY-MM-DD` | yes | One calendar day in the store's time zone. No duplicates. |
| `net_sales` | number | yes | Store net sales for the day: **gross sales − discounts, before returns/refunds, excluding tax and shipping**. Shopify: Analytics → *Sales over time* (gross sales − discounts), or Admin API orders summed by `processed_at`. If your approved revenue definition is already net of returns, use it and set `return_rate_pct` to 0. |
| `total_orders` | integer | yes | Orders placed that day. |
| `new_customer_orders` | integer | yes | Orders from first-time customers. Must be ≤ `total_orders`. Used only for projected new customers and new-customer cost. |
| `meta_spend` | number | at least one of the four | Meta Ads Manager, *Amount spent*, all campaigns. Rename the column to `meta_spend`: platform header names such as *Amount spent* or *Cost* exit 2 because the channel can't be inferred. |
| `google_spend` | number | at least one of the four | Google Ads *Cost*, all campaigns (Search, Shopping, PMax, YouTube, Demand Gen). |
| `tiktok_spend` | number | at least one of the four | TikTok Ads Manager *Cost*. |
| `other_spend` | number | at least one of the four | Everything else paid: Pinterest, Snap, affiliates' paid placements, Microsoft Ads. |

**Numbers.** One rule for every numeric cell: `.` is the decimal separator; `,` is allowed only as a thousands separator (`^\d{1,3}(,\d{3})*(\.\d+)?$`); a leading or trailing currency symbol and spaces are stripped (`1234.56`, `1,234.56`, `$1,234.56`, `€1,234.56` all read as 1234.56). Rejected with exit 2, naming the file, line and column: comma decimals (`2255,74`, `28.320,00`), `nan`, `inf`, exponents (`1e9`), other text, and negatives (including `(50.00)`). An unquoted comma decimal splits the cell in two, so that row has one cell too many and is rejected too. If more than half of a column's values look like `1.234,56`, the file is rejected as European-format with its own message: export with `.` as the decimal separator. Empty cells count as 0.

**Unrecognised columns.** After header normalisation, any column whose name looks like spend (contains `spend`, `spent`, `cost`, `amount`, `budget` or `ad fee`) and isn't one of the four spend columns (or a platform-attributed column, below) stops the run with exit 2 and the expected names. So does any other column that mostly holds numbers, such as a channel exported as `Facebook Ads` or `Meta`, unless it is a common store metric that is clearly not ad spend (`gross_sales`, `discounts`, `returns`, `refunds`, `sessions`, `aov`, `orders`, `units` and similar). Rename an ad spend column to its `*_spend` name (put any other paid channel in `other_spend`) or remove the column. Text columns such as notes are ignored. No numeric column is silently dropped.

**Spend columns.** Include one for each paid channel you ran; leave out (or fill with 0) a channel you don't run. A missing spend column counts as 0 on every day, and at least one is required. A channel with no spend in this year's last 28 days gets no budget (unless `channel_shares` gives it a minimum) and no column in the report. The `--csv` and `--json` outputs always carry all four channels, with 0 for an unused one.

**Rows needed**
- **Last year:** every day from Oct 1 to Dec 31 of the previous year. The planner shifts last year so Black Friday lines up with Black Friday (2025's Nov 28 → 2026's Nov 27, a 364-day shift), and every shifted day of the plan window must be present with spend > 0 and sales > 0. Last October (14+ days) is used for the demand index.
- **This year:** the trailing days up to today, before the plan window. 90 recommended, 28 minimum before a warning. The last 14 calendar days set today's run rate; the last 28 set the demand index and the channel mix. The report warns when the history ends more than 14 days before today (`--as-of`, default the system date) or the plan start, whichever is earlier, and when 7 or more of the last 28 days have no row. If the last 14 days have no ad spend at all (ads paused), the report warns and the ramp starts from the average of the 14 days ending on the last day with spend, or from `--starting-daily-budget`.

**Platform-attributed columns** (any column whose name contains `roas` as a word, `attributed`, `conversion_value`, `purchase_value` or `platform_revenue`) are ignored, and the report says so.

**Validation** (any failure exits 2 with the reason): `date`, `net_sales`, `total_orders`, `new_customer_orders` and at least one spend column present, valid dates, no duplicate dates, every row the header's width, numbers parse under the rule above and are finite, no negatives, no unrecognised spend/cost column, `new_customer_orders` ≤ `total_orders`, no rows from this year on or after the window start, and every aligned last-year day present.

## 2. Unit economics and goal (`--economics`, JSON, required)

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `brand` | string | no | Printed in the title. |
| `currency` | string | no | Label only. Default `USD`. |
| `gross_margin_pct` | number 0–100 | yes | (Price − landed COGS) ÷ price, at **full price**. |
| `shipping_fulfillment_per_order` | number | yes | Average shipping label + pick/pack + packaging per order, net of shipping charged to the customer. |
| `payment_fee_pct` | number 0–100 | yes | Processor percentage fee (e.g. 2.9). |
| `payment_fee_fixed` | number | no | Fixed fee per order (e.g. 0.30). Default 0. |
| `return_rate_pct` | number 0–100 | yes | Expected share of BFCM net sales refunded. |
| `discount_pct` | number 0–99 | yes | Planned average BFCM discount depth off full price, on the days in `sale_window`. |
| `sale_window` | object | no | `{"start": "2026-11-24", "end": "2026-12-01"}`: the days the discount runs, early access included. Those days are priced at `discount_pct`; every other day of the window at full price. Default: the whole plan window (every day at the discount, the cautious choice). Must overlap the plan window; days outside the window are ignored. |
| `target` | object | yes | `{"type": "margin_floor", "min_contribution_pct": 12}` (keep at least 12% of net sales after ads) or `{"type": "mer", "mer": 4.5}`. Any target or key mentioning ROAS is refused (exit 3). |
| `total_budget_cap` | number | no | Hard cap on total ad spend for the window. |
| `max_daily_increase_pct` | number | no | Largest day-over-day budget increase. Default 30. |
| `max_daily_decrease_pct` | number | no | Largest day-over-day cut. Default 50. |
| `min_daily_spend` | number | no | Daily floor. Default: the ramp start (today's run rate, the average of the last 14 days; see `--starting-daily-budget`). |
| `channel_shares` | object | no | `{"meta": {"min": 45, "max": 65}, ...}` for `meta`, `google`, `tiktok`, `other`, in percent. A channel left out gets 0–100. Minimums must add up to ≤ 100 and maximums to ≥ 100. |
| `bfcm_aov` | number | no | Expected BFCM average order value, used for sale days. Default: last year's cyber-week net sales ÷ orders. |
| `full_price_aov` | number | no | Expected average order value on full-price days. Default: last year's net sales ÷ orders on the days aligned to this plan's full-price days (the sale AOV if there are none). |
| `demand_index` | number | no | Override the estimated year-over-year demand index (1.10 = 10% more demand than last year at the same spend). |
| `scenarios` | object | no | `{"low": 0.85, "high": 1.10}` demand multipliers. |
| `plan_year` | integer | no | Default 2026. |
| `black_friday` | `YYYY-MM-DD` | no | Default: the day after the fourth Thursday of November of `plan_year`. Must be a Friday (exit 2 otherwise): last year's curve is aligned on it. |
| `cyber_monday` | `YYYY-MM-DD` | no | Always the Monday after `black_friday`; if given, it must be that date (exit 2 otherwise). |
| `plan_window` | object | no | `{"start": "2026-11-01", "end": "2026-12-06"}`. Max 93 days. |
| `cyber_week` | object | no | `{"start": "2026-11-26", "end": "2026-12-01"}`. Must contain Black Friday and sit inside the window. |

Every number may be a JSON number or a string under the same rule as the history cells (`"$11.00"` is fine, `"2,9"` is not). `NaN`, `Infinity` and out-of-range values exit 2 naming the file and key. Validation failures exit 2 with the reason.

## 3. Formulas

Per $1 of net sales (gross sales − discounts, before returns/refunds, excluding tax and shipping), with discount `d`, gross margin `gm`, return rate `r`, AOV `A`. It is worked out twice: with `d = discount_pct` for days in `sale_window`, and with `d = 0` for the other (full-price) days. `c_t` is the day's rate.

| Line | Formula |
| --- | --- |
| Sales kept | `1 − r` |
| COGS on kept goods | `−(1 − gm) ÷ (1 − d) × (1 − r)` (returned goods are restocked) |
| Payment fees | `−(fee% + fixed fee ÷ A)` (not refunded on returns) |
| Shipping and fulfillment | `−shipping ÷ A` (spent on every order, returned or not) |
| **Contribution before ads `c`** | sum of the above |

- **Breakeven MER** = `1 ÷ c`, for sale days and for full-price days. If `c ≤ 0`, no MER breaks even.
- **Target MER** with margin floor `f` = `1 ÷ (c − f)`. With a MER target `m`, the implied margin is `c − 1 ÷ m`.
- **MER** = net sales ÷ total ad spend (every spend column).
- **Contribution after ads** = `c_t × net sales − ad spend` per day, summed over days. The margin floor applies to the whole window: `Σ contribution after ads ≥ f × Σ net sales`.

**Response curve.** For each phase `p` (warm-up = window start to the day before cyber week, cyber week, taper = after cyber week), elasticity `b_p` is the slope of `log(net_sales)` on `log(spend)` over last year's aligned days in that phase. It needs 5+ days, real spend variation and r² ≥ 0.2, otherwise 0.5 is used. A slope outside 0.20–0.70 is clamped to the nearer bound and reported as **assumed (clamped from X)**, not fitted, and listed under Warnings (with a note when the cyber-week curve, and so the Black Friday budget, rests on an assumption). The same fit on last October gives `b_oct`. A sensitivity line re-projects the planned spend with elasticity 0.40 in every phase.

**Demand index** = net sales on this year's last 28 days that had ad spend ÷ what last October's curve (`a × spend^b_oct`) predicts at those days' spend. Days with zero spend are left out of both sides and counted in the report; more than 20% left out is warned. An index outside 0.5–2.0 is warned; outside 0.25–4.0 the run stops with exit 2 unless `--accept-demand-index` is passed (a `demand_index` set in `economics.json` is used as given).

**Projected net sales** on plan day `t` at spend `s` = `index × LYsales_t × (s ÷ LYspend_t)^b_p`, where `LYsales_t` and `LYspend_t` are the aligned last-year day. Warm-up and taper days use a 7-day centered average of last year's days within the phase; cyber-week days use their own day.

**Allocation.** Every day gets spend up to where its next dollar earns the same contribution before ads, `k` (its marginal MER is `k ÷ c_t`), so the next dollar earns the same everywhere. `k` starts at 1 (marginal breakeven, the most contribution) and rises until the window meets the target (the margin floor, or the blended MER target) and total spend fits the cap. With one price on every day this is the same as equalizing marginal MER, starting at breakeven MER. Days where `c_t ≤ 0` get only the floor. Then each day is held at or above the floor; earlier days are lifted so no day needs more than the max daily increase; and a forward pass from the ramp start (today's run rate; if that is $0, the last 14 days with spend; or `--starting-daily-budget`) enforces the increase and decrease limits in whole dollars, rounded so neither limit is exceeded (a 50% cut is never −50.01%). If even the floor plan costs more than the cap, the plan is a flat `cap ÷ days` per day and the report warns. If even the floor plan misses the target, the plan holds the floor and the report warns.

**Channels.** This year's last-28-day spend mix, clamped to `channel_shares` (the excess is redistributed in proportion to the remaining channels), then split into whole dollars that add up to each day's total.

**Scenarios.** Low and high multiply projected sales by `scenarios.low` / `scenarios.high` at the same spend.

**Guardrails (per day).** With `m_t` = the day's base-case plan MER and breakeven/target MER at the day's pricing:
- `pull_back_mer[t] = max(breakeven MER, m_t × low)`, `scale_mer[t] = max(target MER, m_t × high)`, at 2 decimals.
- Invariant, checked at the 2-decimal values written out: `pull_back_mer[t] < m_t < scale_mer[t]` on every day. When breakeven sits just under `m_t` the pull-back line is rounded down to stay below it. When the day is planned at or below breakeven, the pull-back line is `m_t × low` (below breakeven) and the report warns that those days are planned to lose money; the line is never moved above the plan.
- Check at 2 pm (warm-up, taper) or noon and 6 pm (cyber week), store time.
- The report's phase table has one row per run of days with the same phase and pricing (a `sale_window` that starts or ends inside a phase splits it) and shows the min–max of those days' lines.

**No organic baseline.** Projected sales are tied to ad spend (`s^b`), so the model projects $0 at $0 spend. It does not separate organic or email-driven sales from paid demand.

## 4. Outputs

- **stdout:** the Markdown plan. Channels with no budget are left out of its tables.
- **`--csv`:** one row per plan day, in this column order. Columns are only ever added at the end; existing names and order don't change.

| Column | Format | Meaning |
| --- | --- | --- |
| `date` | `YYYY-MM-DD` | Plan day, store time zone. |
| `phase` | text | `warm-up`, `cyber week` or `taper`. |
| `tag` | text | `Thanksgiving`, `Black Friday`, `Cyber Monday`, or empty. |
| `total_budget` | integer | The day's total ad budget, whole dollars. |
| `meta_budget`, `google_budget`, `tiktok_budget`, `other_budget` | integer | The day's budget per channel; they add up to `total_budget`. All four are always present; 0 for a channel with no share. |
| `proj_net_sales_low`, `proj_net_sales_base`, `proj_net_sales_high` | number, 2 decimals | Projected net sales in the low, base and high scenarios. |
| `proj_mer_base` | number, 4 decimals | Base-case projected MER (`proj_net_sales_base ÷ total_budget`). Empty if the budget is 0. |
| `proj_contribution_base` | number, 2 decimals | Base-case contribution after ads: `c_t × proj_net_sales_base − total_budget`. |
| `ly_date` | `YYYY-MM-DD` | Last year's aligned day (same weekday, Black Friday on Black Friday). |
| `ly_net_sales` | number, 2 decimals | Last year's net sales on `ly_date`. |
| `ly_ad_spend` | number, 2 decimals | Last year's total ad spend on `ly_date`. |
| `breakeven_mer` | number, 2 decimals | MER at which the day's ads exactly pay for themselves at its pricing (sale or full price). Empty if no MER breaks even. |
| `target_mer` | number, 2 decimals | MER that keeps the margin floor at the day's pricing (with a MER target: that MER). Empty if out of reach. |
| `pull_back_mer` | number, 2 decimals | The day's own pull-back line, always below `proj_mer_base`: if MER to date is below it at a check time, cut the rest of today's budgets 20% and hold tomorrow at today's level. |
| `scale_mer` | number, 2 decimals | The day's own scale line, always above `proj_mer_base`: if MER to date is above it by the first check time two days running, raise tomorrow's budget by up to the max daily increase. |
| `check_times` | `HH:MM`, `;`-separated | When to check MER to date, store time, 24-hour: `14:00` (warm-up, taper) or `12:00;18:00` (cyber week). |

The last five columns are the day's guardrails: `pull_back_mer` and `scale_mer` equal the "Pull back below" and "Scale above" columns of that day's row in the report's daily plan, with the same 2-decimal values, and fall inside the phase table's min–max range; `breakeven_mer`, `target_mer` and `check_times` repeat the phase table. A monitor (for example BFCM Live Pulse on Black Friday) can read today's row and compare MER to date at each check time.

- **`--json`:** everything in the report, machine-readable. Infinite values (unreachable MER) are written as `null`. Each `rows` entry also carries `on_sale`, `contribution_rate` (`c_t`) and the same guardrail values as the CSV (`check_times` as a list); `guardrails` lists the phase-table rows with `pull_back_min`/`pull_back_max` and `scale_min`/`scale_max`; `fits` marks each curve `fitted`, `clamped` and its `raw_b`; `sensitivity` holds the elasticity-0.40 re-projection; `demand_index_skipped_days` counts zero-spend days left out of the index; `economics.full_price` holds the full-price unit economics; `window.sale_window` holds the sale days; `active_channels` lists the channels with budget.

## 5. Command-line options

| Option | Meaning |
| --- | --- |
| `--history`, `--economics` | The two input files (required). |
| `--csv`, `--json` | Also write the daily plan CSV / the structured plan JSON. |
| `--as-of YYYY-MM-DD` | Today's date, default the system date. Used only for the stale-history warning; the sample run pins it so its output is reproducible. |
| `--starting-daily-budget N` | The budget the ramp starts from (and the default daily floor). Default: the last 14 days' average spend, or the last 14 days with spend if ads were paused. |
| `--accept-demand-index` | Plan even when the estimated demand index is outside 0.25–4.0. |
