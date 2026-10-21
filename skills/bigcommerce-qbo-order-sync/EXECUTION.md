# Export → upload → acknowledge → import → confirm

## 1. Preview

Run with `mode: "preview"` for a fixed window. The run records nothing.

- The folder is `PREVIEW-<…>`, every file name starts with `PREVIEW-DO-NOT-IMPORT-`, and `summary.md` starts with "PREVIEW — DO NOT IMPORT".
- Show the user `summary.md` and the review queue. Fix the configuration (item map, shipping item, suppliers) and rerun the preview until the result is right.
- Preview batches cannot be acknowledged. Never import them: a later export of the same orders would duplicate them in QuickBooks.

## 2. Export (prepare a batch)

With the user's approval, rerun the same window (or poll) with `mode: "export"`. The run:

1. Reads the follow-up index of pending orders and of exported invoices awaiting confirmation. For each pending order it checks the batch that holds it:
   - If that batch is **acknowledged** and its manifest lists this order with the same row hash, the order is recorded `exported`.
   - Otherwise it is **carried**: its current rows are rendered into this batch.
2. Decides every listed, re-exported and carried order: plan, review, QuickBooks posted check, changes since export.
3. Before rewriting any pending order, it moves the order's earlier batch from `prepared` to `superseded` with a compare-and-swap. If that batch was acknowledged at the same moment, its orders are treated as exported instead.
4. Adds new orders to the index, then records each rendered order as `pending` in this batch (`pendingBatch`: batch ID, row hash). An order whose record cannot be written is left out of the batch (`order_claim_conflict`). It is not lost: it stays pending and is carried next run.
5. Records the batch manifest (create-only): order IDs, row hashes, file names, sizes and SHA-256 hashes, and `contentSha256`.
6. Writes the batch folder all or nothing (staging folder, fsync, rename).

The result has `status: "prepared"`, `acknowledge: { batchId, contentSha256 }`, `supersedes` (the earlier unacknowledged batch IDs), `carried`, `notCarried`, and file paths, sizes and SHA-256 hashes. `acknowledgedBatchesCompleted` (when present, also on `blocked` results) lists acknowledged batches whose orders this run recorded as exported because the acknowledging call did not finish them; `nextStep` and `summary.md` then say "Batch X was acknowledged — its files are ready to import". Tell the user.

- If the manifest cannot be recorded, the result is `blocked` / `batch_manifest_not_recorded`.
- If the folder cannot be written, the result is `blocked` / `output_delivery_failed` and the manifest is voided.
- In both cases no contents are returned, the orders stay pending, and the next run carries them.

## 3. Upload and acknowledge (agent)

1. Upload every file in `delivery.directory` (all files in `delivery.files`) to Type with `upload_file`.
2. Call the skill with `mode: "export"`, the same config and connections, and `acknowledgeBatch: { batchId, contentSha256 }`.
3. The acknowledgement is accepted only if all of these hold:
   - the manifest exists and is still `prepared`;
   - `contentSha256` matches;
   - every order of the batch is still pending in exactly this batch with the recorded row hash.

   The commit is a single compare-and-swap of the manifest to `acknowledged`. After it, each order is recorded `exported`. If an order write fails, the order is completed by the next run, or by repeating the acknowledgement, which is idempotent.
4. Results:
   - `acknowledged`: tell the user the batch is ready to import. The optional Slack summary is sent now, at most once per batch.
   - `batch_superseded`, `batch_void`, `batch_not_latest_for_orders` or `acknowledgement_content_mismatch`: do not hand over these files. Export again, then upload and acknowledge the newest batch.
   - A tool error or timeout of the acknowledgement call, `acknowledgement_not_recorded`, `batch_manifest_unavailable` or `journal_unavailable`: the outcome is unknown (the commit may be applied). Repeat the identical acknowledgement until it returns `acknowledged` or a definitive refusal (any other `blocked` reason, such as `batch_superseded`, `batch_void`, `batch_not_latest_for_orders`, `acknowledgement_content_mismatch`, `batch_not_found` or `batch_manifest_invalid`). Do not tell the user the batch was not acknowledged before then. If the agent stops anyway, the next export or poll reports the batch in `acknowledgedBatchesCompleted`.

**Carried and superseded batches:** while a batch is unacknowledged, every new export re-renders its orders. The new `summary.md` says "This batch supersedes unacknowledged batch X — import only the newest batch" and lists each carried order with its earlier batch ID. "(rows differ …)" marks an order whose rows changed since that batch.

**Pending backlog:** at most 200 pending orders are allowed, and every new batch carries all of them.

- When the backlog is full, new orders go to durable review (`unacknowledged_backlog_full`), and polling still advances. Acknowledge the newest batch, then rerun those orders with `reexportOrderIds`.
- An order that cannot be carried in a run stays pending and is listed under "Pending orders not in this batch". This happens if it cannot be re-read (`pending_order_read_failed`) or if the planner budget runs out. After 3 failed reads it goes to review.

## 4. Import in QuickBooks Online (human, after acknowledgement)

Follow the numbered steps in that batch's `summary.md`. Use only the files of the newest acknowledged batch, and import each batch once. In order:

1. **Customers.** If `qbo-customers-import.csv` exists: Settings ⚙ → Import data → Customers → upload → map columns → import. Or tick "add new customers" during the invoice import.
2. **Invoices.** Settings ⚙ → Import data → Invoices.
   - Upload each `qbo-invoices-import*.csv` and map the columns (Not applicable for unused fields).
   - Choose the date format shown in the summary.
   - Non-US: choose Exclusive and map the zero-rated tax code.
   - Start import. If some invoices fail, note why and fix them manually; do not re-export them.
3. **Sales receipts** (`sales-receipts-worksheet.csv`): + New → Sales receipt, one per `SalesReceiptNo`, with the lines as listed. Check the receipt total.
4. **Payments** (`payments-to-record.csv`, `invoice_then_payment` only): after the invoices exist, + New → Receive payment for each row.
5. **Purchase orders** (`supplier-po-worksheet.csv`): + New → Purchase order, one per vendor. A `VendorStatus` other than `matched` means create or fix the vendor first. Nothing is sent to suppliers.
6. Work through `review-queue.csv`. Each row has an action.

Do not open the CSVs in a spreadsheet and re-save them before importing.

## 5. Confirm

Rerun later (same window, or the next poll) with QuickBooks connected. For each exported invoice awaiting confirmation, the skill looks up QuickBooks invoices by DocNumber:

- Same DocNumber, total and customer name: `confirmed_posted`, with the QuickBooks invoice ID.
- Same DocNumber but a different total or customer: review `posted_invoice_mismatch`. More than one invoice with that DocNumber: `duplicate_docnumber_in_qbo`.
- Not seen: still `awaiting_confirmation`. An acknowledged order is **never re-rendered automatically**, whatever the list shows. Only `reexportOrderIds` can export it again.
- A pending (unacknowledged) order that QuickBooks already shows (someone imported an unacknowledged batch) is recorded `confirmed_posted` and is not rendered again.

**The invoice list is advisory.**

- *Verified paging.* Each page starts 5 rows before the end of the previous one, and those rows must come back identical and in the same positions. If the list changes between pages (an insert or delete), or the tool ignores `startPosition` or repeats an Id, the read is retried once. If it is still inconsistent, the run is blocked with `qbo_<entity>_paging_inconsistent` and nothing is recorded. If a page is too short to give the next page the full 5-row overlap (the tool returns 5 rows or fewer per page) and more rows follow, that boundary cannot be verified, so the list is incomplete (`posted_status_unknown` for new invoices). Residual limit: a delete and an insert before the same page boundary between two page reads leave the overlap rows unchanged, so the inserted row can be missed; this needs concurrent QuickBooks edits during the run and is not detected.
- *Page limit.* A list longer than `qboLimits.maxInvoicePages` is incomplete. For orders not yet exported, an incomplete list gives `posted_status_unknown` review. Raise the limit (up to 1,000 pages) and rerun.
- *Targeted read.* An order the journal records as posted (with a QuickBooks invoice ID), but whose invoice the list did not return, is checked with `quickbooks_get_entity`. If the invoice exists, the list skipped rows, so this run treats it as incomplete: new invoices go to `posted_status_unknown` and are not exported.

Sales receipts and POs cannot be confirmed, because there is no read tool for them. Once acknowledged they stay `exported` and are never exported again.

## Re-export and changes

- **Already exported (acknowledged):** the order is skipped (`already_exported`). Only `reexportOrderIds` exports it again, and only after the user confirms the earlier rows were not imported. The re-exported order is pending again until its new batch is acknowledged.
- **Re-export is refused** (`reexport_blocked_already_in_qbo`) when the journal records the invoice as posted, whether or not QuickBooks is connected, and when QuickBooks currently shows it.
- **Changes after export:** if an exported order's money, lines, email or date change, it goes to `changed_after_export`. A refund or cancellation after export goes to `refund_or_cancellation_after_export`. Fix QuickBooks manually.
- **Already in QuickBooks before export:** an order never exported that QuickBooks already shows is recorded `confirmed_posted`. If it later changes, it goes to `changed_after_posted_in_qbo`.
- **Payment captured later:** an unpaid order exported as an invoice that is later paid in BigCommerce shows `payment_captured_after_invoice_export` once. Record + New → Receive payment against that invoice.

## Failures

- **`blocked` results** export nothing. Read the `reason`, fix the cause and rerun.
- **Polling cursor.** Polling advances only when the batch reports `cursorAdvanceSafe: true`, meaning every order in the window has a durable journal record (pending, review, exported, …). This does not depend on acknowledgement: pending orders are carried by the index.
- **Journal failures.** A journal read or write failure, or an unreadable batch state (`batch_state_unknown`: the manifest could not be read or no longer parses), holds the cursor. An order record that no longer parses is never overwritten and never rendered as new: the order is reported as `journal_record_invalid` on every run that sees it.
- **Orders that can never be read safely** get a durable `needs_review` record, and the window advances:
  - more than 500 products (`order_exceeds_import_line_limit`);
  - missing products (`order_products_not_found`);
  - an invalid creation date (`order_creation_date_invalid`);
  - planner input over 2 MiB for one order (`order_too_large_for_planner`);
  - a pending order that no longer exists (`pending_order_not_found`).
- **Products-read failures.** Other products-read failures (`products_read_failed`) hold the window. After 3 failing runs a new order is parked as `products_read_failed_repeatedly`. An exported or posted order keeps its record and fingerprint: the failure is only reported, and after 3 runs it no longer holds the window.
- **Planner budget.** Planner input is limited to the fields the planner reads, at most 64 MiB per run. If a full page does not fit, the page ends early and `nextAfterOrderId` continues from the last planned order (warning `planner_budget_page_shortened`).
- **Concurrency.** Run one poller per store, and do not run exports and acknowledgements for the same store at the same time. They are serialized by compare-and-swap, but concurrent runs make batches supersede each other.
- Never delete journal, batch or checkpoint records to force progress.
