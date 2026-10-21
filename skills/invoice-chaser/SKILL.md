---
name: invoice-chaser
description: Generate professional invoices from a brief or time log, track payment status in a simple ledger or Stripe, and draft polite, escalating follow-up emails for overdue invoices. Use when someone says "invoice [client] for [work]", "invoice from this time log", "what invoices are outstanding?", "who owes us money?", "chase overdue invoices", or on a weekly chase schedule. Works standalone with a markdown ledger or read-only against Stripe.
---

# Invoice Generator & Chaser

Freelancers and small teams put off invoicing because it is tedious, then put off chasing because it is awkward. Both cost real money. This skill makes invoicing as fast as describing the work, and makes chasing routine enough that nobody has to feel uncomfortable about it.

It does three jobs:

- **Create**: brief or time log → a numbered, totalled invoice for review.
- **Chase**: overdue invoices → one message with a table and one drafted follow-up per invoice. The tone escalates over four tiers.
- **Status**: what is outstanding, overdue, partly paid, or still a draft, with aging buckets.

Nothing reaches a client without a human approving it.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure every file listed with this skill (scripts, config, sample data, `run.sh`, `expected_output.txt`) is in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `invoice-chaser`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`.
4. **Try the sample first.** If the skill ships `run.sh` and `expected_output.txt`, run `bash run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Config

`invoice.json` ships the defaults; do not edit it. The user's confirmed values live in a settings file in their working folder or Space, `invoice-chaser-settings.json`: a copy of `invoice.json` with the Step 0 answers filled in. It is not part of the skill. Pass it to every command with `--config invoice-chaser-settings.json`. Every key and its validation rule is listed in `DATA_CONTRACT.md`.

```yaml
billing_source: ""              # stripe | manual (where payment status comes from). Set in Step 0
currency: USD                   # ISO 4217 default for new invoices
payment_terms_days: 30          # default NET terms
payment_instructions_ref: ""    # NAME of a secure note holding bank details / pay links (spec: bank_details)
invoice_prefix: "INV"           # INV-2026-001, sequence restarts each year
chase_schedule:                 # days overdue → tone
  - {days: 1,  tone: gentle}      # day after due: gentle reminder
  - {days: 7,  tone: firm}        # 1 week: firmer follow-up
  - {days: 14, tone: escalation}  # 2 weeks: escalation
  - {days: 30, tone: final}       # 1 month: final notice
follow_up: draft                # draft | send (draft by default)
ledger_file: invoice-ledger.md  # source of truth when billing_source: manual
post_to: ""                     # Slack channel, or blank for this Space
business_name: ""               # shown on invoices and email sign-offs (not secret)
business_address: ""
from_email: ""
sender_name: ""                 # signs the chase emails
tax_rate: null                  # e.g. 0.0825. null means totals say "excluding tax"
tax_label: "Tax"
late_fee: null                  # null, or {note: "<exact agreed wording>", from_tier: 4}
```

Email wording lives in `chase_templates.json`. The script refuses to start if a template mentions late fees, interest or legal action while `late_fee` is null. Banned terms match whole words, so "interest" is caught but "interested" is not. Benign phrases listed in `allowed_phrases_without_late_fee` ("interest-free", "no interest", "0% interest") are ignored by that check.

## Step 0 — First run: confirm connections and identity

Run this step when there is no `invoice-chaser-settings.json` yet, when its `billing_source` or business identity fields are blank, or when the configured source stops working.

1. **Detect.** Find which billing sources actually work in this Space. Making one real read call is the only proof.
   - **In Type, also check the workspace:** a connection (Stripe, Gmail, Outlook) can exist in the workspace without being added to this Space. List those too. Tell the user about each one and offer to add it to the Space. Never treat it as absent without saying so.
   - **Stripe:** `GET https://api.stripe.com/v1/invoices?limit=1`. A tool that is listed but fails this call does not count.
   - **Ledger:** look for `ledger_file` in the Space files. Run `status` on it. A clean exit means it is readable.
   - **Email (only matters for `follow_up: send`):** check whether Gmail or Outlook can read the user's own profile.
2. **Propose.** For example: "I found Stripe connected and no ledger. Use `billing_source: stripe`?" If both work, ask which one is the source of truth. If neither works, propose `manual` and create the ledger with `python3 invoice.py init-ledger --ledger invoice-ledger.md`.
3. **Ask for identity and payment reference.** You need `business_name`, `business_address`, `from_email` and `sender_name`. You also need the *name* of the secure note that holds payment details. If the user pastes bank details or payment links into chat, do not copy them into config, the ledger or this skill.
4. **Wait for the user to confirm.** Never pick a billing source silently, or just because it comes first in a list.
5. **Save** the confirmed values to `invoice-chaser-settings.json` in the user's working folder or Space: copy `invoice.json` and fill in the answers. Never edit this SKILL.md or the skill's own `invoice.json`; that breaks the published-file check in Before you run. If you cannot write files, give the user the exact JSON lines to save.

If an automation triggered the run and nobody is there to confirm, post one setup question to `post_to`, then exit. That is the only message this skill posts outside a chase or status report.

## Step 1 — Preflight

1. `bash run.sh` matches `expected_output.txt` (see Before you run).
2. The source is readable. For `manual`, run `status` on the ledger. Exit code 2 with "ledger is unreadable — stop, don't guess" means stop. Tell the user which line is broken. Never rebuild the ledger from memory.
3. For Stripe, the key can read invoices. This skill never needs write access to Stripe and never uses it.

## Create an invoice

1. **Parse the brief.** Pull the client, contact, line items (description, quantity, rate) and service dates from the user's message or attached time log. Write them to `brief.json` (schema in `DATA_CONTRACT.md`). For a time log, group the entries into line items by task, with hours as `qty`. Never invent a line item, a quantity or a rate. If anything is ambiguous, ask.
2. **Run the preview** (no ledger change yet). This and steps 4–5 are for `billing_source: manual`; for Stripe see below.
   ```bash
   python3 invoice.py create --brief brief.json --ledger invoice-ledger.md \
     --config invoice-chaser-settings.json --as-of 2026-10-02
   ```
   The script looks up earlier invoices to the same client. It auto-fills email, contact name, address and terms, assigns the next number, and computes subtotal, tax and total using decimal arithmetic with half-up rounding. The due date is the issue date plus the terms. Exit 2 means a rate, quantity or email is missing or invalid. Ask the user for that exact value, then rerun.
3. **Post for review.** Show the invoice and the "Before this goes out" notes. Say what was auto-filled, and point out any possible duplicate.
4. **On approval**, rerun with `--write` to append the invoice to the ledger as `draft`. Then send it the way the user prefers. Once it has actually gone out, run `mark-sent --number INV-… --date YYYY-MM-DD`.
5. **Payments**: `mark-paid --number INV-… --date YYYY-MM-DD [--amount 2500.00]`. A partial payment sets the status to `partial` and keeps the balance. An overpayment is refused, because it needs a human decision.

### With `billing_source: stripe`

Stripe is the source of truth and is read-only for this skill. `create` shows a draft for the user to create in Stripe themselves. It never writes to Stripe, needs no ledger and refuses `--ledger` and `--write`.

```bash
python3 invoice.py create --brief brief.json --config invoice-chaser-settings.json \
  --as-of 2026-10-02 [--stripe-invoices stripe-all.json] [--number FERN-0010]
```

- **Number.** Stripe assigns the real number when the user finalizes the invoice. By default the preview says "(number assigned by Stripe)". To show the expected number, pass `--stripe-invoices` with an export of the user's invoices: `GET /v1/invoices?limit=100`, all statuses, every page. The script continues from the highest number. If the numbers use several prefixes, it stops and you ask the user for the number. Or ask the user and pass `--number`.
- **Auto-fill.** With an export, the contact email comes from the client's latest Stripe invoice, and same-client, same-total invoices are flagged as possible duplicates.
- **After review**, the user creates and sends the invoice in Stripe. There is no `mark-sent` or `mark-paid` in this mode; `chase` and `status` read status from Stripe.

## Chase overdue invoices

1. **Get payment status.**
   - **manual:** the ledger.
   - **stripe:** call `GET /v1/invoices?status=open&limit=100` and page with `starting_after` until `has_more` is false. Save the combined `data` array as `stripe.json`. See `DATA_CONTRACT.md` for the fields used and the zero-decimal currency handling.
2. **Run it:**
   ```bash
   python3 invoice.py chase --ledger invoice-ledger.md --config invoice-chaser-settings.json --as-of 2026-10-02
   # or: --stripe-invoices stripe.json --chase-log chase-log.md
   ```
   An invoice is overdue when its status is `sent` or `partial` and its due date is before `--as-of`. The script assigns the highest tier reached. It skips any invoice whose tier was already sent, which is tracked in `last_chase_tier`, so weekly runs never repeat a reminder. Once the final notice has gone, it stops drafting and flags the invoice for a human decision.
3. **Post one message** to `post_to`: the summary, the table and the numbered drafts, all copied verbatim. Do not split it into several messages.
4. **After approval and sending**, record what actually went out:
   `chase … --as-of <same date> --record INV-2026-038,INV-2026-039`.
   Only `--record` changes chase history.

**`follow_up: draft`** (default): nothing is sent. The user replies with something like "send 1 and 3", and you send only those.

**`follow_up: send`**: tiers below the final notice may be sent automatically. This requires an email connection confirmed in Step 0. The final notice always waits for approval.

Never write payment details into a draft yourself. The placeholder `[Payment details: see <ref>]` tells the user where to paste them at send time. In Stripe mode the placeholder points to the invoice's hosted page instead.

## Status check

```bash
python3 invoice.py status --ledger invoice-ledger.md --config invoice-chaser-settings.json --as-of 2026-10-02
```

Lead with the outstanding and overdue totals per currency. Totals are never summed across currencies. Then give the oldest overdue items. Drafts are listed separately and are not counted as outstanding.

`create`, `chase` and `status` accept `--json`. `init-ledger`, `mark-sent` and `mark-paid` print one plain confirmation line and do not.

## Reading and presenting the output

- Every amount, date and day count is observed from the ledger or Stripe. The only computed values are line amounts, tax, totals and days overdue.
- In the chase table, "Tier reached" is what the schedule calls for. "Last chased" is what was recorded. The Action column says whether a draft is ready, or when the next tier falls due.
- An invoice getting its "first reminder" at a high tier (for example escalation at 15 days) means nobody chased it earlier. Mention that, because the user may want to soften the draft.

## Guardrails

- Never store bank details, payment links or financial credentials in the skill, the config or the ledger. Reference a secure note via `payment_instructions_ref`. The script rejects a `bank_details` key and any reference that looks like a link or an account number, including numbers split by spaces, hyphens or dots.
- Never send a chase email or an invoice without approval in `draft` mode. The final notice always needs approval.
- Never fabricate line items. Missing, zero or negative rates and quantities stop the script. Ask, don't guess amounts.
- The ledger is the source of truth when `billing_source: manual`. If it is corrupted or unreadable, stop and say which line is broken.
- Never include late fees or legal language unless `late_fee` is explicitly configured. Even then, use only the configured wording, and only from `from_tier` onwards. The script never calculates a fee. The default tone is professional and human.
- Tax is out of scope unless a fixed `tax_rate` is configured. Without one, say "excluding tax" rather than calculating it wrong.
- One invoice, one currency. Mixed-currency briefs are refused.
- Stripe is read-only for this skill.

## Automation recipe

Nothing happens until this has a trigger. In **Space settings → Automations**:

- **Chase run:** Schedule, weekly (for example Monday 9:00). Prompt: "Use the Invoice Generator & Chaser skill to run the chase as of today and post the drafts."
- **Status:** optional monthly schedule for month-end. Prompt: "Use the Invoice Generator & Chaser skill to post the outstanding-invoices status as of today."

The prompts name the skill by its display name, so they keep working whatever handle the skill has in your workspace.

New automations start disabled. Enable it. Invoice creation is always interactive.

## Limits

- No PDF rendering. The invoice is markdown. Paste it into the user's invoice template, or export it if their tools allow.
- Accounting tools (QuickBooks, Xero) are supported only by exporting open invoices into the ledger format. The mapping is in `DATA_CONTRACT.md`. Keep `last_chase_tier` when you refresh the ledger.
- Late fees, interest, FX conversion and multi-rate tax are not calculated.
- Stripe mode skips open invoices with no `due_date` (auto-charge invoices). It cannot see payments recorded outside Stripe.

## Verification status

**Tested offline:** `run.sh` (create, chase and status on fictional data) is byte-identical across runs. `tests.sh` passes 65 checks. They cover:

- invalid input: missing, zero or negative rates; mixed currencies; corrupted ledgers that name the bad line; secrets in config, including account numbers split by separators; legal wording without `late_fee`, matched as whole words;
- calculations: tax rounding, yearly numbering, partial payments, overpayment refusal;
- chase behaviour: tier recording without repeats, the hand-off after the final notice;
- Stripe JSON handling: minor-unit and zero-decimal currencies, and a local chase log;
- Stripe-mode `create` with no ledger: Stripe-assigned, derived or `--number` numbering;
- totals for currencies without a symbol (code shown once).

**Unverified against live systems:** Stripe API calls and pagination, sending email through Gmail or Outlook, and automation triggers. These have not been run against real accounts.
