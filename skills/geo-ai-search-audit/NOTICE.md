# Notice

Adapted from https://github.com/zubair-trabzada/geo-seo-claude at commit
b2c11085ba5c15d7b34a134b8f6ad39363a9b321 by Zubair Trabzada, MIT License.
The upstream licence is reproduced verbatim in `LICENSE.txt`.

Adaptation by Type for the public Skill Library (agency, multi-client use).

## What was taken

- `skills/geo-audit`, `geo-citability`, `geo-crawlers`, `geo-llmstxt`, `geo-schema`
  (SKILL.md guidance): condensed into one `SKILL.md` and into deterministic checks.
- `scripts/citability_scorer.py`: `score_passage` rubric ported to `scripts/geo_lib/citability.py`.
- `scripts/fetch_page.py`: page parsing, JSON-LD `@graph` flattening, SSR check and
  robots.txt/llms.txt fetching, ported to `scripts/geo_lib/fetcher.py`,
  `html_extract.py`, `crawlers.py` and `technical.py`.
- `scripts/llmstxt_generator.py`: validation and draft generation ported to `scripts/geo_lib/llmstxt.py`.
- `schema/*.json`: copied verbatim to `templates/schema/`.

## Modifications

- Merged five skills into one skill, `geo-ai-search-audit`, with one entry point
  `scripts/geo_audit.py` (`--client <slug>` | `--url <url>`, `--competitors`, `--json`, `--out`).
- Added client-profile support per `CLIENTS.md`: reads `website` and `competitors` from
  `clients/<slug>/client.json`, resolves by slug or name, and stops with a named-field
  message when the profile or a field is missing.
- Added competitor benchmarking (same checks side by side, "where a competitor leads").
- Output is a Markdown (or JSON) report marked DRAFT for account-lead review, with a
  prioritized fix list (severity, area, finding, fix, effort) instead of upstream's
  `GEO-AUDIT-REPORT.md` template.
- Replaced `requests`, `beautifulsoup4` and `lxml` with the Python standard library
  (`urllib`, `html.parser`); no `requirements.txt` is needed. Text extraction therefore
  differs slightly from upstream and passage scores can differ from upstream's.
- Removed hard-coded `~/.claude/skills/geo/scripts` paths; the entry point resolves its
  modules relative to its own location.
- Replaced upstream's spoofed Chrome User-Agent with an honest identifying one; added a
  5 MiB response cap and http/https-only enforcement.
- Rewrote robots.txt parsing to RFC 9309 semantics (grouped user-agent lines,
  case-insensitive tokens, longest match with Allow winning ties, `*`/`$` wildcards,
  4xx = no restrictions). Upstream only matched exact agent strings and `Disallow: /`.
- Revised the crawler list: separated search/answer agents (OAI-SearchBot, ChatGPT-User,
  Claude-SearchBot, Claude-User, PerplexityBot, Perplexity-User, plus Googlebot/Bingbot)
  from training agents (GPTBot, ClaudeBot, Google-Extended, Applebot-Extended,
  Meta-ExternalAgent, CCBot, Amazonbot, Bytespider). Upstream described GPTBot as powering
  ChatGPT search; here training agents are reported but not scored, because blocking them is
  a policy choice. Dropped legacy tokens `anthropic-ai`, `cohere-ai`, `GoogleOther`, `FacebookBot`.
- Replaced upstream's six-category composite (which included Brand Authority, E-E-A-T and
  Platform scores that require manual or external research) with five areas the script can
  measure; areas that could not be checked are excluded from the overall score, not scored 0.
- Citability: year pattern generalised from 2023-2026 to 2010-2029; blocks are scored across
  all sampled pages; report lists strongest and weakest passages.
- llms.txt: validation works on already-fetched content; the generator no longer invents a
  `contact@<domain>` address and marks descriptions as TODO; the `llms-full.txt` generator
  (which fetched every linked page) was removed. llms.txt fixes are rated Low because engine
  support is unconfirmed.
- Schema: condensed to required-property checks for Organization/LocalBusiness, WebSite,
  Article, Product, SoftwareApplication, FAQPage and BreadcrumbList, plus sameAs coverage and
  a draft Organization block prefilled from profile links found on the site.
- Added offline modes (`--offline-root`, `--html-file`, `--robots-file`, `--llms-file`) and
  `--date`, with synthetic fixtures and `examples/run.sh` for deterministic output.
- Added "Method and limits" to every report and a Limits section to `SKILL.md`, and removed
  upstream marketing statistics (for example "30-115% more visibility") from the guidance.
- Excluded: `geo-update` (fetches and installs upstream changes), `brand_scanner.py` (stub),
  `geo-proposal`, `geo-prospect`, `crm_dashboard.py`, `scripts/webapp/`, `white-label/`,
  `geo-report-pdf` and PDF tooling, `install*.sh`/`uninstall.sh`, the `.github/` star-history
  workflow, and the remaining skills (`geo-brand-mentions`, `geo-platform-optimizer`,
  `geo-content`, `geo-technical`, `geo-compare`, `geo-report`) and subagent definitions.
  No retained code contacts any host other than the site being audited.

- Packaging: the upstream `LICENSE` file is shipped as `LICENSE.txt` (text unchanged) so Type's skill installer accepts it.
- The draft llms.txt (Appendix A) leaves out linked pages that failed to load
  during the same audit and lists them above the draft, so it no longer
  recommends pages the report flags as broken.
- `SKILL.md` run examples pass `--clients-dir` on every `--client` command, add
  an offline first-run example against `examples/clients`, and state the
  clients-folder requirement.
