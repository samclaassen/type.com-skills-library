# User Research Synthesis

Synthesise one study's user interviews from Granola against its research questions. You get findings per question
with a confidence band based on how many **different participants** support or contradict each one, quotes
linked to their meetings, jobs to be done, pains, workarounds and desired outcomes, differences by segment, surprises,
opportunities ranked by the evidence behind them, a pseudonymous participants table and a "what we still don't know"
list. Optional JSON and an insight-card CSV for a research repository.

Participants appear only as P1, P2… Names, organisations and emails the agent lists for the study, plus email
addresses, phone numbers and web addresses found by pattern, are replaced in every displayed field. That redaction is
pattern-based and can miss unlisted names, nicknames and misspellings, so read the report before sharing it.
Participants are quoted only with consent; those who declined are left out. The agent extracts and codes the evidence;
the script checks every citation against the bundle, counts participants, computes the bands and renders the report.
It checks every quote against the meeting text it was given: a quote must be one complete sentence, or several
consecutive complete sentences, from a single speaker's turn, ignoring case and formatting. The check is mechanical;
it doesn't judge whether a quote is representative, in context, or correctly attributed.

Example output: `bash run.sh` (fictional data) or `expected_output.txt`.

For the weekly, account-level digest of customer calls, see the sibling skill `customer-feedback-digest`.

## Set up in Type

1. In **Integrations**, connect **Granola**, and add it to the Space where the research lives. A paid Granola plan
   lets the skill list a study's folder directly; on a free plan, give it the meeting links instead.
2. Add this skill to the Space and ask: "synthesise the onboarding interviews" or "what did we learn from these 8
   user interviews?". The first run confirms the source, your internal email domains and where consent is recorded,
   and saves them in `user-research-synthesis-settings.md`. Each run then agrees the research questions, segments and
   consent with you before reading anything.
3. The report is written to a Type doc in the Space. Nothing is posted or imported anywhere else unless you ask for
   that destination.

Runs on request only; there is no schedule and no state between runs.

Setup help: https://type.com

## Outside Type

The script is plain Python 3.8+ with no dependencies, so the skill also works in any agent that can read Granola
through Granola's MCP server (for example Claude or Cursor with the Granola connector), or with pasted transcripts:

1. Put all files from this skill in one folder and run `bash run.sh`; it should match `expected_output.txt`.
2. Follow `SKILL.md`: list the study's meetings with Granola's `list_meetings`, read notes with `get_meetings` and
   transcripts with `get_meeting_transcript`, code the evidence as `research-coding-guide.md` describes, build the
   bundle in `DATA_CONTRACT.md`, then run
   `python3 build_synthesis.py --bundle bundle.json --as-of <today>`.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | The workflow the agent follows. |
| `research-coding-guide.md` | How to code evidence and build findings. |
| `DATA_CONTRACT.md` | The JSON bundle the script reads, field by field, and what the script does with it. |
| `build_synthesis.py`, `synthesis.json` | Deterministic script and its thresholds. |
| `granola_text.py` | Shared quote-check, contact-redaction and escaping helpers (a byte-for-byte copy used by Type's Granola skills). |
| `sample_bundle.json` | Fictional sample study. |
| `run.sh`, `expected_output.txt`, `tests.sh` | Sample run, its exact output, and the test suite (`bash tests.sh`). |
| `NOTICE.md`, `LICENSE.txt` | Attribution for the adapted Apache-2.0 material. |
