/**
 * Data of the live plant map (sprint6 §1.3).
 *   ["plant-map", plantId]       live marker state (kpis=false) — patched by machine_status messages and refetched
 *                                ≤ 300 ms after any status / stop / failure event (useWebSocket); polled every 15 s
 *                                while the socket is down for more than 10 s
 *   ["plant-map-kpis", plantId]  current-shift OEE / cadence of each marker, every minute
 *   ["layout-image", layoutId]   the floor plan (blob, needs the JWT) → object URL
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { layoutApi } from "@/services/sprint6Api";
import type { MapView, PlantLayout } from "@/services/sprint6Types";
import { useRealtimeStore } from "@/store/realtime";
import { liveness, mergeKpis, POLL_INTERVAL_MS, serverOffset, type Liveness } from "@/utils/plantMap";
import { useNow } from "./useNow";

async function fetchMap(plantId: string, kpis: boolean): Promise<MapView> {
  // the versions list says whether a layout is published (no 404 round trip, no console noise)
  const versions = (await layoutApi.list(plantId)).data;
  if (versions.some((v) => v.status === "published")) {
    try {
      return (await layoutApi.published(plantId, kpis)).data;
    } catch (err) {
      // published in between list and fetch was archived/removed → automatic layout
      if (!(isAxiosError(err) && err.response?.status === 404)) throw err;
    }
  }
  // no published layout → the automatic one (§1.5), identified as such
  return (await layoutApi.auto(plantId, kpis)).data;
}

export const mapKeys = {
  live: (plantId: string) => ["plant-map", plantId] as const,
  kpis: (plantId: string) => ["plant-map-kpis", plantId] as const,
  image: (layoutId: string) => ["layout-image", layoutId] as const,
};

export function useLiveness(): { state: Liveness; lastUpdate: number | null } {
  const status = useRealtimeStore((s) => s.status);
  const downSince = useRealtimeStore((s) => s.downSince);
  const lastMessageAt = useRealtimeStore((s) => s.lastMessageAt);
  const now = useNow(1000);
  const [mountedAt] = useState(() => Date.now());
  return { state: liveness(status === "connected", status === "connected" ? null : downSince ?? mountedAt, now), lastUpdate: lastMessageAt };
}

export function usePlantMap(plantId: string | undefined) {
  const live = useLiveness();
  const polling = live.state === "polling" || live.state === "paused";
  const q = useQuery({
    queryKey: mapKeys.live(plantId ?? ""),
    queryFn: () => fetchMap(plantId as string, false),
    enabled: !!plantId,
    refetchInterval: polling ? POLL_INTERVAL_MS : 60_000,
    staleTime: 5_000,
  });
  const k = useQuery({
    queryKey: mapKeys.kpis(plantId ?? ""),
    queryFn: () => fetchMap(plantId as string, true),
    enabled: !!plantId && q.isSuccess,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  const view = useMemo(() => (q.data ? mergeKpis(q.data, k.data) : undefined), [q.data, k.data]);
  const offset = useMemo(() => serverOffset(q.data?.generated_at, q.dataUpdatedAt || Date.now()), [q.data, q.dataUpdatedAt]);
  const lastUpdate = Math.max(live.lastUpdate ?? 0, q.dataUpdatedAt || 0) || null;
  return { query: q, kpisQuery: k, view, liveness: live.state, lastUpdate, serverOffset: offset };
}

/** Cache key of a layout's image: changes only when the image does (not when markers are saved). */
export function imageVersion(l: Pick<PlantLayout, "image_bytes" | "image_width_px" | "image_height_px" | "image_content_type"> | null | undefined): string | null {
  return l ? `${l.image_content_type}:${l.image_bytes}:${l.image_width_px}x${l.image_height_px}` : null;
}

/** Object URL of a layout's floor plan (revoked on change / unmount). */
export function useLayoutImage(layoutId: string | null | undefined, hasImage: boolean, version?: string | null) {
  const q = useQuery({
    queryKey: [...mapKeys.image(layoutId ?? ""), version ?? null],
    queryFn: () => layoutApi.image(layoutId as string).then((r) => r.data),
    enabled: !!layoutId && hasImage,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) {
      setUrl(null);
      return;
    }
    const u = URL.createObjectURL(q.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [q.data]);
  return { url, query: q };
}
