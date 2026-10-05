/**
 * Technician app offline support (workflows §3: chão de fábrica sem sinal).
 *
 * - Snapshot cache: the last "my work orders" list and each work order detail are saved on the device
 *   (localStorage) and served — flagged as offline, with the time they were saved — when the API is
 *   unreachable. Never shown as live data.
 * - Action queue: task checks, labor entries and state transitions made offline are queued in order and
 *   replayed when the connection returns. Each action carries a stable id (X-Client-Action-Id) so a
 *   replay after a lost response can be de-duplicated by the server.
 * - Pending actions are applied on top of the cached work order so the screen reflects what the
 *   technician did (local state machine, S4-5).
 */
import { isAxiosError } from "axios";
import type { LaborBody, TransitionBody, WorkOrder } from "./maintenanceTypes";
import { localAllowedTransitions } from "@/utils/maintenance";

export type QueuedKind = "task" | "labor" | "transition";

interface QueuedBase {
  id: string;
  wo_id: string;
  /** WO number for messages ("OS-114"). */
  wo_number?: string;
  created_at: string;
}

export type QueuedAction =
  | (QueuedBase & { kind: "task"; body: { task_id: string; done: boolean } })
  | (QueuedBase & { kind: "labor"; body: LaborBody })
  | (QueuedBase & { kind: "transition"; body: TransitionBody });

export interface FailedAction {
  action: QueuedAction;
  message: string;
  at: string;
}

const QUEUE_KEY = "fm-offline-queue";
const FAILED_KEY = "fm-offline-failed";
const LIST_KEY = "fm-my-wo";
const WO_KEY = (id: string) => `fm-wo:${id}`;

// ── storage helpers ───────────────────────────────────────────────

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch { /* quota / blocked: the queue still lives in memory for this session */ }
}

type Listener = () => void;
const listeners = new Set<Listener>();

export function subscribeQueue(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn();
}

// ── network classification ────────────────────────────────────────

/** True when the request never reached the server (offline / DNS / connection refused / timeout). */
export function isNetworkError(err: unknown): boolean {
  if (isAxiosError(err)) return !err.response;
  return err instanceof TypeError; // fetch-style network failure
}

export function isOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

// ── queue ─────────────────────────────────────────────────────────

export function getQueue(): QueuedAction[] {
  return read<QueuedAction[]>(QUEUE_KEY, []);
}

export function getFailed(): FailedAction[] {
  return read<FailedAction[]>(FAILED_KEY, []);
}

export function clearFailed() {
  write(FAILED_KEY, []);
  emit();
}

function newId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `a-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function enqueue(a: Omit<QueuedBase, "id" | "created_at"> & Pick<QueuedAction, "kind" | "body">): QueuedAction {
  const action = { ...a, id: newId(), created_at: new Date().toISOString() } as QueuedAction;
  write(QUEUE_KEY, [...getQueue(), action]);
  emit();
  return action;
}

export function pendingFor(woId: string): QueuedAction[] {
  return getQueue().filter((a) => a.wo_id === woId);
}

export type Executor = (a: QueuedAction) => Promise<unknown>;

let flushing: Promise<FlushResult> | null = null;

export interface FlushResult {
  sent: number;
  failed: number;
  /** Stopped because the network is still down. */
  stalled: boolean;
}

/**
 * Replays the queue in order. Network failure → stop and keep the rest (retry later).
 * Server rejection (4xx/5xx) → move the action to the failed list (shown to the technician) and continue.
 */
export function flushQueue(exec: Executor, errorMessage: (err: unknown) => string): Promise<FlushResult> {
  if (flushing) return flushing;
  flushing = (async () => {
    const result: FlushResult = { sent: 0, failed: 0, stalled: false };
    for (;;) {
      const [head] = getQueue();
      if (!head) break;
      try {
        await exec(head);
        result.sent += 1;
      } catch (err) {
        if (isNetworkError(err)) {
          result.stalled = true;
          break;
        }
        result.failed += 1;
        write(FAILED_KEY, [...getFailed(), { action: head, message: errorMessage(err), at: new Date().toISOString() }]);
      }
      write(QUEUE_KEY, getQueue().filter((a) => a.id !== head.id));
      emit();
    }
    return result;
  })().finally(() => { flushing = null; });
  return flushing;
}

// ── snapshots ─────────────────────────────────────────────────────

export interface Snapshot<T> {
  data: T;
  saved_at: string;
}

export interface Sourced<T> {
  data: T;
  /** "network" = live from the API; "cache" = device snapshot (offline). */
  source: "network" | "cache";
  saved_at: string;
}

export function saveList(list: WorkOrder[]) {
  const saved_at = new Date().toISOString();
  write(LIST_KEY, { data: list, saved_at });
  for (const wo of list) {
    // keep a richer detail snapshot if we have one
    const prev = read<Snapshot<WorkOrder> | null>(WO_KEY(wo.id), null);
    if (!prev || prev.saved_at < saved_at) write(WO_KEY(wo.id), { data: { ...prev?.data, ...wo }, saved_at });
  }
}

export function loadList(): Snapshot<WorkOrder[]> | null {
  return read<Snapshot<WorkOrder[]> | null>(LIST_KEY, null);
}

export function saveWorkOrder(wo: WorkOrder) {
  write(WO_KEY(wo.id), { data: wo, saved_at: new Date().toISOString() });
}

export function loadWorkOrder(id: string): Snapshot<WorkOrder> | null {
  return read<Snapshot<WorkOrder> | null>(WO_KEY(id), null);
}

/**
 * Runs `fetcher`; on success saves the snapshot, on a network failure falls back to the snapshot
 * (flagged "cache"). Other errors propagate (shown as errors, never masked).
 */
export async function withSnapshot<T>(fetcher: () => Promise<T>, save: (d: T) => void, load: () => Snapshot<T> | null): Promise<Sourced<T>> {
  // known offline: don't even try (no failed request per poll)
  if (!isOnline()) {
    const snap = load();
    if (snap) return { data: snap.data, source: "cache", saved_at: snap.saved_at };
  }
  try {
    const data = await fetcher();
    save(data);
    return { data, source: "network", saved_at: new Date().toISOString() };
  } catch (err) {
    const snap = isNetworkError(err) ? load() : null;
    if (snap) return { data: snap.data, source: "cache", saved_at: snap.saved_at };
    throw err;
  }
}

// ── optimistic application ────────────────────────────────────────

/** Applies queued actions to a work order (what the technician sees until the sync). */
export function applyPending(wo: WorkOrder, queue: QueuedAction[]): WorkOrder {
  let out = wo;
  for (const a of queue) {
    if (a.wo_id !== wo.id) continue;
    if (a.kind === "task") {
      out = { ...out, task_list: out.task_list.map((t) => (t.id === a.body.task_id ? { ...t, done: a.body.done } : t)) };
    } else if (a.kind === "labor") {
      out = {
        ...out,
        labor_hours: (out.labor_hours ?? 0) + a.body.hours,
        remaining_hours: a.body.remaining_hours ?? out.remaining_hours,
      };
    } else {
      const to = a.body.to;
      out = {
        ...out,
        state: to,
        hold_reason: to === "on_hold" ? a.body.hold_reason ?? out.hold_reason : out.hold_reason,
        remaining_hours: a.body.remaining_hours ?? out.remaining_hours,
        started_at: to === "in_progress" && !out.started_at ? a.created_at : out.started_at,
        completed_at: to === "completed" ? a.created_at : out.completed_at,
        history: [...(out.history ?? []), { at: a.created_at, by: null, from_state: out.state, to_state: to, note: a.body.note ?? null }],
        allowed_transitions: localAllowedTransitions(to, out.type, out.urgency),
      };
    }
  }
  return out;
}
