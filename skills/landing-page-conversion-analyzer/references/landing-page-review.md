# Landing-page diagnostic checklist
Read after identifying an affected page, or when a user explicitly requests a qualitative review. A URL or screenshot alone permits a heuristic review, not a conversion-performance report.

## Evidence to collect
Capture the exact URL, date, viewport, device class, campaign destination, and visible page state. Use a screenshot provided by the user or a browser the user has authorized. If access is unavailable, request an image or state the limitation; do not reconstruct an imagined page. Distinguish a captured screenshot from an AI illustration.

| Area | Inspect | Follow-up evidence |
| --- | --- | --- |
| Message match | Does the destination honor the specific product, offer, and audience promised by the ad? | Ad creative and targeting; matched source/campaign outcomes |
| Offer clarity | Are eligibility, price, bundle contents, and material conditions understandable? | Offer terms; product and cart behavior; user questions |
| Product understanding | Can someone identify the product and its use? Do images explain scale, fit, compatibility, or contents where relevant? | Product detail pages and task-based user feedback |
| Primary action | Does the label describe the next action? Does it go to the expected place and work with keyboard and touch? | Destination check, interaction errors, relevant event counts |
| Confidence | Are claims supported? Is proof relevant to this product? Can users find returns, delivery, and support? | Source of claims; current policies; recurring objections |
| Purchase friction | Can users choose variants, see stock, understand total costs, and recover from form errors? | Authorized walkthrough, support themes, checkout events |
| Performance | Does the experience load, respond, and remain stable on the affected device? | Field performance and errors; a screenshot cannot measure speed |
| Lead quality | Does the form explain the exchange and ask appropriate questions? | Qualified-lead and activation outcomes, not submissions alone |

Use Baymard's checkout research as a reason to investigate unclear costs and unnecessary effort, not as evidence that either explains this site's behavior. See sources.md.

For performance, Google's Core Web Vitals cover loading (LCP), responsiveness (INP), and visual stability (CLS). Its good-experience thresholds are LCP at most 2.5 seconds, INP at most 200 milliseconds, and CLS at most 0.1, assessed at the 75th percentile with mobile and desktop considered separately. These are experience thresholds, not conversion benchmarks. This package does not measure them; obtain field measurements before assigning a performance finding. Source and review date: sources.md.

## Turn a review into a useful finding
Use this format:
- Observed: exact element and where it appears.
- Question: what remains unknown about the visitor's experience.
- Hypothesis: a plausible mechanism and competing explanation.
- Check: the data or user task needed next.
- If supported: one change to test and the primary business outcome.

Recommend the smallest change that tests the mechanism. Avoid generic lists such as “make the CTA bigger” without an observed issue. Do not invent testimonials, certifications, scarcity, discounts, or outcome guarantees.

## Screenshot annotations
Limit a showcase image to three or four labels. Point each label at a visible element and explain the corresponding question. Keep the page's logo, product, and copy intact. Labels such as “Offer clarity” are review categories, not defect findings. Do not add invented conversion percentages or heatmaps. State source and capture date; use a separate labeled dataset for quantitative examples.

## Stop conditions
If purchase tracking is suspect, flag performance conclusions as provisional and route to analytics verification first. If the page is inaccessible, say which surfaces were not inspected. Do not place orders, submit forms, change prices, or alter a site merely to complete a review.
