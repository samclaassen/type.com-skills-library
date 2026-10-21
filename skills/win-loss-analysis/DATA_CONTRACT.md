# Data contract: win-loss-analysis

The agent reads the CRM (or a CSV export) and the meeting recorder through the Space's integrations and normalises
everything into **one JSON bundle** per report. `build_winloss.py` reads only that bundle, the optional deals CSV,
`winloss.json` and `reason_taxonomy.json`. It never calls an API, never writes a file, and never reads the clock
(`--as-of` is the only "now").

Conventions:
- UTF-8 JSON. Unknown keys are ignored, so you can keep provenance such as `_note` or raw CRM ids. `NaN`,
  `Infinity`, numbers too large for a float, integers longer than 30 digits and `\ud800`-style lone surrogate
  escapes are rejected (exit 2).
- **date** = `YYYY-MM-DD` (ASCII digits, years 1900–2999). **datetime** = `YYYY-MM-DDTHH:MM[:SS[.ffffff]]` followed by
  `Z` or a `±HH:MM` offset up to `±14:00` (`2026-09-29T10:00:00-07:00`). Hours run 00–23 and minutes and seconds 00–59.
  Anything else, including a datetime without an offset, `T24:00`, compact forms (`20260929T100000Z`) and ISO-week
  dates (`2026-W40-2`), is an error on every Python version. Surrounding whitespace is trimmed.
- Strings: whitespace (including newlines) is collapsed; control, bidi-override and invisible characters (zero-width
  space, soft hyphen, and zero-width joiners next to ASCII text) are dropped. Displayed text is escaped by the script
  for markdown and Slack, so never pre-escape it. Email addresses and phone numbers in displayed text are replaced
  with `[email removed]` / `[phone removed]` (`granola_text.redact_contacts`). Redaction is pattern-based and can
  miss formats. Phone numbers are removed only when there's a clear signal (a `+` country code, a word like "call" or
  "mobile", or a standard US/UK/French format such as `07700-900123`), so number lists, prices, ranges
  (`40000-50000`), dates and reference numbers are left alone.
- **url** fields must be absolute `http://` or `https://` links with a real host (at least one dot, no `user@`, port
  1–65535). Unsafe characters are percent-encoded. Any other value is dropped: the text is shown without a link and
  Coverage counts it under "Links dropped".
- Length limits (exit 2 when exceeded): account 200 characters, meeting title 300, segment, stage and competitor 80,
  taxonomy label 120, CRM field name 80 and value 2,000, source detail 300, period label 80, quote `max_quote_chars`
  (400), speaker role `max_role_chars` (60).
- Optional means the key may be absent or `null`. It never means the agent should make up a value.
- Exit 2 messages name the file and the JSON path, e.g.
  `bundle b.json: $.evidence[3].meeting_id: meeting 'w20' is not linked to deal 'D-1005' (…)`, or the CSV row.

## Top level

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `period` | object | yes | `start` (date), `end` (date, not after `--as-of`), optional `label` ("Q3 2026"). At most 731 days. Deals whose `close_date` falls outside it are ignored and counted. |
| `currency` | string | no | 3-letter code (`USD`). Every deal `currency` must match it (or, without it, each other). |
| `sources` | object | yes | Status of each source; see below. |
| `deals` | list | yes, unless `--deals-csv` is given (then absent or `[]`) | Every deal closed won or lost in the period. |
| `meetings` | list | yes (may be `[]`) | **Every** meeting the recorder listed for the meeting window, internal ones included. |
| `evidence` | list | yes (may be `[]`) | Extracted reasons, objections and competitor mentions. |

## `sources`

Required keys: `deals`, `meetings`, `transcripts`. Each is `{ "provider": string|null, "status": enum, "detail": string|null }`.

- `status`: `ok`, `partial` (some reads failed, e.g. some transcripts denied), `unavailable` (connected, read failed),
  `denied`, `not_connected`, `not_configured`, `not_requested`. Anything but `ok` appears on the report's **Gaps**
  line with its `detail`. Set `ok` only after a real read succeeded. If `deals` is not `ok` the report opens with
  "Deal list incomplete"; if `meetings` is not `ok`, with "Call coverage incomplete".
- `provider` values with nice labels: `granola`, `gong`, `fathom`, `fireflies`, `otter`, `zoom`, `manual`,
  `hubspot`, `attio`, `salesforce`, `pipedrive`, `csv`, `none`. Other values are shown as given (max 40 characters).
- `sources.meetings.history_days` (int, optional): how far back the recorder's plan shows notes (30 on free Granola
  plans). Deals closed before `--as-of` minus this are flagged in "Deals with no call coverage" and Coverage says
  calls before that date may be missing.

## `deals[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `deal_id` | string | yes | Unique. Letters, digits and `: / _ . # -` only, max 80 (the CRM's record id is ideal). |
| `account` | string | yes | Company name as the CRM shows it. Two deals may share an account; two names for one domain are rejected. |
| `domains` | list of strings | no | The company's email domains (`outfitters.example`). Free-mail domains (`free_email_domains`) are rejected here. Empty = matched only through meeting `deal_ids`. |
| `outcome` | `won` \| `lost` | yes | Open deals do not belong in the bundle. |
| `close_date` | date | yes | Not after `--as-of`. |
| `amount` | number ≥ 0 | no | Up to 1e15. Unknown amounts are reported as unknown. |
| `currency` | string | no | 3-letter code; see top-level `currency`. |
| `segment` | string | no | The CRM's own segment or size band. |
| `stage_lost_at` | string | no | Lost deals only (exit 2 on a won deal): the last stage before closed lost. |
| `competitors` | list of strings | no | The CRM's competitor field values. Names match ignoring case, full-width forms, invisible characters and repeated or surrounding spaces (`count whistle` and `Countwhistle` stay two names). |
| `lost_to` | string | no | Lost deals only. Must equal (compared like competitor names) a value in this deal's `crm_fields`, or the `competitor` of a `loss_reason` evidence item on this deal. With `--deals-csv`, the CSV's own `lost_to` column counts as that CRM record. Makes the loss competitive. |
| `crm_url` | url | no | Link to the CRM record (used on CRM-field evidence and in the Deals table). |
| `crm_fields` | object | no | Field label → value as read (strings only; `null` values are skipped). Only these can be cited with `source: crm_field`. Labels match ignoring case, spaces, `_` and `-`. |

## Deals CSV (`--deals-csv`)

UTF-8 (a BOM is fine), comma-separated, first row is the header. Headers are matched case-insensitively with spaces and
`-` read as `_`. Required columns: `deal_id`, `account`, `outcome`, `close_date`. Optional core columns: `domains` and
`competitors` (`;`-separated), `amount`, `currency`, `segment`, `stage_lost_at`, `lost_to`, `crm_url`. Every other
column with a value becomes a `crm_fields` entry under its original header (for example `Closed Lost Reason`).
- `outcome`: `won`, `lost`, `Closed Won` or `Closed Lost` (any case).
- `amount`: a plain number (`24000`, `24,000.00`), optionally after `$ € £ ¥` or a 3-letter code; negative numbers,
  European `12.345,00` and other forms are rejected with the row number.
- Every row must have as many cells as the header; empty rows are skipped; NUL bytes and broken quoting are rejected.
- The values then go through the same checks as `$.deals` (errors name `row N`).

## `meetings[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `meeting_id` | string | yes | Unique. The Granola note id. |
| `title` | string | yes | |
| `start` | datetime | yes | With its UTC offset. Not after `--as-of`. |
| `url` | url | yes | Note link (`https://notes.granola.ai/d/<id>`). |
| `attendees` | list | yes (may be `[]`) | `{email}` per listed participant; other keys ignored. Every attendee, including your colleagues. |
| `deal_ids` | list of strings | no | Deals this meeting belongs to when no attendee's domain matches (for example a customer on a personal address). Flagged in Coverage when the domain doesn't match. |
| `transcript` | enum | no | `ok`, `denied`, `unavailable`, `not_requested` (default). |
| `notes` | enum | no | `ok` (default) or `unavailable`. |
| `notes_text` | string | no | The notes or AI summary text, only to check quotes against; never printed. Not allowed when `notes` is `unavailable`. |
| `transcript_text` | string | no | The transcript text, only to check quotes against; never printed. Not allowed unless `transcript` is `ok`. |

How the script uses meetings:
- **Bots**: attendees whose domain is (or is a subdomain of) `ignored_attendee_domains`, or whose local part
  (ignoring a `+tag`) is in `ignored_attendee_local_parts`, are ignored.
- **Internal**: every remaining attendee on `--internal-domain` (subdomains count) or `--internal-email` → internal;
  only one remaining attendee and nobody outside → "only the note taker listed". Neither is linked to a deal; evidence
  citing them is dropped and counted.
- **Linking**: an outside attendee whose domain equals or is a subdomain of a deal's `domains` links the meeting to
  that deal (free-mail domains never match); `deal_ids` adds explicit links. A meeting can belong to several deals.
- **Duplicates**: two notes with the same start minute (in UTC) and a shared outside attendee, directly or through a
  third note, are one call; the note with a readable transcript (then readable notes, then the lower id) is kept and
  evidence citing the other is attributed to it.
- **Coverage**: a deal's calls are its linked meetings whose notes or transcript are readable. Deals with none are
  listed under "Deals with no call coverage".

## `evidence[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | no | Unique; defaults to `e<n>`. |
| `deal_id` | string | yes | Must be in the deal list. |
| `kind` | enum | yes | `loss_reason` (lost deals only), `win_reason` (won deals only), `objection`, `competitor_mention`. |
| `category` | string | for reasons and objections | A key from the taxonomy section for that kind (`loss_reasons`, `win_reasons`, `objections`). Not allowed on `competitor_mention`. |
| `competitor` | string | for `competitor_mention` | Optional on the other kinds (for example the competitor a loss went to). |
| `source` | enum | yes | `transcript`, `note` or `crm_field`. |
| `meeting_id` | string | for `transcript` and `note` | A meeting in `$.meetings` linked to this deal. Not allowed with `crm_field`. |
| `crm_field` | string | for `crm_field` | A label in this deal's `crm_fields`. |
| `quote` | string | yes | `transcript`: the customer's exact words, one or more complete sentences of one speaker turn. `note`: one or more complete sentences of the notes, exactly as written. `crm_field`: the whole field value, or whole sentences of it. One pair of wrapping quote marks is removed. At most `max_quote_chars`. |
| `speaker_role` | string | for `transcript` | A role such as "Procurement lead"; never a name, email, phone number or link. Optional for `note`. |
| `speaker_side` | enum | no | `customer` (default) or `unconfirmed` (shown as "speaker unconfirmed"). `internal` is rejected. |

Rules the script enforces:
- `source: transcript` needs the meeting's `transcript: ok`; `source: note` needs `notes: ok`.
- Objections must cite a meeting (not a CRM field). Objections from meetings dated after the deal's close date are
  dropped and counted; reasons from after the close (loss debriefs) are kept.
- **Whole-sentence check** (`granola_text.check_quote`): when the meeting has `transcript_text` (for transcript
  quotes) or `notes_text` (for note excerpts), the quote must equal one complete sentence, or a run of consecutive
  complete sentences, inside one speaker turn of that text. Both sides are normalised first (NFKC, HTML entities,
  Granola backslash escapes, markdown emphasis and link syntax, curly quote marks and dashes, whitespace) and case is
  ignored. One pair of wrapping quote marks is ignored; a final `.` or `!` may be dropped or swapped, a final `?`
  must match. Rejected with the reason (exit 2): an ellipsis, a speaker label, fewer than 3 words, part of a
  sentence (so a leading "Nobody" or "not" can't be dropped), and text that only exists across two turns. A turn
  starts at each line and at each speaker label at the start of a line (`Microphone:`, `System audio (Org):`, `Speaker 2:`,
  `Me:`, `Them:`, `Name:`, `Name (Role):`); a label later in a line starts a turn only where it begins a sentence and no
  negation is in scope (`Not even our asst. mgr. Okafor:` is one turn); label detection is heuristic. Without the meeting text, a quote is still
  rejected for an ellipsis, a speaker label or fewer than 3 words, and Coverage counts it as not checkable. A
  `crm_field` quote must be the whole field value (ignoring case, formatting and a final `.` or `!`) or pass the
  same whole-sentence check against it. The check is mechanical: it doesn't judge whether a quote is
  representative, in context or correctly attributed. One attribution check exists: a customer quote is refused when every turn holding it
  is labelled `Microphone` or `Me` (the seller's side). For this check a line with no label of its own belongs to the last labelled
  speaker before it, so a `Microphone`/`Me` turn continued on the next line (even after a blank line or a heading) is still the seller's.
- Evidence for deals outside the period, or citing internal meetings, is dropped and counted. Identical evidence
  (same deal, kind, category, competitor, call and text) is counted once.

## `winloss.json` (config)

| Key | Default | Notes |
| --- | --- | --- |
| `min_sample_for_percent` | 5 | Groups smaller than this show counts, no percentages (1–1000). |
| `recurring_min_deals` | 2 | Lost deals an objection needs to be listed as recurring. |
| `max_quotes_per_reason` | 3 | Evidence lines per reason or objection (0–50); one per deal first. |
| `max_quotes_per_competitor` | 3 | Customer quotes per competitor (0–50). |
| `max_quote_chars` | 400 | Longest quote accepted (20–5000). |
| `max_role_chars` | 60 | Longest speaker role (5–200). |
| `currency_symbol` | `$` | Up to 5 plain characters (no markup characters). |
| `stage_order` | Discovery … Negotiation | Your pipeline's stage names in order, for the stage table; other stages follow by count; "not recorded" is last. |
| `ignored_attendee_domains` | note-taker vendors | Domains whose attendees are bots. |
| `ignored_attendee_local_parts` | `notetaker`, `noreply`, … | Local parts (before `+`) that are bots. |
| `free_email_domains` | gmail.com, outlook.com, … | Personal email domains: never used to match a deal. |

All keys are required; lists may be empty but not `null`.

## `reason_taxonomy.json`

```json
{ "version": 1,
  "loss_reasons": { "price_value": {"label": "Price or value not justified", "description": "…"},
                    "no_decision_status_quo": {"label": "No decision: stayed with the status quo", "no_decision": true} },
  "win_reasons": { "time_to_value": {"label": "Fast time to value"} },
  "objections": { "price_value": {"label": "Price or value"} } }
```

Keys are lower-case letters, digits and `_` (max 60); labels must be unique within a section (ignoring case).
`no_decision: true` is allowed only on loss reasons and decides the no-decision loss type. Each section needs at
least one category. Edit a copy and pass it with `--taxonomy`; `--list-categories` prints what the script loaded.

## JSON output (`--format json`)

`period`, `as_of`, `weighting`, `min_sample_for_percent`, `currency`, `totals` (deals, won, lost,
`win_rate_percent` or `null` below the threshold, `outside_period`), `loss_types` (deal ids and ranked reasons per
type), `loss_reasons`, `win_reasons` (each with `deal_count`, `of`, `percent` or `null`, `deal_ids`, `amount`,
`amount_unknown_deals`, `evidence_ids`), `won_without_reason`, `competitors` (from our side: `deals`, `we_won`,
`we_lost_to_them`, `we_lost_otherwise`, `our_win_rate_percent` or `null`, `deal_ids`, `mentioned_only_deal_ids`,
`evidence_ids`), `stages`, `segments`, `objections`,
`no_call_coverage`, `deals`, `evidence` (each with `verified_against_text`), `coverage`, `sources` and the full
`markdown`.


In recorder-labelled text (Me/Microphone/System audio/Them), only these recorder labels switch recording side. A prose `Look:` or a manually mixed name label cannot turn a seller continuation into customer evidence. Preserve original recorder labels; mixed manual labels may be refused conservatively.
