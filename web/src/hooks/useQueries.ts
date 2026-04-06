/**
 * Centralized React Query hooks — one place to adjust keys, stale times and refetch intervals.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { linesApi, machinesApi, alertsApi, sensorsApi, camerasApi } from "@/services/api";

// ── Lines ─────────────────────────────────────────────────────────
export function useLines() {
  return useQuery({
    queryKey: ["lines"],
    queryFn:  () => linesApi.list().then((r) => r.data),
  });
}

// ── Machines ──────────────────────────────────────────────────────
export function useMachines(lineId?: string) {
  return useQuery({
    queryKey: ["machines", lineId],
    queryFn:  () => machinesApi.list(lineId).then((r) => r.data),
    refetchInterval: 30_000,   // refresh status every 30s
  });
}

// ── Alert events ──────────────────────────────────────────────────
export function useAlertEvents(activeOnly = false) {
  return useQuery({
    queryKey: ["alerts", "events", activeOnly],
    queryFn:  () => alertsApi.listEvents(activeOnly).then((r) => r.data),
    refetchInterval: 15_000,
  });
}

export function useAlertRules() {
  return useQuery({
    queryKey: ["alerts", "rules"],
    queryFn:  () => alertsApi.listRules().then((r) => r.data),
  });
}

export function useResolveAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => alertsApi.resolveEvent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

// ── Cameras ───────────────────────────────────────────────────────
export function useCameras(machineId?: string) {
  return useQuery({
    queryKey: ["cameras", machineId],
    queryFn:  () => camerasApi.list(machineId).then((r) => r.data),
  });
}

export function useCreateCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: unknown) => camerasApi.create(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });
}

export function useUpdateCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: unknown }) => camerasApi.update(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });
}

export function useDeleteCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => camerasApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });
}

// ── Sensor history ────────────────────────────────────────────────
export function useSensorHistory(
  tagId: string,
  params?: { start?: string; stop?: string; window?: string }
) {
  return useQuery({
    queryKey: ["sensors", "history", tagId, params],
    queryFn:  () => sensorsApi.history(tagId, params).then((r) => r.data),
    enabled:  !!tagId,
    refetchInterval: 60_000,
  });
}
