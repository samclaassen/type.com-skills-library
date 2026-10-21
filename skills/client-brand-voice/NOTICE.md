# NOTICE

`client-brand-voice` is an adapted work. Type (type.com) built it for
agencies that manage many client brands, from the open-source sources below.
The full license texts are in `LICENSE.txt`.

## Sources

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `partner-built/brand-voice/skills/discover-brand` (SKILL.md, references/search-strategies.md, references/source-ranking.md) | [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `1bd42820da111e5f0206e570bf5228a1c35839c7` | Tribe AI | MIT, Copyright (c) 2025 Tribe AI, from the folder's own `partner-built/brand-voice/LICENSE` |
| `partner-built/brand-voice/skills/guideline-generation` (SKILL.md, references/guideline-template.md, references/confidence-scoring.md) | same @ `1bd42820da111e5f0206e570bf5228a1c35839c7` | Tribe AI | MIT (as above) |
| `partner-built/brand-voice/skills/brand-voice-enforcement` (SKILL.md, references/voice-constant-tone-flexes.md) | same @ `1bd42820da111e5f0206e570bf5228a1c35839c7` | Tribe AI | MIT (as above) |
| `marketing/skills/brand-review/SKILL.md` | same @ `1bd42820da111e5f0206e570bf5228a1c35839c7` | Anthropic | Apache License 2.0 (`marketing/LICENSE`; the repo root `LICENSE` is also Apache-2.0) |

Commit `1bd42820da111e5f0206e570bf5228a1c35839c7` was upstream `HEAD` on
2026-09-22 ("Add Pendo plugins …").

About the Apache text used: the repository root `LICENSE` at this commit is
the Apache License 2.0 followed by about ten lines of unrelated stray text
after the appendix. `LICENSE.txt` in this package reproduces
`marketing/LICENSE` instead. That is the marketing plugin's own copy, and it
matches the root file line for line up to the end of the appendix, without
the stray text.

## Modifications (Apache-2.0 §4(b): statement of changes)

Changes that apply across the package:
- Merged three upstream brand-voice skills and the brand-review skill into
  one skill, `client-brand-voice`, with three modes: build, review, and list.
- Replaced the single workspace-wide guidelines file
  (`.claude/brand-voice-guidelines.md`) and the settings file
  (`.claude/brand-voice.local.md`) with one file per client at
  `clients/<slug>/brand-voice.md`, following the shared agency `CLIENTS.md`
  profile format (included verbatim).
- Required every operation to name a client. The skill resolves the client
  by slug or by `client.json` name, stops and asks when the match is
  ambiguous, and reports exactly what's missing.
- Added rules that stop clients mixing: never load two clients' guidelines,
  never apply one client's guidelines to another's copy, and never fall back
  to another client's guidelines.
- Marked generated guidelines, discovery reports, and review notes as drafts
  for the client's account lead. Added a `status: draft|approved` field that
  only the account lead changes.
- Kept upstream's approval-before-writing behavior and archive-on-overwrite
  (`brand-voice-YYYY-MM-DD.md`), and made the approval name the client and
  the file.
- Removed slash-command references (`/brand-voice:*`, `/brand-review`), the
  sub-agent delegation (discover-brand, document-analysis,
  conversation-analysis, content-generation, and quality-assurance agents),
  the `CONNECTORS.md` placeholder mechanism, and the plugin manifest and
  `.mcp.json`. None of these were carried over. The quality checks are
  inlined in SKILL.md.
- Dropped content generation (the enforcement skill's "write an email in our
  voice" path) and the `before-after-examples.md` reference, which isn't
  included. Review mode keeps the enforcement skill's conflict-handling and
  open-question behavior.
- Added a "Limits" section.

`references/discovery-and-ranking.md` (from discover-brand SKILL.md,
search-strategies.md, and source-ranking.md):
- Discovery now searches only the sources the user scopes to one client,
  instead of all connected platforms. Queries are confined to the named
  folder, space, or channel and include the client's name.
- Added an exclusion rule for material that might belong to another client or
  to the agency, plus an "Excluded as possibly another client's" list.
- Condensed the per-platform query lists into one table. Rephrased the
  category, ranking, adaptive-scoring, recency-cutoff, and triage rules for
  one client. The weights and thresholds are unchanged.
- Agency-written copy the client hasn't approved is classified as CONTEXTUAL
  at most.
- Added a per-client discovery report template.

`references/guideline-template.md` (from guideline-template.md):
- Changed the title to the client name and added a draft banner, plus
  `Client`, `Status`, and "Excluded as possibly another client's" metadata.
- Added the "Machine-Checkable Rules" section with the `voice-rules` block.
- Added usage notes: no reuse of another client's content, and the status
  workflow.

`references/confidence-scoring.md` (from confidence-scoring.md):
- Added an adaptation note and a rule that only this client's scoped sources
  count toward corroboration. Everything else is unchanged upstream text.

`references/review-rubric.md` (from brand-review SKILL.md and
voice-constant-tone-flexes.md):
- Tied reviews to one client's guidelines file, with its version and status
  in the header.
- Added line-numbered findings, merged with the helper's mechanical hits,
  and default severities for each mechanical rule.
- Made the output a draft for the account lead. The generic review is
  explicitly labelled and never borrows another client's guidelines.
- Condensed the voice and tone reference tables, and removed the generic
  brand-voice documentation framework, which the guideline template covers.
- The legal and compliance flags now point to `client.json` notes for
  client-specific legal rules.

New files written by Type (not from upstream): `scripts/check_voice_rules.py`,
`DATA_CONTRACT.md`, `listing.json`, `examples/` (synthetic clients "Harbor &
Vine" and "Northwind Ledger", a synthetic draft, `run.sh`, and
`expected_output.txt`), and this `NOTICE.md`.

- Packaging: the upstream `LICENSE` file is shipped as `LICENSE.txt` (text unchanged) so Type's skill installer accepts it.
