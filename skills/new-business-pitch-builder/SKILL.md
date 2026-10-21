---
name: new-business-pitch-builder
description: Turn an audit of a prospect's paid, organic and funnel surface into a pitch with a prioritized gap list, confidence-discounted opportunity sizing in gross profit, and a 90-day engagement outline built from the gaps that scored highest.
---

# New business pitch builder

Converts four exported files about a prospect into the spine of a pitch: what is
broken, what each fix is worth, in what order, and what the first 90 days look
like. Every number is traceable to a line in the input and to a stated
assumption, so the prospect can argue with the assumption instead of dismissing
the number.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `new-business-pitch-builder`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Preparing a pitch, an audit deck or a proposal for a prospect where a data
  pull is possible (shared read-only access, a Looker Studio export, or numbers
  the prospect supplied in discovery).
- Qualifying an inbound lead: run it early to see whether there is enough
  opportunity to justify the pursuit.
- Building a renewal or expansion case for an existing client — the math is
  identical.

Do not use it to invent numbers for a prospect who has shared nothing. Without
real inputs the output is a confident fiction, which is worse than no deck.

## Gathering the inputs

Four CSVs, specified in `DATA_CONTRACT.md`. The script reads local files only.

1. **`paid_snapshot.csv`** — one row per paid channel for a representative
   month: spend, impressions, clicks, conversions, revenue. Sources: the
   prospect's ad accounts if read access was granted, a shared reporting
   dashboard, or figures they provided. Use one clean month, not a partial one.
2. **`seo_pages.csv`** — URL, target keyword, monthly search volume, current
   position, current monthly clicks. Sources: the prospect's Search Console
   export, or a third-party rank/volume tool. Never estimate positions by eye.
3. **`funnel_steps.csv`** — the site funnel as an ordered list of steps with
   user counts, from sessions through purchase or qualified lead. Source: the
   analytics property's funnel or path report.
4. **`benchmarks.csv`** — the comparison rates the pitch is argued against:
   per-channel conversion rate, per-step funnel rate, and optional
   position-CTR overrides. Use the agency's own portfolio medians where
   possible; cite the source in the file's `source` column and be ready to
   defend it in the room. Benchmarks are the load-bearing assumption of the
   entire deck.

Ask the prospect for average order value and gross margin. If they will not
share margin, use a conservative industry figure and say so on the slide — the
entire opportunity scales linearly with it.

## Running it

First run: `bash examples/run.sh` builds the pitch for the bundled sample
prospect in `examples/data/`. The equivalent command, which you point at your
own exports by replacing the `examples/data/` paths:

```bash
python3 scripts/build_pitch.py \
  --paid examples/data/paid_snapshot.csv \
  --seo examples/data/seo_pages.csv \
  --funnel examples/data/funnel_steps.csv \
  --benchmarks examples/data/benchmarks.csv \
  --prospect "Tidewater Outfitters" \
  --aov 85 \
  --gross-margin 55 \
  --as-of 2026-09-22 \
  --proposed-retainer 9500
```

- `--aov` and `--gross-margin` convert incremental conversions into gross
  profit. Both are required; there is no default, because a guessed margin
  silently changes every number in the deck.
- `--as-of` is required and only labels the analysis.
- `--target-position` (default 3) and `--max-position` (default 20) bound the
  organic estimate.
- `--reallocation-threshold` (default 25) is how much of a channel's spend must
  be recoverable before it is called a gap.
- `--confidence-scale` (default 1.0, maximum 1.0) uniformly lowers every
  confidence haircut. Use `0.7` or `0.6` for a deliberately conservative
  version — useful when the prospect is analytical or burned by a previous
  agency.
- `--proposed-retainer` compares the non-overlapping gross-profit estimate to
  the fee. Omit it if pricing is not yet on the table.
- `--json` for machine output.

## Reading and presenting the output

- **What we found** is the opening slide. It is written to be read aloud.
- **Prioritized gaps** is ranked by adjusted annual value per week of delivery
  effort, so the quick wins lead. Present the table, then the two or three
  detail blocks you intend to defend — do not read all of them.
- Every gap shows both a **raw** and an **adjusted** value. Present the
  adjusted number. Keep the raw number available for the question "where did
  that come from", and never total the raw column on a slide.
- **Opportunity summary** is the one-number-per-area slide. It deliberately has
  no total row and no raw column. Gaps are valued in two units — **gross
  profit** (extra orders) and **recovered spend** (paid budget that can be moved
  or cut) — and the two are never added together. The funnel and paid
  conversion-rate rows lift conversion of the same visitors, so the
  **non-overlapping gross profit** line counts only the larger of the two plus
  organic search. Quote that line and the recovered-spend line separately; do
  not add the table's rows on a slide. Funnel work usually dominates; that is
  real, and it is also the work a prospect is least likely to believe, so lead
  with the fast paid fixes and let the funnel number sit behind them.
- The **retainer comparison** uses only the non-overlapping gross-profit figure.
  Present it as "the estimate is N times the fee", never as a promise that the
  fee pays for itself.
- **Proposed 90-day engagement** is populated from the gaps that actually
  scored, not from a template: the phases contain the workstreams that follow
  from this prospect's evidence. Edit the wording, keep the mapping.
- **How these numbers were built** and **What this analysis cannot see** belong
  in the appendix and in the room. Presenting the limits is what makes the rest
  credible.

## Limits

- Opportunity sizing is arithmetic against benchmarks, not a forecast and not a
  guarantee. Never present it as revenue the agency will deliver.
- Benchmarks are supplied, not derived. A generous benchmark file produces a
  generous pitch; the skill cannot tell whether a benchmark is fair.
- Each paid channel contributes at most one gap, and funnel gaps are each
  computed with the other steps held at their current rates. Even so, gaps
  across areas overlap: improving paid conversion rate and fixing checkout touch
  the same users.
- Organic estimates assume ranking at the target position is achievable and
  ignore SERP features, intent mismatch, cannibalization and competitive
  difficulty. Keywords beyond `--max-position` are excluded outright.
- It uses platform-reported conversions and revenue as given. No attribution
  correction, no incrementality, no de-duplication across channels.
- It does not price the engagement, write the proposal, or assess whether the
  prospect can execute. It produces the analytical spine a human turns into a
  pitch.
