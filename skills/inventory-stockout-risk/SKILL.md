---
name: inventory-stockout-risk
description: Compute velocity-based days of cover per SKU from a sales history and an on-hand snapshot, rank imminent stockouts by the revenue they put at risk, recommend reorder quantities against supplier lead times, MOQ and case pack, and surface the working capital sitting in overstock and dead stock.
---

# Inventory Stockout Risk

Turns three exports into one purchasing decision: what runs out before it can
be replaced, how much revenue that costs, how much to order from whom, and
what is already over-bought.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `inventory-stockout-risk`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Weekly or fortnightly purchasing review, ahead of cutting POs.
- Before a demand spike (a sale, a launch, a seasonal peak) to find the SKUs
  that cannot survive it.
- When the storefront is oversetting expectations: negative on-hand, "sold
  out" bestsellers, back-in-stock emails piling up.
- Cash planning: how much working capital is frozen in slow stock that could
  fund the reorder of fast stock.
- After a supplier lead time changes, to re-cut every reorder point at once.

## Gathering the input

Three local files; the script never touches the network.

**1. Sales history** (`--sales`). One row per SKU per day, or per SKU per
order line — the script sums by SKU and date either way.
- **Shopify**: the Sales by product report, or an orders export exploded to
  line-item level. Exclude cancelled and test orders.
- **A warehouse or ERP**: a shipped or invoiced line-item table.
- Use at least the `--base-window-days` span (default 84 days). Shorter
  histories still run but the baseline velocity and the demand sigma behind
  safety stock get noisy; say so if you only have a few weeks.

**2. On-hand inventory snapshot** (`--inventory`). One row per SKU, taken the
same day as `--as-of`.
- **Shopify**: Inventory export (available quantity), joined to cost per item
  and price.
- **A 3PL or ERP** (ShipBob, ShipHero, Cin7, NetSuite): on-hand by SKU, plus
  open purchase orders and their ETAs.
- `unit_cost` must be *landed* cost, not invoice cost, or the working-capital
  figure understates.

**3. Supplier table** (`--suppliers`, optional but it changes the answers).
Lead time, MOQ, and case pack per supplier. This normally lives in a
purchasing spreadsheet, not in any system — ask for it. Without it every SKU
falls back to `--default-lead-time-days`.

## Running it

```bash
python3 scripts/stockout_risk.py \
  --sales examples/sales_history.csv \
  --inventory examples/inventory_snapshot.csv \
  --suppliers examples/suppliers.csv \
  --as-of 2026-09-15
```

Tuning that matters:

- `--as-of YYYY-MM-DD` (required) is the snapshot date. Every window ends
  here, and a sales row dated after it is an error, not a silent drop.
- `--window-days` (28) and `--base-window-days` (84) with
  `--recent-weight` (70) set the velocity blend. A seasonal brand should raise
  the recent weight; a steady replenishment brand should lower it.
- `--service-level` (95) drives safety stock through the normal inverse CDF.
  90 for low-margin commodity SKUs, 97–99 for hero SKUs you cannot be out of.
- `--review-days` (14) is how often you actually cut POs. Ordering monthly
  with a 14-day review assumption under-orders.
- `--overstock-days` (120) and `--dead-stock-value` (500) classify slow stock.
- `--json` for machine-readable output.

Bad input exits 2 naming the file, row, and column.

## Reading and presenting the output

- **Stockout risk table**, ranked by revenue at risk. `short` is the number of
  days the shelf is empty before a new order placed on the as-of date can
  land. An existing inbound PO counts by quantity and ETA: it extends cover
  by `on_order / velocity` days (or refills an already-empty shelf on its
  ETA), so a small PO that lands early can still leave a gap later. `stockout`
  is the first empty day after that simulation, which is why it can sit later
  than `cover` (on hand only) implies. `rev at risk` is short days times
  velocity times selling price. `trend` compares recent velocity
  with the baseline window: a `+40%` SKU with 7 days of cover is the most
  urgent line in any report.
- **Recommended replenishment**, grouped by supplier with quantities rounded
  up to case pack and raised to MOQ, and a purchase order value per supplier.
  This is the section a buyer acts on directly.
- **Overstock and dead stock**, with excess units and capital tied up at
  landed cost.
- **Summary** with status counts and the two headline numbers: revenue at risk
  and capital frozen.

When reporting to a human:

1. Lead with the two headline numbers and the top three at-risk SKUs by name.
2. Separate *cannot be fixed* from *can be fixed*. A SKU with 7 days of cover
   and a 60-day lead time will stock out no matter what is ordered today —
   the decision there is air freight, a partial PO, a substitution, or pulling
   paid spend off that product until it is back. Say that explicitly rather
   than just printing an order quantity.
3. Point out where the PO value exceeds the capital sitting in overstock:
   that is the liquidation conversation.
4. Flag any status of `OVERSOLD` as an operational incident, not a purchasing
   one — the storefront is selling units that do not exist.

## Limits

- **Velocity is history, not a forecast.** No seasonality model, no promotion
  calendar, no launch curve, no trend extrapolation beyond the recent-window
  weighting. A SKU about to appear in a sale will run out faster than this
  says. Override it for known events.
- **Safety stock assumes normally distributed daily demand and a fixed lead
  time.** Real DTC demand is lumpy and lead times vary; the sigma is measured
  from the baseline window only. Treat the recommended quantity as a starting
  point a buyer adjusts, not a number to send to a supplier unreviewed.
- **Past stockouts bias velocity downward.** If a SKU was out of stock for
  three weeks inside the window, its measured velocity understates true
  demand, so it will be under-ordered. The script cannot see historical
  availability. Check the at-risk list against known stockouts.
- **No multi-location or channel splits.** One pooled on-hand figure per SKU.
  It does not model bin, warehouse, retail, marketplace, or reserved-for-
  order inventory.
- **No bundles or kits.** A bundle that consumes component SKUs is only
  counted as whatever the sales history says it is.
- **No supplier price breaks, container fill, or freight optimisation.**
  Quantities respect MOQ and case pack only.
- SKUs in the sales history but absent from the inventory snapshot are
  skipped and listed under DATA QUALITY rather than silently ignored.
