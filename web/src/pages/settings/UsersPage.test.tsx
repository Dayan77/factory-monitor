import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ok, renderWithClient } from "@/test/utils";

const users = vi.hoisted(() => ({ list: vi.fn(), invitations: vi.fn(), invite: vi.fn(), revokeInvitation: vi.fn() }));
vi.mock("@/services/accountApi", async (orig) => ({ ...(await orig<typeof import("@/services/accountApi")>()), usersApi: users }));
vi.mock("@/hooks/useQueries", () => ({ useLines: () => ({ isPending: false, isError: false, data: [{ id: "l1", name: "Linha de Montagem" }] }) }));

import UsersPage from "./UsersPage";

beforeEach(() => {
  Object.values(users).forEach((f) => f.mockReset());
  users.list.mockResolvedValue(ok([{ id: "u1", name: "Ana", email: "ana@acme.com", role: "admin", roles: ["admin"], is_active: true }]));
  users.invitations.mockResolvedValue(ok([
    { id: "i1", email: "joao@acme.com", name: null, role: "technician", roles: ["technician"], line_ids: [], state: "pending", invited_by: "u1", user_id: null, created_at: "2026-10-06T12:00:00Z", expires_at: "2026-10-13T12:00:00Z", accepted_at: null },
  ]));
});

describe("Usuários e convites (admin)", () => {
  it("invites with roles and lines (operator) and shows the one-time link", async () => {
    users.invite.mockResolvedValue(ok({ id: "i2", email: "op@acme.com", state: "pending", role: "operator", roles: ["operator"], line_ids: ["l1"], invite_url: "http://app/convite?token=abc" }));
    renderWithClient(<UsersPage />);
    await userEvent.type(screen.getByLabelText("E-mail"), "op@acme.com");
    await userEvent.click(screen.getByRole("checkbox", { name: "Linha de Montagem" }));
    await userEvent.click(screen.getByRole("button", { name: "Enviar convite" }));
    await waitFor(() => expect(users.invite).toHaveBeenCalledWith({ email: "op@acme.com", name: undefined, role: "operator", roles: ["operator"], line_ids: ["l1"] }));
    expect(await screen.findByTestId("invite-link")).toHaveTextContent("http://app/convite?token=abc");
  });

  it("primary role is the most privileged picked; no role is refused locally", async () => {
    users.invite.mockResolvedValue(ok({ id: "i3", email: "g@acme.com", state: "pending", role: "manager", roles: [], line_ids: [] }));
    renderWithClient(<UsersPage />);
    await userEvent.type(screen.getByLabelText("E-mail"), "g@acme.com");
    await userEvent.click(screen.getByRole("checkbox", { name: "Operador" }));       // untick the default
    await userEvent.click(screen.getByRole("button", { name: "Enviar convite" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escolha ao menos um papel");
    await userEvent.click(screen.getByRole("checkbox", { name: "Gestor" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Técnico de manutenção" }));
    await userEvent.click(screen.getByRole("button", { name: "Enviar convite" }));
    await waitFor(() => expect(users.invite).toHaveBeenCalledWith(expect.objectContaining({ role: "manager", roles: ["manager", "technician"], line_ids: [] })));
  });

  it("lists invitations with state and revokes a pending one", async () => {
    users.revokeInvitation.mockResolvedValue(ok(null));
    renderWithClient(<UsersPage />);
    const item = (await screen.findByText("joao@acme.com")).closest("li")!;
    expect(within(item).getByText("Pendente")).toBeInTheDocument();
    await userEvent.click(within(item).getByRole("button", { name: /Revogar/ }));
    await waitFor(() => expect(users.revokeInvitation).toHaveBeenCalledWith("i1"));
    expect(await screen.findByRole("cell", { name: "ana@acme.com" })).toBeInTheDocument();
  });
});
