# Change Control Policy

Use this policy before applying any Google Ads, analytics, tracking, or CRM mutation.

## Risk Levels

- `low` - report generation, saved view creation, task creation, internal draft, or annotation.
- `medium` - new negative keyword draft, bid recommendation, pause recommendation, ad copy test plan, or budget reallocation plan.
- `high` - budget edit, bid edit, status pause, keyword addition, negative keyword addition, RSA asset edit, final URL edit, UTM edit, conversion action edit, or CRM attribution edit.
- `critical` - billing, account access, conversion tracking deletion, bulk campaign restructuring, automated bidding strategy switch, or changes affecting many campaigns.

## Default Permission

Drafts and read-only analysis can proceed within the user request. A risk label does not authorize a write: saved views, task creation, annotations, and external reporting need actual user authorization. `medium` actions should be drafted or recommended. `high` and `critical` actions require actual user-granted authorization for the exact scope.

## Approval Checklist

Before applying a medium-risk or higher mutation, confirm:

- account and campaign scope
- exact change list
- date range and evidence
- expected impact
- rollback path
- owner who approved the change
- whether the change should be logged or annotated

## Never Auto-Apply

Never auto-apply these unless the user has actually authorized the exact case, scope, and limits:

- budget increases or decreases
- bidding strategy changes
- broad or phrase negative keyword additions
- campaign, ad group, ad, or keyword pauses
- final URL, tracking template, or UTM changes
- conversion action creation, deletion, or inclusion changes
- billing, access, or account-linking changes
- CRM lifecycle, revenue, or attribution field edits

## Safe Draft Format

```text
Proposed change: <specific edit>.
Scope: <account/campaign/ad group>.
Evidence: <metric window and finding>.
Expected impact: <plain-language expectation>.
Risk: <main downside>.
Rollback: <how to undo>.
Approval needed: <yes/no and from whom>.
```

