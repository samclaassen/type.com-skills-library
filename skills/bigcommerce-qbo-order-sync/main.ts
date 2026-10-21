import { defineAutomation, type Type, type JsonValue } from "@type-dot-com/sdk";
import { Result, ResultAsync } from "neverthrow";
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { runAcknowledge } from "./ack";
import { configSchema, storeTimezone } from "./config";
import { batchIdSchema, sha256Schema } from "./journal";
import { QBO_READ_TOOLS, type QboCall } from "./qbo";
import type { OutputFile } from "./render";
import { pollingSchema, runPoll } from "./scheduler";
import { MAX_PLANNER_INPUT_BYTES, runSync, type Delivery, type Ports } from "./sync";

const integrationId = z.string().regex(/^orgint_[a-zA-Z0-9]+$/);
const isoTime = z.iso.datetime({ offset: true });

export const inputSchema = z.strictObject({
  config: configSchema,
  integrationIds: z.strictObject({
    bigcommerce: integrationId,
    quickbooks: integrationId.optional(),
    slack: integrationId.optional(),
  }),
  mode: z.enum(["preview", "export"]).default("preview"),
  maxOrders: z.number().int().min(1).max(100).default(100),
  since: isoTime.optional(),
  until: isoTime.optional(),
  polling: pollingSchema.optional(),
  timezone: z.string().min(1).max(100).refine((value) => storeTimezone(value) !== null, "Invalid IANA timezone"),
  afterOrderId: z.string().regex(/^(0|[1-9]\d{0,14})$/).optional(),
  reexportOrderIds: z.array(z.string().regex(/^[1-9]\d{0,14}$/)).max(100).default([]),
  slackChannel: z.string().regex(/^[CG][A-Z0-9]+$/).optional(),
  outputDir: z.string().max(300).regex(/^\/[A-Za-z0-9._/-]+$/)
    .refine((value) => !value.split("/").includes(".."), "outputDir must not contain ..").optional(),
  /** Sent by the running agent after it uploaded every file of the batch folder to Type. Makes the batch's orders exported. */
  acknowledgeBatch: z.strictObject({ batchId: batchIdSchema, contentSha256: sha256Schema }).optional(),
}).superRefine((args, ctx) => {
  if (args.acknowledgeBatch) {
    if (args.mode !== "export") ctx.addIssue({ code: "custom", message: "acknowledgeBatch records state; pass mode: export", path: ["mode"] });
    if (args.polling || args.since !== undefined || args.until !== undefined || args.afterOrderId !== undefined || args.reexportOrderIds.length)
      ctx.addIssue({ code: "custom", message: "acknowledgeBatch is a separate call without window, polling or re-export", path: ["acknowledgeBatch"] });
    return;
  }
  if (args.polling && (args.since !== undefined || args.until !== undefined || args.afterOrderId !== undefined || args.reexportOrderIds.length))
    ctx.addIssue({ code: "custom", message: "Polling owns its window and cursor; re-export with a fixed window", path: ["polling"] });
  if (!args.polling && (!args.since || !args.until))
    ctx.addIssue({ code: "custom", message: "Provide a fixed window or polling settings", path: ["since"] });
  if (args.since && args.until && Date.parse(args.since) > Date.parse(args.until))
    ctx.addIssue({ code: "custom", message: "since must not be later than until", path: ["until"] });
  if (args.slackChannel && !args.integrationIds.slack)
    ctx.addIssue({ code: "custom", message: "Slack delivery requires a bound connection", path: ["integrationIds", "slack"] });
});
export type Input = z.output<typeof inputSchema>;

const stateKey = z.string().regex(/^bcqbo\/[A-Za-z0-9._:/-]+$/).max(200);
const jsonEncode = Result.fromThrowable((value: unknown) => JSON.stringify(value), () => "invalid_json");
const jsonDecode = Result.fromThrowable((value: string): unknown => JSON.parse(value), () => "invalid_json");
const slackSent = z.object({ status: z.literal("sent"), channelId: z.string(), ts: z.string().nullable() });
const qboError = z.object({ error: z.object({ retryable: z.boolean() }) });
const MAX_PLANNER_OUTPUT_BYTES = 2 * MAX_PLANNER_INPUT_BYTES;
export const plannerEnvironment = Object.freeze({
  PATH: "/usr/local/bin:/usr/bin:/bin",
  LANG: "C.UTF-8",
  PYTHONDONTWRITEBYTECODE: "1",
});

/** Pure Python IPC: credentials and shell evaluation never reach the child. */
export async function runPlanner(input: unknown): ReturnType<Ports["plan"]> {
  const encoded = jsonEncode(input);
  if (encoded.isErr() || encoded.value === undefined || Buffer.byteLength(encoded.value, "utf8") > MAX_PLANNER_INPUT_BYTES)
    return { ok: false, error: "planner_input_invalid_or_too_large" };
  const spawned = Result.fromThrowable(
    () => Bun.spawn(["python3", "-I", `${import.meta.dir}/main.py`], {
      env: { ...plannerEnvironment },
      stdin: new Blob([encoded.value]),
      stdout: "pipe",
      stderr: "ignore",
    }),
    () => "planner_unavailable",
  )();
  if (spawned.isErr()) return { ok: false, error: spawned.error };
  const child = spawned.value;
  const timeout = setTimeout(() => child.kill(), 60000);
  const collected = await ResultAsync.fromPromise((async () => {
    const reader = child.stdout.getReader();
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      bytes += next.value.length;
      if (bytes > MAX_PLANNER_OUTPUT_BYTES) {
        child.kill();
        await reader.cancel();
        return { ok: false, error: "planner_output_too_large" };
      }
      chunks.push(next.value);
    }
    const exit = await child.exited;
    if (exit !== 0) return { ok: false, error: "planner_failed" };
    const combined = new Uint8Array(bytes);
    let position = 0;
    for (const chunk of chunks) {
      combined.set(chunk, position);
      position += chunk.length;
    }
    const decoded = jsonDecode(new TextDecoder().decode(combined));
    return decoded.isErr() ? { ok: false, error: "planner_invalid_json" } : { ok: true, value: decoded.value };
  })(), () => "planner_io_failed");
  clearTimeout(timeout);
  if (collected.isErr()) {
    child.kill();
    return { ok: false, error: collected.error };
  }
  const value = collected.value;
  return value.ok ? { ok: true, value: value.value } : { ok: false, error: value.error ?? "planner_failed" };
}

export type Bindings = {
  mode: "preview" | "export"; storeHash: string;
  integrationIds: { bigcommerce: string; quickbooks?: string; slack?: string }; slackChannel?: string;
  /** Parent folder; each batch is published into its own new subfolder. Default ./exports. */
  outputParent?: string;
};

/**
 * Read-only provider adapters. BigCommerce requests are always GET; QuickBooks is limited to
 * four native read tools; Type data writes happen only in export mode with a version precondition.
 */
export function createPorts(type: Type, runId: string, bindings: Bindings): Ports {
  const { integrationIds: ids } = bindings;
  const quickbooksId = ids.quickbooks;
  const qbo: QboCall | null = quickbooksId ? async (tool, args) => {
    if (!(QBO_READ_TOOLS as readonly string[]).includes(tool)) return { ok: false, error: "tool_not_allowed", retryable: false };
    const result = await type.tools.call({ service: "quickbooks", name: tool, organizationIntegrationId: quickbooksId, arguments: args });
    if (result.isErr()) return { ok: false, error: "qbo_transport_failed", retryable: false };
    if (result.value.isError) {
      const detail = qboError.safeParse(result.value.structuredContent);
      return { ok: false, error: "qbo_tool_error", retryable: detail.success && detail.data.error.retryable };
    }
    if (result.value.structuredContent !== undefined) return { ok: true, data: result.value.structuredContent };
    const text = result.value.content.find((part) => part.type === "text")?.text;
    const decoded = text === undefined ? null : jsonDecode(text);
    return decoded?.isOk() ? { ok: true, data: decoded.value } : { ok: false, error: "qbo_response_invalid", retryable: false };
  } : null;
  const slackId = ids.slack, channel = bindings.slackChannel;
  const slack = slackId && channel ? async (text: string): Promise<"sent" | "outcome_unknown"> => {
    if (bindings.mode !== "export") return "outcome_unknown";
    const result = await type.tools.call({
      service: "slack", name: "slack_post_message", organizationIntegrationId: slackId,
      arguments: { channel_id: channel, text: text.slice(0, 4000), response_format: "json" },
    });
    if (result.isErr() || result.value.isError) return "outcome_unknown";
    const sent = slackSent.safeParse(result.value.structuredContent);
    return sent.success && sent.data.channelId === channel ? "sent" : "outcome_unknown";
  } : null;
  return {
    runId,
    now: () => new Date().toISOString(),
    plan: runPlanner,
    sleep: async (ms) => { await Bun.sleep(Math.max(0, Math.min(ms, 30000))); },
    deliver: async (files, batchId) => publishFiles(files, bindings.outputParent ?? join(process.cwd(), "exports"), batchId),
    qbo,
    slack,
    async bigcommerce(path, query) {
      const prefix = `/stores/${bindings.storeHash}/`;
      if (!path.startsWith(prefix) || /[?#\\%]/.test(path) || path.split("/").some((part) => part === ".." || part === "."))
        return { status: 0, data: { error: "invalid_provider_path" } };
      const result = await type.integrations.request({
        organizationIntegrationId: ids.bigcommerce, method: "GET", path, ...(query ? { query } : {}),
      });
      if (result.isErr()) return { status: 0, data: { error: "transport_failed" } };
      if (result.value.truncated) return { status: 0, data: { error: "response_truncated" } };
      return { status: result.value.status, data: result.value.data, headers: result.value.headers };
    },
    async read(key) {
      if (!stateKey.safeParse(key).success) return { ok: false, error: "invalid_state_key" };
      const result = await type.data.read(key, { scope: "space" });
      if (result.isErr()) return { ok: false, error: "state_read_failed" };
      return { ok: true, value: result.value.found ? result.value.value : null, version: result.value.version };
    },
    async cas(key, value, expectedVersion) {
      if (bindings.mode !== "export") return { ok: false, error: "preview_mode_records_nothing" };
      const json = z.json().safeParse(value);
      if (!stateKey.safeParse(key).success || !json.success) return { ok: false, error: "invalid_state_record" };
      const result = await type.data.record(key, json.data, { scope: "space", expectedVersion });
      if (result.isErr()) return { ok: false, error: result.error.isVersionConflict ? "version_conflict" : "state_write_failed" };
      return { ok: true, version: result.value.version };
    },
  };
}

const ignore = Result.fromThrowable((action: () => void) => action(), () => "ignored");

/**
 * Writes a batch folder all or nothing: files are written and fsynced in a hidden staging folder, which is then
 * renamed to <parent>/<batchId>. An existing batch folder is never reused or mixed with new files. A written folder
 * is not a delivery: the agent still has to upload it to Type and acknowledge the batch.
 */
export function publishFiles(files: OutputFile[], parent: string, batchId: string): Delivery {
  if (!/^[A-Za-z0-9-]{1,64}$/.test(batchId)) return { ok: false, error: "invalid_batch_id" };
  if (files.some((file) => !/^[A-Za-z0-9._-]{1,100}$/.test(file.name) || file.name.startsWith("."))) return { ok: false, error: "invalid_file_name" };
  const target = join(parent, batchId), staging = join(parent, "." + batchId + ".partial");
  if (existsSync(target)) return { ok: false, error: "output_folder_exists" };
  const written = Result.fromThrowable(() => {
    mkdirSync(parent, { recursive: true });
    mkdirSync(staging);
    for (const file of files) {
      const fd = openSync(join(staging, file.name), "wx");
      writeFileSync(fd, file.content, "utf8");
      fsyncSync(fd);
      closeSync(fd);
    }
    renameSync(staging, target);
    const directory = openSync(parent, "r");
    fsyncSync(directory);
    closeSync(directory);
  }, () => "output_write_failed")();
  if (written.isErr()) {
    ignore(() => rmSync(staging, { recursive: true, force: true }));
    return { ok: false, error: written.error };
  }
  return { ok: true, directory: target };
}

/** File metadata for the run output. File contents are never returned (the SDK caps results at 64 KiB). */
export function describeFiles(files: OutputFile[], directory: string | null) {
  return files.map(({ name, bytes, rows, sha256 }) => ({ name, bytes, rows, sha256, path: directory ? join(directory, name) : null }));
}

/** Results are kept well below the SDK's 64 KiB limit; the batch folder's manifest.json always has the full detail. */
export const RESULT_BUDGET_BYTES = 48 * 1024;
const size = (value: unknown) => Buffer.byteLength(JSON.stringify(value) ?? "", "utf8");
const LISTS = ["orders", "reviewQueue", "carried", "notCarried", "warnings", "supersedes"] as const;
type Shaped = Record<string, unknown>;
const shrinkLists = (record: Shaped, keep: number): Shaped => {
  const out: Shaped = { ...record };
  for (const key of LISTS) {
    const list = out[key];
    if (Array.isArray(list) && list.length > keep) { out[key] = list.slice(0, keep); out[key + "Omitted"] = list.length - keep; }
  }
  return out;
};
/**
 * Hard guard on the result size: per-order lists are truncated (with an "<name>Omitted" count), then the per-file
 * list is replaced by a count, and as a last resort only status, batch identity and counts remain. The acknowledge
 * values, batch folder path and manifest path are kept at every step.
 */
export function boundOutput(output: Shaped): Shaped {
  if (size(output) <= RESULT_BUDGET_BYTES) return output;
  const nestedBatch = typeof output.batch === "object" && output.batch !== null ? (output.batch as Shaped) : null;
  const apply = (fn: (r: Shaped) => Shaped) => (nestedBatch ? { ...output, batch: fn(nestedBatch) } : fn(output));
  for (const keep of [50, 10, 0]) {
    const shaped = apply((r) => shrinkLists(r, keep));
    if (size(shaped) <= RESULT_BUDGET_BYTES) return { ...shaped, truncated: true };
  }
  const delivery = (output.delivery ?? {}) as Shaped;
  const files = Array.isArray(delivery.files) ? (delivery.files as Shaped[]) : [];
  const compactDelivery = { directory: delivery.directory ?? null, error: delivery.error ?? null, fileCount: files.length,
    totalBytes: files.reduce((sum, f) => sum + (typeof f.bytes === "number" ? f.bytes : 0), 0),
    manifestPath: delivery.directory ? join(String(delivery.directory), "manifest.json") : null };
  const lean = { ...apply((r) => shrinkLists(r, 0)), delivery: compactDelivery, truncated: true };
  if (size(lean) <= RESULT_BUDGET_BYTES) return lean;
  const source = nestedBatch ?? output;
  return { status: output.status, reason: output.reason ?? null, batchId: source.batchId ?? null, acknowledge: source.acknowledge ?? null,
    ...(source.acknowledgedBatchesCompleted ? { acknowledgedBatchesCompleted: source.acknowledgedBatchesCompleted } : {}),
    counts: source.counts ?? null, cursorAdvanceSafe: source.cursorAdvanceSafe ?? null, delivery: compactDelivery, truncated: true };
}

export default defineAutomation({
  input: inputSchema,
  async run(args, { type, runId }) {
    const { polling, since, until, outputDir, integrationIds, slackChannel, reexportOrderIds, afterOrderId, acknowledgeBatch, ...base } = args;
    const ports = createPorts(type, runId, { mode: args.mode, storeHash: args.config.storeHash, integrationIds, slackChannel,
      ...(outputDir ? { outputParent: outputDir } : {}) });
    if (acknowledgeBatch) {
      const acknowledged = await runAcknowledge({ config: args.config, ...acknowledgeBatch }, ports);
      const checked = z.json().safeParse(boundOutput(acknowledged));
      return { action: "skip", output: checked.success ? checked.data : { status: "blocked", reason: "output_invalid" } };
    }
    const output = polling
      ? await runPoll({ ...base, polling, bindingExtras: { integrationIds, slackChannel: slackChannel ?? null } }, ports)
      : await runSync({ ...base, since: since ?? "", until: until ?? "", reexportOrderIds,
        ...(afterOrderId ? { afterOrderId } : {}) }, ports);
    const batch = "batch" in output && output.batch && typeof output.batch === "object" ? output.batch : output;
    const files = "files" in batch && Array.isArray(batch.files) ? (batch.files as OutputFile[]) : [];
    const published = "delivery" in batch && batch.delivery && typeof batch.delivery === "object" && "directory" in batch.delivery
      && typeof batch.delivery.directory === "string" ? batch.delivery.directory : null;
    const deliveryError = "deliveryError" in batch && typeof batch.deliveryError === "string" ? batch.deliveryError : null;
    const delivered = { directory: published, error: deliveryError, files: describeFiles(published ? files : [], published) };
    const { files: _files, delivery: _delivery, ...summary } = batch as Record<string, unknown>;
    const acknowledge = (summary.acknowledge ?? null) as { batchId: string; contentSha256: string } | null;
    const ackedEarlier = Array.isArray(summary.acknowledgedBatchesCompleted) ? (summary.acknowledgedBatchesCompleted as string[]) : [];
    const steps = [
      ...ackedEarlier.map((id) => `Batch ${id} was acknowledged — its files are ready to import. Tell the user to import the uploaded files of batch ${id} in QuickBooks Online (steps in its summary.md).`),
      ...(acknowledge ? [`Upload every file in ${published} to Type (upload_file), then call this skill again with mode "export" and acknowledgeBatch ${JSON.stringify(acknowledge)}. Do not tell the user to import before the acknowledgement returns status "acknowledged".`] : []),
    ];
    const nextStep = steps.length ? steps.join(" ") : null;
    const result = batch === output ? { ...summary, delivery: delivered, nextStep } : { ...output, batch: summary, delivery: delivered, nextStep };
    const checked = z.json().safeParse(boundOutput(result));
    const value: JsonValue = checked.success ? checked.data : { status: "blocked", reason: "output_invalid" };
    return { action: "skip", output: value };
  },
});
