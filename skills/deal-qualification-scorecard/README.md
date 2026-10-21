# Deal Qualification Scorecard

Ask "qualify the Ostrevane deal", "MEDDICC check on Ostrevane" or "what are we missing on Ostrevane?" and this skill reads every sales meeting with the account in Granola, plus the CRM deal if one is connected, and scores the deal against a qualification framework: MEDDICC by default, with MEDDPICC, BANT and SPICED included, or your own framework file.

For each element you get:

- a status: evidenced, partial, missing or contradicted
- quotes with the meeting link and date, and whether the customer or our side said it (the agent's judgement)
- what's still missing
- one or two questions to ask on the next call

A **Fix first** list calls out conflicting answers across meetings (two decision dates, two champions), elements only our side asserted, stale evidence and CRM fields that disagree with the calls. You can also get a CRM field-update draft, which nothing writes until you approve each line, a JSON version, or one table across a batch of open deals.

The agent reads the meetings and extracts the evidence; the script checks it, merges duplicate notes, leaves out internal-only meetings, computes statuses and flags, and renders the same output every time for the same input. Every finding must cite a meeting in the bundle. The script checks every quote against the meeting text it was given. A quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting. Partial sentences, ellipses, speaker labels and quotes that run across speakers are rejected. The check is mechanical: it confirms the words appear as whole sentences. It doesn't judge whether a quote is representative, in context, or correctly attributed. Meeting text is escaped so it can't create links, mentions or formatting in the markdown output. Email addresses and phone numbers are redacted before any text is shortened. Redaction is pattern-based and can miss formats. Phone numbers are removed only when there's a clear signal (a + country code, a word like 'call' or 'mobile', or a standard US/UK/French format), so number lists, prices, ranges, dates and reference numbers are left alone.

It is a coverage checklist, not a win prediction: no probabilities, forecasts or weighted scores.

Pairs with [Pre-Call Briefing](https://type.com/library/skills/pre-call-briefing) (before the next call) and [Call Debrief](https://type.com/library/skills/call-debrief) (after one call).

## Set up in Type

1. In [Type](https://type.com), open **Integrations** and connect **Granola**, then add it to the Space where you'll run this skill. Use the Granola account your sales team records with. Team Space notes and transcripts need a paid Granola plan; the free plan only shows the last 30 days.
2. Optional: connect your CRM (HubSpot, Attio, Salesforce or Pipedrive) to compare the deal's close date, champion and economic buyer with what was said on the calls.
3. Add this skill to the Space and ask: "qualify the Ostrevane deal". On the first run it confirms which tools and framework to use and your company's email domain, and saves them in `deal-qualification-scorecard-settings.md`.

It runs on request and keeps no state between runs. Other questions it answers: "MEDDPICC check on Ostrevane", "BANT on the Ketterbrae deal", "score my open deals".

## Outside Type

The skill works in any agent that can run Python 3.8+ and reach Granola through its MCP server (`https://mcp.granola.ai/mcp`), such as Claude or Cursor with Granola connected. Put all the files in one folder, run `bash run.sh` and check the output matches `expected_output.txt`, then follow SKILL.md. Without Granola, paste or upload call transcripts and set `sources.meeting_notes.provider` to `pasted`.

## Files

- `SKILL.md`: the workflow the agent follows.
- `DATA_CONTRACT.md`: the bundle, framework and config formats, every rule the script applies, and the exit codes.
- `build_scorecard.py`: the script (Python 3.8+, standard library only, no network).
- `granola_text.py`: the shared quote check, contact redaction and markdown escaping used by the script, copied unchanged from Type's shared Granola helpers.
- `framework_meddicc.json`, `framework_meddpicc.json`, `framework_bant.json`, `framework_spiced.json`: the frameworks.
- `scorecard.json`: thresholds (stale after 45 days, quotes shown per element, bot and personal-email lists).
- `sample_bundle_ostrevane.json`, `sample_bundle_ketterbrae.json`, `sample_bundle_dunnolark.json`: fictional sample deals.
- `run.sh`, `expected_output.txt`, `tests.sh`: the sample run, its exact output and the test suite.
- `NOTICE.md`, `LICENSE.txt`: what was adapted from open source, and its license.
