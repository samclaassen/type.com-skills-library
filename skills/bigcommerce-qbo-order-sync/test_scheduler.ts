import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runAcknowledge } from "./ack";
import { fixturePorts, fixtureSchema, type Fixture } from "./preview";
import { runPoll, type PollArgs } from "./scheduler";
import type { Ports } from "./sync";

const fixture: Fixture = fixtureSchema.parse(JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8")));
type Journal = Map<string, { value: unknown; version: string }>;
const polling = { bootstrapSince: "2026-10-15T00:00:00Z", overlapSeconds: 120, maxWindowSeconds: 172800 };
const args = (mode: "preview" | "export", extra: Partial<PollArgs> = {}): PollArgs =>
  ({ config: fixture.config, mode, timezone: "UTC", polling, ...extra });

function harness(options: { journal?: Journal; now?: string; failRead?: boolean; failOrderCas?: boolean } = {}) {
  const journal = options.journal ?? new Map();
  const { ports } = fixturePorts(fixture, journal);
  const calls: string[] = [];
  const wrapped: Ports = {
    ...ports,
    now: () => options.now ?? fixture.window.until,
    async bigcommerce(path, query) { calls.push(path + (query?.min_id ? "?min_id=" + query.min_id : "")); return ports.bigcommerce(path, query); },
    async read(key) { return options.failRead ? { ok: false, error: "unavailable" } : ports.read(key); },
    async cas(key, value, version) {
      return options.failOrderCas && key.includes("/order/") ? { ok: false, error: "unavailable" } : ports.cas(key, value, version);
    },
  };
  return { ports: wrapped, journal, calls };
}
const checkpoint = (journal: Journal) => journal.get("bcqbo/demo/poll")?.value as Record<string, any> | undefined;

describe("polling: export new orders since the last run", () => {
  test("first export poll fixes a window, exports it, then marks it complete", async () => {
    const h = harness();
    const result = await runPoll(args("export"), h.ports) as Record<string, any>;
    expect(result).toMatchObject({ status: "polled", checkpointWritten: true, completedThrough: "2026-10-17T00:00:00.000Z", pendingWindow: null });
    expect(result.batch.counts).toMatchObject({ prepared: 2, review: 2 });
    expect(result.batch.acknowledge).toMatchObject({ batchId: result.batch.batchId });
    expect(checkpoint(h.journal)).toMatchObject({ completedThrough: "2026-10-17T00:00:00.000Z", window: null });
  });

  test("the next poll overlaps the previous window and does not re-export an acknowledged batch", async () => {
    const h = harness();
    const first = await runPoll(args("export"), h.ports) as Record<string, any>;
    expect(await runAcknowledge({ config: fixture.config, ...first.batch.acknowledge }, h.ports)).toMatchObject({ status: "acknowledged" });
    const later = harness({ journal: h.journal, now: "2026-10-17T06:00:00Z" });
    const result = await runPoll(args("export"), later.ports) as Record<string, any>;
    expect(result.batch.window).toEqual({ since: "2026-10-16T23:58:00.000Z", until: "2026-10-17T06:00:00.000Z" });
    expect(result.batch.counts).toMatchObject({ prepared: 0, carried: 0, awaitingConfirmation: 1 });
  });

  test("without an acknowledgement the next poll still advances and carries the pending orders into its batch", async () => {
    const h = harness();
    const first = await runPoll(args("export"), h.ports) as Record<string, any>;
    const later = harness({ journal: h.journal, now: "2026-10-17T06:00:00Z" });
    const result = await runPoll(args("export"), later.ports) as Record<string, any>;
    expect(result).toMatchObject({ status: "polled", checkpointWritten: true });
    expect(result.batch.counts).toMatchObject({ prepared: 2, carried: 2 });
    expect(result.batch.supersedes).toEqual([first.batch.batchId]);
    expect(await runAcknowledge({ config: fixture.config, ...first.batch.acknowledge }, h.ports)).toMatchObject({ status: "blocked", reason: "batch_superseded" });
  });

  test("a full page keeps the window open and continues from the ID cursor", async () => {
    const h = harness();
    const first = await runPoll(args("export", { maxOrders: 1 }), h.ports) as Record<string, any>;
    expect(first.pendingWindow).toMatchObject({ afterOrderId: "1001" });
    const second = await runPoll(args("export", { maxOrders: 1 }), h.ports) as Record<string, any>;
    expect(second.pendingWindow).toMatchObject({ afterOrderId: "1002" });
    expect(h.calls.filter((c) => c.includes("?min_id="))).toEqual(["/stores/demo/v2/orders?min_id=1", "/stores/demo/v2/orders?min_id=1002"]);
  });

  test("an unsafe batch (journal write failed) leaves the checkpoint where it was", async () => {
    const h = harness({ failOrderCas: true });
    const result = await runPoll(args("export"), h.ports) as Record<string, any>;
    expect(result).toMatchObject({ status: "blocked", reason: "batch_requires_review_checkpoint_unchanged" });
    expect(checkpoint(h.journal)).toMatchObject({ completedThrough: null, window: { since: "2026-10-15T00:00:00.000Z", afterOrderId: "0" } });
  });

  test("checkpoint read failure stops before any provider request", async () => {
    const h = harness({ failRead: true });
    expect(await runPoll(args("export"), h.ports)).toMatchObject({ status: "blocked", reason: "poll_checkpoint_read_failed" });
    expect(h.calls).toHaveLength(0);
  });

  test("a changed configuration needs review instead of reusing the cursor", async () => {
    const h = harness();
    await runPoll(args("export"), h.ports);
    const changed = await runPoll(args("export", { config: { ...fixture.config, termsDays: 15 } }), h.ports);
    expect(changed).toMatchObject({ status: "blocked", reason: "poll_configuration_changed_requires_review" });
  });

  test("preview shows the next window without writing anything", async () => {
    const h = harness();
    const result = await runPoll(args("preview"), h.ports) as Record<string, any>;
    expect(result).toMatchObject({ status: "preview", checkpointWritten: false });
    expect(h.journal.size).toBe(0);
  });

  test("future bootstrap and a caught-up checkpoint are handled without exports", async () => {
    const h = harness({ now: "2026-10-14T00:00:00Z" });
    expect(await runPoll(args("export"), h.ports)).toMatchObject({ status: "blocked", reason: "bootstrap_in_future" });
    const done = harness();
    await runPoll(args("export"), done.ports);
    expect(await runPoll(args("export"), done.ports)).toMatchObject({ status: "idle" });
  });
});
