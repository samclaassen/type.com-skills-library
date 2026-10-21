# Data contract: decision-log

`build_decision_log.py` reads three files: the **bundle** the agent assembles for one run, the **log** carried
across runs, and the **config** (`decision_log.json`). All are UTF-8 JSON. `NaN`, `Infinity`, lone surrogates
(`\ud800`-style escapes) and duplicate ids are rejected. Any problem stops the run with exit 2 and a message that
names the file and the JSON path, before anything is printed.

Dates are strict: `YYYY-MM-DD` with ASCII digits, years 1900–2999. Datetimes are ISO 8601 with a UTC offset:
`YYYY-MM-DDTHH:MM[:SS[.fff|.ffffff]]` followed by `Z` or `±HH:MM` (hours 00–23, minutes and seconds 00–59, offsets
up to ±14:00). Naive datetimes, compact (`20261009`) and ISO-week (`2026-W41`) forms and `T24:00` are rejected the
same way on Python 3.8 and 3.14.

Text fields are trimmed. An empty string counts as missing. Length limits are in characters.

## Bundle

```json
{
  "schema_version": 1,
  "scan": {"from": "2026-10-09", "to": "2026-10-16", "cadence": "weekly", "folder": null, "workspace_only": false},
  "sources": {"meetings": {"provider": "granola", "status": "ok", "detail": null, "plan": "business"}},
  "meetings": [ ... ],
  "decisions": [ ... ],
  "user_actions": [ ... ]
}
```

Unknown keys are ignored (the samples carry a `_comment`).

### `scan` (required)

| Field | Type | Notes |
| --- | --- | --- |
| `from`, `to` | date, required | The window, inclusive. `from` ≤ `to` ≤ the `--as-of` date. Scheduled runs use the window from `--next-window`; `to` must equal the `--as-of` date. |
| `cadence` | `weekly` \| `on_request`, required | `--scheduled` needs `weekly`. |
| `folder` | string ≤ 120, optional | Granola folder name when the scan was limited to one folder. Printed under Coverage → Scope. |
| `workspace_only` | boolean, optional (default `false`) | `true` when only Team Space (workspace-visible) notes were listed. Printed under Scope. |

### `sources.meetings` (required)

A source-status object for the meeting list.

| Field | Type | Notes |
| --- | --- | --- |
| `provider` | string ≤ 40, required | `granola`, `gong`, `fathom`, `fireflies`, `otter`, `zoom` or `pasted` get a display name; anything else is printed as given, in lower case. |
| `status` | `ok` \| `unavailable` \| `denied` \| `not_configured`, required | Anything but `ok` makes the run **Incomplete** (exit 5): nothing is merged or staged. |
| `detail` | string ≤ 300 or null | The error, printed in the report. |
| `plan` | string ≤ 40 or null | Granola `mcp_plan`. Looked up (case-insensitively) in the config's `plan_history_days`. |
| `history_limit_days` | integer ≥ 1 or null | Overrides the plan lookup. A window starting before `--as-of` minus this many days is Incomplete. |

### `meetings[]` (optional list; `[]` when none)

| Field | Type | Notes |
| --- | --- | --- |
| `meeting_id` | string, required | Unique in the bundle. Letters, digits and `_ . : / + = -`, up to 128 characters, starting with a letter or digit (a Granola UUID or `not_…` id fits, and so do Zoom meeting UUIDs). |
| `title` | string ≤ 300, required | Unescape HTML entities first (`&lt;&gt;` → `<>`). Printed escaped. |
| `start` | datetime with offset, required | Its local date must be inside the window, and it must not be after `--as-of`. The local date is the decision date. |
| `url` | string or null | The meeting's note link (`https://notes.granola.ai/d/<id>`). Only absolute `http`/`https` URLs with a DNS host name and a port 1–65535 are linked; anything else is dropped and counted under Coverage. |
| `notes_status` | `ok` \| `denied` \| `unavailable`, required | Whether notes and AI summary were read. |
| `transcript_status` | `ok` \| `denied` \| `unavailable` \| `not_requested`, required | Whether the transcript was read. A meeting with neither notes nor transcript readable is named under Coverage; if every meeting is unreadable the run is Incomplete. |
| `notes_text`, `summary_text`, `transcript_text` | string or null, optional | The text you read (your notes, Granola's AI summary, the transcript), up to 5,000,000 characters each. Only allowed when the matching status is `ok` (notes and summary use `notes_status`). Used to check quotes, then discarded: never written to the log. An empty or whitespace-only value is an error (exit 2); omit the field when the text wasn't read. |

### `decisions[]` (optional list)

One entry per decision or proposal the agent extracted.

| Field | Type | Notes |
| --- | --- | --- |
| `ref` | string, required | Unique in the bundle; letters, digits, `_ . -`, up to 64. Lets a later decision in the same bundle point at this one. |
| `kind` | `decision` \| `proposal`, required | See SKILL.md Step 3. If unsure, `proposal`. |
| `meeting_id` | string, required | Must be a `meeting_id` in this bundle. |
| `statement` | string ≤ 300, required | The decision (or proposal) in one line. |
| `topic` | string, required | `area/subject` key: lowercase `a-z`, `0-9` and hyphens, 1–4 parts joined by `/`, at most 80 characters. Same question, same key. |
| `area` | string, required | Tag: lowercase `a-z`, `0-9` and hyphens, at most 40 characters. |
| `decided_by` | list of names, optional | Up to 10, each ≤ 120 characters; a value containing something shaped like an email address (`name@company.example`, also with a full-width `＠`) is rejected; case-insensitive duplicates dropped. For a proposal, who proposed it. |
| `owner` | name or null, optional | ≤ 120 characters, email-shaped values rejected as for `decided_by`. Not allowed on a proposal. `null` on a decision is flagged as "no owner". |
| `rationale` | string ≤ 600 or null, optional | The reason given in the meeting. |
| `alternatives_rejected` | list of strings, optional | Up to 10, each ≤ 200. |
| `revisit_on` | date or null, optional | Not before the meeting's date. |
| `quote` | string ≤ 1000, required | Copied word for word. When the meeting's text for `quote_source` is in the bundle, the quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn in it (`granola_text.check_quote`; turns and sentences are found heuristically, see SKILL.md, Validation). Partial sentences, ellipses (`…`, `...`), speaker labels, quotes under 3 words and quotes that run across speakers are rejected, and a sentence's final `?` can't be added or removed. Case, whitespace, curly/straight quotes, dashes, full-width forms, invisible characters, HTML entities, markdown backslash escapes, emphasis markers (`**` `__` `~~` `*` `_`), inline code backticks and link syntax (`[label](url)` → label) are ignored on both sides. The check doesn't judge whether the quote is representative or in context. |
| `quote_source` | `transcript` \| `notes` \| `summary`, required | `transcript` needs `transcript_status: ok`; `notes` and `summary` need `notes_status: ok`. `summary` is labelled "Granola AI summary, not verbatim speech". |
| `same_as` | string or null, optional | A logged id (`dl-` + 10 hex) or the `ref` of an earlier decision in this bundle (earlier by meeting date, then start time, then bundle order). Links this entry as a repeat. Must be the same kind; the target can't be dismissed. Not together with `supersedes`. |
| `supersedes` | list of strings, optional | Logged ids or refs of earlier decisions in this bundle (up to 10). Only on a decision. Each target must be a live decision or an open proposal dated on or before this meeting. |

### `user_actions[]` (optional list)

The user's replies (SKILL.md Step 6). Applied in order of `date`, then position, before decisions are merged. A reply
identical to one already in the entry's history (same action, date, note and values) is skipped.

| Field | Type | Notes |
| --- | --- | --- |
| `decision_id` | logged id, required | Must be in the log. |
| `action` | `dismiss` \| `restore` \| `set_owner` \| `set_revisit` \| `confirm` \| `resolve`, required | |
| `date` | date, required | Not after `--as-of`, not before the entry's meeting date. |
| `note` | string ≤ 300 or null | Shown with dismissals and confirmations. |
| `owner` | name | `set_owner` only (required). Decisions only (including confirmed proposals). |
| `revisit_on` | date or null | `set_revisit` only (key required; `null` clears). Decisions only. |
| `other_id` | logged id | `resolve` only (required); must differ from `decision_id`. |
| `resolution` | `supersedes` \| `both_stand` | `resolve` only (required). `supersedes`: `decision_id` (a decision) supersedes `other_id` (a live decision or open proposal, not newer, no cycle). `both_stand`: clears a flagged conflict; `decision_id` must be the entry that carries the flag (the newer one). |

`confirm` applies only to an open proposal; it becomes a decision dated the reply's `date`. Keys that belong to
another action (for example `owner` on a `dismiss`) are rejected.

## Log file (`log_file`, written by the script)

```json
{
  "log_version": 1,
  "updated_at": "2026-10-16T16:00:00-07:00",
  "records": {"dl-5fafbb740f": { ... }},
  "runs": [ ... ]
}
```

Written atomically (temp file, then rename), keys sorted, before anything is printed. Every field is validated on
load; a log that fails validation stops the run (exit 2, "refusing to run blind"). Entries are never deleted:
dismissed ones stay, hidden.

### `records.<id>`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `dl-` + 10 hex | Equals its key. |
| `kind` | `decision` \| `proposal` | As first logged; a confirmed proposal keeps `kind: proposal` and has `confirmed`. |
| `statement`, `topic`, `area`, `decided_by`, `owner`, `rationale`, `alternatives` | as in the bundle | `alternatives` holds `alternatives_rejected`. |
| `owner_set` | date or null | When a `set_owner` reply set the owner. |
| `revisit_on`, `revisit_set` | date or null | `revisit_set`: when a `set_revisit` reply changed it. |
| `origin` | object | `meeting_id`, `title`, `date`, `start`, `url`, `quote`, `quote_source`, `quote_checked` (boolean). |
| `mentions` | list | Repeats: `meeting_id`, `title`, `date`, `url`, `quote`, `quote_source`. Only the newest `max_mentions_kept` are kept. |
| `mention_count` | integer | All repeats ever linked. |
| `supersedes` | list of ids | Entries this one replaced. "Superseded by" is computed from these. |
| `conflicts` | list | `{"with": <older id>, "resolution": null \| "supersedes" \| "both_stand", "date": <date or null>}`. |
| `dismissed`, `confirmed` | `{"date", "note"}` or null | From replies. |
| `logged_at` | datetime | The `--as-of` of the run that added it. |
| `reported_at` | datetime or null | Set by `--confirm-posted` for entries in a confirmed weekly report. |
| `history` | list | Applied replies: `action`, `date`, `note`, and the action's values. |

Status is computed, never stored: *dismissed*; *superseded* (a live decision lists it in `supersedes`), or *decided*
for a proposal; otherwise *active* (a decision or confirmed proposal) or *open* (a proposal).

### `runs[]`

Scheduled runs only, newest last, at most `max_runs_logged`.

| Field | Type | Notes |
| --- | --- | --- |
| `period_key` | `weekly:<from>..<to>` | |
| `as_of` | datetime | The run's `--as-of`. |
| `status` | `pending` \| `posted` \| `empty` \| `superseded` \| `incomplete` | `empty` = nothing to report (recorded straight away). `incomplete` records the attempt so `--next-window` covers it again; `--confirm-posted` on it records only that its Incomplete notice was posted (`notice_posted`). |
| `staged` | list of ids or null | The entries the report announced; a list for `pending`, `posted` and `superseded`, null otherwise. |
| `confirmed_at` | datetime or null | From `--confirm-posted --as-of`. |
| `notice_posted` | boolean, optional | Only on `incomplete` runs: the Incomplete notice was posted. Kept when the period is rerun; later Incomplete runs ending in the same Monday–Sunday week print `Skipped: …` (exit 5). |
| `counts` | object of integers | Decisions and proposals announced. |

## Config (`decision_log.json`)

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `plan_history_days` | object: plan name → integer ≥ 1 | `{"free": 30, "basic": 30}` | Note history per Granola plan. Plans not listed have no limit unless the bundle sets `history_limit_days`. |
| `max_quote_chars` | integer 40–1000 | 280 | Quotes longer than this are shortened at a word boundary with ` …` in reports (the log keeps the full quote). |
| `max_runs_logged` | integer 4–10000 | 104 | Scheduled runs kept in the log. |
| `max_mentions_kept` | integer 1–1000 | 20 | Repeats kept per entry (the count keeps growing). |
| `search_max_results` | integer 1–200 | 10 | Entries `--search` prints. |

All five keys are required.

## Commands

| Command | Needs | Prints |
| --- | --- | --- |
| `--bundle B --log L --as-of T [--scheduled [--resume-pending]] [--dry-run] [--format markdown\|slack] [--json]` | | The period report (or `Skipped: …`); updates the log unless `--dry-run`. |
| `--next-window --log L --as-of T` | | JSON: `from`, `to`, `cadence`, `period_key`, `last_posted`, `already_posted`, `incomplete_notice_posted` (this week's recorded Incomplete notice, or null), `pending`. |
| `--confirm-posted KEY --log L [--as-of T]` | an existing log | `Confirmed posted: …` or `Already confirmed: …`; for an Incomplete period, `Recorded: the Incomplete notice …` or `Already recorded: …`. |
| `--topics --log L` | | JSON list of live decisions and open proposals: `id`, `kind`, `status`, `topic`, `area`, `statement`, `date`, `meeting_title`. |
| `--search TEXT --log L --as-of T [--format] [--json]` | | Matching entries with citations. |
| `--render-log --log L --as-of T [--format]` | | The whole log: active decisions by area, open proposals, superseded and closed, dismissed. |

`--config` defaults to `decision_log.json` next to the script. The commands can't be combined. On a missing log,
`--topics`, `--search` and `--render-log` say there is no log yet and exit 0.
