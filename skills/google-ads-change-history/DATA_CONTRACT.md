# Data contract

## Access

Read-only. The script only calls `GoogleAdsService.Search` with GAQL `SELECT`
queries. It never calls a mutate method, and it writes no files.

## Credentials (live mode only)

The official `google-ads` Python library loads credentials. The script looks
for them in this order:

1. `--google-ads-config /path/to/google-ads.yaml`
2. The `GOOGLE_ADS_CONFIGURATION_FILE_PATH` environment variable, which points
   to a `google-ads.yaml`
3. `GOOGLE_ADS_*` environment variables, used when `GOOGLE_ADS_DEVELOPER_TOKEN`
   is set: `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`,
   `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, and optionally
   `GOOGLE_ADS_LOGIN_CUSTOMER_ID`. Service accounts use
   `GOOGLE_ADS_JSON_KEY_FILE_PATH`, and Application Default Credentials use
   `GOOGLE_ADS_USE_APPLICATION_DEFAULT_CREDENTIALS`.
4. `./google-ads.yaml` in the working directory
5. `~/google-ads.yaml`, the library default

A minimal `google-ads.yaml`:

```yaml
developer_token: INSERT_DEVELOPER_TOKEN
client_id: INSERT_OAUTH_CLIENT_ID
client_secret: INSERT_OAUTH_CLIENT_SECRET
refresh_token: INSERT_REFRESH_TOKEN
login_customer_id: 9876543210   # optional default MCC; client.json overrides it per client
use_proto_plus: True
```

The account needs at least read access to each client's Google Ads customer.
If a client's `accounts.google_ads_login_customer_id` is set, it overrides
`login_customer_id` for that client. The script never prints or logs credential
values. Keep credential files outside the skill folder and out of version
control.

## Client profile fields read

From `clients/<slug>/client.json` (see `CLIENTS.md`):

| Field | Required | Use |
|---|---|---|
| `name` | yes | Report header and resolving the client by name |
| `accounts.google_ads_customer_id` | yes | Customer ID to query (dashes allowed) |
| `accounts.google_ads_login_customer_id` | no | Manager (MCC) ID sent as `login-customer-id` |
| `website` | no | Flags changes made from the client's own email domain |

## API

- Google Ads API **v25**, pinned in `scripts/change_history.py` (`API_VERSION`).
  Google's schedule lists v25 sunset as August 2027.
- Client library **google-ads==32.0.0**, pinned in `requirements.txt`. It
  supports v21 through v25. The library prints deprecation warnings on Python 3.9, so use Python 3.10 or later.
- To upgrade, bump both together and re-check the field list below against
  the new version's `change_event` and `change_status` reference pages.

## Fields read

`change_event` (range: last 30 days; `LIMIT` of 10,000 or less, paged by
timestamp, up to 10 pages):

- `change_event.resource_name`, `change_event.change_date_time`,
  `change_event.change_resource_name`, `change_event.user_email`,
  `change_event.client_type`, `change_event.change_resource_type`,
  `change_event.old_resource`, `change_event.new_resource`,
  `change_event.resource_change_operation`, `change_event.changed_fields`,
  `change_event.campaign`, `change_event.ad_group`
- Attributed fields: `campaign.name`, `ad_group.name`, `customer.currency_code`

`change_status` (only for window days older than 30; range: last 90 days;
`LIMIT` of 10,000 or less, paged):

- `change_status.resource_name`, `change_status.resource_type`,
  `change_status.resource_status`, `change_status.last_change_date_time`
- Attributed fields: `campaign.name`, `ad_group.name`

From `old_resource`/`new_resource`, the script keeps only the paths listed in
`changed_fields`, plus `name`, `keyword.text` or `text_asset.text` to label
the changed item.

## Outputs

- Plain-text report on stdout: header, timeline by day, by-user rollup, older
  `change_status` counts (if the window goes past 30 days), and one escalation
  summary with a suggested reply per client. Everything is marked as a draft.
- Contains user email addresses of people who changed the account. Treat it as
  internal until the account lead has reviewed it.
- Exit codes: `0` report printed; `1` no client could be reported (every one
  skipped); `2` bad arguments, missing profile or field, or a window beyond 90
  days.

## Fixture format (`--fixture`)

```json
{
  "as_of": "YYYY-MM-DD",
  "customers": {
    "<10-digit customer id>": {
      "currency_code": "USD",
      "change_event":  [{"change_date_time": "...", "user_email": "...", "client_type": "GOOGLE_ADS_WEB_CLIENT",
                         "change_resource_type": "CAMPAIGN_BUDGET", "resource_change_operation": "UPDATE",
                         "change_resource_name": "...", "campaign_name": "...", "ad_group_name": "...",
                         "entity_name": "...", "changed_fields": ["amount_micros"],
                         "old": {"amount_micros": 50000000}, "new": {"amount_micros": 75000000}}],
      "change_status": [{"last_change_date_time": "...", "resource_type": "CAMPAIGN", "resource_status": "CHANGED",
                         "resource_name": "...", "campaign_name": "...", "ad_group_name": ""}]
    }
  }
}
```

This is the same normalized shape that the live path builds from API rows.
Rows outside the requested window are filtered the same way as in live mode.
