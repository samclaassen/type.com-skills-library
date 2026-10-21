---
name: investor-update-drafter
description: Draft a monthly or quarterly investor or board update from the period's Granola meetings, your metrics (Stripe or a metrics file) and last period's update. It writes TL;DR, a key-metrics table with period-over-period change, highlights, lowlights, product, customers and go-to-market, team and hiring, asks, and what we said last time vs what happened. Every metric figure comes from metrics data with its source and as-of date, customers and people are named only when marked public (pattern-based checks hold back text that names others), items tagged confidential are left out or kept to a board-only section, and nothing is sent. Use when someone says "draft this month's investor update", "board update for Q3", "write our investor update", or "what did we promise investors last month and did we do it". On request only; drafts only.
---

## What this does

It drafts the update a founder sends investors (or the board) every month or quarter, in the standard format:

- **TL;DR**: the headline metrics with their change, the top highlight, the top lowlight and the top ask.
- **Key metrics**: MRR, ARR, customers, burn, runway and the rest, this period vs last, with the change, the source
  and the as-of date of every figure. The script formats every figure and computes every change; the agent never
  types a metric figure.
- **Highlights, Lowlights, Product, Customers & go-to-market, Team & hiring, Asks**: extracted from the period's
  Granola meetings (customer calls, all-hands, hiring, partner meetings), each linked to its meeting. Lowlights is
  never omitted.
- **What we said last time vs what happened**: every commitment from the previous update with a true status, checked
  against this period's figure when the commitment had a numeric target.
- **Before you send**: what was withheld and why, metrics that moved the wrong way, restated figures, coverage gaps,
  and the meetings cited, for the founder to review and delete.

Granola already writes notes for each meeting. This skill reads a whole month or quarter of them, combines them with
billing and finance figures and last period's promises, and applies rules that are easy to get wrong by hand: no
metric figure without a source, no customer named without permission, no confidential item in an investor email, no
promise the founder didn't make. The script enforces what it can check; its text checks are pattern-based, so the
founder still reviews every draft. It drafts; the founder edits and sends.

## Before you run

This skill ships a script and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (`build_update.py`, `update.json`, the sample files,
   `run.sh`, `expected_output.txt`, `tests.sh` and the docs) is in one working folder alongside this SKILL.md. All
   files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md. If yours did,
   fetch each file from this skill's published files and write it into that folder under the same filename. In Type,
   read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path,
   content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug
   `investor-update-drafter`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters), and its hash where your tools
   report one, with the published version before running. Never run a script you summarised or reconstructed from
   memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 ...` and `bash run.sh`.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't,
   stop and report the first differing line rather than running on real data.

## Config

```yaml
cadence: monthly            # monthly | quarterly: what "this period's update" means
audience: investors         # investors | board: the default audience
billing: ""                 # stripe | none: set in Step 0
metrics_file: ""            # optional: path or doc holding metric,period,value,source,as_of rows (burn, runway, headcount)
previous_update_from: ""    # gmail | doc | paste: where last period's update lives, set in Step 0
granola_folder: ""          # optional Granola folder id to narrow meetings (paid Granola plans)
internal_domains: []        # your company's email domains: tells customer calls from internal meetings
public_names: []            # customers, partners and people the founder has cleared to be named in updates
gmail_draft: ask            # ask | never: offer a Gmail draft (never a send) after the founder approves
recipients: []              # investor or board emails for that draft
```

These are defaults. Do not edit them here. The values confirmed in Step 0 live in a small settings file,
`investor-update-drafter-settings.md`, in the user's working folder or Space. Read it at the start of every run; any
key it sets overrides the default above. The script's own rules (metric definitions, rounding, the term lists for
confidential, forward-looking and figure checks) live in `update.json`; `DATA_CONTRACT.md` documents every key.

`public_names` starts empty on purpose: no customer, partner or person is named until the founder says so.

## Step 0: First run, confirm sources

Run this step when the settings file is missing or a configured source is no longer connected.

1. **Detect.** Check which sources this Space can actually read with one real read call each: Granola (`get_account_info`,
   then `list_meetings` for the last 7 days), Stripe (read one subscription or customer), Gmail (search for a previous
   update), Google Drive or Type docs (a metrics sheet or past update). A tool that is listed but fails on a read does
   not count. In Type, also name integrations that are connected for the workspace but not added to this Space, and
   offer to add them; use them only after they are added and a read succeeds.
2. **Propose.** Tell the founder what you found and the values you would set, for example: "Granola and Stripe are
   connected; Gmail is connected for the workspace but not added here, want me to add it? I'd use `billing: stripe`,
   `cadence: monthly`, and look for last month's update in Gmail. Which customers may be named in investor updates?"
3. **Wait for the founder to confirm.** Never pick a billing source, a previous update or a list of public names on
   your own.
4. **Save** the confirmed values to `investor-update-drafter-settings.md`. Never edit this SKILL.md. If you cannot
   write files there, give the founder the lines to save.

## Step 1: Preflight

This skill only reads. It never writes to Granola, Stripe or any other system; the only write it may ever make is a
Gmail **draft**, after approval (Step 8).

1. Granola readable. If it is not, continue with `sources.meetings.status: unavailable` (or `denied`); the draft then
   carries an **Incomplete** banner. Offer pasted notes or another recorder (Gong, Fathom, Fireflies, Otter, Zoom)
   as a fallback.
2. Billing readable (if `billing: stripe`). If it is not, set `sources.billing` to `unavailable` and ask the founder
   for the figures with their source.
3. The previous update found. If there is none (first update), say so; the said-vs-happened section will say there
   is nothing to compare.

## Step 2: Pick the period and audience

- "This month's update" on or after the 1st means the month that just ended; "board update for Q3" means `2026-Q3`.
  Confirm the period if it is ambiguous. A period that has not ended yet gets a **Period not over** banner.
- `--audience investors` (default) leaves confidential items out. `--audience board` prints them in a separate
  **Board only — do not forward** section. Use the board audience only when the founder asks for the board version.

## Step 3: Read the period's meetings (Granola)

1. `list_meetings` with `time_range: custom`, `custom_start` and `custom_end` set to the period's first and last day
   (`last_30_days` counts back from today, so it does not cover a calendar month), plus `folder_id` if
   `granola_folder` is set. Note the `requested_range` it reports. Granola returns dates like
   `Oct 9, 2026 1:00 PM PDT`; write each meeting's local date as `YYYY-MM-DD`.
2. Classify each meeting's `kind`: `customer`, `team` (all-hands, standups, retros), `hiring`, `partner`,
   `investor`, `board` or `other`, using the title and attendee domains against `internal_domains`.
3. `get_meetings` in batches of at most 10 ids for the meetings that matter for an investor update (customer, team,
   hiring, partner, board). Set `notes_read: true` only for meetings you actually read. Put the notes text you rely on
   in `notes_text` so the script can check quotes and founder statements against it; it is never printed.
4. `get_meeting_transcript` only for an exact quote or a missing detail. If access is denied, set `transcript:
   denied`, keep going with the notes, never retry through another tool, and never conclude something was not
   discussed. `query_granola_meetings` is fine for open questions ("what did we promise customers this month?"), but
   keep its citation links and confirm against `get_meetings` before citing.
5. Granola's free plan may not return notes older than 30 days; the draft says so for older periods. Report a low
   meeting count rather than hiding it.

## Step 4: Collect the metrics

Every metric figure in the update comes from a metrics row with a `source` and an `as_of` date (`DATA_CONTRACT.md`).
The script withholds any row without one (or with a placeholder such as "unknown" or "TBD"), and any row whose source
is a meeting, call or notes ("Granola", "all-hands notes"): nothing a meeting said becomes a metric. Put the system of
record in `source` ("Stripe", "Finance sheet"), never the meeting where a number came up.

- **Stripe** (`billing: stripe`), read-only: MRR, ARR, paying customers, new and churned customers, net new MRR for
  this period and the previous one. Prefer figures Stripe itself reports (the Billing dashboard's MRR, a Sigma or
  revenue report the founder shares). If you compute a figure from the API (for example MRR as the sum of active
  subscriptions normalised to a month), say so in `source` ("Stripe API, computed by the agent from active
  subscriptions") and ask the founder to confirm it before sending. Never estimate a figure Stripe did not give you.
- **Metrics file** (optional): cash, net burn, runway, headcount and anything else, as CSV rows
  `metric,period,value,source,as_of` passed with `--metrics-csv`, or as rows in the bundle. Plain numbers only.
- **The founder**: a figure the founder types in chat is a valid source: `"source": "Founder (typed in chat)"`.
- **DTC businesses**: define revenue, orders, AOV or repeat rate in `metric_definitions` and supply them the same way.
- One currency per update (`company.currency`); convert before supplying, never in the script.
- A missing figure stays missing. Do not fill it with zero, an estimate or last period's number.

## Step 5: The previous update

Find the previous update the founder sent (Gmail search by subject, or the doc they point to) and fill
`previous_update`: its `period`, `sent_on`, every commitment it made, quoted as written (use `{org:...}` /
`{person:...}` placeholders where it names someone who is not public), with a `metric_target` when the commitment was
a number ("Reach $40,000 MRR" is `{"metric": "mrr", "op": ">=", "value": 40000}`), and the figures it reported in
`reported_metrics`. Then add one `commitment_reviews` entry per commitment with a true status (`done`, `partial`,
`missed`, `changed`, `blocked`, `dropped`) and its evidence. Never mark something done without evidence; when you
don't know, leave the commitment without a review and it shows as "Not reviewed".

## Step 6: Extract the items

Read `update-writing-guide.md` first. Then write `items`, each with a section, one sentence of text and at least one
citation (a meeting, the founder, a metric, or a document):

- **Names**: put every customer, partner, investor or person you might mention in `entities`, with `public_ok: true`
  only for names in `public_names`, and a descriptor for the rest. Write `{org:key}` / `{person:key}` in the text,
  never the name itself.
- **Figures**: only through `{metric:key}`, `{metric:key.previous}` or `{metric:key.change}`.
- **Confidential**: tag fundraising terms, legal matters, personnel matters about identifiable people, and anything
  said to be board-only. A meeting that was itself confidential (a board meeting, a call with lawyers) can be tagged
  once on the meeting.
- **Forward-looking**: only the founder's own sentence. The item's text must **be** a sentence the founder said in
  a meeting, word for word, and `founder_stated` gives that sentence as `excerpt`, the meeting as `meeting_id`, and
  the founder's speaker label in that meeting's text as `speaker` (`Maren`, or Granola's `Me`). The script accepts it
  only if the excerpt is one or more whole sentences from that speaker's turn in the notes or transcript text in the
  bundle, none of them is a question or a conditional ("Will we be profitable next year?", "If we close the round, we
  will be profitable."), the rest of the founder's words around it read as plain statements (see the context check
  at the end of this document: anything it doesn't recognise withholds the item), and the item is exactly one of
  those sentences (case and formatting ignored). There is no
  "close enough": a paraphrase is withheld even when it keeps most of the words ("March" for "November", "close" for
  "open"). If you want different wording, drop the forward-looking part, or keep the founder's sentence unchanged.
  Something the founder said only in chat can't be checked, so it doesn't unlock forward-looking wording; the founder
  can add it when they edit the draft. Hopes voiced by the team are not promises to investors. A plan written in the
  present tense is forward-looking too: an item (or a quote, or a commitment-review note) that names a date after the
  period ("The bridge round wraps up in March.", "We are hiring a head of sales in December.") or a schedule
  (scheduled for, slated, due in ...) and has no past-tense verb is withheld without `founder_stated`.
- **Quotes**: only with `permission: true`, which means the founder confirmed the speaker agreed to be quoted in
  investor updates; one or more whole sentences from one speaker's turn in the notes or transcript you read, copied
  as written, without the speaker label, an ellipsis or a cut-off start or end.
- **Lowlights**: at least one, unless the founder tells you there was genuinely nothing.

Build the bundle as specified in `DATA_CONTRACT.md`, in a working folder. Do not keep meeting text or transcripts
after the run.

## Step 7: Build the draft

```bash
python3 build_update.py \
  --bundle bundle.json \
  --metrics-csv metrics.csv \
  --as-of 2026-10-02T09:00:00-04:00 \
  --audience investors
```

- `--as-of` is required and is the only "now", as an ISO datetime with an offset. Use the run time.
- `--format draft` (default) is for review; `--format email` prints the body for the email after approval;
  `--format json` returns both plus the checks.
- Exit 0 prints the draft. Exit 2 means bad input: the message names the file and JSON path or CSV line; fix the
  bundle and rerun, never edit the script. Exit 3 means a non-public name would have been printed from text the
  script cannot withhold (such as a metric label): rename it or ask the founder whether the entity may be named. Exit 6 means the output could not be written.
- Every withheld item is listed under "Before you send" with its reasons. Fix what you can in the bundle (use a
  placeholder, tag it, remove an unsourced figure, add the founder statement) and rerun. Never copy a withheld item
  into the draft by hand.

## Step 8: Review with the founder, then (optionally) a Gmail draft

1. Show the founder the draft (`--format draft`), including **Before you send**. Ask them to confirm, in particular:
   the metrics marked withheld or restated, the lowlights, every name that is printed, and the said-vs-happened
   statuses. Make their edits in the bundle and rerun; do not hand-edit figures.
2. Only when the founder approves the text, and only if `gmail_draft: ask` and they say yes, create a **Gmail draft**
   (never send) with the subject and body from `--format email`, addressed to `recipients` they confirm. Tell them
   the draft is in their Drafts folder and that they send it. If Gmail is not connected or read-only, give them the
   text to paste.
3. Never post the update to Slack, a doc shared outside the company, or any other destination unless the founder
   asks for that specific destination.

## Output format

The draft has these sections, in this order: the title and a "Draft — not sent" line, banners (Incomplete, Period
not over) when they apply, **TL;DR**, **Key metrics** (Metric | this period | previous | Change | Source, as of),
**Highlights**, **Lowlights** (always present; "None recorded" with a prompt when empty), **Product**, **Customers &
go-to-market**, **Team & hiring**, **Asks**, **What we said last time vs what happened** (We said | Status | What
happened), **Board only — do not forward** (board audience only), and **Before you send** (checks, coverage, sources
cited). The email body has the same sections without the references, banners and Before you send, starts with a
`Subject:` line and ends with "Confidential — please do not forward."

## Guardrails

- Never send anything. The only write is a Gmail draft, after the founder approves the exact text.
- Never present a metric figure that did not come from metrics data with a source and an as-of date. Never present
  a figure from a meeting as a metric. (Last update's commitments are quoted as written, figures included.)
- Never name a customer, partner or person the founder has not cleared; use the descriptor.
- Never put fundraising terms, legal matters, personnel matters about identifiable people, or board-only items in an
  investor update. They belong in the board version's board-only section, or nowhere.
- Never quote anyone without permission, and never paraphrase inside quotation marks.
- Never write a forward-looking promise the founder did not state.
- Never drop the Lowlights section or soften a status in "What we said last time vs what happened".
- Never hide a coverage gap: a denied transcript, an unreadable source or a short meeting list is reported, not
  smoothed over.
- Read-only everywhere else: no writes to Granola, Stripe, a CRM or a metrics sheet.

## On request only

This skill runs when someone asks. It keeps no state and has no scheduled mode: an investor update needs the founder
in the loop every time. A reminder automation ("on the 2nd of each month, ask me whether to draft the update") is
fine; it should ask, not draft and send.

## Limits

See the founder-evidence grammar and context limits at the end of this document.

- Extraction is done by the agent. The script checks and renders what was extracted; a highlight nobody extracted is
  a silent miss, and a wrong classification of a meeting or an item is not caught.
- Name matching, the confidential-term scan, the figure check, the meeting-source check on metric rows and the
  forward-looking checks use patterns and a bounded positive grammar. Assertions without a founder statement
  need a recognised past or current-status predicate, so date-free plans are held too. These checks
  catch the listed names and aliases and the listed phrases with case, accent, spacing and some look-alike variations,
  and can miss paraphrases, misspellings, nicknames that are not aliases, numbers written as words outside the listed
  patterns ("forty-two grand"), a year-like number before a noun that is not a metric noun ("2025 new docks"), and
  indirect identification ("our largest customer in Ohio"). They also withhold some harmless text (a
  person called Will, "target customers"); rephrase it.
- Email addresses and phone numbers are redacted before any text is printed. Redaction is pattern-based and can miss
  formats. Phone numbers are removed only when there's a clear signal (a + country code, a word like "call" or
  "mobile", or a standard US/UK/French format), so number lists, prices, ranges, dates and reference numbers are left
  alone.
- The script does not verify that a figure is correct, only that it has a source and an as-of date and agrees with
  other rows for the same period. Stripe-derived figures computed by the agent need the founder's check.
- Single currency; no conversion. Quarterly flow metrics need quarter figures.
- The script checks every quote against the meeting text it was given. A quote must be one complete sentence, or
  several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting.
  Partial sentences, ellipses, speaker labels and quotes that run across speakers are rejected. When the meeting
  text is not in the bundle, a quote is printed with a "not checked" note. The check is mechanical: it confirms the
  words appear as whole sentences. It doesn't judge whether a quote is representative, in context, or correctly
  attributed: a whole sentence can still be cherry-picked (an answer that depends on the question, sarcasm, a later
  retraction).
- Speaker turns are found with heuristics (Granola's labels and `Name:` patterns), so a founder statement's
  `speaker` is matched against those labels; a label the heuristics miss means the statement is withheld.
- Founder statements must be the founder's sentence word for word, so a forward-looking item can't be reworded. The
  question-and-conditional filter on the claim itself is keyword-based ("if", "unless", "will we", a final "?"
  ...). The check of the founder's surrounding words fails closed and is described, with its measured limits, at
  the end of this document; it withholds many harmless claims, which the founder can add by hand.
- The quote check and the contact redaction come from `granola_text.py`, a shared helper shipped unchanged with this
  skill.

## Verification status

**Tested offline:** `bash run.sh` matches `expected_output.txt` byte for byte and `bash tests.sh` passes on Python
3.8 and 3.14 (the verification is recorded in the release receipts), using fictional sample data only. The suite covers the metric rules (rounding half-up,
changes from rounded figures, withheld rows for a missing or placeholder source, a meeting or call as the source,
a source label with a figure or confidential wording, as-of date, currency or whole-number problems, conflicts and
merges, restatements, fallback to the previous update's figures, 10x jumps, quarterly stock and flow
metrics, negative figures), name handling (placeholders, descriptors, direct names in text, quotes, attributions,
commitments, notes, titles, URLs, source and document labels, and the exit-3 refusal for fixed text), the
confidential tags and term scan (including departures and other personnel wording, M&A and legal phrases, and
confidential titles and labels masked in an investor draft), metric labels and the company name checked as fixed
text, the figure check (including numbers glued to metric nouns such as `40kMRR`) and the forward-looking check, quote permission and the whole-sentence quote check (partial
sentences, a dropped leading "None of" or "Nobody", ellipses, speaker labels, quotes across turns, markdown in the
notes), founder statements (paraphrases such as "March" for "November" and "close" for "open", "profitable" for
"unprofitable", questions, conditionals, a statement from someone else's turn, a missing speaker, a chat-only
statement, unrelated, "ok", reversed and negated excerpts), the shared helper's SHA-256, running from another
directory and with `python -I`,
commitment status checks against metric targets (including "Not verified" when the figure is missing), config
validation of `metric_order` and `headline_metrics`, the required Lowlights section, validation errors (exit 2 with
nothing printed), closed stdout and broken pipes, and an ASCII-only locale. Fuzz tests put 22 injection strings (including `$…$`,
`==…==` and web addresses) into every text field at once in every format and 5 of them into each text field alone,
102 reversed, cross-turn, labelled and cross-line quotes and founder excerpts into notes and transcript text, 7 spellings of non-public names
into every string field, and 12 bad values into every field of the sample; they found no working injected link, autolink, math, highlight or
markup, no printed reversed quote, no printed non-public name, and no traceback. A further 97 attacks written only from this
file, `DATA_CONTRACT.md` and the listing (unsourced and meeting-sourced metrics, non-public names, confidential
items, reversed, negated, conditional and paraphrased founder statements, partial quotes, injection) all held.

**Run against live Granola (read-only, 2026-10-12, with the version before the round-4 and round-5 changes to quote
matching, founder-statement support, redaction and escaping; not re-run since, so those changes, including the
shared `granola_text.py` helper and the word-for-word founder-statement rule, are tested offline only):** `get_account_info`, then
`list_meetings` for the last 30 days (63 meetings listed), `get_meetings` on 10 of them in one batch, and
`get_meeting_transcript` on one (access allowed). A bundle built from those shapes in a temporary folder (all 63
meetings, an October period that had not ended, made-up metrics including a negative figure, a zero previous figure,
a row without a source and a row whose source was a meeting, and three entities marked non-public) ran in every
format and audience on Python 3.8 and 3.14 with identical output and no errors. A verbatim transcript quote with
permission was printed with the descriptor, not the name; an item naming a non-public entity directly, a sentence
with a figure from the transcript, an item citing a meeting whose notes were not read, a personnel item (investor
version) and a forward-looking customer quote were held back; the meeting-sourced and unsourced rows were withheld;
and no non-public name appeared in any output. The data was deleted after the run.

**Unverified against live systems:** Stripe reads, the metrics file and previous-update lookups in Gmail or docs,
creating a Gmail draft, and the agent's extraction. These follow each provider's documented read endpoints but have
not been run against real accounts. Check the first few drafts against the meetings and the billing system by hand.

Founder evidence uses a conservative transcript grammar: the founder's label must start at column zero, outside a blockquote or list item, at the beginning of the source or after terminal punctuation. Leading timestamps and the forms `Name:`, `[Name]:`, `Name (CEO):`, `Name [CEO]:`, `Name - ` and `Name — ` are supported. A role annotation must be a supported job title or department: executive/founder titles and engineering, product, operations, sales, finance or marketing (optionally with head, VP, director or lead). Unknown roles, including stance annotations such as `sarcastic`, withhold the item. This can also withhold legitimate but unlisted job titles. Mid-line names cannot establish founder attribution. Names with conflicting roles, read-outs, duplicate words under another speaker, and an excerpt missing from an included transcript are checked separately. These are mechanical checks on caller-supplied labels, not identity verification.

A founder claim needs a bounded noun-phrase subject followed by a finite verb and a complete supported predicate. Novel product and project nouns are accepted in noun positions; they do not supply an unknown predicate. The parser cannot skip an unknown verb and treat a later word as the verb: `We put the launch on hold` does not become a statement whose verb is `hold`. Unknown predicates, punctuation introducing an aside, and unrecognised claim qualifiers withhold that item. Unlabelled source prefaces and all preceding turns are checked as context. The rest of the founder's turn and later founder turns must match complete supported past/current-status clauses, a greeting/agenda phrase, or an explicit reinforcement. A preceding question from another speaker can introduce the claim when it matches a supported how/what-is/are topic question, or how-did plus a listed business topic, time period or operational process and the status verb go, perform or progress. Those past-performance question topics and their modifiers are bounded; unfamiliar project names or adjectives in that question form are held. Framing and other qualification markers still apply to those questions. An earlier turn may contain a complete, explicit will-action commitment with no shared content words with the current claim; other preceding context must match the supported work/status or greeting forms. A new speaker label or intervening acknowledgement does not erase a framing cue. Agenda fragments accept the listed business topics, not arbitrary topic nouns; detected readout or performance framing takes precedence over a greeting or agenda shape. A second future schedule is held even when it is harmless, such as `The audit starts Monday.` Later founder wording that refers strongly to the claim must be an explicit reinforcement; an unfamiliar retraction is not accepted simply because it avoids a list of negative words. Another speaker's replies after the claim and later references still use narrower marker and date checks. Unlisted preceding questions and unrelated future plans may be held.

Assertions without `founder_stated` need a recognised past or current-status predicate. An `ask` label does not bypass this gate: an unstated ask must have a supported request noun or imperative (for example, introductions, referrals, or “Please introduce us to…”), with any following explanation limited to supported factual statements. Present-tense schedules and unrecognised assertions are held for review, including date-free plans such as `We open the beta to every warehouse.` A held item is omitted from every draft format and has a review reason; no source excerpt or rejected claim is copied into that reason. Some valid fragments and ordinary statements are therefore held too. Keep the source wording and have the founder add a reviewed item manually; do not rewrite evidence to make the parser accept it.

These checks use a bounded English grammar and phrase lists. They do not understand all implications, prove a claim is true, identify the real founder, or resolve every disagreement between notes and transcript. A qualifying statement can still pass if it fits the accepted grammar or occurs outside the context the checks associate with the claim. Unlisted wording can cause false holds. Prior round-9/10 results describe earlier code and are not clearance for this version. The bundled cases are regression tests, including previously disclosed QA strings; they are no longer unseen evaluation. Review the complete meeting and get founder approval before sending any draft.
