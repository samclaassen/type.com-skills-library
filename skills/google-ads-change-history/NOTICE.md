# Notice

This skill is adapted from **change-history-checker** in
[fourteenwm/ppc-ai-skills](https://github.com/fourteenwm/ppc-ai-skills)
at commit `90f0e419f2d820789d5e50eee1237ab9fd14dfc6` (2026-08-25).

- Original author: Kurt Henninger (Fourteen Web Media)
- License: MIT. The upstream license text is reproduced verbatim in `LICENSE.txt`.
- Adapted by Type for the public Skill Library, for agencies working across
  many clients.

## Modifications

- Renamed the skill to `google-ads-change-history` and rewrote `SKILL.md` for
  agency use. The upstream `README.md`, `rules.md`, `examples.md` and
  `references/history-windows.md` were not copied; the parts still relevant
  (window limits, automated-source triage, the 90-day wall, "never conclude
  compromise from change history alone") are folded into `SKILL.md`.
- Replaced `scripts/check_change_history.py` with a new
  `scripts/change_history.py`:
  - Primary source is now `change_event` (user email, client type, old/new
    values) for the last 30 days. `change_status` (upstream's only source) is
    kept only for days 31-90 of a requested window, and clearly labelled as
    having no actor data.
  - The account is resolved from the shared client profile
    (`clients/<slug>/client.json`, `accounts.google_ads_customer_id` and
    optional `google_ads_login_customer_id`) per `CLIENTS.md`, instead of a
    raw customer ID argument. The login customer ID is set per client, so one
    portfolio run can span different manager accounts.
  - Added a portfolio view (`--all-clients`) with every row labelled by client,
    and explicit "missing field" messages for profiles without a customer ID.
  - Output reworked: timeline grouped by day, per-user rollup, BUDGET/BID/STATUS
    highlighting with formatted old -> new values in the account currency, and
    a draft escalation summary with a suggested client reply.
  - Pinned Google Ads API `v25` explicitly in code and `google-ads==32.0.0` in
    `requirements.txt` (upstream used the library default and had no
    requirements file). v20 and v21 are already shut off.
  - Credentials load from `--google-ads-config`, `GOOGLE_ADS_CONFIGURATION_FILE_PATH`,
    `GOOGLE_ADS_*` env vars, `./google-ads.yaml` or `~/google-ads.yaml`
    (upstream required `./google-ads.yaml`). Secrets are never printed.
  - Customer IDs with dashes are accepted (upstream failed on dashed IDs).
  - Queries page past the 10,000-row `LIMIT` by timestamp (upstream silently
    capped at 500 rows).
  - API errors are reported as error codes plus request ID instead of a
    traceback.
  - Added `--fixture` (recorded rows, no API) and `--as-of` for deterministic
    offline runs.
  - Removed `--list-accounts`, `--types` and `--detailed` (asset text) to keep
    the skill focused on the who/what-changed question.
  - Removed the `pyyaml` dependency. Upstream had no Google Sheets, telemetry
    or self-update code; none was added.
- Added `CLIENTS.md` (shared contract), `DATA_CONTRACT.md`, `listing.json`,
  `requirements.txt`, and `examples/` (synthetic fixture, sample client
  profiles, `run.sh`, `expected_output.txt`).

- Packaging: the upstream `LICENSE` file is shipped as `LICENSE.txt` (text unchanged) so Type's skill installer accepts it.
- Added a `SETTING` highlight category (networks, locations, location
  targeting options, languages, ad schedule, start/end dates, campaign type)
  with human-readable field labels; these changes now appear in the escalation
  summary alongside budget and bid changes, and the suggested reply counts them.
- On/off (boolean) values are shown as `on` / `off` instead of `True` / `False`.
- The portfolio summary reports people and automated sources separately
  instead of a single "actor(s)" count.
- The suggested client reply for changes older than 30 days no longer promises
  follow-up work; it states that Google Ads does not keep user detail that far
  back.
