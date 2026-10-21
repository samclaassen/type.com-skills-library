# NOTICE

`user-research-synthesis` is an adapted work. Type (type.com) built it from the open-source source below and added a
per-study synthesis over Granola meeting notes and transcripts, a deterministic validation and rendering script,
distinct-participant confidence bands, consent handling and pattern-based pseudonymisation. The full license text is
in `LICENSE.txt`.

## Source

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `product-management/skills/synthesize-research/SKILL.md` | [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (`product-management/LICENSE`) |

The same file and commit are the source of the sibling skill `customer-feedback-digest`. `LICENSE.txt` reproduces
`product-management/LICENSE` from that commit, text unchanged (shipped as `LICENSE.txt` so Type's skill installer
accepts it).

## Modifications (Apache-2.0 §4(b): statement of changes)

`research-coding-guide.md` is adapted from the upstream file's sections "Process the Research", "Identify Themes and
Patterns", "Research Synthesis Methodology" (thematic analysis, affinity mapping, triangulation) and "Interview Note
Analysis" (observations, direct quotes, behaviours vs stated preferences, signals of intensity, cross-interview
analysis) and "Tips". Kept, condensed and reworded: familiarise before coding; code generously, one observation per item, easier
to merge than to split; let groups emerge rather than pre-defining them; split clusters that grow too large; outliers
are interesting; "a quote is evidence, not a finding"; attribute quotes to participant type rather than name;
behaviour is stronger evidence than stated preference; workarounds are unmet needs in disguise; the four signals of
intensity; looking for contradictions, segments and surprises; "five to eight strong findings beat twenty weak ones";
and the triangulation paragraph on disagreeing sources.

Dropped from upstream: the gather-inputs questions, survey data interpretation, combining qualitative and
quantitative insights, persona development, opportunity sizing and scoring, the "Output Format" section, the frequency × impact priority matrix
(replaced by the script's evidence ranking), the output template, the "Review and Extend" step, slash commands and
`$ARGUMENTS`, the `CONNECTORS.md` placeholders (`~~knowledge base` and similar) and Claude-specific instructions.

Added by Type (not from upstream): the evidence-item model (`said`/`did`, `transcript`/`notes`, verbatim spans,
interviewer question and leading-question flag), the guidance on leading questions and weak evidence, Granola speaker
labels, the finding types (jobs to be done, pains, workarounds, desired outcomes), the instruction to search for
disconfirming evidence, surprises against prior assumptions, the no-counts rule for statements, open questions, and
the rules on consent and private identifiers.

`SKILL.md` restates those rules briefly in Steps 3–4 and Guardrails. Everything else in `SKILL.md` (scoping the study,
Granola listing and coverage, participant mapping and consent, the script's rules, delivery, guardrails) is new.

New files written by Type (not from upstream): `build_synthesis.py`, `synthesis.json`, `DATA_CONTRACT.md`,
`README.md`, `run.sh`, `tests.sh`, `expected_output.txt`, `sample_bundle.json` (fictional data) and this `NOTICE.md`.
`granola_text.py` is Type's shared text module (whole-sentence quote check, email and phone redaction, markdown
escaping), copied byte for byte from Type's Granola skills. Other hardened helpers in `build_synthesis.py` (URL
validation, strict dates, safe output) are reused from Type's own sibling skills.

## Other sources checked (nothing copied)

- `anthropics/knowledge-work-plugins` @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1`, `design/skills/research-synthesis`
  and `design/skills/user-research`: overlap the file above. The `design` plugin has no `LICENSE` file of its own at
  that commit and the repository root `LICENSE` has stray non-license text appended, so nothing was taken from them.
- `krishnabhat/continuous-discovery-skills` @ `632a46b75c978e8ab273e1846e77a600329f32df` (MIT),
  `skills/interview-synthesizer`: a single-interview snapshot with share-back drafts; different job, nothing copied.
- `phuryn/pm-skills` @ `c68487d721e419ec87af1ae6412a842978eab785` (MIT),
  `pm-product-discovery/skills/summarize-interview`: a single-interview summary template; nothing copied.
- `deanpeters/Product-Manager-Skills`: CC BY-NC-SA 4.0 (non-commercial), so nothing was copied (checked for the
  sibling skill).
- GitHub repository searches for "user research synthesis skill", "interview synthesis claude skills" and "ux research
  skills SKILL.md" (2026-10-12) found no other permissively licensed per-study synthesis skill.
- Granola: no official open-source skills or prompts were found.
