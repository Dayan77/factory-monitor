import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PlantMapPage from "./PlantMapPage";
import { plantsApi } from "@/services/api";
import { layoutApi } from "@/services/sprint6Api";
import type { AutoMapView, MachineVisual } from "@/services/sprint6Types";
import { useRealtimeStore } from "@/store/realtime";
import { plantTree } from "@/test/fixtures";
import { ok, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  downtimeApi: { list: vi.fn(() => Promise.resolve({ data: [] })) },
  reasonCodesApi: { list: vi.fn(() => Promise.resolve({ data: [] })) },
}));
vi.mock("@/services/sprint6Api", () => ({
  layoutApi: { list: vi.fn(), auto: vi.fn(), published: vi.fn(), image: vi.fn() },
}));

function mv(id: string, name: string, over: Partial<MachineVisual>): MachineVisual {
  return {
    machine_id: id, name, line_id: "line-2", line_name: "Linha 2 — Envase", state: "running", icon: "play", text: "rodando",
    since: null, detail: {}, badges: [], status: "running", last_seen_at: new Date().toISOString(), kpis: null, ...over,
  };
}

const auto: AutoMapView = {
  kind: "auto", banner: "Layout automático — envie a planta baixa para ver o mapa real", plant_id: "plant-1", plant_name: "Planta Manaus",
  generated_at: new Date().toISOString(),
  lanes: [{ line_id: "line-2", name: "Linha 2 — Envase", machine_ids: ["m-3", "m-4", "m-5"], y: 0.5 }],
  markers: [
    { kind: "line_label", line_id: "line-2", machine_id: null, label: null, x: 0.04, y: 0.5, rotation_deg: 0, size: "m" },
    { kind: "machine", machine_id: "m-3", line_id: null, label: null, x: 0.2, y: 0.5, rotation_deg: 0, size: "m" },
    { kind: "machine", machine_id: "m-4", line_id: null, label: null, x: 0.5, y: 0.5, rotation_deg: 0, size: "m" },
    { kind: "machine", machine_id: "m-5", line_id: null, label: null, x: 0.8, y: 0.5, rotation_deg: 0, size: "m" },
  ],
  machines: [
    mv("m-3", "Envasadora E-01", { state: "failure", icon: "wrench", text: "falha — EQP-01", status: "error", since: new Date(Date.now() - 8 * 60_000).toISOString() }),
    mv("m-4", "Tampadora T-01", { state: "stopped", icon: "pause", text: "parada — sem motivo", status: "stopped", detail: { downtime_id: "d1" }, since: new Date(Date.now() - 6 * 60_000).toISOString() }),
    mv("m-5", "Rotuladora R-01", { kpis: { oee: 0.812, cadence_per_hour: 120, good: 10, scrap: 0 } }),
  ],
  lines: [{ line_id: "line-2", name: "Linha 2 — Envase", output_machine_id: "m-5", output_state: "running", output_text: "rodando", kpis: { oee: 0.75, cadence_per_hour: 120, good: 10, scrap: 0 } }],
  shift: { name: "Turno A", start: "", end: "", productive_day: "2026-10-07" }, unplaced_machine_ids: [],
};

afterEach(() => { vi.useRealTimers(); useRealtimeStore.getState().reset(); });

describe("PlantMapPage (sprint6 §1)", () => {
  it("shows the automatic layout, identified as such, with colour + icon + text on every marker", async () => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(layoutApi.list).mockResolvedValue(ok([]));
    vi.mocked(layoutApi.auto).mockResolvedValue(ok(auto));
    renderWithClient(<PlantMapPage />);
    expect(await screen.findByTestId("auto-layout-banner")).toHaveTextContent("Layout automático");
    const markers = await screen.findAllByTestId("map-marker");
    expect(markers.map((m) => m.dataset.state)).toEqual(["failure", "stopped", "running"]);
    expect(markers[0]).toHaveAccessibleName(/Envasadora E-01: falha — EQP-01 · há 8 min/);
    expect(markers[1]).toHaveAccessibleName(/parada — sem motivo · há 6 min/);
    expect(within(markers[2]).getByText(/OEE 81,2/)).toBeInTheDocument();
    for (const m of markers) expect(m.querySelector("svg[data-icon]")).not.toBeNull();
    // most important problem first in the side list
    const problems = screen.getByTestId("map-problems");
    expect(within(problems).getAllByRole("button")[0]).toHaveTextContent("Envasadora E-01");
    expect(layoutApi.published).not.toHaveBeenCalled();
  });

  it("opens the summary of a machine with the drill-down link keeping the period", async () => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(layoutApi.list).mockResolvedValue(ok([]));
    vi.mocked(layoutApi.auto).mockResolvedValue(ok(auto));
    renderWithClient(<PlantMapPage />);
    const [first] = await screen.findAllByTestId("map-marker");
    await userEvent.click(first);
    const link = await screen.findByTestId("open-machine");
    expect(link).toHaveAttribute("href", "/factory/machines/m-3?period=today");
  });

  it("shows 'ao vivo pausado' when the socket is down for 30 s, never stale state as live", async () => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(layoutApi.list).mockResolvedValue(ok([]));
    vi.mocked(layoutApi.auto).mockResolvedValue(ok(auto));
    useRealtimeStore.setState({ status: "disconnected", downSince: Date.now() - 31_000, lastMessageAt: Date.now() - 31_000 });
    renderWithClient(<PlantMapPage />);
    await screen.findAllByTestId("map-marker");
    await act(async () => { await new Promise((r) => setTimeout(r, 1100)); });
    expect(screen.getByTestId("live-paused")).toHaveTextContent(/Ao vivo pausado — última atualização \d\d:\d\d:\d\d/);
  });
});
