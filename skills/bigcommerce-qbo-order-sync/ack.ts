import { z } from "zod";
import { configSchema, type Config } from "./config";
import {
  acknowledgedExport, batchIdSchema, byId, contentSha256, indexSchema, journalSchema, keys, manifestSchema, sha256Schema, stable,
  type Journal,
} from "./journal";
import type { Ports } from "./sync";

export type AckArgs = { config: Config; batchId: string; contentSha256: string };
type AckPorts = Pick<Ports, "runId" | "now" | "read" | "cas" | "slack">;

/**
 * Acknowledge a prepared batch after its files were uploaded to Type (or the user confirmed receipt). This is the
 * only step that makes orders `exported`. The commit is one compare-and-swap of the batch manifest from "prepared"
 * to "acknowledged"; it is accepted only when contentSha256 matches the recorded manifest and every order of the
 * batch is still pending in exactly this batch with the recorded rows. Superseded, void or unknown batches are
 * refused, so no order can be in two acknowledged batches. Per-order records are completed after the commit; any
 * that fail are completed by the next run (or by repeating the acknowledgement, which is idempotent).
 */
export async function runAcknowledge(input: AckArgs, ports: AckPorts) {
  const blocked = (reason: string, extra: Record<string, unknown> = {}) => ({ status: "blocked" as const, reason, ...extra });
  const checked = z.strictObject({ config: configSchema, batchId: batchIdSchema, contentSha256: sha256Schema }).safeParse(input);
  if (!checked.success) return blocked("invalid_acknowledgement_input");
  const { config, batchId } = checked.data;
  if (batchId.startsWith("PREVIEW-")) return blocked("preview_batches_cannot_be_acknowledged");
  const k = keys(config.storeHash);
  const stored = await ports.read(k.batch(batchId));
  if (!stored.ok) return blocked("batch_manifest_unavailable", { batchId });
  if (stored.value === null) return blocked("batch_not_found", { batchId });
  const parsed = manifestSchema.safeParse(stored.value);
  if (!parsed.success || parsed.data.batchId !== batchId
      || contentSha256(batchId, parsed.data.orders, parsed.data.files) !== parsed.data.contentSha256)
    return blocked("batch_manifest_invalid", { batchId });
  const manifest = parsed.data;
  if (manifest.contentSha256 !== checked.data.contentSha256) return blocked("acknowledgement_content_mismatch", { batchId });
  if (manifest.status === "superseded") return blocked("batch_superseded", { batchId, supersededBy: manifest.supersededBy ?? null });
  if (manifest.status === "void") return blocked("batch_void", { batchId });
  if (manifest.orders.length === 0) return blocked("batch_has_no_orders", { batchId });
  const already = manifest.status === "acknowledged";

  // Every order must still be pending in this batch with these rows (or already completed from this batch).
  const records = new Map<string, { journal: Journal; version: string | null }>();
  const stale: string[] = [];
  for (const entry of manifest.orders) {
    const read = await ports.read(k.order(entry.orderId));
    if (!read.ok) return blocked("journal_unavailable", { batchId });
    const journal = journalSchema.safeParse(read.value);
    const j = journal.success ? journal.data : null;
    const heldHere = j?.state === "pending" && j.pendingBatch?.batchId === batchId && j.pendingBatch.rowHash === entry.rowHash;
    if (heldHere && j) records.set(entry.orderId, { journal: j, version: read.version });
    else if (!already) stale.push(entry.orderId);
  }
  if (stale.length) return blocked("batch_not_latest_for_orders", { batchId, orderIds: stale.sort(byId).slice(0, 50), staleCount: stale.length });

  const acknowledgedAt = ports.now();
  if (!already) {
    const committed = await ports.cas(k.batch(batchId), { ...manifest, status: "acknowledged", acknowledgedAt, acknowledgedBy: ports.runId }, stored.version);
    // An unknown outcome is safe to retry: a repeated acknowledgement of an acknowledged batch is idempotent.
    if (!committed.ok) return blocked("acknowledgement_not_recorded", { batchId, retry: "Repeat the same acknowledgement." });
  }
  const acked = { ...manifest, status: "acknowledged" as const };
  const warnings: string[] = [];
  let completed = 0;
  const receipts: string[] = [];
  for (const [oid, { journal, version }] of [...records.entries()].sort((a, b) => byId(a[0], b[0]))) {
    const done = acknowledgedExport(journal, oid, acked, ports.runId);
    if (!done) continue;
    const written = await ports.cas(k.order(oid), done, version);
    if (!written.ok) { warnings.push("export_record_deferred:" + oid); continue; }
    completed++;
    if (done.export?.kind === "sales_receipt") receipts.push(oid);
  }
  // Sales receipts cannot be confirmed in QuickBooks; they leave the follow-up index (best effort; later runs also do this).
  if (receipts.length) {
    const index = await ports.read(k.index);
    const parsedIndex = index.ok && index.value !== null ? indexSchema.safeParse(index.value).data : null;
    if (index.ok && parsedIndex) {
      const remaining = parsedIndex.orderIds.filter((oid) => !receipts.includes(oid));
      if (stable(remaining) !== stable(parsedIndex.orderIds)) {
        const written = await ports.cas(k.index, { schema: 1, orderIds: remaining }, index.version);
        if (!written.ok) warnings.push("pending_index_update_deferred");
      }
    }
  }

  const kinds = { invoice: 0, sales_receipt: 0, invoice_then_payment: 0 };
  for (const o of manifest.orders) kinds[o.kind]++;
  let notification = "disabled";
  if (ports.slack && !already) {
    const claim = await ports.cas(k.summary(batchId), { schema: 1, runId: ports.runId, batchId }, null);
    notification = claim.ok
      ? await ports.slack(`BigCommerce → QuickBooks batch ${batchId} acknowledged: ${manifest.orders.length} orders (${kinds.invoice + kinds.invoice_then_payment} invoices, ${kinds.sales_receipt} sales receipts) are in its import files, which were uploaded to Type (not to Slack). Import that batch only; nothing was posted to QuickBooks.`)
      : "already_claimed_or_storage_unavailable";
  }
  return {
    status: "acknowledged" as const, batchId, contentSha256: manifest.contentSha256, alreadyAcknowledged: already,
    counts: { orders: manifest.orders.length, invoices: kinds.invoice, salesReceipts: kinds.sales_receipt, invoiceThenPayment: kinds.invoice_then_payment,
      recordsCompleted: completed },
    notification, warnings: warnings.slice(0, 50),
    nextStep: "Tell the user the uploaded files of batch " + batchId + " are ready to import in QuickBooks Online (steps in its summary.md). Rerun later to confirm posted invoices.",
  };
}
