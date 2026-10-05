import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { AxiosError } from "axios";
import DashboardPage from "./DashboardPage";
import { alertsApi, linesApi, machinesApi, tagsApi } from "@/services/api";
import { useRealtimeStore } from "@/store/realtime";
import { alertEvent, line, machine, tag } from "@/test/fixtures";
import { ok, pending, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  linesApi: { list: vi.fn() },
  machinesApi: { list: vi.fn() },
  alertsApi: { listEvents: vi.fn() },
  tagsApi: { list: vi.fn() },
}));

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.mocked(linesApi.list).mockResolvedValue(ok([line()]));
    vi.mocked(alertsApi.listEvents).mockResolvedValue(ok([]));
    vi.mocked(tagsApi.list).mockResolvedValue(ok([]));
  });
  afterEach(() => useRealtimeStore.getState().reset());

  it("never presents a fake OEE value — shows the Fase 2 empty state", async () => {
    vi.mocked(machinesApi.list).mockResolvedValue(ok([machine()]));
    renderWithClient(<DashboardPage />);
    expect(await screen.findByText("OEE disponível na Fase 2")).toBeInTheDocument();
    expect(screen.queryByText(/Últimas 24h/)).not.toBeInTheDocument();
    expect(screen.queryByText(/OEE Geral/)).not.toBeInTheDocument();
  });

  it("shows skeletons while loading", () => {
    vi.mocked(machinesApi.list).mockReturnValue(pending());
    renderWithClient(<DashboardPage />);
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  it("shows an error state when machines fail to load", async () => {
    vi.mocked(machinesApi.list).mockRejectedValue(new AxiosError("Network Error"));
    renderWithClient(<DashboardPage />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts[0]).toHaveTextContent("Não foi possível carregar as máquinas");
  });

  it("shows real counts and empty states with no data", async () => {
    vi.mocked(machinesApi.list).mockResolvedValue(ok([]));
    renderWithClient(<DashboardPage />);
    expect(await screen.findByText("0 / 0")).toBeInTheDocument();
    expect(screen.getAllByText("Nenhuma máquina cadastrada").length).toBeGreaterThan(0);
    expect(screen.getByText("Nenhum alerta ativo")).toBeInTheDocument();
  });

  it("lists active alerts, critical first", async () => {
    vi.mocked(machinesApi.list).mockResolvedValue(ok([machine()]));
    vi.mocked(alertsApi.listEvents).mockResolvedValue(ok([
      alertEvent({ id: "w", rule_name: "Aviso", severity: "warning", triggered_at: "2026-10-05T12:30:00Z" }),
      alertEvent({ id: "c", rule_name: "Crítico X", severity: "critical", triggered_at: "2026-10-05T12:00:00Z" }),
      alertEvent({ id: "r", rule_name: "Já resolvido", resolved_at: "2026-10-05T12:10:00Z" }),
    ]));
    renderWithClient(<DashboardPage />);
    const section = (await screen.findByRole("heading", { name: "Alertas ativos" })).closest("section");
    if (!section) throw new Error("section not found");
    const items = await within(section).findAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([expect.stringContaining("Crítico X"), expect.stringContaining("Aviso")]);
    expect(within(section).queryByText("Já resolvido")).not.toBeInTheDocument();
  });

  it("labels live readings with sensor and machine names, never the raw tag UUID", async () => {
    vi.mocked(machinesApi.list).mockResolvedValue(ok([machine({ id: "m-1", name: "Torno CNC-01" })]));
    vi.mocked(tagsApi.list).mockResolvedValue(ok([tag({ id: "6f1c-uuid", machine_id: "m-1", name: "Temperatura do fuso", unit: "°C" })]));
    useRealtimeStore.getState().pushReading({ tag_id: "6f1c-uuid", value: 71.25, unit: null, timestamp: "2026-10-05T12:00:00Z" });

    renderWithClient(<DashboardPage />);
    const section = (await screen.findByRole("heading", { name: "Leituras ao Vivo" })).closest("section");
    if (!section) throw new Error("section not found");
    expect(await within(section).findByText("Temperatura do fuso")).toBeInTheDocument();
    expect(within(section).getAllByText("Torno CNC-01").length).toBeGreaterThan(0);
    expect(within(section).getByText("°C")).toBeInTheDocument();
    expect(section).not.toHaveTextContent("6f1c-uuid");
  });
});
