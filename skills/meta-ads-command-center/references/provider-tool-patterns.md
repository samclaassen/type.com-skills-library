# Provider Tool Patterns

Use available integrations, API tools, or user-provided exports; do not assume a particular agent host or API surface.

## Tool Discovery

Before acting, discover available tools in the current environment. Prefer native connected tools over browser automation.

Look for these capability groups:

- Meta Ads account, campaign, ad set, ad, insight, breakdown, creative, and delivery reporting
- Meta Ads mutate, draft change, or bulk edit support
- Meta Events Manager, Pixel, Conversions API, offline conversion, and event diagnostics
- Shopify, ecommerce, or order revenue reporting
- Google Analytics 4 attribution and landing page reporting
- HubSpot, Salesforce, or another CRM
- spreadsheet, document, and BI report read tools
- notification tools such as Slack, email, or the current workspace

If multiple accounts or sources are connected, use the approved operating definitions to pick the right one. If the instructions are missing or ambiguous, ask one targeted question.

## Meta Ads

Provider-neutral rules:

- Confirm account, date range, timezone, currency, attribution setting, and primary event before analysis.
- Follow the customer-approved source hierarchy. Record extraction time and freshness whether using APIs or exports; explain conflicts rather than silently preferring a source.
- Pull campaign, ad set, and ad-level context before recommending structural edits.
- Use breakdowns only as deeply as the data volume supports.
- Treat platform recommendations as inputs, not instructions to blindly apply.
- Confirm mutate success before saying a change was applied.

## Events and Tracking

Tracking quality is upstream of every optimization decision:

- Pixel activity
- Conversions API status
- deduplication
- event match quality
- event priority
- primary versus secondary conversion events
- offline conversion import freshness

If tracking is missing, stale, or inconsistent, report that first and avoid confident scaling guidance until the measurement issue is addressed.

## Analytics and Ecommerce

Use off-platform sources to explain whether Meta-reported outcomes are credible:

- Shopify orders and revenue
- GA4 paid social sessions and key events
- landing page conversion rate
- checkout or form completion rate
- refunds, cancellations, and repeat purchase behavior when available
- blended revenue and marketing efficiency ratio

Do not compare Meta ROAS and MER as if they are the same metric. Use Marketing Measurement for the customer-approved revenue basis, cost pool, attribution windows, and metric variants. Never add platform-attributed revenue claims as total business sales.

## CRM

For lead generation or SaaS, CRM data should be treated as downstream quality:

- read lifecycle stages, demo or meeting fields, pipeline value, close dates, and revenue only from the connected CRM or approved BI source
- normalize campaign, source, medium, content, term, click ID, and owner fields before grouping
- separate lead volume, qualified pipeline, closed revenue, and sales-cycle lag
- avoid showing customer or deal names unless the channel is approved for sensitive data

