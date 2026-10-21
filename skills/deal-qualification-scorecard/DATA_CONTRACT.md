# Data contract: deal-qualification-scorecard

The agent builds one JSON bundle per deal from Granola (or another recorder, or pasted transcripts) and, if connected, the CRM. `build_scorecard.py` validates it, then scores it against a framework file. Anything that breaks a rule below is exit 2 with a message naming the file and the JSON path, and nothing is printed on stdout.

Conventions:

- Dates are `YYYY-MM-DD`; datetimes are `YYYY-MM-DDTHH:MM[:SS[.fff|.ffffff]]` with `Z` or an offset up to `±14:00`, in ASCII digits, years 1900 to 2999. Python 3.8 and 3.14 accept and reject the same strings.
- Strings are trimmed and runs of whitespace (including newlines) become one space. Titles, names, labels, claim values and questions are at most 300 characters; quotes at most 2,000; `notes_text` and `transcript_text` at most 2,000,000. Lists hold at most 2,000 items unless stated (`account.domains` 20, `matched_by` 5, `parts` 6, `questions` 2 per element, config lists 500).
- `NaN`, `Infinity` and unpaired surrogate escapes (`\ud800`) are rejected. The file must be UTF-8.
- Ids (`meeting_id`, `deal.record_ref`) use only letters, digits and `: / _ . -`.
- URLs must be `http://` or `https://` with a real host name, no user name or password, and a valid port.
- Optional fields may be left out or set to `null`.

## Bundle

```json
{
  "schema_version": 1,
  "account": {"name": "Ostrevane Freight (fictional)", "domains": ["ostrevane.example"]},
  "sources": {
    "meeting_notes": {"provider": "granola", "status": "ok", "plan": "business", "detail": null},
    "crm": {"provider": "hubspot", "status": "ok", "detail": null}
  },
  "window": {"start": "2026-08-01", "end": "2026-10-12"},
  "deal": {
    "name": "Ostrevane — dispatch visibility", "stage": "Proposal", "record_ref": "hubspot:deal/58120",
    "url": "https://crm.example/deals/58120",
    "fields": {"close_date": "2026-11-30", "economic_buyer": "Ruth Abernathy", "champion": "Teodor Vasko"}
  },
  "meetings": [ … ],
  "findings": [ … ],
  "questions": {"decision_process": ["Earlier we heard November 20 and now January 29. Which date is real?"]}
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | `1` | yes | |
| `account.name` | string | yes | Shown in the heading. |
| `account.domains` | list of domains | yes, at least 1 | The account's email domains. Subdomains match. A personal provider in `consumer_domains` (gmail.com …) or a domain overlapping `--internal-domain` is exit 2. |
| `sources.meeting_notes.provider` | `granola` `gong` `fathom` `fireflies` `otter` `zoom` `pasted` `other` | yes | |
| `sources.meeting_notes.status` | `ok` `unavailable` `denied` `not_configured` | yes | Not `ok`: a single deal is not scored (exit 3); in a batch its row says "not scored". |
| `sources.meeting_notes.plan` | `free` `basic` `plus` `business` `enterprise` `unknown` | no (default `unknown`) | `get_account_info` reports it as `mcp_plan`. `free` or `basic` adds a gap when the window starts more than `free_plan_window_days` before `--as-of`, or when there is no `window`. |
| `sources.meeting_notes.detail`, `sources.crm.detail` | string | no | Shown under Gaps. |
| `sources.crm.status` | same four values | yes | `not_configured` when there is no CRM. |
| `sources.crm.provider` | `hubspot` `attio` `salesforce` `pipedrive` `other` | when status is `ok` | |
| `window.start`, `window.end` | dates | no | The search window. Used only for the free-plan gap. `end` before `start` is exit 2. |
| `deal` | object or null | no | Only when `sources.crm.status` is `ok` (otherwise exit 2). `null` = no deal matched. |
| `deal.name` | string | yes | |
| `deal.stage`, `deal.record_ref`, `deal.url` | string | no | `record_ref` like `hubspot:deal/58120`. |
| `deal.fields` | object of string or null | no | CRM values keyed by the framework's `crm_fields[].field` (`close_date`, `champion`, `economic_buyer`, and for BANT `amount`). Other keys are ignored. `null` = empty in the CRM. At most 100 keys. A `close_date` is also shown in the heading. |
| `meetings` | list | yes (`[]` allowed) | One entry per Granola note. |
| `findings` | list | yes (`[]` allowed) | |
| `questions` | object: element key → list of up to 2 strings | no | The agent's discovery questions. Used only for elements that are not evidenced. An unknown element key is exit 2. |

### `meetings[]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `meeting_id` | string | yes | The Granola id (UUID or `not_…`). Repeating an id with the same `start` merges the entries (counted); a quote must then be whole sentences of one entry's text, never running from one into the other. With a different `start` it is exit 2. |
| `title` | string | yes | |
| `start` | datetime | yes | After `--as-of` is exit 2. Its local date (in its own offset) is the meeting date. |
| `url` | URL | no | The note link, e.g. `https://notes.granola.ai/d/<id>`. Quotes link to it. |
| `attendees` | list of `{name, email}` | no | Each needs a name or an email. Emails are lower-cased; addresses matching `bot_email_patterns` are dropped and counted. |
| `matched_by` | list of `domain` `title` `mention` `crm` `user` | no | Why the agent included it. |
| `attendees_incomplete` | bool | no (false) | The agent read the notes and the customer was there though no account address is on the invite. Scored only with a `title`, `mention`, `crm` or `user` match. |
| `exclude_reason` | string | no | The agent excludes it (a look-alike company, a webinar). Listed under "Not scored" with this reason. |
| `notes_read` | bool | no (true) | `false` when the notes could not be read; listed under Gaps. |
| `transcript` | `ok` `denied` `unavailable` `not_requested` | no (`not_requested`) | Outcome of `get_meeting_transcript`. |
| `notes_text` | string | no | The note text the agent read. Needs `notes_read: true`. When present, `notes` quotes from this meeting are checked against it. |
| `transcript_text` | string | no | The transcript the agent read. Needs `transcript: ok`. When present, `transcript` quotes from this meeting are checked against it. An empty or whitespace-only `notes_text`/`transcript_text` is an error (exit 2): it usually means a failed fetch, so omit the field instead. A customer-side quote whose only matching turns are labelled `Microphone` or `Me` (your side) is refused; for this check a line with no label of its own belongs to the last labelled speaker before it, so a `Microphone`/`Me` turn continued on the next line (even after a blank line or a heading) is still your side. |

How meetings are scored:

1. Entries with `exclude_reason` are set aside.
2. The rest are grouped: two entries with the same start instant and the same title (ignoring case and punctuation, in any script) or a shared attendee on `account.domains` are duplicate notes of one meeting. The group takes the first note's title, date and link, its transcript status is the best of its notes' (`ok` > `denied` > `unavailable` > `not_requested`), and it counts as read if any note was read.
3. A group is scored when it has an attendee on `account.domains`, or `attendees_incomplete` with a `title`, `mention`, `crm` or `user` match. Otherwise it is listed as `internal-only` (every attendee has an email on `--internal-domain`) or `nobody from <domains> on the invite`.

### `findings[]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `element` | element key | yes | A key from the framework file, e.g. `metrics`. |
| `parts` | list of part keys | yes, at least 1 | The element's parts this quote supports, e.g. `["baseline"]`. |
| `meeting_id` | string | yes | Must be a `meeting_id` in `meetings`. A finding that cites a meeting that is not scored is dropped and counted. |
| `quote` | string | yes | One or more complete sentences from one speaker turn, copied word for word (see "Quote check"). At least 3 words and `min_quote_chars` characters after normalising. |
| `quote_from` | `transcript` `notes` | yes | `transcript` needs the meeting's `transcript: ok`; `notes` needs `notes_read: true`. |
| `speaker_side` | `customer` `ours` `unknown` | yes | |
| `speaker` | string | no | At most 100 characters. |
| `claim` | `{topic, value, supersedes_earlier}` | no | `topic` must be one of the element's `topics` (matched ignoring case and punctuation). `value` needs a letter or digit. `supersedes_earlier` (default false): the customer said the earlier value changed; only allowed with `speaker_side: customer` (otherwise exit 2). |

**Quote check.** The script checks every quote against the meeting text it was given. A quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting. Partial sentences, ellipses, speaker labels and quotes that run across speakers are rejected. It uses the shared `granola_text.py` (`check_quote`), shipped unchanged next to the script:

1. **Normalising.** The quote and the meeting text are normalised the same way: Unicode NFKC, HTML entities and Granola's markdown backslash escapes (`\&` → `&`) undone, emphasis markers, backticks, link syntax (`[text](url)` → `text`) and list, heading and quote markers removed, curly quotes, apostrophes and dashes made straight, invisible characters dropped, whitespace collapsed. Case is ignored when comparing.
2. **Turns and sentences.** The meeting text is split into speaker turns: a new turn starts at every line (a lower-case line that continues an unfinished line joins it) and at every speaker label at the start of a line; a label later in a line starts a turn only where it begins a sentence and no negation is in scope (a full stop that may follow an abbreviation, as in `Not even our asst. mgr. Okafor:` or `Nobody except the SVP. Okafor:`, does not end the negation). Labels are Granola's labels (`Microphone`, `System audio`, `Speaker A`, `Me`, `Them` …) and `Name:` patterns of one to three capitalised words with an optional `(Role)`. Headings such as `Decision:` or `Next steps:`, and anything containing a negation, quantifier or condition word (`No`, `Nobody`, `None`, `Only`, `If` …), are not labels. Inside a turn, a sentence ends at `.`, `!` or `?` followed by a space and something other than a lower-case letter, never inside a number, after a common abbreviation or at an ellipsis.
3. **Match.** The normalised quote must equal one sentence, or a run of consecutive sentences, of one turn. One pair of outer quotation marks is ignored; a final `.` or `!` may be dropped or swapped; a final `?` must match.

Rejected (exit 2, the message gives the reason): a quote with an ellipsis (`...`, `…`, `. . .`, `[...]`), a speaker label, or fewer than 3 words, whether or not the meeting's text is in the bundle; and, when it is, `not a complete sentence in the meeting text` (so "will renew next quarter" is refused when the text says "Nobody will renew next quarter.", and "satisfied with the rollout" when it says "dissatisfied with the rollout"), `spans two speakers`, `changes the sentence's final question mark` and `not found in the meeting text`. With several failing quotes, the first in bundle order is reported. Without the meeting's text the quote is used but counted as "not checked".

The check is mechanical: it confirms the words appear as whole sentences. It doesn't judge whether a quote is representative, in context, or correctly attributed. Speaker turns are found with heuristics (Granola's labels and `Name:` patterns), so a capitalised `Name:` after a sentence end mid-line starts a new turn even when it continues the same speaker's thought, and all-lower-case mid-line names aren't labels. All-caps mid-line names can be labels after a recognised sentence end. A quote in a script written without spaces between words (Chinese, Japanese, Thai) counts as one word and is refused as too short.

**Value comparison.** A claim or CRM value that is a `YYYY-MM-DD` date is compared as that date. Any other value is compared after Unicode NFKC and case folding with everything but letters and digits removed, so `Ines Marlowe` = `ines marlowe` and `52,000` = `52000`, but `52k` ≠ `52000` and `Jan 29` ≠ `2027-01-29`.

## Element status (computed)

For each part of an element, from the scored findings that list it:

| Part state | Rule |
|---|---|
| `ok` | at least one `customer` finding whose meeting is at most `stale_days` days before `--as-of`'s date |
| `stale` | `customer` findings exist, all older than `stale_days`; shown as "last confirmed <date>, N days ago" |
| `unknown` | no `customer` finding, at least one `unknown` |
| `ours` | only `ours` findings |
| `none` | no finding ("not heard") |

Element status: `contradicted` if any of its topics has two or more current values; otherwise `missing` with no findings, `evidenced` when every part is `ok`, else `partial`.

Topics: claims are walked in meeting order (then bundle order). A value with `supersedes_earlier: true` replaces every earlier, different value and is recorded as a change; any other value joins the current set. Two or more current values are a conflict.

Flags: `ours_only` (every finding is `ours`), `unattributed` (no `customer` finding and not all `ours`), `stale` (every finding older than `stale_days`), `crm_disagrees`.

CRM comparison, for each framework `crm_fields` entry whose `field` is in `deal.fields`: `match`, `disagrees`, `crm_empty` (calls have one value, CRM is null), `calls_disagree` (the topic has a conflict) or `crm_only` (CRM has a value, no call claim). A field that is null with no call claim is not shown.

Discovery questions, for elements that are not evidenced: the agent's `questions` for that element if given; otherwise, up to 2 of: a question naming the conflicting values for each conflicting topic, then the framework's `question` for each part that is not `ok`.

## Framework file

```json
{
  "schema_version": 1, "key": "meddicc", "name": "MEDDICC",
  "elements": [
    {"key": "metrics", "short": "M", "label": "Metrics", "description": "…",
     "parts": [{"key": "baseline", "label": "Current number", "question": "…"}, …],
     "topics": ["Target"]}, …
  ],
  "crm_fields": [{"field": "close_date", "label": "Close date", "element": "decision_process", "topic": "Decision date"}, …]
}
```

`key`, element and part `key`s and `crm_fields[].field` are lower-case letters, digits and `_` (starting with a letter, up to 40). `name` up to 60 characters. 1 to 12 elements; element keys and `short` labels (up to 4 characters) are unique; element `label` up to 60, `description` and part `question` up to 300, part `label` up to 80; each element has 1 to 6 parts with unique keys, and up to 10 topics (`[]` allowed). Up to 20 `crm_fields`, each naming an existing element and one of its topics. Shipped: `framework_meddicc.json` (7 elements), `framework_meddpicc.json` (8, adds Paper process), `framework_bant.json` (4) and `framework_spiced.json` (5).

## Config (`scorecard.json`)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `schema_version` | 1 | 1 | |
| `stale_days` | integer 1–3650 | 45 | Evidence older than this many days no longer confirms a part. |
| `max_quotes_per_element` | integer 1–20 | 2 | Quotes shown per element in the scorecard (all are in `--format json`). |
| `max_quote_chars` | integer 40–2000 | 240 | Longer quotes are shortened at a word boundary with "…" in the scorecard. Emails and phone numbers are redacted before the text is shortened, so a cut can't leave part of an address the redaction recognised. |
| `min_quote_chars` | integer 1–200 | 12 | Shorter quotes are exit 2. |
| `free_plan_window_days` | integer 1–3650 | 30 | Granola's free-plan history, for the gap line. |
| `bot_email_patterns` | list of strings | note-taker bots | `bot@` matches that whole local part; a domain (`read.ai`) matches it and its subdomains; anything else is a substring. |
| `consumer_domains` | list of strings | personal email providers | Never accepted as an account or internal domain. |

Every key is required; a missing key, a wrong type, `null` or an out-of-range value is exit 2.

## Command line

| Option | Meaning |
|---|---|
| `--bundle FILE` | Required. Repeat for a batch (up to 200): the output is one table. |
| `--framework FILE` | Default `framework_meddicc.json` next to the script. |
| `--config FILE` | Default `scorecard.json` next to the script. |
| `--as-of DATETIME` | Required. The only "now". |
| `--internal-domain DOMAIN` | Required, repeatable. |
| `--format scorecard\|crm-draft\|json` | Default `scorecard`. `crm-draft` needs one bundle with a deal. `--json` is the same as `--format json`; combined with another `--format` it is exit 2. |

Exit codes: 0 output printed; 2 bad input, config, framework or arguments (nothing on stdout); 3 a single deal whose meeting-notes source is not `ok` (message on stderr, nothing on stdout); 6 the output could not be written (closed pipe, full disk).

## JSON output

Single deal: `account`, `account_domains`, `framework`, `sources`, `deal` (name, stage, record_ref, url), `scored`, `coverage` (count per status), `elements[]` (key, label, status, flags, `parts[]` with state and `last_confirmed`, every `evidence[]` item with meeting id, date, url, quote, `quote_from`, `quote_checked`, side, speaker, parts, stale and claim, `topics[]` with current values, conflict and changes, `questions`), `crm_comparison[]`, `meetings[]` (with merged `note_ids`), `excluded_meetings[]`, `quotes` (used, checked), `findings_not_used`, `schema_version`, `as_of` and `markdown` (the scorecard). Batch: `{schema_version, as_of, framework, deals: [...], markdown}` with the same per-deal objects (`scored: false` for a deal whose meeting notes were not read). Only `markdown` is escaped; other strings are raw, with the emails and phone numbers it recognises replaced by `[email removed]` / `[phone removed]` (pattern-based; it can miss formats) and invisible characters dropped.


Round 12 clarification: all-caps mid-line labels can start a turn after a recognised sentence boundary; lower-case mid-line labels are not recognised. A short determiner-led role abbreviation does not erase negation. An unfinished negated clause ending in an adverb can continue across a newline. These mechanical rules do not evaluate sentence meaning.


In recorder-labelled text (Me/Microphone/System audio/Them), only these recorder labels switch recording side. A prose `Look:` or a manually mixed name label cannot turn a seller continuation into customer evidence. Preserve original recorder labels; mixed manual labels may be refused conservatively.
