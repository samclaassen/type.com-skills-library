# Data contract — lead-triage-router

Two CSV files plus one JSON rubric. UTF-8, comma separated, one header row.
Dates are `YYYY-MM-DD`. Money is plain USD. Booleans accept
`yes/no/y/n/true/false/1/0`; blank means no. Anything else is an error.

## 1. `leads.csv` — required

One row per lead. `lead_id` must be unique.

### Identity and firmographics

| Column | Type | Required | Notes | Typical source |
| --- | --- | --- | --- | --- |
| `lead_id` | string | yes | CRM record id. Duplicates are an error, not a warning. | HubSpot contact id, Salesforce `Lead.Id`, Attio person id |
| `full_name` | string | yes (may be empty) | Display only. | CRM |
| `email` | string | yes (may be empty) | The domain after `@` drives the free-email disqualifier. Blank disables that check and raises a data note. | CRM |
| `job_title` | string | yes (may be empty) | Matched whole-word against `title_bands`. | CRM / enrichment |
| `company_name` | string | yes (may be empty) | Display only. | CRM / enrichment |
| `employee_count` | integer | yes (may be empty) | Drives the size band **and** the segment. Blank means unknown: zero points, segment `unknown`. Non-numeric or negative is an error. | Clearbit, ZoomInfo, Apollo, HubSpot `numberofemployees` |
| `annual_revenue_usd` | number | yes (may be empty) | Blank scores zero. `$` and commas tolerated. | Enrichment provider |
| `industry` | string | yes (may be empty) | Matched exactly against `industry_points`; unmatched values get `_default`. Keep the provider's vocabulary consistent. | Enrichment provider |
| `country` | string | yes (may be empty) | Exact match against `country_points` and `regions`; unmatched values get `_default` / `INTL`. | CRM / IP enrichment |
| `lead_source` | string | yes (may be empty) | Display only, not scored. Useful when reviewing tier mix by channel. | CRM |
| `created_date` | date | yes | Lead creation date. Required; a blank is an error. | CRM |

### Intent signals

All counters are for the 30 days ending on `--as-of`. Blank is treated as 0.

| Column | Type | Notes | Typical source |
| --- | --- | --- | --- |
| `last_activity_date` | date, may be empty | Most recent tracked action of any kind. Drives the recency multiplier. Must not be after `--as-of`. Blank falls to the lowest multiplier. | CRM activity, product analytics |
| `website_sessions_30d` | integer | Distinct sessions. | GA4, PostHog, Amplitude, HubSpot analytics |
| `pricing_page_views_30d` | integer | Views of pricing/plans pages only. | Same |
| `content_downloads_30d` | integer | Gated asset downloads. | Marketing automation |
| `demo_requested` | boolean | A submitted demo/contact-sales form. | CRM form submission |
| `trial_started` | boolean | Self-serve trial or sandbox created. | Product database or warehouse |
| `webinar_attended` | boolean | Attended, not merely registered. | Webinar platform |

To add a signal: add the column here, add an entry to `intent.signals` in the
rubric with `kind` `flag` or `count`, and add the column to `LEAD_COLUMNS` in
the script. Unlisted columns in the CSV are ignored.

## 2. `routing.csv` — required

Territory and queue rules, evaluated in ascending `priority` then `rule_id`.
First match wins.

| Column | Type | Notes |
| --- | --- | --- |
| `rule_id` | string | Unique. Printed on every routed lead so an assignment can be traced. |
| `priority` | integer | Lower runs first. |
| `tier` | enum | A configured tier name (`A`/`B`/`C`/`D` by default) or `*`. An unknown value is an error. |
| `segment` | enum | A configured segment (`enterprise`, `mid-market`, `smb`), `unknown`, or `*`. |
| `region` | enum | A region value from `regions` (`NA`, `EMEA`, `APAC`, `INTL`) or `*`. |
| `assignee` | string | Rep or pod name. Blank becomes `(unassigned)`. |
| `queue` | string | Required. Queue, list, or Slack channel that receives the lead. |
| `sla_hours` | string | Free text/number, printed with the assignment. May be empty. |

Keep a final catch-all row (`tier=*`, `segment=*`, `region=*`). Without one,
unmatched leads land in `--fallback-queue` and are flagged in the summary.

Disqualified leads bypass this table entirely and go to
`disqualifiers.queue` in the rubric.

## 3. `config/rubric.json` — required (defaults ship with the skill)

| Key | Meaning |
| --- | --- |
| `fit_weight`, `intent_weight` | Must sum to exactly 1.0. |
| `fit.max_points` | Denominator for fit normalization; the band points should sum to this. |
| `fit.employee_bands[]` | `{min, points, label}`, highest `min` first. First band whose `min` is met wins. |
| `fit.revenue_bands[]` | Same shape, USD. |
| `fit.industry_points` | Exact-match map plus a required `_default`. |
| `fit.country_points` | Exact-match map plus a required `_default`. |
| `fit.title_bands[]` | `{keywords[], points, label}` in priority order. Single-word keywords match a whole token; multi-word keywords match a run of consecutive whole tokens (`head of` matches "Head of Sales", not "Forehead Office Manager"). |
| `fit.title_default` | Points for an unmatched title. |
| `intent.max_points` | Cap on summed intent points before normalizing. |
| `intent.signals[]` | `flag`: `{field, kind, points, label}`. `count`: `{field, kind, points_per_unit, cap, label}`. |
| `intent.recency_bands[]` | `{max_days, multiplier, label}` ascending; the last entry must use `max_days: null` as the catch-all. |
| `intent.missing_recency` | Multiplier used when `last_activity_date` is blank. |
| `tiers[]` | `{name, min_score, description}`. Evaluated highest `min_score` first. |
| `segments[]` | `{name, min_employees}` descending. |
| `regions` | Country to region map plus a required `_default`. |
| `disqualifiers` | `free_email_domains[]`, `blocked_countries[]`, `blocked_industries[]`, `min_employee_count`, plus the `queue`, `assignee`, and `sla_hours` disqualified leads are routed to. |

## Failure modes

The script exits `2` and names the file, row, and column for: a missing or
empty file, a missing required column, a duplicate `lead_id` or `rule_id`, a
non-numeric or negative count or revenue, a boolean outside the accepted set, a
malformed date, a `last_activity_date` after `--as-of`, a routing value outside
the configured tiers/segments/regions, weights that do not sum to 1.0, and an
intent signal with an unknown `kind`.
