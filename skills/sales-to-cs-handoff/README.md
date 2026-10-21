# Sales-to-CS Handoff

When a deal closes, this skill reads the account's sales meetings in Granola, plus the CRM deal and email when they're connected. It writes the handoff doc a CSM or implementation lead needs on day one:

- why they bought, in their words
- success criteria and milestones
- what sales promised in those meetings and emails, each with a verbatim quote and its source (the meeting's note link, or the email), flagged when terms conflict between meetings, when they're non-standard or roadmap, or when they're not in the contract
- a stakeholder map that flags people who appeared once or went quiet
- technical requirements and open objections
- a don't-ask-again list
- a coverage section that says what was read and what wasn't

It can also draft a CRM note and a Slack post for your CS channel. Neither is written or posted until you approve it. Values it prints are escaped for markdown, Slack or the CRM note, so a meeting title or a quote can't add a link, an autolink (a URL, `www.` or a bare domain is broken up with escapes or look-alike characters), a mention, or formatting, math (`$…$`) or highlight (`==…==`) in the doc or the Slack post (the CRM note escapes links and HTML but leaves `*` and `_` as written); the only links are the ones it builds from http(s) note and doc URLs. The agent copies the quotes; the script does not check them against the meeting text. It removes the email addresses and phone numbers it recognises. Phone removal is pattern-based and acts only on a clear signal (a leading `+`, a word such as "call" or "mobile" right before the number, or a standard format such as `(555) 123-4567`, `415 555 0134` or `07700 900123`), tested price-list patterns stay, but ambiguous numeric content can be misclassified and unusual formats can slip through; keep contact details out of what you paste in.

Pairs with [Pre-Call Briefing](https://type.com/library/skills/pre-call-briefing) (before a call) and [Call Debrief](https://type.com/library/skills/call-debrief) (after one call).

## Set up in Type

1. In [Type](https://type.com), open **Integrations** and connect **Granola**, then add it to the Space where you'll run this skill. Use the Granola account your sales team records with. Team Space notes and transcripts need a paid Granola plan; the free plan only shows the last 30 days.
2. Optional: connect your CRM (HubSpot, Attio or Salesforce), Gmail or Outlook, and Slack.
3. Add this skill to the Space and ask: "write the handoff for Quillfen Freight". On the first run it confirms which tools to use and your company's email domain, and saves them in `sales-to-cs-handoff-settings.md`.
4. For hands-off handoffs, add an automation: run on a closed-won deal event, or daily. Each scheduled handoff is logged as pending, and once the agent confirms the doc was saved (and the post sent) that deal is not handed off again. A run that finds a pending handoff stops instead of posting it twice.

Other questions it answers: "what did sales promise Quillfen?" and "onboarding brief for Quillfen".

## Outside Type

The skill works in any agent that can run Python 3.8+ and reach Granola through its MCP server (`https://mcp.granola.ai/mcp`), such as Claude or Cursor with Granola connected. Put all the files in one folder, run `bash run.sh` and check the output matches `expected_output.txt`, then follow SKILL.md. Without Granola, paste or attach call transcripts and use `meeting_notes: upload`.

## Files

| File | What it is |
| --- | --- |
| `SKILL.md` | The workflow the agent follows |
| `DATA_CONTRACT.md` | The JSON bundle the agent builds from Granola, the CRM and email |
| `build_handoff.py` | Deterministic renderer: validation, dedupe, conflicts, coverage (stdlib only) |
| `handoff.json` | Thresholds, labels and the handoff checklist |
| `sample_bundle_quillfen.json`, `sample_handoff_log.md` | Fictional sample data (invented companies, `.example` domains) |
| `run.sh`, `expected_output.txt` | Runs the sample; the output must match byte for byte |
| `tests.sh` | Test suite; prints `PASS n/n` |
| `NOTICE.md`, `LICENSE.txt` | Credits for the MIT-licensed material adapted here |

Round 8 contact cues also include named possessives (straight or curly apostrophes), my/his/her/their, personal/direct/best/work/home, line-start Number, and is/'s/:/- separators. Spaced US country code `1` is recognized with a valid area/exchange shape. Short counts, business identifiers and the tested H7 price/year lists are retained. This remains pattern-based, not complete contact detection.
