import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PredictivePage from "./PredictivePage";
import PredictiveCard from "@/components/predictive/PredictiveCard";
import { ConditionPanel } from "@/components/predictive/parts";
import { plantsApi, positionsApi } from "@/services/api";
import { predictiveApi } from "@/services/predictiveApi";
import { useAuthStore } from "@/store/auth";
import { plantTree } from "@/test/fixtures";
import { TECH, user, workOrder } from "@/test/maintenanceFixtures";
import { alert, F1_TEXT, learningMonitor, monitor, series } from "@/test/predictiveFixtures";
import { ok, renderRoute, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  positionsApi: { list: vi.fn(), get: vi.fn() },
}));
vi.mock("@/services/predictiveApi", () => ({
  predictiveApi: {
    monitors: vi.fn(), monitor: vi.fn(), createMonitor: vi.fn(), updateMonitor: vi.fn(), rebaseline: vi.fn(),
    createRule: vi.fn(), updateRule: vi.fn(), deleteRule: vi.fn(), features: vi.fn(), alerts: vi.fn(), alert: vi.fn(),
    transition: vi.fn(), convert: vi.fn(), evaluation: vi.fn(),
  },
}));

beforeEach(() => {
  useAuthStore.setState({ user: user(), loading: false, offline: false });
  vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
  vi.mocked(positionsApi.list).mockResolvedValue(ok([]));
  vi.mocked(predictiveApi.alerts).mockResolvedValue(ok([
    alert(),
    alert({ id: "al-2", severity: "critical", position_name: "Cilindro A2", explanation: "Cilindro A2 — crítico." }),
    alert({ id: "al-3", severity: "watch", position_name: "Motor esteira", feature: "current", rul_condition: { rul_days: null, reason: "no_significant_trend" } }),
  ]));
  vi.mocked(predictiveApi.alert).mockResolvedValue(ok(alert()));
  vi.mocked(predictiveApi.features).mockResolvedValue(ok({ position_id: "POS-B3", series: [series()] }));
  vi.mocked(predictiveApi.monitors).mockResolvedValue(ok([monitor(), learningMonitor()]));
});

describe("Preditiva — alertas", () => {
  it("groups open alerts by severity (critical first) with explanation and RUL; no-trend says why", async () => {
    renderRoute(<PredictivePage />, "/maintenance/predictive", "/maintenance/predictive");
    const cards = await screen.findAllByTestId("pred-alert-card");
    expect(cards.map((c) => c.getAttribute("data-severity"))).toEqual(["critical", "warning", "watch"]);
    expect(cards[1]).toHaveTextContent(F1_TEXT.slice(0, 40));
    expect(cards[1]).toHaveTextContent("Falha em 11 dias · 11 dias (8–16)");
    expect(cards[2]).toHaveTextContent("Sem projeção — sem tendência de degradação significativa");
    expect(vi.mocked(predictiveApi.alerts).mock.calls[0][0]).toMatchObject({ state: ["open", "acknowledged", "converted"] });
    expect(screen.getByRole("group", { name: "Severidade" })).toHaveTextContent("Crítico1");
  });

  it("detail: explanation, evidence, chart; 'Gerar OS preditiva' converts and shows the WO", async () => {
    vi.mocked(predictiveApi.convert).mockResolvedValue(ok({ ...alert({ state: "converted", work_order_id: "wo-9", work_order_number: "OS-200" }), work_order: workOrder({ id: "wo-9", number: "OS-200", type: "predictive" }) }));
    const u = userEvent.setup();
    renderRoute(<PredictivePage />, "/maintenance/predictive?alert=al-1", "/maintenance/predictive");
    const detail = await screen.findByTestId("alert-detail");
    expect(within(detail).getByTestId("alert-explanation")).toHaveTextContent(F1_TEXT);
    expect(within(detail).getByTestId("alert-evidence")).toHaveTextContent("EWMA 1,34 > 1,00");
    expect(await within(detail).findByTestId("condition-chart")).toBeInTheDocument();
    expect(detail).toHaveTextContent("Falha projetada em 11 dias");
    await u.click(within(detail).getAllByRole("button", { name: "Gerar OS preditiva" })[0]);
    await u.type(screen.getByLabelText(/Horas estimadas/), "2,5");
    await u.click(within(screen.getByTestId("alert-actions")).getByRole("button", { name: "Gerar OS preditiva" }));
    await waitFor(() => expect(predictiveApi.convert).toHaveBeenCalledWith("al-1", { estimated_hours: 2.5, note: null }));
    expect(await screen.findByText(/OS preditiva OS-200 criada e aprovada/)).toBeInTheDocument();
  });

  it("closing requires a resolution and, for 'novo normal', a reason", async () => {
    vi.mocked(predictiveApi.transition).mockResolvedValue(ok(alert({ state: "closed", resolution: "rebaseline" })));
    const u = userEvent.setup();
    renderRoute(<PredictivePage />, "/maintenance/predictive?alert=al-1", "/maintenance/predictive");
    await u.click(await screen.findByRole("button", { name: "Fechar alerta…" }));
    await u.click(screen.getByRole("button", { name: "Fechar alerta" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escolha a resolução.");
    await u.click(screen.getByLabelText(/Novo normal/));
    await u.click(screen.getByRole("button", { name: "Fechar alerta" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Informe o motivo");
    await u.type(screen.getByLabelText("Motivo (obrigatório)"), "mudança de produto");
    await u.click(screen.getByRole("button", { name: "Fechar alerta" }));
    await waitFor(() => expect(predictiveApi.transition).toHaveBeenCalledWith("al-1", { to: "closed", resolution: "rebaseline", comment: "mudança de produto" }));
  });

  it("technician: can acknowledge, cannot convert or close (server list of allowed transitions)", async () => {
    useAuthStore.setState({ user: TECH });
    vi.mocked(predictiveApi.alert).mockResolvedValue(ok(alert({ user_allowed_transitions: ["acknowledged"] })));
    renderRoute(<PredictivePage />, "/maintenance/predictive?alert=al-1", "/maintenance/predictive");
    const actions = await screen.findByTestId("alert-actions");
    expect(within(actions).getByRole("button", { name: "Reconhecer" })).toBeInTheDocument();
    expect(within(actions).queryByRole("button", { name: "Gerar OS preditiva" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Monitorar condição" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Desempenho" })).toBeNull();
  });

  it("empty state is explicit", async () => {
    vi.mocked(predictiveApi.alerts).mockResolvedValue(ok([]));
    renderRoute(<PredictivePage />, "/maintenance/predictive", "/maintenance/predictive");
    expect(await screen.findByText("Nenhum alerta de condição em aberto")).toBeInTheDocument();
  });
});

describe("Preditiva — monitores e desempenho", () => {
  it("learning monitor shows progress instead of z/RUL", async () => {
    renderRoute(<PredictivePage />, "/maintenance/predictive?tab=monitors", "/maintenance/predictive");
    const rows = await screen.findAllByTestId("monitor-row");
    expect(rows[0]).toHaveTextContent("Monitorando");
    expect(rows[0]).toHaveTextContent("+4,00");
    expect(rows[1]).toHaveTextContent("aprendendo (12/20 janelas)");
    expect(within(rows[1]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "60");
  });

  it("monitor sheet: add a % rule (110 → 1.10)", async () => {
    vi.mocked(predictiveApi.monitor).mockResolvedValue(ok(monitor()));
    vi.mocked(predictiveApi.createRule).mockResolvedValue(ok(monitor().rules![0]));
    const u = userEvent.setup();
    renderRoute(<PredictivePage />, "/maintenance/predictive?tab=monitors&monitor=mon-1", "/maintenance/predictive");
    await u.click(await screen.findByRole("button", { name: "Nova regra" }));
    const form = screen.getByTestId("new-rule");
    await u.type(within(form).getByLabelText(/% da referência/), "115");
    await u.click(within(form).getByRole("button", { name: "Adicionar regra" }));
    await waitFor(() => expect(predictiveApi.createRule).toHaveBeenCalledWith({ monitor_id: "mon-1", type: "pct_of_baseline", value: 1.15, sustained_for: 2, severity: "warning" }));
  });

  it("evaluation tiles read the P-10 metrics", async () => {
    vi.mocked(predictiveApi.evaluation).mockResolvedValue(ok({ precision_warning: 1, precision: 0.5, detection_rate: 1, failures_total: 1, failures_detected: 1, lead_time_h_median_warning: 420, false_alarms_per_position_year_warning: 0, false_alarms_per_position_year: 4.97, rul_band_coverage: 0.8, rul_band_checkpoints: 5, alerts: 2, horizon_days: 30 }));
    renderRoute(<PredictivePage />, "/maintenance/predictive?tab=evaluation", "/maintenance/predictive");
    expect(await screen.findByTestId("eval-precision")).toHaveTextContent(/100,0\s%/);
    expect(screen.getByText("17,5 dias")).toBeInTheDocument();
    expect(screen.getByText(/Em Atenção: 5,0/)).toBeInTheDocument();
  });
});

describe("Condição na gaveta e no Raio-X", () => {
  it("ConditionPanel without monitors says so", async () => {
    vi.mocked(predictiveApi.monitors).mockResolvedValue(ok([]));
    renderWithClient(<ConditionPanel positionId="POS-X" />);
    expect(await screen.findByText("Sem monitoramento de condição")).toBeInTheDocument();
  });
  it("ConditionPanel in learning never shows a z-score", async () => {
    vi.mocked(predictiveApi.monitors).mockResolvedValue(ok([learningMonitor()]));
    vi.mocked(predictiveApi.features).mockResolvedValue(ok({ position_id: "POS-M1", series: [] }));
    renderWithClient(<ConditionPanel positionId="POS-M1" />);
    expect(await screen.findByTestId("monitor-learning")).toHaveTextContent("aprendendo (12/20 janelas)");
    expect(screen.queryByText("Desvio (z)")).toBeNull();
  });
  it("Raio-X card answers 'o que está degradando'", async () => {
    renderWithClient(<PredictiveCard />);
    const card = await screen.findByTestId("raiox-predictive");
    expect(await within(card).findByText(/1 crítico/)).toBeInTheDocument();
    expect(within(card).getAllByRole("link")[1]).toHaveAttribute("href", "/maintenance/predictive?alert=al-2");
  });
  it("Raio-X card hidden for operators", () => {
    useAuthStore.setState({ user: user({ role: "operator", roles: ["operator"] }) });
    const { container } = renderWithClient(<PredictiveCard />);
    expect(container).toBeEmptyDOMElement();
    expect(predictiveApi.alerts).not.toHaveBeenCalled();
  });
});
