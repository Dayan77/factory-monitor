/**
 * Device snapshot of what the operator tablet needs to justify stops offline (docs/sprint6.md §3.2, acceptance 5):
 * the plant tree (the tablet's line), the reason codes and the line's unjustified stops.
 *
 * React Query's cache lives in memory only, so after the app is (re)opened with no network the justify screens had
 * nothing to show ("Carregando…" forever). Here every successful fetch of those queries is saved on the device
 * (localStorage, per `${tenant}:${user}` owner) and, when the app starts, seeded back into the query cache with the
 * time it was saved — so React Query still refetches it as soon as the network allows, and the screens can say
 * "lista salva às HH:MM" while offline. Reason codes are also prefetched on /op load.
 */
import { useEffect, useState } from "react";
import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { effKeys } from "@/hooks/useEfficiency";
import { queryKeys } from "@/hooks/useAssets";
import { reasonCodesApi } from "@/services/api";
import { currentOwner } from "@/services/offline";

const PREFIX = "fm-op-cache";
/** Snapshots older than this are not used (a stale stop list is worse than none). */
export const SNAPSHOT_MAX_AGE_MS = 24 * 3600_000;

export interface Snapshot<T = unknown> { data: T; saved_at: number }

export function tabletKeys(lineId: string | null): QueryKey[] {
  const keys: QueryKey[] = [queryKeys.tree, effKeys.reasonCodes(false)];
  if (lineId) keys.push(effKeys.downtime({ line_id: lineId, unjustified: true }));
  return keys;
}

function storageKey(owner: string, key: QueryKey): string {
  return `${PREFIX}:${owner}:${JSON.stringify(key)}`;
}

export function readSnapshot<T>(owner: string, key: QueryKey, now = Date.now()): Snapshot<T> | null {
  try {
    const raw = localStorage.getItem(storageKey(owner, key));
    if (!raw) return null;
    const s = JSON.parse(raw) as Snapshot<T>;
    if (typeof s?.saved_at !== "number" || now - s.saved_at > SNAPSHOT_MAX_AGE_MS) return null;
    return s;
  } catch {
    return null;
  }
}

export function writeSnapshot(owner: string, key: QueryKey, data: unknown, savedAt = Date.now()) {
  try {
    localStorage.setItem(storageKey(owner, key), JSON.stringify({ data, saved_at: savedAt }));
  } catch { /* quota / blocked: the tablet still works online */ }
}

/** Drops the snapshots of every other user of this tablet (called on mount; a new operator never sees A's list). */
export function purgeOtherOwners(owner: string) {
  try {
    const drop: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (k && k.startsWith(`${PREFIX}:`) && !k.startsWith(`${PREFIX}:${owner}:`)) drop.push(k);
    }
    for (const k of drop) localStorage.removeItem(k);
  } catch { /* storage blocked */ }
}

/** Seeds the query cache from the device snapshot (only where the cache has no data yet). Synchronous. */
export function seedFromSnapshot(qc: QueryClient, owner: string, keys: QueryKey[]) {
  for (const key of keys) {
    if (qc.getQueryData(key) !== undefined) continue;
    const s = readSnapshot(owner, key);
    if (s) qc.setQueryData(key, s.data, { updatedAt: s.saved_at });
  }
}

/** Seeds the query cache from the device snapshot on the first render, before any query of the tablet mounts. */
export function useSeedTabletSnapshot(storedLineId: string | null) {
  const qc = useQueryClient();
  useState(() => {
    const owner = currentOwner();
    purgeOtherOwners(owner);
    seedFromSnapshot(qc, owner, tabletKeys(storedLineId));
    return null;
  });
}

/**
 * Keeps the tablet's offline snapshot for the resolved line: seeds its stop list if needed, prefetches the reason
 * codes (so the justify screen never waits on the network) and saves every successful fetch of the tablet keys.
 */
export function useTabletSnapshot(lineId: string | null) {
  const qc = useQueryClient();
  const owner = currentOwner();
  const [seededLine, setSeededLine] = useState(lineId);
  if (seededLine !== lineId) {
    seedFromSnapshot(qc, owner, tabletKeys(lineId));
    setSeededLine(lineId);
  }

  useEffect(() => {
    void qc.prefetchQuery({
      queryKey: effKeys.reasonCodes(false),
      queryFn: () => reasonCodesApi.list(false).then((r) => r.data),
      staleTime: 10 * 60_000,
    });
  }, [qc]);

  useEffect(() => {
    const wanted = new Set(tabletKeys(lineId).map((k) => JSON.stringify(k)));
    const save = (key: QueryKey, data: unknown, at: number) => {
      if (data !== undefined && wanted.has(JSON.stringify(key))) writeSnapshot(owner, key, data, at);
    };
    for (const q of qc.getQueryCache().getAll()) {
      if (q.state.status === "success" && q.state.dataUpdatedAt > 0) save(q.queryKey, q.state.data, q.state.dataUpdatedAt);
    }
    return qc.getQueryCache().subscribe((e) => {
      // Only real fetches: setQueryData (our seed, optimistic updates) is "manual".
      if (e.type !== "updated" || e.action.type !== "success" || e.action.manual) return;
      save(e.query.queryKey, e.query.state.data, e.query.state.dataUpdatedAt);
    });
  }, [qc, owner, lineId]);
}
