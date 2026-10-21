# Data contract

## Access

- **Read-only.** Only `GoogleAdsService.search` is called. No mutate
  services are imported or used. Nothing in any Google Ads account is changed.
- Google Ads API version: `v25` (pinned in `scripts/mcc_security_audit.py`),
  client library `google-ads==32.0.0`. v25 sunset: August 2027
  (https://developers.google.com/google-ads/api/docs/sunset-dates).
- Credentials: `google-ads.yaml` (`--config`) or `GOOGLE_ADS_*` environment
  variables. Required keys: developer token, OAuth client ID/secret and
  refresh token (or a service-account JSON key path), `login_customer_id`
  (10 digits, no dashes), `use_proto_plus: True`. Credential values are never
  printed or written by the script.
- Optional write, agency-owned only: `--sheet-id` clears and rewrites two tabs
  (`Security Findings`, `All Manager Links`) in a Google Sheet the operator
  names, via a `gspread` service account. `--csv-dir` and `--dump-rows` write
  local files.

## Queries (per run)

| Query | Scope | Fields |
|---|---|---|
| `customer_client` | once, on the login manager | `id`, `descriptive_name`, `status`, `manager`, `level` |
| `customer_manager_link` | every account in the tree | `manager_customer`, `manager_link_id`, `status` (no status filter) |
| `customer_user_access` | every account | `user_id`, `email_address`, `access_role`, `access_creation_date_time`, `inviter_user_email_address`, `passkey_enabled` |
| `customer_user_access_invitation` | every account | `invitation_id`, `email_address`, `access_role`, `invitation_status`, `creation_date_time` |

A failing query on one account is recorded under `errors` for that account and
does not stop the run.

## Inputs

- `clients/<slug>/client.json` (see `CLIENTS.md`). Fields read: `name`,
  `website` (host, minus `www.`, is the client's own email domain),
  `accounts.google_ads_customer_id`. Dashes in IDs are ignored.
- `--allowlist` JSON: `trusted_manager_ids` (object of ID to note, or list),
  `trusted_email_domains` (list; subdomains match), `trusted_emails` (list).
- `--hostile-list` JSON: object of manager ID to note.

## Recorded rows (`--fixture` input, `--dump-rows` output)

```json
{
  "login_customer_id": "1000000001",
  "as_of": "2026-09-20",
  "api_version": "v25",
  "accounts": [{"cid": "2015550101", "name": "...", "status": "ENABLED", "level": 1, "is_manager": false}],
  "manager_links": {"2015550101": [{"manager_cid": "4821169031", "link_id": 400977, "status": "PENDING"}]},
  "user_access": {"2015550101": [{"user_id": "21", "email": "...", "role": "STANDARD", "created": "YYYY-MM-DD HH:MM:SS", "inviter": "...", "passkey_enabled": false}]},
  "invitations": {"2015550103": [{"invitation_id": "9001", "email": "...", "role": "ADMIN", "status": "PENDING", "created": "YYYY-MM-DD HH:MM:SS"}]},
  "errors": {"2015550150": "customer_manager_link: ..."}
}
```

Enum values are the API enum names (`ACTIVE`, `PENDING`, `REFUSED`,
`CANCELED`, `INACTIVE`; `ADMIN`, `STANDARD`, `READ_ONLY`, `EMAIL_ONLY`;
invitation `PENDING`, `DECLINED`, `EXPIRED`). `as_of` is the reference date for
"recent"; `--as-of` overrides it.

## Outputs

- stdout: markdown report (default) or JSON (`--json`) with `totals`,
  `severity_counts`, `findings[]` (`id`, `severity`, `kind`, `client_slug`,
  `client_name`, `account_id`, `account_name`, `account_status`, `title`,
  `why`, `recommended_action`, `evidence`), `allowlisted_links[]`,
  `profiles_without_customer_id[]`.
- stderr: progress and errors.
- `--csv-dir`: `mcc_link_scan_YYYYMMDD.csv` (every manager link with
  classification) and `mcc_security_findings_YYYYMMDD.csv`. Same-day re-runs
  overwrite; different days accumulate for diffing.

## Sensitivity

Reports, CSVs and recorded rows contain user email addresses and the agency's
account structure. Treat them as internal security material: do not commit
them, do not send them to clients, and delete recorded rows when no longer
needed.
