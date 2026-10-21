# Interview Scorecard

Turns Granola interviews into draft scorecards against your hiring team's approved rubric. It records source-linked work observations, shows interviewer-entered 1–4 ratings, identifies gaps and unasked planned questions, and compares filed ratings across a panel.

The agent extracts supported action/object/result codes. The script renders a limited vocabulary, with the source kind, row, link and available timestamp. Raw evidence, free-text rationales and gap notes never enter Markdown, JSON or Greenhouse/Ashby/Lever drafts. Candidate name, role, interviewer metadata and the actual rubric remain visible. No numeric rating is inferred; the hiring recommendation stays empty.

The agent copies quotes; the script can't verify them. A controlled observation may still misrepresent its source: the interviewer must review each source against the rubric and enter their own rating. Metadata/rubric checks are heuristic. The code vocabulary cannot describe every job or preserve every nuance; unsupported work requires direct source review. No approval flag makes raw prose exportable.

## Set up

Connect Granola in Type and add it to the hiring Space. Optional ATS connections support drafts; nothing is sent without the interviewer's approval. Ask “set up interview scorecards” and have a hiring manager approve a rubric before the interviews. Start from rubric_template.json and rubric-guide.md.

Ask “write up my interview with Jordan” or “panel debrief for Jordan”. Each draft is private to the interviewer. The panel sees ratings only once every interviewer has filed; it then shows disagreements and competencies nobody assessed.

Optional scheduled runs use a durable listing window and pending/delivered log. They retry unreadable or undelivered interviews without silently losing them. See SKILL.md for --next-window, --mark-listed and delivery confirmation.

## Run offline

Python 3.8+; no dependencies. Keep files together and run `bash run.sh`; output must match expected_output.txt. Assemble the DATA_CONTRACT.md bundle, then run:

```bash
python3 build_scorecard.py --rubric sample_rubric_finance_ops_analyst.json --bundle sample_bundle_single.json --as-of 2026-09-29
bash tests.sh
```

Use `PYTHON=/path/to/python` to select an interpreter. These commands perform no API calls or delivery.

## Contract migration

Round 12 replaces heuristic export of unrestricted source prose with bounded work observations. Text-only legacy evidence is held with a reason. Suggested ratings are held for the interviewer; they are never auto-confirmed. Add observations only where the source supports the exact codes, then collect the human's own ratings and reference observation IDs. A legacy rationale or gap note remains input-only even if confirmed.

SKILL.md gives the agent workflow; DATA_CONTRACT.md gives exact keys and codes; TEST_MIGRATION.md records which old rendering assertions were replaced and which schema/scheduler checks remain. test_structured.py exercises original adversarial/safe source strings through actual Markdown, JSON and all ATS renderers. test_validation.sh retains validation and durable-delivery checks. Sample bundles, rubric, log, run.sh and expected_output.txt are fictional examples. NOTICE.md and LICENSE.txt retain attribution.

The repair was tested offline. No live Granola or ATS read/write verification is claimed.
