# Recovery policy

Steps 4 and 5 of `SKILL.md` read this file. It has four parts: where to find the decline code in each billing system, how that code maps to a class, the retry and outreach schedule, and the message for each step.

The class determines the message. If a code is not in these tables, or the field is empty, classify it **ambiguous**. Retrying an unknown decline once costs little. Running the full soft ladder on a card that can never succeed wastes every retry.

## 1. Where to read the decline code

Read the field listed here. The top-level error `code` (Stripe `card_declined`, for example) is the same for almost every decline. Classifying on it puts every failure in one bucket.

### Stripe (`billing_system: stripe`)

| What | Field path |
|---|---|
| Invoice → PaymentIntent | API versions `2025-03-31` and later: expand `payments.data.payment.payment_intent` on the invoice. Older versions: `invoice.payment_intent`. |
| **Decline code (primary)** | PaymentIntent `last_payment_error.decline_code` |
| Advice code | PaymentIntent `last_payment_error.advice_code`, or Charge `outcome.advice_code` |
| Decline code (fallback) | Charge (`PaymentIntent.latest_charge`) `outcome.reason`. Stripe clears `last_payment_error` if the PaymentIntent is updated after the failure. |
| Blocked by Stripe Radar | Charge `outcome.type` = `blocked` |
| Is Stripe already retrying? | Invoice `next_payment_attempt` (non-null means Stripe has another attempt scheduled), `attempt_count` |
| Retry call | `POST /v1/invoices/{id}/pay` |

`outcome.reason` is usually the same as `decline_code`, but not always. For a Radar block it holds a risk reason (for example `highest_risk_level` or `rule`) instead of an issuer code. Use `outcome.type` = `blocked` to recognise these.

Stripe's `advice_code` can only make a class more cautious:
- `do_not_try_again` → no retries, whatever the class. A soft or ambiguous decline with this advice is handled as **hard**.
- `confirm_card_data` → **customer_action**.
- `try_again_later` → no change.

If the decline code and `advice_code` point to different classes, use whichever comes first in this order: risk, hard, customer_action, ambiguous, soft. For example, a hard code with `confirm_card_data` stays **hard**.

### Chargebee (`billing_system: custom`)

| What | Field path |
|---|---|
| Invoice → transaction | The invoice's most recent failed payment in `linked_payments[]` (`txn_id`), then retrieve that transaction |
| Transaction outcome | `transaction.status`: `failure` or `late_failure` means a decline. `timeout` and `needs_attention` are not declines; see below. |
| **Decline code (primary)** | `transaction.error_detail.decline_code`. This is the gateway's own code, so read it together with `transaction.gateway`. |
| Decline code (fallback) | `transaction.error_code` (the code received from the gateway) and `transaction.error_text` |
| Chargebee's normalised cause | `transaction.error_detail.error_cause_id` |
| Retry call | `POST /api/v2/invoices/{invoice_id}/collect_payment` |

Chargebee returns the gateway's own codes. When `transaction.gateway` is `stripe`, use the Stripe table below. For other gateways, map the code only if it clearly matches a row in the table. Otherwise classify it **ambiguous** and put `error_text` in the summary. The Chargebee error `gateway.general.decline` is **ambiguous**.

A transaction with status `timeout` or `needs_attention` was not declined by the issuer. The connection to the gateway failed, so the payment may or may not have gone through. Do not contact the customer and do not retry. Flag it for a human to reconcile.

### Recurly (`billing_system: custom`)

| What | Field path |
|---|---|
| Invoice → transactions | `GET /invoices/{invoice_id}/transactions`, most recent with `status` = `declined` |
| **Decline code (stored transaction)** | `transaction.status_code` |
| Decline code (failed API call) | `error.transaction_error.code`, with `error.transaction_error.category` |
| Gateway's raw code | `transaction.gateway_response_code` |
| Retry call | `PUT /invoices/{invoice_id}/collect` |

Recurly returns its own normalised codes, such as `insufficient_funds` and `expired_card`, and sorts them into categories: soft, hard, fraud, communication and configuration. The API reference describes `status_code` only as "Status code". On the first real declined transaction, confirm that it holds a code from Recurly's error table before you rely on it. If a code is not in the table below, use its Recurly category: soft → **ambiguous**, communication → **soft**, hard → **hard**, fraud → **risk**, configuration → **risk** (a merchant setup problem, not a customer problem).

### Recharge (`billing_system: recharge`)

| What | Field path |
|---|---|
| Decline | `charge.error_type` and `charge.error` on a charge with `status` = `error` |
| Retry call | `POST /charges/{id}/process` |

Recharge does not publish one list of decline codes. The processor's code, such as `do_not_honor`, appears in the charge's error details. If the processor is Stripe, map the code with the Stripe table. Otherwise classify it **ambiguous**. Recharge also runs its own Failed Payment Recovery retries, so see "Retries the billing system already runs" below.

### Custom billing systems

Find the field that holds the issuer's or processor's decline reason, not the generic error type. Write the field path into the preflight report. If there is no such field, every failure is **ambiguous**.

## 2. Classes

These are the five classes from `SKILL.md` Step 4. Stripe codes are listed first because Stripe, Chargebee-on-Stripe and Recharge-on-Stripe all use them.

| Class | Meaning | Retry | Outreach |
|---|---|---|---|
| **soft** | Temporary. The same card may work later. | Yes, on the schedule | Light touch |
| **ambiguous** | The issuer declined without a reason | **Once**, then handle as hard | Yes |
| **customer_action** | The card may still work, but only after the customer does something (authenticate, confirm details, call the bank) | No scheduled retries. Retry once after the customer acts. | Ask for that specific action |
| **hard** | This payment method will not work again | Never | Ask for a new payment method |
| **risk** | Fraud signal, block list, or a merchant-side problem | Never | **None.** Flag for a human. |

### Stripe decline codes

| Class | `decline_code` / `outcome.reason` |
|---|---|
| soft | `insufficient_funds`, `card_velocity_exceeded`, `withdrawal_count_limit_exceeded`, `issuer_not_available`, `processing_error`, `reenter_transaction`, `try_again_later`, `approve_with_id` |
| ambiguous | `generic_decline` (when `outcome.type` is not `blocked`), `do_not_honor`, `call_issuer`, `no_action_taken`, `not_permitted`, `transaction_not_allowed`, `service_not_allowed`, `security_violation`, any unmapped code |
| customer_action | `authentication_required`, `authentication_not_handled`, `incorrect_cvc`, `invalid_cvc`, `incorrect_zip`, `incorrect_address`, `invalid_expiry_month`, `invalid_expiry_year`, and any code with `advice_code` = `confirm_card_data` |
| hard | `expired_card`, `incorrect_number`, `invalid_number`, `invalid_account`, `new_account_information_available`, `card_not_supported`, `currency_not_supported`, `lost_card`, `stolen_card`, `pickup_card`, `restricted_card`, `stop_payment_order`, `revocation_of_authorization`, `revocation_of_all_authorizations`, `do_not_try_again`, and any code with `advice_code` = `do_not_try_again` |
| risk | `fraudulent`, `merchant_blacklist`, any charge with `outcome.type` = `blocked`, and non-declines such as `duplicate_transaction`, `invalid_amount`, `testmode_decline` |

Stripe says the specific reason should not be shown to the customer for `lost_card`, `stolen_card`, `fraudulent` and `merchant_blacklist`. Those customers get the hard-class message, which never states a reason. Customers in the risk class get no message.

`stop_payment_order` and the `revocation_*` codes can mean the customer told their bank to stop paying you. Send the single hard-class request and nothing more. If that is ignored, the case is closer to voluntary churn than a card problem.

### Recurly codes

| Class | Recurly code |
|---|---|
| soft | `insufficient_funds`, `try_again`, `simultaneous`, `partial_approval`, `exceeds_daily_limit`, `issuer_unavailable`, `processor_not_available`, `too_many_attempts`, and the communication category (`gateway_timeout`, `gateway_unavailable`, `processor_unavailable`, `too_busy`, …) |
| ambiguous | `declined`, `declined_saveable`, `temporary_hold` (Recurly's name for Do Not Honor), `call_issuer`, `invalid_payment_method` |
| customer_action | `declined_security_code`, `call_issuer_update_cardholder_data`, `payer_authentication_rejected`, `three_d_secure_authentication`, `three_d_secure_action_required` |
| hard | `expired_card`, `declined_card_number`, `declined_expiration_date`, `invalid_payment_method_hard`, `paypal_hard_decline`, `paypal_expired_reference_id` |
| risk | every `fraud_*` code, and the configuration category (`invalid_gateway_configuration`, `no_gateway`, `invalid_login`, …) |

## 3. Retry and outreach schedule

Day 0 is the date of the invoice's **first** failed payment attempt, not the date this skill first saw it.

| Step | Day | soft | ambiguous | customer_action | hard |
|---|---|---|---|---|---|
| **1** `first_notice` | 0 | email | email | email | email |
| retry | 3 | retry | retry (its only one) | — | — |
| **2** `reminder` | 4 | email if unpaid | email if unpaid, now with hard wording | email if unpaid | email if unpaid |
| retry | 7 | retry | — | — | — |
| **3** `second_reminder` | 8 | email if unpaid | email if unpaid | email if unpaid | email if unpaid |
| retry | 12 | retry | — | — | — |
| **4** `final_notice` | 14 | email if unpaid | email if unpaid | email if unpaid | email if unpaid |
| ladder closes | 21 | outcome `abandoned`, no further contact, hand to a human | same | same | same |

Risk-class failures do not enter the ladder. There are no retries and no email. Record the class and put the failure in the summary.

Rules:
- **Retries the billing system already runs.** If the billing system still has its own retry scheduled for this invoice, do not add retries. Examples: Stripe `next_payment_attempt` is not null, or Recharge Failed Payment Recovery is active for the charge. Send the outreach steps only. A duplicate retry adds another decline on the customer's card and gains nothing.
- **Retry after customer action.** For customer_action, hard, and ambiguous after its one retry: when the payment method on file changes, retry once straight away. That retry does not count as a touch.
- **Stop on payment.** Once the invoice is paid, set `type_recovery_outcome: recovered` and send nothing further.
- **Quiet hours.** Emails follow `quiet_hours`. A step due inside the window waits for the next allowed hour.
- **Discounts.** No step in this default schedule offers a discount. If you add one, put it on `final_notice` only, and only when `max_discount_pct` > 0.
- **Touch cap.** Four steps means four touches. This matches the default `max_touches_per_customer: 4`. If the cap is lower, drop the middle steps and keep `first_notice` and `final_notice`.

### Definitions

- **Last ladder step.** Step 4, `final_notice`, sent on day 14. It is the last message a customer on the ladder receives.
- **`final_notice`.** The last outreach for an invoice. It says the payment is still open, gives the update link, and says no more reminders will follow. It may mention a service change, such as cancellation or pause, only if the billing system shows a concrete scheduled date for it. Otherwise it says nothing about consequences. It never invents a deadline.
- **Ladder window.** Days 0 to 14, from the first failed attempt to the `final_notice` day. A failure that is **past the ladder window** (first failed more than 14 days ago) and has no ledger entry is **backlog**. `SKILL.md` Step 3 covers backlog handling. Invoices that already have a ledger entry finish the ladder normally, up to day 21.
- **Ladder exhausted.** `final_notice` has been sent and day 21 has passed without payment. Set outcome `abandoned` and stop contact.

## 4. Message templates

Placeholders: `{first_name}`, `{company}`, `{product}`, `{amount}`, `{invoice_date}`, `{update_link}`, `{next_retry_date}`, `{support_email}`, `{scheduled_change}`. Fill every placeholder from billing data. If a value is missing, drop the sentence that uses it rather than guess.

Tone rules for every template:
- Lead with the link.
- Never say the customer did something wrong.
- Never name the decline reason. Say "your bank", not "your card was reported stolen".
- No false urgency: no "act now", "immediately", "last chance" or countdowns, and no deadlines the billing system does not show.
- One call to action.
- If the Space has a brand voice file, adapt the wording to it but keep these rules.

### Step 1: `first_notice`

**Subject:** Your {product} payment didn't go through

> Hi {first_name},
>
> You can update your payment details here: {update_link}
>
> *[soft]* We'll try the payment for {amount} again on {next_retry_date}. You don't need to do anything, but if you'd like to use a different card, the link above is the quickest way.
>
> *[ambiguous]* Your bank didn't approve the payment for {amount} and didn't give a reason. We'll try once more. Updating your card, or checking with your bank, usually sorts it out.
>
> *[customer_action]* Your bank needs one more step from you before the payment for {amount} can go through. That's usually confirming the payment or checking your card details. The link above walks you through it.
>
> *[hard]* The card on file can't be used for the payment of {amount}, so we won't try it again. Adding a current card at the link above is all that's needed.
>
> Questions? Just reply, or write to {support_email}.
>
> {company}

### Step 2: `reminder`

**Subject:** Quick reminder about your {product} payment

> Hi {first_name},
>
> The payment of {amount} for {product} is still open. You can update your payment details here: {update_link}
>
> *[soft]* We'll try again on {next_retry_date}.
> *[ambiguous, now handled as hard; customer_action; hard]* We won't retry the current card until it's updated.
>
> {company}

### Step 3: `second_reminder`

**Subject:** Your {product} payment is still open

> Hi {first_name},
>
> Your payment details can be updated here: {update_link}
>
> The {amount} payment from {invoice_date} hasn't gone through yet. If something's changed on your side, or you'd like help, just reply to this email.
>
> {company}

### Step 4: `final_notice`

**Subject:** Your {product} payment: last reminder

> Hi {first_name},
>
> The payment of {amount} from {invoice_date} is still open. You can update your payment details here: {update_link}
>
> This is the last reminder we'll send about it.
> *[only if the billing system shows a scheduled date]* Your {product} subscription is scheduled to {scheduled_change}.
>
> If you'd rather talk it through, reply to this email or write to {support_email}.
>
> {company}

### Backlog: single touch (`final_notice` framing)

Use only after a human approves it (`SKILL.md` Step 3). One per invoice.

**Subject:** An open {product} invoice from {invoice_date}

> Hi {first_name},
>
> There's still an open invoice of {amount} from {invoice_date} on your {product} account. If you'd like to settle it, you can update your payment details here: {update_link}
>
> We won't send further reminders about this invoice. If you think it's been sent in error, just reply and we'll look into it.
>
> {company}

## Sources

Checked 2026-09-23.

- Stripe decline codes: https://docs.stripe.com/declines/codes
- Stripe card declines, `outcome.reason`, `advice_code`, `last_payment_error.decline_code`: https://docs.stripe.com/declines/card
- Stripe declines overview (`outcome.reason` vs. `decline_code`, Radar blocks): https://docs.stripe.com/declines
- Stripe error object (`decline_code`, `code`): https://docs.stripe.com/api/errors
- Stripe PaymentIntent object (`last_payment_error`): https://docs.stripe.com/api/payment_intents/object
- Stripe Charge object (`outcome`): https://docs.stripe.com/api/charges/object
- Stripe Invoice object (`next_payment_attempt`, `attempt_count`): https://docs.stripe.com/api/invoices/object
- Stripe invoice ↔ PaymentIntent change in `2025-03-31.basil`: https://docs.stripe.com/changelog/basil/2025-03-31/add-support-for-multiple-partial-payments-on-invoices
- Stripe pay an invoice: https://docs.stripe.com/api/invoices/pay
- Chargebee transaction object (`status`, `error_code`, `error_text`, `error_detail`): https://apidocs.chargebee.com/docs/api/transactions
- Chargebee gateway error references (`decline_code`, `error_cause_id`): https://apidocs.chargebee.com/docs/api/gateway_error_references
- Chargebee `gateway.general.decline`: https://apidocs.chargebee.com/docs/api/errors/gateway.general.decline
- Chargebee collect payment for an invoice: https://apidocs.chargebee.com/docs/api/invoices/collect-payment-for-an-invoice
- Recurly transaction error codes and categories: https://docs.recurly.com/recurly-subscriptions/docs/api-transaction-errors
- Recurly v3 API (`Transaction`, `TransactionError`, collect invoice): https://recurly.com/developers/api/v2021-02-25/index.html
- Recharge charge object (`error`, `error_type`) and process a charge: https://developer.rechargepayments.com/2021-11/charges/charge_process
- Recharge Failed Payment Recovery: https://support.getrecharge.com/hc/en-us/articles/21158518425367-How-to-create-a-Failed-Payment-Recovery-strategy

Write-scope checks for `SKILL.md` Step 1:
- Stripe restricted keys (Read/Write/None per resource; no endpoint lists a key's permissions): https://docs.stripe.com/keys/restricted-api-keys
- Stripe API permissions reference: https://docs.stripe.com/keys/permissions-reference
- Recharge API token scopes (set on the token edit page): https://docs.rechargepayments.com/docs/recharge-api-key
- Chargebee API key types (Full-access, Read-only: All / Restricted, Publishable): https://www.chargebee.com/docs/billing/2.0/site-configuration/api_keys
- Recurly REST API keys: https://docs.recurly.com/recurly-subscriptions/docs/api-keys
