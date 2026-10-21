# Notice

This skill is adapted from `mcc-hack-audit` in
[fourteenwm/ppc-ai-skills](https://github.com/fourteenwm/ppc-ai-skills) at
commit `90f0e419f2d820789d5e50eee1237ab9fd14dfc6` (2026-08-25).

- Original author: Kurt Henninger (fourteenwm)
- Original license: MIT, Copyright (c) 2026 Kurt Henninger. The full license
  text is in `LICENSE.txt` and applies to this adapted work.

## Modifications by Type

- Renamed to `mcc-security-audit` and rewrote `SKILL.md` for agency use across
  a client book; the upstream `rules.md` and `references/scan-contract.md`
  were condensed into `references/triage.md` and `DATA_CONTRACT.md`.
- Added user-access checks: `customer_user_access` (unknown users, admin
  count, recent grants, admins without passkeys) and
  `customer_user_access_invitation` (pending invitations). Upstream scanned
  manager links only.
- Added client mapping via `clients/*/client.json`
  (`accounts.google_ads_customer_id`) per the shared `CLIENTS.md`: every
  finding is labeled with its client, unmapped accounts are findings, and
  profiles pointing outside the tree are reported.
- Added an allowlist file (trusted manager IDs, email domains, emails),
  replacing upstream's `--trusted-cids` flag; allowlisted managers with live
  links are still listed in every report. Kept upstream's hostile list and
  its HOSTILE > INTERNAL > trusted > EXTERNAL precedence.
- Replaced the classification-only CSV output with severity-ranked findings
  (CRITICAL to INFO), each with exact account/manager IDs, evidence and a
  recommended manual action. Hostile rows with non-live link status are now
  reported as history instead of "actively linked" (upstream's banner noted
  this overstatement in its own contract). Added cross-account probing
  detection.
- Output is markdown to stdout by default, `--json` optional; CSV
  (`--csv-dir`) and Google Sheets (`--sheet-id`) are opt-in. Progress goes to
  stderr.
- Pinned Google Ads API `v25` in code and `google-ads==32.0.0` in
  `requirements.txt`; upstream used the library's default version.
- Credentials from `google-ads.yaml` or `GOOGLE_ADS_*` environment variables;
  config errors are reported in one line without secret values. Per-query
  errors are recorded per account and reported as scan-error findings
  instead of only a console count.
- Added `--fixture` (offline re-analysis of recorded rows), `--dump-rows`, and
  a deterministic offline example under `examples/`.
- Stopped selecting the unused `customer_client.hidden` and
  `customer_client.test_account` fields.
- Removed references to sibling skills from the upstream repository.

- Packaging: the upstream `LICENSE` file is shipped as `LICENSE.txt` (text unchanged) so Type's skill installer accepts it.
