import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { configSchema } from "./config";
import { fixturePorts, fixtureSchema, previewFixture, type Fixture } from "./preview";
import { runAcknowledge } from "./ack";
import { csv, PREVIEW_PREFIX, renderBatch, textCell, type PlannedOrder } from "./render";
import { runSync, type Ports } from "./sync";

const base: Fixture = fixtureSchema.parse(JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8")));
const clone = () => structuredClone(base);
/** Preview files carry the PREVIEW-DO-NOT-IMPORT- prefix; lookups accept either form. */
const fileOf = (result: { files: { name: string; content: string }[] }, name: string) =>
  result.files.find((f) => f.name === name || f.name === PREVIEW_PREFIX + name)?.content;
const names = (result: { files: { name: string }[] }) => result.files.map((f) => f.name);
type Journal = Map<string, { value: unknown; version: string }>;
type Batch = Extract<Awaited<ReturnType<typeof runSync>>, { batchId: string }>;
async function run(fixture: Fixture, mode: "preview" | "export", journal: Journal = new Map(), extra: { reexportOrderIds?: string[] } = {},
  wrap?: (ports: Ports) => Ports) {
  const { ports } = fixturePorts(fixture, journal);
  const result = await runSync({ config: fixture.config, mode, ...fixture.window, ...extra }, wrap ? wrap(ports) : ports);
  // Tests inspect both completed and blocked outcomes; blocked results simply lack the batch fields.
  return { result: result as Batch & { reason?: string }, journal };
}
/** Export, then acknowledge the batch as the running agent does after uploading it: orders become exported. */
async function exportAcked(fixture: Fixture, journal: Journal = new Map(), extra: { reexportOrderIds?: string[] } = {}) {
  const prepared = await run(fixture, "export", journal, extra);
  const { ports } = fixturePorts(fixture, journal);
  const acknowledge = prepared.result.acknowledge;
  const acknowledged = acknowledge ? await runAcknowledge({ config: fixture.config, ...acknowledge }, ports) : null;
  return { ...prepared, acknowledged };
}
const order = (f: Fixture, id: number) => f.bigcommerce.orders.find((o) => o.id === id) as Record<string, any>;
const invoice = (doc: string, total: number, customer: string, id = "500") =>
  ({ Id: id, DocNumber: doc, TotalAmt: total, Balance: total, CustomerRef: { value: "1", name: customer } });

describe("rendered import files for the fixture", () => {
  test("exact bytes: invoices, customers, sales receipt worksheet, supplier POs, review queue", async () => {
    const { result } = await previewFixture(base);
    expect(result.status).toBe("preview");
    expect(names(result)).toEqual(["qbo-invoices-import.csv", "qbo-customers-import.csv", "sales-receipts-worksheet.csv",
      "supplier-po-worksheet.csv", "review-queue.csv", "review-queue.json", "summary.md"].map((n) => PREVIEW_PREFIX + n));
    expect(fileOf(result, "qbo-invoices-import.csv")).toBe(
      "InvoiceNo,Customer,InvoiceDate,DueDate,Terms,Memo,Item(Product/Service),ItemDescription,ItemQuantity,ItemRate,ItemAmount\r\n"
      + "BC-1002,Pat Sample,10/16/2026,11/15/2026,Net 30,BigCommerce order 1002,Gadget,\"'=Gadget \"\"deluxe\"\"\",1,15.50,15.50\r\n"
      + "BC-1002,Pat Sample,10/16/2026,11/15/2026,Net 30,BigCommerce order 1002,Shipping,Shipping,1,4.50,4.50\r\n");
    expect(fileOf(result, "qbo-customers-import.csv")).toBe(
      "Customer,Company name,Email,Phone,Website,Billing Street,Billing City,Billing State,Billing Zip,Billing Country\r\n"
      + "Pat Sample,Sample Studio,pat@example.test,555-0100,,\"2 Sample Road, Unit 3\",Sampleton,NY,10001,US\r\n");
    expect(fileOf(result, "sales-receipts-worksheet.csv")).toBe(
      "SalesReceiptNo,Customer,SalesReceiptDate,PaymentMethod,DepositTo,LineType,Item(Product/Service),ItemDescription,ItemQuantity,ItemRate,ItemAmount,Memo,ReceiptTotal\r\n"
      + "BC-1001,Example Buyer,10/16/2026,Credit Card,Undeposited Funds,product,Widget,\"Widget, large\",2,10.00,20.00,BigCommerce order 1001,22.00\r\n"
      + "BC-1001,Example Buyer,10/16/2026,Credit Card,Undeposited Funds,shipping,Shipping,Shipping,1,5.00,5.00,BigCommerce order 1001,22.00\r\n"
      + "BC-1001,Example Buyer,10/16/2026,Credit Card,Undeposited Funds,discount,,Order discount,1,-3.00,-3.00,BigCommerce order 1001,22.00\r\n");
    expect(fileOf(result, "supplier-po-worksheet.csv")).toBe(
      "Vendor,VendorStatus,LineType,BigCommerceOrder,Item(Product/Service),SKU,Quantity,UnitCost,Amount,Currency\r\n"
      + "Example Supply Co,matched,Line,1001,Widget,WIDGET,2,5.00,10.00,USD\r\n"
      + "Example Supply Co,matched,Subtotal,,,,,,10.00,USD\r\n");
    expect(fileOf(result, "review-queue.csv")).toBe("OrderId,Reason,Action\r\n"
      + "1003,refund_or_cancellation_requires_reconciliation,Refunded or cancelled in BigCommerce. Record any credit memo or refund in QuickBooks manually.\r\n"
      + "1004,taxable_order_requires_manual_entry,Order has tax. QuickBooks invoice import cannot carry it safely; enter it manually.\r\n");
    const summary = fileOf(result, "summary.md") ?? "";
    expect(summary).toContain("This run made no QuickBooks or BigCommerce changes.");
    expect(summary).toStartWith("# PREVIEW — DO NOT IMPORT");
    expect(summary).not.toContain("Settings ⚙ → Import data → Invoices");
    const exported = await run(base, "export");
    expect(fileOf(exported.result, "summary.md")).toContain("Settings ⚙ → Import data → Invoices");
    expect(summary).not.toContain("example.test");
    expect(JSON.parse(fileOf(result, "review-queue.json") ?? "[]")).toHaveLength(2);
  });

  test("every money column reconciles to the order total to the cent", async () => {
    const { result } = await previewFixture(base);
    const sum = (content: string, doc: string, column: number) => content.trim().split("\r\n").slice(1)
      .map((row) => row.split(","))
      .filter((cells) => cells[0] === doc).reduce((total, cells) => total + Math.round(Number(cells.at(column)) * 100), 0);
    expect(sum(fileOf(result, "qbo-invoices-import.csv") ?? "", "BC-1002", -1)).toBe(2000);
    expect(sum((fileOf(result, "sales-receipts-worksheet.csv") ?? "").replace("\"Widget, large\"", "Widget large"), "BC-1001", -3)).toBe(2200);
  });

  test("files are UTF-8, CRLF-delimited, and free of raw control characters", async () => {
    const { result } = await previewFixture(base);
    for (const file of result.files.filter((f) => f.name.endsWith(".csv"))) {
      expect(new TextDecoder("utf-8", { fatal: true }).decode(new TextEncoder().encode(file.content))).toBe(file.content);
      expect(/[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f]/.test(file.content)).toBe(false);
      expect(file.content.endsWith("\r\n")).toBe(true);
    }
  });

  test("preview records nothing", async () => {
    const { journal } = await run(base, "preview");
    expect(journal.size).toBe(0);
  });
});

describe("formula injection and CSV safety", () => {
  test("text cells that could execute in a spreadsheet are neutralized", () => {
    expect(textCell("=1+1")).toBe("'=1+1");
    expect(textCell("+SUM(A1)")).toBe("'+SUM(A1)");
    expect(textCell("-2+3")).toBe("'-2+3");
    expect(textCell("@cmd")).toBe("'@cmd");
    expect(textCell("\t=1")).toBe("' =1");
    expect(textCell("\r=1")).toBe("' =1");
    expect(textCell(" =1")).toBe("' =1");
    expect(textCell("safe = text")).toBe("safe = text");
    expect(textCell("line\nbreak\u0000end")).toBe("line break end");
    const separators = String.fromCharCode(0x2028) + String.fromCharCode(0x2029);
    expect(textCell("a" + separators + "b")).toBe("a  b");
    expect(csv(["A"], [[{ text: "a,\"b\"" }], [{ number: "-3.00" }]])).toBe("A\r\n\"a,\"\"b\"\"\"\r\n-3.00\r\n");
  });

  test("hostile product names are neutralized in rendered output while identity columns stay exact", async () => {
    const f = clone();
    order(f, 1002).products[0].name = "@SUM(1+1)\u0007|cmd";
    const { result } = await run(f, "preview");
    const rows = (fileOf(result, "qbo-invoices-import.csv") ?? "").split("\r\n");
    expect(rows[1]).toBe("BC-1002,Pat Sample,10/16/2026,11/15/2026,Net 30,BigCommerce order 1002,Gadget,'@SUM(1+1) |cmd,1,15.50,15.50");
  });

  test("formula-like identity names are rejected by configuration instead of rewritten", () => {
    expect(configSchema.safeParse({ ...base.config, itemMap: { WIDGET: "=Widget" } }).success).toBe(false);
    expect(configSchema.safeParse({ ...base.config, shippingItemName: " Shipping" }).success).toBe(false);
    expect(configSchema.safeParse({ ...base.config, suppliers: { W: { vendorName: "+V", unitCostCents: 1, currency: "USD", verified: true } } }).success).toBe(false);
  });

  test("formula-like buyer names go to review with a safe counterpart still exported", async () => {
    const f = clone();
    order(f, 1002).billing_address.first_name = "=cmd";
    order(f, 1002).billing_address.last_name = "";
    order(f, 1002).billing_address.company = "";
    f.config.customerNameSource = "billing_name";
    const { result } = await run(f, "preview");
    expect(result.reviewQueue).toContainEqual({ orderId: "1002", reason: "customer_name_requires_review" });
    expect(fileOf(result, "sales-receipts-worksheet.csv")).toContain("BC-1001,Example Buyer");
  });
});

describe("journal: export, re-export prevention, changes after export", () => {
  test("export records each order once (after acknowledgement); a rerun does not re-export", async () => {
    const journal: Journal = new Map();
    const first = await exportAcked(base, journal);
    expect(first.result.status).toBe("prepared");
    expect(first.acknowledged).toMatchObject({ status: "acknowledged" });
    expect(first.result.cursorAdvanceSafe).toBe(true);
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ state: "exported", export: { docNumber: "BC-1002", exportCount: 1, totalCents: 2000 } });
    expect(journal.get("bcqbo/demo/order/1001")?.value).toMatchObject({ state: "exported", export: { kind: "sales_receipt" } });
    expect(journal.get("bcqbo/demo/order/1003")?.value).toMatchObject({ state: "needs_review", reason: "refund_or_cancellation_requires_reconciliation" });
    expect(journal.get("bcqbo/demo/pending")?.value).toEqual({ schema: 1, orderIds: ["1002"] });
    const second = await run(base, "export", journal);
    expect(names(second.result)).toEqual(["review-queue.csv", "review-queue.json", "summary.md"]);
    expect(second.result.counts).toMatchObject({ prepared: 0, alreadyExported: 2 });
    expect(second.result.acknowledge).toBeNull();
  });

  test("reexportOrderIds is the only way to export an order again", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const again = await exportAcked(base, journal, { reexportOrderIds: ["1002"] });
    expect(again.result.counts.prepared).toBe(1);
    expect(fileOf(again.result, "qbo-invoices-import.csv")).toContain("BC-1002,Pat Sample");
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ export: { exportCount: 2 } });
  });

  test("re-export requests outside the window are read by ID; unknown IDs go to review", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    f.window = { since: "2026-10-18T00:00:00Z", until: "2026-10-19T00:00:00Z", timezone: "UTC" };
    const again = await run(f, "export", journal, { reexportOrderIds: ["1002", "9999"] });
    expect(again.result.counts.prepared).toBe(1);
    expect(again.result.reviewQueue).toContainEqual({ orderId: "9999", reason: "reexport_order_not_found" });
  });

  test("a financial change after export goes to review instead of a second export", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    const o = order(f, 1002);
    o.products[0].price_ex_tax = "16.50"; o.products[0].total_ex_tax = "16.50";
    o.subtotal_ex_tax = "16.50"; o.total_ex_tax = "21.00"; o.total_inc_tax = "21.00";
    const after = await run(f, "export", journal);
    expect(after.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "changed_after_export" });
    expect(fileOf(after.result, "qbo-invoices-import.csv")).toBeUndefined();
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ state: "needs_review", export: { exportCount: 1 } });
    expect(journal.get("bcqbo/demo/pending")?.value).toEqual({ schema: 1, orderIds: [] });
  });

  test("refund or cancellation after export gets its own reason", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    order(f, 1002).status_id = 5;
    const after = await run(f, "export", journal);
    expect(after.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "refund_or_cancellation_after_export" });
  });

  test("fulfilment status or payment capture changes are not accounting changes", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    order(f, 1002).status_id = 2;
    const after = await run(f, "export", journal);
    expect(after.result.orders).toContainEqual({ orderId: "1002", outcome: "awaiting_confirmation" });
    expect(after.result.counts).toMatchObject({ prepared: 0, alreadyExported: 2, paymentFollowUps: 0 });
    expect(after.result.reviewQueue.some((r) => r.orderId === "1002")).toBe(false);
  });

  test("payment captured after an unpaid invoice was exported is reported once as a payment follow-up, never re-exported", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    order(f, 1002).payment_status = "captured";
    const after = await run(f, "export", journal);
    expect(after.result.orders).toContainEqual({ orderId: "1002", outcome: "awaiting_confirmation" });
    expect(after.result.counts).toMatchObject({ prepared: 0, alreadyExported: 2, paymentFollowUps: 1 });
    expect(after.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "payment_captured_after_invoice_export" });
    expect(fileOf(after.result, "review-queue.csv")).toContain("1002,payment_captured_after_invoice_export,Payment was captured");
    expect(fileOf(after.result, "qbo-invoices-import.csv")).toBeUndefined();
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ state: "exported", paymentFollowUp: true });
    const again = await run(f, "export", journal);
    expect(again.result.counts.paymentFollowUps).toBe(0);
    expect(again.result.reviewQueue.some((r) => r.orderId === "1002")).toBe(false);
  });

  test("a lost claim keeps the order out of the files and holds the cursor", async () => {
    const result = await run(base, "export", new Map(), {}, (ports) => ({
      ...ports, cas: async (key, value, version) => key.endsWith("/order/1002") ? { ok: false, error: "version_conflict" } : ports.cas(key, value, version),
    }));
    expect(result.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "order_claim_conflict" });
    expect(fileOf(result.result, "qbo-invoices-import.csv")).toBeUndefined();
    expect(result.result.cursorAdvanceSafe).toBe(false);
  });

  test("unreadable journal blocks the run before anything is exported", async () => {
    const result = await run(base, "export", new Map(), {}, (ports) => ({ ...ports, read: async () => ({ ok: false, error: "down" }) }));
    expect(result.result).toMatchObject({ status: "blocked", reason: "journal_unavailable", files: [] });
  });
});

describe("posted detection through read-only QuickBooks invoices", () => {
  test("an exported invoice found by number, total and customer becomes confirmed_posted", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    f.quickbooks!.invoices = [invoice("BC-1002", 20, "Pat Sample", "77")];
    const after = await run(f, "export", journal);
    expect(after.result.orders).toContainEqual({ orderId: "1002", outcome: "confirmed_posted" });
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "77" });
    expect(journal.get("bcqbo/demo/pending")?.value).toEqual({ schema: 1, orderIds: [] });
  });

  test("incomplete invoice paging is unknown, never 'not posted'; complete paging finds it", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    const filler = Array.from({ length: 100 }, (_, i) => invoice("BC-9" + String(i).padStart(3, "0"), 1, "Someone", String(1000 + i)));
    f.quickbooks!.invoices = [...filler, invoice("BC-1002", 20, "Pat Sample", "77")];
    f.config.qboLimits.maxInvoicePages = 1;
    const partial = await run(f, "export", journal);
    expect(partial.result.qbo.invoicesComplete).toBe(false);
    expect(partial.result.orders).toContainEqual({ orderId: "1002", outcome: "awaiting_confirmation" });
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ state: "exported" });
    expect(fileOf(partial.result, "summary.md")).toContain("posted-invoice check incomplete");
    f.config.qboLimits.maxInvoicePages = 2;
    const complete = await run(f, "export", journal);
    expect(complete.result.qbo.invoicesComplete).toBe(true);
    expect(journal.get("bcqbo/demo/order/1002")?.value).toMatchObject({ state: "confirmed_posted" });
  });

  test("a posted invoice with a different total or customer is review, not confirmation", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    f.quickbooks!.invoices = [invoice("BC-1002", 21, "Pat Sample")];
    const after = await run(f, "export", journal);
    expect(after.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "posted_invoice_mismatch" });
  });

  test("never-exported orders already in QuickBooks are not exported again", async () => {
    const f = clone();
    f.quickbooks!.invoices = [invoice("BC-1002", 20, "Pat Sample")];
    const same = await run(f, "export");
    expect(same.result.orders).toContainEqual({ orderId: "1002", outcome: "already_posted" });
    expect(fileOf(same.result, "qbo-invoices-import.csv")).toBeUndefined();
    f.quickbooks!.invoices = [invoice("BC-1002", 99, "Other")];
    const clash = await run(f, "export");
    expect(clash.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "docnumber_already_used_in_qbo" });
    f.quickbooks!.invoices = [invoice("BC-1002", 20, "Pat Sample", "1"), invoice("BC-1002", 20, "Pat Sample", "2")];
    const twice = await run(f, "export");
    expect(twice.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "duplicate_docnumber_in_qbo" });
  });

  test("re-export is refused when QuickBooks already shows the invoice", async () => {
    const journal: Journal = new Map();
    await exportAcked(base, journal);
    const f = clone();
    f.quickbooks!.invoices = [invoice("BC-1002", 20, "Pat Sample")];
    const again = await run(f, "export", journal, { reexportOrderIds: ["1002"] });
    expect(again.result.reviewQueue).toContainEqual({ orderId: "1002", reason: "reexport_blocked_already_in_qbo" });
    expect(fileOf(again.result, "qbo-invoices-import.csv")).toBeUndefined();
  });
});

describe("customers, vendors and the no-QuickBooks path", () => {
  test("ambiguous customer email goes to review; other orders still export", async () => {
    const f = clone();
    f.quickbooks!.customers.push({ Id: "11", DisplayName: "Example Buyer 2", Active: true, PrimaryEmailAddr: { Address: "BUYER@example.test " } });
    const { result } = await run(f, "preview");
    expect(result.reviewQueue).toContainEqual({ orderId: "1001", reason: "ambiguous_customer_email" });
    expect(fileOf(result, "qbo-invoices-import.csv")).toContain("BC-1002");
  });

  test("a derived new-customer name that collides with an existing customer goes to review", async () => {
    const f = clone();
    f.quickbooks!.customers.push({ Id: "12", DisplayName: "pat sample", Active: true, PrimaryEmailAddr: { Address: "other@example.test" } });
    const { result } = await run(f, "preview");
    expect(result.reviewQueue).toContainEqual({ orderId: "1002", reason: "customer_name_collision" });
  });

  test("new customers are review when the configuration disallows them", async () => {
    const f = clone();
    f.config.allowNewCustomers = false;
    const { result } = await run(f, "preview");
    expect(result.reviewQueue).toContainEqual({ orderId: "1002", reason: "customer_not_found" });
    expect(fileOf(result, "qbo-customers-import.csv")).toBeUndefined();
  });

  test("customer list beyond the paging limit is review, not 'new customer'", async () => {
    const f = clone();
    f.quickbooks!.customers = [...Array.from({ length: 100 }, (_, i) => ({ Id: String(100 + i), DisplayName: "C" + i, Active: true })), ...f.quickbooks!.customers];
    f.config.qboLimits.maxCustomerPages = 1;
    const { result } = await run(f, "preview");
    expect(result.reviewQueue).toContainEqual({ orderId: "1002", reason: "qbo_customer_list_incomplete" });
    expect(result.reviewQueue).toContainEqual({ orderId: "1001", reason: "qbo_customer_list_incomplete" });
  });

  test("without a QuickBooks connection files are still produced, marked unverified", async () => {
    const f = clone();
    f.quickbooks = null;
    const { result } = await run(f, "export");
    expect(result.status).toBe("prepared");
    expect(result.qbo.connected).toBe(false);
    expect(fileOf(result, "qbo-customers-import.csv")).toContain("Example Buyer,,buyer@example.test");
    expect(fileOf(result, "supplier-po-worksheet.csv")).toContain("Example Supply Co,unverified,Line");
    expect(fileOf(result, "summary.md")).toContain("not connected; customers unverified and posting not confirmed");
    expect(result.counts).toMatchObject({ prepared: 2, awaitingConfirmation: 0 });
  });

  test("QuickBooks company region or home currency mismatch blocks the run", async () => {
    const f = clone();
    f.quickbooks!.companyInfo.Country = "GB";
    expect((await run(f, "preview")).result).toMatchObject({ status: "blocked", reason: "qbo_company_region_mismatch" });
    const g = clone();
    g.quickbooks!.homeCurrency = "CAD";
    expect((await run(g, "preview")).result).toMatchObject({ status: "blocked", reason: "qbo_home_currency_mismatch" });
  });

  test("vendor missing in QuickBooks is flagged on the PO worksheet", async () => {
    const f = clone();
    f.quickbooks!.vendors = [];
    const { result } = await run(f, "preview");
    expect(fileOf(result, "supplier-po-worksheet.csv")).toContain("Example Supply Co,not_found_in_qbo,Line");
  });
});

describe("payment treatments, regions and import limits", () => {
  test("invoice_then_payment adds captured orders to the invoice import plus a payments worksheet", async () => {
    const f = clone();
    f.config.paidOrderTreatment = "invoice_then_payment";
    const o = order(f, 1001);
    o.discount_amount = "0.00"; o.total_ex_tax = "25.00"; o.total_inc_tax = "25.00";
    const { result } = await run(f, "preview");
    expect(fileOf(result, "payments-to-record.csv")).toBe("InvoiceNo,Customer,PaymentDate,Amount,PaymentMethod,DepositTo,ReferenceNo,Memo\r\n"
      + "BC-1001,Example Buyer,10/16/2026,25.00,Credit Card,Undeposited Funds,BC-1001,BigCommerce order 1001 payment\r\n");
    expect(fileOf(result, "qbo-invoices-import.csv")).toContain("BC-1001,Example Buyer");
    expect(fileOf(result, "sales-receipts-worksheet.csv")).toBeUndefined();
  });

  test("discounts cannot go into the invoice import", async () => {
    const f = clone();
    order(f, 1001).payment_status = "pending";
    const { result } = await run(f, "preview");
    expect(result.reviewQueue).toContainEqual({ orderId: "1001", reason: "discount_not_supported_by_invoice_import" });
  });

  test("non-US companies get the tax code columns", async () => {
    const f = clone();
    f.config = configSchema.parse({ ...f.config, region: "non_US", zeroTaxCodeName: "Zero-rated", dateFormat: "DD/MM/YYYY" });
    f.quickbooks!.companyInfo.Country = "GB";
    const { result } = await run(f, "preview");
    expect(fileOf(result, "qbo-invoices-import.csv")).toBe(
      "InvoiceNo,Customer,InvoiceDate,DueDate,Terms,Memo,Item(Product/Service),ItemDescription,ItemQuantity,ItemRate,ItemAmount,ItemTaxCode,ItemTaxAmount\r\n"
      + "BC-1002,Pat Sample,16/10/2026,15/11/2026,Net 30,BigCommerce order 1002,Gadget,\"'=Gadget \"\"deluxe\"\"\",1,15.50,15.50,Zero-rated,0.00\r\n"
      + "BC-1002,Pat Sample,16/10/2026,15/11/2026,Net 30,BigCommerce order 1002,Shipping,Shipping,1,4.50,4.50,Zero-rated,0.00\r\n");
  });

  test("invoice files split at 100 invoices and under 1,000 rows", () => {
    const config = configSchema.parse(base.config);
    const orders: PlannedOrder[] = Array.from({ length: 101 }, (_, i) => ({
      orderId: String(2000 + i), status: "planned", kind: "invoice", docNumber: "BC-" + (2000 + i), date: "2026-10-16",
      currency: "USD", totalCents: 100, customer: { name: "Example Buyer", match: "matched" }, purchaseLines: [],
      lines: [{ type: "product", sku: "WIDGET", item: "Widget", description: "", qty: 1, rateCents: 100, amountCents: 100 }],
    }));
    const files = renderBatch(config, orders, [], {
      batchId: "x", mode: "export", since: "a", until: "b", timezone: "UTC",
      counts: { prepared: 101, carried: 0, notCarried: 0, alreadyExported: 0, alreadyPosted: 0, confirmedPosted: 0, awaitingConfirmation: 0, review: 0, paymentFollowUps: 0 },
      carried: [], supersedes: [], notCarried: [],
      qbo: { connected: false, customerLookup: "not_connected", postedCheck: "not_connected", warnings: [] },
    });
    expect(files.filter((f) => f.name.startsWith("qbo-invoices-import")).map((f) => [f.name, f.rows]))
      .toEqual([["qbo-invoices-import.csv", 100], ["qbo-invoices-import-part2.csv", 1]]);
  });

  test("ineligible window bounds and invalid inputs are blocked", async () => {
    const { ports } = fixturePorts(base);
    expect(await runSync({ config: base.config, mode: "preview", since: "2026-10-17T00:00:00Z", until: "2026-10-15T00:00:00Z", timezone: "UTC" }, ports))
      .toMatchObject({ status: "blocked", reason: "fixed_timestamp_window_required" });
    expect(await runSync({ config: base.config, mode: "execute" as "export", since: base.window.since, until: base.window.until, timezone: "UTC" }, ports))
      .toMatchObject({ status: "blocked", reason: "invalid_runtime_input" });
  });
});
