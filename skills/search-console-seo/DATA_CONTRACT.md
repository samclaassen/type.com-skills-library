# Data contract — search-console-seo

All inputs are UTF-8 CSV with one header row, comma separated, plus two JSON
config files. Column order does not matter. Header names are matched
case-insensitively, and the Search Console UI names are accepted as aliases
(`Top queries` = `query`, `Top pages` = `page`, `Clicks`, `Impressions`, `CTR`,
`Position`). Numbers may contain thousands separators (`1,234`).

The CSVs carry no dates. The script works out the two windows from `--as-of`,
`compare_period` and `data_lag_days` (see "Periods" below). Pull each file for
exactly those windows. `--plan` prints them.

## Modes

| Mode | Inputs | What you lose |
| --- | --- | --- |
| **Full** (preferred) | `--current` and `--prior` with `query` **and** `page` columns, from the API | Nothing |
| **Reduced** | `--current`/`--prior` = Queries export (`query` column only); optional `--current-pages`/`--prior-pages` = Pages export | Cannibalization is reported as **not evaluated**. Declining pages have no top query. Without the Pages files, declining pages are **not evaluated** too. |

Both periods must have the same shape. Mixing a full file with a Queries-only
file exits 2.

## 1. Period metrics: `--current`, `--prior` (required)

One row per `query` × `page` (full) or per `query` (reduced) for one period.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `query` | string | yes | Search query exactly as Search Console reports it. Must not be empty. |
| `page` | string | full mode | Absolute URL (`https://harborandvine.example/wine-club`). URLs on the property's host print as paths. |
| `clicks` | integer ≥ 0 | yes | Must not exceed `impressions`. |
| `impressions` | integer ≥ 0 | yes | |
| `ctr` | fraction or percent | no | `0.0306` or `3.06%`. It is validated, then ignored: CTR is always recomputed as clicks ÷ impressions so that aggregates stay consistent. Blank is allowed. |
| `position` | number ≥ 1 | yes | Search Console's average position for the row. |

The same (`query`, `page`) pair can't appear twice in a file. A blank value in a
required column is reported as missing, never filled in.

### Producing it from the API (full mode)

`searchanalytics.query`, read-only scope
`https://www.googleapis.com/auth/webmasters.readonly`:

```
POST https://searchconsole.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query
{
  "startDate": "2026-08-29", "endDate": "2026-09-25",
  "dimensions": ["query", "page"],
  "type": "web", "dataState": "final",
  "rowLimit": 25000, "startRow": 0
}
```

- `{siteUrl}` is the property, URL-encoded: `sc-domain%3Aexample.com` or
  `https%3A%2F%2Fwww.example.com%2F`.
- **Paging:** repeat the request with `startRow` 25000, 50000, … until a
  response returns fewer than 25,000 rows or has no `rows` key. Save every page.
- Response rows look like `{"keys": ["<query>", "<page>"], "clicks": 12.0,
  "impressions": 340.0, "ctr": 0.0353, "position": 4.2}`.
- Convert all pages of one request in a single call (the converter sorts rows and
  rejects overlapping pages):

```bash
python3 gsc_api_to_csv.py --dimensions query,page --out current.csv resp_cur_0.json resp_cur_25000.json
```

Run the same request for the prior window to get `prior.csv`.

### Producing it from the Search Console UI (reduced mode)

Performance → Search results, search type **Web**, set a **custom date range**
(not "Compare") for one window, then Export → CSV. The download is a zip with
`Queries.csv`, `Pages.csv`, `Devices.csv`, `Countries.csv` and others. Use
`Queries.csv` as `--current` and `Pages.csv` as `--current-pages`. Repeat for
the prior window. Leave the UI headers as they are. In compare mode the UI puts
both periods in one file with prefixed column names. The script does not read
that layout, so export each window on its own.

## 2. Device and country splits: `--devices`, `--countries` (optional, current period)

Same columns as above, with `device` or `country` in place of `page`.

| Column | Notes |
| --- | --- |
| `device` | `DESKTOP`, `MOBILE` or `TABLET` (any case). Anything else exits 2. |
| `country` | ISO 3166-1 alpha-3 as the API returns it (`usa`, `gbr`). Printed upper-case. |

API: same request with `"dimensions": ["query", "device"]` or
`["query", "country"]` for the current window only. The API cannot OR-filter a
list of queries, so pull the whole property and let the script pick the top
`breakdown_top_queries` queries by current clicks. The UI's `Devices.csv` and
`Countries.csv` are site-level (no query column), so they can't be used here.

## 3. Property totals: `--totals` (optional)

| Column | Notes |
| --- | --- |
| `period` | `current` or `prior`, one row each. |
| `clicks`, `impressions`, `position` | Property totals for the window. `ctr` optional. |

API: the same request with `"dimensions": []` (one row, no `keys`). Convert both
windows into one file:

```bash
python3 gsc_api_to_csv.py --dimensions "" --set period=current --out totals.csv resp_tot_cur.json
python3 gsc_api_to_csv.py --dimensions "" --set period=prior --out totals.csv --append resp_tot_pri.json
```

**Why it matters:** rows with a `query` dimension leave out anonymized
(privacy-filtered) queries, so the sum of query rows is lower than the property
total. With `--totals`, the summary line and the low-volume check use the real
totals. Without it they use the export rows, and the report says so.

## Periods

```
current_end   = as_of − data_lag_days            (default 3: Search Console lags ~2-3 days)
current_start = current_end − (compare_period − 1)
prior_end     = current_start − 1
prior_start   = prior_end − (compare_period − 1)
```

Both windows are inclusive and equal in length. `--as-of` is the only source of
"today". `python3 gsc_analyze.py --plan --property … --as-of …` prints
the windows and the request bodies, including `rowLimit`, `startRow`, `type` and
`dataState`.

### New properties (prior period without data)

If the prior window has no data, the script exits 2 and says so instead of
comparing against nothing. To find the largest window the data supports, run
the `first_data_date_probe` request that `--plan` prints (dimension `date`).
The earliest date in the response is the first date with data. Pass it as
`--first-data-date YYYY-MM-DD` to `--plan` or to the analysis. If the prior
window would start before that date, the script exits 2 and names the largest
`compare_period` that fits:

```
largest compare_period = floor((current_end − first_data_date + 1 day) ÷ 2)
full window available  = first_data_date + 2 × compare_period − 1 + data_lag_days  (as --as-of)
```

## How metrics are combined

- Clicks and impressions are summed.
- **Position is impressions-weighted:** `Σ(position × impressions) ÷ Σ impressions`.
  A plain average of the row positions would be wrong.
- CTR is `clicks ÷ impressions` of the aggregate.
- In full mode, query-level figures are summed from query × page rows. When two
  of your URLs show for the same search, Search Console's query view counts one
  impression, but these rows count two. Query impressions for split queries can
  therefore run higher than the Queries tab. Page-level figures are not affected.

## 4. `settings.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `gsc_property` | `""` | Property, e.g. `sc-domain:example.com`. `--property` overrides it. One of the two is required. |
| `compare_period` | 28 | Days per window. |
| `data_lag_days` | 3 | Days between `--as-of` and the end of the current window. |
| `min_impressions` | 50 | Queries under this (current period) are ignored by CTR, rising, cannibalization and page-1 checks. |
| `min_position_change` | 3 | A query counts as rising when its position improved by at least this. Also the device-gap note threshold. |
| `ctr_benchmark` | `auto` | `auto` = `ctr_curve.json`; `custom` = `ctr_curve_custom`. Blank is treated as `auto`. |
| `ctr_curve_custom` | `{}` | `{"1": 0.31, "2": 0.17, …}`. Positions as keys, CTR fractions as values, at least 2 points. |
| `cannibalization_threshold` | 2 | Minimum number of URLs (each above the share floor) for a query to count as split. |
| `cannibalization_min_impression_share` | 0.10 | Each URL needs at least this share of the query's impressions. Stops a stray 1-impression URL from counting. |
| `post_to` | `""` | Where the skill posts the report (used by the agent, not the script). |
| `low_volume_click_threshold` | 1000 | Below this many current clicks the report warns and assigns no priorities. |
| `max_rows_per_section` | 10 | Table rows shown per section. `--json` has all of them. |
| `max_priority_actions` | 15 | Lines shown in Prioritized actions. |
| `decline_min_drop_pct` | 20 | A page must lose **more** than this % of clicks. |
| `decline_min_impressions` | 100 | …and have **more** than this many impressions in either period (the larger of the two, so pages that disappeared still count). |
| `decline_min_click_loss` | 10 | …and lose at least this many clicks, so 8 → 5 clicks is not flagged. |
| `cause_min_position_worsening` | 1.0 | Position worsened by at least this → "Avg position a→b". |
| `cause_min_impression_drop_pct` | 15 | Impressions fell by at least this % → noted. |
| `cause_min_ctr_drop_pct` | 20 | Relative CTR fall at a similar position → noted. |
| `ctr_max_position` | 5.0 | CTR opportunities: average position ≤ this. |
| `ctr_min_gap_pp` | 1.0 | CTR must sit at least this many points below expected. |
| `rising_min_impression_growth_pct`, `rising_min_impression_gain` | 30, 100 | Impression-based rising: both must be met. |
| `rising_new_query_min_impressions` | 200 | A query with no prior-period row is listed as new at or above this. |
| `page1_min_position`, `page1_max_position` | 8, 12 | Page-1 window (inclusive). |
| `page1_target_position` | 5 | Position used for the est. gain. |
| `breakdown_top_queries`, `breakdown_top_countries` | 5, 3 | Device/country table size. |
| `breakdown_min_country_share` | 0.01 | Countries with zero clicks or under this share of the query's clicks are left out of the Top countries cell. Optional. |
| `brand_terms` | `[]` | Case-insensitive whole words, e.g. `["harbor and vine", "harborvine"]`. Anything that is not a letter or digit (space, `.`, `-`) counts as a word boundary, so `type` matches "type.com" and "type com" but not "typeform"; spaces in a term match any separator. List spelled-together variants (`harborvine`) separately or use `exclude_query_patterns`. Matching queries are left out of CTR opportunities and Page-1 opportunities (brand queries have their own CTR pattern). The report prints how many were left out; `--json` lists them under `excluded_by_query_filter`. Optional. |
| `exclude_query_patterns` | `[]` | Case-insensitive Python regular expressions (for example `"^harb[eo]r.?(and)?.?vine"` for brand typos), applied to the same two lists. An invalid pattern exits 2. Optional. |
| `priority_rules` | see file | Per finding type: `metric` (fixed, below) plus `critical` and `high` thresholds (number or `null`). At or above `critical` → critical, else at or above `high` → high, else medium. |
| `section_actions` | see file | The `→ Action:` line printed under each table. |
| `finding_actions` | see file | Per-finding action templates. Placeholders: `{page}`, `{query}`, `{position}`, `{gap}`. When there is no real query or URL to insert (reduced mode), a plain phrase such as `its top query` is used and the quotes around the placeholder are dropped. |

Priority metrics: `declining_page` = observed click loss; `ctr_opportunity` and
`page1_opportunity` = est. click gain; `rising_query` = observed impression gain;
`cannibalization` = the query's current impressions. Only critical and high
findings are counted in the summary and listed under Prioritized actions.

## 5. `ctr_curve.json`

`{"positions": {"1": 0.28, "2": 0.155, …}}`. Positions are linearly
interpolated. Past the last point, the last value is used. These are generic,
rounded figures. They are not this site's CTRs and not any one study's exact
numbers. Brand queries, SERP features and mobile-heavy audiences move real CTR
far from them. Use `ctr_benchmark: custom` with your own curve when you have one.

## Estimates vs observed

Observed: every click, impression, CTR and position figure, page click loss,
impression gain, and URL shares. Estimated (printed with "est."): the CTR click
gain (`impressions × (expected − actual CTR)`) and the page-1 gain
(`impressions × (expected CTR at target − actual CTR)`). Both assume impressions
stay the same. "Likely cause" only describes what changed in the data. It is
never a confirmed cause.

## Failure modes

Exits `2` and names the file, row and column for: a missing or empty file; a
missing required column; a duplicate header; unexpected dimension columns;
periods of different shapes; a blank, non-numeric or negative count; a position
below 1; clicks above impressions; a duplicate row key; a bad device value;
malformed totals; invalid config keys or types; an invalid
`exclude_query_patterns` regex; a custom curve with fewer than 2 points; a
missing property; and a period with no data rows (for an empty prior period the
message suggests a shorter `compare_period`). It never silently drops a row.

## Sample data

`sample_*.csv` describe **fictional** properties
(`harborandvine.example`, a made-up wine shop, and `tidepoolpottery.example`, a
made-up studio used for the low-volume test). They were generated
deterministically and contain no real site or customer data. `sample_ui_*` mirror
the UI export layout. `sample_api_response.json` is one response page in the API
shape.
