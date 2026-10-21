---
name: search-console-seo
description: Analyze Google Search Console data period over period to find declining pages, CTR problems, rising queries, keyword cannibalization and page-1 opportunities, then produce a prioritized action list with estimated traffic impact. Use for a weekly SEO check-in or when someone asks "analyze my Search Console data", "what's happening with our SEO?", "which pages lost traffic?", "where are we losing clicks?" or "are pages cannibalizing each other?".
---

# Search Console SEO Analyzer

SEO teams export Search Console data, pivot it in a spreadsheet and eyeball what
moved. This skill does that pass mechanically on the full export. Which pages
lost clicks and what changed alongside? Which top-5 queries get fewer clicks
than their position should earn? What is gaining momentum? Which queries are
split across several URLs? What sits just off page 1? The script is
deterministic, every threshold lives in `settings.json` and `ctr_curve.json`,
and every projection is labelled **est.**

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (scripts, config, sample data, `run.sh`, `expected_output.txt`) is in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `search-console-seo`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`.
4. **Try the sample first.** Run `bash run.sh`; its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests.sh` runs the offline checks and should end with `ALL TESTS PASSED`.

## Config

```yaml
gsc_property: ""                # Search Console property, e.g. sc-domain:example.com. Set in Step 0
compare_period: 28              # days: last N days vs. the prior N days
data_lag_days: 3                # periods end this many days before as-of (Search Console lags ~2-3 days)
min_impressions: 50             # ignore queries below this
min_position_change: 3          # a query that improved by >= this many positions counts as rising
ctr_benchmark: auto             # auto (ctr_curve.json) | custom (ctr_curve_custom)
cannibalization_threshold: 2    # flag queries ranking with >= this many URLs
post_to: ""                     # channel for the report; blank = this Space
```

The script reads these from `settings.json`, which also holds the tunables
(decline %, CTR gap, page-1 window, priority rules, action templates, and the
optional `brand_terms` / `exclude_query_patterns` filters that keep brand and
typo queries out of the CTR and page-1 lists). `DATA_CONTRACT.md` explains
every key. Never edit the script, this SKILL.md or the skill's own
`settings.json`: they are checked against published hashes. To change a value,
copy `settings.json` to `search-console-seo-settings.json` in the user's working
folder (outside the skill's files), edit the copy and pass it with
`--config search-console-seo-settings.json`.

## Step 0: First run, confirm the connection

Run this whenever `gsc_property` is blank or the property can no longer be read.

1. **Detect.** Check whether this Space has a working Search Console
   connection: a Google integration with Search Console access, or a custom API
   connection to `searchconsole.googleapis.com` with the read-only scope
   `https://www.googleapis.com/auth/webmasters.readonly`. In Type, also check
   workspace connections that exist but are not added to this Space. Mention
   any you find and offer to add them to the Space; never silently treat them
   as absent. "Working" means one real read call succeeds:
   `GET https://searchconsole.googleapis.com/webmasters/v3/sites` returns
   `siteEntry[]` with `siteUrl` and `permissionLevel`. A connection that shows
   in a list but fails this call does not count.
   - `searchAnalytics.query` is a `POST` even though it only reads data. In
     Type, a connection granted read-only access can block `POST` requests
     through the proxy, so `sites.list` may work while the analysis calls fail.
     If that happens, tell the user the connection needs Full access for this
     one read endpoint, and let them decide. Never call any write endpoint
     either way.
2. **Propose.** Report what you found, e.g. "Search Console is connected and
   can read `sc-domain:harborandvine.example` and
   `https://shop.harborandvine.example/`. Use `gsc_property: sc-domain:harborandvine.example`?"
   - Several properties: ask which one. Never pick the first.
   - `permissionLevel: siteUnverifiedUser`: that property has no data. Say so.
   - No working connection: propose **manual mode**, where the user uploads the
     Search Console UI exports (reduced mode, see Step 2).
3. **Wait for the user to confirm.** Never choose a property or a provider
   silently.
4. **Save.** Never write into this SKILL.md or the skill's own
   `settings.json`. Copy `settings.json` to `search-console-seo-settings.json`
   in the user's working folder or Space files, set `gsc_property` (and
   `post_to`, if given) in that copy, and pass it with `--config` on every run.
   If you can't save files there, give the user the exact line,
   e.g. `"gsc_property": "sc-domain:harborandvine.example",`, and pass
   `--property` on each run instead.

If an automation started the run and nobody is there to confirm, post one
message to `post_to` asking which property to use, then exit without analyzing.
This setup question is the only message the skill sends when it isn't
delivering a report.

## Step 1: Preflight

1. Pick the reporting date. For scheduled runs it is the run date. For manual
   runs, use the date the user names, or today. Pass it as `--as-of`. The script
   never reads the clock.
2. Print the windows and exact request bodies:
   `python3 gsc_analyze.py --plan --property <property> --as-of <date>`, plus
   `--config search-console-seo-settings.json` if the user has a saved copy.
   With defaults and `--as-of 2026-09-28`, that gives 2026-08-29 to 2026-09-25
   vs. 2026-08-01 to 2026-08-28.
3. Stay read-only. Only `searchAnalytics.query` and `sites.list` are needed.
   `searchAnalytics.query` is sent as `POST` but changes nothing (see Step 0).

## Step 2: Pull the data

**Full mode (API, preferred).** For each request in `--plan`, call
`POST https://searchconsole.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query`
with the printed body (`type: web`, `dataState: final`, `rowLimit: 25000`).
Page with `startRow` += 25000 until a response returns fewer than 25,000 rows.
Save each response, then convert:

```bash
python3 gsc_api_to_csv.py --dimensions query,page   --out current.csv  cur_*.json
python3 gsc_api_to_csv.py --dimensions query,page   --out prior.csv    pri_*.json
python3 gsc_api_to_csv.py --dimensions query,device  --out devices.csv  dev_*.json
python3 gsc_api_to_csv.py --dimensions query,country --out countries.csv ctry_*.json
python3 gsc_api_to_csv.py --dimensions "" --set period=current --out totals.csv tot_cur.json
python3 gsc_api_to_csv.py --dimensions "" --set period=prior --out totals.csv --append tot_pri.json
```

That is query × page for both windows, query × device and query × country for
the current window (the script takes the top queries from these), and
no-dimension property totals for both windows. The totals include anonymized
queries, which the query rows leave out.

**Reduced mode (UI export).** Performance → Search results → Web → custom date
range (one window at a time, not Compare) → Export → CSV. Use `Queries.csv` and
`Pages.csv` from each window's zip. The UI caps each tab at 1,000 rows and has no
query × page view, so cannibalization is reported as **not evaluated** and
declining pages show no top query. Say this in the report; the script already
prints it.

## Step 3: Run the analysis

```bash
# full mode
python3 gsc_analyze.py --current current.csv --prior prior.csv \
  --devices devices.csv --countries countries.csv --totals totals.csv \
  --config settings.json --property sc-domain:example.com --as-of 2026-09-28

# reduced mode
python3 gsc_analyze.py --current Queries_cur.csv --prior Queries_pri.csv \
  --current-pages Pages_cur.csv --prior-pages Pages_pri.csv \
  --property sc-domain:example.com --as-of 2026-09-28
```

Add `--config search-console-seo-settings.json` when the user has a saved
settings copy (Step 0).

- `--json` emits every finding, including rows cut from the tables.
- Bad input exits `2` and names the file, row and column. Fix the export and
  rerun. Never patch the data or the script to get past it, and never fill in a
  missing value.
- **New property, prior period empty.** If the prior window has no data (the
  error says "the prior period … has no Search Console data"), nothing is
  analyzed. Tell the user plainly: the property is too new for the configured
  comparison (by default the last 28 days vs. the 28 days before). Offer two
  options: a shorter comparison window, or waiting until there is enough
  history. To size the shorter window, run the `first_data_date_probe` request
  from `--plan`, take the earliest date it returns and rerun with
  `--first-data-date YYYY-MM-DD`. The script then names the largest
  `compare_period` that fits and the `--as-of` date when the full window will
  be available. If the user picks the shorter window, set `compare_period` in
  their settings copy, pull the data again for the new `--plan` windows and
  rerun. Never compare against an empty or partial prior period.

## Step 4: Read and present the output

The markdown matches the spec's report layout and is ready for chat:

- **Summary**: clicks, impressions and impressions-weighted average position,
  with period-over-period change and the count of critical and high findings.
  For position, lower is better; the summary says "worse" or "better".
- **🔻 Declining pages**: clicks down more than 20% with more than 100
  impressions, sorted by absolute click loss. "Likely cause" states only what the
  data shows, e.g. "Avg position 3.7→6.7" or "Impressions −45% at stable
  position (consistent with lower demand)".
- **🎯 CTR opportunities**: positions 1-5 with CTR below the expected-CTR curve.
  Shows the gap in pp and an **est.** click gain. These are title and snippet
  problems, not ranking problems.
- **📈 Rising queries**: impressions up at least 30% (and +100), position
  improved by `min_position_change` or more, or new queries. These are the
  momentum plays.
- **🔀 Cannibalization**: queries where `cannibalization_threshold`+ URLs each
  hold ≥10% of impressions. Shows the best-ranked URL and a consolidation
  recommendation. Queries flagged here are kept out of the CTR and page-1 lists
  because their position is a blend of several URLs.
- **🏁 Page-1 opportunities**: positions 8-12, with the **est.** gain if the
  query moved to position 5, per comparison window.
- **📱 Device & country**: click share and position for the top queries, with a
  note when device positions differ by `min_position_change` or more.
- **✅ Prioritized actions**: critical, then high findings, each with a
  rule-based action. The rules are in `settings.json` under
  `priority_rules`.

When presenting, open with the summary line and the top two or three
prioritized actions. Keep "est." on every projected number, and present "Likely
cause" as an observation ("position fell from 3.7 to 6.7"), not a verdict.

## Step 5: Post

Post the report to `post_to`, or reply in this Space if it is blank. Send one
message. If the low-volume warning fired, keep it at the top.

## Triggers

- **Best:** weekly schedule, Monday morning. Scheduled runs use `--as-of` = the
  run date. The default 3-day lag keeps both windows on finalized data.
- **Manual:** "Analyze my Search Console data", "What's happening with our SEO?",
  "Which pages lost traffic this month?"

## Automation recipe

Nothing runs on a schedule until you set it up. In **Space settings →
Automations**, create a schedule (weekly, Monday 08:00 in the team's time zone)
that runs this skill. New automations start **disabled**, so enable it. Complete
Step 0 interactively first. Otherwise the first scheduled run only posts the
setup question and exits.

## Guardrails

- **Read-only.** Never modify the site, submit URLs or sitemaps, request
  indexing, or change Search Console settings, users or properties. Use only
  `webmasters.readonly` calls.
- **Correlation is not causation.** A position drop that coincides with an
  algorithm update is a correlation, not a confirmed cause. "Likely cause" only
  reports metric changes. Never write "because of a competitor", "due to the
  core update" or similar unless the user supplies the evidence.
- **Label estimates.** Every projected click gain carries "est." Observed
  figures come straight from the export and don't.
- **Low volume.** If the property has fewer than 1,000 clicks in the period,
  say the data volume is too low for reliable pattern detection. The script
  prints the warning and does not assign priorities.
- Never choose a property or provider the user hasn't confirmed (Step 0).
- Never guess missing values; the script exits 2 instead.
- Use only the confirmed property's data. Don't paste raw query lists outside
  `post_to`.

## Limits

- Expected CTR comes from a generic, rounded curve (`ctr_curve.json`), not
  your site's own data. Brand queries, rich results and local packs move real
  CTR a long way from it. Use `ctr_benchmark: custom` with your own curve if
  you have one, and list brand names and common misspellings in `brand_terms`
  or `exclude_query_patterns` so they don't crowd the CTR and page-1 lists.
  `brand_terms` match whole words (`type` catches "type.com" but not
  "typeform"), so list joined-up variants separately.
- `searchAnalytics.query` is a `POST`. A read-only connection grant in Type can
  block it even though the call only reads data (Step 0).
- A property needs `2 × compare_period` days of data (plus the lag) before the
  default comparison works. Newer properties need a shorter `compare_period`.
- Estimated gains assume impressions stay flat and the curve holds. Treat them
  as a way to rank opportunities, not a forecast.
- Rising-query trends compare two windows. There is no weekly series, so
  there's no "↑ 3 weeks" style trend.
- Full-mode query totals are summed from query × page rows, so split queries
  can show more impressions than the Queries tab (DATA_CONTRACT.md explains
  why).
- The query-dimension API data omits anonymized queries and returns a limited
  number of rows per day. Very large sites see the head of the distribution, not
  all of it.
- Reduced (UI) mode can't detect cannibalization and is capped at 1,000 rows
  per tab.
- Google Search Console only. Bing Webmaster data and other search types
  (image, video, news, Discover) are out of scope.
- It produces findings and suggested actions. It does not edit pages, titles or
  redirects.

## Verification status

**Tested offline:** `bash run.sh` matches `expected_output.txt`
byte-for-byte across repeated runs. `bash tests.sh` covers bad and missing
input (exit 2 with file, row and column), mixed export shapes, the low-volume
guardrail, reduced mode (including unquoted fallback text in actions),
impressions-weighted position, the API JSON converter, `--plan` request bodies,
the empty-prior-period message and `--first-data-date` sizing, zero-click
countries being left out, and the `brand_terms` / `exclude_query_patterns`
filters. All sample data is fictional.

**Run live once (QA):** against a real property. That run surfaced the
new-property, proxy `POST` and brand-query issues handled above.

**Not yet verified live:** real paging beyond 25,000 rows, real UI export
files (header names and number formats follow Google's current layout but
haven't been checked against a live download), and how connections are exposed
in every Space. Check the first
real run's summary against the Search Console Performance chart for the same
dates.
