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
 * - Everything is scoped to the user (tenant + user id) who did it: on a shared team phone, technician B never
 *   sees A's cached work orders, and A's queued actions are only replayed with A's own session (they wait,
 *   untouched, until A logs in again). Snapshots of other users are dropped when someone else logs in.
 */
import { isAxiosError } from "axios";
import type { LaborBody, TransitionBody, WorkOrder } from "./maintenanceTypes";
import { localAllowedTransitions } from "@/utils/maintenance";

export type QueuedKind = "task" | "labor" | "transition";

interface QueuedBase {
  id: string;
  /** `${tenant_id}:${user_id}` of who did it (only replayed with that user's session). */
  owner?: string;
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
  owner?: string;
  action: QueuedAction;
  message: string;
  at: string;
}

const QUEUE_KEY = "fm-offline-queue";
const FAILED_KEY = "fm-offline-failed";
const LIST_PREFIX = "fm-my-wo";
const WO_PREFIX = "fm-wo:";
const LIST_KEY = () => `${LIST_PREFIX}:${currentOwner()}`;
const WO_KEY = (id: string) => `${WO_PREFIX}${currentOwner()}:${id}`;
/** Same key as the auth store's cached user (store/auth.ts): who is using the device now. */
const USER_CACHE = "fm-user";

/** `${tenant_id}:${user_id}` of the logged-in user ("anon" before login). */
export function currentOwner(): string {
  try {
    const raw = localStorage.getItem(USER_CACHE);
    const u = raw ? (JSON.parse(raw) as { id?: unknown; tenant_id?: unknown }) : null;
    if (u && typeof u.id === "string" && typeof u.tenant_id === "string") return `${u.tenant_id}:${u.id}`;
  } catch { /* storage blocked */ }
  return "anon";
}

/**
 * Called when a user logs in: drops the work-order snapshots of any other user of this device (and legacy
 * unscoped ones). Queued and failed actions of other users are kept — they replay when that user logs in.
 */
export function purgeOtherUsersSnapshots() {
  const owner = currentOwner();
  try {
    const drop: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (!k) continue;
      const isList = k === LIST_PREFIX || k.startsWith(`${LIST_PREFIX}:`);
      const isWo = k.startsWith(WO_PREFIX);
      if ((isList && k !== `${LIST_PREFIX}:${owner}`) || (isWo && !k.startsWith(`${WO_PREFIX}${owner}:`))) drop.push(k);
    }
    for (const k of drop) localStorage.removeItem(k);
  } catch { /* storage blocked */ }
  emit();
}

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

function allQueued(): QueuedAction[] {
  return read<QueuedAction[]>(QUEUE_KEY, []);
}

function allFailed(): FailedAction[] {
  return read<FailedAction[]>(FAILED_KEY, []);
}

/** The current user's pending actions (other users' stay stored, untouched). */
export function getQueue(): QueuedAction[] {
  const owner = currentOwner();
  return allQueued().filter((a) => a.owner === owner);
}

export function getFailed(): FailedAction[] {
  const owner = currentOwner();
  return allFailed().filter((f) => (f.owner ?? f.action.owner) === owner);
}

export function clearFailed() {
  const owner = currentOwner();
  write(FAILED_KEY, allFailed().filter((f) => (f.owner ?? f.action.owner) !== owner));
  emit();
}

function newId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `a-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function enqueue(a: Omit<QueuedBase, "id" | "created_at"> & Pick<QueuedAction, "kind" | "body">): QueuedAction {
  const action = { ...a, id: newId(), owner: currentOwner(), created_at: new Date().toISOString() } as QueuedAction;
  write(QUEUE_KEY, [...allQueued(), action]);
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

/** HTTP answers that mean "try again later" (server restarting/deploying, overloaded, rate limited). */
const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);

/** True when the action should stay queued and be retried (no answer, or a transient server answer). */
export function isRetryable(err: unknown): boolean {
  if (isNetworkError(err)) return true;
  const status = isAxiosError(err) ? err.response?.status : undefined;
  return status != null && RETRYABLE_STATUS.has(status);
}

/**
 * Replays the current user's queue in order. No answer or a transient answer (5xx, 408, 429) → stop and keep
 * the rest (retry later; the server de-duplicates replays by X-Client-Action-Id). A definitive rejection
 * (400/403/404/409/422…) → move the action to the failed list (shown to the technician) and continue.
 */
export function flushQueue(exec: Executor, errorMessage: (err: unknown) => string): Promise<FlushResult> {
  if (flushing) return flushing;
  flushing = (async () => {
    const result: FlushResult = { sent: 0, failed: 0, stalled: false };
    const owner = currentOwner();
    for (;;) {
      const [head] = getQueue();
      if (!head || currentOwner() !== owner) break;          // user switched mid-flush: stop
      try {
        await exec(head);
        result.sent += 1;
      } catch (err) {
        if (isRetryable(err)) {
          result.stalled = true;
          break;
        }
        result.failed += 1;
        write(FAILED_KEY, [...allFailed(), { owner, action: head, message: errorMessage(err), at: new Date().toISOString() }]);
      }
      write(QUEUE_KEY, allQueued().filter((a) => a.id !== head.id));
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
  write(LIST_KEY(), { data: list, saved_at });
  for (const wo of list) {
    // keep a richer detail snapshot if we have one
    const prev = read<Snapshot<WorkOrder> | null>(WO_KEY(wo.id), null);
    if (!prev || prev.saved_at < saved_at) write(WO_KEY(wo.id), { data: { ...prev?.data, ...wo }, saved_at });
  }
}

export function loadList(): Snapshot<WorkOrder[]> | null {
  return read<Snapshot<WorkOrder[]> | null>(LIST_KEY(), null);
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
