/**
 * React Query hooks of the maintenance screens (Sprint 4).
 * Key families (none starts with "machines": the realtime hook patches those as Machine[]):
 *   ["maintenance", "plans-status", q] · ["maintenance", "plans"] · ["maintenance", "work-orders", q]
 *   ["maintenance", "work-order", id] · ["maintenance", "failures", q] · ["maintenance", "kpis", q]
 *   ["maintenance", "reliability", q] · ["failure-codes"] · ["notifications"] · ["users", "technicians"]
 * Any work-order mutation invalidates the whole ["maintenance"] family (statuses, KPIs and lists move together).
 */
import { useMemo } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { positionsApi } from "@/services/api";
import {
  failuresApi, maintenanceKpiApi, notificationsApi, plansApi, usersApi, workOrdersApi,
} from "@/services/maintenanceApi";
import type {
  FailurePatchBody, FailureQuery, LaborBody, MaintenancePlanBody, PlanStatusQuery, ReliabilityGroupBy, ReplacementBody,
  TransitionBody, WorkOrder, WorkOrderCreateBody, WorkOrderQuery,
} from "@/services/maintenanceTypes";
import type { Position, TreePlant } from "@/services/types";
import { usePlantTree } from "./useAssets";
import { useAuthStore } from "@/store/auth";
import { hasRole, type RoleCarrier } from "@/utils/maintenance";

export const mntKeys = {
  all: ["maintenance"] as const,
  planStatus: (q: PlanStatusQuery) => ["maintenance", "plans-status", q] as const,
  plans: ["maintenance", "plans"] as const,
  workOrders: (q: WorkOrderQuery) => ["maintenance", "work-orders", q] as const,
  workOrder: (id: string) => ["maintenance", "work-order", id] as const,
  failures: (q: FailureQuery) => ["maintenance", "failures", q] as const,
  failure: (id: string) => ["maintenance", "failure", id] as const,
  kpis: (q: object) => ["maintenance", "kpis", q] as const,
  reliability: (q: object) => ["maintenance", "reliability", q] as const,
  failureCodes: ["failure-codes"] as const,
  technicians: ["users", "technicians"] as const,
  notifications: ["notifications"] as const,
  notificationPrefs: ["notifications", "preferences"] as const,
  positionsOf: (machineId: string) => ["positions", "machine", machineId] as const,
};

// ── reads ─────────────────────────────────────────────────────────

export function usePlanStatus(q: PlanStatusQuery = {}, enabled = true) {
  return useQuery({
    queryKey: mntKeys.planStatus(q),
    queryFn: () => plansApi.status(q).then((r) => r.data),
    refetchInterval: 60_000,
    enabled,
  });
}

export function usePlans(enabled = true) {
  return useQuery({ queryKey: mntKeys.plans, queryFn: () => plansApi.list().then((r) => r.data), enabled });
}

export function useWorkOrders(q: WorkOrderQuery = {}, opts: { enabled?: boolean; refetchMs?: number | false } = {}) {
  return useQuery({
    queryKey: mntKeys.workOrders(q),
    queryFn: () => workOrdersApi.list(q).then((r) => r.data),
    refetchInterval: opts.refetchMs ?? 30_000,
    enabled: opts.enabled ?? true,
    placeholderData: (prev) => prev,
  });
}

export function useWorkOrder(id: string | null) {
  return useQuery({
    queryKey: mntKeys.workOrder(id ?? ""),
    queryFn: () => workOrdersApi.get(id as string).then((r) => r.data),
    enabled: !!id,
  });
}

export function useFailures(q: FailureQuery = {}, enabled = true) {
  return useQuery({
    queryKey: mntKeys.failures(q),
    queryFn: () => failuresApi.list(q).then((r) => r.data),
    refetchInterval: 60_000,
    enabled,
  });
}

export function useFailure(id: string | null) {
  return useQuery({
    queryKey: mntKeys.failure(id ?? ""),
    queryFn: () => failuresApi.get(id as string).then((r) => r.data),
    enabled: !!id,
  });
}

export function useFailureCodes() {
  return useQuery({ queryKey: mntKeys.failureCodes, queryFn: failuresApi.codes, staleTime: 3_600_000 });
}

export function useMaintenanceKpis(q: { plant_id?: string; start: string; stop: string } | null) {
  return useQuery({
    queryKey: mntKeys.kpis(q ?? {}),
    queryFn: () => maintenanceKpiApi.kpis(q as { start: string; stop: string }).then((r) => r.data),
    enabled: !!q,
    placeholderData: (prev) => prev,
  });
}

export function useReliability(q: { group_by: ReliabilityGroupBy; start: string; stop: string }) {
  return useQuery({
    queryKey: mntKeys.reliability(q),
    queryFn: () => maintenanceKpiApi.reliability(q),
    placeholderData: (prev) => prev,
  });
}

/** Who may list the tenant's users (server: admin, manager, maintenance_planner, production_supervisor). */
export function canListUsers(u: RoleCarrier | null | undefined): boolean {
  return hasRole(u, "admin", "superadmin", "manager", "maintenance_planner", "production_supervisor");
}

export function useTechnicians(enabled = true) {
  const user = useAuthStore((s) => s.user);
  return useQuery({
    queryKey: mntKeys.technicians,
    queryFn: () => usersApi.technicians().then((r) => r.data),
    staleTime: 300_000,
    retry: false,
    enabled: enabled && canListUsers(user),
  });
}

// ── name resolution (tree + positions) ────────────────────────────

export interface MachineRef {
  id: string;
  name: string;
  line_id: string;
  line_name: string;
  plant_id: string;
}

export function machineIndex(tree: TreePlant[] | undefined): Map<string, MachineRef> {
  const out = new Map<string, MachineRef>();
  for (const p of tree ?? []) for (const l of p.lines) for (const m of l.machines) {
    out.set(m.id, { id: m.id, name: m.name, line_id: l.id, line_name: l.name, plant_id: p.id });
  }
  return out;
}

export function useMachineIndex() {
  const tree = usePlantTree();
  const index = useMemo(() => machineIndex(tree.data), [tree.data]);
  return { tree, index };
}

/** Positions of the given machines (id → Position). */
export function usePositionsIndex(machineIds: (string | null | undefined)[]) {
  const ids = useMemo(() => [...new Set(machineIds.filter((x): x is string => !!x))].sort(), [machineIds]);
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: mntKeys.positionsOf(id),
      queryFn: () => positionsApi.list(id).then((r) => r.data),
      staleTime: 300_000,
    })),
  });
  const data = results.map((r) => r.data);
  return useMemo(() => {
    const m = new Map<string, Position>();
    for (const list of data) for (const p of list ?? []) m.set(p.id, p);
    return m;
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Machine / position / assignee names for a work order (server fields first). */
export function useWoNames() {
  const { index } = useMachineIndex();
  const techs = useTechnicians();
  const me = useAuthStore((s) => s.user);
  return useMemo(() => {
    const techName = new Map((techs.data ?? []).map((t) => [t.id, t.name]));
    if (me) techName.set(me.id, me.name);
    return {
      machine: (wo: Pick<WorkOrder, "machine_id" | "machine_name">) =>
        wo.machine_name ?? (wo.machine_id ? index.get(wo.machine_id)?.name : null) ?? "—",
      line: (machineId: string | null | undefined) => (machineId ? index.get(machineId) : undefined),
      assignee: (wo: Pick<WorkOrder, "assignee_id" | "assignee_name">) =>
        wo.assignee_name ?? (wo.assignee_id ? techName.get(wo.assignee_id) ?? null : null),
      user: (id: string | null | undefined) => (id ? techName.get(id) ?? null : null),
    };
  }, [index, techs.data, me]);
}

// ── mutations ─────────────────────────────────────────────────────

function useInvalidateMaintenance() {
  const qc = useQueryClient();
  return () => Promise.all([
    qc.invalidateQueries({ queryKey: mntKeys.all }),
    qc.invalidateQueries({ queryKey: ["components"] }),
    qc.invalidateQueries({ queryKey: ["positions"] }),
  ]);
}

/** Puts the returned work order in its detail cache, then refreshes lists. */
function useWoMutation<V>(fn: (v: V) => Promise<WorkOrder>) {
  const qc = useQueryClient();
  const invalidate = useInvalidateMaintenance();
  return useMutation({
    mutationFn: fn,
    onSuccess: (wo) => {
      if (wo && typeof wo === "object" && "id" in wo) qc.setQueryData(mntKeys.workOrder(wo.id), wo);
      return invalidate();
    },
  });
}

export function useCreateWorkOrder() {
  return useWoMutation((body: WorkOrderCreateBody) => workOrdersApi.create(body).then((r) => r.data));
}

export function useTransition() {
  return useWoMutation(({ id, body }: { id: string; body: TransitionBody }) => workOrdersApi.transition(id, body).then((r) => r.data));
}

export function useLabor() {
  return useWoMutation(({ id, body }: { id: string; body: LaborBody }) => workOrdersApi.labor(id, body).then((r) => r.data));
}

export function useApproveDraft() {
  return useWoMutation((id: string) => workOrdersApi.approveDraft(id).then((r) => r.data));
}

export function useEmergencyCall() {
  return useWoMutation((body: Parameters<typeof workOrdersApi.emergencyCall>[0]) => workOrdersApi.emergencyCall(body).then((r) => r.data));
}

/** Reprogram a scheduled / on-hold WO (PATCH scheduled_for, assignee_id — keeps scheduled_at, S4-5). */
export function useUpdateWorkOrder() {
  return useWoMutation(({ id, body }: { id: string; body: Parameters<typeof workOrdersApi.update>[1] }) => workOrdersApi.update(id, body).then((r) => r.data));
}

export function useComponentsLife(q: { plant_id?: string; line_id?: string } = {}) {
  return useQuery({
    queryKey: ["maintenance", "components", q],
    queryFn: () => maintenanceKpiApi.components(q).then((r) => r.data),
    refetchInterval: 120_000,
  });
}

export function useReplacement() {
  return useWoMutation(({ id, body }: { id: string; body: ReplacementBody }) => workOrdersApi.replacement(id, body).then((r) => r.data));
}

export function useSavePlan() {
  const invalidate = useInvalidateMaintenance();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: MaintenancePlanBody }) =>
      (id ? plansApi.update(id, body) : plansApi.create(body)).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useDeletePlan() {
  const invalidate = useInvalidateMaintenance();
  return useMutation({ mutationFn: (id: string) => plansApi.remove(id), onSuccess: invalidate });
}

export function usePatchFailure() {
  const invalidate = useInvalidateMaintenance();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: FailurePatchBody }) => failuresApi.patch(id, body).then((r) => r.data),
    onSuccess: invalidate,
  });
}

// ── notifications ─────────────────────────────────────────────────

export function useNotifications(enabled = true) {
  return useQuery({
    queryKey: mntKeys.notifications,
    queryFn: () => notificationsApi.list(),
    refetchInterval: 120_000,
    enabled,
  });
}
