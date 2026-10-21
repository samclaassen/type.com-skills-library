# Data contract — pipeline-hygiene-audit

Three CSV files plus one JSON config. UTF-8, comma separated, one header row.
All dates are `YYYY-MM-DD`. All money is plain USD numbers (`45000` or
`45000.00`); `$` and thousands separators are tolerated, other currencies are
not converted. Column order does not matter; column names must match exactly.

## 1. `deals.csv` — required

One row per opportunity, open **and** closed. Closed deals are excluded from the
checks but are used for duplicate-account clustering, so include the last 12
months of closed business.

| Column | Type | Required | Notes | Typical source |
| --- | --- | --- | --- | --- |
| `deal_id` | string | yes | Unique across the file; a repeat is an error. | Salesforce `Opportunity.Id`, HubSpot `hs_object_id`, Attio deal record id |
| `account_id` | string | yes | CRM account/company id. Two ids for one real company is exactly what the duplicate check looks for. | `Opportunity.AccountId`, HubSpot associated company id |
| `account_name` | string | yes | Display name as stored. Do not clean it up — the skill normalizes it. | `Account.Name` |
| `account_domain` | string | no | Web domain. `www.` and scheme prefixes are stripped. Improves duplicate detection a lot. | `Account.Website`, HubSpot `domain` |
| `owner_name` | string | yes (may be empty) | Empty is a finding (`missing_owner`), not an error. | `Owner.Name`, HubSpot owner |
| `stage` | string | yes | Must appear in `stages.csv`, exact match. | `StageName`, `dealstage` label |
| `amount_usd` | number | yes (may be empty) | Empty or below `min_amount_usd` is a finding. Negative is an error. | `Amount` |
| `created_date` | date | yes | Opportunity creation date. | `CreatedDate` |
| `close_date` | date | yes (may be empty) | Expected close for open deals, actual close for won/lost. Empty on an open deal is a finding. | `CloseDate` |
| `last_activity_date` | date | yes (may be empty) | Last logged call, email, meeting, or task. Empty means "never touched" and is a finding. | `LastActivityDate`, HubSpot `notes_last_updated` |
| `stage_entered_date` | date | yes | Date the deal entered its **current** stage. Must not be after `--as-of`. | Last stage-history event, or HubSpot `hs_date_entered_<stage>` |
| `status` | enum | yes | `open`, `won`, or `lost`, lowercase or mixed case. Anything else is an error. | Derived from `IsClosed`/`IsWon` |

## 2. `stages.csv` — required

| Column | Type | Notes |
| --- | --- | --- |
| `stage_name` | string | Must match `deals.stage` exactly. Listed once. |
| `stage_order` | integer | Ascending pipeline order starting at 1. Closed Won and Closed Lost may share the final order value. |

Source: Salesforce `OpportunityStage.SortOrder`, the HubSpot pipeline's stage
`displayOrder`, or the Attio deal status option order.

## 3. `stage_history.csv` — optional

Enables the `stage_skipped` check. Include only open deals if the full history
export is large.

| Column | Type | Notes |
| --- | --- | --- |
| `deal_id` | string | Matches `deals.deal_id`. Deals with no rows are simply not checked. |
| `stage` | string | Must appear in `stages.csv`. |
| `entered_date` | date | When the deal entered that stage. Multiple rows per deal. |

Rows are sorted by `entered_date`, then stage order. A forward jump of more than
one stage position is reported as a skip. Backward moves are not flagged.

Source: Salesforce `OpportunityHistory`, HubSpot `dealstage` property history,
Attio status change history.

## 4. `config/rules.json` — required (defaults ship with the skill)

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `stale_activity_days` | integer | 21 | Days without activity before `stale_activity` fires. |
| `stuck_multiplier` | number | 1.5 | Multiple of the stage median that counts as stuck. |
| `stuck_min_sample` | integer | 4 | Minimum open deals before a stage's own median is used. |
| `min_amount_usd` | number | 1000 | Amount floor for `missing_amount`. |
| `amount_reference_usd` | number | 50000 | Denominator of the amount weight. Must be > 0. |
| `amount_weight_cap` | number | 4.0 | Cap on the amount weight. |
| `severity_points` | object | see file | Integer points for each of the seven finding codes. All seven keys are required. |
| `legal_suffixes` | string[] | see file | Trailing tokens stripped before duplicate-name comparison. |

## Failure modes

The script exits `2` and names the file, row number, and column for: a missing
file, an empty file, a missing required column, a duplicate `deal_id`, a stage
not present in `stages.csv`, a `status` outside the enum, a non-numeric or
negative amount, a malformed date, and a `stage_entered_date` after `--as-of`.
It never silently drops a row.
