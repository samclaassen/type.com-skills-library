# Data contract — ad-account-audit

Four CSV files, UTF-8, comma separated, with a header row (a BOM is tolerated).
All four describe **one ad account** over **one date range**: the range given by
`--period-start` and `--as-of`. Extra columns are ignored.

Numeric fields accept `$` and thousands separators. Negative numbers are
rejected everywhere — net out refunds and credits upstream.

Campaign names are the join key. Every `campaign_name` in `ads.csv`,
`keywords.csv` and `search_terms.csv` must exist in `campaigns.csv`, with one
exception: `keywords.csv` accepts the literal value `ACCOUNT` for shared,
account-level negative lists. An unmatched name is a hard error, because a
silent join miss looks identical to a clean account.

---

## 1. `campaigns.csv` — campaign delivery and budgets

One row per campaign. Duplicate `campaign_name` values are an error.

**Source:** Google Ads → Campaigns view, date range set to the audit window,
columns Campaign, Campaign status, Budget, Cost, Impr., Clicks, Conversions,
Conv. value. Meta Ads Manager → Campaigns tab, columns Campaign name, Delivery,
Budget, Amount spent, Impressions, Link clicks, Results, Purchase conversion
value. Microsoft Advertising exports the same shape.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `campaign_name` | text | — | yes | Join key. Unique within the file. |
| `status` | text | — | yes | `ENABLED`, `PAUSED` or `REMOVED` (case-insensitive). Only `ENABLED` campaigns are checked; the others still count toward total spend and are listed in the audit notes. |
| `channel` | text | — | no | e.g. `search`, `shopping`, `display`, `video`, `pmax`. Recorded for context, not used in the math. |
| `daily_budget` | number | currency/day | yes | The campaign's daily cap. Use `0` for campaigns on a shared or portfolio budget; pacing is then skipped and noted. |
| `spend` | number | currency | yes | Cost over the audit window. |
| `impressions` | number | count | yes | |
| `clicks` | number | count | yes | |
| `conversions` | number | count | yes | Conversions over the window. `0` triggers the tracking check above `--tracking-floor`. |
| `conversion_value` | number | currency | yes | `0` is valid for lead-gen accounts. |

---

## 2. `ads.csv` — ad status, policy state and last edit

One row per ad (or per asset group for Performance Max).

**Source:** Google Ads → Ads & assets → Ads, with the "Status" and "Policy
details" columns and the "Last modified" column enabled (Google Ads' change
history holds the edit date if the column is unavailable). Meta Ads Manager →
Ads tab, with Delivery and "Last significant edit".

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `campaign_name` | text | — | yes | Must exist in `campaigns.csv`. |
| `ad_group` | text | — | yes | Ad group, ad set, or asset group name. |
| `ad_id` | text | — | yes | Stable platform id; quoted verbatim in findings. |
| `ad_status` | text | — | yes | `ENABLED`, `PAUSED` or `REMOVED`. Only `ENABLED` ads are checked. |
| `approval_status` | text | — | yes | `APPROVED`, `LIMITED`, `DISAPPROVED` or `UNDER_REVIEW`. Map platform wording onto these four values. |
| `last_modified` | date | `YYYY-MM-DD` | yes | Date of the last substantive edit. Drives the creative-freshness check. |
| `impressions` | number | count | no | Context only. |
| `spend` | number | currency | no | Context only. |

An enabled campaign with no rows in this file is reported in the audit notes;
approval and freshness checks cannot run for it.

---

## 3. `keywords.csv` — targeting and negative keywords

One row per keyword. A header-only file (no data rows) is valid: paid-social
accounts have no keywords. If both this file and `search_terms.csv` are
header-only, the negative-keyword check is skipped and reported as not
applicable in the audit notes.

**Source:** Google Ads → Audiences, keywords and content → Search keywords
(targeting) and Negative keywords (campaign-level and shared lists). Microsoft
Advertising exports the same views.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `campaign_name` | text | — | yes | Must exist in `campaigns.csv`, or be the literal `ACCOUNT` for a shared negative list that applies to every campaign. |
| `ad_group` | text | — | no | Blank for campaign-level negatives. |
| `list_type` | text | — | yes | `targeting` or `negative`. |
| `keyword` | text | — | yes | The keyword text without match-type punctuation (`"cargo ebike"` not `[cargo ebike]`). |
| `match_type` | text | — | yes | `exact`, `phrase` or `broad`. |

Negative matching applied by the script, on lowercased alphanumeric tokens:

- `exact` blocks a search term whose tokens are identical to the keyword's;
- `phrase` blocks a term containing the keyword's tokens contiguously, in order;
- `broad` blocks a term containing all of the keyword's tokens in any order.

Close variants, plurals, stemming and misspellings are **not** modeled.

---

## 4. `search_terms.csv` — search terms report

One row per campaign / search term for the audit window. A header-only file is
valid (paid-social accounts have no search terms report); search-term waste is
then not measured and the audit notes say so.

**Source:** Google Ads → Insights and reports → Search terms, date range set to
the audit window, columns Search term, Campaign, Cost, Clicks, Conversions.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `campaign_name` | text | — | yes | Must exist in `campaigns.csv`. |
| `search_term` | text | — | yes | The query as reported. |
| `spend` | number | currency | yes | |
| `clicks` | number | count | yes | |
| `conversions` | number | count | yes | A term with any conversion is never flagged as waste. |

---

## Thresholds and derived values

| Value | Definition |
| --- | --- |
| `days` | `(--as-of - --period-start) + 1`, inclusive |
| Budget capacity | `daily_budget × days` |
| Pace | `spend / capacity`; flagged outside `1 ± --pace-tolerance` |
| Unspent capacity | `capacity - spend` for under-delivering campaigns |
| Wasted spend | Sum of spend on search terms with zero conversions, spend `>= --term-floor`, not blocked by any campaign or account negative |
| Unmeasured spend | Spend in enabled campaigns with zero conversions and spend `>= --tracking-floor` |
| Tracking coverage | Share of total spend in campaigns with at least one conversion |
| Creative staleness | `--as-of - max(last_modified of enabled ads)`, flagged at `>= --stale-days` |

The five spend buckets overlap; they are reported separately and never summed.

## Failure modes

The script exits `2` and names the file, line and column when:

- a required column is missing, or the file is empty (no header row);
- `campaigns.csv` or `ads.csv` has a header but no data rows (`keywords.csv` and
  `search_terms.csv` may be header-only);
- a numeric field is empty, non-numeric or negative;
- `status`, `ad_status`, `approval_status`, `list_type` or `match_type` holds a
  value outside its allowed set;
- `last_modified` is not a real `YYYY-MM-DD` date;
- a `campaign_name` does not exist in `campaigns.csv`;
- `campaigns.csv` contains a duplicate `campaign_name`;
- `--as-of` precedes `--period-start`, or `--naming-pattern` has no
  `{placeholder}` segments or mixes delimiters.
