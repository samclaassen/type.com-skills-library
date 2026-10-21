import { randomUUID } from "node:crypto";
import { z } from "zod";
import { configSchema, storeTimezone, type Config } from "./config";
import {
  acknowledgedExport, byId, contentSha256, hash, indexSchema, journalSchema, keys, manifestSchema, MAX_PENDING, MAX_READ_FAILURES,
  MAX_UNACKED, orderId, stable, type Journal, type Manifest,
} from "./journal";
import { invoiceById, postedCheck, readQuickBooks, type QboCall, type QboSnapshot } from "./qbo";
import { makeFile, renderBatch, type BatchCounts, type CarriedOrder, type OutputFile, type PlannedOrder, type ReviewItem } from "./render";

export { hash } from "./journal";
export type Response = { status: number; data: unknown; headers?: Record<string, string> };
export type Delivery = { ok: true; directory: string } | { ok: false; error: string };
export type Ports = {
  runId: string;
  now(): string;
  /** BigCommerce reads. The port has no method parameter: it can only issue GET requests. */
  bigcommerce(path: string, query?: Record<string, string>): Promise<Response>;
  /** Native read-only QuickBooks tools, or null when no QuickBooks connection is bound. */
  qbo: QboCall | null;
  /** Optional Slack summary, bound to one approved channel. Used only when a batch is acknowledged. */
  slack: ((text: string) => Promise<"sent" | "outcome_unknown">) | null;
  read(key: string): Promise<{ ok: true; value: unknown | null; version: string | null } | { ok: false; error: string }>;
  cas(key: string, value: unknown, expectedVersion: string | null): Promise<{ ok: boolean; version?: string; error?: string }>;
  plan(input: unknown): Promise<{ ok: true; value: unknown } | { ok: false; error: string }>;
  sleep(ms: number): Promise<void>;
  /**
   * Writes one batch's files into a new local folder named after the batch ID, all or nothing. Never writes into an
   * existing folder. Writing the folder is NOT delivery: the batch counts only after it is acknowledged (ack.ts).
   */
  deliver(files: OutputFile[], batchId: string): Promise<Delivery>;
};
export type SyncArgs = {
  config: Config;
  mode: "preview" | "export";
  since: string;
  until: string;
  timezone: string;
  afterOrderId?: string;
  maxOrders?: number;
  reexportOrderIds?: string[];
};
export type Outcome = "pending_acknowledgement" | "already_exported" | "already_posted" | "review" | "confirmed_posted" | "awaiting_confirmation";

const lineSchema = z.object({
  type: z.enum(["product", "shipping", "discount"]), sku: z.string(), item: z.string(), description: z.string(),
  qty: z.number().int().positive(), rateCents: z.number().int(), amountCents: z.number().int(),
});
const plannedSchema = z.object({
  orderId, status: z.literal("planned"), kind: z.enum(["invoice", "sales_receipt", "invoice_then_payment"]),
  docNumber: z.string().min(1).max(21), date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), currency: z.string().regex(/^[A-Z]{3}$/),
  totalCents: z.number().int().positive(),
  customer: z.object({
    name: z.string().min(1), match: z.enum(["matched", "new", "unverified"]), qboId: z.string().optional(),
    details: z.object({ company: z.string(), email: z.string(), phone: z.string(), street: z.string(), city: z.string(),
      state: z.string(), zip: z.string(), country: z.string() }).optional(),
  }),
  lines: z.array(lineSchema).min(1).max(502),
  purchaseLines: z.array(z.object({
    vendor: z.string(), vendorStatus: z.enum(["matched", "unverified", "not_found_in_qbo", "ambiguous_in_qbo"]), sku: z.string(),
    item: z.string(), qty: z.number().int().positive(), unitCostCents: z.number().int().positive(), amountCents: z.number().int().positive(),
  })).max(500),
});
const reviewSchema = z.object({ orderId: z.string().nullable(), status: z.literal("review"), reason: z.string().regex(/^[a-z_]{1,80}$/) });
const planSchema = z.object({ status: z.literal("planned"), orders: z.array(z.union([plannedSchema, reviewSchema])) });


const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const good = (r: Response) => r.status >= 200 && r.status < 300;
const MONEY_FIELDS = ["total_inc_tax", "total_ex_tax", "subtotal_ex_tax", "subtotal_tax", "shipping_cost_ex_tax", "shipping_cost_tax",
  "discount_amount", "coupon_discount", "handling_cost_ex_tax", "handling_cost_tax", "wrapping_cost_ex_tax", "wrapping_cost_tax",
  "refunded_amount", "gift_certificate_amount", "store_credit_amount", "default_currency_code", "qbo_txn_date"];
const PRODUCT_FIELDS = ["sku", "quantity", "price_ex_tax", "total_ex_tax", "total_tax", "is_refunded", "quantity_refunded", "applied_discounts"];

/** Financial fingerprint. Fulfilment status and payment capture changes are not accounting changes. */
export function sourceHash(raw: Record<string, unknown>): string {
  const pick = (source: Record<string, unknown>, keys: string[]) => Object.fromEntries(keys.map((k) => [k, source[k] ?? null]));
  const billing = isRecord(raw.billing_address) ? raw.billing_address : {};
  const products = Array.isArray(raw.products) ? raw.products.map((p) => (isRecord(p) ? pick(p, PRODUCT_FIELDS) : null)) : null;
  return hash({ id: String(raw.id), ...pick(raw, MONEY_FIELDS), email: typeof billing.email === "string" ? billing.email.trim().toLowerCase() : null, products });
}

async function get(ports: Ports, path: string, query?: Record<string, string>): Promise<Response> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await ports.bigcommerce(path, query);
    if (response.status !== 429 || attempt === 2) return response;
    const value = response.headers?.["retry-after"];
    const seconds = value && /^\d+$/.test(value) ? Number(value) : 2 ** attempt;
    // Defer instead of sleeping beyond the bounded invocation budget.
    if (seconds > 10) return response;
    await ports.sleep(Math.max(1, seconds) * 1000);
  }
  return { status: 0, data: null };
}

type Unreadable = "order_exceeds_import_line_limit" | "order_products_not_found" | "products_read_failed";
/** Durable reasons are properties of the order itself; only products_read_failed may be transient. */
async function readProducts(ports: Ports, bc: string, oid: string): Promise<{ ok: true; products: unknown[] } | { ok: false; reason: Unreadable }> {
  const products: unknown[] = [];
  for (let p = 1; p <= 3; p++) {
    const response = await get(ports, bc + "/v2/orders/" + oid + "/products", { page: String(p), limit: "250" });
    if (response.status === 404) return { ok: false, reason: "order_products_not_found" };
    const data = response.status === 204 ? [] : response.data;
    if (!good(response) || !Array.isArray(data) || data.length > 250) return { ok: false, reason: "products_read_failed" };
    products.push(...data);
    if (data.length < 250) return products.length <= 500 ? { ok: true, products } : { ok: false, reason: "order_exceeds_import_line_limit" };
  }
  return { ok: false, reason: "order_exceeds_import_line_limit" };
}

function txnDate(created: number, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date(created));
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  return part("year") + "-" + part("month") + "-" + part("day");
}


/** Planner IPC bounds (main.ts enforces the same input limit). */
export const MAX_PLANNER_INPUT_BYTES = 64 * 1024 * 1024;
/** One order's planner input above this goes to durable review instead of blocking the run. */
export const MAX_ORDER_PLAN_BYTES = 2 * 1024 * 1024;
/** Pending orders re-rendered per run. Equal to MAX_UNACKED, so the newest batch normally holds every pending order. */
export const MAX_CARRY = MAX_UNACKED;
const MiB = 1024 * 1024;

const ORDER_PLAN_FIELDS = ["id", "status_id", "payment_status", "default_currency_code", ...MONEY_FIELDS];
const BILLING_PLAN_FIELDS = ["first_name", "last_name", "company", "email", "phone", "street_1", "street_2", "city", "state", "zip", "country_iso2"];
const PRODUCT_PLAN_FIELDS = ["sku", "name", "quantity", "price_ex_tax", "total_ex_tax", "total_tax", "is_refunded", "quantity_refunded",
  "refund_amount", "discounted_total_inc_tax", "applied_discounts", "tax_class_id"];
const present = (source: Record<string, unknown>, fields: string[]) =>
  Object.fromEntries(fields.filter((k) => k in source).map((k) => [k, source[k]]));
const project = (value: unknown, fields: string[]) => (isRecord(value) ? present(value, fields) : value);
/**
 * The planner sees only the fields it reads. Raw BigCommerce product objects (options, images, URLs...) are dropped,
 * which bounds the IPC payload; missing keys stay missing so the planner's own checks are unchanged.
 */
export function plannerOrder(raw: Record<string, unknown>): Record<string, unknown> {
  const order = present(raw, ORDER_PLAN_FIELDS);
  if ("billing_address" in raw) order.billing_address = project(raw.billing_address, BILLING_PLAN_FIELDS);
  order.products = Array.isArray(raw.products) ? raw.products.map((p) => project(p, PRODUCT_PLAN_FIELDS)) : raw.products;
  return order;
}
const nested = (record: Record<string, unknown>, field: string, inner: string) =>
  field in record ? { [field]: isRecord(record[field]) ? present(record[field] as Record<string, unknown>, [inner]) : record[field] } : {};
const plannerCustomer = (c: Record<string, unknown>) =>
  ({ ...present(c, ["Id", "DisplayName", "Active"]), ...nested(c, "PrimaryEmailAddr", "Address"), ...nested(c, "CurrencyRef", "value") });
const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value) ?? "null", "utf8");

const AFTER_EXPORT_REFUND = /refund|cancel/;
type Entry = { ok: true; journal: Journal | null; invalid: boolean; version: string | null } | { ok: false };
type ManifestEntry = { ok: true; manifest: Manifest | null; version: string | null } | { ok: false };
type Open = { previousBatchId: string; rowHash: string };
type Planned = { order: PlannedOrder; oid: string; fingerprint: string; carried: Open | undefined };
type Source = "listed" | "reexport" | "carried";

/**
 * Prepare run (preview or export). Export records each rendered order as `pending` in a durably recorded batch
 * manifest; nothing becomes `exported` here. The running agent uploads the batch folder to Type and then calls
 * runAcknowledge with the returned { batchId, contentSha256 }. Until then every pending order is re-rendered into
 * each new batch, and the newer batch supersedes the older one (which can then no longer be acknowledged).
 */
export async function runSync(input: SyncArgs, ports: Ports) {
  // Acknowledged batches whose orders this run recorded as exported (the acknowledging call did not finish them).
  // Reported in every result, blocked ones included, so the user is told the batch is ready to import.
  const acknowledgedDone = new Set<string>();
  const acknowledgedBatchesCompleted = () => (acknowledgedDone.size ? { acknowledgedBatchesCompleted: [...acknowledgedDone].sort() } : {});
  const blocked = (reason: string, extra: Record<string, unknown> = {}) =>
    ({ status: "blocked" as const, reason, files: [] as OutputFile[], orders: [], ...acknowledgedBatchesCompleted(), ...extra });
  const checked = z.strictObject({
    config: configSchema, mode: z.enum(["preview", "export"]), since: z.string(), until: z.string(), timezone: z.string(),
    afterOrderId: z.string().regex(/^(0|[1-9]\d{0,14})$/).optional(), maxOrders: z.number().int().min(1).max(100).optional(),
    reexportOrderIds: z.array(orderId).max(100).optional(),
  }).safeParse(input);
  if (!checked.success) return blocked("invalid_runtime_input");
  const args = checked.data, config = args.config, limit = args.maxOrders ?? 100;
  const since = Date.parse(args.since), until = Date.parse(args.until);
  if (!Number.isFinite(since) || !Number.isFinite(until) || since > until
      || !/T.*(Z|[+-]\d\d:\d\d)$/.test(args.since) || !/T.*(Z|[+-]\d\d:\d\d)$/.test(args.until))
    return blocked("fixed_timestamp_window_required");
  // Same rule as the input schema, so a timezone accepted there is never blocked here.
  const timezone = storeTimezone(args.timezone);
  if (!timezone) return blocked("supported_store_timezone_required");
  const exportMode = args.mode === "export";
  const bc = "/stores/" + config.storeHash, k = keys(config.storeHash);
  const reexport = new Set(args.reexportOrderIds ?? []);
  // windowId names the approved window; batchId is unique per run, so no two runs share an output folder.
  const windowId = hash([config.storeHash, args.since, args.until, args.afterOrderId ?? "0", [...reexport].sort()]).slice(0, 12);
  const batchId = (exportMode ? "" : "PREVIEW-") + windowId + "-" + randomUUID().replace(/-/g, "").slice(0, 8);

  let qbo: QboSnapshot | null = null;
  if (ports.qbo) {
    const read = await readQuickBooks(ports.qbo, ports.sleep, config, ports.now().slice(0, 10));
    if (!read.ok) return blocked(read.reason);
    qbo = read.value;
  }

  const source = await get(ports, bc + "/v2/orders", {
    min_date_modified: args.since, max_date_modified: args.until, sort: "id:asc",
    page: "1", limit: String(limit), min_id: String(BigInt(args.afterOrderId ?? "0") + 1n),
  });
  const listed = source.status === 204 ? [] : source.data;
  if ((source.status !== 204 && !good(source)) || !Array.isArray(listed) || listed.length > limit) return blocked("orders_read_failed");
  const listedRaw: Record<string, unknown>[] = [];
  let previous = BigInt(args.afterOrderId ?? "0");
  for (const raw of listed) {
    if (!isRecord(raw) || !orderId.safeParse(String(raw.id)).success) return blocked("invalid_source_order_id");
    const modified = typeof raw.date_modified === "string" ? Date.parse(raw.date_modified) : NaN;
    if (!Number.isFinite(modified) || modified < since || modified > until) return blocked("source_outside_approved_window");
    if (BigInt(String(raw.id)) <= previous) return blocked("source_cursor_not_ordered");
    previous = BigInt(String(raw.id));
    listedRaw.push(raw);
  }
  const listedIds = new Set(listedRaw.map((r) => String(r.id)));

  const indexRead = await ports.read(k.index);
  if (!indexRead.ok) return blocked("journal_unavailable");
  const indexParsed = indexRead.value === null ? { schema: 1 as const, orderIds: [] } : indexSchema.safeParse(indexRead.value).data;
  if (!indexParsed) return blocked("invalid_pending_index");
  const pending = new Set(indexParsed.orderIds);
  let indexSaved = stable([...pending].sort());
  let indexVersion = indexRead.version;
  let unsafe = false;
  const writeIndex = async (critical: boolean) => {
    const sorted = [...pending].sort(byId);
    if (!exportMode || stable([...sorted].sort()) === indexSaved) return true;
    const written = await ports.cas(k.index, { schema: 1, orderIds: sorted }, indexVersion);
    if (!written.ok) { if (critical) unsafe = true; return false; }
    indexVersion = written.version ?? null;
    indexSaved = stable([...sorted].sort());
    return true;
  };

  const reviews: ReviewItem[] = [];
  const warnings: string[] = [];
  const outcomes = new Map<string, { orderId: string; outcome: Outcome; reason?: string }>();
  const note = (entry: { orderId: string; outcome: Outcome; reason?: string }) => outcomes.set(entry.orderId, entry);
  const counts: BatchCounts = { prepared: 0, carried: 0, notCarried: 0, alreadyExported: 0, alreadyPosted: 0, confirmedPosted: 0,
    awaitingConfirmation: 0, review: 0, paymentFollowUps: 0 };
  /** A review row without any journal change. */
  const flag = (oid: string, reason: string) => {
    reviews.push({ orderId: oid, reason }); counts.review++; note({ orderId: oid, outcome: "review", reason });
  };

  // Journal records, read once per run and kept current after every write.
  const journals = new Map<string, Entry>();
  const readJournal = async (oid: string): Promise<Entry> => {
    const cached = journals.get(oid);
    if (cached) return cached;
    const stored = await ports.read(k.order(oid));
    let entry: Entry = { ok: false };
    if (stored.ok && stored.value === null) entry = { ok: true, journal: null, invalid: false, version: stored.version };
    else if (stored.ok) {
      const parsed = journalSchema.safeParse(stored.value);
      entry = { ok: true, journal: parsed.success ? parsed.data : null, invalid: !parsed.success, version: stored.version };
    }
    journals.set(oid, entry);
    return entry;
  };
  const save = async (oid: string, value: Journal, version: string | null) => {
    if (!exportMode) { journals.set(oid, { ok: true, journal: value, invalid: false, version }); return true; }
    const stored = await ports.cas(k.order(oid), value, version);
    if (!stored.ok) { unsafe = true; journals.delete(oid); return false; }
    journals.set(oid, { ok: true, journal: value, invalid: false, version: stored.version ?? null });
    return true;
  };
  const toReview = async (oid: string, reason: string, journal: Journal | null, version: string | null, hashValue: string,
    extra: { readFailures?: number } = {}) => {
    flag(oid, reason);
    if (journal?.state === "needs_review" && journal.reason === reason && journal.sourceHash === hashValue
        && journal.readFailures === extra.readFailures) { pending.delete(oid); return; }
    // The index entry goes only once the record has left its earlier state, so a pending order is never orphaned.
    if (await save(oid, { schema: 3, state: "needs_review", sourceHash: hashValue, reason,
      ...(journal?.export ? { export: journal.export } : {}), ...(journal?.qboInvoiceId ? { qboInvoiceId: journal.qboInvoiceId } : {}),
      ...(journal?.paymentFollowUp ? { paymentFollowUp: true } : {}), ...extra, updatedBy: ports.runId }, version)) pending.delete(oid);
  };

  // Batch manifests, read once per run.
  const manifests = new Map<string, ManifestEntry>();
  const readManifest = async (id: string): Promise<ManifestEntry> => {
    const cached = manifests.get(id);
    if (cached) return cached;
    const stored = await ports.read(k.batch(id));
    // A present but unparseable manifest is unknown (batch_state_unknown), never missing: it may be acknowledged.
    const parsed = stored.ok && stored.value !== null ? manifestSchema.safeParse(stored.value).data ?? null : null;
    const entry: ManifestEntry = stored.ok && (stored.value === null || parsed)
      ? { ok: true, manifest: parsed, version: stored.version }
      : { ok: false };
    manifests.set(id, entry);
    return entry;
  };

  // Pending orders. If their batch was acknowledged (and holds exactly their rows) they are exported: record that.
  // Otherwise they are open and are re-rendered into this batch. An unreadable batch state leaves the order untouched.
  const open = new Map<string, Open>();
  const unknownBatch = new Set<string>();
  const resolvePending = async (oid: string) => {
    const entry = await readJournal(oid);
    if (!entry.ok || !entry.journal || entry.journal.state !== "pending" || !entry.journal.pendingBatch) return;
    const journal = entry.journal, held = entry.journal.pendingBatch;
    const manifest = await readManifest(held.batchId);
    if (!manifest.ok) { unknownBatch.add(oid); unsafe = true; return; }
    const done = manifest.manifest ? acknowledgedExport(journal, oid, manifest.manifest, ports.runId) : null;
    if (!done) { open.set(oid, { previousBatchId: held.batchId, rowHash: held.rowHash }); return; }
    open.delete(oid);
    acknowledgedDone.add(held.batchId);
    if (!(await save(oid, done, entry.version))) {
      // Still exported (its batch is acknowledged); the record is completed by a later run, so it stays indexed.
      journals.set(oid, { ok: true, journal: done, invalid: false, version: entry.version });
      warnings.push("acknowledged_export_not_recorded:" + oid);
      return;
    }
    if (done.export?.kind === "sales_receipt") pending.delete(oid);
  };
  for (const oid of [...new Set([...listedIds, ...reexport, ...pending])].sort(byId)) await resolvePending(oid);

  // Candidates: the listed page, explicit re-exports and carried pending orders (read by ID, independent of the window).
  const carryIds = [...open.keys()].filter((oid) => !listedIds.has(oid) && !reexport.has(oid)).sort(byId).slice(0, MAX_CARRY);
  const sourceOf = (oid: string): Source => (listedIds.has(oid) ? "listed" : reexport.has(oid) ? "reexport" : "carried");
  const candidates = listedRaw.map((raw) => ({ raw, oid: String(raw.id) }));
  const unreadable = new Map<string, { reason: string; fingerprint: string }>();
  for (const oid of [...new Set([...reexport, ...carryIds])].filter((id) => !listedIds.has(id)).sort(byId)) {
    const response = await get(ports, bc + "/v2/orders/" + oid);
    if (good(response) && isRecord(response.data) && String(response.data.id) === oid) { candidates.push({ raw: response.data, oid }); continue; }
    if (!open.has(oid)) { flag(oid, "reexport_order_not_found"); continue; }
    unreadable.set(oid, { reason: response.status === 404 ? "pending_order_not_found" : "products_read_failed", fingerprint: hash({ id: oid }) });
  }
  const rawOrders: { raw: Record<string, unknown>; oid: string }[] = [];
  for (const { raw, oid } of candidates) {
    const entry = await readJournal(oid);
    if (!entry.ok) { flag(oid, "journal_read_failed"); unsafe = true; continue; }
    if (unknownBatch.has(oid)) { flag(oid, "batch_state_unknown"); continue; }
    const products = await readProducts(ports, bc, oid);
    const created = typeof raw.date_created === "string" ? Date.parse(raw.date_created) : NaN;
    if (!products.ok) { unreadable.set(oid, { reason: products.reason, fingerprint: sourceHash(raw) }); continue; }
    if (!Number.isFinite(created)) { unreadable.set(oid, { reason: "order_creation_date_invalid", fingerprint: sourceHash(raw) }); continue; }
    rawOrders.push({ raw: { ...raw, products: products.products, qbo_txn_date: txnDate(created, timezone) }, oid });
  }
  for (const oid of unknownBatch) if (!candidates.some((c) => c.oid === oid)) flag(oid, "batch_state_unknown");

  // Planner budget. Each order's projected input is measured: an oversized order goes to durable review; the rest
  // fill the budget in priority order (carried pending orders up to half, then the listed page, then re-exports).
  // A listed order that does not fit ends this run's page early: the cursor continues from the last planned order.
  const catalog = {
    config, customers: qbo ? qbo.customers.map(plannerCustomer) : null, customersComplete: qbo?.customersComplete ?? false,
    vendors: qbo?.vendors ? qbo.vendors.map((v) => present(v, ["DisplayName", "Active"])) : null, vendorsComplete: qbo?.vendorsComplete ?? false,
  };
  const budget = MAX_PLANNER_INPUT_BYTES - bytes(catalog) - MiB;
  if (budget < 8 * MAX_ORDER_PLAN_BYTES) return blocked("qbo_catalog_too_large_for_planner");
  const rank = (oid: string) => ({ carried: 0, listed: 1, reexport: 2 })[sourceOf(oid)];
  const ordered = [...rawOrders].sort((a, b) => rank(a.oid) - rank(b.oid) || byId(a.oid, b.oid));
  const toPlan: Record<string, unknown>[] = [];
  const rawById = new Map<string, Record<string, unknown>>();
  let used = 0, firstDeferred: string | null = null;
  for (const { raw, oid } of ordered) {
    const projected = plannerOrder(raw);
    const size = bytes(projected) + 1;
    const from = sourceOf(oid);
    if (size > MAX_ORDER_PLAN_BYTES) { unreadable.set(oid, { reason: "order_too_large_for_planner", fingerprint: sourceHash(raw) }); continue; }
    if (from === "carried" && used + size > budget / 2) continue;
    if (from === "listed" && (firstDeferred !== null || used + size > budget)) { firstDeferred ??= oid; continue; }
    if (from === "reexport" && used + size > budget) { flag(oid, "reexport_deferred_batch_size"); continue; }
    used += size;
    toPlan.push(projected);
    rawById.set(oid, raw);
  }
  // Listed orders from the first deferred one on are left for the next run (nothing is recorded for them now).
  const deferred = (oid: string) => firstDeferred !== null && listedIds.has(oid) && byId(oid, firstDeferred) >= 0;
  for (const oid of [...unreadable.keys()]) if (deferred(oid)) unreadable.delete(oid);
  let nextAfterOrderId = listed.length === limit ? String((listed.at(-1) as Record<string, unknown>).id) : null;
  if (firstDeferred !== null) {
    const before = [...listedIds].filter((oid) => byId(oid, firstDeferred as string) < 0).sort(byId).at(-1);
    nextAfterOrderId = before ?? args.afterOrderId ?? "0";
    if (!before) unsafe = true;
    warnings.push("planner_budget_page_shortened");
  }

  const planned = await ports.plan({ ...catalog, orders: toPlan });
  const plan = planned.ok ? planSchema.safeParse(planned.value) : null;
  if (!plan?.success) return blocked("planner_failed");
  const planFor = new Map(plan.data.orders.map((o) => [o.orderId ?? "", o]));

  const settledOf = (j: Journal | null) => Boolean(j && j.state !== "pending"
    && (j.state === "confirmed_posted" || j.qboInvoiceId || (j.export && j.export.exportCount > 0)));
  const postedOf = (j: Journal | null) => Boolean(j && (j.state === "confirmed_posted" || j.qboInvoiceId));
  const decidedIds = [...new Set([...rawById.keys(), ...unreadable.keys()])].sort(byId);

  // The invoice list is advisory: if it misses an invoice the journal records as posted and a targeted read finds
  // it, the list skipped rows, so this run treats it as incomplete (unseen invoices are unknown, not unposted).
  if (qbo && ports.qbo && qbo.invoicesComplete) {
    for (const oid of decidedIds) {
      const entry = await readJournal(oid);
      const journal = entry.ok ? entry.journal : null;
      const doc = journal?.export?.docNumber;
      if (!journal?.qboInvoiceId || !doc || qbo.invoices.has(doc)) continue;
      if ((await invoiceById(ports.qbo, ports.sleep, journal.qboInvoiceId)) !== "present") continue;
      qbo.invoicesComplete = false;
      qbo.warnings.push("QuickBooks invoice list missed invoice " + journal.qboInvoiceId + " that exists; unseen invoices are treated as unknown this run.");
      break;
    }
  }

  // Before any pending order is rewritten, its earlier batch is superseded (compare-and-swap on the manifest), so it
  // can never also be acknowledged. If that batch was acknowledged meanwhile, its orders are exported instead.
  const superseded = new Set<string>();
  for (const id of [...new Set(decidedIds.filter((oid) => open.has(oid)).map((oid) => (open.get(oid) as Open).previousBatchId))].sort()) {
    const entry = await readManifest(id);
    const holders = [...open.entries()].filter(([, o]) => o.previousBatchId === id).map(([oid]) => oid);
    if (!entry.ok) { for (const oid of holders) { open.delete(oid); unknownBatch.add(oid); unsafe = true; } continue; }
    if (!entry.manifest || entry.manifest.status !== "prepared") { superseded.add(id); continue; }
    if (!exportMode) { superseded.add(id); continue; }
    const written = await ports.cas(k.batch(id), { ...entry.manifest, status: "superseded", supersededBy: batchId }, entry.version);
    if (written.ok) { manifests.set(id, { ok: true, manifest: { ...entry.manifest, status: "superseded", supersededBy: batchId }, version: written.version ?? null }); superseded.add(id); continue; }
    manifests.delete(id);
    const again = await readManifest(id);
    if (again.ok && again.manifest && again.manifest.status !== "prepared") {
      if (again.manifest.status === "acknowledged") for (const oid of holders) await resolvePending(oid);
      else superseded.add(id);
      continue;
    }
    for (const oid of holders) { open.delete(oid); unknownBatch.add(oid); unsafe = true; }
  }

  const exports: Planned[] = [];
  let newPending = 0;
  for (const oid of decidedIds) {
    if (unknownBatch.has(oid)) { flag(oid, "batch_state_unknown"); continue; }
    const entry = await readJournal(oid);
    if (!entry.ok) { flag(oid, "journal_read_failed"); unsafe = true; continue; }
    const journal = entry.journal, version = entry.version;
    const lost = unreadable.get(oid);
    const raw = rawById.get(oid);
    const fingerprint = lost ? lost.fingerprint : raw ? sourceHash(raw) : hash({ id: oid });
    // Never overwritten: the unreadable record itself keeps the order in review on every run (never rendered as new).
    if (entry.invalid) { flag(oid, "journal_record_invalid"); continue; }
    const carried = open.get(oid);
    const settled = settledOf(journal), posted = postedOf(journal), requested = reexport.has(oid);
    const holdsWindow = listedIds.has(oid);
    if (lost) {
      if (journal && settled) {
        // An exported or posted order keeps its record: without products nothing can be re-derived.
        if (lost.reason !== "products_read_failed") { flag(oid, lost.reason); continue; }
        const failures = (journal.readFailures ?? 0) + 1;
        await save(oid, { ...journal, readFailures: failures, updatedBy: ports.runId }, version);
        if (failures >= MAX_READ_FAILURES) { flag(oid, "products_read_failed_repeatedly"); continue; }
        flag(oid, "products_read_failed"); if (holdsWindow) unsafe = true;
        continue;
      }
      const keepHash = carried && journal ? journal.sourceHash : fingerprint;
      if (lost.reason !== "products_read_failed") { await toReview(oid, lost.reason, journal, version, keepHash); continue; }
      const failures = (journal?.readFailures ?? 0) + 1;
      if (failures >= MAX_READ_FAILURES) {
        await toReview(oid, "products_read_failed_repeatedly", journal, version, keepHash, { readFailures: failures }); continue;
      }
      if (carried && journal) {
        // Still pending (durable); it is not in this batch and is retried next run.
        flag(oid, "pending_order_read_failed");
        await save(oid, { ...journal, readFailures: failures, updatedBy: ports.runId }, version);
        continue;
      }
      // Possibly transient: count the failure and hold the cursor so the window is retried.
      flag(oid, "products_read_failed"); unsafe = true;
      await save(oid, journal ? { ...journal, readFailures: failures, updatedBy: ports.runId }
        : { schema: 3, state: "needs_review", sourceHash: fingerprint, reason: "products_read_failed", readFailures: failures, updatedBy: ports.runId }, version);
      continue;
    }
    const result = planFor.get(oid);
    // A posting recorded in the journal is final; QuickBooks being unavailable never re-opens it.
    if (requested && posted) { flag(oid, "reexport_blocked_already_in_qbo"); continue; }
    const delivered = Boolean(journal?.export && journal.export.exportCount > 0);
    const changedReason = delivered ? "changed_after_export" : "changed_after_posted_in_qbo";
    const unchanged = () => {
      if (delivered) { note({ orderId: oid, outcome: "already_exported" }); counts.alreadyExported++; }
      else { note({ orderId: oid, outcome: "already_posted" }); counts.alreadyPosted++; }
    };

    if (journal && settled && !requested) {
      if (!result || result.status === "review") {
        const reason = result?.status === "review" ? result.reason : "invalid_plan";
        if (AFTER_EXPORT_REFUND.test(reason)) { await toReview(oid, "refund_or_cancellation_after_export", journal, version, fingerprint); continue; }
        if (journal.sourceHash !== fingerprint) { await toReview(oid, changedReason, journal, version, fingerprint); continue; }
        if (journal.state === "needs_review") { await toReview(oid, journal.reason ?? reason, journal, version, fingerprint); continue; }
        unchanged(); continue;
      }
      const order = result as PlannedOrder;
      if (journal.sourceHash !== fingerprint) { await toReview(oid, changedReason, journal, version, fingerprint); continue; }
      if (journal.state === "needs_review") { await toReview(oid, journal.reason ?? changedReason, journal, version, fingerprint); continue; }
      // Self-heal a lost index update so the export is still confirmed later.
      if (journal.state === "exported" && journal.export?.kind !== "sales_receipt" && pending.size < MAX_PENDING) pending.add(oid);
      const { readFailures: _failures, ...rest } = journal;
      let next: Journal | null = journal.readFailures ? { ...rest, updatedBy: ports.runId } : null;
      // An unpaid invoice that was later paid needs a Receive payment in QuickBooks. Reported once.
      if (journal.export?.kind === "invoice" && order.kind !== "invoice" && !journal.paymentFollowUp) {
        reviews.push({ orderId: oid, reason: "payment_captured_after_invoice_export" }); counts.paymentFollowUps++;
        next = { ...(next ?? journal), paymentFollowUp: true, updatedBy: ports.runId };
      }
      if (next) await save(oid, next, version);
      unchanged(); continue;
    }
    // New, pending (carried) or explicitly re-exported orders.
    if (!result || result.status === "review") {
      await toReview(oid, result?.status === "review" ? result.reason : "invalid_plan", journal, version, fingerprint); continue;
    }
    const order = result as PlannedOrder;
    if (order.kind !== "sales_receipt" && qbo) {
      const check = postedCheck(qbo, order.docNumber, order.totalCents, order.customer.name);
      if (check.result === "same") {
        // Already in QuickBooks (for a pending order: someone imported an unacknowledged batch). Never rendered again.
        if (await save(oid, { schema: 3, state: "confirmed_posted", sourceHash: fingerprint, reason: "already_posted_in_qbo", qboInvoiceId: check.id,
          export: journal?.export ?? { kind: order.kind, docNumber: order.docNumber, customerName: order.customer.name, totalCents: order.totalCents,
            currency: order.currency, batchId: journal?.pendingBatch?.batchId ?? batchId, exportCount: 0 }, updatedBy: ports.runId }, version)) pending.delete(oid);
        if (requested) { flag(oid, "reexport_blocked_already_in_qbo"); continue; }
        note({ orderId: oid, outcome: "already_posted" }); counts.alreadyPosted++; continue;
      }
      if (check.result === "different") { await toReview(oid, "docnumber_already_used_in_qbo", journal, version, fingerprint); continue; }
      if (check.result === "duplicate") { await toReview(oid, "duplicate_docnumber_in_qbo", journal, version, fingerprint); continue; }
      // Incomplete or inconsistent invoice paging: the invoice may exist. Unknown is never "not posted".
      if (check.result === "unknown") { await toReview(oid, "posted_status_unknown", journal, version, fingerprint); continue; }
    }
    if (!carried) {
      const backlog = (reason: string) => (requested ? flag(oid, reason) : toReview(oid, reason, journal, version, fingerprint));
      if (open.size + newPending >= MAX_UNACKED) { await backlog("unacknowledged_backlog_full"); continue; }
      const reserved = exports.filter((e) => !pending.has(e.oid)).length;
      if (!pending.has(oid) && pending.size + reserved >= MAX_PENDING) { await backlog("confirmation_backlog_full"); continue; }
      newPending++;
    }
    exports.push({ order, oid, fingerprint, carried });
  }

  // Confirm earlier exports that QuickBooks now shows as posted.
  const rendering = new Set(exports.map((e) => e.oid));
  for (const oid of [...pending].sort(byId)) {
    if (rendering.has(oid) || unknownBatch.has(oid)) continue;
    const entry = await readJournal(oid);
    if (!entry.ok) { unsafe = true; continue; }
    const journal = entry.journal;
    if (journal?.state === "pending") continue;
    if (!journal || journal.state !== "exported" || !journal.export || journal.export.kind === "sales_receipt") { pending.delete(oid); continue; }
    const info = journal.export;
    if (!qbo) { counts.awaitingConfirmation++; continue; }
    const check = postedCheck(qbo, info.docNumber, info.totalCents, info.customerName);
    if (check.result === "same") {
      const { readFailures: _failures, ...rest } = journal;
      if (await save(oid, { ...rest, state: "confirmed_posted", qboInvoiceId: check.id, updatedBy: ports.runId }, entry.version)) {
        pending.delete(oid); counts.confirmedPosted++; note({ orderId: oid, outcome: "confirmed_posted" });
      }
    } else if (check.result === "different" || check.result === "duplicate") {
      const reason = check.result === "different" ? "posted_invoice_mismatch" : "duplicate_docnumber_in_qbo";
      await toReview(oid, reason, journal, entry.version, journal.sourceHash);
    } else {
      counts.awaitingConfirmation++;
      note({ orderId: oid, outcome: "awaiting_confirmation" });
    }
  }

  // Record every rendered order as pending in this batch. The index lists new orders first, so a crash anywhere
  // later leaves them findable (they are carried into the next batch). Nothing here makes an order "exported".
  const claimed: Planned[] = [];
  if (!exportMode) claimed.push(...exports);
  else if (exports.length) {
    const added = exports.filter((e) => !pending.has(e.oid)).map((e) => e.oid);
    for (const oid of added) pending.add(oid);
    const indexed = await writeIndex(true);
    if (!indexed) for (const oid of added) pending.delete(oid);
    for (const e of exports) {
      if (!indexed && added.includes(e.oid)) { flag(e.oid, "pending_index_update_failed"); continue; }
      const current = journals.get(e.oid);
      const journal = current?.ok ? current.journal : null;
      const next: Journal = { schema: 3, state: "pending", sourceHash: e.fingerprint, ...(journal?.export ? { export: journal.export } : {}),
        pendingBatch: { kind: e.order.kind, docNumber: e.order.docNumber, customerName: e.order.customer.name, totalCents: e.order.totalCents,
          currency: e.order.currency, batchId, rowHash: hash(e.order), runId: ports.runId }, updatedBy: ports.runId };
      if (current?.ok && (await save(e.oid, next, current.version))) claimed.push(e);
      else { flag(e.oid, "order_claim_conflict"); unsafe = true; }
    }
  }
  for (const e of claimed) note({ orderId: e.oid, outcome: "pending_acknowledgement" });
  counts.prepared = claimed.length;
  const carriedOut: CarriedOrder[] = claimed.filter((e) => e.carried)
    .map((e) => ({ orderId: e.oid, previousBatchId: (e.carried as Open).previousBatchId, sameRows: (e.carried as Open).rowHash === hash(e.order) }));
  counts.carried = carriedOut.length;
  const claimedIds = new Set(claimed.map((e) => e.oid));
  const notCarried = [...pending].filter((oid) => {
    const entry = journals.get(oid);
    return !claimedIds.has(oid) && entry?.ok === true && entry.journal?.state === "pending";
  }).sort(byId);
  counts.notCarried = notCarried.length;
  const supersedes = [...new Set([...superseded, ...carriedOut.map((c) => c.previousBatchId)])].sort();

  const files = renderBatch(config, claimed.map((e) => e.order), reviews, {
    batchId, mode: args.mode, since: args.since, until: args.until, timezone, counts, carried: carriedOut, supersedes, notCarried,
    acknowledgedCompleted: [...acknowledgedDone].sort(),
    qbo: { connected: qbo !== null, customerLookup: qbo ? (qbo.customersComplete ? "complete" : "incomplete") : "not_connected",
      postedCheck: qbo ? (qbo.invoicesComplete ? "complete" : "incomplete") : "not_connected", warnings: qbo?.warnings ?? [] },
  });
  const manifestOrders = claimed.map((e) => ({ orderId: e.oid, rowHash: hash(e.order), kind: e.order.kind })).sort((a, b) => byId(a.orderId, b.orderId));
  const fileMeta = files.map(({ name, bytes: size, sha256, rows }) => ({ name, bytes: size, sha256, rows }));
  const sha = contentSha256(batchId, manifestOrders, fileMeta);
  const acknowledge = exportMode && claimed.length ? { batchId, contentSha256: sha } : null;
  const manifest: Manifest = { schema: 1, batchId, windowId, runId: ports.runId, createdAt: ports.now(), status: "prepared",
    orders: manifestOrders, files: fileMeta, contentSha256: sha, supersedes };
  const published = acknowledge ? [...files, makeFile("manifest.json", JSON.stringify({
    note: "Batch manifest. Upload every file in this folder to Type, then acknowledge the batch with acknowledgeBatch below. Do not import before the acknowledgement succeeds.",
    acknowledgeBatch: acknowledge, batchId, orders: manifestOrders, files: fileMeta, supersedes }, null, 2) + "\n", manifestOrders.length)] : files;

  let manifestVersion: string | null = null;
  if (acknowledge) {
    const recorded = await ports.cas(k.batch(batchId), manifest, null);
    // Without a manifest the batch can never be acknowledged; its orders stay pending and are carried next run.
    if (!recorded.ok) return blocked("batch_manifest_not_recorded", { batchId, cursorAdvanceSafe: false, pendingOrderIds: [...claimedIds].slice(0, 100) });
    manifestVersion = recorded.version ?? null;
  }
  const delivery = await ports.deliver(published, batchId);
  if (!delivery.ok) {
    // No folder: the manifest is voided (best effort) so it cannot be acknowledged; the orders are carried next run.
    if (acknowledge) await ports.cas(k.batch(batchId), { ...manifest, status: "void" }, manifestVersion);
    return blocked("output_delivery_failed", { batchId, deliveryError: delivery.error, cursorAdvanceSafe: false,
      pendingOrderIds: [...claimedIds].slice(0, 100) });
  }
  if (!(await writeIndex(false))) warnings.push("pending_index_update_failed");

  return {
    status: exportMode ? ("prepared" as const) : ("preview" as const), batchId, windowId, counts, acknowledge,
    supersedes, carried: carriedOut, notCarried, ...acknowledgedBatchesCompleted(),
    orders: [...outcomes.values()].sort((a, b) => byId(a.orderId, b.orderId)), reviewQueue: [...reviews], files: published,
    delivery: { directory: delivery.directory },
    qbo: { connected: qbo !== null, customersComplete: qbo?.customersComplete ?? null, invoicesComplete: qbo?.invoicesComplete ?? null,
      warnings: qbo?.warnings ?? [] },
    cursorAdvanceSafe: exportMode && !unsafe, warnings, nextAfterOrderId,
    window: { since: args.since, until: args.until },
  };
}
