---
name: google-ads-change-history
description: Answer "what changed on this client's Google Ads account, and who changed it?" for up to 90 days, as a read-only timeline grouped by day and by user with budget, bid, status and campaign-setting changes highlighted and a draft escalation summary for replying to the client.
---

# Google Ads change history

Use this skill when an account lead asks what changed on a client's Google Ads
account, who changed it, or needs to answer a client question such as "why did
our budget go down last week?" or "who paused the bedding campaign?". It also
gives a portfolio view across every client profile, with each row labelled by
client.

**Read-only.** The script only runs GAQL `SELECT` queries. It never changes a
campaign, budget, bid, status or any other setting, and it writes nothing to
disk. Output goes to the terminal.

## Get the skill's files first

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `google-ads-change-history`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Before you run it

1. **Client profile.** Resolve the client from `clients/<slug>/client.json`
   (see [CLIENTS.md](CLIENTS.md)). The skill needs
   `accounts.google_ads_customer_id`. If you reach the account through a
   manager (MCC) account, also set `accounts.google_ads_login_customer_id`.
   If a field is missing, the script names it, explains how to add it and
   stops. It does not guess.
2. **Credentials.** Use a standard `google-ads.yaml` or `GOOGLE_ADS_*`
   environment variables. See [DATA_CONTRACT.md](DATA_CONTRACT.md). Never paste
   credentials into the conversation.
3. **Install** (live mode only): `pip install -r requirements.txt`. This pins
   `google-ads==32.0.0`, and the script pins Google Ads API `v25`.

## Run

Run from the directory that contains `clients/`. You can also pass
`--clients-dir`.

```bash
# One client, last 30 days (the default window)
python3 scripts/change_history.py --client harbor-and-vine

# One client, full 90 days: days 1-30 include who changed what; days 31-90 show only which items changed
python3 scripts/change_history.py --client "Harbor & Vine" --days 90

# A specific range (must be inside the last 90 days)
python3 scripts/change_history.py --client harbor-and-vine --start 2026-09-01 --end 2026-09-15

# Portfolio view across every client profile, every row labelled [client-slug]
python3 scripts/change_history.py --all-clients --days 7

# Explicit credentials file
python3 scripts/change_history.py --client harbor-and-vine --google-ads-config ~/secure/google-ads.yaml

# Offline: recorded rows, no API, no credentials (see examples/)
bash examples/run.sh
```

`--fixture <json>` reads recorded `change_event`/`change_status` rows instead
of calling the API. Use it to test the formatting and aggregation or to replay
an export. `--as-of` sets "today" for fixture runs only.

## What the output contains

1. **Header:** client, customer ID, window, and which data source covers which
   dates. It is always marked read-only and draft.
2. **Timeline by day** (oldest first, in the account's time zone). Each line
   shows time, a tag (`[BUDGET]`, `[BID]`, `[STATUS]`, `[SETTING]`, or `[other]`), who made
   the change and which tool they used (Web UI, Editor, API, scripts,
   automated rule, or auto-applied recommendation), what changed, and old and
   new values. Money values use the account currency. Target ROAS appears as a
   percentage, and on/off settings appear as `on` / `off`.
   - `BUDGET`: campaign budget changes, or a campaign moved to another budget.
   - `BID`: bid strategy, target CPA/ROAS, max CPC/CPM/CPV, and bid
     adjustments.
   - `STATUS`: status updates such as pause or enable, and removals.
   - `SETTING`: material campaign settings — networks (Search partners,
     Display Network, YouTube), locations and location targeting options,
     languages, ad schedule, start/end dates and campaign type. Bid strategy
     changes are tagged `BID`.
3. **By user:** change count per person or automated source, broken down by
   category, with the date span and tools used.
4. **Older changes** (only when the window goes past 30 days). This section
   comes from `change_status` and counts which resources changed per day and
   type. It has **no user and no old/new values**.
5. **Escalation summary** (one per client). It lists every budget, bid and
   campaign-setting change (up to 10, then a pointer to the timeline), status outcomes, who made changes, automated sources, changes made
   from the client's own email domain (from `website` in client.json), weekend
   activity, row-cap warnings, and a neutral **suggested reply**. The reply
   only states what the history shows; it does not promise any follow-up, so
   add one yourself if the account lead agrees to it.
6. **Portfolio summary** (`--all-clients` only): per client, change counts by
   category, then how many distinct people (user emails) and how many distinct
   automated sources made them, counted separately.

## How to use the result

- Present the summary to the account lead as a **draft**. Do not send anything
  to the client automatically (CLIENTS.md rule 2).
- Before you attribute a change to a person, check automated sources:
  auto-applied recommendations, automated rules, and scripts explain many
  "nobody did this" questions.
- If a change came from an unknown email address, flag it to the account lead
  as an access question. Do not conclude that the account was compromised.
  The Google Ads UI shows who has access.
- Never mix clients. A single-client run reads only that client's customer ID.
  The portfolio view labels every row.
- This skill does not make fixes. If the lead wants something changed back,
  that is a separate write action. It needs explicit approval in the
  conversation that names the client and the change (CLIENTS.md rule 3).

## Limits

- **30 days of "who".** Google Ads `change_event` only returns changes from
  the last 30 days. That is the only API source that includes user email,
  tool, and old/new values. The query must also include a date range and a
  `LIMIT` of 10,000 or less. The script pages past the limit by timestamp, up
  to 100,000 rows.
- **90 days of "what".** For days 31 to 90, the script uses `change_status`.
  It returns one row per resource, dated by that resource's latest change. It
  has no user, no tool, and no old/new values. If a resource changed again
  later, it drops out of the older part of the window.
- **Nothing older than 90 days** is available through the API. Older changes
  (about 2 years) are in the Google Ads UI under Tools > Change history, which
  can export CSV.
- **change_event is not the full UI log.** Google says it may omit some rows
  shown in the UI's Change History. Not every client type records events. It
  can also lag by up to about 3 minutes.
- **Not captured:** who accepted a manager-account link or changed user
  access; billing and payment changes; many changes Google makes on its own
  (for example automated bidding adjustments inside a Smart Bidding strategy,
  and Performance Max asset serving). Some resource types come back as
  `UNKNOWN`. Performance data is not included, so this skill does not explain
  *why* results moved.
- **Automated changes** from rules, scripts, or auto-applied recommendations
  often have no user email. The report labels the tool instead.
- **Time zone:** timestamps are in the account's time zone. The window is
  calculated from the local date where the script runs, so edge days can be
  off by one across time zones.
- **Currency:** `*_micros` values are divided by 1,000,000 and labelled with
  the account currency. Budget amounts are shown as stored and are not
  converted to a period.

## Files

| File | Purpose |
|---|---|
| `scripts/change_history.py` | Read-only GAQL queries, normalization, and report |
| `requirements.txt` | Pinned `google-ads` client library (live mode only) |
| `examples/` | Synthetic fixture, sample client profiles, `run.sh`, `expected_output.txt` |
| `CLIENTS.md` | Shared client-profile contract |
| `DATA_CONTRACT.md` | Credentials, API version, fields read, outputs |
| `NOTICE.md`, `LICENSE.txt` | Upstream attribution (MIT) and list of modifications |
