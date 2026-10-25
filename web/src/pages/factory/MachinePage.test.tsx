import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MachinePage from "./MachinePage";
import { componentsApi, plantsApi, positionsApi, usageApi } from "@/services/api";
import { applyUsageToMachineUsage } from "@/services/realtime";
import { queryKeys } from "@/hooks/useAssets";
import type { MachineUsage } from "@/services/types";
import { component, machineUsage, plantTree, positionDetail } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  usageApi: { machine: vi.fn(), history: vi.fn() },
  positionsApi: { get: vi.fn(), install: vi.fn() },
  componentsApi: { list: vi.fn(), create: vi.fn(), remove: vi.fn() },
}));

const PATH = "/factory/machines/:machineId";

describe("MachinePage", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(usageApi.machine).mockResolvedValue(ok(machineUsage()));
    vi.mocked(usageApi.history).mockResolvedValue(ok({ points: [{ t: "2026-10-05T10:00:00Z", value: 12 }] }));
    vi.mocked(positionsApi.get).mockResolvedValue(ok(positionDetail()));
    vi.mocked(componentsApi.list).mockResolvedValue(ok([component()]));
  });

  it("shows breadcrumbs, status and today's production", async () => {
    renderRoute(<MachinePage />, "/factory/machines/m-1", PATH);
    expect(await screen.findByRole("heading", { name: "Prensa P-01" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Navegação estrutural" })).toHaveTextContent("Linha 1 — Montagem");
    const prod = screen.getByRole("region", { name: "Produção de hoje" });
    expect(await within(prod).findByText("980")).toBeInTheDocument();
    expect(within(prod).getByText("2,0 % do total")).toBeInTheDocument();
    expect(within(prod).getByText("Componentes acima de 90%")).toBeInTheDocument();
    expect(within(prod).getByText("Planejar a troca")).toBeInTheDocument();
  });

  it("lists positions most-worn first with life bars in the right band", async () => {
    renderRoute(<MachinePage />, "/factory/machines/m-1", PATH);
    const table = await screen.findByRole("table", { name: "Posições e componentes da máquina" });
    const rows = within(table).getAllByTestId("position-row");
    expect(rows.map((r) => within(r).getAllByText(/./)[0].textContent)).toEqual(["Motor principal", "Cilindro de fixação", "Garra"]);
    expect(rows[0].querySelector("[data-band]")).toHaveAttribute("data-band", "danger");
    expect(rows[1].querySelector("[data-band]")).toHaveAttribute("data-band", "warn");
    expect(rows[2].querySelector("[data-band]")).toHaveAttribute("data-band", "ok");
    expect(within(rows[1]).getByText("3.600.000")).toBeInTheDocument();
  });

  it("applies live usage increments to the cached usage", async () => {
    const { client } = renderRoute(<MachinePage />, "/factory/machines/m-1", PATH);
    const table = await screen.findByRole("table", { name: "Posições e componentes da máquina" });
    act(() => {
      client.setQueryData<MachineUsage>(queryKeys.machineUsage("m-1"), (old) =>
        applyUsageToMachineUsage(old, "m-1", { type: "usage", position_id: "pos-1", machine_id: "m-1", measure: "cycles", delta: 2, total: 3_600_002 }));
    });
    expect(await within(table).findByText("3.600.002")).toBeInTheDocument();
  });

  it("opens the position drawer from a row (URL ?pos=) and closes it", async () => {
    const user = userEvent.setup();
    renderRoute(<MachinePage />, "/factory/machines/m-1", PATH);
    const table = await screen.findByRole("table", { name: "Posições e componentes da máquina" });
    await user.click(within(table).getByRole("row", { name: "Abrir posição Cilindro de fixação" }));
    const dialog = await screen.findByRole("dialog");
    expect((await within(dialog).findAllByText("Festo DSBC-50")).length).toBeGreaterThan(0);
    expect(screen.getByTestId("location")).toHaveTextContent("?pos=pos-1");
    expect(positionsApi.get).toHaveBeenCalledWith("pos-1");
    await user.click(within(dialog).getByRole("button", { name: "Fechar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("usage history: machine scope by default, switching to a position measure", async () => {
    const user = userEvent.setup();
    renderRoute(<MachinePage />, "/factory/machines/m-1", PATH);
    await screen.findByRole("table", { name: "Posições e componentes da máquina" });
    expect(usageApi.history).toHaveBeenCalledWith({ machine_id: "m-1" }, "good", expect.any(Object));
    await user.selectOptions(screen.getByLabelText("Escopo"), "pos-2");
    await user.click(screen.getByRole("tab", { name: "Horas" }));
    expect(usageApi.history).toHaveBeenLastCalledWith({ position_id: "pos-2" }, "hours", expect.any(Object));
  });

  it("shows not-found for unknown machines", async () => {
    renderRoute(<MachinePage />, "/factory/machines/zzz", PATH);
    expect(await screen.findByText("Máquina não encontrada")).toBeInTheDocument();
  });
});
