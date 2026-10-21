---
name: failed-payment-recovery
description: |-
  Recover involuntary churn from failed subscription payments. Detects failed charges and past-due invoices, classifies the decline reason, runs a retry and outreach ladder, and records every send so nobody is contacted twice. Use when a payment fails, an invoice goes past due, a dunning or recovery run is requested, or someone asks about failed payments, declined cards, involuntary churn, or recovered revenue. Works with Stripe, Recharge, or any billing system reachable through a Custom API connection.
---

## What this does

Recovers revenue lost to failed payments — the churn nobody chose. Industry recovery rates sit near 50% (Recurly 53% on monthly plans across 76M subscriptions; Stripe reports 55% average), and B2B recovers better than B2C (53.5% vs 34.6% on past-due invoices). Anything below ~40% is money on the floor.

The job is finished when the payment is recovered or the ladder is exhausted — not when a draft exists.

## Config

Read this block first. These are the only values a customer should need to change.

```yaml
billing_system: stripe          # stripe | recharge | custom
lookback_hours: 24              # how far back to scan on each run
ledger_mode: system_of_record   # system_of_record | skill_file — see Step 3
ledger_file: ledger.md          # only used when ledger_mode: skill_file
notify_channel: ""              # Slack channel for the weekly summary; blank = post in this Space
send_mode: approve_first_run    # approve_first_run | approve_always | autonomous
max_discount_pct: 0             # 0 = never offer a discount without approval
max_touches_per_customer: 4     # across the whole ladder
quiet_hours: "21:00-08:00"      # recipient local time; never send inside this window
currency_floor:                 # skip recovery attempts below this amount, in the invoice's own currency (major units)
  default: 5                    # used for any currency not listed
  JPY: 500
  KRW: 5000
```

**Units for `currency_floor`:** compare in major units. Billing APIs such as Stripe report amounts in the smallest unit (cents), so divide by 100 for two-decimal currencies. Zero-decimal currencies such as JPY and KRW are already whole units: do not divide. Three-decimal currencies such as KWD and BHD divide by 1000. List a floor for every zero- or three-decimal currency you bill in; `default` is meant for dollar-, euro- and pound-sized units.

If a value is missing, use the default above. **Do not ask the user to confirm config on a normal run.**

## Step 1 — Preflight

Check before doing any work. If a check fails, say exactly what is missing and stop. Do not improvise around a missing connection.

1. Billing connection reachable, with **read** access to charges/invoices and subscriptions.
2. Billing connection has **write** scope. A connected billing system is not necessarily a writable one. On 2026-09-19 a live Stripe connection read invoices fine but rejected writes with "Your API key does not have the required permissions." Check this at preflight, not halfway through a recovery.

   **Never test write scope with a write.** A test write is a real change to a customer's billing record. Check permissions by reading them instead, or by asking:
   - **Stripe.** Stripe has no API endpoint that lists a key's permissions. Ask the user to confirm the connection uses a standard secret key, or a restricted key with **Write** on Invoices and on the other resources you will write to. Stripe's permissions reference shows which permission each endpoint needs.
   - **Recharge.** Recharge has no documented endpoint for reading a token's scopes either. Ask the user to confirm on the token's edit page that it has **Read and Write** access for the resources recovery touches (for example `write_orders`, `write_customers`, `write_subscriptions`).
   - **Chargebee** (via `custom`). Ask the user to confirm the key is a **Full-access** key. **Read-only: All** and **Read-only: Restricted** keys cannot retry or write metadata.
   - **Recurly** (via `custom`). Recurly documents its private API keys as full-access, so a working key can write. Confirm with the user that the connection uses a private API key.
   - **Any other system.** If its API documents a key-introspection or permissions endpoint, read it. Otherwise ask the user.

   Until the user confirms, treat the connection as **read-only**.
3. A send path exists (email tool, or the billing system's own dunning email).
4. The ledger is readable — metadata search if `ledger_mode: system_of_record`, otherwise `ledger_file`.

If the billing system is connected but the API errors, report the error verbatim and stop. A silent partial run is worse than no run.

**Read-only billing connection** is a degraded but useful mode: detect and report what would be recovered, and say plainly that recovery cannot proceed until write scope is granted. Do not pretend to run the ladder.

## Step 2 — Find the failures

Query for, within `lookback_hours`:
- Failed charges / declined payment intents
- Invoices that moved to past due or unpaid
- Subscriptions that entered a dunning or grace state

**If nothing is found, exit silently.** Do not post "no failed payments today." A monitor that speaks when it has nothing to say gets muted, and muted monitors get uninstalled.

## Step 3 — Filter against the ledger

**Default is `system_of_record`.** Store recovery state as metadata on the invoice or subscription itself:

| Key | Value |
|---|---|
| `type_recovery_step` | last ladder step delivered |
| `type_recovery_pending_step` | ladder step drafted but not yet sent (outcome `queued` or `held`); cleared when it is sent |
| `type_recovery_last_touch` | ISO 8601 UTC |
| `type_recovery_class` | soft / ambiguous / customer_action / hard / risk / backlog |
| `type_recovery_outcome` | sent / queued / held / recovered / abandoned |
| `type_recovery_retries` | number of payment retries this skill has run on the invoice |
| `type_recovery_last_retry` | ISO 8601 UTC of the last retry this skill ran |

Read it back with a metadata-filtered search — verified working on Stripe 2026-09-19 via `GET /v1/invoices/search` with `metadata["type_recovery_step"]:"2"`.

This beats a skill file on every axis that matters: no version churn, no write races, it survives skill updates, and it works when the person who installed the skill does not own it. **Only use `skill_file` mode for low-volume runs, and read `ledger.md` before you do.**

For each failure, drop it if:
- This customer + invoice already has a ledger entry at the same ladder step with `type_recovery_outcome: sent`
- The customer has already received `max_touches_per_customer` touches for this invoice
- The amount is below the `currency_floor` for its currency
- The subscription was cancelled deliberately by the customer (that is voluntary churn — a different skill)

**This step is what makes the skill safe to run every hour.** Never send before reading the ledger.

**Queued and held entries are not sends.** Only `sent` counts toward the same-step rule and toward `max_touches_per_customer`. A `queued` or `held` entry means the draft for that step already exists: do not draft or post it again. Send it when its condition clears (quiet hours end, or approval arrives), then set `type_recovery_step` to that step, clear `type_recovery_pending_step`, and set the outcome to `sent`. Retries waiting on the first approval are not recorded as pending: nothing is written, and each later run re-evaluates them after approval. The `approve_first_run` draft works the same way: post it once, record it as `held`, and on later runs check for the approval instead of posting a new draft.

### Retries are recorded too

A retry charges the customer's card, so it gets the same protection as an email. Before any retry:

1. **Use one trigger.** Run recovery from either the webhook or the hourly schedule, never both, so two runs never work the same invoice at once. If you find two recovery triggers set up, stop and tell the user which one to disable. On Stripe, also send an `Idempotency-Key` of `<invoice id>-<retry day>` with `POST /v1/invoices/{id}/pay`, so a repeated call cannot charge twice.
2. **Check the ledger.** Skip the retry if `type_recovery_last_retry` falls on or after the current retry day in `recovery-policy.md`, or if the class is ambiguous and `type_recovery_retries` is already 1.
3. **Check the billing system's own history.** Skip the retry if any payment attempt on this invoice, by anyone (including the billing system's own dunning), happened in the last 24 hours. On Stripe, compare the invoice's latest charge or payment attempt time and `attempt_count` with what the ledger expects; on Chargebee, list the invoice's transactions. If the latest attempt is newer than `type_recovery_last_retry` and you cannot show that the billing system's own dunning made it, assume it was an unrecorded retry by this skill: skip the retry and flag the invoice for a human. If `type_recovery_last_retry` is not set, this skill has never retried the invoice, so treat earlier attempts as the billing system's own. The one exception is an invoice flagged by an earlier run whose ledger write failed; leave it for the human.
4. **Record it straight away.** Write `type_recovery_retries` and `type_recovery_last_retry` immediately after the retry call and before any email. Record it even if the call errored or timed out: an unknown result counts as a retry, because the charge may have gone through. If that write fails, stop the run and flag the invoice for a human, naming the retry that went unrecorded: without the record, the next run would retry again.

At most one retry per invoice per retry day, however often the skill runs.

### First run: the backlog

The first time this skill runs on a real account it will not find today's failures — it will find months of them. Verified on a live account 2026-09-19: four open invoices aged 48 to 91 days, every one with `attempt_count: 9`, `next_payment_attempt: null`, `auto_advance: false`. The billing system had already given up on all of them.

Do not run the normal ladder on these. Sending a breezy "your payment didn't go through" about a charge that failed three months ago is worse than saying nothing.

On the first run, and on any later run, a failure with no ledger entry that is **past the ladder window** is backlog. The ladder window runs from the first failed attempt to the `final_notice` day in `recovery-policy.md`. For each backlog failure:

1. Classify it as **backlog** and do not retry automatically.
2. Group the backlog into one summary for a human — count, total amount, age range, and class breakdown.
3. Require approval before any backlog contact, regardless of `send_mode`.
4. Allow at most **one** touch per backlog invoice, using `final_notice` framing with a card-update link. Never the full ladder.
5. For anything the customer clearly abandoned, recommend void or write-off rather than contact.

After the backlog is cleared once, record it so later runs skip straight to live failures.

## Step 4 — Classify the decline

Read `recovery-policy.md` and map the decline code to a class. The class determines the message, not your judgment of it.

Classify on **`decline_code`**, not the top-level `code` — `recovery-policy.md` has the exact field path for each billing system. Reading the wrong field collapses every decline into one bucket and the ladder stops working.

The five classes behave very differently:

- **Soft** (insufficient funds, issuer temporarily unavailable) → retry on the schedule, with light-touch messages. Most recoverable.
- **Ambiguous** (`generic_decline`, `do_not_honor`) → the issuer declined without saying why. Retry **once**, then treat as hard. Common in practice, and the most expensive class to get wrong.
- **Customer action** (authentication required, CVC or postcode mismatch) → the card may still work, but only after the customer does something. No scheduled retries. Ask for that specific action, and retry once after they take it.
- **Hard** (expired, stolen, closed account) → retrying is pointless. Go straight to a card-update request.
- **Risk/fraud** → do not retry, do not email. Flag for a human.

Misclassifying a hard or ambiguous decline as soft wastes the whole ladder on retries that cannot succeed. A live account was observed making nine consecutive attempts against a `generic_decline` on a card that had not expired — every one of them doomed.

## Step 5 — Select the ladder step and draft

`recovery-policy.md` holds the retry schedule and the message for each step. Pick the next step this customer has not received for this invoice.

Draft rules:
- Lead with the fix, not the failure. The customer needs a working link, not an explanation of dunning.
- One call to action: update the card.
- Never imply the customer did something wrong.
- Never offer a discount unless `max_discount_pct` > 0 and the step calls for it.
- Match the brand's voice file if one exists in the Space.

## Step 6 — Send

Behavior depends on `send_mode`:
- `approve_first_run` (default) — post the first drafted message for approval to `notify_channel` (or this Space if it is blank), then run autonomously once approved. Until it is approved, queue everything else, retries included: nothing goes to a customer or their card before the first approval. This is how the skill earns trust without stalling.
- `approve_always` — every send waits for approval.
- `autonomous` — send without asking.

**Never use `ask_user` mid-run to resolve a config question.** If a send is genuinely ambiguous, **hold it**: do not send it. Record it with `type_recovery_outcome: held` and the reason. Then post it for approval in the same place `approve_first_run` posts drafts, and continue with the sends that are not in doubt. A held send goes out only after approval, whatever the `send_mode`.

Respect `quiet_hours`. If the send would land inside them, queue for the next allowed hour. If the recipient's time zone is unknown, use the billing address country when it has a single time zone; otherwise send at 15:00 UTC and record that the time zone was assumed.

## Step 7 — Record

Write state immediately after each send — metadata on the invoice in `system_of_record` mode, or an appended row in `skill_file` mode. Record the send even if it partially failed, with the error. An unrecorded send becomes a duplicate send on the next run.

If the write fails, **stop the run**. Continuing without a recorded state guarantees duplicate contact on the next trigger.

## Step 8 — Weekly summary

This step runs as its own **scheduled weekly task**, not as part of the hourly or webhook recovery run. Set it up as a second scheduled task in the Space, running once a week, that invokes this skill and asks for the weekly summary (see Automation recipe). When the skill is invoked that way, skip Steps 2–7 and do only this step.

Read the ledger for the past 7 days and post to `notify_channel`: attempts, recovered count, recovered amount, recovery rate, the ladder step that is converting best, and any sends still `held` for approval. Recovery rate is the number that matters. Track it against the ~50% benchmark, not against zero.

If there were no attempts that week, post nothing.

## Guardrails

- **Backlog protection.** Never run the standard ladder on a failure that is **past the ladder window** (`recovery-policy.md`). Summarize it and ask.
- Never contact a customer more than `max_touches_per_customer` times for one invoice.
- Never discount past `max_discount_pct` without explicit approval.
- Never retry a hard decline or a fraud block, and never retry an ambiguous decline more than once.
- Never retry the same invoice twice on one retry day. Record every retry in the ledger immediately after running it; if the record can't be written, stop.
- Never send inside quiet hours.
- If the ledger is unreadable, stop. Do not send blind.
- Never test write scope with a write.

## Limits

- The recovery rates above are industry benchmarks, not a promise for your account.
- Classification is only as good as the decline data. Codes that are missing or not in the tables are treated as ambiguous.
- Chargebee and Recurly run through `billing_system: custom`. Their field paths come from public documentation and have not been checked against a live account.
- On Stripe, Recharge and Chargebee, write scope cannot be verified without writing, so preflight relies on the user's confirmation.
- If the billing system's own dunning emails are also on, customers get both. Pick one sender.
- Out of scope: voluntary cancellations, disputes and chargebacks, and refunds.

## Automation recipe

This skill does nothing until it has a trigger. Set one up in **Space settings → Automations**:

- **Best:** Webhook, fired by the billing system on payment failure (`invoice.payment_failed` in Stripe). Near-real-time, no polling.
- **Simplest:** Schedule, hourly, with `lookback_hours: 24` for overlap so nothing is missed at the boundary.
Pick one of these two for recovery runs, never both (see "Retries are recorded too" in Step 3).
- **Weekly summary (Step 8):** a separate scheduled task, once a week, that asks this skill for the weekly summary.

New automations start disabled. Enable it, or the skill will never run.
