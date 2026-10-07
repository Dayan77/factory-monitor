/** State of the operator tablet: the tablet's line, the offline queue, the connection and the idle session. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePlantTree } from "@/hooks/useAssets";
import { useAuthStore } from "@/store/auth";
import { currentOwner } from "@/services/offline";
import { flush, onQueueChange, pending, pendingByOwner, type FlushReport, type OpAction, type OwnerPending } from "@/services/operatorQueue";
import type { TreeLine, TreePlant } from "@/services/types";
import { rolesOf } from "@/utils/maintenance";

export const LINE_KEY = "fm-op-line";
export const IDLE_MS = 15 * 60_000;
export const OFFLINE_BANNER_MS = 30_000;

export function readLine(): string | null {
  try { return localStorage.getItem(LINE_KEY); } catch { return null; }
}

/** The lines this user may run the tablet on: a pure operator only his own (User.line_ids); others any line. */
export function allowedLines(tree: TreePlant[], user: { role?: string | null; roles?: string[] | null; line_ids?: string[] } | null): { plant: TreePlant; line: TreeLine }[] {
  const all = tree.flatMap((plant) => plant.lines.map((line) => ({ plant, line })));
  const roles = rolesOf(user);
  const pureOperator = roles.length > 0 && roles.every((r) => r === "operator");
  if (pureOperator && user?.line_ids?.length) {
    const own = new Set(user.line_ids);
    return all.filter((x) => own.has(x.line.id));
  }
  return all;
}

export function useTabletLine() {
  const tree = usePlantTree();
  const user = useAuthStore((s) => s.user);
  const [lineId, setLineId] = useState<string | null>(() => readLine());
  const options = useMemo(() => allowedLines(tree.data ?? [], user), [tree.data, user]);
  const current = options.find((o) => o.line.id === lineId) ?? (options.length === 1 ? options[0] : null);
  const choose = useCallback((id: string | null) => {
    try { if (id) localStorage.setItem(LINE_KEY, id); else localStorage.removeItem(LINE_KEY); } catch { /* storage blocked */ }
    setLineId(id);
  }, []);
  return { tree, options, current, choose };
}

/** navigator.onLine + failures of the API; "sem conexão desde HH:MM" after 30 s. */
export function useConnectivity() {
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  const [since, setSince] = useState<number | null>(() => (typeof navigator !== "undefined" && !navigator.onLine ? Date.now() : null));
  useEffect(() => {
    const up = () => { setOnline(true); setSince(null); };
    const down = () => { setOnline(false); setSince((s) => s ?? Date.now()); };
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, []);
  const markFailure = useCallback(() => setSince((s) => s ?? Date.now()), []);
  const markSuccess = useCallback(() => { if (navigator.onLine) setSince(null); }, []);
  return { online, since, markFailure, markSuccess };
}

/** Offline queue of the logged-in operator: list + flush in order (on reconnect, every 10 s, after each enqueue). */
/** Queued actions of every operator of this tablet (login screen). */
export function usePendingOwners() {
  const [list, setList] = useState<OwnerPending[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () => { void pendingByOwner().then((l) => { if (alive) setList(l); }).catch(() => undefined); };
    load();
    const off = onQueueChange(load);
    return () => { alive = false; off(); };
  }, []);
  return list;
}

export function useOperatorQueue(onReport?: (r: FlushReport) => void) {
  const qc = useQueryClient();
  const owner = currentOwner();
  const [items, setItems] = useState<OpAction[]>([]);
  const report = useRef(onReport);
  report.current = onReport;
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const refresh = useCallback(async () => setItems(await pending(owner)), [owner]);

  const run = useCallback(async () => {
    clearTimeout(timer.current);
    const r = await flush(owner);
    if (r.sent.length || r.conflicts.length || r.rejected.length) {
      void qc.invalidateQueries({ queryKey: ["downtime"] });
      void qc.invalidateQueries({ queryKey: ["plant-map"] });
      void qc.invalidateQueries({ queryKey: ["efficiency"] });
    }
    report.current?.(r);
    if (r.retryAt) timer.current = setTimeout(() => void run(), Math.max(200, r.retryAt - Date.now() + 50));
    await refresh();
    return r;
  }, [owner, qc, refresh]);

  useEffect(() => {
    void refresh();
    const off = onQueueChange(() => { void refresh(); });
    const onOnline = () => void run();
    window.addEventListener("online", onOnline);
    const iv = setInterval(() => { if (navigator.onLine) void run(); }, 10_000);
    return () => { off(); window.removeEventListener("online", onOnline); clearInterval(iv); clearTimeout(timer.current); };
  }, [refresh, run]);

  return { items, owner, flush: run };
}

/**
 * Ends the operator session after 15 min without use (§3.2) — except while `hold` is true (the operator still has
 * actions waiting to be sent: they are only replayed with his session, so ending it would strand them until he
 * logs in again on this tablet). The timer re-arms and the session ends at the first idle period with nothing
 * pending.
 */
export function useIdleLogout(onIdle: () => void, ms = IDLE_MS, hold = false) {
  const cb = useRef(onIdle);
  cb.current = onIdle;
  const held = useRef(hold);
  held.current = hold;
  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const fire = () => { if (held.current) t = setTimeout(fire, ms); else cb.current(); };
    t = setTimeout(fire, ms);
    const reset = () => { clearTimeout(t); t = setTimeout(fire, ms); };
    const evs = ["pointerdown", "keydown", "touchstart"] as const;
    for (const e of evs) window.addEventListener(e, reset, { passive: true });
    return () => { clearTimeout(t); for (const e of evs) window.removeEventListener(e, reset); };
  }, [ms]);
}
