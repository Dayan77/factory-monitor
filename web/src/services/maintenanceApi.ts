/**
 * Maintenance endpoints (Sprint 4). Mirrors the backend contract; see maintenanceTypes.ts.
 */
import { api } from "./api";
import { parseNotification } from "./realtime";
import type {
  AppNotification, ComponentLifeRow, FailureCode, FailureCodes, FailurePatchBody, FailureQuery, FailureRecord, LaborBody, MaintenanceKpis,
  MaintenancePlan, MaintenancePlanBody, PlanStatus, PlanStatusQuery, ReliabilityGroupBy, ReliabilityRow, ReplacementBody,
  EmergencyCallResponse, TechnicianUser, TransitionBody, TransitionResult, WorkOrder, WorkOrderCreateBody, WorkOrderQuery,
} from "./maintenanceTypes";

/** Drops undefined/empty params so the URL only carries real filters. */
function clean<T extends object>(q: T): Partial<T> {
  return Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== "")) as Partial<T>;
}

export const plansApi = {
  status: (q: PlanStatusQuery = {}) => api.get<PlanStatus[]>("/api/maintenance-plans/status", { params: clean(q) }),
  list: () => api.get<MaintenancePlan[]>("/api/maintenance-plans/"),
  get: (id: string) => api.get<MaintenancePlan>(`/api/maintenance-plans/${id}`),
  create: (body: MaintenancePlanBody) => api.post<MaintenancePlan>("/api/maintenance-plans/", body),
  update: (id: string, body: Partial<MaintenancePlanBody>) => api.patch<MaintenancePlan>(`/api/maintenance-plans/${id}`, body),
  remove: (id: string) => api.delete(`/api/maintenance-plans/${id}`),
};

export const workOrdersApi = {
  list: (q: WorkOrderQuery = {}) => api.get<WorkOrder[]>("/api/work-orders/", { params: clean(q) }),
  get: (id: string) => api.get<WorkOrder>(`/api/work-orders/${id}`),
  create: (body: WorkOrderCreateBody) => api.post<WorkOrder>("/api/work-orders/", body),
  transition: (id: string, body: TransitionBody, actionId?: string) =>
    api.post<TransitionResult>(`/api/work-orders/${id}/transition`, body, actionHeaders(actionId)),
  /** One click "aprovar rascunho": draft → open → approved (two audited transitions). */
  approveDraft: (id: string) => api.post<WorkOrder>(`/api/work-orders/${id}/approve-draft`, {}),
  /** "Chamar manutenção": emergency corrective WO linked to the stop (returns the open one if already called). */
  emergencyCall: (body: { downtime_event_id?: string; machine_id?: string; position_id?: string; title?: string; note?: string }) =>
    api.post<EmergencyCallResponse>("/api/work-orders/emergency-call", body),
  /** Planner edits — reprogramming a scheduled WO (scheduled_for / assignee_id). */
  update: (id: string, body: { scheduled_for?: string; assignee_id?: string | null }) => api.patch<WorkOrder>(`/api/work-orders/${id}`, body),
  labor: (id: string, body: LaborBody, actionId?: string) =>
    api.post<WorkOrder>(`/api/work-orders/${id}/labor`, body, actionHeaders(actionId)),
  task: (id: string, body: { task_id: string; done: boolean }, actionId?: string) =>
    api.patch<WorkOrder>(`/api/work-orders/${id}/tasks`, body, actionHeaders(actionId)),
  replacement: (id: string, body: ReplacementBody) => api.post<WorkOrder>(`/api/work-orders/${id}/replacements`, body),
};

/** Offline-queued actions carry a stable id so a server can drop a replayed duplicate. */
function actionHeaders(actionId?: string) {
  return actionId ? { headers: { "X-Client-Action-Id": actionId } } : undefined;
}

export const failuresApi = {
  list: (q: FailureQuery = {}) => api.get<FailureRecord[]>("/api/failures/", { params: clean(q) }),
  get: (id: string) => api.get<FailureRecord>(`/api/failures/${id}`),
  patch: (id: string, body: FailurePatchBody) => api.patch<FailureRecord>(`/api/failures/${id}`, body),
  codes: () => api.get<unknown>("/api/failure-codes/").then((r) => normalizeFailureCodes(r.data)),
  /** Failure found without a stop (e.g. in a preventive: detection_method = periodic_maintenance). */
  create: (body: { machine_id: string; position_id?: string | null; failed_at: string; severity: string; failure_mode?: string | null;
    detection_method?: string | null; work_order_id?: string | null; note?: string | null }) => api.post<FailureRecord>("/api/failures/", body),
};

export const maintenanceKpiApi = {
  kpis: (q: { plant_id?: string; start: string; stop: string }) =>
    api.get<MaintenanceKpis>("/api/maintenance/kpis", { params: clean(q) }),
  reliability: (q: { group_by: ReliabilityGroupBy; start?: string; stop?: string }) =>
    api.get<unknown>("/api/maintenance/reliability", { params: q }).then((r) => rowsOf<ReliabilityRow>(r.data)),
  components: (q: { plant_id?: string; line_id?: string } = {}) =>
    api.get<ComponentLifeRow[]>("/api/maintenance/components", { params: clean(q) }),
};

/** Accepts a bare list or `{rows|items: [...]}`. */
export function rowsOf<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (isRecord(data)) {
    const inner = data.rows ?? data.items;
    if (Array.isArray(inner)) return inner as T[];
  }
  return [];
}

export const usersApi = {
  /** Technicians of the tenant (assignee picker). */
  technicians: () => api.get<TechnicianUser[]>("/api/users/", { params: { role: "technician" } }),
};

export const notificationsApi = {
  list: () => api.get<unknown>("/api/notifications/").then((r) =>
    rowsOf<unknown>(r.data).map(parseNotification).filter((n): n is AppNotification => n !== null)),
  read: (id: string) => api.post(`/api/notifications/${id}/read`, {}),
  readAll: () => api.post("/api/notifications/read-all", {}),
  preferences: () => api.get<unknown>("/api/notifications/preferences"),
  savePreferences: (body: unknown) => api.put<unknown>("/api/notifications/preferences", body),
};

export const pushApi = {
  vapidKey: () => api.get<{ public_key?: string | null; key?: string | null; enabled?: boolean } | string>("/api/push/vapid-public-key"),
  subscribe: (body: { endpoint: string; keys: { p256dh: string; auth: string } }) => api.post("/api/push/subscribe", body),
};

// ── normalization ─────────────────────────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function toCodes(v: unknown): FailureCode[] {
  if (Array.isArray(v)) {
    return v.flatMap((it): FailureCode[] => {
      if (!isRecord(it) || typeof it.code !== "string") return [];
      return [{
        code: it.code,
        label: typeof it.label === "string" ? it.label : it.code,
        group: typeof it.group === "string" ? it.group : null,
        component_types: Array.isArray(it.component_types) ? it.component_types.filter((t): t is string => typeof t === "string") : null,
      }];
    });
  }
  // {"ELP": "Vazamento externo", …}
  if (isRecord(v)) return Object.entries(v).filter(([, l]) => typeof l === "string").map(([code, label]) => ({ code, label: label as string }));
  return [];
}

/** Accepts `{modes|failure_modes, mechanisms|failure_mechanisms, causes|failure_causes, detection_methods}`. */
export function normalizeFailureCodes(data: unknown): FailureCodes {
  const d = isRecord(data) ? data : {};
  let modes = toCodes(d.modes ?? d.failure_modes);
  // {"pneumatic_cylinder": ["ELP", "INL", …]} → component_types on each mode
  if (isRecord(d.modes_by_component_type)) {
    const byType = d.modes_by_component_type;
    modes = modes.map((m) => {
      const types = Object.entries(byType).filter(([, codes]) => Array.isArray(codes) && codes.includes(m.code)).map(([t]) => t);
      return types.length ? { ...m, component_types: [...(m.component_types ?? []), ...types] } : m;
    });
  }
  return {
    modes,
    mechanisms: toCodes(d.mechanisms ?? d.failure_mechanisms),
    causes: toCodes(d.causes ?? d.failure_causes),
    detection_methods: toCodes(d.detection_methods ?? d.detection),
  };
}
