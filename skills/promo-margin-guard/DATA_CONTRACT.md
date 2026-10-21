# Data contract — promo-margin-guard

Three input files, all local. No network access, no credentials in the script.
No date argument and no clock read: the analysis is defined entirely by the
data and the assumption flags.

## File 1: promotion orders (`--orders`, required)

- Format: CSV, UTF-8, header row, **one row per order** (a duplicate
  `order_id` exits 2).
- Scope: every order you want attributed to a promotion, plus optionally
  orders from the same window with an empty `promo_code` as a baseline.
- Exclude cancelled and test orders. Single currency.
- Upstream: Shopify Orders export for the promotion window, or a warehouse
  `orders` table joined to the discount code.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `order_id` | string | — | Required, unique, and must match the line items file. |
| `order_date` | string | `YYYY-MM-DD` | Required, carried for reference only; no date maths is performed. |
| `promo_code` | string | — | Required column. An empty **value** buckets the order under `(no code)`, which is the baseline comparison. |
| `gross_revenue` | money | decimal | Required. Product revenue **before** the discount, **excluding** tax and shipping. |
| `discount_amount` | money | decimal, positive | Required. Total order discount. Must not exceed `gross_revenue`. |
| `shipping_charged` | money | decimal | Optional, default 0. What the customer paid for shipping. |
| `shipping_cost` | money | decimal | Optional, default 0. Carrier cost of the outbound parcel. |
| `payment_fee` | money | decimal | Optional. Actual processor fee. Overrides `--payment-fee-pct` / `--payment-fee-fixed` for that order. |
| `refund_amount` | money | decimal, positive | Optional, default 0. Product revenue already refunded on this order. |

```csv
order_id,order_date,promo_code,gross_revenue,discount_amount,shipping_charged,shipping_cost,payment_fee,refund_amount
WA-5101,2026-08-21,FLASH40,160.00,64.00,0.00,8.40,3.08,0.00
WA-5102,2026-08-21,,84.00,0.00,0.00,7.20,2.74,0.00
```

## File 2: order line items (`--lines`, required)

- Format: CSV, UTF-8, header row, one row per order line. Multiple rows per
  order are expected.
- Every `order_id` must exist in the orders file, and every order must have at
  least one line, or the run exits 2 naming the row.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `order_id` | string | — | Required. Must match the orders file. |
| `sku` | string | — | Required. Must exist in the products file. |
| `units` | number | units | Required, greater than zero. |
| `gross_line_revenue` | money | decimal | Required. Line revenue at **full price**, before any discount. |
| `line_discount` | money | decimal | Optional. Line-level discount. When absent, the order discount is allocated across lines by their share of gross line revenue. |

```csv
order_id,sku,units,gross_line_revenue
WA-5101,WA-BOOT-CHL,1,198.00
WA-5101,WA-SOCK-3PK,1,24.00
```

If an order's line items do not sum to its `gross_revenue` within 1%, the run
still completes and the order is listed under DATA QUALITY. Shipping, fees,
and refunds are always allocated to lines by their share of the **line** total,
so a large gap distorts per-SKU figures.

## File 3: products (`--products`, required)

- Format: CSV, UTF-8, header row, one row per SKU (a duplicate exits 2).
- Upstream: ERP or 3PL cost file for `unit_cost`, the price list for
  `list_price`, and the returns platform (Loop, Returnly, AfterShip) or a
  12-month returns report for `return_rate_pct`.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `sku` | string | — | Required, unique. |
| `unit_cost` | money | decimal | Required. **Landed** cost: goods, inbound freight, duty. Not invoice cost. |
| `list_price` | money | decimal, > 0 | Required. Undiscounted selling price, the base the breakeven depth is expressed against. |
| `product_name` | string | — | Optional. Defaults to the SKU. |
| `return_rate_pct` | number | percent, 0 to <100 | Optional. Expected share of units returned. Falls back to `--return-rate-pct` when absent. |

```csv
sku,product_name,unit_cost,list_price,return_rate_pct
WA-BOOT-CHL,Chelsea Boot,104.00,198.00,45
WA-SOCK-3PK,Everyday Sock 3 Pack,8.40,24.00,4
```

Return-rate sanity ranges for DTC: accessories and home goods 4–10%, tops and
knitwear 12–20%, footwear, denim, and fitted dresses 25–45%.

## Margin definition

Per line, after allocating order-level amounts by the line's share of order
gross line revenue:

```
net_revenue      = gross_line_revenue - discount
refunded_share   = refund_allocated / net_revenue        (capped at 1)
expected_share   = return_rate * (1 - refunded_share)

booked_margin    = net_revenue - refund_allocated + shipping_charged
                 - shipping_cost - fulfillment - payment_fee
                 - cogs + cogs * refunded_share * restock_recovery
                 - units * refunded_share * return_shipping_cost

expected_margin  = booked_margin
                 - net_revenue * expected_share                      (refunds still to come)
                 - units * expected_share * return_shipping_cost     (their handling)
                 + cogs * expected_share * restock_recovery          (stock they will recover)
```

Booked margin contains only returns that have already happened (the refunded
share); every term driven by `expected_share` belongs to expected margin
alone. The text report prints this as a per-promo bridge, booked → minus
refunds still to come → minus their handling → plus recovered stock →
expected. Each running subtotal is rounded to cents and each step is the
difference of two rounded subtotals, so every row adds up exactly and both
ends match the booked and expected margins printed elsewhere; a step can
differ by 0.01 from rounding that component on its own (the JSON
`expectedFuture*` fields are rounded individually).

Payment fees are charged on the amount captured and are **not** returned when
an order is refunded, which matches how most processors work.

## Breakeven discount depth

Per SKU, `d` is the deepest discount off `list_price` whose unit contribution
still clears `--min-unit-margin`:

```
revenue_factor = (1 - return_rate) - payment_fee_pct
cost_side      = min_unit_margin
               + unit_cost * (1 - return_rate * restock_recovery)
               + observed_shipping_net_per_unit
               + return_rate * return_shipping_cost

d = 1 - cost_side / (list_price * revenue_factor)
```

`observed_shipping_net_per_unit` is that SKU's own
`(allocated shipping cost + fulfillment - shipping charged) / units` from this
data set, so a SKU that habitually ships free is charged for it. When
`revenue_factor <= 0` — a return rate so high that fees and returns consume
the revenue — no discount depth is profitable and the SKU is reported as
`none`.

## Defaults

| input | default | flag |
| --- | --- | --- |
| payment fee | 2.9% of captured + 0.30 per order | `--payment-fee-pct`, `--payment-fee-fixed` |
| fulfillment | 0.00 per order | `--fulfillment-cost` |
| return rate when no column | 0% | `--return-rate-pct` |
| return handling per returned unit | 0.00 | `--return-shipping-cost` |
| unit cost recovered on a return | 0% | `--restock-recovery-pct` |
| planned sitewide depth | 20% | `--planned-discount-pct` |
| required unit margin | 0.00 | `--min-unit-margin` |

## Money handling

All monetary values are parsed and accumulated with `decimal.Decimal` and
quantized to cents only at print time. No float arithmetic on currency.
Parentheses (`(12.50)`) and leading `$` are tolerated on input.
