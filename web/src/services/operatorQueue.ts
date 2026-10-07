/**
 * Offline action queue of the operator tablet (docs/sprint6.md §3.2): actions go to a local queue (IndexedDB) with
 * an idempotency key (X-Client-Action-Id), show as "pendente de envio" and are sent in order when the network is back.
 * Conflict (stop already justified by someone else) → the server wins: the action is dropped and the tablet shows
 * what was recorded. A replay of an already executed key returns what was recorded (never a second effect).
 */
import { isAxiosError } from "axios";
import { api } from "./api";
import type { DowntimeEvent } from "./types";

export type OpActionKind = "justify" | "create_stop";

export interface OpAction {
  /** Idempotency key (X-Client-Action-Id) — also the queue key. */
  id: string;
  kind: OpActionKind;
  /** `${tenant}:${user}` of who did it (replayed only with that user's session). */
  owner: string;
  created_at: string;
  /** Downtime event (justify). */
  event_id?: string;
  body: Record<string, unknown>;
  /** Human text for the pending list ("Torno — Falta de material"). */
  label: string;
  attempts: number;
  last_error?: string | null;
  /** Not sent before this instant (ms): the 5 s "desfazer" window instead of a confirmation dialog (§3.2). */
  not_before?: number;
}

export interface QueueStore {
  all(): Promise<OpAction[]>;
  put(a: OpAction): Promise<void>;
  remove(id: string): Promise<void>;
}

/** In-memory store (tests, or a browser without IndexedDB). */
export function memoryStore(): QueueStore {
  const m = new Map<string, OpAction>();
  return {
    all: async () => [...m.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)),
    put: async (a) => { m.set(a.id, a); },
    remove: async (id) => { m.delete(id); },
  };
}

const DB_NAME = "fm-operator";
const STORE = "queue";

function idbStore(): QueueStore {
  let dbp: Promise<IDBDatabase> | null = null;
  const open = () => (dbp ??= new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
  const tx = async <T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const db = await open();
    return new Promise<T>((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const r = fn(t.objectStore(STORE));
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  };
  return {
    all: async () => ((await tx<OpAction[]>("readonly", (s) => s.getAll() as IDBRequest<OpAction[]>)) ?? [])
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)),
    put: async (a) => { await tx("readwrite", (s) => s.put(a)); },
    remove: async (id) => { await tx("readwrite", (s) => s.delete(id)); },
  };
}

let store: QueueStore | null = null;
export function queueStore(): QueueStore {
  if (!store) store = typeof indexedDB !== "undefined" ? idbStore() : memoryStore();
  return store;
}
export function setQueueStore(s: QueueStore | null) {
  store = s;
}

export function newActionId(): string {
  const r = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `op-${r}`;
}

type Listener = () => void;
const listeners = new Set<Listener>();
export function onQueueChange(l: Listener): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
function emit() {
  for (const l of listeners) l();
}

export async function enqueue(a: Omit<OpAction, "attempts" | "created_at" | "id"> & { id?: string; created_at?: string }): Promise<OpAction> {
  const full: OpAction = { attempts: 0, created_at: new Date().toISOString(), id: newActionId(), ...a } as OpAction;
  await queueStore().put(full);
  emit();
  return full;
}

export async function pending(owner: string): Promise<OpAction[]> {
  return (await queueStore().all()).filter((a) => a.owner === owner);
}

export async function cancel(id: string): Promise<void> {
  await queueStore().remove(id);
  emit();
}

export type SendResult =
  | { kind: "sent"; event: DowntimeEvent; replay: boolean }
  | { kind: "conflict"; current: DowntimeEvent | null; message: string }
  | { kind: "rejected"; message: string }
  | { kind: "offline" };

/** Sends one action (with its idempotency key). Network failure → "offline" (kept in the queue). */
export async function send(a: OpAction): Promise<SendResult> {
  const headers = { "X-Client-Action-Id": a.id };
  try {
    const res = a.kind === "justify"
      ? await api.patch<DowntimeEvent>(`/api/downtime/${a.event_id}`, a.body, { headers })
      : await api.post<DowntimeEvent>("/api/downtime/", a.body, { headers });
    return { kind: "sent", event: res.data, replay: String(res.headers?.["idempotent-replay"] ?? "") === "true" };
  } catch (err) {
    if (!isAxiosError(err) || !err.response) return { kind: "offline" };
    const status = err.response.status;
    const detail = (err.response.data as { detail?: unknown } | undefined)?.detail;
    if (status === 409 && detail && typeof detail === "object" && "current" in detail) {
      const d = detail as { message?: string; current?: DowntimeEvent };
      return { kind: "conflict", current: d.current ?? null, message: d.message ?? "Parada já justificada por outro usuário." };
    }
    if (status >= 500 || status === 408 || status === 429) return { kind: "offline" };
    const message = typeof detail === "string" ? detail
      : detail && typeof detail === "object" && "message" in detail ? String((detail as { message: unknown }).message)
      : `Erro ${status}`;
    return { kind: "rejected", message };
  }
}

export interface FlushReport {
  /** An action still inside its undo window stopped the flush (order is kept); retry at this instant. */
  retryAt?: number;
  sent: OpAction[];
  conflicts: { action: OpAction; current: DowntimeEvent | null; message: string }[];
  rejected: { action: OpAction; message: string }[];
  remaining: number;
}

let flushing: Promise<FlushReport> | null = null;

/**
 * Sends the owner's queued actions in order. Stops at the first network failure (order is kept: a later action
 * never overtakes an earlier one). Sent, conflicting and rejected actions leave the queue.
 */
export function flush(owner: string, sender: (a: OpAction) => Promise<SendResult> = send, now: () => number = Date.now): Promise<FlushReport> {
  if (flushing) return flushing;
  flushing = (async () => {
    const rep: FlushReport = { sent: [], conflicts: [], rejected: [], remaining: 0 };
    const items = await pending(owner);
    for (let i = 0; i < items.length; i += 1) {
      const a = items[i];
      if (a.not_before && a.not_before > now()) {
        rep.retryAt = a.not_before;
        rep.remaining = items.length - i;
        break;
      }
      const r = await sender(a);
      if (r.kind === "offline") {
        await queueStore().put({ ...a, attempts: a.attempts + 1, last_error: "sem conexão" });
        rep.remaining = items.length - i;
        break;
      }
      await queueStore().remove(a.id);
      if (r.kind === "sent") rep.sent.push(a);
      else if (r.kind === "conflict") rep.conflicts.push({ action: a, current: r.current, message: r.message });
      else rep.rejected.push({ action: a, message: r.message });
    }
    emit();
    return rep;
  })().finally(() => { flushing = null; });
  return flushing;
}

/** Most used reasons of a machine (the 8 big buttons, §3.2): justified stops, most frequent first. */
export function topReasons(events: Pick<DowntimeEvent, "reason_code" | "effective_reason_code">[], n = 8, exclude: (c: string) => boolean = () => false): string[] {
  const count = new Map<string, number>();
  for (const e of events) {
    const c = e.reason_code ?? e.effective_reason_code;
    if (!c || c === "UNK-01" || exclude(c)) continue;
    count.set(c, (count.get(c) ?? 0) + 1);
  }
  return [...count.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, n).map(([c]) => c);
}
