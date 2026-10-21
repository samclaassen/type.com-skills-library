# From diagnosis to an experiment
Read when recommending a change. The analyzer is a monitoring tool, not an A/B experiment evaluator; it does not implement the experimental procedures below.

## Write a test brief
Fill in templates/experiment-brief.md. Name the affected audience and one mechanism. “Clarify shipping cost before checkout” is testable; “redesign everything to improve conversion” obscures what caused a change.

Choose a primary business metric and record its event, denominator, assignment unit, eligibility rules, and attribution window before launch. Use guardrails such as revenue per eligible visitor, qualified-lead rate, refunds, and error rate where relevant. A higher CTA click rate alone is insufficient.

## Plan the comparison
Randomly assign eligible units and keep assignment stable. Choose the unit to reduce contamination: returning visitors should not switch variants across sessions, and a shared account may need account-level assignment. Use the same measurement contract in both variants.

Estimate sample requirements from baseline rate, minimum worthwhile effect, chosen error rate, and desired power with a validated statistical tool. Record assumptions and the planned exposure period. No fixed “two-week” rule works for every site. Include relevant business cycles and time for delayed outcomes.

Choose either a fixed-horizon analysis or a validated sequential procedure before launch. Repeatedly checking ordinary fixed-horizon p-values and stopping at the first favorable result invalidates their interpretation. Multiple variants and many segment comparisons require an appropriate analysis plan.

## Verify before interpreting
Check whether observed allocation is consistent with the planned ratio. Sample ratio mismatch is a warning of possible assignment, eligibility, or telemetry defects; diagnose it before trusting the estimated effect. Microsoft's experimentation guidance explicitly treats SRM as a data-quality gate. See sources.md.

Also inspect missing events, duplicate outcomes, exposure timing, variant delivery, and instrumentation differences. Do not remove segments after seeing results merely to make a winner emerge. An A/A check can exercise the system, but does not guarantee future tests are valid.

## Decide
Report absolute and relative effects, uncertainty intervals, sample sizes, duration, and guardrails using the chosen analysis tool. Distinguish statistical uncertainty from commercial importance. An inconclusive result can mean the experiment cannot resolve the minimum worthwhile effect; it does not certify equivalence.

Ship only when the evidence meets the agreed decision rule and relevant guardrails. Otherwise continue only under the original analysis plan, revise the hypothesis for a new test, or retain the current experience. Document rejected hypotheses as useful learning.

## Low-volume alternative
Prioritize reproducible defects and qualitative usability sessions. Fix a verified broken control with the owner's approval; do not require an experiment to preserve a known failure. For preference or messaging changes, use observed user difficulties to refine a hypothesis and label before/after follow-up as observational. Never promise a specific lift from a heuristic review.
