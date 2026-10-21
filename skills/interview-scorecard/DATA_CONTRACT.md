# Data contract: interview-scorecard

The script reads an approved rubric, a schema-version-1 bundle and scorecard.json. It calls no APIs. `--as-of YYYY-MM-DD` is the only run date. Python 3.8+; no third-party dependencies.

UTF-8 JSON only, no NaN/Infinity or unpaired surrogate escapes. Dates must be ASCII YYYY-MM-DD; timestamps MM:SS or HH:MM:SS. IDs are 1–128 ASCII letters/digits plus :, ., _ and -, beginning with a letter/digit. Slugs are lower-case letters/digits plus - and _. Wrong types are rejected; booleans are not scores or observation IDs. Optional fields may be absent/null. Unknown keys are ignored except forbidden keys and observation keys, which are rejected. Text is normalized/escaped for display.

The agent copies quotes; the script can't verify them. Raw evidence, rationale and gap text stays input-only. Output observations are a finite projection, not a semantic classification or a verbatim quote. The agent must judge relevance and source accuracy; the interviewer must review the source and enter ratings independently.

Exit codes: 0 success/skip/confirmation; 2 bad bundle/config/log/arguments; 3 unusable rubric; 4 pending delivery; 5 busy log lock; 6 failed stdout write (scheduled key remains pending). Exits 2–5 print no stdout and do not change the log. JSON path errors identify the field. Fix input rather than bypassing validation.

## Rubric (`rubric_<role_id>.json`)

One file per role, written with the hiring team **before** interviews are
scored (start from `rubric_template.json`; guidance in `rubric-guide.md`).

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `role_id` | slug | yes | Must equal the bundle's `role_id`, otherwise exit 3. |
| `role_title` | string | yes | Shown in the heading. |
| `version` | string | yes | e.g. the approval date `2026-09-15`. Shown on every scorecard. |
| `status` | `draft` \| `approved` | yes | Scoring needs `approved` (exit 3 otherwise). Only the hiring manager or recruiting lead changes it. |
| `approved_by` | string | when approved | Who approved it; a role is fine ("Hiring manager, Finance"). |
| `scale_labels` | object | yes | Exactly keys `"1"`–`"4"`, e.g. `"3": "Meets the bar"`. |
| `competencies[]` | list | yes | 3–10 items (`min_competencies`/`max_competencies` in config). |
| `competencies[].id` | slug | yes | Unique. |
| `competencies[].name` | string | yes | |
| `competencies[].definition` | string | yes | One sentence of observable behaviour. |
| `competencies[].must_have` | bool | no | Default `false`. Must-haves are starred (★) and called out when not assessed. |
| `competencies[].anchors` | object | yes | Exactly keys `"1"`–`"4"`, each a non-empty behavioural description. |
| `interview_plan[]` | list | no | Stages. Without it, unasked-question checks are off and each scorecard covers every competency. |
| `interview_plan[].stage_id` | slug | yes | Unique. Interviews refer to it. |
| `interview_plan[].stage_name` | string | yes | e.g. "Hiring manager interview". |
| `interview_plan[].competencies` | list of ids | yes | What this stage owns. A competency may sit in more than one stage. |
| `interview_plan[].questions[]` | list | no | `{ "id": slug (unique across the plan), "competency": id owned by this stage, "text": string }`. |

The rubric is rejected (exit 3, naming the field but not repeating its text) when **any** text
field (`role_id`, `role_title`, `version`, `approved_by`, `scale_labels`, competency
`id`/`name`/`definition`/`anchors`, `stage_id`, `stage_name`, question `id` and `text`) matches a
protected-characteristic, proxy or hiring-decision term ("native English speaker", "Culture-fit", a
scale label "Strong hire", a question about visas), or contains an e-mail, phone or ID number or
link. In ids, names and titles (and interview titles and interviewer roles), a part that only names
an interview stage ("Onsite", "Final round") is a name, not a verdict.

`--check-rubric --rubric <file>` validates a rubric on its own and prints a
summary; it exits 0 for a valid draft too (and says it is a draft).

## Bundle

Required top-level fields: `schema_version: 1`, `mode: single|panel`, `role_id` matching the rubric, `candidate`, `sources` and nonempty `interviews`. Single mode requires one interview after deduplication. Recommendation/transcript keys in scorecard.json's forbidden lists are rejected.

`candidate`: `ref` (opaque ID, also used in log) and `display_name` (short name such as Jordan R.). Email, phone, address, profile, age and similar forbidden candidate keys are rejected. Names remain visible; name-shape/contact checks are not a general semantic sanitizer.

`sources`: required `notes` and `transcript` objects, optional `ats`, each with `provider` and `status`. Status is `ok|unavailable|denied|not_requested`; provider is a configured provider code (see scorecard.json). Optional detail is source context, withheld from exports. Source URLs must be http(s), are escaped, and invalid schemes are omitted with a gap count.

### Interviews

Required: `interview_id`, local `date`, `interviewer: {name, role?}`, `recording_disclosed: true|false|null`, `notes_status`, `transcript_status`. Optional: `meeting_id`, `url`, `title`, `stage_id`, `questions_asked`, `evidence`, `observations`, `scores`, `gaps`, `concerns`, `filed`.
Stage/question IDs must exist in the rubric. Legitimate candidate/job/interviewer metadata and the approved rubric remain visible; roles retain bounded length and heuristic checks. Source meeting titles are input-only: without an assigned rubric stage, exports use the fixed label "Interview (stage not assigned)". Never use these fields to carry evaluation prose.

`filed: true` means the interviewer has actually filed, even if the assessment is empty; every supplied rating must have status interviewer_confirmed. Missing `filed` means filed only if at least one rating remains and none was withheld. An explicit true never rescues a withheld rating.

### Source evidence (input-only)

`evidence[]` rows have a rubric `competency`, `kind: quote|note|summary`, required `text` (1–400 characters), optional `evidence_kind`, `timestamp` and `url`. Quotes require `speaker: candidate` and transcript_status ok. Notes/summaries require notes_status ok. Legacy evidence_kind is accepted for migration but is not an approval or export gate. A row without a corresponding observation is not exported and adds fairness flag `structured_observation_required`.

Do not supply sensitive personal excerpts merely because the script withholds them; minimize the working bundle. The source text is never emitted even with approved/safe/confirmed-like input metadata.

### Work observations

Each row has exactly these keys:

| Key | Contract |
| --- | --- |
| id | Unique positive integer within the interview. |
| competency | Rubric competency; must match the referenced evidence row. |
| source_index | Zero-based integer index into this interview's evidence list. |
| action, object, result | Exact codes below; arbitrary strings and extra fields rejected. |

Actions: `reconcile, analyze, trace, validate, correct, document, automate, explain, prioritize, test, design, implement, review, plan, revise, coordinate, investigate`.

Objects: `bank-reconciliation, accounts-payable, accounts-receivable, ledger, close-checklist, variance-report, forecast-model, data-validation, reconciliation-control, stakeholder-update, feedback-plan, work-plan, workflow, system-test, technical-design, incident-response, research-plan, customer-requirement, delivery-plan, quality-check`.

Results: `completed, issue-identified, corrected, validated, documented, explained, proposed, unresolved, insufficient-detail`.

The script builds a sentence only from these fixed labels and adds observation ID, source kind, one-based source row, URL and available timestamp. Those are locators, not proof of semantic support. A summary-derived observation retains its weaker source kind. No free-text approval field can bypass this contract. If a role's evidence cannot be faithfully expressed by these codes, the reviewer must inspect the source directly; do not force a misleading code.

Duplicate source/competency/action/object/result combinations render once. Ratings referring to an omitted duplicate ID are withheld; reference the retained first ID. False/unknown recording disclosure excludes quote/summary observations; notes remain eligible.

### Ratings and follow-ups

`scores[]` rows require `competency`, integer `score: 1..4`, `rationale` (1–600 characters, input-only), optional `status` (default suggested) and `observation_ids` (unique integer IDs from this interview, same competency).

Only the interviewer's separately entered ratings use `interviewer_confirmed`. The agent must not choose a rating or relabel a suggestion as confirmed. The renderer cannot authenticate who typed a field; this is a workflow/provenance obligation. It never turns a suggestion into a score.

| Output label | Reason |
| --- | --- |
| Awaiting interviewer rating | Input status is suggested/default, regardless of wording. |
| No valid structured source | Confirmed rating has no references or references excluded observations (including consent and duplicate exclusions). |
| Withdrawn — evidence withdrawn | Another source row for this competency lacks a structured observation or was excluded by consent. Review all source rows before re-entering a rating. |

A retained rating uses a fixed source-reference explanation instead of the raw rationale. A rating relying only on summary-derived observations is flagged as weaker. No rating means either “Not scored — evidence below” or “No evidence — not assessed”. Coverage and planned questions come from the actual user-supplied rubric.

`gaps[]: {competency?, note}` accepts legacy notes up to 600 characters but always withholds the text and reports `free_text_gap`. Follow-ups use unassessed competencies and the rubric's planned questions. `concerns[]: {topic, timestamp?, url?}` uses a configured category, never question or answer prose; answer/text/quote/response fields are rejected.

## ATS and JSON exports

`--ats-draft greenhouse|ashby|lever` adds a single-interview draft with interviewer ratings, rubric labels and controlled work observations. Unknown recording disclosure blocks ATS export. Recommendation is empty; no submission occurs.

`--json` includes mode/role/candidate reference, run key, draft true, recommendation null, per-interview metadata, retained scores, controlled observations, evidence counts, export_policy `controlled-work-observations-v1`, withdrawal reasons, fairness flags, concerns, unasked question IDs and rendered Markdown. It never serializes the input bundle's raw evidence/rationale/gaps. Panel ratings, spread and disagreement are hidden until every interviewer files unless the private hiring-manager preview is explicitly selected.

Metadata and rubric values remain user-supplied text and require separate review. Pattern checks for them are heuristics. Fixed observation labels prevent raw evaluation-prose leakage; they do not prove relevance, accuracy, fairness, speaker attribution or a rating's human provenance.

## Migration and tests

Schema version remains 1 so older bundles can be read. Their text-only rows are held rather than silently exported. Add supported observation codes and human-entered ratings with IDs; never auto-confirm a previous suggestion. Raw-prose rendering assertions from rounds 8–10 are superseded by projection/invariance tests using the same source corpus. Schema, rubric, consent, log/recovery and concurrency tests remain. See TEST_MIGRATION.md.

Two notes with the same meeting_id count once; duplicate interview IDs count once. Different meetings remain separate even on the same day. The panel flags spread >= configured disagreement_threshold (default 2). Configuration shape and scanner/metadata defaults remain in scorecard.json; the raw-prose export boundary does not depend on keyword screening.

## Log (`--log`)

Markdown, created on first use:

```
# Interview scorecard log

Listed through: 2026-10-02

| key | kind | role_id | candidate_ref | interviews | drafted_at | status |
|---|---|---|---|---|---|---|
```

- Single key: `single:<role_id>:<meeting_id, or interview_id when there is none>`.
- Panel key: `panel:<role_id>:<candidate ref>:<first 12 hex of sha1 of the sorted meeting ids>`.
  A new interview for the candidate gives a new key.
- `Listed through: YYYY-MM-DD` (optional, above the table) is the run date of the last completed scheduled
  listing, written by `--log <file> --as-of <date> --mark-listed` (never moved back). `--log <file> --as-of
  <date> --next-window [--json]` prints the dates to list, from that day (inclusive) to the day after
  `--as-of` (`custom_start`/`custom_end`; 7 days back when there is no listing yet), and every `skipped` or
  `pending` key to re-run (`rerun`: key, status, recorded date). Exit 0; nothing else is written.
- `status` is `pending`, `delivered` or `skipped`. Two-phase: a `--scheduled` run writes the row as
  `pending` **before** printing; once the scorecard is delivered, `--log <file> --confirm-delivered
  <key>` sets `delivered`. Scheduled runs skip `delivered` keys (one-line `Skipped: …`, exit 0) and stop
  on `pending` keys (exit 4, nothing printed) unless `--resume-pending` is given, which drafts it again
  without a second row. `--confirm-delivered` on a `skipped` key exits 2 (nothing printed on stdout, the
  key stays `skipped`).
- A scheduled run whose interview notes aren't `ok` drafts nothing (`Skipped: notes not available …`)
  and records the key as `skipped` (a `pending` key stays `pending`); `--next-window` lists it, and a
  later scheduled run whose notes are `ok` drafts it and sets it to `pending`. A
  panel debrief that is not ready to share is never logged.
- Runs without `--scheduled` never write the log; `--dry-run` never writes.
- Writes take an exclusive lock file `<log>.lock` (`O_CREAT|O_EXCL`) holding the run's pid and a
  random token; a second run exits 5. A lock older than 15 minutes, or dated more than a minute in the
  future, is treated as left by a crashed run and removed once, under a short-lived
  `<log>.lock.takeover` guard and only if it is still the same file. A run only ever removes a lock
  holding its own token. A folder at the lock path exits 2. The log is rewritten atomically (temp file +
  rename); its folder must exist and be writable before anything is printed (exit 2 otherwise).
- The log stores ids only, never names or evidence. A file that isn't a scorecard log, or any
  malformed row, stops the run (exit 2) rather than guessing what was delivered. Logs from before the
  `status` column must be migrated by adding `| delivered |` to each row and the header column.


## Mapping from Granola (read-only MCP tools)

Call parameters and speaker attribution: SKILL.md, Step 1.

| Need | Tool | Notes |
| --- | --- | --- |
| Account, plan, note-access scopes | `get_account_info` | Explains missing notes (plan, workspace, scopes). |
| Interviews in a window or folder | `list_meetings` | Returns id, title, human-readable date, participants (name, company, e-mail) and URL. Participant lists can be incomplete. Drop attendee e-mails after identifying the candidate. |
| Notes, summary, attendees | `get_meetings` | 1–10 ids per call. Interviewer notes → `kind: note`; AI summary → `kind: summary`. No transcript. |
| Verbatim quotes | `get_meeting_transcript` | Lines labelled by audio source; no per-line timestamps. Denied → `transcript_status: denied`; never retry through another tool. |
| All interviews for a candidate | `query_granola_meetings` | Slow; read the cited ids with `get_meetings`. |
