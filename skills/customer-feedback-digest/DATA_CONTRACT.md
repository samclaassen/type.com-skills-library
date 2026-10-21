# Data contract: customer-feedback-digest

The agent reads the meeting recorder, CRM, billing and issue tracker through the Space's integrations and
normalises everything into **one JSON bundle** per digest. `build_digest.py` reads only that bundle, `digest.json`
and the optional state file. It never calls an API, and the clock is read only to judge whether a leftover state
lock is stale (`--as-of` is the only "now" in the digest).

Conventions:
- UTF-8 JSON. Unknown keys are ignored, so you can keep provenance such as `_note` or raw ids. `NaN`, `Infinity`,
  numbers too large for a float and integers longer than 16 digits are rejected (exit 2).
- **date** = `YYYY-MM-DD` (ASCII digits). **datetime** = `YYYY-MM-DDTHH:MM[:SS[.ffffff]]` followed by `Z` or a
  `±HH:MM` offset of at most ±14:00 with minutes below 60 (`2026-09-29T10:00:00-07:00`). Anything else, including a datetime without an offset, compact
  forms (`20260929T100000Z`) and ISO-week dates (`2026-W40-2`), is an error on every Python version, and so is a year outside 1900–2999.
- Strings: a `\u` escape that is not a character (a lone surrogate) is an error (exit 2); whitespace (including newlines) is collapsed; control, bidi-override and invisible characters (zero-width
  space, soft hyphen, and zero-width joiners next to ASCII text) are dropped. Displayed text is
  escaped by the script for markdown (backslash first; `$` and `=` so nothing becomes math or a highlight; `scheme://`, `www.` and every dot inside a word escaped) and Slack (entities plus look-alike
  characters for `*` `_` `~` `@` and the backtick), so never pre-escape it. Emails and phone numbers in displayed
  text are replaced with `[email removed]` / `[phone removed]`. That redaction is pattern-based and can miss
  formats: a phone needs 9–15 digits in phone-like groups (or a leading `+`), counting other scripts' digits and dash
  look-alikes (a full-width `＠` counts for emails); dates, times, version numbers, IP addresses, amounts, ranges of
  thousands, lists of decimals or years, other ascending or descending number lists (`0800 0900 1000`) and numbers after
  order/PO/invoice/ticket/ISBN/ZIP/`#` are left as they are, while a number in US phone shape (`212 555 1234`)
  or right after a phone word (call, phone, tel, mobile, fax…)
  counts as a phone even inside such a list.
- **url** fields must be absolute `http://` or `https://` links with a plain host (no `user@`). Unsafe characters
  are percent-encoded. Any other value (`javascript:`, `data:`, a bare path) is dropped: the text is shown without a
  link and Coverage counts it under "Links dropped".
- **key** = lower-case letters, digits, `.`, `_` or `-` (for example `outfitters.example`, `bulk-csv-import`).
- Optional means the key may be absent or `null`. It never means the agent should make up a value.
- Every item cites the meeting it came from, and the agent copies its evidence text verbatim. The script does not check quotes
  against meeting text (the bundle carries none), so it cannot catch a changed word. If you cannot cite and quote it,
  leave it out.
- Exit 2 messages name the bundle file and the JSON path, e.g. `bundle b.json: $.items[3].theme: 'x' is not defined in $.themes`.

## Top level

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `period` | object | yes | `start` (date), `end` (date, not after `--as-of`), optional `timezone_label`. At most 92 days. A Monday–Sunday period is keyed by its ISO week (`2026-W40`); any other range by `start..end` (`2026-09-05..2026-10-04`). `--scheduled` accepts only Monday–Sunday periods. |
| `sources` | object | yes | Status of each source; see below. |
| `accounts` | list | yes (may be `[]`) | Companies the meetings are with. |
| `meetings` | list | yes (may be `[]`; missing is an error) | **Every** meeting the recorder listed for the period, internal ones included. |
| `themes` | list | yes (may be `[]`) | Themes with tracker matches. Themes with no items are ignored. |
| `items` | list | yes (may be `[]`) | Extracted feedback items. |
| `date_gaps` | list | no | `{start: date, end: date, reason: string}` for known holes in coverage. |

## `sources`

Required keys: `meetings`, `transcripts`, `crm`, `billing`, `issue_tracker`. Each is
`{ "provider": string|null, "status": enum, "detail": string|null }`.

- `status`: `ok`, `partial` (some reads failed, e.g. some transcripts denied), `unavailable` (connected, read failed),
  `denied` (the provider refused access), `not_connected`, `not_configured` (e.g. `crm: none`), `not_requested`.
  Anything but `ok` appears on the digest's **Gaps** line with its `detail`. Set `ok` only after a real read call
  succeeded. `sources.meetings` is the coverage list: if it is not `ok`, the digest opens with a "Coverage
  incomplete" banner, never says no meetings were found, and a `--scheduled` run records nothing (exit 5).
- `provider` values with nice labels: `granola`, `gong`, `fathom`, `fireflies`, `otter`, `zoom`, `manual`,
  `hubspot`, `attio`, `salesforce`, `stripe`, `linear`, `jira`, `github`, `none`.
- `sources.meetings.history_days` (int, optional): how far back the recorder's plan shows notes (30 on free Granola
  plans). If the period starts earlier than `--as-of` minus this, Coverage reports a date gap.

## `accounts[]`

| Key | Type | Req | Notes | HubSpot | Attio | Salesforce | Stripe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `key` | key | yes | Stable id; use the company's primary email domain. | `domain` | `domains[0]` | `Website` host | |
| `name` | string | yes | Company name as the CRM gives it. Two accounts may not share a name ignoring case, full-width letters and invisible characters (two different companies: make the names distinct, e.g. add the city; one company: merge them), or a domain (a key that looks like a domain counts as one; domains in `free_email_domains` are ignored for this check): otherwise the script exits 2. Attendees from one business domain in scanned meetings filed under two accounts get a Coverage warning. | `name` | `name` | `Name` | |
| `domains` | list of strings | no | Email domains; used for the attribution check. | `domain` | `domains` | | |
| `status` | enum | no | `customer`, `prospect`, `churned`, `unknown` (default). Drives the "bug from a paying customer" draft rule. | `lifecyclestage` | your status attribute | `Type` | active subscription → `customer` |
| `segment` | string | no | e.g. `Mid-market`, `Agency`. | your segment property | | `Industry` / custom | |
| `plan` | string | no | e.g. `Growth`. Matched case-insensitively against `plan_weights`. | custom property | | custom field | price `nickname` / product name |
| `arr` | number | no | Annual recurring revenue, one currency, at most 10^15. Unknown = leave out (counts as 0 with `--weighting arr`, and the digest says how many were known). | custom property | | custom field | sum of active subscription items × 12 for monthly prices |
| `arr_source` | string | no | `crm`, `stripe` or `manual`; audit only. | | | | |

Stripe: `GET /v1/customers/search?query=email~"@<domain>"`, then `GET /v1/subscriptions?customer=<id>&status=active`.
Never fill ARR from memory or a public estimate.

## `meetings[]`

| Key | Type | Req | Notes | Granola |
| --- | --- | --- | --- | --- |
| `meeting_id` | string | yes | Unique within the bundle. | `id` from `list_meetings` |
| `title` | string | yes | Shown in citations for scanned meetings, and listed under Coverage for unclassified and attendees-unknown meetings. Internal-only titles are never shown. | `title` (unescape `&lt;` `&gt;` `&amp;`; the script re-escapes) |
| `start` | datetime | yes | With its offset. The calendar date in that offset decides whether it is in the period. | `date` → convert `Oct 2, 2026 7:30 AM PDT` to `2026-10-02T07:30:00-07:00` |
| `url` | url | yes | Link to the note; every citation uses it. | `url` (`https://notes.granola.ai/d/<id>`) |
| `source` | string | no | Defaults to `sources.meetings.provider`. | |
| `attendees[]` | list | yes | Every listed participant, internal and external, including the note creator. Use `[]` only when the source lists nobody. | `known_participants` |
| `attendees[].email` | string | yes | Lower-cased by the script. | `<email>` |
| `attendees[].name`, `.org` | string | no | Not shown in the digest. | `Name`, `from Org` |
| `kind` | enum | yes for external meetings | `customer` or `prospect` (scanned); `partner`, `investor`, `candidate`, `vendor`, `other` (counted, not scanned); `unknown` (default; listed as unclassified). Ignored for internal-only meetings. | from CRM lifecycle / deal, else the meeting itself |
| `account_key` | key | yes for `customer`/`prospect` | Must exist in `accounts`. Never guess. | |
| `transcript` | enum | no | `ok` (read), `denied` (workspace or plan refused), `unavailable` (read failed), `not_requested` (default). | `get_meeting_transcript` |
| `notes` | enum | no | `ok` (default) or `unavailable`. | `get_meetings` |

How the script classifies meetings (`--internal-domain`, `--internal-email`, and the config keys `ignored_attendee_domains` and `ignored_attendee_local_parts`):

1. Note-taker bots are dropped: an email whose domain equals, or is a subdomain of, an `ignored_attendee_domains`
   entry (`eu.read.ai` matches `read.ai`; `spread.ai` does not), or whose local part without a `+tag` equals an
   `ignored_attendee_local_parts` entry (`notetaker+x1@…`).
2. Internal = email on an internal domain or subdomain, or listed with `--internal-email`. External = everyone else.
3. Outside the period → ignored (counted). No external attendee and at most one person → **attendees unknown**
   (listed). No external attendee → **internal-only** (counted; title not shown). External + `customer`/`prospect`
   → **scanned**. External + other kind → **not a customer** (counted by kind). External + `unknown` →
   **unclassified** (listed so the user can classify and rerun).
4. **Duplicate notes**: scanned meetings with the same start minute (UTC) that share an external attendee (directly,
   or through another note of that minute) are one meeting. The canonical note is the one with a transcript, then the lowest `meeting_id`. Items from the other note
   count toward the canonical meeting. Both notes must name the same `account_key`.

## `themes[]`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `key` | key | yes | Stable across weeks; reuse keys printed by `--known-themes`. |
| `title` | string | yes | Short noun phrase. |
| `description` | string | no | One sentence in your words: what customers need. Used in drafts. |
| `tracker_search` | object | no | `{status: searched|not_searched|unavailable, query: string, detail: string}`. Default `not_searched`. |
| `issue_matches[]` | list | no | Candidates from a read-only search. |
| `issue_matches[].provider` | enum | yes | `linear`, `jira`, `github`, `other`. |
| `issue_matches[].id`, `.title`, `.url` | string, string, url | yes | e.g. `TAM-142`, issue title, link. |
| `issue_matches[].state` | string | no | Workflow state as shown in the tracker, e.g. `In Progress`. |
| `issue_matches[].confidence` | enum | yes | `high` (same capability/defect), `medium` (same area, partial overlap), `low` (keyword overlap). |
| `issue_matches[].reason` | string | yes | One line: why it matches. |

Tracker reads: Linear `list_issues` (`query`, `team`, `fields: ["id","title","url","status"]`); Jira
`GET /rest/api/3/search?jql=project=<KEY> AND text ~ "<terms>"`; GitHub
`GET /search/issues?q=repo:<owner/repo>+is:issue+<terms>`.

## `items[]`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `id` | string | no | For audit; defaults to `item-<n>`. |
| `meeting_id` | string | yes | Must exist in `meetings`. Items whose meeting is not scanned are dropped and counted in Coverage. |
| `type` | enum | yes | `feature_request`, `bug`, `usability`, `objection`, `churn_risk`, `competitor`, `praise` (keys of `feedback_types`). |
| `theme` | key | yes | Must exist in `themes`. |
| `summary` | string | no | Your one-line interpretation. Never shown inside quote marks. |
| `competitor` | string | yes for `competitor` | Product name as the customer said it. |
| `evidence.kind` | enum | yes | `transcript` (verbatim speech; the meeting's `transcript` must be `ok`) or `note` (verbatim excerpt from the AI notes). |
| `evidence.text` | string | yes | Verbatim, at most `max_quote_chars` (400). Whitespace is collapsed; one pair of quote marks wrapping the whole text is removed only when that mark appears nowhere else in it (so `"Fine," she said, "really"` is kept as is); emails and phone-like digit runs are replaced with `[email removed]` / `[phone removed]` everywhere the text appears (digest, issue drafts, JSON). Nothing else is changed. |
| `evidence.speaker_role` | string | yes | A role ("Head of Ops", "Customer participant"), at most `max_role_chars` (60). Emails, phone numbers and links are rejected. |
| `evidence.speaker_side` | enum | no | `customer` (default) or `unconfirmed` (e.g. a `System audio` line not tied to a named person). `internal` is rejected: internal speakers are not customer feedback. |

Dedupe: items with the same account, theme and (canonical) meeting become one row. Its mentions count the items from
the canonical note (or from the duplicate note if the canonical note had none); identical evidence text is shown once.

## `digest.json` (config)

| Key | Meaning |
| --- | --- |
| `feedback_types` | Type → label. |
| `type_priority` | Tie-break order; also orders single mentions. Must list each type once. |
| `draftable_types` | Theme types that can get an issue draft. |
| `top_min_accounts` | Distinct accounts needed to be a top theme (2). |
| `max_top_themes`, `max_quotes_per_theme`, `max_single_mentions`, `max_quieter_themes` | Display caps. |
| `draft_min_accounts` | Distinct accounts that make an unmatched theme high-signal (2). |
| `draft_if_customer_types` | Types that get a draft from a single paying customer (`bug`). |
| `max_quote_chars`, `max_role_chars` | Input limits (400, 60). |
| `currency_symbol` | For ARR. |
| `plan_weights`, `default_plan_weight` | For `--weighting plan`. |
| `ignored_attendee_domains` | Note-taker bot domains; an attendee on this domain or a subdomain of it is ignored (exact match, not substring). |
| `ignored_attendee_local_parts` | Bot mailbox names (`notetaker`, `no-reply`…); matched against the part before `@`, ignoring a `+tag`. |
| `free_email_domains` | Skipped in the attribution check. |

Every key is required and type-checked when the config is loaded (lists of strings, non-negative integers,
non-negative weights up to 10^6); a bad config is exit 2 before anything is printed.

## State file (`--state`)

JSON, written only by `--scheduled` runs (never with `--dry-run`, which cannot be combined with `--confirm-posted`)
and by `--confirm-posted`, always under an
exclusive lock file `<state>.lock` (created with O_CREAT|O_EXCL, holding the run's pid and a random token). A lock
whose modification time is more than 15 minutes old, or more than a minute in the future, is treated as left over by
a crashed run and replaced once, under a short `<state>.lock.takeover` guard and only if it is still the same file
(inode, mtime and contents); a run only removes a lock holding its own token. A run that finds the lock held exits
3; a folder at the lock path is exit 2. The state is checked for a writable
folder and written **before** the digest is printed; if it cannot be written the run exits 2 with nothing on
stdout.

```json
{"schema_version": 1, "skill": "customer-feedback-digest",
 "digests": [{"week": "2026-W40", "period_start": "2026-09-28", "period_end": "2026-10-04",
              "status": "pending|posted", "rendered_at": "...", "posted_at": "...", "post_ref": "...",
              "themes": {"bulk-csv-import": {"title": "Bulk CSV import for orders", "accounts": 4, "mentions": 5}}}]}
```

- Only Monday–Sunday weeks that have ended are recorded (period end before the `--as-of` date), keyed by ISO week;
  `period_start`/`period_end` must be that week.
- If stdout is closed or the pipe breaks after a week was recorded as pending, the run exits 1 with a message; the
  next scheduled run exits 4 and `--resume-pending` prints the digest again.
- A scheduled run prints a one-line skip for a week whose status is `posted` (exit 0). For a `pending` week it exits
  4 with nothing on stdout, unless `--resume-pending` is given; check the destination for an existing post first.
- A scheduled run whose `sources.meetings` is not `ok` records nothing and exits 5 with a one-line notice.
- `--confirm-posted <week>` is the only thing that marks a week `posted`.
- Trends compare with the latest recorded week that ended before the current period started; "returning" means seen
  in any earlier week. `growing` (with its +N) is used only when the current period is 7 days long; a longer period
  shows `recurring` instead, since its account counts are not comparable with one week's.
- Per week it stores the dates, status, timestamps, the post link, and theme keys, titles and counts: no quotes,
  names or account names.
- A missing file is an empty history; a link to a missing file is an error (exit 2), and so is a path the system
  cannot check (for example a name that is too long). A link to an existing file is followed: the file it points to
  is locked and updated, and the link is kept. A file that is not valid JSON, not this skill's state, or has a malformed entry
  (wrong types, a week that doesn't match its dates, duplicate weeks, bad timestamps, a `\u` escape that is not a
  character) is an error (exit 2).
- A scheduled run notes on stderr any earlier week still `pending`, so an unconfirmed post is not forgotten.
