import { create } from "zustand";
import { isAxiosError } from "axios";
import { authApi } from "@/services/api";
import { purgeOtherUsersSnapshots } from "@/services/offline";

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  /** Primary role (legacy field). */
  role: string;
  /** All roles of the user (Sprint 4: maintenance_planner, technician, production_supervisor, manager, admin, operator). */
  roles?: string[];
  tenant_id: string;
  /** Lines of the user's area (operator tablet: an operator only acts on these, sprint6 §3.2). */
  line_ids?: string[];
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  /** True when the user came from the device cache because the server was unreachable (offline). */
  offline: boolean;
  login:  (email: string, password: string) => Promise<void>;
  logout: () => void;
  loadMe: () => Promise<void>;
}

/** Last /auth/me, kept on the device so the technician app opens offline (persistent login, workflows §3). */
const USER_CACHE = "fm-user";

function cacheUser(u: AuthUser | null) {
  try {
    if (u) localStorage.setItem(USER_CACHE, JSON.stringify(u));
    else localStorage.removeItem(USER_CACHE);
  } catch { /* storage blocked */ }
  // offline data is per user (services/offline.ts): a new user on a shared phone never sees the previous
  // user's cached work orders; queued actions stay with their author.
  if (u) purgeOtherUsersSnapshots();
}

export function cachedUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_CACHE);
    if (!raw) return null;
    const u = JSON.parse(raw) as AuthUser;
    return u && typeof u.id === "string" && typeof u.tenant_id === "string" ? u : null;
  } catch {
    return null;
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user:    null,
  loading: false,
  offline: false,

  login: async (email, password) => {
    set({ loading: true });
    try {
      const res = await authApi.login(email, password);
      localStorage.setItem("access_token",  res.data.access_token);
      localStorage.setItem("refresh_token", res.data.refresh_token);
      const me = await authApi.me();
      cacheUser(me.data);
      set({ user: me.data, offline: false });
    } finally {
      set({ loading: false });
    }
  },

  logout: () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    cacheUser(null);
    set({ user: null, offline: false });
  },

  loadMe: async () => {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    set({ loading: true });
    try {
      const me = await authApi.me();
      cacheUser(me.data);
      set({ user: me.data, offline: false });
    } catch (err) {
      // No response = network down (offline shop floor): keep the session and use the cached user.
      const cached = cachedUser();
      if (isAxiosError(err) && !err.response && cached) {
        set({ user: cached, offline: true });
      } else {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        cacheUser(null);
      }
    } finally {
      set({ loading: false });
    }
  },
}));
