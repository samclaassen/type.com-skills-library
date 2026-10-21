# Data contract: geo-ai-search-audit

## Inputs

### Client profile (`clients/<slug>/client.json`, see CLIENTS.md)

| Field | Required | Used for |
|---|---|---|
| `name` | yes | Report title; resolving `--client` by name |
| `website` | yes for `--client` runs | The site audited (scheme added if missing) |
| `competitors` | only with `--competitors` | Competitor sites benchmarked (first `--max-competitors`, default 3) |
| `industry`, `goals`, `notes`, `accounts` | no | Not read by the script. `industry` and `notes` guide the agent's review of the draft |

If `clients/` does not exist, the client is not found, `website` is missing, or
`--competitors` is used without a `competitors` list, the script exits with
code 2 and a message naming the missing file or field.

### Command-line inputs

- `--url` - audit a URL without a profile.
- `--html-file`, `--robots-file`, `--llms-file` - saved files for offline single-page audits.
- `--offline-root DIR` - saved site files laid out as `DIR/<host>/index.html`,
  `DIR/<host>/<path>.html` or `DIR/<host>/<path>/index.html`, `DIR/<host>/robots.txt`,
  `DIR/<host>/llms.txt`. Missing files are treated as HTTP 404.

## Network access (live mode only)

Live mode sends HTTP GET requests, and nothing else, to the audited sites only:

- For the client site and each benchmarked competitor:
  - `/` (the homepage from the profile or `--url`)
  - `/robots.txt`, `/llms.txt`, `/llms-full.txt`
  - up to `--max-pages - 1` same-host pages linked from the homepage (default 4),
    skipping paths robots.txt disallows for all agents and cart/login-type paths.
- User-Agent: `Mozilla/5.0 (compatible; GEO-AI-Search-Audit/1.0; agency site review)`.
- Per-request timeout `--timeout` (default 20 s); responses capped at 5 MiB; http/https only.

No other hosts are contacted: no analytics, licence checks, update checks or AI
APIs. Offline modes (`--offline-root`, `--html-file`) make no network requests.

## Outputs

- stdout (or `--out FILE`): a Markdown report marked "DRAFT for account-lead review",
  or JSON with `--json` (`draft: true`, `site`, `competitors`, per-area results,
  `scores`, `overall_score`, `issues[]` with `severity`, `area`, `finding`, `fix`, `effort`).
- Nothing is written anywhere else, and nothing is sent to the client or any account.
- Exit codes: 0 success (including sites that could not be fetched, which are reported
  in the output), 2 missing/invalid client profile or arguments.

## Personal data

The audit reads public web pages. It stores nothing between runs. Report
content may quote public page text.
