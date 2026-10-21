# Data contract: bfcm-offer-config-qa

Four inputs: the offer plan (JSON), the live discounts (JSON), the catalog (CSV) and, optionally, price history (CSV). UTF-8 (a BOM is fine). In CSVs the header row comes first, column order doesn't matter, unknown columns are ignored, and numbers may contain `$` or thousands commas. Any validation failure exits 2 with the file, line (or entry) and reason.

Times: the plan is written in **store time** (`2026-11-24 00:00`); the discounts file keeps Shopify's times **with their UTC offset** (`2026-11-24T05:00:00Z`). The analyzer converts everything to the store's time zone.

## 1. Offer plan (`--plan`, JSON, required)

What the team intends to run. Write it with whoever owns the promotion calendar.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `store` | string | no | Report title. |
| `timezone` | string | yes | The store time zone from Shopify (Settings → General → Store defaults → Time zone), as an IANA name such as `America/New_York`. Built-in rules cover US, Canadian, UK, EU, Australian, NZ and common Asian zones; anything else can be given as a POSIX TZ rule, e.g. `EST5EDT,M3.2.0,M11.1.0`. `timezone_label` (optional) overrides the short label (`ET`). |
| `currency` | string | no | Informational; amounts print with `$`. |
| `launch` | `YYYY-MM-DD` | no | First sale day. Default: the earliest `phase` start. Fix-by and freeze dates count back from it. |
| `max_item_discount_pct` | number | yes | The deepest total discount any single item may get from discounts (not counting compare-at markdowns), e.g. `25`. |
| `margin_floor_pct` | number | no | Gross-margin floor on the discounted price, e.g. `15`. Default 0 (only below-cost is flagged). `--margin-floor` overrides it. |
| `shopify_plus` | bool | no | `true` if the store is on Shopify Plus, so product discounts tagged to combine on the same line can stack on one item. Default `false`. |
| `economics` | object | no | For the free-shipping estimate: `shipping_cost_per_order` (what one order's shipping costs you), `last_bfcm_orders`, `last_bfcm_orders_under_free_shipping_min` (orders below the planned minimum last BFCM), `orders_per_day_now`. |
| `offers` | array | yes | One object per intended discount (below). |
| `must_not_be_active` | array of strings | no | Codes or automatic-discount titles that must be off (old codes, last year's sale). |

**Each offer:**

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | no | Label in the report. |
| `method` | `code` / `automatic` | yes | |
| `code` | string | code offers | Matched case-insensitively against the discount's codes. A code offer may also give `title` (matched if the code isn't among the codes in the export, e.g. bulk-code discounts). |
| `title` | string | automatic offers | The automatic discount's title in Shopify, matched case-insensitively. |
| `type` | `percentage` / `fixed_amount` / `free_shipping` / `bxgy` | yes | |
| `value` | number | percentage, fixed | Percent (`25`) or amount (`10`). |
| `class` | `product` / `order` | percentage, fixed | Shopify's *Amount off products* = `product`, *Amount off order* = `order`. It decides how the offer stacks. `free_shipping` is always `shipping`; `bxgy` is `product`. |
| `bxgy` | object | bxgy | `buy_quantity`, `get_quantity`, `get_percentage` (100 = free). |
| `applies_on_each_item` | bool | no | For a `fixed_amount` product offer: `true` (default) takes the amount off each eligible item; `false` splits it across the eligible items (Shopify's *Only apply discount once per order*). Compared with the live `appliesOnEachItem`. |
| `eligible` | `"all"`, `{"collections": [...]}`, `{"products": [handles], "skus": [...]}` | no | What a product offer should reach. Default `"all"`. |
| `exclude_collections`, `exclude_products`, `exclude_skus` | arrays | no | Taken out of `eligible`. Collection names match the products file (case and punctuation ignored). |
| `min_subtotal` | number | no | Minimum purchase amount. |
| `min_quantity` | integer | no | Minimum quantity of items. |
| `customer_eligibility` | see notes | no | `"all"` (default), `{"segments": [names]}`, `{"first_order": true, "segment": "name"}` (first-order offers, which Shopify enforces through a customer segment), `{"markets": [names]}` or `{"customers": true}`. |
| `usage_limit` | integer | no | Total uses (Shopify's *Limit number of times this discount can be used in total*). |
| `once_per_customer` | bool | no | Shopify's *Limit to one use per customer*. |
| `combines_with` | array of `product`, `order`, `shipping` | no | Classes the offer may combine with. Default: none. |
| `starts`, `ends` | `YYYY-MM-DD HH:MM` (store time) or null | no | Null start = already running ("always on"); null end = no end date. A date without a time means 00:00 for `starts` and 23:59 for `ends`. |
| `phase` | string | no | Marks a sale phase (VIP early access, main sale). Phases need both dates; consecutive phases are checked for gaps and overlaps. |
| `public` | bool | no | `true` for codes meant to be shared publicly, so they aren't flagged as guessable. |

## 2. Live discounts (`--discounts`, JSON, required)

`scripts/normalize_shopify_discounts.py` writes this file from the Admin GraphQL API response. You can also write it by hand from the Discounts page.

```json
{"source": "Shopify Admin GraphQL discountNodes", "shop": "Store", "exportedAt": "2026-10-06T14:05:00-04:00",
 "discounts": [{"id": "gid://shopify/DiscountNode/…", "title": "WELCOME10", "method": "code", "codes": ["WELCOME10"],
   "class": "order", "type": "percentage", "value": 10, "target": {"type": "all"}, "minimum": null,
   "eligibility": {"type": "segments", "names": ["Never purchased"]}, "usageLimit": null, "oncePerCustomer": true,
   "timesUsed": 1734, "combinesWith": {"productDiscounts": true, "orderDiscounts": false, "shippingDiscounts": true},
   "tags": [], "startsAt": "2025-02-01T05:00:00Z", "endsAt": null, "status": "ACTIVE", "updatedAt": "2026-03-18T15:22:09Z"}]}
```

| Field | Required | Notes |
| --- | --- | --- |
| `exportedAt` | recommended | When the export was taken, with offset. It is the report's "now" (which discounts are live today). Without it, pass `--as-of`. |
| `id`, `title` | yes | |
| `method` | yes | `code` or `automatic`. |
| `codes` | code discounts | Up to the first few codes; `codesCount` gives the total for bulk-code discounts. |
| `class` | yes | `product`, `order`, `shipping`, or `multi` (an app discount with several classes; `classes` lists them). |
| `type` | yes | `percentage`, `fixed_amount` (with `appliesOnEachItem`), `free_shipping`, `bxgy` (with a `bxgy` object), `app` or `unknown`. App, multi-class and unknown discounts are listed but not modeled. A `bxgy` object has `buys` (`quantity` or `amount`), `buysTarget`, `gets` (`quantity`, `percentage`, and `amount` when the get is a fixed amount off) and `usesPerOrderLimit`; a fixed-amount get is described ("$5 off the get item") but not modeled for stacking or below-cost, so check it manually. |
| `value` | percentage, fixed | Percent 0–100 (the normalizer converts Shopify's 0–1 fraction) or amount. In a hand-built file a percentage of 1 or less prints a warning (0.25 is read as 0.25%, not 25%). |
| `target` | product discounts | `{"type": "all"}`, `{"type": "collections", "collections": [titles]}`, `{"type": "products", "products": [handles], "variants": [skus]}`, or `{"type": "unknown"}` (app and multi-class discounts: the API doesn't show what they reach, so eligibility is an INFO "check manually"). `"truncated": true` means the export cut the list off at 50; that discount's eligibility is skipped and listed under Not checked. |
| `minimum` | yes (may be null) | `null`, `{"subtotal": 75}` or `{"quantity": 2}`. |
| `eligibility` | no | `{"type": "all" / "segments" / "customers" / "markets" / "unknown", "names": [...]}`. |
| `usageLimit`, `oncePerCustomer`, `timesUsed` | no | Automatic discounts have no usage limit. |
| `combinesWith` | yes | `productDiscounts`, `orderDiscounts`, `shippingDiscounts` (true/false); `sameLineTags` for Plus same-line combinations. |
| `tags` | no | The discount's own tags (used with `sameLineTags`). |
| `startsAt`, `endsAt` | yes / null | ISO 8601 **with offset or `Z`**. A time without an offset is rejected. |
| `status` | yes | `ACTIVE`, `SCHEDULED` or `EXPIRED` (Shopify's `DiscountStatus`). |
| `updatedAt` | no | Shown as "last edited". |

### Getting it from Shopify

You need an Admin API token with `read_discounts` (type.com's Shopify integration, a custom app, or the Shopify GraphiQL app). Never paste tokens into chat. Run this query, follow `pageInfo.endCursor` with `after:` until `hasNextPage` is false, save each response page as JSON, then:

```bash
python3 scripts/normalize_shopify_discounts.py --in page1.json --in page2.json \
  --exported-at 2026-10-06T14:05:00-04:00 --shop "Store name" --out discounts.json
```

`--print-query` prints the same query. The normalizer refuses a response with `errors`, and a last page with `hasNextPage: true` unless you pass `--allow-partial`.

```graphql
query DiscountsForQA($after: String) {
  discountNodes(first: 25, after: $after, query: "status:active,scheduled") {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      discount {
        __typename
        ... on DiscountCodeBasic {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountAutomaticBasic {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount tags
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountCodeFreeShipping {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          maximumShippingPrice { amount }
        }
        ... on DiscountAutomaticFreeShipping {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount tags
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          maximumShippingPrice { amount }
        }
        ... on DiscountCodeBxgy {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer usesPerOrderLimit tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          customerBuys { value { ...BuysValue } items { ...Items } }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountAutomaticBxgy {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount usesPerOrderLimit tags
          combinesWith { ...Combines }
          context { ...Context }
          customerBuys { value { ...BuysValue } items { ...Items } }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountCodeApp {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          appDiscountType { title }
        }
        ... on DiscountAutomaticApp {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount tags
          combinesWith { ...Combines }
          context { ...Context }
          appDiscountType { title }
        }
      }
    }
  }
}

fragment Combines on DiscountCombinesWith {
  productDiscounts orderDiscounts shippingDiscounts
  productDiscountsWithTagsOnSameCartLine
}

fragment Context on DiscountContext {
  __typename
  ... on DiscountCustomerSegments { segments { name } }
  ... on DiscountCustomers { customers { id } }
  ... on DiscountMarkets { markets(first: 20) { nodes { name } } }
}

fragment Minimum on DiscountMinimumRequirement {
  __typename
  ... on DiscountMinimumSubtotal { greaterThanOrEqualToSubtotal { amount } }
  ... on DiscountMinimumQuantity { greaterThanOrEqualToQuantity }
}

fragment GetsValue on DiscountCustomerGetsValue {
  __typename
  ... on DiscountPercentage { percentage }
  ... on DiscountAmount { amount { amount } appliesOnEachItem }
  ... on DiscountOnQuantity {
    quantity { quantity }
    effect {
      __typename
      ... on DiscountPercentage { percentage }
      ... on DiscountAmount { amount { amount } }
    }
  }
}

fragment BuysValue on DiscountCustomerBuysValue {
  __typename
  ... on DiscountQuantity { quantity }
  ... on DiscountPurchaseAmount { amount }
}

fragment Items on DiscountItems {
  __typename
  ... on DiscountProducts {
    products(first: 50) { pageInfo { hasNextPage } nodes { handle } }
    productVariants(first: 50) { pageInfo { hasNextPage } nodes { sku } }
  }
  ... on DiscountCollections { collections(first: 50) { pageInfo { hasNextPage } nodes { title } } }
}
```

**API version.** The query follows the GraphQL Admin API **2026-10** reference (`discountNodes`, the `Discount` union, `DiscountCombinesWith`, `DiscountContext`, `DiscountCustomerGets`, `DiscountItems`, `DiscountMinimumRequirement`, `DiscountRedeemCode`, `DiscountStatus`, `DiscountClass`). It was written from the documentation, not run against a live store, so check it against the version you call:
- **Use API version 2025-10 or later.** Shopify's docs say earlier versions leave automatic discounts that aren't open to all customers out of `discountNodes`, so an older export can be missing discounts; renaming fields doesn't fix that.
- `productDiscountsWithTagsOnSameCartLine` is in 2026-04 and later, not in 2026-01. Remove it from the `Combines` fragment on older versions.
- The normalizer still accepts the older spellings (`customerSelection` for `context`, `discountClass` for `discountClasses`, `usageCount` for `asyncUsageCount`) for files exported earlier.
- A target list longer than 50 products, variants or collections comes back with `pageInfo.hasNextPage` true. The normalizer warns ("target truncated at 50; check in admin") and marks the target `truncated`.
- Requested cost grows with the page sizes. If Shopify answers `MAX_COST_EXCEEDED` or throttles, lower `first:` on `discountNodes` or on the nested `products` / `collections` connections.
- `percentage` comes back as a fraction (0.25 = 25%; Shopify's input docs say 0.00–1.00). The normalizer multiplies by 100 and warns if a value is above 1. A non-numeric value exits 2.
- `query: "status:active,scheduled"` leaves out expired discounts. Drop it to also confirm that old codes are expired.

**By hand.** Discounts page → open each discount and copy: method, the *Amount off products / order* choice (class), value, Applies to, Minimum purchase requirements, Eligibility, Maximum discount uses, Combinations, Active dates (convert to UTC with the store's offset for those dates) and status.

## 3. Catalog (`--products`, CSV, required)

One row per variant.

| Column | Required | Notes |
| --- | --- | --- |
| `handle` | yes | Product handle. |
| `sku` | yes | Unique per row. If a variant has no SKU in Shopify, put any unique ID; variant-level discount targets match on SKU. |
| `price` | yes | Current selling price. |
| `title`, `variant_title` | no | Display name: `Title — Variant` (`Default Title` is dropped). |
| `compare_at_price` | no | Blank if none. |
| `unit_cost` | no | Shopify *Cost per item*. Blank skips the margin checks for that SKU. |
| `collections` | yes in practice | Collection titles separated by `;`. Collection targets and exclusions resolve through this column. |
| `inventory` | no | Available units (sums the stock at risk in below-cost findings). |
| `units_sold_90d` | no | Units sold in the last 90 days (context only). |
| `gift_card` | no | `true`/`false`. If the column is missing, a SKU counts as a gift card when its handle starts with `gift-card` or it is in a collection named Gift Card(s). |

From the Admin API: `products(first: 100) { nodes { handle title isGiftCard collections(first: 50) { nodes { title } } variants(first: 100) { nodes { sku title price compareAtPrice inventoryQuantity inventoryItem { unitCost { amount } } } } } }` (field names checked against the 2026-10 reference). Units sold come from Analytics → Reports → sales by product variant SKU, last 90 days. The standard product CSV export has no collections column, so use the API or add collections by hand.

## 4. Price history (`--price-history`, CSV, optional)

| Column | Required | Notes |
| --- | --- | --- |
| `sku` | yes | Rows for SKUs not in the products file are ignored (and counted). |
| `date` | yes | `YYYY-MM-DD`. Each row is the price **from that date on** until the SKU's next row, so a row per change is enough; daily snapshots work too. One row per SKU per date. |
| `price` | yes | Selling price. |
| `compare_at_price` | no | Blank if none. |

Cover at least 90 days before any compare-at change you want judged. Sources: a price-tracking or product-history app, product exports saved over time, or your own records. Without this file the report says compare-at raises were not checked.

## 5. What the analyzer does with it

**Matching.** Each planned offer is matched to a live discount by code (code offers) or title (automatic offers). A planned offer missing from the export, or expired, is critical. Then, per offer: a different type, or a deeper value, is critical; a different class (*Amount off products* vs *order*), a shallower value, a different minimum (other than free shipping, see below), a different customer eligibility, a missing or higher usage limit, a missing once-per-customer limit, and usage at 80% or more of the limit are warnings. Live discounts that aren't planned and aren't expired are "not in the plan" (warning); `must_not_be_active` codes that are active or scheduled are critical.

**Windows.** Plan times are store time; live times are converted from UTC. An end at hh:59:00 counts through that minute, so 11:59 pm followed by 12:00 am is no gap. A time that differs from the plan is checked against UTC, Pacific, Central, Mountain, Eastern and UK time to name the slip (for example "23:59 UTC"). Gaps and overlaps between consecutive phases longer than one minute, and not in the plan, are warnings. A phase with no end date, or one that starts early and is already live, is critical.

**Eligibility.** A product discount's target resolves to SKUs through the products file and is compared with the plan's eligible set. Extra SKUs in a collection the plan excludes are critical; other extras and missing SKUs are warnings. Order and shipping discounts apply to the whole cart.

**Stacking model** (each rule is labeled with its source):
- Two discounts combine only if **each** allows the other's class; two shipping discounts never combine. *(Confirmed: Shopify Help Center, "Combining discounts": "Multiple discounts are applied to an order only when the setting on each discount allows the combination"; the supported-combinations table.)*
- Product discounts apply first, then order discounts on the subtotal after product discounts, then shipping. Several percentage order discounts are each taken on that same subtotal (10% + 15% of $350 = $262.50); a percentage order discount applies before a fixed one. *(Confirmed: same page, "Discount application order", "How percentage order discounts stack" and its worked examples. tests/run_tests.sh reproduces five of its examples to the cent.)*
- One product discount per item, unless both carry tags that let them share a line, which needs Shopify Plus. *(Confirmed: `DiscountCombinesWith.productDiscountsWithTagsOnSameCartLine`, "By default, only one product discount applies per line"; Help Center.)* With `shopify_plus: true`, groups of up to three tagged product discounts are tried; stacked percentages on one line are added (assumption: worst case).
- When discounts can't combine, the customer gets the best one or the best combination. *(Confirmed: Help Center, "Best discount selection".)* So the merchant's worst case is the deepest valid combination.
- Up to 5 product/order codes and 1 shipping code per order. *(Confirmed: Help Center, "Discount limits".)*
- Gift cards are discounted only by a discount that names the gift card product; All products and collection targets skip them. *(Confirmed: Help Center, "Add a discount to a gift card" and the discounts FAQ. Assumption: order discounts also skip gift-card lines.)*
- Minimums. Product discounts count only the items they target; an order discount's minimum must be met before product discounts (no gift cards) and after them. *(Confirmed: Help Center, "Amount off discounts", minimum purchase requirements.)* The free-shipping minimum counts prices after combinable discounts *(confirmed, "Free shipping discounts")*; this script applies it after product **and** order discounts (assumption).
- Rounding: each discount is rounded to the cent, half up, per line (assumption; Shopify may differ by a cent).
- Buy X get Y: the worst case spreads the free or discounted unit over the bundle at equal prices (approximation); test carts skip BXGY. A buy X get Y whose get is a fixed amount off isn't modeled. App and multi-class discounts run their own Function logic. All of these are listed under "Not checked" (stacking and below-cost: check manually).
- A fixed amount off products split across the items (`appliesOnEachItem` false) with a minimum purchase is shared by a cart exactly at that minimum: a $30 item under "$20 off, $100 minimum" gets at most $20 × 30/100 = $6.

**Worst case per SKU.** For every stretch of time between now and a day after the last phase (wherever a discount starts or ends), the deepest price one unit can reach with any valid combination of live product and order discounts, assuming each minimum is met (a shopper can add items) and ignoring customer eligibility (one customer can be in several segments). A fixed order discount with a minimum is shared by a cart exactly at that minimum. The same is computed for the plan, so the report shows planned and live side by side. Depth above `max_item_discount_pct` is critical when a forbidden combination causes it; a price below unit cost is critical; a margin under the floor is a warning. Margin = (discounted price − unit cost) ÷ discounted price, before shipping and payment fees.

**Codes and free shipping.** Usage limit missing or higher than planned, not once per customer when planned, usage at 80% or more of the limit (warning). A shipping discount whose minimum is below the plan's is critical; the estimate is `last_bfcm_orders_under_free_shipping_min × shipping_cost_per_order`, plus a daily figure at `orders_per_day_now` and last year's share, labeled as an estimate. A code open to everyone, uncapped, 15% or deeper (or free shipping) and shaped like WORD+NUMBER is flagged as guessable unless the plan marks it `public`.

**Reference prices.** Compare-at below the selling price (products file). With history: a compare-at increase still in effect, to a price above the selling price, is a warning when the SKU sold at that compare-at or more on fewer than 30 of the 90 days before the increase (a markdown whose compare-at is the old selling price passes). With under 30 days of history before the increase it is listed as INFO. This is a review trigger based on the FTC's Guides Against Deceptive Pricing (16 CFR 233.1: a former price should be one at which the item was openly and actively offered for a reasonably substantial period, in the recent regular course of business), not a legal test or conclusion. A current compare-at above the price that never appears as a selling price anywhere in the SKU's history (for example, already in place on the first history row) is also a warning. This check follows US (FTC 16 CFR 233.1) guidance. It does not apply the EU/UK rule that the reference price must be the lowest price in the prior 30 days.

**Test checkouts.** For each finding the script builds a cart, a time and a customer, then simulates checkout twice with the same rules: once with the discounts as planned and once with only that finding's setting as found. It lists the cart only when the totals differ. Row 1 is the worst case as exported (every finding at once).

## 6. Verdict and dates

- **NOT READY**: at least one critical finding.
- **FIX BEFORE LAUNCH**: warnings only.
- **READY**: no critical or warning findings (INFO notes allowed).

Dates count back from `launch`: fix by −14 days, re-check and test checkouts −7, discount freeze −3, final export and run −1. Items live today (a discount costing money now) are due today. A date already past prints as "now (… has passed)". `--as-of` defaults to the export date; an export older than the report date is flagged.
