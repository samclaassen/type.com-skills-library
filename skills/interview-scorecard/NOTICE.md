# NOTICE

`interview-scorecard` is in part an adapted work. Type (type.com) built it to
turn Granola interview notes into rubric-based scorecards and panel debriefs,
using the open-source sources below. The full license text is in `LICENSE.txt`.

## Sources

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `human-resources/skills/interview-prep/SKILL.md` | [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (repository root `LICENSE`; the plugin folder has no license file of its own) |
| `small-business/skills/job-post-builder/reference/interview-guide-structure.md` | same @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (as above) |
| `small-business/skills/hiring-screener/reference/fair_screening.md` | same @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (as above) |
| `small-business/skills/hiring-screener/reference/rubric.md` | same @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (as above) |
| `small-business/skills/hiring-screener/reference/gotchas.md` (the "screening without a rubric" and "scoring something the rubric does not name" items) | same @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` | Anthropic | Apache License 2.0 (as above) |

Commit `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` (committed 2026-10-07,
"bump(figma): 5d718a28 → f0493295 (#1287)") was upstream `HEAD` when the
sources were retrieved on 2026-10-08.

About the license text: the repository root `LICENSE` at this commit is the
Apache License 2.0 followed by about ten lines of unrelated stray text after
the appendix. `LICENSE.txt` in this package reproduces `marketing/LICENSE`
from the same commit instead, which matches the root file line for line up to
the end of the appendix, without the stray text.

## Modifications (Apache-2.0 §4(b): statement of changes)

`rubric-guide.md` combines and rewrites the five upstream files above:
- From `interview-prep/SKILL.md`: the five interview design principles, the
  4–6 competency guidance and the behavioural/situational question mix,
  reworded for a per-role rubric file.
- From `interview-guide-structure.md`: per-role behavioural anchors, follow-up
  probes, and the debrief guide (scores shared before discussion, focus on
  divergent scores, disqualifying signals kept separate). Upstream's 1/3/5
  anchors on a 1–5 scale became anchors for every score on a 1–4 scale, and
  divergence is defined as a spread of 2 or more.
- From `rubric.md`: "write the rubric before reading candidates" (applied to
  interviews), must-have discipline, and "writing a criterion so it can be
  scored" (bad/good examples rewritten for a finance operations role;
  "culture fit" restated as behaviour). Upstream's resume-screening weights,
  0–3 scale and weighted-percentage formula were dropped: this skill does not
  weight or total scores.
- From `fair_screening.md`: "score only against the rubric", the off-limits
  table (adapted from resumes to interviews; added pregnancy/family status,
  sexual orientation, gender identity and disability rows, and the
  accommodation and language-skill notes), "a score with no quoted evidence is
  an impression", "do not infer" (upstream's dash became "No evidence — not
  assessed"), and "what the owner still decides" (now the interviewer and
  panel). The anonymisation pass and the "cannot assess" e-mail band were dropped.
- From `gotchas.md`: the two failure modes named above, folded into the rules.
- Added: handling of questions of concern (topic only, never the answer).
- Revised 2026-10-09 (Type, after QA review): the off-limits table gained
  rows for genetic information and family medical history, military or
  veteran status, union membership, and hiring-decision language ("strong
  hire", "make an offer") kept out of scorecards; existing rows now name
  graduation years ("class of …"), visa and immigration status, caring for a
  relative, and HBCUs and women's colleges as proxies; the debrief section
  notes that the script hides scores until every interviewer has filed.

`SKILL.md` is new Type text. Its rubric gate, "score only against the rubric",
no-inference rule, off-limits list and debrief practice follow the upstream
material above; everything about Granola, the bundle, consent, scheduling,
delivery and ATS drafts is new. Upstream's Claude-specific tool references
(`allowed-tools`, `docx` skill, DocuSign/Chrome routing, `../../shared/*`
links) were not carried over.

`SKILL.md`, `DATA_CONTRACT.md` and `README.md` were revised on 2026-10-09 so
every claim matches what the script enforces (keyword-based fairness and
hiring-decision check, required recording disclosure, panel readiness, the
two-phase scheduled log, Granola tool parameters).

Revised again on 2026-10-09 (round-2 QA): the hiring-decision check became a
sentence-level heuristic over keyword patterns, the term lists use person
context for ambiguous words, a withdrawn evidence item withdraws its score,
and the ATS draft carries no agent-written text. `SKILL.md`, `DATA_CONTRACT.md`,
`README.md` and `rubric-guide.md` were updated to match (keyword and pattern
lists plus a heuristic, with stated limits); `rubric-guide.md` gained a
`filed` note in the debrief section and a sentence on what the scanner can
and cannot catch.

Revised 2026-10-13 (round-5 QA): evidence items carry an `evidence_kind`
(behaviour example, skill demonstration, answer to a planned question,
observation of work output) and untagged items are not shown; every evidence
item gets the same fairness and hiring-decision checks whoever said it;
subject-less fragments and near-misspellings are read as about the candidate;
pay figures are withheld; a skipped interview can't be confirmed as delivered.
The agent copies quotes; the script can't verify them, because bundles carry
no transcript or note text. `SKILL.md`, `DATA_CONTRACT.md`, `README.md` and the
listing say the same.

New files written by Type (not from upstream): `build_scorecard.py`,
`scorecard.json`, `scanner_corpus.json`, `DATA_CONTRACT.md`, `README.md`, `rubric_template.json`,
`sample_rubric_finance_ops_analyst.json`, `sample_bundle_single.json`,
`sample_bundle_panel.json`, `sample_scorecard_log.md`, `run.sh`, `tests.sh`,
`expected_output.txt` and this `NOTICE.md`. All sample people and quotes are
fictional.

Packaging: the upstream license is shipped as `LICENSE.txt` (text unchanged)
so Type's skill installer accepts it.

## Other repositories checked (nothing copied)

- `anthropics/skills` @ `683bc88e56f3e09ba94f7055977f3d3aa499f202`: no hiring,
  interview or scorecard skill.
- `ComposioHQ/awesome-claude-skills` @ `be2a406907dbc61b73e6827ded415c96139d13a2`:
  only an Ashby API automation skill; no scorecard method, no root license file.
- `alirezarezvani/claude-skills` @ `19392f7a08264ed00486a251f5b2098321771f94` (MIT)
  and `wshobson/agents` @ `46891e7e60da0e52baf1050b7b6391b64e84c6d9` (MIT):
  general HR advisor prompts; no interview-scorecard workflow worth adapting.
- `VoltAgent/awesome-claude-code-subagents` @ `721e9734670bfaf7283194e234ebb88e94c82dcd`: nothing relevant.
- Granola: no public skill or prompt repository for interview scorecards was found.

## Round-12 export-contract revision (2026-10-15)

Current behavior supersedes historical raw-prose screening and suggested-score descriptions above. Source excerpts, rationale and gap prose are input-only; deterministic exports use bounded action/object/result labels and source pointers. Ratings are independently entered by interviewers. Candidate/job/interviewer metadata and the user-approved rubric remain. New projection, validation and scheduling tests replace assertions that benign raw source sentences must print. The agent copies quotes; the script can't verify them. No live integration verification is claimed.
