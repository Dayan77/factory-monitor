import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StopsPage from "./StopsPage";
import { downtimeApi, plantsApi, productsApi, reasonCodesApi, runsApi } from "@/services/api";
import { downtime, plantTree, reasonCodes } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  downtimeApi: { list: vi.fn(), justify: vi.fn(), create: vi.fn() },
  reasonCodesApi: { list: vi.fn() },
  productsApi: { forMachine: vi.fn() },
  runsApi: { list: vi.fn(), start: vi.fn(), stop: vi.fn() },
}));

const STOPS = [
  downtime({ id: "closed", start: "2026-10-05T10:00:00Z", end: "2026-10-05T10:15:00Z" }),
  downtime({ id: "open", machine_name: "Robô R-02", machine_id: "m-2", start: new Date(Date.now() - 125_000).toISOString(), end: null, duration_s: null, suggested_reason_code: null, fault_code: null, source: "auto_run_state" }),
];

function render(path = "/stops") {
  return renderRoute(<StopsPage />, path, "/stops");
}

describe("StopsPage (operator)", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(downtimeApi.list).mockResolvedValue(ok(STOPS));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
  });

  it("lists unjustified stops, the open one first with a live timer", async () => {
    render("/stops?line=line-1");
    const cards = await screen.findAllByTestId("stop-card");
    expect(cards[0]).toHaveTextContent("Robô R-02");
    expect(cards[0]).toHaveTextContent("Parada agora");
    expect(cards[0].textContent).toMatch(/0[23]:\d\d/);
    expect(cards[1]).toHaveTextContent("Sugestão: Falha pneumática/hidráulica");
    expect(downtimeApi.list).toHaveBeenCalledWith({ unjustified: true, line_id: "line-1" });
    expect(screen.getByRole("button", { name: "Linha 1 — Montagem" })).toHaveAttribute("aria-pressed", "true");
  });

  it("justifies with the suggested reason preselected and removes the stop at once", async () => {
    const user = userEvent.setup();
    let resolve: (v: unknown) => void = () => {};
    vi.mocked(downtimeApi.justify).mockReturnValue(new Promise((r) => { resolve = r; }) as never);
    render();
    const cards = await screen.findAllByTestId("stop-card");
    await user.click(cards[1]);
    const dialog = await screen.findByRole("dialog", { name: "Justificar parada" });
    const suggested = within(dialog).getByRole("radio", { name: /Falha pneumática\/hidráulica/ });
    expect(suggested).toHaveAttribute("aria-checked", "true");
    expect(within(dialog).getByText("Sugerido")).toBeInTheDocument();
    // grouped by category, "não justificada" is not offered
    expect(within(dialog).getByRole("group", { name: "Equipamento" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: /Não justificada/ })).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Comentário (opcional)"), "vazamento no cilindro");
    await user.click(within(dialog).getByRole("button", { name: "Confirmar motivo" }));
    expect(downtimeApi.justify).toHaveBeenCalledWith("closed", { reason_code: "EQP-03", comment: "vazamento no cilindro" });
    // optimistic: gone from the list before the server answers
    await waitFor(() => expect(screen.getAllByTestId("stop-card")).toHaveLength(1));
    resolve(ok(downtime({ id: "closed", reason_code: "EQP-03" })));
    expect(await screen.findByText(/justificada: Falha pneumática/)).toBeInTheDocument();
  });

  it("requires a reason when there is no suggestion and restores the list on error", async () => {
    const user = userEvent.setup();
    vi.mocked(downtimeApi.justify).mockRejectedValue(new Error("boom"));
    render();
    await user.click((await screen.findAllByTestId("stop-card"))[0]);
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Confirmar motivo" });
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("radio", { name: /Setup \/ troca de produto/ }));
    await user.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Não foi possível salvar");
    expect(screen.getAllByTestId("stop-card")).toHaveLength(2);
  });

  it("registers a manual stop", async () => {
    const user = userEvent.setup();
    vi.mocked(downtimeApi.create).mockResolvedValue(ok(downtime({ id: "new", source: "manual" })));
    render();
    await screen.findAllByTestId("stop-card");
    await user.click(screen.getByRole("button", { name: "Registrar parada" }));
    const dialog = await screen.findByRole("dialog", { name: "Registrar parada" });
    await user.selectOptions(within(dialog).getByLabelText("Máquina"), "m-4");
    await user.click(within(dialog).getByRole("radio", { name: /Falta de material/ }));
    await user.click(within(dialog).getByRole("button", { name: "Registrar parada" }));
    expect(downtimeApi.create).toHaveBeenCalledWith(expect.objectContaining({ machine_id: "m-4", reason_code: "MAT-01", end: null, comment: null }));
    expect(await screen.findByText("Parada registrada.")).toBeInTheDocument();
  });

  it("changes the product of a machine (starts a production run)", async () => {
    const user = userEvent.setup();
    vi.mocked(runsApi.list).mockResolvedValue(ok([]));
    vi.mocked(productsApi.forMachine).mockResolvedValue(ok([
      { id: "mp-1", machine_id: "m-1", product_id: "p-1", product_code: "A-100", product_name: "Tampa 100 mm", ideal_cycle_time_s: 2.5, parts_per_cycle: 1, is_default: true, ideal_time_per_part_s: 2.5 },
    ]));
    vi.mocked(runsApi.start).mockResolvedValue(ok({ id: "r-1", machine_id: "m-1", product_id: "p-1", product_code: "A-100", started_at: "2026-10-05T12:00:00Z", ended_at: null }));
    render();
    await screen.findAllByTestId("stop-card");
    await user.click(screen.getByRole("button", { name: "Trocar produto" }));
    const dialog = await screen.findByRole("dialog", { name: "Trocar produto" });
    await user.selectOptions(within(dialog).getByLabelText("Máquina"), "m-1");
    expect(await within(dialog).findByText("Produto não informado")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Tampa 100 mm/ }));
    expect(runsApi.start).toHaveBeenCalledWith({ machine_id: "m-1", product_id: "p-1" });
    expect(await screen.findByText("Produção de Tampa 100 mm iniciada.")).toBeInTheDocument();
  });

  it("celebrates an empty list", async () => {
    vi.mocked(downtimeApi.list).mockResolvedValue(ok([]));
    render();
    expect(await screen.findByText("Nenhuma parada sem justificativa")).toBeInTheDocument();
  });
});
