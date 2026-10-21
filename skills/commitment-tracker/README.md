# Commitment Tracker

Finds the promises made in your meetings and checks whether they were kept.
It covers what you and your team owe people ("I'll send the SOW by Wednesday")
and what customers and partners owe you. Each one comes with its verbatim
quote (the agent copies each quote; the script can't verify the wording), a
meeting link and a due date: stated, or inferred from "by Friday" and clearly
marked as inferred. It then looks for proof of delivery in sent
mail, Slack, Linear or Jira, your calendar and later meetings, and posts one
digest: Overdue, Due soon, Open with no date, They owe us, and Done since last
run. A state file keeps your dismissals and stops the digest repeating
itself: a scheduled digest counts as posted only once the post is confirmed,
so a failed post can be retried, and a schedule that fires again in the same
week (or, for a daily digest, the same day) posts nothing. It never says
"nothing to report" unless every meeting was read: when the meeting source,
the window's note history, a meeting whose read failed or every meeting was
unreadable it says Incomplete, and it names each meeting it couldn't read. A
meeting whose read failed is retried by the next scheduled runs (2 more runs
or 3 days, whichever comes first, set in `commitments.json`); after that the
digest posts normally and names it as not covered, and it is re-read if it
becomes readable later. Items an Incomplete digest announced are never
flagged new again. Read-only;
follow-up emails are drafted only when you approve them, and never sent.

## Set it up in Type

1. In [Type](https://type.com), open **Integrations** and connect **Granola**.
   Add it to the Space where you'll run this skill.
2. Optional, for completion checks: connect Gmail or Outlook, Slack, Linear or
   Jira, and Google or Outlook Calendar. Each one you add makes "done" more
   reliable. Missing ones are listed under Gaps in every digest.
3. Add the **Commitment Tracker** skill to the Space and ask: "what did I
   promise people this week?" The first run asks you to confirm your company's
   email domain, sources and where to post (Step 0 in `SKILL.md`).
4. For a recurring digest, create an automation in **Space settings →
   Automations** (weekly on Friday afternoon, or daily on weekday mornings) and
   enable it. Each run asks `--next-window` where the last posted digest ended
   (and whether this week's is already out), posts, and then confirms the post
   (`--confirm-posted`), as described in the automation recipe in `SKILL.md`.

No Granola? Transcripts from Gong, Fathom, Fireflies, Otter or Zoom work the
same way, as do transcripts you paste or upload.

## Outside Type

Any agent that can call Granola's MCP server (for example Claude or Cursor
with the Granola MCP connected) and run Python 3.8+ can use this skill. Put
all files in one folder, run `bash run.sh` and check it matches
`expected_output.txt`, then follow `SKILL.md`. Use your own mail, Slack and
tracker tools for the read-only completion checks. Keep `commitment-state.json`
somewhere that persists between runs. Use `--format slack` when the digest goes
to Slack.

## Files

| File | What it is |
| --- | --- |
| `SKILL.md` | The workflow the agent follows |
| `DATA_CONTRACT.md` | The JSON bundle, state file and config, field by field |
| `build_commitments.py` | Deterministic validator, merger, due-date resolver and renderer (stdlib only) |
| `commitments.json` | Thresholds, business days, holidays, plan history limits |
| `sample_bundle_week1.json`, `sample_bundle_week2.json` | Fictional two-week sample |
| `run.sh`, `expected_output.txt` | Sample run and its exact expected output |
| `tests.sh` | Guardrail and edge-case tests (`PASS n/n`) |
| `NOTICE.md`, `LICENSE.txt` | Open-source attribution (MIT) |
