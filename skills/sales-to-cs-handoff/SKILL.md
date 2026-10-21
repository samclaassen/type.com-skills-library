---
name: sales-to-cs-handoff
description: Write the onboarding handoff a CSM needs on day one from an account's sales meetings in Granola, plus the CRM deal and email. It covers why they bought in their words, success criteria and milestones, and the promises found in those meetings, each with a verbatim quote and meeting link, flagging terms that changed between meetings, non-standard or roadmap items, and anything not in the contract. It adds a stakeholder map that flags people who appeared once or went quiet, open objections, a don't-ask-again list and a coverage check. Use when a deal closes, when someone says "write the handoff for [account]", "what did sales promise [account]?" or "onboarding brief for [account]", or on a closed-won trigger or daily schedule. Drafts a CRM note and a Slack post but writes neither without approval. Pairs with call-debrief and pre-call-briefing.
---

## What this does

When a deal closes, the CSM inherits a relationship they weren't part of. What the customer was told is spread across five or ten calls, two reps' notes and an email thread. This skill reads those meetings, the deal and the email, and writes one handoff document:

- **Why they bought**: the pains and the trigger event, quoted in the customer's words, each linked to the meeting it came from.
- **Success criteria and milestones**: what they will measure, the targets they named, and the dates they were given.
- **What sales promised**: the features, integrations, custom work, pricing and discounts, SLAs, support, training, timelines and roadmap items found in the meetings and email, each with a verbatim quote and its source (the meeting's note link, or the email). It flags terms that changed between meetings, non-standard terms, roadmap items, and anything that is not in the contract.
- **Stakeholder map**: champion, economic buyer or signer, day-to-day contacts, technical owner and detractors, with the first and last meeting each person was in. It flags people who appeared once, went quiet, or never came to a meeting.
- **Technical requirements**, unresolved objections, risks and the competitors they evaluated.
- **Don't ask them again**: questions the customer already answered. **Open questions**: what nobody knows yet.
- **Coverage**: which meetings were read, the date range, transcript access, what was excluded or merged, and the gaps.

Granola holds each meeting's notes. This skill works across them: it merges the same meeting captured by two teammates, catches the discount that was 15% on one call and 20% on the next, and checks promises against the CRM deal and the contract.

It pairs with [`pre-call-briefing`](https://type.com/library/skills/pre-call-briefing), which covers what to know before a call, and [`call-debrief`](https://type.com/library/skills/call-debrief), which covers one call after it ends. This skill covers the whole sales cycle at the moment the account changes hands.

## Before you run

This skill ships a script and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (`build_handoff.py`, `handoff.json`, `DATA_CONTRACT.md`, `sample_bundle_quillfen.json`, `sample_handoff_log.md`, `run.sh`, `expected_output.txt`, `tests.sh`) is in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `sales-to-cs-handoff`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters), and its hash where your tools report one, with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Config

```yaml
meeting_notes: ""              # granola | gong | fathom | fireflies | otter | zoom | upload — set in Step 0
crm: ""                        # hubspot | attio | salesforce | none — set in Step 0
email: ""                      # gmail | outlook | none — set in Step 0
internal_domains: []           # your company's email domains; meetings with only these attendees are excluded
deliver_to: type_doc           # type_doc (a doc in this Space) | markdown (a .md file)
post_to: ""                    # CS Slack channel for the announcement draft; blank = no Slack draft
crm_note: draft                # draft | off. A CRM note is only ever written after the user approves it
lookback_days: 180             # how far back to search when the CRM gives no deal-created date
handoff_log: handoff-log.md    # scheduled runs record each handoff; a confirmed one is never handed off again
```

These are defaults. Do not edit them here. The values confirmed in Step 0 live in a small settings file, `sales-to-cs-handoff-settings.md`, in the user's working folder or Space. That file is not part of this skill. Read it at the start of every run; any key it sets overrides the default above.

`meeting_notes`, `crm` and `email` start blank on purpose. They depend on what this Space actually has connected, so Step 0 sets them with the user. Never assume a CRM.

`internal_domains` must hold at least one domain. Without one, the script refuses to run, because it could not tell an internal deal review from a customer call.

Thresholds (when someone counts as "went quiet", how soon a milestone is "due soon", the free-plan window) and the labels live in `handoff.json`. `DATA_CONTRACT.md` documents every key.

## Step 0: First run, confirm connections

Run this step when `sales-to-cs-handoff-settings.md` is missing, when `meeting_notes` or `crm` is blank in it, or when a configured source is no longer connected.

1. **Detect.** Find what is actually connected. Connected means one real read call succeeds; a tool that is listed but fails its read does not count.
   - **Granola**: call `get_account_info`. It returns the connected account's email, active workspace, plan (`mcp_plan`) and note-access scopes (`personal`, `public`). Note all three. The wrong account or workspace is the most common reason meetings go missing.
   - Other recorders (Gong, Fathom, Fireflies, Otter, Zoom): list one recent call.
   - CRM (HubSpot, Attio, Salesforce): read one deal. Email (Gmail, Outlook): list one thread. Slack: only needed if `post_to` will be set.
   - **In Type, also check workspace connections that exist but are not added to this Space** (for example Granola or HubSpot connected for the workspace but not attached here). Name each one and offer to add it to this Space; only use it once it has been added and a read succeeds.
2. **Propose.** Tell the user what you found and the values you would set. For example: "Granola is connected as dana@corvalto.example (Business plan, personal and Team Space notes). HubSpot and Gmail are connected. Use `crm: hubspot`, `email: gmail`, internal domain `corvalto.example`? Should I draft a Slack post for a CS channel, and which one?"
   - More than one CRM: ask which one. No CRM: propose `crm: none`; the handoff will say promises were not checked against a contract.
   - No meeting recorder: propose `meeting_notes: upload`. Each run then needs the call notes or transcripts pasted or attached.
3. **Wait for the user to confirm.** Never pick a CRM or a Slack channel silently or because it comes first in a list.
4. **Save.** Write the confirmed values to `sales-to-cs-handoff-settings.md` in the user's working folder or Space as a short YAML block using the keys from Config. Never edit this SKILL.md: it is a published skill, and editing it breaks the file-hash check in "Before you run". If you cannot write files there, give the user the exact lines to save.

If an automation triggered the run and nobody is there to confirm, post one message to `post_to` (or the Space) asking for these values, then exit. This setup question is the only message this skill posts when it is not delivering a handoff.

## Step 1: Preflight

This skill only reads Granola, the CRM and email. The only things it creates are the handoff document and, after approval, a CRM note and a Slack post.

1. Meeting notes reachable. For Granola, `get_account_info` succeeded and the active workspace is the one the sales team records in. If the plan is Basic (free), only the last 30 days of notes are available and transcripts and folders are paid-only. Record that; it becomes a coverage gap, not a reason to stop.
2. CRM reachable with read access. If a read fails, continue with `sources.crm.status: unavailable`. The handoff then says the contract was not checked.
3. Email reachable, if configured. Failures are `unavailable` or `denied`, never silently dropped.
4. `handoff_log` readable and its folder writable (scheduled runs). If it does not exist yet, it is created on the first scheduled handoff. If it exists but cannot be read, or its folder cannot be written, the script stops with exit 2 before printing anything: running blind means handing off a deal twice.

## Step 2: Identify the account

There are three ways in:

- **A person asks** ("write the handoff for Quillfen", "what did sales promise Quillfen?", "onboarding brief for Quillfen"). Resolve the account name to its email domain(s): from the CRM company record, from the deal's contacts, or by asking the user. Some accounts use several domains (a parent and a subsidiary, or a rebrand); list all of them in `account.domains`. Never use a personal email provider such as gmail.com as the account domain.
- **Closed-won trigger** (automation). The CRM event names the deal. Read the deal, its company and its contacts for the domain(s).
- **Daily schedule** (automation). List deals moved to closed-won since the last run. Hand off each one that is not already in `handoff_log`.

If the name matches more than one CRM company or deal, list them and ask which. Never write a handoff for a company that only looked close.

Set the search window: from 14 days before the CRM deal's created date (or `lookback_days` before today when there is no deal) up to today.

## Step 3: Find every sales meeting

The goal is complete coverage, so prefer the deterministic tools and use search only to fill gaps.

1. **List.** Call `list_meetings` with `time_range: custom` and `custom_start` / `custom_end` set to the search window as ISO dates (both are required with `custom`; the default range is `last_30_days`, and `this_week` and `last_week` also exist). Split long windows into monthly calls. Leave out `involvement` and `workspace_only`, so teammates' shared notes are included as well as your own:
   - `involvement` takes `captured_by_me` and `listed_as_participant`. Conditions set to `true` are ORed together; conditions set to `false` are exclusions ANDed on top. `workspace_only` can only be `true` (omit it otherwise) and is ANDed with `involvement`. Any of them narrows the list, which is the opposite of what a handoff needs.
   - `folder_id` (paid plans) narrows to one folder. Use it only when the user says every sales call is filed there.
   - Each result has an id, title, date, note URL and known participants with their emails. Participant metadata comes from the calendar and can be incomplete; it does not prove someone attended.
2. **Match.** A meeting belongs to the account when:
   - `domain`: a participant's email domain equals one of the account's domains, or is a subdomain of one; or
   - `title`: the title contains the account name or an alias as a whole word; or
   - `mention`: the notes clearly discuss this account (found with `query_granola_meetings`, below); or
   - `crm`: the CRM logged the meeting against the deal; or `user`: the user pointed you to it.

   Record every reason that applies in `matched_by`. A title or mention match alone can be a different company with the same name: open the notes and check before including it. Put look-alikes you rejected in `excluded_meetings` with the reason.
3. **Fill gaps.** Call `query_granola_meetings` with `query` (required) set to the account name and aliases, to find meetings whose participants used personal email addresses or were never listed (phone calls, in-person meetings). To ask about specific meetings, pass their ids in `document_ids` (alias `note_ids`, at most 10). It is slow and its answer is a paraphrase. Use it to find meeting ids, which are in its citation links `[[n]](https://notes.granola.ai/d/<id>)`, and never as the source of a quote. In testing it gave a summary that missed a price quoted on an earlier call. Only reading each meeting catches that.
4. **Internal-only meetings are excluded.** A meeting whose known participants are all on `internal_domains` (a deal review, a pricing huddle) is not a customer meeting. Colleagues listed by name only, or from a personal address, count as internal when the same name appears with an internal address in another meeting (or is the deal owner). A meeting whose only outside participants are name-only or personal-email people is *unclassified*: it is excluded too, unless you read the notes, the customer was there, and you set `attendees_incomplete: true` with a `title`, `mention`, `crm` or `user` match. List every meeting anyway; the script excludes these and names them under Coverage. If an internal meeting says something was promised that you cannot find in any customer meeting or email, add it as an open question for the account executive, not as a promise.
5. **Duplicates are expected.** When two teammates both record a call, Granola has two notes with different ids, the same start time and often the same title. Include both, because each note can hold details the other missed. The script merges them into one meeting and keeps both links.
6. Drop note-taker bots from participant lists, or leave them in: the script drops addresses that match `bot_email_patterns`.

## Step 4: Read every meeting and extract

1. **Read the notes.** Call `get_meetings` with `meeting_ids` (alias `note_ids`) for every matched meeting, in batches of 1 to 10 ids per call (an id is a UUID or `not_` plus 14 characters). It returns the private notes, the AI summary, participants and the note URL, but no transcript. Set `notes_read: false` for any meeting you could not read; it becomes a gap.
2. **Read transcripts where exact words matter.** Call `get_meeting_transcript` with one meeting id for the meetings where pricing, terms, timelines or commitments were discussed (usually the commercial and final calls), and for any detail the notes leave unclear. Record the outcome per meeting in `transcript`: `ok`, `denied` (workspace setting or plan), `unavailable` (the tool failed or the meeting has no transcript) or `not_requested`.
   - **If access is denied (a workspace policy or the plan), record `denied` and continue with the notes.** Never retry through another tool, and never conclude something "was not discussed" because the notes omit it.
   - Granola transcripts label audio sources, not people: `Microphone` is the recorder's side and `System audio (…)` is the call audio, which can hold several voices. Set `speaker` only when the transcript or context makes the person unambiguous. Otherwise leave `speaker` out. A quote with no speaker is better than one attributed to the wrong person.
3. **Extract, one meeting at a time.** For each meeting, pull out the items listed in `DATA_CONTRACT.md` (`findings`), each with its source meeting id:
   - **pains** and **triggers**: what hurt and what made them act now, quoted.
   - **success_criteria**: the goal, the metric, the target and how they will measure it. Copy numbers exactly as spoken.
   - **milestones**: what is due and when. Leave `due` null when the date was vague ("before peak season").
   - **promises**: anything our side said we would do, provide, build, integrate, discount, guarantee or deliver by a date. Categorise each one (`feature`, `integration`, `custom_work`, `pricing`, `discount`, `sla`, `support`, `training`, `timeline`, `roadmap`, `security`, `other`). Give it a short `topic` and reuse **exactly the same topic wording** whenever another meeting or email touches the same subject, because that is how the script finds conflicts. Write `terms` as a short plain statement of what was offered.
     - `flags.roadmap: true` when it depends on something not yet released. `flags.non_standard: true` when sales called it an exception, or it departs from the standard pricing or terms the user told you about.
     - `in_contract`: `yes` or `no` only after you read the contract, order form or CRM line items; otherwise `unknown`.
   - **technical_requirements**: integrations, data, migration, security, SSO, compliance and infrastructure asks.
   - **objections** (`open`, `partly_resolved`, `resolved`), **risks** (only with evidence) and **competitors** (with a quote).
   - **answered_questions**: facts the customer already gave us (headcount, systems, who signs). These become the "don't ask them again" list.
   - **open_questions**: what is still unknown, and who owes the answer.
4. **Quotes are verbatim or they are not quotes.** Copy the words exactly as they read. The agent copies the quotes; the script does not check them against the meeting text (the bundle carries none), so copying exactly is your job. Granola returns notes as markdown: drop its backslash escapes (write `~`, not `\~`), because the script escapes for each output itself and prints a backslash as a backslash. Set `quote_from: transcript` only for text taken from a transcript you read (`transcript: ok`). Text taken from the notes or AI summary is `quote_from: notes`, and the handoff labels it "notes:", never as the customer's words. Never stitch two statements into one quote. Pains, triggers, promises, objections and competitors must have a quote; with no quote, leave the item out.
5. **Stakeholders.** List each customer person with title and roles (`champion`, `economic_buyer`, `signer`, `executive_sponsor`, `day_to_day`, `user`, `technical_owner`, `data_owner`, `procurement`, `legal_security`, `influencer`, `detractor`, or `unknown`), and what they care about. Use the CRM's contact roles where they exist. Give a role only on evidence:
   - A champion has done something for the deal: made an introduction, shared internal information or pushed a next step.
   - A detractor raised objections or was openly sceptical.
   - Otherwise use `unknown`.

   Every "cares about" item needs its source (meeting, email or CRM). `note` is only for a short fact from the CRM contact record or the user. You do not need to list who attended which meeting. The script works that out from the participant lists and adds any customer participant you did not describe.

Email and CRM notes are extracted the same way, with `source.type` `email`, `crm_note` or `crm_field` and their date.

## Step 5: CRM deal and email (optional)

- **CRM** (match the deal by company domain, then by contact email):
  - HubSpot: `POST /crm/v3/objects/deals/search` filtered on the associated company; read `dealname`, `dealstage` (as its label), `amount`, `closedate`, `createdate`, `hubspot_owner_id` and line items. Contacts and their roles come through associations.
  - Attio: `POST /v2/objects/deals/records/query` on the linked company, then the deal's `stage`, `value`, `close_date` and `owner`, plus people by `email_addresses`.
  - Salesforce: `Opportunity` (`StageName`, `IsWon`, `Amount`, `CloseDate`, `CreatedDate`, `Owner.Name`), `OpportunityLineItem`, and `OpportunityContactRole` for roles. Map `Economic Buyer` to `economic_buyer`, `Decision Maker` to `signer`, `Technical Buyer` to `technical_owner`, `Business User` to `user`, and `Evaluator` or `Influencer` to `influencer`.
  - If a contract or order form is attached to the deal, read it and set `contract_read: true`. That lets you mark each promise `in_contract: yes/no`.
  - No match: `deal: null`. The handoff says so plainly.
- **Email**: search `from:<domain> OR to:<domain>` across the search window. Read only the threads about terms, pricing, scope, security or dates, and extract promises and answers from them like a meeting. Record how many threads you read in `email.threads_read`.

## Step 6: Build the handoff

Save the bundle as a file in the working folder (never inside the skill's folder) and run:

```bash
python3 build_handoff.py \
  --bundle bundle.json \
  --config handoff.json \
  --as-of 2026-10-01T09:00:00-07:00 \
  --internal-domain corvalto.example
```

- `--as-of` is required and is the only "now", as an ISO datetime with an offset. Use the run time.
- Pass `--internal-domain` once per entry in `internal_domains`, each a plain domain such as `corvalto.example` (a URL, an address or a comma-separated list is exit 2).
- `--format promises` prints only the promises section. Use it to answer "what did sales promise [account]?". `--format crm-note` and `--format slack` print the drafts (Step 7); add `--doc-url` (an `http(s)` link to the saved doc) so they link to it, and `--post-to` to name the channel on the Slack draft.
- `--scheduled --log handoff-log.md` is for automation runs only, and is the only run that writes the log. It needs the deal's `record_ref` (exit 2 without it), so every run keys on the same CRM deal. It skips a deal that is not closed-won or already delivered. Otherwise it records the handoff as `pending` (before printing it) and prints it. After the doc is saved and the post sent, run `--confirm-delivered <handoff key> --log handoff-log.md --as-of <now>`, which marks it `delivered`. A person asking (no `--scheduled`) always gets the handoff and never writes the log, so a pre-close draft cannot block the real handoff. `--dry-run` makes a scheduled run read-only. `--json` returns the counts, conflicts, flags and markdown as JSON.
- Exit codes. **0**: the handoff, a one-line `Skipped: …` or a `Confirmed: …` line. **2**: bad input; the message names the file and the JSON path. **3**: a scheduled run while `sources.meeting_notes` is not `ok`; nothing is recorded, so the next run retries. **4**: the handoff is still `pending` from an earlier scheduled run: check whether the doc was saved and the post went out, then either `--confirm-delivered` it or rerun with `--resume-pending` to print it again. **5**: another run holds the log's lock file (`handoff-log.md.lock`; one older than 15 minutes, or dated more than a minute in the future, is treated as stale and removed if it is still the same file; a lock path that is a folder is exit 2). **6**: the output could not be written (closed pipe, full disk); a scheduled handoff stays `pending`, so rerun with `--resume-pending`. On any other non-zero exit nothing is printed to stdout. Fix the bundle and rerun; never edit the script to get past an error.

What the script does, deterministically:

| Step | Rule |
| --- | --- |
| Internal-only | Every listed participant is ours (an internal domain, or a name-only or personal-email attendee whose name appears with an internal address elsewhere or is the deal owner) → excluded and listed once under Coverage. Only ours plus name-only or personal-email outsiders → unclassified, also excluded and listed. Either is kept only if `attendees_incomplete: true` and it was matched by title, mention, CRM or user. A meeting with no participants listed at all is kept and marked "attendee list incomplete". |
| Duplicates | Same start time (compared in UTC) and the same title or a shared customer participant → one meeting with every note link kept. The best transcript status wins. |
| Order | Meetings are numbered by start time. Every finding resolves to its meeting's title, date and link. |
| Promise conflict | Two or more promises with the same `topic` and different `terms` (ignoring case, punctuation and thousands separators, so `$1,500` and `$1500` agree). All versions are shown with their meetings, plus the latest one. The script never picks the binding one. |
| Contract flags | `in_contract: no`, roadmap, non-standard, and promises not checked against a contract. |
| Stakeholders | Merged by email, then by name. Customer participants you did not describe are added. Flags: **appeared once** (1 of 3 or more meetings), **went quiet** (in 2 or more meetings, then missed the last 2 or more, spanning 14 or more days), **not in any meeting read**. A missing champion or economic buyer is flagged too. |
| Milestones | Passed, due within 14 days, later, or no date. |
| Coverage gaps | Notes source unreadable, transcripts denied or unavailable, meetings not read, Granola free plan's 30-day window, personal-only note access, a CRM deal created long before the first meeting found, and CRM or email unreachable. |
| Checklist | Twelve items a CSM needs on day one, ticked from what was found. |
| Output safety | Every value from the bundle is escaped for its output. Markdown: the backslash (first), brackets, asterisks, underscores, backticks, tildes, pipes, `$` and `=` are backslash-escaped, `&`, `<`, `>` become entities, and `://` and every dot inside a word are backslash-escaped (they render the same), so a value (even one written as `\[click\](https://…)`) cannot form a link, image, raw HTML, emphasis, math (`$…$`), highlight (`==…==`), a table-cell break, or an autolink from a URL, `www.` or a bare domain; the deal amount's `$` is escaped too. Slack: `&`, `<`, `>` become entities (so `<!channel>` or `<@U…>` cannot ping) and asterisks, underscores, tildes, backticks, `@` and brackets become full-width look-alikes, so a value cannot break the bold around it or form a markdown link if a tool converts the post. CRM note: `&`, `<`, `>` become entities and the backslash and brackets are backslash-escaped. In Slack and the CRM note, which have no backslash escape, `://` becomes look-alike slashes (`:∕∕`) and a dot before a letter or after `www` a look-alike dot (`․`), so a URL, `www.` or domain in a value is not linked. Invisible control, zero-width and direction-override characters are removed. Links are built only by the script from `http(s)` URLs with a real host and no user name or password, percent-encoded; any other link is dropped, its text kept, and counted under Gaps. Email addresses and phone numbers it recognises are replaced with "(email removed)" / "(phone removed)" in every format; this is pattern-based (see Guardrails). |

Sections the agent found nothing for say "None captured in the … read", naming what was actually read, so an empty section is never mistaken for a fact.

## Step 7: Deliver

1. **The handoff doc** (always). With `deliver_to: type_doc`, create a doc in this Space titled `Handoff: <Account>` with the script's markdown, unchanged. With `markdown`, save `handoff-<account-slug>.md` in the working folder. Do not soften or trim flags, the Coverage section or the Gaps line. A CSM who knows a transcript was denied reads the quotes differently.
2. **CRM note draft** (`crm_note: draft`). Run `--format crm-note --doc-url <doc link>`, show it to the user, and say which record it would go on. Write it to the CRM only after the user approves that exact text. If the CRM connection is read-only, say so and leave the draft with the user.
3. **Slack post draft** (only when `post_to` is set). Run `--format slack --post-to <channel> --doc-url <doc link>` and show it. Post it only after the user approves.
4. Then tell the user, in two or three lines, what most needs their attention: the conflicts, the promises not in the contract, and the stakeholder flags.
5. **Scheduled runs only: confirm delivery.** Once the doc is saved (and the Slack post sent, if `post_to` is set), run `--confirm-delivered <handoff key>` with the same `--log`. Until then the handoff stays `pending`, and the next scheduled run stops with exit 4 instead of posting it again. If saving or posting failed, do not confirm; rerun with `--resume-pending` and deliver it.

For "what did sales promise [account]?", reply with the `--format promises` output and offer the full handoff.

## Output format

The handoff doc always has these sections, in this order:

1. Read this first: conflicts, promises not in the contract, roadmap and non-standard terms, unchecked promises, open objections, missing roles, quiet or one-time stakeholders, milestones due, and transcript gaps.
2. Deal snapshot, or a plain statement that there is no CRM deal.
3. Why they bought: pains and trigger event, quoted.
4. Success criteria and how they'll measure them.
5. Timeline and milestones.
6. What sales promised, by category, with flags and a "Conflicts between meetings" block.
7. Stakeholder map, plus "Our side" (who from our team was in the meetings, to ask for context).
8. Technical requirements, integrations, data and security.
9. Objections, risks and competitors.
10. Don't ask them again.
11. Open questions.
12. Handoff checklist.
13. Coverage: the meetings table (date, link, how it matched, notes read, transcript), search window and method, exclusions, merges, transcript access, sources and gaps, and the handoff key.

## Guardrails

- **Every claim cites its source.** Every quote and finding links to its meeting (title, date, note URL) or names its email or CRM record and date. No source, no claim.
- **Quotes are verbatim, and notes are not speech.** The agent copies the quotes; the script does not check them against the meeting text. Transcript quotes are labelled `transcript`. Text from Granola notes or summaries is labelled `notes:`. Never quote `query_granola_meetings` answers.
- **Never resolve a conflict yourself.** Show every version and ask the account executive which is binding.
- **Never invent intel.** If the data is thin (one meeting, no transcripts, no CRM), the handoff says so at the top. A short honest handoff beats a complete-looking one.
- **Transcript denied means notes only.** Record `denied`, continue, and never retry through another tool or claim something was "not discussed".
- **Never write without approval.** Granola is read-only. A CRM note and a Slack post are drafts until the user approves that exact text. A scheduled run posts only to the `post_to` channel the user chose in Step 0, and never writes to the CRM.
- **Internal-only meetings are never quoted as customer statements.** The script rejects findings that cite them.
- **Personal data: minimum necessary.** The handoff prints names and titles. The script removes recognised emails and phone numbers, including in notes and quotes. Phone detection is pattern-based: a country-code prefix, nearby phone cue (including "cell is"), or recognised national grouping. Spaced US numbers such as `415 555 0134` are removed when area and exchange groups begin with 2–9 and groups are not repeated. Dates, IPs, reference numbers and tested price/year lists are preserved. Ambiguous numbers can be misclassified and unusual formats missed; do not put contact details in free text. Review redacted quotes before sharing. Keep only the necessary quotes rather than full transcripts, and delete the bundle after saving the approved handoff.
- **Internal document.** The handoff holds customer quotes and commercial terms. Do not paste it, or quotes from it, into anything the customer will see without the account team reviewing it.
- **Never choose a CRM, a Slack channel or an account the user has not confirmed** (Steps 0 and 2).

## Automation recipe

Nothing happens until this has a trigger. In **Space settings → Automations**:

- **Best:** App event, when a deal moves to closed-won in HubSpot, Attio or Salesforce. Run this skill in scheduled mode for that deal.
- **Simplest:** Schedule, daily, listing deals that moved to closed-won since the last run and running scheduled mode for each.
- **On request:** "write the handoff for [account]" or "what did sales promise [account]?" from anyone in the Space.

Scheduled mode is two-phase, so a failed save or post is not recorded as done, and a retry stops instead of posting again (this relies on confirming only after delivery):

1. Run the script with `--scheduled --log <handoff_log>`. It skips deals that are not closed-won and deals already delivered. When it prints `Skipped: …`, stop and post nothing. Exit 3 (meeting notes not readable), 4 (pending) or 5 (locked): post nothing and report the message to the Space.
2. When it prints a handoff, it has already recorded the deal as `pending`. Save the doc. If `post_to` is set, render the announcement with `--format slack --post-to <post_to> --doc-url <doc link>` (without `--scheduled`) and post it to `post_to` without its first line, which is the draft banner. The user chose that channel in Step 0, and that choice is the approval for scheduled posts.
3. Run `--confirm-delivered <handoff key> --log <handoff_log> --as-of <now>`. The handoff key is on the `Handoff key:` line at the end of Coverage, between backticks and exactly as `--confirm-delivered` expects it, and is `handoff_key` in `--json` output. Only now is the deal marked `delivered`.

If step 2 fails, do not confirm. The next scheduled run exits 4; after checking what was saved or posted, confirm it or rerun with `--resume-pending`. The CRM note stays a draft for a person to approve. New automations start disabled. Enable it.

## Limits

- Extraction is done by the agent. The script only checks, merges, orders and renders what was extracted, so a promise nobody extracted is a silent miss. Read every meeting, not a search answer.
- Conflict detection depends on the agent reusing the same `topic` wording. Two promises about the same thing under different topics are not compared.
- Granola only returns notes the connected account can see: its own notes, notes shared with it, and (with the `public` scope) Team Space notes. A colleague's private notes are invisible. On the free plan, only the last 30 days are visible and transcripts are unavailable.
- Granola participant lists come from the calendar invite and can be incomplete. They do not prove someone attended, so "appeared once" and "went quiet" are about invitations recorded in the notes.
- `in_contract` is only as good as the contract the agent could read. Promises made on calls that were never recorded are invisible.
- One account per bundle. Single currency per deal.
- Colleagues are recognised by email domain, or by a name that also appears with an internal address (or is the deal owner). A colleague who only ever joins by name or from a personal address, and is not the deal owner, is treated as an outside participant; a customer with the same name as a colleague is treated as a colleague.
- The log lock's staleness check is the only place the script reads the clock. Everything else uses `--as-of`.
- The handoff key (`deal.record_ref`, required for scheduled runs) may only use letters, digits and `: / _ . -`; anything else is exit 2. Every log row must parse (an indented row counts); a row the script cannot read stops the run (exit 2) rather than risk posting a handoff twice.
- In `--json` output only `markdown` is escaped for display; the other fields are the raw values (with emails and phones removed). Escape them yourself if you render them.

## Verification status

**Tested offline:** validation and error paths (strict dates and times in ASCII digits with offsets up to ±14:00, so Python 3.8 and 3.14 agree on values such as `T24:00`; NaN, wrong types, `null` config lists and unpaired surrogates exit 2 with nothing printed), internal-only and unclassified exclusion (including colleagues listed by name only or on personal addresses), duplicate-note merging (including the same start written with different UTC offsets), note-taker bot removal, chronological ordering, promise conflict detection (and no false conflict on case or punctuation), contract flags, stakeholder merging, the appeared-once, went-quiet and never-in-a-meeting flags, missing champion or economic buyer, milestones, every coverage gap, the CRM-note, Slack and promises formats, link checks, the two-phase log (pending, confirm, resume, indented and malformed rows, lock takeover with future or stale times, a folder at the lock path, removing only its own lock), output to an ASCII-only terminal, a closed pipe and a full disk, and unicode. Escaping is checked by a fuzz in `tests.sh`: 22 injection strings (backslash-escaped link syntax, autolinks, raw HTML, entities, Slack mentions and `<url|label>` links, formatting characters, `$…$` and `==…==`, bare URLs, `www.` and bare domains) put into every text field of the sample, all at once and one field at a time, must add no live markup, math, highlight or autolink in any format; a wider version of that fuzz (28 strings, every field, plus crash fuzzing of the bundle, config, log and command line) found no traceback and no injected link, mention or formatting. Phone removal is checked against 78 cases (36 phone formats including `+33 1 23 45 67 89`, `+49 30 12345678`, `07700 900123` and `(555) 123-4567`; 42 look-alikes that must stay, such as ISO dates and times, date ranges, versions, IP addresses, PO, order and invoice numbers and seat counts), plus 15 more international formats (`+33 (0)1 23 45 67 89`, `+81-90-1234-5678`, en dashes), 6 email addresses with non-ASCII letters, a full-width `＠` or a zero-width space, and 60,000 generated dates, ranges, amounts and order numbers that must stay, plus 12 number lists that must stay (prices such as `9.99 19.99 29.99`, tiers ascending and descending, `0800 0900 1000`, `40000-50000`) and 7 phones that must still go (`07700 900 123`, `06 12 34 56 78`, `Call 0800 0900 1000`); two conflicting price lists both print in full. A signal-only corpus of 65 more cases checks that lists without a `+` or cue word stay (31, such as `36000 36000 38000`, `500 250 1000 750`, `2025 2026 2025 2027`) and that UK, US, French, German and international phones, and numbers after a cue word, go (34, including bare spaced US numbers); conflicting non-monotonic prices (`36000 36000 38000` vs `36000 38000 38000`) print in full in every format. Email matching stays fast on 32,000-character tokens. Also tested: domain bot patterns (`ann@bread.ai.example` is a person, not the `read.ai` bot; `mattldvorak@…` is a person, not the `tldv.io` bot), quotes with inner quotation marks kept verbatim, handoff keys printed exactly and confirmable, distinct keys for account names in other scripts, and links with credentials or bogus hosts dropped. `bash run.sh` matches `expected_output.txt` byte for byte and `bash tests.sh` passes on Python 3.8 and 3.14 (2026-10-13). Both use fictional sample data only.

**Run against live Granola (read-only, 2026-10-09, on the script as of that day; the stricter checks added on 2026-10-11 and 2026-10-12, and the round-5 changes of 2026-10-13 (signal-only phone removal), were tested offline only):** `list_meetings` (last 30 days: 70 meetings listed), `get_meetings` on one external account's 6 notes in one batch, and `get_meeting_transcript` on one of them (access ok). A bundle built from them in a temporary folder, plus one internal meeting, ran through every format on Python 3.8 and 3.14 with identical output and no errors: 6 notes merged into 3 meetings, the internal meeting excluded, 1 price conflict between meetings caught, no email address or raw markup in the output. The scheduled flow (pending, rerun stops with exit 4, confirm, then skipped) ran on the same bundle with a stub closed-won deal, as no CRM was connected. The bundle and transcript were deleted after the run.

**Unverified against live systems:** the CRM and email reads in Step 5, writing the CRM note, posting to Slack, other recorders, and the closed-won automation. These follow each provider's documented endpoints but have not been run against real accounts. Check the first few handoffs against the meetings by hand.

Round 8 contact cues also include named possessives (straight or curly apostrophes), my/his/her/their, personal/direct/best/work/home, line-start Number, and is/'s/:/- separators. Spaced US country code `1` is recognized with a valid area/exchange shape. Short counts, business identifiers and the tested H7 price/year lists are retained. This remains pattern-based, not complete contact detection.
