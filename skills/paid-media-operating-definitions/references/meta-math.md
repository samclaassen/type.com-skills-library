# Platform calculations and limitations

Use this reference for arithmetic after loading the customer's approved Revenue Definitions, Marketing Measurement, and operating definitions. It does not select targets, revenue recognition, attribution windows, cost pools, or profitability assumptions.

## Diagnostic ratios

With compatible account, period, currency, and scope:

- CPM = spend / impressions × 1,000.
- Link CPC = spend / link clicks; link CTR = link clicks / impressions × 100. Label all-click variants explicitly and never mix their denominators.
- Frequency = impressions / reach. Reach is not additive across overlapping audiences or periods.
- Cost per selected result = spend / count of that explicitly named event.
- Conversion rate = the selected conversion count / its approved exposure or visit denominator × 100. State both fields.
- A video diagnostic may use 3-second views / impressions or ThruPlays / 3-second views. Name the exact fields and video lengths; these are not universal hook/hold standards or controlled tests.

For ROAS, MER, CAC, attribution and customer definitions, use Marketing Measurement's approved variant and label numerator, denominator, window, source and cost coverage. A platform conversion-value claim is attributed performance, not independent business revenue. Never add cross-platform revenue claims to derive total sales.

Zero or missing denominators produce unavailable, not zero or infinity. Do not silently treat missing inputs as zero. Avoid averaging ratios: aggregate valid numerators/denominators first, only when their scopes are compatible. Mark partial days, delayed conversions, missing feeds and changed settings.

## Conditional profitability

Require the customer-approved revenue basis and pre-ad contribution costs for the same order/customer cohort and horizon. Include every variable cost the approved model requires; identify exclusions and avoid subtracting costs already netted from revenue.

- Pre-ad contribution = approved revenue minus approved non-ad variable costs.
- Under that model, break-even acquisition cost per order/customer = pre-ad contribution per corresponding acquired order/customer.
- Break-even revenue/spend ratio = 1 / pre-ad contribution margin only when that margin is positive, complete under the approved model, and uses the same revenue basis as the ratio. A nonpositive margin has no finite positive break-even ratio under this model.
- Model contribution after media = pre-ad contribution minus applicable acquisition spend. Do not call this accounting net profit.
- A lead acquisition threshold can use contribution per acquired customer × an approved, cohort-matured lead-to-customer rate only when populations and horizons match.

Gross margin alone is insufficient if fulfillment, payment fees, refunds or other required costs are excluded. Missing costs block break-even and profitability claims. An LTV model must state the customer-approved horizon, retention, servicing costs, evidence and uncertainty; do not substitute it for observed first-order economics.

## Diagnosis and forecasts

Rising frequency/CPM and falling CTR may suggest fatigue or saturation; check tracking, landing pages, audience mix, offer, and seasonality before attributing cause. Use the customer's minimum evidence and alert thresholds; no universal frequency, spend, or scaling cutoff is supplied.

For a customer-approved scenario, budget × days is planned spend, not guaranteed platform spend; planned spend / assumed CPA is a scenario result, not a promise. Show the assumptions, observed baseline, conversion lag, limits, and sensitivity. Scaling may change CPA, delivery and conversion mix. Do not extrapolate beyond the customer's approved scenario range or claim profitability from platform ROAS alone.
