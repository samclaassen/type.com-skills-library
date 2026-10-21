# NOTICE

`commitment-tracker` is an adapted work. Type (type.com) built it to track
meeting commitments across many Granola meetings and to check them against
other tools. Its extraction rules and digest layout are adapted from the two
MIT-licensed open-source projects below. The full license texts are in
`LICENSE.txt`.

## Sources

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `SKILL.md` (sections "Hallucination prevention", "Nudge logic", "Completion tracking"), `references/hallucination-prevention.md` (sections "What an action item IS", "What an action item is NOT", "When you cannot grep the transcript", "The single most important rule"), `templates/daily-briefing.md`, `templates/weekly-review.md` | [nickyc1/granola-action-items](https://github.com/nickyc1/granola-action-items) @ `2b09bb51658e9483b71a74660c0ff25101c052dc` | Nick Christensen | MIT, Copyright (c) 2026 Nick Christensen |
| `src/agents/prompts/extract.txt`, `src/agents/prompts/track.txt` | [zhenkaifu/meeting-accountability-agent](https://github.com/zhenkaifu/meeting-accountability-agent) @ `91bd94afdd3751e536b5d9ae6095f1d213bcda01` | Zhenkai Fu | MIT, Copyright (c) 2026 ZHENKAI FU |

Both commits were the upstream `HEAD` when checked on 2026-10-08. No code was
copied from either project. Their scripts read Granola's local cache file or
run on AWS, Slack and Streamlit, and none of that applies here.

## What was adapted, and how it changed (statement of changes)

From **nickyc1/granola-action-items**:
- The "is / is not an action item" heuristics ("I'll…", "Let me…", "Can you…
  by [date]?" plus an acknowledgement, versus "We should look into…", "Maybe
  we could…", "That would be cool", "Someone needs to…", "Let's… sometime")
  became Step 3 items 5 and 6 of `SKILL.md`. We added team promises ("We'll
  get you…") and the they-owe-us direction, and dropped the
  keyword-plus-commitment-phrase grep check: the agent copies each quote;
  the script can't verify the wording (the bundle has no meeting text). It
  only requires a quote and checks its source was marked read.
- "If the verifier is unsure, drop the item" (precision over recall) is kept
  as "If you're unsure, leave it out". The optional `soft_mention` kind was
  added so vague intentions can be listed separately without being tracked.
- The warning for working from Granola's auto-summary without the transcript
  became the `quote_source: summary` label ("Granola AI summary, not verbatim
  speech") and the denied-transcript line in Coverage.
- The daily briefing groups (due today/overdue, this week, open with no
  deadline, waiting on others, stale) and the weekly review's "completed this
  week" became the digest order: Overdue, Due in the next N days, Due later,
  Open with no date, They owe us, Done since last run. The 3-day stale rule
  became a configurable 21 days. Emoji headings were dropped.
- "Completion tracking" (scan later transcripts for signs an item was done)
  became one evidence source among several (sent and received mail, Slack,
  Linear or Jira, calendar, later meetings). We added strong, weak and against
  evidence strengths, and a "likely done" status that needs a stated reason.
- Not carried over: the local cache and SSH fetch, the Slack people map,
  `action-items.json`, the 30-minute meeting monitor, and the post-hoc
  correction protocol. This skill never posts automatically except its own
  digest to a configured destination.

From **zhenkaifu/meeting-accountability-agent**:
- From `extract.txt`: every item carries a verbatim quote; vague deadlines get
  no date and are never guessed; no clear owner means no item; status reports
  on finished work are not commitments; the meeting date anchors relative
  dates. These became Step 3 items 7 and 8. Resolving the date moved from the
  model into the script (`resolve_due`), which adds business days, holidays,
  the meeting's local timezone, and a "date unclear" result for a weekday
  said on that same weekday.
- From `track.txt`: check external systems before nudging, and don't bother a
  person when a tool already shows the work is done. This became Step 4, done
  before any nudge is offered in Step 7, which happen only when overdue and
  not likely done. Its tiered gentle, firm and escalate-to-manager DMs were
  replaced by drafts the user must approve, which are never sent automatically.

New work by Type, not from upstream: `build_commitments.py` (validation,
stable ids, cross-meeting and cross-run merging, relative-date resolution,
status rules, state file, idempotent scheduled periods, rendering),
`commitments.json`, `DATA_CONTRACT.md`, the Granola access rules in
`SKILL.md` (list_meetings and get_meetings first, transcripts for quotes,
query_granola_meetings as a recall net, denied access recorded, plan history
limits, duplicate notes, incomplete participant lists, Microphone and System
audio attribution), the sample bundles, `run.sh`, `expected_output.txt`,
`tests.sh`, `README.md` and this file.

### Changes after review (2026-10-09)

All new work by Type; nothing further was taken from upstream:
- Scheduled runs are two-phase: the digest is staged as pending and only
  `--confirm-posted` marks its items reported. Pending periods stop with exit
  4 unless `--resume-pending`; an exclusive lock file guards the state; the
  state is validated and saved before anything is printed; a period is never
  recorded when the meeting source was not read, and an incomplete scan is
  never reported as "nothing to report".
- Repeats merge only into open items; commitment ids include the deliverable
  (older state files are read and upgraded); on-request and `--owner` runs no
  longer change reporting state; `--next-window` starts each scheduled window
  where the last posted one ended; `reopen` action; open items are pruned by
  age; weekend "end of month/quarter" and year-inferred dates fixed.
- Every rendered value is escaped for markdown and the new `--format slack`;
  links must be absolute http(s) URLs; dates are strict ISO on every Python.
- Sample company names replaced with invented ones (Brackenvole Analytics,
  Fennaroot Dental, Spokewren Cycles, Oatmerle Foods).

### Changes after the second review (2026-10-09)

All new work by Type; nothing further was taken from upstream:
- A scheduled cadence posts at most one digest per slot (Monday–Sunday week,
  or day): a second run in the same slot prints `Skipped: already posted`,
  and `--next-window` reports `already_posted`. With no posted run yet the
  window starts at the earliest unposted attempt, with a Gap line otherwise.
- Partial coverage (window older than the plan's note history, or no meeting
  readable) is Incomplete like an unreadable meeting source: exit 5 on a
  schedule, never "nothing to report", never confirmable.
- Far-future relative dates give no date; a re-extraction with the same
  meeting, quote and owner joins its item; reopens are applied before
  merging; "still waiting" evidence fully reopens; same-day replies are
  ordered; stricter datetimes, URLs (host name, port), Slack escaping of
  `* _ ~ `` ` ``, UTF-8 output, broken pipes, lone surrogates, null lists
  and lock edge cases.
- Two sample names that matched existing businesses or products were
  replaced with newly coined ones.

### Changes before the fourth review (2026-10-12)

All new work by Type; nothing further was taken from upstream:
- Scheduled windows: the next window starts on the last day of the last
  posted window (or the UTC date 14 hours before that run, if earlier), and
  a resumed digest keeps the time it was first staged; a scheduled window
  can't end after the run; an older period can't supersede a newer pending
  digest; a skipped run says how many replies it didn't apply.
- Merging and status: evidence older than a later repeat of the promise
  doesn't count; a repeat said before an item closed joins it; a
  re-extraction joins the item that already cites its meeting; replies apply
  in bundle order.
- Pruning counts an item's latest mention, evidence, dismissal or restore,
  and never drops an item the run's own bundle refers to.
- Escaping also breaks bare URLs, `www.` names and domains in markdown and
  Slack, and `@` before a name is a look-alike in Slack.

## Also checked, not used

- [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` (Apache-2.0): `productivity/skills/update`, `sales/skills/end-of-day`, `sales/skills/call-summary`. These have an ours/theirs commitment ledger but no cross-run tracking or completion checks. No text was copied.
- [anthropics/skills](https://github.com/anthropics/skills) @ `683bc88e56f3e09ba94f7055977f3d3aa499f202`: no commitment or follow-up skill.
- [OneWave-AI/claude-skills](https://github.com/OneWave-AI/claude-skills) @ `fc5b7851a6c7ba367e05797df46ad3679df355dd` (MIT) `meeting-to-tasks`: not used, because it turns vague deadlines into dates, which this skill forbids.
- [milock/meetings](https://github.com/milock/meetings) @ `97450359b1946a7d2de13f23437267738264daaf` (MIT): a meeting recap and prep lifecycle, not a commitment tracker. Not used.
- cathrynlavery/granola-skill and the alphaparkinc "genpark" meeting action-item skills: no license, so not used. The Granola GitHub organisation: no public skills.
