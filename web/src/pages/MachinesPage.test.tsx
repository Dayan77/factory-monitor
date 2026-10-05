import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError } from "axios";
import MachinesPage from "./MachinesPage";
import { linesApi, machinesApi } from "@/services/api";
import { applyMachineStatus } from "@/services/realtime";
import type { Machine } from "@/services/types";
import { line, machine } from "@/test/fixtures";
import { ok, pending, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  linesApi: { list: vi.fn() },
  machinesApi: { list: vi.fn() },
}));

const listLines = vi.mocked(linesApi.list);
const listMachines = vi.mocked(machinesApi.list);

describe("MachinesPage", () => {
  beforeEach(() => {
    listLines.mockResolvedValue(ok([line()]));
  });

  it("shows a loading skeleton while machines load (no fake data)", () => {
    listMachines.mockReturnValue(pending());
    renderWithClient(<MachinesPage />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByTestId("machine-card")).not.toBeInTheDocument();
  });

  it("shows an explicit error state with retry when the API fails", async () => {
    listMachines.mockRejectedValueOnce(new AxiosError("Network Error"));
    renderWithClient(<MachinesPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Não foi possível carregar as máquinas");
    expect(alert).toHaveTextContent("Servidor indisponível");
    expect(screen.queryByTestId("machine-card")).not.toBeInTheDocument();

    listMachines.mockResolvedValueOnce(ok([machine()]));
    await userEvent.click(within(alert).getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("Torno CNC-01")).toBeInTheDocument();
  });

  it("shows an empty state when the tenant has no machines", async () => {
    listMachines.mockResolvedValue(ok([]));
    renderWithClient(<MachinesPage />);
    expect(await screen.findByText("Nenhuma máquina cadastrada")).toBeInTheDocument();
  });

  it("renders status, line name and 'visto há' for each machine", async () => {
    const seen = new Date(Date.now() - 12_000).toISOString();
    listMachines.mockResolvedValue(ok([
      machine({ id: "m-1", name: "Torno CNC-01", status: "running", last_seen_at: seen }),
      machine({ id: "m-2", name: "Prensa HP-200", status: "unknown", last_seen_at: null }),
    ]));
    renderWithClient(<MachinesPage />);

    const cards = await screen.findAllByTestId("machine-card");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Operando")).toBeInTheDocument();
    expect(within(cards[0]).getByText(/visto há 1[2-4] s/)).toBeInTheDocument();
    expect(await within(cards[0]).findByText(/Linha 1 — Montagem/)).toBeInTheDocument();
    expect(within(cards[1]).getByText("Sem comunicação")).toBeInTheDocument();
    expect(within(cards[1]).getByText("nunca visto")).toBeInTheDocument();
  });

  it("works when the backend does not send last_seen_at yet", async () => {
    const legacy = machine();
    delete legacy.last_seen_at;
    listMachines.mockResolvedValue(ok([legacy]));
    renderWithClient(<MachinesPage />);
    expect(await screen.findByText("nunca visto")).toBeInTheDocument();
  });

  it("filters by status", async () => {
    listMachines.mockResolvedValue(ok([
      machine({ id: "m-1", name: "Torno CNC-01", status: "running" }),
      machine({ id: "m-2", name: "Forno F-01", status: "error" }),
    ]));
    renderWithClient(<MachinesPage />);
    await screen.findAllByTestId("machine-card");

    await userEvent.click(screen.getByRole("button", { name: "Falha" }));
    expect(screen.getAllByTestId("machine-card")).toHaveLength(1);
    expect(screen.getByText("Forno F-01")).toBeInTheDocument();
    expect(screen.getByText("1 de 2 máquinas")).toBeInTheDocument();
  });

  it("re-renders when a machine_status WebSocket update patches the cache", async () => {
    listMachines.mockResolvedValue(ok([machine({ id: "m-1", status: "running" })]));
    const { client } = renderWithClient(<MachinesPage />);
    await screen.findByText("Operando");

    client.setQueriesData<Machine[]>({ queryKey: ["machines"] }, (old) =>
      applyMachineStatus(old, { machine_id: "m-1", status: "unknown", last_seen_at: null }),
    );
    expect(await screen.findByText("Sem comunicação")).toBeInTheDocument();
  });
});
