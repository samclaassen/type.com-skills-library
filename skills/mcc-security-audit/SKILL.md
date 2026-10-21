---
name: mcc-security-audit
description: Read-only scan of an agency's Google Ads manager (MCC) account tree that flags unexpected manager links, unknown or over-privileged users, pending invitations, recent access changes and accounts not mapped to a client, ranked by severity with exact account IDs and a recommended action for each.
---

# MCC security audit

Answers one question across the agency's whole book: **who can get into our
clients' Google Ads accounts right now, and which of those did nobody
approve?** The Google Ads UI shows access one account at a time; attackers
who phish a client admin work across many accounts at once. This skill walks
every account under one login manager (MCC), reads its manager links, users
and open invitations, maps each account to a client, and returns a
severity-ranked list of findings.

Adapted from `mcc-hack-audit` in fourteenwm/ppc-ai-skills (MIT), built after
a real incident where hostile external MCCs were linked to client accounts
through compromised client admin credentials. See `NOTICE.md`.

**Read-only.** The script only issues `GoogleAdsService.search` queries. It
never removes, declines or edits a manager link, user or invitation, and it
never contacts a client. Every recommended action is a manual step in the
Google Ads UI that needs explicit approval in the conversation naming the
client and the change (CLIENTS.md rule 3).

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `mcc-security-audit`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use

- "Run an MCC security audit", "who has access to our client accounts",
  "check for unknown manager links", "any hostile MCCs in the book?"
- Quarterly baseline; immediately when a client reports unexpected campaign
  changes; after onboarding a client (check existing access) and after
  offboarding (check your own removal).

## Inputs

1. **Client profiles** (`clients/<slug>/client.json`, see `CLIENTS.md`).
   Each client's `accounts.google_ads_customer_id` maps a child account back to
   the client. `website` gives the client's own email domain, so client staff
   on that domain are not flagged as unknown on that client's account. If the
   directory is missing, the skill stops and says how to create it.
2. **Credentials** (live mode), either:
   - `--config google-ads.yaml` with `developer_token`, `client_id`,
     `client_secret`, `refresh_token`, `login_customer_id` (10 digits, no
     dashes) and `use_proto_plus: True`; or
   - environment variables `GOOGLE_ADS_DEVELOPER_TOKEN`,
     `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`,
     `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`,
     `GOOGLE_ADS_USE_PROTO_PLUS=True` (or `GOOGLE_ADS_JSON_KEY_FILE_PATH` for
     a service account), read by the client library's `load_from_env`.
   The script never prints credential values. Keep `google-ads.yaml` out of
   version control.
3. **Allowlist** (`--allowlist allowlist.json`, optional):
   ```json
   {
     "trusted_manager_ids": {"300-000-0009": "Client parent company MCC, confirmed 2026-05"},
     "trusted_email_domains": ["your-agency.com"],
     "trusted_emails": ["freelancer@example.com"]
   }
   ```
4. **Hostile list** (`--hostile-list hostile.json`, optional): manager ID to a
   one-line note. Start empty; add an ID only after an incident confirms it.

Managers inside your own tree are detected automatically (INTERNAL) and never
need allowlisting.

## How to run

```bash
pip install -r requirements.txt          # live mode only

# Live (read-only), markdown to stdout
python scripts/mcc_security_audit.py --config google-ads.yaml \
  --clients-dir clients --allowlist allowlist.json --hostile-list hostile.json

# JSON instead of markdown
python scripts/mcc_security_audit.py --config google-ads.yaml --clients-dir clients --json

# Keep datestamped CSVs for run-to-run diffs, and/or overwrite tabs in a Sheet
python scripts/mcc_security_audit.py ... --csv-dir output --sheet-id SHEET_ID

# Save raw rows from a live run, then re-analyze offline later
python scripts/mcc_security_audit.py ... --dump-rows rows.json
python scripts/mcc_security_audit.py --fixture rows.json --clients-dir clients

# Offline example (no credentials, no network)
bash examples/run.sh
```

Options: `--recent-days` (default 30) sets the window for "recent access
change"; `--max-admins` (default 3) flags accounts with more admin users;
`--workers` (default 10) sets live parallelism (lower it on
`RESOURCE_EXHAUSTED`); `--as-of YYYY-MM-DD` fixes the reference date.
Sheets output is optional and needs `gspread` plus a service account with
access to the sheet.

The script pins Google Ads API **v25** (`GOOGLE_ADS_API_VERSION`) with
`google-ads==32.0.0`. v20 and v21 are already shut off; v25 is supported
until August 2027. Check
https://developers.google.com/google-ads/api/docs/sunset-dates before that
date and bump both pins together.

## What it checks

For every account in the tree (all statuses, including your root MCC and
cancelled accounts):

| Resource | Used for |
|---|---|
| `customer_client` | the tree walk; internal managers; account name/status |
| `customer_manager_link` (all link statuses) | who manages the account, and attempt history |
| `customer_user_access` | users, role, grant date, inviter, passkey status |
| `customer_user_access_invitation` | open invitations |

Manager classification, first match wins: **HOSTILE** (on your hostile list;
beats everything, even a manager inside your tree) > **INTERNAL** (in your
tree) > **ALLOWLISTED** > **EXTERNAL** (everything else, never auto-cleared).

User classification: known if the email is allowlisted, on an allowlisted
domain, or on the client's own website domain for that client's account.
Everything else is unknown.

## Severity model

| Severity | Finding |
|---|---|
| CRITICAL | Hostile manager with an ACTIVE or PENDING link. Unknown ADMIN user granted inside the recent window |
| HIGH | PENDING link from an unknown manager (the pre-breach catch). ACTIVE link to an unknown manager. Unknown ADMIN user (older). Pending ADMIN invitation to an unknown email. Unknown STANDARD user granted recently |
| MEDIUM | Hostile manager in link history only. Unknown manager with a refused/cancelled link here and a live link elsewhere in the book (probing). Unknown STANDARD user. Pending STANDARD invitation to unknown email. More admins than `--max-admins`. ENABLED/SUSPENDED account not mapped to any client. Customer ID claimed by two profiles. Query errors on a live account |
| LOW | Unknown READ_ONLY / EMAIL_ONLY user (MEDIUM if granted recently). Pending READ_ONLY / EMAIL_ONLY invitation to unknown email. Other unknown-manager link history. Known admin without a passkey. Unmapped or errored cancelled/closed account. Client profile pointing at an account outside this tree |
| INFO | Known user granted access inside the recent window (confirm it matches a staffing change) |

Within a severity, findings sort by triage order, then newest manager link ID
first (link IDs increase over time and are the API's only link chronology),
then newest grant date.

## Output

Markdown (default) or `--json`, with:

- totals and a count per severity;
- each finding: severity, client name and slug (or `UNMAPPED`, `(agency login
  MCC)`, `(agency sub-manager)`), exact account ID, name and status, the
  evidence (manager ID, link ID and status, user email/role/grant date/inviter,
  invitation ID), why it matters, and a recommended manual action;
- allowlisted managers that still hold live links, so trusted access stays
  visible and gets re-audited;
- client profiles that have no Google Ads customer ID (not audited).

The report is internal. It contains user email addresses; do not paste it
to a client. Any client message drafted from it is a draft for the account
lead (CLIENTS.md rule 2).

## Reading the results

Work top down. `references/triage.md` has the full playbook; the short form:

1. **CRITICAL first.** A hostile manager with live access or a new unknown
   admin is incident mode: remove access in the UI (with approval), have the
   client rotate admin credentials, review change history since the access
   appeared.
2. **Pending links and invitations next.** They are pre-breach: decline them
   in the UI if nobody can name them.
3. **Unknown ACTIVE managers, newest link ID first.** Put a name on each ID in
   the UI (Admin > Access and security; the API cannot name external MCCs) and
   ask the client. Former agencies and parent companies are common; add them
   to the allowlist once confirmed.
4. **Unmapped accounts.** Assign an owner in `client.json` or unlink the
   account (with approval). Nobody reviews access on an account nobody owns.
5. **Errors are not clean results.** An errored account was not audited.

Never allowlist a manager or user just to make a finding go away. The
allowlist hides a trusted party from findings; it does not reduce what that
party can do.

## Limits

- **Live API calls are untested in this package.** The analysis, fixture
  mode and output are tested offline, and the live parsing code was exercised
  against mocked v25 rows; the queries have not been run against a real
  account tree. Run once on a small tree first.
- One login manager per run. Multiple top-level MCCs mean one run each.
- The API cannot resolve names of managers outside your tree, and
  `customer_manager_link` exposes no link timestamp. Link recency is inferred
  from `manager_link_id`, which is relative, not a date.
- `change_event` does not record manager-link acceptance, so the skill cannot
  say who accepted a link. Use Change History in the UI for that.
- "Recent access change" is based on `access_creation_date_time` of current
  grants. Removed users and role changes on existing users are not visible
  here.
- User and invitation queries may be refused on some accounts (permissions,
  cancelled or closed accounts). Those accounts appear as scan-error findings,
  not as clean.
- Known/unknown user classification is by email and domain only. A
  compromised trusted mailbox looks trusted.
- Large trees take time: three queries per account. Lower `--workers` if you
  see `RESOURCE_EXHAUSTED`.
- No watchdog mode. For link churn between runs, keep `--csv-dir` output and
  diff the newest two scans.

## Files

| File | Role |
|---|---|
| `scripts/mcc_security_audit.py` | Scan + analysis (live or `--fixture`) |
| `references/triage.md` | Triage order, escalation path, cadence, false alarms |
| `DATA_CONTRACT.md` | Inputs, fixture/row format, outputs |
| `examples/` | Synthetic tree with planted risks, profiles, allowlist, expected output |
| `CLIENTS.md` | Shared client-profile contract |
| `requirements.txt` | Pinned live-mode dependencies |
