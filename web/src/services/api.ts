/**
 * Axios instance with:
 * - JWT Authorization header injection
 * - Automatic token refresh on 401
 * - Tenant-scoped requests (token embeds tenant_id)
 */

import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";
import type {
  AlertEvent, CalendarException, CalendarExceptionBody, Camera, Component, ComponentCreateBody, DowntimeCreateBody,
  DowntimeEvent, DowntimePatchBody, DowntimeQuery, LeadBody, Line, LineProduction, Machine, MachineProduct,
  MachineNoData, MachineProductBody, MachineUsage, NoDataQuery, NominalLife, OeeQuery, OeeResult, Overview, ParetoRow, Position, PositionDetail,
  Product, ProductionRun, ReasonCode, ReasonCodeCreateBody, ReasonCodeUpdateBody, Reliability, RemovalReason,
  SensorHistory, Shift, ShiftBody, ShiftReport, Tag, TreePlant, UsageHistory, UsageMeasure,
} from "./types";
import { normalizeOverview } from "./efficiency";
import { DEMO_WRITE_MSG, demoAllowsRequest, tokenIsDemo } from "@/utils/demo";
import { TOKENS_EVENT } from "@/utils/jwt";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export const api = axios.create({ baseURL: BASE_URL, timeout: 15_000 });

// ── Inject access token ───────────────────────────────────────────
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  // Public demo (sprint6 §5): read-only. The server answers 403 anyway; failing here keeps the message identical
  // without a round trip (and never sends a write from a demo session).
  if (tokenIsDemo(token) && !demoAllowsRequest(config.method, config.url)) {
    const err = new AxiosError(DEMO_WRITE_MSG, "ERR_DEMO_READ_ONLY", config, null, {
      data: { detail: DEMO_WRITE_MSG }, status: 403, statusText: "Forbidden", headers: {}, config,
    });
    return Promise.reject(err);
  }
  return config;
});

// ── Auto-refresh on 401 ───────────────────────────────────────────
// The server rotates the refresh token on every use and treats a second use of the old one as theft (the whole
// session is revoked — docs/security.md). Several tabs of the same browser share localStorage, so a refresh must
// never be sent twice with the same token: it runs under a cross-tab lock (Web Locks API when available) and, once
// inside, a tab whose stored refresh token changed meanwhile just adopts the tokens the other tab obtained.
let _refreshing: Promise<string> | null = null;

/** Paths the refresh flow never touches (public auth endpoints answer 401 for bad credentials / codes). */
const NO_REFRESH = /\/api\/auth\/(login|refresh|mfa\/verify|password-reset|invitations|register)/;

async function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = (navigator as Navigator & { locks?: { request: (n: string, cb: () => Promise<T>) => Promise<T> } }).locks;
  return locks?.request ? locks.request("fm-token-refresh", fn) : fn();
}

export async function refreshTokens(staleRefresh: string | null): Promise<string> {
  return withRefreshLock(async () => {
    const current = localStorage.getItem("refresh_token");
    const access = localStorage.getItem("access_token");
    // another tab already rotated while we waited for the lock
    if (current && staleRefresh && current !== staleRefresh && access) return access;
    if (!current) throw new Error("no refresh token");
    const res = await axios.post<{ access_token: string; refresh_token: string }>(
      `${BASE_URL}/api/auth/refresh`, { refresh_token: current },
    );
    localStorage.setItem("access_token", res.data.access_token);
    localStorage.setItem("refresh_token", res.data.refresh_token);
    // the realtime socket re-authenticates in place (no reconnect, no gap in the live data)
    window.dispatchEvent(new CustomEvent(TOKENS_EVENT, { detail: res.data.access_token }));
    return res.data.access_token;
  });
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean };
    if (error.response?.status !== 401 || !original || original._retry || NO_REFRESH.test(original.url ?? "")) {
      return Promise.reject(error);
    }

    original._retry = true;

    if (!_refreshing) {
      const refresh = localStorage.getItem("refresh_token");
      if (!refresh) {
        sessionExpired();
        return Promise.reject(error);
      }
      _refreshing = refreshTokens(refresh)
        .catch(() => {
          sessionExpired();
          return Promise.reject(new Error("Session expired"));
        })
        .finally(() => { _refreshing = null; });
    }

    const newToken = await _refreshing;
    original.headers.Authorization = `Bearer ${newToken}`;
    return api(original);
  }
);

/** sessionStorage key read by the login screen to explain why the user is there. */
export const SESSION_EXPIRED_KEY = "fm-session-expired";

/** Where the login screen sends the user back after a session expiry (same-origin app paths only). */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return null;
  if (/^\/(login|op\/login|esqueci-senha|redefinir-senha|convite)(\/|\?|$)/.test(next)) return null;
  return next;
}

/**
 * The session can no longer be renewed (refresh token expired, revoked from another device, password changed):
 * clear it and go to the login, keeping the current screen to come back to after signing in again.
 */
export function sessionExpired(): void {
  const demo = tokenIsDemo(localStorage.getItem("access_token"));
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  // an expired demo session (2 h) goes back to the public site, where "Ver demonstração" opens a new one
  if (demo) { window.location.replace("/?demo=expired"); return; }
  const here = window.location.pathname + window.location.search;
  try { sessionStorage.setItem(SESSION_EXPIRED_KEY, "1"); } catch { /* storage blocked */ }
  const op = here === "/op" || here.startsWith("/op/") || here.startsWith("/op?");
  const next = safeNext(here);
  const target = op ? "/op/login" : "/login";
  window.location.replace(next && !op ? `${target}?next=${encodeURIComponent(next)}` : target);
}

// ── Typed API calls ───────────────────────────────────────────────

export const authApi = {
  login:   (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string; token_type: string }>(
      "/api/auth/login", { email, password }
    ),
  me: () => api.get("/api/auth/me"),
  register: (body: { company_name: string; admin_name: string; admin_email: string; admin_password: string }) =>
    api.post<{ message: string; tenant_slug: string }>("/api/auth/register", body),
};

export const linesApi = {
  list:   ()               => api.get<Line[]>("/api/lines/"),
  get:    (id: string)     => api.get<Line>(`/api/lines/${id}`),
  create: (body: unknown)  => api.post("/api/lines/", body),
  update: (id: string, body: unknown) => api.patch(`/api/lines/${id}`, body),
  remove: (id: string)     => api.delete(`/api/lines/${id}`),
};

export const machinesApi = {
  list:   (lineId?: string) =>
    api.get<Machine[]>("/api/machines/", { params: lineId ? { line_id: lineId } : undefined }),
  get:    (id: string)      => api.get<Machine>(`/api/machines/${id}`),
  create: (body: unknown)   => api.post("/api/machines/", body),
  update: (id: string, body: unknown) => api.patch(`/api/machines/${id}`, body),
  remove: (id: string)      => api.delete(`/api/machines/${id}`),
};

export const tagsApi = {
  list:   (machineId?: string) =>
    api.get<Tag[]>("/api/tags/", { params: machineId ? { machine_id: machineId } : undefined }),
  create: (body: unknown)      => api.post("/api/tags/", body),
  update: (id: string, body: unknown) => api.patch(`/api/tags/${id}`, body),
  remove: (id: string)         => api.delete(`/api/tags/${id}`),
};

export const alertsApi = {
  listRules:    ()              => api.get("/api/alerts/rules"),
  createRule:   (body: unknown) => api.post("/api/alerts/rules", body),
  toggleRule:   (id: string)    => api.patch(`/api/alerts/rules/${id}/toggle`),
  deleteRule:   (id: string)    => api.delete(`/api/alerts/rules/${id}`),
  listEvents:   (activeOnly = false) =>
    api.get<AlertEvent[]>("/api/alerts/events", { params: { active_only: activeOnly } }),
  resolveEvent: (id: string)    => api.post(`/api/alerts/events/${id}/resolve`),
};

export const sensorsApi = {
  history: (tagId: string, params?: { start?: string; stop?: string; window?: string }) =>
    api.get<SensorHistory>("/api/sensors/history", { params: { tag_id: tagId, ...params } }),
  latest:  (tagIds: string[]) =>
    api.get<Record<string, number>>("/api/sensors/latest", { params: { tag_ids: tagIds }, paramsSerializer: (p) =>
      p.tag_ids.map((id: string) => `tag_ids=${id}`).join("&")
    }),
};

export const camerasApi = {
  list:   (machineId?: string) =>
    api.get<Camera[]>("/api/cameras/", { params: machineId ? { machine_id: machineId } : undefined }),
  get:    (id: string)         => api.get<Camera>(`/api/cameras/${id}`),
  create: (body: unknown)      => api.post("/api/cameras/", body),
  update: (id: string, body: unknown) => api.patch(`/api/cameras/${id}`, body),
  remove: (id: string)         => api.delete(`/api/cameras/${id}`),
  /** Returns the proxied HLS playlist URL for this camera. */
  hlsUrl: (id: string)         => `/api/cameras/${id}/hls/index.m3u8`,
};

export const adminApi = {
  // Tenants
  listTenants:   ()                              => api.get("/api/admin/tenants"),
  getTenant:     (id: string)                    => api.get(`/api/admin/tenants/${id}`),
  createTenant:  (body: unknown)                 => api.post("/api/admin/tenants", body),
  updateTenant:  (id: string, body: unknown)     => api.patch(`/api/admin/tenants/${id}`, body),
  deleteTenant:  (id: string)                    => api.delete(`/api/admin/tenants/${id}`),
  // Users de um tenant
  listUsers:     (tenantId: string)              => api.get(`/api/admin/tenants/${tenantId}/users`),
  createUser:    (tenantId: string, body: unknown) => api.post(`/api/admin/tenants/${tenantId}/users`, body),
  updateUser:    (tenantId: string, userId: string, body: unknown) =>
    api.patch(`/api/admin/tenants/${tenantId}/users/${userId}`, body),
  deleteUser:    (tenantId: string, userId: string) =>
    api.delete(`/api/admin/tenants/${tenantId}/users/${userId}`),
};

// ── Assets & usage (Fase 1) ───────────────────────────────────────

export interface TimeWindow {
  start?: string;
  stop?: string;
  bucket?: "1h" | "1d";
}

export const plantsApi = {
  tree: () => api.get<TreePlant[]>("/api/plants/tree"),
};

export const positionsApi = {
  list: (machineId: string) => api.get<Position[]>("/api/positions/", { params: { machine_id: machineId } }),
  /** Every position of the tenant (global search). */
  listAll: () => api.get<Position[]>("/api/positions/"),
  get: (id: string) => api.get<PositionDetail>(`/api/positions/${id}`),
  install: (id: string, componentId: string) =>
    api.post<PositionDetail>(`/api/positions/${id}/install`, { component_id: componentId }),
};

export const componentsApi = {
  list: (params: { position_id?: string; in_stock?: boolean }) =>
    api.get<Component[]>("/api/components/", { params }),
  create: (body: ComponentCreateBody) => api.post<Component>("/api/components/", body),
  update: (id: string, body: Partial<{ manufacturer: string | null; model: string | null; serial_number: string | null; nominal_life: NominalLife }>) =>
    api.patch<Component>(`/api/components/${id}`, body),
  remove: (id: string, reason: RemovalReason) => api.post<Component>(`/api/components/${id}/remove`, { reason }),
};

export const usageApi = {
  machine: (machineId: string) => api.get<MachineUsage>(`/api/machines/${machineId}/usage`),
  history: (
    scope: { position_id: string } | { machine_id: string },
    measure: UsageMeasure,
    window: TimeWindow = {},
  ) => api.get<UsageHistory>("/api/usage/history", { params: { ...scope, measure, bucket: "1h", ...window } }),
  lineProduction: (lineId: string, window: TimeWindow = {}) =>
    api.get<LineProduction>(`/api/lines/${lineId}/production`, { params: { bucket: "1h", ...window } }),
};

// ── Eficiência produtiva (Fase 2) ─────────────────────────────────

type Window = { start?: string; stop?: string };

export const efficiencyApi = {
  /** "Raio-X" of a plant (default: the tenant's first plant), normalized to the UI shape. */
  overview: (plantId?: string) =>
    api.get<unknown>("/api/overview", { params: plantId ? { plant_id: plantId } : undefined })
      .then((r): Overview => normalizeOverview(r.data)),
  oee: (q: OeeQuery) => api.get<OeeResult>("/api/oee", { params: q }),
  pareto: (q: { scope: OeeQuery["scope"]; id: string } & Window) => api.get<ParetoRow[]>("/api/losses/pareto", { params: q }),
  reliability: (q: { scope: "machine" | "line"; id: string } & Window) => api.get<Reliability>("/api/reliability", { params: q }),
  shiftReport: (q: { line_id: string; productive_day: string; shift_id?: string }) =>
    api.get<ShiftReport>("/api/reports/shift", { params: q }),
};

export const downtimeApi = {
  list: (q: DowntimeQuery) => api.get<DowntimeEvent[]>("/api/downtime/", { params: q }),
  noData: (q: NoDataQuery) => api.get<MachineNoData[]>("/api/downtime/no-data", { params: q }),
  justify: (id: string, body: DowntimePatchBody) => api.patch<DowntimeEvent>(`/api/downtime/${id}`, body),
  create: (body: DowntimeCreateBody) => api.post<DowntimeEvent>("/api/downtime/", body),
};

export const reasonCodesApi = {
  list: (includeInactive = false) =>
    api.get<ReasonCode[]>("/api/reason-codes/", { params: includeInactive ? { include_inactive: true } : undefined }),
  create: (body: ReasonCodeCreateBody) => api.post<ReasonCode>("/api/reason-codes/", body),
  update: (id: string, body: ReasonCodeUpdateBody) => api.patch<ReasonCode>(`/api/reason-codes/${id}`, body),
};

export const shiftsApi = {
  list: (plantId?: string) => api.get<Shift[]>("/api/shifts/", { params: plantId ? { plant_id: plantId } : undefined }),
  create: (body: ShiftBody) => api.post<Shift>("/api/shifts/", body),
  update: (id: string, body: Partial<ShiftBody>) => api.patch<Shift>(`/api/shifts/${id}`, body),
  remove: (id: string) => api.delete(`/api/shifts/${id}`),
};

export const calendarApi = {
  list: (plantId?: string) =>
    api.get<CalendarException[]>("/api/calendar-exceptions/", { params: plantId ? { plant_id: plantId } : undefined }),
  create: (body: CalendarExceptionBody) => api.post<CalendarException>("/api/calendar-exceptions/", body),
  remove: (id: string) => api.delete(`/api/calendar-exceptions/${id}`),
};

export const productsApi = {
  list: () => api.get<Product[]>("/api/products/"),
  create: (body: { code: string; name: string }) => api.post<Product>("/api/products/", body),
  update: (id: string, body: { code?: string; name?: string }) => api.patch<Product>(`/api/products/${id}`, body),
  remove: (id: string) => api.delete(`/api/products/${id}`),
  forMachine: (machineId: string) => api.get<MachineProduct[]>(`/api/machines/${machineId}/products`),
  addToMachine: (machineId: string, body: MachineProductBody) =>
    api.post<MachineProduct>(`/api/machines/${machineId}/products`, body),
  updateOnMachine: (machineId: string, mpId: string, body: Partial<Omit<MachineProductBody, "product_id">>) =>
    api.patch<MachineProduct>(`/api/machines/${machineId}/products/${mpId}`, body),
  removeFromMachine: (machineId: string, mpId: string) => api.delete(`/api/machines/${machineId}/products/${mpId}`),
};

export const runsApi = {
  list: (q: { machine_id?: string; open?: boolean }) => api.get<ProductionRun[]>("/api/production-runs/", { params: q }),
  start: (body: { machine_id: string; product_id: string }) => api.post<ProductionRun>("/api/production-runs/", body),
  stop: (id: string) => api.post<ProductionRun>(`/api/production-runs/${id}/stop`, {}),
};

// ── Public (no auth) ──────────────────────────────────────────────

export const publicApi = {
  /** 202 on success (also for honeypot hits); 422 validation; 429 rate limit. */
  createLead: (body: LeadBody) => axios.post<{ status: string }>(`${BASE_URL}/api/public/leads`, body, { timeout: 15_000 }),
};
