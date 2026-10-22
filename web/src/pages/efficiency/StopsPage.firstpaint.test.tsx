import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StopsPage from "./StopsPage";
import { downtimeApi, plantsApi, reasonCodesApi } from "@/services/api";
import { downtime, plantTree, reasonCodes } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  downtimeApi: { list: vi.fn(), justify: vi.fn(), create: vi.fn() },
  reasonCodesApi: { list: vi.fn() },
  productsApi: { forMachine: vi.fn() },
  runsApi: { list: vi.fn(), start: vi.fn(), stop: vi.fn() },
}));

// records every render of the picker inside the justify sheet: (value shown, suggestion of the stop)
const renders: { value: string | null; suggested: string | null | undefined }[] = [];
vi.mock("@/components/efficiency/ReasonPicker", () => ({
  default: ({ value, suggested }: { value: string | null; suggested?: string | null }) => {
    renders.push({ value, suggested });
    return <p data-testid="picker">{value ?? "—"}</p>;
  },
}));

const STOPS = [
  downtime({ id: "a", start: "2026-10-05T09:45:00Z", end: "2026-10-05T10:00:00Z", suggested_reason_code: "EQP-03", machine_name: "Prensa A" }),
  downtime({ id: "b", start: "2026-10-05T12:20:00Z", end: "2026-10-05T12:30:00Z", suggested_reason_code: "EQP-01", machine_name: "Prensa B" }),
];

describe("StopsPage justify sheet (first paint)", () => {
  beforeEach(() => {
    renders.length = 0;
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(downtimeApi.list).mockResolvedValue(ok(STOPS));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
  });

  it("never paints the previous stop's reason when another stop is opened", async () => {
    const user = userEvent.setup();
    renderRoute(<StopsPage />, "/stops", "/stops");
    const card = async (name: string) => (await screen.findAllByTestId("stop-card")).find((c) => c.textContent?.includes(name))!;
    await user.click(await card("Prensa A"));
    let dialog = await screen.findByRole("dialog", { name: "Justificar parada" });
    expect(within(dialog).getByTestId("picker")).toHaveTextContent("EQP-03");
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    renders.length = 0;
    await user.click(await card("Prensa B"));
    dialog = await screen.findByRole("dialog", { name: "Justificar parada" });
    expect(within(dialog).getByTestId("picker")).toHaveTextContent("EQP-01");
    const forSecond = renders.filter((r) => r.suggested === "EQP-01");
    expect(forSecond.length).toBeGreaterThan(0);
    expect(forSecond.map((r) => r.value)).not.toContain("EQP-03");
  });
});
