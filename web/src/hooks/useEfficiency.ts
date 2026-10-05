/**
 * React Query hooks of the efficiency screens (Fase 2).
 * Key families:
 *   ["efficiency", …]  server-computed KPIs (overview, OEE, Pareto, reliability, shift report) —
 *                      refreshed (throttled) by the realtime hook on every downtime event
 *   ["downtime", query] stop lists — patched in place by downtime socket messages
 *   ["reason-codes"], ["shifts", plantId], ["calendar", plantId], ["products"],
 *   ["machine-products", machineId], ["runs", machineId] — settings / master data
 * (no key starts with "machines": the realtime hook patches those as Machine[]).
 */

import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  calendarApi, downtimeApi, efficiencyApi, productsApi, reasonCodesApi, runsApi, shiftsApi,
} from "@/services/api";
import { isUnjustified } from "@/services/realtime";
import type {
  CalendarExceptionBody, DowntimeCreateBody, DowntimeEvent, DowntimePatchBody, DowntimeQuery, MachineProductBody,
  OeeQuery, OeeScope, ReasonCodeCreateBody, ReasonCodeUpdateBody, ShiftBody, TreePlant,
} from "@/services/types";
import { usePlantTree } from "./useAssets";

export const effKeys = {
  overview: (plantId?: string) => ["efficiency", "overview", plantId ?? null] as const,
  oee: (q: OeeQuery) => ["efficiency", "oee", q] as const,
  pareto: (q: object) => ["efficiency", "pareto", q] as const,
  reliability: (q: object) => ["efficiency", "reliability", q] as const,
  shiftReport: (q: object) => ["efficiency", "shift-report", q] as const,
  downtime: (q: DowntimeQuery) => ["downtime", q] as const,
  reasonCodes: (all: boolean) => ["reason-codes", all] as const,
  shifts: (plantId?: string) => ["shifts", plantId ?? null] as const,
  calendar: (plantId?: string) => ["calendar", plantId ?? null] as const,
  products: ["products"] as const,
  machineProducts: (machineId: string) => ["machine-products", machineId] as const,
  runs: (machineId: string) => ["runs", machineId] as const,
};

// ── plant context ─────────────────────────────────────────────────

/** The plant shown by the efficiency screens (first plant of the tenant unless `plantId`). */
export function usePlant(plantId?: string | null) {
  const tree = usePlantTree();
  const plant = useMemo(() => {
    const plants: TreePlant[] = tree.data ?? [];
    return plants.find((p) => p.id === plantId) ?? plants[0] ?? null;
  }, [tree.data, plantId]);
  return { tree, plant, tz: plant?.timezone ?? "America/Sao_Paulo" };
}

// ── KPIs ──────────────────────────────────────────────────────────

export function useOverview(plantId?: string, refetchMs = 60_000) {
  return useQuery({
    queryKey: effKeys.overview(plantId),
    queryFn: () => efficiencyApi.overview(plantId),
    refetchInterval: refetchMs,
  });
}

export function useOee(q: OeeQuery | null, refetchMs: number | false = 60_000) {
  return useQuery({
    queryKey: effKeys.oee(q ?? { scope: "machine", id: "" }),
    queryFn: () => efficiencyApi.oee(q as OeeQuery).then((r) => r.data),
    enabled: !!q && !!q.id,
    refetchInterval: refetchMs,
    placeholderData: (prev) => prev,
  });
}

export function usePareto(q: { scope: OeeScope; id: string; start?: string; stop?: string } | null) {
  return useQuery({
    queryKey: effKeys.pareto(q ?? {}),
    queryFn: () => efficiencyApi.pareto(q as { scope: OeeScope; id: string }).then((r) => r.data),
    enabled: !!q && !!q.id,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useReliability(q: { scope: "machine" | "line"; id: string; start?: string; stop?: string } | null) {
  return useQuery({
    queryKey: effKeys.reliability(q ?? {}),
    queryFn: () => efficiencyApi.reliability(q as { scope: "machine" | "line"; id: string }).then((r) => r.data),
    enabled: !!q && !!q.id,
    placeholderData: (prev) => prev,
  });
}

export function useShiftReport(q: { line_id: string; productive_day: string; shift_id?: string } | null) {
  return useQuery({
    queryKey: effKeys.shiftReport(q ?? {}),
    queryFn: () => efficiencyApi.shiftReport(q as { line_id: string; productive_day: string }).then((r) => r.data),
    enabled: !!q && !!q.line_id && !!q.productive_day,
  });
}

// ── downtime ──────────────────────────────────────────────────────

export function useDowntime(q: DowntimeQuery | null, refetchMs: number | false = 60_000) {
  return useQuery({
    queryKey: effKeys.downtime(q ?? {}),
    queryFn: () => downtimeApi.list(q as DowntimeQuery).then((r) => r.data),
    enabled: !!q,
    refetchInterval: refetchMs,
  });
}

/** Justify a stop. Optimistic: it leaves "unjustified" lists at once (rolled back on error). */
export function useJustifyDowntime() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DowntimePatchBody }) => downtimeApi.justify(id, body).then((r) => r.data),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: ["downtime"] });
      const snapshot = qc.getQueriesData<DowntimeEvent[]>({ queryKey: ["downtime"] });
      for (const [key, list] of snapshot) {
        if (!list) continue;
        const q = (key[1] ?? {}) as DowntimeQuery;
        const next = list
          .map((e) => (e.id === id ? { ...e, ...body } : e))
          .filter((e) => !(q.unjustified === true && e.id === id && !isUnjustified(e)));
        qc.setQueryData(key, next);
      }
      return { snapshot };
    },
    onError: (_e, _v, ctx) => {
      for (const [key, list] of ctx?.snapshot ?? []) qc.setQueryData(key, list);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["downtime"] });
      void qc.invalidateQueries({ queryKey: ["efficiency"] });
    },
  });
}

export function useCreateDowntime() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DowntimeCreateBody) => downtimeApi.create(body).then((r) => r.data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["downtime"] });
      void qc.invalidateQueries({ queryKey: ["efficiency"] });
    },
  });
}

// ── reason codes ──────────────────────────────────────────────────

export function useReasonCodes(includeInactive = false) {
  return useQuery({
    queryKey: effKeys.reasonCodes(includeInactive),
    queryFn: () => reasonCodesApi.list(includeInactive).then((r) => r.data),
    staleTime: 10 * 60_000,
  });
}

export function useCreateReasonCode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ReasonCodeCreateBody) => reasonCodesApi.create(body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reason-codes"] }),
  });
}

export function useUpdateReasonCode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ReasonCodeUpdateBody }) => reasonCodesApi.update(id, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reason-codes"] }),
  });
}

// ── shifts & calendar ─────────────────────────────────────────────

export function useShifts(plantId?: string) {
  return useQuery({
    queryKey: effKeys.shifts(plantId),
    queryFn: () => shiftsApi.list(plantId).then((r) => r.data),
    enabled: !!plantId,
    staleTime: 5 * 60_000,
  });
}

function invalidateCalendar(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ["shifts"] });
  void qc.invalidateQueries({ queryKey: ["calendar"] });
  void qc.invalidateQueries({ queryKey: ["efficiency"] });
}

export function useSaveShift() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: ShiftBody }) =>
      (id ? shiftsApi.update(id, body) : shiftsApi.create(body)).then((r) => r.data),
    onSuccess: () => invalidateCalendar(qc),
  });
}

export function useDeleteShift() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => shiftsApi.remove(id), onSuccess: () => invalidateCalendar(qc) });
}

export function useCalendarExceptions(plantId?: string) {
  return useQuery({
    queryKey: effKeys.calendar(plantId),
    queryFn: () => calendarApi.list(plantId).then((r) => r.data),
    enabled: !!plantId,
  });
}

export function useCreateCalendarException() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarExceptionBody) => calendarApi.create(body).then((r) => r.data),
    onSuccess: () => invalidateCalendar(qc),
  });
}

export function useDeleteCalendarException() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => calendarApi.remove(id), onSuccess: () => invalidateCalendar(qc) });
}

// ── products, ideal cycle times, production runs ──────────────────

export function useProducts() {
  return useQuery({ queryKey: effKeys.products, queryFn: () => productsApi.list().then((r) => r.data), staleTime: 5 * 60_000 });
}

export function useSaveProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: { code: string; name: string } }) =>
      (id ? productsApi.update(id, body) : productsApi.create(body)).then((r) => r.data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: effKeys.products });
      void qc.invalidateQueries({ queryKey: ["machine-products"] });
    },
  });
}

export function useDeleteProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => productsApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: effKeys.products });
      void qc.invalidateQueries({ queryKey: ["machine-products"] });
    },
  });
}

export function useMachineProducts(machineId: string | null) {
  return useQuery({
    queryKey: effKeys.machineProducts(machineId ?? ""),
    queryFn: () => productsApi.forMachine(machineId as string).then((r) => r.data),
    enabled: !!machineId,
  });
}

export function useSaveMachineProduct(machineId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mpId, body }: { mpId?: string; body: MachineProductBody }) =>
      (mpId
        ? productsApi.updateOnMachine(machineId, mpId, {
          ideal_cycle_time_s: body.ideal_cycle_time_s, parts_per_cycle: body.parts_per_cycle, is_default: body.is_default,
        })
        : productsApi.addToMachine(machineId, body)
      ).then((r) => r.data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: effKeys.machineProducts(machineId) });
      void qc.invalidateQueries({ queryKey: ["efficiency"] });
    },
  });
}

export function useRemoveMachineProduct(machineId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mpId: string) => productsApi.removeFromMachine(machineId, mpId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: effKeys.machineProducts(machineId) }),
  });
}

/** Open production run of a machine (null when none: the default product is assumed). */
export function useOpenRun(machineId: string | null) {
  return useQuery({
    queryKey: effKeys.runs(machineId ?? ""),
    queryFn: () => runsApi.list({ machine_id: machineId as string, open: true }).then((r) => r.data[0] ?? null),
    enabled: !!machineId,
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { machine_id: string; product_id: string }) => runsApi.start(body).then((r) => r.data),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: effKeys.runs(v.machine_id) });
      void qc.invalidateQueries({ queryKey: ["efficiency"] });
    },
  });
}

export function useStopRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId }: { runId: string; machineId: string }) => runsApi.stop(runId).then((r) => r.data),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: effKeys.runs(v.machineId) });
      void qc.invalidateQueries({ queryKey: ["efficiency"] });
    },
  });
}
