# Data contract: one-on-one-prep

The agent reads Granola (and optionally Linear/Jira and Slack), builds one JSON **bundle** per prep, and runs:

```bash
python3 build_prep.py --bundle bundle.json --as-of 2026-10-13T09:30:00-07:00 [--meetings 4] [--json] [--config prep.json]
```

The script is offline and deterministic (stdlib only, Python 3.8+). It never calls an API and never writes a file. The bundle holds quotes from private 1:1s, so delete it after the run.

## Conventions

- **Dates**: `YYYY-MM-DD`, or a full ISO-8601 datetime with `Z` or `±HH:MM` (for example `2026-10-06T10:00:00-07:00`). Only ASCII digits are accepted. Hours run 00–23, minutes and seconds 00–59, optional fractions are .fff or .ffffff, offsets go up to ±14:00, and years run 1900–2999. Compact forms (`20261006`), ISO weeks, `T24:00`, offsets without a colon and naive datetimes are rejected with exit 2. They are rejected the same way on every Python from 3.8. Where a date is expected, a datetime is accepted and its own local date is used.
- **IDs** (`id`, `meeting_id`, `ref`, `event_id`): 1–128 characters from letters, digits, `:` `.` `_` `-`, starting with a letter or digit.
- **Text** is one line. Line breaks, tabs, control characters and invisible format characters (zero-width, bidi overrides) are removed or turned into spaces before use.
- **Links** are kept only if they are absolute `http(s)` URLs with a real host name, no user name or password, and a valid port. Anything else is dropped and counted in Gaps ("N links dropped"). Text is never turned into a link.
- **JSON**: `NaN`, `Infinity` and unpaired surrogate escapes (`\ud800`) are rejected. A file that isn't UTF-8 or is nested absurdly deep is rejected too.
- **Forbidden keys**: a key anywhere in the bundle that matches `forbidden_keys` in `prep.json` (case-insensitive: `rating`, `score`, `sentiment`, `mood`, `morale`, `flight_risk`, `health`, `medical`, `age`, `religion`, `salary` and others) stops the run with exit 2. This skill never rates people or records mood, health or protected characteristics.

## Bundle

```json
{
  "schema_version": 1,
  "user": {"name": "Dana Ortiz", "email": "dana@feltharrow.example", "role": "manager"},
  "counterpart": {"name": "Riley Chen", "email": "riley@feltharrow.example"},
  "series": {"titles": ["Dana / Riley 1:1"], "match": "title_and_attendee"},
  "upcoming": {"start": "2026-10-13T10:00:00-07:00", "title": "Dana / Riley 1:1", "event_id": "evt-1013", "url": null},
  "sources": {
    "meetings": {"provider": "granola", "status": "ok", "detail": "...", "history_days": null},
    "transcripts": {"provider": "granola", "status": "ok"},
    "issues": {"provider": "linear", "status": "ok"},
    "slack": {"provider": "slack", "status": "not_configured"}
  },
  "meetings": [ ... ],
  "items": [ ... ],
  "work_items": [ ... ],
  "withheld_by_agent": 0
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Must be `1`. |
| `user.name`, `counterpart.name` | string ≤ 80 | yes | Printed (the counterpart's first name is used in labels). Their name parts are masked before the withholding check, so a name such as "Hope" or "Christian" is never read as a term. |
| `user.email`, `counterpart.email` | e-mail | yes | Used only to match meetings. Never printed. They must differ. |
| `user.role` | `manager` \| `report` | yes | Which side you are on. It decides labels such as "Feedback you promised Riley" or "Feedback Dana promised you", and which side's topics lead the agenda (always the report's). |
| `series.titles` | list of strings ≤ 200 | yes unless `attendee_only` | The confirmed titles of the recurring 1:1. Matching ignores case, spacing and punctuation. Never printed. |
| `series.match` | `title_and_attendee` (default) \| `attendee_only` | no | `attendee_only` reads any meeting with just the two of you. |
| `upcoming` | object or null | no | `start` (datetime, required if present) puts the time in the heading and resolves "before our next 1:1". `title`, `event_id` and `url` are validated but not printed. |
| `sources.meetings` | source object | yes | Where the 1:1 notes came from. `provider`: `granola`, `gong`, `fathom`, `fireflies`, `otter`, `zoom` or `pasted`. A `status` other than `ok` prints an **Incomplete** banner, and the prep never says "nothing open". `history_days` (int, optional) records a plan limit such as 30 days on free Granola, shown under Gaps. |
| `sources.transcripts` | source object | no | `denied` or `unavailable` adds a Gaps line ("quotes come from notes and summaries"). |
| `sources.issues` | source object | no | `provider`: `linear` or `jira`. If `unavailable` or `denied`, Gaps says open work was not checked. |
| `sources.slack` | source object | no | `provider`: `slack`. Evidence only. |
| `withheld_by_agent` | int 0–1000 | no | Personal items the agent left out of the bundle itself. They are added to the "withheld as personal" count, never described. |

A **source object** is `{provider, status, detail}`. `status` is `ok`, `unavailable`, `denied` or `not_configured`. `detail` (≤ 300 characters) is validated and never printed. A missing optional source counts as `not_configured`. Any other key under `sources` is rejected.

### `meetings[]` (every meeting found with the other person, including group meetings)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | id | yes | Granola meeting ID. Must be unique. |
| `title` | string ≤ 300 | yes | Compared with `series.titles`. Never printed. |
| `start` | datetime with offset | yes | Its local date is the date shown. |
| `url` | http(s) URL | no | The Granola note link, printed as "notes". |
| `attendee_emails` | list of e-mails | yes | Both you and the counterpart must be on it, and nobody else beyond `max_other_attendees` (room calendars in `ignored_attendee_domains` don't count). |
| `notes` | `ok` (default) \| `unavailable` \| `denied` | no | If not `ok`, the date is listed under Gaps ("notes not readable"). |
| `transcript` | `ok` \| `denied` \| `unavailable` \| `not_requested` (default) | no | Recorded and returned in `--json`. |
| `notes_owner` | `user` (default) \| `counterpart` \| `unknown` | no | Who captured the notes quoted from this meeting. Notes quotes are labelled "(your notes)", "(Riley's notes)" or "(notes, author unknown)". |
| `text` | string ≤ 2,000,000 | no | The notes and transcript text you read. If present, every item from this meeting with `quote_from` `transcript` or `notes` is checked against it with the shared `granola_text.py` (`check_quote`): a quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting (HTML entities, markdown emphasis and link syntax, Granola's backslash escapes, curly quotes, spacing). Partial sentences (so a dropped leading "Nobody" or "not"), ellipses, speaker labels, quotes that run across speakers, quotes under 3 words and a changed final question mark are rejected. Turns start at every line and at every speaker label at the start of a line; a label later in a line starts a turn only where it begins a sentence and no negation is in scope ("Dana: will you renew? Riley: no we will not." has two turns, while "Dana: will you renew Riley: no we will not" and "Not even our asst. mgr. Okafor: …" are one turn); labels are found with heuristics. Without `text`, the quote is checked against itself, which still rejects speaker labels, ellipses and quotes under 3 words. A rejected item is not shown and is counted under Gaps. Never printed. |

### `items[]`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | id | yes | Unique in the bundle. |
| `kind` | `commitment` \| `feedback` \| `question` \| `goal` \| `topic` | yes | See SKILL.md Step 5. |
| `topic` | `project` \| `goal` \| `process` \| `customer` \| `team-logistics` \| `work-feedback` \| `career-growth` \| `work-question` \| `personal` \| `other` | no, but see notes | The work category the agent assigns (SKILL.md Step 5). Only the eight work topics are shown. `personal`, `other` or a missing topic withholds the item (and the whole merged group) and counts it as "withheld as personal or uncategorised". Any other value exits 2. Returned in `--json`, never printed. |
| `owner` | `user` \| `counterpart` | yes | Commitment and feedback: who owes it. Question: who asked. Goal and topic: who raised it. `feedback` must be owned by the manager's side, or the run exits 2. |
| `key` | slug `[a-z0-9-]`, ≤ 80 | no | Groups the same item across meetings. Without it, items with the same normalised summary are grouped. |
| `summary` | string ≤ `max_summary_chars` (160) | yes | Neutral, about the work. Printed. Any rating, judgement or mood wording leaves the item out. |
| `meeting_id` | id | yes | The meeting it was said in. If that meeting isn't one of the 1:1s read, the item is not shown and is counted. |
| `quote` | string ≤ 5,000 | yes | The exact words (no quote, no item). Printed in quotation marks, clipped at `max_quote_chars` (280) with "…". If `quote_from` is `summary`, it is labelled "AI summary" and printed without quotation marks. |
| `quote_from` | `transcript` \| `notes` \| `summary` | yes | Where the quote came from. |
| `speaker` | `user` \| `counterpart` \| `unknown` (default) | no | Who said it, only when certain. |
| `status` | `open` (default) \| `likely_done` \| `done` | no | A done claim counts only with `evidence` dated on or after the last meeting where the item came up, and no later than `--as-of`. |
| `due` | date | no | An exact due date. It wins over `due_phrase`. |
| `due_phrase` | string ≤ 60 | no | The words said ("by Friday", "end of month", "before our next 1:1"), resolved against the meeting date. See SKILL.md Step 6. |
| `evidence` | list (≤ 20) | no | See below. |

`evidence[]`: `source` (`granola`, `linear`, `jira`, `slack` or `user`, required), `summary` (≤ 200, required, neutral), `date` (required), `url`, `ref` (an issue key such as `CS-88`) and `meeting_id`. `granola` evidence needs a `meeting_id` that is in `meetings`. `linear`, `jira` and `slack` evidence needs a `url` or a `ref`. `user` means the user told you so.

### `work_items[]` (optional, Linear or Jira issues assigned to the other person)

`source` (`linear` or `jira`), `ref` (id), `topic` (as for items: a work topic, or the issue is withheld), `title` (≤ 300), `state` (`open`, `in_progress`, `done` or `canceled`), `status` (the tracker's own label, ≤ 60, optional), `updated` (date) and `url`. Only `open` and `in_progress` issues are listed, newest first. Titles, labels and refs go through the same withholding check.

## How items are merged and withheld

1. Meetings are selected (SKILL.md Step 2). Items citing other meetings are dropped and counted.
2. Items are grouped by (`kind`, `owner`, `key` or normalised `summary`). An exact repeat (same group, same meeting, same quote) is dropped.
3. Each group goes through two gates. **Gate 1 (allowlist):** if any occurrence lacks a work `topic`, the whole group is withheld and counted once in "N items withheld as personal or uncategorised". **Gate 2 (backstop):** every summary, quote, evidence summary, evidence ref and due phrase in all its occurrences goes through the scanner. Any **personal** hit withholds the whole group and counts in the same line. If there is no personal hit, any **judgement** hit leaves the group out ("Left out: …"). Neither line ever shows the text, the topic, the category or the meeting.
4. The latest occurrence's summary is shown, with the first quote and, if different, the latest quote.

The scanner normalises text first: it decodes HTML entities, applies NFKC, turns non-ASCII digits into ASCII digits, removes invisible characters and accents, case-folds, maps look-alike letters (Cyrillic, Greek and others) to Latin, joins spaced-out letters ("s i c k"), and writes out contractions ("I'm" becomes "I am"). It also checks a copy without markdown emphasis, a copy with leetspeak digits mapped to letters ("m0m" becomes "mom"), a copy where a name (masked to "the person") followed by a noun reads "their", and a copy where a capitalised word right before "is", "was", "'s a", "can't", "doesn't" and similar, or right after "terminate", "fire", "against" or "into", reads "the person" ("Sam is lazy", "Morgan's a nightmare"). Finally, every word of 4+ letters is compared with `fuzzy_terms`: a word with the same first letter that is one edit away (two for terms of 10+ letters; swapping two neighbouring letters is one edit) and is not itself a fuzzy term or in `fuzzy_ignore` counts as personal ("suicidle", "theripist", "Zolof"). Matching is pattern-based and can miss wording. See SKILL.md Limits.

## `prep.json` (script config)

| Key | Default | Meaning |
| --- | --- | --- |
| `schema_version` | 1 | Must be 1. |
| `lookback_meetings` | 4 | Previous 1:1s to read when `--meetings` isn't given (1–12). |
| `lookback_days` | 120 | Only meetings from the last N days are read (7–730). |
| `recurring_min_meetings` | 2 | A topic raised in at least this many of the 1:1s read is "keeps coming back". |
| `max_per_section` | 6 | Lines per section before "+N more (run with --json …)". |
| `agenda_max` | 7 | Agenda lines before "…and N more". |
| `max_other_attendees` | 0 | Other people allowed on a meeting for it to still count as a 1:1. |
| `ignored_attendee_domains` | Google room and group calendar domains | Attendees on these domains (or their subdomains) are not counted as people. |
| `max_summary_chars` | 160 | Longer summaries exit 2. |
| `max_quote_chars` | 280 | Longer quotes are clipped at a word boundary with "…". |
| `max_items` | 300 | Bundle limit (exit 2 above it). |
| `max_meetings` | 200 | Bundle limit. |
| `max_work_items` | 100 | Bundle limit. |
| `forbidden_keys` | `rating`, `score`, `sentiment`, `mood`, … | Bundle keys that stop the run. |
| `category_labels` | | Names for the categories in `sensitive_terms`. They are never printed in a prep; they document the lists. |
| `sensitive_terms` | | Pattern lists per category: health, family and caregiving, bereavement, wellbeing, personal finances, confidential HR matters, political views, the protected characteristics (age, race or ethnicity, national origin, religion, sex or gender, gender identity, sexual orientation, genetic information, military status, union membership), accent or native language, appearance, arrest or legal matters, and `high_risk`: words that count with or without a subject (self-harm including slang and euphemisms, diagnoses, treatment and medication names, leave with a date, arrest and court, bereavement, home life, and a few Spanish, French, German and Italian words). |
| `work_modifier_nouns` | | Work nouns (client, customer, FAQ, handbook, section, policy, campaign, SKU and so on). A sensitive match that only describes one of these nouns, directly or with one word in between, or that follows one of them and "is a" ("our client is a fertility clinic chain"), is about the work and doesn't withhold the item ("fertility clinic client", "visa sponsorship FAQ"). This never applies to a match about leave, sickness, medical matters or an HR process (PIP, investigation, disciplinary, termination, grievance, accommodation, warning, complaint, and the suicide, rehab and HIV/STI terms), so "bereavement leave section of the handbook" is withheld. The word in between can't be a function word, so "pregnant and the client…" is still withheld, and so is a match right after "my", "his", "her", "their" or "your" ("my IVF clinic invoices") or after one of the two people's names, which counts like "their" ("Riley PIP documentation"). The item is withheld if any other sensitive match in the same text is not of this kind. |
| `person_terms` | | Pattern lists per category (each needs a `category_labels` entry) that are about a person whoever it is. They are matched on the person form, an extra scan form where every way of referring to a person becomes "the person": the two people's names in any case ("riley"), pronouns, "my report", capitalised name-like words (a third party such as "Sam" in a 1:1 with Riley), initials ("J.", "T.R.", "J's"), a lower-case word in a name's place (before "'s" unless it is a common word, as the subject at the start of a clause before a verb, "priya is leaving", or as the object of a verb such as "let … go", "give … notice", "telling … the"), and a role noun after a determiner or possessive ("our designer", "the new contractor", "my QA lead", "an intern"; a role followed by a work or position noun, "the analyst seat", is not a person). A capitalised word is not read as a person when it is a common sentence word, a month or day, listed in `non_person_words`, all capitals, right after an article or possessive ("the Redis cache"), right before a system noun ("Salesforce integration"), or at the start of a sentence unless a verb follows ("Exit Sam by Q1": "Exit" is a verb; "Sam stops being on payroll": "Sam" is a person). `sensitive_terms` are matched on this form too, so pay, health and accommodation wording about anyone counts the same as wording about the two people. `{p}` in a pattern is a person reference and `{pp}` a person's possessive. A match withholds the item under any topic. Person detection is heuristic: a role noun that is not on the built-in list, a lower-case name in other positions, or a nickname is not a person, and a capitalised company or product name is (see SKILL.md Limits). |
| `separation_concepts` | | Name-agnostic words and phrases for an employment ending (severance, offboarding, layoff, part ways, let go, last day, successor, replacement, role eliminated, notice, end date, "won't be with us", "has to go", reclaiming a person's laptop or access, warnings and conduct issues, and others). On every work topic, any of these concepts plus any person reference in the same text withholds the item (fail-closed). A separation verb followed by a bare name-like word ("offboarding priya") withholds it on its own. The one exception is a concept that is plainly about a piece of work: it modifies a work noun ("exit survey results", "notice period clause"), a work noun modifies it ("vendor exit", "contract end date"), or it acts on an object that is not a person and is not held by one ("replace the flaky integration tests", "end date for the pilot contract", "a backfill for the open analyst role"). An object held by a person ("Riley's seat", "her accounts"), a person-like object ("our night auditor", "the team"), a person after the object ("the contract for Riley"), and the HR-process concepts (severance, PIP, probation, layoffs, redundancy, resignation, notice period, garden leave, demotion, dismissal) except as a modifier are never work. After the configured dismissal and replacement verbs, an unfamiliar object is treated as a person unless its complete head is recognised as inanimate work, or a complete technical compound establishes that meaning. A work-noun prefix inside an unknown role is insufficient ("workflow chief"). Ambiguous heads such as pilot, agent, driver, owner and job require explicit technical context or a complete inanimate object ("pilot project", "database driver", "cron job"). The decision does not depend on enumerating occupations. |
| `non_person_words` | | Capitalised words never read as a person in the scan form above (teams, functions, common products: `Legal`, `Payroll`, `Redis`, `Jenkins`…). |
| `judgement_terms` | | `any`: wording that rates or judges one of the two people or a named person, used everywhere including quotes. `authored`: mood, feeling and trait words, used only on text the agent wrote. |
| `fuzzy_terms` | | High-risk words (lower-case a-z, 4-30 letters) whose near-miss spellings count as personal. Exact matches are left to the patterns. |
| `fuzzy_ignore` | | Real words that sit within the fuzzy distance of a fuzzy term and are not misspellings of it (`cancel`, `released`, `meditation`…). |

Patterns are Python regular expressions with placeholders: `{who}` (a person, including "I" and "you"), `{other}`, `{be}` (a linking verb), `{end}` (end of a clause) and `{frag}` (the start of a note fragment with no subject). A space in a pattern also matches no space. Use `[0-9]`, not `\d`. A pattern that doesn't compile stops the run with exit 2 and names its path.

## Output

**Markdown** (default): see SKILL.md Step 7 and `expected_output.txt`.

**`--json`**: one object with `schema_version`, `as_of`, `user_role`, `counterpart`, `meetings_read` (`id`, `date`, `url`, `notes`, `transcript`), `excluded_meetings` (counts: `after_as_of`, `not_both_attendees`, `other_attendees`, `other_title`, `older_than_window`, `beyond_count`), `dropped_items` (counts: `outside_meetings`, `unknown_meeting`, `quote_not_found`, `duplicate`), `dropped_links`, `items` (`kind`, `topic`, `owner`, `summary`, `status`, `status_note`, `due`, `due_phrase`, `due_note`, `overdue_days`, `raised`, `recurring`, `first`, `latest`, `evidence`, `latest_evidence`, `item_ids`), `agenda` (markdown lines), `work_items` (with `topic`), `withheld_personal` (`count`, `item_ids`, `by_agent`, `by_topic`: withheld by gate 1, `by_pattern`: withheld by gate 2), `left_out_judgement` (`count`, `item_ids`), `sources`, `gaps` and `markdown`. Text in `items` and `work_items` is plain, with links, ID-like numbers, e-mail addresses and phone numbers removed (pattern-based, with `granola_text.redact_contacts` for e-mails and phones) but not markdown-escaped, so escape it before you render it. Withheld and left-out items appear only as deterministic SHA-256 hashes of their UTF-8 IDs (64 lowercase hexadecimal characters), grouped as before. Their original IDs and text are not echoed. Hashes are stable correlation tokens, not encryption; use opaque IDs rather than putting personal information in identifiers.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The prep was printed. |
| 2 | Bad input, config or arguments. The message names the file and JSON path and never repeats free text. Nothing is printed on stdout. |
| 6 | The output could not be written (closed stdout, broken pipe). |

Screening limits: the patterns are an English-language backstop, not semantic classification. Ambiguous and novel wording may be missed or withheld, and human review remains required. Population prevalence is distinct from workflow quantities such as weeks, seats and technical exception handling. Personal accommodations and a planned dismissal, exit or replacement of a person must be filed as `personal` by the agent even when they look like hiring, backfill or team logistics; the script cannot enforce that filing and its backstop catches only some wordings (see SKILL.md Limits for measured rates).

### Round-10 repair coverage

Explicit dismissal verbs (`fire`, `terminate`, `dismiss`, `sack`, `lay off`, `let go`, `rescind`) require a complete recognised work-object phrase for the work exemption. An unfamiliar role (such as `the SRE`, `the temp`, or `the harbor pilot`) is no longer assumed to be work; crew, pod and squad objects are treated as people. A colleague assisting with a technical object (`with Ana`) or identifying it (`Wen flagged`) can remain ordinary work; person-held employment objects remain conservative. Leave, hospitalisation, named procedures, panic episodes, custody hearings, person-linked pay increases and offer/visa details have additional backstop patterns. Work policies, templates and technical objects retain narrow context exemptions.

`test_regressions.py` runs the disclosed round-10 corpus plus probe pairs through the CLI entrypoint and rendered Markdown. Every attack has a work twin; every work topic and each source-quote, neutral-quote/authored-summary and AI-summary-quote path is exercised with a useful sibling commitment. The tests require found quotes, a personal-withhold reason, no sensitive output, and useful overdue work in the agenda. These are tuned regression cases, not a new recall estimate.

The independent follow-up also checks passive dismissal subjects, complete commercial objects for buyouts, and person-held permits. Requests to cover, move or schedule work are screened independently from their attached temporal/reason clauses; unknown reasons are withheld even when the main clause names legitimate work. Recognisable work and calendar context, technical objects and complete vendor/supplier contract or agreement phrases remain eligible. These rules are conservative and may hold unfamiliar ordinary work for review.
