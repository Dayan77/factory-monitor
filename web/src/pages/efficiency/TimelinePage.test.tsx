import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TimelinePage from "./TimelinePage";
import { downtimeApi, plantsApi, reasonCodesApi, shiftsApi } from "@/services/api";
import { downtime, plantTree, reasonCodes } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  shiftsApi: { list: vi.fn() },
  downtimeApi: { list: vi.fn(), noData: vi.fn() },
  reasonCodesApi: { list: vi.fn() },
}));

describe("TimelinePage", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(shiftsApi.list).mockResolvedValue(ok([]));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
    vi.mocked(downtimeApi.list).mockResolvedValue(ok([
      downtime({ machine_id: "m-3", machine_name: "Envasadora E-01", start: new Date(Date.now() - 2 * 3600e3).toISOString(), end: new Date(Date.now() - 3600e3).toISOString(), duration_s: 3600 }),
    ]));
    vi.mocked(downtimeApi.noData).mockResolvedValue(ok([]));
  });

  it("draws the strips of the selected line and opens a stop's detail", async () => {
    const user = userEvent.setup();
    renderRoute(<TimelinePage />, "/timeline?line=line-2&period=today", "/timeline");
    expect(await screen.findByRole("heading", { name: "Linha 2 — Envase" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Linha 1 — Montagem" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("timeline-strip")).toHaveLength(3);
    expect(downtimeApi.list).toHaveBeenCalledWith(expect.objectContaining({ line_id: "line-2", limit: 5000 }));
    await user.click(screen.getByRole("button", { name: /Envasadora E-01: Falha/ }));
    const dialog = await screen.findByRole("dialog", { name: "Parada" });
    expect(within(dialog).getByText("Não justificada")).toBeInTheDocument();
    expect(within(dialog).getByText("Falha pneumática/hidráulica")).toBeInTheDocument();
    expect(within(dialog).getByText(/código de falha 17/)).toBeInTheDocument();
  });

  it("paints the periods without data as 'sem dados', never as 'sem parada registrada' (Sprint 7b, S7-22)", async () => {
    // Envasadora E-01 had no data from 5 h ago to 3 h ago (gateway offline) — before the stop
    vi.mocked(downtimeApi.noData).mockResolvedValue(ok([
      { machine_id: "m-3", intervals: [{ start: new Date(Date.now() - 5 * 3600e3).toISOString(), end: new Date(Date.now() - 3 * 3600e3).toISOString() }] },
    ]));
    renderRoute(<TimelinePage />, "/timeline?line=line-2&period=7d", "/timeline");
    await screen.findByRole("button", { name: /Envasadora E-01: Falha/ });
    expect(downtimeApi.noData).toHaveBeenCalledWith(expect.objectContaining({ line_id: "line-2" }));
    const strip = screen.getAllByTestId("timeline-strip").find((s) => s.querySelector("button[aria-label^='Envasadora E-01']"))!;
    const states = [...strip.children].map((c) => c.getAttribute("data-state"));
    // running → no data (the gap) → running → fault → running (the 7-day window ends now)
    expect(states).toEqual(["running", "nodata", "running", "error", "running"]);
  });

  it("says so when the gaps without data cannot be loaded (no silent green)", async () => {
    vi.mocked(downtimeApi.noData).mockRejectedValue(new Error("boom"));
    renderRoute(<TimelinePage />, "/timeline?line=line-2&period=today", "/timeline");
    expect(await screen.findByText(/Não foi possível verificar os períodos sem dados/)).toBeInTheDocument();
  });
});
