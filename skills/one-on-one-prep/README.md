# 1:1 Prep

A private, one-page prep for a recurring 1:1, from either side of the table. It reads your last few 1:1s with one
person in Granola, plus Linear or Jira issues and Slack threads if you connect them. It lists:

- commitments both ways, each with the words that were said, the date and a link, and whether it's open or likely done (with the evidence)
- topics that keep coming back without a resolution
- goals and projects raised, and where they stand now
- feedback the manager promised
- questions nobody answered
- a suggested agenda that starts with the report's topics

It is **not a performance review**, and it is a private draft for you to read before sharing any of it. Two gates
keep personal things out. First, the agent files every item under a work topic (project, goal, process, customer,
team logistics, feedback about work, career growth, a question about work); anything filed as personal or other, or
not filed at all, is never shown. This is the main control and depends on the agent categorising honestly. Second, a
pattern-based backstop withholds wording about health, family, money, legal or HR matters and leaves out wording that
rates or judges anyone or describes mood. A planned dismissal, exit or replacement of anyone (the other person or a third party) counts as personal even when it looks like hiring or a backfill, and so do accommodations and pay requests; the agent must file these as personal, and the backstop catches only some wordings. It can miss misspellings, slang and paraphrase. The prep says "1 item
withheld as personal or uncategorised" and never shows the text. Nothing is posted anywhere. Read `SKILL.md` → Limits.

The script checks every quote against the meeting text it was given. A quote must be one complete sentence, or
several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting. Partial
sentences, ellipses, speaker labels and quotes that run across speakers are rejected. The check is mechanical: it
doesn't judge whether a quote is representative, in context, or correctly attributed. When a meeting's text isn't in
the bundle, the script can't check its quotes (it only drops those with a speaker label, an ellipsis or under 3
words). E-mail addresses and phone numbers are redacted before any text is shortened; redaction is pattern-based and
can miss formats.

## In Type

1. Connect **Granola** in Integrations. Optionally connect Linear or Jira, Slack, and Google or Outlook Calendar (the calendar is only needed to run the prep automatically before a 1:1).
2. Add this skill to a **private** Space or your Sidekick, and say "prep my 1:1 with Riley". The first run confirms your sources and your work e-mail.
3. Optionally, create an automation that runs before calendar events with exactly two attendees. See `SKILL.md` → Automation recipe.

## Outside Type

Any agent that can read Granola through its MCP server (for example Claude or Cursor) can follow `SKILL.md`. It
uses the same read-only Granola tools: `list_meetings`, `get_meetings`, `get_meeting_transcript` and
`query_granola_meetings`. The script needs Python 3.8+ and nothing else:

```bash
bash run.sh      # the fictional samples; must match expected_output.txt
bash tests.sh    # guardrail and edge-case tests, including rendered OO10 regression cases; prints PASS n/n
python3 build_prep.py --bundle bundle.json --as-of 2026-10-13T09:30:00-07:00
```

Files: `SKILL.md` (workflow and guardrails), `DATA_CONTRACT.md` (bundle, config and output), `build_prep.py`,
`granola_text.py` (shared quote check, redaction and escaping; a byte-for-byte copy, don't edit it),
`prep.json` (settings and pattern lists), `scanner_corpus.json` (test sentences), three fictional sample bundles,
`run.sh`, `expected_output.txt`, `tests.sh`, `test_regressions.py`, `regression_cases.json`, `NOTICE.md` and `LICENSE.txt` (Apache-2.0, for the adapted parts).
