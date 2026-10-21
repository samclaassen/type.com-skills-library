import { createHash } from "node:crypto";
import { z } from "zod";

/**
 * Durable run state (Type recorded data, space scope). Shared by the prepare run (sync.ts) and the
 * acknowledgement (ack.ts).
 *
 * Order lifecycle:
 *   (none) -> pending (rendered into an unacknowledged batch) -> exported (batch acknowledged after upload)
 *          -> confirmed_posted (QuickBooks shows the invoice).  Any state may move to needs_review.
 * A pending order is re-rendered into every new batch until the batch that holds it is acknowledged.
 */
export const orderId = z.string().regex(/^[1-9]\d{0,14}$/);
export const batchIdSchema = z.string().regex(/^[A-Za-z0-9-]{1,64}$/);
export const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/);
const kindSchema = z.enum(["invoice", "sales_receipt", "invoice_then_payment"]);
export type Kind = z.infer<typeof kindSchema>;

/** The last acknowledged export of the order. */
const exportInfo = z.object({
  kind: kindSchema, docNumber: z.string(), customerName: z.string(),
  totalCents: z.number().int(), currency: z.string(), batchId: z.string(), exportCount: z.number().int().min(0),
});
/** The newest (unacknowledged) batch that holds the order's rows. */
const pendingInfo = z.object({
  kind: kindSchema, docNumber: z.string(), customerName: z.string(), totalCents: z.number().int(), currency: z.string(),
  batchId: z.string(), rowHash: sha256Schema, runId: z.string(),
});
export type PendingInfo = z.infer<typeof pendingInfo>;
export const journalSchema = z.object({
  schema: z.literal(3), state: z.enum(["pending", "exported", "confirmed_posted", "needs_review"]), sourceHash: z.string(),
  reason: z.string().optional(), export: exportInfo.optional(), pendingBatch: pendingInfo.optional(), qboInvoiceId: z.string().optional(),
  paymentFollowUp: z.boolean().optional(), readFailures: z.number().int().min(0).optional(), updatedBy: z.string(),
}).refine((j) => (j.state === "pending") === (j.pendingBatch !== undefined), "pending needs its batch");
export type Journal = z.infer<typeof journalSchema>;

/** Orders that still need follow-up: pending (unacknowledged) orders and exported invoices awaiting confirmation. */
export const MAX_PENDING = 1000;
/** Pending (unacknowledged) orders at most; each new batch re-renders all of them. Beyond it new orders go to review. */
export const MAX_UNACKED = 200;
/** Runs in a row an order's products may fail to read before it is parked for review so polling can move on. */
export const MAX_READ_FAILURES = 3;
export const indexSchema = z.object({ schema: z.literal(1), orderIds: z.array(orderId).max(MAX_PENDING) });

const manifestOrder = z.object({ orderId, rowHash: sha256Schema, kind: kindSchema });
const manifestFile = z.object({ name: z.string(), bytes: z.number().int().min(0), sha256: sha256Schema, rows: z.number().int().min(0) });
export type ManifestFile = z.infer<typeof manifestFile>;
/**
 * One prepared batch. Only a batch in status "prepared" can be acknowledged, and only with its contentSha256.
 * A later run that re-renders any of its orders first moves it to "superseded" (compare-and-swap), so a batch
 * is either acknowledged or superseded, never both.
 */
export const manifestSchema = z.object({
  schema: z.literal(1), batchId: batchIdSchema, windowId: z.string(), runId: z.string(), createdAt: z.string(),
  status: z.enum(["prepared", "superseded", "void", "acknowledged"]),
  orders: z.array(manifestOrder).max(400), files: z.array(manifestFile).max(1000), contentSha256: sha256Schema,
  supersedes: z.array(z.string()).max(400), supersededBy: z.string().optional(),
  acknowledgedAt: z.string().optional(), acknowledgedBy: z.string().optional(),
});
export type Manifest = z.infer<typeof manifestSchema>;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const stable = (v: unknown): string => {
  if (Array.isArray(v)) return "[" + v.map(stable).join(",") + "]";
  if (isRecord(v)) return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + stable(v[k])).join(",") + "}";
  return JSON.stringify(v) ?? "null";
};
export const hash = (v: unknown) => createHash("sha256").update(stable(v)).digest("hex");
export const byId = (a: string, b: string) => (BigInt(a) < BigInt(b) ? -1 : BigInt(a) > BigInt(b) ? 1 : 0);

/** The value the acknowledging agent must echo: binds the batch ID, every order's rows and every file's bytes. */
export function contentSha256(batchId: string, orders: { orderId: string; rowHash: string }[], files: { name: string; sha256: string }[]) {
  return hash({ batchId, orders: orders.map((o) => [o.orderId, o.rowHash]), files: files.map((f) => [f.name, f.sha256]) });
}

export const keys = (storeHash: string) => {
  const ns = "bcqbo/" + storeHash;
  return {
    order: (oid: string) => ns + "/order/" + oid,
    index: ns + "/pending",
    batch: (batchId: string) => ns + "/batch/" + batchId,
    poll: ns + "/poll",
    summary: (batchId: string) => ns + "/summary/" + batchId,
  };
};

/** The exported record for a pending order whose batch is acknowledged, or null if the batch does not hold these rows. */
export function acknowledgedExport(journal: Journal, oid: string, manifest: Manifest, runId: string): Journal | null {
  const pending = journal.pendingBatch;
  if (journal.state !== "pending" || !pending || manifest.status !== "acknowledged" || manifest.batchId !== pending.batchId) return null;
  if (!manifest.orders.some((o) => o.orderId === oid && o.rowHash === pending.rowHash)) return null;
  return {
    schema: 3, state: "exported", sourceHash: journal.sourceHash,
    export: { kind: pending.kind, docNumber: pending.docNumber, customerName: pending.customerName, totalCents: pending.totalCents,
      currency: pending.currency, batchId: pending.batchId, exportCount: (journal.export?.exportCount ?? 0) + 1 },
    updatedBy: runId,
  };
}
