# NOTICE

`decision-log` is an adapted work. Type (type.com) built it to keep a team decision
log across many Granola meetings. The fields each logged decision carries, and the
status vocabulary around them, are adapted from MADR, which is available under the
MIT License (MADR is dual-licensed "MIT OR CC0-1.0"; Type uses it under MIT). The
full license text, upstream `LICENSE.MIT` at the commit below, is in `LICENSE.txt`.

## Source

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `template/adr-template.md` (front matter `status`, `date`, `decision-makers`; sections "Considered Options", "Decision Outcome" — "Chosen option: …, because …", and "More Information" — "if/when it should be re-visited") | [adr/madr](https://github.com/adr/madr) @ `ba75bb1b20d42af5746b246ad348c202419ae681` | Oliver Kopp, Olaf Zimmermann and MADR contributors | MIT, Copyright (c) 2017-2022 Oliver Kopp, Olaf Zimmermann |

The commit was the upstream `develop` HEAD when checked on 2026-10-12 (the template
file was last changed upstream in `8135ed2e01503be852769402ec3eeb585bfe75a2`). No code
and no template text was copied: MADR is a markdown template for architecture decision
records written by hand, one file per decision. Only its field structure and status
vocabulary were adapted.

## What was adapted, and how it changed (statement of changes)

- **Status vocabulary.** MADR's `status: proposed | rejected | accepted | deprecated |
  … | superseded by ADR-0123` became the entry kinds and computed statuses here:
  `proposal` (open, or closed when a later decision supersedes it), `decision`
  (active), `superseded` (computed from a later decision's `supersedes` list), and
  `dismissed` (an entry the user says was never a decision). We added the rule that a
  proposal is never promoted to a decision without a later decision or the user's
  explicit `confirm` reply, and the flags for reversals, possible conflicts on the same
  topic key, missing owners and passed revisit dates. `rejected` and `deprecated` were
  not carried over.
- **`decision-makers`** became `decided_by` (names as the meeting shows them). We added
  a separate `owner` (who carries the decision out), which MADR doesn't have, and the
  "no owner" flag. MADR's `consulted` and `informed` were not carried over.
- **`date`** became the meeting's local date (or the day the user confirmed a proposal),
  not the date the record was last edited.
- **"Considered Options"** became `alternatives_rejected`: only options actually
  discussed and not chosen.
- **"Decision Outcome" — "Chosen option: …, because …"** became `statement` plus
  `rationale`, where the rationale must be the reason given in the meeting, or empty.
- **"More Information" — when the decision should be re-visited** became `revisit_on`,
  a date set only when the meeting named one, and the "revisit date passed" flag.
- **"superseded by ADR-0123"** became the `supersedes` link on the newer decision, with
  the reversal shown on both entries, citing both meetings.
- Added for meetings, with no MADR counterpart: the quote (checked as whole sentences when
  the meeting text is supplied) and its source label,
  the meeting citation on every entry, topic keys and area tags, linked repeats, stable
  ids, the weekly two-phase report, search with citations, and the full-log rendering.
- Not carried over: one markdown file per decision, ADR numbering, "Context and Problem
  Statement", "Decision Drivers", "Consequences", "Confirmation" and "Pros and Cons of
  the Options".

## Repositories checked and not used

- [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins)
  (Apache-2.0): its productivity plugin has task and memory skills but no decision-log
  skill; nothing was taken from it.
- [architecture-decision-record/architecture-decision-record](https://github.com/architecture-decision-record/architecture-decision-record)
  (formerly joelparkerhenderson/architecture-decision-record): the author's own material
  is CC BY-NC-SA, which is not permitted for this library; nothing was taken from it.
- [npryce/adr-tools](https://github.com/npryce/adr-tools): GPL-3.0; nothing was taken
  from it.
