# BFCM Holiday Budget Planner: Playbook

Every budget change in this playbook needs the user's explicit approval. After making one, read the budget back in the platform, then compare the next day's MER with the plan row.

## 1. MER vs platform ROAS

**Platform ROAS** is revenue the platform attributes to its own ads ÷ its own spend. Each platform counts a sale if its ad was clicked (or, for Meta, viewed) within its attribution window. A shopper who saw a TikTok video, clicked a Meta ad, then searched the brand on Google and bought is one sale on Shopify and three sales across the ad platforms. During BFCM this gets worse: shoppers touch more ads, buy on sale days anyway, and view-through windows catch purchases that email or organic drove.

**MER** (marketing efficiency ratio, also called blended ROAS) = store net sales ÷ total ad spend across every platform. It can't double-count, it reconciles to the bank, and it's the number the P&L actually responds to. Its weakness is that it doesn't say which channel did the work. That is fine for setting a budget: the budget question is "how much in total, and on which days", and the channel split is held to this year's working mix inside agreed bounds.

**Contribution margin** turns MER into money. At 25% off, a brand with 64% gross margin keeps far less per dollar than at full price. The planner computes the MER where ads exactly pay for themselves (breakeven) and the MER that leaves the margin you want (target). Spending to a platform ROAS target with no margin check is how brands have a record Black Friday and a loss-making November.

When someone asks for a ROAS target: ask what they're trying to protect (usually margin or cash), set that as a `margin_floor`, and quote the resulting MER. If the business has an approved MER definition, use it so the numbers match the dashboard.

## 2. Pulling the history

You need last year's Oct 1 – Dec 31 and this year's trailing 90 days. Use the store's time zone everywhere; mixing UTC platform days with local Shopify days shifts Black Friday revenue onto Thanksgiving.

**Shopify (sales and orders)**
- UI: Analytics → Reports → *Sales over time*, group by day. Export `Gross sales` and `Discounts`; `net_sales` = gross sales − discounts, before returns/refunds, excluding tax and shipping (Shopify's own "Net sales" column also subtracts returns; don't use that one unless you set the return rate to 0). Export `Orders`.
- New-customer orders: Analytics → Reports → *Customers over time* (first-time customers by day), or *Sales by customer type* / *Orders by first-time vs returning*.
- Admin API: query orders by `processed_at` in range, sum `subtotal_price` (after discounts, before tax and shipping) by local day, and count orders that were the customer's first order.

**Meta Ads**: Ads Manager → Reports, account level, breakdown by Day, column *Amount spent*. API: `GET /act_{id}/insights?level=account&time_increment=1&fields=spend`.
**Google Ads**: Reports → Predefined → Time → Day, column *Cost*, all campaigns. API: GAQL `SELECT segments.date, metrics.cost_micros FROM customer` (divide by 1,000,000).
**TikTok Ads**: Reporting → custom report, dimension Day, metric *Cost*. API: `report/integrated/get` with `stat_time_day`.
**Other**: Pinterest, Snap, Microsoft Ads, paid affiliate placements → `other_spend`. Leave out the column for any channel you don't run.

Don't add the platform conversion-value columns. If they arrive in a shared export, the planner ignores them and says so.

## 3. Phase strategy

**October: prospecting.** The cheapest new customers of the quarter. CPMs are still normal, so build audiences: broad prospecting, creative testing, lead-capture offers (early-access lists, SMS sign-ups). Spend at or a little above today's run rate. The plan's floor is today's run rate for this reason: cutting spend in October to "save it for Black Friday" starves the retargeting pools and email lists cyber week depends on. Lock the creative for cyber week by early November.

**Warm-up (Nov 1 – Nov 25).** Hold spend near the run rate for most of November, when CPMs climb and shoppers wait for deals. Then ramp. The planner lifts the days before Thanksgiving so no day jumps more than the max daily increase (30% by default). Large single-day jumps restart the learning phase on Meta and destabilize Google's bidding, so a budget that has to go from $1,000 to $6,700 must start climbing about a week before. Early-access offers to email and SMS lists land well here. Put early-access days in `sale_window` along with the main sale: the planner prices sale days at the discount and the rest of November at full price, so a full-price day can run a lower MER than a sale day and still keep more margin.

**Cyber week (Thanksgiving – the Tuesday after Cyber Monday).** Peak spend on Black Friday and Cyber Monday, where last year's curve shows demand highest. Saturday and Sunday usually sit lower. Run the guardrails at noon and 6 pm. Don't judge Black Friday in the first hours after a midnight launch: early-morning MER swings widely.

**Taper (Dec 2 – Dec 6 and after).** Demand drops fast but not to zero: shipping-cutoff and gift buyers are still in market. Step down no more than 50% a day, keep retargeting and gifting creative live, and keep the guardrails running. After the window, return to a normal December plan with the shipping-cutoff date in mind.

## 4. Running the guardrails

At each check time:
1. Shopify net sales so far today (gross − discounts), store time zone.
2. Spend so far today on every platform, summed. Platforms report with a lag of up to a few hours, so use each platform's latest number and note the time.
3. MER to date = 1 ÷ 2. Compare with today's lines in the daily plan (the "Pull back below" and "Scale above" columns). Each day's lines come from that day's own plan MER, so a day that runs exactly on plan never trips a pull back: pull back below the higher of breakeven MER and plan MER × 0.85, scale above the higher of target MER and plan MER × 1.10. Sale days carry the sale-day breakeven and target. The `--csv` daily plan carries the same lines on every day's row (`breakeven_mer`, `target_mer`, `pull_back_mer`, `scale_mer`, `check_times`), so a monitor such as BFCM Live Pulse can read today's row directly. If the report warns that a day is planned below breakeven, its pull-back line sits below breakeven: decide before the day whether to accept that loss or cut the budget.
- **Below the pull-back line:** cut the rest of today's budgets by 20% (not below the floor) and hold tomorrow at today's level. Check the site, checkout and offer first: a broken discount code or an out-of-stock hero product looks like an ad problem.
- **Above the scale line two checks running:** raise tomorrow's budget by up to the max daily increase. Put the increase on the channel with the most headroom inside its max share.
- **Every morning:** compare yesterday's full-day MER and contribution with the plan row. If actuals miss the base case by more than 15% for three days, add the new days to the history and re-plan.

## 5. Hand-off

**Budget Pacer.** It paces a month against a committed total. Use the plan's monthly totals as `committed_spend` rows in its `commitments.csv` (one per client and month). December in this plan covers only the window's days, so add the rest of December. Use the plan's daily rows to set each campaign's `min_daily_budget` and `max_daily_budget` bounds in its `campaigns.csv`.

**Meta Budget Scaling.** Give it the plan's Meta column as the daily budget path, the max daily increase as its scaling step limit, and each day's guardrail lines as its scale and pull-back triggers. It decides CBO vs ABO, which campaigns take the increase, and frequency limits.

**Promo Margin Guard.** If breakeven MER looks out of reach, run it on last year's promo orders to find the deepest discount each product can carry. Re-plan with the new `discount_pct`.

## 6. Plan summary template

```
**BFCM budget plan: <brand>** (planned, not yet approved)
- Total: $X over <window>, peak $Y on <peak day>. Cap: $Z.
- Breakeven MER: B at <d>% off (<sale dates>), B_full at full price. Target: T on sale days, T_full at full price (keeps <f>% after ads).
- Base case: $S net sales, MER M, $C contribution after ads. Low/high: $C_low / $C_high.
- Ramp starts <date>, +30% a day max. Taper from <date>.
- Guardrails (per day, from the daily plan): pull back below P_min–P_max by <time>; scale above Q_min–Q_max two checks running.
- Warnings: <list or "none">.
Needs approval: total budget, cap, discount depth, channel split.
```
