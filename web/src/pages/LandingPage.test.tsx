import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, AxiosHeaders } from "axios";
import LandingPage from "./LandingPage";
import { publicApi } from "@/services/api";
import { ok, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({ publicApi: { createLead: vi.fn() } }));
const createLead = vi.mocked(publicApi.createLead);

async function fill(user: ReturnType<typeof userEvent.setup>) {
  const form = screen.getByRole("form", { name: "Formulário de contato" });
  await user.type(within(form).getByLabelText(/Seu nome/), "Maria Souza");
  await user.type(within(form).getByLabelText(/E-mail/), "maria@empresa.com");
  await user.type(within(form).getByLabelText("Empresa"), "Empresa X");
  await user.type(within(form).getByLabelText("Telefone / WhatsApp"), "11 90000-0000");
  await user.type(within(form).getByLabelText(/Mensagem/), "Quero uma demo");
  return form;
}

function httpError(status: number, detail?: unknown) {
  return new AxiosError("err", "ERR", undefined, undefined, {
    status, statusText: "", headers: {}, config: { headers: new AxiosHeaders() }, data: detail ? { detail } : {},
  });
}

describe("LandingPage", () => {
  it("does not present fictional customer cases as real results", () => {
    renderWithClient(<LandingPage />);
    for (const name of ["Metalúrgica Fonseca", "Plásticos Norte", "Alimentos BomDia", "Resultados reais"]) {
      expect(screen.queryByText(name)).not.toBeInTheDocument();
    }
    expect(screen.queryByText("50+")).not.toBeInTheDocument();
    expect(screen.getByText("Ilustração — dados fictícios")).toBeInTheDocument();
  });

  it("posts the lead with LGPD consent and an empty honeypot, then shows success", async () => {
    createLead.mockResolvedValue(ok({ status: "accepted" }));
    const user = userEvent.setup();
    renderWithClient(<LandingPage />);
    const form = await fill(user);
    await user.click(within(form).getByRole("checkbox", { name: /LGPD/ }));
    await user.click(within(form).getByRole("button", { name: "Enviar mensagem" }));
    expect(createLead).toHaveBeenCalledWith({
      name: "Maria Souza", email: "maria@empresa.com", company: "Empresa X", phone: "11 90000-0000",
      message: "Quero uma demo", consent: true, website: "", source: "app-landing",
    });
    expect(await screen.findByText("Mensagem enviada!")).toBeInTheDocument();
  });

  it("the honeypot is hidden from people and keyboard", () => {
    renderWithClient(<LandingPage />);
    const hp = document.getElementById("lead-website") as HTMLInputElement;
    expect(hp).toHaveAttribute("tabindex", "-1");
    expect(hp.closest("[aria-hidden='true']")).not.toBeNull();
  });

  it("requires the consent checkbox", async () => {
    const user = userEvent.setup();
    renderWithClient(<LandingPage />);
    const form = await fill(user);
    const consent = within(form).getByRole("checkbox", { name: /LGPD/ });
    expect(consent).toBeRequired();
    expect(createLead).not.toHaveBeenCalled();
  });

  it.each([
    [httpError(429), "Recebemos muitas mensagens"],
    [httpError(422, [{ msg: "value is not a valid email address" }]), "value is not a valid email address"],
    [new AxiosError("Network Error"), "Sem conexão com o servidor"],
    [httpError(500), "Não foi possível enviar agora"],
  ])("shows an explicit error and keeps the form (%#)", async (err, text) => {
    createLead.mockRejectedValue(err);
    const user = userEvent.setup();
    renderWithClient(<LandingPage />);
    const form = await fill(user);
    await user.click(within(form).getByRole("checkbox", { name: /LGPD/ }));
    await user.click(within(form).getByRole("button", { name: "Enviar mensagem" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(text);
    expect(within(form).getByLabelText(/Seu nome/)).toHaveValue("Maria Souza");
  });
});
