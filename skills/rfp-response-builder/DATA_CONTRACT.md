# Data contract — rfp-response-builder

Two CSV files plus one JSON config. UTF-8, comma separated, one header row.
Quote any field containing a comma. Dates are `YYYY-MM-DD`.

## 1. `requirements.csv` — required

One row per requirement. If the buyer's document bundles several asks into one
paragraph, split them before export; the score treats each row as a single
unit.

| Column | Type | Required | Notes | Typical source |
| --- | --- | --- | --- | --- |
| `requirement_id` | string | yes | The buyer's own numbering where possible (`SEC-03`). Must be unique. | RFP spreadsheet |
| `section` | string | yes (may be empty) | Used for the section-coverage table and for splitting work. Empty becomes `(no section)`. | RFP spreadsheet |
| `requirement_text` | string | yes | The full requirement as written by the buyer. Do not paraphrase; the buyer's vocabulary is what gets matched. Must contain at least one non-stopword term. | RFP spreadsheet |
| `classification` | enum | yes (may be empty with `--infer-classification`) | `mandatory`, `weighted`, or `optional`. Blank is an error unless inference is enabled. | RFP instructions, or inferred |
| `weight` | number | required for `weighted` | The buyer's point value. Blank on mandatory/optional rows (it is ignored there). A blank on an explicitly weighted row is an error; an inferred weighted row falls back to `default_weight`. | RFP scoring table |

Inference, when enabled, reads `requirement_text`: `mandatory_keywords`
(must, shall, required, mandatory) win, then `weighted_keywords` (should,
preferred, desirable, evaluated, scored), otherwise `optional`. Inferred rows
are marked `(inferred)` in the output.

Sources: the buyer's spreadsheet or portal export; a Google Sheet or Excel file
in Drive; a Notion database; or a PDF table transcribed by hand. There is no
PDF parsing in this skill.

## 2. `answer_library.csv` — required

One row per reusable answer.

| Column | Type | Required | Notes | Typical source |
| --- | --- | --- | --- | --- |
| `answer_id` | string | yes | Unique. Printed on every match so a writer can find the source. | Loopio, Responsive/RFPIO, Confluence page id, Notion id |
| `title` | string | yes (may be empty) | Short label. Also contributes tokens to matching. | Same |
| `answer_text` | string | yes | The full approved answer. Empty is an error. Substance matters: short answers match poorly. | Same |
| `tags` | string | yes (may be empty) | Semicolon separated (`sso;saml;scim`). Tag hits earn the tag bonus, and tags are the intended place for synonyms and acronyms the answer body does not spell out. | Same |
| `owner` | string | yes (may be empty) | SME accountable for the answer. Empty becomes `(unowned)`. | Same |
| `product_area` | string | yes (may be empty) | Contributes tokens; useful for routing. | Same |
| `last_reviewed_date` | date | yes | When a human last approved the answer. Drives the staleness flag. A malformed or empty value is an error — an unreviewable answer library is the real finding. | Same |

## 3. `config/matching.json` — required (defaults ship with the skill)

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `min_token_length` | integer | 3 | Tokens shorter than this are dropped. |
| `strong_match_threshold` | number | 0.55 | Score at or above which a requirement counts as covered. |
| `partial_match_threshold` | number | 0.30 | Score at or above which a requirement counts as partial. Must be `0 < partial <= strong <= 1`. |
| `tag_bonus_per_match` | number | 0.05 | Added per requirement term found in an answer's tags. |
| `tag_bonus_cap` | number | 0.15 | Maximum total tag bonus. |
| `max_matches_per_requirement` | integer | 3 | How many candidate answers to print per requirement. |
| `stale_after_days` | integer | 365 | Days since `last_reviewed_date` before an answer is flagged stale. |
| `default_weight` | number | 1.0 | Weight used for an inferred weighted requirement with no weight cell. |
| `inference.mandatory_keywords` | string[] | must, shall, required, mandatory, no exception | Checked first. |
| `inference.weighted_keywords` | string[] | should, preferred, desirable, evaluated, scored | Checked second. |
| `stopwords` | string[] | see file | Removed from both sides before scoring. Trim this list if your domain's real vocabulary is being stripped. |

## Units and conventions

- Scores are 0.00-1.00, rounded to four decimals in JSON and two in the text
  report.
- "Points at risk" is `weight x (1 - best score)` in the buyer's own point
  units; it is only meaningful if `weight` came from the buyer's scoring table.
- Token normalization is deliberately shallow: lowercase, split on
  non-alphanumerics, drop stopwords and short tokens, and strip a trailing
  plural `s` (or `ies` to `y`) on tokens longer than four characters, except
  words ending in `ss` or `us`. Both sides get the same treatment.

## Failure modes

The script exits `2` and names the file, row, and column for: a missing or
empty file, a missing required column, a duplicate `requirement_id` or
`answer_id`, an empty `requirement_text` or `answer_text`, a `classification`
outside the enum, a blank `classification` without `--infer-classification`, a
missing `weight` on an explicitly weighted requirement, a negative or
non-numeric weight, a malformed `last_reviewed_date`, a requirement whose text
has no scoreable terms after stopword removal, and thresholds that violate
`0 < partial <= strong <= 1`.
