---
name: lead-triage-router
description: Scores inbound leads with a tunable ICP-fit and intent rubric, assigns a tier and a routing owner from your territory rules, and prints the exact signals behind every score so reps can see why a lead reached them.
---

# Lead Triage and Router

Takes a raw inbound lead export and produces a tiered, routed, explainable
work list. The rubric lives in `config/rubric.json`, not in the code, so
marketing and sales can argue about weights in a pull request instead of in a
meeting.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `lead-triage-router`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## When to use this

- Daily or hourly triage of form fills, trials, and content downloads when the
  volume has outgrown a human reading each one.
- Rebuilding lead scoring after an ICP change, a new segment, or a territory
  reshuffle — run the new rubric against last month's leads and compare.
- Auditing an existing scoring model: this shows its reasoning, whereas most
  CRM scoring does not.

## Inputs

`DATA_CONTRACT.md` has the exact columns, types, and units. You need:

1. `leads.csv` — one row per lead, firmographics plus intent counters.
2. `routing.csv` — the territory/queue rules, in priority order.
3. `config/rubric.json` — weights, bands, tiers, disqualifiers (ships with
   sensible B2B SaaS defaults).

### Gathering the inputs through Type integrations

- **HubSpot**: export contacts with `email`, `jobtitle`, `company`,
  `num_employees` (or the enriched company property), `annualrevenue`,
  `industry`, `country`, `hs_analytics_source`, `createdate`, and
  `notes_last_contacted`. Page-view and session counters come from the HubSpot
  analytics properties or from your product analytics tool.
- **Salesforce**: `Lead` object plus any enrichment fields (Clearbit, ZoomInfo,
  Apollo) that populate employee count, revenue, and industry.
- **Attio**: the People object joined to the linked Company record.
- **Product analytics (Amplitude, PostHog, GA4, warehouse)**: pricing page
  views, sessions, trial starts. Aggregate to a 30-day window ending on the
  date you pass to `--as-of` so the counters and the recency multiplier agree.
- **Routing rules**: usually a spreadsheet RevOps already maintains. If not,
  write it once by hand from the contract; it changes rarely.

If an enrichment field is missing, leave it blank. Blank fields score zero and
are listed in the data-quality section — do not guess values, because a guessed
employee count silently inflates a tier.

## Running it

```bash
python3 scripts/lead_triage.py \
  --leads path/to/leads.csv \
  --routing path/to/routing.csv \
  --config config/rubric.json \
  --as-of 2026-09-22
```

- `--as-of` is required and drives the intent recency multiplier.
- `--fallback-queue NAME` sets where leads go when no routing rule matches
  (default `unrouted`); those leads are counted separately in the summary.
- `--json` emits the full scored record, contribution by contribution.
- Bad input exits `2` naming the file, row, and column.

See `examples/run.sh` for a complete working invocation.

## How the score works

```
total = fit_weight x fit_score + intent_weight x (intent_score x recency_multiplier)
```

- **Fit (0-100)** sums five firmographic bands — employee count, revenue,
  industry, country, and title seniority — then normalizes against
  `fit.max_points`. Title matching is whole-word, so "Director" does not match
  the executive keyword list, and a multi-word keyword such as "head of" must
  match consecutive whole words, so "Forehead Office Manager" scores as a
  manager, not a head of function. An industry that is listed but scores below
  the `_default` is labelled as low-fit, not as an ICP industry.
- **Intent (0-100)** sums weighted behavioural signals. Count signals have a
  per-signal cap (three pricing-page views should not outscore a demo request),
  and the total is capped at `intent.max_points`.
- **Recency multiplier** discounts intent by how long ago the lead last did
  anything. A lead with no activity date is discounted to the floor rather than
  treated as fresh.
- **Disqualifiers** run before tiering. A personal email domain, a blocked
  industry or country, or a company below the employee floor sends the lead to
  the nurture queue with the reason attached, whatever it scored.
- **Tier** is the highest band whose `min_score` the total meets.
- **Routing** walks the rules in `priority` then `rule_id` order and takes the
  first match on tier, segment, and region. `*` is a wildcard; keep one
  catch-all rule at the bottom.

## Tuning

Edit `config/rubric.json`:

- `fit_weight` / `intent_weight` must sum to 1.0. Product-led motions usually
  run 0.4 / 0.6; enterprise outbound-heavy motions run 0.7 / 0.3.
- `fit.*_bands`, `industry_points`, `country_points`, `title_bands` — the ICP
  itself. Points are arbitrary units; only their ratio matters.
- `intent.signals` — add a field here and the contract, and it is scored. Caps
  stop one loud signal from dominating.
- `tiers[].min_score` — move these first when the A tier is too big for the
  team to work inside the SLA.
- `disqualifiers` — the hard filters, kept separate from scoring on purpose.

After any change, rerun against a week of known leads and check the tier counts
against what the team can actually handle.

## Reading and presenting the output

- **Summary** gives the tier distribution and average score. If Tier A is more
  than about 15% of volume, thresholds are too loose.
- **Routing load** is the operational number: how many leads each queue and rep
  just received. Check it against capacity before you send the assignments.
- **Per-lead blocks** are the deliverable. The `fit` and `intent` lines
  reconstruct the score exactly — paste them into the CRM note or the Slack
  handoff so the rep never has to ask "why did I get this?"
- **Data quality** tells you what enrichment is missing. Persistent blanks
  there are a better investment than another weight change.

## Limits

- It scores the *record*, not the person's real buying intent. A rubric cannot
  see a stalled budget cycle or a competitor already in the account.
- Keyword title matching is coarse. Unusual or non-English titles land in the
  default band; add keywords rather than accepting the drift.
- Counts are taken as given. If the 30-day window in the export does not end on
  `--as-of`, the recency multiplier and the counters disagree and scores skew.
- No deduplication: two form fills from the same person are two leads. Dedupe
  in the export, or in the CRM, first.
- No write-back. It does not assign owners in the CRM, send notifications, or
  enroll anyone in a sequence.
- Disqualifiers are rules, not judgment. A 4-person company with a real budget
  still gets filtered; review the DQ list weekly rather than trusting it blindly.
