---
name: cohort-ltv-analyzer
description: Build monthly acquisition cohorts from an order export and report repeat rate, cumulative revenue and contribution-margin LTV per customer by month since first order, CAC payback per cohort, and which cohorts are tracking below a reference cohort at the same age.
---

# Cohort LTV Analyzer

Answers the question a blended LTV number cannot: *are the customers we are
buying this quarter worth what the customers we bought in January were worth,
at the same age?*

It assigns every customer to the month of their first order, then measures
each cohort at equal ages (M0, M1, M2 …) so a three-month-old cohort is never
unfairly compared with an eight-month-old one.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `cohort-ltv-analyzer`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- CAC is rising and you need to know whether LTV is rising with it.
- A finance or board question about payback period on paid acquisition.
- After a channel shift (a discount-heavy campaign, a new marketplace, an
  influencer push) to see whether the new customers repeat like the old ones.
- Before raising acquisition budget: which cohorts pay back, and by when.
- Subscription and replenishment brands checking whether retention is decaying.

## Gathering the input

Two local files, plus finance's definitions file if the business has one. The
script never touches the network.

**0. Finance's revenue definitions** (`finance/revenue_definitions.json`,
optional). Finance owns this file; see `REVENUE_DEFINITIONS.md`. When it is in
the working folder (or passed with `--definitions`), the skill takes the
reporting timezone, reporting currency and FX rates file, net sales flags,
refund date basis and excluded-order rules from it and prints its version at
the top of the report. When it is absent, the first lines of the output say so
and list the skill's own defaults; the numbers are exactly what earlier
versions produced. Ask whether finance has this file before running, and never
write one on finance's behalf.

**1. Order export** (required). From the user's connected integrations:

- **Shopify**: Orders export, or a report with one row per order. Use *net*
  product revenue (after discounts, before tax and shipping). Without a
  definitions file, exclude test and cancelled orders yourself. With one, keep
  them and include the `tags`, `financial_status` and `cancelled` columns so
  the skill excludes them under finance's rules and reports what it removed.
  Keep the `Created at` timestamp with its UTC offset and, if refunds should
  land when they were issued, a `refund_date` per refunded order.
- **A data warehouse / GA4 BigQuery export / an ERP**: one row per order with
  the columns in `DATA_CONTRACT.md`.
- **A 3PL or ERP** can supply `cogs`, `shipping_cost`, and fulfillment cost
  per order if the storefront export lacks them.

The critical field is `customer_id` and it must be **stable across orders**.
If guest checkouts create a new id per order, every customer looks
single-purchase and the whole analysis is wrong — stitch on hashed email
first and say so in the writeup.

**2. Marketing spend by month** (optional but strongly recommended). Columns
`cohort_month,marketing_spend`. Pull acquisition spend per calendar month from
the ad platforms (Meta, Google Ads), Klaviyo/paid email if it is an
acquisition channel, and any agency retainer allocated to acquisition. CAC per
cohort is then `spend in that month / new customers acquired in that month`.
If spend is unavailable, pass a single blended `--cac`.

## Running it

```bash
python3 scripts/cohort_ltv.py \
  --orders /path/to/orders.csv \
  --spend /path/to/spend.csv \
  --as-of 2026-09-01 \
  --horizon 3 \
  --fulfillment-cost 3.25
```

Key arguments:

- `--as-of YYYY-MM-DD` (required) is the only "today". A cohort month index is
  only shown once that month has fully elapsed as of this date, so partial
  months never masquerade as complete ones.
- `--horizon N` is the cohort age at which cohorts are compared. Default 3
  (a 90-day payback convention). Use 6 or 12 for considered-purchase brands.
- `--cogs-pct N` is required only when the export has no `cogs` column.
- `--payment-fee-pct` / `--payment-fee-fixed` default to 2.9% + 0.30, the
  common US card rate. Override for a different processor, or supply a
  `payment_fee` column and the flags are ignored.
- `--restock-recovery-pct` defaults to 0: refunded goods are assumed
  unsellable. Set it to the real resale rate if returns go back into stock.
- `--reference-cohort YYYY-MM` overrides the default reference (the oldest
  mature cohort).
- `--tolerance-pct` (default 10) is how far below the reference a cohort must
  fall before it is flagged.
- `--definitions PATH` points at finance's revenue definitions. Default:
  `finance/revenue_definitions.json` in the working folder, used if it exists.
  An explicit path that does not exist is an error.
- `--json` emits the same analysis, including both triangles, as JSON, with
  `definitions` and `excludedOrders` keys first.

Bad input exits 2 naming the file, row, and column.

## Reading and presenting the output

First comes either **FINANCE DEFINITIONS** (file, version, owner, and how each
rule was applied or why it could not be) or a **NOTE** that no definitions file
was found, listing the defaults in use. Quote the version in the writeup; if it
is the NOTE, say the figures are not on finance's definitions.

Then five blocks, in this order (plus **Excluded orders** after the
assumptions when a definitions file is in use: count and net_revenue per
rule, and any rule that could not be checked because its column is missing):

1. **Assumptions** — every margin input, printed. Read it first; if the COGS
   or fee assumption is wrong, every number below is wrong. Put this in the
   writeup too, so nobody re-litigates the math later.
2. **Cohort summary** — customers, orders, AOV, repeat rate, revenue per
   customer, contribution margin per customer, CAC, and payback month. `obs`
   is the cohort's last fully elapsed month; rows marked `(immature)` have not
   reached the horizon and are excluded from comparison. A payback of
   `M0, lost M2` means the cohort reached CAC in M0 but later refunds (for
   example under a refund-date basis) or loss-making orders pulled it back
   below, and it is still below at its last observed month.
3. **Cumulative revenue per customer triangle** — the top line. Rising from
   left to right within a row is repeat purchasing.
4. **Cumulative contribution margin per customer triangle** — the same shape
   after product, shipping, fee, and refund costs. This is the number CAC has
   to be compared against, not revenue.
5. **Payback against CAC**, then **cohorts trending below reference**.

How to present it:

- Lead with payback: "January and February cohorts pay back in month 0; the
  June onward cohorts have not paid back and are at CAC $41–49 against $13 of
  contribution margin so far."
- Distinguish a **mix problem** from a **retention problem**. If a recent
  cohort's M0 margin per customer is down, the first order got smaller or more
  discounted. If M0 holds but M1–M3 flatten, customers stopped coming back.
  The triangle tells you which; say which.
- Immature cohorts are early signal, not a verdict. Say "M0 margin is down
  38% versus the reference cohort" rather than "the June cohort is
  unprofitable" when June has only lived one month.
- Convert to a decision: pause or reprice the channel that produced the weak
  cohort, or extend the acceptable payback window if cash allows.

## Limits

- **Contribution margin, not net profit.** Overheads, salaries, software,
  warehousing rent, and agency fees are not modelled. A cohort that "pays
  back" covers its own variable costs and its acquisition cost, nothing more.
- **CAC is blended per month**, spend divided by new customers acquired that
  month. It does not deduplicate organic from paid, does not model view-through
  or lagged attribution, and assigns none of the spend to repeat purchases of
  earlier cohorts. In a brand with heavy organic acquisition, real paid CAC is
  higher than this figure.
- **No forecasting.** There is no curve fitting, no projected LTV, no survival
  model. Every cell is observed history. A cohort with no future months shown
  has no future months of data, not zero future value.
- **Calendar months only.** Not weekly cohorts, not 30-day windows, not
  first-touch-channel cohorts. A customer's cohort is the month of their first
  order in the file — if the export does not go back far enough, genuinely old
  customers get mis-assigned to the first month of the file. Check the earliest
  order date before trusting the oldest cohort.
- **One currency.** Without a definitions file, convert multi-currency exports
  before running. With one, rows in another currency are converted at the
  order date's rate from finance's rates file (the whole row, refund included:
  FX movement between sale and refund is not modelled). A missing rate stops
  the run naming the row. Spend, `--cac` and the cost flags must already be in
  the reporting currency.
- **Refund timing follows finance.** Without a definitions file (or with
  `refunds.date_basis: order_date`), refunds are netted in the month of the
  original order. With `refund_date`, the refund and its cost recovery land in
  the month-since-first-order bucket of the refund date, so cumulative figures
  can fall in later months. `refunds.late_refund_days` is not used.
- **Timezones.** Without a reporting timezone, `order_date` is read as its
  first 10 characters. With one, timestamps must carry a UTC offset; a
  timestamp without one stops the run rather than being guessed.
- **Net sales flags change the revenue figures only.** Contribution margin is
  always net of discounts and refunds and includes shipping charged. Tax and
  gift-card flags cannot be applied (no such columns); the report says so.
- **Excluded orders** are only as good as the `tags`, `financial_status` and
  `cancelled` columns. A rule whose column is missing is reported as not
  checked, and those orders must still be pre-filtered. An excluded order
  never makes its customer a customer: if a customer's only orders are
  excluded, they are in no cohort.
- It cannot see subscriptions, exchanges, gift cards, or store credit as
  distinct events; they are only whatever the order export says they are.
