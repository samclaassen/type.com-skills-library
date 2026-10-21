# Customer Feedback Digest

A weekly voice-of-customer digest built from your Granola meeting notes. It reads the external customer and prospect
calls Granola lists for the period (and says when that list is incomplete), pulls out feature requests, bugs,
usability friction, buying objections, churn-risk signals, competitor mentions and praise with verbatim quotes, and
ranks themes by how many **different accounts** raised them. It compares with last week, links each theme to
existing Linear, Jira or GitHub issues, and drafts new issues that are only created when you approve them.
Emails and phone numbers that slip into quotes are replaced by a pattern-based redaction that can miss unusual
formats, so the extraction step still leaves contact details out.

Example output: `bash run.sh` (fictional data) or `expected_output.txt`.

## Set up in Type

1. In **Integrations**, connect **Granola**, and add it to the Space where the digest should live.
2. Optional: connect a CRM (HubSpot, Attio or Salesforce) for account status, plan and ARR; Stripe for ARR; an issue
   tracker (Linear, Jira or GitHub) for issue matching; Slack for the weekly post.
3. Add this skill to the Space and ask: "what are customers asking for this week?" The first run confirms which
   sources to use, your internal email domains, and where to post, and saves them in
   `customer-feedback-digest-settings.md`.
4. For the weekly version, add an automation in **Space settings → Automations**: every Monday morning, run this skill
   for last week. Each week is recorded as pending in a state file and marked posted only after the post is
   confirmed; a rerun of a pending or posted week does not print the digest again unless told to. That guards
   against double posts but cannot rule them out if a post succeeds and the confirmation never runs.

Setup help: https://type.com

## Outside Type

The script is plain Python 3.8+ with no dependencies, so the skill also works in any agent that can read Granola
through Granola's MCP server (for example Claude or Cursor with the Granola connector):

1. Put all files from this skill in one folder and run `bash run.sh`; it should match `expected_output.txt`.
2. Follow `SKILL.md`: list the period's meetings with Granola's `list_meetings`, read notes with `get_meetings` and
   quotes with `get_meeting_transcript`, build the bundle described in `DATA_CONTRACT.md`, then run
   `python3 build_digest.py --bundle bundle.json --as-of <now> --internal-domain <your domain>`.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | The workflow the agent follows. |
| `feedback-extraction-guide.md` | How to extract feedback items and build themes. |
| `DATA_CONTRACT.md` | The JSON bundle the script reads, field by field. |
| `build_digest.py`, `digest.json` | Deterministic script and its config. |
| `sample_bundle.json`, `sample_state.json` | Fictional sample input and history. |
| `run.sh`, `expected_output.txt`, `tests.sh` | Sample run, its exact output, and the test suite (`bash tests.sh`). |
| `NOTICE.md`, `LICENSE.txt` | Attribution for the adapted Apache-2.0 material. |
