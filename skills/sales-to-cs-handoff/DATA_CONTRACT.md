# Data contract: sales-to-cs-handoff

The agent reads Granola (or another recorder), the CRM and email through the
Space's integrations and normalises everything into **one JSON bundle per
account**. `build_handoff.py` reads only that bundle and `handoff.json`. It
never calls an API and never reads the clock: `--as-of` is the only "now".

Conventions:
- UTF-8 JSON. Unknown keys are ignored, so you can keep provenance such as `_note` or raw ids.
- **date** = `YYYY-MM-DD`. Where a date is expected, a full **datetime** (below) is also accepted and its local date as written is used.
- **datetime** = `YYYY-MM-DDTHH:MM`, optionally `:SS` and `.fff` or `.ffffff`, then `Z` or `±HH:MM` (`2026-09-15T11:00:00-07:00`, `2026-09-15T18:00:00Z`), in ASCII digits, with hours 00–23, minutes and seconds 00–59 and an offset of at most ±14:00 (so `T24:00` and `+00:60` are errors). Both are checked against these exact shapes before parsing, so every Python from 3.8 behaves the same: compact (`20260915T110000-0700`), ISO-week (`2026-W38-2`), ordinal (`2026-258`), space-separated or offset-less values, impossible dates and years outside 1900–2999 are errors (exit 2).
- Numbers must be finite: `NaN`, `Infinity` and overflowing values (`1e999`) are errors. Required keys must be present and of the documented type; a wrong type is an error, never a crash.
- Optional means the key may be absent or `null`. It never means the agent should make up a value.
- Strings are trimmed and runs of whitespace collapse to one space. Printed text also drops invisible characters (control characters, zero-width space, soft hyphen, BOM, and direction marks, overrides and isolates such as U+202E), which cannot change the words but can reorder or hide them.
- **Quotes are verbatim.** Copy the words exactly as the transcript, notes, email or CRM shows them. The agent copies the quotes; the script does not check them against the meeting text (the bundle carries none). Granola returns notes as markdown: drop its backslash escapes (write `~`, not `\~`), because the script escapes for each output itself and prints a backslash as a backslash. One surrounding pair of quotation marks (`"…"` or `“…”`) is stripped, and the script adds its own; a quote with other quotation marks inside (`"Fast" beats "cheap"`) is kept exactly.
- **Links** (`url` fields, `--doc-url`) must be absolute `http://` or `https://` URLs with a real host name or IPv4 address (dotted, letters, digits and `-`), a port from 1 to 65535 if any, and no user name or password. Any other bundle link (`javascript:`, `data:`, relative, no scheme, `https://notes.example\@evil.example`, `https://user:pw@…`) is dropped, its text is kept, and the drop is counted under Gaps; a bad `--doc-url` is an error. Kept links are percent-encoded (spaces, `(`, `)`, `<`, `>`, `"`, `|` and other unsafe characters).
- **Printed text is escaped for its format**, in one pass, backslash included: markdown (`\`, `[`, `]`, `*`, `_`, `` ` ``, `~`, `|`, `$` and `=` are backslash-escaped; `&`, `<`, `>` become `&amp;` `&lt;` `&gt;`; `://` and every dot inside a word are backslash-escaped, which renders the same), so no value, even one written as `\[click\](https://…)`, can form a link, image, raw HTML, emphasis, math (`$…$`), highlight (`==…==`), a table-cell break, or an autolink from a URL, `www.` or a bare domain (the `$` of the deal amount is escaped too); Slack (`&`, `<`, `>` become entities, so `<!channel>`, `<!here>`, `<@U…>`, `<#C…>` and `<url|label>` in a value are inert, and `*`, `_`, `~`, `` ` ``, `@`, `[` and `]` become full-width look-alikes so a value cannot change formatting or form a markdown link if a tool converts the post; the only Slack link is the doc link) and the CRM note (`&`, `<`, `>` become entities; `\`, `[`, `]` are backslash-escaped). Slack and the CRM note have no backslash escape, so in both `://` becomes look-alike slashes (`:∕∕`) and a dot before a letter or after `www` a look-alike dot (`․`): a URL, `www.` or domain in a value is not linked. Enum codes such as `economic_buyer` are left as they are. Write values as plain text; do not pre-escape them. In `--json` only `markdown` is escaped; the other fields are raw values (emails and phones removed).
- Email addresses are used only to classify and merge people. The script replaces the email addresses and phone numbers it recognises in any printed text (names, titles, notes, quotes, answers) with "(email removed)" / "(phone removed)", in every format and in `--json`. Email addresses may use letters in any script (`josé@empresa.example`) and a full-width `＠`. The match is pattern-based and can miss formats (an address written as `maya at northwind dot example` stays): it removes a number only on a clear signal: a leading `+` country code (`+33 1 23 45 67 89`, `+33 (0)1 23 45 67 89`, `+81-90-1234-5678`, `+49 30 12345678`, `+14155550134`, 8 digits or more; `0049 30 12345678` counts too), a cue word right before it ("call", "phone", "tel", "mobile", "cell", "fax", "sms", "WhatsApp", "text me on", with only short words such as "me", "on", "at", "is" or punctuation in between), or a standard phone format of 10 to 15 digits with the same separator between every group: US `(555) 123-4567`, `555-123-4567`, `555.123.4567`, `1-800-555-0199`, and spaced US numbers such as `415 555 0134` (area/exchange groups start with 2–9; repeated groups remain ambiguous lists); French `06 12 34 56 78` (five 2-digit groups starting with 0); UK and German numbers with a trunk 0 and 10 or 11 digits in 2 or 3 groups (`07700 900123`, `07700-900123`, `020 7946 0958`, `030 12345678`). Dates, times, date ranges, version numbers, IP addresses, amounts, seat counts and numbers after "PO", "order", "invoice", "account", "ticket" and similar labels are left alone, and so is a 7-digit local number. Without a phone signal, tested number-list patterns are preserved: repeated values (`36000 36000 38000`), prices and decimals (`9.99 19.99 29.99`), tiers, years and ranges in any order (`500 250 1000 750`, `0800 0900 1000`). Standard spaced US grouping is itself a phone signal. Ambiguous numeric content may be misclassified; review the output and keep contact details out of free text.

## Top level

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `account` | object | yes | See below. |
| `sources` | object | yes | Status of each source. See below. |
| `search` | object | yes | The window and method used to find meetings. Printed under Coverage. |
| `deal` | object or null | no | The CRM deal. `null` or absent when no deal matched or `crm: none`. |
| `email` | object | no | `{ "threads_read": int }`. How many threads were read, for the Sources line. |
| `meetings` | list | yes (may be `[]`, but the key must be present) | Every candidate meeting note, including internal-only ones and duplicate notes. |
| `excluded_meetings` | list | no | Meetings you looked at and rejected, with the reason. |
| `stakeholders` | list | no | Customer people you can describe. |
| `findings` | object | no | Everything extracted. See below. |

## `account`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | As the CRM or the user gives it. |
| `domains` | list of strings | yes (may be `[]`, but the key must be present) | Every email domain the account uses, e.g. `["quillfenfreight.example", "quillfen-eu.example"]`. Lower-cased, and a leading `@` or `www.` is dropped. Subdomains match. An internal domain or a personal email provider (`consumer_domains` in `handoff.json`) is rejected. Empty only when the account has no company domain; then meetings must be matched by title, mention, CRM or user. |
| `aliases` | list of strings | no | Other names used in titles and notes, for your own title and mention search. The script does not use them; list the names you searched in `search.terms` so Coverage shows them. |

## `sources`

Required keys: `meeting_notes`, `crm`, `email`. Each is
`{ "provider": string|null, "status": enum, "detail": string|null }`.

`status` is one of `ok` (a real read succeeded), `unavailable` (connected, but
the read failed), `denied` (the provider refused access, for example a
workspace setting or plan limit), or `not_configured` (for example
`crm: none`). Anything other than `ok` appears in the Gaps line with `detail`.

Provider values with nice labels: `granola`, `gong`, `fathom`, `fireflies`,
`otter`, `zoom`, `chorus`, `upload` (pasted or attached transcripts),
`hubspot`, `attio`, `salesforce`, `gmail`, `outlook`, `none`.

`sources.meeting_notes` also takes:

| Key | Type | Notes |
| --- | --- | --- |
| `plan` | enum | `free`, `basic`, `plus`, `business`, `enterprise` or `unknown` (default). From Granola `get_account_info.mcp_plan`. `free`/`basic` with a search window older than `free_plan_window_days` (30) raises a coverage gap. |
| `scopes` | list | From `get_account_info.mcp_note_access.scopes`, e.g. `["personal", "public"]`. If given without `public`, a gap says teammates' unshared notes are not visible. |

## `search`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `from`, `to` | date | yes | The window searched. `to` may not be before `from`. |
| `methods` | list of strings | no | Plain description, e.g. `"Granola list_meetings by attendee domain"`. |
| `terms` | list of strings | no | Names and aliases searched. The account domains are added when printed. |

## `deal` (CRM)

| Key | Type | Req | HubSpot | Attio | Salesforce |
| --- | --- | --- | --- | --- | --- |
| `record_ref` | string | no; **required for `--scheduled`** (letters, digits and `: / _ . -` only) | `hubspot:deal/<id>` | `attio:deal/<record_id>` | `salesforce:opportunity/<Id>` |
| `url` | string | no | deal URL | record URL | record URL |
| `name` | string | no | `dealname` | deal name attribute | `Name` |
| `stage` | string | no | `dealstage` as its pipeline label | `stage` | `StageName` |
| `closed_won` | bool | **yes** | stage is a closed-won stage | stage is your won stage | `IsWon` |
| `amount` | number | no (finite, 0 to below 1e15) | `amount` | `value` | `Amount` |
| `currency` | string | no | ISO code, default `USD` (printed as `$`). | | |
| `amount_period` | enum | no | `yr` (default), `mo`, `one_time` | | |
| `close_date` | date | no | `closedate` | `close_date` | `CloseDate` |
| `created_date` | date | no | `createdate` | `created_at` | `CreatedDate` |
| `owner` | string | no | owner's name | `owner` | `Owner.Name` |
| `contract_start` | date | no | | | |
| `term_months` | int | no | | | |
| `products` | list of strings | no | line item names | | `OpportunityLineItem` names |
| `contract_read` | bool | no | `true` only when you read the contract, order form or line items. Default `false`. | | |

The handoff key is `record_ref` (prefixed with the CRM provider when it has no
`:`), otherwise `account:<first domain>`, otherwise `account:<name slug>`
(accents folded to ASCII; a name with letters in another script, such as
`株式会社…` or `ООО …`, also gets a short hash of the name so two such accounts
never share a key; the provider is slugged the same way). A scheduled run
requires `record_ref` (exit 2 without it), so every run keys on the same deal
even if the account's domains change. The key is printed exactly, in a code
span, on the `Handoff key:` line.
Account domains must be plain host names (letters, digits, `-` and `.`; use
the `xn--` form for accented domains).

## `meetings[]`

List **every** note you found for the account, including duplicate notes of
the same meeting (two teammates recorded it) and internal-only meetings. The
script excludes, merges and orders them.

| Key | Type | Req | Notes | Granola source |
| --- | --- | --- | --- | --- |
| `meeting_id` | string | yes | Unique per note. Repeating an id with the same title and start is merged and counted. Repeating it with a different title or start is an error. | `list_meetings` `meeting id` |
| `url` | string | no | Note link (`http(s)` only), used for every citation. | `url` (`https://notes.granola.ai/d/<id>`) |
| `title` | string | yes | Granola titles arrive HTML-escaped (`&amp;`); unescape them. | `title` |
| `start` | datetime | yes | With offset. Granola shows `Sep 15, 2026 11:00 AM PDT`; convert it to `2026-09-15T11:00:00-07:00`. The local date as written is the date shown. | `date` |
| `source` | string | no | Defaults to `sources.meeting_notes.provider`. | |
| `attendees[]` | list | no | `{ "name": string, "email": string }`, at least one of the two. Include the note creator and every participant, internal and external. Addresses matching `bot_email_patterns` (note-takers) are dropped and counted. | `known_participants` |
| `attendees_incomplete` | bool | no | Default `false`. Set `true` when the notes show the customer was there but the participant list doesn't identify them (a phone call, an in-person meeting, a customer on a personal address). A meeting that is internal-only or unclassified (below) is kept only when this is `true` and `matched_by` includes `title`, `mention`, `crm` or `user`. | |
| `matched_by` | list | yes | At least one of `domain`, `title`, `mention`, `crm`, `user`. | |
| `notes_read` | bool | no | Default `true`. `false` = matched but not read; listed as a gap. | `get_meetings` succeeded |
| `transcript` | enum | no | `ok`, `denied`, `unavailable`, `not_requested` (default). | `get_meeting_transcript` outcome |

Attendee classification (by email domain, with `--internal-domain`):
**ours** (internal, subdomains included; each `--internal-domain` must be a plain domain such as `corvalto.example`, one per flag, or the run exits 2), **account** (an `account.domains`
match), **personal** (a `consumer_domains` provider), **other** (anything
else, for example a consultant). An attendee with no email is "unknown".
A name-only or personal-email attendee whose name (case and accents
ignored) also appears with an internal address in any listed meeting, or is
the deal `owner`, is **ours**: a colleague, never a stakeholder.

**Internal-only:** every listed participant is ours → excluded and named
under Coverage. **Unclassified:** no account or other-domain participant,
only ours plus unknown or personal-email people → excluded and named under
Coverage as "no customer attendee identified", because it may be an internal
review. Either is kept when `attendees_incomplete` applies (above). A meeting
with no participants listed at all is kept and shown as "attendee list
incomplete". A finding citing an excluded meeting is an error.

**Pasted or uploaded transcripts** (`meeting_notes: upload`, or a recorder
without note links): give each one a stable `meeting_id` such as
`upload:<file name>`, leave `url` out (citations then show the title and
date), list participants if the transcript names them, and set
`transcript: ok`.

**Duplicate notes:** two notes are the same meeting when their starts are
the same instant in UTC and either their titles match (case, accents and
punctuation ignored) or they share a non-internal participant email. The
note that was read, with the best transcript status (`ok` > `denied` >
`unavailable` > `not_requested`), is primary; ties break on id. Participants
are unioned, every note URL is kept, and a finding may cite any of the ids.

## `excluded_meetings[]`

`{ "meeting_id", "title", "start" (datetime), "reason" }`, all required. Use it
for look-alikes you rejected, such as a different company with the same name.
Findings may not cite them. An id may be in `meetings` or here, not both.

## `stakeholders[]`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | |
| `email` | string | no | Used to link the person to meeting participants. Never printed. An internal-domain address is an error: colleagues are not stakeholders. |
| `title` | string | no | Also shown next to their name in quote attributions. |
| `roles` | list | no | Any of `champion`, `economic_buyer`, `signer`, `executive_sponsor`, `day_to_day`, `user`, `technical_owner`, `data_owner`, `procurement`, `legal_security`, `influencer`, `detractor`, `unknown` (default). Use evidence only (see SKILL.md Step 4). `signer` and `economic_buyer` count for each other when checking for a missing economic buyer. |
| `cares_about[]` | list | no | `{ "text", "source", "quote"?, "quote_from"?, "speaker"? }`. `source` is always required. |
| `note` | string | no | A short fact from the CRM contact record or the user, printed as "Note (CRM or user)". Meeting evidence goes in `cares_about` with its source. |

Entries are merged by email, then by name (case and accents ignored). Customer
participants of the included meetings who are not listed here are added with
role `unknown` and "Not described in the notes". The script computes meetings
seen, first and last seen, and these flags (thresholds in `handoff.json`):

- **appeared once**: in exactly 1 meeting when there are at least `single_appearance_min_meetings` (3).
- **went quiet**: in 2 or more meetings, the last of which is followed by at least `quiet_meetings` (2) account meetings spanning at least `quiet_days` (14) days.
- **not in any meeting read**: listed but in no included meeting. For a signer or economic buyer this is called out in "Read this first".

Missing `key_roles` (default champion and economic buyer) are flagged.

## `findings`

Every item has a `source`, and most can carry a quote:

```json
"source": {"type": "meeting", "meeting_id": "sample-k2"}
"source": {"type": "email", "date": "2026-09-30", "label": "Email from Dana Ortiz", "url": null, "ref": "gmail:thread/…"}
```

- `type`: `meeting` (needs `meeting_id`, which must be in `meetings[]` and not excluded), or `email`, `crm_note`, `crm_field`, `slack`, `doc` (need `date`; `label` and `url` are optional, `ref` is never printed).
- Quote fields: `quote` (verbatim), `quote_from` (`transcript`, `notes`, `email`, `crm`, `slack`, `doc`; required with a quote), `speaker` (optional; leave it out when the transcript only labels an audio source).
- `quote_from: transcript` needs a meeting source whose `transcript` is `ok` (after merging duplicates); anything else is an error. Notes quotes are rendered `notes: “…”`, never as speech.

| List | Fields | Quote | Drives |
| --- | --- | --- | --- |
| `pains[]` | `text` (short label) | required | Why they bought |
| `triggers[]` | `text` | required | Trigger event |
| `success_criteria[]` | `goal`, `metric`?, `target`?, `measured_by`? | optional | Success criteria; CRM-note and Slack drafts |
| `milestones[]` | `label`, `due` (date or null) | optional | Timeline table; passed / due within `milestone_soon_days` (14) |
| `promises[]` | `category`, `topic`, `terms`, `flags` {`non_standard`, `roadmap`}, `in_contract` (`yes`/`no`/`unknown`, default `unknown`) | required | What sales promised; conflicts; contract flags |
| `technical_requirements[]` | `category` (`integration`, `data`, `migration`, `security`, `sso`, `compliance`, `infrastructure`, `other`), `text` | optional | Technical requirements |
| `objections[]` | `text`, `status` (`open`, `partly_resolved`, `resolved`), `resolution`? | required | Unresolved objections; resolved ones as context |
| `risks[]` | `text` | optional | Risks |
| `competitors[]` | `name`, `status` (`incumbent`, `evaluated`, `replacing`, `chosen_over`, `mentioned`), `context`? | required | Competitors evaluated |
| `answered_questions[]` | `question`, `answer` | optional | Don't ask them again |
| `open_questions[]` | `question`, `owner` (`us`, `customer`, `unknown`) | n/a | Open questions; `source` optional here |

Promise `category` is one of `feature`, `integration`, `custom_work`,
`pricing`, `discount`, `sla`, `support`, `training`, `timeline`, `roadmap`,
`security`, `other`. Category `roadmap` sets `flags.roadmap`.

**Conflicts:** promises are grouped by `topic` (case, accents and punctuation
ignored). A group whose `terms` differ (same normalisation, and thousands separators ignored, so `$1,500` and `$1500` agree while `$15.00` differs) is a conflict;
every version is shown in time order with its source, and the latest is
named, never chosen. Reuse the exact topic wording across meetings and
emails, or the conflict is missed.

Items are ordered by their source's time (meetings by start; emails and CRM
items at the end of their date in the `--as-of` offset, so an email sent the evening after a call comes after it). Milestones are ordered by due date.

## `handoff.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `quiet_days` | 14 | Minimum days between a person's last meeting and the account's last meeting for "went quiet". |
| `quiet_meetings` | 2 | Minimum account meetings after a person's last one for "went quiet". |
| `single_appearance_min_meetings` | 3 | "Appeared once" only when the account has at least this many meetings. |
| `milestone_soon_days` | 14 | "Due soon" window. |
| `free_plan_window_days` | 30 | Granola free (Basic) plan note window. |
| `cycle_gap_days` | 21 | Gap when the deal was created this many days before the first meeting found. |
| `thin_meetings` | 2 | Fewer meetings than this is flagged as a thin record. |
| `key_roles` | `["champion", "economic_buyer"]` | Roles whose absence is flagged. |
| `role_labels`, `promise_category_labels`, `requirement_category_labels` | see file | Display text. Every code needs a label. |
| `bot_email_patterns` | see file | Note-taker addresses to drop from participants. A pattern that is a domain (such as `read.ai`) matches that domain and its subdomains only, so `ann@bread.ai.example` is kept; a word (such as `notetaker`) matches anywhere in the address. A pattern ending in `@` (such as `bot@`) must be the whole part before the `@`, so `ann.talbot@…` is kept. |
| `consumer_domains` | see file | Personal email providers; never an account domain. |
| `checklist` | 12 items | `{label, satisfied_by: [keys]}`. Keys: `deal`, `products`, `pains`, `triggers`, `success_criteria`, `milestones`, `promises`, `champion`, `economic_buyer`, `technical_owner`, `technical_requirements`, `objections`, `competitors`, `meetings`. |

## Handoff log (`handoff_log`)

A Markdown table. Only two runs write it, both under an exclusive lock file
(`<log>.lock`, created with O_CREAT|O_EXCL and holding this run's pid and a
random token; a lock older than 15 minutes, or dated more than a minute in the
future, is from a crashed run or a clock change and is removed, but only if it
is still the same file (inode) that was judged stale; a run removes only the
lock it created; a lock path that is a folder is exit 2; this is the only
clock read), and both
check that the log's folder is writable before printing anything. The whole
file is rewritten atomically (temp file, then rename).

1. **`--scheduled --log PATH`** (automation). A deal that is not closed-won,
   or whose key is `delivered`, is skipped (`Skipped: …`, exit 0). A key that
   is `pending` stops the run with exit 4 unless `--resume-pending` is given.
   If `sources.meeting_notes.status` is not `ok`, the run exits 3 and records
   nothing. Otherwise the row is written as `pending`, then the handoff is
   printed. `--dry-run` reads the log but never writes it.
2. **`--confirm-delivered KEY --log PATH --as-of NOW`**, after the doc was
   saved and the post sent: marks the row `delivered` with `delivered_at`.
   Confirming again is harmless; a key not in the log is an error.

Any other run (a person asking, a draft format) may read the log but never
writes it, so a draft written before close cannot block the real handoff.

```
# Sales-to-CS handoff log

| handoff_key | account | close_date | status | written_at | delivered_at | meetings_read | promises | conflicts |
|---|---|---|---|---|---|---|---|---|
| hubspot:deal/48213 | Quillfen Freight Co (fictional) | 2026-09-30 | delivered | <--as-of of the run> | <--as-of of the confirm> | 5 | 8 | 1 |
```

A log in the earlier 7-column format (no `status`, `delivered_at`) is read
with every row `delivered`, and is rewritten in the current format on the
next write. Rows may be indented. A file without one of those header rows,
a line that holds `|` but does not start with one, a row with the wrong
number of columns, an unknown status, or a cell that is not what the script
writes (a handoff key outside `A-Z a-z 0-9 : / _ . -`, a date, a datetime, a
`delivered_at` on a pending row or a count) is an error (exit 2) rather than
a risk of posting twice, and nothing unchecked from the log is ever printed.
The `account` cell is markdown-escaped, with `|` written as `/`.

## Exit codes and failure modes

`0` output, `Skipped: …` or `Confirmed: …`; `2` bad input; `3` scheduled run
with meeting notes not `ok`; `4` handoff pending confirmation; `5` log locked
by another run; `6` the output could not be written (closed pipe, full
disk; a scheduled handoff stays `pending`, so the next run exits 4 until it is
confirmed or resumed). Output is always UTF-8, whatever the locale. On any
other non-zero exit nothing is printed to stdout; the message goes to stderr.

Exit 2 messages name the file and the JSON path: missing or unreadable file;
malformed JSON (with line and column), `NaN`/`Infinity`, non-UTF-8 or too
deeply nested; missing required key; wrong type; value outside an enum; a
date or datetime that is not in the exact shape above, has no offset or is
not a real date; `search.to` before `search.from`; an
internal or personal domain as an account domain; a meeting with no
`matched_by`; a repeated meeting id with different details; a finding without
its required quote, or a quote without `quote_from`; a `transcript` quote
whose transcript is not `ok` or whose source is not a meeting; a finding
citing an unknown, internal-only, unclassified or excluded meeting; a
`record_ref` or `--confirm-delivered` key outside `A-Z a-z 0-9 : / _ . -`; an account domain that is not a plain host name; an unpaired surrogate escape (`\ud800`) anywhere; no `--internal-domain`, or one that is not a plain domain; a stakeholder whose email is on an internal domain; a meeting listed in both `meetings` and `excluded_meetings`; a `--scheduled` run whose closed-won deal has no `record_ref`; `--scheduled`
without `--log`; a bad `--doc-url`; a bad config (wrong types, a required list set to `null`, thresholds
above 3650); an unreadable or malformed log, or a log folder that is missing
or not writable when the run would write it.

Round 8 contact cues also include named possessives (straight or curly apostrophes), my/his/her/their, personal/direct/best/work/home, line-start Number, and is/'s/:/- separators. Spaced US country code `1` is recognized with a valid area/exchange shape. Short counts, business identifiers and the tested H7 price/year lists are retained. This remains pattern-based, not complete contact detection.
