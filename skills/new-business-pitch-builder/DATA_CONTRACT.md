# Data contract — new-business-pitch-builder

Four CSV files, UTF-8, comma separated, with a header row (a BOM is tolerated).
Extra columns are ignored — `benchmarks.csv` in particular should carry a
`source` column for provenance even though the script does not read it.

All four describe **one prospect** over **one representative month**, in one
currency. Numeric fields accept `$`, `%` and thousands separators. Negative
values are rejected everywhere.

Two figures are supplied on the command line rather than in a file, because they
come from the conversation rather than a system: `--aov` (average order value)
and `--gross-margin` (percent). Together they set the gross profit per
incremental conversion, which every revenue-side estimate is expressed in.

---

## 1. `paid_snapshot.csv` — paid media, one month

One row per channel. Duplicate channels are an error.

**Source:** the prospect's ad accounts (read access, a shared dashboard, or
figures supplied in discovery). Use a single complete month. Do not blend
several months into one row.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `channel` | text | — | yes | Must match the `scope` of its `paid_conversion_rate` benchmark. |
| `monthly_spend` | number | currency | yes | Media cost for the month. |
| `impressions` | number | count | yes | Must be `>= clicks`. |
| `clicks` | number | count | yes | Must be `>= conversions`. |
| `conversions` | number | count | yes | Platform-reported conversions. `0` triggers the tracking gap above `--tracking-floor`. |
| `revenue` | number | currency | yes | Platform-reported conversion value. `0` is valid for lead generation. |

---

## 2. `seo_pages.csv` — organic positions

One row per URL/keyword pair. Include the money pages, not the entire site;
twenty well-chosen rows make a better pitch than two hundred.

**Source:** the prospect's Search Console export (position and clicks) joined to
a keyword tool for search volume, or a rank tracker that reports all three.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `url` | text | — | yes | The ranking page. |
| `keyword` | text | — | yes | The target query. |
| `monthly_search_volume` | number | count/month | yes | Total monthly searches for the keyword. |
| `current_position` | integer | rank | yes | Whole number, `>= 1`. Pages already at or above `--target-position` are skipped; pages beyond `--max-position` are excluded and listed in the caveats. |
| `monthly_clicks` | number | count/month | yes | Clicks the page currently earns for that keyword. |

---

## 3. `funnel_steps.csv` — the site funnel

One row per step, ordered from the widest step to the conversion. At least two
steps are required; four or five is typical.

**Source:** the analytics property's funnel or path exploration for the same
month as the paid snapshot.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `step_order` | integer | — | yes | `1..n`, contiguous, no duplicates. |
| `step_name` | text | — | yes | Must match the `scope` of its `funnel_step_rate` benchmark. |
| `users` | number | count | yes | Users reaching the step. Must not exceed the preceding step — a funnel cannot widen. The first step must be above 0. |

The site conversion rate used for organic estimates is `last step / first step`.

---

## 4. `benchmarks.csv` — the comparison rates

One row per metric/scope pair. Duplicates are an error. Percentages must be
between 0 and 100.

**Source:** the agency's own portfolio medians, a vertical benchmark report, or
a platform-published figure. Record it in `source`: this file is the load-bearing
assumption of the entire pitch and will be challenged.

| Column | Type | Units | Required | Notes |
| --- | --- | --- | --- | --- |
| `metric` | text | — | yes | One of `paid_conversion_rate`, `funnel_step_rate`, `organic_ctr`. Any other value is an error. |
| `scope` | text | — | yes | Channel name for `paid_conversion_rate`; step name for `funnel_step_rate`; a whole search position for `organic_ctr`. |
| `value` | number | percent | yes | 0 to 100. |
| `source` | text | — | no | Provenance. Not read by the script; include it anyway. |

`funnel_step_rate` is the rate **into** the named step from the step before it.
A channel or step with no benchmark row is simply not evaluated for that gap.

Without `organic_ctr` rows the script uses a built-in position-CTR curve
(27.6% at position 1, 15.8% at 2, 11.0% at 3, tapering to 2.5% at 10 and 1.0%
beyond). Supply `organic_ctr` rows to override any position.

---

## Derived metrics

| Value | Formula |
| --- | --- |
| Gross profit per order | `--aov × --gross-margin / 100` |
| Site conversion rate | `last funnel step users / first funnel step users` |
| Paid conversion-rate gap | `clicks × (benchmark − actual) / 100 × gross profit per order` |
| Paid efficiency gap | `spend − conversions × best channel's cost per conversion`, reported only when it exceeds `--reallocation-threshold` percent of the channel's spend. Valued as recovered spend, not profit. |
| Paid tracking gap | Channel spend, where spend `>= --tracking-floor` and conversions are 0. Valued as spend to verify, not profit. |
| Organic gap | `(volume × CTR at --target-position / 100 − current clicks) × site conversion rate × gross profit per order` |
| Funnel gap | `previous step users × (benchmark − actual) / 100 × product of current downstream rates × gross profit per order` |
| Adjusted value | `raw × confidence × --confidence-scale` |
| Priority score | `adjusted annual value / effort weeks` |
| Headline gross profit | Sum of adjusted funnel, paid conversion-rate and organic gaps, **excluding** the smaller of the funnel and paid conversion-rate groups (they lift conversion of the same visitors). |
| Paid spend to redeploy or verify | Sum of adjusted paid efficiency and paid tracking gaps. Reported separately; never added to gross profit. |
| Retainer comparison | `headline gross profit / --proposed-retainer`. Recovered spend is not counted toward the fee. |

Confidence and effort by gap type, applied before ranking:

| Gap type | Confidence | Effort |
| --- | ---: | ---: |
| Paid tracking | 60% | 1 week |
| Paid efficiency | 40% | 2 weeks |
| Paid conversion rate | 50% | 4 weeks |
| Funnel step | 35% | 6 weeks |
| Organic position | 30% | 12 weeks |

Each paid channel contributes at most one gap: where both a conversion-rate and
an efficiency gap exist, the larger is counted and the other is shown as an
alternative framing.

## Failure modes

The script exits `2` and names the file, line and column when:

- a required column is missing, a file is empty, or it has no data rows;
- a numeric field is empty, non-numeric or negative;
- `clicks > impressions` or `conversions > clicks` on a paid row;
- `current_position` or `step_order` is not a whole number `>= 1`;
- `step_order` is not a contiguous `1..n`, a funnel step widens, or the first
  step has 0 users;
- fewer than two funnel steps are supplied;
- `benchmarks.metric` is outside the allowed set, a percentage exceeds 100, an
  `organic_ctr` scope is not a position number, or a metric/scope pair repeats;
- `--aov` or `--gross-margin` is missing or 0, `--gross-margin` exceeds 100,
  `--confidence-scale` exceeds 1, `--max-position` is below
  `--target-position`, or `--as-of` is not a real `YYYY-MM-DD` date.
