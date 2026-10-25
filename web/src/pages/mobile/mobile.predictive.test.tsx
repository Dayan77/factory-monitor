import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { onlineManager } from "@tanstack/react-query";
import WorkOrderExecPage from "./WorkOrderExecPage";
import { plantsApi, positionsApi } from "@/services/api";
import { failuresApi, workOrdersApi } from "@/services/maintenanceApi";
import { predictiveApi } from "@/services/predictiveApi";
import { useAuthStore } from "@/store/auth";
import { plantTree } from "@/test/fixtures";
import { TECH, workOrder } from "@/test/maintenanceFixtures";
import { alert, F1_TEXT, series } from "@/test/predictiveFixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({ plantsApi: { tree: vi.fn() }, positionsApi: { list: vi.fn(), get: vi.fn() }, componentsApi: { list: vi.fn() } }));
vi.mock("@/services/maintenanceApi", () => ({
  workOrdersApi: { list: vi.fn(), get: vi.fn(), transition: vi.fn(), labor: vi.fn(), task: vi.fn(), create: vi.fn() },
  usersApi: { technicians: vi.fn() },
  failuresApi: { get: vi.fn(), patch: vi.fn(), codes: vi.fn(), create: vi.fn() },
}));
vi.mock("@/services/predictiveApi", () => ({ predictiveApi: { alert: vi.fn(), features: vi.fn() } }));

const PRED = workOrder({
  id: "wo-p", number: "OS-200", type: "predictive", state: "in_progress", source_alert_id: "al-1", position_id: "POS-B3", task_list: [],
  allowed_transitions: ["on_hold", "completed", "cancelled"], user_allowed_transitions: ["on_hold", "completed"], title: "Preditiva — Cilindro B3: tempo de curso",
});

beforeEach(() => {
  useAuthStore.setState({ user: TECH, loading: false, offline: false });
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => true });
  onlineManager.setOnline(true);
  vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
  vi.mocked(positionsApi.list).mockResolvedValue(ok([]));
  vi.mocked(workOrdersApi.get).mockResolvedValue(ok(PRED));
  vi.mocked(workOrdersApi.transition).mockResolvedValue(ok({ ...PRED, state: "completed" }));
  vi.mocked(predictiveApi.alert).mockResolvedValue(ok(alert({ state: "converted", work_order_id: "wo-p" })));
  vi.mocked(predictiveApi.features).mockResolvedValue(ok({ position_id: "POS-B3", series: [series()] }));
});

function exec() {
  return renderRoute(<WorkOrderExecPage />, "/m/work-orders/wo-p", "/m/work-orders/:id");
}

describe("technician — predictive WO", () => {
  it("shows the alert text and the condition chart", async () => {
    exec();
    const block = await screen.findByTestId("wo-predictive");
    expect(within(block).getByTestId("wo-alert-text")).toHaveTextContent(F1_TEXT);
    expect(await within(block).findByTestId("condition-chart")).toBeInTheDocument();
  });

  it("completion requires the finding; 'no defect' sends finding + baseline reset", async () => {
    const u = userEvent.setup();
    exec();
    await u.click(await screen.findByRole("button", { name: "Concluir" }));
    const dlg = screen.getByRole("dialog", { name: "Concluir OS" });
    await u.click(within(dlg).getByRole("button", { name: "Concluir OS" }));
    expect(within(dlg).getByRole("alert")).toHaveTextContent("Informe o achado");
    expect(workOrdersApi.transition).not.toHaveBeenCalled();
    await u.click(within(dlg).getByRole("radio", { name: /Sem defeito/ }));
    await u.click(within(dlg).getByRole("checkbox", { name: /reiniciar a referência/ }));
    await u.click(within(dlg).getByRole("button", { name: "Concluir OS" }));
    await waitFor(() => expect(workOrdersApi.transition).toHaveBeenCalledWith("wo-p", { to: "completed", note: null, finding: "no_defect", resets_baseline: false }, undefined));
    expect(failuresApi.create).not.toHaveBeenCalled();
  });

  it("'defect confirmed' records the ISO 14224 failure (condition monitoring) before completing", async () => {
    vi.mocked(failuresApi.create).mockResolvedValue(ok({} as never));
    const u = userEvent.setup();
    exec();
    await u.click(await screen.findByRole("button", { name: "Concluir" }));
    const dlg = screen.getByRole("dialog", { name: "Concluir OS" });
    await u.click(within(dlg).getByRole("radio", { name: /Defeito confirmado/ }));
    await u.click(within(dlg).getByRole("button", { name: "Concluir OS" }));
    expect(within(dlg).getByRole("alert")).toHaveTextContent("modo de falha");
    await waitFor(() => expect(within(dlg).getByRole("option", { name: "PDE" })).toBeInTheDocument());
    expect(within(dlg).queryByRole("option", { name: "VIB" })).toBeNull();
    await u.selectOptions(within(dlg).getByLabelText(/Modo de falha/), "PDE");
    await u.click(within(dlg).getByRole("radio", { name: /Incipiente/ }));
    await u.click(within(dlg).getByRole("button", { name: "Concluir OS" }));
    await waitFor(() => expect(workOrdersApi.transition).toHaveBeenCalledWith("wo-p", { to: "completed", note: null, finding: "defect_confirmed", resets_baseline: true }, undefined));
    expect(failuresApi.create).toHaveBeenCalledWith(expect.objectContaining({
      machine_id: "m-1", position_id: "POS-B3", severity: "incipient", failure_mode: "PDE", detection_method: "continuous_condition_monitoring", work_order_id: "wo-p",
    }));
  });
});
