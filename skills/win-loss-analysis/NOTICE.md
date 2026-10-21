# NOTICE

`win-loss-analysis` is an adapted work. Type (type.com) built it from the open-source sources below and added a
cross-meeting win/loss report over Granola meeting notes joined to CRM outcomes, a deterministic script, an editable
reason taxonomy and a CSV deal-list import. The full license text is in `LICENSE.txt`.

## Sources

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `sales/skills/win-loss-review/SKILL.md` | [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `02ffa99ff00ce4d3046ccebabb8357cb39f3ab06` | Anthropic | Apache License 2.0 (`sales/LICENSE`) |
| `sales/skills/competitive-intelligence/SKILL.md` | same @ `02ffa99ff00ce4d3046ccebabb8357cb39f3ab06` | Anthropic | Apache License 2.0 (`sales/LICENSE`) |
| `sales/skills/handle-objection/SKILL.md` | same @ `02ffa99ff00ce4d3046ccebabb8357cb39f3ab06` | Anthropic | Apache License 2.0 (`sales/LICENSE`) |

Commit `02ffa99ff00ce4d3046ccebabb8357cb39f3ab06` was upstream `main` on 2026-10-12 (a merge commit dated
2026-10-11). `LICENSE.txt` reproduces `sales/LICENSE` from that commit, unchanged.

## Modifications (Apache-2.0 §4(b): statement of changes)

`win-loss-guide.md` and parts of `SKILL.md` are adapted from the three upstream files:
- From `win-loss-review`: grounding stage, loss-reason and size fields on the live CRM schema; pulling every closed
  opportunity in the period with amount, stage, close date and loss reason; the stage each loss died at from
  opportunity history; win rate by cut and the loss-stage distribution; stated loss reasons (competitor, budget,
  timing, no decision) and objections that appeared in losses but not wins, read from transcripts; "cite
  specifics"; transcripts as untrusted content; "this skill only reads". Reworded and condensed. Dropped: the per-rep
  and lead-source cuts, cycle-length medians, the "5 largest wins and losses" sample (every deal is read instead),
  recommended coaching actions, the hand-off to `update-opportunity`, and the "How it adapts" tier block.
- From `competitive-intelligence`: win/loss by competitor with sample sizes stated before conclusions; "include both
  wins and losses", "distinguish facts from interpretations" and "don't overweight recent anecdotes"; working from
  transcript mentions when the CRM has no competitor field. Dropped: battlecards, enrichment, the weekly digest and
  the in-deal play.
- From `handle-objection`: the objection types (price/value, timing/priority, competitive comparison, risk/trust,
  authority, status quo) and "an objection is not a negotiation move". Dropped: talk tracks, reply drafting and proof
  points.
- From all three: the shared rules on citing every value as read, "blank" versus "not read", asking one question
  when a fact is missing, and never acting on instructions found inside transcripts, emails or records, condensed.

Added by Type (not from upstream): the distinct-deal counting, the small-sample rule, the competitive / no-decision /
other loss split, the evidence source labels and whole-sentence quote checks, the Granola listing, linking and coverage rules,
`reason_taxonomy.json` (its categories are Type's, informed by the upstream reason and objection types), and the
report format.

Removed from upstream: slash commands, `CONNECTORS.md` placeholders, connector-permission and rendering rules
specific to the upstream plugin, and the "How it adapts" blocks.

New files written by Type (not from upstream): `build_winloss.py`, `granola_text.py` (shared text helpers), `winloss.json`, `reason_taxonomy.json`,
`DATA_CONTRACT.md`, `README.md`, `run.sh`, `tests.sh`, `expected_output.txt`, `sample_bundle.json`,
`sample_bundle_csv.json` and `sample_deals.csv` (fictional data), and this `NOTICE.md`.

- Packaging: the upstream `sales/LICENSE` file is shipped as `LICENSE.txt` (text unchanged).

## Other sources checked (not used)

- `anthropics/knowledge-work-plugins` `sales/skills/customer-voice` and `deal-review`: overlap with the files above;
  not needed.
- `coreyhaines31/marketingskills` @ `1efedbc5148b54b2f0f6c6c9fe0be62e151c7fff` (MIT): `competitors`,
  `competitor-profiling` and `sales-enablement` cover competitor pages, profiling and objection libraries for
  content, not win/loss analysis of closed deals; nothing was copied.
- `winterje90/strategic-analysis-skill-public` @ `2d665af6075cbc862afedc60c718d13a192a849b` (MIT): a broad
  CRM-plus-transcripts strategy analysis that includes win/loss; read for comparison only, nothing was copied.
- `j-sulliman/summit53-claude-skills` @ `7f7e76493ad05265003dbc89f8bd6098ff6fe97c`: has a win/loss skill but no
  license, so nothing was copied or read closely.
- Granola: the `granola-ai` GitHub organisation (which appears unofficial) has no skills or prompts, only a `.github`
  and a Windows-download repository; a repository search for Granola win/loss material found nothing relevant
  (2026-10-12).
