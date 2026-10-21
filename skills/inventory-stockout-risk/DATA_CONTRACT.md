# Data contract — inventory-stockout-risk

Three input files, all local. No network access, no credentials in the script.

## File 1: sales history (`--sales`, required)

- Format: CSV, UTF-8, header row.
- Granularity: one row per SKU per day, **or** one row per order line. Rows
  are summed by `(sku, order_date)`, so either shape works.
- Coverage: at least `--base-window-days` (default 84) of days ending at
  `--as-of`. Days with no sales may be omitted; they are treated as zero, not
  as missing data.
- Exclude cancelled and test orders. Decide whether to include refunded units
  and be consistent; the script has no refund concept here.
- Upstream systems: Shopify "Sales by product" report or a line-item order
  export, a warehouse `order_lines` table, or an ERP invoice line export.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `sku` | string | — | Must match the inventory snapshot exactly, including case. |
| `order_date` | date | `YYYY-MM-DD` (datetime accepted, date part read) | A date after `--as-of` exits 2 naming the row. |
| `units_sold` | number | units | Decimals allowed for weight-priced goods. |

```csv
sku,order_date,units_sold
TW-DRYBAG-20,2026-09-14,11
TW-KAYAK-PDL,2026-09-14,6
```

## File 2: on-hand inventory snapshot (`--inventory`, required)

- Format: CSV, UTF-8, header row, **one row per SKU** (a duplicate SKU exits 2).
- Taken on `--as-of`. A snapshot from a different day silently shifts every
  days-of-cover figure.
- Upstream systems: Shopify inventory export joined to cost and price, or a
  3PL/ERP on-hand report (ShipBob, ShipHero, Cin7, NetSuite) joined to the
  price list.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `sku` | string | — | Required. Unique. |
| `on_hand_units` | number | units | Required. **Sellable** units in the warehouse. Negative values are allowed and are reported as `OVERSOLD`. |
| `unit_cost` | money | decimal | Required. **Landed** cost per unit: goods, inbound freight, duty. |
| `unit_price` | money | decimal | Required. Current selling price per unit, used for revenue at risk. |
| `product_name` | string | — | Optional. Defaults to the SKU. |
| `supplier_id` | string | — | Optional. Must match a row in the supplier file when one is supplied, or the run exits 2. Blank means "use the default lead time". |
| `on_order_units` | number | units | Optional. Units on an open purchase order not yet received. Default 0. |
| `inbound_eta` | date | `YYYY-MM-DD` | Optional. Expected arrival for `on_order_units`. Without it an inbound PO cannot offset a projected stockout. An ETA before `--as-of` is treated as landing on `--as-of`. The PO offsets a stockout by its quantity only (`on_order_units / velocity` extra days), never as full cover. |

```csv
sku,product_name,supplier_id,on_hand_units,on_order_units,inbound_eta,unit_cost,unit_price
TW-COOLER-25,Harborline Cooler 25QT,SUP-NORTHPINE,44,60,2026-09-26,71.00,189.00
```

## File 3: suppliers (`--suppliers`, optional)

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `supplier_id` | string | — | Required. Unique. |
| `lead_time_days` | integer | days | Required. Purchase order to receipt, including production and transit, not just transit. |
| `supplier_name` | string | — | Optional. Defaults to the id. |
| `moq_units` | integer | units | Optional. Minimum order quantity. Default 0. |
| `case_pack_units` | integer | units | Optional. Order multiple. Default 1. |

```csv
supplier_id,supplier_name,lead_time_days,moq_units,case_pack_units
SUP-COASTAL,Coastal Rigging Ltd,35,50,10
```

Omitting the file makes every SKU use `--default-lead-time-days` (30) with no
MOQ and a case pack of 1.

## Formulas and defaults

| quantity | definition |
| --- | --- |
| recent velocity | units in the trailing `--window-days` (28) / 28, units per day |
| baseline velocity | units in the trailing `--base-window-days` (84) / 84 |
| velocity | `recent_weight%` (70) of recent + the remainder of baseline |
| trend | recent / baseline − 1, as a percent |
| days of cover | `on_hand_units / velocity` |
| new-order arrival | `as_of + lead_time_days` (an order placed on the as-of date) |
| stockout date | first empty-shelf day in a day-level simulation: on hand depletes at velocity, the inbound PO adds `on_order_units` on its ETA (only if the ETA is before the new-order arrival). If the PO lands before on hand runs out, `as_of + ceil((on_hand + on_order) / velocity)`; otherwise `as_of + ceil(on_hand / velocity)` |
| short days | every empty-shelf day before the new-order arrival: the gap until a late PO lands, plus the gap after the PO's units run out (`eta + ceil((on_order + min(on_hand, 0)) / velocity)`) until the new order lands |
| earliest replenishment | the arrival that ends the last shortage (the new order, or a late PO); with no shortage, the PO ETA if it counts, else the new-order arrival |
| revenue at risk | `short_days × velocity × unit_price` |
| demand sigma | population standard deviation of daily units across the baseline window, zeros included |
| safety stock | `z(service_level) × sigma × sqrt(lead_time_days)`, z from the normal inverse CDF |
| order-up-to | `velocity × (lead_time_days + review_days) + safety_stock` |
| raw order | `order_up_to − (on_hand + on_order)` |
| recommended units | raw order rounded **up** to the case pack, then raised to MOQ and re-rounded |
| excess units | `on_hand − velocity × overstock_days`, floored at 0 |
| capital tied | `excess_units × unit_cost` |

## Money and units

Monetary values are parsed and accumulated with `decimal.Decimal`; unit counts
are also Decimal so fractional-unit SKUs work. No float arithmetic on
currency. The single float operation in the script is the normal inverse CDF
used to turn a service level into a z score, which is immediately converted
back to Decimal.
