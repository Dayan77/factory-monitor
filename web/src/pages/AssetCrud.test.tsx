import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, type AxiosResponse } from "axios";
import LinesPage from "./LinesPage";
import MachinesPage from "./MachinesPage";
import { linesApi, machinesApi, plantsApi } from "@/services/api";
import { useAuthStore } from "@/store/auth";
import { line, machine } from "@/test/fixtures";
import { user } from "@/test/maintenanceFixtures";
import { ok, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  linesApi: { list: vi.fn(), create: vi.fn(), update: vi.fn() },
  machinesApi: { list: vi.fn(), create: vi.fn(), update: vi.fn() },
  plantsApi: { tree: vi.fn() },
}));

const ADMIN = user({ id: "u-adm", role: "admin", roles: ["admin"] });
const OPERATOR = user({ id: "u-op", role: "operator", roles: ["operator"] });

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: ADMIN, loading: false, offline: false });
  vi.mocked(plantsApi.tree).mockResolvedValue(ok([]));
  vi.mocked(linesApi.list).mockResolvedValue(ok([line()]));
  vi.mocked(machinesApi.list).mockResolvedValue(ok([machine()]));
  vi.mocked(linesApi.create).mockResolvedValue(ok(line({ id: "line-2" })) as AxiosResponse<never>);
  vi.mocked(linesApi.update).mockResolvedValue(ok(line()) as AxiosResponse<never>);
  vi.mocked(machinesApi.create).mockResolvedValue(ok(machine({ id: "m-2" })) as AxiosResponse<never>);
  vi.mocked(machinesApi.update).mockResolvedValue(ok(machine()) as AxiosResponse<never>);
});

describe("Lines: create / edit", () => {
  it("admin creates a line from the form", async () => {
    renderWithClient(<LinesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Nova linha" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Criar linha" }));
    expect(within(dialog).getByText("Informe o nome da linha.")).toBeInTheDocument();
    expect(linesApi.create).not.toHaveBeenCalled();

    await userEvent.type(within(dialog).getByLabelText("Nome da linha"), "  Linha LED Driver ");
    await userEvent.type(within(dialog).getByLabelText(/^Local/), "Galpão 2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Criar linha" }));
    await waitFor(() => expect(linesApi.create).toHaveBeenCalledWith({ name: "Linha LED Driver", location: "Galpão 2", description: null }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(vi.mocked(linesApi.list).mock.calls.length).toBeGreaterThan(1); // list refreshed
  });

  it("edits an existing line and can deactivate it", async () => {
    renderWithClient(<LinesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Editar Linha 1 — Montagem" }));
    const dialog = screen.getByRole("dialog");
    const name = within(dialog).getByLabelText("Nome da linha");
    expect(name).toHaveValue("Linha 1 — Montagem");
    await userEvent.clear(name);
    await userEvent.type(name, "Linha 1");
    await userEvent.click(within(dialog).getByLabelText("Linha ativa"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Salvar" }));
    await waitFor(() => expect(linesApi.update).toHaveBeenCalledWith("line-1",
      { name: "Linha 1", location: "Pavilhão A", description: null, is_active: false }));
  });

  it("shows the server error and keeps the form open", async () => {
    vi.mocked(linesApi.create).mockRejectedValueOnce(new AxiosError("x", "400", undefined, undefined,
      { data: { detail: "nome já existe" }, status: 400 } as AxiosResponse));
    renderWithClient(<LinesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Nova linha" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nome da linha"), "Linha X");
    await userEvent.click(within(dialog).getByRole("button", { name: "Criar linha" }));
    expect(await within(dialog).findByText("nome já existe")).toBeInTheDocument();
  });

  it("non-admins see no create/edit actions", async () => {
    useAuthStore.setState({ user: OPERATOR });
    renderWithClient(<LinesPage />);
    expect(await screen.findByText("Linha 1 — Montagem")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nova linha" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar/ })).not.toBeInTheDocument();
  });

  it("'+ Máquina' on a line opens the machine form with that line preselected", async () => {
    renderWithClient(<LinesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Adicionar máquina à Linha 1 — Montagem" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Linha")).toHaveValue("line-1");
  });
});

describe("Machines: create / edit", () => {
  it("admin creates a machine with validated cycle time and micro-stop threshold", async () => {
    renderWithClient(<MachinesPage />);
    await screen.findByTestId("machine-card");
    await userEvent.click(screen.getByRole("button", { name: "Nova máquina" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nome da máquina"), "M1 Hipot");
    await userEvent.type(within(dialog).getByLabelText(/Tempo de ciclo ideal/), "0");
    const micro = within(dialog).getByLabelText(/Limite de micro-parada/);
    await userEvent.clear(micro);
    await userEvent.type(micro, "10");
    await userEvent.click(within(dialog).getByRole("button", { name: "Criar máquina" }));
    expect(within(dialog).getByText("Tempo de ciclo deve ser maior que zero.")).toBeInTheDocument();
    expect(within(dialog).getByText("Entre 30 e 300 segundos.")).toBeInTheDocument();
    expect(machinesApi.create).not.toHaveBeenCalled();

    const cycle = within(dialog).getByLabelText(/Tempo de ciclo ideal/);
    await userEvent.clear(cycle);
    await userEvent.type(cycle, "12,5");
    await userEvent.clear(micro);
    await userEvent.type(micro, "60");
    await userEvent.click(within(dialog).getByRole("button", { name: "Criar máquina" }));
    await waitFor(() => expect(machinesApi.create).toHaveBeenCalledWith({
      line_id: "line-1", name: "M1 Hipot", model: null, serial_number: null,
      ideal_cycle_time_s: 12.5, micro_stop_threshold_s: 60,
    }));
  });

  it("edits a machine from its card", async () => {
    renderWithClient(<MachinesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Editar Torno CNC-01" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Modelo")).toHaveValue("Mazak QT-200");
    await userEvent.click(within(dialog).getByRole("button", { name: "Salvar" }));
    await waitFor(() => expect(machinesApi.update).toHaveBeenCalledWith("m-1", expect.objectContaining({ name: "Torno CNC-01", line_id: "line-1" })));
  });

  it("asks for a line first when the tenant has none", async () => {
    vi.mocked(linesApi.list).mockResolvedValue(ok([]));
    vi.mocked(machinesApi.list).mockResolvedValue(ok([]));
    renderWithClient(<MachinesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Nova máquina" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Cadastre uma linha de produção antes de cadastrar máquinas.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Criar máquina" })).toBeDisabled();
  });

  it("non-admins cannot create machines", async () => {
    useAuthStore.setState({ user: OPERATOR });
    renderWithClient(<MachinesPage />);
    await screen.findByTestId("machine-card");
    expect(screen.queryByRole("button", { name: "Nova máquina" })).not.toBeInTheDocument();
  });
});
