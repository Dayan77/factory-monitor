import { create } from "zustand";
import { isAxiosError } from "axios";
import axios from "axios";
import { authApi } from "@/services/api";
import { authFlowApi, isMfaChallenge, storeTokens, type TokenPair } from "@/services/accountApi";
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

export type LoginResult = { mfa: false } | { mfa: true; mfaToken: string };

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  /** True when the user came from the device cache because the server was unreachable (offline). */
  offline: boolean;
  /** First step of the login. Resolves `{mfa: true, mfaToken}` when the account has MFA (then call verifyMfa). */
  login:  (email: string, password: string) => Promise<LoginResult>;
  /** Second step: TOTP code or recovery code for the challenge returned by login(). */
  verifyMfa: (mfaToken: string, code: { code: string } | { recovery_code: string }) => Promise<void>;
  /** A token pair obtained elsewhere (accepted invitation) becomes the session. */
  signInWithTokens: (t: TokenPair) => Promise<void>;
  /** Ends the session on the server too (best effort: offline the local session is dropped anyway). */
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
      const res = await authFlowApi.login(email, password);
      if (isMfaChallenge(res.data)) return { mfa: true, mfaToken: res.data.mfa_token };
      storeTokens(res.data);
      const me = await authApi.me();
      cacheUser(me.data);
      set({ user: me.data, offline: false });
      return { mfa: false };
    } finally {
      set({ loading: false });
    }
  },

  verifyMfa: async (mfaToken, code) => {
    set({ loading: true });
    try {
      const res = await authFlowApi.mfaVerify(mfaToken, code);
      storeTokens(res.data);
      const me = await authApi.me();
      cacheUser(me.data);
      set({ user: me.data, offline: false });
    } finally {
      set({ loading: false });
    }
  },

  signInWithTokens: async (t) => {
    storeTokens(t);
    const me = await authApi.me();
    cacheUser(me.data);
    set({ user: me.data, offline: false });
  },

  logout: () => {
    const token = localStorage.getItem("access_token");
    if (token) {
      // revoke the session server side; the header is set here because the tokens are cleared right below
      void axios.post(`${import.meta.env.VITE_API_URL ?? ""}/api/auth/logout`, null, {
        headers: { Authorization: `Bearer ${token}` }, timeout: 5_000,
      }).catch(() => undefined);
    }
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
