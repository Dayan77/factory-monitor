import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShiftReportPage from "./ShiftReportPage";
import { efficiencyApi, plantsApi, reasonCodesApi, shiftsApi } from "@/services/api";
import { downtime, oeeResult, paretoRows, plantTree, reasonCodes, shift } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";
import type { ShiftReport } from "@/services/types";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  shiftsApi: { list: vi.fn() },
  efficiencyApi: { shiftReport: vi.fn() },
  reasonCodesApi: { list: vi.fn() },
}));

const REPORT: ShiftReport = {
  line_id: "line-1", productive_day: "2026-10-05", shift_id: "s-1", shift_name: "Turno A",
  period_start: "2026-10-05T10:00:00Z", period_end: "2026-10-05T18:00:00Z", oee: oeeResult(), pareto: paretoRows(),
  downtime: [downtime(), downtime({ id: "d-2", reason_code: "SET-01", comment: "troca para A-200", unjustified: false })],
  production: [{ machine_id: "m-1", machine_name: "Prensa P-01", good: 679, scrap: 21, total: 700, availability: 0.87, performance: 0.9, quality: 0.97, oee: 0.75 }],
};

describe("ShiftReportPage", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(shiftsApi.list).mockResolvedValue(ok([shift()]));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
    vi.mocked(efficiencyApi.shiftReport).mockResolvedValue(ok(REPORT));
  });

  it("renders the report for the chosen line / day / shift and prints it", async () => {
    const user = userEvent.setup();
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    renderRoute(<ShiftReportPage />, "/reports/shift?line=line-1&day=2026-10-05&shift=s-1", "/reports/shift");
    expect(await screen.findByRole("heading", { name: "Linha 1 — Montagem · Turno A" })).toBeInTheDocument();
    expect(efficiencyApi.shiftReport).toHaveBeenCalledWith({ line_id: "line-1", productive_day: "2026-10-05", shift_id: "s-1" });
    const prod = screen.getByRole("table", { name: "Produção por máquina" });
    expect(within(prod).getAllByRole("row")[1]).toHaveTextContent("Prensa P-0167921");
    const stops = screen.getByRole("table", { name: "Paradas do turno" });
    expect(stops).toHaveTextContent("Não justificada");
    expect(stops).toHaveTextContent("Setup / troca de produto");
    expect(stops).toHaveTextContent("troca para A-200");
    expect(screen.getByText("1 sem justificativa")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Imprimir" }));
    expect(print).toHaveBeenCalled();
  });

  it("switches to the light theme only while printing", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    renderRoute(<ShiftReportPage />, "/reports/shift?line=line-1&day=2026-10-05", "/reports/shift");
    window.dispatchEvent(new Event("beforeprint"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    window.dispatchEvent(new Event("afterprint"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("explains days without a shift", async () => {
    vi.mocked(efficiencyApi.shiftReport).mockResolvedValue(ok({ ...REPORT, reason: "no_shift", oee: null, production: [], downtime: [], pareto: [] }));
    renderRoute(<ShiftReportPage />, "/reports/shift?line=line-1&day=2026-10-04", "/reports/shift");
    expect(await screen.findByText("Sem turno neste dia")).toBeInTheDocument();
  });
});
