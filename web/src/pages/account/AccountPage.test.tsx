import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ok, renderWithClient } from "@/test/utils";

const acc = vi.hoisted(() => ({
  me: vi.fn(), updateProfile: vi.fn(), changePassword: vi.fn(), sessions: vi.fn(), revokeSession: vi.fn(), revokeOtherSessions: vi.fn(),
  mfa: vi.fn(), mfaEnroll: vi.fn(), mfaConfirm: vi.fn(), mfaRecoveryCodes: vi.fn(), mfaDisable: vi.fn(), exportData: vi.fn(),
  deleteAccount: vi.fn(), logout: vi.fn(), logoutAll: vi.fn(), tenantSecurity: vi.fn(), setTenantSecurity: vi.fn(),
}));
vi.mock("@/services/accountApi", async (orig) => ({ ...(await orig<typeof import("@/services/accountApi")>()), accountApi: acc }));
vi.mock("qrcode", () => ({ toString: vi.fn(async () => "<svg data-testid='qr'></svg>") }));

import AccountPage from "./AccountPage";
import { useAuthStore } from "@/store/auth";

const ME = { id: "u1", tenant_id: "t1", email: "ana@acme.com", name: "Ana Souza", role: "technician", roles: ["technician"],
  created_at: "2026-01-02T10:00:00Z", last_login_at: "2026-10-07T11:00:00Z", password_changed_at: null, mfa_enabled: false, mfa_required: false };
const SESSIONS = [
  { id: "s1", created_at: "2026-10-07T11:00:00Z", last_used_at: "2026-10-07T12:00:00Z", expires_at: null, ip_prefix: "10.0.0.0/24", user_agent: "Mozilla/5.0 (Windows NT 10.0) Chrome/129 Safari/537.36", mfa: false, current: true },
  { id: "s2", created_at: "2026-10-01T11:00:00Z", last_used_at: "2026-10-02T12:00:00Z", expires_at: null, ip_prefix: "10.0.9.0/24", user_agent: "Mozilla/5.0 (Linux; Android 14) Chrome/129 Mobile Safari/537.36", mfa: false, current: false },
];

beforeEach(() => {
  Object.values(acc).forEach((f) => f.mockReset());
  acc.me.mockResolvedValue(ok(ME));
  acc.sessions.mockResolvedValue(ok(SESSIONS));
  acc.mfa.mockResolvedValue(ok({ enabled: false, enabled_at: null, required: false, pending: false, recovery_codes_left: 0 }));
  useAuthStore.setState({ user: { id: "u1", name: "Ana Souza", email: "ana@acme.com", role: "technician", roles: ["technician"], tenant_id: "t1" } });
});

describe("Minha conta", () => {
  it("activates MFA: password → QR code + key → code → recovery codes shown once", async () => {
    acc.mfaEnroll.mockResolvedValue(ok({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/FactoryConnect:ana%40acme.com?secret=JBSWY3DPEHPK3PXP" }));
    acc.mfaConfirm.mockResolvedValue(ok({ enabled: true, recovery_codes: ["aaaaa-bbbbb", "ccccc-ddddd"] }));
    renderWithClient(<AccountPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Ativar verificação em duas etapas" }));
    await userEvent.type(screen.getByLabelText("Senha atual", { selector: "#mfa-pw" }), "Senha-Atual-123");
    await userEvent.click(screen.getByRole("button", { name: "Continuar" }));
    expect(await screen.findByTestId("mfa-secret")).toHaveTextContent("JBSWY3DPEHPK3PXP");
    expect(await screen.findByRole("img", { name: /QR code/ })).toBeInTheDocument();
    acc.mfa.mockResolvedValue(ok({ enabled: true, enabled_at: "2026-10-07T12:00:00Z", required: false, pending: false, recovery_codes_left: 2 }));
    await userEvent.type(screen.getByLabelText("Código de 6 dígitos"), "123 456");
    await userEvent.click(screen.getByRole("button", { name: "Ativar" }));
    const codes = await screen.findByTestId("recovery-codes");
    expect(within(codes).getByText("aaaaa-bbbbb")).toBeInTheDocument();
    expect(acc.mfaEnroll).toHaveBeenCalledWith("Senha-Atual-123");
    expect(acc.mfaConfirm).toHaveBeenCalledWith("123456");
    expect(await screen.findByTestId("mfa-status")).toHaveTextContent("Ativa desde");
  });

  it("lists sessions (device, network) and ends another device", async () => {
    acc.revokeSession.mockResolvedValue(ok(null));
    renderWithClient(<AccountPage />);
    const rows = await screen.findAllByTestId("session-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Chrome · Windows");
    expect(rows[0]).toHaveTextContent("Esta sessão");
    expect(within(rows[0]).queryByRole("button")).toBeNull();          // the current one cannot be ended here
    expect(rows[1]).toHaveTextContent("rede 10.0.9.0/24");
    await userEvent.click(within(rows[1]).getByRole("button", { name: /Encerrar/ }));
    await waitFor(() => expect(acc.revokeSession).toHaveBeenCalledWith("s2"));
  });

  it("password change validates the policy before calling the server", async () => {
    acc.changePassword.mockResolvedValue(ok(null));
    renderWithClient(<AccountPage />);
    await screen.findByDisplayValue("Ana Souza");
    await userEvent.type(screen.getByLabelText("Senha atual", { selector: "#pw-current" }), "Senha-Atual-123");
    await userEvent.type(screen.getByLabelText("Nova senha"), "AnaSouza-nova-2026");
    await userEvent.type(screen.getByLabelText("Confirme a nova senha"), "AnaSouza-nova-2026");
    await userEvent.click(screen.getByRole("button", { name: "Trocar senha" }));
    expect(await screen.findByText(/não pode conter seu nome/)).toBeInTheDocument();
    expect(acc.changePassword).not.toHaveBeenCalled();
  });

  it("delete account needs the password and typing EXCLUIR", async () => {
    acc.deleteAccount.mockResolvedValue(ok(null));
    renderWithClient(<AccountPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Excluir conta…" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Excluir definitivamente" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Senha atual"), "Senha-Atual-123");
    await userEvent.type(within(dialog).getByLabelText(/para confirmar/), "EXCLUIR");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(acc.deleteAccount).toHaveBeenCalledWith("Senha-Atual-123"));
    await waitFor(() => expect(useAuthStore.getState().user).toBeNull());
  });

  it("errors are explicit, never a blank section", async () => {
    acc.sessions.mockRejectedValue(Object.assign(new Error("x"), { isAxiosError: true, response: { status: 500, data: {} } }));
    renderWithClient(<AccountPage />);
    const region = await screen.findByRole("region", { name: "Sessões ativas" });
    expect(await within(region).findByRole("alert")).toHaveTextContent("Não foi possível carregar os dados");
  });
});
