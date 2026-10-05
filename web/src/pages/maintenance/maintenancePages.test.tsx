import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, AxiosHeaders } from "axios";
import UpcomingPage from "./UpcomingPage";
import KpisPage, { monthRange, previousMonth } from "./KpisPage";
import ReliabilityPage, { reliabilityWindow } from "./ReliabilityPage";
import { toBody, validatePlan } from "./PlansSettingsPage";
import WorkOrderSheet from "@/components/maintenance/WorkOrderSheet";
import { plantsApi, positionsApi } from "@/services/api";
import { maintenanceKpiApi, plansApi, usersApi, workOrdersApi } from "@/services/maintenanceApi";
import { useAuthStore } from "@/store/auth";
import { plantTree } from "@/test/fixtures";
import { SUP, TECH, planStatus, user, workOrder } from "@/test/maintenanceFixtures";
import { ok, renderRoute, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  positionsApi: { list: vi.fn(), get: vi.fn() },
}));
vi.mock("@/services/maintenanceApi", () => ({
  plansApi: { status: vi.fn(), list: vi.fn() },
  workOrdersApi: { list: vi.fn(), get: vi.fn(), transition: vi.fn(), approveDraft: vi.fn(), update: vi.fn() },
  usersApi: { technicians: vi.fn() },
  maintenanceKpiApi: { kpis: vi.fn(), reliability: vi.fn() },
  failuresApi: { codes: vi.fn() },
}));

function http422(allowed: string[]) {
  return new AxiosError("x", "ERR_BAD_REQUEST", undefined, undefined, {
    status: 422, statusText: "", headers: {}, config: { headers: new AxiosHeaders() },
    data: { detail: { message: "Transição inválida.", allowed, state: "approved", to: "completed" } },
  });
}

beforeEach(() => {
  useAuthStore.setState({ user: user(), loading: false, offline: false });
  vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
  vi.mocked(positionsApi.list).mockResolvedValue(ok([]));
  vi.mocked(usersApi.technicians).mockResolvedValue(ok([{ id: "u-tech", name: "Tiago Técnico" }]));
  vi.mocked(workOrdersApi.list).mockResolvedValue(ok([]));
});

describe("Próximas manutenções", () => {
  it("lists plans by due date with state, consumed (trigger), due date by the first trigger and next usage", async () => {
    vi.mocked(plansApi.status).mockResolvedValue(ok([
      planStatus({ plan_id: "PL1", plan_name: "Vedações A1", state: "ok", consumed: 0.17, determining_trigger: "cycles", projected_date: "2099-03-04", days_to_due: 93,
        triggers: [{ measure: "cycles", interval: 2_000_000, usage: 340_000, consumed: 0.17, projected_date: "2099-03-04" }], next_due_usage: { measure: "cycles", value: 9_200_000 } }),
      planStatus({ days_to_due: 40, projected_date: "2099-01-10", triggers: planStatus().triggers.map((t) => ({ ...t, projected_date: t.projected_date?.replace("2027", "2099") ?? null })) }),
      planStatus({ plan_id: "PL6", plan_name: "Filtro de ar", state: "overdue", consumed: 3.714, determining_trigger: "days", days_to_due: 0, past_due: true, position_id: null, position_name: null,
        triggers: [{ measure: "days", interval: 7, usage: 26, consumed: 3.714, projected_date: "2026-12-01" }], next_due_usage: null,
        open_work_order_id: "wo-123", open_work_order_number: "OS-123", open_work_order_state: "draft" }),
    ]));
    vi.mocked(workOrdersApi.approveDraft).mockResolvedValue(ok(workOrder({ id: "wo-123", state: "approved" })));
    const u = userEvent.setup();
    renderRoute(<UpcomingPage />, "/maintenance/upcoming", "/maintenance/upcoming");
    const rows = await screen.findAllByTestId("plan-row");
    expect(rows.map((r) => r.getAttribute("data-plan"))).toEqual(["PL6", "PL3", "PL1"]);
    expect(rows[0]).toHaveTextContent("Atrasado");
    expect(rows[0]).toHaveTextContent(/371,4\s%/);
    expect(rows[1]).toHaveTextContent(/82,0\s%\s?\(partidas\)/);
    expect(rows[1]).toHaveTextContent("por horas");
    expect(rows[1]).toHaveTextContent("45.800 h no horímetro");
    expect(within(rows[0]).getByText("OS-123")).toBeInTheDocument();
    await u.click(within(rows[0]).getByRole("button", { name: "Aprovar rascunho" }));
    await waitFor(() => expect(workOrdersApi.approveDraft).toHaveBeenCalledWith("wo-123"));
    // state filter tile
    await u.click(screen.getByTestId("count-overdue"));
    await waitFor(() => expect(screen.getAllByTestId("plan-row")).toHaveLength(1));
  });

  it("explicit empty and error states", async () => {
    vi.mocked(plansApi.status).mockResolvedValue(ok([]));
    renderRoute(<UpcomingPage />, "/maintenance/upcoming", "/maintenance/upcoming");
    expect(await screen.findByText("Nenhum plano de manutenção cadastrado")).toBeInTheDocument();
  });
});

describe("Work order detail", () => {
  it("offers only the server's allowed transitions for the user, shows the audit trail", async () => {
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ state: "approved", allowed_transitions: ["scheduled", "cancelled"], user_allowed_transitions: ["scheduled", "cancelled"] })));
    renderWithClient(<WorkOrderSheet id="wo-1" onClose={() => {}} />);
    const group = await screen.findByRole("group", { name: "Ações da OS" });
    const names = within(group).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Programar", "Cancelar OS"]);
    const trail = screen.getByRole("list", { name: "Histórico de estados" });
    expect(trail).toHaveTextContent("Aprovada → Programada");
    expect(trail).toHaveTextContent("Paula Planejadora");
    expect(trail).toHaveTextContent("“semana 49”");
  });

  it("422 → shows the allowed list; schedule sends date + technician", async () => {
    const u = userEvent.setup();
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ state: "approved", allowed_transitions: ["scheduled", "cancelled"], user_allowed_transitions: ["scheduled", "cancelled"] })));
    vi.mocked(workOrdersApi.transition).mockRejectedValueOnce(http422(["scheduled", "cancelled"])).mockResolvedValueOnce(ok({ ...workOrder({ state: "scheduled" }), plans_reset: [] }));
    renderWithClient(<WorkOrderSheet id="wo-1" onClose={() => {}} />);
    await u.click(await screen.findByRole("button", { name: "Programar" }));
    const form = screen.getByRole("form", { name: "Programar" });
    await waitFor(() => expect(within(form).getByRole("option", { name: "Tiago Técnico" })).toBeInTheDocument());
    await u.clear(within(form).getByLabelText("Data"));
    await u.type(within(form).getByLabelText("Data"), "2026-12-03");
    await u.selectOptions(within(form).getByLabelText("Técnico responsável"), "u-tech");
    await u.click(within(form).getByRole("button", { name: "Programar" }));
    expect(await screen.findByText(/Transição não permitida no estado atual. Permitidas: Programada, Cancelada./)).toBeInTheDocument();
    await u.click(within(form).getByRole("button", { name: "Programar" }));
    await waitFor(() => expect(workOrdersApi.transition).toHaveBeenLastCalledWith("wo-1", expect.objectContaining({ to: "scheduled", scheduled_for: "2026-12-03", assignee_id: "u-tech" })));
  });

  it("cancel requires a reason; draft approval uses the one-click endpoint", async () => {
    const u = userEvent.setup();
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ state: "draft", allowed_transitions: ["open", "cancelled"], user_allowed_transitions: ["open", "cancelled"] })));
    vi.mocked(workOrdersApi.approveDraft).mockResolvedValue(ok(workOrder({ state: "approved" })));
    renderWithClient(<WorkOrderSheet id="wo-1" onClose={() => {}} />);
    await u.click(await screen.findByRole("button", { name: "Descartar" }));
    const form = screen.getByRole("form", { name: "Cancelar OS" });
    expect(within(form).getByRole("button", { name: "Cancelar OS" })).toBeDisabled();
    await u.click(screen.getByRole("button", { name: "Aprovar rascunho" }));
    await waitFor(() => expect(workOrdersApi.approveDraft).toHaveBeenCalledWith("wo-1"));
  });

  it("technician / supervisor see only their actions", async () => {
    useAuthStore.setState({ user: SUP });
    vi.mocked(workOrdersApi.get).mockResolvedValue(ok(workOrder({ state: "completed", allowed_transitions: ["closed"] })));
    renderWithClient(<WorkOrderSheet id="wo-1" onClose={() => {}} />);
    expect(await screen.findByRole("button", { name: "Fechar OS" })).toBeInTheDocument();
    useAuthStore.setState({ user: TECH });
  });
});

describe("Gestão da manutenção", () => {
  it("PMP, compliance with the excluded list and reasons, backlog in weeks", async () => {
    vi.mocked(maintenanceKpiApi.kpis).mockResolvedValue(ok({
      pmp: 0.6842105263157895, schedule_compliance: 0.5, backlog_hours: 18, backlog_weeks: 0.45, crew_capacity_hours_per_week: 40, overdue_count: 3,
      compliance_excluded: [{ wo_id: "w115", wo_number: "OS-115", reason: "cancelled_superseded" }, { wo_id: "w121", wo_number: "OS-121", reason: "scheduled_after_freeze" }],
    }));
    renderRoute(<KpisPage />, "/maintenance/kpis?month=2026-11", "/maintenance/kpis");
    expect(await screen.findByTestId("kpi-pmp")).toHaveTextContent(/^68,4\s%$/);
    expect(screen.getByTestId("kpi-compliance")).toHaveTextContent(/^50,0\s%$/);
    expect(screen.getByTestId("kpi-backlog")).toHaveTextContent("18,0");
    expect(screen.getByText(/0,45 semana\(s\) de trabalho/)).toBeInTheDocument();
    // PMP drill-down: the WOs completed in the month (completed_at in the plant-local month), any state
    const pmpLink = screen.getByTestId("kpi-pmp").closest("a")!;
    const href = new URL(pmpLink.getAttribute("href")!, "http://x");
    expect(href.searchParams.get("completed_from")).toBe("2026-11-01T04:00:00.000Z");
    expect(href.searchParams.get("completed_to")).toBe("2026-12-01T04:00:00.000Z");
    expect(href.searchParams.get("state")).toBeNull();
    const excluded = screen.getAllByTestId("excluded-row");
    expect(excluded[0]).toHaveTextContent("OS-115");
    expect(excluded[1]).toHaveTextContent("encaixe — programada depois do congelamento da semana");
    expect(maintenanceKpiApi.kpis).toHaveBeenCalledWith({ plant_id: "plant-1", start: "2026-11-01T04:00:00.000Z", stop: "2026-12-01T04:00:00.000Z" });
  });

  it("month range in the plant time zone; previous month", () => {
    expect(monthRange("2026-11", "America/Manaus")).toEqual({ start: "2026-11-01T04:00:00.000Z", stop: "2026-12-01T04:00:00.000Z", from: "2026-11-01", to: "2026-11-30" });
    expect(previousMonth("2026-12-01")).toBe("2026-11");
    expect(previousMonth("2027-01-15")).toBe("2026-12");
  });
});

describe("Confiabilidade", () => {
  it("shows '—' with the reason for null values and MTTF with failures/suspensions", async () => {
    const u = userEvent.setup();
    vi.mocked(maintenanceKpiApi.reliability).mockImplementation(async (q) => (q.group_by === "manufacturer_model"
      ? [{ key: "f", label: "Festo DSBC", n: 3, failures_removed: 1, suspensions: 2, in_service: 0, mttf: 3266666, mttf_measure: "cycles", small_sample: true, mtbf_h: null, mcbf: null, mttr_h: null, mttf_h: null, restored: 0, incipient: 0, null_reason: null }]
      : [
        { key: "a1", label: "Cilindro A1", n: 3, mtbf_h: 1733.3, mcbf: 2_000_000, mttr_h: 1.3889, mttf_h: null, restored: 3, incipient: 1, suspensions: 0, null_reason: null },
        { key: "a3", label: "Cilindro A3", n: 0, mtbf_h: null, mcbf: null, mttr_h: null, mttf_h: null, restored: 0, incipient: 0, suspensions: 0, null_reason: "no_failures" },
      ]) as never);
    renderRoute(<ReliabilityPage />, "/maintenance/reliability", "/maintenance/reliability");
    const rows = await screen.findAllByTestId("reliability-row");
    expect(rows[0]).toHaveTextContent("1.733 h");
    expect(rows[0]).toHaveTextContent("1 h 23 min");
    expect(rows[1]).toHaveTextContent("—");
    expect(rows[1]).toHaveTextContent("sem falhas no período");
    await u.click(screen.getByRole("tab", { name: /Fabricante \/ modelo/ }));
    expect(await screen.findByText("n = 1 falha, 2 suspensões")).toBeInTheDocument();
    expect(screen.getByText("amostra pequena")).toBeInTheDocument();
    expect(screen.getByText("3.266.666 ciclos")).toBeInTheDocument();
  });

  it("12-month window ending today 00:00 local", () => {
    expect(reliabilityWindow("2026-12-01", "America/Manaus")).toEqual({ start: "2025-12-01T04:00:00.000Z", stop: "2026-12-01T04:00:00.000Z" });
  });
});

describe("Plan form", () => {
  const base = {
    machine_id: "m-1", position_id: "POS-A1", name: "Vedações", tasks: ["Trocar kit", " "], estimated_hours: "1,5", wo_type: "preventive" as const,
    interval_cycles: "2.000.000", interval_hours: "", interval_starts: "", interval_days: "180", tolerance: "10",
    reset_on_replacement: true, stops_machine: true, is_active: true, last_done_at: "",
  };
  it("requires at least one trigger, positive numbers and hours", () => {
    expect(validatePlan(base)).toBeNull();
    expect(validatePlan({ ...base, interval_cycles: "", interval_days: "" })).toMatch(/pelo menos um gatilho/);
    expect(validatePlan({ ...base, interval_days: "-3" })).toMatch(/maiores que zero/);
    expect(validatePlan({ ...base, machine_id: "" })).toMatch(/máquina/);
  });
  it("builds the API body (position anchor, tolerance in %, checklist without blanks)", () => {
    expect(toBody(base)).toMatchObject({
      position_id: "POS-A1", machine_id: null, interval_cycles: 2_000_000, interval_days: 180, interval_hours: null,
      tolerance_pct: 10, estimated_hours: 1.5, task_list: [{ text: "Trocar kit" }],
    });
    expect(toBody({ ...base, position_id: "" }).machine_id).toBe("m-1");
  });
});
