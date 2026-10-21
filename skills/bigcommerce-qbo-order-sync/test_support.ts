/** Shared fixtures for test_ack.ts: synthetic orders, fixture ports and a fake Type API behind the real SDK. */
import { Type } from "@type-dot-com/sdk";
import { readFileSync } from "node:fs";
import skill from "./main";
import { fixturePorts, fixtureSchema, type Fixture } from "./preview";
import type { Ports } from "./sync";

export const base: Fixture = fixtureSchema.parse(JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8")));
export const WINDOW = { since: "2026-10-15T00:00:00Z", until: "2026-10-17T00:00:00Z", timezone: "UTC" };
export type Journal = Map<string, { value: unknown; version: string }>;
export type Row = Record<string, any>;

export function product(sku: string, quantity: number, price: string, total: string, extra: Row = {}): Row {
  return { sku, name: sku, quantity, price_ex_tax: price, total_ex_tax: total, total_tax: "0.00", tax_class_id: 0, applied_discounts: [], ...extra };
}
/** Eligible, unpaid, tax-free: 2 x WIDGET @10.00 + 5.00 shipping = 25.00. */
export function order(id: number, over: Row = {}, products: Row[] | null = null): Row {
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
/** An order with `lines` products of 5.00 each plus 5.00 shipping. */
export function bigOrder(id: number, lines: number, extra: Row = {}): Row {
  const sub = (5 * lines).toFixed(2), total = (5 * lines + 5).toFixed(2);
  return order(id, { subtotal_ex_tax: sub, total_ex_tax: total, total_inc_tax: total },
    Array.from({ length: lines }, (_, k) => product(k % 2 ? "WIDGET" : "GADGET", 1, "5.00", "5.00", { name: "Product " + k, ...extra })));
}
export function fixture(orders: Row[], options: { config?: Row; qbo?: Row | null } = {}): Fixture {
  const config = { ...base.config, purchaseOrders: false, suppliers: {}, ...(options.config ?? {}) };
  const quickbooks = options.qbo === null ? null : { ...base.quickbooks, customers: [], invoices: [], ...(options.qbo ?? {}) };
  return fixtureSchema.parse({ window: WINDOW, config, bigcommerce: { orders }, quickbooks });
}
export const invoice = (doc: string, total: string, name: string, id = "900") => ({ Id: id, DocNumber: doc, TotalAmt: total, CustomerRef: { value: "1", name } });
export function ports(f: Fixture, journal: Journal = new Map(), wrap?: (p: Ports) => Partial<Ports>) {
  const { ports: p } = fixturePorts(f, journal);
  return { ...p, ...(wrap ? wrap(p) : {}) } as Ports;
}
export const state = (journal: Journal, oid: string) => journal.get("bcqbo/demo/order/" + oid)?.value as Row | undefined;
export const manifest = (journal: Journal, batchId: string) => journal.get("bcqbo/demo/batch/" + batchId)?.value as Row | undefined;

/** Fake Type API behind the real SDK; BigCommerce/QuickBooks come from the fixture, Type data is a versioned map. */
export function fakeType(f: Fixture) {
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
/** Runs the saved entrypoint through the real SDK execute(), which enforces the 64 KiB result limit. */
export async function execute(args: Row, type: Type, runId = "run_" + Math.random().toString(36).slice(2)) {
  return skill.execute(args as Parameters<typeof skill.execute>[0], { type, runId, integrations: {} });
}
