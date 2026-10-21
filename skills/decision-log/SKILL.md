---
name: decision-log
description: Keep a running log of what your team decided in meetings. It scans your Granola meetings for the week (optionally one folder or Team Space), logs each decision with who made it, the owner, the date, the meeting link, the reason given, the options rejected and a quote, and flags reversals of earlier decisions, decisions with no owner, proposals that were never actually decided, and revisit dates that have passed. Use when someone asks "what did we decide this week?", "update the decision log" or "when did we decide X and why?", or on a weekly schedule. Read-only on Granola; it writes only the log file, the log doc and the report destination you configure.
---

## What this does

Teams decide things in meetings and then lose them. Pricing changed on a Tuesday call, the CSM threshold moved in a leadership sync, the close calendar shifted in a finance review. Three weeks later nobody remembers who decided, why, or whether a later meeting quietly reversed it. Granola has the notes, one meeting at a time. This skill keeps the ledger across all of them:

- Lists the meetings in the period from Granola (as far as your plan's note history and access allow) and extracts each **decision**: what was decided, who decided it, who owns carrying it out, the date, the meeting link, the reason given, the options rejected, an area tag, a quote copied word for word and any revisit date.
- Keeps **proposals** apart. Something suggested but not agreed ("what if we…", "tentative plan", "to be discussed") is logged as an open proposal, and it never becomes a decision without a later meeting deciding it or you saying it was decided.
- Keeps one durable **decision log** with stable ids. A decision restated in a later meeting is linked to the logged one instead of being logged twice.
- Flags what needs attention: **reversals** (a decision that explicitly supersedes an earlier one, with both cited), **possible conflicts** (two live decisions on the same topic, not linked either way), decisions with **no owner**, and **revisit dates** that have passed.
- Answers "when did we decide X, and why?" from the log, with the meeting citation for every answer.

The agent reads the meetings and does the extraction. A deterministic script (`build_decision_log.py`) checks the extraction (every decision cites a meeting in the bundle; when the meeting text is supplied, a quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn), merges it into the log with stable ids, applies the flag rules and renders the report, the full log and search results.

[`commitment-tracker`](https://type.com/library/skills/commitment-tracker) tracks what people promised to *do*. This skill tracks what the team *decided*. They read the same meetings and work well side by side.

## Before you run

This skill ships a script, a config file and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (`build_decision_log.py`, `decision_log.json`, `sample_bundle_week1.json`, `sample_bundle_week2.json`, `run.sh`, `tests.sh`, `expected_output.txt`, `DATA_CONTRACT.md`) is in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `decision-log`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters), and its hash where your tools report one, with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`. Python 3.8 or newer, standard library only.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Config

```yaml
meeting_source: ""             # granola | gong | fathom | fireflies | otter | zoom | pasted — set in Step 0
folder: ""                     # optional Granola folder name to scan (folders need a paid Granola plan); blank = all meetings
workspace_only: false          # true = only Team Space (workspace-visible) notes
involvement: team              # team = every meeting you can read | mine = meetings you captured or are listed on
log_file: decision-log.json    # the durable log, carried across runs (Step 4)
log_doc: ""                    # optional Type doc that mirrors the full log; rewritten from --render-log after each update
post_to: ""                    # Slack channel or Type Space for the weekly report; blank = reply here only
format: markdown               # markdown (Type Spaces and docs) | slack (Slack mrkdwn; use it when post_to is Slack)
```

These are defaults. Do not edit them here. The values confirmed in Step 0 live in a small settings file, `decision-log-settings.md`, in the user's working folder or Space. That file is not part of this skill. Read it at the start of every run; any key it sets overrides the default above.

Plan history limits, quote length in the report, how many runs and repeats the log keeps, and the number of search results live in `decision_log.json`. `DATA_CONTRACT.md` documents every key and every bundle and log field.

## Step 0: First run, confirm connections

Run this step when `decision-log-settings.md` is missing, when `meeting_source` is blank, or when a configured source or destination is no longer connected.

1. **Detect.** Find which sources can actually be read in this Space. Connected means one real read call succeeds; a tool that is listed but fails its read does not count.
   - **Granola:** call `get_account_info`. Note `mcp_plan` (it sets the note-history limit) and `mcp_note_access.scopes` (`personal`, `public` or both; `public` means Team Space folders are readable). Then `list_meeting_folders` (folder ids, titles, visibility) and `list_meetings` for a short custom range.
   - **In Type, also check workspace connections that exist but are not added to this Space** (for example Granola or Slack connected for the workspace but not attached here). Name each one and offer to add it; use it only once it has been added and a read call succeeds.
   - No Granola? Gong, Fathom, Fireflies, Otter or Zoom transcripts work the same way, as do transcripts the user pastes or uploads (`meeting_source: pasted`).
2. **Propose.** Tell the user what you found and the values you'd set, for example: "Granola (Business plan, Team Space readable) is connected, with folders Leadership, Pricing council and Customer calls. Log decisions from the Leadership folder? Keep the log in `decision-log.json` and mirror it to a Type doc called Decision Log? Weekly report on Fridays at 4pm, posted here?"
3. **Wait for the user to confirm.** Never pick a meeting source, folder, log doc or destination silently.
4. **Save** the confirmed values to `decision-log-settings.md` as a short YAML block using the keys from Config. Never edit this SKILL.md; editing it breaks the file-hash check in "Before you run". If you cannot write files there, give the user the exact lines to save.

If an automation triggered the run and nobody is there to confirm, post one message to `post_to` (or this Space) asking for these values, then exit. That setup question is the only message this skill posts when it is not delivering a report.

## Step 1: Preflight

Granola is read-only and this skill only reads it. The only writes are the log file, the `log_doc` (when configured) and the weekly report to `post_to`.

1. **Meeting source reachable.** If `get_account_info` or `list_meetings` fails, set `sources.meetings.status` to `unavailable` (or `denied`) with the error in `detail`, and run the script anyway so it can say so. The report then opens with an **Incomplete** line, nothing from the window is added to the log, and the script exits 5. Never report "no decisions this week" when the meetings could not be listed.
2. **Log file.** If `log_file` doesn't exist yet, the first update run creates it. If it exists but can't be read or fails validation (any field of the wrong type, missing, out of range, a link to an id that isn't there, or a supersedes cycle), the script stops with exit 2 ("refusing to run blind") before printing anything. Tell the user; don't delete or hand-edit the file.
3. **History limit.** Granola plans that keep a limited note history (30 days on free plans, per `plan_history_days` in `decision_log.json`) can't show older meetings. Put `mcp_plan` in `sources.meetings.plan`, or set `history_limit_days` when you know the limit. A window that starts before the limit is **Incomplete** (exit 5, nothing merged). Start the window at `--as-of` minus the limit instead.

## Step 2: Choose the window and list meetings

- **Scheduled (weekly):** ask the script for the window, so nothing falls between runs:

  ```bash
  python3 build_decision_log.py --next-window --as-of 2026-10-16T16:00:00-07:00 --log decision-log.json
  ```

  It prints `from` (where the window of the last posted report, or of the last week recorded with nothing to report, ended, inclusive; the day before that when that run's `--as-of` was earlier than 14:00 UTC, so a meeting dated in a timezone up to 14 hours behind UTC isn't missed) and `to` (today). With no posted report yet, `from` is the earliest window that was staged or attempted but never posted; with no runs at all, this week's Monday. If `already_posted` is set, this Monday–Sunday week already has its report: stop and post nothing. If `pending` is not empty, a previous report was never confirmed: handle that first (Step 5). If `from` is older than the plan's note history, start at the limit instead (Step 1); the script lists any days it never scanned as a **Gap**.
- **On request** (`scan.cadence: on_request`): the period the person names. "This week" is Monday to today. "Update the decision log" with no period means from the last update to today (the log's `updated_at`, shown in `--render-log`), or the last 7 days for a new log.

List meetings with Granola `list_meetings`. Its `time_range` is one of `last_7_days` (the default), `this_week`, `last_week`, `last_30_days` or `custom`. Always use `custom` with `custom_start` and `custom_end` set to the window's `from` and `to` (ISO dates, both required with `custom`; the end date counts as a whole day in the user's Granola timezone). Don't use `this_week` or `last_week`: they follow the Granola account's own week, which can start on Sunday, while this skill's weeks run Monday to Sunday. Then:

- **Scope.** With `folder` set, pass that folder's `folder_id` (from `list_meeting_folders`; paid Granola plans) to limit the list to one folder. `workspace_only: true` keeps only Team Space (workspace-visible) notes; omit it otherwise. For `involvement: mine` pass `involvement: {captured_by_me: true, listed_as_participant: true}` (true conditions are OR-ed); for `team`, omit `involvement`. `false` conditions are exclusions that are AND-ed, and `workspace_only` is AND-ed with the involvement result. Participant metadata can be incomplete and doesn't prove attendance. Put the folder name and `workspace_only` in `scan`, so the report states the scope.
- **Dates.** `list_meetings` shows times like `Oct 6, 2026 10:00 AM PDT`. Convert each to ISO 8601 with its offset (`2026-10-06T10:00:00-07:00`). The meeting's local date is the decision date.
- **Text.** Titles and notes can arrive with HTML entities (`&lt;&gt;`, `&apos;`). Unescape titles before putting them in the bundle. Meeting text can go in as returned: the quote check ignores entities, markdown backslash escapes and `**` markers.
- **Duplicate notes.** Two people can capture the same meeting. Keep both notes as separate meetings; the report counts them, and a decision extracted from both is one decision (link the second with `same_as`, or give both the same topic and wording).

## Step 3: Read the meetings and extract decisions

1. **Read notes first.** `get_meetings` takes `meeting_ids` (alias `note_ids`): 1 to 10 ids per call, each a UUID or `not_` plus 14 characters. Split longer lists into batches of 10. It returns private notes, Granola's AI summary, attendees and note URLs (no transcript). Set `notes_status` per meeting: `ok`, `denied` or `unavailable`. Put the text you read in `notes_text` (your notes) or `summary_text` (the AI summary) so the script can check quotes against it.
2. **Read the transcript for the exact words.** Call `get_meeting_transcript` (one meeting per call) for each meeting whose notes record a decision, and whenever the notes are too thin to quote. Set `transcript_status` (`ok`, `denied`, `unavailable`, or `not_requested`) and put the transcript in `transcript_text`. Workspace policy can deny transcripts: record `denied`, work from the notes, don't retry through another tool, and never conclude something "wasn't decided" because a transcript was missing.
3. **Use `query_granola_meetings` as a recall net, not a source.** It takes a required `query` and, optionally, up to 10 `document_ids` (alias `note_ids`); it is slow. Ask, for example, "What decisions were made between <from> and <to>?" Its answer is a pointer list: open each cited meeting with `get_meetings` (the citation URL `https://notes.granola.ai/d/<id>` carries the id) and quote the note or transcript itself. Keep its `[[n]](url)` citations if you show its answer to the user.
4. **Decision or proposal?** This is the judgement call the whole log depends on.
   - **Decision:** the meeting settled it. Look for "decided", "we're going with", "agreed", "final answer", "let's do X" that the others accept, a call made by the person who owns that area, or notes that say "Decision:", "Agreed:" or "Consensus:". The decision is the chosen option, stated in a short line.
   - **Proposal:** suggested, not settled. "What if we…", "I propose…", "maybe we should…", "leaning towards…", "tentative plan", "to be discussed", "pending sign-off from finance", or a recommendation nobody accepted. Log it with `kind: "proposal"`.
   - **Not either:** action items (who will do what; that's [`commitment-tracker`](https://type.com/library/skills/commitment-tracker)), status updates, facts, opinions, questions, and decisions made elsewhere that the meeting only mentions without confirming.
   - **If you're unsure whether it was decided, log it as a proposal.** If you're unsure it was even proposed, leave it out. A proposal becomes a decision only when a later meeting decides it (log that decision with `supersedes` pointing at the proposal) or when the user tells you it was decided (a `confirm` reply, Step 6). Never upgrade a proposal yourself.
5. **Fill each entry from what was said.** Nothing here is guessed:
   - `statement`: the decision in one plain line ("Annual plans get 15% off; monthly stays at list").
   - `decided_by`: who made or agreed the call, by name, as the meeting shows it. Empty when it isn't clear. In Granola transcripts `Microphone` is the recorder's microphone and `System audio` can hold several voices, so don't attribute a decision to a speaker by audio label alone. For a proposal, put who proposed it here.
   - `owner`: the one person named as responsible for carrying it out ("Sam, you own the price book"). `null` when nobody was named; the report flags it. Never infer an owner from a job title. Proposals have no owner.
   - `rationale`: the reason given in the meeting, in a short line, or `null` when none was given (the report then says "no reason recorded in the meeting").
   - `alternatives_rejected`: options that were actually discussed and not chosen.
   - `revisit_on`: a YYYY-MM-DD date only when the meeting set one ("let's look again on the 15th"; "reassess at the end of October" is the 31st). Vague timing ("after launch") stays `null`.
   - `area`: a short tag such as `pricing`, `customer-success`, `finance`, `marketing`, `sales`, `product` or `partnerships`. Reuse the tags already in the log.
   - `topic`: the key for the question the decision answers, as `area/subject` (`pricing/annual-discount`, `cs/named-csm-threshold`). Same question, same key, every time: that is how the script finds reversals and conflicts. Run `--topics` first and reuse an existing key whenever a new decision answers the same question.
   - `quote` and `quote_source`: the words that show the decision, copied word for word: one complete sentence, or several consecutive complete sentences, from one speaker's turn in the transcript, or from one line of your notes or of Granola's AI summary. Never cut a sentence short or start it midway, never use `…` or `...` to skip words, never include a speaker label (`Microphone:`, `System audio (Customer):`, `Dana:`), and never run on into the next speaker's turn: "will renew next quarter" cut from "Nobody will renew next quarter" reverses the speaker, and the script rejects it. `summary` quotes are labelled "Granola AI summary, not verbatim speech" in every output.
6. **Link to the log.** Before building the bundle, run `python3 build_decision_log.py --topics --log decision-log.json`. It lists every live decision and open proposal with its id, topic and statement.
   - The same decision said again (a recap, the next meeting confirming it): `same_as` the logged id, or the `ref` of an earlier decision in this bundle. It is linked as a repeat. A repeat with the same topic key and the same wording is linked even without `same_as`.
   - A decision that changes or reverses a logged one, or that decides an open proposal: `supersedes: ["dl-…"]` (logged ids, or refs of earlier decisions in this bundle). Only use it when the meeting says so or clearly decides the same question differently.
   - Not sure whether it's a change or a separate decision: leave both empty and give it the right topic key. If a live decision has the same topic, the report flags a **possible conflict** citing both, and you ask the user.
   - Two decisions in this window that change the same thing: the later one supersedes the earlier one's `ref`, not the logged id the earlier one already replaced. Link only to entries dated on or before the meeting. A bundle whose replies dismiss or supersede an entry that one of its decisions also supersedes stops with exit 2; drop one of the two.
7. **Leave out personal decisions about individuals:** anyone's pay, performance, health, hiring or firing. They don't belong in a shared log.

## Step 4: Update the log

Save the bundle in the working folder (`DATA_CONTRACT.md` has every field) and run:

```bash
python3 build_decision_log.py \
  --bundle bundle.json \
  --log decision-log.json \
  --as-of 2026-10-16T16:00:00-07:00 \
  --scheduled
```

- `--as-of` is required and is the only "now" (ISO datetime with offset). Use the run time.
- `--scheduled` is for the weekly automation only, and it is **two-phase**. The run saves the log, then prints the report and stages the week as *pending*. After you post it, run `--confirm-posted <period>` (Step 5); only then are its entries marked reported. Without `--scheduled` (a person asked), the log is updated the same way and the report lists the decisions dated in the window, but nothing is marked reported, so the next weekly report still announces them. `--dry-run` reads the log and writes nothing.
- Pass `--format` from the `format` setting: `slack` when the report goes to a Slack channel; the default `markdown` suits a Type Space or doc. In both, every value from the bundle is escaped, so its characters can't mention anyone, ping a channel, form a markdown or `<url|label>` link, an image, raw HTML, an autolink (`://`, `www.` and domain dots are broken up) or formatting (in markdown that includes `$` math, `==` highlight and `:emoji:` shortcodes). Markdown uses backslash escapes, which render as the character itself. Slack has no escape character, so in Slack output `*`, `_`, `~`, `` ` ``, `@`, `[` and `]` inside values, the slashes of `://` and dots between letters (as in `www.` or a domain name) are shown as look-alike characters, and a quote containing them is not byte-for-byte verbatim. Links are built only by the script, from absolute `http`/`https` URLs with a real host name and a valid port; other links are dropped and counted.
- `--json` returns the decisions, flags, open proposals, coverage counts, `confirm_with` on a staged run, and the markdown.
- Runs that write the log take an exclusive lock file next to it (`<log>.lock`). A lock older than 15 minutes, or dated more than a minute in the future, is treated as stale (the only time the script reads the clock); it is replaced only if it is still the same file, and a run removes only its own lock. A lock path that is a folder stops the run with exit 2.

Exit codes. The script checks the bundle, config and log, and saves the log, **before** it prints anything:

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Report, log, search result, `Skipped: …` or a command result printed | Post the report (if any); for a scheduled run, then confirm it (Step 5). |
| 2 | Bad input, config, log or arguments (the message names the file and JSON path, for example `$.decisions[2].quote: not a complete sentence in the meeting text…`), a rerun of a superseded week, or stdout could not be written | Fix the bundle and rerun. Never edit the script or the log to get past an error. |
| 3 | Another run holds the log lock | Post nothing. Try again in a few minutes. |
| 4 | This week's report is still pending confirmation (also when this run staged it but its output could not be written, e.g. a closed pipe) | Post nothing yet. Follow "If posting fails" in Step 5. |
| 5 | Coverage incomplete: the meeting list was not `ok`, the window starts before the plan's note history, or none of the meetings could be read | Nothing from the window was added to the log and the week was not staged. On a schedule, post the printed Incomplete notice once (it says why), then run `--confirm-posted <notice key>`: that records the notice, not the week, so the next run's window still covers the week again. If this week's notice was already recorded, the script prints `Skipped: the Incomplete notice … was already posted …` instead: post nothing. On request, tell the user why and what's missing. |

What the script decides, deterministically:

| Rule | Behaviour |
| --- | --- |
| Validation | Every decision cites a `meeting_id` in the bundle, and its `quote_source` must have been read (`transcript_status` or `notes_status` `ok`). When the meeting's `transcript_text`, `notes_text` or `summary_text` is in the bundle, the script checks the quote against it (the shared `granola_text.py` helper): a quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting (whitespace, curly vs straight quotes, dashes, HTML entities, markdown escapes, emphasis, inline code, and link syntax, where `[label](url)` counts as its label). Partial sentences, ellipses, speaker labels and quotes that run across speakers are rejected, as are quotes under 3 words and quotes that add or remove a sentence's final `?`; the error names the reason (for example `not a complete sentence in the meeting text`). Speaker turns are found with heuristics (Granola's labels such as `Microphone`, `System audio`, `Me`, `Them`, `Speaker 2`, and `Name:` patterns; headings like `Decision:` and `Next steps:` are not labels), so an unusual transcript layout can still slip through. The check is mechanical: it confirms the words appear as whole sentences. It doesn't judge whether a quote is representative, in context, or correctly attributed. Without that text the quote is accepted unchecked, and Coverage says how many quotes were checked. |
| Id | `dl-` + the first 10 hex digits of sha256(`meeting_id` \| normalised quote \| topic). Once logged, an id never changes. |
| Re-extraction | The same id, or the same meeting and quote as a logged entry or one of its repeats, changes nothing: it is counted as "already logged". When that meeting and quote gave more than one entry (or this bundle takes more than one decision from it), a match also needs the same topic or wording, so two decisions from one sentence stay two. If its kind, `same_as` or `supersedes` differ from what is logged, the logged version is kept and Coverage says so; use a reply (Step 6) to change it. |
| Repeat | `same_as`, or the same kind, topic and wording (case, punctuation and filler words ignored) as a live entry: linked to that entry and shown as "Repeated: <date> in <meeting>". |
| Reversal | A decision whose `supersedes` names a logged decision: the earlier one becomes *superseded*, and the report shows "⚠️ Reverses `dl-…`" citing both meetings. Superseding a proposal closes it as decided. Only live entries can be superseded, never by an older decision, and never in a cycle. |
| Possible conflict | Two live decisions with the same `topic` where neither repeats nor supersedes the other (checked when a decision is logged or confirmed, and again after every dismiss or restore). Both stay live and both are cited until you reply which stands (Step 6). |
| No owner | A live decision with `owner: null`. |
| Revisit date passed | A live decision whose `revisit_on` is on or before the `--as-of` date. |
| Open proposal | A proposal that no decision superseded, you didn't confirm and you didn't dismiss. Listed in its own section, never with decisions. |
| New (scheduled) | Every entry not yet in a confirmed weekly report, including ones added by on-request runs or by a report that was never confirmed. |
| Nothing to report | A scheduled run with complete coverage, no new entries, no conflicts, no ownerless decisions, no passed revisit dates, no Gap and every meeting readable prints `Skipped: nothing to report …`; that week is recorded straight away, and later runs in the same week print `Skipped: already recorded with nothing to report: …`. |

## Step 5: Post the report and update the log doc

Post the script's output (stdout only; its stderr notes are for you) as one message, to `post_to` for scheduled runs or as the reply when someone asked. It has this shape:

1. **Header and counts**: the window, the as-of time, an **Incomplete** line when coverage was partial, and one line of counts.
2. **New decisions** (scheduled) or **Decisions in this window** (on request): statement, area, id, the date and meeting link, who decided and the owner, the quote with its source, why, what was rejected, the revisit date, repeats, and any reversal or possible conflict.
3. **Needs attention**: reversals, possible conflicts (with the reply that resolves them), decisions with no owner, and revisit dates that have passed.
4. **Open proposals**: discussed, not decided, newest marked new.
5. **Coverage**: window, source and scope, any Gap, the note history limit, meetings and notes read, transcripts read or denied (each denied meeting named), unreadable meetings, duplicate notes, what was extracted and linked, quotes checked, replies applied, links dropped, and the log's totals.

Do not trim Coverage, and do not soften "Reverses" or "Possible conflict". If the script printed `Skipped: …` on a scheduled run, post nothing.

**Confirm a scheduled post (second phase).** As soon as the post succeeds, run:

```bash
python3 build_decision_log.py --confirm-posted weekly:2026-10-09..2026-10-16 --log decision-log.json --as-of <now>
```

(the period key is the `Period key` line of the report, and `confirm_with` in `--json`). Only this marks the entries reported; a rerun of that week then prints `Skipped: already posted …`, and so does any later run in the same Monday–Sunday week. Confirming twice is harmless.

**If posting fails.** Don't confirm. The week stays *pending*, and any rerun of it stops with exit 4 instead of printing the report again. To retry: if you're not sure whether the first post went through (a timeout, for example), look in the destination first; if it is there, just confirm. Otherwise run the same command with `--resume-pending` added, which prints the report again, post it, then confirm. If a pending week is never confirmed, the next week's report supersedes it: its entries reappear as new, Coverage names the unconfirmed report, and the old week can no longer be confirmed or rerun (exit 2).

**Update the log doc** (when `log_doc` is set) after every update run that wrote the log, scheduled ones after the confirm: run `python3 build_decision_log.py --render-log --log decision-log.json --as-of <now>` and replace the doc's content with its output. The doc is a view of `decision-log.json`; edits made in the doc are not read back, so the doc says to reply with changes instead.

## Step 6: Replies the user can give

Turn replies into `user_actions` and apply them with an on-request run straight away (a bundle for any window plus the actions), or include them in the next scheduled bundle. Replies are applied before new decisions are merged, and the log keeps each one in the entry's history. Sending the same reply again changes nothing. A scheduled run that prints `Skipped: already posted …` applies nothing and says how many replies it skipped, and an Incomplete run applies none either.

- "dl-… wasn't a decision" or "drop dl-…" → `dismiss` with their reason as `note`. Hidden from reports and from search, listed under Dismissed in the full log. If it superseded something, that earlier entry is live again.
- "bring back dl-…" → `restore`.
- After every dismiss or restore the script checks all live decisions again: any two on the same topic that neither repeats nor supersedes the other (directly or through entries that aren't dismissed) get a **possible conflict** flag, so the report, the full log and search never show two contradictory live decisions without one.
- "owner of dl-… is Priya" → `set_owner` with `owner`. Shown as "(set by you …)".
- "revisit dl-… on Jan 15" or "no revisit date" → `set_revisit` with `revisit_on` (a date, or `null` to clear it).
- "dl-… (a proposal) was decided" → `confirm`, only when the user says it was actually decided. It becomes a decision dated the day they confirmed it, shown as "Confirmed as decided by you on …", and it is checked for conflicts like any new decision.
- "dl-B supersedes dl-A" → `resolve` with `decision_id` dl-B (the newer), `other_id` dl-A and `resolution: "supersedes"`. Works for a flagged conflict, or to link a reversal the script couldn't see because the topic keys differed.
- "both stand" about a flagged conflict → `resolve` with `resolution: "both_stand"` on the pair as flagged (the newer entry's id as `decision_id`). The flag clears; both stay live.

## Search: "when did we decide X, and why?"

```bash
python3 build_decision_log.py --search "annual discount" --log decision-log.json --as-of <now>
```

It prints up to `search_max_results` matching entries (statement, topic, area, reason, rejected options, quote, meeting title and people are searched; question words like "when", "why" and "decided" are ignored; an id like `dl-5fafbb740f` finds that entry), best matches first and, among equal matches, live entries before superseded ones. Each comes with its date, meeting link, quote, reason and its reversal chain ("Superseded by `dl-…`" or "Reverses `dl-…`"). Dismissed entries are left out unless you search for their id.

Answer **only from these entries**: give the current decision first, when and in which meeting it was made (with the link), the reason recorded, and, when it changed, what it replaced and when. If nothing matches, say the log has no such decision, name the dates its entries span (the output prints them), and offer to scan Granola for that period with an update run. Don't answer from memory, and don't present a `query_granola_meetings` answer as a logged decision; log it first with an update run.

## Guardrails

- **Never invent a decision, an owner, a reason, a date or a quote.** No quote, no entry. An empty field is shown as empty ("owner: none named", "no reason recorded in the meeting").
- **Never promote a proposal.** Only a later decision that supersedes it, or the user's explicit `confirm`, makes it a decision.
- **Quotes are copied word for word**, as whole sentences, and labelled with their source. Never present a Granola AI-summary line as something a person said.
- **Every entry cites its meeting** (title, date, link), and every reversal or conflict cites both meetings.
- **Read-only on Granola.** The only writes are the log file, the configured `log_doc` and the report to the configured `post_to`. Nothing else is posted, emailed or created.
- **No silent gaps.** An unreadable meeting list, denied transcripts, unreadable notes, plan history limits and unscanned days all appear in Coverage, and a window that couldn't be read is reported as Incomplete, never as "no decisions".
- **A schedule posts once per Monday–Sunday week, when the post is confirmed** (two-phase log plus a lock against concurrent runs). The script can't see the destination, so it relies on you confirming each post and checking the destination before re-posting.
- **Minimum personal data.** The log keeps statements, names of who decided and who owns each decision, meeting titles and links, quotes and your replies. It never stores meeting notes or transcripts beyond each entry's quote: the text you put in the bundle is used for the quote check and then discarded. Names only: the script rejects a name field that contains something shaped like an email address (a pattern check, so don't rely on it to catch everything). Leave out decisions about individual people (Step 3).
- Never choose a meeting source, folder, log doc or destination the user has not confirmed (Step 0). Never edit this SKILL.md, the script or the log file to get past an error.

## Automation recipe

Nothing happens until this has a trigger. In **Space settings → Automations**:

- **Weekly (recommended):** Fridays at 4pm local time, running this skill in scheduled mode. The report lands before the weekend with the week's decisions and what needs attention.
- **On request:** "what did we decide this week?", "update the decision log", "when did we decide X and why?", "what's still open from our meetings?"

New automations start disabled. Enable it. Each scheduled run follows the same steps:

1. `--next-window --as-of <now> --log …`. If `already_posted` is set, stop: this week's report is out. If `pending` is not empty, resolve it first (Step 5). `incomplete_notice_posted` set means this week's Incomplete notice is already out: run anyway (coverage may be back), but if the run exits 5 again it prints `Skipped: …`; post nothing.
2. `--topics --log …`, then build the bundle for `from`..`to` (Steps 2 and 3), and run with `--scheduled`.
3. Exit 0 with a report: post it, run `--confirm-posted <period key>`, then update `log_doc`. `Skipped: …`: post nothing. Exit 3 or 4: post nothing. Exit 5: post the Incomplete notice, then run `--confirm-posted <notice key>` (it records the notice only; the week stays open); exit 5 with `Skipped: …`: post nothing. One Incomplete notice per Monday–Sunday week.

If the automation fires twice in a week (a retry, or a manual run after the Friday post), the second run gets a short window from `--next-window`, which already says `already_posted`; a run on it prints `Skipped: already posted …`. Use an on-request run to see the week's decisions again.

## Limits

- Extraction is the agent's judgement: whether something was decided or only proposed, who decided it, and its topic key. The script checks citations, quotes, dates and links, but it can't tell a decision from a proposal, and a decision nobody extracted is a silent miss.
- Reversals and conflicts are found only through `supersedes` and matching topic keys. Two decisions on the same question filed under different keys aren't compared; `--topics` and the `resolve` reply exist for that.
- Repeats are matched on exact wording (after normalising case, punctuation and filler words) or `same_as`. A reworded repeat without `same_as` is flagged as a possible conflict, which you then resolve.
- Quotes are checked only against meeting text that is in the bundle. The check is mechanical: it confirms the words appear as whole sentences from one speaker's turn, not that the quote is representative, in context or correctly attributed (a sentence answering a question, a later retraction or sarcasm can still mislead). Granola AI summaries paraphrase; quotes from them are labelled as such.
- Coverage is only as complete as the meeting list: notes outside the scope (folder, Team Space, involvement), older than the plan's note history, or not shared with the connected account are not seen.
- One log per scope. Keep separate log files for separate teams or folders. The doc mirror is one-way.
- Weekly cadence only for scheduled runs; on-request runs take any window.
- On request, "Decisions in this window" lists decisions made (or confirmed) in the window. A decision made earlier and only restated in the window isn't listed there; it shows as "Repeated" on its entry in search and the full log.
- A weekly report lists every open proposal and every live decision without an owner or with a passed revisit date, every week until they are resolved, so a large backlog makes a long post.

## Verification status

**Tested offline (`bash tests.sh`):** input validation (every required field, wrong types, `null` lists, lone surrogates, NaN, strict dates with naive, compact, ISO-week, `T24:00` and out-of-range offsets rejected, quotes that break the whole-sentence rule (QA's dropped-negation examples such as "will renew next quarter" from "Nobody will renew next quarter", a mid-line `Riley:` label crossed, partial-word, speaker-label and cross-turn examples, `…`-stitched and invented quotes; emphasis, link syntax and Granola escapes ignored), `granola_text.py` byte-identical to the shared copy (SHA-256), quotes from unread sources, decisions citing meetings not in the bundle, email addresses as names), merging (re-extraction by id and by meeting and quote, two decisions from one quote, repeats by `same_as`, by ref and by wording, duplicate notes), supersedes (reversals, proposals settled, cycles, older-than and already-superseded targets), conflicts and both resolutions, conflicts re-checked after dismiss and restore, replies in every kind and their idempotence, flags (no owner, revisit passed, open proposals), search (question words, ids, reversal chains, no match), the full log, the two-phase weekly flow (stage, rerun exit 4, resume, confirm, same-week skip, superseded weeks, Gap lines, Incomplete exit 5 for an unavailable source, a window past the note history and unreadable meetings, never recorded; the Incomplete notice recorded once and skipped on later runs that week), lock files (held, stale, future-dated, folder, foreign), unwritable logs, broken pipes, a single-field mutation fuzz over the bundle and the log (no tracebacks), an escaping fuzz over every printed field in markdown and Slack (no link, autolink, image, raw HTML, mention or broken formatting from bundle text), a multi-week scenario harness that follows the automation recipe literally (outages, failed posts, lost confirms, double triggers, on-request runs and replies in between) and checks after every step that no week is posted twice, nothing is announced as new twice and nothing logged is lost, plus docs-consistency checks. `bash run.sh` matches `expected_output.txt` byte for byte on Python 3.14 and 3.8. All sample data is fictional.

**Smoke-tested live (read-only), 2026-10-12:** against a connected Granola account in Type (`mcp_plan` plus; personal and Team Space scopes): `get_account_info`, `list_meetings` with `custom` over one Monday–Sunday week (15 meetings, 5 of them Team Space notes), `get_meetings` on 6 of them in one call, and `get_meeting_transcript` on one meeting (access allowed). A bundle built from those real response shapes (6 meetings including one meeting captured twice; 8 decisions, 1 of them a repeat, and 2 proposals; 10 of 10 quotes checked against AI-summary text containing HTML entities, markdown escapes and a markdown link, and against transcript text with straight instead of curly apostrophes) ran the scheduled flow on Python 3.14 and 3.8 with identical output and no traceback: `--next-window`, a staged report, a rerun stopped with exit 4, `--resume-pending` in Slack format, `--confirm-posted`, `--next-window` reporting `already_posted`, `Skipped: already posted …`, then `--topics`, `--search`, `--render-log` and an on-request `--json` run. The real data was deleted afterwards; nothing from it is in this package. That live run used the earlier code: this round's changes (the shared `granola_text.py` quote check and escaping) were tested offline only.

**Not yet verified live:** posting from a real automation to Slack or a Space, rewriting a real Type doc from `--render-log`, and other recorders (Gong, Fathom, Fireflies, Otter, Zoom). Check the first few reports by hand.

## Credits

The decision record fields (status vocabulary proposed / accepted / superseded, decision-makers, considered options, chosen option and its justification, "superseded by", and when to revisit) are adapted from [MADR](https://github.com/adr/madr) (Markdown Architectural Decision Records), MIT. See `NOTICE.md` and `LICENSE.txt`.
