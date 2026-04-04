import { create } from "zustand";
import { authApi } from "@/services/api";

interface AuthUser {
  id: string;
  name: string;
  email: string;
  role: string;
  tenant_id: string;
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login:  (email: string, password: string) => Promise<void>;
  logout: () => void;
  loadMe: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user:    null,
  loading: false,

  login: async (email, password) => {
    set({ loading: true });
    const res = await authApi.login(email, password);
    localStorage.setItem("access_token",  res.data.access_token);
    localStorage.setItem("refresh_token", res.data.refresh_token);
    const me = await authApi.me();
    set({ user: me.data, loading: false });
  },

  logout: () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    set({ user: null });
  },

  loadMe: async () => {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    set({ loading: true });
    try {
      const me = await authApi.me();
      set({ user: me.data });
    } catch {
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
    } finally {
      set({ loading: false });
    }
  },
}));
