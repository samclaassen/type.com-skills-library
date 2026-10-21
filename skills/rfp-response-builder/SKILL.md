---
name: rfp-response-builder
description: Parses an RFP requirements export into mandatory, weighted, and optional requirements, matches each against your answer library by IDF-weighted keyword overlap, and reports coverage, best-match answers, weighted points at risk, and the gaps nobody can answer.
---

# RFP Response Builder

Answers the first question of any RFP: can we actually respond to this, and
what do we still have to write? It scores every requirement against the answer
library you already maintain, then separates the requirements that are covered
from the ones that will lose points or disqualify the bid.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `rfp-response-builder`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Bid/no-bid decisions. Run it on the requirements the day the RFP lands; the
  blocker count is the bid risk.
- Work planning once you have decided to bid: the gap list is the SME
  assignment list, ranked by what it costs to leave it empty.
- Answer library maintenance. The "never selected" and "stale" lists show which
  answers are dead weight and which are overdue for review.

## Inputs

`DATA_CONTRACT.md` has the exact columns, types, and units. You need:

1. `requirements.csv` — one row per RFP requirement.
2. `answer_library.csv` — your reusable answers, with tags and owners.
3. `config/matching.json` — thresholds, stopwords, bonuses (ships with defaults).

### Gathering the inputs through Type integrations

- **Requirements** usually arrive as a spreadsheet or a PDF table. Convert the
  sheet (Google Drive/Sheets, Excel, or a Notion database) to CSV with the
  contract's columns. If the buyer supplied only prose, split it one
  requirement per row first; the scoring assumes one requirement per record.
- **Answer library**: Loopio, Responsive/RFPIO, Confluence, Notion, a Google
  Doc, or a Drive spreadsheet. Export answer id, title, body text, tags, owner,
  product area, and the last review date. The last review date is what makes
  the staleness warning meaningful — do not fake it.
- **Owners** should be real people; the gap list is only actionable if each
  answer has someone attached.

Keep the answer text substantive. Matching is lexical: a two-line answer will
not match a detailed requirement even if the underlying capability exists.

## Running it

```bash
python3 scripts/rfp_builder.py \
  --requirements path/to/requirements.csv \
  --answers path/to/answer_library.csv \
  --config config/matching.json \
  --as-of 2026-09-22 \
  --infer-classification
```

- `--as-of` is required and drives answer staleness only.
- `--infer-classification` allows blank `classification` cells to be inferred
  from must/shall/should language. Without it, a blank cell is an error — that
  is deliberate, because misclassifying a mandatory requirement is the most
  expensive mistake this tool can make.
- `--json` emits every requirement with its matches, matched terms, and missing
  terms.
- Bad input exits `2` naming the file, row, and column.

See `examples/run.sh` for a complete working invocation.

## How matching works

1. Both requirement and answer text are lowercased, split on non-alphanumerics,
   stripped of stopwords, and lightly stemmed (plural `s`/`ies` only).
2. Each term gets an IDF weight computed across the answer library. A term that
   appears in every answer ("data") is nearly worthless; a rare term
   ("fedramp") is worth a lot. Terms found nowhere in the library carry the
   maximum weight, so an unanswerable requirement scores near zero instead of
   matching on filler words.
3. `coverage = matched term weight / total requirement term weight`.
4. A small bonus is added per requirement term that also appears in an answer's
   tags, capped by `tag_bonus_cap`.
5. `score >= strong_match_threshold` is covered, `>= partial_match_threshold`
   is partial, anything lower is a gap.

Everything in that pipeline is in `config/matching.json`.

## Reading and presenting the output

- **Coverage summary** — the headline. Mandatory gaps are blockers; the
  weighted score is the share of scored points your current library can defend.
- **Blockers** — mandatory requirements with no answer. Each shows the closest
  answer and the specific unanswered terms, which is usually enough for an SME
  to start writing. Two or more blockers on hard compliance requirements
  (certifications, authorizations) is a no-bid conversation.
- **Weighted points at risk** — ranked by `weight x (1 - score)`. Work this
  list top-down; it is where writing effort buys the most score.
- **Section coverage** — which part of the response is weakest, useful for
  splitting the work across teams.
- **Requirement detail** — the drafting aid. Each requirement lists its top
  matches with owner and the matched/missing terms, so a writer can start from
  a real answer instead of a blank page.
- **Answer library health** — stale best matches and never-selected answers.

When presenting: lead with blockers and the weighted score, name the owners who
need to write, and give the date the draft has to be back.

## Limits

- **Lexical matching only.** There is no embedding model and no synonym list.
  "MFA" will not match "multi-factor authentication" unless one of them appears
  in the answer's tags. Keep tags rich; that is the intended workaround.
- It does not write the response. Best-match answers are starting material that
  a human must tailor to the buyer's wording.
- Scores are relative to your library, not to the buyer's scoring rubric. A
  0.90 means your library covers the requirement's vocabulary, not that an
  evaluator will award full marks.
- Classification inference reads keywords only. A requirement that says
  "should" in a section headed "Mandatory Requirements" will be misclassified;
  supply the column explicitly when the stakes are high.
- Multi-part requirements ("describe X, Y, and Z") are scored as one unit and
  can look covered when only X is answered. Split them during export.
- No PDF or Word parsing, no submission formatting, no compliance matrix
  export. Input is CSV, output is a work list.
