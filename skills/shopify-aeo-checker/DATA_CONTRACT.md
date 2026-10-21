# Data contract

## Share of shelf (`scripts/aeo_global.py`)

Fixture folder (`--record` writes it, `--fixtures` replays it):

```
pdp.json                    # product page facts + shop identity
search/<query-slug>.json    # {"total": <int|null>, "products": [...]} trimmed Global Catalog results
```

`pdp.json` fields: `url, handle, domain, title, type, vendor, tags, description, price,
options[{name, values}], shop_name, myshopify_domain, shop_domain`.

Trimmed product fields: `title, url, handle, description (plain text, max 600 chars),
price, currency, image, alt, seller, seller_domain, seller_url, rating, reviews`.

Outputs in `--out`: `report.md`, `aeo-data.json` and `aeoData.ts` (same data as a
TypeScript module for `app_template/`). `aeo-data.json` fields: `brand, date, top,
country, endpoint, profile, tgcLogo, product{handle, title, url}, match{names, hosts,
myshopify}, runs[{query, total, products}]`.

Prices are integers in minor units (cents). Query slug: lower-case, non-alphanumerics
replaced by `-`, max 80 chars.

## Single-store check (`scripts/aeo_check.py`)

### Input file (`--input`)

```json
{
  "brand": "Northfield Knit",
  "domain": "northfield-knit.example",
  "queries": ["merino sweater for travel"],
  "products": [
    {"url": "https://northfield-knit.example/products/harbor-merino-crew-oatmeal", "queries": []},
    {"url": "https://northfield-knit.example/products/ridgeline-cardigan-charcoal", "queries": ["warm cardigan gift"]}
  ]
}
```

- `products[].url` (required): a product page URL containing `/products/<handle>`.
- `products[].queries` (optional): shopper questions for that product only.
- `queries` (optional): shopper questions run for every product.
- `--product` and `--query` flags add to whatever the file contains.

### Fixture folder

```
manifest.json               # the store's /.well-known/ucp response
pages/<handle>.json         # scraped product page facts
search/<query-slug>.json    # {"products": [...]} top 20 UCP results, trimmed
```

Page facts fields: `handle, url, title, type, vendor, tags, price, compare_at,
available, images, options[{name, values}]`.

Trimmed search product fields: `handle, title, url, description (plain text, max 1200
chars), price, currency, list_price, media[{url, alt}] (max 6), media_count,
categories, tags, available`.
