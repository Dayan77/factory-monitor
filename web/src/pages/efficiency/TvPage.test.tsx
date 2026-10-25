import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import TvPage from "./TvPage";
import { downtimeApi, efficiencyApi, plantsApi, reasonCodesApi } from "@/services/api";
import { normalizeOverview } from "@/services/efficiency";
import { downtime, overviewPayload, plantTree, reasonCodes } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";
import { useThemeStore } from "@/store/theme";

vi.mock("@/services/api", () => ({
  authApi: { me: vi.fn() },
  plantsApi: { tree: vi.fn() },
  efficiencyApi: { overview: vi.fn() },
  downtimeApi: { list: vi.fn() },
  reasonCodesApi: { list: vi.fn() },
}));

describe("TvPage (andon)", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(efficiencyApi.overview).mockResolvedValue(normalizeOverview(overviewPayload()));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
    vi.mocked(downtimeApi.list).mockResolvedValue(ok([
      downtime({ id: "o", machine_id: "m-3", machine_name: "Envasadora E-01", start: new Date(Date.now() - 65_000).toISOString(), end: null, duration_s: null, reason_code: null, suggested_reason_code: "EQP-03" }),
    ]));
  });

  it("shows huge OEE of the current shift, the open stop with a timer and every machine status", async () => {
    useThemeStore.getState().setPref("light");                  // the user's theme
    const { unmount } = renderRoute(<TvPage />, "/tv/lines/line-2", "/tv/lines/:lineId");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(await screen.findByRole("heading", { name: "Linha 2 — Envase" })).toBeInTheDocument();
    expect(await screen.findByText("Parada em andamento")).toBeInTheDocument();
    expect(screen.getByTestId("tv-timer").textContent).toMatch(/^01:0\d$/);
    expect(screen.getByText("Falha pneumática/hidráulica (sugestão)")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /^OEE: 41,0\s%$/ })).toBeInTheDocument(); // no current shift → today's OEE
    expect(screen.getByText("OEE de hoje")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.getAttribute("data-status"))).toEqual(["error", "stopped", "running"]);
    expect(downtimeApi.list).toHaveBeenCalledWith({ line_id: "line-2", open: true });
    unmount();
    // restored in the unmounting commit itself (layout effect): the next page never paints dark
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    useThemeStore.getState().setPref("system");
  });

  it("uses the current-shift OEE when a shift is running", async () => {
    renderRoute(<TvPage />, "/tv/lines/line-1", "/tv/lines/:lineId");
    expect(await screen.findByRole("img", { name: /^OEE: 81,0\s%$/ })).toBeInTheDocument();
    expect(screen.getByText("Turno A · OEE do turno")).toBeInTheDocument();
  });
});
