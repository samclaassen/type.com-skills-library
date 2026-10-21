---
name: holiday-support-macros
description: Get a DTC support team ready for BFCM and the holidays. Learns last season's holiday ticket mix from a helpdesk export (Gorgias, Zendesk, Help Scout and others), forecasts this season's tickets and the agents needed on peak days, finds ticket types customers will ask about that have no written policy, catches stale or contradictory macros (past dates, wrong return windows or cutoff dates, old codes, delivery promises), and generates a full set of holiday macros whose facts come only from the store's written policies, with a dated checklist to have them live by Nov 20. Read-only; macros are drafts for review.
---

# Holiday Support Macros

Black Friday 2026 is **Nov 27** and Cyber Monday is **Nov 30**. Holiday support goes wrong in predictable ways. "Where is my order?" swamps the team the week after Black Friday while the backlog ships. Customers who bought a week early ask for the sale price, and with no written rule each agent answers differently. A code that doesn't stack with the sale produces a wave of "my code isn't working". And last year's macros are still live: "Order by Dec 18, 2025", a 30-day returns answer that ignores the holiday extension, a code that expired.

This skill does the preparation from local exports and puts a date on every step: **learn last season's mix → forecast tickets and staffing → close the policy gaps → fix or retire stale macros → generate the holiday macros from the written policies → have them live by Nov 20.**

It sits next to other skills and does not repeat them:
- **Holiday Shipping Cutoff Checker** works out the last safe order dates and the BFCM backlog. Pass its `--json` result unchanged with `--cutoffs`: its dates, checkout rates, same-day cutoff time and backlog replace policies.json's where they disagree, and the report warns with both values. Run it with the Budget Planner's `--csv` plan as demand and `--aov` set to the planner's AOV (88.66 in the sample), or the backlog will differ.
- **BFCM Offer & Discount Config QA** checks how codes and the sale are set up in Shopify. Use its fixed configuration for the codes listed in `policies.json`.
- **BFCM Holiday Budget Planner** projects BFCM orders. Its `--json` plan is this skill's order projection (its `--csv` has no orders column and is refused). Pass it together with last season's daily orders: `--orders last_season.csv --orders plan.json`. If the orders file also holds this season's projection rows, remove them or add `--prefer-plan`.
- **BFCM Live Pulse** watches the sale as it runs; **Review Insight Miner** clusters reviews and tickets into product themes after the season.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/`, `tests/`, `references/` and `DATA_CONTRACT.md`) are in your working folder at the same relative paths. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it to the matching path. In type.com, read them with the skill-file tools. Anywhere else, the type.com Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `holiday-support-macros`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`. Python 3.8+ and the standard library only; there is nothing to install.
4. **Try the sample first.** Run `bash examples/run.sh`. Its output must match `examples/expected_output.txt` exactly. If it doesn't, stop and report the first differing line rather than running on real data. `bash tests/run_tests.sh` runs the full check suite.

## When to use this

- October to mid-November, for a DTC brand's support team, or for each brand an agency supports.
- Someone asks "are our holiday macros ready?", "how many tickets should we expect over BFCM?", "do we need more people on Black Friday weekend?" or "what do we tell customers who want the sale price?"
- After a policy changes (new cutoff dates, a new or retired code, a different return window): re-run to catch macros that now contradict it.
- After the new macros are live: re-run with a fresh macros export to mark them verified, and again on the final-run date.
- On a schedule: weekly from October to Black Friday. Stay silent when the verdict is READY and nothing changed.

## Operating rules

1. **Read-only.** The script reads local files and writes a report and drafts. Never send a reply, create, edit or delete a macro, change helpdesk rules or tags, or change store settings unless the user approves that specific change. When they do, make it, read it back, export the macros and re-run to confirm.
2. **Never invent a policy.** Every date, number, price, code, rule and promise in a macro comes from `policies.json` (or the Shipping Cutoff Checker's `--cutoffs` result). If a policy is missing, report the gap and ask the owner to decide; don't fill it from "what most stores do" or a competitor's page. The script enforces this: no macro without its policy, and templates can't carry facts or promise words (guarantee, refund, free, always, never, Christmas, holiday, arrive by, in full) of their own.
3. **Policies are the business's decision.** For a gap, lay out the options in `references/playbook.md` §3 and let the owner choose. Write the decision into `policies.json` and publish it where customers can see it.
4. **Macros are drafts.** A person reviews every macro before it goes live. Tone can change; facts can't (see "Adapting the tone" below).
5. **Quote exact numbers**: X of Y tickets, the percentage, the date. Forecasts are estimates scaled from last season; say what they rest on.
6. **Keep exports local.** Tickets carry names, emails and order numbers. The report masks emails, phone numbers, street addresses, names after "my name is" / "this is" / a sign-off, and long numbers in its examples; `tickets_classified.csv` holds the raw ticket text (formula-safe for spreadsheets). Don't paste exports into chat, and delete them when done.
7. **Escalations go to a person.** Never answer anything on the escalation map with a macro.
8. Say which state each item is in: **detected**, **recommended**, **applied** or **verified**.

## Gathering the inputs

Exact columns and fields are in `DATA_CONTRACT.md`.

1. **Holiday policies, required** (`policies.json`). Write them from the store's published pages (shipping, returns, FAQ) and its discount setup: shipping services with last safe order dates and the checkout rate each one sits under, the share of customers the dates hold for, the BFCM backlog note, returns (standard and holiday window), price adjustments, discount rules and this season's codes, change/cancel window, address changes, gift options, backorders, damaged items, international shipping, holiday support hours (and the dates weekends are covered) and escalation thresholds. Leave out what isn't decided. The report will name it.
2. **Last season's tickets, required** (`tickets.csv`). Export last November to December from your helpdesk as CSV with created date, subject, first message (a preview is enough) and tags.
3. **Existing macros, optional** (`macros_existing.csv`): name and body of every macro or saved reply in use. Without it, stale macros are not checked.
4. **Orders, optional** (`orders.csv`): last season's orders by day from Shopify Analytics, and this season's projection by day (your own, or the BFCM Holiday Budget Planner's `--json` plan). A date may appear only once: with the planner, pass last season's orders plus `plan.json`, and remove this season's rows from the orders file or add `--prefer-plan`. Without it, the forecast is last season's tickets × `--growth`.
5. **Capacity, optional**: agents on shift on weekdays and weekends, and tickets one agent handles in a shift (your helpdesk's productivity report, or last December's average).

If the user has connected tools that can export these (a helpdesk integration, Shopify), you may pull the same data that way, but write it into these files and run the script on them. Never ask the user to paste API keys or tokens into chat.

## Running it

```bash
python3 scripts/holiday_support.py --policies policies.json --tickets tickets.csv \
  [--macros macros_existing.csv] [--helpdesk gorgias|zendesk|help-scout|shopify-inbox] \
  [--orders orders.csv ...] [--prefer-plan] [--cutoffs cutoffs.json] [--date-format mdy|dmy] \
  [--agents 2 --weekend-agents 1 --tickets-per-agent 45] \
  [--as-of YYYY-MM-DD] [--out-dir out/] [--json analysis.json]
```

`bash examples/run.sh [OUT_DIR]` runs it on the bundled sample store (Fernhill Goods, fictional) and writes the macros to `OUT_DIR`.

What it does:

1. **Learns last season's mix.** Sorts every ticket with the keyword rules in `references/ticket_rules.json` (whole words; escalations first, then "first" phrases such as "hasn't arrived", then the first matching type; a keyword with "not", "no", "isn't" or "wasn't" up to 3 words before it doesn't count; edit freely) into escalation (chargeback, legal, safety: flagged for a person), WISMO, shipping deadline, discount code, price adjustment, cancel/change, address change, return/exchange, damaged, gift options, out of stock, or other. Reports the mix, tickets per 100 orders by type, peak days, and the most common unclassified subjects so you can add keywords.
2. **Forecasts this season.** Each day = last season's tickets on the same weekday (364 days earlier, so Black Friday lines up) × this season's projected orders ÷ last season's, over the 7 days up to that day. Then agent-hours and agents needed on the busiest days (the day's tickets plus those still waiting), days over capacity (weekends outside `support_hours.weekend_coverage` count as closed), and how long unanswered tickets carry over.
3. **Finds policy gaps.** Any ticket type with at least 3% of last season's tickets and no policy to answer it is a **GAP** with a decide-by date (a week before the macros go live).
4. **Audits existing macros.** Past dates and years, cutoff dates and return windows that contradict the policy, other numbers (free-shipping threshold, damage-report and change windows, reply time), codes that aren't this season's, and delivery promises such as "guaranteed by Christmas".
5. **Generates the holiday macros** from `references/macro_templates.json`, one or more per ticket type (for example WISMO before shipping during the BFCM backlog, WISMO in transit, and three stages of shipping deadlines, with one date per checkout rate, never a carrier date customers can't choose), filled only with policy values, with `{{customer_first_name}}`, `{{order_number}}`, `{{tracking_url}}` and `{{agent_first_name}}` left for the helpdesk. A macro whose policy is missing is not generated and the gap is reported.
6. **Gives a verdict and a dated checklist**: `NOT READY` (any critical), `FIX BEFORE NOV 20` (warnings only) or `READY`, with when to start and stop using each time-bound macro.

Exit codes: 0 means the report was written, 2 means invalid input (with the file, line or field and the reason).

## Acting on the report

Present the verdict and the issues, then work through them in this order. Each change needs the user's approval. Details and templates are in `references/playbook.md`.

1. **Stale macros first** (CRITICAL within a week). They may already be in use. Retire or correct each flagged macro; the report names the replacement.
2. **Close the policy gaps** by the decide-by date. Price adjustments are the usual one: the playbook lists the options and what each costs. Add the decision to `policies.json` and re-run, and the macro appears.
3. **Review the drafts** in `macros_preview.md`: check each fact against the policy, adapt the tone (below), and map the placeholders to your helpdesk with the table in `macros_preview.md`. That table shows which variables were confirmed in each helpdesk's documentation and which say "verify in your helpdesk".
4. **Staffing.** Cover the days over capacity, especially weekends and days support is closed, or agree the response time customers will see.
5. **Go live by Nov 20**, tagged, tested on a test ticket. Export the macros and re-run with `--macros --helpdesk <yours>`: unchanged drafts with their placeholders mapped show as **verified**, an edited fact is caught (**applied**), and a macro with a placeholder still in generic form or one your helpdesk can't fill (order number and tracking link on Help Scout and Shopify Inbox) shows as **needs manual fill**, never verified.
6. **Switch on the dates in the checklist** (BFCM backlog macro on, then off; the shipping-deadline macros in three stages), and re-run on the final-run date. Once the backlog has cleared (the catch-up date), the same-day sentence in “WISMO: not shipped yet” and “Shipping: holiday order-by dates” still describes the Black Friday rush; with the owner's approval, cut it back to the plain same-day rule so customers don't read a date that has passed.

### Adapting the tone to your brand voice without changing facts

You may change: greetings and sign-offs, word choice, sentence order, warmth, emoji (if your brand uses them), length, and formatting such as bullets. You may not change: any date, weekday, number, price, percentage, code, shipping method name, URL, condition ("unused and in the original packaging"), what is or isn't offered, or who pays for what. Don't add promises ("guaranteed", "for sure") or facts that aren't in `policies.json`, and keep every placeholder. Easiest: put your voice in the templates (they hold wording only) and re-run, so next season starts in your voice. After editing, export the live macros and re-run with `--macros`: any changed or dropped date or number shows as **applied** instead of **verified**.

### Escalation map: what must go to a person

The report builds this from `policies.json` → `escalation` and counts last season's tickets that would have matched. Never answer these with a macro:

- **Money above the threshold**: an order over `order_value_over`, or a refund, credit or replacement worth more than `refund_over`, needs the owner's approval.
- **Chargebacks, payment disputes, fraud**: same day, to the owner.
- **Safety**: injury, burns, allergic reactions, broken glass. Same day, to the owner; keep the photos.
- **Legal threats, press, influencers.**
- **Policy gaps**: until a gap is decided, every such ticket goes to the owner so each customer gets the same answer.
- **Third contact about the same order**: a person, not another macro.

## Files

- `scripts/holiday_support.py`: the analyzer and macro generator. Local files only, deterministic output.
- `DATA_CONTRACT.md`: every input field, the facts templates can use, outputs, thresholds and dates.
- `references/ticket_rules.json`: editable keyword rules for the ticket types.
- `references/macro_templates.json`: the macro templates (wording only; facts come from the policies).
- `references/playbook.md`: tuning the rules, forecasting and staffing, options for common policy gaps, helpdesk placeholder syntax with sources, brand-voice examples, the escalation map, the 2026 timeline and a status-update template.
- `examples/run.sh`, `examples/expected_output.txt`, `examples/data/`, `examples/expected/`: sample run and its expected report, macros, preview and forecast. `examples/make_fixtures.py` regenerates the sample data.
- `tests/run_tests.sh`, `tests/make_cases.py`: 24 end-to-end checks on hand-checkable inputs, including a Budget Planner → Shipping Cutoff Checker → this skill run when those packages sit next to this one (skipped, and said so, otherwise). No network needed.
