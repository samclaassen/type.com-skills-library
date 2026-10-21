/**
 * Regression tests for the r5 independent QA findings (F1–F13), restated for the 3.1 acknowledgement design
 * (orders are pending until the uploaded batch is acknowledged). Each test asserts the rendered output or the
 * journal, the expected reason, and that a safe counterpart in the same batch still exports.
 */
import { describe, expect, test } from "bun:test";
import { Type } from "@type-dot-com/sdk";
import { chmodSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import skill, { inputSchema, publishFiles } from "./main";
import { fixturePorts, fixtureSchema, type Fixture } from "./preview";
import { listAll, type QboCall } from "./qbo";
import { runAcknowledge } from "./ack";
import { phoneCell, PREVIEW_PREFIX } from "./render";
import { runPoll } from "./scheduler";
import { runSync, type Ports } from "./sync";

const base: Fixture = fixtureSchema.parse(JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8")));
const WINDOW = { since: "2026-10-15T00:00:00Z", until: "2026-10-17T00:00:00Z", timezone: "UTC" };
type Journal = Map<string, { value: unknown; version: string }>;
type Row = Record<string, any>;

/** Eligible, unpaid, tax-free: 2 x WIDGET @10.00 + 5.00 shipping = 25.00. */
function order(id: number, over: Row = {}, products: Row[] | null = null): Row {
  return {
    id, date_created: "2026-10-16T12:00:00Z", date_modified: "2026-10-16T12:00:00Z", status_id: 11, payment_status: "pending",
    default_currency_code: "USD", currency_code: "USD",
    billing_address: { first_name: "Test", last_name: "Buyer" + id, email: `b${id}@example.test`, street_1: "1 Test St",
      city: "Testville", state: "CA", zip: "90001", country_iso2: "US" },
    total_inc_tax: "25.00", total_ex_tax: "25.00", subtotal_ex_tax: "20.00", subtotal_tax: "0.00", shipping_cost_ex_tax: "5.00",
    shipping_cost_tax: "0.00", discount_amount: "0.00", coupon_discount: "0.00", handling_cost_ex_tax: "0", handling_cost_tax: "0",
    wrapping_cost_ex_tax: "0", wrapping_cost_tax: "0", refunded_amount: "0", gift_certificate_amount: "0", store_credit_amount: "0",
    products: products ?? [product("WIDGET", 2, "10.00", "20.00")], ...over,
  };
}
function product(sku: string, quantity: number, price: string, total: string, extra: Row = {}): Row {
  return { sku, name: sku, quantity, price_ex_tax: price, total_ex_tax: total, total_tax: "0.00", tax_class_id: 0, applied_discounts: [], ...extra };
}
function fixture(orders: Row[], options: { config?: Row; qbo?: Row | null } = {}): Fixture {
  const config = { ...base.config, purchaseOrders: false, suppliers: {}, ...(options.config ?? {}) };
  const quickbooks = options.qbo === null ? null : { ...base.quickbooks, customers: [], invoices: [], ...(options.qbo ?? {}) };
  return fixtureSchema.parse({ window: WINDOW, config, bigcommerce: { orders }, quickbooks });
}
const invoice = (doc: string, total: string, name: string, id = "900") => ({ Id: id, DocNumber: doc, TotalAmt: total, CustomerRef: { value: "1", name } });
function ports(f: Fixture, journal: Journal = new Map(), wrap?: (p: Ports) => Partial<Ports>) {
  const { ports: p } = fixturePorts(f, journal);
  return { ...p, ...(wrap ? wrap(p) : {}) } as Ports;
}
type Out = Record<string, any>;
const run = async (f: Fixture, p: Ports, extra: Row = {}) =>
  (await runSync({ config: f.config, mode: "export", ...WINDOW, ...extra }, p)) as Out;
const fileOf = (r: Out, name: string) => (r.files as { name: string; content: string }[]).find((x) => x.name === name || x.name === PREVIEW_PREFIX + name)?.content;
const acknowledge = (f: Fixture, p: Ports, r: Out) => runAcknowledge({ config: f.config, ...r.acknowledge }, p) as Promise<Out>;
const reasonOf = (r: Out, oid: string) => (r.reviewQueue as { orderId: string; reason: string }[]).find((x) => x.orderId === oid)?.reason;
const outcomeOf = (r: Out, oid: string) => (r.orders as { orderId: string; outcome: string }[]).find((x) => x.orderId === oid)?.outcome;
const state = (journal: Journal, oid: string) => journal.get("bcqbo/demo/order/" + oid)?.value as Row | undefined;

/** Fake Type API behind the real SDK; BigCommerce/QuickBooks come from the fixture, Type data is a versioned map. */
function fakeType(f: Fixture) {
  const { ports: source } = fixturePorts(f);
  const store = new Map<string, { value: unknown; version: string }>();
  const sent: { path: string; body: Row }[] = [];
  let counter = 0;
  const type = new Type({
    serverUrl: "https://fixture.type.test", accessToken: "fixture-token", agentId: "agent_fixture", channelId: "ch_fixture",
    fetch: async (url, init) => {
      const path = new URL(String(url)).pathname;
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      sent.push({ path, body });
      const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
      if (path.endsWith("/credential-connections/api/request")) {
        const answer = await source.bigcommerce(body.path, body.query);
        return json({ connection: { id: body.organizationIntegrationId, name: "Fixture", baseUrl: "https://api.bigcommerce.com" },
          status: answer.status, statusText: "OK", success: answer.status < 300, truncated: false, data: answer.data, headers: {} });
      }
      if (path.endsWith("/tools/call")) {
        if (body.service === "slack") return json({ content: [], structuredContent: { status: "sent", channelId: body.arguments.channel_id, ts: "1.2" } });
        const answer = source.qbo ? await source.qbo(body.name, body.arguments) : { ok: false as const, error: "none", retryable: false };
        return json("data" in answer ? { content: [], structuredContent: answer.data } : { content: [], isError: true, structuredContent: { error: { retryable: false } } });
      }
      if (path.endsWith("/data")) {
        const row = store.get(body.key);
        if (body.operation === "read")
          return json(row ? { found: true, key: body.key, version: row.version, updatedAt: "2026-10-16T00:00:00Z", value: row.value } : { found: false, key: body.key, version: null });
        if (body.operation === "record") {
          if ((row?.version ?? null) !== body.expectedVersion) return json({ error: "changed", code: "version_conflict" }, 409);
          const version = "00000000-0000-4000-8000-" + String(++counter).padStart(12, "0");
          store.set(body.key, { value: body.value, version });
          return json({ stored: true, key: body.key, version });
        }
      }
      return json({ error: "unexpected" }, 500);
    },
  });
  return { type, sent, store };
}
const execute = async (args: Row, type: Type, runId = "run_" + Math.random().toString(36).slice(2)) => {
  const result = await skill.execute(args as Parameters<typeof skill.execute>[0], { type, runId, integrations: {} });
  const value = result._unsafeUnwrap();
  return (value.action === "skip" ? value.output : null) as Out;
};

describe("F1 (3.1): nothing is exported before acknowledgement; interrupted runs are carried, never lost", () => {
  test("crash right after the first pending record: the rerun carries the order into a new batch; acknowledging it exports both", async () => {
    const f = fixture([order(5001), order(5002)]);
    const journal: Journal = new Map();
    let writes = 0;
    const crashing = ports(f, journal, (p) => ({
      cas: async (key, value, version) => {
        const stored = await p.cas(key, value, version);
        if (key.endsWith("/order/5001") && ++writes === 1) throw new Error("simulated crash");
        return stored;
      },
    }));
    await expect(run(f, crashing)).rejects.toThrow("simulated crash");
    expect(state(journal, "5001")).toMatchObject({ state: "pending", pendingBatch: { docNumber: "BC-5001" } });
    const previousBatch = (state(journal, "5001")?.pendingBatch as Row).batchId;
    expect(journal.get("bcqbo/demo/batch/" + previousBatch)).toBeUndefined();
    const rerun = await run(f, ports(f, journal));
    expect(outcomeOf(rerun, "5001")).toBe("pending_acknowledgement");
    expect(outcomeOf(rerun, "5002")).toBe("pending_acknowledgement");
    expect(fileOf(rerun, "qbo-invoices-import.csv")).toContain("BC-5001,Test Buyer5001");
    expect(fileOf(rerun, "qbo-invoices-import.csv")).toContain("BC-5002,Test Buyer5002");
    expect(rerun.carried).toEqual([{ orderId: "5001", previousBatchId: previousBatch, sameRows: true }]);
    expect(fileOf(rerun, "summary.md")).toContain(`- Order 5001: earlier batch ${previousBatch}`);
    expect(rerun.cursorAdvanceSafe).toBe(true);
    expect(state(journal, "5001")).toMatchObject({ state: "pending" });
    expect(await acknowledge(f, ports(f, journal), rerun)).toMatchObject({ status: "acknowledged" });
    expect(state(journal, "5001")).toMatchObject({ state: "exported", export: { exportCount: 1, batchId: rerun.batchId } });
    const third = await run(f, ports(f, journal));
    expect(third.counts).toMatchObject({ prepared: 0, alreadyExported: 2 });
  });

  test("a pending order outside the window is found through the index and carried", async () => {
    const f = fixture([order(5003, { date_modified: "2026-10-01T00:00:00Z" })]);
    const journal: Journal = new Map();
    const crashing = ports(f, journal, (p) => ({
      cas: async (key, value, version) => {
        const stored = await p.cas(key, value, version);
        if (key.endsWith("/order/5003")) throw new Error("simulated crash");
        return stored;
      },
    }));
    await expect(run(f, crashing, { reexportOrderIds: ["5003"] })).rejects.toThrow("simulated crash");
    expect((journal.get("bcqbo/demo/pending")?.value as Row).orderIds).toEqual(["5003"]);
    const later = await run(f, ports(f, journal));
    expect(outcomeOf(later, "5003")).toBe("pending_acknowledgement");
    expect(fileOf(later, "qbo-invoices-import.csv")).toContain("BC-5003");
    await acknowledge(f, ports(f, journal), later);
    expect(state(journal, "5003")).toMatchObject({ state: "exported", export: { exportCount: 1 } });
  });

  test("a crash after the batch folder was written: the batch is never treated as delivered; the next run supersedes it", async () => {
    const f = fixture([order(5004)]);
    const journal: Journal = new Map();
    const crashing = ports(f, journal, () => ({ deliver: async () => { throw new Error("crash after the folder was written"); } }));
    await expect(run(f, crashing)).rejects.toThrow("crash after the folder was written");
    const first = (state(journal, "5004")?.pendingBatch as Row).batchId as string;
    expect((journal.get("bcqbo/demo/batch/" + first)?.value as Row).status).toBe("prepared");
    const rerun = await run(f, ports(f, journal));
    expect(rerun.supersedes).toEqual([first]);
    expect(fileOf(rerun, "summary.md")).toContain(`This batch supersedes unacknowledged batch ${first} — import only the newest batch.`);
    expect(fileOf(rerun, "qbo-invoices-import.csv")).toContain("BC-5004");
    const stale = await acknowledge(f, ports(f, journal), { acknowledge: { batchId: first,
      contentSha256: (journal.get("bcqbo/demo/batch/" + first)?.value as Row).contentSha256 } });
    expect(stale).toMatchObject({ status: "blocked", reason: "batch_superseded", supersededBy: rerun.batchId });
    expect(await acknowledge(f, ports(f, journal), rerun)).toMatchObject({ status: "acknowledged" });
    expect(state(journal, "5004")).toMatchObject({ state: "exported", export: { exportCount: 1, batchId: rerun.batchId } });
  });

  test("a failed folder write returns no content, voids the manifest, leaves orders pending, and the next run carries them", async () => {
    const f = fixture([order(5005)]);
    const journal: Journal = new Map();
    const failing = ports(f, journal, () => ({ deliver: async () => ({ ok: false as const, error: "output_write_failed" }) }));
    const failed = await run(f, failing);
    expect(failed).toMatchObject({ status: "blocked", reason: "output_delivery_failed", cursorAdvanceSafe: false, files: [], pendingOrderIds: ["5005"] });
    expect(state(journal, "5005")).toMatchObject({ state: "pending" });
    expect((journal.get("bcqbo/demo/batch/" + failed.batchId)?.value as Row).status).toBe("void");
    const next = await run(f, ports(f, journal));
    expect(fileOf(next, "qbo-invoices-import.csv")).toContain("BC-5005");
    expect(await acknowledge(f, ports(f, journal), next)).toMatchObject({ status: "acknowledged" });
    expect(state(journal, "5005")).toMatchObject({ state: "exported" });
  });

  test("F1b: a large batch whose folder cannot be written returns no content and records nothing as exported", async () => {
    const many = Array.from({ length: 3 }, (_, i) => order(5100 + i, { billing_address: { first_name: "Big", last_name: "Buyer" + i,
      email: `big${i}@example.test`, country_iso2: "US" } }, [product("WIDGET", 2, "10.00", "20.00", { name: "x".repeat(200000) })]));
    const f = fixture(many);
    const fake = fakeType(f);
    const parent = mkdtempSync(join(tmpdir(), "bcqbo-ro-"));
    writeFileSync(join(parent, "blocker"), "file, not a folder");
    const out = await execute({ config: f.config, integrationIds: { bigcommerce: "orgint_bc" }, ...WINDOW, mode: "export",
      outputDir: join(parent, "blocker") }, fake.type);
    expect(out).toMatchObject({ status: "blocked", reason: "output_delivery_failed", delivery: { directory: null, error: "output_write_failed", files: [] } });
    for (let i = 0; i < 3; i++) expect((fake.store.get("bcqbo/demo/order/" + (5100 + i))?.value as Row).state).toBe("pending");
  });

  test("publishing never writes into an existing folder", () => {
    const parent = mkdtempSync(join(tmpdir(), "bcqbo-pub-"));
    mkdirSync(join(parent, "abc-1"));
    expect(publishFiles([], parent, "abc-1")).toEqual({ ok: false, error: "output_folder_exists" });
    const unwritable = mkdtempSync(join(tmpdir(), "bcqbo-pub-"));
    chmodSync(unwritable, 0o500);
    const denied = publishFiles([{ name: "a.csv", content: "a", bytes: 1, sha256: "", rows: 1 }], unwritable, "abc-2");
    chmodSync(unwritable, 0o700);
    if (process.getuid?.() !== 0) expect(denied).toEqual({ ok: false, error: "output_write_failed" });
    expect(readdirSync(unwritable)).toEqual([]);
  });
});

describe("F2: every run publishes into its own folder", () => {
  test("rerunning the same window never leaves a stale import file beside the new summary", async () => {
    const f = fixture([order(7001), order(7002)]);
    const fake = fakeType(f);
    const dir = mkdtempSync(join(tmpdir(), "bcqbo-runs-"));
    const args = { config: f.config, integrationIds: { bigcommerce: "orgint_bc", quickbooks: "orgint_qbo" }, ...WINDOW, mode: "export", outputDir: dir };
    const preview = await execute({ ...args, mode: "preview" }, fake.type);
    const first = await execute(args, fake.type);
    const { since: _s, until: _u, ...ackArgs } = args;
    expect(await execute({ ...ackArgs, outputDir: undefined, acknowledgeBatch: first.acknowledge }, fake.type)).toMatchObject({ status: "acknowledged" });
    const second = await execute(args, fake.type);
    expect(new Set([preview.batchId, first.batchId, second.batchId]).size).toBe(3);
    expect(first.windowId).toBe(second.windowId);
    expect(readdirSync(dir).sort()).toEqual([preview.batchId, first.batchId, second.batchId].sort());
    expect(readdirSync(join(dir, first.batchId))).toContain("qbo-invoices-import.csv");
    expect(readdirSync(join(dir, first.batchId))).toContain("manifest.json");
    expect(readdirSync(join(dir, second.batchId)).sort()).toEqual(["review-queue.csv", "review-queue.json", "summary.md"]);
    const summary = readFileSync(join(dir, second.batchId, "summary.md"), "utf8");
    expect(summary).toContain(`# BigCommerce → QuickBooks import batch ${second.batchId}`);
    expect(summary).toContain("- No import files in this batch.");
    expect(second.counts).toMatchObject({ prepared: 0, alreadyExported: 2 });
  });
});

describe("F3–F5: posted invoices are final and unknown is never 'not posted'", () => {
  test("F3: an order already in QuickBooks before export that later changes goes to review; an unchanged one stays posted", async () => {
    const q = { invoices: [invoice("BC-4001", "25.00", "Test Buyer4001", "1"), invoice("BC-4011", "25.00", "Test Buyer4011", "2")] };
    const journal: Journal = new Map();
    const f = fixture([order(4001), order(4011)], { qbo: q });
    const first = await run(f, ports(f, journal));
    expect(first.counts).toMatchObject({ prepared: 0, alreadyPosted: 2 });
    const changed = fixture([order(4001, { total_inc_tax: "30.00", total_ex_tax: "30.00", shipping_cost_ex_tax: "10.00" }), order(4011)], { qbo: q });
    const r = await run(changed, ports(changed, journal));
    expect(reasonOf(r, "4001")).toBe("changed_after_posted_in_qbo");
    expect(fileOf(r, "review-queue.csv")).toContain("4001,changed_after_posted_in_qbo,Order changed after QuickBooks already had this invoice.");
    expect(outcomeOf(r, "4011")).toBe("already_posted");
    expect(fileOf(r, "qbo-invoices-import.csv")).toBeUndefined();
    expect(state(journal, "4001")).toMatchObject({ state: "needs_review", qboInvoiceId: "1" });
    // Without QuickBooks the parked order is still never exported.
    const offline = fixture([order(4001, { total_inc_tax: "30.00", total_ex_tax: "30.00", shipping_cost_ex_tax: "10.00" })], { qbo: null });
    const again = await run(offline, ports(offline, journal));
    expect(reasonOf(again, "4001")).toBe("changed_after_posted_in_qbo");
    expect(fileOf(again, "qbo-invoices-import.csv")).toBeUndefined();
  });

  test("F4: re-export of a journal-confirmed invoice is refused without QuickBooks; the journal keeps its posting", async () => {
    const journal: Journal = new Map();
    const f = fixture([order(4002), order(4012)]);
    await acknowledge(f, ports(f, journal), await run(f, ports(f, journal)));
    const fq = fixture([order(4002), order(4012)], { qbo: { invoices: [invoice("BC-4002", "25.00", "Test Buyer4002", "77")] } });
    await run(fq, ports(fq, journal));
    expect(state(journal, "4002")).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "77" });
    const offline = fixture([order(4002), order(4012)], { qbo: null });
    const r = await run(offline, ports(offline, journal), { reexportOrderIds: ["4002", "4012"] });
    expect(reasonOf(r, "4002")).toBe("reexport_blocked_already_in_qbo");
    expect(fileOf(r, "qbo-invoices-import.csv")).not.toContain("BC-4002");
    expect(state(journal, "4002")).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "77", export: { exportCount: 1 } });
    // The unposted sibling can still be re-exported on request.
    expect(fileOf(r, "qbo-invoices-import.csv")).toContain("BC-4012");
    expect(state(journal, "4012")).toMatchObject({ state: "pending", export: { exportCount: 1 } });
    await acknowledge(offline, ports(offline, journal), r);
    expect(state(journal, "4012")).toMatchObject({ state: "exported", export: { exportCount: 2 } });
  });

  test("F5: with an incomplete invoice list an invoice goes to posted_status_unknown review; receipts still export; complete paging decides", async () => {
    const filler = Array.from({ length: 100 }, (_, i) => invoice("ZZ-" + i, "1.00", "x", String(i + 1)));
    const orders = [order(4003), order(4013, { payment_status: "captured" })];
    const limits = (pages: number) => ({ qboLimits: { maxCustomerPages: 100, maxVendorPages: 20, maxInvoicePages: pages } });
    const f = fixture(orders, { qbo: { invoices: [...filler, invoice("BC-4003", "25.00", "Test Buyer4003", "500")] }, config: limits(1) });
    const journal: Journal = new Map();
    const r = await run(f, ports(f, journal));
    expect(r.qbo.invoicesComplete).toBe(false);
    expect(reasonOf(r, "4003")).toBe("posted_status_unknown");
    expect(fileOf(r, "qbo-invoices-import.csv")).toBeUndefined();
    expect(fileOf(r, "sales-receipts-worksheet.csv")).toContain("BC-4013");
    const full = fixture(orders, { qbo: { invoices: [...filler, invoice("BC-4003", "25.00", "Test Buyer4003", "500")] }, config: limits(2) });
    const complete = await run(full, ports(full, journal));
    expect(outcomeOf(complete, "4003")).toBe("already_posted");
    expect(state(journal, "4003")).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "500" });
    const absent = fixture([order(4023)], { qbo: { invoices: filler }, config: limits(2) });
    expect(fileOf(await run(absent, ports(absent)), "qbo-invoices-import.csv")).toContain("BC-4023");
  });
});

describe("F6: unreadable or oversized orders never stall polling", () => {
  const poll = (f: Fixture, journal: Journal, wrap?: (p: Ports) => Partial<Ports>) =>
    runPoll({ config: f.config, mode: "export", timezone: "UTC", polling: { bootstrapSince: "2026-10-15T00:00:00Z", maxWindowSeconds: 172800 } }, ports(f, journal, wrap)) as Promise<Out>;

  test("an order with more than 500 products is parked for review and the checkpoint advances; its sibling exports", async () => {
    const big = order(5007, { total_inc_tax: "506.00", total_ex_tax: "506.00", subtotal_ex_tax: "501.00" },
      Array.from({ length: 501 }, (_, i) => product("WIDGET", 1, "1.00", "1.00", { name: "W" + i })));
    const f = fixture([big, order(5008)]);
    const journal: Journal = new Map();
    const first = await poll(f, journal);
    expect(first.status).toBe("polled");
    expect(reasonOf(first.batch, "5007")).toBe("order_exceeds_import_line_limit");
    expect(fileOf(first.batch, "qbo-invoices-import.csv")).toContain("BC-5008");
    expect(state(journal, "5007")).toMatchObject({ state: "needs_review", reason: "order_exceeds_import_line_limit" });
    expect((journal.get("bcqbo/demo/poll")?.value as Row).completedThrough).toBe("2026-10-17T00:00:00.000Z");
    expect((await poll(f, journal)).status).toBe("idle");
  });

  test("a products read that keeps failing holds the window for 3 runs, then is parked and polling moves on", async () => {
    const f = fixture([order(5009), order(5019)]);
    const journal: Journal = new Map();
    const failing = (p: Ports): Partial<Ports> => ({
      bigcommerce: async (path, query) => (path.endsWith("/5009/products") ? { status: 500, data: null } : p.bigcommerce(path, query)),
    });
    const statuses: string[] = [];
    for (let i = 0; i < 3; i++) {
      const out = await poll(f, journal, failing);
      statuses.push(out.status + ":" + (out.reason ?? "") + ":" + reasonOf(out.batch, "5009"));
    }
    expect(statuses).toEqual([
      "blocked:batch_requires_review_checkpoint_unchanged:products_read_failed",
      "blocked:batch_requires_review_checkpoint_unchanged:products_read_failed",
      "polled::products_read_failed_repeatedly",
    ]);
    expect(state(journal, "5019")).toMatchObject({ state: "pending" });
    expect(state(journal, "5009")).toMatchObject({ state: "needs_review", reason: "products_read_failed_repeatedly", readFailures: 3 });
  });

  test("a single transient failure is retried and the order then exports normally", async () => {
    const f = fixture([order(5020)]);
    const journal: Journal = new Map();
    let failures = 1;
    const flaky = (p: Ports): Partial<Ports> => ({
      bigcommerce: async (path, query) => (path.endsWith("/5020/products") && failures-- > 0 ? { status: 503, data: null } : p.bigcommerce(path, query)),
    });
    expect((await poll(f, journal, flaky)).status).toBe("blocked");
    const second = await poll(f, journal, flaky);
    expect(second.status).toBe("polled");
    expect(fileOf(second.batch, "qbo-invoices-import.csv")).toContain("BC-5020");
  });
});

describe("F7–F13: lower-severity findings", () => {
  test("F7: one email with two derived names goes to review; a distinct buyer still exports", async () => {
    const f = fixture([order(4004, { billing_address: { first_name: "Pat", last_name: "Lee", email: "pat@example.test" } }),
      order(4005, { billing_address: { first_name: "Patricia", last_name: "Lee", email: "PAT@example.test" } }), order(4006)]);
    const r = await run(f, ports(f), { mode: "preview" });
    expect(reasonOf(r, "4004")).toBe("new_customer_email_conflict");
    expect(reasonOf(r, "4005")).toBe("new_customer_email_conflict");
    expect(fileOf(r, "qbo-customers-import.csv")).toBe("Customer,Company name,Email,Phone,Website,Billing Street,Billing City,Billing State,Billing Zip,Billing Country\r\n"
      + "Test Buyer4006,,b4006@example.test,,,1 Test St,Testville,CA,90001,US\r\n");
  });

  test("F8: a tool that caps pages at 50 rows is paged by rows returned, so the existing customer is matched", async () => {
    const customers = Array.from({ length: 60 }, (_, i) => ({ Id: String(i), DisplayName: "C" + i, Active: true, PrimaryEmailAddr: { Address: `c${i}@example.test` } }));
    const f = fixture([order(4007, { billing_address: { first_name: "C59", last_name: "", email: "c59@example.test" } }), order(4017)], { qbo: { customers } });
    const capped = ports(f, new Map(), (p) => ({ qbo: (async (tool, a) => p.qbo!(tool, tool === "quickbooks_list_entities" ? { ...a, maxResults: 50 } : a)) as QboCall }));
    const r = await run(f, capped, { mode: "preview" });
    expect(r.qbo.customersComplete).toBe(true);
    expect(fileOf(r, "qbo-invoices-import.csv")).toContain("BC-4007,C59,");
    expect(fileOf(r, "qbo-customers-import.csv")).not.toContain("C59");
    expect(fileOf(r, "qbo-customers-import.csv")).toContain("Test Buyer4017");
  });

  test("F8: a tool that ignores startPosition is a paging error, never a complete list", async () => {
    const page = { ok: true as const, data: { QueryResponse: { Customer: Array.from({ length: 100 }, (_, i) => ({ Id: String(i) })) } } };
    expect(await listAll(async () => page, async () => {}, "Customer", 5)).toEqual({ ok: false, reason: "qbo_customer_paging_inconsistent" });
    const exact = Array.from({ length: 100 }, (_, i) => ({ Id: String(i) }));
    const pages: QboCall = async (_tool, a) => ({ ok: true, data: { QueryResponse: { Customer: exact.slice(Number(a.startPosition) - 1, Number(a.startPosition) - 1 + Number(a.maxResults)) } } });
    expect(await listAll(pages, async () => {}, "Customer", 1)).toMatchObject({ ok: true, complete: true });
  });

  test("F9/N6/R7-4: E.164 phones (also once formatting is removed) are written as E.164; anything else starting with + - = @ is neutralized", async () => {
    expect(phoneCell("+15551234567")).toBe("+15551234567");
    expect(phoneCell("555-0100")).toBe("555-0100");
    expect(phoneCell("+1 555 0100")).toBe("+15550100");
    expect(phoneCell("+44 (20) 7946-0958")).toBe("+442079460958");
    expect(phoneCell("+SUM(A1)")).toBe("'+SUM(A1)");
    expect(phoneCell("=1+1")).toBe("'=1+1");
    expect(phoneCell("-2+3")).toBe("'-2+3");
    expect(phoneCell("+1 555\u0007")).toBe("'+1 555 ");
    const f = fixture([order(7010, { billing_address: { first_name: "Ann", last_name: "Q", email: "ann@example.test", phone: "+15550100" } }),
      order(7011, { billing_address: { first_name: "Bo", last_name: "R", email: "bo@example.test", phone: "+HYPERLINK(1)" } })]);
    const r = await run(f, ports(f), { mode: "preview" });
    expect(fileOf(r, "qbo-customers-import.csv")).toContain("Ann Q,,ann@example.test,+15550100,");
    expect(fileOf(r, "qbo-customers-import.csv")).toContain("Bo R,,bo@example.test,'+HYPERLINK(1),");
  });

  test("F10: Slack is sent only when the batch is acknowledged; it names the batch and never claims files are attached", async () => {
    const f = fixture([order(7020)]);
    const texts: string[] = [];
    const journal: Journal = new Map();
    const withSlack = ports(f, journal, () => ({ slack: async (text: string) => { texts.push(text); return "sent" as const; } }));
    const r = await run(f, withSlack);
    expect(texts).toHaveLength(0);
    const acked = await acknowledge(f, withSlack, r);
    expect(acked.notification).toBe("sent");
    expect(texts[0]).toContain(`batch ${r.batchId} acknowledged`);
    expect(texts[0]).toContain("uploaded to Type (not to Slack)");
    expect(texts[0]).not.toMatch(/attached|example\.test|Buyer7020/i);
    expect((await acknowledge(f, withSlack, r)).alreadyAcknowledged).toBe(true);
    expect(texts).toHaveLength(1);
  });

  test("F11: timezone aliases are accepted by both the input schema and the engine; offsets are rejected by both", async () => {
    const f = fixture([order(6200, { date_created: "2026-10-16T02:00:00Z" })]);
    const input = (timezone: string) => ({ config: f.config, integrationIds: { bigcommerce: "orgint_x" }, ...WINDOW, timezone });
    for (const tz of ["Etc/UTC", "US/Eastern", "America/New_York"]) {
      expect(inputSchema.safeParse(input(tz)).success).toBe(true);
      const r = await run(f, ports(f), { mode: "preview", timezone: tz });
      expect(r.status).toBe("preview");
      expect(fileOf(r, "qbo-invoices-import.csv")).toContain(tz === "Etc/UTC" ? "BC-6200,Test Buyer6200,10/16/2026" : "BC-6200,Test Buyer6200,10/15/2026");
    }
    for (const tz of ["+05:00", "Not/AZone"]) {
      expect(inputSchema.safeParse(input(tz)).success).toBe(false);
      expect(await run(f, ports(f), { mode: "preview", timezone: tz })).toMatchObject({ status: "blocked", reason: "supported_store_timezone_required" });
    }
  });

  test("F12: a full page of 100 plus an out-of-window re-export ID plans all 101 orders", async () => {
    const orders = Array.from({ length: 101 }, (_, i) => order(6000 + i, i === 100 ? { date_modified: "2026-10-01T00:00:00Z" } : {}));
    const f = fixture(orders);
    const r = await run(f, ports(f), { mode: "preview", reexportOrderIds: ["6100"] });
    expect(r.status).toBe("preview");
    expect(r.counts.prepared).toBe(101);
    expect(fileOf(r, "qbo-invoices-import-part2.csv")).toContain("BC-6100");
  });

  test("F13: a retryable QuickBooks failure backs off only between attempts (2 s + 4 s)", async () => {
    const f = fixture([order(4008)]);
    let slept = 0;
    const r = await run(f, ports(f, new Map(), (p) => ({
      sleep: async (ms: number) => { slept += ms; },
      qbo: (async (tool, a) => (tool === "quickbooks_list_entities" ? { ok: false, error: "timeout", retryable: true } : p.qbo!(tool, a))) as QboCall,
    })));
    expect(r.reason).toBe("qbo_customer_read_failed");
    expect(slept).toBe(6000);
  });
});
