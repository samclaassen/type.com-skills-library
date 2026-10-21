# Data contract: bfcm-tracking-preflight

Three CSV files. UTF-8 (a BOM is fine), header row first, one comma-separated row per record. Column order doesn't matter and unknown columns are ignored. Any validation failure exits 2 with the file, line, column and reason.

**Numbers** are parsed strictly. Allowed: `1234.56`, `1,234.56`, a leading or trailing currency symbol (`$1,234.56`, `€1,234.56`). Commas are accepted only as thousands separators (`^\d{1,3}(,\d{3})*(\.\d+)?$`). Rejected with exit 2: comma decimals (`2255,74`, `28.320,00`), `nan`, `inf`, exponents (`1e9`), stray text and negative values (including `(50.00)`); nothing in these files may be negative. A numeric column where more than half the values look European-formatted (`1.234,56`) fails with a specific message: export with `.` as the decimal separator.

**Net sales** everywhere in this skill = gross sales − discounts, before returns/refunds, excluding tax and shipping. In Shopify's *Sales over time* report that is *Gross sales* − *Discounts* (Shopify's own *Net sales* column also subtracts returns, so don't use it as-is).

## 1. Daily events (`--events`, required)

One row per day, **28 days** recommended (at least 14). If the file has more, the last 28 are used. Dates must be consecutive, with no gaps or duplicates. Count only **online-store** orders; leave out POS, draft and wholesale orders, because no pixel sees them.

| Column | Type | Required | Where to get it |
| --- | --- | --- | --- |
| `date` | `YYYY-MM-DD` | yes | Store time zone. |
| `shopify_orders` | integer | yes | Shopify Analytics → Reports → *Sales over time* (or *Orders over time*), by day, sales channel = Online Store. |
| `shopify_net_sales` | number | yes | Same report, *Gross sales* − *Discounts* (before returns/refunds, excluding tax and shipping). |
| `meta_purchases` | integer | Meta | Events Manager → your dataset (pixel) → Overview → **Purchase**, by day. This is the total **after** deduplication. |
| `meta_browser_events` | integer | recommended | Same view, broken down by connection method: **Browser**. |
| `meta_server_events` | integer | recommended | Same view: **Server** (Conversions API). Leave the column out only if Events Manager shows no server events at all; the report then flags CAPI as missing. |
| `meta_purchase_value` | number | optional | Purchase value Meta received, by day (after dedup). |
| `google_conversions` | integer | Google | Google Ads → Goals → Conversions → your purchase action, *Conversions* (or *All conv.*) **by conversion time**, by day. These are ad-attributed only. |
| `google_conversion_value` | number | optional | Same, *Conv. value*. |
| `tiktok_purchases` | integer | TikTok | TikTok Events Manager → your pixel → Overview → **Complete Payment** (or Purchase), by day. Site-wide, like Meta. |
| `tiktok_purchase_value` | number | optional | Same, value. |

At least one platform count column (`meta_purchases`, `google_conversions` or `tiktok_purchases`) is required. Meta browser, server or value columns without `meta_purchases` are rejected. Integers must be whole and nothing may be negative or blank (write `0`).

What the analyzer does with it:
- **Match rate** (Meta, TikTok): platform purchases ÷ Shopify orders over the last 7 days. OK 0.85–1.10x, warning 0.70–0.85x or 1.10–1.25x, critical outside 0.70–1.25x. Google Ads is not match-rated because it only counts ad-attributed conversions.
- **Deduplication** (Meta, needs browser and server columns): per day, merged = browser + server − purchases, and dedup rate = merged ÷ min(browser, server). A day is *broken* when the dedup rate is under 50% **and** Meta counted more than 1.25 purchases per order. The most recent run of broken days (two or more, or ending on the last day) is reported with its start date.
- **Conversions API**: critical if there is no server column or no server events in the last 7 days; warning if server events cover under 0.60 of orders.
- **Zero-event days**: a day with 0 events for a stream that normally sends at least 3 a day (window median), while Shopify had at least 5 orders. Ongoing (includes the last day) is critical; recovered is a warning.
- **Value**: (platform value ÷ platform events) ÷ (Shopify net sales ÷ orders) over the window. OK 0.85–1.15x, warning up to 0.50–2.00x, critical beyond.

## 2. Orders with landing site (`--orders`, optional)

One row per online-store order, ideally the same 28 days. Orders dated outside the events window are counted and reported as an INFO line, because they make the UTM shares describe a different period.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `order_id` | string | yes | e.g. `#18241`. Must be unique: a repeated `order_id` exits 2. |
| `date` | `YYYY-MM-DD` or ISO timestamp | yes | Order date. A timestamp such as `2026-09-08T14:03:00-05:00` (or `…Z`) is accepted; its calendar date is taken as written in that offset, so export in the store time zone. Trailing text (`2026-09-08garbage`) is rejected. |
| `landing_site` | string | yes | Landing page path or URL **with its query string**, e.g. `/products/apron?utm_source=facebook&fbclid=…`. Blank if unknown. |
| `referring_site` | string | no | Referrer URL. Blank means none. |
| `utm_source`, `utm_medium`, `utm_campaign` | string | no | If present and non-blank, these win over values parsed from `landing_site`. |

Where to get it:
- **Admin GraphQL API:** `orders { name createdAt customerJourneySummary { lastVisit { landingPage referrerUrl utmParameters { source medium campaign } } } }`. Write `landingPage` to `landing_site`, `referrerUrl` to `referring_site`, and the UTM fields to their columns.
- **Legacy REST:** `GET /admin/api/<version>/orders.json?status=any&fields=name,created_at,landing_site,referring_site`.
- **Export apps** that include *landing site* and *referring site* columns work too.

What the analyzer does with it: share of orders without `utm_source`; "direct" = no UTM, no referrer and no click ID; click IDs without UTMs (`fbclid` → Meta, `ttclid` → TikTok, `msclkid` → Microsoft Ads are warnings; `fbclid` is also added to organic Facebook/Instagram link clicks, so it is reported as a Meta click ID from ads or organic posts, not proof of an untagged ad; `gclid`/`gbraid`/`wbraid` → Google Ads are info, because auto-tagging is normal); `utm_source` spelling variants (case-insensitive, with aliases such as `fb`, `ig`, `instagram`, `facebook.com` → `facebook`, and `adwords` → `google`); unfilled macros (`{{…}}`, `{…}`, `__…__`) in source, medium or campaign; the top 5 landing pages among orders without UTMs. No-UTM share: warning ≥ 20%, critical ≥ 40%.

## 3. Last BFCM (`--last-bfcm`, optional)

One row per day of last year's BFCM period, typically the Monday before Thanksgiving through the day after Cyber Monday. Dates need not be consecutive, but every date must be in November or December (anything else exits 2).

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `date` | `YYYY-MM-DD` | yes | |
| `shopify_net_sales` | number | yes | All online-store net sales that day (Shopify *Sales over time*: gross sales − discounts, before returns, excluding tax and shipping). |
| `<platform>_claimed_revenue` | number | ≥ 1 | Revenue the platform's own reporting credited to itself that day, at the attribution setting you actually used (e.g. Meta 7-day click / 1-day view *Purchases conversion value*; Google Ads *Conv. value* by conversion time; TikTok *Complete payment value*). `<platform>` is any lowercase key: `meta`, `google`, `tiktok`, `pinterest`, … |
| `<platform>_spend` | number | no | Ad spend that day. Any `*_spend` column feeds blended MER. Include spend for **every** platform with a `_claimed_revenue` column: if one has claimed revenue but no spend, the report names it, marks MER as overstated and does not use it for budgets. |
| `<platform>_utm_sales` | number | no | Shopify net sales from orders whose UTM source is that platform (Shopify Analytics → *Sales attributed to marketing*, by UTM source, last click). Gives each platform's attribution-gap ratio. |

Outputs: total and per-day sum of claims ÷ Shopify net sales; per platform, claimed ÷ UTM sales (descriptive attribution gap), reported ROAS (claimed ÷ spend) and Shopify UTM (last-click) ROAS (UTM sales ÷ spend); blended MER = Shopify net sales ÷ total spend. Summed platform claims ÷ total spend is deliberately not computed or exported (platform ROAS cannot be added across platforms); the JSON has `mer`, `mer_complete` and `missing_spend` instead. The claim gap does not change the verdict and is not a causal contribution estimate or ROAS correction multiplier. The JSON key over_claim is retained for compatibility and contains this descriptive ratio. Aggregate purchase-value differences require matched-order reconciliation before diagnosing value errors.

## 4. Verdict

- **NOT READY**: at least one critical finding.
- **FIX BEFORE BFCM**: warnings only.
- **READY**: nothing above info in the checks that ran. Checks that didn't run (missing optional file) are listed under "Not checked".

Dates: Black Friday is the day after the fourth Thursday of November (Nov 27 in 2026). Fix-by = Black Friday − 17 days, re-check = − 14, budgets = − 10, change freeze = − 7, final run = − 3. `--as-of` defaults to the last date in `--events`; a deadline already past prints as "now (… has passed)".
