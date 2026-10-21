# Data contract — invoice-chaser

All files are UTF-8. Dates are `YYYY-MM-DD`. Money in the ledger and brief is
plain decimal in major units (`8000.00`, `1450.5`) — no currency symbols or
thousands separators. Bad input exits `2` naming the file, line (or item index),
and column; the script never drops or repairs a row.

## 1. Ledger (`ledger_file`, markdown) — source of truth when `billing_source: manual`

Free text (title, notes) may precede the table and is preserved on rewrite. The
table header must be exactly:

```
| number | client | contact_name | contact_email | client_address | issue_date | terms_days | due_date | currency | total | amount_paid | status | sent_date | paid_date | last_chase_tier | last_chase_date |
```

followed by a `|---|…|` separator, then one row per invoice. Only blank lines may
follow the table. Create an empty one with `invoice.py init-ledger --ledger PATH`.

| Column | Type | Required | Rules |
| --- | --- | --- | --- |
| `number` | string | yes | Unique. New numbers are `{invoice_prefix}-{YYYY}-{seq:03d}`; seq is max existing seq for that prefix+year + 1. |
| `client` | string | yes | Client matching for auto-fill is case/whitespace-insensitive exact name. |
| `contact_name` | string | no | First word becomes the email greeting ("Hi Sarah"); blank → "Hi there". |
| `contact_email` | email | no | Needed to send; chase flags rows without one. |
| `client_address` | string | no | Single line. |
| `issue_date` | date | yes | |
| `terms_days` | integer ≥ 0 | yes | 0 renders "due on receipt". |
| `due_date` | date | yes | Not before `issue_date`. |
| `currency` | ISO 4217 | yes | Uppercase, e.g. `USD`. One currency per invoice. |
| `total` | money > 0 | yes | Incl. tax if a tax rate was configured. Decimal places ≤ the currency's minor units. |
| `amount_paid` | money ≥ 0 | yes | ≤ `total`. |
| `status` | enum | yes | `draft`, `sent`, `partial`, `paid`, `void`. `overdue` is derived, never stored. |
| `sent_date` | date | no | Set by `mark-sent`. |
| `paid_date` | date | `paid` only | Set by `mark-paid` when the balance reaches zero. |
| `last_chase_tier` | integer ≥ 0 | yes | 0 = never chased; N = tier N of `chase_schedule` was sent. Set only by `chase --record`. |
| `last_chase_date` | date | when tier > 0 | Must be blank when tier is 0. |

Consistency rules (violations exit 2): `paid` ⇒ `amount_paid == total` and a
`paid_date`; `partial` ⇒ `0 < amount_paid < total`; `draft`/`sent` ⇒
`amount_paid == 0`. The `|` character is not allowed inside cells.

Never put bank details or payment links in any ledger column.

## 2. Brief (`brief.json`) — what the agent extracted from the user's message

```json
{
  "client": "Acme Corp",
  "contact_name": "Sarah Chen",            // optional, auto-filled from ledger
  "contact_email": "sarah@acmecorp.example", // optional if the ledger has one
  "client_address": "…",                    // optional, auto-filled
  "currency": "USD",                        // optional, default config currency
  "terms_days": 14,                         // optional override; else client's last terms; else config
  "notes": "PO 4471",                       // optional, printed on the invoice
  "items": [
    {"description": "Brand strategy workshop", "qty": 1, "rate": 2500,
     "service_date": "2026-09-15", "service_end_date": null, "currency": "USD"}
  ]
}
```

(Comments above are explanatory; real JSON must not contain them.) Unknown keys
are rejected. `qty` and `rate` are required, numeric (or numeric strings), and
> 0 — never default them. An item `currency` different from the invoice
currency is refused. Line amount = `qty × rate` rounded half-up to the
currency's minor unit; subtotal = sum of line amounts; tax = subtotal ×
`tax_rate` rounded half-up; total = subtotal + tax.

Time logs: one entry per line item, or group entries by task and put summed
hours in `qty` with the hourly rate in `rate`; put the date range in
`service_date`/`service_end_date`.

## 3. Stripe input (`--stripe-invoices`) — read-only

Source: `GET https://api.stripe.com/v1/invoices?status=open&limit=100`, paging
with `starting_after=<last id>` while `has_more` is true. Pass either the whole
list object or a JSON array of invoice objects. Fields read:

| Stripe field | Use |
| --- | --- |
| `number` | Invoice number (required on open invoices). |
| `status` | Only `open` is considered; others are counted as "not considered". |
| `customer_name`, `customer_email` | Client and recipient (no contact first name → "Hi there"). |
| `currency` | Lowercase in Stripe; uppercased. |
| `amount_due`, `amount_remaining` | Integers in minor units. Divided by 10^d where d = 0 for zero-decimal currencies (`currencies.zero_decimal`, e.g. JPY, KRW), 3 for `three_decimal` (BHD, JOD, KWD, OMR, TND), else 2. `amount_due − amount_remaining > 0` ⇒ partial. |
| `due_date` | Unix seconds (UTC) or null. Null = auto-charge invoice, never chased. |
| `created` | Unix seconds; shown as the issue date. |

`hosted_invoice_url` is deliberately not read; drafts carry the placeholder
`[Payment link: Stripe hosted invoice page for <number>]` for the human to
fill at send time. The skill never creates, finalizes, sends, voids or pays
Stripe invoices.

**`create` in Stripe mode** — optional `--stripe-invoices` export of
`GET /v1/invoices?limit=100` (all statuses, every page). Read only for hints:
`number` (the next number continues the highest trailing-digit number; several
prefixes → exit 2, pass `--number`), `customer_name`/`customer_email` (contact
email auto-fill), `status`, `currency`, `amount_due` (possible-duplicate check),
`created`. No ledger is read or written. Without `--number` or an export, the
preview shows "(number assigned by Stripe)" and JSON `number` is null.

**Chase log (Stripe mode)** — `--chase-log chase-log.md`, a markdown table
`| number | last_chase_tier | last_chase_date |`, created on first `--record`.
Without it, every overdue invoice looks never-chased (the output says so).

## 4. Accounting tools (map into the ledger, `billing_source: manual`)

| Ledger column | QuickBooks Online `Invoice` | Xero `GET /api.xro/2.0/Invoices?Statuses=AUTHORISED` |
| --- | --- | --- |
| `number` | `DocNumber` | `InvoiceNumber` |
| `client` | `CustomerRef.name` | `Contact.Name` |
| `contact_email` | `BillEmail.Address` | Contact `EmailAddress` |
| `issue_date` / `due_date` | `TxnDate` / `DueDate` | `DateString` / `DueDateString` (date part) |
| `currency` | `CurrencyRef.value` | `CurrencyCode` |
| `total` / `amount_paid` | `TotalAmt` / `TotalAmt − Balance` | `Total` / `AmountPaid` |

Derive `status` (`sent` if nothing paid, else `partial`) and `terms_days` (due −
issue). When refreshing, carry `last_chase_tier`/`last_chase_date` over from the
previous ledger row with the same number.

## 5. Config (`invoice.json` defaults; the user's copy is `invoice-chaser-settings.json`)

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `business_name`, `business_address`, `from_email` | string | `""` | Invoice "From" line. Required for `create`. Not secrets. |
| `sender_name` | string | `""` | Chase email sign-off. Required for `chase`. |
| `billing_source` | `manual`/`stripe` | `""` | Blank → exit 2 "run Step 0". Must match `--ledger` / `--stripe-invoices`. |
| `currency` | ISO code | `USD` | Default invoice currency. |
| `payment_terms_days` | int 0–365 | 30 | Default NET terms. |
| `payment_instructions_ref` | string | `""` | Name of a secure note. Rejected if it contains a URL, `@`, a banking label next to a number (`acct`, `account`, `sort code`, `routing`, `IBAN`, ... followed by 4+ digits), a 6+ digit run or an IBAN-like token. Digit groups split by spaces, hyphens, dots or slashes are joined first, so `acct 1234-5678` and `12-34-56` are rejected. Dates (`2026-10-02`, `2026/10/02`, `02-10-2026`) and year ranges (`2024-2025`, `FY2024-25`) are ignored, so `Payments FY2024-2025` and `Invoice note 2026-10-02` are accepted. Spec name: `bank_details` (rejected as a key to prevent pasting real details). |
| `invoice_prefix` | `[A-Z][A-Z0-9]{0,9}` | `INV` | |
| `tax_rate` | null or 0–1 | null | Fixed rate on the subtotal. null → "excluding tax". |
| `tax_label` | string | `Tax` | e.g. `VAT`, `Sales tax`. |
| `late_fee` | null or `{note, from_tier}` | null | Exact agreed wording appended to drafts at tier ≥ `from_tier` and printed on invoices. Never computed. |
| `chase_schedule` | list of `{days, tone}` | 1/7/14/30 | Strictly increasing days; `tone` must exist in the templates file. |
| `follow_up` | `draft`/`send` | `draft` | |
| `ledger_file` | path | `invoice-ledger.md` | Used when `--ledger` is omitted for create/mark-* (manual mode only). |
| `post_to` | string | `""` | Agent-side destination. |
| `aging_buckets_days` | increasing ints | [30, 60, 90] | Status aging columns. |
| `forbidden_phrases_without_late_fee` | string[] | see file | Template text that is refused while `late_fee` is null. Whole-word, case-insensitive, plural `s`/`es` allowed: `interest` matches "interest charges" but not "interested". |
| `allowed_phrases_without_late_fee` | string[] | see file | Optional. Benign phrases removed before the banned-term check, so "interest-free", "No interest, no fees" and "0% interest" pass while "interest charges" and "we may add interest" are still refused. Case-insensitive; spaces and hyphens are interchangeable; a phrase directly followed by charges/fees/rates/penalties is not treated as benign. Missing = `[]`. |
| `currencies` | object | see file | `symbols`, `zero_decimal`, `three_decimal`. Currencies without a symbol print as `1,234.00 CHF`. |

## 6. Templates (`chase_templates.json`)

`signoff` (string) and `tones.{name}` = `{label, subject, body: [paragraphs]}`.
Allowed placeholders: `{number} {client} {greeting_name} {amount} {due}
{overdue} {payment} {sender_name} {business_name}`; any other placeholder exits
2. `{amount}` is the balance, phrased "X outstanding of Y" for partial payments;
`{overdue}` is "1 day", "a week", "two weeks", or "N days".

## Failure modes (exit 2)

Missing/invalid file or JSON; ledger header drift, wrong cell count, content
after the table, duplicate number, bad date, bad money precision, inconsistent
status; brief with missing/zero/negative qty or rate, no items, unknown keys,
mixed currencies, no recipient email; config without `billing_source`, with a
`bank_details` key or a secret-looking reference, or templates with legal
language while `late_fee` is null; `mark-paid` on a draft or overpayment;
`chase --record` for an invoice with no draft in that run; Stripe amounts that
are not integer minor units; Stripe-mode `create` with `--ledger`/`--write`, or
an export whose numbers use several prefixes; `--number`/`--stripe-invoices` on
`create` in manual mode.
