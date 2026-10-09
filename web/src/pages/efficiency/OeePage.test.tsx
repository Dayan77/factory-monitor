import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OeePage from "./OeePage";
import { downtimeApi, efficiencyApi, plantsApi, reasonCodesApi, shiftsApi } from "@/services/api";
import { downtime, oeeResult, paretoRows, plantTree, reasonCodes } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  shiftsApi: { list: vi.fn() },
  efficiencyApi: { oee: vi.fn(), pareto: vi.fn(), reliability: vi.fn() },
  downtimeApi: { list: vi.fn(), noData: vi.fn() },
  reasonCodesApi: { list: vi.fn() },
}));

const render = (path: string) => renderRoute(<OeePage />, path, "/oee");

describe("OeePage", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(shiftsApi.list).mockResolvedValue(ok([]));
    vi.mocked(efficiencyApi.oee).mockResolvedValue(ok(oeeResult()));
    vi.mocked(efficiencyApi.pareto).mockResolvedValue(ok(paretoRows()));
    vi.mocked(efficiencyApi.reliability).mockResolvedValue(ok({ failures: 2, short_faults: 1, operating_h: 120, mtbf_h: 60, mttr_h: 0.5, mttr_s: 1800, availability_inherent: 0.9917 }));
    vi.mocked(downtimeApi.list).mockResolvedValue(ok([downtime({ machine_id: "m-1", start: new Date(Date.now() - 3600e3).toISOString(), end: new Date(Date.now() - 3000e3).toISOString() })]));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
    vi.mocked(downtimeApi.noData).mockResolvedValue(ok([]));
  });

  it("queries the selected scope and period and shows A/P/Q/OEE/TEEP", async () => {
    render("/oee?scope=line&id=line-1&period=today");
    expect(await screen.findByRole("img", { name: /^OEE: 75,4\s%$/ })).toBeInTheDocument();
    const q = vi.mocked(efficiencyApi.oee).mock.calls[0][0];
    expect(q).toMatchObject({ scope: "line", id: "line-1", group_by: "hour" });
    expect(Date.parse(q.stop!) - Date.parse(q.start!)).toBeLessThanOrEqual(24 * 3600e3);
    expect(screen.getByRole("heading", { name: /OEE · Linha 1 — Montagem/ })).toBeInTheDocument();
    const kpis = within(screen.getByRole("region", { name: "Indicadores" }));
    expect(kpis.getByText("Disponibilidade").closest("div.surface")).toHaveTextContent("86,7 %");
    expect(kpis.getByText("TEEP").closest("div.surface")).toHaveTextContent("23,6 %");
    expect(kpis.getByText("Micro-paradas").closest("div.surface")).toHaveTextContent("12");
  });

  it("renders the waterfall, the Pareto (tabular data) and MTBF/MTTR", async () => {
    render("/oee?scope=line&id=line-1&period=today");
    const pareto = (await screen.findByRole("heading", { name: "Pareto de perdas" })).closest("div.surface") as HTMLElement;
    const rows = await within(pareto).findAllByRole("row");
    expect(rows[1]).toHaveTextContent("Perda de velocidade30 min036,0 %36,0 %");
    expect(within(pareto).getByRole("img", { name: /maior perda Perda de velocidade/ })).toBeInTheDocument();
    const wf = screen.getByRole("heading", { name: "Cascata de tempo" }).closest("div.surface") as HTMLElement;
    expect(within(wf).getByRole("img", { name: /POT 8 h 00 min, Planejadas 30 min, PBT 7 h 30 min/ })).toBeInTheDocument();
    const rel = within(screen.getByRole("heading", { name: "Confiabilidade" }).closest("div.surface") as HTMLElement);
    expect(await rel.findByText("60,0 h")).toBeInTheDocument(); // MTBF
    expect(rel.getByText("MTTR").closest("div.surface")).toHaveTextContent("30 min");
    expect(efficiencyApi.reliability).toHaveBeenCalledWith(expect.objectContaining({ scope: "line", id: "line-1" }));
  });

  it("warns about low data coverage and unknown product", async () => {
    vi.mocked(efficiencyApi.oee).mockResolvedValue(ok(oeeResult({ data_coverage: 0.72, product_unknown: true })));
    render("/oee?scope=machine&id=m-1&period=yesterday");
    const warnings = await screen.findByRole("list", { name: "Avisos sobre os dados" });
    expect(warnings).toHaveTextContent("Cobertura de dados: 72,0 %");
    expect(warnings).toHaveTextContent("Produto não informado");
  });

  it("warns about production outside the calendar and pro-rata hourly counts (S3-7, D7)", async () => {
    vi.mocked(efficiencyApi.oee).mockResolvedValue(ok(oeeResult({
      produced_outside_schedule: { total: 12, good: 12, scrap: 0 }, counts_prorated: true })));
    render("/oee?scope=machine&id=m-1&period=yesterday");
    const warnings = await screen.findByRole("list", { name: "Avisos sobre os dados" });
    expect(warnings).toHaveTextContent("Produção fora do calendário: 12 peças");
    expect(warnings).toHaveTextContent("Contagem proporcional");
  });

  it("plant scope: no MTBF (machine/line only) and no state timeline", async () => {
    render("/oee?scope=plant&period=7d");
    expect(await screen.findByText("Escolha uma linha ou máquina")).toBeInTheDocument();
    expect(efficiencyApi.oee).toHaveBeenCalledWith(expect.objectContaining({ scope: "plant", id: "plant-1", group_by: "day" }));
    expect(efficiencyApi.reliability).not.toHaveBeenCalled();
    expect(screen.queryByText("Linha do tempo de estados")).not.toBeInTheDocument();
  });

  it("'turno atual' without shifts says so instead of querying", async () => {
    render("/oee?scope=line&id=line-1&period=shift");
    expect(await screen.findByText("Nenhum turno cadastrado.")).toBeInTheDocument();
    expect(efficiencyApi.oee).not.toHaveBeenCalled();
  });

  it("switching scope updates the URL-driven query", async () => {
    const user = userEvent.setup();
    render("/oee?scope=plant&period=today");
    await screen.findByRole("img", { name: /OEE:/ });
    await user.click(screen.getByRole("tab", { name: "Máquina" }));
    const loc = new URLSearchParams(screen.getByTestId("location").textContent?.split("?")[1]);
    expect([loc.get("scope"), loc.get("id"), loc.get("period")]).toEqual(["machine", "m-1", "today"]);
    expect(await screen.findByRole("combobox", { name: "Máquina" })).toHaveValue("m-1");
  });
});
