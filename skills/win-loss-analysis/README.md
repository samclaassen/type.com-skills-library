# Win/Loss Analysis

A win/loss report for the deals you closed won and closed lost in a period, built from what customers actually said
on your Granola calls and what your CRM recorded. It reads every call with each closed deal's account and reports
the top reasons you win and lose, counted by **distinct deals** (optionally weighted by deal amount), with customer
quotes and meeting links; our head-to-head win rate against each competitor and what customers said about each competitor; where
losses happen by stage and segment; objections that came up before losses; and no-decision losses kept apart from
competitive ones. Groups with fewer than 5 deals show counts, not percentages, and deals with no call coverage are
listed as gaps. A deal with no stated reason is shown as "no recorded reason", never guessed.

The agent reads the calls and classifies what customers said using an editable reason taxonomy
(`reason_taxonomy.json`); the script checks every reason cites a deal and a meeting or CRM field in the bundle, counts and renders. When the
meeting text is included, the script checks every quote against it: a quote must be one complete sentence, or several
consecutive complete sentences, from a single speaker's turn (a CRM-field quote may also be the whole field value).
The check ignores case and formatting; partial sentences, ellipses, speaker labels and quotes that run across
speakers are rejected. The check is mechanical: it confirms the words appear as whole sentences. It doesn't judge
whether a quote is representative, in context, or correctly attributed. Email addresses and phone numbers are
redacted before display; redaction is pattern-based and can miss formats.

Example output: `bash run.sh` (fictional data) or `expected_output.txt`.

## Set up in Type

1. In **Integrations**, connect **Granola**, and add it to the Space where the report should live.
2. Connect your CRM (HubSpot, Attio, Salesforce or Pipedrive), or have a CSV export of closed deals ready. Optional:
   Slack, for a summary post after you approve it.
3. Add this skill to the Space and ask: "why did we lose deals last quarter?" or "win/loss review for Q3". The first
   run confirms your CRM, your internal email domains and the reason categories, and saves them in
   `win-loss-analysis-settings.md`.

It runs on request only: no schedule, no state, and it never writes to your CRM. Setup help: https://type.com

## Outside Type

The script is plain Python 3.8+ with no dependencies, so the skill also works in any agent that can read Granola
through Granola's MCP server and your CRM (for example Claude or Cursor with the Granola connector):

1. Put all files from this skill in one folder and run `bash run.sh`; it should match `expected_output.txt`.
2. Follow `SKILL.md`: pull the closed deals from the CRM (or use a CSV export), list the meetings with Granola's
   `list_meetings`, read notes with `get_meetings` and quotes with `get_meeting_transcript`, build the bundle
   described in `DATA_CONTRACT.md`, then run
   `python3 build_winloss.py --bundle bundle.json --as-of <now> --internal-domain <your domain>`
   (add `--deals-csv deals.csv` for a CSV deal list).

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | The workflow the agent follows. |
| `win-loss-guide.md` | How to extract and classify reasons, objections and competitor mentions. |
| `DATA_CONTRACT.md` | The JSON bundle and CSV the script reads, field by field. |
| `build_winloss.py`, `winloss.json` | Deterministic script and its config. |
| `granola_text.py` | Shared text helpers (quote check, contact redaction, markdown and Slack escaping); an unedited copy of the file used across Type's Granola skills. |
| `reason_taxonomy.json` | Default reason categories; edit a copy to fit your business. |
| `sample_bundle.json`, `sample_bundle_csv.json`, `sample_deals.csv` | Fictional sample input. |
| `run.sh`, `expected_output.txt`, `tests.sh` | Sample run, its exact output, and the test suite (`bash tests.sh`). |
| `NOTICE.md`, `LICENSE.txt` | Attribution for the adapted Apache-2.0 material. |
