/** API calls of the Sprint 6 screens (plant map, onboarding wizard, demo, reports). */
import axios from "axios";
import { api } from "./api";
import type {
  BrowseCommand, CatalogEntry, DemoSessionInfo, DemoSessionOut, DemoStatus, GenerateBody, ImportPreview, LayoutMarker,
  MapView, OnboardingProgress, PlantLayout, Readiness, ReportRun, ReportSchedule, ReportsStatus, ScheduleBody,
  ScheduleHistory, StepState,
} from "./sprint6Types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export const layoutApi = {
  list: (plantId: string) => api.get<PlantLayout[]>(`/api/plants/${plantId}/layouts`),
  createDraft: (plantId: string) => api.post<PlantLayout>(`/api/plants/${plantId}/layouts`),
  get: (id: string) => api.get<PlantLayout>(`/api/layouts/${id}`),
  patch: (id: string, body: { scale_m_per_px: number | null }) => api.patch<PlantLayout>(`/api/layouts/${id}`, body),
  uploadImage: (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post<PlantLayout>(`/api/layouts/${id}/image`, fd, { timeout: 60_000 });
  },
  /** The floor-plan image needs the tenant JWT: fetched as a blob (an <img src> cannot send headers). */
  image: (id: string) => api.get<Blob>(`/api/layouts/${id}/image`, { responseType: "blob", timeout: 60_000 }),
  putMarkers: (id: string, markers: LayoutMarker[]) =>
    api.put<PlantLayout>(`/api/layouts/${id}/markers`, {
      markers: markers.map(({ kind, machine_id, line_id, label, x, y, rotation_deg, size }) => ({
        kind, machine_id, line_id, label, x, y, rotation_deg, size,
      })),
    }),
  publish: (id: string) => api.post<PlantLayout>(`/api/layouts/${id}/publish`),
  published: (plantId: string, kpis: boolean) =>
    api.get<MapView>(`/api/plants/${plantId}/layout/published`, { params: { kpis } }),
  auto: (plantId: string, kpis: boolean) => api.get<MapView>(`/api/plants/${plantId}/layout/auto`, { params: { kpis } }),
};

export const onboardingApi = {
  get: () => api.get<OnboardingProgress>("/api/onboarding"),
  put: (step: number, state: StepState, data?: Record<string, unknown>) =>
    api.put<OnboardingProgress>("/api/onboarding", { step, state, data }),
  readiness: (plantId?: string | null) =>
    api.get<Readiness>("/api/onboarding/readiness", { params: plantId ? { plant_id: plantId } : undefined }),
  importAssets: (plantId: string, csv: string, confirm: boolean) =>
    api.post<ImportPreview>("/api/onboarding/assets/import", { plant_id: plantId, csv, confirm }, { timeout: 60_000 }),
  importTags: (gatewayId: string, csv: string, confirm: boolean) =>
    api.post<ImportPreview>("/api/onboarding/tags/import", { gateway_id: gatewayId, csv, confirm }, { timeout: 60_000 }),
  templateUrl: (name: "assets.csv" | "tags.csv") => `/api/onboarding/templates/${name}`,
  template: (name: "assets.csv" | "tags.csv") => api.get<Blob>(`/api/onboarding/templates/${name}`, { responseType: "blob" }),
};

export const browseApi = {
  start: (gatewayId: string, body: { source_id: string; root_node_id?: string; max_depth?: number; max_nodes?: number }) =>
    api.post<BrowseCommand>(`/api/gateways/${gatewayId}/browse`, body),
  poll: (gatewayId: string, requestId: string) => api.get<BrowseCommand>(`/api/gateways/${gatewayId}/commands/${requestId}`),
};

export const demoApi = {
  /** No auth: "Ver demonstração" on the public site. */
  createSession: () => axios.post<DemoSessionOut>(`${BASE_URL}/api/public/demo-session`, {}, { timeout: 15_000 }),
  status: () => axios.get<DemoStatus>(`${BASE_URL}/api/public/demo-status`, { timeout: 15_000 }),
  session: () => api.get<DemoSessionInfo>("/api/demo/session"),
};

export const reportsApi = {
  catalog: () => api.get<CatalogEntry[]>("/api/reports/catalog"),
  status: () => api.get<ReportsStatus>("/api/reports/status"),
  generate: (body: GenerateBody) =>
    api.post<Blob>("/api/reports/generate", body, { responseType: "blob", timeout: 120_000 }),
  runs: (q: { schedule_id?: string; report_type?: string; limit?: number } = {}) =>
    api.get<ReportRun[]>("/api/report-runs/", { params: q }),
  download: (runId: string) => api.get<Blob>(`/api/report-runs/${runId}/download`, { responseType: "blob", timeout: 120_000 }),
  schedules: () => api.get<ReportSchedule[]>("/api/report-schedules/"),
  createSchedule: (body: ScheduleBody) => api.post<ReportSchedule>("/api/report-schedules/", body),
  updateSchedule: (id: string, body: Partial<ScheduleBody>) => api.patch<ReportSchedule>(`/api/report-schedules/${id}`, body),
  deleteSchedule: (id: string) => api.delete(`/api/report-schedules/${id}`),
  pause: (id: string) => api.post<ReportSchedule>(`/api/report-schedules/${id}/pause`),
  resume: (id: string) => api.post<ReportSchedule>(`/api/report-schedules/${id}/resume`),
  sendNow: (id: string) =>
    api.post<{ dispatch_id: string; status: string; period_key: string; period_label: string }>(
      `/api/report-schedules/${id}/send-now`, {}, { timeout: 120_000 }),
  history: (id: string) => api.get<ScheduleHistory>(`/api/report-schedules/${id}/history`),
};
