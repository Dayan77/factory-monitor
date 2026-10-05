import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError } from "axios";
import { onlineManager } from "@tanstack/react-query";
import WorkOrderExecPage from "./WorkOrderExecPage";
import MyWorkOrdersPage from "./MyWorkOrdersPage";
import CallMaintenanceButton from "@/components/maintenance/CallMaintenanceButton";
import EmergencyAlert from "@/components/notifications/EmergencyAlert";
import { plantsApi, positionsApi } from "@/services/api";
import { workOrdersApi } from "@/services/maintenanceApi";
import { getQueue } from "@/services/offline";
import { useAuthStore } from "@/store/auth";
import { useNotificationStore } from "@/store/notifications";
import { plantTree } from "@/test/fixtures";
import { OPERATOR, SUP, TECH, workOrder } from "@/test/maintenanceFixtures";
import { ok, renderRoute, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  positionsApi: { list: vi.fn(), get: vi.fn() },
  componentsApi: { list: vi.fn() },
}));
vi.mock("@/services/maintenanceApi", () => ({
  workOrdersApi: { list: vi.fn(), get: vi.fn(), transition: vi.fn(), labor: vi.fn(), task: vi.fn(), emergencyCall: vi.fn(), replacement: vi.fn(), create: vi.fn() },
  usersApi: { technicians: vi.fn() },
  failuresApi: { get: vi.fn(), patch: vi.fn(), codes: vi.fn(), create: vi.fn() },
}));

function setOnline(v: boolean) {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => v });
  window.dispatchEvent(new Event(v ? "online" : "offline"));
  onlineManager.setOnline(v);
}

beforeEach(() => {
  useAuthStore.setState({ user: TECH, loading: false, offline: false });
  vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
  vi.mocked(positionsApi.list).mockResolvedValue(ok([]));
  setOnline(true);
});
afterEach(() => setOnline(true));

function exec(id = "wo-1") {
  return renderRoute(<WorkOrderExecPage />, `/m/work-orders/${id}`, "/m/work-orders/:id");
}

describe("technician execution", () => {
  it("starts a scheduled WO from the thumb-zone button", async () => {
    const u = userEvent.setup();
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ user_allowed_transitions: ["in_progress"] })));
    vi.mocked(workOrdersApi.transition).mockResolvedValue(ok(workOrder({ state: "in_progress", allowed_transitions: ["on_hold", "completed", "cancelled"] })));
    exec();
    await u.click(await screen.findByRole("button", { name: "Iniciar" }));
    await waitFor(() => expect(workOrdersApi.transition).toHaveBeenCalledWith("wo-1", { to: "in_progress", note: null }, undefined));
    expect(await screen.findByText("OS iniciada.")).toBeInTheDocument();
  });

  it("offline: task check, labor and pause are queued and applied locally (nothing sent)", async () => {
    const u = userEvent.setup();
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ state: "in_progress", labor_hours: 0.5, allowed_transitions: ["on_hold", "completed", "cancelled"], user_allowed_transitions: ["on_hold", "completed"] })));
    exec();
    await screen.findByRole("button", { name: "Concluir" });
    act(() => setOnline(false));
    await u.click(screen.getAllByTestId("task-item")[0]);
    expect(await screen.findByText(/Salvo no aparelho/)).toBeInTheDocument();
    expect(screen.getAllByTestId("task-item")[0]).toHaveAttribute("aria-checked", "true");
    await u.click(screen.getByRole("button", { name: "Apontar horas" }));
    const lab = screen.getByRole("dialog", { name: "Apontar horas" });
    await u.click(within(lab).getByRole("button", { name: "Apontar" }));
    await waitFor(() => expect(screen.getByTestId("labor-hours")).toHaveTextContent("1,0 h"));
    await u.click(screen.getByRole("button", { name: "Pausar" }));
    const pause = screen.getByRole("dialog", { name: "Pausar OS" });
    await u.click(within(pause).getByRole("radio", { name: "Aguardando peça" }));
    await u.click(within(pause).getByRole("button", { name: "Pausar" }));
    await waitFor(() => expect(getQueue().map((a) => a.kind)).toEqual(["task", "labor", "transition"]));
    expect(getQueue()[2].body).toMatchObject({ to: "on_hold", hold_reason: "waiting_parts" });
    expect(workOrdersApi.task).not.toHaveBeenCalled();
    expect(workOrdersApi.labor).not.toHaveBeenCalled();
    expect(screen.getByText("Em espera: Aguardando peça")).toBeInTheDocument();
    expect(screen.getByText("3 pendentes de envio")).toBeInTheDocument();
  });

  it("a network failure while 'online' also queues instead of losing the action", async () => {
    const u = userEvent.setup();
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ state: "in_progress", allowed_transitions: ["on_hold", "completed"], user_allowed_transitions: ["on_hold", "completed"] })));
    vi.mocked(workOrdersApi.task).mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));
    exec();
    await u.click((await screen.findAllByTestId("task-item"))[1]);
    await waitFor(() => expect(getQueue()).toHaveLength(1));
  });

  it("completed: labor prefilled from the time in execution, then labor + transition", async () => {
    const u = userEvent.setup();
    const started = new Date(Date.now() - 90 * 60_000).toISOString();
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({
      state: "in_progress", started_at: started, allowed_transitions: ["on_hold", "completed"], user_allowed_transitions: ["on_hold", "completed"],
      history: [{ at: started, by: "u-tech", from_state: "scheduled", to_state: "in_progress", note: null }],
    })));
    vi.mocked(workOrdersApi.labor).mockResolvedValue(ok(workOrder({ state: "in_progress", labor_hours: 1.5 })));
    vi.mocked(workOrdersApi.transition).mockResolvedValue(ok(workOrder({ state: "completed" })));
    exec();
    await u.click(await screen.findByRole("button", { name: "Concluir" }));
    const dlg = screen.getByRole("dialog", { name: "Concluir OS" });
    expect(within(dlg).getByLabelText("Horas trabalhadas nesta sessão")).toHaveValue("1,5");
    expect(within(dlg).getByRole("alert")).toHaveTextContent("2 item(ns) do checklist não marcado(s)");
    await u.click(within(dlg).getByRole("button", { name: "Concluir OS" }));
    await waitFor(() => expect(workOrdersApi.transition).toHaveBeenCalledWith("wo-1", { to: "completed", note: null }, undefined));
    expect(workOrdersApi.labor).toHaveBeenCalledWith("wo-1", { hours: 1.5, note: null }, undefined);
  });

  it("offline with no network data falls back to the device snapshot, flagged", async () => {
    vi.mocked(workOrdersApi.get).mockResolvedValueOnce(ok(workOrder()));
    const first = exec();
    await screen.findByRole("heading", { name: "Troca de vedações — Cilindro A2" });
    first.unmount();
    act(() => setOnline(false));
    exec();
    expect(await screen.findByText(/Salva no aparelho/)).toBeInTheDocument();
  });
});

describe("Minhas OS", () => {
  it("orders emergency > overdue > today and shows open emergency calls to assume", async () => {
    const today = new Intl.DateTimeFormat("en-CA").format(new Date());
    vi.mocked(workOrdersApi.list).mockImplementation(async (q) => ok(q?.assignee_id
      ? [
        workOrder({ id: "a", number: "OS-1", scheduled_for: today }),
        workOrder({ id: "b", number: "OS-2", scheduled_for: "2020-01-01" }),
        workOrder({ id: "c", number: "OS-3", type: "corrective", urgency: "emergency", state: "in_progress", scheduled_for: null }),
        workOrder({ id: "d", number: "OS-4", state: "closed" }),
      ]
      : [workOrder({ id: "e", number: "OS-9", type: "corrective", urgency: "emergency", state: "open", allowed_transitions: ["in_progress", "cancelled"], title: "Robô parado" })]) as never);
    renderRoute(<MyWorkOrdersPage />, "/m/work-orders", "/m/work-orders");
    await waitFor(() => expect(screen.getAllByTestId("my-wo").map((e) => e.getAttribute("data-wo"))).toEqual(["OS-3", "OS-2", "OS-1"]));
    expect(await screen.findByTestId("emergency-call")).toHaveTextContent("Robô parado");
    expect(workOrdersApi.list).toHaveBeenCalledWith({ assignee_id: "u-tech" });
  });
});

describe("Chamar manutenção", () => {
  const ev = { id: "d-2", machine_id: "m-2", machine_name: "Robô de Solda", fault_code: 251, suggested_reason_code: "EQP-04", reason_code: null };
  it("supervisor / operator creates the emergency call linked to the stop", async () => {
    const u = userEvent.setup();
    useAuthStore.setState({ user: SUP });
    vi.mocked(workOrdersApi.emergencyCall).mockResolvedValue(ok(workOrder({ number: "OS-125", state: "open" })));
    renderWithClient(<CallMaintenanceButton ev={ev} />);
    await u.click(screen.getByRole("button", { name: "Chamar manutenção" }));
    expect(await screen.findByText(/Manutenção chamada: OS-125/)).toBeInTheDocument();
    expect(workOrdersApi.emergencyCall).toHaveBeenCalledWith(expect.objectContaining({ downtime_event_id: "d-2", machine_id: "m-2" }));
    useAuthStore.setState({ user: OPERATOR });
  });

  it("hidden without a role that may request", () => {
    useAuthStore.setState({ user: { ...TECH, role: "viewer", roles: ["viewer"] } });
    renderWithClient(<CallMaintenanceButton ev={ev} />);
    expect(screen.queryByRole("button", { name: "Chamar manutenção" })).not.toBeInTheDocument();
  });
});

describe("Emergency alert", () => {
  it("full screen until acknowledged; 'Atender' opens the work order", async () => {
    const u = userEvent.setup();
    renderRoute(<EmergencyAlert mobile />, "/m/work-orders", "/m/work-orders");
    act(() => useNotificationStore.getState().raise({ id: "n1", type: "emergency_call", severity: "emergency", title: "Robô de Solda — falha 251", body: "há 3 min", url: "/m/work-orders/wo-9", created_at: new Date().toISOString(), read_at: null }));
    const alert = await screen.findByRole("alertdialog", { name: "Robô de Solda — falha 251" });
    expect(alert).toHaveAccessibleDescription("há 3 min");
    // the same call arriving again (push + WebSocket) is not raised twice
    act(() => useNotificationStore.getState().raise({ id: "n1", type: "emergency_call", title: "dup", body: null, created_at: "", read_at: null }));
    expect(useNotificationStore.getState().emergencies).toHaveLength(1);
    await u.click(screen.getByRole("button", { name: "Atender chamado" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/m/work-orders/wo-9");
  });
});
