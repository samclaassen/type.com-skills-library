# Data contract: user-research-synthesis

The agent reads one study's interviews (from Granola, another recorder, or pasted transcripts), extracts and codes
the evidence, and writes **one JSON bundle**. `build_synthesis.py` reads only that bundle and `synthesis.json`. It
never calls an API, never reads the clock (`--as-of` is the only "now"), never writes a file, and prints one report
to stdout.

Conventions:
- UTF-8 JSON, at most 50 MB. Unknown keys are ignored, so you can keep provenance such as `_note`. `NaN`,
  `Infinity` and unpaired surrogate characters are rejected (exit 2).
- **date** = `YYYY-MM-DD` with ASCII digits and a year from 1900 to 2999. Anything else (`28/09/2026`, `20260928`,
  `2026-W40-1`, a datetime) is an error on every Python version.
- Strings: whitespace (including newlines) is collapsed, except in `transcript.text` and `notes.text`, whose line
  breaks are kept for the quote check (each line is a speaker turn of its own); control, bidi-override and invisible characters (zero-width
  space, soft hyphen, zero-width joiners next to ASCII text) are dropped; text is NFKC-normalised, so full-width and
  other compatibility letters become plain letters. Never pre-escape text: the script escapes it for each format.
- Optional means the key may be absent or `null`. It never means the agent should make up a value.
- Exit 2 messages name the bundle file and the JSON path, e.g.
  `bundle b.json: $.evidence[4].text: is not a whole-sentence quote (not a complete sentence in the meeting text;
  checked against $.meetings[2].transcript.text) …`. They
  never repeat the text of a private identifier.

## What the script does with displayed text (pseudonymisation)

Every value that reaches the output (study name, research questions, segment labels, statements, quotes, interviewer
questions, assumptions, open questions, excluded reasons, source details and folder names) goes through the same
redaction, in this order:

1. Email addresses → `[email removed]` and phone numbers → `[phone removed]`, with the shared `granola_text.py`
   rules (below). This runs before any text is shortened.
2. Web addresses → `[web address removed]`: anything with `scheme://` or `www.`, and bare domains ending in a common
   top-level domain (`.com`, `.io`, `.ai`, `.co`, `.org`, `.net`, `.example`, country codes such as `.uk`, `.de` …).
3. Every value in `study.private` and `participants[].private` → `[name]` or `[organisation]`, matched
   case-insensitively as whole words (so `Maya's` becomes `[name]'s`). Added automatically: each part of a
   multi-word name of 3+ characters (`Maya Okonjo` also redacts `Maya` and `Okonjo`; particles such as `van` or
   `de` are skipped), an accent-free form (`José` also redacts `Jose`), handle-style joins (`mayaokonjo`,
   `maya.okonjo`, `maya_okonjo`, `maya-okonjo`), and for each listed email the domain and, when it has 4+
   characters and is not a free-mail domain, its first label (`maya@vellicor.example` adds `vellicor.example` and
   `vellicor`). Apostrophes and dashes in a listed value match their typographic forms (`O'Brien` also redacts `O’Brien`), the pieces of a hyphenated or apostrophe name part of 3+ characters are redacted too (`Jean-Luc` also redacts `Jean` and `Luc`), and a digit or `_` next to a name does not protect it (`Okonjo2` becomes `[name]2`).
Emails are caught in plain and disguised forms (Granola's `\@` escapes, `[at]`/`(at)`, `x at y dot z`,
full-width `＠`). Phone numbers are removed only when there's a clear signal: a `+` (or `00`) country code, a word
like `call`, `phone`, `mobile` or `WhatsApp` just before the number, or a standard US (`(555) 123-4567`,
`555-123-4567`), UK (`07700 900123`, `020 7946 0958`) or French (`06 12 34 56 78`) grouping. Number lists, prices,
tiers, ranges, dates, times, IP addresses, versions and numbers after a reference word (order, PO, invoice, ticket,
`#`, `$`…) are left alone, and so are unseparated national numbers (`07700900123`, `4155550199`) and other
countries' formats without a `+` or a cue word.

This is **pattern-based and can miss things**: a name nobody listed, a nickname, a misspelling, a name written with
look-alike letters from another script, a plural or other inflected form (`the Okonjos`), a short given name that is
only listed as part of a full name (parts under 3 letters, such as `Al` in `Al Rivers`, are not redacted on their own), an accented form when only the accented spelling appears in the text but the
plain one was listed, or a phone number with no `+`, no cue word and no US/UK/French grouping. It can also blank an ordinary word that equals a listed name (a
participant called Will turns "I will" into "I [name]"). List every person and organisation you hear in the
interviews, and read the report before sharing it.

Identity checks that are enforced:
- A meeting `url` that contains a listed identifier (as whole words, or squashed together for identifiers of 5+
  letters) is dropped, and the report counts it under "Dropped". Recorder ids (UUIDs, `not_…`) inside a URL are
  ignored for this check, so a hex id that happens to contain `ada` is not mistaken for a name.
- A `meeting_id` that is not a recorder id (UUID or `not_` + 14 characters) and contains a listed identifier is
  rejected (exit 2): use the recorder's id or a neutral one such as `pasted-1`.
- Participant, research-question, finding, evidence and opportunity ids must match fixed patterns (`P1`, `RQ1`,
  `F1`, `E1`, `O1`), so an id cannot carry a name.
- Meeting titles and attendee lists are not part of the bundle at all; sessions appear as `M1`, `M2` … numbered by
  date, then recorder id.

## Top level

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `study` | object | yes | See below. |
| `sources` | object | yes | `meetings` and `transcripts` source status. |
| `participants` | list | yes (may be `[]`) | Everyone interviewed, pseudonymous ids only. |
| `meetings` | list | yes (may be `[]`) | Every session in scope, including ones you exclude (with `excluded_reason`). |
| `evidence` | list | yes (may be `[]`) | Coded observations: one quote or note excerpt each. |
| `findings` | list | yes (may be `[]`) | Findings, jobs, pains, workarounds and desired outcomes. |
| `opportunities` | list | no | Opportunities, each based on findings. |
| `open_questions` | list | no | `{rq: RQ id or null, text ≤ 400}`: what the study did not answer, in your words. |

## `study`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string ≤ 200 | yes | Shown in the title (redacted like everything else). |
| `method` | enum | no | `interviews` (default), `usability_tests` or `mixed`. Only changes wording. |
| `research_questions` | list | yes, at least one | `{id: "RQ1"…, text ≤ 400}`. Ids are unique. |
| `segments` | list | no | `{key, label ≤ 80}`. `key` is lower-case letters, digits, `.`, `_`, `-`; labels must differ. Order here is the column order in the report. Keys are never printed. |
| `private` | object | no | `{names: [..], organisations: [..]}` for people and organisations mentioned who are not participants: colleagues they name, interviewers, the participants' customers. Used only for redaction; never printed. Each value 2–200 characters, at most 100 per list. |

## `sources`

Both `meetings` and `transcripts` are required:
`{ "provider": string|null, "status": enum, "detail": string|null, "folder": string|null }`.

- `provider`: `granola`, `gong`, `fathom`, `fireflies`, `otter`, `zoom`, `manual` (pasted or uploaded) or `null`.
- `status`: `ok`, `partial`, `unavailable`, `denied`, `not_connected`, `not_configured` or `not_requested`. Anything
  but `ok` is printed under Gaps. `meetings.status` other than `ok` adds a **Coverage incomplete** banner at the top.
- `detail` (≤ 300) and `folder` (≤ 120, the Granola folder name) are shown, redacted.

## `participants[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | `P1`…`P999` | yes | Unique. Assign in interview-date order. |
| `consent` | enum | yes | `granted`: agreed to research use including anonymised quotes. `no_quotes`: research use, no quotes. `unknown`: not recorded; treated like `no_quotes`. `declined`: excluded entirely: their evidence is dropped before counting, they are not listed, and a session whose participants all declined is excluded. |
| `segment` | key | no | Must be a `study.segments` key. |
| `private` | object | no | `{names, organisations, emails}` from the recorder's attendee list and the interview itself. Used only for redaction and the duplicate check below; never printed. Each value 2–200 characters (emails 3+ and must contain `@`), at most 100 per list. |

Two participants whose `private` lists share a multi-word name or an email (compared case-insensitively) are rejected
(exit 2): if they are the same person, merge them. Every participant who did not decline must be listed on at least
one included meeting.

## `meetings[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `meeting_id` | string | yes | The recorder's id (Granola UUID or `not_…`), or a neutral id for pasted transcripts (`pasted-1`). Letters, digits, `_`, `-`, up to 64. Unique. |
| `date` | date | yes | Local date of the session. Not after `--as-of`. |
| `excluded_reason` | string ≤ 120 | no | Set it for sessions in the folder that are not interviews for this study (an internal debrief, a pilot). The meeting is then only counted under Method, and evidence must not cite it. Other keys are ignored. |
| `url` | string | no | `http(s)` link to the note. Unsafe links, and links containing a listed identifier, are dropped and counted. |
| `participant_ids` | list of ids | yes (non-empty) | Participants interviewed in this session. Group sessions list several. |
| `transcript` | object | yes | `{status: ok|denied|unavailable|not_requested, text: string|null}`. Put the verbatim transcript in `text` so quotes are checked. `text` with a status other than `ok` is an error. An empty, whitespace-only or invisible-only `text` is an error (exit 2): it usually means a failed fetch, so fetch it again or set `text` to null (its quotes are then reported as not checked). |
| `notes` | object | yes | Same shape, for the AI summary and private notes. |

## `evidence[]`

One quote or note excerpt that the agent coded. Evidence can support several findings.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | `E1`… | yes | Unique. |
| `participant_id` | id | yes | Must be listed in that meeting's `participant_ids`. |
| `meeting_id` | id | yes | An included meeting. |
| `kind` | enum | yes | `said`: what the participant said (attitudes, preferences, descriptions of what they usually do). `did`: behaviour the interviewer observed in the session (screen share, a task), as the notes or the transcript record it. |
| `source` | enum | yes | `transcript` (verbatim speech) or `notes`. The meeting's status for that source must not be `denied`, `unavailable` or `not_requested`. |
| `text` | string | yes | Word for word: one complete sentence, or several consecutive complete sentences, from a single speaker turn. One wrapping pair of quote marks is removed. When the meeting's `transcript.text` / `notes.text` is in the bundle, the script checks the quote against it with the shared `granola_text.check_quote` (whole-sentence rule). The check ignores case and formatting (markdown emphasis, `[link](url)` syntax, Granola's backslash escapes, HTML entities, curly-versus-straight quotes and dashes). Every line, and every speaker label at the start of a line (`System audio:`, `Interviewer:`, `Speaker 2:`, `P3:`, `Dana:`…), starts a new turn, so a participant's line and the interviewer's line can't be joined into one quote; a label later in a line starts a turn only where it begins a sentence and no negation is in scope (`Not even our asst. mgr. Okafor:` is one turn). Rejected (exit 2): partial sentences (so a leading "Nobody" or "not" can't be dropped, and the middle of a sentence can't be quoted), ellipses or cuts (`...`, `…`, `[...]`), speaker labels, quotes that run across two turns, quotes under 3 words, a statement turned into a question or the other way round, and anything else not found. Ellipses, speaker labels and quotes under 3 words are rejected even without source text. Without source text the quote is marked "not checked against source text". The check is mechanical: it confirms the words appear as whole sentences, not that the quote is representative, in context or correctly attributed; speaker turns are found with heuristics. Long quotes are shortened for display only after the check (see `max_quote_chars`). |
| `question` | string ≤ 600 | no | The interviewer's question this answers, word for word and without the speaker label; checked against `transcript.text` when present, with the same whole-sentence rule as `text`. A `text` that is the question itself is rejected. |
| `leading_question` | bool or null | no | `true` when the question suggested the answer. When `question` is given, the script also marks the answer as leading if the question matches one of `leading_question_patterns` (heuristic). `null`/absent with no question is counted as "question wording unknown". |

## `findings[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | `F1`… | yes | Unique. |
| `type` | enum | no | `finding` (default; answers a research question), `job`, `pain`, `workaround`, `desired_outcome`. |
| `rq` | RQ id | for `finding` | Optional for the other types. |
| `statement` | string | yes | ≤ `max_statement_chars`. Write the claim only. A statement whose wording asserts a count or share of participants or their teams/companies/sessions (`most participants`, `5 of 8 users`, `3 of 5 said`, `4 out of 5`, `3/5`, `9 in 10 admins`, `two in three`, `everyone`, `nearly everyone`, `nobody`, `no one`, `all of them`, `none of them`, `all but one`, `only one participant`, `most…` at the start, `majority`, `a handful`, `40% of users`, `70 percent of admins`, `two thirds of…`, `half the teams`, `most teams`, `nearly all`) is rejected (exit 2): the script computes the counts. This is a wording heuristic; phrasings it doesn't recognise can get through, and an ordinary sentence that uses one of these words (`everyone on the team`) is refused too, so rephrase it. |
| `supporting` | list of evidence ids | yes (non-empty) | |
| `contradicting` | list of evidence ids | no | Evidence that cuts against the finding. An id cannot be in both lists. |
| `surprise` | object | no | `{assumption ≤ 300}`: what the team believed before. Listed under Surprises. |

How a finding is counted (distinct participants, never mentions; thresholds from `synthesis.json`):
- **Supporting participants** = participants with any supporting evidence. **Not leading** = those with at least one
  supporting item that is not an answer to a leading question. **Only via leading** = the rest.
- **Contradicting participants** = participants with any contradicting evidence. Someone on both sides is shown as
  "Mixed".
- Band: fewer than `min_pattern_participants` supporters → **Single** (listed under single-participant signals, never
  as a pattern, and not exported as a card). Else at least as many contradicting as supporting → **Contested**. Else
  not-leading supporters ≥ `high_min_participants` and ≥ `high_min_share_of_participants` × included participants, with
  contradicting ≤ `high_max_contradicting_ratio` × not-leading supporters → **High**. Else not-leading supporters ≥
  `medium_min_participants` → **Medium**. Else → **Low**.
- A finding whose supporting evidence all came from participants who declined is dropped (with any contradicting
  evidence it cites) and named under Method.

## `opportunities[]`

`{id: "O1"…, statement, findings: [finding ids]}`. The statement follows the same no-counts rule. Opportunities are
ranked by the script: patterns first (`min_pattern_participants` or more supporters and at least one linked
finding that is not Single), then distinct
not-leading supporters across the linked findings, then all supporters, then fewest contradicting, then the strongest
linked band, then the number of linked findings, then id. Bundle order and any opinion of value or effort play no
part.

## `synthesis.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `min_pattern_participants` | 2 | Supporters needed before a finding is a pattern (minimum 2). |
| `medium_min_participants` | 3 | Not-leading supporters for Medium (≥ `min_pattern_participants`). |
| `high_min_participants` | 4 | Not-leading supporters for High (≥ `medium_min_participants`). |
| `high_min_share_of_participants` | 0.5 | High also needs this share of included participants. |
| `high_max_contradicting_ratio` | 0.25 | High allows contradicting participants up to this share of not-leading supporters. |
| `segment_min_participants` | 2 | Segments smaller than this are left out of segment comparisons. |
| `segment_gap_min_share` | 0.5 | A finding is listed under Differences by segment when the share of supporters differs by at least this much between two compared segments. |
| `max_quotes_per_finding` | 3 | Supporting quotes shown per finding (one per participant first). |
| `max_contradicting_quotes` | 2 | Contradicting quotes shown per finding. |
| `max_quote_chars` | 400 | Longer quotes are shortened for display at a word boundary and end with `…` (contact details are redacted first). The full quote is what was checked. |
| `max_statement_chars` | 300 | Longest statement accepted. |
| `max_single_signals` | 20 | Single-participant signals listed in the report (all are in `--format json`). |
| `date_precision` | `day` | `day`, `month` or `none` for session dates in every format (`--date-precision` overrides it). Coarser dates make a participant harder to recognise. |
| `leading_question_patterns` | see file | Case-insensitive regular expressions for leading question wording. |
| `free_email_domains` | see file | Email domains that are never turned into organisation identifiers. |

## Outputs

- `--format markdown` (default): the report. Bundle values are escaped with the shared `granola_text.md`
  (backslash first, then link, emphasis, code, table, heading, math `$`, highlight `=`, `^` and `:` characters;
  dots inside words escaped so nothing auto-links; `&`, `<` and `>` as entities; one line), so meeting text can't
  create links, mentions or formatting. Links are only the meeting links the script validated.
- `--format json`: the same content structured, plus the markdown. Values are plain redacted text (not markdown
  escaped). Quote text and interviewer questions are `null` for participants without quote consent. Nothing from
  `private`, and no transcript or notes text beyond the coded evidence, is included.
- `--format csv`: insight cards, one row per finding that is a pattern (single-participant signals are not
  exported). Plain redacted text; a cell starting with `=`, `+`, `-`, `@`, `|`, a tab or their full-width forms gets
  a leading `'` so spreadsheets don't run it as a formula. Columns: `study, card_id, type, research_question,
  statement, confidence, supporting_count, supporting_participants, contradicting_count, contradicting_participants,
  only_via_leading_questions, segments, quote_1, quote_1_participant, quote_1_source, quote_2, quote_2_participant,
  quote_2_source, meeting_refs, meeting_links, surprise_assumption`.
- `--no-links` leaves meeting links out of every format (they open the original, non-anonymised notes).
- `--date-precision day|month|none` overrides `date_precision` from the config.

Exit codes: **0** report printed. **2** bad input, config or arguments; the message is on stderr and nothing is
printed on stdout. **1** stdout was closed or the pipe broke before the report was written.

Count-and-share check limits: the check that refuses statements stating a count or share is an English word-pattern heuristic, and it errs in both directions. On a fresh independent set it missed about a third of prevalence claims (often ones about roles outside the participant nouns it knows, such as PMs or buyers) and refused about two in five ordinary workflow, limit and policy statements (for example "Approvals take three to five business days."). A refused statement exits 2 with a message so it can be reworded; a missed one is still printed beside the script-computed counts. Treat the check as a backstop, not proof, and review every finding statement.
