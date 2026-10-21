/**
 * r7 regression suite for the acknowledgement design (independent QA r6 findings N1–N9).
 * Invariants: an order becomes exported only through an acknowledged batch; no order is ever lost (it is always in
 * the newest unacknowledged batch, acknowledged, posted, pending for the next batch, or in review); no order is in
 * two acknowledged batches.
 */
import { describe, expect, test } from "bun:test";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runAcknowledge } from "./ack";
import { boundOutput, RESULT_BUDGET_BYTES } from "./main";
import type { Fixture } from "./preview";
import { listAll, type QboCall } from "./qbo";
import { phoneCell, PREVIEW_PREFIX } from "./render";
import { runPoll } from "./scheduler";
import { MAX_ORDER_PLAN_BYTES, plannerOrder, runSync, type Ports } from "./sync";
import {
  bigOrder, execute, fakeType, fixture, invoice, manifest, order, ports, product, state, WINDOW, type Journal, type Row,
} from "./test_support";

type Out = Record<string, any>;
const run = async (f: Fixture, p: Ports, extra: Row = {}) => (await runSync({ config: f.config, mode: "export", ...WINDOW, ...extra }, p)) as Out;
const ack = async (f: Fixture, p: Ports, value: { batchId: string; contentSha256: string }) => (await runAcknowledge({ config: f.config, ...value }, p)) as Out;
const fileOf = (r: Out, name: string) => (r.files as { name: string; content: string }[]).find((x) => x.name === name || x.name === PREVIEW_PREFIX + name)?.content;
const reasonOf = (r: Out, oid: string) => (r.reviewQueue as { orderId: string; reason: string }[]).find((x) => x.orderId === oid)?.reason;
const outcomeOf = (r: Out, oid: string) => (r.orders as { orderId: string; outcome: string }[]).find((x) => x.orderId === oid)?.outcome;

describe("acknowledgement protocol", () => {
  test("export records orders as pending only; the acknowledgement must match the recorded manifest exactly", async () => {
    const f = fixture([order(9001), order(9002, { payment_status: "captured" })], { qbo: null });
    const journal: Journal = new Map();
    const r = await run(f, ports(f, journal));
    expect(r.status).toBe("prepared");
    expect(state(journal, "9001")).toMatchObject({ state: "pending", pendingBatch: { batchId: r.batchId } });
    expect(manifest(journal, r.batchId)).toMatchObject({ status: "prepared", contentSha256: r.acknowledge.contentSha256,
      orders: [{ orderId: "9001", kind: "invoice" }, { orderId: "9002", kind: "sales_receipt" }] });
    const listed = JSON.parse(fileOf(r, "manifest.json") ?? "{}");
    expect(listed.acknowledgeBatch).toEqual(r.acknowledge);
    const p = ports(f, journal);
    expect(await ack(f, p, { batchId: r.batchId, contentSha256: "0".repeat(64) })).toMatchObject({ status: "blocked", reason: "acknowledgement_content_mismatch" });
    expect(await ack(f, p, { batchId: "nosuchbatch-1", contentSha256: r.acknowledge.contentSha256 })).toMatchObject({ status: "blocked", reason: "batch_not_found" });
    expect(await ack(f, p, { batchId: "PREVIEW-x-1", contentSha256: r.acknowledge.contentSha256 })).toMatchObject({ reason: "preview_batches_cannot_be_acknowledged" });
    // A tampered manifest (rows no longer match its own hash) is refused.
    const stored = journal.get("bcqbo/demo/batch/" + r.batchId)!;
    journal.set("bcqbo/demo/batch/" + r.batchId, { value: { ...(stored.value as Row), orders: [(stored.value as Row).orders[0]] }, version: stored.version });
    expect(await ack(f, p, r.acknowledge)).toMatchObject({ status: "blocked", reason: "batch_manifest_invalid" });
    journal.set("bcqbo/demo/batch/" + r.batchId, stored);
    expect(state(journal, "9001")?.state).toBe("pending");
    const done = await ack(f, p, r.acknowledge);
    expect(done).toMatchObject({ status: "acknowledged", counts: { orders: 2, invoices: 1, salesReceipts: 1, recordsCompleted: 2 } });
    expect(state(journal, "9001")).toMatchObject({ state: "exported", export: { batchId: r.batchId, exportCount: 1 } });
    expect((journal.get("bcqbo/demo/pending")?.value as Row).orderIds).toEqual(["9001"]);
    expect(JSON.stringify(done).length).toBeLessThan(2000);
  });

  test("only the newest batch can be acknowledged; a superseded batch is refused, so no order is in two acknowledged batches", async () => {
    const f = fixture([order(9011), order(9012)], { qbo: null });
    const journal: Journal = new Map();
    const x = await run(f, ports(f, journal));
    const y = await run(f, ports(f, journal));
    expect(y.supersedes).toEqual([x.batchId]);
    expect(y.counts).toMatchObject({ prepared: 2, carried: 2 });
    expect(fileOf(y, "summary.md")).toContain(`This batch supersedes unacknowledged batch ${x.batchId} — import only the newest batch.`);
    expect(manifest(journal, x.batchId)).toMatchObject({ status: "superseded", supersededBy: y.batchId });
    expect(await ack(f, ports(f, journal), x.acknowledge)).toMatchObject({ status: "blocked", reason: "batch_superseded" });
    expect(await ack(f, ports(f, journal), y.acknowledge)).toMatchObject({ status: "acknowledged" });
    expect(await ack(f, ports(f, journal), x.acknowledge)).toMatchObject({ status: "blocked", reason: "batch_superseded" });
    const acknowledged = [...journal.entries()].filter(([key, v]) => key.includes("/batch/") && (v.value as Row).status === "acknowledged");
    expect(acknowledged).toHaveLength(1);
  });

  test("an order that left the batch (pending elsewhere) blocks the acknowledgement without any write", async () => {
    const f = fixture([order(9021), order(9022)], { qbo: null });
    const journal: Journal = new Map();
    const x = await run(f, ports(f, journal));
    const record = journal.get("bcqbo/demo/order/9022")!;
    journal.set("bcqbo/demo/order/9022", { value: { ...(record.value as Row), pendingBatch: { ...(record.value as Row).pendingBatch, batchId: "other-1" } }, version: record.version });
    const before = JSON.stringify([...journal.entries()]);
    expect(await ack(f, ports(f, journal), x.acknowledge)).toMatchObject({ status: "blocked", reason: "batch_not_latest_for_orders", orderIds: ["9022"] });
    expect(JSON.stringify([...journal.entries()])).toBe(before);
  });

  test("a crash after the acknowledgement commit: repeating it is idempotent and the next run completes the records instead of re-exporting", async () => {
    const f = fixture([order(9031), order(9032)], { qbo: null });
    const journal: Journal = new Map();
    const x = await run(f, ports(f, journal));
    const crashing = ports(f, journal, (p) => ({ cas: async (key, value, version) => {
      if (key.includes("/order/")) throw new Error("crash after commit");
      return p.cas(key, value, version);
    } }));
    await expect(ack(f, crashing, x.acknowledge)).rejects.toThrow("crash after commit");
    expect(manifest(journal, x.batchId)?.status).toBe("acknowledged");
    expect(state(journal, "9031")?.state).toBe("pending");
    const next = await run(f, ports(f, journal));
    expect(next.counts).toMatchObject({ prepared: 0, carried: 0, alreadyExported: 2 });
    expect(next.acknowledge).toBeNull();
    expect(state(journal, "9031")).toMatchObject({ state: "exported", export: { batchId: x.batchId } });
    expect(await ack(f, ports(f, journal), x.acknowledge)).toMatchObject({ status: "acknowledged", alreadyAcknowledged: true });
  });

  test("an acknowledgement racing a new run: the run's supersede loses, so it treats those orders as exported", async () => {
    const f = fixture([order(9041)], { qbo: null });
    const journal: Journal = new Map();
    const x = await run(f, ports(f, journal));
    const racing = ports(f, journal, (p) => ({ cas: async (key, value, version) => {
      if (key.endsWith("/batch/" + x.batchId) && (value as Row).status === "superseded") await ack(f, ports(f, journal), x.acknowledge);
      return p.cas(key, value, version);
    } }));
    const y = await run(f, racing);
    expect(y.counts).toMatchObject({ prepared: 0, alreadyExported: 1 });
    expect(manifest(journal, x.batchId)?.status).toBe("acknowledged");
    expect(state(journal, "9041")).toMatchObject({ state: "exported", export: { batchId: x.batchId, exportCount: 1 } });
  });

  test("N1: a pending write applied but reported failed leaves the order out of the batch; acknowledging that batch does not export it; it is carried next", async () => {
    const f = fixture([order(9051), order(9052)], { qbo: null });
    const journal: Journal = new Map();
    const ambiguous = ports(f, journal, (p) => ({ cas: async (key, value, version) => {
      const stored = await p.cas(key, value, version);
      return key.endsWith("/order/9052") && (value as Row).state === "pending" ? { ok: false, error: "state_write_failed" } : stored;
    } }));
    const x = await run(f, ambiguous);
    expect(reasonOf(x, "9052")).toBe("order_claim_conflict");
    expect(fileOf(x, "qbo-invoices-import.csv")).not.toContain("BC-9052");
    expect(state(journal, "9052")).toMatchObject({ state: "pending", pendingBatch: { batchId: x.batchId } });
    expect(await ack(f, ports(f, journal), x.acknowledge)).toMatchObject({ status: "acknowledged", counts: { orders: 1 } });
    const next = await run(f, ports(f, journal));
    expect(outcomeOf(next, "9052")).toBe("pending_acknowledgement");
    expect(fileOf(next, "qbo-invoices-import.csv")).toContain("BC-9052");
    expect(state(journal, "9051")?.state).toBe("exported");
    await ack(f, ports(f, journal), next.acknowledge);
    expect(state(journal, "9052")).toMatchObject({ state: "exported", export: { batchId: next.batchId } });
  });

  test("N3: an outage after the batch is written: the acknowledgement fails, nothing is exported, and the next batch carries the orders", async () => {
    const f = fixture([order(9061), order(9062, { payment_status: "captured" })], { qbo: null });
    const journal: Journal = new Map();
    let down = false;
    const flaky = ports(f, journal, (p) => ({ cas: async (key, value, version) => (down ? { ok: false, error: "state_write_failed" } : p.cas(key, value, version)) }));
    const x = await run(f, flaky);
    expect(x.status).toBe("prepared");
    down = true;
    expect(await ack(f, flaky, x.acknowledge)).toMatchObject({ status: "blocked", reason: "acknowledgement_not_recorded" });
    expect(state(journal, "9061")?.state).toBe("pending");
    expect(state(journal, "9062")?.state).toBe("pending");
    down = false;
    const y = await run(f, flaky);
    expect(y.counts).toMatchObject({ prepared: 2, carried: 2 });
    expect(fileOf(y, "sales-receipts-worksheet.csv")).toContain("BC-9062");
  });

  test("a pending order whose move to review cannot be written stays pending and indexed (never orphaned)", async () => {
    const f = fixture([order(9081, { date_modified: "2026-10-01T00:00:00Z" })], { qbo: null });
    const journal: Journal = new Map();
    await run(f, ports(f, journal), { reexportOrderIds: ["9081"] });
    const refunded = fixture([order(9081, { date_modified: "2026-10-01T00:00:00Z", status_id: 5 })], { qbo: null });
    const failing = ports(refunded, journal, (p) => ({ cas: async (key, value, version) =>
      (key.endsWith("/order/9081") ? { ok: false, error: "state_write_failed" } : p.cas(key, value, version)) }));
    const r = await run(refunded, failing);
    expect(r.cursorAdvanceSafe).toBe(false);
    expect(state(journal, "9081")?.state).toBe("pending");
    expect((journal.get("bcqbo/demo/pending")?.value as Row).orderIds).toContain("9081");
    const healed = await run(refunded, ports(refunded, journal));
    expect(reasonOf(healed, "9081")).toBe("refund_or_cancellation_requires_reconciliation");
    expect(state(journal, "9081")?.state).toBe("needs_review");
    expect((journal.get("bcqbo/demo/pending")?.value as Row).orderIds).not.toContain("9081");
  });

  test("a pending order that QuickBooks already shows (an unacknowledged batch was imported anyway) is confirmed, never rendered again", async () => {
    const offline = fixture([order(9071), order(9072)], { qbo: null });
    const journal: Journal = new Map();
    await run(offline, ports(offline, journal));
    const online = fixture([order(9071), order(9072)], { qbo: { invoices: [invoice("BC-9071", "25.00", "Test Buyer9071", "71")] } });
    const y = await run(online, ports(online, journal));
    expect(outcomeOf(y, "9071")).toBe("already_posted");
    expect(fileOf(y, "qbo-invoices-import.csv")).not.toContain("BC-9071");
    expect(fileOf(y, "qbo-invoices-import.csv")).toContain("BC-9072");
    expect(state(journal, "9071")).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "71" });
  });

  test("the unacknowledged backlog is bounded: beyond 200 pending orders new orders go to durable review and polling advances", async () => {
    const day = (d: string, from: number, n: number) => Array.from({ length: n }, (_, i) => order(from + i, { date_modified: d + "T12:00:00Z", date_created: d + "T12:00:00Z" }));
    const all = [...day("2026-10-15", 20000, 100), ...day("2026-10-16", 21000, 100), ...day("2026-10-17", 22000, 1)];
    const f = fixture(all, { qbo: null });
    const journal: Journal = new Map();
    const a = await run(f, ports(f, journal), { since: "2026-10-15T00:00:00Z", until: "2026-10-15T23:59:59Z" });
    const b = await run(f, ports(f, journal), { since: "2026-10-16T00:00:00Z", until: "2026-10-16T23:59:59Z" });
    expect(a.counts.prepared).toBe(100);
    expect(b.counts).toMatchObject({ prepared: 200, carried: 100 });
    const c = await run(f, ports(f, journal), { since: "2026-10-17T00:00:00Z", until: "2026-10-17T23:59:59Z" });
    expect(reasonOf(c, "22000")).toBe("unacknowledged_backlog_full");
    expect(state(journal, "22000")).toMatchObject({ state: "needs_review", reason: "unacknowledged_backlog_full" });
    expect(c.cursorAdvanceSafe).toBe(true);
    expect(c.counts).toMatchObject({ prepared: 200, carried: 200 });
  }, 60000);
});

/** Crash matrix (adapted from independent QA r6 crash_matrix.ts) for the prepare and acknowledge steps. */
describe("crash matrix: no order lost, none in two acknowledged batches, exported only via an acknowledged batch", () => {
  type Mode = "throw_before" | "throw_after" | "ambiguous" | "fail";
  type Inject = { phase: "export" | "ack"; casAt?: number; mode?: Mode; deliver?: "throw_before" | "throw_after" | "fail"; outageAfterManifest?: boolean };
  const IDS = ["8001", "8002", "8003"];
  const crashFixture = () => fixture([order(8001), order(8002), order(8003, { payment_status: "captured" })], { qbo: null });

  function world(f: Fixture, journal: Journal, folders: Map<string, { name: string; content: string }[]>, inject?: Inject) {
    let n = 0, outage = false;
    const p = ports(f, journal, (inner) => ({
      cas: async (key, value, version) => {
        const index = ++n;
        if (outage) return { ok: false, error: "state_write_failed" };
        const mode = inject?.casAt === index ? inject.mode : undefined;
        if (mode === "throw_before") throw new Error("crash before write " + index);
        if (mode === "fail") return { ok: false, error: "state_write_failed" };
        const stored = await inner.cas(key, value, version);
        if (inject?.outageAfterManifest && key.includes("/batch/")) outage = true;
        if (mode === "throw_after") throw new Error("crash after write " + index);
        if (mode === "ambiguous") return { ok: false, error: "state_write_failed" };
        return stored;
      },
      deliver: async (files, batchId) => {
        if (inject?.deliver === "throw_before") throw new Error("crash before folder");
        if (inject?.deliver === "fail") return { ok: false as const, error: "output_write_failed" };
        folders.set(batchId, files.map((x) => ({ name: x.name, content: x.content })));
        if (inject?.deliver === "throw_after") throw new Error("crash after folder");
        return { ok: true as const, directory: "mem:" + batchId };
      },
    }));
    return { p, count: () => n };
  }
  const attempt = async <T,>(fn: () => Promise<T>) => { try { return await fn(); } catch { return { status: "crashed" } as T; } };

  async function scenario(inject: Inject) {
    const f = crashFixture();
    const journal: Journal = new Map();
    const folders = new Map<string, { name: string; content: string }[]>();
    const handed = new Set<string>();
    const cycle = async (faulty: boolean) => {
      const exportWorld = world(f, journal, folders, faulty && inject.phase === "export" ? inject : undefined);
      const r = await attempt(() => run(f, exportWorld.p));
      if (r.status !== "prepared" || !r.acknowledge) return;
      // The agent uploads every file of the returned folder, then acknowledges; it retries a failed acknowledgement once.
      const ackWorld = world(f, journal, folders, faulty && inject.phase === "ack" ? inject : undefined);
      let a = await attempt(() => ack(f, ackWorld.p, r.acknowledge));
      if (a.status !== "acknowledged") a = await attempt(() => ack(f, world(f, journal, folders).p, r.acknowledge));
      if (a.status === "acknowledged") handed.add(r.batchId);
    };
    await cycle(true);
    // After the faulty cycle: anything exported is in a batch the agent acknowledged (and therefore uploaded).
    for (const oid of IDS) {
      const j = state(journal, oid);
      expect(["pending", "exported", "needs_review", undefined]).toContain(j?.state);
      if (j?.state === "exported") expect(handed.has(j.export.batchId)).toBe(true);
    }
    await cycle(false);
    await cycle(false);
    const acknowledged = [...journal.entries()].filter(([key, v]) => key.includes("/batch/") && (v.value as Row).status === "acknowledged")
      .map(([, v]) => v.value as Row);
    for (const m of acknowledged) expect(handed.has(m.batchId)).toBe(true);
    for (const oid of IDS) {
      const j = state(journal, oid);
      expect(j?.state).toBe("exported");
      const batch = j?.export.batchId as string;
      expect(handed.has(batch)).toBe(true);
      expect((folders.get(batch) ?? []).some((x) => x.name !== "summary.md" && x.name !== "manifest.json" && !x.name.startsWith("review-queue")
        && x.content.includes("BC-" + oid + ","))).toBe(true);
      expect(acknowledged.filter((m) => (m.orders as Row[]).some((o) => o.orderId === oid))).toHaveLength(1);
    }
  }

  test("every Type data write of the prepare and acknowledge steps x 4 fault modes, every folder fault, and an outage", async () => {
    const probe = world(crashFixture(), new Map(), new Map());
    const r = await run(crashFixture(), probe.p);
    const exportWrites = probe.count();
    const ackProbe = world(crashFixture(), new Map(), new Map());
    const journal: Journal = new Map();
    const f = crashFixture();
    const prepared = await run(f, ports(f, journal));
    let ackWrites = 0;
    await ack(f, ports(f, journal, (p) => ({ cas: async (k, v, ver) => { ackWrites++; return p.cas(k, v, ver); } })), prepared.acknowledge);
    expect(r.status).toBe("prepared");
    expect(exportWrites).toBeGreaterThanOrEqual(5);
    expect(ackWrites).toBeGreaterThanOrEqual(4);
    expect(ackProbe.count()).toBe(0);
    let scenarios = 0;
    for (const phase of ["export", "ack"] as const)
      for (let k = 1; k <= (phase === "export" ? exportWrites : ackWrites); k++)
        for (const mode of ["throw_before", "throw_after", "ambiguous", "fail"] as const) { await scenario({ phase, casAt: k, mode }); scenarios++; }
    for (const deliver of ["throw_before", "throw_after", "fail"] as const) { await scenario({ phase: "export", deliver }); scenarios++; }
    await scenario({ phase: "export", outageAfterManifest: true }); scenarios++;
    expect(scenarios).toBeGreaterThanOrEqual(40);
  }, 120000);
});

describe("N9: results stay far below the SDK's 64 KiB limit", () => {
  test("100 orders x 500 lines through the real SDK execute(): small result, files on disk, acknowledged afterwards", async () => {
    const f = fixture(Array.from({ length: 100 }, (_, i) => bigOrder(30000 + i, 500)), { qbo: null });
    const fake = fakeType(f);
    const dir = mkdtempSync(join(tmpdir(), "bcqbo-n9-"));
    const args = { config: f.config, integrationIds: { bigcommerce: "orgint_bc", slack: "orgint_s" }, slackChannel: "C1", ...WINDOW, mode: "export", outputDir: dir };
    const result = await execute(args, fake.type);
    expect(result.isOk()).toBe(true);
    const value = result._unsafeUnwrap();
    const output = (value.action === "skip" ? value.output : null) as Out;
    expect(Buffer.byteLength(JSON.stringify(value))).toBeLessThan(RESULT_BUDGET_BYTES);
    expect(output).toMatchObject({ status: "prepared", counts: { prepared: 100 } });
    expect(JSON.stringify(output)).not.toContain("InvoiceNo,Customer");
    expect(fake.sent.filter((s) => s.body.service === "slack")).toHaveLength(0);
    const folder = readdirSync(join(dir, output.batchId));
    expect(folder.filter((n) => n.startsWith("qbo-invoices-import"))).toHaveLength(100);
    expect(folder).toContain("manifest.json");
    expect(JSON.parse(readFileSync(join(dir, output.batchId, "manifest.json"), "utf8")).acknowledgeBatch).toEqual(output.acknowledge);
    const { since: _s, until: _u, outputDir: _o, ...ackArgs } = args;
    const acked = (await execute({ ...ackArgs, acknowledgeBatch: output.acknowledge }, fake.type))._unsafeUnwrap();
    expect(acked.action === "skip" ? acked.output : null).toMatchObject({ status: "acknowledged", counts: { orders: 100 }, notification: "sent" });
    expect((fake.store.get("bcqbo/demo/order/30099")?.value as Row).state).toBe("exported");
  }, 120000);

  test("the hard guard truncates per-order lists, then file lists, keeping the acknowledgement values", () => {
    const huge = { status: "prepared", batchId: "b-1", acknowledge: { batchId: "b-1", contentSha256: "a".repeat(64) }, counts: { prepared: 5000 },
      orders: Array.from({ length: 5000 }, (_, i) => ({ orderId: String(i + 1), outcome: "pending_acknowledgement" })),
      reviewQueue: Array.from({ length: 5000 }, (_, i) => ({ orderId: String(i + 1), reason: "posted_status_unknown" })),
      delivery: { directory: "/x/b-1", error: null, files: Array.from({ length: 3000 }, (_, i) => ({ name: "qbo-invoices-import-part" + i + ".csv", bytes: 1, rows: 1, sha256: "b".repeat(64), path: "/x/b-1/f" + i })) } };
    const bounded = boundOutput(huge);
    expect(Buffer.byteLength(JSON.stringify(bounded))).toBeLessThanOrEqual(RESULT_BUDGET_BYTES);
    expect(bounded).toMatchObject({ acknowledge: huge.acknowledge, truncated: true, delivery: { directory: "/x/b-1", fileCount: 3000, manifestPath: "/x/b-1/manifest.json" } });
    const small = { status: "prepared", orders: [{ orderId: "1" }] };
    expect(boundOutput(small)).toBe(small);
  });
});

describe("N5: planner input is bounded per order, never per run", () => {
  test("raw product payloads are trimmed to planner fields (20 orders x 500 lines with 2 KB of options each still plan)", async () => {
    const options = { product_options: Array.from({ length: 20 }, (_, i) => ({ id: i, display_name: "Option " + i, display_value: "x".repeat(80) })) };
    const f = fixture(Array.from({ length: 20 }, (_, i) => bigOrder(40000 + i, 500, options)), { qbo: null });
    const raw = JSON.stringify(f.bigcommerce.orders).length;
    expect(raw).toBeGreaterThan(17 * 1024 * 1024);
    const r = await run(f, ports(f));
    expect(r.status).toBe("prepared");
    expect(r.counts.prepared).toBe(20);
    expect(plannerOrder({ id: 1, junk: 1, billing_address: { email: "a@b", extra: 1 }, products: [{ sku: "A", refund_amount: "0", images: [1] }] }))
      .toEqual({ id: 1, billing_address: { email: "a@b" }, products: [{ sku: "A", refund_amount: "0" }] });
  }, 60000);

  test("when a full page exceeds the run budget, the page ends early and the cursor continues from the last planned order", async () => {
    const name = "n".repeat(3800);
    const orders = Array.from({ length: 40 }, (_, i) => bigOrder(41000 + i, 500, { name }));
    const f = fixture(orders.map((o) => ({ ...o, products: [] })), { qbo: null });
    const byId = new Map(orders.map((o) => [String(o.id), o]));
    // Shared references instead of the fixture's per-request clones keep this 80 MB scenario fast.
    const lean = ports(f, new Map(), (p) => ({ bigcommerce: async (path, query) => {
      const match = /\/v2\/orders\/(\d+)\/products$/.exec(path);
      if (!match) return p.bigcommerce(path, query);
      const page = Number(query?.page ?? "1");
      return { status: 200, data: (byId.get(match[1] as string)?.products as Row[]).slice((page - 1) * 250, page * 250) };
    } }));
    const r = await run(f, lean, { maxOrders: 40 });
    expect(r.status).toBe("prepared");
    expect(r.warnings).toContain("planner_budget_page_shortened");
    expect(r.counts.prepared).toBeGreaterThan(20);
    expect(r.counts.prepared).toBeLessThan(40);
    expect(r.nextAfterOrderId).toBe(String(41000 + r.counts.prepared - 1));
    expect(r.cursorAdvanceSafe).toBe(true);
  }, 120000);

  test("one oversized order goes to durable review; its sibling exports and polling advances", async () => {
    const huge = bigOrder(40100, 500, { name: "n".repeat(Math.ceil(MAX_ORDER_PLAN_BYTES / 500) + 100) });
    const f = fixture([huge, order(40101)], { qbo: null });
    const journal: Journal = new Map();
    const polled = await runPoll({ config: f.config, mode: "export", timezone: "UTC", polling: { bootstrapSince: "2026-10-15T00:00:00Z", maxWindowSeconds: 172800 } },
      ports(f, journal)) as Out;
    expect(polled.status).toBe("polled");
    expect(reasonOf(polled.batch, "40100")).toBe("order_too_large_for_planner");
    expect(state(journal, "40100")).toMatchObject({ state: "needs_review", reason: "order_too_large_for_planner" });
    expect(fileOf(polled.batch, "qbo-invoices-import.csv")).toContain("BC-40101");
  }, 60000);
});

describe("N6: phone cells", () => {
  test("arithmetic-like phones are neutralized; strict E.164 and digit-led numbers stay exact", () => {
    for (const value of ["+1-1", "+1/1"]) expect(phoneCell(value)).toBe("'" + value);
    // R7-4: formatted numbers that are E.164 once spaces, dashes, dots and parentheses are removed are written normalized.
    expect(phoneCell("+1-555-0100")).toBe("+15550100");
    expect(phoneCell("+1 (555) 123-4567")).toBe("+15551234567");
    expect(phoneCell("+15551234567")).toBe("+15551234567");
    expect(phoneCell("(555) 123-4567")).toBe("(555) 123-4567");
  });
});

describe("N7: the QuickBooks invoice list is advisory", () => {
  const rows = (n: number, from = 1) => Array.from({ length: n }, (_, i) => invoice("ZZ-" + (from + i), "1.00", "x", String(from + i)));

  test("a deletion between pages is detected by the page overlap; the retry reads a consistent list and finds the posted invoice", async () => {
    const all = [...rows(150), invoice("BC-9300", "25.00", "Test Buyer9300", "9300")];
    let deleted = false, calls = 0;
    const qbo: QboCall = async (_tool, a) => {
      calls++;
      const start = Number(a.startPosition);
      if (start > 1 && !deleted) { deleted = true; all.splice(10, 1); }
      return { ok: true, data: { QueryResponse: { Invoice: all.slice(start - 1, start - 1 + Number(a.maxResults)) } } };
    };
    const listed = await listAll(qbo, async () => {}, "Invoice", 10);
    expect(listed).toMatchObject({ ok: true, complete: true });
    expect(listed.ok && listed.records.some((r) => r.DocNumber === "BC-9300")).toBe(true);
    expect(calls).toBeGreaterThan(3);
    // A list that keeps changing is an error, never a complete list.
    let shift = 0;
    const unstable: QboCall = async (_tool, a) => {
      const start = Number(a.startPosition) + (shift++ % 2);
      return { ok: true, data: { QueryResponse: { Invoice: rows(300).slice(start - 1, start - 1 + Number(a.maxResults)) } } };
    };
    expect(await listAll(unstable, async () => {}, "Invoice", 10)).toEqual({ ok: false, reason: "qbo_invoice_paging_inconsistent" });
  });

  test("a posted invoice the list missed is found by a targeted read; the list is then treated as incomplete for new invoices", async () => {
    const posted = invoice("BC-9310", "25.00", "Test Buyer9310", "310");
    const f = fixture([order(9310), order(9311)], { qbo: { invoices: [posted] } });
    const journal: Journal = new Map();
    await run(f, ports(f, journal));
    expect(state(journal, "9310")).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "310" });
    const changed = fixture([order(9310, { date_modified: "2026-10-16T13:00:00Z" }), order(9312)], { qbo: { invoices: [posted] } });
    const skipping = ports(changed, journal, (p) => ({ qbo: (async (tool, a) => {
      const answer = await p.qbo!(tool, a);
      if (tool !== "quickbooks_list_entities" || a.entity !== "Invoice" || !answer.ok) return answer;
      return { ok: true, data: { QueryResponse: {} } };
    }) as QboCall }));
    const r = await run(changed, skipping);
    expect(r.qbo.invoicesComplete).toBe(false);
    expect(r.qbo.warnings.join(" ")).toContain("missed invoice 310");
    expect(outcomeOf(r, "9310")).toBe("already_posted");
    expect(reasonOf(r, "9312")).toBe("posted_status_unknown");
    expect(fileOf(r, "qbo-invoices-import.csv")).toBeUndefined();
  });

  test("an acknowledged invoice not seen in a complete list stays awaiting confirmation and is never re-rendered automatically", async () => {
    const f = fixture([order(9320)], { qbo: { invoices: rows(3) } });
    const journal: Journal = new Map();
    const x = await run(f, ports(f, journal));
    await ack(f, ports(f, journal), x.acknowledge);
    const again = await run(f, ports(f, journal));
    expect(again.counts).toMatchObject({ prepared: 0, alreadyExported: 1, awaitingConfirmation: 1 });
    expect(fileOf(again, "qbo-invoices-import.csv")).toBeUndefined();
  });
});

describe("N8: preview output can never be mistaken for an import batch", () => {
  test("preview file names are watermarked, the summary says do not import, nothing is recorded and it cannot be acknowledged", async () => {
    const f = fixture([order(9401)], { qbo: null });
    const journal: Journal = new Map();
    const r = (await runSync({ config: f.config, mode: "preview", ...WINDOW }, ports(f, journal))) as Out;
    expect(r.batchId).toStartWith("PREVIEW-");
    expect(r.acknowledge).toBeNull();
    expect((r.files as { name: string }[]).every((x) => x.name.startsWith(PREVIEW_PREFIX))).toBe(true);
    const summary = fileOf(r, "summary.md") ?? "";
    expect(summary).toStartWith("# PREVIEW — DO NOT IMPORT");
    expect(summary).toContain("Never import preview files.");
    expect(journal.size).toBe(0);
  });
});

describe("N4: exported orders whose products cannot be read keep their record", () => {
  test("three failed reads never change the export record or its fingerprint; once readable it is confirmed normally", async () => {
    const f = fixture([order(9501), order(9502)], { qbo: null });
    const journal: Journal = new Map();
    const x = await run(f, ports(f, journal));
    await ack(f, ports(f, journal), x.acknowledge);
    const before = state(journal, "9501");
    const touched = fixture([order(9501, { date_modified: "2026-10-16T13:00:00Z" }), order(9502)], { qbo: null });
    const failing = (p: Ports): Partial<Ports> => ({ bigcommerce: async (path, q) => (path.endsWith("/9501/products") ? { status: 500, data: null } : p.bigcommerce(path, q)) });
    const reasons: string[] = [];
    for (let i = 0; i < 3; i++) reasons.push(reasonOf(await run(touched, ports(touched, journal, failing)), "9501") ?? "");
    expect(reasons).toEqual(["products_read_failed", "products_read_failed", "products_read_failed_repeatedly"]);
    expect(state(journal, "9501")).toMatchObject({ state: "exported", sourceHash: before?.sourceHash, export: before?.export, readFailures: 3 });
    expect((journal.get("bcqbo/demo/pending")?.value as Row).orderIds).toContain("9501");
    const online = fixture([order(9501, { date_modified: "2026-10-16T13:00:00Z" }), order(9502)],
      { qbo: { invoices: [invoice("BC-9501", "25.00", "Test Buyer9501", "51")] } });
    const healed = await run(online, ports(online, journal));
    expect(reasonOf(healed, "9501")).toBeUndefined();
    expect(state(journal, "9501")).toMatchObject({ state: "confirmed_posted", qboInvoiceId: "51" });
    expect(state(journal, "9501")?.readFailures).toBeUndefined();
  });
});

describe("product projection keeps every planner check", () => {
  test("refund and discount fields still reach the planner", async () => {
    const f = fixture([order(9601, {}, [product("WIDGET", 2, "10.00", "20.00", { refund_amount: "5.00", images: ["x"] })]), order(9602)], { qbo: null });
    const r = await run(f, ports(f), { mode: "preview" });
    expect(reasonOf(r, "9601")).toBe("product_refund_requires_reconciliation");
    expect(fileOf(r, "qbo-invoices-import.csv")).toContain("BC-9602");
  });
});

/**
 * r8 regression tests for independent QA r7 findings R7-1..R7-4. Each test checks the actual output, states the
 * reason in its name, and includes the safe counterpart.
 */
describe("r8 repairs (QA r7)", () => {
  test("R7-1: an acknowledgement applied but reported failed is surfaced by the next run as ready to import; a finished acknowledgement is not", async () => {
    const f = fixture([order(9701), order(9702)], { qbo: null });
    const journal: Journal = new Map();
    const r = await run(f, ports(f, journal));
    // The manifest CAS is applied, but the agent sees a failure and does not retry.
    const lost = ports(f, journal, (p) => ({ cas: async (key: string, value: unknown, version: string | null) => {
      const written = await p.cas(key, value, version);
      return key.includes("/batch/") && (value as Row).status === "acknowledged" ? { ok: false, error: "timeout" } : written;
    } }));
    expect(await ack(f, lost, r.acknowledge)).toMatchObject({ status: "blocked", reason: "acknowledgement_not_recorded" });
    expect(manifest(journal, r.batchId)?.status).toBe("acknowledged");
    expect(state(journal, "9701")?.state).toBe("pending");
    const next = await run(f, ports(f, journal));
    expect(next.acknowledgedBatchesCompleted).toEqual([r.batchId]);
    expect(next.counts).toMatchObject({ prepared: 0, alreadyExported: 2 });
    expect(fileOf(next, "summary.md")).toContain(`Batch ${r.batchId} was acknowledged — its files are ready to import`);
    expect(state(journal, "9701")?.state).toBe("exported");
    // Through the saved entrypoint the same case also sets nextStep; a blocked run reports it too.
    const fake = fakeType(f);
    const dir = mkdtempSync(join(tmpdir(), "bcqbo-r8-"));
    const args = { config: f.config, integrationIds: { bigcommerce: "orgint_bc" }, ...WINDOW, mode: "export", outputDir: dir };
    const first = (await execute(args, fake.type))._unsafeUnwrap();
    const prepared = (first.action === "skip" ? first.output : null) as Out;
    const key = "bcqbo/demo/batch/" + prepared.batchId;
    const row = fake.store.get(key) as { value: Row; version: string };
    fake.store.set(key, { value: { ...row.value, status: "acknowledged" }, version: row.version + "x" });
    const second = (await execute(args, fake.type))._unsafeUnwrap();
    const out = (second.action === "skip" ? second.output : null) as Out;
    expect(out.acknowledgedBatchesCompleted).toEqual([prepared.batchId]);
    expect(out.nextStep).toContain(`Batch ${prepared.batchId} was acknowledged — its files are ready to import.`);
    // Safe counterpart: an acknowledgement that finished its records is not reported again.
    const g = fixture([order(9703)], { qbo: null });
    const clean: Journal = new Map();
    const c = await run(g, ports(g, clean));
    expect(await ack(g, ports(g, clean), c.acknowledge)).toMatchObject({ status: "acknowledged", counts: { recordsCompleted: 1 } });
    const after = await run(g, ports(g, clean));
    expect(after.acknowledgedBatchesCompleted).toBeUndefined();
    expect(fileOf(after, "summary.md")).not.toContain("was acknowledged — its files are ready to import");
  }, 60000);

  test("R7-2: an unreadable order record or acknowledged manifest holds the order in review on every run; readable ones are used", async () => {
    // (a) A schema-2 exported record: never overwritten, never rendered as new, flagged on every run.
    const f = fixture([order(9711)], { qbo: null });
    const journal: Journal = new Map();
    const legacy = { schema: 2, state: "exported", sourceHash: "x", updatedBy: "r6", export: { kind: "invoice", docNumber: "BC-9711",
      customerName: "Test Buyer9711", totalCents: 2500, currency: "USD", batchId: "old", exportCount: 1 } };
    journal.set("bcqbo/demo/order/9711", { value: legacy, version: "v-old" });
    for (let i = 0; i < 2; i++) {
      const r = await run(f, ports(f, journal));
      expect(reasonOf(r, "9711")).toBe("journal_record_invalid");
      expect(r.counts.prepared).toBe(0);
      expect(fileOf(r, "qbo-invoices-import.csv") ?? "").not.toContain("BC-9711,");
      expect(journal.get("bcqbo/demo/order/9711")).toEqual({ value: legacy, version: "v-old" });
    }
    // (b) An acknowledged manifest that no longer parses, while an order's exported write was deferred.
    const g = fixture([order(9712)], { qbo: null });
    const store: Journal = new Map();
    const e = await run(g, ports(g, store));
    const deferred = ports(g, store, (p) => ({ cas: async (key: string, value: unknown, version: string | null) =>
      key.endsWith("/order/9712") ? { ok: false, error: "state_write_failed" } : p.cas(key, value, version) }));
    expect(await ack(g, deferred, e.acknowledge)).toMatchObject({ status: "acknowledged", warnings: ["export_record_deferred:9712"] });
    const key = "bcqbo/demo/batch/" + e.batchId;
    const intact = store.get(key) as { value: Row; version: string };
    store.set(key, { value: { ...intact.value, schema: 2 }, version: intact.version });
    const n = await run(g, ports(g, store));
    expect(reasonOf(n, "9712")).toBe("batch_state_unknown");
    expect(n.counts.prepared).toBe(0);
    expect(n.supersedes).toEqual([]);
    expect(fileOf(n, "summary.md")).not.toContain("supersedes");
    expect(n.cursorAdvanceSafe).toBe(false);
    expect(state(store, "9712")?.state).toBe("pending");
    // Safe counterpart: the intact manifest completes the order as exported.
    store.set(key, intact);
    const ok = await run(g, ports(g, store));
    expect(outcomeOf(ok, "9712")).toBe("already_exported");
    expect(state(store, "9712")?.state).toBe("exported");
  }, 60000);

  test("R7-3: rows after a page too short for the full overlap make the list incomplete (posted_status_unknown); full pages and a short last page are complete", async () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => invoice("ZZ-" + (i + 1), "1.00", "x", String(i + 1)));
    const pager = (all: Row[], size: number): QboCall => async (_tool, a) =>
      ({ ok: true, data: { QueryResponse: { Invoice: all.slice(Number(a.startPosition) - 1, Number(a.startPosition) - 1 + Math.min(size, Number(a.maxResults))) } } });
    const tiny = await listAll(pager(rows(3), 1), async () => {}, "Invoice", 10);
    expect(tiny).toMatchObject({ ok: true, complete: false });
    const f = fixture([order(9721)]);
    const onePerPage = ports(f, new Map(), (p) => ({ qbo: async (tool, a) =>
      tool === "quickbooks_list_entities" && a.entity === "Invoice" ? pager(rows(3), 1)(tool, a) : (p.qbo as QboCall)(tool, a) }));
    const r = await run(f, onePerPage, { mode: "preview" });
    expect(reasonOf(r, "9721")).toBe("posted_status_unknown");
    expect(fileOf(r, "qbo-invoices-import.csv") ?? "").not.toContain("BC-9721,");
    // Safe counterparts: full-size pages, and a list that fits in one short page, are complete.
    expect(await listAll(pager(rows(250), 100), async () => {}, "Invoice", 10)).toMatchObject({ ok: true, complete: true });
    expect(await listAll(pager(rows(3), 100), async () => {}, "Invoice", 10)).toMatchObject({ ok: true, complete: true });
    expect(outcomeOf(await run(f, ports(f), { mode: "preview" }), "9721")).toBe("pending_acknowledgement");
  });

  test("R7-4: formatted phones that are E.164 once spaces, dashes, dots and parentheses are removed are written normalized; others keep the apostrophe", async () => {
    expect(phoneCell("+1 555 0100")).toBe("+15550100");
    expect(phoneCell("+44 (20) 7946-0958")).toBe("+442079460958");
    expect(phoneCell("+1.555.012.3456")).toBe("+15550123456");
    // Safe counterparts: not E.164 after normalization (too short, other characters, leading 0) keeps the neutralization.
    expect(phoneCell("+1 555")).toBe("'+1 555");
    expect(phoneCell("+1/5551234567")).toBe("'+1/5551234567");
    expect(phoneCell("+0 555 123 4567")).toBe("'+0 555 123 4567");
    expect(phoneCell("+1 555 0100 x12")).toBe("'+1 555 0100 x12");
    expect(phoneCell("(555) 123-4567")).toBe("(555) 123-4567");
    const f = fixture([order(9731, { billing_address: { first_name: "Ann", last_name: "Q", email: "ann@example.test", phone: "+44 (20) 7946-0958" } }),
      order(9732, { billing_address: { first_name: "Bo", last_name: "R", email: "bo@example.test", phone: "+1/1" } })]);
    const r = await run(f, ports(f), { mode: "preview" });
    expect(fileOf(r, "qbo-customers-import.csv")).toContain("Ann Q,,ann@example.test,+442079460958,");
    expect(fileOf(r, "qbo-customers-import.csv")).toContain("Bo R,,bo@example.test,'+1/1,");
  });
});
