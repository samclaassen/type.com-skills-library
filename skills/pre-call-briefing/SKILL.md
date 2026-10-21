---
name: pre-call-briefing
description: Build a one-page briefing a rep reads 5 minutes before a sales call. It pulls the CRM deal, recent email and Slack, the stakeholders on the invite, and open risks, then flags stale records, budget conflicts, unanswered questions, overdue commitments, new stakeholders and competitor landmines. Use before an external sales or customer call, when someone says "prep me for my call with [company/person]", "brief me on this meeting", or "what do I need to know before this call", or on a 15-minute schedule that briefs upcoming calls. Pairs with call-debrief.
---

## What this does

It writes a briefing that takes 2 minutes to read and surfaces everything that would change how the call is run. That includes a competitor the prospect mentioned last week, a deal amount above the stated budget, a question nobody answered, and a quote we promised and haven't sent.

Most reps prep by skimming the CRM 30 seconds before the call, if they prep at all. This skill does that skim properly, and it says plainly when the data is thin.

[`call-debrief`](https://type.com/library/skills/call-debrief) covers what happened after the call. This skill covers what to walk in knowing. Together they close the loop.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (scripts, config, sample data, `run.sh`, `expected_output.txt`) is in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `pre-call-briefing`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters), and its hash where your tools report one, with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Config

```yaml
crm: ""                        # hubspot | attio | salesforce | none — set in Step 0
calendar_source: ""            # google | outlook | manual (manual = no calendar connected) — set in Step 0
call_notes: ""                 # optional: granola | gong | fireflies | otter | fathom | none — set in Step 0
enrichment: basic              # basic | full — basic = CRM + email history; full = adds web/news/LinkedIn
lookahead_minutes: 30          # scheduled mode: brief calls starting this far ahead
match_horizon_days: 7          # name lookup: how far ahead to search the calendar for the named company/person
post_to: ""                    # Slack channel, or blank for this Space
internal_domains: []           # your company's email domains; meetings with only these are skipped
briefing_log: briefing-log.md  # scheduled runs never brief the same event twice — see Step 2
```

These are defaults. Do not edit them here. The values confirmed in Step 0 live in a small settings file, `pre-call-briefing-settings.md`, in the user's working folder or Space. That file is not part of this skill. Read it at the start of every run; any key it sets overrides the default above.

`crm` and `calendar_source` start blank on purpose. They depend on what this Space actually has connected, so Step 0 sets them with the user. Never assume a CRM or a calendar.

`internal_domains` must hold at least one domain. Without one, the script refuses to run, because it could not tell an internal standup from a customer call.

Thresholds such as `stale_days` (14), the Slack window (7 days), email threads (5) and flag priority live in `briefing.json`. `DATA_CONTRACT.md` documents every key.

## Step 0: First run, confirm connections

Run this step when `pre-call-briefing-settings.md` is missing, when `crm` or `calendar_source` is blank in it, or when the configured CRM or calendar is no longer connected.

1. **Detect.** Find which calendars (Google Calendar, Outlook) and which CRMs (HubSpot, Attio, Salesforce) are actually connected in this Space. Connected means you can make one real read call, such as listing one event in the next day or reading one deal. A tool that appears in a list but fails on a read does not count. Note whether Gmail or Outlook mail and Slack can also be read, whether a call recorder or note taker (Granola, Gong, Fireflies, Otter, Fathom) can be read for past-call notes and transcripts, and whether web search is available for `enrichment: full`.
   - **In Type, also check workspace connections that exist but are not added to this Space** (for example Attio, Slack or Granola connected for the workspace but not attached here). Never silently treat them as absent. Name each one and offer to add it to this Space; only use it once it has been added and a read call succeeds.
2. **Propose.** Tell the user what you found and the values you would set. For example: "I found Google Calendar and Attio connected. Slack and Granola are connected in your workspace but not added to this Space; want me to add them? Use `calendar_source: google` and `crm: attio`? Your internal domain looks like `ledgerline.example`, is that right?"
   - More than one CRM connected: ask which one to use.
   - No CRM connected: propose `crm: none`. Briefings will then say no CRM is configured.
   - No calendar connected: propose `calendar_source: manual`. With no calendar, each run needs the user to give the company, the call time, and the external attendees' emails or the company's email domain (Step 2).
   - No call recorder: `call_notes: none`. It is optional.
3. **Wait for the user to confirm.** Never pick HubSpot, or any CRM, silently or because it comes first in a list.
4. **Save.** Write the confirmed values, including `internal_domains`, to `pre-call-briefing-settings.md` in the user's working folder or Space, as a short YAML block using the keys from Config. Never edit this SKILL.md: it is a published skill, and editing it breaks the file-hash check in "Before you run". If you cannot write files there, give the user the exact lines to save.

If an automation triggered the run and nobody is there to confirm, post one message to `post_to` asking for these values, then exit without briefing. This setup question is the only message this skill posts when it is not delivering a briefing.

## Step 1: Preflight

This skill only reads, so no write scope is needed anywhere. It never writes to the CRM.

1. Calendar reachable. For `calendar_source: manual`, the user gave the company, the call time, and the external attendees' emails or the company's email domain. If any is missing, ask for it before going further.
2. CRM reachable with read access. If a read fails, continue with `sources.crm.status: unavailable`. The briefing then states that no deal data was read.
3. Email, Slack and, if configured, the call recorder (`call_notes`): try them. Mark failures as `unavailable` rather than dropping them silently.
4. `briefing_log` readable. If it does not exist yet, it is created on the first briefing. If it exists but cannot be read, stop, because running blind means briefing calls twice.

## Step 2: Find the call

There are three ways in. Keep the terms separate: **name lookup** searches a connected calendar for the call the user named, while **`calendar_source: manual`** means no calendar is connected at all.

- **Scheduled** (automation, calendar connected). List events from now until now + `lookahead_minutes`.
  - Google: `GET /calendar/v3/calendars/primary/events?timeMin=<now>&timeMax=<now+lookahead>&singleEvents=true&orderBy=startTime`.
  - Outlook: `GET /me/calendarView?startDateTime=…&endDateTime=…`.
  - Take each event that has at least one attendee outside `internal_domains`. If nothing is in the window, exit silently.
- **Name lookup** (a person asked, calendar connected). Search events from now until now + `match_horizon_days` (default 7 days) with the same calendar calls. An event matches when an attendee's email domain equals the company's domain, an attendee's name matches the person named, or the title contains the company name.
  - One match: brief it.
  - More than one: list them with date and time and ask which.
  - **No match:** tell the user that nothing in the next `match_horizon_days` days matched, and say what you searched for. Ask for the call time and the external attendees' emails or the company's email domain. Then continue as a `calendar_source: manual` run for that call. Never invent attendees, and never brief a different call because it looked close.
- **`calendar_source: manual`** (no calendar connected, or name lookup found nothing). The user must give the company, the call time, and either the external attendees' emails or the company's email domain. If they gave only the company and time, ask for the emails or domain before building anything. Put the emails in `meeting.attendees` or the domain in `meeting.external_domains` (see `DATA_CONTRACT.md`). If the script exits 2 with `no external attendee information supplied`, ask the user; never fill the gap with CRM contacts or guessed addresses.
  - **Naming.** Set `company.name` to the company name as the user or the CRM gives it, and `company.domain` to its email domain whenever you know it (from the user, the CRM, or the attendees' emails). Leave `meeting.title` and `meeting.event_id` out: the script titles the call `Call with <Company>` and derives the event id itself, so two runs about the same call always get the same key. The id is `<company domain>-<YYYY-MM-DD>` when a domain is known (most stable), otherwise `<company-slug>-<YYYY-MM-DD>`, where the slug is the company name lowercased, legal suffixes such as inc, llc, ltd, gmbh, co and corp dropped from the end, and every run of other characters turned into one `-`. "Northpeak Outfitters" and "Northpeak Outfitters Inc." both become `northpeak-outfitters`. The date is the call's local date as the user gave it.
  - The briefing says `Not on your calendar — time supplied by you.` under its heading, so the rep knows the time was not read from a calendar.

Skip, and exit silently, when:
- A calendar event's attendees are all on `internal_domains` addresses. The script also enforces this and prints a one-line skip notice. This is different from a manual run with no external attendee information, which is an error the user must resolve (Step 2).
- The event is cancelled, or (scheduled) it starts outside the lookahead window.
- (Scheduled runs only) It is already briefed. Its event key `<calendar_source>:<event id>@<start>` is in `briefing_log`. For manual runs the script derives the event id (see Naming above) and adds the `manual:` prefix once. Recurring meetings have one id per instance. A moved meeting has a new start, so it gets a new briefing.

**A person asking again gets the briefing again.** The log skip exists so a 15-minute schedule doesn't post the same call twice. When a person asks for a call that is already in the log, run without `--scheduled` (as every person-asked run is): the script prints the briefing and does not add a second log row. Use `--dry-run` when you want to show a briefing without logging it at all.

**Silence is the correct output here.** A briefing bot that posts about the internal standup gets muted within a week. Silence means no message to `post_to`. When a person ran the skill, you may tell them why it skipped.

## Step 3: Gather and normalise

Build one bundle as specified in `DATA_CONTRACT.md`, which lists the exact fields per integration.

1. **Meeting and attendees** from the calendar event: id, title, start with its offset, and attendees with response status.
2. **CRM record.** Match the deal by company domain, then by attendee email. Read stage, amount, close date, last activity, owner, open tasks, recent notes, and contacts with title, role and prior meetings and emails.
   - HubSpot: `POST /crm/v3/objects/deals/search` plus associations, notes and tasks.
   - Attio: `POST /v2/objects/deals/records/query`, `GET /v2/notes`, `GET /v2/tasks`.
   - Salesforce: `Opportunity`, `OpportunityContactRole`, `Task` and `Event` via SOQL.
   - If nothing matches, set `record_found: false`. An unmatched external call is itself worth flagging.
3. **Recent comms.**
   - Gmail: search `from:<domain> OR to:<domain> newer_than:30d`, last 5 threads.
   - Slack: `search.messages` for the company name or domain over the last 7 days.
   - Call notes and transcripts (optional, `call_notes` set): Granola or another recorder, for past calls with this company over the last 90 days. List each past call found in `call_notes.calls` (date and title). Use them for prior-call context and for verbatim budget, timeline, competitor, question and commitment quotes, sourced as `call_transcript` or `call_note`. Record the source in `sources.call_notes`.
4. **Extract signals** from CRM notes, call notes and transcripts, emails and Slack. Quote verbatim and record the source and date of each one:
   - budget statements
   - timeline statements, with `stated_date: null` when it's vague, like "before Black Friday"
   - competitor mentions
   - questions the prospect asked and whether we answered
   - commitments on both sides with due dates and done status

   No quote, no signal.
5. **Company context** (only `enrichment: full`): recent news, funding, job postings and tech-stack signals, each with a URL and date. If web search fails, set the enrichment status to `unavailable`. Do not fill the gap from memory.

## Step 4: Build the briefing

Save the bundle as a file in the working folder and run:

```bash
python3 build_briefing.py \
  --bundle bundle.json \
  --config briefing.json \
  --as-of 2026-10-06T09:35:00-07:00 \
  --internal-domain ledgerline.example \
  --enrichment basic \
  --scheduled --lookahead-minutes 30 \
  --log briefing-log.md
```

- `--as-of` is required and is the only "now", as an ISO datetime with an offset. Use the run time.
- Pass `--internal-domain` once per entry in `internal_domains`.
- `--scheduled` is for automation runs only. It skips events outside the lookahead window and events already in `--log`. Omit it whenever a person asked (name lookup or `calendar_source: manual`); then the briefing is always printed, even for a call that was already briefed.
- `--log` appends the event after briefing, once per event key; a rerun does not add a second row. `--dry-run` never appends, which is useful for previews. `--json` emits flags, agenda and markdown as structured JSON.
- Exit 0 prints a briefing or a one-line `Skipped: …`. Exit 2 means bad input, and the message names the file and JSON path. Fix the bundle and rerun; never edit the script to get past an error. Exit 2 with `no external attendee information supplied` means you must ask the user for the attendees' emails or the company domain.

What the script flags, deterministically:

| Flag | Rule |
| --- | --- |
| No history found | No CRM record (or `crm: none`) and nothing in the other sources read: 0 email threads, 0 Slack mentions, 0 past calls or call notes, no contact touchpoints, no signals. Highest priority. The TL;DR then says plainly, for example, "No history found anywhere (no CRM record, 0 emails, 0 Slack mentions, 0 call notes) — treat as a cold first call; confirm who's joining and why." If a source could not be read it says "in the sources read" and names it, instead of "anywhere". |
| Stale record | Days since CRM last activity ≥ `stale_days` (14). At ≥ `outdated_days` (60) the briefing says the record looks outdated. |
| Budget conflict | A captured budget statement is below the CRM amount, compared after annualising. |
| Close date | It has passed, it is missing, the prospect's stated timeline is vague, or they stated a different date. |
| Unanswered question | A prospect question with `answered: false`. |
| Overdue commitment | Ours, due before today, not done. CRM open tasks count as ours. |
| New stakeholder | External attendee with no prior meetings or emails. ⚠️ |
| Signer first meeting | Signer or decision maker with no prior meetings. ⚠️ |
| Competitor | Every captured mention, shown with its quote and source. |

The TL;DR comes from the stage plus the top two flags (or, for a call with no history, the no-history sentence alone). The agenda takes one item per flag, in `flag_priority` order, capped at 5. When Open threads or Risks has nothing to show, its line names only the sources that were actually read (for example `None found in email (0 threads), Slack (last 7 days), call notes (Granola).`), never a source that was unavailable or not configured.

## Step 5: Read and post

Post the briefing to `post_to` as one message, at least `lookahead_minutes` before the call. It follows this shape:

1. **TL;DR**: where the deal stands and what this call needs to accomplish. Manual runs carry `Not on your calendar — time supplied by you.` just above it.
2. **The deal**: stage, amount, close date and days since activity, with a Flag column.
3. **Who's on the call**: role, history, and ⚠️ on anyone new.
4. **Open threads**: unanswered questions, commitments, recent Slack.
5. **Risks & landmines**: budget conflict, competitors, signer in the room for the first time.
6. **Company context**, or a plain statement that it is not available.
7. **Suggested agenda**.
8. **Sources / Gaps**: what was read and what was unavailable.

Do not trim the Gaps line. A rep who knows Slack was unreachable reads the briefing differently. Do not rewrite flags into softer language. If the script printed `Skipped: …` during a scheduled run, post nothing.

## Guardrails

- Never fabricate company intel. If enrichment sources are unavailable, say so. Don't guess.
- Never expose internal notes (for example "rep flagged this deal as at-risk") in anything customer-facing. The briefing is internal prep. Never paste it, or quotes from it, into an email, deck, proposal or shared doc that a customer will see.
- If the CRM record is empty or clearly outdated, say that plainly. A briefing that papers over gaps is worse than no briefing.
- Do not generate a briefing for internal-only meetings.
- A schedule never briefs the same event twice: scheduled runs check `briefing_log` first (Step 2). A person who asks again gets the briefing again.
- Never choose a CRM or calendar the user has not confirmed (Step 0).
- Never invent attendees. If no external attendee email or company domain is known, ask (Step 2).
- Never surface a budget, timeline or competitor signal without its verbatim quote and source.
- Read-only. This skill never writes to the CRM or contacts anyone.

## Automation recipe

Nothing happens until this has a trigger. In **Space settings → Automations**:

- **Best:** Schedule, every 15 minutes, running this skill in scheduled mode. With `lookahead_minutes: 30`, each call is inside the window on two runs, and the log makes sure only the first one posts.
- **On request:** the user says "prep me for my call with [company/person]". With a calendar connected this is a name lookup over the next `match_horizon_days` days.

New automations start disabled. Enable it.

With `calendar_source: manual` there is nothing to schedule. Run the skill by giving the company, the call time, and the attendees' emails or the company domain.

## Limits

- Signal extraction (budget, timeline, competitor, question, commitment) is done by the agent reading notes and emails. The script only applies rules to what was extracted, so an unextracted signal is a silent miss.
- "Answered" relies on the agent checking for a later reply from us. Questions asked on a call that nobody logged are invisible.
- Prior-touchpoint counts are only as good as the CRM's activity logging. When the last email is newer than the CRM's last activity, the briefing notes that activity isn't being logged.
- Single currency. Budgets are compared only when both sides are recurring (`yr` or `mo`).
- One meeting per bundle. Back-to-back calls get separate runs.

## Verification status

**Tested offline:** the rules, rendering, internal-only skip, manual runs with no attendee information (exit 2) or only a company domain, the derived manual event key (domain-based, or a name slug where "Northpeak Outfitters" and "Northpeak Outfitters Inc." match) with a single `manual:` prefix, the "not on your calendar" line, the no-history flag and its source-aware wording, empty-state lines naming only the sources read, lookahead window, duplicate log (scheduled runs skip; a person asking again is re-briefed without a second log row), missing, empty or outdated CRM records, `crm: none` ("not configured") versus an unreachable CRM, an optional call-notes source, enrichment unavailable and enrichment cited, and input validation. `bash run.sh` matches `expected_output.txt` byte for byte, and `bash tests.sh` passes. Both use fictional sample data only.

**Unverified against live systems:** the calendar name lookup, the CRM, Gmail, Slack and call-recorder (Granola etc.) reads in Step 3 and `DATA_CONTRACT.md`, the agent's signal extraction, and the 15-minute automation. These follow each provider's documented endpoints but have not been run against real accounts. Check the first few briefings against the CRM by hand.
