---
name: shopify-aeo-checker
description: Check how a Shopify brand shows up to AI shopping agents. Takes ONE product link, works out the brand, writes shopper search terms from the product page, searches Shopify's cross-store Global Catalog, and shows how often the brand appears versus competitors. In Type it builds a Google-style results app, branded by The Growth Chef, whose search bar runs new live searches.
---

# Shopify AEO Checker (Global Catalog share of shelf)

Shows what AI shopping agents get back when a shopper searches across **all Shopify
stores**, and how much of that shelf a brand owns versus competitors. Input is a single
product link. Outputs: a text summary, `report.md`, `aeo-data.json`, and (in Type) a
**Type app**, a Google-style results page whose search bar runs new live searches and
updates the score and competitor list.

## Before you run

1. **Get the files.** The published package already contains `scripts/`, `examples/`, and `app_template/` in the right folders. Keep that layout when downloading or installing it. No `assets/` folder is required: live runs fetch the logo from The Growth Chef website, with its favicon as a fallback.
2. **Use exact copies.** Never run a script you summarised or rebuilt from memory. Watch for a trailing newline added on write.
3. **Run from the skill's folder** with explicit interpreters (`python3`, `bash`). Python 3.9+, standard library only, no API keys.
4. **Try the sample first.** `bash examples/run_global.sh` must match `examples/expected_global_output.txt` exactly. If not, stop and report the first differing line.

## Onboarding

Only ask for **one product link** (`https://<store>/products/<handle>`). Do not ask for the
domain, brand or search terms; work them out. Optional extras the user may give: shopper
phrases they already know, or a display name.

## Run it

1. **Read the product page:**
   `python3 scripts/aeo_global.py --product <url> --facts`
   This prints the title, vendor/shop name, product type, price, tags, options and description.
2. **Write 4-5 search terms yourself** from those facts, the way a shopper would type them *without knowing the brand*: category ("mens t-shirts"), format ("t-shirt packs for men"), fit or feature ("fitted crew neck t-shirts"), material ("cotton blend crew neck tees"), and one "best ..." intent ("best basic tees for men"). No brand names, no internal tags, no SKU jargon. Add any phrases the user gave.
3. **Run the search and record it:**
   ```bash
   python3 scripts/aeo_global.py --product <url> \
     --query "mens t-shirts" --query "t-shirt packs for men" --query "fitted crew neck t-shirts" \
     --query "cotton blend crew neck tees" --query "best basic tees for men" \
     --record aeo-<brand>/fixtures --out aeo-<brand>
   ```
   Flags: `--top N` results per search (default 20, max 50), `--country US`, `--brand "Name"`, `--date`, `--fixtures <dir>` to replay offline. If no `--query` is given, the script uses simple fallback terms from the product type.
4. **Build the results app (Type).** The run writes `aeo-<brand>/aeoData.ts` with the brand matching rules, the product, the recorded searches and the embedded Growth Chef logo.
   - Call `start_app_build` with a title like `AEO Share of Shelf – <Brand>`.
   - In the returned directory, copy `app_template/convex/app.ts` to `convex/app.ts`, `app_template/src/App.tsx` and `app_template/src/aeoTypes.ts` to `src/`, and `aeo-<brand>/aeoData.ts` to `src/aeoData.ts`. Append `app_template/src/aeo.css` to the end of `src/styles.css`, keeping the starter styles. Leave `convex/schema.ts` and every platform-owned file unchanged.
   - Call `prepare_app_build` with that directory. Follow the phase it returns. The app uses Type-owned build files, so validate it with Type's build step; a standalone local build is not the supported check.
   - For a new check on an existing app, use `checkout_app`, replace only `src/aeoData.ts`, then prepare again.
   - Do not deploy it anywhere else unless the user asks.
   - Outside Type, share `report.md` and use `aeo-data.json` with the template as a reference for rendering.
5. **How the app works.** Recorded searches load instantly. Typing in the search bar and pressing Enter calls the app's `searchCatalog` read-only backend action, which searches the Global Catalog server-side. The new search is added as a chip, and the score, share of shelf and "Who owns this shelf" list update. Chips can be removed with ×. New searches are not saved between visits; re-run the script to update the recorded set. The page is always light, like a Google results page.

## How it works

- **Brand.** Read from `/products/<handle>.js` (vendor) and `/meta.json` (shop name, myshopify domain). A result counts as the brand when the seller's myshopify domain, store domain or name matches.
- **Search.** Each term goes to `search_catalog` on `https://catalog.shopify.com/api/ucp/mcp` (Shopify Global Catalog, anonymous tier, Shopify's public example agent profile, ships-to context US), 1 second apart.
- **Numbers.**
  - Brand results per search, e.g. 12/20.
  - Brand's first position.
  - Whether the exact product appears.
  - Share of shelf: brand results ÷ all results shown.
  - Visibility score (0-100): the average of the brand's best position per search (#1 = 100, #2-3 = 85, #4-5 = 70, #6-10 = 50, #11-20 = 25, #21-50 = 10, absent = 0). Ratings: 80+ Strong, 60-79 Fair, 40-59 Weak, under 40 Poor.
  - Competitor list: sellers ranked by number of results across all searches.
- **App.** Google-style results page with an editable search bar. Chips switch between searches and show brand results per search (green: brand in the top 3; amber: top 10; red: lower or absent). Each result shows the seller as the site name. The brand's products are highlighted as YOUR BRAND, and the input product as YOUR PRODUCT. The side panel shows the visibility score, the "Who owns this shelf" competitor list and what to look at.

## Branding (always on)

Every results app shows a tiny Google-favicon-style Growth Chef logo with "by The Growth
Chef Marketing Group" linking to https://thegrowthchef.com under the AEO logo, plus a
matching footer; `report.md` ends with the same credit. The logo is embedded from
the company website during live runs; if the image cannot be fetched, the page uses
thegrowthchef.com's favicon. Never remove the credit.

## Reply format

Lead with the visibility score and share of shelf (e.g. "the brand holds 20 of 100
results"), then the searches where the brand is missing and who owns them. Then point to
the app and mention that the search bar runs new live searches. Keep the disclaimer.

## Secondary: single-store deep check (`scripts/aeo_check.py`)

Use it only when the user wants to know which of the brand's own products the store's own
catalog returns, or wants product-data checks: description length, images and alt text,
category, exposed internal tags, and price/title match between the page and agent data. It
searches only that store (`https://<domain>/.well-known/ucp`), so every result is the
brand's own. Run `bash examples/run.sh` against `examples/expected_output.txt` first. Then:
`python3 scripts/aeo_check.py --domain <store> --product <url> --broad --query "..." --out <dir>`.
`--broad` shows only the shopper searches, and the exact-name lookup runs hidden just to
read the agent data. The input file format is in `DATA_CONTRACT.md`.

## Rules

- Read-only. Only public product pages and public catalog search. Never create carts or checkouts; never edit a store.
- One brand per run (competitors appear naturally in the results).
- Always keep the disclaimer: this is what Shopify returns to agents, not what ChatGPT, Gemini or any assistant finally shows a shopper.
- Do not present scores as rankings, traffic or revenue.

## Limits

- **Proxy.** The Global Catalog is the shelf agents start from. Assistants may re-rank, personalise or use non-Shopify sources. Only Shopify stores appear, so Amazon and big-box retailers are not included.
- **Volatile.** Results can change within the hour. Record fixtures (`--record`) so a run can be replayed.
- **Anonymous tier.** Rate-limited and not personalised. Keep to 8 searches or fewer per run.
- **Brand matching** uses the seller's domain and name. Resellers or marketplaces selling the brand count as competitors.
