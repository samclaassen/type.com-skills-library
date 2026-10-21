import { z } from "zod";
import { hash, runSync, type Ports, type SyncArgs } from "./sync";

export const pollingSchema = z.strictObject({
  bootstrapSince: z.iso.datetime({ offset: true }),
  overlapSeconds: z.number().int().min(0).max(3600).default(120),
  maxWindowSeconds: z.number().int().min(60).max(604800).default(86400),
}).refine((value) => value.overlapSeconds < value.maxWindowSeconds, {
  message: "overlapSeconds must be less than maxWindowSeconds",
});
export type PollArgs = Omit<SyncArgs, "since" | "until" | "afterOrderId" | "reexportOrderIds"> & {
  polling: z.input<typeof pollingSchema>; bindingExtras?: unknown;
};
const iso = z.iso.datetime({ offset: true });
const windowSchema = z.strictObject({ since: iso, until: iso, afterOrderId: z.string().regex(/^(0|[1-9]\d{0,14})$/) });
const stateSchema = z.strictObject({
  schema: z.literal(1), bindingHash: z.string().regex(/^[a-f0-9]{64}$/),
  completedThrough: iso.nullable(), window: windowSchema.nullable(),
});
type State = z.infer<typeof stateSchema>;
/** A prepared batch may move the cursor: every order of the window has a durable record (pending, review, ...). */
const advanceSchema = z.object({
  status: z.literal("prepared"),
  cursorAdvanceSafe: z.literal(true),
  nextAfterOrderId: z.string().regex(/^[1-9]\d{0,14}$/).nullable(),
  window: z.object({ since: iso, until: iso }),
});

/** "Export new orders since last run": a fixed window and ID cursor survive invocations. */
export async function runPoll(args: PollArgs, ports: Ports) {
  const blocked = (reason: string, batch?: unknown) => ({ status: "blocked", reason, ...(batch === undefined ? {} : { batch }) });
  const parsed = pollingSchema.safeParse(args.polling);
  const nowParsed = iso.safeParse(ports.now());
  const store = z.string().regex(/^[A-Za-z0-9_-]{1,40}$/).safeParse(args.config.storeHash);
  if (!parsed.success || !nowParsed.success || !store.success) return blocked("invalid_polling_input");
  const settings = parsed.data, now = Date.parse(nowParsed.data), bootstrap = Date.parse(settings.bootstrapSince);
  if (bootstrap > now) return blocked("bootstrap_in_future");
  const { polling: _polling, bindingExtras, ...base } = args;
  const newWindow = (completed: string | null) => {
    const since = Math.max(bootstrap, completed ? Date.parse(completed) - settings.overlapSeconds * 1000 : bootstrap);
    const until = Math.min(now, since + settings.maxWindowSeconds * 1000);
    return { since: new Date(since).toISOString(), until: new Date(until).toISOString(), afterOrderId: "0" };
  };
  const key = "bcqbo/" + store.data + "/poll";
  const current = await ports.read(key);
  if (!current.ok) return blocked("poll_checkpoint_read_failed");
  const bindingHash = hash([args.config, args.timezone, bindingExtras ?? null, settings]);
  if (args.mode === "preview") {
    // Preview the next window without writing the checkpoint.
    const checkpoint = stateSchema.safeParse(current.value);
    const window = checkpoint.success && checkpoint.data.bindingHash === bindingHash
      ? checkpoint.data.window ?? newWindow(checkpoint.data.completedThrough) : newWindow(null);
    return { status: "preview", batch: await runSync({ ...base, ...window }, ports), window, checkpointWritten: false };
  }
  let state: State, version: string;
  if (current.value === null) {
    if (current.version !== null) return blocked("invalid_poll_checkpoint");
    state = { schema: 1, bindingHash, completedThrough: null, window: newWindow(null) };
    const initialized = await ports.cas(key, state, null);
    if (!initialized.ok || !initialized.version) return blocked("poll_checkpoint_init_conflict_or_unknown");
    version = initialized.version;
  } else {
    const checked = stateSchema.safeParse(current.value);
    if (!checked.success || !current.version) return blocked("invalid_poll_checkpoint");
    state = checked.data; version = current.version;
    if (state.bindingHash !== bindingHash) return blocked("poll_configuration_changed_requires_review");
    if (state.completedThrough && Date.parse(state.completedThrough) > now) return blocked("clock_before_poll_checkpoint");
    if (!state.window) {
      if (state.completedThrough && Date.parse(state.completedThrough) === now)
        return { status: "idle", completedThrough: state.completedThrough };
      state = { ...state, window: newWindow(state.completedThrough) };
      const started = await ports.cas(key, state, version);
      if (!started.ok || !started.version) return blocked("poll_window_commit_conflict_or_unknown");
      version = started.version;
    }
  }
  const window = state.window;
  if (!window || Date.parse(window.since) > Date.parse(window.until) || Date.parse(window.until) > now)
    return blocked("invalid_poll_window");
  const batch = await runSync({ ...base, ...window }, ports);
  const advance = advanceSchema.safeParse(batch);
  if (!advance.success) return blocked("batch_requires_review_checkpoint_unchanged", batch);
  if (advance.data.window.since !== window.since || advance.data.window.until !== window.until)
    return blocked("batch_window_mismatch", batch);
  if (advance.data.nextAfterOrderId !== null && BigInt(advance.data.nextAfterOrderId) <= BigInt(window.afterOrderId))
    return blocked("batch_cursor_not_advancing", batch);
  const next: State = advance.data.nextAfterOrderId === null
    ? { ...state, completedThrough: window.until, window: null }
    : { ...state, window: { ...window, afterOrderId: advance.data.nextAfterOrderId } };
  const saved = await ports.cas(key, next, version);
  if (!saved.ok || !saved.version) return blocked("poll_checkpoint_advance_conflict_or_unknown", batch);
  return { status: "polled", batch, completedThrough: next.completedThrough, pendingWindow: next.window, checkpointWritten: true };
}
