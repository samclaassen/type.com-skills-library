# Accounting rules

The planner (`main.py`) is deterministic and works in integer cents. It never rounds source money: amounts with more than two significant decimals go to review.

## Eligible order

- Status in `eligibleStatusIds`; not refunded/cancelled (statuses 4, 5, 6, 14 or any `refunded_amount`).
- Transaction currency (`default_currency_code`) equals `config.currency`.
- No tax anywhere (line, shipping, subtotal). Taxable orders → `taxable_order_requires_manual_entry`.
- No handling, wrapping, gift certificate or store credit amounts.
- Every product: mapped SKU, whole quantity, `price_ex_tax × quantity = total_ex_tax`, no product-level refunds, no allocated `applied_discounts`.
- Reconciliation: Σ lines = `subtotal_ex_tax`; subtotal + shipping − discount = `total_ex_tax`; `total_ex_tax` + tax = `total_inc_tax` > 0; and the rendered lines sum exactly to the order total.
- Payment status `captured` → paid treatment; `pending`/`authorized` → invoice; anything else → review.

## Document mapping

| Order | Output |
| --- | --- |
| Unpaid | Invoice rows: products, then a shipping line (`shippingItemName`). DocNumber `invoicePrefix + orderId` (≤ 21 chars). |
| Paid, `sales_receipt_worksheet` | Sales receipt worksheet rows, including a negative "Order discount" line when present. |
| Paid, `invoice_then_payment` | Invoice rows plus one `payments-to-record.csv` row for the full total. |
| Any, with `purchaseOrders` | Supplier PO worksheet lines for SKUs in `suppliers` (verified cost, same currency), grouped by vendor with a subtotal row. |

Order discounts cannot be put on an import invoice (QuickBooks rejects negative charges) → `discount_not_supported_by_invoice_import`. Invoice date = order creation date in the store timezone; due date = invoice date + `termsDays`. Non-US rows carry `zeroTaxCodeName` with tax amount 0.00.

## Customers

- QuickBooks connected and complete: match by billing email (case-insensitive). One active match → its DisplayName (must be unique). Several → `ambiguous_customer_email`; inactive → `inactive_customer`; other currency → `customer_currency_mismatch`.
- No match: if `allowNewCustomers`, a derived name (`customerNameSource`) goes to `qbo-customers-import.csv`, unless it collides with an existing customer (`customer_name_collision`), with another buyer in the batch (`new_customer_name_conflict`), or the same email derives a different name in another order of the batch (`new_customer_email_conflict`). Otherwise `customer_not_found`.
- Customer list incomplete → `qbo_customer_list_incomplete` (never assumed new).
- No QuickBooks connection → derived name, marked `unverified`. The customer file may then include buyers who already exist in QuickBooks (SETUP.md).
- Phone numbers: a phone that is strict E.164 (`+` then 7–15 digits) once spaces, dashes, dots and parentheses are removed is written in that form (`+1 (555) 123-4567` → `+15551234567`, `+44 (20) 7946-0958` → `+442079460958`). Strict E.164 and numbers that do not start with `= + - @` (e.g. `555-0100`, `(555) 123-4567`) are written exactly. Any other phone starting with `= + - @` (e.g. `+1/1`, `+1 555`, `+1 555 0100 x12`) gets a leading `'` like every other text cell, because a spreadsheet would evaluate it as arithmetic. QuickBooks may store that apostrophe; fix such numbers in BigCommerce, or edit them in QuickBooks after import. Do not open the CSVs in a spreadsheet and re-save them before importing.

## Review reasons

Planner (per order): `taxable_order_requires_manual_entry`, `refund_or_cancellation_requires_reconciliation`, `product_refund_requires_reconciliation`, `ineligible_order_status`, `currency_not_approved`, `unsupported_payment_status`, `unsupported_fees_or_tender`, `missing_or_non_cent_order_amount`, `line_price_discount_or_rounding_requires_review`, `allocated_product_discounts_require_review`, `discounted_product_total_requires_review`, `line_tax_does_not_reconcile`, `discount_or_subtotal_does_not_reconcile`, `order_total_does_not_reconcile`, `export_total_does_not_reconcile`, `discount_not_supported_by_invoice_import`, `item_mapping_required`, `shipping_item_mapping_required`, `supplier_cost_or_currency_requires_review`, `document_number_too_long`, customer reasons above, input-shape reasons (`valid_sku_required`, `valid_buyer_email_required`, `tax_class_required`, `invalid_transaction_date`, …), `duplicate_order_in_batch`, `conflicting_order_snapshots`.

Journal/QuickBooks (per order): `changed_after_export`, `changed_after_posted_in_qbo`, `refund_or_cancellation_after_export`, `docnumber_already_used_in_qbo`, `duplicate_docnumber_in_qbo`, `posted_invoice_mismatch`, `posted_status_unknown`, `reexport_blocked_already_in_qbo`, `reexport_order_not_found`, `confirmation_backlog_full`, `journal_read_failed`, `journal_record_invalid`, `order_claim_conflict`, `pending_index_update_failed`, `order_exceeds_import_line_limit`, `order_products_not_found`, `order_creation_date_invalid`, `order_too_large_for_planner`, `products_read_failed`, `products_read_failed_repeatedly`, `pending_order_not_found`, `pending_order_read_failed`, `unacknowledged_backlog_full`, `batch_state_unknown`, `reexport_deferred_batch_size`. Follow-up (not a re-export): `payment_captured_after_invoice_export`.

`review-queue.csv` gives the human action for each reason (`REVIEW_ACTIONS` in `render.ts`).

Posted-invoice detection limit: QuickBooks lists are paged with a 5-row overlap check, and rows after a page too short for that overlap make the list incomplete (`posted_status_unknown`). A delete and an insert before the same page boundary between two page reads are not detected; the inserted invoice can be missed. This needs concurrent QuickBooks edits during the run (unverified live).

## Not supported (manual)

Sales tax/VAT amounts, multicurrency/FX, refunds and credit memos, partial payments, payments captured after an invoice export (flagged once for manual Receive payment, no worksheet row), gift certificates/store credit, allocated or per-line discounts, inventory quantities, and sending POs to suppliers.
