# Decision Log

Keeps a running log of what your team decided in meetings. Each week (or when
you ask) it reads your Granola meetings, logs each decision it finds with who made it,
who owns it, the date, the meeting link, the reason given, the options
rejected and a quote (lines from Granola's AI summary are labelled as such), and keeps proposals that were only suggested in
a separate "open proposals" list until someone actually decides them. It flags
decisions that reverse an earlier one (citing both meetings), possible
conflicts on the same topic, decisions nobody owns, and revisit dates that
have passed. Ask "when did we decide X, and why?" and it answers from the log,
with the meeting link.

The agent reads the meetings and judges what was decided; the script checks
every entry cites a meeting in the run (and, when the meeting text is
supplied, that each quote is one complete sentence, or several consecutive
complete sentences, from a single speaker's turn; the check is mechanical and
doesn't judge whether a quote is representative or in context), merges it into the log with stable ids, applies the
flag rules and renders the report. A weekly report counts as posted only once
the post is confirmed, so a failed post can be retried and a week is not
posted twice; a week whose meetings couldn't be read is reported as
Incomplete, never as "no decisions". Read-only on Granola.

## Set it up in Type

1. In [Type](https://type.com), open **Integrations** and connect **Granola**.
   Add it to the Space where you'll run this skill.
2. Optional: connect Slack if the weekly report should go to a channel.
3. Add the **Decision Log** skill to the Space and ask: "what did we decide
   this week?" The first run asks you to confirm the meeting source, an
   optional Granola folder or Team Space, where the log lives (a JSON file,
   optionally mirrored to a Type doc) and where to post (Step 0 in
   `SKILL.md`).
4. For a weekly report, create an automation in **Space settings →
   Automations** (Fridays at 4pm works well) and enable it. Each run asks
   `--next-window` for its window, posts, then confirms the post
   (`--confirm-posted`), as described in the automation recipe in `SKILL.md`.

No Granola? Transcripts from Gong, Fathom, Fireflies, Otter or Zoom work the
same way, as do transcripts you paste or upload.

## Outside Type

Any agent that can call Granola's MCP server (for example Claude or Cursor
with the Granola MCP connected) and run Python 3.8+ can use this skill. Put
all files in one folder, run `bash run.sh` and check it matches
`expected_output.txt`, then follow `SKILL.md`. Keep `decision-log.json`
somewhere that persists between runs. Use `--format slack` when the report
goes to Slack.

## Files

| File | What it is |
| --- | --- |
| `SKILL.md` | The workflow the agent follows |
| `DATA_CONTRACT.md` | The bundle, the log file and the config, field by field |
| `build_decision_log.py` | Deterministic validator, merger, flagger and renderer (stdlib only) |
| `granola_text.py` | Shared text helpers (quote check, markdown and Slack escaping), copied unchanged from the Granola skills' shared module |
| `decision_log.json` | Plan history limits, quote length, retention and search settings |
| `sample_bundle_week1.json`, `sample_bundle_week2.json` | Fictional two-week sample |
| `run.sh`, `expected_output.txt` | Sample run and its exact expected output |
| `tests.sh` | Guardrail, fuzz and scenario tests (`PASS n/n`) |
| `NOTICE.md`, `LICENSE.txt` | Open-source attribution (MIT) |
