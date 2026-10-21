---
name: geo-ai-search-audit
description: Audit one client's website for AI search readiness (GEO) - AI crawler access in robots.txt, passage citability, schema.org structured data, llms.txt and on-page rendering - optionally benchmark it against the competitors in the client profile, and produce a DRAFT client report with a prioritized fix list, draft llms.txt and draft Organization JSON-LD.
---

# GEO / AI search readiness audit

Checks whether AI answer engines (ChatGPT search, Perplexity, Claude, Google AI
Overviews, Copilot) can reach, read and quote a client's website, and turns the
findings into a prioritized fix list the account lead can review and send.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `geo-ai-search-audit`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use

- "Run a GEO / AI search / AI visibility audit for <client>."
- "Why aren't we showing up in ChatGPT or Perplexity answers?" (as the technical first pass)
- "How does <client> compare with its competitors for AI search readiness?"
- Before a content or technical SEO sprint, to find the blockers worth fixing first.

Not for: measuring actual AI citations, rankings or traffic (this skill does not
query any AI engine), brand-mention or PR research, or writing pitch decks.

## Inputs: the client profile

Read the client from `clients/<slug>/client.json` as described in `CLIENTS.md`
(resolve by slug or by the `name` field). Fields used:

- `name` (required) - used in the report title.
- `website` (required for this skill) - the site to audit.
- `competitors` (optional) - list of competitor URLs; only needed for `--competitors`.
- `industry` (optional, informational) - use it when judging which schema types matter
  (e.g. Product for DTC, SoftwareApplication/Service for B2B).

If the profile or a needed field is missing, the script stops and prints exactly
what to add and where. Relay that message; do not guess a URL. Audit one client
per run - never mix clients' data in one report.

## Run it

From the skill directory (Python 3.9+, standard library only, no install step).

`--client` needs a clients folder containing `<slug>/client.json` (see
`CLIENTS.md`). The script looks for `./clients` in the directory you run it
from, which is usually not where the agency keeps profiles, so pass
`--clients-dir` explicitly in every `--client` run; without it the script stops
with "No clients directory at clients". `--url` runs need no clients folder.

```bash
# Try it first on the bundled sample client (offline, no network)
python3 scripts/geo_audit.py --client harbor-and-vine --clients-dir examples/clients --offline-root examples/sites

# One client, live
python3 scripts/geo_audit.py --client harbor-and-vine --clients-dir /path/to/clients

# Same, benchmarked against the competitors in client.json (max 3 by default)
python3 scripts/geo_audit.py --client harbor-and-vine --clients-dir /path/to/clients --competitors

# Ad-hoc URL without a profile
python3 scripts/geo_audit.py --url https://example.com

# Offline: a saved page (plus optional saved robots.txt / llms.txt)
python3 scripts/geo_audit.py --url https://example.com --html-file page.html --robots-file robots.txt

# Machine-readable output / write to a file
python3 scripts/geo_audit.py --client harbor-and-vine --clients-dir /path/to/clients --json --out audit.json
```

Other flags: `--max-pages N` (pages per site, homepage first, default 5),
`--max-competitors N`, `--timeout S`, `--date YYYY-MM-DD`, `--offline-root DIR`
(serve `DIR/<host>/...` files instead of the network; see `examples/`).

`bash examples/run.sh` runs a complete offline demo for a synthetic client; its
output is in `examples/expected_output.txt`.

## What it checks

| Area | Weight | What is scored |
|---|---|---|
| Content citability | 30% | Heading-delimited passages scored 0-100 for answer-first phrasing, self-containment, readability, specific numbers/sources and original-data signals |
| AI crawler access | 25% | robots.txt rules (RFC 9309 matching) for Googlebot, Bingbot and the AI search/answer agents of OpenAI, Anthropic and Perplexity; training crawlers are reported but not scored |
| Structured data | 20% | JSON-LD (incl. `@graph`), microdata/RDFa, Organization + sameAs, WebSite, offering/FAQ/Article types, required properties, parse errors |
| Technical / on-page | 15% | Server-rendered text vs empty JS app shell, noindex/nosnippet, title, meta description, single H1, canonical, lang, Open Graph, image alt text |
| llms.txt | 10% | Presence and format of /llms.txt (llmstxt.org); /llms-full.txt presence |

Sampling: homepage plus the most relevant internal links (about, pricing,
product, service, FAQ, blog...), skipping paths robots.txt disallows and
cart/login pages. Areas that could not be checked are marked "n/a" and dropped
from the overall score rather than counted as zero.

## Output

A Markdown report headed **DRAFT for account-lead review**. It contains:

1. Summary: overall heuristic score and rating, per-area scores.
2. Prioritized fix list: Critical / High / Medium / Low, with finding, recommended fix and effort (S/M/L).
3. Competitor benchmark (with `--competitors`): scores side by side and where competitors lead.
4. Details: crawler access table, strongest and weakest passages, schema types found, llms.txt, on-page checks.
5. Appendix A: draft llms.txt from homepage links (when missing or malformed). Linked pages that failed to load during the audit are left out and listed above the draft.
6. Appendix B: draft Organization JSON-LD with sameAs prefilled from profile links on the site (when missing).
7. Method and limits.

After running, read the report yourself before handing it over: sanity-check the
top fixes against the site, remove anything that is intentional for this client
(for example a deliberate opt-out of AI training crawlers), and adapt wording to
the client's `notes` and brand voice. Fuller schema templates are in
`templates/schema/`.

## Rules

- The report is a draft for the account lead. Never send it to the client or
  post it anywhere automatically.
- This skill only reads public pages. It never edits a client's site, robots.txt,
  CMS or tag manager. Any such change needs explicit approval in the conversation,
  naming the client and the change (CLIENTS.md rule 3).
- Do not present scores as rankings or as proof of AI visibility.

## Limits

- Scores are heuristics about readiness, not measurements. The skill does not query
  ChatGPT, Perplexity, Gemini, Claude or Google, and cannot say whether a site is
  actually cited or ranked.
- Crawler access is inferred from robots.txt only. CDN/WAF bot blocking, rate
  limits, IP allowlists and whether a crawler honours robots.txt are not tested.
  The crawler list and each agent's purpose reflect vendor documentation at the
  time of adaptation and change over time.
- Only raw HTML is analysed; JavaScript-rendered content is not seen. Sites that
  block non-browser requests may return errors or challenge pages in live mode.
- Up to `--max-pages` pages per site are sampled, not the whole site.
- The citability rubric (including the 134-167 word "optimal passage" band) is the
  upstream author's heuristic and is not a validated model of any engine.
- Brand authority, third-party mentions, reviews, E-E-A-T and platform presence
  (Reddit, YouTube, Wikipedia) are not measured.
- llms.txt is a community proposal; support by major AI engines is not confirmed,
  so it carries low weight and fixes are rated Low.
