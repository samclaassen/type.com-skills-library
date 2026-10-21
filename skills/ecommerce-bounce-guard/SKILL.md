---
name: ecommerce-bounce-guard
description: Catch and fix email bounce spikes caused by bot or fake Shopify checkouts that enroll invalid addresses in Klaviyo abandoned-checkout and welcome flows. Finds the flows that are bouncing, dates the first bad day, tells bot traffic apart from list decay, and gives the exact filters, Shopify fixes and reputation-recovery plan to protect deliverability before BFCM. Read-only.
---

# Ecommerce Bounce Guard

Since late September 2026, bots have been creating fake checkouts on Shopify stores. Each fake checkout fires `Checkout Started` with an invalid email, which enrolls it in the abandoned-checkout flow, and often the welcome flow too. The emails hard-bounce. One merchant found his first abandoned-cart email bouncing **1,344 of 1,597 times (84%)** and his welcome email **552 of 851 (65%)**, two weeks after it started. Other merchants saw the same on Klaviyo and Omnisend. The flows stayed "live" and revenue looked normal, so nobody noticed.

Mailbox providers read a high hard-bounce rate as a dirty or bought list. Sender reputation drops fast and takes months to rebuild, and this is the worst time of year for it to happen.

This skill finds the damage, proves the cause, and lays out the fix in four layers: **stop the bleeding in the ESP → block the bots in Shopify → validate addresses at capture → rebuild reputation before peak.**

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `ecommerce-bounce-guard`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite, including the live fetcher against a mock Klaviyo API.

## When to use this

- Someone says bounces are up, deliverability dropped, or "abandoned cart is bouncing".
- Shopify shows a burst of abandoned checkouts with no matching orders.
- A pre-BFCM deliverability check on a Shopify + Klaviyo store, or on each account an agency manages.
- On a schedule. Daily from October through December, weekly otherwise. Stay silent when the report says no flow is above threshold.

## Operating rules

1. **Read-only.** The scripts only read. Never pause a flow, change a filter, suppress profiles, or change Shopify settings unless the user approves that specific change. When they do, make it, read the setting back, and re-run the audit 48–72 hours later to confirm it worked.
2. **Recommend, don't auto-pause.** These flows make money. State the bounce numbers and the tradeoff, and let the person decide.
3. **Keep bounces and spam placement separate.** A seed or inbox-placement test does not measure bounces.
4. **Quote exact numbers**: X of Y, the percentage, and the first bad day. Those are what support teams need.
5. **Mask addresses.** The report masks them. Never paste raw bounce lists into chat. The bundle file contains real emails, so keep it local and delete it when done.
6. Say which state each item is in: **detected**, **recommended**, **applied**, or **verified**.

## Gathering the inputs

The analyzer reads one **bounce bundle** (JSON). Its exact shape is in `DATA_CONTRACT.md`. There are two ways to get one.

**A. Live from Klaviyo (recommended).** You need a Klaviyo **private API key** with read scopes only: `flows:read`, `metrics:read`, `events:read`, `profiles:read`. Create one in Klaviyo under Settings → API keys → Create private API key → Custom, read access for those four. In Type, connect the Klaviyo integration and run the fetcher with the credential the connection provides. Pass its variable name with `--key-env` if it isn't `KLAVIYO_API_KEY`. Never ask the user to paste a key into chat.

```bash
KLAVIYO_API_KEY=pk_... python3 scripts/klaviyo_fetch.py \
  --account "Store name" --out bundle.json
```

It pulls 12 weeks of weekly and 21 days of daily flow statistics, the most recent 2,000 bounce events with profile email and creation date, and checks up to 50 bounced profiles for any `Active on Site` event. Klaviyo allows only 2 flow reports per minute (225 per day). The fetcher makes two, waits between them, and takes about a minute. Options: `--as-of YYYY-MM-DD`, `--weeks`, `--days`, `--max-bounces`, `--aos-sample`, `--conversion-metric` (the report endpoint requires one; the default is `Placed Order`).

**B. By hand** (another ESP, or no API access): build the bundle from your ESP's flow reports and bounce export, following `DATA_CONTRACT.md`. Omnisend, Mailchimp and other ESPs work the same way. See `references/playbook.md` → Other ESPs.

**Optional, recommended: Shopify daily counts.** A CSV of `date,abandoned_checkouts,orders` covering the same 21 days. Get it from Shopify Analytics, or count from Orders → Abandoned checkouts and Orders. With it, the report checks whether checkouts jumped while orders stayed flat, which is the clearest bot signature.

## Running it

```bash
python3 scripts/bounce_audit.py --bundle bundle.json [--shopify-daily shopify_daily.csv] [--json analysis.json]
```

`bash examples/run.sh` runs it on the bundled sample store (Fernhill Goods, fictional, modeled on the real incident).

What it does:

1. **Scores every flow.** It compares the latest week's bounce rate with the flow's own baseline from the 10 weeks before. **Warning ≥ 2%. Critical ≥ 5%, or ≥ 3× baseline once above 2%.** Flows with fewer than 25 recipients in the week are listed as not scored. Healthy ecommerce flows run well under 1%.
2. **Names the worst email** in each flagged flow. Bots bounce on email 1; later emails look fine because the addresses are already suppressed.
3. **Finds the first bad day.** This is the first day with at least 5 bounces, at least 5% bounced (and 3× baseline), that stays high on the following days, so a one-day blip doesn't count. It reports the clean days before it and every other flow that broke on the same day.
4. **Fingerprints who is bouncing.** Hard vs soft bounces, profiles created less than 48 hours before bouncing, profiles that never fired Active on Site (scripted checkouts don't run Klaviyo's onsite JavaScript), machine-generated address patterns, top domains and sources.
5. **Gives a verdict.** `LIKELY BOT / FAKE-CHECKOUT TRAFFIC` needs a dated step change plus at least two independent bot signals. `POSSIBLE BOT TRAFFIC — CONFIRM BEFORE ACTING` means one signal. `LIKELY LIST-HYGIENE PROBLEM` means the bounces come from old, real profiles. `HEALTHY` means nothing is above threshold.
6. **Lists next steps** that fit the verdict.

Exit codes: 0 means the report was written, 2 means invalid input (with the reason). The fetcher returns 1 for API errors, with a scope hint on 401/403, and 2 when the key is missing.

## Acting on the report

Present the report, then walk through the fixes in this order. Each one needs the user's approval. Exact settings and UI paths are in `references/playbook.md`.

1. **Flow filters (the main fix).** Add *Active on Site at least once in the last 30 days* and *Bounced Email zero times over all time* to the flagged flows. Add trigger filters *$value > 0* and *Item Count ≥ 1*. Real shoppers stay in the flow and scripted checkouts drop out. Check first that Klaviyo onsite tracking is installed. Without it, the Active on Site filter blocks real customers too.
2. **Manual mode as a stopgap.** If the filters can't go in today, set the bouncing email to Manual so sends queue instead of bouncing. Review and clear the queue before going live again.
3. **Block bots at the source.** Shopify bot protection (hCaptcha) on checkout, login and forms. Edge rate limits on `/cart` and `/checkouts`. A Shopify support ticket with the onset date.
4. **Validate addresses at capture.** Real-time verification before email 1. Suppression alone won't fix this: hard bounces are already auto-suppressed and the bots keep creating new addresses. Only suppress queued bot profiles after spot-checking samples.
5. **Rebuild reputation.** Authentication (SPF, DKIM, DMARC, one-click unsubscribe), engaged-only sends, volume ramps of 20–30% per send day, and daily checks of Google Postmaster Tools, until bounces on new sends are under 1% and spam complaints are under 0.1%.
6. **Report and escalate.** Use the report and the support-ticket drafts in `references/playbook.md`. Drafts only; never send without approval.

## Files

- `scripts/bounce_audit.py`: the analyzer. Local files only, deterministic output.
- `scripts/klaviyo_fetch.py`: read-only Klaviyo fetcher that writes a bundle.
- `DATA_CONTRACT.md`: the bundle and Shopify CSV formats, and how to build them by hand.
- `references/playbook.md`: exact flow filters, Klaviyo UI paths, Shopify hardening, suppression rules, the recovery ramp, other ESPs, and report and support-ticket templates.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`: sample run. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`, `tests/mock_klaviyo.py`: 9 end-to-end checks, including fetch → analyze against a mock Klaviyo API. No network needed.
