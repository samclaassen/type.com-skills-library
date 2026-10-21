---
name: review-insight-miner
description: Cluster product reviews and support tickets into recurring themes by keyword co-occurrence, separate product defects from expectation mismatches, rank each theme by volume and the star rating it drags down, and attach a specific listing-copy or product fix with owner and verbatim evidence.
---

# Review Insight Miner

Turns a pile of reviews and tickets into a ranked, evidenced list of the
things that are actually costing stars and generating support volume — and
splits them into *the product is failing* versus *the page is promising the
wrong thing*, because those have different owners and very different costs to
fix.

Themes are discovered from the text, not from a fixed list of categories:
terms that keep appearing together are merged by pointwise mutual information,
and only then is each cluster labelled.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `review-insight-miner`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- The rating on a hero SKU has slipped and nobody can say precisely why.
- Support volume is rising and you want the top drivers, ranked, with evidence.
- Before a product revision or a resupply: which failure modes are worth
  tooling changes.
- Before a listing rewrite: which promises the page is making that the product
  does not keep.
- Quarterly voice-of-customer review, or a category-level read across SKUs.

## Gathering the input

One CSV, assembled from the user's connected integrations. The script never
touches the network.

- **Reviews**: Shopify product reviews, Judge.me, Okendo, Yotpo, Stamped, or a
  marketplace review export. Keep the star rating and the SKU.
- **Support tickets**: Gorgias, Zendesk, Front, Intercom, Help Scout. Use the
  **first customer message**, not the whole thread — agent replies and
  signatures swamp the vocabulary. One row per conversation.
- **Post-purchase surveys or return reasons** also work as `ticket` rows if the
  free-text field is substantive.

Practical points that change the result:

- **Strip boilerplate.** Templated openings ("Customer reports…", "Hi team")
  cluster tickets with each other rather than by subject. Common support
  scaffolding is already in the stop list, but export-specific templates are
  not; remove them before running.
- **Window it.** Mine a defined period (a quarter, a product generation, since
  a supplier change). A five-year export buries a current defect under
  historical noise.
- **Volume.** Below roughly 60 documents the co-occurrence statistics are thin
  and themes will not separate cleanly; say so rather than over-reading a
  three-document cluster.
- **One language.** Mixed-language exports fragment every theme. Filter or run
  each language separately.

## Running it

```bash
python3 scripts/mine_reviews.py --feedback /path/to/feedback.csv --top 6
```

Tuning:

- `--min-term-docs` (3) is how many documents a word must appear in to be
  eligible. Raise it on large exports.
- `--max-term-df-pct` (35) drops words that appear nearly everywhere — the
  product noun, the brand name — which would otherwise glue everything into a
  single cluster.
- `--min-pmi` (1.0 bits) is the merge threshold. Lower it for fewer, broader
  themes; raise it for more, tighter ones.
- `--min-theme-docs` (3) is the smallest cluster reported as a theme; the rest
  fall into "no recurring theme".
- `--examples` and `--quote-chars` control the verbatims quoted per theme.
- `--json` emits every theme with its full document id list, for pushing back
  into a helpdesk or a spreadsheet.

Bad input exits 2 naming the row and column: a missing required column, an
empty text, a duplicate id, a `source` that is not `review` or `ticket`, or a
rating outside 1–5.

## Reading and presenting the output

- **Themes ranked by rating drag.** Drag is `(overall mean − theme mean) ×
  rated reviews in the theme`, in star-points: it combines how bad a theme is
  with how often it happens, which volume alone does not. A **negative** drag
  means the theme rates above average — those clusters are praise, and they
  are the language to reuse in marketing copy, not a problem to fix.
- **Category** per theme: `PRODUCT DEFECT`, `EXPECTATION MISMATCH`,
  `LOGISTICS`, `SERVICE`, `USABILITY`, `VALUE`, or `UNCLASSIFIED`. The signal
  counts underneath show how decisive the label was; a theme with a thin
  margin between two categories deserves a human read.
- **Theme detail** carries the terms, the SKU breakdown, two representative
  verbatims with ids, and an owner plus an action.
- **Defect versus expectation** totals. This is the split to lead with.

When reporting to a human:

1. Open with the split: how much of the negative feedback is the product
   failing versus the page over-promising. Expectation mismatches are usually
   fixable this week by copy, spec, and photography; defects need a supplier
   or tooling conversation.
2. Give the top three themes by drag with the star-point number, one verbatim
   each, and the SKUs affected. The verbatim is what makes it land.
3. Convert each theme into one owned action, not a summary. "Add base diameter
   in inches to the spec table and the first bullet on NF-BTL-32, and shoot one
   photo of it in a car cup holder" beats "customers mention fit".
4. Note the themes with negative drag as copy that already works.
5. Be honest about cluster quality: name any theme whose label looks like an
   artefact and exclude it from the recommendation list.

## Limits

- **No sentiment analysis.** A theme groups documents that talk about the same
  subject, including the ones that praise it. A `lid / leaks` theme can contain
  "the lid seals perfectly". Always read the quoted verbatims before acting;
  the rating column is the only polarity signal in the report.
- **No semantics or synonyms beyond stemming.** "Too small" and "runs tight"
  do not merge. Stemming is a conservative suffix rule, not a lemmatiser, and
  it will occasionally mangle a word. Labels show the most common spelling
  behind each stem, not the stem.
- **No negation handling.** "Does not leak" and "leaks" contribute the same
  term.
- **Themes are statistical, not semantic.** Clusters are built from
  co-occurrence in this data set, so they shift when the input window or the
  thresholds change. Two runs over different months are not directly
  comparable; re-run both windows if you need a trend.
- **Categories come from a fixed signal lexicon** applied after clustering. It
  is English, it is DTC-flavoured, and it will mislabel niche vocabulary. It
  labels clusters, it never creates them.
- **No causality and no revenue attribution.** It cannot tell you what a theme
  costs in returns or lost sales; pair it with returns data for that.
- **Sampling bias is not corrected.** Reviews over-represent the delighted and
  the furious, and tickets over-represent problems by construction. Theme
  shares describe the feedback you received, not the customer base.
- Quoted verbatims are chosen deterministically (most theme terms, then
  shortest, then id), not for rhetorical strength.
