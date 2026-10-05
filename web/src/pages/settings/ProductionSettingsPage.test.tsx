import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProductionSettingsPage, { REASON_CODE_RE, shiftHours } from "./ProductionSettingsPage";
import { calendarApi, plantsApi, productsApi, reasonCodesApi, shiftsApi } from "@/services/api";
import { useAuthStore } from "@/store/auth";
import { plantTree, reasonCodes, shift } from "@/test/fixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({
  authApi: { me: vi.fn() },
  plantsApi: { tree: vi.fn() },
  shiftsApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
  calendarApi: { list: vi.fn(), create: vi.fn(), remove: vi.fn() },
  productsApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), forMachine: vi.fn(), addToMachine: vi.fn(), updateOnMachine: vi.fn(), removeFromMachine: vi.fn() },
  reasonCodesApi: { list: vi.fn(), create: vi.fn(), update: vi.fn() },
}));

function asRole(role: string) {
  useAuthStore.setState({ user: { id: "u", name: "U", email: "u@x", role, tenant_id: "t1" } });
}
const render = (path = "/settings/production") => renderRoute(<ProductionSettingsPage />, path, "/settings/production");

describe("ProductionSettingsPage", () => {
  beforeEach(() => {
    asRole("admin");
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(shiftsApi.list).mockResolvedValue(ok([shift()]));
    vi.mocked(calendarApi.list).mockResolvedValue(ok([]));
    vi.mocked(reasonCodesApi.list).mockResolvedValue(ok(reasonCodes()));
    vi.mocked(productsApi.list).mockResolvedValue(ok([{ id: "p-1", tenant_id: "t1", code: "A-100", name: "Tampa", created_at: "" }]));
    vi.mocked(productsApi.forMachine).mockResolvedValue(ok([]));
  });

  it("helpers: net shift hours and reason-code format", () => {
    expect(shiftHours(shift())).toBe(7.5);
    expect(shiftHours(shift({ start_local: "22:00", end_local: "06:00", breaks: [] }))).toBe(8);
    expect(REASON_CODE_RE.test("EQP-01-HID")).toBe(true);
    expect(REASON_CODE_RE.test("eqp-1")).toBe(false);
  });

  it("lists shifts and creates one with weekdays and a break", async () => {
    const user = userEvent.setup();
    vi.mocked(shiftsApi.create).mockResolvedValue(ok(shift({ id: "s-2" })));
    render();
    const item = await screen.findByTestId("shift-item");
    expect(item).toHaveTextContent("Turno A");
    expect(item).toHaveTextContent("06:00–14:00 · 7,5 h líquidas");
    await user.click(screen.getByRole("button", { name: "Novo turno" }));
    const dialog = await screen.findByRole("dialog", { name: "Novo turno" });
    await user.type(within(dialog).getByLabelText("Nome"), "Turno B");
    await user.click(within(dialog).getByRole("button", { name: "Sex" })); // toggle Friday off
    await user.click(within(dialog).getByRole("button", { name: "Sáb" })); // Saturday on
    await user.click(within(dialog).getByRole("button", { name: "+ Adicionar pausa" }));
    await user.click(within(dialog).getByRole("button", { name: "Salvar turno" }));
    expect(shiftsApi.create).toHaveBeenCalledWith({
      plant_id: "plant-1", name: "Turno B", start_local: "06:00", end_local: "14:00", weekdays: [0, 1, 2, 3, 5],
      breaks: [{ start_local: "10:00", end_local: "10:15", reason_code: "PLN-01" }], is_active: true,
    });
  });

  it("editing hours warns that the past keeps the old schedule and sends effective_from (D11)", async () => {
    const user = userEvent.setup();
    vi.mocked(shiftsApi.update).mockResolvedValue(ok(shift({ id: "s-9", start_local: "07:00" })));
    render();
    await screen.findByTestId("shift-item");
    await user.click(screen.getByRole("button", { name: "Editar" }));
    const dialog = await screen.findByRole("dialog", { name: "Editar turno" });
    expect(within(dialog).queryByRole("note")).not.toBeInTheDocument();          // nothing changed yet
    await user.click(within(dialog).getByRole("button", { name: "Sáb" }));
    const note = within(dialog).getByRole("note");
    expect(note).toHaveTextContent("Os dias anteriores continuam com o horário antigo");
    const eff = within(dialog).getByLabelText("Vale a partir de");
    await user.clear(eff);
    await user.type(eff, "2026-11-02");
    await user.click(within(dialog).getByRole("button", { name: "Salvar turno" }));
    expect(shiftsApi.update).toHaveBeenCalledWith(shift().id, expect.objectContaining({ effective_from: "2026-11-02" }));
  });

  it("is read-only for operators", async () => {
    asRole("operator");
    render();
    expect(await screen.findByTestId("shift-item")).toBeInTheDocument();
    expect(screen.getByText("Somente administradores alteram estas configurações.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Novo turno" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar" })).not.toBeInTheDocument();
  });

  it("adds a tenant reason code and toggles only tenant codes", async () => {
    const user = userEvent.setup();
    vi.mocked(reasonCodesApi.create).mockResolvedValue(ok(reasonCodes()[6]));
    vi.mocked(reasonCodesApi.update).mockResolvedValue(ok(reasonCodes()[6]));
    render("/settings/production?tab=reasons");
    const table = await screen.findByRole("table", { name: "Motivos de parada" });
    expect(within(table).getAllByRole("button", { name: /Desativar|Ativar/ })).toHaveLength(2); // only tenant codes
    const add = screen.getByRole("button", { name: "Adicionar" });
    await user.type(screen.getByLabelText("Código"), "eqp-1");
    expect(screen.getByText("Formato CAT-NN")).toBeInTheDocument();
    expect(add).toBeDisabled();
    await user.clear(screen.getByLabelText("Código"));
    await user.type(screen.getByLabelText("Código"), "EQP-02-SRV");
    await user.type(screen.getByLabelText("Descrição"), "Servo motor");
    await user.click(add);
    expect(reasonCodesApi.create).toHaveBeenCalledWith({ code: "EQP-02-SRV", category: "EQP", label: "Servo motor", planned: false, oee_loss: "availability" });
    await user.click(within(table).getByRole("button", { name: "Ativar" }));
    expect(reasonCodesApi.update).toHaveBeenCalledWith("rc-OPR-02", { is_active: true });
  });

  it("links a product to a machine with its ideal cycle time", async () => {
    const user = userEvent.setup();
    vi.mocked(productsApi.addToMachine).mockResolvedValue(ok({ id: "mp", machine_id: "m-1", product_id: "p-1", product_code: "A-100", product_name: "Tampa", ideal_cycle_time_s: 2.5, parts_per_cycle: 2, is_default: false, ideal_time_per_part_s: 1.25 }));
    render("/settings/production?tab=products");
    expect(await screen.findByText("Nenhum produto nesta máquina")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Produto"), "p-1");
    await user.type(screen.getByLabelText("Ciclo ideal (s)"), "2,5");
    await user.clear(screen.getByLabelText("Peças/ciclo"));
    await user.type(screen.getByLabelText("Peças/ciclo"), "2");
    await user.click(screen.getByRole("button", { name: "Vincular" }));
    expect(productsApi.addToMachine).toHaveBeenCalledWith("m-1", { product_id: "p-1", ideal_cycle_time_s: 2.5, parts_per_cycle: 2, is_default: false });
  });
});
