# Data contract — review-insight-miner

One input file. No network access, no credentials in the script, no clock read.

## File: feedback (`--feedback`, required)

- Format: CSV, UTF-8, header row, **one row per document** — one review, or
  one support conversation.
- Encoding note: quote any field containing a comma or a newline. Collapse
  internal newlines; the script normalises whitespace but a raw newline must
  still sit inside CSV quotes.
- Language: one language per run.
- Volume: works from roughly 60 documents. Below that, co-occurrence is too
  sparse for themes to separate and the output should be read as suggestive
  only.

### Required columns

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `id` | string | — | Unique per row. Used to cite verbatims back to the source. A duplicate exits 2. |
| `source` | enum | `review` or `ticket` | Anything else exits 2 naming the row. Case insensitive. |
| `text` | string | free text | Required and non-empty. For tickets use the **first customer message only**, not the agent thread. |

### Optional columns

| column | type | units / format | notes |
| --- | --- | --- | --- |
| `rating` | integer | 1 to 5 | Star rating. Required for a document to contribute to mean rating and rating drag. Blank for tickets. A value outside 1–5 exits 2. |
| `sku` | string | — | Enables the per-theme SKU breakdown. Use the same SKU convention as the catalogue. |
| `date` | string | `YYYY-MM-DD` | Carried for reference only. No date filtering is performed — window the export before running. |

Any other column is ignored.

```csv
id,date,source,sku,rating,text
NF-RV-001,2026-08-02,review,NF-LID-STR,1,"The straw lid leaks every time I put the bottle in my bag."
NF-TK-007,2026-08-15,ticket,NF-LID-STR,,"Straw lid leaking from the gasket, requesting a replacement lid."
```

## Upstream systems

| data | where it comes from |
| --- | --- |
| reviews | Judge.me, Okendo, Yotpo, Stamped, Shopify product reviews, marketplace review export |
| tickets | Gorgias, Zendesk, Front, Intercom, Help Scout — export the conversation's first customer message |
| return reasons | Loop, Returnly, AfterShip; load free-text reasons as `ticket` rows |
| post-purchase survey | KnoCommerce, Fairing; load substantive free-text answers as `ticket` rows |

## Preparation rules that change the result

1. **Strip templated boilerplate** from ticket exports. Text such as
   "Customer reports…", "Hi team", agent signatures, and auto-appended order
   summaries cluster tickets with each other instead of by subject. The
   commonest support scaffolding is already in the stop list; export-specific
   templates are not.
2. **One message per row.** A whole ticket thread pulls agent vocabulary into
   the customer's theme.
3. **Window the export** to the period you want to reason about.
4. **Keep the SKU** if you intend to act on listing copy; without it the
   themes cannot be pointed at a product page.

## Method and parameters

| step | definition | flag |
| --- | --- | --- |
| tokenise | lowercase, words only, conservative suffix stemming (plurals, `-ing`, `-ed`) | — |
| vocabulary | terms in at least N documents and at most a percentage of them | `--min-term-docs` (3), `--max-term-df-pct` (35) |
| association | `PMI(a,b) = log2( P(a,b) / (P(a)P(b)) )` over document co-occurrence, rounded to 4 decimals | `--min-pair-docs` (2) |
| clustering | greedy average-linkage agglomeration, merging while average PMI is at or above the threshold | `--min-pmi` (1.0 bits) |
| assignment | each document joins the cluster with the highest sum of inverse document frequency over shared terms; zero overlap means no theme | — |
| reporting threshold | clusters below this document count are folded into "no recurring theme" | `--min-theme-docs` (3) |
| category | signal lexicon counted over the theme's documents **after** clustering | — |
| rating drag | `(overall mean rating − theme mean rating) × rated reviews in the theme`, in star-points | — |

Categories: `PRODUCT DEFECT`, `EXPECTATION MISMATCH`, `LOGISTICS`, `SERVICE`,
`USABILITY`, `VALUE`, `UNCLASSIFIED`.

## Determinism

Every ordering is by an explicit sort key, never by dictionary insertion
order. The only floating-point values are PMI and inverse document frequency,
both rounded to four decimal places before any comparison, so the same input
file produces byte-identical output.
