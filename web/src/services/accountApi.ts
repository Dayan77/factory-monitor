/**
 * Account security & privacy (Sprint 7 — docs/security.md, docs/privacy.md).
 * Mirrors server/src/api/routes/{auth,account,users}.py.
 */
import axios from "axios";
import { api } from "./api";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type?: string;
  expires_in?: number;
  session_id?: string;
}

export interface MfaChallenge {
  mfa_required: true;
  mfa_token: string;
  methods?: string[];
}

export type LoginResponse = TokenPair | MfaChallenge;

export function isMfaChallenge(r: LoginResponse): r is MfaChallenge {
  return (r as MfaChallenge).mfa_required === true && typeof (r as MfaChallenge).mfa_token === "string";
}

export interface AccountMe {
  id: string;
  tenant_id: string;
  email: string;
  name: string;
  role: string;
  roles: string[];
  created_at: string | null;
  last_login_at: string | null;
  password_changed_at: string | null;
  mfa_enabled: boolean;
  mfa_required: boolean;
}

export interface AccountSession {
  id: string;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  ip_prefix: string | null;
  user_agent: string | null;
  mfa: boolean;
  current: boolean;
}

export interface MfaStatus {
  enabled: boolean;
  enabled_at: string | null;
  required: boolean;
  pending: boolean;
  recovery_codes_left: number;
}

export interface MfaEnrollment {
  secret: string;
  otpauth_uri: string;
  digits?: number;
  period?: number;
}

export interface InvitationPreview {
  email: string;
  name: string | null;
  tenant_name: string | null;
  role: string;
  expires_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  name: string | null;
  role: string;
  roles: string[];
  line_ids: string[];
  state: "pending" | "accepted" | "revoked" | "expired";
  invited_by: string | null;
  user_id: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  /** Returned once, on creation (to share by another channel when e-mail is not configured). */
  invite_url?: string;
}

export interface InvitationBody {
  email: string;
  name?: string;
  role: string;
  roles?: string[];
  line_ids?: string[];
}

export interface TenantUser {
  id: string;
  name: string;
  email: string;
  role: string;
  roles?: string[];
  is_active?: boolean;
  line_ids?: string[];
}

/** Public endpoints (no Authorization header, never the 401 → refresh interceptor). */
const pub = axios.create({ baseURL: BASE_URL, timeout: 15_000 });

export const authFlowApi = {
  login: (email: string, password: string) => pub.post<LoginResponse>("/api/auth/login", { email, password }),
  /** Second step of the login: TOTP code or a recovery code. */
  mfaVerify: (mfa_token: string, code: { code?: string; recovery_code?: string }) =>
    pub.post<TokenPair>("/api/auth/mfa/verify", { mfa_token, ...code }),
  requestReset: (email: string) => pub.post<{ status: string; message?: string }>("/api/auth/password-reset/request", { email }),
  confirmReset: (token: string, new_password: string) =>
    pub.post("/api/auth/password-reset/confirm", { token, new_password }),
  invitation: (token: string) => pub.get<InvitationPreview>(`/api/auth/invitations/${encodeURIComponent(token)}`),
  acceptInvitation: (token: string, password: string, name?: string) =>
    pub.post<TokenPair>("/api/auth/invitations/accept", { token, password, ...(name ? { name } : {}) }),
};

export const accountApi = {
  me: () => api.get<AccountMe>("/api/account/me"),
  updateProfile: (name: string) => api.patch<AccountMe>("/api/account/me", { name }),
  changePassword: (current_password: string, new_password: string) =>
    api.post("/api/account/password", { current_password, new_password }),
  sessions: () => api.get<AccountSession[]>("/api/account/sessions"),
  revokeSession: (id: string) => api.delete(`/api/account/sessions/${encodeURIComponent(id)}`),
  revokeOtherSessions: () => api.delete("/api/account/sessions"),
  mfa: () => api.get<MfaStatus>("/api/account/mfa"),
  mfaEnroll: (password: string) => api.post<MfaEnrollment>("/api/account/mfa/enroll", { password }),
  mfaConfirm: (code: string) => api.post<{ enabled: true; recovery_codes: string[] }>("/api/account/mfa/confirm", { code }),
  mfaRecoveryCodes: (code: string) => api.post<{ recovery_codes: string[] }>("/api/account/mfa/recovery-codes", { code }),
  mfaDisable: (password: string, code: string) => api.post("/api/account/mfa/disable", { password, code }),
  exportData: () => api.get<Blob>("/api/account/export", { responseType: "blob" }),
  deleteAccount: (password: string) => api.delete("/api/account", { data: { password, confirm: "EXCLUIR" } }),
  logout: () => api.post("/api/auth/logout"),
  logoutAll: () => api.post("/api/auth/logout-all"),
  tenantSecurity: () => api.get<{ require_mfa_admins: boolean; updated_at: string | null }>("/api/account/tenant-security"),
  setTenantSecurity: (require_mfa_admins: boolean) =>
    api.put<{ require_mfa_admins: boolean; updated_at: string | null }>("/api/account/tenant-security", { require_mfa_admins }),
};

export const usersApi = {
  list: () => api.get<TenantUser[]>("/api/users/"),
  invitations: () => api.get<Invitation[]>("/api/users/invitations"),
  invite: (body: InvitationBody) => api.post<Invitation>("/api/users/invitations", body),
  revokeInvitation: (id: string) => api.delete(`/api/users/invitations/${encodeURIComponent(id)}`),
};

/** Store a token pair as the current session (login, MFA step, accepted invitation). */
export function storeTokens(t: TokenPair): void {
  localStorage.setItem("access_token", t.access_token);
  localStorage.setItem("refresh_token", t.refresh_token);
}
