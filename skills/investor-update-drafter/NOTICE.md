# NOTICE

`investor-update-drafter` is an adapted work. Type (type.com) built it from the open-source sources below and added
a cross-meeting workflow over Granola meeting notes, metrics from Stripe or a metrics file, a deterministic script
(`build_update.py`) and its rules for sources, names, confidential items, figures and forward-looking wording. The
full license texts are in `LICENSE.txt`.

## Sources

| Source files | Repository @ commit | Author | License |
|---|---|---|---|
| `SKILL.md`, `templates/update-structure.md` | [Pitchdrive/investor-update-skill](https://github.com/Pitchdrive/investor-update-skill) @ `29ad5c74f4d025d0711809638f0ce1f732eabd66` | Pitchdrive | MIT |
| `TEMPLATE.md`, `GUIDE.md` | [batkotron/investor-update-template](https://github.com/batkotron/investor-update-template) @ `0007c5ded07701f8c02bf7a731d0be19716b09a0` | Michael Batko | MIT |
| `skills/investor-update/SKILL.md`, `skills/investor-update/references/say-do-ledger.md`, `skills/investor-update/references/honesty-and-bad-news.md`, `skills/investor-update/references/metrics-frame.md` | [Management-OS-Inc/revenue-leader-skills](https://github.com/Management-OS-Inc/revenue-leader-skills) @ `415068ae144e2621f1a4d1f5e39126a385cf6086` | Management OS Inc. | MIT |

Each commit was its repository's `HEAD` on 2026-10-12. `LICENSE.txt` reproduces each repository's `LICENSE` file at
that commit, unchanged.

## Modifications (statement of changes)

`update-writing-guide.md` is adapted from the sources above, condensed and reworded; a few of the ideas below are
enforced by `build_update.py` instead (noted where they are):

- From Pitchdrive: "never invent a number; a missing metric is stated as missing", checking a figure that moved about
  10x before using it (the script's `jump_factor` check), attributing each number to its system and date (the
script's Source, as of column), bad news in the first sentence of its section,
  specific asks, "nothing significant this month is a legitimate line", no padding, and "you draft; the founder
  sends" (SKILL.md Step 8). Dropped: the eight-section CEO-letter structure, the HTML version, brand discovery, publishing to a web
  host, the cover-email step and the local `investor-updates/` files.
- From batkotron: the section set and order (TL;DR, highlights, lowlights, metrics with this period, last period and
  change, team, asks), "lowlights are where you build trust" and the three lowlight rules (specific, what you
  learned, what you are doing about it), three-to-five highlights, specific asks, and the adjective-inflation
  warning. Changed: the guide's example of naming a departing employee and the reason they left is replaced by
  Type's rule that personnel matters about identifiable people stay out of investor updates.
- From Management OS: the say-do ledger (restate last period's commitments as written, statuses done, partial,
  missed, changed, blocked, dropped; no rewriting history), status words that carry information, phrases to delete,
  the bad-news order (fact, cause, plan), paired disclosure, "same metrics, same order, every period", "runway,
  always", "unmeasured means unmeasured" and "report by count, not by name, unless the audience is entitled to
  names". Dropped: the scoring rubric, worked examples, templates for other update types, and the advice to call the
  lead investor.
- Added by Type: the rules the script enforces (figures only through metric placeholders, names only through entity
  placeholders and `public_ok`, confidential tags and the board-only section, founder statements for forward-looking
  wording, quote permission) and how to write items that pass them.

`SKILL.md` restates the section order, the say-do statuses and the honesty rules briefly. Everything else in it
(Granola listing and reading, meeting classification, Stripe and metrics-file collection, the bundle, the review and
Gmail-draft steps, guardrails and limits) is new. Nothing from the sources' slash commands, local file layouts or
publishing steps was kept.

New files written by Type (not from upstream): `build_update.py`, `update.json`, `DATA_CONTRACT.md`, `README.md`,
`run.sh`, `tests.sh`, `expected_output.txt`, `sample_bundle.json` and `sample_metrics.csv` (fictional data), and this
`NOTICE.md`. `granola_text.py` is Type's shared Granola text helper (v1.0.0), copied unchanged into each Granola skill
that checks quotes, redacts contact details or escapes markdown.

## Other sources checked (not used)

- `anthropics/knowledge-work-plugins` @ `02ffa99ff00ce4d3046ccebabb8357cb39f3ab06` (Apache-2.0):
  `product-management/skills/stakeholder-update` (a general stakeholder update; its board advice overlaps the sources
  above), `finance/skills/variance-analysis` (budget-vs-actual decomposition, beyond this skill's scope) and
  `small-business/shared/absent-is-not-zero.md` (the idea that a missing figure is not a zero is stated here in our
  own words; no text was copied). The `small-business` plugin has no investor-update skill.
- `tjboudreaux/cc-skills-vc-fundraising` @ `dc227c825356ef5aeab1fe7ce4928c2cd21becec` (MIT): an investor-update
  writer agent; it overlaps the sources above and was not needed.
- `Kotrotsos/claude-skills-non-coding` @ `2aa39fb31e1ce4bab882a93a58c20ca644a1f805` (MIT): an investor-update skill;
  not needed.
- `mooster/founder-toolkit` @ `63d143d6b3b2a96835ec0709d03be4dd3b9b57d0`: no license file, so nothing was copied.
