/**
 * Technician mobile app (/m) data: offline-aware reads and the action queue.
 * Reads go through `withSnapshot` (device cache when the API is unreachable, flagged as such);
 * task checks / labor / transitions go through `runOrQueue` (queued offline, replayed on reconnect).
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { workOrdersApi } from "@/services/maintenanceApi";
import type { LaborBody, TransitionBody, WorkOrder } from "@/services/maintenanceTypes";
import {
  applyPending, enqueue, flushQueue, getFailed, getQueue, isNetworkError, isOnline, loadList, loadWorkOrder,
  saveList, saveWorkOrder, subscribeQueue, withSnapshot, type QueuedAction, type Sourced,
} from "@/services/offline";
import { apiErrorMessage } from "@/utils/errors";
import { ACTIVE_STATES, dayOf, isoDay } from "@/utils/maintenance";
import { mntKeys } from "./useMaintenance";

export const techKeys = {
  mine: (userId: string) => ["maintenance", "mine", userId] as const,
  emergencies: ["maintenance", "emergencies"] as const,
  detail: (id: string) => ["maintenance", "tech-wo", id] as const,
};

/** Queue length / failed list, re-rendering on every change. */
export function useQueueState() {
  const queue = useSyncExternalStore(subscribeQueue, queueSnapshot, queueSnapshot);
  const failed = useSyncExternalStore(subscribeQueue, failedSnapshot, failedSnapshot);
  return { queue: JSON.parse(queue) as QueuedAction[], failed: JSON.parse(failed) as ReturnType<typeof getFailed> };
}

function queueSnapshot() {
  return JSON.stringify(getQueue());
}
function failedSnapshot() {
  return JSON.stringify(getFailed());
}

/** navigator.onLine as state. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(isOnline());
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  return online;
}

/** Work orders that belong on "Minhas OS": active ones + those completed today. */
export function isMine(wo: WorkOrder, today: string): boolean {
  return ACTIVE_STATES.includes(wo.state) || (wo.state === "completed" && dayOf(wo.completed_at) === today);
}

export function useMyWorkOrders(userId: string | undefined) {
  return useQuery<Sourced<WorkOrder[]>>({
    queryKey: techKeys.mine(userId ?? ""),
    queryFn: async () => {
      const res = await withSnapshot(
        () => workOrdersApi.list({ assignee_id: userId }).then((r) => r.data),
        saveList,
        loadList,
      );
      const today = isoDay();
      return { ...res, data: res.data.filter((wo) => isMine(wo, today)) };
    },
    enabled: !!userId,
    refetchInterval: 30_000,
    networkMode: "always",
    retry: false,
  });
}

/** Open emergency calls not yet taken (workflows §3 "Chamado emergencial"). */
export function useEmergencyCalls(enabled: boolean) {
  return useQuery({
    queryKey: techKeys.emergencies,
    queryFn: () => workOrdersApi.list({ type: "corrective", state: "open" }).then((r) => r.data.filter((w) => w.urgency === "emergency")),
    enabled,
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useTechWorkOrder(id: string) {
  return useQuery<Sourced<WorkOrder>>({
    queryKey: techKeys.detail(id),
    queryFn: () => withSnapshot(() => workOrdersApi.get(id).then((r) => r.data), saveWorkOrder, () => loadWorkOrder(id)),
    enabled: !!id,
    networkMode: "always",
    retry: false,
  });
}

/** The work order as the technician sees it: server/snapshot + pending offline actions. */
export function useEffectiveWorkOrder(wo: WorkOrder | undefined): WorkOrder | undefined {
  const { queue } = useQueueState();
  return wo ? applyPending(wo, queue) : undefined;
}

export type TechAction =
  | { kind: "task"; wo: WorkOrder; body: { task_id: string; done: boolean } }
  | { kind: "labor"; wo: WorkOrder; body: LaborBody }
  | { kind: "transition"; wo: WorkOrder; body: TransitionBody };

function execute(a: { kind: QueuedAction["kind"]; wo_id: string; body: unknown; id?: string }) {
  if (a.kind === "task") return workOrdersApi.task(a.wo_id, a.body as { task_id: string; done: boolean }, a.id);
  if (a.kind === "labor") return workOrdersApi.labor(a.wo_id, a.body as LaborBody, a.id);
  return workOrdersApi.transition(a.wo_id, a.body as TransitionBody, a.id);
}

/**
 * Runs an action now when online (errors from the server are thrown — e.g. 422 with the allowed list),
 * or queues it when the device is offline / the request cannot reach the server.
 * Resolves to "sent" | "queued".
 */
export function useTechAction() {
  const qc = useQueryClient();
  return useMutation({
    networkMode: "always",
    mutationFn: async (a: TechAction): Promise<"sent" | "queued"> => {
      const base = { kind: a.kind, wo_id: a.wo.id, wo_number: a.wo.number, body: a.body } as const;
      // keep order: while something is queued for this WO, queue behind it
      if (!isOnline() || getQueue().some((q) => q.wo_id === a.wo.id)) {
        enqueue(base as Parameters<typeof enqueue>[0]);
        return "queued";
      }
      try {
        const res = await execute(base);
        if (res?.data && typeof res.data === "object" && "id" in res.data) {
          saveWorkOrder(res.data as WorkOrder);
          qc.setQueryData<Sourced<WorkOrder>>(techKeys.detail(a.wo.id), { data: res.data as WorkOrder, source: "network", saved_at: new Date().toISOString() });
        }
        return "sent";
      } catch (err) {
        if (isNetworkError(err)) {
          enqueue(base as Parameters<typeof enqueue>[0]);
          return "queued";
        }
        throw err;
      }
    },
    onSuccess: (r) => {
      if (r === "sent") void qc.invalidateQueries({ queryKey: mntKeys.all });
    },
  });
}

/**
 * Replays the queue when the connection returns (online event), on mount and every 20 s while
 * something is pending. Exposes `syncing` and the last result for the status bar.
 */
export function useOfflineSync() {
  const qc = useQueryClient();
  const { queue } = useQueueState();
  const online = useOnline();
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<{ sent: number; failed: number; at: number } | null>(null);
  const pending = queue.length;

  const sync = useCallback(async () => {
    if (!isOnline() || getQueue().length === 0) return;
    setSyncing(true);
    try {
      const r = await flushQueue(
        (a) => execute(a),
        (err) => apiErrorMessage(err, "O servidor recusou a ação."),
      );
      if (r.sent || r.failed) setLastSync({ sent: r.sent, failed: r.failed, at: Date.now() });
      await qc.invalidateQueries({ queryKey: mntKeys.all });
    } finally {
      setSyncing(false);
    }
  }, [qc]);

  useEffect(() => {
    if (online) void sync();
  }, [online, sync]);

  useEffect(() => {
    if (!pending || !online) return;
    const t = setInterval(() => void sync(), 20_000);
    return () => clearInterval(t);
  }, [pending, online, sync]);

  return { online, pending, syncing, lastSync, sync };
}
