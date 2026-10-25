import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LinePage from "./LinePage";
import { plantsApi, usageApi } from "@/services/api";
import { lineProduction, plantTree } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  usageApi: { lineProduction: vi.fn() },
}));
const tree = vi.mocked(plantsApi.tree);
const production = vi.mocked(usageApi.lineProduction);

describe("LinePage", () => {
  beforeEach(() => {
    tree.mockResolvedValue(ok(plantTree()));
    production.mockResolvedValue(ok(lineProduction()));
  });

  it("renders breadcrumbs, production stats, cadence and machine tiles", async () => {
    renderRoute(<LinePage />, "/factory/lines/line-1", "/factory/lines/:lineId");
    expect(await screen.findByRole("heading", { name: "Linha 1 — Montagem" })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Navegação estrutural" });
    expect(crumbs).toHaveTextContent("Linha 1 — Montagem");
    expect(crumbs).toHaveTextContent("Planta Manaus");
    expect(await screen.findByText("Cadência média")).toBeInTheDocument();
    expect(screen.getByText("290")).toBeInTheDocument(); // good
    expect(screen.getByText("100,0")).toBeInTheDocument(); // parts/h
    expect(screen.getByText("3,3 % do total")).toBeInTheDocument();
    const tiles = screen.getAllByTestId("machine-tile");
    expect(tiles).toHaveLength(2);
    expect(tiles[0]).toHaveAttribute("href", "/factory/machines/m-1?period=today");
    expect(production).toHaveBeenCalledWith("line-1", expect.objectContaining({ start: expect.any(String), stop: expect.any(String) }));
  });

  it("changing the range refetches production for that window", async () => {
    const user = userEvent.setup();
    renderRoute(<LinePage />, "/factory/lines/line-1", "/factory/lines/:lineId");
    await screen.findByText("Cadência média");
    await user.click(screen.getByRole("tab", { name: "8 h" }));
    const calls = production.mock.calls;
    const last = calls[calls.length - 1][1]!;
    const hours = (Date.parse(last.stop!) - Date.parse(last.start!)) / 3600_000;
    expect(Math.round(hours)).toBe(8);
  });

  it("explains when the line has no output machine (no fake numbers)", async () => {
    production.mockResolvedValue(ok(lineProduction({ reason: "no_output_machine", good: 0, scrap: 0, total: 0, cadence_per_hour: null, points: [] })));
    renderRoute(<LinePage />, "/factory/lines/line-1", "/factory/lines/:lineId");
    expect(await screen.findByText("Produção indisponível")).toBeInTheDocument();
    expect(screen.queryByText("Cadência média")).not.toBeInTheDocument();
  });

  it("shows not-found for an unknown line", async () => {
    renderRoute(<LinePage />, "/factory/lines/nope", "/factory/lines/:lineId");
    expect(await screen.findByText("Linha não encontrada")).toBeInTheDocument();
  });
});
