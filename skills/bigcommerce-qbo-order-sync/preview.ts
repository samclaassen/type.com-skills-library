/**
 * Credential-free preview: runs the full pipeline against fixture data with in-memory
 * providers and journal. No network, no Type data, no provider writes.
 *
 *   bun preview.ts [fixture.json] [output-directory]
 */
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { configSchema } from "./config";
import { describeFiles, publishFiles, runPlanner } from "./main";
import type { QboArgs, QboCall, QboTool } from "./qbo";
import type { OutputFile } from "./render";
import { runSync, type Delivery, type Ports, type Response } from "./sync";

export const fixtureSchema = z.object({
  window: z.object({ since: z.string(), until: z.string(), timezone: z.string() }),
  config: configSchema,
  bigcommerce: z.object({ orders: z.array(z.record(z.string(), z.unknown())) }),
  quickbooks: z.object({
    companyInfo: z.record(z.string(), z.unknown()),
    homeCurrency: z.string().nullable(),
    customers: z.array(z.record(z.string(), z.unknown())),
    vendors: z.array(z.record(z.string(), z.unknown())),
    invoices: z.array(z.record(z.string(), z.unknown())),
  }).nullable(),
});
export type Fixture = z.infer<typeof fixtureSchema>;

/** In-memory providers that answer exactly like the read endpoints/tools the skill uses. */
export function fixturePorts(fixture: Fixture, journal = new Map<string, { value: unknown; version: string }>(),
  options: { outputParent?: string; deliver?: (files: OutputFile[], batchId: string) => Promise<Delivery> } = {}) {
  /** Published batches (in memory unless outputParent is given). */
  const published = new Map<string, OutputFile[]>();
  let revision = journal.size;
  const orders = () => fixture.bigcommerce.orders.map((o) => structuredClone(o)).sort((a, b) => Number(a.id) - Number(b.id));
  const bigcommerce = async (path: string, query?: Record<string, string>): Promise<Response> => {
    const products = /\/v2\/orders\/(\d+)\/products$/.exec(path);
    if (products) {
      const order = orders().find((o) => String(o.id) === products[1]);
      const list = Array.isArray(order?.products) ? order.products : [];
      const page = Number(query?.page ?? "1"), limit = Number(query?.limit ?? "50");
      return order ? { status: 200, data: list.slice((page - 1) * limit, page * limit) } : { status: 404, data: null };
    }
    const single = /\/v2\/orders\/(\d+)$/.exec(path);
    if (single) {
      const order = orders().find((o) => String(o.id) === single[1]);
      if (!order) return { status: 404, data: null };
      const { products: _p, ...rest } = order;
      return { status: 200, data: rest };
    }
    if (path.endsWith("/v2/orders")) {
      const since = Date.parse(query?.min_date_modified ?? ""), until = Date.parse(query?.max_date_modified ?? "");
      const minId = Number(query?.min_id ?? "0"), limit = Number(query?.limit ?? "50");
      const list = orders().filter((o) => {
        const modified = Date.parse(String(o.date_modified));
        return modified >= since && modified <= until && Number(o.id) >= minId;
      }).slice(0, limit).map(({ products: _p, ...rest }) => rest);
      return list.length ? { status: 200, data: list } : { status: 204, data: null };
    }
    return { status: 404, data: null };
  };
  const qb = fixture.quickbooks;
  const qbo: QboCall | null = qb ? async (tool: QboTool, args: QboArgs) => {
    if (tool === "quickbooks_get_company_info") return { ok: true, data: { CompanyInfo: structuredClone(qb.companyInfo) } };
    if (tool === "quickbooks_run_report") return { ok: true, data: { Header: qb.homeCurrency ? { Currency: qb.homeCurrency } : {} } };
    if (tool === "quickbooks_get_entity" && args.entity === "Invoice") {
      const found = qb.invoices.find((r) => String(r.Id) === String(args.id));
      return found ? { ok: true, data: { Invoice: structuredClone(found) } } : { ok: false, error: "not_found", retryable: false };
    }
    if (tool === "quickbooks_list_entities") {
      const entity = String(args.entity);
      const all = entity === "Customer" ? qb.customers : entity === "Vendor" ? qb.vendors : entity === "Invoice" ? qb.invoices : [];
      const start = Number(args.startPosition), max = Number(args.maxResults);
      const page = all.slice(start - 1, start - 1 + max).map((r) => structuredClone(r));
      return { ok: true, data: { QueryResponse: page.length ? { [entity]: page, startPosition: start, maxResults: page.length } : {} } };
    }
    return { ok: false, error: "not_found", retryable: false };
  } : null;
  const ports: Ports = {
    runId: "fixture-run-" + randomUUID(),
    now: () => fixture.window.until,
    bigcommerce, qbo, slack: null,
    plan: runPlanner,
    sleep: async () => {},
    deliver: options.deliver ?? (async (files, batchId) => {
      if (published.has(batchId)) return { ok: false, error: "output_folder_exists" };
      if (options.outputParent) {
        const written = publishFiles(files, options.outputParent, batchId);
        if (written.ok) published.set(batchId, files);
        return written;
      }
      published.set(batchId, files);
      return { ok: true, directory: "memory:" + batchId };
    }),
    async read(key) {
      const row = journal.get(key);
      return { ok: true, value: row ? structuredClone(row.value) : null, version: row?.version ?? null };
    },
    async cas(key, value, expected) {
      if ((journal.get(key)?.version ?? null) !== expected) return { ok: false, error: "version_conflict" };
      const version = String(++revision);
      journal.set(key, { value: structuredClone(value), version });
      return { ok: true, version };
    },
  };
  return { ports, journal, published };
}

export async function previewFixture(fixture: Fixture, mode: "preview" | "export" = "preview") {
  const { ports, journal } = fixturePorts(fixture);
  const result = await runSync({ config: fixture.config, mode, ...fixture.window }, ports);
  return { result, journal };
}

if (import.meta.main) {
  const path = process.argv[2] ?? join(import.meta.dir, "fixture.json");
  const parsed = fixtureSchema.safeParse(JSON.parse(readFileSync(path, "utf8")));
  if (!parsed.success) {
    console.error(JSON.stringify(z.treeifyError(parsed.error)));
    process.exit(2);
  }
  // Each preview is written into a new <output-directory>/PREVIEW-<batchId> folder; every file name starts with
  // PREVIEW-DO-NOT-IMPORT-.
  const { ports } = fixturePorts(parsed.data, new Map(), { outputParent: process.argv[3] ?? join(process.cwd(), "exports") });
  const result = await runSync({ config: parsed.data.config, mode: "preview", ...parsed.data.window }, ports);
  const summary = result.files.find((f) => f.name.endsWith("summary.md"));
  console.log(summary?.content ?? JSON.stringify(result));
  const directory = "delivery" in result ? result.delivery.directory : null;
  console.log(JSON.stringify(describeFiles(result.files, directory).map(({ name, path: filePath, rows, sha256 }) => ({ name, path: filePath, rows, sha256 })), null, 2));
  if (result.status === "blocked") process.exit(1);
}
