# Investor Update Drafter

Drafts a founder's monthly or quarterly investor (or board) update from the period's Granola meetings, the metrics
you supply or read from Stripe or a metrics file, and last period's update. The draft follows the standard format
(TL;DR, key metrics with period-over-period change, highlights, lowlights, product, customers and go-to-market, team
and hiring, asks, and what we said last time vs what happened) and ends with a "Before you send" checklist.

The agent extracts the highlights with links to their meetings; the script does the checkable parts. It prints a
metric figure only from a metrics row with a source and an as-of date, computes every change, names a customer or person
only when marked `public_ok`, leaves confidential items out of investor drafts (a board draft keeps them in a
separate section), and withholds text with figures from meetings or promises the founder didn't make (a
forward-looking item, including a present-tense plan with a later date such as "launches in March", must be the
founder's own sentence, word for word, from the meeting text, and is withheld unless it and the founder's surrounding
words match a bounded grammar, with detected later qualifications held for review). Assertions without a
founder statement need a recognised past or current-status predicate. Those text checks
are pattern-based, so the founder still reviews every draft. The script checks every quote against the meeting text
it was given: a quote must be one or more complete sentences from a single speaker's turn. The check is mechanical;
it doesn't judge whether a quote is representative or in context. Nothing is sent: the only write the skill
describes is a Gmail draft after the founder approves the text.

Example output: `bash run.sh` (fictional data) or `expected_output.txt`.

## Set up in Type

1. In **Integrations**, connect **Granola** and add it to the Space where you'll draft updates.
2. Optional: connect **Stripe** (MRR, ARR, customers), **Gmail** (to find last period's update and, after approval,
   save a draft) and **Google Drive** (a metrics sheet for cash, burn, runway, headcount).
3. Add this skill to the Space and ask: "draft this month's investor update". The first run confirms the sources,
   the cadence, and which customers may be named, and saves them in `investor-update-drafter-settings.md`.

The skill runs on request only. Setup help: https://type.com

## Outside Type

The script is plain Python 3.8+ with no dependencies, so the skill also works in any agent that can read Granola
through Granola's MCP server (for example Claude or Cursor with the Granola connector):

1. Put all files from this skill in one folder and run `bash run.sh`; it should match `expected_output.txt`.
2. Follow `SKILL.md`: list the period's meetings with `list_meetings`, read them with `get_meetings`, collect the
   metrics, build the bundle described in `DATA_CONTRACT.md`, then run
   `python3 build_update.py --bundle bundle.json --metrics-csv metrics.csv --as-of <now>`.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | The workflow the agent follows. |
| `update-writing-guide.md` | How to write the items: sections, status words, bad news, and the rules the script enforces. |
| `DATA_CONTRACT.md` | The JSON bundle and metrics CSV the script reads, field by field, and the exit codes. |
| `build_update.py`, `update.json` | The deterministic script and its config (metric definitions, rounding, term lists). |
| `granola_text.py` | Shared Granola text helper (quote check, contact redaction, markdown escaping), shipped unchanged; `build_update.py` loads it from its own folder. |
| `sample_bundle.json`, `sample_metrics.csv` | Fictional sample input. |
| `test_founder_context_r12.py` | Positive subject/predicate grammar, later retractions, relative-date facts, role annotations and paired clean controls through four output representations. |
| `test_founder_context.py`, `founder_context_independent.json` | Founder-context final-output regressions and independent pre-tuning cases. |
| `test_founder_context_r9.py`, `founder_context_r9_cases.json` | Founder-context regressions run through every output format: disclosed review strings and the repair owner's tuning cases (both labelled; neither is unseen validation), each withheld case with a safe twin that prints. |
| `test_founder_context_r10.py`, `founder_context_r10_cases.json` | Round-10 founder-context regressions in four outputs (JSON email body and markdown, investor draft, board email): every round-9 review repro string, and the round-10 repair owner's fresh cases (run once before tuning, then kept as regressions), each withheld case with an eligible twin that prints. |
| `run.sh`, `expected_output.txt`, `tests.sh` | Sample run, its exact output, and the test suite (`bash tests.sh`). |
| `NOTICE.md`, `LICENSE.txt` | Attribution for the adapted MIT-licensed material. |
