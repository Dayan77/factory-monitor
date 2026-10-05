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
  downtimeApi: { list: vi.fn() },
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
});
