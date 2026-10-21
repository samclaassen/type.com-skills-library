---
name: interview-scorecard
description: Turn interviews in Granola into draft scorecards against an approved role rubric, with source-linked work observations, interviewer-entered 1–4 ratings, coverage gaps and unasked questions. Raw evidence, rationale and gap prose never enter exports. Panel mode compares filed ratings and flags disagreements; it hides ratings until every interviewer has filed. Optional Greenhouse, Ashby and Lever drafts leave the hiring recommendation empty. Use for interview write-ups, panel debriefs or scheduled private drafts. Human review of the sources and rubric is required.
---

## What this does

The agent reads notes against the hiring team's approved rubric and extracts bounded work observations. The script renders those observations with source pointers, competency coverage, planned questions and ratings entered by the interviewer. It does not infer numeric ratings or a hiring decision.

The source excerpt stays in the temporary bundle. Its wording, free-text rationales and gap notes are never exported to Markdown, JSON or ATS drafts, even when tagged or marked confirmed. Exports retain the candidate name, role, interviewer, date, approved rubric and meeting links so the draft remains usable. Observation labels are a limited vocabulary, not verbatim quotes.

The agent must decide whether each observation accurately represents job-relevant work. The script checks structure and provenance fields, not semantic truth; the interviewer must review every linked source and anchor. There is no approval boolean that makes unrestricted prose exportable. Everything remains a draft until the interviewer reviews it. Panel mode compares filed ratings and flags disagreements; until every interviewer files, it shows coverage only.

## Before you run

This skill ships a script, a config, a rubric template and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Put every file listed with this skill in one flat working folder (no subfolders) alongside this SKILL.md. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `interview-scorecard`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters), and its hash where your tools report one, with the published version before running. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real interviews.

## Config

```yaml
notes_source: ""               # granola | gong | fathom | fireflies | otter | zoom | upload — set in Step 0
interview_folder: ""           # optional Granola folder id that holds interview notes (folders need a paid Granola plan)
interview_title_patterns: ["interview", "screen", "panel", "case study", "onsite"]  # used when no folder is set
internal_domains: []           # your company's email domains; the candidate is the attendee outside them
rubric_dir: hiring/rubrics     # where rubric_<role_id>.json files live (working folder or Space)
ats: none                      # greenhouse | ashby | lever | none — set in Step 0; drafts only
deliver_to: type_doc           # type_doc (private to the interviewer) | dm_interviewer | chat
recruiting_lead: ""            # who receives fairness/consent flags and candidate data requests (a role is fine)
scorecard_log: scorecard-log.md  # scheduled runs never draft the same interview or panel twice
```

These are defaults. Do not edit them here. The values confirmed in Step 0 live in a small settings file, `interview-scorecard-settings.md`, in the user's working folder or Space. That file is not part of this skill. Read it at the start of every run; any key it sets overrides the default above.

Thresholds and legacy metadata/rubric checks live in `scorecard.json`. The export boundary does not depend on those keyword lists. `DATA_CONTRACT.md` documents the bounded observation vocabulary, rubric, bundle and log. `tests.sh` checks current exports plus retained validation and scheduling behavior.

## Step 0: First run, confirm connections and rubrics

Run this step when `interview-scorecard-settings.md` is missing, when `notes_source` is blank, or when the configured source is no longer connected.

1. **Detect.** Check which note sources can actually be read: Granola (call `get_account_info`, then `list_meetings` with `time_range: this_week`), or another recorder (Gong, Fathom, Fireflies, Otter, Zoom). Connected means one real read call succeeded. Note whether an ATS (Greenhouse, Ashby, Lever) can be read, and whether Type docs or Slack DMs can be written for delivery.
   - **In Type, also check workspace connections that exist but are not added to this Space** (for example Granola or Ashby connected for the workspace but not attached here). Name each one and offer to add it; only use it once it has been added and a read call succeeds.
   - Granola's `get_account_info` reports the plan and note-access scopes (personal, public). Tell the user if other interviewers' notes will only be visible when they're shared with them or in a Team Space folder; panel mode depends on it.
2. **Find the rubrics.** List `rubric_*.json` files in `rubric_dir`. For each, run `python3 build_scorecard.py --check-rubric --rubric <file>` and report the role, version and status. If there are none, say so; Step 2 builds one before anything is scored.
3. **Propose** the values: `notes_source`, `interview_folder` (offer the user's Granola folders from `list_meeting_folders` if they keep interviews in one), `internal_domains`, `ats`, `deliver_to`, `recruiting_lead`.
4. **Wait for the user to confirm.** Never pick an ATS or a delivery channel silently.
5. **Save** the confirmed values to `interview-scorecard-settings.md` as a short YAML block using the keys from Config. Never edit this SKILL.md: it is a published skill, and editing it breaks the file-hash check in "Before you run". If you cannot write files there, give the user the exact lines to save.

If an automation triggered the run and nobody is there to confirm, send one message to the person who set up the automation asking for these values, then exit without drafting anything.

## Step 1: Find the interview(s)

Granola is read-only and is the only place this skill reads notes from unless another source was confirmed.

- **Single interview** ("score my interview with Jordan"). Call `list_meetings` with `time_range: last_30_days` (or `time_range: custom` with ISO dates in `custom_start` and `custom_end`, both required, when the user names a date), `involvement: {captured_by_me: true, listed_as_participant: true}` (true conditions OR together), and `folder_id` when `interview_folder` is set. Leave `workspace_only` out unless the user wants workspace notes only (it only accepts `true`). A meeting matches when its title contains the candidate's name or one of `interview_title_patterns` and it has an attendee outside `internal_domains` (participant lists can be incomplete). One match: use it. Several: list them with date and title and ask. None: say what you searched and ask for the date or a pasted transcript. Never score a different meeting because it looked close.
- **Panel debrief** ("panel debrief for Jordan"). Find every interview for the candidate, by any interviewer: `list_meetings` without the involvement filter (so shared and Team Space notes count), and `query_granola_meetings` (`query` required, slow; `document_ids`/`note_ids` narrow it to at most 10 notes) with a question such as "Which meetings were interviews with Jordan R. for the Finance Operations Analyst role?". Take the meeting ids from its `https://notes.granola.ai/d/<id>` citations and read them with `get_meetings`. Ask the user to confirm the list. If an interviewer on the loop has no accessible notes, record it as a gap ("no notes visible for Theo K.; ask him to share the note") instead of dropping them silently.
- **Scheduled** (automation). Get the dates to list and the keys to re-run from `--next-window` (see Scheduled mode). Call `list_meetings` with `time_range: custom`, `custom_start` and `custom_end` set to those dates (and `folder_id` when set), keep the meetings in `interview_folder` (or matching `interview_title_patterns`), and draft one single-interview scorecard each, for the interviewer who captured the note. Skip, silently, any meeting whose attendees are all on `internal_domains`, and any meeting whose notes don't read as a candidate interview (a title like "screen share review" can match a pattern). Internal candidates are scored on request only.

Then, for each interview:

1. `get_meetings` with `meeting_ids` (1–10 ids per call; batch longer lists in chunks of 10) for the interviewer's own notes, the AI summary, attendees, date and the note URL.
2. `get_meeting_transcript` only for verbatim candidate quotes. Granola labels audio sources: `Microphone` is the device that recorded (usually the interviewer) and `System audio` is the call's other side. When Granola can attribute a line it adds the participant's name (`System audio (Name)`); mobile or imported transcripts may say `Speaker A`/`Speaker B`. A name is a hint, not proof, and one source can hold several voices. Treat a line as the candidate's words only when it is attributed to the candidate, or in a one-to-one remote interview where the `System audio` line is plainly the answer to the interviewer's question; otherwise don't use it as a quote. Granola transcripts carry no per-line timestamps today, so leave `timestamp` empty; recorders that have them (Gong, Fathom, Otter) should fill it.
3. If `get_meeting_transcript` is denied (workspace policy or plan), set that interview's `transcript_status: denied` and continue with the notes. Never retry through another tool and never conclude something "wasn't discussed" because the transcript was unavailable.
4. Notes older than 30 days may be unavailable on free Granola plans. Set `notes_status: unavailable` with that reason; it is printed as a gap.

Fallbacks: another recorder's transcript, or a transcript or notes the interviewer uploads or pastes (`provider: upload` or `pasted`). Read it for this run only.

## Step 2: Rubric gate

Look for `rubric_<role_id>.json` for the candidate's role in `rubric_dir` and run `--check-rubric` on it.

- **Approved rubric found:** continue.
- **No rubric, or only a draft:** stop scoring. Offer to build one with the hiring manager now from `rubric_template.json`, following `rubric-guide.md` (3–10 job-related competencies, an observable anchor for every score 1–4, must-haves marked, an interview plan). Save it with `status: draft`. Only the hiring manager or recruiting lead sets `status: approved` and `approved_by`. **Do not read the interview notes against a rubric written after the interview to fit the candidate**, and never score against a rubric you invented. The script exits 3 without an approved rubric for the bundle's `role_id`.
- **The rubric contains a protected characteristic or proxy** (for example "native English speaker" or a bare "culture fit"): the script exits 3 and names the field. Help the hiring team restate it as an observable behaviour; never work around it.

## Step 3: Extract work observations and collect ratings

1. Read each source in context. Keep only a short source excerpt (maximum 400 characters) in the temporary bundle's `evidence[]`, with its competency, `kind` (`quote`, `note` or `summary`), source URL and available timestamp. Preserve speaker attribution: a quote requires `speaker: candidate`. The agent copies quotes; the script can't verify them. Do not invent a quotation or paste a whole transcript.
2. For supported job-relevant work, add an `observations[]` row with a positive integer `id`, matching `competency`, zero-based `source_index`, and exact `action`, `object`, `result` codes from DATA_CONTRACT. Example: `{"id":1,"competency":"reconciliation","source_index":0,"action":"trace","object":"ledger","result":"issue-identified"}`. This renders “Traced the ledger; result: issue identified.” with the source pointer. Select codes only when their meaning is supported by that excerpt in context. If the vocabulary cannot express the work faithfully, leave the observation out and ask the interviewer to review the source directly. Never map personal information, praise or a hiring verdict to a favorable work code.
3. Ratings belong to the interviewer. Do not choose a number or relabel an agent suggestion `interviewer_confirmed`. Start with `scores: []`; collect the interviewer's own 1–4 ratings after they review the sources against their rubric. Record each with `status: interviewer_confirmed` and the supporting `observation_ids`. The required legacy `rationale` can be a short human note, but it is never exported; the output supplies a fixed source-reference explanation. Suggested ratings are explicitly held as “Awaiting interviewer rating”, even with valid observations.
4. Set `filed: true` only after the interviewer actually files their ratings, or explicitly files an empty assessment. A withheld rating prevents that interview from being treated as filed. Match questions to the plan in `questions_asked`; missing competencies and unasked planned questions drive follow-ups. Legacy free-text `gaps[].note` is withheld with a reason, never copied into output.
5. Exclude protected characteristics, personal circumstances, pay, candidate comparisons and hiring verdicts from the extraction and rating process. If the interviewer asks about a sensitive topic, add a topic-only `concerns` entry with its source link; never record the answer. The bounded export prevents verbatim prose leakage, but does not establish fairness or relevance of an extraction. Review every observation, source and rating.
6. `recording_disclosed` is required. Use `true` only with evidence of disclosure; `false` or `null` excludes quote- and summary-derived observations. An interviewer rating without usable referenced observations is withheld. Unknown disclosure also blocks ATS drafts. A rating backed only by summary-derived observations is flagged as weaker.
7. Keep minimum metadata: candidate reference and display name, interviewer name and role, stage/date and genuine source URLs. Do not put evidence or rationale in metadata fields. Metadata/rubric pattern checks are additional heuristics, not a guarantee. Never fabricate a source URL, timestamp, rating or approval.

## Step 4: Build the scorecard

Save the bundle in a temporary working folder and run:

```bash
python3 build_scorecard.py \
  --rubric rubric_finance-ops-analyst.json \
  --bundle bundle.json \
  --as-of 2026-09-29 \
  --log scorecard-log.md
```

- `--as-of` is the run date (`YYYY-MM-DD`) and the only "today" the script uses.
- Panel debriefs use the same command with a `mode: panel` bundle.
- `--ats-draft greenhouse|ashby|lever` adds an ATS draft block (single mode only).
- `--scheduled` (automations only) records the key in `--log` as `pending` before printing and skips keys already `delivered`; when the notes can't be read it drafts nothing and records the key as `skipped`.
- `--next-window` and `--mark-listed` drive Scheduled mode (`DATA_CONTRACT.md`, Log). After the scorecard reaches the interviewer, run `python3 build_scorecard.py --log scorecard-log.md --confirm-delivered <key>` (the key is `single:<role_id>:<meeting id>`, also in `--json`). Confirming a `skipped` key exits 2 and leaves it `skipped`: nothing was drafted. Rerunning a `pending` key exits 4 and prints nothing: confirm it if it was delivered, or add `--resume-pending` to draft it again. Runs without `--scheduled`, and `--dry-run` runs, never write the log. `--json` emits the analysis and markdown as JSON.
- `--hiring-manager-preview` (panel only) shows scores before every interviewer has filed, under a "not ready to share" banner, for the hiring manager alone.
- Exit 0 prints the scorecard, a one-line `Skipped: …`, a confirmation or the `--next-window` plan. Exit 2 means bad input; the message names the file and JSON path (never the flagged text). Exit 3 means there is no usable rubric (Step 2). Exit 4 means a scheduled run found the key pending. Exit 5 means another run holds the log lock (`<log>.lock`; one older than 15 minutes, or dated in the future, is cleared automatically; a folder at that path exits 2). On exits 2–5 nothing is printed on stdout and nothing is logged. Exit 6 means the scorecard could not be written to stdout (closed output or broken pipe); in a scheduled run its key is already `pending`, so the next run stops on it (exit 4): rerun with `--resume-pending` once output works. Fix the input and rerun; never edit the script to get past an error.

The script validates the approved rubric, field types, source access, consent, exact observation codes and same-competency source/rating references. It exports template-built observations, preserves human ratings only with usable references, leaves the recommendation empty, and enforces panel filing and two-phase delivery. It never verifies semantic accuracy from a keyword absence. See DATA_CONTRACT for exact withdrawal reasons and limits.

## Step 5: Deliver

`expected_output.txt` shows both layouts: a DRAFT heading, **Summary**, competency table, a section per competency (anchor, reference explanation, structured work observations), **Gaps to probe next round**, **Planned questions not asked**, **Fairness & compliance**, the optional **ATS draft**, then Sources, Gaps and the data-handling note; the panel debrief adds the side-by-side table, **Discuss first: disagreements**, **Not covered by anyone** and evidence by competency.

- **Single scorecard:** a Type doc (or a DM) visible only to the interviewer, with the title `Scorecard (draft): <display name> — <role> — <stage>`. Ask them to review each source, enter their own ratings against the anchors, confirm the consent line, and file it. Never post a candidate's scorecard in a shared channel.
- **Panel debrief:** share with the hiring panel only, and only after each interviewer has filed their own scores, so nobody anchors on someone else's. The script enforces this: it hides every score until every interviewer has filed, and heads the debrief "NOT READY TO SHARE" with the names it is waiting on; rerun once they have. `--hiring-manager-preview` shows the scores early for the hiring manager only; never forward that version. Lead the debrief with "Discuss first: disagreements" and "Not covered by anyone".
- **ATS (optional):** with `ats` configured, show the ATS draft block. It holds interviewer ratings, rubric labels, controlled work observations and an empty recommendation field; source prose and free-text rationales are absent. Submit nothing until the interviewer whose scorecard it is says yes to that exact text. Ashby (`applicationFeedback.submit`) and Lever (feedback on the opportunity) accept feedback through their APIs; map ratings onto your scorecard form's fields at setup. For Greenhouse, the interviewer pastes the draft into their scorecard. Leave the recommendation field empty for the interviewer to write.
- **Flags:** if the fairness section lists a question of concern or an undisclosed recording, tell `recruiting_lead` the topic and the meeting link only.
- **Clean up:** delete the bundle and any transcript text you held for the run. Keep only the filed scorecard and the log (which stores ids, not names).

Keep the output's sections and wording. Do not soften "No evidence — not assessed" or a "Withdrawn — …" label into a low score, and do not trim the Fairness & compliance, Sources or Gaps lines.

## Guardrails: never

- Never score against an invented, draft or mismatched rubric. No rubric means build one and stop.
- Never infer a score from absence of evidence, from "vibes", or from anything outside the rubric's competencies.
- Never record, repeat or weigh protected characteristics or their proxies, or a candidate's answer to a question about them.
- Never make or imply the hire/no-hire decision. Suggested scores are held until the interviewer enters their own rating.
- Never submit to an ATS, post to a channel or e-mail anyone without the interviewer's explicit approval of the exact text. Granola is read-only; this skill never writes to it.
- Never use transcript evidence when the candidate wasn't told the interview was recorded or transcribed.
- Never store candidate contact details or transcripts beyond the run, and never put real candidate data in examples.
- Never show one interviewer's scores to another before both have filed theirs (the hiring-manager preview is for the hiring manager only).

## Scheduled mode

Nothing runs until there is a trigger. In **Space settings → Automations**, add a schedule (for example 18:00 on weekdays) that runs this skill with `--scheduled`. Each run:

1. Runs `--log scorecard-log.md --as-of <today> --next-window`. It gives the dates to list, from the day of the last completed listing (inclusive; meetings seen twice are skipped because their keys are already `delivered`; on the first run, 7 days back) to the end of today, so interviews that ended Friday evening, Saturday or Sunday, or before a missed run, are picked up. It also lists every `skipped` key (notes couldn't be read) and `pending` key (drafted, delivery never confirmed) in the log.
2. Lists those dates in Granola and, for each interview found plus each listed key (its meeting id is the key's last part), runs the script with `--scheduled --log` (add `--resume-pending` for a `pending` key you have checked was not received), sends the scorecard privately to the interviewer who captured the note, and only then runs `--confirm-delivered <key>`. If sending fails, don't confirm: the key stays `pending` and the next run's `--next-window` lists it again. Never post a pending key twice without checking.
3. Interviews whose notes can't be read print `Skipped: notes not available …` and are logged as `skipped`, so the next run's `--next-window` lists them again; say so rather than reporting "nothing to draft". Don't confirm a skipped key: `--confirm-delivered` refuses it (exit 2) and it stays `skipped`.
4. When every listed interview and key has been run, runs `--log scorecard-log.md --as-of <today> --mark-listed`. If the run stops before this, the next run lists the same days again.

Each meeting id is its own key, so two interviews by the same person on the same day are two drafts. `tests.sh` simulates two weeks of weekday 18:00 runs and checks every interview reaches its interviewer exactly once. Panel debriefs are on request; a not-ready panel is never logged, and a new interview for the same candidate gives a new panel key. Runs on request never write the log, so a scheduled run may later send the same interviewer a draft they already asked for.

New automations start disabled. Enable it. Scheduled runs only ever send to the interviewer (or the destination configured in Step 0), never to a shared channel.

## Limits and verification

The agent's source extraction and competency mapping can be wrong. Code labels preserve only limited work detail; they do not prove that a source supports the observation. The interviewer must assess the original source against the approved rubric and enter ratings independently. The script cannot authenticate who entered a confirmed status. Unsupported roles or nuanced examples may require source review outside this export.

The script preserves legitimate names, role/interviewer metadata, rubric wording and source URLs. Their shape/length/contact/keyword checks are heuristic; they are not unrestricted evaluation-prose slots and must be reviewed separately. Unknown disclosure prevents transcript-derived observations, not all possible privacy problems.

`bash tests.sh` checks controlled projection over the historical source corpus, all supported vocabulary combinations, all three ATS renderers and JSON, paired safe work controls, invalid observations, consent, human-rating gates, schema/rubric validity and durable scheduling/recovery. `bash run.sh` is the exact sample and listing source. TEST_MIGRATION.md distinguishes replaced raw-output assertions from retained validation/scheduler coverage. Tests are offline; live Granola/ATS access, delivery and scheduled integrations remain unverified.

## Export-contract migration

Round 12 replaces heuristic release of raw evidence/rationale/gap prose with controlled observations. Existing schema-version-1 bundles still load, but text-only evidence is withheld with a structured-observation-required reason; no suggestions are promoted. Add supported observation codes, have the interviewer enter ratings and reference their observation IDs, then rerun. Do not convert old suggestions to confirmed automatically. Tests run offline on sample and adversarial data; this repair makes no claim of live Granola/ATS verification or universal semantic screening.
