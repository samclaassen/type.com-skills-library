---
name: call-debrief
description: |-
  Turn a sales or customer call recording into a structured debrief, an updated CRM record, and a drafted follow-up. Use after a call, when a transcript or meeting recording lands, when someone asks for a recap, follow-up email, next steps, or deal risks from a call, or when a CRM record needs updating from a conversation. Works with Granola, Fathom, or any transcript source, and writes to HubSpot, Attio, or Salesforce.
---

## What this does

Turns a call into three finished things: a debrief people can read, a CRM record that matches reality, and a follow-up ready to send.

Reps spend about 60% of their time on non-selling work, and most of that is this. The job is done when the CRM is updated and the follow-up is drafted — not when a summary exists.

## Config

```yaml
transcript_source: ""          # granola | fathom | gong | fireflies | manual — set in Step 0
crm: ""                        # hubspot | attio | salesforce | none — set in Step 0
post_to: ""                    # Slack channel or blank for this Space
write_mode: confirm_first      # confirm_first | autonomous
follow_up: draft               # draft | send | none
min_duration_minutes: 5        # ignore shorter calls
internal_domains: []           # skip calls with only these domains
debrief_log: debrief-log.md    # duplicate check when crm: none or the CRM is read-only — see Step 2
```

Defaults are deliberate. `write_mode: confirm_first` and `follow_up: draft` mean nothing reaches a customer or overwrites a CRM field without a human agreeing once.

`transcript_source` and `crm` start blank on purpose. They depend on what this Space actually has connected, so Step 0 sets them with the user. Never assume a CRM or a transcript source.

**`transcript_source: manual`** means a person supplies the transcript when they run the skill. It can be a file attached to the message that invokes the skill, or text pasted into that message. Accepted formats are plain text, Markdown, `.vtt` and `.srt`. For any other format, extract the text first. `.vtt` and `.srt` timestamps give the call's duration. If a transcript has no timestamps, the duration is unknown, so do not skip the call on `min_duration_minutes`. Manual runs have no automation trigger. They happen only when someone attaches or pastes a transcript and asks for a debrief.

## Step 0 — First run: confirm connections

Run this step whenever `transcript_source` or `crm` is blank, and whenever the configured CRM or transcript source is no longer connected.

1. **Detect.** Find which transcript integrations (Granola, Fathom, Gong, Fireflies) and which CRMs (HubSpot, Attio, Salesforce) are actually connected in this Space. Connected means you can make one read call, such as listing one recent transcript or reading one record. A tool that appears in a list but fails on a read does not count.
2. **Propose.** Tell the user what you found and the values you would set, for example: "I found Fathom and Attio connected. Use `transcript_source: fathom` and `crm: attio`?"
   - More than one CRM connected: ask which one to use.
   - No CRM connected: propose `crm: none`.
   - No transcript integration connected: propose `transcript_source: manual`.
3. **Wait for the user to confirm.** Never pick HubSpot, or any CRM, silently or because it comes first in a list.
4. **Save.** Write the confirmed values into the Config block above. If you cannot edit this skill, give the user the exact two lines to change.

If an automation triggered the run and nobody is there to confirm, post one message to `post_to` asking for the two values, then exit without debriefing. This setup question is the only message this skill posts when it is not delivering a debrief.

## Step 1 — Preflight

1. Transcript source reachable. For `manual`, that means a transcript is attached to or pasted into the invoking message.
2. CRM reachable, **with write scope**. A connected CRM is not necessarily a writable one, so check before you start, not after you've drafted. **Never test write scope with a write.** A test write is a real change to someone's CRM. Check by reading permissions instead:
   - **Attio.** Call `GET https://api.attio.com/v2/self`. The `scope` string must include `record_permission:read-write` to update records and `note:read-write` to add the debrief note. `{"active": false}` means the token is revoked or invalid.
   - **Salesforce.** Call `GET /services/data/vXX.X/sobjects/Opportunity/describe`. The object must be `updateable: true`, and every field you plan to set (for example `StageName`, `NextStep`, `CloseDate`) must be `updateable: true`. The object that will hold the debrief note must be `createable: true`. For a specific record, query `SELECT RecordId, HasEditAccess FROM UserRecordAccess WHERE UserId = '<user id>' AND RecordId = '<record id>'`.
   - **HubSpot.** HubSpot's token-metadata endpoints take the token itself as input, and OAuth apps also need client credentials, so a managed connection usually cannot call them. If you can call one, check that the returned `scopes` include write scopes for every object you will update (for example `crm.objects.deals.write` and `crm.objects.contacts.write`). Otherwise ask the user to confirm the connection can write deals, contacts and notes.
   - If none of these checks is possible, ask the user.

   Until write scope is confirmed, treat the CRM as **read-only**.
3. Destination for the debrief exists.

If the CRM is read-only, still produce the debrief and follow-up, and say plainly which CRM fields you would have set. Do not silently drop the update.

## Step 2 — Decide if this call counts

Skip and exit silently when:
- Shorter than `min_duration_minutes`
- Every attendee is on an `internal_domains` address
- No transcript attached
- Already debriefed (see below)

### Call ID and the duplicate check

A call ID is `<transcript_source>:<id>`, built from the source's own identifier:

| Source | ID to use | Example |
|---|---|---|
| Granola | the note `id` from the Granola API | `granola:not_1d3tmYTlCICgjy` |
| Fathom | the meeting's `recording_id` | `fathom:123456789` |
| Gong | the call `id`, the same number as `id=` in the Gong call URL | `gong:7782342274025937895` |
| Fireflies | the transcript `id` | `fireflies:<transcript id>` |
| Manual | the first 16 hex characters of the SHA-256 of the transcript file's bytes, or of the pasted text with leading and trailing whitespace trimmed | `manual:3f9a0c1d2b4e5f60` |

Put the call ID on its own line in every debrief, as `Call ID: <call id>`, so it can be searched for exactly.

To decide whether a call is already debriefed:
- **Writable CRM.** Search the linked record's notes for `Call ID: <call id>`.
- **`crm: none`, or a CRM that is read-only.** Read `debrief_log`. After each debrief is posted, append a row: `| call_id | call date | company | debriefed_at (UTC) | crm_status |`, where `crm_status` is `none`, `read-only (proposed only)`, or `written`. If the file does not exist yet, create it on the first debrief. If it exists but cannot be read, stop. Running blind means debriefing calls twice.
- **Manual transcripts.** An edited or re-exported transcript produces a new hash. If a manual transcript's date and attendees match an existing log row or CRM debrief note, tell the person who ran the skill and ask before debriefing again.

**Silence is the correct output here.** A debrief bot that posts about the internal standup gets muted within a week.

Silence means no message addressed to `post_to`. When a run is triggered interactively by a person rather than by an automation, skip reasoning may be returned to that person in the reply — that is not a post.

## Step 3 — Read the call

Pull the transcript plus attendees, duration, and the linked CRM record. If no CRM record matches, note that — an unmatched call is itself worth flagging, because it usually means the deal is not in the pipeline.

## Step 4 — Write the debrief

Structure, in this order:

1. **Outcome** — one line. What actually happened, not what was discussed.
2. **Next step** — who does what, by when. If the call ended without one, say so explicitly. That absence is the single most useful signal in the whole debrief.
3. **What they said** — direct quotes for anything about budget, timeline, authority, or competitors. Quote, do not paraphrase; paraphrasing is where deals get misread.
4. **Risks** — only if evidenced in the transcript. No speculation.
5. **Open questions** — what the rep still does not know.

Rules:
- Never invent a commitment nobody made.
- Never soften a bad call. "They were lukewarm" is more useful than "positive discussion."
- Keep it under 200 words, counting the debrief body only — not the CRM diff or the follow-up draft. Long debriefs go unread.

## Step 5 — Update the CRM

Set only fields the call actually evidences:
- Stage, when the call clearly moved it
- Next step and date
- Close date, only if they stated one
- Competitor mentions
- A debrief note containing the line `Call ID: <call id>` (Step 2)

In `confirm_first` mode, post the proposed changes as a diff — current value → new value — and apply on approval. After the first approval, later calls in the same Space may run autonomously if the user opts in.

**Never overwrite a human-entered field with a lower-confidence value.** If the rep set a close date and the call implies a different one, flag the conflict instead of resolving it.

## Step 6 — Draft the follow-up

Reference two specifics from the call so it is obviously not a template. Restate the next step and owner. Keep it shorter than the recipient expects. Match the Space's brand voice file if one exists.

If the customer asked for a specific artifact or material on the call, the draft either contains it or states when it will arrive and who owns it. An unanswered ask is the thing they notice.

Draft only, unless `follow_up: send`.

## Step 7 — Post

Debrief to `post_to`, with the CRM diff and the follow-up draft attached. One message, not three.

## Guardrails

- Never send to a customer in `draft` mode.
- Never write to the CRM without write scope confirmed.
- Never debrief the same call twice. Check the call ID against the CRM or `debrief_log` first (Step 2).
- Never choose a CRM or transcript source the user has not confirmed (Step 0).
- Never report a risk the transcript does not support.
- Exit silently on internal or short calls.

## Automation recipe

Nothing happens until this has a trigger. In **Space settings → Automations**:

- **Best:** App event — new transcript available from the recorder.
- **Simplest:** Schedule, hourly, checking for transcripts since the last run.

New automations start disabled. Enable it.

With `transcript_source: manual` there is nothing to trigger on. Run the skill by attaching or pasting a transcript.

## Verification status

Detection, transcript reading, and debrief drafting are testable with the connections most teams already have. **CRM writes and follow-up sending are unverified** until run against a real CRM with write scope.
