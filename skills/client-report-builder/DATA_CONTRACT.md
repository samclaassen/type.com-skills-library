# Data contract — client-report-builder

Three required CSV files, plus two optional finance inputs (sections 4 and 5).
CSVs are UTF-8, comma separated, with a header row. A UTF-8 BOM is
tolerated. Extra columns are ignored, so a raw platform export can be used as
long as the required columns are present and named exactly as below.

Money is in one currency across all three files; the script does no conversion.
Numeric fields may include `$` and thousands separators (`1,250` and `$1250`
both parse). Negative values are rejected — refunds and credits must be netted
out upstream, because a negative spend row silently breaks pacing.

Every row is matched on the exact `client` string. Normalize client naming
across systems before writing the files.

---

## 1. `paid_performance.csv` — ad platform delivery

One row per client / month / channel / campaign. The script aggregates campaigns
up to the channel level, so campaign-level granularity is optional but
recommended (it keeps the export auditable).

**Source:** the user's connected ad platforms — Google Ads, Meta Ads, TikTok
Ads, Microsoft Advertising, LinkedIn Ads. Export the reporting month and the
comparison month in the same pull, with the same attribution window and the same
conversion action selection.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Exact client name, matched literally. |
| `month` | text | `YYYY-MM` | yes | Reporting month of the row. |
| `channel` | text | — | yes | One label per platform, e.g. `Google Ads`. Must match `budget.csv`. |
| `campaign` | text | — | no | Campaign name. Ignored by the calculations; kept for auditability. |
| `spend` | number | currency | yes | Media cost. `>= 0`. |
| `impressions` | number | count | yes | `>= 0`. |
| `clicks` | number | count | yes | `>= 0`. |
| `conversions` | number | count | yes | Platform-attributed conversions. `>= 0`. Fractional values from modeled conversions are accepted. |
| `revenue` | number | currency | yes | Platform-claimed conversion value in the platform's attribution window. `>= 0`. Use `0` for lead-gen accounts with no revenue value. Shown per platform only; never summed across platforms. |

Example:

```csv
client,month,channel,campaign,spend,impressions,clicks,conversions,revenue
Northwind Coffee Co.,2026-08,Google Ads,NW_US_Brand_Search,4200,210000,12600,520,41600
```

---

## 2. `analytics_channels.csv` — site-wide analytics

One row per client / month / channel group, covering **all** traffic. This is
the denominator for every blended metric, so omitting organic or direct inflates
performance.

**Source:** the connected web analytics property — GA4 "Traffic acquisition" by
default channel group, or the equivalent report in whichever analytics tool is
connected. Use the same conversion event the client considers a customer
acquisition.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Must match `paid_performance.csv`. |
| `month` | text | `YYYY-MM` | yes | |
| `channel_group` | text | — | yes | e.g. `Paid Search`, `Organic Search`, `Direct`, `Email`. Need not match ad-platform channel labels. |
| `sessions` | number | count | yes | `>= 0`. |
| `new_users` | number | count | yes | `>= 0`. Reported for context; not used in the headline math. |
| `conversions` | number | count | yes | Site-recorded conversions. `>= 0`. The sum across groups is the blended CAC denominator. |
| `revenue` | number | currency | yes | Site-recorded revenue. `>= 0`. The sum across groups is the blended ROAS numerator. |

Example:

```csv
client,month,channel_group,sessions,new_users,conversions,revenue
Northwind Coffee Co.,2026-08,Paid Search,96000,61000,1020,84600
```

---

## 3. `budget.csv` — committed media budget

One row per client / channel for the reporting month. A duplicate
client+month+channel row is a hard error rather than a silent sum, because
duplicated budget rows are almost always an export mistake.

**Source:** the agency's own system of record — the media plan in the statement
of work, a budget spreadsheet, or a project-management field. Ask the user if no
connected system holds it. Never derive budget from spend.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | |
| `month` | text | `YYYY-MM` | yes | Only rows matching `--month` are read. |
| `channel` | text | — | yes | Must match the `channel` labels in `paid_performance.csv`. Channels present here but absent from delivery appear in the report's Data notes. |
| `committed_budget` | number | currency | yes | Full-month committed spend. `>= 0`. |

Example:

```csv
client,month,channel,committed_budget
Northwind Coffee Co.,2026-08,Google Ads,22000
```

---

## 4. `revenue_definitions.json` — finance's definitions (optional)

Finance's shared definitions file; the full format is in
`REVENUE_DEFINITIONS.md`. Looked up at `finance/revenue_definitions.json` in the
working folder, or wherever `--definitions` points.

Keys this skill reads (all required when the file is present; any other keys are
ignored):

| Key | Used for |
| --- | --- |
| `version` | Printed in the report's first line so readers know which definitions applied. |
| `reporting_currency` | ISO code (`USD`, `EUR`, ...). Labels every amount; `USD` renders as `$`, others as `EUR 1,250`. No conversion. |
| `marketing.attribution_windows` | Object of platform → window, e.g. `{"meta": "7d_click_1d_view", "google": "30d_click"}`. Shown next to each platform's claimed figures. |
| `marketing.new_customer_definition` | Printed with the report so new-customer CAC is read against finance's definition. |
| `marketing.mer` | Must be `net_sales / total_marketing_spend`. |
| `marketing.new_customer_cac` | Must be `total_marketing_spend / new_customers`. |

`owner` and `marketing.attributed_revenue_basis` are printed when present.

**Matching channels to windows.** A `channel` label is lower-cased and a
trailing ` Ads` or ` Advertising` is dropped: `Meta Ads` → `meta`, `Google Ads`
→ `google`, `TikTok Ads` → `tiktok`. A channel with no matching key is shown as
"not defined by finance" and listed in Data notes.

**When the file is absent** (and `--definitions` was not passed) the skill uses
these defaults, and the first lines of the report say no finance definitions
file was found and list them:

| Setting | Default |
| --- | --- |
| Attributed revenue | Each platform's claimed conversion value as exported, per platform, never summed |
| Attribution windows | Whatever each export used; shown as "as exported (unverified)" |
| New customer | No definition; `new_customers` taken as counted in `finance_totals.csv` |
| MER | `net_sales / total_marketing_spend` |
| New-customer CAC | `total_marketing_spend / new_customers` |
| Currency | One currency as exported, shown as `$`, no FX |

---

## 5. `finance_totals.csv` — finance net sales and new customers (optional)

One row per client / month, passed with `--finance-totals`. Enables MER and
new-customer CAC. Without this file neither metric is computed; the skill never
substitutes analytics revenue for net sales or analytics conversions for new
customers.

**Source:** finance — net sales per `net_sales` in the definitions file (for
example the daily revenue check or the accounting close), and the new-customer
count per `marketing.new_customer_definition` (for example first order ever, any
channel, by customer email). Same currency as the other files.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `client` | text | — | yes | Must match the other files. |
| `month` | text | `YYYY-MM` | yes | A row for `--month` is required; a row for the comparison month enables deltas. |
| `net_sales` | number | currency | column yes, value may be blank | Finance net sales for the month. `>= 0`. Blank means not supplied: MER is not computed and Data notes say why. |
| `new_customers` | number | count | column yes, value may be blank | New customers per finance's definition. `>= 0`. Blank or `0` means new-customer CAC is not computed, with a Data note. |

Example:

```csv
client,month,net_sales,new_customers
Northwind Coffee Co.,2026-08,247300,1210
```

---

## Derived metrics

| Metric | Formula |
| --- | --- |
| CPA (per channel) | `spend / conversions` from `paid_performance.csv` |
| Claimed revenue (per channel) | `revenue` from `paid_performance.csv`, in the platform's attribution window. Never summed across channels. |
| Claimed ROAS (per channel) | `revenue / spend` from `paid_performance.csv`. Never computed on summed revenue. |
| Median platform-claimed ROAS | Median of per-channel claimed ROAS over channels with conversions `> 0`. Benchmark for the default ROAS floor (half of it) and the scale test (1.25× it). |
| Blended CAC | total paid `spend` / total analytics `conversions` (all channel groups) |
| Blended ROAS | total analytics `revenue` / total paid `spend` |
| Total marketing spend | total paid `spend` across all channels in `paid_performance.csv` (no non-media costs) |
| MER (blended marketing efficiency) | finance `net_sales` / total marketing spend. Only with `finance_totals.csv`. |
| New-customer CAC | total marketing spend / finance `new_customers`. Only with `finance_totals.csv`. |
| Expected spend to date | `committed_budget × (days elapsed in month / days in month)`, where elapsed days come from `--as-of` and are capped at the full month |
| Pace | `spend / expected spend to date` |
| Period-over-period delta | `(current - prior) / prior`, reported as `n/a` when the prior value is zero or missing |

## Failure modes

The script exits `2` and names the offending file, line and column when:

- a required column is missing or the file has no header;
- the file has a header but no data rows;
- a numeric field is empty, non-numeric, or negative;
- `month` is not `YYYY-MM` or `--as-of` is not a real `YYYY-MM-DD` date;
- `--as-of` precedes the first day of `--month`;
- no rows exist for the requested client in the requested months;
- `budget.csv` contains duplicate rows for one client+month+channel;
- `--definitions` names a file that does not exist, the definitions file is not
  valid JSON, a required key listed in section 4 is missing or empty,
  `reporting_currency` is not a three-letter code, or `marketing.mer` /
  `marketing.new_customer_cac` differs from the formula this skill implements;
- `finance_totals.csv` is missing a column, has no row for the client in
  `--month`, has duplicate client+month rows, or has a non-numeric or negative
  value.

Division by zero is never an error: affected metrics render as `n/a`.
