import { createHash } from "node:crypto";
import type { Config } from "./config";

export type Line = {
  type: "product" | "shipping" | "discount"; sku: string; item: string; description: string;
  qty: number; rateCents: number; amountCents: number;
};
export type PurchaseLine = {
  vendor: string; vendorStatus: "matched" | "unverified" | "not_found_in_qbo" | "ambiguous_in_qbo";
  sku: string; item: string; qty: number; unitCostCents: number; amountCents: number;
};
export type CustomerDetails = {
  company: string; email: string; phone: string; street: string; city: string; state: string; zip: string; country: string;
};
export type PlannedOrder = {
  orderId: string; status: "planned"; kind: "invoice" | "sales_receipt" | "invoice_then_payment";
  docNumber: string; date: string; currency: string; totalCents: number;
  customer: { name: string; match: "matched" | "new" | "unverified"; qboId?: string; details?: CustomerDetails };
  lines: Line[]; purchaseLines: PurchaseLine[];
};
export type ReviewItem = { orderId: string | null; reason: string };
export type OutputFile = { name: string; content: string; bytes: number; sha256: string; rows: number };
export type BatchCounts = {
  /** Orders whose rows are in this batch's files (pending acknowledgement). */
  prepared: number;
  /** Of those, orders carried from earlier unacknowledged batches. */
  carried: number;
  /** Pending orders not in this batch (still pending; they go into a later batch). */
  notCarried: number;
  alreadyExported: number; alreadyPosted: number; confirmedPosted: number;
  awaitingConfirmation: number; review: number; paymentFollowUps: number;
};
export type CarriedOrder = { orderId: string; previousBatchId: string; sameRows: boolean };
export type BatchMeta = {
  batchId: string; mode: "preview" | "export"; since: string; until: string; timezone: string;
  qbo: { connected: boolean; customerLookup: string; postedCheck: "complete" | "incomplete" | "not_connected"; warnings: string[] };
  counts: BatchCounts;
  carried: CarriedOrder[];
  /** Unacknowledged batches whose orders this batch re-renders. They can no longer be acknowledged. */
  supersedes: string[];
  notCarried: string[];
  /** Acknowledged batches whose orders this run recorded as exported (their files are ready to import). */
  acknowledgedCompleted?: string[];
};
/** Every preview file name starts with this, so preview output can never be mistaken for an import batch. */
export const PREVIEW_PREFIX = "PREVIEW-DO-NOT-IMPORT-";

/** Human actions for each review reason. Unknown reasons fall back to a generic action. */
export const REVIEW_ACTIONS: Record<string, string> = {
  refund_or_cancellation_requires_reconciliation: "Refunded or cancelled in BigCommerce. Record any credit memo or refund in QuickBooks manually.",
  product_refund_requires_reconciliation: "A product was refunded. Record the adjustment in QuickBooks manually.",
  refund_or_cancellation_after_export: "Refunded or cancelled after export or posting. Adjust the QuickBooks transaction manually.",
  changed_after_posted_in_qbo: "Order changed after QuickBooks already had this invoice. Compare the order with the QuickBooks invoice and adjust QuickBooks manually.",
  posted_status_unknown: "The QuickBooks invoice list was incomplete (beyond qboLimits.maxInvoicePages, or it missed an invoice found by a targeted read), so this invoice may already exist. Check QuickBooks for this number or raise maxInvoicePages, then rerun; nothing was exported.",
  reexport_blocked_already_in_qbo: "QuickBooks already has this invoice (recorded in the journal or found by number, total and customer). It is never re-exported.",
  payment_captured_after_invoice_export: "Payment was captured in BigCommerce after the unpaid invoice was exported. In QuickBooks use + New → Receive payment against this invoice number. Shown once.",
  new_customer_email_conflict: "Orders in this batch share a buyer email but derive different customer names. Create or pick one QuickBooks customer, then rerun.",
  order_exceeds_import_line_limit: "Order has more than 500 products, beyond what this skill imports safely. Enter it manually. Polling continues past it.",
  order_creation_date_invalid: "BigCommerce returned no valid creation date for this order. Check it in BigCommerce and enter it manually. Polling continues past it.",
  order_products_not_found: "BigCommerce returned no products for this order (deleted or unavailable). Check it in BigCommerce. Polling continues past it.",
  products_read_failed: "Order products could not be read this run. The window is retried; after 3 failed runs the order is parked for review (an exported order keeps its export record).",
  products_read_failed_repeatedly: "Order products failed to read in 3 runs. Check the order in BigCommerce, then rerun with reexportOrderIds or enter it manually. Polling continues past it.",
  pending_order_not_found: "A pending (unacknowledged) order could not be re-read from BigCommerce. Check the order and enter it manually if needed; do not import it from an older batch.",
  pending_order_read_failed: "A pending (unacknowledged) order could not be re-read this run, so it is not in this batch. It stays pending and is retried next run.",
  order_too_large_for_planner: "Order data is too large to plan safely in one run. Enter it manually. Polling continues past it.",
  unacknowledged_backlog_full: "Too many orders are waiting for a batch acknowledgement. Upload and acknowledge the newest batch, then rerun with reexportOrderIds for this order.",
  batch_state_unknown: "The state of the batch holding this order could not be read, or its stored record is unreadable. Nothing was changed for this order; rerun. If it persists, investigate the batch record; do not delete records.",
  reexport_deferred_batch_size: "This run's planner budget was used by other orders, so the re-export was not prepared. Rerun the re-export.",
  pending_index_update_failed: "Run state could not be updated, so nothing new was prepared. Rerun.",
  journal_record_invalid: "This order's run-state record is unreadable. Investigate before exporting; do not delete records.",
  changed_after_export: "Order changed after its import file was produced. Compare with QuickBooks; rerun with reexportOrderIds only if the old rows were never imported.",
  taxable_order_requires_manual_entry: "Order has tax. QuickBooks invoice import cannot carry it safely; enter it manually.",
  currency_not_approved: "Order currency differs from the configured currency (FX). Enter manually.",
  customer_currency_mismatch: "QuickBooks customer uses a different currency. Enter manually.",
  ambiguous_customer_email: "More than one QuickBooks customer has this email. Merge or pick the customer, then rerun.",
  ambiguous_customer_name: "More than one QuickBooks customer has this display name. Rename one, then rerun.",
  customer_name_collision: "A different QuickBooks customer already uses the derived name. Add the buyer manually, then rerun.",
  new_customer_name_conflict: "Two buyers in this batch derive the same customer name. Add customers manually, then rerun.",
  customer_not_found: "Buyer is not a QuickBooks customer and new customers are disabled. Add the customer, then rerun.",
  customer_name_requires_review: "Customer name is empty or unsafe for an exact-match import. Fix the name, then rerun.",
  inactive_customer: "Matching QuickBooks customer is inactive. Reactivate or choose another customer.",
  qbo_customer_list_incomplete: "QuickBooks customer list exceeded the paging limit, so matching is unknown. Raise qboLimits.maxCustomerPages or enter manually.",
  item_mapping_required: "A SKU has no QuickBooks product/service mapping. Add it to config.itemMap, then rerun.",
  shipping_item_mapping_required: "Set config.shippingItemName to an existing QuickBooks product/service.",
  discount_not_supported_by_invoice_import: "QuickBooks invoice import cannot carry discounts. Enter this invoice manually.",
  supplier_cost_or_currency_requires_review: "Supplier cost is unverified or in another currency. Fix config.suppliers, then rerun.",
  unsupported_payment_status: "Payment status is neither unpaid nor captured. Review the payment in BigCommerce.",
  ineligible_order_status: "Order status is not in config.eligibleStatusIds.",
  docnumber_already_used_in_qbo: "QuickBooks already has this invoice number with different details. Investigate before importing.",
  duplicate_docnumber_in_qbo: "QuickBooks has more than one invoice with this number. Investigate duplicates.",
  posted_invoice_mismatch: "The QuickBooks invoice with this number differs in total or customer. Investigate.",
  confirmation_backlog_full: "Too many orders await acknowledgement or QuickBooks confirmation. Import and confirm earlier batches first, then rerun with reexportOrderIds for this order.",
  journal_read_failed: "Run state could not be read. Rerun later; nothing was exported for this order.",
  order_claim_conflict: "This order's run state changed during the run (or could not be written). It is not in this batch; rerun.",
  reexport_order_not_found: "Requested re-export order could not be read from BigCommerce.",
};

const CONTROL = /[\u0000-\u001f\u007f-\u009f\p{Zl}\p{Zp}]/gu;
/**
 * The only phone form kept raw when it starts with "+": strict E.164, "+" then 2-15 digits and nothing else. A
 * spreadsheet reads it as a plain number at worst. Any other leading = + - @ (e.g. "+1-555-0100", "+1/1",
 * "+1 (555) 123-4567") could be evaluated as arithmetic, so it is prefixed with ' like every other text cell.
 */
const E164 = /^\+[1-9][0-9]{1,14}$/;
/** Text cells: strip control characters and neutralize spreadsheet formulas. */
export function textCell(value: string): string {
  const cleaned = value.replace(CONTROL, " ");
  return /^[=+\-@\t\r]/.test(value) || /^[=+\-@]/.test(cleaned.trimStart()) ? "'" + cleaned : cleaned;
}
function quote(cell: string): string {
  return /[",\r\n]/.test(cell) || cell !== cell.trim() ? `"${cell.replace(/"/g, '""')}"` : cell;
}
export type Cell = { text: string } | { number: string } | { phone: string };
const t = (text: string): Cell => ({ text });
const n = (number: string): Cell => ({ number });
/**
 * Phone cells: a phone that is strict E.164 ("+" then 7-15 digits) once spaces, dashes, dots and parentheses are
 * removed is written in that normalized form; otherwise strict E.164 is kept exactly and everything else follows the
 * text-cell rule (see E164).
 */
export function phoneCell(value: string): string {
  const normalized = value.replace(/[ .()-]/g, "");
  if (/^\+[1-9][0-9]{6,14}$/.test(normalized)) return normalized;
  return E164.test(value) ? value : textCell(value);
}
export function money(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return sign + Math.floor(abs / 100) + "." + String(abs % 100).padStart(2, "0");
}
export function csv(header: string[], rows: Cell[][]): string {
  const render = (cell: Cell) => quote("text" in cell ? textCell(cell.text) : "phone" in cell ? phoneCell(cell.phone) : cell.number);
  return [header.map((h) => quote(h)), ...rows.map((row) => row.map(render))].map((r) => r.join(",")).join("\r\n") + "\r\n";
}
export function formatDate(iso: string, format: Config["dateFormat"], addDays = 0): string {
  const [y = 0, m = 1, d = 1] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + addDays));
  const yyyy = String(date.getUTCFullYear()).padStart(4, "0");
  const mm = String(date.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(date.getUTCDate()).padStart(2, "0");
  return format === "MM/DD/YYYY" ? `${mm}/${dd}/${yyyy}` : format === "DD/MM/YYYY" ? `${dd}/${mm}/${yyyy}` : `${yyyy}-${mm}-${dd}`;
}
const byOrder = (a: { orderId: string | null }, b: { orderId: string | null }) => {
  const x = BigInt(a.orderId ?? "0"), y = BigInt(b.orderId ?? "0");
  return x < y ? -1 : x > y ? 1 : 0;
};
export function makeFile(name: string, content: string, rows: number): OutputFile {
  const bytes = new TextEncoder().encode(content);
  return { name, content, bytes: bytes.length, rows, sha256: createHash("sha256").update(bytes).digest("hex") };
}

// QuickBooks import limits: fewer than 1,000 spreadsheet rows and at most 100 invoices per file.
const MAX_DATA_ROWS = 998, MAX_INVOICES = 100;

function invoiceFiles(config: Config, orders: PlannedOrder[]): OutputFile[] {
  const nonUs = config.region === "non_US";
  const header = ["InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Terms", "Memo", "Item(Product/Service)",
    "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount", ...(nonUs ? ["ItemTaxCode", "ItemTaxAmount"] : [])];
  const chunks: Cell[][][] = [];
  let current: Cell[][] = [], invoices = 0;
  for (const order of orders) {
    const rows = order.lines.map((line) => [
      t(order.docNumber), t(order.customer.name), n(formatDate(order.date, config.dateFormat)),
      n(formatDate(order.date, config.dateFormat, config.termsDays)), t(config.termsName ?? ""),
      t("BigCommerce order " + order.orderId), t(line.item), t(line.description), n(String(line.qty)),
      n(money(line.rateCents)), n(money(line.amountCents)),
      ...(nonUs ? [t(config.zeroTaxCodeName ?? ""), n("0.00")] : []),
    ]);
    if (current.length && (current.length + rows.length > MAX_DATA_ROWS || invoices === MAX_INVOICES)) {
      chunks.push(current); current = []; invoices = 0;
    }
    current.push(...rows); invoices++;
  }
  if (current.length) chunks.push(current);
  return chunks.map((rows, index) => makeFile(
    index === 0 ? "qbo-invoices-import.csv" : `qbo-invoices-import-part${index + 1}.csv`, csv(header, rows), rows.length));
}

/** Pure, deterministic rendering of import files, worksheets, review queue and summary. */
export function renderBatch(config: Config, exported: PlannedOrder[], reviews: ReviewItem[], meta: BatchMeta): OutputFile[] {
  const files = renderFiles(config, exported, reviews, meta);
  if (meta.mode === "export") return files;
  return files.map((f) => ({ ...f, name: PREVIEW_PREFIX + f.name }));
}

function renderFiles(config: Config, exported: PlannedOrder[], reviews: ReviewItem[], meta: BatchMeta): OutputFile[] {
  const orders = [...exported].sort(byOrder);
  const files: OutputFile[] = [];
  const invoiceOrders = orders.filter((o) => o.kind !== "sales_receipt");
  if (invoiceOrders.length) files.push(...invoiceFiles(config, invoiceOrders));

  const newCustomers = new Map<string, PlannedOrder["customer"]>();
  if (config.allowNewCustomers)
    for (const order of orders)
      if (order.customer.match !== "matched" && order.customer.details && !newCustomers.has(order.customer.name.toLowerCase()))
        newCustomers.set(order.customer.name.toLowerCase(), order.customer);
  if (newCustomers.size) {
    const rows = [...newCustomers.values()].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)).map((c) => {
      const d = c.details as CustomerDetails;
      return [t(c.name), t(d.company), t(d.email), { phone: d.phone }, t(""), t(d.street), t(d.city), t(d.state), t(d.zip), t(d.country)];
    });
    files.push(makeFile("qbo-customers-import.csv", csv(["Customer", "Company name", "Email", "Phone", "Website", "Billing Street",
      "Billing City", "Billing State", "Billing Zip", "Billing Country"], rows), rows.length));
  }

  const receipts = orders.filter((o) => o.kind === "sales_receipt");
  if (receipts.length) {
    const nonUs = config.region === "non_US";
    const rows = receipts.flatMap((order) => order.lines.map((line) => [
      t(order.docNumber), t(order.customer.name), n(formatDate(order.date, config.dateFormat)),
      t(config.paymentMethodName ?? ""), t(config.depositAccountName ?? ""), t(line.type),
      t(line.item), t(line.description), n(String(line.qty)), n(money(line.rateCents)), n(money(line.amountCents)),
      ...(nonUs ? [t(line.type === "discount" ? "" : config.zeroTaxCodeName ?? "")] : []),
      t("BigCommerce order " + order.orderId), n(money(order.totalCents)),
    ]));
    files.push(makeFile("sales-receipts-worksheet.csv", csv(["SalesReceiptNo", "Customer", "SalesReceiptDate", "PaymentMethod",
      "DepositTo", "LineType", "Item(Product/Service)", "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount",
      ...(nonUs ? ["ItemTaxCode"] : []), "Memo", "ReceiptTotal"], rows), rows.length));
  }

  const payments = orders.filter((o) => o.kind === "invoice_then_payment");
  if (payments.length) {
    const rows = payments.map((order) => [
      t(order.docNumber), t(order.customer.name), n(formatDate(order.date, config.dateFormat)), n(money(order.totalCents)),
      t(config.paymentMethodName ?? ""), t(config.depositAccountName ?? ""), t(order.docNumber),
      t("BigCommerce order " + order.orderId + " payment"),
    ]);
    files.push(makeFile("payments-to-record.csv", csv(["InvoiceNo", "Customer", "PaymentDate", "Amount", "PaymentMethod",
      "DepositTo", "ReferenceNo", "Memo"], rows), rows.length));
  }

  const purchases = orders.flatMap((o) => o.purchaseLines.map((line) => ({ ...line, orderId: o.orderId, currency: o.currency })));
  if (purchases.length) {
    const vendors = [...new Set(purchases.map((p) => p.vendor))].sort();
    const rows: Cell[][] = [];
    for (const vendor of vendors) {
      const lines = purchases.filter((p) => p.vendor === vendor).sort((a, b) => byOrder(a, b) || (a.sku < b.sku ? -1 : a.sku > b.sku ? 1 : 0));
      const first = lines[0];
      if (!first) continue;
      for (const p of lines)
        rows.push([t(vendor), t(p.vendorStatus), t("Line"), t(p.orderId), t(p.item), t(p.sku), n(String(p.qty)),
          n(money(p.unitCostCents)), n(money(p.amountCents)), t(p.currency)]);
      rows.push([t(vendor), t(first.vendorStatus), t("Subtotal"), t(""), t(""), t(""), n(""), n(""),
        n(money(lines.reduce((sum, p) => sum + p.amountCents, 0))), t(first.currency)]);
    }
    files.push(makeFile("supplier-po-worksheet.csv", csv(["Vendor", "VendorStatus", "LineType", "BigCommerceOrder",
      "Item(Product/Service)", "SKU", "Quantity", "UnitCost", "Amount", "Currency"], rows), rows.length));
  }

  const queue = [...reviews].sort((a, b) => byOrder(a, b) || (a.reason < b.reason ? -1 : a.reason > b.reason ? 1 : 0));
  const action = (reason: string) => REVIEW_ACTIONS[reason] ?? "Review this order in BigCommerce and QuickBooks.";
  files.push(makeFile("review-queue.csv", csv(["OrderId", "Reason", "Action"],
    queue.map((r) => [t(r.orderId ?? ""), t(r.reason), t(action(r.reason))])), queue.length));
  files.push(makeFile("review-queue.json", JSON.stringify(queue.map((r) => ({ ...r, action: action(r.reason) })), null, 2) + "\n", queue.length));

  const c = meta.counts;
  const shown = (name: string) => (meta.mode === "preview" ? PREVIEW_PREFIX : "") + name;
  const listed = files.filter((f) => !f.name.startsWith("review-queue")).map((f) => `- \`${shown(f.name)}\`: ${f.rows} rows`);
  const totalOf = (list: PlannedOrder[]) => money(list.reduce((sum, o) => sum + o.totalCents, 0));
  const steps: string[] = [];
  if (newCustomers.size)
    steps.push("Customers first: Settings ⚙ → Import data → Customers, upload `qbo-customers-import.csv` (or tick the add-new-customers box during the invoice import).");
  if (invoiceOrders.length)
    steps.push("Invoices: in QuickBooks Online go to Settings ⚙ → Import data → Invoices, upload each `qbo-invoices-import*.csv`, map the columns, choose date format " + config.dateFormat + ", then Start import."
      + (config.region === "US"
        ? " QuickBooks (US) does not allow this import when sales tax is set up in the company; if so, enter these invoices manually from the file."
        : " Choose the Exclusive VAT/tax option and map the tax code column to your zero-rated code."));
  if (receipts.length) steps.push("Sales receipts: enter each receipt in `sales-receipts-worksheet.csv` with + New → Sales receipt.");
  if (payments.length) steps.push("Payments: after importing invoices, record each row of `payments-to-record.csv` with + New → Receive payment.");
  if (purchases.length) steps.push("Purchase orders: create one PO per vendor from `supplier-po-worksheet.csv` with + New → Purchase order.");
  steps.push("Work through `review-queue.csv`.");
  if (meta.mode === "export" && exported.length) steps.unshift(`Before importing: confirm batch ${meta.batchId} was acknowledged. If a newer batch exists, use only the newest one.`);
  steps.push("Rerun the skill later; invoices it finds in QuickBooks by number, total and customer are marked confirmed_posted.");
  const carried = [...meta.carried].sort(byOrder);
  const preview = meta.mode === "preview";
  const header = preview ? [
    `# PREVIEW — DO NOT IMPORT (${meta.batchId})`,
    "",
    `Every file in this folder starts with \`${PREVIEW_PREFIX}\`. Nothing was recorded, so importing these files and later exporting the same orders would create duplicates in QuickBooks. Rerun with mode=export to get an import batch.`,
  ] : [
    `# BigCommerce → QuickBooks import batch ${meta.batchId}`,
    "",
    `Status: prepared, pending upload and acknowledgement. Import these files only after batch ${meta.batchId} has been acknowledged (the agent uploads every file in this folder to Type, then acknowledges the batch). Import only the newest batch, once; never import files from any other batch folder.`,
  ];
  const summary = [
    ...header,
    "",
    ...(meta.acknowledgedCompleted ?? []).flatMap((id) => [
      `Batch ${id} was acknowledged — its files are ready to import (its orders are not in this batch).`, ""]),
    ...(meta.supersedes.length ? [
      `This batch supersedes unacknowledged batch ${meta.supersedes.join(", ")} — import only the newest batch. Superseded batches can no longer be acknowledged; delete their files.`,
      "",
    ] : []),
    `Mode: ${meta.mode}${preview ? " (nothing recorded; rerun with mode=export to track these orders)" : ""}`,
    `Window: ${meta.since} → ${meta.until} (${meta.timezone})`,
    `QuickBooks read-only check: ${meta.qbo.connected ? `connected; customer lookup ${meta.qbo.customerLookup}; posted-invoice check ${meta.qbo.postedCheck}` : "not connected; customers unverified and posting not confirmed"}`,
    ...meta.qbo.warnings.map((w) => `Warning: ${w}`),
    "",
    "| Outcome | Orders |",
    "| --- | ---: |",
    `| In this batch's files / worksheets | ${c.prepared} |`,
    `| of which carried from unacknowledged batches | ${c.carried} |`,
    `| Pending, not in this batch (next batch) | ${c.notCarried} |`,
    `| Already exported earlier (skipped) | ${c.alreadyExported} |`,
    `| Already in QuickBooks (skipped) | ${c.alreadyPosted} |`,
    `| Confirmed posted this run | ${c.confirmedPosted} |`,
    `| Exported, awaiting confirmation | ${c.awaitingConfirmation} |`,
    `| Needs review | ${c.review} |`,
    `| Payment captured after invoice export (record payment) | ${c.paymentFollowUps} |`,
    "",
    `Invoice total ${config.currency} ${totalOf(invoiceOrders)}; sales receipt total ${config.currency} ${totalOf(receipts)}.`,
    "",
    ...(carried.length ? [
      "## Carried from unacknowledged batches",
      "These orders were in an earlier batch that was never acknowledged. Their current rows are in this batch.",
      ...carried.map((r) => `- Order ${r.orderId}: earlier batch ${r.previousBatchId}${r.sameRows ? "" : " (rows differ from the earlier batch)"}`),
      "",
    ] : []),
    ...(meta.notCarried.length ? [
      "## Pending orders not in this batch",
      "These orders are still pending and will be in a later batch. Do not import them from an older batch.",
      `- ${meta.notCarried.join(", ")}`,
      "",
    ] : []),
    "## Files",
    ...(listed.length ? listed : ["- No import files in this batch."]),
    `- \`${shown("review-queue.csv")}\`: ${queue.length} rows`,
    "",
    "## Next steps",
    ...(preview ? [
      "1. Check these files and the review queue. Fix configuration and rerun the preview until the result is right.",
      "2. Never import preview files. Rerun with mode=export to get an import batch, upload it, acknowledge it, then import that batch.",
    ] : steps.map((s, i) => `${i + 1}. ${s}`)),
    "",
    "This run made no QuickBooks or BigCommerce changes.",
    "",
  ].join("\n");
  files.push(makeFile("summary.md", summary, 0));
  return files;
}
