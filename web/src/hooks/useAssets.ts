/**
 * React Query hooks for the asset hierarchy, usage and production (Fase 1).
 * Keys never start with "machines": the realtime hook patches ["machines", …] as Machine[].
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { componentsApi, plantsApi, positionsApi, usageApi } from "@/services/api";
import type { ComponentCreateBody, RemovalReason, UsageMeasure } from "@/services/types";

/** Relative time windows (resolved to start/stop when the request is made). */
export type RangeKey = "8h" | "24h" | "7d";
export const RANGE_HOURS: Record<RangeKey, number> = { "8h": 8, "24h": 24, "7d": 168 };

export function resolveRange(range: RangeKey, now: number = Date.now()): { start: string; stop: string } {
  return {
    start: new Date(now - RANGE_HOURS[range] * 3600_000).toISOString(),
    stop: new Date(now).toISOString(),
  };
}

export type UsageScope = { position_id: string } | { machine_id: string };

export const queryKeys = {
  tree: ["plants", "tree"] as const,
  machineUsageAll: ["usage", "machine"] as const,
  machineUsage: (machineId: string) => ["usage", "machine", machineId] as const,
  usageHistory: (scope: UsageScope, measure: UsageMeasure, range: RangeKey) =>
    ["usage", "history", scope, measure, range] as const,
  position: (id: string) => ["positions", id] as const,
  positionComponents: (positionId: string) => ["components", "position", positionId] as const,
  stock: ["components", "stock"] as const,
  lineProduction: (lineId: string, range: RangeKey) => ["lines", lineId, "production", range] as const,
};

export function usePlantTree() {
  return useQuery({
    queryKey: queryKeys.tree,
    queryFn: () => plantsApi.tree().then((r) => r.data),
    refetchInterval: 30_000,
  });
}

export function useMachineUsage(machineId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.machineUsage(machineId ?? ""),
    queryFn: () => usageApi.machine(machineId as string).then((r) => r.data),
    enabled: !!machineId,
    refetchInterval: 30_000,
  });
}

export function useUsageHistory(scope: UsageScope | null, measure: UsageMeasure, range: RangeKey = "24h") {
  return useQuery({
    queryKey: queryKeys.usageHistory(scope ?? { machine_id: "" }, measure, range),
    queryFn: () => usageApi.history(scope as UsageScope, measure, resolveRange(range)).then((r) => r.data),
    enabled: !!scope && Object.values(scope)[0] !== "",
    refetchInterval: 60_000,
  });
}

export function useLineProduction(lineId: string | undefined, range: RangeKey = "24h") {
  return useQuery({
    queryKey: queryKeys.lineProduction(lineId ?? "", range),
    queryFn: () => usageApi.lineProduction(lineId as string, resolveRange(range)).then((r) => r.data),
    enabled: !!lineId,
    refetchInterval: 60_000,
  });
}

export function usePosition(positionId: string | null) {
  return useQuery({
    queryKey: queryKeys.position(positionId ?? ""),
    queryFn: () => positionsApi.get(positionId as string).then((r) => r.data),
    enabled: !!positionId,
  });
}

/** Installed + past components of a position (history), newest first. */
export function usePositionComponents(positionId: string | null) {
  return useQuery({
    queryKey: queryKeys.positionComponents(positionId ?? ""),
    queryFn: () => componentsApi.list({ position_id: positionId as string }).then((r) => r.data),
    enabled: !!positionId,
  });
}

export function useStockComponents(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.stock,
    queryFn: () => componentsApi.list({ in_stock: true }).then((r) => r.data),
    enabled,
  });
}

/** After any install/remove/create: refresh everything derived from the asset state. */
function useInvalidateAssets() {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ["positions"] }),
      qc.invalidateQueries({ queryKey: ["components"] }),
      qc.invalidateQueries({ queryKey: ["usage"] }),
      qc.invalidateQueries({ queryKey: queryKeys.tree }),
    ]);
}

export function useInstallComponent(positionId: string) {
  const invalidate = useInvalidateAssets();
  return useMutation({
    mutationFn: (componentId: string) => positionsApi.install(positionId, componentId).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useRemoveComponent() {
  const invalidate = useInvalidateAssets();
  return useMutation({
    mutationFn: ({ componentId, reason }: { componentId: string; reason: RemovalReason }) =>
      componentsApi.remove(componentId, reason).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useCreateComponent() {
  const invalidate = useInvalidateAssets();
  return useMutation({
    mutationFn: (body: ComponentCreateBody) => componentsApi.create(body).then((r) => r.data),
    onSuccess: invalidate,
  });
}
