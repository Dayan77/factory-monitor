import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeQueryClient, ok } from "@/test/utils";

const flow = vi.hoisted(() => ({
  login: vi.fn(), mfaVerify: vi.fn(), requestReset: vi.fn(), confirmReset: vi.fn(), invitation: vi.fn(), acceptInvitation: vi.fn(),
}));
const me = vi.hoisted(() => vi.fn());
vi.mock("@/services/accountApi", async (orig) => ({ ...(await orig<typeof import("@/services/accountApi")>()), authFlowApi: flow }));
vi.mock("@/services/api", async (orig) => ({ ...(await orig<typeof import("@/services/api")>()), authApi: { me: () => me(), login: vi.fn(), register: vi.fn() } }));

import LoginPage from "@/pages/LoginPage";
import ForgotPasswordPage from "./ForgotPasswordPage";
import ResetPasswordPage from "./ResetPasswordPage";
import InvitePage from "./InvitePage";
import { useAuthStore } from "@/store/auth";
import { SESSION_EXPIRED_KEY } from "@/services/api";

const USER = { id: "u1", name: "Ana Souza", email: "ana@acme.com", role: "admin", roles: ["admin"], tenant_id: "t1" };

function at(path: string, el: JSX.Element, routePath = path.split("?")[0]) {
  window.history.replaceState(null, "", path);
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path={routePath} element={el} />
          <Route path="/dashboard" element={<p>raio-x</p>} />
          <Route path="/maintenance/work-orders" element={<p>ordens</p>} />
          <Route path="/login" element={<p>login page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function axiosErr(status: number, detail: string) {
  return Object.assign(new Error(detail), { isAxiosError: true, response: { status, data: { detail } } });
}

beforeEach(() => {
  Object.values(flow).forEach((f) => f.mockReset());
  me.mockReset();
  localStorage.clear();
  sessionStorage.clear();
  useAuthStore.setState({ user: null, loading: false, offline: false });
});

describe("login with MFA", () => {
  it("asks for the authenticator code after the password, then signs in and goes back to ?next", async () => {
    flow.login.mockResolvedValue(ok({ mfa_required: true, mfa_token: "mfa-1", methods: ["totp", "recovery_code"] }));
    flow.mfaVerify.mockResolvedValue(ok({ access_token: "a1", refresh_token: "r1" }));
    me.mockResolvedValue(ok(USER));
    at("/login?next=%2Fmaintenance%2Fwork-orders", <LoginPage />);
    await userEvent.type(screen.getByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "Senha-Longa-123");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByTestId("mfa-step")).toBeInTheDocument();
    expect(localStorage.getItem("access_token")).toBeNull();          // no session before the second factor
    expect(screen.getByLabelText("Código do autenticador")).toHaveFocus();
    await userEvent.type(screen.getByLabelText("Código do autenticador"), "123 456");
    await userEvent.click(screen.getByRole("button", { name: "Verificar" }));
    expect(await screen.findByText("ordens")).toBeInTheDocument();
    expect(flow.mfaVerify).toHaveBeenCalledWith("mfa-1", { code: "123456" });
    expect(localStorage.getItem("access_token")).toBe("a1");
  });

  it("accepts a recovery code instead", async () => {
    flow.login.mockResolvedValue(ok({ mfa_required: true, mfa_token: "mfa-2" }));
    flow.mfaVerify.mockResolvedValue(ok({ access_token: "a2", refresh_token: "r2" }));
    me.mockResolvedValue(ok(USER));
    at("/login", <LoginPage />);
    await userEvent.type(screen.getByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "Senha-Longa-123");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    await userEvent.click(await screen.findByRole("button", { name: "Usar um código de recuperação" }));
    await userEvent.type(screen.getByLabelText("Código de recuperação"), "abcde-fghjk");
    await userEvent.click(screen.getByRole("button", { name: "Verificar" }));
    expect(await screen.findByText("raio-x")).toBeInTheDocument();
    expect(flow.mfaVerify).toHaveBeenCalledWith("mfa-2", { recovery_code: "abcdefghjk" });
  });

  it("an expired MFA challenge sends the user back to the password step with the server message", async () => {
    flow.login.mockResolvedValue(ok({ mfa_required: true, mfa_token: "mfa-3" }));
    flow.mfaVerify.mockRejectedValue(axiosErr(401, "Verificação expirada. Entre novamente."));
    at("/login", <LoginPage />);
    await userEvent.type(screen.getByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "Senha-Longa-123");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    await userEvent.type(await screen.findByLabelText("Código do autenticador"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Verificar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Verificação expirada");
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("explains a session expiry and wrong credentials", async () => {
    sessionStorage.setItem(SESSION_EXPIRED_KEY, "1");
    flow.login.mockRejectedValue(axiosErr(401, "Credenciais inválidas."));
    at("/login", <LoginPage />);
    expect(screen.getByTestId("session-expired")).toHaveTextContent("Sua sessão expirou");
    expect(sessionStorage.getItem(SESSION_EXPIRED_KEY)).toBeNull();
    await userEvent.type(screen.getByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "errada-errada");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("E-mail ou senha incorretos.");
  });
});

describe("password reset", () => {
  it("request: the same confirmation whatever the e-mail (no account enumeration)", async () => {
    flow.requestReset.mockResolvedValue(ok({ status: "accepted" }));
    at("/esqueci-senha", <ForgotPasswordPage />);
    await userEvent.type(screen.getByLabelText("E-mail da sua conta"), "quem@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Enviar link" }));
    expect(await screen.findByTestId("reset-sent")).toHaveTextContent("Se houver uma conta com quem@acme.com");
  });

  it("confirm: reads the token, removes it from the address bar, checks the policy and returns to the login", async () => {
    flow.confirmReset.mockResolvedValue(ok(null));
    at("/redefinir-senha?token=tok-123", <ResetPasswordPage />);
    expect(window.location.search).toBe("");                          // token no longer in the URL / history
    await userEvent.type(screen.getByLabelText("Nova senha"), "curta");
    await userEvent.type(screen.getByLabelText("Confirme a nova senha"), "curta");
    await userEvent.click(screen.getByRole("button", { name: "Salvar nova senha" }));
    expect(screen.getByRole("alert")).toHaveTextContent("mínimo 12 caracteres");
    expect(flow.confirmReset).not.toHaveBeenCalled();
    await userEvent.clear(screen.getByLabelText("Nova senha"));
    await userEvent.clear(screen.getByLabelText("Confirme a nova senha"));
    await userEvent.type(screen.getByLabelText("Nova senha"), "Bancada-Verde-Seguro-42");
    await userEvent.type(screen.getByLabelText("Confirme a nova senha"), "Bancada-Verde-Seguro-42");
    await userEvent.click(screen.getByRole("button", { name: "Salvar nova senha" }));
    await waitFor(() => expect(flow.confirmReset).toHaveBeenCalledWith("tok-123", "Bancada-Verde-Seguro-42"));
    expect(await screen.findByText("login page")).toBeInTheDocument();
  });

  it("confirm: an expired / used link offers a new one", async () => {
    flow.confirmReset.mockRejectedValue(axiosErr(400, "Link de redefinição inválido ou expirado."));
    at("/redefinir-senha?token=velho", <ResetPasswordPage />);
    fireEvent.change(screen.getByLabelText("Nova senha"), { target: { value: "Bancada-Verde-Seguro-42" } });
    fireEvent.change(screen.getByLabelText("Confirme a nova senha"), { target: { value: "Bancada-Verde-Seguro-42" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Salvar nova senha" })); });
    expect(await screen.findByTestId("reset-invalid")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pedir um novo link" })).toHaveAttribute("href", "/esqueci-senha");
  });
});

describe("invitation", () => {
  it("shows the invitation, refuses a password with the company name and signs in after accepting", async () => {
    flow.invitation.mockResolvedValue(ok({ email: "joao@acme.com", name: "João", tenant_name: "Metalúrgica Acme", role: "technician", expires_at: "2026-10-14T12:00:00Z" }));
    flow.acceptInvitation.mockResolvedValue(ok({ access_token: "a9", refresh_token: "r9" }));
    me.mockResolvedValue(ok({ ...USER, email: "joao@acme.com", name: "João" }));
    at("/convite?token=inv-9", <InvitePage />);
    expect(await screen.findByText("joao@acme.com")).toBeInTheDocument();
    expect(screen.getByText("Técnico de manutenção")).toBeInTheDocument();
    expect(window.location.search).toBe("");
    await userEvent.type(screen.getByLabelText("Crie uma senha"), "Bancada-Acme-forte-99");
    await userEvent.type(screen.getByLabelText("Confirme a senha"), "Bancada-Acme-forte-99");
    await userEvent.click(screen.getByRole("button", { name: "Criar conta e entrar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("nome da empresa");
    await userEvent.clear(screen.getByLabelText("Crie uma senha"));
    await userEvent.clear(screen.getByLabelText("Confirme a senha"));
    await userEvent.type(screen.getByLabelText("Crie uma senha"), "Bancada-Verde-Seguro-42");
    await userEvent.type(screen.getByLabelText("Confirme a senha"), "Bancada-Verde-Seguro-42");
    await userEvent.click(screen.getByRole("button", { name: "Criar conta e entrar" }));
    expect(await screen.findByText("raio-x")).toBeInTheDocument();
    expect(flow.acceptInvitation).toHaveBeenCalledWith("inv-9", "Bancada-Verde-Seguro-42", "João");
    expect(localStorage.getItem("access_token")).toBe("a9");
  });

  it("an invalid / expired invitation says so", async () => {
    flow.invitation.mockRejectedValue(axiosErr(404, "Convite inválido ou expirado."));
    at("/convite?token=x", <InvitePage />);
    expect(await screen.findByTestId("invite-invalid")).toHaveTextContent("Convite inválido ou expirado.");
  });
});
