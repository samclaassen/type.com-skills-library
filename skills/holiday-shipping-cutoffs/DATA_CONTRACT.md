# Data contract: holiday-shipping-cutoffs

Five input files. CSVs are UTF-8 (a BOM is fine) with a header row; column order doesn't matter and unknown columns are ignored. Dates are `YYYY-MM-DD` in the store's time zone. Any validation failure exits 2 with the file, line and reason.

## 1. Carriers (`--carriers`, CSV, required)

One or more rows per shipping service. A row carries **transit days by zone**, the carrier's **holiday ship-by date**, or both. Rows with the same `carrier` and `service` are merged, so you can keep transit and ship-by dates on separate rows with separate sources. A service can have one transit row and one ship-by date.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `carrier` | text | yes | `USPS`, `UPS`, `FedEx`, a regional carrier or 3PL. |
| `service` | text | yes | `Ground Advantage`, `Ground`, `2nd Day Air`, … The report names the service "carrier service". |
| `checkout_rate` | text | no | The checkout rate customers choose that ships with this service, e.g. `Standard` or `Express`. Services that share a rate (rate shopping) are grouped, and the rate's last safe date is the earliest of them. Default: the service name. |
| `zone_1` … `zone_8` | days | transit | Business days in transit from your warehouse to that carrier zone: `3`, or a range `3-5` when destinations in the zone differ. Blank is allowed only for zones with no orders in destinations.csv. All eight blank means the row has no transit table. |
| `zone_9` | days | no | Optional USPS zone 9 (US territories). Required only if destinations.csv sends orders to zone 9. |
| `peak_extra_days` | whole number | no | Days you add to every zone for December delays. Your allowance, not carrier data. Default 0. Set it from last December's actual delivery times if you have them. |
| `delivery_days` | days | with transit | Days the service picks up and delivers: `Mon-Fri`, `Mon-Sat`, or a list such as `Mon;Wed;Fri`. |
| `non_delivery_dates` | dates | no | Carrier holidays, separated by `;`: `2026-11-26;2026-12-25`. |
| `ship_by_date` | date | ship-by | The carrier's published last day to ship for holiday delivery. |
| `source` | text | yes | The URL the row's data came from, or `sample` for fictional data. Other text (for example `ShipStation rate calculator, Oct 2026`) is printed as given and marked "no URL". |

Where to get it: see `references/playbook.md` §1 (holiday ship-by pages for USPS, UPS and FedEx) and §2 (transit maps and calculators). A carrier date is described as carrier-published only through its `source`; sample rows print as fictional.

`ship_by_date` must be for delivery by your target date or later, and can't be after the target date itself (that exits 2). Carriers publish dates "for delivery by" a stated day, often Dec 24 or Dec 25. If the carrier doesn't deliver on Dec 25, its "by Dec 25" date also serves a Dec 24 target. If your target is earlier than the carrier's delivery date, move the date back by the number of the carrier's delivery days in between before entering it.

How a service is evaluated for an order that ships on day S:
- **Pickup** is S if the carrier works that day, otherwise its next delivery day (a non-delivery date counts as a day off).
- **Days left** = the carrier's delivery days after pickup, up to and including the target date, skipping non-delivery dates.
- A zone with transit `lo-hi` (each plus `peak_extra_days`) arrives in time with probability 1 when days left ≥ hi, 0 when days left < lo, otherwise (days left − lo + 1) ÷ (hi − lo + 1). The days in a range are treated as equally likely.
- **Coverage** = the sum over zones of zone share × that probability.
- With a `ship_by_date`, an order that ships after it is not safe, whatever its coverage. A service with only a ship-by date (no transit) is safe exactly when it ships by that date.

## 2. Warehouse (`--warehouse`, JSON, required)

```json
{
  "name": "Columbus, OH warehouse",
  "timezone": "America/New_York",
  "order_cutoff": "13:00",
  "after_cutoff_pct": 60,
  "handling_days": 0,
  "working_days": "Mon-Fri",
  "closed_dates": [{"date": "2026-11-26", "name": "Thanksgiving"}, "2026-12-25"],
  "daily_capacity": 250,
  "peak_capacity": [{"from": "2026-11-27", "to": "2026-12-04", "capacity": 300}]
}
```

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | text | no | Printed in the report. |
| `timezone` | IANA name | yes | The time zone of the cutoff. US zones print as ET, CT, MT, PT. Times stated in promises ("5 pm ET", "11:59 pm PT") are converted to this zone using US standard-time offsets; a stated time with no zone is read as warehouse time. |
| `order_cutoff` | `"HH:MM"` | yes | 24-hour time. Orders placed by then on a working day count for that day. |
| `after_cutoff_pct` | 0–100 | recommended | Share of a day's orders placed after the cutoff. Default 50, printed as assumed. From orders by hour of day over 4–8 recent weeks (references/playbook.md §4). |
| `handling_days` | 0–10 | no | Working days between an order's release and its ship date. 0 = ships the same day. Default 0. |
| `working_days` | days | no | Days the warehouse ships. Default `Mon-Fri`. |
| `closed_dates` | list | no | Dates the warehouse is closed: `"2026-12-25"` or `{"date": …, "name": …}`. |
| `daily_capacity` | whole number | yes | Orders picked, packed and handed to carriers per working day at normal staffing. |
| `peak_capacity` | list | no | `{"from", "to", "capacity"}`: replaces `daily_capacity` on working days in that range (temporary staff, overtime). |

How ship dates are worked out:
- A day's orders are split by `after_cutoff_pct` (after-cutoff orders rounded half up). Orders placed by the cutoff on a working day are released that day; after-cutoff orders and orders placed on a non-working day are released the next working day. An order can ship `handling_days` working days after release.
- Each working day ships up to its capacity, oldest first (by order date, then by-cutoff before after-cutoff). The rest carry over.
- **Business days to ship** = working days after the order date up to its ship date (0 = shipped the day it was placed). **Extra delay** = working days between the normal ship date and the modeled one.
- **Backlog** = orders ready to ship that didn't ship that day. A backlog period runs from the first working day that ends with a backlog to the first working day that ends with none: the day it **clears**.
- For the last safe date, each order date is judged by the last order placed before the cutoff that day (a hypothetical one if no orders are forecast), so a backlog that day counts against it.
- The **BFCM window** is Thanksgiving (the fourth Thursday of November) to Cyber Monday. The headline gives the most business days to ship for orders placed in it and when the backlog clears.

## 3. Destinations (`--destinations`, CSV, required)

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `zone` | 1–9 | yes | Carrier zone from your warehouse. Each zone once. Zones not listed have no orders. |
| `orders` | whole number | one of the two | Orders shipped to that zone. Best: last November–December. |
| `share_pct` | number | one of the two | Percent of orders. Must add up to 100 (±0.5). |

Where to get it: a ShipStation shipments export includes each shipment's zone; count shipments by zone. Or export Shopify orders with shipping ZIP codes and look up each ZIP's first three digits in the USPS Domestic Zone Chart for your origin ZIP. USPS and UPS zone charts differ slightly; use one. Check Alaska, Hawaii, territories, PO boxes and APO/FPO addresses separately: USPS notes slower service to Alaska, Hawaii and offshore destinations.

## 4. Demand (`--demand`, CSV, optional)

One row per day, consecutive (no gaps, no duplicates). Cover at least the week before Thanksgiving through Cyber Monday; through the target date is better, because a Monday after a busy December weekend can exceed capacity too.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `date` | date | yes | Order date. |
| `orders` | whole number | yes | Projected orders placed that day. |
| `proj_net_sales_base` | number | instead of `orders` | The BFCM Holiday Budget Planner's `--csv` daily plan. Pass `--aov` (net sales per order) and orders = net sales ÷ AOV, rounded. Use the planner's own AOV (88.66 in its sample); another value changes every order count. |

Sources: the Budget Planner's daily plan (it covers Nov 1 – Dec 6 by default; add later days by hand), last year's orders by day shifted so Black Friday lines up with Black Friday, times your expected growth, or a manual forecast. Without this file the backlog isn't modeled and every order is assumed to ship on schedule. Days after the file ends are treated as having no new orders (the report says so).

## 5. Promises (`--promises`, CSV, optional)

One row per place a delivery date or shipping time is shown to customers.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `placement` | text | yes | Where it appears: `Site banner`, `PDP: Merino Wool Throw`, `Checkout rate name`, `FAQ: Shipping`, `Email: Gift guide`, `SMS`, `Gorgias macro: Holiday shipping`. |
| `text` | text | yes | The exact text customers see. |
| `service` | text | no | The service or checkout rate it implies: `UPS Ground`, `Standard`, or several separated by `;`. Blank or `any` means no method is named, so the earliest last safe date applies. |
| `order_by` | date | no | The order-by date it states. Blank if none. |
| `ships_within` | whole number | no | For shipping-time claims: business days it says every order ships within (same day = 0, next business day = 1). |
| `send_date` | date | no | Email or SMS send date. Blank for always-on placements. |

Rules (the "last safe date" is the one at your threshold for the implied service or rate):
- **RISKY**: `order_by` is after the last safe date; or a message with no `order_by` and no `ships_within` is sent after it; or `order_by` equals the last safe date and the text states a time later than the cutoff (after converting its zone to the warehouse zone), when orders placed after the cutoff that day would miss.
- **WARNING**: no `order_by` and no `ships_within` (an unqualified holiday promise; the report adds a note on the FTC Mail, Internet, or Telephone Order Merchandise Rule, 16 CFR Part 435; not legal advice). Also when `order_by` equals the last safe date, the text gives no time (such as "1 pm"), and orders placed after the cutoff that day would miss; or it states a time whose zone can't be compared with the warehouse zone (a non-US warehouse zone). Also when `ships_within` is less than the business days some orders take: for always-on placements, any order date from `--as-of` to the target; for emails and SMS, orders placed in the 7 days from `send_date`.
- **INFO**: `order_by` is 3 or more days before the last safe date (customers are told to stop ordering early).
- **OK**: anything else. Times are read as `h am/pm`, `h:mm am/pm`, `noon`, `midnight` (end of the day) or 24-hour `hh:mm`, each with an optional zone (ET/EST/EDT/Eastern, CT, MT, PT, AKT, HT). A stated time at or before the cutoff is OK.

Coverage for each order date uses the latest ship date of that day's orders (the last ones out of a backlog), so it is conservative for orders placed early in a backlog.

## 6. Options and output

| Option | Default | Notes |
| --- | --- | --- |
| `--target` | Dec 24 of the `--as-of` year | Deliver-by date. Must be after `--as-of`. |
| `--threshold` | 95 | Percent of customers (weighted by zone mix) who must get the order by the target. The report also shows 80% and 99%. |
| `--as-of` | today | Report date: past dates print as "passed", and fixes are dated from it. |
| `--account` | `Store` | Name in the report title. |
| `--aov` | — | Net sales per order, to read a Budget Planner plan as demand. |
| `--json` | — | Also write the structured result: `services` (last safe dates by threshold, basis, sources), `checkout_rates`, `backlog` (BFCM summary, periods, ship days), `promises`, `issues`, `calendar`, `fixes`, `coverage_by_order_date` (last 21 days), `verdict`, `all_cutoffs_passed`. `backlog.bfcm_orders_forecast` is false (and `bfcm_ship_within_business_days` null) when no orders are forecast in the BFCM window. `zone_mix` has keys `1`–`8`, plus `9` when orders go there. |

**Verdict:** `NOT READY` with any RISKY item, `FIX BEFORE PEAK` with warnings only, `READY` otherwise. A backlog that adds 3 or more business days to some orders is also a WARNING.

**Messaging calendar:** a shipping-time note for each backlog period that makes some orders take longer than an ordinary after-cutoff order (covering the BFCM window plus the other delayed days in that period); cutoff messaging 7 days before the first rate's last safe date; a morning reminder and a switch at the cutoff time on each rate's last safe date ("expedited only" when one rate is left, then e-gift cards after the last); a notice before each warehouse closure after the last cutoff; and taking the holiday messaging down the day after the target.
