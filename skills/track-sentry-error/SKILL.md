---
name: track-sentry-error
description: Create or link durable Linear tracking for a human-approved Sentry error discussed in Slack. Use only on explicit requests such as "track this", "track this error", or "create/link the Linear issue" in a Sentry error thread. Searches Linear for duplicates first, creates new issues only in your configured Linear team and project, preserves the human gate, and backlinks both the Slack thread and the Sentry issue.
---

# Track Sentry Error

Turn a human-approved Sentry error from a Slack thread into durable Linear tracking. Prefer linking and enriching an existing issue over creating a duplicate.

Typical entry points, when posted in a Slack thread about a Sentry error:

- `track this`
- `track this error`
- `create the Linear issue` or `link the Linear issue`
- `@Type track this` (or a mention of whichever agent your team uses)

Pairs with **Investigate Sentry Error**, which produces the triage this skill builds on.

## Configure before first use

Set these once for your workspace. They are creation invariants: every new issue goes here.

| Setting | Meaning | Example |
| --- | --- | --- |
| `LINEAR_TEAM_KEY` | The Linear team whose issue identifiers you want, by prefix | `ENG` |
| `LINEAR_PROJECT` | The exact Linear project name for error work | `Sentry Error Backlog` |

If either value is missing, ask the human for it before writing to Linear. Do not guess, and do not fall back to a default team or backlog.

## Requirements

- Slack access to read the thread and post the reply
- Sentry access to read the issue and representative events
- Linear access with permission to search, create issues, and comment
- Optional: read access to the codebase and GitHub pull requests for richer evidence

## Human gate

- Run this workflow only after an explicit human request to track the error.
- Do not treat the investigation skill's `TRACK` recommendation, a bot-authored suggestion, or a conditional question such as `should we track this?` as approval.
- The human's `track this` request is approval for the scoped Linear search and write actions in this workflow. It is not approval to implement a fix, assign an engineer, start work, or open a pull request.

## Establish the error identity

1. Read the Slack parent message and the full reply thread, including any prior investigation.
2. Capture the exact Slack thread permalink and every relevant Sentry issue or event URL.
3. Extract a durable identity for the problem:
   - Sentry issue ID or fingerprint
   - exception type and normalized error message
   - top in-app stack frame and affected operation
   - likely underlying root cause and affected product behavior
4. Reuse the prior investigation when it is supported by evidence. If there is no prior investigation, gather enough Sentry and code context to write a meaningful issue rather than copying the alert title.
5. If multiple unrelated errors are present and the requested target is genuinely ambiguous, ask the human which one to track before writing to Linear.

## Search for duplicates before writing

Search Linear across active work and relevant recently closed work. Use several independent keys:

- exact Sentry issue ID and URL
- exception type and stable portion of the normalized message
- top in-app file, function, route, service, or operation
- user-visible symptom
- likely root cause, including alternative wording

Inspect candidate descriptions and comments, not titles alone. Also check linked or open pull requests when available. A duplicate means the same underlying defect or remediation, not merely the same error class.

Classify the result as one of:

- `EXISTING`: an issue already represents the same live problem.
- `REGRESSION`: a previously resolved issue clearly recurred after its fix or release.
- `NEW`: no credible duplicate exists.

## If an existing issue matches

1. Do not create another issue.
2. Ensure the existing issue contains both backlinks:
   - the canonical Sentry issue URL
   - the Slack thread permalink
3. If either link or material new evidence is missing, add one concise comment with the links, current impact, and any evidence that changes the issue's understanding. Do not add a redundant comment when the same context is already present.
4. Do not change status, priority, project, assignee, or labels unless the human explicitly requested it or an unambiguous team convention requires it.
5. Reply in Slack with the existing issue identifier and link, and say that no duplicate was created.

## If this is a regression

- Follow your engineering team's established Linear convention if one is visible.
- Prefer reopening or linking the prior issue only when that convention clearly calls for it.
- Otherwise create a new regression issue and cross-link the prior issue so the new occurrence is not hidden inside completed work.
- Explain the evidence that makes this a recurrence rather than a superficially similar error.

## If a new issue is needed

Create exactly one Linear issue with both of these mandatory destinations:

- **Team:** the team whose issue identifier prefix is `LINEAR_TEAM_KEY`
- **Project:** `LINEAR_PROJECT` (exact project name)

These apply to newly created regression issues too. Before creating the issue, resolve the team and the exact project in Linear and pass both explicitly to the create operation. Do not rely on a default team or backlog, and do not substitute a similarly named team or project.

If either destination cannot be found or selected, do not create the issue somewhere else. Report the blocker in Slack and preserve the proposed issue content so a human can finish the action.

Use a title that describes the broken behavior or root cause. Do not use only the raw Sentry title.

Use this body structure:

```markdown
## Problem
<What fails, where, and the user-visible or system consequence>

## Impact
<Affected users/operations, frequency, trend, environment, and criticality. Use Unknown where needed.>

## Evidence and likely root cause
<Observed Sentry/code evidence, followed by a clearly labeled hypothesis and confidence level>

## Proposed work
<Smallest credible remediation and important risk or rollout considerations>

## Verification / acceptance criteria
- <The failure is reproduced or otherwise pinned>
- <The expected behavior after the fix>
- <The relevant test, monitoring, or Sentry verification>

## Context
- Sentry: <canonical issue URL>
- Slack: <thread permalink>
- Related PR/issue/release: <links when relevant>
```

Apply workspace conventions conservatively:

- The configured team and project are mandatory and override any generic workspace default.
- Use that team's normal triage or backlog status unless the human explicitly requests another status.
- Add existing `bug` or Sentry-related labels only if those labels are already used for this kind of work.
- Set priority only when supported by impact evidence and normal team policy.
- Do not invent an assignee, cycle, deadline, customer count, or severity.
- Preserve uncertainty from the investigation rather than upgrading a hypothesis into fact.

After creation, read the created issue back and verify:

- its identifier begins with `LINEAR_TEAM_KEY-`
- its project is exactly `LINEAR_PROJECT`
- its description contains both the canonical Sentry URL and the Slack thread permalink

If verification fails, correct the team, project, or missing backlinks immediately within the same approved tracking action. If correction is not possible, report the exact mismatch and do not claim successful completion.

## Required Slack reply

For an existing match:

`Already tracked in <ISSUE-ID>: <link>. Added the Sentry and Slack context if it was missing; no duplicate created.`

For a new issue:

`Created <ISSUE-ID> in <LINEAR_PROJECT>: <link>. Added the Sentry issue and this Slack thread as backlinks.`

For a regression, state whether the prior issue was reopened or a cross-linked regression issue was created.

If Linear is unavailable or a write fails, do not claim success. Report what failed, preserve the proposed issue content in the reply, and tell the human exactly what remains to be done.
