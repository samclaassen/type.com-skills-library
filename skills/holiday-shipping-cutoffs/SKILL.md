---
name: holiday-shipping-cutoffs
description: Work out the last day customers can order on each shipping service and still get their gift by the holiday, for a stated share of the store's customers. Models when orders leave the warehouse (cutoff time, weekends, closures, BFCM backlog), checks every delivery promise on the site, at checkout and in email or SMS against those dates, and gives a dated messaging calendar (expedited only, then e-gift cards) and fix list. Read-only.
---

# Holiday Shipping Cutoff Checker

Many stores tell customers "Order by Dec 18 for Christmas delivery" because that is what the banner said last year. The real last safe day is different for every shipping service, and it depends on four things you can measure: when orders actually leave your warehouse (cutoff time, weekends, closures and the BFCM backlog), where your customers live (the carrier zone mix), how long each service takes to each zone in December, and the carrier's own holiday ship-by date. Get it wrong one way and gifts arrive late, followed by refund requests and "where is my order" tickets. Get it wrong the other way and you stop selling days before you need to.

This skill works the date out for each service from local files, checks every delivery promise you make against it, and plans the switches: **last safe date per service → promises fixed → BFCM backlog explained → expedited only → e-gift cards.**

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In type.com, read them with the skill-file tools. Anywhere else, the type.com Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `holiday-shipping-cutoffs`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite.

## When to use this

- October to early November, before holiday delivery messaging goes live, on a store that ships its own orders (or uses a 3PL whose cutoff and daily capacity you know). Or on each store an agency manages.
- Someone asks "what's our last day to order for Christmas?", "is 'order by Dec 18' still right?", "when do we switch to expedited only?" or "will Black Friday orders ship on time?".
- Again when the carriers publish their holiday dates, after BFCM with actual orders, and the week before cutoff messaging starts. Stay silent when the verdict is READY and nothing changed.
- Other gifting dates work the same way: set `--target` (Hanukkah, Valentine's Day, Mother's Day).

## Operating rules

1. **Read-only.** The script reads local files only. Never change a banner, checkout rate, email, SMS, help-desk macro, ShipStation rule or carrier setting unless the user approves that specific change. When they do, make it, read it back (view the live page or send a test), and re-run.
2. **Never present a date as carrier-published unless the user supplied it with a source.** Carrier ship-by dates go in carriers.csv with the page URL in `source`. Don't fill in carrier dates from memory or from last year. If the user doesn't have them yet, run on transit days alone and say so. The sample's carrier data is fictional and labeled `sample`.
3. **Estimates, not guarantees.** "95% of customers" means 1 in 20 may still get it late. Suggest dated, plain copy; never suggest the word "guaranteed" unless the business offers a remedy when it's late. The FTC note in the report is general information, not legal advice; send guarantee wording to whoever reviews legal copy.
4. **Quote exact numbers**: the date with its weekday, the cutoff time and time zone, the coverage percentage, business days to ship and the day the backlog clears.
5. **Keep exports local.** Order exports with addresses stay on the user's machine. Only zone counts go into destinations.csv.
6. Say which state each item is in: **detected**, **recommended**, **applied**, or **verified**.

## Gathering the inputs

Exact columns, units and sources are in `DATA_CONTRACT.md`. Most stores can build all five files in about an hour.

1. **carriers.csv, required.** One row per service with its transit business days to zones 1–8 (and optionally zone 9, US territories) from your warehouse (a single number or a range like `3-5`), the days it delivers, and its non-delivery dates. Add `peak_extra_days` for the December slowdown you expect (your allowance, not carrier data). And/or a row with the carrier's own holiday **ship-by date** and its URL. Where carriers publish these is in `references/playbook.md` §1. Every row needs a `source`.
2. **warehouse.json, required.** Time zone (required: customer copy shows the cutoff with its zone), order cutoff time, the share of orders that arrive after the cutoff, handling days, working days, closed dates, daily pick/pack capacity and any temporary peak capacity.
3. **destinations.csv, required.** Orders by carrier zone, ideally last November–December. ShipStation's shipment export includes the zone; or count a Shopify orders export by ZIP against the USPS zone chart for your origin ZIP.
4. **demand.csv, optional.** Projected orders per day from Nov 1 through the holiday: the BFCM Holiday Budget Planner's `--csv` daily plan (pass `--aov`), last year's orders by day shifted to this year's calendar, or a manual forecast. Without it, the backlog isn't modeled.
   - **Planner handoff:** the planner CSV has net sales per day, not orders, so `--aov` is required (the run exits 2 without it). Use the same AOV the planner used (88.66 in the planner's sample, which its report rounds to "AOV $89"); a different AOV gives different order counts, so the backlog and ship dates won't line up with the plan.
5. **promises.csv, optional.** Every place a delivery date or shipping time appears: site banner, product pages, cart, checkout rate names, shipping FAQ, emails and SMS (with send dates), help-desk macros. Without it, promises aren't checked.

If the user has connected Shopify, ShipStation or Klaviyo integrations, you may pull the same data read-only and write it into these files. Never ask the user to paste API keys or tokens into chat.

## Running it

```bash
python3 scripts/check_cutoffs.py --carriers carriers.csv --warehouse warehouse.json \
  --destinations destinations.csv [--demand demand.csv [--aov 85]] [--promises promises.csv] \
  [--account "Store"] [--target 2026-12-24] [--threshold 95] [--as-of YYYY-MM-DD] [--json result.json]
```

`bash examples/run.sh` runs it on the bundled sample store (Fernhill Goods, fictional). `--target` defaults to Dec 24 and `--threshold` to 95 (% of customers). `--as-of` defaults to today; pass it for a reproducible report.

What it does:

1. **Ship dates.** Orders placed by the cutoff on a working day ship that day (plus any handling days); later orders, weekend orders and orders on closed days count from the next working day. Orders beyond daily capacity wait in a first-in, first-out queue. The report shows the backlog by day, business days to ship for each order date, the worst extra delay and the day it clears, with a one-line summary for BFCM (Thanksgiving to Cyber Monday).
2. **Coverage.** For each order date and service: from the ship date, count the carrier's delivery days to the target (skipping its non-delivery days) and weight each zone by your zone mix. A range like 3–5 days is spread evenly across its days. Each order date uses the latest ship date of that day's orders (the last ones out of the backlog), so coverage is conservative.
3. **Last safe order date.** The last order date (by the cutoff) with coverage at or above the threshold, also shown at 80% and 99%. A carrier ship-by date overrides a later transit-based date. A checkout rate that uses several carriers gets the earliest date, with a routing option to extend it.
4. **Promises.** RISKY: an order-by date later than the last safe date, a message sent after it, or a stated time later than the cutoff on the last safe day (times like "11:59 pm PT" or "5 pm ET" are converted to the warehouse zone; US standard time). WARNING: a holiday promise with no date (with the FTC Mail, Internet, or Telephone Order Merchandise Rule note), the last safe date without the cutoff time, or a shipping time the backlog breaks. INFO: a date 3+ days earlier than needed.
5. **Messaging calendar and fix list.** Dated switches with plain copy (backlog note, cutoff messaging, last-day reminders, expedited only, e-gift cards, closure notice), and a verdict: `NOT READY` (any RISKY), `FIX BEFORE PEAK` (warnings only) or `READY`.

If every last safe date has already passed, the verdict line says so ("all cutoffs have passed") rather than "Nothing to fix". A BFCM window with no forecast orders is reported as such, including when an earlier backlog runs through it.

Exit codes: 0 means the report was written, 2 means invalid input (with the file, line and reason), including a carrier `ship_by_date` after the target date or a warehouse file with no `timezone`.

`--json` output: additive fields in this version are `all_cutoffs_passed` (top level) and `backlog.bfcm_orders_forecast` (false when no orders are forecast in the BFCM window; `bfcm_ship_within_business_days` is then null). `zone_mix` has keys `1`–`8`, plus `9` only when orders go to zone 9. Nothing was renamed or removed.

## Acting on the report

Present the verdict and the last safe dates, then walk through the fixes in this order. Each needs the user's approval. Copy templates and UI paths are in `references/playbook.md`.

1. **RISKY promises first.** Replace each late date with the per-service copy from the fix list, including the cutoff time. Check every placement the same text lives in (banner, announcement bar, product templates, help-desk macros).
2. **Unqualified promises.** Replace "guaranteed by Christmas" and similar lines with dated, per-method copy. Explain the FTC note plainly and suggest legal review of any guarantee.
3. **Backlog.** Add the dated shipping-time note to the FAQ, cart and order confirmation before the first affected day. If the backlog is longer than the business can accept, show the capacity option (temporary staff, a weekend shift) next to the messaging option.
4. **Conservative dates.** Move them later to the last safe date; this is free revenue.
5. **Calendar.** Schedule every switch ahead of time (banner, cart, checkout rate names, email and SMS sends), including the e-gift card switch and the closure notice.
6. **Re-run.** When carriers publish their holiday dates, after BFCM with actual orders, and before cutoff messaging starts. Mark items **verified** only when a later run (or a live page check) shows them fixed.

Related library skills: BFCM Holiday Budget Planner feeds `demand.csv` (with `--aov` set to the planner's AOV); Holiday Support Macros should use the same dates; Inventory Stockout Risk covers whether the stock is there to ship.

## Files

- `scripts/check_cutoffs.py`: the analyzer. Local files only, deterministic output, optional `--json`.
- `DATA_CONTRACT.md`: the five input formats, where each column comes from, every rule and threshold, and the JSON output.
- `references/playbook.md`: where carriers publish holiday dates and transit times, building the zone mix, measuring cutoff share and capacity, the FTC rule in plain terms, copy templates, Shopify, ShipStation and Klaviyo steps, and a status-update template.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`: sample run. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`, `tests/make_cases.py`: 19 end-to-end checks on hand-checkable inputs. No network needed.
