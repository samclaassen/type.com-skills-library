# Data contract: commitment-tracker

The agent reads meetings and completion evidence through the Space's
integrations and normalises everything into **one JSON bundle per run**.
`build_commitments.py` reads only that bundle, `commitments.json` and the
state file. It never calls an API and never reads the clock (`--as-of` is
"now"), except to tell whether a leftover lock file is stale.

Conventions:
- UTF-8 JSON. Unknown keys are ignored, so you can keep provenance such as `_note`. `NaN`, `Infinity`, lone surrogate escapes (`\ud800`) and absurdly nested JSON are rejected. Leading and trailing whitespace is trimmed from every string.
- **date** = exactly `YYYY-MM-DD`. A full **datetime** (below) is also accepted where a date is expected, and its own local date is used. Compact (`20261009`), ISO-week (`2026-W41-4`) and other forms are rejected with exit 2, identically on Python 3.8 to 3.14. Years must be 1900 to 2999.
- **datetime** = `YYYY-MM-DDTHH:MM[:SS[.fff or .ffffff]]` followed by `Z` or `±HH:MM` (`2026-10-06T10:00:00-07:00`), ASCII digits only, hours `00`–`23`, minutes and seconds `00`–`59`, offset from `-14:00` to `+14:00`. `T24:00`, `+00:60` and the like are rejected on every Python version. A datetime without an offset is an error.
- **url** = an absolute `http://` or `https://` URL whose host is a DNS name: dot-separated labels of letters, digits and hyphens (no empty label, no leading or trailing hyphen) ending in an alphabetic or `xn--` top-level domain, with an optional port from 1 to 65535. No `user@`, IP literals or single-label hosts such as `localhost`. Spaces, parentheses, `<`, `>`, `"`, `|` and non-ASCII characters in the path are percent-encoded. Any other URL (`javascript:`, `data:`, relative paths) is dropped: the text is kept without a link and Coverage lists it under "Links dropped".
- Every text value is escaped when rendered, so its characters can't form a link, an autolink, raw HTML, a Slack ping, mention or `<url|label>` link, or open or close the digest's own bold, italic or code. Markdown: `&`, `<`, `>` become entities, then `\`, `` ` ``, `*`, `[`, `]`, `|`, `~`, `$`, `=` and word-edge `_` get a backslash (the backslash itself is escaped in the same pass, so `\[x\](url)` stays text; `$` and `=` so `$…$` can't become math and `==…==` a highlight), then `://` and every dot inside a word get one too (rendered unchanged), so a bare URL, `www.` name or domain can't be auto-linked. Slack: `&`, `<`, `>` become entities and, because Slack has no escape character, `*`, `_`, `~` and `` ` `` are shown as the look-alikes `∗`, `＿`, `∼` and `ˋ`, `@` before a name as `＠`, and `://` and a dot inside a word before a letter (as in a domain or `www.` name) as `:∕∕` and `․` (so a quote with those characters is not byte-for-byte verbatim in Slack).
- Optional means the key may be absent or `null`, except list fields (`commitments`, `evidence`, `user_actions`, `attendees`; and `meetings`, which should always be given): those may be absent or `[]`, and `null` or any non-list is an error. It never means the agent should make up a value.
- Every commitment carries a `quote`, copied verbatim by the agent, and the `meeting_id` it came from. The bundle has no meeting text, so the script doesn't check the wording: it requires a quote and checks that its `quote_source` was read. Every piece of evidence carries a `url`. No quote, no commitment; no link, no evidence.
- Errors exit 2 with `bundle <file>: <json.path>: <problem>`.

## Top level

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `scan` | object | yes | The window and cadence. See below. |
| `sources` | object | yes | Status of each source. See below. |
| `meetings` | list | yes (may be empty) | Every meeting in the window that was listed, read or not. |
| `commitments` | list | no | Extracted commitments and soft mentions. |
| `evidence` | list | no | Completion evidence for this run's commitments or for items in the state file. |
| `user_actions` | list | no | Dismiss / restore / mark-done instructions from the user. |

## `scan`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `from` | date | yes | First local date of the window, inclusive. |
| `to` | date | yes | Last local date, inclusive. `from` must not be after `to` or after `--as-of`. With `--scheduled`, `to` must not be after the `--as-of` date either (take both from `--next-window`). |
| `cadence` | enum | yes | `daily`, `weekly` or `on_request`. `on_request` bundles are refused with `--scheduled`. |

The period key is `<cadence>:<from>..<to>`, e.g. `weekly:2026-10-05..2026-10-09`.
For scheduled runs, take `from` and `to` from `--next-window` (from where the
last posted window ended through today; see State file). A scheduled run prints `Skipped: already
posted …` and changes nothing when its period is already posted **or** when a
digest of the same cadence was already posted in the same slot: the same
Monday–Sunday week for `weekly`, the same day for `daily`, judged by the day
each window ends (a digest resumed and posted days later still belongs to the
week it covered). So
the schedule firing again at 4:30pm after a confirmed 4pm digest posts nothing,
even though `--next-window` then returns a one-day window. A rerun of an
already posted period always prints `Skipped`; otherwise a run stops with exit 4
while its own period, or another digest for the same or a later slot, is still
pending (see State file). A skipped run applies none of the bundle's
`user_actions` and says how many it didn't apply.

## `sources`

Required keys: `meetings`, `email`, `slack`, `tracker`, `calendar`. Each is
`{ "provider": string|null, "status": enum, "detail": string|null }`.

| Key | Provider values | Used for |
| --- | --- | --- |
| `meetings` | `granola`, `gong`, `fathom`, `fireflies`, `otter`, `zoom`, `pasted` | The meetings themselves, and `later_meeting` evidence. `provider` is always required here. |
| `email` | `gmail`, `outlook` | `sent_email` / `received_email` evidence |
| `slack` | `slack` | `slack_message` evidence |
| `tracker` | `linear`, `jira`, `asana` | `tracker_issue` evidence |
| `calendar` | `google` (or `google_calendar`), `outlook_calendar` | `calendar_event` evidence |

`status` is one of `ok`, `unavailable` (connected, but the read failed),
`denied` (permission refused), `not_connected`, or `not_configured` (the user
didn't choose this source). Set `ok` only after a real read call succeeded.
`provider` is required when status is `ok`. Every non-`ok` source is printed on
the digest's **Gaps** line with its `detail`. Evidence from a source that is
not `ok` is rejected.

`sources.meetings` also takes:

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `plan` | string | no | Granola `get_account_info.mcp_plan` (e.g. `free`, `plus`, `business`). Looked up in `plan_history_days` in `commitments.json`. |
| `history_limit_days` | int | no | Overrides the plan lookup when you know the limit. When `scan.from` is older than `--as-of` minus the limit, the digest is **Incomplete** (see Output): Coverage warns that meetings may be missing, a scheduled run exits 5 and is not recorded. Start the window at `--as-of` minus the limit instead; the days before it then show as a Gap. |

## `meetings[]`

| Key | Type | Req | Granola source | Notes |
| --- | --- | --- | --- | --- |
| `meeting_id` | string | yes | `list_meetings` `id` (UUID, or `not_` plus 14 characters) | Unique in the bundle. Part of every commitment id. Read details with `get_meetings`, at most 10 ids per call. |
| `title` | string | yes | `title` | |
| `start` | datetime | yes | `date`, e.g. `Oct 6, 2026 10:00 AM PDT` → `2026-10-06T10:00:00-07:00` | Convert the timezone abbreviation to its offset. Its local date (in its own offset) must fall inside `scan` (or, with `reread: true`, on or before `scan.to`), and it anchors relative due dates. |
| `url` | url | no | `url` (`https://notes.granola.ai/d/<id>`) | Shown as the meeting link. Leave out for pasted transcripts with no link. |
| `account` | string | no | | The outside organisation's name, used as the default counterpart. |
| `attendees[]` | list | no | `known_participants` | `{ "email": string (required), "name": string? }`. Internal vs external is computed from email domains against `--internal-domain` (subdomains count as internal). |
| `audience` | enum | no | | `external` or `internal`. Overrides the computed value when the participant list is incomplete or wrong. Requires `audience_reason` (string). Overrides are counted in Coverage. |
| `notes_status` | enum | yes | `get_meetings` | `ok`, `denied`, `unavailable`. If no meeting in the bundle had its notes or its transcript read, the digest is **Incomplete** (exit 5 on a schedule). A meeting read neither way whose notes or transcript status is `unavailable` (a failed read, which can be retried) also makes it Incomplete, so the period isn't recorded and the next window reads that meeting again, but only within the retry budget (`unreadable_retry_runs`, `unreadable_retry_days`; see State file `unread_meetings`): after that it is given up, listed in Coverage as not covered, and no longer makes runs Incomplete. One that is only `denied` is listed in Coverage. If a `get_meetings` batch fails, list its meetings as `unavailable`; never leave them out (a meeting that failed before and is missing from a later bundle whose window holds its date counts as failing again). |
| `reread` | boolean | no | | `true` for a meeting `--next-window` listed under `reread` (given up earlier) that you read again; its date may be before `scan.from`. Default `false`. |
| `transcript_status` | enum | yes | `get_meeting_transcript` | `ok`, `denied`, `unavailable`, `not_requested`. Denied and unavailable transcripts are named in Coverage. |

A meeting with no attendees and no `audience` is "unknown" and treated as
external. With `--scope external` (the default), commitments from internal
meetings are counted and skipped. Two notes with the same title and start
instant are reported as duplicate notes of one meeting.

## `commitments[]`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `ref` | string | yes | Unique within this bundle. Used by `evidence[].ref` and `same_as`. |
| `meeting_id` | string | yes | Must be in `meetings`. |
| `kind` | enum | no | `commitment` (default) or `soft_mention`. Soft mentions need only `ref`, `meeting_id`, `quote`, `quote_source` (and optional `owner`); they are never tracked or stored, and are listed only with `--soft-mentions`. |
| `direction` | enum | yes | `we_owe` (owner is you or your team) or `they_owe` (owner is outside). |
| `owner` | string | yes | Who made the promise (the speaker, or the person who accepted an assignment). |
| `owner_email` | string | no | When given, its domain must match `direction`: internal for `we_owe`, external for `they_owe`. |
| `counterpart` | string | no | Account or person the promise was made to (`we_owe`) or the owner's organisation (`they_owe`). Defaults to the meeting's `account`, then its external domains, then "internal". |
| `counterpart_email` | string | no | Recipient for a follow-up draft (`we_owe`). Business addresses only. Not shown in the digest. |
| `deliverable` | string | yes | Short imperative: "Send the revised SOW". Part of the commitment id (two deliverables from one sentence are two items) and used for merging, so keep the wording consistent across runs. A re-extraction from the same meeting with the same quote and owner joins the tracked item even if the deliverable is worded differently, unless that sentence holds more than one tracked or extracted commitment. |
| `quote` | string | yes | Verbatim from the source. Light trimming at the ends only. |
| `quote_source` | enum | yes | `transcript` (requires `transcript_status: ok`), `notes` (the user's own notes) or `summary` (Granola's AI summary). Both `notes` and `summary` require `notes_status: ok`. Summary quotes are labelled "Granola AI summary, not verbatim speech". |
| `due` | object | no | Defaults to `{ "kind": "none" }`. See below. |
| `same_as` | string | no | Another ref in this bundle that is the same promise (a paraphrased repeat). Must have the same direction and owner. |
| `known_id` | string | no | An id from the state file (via `--list-open`) that this is a re-extraction of. Must exist in the state file, and must be open unless this mention is from a meeting the item already cites or from a day the item was still open (before it was dismissed or marked done by you, or on or before the date of the evidence that closed it): a new promise made after that is a new item. |

### `due`

| `kind` | Other keys | Meaning |
| --- | --- | --- |
| `explicit` | `date` (required, not before the meeting), `phrase` (optional) | A date was stated. Used as given. |
| `relative` | `phrase` (required); `date` not allowed | Timing relative to the meeting. The script resolves it and labels it "inferred from …". A phrase that would land more than 3650 days after the meeting (or after 2999) gives no date and is shown as "no firm date (“…” is more than 3650 days out — confirm)". |
| `none` | `phrase` (optional) | No timing, or vague timing ("soon", "ASAP", "before the renewal"). Shown as "no firm date (…)". |

Relative phrases the script resolves (case-insensitive). Leading words such
as *by, before, until, on, due, no later than* and trailing time-of-day words
such as *EOD, COB, morning, at 3pm* are ignored. Here *d* is the meeting's
local date, and business days are `business_days` minus `holidays` in
`commitments.json`:

| Phrase | Resolves to |
| --- | --- |
| today, tonight, end of day, EOD, COB | d |
| tomorrow | next business day after d |
| Friday, this Friday (any weekday) | next such weekday after d. **Said on that same weekday → no date, "date unclear — confirm".** |
| next Friday | that weekday in the week (Mon–Sun) after d's week |
| end of week, EOW, this week, later this week | last business day of d's week (next week's if d is after it) |
| next week, end of next week | last business day of next week |
| early next week, start of next week | second business day of next week |
| in/within N days, in a week, in N weeks | d + N calendar days / 7·N days |
| in N business (working) days | N business days after d |
| end of month, EOM, this month | last business day of d's month (next month's if d is after it, e.g. Saturday the 31st) |
| next month, end of next month | last business day of next month |
| end of quarter, EOQ, this quarter | last business day of d's calendar quarter (next quarter's if d is after it) |
| Oct 9, October 9th, 9 October, the 9th of October | that date, this year, or next year if it's already passed (kind `explicit`, shown as "year inferred from …") |
| 2026-10-20 | that date (kind `explicit`) |
| anything else (soon, ASAP, 10/9, a couple of days) | no date |

## `evidence[]`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `ref` / `commitment_id` | string | exactly one | `ref` for a commitment in this bundle; `commitment_id` (`ct-…`) for an item from the state file. |
| `kind` | enum | yes | `sent_email`, `received_email` (source `email`), `slack_message` (`slack`), `tracker_issue` (`tracker`), `calendar_event` (`calendar`), `later_meeting` (`meetings`). That source's status must be `ok`. |
| `strength` | enum | yes | `strong` (delivered: done), `weak` (suggests delivery: likely done), `against` (still outstanding: cancels older evidence). |
| `date` | date | yes | When it happened. Not before the commitment's meeting date, not after `--as-of`. |
| `url` | url | yes | Link to the email, message, issue, event or note. A non-http(s) link is dropped (counted under "Links dropped"); the evidence still counts. |
| `summary` | string | yes | One line written by the agent, e.g. `Sent “Pricing: two new locations” to Priya Shah`. No pasted bodies. |
| `why` | string | for `weak` | Why it's only likely, e.g. "email sent, but no quote attachment confirmed". |

Status rule: only evidence dated on or after the item's latest promise counts
(a promise repeated after an email means that email didn't deliver it; the
digest shows such evidence as "Not counted"). The item is **done** if it has
strong evidence and no `against` evidence dated after the latest strong item.
It is **likely done** if it has weak evidence and no `against` dated after the
latest weak item. Otherwise it
is open: overdue, due soon, later, or no date. On the same date, `against`
wins over found evidence; between your own replies (`mark_done`, `reopen`) the
later one wins, by run and by position in `user_actions`. An item that is no
longer done is open again everywhere: it loses its "reported done" mark, is
listed by `--list-open`, is reported again when it is next done, and is never
pruned as a done item.

## `user_actions[]`

| Key | Type | Req | Notes |
| --- | --- | --- | --- |
| `commitment_id` | string | yes | Must be tracked in the state file (or created in this run). |
| `action` | enum | yes | `dismiss` (hide from now on), `restore` (undo a dismissal), `mark_done` (user says it's done; shown once under Done), `reopen` (user says a done item isn't done: it becomes open again and is reported again when next done), `set_due` (user gives or corrects the due date). |
| `date` | date | yes | When the user said it. Not after `--as-of`. |
| `due_date` | date | for `set_due` | The date the user gave. Not before the commitment's meeting. Overrides inferred and re-promised dates; shown as "(set by you …)". |
| `note` | string | no | The user's reason. Shown for `mark_done` and `reopen`. |

Actions are saved in the state file, so they don't need to be repeated in
later bundles. `reopen` and `restore` for tracked items are also applied before
the bundle's commitments are merged, so a promise repeated in the same bundle
joins the reopened item rather than starting a second one. After merging, every
action is applied in order, so "dismiss, then restore" ends restored and
"restore, then dismiss" ends dismissed.

## State file (`--state`, default name `commitment-state.json`)

Written atomically by the script, never by hand, and always **before** the
digest is printed. `--dry-run` never writes it. Runs that write it hold an
exclusive lock file, `<state>.lock` (created with O_CREAT|O_EXCL, removed at
the end of the run, and only if it still holds this run's pid and random
token). A second run finding a lock exits 3 and prints nothing. A lock older
than 15 minutes, or dated more than 60 seconds in the future, is stale: it is
removed only if it is still the same file (device, inode and mtime) that was
found stale, then the run retries once. A lock path that is a folder or
another non-file stops the run with exit 2.

```json
{
  "state_version": 2,
  "updated_at": "2026-10-09T16:00:00-07:00",
  "commitments": {
    "ct-97efb9ee06": {
      "id": "ct-97efb9ee06", "direction": "we_owe", "owner": "Dana Ortiz", "owner_email": "dana@brackenvole.example",
      "counterpart": "Fennaroot Dental", "counterpart_email": "priya@fennaroot.example",
      "deliverable": "Send the revised SOW", "dedupe_key": "we_owe|dana ortiz|fennaroot dental|send revised sow",
      "origin": {"meeting_id": "sample-1a2b", "title": "…", "start": "…", "day": "2026-10-05", "url": "…"},
      "mentions": [{"meeting_id": "…", "quote": "…", "quote_source": "transcript", "due": {"date": "2026-10-07", "kind": "inferred", "phrase": "by Wednesday", "note": null}, "…": "…"}],
      "due": {"date": "2026-10-12", "kind": "inferred", "phrase": "by Monday", "was": "2026-10-07", "…": "…"},
      "evidence": [], "dismissed": null, "due_override": null, "earlier": null, "restored_on": null,
      "first_reported": "2026-10-09T16:00:00-07:00", "done_reported": null, "last_status": "open"
    }
  },
  "runs": [{"period_key": "weekly:2026-10-05..2026-10-09", "as_of": "…", "mode": "scheduled", "status": "posted",
            "staged": {"first_reported": ["ct-…"], "done_reported": ["ct-…"]}, "confirmed_at": "…", "counts": {"…": 0}}],
  "unread_meetings": {
    "sample-7a8b": {"title": "…", "day": "2026-10-08", "url": "…", "attempts": 2, "first_failed": "…", "last_failed": "…",
                    "last_slot": "weekly:2026-10-12", "given_up": "2026-10-16T16:00:00-07:00", "reread_until": "2027-02-05"}
  }
}
```

- **Validation.** Every field is checked on load: types, enums, ids (`ct-` plus 10 hex characters, equal to their key), dates and datetimes (strict, as above), `was` and `repromised_day` set together, `set_on` present for `set_by_user`. A file that can't be parsed, has another `state_version`, or fails any check stops the run with exit 2 ("refusing to run blind") before anything is printed. Links are re-checked and unsafe ones dropped.
- `due.kind` in the state is `explicit`, `inferred`, `none`, `ambiguous` or `set_by_user` (from a `set_due` action, kept in `due_override`). `earlier` is the done or dismissed item a new promise repeats, if any. `restored_on` (optional) is the date of the last `restore`.
- **Runs (two-phase).** Only scheduled runs are logged. `period_key` must be `daily:` or `weekly:` plus two valid dates. On-request runs never add a run or set `first_reported` / `done_reported` (only `--confirm-posted` sets them; a reopen clears `done_reported`). A scheduled run that prints a digest appends `status: "pending"` with the ids it showed as new and as done in `staged`. `--confirm-posted <period_key>` applies those marks (stamped with the run's `as_of`) and sets `status: "posted"`. A pending run printed again with `--resume-pending` keeps the `as_of` of the run that first staged it (its meetings were listed then), whatever `--as-of` the resume used. A run with nothing to report, every meeting read and no Gap is logged as `empty`. An **incomplete** scheduled run (meeting source not `ok`, window past the note-history limit, a meeting whose read failed within its retry budget, or no meeting readable; exit 5) is logged as `status: "incomplete"` with the ids its digest showed as new and as done in `staged`. The period is never recorded as covered, but once its digest is posted, `--confirm-posted <period_key>` applies those marks and sets `confirmed_at` (status stays `incomplete`), so no later digest flags those items new again. It is replaced by the next run of the same period (which also supersedes a pending digest of that same period). A new complete scheduled period in a later slot (week or day, by the day each window ends) marks any older pending run `superseded`; its items are simply reported again, and a superseded period can't be run again (exit 2). While a run is pending, another period for the same slot or an earlier one stops with exit 4 and changes nothing. A scheduled run whose `--as-of` is earlier than the `as_of` of the last posted (or empty) run is a stale trigger: it exits 2 and changes nothing (a `--resume-pending` of a pending period is exempt).
- User `reopen` / `mark_done` evidence carries an `order` string (the run's as-of in UTC plus the action's position) that breaks same-day ties; older files without it still load.
- **Older files.** `state_version: 1` files (from the first release) are read and upgraded on the next write: their scheduled runs become `posted` (or `empty`), on-request runs are dropped, and item ids are kept. Items are matched to new extractions by their stored mentions (meeting, quote, owner and deliverable), so a re-run doesn't duplicate them; a second deliverable from the same sentence, which the old id scheme dropped, becomes its own item.
- `runs` keeps the last `max_runs_logged` entries. Done-and-reported or dismissed items with no mention, evidence, dismissal or restore in the last `prune_after_days` are removed; open items with no mention, due date, evidence or restore in the last `prune_open_after_days` are removed too (counted in Coverage). A run never removes an item its own bundle mentions, gives evidence for or acts on, so the same bundle can be run again with `--resume-pending`. A removed item is forgotten: an on-request run over meetings that old tracks their promises again as new items.
- **`unread_meetings`** (optional; older files without it load) tracks meetings whose read failed, keyed by meeting id: `title`, `day`, `url` (nullable), `attempts` (scheduled runs that failed to read it, at most one per week or day: `last_slot` is `<cadence>:<slot start>`), `first_failed`, `last_failed`, `given_up` (null while retrying) and `reread_until` (its date plus the plan's note history, or `prune_after_days` with no limit). A meeting is given up once `attempts` exceeds `unreadable_retry_runs` or `unreadable_retry_days` have passed since `first_failed`. A retrying meeting that reads (or turns `denied`) is dropped; a given-up one that reads again is dropped once a digest that read it is confirmed posted (staged as `staged.read_again`), and records past `reread_until` are dropped.
- It stores one quote per mention, names, the emails given in the bundle, titles and links. It never stores transcripts or email bodies.

Commands that use only the state file:

- `--list-open --state <file>` prints the items still needing a completion check (not dismissed, not already reported done) as JSON: `commitment_id`, `direction`, `owner`, `owner_email`, `counterpart`, `counterpart_email`, `deliverable`, `made_on`, `meeting_title`, `meeting_url`, `due`, `last_status`. Without a state file it prints `No state file at … yet: nothing is tracked.` and exits 0.
- `--next-window daily|weekly --as-of <now> --state <file>` prints `{"from", "to", "cadence", "period_key", "last_posted", "already_posted", "pending", "unconfirmed_incomplete", "reread"}`: the window for the next scheduled run, from the last day of the last posted window through today (or from the UTC date 14 hours before that run, if earlier, because a meeting in a zone down to UTC-14:00 that started after the run can still carry that date; a digest resumed and posted days later still ends on its own window's last day). With no posted run yet it starts at the earliest `from` of a pending, superseded or incomplete run, so unconfirmed or failed days are scanned again; with no runs at all, this week's Monday (weekly) or the previous business day (daily). `already_posted` names the confirmed run whose window ends in the same slot (week or day) when there is one: post nothing that slot. It does not know the plan's note history, so start the window no earlier than `--as-of` minus `history_limit_days`. `unconfirmed_incomplete` lists Incomplete periods whose digest was staged but not confirmed (confirm each one that reached the destination). `reread` lists given-up meetings still inside their re-read horizon (`meeting_id`, `title`, `day`, `url`, `attempts`, `given_up`).
- `--confirm-posted <period_key> --state <file> [--as-of <now>]` commits a pending run (exit 0, also when already confirmed), or records the marks of an Incomplete period's digest (exit 0, `the period stays open`). Unknown or superseded periods, and incomplete ones logged without staged marks (a dry run or an older file), exit 2.

## `commitments.json` (config)

| Key | Default | Meaning |
| --- | --- | --- |
| `due_soon_days` | 7 | "Due in the next N days" horizon (calendar days, today included). `--due-soon-days` overrides. All day counts must be integers from 1 to 36500. |
| `stale_days` | 21 | Open no-date items this old get "still on your radar?". |
| `business_days` | mon–fri | Weekdays that count as business days. |
| `holidays` | `[]` | Dates (`YYYY-MM-DD`) that are not business days. |
| `plan_history_days` | `{"free": 30, "basic": 30}` | Note history per Granola plan name; plans not listed have no known limit. A window starting before `--as-of` minus this many days makes the digest Incomplete. |

`business_days` and `holidays` must be lists and `plan_history_days` an
object; `null` or another type exits 2.
| `max_quote_chars` | 240 | Longer quotes are cut at a word boundary with "…" in the digest (stored in full). |
| `prune_after_days` | 120 | See State file. |
| `prune_open_after_days` | 180 | Optional (180 if absent). Open items with no mention, due date, evidence or restore for this many days are dropped. |
| `unreadable_retry_runs` | 2 | Optional (2 if absent; 0 or more). Later scheduled runs that retry a meeting whose read failed (each one Incomplete) before it is given up and listed as not covered. |
| `unreadable_retry_days` | 3 | Optional (3 if absent; 0 or more). Days after the first failed read after which the meeting is given up even if runs remain (whichever limit comes first; a weekly schedule gives up on its second failed run). |
| `max_runs_logged` | 200 | See State file. |

## Output

Markdown (default), Slack mrkdwn (`--format slack`), or `--json`:

```json
{
  "status": "reported",
  "period_key": "weekly:2026-10-05..2026-10-09",
  "counts": {"overdue": 1, "due_soon": 2, "later": 0, "no_date": 1, "they": 2, "done": 2},
  "groups": {"overdue": [{"id": "ct-…", "direction": "we_owe", "owner": "…", "counterpart": "…", "deliverable": "…", "due": "2026-10-08", "due_kind": "inferred", "status": "likely_done", "new": true, "meeting_title": "…", "meeting_url": "…", "meeting_day": "…"}]},
  "nudge_candidates": [{"id": "ct-…", "direction": "they_owe", "deliverable": "…", "owner": "…", "owner_email": "…", "counterpart": "…", "counterpart_email": null, "due": "…", "days_overdue": 2, "quote": "…", "meeting_title": "…", "meeting_url": "…", "meeting_day": "…"}],
  "coverage": {"meetings": 6, "soft_mentions": 1, "dismissed": 0, "out_of_scope": 1, "meeting_source": "ok", "meetings_not_read": 0, "links_dropped": 0, "incomplete": [], "retrying": [], "not_covered": [], "read_again": []},
  "confirm_with": "--confirm-posted weekly:2026-10-05..2026-10-09",
  "markdown": "## Commitments — …"
}
```

`status` is `reported`, `skipped` (with `reason`) or `incomplete`. Incomplete
means coverage was partial: the meeting source was not `ok`, the window starts
before the plan's note history, a meeting's read failed (`unavailable`, not
read another way), or no meeting in the bundle could be read
(`coverage.incomplete` lists `meeting_source`, `history_limit`,
`meeting_unread`, `no_meeting_readable`). The digest then opens with an **Incomplete** line; it is
never "nothing to report", and on a scheduled run it exits 5 and the period is
not recorded. `confirm_with` appears on a pending scheduled run and on an
incomplete one (confirming that only records which items it announced).
`coverage.retrying`, `coverage.not_covered` and `coverage.read_again` list
meetings whose read failed and are being retried, were given up after their
retries (Coverage: "Meeting <title>, <date> could not be read after N attempts
— not covered"), or were read again after being given up: `meeting_id`,
`title`, `day`, `url`, `attempts`, `missing` (failed earlier and not in this
bundle), `given_up`.

Exit codes: 0 printed; 2 bad input, config, state, state or lock path, or
stdout could not be written (nothing printed on stdout before the error); 3
state lock held by another run; 4 period pending confirmation (rerun with
`--resume-pending` to print it again), or another period pending for the same
or a later week or day, also when a scheduled digest was staged
but stdout failed (a closed pipe); 5 incomplete scheduled run, period not
recorded. Output is always written as UTF-8.
`nudge_candidates` holds overdue items that are not likely done: for
`we_owe` the recipient is `counterpart_email`, and for `they_owe` it's
`owner_email`.
