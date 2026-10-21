# BFCM Cohort Quality: Playbook

Every change in this playbook needs the user's explicit approval. After making one, read the setting back, then re-run the report when the next horizon matures. Mark a change **verified** only when a later run shows the effect.

## 1. Pulling the data

**Orders: full history, one row per order.** The analysis is only as good as "first-ever order". Export every order since the store opened, not just last year: a customer who bought in 2023 and came back on Black Friday is not a new customer. If the export must be filtered to stay small, filter on each customer's first-ever order date and pass `--history-since` with the store's first order date.

- **In type.com (recommended):** use the connected Shopify integration with read-only scopes. Shopify only returns the last 60 days of orders unless the app has `read_all_orders`. Page through `orders` 100 at a time with the GraphQL query in `DATA_CONTRACT.md` §1; for stores with more than ~50,000 orders, a bulk operation (`bulkOperationRunQuery`) with the same fields is faster. GraphQL timestamps are UTC, so pass `--timezone` with the store's zone (Shopify admin → Settings → General → Time zone).
- **By hand:** Orders → Export → All orders → CSV. The file loads as is. It has no UTM fields, so the source cut is skipped; everything else works.
- **Customer key.** Use Shopify's customer ID. If guest checkout creates a new customer per order (every customer looks like a one-time buyer), use the email as `customer_id` instead and say so in the summary.
- **Leave out** test orders, wholesale and draft orders, and gift-card-only purchases (gift card sales aren't revenue until redeemed). POS orders count only if they carry a customer.
- **Definitions.** `net_sales` is gross sales − discounts, before returns/refunds, excluding tax and shipping (the same definition across the BFCM collection). If the only figure available is already net of refunds (for example a business's own net sales definition), say so, drop `refunded_amount` and set `return_rate_pct` to 0 so refunds aren't subtracted twice.

**Spend: daily, every paid channel, store time zone.** Meta Ads Manager *Amount spent*, Google Ads *Cost*, TikTok *Cost*, plus Pinterest, Snap, affiliates and anything else paid in `other_spend`. It must cover every day of each cohort window. The Budget Planner's `history.csv` already has these columns for last October to December; add January–February for the third cohort.

## 2. Reading the report

1. **Headline first:** CM per new customer at the longest horizon all cohorts have reached. This is the number to quote.
2. **Offer problem or retention problem?** Compare the gap at 30 days with the gap at 180 days. A gap that is already there at 30 days comes from the first order: deeper discount, smaller basket, more refunds. A gap that widens after 30 days comes from fewer repeat purchases. In the Fernhill sample most of the gap is there on day 30 ($26.59 vs $40.91), and it widens to $31.49 vs $52.54 by day 180: both the offer and retention are weaker.
3. **Payback** says whether the cohort earned back what it cost to acquire, on contribution. "Not by day N (X% recovered)" is a fact about the days observed so far, not a forecast. Don't extrapolate it.
4. **Discount dependence** is the share of repeat orders that used a discount. A high share may mean the customer learned to wait for a code (a hypothesis; the data shows the share, not the reason). Check the "Seasons differ" line: an October cohort's first months include BFCM itself, so some of its discounted repeat orders were the sale.
5. **Cuts overlap.** The weakest source often took the deepest discounts (in the sample, 49% of TikTok-acquired customers took more than 25% off vs 22% overall), so don't read the source and discount cuts as two separate effects. "Within noise" means the 95% range includes the cohort-wide rate: no conclusion. Cells under `--min-cell` customers show no rates at all.
6. **Not mature means unknown**, not zero. Quote the maturity date instead.

## 3. Setting the BFCM offer

**New customers: depth.** Judge deep first-order discounts on what happened after the first order. Total CM always falls at a deeper discount because the first order earns less, so it can't show the customers were weaker. Act only when the report says customers acquired above the planned depth repeated clearly less, or added clearly less CM from repeat orders (non-overlapping 95% ranges). Even then it may reflect who the discount attracts: propose a holdout test of a cap at the planned depth before setting one.
- Shopify admin → Discounts → open each code (welcome, creator, influencer, old seasonal codes) → **Combinations**: during the sale, untick *Product discounts* and *Order discounts* so the code can't stack on the automatic sale discount. Set **Maximum discount uses** → *Limit to one use per customer*, and an end date. BFCM Offer & Discount Config QA checks all of this from an export of the discounts.
- If someone wants a deeper first-order offer anyway, run it as a test: one prospecting audience at the planned depth, one deeper, and compare CM per customer at 60 days in the January read. Price the deeper offer with Promo Margin Guard first.

**Existing customers: access, not depth.** The report counts returning customers' BFCM orders and the share that used a discount (recommendation 2 cites them only with at least 50 orders, with a 95% range). Most of those orders may have been discounted, so this doesn't show they'd come back without one: test it (e.g. hold the code back from a random slice). Consider early access at the same depth (an SMS/VIP window before the main sale) rather than a deeper code, and leave them out of new-customer welcome offers.

**Post-purchase: no discount first.** BFCM buyers already lean on codes. Open their post-purchase flow with the cross-sell at full price; hold any winback code until after the report's three-quarter day.

## 4. Entry products and prospecting

- Lead BFCM prospecting creative, gift-guide placements and collection order with the entry product whose buyers came back most (in the sample, `ceramic-pour-over-set`: 23.9% vs 12.3%).
- Build the natural pair into the offer: the entry product with its usual next product (`stoneware-mug-set`), at the same depth, not deeper.
- Keep products whose buyers rarely return in the sale, but don't build acquisition creative around them.
- Check stock for the entry product before scaling it (Inventory Stockout Risk).

## 5. Post-purchase flow timing

Use the report's quartiles for the BFCM cohort (repeaters within the comparison horizon): P25, median, P75. A Klaviyo layout:

| Step | Delay after the first order | Content |
| --- | --- | --- |
| 1 | day 2–5 | Thank you, care and use of what they bought. No discount. |
| 2 | by P25 (day 51 in the sample) | Cross-sell the entry product's usual next product, full price. |
| 3 | before the median (day 79) | Second cross-sell or a bundle, full price. |
| 4 | after P75 (day 122) | Move non-buyers to the regular newsletter; any winback code goes here, not earlier. |

- Trigger: *Placed Order*, filtered to first-time customers (Klaviyo flow filter: *Placed Order* equals 1 over all time, which counts the triggering order).
- **Gift season:** BFCM buyers place some second orders before Christmas (16 of 104 in the sample). Add a gift reminder that lands a few days before the last safe ground-shipping date (Holiday Shipping Cutoff Checker), separate from the cross-sell.
- Don't let a generic browse- or cart-abandonment discount reach BFCM buyers in their first weeks; it undoes step 2.

## 6. Budget math with the BFCM Holiday Budget Planner

The planner sets the BFCM budget from contribution per order at the planned discount. This report adds three numbers:

1. **Return rate.** If BFCM first orders refunded more than `return_rate_pct` (9.2% vs 7% in the sample), put the BFCM figure into the planner's `economics.json` for this year's plan.
2. **Don't buy BFCM customers on lifetime value.** Keep the planner's target MER on first-order contribution. The report's "added after the first day" figure shows how little repeat value BFCM customers bring ($5.45 by 180 days in the sample vs $13.69 for October customers); it doesn't justify a lower MER target.
3. **Affordable new-customer cost.** CM per BFCM customer at the comparison horizon ($31.49 in the sample) is the most a BFCM new customer can cost and still pay back within that horizon. If the planner's projected blended new-customer cost is higher, either accept BFCM as a cash event that buys customers at a known loss, or change the offer (cap stacking, lead with better entry products) so first-order contribution rises. Say which.

Cohort LTV Analyzer's monthly cohorts mix BFCM buyers into November and December. For BFCM budget questions, use this report's BFCM-only numbers; for blended monthly LTV and payback trends, use Cohort LTV Analyzer.

## 7. The January read

- **When:** the 30-day horizon matures 30 days after the window's last day (Dec 31, 2026 for BFCM 2026) and the 60-day one 30 days later (Jan 30, 2027). Holiday return windows often run to Jan 31, so refunds and CM still move until February. Re-run then.
- **How:** compare like with like, this year's BFCM against last year's at the same horizon:
  `--year 2026 --compare "BFCM 2025=2025-11-24:2025-12-02" --compare "October 2026=2026-10-01:2026-10-31"`.
- **What a 30-day read can tell you:** first-order economics (AOV, depth, refunds so far, CM on day 0–30), whether the stacking fix worked (the deep-discount band should be small), and early repeats (inflated by December gift shopping).
- **What it can't:** 180-day value or payback. Those rows print "not mature". Never compare this year's 30-day number with last year's 180-day number.

## 8. Summary template

```
BFCM {year} cohort quality, as of {as_of} (detected)
- Worth: {cm} contribution per new customer at {H} days vs {cm_cmp} for {comparison} ({diff}).
- Cost: {cost} blended per new customer. Payback: {payback}.
- Came back: {repeat} within {H} days vs {repeat_cmp}. Repeat orders with a discount: {dd} vs {dd_cmp}.
- Weakest: {deep-discount band / source}. Best entry product: {product} → next buy {next}.
- Refunds: {refund_rate} of first-order net sales vs {return_rate} planned.

Recommended for BFCM {next_year} (not applied; each needs approval):
1. ...
Next read: {date} ({horizon}-day horizon matures).
```
