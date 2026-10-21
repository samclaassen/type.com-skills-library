# Worked diagnostic examples
All numerical cases below are teaching examples, not customer results. The bundled examples/showcase/ directory contains the separately reproducible analyzer run.

## 1. A site decline caused by traffic mix
Given: source A has a 5% conversion rate and source B has 1%. In the baseline, A supplies 80% of visits; later it supplies 20%. Within-source rates stay unchanged.

Arithmetic for this teaching case: baseline blended rate = 0.8 × 5% + 0.2 × 1% = 4.2%; current = 0.2 × 5% + 0.8 × 1% = 1.8%.

Interpretation: the mix changed; these figures do not show that either landing page deteriorated. Check campaign allocation and the economics of the new traffic with the media owner. Do not propose a page redesign based solely on the blended decline. In a real analysis, report the engine's mix output rather than substituting this hand calculation.

## 2. Checkout completion falls
Given: the packaged synthetic showcase reports 14,000 current sessions in the affected root cell, 2% purchase conversion versus a 4% adjusted historical rate, and $28,000 revenue at stake.

Observation: purchase conversion declined in the affected segment. Read the actual worst-transition and tracking findings before deciding where to investigate.
Hypotheses: checkout friction, payment failures, changes in purchase tracking, or altered purchase intent.
Next: analytics verifies purchase instrumentation; the ecommerce or engineering owner inspects the affected checkout path if tracking is consistent. The modeled amount is not guaranteed recoverable revenue.
Reject: “A broken payment button cost the business $28,000.” Neither the exact cause nor realized loss follows from the report.

## 3. A storefront screenshot, no analytics
Given: a product homepage shows a promotion banner, a benefit-led hero, product photography, and a shop CTA.

Observation: those elements are visible. Their conversion rates are unknown.
Useful review: compare the campaign offer with the banner, check whether the product benefits answer the target visitor's question, and follow the CTA to verify the expected destination.
Output: three investigation questions and, if the actual image is accessible, an annotated screenshot. If only a text description is supplied, attribute observations to that description. Request an analytics export only for a measured conversion report.
Reject: an invented heatmap, a “low-converting” label, or a predicted percentage lift.

## 4. A promising experiment with invalid allocation
Given: the planned split is 50/50, but logged eligible exposures are 6,000 control and 4,000 treatment. Treatment's reported purchase rate is higher.

Decision: flag possible sample ratio mismatch and investigate assignment and telemetry before recommending a winner. Check assignment, exposure eligibility, redirects, and missing events. Do not silently rebalance the data or treat the allocation problem as harmless.

## 5. Very little data
Given: one conversion in 50 visits, then two conversions in 50 visits.

Observation: the relative rate doubled, but the counts are small. Do not describe this as a proven improvement. Use the engine's sensitivity/untested output for real monitoring; collect more comparable evidence or conduct qualitative review. “Not enough evidence” is actionable when paired with what to collect next.
