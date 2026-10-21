# CRO foundations
Public reference edition 1.1.0 · Reviewed 2026-09-10

Use this reference before interpreting findings. These are diagnostic principles, not universal conversion targets. The included analyzer measures changes against the business's history; the agent applies the knowledge below to choose the next investigation.

## Define success before evaluating the page
Write down the primary outcome, eligible audience, attribution window, and denominator. A session with a purchase differs from an order count: one session may contain multiple orders. A signup differs from an activated or qualified lead. Keep those measures separate. Agree on the economic outcome as well as the leading indicator. More clicks with fewer profitable purchases is not a successful optimization.

For ecommerce, record product category, price range, contribution margin if available, shipping markets, inventory constraints, returns policy, and campaign promise. For lead generation, record the qualified-lead definition, disqualification reasons, sales cycle, and follow-up process. Use supplied facts; unknown is a useful value.

## Observation, hypothesis, decision
- Observation: a visible element, a measured output, or a documented user behavior. Attach the source, time, and segment.
- Hypothesis: an explanation consistent with that observation. Include an alternative explanation and the evidence that would distinguish them.
- Decision: the next investigation or experiment, its owner role, and the criterion for moving forward.

A screenshot can establish that a CTA is present. It cannot establish its click rate, whether visitors understand it, or its causal effect on purchases. A falling checkout completion rate localizes the symptom; it does not establish a payment bug.

## The conversion chain
Evaluate these questions in order:
1. Intent: What did the campaign promise, and who clicked? Compare the exact ad or search intent with the destination.
2. Relevance: Can visitors identify the product, audience, and benefit from the first screen?
3. Comprehension: Can they understand price, options, what is included, and the next action?
4. Confidence: Are product claims supported by relevant evidence? Can visitors find delivery, returns, and support information?
5. Effort: Does selecting a variant, adding to cart, or completing a form work on their device?
6. Outcome: Does the transaction or lead reach the business system, and is it measured once?

These are review prompts. A page need not expose every detail above the fold; prioritize what the user needs to make the next decision.

## Read the economics carefully
Use the analyzer's revenue-at-stake estimate to prioritize an investigation. It models a shortfall relative to historical performance; it is not observed lost revenue, profit, or a promise of recovery. If revenue is unavailable, prioritize qualified outcomes and volume without inventing currency amounts. Consider engineering effort, reversibility, and evidence strength alongside impact.

## Diagnostic order
First verify collection and event definitions. Next compare comparable traffic and dates. Then locate the affected funnel transition. Only then inspect the matching page state and propose a test. Record campaigns, promotions, inventory changes, and deployments as possible confounders. Do not erase inconvenient periods after seeing the result.

## Practical limits
Low volume means uncertainty, not safety. A non-significant result is not proof of equivalence. Session-scoped attribution does not measure full customer lifetime value or cross-session channel contribution. Before/after monitoring finds associations; randomization is needed for a credible causal test of a proposed change.

The source index in sources.md distinguishes external references from this original editorial framework. The package includes the guidance needed to work offline; links are for verification and further study.
