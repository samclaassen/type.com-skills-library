---
name: promo-margin-guard
description: Compute the true contribution margin of a discount or promotion after COGS, shipping, payment fees, refunds and expected returns, identify which promo codes lost money, solve the breakeven discount depth for every product, and list the SKUs to exclude or cap in the next sitewide sale.
---

# Promo Margin Guard

A promotion's revenue is easy to see and its cost is not. This prices the
whole promotion: what was given away, what the goods cost, what shipping and
fees ate, and what the returns that have not landed yet will take back. Then
it works backwards to the deepest discount each product can actually carry.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `promo-margin-guard`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- After any sale, code, or campaign, to settle whether it made money.
- **Before** a sitewide sale, to decide the depth and the exclusion list.
- When a founder or finance lead asks "should we run 20% or 30% off?"
- When a specific code is suspected of being abused or stacked.
- Quarterly discount hygiene: which evergreen codes (welcome, winback, VIP)
  are quietly unprofitable.

## Gathering the input

Three local files; the script never touches the network.

**1. Promotion orders** (`--orders`), one row per order.
- **Shopify**: Orders export filtered to the promotion window, or the
  Discounts report drilled into orders. Keep `Discount Code`, discount amount,
  subtotal *before* discount, shipping charged, and refunded amount.
- Include a control group if you can: orders in the same window with **no**
  code. Leave `promo_code` empty and they bucket as `(no code)`, giving you a
  baseline margin rate to compare the promo rates against.
- `gross_revenue` must be **before** the discount. If the export only has
  post-discount revenue, add the discount back before running, or every
  breakeven number will be wrong.

**2. Order line items** (`--lines`), one row per order line. Needed to push
COGS, shipping, fees, and refunds down to SKU level. Shopify's line-item
order export or a warehouse `order_lines` table both work.

**3. Product costs** (`--products`): SKU, landed unit cost, list price, and —
important — `return_rate_pct` per SKU. Costs usually come from an ERP, 3PL,
or the purchasing spreadsheet, not the storefront. Return rates come from the
returns platform (Loop, Returnly, AfterShip) or from a returns report over the
last 6–12 months. Typical DTC ranges to sanity check against: 4–10% for
accessories and home goods, 12–20% for knitwear and tops, 25–45% for footwear,
denim, and fitted dresses. If you only have a blended rate, pass
`--return-rate-pct` and say so in the writeup.

## Running it

```bash
python3 scripts/promo_margin.py \
  --orders examples/promo_orders.csv \
  --lines examples/promo_lines.csv \
  --products examples/products.csv \
  --fulfillment-cost 2.85 \
  --return-shipping-cost 8.50 \
  --restock-recovery-pct 75 \
  --planned-discount-pct 25
```

- `--planned-discount-pct` is the depth of the sale you are about to run; the
  exclusion list is built against it.
- `--payment-fee-pct` / `--payment-fee-fixed` default to 2.9% + 0.30. A
  `payment_fee` column in the orders file overrides them.
- `--fulfillment-cost` is pick, pack, and box per order.
- `--return-shipping-cost` is what one returned unit costs to get back and
  process; `--restock-recovery-pct` is how much of its unit cost you recover
  by reselling it. Footwear and apparel rarely recover 100%.
- `--min-unit-margin` raises the bar for the breakeven solve above zero, for
  example `--min-unit-margin 5.00` to require five dollars of contribution.
- `--json` for machine-readable output. Each promotion carries a
  `bookedToExpectedBridge` with the same cent-exact steps as the text bridge;
  use it rather than rounding the raw components yourself.

There is no date argument and no clock read: every figure comes from the data
and the stated assumptions. Bad input exits 2 naming the file, row, and column.

## Reading and presenting the output

- **Promotion P&L**, one row per code, sorted worst margin first, with a
  TOTAL row. `returns` combines refunds already taken, the revenue expected to
  come back, and return handling.
- **Booked versus expected.** Booked margin counts only what has already
  happened: refunds already processed, plus the return handling and the
  recovered stock on those same returned units. Expected margin is booked
  minus the full effect of the returns still to come: the revenue they will
  refund, minus their handling, plus the stock they will recover. The
  **booked to expected bridge** prints those three steps per promo and they
  add up to the cent. For a promotion that ran in the last few weeks, booked
  margin is flattering and expected margin is the honest number — lead with
  expected and use the bridge to reconcile to what finance has seen so far.
- **Promotions that lost money**, with per-order loss and the returns cost
  that pushed them under. `[THIN]` entries cleared costs but on under 10%
  contribution margin.
- **Breakeven discount depth by product**, sorted shallowest first. `given` is
  what the SKU actually received; `headroom` is breakeven minus given. A
  negative headroom means that SKU has already been discounted past its
  breakeven.
- **Exclude from the next sale**, split into hard caps and thin-headroom
  warnings.

When reporting to a human:

1. Open with the total: full-price value, discount given away, and expected
   contribution margin as a percentage of net revenue. Compare it with the
   `(no code)` baseline row if one exists.
2. Name the codes that lost money and the per-order loss. "FLASH40 lost $7.31
   on every order it touched" lands; "-8.1% margin" does not.
3. Give the exclusion list as a concrete instruction for the next sale: these
   SKUs out, these capped at this depth, everything else fine at the planned
   depth.
4. Separate the *depth* problem from the *mix* problem. If the codes are fine
   but the discounted mix skewed to low-margin, high-return SKUs, the fix is
   exclusions, not a shallower sitewide depth.
5. Caveat acquisition: a money-losing welcome code can still be correct if the
   customers it buys repeat. That question belongs to a cohort LTV analysis,
   not to this one — say so rather than calling the code a mistake outright.

## Limits

- **Contribution margin only.** No ad spend, no overheads, no payroll, no
  warehousing rent, no platform or app fees. A promo that "made money" here
  covered its variable costs, not the business.
- **No acquisition value.** A first-order discount that loses money on order
  one may be the cheapest customer acquisition the brand has. This skill has
  no view of repeat purchasing; pair it with cohort analysis before killing a
  welcome or winback code.
- **No incrementality.** It cannot tell you whether a discounted order would
  have happened anyway at full price. The `(no code)` bucket is a comparison,
  not a control group. Every conclusion is "what this promotion earned", never
  "what this promotion caused".
- **Expected returns are a rate applied to revenue, not a returns forecast.**
  There is no per-order return prediction, no seasonality, and no allowance for
  the fact that deeply discounted goods are often returned *more*. If anything,
  return-driven losses on deep promos are understated here.
- **Line-level allocation is pro-rata by gross line revenue.** Shipping,
  fees, and refunds are spread across lines by their share of the order, which
  is right on average and wrong for any single mixed basket — a heavy item in
  a light basket is under-charged for shipping.
- **Payment fees are assumed non-refundable**, which matches most processors
  but not all, and they are charged on the amount captured.
- **Breakeven depth is per unit at list price**, using that SKU's observed
  shipping economics from this data. It does not model bundles, BOGO
  mechanics, gift-with-purchase, tiered thresholds, or stacked codes. For a
  BOGO, express it as its equivalent percentage depth before running.
- Orders whose line items do not sum to the order total within 1% are flagged
  under DATA QUALITY; their per-SKU figures are distorted.
