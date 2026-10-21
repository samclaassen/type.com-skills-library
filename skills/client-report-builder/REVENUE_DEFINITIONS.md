# Revenue definitions

Revenue skills in the Type library read their rules from one file,
`revenue_definitions.json`, so every report uses the same numbers. **Finance owns
this file.** Skills never pick a definition on finance's behalf.

- Skills built around these definitions (such as the daily revenue check)
  **require** the file: if a rule they need is missing, they name the missing key
  and stop.
- Older skills **use** the file when it is present. Without it they fall back to
  the defaults in their own data contract, and the first lines of their output say
  so and list those defaults, so nobody mistakes them for finance's definitions.

## Where it lives

`finance/revenue_definitions.json` in the working folder. A skill may accept
`--definitions <path>` to point elsewhere.

## Format

```json
{
  "version": "2026-09-23",
  "owner": "finance@example.com",
  "reporting_timezone": "America/New_York",
  "reporting_currency": "USD",
  "fx": { "source": "daily ECB close", "rates_file": "finance/fx_rates.csv" },

  "net_sales": {
    "starts_from": "gross_product_sales",
    "subtract_discounts": true,
    "subtract_refunds": true,
    "include_shipping_charged": false,
    "include_tax": false,
    "include_gift_card_sales": false
  },

  "refunds": {
    "date_basis": "refund_date",
    "late_refund_days": 7
  },
  "sale_date_basis": "order_created",

  "excluded_orders": {
    "tags": ["test", "internal", "replacement"],
    "financial_statuses": ["voided"],
    "cancelled": true,
    "zero_value": true
  },

  "channels": {
    "shopify":   { "source_of_truth": "Shopify Finance > Sales report", "export": "shopify_orders.csv", "dedupe_key": "order_id" },
    "amazon":    { "source_of_truth": "Amazon Seller Central > Date Range Report", "export": "amazon_orders.csv", "dedupe_key": "amazon_order_id" },
    "wholesale": { "source_of_truth": "Invoices in the accounting system", "export": "wholesale_invoices.csv", "dedupe_key": "invoice_id",
                   "revenue_event": "invoice", "exclude_retailer_sell_through": true }
  },
  "cross_channel_duplicates": [
    { "description": "Amazon MCF fulfilment orders mirrored into Shopify",
      "shopify_tag": "amazon-mcf" },
    { "description": "Wholesale draft orders created in Shopify for invoicing",
      "shopify_tag": "wholesale" }
  ],

  "marketing": {
    "attributed_revenue_basis": "platform_claimed_in_platform_window",
    "attribution_windows": { "meta": "7d_click_1d_view", "google": "30d_click", "tiktok": "7d_click_1d_view" },
    "mer": "net_sales / total_marketing_spend",
    "new_customer_definition": "first order ever, any channel, by customer email",
    "new_customer_cac": "total_marketing_spend / new_customers"
  },

  "checks": {
    "max_feed_staleness_hours": 6,
    "reconciliation_tolerance_pct": 0.5,
    "reconciliation_tolerance_abs": 50
  }
}
```

## Rules every revenue skill follows

1. **Days are reporting-timezone days.** Timestamps are converted to
   `reporting_timezone` before an order or refund is assigned to a day.
2. **One currency.** Amounts are converted to `reporting_currency` with the rates
   finance names. A skill never guesses a rate.
3. **Refunds land where finance says.** `refund_date` puts a refund on the day it
   was issued; `order_date` puts it back on the original sale's day. Reports say
   which basis they used.
4. **Excluded orders are reported, not hidden.** Every output states how many
   orders and how much value were excluded, and under which rule.
5. **Each channel is counted once, from its source of truth.** An order that
   appears in two channel exports is counted in one. Retailer sell-through is
   never wholesale revenue; wholesale revenue is what was invoiced to the retailer.
6. **Platform-claimed revenue is never totalled.** Meta, Google and TikTok each
   claim credit for overlapping sales. Their claimed revenue is shown per
   platform, with its attribution window, and never summed or presented as
   sales. Total sales come only from the channel sources of truth.
