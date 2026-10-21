# Provider Tool Patterns

Use available integrations, API tools, or user-provided exports; do not assume a particular agent host or API surface.

## Tool Discovery

Before acting, discover available tools in the current environment. Prefer native connected tools over browser automation.

Look for these capability groups:

- Google Ads account, campaign, ad group, keyword, search term, asset, auction insights, and segment reporting
- Google Ads mutate, bulk upload, or draft change support
- Google Analytics 4 landing page, event, and conversion reporting
- Google Search Console query and landing page reporting
- CRM read tools for HubSpot, Salesforce, or another source
- spreadsheet, document, and BI report read tools
- notification tools such as Slack, email, or the current workspace

If multiple accounts or sources are connected, use the approved operating definitions to pick the right one. If the instructions are missing or ambiguous, ask one targeted question.

## Google Ads

Provider-neutral rules:

- Confirm account, date range, timezone, currency, and conversion actions before analysis.
- Follow the customer-approved source hierarchy. Record extraction time and freshness whether using APIs or exports; explain conflicts rather than silently preferring a source.
- Compare like-for-like periods and call out seasonality or missing days.
- Segment only as deeply as the data volume supports.
- Treat recommendations from Google Ads as inputs, not instructions to blindly apply.
- Confirm mutate success before saying a change was applied.

## Analytics and Landing Pages

Use analytics sources to explain campaign outcomes:

- landing page conversion rate
- form completion rate
- bounce or engagement signals
- page speed or technical issues when available
- query to landing page mismatch
- organic search context when Search Console is connected

Do not attribute paid-search changes to landing pages unless the date window, traffic source, and conversion definition support the claim.

## CRM

Use the approved CRM source for downstream stages; closed-won deal value is not automatically recognized revenue:

- read lifecycle stages, demo or meeting fields, pipeline value, close dates, and revenue only from the connected CRM or approved BI source
- normalize campaign and source fields before grouping
- separate lead volume, qualified pipeline, closed revenue, and sales-cycle lag
- avoid showing customer or deal names unless the channel is approved for sensitive data

## Spreadsheets and BI Snapshots

When using spreadsheets or BI exports:

- identify the export timestamp
- check whether data is partial or stale
- avoid editing formulas unless the user asked for spreadsheet maintenance
- cite sheet, tab, and range when making recommendations from a spreadsheet

