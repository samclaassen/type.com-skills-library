# Data contract: `build_update.py`

The agent reads Granola, the billing system, metrics files and the previous update, and writes **one JSON bundle**
(plus, optionally, metrics CSV files). The script validates it, computes the metric changes, applies the name,
confidentiality, figure, forward-looking and quote rules, and prints a draft. It reads nothing else and sends nothing.

```
python3 build_update.py --bundle bundle.json [--metrics-csv metrics.csv ...] --as-of 2026-10-02T09:00:00-04:00 \
  [--audience investors|board] [--format draft|email|json] [--config update.json]
```

| Flag | Meaning |
| --- | --- |
| `--bundle` | The JSON bundle below. Required. |
| `--metrics-csv` | A metrics CSV (format below). Repeatable; rows are added to the bundle's `metrics`. |
| `--as-of` | The run time, an ISO datetime with a UTC offset. The only "now": the clock is never read. Required. |
| `--audience` | `investors` (default): confidential items are left out and only counted. `board`: they are printed in a separate "Board only — do not forward" section. |
| `--format` | `draft` (default): markdown with references (M1, M2 ...) and a "Before you send" section. `email`: the body only, with a `Subject:` line, no references, no review notes. `json`: structured result with both. |
| `--config` | Defaults to `update.json` next to the script. |

## Exit codes

| Code | Meaning | stdout |
| --- | --- | --- |
| 0 | Draft printed. Withheld items, metrics and quotes are listed under "Before you send" (draft format) and in the JSON. | the draft |
| 2 | Bad input. The message names the file and the JSON path (or the CSV line). Fix the bundle; never edit the script. | empty |
| 3 | Refused: a non-public name or alias would have appeared in the output, from text the script cannot withhold (such as a metric label). | empty |
| 6 | The output could not be written (closed pipe, full disk). Nothing was sent anywhere. | — |

Error messages name the field and its JSON path. They show enum, id and date values and unknown key names
(shortened) but not the content of free-text fields.

## Strictness

- Every object in the bundle rejects unknown keys (exit 2), so a typo such as `confidental` can never silently make an item public.
- Dates are `YYYY-MM-DD` in ASCII digits, years 1900 to 2999 (a full ISO datetime with `Z` or an offset up to
  +/-14:00 is accepted and taken as its local date). Periods are `YYYY-MM` (a month) or `YYYY-Qn` (a quarter).
  `NaN`, `Infinity`, unpaired surrogates (`\ud800`), non-UTF-8 files and nesting too deep for the parser exit 2.
- Numbers are JSON numbers, finite, below 1e15 in size.
- Free text is trimmed and whitespace-collapsed; invisible control, zero-width and direction characters are removed.
  Item text is limited to 1500 characters, labels to 60 to 300.

## Bundle

```json
{
  "schema_version": 1,
  "company": {"name": "Wrenhallow", "currency": "USD"},
  "period": "2026-09",
  "sources": { "meetings": {...}, "billing": {...}, "metrics_file": {...}, "previous_update": {...} },
  "meetings": [ ... ],
  "entities": [ ... ],
  "metric_definitions": { ... },
  "metrics": [ ... ],
  "items": [ ... ],
  "previous_update": { ... },
  "commitment_reviews": [ ... ]
}
```

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | `1`. |
| `company` | object | yes | `name` (printed in the title and subject; a name matching `confidential_terms` exits 2) and `currency` (3-letter code, e.g. `USD`). One currency per update; the script never converts. |
| `period` | string | yes | `2026-09` or `2026-Q3`. The previous period (`2026-08`, `2026-Q2`) is the comparison. |
| `sources` | object | yes | Source-status objects (below); `meetings` is required. |
| `meetings` | list | yes | Every meeting listed for the period (use `[]` when there are none). |
| `entities` | list | yes | Every customer, partner, investor or person the items may mention (`[]` if none). |
| `metric_definitions` | object | no | Extra metrics, or overrides of the defaults in `update.json`. |
| `metrics` | list | yes | Metric rows (`[]` when all rows come from CSV files). |
| `items` | list | yes | What the agent extracted. |
| `previous_update` | object or null | no | The last update as sent. |
| `commitment_reviews` | list | no | The agent's status for each commitment in `previous_update`. |

### Source-status objects (`sources.*`)

`{"provider": "Granola", "status": "ok", "detail": "list_meetings for Sep 1-30"}`. Keys: `provider` (text, required),
`status` (`ok`, `unavailable`, `denied` or `not_configured`, required), `detail` (text, optional). Sources:
`meetings` (Granola or another recorder), `billing` (Stripe or another billing system), `metrics_file` (CSV or
sheet), `previous_update` (Gmail, a doc, pasted). A `meetings`, `billing`, `metrics_file` or `previous_update` source
that is `unavailable` or `denied` puts an **Incomplete** banner at the top of the draft. All sources are listed under
Coverage.

### `meetings[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `meeting_id` | string | yes | Granola meeting UUID or `not_...` note id (letters, digits, `_`, `-`; up to 64). Unique. |
| `title` | string | yes | As listed. Shown only in the review section's "Sources cited", and masked when it contains a non-public name or, in an investor draft, matches `confidential_terms` (the same masking applies to document labels and source providers and details). |
| `date` | string | yes | `YYYY-MM-DD`, the meeting's local date. Granola's `list_meetings` prints dates like `Oct 9, 2026 1:00 PM PDT`; convert them. |
| `url` | string | no | The note URL (`https://notes.granola.ai/d/<id>`). Kept only if http(s) with a real host; dropped (and counted) otherwise, and dropped when it contains a non-public name. |
| `kind` | string | yes | `customer`, `team`, `hiring`, `partner`, `investor`, `board` or `other`. Your classification. |
| `notes_read` | bool | yes | Whether you read its notes with `get_meetings`. Items, quotes and founder statements citing a meeting with `notes_read: false` are withheld. |
| `transcript` | string | yes | `ok`, `denied`, `unavailable` or `not_requested`. Counted under Coverage. |
| `confidential` | string | no | A tag (below). Every item citing this meeting inherits it. A `board` meeting is `board_only` unless tagged otherwise. |
| `notes_text` | string | no | The notes or AI summary text you read (up to 400,000 characters). Used only to check quotes and founder statements against the meeting text; never printed. Do not store it after the run. |
| `transcript_text` | string | no | The transcript excerpt you read, same use. |

### `entities[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `key` | string | yes | Lower-case letters, digits, `_`, `-`. Used in placeholders; never printed. |
| `name` | string | yes | The real name. Printed only when `public_ok` is `true`. |
| `kind` | string | yes | `customer`, `partner`, `investor`, `person` or `other`. `{person:...}` placeholders need `person`; `{org:...}` the others. |
| `public_ok` | bool | yes | `true` only when the founder confirmed this name may appear in this update. |
| `descriptor` | string | if not public | What is printed instead of the name, e.g. `a mid-market logistics customer`. Must not contain any non-public name or alias, a figure, a confidential term or forward-looking wording (exit 2). |
| `aliases` | list of strings | yes | Short forms, nicknames, product names, domains (`[]` if none). |

Name matching (pattern-based) uses the full name, the name without a legal suffix (`legal_suffixes`), each alias,
the first word of an organisation's name, and each word of a person's name, except words shorter than 4 (3 for a
person), all-digit words and words in `generic_name_words`. It folds case, accents, full-width and a few Cyrillic and Greek
look-alike letters, ignores zero-width characters and punctuation, and for names of 8 or more letters also matches
them with spacing removed (`Q u o r r e l i n d`). It can miss misspellings, nicknames not listed as aliases, letters
swapped for digits, and indirect descriptions ("our largest customer in Ohio"). The company name, a descriptor, or a
public entity's name that contains a non-public name exits 2.

### `metric_definitions` and `update.json` metrics

`{"orders": {"label": "Orders", "unit": "count", "kind": "flow", "better": "up"}}`. Keys: `label`, `unit`
(`currency`, `count`, `percent`, `months`, `number`), `kind` (`stock`: a level at the end of the period, such as MRR,
customers, cash, runway, headcount; `flow`: a total over the period, such as net new MRR, new customers, burn) and
`better` (`up`, `down`, `neutral`, used only to list metrics that moved the wrong way). The defaults in `update.json`:
`mrr`, `arr`, `net_new_mrr`, `revenue`, `customers`, `new_customers`, `churned_customers`, `gross_mrr_churn`, `cash`,
`net_burn`, `runway_months`, `headcount`.

A label is fixed text printed in every version, so a label (from the bundle or `update.json`) that has a figure,
matches `confidential_terms` or has forward-looking wording exits 2 with its JSON path.

### `metrics[]` (and CSV rows)

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `metric` | string | yes | A defined metric key. Unknown keys exit 2. |
| `period` | string | yes | `YYYY-MM` or `YYYY-Qn`. Rows for periods other than this one and the previous one are ignored and counted. For a quarterly update, a **stock** metric's row for the quarter's last month counts for the quarter; a flow metric needs a quarter row. |
| `value` | number or null | yes | `null` is missing (never zero). Percent values are percentage numbers (`1.9` means 1.9%). Months are months (`12.7`). |
| `source` | string | no* | Where the figure comes from, e.g. `Stripe`, `Finance sheet`, `Founder (typed in chat)`. |
| `as_of` | string | no* | The date the figure was read or is valid for, `YYYY-MM-DD`. |
| `currency` | string | no | For currency metrics; must equal `company.currency`. |

\* A row without a `source` or `as_of`, with a placeholder source (`unknown`, `TBD`, `n/a`, `-`, `?` and the like),
with a source that names a meeting, call or notes (a phrase in `meeting_source_terms`, such as "Granola", "call",
"notes" or "all-hands", or a meeting title from the bundle: a figure said in a meeting is not a metric), with a
source label that has a figure or matches `confidential_terms`, with a blank value, with an as-of date after `--as-of` or before its period
began, in another currency, or a `count` that is not a whole number is **withheld**: its figure is not printed
anywhere, and "Before you send" says why (with the CSV line or JSON path; a CSV file name that contains a non-public
name is not printed). Two rows for the same metric and period
that round to different figures are a conflict: neither is shown, and both are listed with their sources. Rows that
round to the same figure are merged and their sources joined.

When there is no row for the previous period, the previous update's `reported_metrics` figure is used, with the
source `previous update` and its `sent_on` date. When both exist and differ after rounding, the draft keeps the
current source's figure and flags the restatement.

### Metrics CSV (`--metrics-csv`)

UTF-8 (a BOM is fine), header `metric,period,value,source,as_of` with an optional `currency` column, in any order.
Values are plain numbers (`1840000`, `12.7`, `-500`): no currency signs, thousands separators or `%` (exit 2 with the
line number). Blank lines are skipped. A blank `source` or `as_of` cell withholds that row, as above.

### Rounding and changes

Currency rounds to `currency_decimals` (0 by default) and prints with the symbol from `currency_symbols` (or the
code, `CHF 41,250`); counts are whole numbers; percent and months one decimal; `number` uses `number_decimals`.
Rounding is half-up. The change is computed from the **rounded** figures, so the table adds up. Currency, count and
number changes show the absolute change and, when the previous figure is above zero and the current one is not
negative, the percentage change to one decimal; percent metrics show percentage points (`pp`); months show months.
A change of `jump_factor` (10) times or more is flagged for a second look.

### `items[]`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `section` | string | yes | `highlight`, `lowlight`, `product`, `customers`, `team` or `ask`. |
| `text` | string | yes | Your sentence, with placeholders (below). |
| `citations` | list | yes | At least one: `{"meeting_id": "..."}`, `{"source": "user", "detail": "..."}` (the founder said it in chat), `{"source": "metric", "metric": "mrr"}`, or `{"source": "document", "label": "...", "url": "..."}`. Citation keys: `meeting_id`, `source`, `detail`, `metric`, `label`, `url`. |
| `confidential` | string or null | no | `board_only`, `legal`, `fundraising_terms` or `personnel`. |
| `quote` | object | no | `text` (one or more whole sentences from one speaker's turn, copied as written), `attribution` (a role, may use placeholders), `permission` (bool, required), `meeting_id` (required). |
| `founder_stated` | object | no | For forward-looking wording: `excerpt` (the founder's own sentence or sentences, copied from the meeting text), `meeting_id`, and `speaker` (the founder's speaker label in that meeting's text, such as `Maren` or Granola's `Me`). `source: "user"` (said in chat) is accepted by the schema but never unlocks an item, because the script can't check it. |
| `cleared_by_founder` | bool | no | `true` only when the founder approved this exact item after the confidential-term check flagged it. Skips that check only. |

Placeholders in `text`, quote `attribution` and review `note`:

- `{org:key}` and `{person:key}`: the entity's name if `public_ok`, else its descriptor (capitalised at the start of
  the text). An unknown key, or a person key in `{org:...}`, withholds the text without echoing the key.
- `{metric:key}`, `{metric:key.previous}`, `{metric:key.change}`: this period's figure, the previous figure, or the
  change, formatted as in the table. A placeholder for a figure that is withheld or not supplied withholds the text.

What withholds an item (it is not printed; "Before you send" lists it by section, number and `items[i]` with the
reasons, never its text):

1. It cites a meeting whose notes were not read.
2. Its text writes a non-public name or alias directly (use the placeholder).
3. It matches a phrase in `confidential_terms` and is not tagged or cleared (pattern-based).
4. It has a figure that did not come from a `{metric:...}` placeholder: any digits except years, `YYYY-MM-DD` dates,
   month-day dates (`Oct 3`), quarters and other letter-number words (`Q3`, `3PL`, `B2B`), version numbers (`v2.1`)
   and standards (`SOC 2`, `ISO 27001`); a number or number word (`number_words`) right before a metric noun
   (`metric_nouns`); a number glued to a metric noun or a multiplier (`40kMRR`, `100customers`, `ARR2M`, `x10`);
   or a word in `scale_words` such as "million" or "doubled" (pattern-based: amounts written as words outside these
   patterns, such as "forty-two grand", and a year-like number before a noun that is not a metric noun, such as
   "2025 new docks", get through).
5. It has wording from `forward_terms` (will, plan to, on track, next quarter ...), or reads as a present-tense or
   scheduled plan, and has no valid `founder_stated`. A present-tense plan is a clause that names a date after the
   update period (a month one to nine months after the period's last month or with a later year, a later quarter,
   half or year, a season, Christmas, the new year, next ..., in two weeks, end of the year ...) and has no finite
   past-tense verb ("The bridge round wraps up in March.", "We are hiring a head of sales in December."), or a
   schedule word (scheduled, slated or pencilled in for, planned, set or booked for, due in or is due, upcoming).
   Clauses are split at ; : dashes and at and, but, while and then. This applies to items, quotes and
   commitment-review notes. Separately, an assertion without `founder_stated` must have a recognised past or
   current-status predicate. This positive assertion gate withholds date-free plans such as "We open the beta to every warehouse."
6. It has a `founder_stated` that is not valid, or whose sentences it does not repeat exactly. A founder statement is
   valid only when all of these hold:
   - it cites a meeting whose notes were read, and that meeting's `notes_text` or `transcript_text` is in the bundle
     (a statement typed in chat, `source: "user"`, can't be checked and is never valid);
   - the excerpt passes the quote check below (whole sentences from one speaker's turn) and comes from a turn whose
     speaker label is `speaker` (case ignored; a trailing `(Role)` is ignored when `speaker` has none, and must match
     when it has one);
   - none of its sentences is a question or a conditional (a final `?`, openings such as "will we" or "could", and
     words such as "if", "unless", "assuming", "as long as"; a keyword heuristic).

   - every matching occurrence passes the founder context and attribution rules in "Founder evidence grammar and
     context limits" below: the claim sentence itself and every other sentence of the founder's turn (inside or
     outside the excerpt) and of the founder's later turns must match the supported grammar, greetings, agenda phrases or reinforcers;
     detected retractions or qualifications withhold the item, as does an unrecognised founder predicate; a clean
     occurrence does not override a qualified occurrence.

   The item's text, with placeholders filled in, must then **be** one of the excerpt's sentences: the same words
   after the quote check's normalisation (case, formatting, curly quotes and a final `.` or `!` ignored). A
   paraphrase is not accepted, however close: "March" for "November", "close" for "open", "profitable" for
   "unprofitable", "will renew next quarter" cut from "Nobody will renew next quarter", and "we will be profitable"
   cut from "If we close the round, we will be profitable" are all withheld. To say it differently, drop the
   forward-looking wording, or keep the founder's sentence as it is. The check is mechanical: it confirms the
   founder said these words as whole sentences; it doesn't judge whether the sentence is in context or still
   current.

Tagged items (or items that cite a tagged meeting, or whose quote or founder statement comes from one) skip check 3 and go to the board-only section of a board draft; an
investor draft leaves them out and prints only a count per tag. All other checks still apply to them.

A quote is printed under its item only when `permission` is `true`, its meeting's notes were read, it passes the
quote check, and the quote and attribution pass checks 2 to 5 (check 3 is skipped for tagged or cleared items; no
founder statement can unlock a customer's forward-looking words).

The quote check is `check_quote` from `granola_text.py` (the shared Granola text helper, shipped unchanged next to the
script). The script checks every quote against the meeting text it was given. A quote must be one complete sentence, or several consecutive complete sentences, from a single speaker's turn. The check ignores case and formatting. Partial sentences, ellipses, speaker labels and quotes that run across speakers are rejected. A final `?` must match. It runs against `notes_text` and against `transcript_text`
separately; when neither is in the bundle, a quote is printed with a "not checked" note (ellipses, speaker labels and
quotes under three words are still rejected), and a founder statement is not valid. Speaker turns are found with
heuristics (Granola's labels such as `Me`, `Them`, `Speaker 2`, and `Name:` patterns). The check is mechanical: it
confirms the words appear as whole sentences. It doesn't judge whether a quote is representative, in context, or
correctly attributed.

Email addresses and phone numbers are redacted before any text is printed, with `granola_text.redact_contacts`:
they become `[email removed]` / `[phone removed]` in every printed text, quotes included. Redaction is pattern-based
and can miss formats. Phone numbers are removed only when there's a clear signal (a + country code, a word like
"call" or "mobile", or a standard US/UK/French format), so number lists, prices, ranges, dates and reference numbers
are left alone.

### `previous_update`

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `period` | string | yes | The period it covered. A period other than the previous one is flagged. |
| `sent_on` | string | yes | `YYYY-MM-DD`, not after `--as-of`. |
| `url` | string | no | Link to the sent update (shown in Sources cited). |
| `commitments` | list | yes | What it promised: `id` (unique), `text` (as written; use placeholders for non-public names), optional `metric_target` `{"metric", "op", "value"}` with `op` one of `>=`, `<=`, `>`, `<`, `=`, and optional `cleared_by_founder`. |
| `reported_metrics` | list | no | Figures it reported: `{"metric", "period", "value"}`. Used for restatement checks, as the previous figure when no row exists, and to flag metrics that were reported last time but are missing now. |

Commitment text goes through checks 2 and 3 only (it may contain figures and promises: it is quoting what was said).
If it fails, the row says "(withheld: see Before you send)".

### `commitment_reviews[]`

`{"commitment_id": "c1", "status": "done", "note": "...", "citations": [...]}`. `commitment_id` is an `id` from
`previous_update.commitments` (each reviewed once); `status` is `done`, `partial`,
`missed`, `changed`, `blocked` or `dropped`; `citations` needs at least one entry; `note` is checked like item text.
A commitment with no review is shown as "Not reviewed" and flagged. A commitment with a `metric_target` is compared
with this period's (rounded) figure: a review of `done` that the figure contradicts becomes `Missed`, and a review of
`missed` or `partial` that the figure meets becomes `Done`; each change is explained under "Before you send".
A commitment with a target and no review takes its status from the figure. A review of `done` whose target figure
is missing or withheld this period is shown as "Not verified" (and explained under "Before you send"). In an investor draft, a review that
cites a meeting tagged confidential keeps its note and references out (and says so under "Before you send"). `changed`, `blocked` and `dropped`
are kept, with the figure beside them.

## `update.json` (config)

| Key | Meaning |
| --- | --- |
| `config_version` | `1`. |
| `currency_symbols` | Code to symbol, e.g. `{"USD": "$"}`. Other currencies print their code. |
| `currency_decimals` | 0 to 2 (default 0). |
| `number_decimals` | 0 to 3, for `number` metrics. |
| `metrics` | Default metric definitions (see above). |
| `metric_order` | Table order. Bundle-defined metrics follow. |
| `headline_metrics` | Shown in the TL;DR and always in the table, as "not supplied" when missing (default MRR, paying customers, runway). |
| `jump_factor` | Flag changes of this factor or more (default 10). |
| `plan_window_days` | Granola's free-plan note history (30): a period that began longer ago than this gets a coverage note. |
| `confidential_terms` | Phrases per tag (`board_only`, `legal`, `fundraising_terms`, `personnel`); a trailing `*` matches word prefixes. |
| `forward_terms` | Forward-looking phrases. |
| `metric_nouns` | Nouns that make a nearby number or number word a figure. |
| `number_words` | Number words checked before metric nouns. |
| `scale_words` | Words that always count as a figure ("million", "doubled"). |
| `legal_suffixes` | Dropped from the end of names when matching ("Inc", "LLC"). |
| `generic_name_words` | Words never matched on their own as part of a name ("Global", "Logistics"). |
| `meeting_source_terms` | Phrases that mark a metric `source` as a meeting, call or notes ("Granola", "call", "notes"); such rows are withheld. |

Config errors (missing keys, wrong types, `null` lists) exit 2.

## JSON output (`--format json`)

`status` (`draft`), `sent` (`false`), `audience`, `period`, `as_of`, `subject`, `metrics` (label, unit, current,
previous, change, states, source, as-of; text values carry the same markdown escaping as the draft), `sections`
(items printed per section), `board_only_items`, `held_back` (count per tag), `withheld`, `quote_notes`,
`commitment_notes`, `metric_notes`, `markdown` (the draft) and `email_body`.

## Escaping

Every value is escaped with `granola_text.md` after contact redaction: the text is collapsed to one line and
control, bidi and invisible characters are dropped; the backslash is escaped first, then `` ` ``, `*`, `_`, `~`,
`[`, `]`, `(`, `)`, `|`, `#`, `@`, `!`, `$`, `=`, `^` and `:` with a backslash (so `$5 to $9` is not math and
`==x==` is not a highlight), `&`, `<` and `>` become entities, every dot inside a word is escaped (so `https://…`,
`www.…`, bare domains and IPs are not autolinked), and a leading `-`, `+` or `1.` / `1)` is escaped. Currency symbols
in figures are escaped the same way. Full-width look-alike dots and colons are left as plain characters.
The draft, the email body and every text value in the JSON output use this escaping.
A value cannot form a link, image, autolink, raw HTML, emphasis, code, math, highlight or a table-cell break, and links
are printed only by the script from URLs that passed the http(s) check, percent-encoded. A web address written out in
a value stays text (pattern-based; a viewer with its own link detection could still differ).

## Founder evidence grammar and context limits

Founder evidence uses a conservative transcript grammar: the founder's label must start at column zero, outside a blockquote or list item, at the beginning of the source or after terminal punctuation. Leading timestamps and the forms `Name:`, `[Name]:`, `Name (CEO):`, `Name [CEO]:`, `Name - ` and `Name — ` are supported. A role annotation must be a supported job title or department: executive/founder titles and engineering, product, operations, sales, finance or marketing (optionally with head, VP, director or lead). Unknown roles, including stance annotations such as `sarcastic`, withhold the item. This can also withhold legitimate but unlisted job titles. Mid-line names cannot establish founder attribution. Names with conflicting roles, read-outs, duplicate words under another speaker, and an excerpt missing from an included transcript are checked separately. These are mechanical checks on caller-supplied labels, not identity verification.

A founder claim needs a bounded noun-phrase subject followed by a finite verb and a complete supported predicate. Novel product and project nouns are accepted in noun positions; they do not supply an unknown predicate. The parser cannot skip an unknown verb and treat a later word as the verb: `We put the launch on hold` does not become a statement whose verb is `hold`. Unknown predicates, punctuation introducing an aside, and unrecognised claim qualifiers withhold that item. Unlabelled source prefaces and all preceding turns are checked as context. The rest of the founder's turn and later founder turns must match complete supported past/current-status clauses, a greeting/agenda phrase, or an explicit reinforcement. A preceding question from another speaker can introduce the claim when it matches a supported how/what-is/are topic question, or how-did plus a listed business topic, time period or operational process and the status verb go, perform or progress. Those past-performance question topics and their modifiers are bounded; unfamiliar project names or adjectives in that question form are held. Framing and other qualification markers still apply to those questions. An earlier turn may contain a complete, explicit will-action commitment with no shared content words with the current claim; other preceding context must match the supported work/status or greeting forms. A new speaker label or intervening acknowledgement does not erase a framing cue. Agenda fragments accept the listed business topics, not arbitrary topic nouns; detected readout or performance framing takes precedence over a greeting or agenda shape. A second future schedule is held even when it is harmless, such as `The audit starts Monday.` Later founder wording that refers strongly to the claim must be an explicit reinforcement; an unfamiliar retraction is not accepted simply because it avoids a list of negative words. Another speaker's replies after the claim and later references still use narrower marker and date checks. Unlisted preceding questions and unrelated future plans may be held.

Assertions without `founder_stated` need a recognised past or current-status predicate. An `ask` label does not bypass this gate: an unstated ask must have a supported request noun or imperative (for example, introductions, referrals, or “Please introduce us to…”), with any following explanation limited to supported factual statements. Present-tense schedules and unrecognised assertions are held for review, including date-free plans such as `We open the beta to every warehouse.` A held item is omitted from every draft format and has a review reason; no source excerpt or rejected claim is copied into that reason. Some valid fragments and ordinary statements are therefore held too. Keep the source wording and have the founder add a reviewed item manually; do not rewrite evidence to make the parser accept it.

These checks use a bounded English grammar and phrase lists. They do not understand all implications, prove a claim is true, identify the real founder, or resolve every disagreement between notes and transcript. A qualifying statement can still pass if it fits the accepted grammar or occurs outside the context the checks associate with the claim. Unlisted wording can cause false holds. Prior round-9/10 results describe earlier code and are not clearance for this version. The bundled cases are regression tests, including previously disclosed QA strings; they are no longer unseen evaluation. Review the complete meeting and get founder approval before sending any draft.
