---
name: bfcm-offer-config-qa
description: Pre-launch QA of a Shopify store's BFCM discounts against the offer plan. Catches sale windows set in the wrong time zone or with gaps, sales that reach clearance or new items, stacks deeper than planned and items sold below cost (modeled on Shopify's combination rules), uncapped creator codes, free shipping with no minimum and inflated compare-at prices. Writes a test-checkout script and a READY / FIX BEFORE LAUNCH / NOT READY verdict with a dated fix list. Read-only.
---

# BFCM Offer & Discount Config QA

The costly BFCM discount mistakes are rarely in the offer plan. They're in the settings: an automatic sale that applies to *All products* and takes another 25% off clearance; a welcome code allowed to combine with product discounts while the sale is allowed to combine with order discounts, so the two stack; a creator code with no usage limit that leaks on Black Friday; a free-shipping discount whose $75 minimum was deleted; an early-access code whose end time was entered in UTC and expires at 6:59 pm instead of midnight; an old summer code someone reactivated. Each one looks fine on the Discounts page and shows up only in orders.

This skill audits the live discount setup against the plan before launch, from local exports. It models how Shopify combines discounts (each discount must allow the other's class; product discounts first, then order discounts on the reduced subtotal; one product discount per item; the customer gets the best valid combination) to find the deepest price any SKU can reach. Then it writes the test carts that prove each fix: **plan vs config → windows in store time → who gets each discount → stacking and worst-case price → codes and free shipping → reference prices → test checkouts.**

It sits next to other library skills and does not repeat them:
- **Promo Margin Guard** works out what a promotion costs and the deepest discount each SKU can carry. Use it to set this plan's max discount and margin floor; this skill checks that the configuration enforces them.
- **Product Feed Auditor** checks sale price and price coherence in the Google feed. This skill checks the store's own compare-at reference prices.
- **BFCM Live Pulse** watches code usage once the sale is live, which is where a leaked code shows up. **BFCM Holiday Budget Planner** plans spend at the planned discount depth.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In type.com, read them with the skill-file tools. Anywhere else, the type.com Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `bfcm-offer-config-qa`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite.

## When to use this

- Two to six weeks before BFCM (or any large sale), once the discounts are built in Shopify. Again after every discount change, and a final time the day before launch.
- Someone asks "are our Black Friday discounts set up right?", "will WELCOME10 stack with the sale?", "why did a code work on clearance?" or "what's the lowest price a customer can get?".
- After discounts were created or edited by an app, an import or an API script. Most time-zone slips come from there.
- An agency QA-ing each client's discounts before launch.
- On a schedule: weekly from mid-October, daily in launch week. Stay silent when the verdict is READY and nothing changed.

## Operating rules

1. **Read-only.** The scripts read local files and never call Shopify. Never create, edit, deactivate or delete a discount, or change a price, unless the user approves that specific change. When they do, make it, read the setting back, re-export, re-run this check, and run the matching test checkout.
2. **The plan is the reference.** When the plan and the setup disagree, report it; never edit the plan to match the setup. If the plan contradicts itself (it allows a stack deeper than its own max), say so and ask which one is right.
3. **Worst case, not typical case.** Report the lowest price a customer can reach. Shopify always gives the customer the best valid combination, so assume someone will find it.
4. **Label estimates and risks.** The free-shipping cost is an estimate from last year's numbers. Reference-price notes are risks to review, not legal conclusions.
5. **Quote exact numbers**: codes, X of Y SKUs, prices to the cent, times in store time (with UTC where the slip matters).
6. **A setting isn't verified until a checkout shows it.** Apps, Scripts and rollouts can change what checkout does. Mark a fix verified only when its test cart shows the expected total.
7. Say which state each item is in: **detected**, **recommended**, **applied**, or **verified**.

## Gathering the inputs

Three files are required and one is optional. Exact formats are in `DATA_CONTRACT.md`.

1. **Offer plan** (`offer_plan.json`). Write it with whoever owns the promotion calendar: store time zone, the maximum discount any item may get, a margin floor, and each intended offer (code or automatic title, type, *Amount off products* or *Amount off order*, value, store-time start and end, eligible collections and exclusions, minimum purchase, customer eligibility such as first order or a segment, usage limit, once per customer, and which discount classes it may combine with). Add the codes that must not be active, and last BFCM's order counts and your shipping cost per order for the free-shipping estimate.
2. **Live discounts** (`discounts.json`). Run the GraphQL query in `DATA_CONTRACT.md` (also printed by `python3 scripts/normalize_shopify_discounts.py --print-query`) with read-only `read_discounts` access, save each page, and normalize it. In type.com, use the connected Shopify integration and write the responses to files. Never ask the user to paste an API token into chat. Without API access, build the file by hand from the Discounts page.
3. **Catalog** (`products.csv`). One row per variant: handle, SKU, price, compare-at, unit cost, collections, inventory and units sold in the last 90 days. The product CSV export has no collections column, so use the products query in `DATA_CONTRACT.md` or add them.
4. **Price history** (`price_history.csv`, optional): SKU, date, price and compare-at for at least the last 90 days. Without it, compare-at raises are not checked.

## Running it

```bash
python3 scripts/normalize_shopify_discounts.py --in page1.json [--in page2.json] \
  --exported-at 2026-10-06T14:05:00-04:00 --shop "Store name" --out discounts.json
python3 scripts/offer_qa.py --plan offer_plan.json --discounts discounts.json --products products.csv \
  [--price-history price_history.csv] [--as-of YYYY-MM-DD] [--margin-floor PCT] [--json analysis.json]
```

`bash examples/run.sh` runs it on the bundled sample store (Fernhill Goods, fictional): six live discounts checked on Oct 6, 2026, against a five-offer plan.

What it does:

1. **Plan vs config.** Matches every planned offer to its live discount by code or title, and compares type, value, class, minimum, customer eligibility, usage limit and once-per-customer. Flags planned offers that are missing, live discounts that aren't in the plan, and must-not-be-active codes that are still active or scheduled.
2. **Windows in store time.** Converts Shopify's UTC times to the store's time zone, names a slip when a time matches the plan in UTC or another time zone (for example "23:59 UTC"), and prints a timeline of the phases with any gap or overlap the plan doesn't have.
3. **Who gets each discount.** Resolves each product discount's target to SKUs and compares it with the plan's eligible set. "All products" that pulls in clearance or new arrivals is critical. Gift cards are only discounted when the gift card product is named. App and multi-class discounts don't expose their targeting through the API, so they get an INFO line to check by hand; a target list the export cut off at 50 is skipped and listed under Not checked.
4. **Stacking and worst-case price.** Lists every pair of discounts that can combine and computes, for every SKU and every stretch of the sale calendar, the lowest price any valid combination reaches. A fixed amount split across items with a minimum is shared by a cart at that minimum. App, multi-class and fixed-amount buy X get Y discounts aren't modeled and are listed as "check manually". Flags depth past the plan's max, prices below unit cost and margins under the floor, with the planned worst case alongside.
5. **Codes, limits and free shipping.** Missing or higher usage limits, codes that aren't once per customer when planned, creator codes that combine with the sale, guessable WORD+NUMBER codes open to everyone, and free shipping below the planned minimum, with a labeled cost estimate.
6. **Reference prices.** Compare-at below the price, compare-at raises the SKU never actually sold at for 30 of the 90 days before, and compare-ats never seen as a selling price in the history (a review trigger based on the FTC's Guides Against Deceptive Pricing, 16 CFR 233.1, not a legal test). This check follows US (FTC 16 CFR 233.1) guidance. It does not apply the EU/UK rule that the reference price must be the lowest price in the prior 30 days.
7. **Test-checkout script.** Concrete carts with a time, a customer and a code, the total to expect if the setup matches the plan, and the total that shows the bug.
8. **Verdict and dated fix list.** `NOT READY` (any critical), `FIX BEFORE LAUNCH` (warnings only) or `READY`. Fixes are due 14 days before launch; anything costing money today is due today.

Exit codes: 0 means the report was written, 2 means invalid input (with the file, line or entry, and the reason). The normalizer also exits 2 when the API response has errors or more pages to fetch.

## Acting on the report

Present the verdict and the ranked issues, then walk through the fixes in this order. Each one needs the user's approval. Exact admin paths, the safe test method and templates are in `references/playbook.md`.

1. **Anything live today.** Free shipping below its minimum and codes that must not be active cost money every hour. Set the minimum; deactivate the code (reactivating it later clears its end date, so set one).
2. **Who gets the sale.** Point the sale's *Applies to* at a collection that holds only eligible products (the sample uses a "BFCM Eligible" collection). Never "All products" when anything is excluded.
3. **Combinations.** Untick the classes the plan forbids, on both discounts in each pair. Either side stops the stack; both sides survive a later edit.
4. **Code limits.** Usage caps and one use per customer on creator and VIP codes; one code per creator; end dates on everything.
5. **Windows.** Set times in the admin, which uses the store's time zone. For apps and API scripts, send UTC with `Z`, and remember US daylight time ends Nov 1, 2026 (ET is UTC−5 from then on).
6. **Reference prices.** Put a raised compare-at back or document that it was a real former price, and get a pricing-compliance review.
7. **Prove it.** Re-export, re-run until READY, run the test checkouts and mark items verified. Freeze discounts 3 days out; final run the day before; one real order in each phase as it opens.

## Files

- `scripts/offer_qa.py`: the analyzer. Local files only, deterministic output, optional `--json`.
- `scripts/normalize_shopify_discounts.py`: turns Admin GraphQL `discountNodes` pages into `discounts.json`; `--print-query` prints the query.
- `DATA_CONTRACT.md`: the four input formats, the GraphQL query and API-version notes, and every rule and threshold, with which Shopify behaviors are confirmed from Shopify's documentation and which are assumptions.
- `references/playbook.md`: Shopify admin paths for each fix, how to test scheduled discounts safely, creator and VIP code hygiene, time zones and DST, reference-price practice, and a status-update template.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`: sample run, including the raw GraphQL response the sample `discounts.json` was normalized from. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`, `tests/make_cases.py`: 23 end-to-end checks on hand-checkable inputs, including Shopify's own combination examples. No network needed.
