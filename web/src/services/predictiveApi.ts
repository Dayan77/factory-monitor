/**
 * Predictive endpoints (Sprint 5 — docs/predictive.md P-12). See predictiveTypes.ts.
 */
import { api } from "./api";
import type { WorkOrder } from "./maintenanceTypes";
import type {
  AlertQuery, Baseline, ConvertAlertBody, Evaluation, FeatureQuery, FeaturesResponse, Monitor, MonitorCreateBody,
  MonitorPatchBody, MonitorQuery, PredictiveAlert, PredRule, RuleCreateBody, RulePatchBody, TransitionAlertBody,
} from "./predictiveTypes";

function clean<T extends object>(q: T): Partial<T> {
  return Object.fromEntries(Object.entries(q).filter(([, v]) =>
    v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))) as Partial<T>;
}

/** FastAPI reads repeated query keys (`state=open&state=acknowledged`) for list params. */
function serialize(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else if (v !== undefined && v !== null) sp.append(k, String(v));
  }
  return sp.toString();
}

export const predictiveApi = {
  monitors: (q: MonitorQuery = {}) => api.get<Monitor[]>("/api/predictive/monitors", { params: clean(q) }),
  monitor: (id: string) => api.get<Monitor>(`/api/predictive/monitors/${id}`),
  createMonitor: (body: MonitorCreateBody) => api.post<Monitor>("/api/predictive/monitors", body),
  updateMonitor: (id: string, body: MonitorPatchBody) => api.patch<Monitor>(`/api/predictive/monitors/${id}`, body),
  rebaseline: (id: string, reason: string) => api.post<Baseline>(`/api/predictive/monitors/${id}/rebaseline`, { reason }),
  createRule: (body: RuleCreateBody) => api.post<PredRule>("/api/predictive/rules", body),
  updateRule: (id: string, body: RulePatchBody) => api.patch<PredRule>(`/api/predictive/rules/${id}`, body),
  deleteRule: (id: string) => api.delete(`/api/predictive/rules/${id}`),
  features: (q: FeatureQuery) => api.get<FeaturesResponse>("/api/predictive/features", { params: clean(q) }),
  alerts: (q: AlertQuery = {}) => api.get<PredictiveAlert[]>("/api/predictive/alerts", {
    params: clean(q), paramsSerializer: { serialize: (p) => serialize(p as Record<string, unknown>) },
  }),
  alert: (id: string) => api.get<PredictiveAlert>(`/api/predictive/alerts/${id}`),
  transition: (id: string, body: TransitionAlertBody) => api.post<PredictiveAlert>(`/api/predictive/alerts/${id}/transition`, body),
  convert: (id: string, body: ConvertAlertBody = {}) =>
    api.post<PredictiveAlert & { work_order: WorkOrder }>(`/api/predictive/alerts/${id}/convert`, body),
  evaluation: (q: { start?: string; stop?: string; line_id?: string } = {}) =>
    api.get<Evaluation>("/api/predictive/evaluation", { params: clean(q) }),
};
