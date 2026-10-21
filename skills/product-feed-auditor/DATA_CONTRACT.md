# Data contract — product-feed-auditor

One input file. No network access, no credentials in the script.

## File: product feed export (`--feed`)

- Format: CSV (default) or TSV (`--delimiter '\t'`).
- Encoding: UTF-8, with or without a BOM.
- First row is a header. Column names are the Merchant Center product data
  specification names, lower case with underscores.
- One row per **item id** submitted to Merchant Center — that is, one row per
  variant, not per parent product.
- Upstream systems: Merchant Center (Products → download, or the processed
  primary feed), the Shopify Google & YouTube channel feed export, or the
  output feed of a feed vendor such as Feedonomics, DataFeedWatch, or
  Channable. Always export the feed that is actually **submitted**, after feed
  rules and transformations, not the raw catalogue.

### Required columns

The run exits 2 and names the missing columns if any of these are absent. A
column may be present and empty; that is reported as a coverage gap, which is
the honest result.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `id` | string | — | Merchant Center item id. Case sensitive, must be unique per row. |
| `title` | string | plain text | No HTML. Spec limit 150 characters. |
| `description` | string | plain text | No HTML. Spec limit 5000 characters. |
| `link` | string | absolute URL | Must be `https://` and on the claimed domain. |
| `image_link` | string | absolute URL | Primary image. Must be `http(s)://`. |
| `availability` | enum | `in_stock`, `out_of_stock`, `preorder`, `backorder` | Lower case with underscores. |
| `price` | money | `AMOUNT CURRENCY`, e.g. `128.00 USD` | Decimal point, ISO 4217 code. `USD 128.00` is also accepted. Thousands separators are tolerated. |
| `brand` | string | — | Required for essentially all new branded products. |
| `condition` | enum | `new`, `refurbished`, `used` | Lower case. |

### Counting rules

- The coverage table is per **row**; a duplicated id contributes each of its
  rows, because each row carries its own attribute values.
- Finding item counts and the summary (at risk, warnings only, clean, and the
  at-risk share) are per **unique item id**. A duplicated id counts once; a row
  with a blank id counts as its own item. Pair findings (`ID-GTIN-REUSED`,
  `ID-CASE-COLLISION`) credit every id involved, not just the first.

### Optional columns

Present columns are audited; absent columns are skipped and do not appear in
the coverage table.

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `sale_price` | money | same format as `price` | Must be strictly lower than `price` and in the same currency. |
| `sale_price_effective_date` | string | `START/END` | ISO 8601 date or datetime on each side, slash separated, e.g. `2026-09-01T00:00-0700/2026-09-30T23:59-0700`. Only the date part is read. |
| `gtin` | string | 8, 12, 13, or 14 digits | Check digit is verified. Keep it text-formatted so spreadsheets do not strip leading zeros or convert to scientific notation. |
| `mpn` | string | — | Manufacturer part number. Satisfies the identifier requirement when no GTIN exists. |
| `identifier_exists` | enum | `yes` / `no` | Set to `no` only for genuinely identifier-less products (custom, handmade, vintage). Any other value is treated as "an identifier is expected". |
| `item_group_id` | string | — | Shared across variants of one product. Used to decide whether a shared GTIN is deliberate. |
| `google_product_category` | string | — | Coverage reporting only. |
| `product_type` | string | — | Coverage reporting only. |
| `additional_image_link` | string | — | Coverage reporting only; not URL-validated. |

Unlisted columns (`shipping`, `tax`, `color`, `size`, `age_group`, custom
labels, and so on) are ignored, not rejected. Keeping them in the export is
harmless.

## Other arguments

| argument | required | format | source |
| --- | --- | --- | --- |
| `--as-of` | yes | `YYYY-MM-DD` | The date the feed was exported. The only "today" the script has; used to decide whether a sale window has closed. |
| `--domain` | no | bare host, e.g. `shop.harborloom.com` | The claimed and verified domain in Merchant Center. Subdomains of it are accepted. Omitting it skips the landing-page domain check. |

## Producing the file by hand

A minimal valid file is a header row of the nine required column names
followed by one row per item, comma separated, with any field containing a
comma wrapped in double quotes:

```csv
id,title,description,link,image_link,availability,price,brand,condition
SKU-1,"Waffle Robe, Grey","Mid-weight waffle knit robe ...",https://shop.example.com/p/robe,https://cdn.example.com/robe.jpg,in_stock,128.00 USD,Example,new
```

`examples/sample_feed.csv` is a 22-row synthetic feed carrying one instance of
nearly every failure mode; use it as a template.

## Money handling

All monetary values are parsed with `decimal.Decimal`. No float arithmetic is
performed on currency anywhere in this skill.
