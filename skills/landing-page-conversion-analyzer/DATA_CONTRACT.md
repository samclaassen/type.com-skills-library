# Data Contract — Landing Page Conversion Analyzer

The analyzer reads one daily aggregate table from a local CSV or Parquet file. It does not connect to your warehouse, analytics account, or store. Map a read-only export into the contract below before running it. The bundled synthetic example works without an account.

## 1. Grain

One row per unique combination of dimension columns per day. Attribute each session to the landing page it entered on and the session-scoped source that delivered it. Each session contributes to exactly one row; avoid joins that multiply sessions or revenue.

## 2. Dimension columns

Required columns:

| Column | Type | Meaning |
|---|---|---|
| date | YYYY-MM-DD | Session-start date in your reporting timezone. |
| landing_page | string | The session's first-page path. |
| utm_source | string | Raw session source, preserving spelling and case. |
| utm_medium | string | Raw session medium. |
| utm_campaign | string | Raw session campaign. |
| country | ISO alpha-2 | Use ZZ for unknown. |
| sessions | positive integer | Number of sessions in this daily cell. |

Optional columns:

| Column | Values | Purpose |
|---|---|---|
| device | mobile, desktop, tablet | Controls for shifts in device composition. |
| new_vs_returning | new, returning | Controls for shifts in visitor composition. |

Supply optional columns completely or omit them. Missing control dimensions limit what the analysis can rule out. Cost, impressions, and clicks are outside this analyzer's scope.

## 3. Landing page policy

Set meta.landing_page_form to path if the export already strips query strings, or path_plus_query if it preserves them. Preserve source values as exported and review query-string hygiene findings. Do not merge pages with meaningful variant or language parameters merely to reduce row count.

## 4. Define the conversion and ordered funnel

The config must name the events you actually have. Mark exactly one step as is_conversion. Do not infer a purchase event from a column name or choose a dead event without inspecting its volume.

```yaml
funnel:
  steps:
    - {name: view_item, event: view_item}
    - {name: add_to_cart, event: add_to_cart}
    - {name: begin_checkout, event: begin_checkout}
    - {name: purchase, event: purchase, is_conversion: true}
```

The table includes step_view_item, step_add_to_cart, step_begin_checkout, and step_purchase for that example. Each value counts **sessions containing at least one event**, not total event occurrences. Values must be integers from zero to sessions. A session with multiple purchases counts once in step_purchase; revenue can include the session's total attributable order revenue.

The revenue column is session-attributed revenue in one fixed currency. Preserve zero-conversion rows wherever traffic existed. Do not clamp funnel steps to force monotonicity: cart-restoration links can legitimately skip earlier steps.

For lead generation, use your confirmed event names. A thin business outcome can remain is_conversion: true and alert_level: site, while a higher-volume leading step uses alert_level: cell. Confirm the meaning and volume with the business owner.

Session-scoped attribution under-credits multi-session journeys. A visitor who discovers a page, leaves, and returns via direct to buy credits the converting direct session. This is a page and traffic-quality diagnostic, not a channel profit-and-loss or multi-touch attribution model.

## 5. Sidecar inputs

- config.yaml: the human-authored funnel, windows, floors, source groupings, known events, business rules, and notification policy.
- state.json: previous baseline and finding state. The engine reads the input and writes an updated copy. Persist the output for the next run.
- meta.json: provenance and freshness declarations.

Synthetic metadata example:

```json
{
  "timezone": "UTC",
  "currency": "USD",
  "source_system": "synthetic_showcase",
  "landing_page_form": "path",
  "sampled": false,
  "sampling_rate": null,
  "other_row_sessions": 0,
  "reconciliation_delta_pct": 0.0,
  "complete_through": "2026-09-04",
  "synthetic": true
}
```

For your own export, replace every demonstration value with the truth about that export. Set complete_through to the last date whose data has settled, not simply the latest date in the file.

## 6. Freshness

The engine respects complete_through and checks for unusual unresolved-source volume. These are guards, not a guarantee of complete data. Your export must reread a trailing period so the engine can detect restatements.

Preserve the distinction between direct traffic, unresolved sources such as (not set), withheld data such as (data not available), and empty values caused by mapping defects. Do not silently convert all of them to direct.

## 7. Export responsibilities

Before analysis:

- Aggregate at the exact daily session grain.
- Preserve source spelling, case, and zero-conversion rows.
- Use one reporting timezone and currency.
- Reconcile sessions and revenue to your source report.
- Declare sampling, withheld rows, overflow, and reconciliation gaps honestly.
- Check the selected conversion's volume at the proposed page and source grain.
- Confirm that session-with-event counts do not exceed sessions.
- Keep credentials and personal data out of config and exported files.

The analyzer does not validate a live account connection or repair incorrect session attribution.

## 8a. Warehouse export mapping

A warehouse export can provide the complete contract, but the mapping is specific to your schema. For GA4 BigQuery exports, verify session identifiers, session-scoped acquisition fields, consent effects, the first pageview, reporting timezone, and revenue allocation before aggregating. **Do not substitute first-user acquisition fields for session source.** Those describe a different attribution scope.

Use read-only queries reviewed against a trusted source report. Derive each step from distinct sessions containing its event, not COUNT of the event. Map country names to ISO alpha-2 and reduce full page URLs to the declared path form. The package includes no production warehouse connector or universal SQL adapter.

## 8b. GA4 API or connector mapping

Confirm the current provider schema and account-generated event fields before extracting data. Session conversion metrics and event counts are not interchangeable. If a provider returns a conversion rate, confirm its unit and session scope before converting it to a session count; rounding can introduce error.

Review dimension compatibility, cardinality overflow, sampling, thresholding, and withheld rows. Mark these limits in metadata and reconcile the result. This package does not include or certify a live GA4 or third-party connector.

## 9. Validation and refusals

Required columns, the declared conversion, and session-with-event bounds must be valid before a useful result is possible. The engine emits a refused status for contract violations it detects; relay the reason and stop instead of inventing a result.

Other problems appear as findings: missing dates, incomplete optional dimensions, revenue with no conversion, non-monotonic funnels, unresolved sources, and freshness issues. Inspect these before acting on an alert.

Run the bundled tests and synthetic example before using your data. Passing them verifies those controlled cases; it does not establish the correctness of your export or prove a business cause.
