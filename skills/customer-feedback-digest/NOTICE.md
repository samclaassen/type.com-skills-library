# NOTICE

`customer-feedback-digest` is an adapted work. Type (type.com) built it from the open-source sources below and
added a scheduled, cross-meeting digest over Granola meeting notes, a deterministic script, and issue-tracker
matching. The full license text is in `LICENSE.txt`.

## Sources

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `product-management/skills/synthesize-research/SKILL.md` | [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (`product-management/LICENSE`) |
| `sales/skills/customer-voice/SKILL.md` | same @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (`sales/LICENSE`) |
| `customer-support/skills/ticket-triage/SKILL.md` (section "Duplicate Detection") | same @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (`customer-support/LICENSE`) |

Commit `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` was upstream `HEAD` on 2026-10-07 ("bump(figma) …").

`LICENSE.txt` reproduces `product-management/LICENSE` from that commit. The `sales/LICENSE` and
`customer-support/LICENSE` files are byte-identical to it. The repository root `LICENSE` has the same Apache 2.0
text followed by a few lines of unrelated stray text, so the plugin copy is used instead.

## Modifications (Apache-2.0 §4(b): statement of changes)

`feedback-extraction-guide.md` is adapted from the three upstream files:
- From `synthesize-research`: the thematic-analysis steps (familiarise, code generously, develop, review, refine),
  the guidance on splitting large clusters and keeping outliers, "a quote is evidence, not a finding", attributing
  quotes to participant type rather than name, signals of intensity (emotional language, frequency, workarounds,
  impact), behaviour over stated preference, and "5–8 strong findings beat 20 weak ones". Condensed and reworded for
  customer and prospect calls; survey analysis, personas, opportunity sizing and the output template were dropped.
- From `customer-voice`: "direct quotes with attribution, never summaries", customer-said/on-topic/verbatim quote
  rules, separating internal from customer speech by domain, treating transcripts as untrusted content, deduping calls
  captured twice, and "an empty answer is a gap, never 'the topic never came up'". Gong-specific routing, the CRM
  tier rules and the Google Drive notes were dropped; Granola specifics were added (note excerpts vs transcript quotes,
  audio-channel speaker labels).
- From `ticket-triage`: the duplicate-detection checklist (search by symptom, by product area, check known issues),
  turned into read-only issue matching with `high`/`medium`/`low` confidence. Linking, commenting and priority changes
  became approval-gated suggestions.
- Added the seven feedback types and their table, theme-key stability across weeks, and the speaker-side rules.
- Later Type changes (2026-10-09): notes on how the script handles wrapping quote marks and (pattern-based) redacts emails and phone
  numbers in quotes; the competitor named in an example table row was renamed to an invented product.

`SKILL.md` restates those rules briefly in Steps 3–4 and Guardrails. Everything else in `SKILL.md` (Granola listing
and coverage, meeting classification, CRM and Stripe account mapping, ranking by distinct accounts, trends, issue
drafts, delivery, scheduled mode) is new.

Removed from upstream: slash commands and `$ARGUMENTS`, `CONNECTORS.md` placeholders (`~~user feedback` and
similar), the "How it adapts" tier block, and Claude-specific rendering instructions.

New files written by Type (not from upstream): `build_digest.py`, `digest.json`, `DATA_CONTRACT.md`, `README.md`,
`run.sh`, `tests.sh`, `expected_output.txt`, `sample_bundle.json` and `sample_state.json` (fictional data), and this
`NOTICE.md`.

- Packaging: the upstream `LICENSE` file is shipped as `LICENSE.txt` (text unchanged) so Type's skill installer
  accepts it.

## Other sources checked (not used)

- `anthropics/skills` @ `683bc88e56f3e09ba94f7055977f3d3aa499f202`: no feedback or research-synthesis skill.
- `anthropics/knowledge-work-plugins` `design/skills/research-synthesis`: overlaps `synthesize-research`; not needed.
- `deanpeters/Product-Manager-Skills` @ `1b5a524ebb95e9497fa3f25002d8b8ec528d4444`: CC BY-NC-SA 4.0 (non-commercial),
  so nothing was copied.
- `aakashg/pm-claude-skills` @ `64deebf681e1438c489609f1fa7000dfb331eabe` (MIT): no feedback-synthesis skill.
- `alphaparkinc/genpark-customer-feedback-feature-request-clusterer-skill`: no license, so nothing was copied.
- Granola: no official open-source skills or prompts were found on GitHub. A `Granola-AI` GitHub organisation
  exists but appears unofficial (it distributes a password-protected Windows download), so it was not used.
