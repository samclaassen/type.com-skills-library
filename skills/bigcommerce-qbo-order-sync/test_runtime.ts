import { describe, expect, test } from "bun:test";
import { Type } from "@type-dot-com/sdk";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import skill, { createPorts, inputSchema, plannerEnvironment, runPlanner, type Bindings } from "./main";
import { fixturePorts, fixtureSchema, type Fixture } from "./preview";
import { QBO_READ_TOOLS } from "./qbo";
import { PREVIEW_PREFIX } from "./render";

const fixture: Fixture = fixtureSchema.parse(JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8")));
const ids = { bigcommerce: "orgint_bc", quickbooks: "orgint_qbo", slack: "orgint_slack" };
const fixed = {
  config: fixture.config, integrationIds: ids, timezone: "UTC",
  since: fixture.window.since, until: fixture.window.until, slackChannel: "C123",
};
type Sent = { path: string; body: Record<string, any> };

/**
 * A fake Type API behind the real SDK. BigCommerce and QuickBooks answers come from the fixture;
 * Type data is an in-memory versioned store. Every request is recorded (runtime spy).
 */
function fakeType(options: { fixture?: Fixture; reply?: (sent: Sent) => { status?: number; body: unknown } | null } = {}) {
  const f = options.fixture ?? fixture;
  const { ports: source } = fixturePorts(f);
  const store = new Map<string, { value: unknown; version: string }>();
  const sent: Sent[] = [];
  let counter = 0;
  const type = new Type({
    serverUrl: "https://fixture.type.test", accessToken: "fixture-token", agentId: "agent_fixture", channelId: "ch_fixture",
    fetch: async (url, init) => {
      const path = new URL(String(url)).pathname;
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const request = { path, body };
      sent.push(request);
      const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
      const custom = options.reply?.(request);
      if (custom) return json(custom.body, custom.status ?? 200);
      if (path.endsWith("/credential-connections/api/request")) {
        const answer = await source.bigcommerce(body.path, body.query);
        return json({ connection: { id: body.organizationIntegrationId, name: "Fixture", baseUrl: "https://api.bigcommerce.com" },
          status: answer.status, statusText: "OK", success: answer.status < 300, truncated: false, data: answer.data, headers: {} });
      }
      if (path.endsWith("/tools/call")) {
        if (body.service === "slack") return json({ content: [], structuredContent: { status: "sent", channelId: body.arguments.channel_id, ts: "1.2" } });
        const answer = source.qbo ? await source.qbo(body.name, body.arguments) : { ok: false, error: "none", retryable: false };
        return json("data" in answer ? { content: [], structuredContent: answer.data } : { content: [], isError: true, structuredContent: { error: { retryable: false } } });
      }
      if (path.endsWith("/data")) {
        const row = store.get(body.key);
        if (body.operation === "read")
          return json(row ? { found: true, key: body.key, version: row.version, updatedAt: "2026-10-16T00:00:00Z", value: row.value }
            : { found: false, key: body.key, version: null });
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

/** Every outbound request must be a GET to BigCommerce, a QuickBooks read tool, Slack summary, or Type data. */
function assertReadOnlyProviders(sent: Sent[]) {
  for (const { path, body } of sent) {
    if (path.endsWith("/credential-connections/api/request")) {
      expect(body.method).toBe("GET");
      expect(body.organizationIntegrationId).toBe("orgint_bc");
      expect(body.body).toBeUndefined();
    } else if (path.endsWith("/tools/call")) {
      if (body.service === "quickbooks") expect(QBO_READ_TOOLS as readonly string[]).toContain(body.name);
      else expect([body.service, body.name]).toEqual(["slack", "slack_post_message"]);
    } else expect(path.endsWith("/data")).toBe(true);
  }
}
const execute = (args: unknown, type: Type, runId = "run_fixture") =>
  skill.execute(args as Parameters<typeof skill.execute>[0], { type, runId, integrations: {} });
const outputOf = (result: Awaited<ReturnType<typeof execute>>) => {
  const value = result._unsafeUnwrap();
  return (value.action === "skip" ? value.output : null) as Record<string, any>;
};
const outDir = () => mkdtempSync(join(tmpdir(), "bcqbo-"));

describe("saved entrypoint through the real Type SDK", () => {
  test("input schema: preview is the default, no execute mode, QuickBooks and Slack optional", () => {
    const parsed = inputSchema.parse(fixed);
    expect(parsed.mode).toBe("preview");
    expect(inputSchema.safeParse({ ...fixed, mode: "execute" }).success).toBe(false);
    expect(inputSchema.safeParse({ ...fixed, integrationIds: { bigcommerce: "orgint_bc" }, slackChannel: undefined }).success).toBe(true);
    expect(inputSchema.safeParse({ ...fixed, integrationIds: { bigcommerce: "orgint_bc" } }).success).toBe(false);
    expect(inputSchema.safeParse({ ...fixed, since: fixed.until, until: fixed.since }).success).toBe(false);
    expect(inputSchema.safeParse({ ...fixed, timezone: "untrusted/no-such-zone" }).success).toBe(false);
    expect(inputSchema.safeParse({ ...fixed, executeConfirmed: true }).success).toBe(false);
    const { since, until: _u, ...base } = fixed;
    expect(inputSchema.safeParse({ ...base, polling: { bootstrapSince: since }, reexportOrderIds: ["1001"] }).success).toBe(false);
  });

  test("preview writes the import files locally, records nothing, and only reads providers", async () => {
    const fake = fakeType();
    const dir = outDir();
    const result = await execute({ ...fixed, outputDir: dir }, fake.type);
    expect(result.isOk()).toBe(true);
    const output = outputOf(result);
    expect(output).toMatchObject({ status: "preview", counts: { prepared: 2, review: 2, awaitingConfirmation: 0 }, acknowledge: null });
    // Each run publishes into its own new <outputDir>/<batchId> folder.
    expect(readdirSync(dir)).toEqual([output.batchId]);
    expect(output.delivery.directory).toBe(join(dir, output.batchId));
    expect(readdirSync(output.delivery.directory).sort()).toEqual(["qbo-customers-import.csv", "qbo-invoices-import.csv", "review-queue.csv",
      "review-queue.json", "sales-receipts-worksheet.csv", "summary.md", "supplier-po-worksheet.csv"].map((n) => PREVIEW_PREFIX + n));
    expect(readFileSync(join(output.delivery.directory, PREVIEW_PREFIX + "qbo-invoices-import.csv"), "utf8")).toStartWith("InvoiceNo,Customer,InvoiceDate,DueDate");
    expect(output.delivery.files.every((f: Record<string, unknown>) => !("content" in f))).toBe(true);
    expect(fake.sent.some((s) => s.body.operation === "record")).toBe(false);
    expect(fake.sent.some((s) => s.body.service === "slack")).toBe(false);
    assertReadOnlyProviders(fake.sent);
  });

  test("export journals through CAS as pending; the acknowledgement exports and posts one Slack summary; a rerun exports nothing new", async () => {
    const fake = fakeType();
    const first = outputOf(await execute({ ...fixed, mode: "export", outputDir: outDir() }, fake.type));
    expect(first).toMatchObject({ status: "prepared", counts: { prepared: 2 }, cursorAdvanceSafe: true });
    expect(first.nextStep).toContain("acknowledgeBatch");
    expect([...fake.store.keys()].sort()).toEqual(expect.arrayContaining(["bcqbo/demo/order/1001", "bcqbo/demo/order/1002", "bcqbo/demo/pending",
      "bcqbo/demo/batch/" + first.batchId]));
    expect((fake.store.get("bcqbo/demo/order/1002")?.value as Record<string, unknown>).state).toBe("pending");
    expect(fake.sent.filter((s) => s.body.service === "slack")).toHaveLength(0);
    const records = fake.sent.filter((s) => s.body.operation === "record");
    expect(records.every((s) => s.body.scope === "space" && "expectedVersion" in s.body)).toBe(true);
    const { since: _s, until: _u, ...ackBase } = fixed;
    const acked = outputOf(await execute({ ...ackBase, mode: "export", acknowledgeBatch: first.acknowledge }, fake.type, "run_ack"));
    expect(acked).toMatchObject({ status: "acknowledged", batchId: first.batchId, notification: "sent", counts: { orders: 2 } });
    expect((fake.store.get("bcqbo/demo/order/1002")?.value as Record<string, unknown>).state).toBe("exported");
    const second = outputOf(await execute({ ...fixed, mode: "export", outputDir: outDir() }, fake.type, "run_two"));
    expect(second.counts).toMatchObject({ prepared: 0, alreadyExported: 2, awaitingConfirmation: 1, review: 2 });
    assertReadOnlyProviders(fake.sent);
    expect(fake.sent.filter((s) => s.body.service === "slack")).toHaveLength(1);
  });

  test("polling preview through the SDK writes no checkpoint", async () => {
    const fake = fakeType();
    const { since, until: _u, ...base } = fixed;
    const result = await execute({ ...base, polling: { bootstrapSince: since }, outputDir: outDir() }, fake.type);
    expect(outputOf(result)).toMatchObject({ status: "preview", checkpointWritten: false });
    expect(fake.sent.some((s) => s.body.operation === "record")).toBe(false);
    assertReadOnlyProviders(fake.sent);
  });

  test("no QuickBooks connection: no QuickBooks tool calls, files still produced", async () => {
    const fake = fakeType();
    const result = await execute({ ...fixed, integrationIds: { bigcommerce: "orgint_bc" }, slackChannel: undefined, outputDir: outDir() }, fake.type);
    expect(outputOf(result)).toMatchObject({ status: "preview", qbo: { connected: false } });
    expect(fake.sent.some((s) => s.path.endsWith("/tools/call"))).toBe(false);
  });
});

describe("provider ports", () => {
  const bindings = (mode: "preview" | "export"): Bindings => ({ mode, storeHash: "demo", integrationIds: ids, slackChannel: "C123" });

  test("BigCommerce port issues GET only and refuses cross-store or injected paths before HTTP", async () => {
    const fake = fakeType();
    const ports = createPorts(fake.type, "run", bindings("export"));
    expect((await ports.bigcommerce("/stores/demo/v2/orders", { limit: "1", min_date_modified: fixture.window.since, max_date_modified: fixture.window.until })).status).toBe(200);
    for (const path of ["/stores/other/v2/orders", "/stores/demo/../x", "/stores/demo/v2/orders?x=1", "/stores/demo/%2e%2e/x"])
      expect((await ports.bigcommerce(path)).status).toBe(0);
    expect(fake.sent).toHaveLength(1);
    expect(fake.sent[0]?.body).toMatchObject({ method: "GET", organizationIntegrationId: "orgint_bc", path: "/stores/demo/v2/orders" });
  });

  test("QuickBooks port allows only the four native read tools", async () => {
    const fake = fakeType();
    const ports = createPorts(fake.type, "run", bindings("export"));
    for (const tool of ["quickbooks_create_entity", "quickbooks_update_entity", "quickbooks_delete_entity", "quickbooks_send_invoice"])
      expect(await ports.qbo!(tool as "quickbooks_get_entity", {})).toEqual({ ok: false, error: "tool_not_allowed", retryable: false });
    expect(fake.sent).toHaveLength(0);
    expect(await ports.qbo!("quickbooks_get_company_info", {})).toMatchObject({ ok: true, data: { CompanyInfo: { Country: "US" } } });
    expect(fake.sent[0]?.body).toMatchObject({ service: "quickbooks", name: "quickbooks_get_company_info", organizationIntegrationId: "orgint_qbo" });
  });

  test("QuickBooks tool errors keep their retryable flag; text-only results are decoded", async () => {
    const retry = fakeType({ reply: () => ({ body: { content: [], isError: true, structuredContent: { error: { retryable: true } } } }) });
    expect(await createPorts(retry.type, "run", bindings("preview")).qbo!("quickbooks_list_entities", { entity: "Invoice", startPosition: 1, maxResults: 100 }))
      .toEqual({ ok: false, error: "qbo_tool_error", retryable: true });
    const text = fakeType({ reply: () => ({ body: { content: [{ type: "text", text: "{\"QueryResponse\":{}}" }] } }) });
    expect(await createPorts(text.type, "run", bindings("preview")).qbo!("quickbooks_list_entities", { entity: "Invoice", startPosition: 1, maxResults: 100 }))
      .toEqual({ ok: true, data: { QueryResponse: {} } });
  });

  test("preview never writes Type data; export sends the exact expected version", async () => {
    const fake = fakeType();
    expect(await createPorts(fake.type, "run", bindings("preview")).cas("bcqbo/demo/x", { a: 1 }, null))
      .toEqual({ ok: false, error: "preview_mode_records_nothing" });
    expect(fake.sent).toHaveLength(0);
    const ports = createPorts(fake.type, "run", bindings("export"));
    const created = await ports.cas("bcqbo/demo/x", { a: 1 }, null);
    expect(created.ok).toBe(true);
    expect(await ports.cas("bcqbo/demo/x", { a: 2 }, null)).toEqual({ ok: false, error: "version_conflict" });
    expect(await ports.read("bcqbo/demo/x")).toEqual({ ok: true, value: { a: 1 }, version: created.version as string });
    expect(await ports.read("bcqbo/demo/missing")).toEqual({ ok: true, value: null, version: null });
    expect(await ports.read("other/secret")).toEqual({ ok: false, error: "invalid_state_key" });
  });

  test("Slack posts only in export mode, only to the bound channel, and needs a receipt", async () => {
    const preview = fakeType();
    expect(await createPorts(preview.type, "run", bindings("preview")).slack!("hi")).toBe("outcome_unknown");
    expect(preview.sent).toHaveLength(0);
    const fake = fakeType();
    expect(await createPorts(fake.type, "run", bindings("export")).slack!("hi")).toBe("sent");
    expect(fake.sent[0]?.body).toMatchObject({ service: "slack", name: "slack_post_message", arguments: { channel_id: "C123", text: "hi" } });
    const vague = fakeType({ reply: () => ({ body: { content: [{ type: "text", text: "probably sent" }] } }) });
    expect(await createPorts(vague.type, "run", bindings("export")).slack!("hi")).toBe("outcome_unknown");
  });
});

describe("Python planner IPC", () => {
  test("runs credential-free and returns a plan", async () => {
    const result = await runPlanner({ config: fixture.config, orders: [], customers: null, vendors: null });
    expect(result).toMatchObject({ ok: true, value: { status: "planned", orders: [] } });
    expect(Object.keys(plannerEnvironment).sort()).toEqual(["LANG", "PATH", "PYTHONDONTWRITEBYTECODE"]);
  });
  test("malformed or oversized input never yields a plan", async () => {
    expect(await runPlanner({ config: null })).toMatchObject({ ok: true, value: { status: "invalid" } });
    expect(await runPlanner({ data: "x".repeat(65 * 1024 * 1024) })).toEqual({ ok: false, error: "planner_input_invalid_or_too_large" });
  });
});

describe("static guard: no write paths exist in shipped code", () => {
  const dir = new URL(".", import.meta.url).pathname;
  const sources = readdirSync(dir).filter((name) => /\.(ts|py)$/.test(name) && !name.startsWith("test_"));
  const read = (name: string) => readFileSync(join(dir, name), "utf8");

  test("the shipped source set is what the guard expects", () => {
    expect(sources.sort()).toEqual(["ack.ts", "config.ts", "journal.ts", "main.py", "main.ts", "preview.ts", "qbo.ts", "render.ts", "scheduler.ts", "sync.ts"]);
  });

  test("exactly one integrations.request call, and it is hard-coded GET", () => {
    const all = sources.map(read).join("\n");
    expect(all.match(/integrations\.request\s*\(/g)).toHaveLength(1);
    expect(all.match(/method:\s*"[A-Z]+"/g)).toEqual(['method: "GET"']);
    expect(/\bmethod:\s*[a-z]/.test(all)).toBe(false);
    expect(/["'](POST|PUT|PATCH|DELETE)["']/.test(all)).toBe(false);
  });

  test("only the four QuickBooks read tools are named anywhere; Slack only posts", () => {
    const all = sources.map(read).join("\n");
    expect([...new Set(all.match(/quickbooks_[a-z_]+/g))].sort()).toEqual([...QBO_READ_TOOLS].sort());
    expect([...new Set(all.match(/slack_[a-z_]+/g))]).toEqual(["slack_post_message"]);
    expect(all.match(/tools\.call\s*\(/g)).toHaveLength(2);
    expect(/quickbooks\.api\.intuit|\/v3\/company|oauth|refresh_token/i.test(all)).toBe(false);
  });
});
