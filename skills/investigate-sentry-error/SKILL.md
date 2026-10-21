---
name: investigate-sentry-error
description: Investigate and triage a Sentry error from its Slack thread without creating or changing Linear issues. Use when someone says "investigate this error", "triage this", "@Type investigate", or asks for analysis of a Sentry error in Slack. Always finish with one disposition recommendation (IGNORE, QUICK FIX, or TRACK) and tell the human how to approve tracking when warranted.
---

# Investigate Sentry Error

Investigate one Sentry error from the Slack thread where the request was made. Produce an evidence-backed, opinionated triage result that a human can act on.

Typical entry points, when posted in a Slack thread about a Sentry error:

- `investigate this error`
- `triage this`
- `analyze this Sentry issue`
- `@Type investigate` (or a mention of whichever agent your team uses)

Pairs with **Track Sentry Error**, which creates or links the Linear issue after a human approves.

## Requirements

- Slack access to read the error thread
- Sentry access to read the issue and representative events
- Read access to the codebase the error comes from (for example, a connected GitHub repository)
- Read access to Linear, used only to find existing work

## Hard boundary: investigation only

- Do not create, update, assign, reopen, close, or comment on a Linear issue.
- Do not start a fix or open a pull request.
- A recommendation to track is not approval to track.
- Linear writes require a later, explicit human request such as `track this`.
- You may search Linear and GitHub read-only to find existing work and report it.

## Context to collect

1. Read the Slack parent message and the full reply thread, not just the message that invoked the skill.
2. Capture the Slack thread permalink for later backlinking.
3. Identify every Sentry issue or event URL in the thread. Select the issue under discussion; if multiple unrelated issues are present and the target is genuinely ambiguous, ask which one to investigate.
4. Inspect the Sentry issue and representative production events. Gather what is available:
   - issue ID, exception type, normalized error message, and fingerprint
   - first seen, last seen, event count, affected users, and recent trend
   - environment, release, service, route or operation, tags, breadcrumbs, and request context
   - the most useful stack trace, especially the top in-app frames
5. Trace the failure into the connected codebase:
   - follow the stack and call path to the relevant source
   - distinguish the failing line from the condition that made it fail
   - inspect nearby tests, error handling, and ownership boundaries
   - check recent commits, pull requests, releases, or deployments only when they help test a regression hypothesis
6. Search Linear and relevant open pull requests for known work using the Sentry ID or URL, exception and normalized message, top in-app frame, affected operation, and likely root cause. Treat superficial title similarity as insufficient evidence of a duplicate.
7. Separate observed facts from hypotheses. Never invent frequency, user impact, a regression window, an owner, or an effort estimate. Use `Unknown` when evidence is unavailable.

## Decide a disposition

Choose exactly one:

- `IGNORE`: expected, transient, non-production, no meaningful user impact, or not actionable. Explain why recurrence does not currently warrant work.
- `QUICK FIX`: the root cause and narrow remediation are clear, the blast radius is small, and the work can credibly be handled immediately without durable planning. Recommend tracking if it will not actually be fixed promptly.
- `TRACK`: customer-visible or recurring impact, a critical path, data or security risk, uncertain or cross-cutting remediation, meaningful coordination, or work that should survive beyond the current Slack thread.

Use impact and evidence, not raw event count alone. State uncertainty directly and lower confidence when the evidence does not prove causality.

## Required output

Use this exact section order, keeping each section concise:

```text
Summary
-------
<What is failing and where, in one or two sentences>

Impact
------
<Who or what is affected; frequency/trend; criticality. Say Unknown where needed.>

Evidence
--------
- <Sentry fact>
- <code/release/history fact>
- <existing Linear issue or PR, if any>

Likely root cause
-----------------
<The causal explanation, clearly marked as a hypothesis unless proven>

Confidence
----------
<High | Medium | Low>: <brief reason>

Likely fix
----------
<Smallest credible remediation and important verification>

Estimated effort
----------------
<Evidence-based range or Unknown>

Disposition recommendation
--------------------------
<IGNORE | QUICK FIX | TRACK>

Why
---
<The decisive reasons for the disposition>

Next action
-----------
<Human-facing next step>
```

For `TRACK`, end the Next action with:

`Reply "track this" to create or link the Linear issue.`

For `QUICK FIX`, say who can act if ownership is known and add:

`If this will not be fixed promptly, reply "track this" so it does not get lost.`

For `IGNORE`, state what evidence or threshold should trigger reconsideration. Do not suggest tracking merely as a ritual.

## Quality bar

- Prefer a narrow causal claim supported by Sentry and code evidence over a broad plausible story.
- Do not paste a long stack trace or merely restate the Sentry title.
- Make the recommendation unambiguous even when confidence is low.
- Include links to the Sentry issue, any existing Linear issue or pull request, and the Slack thread when the surface supports links.
- If a required connector or repository is unavailable, complete the parts you can, name the missing evidence, lower confidence, and still provide a disposition.
