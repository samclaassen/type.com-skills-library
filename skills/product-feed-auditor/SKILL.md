---
name: product-feed-auditor
description: Audit a Google Merchant Center or Shopping product feed export offline for attribute coverage, title and description policy violations, malformed image and landing-page URLs, price and sale-price coherence, and duplicate ids, returning findings by severity with the exact item ids and the disapproval risk each carries.
---

# Product Feed Auditor

Catch the item-level problems that get products disapproved *before* the next
feed fetch. Merchant Center tells you an item is disapproved after it has
already stopped serving; this runs the same class of checks against a local
export in seconds, and names the exact item ids to fix.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `product-feed-auditor`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- A Merchant Center account has disapproved items and the owner wants the list
  grouped by cause, not by item.
- Before a bulk catalogue change ships: a new supplier drop, a re-platform, a
  price file import, a sitewide sale that writes `sale_price` across the feed.
- Recurring hygiene, for example weekly, to catch feed drift (placeholder
  images from a failed asset sync, zero prices from a missing cost column).
- When Shopping impressions fall and nobody has changed bids.

Do not use it to explain *performance* (ROAS, impression share). It audits
feed data quality only.

## Gathering the input

The script never touches the network. Export the feed first, then hand the
script a local file. Any of these produce an acceptable export:

- **Merchant Center**: Products → All products → download, or Feeds → the
  primary feed → download the processed feed. Export as CSV or TSV.
- **Shopify** (via the connected Shopify integration): pull products with
  variants and map them into the Merchant Center column names below. The
  Google & YouTube channel's product feed export already uses those names.
- **A feed vendor** (Feedonomics, DataFeedWatch, Channable): export the
  *output* feed, the one that actually gets submitted, not the source catalogue.
- **A raw feed URL** the owner already has: download it to a local file first.

`DATA_CONTRACT.md` lists the required and optional columns. Required columns
are `id`, `title`, `description`, `link`, `image_link`, `availability`,
`price`, `brand`, `condition`. If a column is genuinely absent from the
platform's export, add it as an empty column rather than dropping it — the
coverage table then reports it honestly as 0% covered.

Also ask the owner for the **claimed store domain** (for example
`shop.harborloom.com`). Without it the landing-page domain check is skipped.

## Running it

```bash
python3 scripts/audit_feed.py \
  --feed /path/to/feed.csv \
  --as-of 2026-09-15 \
  --domain shop.harborloom.com
```

- `--as-of YYYY-MM-DD` is required. It is the only notion of "today" the
  script has, and it is used to decide whether a `sale_price_effective_date`
  window has already closed. Use the date the export was taken.
- `--delimiter '\t'` for a TSV export.
- `--max-ids N` controls how many item ids print per finding (default 8).
- `--json` emits the same analysis as JSON for downstream automation.
- `--fail-on critical` makes the process exit 1 when any critical finding
  exists, which is useful in a pre-submit check.

Structural problems with the file (missing required column, empty file,
unreadable `--as-of`) exit 2 with a message naming the offending column. Bad
values *inside* the feed are the point of the tool and are reported as
findings.

## Reading and presenting the output

Three severities, and they mean different things:

- **critical** — Merchant Center will disapprove or drop the item. Fix before
  the next fetch. Every critical finding names the disapproval reason it maps
  to.
- **warning** — the item serves, but with degraded reach, wasted margin, or a
  data error that will bite later (reused GTIN, 2% "sale", expired sale
  window).
- **info** — merchandising advice, not a policy problem.

When reporting to a human:

1. Lead with the summary line: how many items carry at least one critical
   finding, as a share of the feed's unique item ids. That is the number that
   maps to lost revenue. Summary counts are per unique id: a duplicated id is
   one item, and a pair finding (reused GTIN, case collision) counts every id
   in the pair. The coverage table is per row, so its denominator can differ.
2. Then list critical findings by item count, largest first — a single bad
   template usually explains most of them. Twelve items with
   `IMG-PLACEHOLDER` is one broken asset sync, not twelve problems.
3. Give the exact item ids for the top findings so someone can fix them
   without rerunning anything.
4. Translate each fix into feed work: which system owns the column
   (`brand` comes from the PIM, `image_link` from the CDN pipeline,
   `sale_price` from the promo file), so the fix lands upstream and does not
   regress on the next sync.

## Limits

State these plainly; do not overclaim.

- **No network access.** Image and landing-page URLs are validated by
  structure and pattern only — scheme, host, unencoded characters, extension,
  placeholder naming, non-public host. The script cannot tell you an image is
  a 404, is below Google's minimum pixel size, has a promotional overlay, or
  that a landing page 500s or shows a different price than the feed. Those
  require a crawl.
- **Not a policy oracle.** It encodes the mechanical, checkable parts of the
  product data specification. It cannot judge restricted or prohibited
  products, misrepresentation, trademark issues, or whether a title is
  *accurate*.
- **No Merchant Center account state.** It cannot see existing disapprovals,
  account-level suspensions, shipping or tax configuration, or feed rules
  applied after upload. Reconcile with the Diagnostics tab.
- **Single-feed, single-country.** Multi-country supplemental feeds, regional
  price overrides, and `[country]` column suffixes are not modelled. Audit
  each output feed separately.
- **Heuristic text checks.** Capitalisation, promotional wording, and
  keyword-repetition checks use thresholds documented in the script header.
  They will occasionally flag a legitimate title (a brand genuinely named
  "SALE") — confirm before mass-editing.
- It does not edit or re-upload anything. Output is a report.
