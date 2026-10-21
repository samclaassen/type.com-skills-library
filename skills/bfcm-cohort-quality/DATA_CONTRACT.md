# Data contract: bfcm-cohort-quality

Two required files and one optional file, all local. CSVs are UTF-8 (a BOM is fine) with a header row; column order doesn't matter and unknown columns are ignored. Header names are matched without regard to case, with spaces and hyphens read as `_` (so `Created at` = `created_at`). Any validation failure exits 2 with the file, line, column and reason.

**Numbers** are parsed strictly. Allowed: `1234.56`, `1,234.56`, a leading or trailing currency symbol (`$1,234.56`, `€1,234.56`). Commas are accepted only as thousands separators (`^\d{1,3}(,\d{3})*(\.\d+)?$`). Rejected with exit 2: comma decimals (`75,00`, `28.320,00`), `nan`, `inf`, exponents (`1e9`) and stray text. A numeric column where more than half the values look European-formatted (`1.234,56`) fails with a specific message: export with `.` as the decimal separator. Negative amounts are rejected everywhere except `refunded_amount`, where `-50.00` or `(50.00)` (an accounting sign) is read as a refund of 50.00.

**Net sales** = gross sales − discounts, before returns/refunds, excluding tax and shipping. This is the definition used across the BFCM collection (Budget Planner, Live Pulse, Tracking Preflight).

## 1. Orders (`--orders`, CSV, required)

One row per order, **every order of every customer you want analysed, as far back as the store's first order**. The script finds each customer's first-ever order itself, so a customer whose earlier orders are missing would wrongly count as new. A full export holds everyone; the script keeps the customers whose first order falls in a cohort window and ignores the rest.

| Column | Required | Also accepted as | Notes |
| --- | --- | --- | --- |
| `order_id` | yes | `name`, `order_name`, `order_number` | Unique per order, e.g. `#18241`. |
| `customer_id` | yes | `customer`, `email`, `customer_email` | **Stable across a customer's orders.** Shopify customer ID, or the email if guest checkouts create a new ID per order. Never printed. Rows with a blank value are dropped and counted. |
| `created_at` | yes | `order_date`, `processed_at`, `date` | `YYYY-MM-DD`, or an ISO 8601 / Shopify timestamp: `2025-11-28T09:14:00-05:00`, `2025-11-28 09:14:00 -0500`, `2025-11-28T14:14:00Z`. See time zones below. |
| `net_sales` | yes | `net_revenue`, `subtotal`, `subtotal_price` | Product sales **after discounts, before refunds**, excluding tax and shipping charged (gross sales − discounts). The same definition as the BFCM Holiday Budget Planner's `net_sales`. If your figure is already net of refunds, don't also supply `refunded_amount`. |
| `discount_amount` | yes | `discount`, `discounts`, `total_discounts` | Product and order discounts on the order (blank = 0). Leave free-shipping discounts out. Compare-at markdowns (a clearance price) are not discounts here. |
| `discount_codes` | no | `discount_code` | Codes used, separated by `;`, `,` or `|`. Automatic discounts have no code. Used to name the codes behind deep first-order discounts. |
| `refunded_amount` | no | `refund_amount`, `total_refunded` | Amount refunded on the order, whenever it was refunded (blank = 0). `-50.00` and `(50.00)` are read as 50.00. A refund larger than `net_sales` (tax or shipping refunded too) is capped at `net_sales` and counted. **Without this column** every order is assumed to lose `return_rate_pct` of its net sales. |
| `cogs` | no | | Landed product cost of the whole order. Blank or absent: estimated as `(1 − gross_margin_pct) × (net_sales + discount_amount)`. |
| `utm_source` | no | | The order's `utm_source`, last click. Blank: read from `landing_site`. |
| `landing_site` | no | `landing_page`, `landing_url` | Landing URL with its query string; `utm_source` is taken from it when the column above is blank. |
| `products` | no | `product_handles`, `handles`, `lineitem_name` | Products in the order, separated by `;` (handles or titles; a title with a variant suffix counts as its own product). Without it the entry-product cut is skipped. |
| `cancelled_at` | no | | Any value drops the order (cancelled orders are not sales). |

**Shopify admin export (one row per line item).** A row whose `order_id` was already seen and whose `net_sales` is blank is an extra line item: its product is added to the order and nothing else is read. Any other repeated `order_id` is an error.

**Time zones.** Cohort windows and horizons are store-local calendar days. A timestamp without an offset, or any timestamp when `--timezone` is not given, is read as the date written; Shopify's admin export and REST API already write store-local times. GraphQL returns UTC (`…Z`): pass `--timezone` with the store's zone and each timestamp is converted before its date is taken. Supported: `UTC`, fixed offsets (`-05:00`), US zones with daylight saving (`America/New_York`, `America/Chicago`, `America/Denver`, `America/Los_Angeles`, `America/Anchorage`, `America/Toronto`, `America/Vancouver`, `America/Detroit`, `America/Winnipeg`, `America/Edmonton`, and `US/Eastern`-style names), `America/Phoenix`, `Pacific/Honolulu`, and European zones (`Europe/London`, `Dublin`, `Lisbon`, `Paris`, `Berlin`, `Amsterdam`, `Brussels`, `Madrid`, `Rome`, `Vienna`, `Stockholm`, `Copenhagen`, `Oslo`, `Zurich`, `Prague`, `Warsaw`, `Helsinki`, `Athens`). Rules: US 2007+ (second Sunday of March to first Sunday of November, 2 am local), EU (last Sunday of March to last Sunday of October, 01:00 UTC). If UTC timestamps are read without `--timezone`, the report says how many.

**Validation (exit 2):** a required column missing; a blank `order_id`, `created_at` or `net_sales`; a date or number that doesn't parse (including comma decimals, `nan`, `inf` and European-formatted columns); a negative amount (except a signed refund); a duplicate `order_id`; no usable orders; no customer whose first order falls in the BFCM window.

### Where to get it

- **Shopify Admin GraphQL API** (in type.com, through the Shopify integration; read-only `read_orders`, plus `read_all_orders` for orders older than 60 days):

  ```graphql
  query Orders($cursor: String) {
    orders(first: 100, after: $cursor, sortKey: CREATED_AT, query: "created_at:>=2021-01-01") {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        createdAt
        cancelledAt
        customer { id }
        subtotalPriceSet { shopMoney { amount } }
        totalDiscountsSet { shopMoney { amount } }
        discountCodes
        totalRefundedSet { shopMoney { amount } }
        customerJourneySummary { lastVisit { landingPage utmParameters { source } } }
        lineItems(first: 50) { nodes { product { handle } } }
      }
    }
  }
  ```

  Write `name` → `order_id`, `customer.id` → `customer_id`, `createdAt` → `created_at` (UTC: pass `--timezone`), `subtotalPriceSet` → `net_sales`, `totalDiscountsSet` → `discount_amount`, `discountCodes` joined with `;` → `discount_codes`, `totalRefundedSet` → `refunded_amount`, `lastVisit.utmParameters.source` → `utm_source`, `lastVisit.landingPage` → `landing_site`, the product handles joined with `;` → `products`, `cancelledAt` → `cancelled_at`. If the store's prices include tax, `subtotalPriceSet` does too; take the tax out first.
- **Shopify admin export** (Orders → Export → All orders, CSV): loads unchanged through the aliases above (`Name`, `Email`, `Created at`, `Subtotal`, `Discount Code`, `Discount Amount`, `Refunded Amount`, `Lineitem name`, `Cancelled at`). Before relying on `Subtotal`, check one discounted order: it should equal the item prices minus the discount. The export has no UTM data, so the source cut is skipped. **Caveat:** the export's `Discount Amount` includes free-shipping discounts, which this contract leaves out, so on orders with a shipping discount the depth is slightly overstated (and COGS, estimated on net + discount, slightly too high). Where that matters, subtract shipping discounts first.
- **Cohort LTV Analyzer's order export** loads through `order_date`, `net_revenue`, `discount`, `refund_amount`. Check how that export defines `net_revenue` (not verified here): if it is before refunds, load it unchanged; if it is already net of refunds, remove or rename the `refund_amount` column and set `return_rate_pct` to 0, otherwise refunds are subtracted twice.
- **A pre-filtered export** (only the customers whose first order falls in a cohort, as the sample is, to keep the file small) is fine. Pass `--history-since` with the date the store's order history begins, because the file's own earliest order no longer shows it.

## 2. Unit economics (`--economics`, JSON, required)

The same file as the BFCM Holiday Budget Planner. Only these keys are read; every other key (`target`, `plan_window`, `sale_window`, `channel_shares`, …) is ignored, so the planner's file works unchanged.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `gross_margin_pct` | number 0–100 | yes | (Price − landed COGS) ÷ price, at **full price**, as a percent (`64`, not `0.64`). Used for orders without `cogs`. A value between 0 and 1 exits 2 ("did you mean 64?"). |
| `shipping_fulfillment_per_order` | number ≥ 0 | yes | Shipping label + pick/pack + packaging per order, net of shipping charged. |
| `payment_fee_pct` | number 0–100 | yes | Processor percentage fee, as a percent (`2.9`). Applied to net sales only: fees on tax and shipping charged aren't in the export, so CM is slightly overstated. A value between 0 and 1 is flagged in the caveats. |
| `payment_fee_fixed` | number ≥ 0 | no | Fixed fee per order. Default 0. |
| `return_rate_pct` | number 0–100 | yes | Used only when the orders file has no `refunded_amount` column; otherwise printed next to the actual refund rate. A value between 0 and 1 is flagged in the caveats. |
| `discount_pct` | number 0–99 | no | This year's planned BFCM depth, as a percent. Its band is the reference band for the deep-discount comparison. Without it, the band most BFCM first orders fell in is used. A value between 0 and 1 exits 2. |
| `brand`, `currency` | string | no | Report title and currency. `currency` sets the symbol on every amount (`USD` $, `EUR` €, `GBP` £, `CAD` CA$, `AUD` A$, …; other codes print as `SEK 12.00`). |

## 3. Daily ad spend (`--spend`, CSV, optional)

| Column | Notes |
| --- | --- |
| `date` | `YYYY-MM-DD`, store time zone. No duplicates. |
| `*_spend` | One column per paid channel: `meta_spend`, `google_spend`, `tiktok_spend`, `other_spend`, … (blank = 0). All are summed. A single `total_spend` column also works, but not together with per-channel columns. |

Other columns are ignored, so the Budget Planner's `history.csv` works as is. A cohort gets a blended cost only if the file has **every day** of its window; otherwise the report names the missing days instead of guessing. A window whose spend is 0 on every day is treated as missing spend (no cost, no payback), not as free customers.

## 4. Settings

| Flag | Default | Notes |
| --- | --- | --- |
| `--as-of` | last order date in the file | The analysis date. Orders after it are ignored. |
| `--year` | the latest year whose BFCM window ended by `--as-of` | The BFCM being analysed. With `--bfcm-window` and no `--year`, the window's year. |
| `--bfcm-window START:END` | Monday of Thanksgiving week → Tuesday after Cyber Monday | Thanksgiving = fourth Thursday of November. 2025: Nov 24–Dec 2. 2026: Nov 23–Dec 1. |
| `--compare "LABEL=START:END"` | `October <year>` (Oct 1–31) and `Jan–Feb <year+1>` (Jan 1–last day of Feb) | Repeatable; replaces the defaults. Labels must be unique and windows must not overlap (a customer can only be new once). |
| `--horizons` | `30,60,90,180,365` | Days since the first order, 1 to 8 values. |
| `--min-cell` | 30 | Cuts with fewer customers show counts but no rates. |
| `--timezone` | none | See time zones above. |
| `--history-since` | the earliest order in the file | Date from which the file holds every order of its customers. |
| `--account` | `brand` from economics.json | Report title. |
| `--json PATH` | none | Also writes the analysis as JSON. |

A comparison window that starts after `--as-of` is left out and named; one still open on `--as-of` is shown as "still acquiring", with every horizon not mature. A comparison cohort with no customers in the file is left out and named.

## 5. Definitions and formulas

- **New customer of a cohort**: a customer whose first order (earliest `created_at`, ties by file order) falls inside the window, on or before `--as-of`.
- **Day k** of an order = its store-local date − the customer's first-order date. **Repeat order** = an order on day 1 or later. Orders on day 0 after the first count toward value but not as a repeat.
- **Horizon H is mature** for a cohort when `--as-of` ≥ window end + H days, so every member has had H full days. Otherwise every H-day figure prints "not mature" with that date (right-censoring).
- **Repeat rate at H** = customers with a repeat order on day 1…H ÷ cohort customers.
- **CM per order** (contribution before ads), with `d` = discount_amount, `r` = refunded amount (capped at net sales), `keep` = (net − r) ÷ net:
  `CM = (net − r) − COGS × keep − (payment_fee_pct × net + payment_fee_fixed) − shipping_fulfillment_per_order`,
  where COGS is the `cogs` column or `(1 − gross_margin) × (net + d)`. Refunded goods are restocked; fees and shipping are not recovered (the Budget Planner's assumptions). Without `refunded_amount`, `r = net × return_rate` and `keep = 1 − return_rate`.
- **CM per customer at H** = sum of CM on day 0…H for every member ÷ cohort customers. In the discount-depth cut it is split into **first-order CM** (day 0, including other same-day orders) and **CM from repeat orders** (day 1…H), with a 95% normal-approximation interval for the repeat-order mean.
- **Deep-discount finding**: compares the deep group (bands above the reference band) with the reference band on behaviour after the first order only: the repeat rate (95% Wilson ranges) and CM from repeat orders per customer (95% intervals). "Repeated less" / "worth less after their first order" needs non-overlapping ranges in that direction; otherwise the finding says there is no clear difference. Total CM is not used, because a deeper discount lowers first-order CM by construction. The wording is non-causal; confirm with a holdout before setting a cap. JSON: `cuts.deep_vs_ref` (`repeat`, `later_cm`: −1/0/+1; `verdict`: `weaker`/`stronger`/`unclear`).
- **First-order AOV** = mean `net_sales` of first orders. **Average depth** = Σ discount ÷ Σ (net + discount) over first orders. **Refund rate** = Σ refunded ÷ Σ net sales of first orders.
- **Discount depth band** of an order: depth = discount ÷ (net + discount), as a whole percent rounded half up (32.5% → 33%). Bands: `0%` (no discount), `1–15%` (any discount up to 15, including under 1%), `16–25%`, `26–35%`, `>35%`. The **reference band** holds `discount_pct`; "deep" means every band above it.
- **Discount dependence** = repeat orders (day 1…H) with a discount ÷ all repeat orders, at the comparison horizon.
- **Days to second order**: day of the first repeat order, for customers who repeated within the comparison horizon; median and quartiles are nearest-rank (P = value at rank ⌈p × n⌉).
- **Blended new-customer cost** = total spend over every day of the window ÷ new customers. Not attribution.
- **Payback day** = first day d (0 … as-of − window end) on which the average member's cumulative CM (orders on day 0…d) reaches the blended cost. Not reached: "not by day N", with CM at day N as a share of cost.
- **Comparison horizon** = the longest horizon mature for the BFCM cohort and at least one comparison cohort (if none, the longest mature for BFCM). The headline, discount dependence, days to second order and the "Seasons differ" counts use it. The cuts use the longest horizon mature for the BFCM cohort.
- **Source** = the first order's `utm_source`, lower-cased and merged by alias (`fb`, `facebook.com`, `l.facebook.com`, `ig`, `instagram`, `meta`, … → Meta; `adwords`, `googleads`, `google.com` → Google; `tt`, `tiktokads` → TikTok; `pin` → Pinterest; `snap` → Snapchat; `microsoft` → Microsoft), the same table as BFCM Tracking & Attribution Preflight. Blank = "No UTM (direct/unknown)". Other values are kept as written, lower-cased.
- **Entry product**: each product in the first order (a two-product first order counts under both). **Next product** = the product most often in the repeaters' second orders (ties alphabetical), as a share of repeaters.
- **95% range** = Wilson score interval for the repeat rate. **Clearly higher/lower** = the range lies entirely above/below the cohort-wide rate; otherwise "within noise".
- **Recommendations**: the discount-depth and entry-product ones appear only when the cells they compare clear `--min-cell`; the discount-depth one proposes a holdout-tested cap only on a clearly lower repeat rate or repeat-order CM (as above); the existing-customer one cites returning customers' BFCM orders only when there are at least 50, with the discounted share and its 95% range; the post-purchase timing one needs at least 10 repeaters within the comparison horizon; the existing-customer and budget ones need a mature comparison horizon.

## 6. Output

- **stdout**: the Markdown report: headline, numbered findings (detected), 1. cohorts side by side, 2–4. the BFCM cuts by discount depth, source and entry product, 5. recommendations (recommended), 6. caveats and data checks.
- **`--json`**: the same analysis: `cohorts` (per cohort: window, status, counts, first-order figures, `cost`, `horizons` keyed by day with `mature`, `matures_on`, `repeat_rate`, `repeaters`, `cm_per_customer`, `net_sales_per_customer`, plus `payback_day`, `max_day`, `cm_at_max_day` and, at the comparison horizon, `at`), `cuts` (`discount`, `deep`, `deep_codes`, `source`, `product`), `recommendations`, `data` (row counts and checks), `compare_horizon`, `cut_horizon`. Dates are ISO strings; no customer identifiers are written.
