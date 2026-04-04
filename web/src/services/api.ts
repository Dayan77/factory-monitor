/**
 * Axios instance with:
 * - JWT Authorization header injection
 * - Automatic token refresh on 401
 * - Tenant-scoped requests (token embeds tenant_id)
 */

import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export const api = axios.create({ baseURL: BASE_URL, timeout: 15_000 });

// ── Inject access token ───────────────────────────────────────────
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Auto-refresh on 401 ───────────────────────────────────────────
let _refreshing: Promise<string> | null = null;

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean };
    if (error.response?.status !== 401 || original._retry) {
      return Promise.reject(error);
    }

    original._retry = true;

    if (!_refreshing) {
      const refresh = localStorage.getItem("refresh_token");
      if (!refresh) {
        _logout();
        return Promise.reject(error);
      }
      _refreshing = axios
        .post<{ access_token: string; refresh_token: string }>(
          `${BASE_URL}/api/auth/refresh`,
          { refresh_token: refresh }
        )
        .then((res) => {
          localStorage.setItem("access_token",  res.data.access_token);
          localStorage.setItem("refresh_token", res.data.refresh_token);
          return res.data.access_token;
        })
        .catch(() => {
          _logout();
          return Promise.reject(new Error("Session expired"));
        })
        .finally(() => { _refreshing = null; });
    }

    const newToken = await _refreshing;
    original.headers.Authorization = `Bearer ${newToken}`;
    return api(original);
  }
);

function _logout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  window.location.replace("/login");
}

// ── Typed API calls ───────────────────────────────────────────────

export const authApi = {
  login:   (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string; token_type: string }>(
      "/api/auth/login", { email, password }
    ),
  me: () => api.get("/api/auth/me"),
};

export const linesApi = {
  list:   ()               => api.get("/api/lines"),
  get:    (id: string)     => api.get(`/api/lines/${id}`),
  create: (body: unknown)  => api.post("/api/lines", body),
  update: (id: string, body: unknown) => api.patch(`/api/lines/${id}`, body),
  remove: (id: string)     => api.delete(`/api/lines/${id}`),
};

export const machinesApi = {
  list:   (lineId?: string) =>
    api.get("/api/machines", { params: lineId ? { line_id: lineId } : undefined }),
  get:    (id: string)      => api.get(`/api/machines/${id}`),
  create: (body: unknown)   => api.post("/api/machines", body),
  update: (id: string, body: unknown) => api.patch(`/api/machines/${id}`, body),
  remove: (id: string)      => api.delete(`/api/machines/${id}`),
};

export const tagsApi = {
  list:   (machineId?: string) =>
    api.get("/api/tags", { params: machineId ? { machine_id: machineId } : undefined }),
  create: (body: unknown)      => api.post("/api/tags", body),
  update: (id: string, body: unknown) => api.patch(`/api/tags/${id}`, body),
  remove: (id: string)         => api.delete(`/api/tags/${id}`),
};

export const alertsApi = {
  listRules:    ()              => api.get("/api/alerts/rules"),
  createRule:   (body: unknown) => api.post("/api/alerts/rules", body),
  toggleRule:   (id: string)    => api.patch(`/api/alerts/rules/${id}/toggle`),
  deleteRule:   (id: string)    => api.delete(`/api/alerts/rules/${id}`),
  listEvents:   (activeOnly = false) =>
    api.get("/api/alerts/events", { params: { active_only: activeOnly } }),
  resolveEvent: (id: string)    => api.post(`/api/alerts/events/${id}/resolve`),
};

export const sensorsApi = {
  history: (tagId: string, params?: { start?: string; stop?: string; window?: string }) =>
    api.get("/api/sensors/history", { params: { tag_id: tagId, ...params } }),
  latest:  (tagIds: string[]) =>
    api.get("/api/sensors/latest", { params: { tag_ids: tagIds }, paramsSerializer: (p) =>
      p.tag_ids.map((id: string) => `tag_ids=${id}`).join("&")
    }),
};

export const aiApi = {
  chat: (message: string) => api.post<{ reply: string }>("/api/ai/chat", { message }),
};
