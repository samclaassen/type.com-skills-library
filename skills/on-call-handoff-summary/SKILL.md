---
name: on-call-handoff-summary
description: Generate a concise, evidence-linked draft handoff for an engineering on-call rotation from Slack alerts, Sentry, Linear, and GitHub. Use whenever someone asks for an on-call handoff, shift summary, operational handoff, end-of-shift report, or a summary of incidents, alerts, recurring problems, fixes, owners, and next actions, including when a scheduled automation runs it before a shift change.
---

# On-call Handoff Summary

Prepare the first draft of an on-call handoff so the outgoing engineer only needs to review, correct, and add context. This is an operational summary, not a runbook, incident retrospective, or exhaustive alert log.

## Configure before first use

| Setting | Meaning | Example |
| --- | --- | --- |
| `ALERT_CHANNELS` | Slack channels where errors, alerts, and incident discussion land | `#errors`, `#alerts`, `#incidents` |
| `TIMEZONE` | Timezone for the handoff window | `America/New_York` |
| `DEFAULT_LOOKBACK` | Window to use when no prior handoff is found | `7 days` |

If `ALERT_CHANNELS` is not set, ask which channels to read before gathering evidence.

## Requirements

- Slack access to the alert channels and any linked incident threads
- Sentry access for issue activity and recurrence
- Linear access to read issues, owners, and status
- GitHub access to read pull requests, reviews, and merges

## Principles

- Treat the output as a review-required draft. Say this clearly at the top.
- Report only what the evidence supports. Never invent an owner, status, cause, resolution, deployment state, or next action.
- Distinguish confirmed facts from reasonable concerns. Put uncertain items under **Needs human confirmation**.
- Prefer concise synthesis over a chronology or raw alert dump.
- Link every substantive item to the best available source.
- Do not modify Slack, Sentry, Linear, or GitHub while gathering evidence. This skill is read-only unless the user separately asks for an action.

## Determine the handoff window

1. Use the time window supplied by the caller or scheduled task.
2. If no window is supplied, use the period since the most recent on-call handoff in the same channel or workspace.
3. If no prior handoff can be found, use `DEFAULT_LOOKBACK`.
4. State the exact start, end, and timezone in the output.

Include late updates to an older incident when they changed its status, owner, mitigation, or next action during the window.

## Prepare integrations

Prepare the required integrations once before gathering evidence:

- Slack for alerts, incident discussion, investigation threads, and human context
- Sentry for issue activity, recurrence, status, and event evidence
- Linear for durable tracking, owner, priority, and current status
- GitHub for pull requests, reviews, merges, and deployment-relevant fix progress

Discover current tools and schemas at runtime.

If one source is unavailable, continue with the others and disclose the gap under **Needs human confirmation**. Do not claim the handoff is complete when a primary source could not be checked.

## Gather evidence

### Slack

Read messages and relevant threads from the handoff window in every channel in `ALERT_CHANNELS`.

Also inspect incident channels or linked threads referenced by those messages. Capture incident-management notifications and links (for example from incident.io, PagerDuty, or Opsgenie) when they appear in Slack. Search for mitigation, recurrence, customer impact, ownership, and follow-up decisions rather than copying all messages.

Use Slack permalinks for cited messages or threads.

### Sentry

Find issues active during the window, with emphasis on:

- New or materially regressed errors
- High-volume or high-impact errors
- Errors discussed in the Slack channels above
- Recurring issues thought to be fixed
- Issues linked to Linear tickets or GitHub pull requests

Record current status, recurrence, and links. Treat Sentry's resolved state as evidence about Sentry state, not proof that a production fix shipped.

### Linear

Find issues created, updated, or referenced during the window that relate to operational events, customer bugs, Sentry errors, or follow-up work. Record the current owner, status, priority when meaningful, and explicit next action.

Do not move or relabel issues. This skill summarizes existing tracking; it does not reclassify work.

### GitHub

Inspect linked or relevant pull requests. Record whether each fix is draft, open, awaiting review, approved, merged, or otherwise blocked. Only call a fix shipped when production deployment is established by available evidence. A merged pull request alone is not always proof of deployment.

## Correlate and prioritize

Group Slack alerts, Sentry issues, Linear tickets, and GitHub pull requests that describe the same underlying problem. Use explicit links, shared identifiers, affected service, timing, and discussion context to correlate them.

Prioritize:

1. Active or recently mitigated operational incidents
2. Unresolved or recurring problems that the next engineer may encounter
3. Customer-impacting bugs and fixes in progress
4. Important Sentry errors and hard-fix work
5. Lower-risk informational changes only when they affect the next shift

Do not include routine noise, duplicate alerts, or every small merged fix. Include resolved events when the next engineer should know what happened, what changed, or what to watch.

## Output format

Use this structure exactly. Omit an optional section only when it would be empty, but always include **Active incidents**, **Unresolved or recurring**, **Fixes in progress**, **How was on-call?**, and **Needs human confirmation**.

# On-call handoff: [start] to [end]

> Draft generated from operational activity. The outgoing on-call engineer must review, correct, and add context before handoff.

## At a glance

- [Two to five bullets covering the most important current state]

## Active incidents

For each incident:

### [Short incident name]
- **Impact:** [confirmed impact or `Unknown`]
- **Status:** [active, monitoring, mitigated, or other evidenced state]
- **Owner:** [named owner or `Unassigned`]
- **Next action:** [explicit next action or `Needs confirmation`]
- **Evidence:** [linked sources]

Write `None observed` when no active incidents were found.

## Important operational events

- **[Event]:** [what happened, impact, mitigation or resolution, and anything to watch] [sources]

## Unresolved or recurring

- **[Problem]:** [current state, recurrence signal, owner, and next action] [sources]

Write `None observed` when no unresolved or recurring problems were found.

## Fixes in progress

- **[Fix]:** [PR or issue status, owner, blocker or next action, and deployment state when known] [sources]

Write `None observed` when no relevant fixes are in progress.

## Shipped fixes

- **[Fix]:** [what changed and the evidence that it shipped] [sources]

## Watch next shift

- [Specific condition, alert, vendor, rollout, or follow-up the incoming engineer should watch]

## How was on-call?

_Outgoing on-call engineer: select one and add context if useful._

- [ ] 👍
- [ ] 😐
- [ ] 😱
- **Notes:**

Leave every option unchecked. This rating is subjective and must be completed by the outgoing on-call engineer; never infer it from alert volume or incident severity.

## Needs human confirmation

- [Missing context, uncertain correlation, unknown owner, ambiguous resolution, unavailable source, or unverified deployment]

Write `None` only when all primary sources were checked and no material uncertainty remains.

## Quality check

Before returning the draft:

- Confirm the time window and timezone are explicit.
- Confirm active incidents appear before completed work.
- Confirm related Slack, Sentry, Linear, and GitHub evidence is deduplicated into one item.
- Confirm each substantive item has a source link.
- Confirm owners and next actions come from evidence or are marked unknown.
- Confirm merged and shipped are not used interchangeably.
- Confirm quiet shifts still say which sources were checked and do not manufacture activity.
- Confirm **How was on-call?** is present with all three options unchecked and room for the outgoing engineer's notes.
- Keep the summary short enough to scan at shift change.
