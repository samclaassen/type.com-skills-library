---
name: commitment-tracker
description: Scan your Granola meetings for the promises made on each side — what you and your team owe people ("I'll send the deck by Friday") and what they owe you — then check Gmail, Slack, Linear or Jira, your calendar and later meetings for proof each one was delivered, and report what is overdue, due soon, open, owed to you and newly done, with drafted nudges you approve. Use when someone asks "what did I promise people this week?", "what am I behind on?", "open follow-ups from my meetings" or "who owes us what?", or on a daily or weekly schedule. Read-only on your tools; follow-up emails are drafted only when you approve them and are never sent.
---

## What this does

Every call ends with promises: a revised SOW by Wednesday, pricing by Friday, a security questionnaire coming back "by end of week". Granola captures them one meeting at a time. Nobody adds them up across meetings, and nobody checks whether they were kept. This skill does both:

- Lists the meetings in the period from Granola (as far as your plan's note history and access allow) and extracts each commitment with its owner, the account it's owed to, the verbatim quote and the meeting link.
- Splits **we owe** (you and your team) from **they owe us** (customers, partners, vendors).
- Turns "by Friday" or "next week" into a date, using the meeting's own date and business days, and marks it *inferred*. It never invents a date.
- Looks for proof of delivery in the tools you've connected (sent mail, Slack, Linear or Jira, calendar invites, later meetings) and marks each item done, likely done (with the reason), open or overdue.
- Remembers what it has already reported. Items aren't announced as new twice, dismissals stick, and done items are shown once and then drop off. A scheduled digest counts as posted only after you (or the automation) confirm the post, so a failed post can be retried and a confirmed period is not posted again.

The output is one digest: **Overdue → Due in the next N days → (Due later) → Open with no date → They owe us → Done since last run**, plus a Coverage section that says exactly which meetings and sources were read and which weren't.

[`pre-call-briefing`](https://type.com/library/skills/pre-call-briefing) surfaces overdue commitments for one upcoming call. This skill keeps the running ledger across all of them.

## Before you run

This skill ships a script, a config file and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (`build_commitments.py`, `commitments.json`, `sample_bundle_week1.json`, `sample_bundle_week2.json`, `run.sh`, `tests.sh`, `expected_output.txt`, `DATA_CONTRACT.md`) is in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `commitment-tracker`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters), and its hash where your tools report one, with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`. Python 3.8 or newer, standard library only.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Config

```yaml
meeting_source: ""             # granola | gong | fathom | fireflies | otter | zoom | pasted — set in Step 0
internal_domains: []           # your company's email domains; decides "we owe" vs "they owe us" and internal meetings
completion_sources: []         # any of gmail, outlook, slack, linear, jira, asana, google_calendar, outlook_calendar — set in Step 0
cadence: weekly                # daily | weekly — the scheduled scan window (Step 2)
scope: external                # external | all — all also tracks promises made in internal-only meetings
owners: []                     # optional: names/emails to show only these people's commitments on our side ("what did *I* promise")
due_soon_days: 7               # "Due in the next N days"
soft_mentions: false           # true lists vague intentions ("we should think about…") in their own section; never tracked
post_to: ""                    # Slack channel or Type Space for scheduled digests; blank = reply here only (Slack → run with --format slack)
state_file: commitment-state.json   # carried across runs; see Step 5
nudge_drafts: offer            # offer | off — offer drafted follow-ups for overdue items; never created without approval
```

These are defaults. Do not edit them here. The values confirmed in Step 0 live in a small settings file, `commitment-tracker-settings.md`, in the user's working folder or Space. That file is not part of this skill. Read it at the start of every run; any key it sets overrides the default above.

`internal_domains` must hold at least one domain. Without it the script refuses to run, because it could not tell your promises from theirs.

Business days, holidays, the stale threshold (21 days), Granola plan history limits, quote length and the retry budget for unreadable meetings (`unreadable_retry_runs: 2`, `unreadable_retry_days: 3`, Step 3) live in `commitments.json`. `DATA_CONTRACT.md` documents every key and every bundle field.

## Step 0: First run, confirm connections

Run this step when `commitment-tracker-settings.md` is missing, when `meeting_source` or `internal_domains` is blank, or when a configured source is no longer connected.

1. **Detect.** Find which sources can actually be read in this Space. Connected means one real read call succeeds; a tool that is listed but fails its read does not count.
   - **Granola:** call `get_account_info`. Note the account email (who "I" am), `mcp_plan` (sets the history limit) and the active workspace. Then `list_meetings` for `this_week`.
   - **Completion sources:** one cheap read each. Gmail or Outlook (search sent mail for `newer_than:1d`), Slack (search one word), Linear or Jira (list one issue), Google or Outlook Calendar (list one event).
   - **In Type, also check workspace connections that exist but are not added to this Space** (for example Granola or Gmail connected for the workspace but not attached here). Name each one and offer to add it; use it only once it has been added and a read call succeeds.
   - No Granola? Gong, Fathom, Fireflies, Otter or Zoom transcripts work the same way, as do transcripts the user pastes or uploads (`meeting_source: pasted`).
2. **Propose.** Tell the user what you found and the values you'd set, for example: "Granola (Plus plan) and Gmail are connected; Slack and Linear are connected in your workspace but not added to this Space, want me to add them? Your internal domain looks like `brackenvole.example`, right? Weekly digest on Fridays at 4pm, posted here?"
3. **Wait for the user to confirm.** Never pick a meeting source, a destination channel or an internal domain silently.
4. **Save** the confirmed values to `commitment-tracker-settings.md` as a short YAML block using the keys from Config. Never edit this SKILL.md; editing it breaks the file-hash check in "Before you run". If you cannot write files there, give the user the exact lines to save.

If an automation triggered the run and nobody is there to confirm, post one message to `post_to` (or this Space) asking for these values, then exit. That setup question is the only message this skill posts when it is not delivering a digest.

## Step 1: Preflight

This skill only reads. Granola is read-only, and every completion check is a search or a read. The only writes are the digest itself (to `post_to`) and, after the user approves a specific draft, a Gmail or Outlook draft (Step 7), which needs draft permission on that mailbox.

1. **Meeting source reachable.** If `get_account_info` or `list_meetings` fails, set `sources.meetings.status` to `unavailable` (or `denied`) and continue only to say so: the digest opens with an **Incomplete** line, carries `Source: Granola — unavailable`, still lists items already in the state file, and claims nothing about new meetings. Never fall back to "no commitments found". On a scheduled run the script then exits 5 and does **not** record the period (only the attempt), so the next run covers that window again (Step 6). The same happens when no meeting in the bundle could be read at all (every one's notes and transcript denied or unavailable): partial coverage is never reported as complete or as "nothing to report".
2. **Completion sources.** Try each configured one. A failure becomes `status: unavailable` with the error in `detail`; it is printed on the Gaps line, never dropped.
3. **State file.** If `state_file` doesn't exist yet, the first run creates it. If it exists but can't be read or fails validation (any field of the wrong type, missing or out of range), the script stops with exit 2 ("refusing to run blind") before printing anything: running without it would re-announce every item as new and could double-post a period. Tell the user; don't delete the file. State files written by earlier versions of this skill (`state_version: 1`) are read and upgraded on the next write; their item ids are kept.
4. **History limit.** Granola plans that keep a limited note history (30 days on free plans, per `plan_history_days` in `commitments.json`) cannot show older meetings. Put `mcp_plan` in `sources.meetings.plan`. If the user's plan is different from what the config lists, set `history_limit_days` explicitly. A window that starts before the limit is **Incomplete**: the digest says so, and a scheduled run exits 5 without recording the period. So never ask for a window older than the limit: start it at `--as-of` minus the limit (30 days on free plans) and let Coverage list the earlier days as a Gap.

## Step 2: Choose the window and list meetings

- **Scheduled (weekly or daily):** the window runs from the end of the last *posted* period to now, so nothing falls between runs (a Friday 4pm weekly run would otherwise miss Friday evening and the weekend). Ask the script:

  ```bash
  python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state commitment-state.json
  ```

  It prints `from` (the last day of the last posted window, inclusive, or the UTC date 14 hours before that run if that is earlier, so a meeting in a timezone up to 14 hours behind UTC that started after the run isn't missed; the overlap is harmless because the script merges anything already tracked, even a re-extraction whose deliverable you worded differently) and `to` (today), with `scan.cadence` set to the cadence you passed. With no posted run yet, `from` is the earliest window that was staged or attempted but never posted (an unconfirmed digest, or a run that exited 5), so those days are scanned again; with no runs at all it is this week's Monday (weekly) or the previous business day (daily). If `from` is older than the plan's note history, use `--as-of` minus the limit instead (Step 1). Use the same `--as-of` for the run itself: a scheduled bundle whose `to` is after the `--as-of` date is refused (exit 2), because it would use up a later week's or day's slot. If `already_posted` is set, this week (weekly) or today (daily) already has a confirmed digest: stop and post nothing; a run on that window would print `Skipped: already posted …`. If its `pending` list is not empty, a previous digest was never confirmed: handle that first (Step 6). A window that starts after the last posted run, or after the earliest unposted one, is flagged as a **Gap** in Coverage.
- **On request** (`scan.cadence: on_request`): the period the person names. "This week" is Monday to today; "what am I behind on?" with no period is the last 14 days (the state file already holds anything older that is still open).

List meetings with Granola `list_meetings`. Its `time_range` is one of `this_week`, `last_week`, `last_30_days` (the default) or `custom`; for this skill use `custom` with `custom_start` and `custom_end` (ISO dates, both required with `custom`). Optional: `folder_id` (paid Granola plans) and `workspace_only: true` (omit it otherwise). Then:

- **Whose meetings.** `involvement` takes `captured_by_me` and `listed_as_participant`. Conditions set to `true` are OR-ed (either one qualifies); conditions set to `false` are exclusions that are AND-ed; `workspace_only` is AND-ed with the involvement result. For "what did *I* promise", set `involvement: {captured_by_me: true, listed_as_participant: true}` (meetings I captured **or** am listed on). For the team ledger, omit `involvement` so teammates' workspace-visible notes count too. Participant metadata can be incomplete and doesn't prove attendance, so don't treat the filter as proof of who was there.
- **Dates.** `list_meetings` shows times like `Oct 6, 2026 10:00 AM PDT`. Convert each to ISO 8601 with its offset (`2026-10-06T10:00:00-07:00`). The meeting's own local date anchors every relative due date, so keep the offset that was in effect at the meeting.
- **Internal vs external.** The script classifies each meeting from attendee domains against `internal_domains`. Granola's `known_participants` is sometimes incomplete (a customer call can list only the note creator). When the notes or transcript clearly show an outside party, set `audience: "external"` with an `audience_reason`; the Coverage section discloses every override. With `scope: external`, internal-only meetings are counted but their promises are skipped.
- **Duplicate notes.** Two teammates can each capture the same call. Keep both notes as separate meetings. The script reports same-title, same-start notes as duplicates in Coverage and merges a commitment extracted from both when its owner, counterpart and deliverable match; if you worded it differently in the second note, link it with `same_as`.

## Step 3: Read the meetings and extract commitments

1. **Read notes first.** `get_meetings` takes `meeting_ids` (alias `note_ids`): 1 to 10 ids per call, each a UUID or `not_` plus 14 characters. Split longer lists into batches of 10. It returns the AI summary, private notes, attendees and note URLs (no transcript). Set `notes_status` per meeting: `ok`, `denied` or `unavailable`. If a batch call fails, retry it once; if it still fails, keep every meeting of that batch in the bundle with `notes_status: unavailable` (never drop them). A meeting whose notes or transcript read failed (`unavailable`) and that wasn't read another way makes the run **Incomplete**: nothing is staged, so the next run's window starts before it and reads it again. That retry is bounded, so one meeting that stays unreadable can't stall every run: scheduled runs retry it for up to `unreadable_retry_runs` more runs (2) or `unreadable_retry_days` days after its first failure (3), whichever comes first (a second trigger in the same week or day is not a new attempt). After that the script gives up on it: the digest is staged normally with a Coverage line "Meeting <title>, <date> could not be read after N attempts — not covered", and the state file records it as given up so it never blocks a run again. `--next-window` then lists it under `reread` until it falls out of the plan's note history (120 days with no limit): try `get_meetings` on each one that isn't in your window; if it reads, add it to the bundle with `reread: true` (its date may be before `from`) and extract its commitments as usual; if it still fails, leave it out. Coverage then says it "was read again". A meeting is never dropped silently: it is read, retried (Incomplete), or listed as not covered; one that failed before and is missing from a later bundle whose window holds its date counts as failing again. `denied` (no permission) is not retried; it is listed in Coverage.
2. **Read the transcript for quotes.** Call `get_meeting_transcript` (one meeting id per call) for each external meeting whose notes mention a deliverable, a deadline or "next steps", and whenever the notes are too thin to quote. Set `transcript_status` (`ok`, `denied`, `unavailable`, or `not_requested` if you didn't ask). Workspace policy can deny transcripts: record `denied` and work from the notes. Don't retry through another tool, and never conclude something "wasn't discussed".
3. **Use `query_granola_meetings` as a recall net, not a source.** It takes a required `query` and, optionally, up to 10 `document_ids` (alias `note_ids`) to search within; it is slow. Ask it, for example, "List the follow-ups promised to or by external people between <from> and <to>". Its answer is a pointer list. Open each cited meeting with `get_meetings` (the citation URL `https://notes.granola.ai/d/<id>` carries the id) and quote from the note or transcript itself. Keep its `[[n]](url)` citations if you show its answer to the user.
4. **Who said it.** In Granola transcripts `Microphone` is the recorder's own microphone. That's the note creator, which may be a teammate rather than the user. `System audio (Name)` is the other side of the call, but an unnamed `System audio` line can hold several voices. Attribute an owner only when the speaker, or an explicit assignment ("Sam will send…"), makes it clear.
5. **What counts as a commitment.** It needs an owner and a concrete deliverable, and the owner must have agreed to it:
   - "I'll …", "Let me …", "We'll get you …", "I'll have it to you by …": personal or team commitment. The owner is the speaker. For "we'll", it's the speaker on behalf of their team.
   - "Can you … by Friday?" answered with "sure" or "yes, I'll do it": an assigned commitment, owned by the person who said yes.
   - A notes or summary line like "Dana to send the checklist next week": a commitment, with `quote_source: "summary"` or `"notes"`.
6. **What doesn't count.** "We should look into…", "Maybe we could…", "That would be cool", "Someone needs to…" (no owner), "Let's circle back sometime", and status reports on finished work ("I already sent it"). Record the first three kinds as `kind: "soft_mention"` with their quote. They are never tracked; the script lists them in their own section when `soft_mentions` is on, and otherwise only counts them in Coverage. **If you're unsure, leave it out.** A missed promise can be added by hand; an invented one costs the skill its credibility.
7. **Quote verbatim.** The `quote` is copied character for character from the transcript, your notes or the AI summary, and `quote_source` says which. Light trimming at the ends is fine; rewording is not. No quote, no commitment. The bundle carries no meeting text, so the script doesn't check the wording: it only requires a quote and checks that its source was marked read. Copying it exactly is up to you.
8. **Due dates: give the words, not your guess.**
   - An explicit date ("by October 9", "on the 15th" with the month clear): `due: {"kind": "explicit", "date": "2026-10-09", "phrase": "by October 9"}`.
   - Anything relative ("by Friday", "tomorrow", "end of week", "next week", "early next week", "in 3 business days", "end of month"): `due: {"kind": "relative", "phrase": "by Friday"}`, with no date. The script resolves it from the meeting's local date and business days and labels it *inferred*. A weekday said on that same weekday ("by Friday" on a Friday) comes back as "date unclear — confirm".
   - No timing, or vague timing ("soon", "ASAP", "before the renewal"): `due: {"kind": "none", "phrase": "soon"}` (phrase optional). It goes in "Open, no date".
9. **Fill the rest** per `DATA_CONTRACT.md`: `direction` (`we_owe` when the owner is on an internal domain, otherwise `they_owe`), `owner`, `owner_email` when known (the script rejects a `we_owe` owner from outside your domains), `counterpart` (the account, or the person waiting), an imperative `deliverable` ("Send the revised SOW"), and a `ref` unique within the bundle.
10. **Repeats.** If a later meeting restates a promise ("sorry, the SOW slipped, I'll get it over by Monday"), extract it again from that meeting. The script merges items with the same direction, owner, counterpart and deliverable wording, uses the newest date, and shows "re-promised …, was due …". For a paraphrase, set `same_as` to the earlier ref. For an item already in the state file, set `known_id` to its id (see `--list-open` in Step 4). Only items still open when the repeat was said take it: if the earlier item was done or dismissed before that day, the new promise is a new item (leave `known_id` out; the script rejects a `known_id` that points at an item closed before that meeting). A repeat said before the item closed (read late, for example) joins it; one said on the day you dismissed it or marked it done is a new promise.

## Step 4: Check for proof of delivery (read-only)

Check two sets: the commitments you just extracted, and every item still open in the state file:

```bash
python3 build_commitments.py --list-open --state commitment-state.json
```

That prints each open item's `commitment_id`, direction, owner, counterpart, counterpart email, deliverable and the date it was made (or a one-line "No state file … yet" before the first run). Search only after that date:

| Source | We owe (did we deliver?) | They owe us (did they?) |
| --- | --- | --- |
| Gmail | `in:sent to:<counterpart domain or email> after:<YYYY/MM/DD>` plus a deliverable keyword | `from:<domain> after:<date>` |
| Outlook | `GET /me/mailFolders/sentitems/messages?$search="<keyword>"`, filtered by recipient and date | Inbox, filtered by sender domain |
| Slack | `search.messages` for the deliverable or account name after the date, including shared channels | same |
| Linear / Jira | Issues mentioning the account or deliverable; done/closed state | — |
| Calendar | For "set up a call" or "schedule a session": an event with the counterpart created after the meeting | — |
| Later meetings | A later Granola meeting with the account where it was handed over, acknowledged, or still being chased | same |

Search results are metadata; open a message, thread or issue only to confirm it matches, and never copy its body into the bundle. Record each finding as an `evidence` entry: `kind`, `strength`, `date`, `url`, a short `summary` written by you (no long pasted email bodies), and `why` for weak evidence. Use `ref` for this run's commitments or `commitment_id` for items from the state file.

- **strong**: it plainly delivers the thing to the counterpart. Examples: a sent email to their domain whose subject or attachment matches; a Linear issue closed for exactly this; a calendar invite with them for the promised session; a later meeting where they confirm receipt. Result: **done**.
- **weak**: it suggests delivery but doesn't prove it. Examples: an email to them after the meeting with no matching attachment; "sending it now" in Slack; an issue moved to review. Result: **likely done**, shown with your `why`, and the item stays in its group until confirmed.
- **against**: it shows the item is still outstanding, for example "still waiting on the quote" in a later meeting. It cancels older weak or strong evidence; an item already reported done becomes fully open again (back in `--list-open`, reported again when next done).

An empty search is not evidence. Leave the item open; the digest lists which sources were checked. Only cite a source whose status is `ok` (the script enforces this). Evidence dated before the commitment is rejected, and evidence older than a later repeat of the promise doesn't count (the repeat shows it didn't deliver it; the digest shows it as "Not counted").

## Step 5: Build the digest

Save the bundle in the working folder and run:

```bash
python3 build_commitments.py \
  --bundle bundle.json \
  --config commitments.json \
  --as-of 2026-10-09T16:00:00-07:00 \
  --internal-domain brackenvole.example \
  --state commitment-state.json \
  --scheduled
```

- `--as-of` is required and is the only "now" (ISO datetime with offset). Use the run time.
- Pass `--internal-domain` once per entry in `internal_domains`; `--owner` once per name or email in `owners`; `--scope all` and `--soft-mentions` when configured; `--due-soon-days N` to override the config.
- `--format slack` when `post_to` is a Slack channel (Slack mrkdwn, `<url|label>` links); the default `markdown` suits a Type Space or doc. In both, every value from the bundle is escaped, so its characters can't ping `@channel`, mention people, form a markdown or `<url|label>` link, an autolink or raw HTML, or break the digest's bold, italic or code. Markdown escapes with backslashes (the backslash first), including `$` and `=`, so `$…$` and `==…==` in a value can't become math or a highlight (Slack has neither, so they stay as typed there). Markdown also escapes `://` and every dot inside a word (rendered unchanged), so a bare URL, `www.` name or domain in a title or quote can't be auto-linked. Slack has no escape character, so `*`, `_`, `~` and `` ` `` inside values are shown as look-alikes (`∗ ＿ ∼ ˋ`), `@` before a name as `＠` (so even a post sent with Slack's `link_names` can't mention anyone), and `://` and a dot inside a word before a letter (as in a domain or `www.` name) as `:∕∕` and `․` (so Slack can't auto-link them); a quote containing them is not byte-for-byte verbatim in Slack. Links the script builds use only absolute `http`/`https` URLs with a real host name and a valid port; others are dropped and counted under Coverage.
- `--scheduled` is for automation runs only, and it is **two-phase**. The run saves the state, then prints the digest and stages the period (`<cadence>:<from>..<to>`) as *pending*. After you post it, run `--confirm-posted <period>` (Step 6); only then do its items stop being "new" and its done items drop off. It prints `Skipped: already posted …` for a confirmed period, and also for any later run of the same cadence slot (the same Monday–Sunday week for weekly, the same day for daily, by the day each window ends), whatever window `--next-window` returned (while a digest for the same or a later slot is still pending, the run stops with exit 4 instead); and `Skipped: nothing to report …` when there's nothing at all to say and coverage was complete: every meeting read and no Gap (that period is recorded straight away). Skipped runs post nothing.
- Without `--scheduled` (a person asked, for example "what did *I* promise?" with `--owner`), the digest is always printed and the run is **read-only on reporting state**: new promises, evidence and your replies are saved, but nothing is marked as reported (only `--confirm-posted` does that), so the next scheduled digest still announces everything. Use `--dry-run` for a preview that writes nothing at all.
- `--json` returns counts, groups, `nudge_candidates` (overdue items with recipients and quotes for Step 7), `confirm_with` on a pending scheduled run, and the markdown.
- Runs that write the state take an exclusive lock file next to it (`<state>.lock`), so two runs can't both post the same period. A lock older than 15 minutes, or dated more than a minute in the future, is treated as stale (the only time the script reads the clock); it is replaced only if it is still the same file, and a run removes only its own lock. A lock path that is a folder stops the run with exit 2.

Exit codes. The script checks the bundle, config and state, and saves the state, **before** it prints anything, so a failing run never leaves a half-posted digest:

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Digest, `Skipped: …` or a command result printed | Post the digest (if any); for a scheduled run, then confirm it (Step 6). |
| 2 | Bad input, config or state (including a list set to `null`), a rerun of a superseded period, a stale scheduled run (`--as-of` earlier than the last posted run: post nothing), or stdout could not be written; the message names the file and JSON path (for example `$.commitments[3].quote: is required`) | Fix the bundle and rerun. Never edit the script or the state file to get past an error. |
| 3 | Another run holds the state lock | Post nothing. Try again in a few minutes. |
| 4 | This period's digest, or another digest for the same or a later week (weekly) or day (daily), is still pending confirmation (also when this run staged it but its output could not be written, e.g. a closed pipe) | Post nothing yet. Follow "If posting fails" in Step 6. |
| 5 | Scheduled run with incomplete coverage: the meeting source was not `ok`, the window starts before the plan's note history, a meeting's read failed (`unavailable`, not read another way, within its retry budget), or no meeting could be read | Post the printed digest once (its **Incomplete** line says why), then `--confirm-posted <period key>`: that only records the items it announced (so they aren't flagged new again); the period stays open, so the next run's window covers it again. For a history-limit window, rerun with `from` at the limit instead. Don't re-post an identical incomplete digest if the automation retries while the source is still down. |

What the script decides, deterministically:

| Rule | Behaviour |
| --- | --- |
| Commitment id | `ct-` + first 10 hex of sha256(`meeting_id` \| normalised quote \| normalised owner \| normalised deliverable). Two deliverables promised in one sentence are two items. Stable across runs; curly vs straight quotes, case, punctuation and filler words in the deliverable don't change it. Items in state files from earlier versions keep their old ids and are matched by their stored mentions. |
| Merge | Same direction + owner + counterpart + deliverable (filler words ignored), `same_as` or `known_id` → one item. This is also how a commitment captured in two notes of one meeting merges. A re-extraction from a meeting an item already cites joins that item. Otherwise a repeat merges only into an item that was **open** when it was said (the newest one made by the day it was said, if two open items share the wording): a new promise made after the matching item was done or dismissed becomes a new item, marked "promised again; the earlier `ct-…` was done/dismissed"; one said before that day (read late) joins the closed item; one said on the day you dismissed it or marked it done counts as new. The newest dated mention sets the due date; an earlier different date shows as "was due". A re-extraction from the same meeting with the same verbatim quote and owner joins the tracked item even when the deliverable is worded differently (unless that one sentence holds two promises). |
| Due dates | Relative phrases resolve from the meeting's local date: weekday names to the next such day; "tomorrow" to the next business day; "end of week", "next week", "end of month" and "end of quarter" to the last business day of that period (the next period's, if it was said after the last business day, e.g. on a Saturday the 31st); "early next week" to its second business day; "in N business days" skipping weekends and `holidays`. A month and day without a year ("Oct 20") is shown as "year inferred". Unrecognised or vague phrases give no date, and so does anything that would land more than 3650 days after the meeting ("in 400000 days" is shown as "no firm date … — confirm"). |
| Status | Dismissed by user → hidden. Only evidence dated on or after the latest promise counts. Strong evidence (not overtaken by later "against" or your "reopen") → done. On the same day "against" beats found evidence, and between your own replies the later one wins (a `mark_done` after a `reopen` counts). An item that stops being done is fully open again: back in `--list-open`, reported again when next done, never pruned as done. Weak evidence → likely done, kept in its group. Otherwise: past due → overdue; due within `due_soon_days` → due soon; later; or no date. |
| Stale | Open, no date, made ≥ `stale_days` (21) ago → "still on your radar?" |
| New / done | "new" until a scheduled digest showing it is confirmed posted; done items are shown under "Done since last run" until then, and then drop off. |
| Pruning | Done-and-reported or dismissed items with no mention, evidence, dismissal or restore in the last `prune_after_days` (120) are removed from the state file. Open items with no mention, due date, evidence or restore in the last `prune_open_after_days` (180) are dropped too, and the digest says how many. A run never drops an item its own bundle mentions, gives evidence for or acts on. |

## Step 6: Post the digest

Post the script's output (stdout only; its stderr notes are for you) as one message, to `post_to` for scheduled runs or as the reply when someone asked. It has this shape:

1. **Header and counts**: the window, the as-of time, an **Incomplete** line when coverage was partial (meeting source unreadable, window older than the plan's note history, a meeting whose read failed, or no meeting readable), and one line of counts.
2. **Overdue**, oldest first: deliverable, owner → account, due date (and how it was inferred), days overdue, the verbatim quote with its meeting link and date, then completion evidence or "No completion evidence in …" naming the sources checked, and the item id.
3. **Due in the next N days**, then **Due later** (only shown when non-empty), then **Open, no date** (with stale flags).
4. **They owe us**: what customers or partners promised, overdue first.
5. **Done since last run**, each with its evidence link.
6. **Coverage**: window and source, any Gap since the last posted run (days older than the plan's note history are marked as unrecoverable), plan history limit, meetings scanned (external/internal, audience overrides), duplicate notes, notes and transcripts read or denied (each denied meeting named), each meeting whose read failed (with its attempt number), each meeting given up after its retries ("could not be read after N attempts — not covered") or read again later, completion sources checked, Gaps, links dropped, and the counts of soft mentions, skipped internal items, dismissed, carried-over and pruned items.

Do not trim Coverage or Gaps, and do not soften "overdue". If the script printed `Skipped: …` on a scheduled run, post nothing.

**Confirm a scheduled post (second phase).** As soon as the post succeeds, run:

```bash
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state commitment-state.json --as-of <now>
```

(the period key is the `Period key` line of the digest, and `confirm_with` in `--json`). Only this commits the run: items stop being "new", done items drop off, and a rerun of the period prints `Skipped: already posted …`. Confirming twice is harmless.

**If posting fails.** Don't confirm. The period stays *pending*, and any rerun of it stops with exit 4 instead of printing the digest again. To retry: if you're not sure whether the first post went through (a timeout, for example), look in the destination first; if it is there, just confirm. Otherwise run the same command with `--resume-pending` added, which prints the digest again (same items, still marked new), post it, then confirm. A resumed digest keeps the time it was first staged, so even with a later `--as-of` the next window still covers meetings that started after it was staged. If a pending period is never confirmed, the next scheduled period in a later week (or, for daily, a later day) supersedes it: its items reappear in that digest, Coverage says the earlier digest was never confirmed, and the old period can no longer be confirmed or run again (exit 2). A run for the same week (or day), or an earlier one, stops with exit 4 instead, so one slot never gets two digests and an older period never replaces a newer pending one. `--next-window` lists any pending period.

## Step 7: Follow-up drafts and user replies

**Drafts (only when `nudge_drafts: offer`).** The digest ends by offering drafts for overdue items. When the user says yes, write them in chat first, one per item, from `nudge_candidates` in `--json`:

- *We owe, overdue:* a short update to the counterpart. Deliver the thing if it's ready, or own the slip and give a new date the owner has confirmed. Never invent a new date or claim it was sent.
- *They owe us, overdue:* a polite nudge that restates what they offered, quoting the meeting date ("On our call on Oct 5 you mentioned…"), and makes it easy to reply.
- Keep each under 120 words, in the user's voice, with no internal notes, ids, or other accounts' details.

Create Gmail (or Outlook) **drafts only after the user explicitly approves that specific draft**, and never send. Apart from the digest itself, nothing is posted to Slack, written to Linear, Jira or the CRM, or emailed by this skill.

**Replies the user can give,** turned into `user_actions`. Apply them with an on-request run straight away (a bundle for the same window plus the actions), or include them in the next scheduled bundle. A scheduled run that prints `Skipped: already posted …` applies nothing and says how many replies it skipped; apply those with an on-request run. Once applied, the state file keeps them:

- "dismiss ct-…" or "that's not a real commitment" → `{"commitment_id": "ct-…", "action": "dismiss", "date": "<today>", "note": "<their reason>"}`. Hidden from then on and counted in Coverage.
- "bring back ct-…" → `restore`.
- "ct-… is done" → `mark_done` with their note. It's shown once as done ("marked done by you").
- "ct-… isn't actually done" about an item already reported done → `reopen` with their note. It's open again ("Reopened by you: …"), back in `--list-open`, and is reported again when it's next done.
- "ct-… is actually due the 30th" or a reply to "date unclear — confirm" → `set_due` with `due_date`. Shown as "due … (set by you …)"; it overrides inferred dates, including later re-promises, until changed again.
- "that's still open" about a likely-done item → record nothing. It stays listed with its weak evidence until strong evidence or a `mark_done` arrives.

## Guardrails

- **Never invent a commitment, an owner or a due date.** No quote, no item. Relative timing goes to the script as words; vague timing stays undated.
- **Quotes are copied verbatim by you**: the agent copies each quote; the script can't verify the wording (it doesn't check the wording; the bundle has no meeting text) and labelled with their source (transcript, your notes, or Granola's AI summary). Never present an AI-summary line as something a person said word for word.
- **Every item cites its meeting** (title, date, link). Every "done" cites its evidence link.
- **Read-only.** Granola is read-only. Completion checks only search and read. Drafts are created only after explicit approval, and nothing is ever sent.
- **No silent gaps.** Denied transcripts, unreadable notes, unavailable sources, plan history limits and incomplete participant lists all appear in Coverage.
- **A schedule posts each period once when the post is confirmed** (two-phase state file plus a lock against concurrent runs; the script can't see the destination, so it relies on you confirming each post and checking the destination before re-posting). It posts only to the destination the user configured.
- **Minimum personal data.** The state file keeps each commitment's quote, owner and counterpart names, the emails you put in the bundle (for drafts), meeting titles and links, and evidence links. It never stores transcripts or email bodies. The script doesn't check whether an address is personal, so only put business addresses in the bundle, and never phone numbers. Open items with no activity for `prune_open_after_days` are dropped. Don't paste transcript excerpts into posts beyond the quote.
- **Internal stays internal.** The digest is for your team; never paste it, item ids, or another account's commitments into a customer-facing email.
- Never choose a meeting source, internal domain or destination the user has not confirmed (Step 0). Never edit this SKILL.md or the script to get past an error.

## Automation recipe

Nothing happens until this has a trigger. In **Space settings → Automations**:

- **Weekly (recommended):** Fridays at 4pm local time, running this skill in scheduled mode with `cadence: weekly`. The digest lands before the weekend with the week's promises and what slipped.
- **Daily:** weekdays at 8am with `cadence: daily`, for teams with many calls. Each day's run scans from where the last posted window ended (`--next-window`, Step 2) through today; the state file keeps items from being re-announced, and a second run on the same day posts nothing.
- **On request:** "what did I promise people this week?", "what am I behind on?", "open follow-ups from my meetings", "who owes us what?"

New automations start disabled. Enable it. Each scheduled run follows the same steps:

1. `--next-window <cadence> --as-of <now> --state …`. For each period in `unconfirmed_incomplete` (an Incomplete digest whose confirm was lost), check the destination: if it was posted, `--confirm-posted <that key>`. If `already_posted` is set, stop: this week's (or today's) digest is already out. If `pending` is not empty, resolve it first (Step 6).
2. Build the bundle for `from`..`to` (Steps 2 to 4), plus any `reread` meeting that now reads (Step 3), and run with `--scheduled`.
3. Exit 0 with a digest: post it, then `--confirm-posted <period key>`. `Skipped: …`: post nothing. Exit 4: post nothing (Step 6). Exit 5: post the Incomplete digest once (not again if an Incomplete digest for this week or day already reached the destination), then `--confirm-posted <period key>`: that records the items it announced, so no later digest flags them new again; the period stays open and the next run covers it again.

If the automation fires twice in the same slot (a retry, or a manual run at 4:30pm after the 4pm post), the second run gets a short window from `--next-window`, but it posts nothing: it stops with exit 4 while the first digest is unconfirmed, and prints `Skipped: already posted …` once it is confirmed. `bash run.sh` shows this sequence on the sample data.

## Limits

- Extraction is done by the agent reading notes and transcripts. The script only validates, merges, resolves dates and renders, so a promise the agent didn't extract is a silent miss. Granola summaries can omit details; transcripts reduce, but don't remove, that risk.
- Completion is only as good as the searches. Work delivered in a channel nobody connected (a phone call, a customer portal, a personal inbox) looks open until the user marks it done.
- Speaker attribution in transcripts can be uncertain (`Microphone` is the recorder; shared room audio mixes voices). When it's unclear, the item is left out rather than attributed.
- Relative dates follow fixed rules (above). "Next Friday" means the Friday of the following week, which not every speaker intends; check inferred dates that matter.
- One timezone per meeting (its offset at start). Holidays come only from `commitments.json`. Dates must be strict ISO (`YYYY-MM-DD`, datetimes with an offset, ASCII digits, hours 00–23, offsets within ±14:00); compact (`20261009`), ISO-week and `T24:00` forms are rejected the same way on every Python version.
- Double-post protection is only as good as the confirm step: the script can't see Slack or the Space, so a post that went through without being confirmed can be posted again with `--resume-pending`. Check the destination first.
- The state lock is a file next to the state file. It protects runs that share that file system; a lock left by a crashed run blocks writes for up to 15 minutes. One digest per week (weekly) or per day (daily): a deliberate second digest in the same slot needs an on-request run.
- Escaping stops bundle text from forming links, autolinks, mentions, HTML or formatting. In Slack output, `*`, `_`, `~`, `` ` ``, `@` before a name, `://` and dots inside a word before a letter (as in a domain) inside values appear as look-alike characters, so a quote there is not byte-for-byte verbatim.
- Pruned items are forgotten: an on-request run over meetings older than `prune_after_days` (or `prune_open_after_days`) tracks their promises again, as new items.
- Free Granola plans expose about 30 days of notes; older meetings can't be scanned. The state file keeps items you already tracked.
- Merging needs the same owner, counterpart and deliverable wording (filler words ignored), or, for a re-extraction from the same meeting, the same quote as first extracted from it and the same owner. Differently worded repeats from other meetings need `same_as` or `known_id`.

## Verification status

**Tested offline:** `bash run.sh` matches `expected_output.txt` byte for byte, and `bash tests.sh` passes (it prints `PASS n/n`) on Python 3.8, 3.10 and 3.14. The tests cover input validation (malformed JSON, missing fields, lists set to `null`, lone surrogates, naive, compact, ISO-week, `T24:00`, out-of-range offsets and non-ASCII-digit dates, NaN, quotes from unread transcripts, evidence from unavailable sources or dated before the promise), empty input, merging (duplicate notes, repeats, `same_as`, `known_id`, repeats of done or dismissed items, two deliverables in one sentence, reworded re-extractions in the window overlap, reopen plus repeat in one bundle, state files from the previous version), relative due dates (weekdays, same-day ambiguity, business days, holidays, month and quarter ends including weekends, year rollover, vague phrases, far-future phrases, meeting-local dates), status rules (strong, weak, against, user mark-done, reopen, same-day reply order, set-due), state behaviour (the documented automation flow end to end: stage, confirm, a second run in the same week or day skipped; pending, resumed and superseded periods; no posted run yet; Granola unavailable, windows past the plan's note history, failed meeting reads and unreadable meetings reported as Incomplete and never recorded, a meeting unreadable for one run (recovers), for weeks (given up after the retry budget and listed as not covered, later digests normal, nothing flagged new twice) and readable again after giving up (re-read from an earlier window), Incomplete digests confirmed; stale scheduled runs refused; unwritable state; lock files incl. future-dated, folder and foreign locks, and concurrent runs; dry runs, new markers, done drop-off, dismissals, restore, on-request runs that don't touch reporting state, pruning, and a fuzz test that sets every field of a real state file to null, missing or a wrong type), an escaping fuzz over every rendered field in markdown and Slack (no link, autolink, raw HTML, mention, math, highlight or broken bold from bundle text, and no bare URL or domain left linkable), link validation (host names, ports), UTF-8 output, broken pipes and closed streams, owner and scope filters, soft mentions, coverage lines and JSON output. It also runs a scenario harness that follows the automation recipe above literally over many simulated weeks (daily and weekly cadences, runs late on Friday, at the weekend and at Monday 00:00, daylight-saving changes in New York and London, meetings in seven timezones, Granola outages, unreadable notes and failed `get_meetings` batches, meetings unreadable for days, weeks or for good (retried, given up, re-read), Incomplete digests posted and confirmed (or their confirm lost), stale triggers, failed posts, lost confirms, resumed digests (with the original or a later `--as-of`) and superseded ones, racing triggers, held locks, wrong and repeated confirms, on-request and `--owner` runs, replies in every order, repeats after delivery, re-extractions trimmed or reworded differently, two deliverables in one sentence, upgrades from a `state_version` 1 file, and runs long enough to prune) and checks after every step that the state file loads, every meeting is scanned by a posted digest, disclosed as a Gap or listed by name as not covered (and re-read once readable again), no period or week is posted twice, nothing is announced as new twice, no open promise is lost or tracked twice, `--list-open` matches the state, and a rejected confirm changes nothing. All sample data is fictional, and every email address uses a `.example` domain.

**Smoke-tested live (read-only), 2026-10-09, on the previous build of this version:** against a connected Granola account in Type: `get_account_info`, `list_meetings` with `last_week` (18 meetings) and with `custom` for Monday to Friday of the current week (15 meetings), `get_meetings` in two batches (10 and 5 ids), and `get_meeting_transcript` on one meeting (access allowed). A bundle built from those real response shapes (15 meetings, 7 external including one audience override, 5 commitments, 1 soft mention, real titles containing `<>`) ran the documented scheduled flow on Python 3.14 and 3.8: `--next-window`, a staged digest, a rerun stopped with exit 4, `--confirm-posted`, then at 4:30pm `--next-window` reported `already_posted` and the run printed `Skipped: already posted …`. The Slack-format digest was byte-identical on both versions, and no run printed a traceback. The real data was deleted afterwards; nothing from it is in this package. The fixes made after that run (window boundaries for resumed digests and other timezones, one digest per week or day, evidence older than a repeated promise, repeats read late, reply order, pruning by latest activity, autolink escaping) change no input format; on the bundled sample data the output differs only in escaping (`://` and dots inside words), and they were verified offline only. The QA round 5 changes (bounded retry for unreadable meetings, confirming Incomplete digests, `reread`, late reads joining the item that existed when they were said) were also tested offline only, not against live Granola. Earlier rounds also exercised `query_granola_meetings` and Gmail sent-mail searches.

**Not yet verified live:** Slack, Linear, Jira, Outlook and calendar evidence searches, posting the digest from a real automation (the confirm flow above was run locally on live-shaped data, without posting), and Gmail draft creation. Those follow each provider's documented read endpoints. Check the first few digests by hand.

## Credits

The commitment rules in Step 3, check-before-nudge in Step 4 and the digest grouping are adapted from [nickyc1/granola-action-items](https://github.com/nickyc1/granola-action-items) and [zhenkaifu/meeting-accountability-agent](https://github.com/zhenkaifu/meeting-accountability-agent), both MIT. See `NOTICE.md` and `LICENSE.txt`.
