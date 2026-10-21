# Data contract: pre-call-briefing

The agent reads each source through the Space's integrations and normalises
everything into **one JSON bundle**, one meeting per bundle. `build_briefing.py`
reads only that bundle and `briefing.json`. It never calls an API and never
reads the clock.

Conventions:
- UTF-8 JSON. Unknown keys are ignored, so you can keep provenance such as `_note` or raw ids.
- **date** = `YYYY-MM-DD`. When a datetime is given where a date is expected, the first 10 characters are used as written, with no timezone conversion.
- **datetime** = ISO-8601 with a UTC offset (`2026-10-06T10:00:00-07:00` or `...Z`). A datetime with no offset is an error.
- Money is a plain number in the deal's currency. The script does not convert currencies.
- Optional means the key may be absent or `null`. It never means the agent should make up a value.
- Every **signal** (budget, timeline, competitor, question, commitment) must carry the `source` it came from, and quotes must be verbatim. If you cannot quote it, leave it out.

## Top level

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `company` | object | yes | `name` (string, required; as the user or the CRM gives it). `domain` (string, optional, e.g. `outfitters.example`; lower-cased, a leading `www.` or `@` dropped). Attendees outside this domain are labelled "other org". Set `domain` whenever you know it: manual runs key on it. |
| `sources` | object | yes | Status of each source. See below. |
| `meeting` | object | yes | The calendar event. |
| `crm` | object or null | no | `null` or absent when `crm: none`. |
| `email` | object | no | `{ "threads": [...] }` |
| `slack` | object | no | `{ "mentions": [...] }` |
| `call_notes` | object | no | `{ "calls": [{ "date": date, "title": string|null }] }`: past calls with this company found in the call recorder (`sources.call_notes`). Counted on the Sources line and in the no-history check. |
| `signals` | object | no | Extracted budget, timeline, competitor, question and commitment facts. |
| `enrichment` | object | no | `{ "items": [...] }`. Used only with `--enrichment full`. |

## `sources`

Required keys: `calendar`, `crm`, `email`, `slack`, `enrichment`. Each is
`{ "provider": string|null, "status": enum, "detail": string|null }`.

Optional key: `call_notes`, for a call recorder or note taker such as Granola,
Gong, Fireflies, Otter or Fathom (provider values `granola`, `gong`,
`fireflies`, `otter`, `fathom`, `chorus`, `zoom`). Include it only when such a
source is configured; when present it is printed on the Sources line and, if
not `ok`, on the Gaps line.

`status` is one of `ok`, `unavailable` (connected, but the read failed),
`not_connected`, `not_configured` (for example `crm: none`), or `not_requested`
(enrichment while `enrichment: basic`). Any status other than `ok` is printed on
the briefing's **Gaps** line, together with `detail`. Set `ok` only after a real
read call succeeded. A tool that is listed but fails its read counts as `unavailable`.

Provider values the script labels nicely: `google`, `outlook`, `manual`,
`hubspot`, `attio`, `salesforce`, `gmail`, `slack`, `web`, plus the call-notes
providers above.

## `meeting`

| Key | Type | Req | Notes | Google Calendar | Outlook (Graph) |
| --- | --- | --- | --- | --- | --- |
| `event_id` | string | yes (calendar) | Instance id. For recurring meetings use the instance, not the series. For `manual` leave it out: the script derives it and ignores any value given (see below). | `events[].id` | `event.id` |
| `calendar_source` | enum | yes | `google`, `outlook` or `manual` | | |
| `title` | string | yes (calendar) | For `manual` leave it out: the script uses `Call with <company.name>`. | `summary` | `subject` |
| `start` | datetime | yes | Copy the offset form verbatim, because it is part of the dedupe key. | `start.dateTime` | `start.dateTime` + `start.timeZone` (convert to offset form) |
| `end` | datetime | no | | `end.dateTime` | `end.dateTime` |
| `timezone_label` | string | no | Shown after the time, e.g. `PDT`. If absent, the script shows `UTC-07:00`. | | |
| `status` | enum | no | `confirmed` (default), `tentative`, `cancelled`. Cancelled events are skipped. | `status` | `isCancelled` |
| `attendees[]` | list | yes | At least one for `google`/`outlook`. For `manual` it may be `[]`, but only when `external_domains` is set. | `attendees[]` | `attendees[]` |
| `attendees[].email` | string | yes | Lower-cased by the script. | `email` | `emailAddress.address` |
| `attendees[].name` | string | no | Falls back to the email. | `displayName` | `emailAddress.name` |
| `attendees[].response` | enum | no | `accepted`, `tentative`, `declined`, `needs_action` (default). | `responseStatus` (`needsAction` maps to `needs_action`) | `status.response` (`none` and `notResponded` map to `needs_action`) |
| `external_domains[]` | list of strings | no | The other company's email domain(s), e.g. `["outfitters.example"]`, no `@`. Use it in manual runs when the user gave the company domain but not attendee emails. Must not be one of your internal domains. | | |

Include the organiser and every attendee, both internal and external. The
script splits them using `--internal-domain`, where subdomains count as
internal.

External attendee information is required in one of two forms: at least one
attendee email outside `--internal-domain`, or a company domain in
`external_domains`. The script tells the two "no external attendee" cases apart:

- **Genuinely internal-only:** a calendar event (`google`/`outlook`) whose
  attendees are all internal, with no `external_domains`. It prints
  `Skipped: internal-only meeting …` and exits 0.
- **No external attendee information supplied:** `attendees` is empty, or a
  `manual` run lists only internal people, and `external_domains` is empty. It
  exits 2 with `no external attendee information supplied`. Ask the user for
  the external attendees' emails or their company domain and rerun. Never
  invent attendees, and never copy CRM contacts into `attendees` as a guess.

With only `external_domains`, the briefing says the attendees were not named,
lists that on the Gaps line, and cannot run the new-stakeholder or
first-meeting-signer checks.

Manual runs (`calendar_source: manual`) use the start time the user gives, and
the script names them itself so two runs about the same call cannot differ:

- `meeting.title` defaults to `Call with <company.name>`.
- `meeting.event_id` is derived, and any value in the bundle is ignored:
  `<basis>-<YYYY-MM-DD>`, where the date is the first 10 characters of
  `meeting.start` as written (the call's local date) and `<basis>` is, in order:
  1. `company.domain` if set (most stable), e.g. `outfitters.example`;
  2. otherwise the first of `meeting.external_domains`, sorted;
  3. otherwise `<company-slug>`: `company.name` with accents folded to ASCII,
     lowercased, every run of characters other than `a-z0-9` treated as a
     separator, trailing legal suffixes dropped (`inc`, `incorporated`, `llc`,
     `ltd`, `limited`, `gmbh`, `co`, `corp`, `corporation`, `plc`, `llp`, `lp`,
     `ag`, `sa`, `sas`, `bv`, `nv`, `pty`, `oy`, `ab`, `srl`, `spa`; at least one
     word is always kept), and the words joined with single `-`.
     "Northpeak Outfitters" and "Northpeak Outfitters, Inc." both give
     `northpeak-outfitters`.
- The event key is `manual:<event_id>@<start>`, e.g.
  `manual:outfitters.example-2026-10-06@2026-10-06T10:00:00-07:00`. The prefix is
  added once.

A run that knows the domain and one that only knows the name produce different
keys, so always fill `company.domain` when it is known.

Manual briefings print `Not on your calendar — time supplied by you.` directly
under the heading.

## `crm`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `record_found` | bool | yes | `false` when nothing matched by company domain or attendee email. |
| `record_ref` | string | no | e.g. `hubspot:deal/123`. Never shown in the briefing. |
| `notes_count` | int | no | How many recent notes were read for signal extraction. |
| `deal` | object/null | no | Null when there is no record. If every field is null, the briefing says "empty". |
| `deal.name`, `deal.stage`, `deal.owner` | string | no | `stage` is the human label, not the internal id. |
| `deal.amount` | number | no | |
| `deal.amount_period` | enum | no | `yr` (default), `mo` or `one_time`. Budgets are compared after annualising. `one_time` is never compared with recurring amounts. |
| `deal.close_date` | date | no | |
| `deal.last_activity_at` | date/datetime | no | Last logged call, email, meeting, note or task. Null means "no activity logged". |
| `contacts[]` | list | no | Matched to attendees by email. |
| `contacts[].email` | string | yes | |
| `contacts[].name`, `.title` | string | no | |
| `contacts[].role` | enum | no | `signer`, `decision_maker`, `influencer`, `champion`, `user`, `technical`, `unknown` (default). Use `unknown` unless the CRM or a note says otherwise. |
| `contacts[].prior_meetings` | int | yes | Past meetings or calls with this person, excluding this one. |
| `contacts[].prior_emails` | int | yes | Email threads with this person. |
| `contacts[].last_touchpoint` | object/null | no | `{date, summary}`. The summary is a short factual phrase from the record. |
| `open_tasks[]` | list | no | `{id, subject, owner, due (date), done (bool)}`. Each task counts as our commitment. |

Source fields per CRM:

- **HubSpot**: search deals with `POST /crm/v3/objects/deals/search`, filtering on the associated company domain. If that fails, find contacts by attendee email (`POST /crm/v3/objects/contacts/search`, `email IN [...]`) and follow their deal associations. Properties to read are `dealname`, `dealstage` (map it to its label through `GET /crm/v3/pipelines/deals`), `amount`, `closedate`, `hubspot_owner_id`, `notes_last_updated` and `hs_lastmodifieddate`. Use `notes_last_updated` for last activity. Contacts: `firstname`, `lastname`, `jobtitle`, `email`, `num_contacted_notes`, `hs_last_sales_activity_timestamp`. Notes come from `GET /crm/v3/objects/notes` with an association to the deal (`hs_note_body`, `hs_timestamp`). Tasks: `hs_task_subject`, `hs_task_status` (`COMPLETED` means done), `hs_timestamp` (due).
- **Attio**: `POST /v2/objects/deals/records/query` (filter on the linked company, or the company first via `POST /v2/objects/companies/records/query` on `domains`). Use the `stage`, `value`, `close_date` (or your workspace's equivalent) and `owner` attributes. People: `POST /v2/objects/people/records/query` by `email_addresses`, reading `job_title`. Notes: `GET /v2/notes?parent_object=deals&parent_record_id=...`. Tasks: `GET /v2/tasks?linked_object=deals&linked_record_id=...` (`content_plaintext`, `deadline_at`, `is_completed`). Interaction counts come from the record's interaction attributes where your workspace has them. Otherwise derive them from calendar and email.
- **Salesforce**: SOQL `SELECT Id, Name, StageName, Amount, CloseDate, LastActivityDate, Owner.Name FROM Opportunity WHERE AccountId = :acct AND IsClosed = false`. For roles, `SELECT Contact.Email, Contact.Name, Contact.Title, Role, IsPrimary FROM OpportunityContactRole WHERE OpportunityId = :opp`. Map `Role` values: `Economic Buyer` → `signer`, `Decision Maker` → `decision_maker`, `Influencer`/`Evaluator` → `influencer`, `Technical Buyer` → `technical`, `Business User` → `user`. For open tasks, `SELECT Id, Subject, ActivityDate, Status, IsClosed, Owner.Name FROM Task WHERE WhatId = :opp AND IsClosed = false`. For history, `SELECT ... FROM Task WHERE WhoId = :contact AND IsClosed = true` and `Event` for prior meetings.
- **crm: none**: set `crm` to `null` and `sources.crm.status` to `not_configured`. The briefing then says "CRM not configured" and the log records `not configured`. Keep `unavailable` for a configured CRM whose read failed; that is reported as "CRM unreachable". Calendar and email history can still fill `prior_meetings` and `prior_emails` as contacts if you list them under `crm.contacts` with `record_found: false`. Otherwise attendees show as "not in CRM".

## `email.threads[]` (last 5 threads with the company domain)

| Key | Type | Req | Gmail | Outlook (Graph) |
| --- | --- | --- | --- | --- |
| `thread_id` | string | yes | `threads[].id` | `conversationId` |
| `subject` | string | no | `Subject` header | `subject` |
| `last_message_at` | datetime | yes | last message `internalDate` (epoch milliseconds, convert it) | `receivedDateTime` |
| `last_message_from` | string | yes | `From` header address | `from.emailAddress.address` |

Gmail: `GET /gmail/v1/users/me/threads?q=from:outfitters.example OR to:outfitters.example newer_than:30d&maxResults=5`,
then `threads.get?format=metadata&metadataHeaders=From&metadataHeaders=Subject`.
Read full bodies only to extract signals. Graph: `GET /me/messages?$search="outfitters.example"&$top=25`, then group by `conversationId` and keep 5.
The script keeps the 5 most recent threads (`max_email_threads`). If you supply
more, the Sources line reports "5 threads of N".

## `slack.mentions[]` (company name or domain, last 7 days)

`{channel, author, posted_at (datetime), text}`. Slack `search.messages` with
`query="Example Outfitters" after:<as-of minus 8 days>`, using `channel.name`, `username`
and `ts` (epoch seconds, convert it). The script drops anything outside
`slack_lookback_days` before `--as-of`, keeps the newest 3 (`max_slack_mentions`) and quotes `text` verbatim.

## `signals` (extracted by the agent from notes, emails and Slack)

Every item has `source: {type, date, ref}`. `type` is one of `email`,
`call_note`, `call_transcript`, `crm_note`, `meeting`, `slack` or `crm_field`.
Use `call_transcript` for a quote taken from a recorder or note taker's
transcript, and `call_note` for its written summary. `ref` is free text,
never shown in the briefing, and used for audit.

| List | Fields | Drives |
| --- | --- | --- |
| `budget_statements[]` | `amount` (number), `period` (`yr`/`mo`/`one_time`), `qualifier` (`exact`, `approx`, `ceiling`, `approx_ceiling`), `quote` (verbatim), `speaker`, `source` | `budget_conflict` when annualised CRM amount > annualised budget × (1 + `budget_tolerance_pct`/100). Only the most recent statement is used. |
| `timeline_statements[]` | `label` (short noun phrase, e.g. "Black Friday prep"), `quote` (verbatim), `stated_date` (date or null), `speaker`, `source` | Most recent only. `stated_date` null leads to `close_date_unclear`. A date different from the CRM close date leads to `close_date_conflict`. |
| `competitor_mentions[]` | `competitor`, `context` (e.g. "Trialed in spring"), `quote` (**required**, verbatim), `speaker`, `source` | Listed under Risks & landmines. A mention with no quote is rejected (exit 2). |
| `questions[]` | `asked_by` (name), `topic` (noun phrase such as "SSO support"), `asked_at` (date), `channel` (`email`/`call`/`slack`/`meeting`), `answered` (bool) | Questions from the prospect only. `answered: false` produces `unanswered_question`. Mark a question answered only if a later message from us addresses it. |
| `commitments[]` | `side` (`ours`/`theirs`), `owner`, `what`, `due` (date or null), `done` (bool), `crm_task_id` (optional), `source` | `overdue_commitment` when `side: ours`, `due` < as-of date and not done. If the commitment is also a CRM task, set `crm_task_id` so the task is not listed twice. |

## Call notes and transcripts (optional: Granola, Gong, Fireflies, Otter, Fathom)

If a call recorder or note taker is connected, read the notes or transcripts of
past calls with this company (matched by attendee email, company name or
domain, last 90 days). Use them only to extract `signals`: budget and timeline
statements, competitor mentions, questions the prospect asked and commitments
made on the call, each with a verbatim quote and `source.type` of
`call_transcript` or `call_note`. A past call found there may also raise a
contact's `prior_meetings` when the CRM did not log it. List every past call you
found in `call_notes.calls` (`date`, `title`); leave the list empty only when the
recorder was read and had nothing. Record the source in
`sources.call_notes`. Granola: list notes and fetch each note's summary and
transcript through its integration. Other recorders expose the same idea as
"calls", "meetings" or "transcripts". Field names vary, so check what the
connected tool returns and quote it exactly.

**No history found.** When there is no CRM record (or `crm: none`) and the bundle
holds no email threads, no Slack mentions in the window, no past calls or
call-sourced signals, no contact with prior meetings or emails, and no signals,
the script raises `no_history_found`. The TL;DR and the empty-state lines name
only sources whose status is `ok`; a source that could not be read is named as
not read, so the briefing never claims "no history anywhere" over a gap.

## `enrichment.items[]` (only with `enrichment: full`)

`{kind, summary, source_url, date}`. `kind` is one of `news`, `funding`,
`hiring`, `tech_stack`, `leadership` or `other`. Both `source_url` and `date` are
required. An item you cannot cite does not go in the bundle. If every
enrichment source failed, set `sources.enrichment.status` to `unavailable`. The
briefing then says that company context is not available.

## `briefing.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `stale_days` | 14 | `stale_record` when days since last activity is at least this. |
| `outdated_days` | 60 | Above this, the briefing says plainly that the record looks outdated. |
| `slack_lookback_days` | 7 | Slack window before `--as-of`. |
| `max_email_threads` | 5 | Threads counted. |
| `max_slack_mentions` | 3 | Mentions quoted. |
| `max_agenda_items` | 5 | Agenda length cap. |
| `budget_tolerance_pct` | 0 | Slack allowed before an amount counts as over budget. |
| `signer_roles` | `["signer","decision_maker"]` | Roles that trigger `signer_first_meeting`, which fires when `prior_meetings` is 0. |
| `role_labels` | see file | Display text for each role. |
| `flag_priority` | see file | Order of TL;DR goals and agenda items. Must list all 13 flag codes. `no_history_found` comes first by default. |

## Briefing log (`briefing_log`)

The log is a Markdown file. The script reads it when you pass `--log PATH` and
appends a row unless `--dry-run` is set or the event key is already there (one
row per event). With `--scheduled`, an event already in the log is skipped
(`Skipped: already briefed …`). Without `--scheduled` (a person asked) the
briefing is printed again; the log is not given a second row.

```
# Pre-call briefing log

| event_key | company | meeting_start | briefed_at | crm_status |
|---|---|---|---|---|
| google:<event id>@<start as written> | Company | <start> | <--as-of> | record found / missing / empty / unavailable / not configured |
```

`event_key` = `<calendar_source>:<event_id>@<meeting.start>` (manual ids are derived; see `meeting`). A rescheduled meeting
gets a new key and is briefed again. If the log file exists but has no header row,
or a row has the wrong column count, the script exits 2 rather than risk
briefing an event twice.

## Failure modes (exit 2)

Each message names the file and the JSON path. The script exits 2 for a missing
file, malformed JSON (with line and column), a missing required key, a wrong type,
a value outside an enum, a date or datetime without an offset where one is
required, `end` before `start`, no external attendee information (no external attendee
email and no `external_domains`, outside a genuinely internal calendar event),
an internal domain listed in `external_domains`, a competitor mention without a quote, an
enrichment item without a source, no `--internal-domain`, an invalid
`flag_priority`, or an unreadable log. Nothing is silently dropped or defaulted
beyond the defaults listed above.
