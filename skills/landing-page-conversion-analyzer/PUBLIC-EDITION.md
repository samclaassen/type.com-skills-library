# Public edition 1.1.0

This is a public adaptation of the existing Landing Page Conversion Analyzer workflow. The original workflow and statistical helpers are retained, with the changes below. The package provides a local CSV/Parquet analysis workflow; it does not provide a hosted monitoring service or a production analytics connector.

## Changes in this edition

- Corrected scripts/analyze.py so the site headline selects the configured conversion step, rather than the first funnel step, including conversions configured for site-only reporting. Site narrative describes an observed rate change without claiming a proven cause. A checkout demo exposed the mismatch; its regression test asserts the configured purchase headline.
- Added the missing example funnel to config.example.yaml so the setup instructions have a complete starting configuration.
- Constrained pandas to >=2.2,<3. pandas 2.2 introduced the groupby include_groups argument used by the code, and pandas 3 failed a categorical aggregation during the tested example. The recorded run used pandas 2.3.3.
- Rewrote DATA_CONTRACT.md to remove named customer examples and unverified account-specific field/volume claims. It now documents the required portable export, cautions against first-user versus session attribution, and explicitly states that live adapters are not included.
- Removed historical account-performance measurements from references/methodology.md. Public proof is now the reproducible synthetic example.
- Updated SKILL.md's adapter and setup references to match this package and linked the public example.
- Added examples/showcase/ with deterministic input, complete configuration, baseline state, raw output, a human-readable report, recorded checks, and reproduction instructions.

## Validation

The public edition passed 28 bundled synthetic checks and 4 focused showcase checks. The showcase was run end to end with the packaged command-line analyzer. These checks cover their defined synthetic cases, not every possible input, live account integration, production environment, or business cause.

Revenue at stake is a model relative to historical conversion performance. It is not a promise of recoverable revenue. All example data is synthetic.

No new license grant is asserted by this document; retain the package's existing provenance and respect any applicable source terms.

## Expert knowledge addition

Added self-contained CRO foundations, a page-review checklist, experiment planning, a reusable brief, five worked cases, and a dated source index. SKILL.md routes the agent to these files and separates qualitative screenshot reviews from quantitative analysis. Revised causal wording so funnel symptoms do not imply proven defects. The statistical engine and synthetic proof are unchanged. This addition is not a certification of professional expertise or measured uplift.
