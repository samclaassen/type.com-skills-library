---
name: landing-page-cvr-analyzer
description: Analyze landing page conversion rate by traffic source (UTM) for ecommerce and lead-gen brands, track how each landing page x source is trending against its own history, and alert only when something genuinely moved. Separates real conversion changes from traffic-mix shifts, locates which funnel step broke, detects broken conversion tags and slow multi-month drift, and reports what the brand's volume can and cannot detect. Runs on a portable data contract from any warehouse (BigQuery, Snowflake, Redshift), GA4, or a CSV. Read-only. Interactive setup on first run; no conversion-rate benchmarks baked in -- conversion judgements compare the brand with its own trailing history. Includes CRO diagnostic references, page-review guidance, and an experiment brief. Can provide a qualitative screenshot review without analytics, clearly separated from measured findings. Use whenever the user wants landing page conversion analysis, CVR by UTM source or campaign, "which landing pages are converting", "why did conversion rate drop", LP performance monitoring, conversion anomaly alerts, or a recurring landing page report -- even if they don't say UTM or name a data source.
---

# Landing Page CVR Analyzer

Landing page conversion rate, sliced by traffic source, watched over time, with alerts
that fire only when something real moved.


## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `landing-page-conversion-analyzer`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Expert knowledge included
Before interpreting a report, read references/cro-foundations.md. It supplies the conversion framework, measurement definitions, and rules for separating observation from hypothesis.
- For page inspection or a screenshot-only request, read references/landing-page-review.md.
- Before proposing a change, read references/experiment-playbook.md. For an early review, give a compact provisional hypothesis, metric, and next check; group unknowns. Use templates/experiment-brief.md when the user wants to plan the actual experiment.
- Consult references/worked-examples.md for mix shifts, checkout symptoms, screenshot-only reviews, invalid experiment allocation, and low-volume cases.
- references/sources.md records the origin and review date of the knowledge. Core guidance is included locally; browsing these links is optional unless current specifications need verification.

The knowledge is general; the business facts come from the user. Save confirmed audience, offer, funnel, and constraints in an editable business brief with source dates. Do not prefill another company's context or present generic advice as measured evidence.

For a screenshot-only review, skip the analyzer setup and baseline steps below. Inspect only what is visible, provide a qualitative review using the checklist, and label proposed changes as hypotheses. Ask for analytics only if a measured performance report is wanted. The Python analyzer does not capture screenshots, inspect websites, measure Core Web Vitals, or run A/B tests; these are agent-assisted follow-up activities requiring the reader's tools and authorization.

## The one rule

**No CVR standards, benchmarks or industry averages determine the analyzer's findings.** Every
judgement is this brand against its own trailing history. That is what lets it run on
messy data on day one and get sharper as the data gets cleaner, instead of needing a
clean-data project before it produces anything.

If the user asks "is 2.4% good?", the honest answer is that it depends on their traffic
mix, price point and category, and that the useful question is whether it is better or
worse than *their* 2.4% last month, on the same mix. Offer to set explicit targets later,
once the data is clean enough to trust.

---

## Which mode you are in

| Situation | Mode |
|---|---|
| First run, or no `state.json` with baselines | **Setup**, then **baseline** |
| Any run after that | **scan** |

Never run a scan without a baseline. There is nothing to compare against, and inventing
one produces confident nonsense.

---

## Setup — first run only

Do this conversationally. **Parse first, then ask.** Look at what the data actually
contains before asking the user anything, so every question is specific and short. Never
ask something the file could have told you.

**1. Find the data.** Ask what they have, in this order of preference:
warehouse (BigQuery GA4 export, Snowflake, Redshift) → GA4 API → a CSV or parquet export.
`DATA_CONTRACT.md` §8a explains warehouse mapping and §8b explains API mapping.
This package includes no live connector; verify the source schema and session attribution.

**2. Discover the conversion events — do not assume.** Pull the list of available
conversion/key events, then **probe each candidate for volume at the grain you will
analyse.** This is not optional. A dead
event yields a clean, confident, meaningless zero, and nobody notices for a month.

Report the volumes back and let the user choose. If they pick an event with too little
volume to support slice-level detection, say so with the number, and propose a
higher-volume leading step for alerting plus the thin one as the business outcome.

**3. Build the funnel with them.** Ordered steps, one marked `is_conversion`. Two or more
steps is strongly preferred: it helps distinguish a checkout-stage symptom from a traffic-mix change. Neither establishes the cause without follow-up evidence. Mark thin
business outcomes `alert_level: site`.

**4. Confirm the business rules the data cannot show.** Which markets they ship to. Which
pages are not meant to convert. Any dated events — a migration, a sale, an outage — that
should be kept out of baselines.

**5. Scheduling.** Ask when their data settles — most analytics teams know theirs.
Recommend running after that and record it in `config.schedule.settles_by`. If they want
to run earlier, say plainly what they will see: yesterday's numbers may still move, and
the run may drop the most recent day. Note that this matters for a **daily** cadence and
barely at all weekly or monthly. Tell them they can also refresh on demand whenever they
want a live read.

There is deliberately no lag model to configure. Two things happen automatically: a day
that looks structurally incomplete is dropped with one line saying so, and any number
that changed since the last run is named. Neither needs a setting.

**6. Set the notification policy.** Walk them through `notification` in config — the
dollar and revenue-share bars for a push. Make the point explicitly: this does not change
what gets analysed. Everything is evaluated and everything lands in the report whatever
its size; this decides only what interrupts someone.

**7. Write `config.yaml`, run baseline, and walk them through the hygiene report.**

---

## Running it

```bash
python3 scripts/analyze.py \
  --data sessions.parquet \
  --config config.yaml \
  --state state.json \
  --meta meta.json \
  --out ./out \
  --mode auto
```

Outputs `out/findings.json` (what you read) and `out/state.json` (what the runtime
persists and passes back next time).

**The engine does the scanning; you do the interpretation.** Never load the session table
yourself, never re-derive a rate, never scan the cube in your own reasoning. The script
tests every cell and hands you the ~15–25 that survived. That is what keeps the cost of a
run flat whether the brand has 400 landing pages or 40,000.

If the script returns `{"status": "refused"}`, relay the reason and stop. It refused
because producing a number would have been worse than producing nothing.

---

## Reading `findings.json`

Read these in order. The order matters — the standing tables are what most runs are
for, and item 2 changes how everything below it should be read.

**1. `channel_stack`, `source_stack`, `page_stack`** — the standing picture. Every
channel, every source, every landing page, present on **every run whether or not
anything is wrong**. Revenue, sessions, CVR, its own baseline, the change, dollars,
and the MDE for that row.

Most mornings there are no findings, and this is the whole output. A tool that
returns only anomalies renders an empty page on a healthy day, and a team reads an
empty page as a broken tool rather than a healthy account. Lead with these when the
findings list is short, and never apologise for a quiet run — show the tables.

Two columns to use rather than skip. **`mde_rel`** is the smallest change that row
could have shown reliably: a channel down 1.6% against a 1.7% sensitivity estimate does not establish a reliable
change from that comparison alone, and saying so is more useful than silence. **`standardised_cvr`** re-weights
a landing page to the portfolio's own source mix, so a page fed by cheap paid
traffic is comparable to one fed by email; it is suppressed (null) on the source and
channel axes, where a row usually carries one medium and standardising would hand
back its own rate dressed as a correction.

**2. `site.notes`** — did the blended rate move, and was it *conversion* or *mix*?
This is the most important sentence in the run. If the site's move is mix-dominated, the
blended rate fell because the traffic composition changed and individual sources are
converting exactly as before. Lead with that, and make sure nobody rebuilds a page over it.

**3. `alerts` where `is_root` is true** — the distinct problems, ranked by dollars at
stake. These are the ones somebody acts on. Each carries `funnel.worst_transition`, which
identifies the transition with the strongest decline, not a proven cause.

Everything else in that list marked `explained_by` is **the same problem through a wider
lens** — when one cell genuinely breaks, every aggregate containing it moves too. Say the
root once; do not re-report its symptoms. The bundled synthetic showcase verifies that overlapping
aggregate findings are attributed to the affected page/source cell. Each explained finding carries what it moves to
once the root is removed, so the attribution is auditable rather than asserted.

**Tiers.** Every finding carries `notify`, `flag` or `context`. `notify` is what to push.
`flag` is real, measured, and below the brand's push bar — it belongs in the artifact,
ranked, unpushed. Never promote a `flag` to urgency because its percentage looks dramatic;
a 40% drop on $800 of revenue is not a headline. `context` is recorded for traceability.

**4. `mix_shifts`** — cells whose move is composition, not conversion. Deliberately a
separate list, and deliberately not ranked with the alerts: the largest number in the run
is often a mix artifact, and putting it at the top of the page trains people to distrust
the whole report. This list is for whoever owns traffic, not whoever owns the site.

**5. `tracking_breaks`** — a conversion step that collapsed while traffic held. This may reflect
a collection defect or an actual conversion decline; verify independently. **Say this before anything else in the output**, and
say plainly that nothing else in the run should be actioned until it is verified.

**6. `drift`** — slow multi-month decay that no week-over-week test will ever catch. A page
losing 1% a week has lost a third of its performance by year end. Nobody notices these
without being shown them.

**7. `untested_but_material`** — cells carrying real revenue that were below the volume
floor. **Untested is not unchanged.** Surface these when they are large; silence about them
is how a dashboard teaches people to trust a blank page.

**8. `restatement`** — days already reported on whose numbers have since changed.
Analytics platforms restate as late events arrive; this is not an error. But anyone who
saw the earlier figure should be told it moved, and any finding resting on those days is
marked provisional.

**9. `notification`** — which findings clear the brand's push policy. Everything else is
still in the file and still belongs in the artifact.

**10. `data_findings` and `scan_stats`** — what the data got wrong, and how much was tested.

---

## Writing the output

**Lead with the next investigation.** Name the affected page, source, measured transition, and uncertainty. For example: "Verify purchase tracking and inspect checkout for the affected page/source segment." Put the engine-produced rates and modeled impact below that action. A screenshot review instead leads with a visible observation and the evidence needed to test its hypothesis.

**Material impact before dramatic percentages.** Rank by modeled revenue at stake when available; otherwise use qualified-outcome volume and uncertainty. Small findings may still merit context. Revenue at stake is not realized loss or guaranteed recovery.

**Name the owner.** The funnel transition tells you who: a decline at `begin_checkout →
purchase` warrants analytics verification followed by ecommerce or engineering investigation; a decline at `sessions → view_item` warrants checking page load, message match, and traffic quality. A mix shift calls for review with the media owner. These are investigation routes, not established causes. Both an in-house team and an agency read this, and ecom and paid
media have to be able to tell instantly which findings are theirs.

**"Nothing worth acting on" is a valid and valuable output.** But say it precisely:
*"nothing detectable moved"* is not *"nothing moved."* Use the sensitivity table to say
which is true. A silent morning on an account that can only detect a 100% swing is not
reassurance.

**Use engine output for analyzer-derived quantitative findings.** Do not fill missing metrics or invent causes. User-supplied numbers, explicit teaching arithmetic, and results from other validated tools may be reported with their source and limitations; they are not analyzer output. Label a causal explanation as a hypothesis and say what would confirm it.

**Every conversion-rate judgement is relative to this brand's own history.** Do not use an industry CVR benchmark to diagnose it. General CRO principles guide hypotheses; they do not establish a measured cause.

---

## Data hygiene — audit, never alter

The engine reports what it does not understand, with the traffic and revenue behind each
problem attached, so a data fix arrives with a business case instead of as one more ticket.

**It never merges source values.** `fb`, `facebook` and `Facebook_Ads` stay three rows.
Fragmented sources are reported as clusters — with the combined volume they would have —
and the brand decides. Confirmed groupings go in `config.source_groups`, written by a
person. `utm_medium` and `utm_campaign` are in the contract precisely so a human can look
at a fragment and place it without guessing.

Two detectors, because one is not enough:
- **String clusters** catch spelling and case variants. They structurally cannot catch
  `fb` for `facebook` — an initialism shares no prefix with the word.
- **Behavioural candidates** catch those: same initial letter, abbreviation-length, and
  converting inside a narrow equivalence band. Note that *equivalence*, not
  non-significance, is the right test — at 900k sessions a 2.33% vs 2.51% gap is
  overwhelmingly significant and practically identical.

When you present these, propose the groupings you think are right and ask for confirmation.
Do not write them into config yourself without the user saying yes.

---

## What this measures, and what it does not

Session-scoped: the session that converts is credited to its own landing page and source.
This is the only definition where slices reconcile to the whole, which is what makes
landing-page comparison valid at all.

It **under-credits multi-session journeys** — a visitor who lands on a blog post, leaves,
and returns via direct to buy credits direct. This is a **page and traffic-quality
diagnostic, not a channel P&L.** If the brand's revenue reporting disagrees, both are right
about different questions. Say that rather than reconciling to a number you cannot defend.

---

## Notes for a runtime rebuilding this

The skill owns no storage, hosting or scheduling. `state.json` is read as an input and
emitted as an output; the runtime persists it. `config.yaml` is authored by a human. The
skill is brand-agnostic — no brand, event name or threshold is hardcoded anywhere.

Recommended cadence: daily. Landing page and source moves fast — a deploy, a broken
template, a new campaign. Deeper slices need more volume per window; the sensitivity table
in the baseline output says how much, for this brand specifically.

`references/methodology.md` documents every statistical choice and why the obvious
alternative is wrong.

## Local setup

Use Python 3.10 or later and pandas 2.2 or 2.3 (the dependency file excludes pandas 3). Create a virtual environment and install the included requirements.txt. Keep the entire package together. Copy config.example.yaml to config.yaml and replace example values with your own confirmed funnel, currency, shipping markets, and notification policy. Supply your own read-only data export using DATA_CONTRACT.md. Run python3 tests/verify.py to check the engine on synthetic data before using your export. These tests do not validate a live account connection or your data mapping.

## Reproducible public example

Run the bundled `examples/showcase/README.md` walkthrough without connecting an account.
Its CSVs, configuration, and output are synthetic demonstration data, never customer results.
The public edition includes a site-summary correction so the headline uses the declared
conversion step. Read `PUBLIC-EDITION.md` for the exact changes and validation scope.
