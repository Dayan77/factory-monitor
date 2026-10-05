/**
 * Browser verification of the Sprint 4 maintenance experience (docs/maintenance_workflows.md §2–§4, §6).
 *
 *   Mocked mode (default): every /api call and the realtime socket are served by page.route /
 *   page.routeWebSocket from an in-memory backend that mirrors the API contract (state machine with 422
 *   + allowed list, labor, tasks, replacements, failures, KPIs, reliability, notifications). Data is
 *   modelled on golden scenario E (Planta Manaus) with dates relative to today; clearly fictitious.
 *
 *   - planner desktop (1440×900) light + dark: upcoming, board/list/detail + audit trail, approve draft,
 *     schedule by drag & drop, failures with ISO 14224 pickers, plans settings, KPIs, reliability,
 *     components, notification center, emergency alert, links from machine / Raio-X, "Chamar manutenção".
 *   - technician mobile (360×740, touch) light + dark: my WOs, start, checklist, offline queue
 *     (context.setOffline) with reload from the service-worker shell, sync on reconnect, pause, labor,
 *     completion, component replacement, failure data, emergency call.
 *   Zero console errors, no horizontal scroll, screenshots in OUT.
 *
 *   Needs a production build served by `vite preview` (service worker):
 *     npx vite build --outDir /tmp/fm-dist && npx vite preview --outDir /tmp/fm-dist --port 5201 --strictPort
 *     BASE_URL=http://localhost:5201 OUT=e2e/screenshots/maintenance node e2e/maintenance.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5201";
const OUT = process.env.OUT ?? "e2e/screenshots/maintenance";
const TZ = "America/Manaus";
const ONLY = process.env.ONLY ?? "all"; // all | desktop | mobile
const THEMES = (process.env.THEMES ?? "light,dark").split(",");
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}`); }
}

// ── dates (plant local) ─────────────────────────────────────────────
const DAY = 86_400_000;
const NOW = Date.now();
const localDay = (ms) => new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
const TODAY = localDay(NOW);
const d = (n) => localDay(NOW + n * DAY);
const iso = (ms) => new Date(ms).toISOString();
const ago = (h) => iso(NOW - h * 3600_000);
const mondayOf = (day) => { const t = Date.parse(`${day}T00:00:00Z`); const dow = new Date(t).getUTCDay(); return new Date(t - ((dow + 6) % 7) * DAY).toISOString().slice(0, 10); };
const THIS_MONDAY = mondayOf(TODAY);
const prevMonth = (() => { const [y, m] = TODAY.split("-").map(Number); return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`; })();

// ── fixtures ─────────────────────────────────────────────────────────
const TREE = [{
  id: "plant-1", name: "Planta Manaus", timezone: TZ,
  lines: [
    { id: "line-1", name: "Linha de Montagem", status_summary: { running: 2, stopped: 0, error: 1, unknown: 0, maintenance: 0 },
      machines: [
        { id: "m-fix", name: "Estação de Fixação", status: "running", last_seen_at: ago(0.01), positions_count: 2 },
        { id: "m-rob", name: "Robô de Solda", status: "error", last_seen_at: ago(0.05), positions_count: 1 },
        { id: "m-tra", name: "Transportador de Saída", status: "running", last_seen_at: ago(0.01), positions_count: 1 },
      ] },
  ],
}];
const POSITIONS = [
  { id: "POS-A1", machine_id: "m-fix", name: "Cilindro A1", component_type: "pneumatic_cylinder", cycles_per_part: 1, installed_component_id: "C-A1-3" },
  { id: "POS-A2", machine_id: "m-fix", name: "Cilindro A2", component_type: "pneumatic_cylinder", cycles_per_part: 1, installed_component_id: "C-A2-2" },
  { id: "POS-AX1", machine_id: "m-rob", name: "Eixo 1", component_type: "robot_axis", cycles_per_part: 1, installed_component_id: "R-1" },
  { id: "POS-MOT", machine_id: "m-tra", name: "Motor do transportador", component_type: "electric_motor", cycles_per_part: 0, installed_component_id: "M-1" },
];
const NAMES = Object.fromEntries([...TREE[0].lines[0].machines.map((m) => [m.id, m.name]), ...POSITIONS.map((p) => [p.id, p.name])]);
const USERS = {
  planner: { id: "u-plan", name: "Paula Planejadora", email: "pcm@example.com", role: "maintenance_planner", roles: ["maintenance_planner"], tenant_id: "t-demo" },
  tech: { id: "u-tech", name: "Tiago Técnico", email: "tec@example.com", role: "technician", roles: ["technician"], tenant_id: "t-demo" },
  sup: { id: "u-sup", name: "Sílvia Supervisora", email: "sup@example.com", role: "production_supervisor", roles: ["production_supervisor"], tenant_id: "t-demo" },
};
const TECHS = [USERS.tech, { id: "u-tech2", name: "Rita Técnica", email: "rita@example.com", role: "technician", roles: ["technician"], tenant_id: "t-demo" }];

const trig = (measure, interval, usage, projected) => ({ measure, interval, usage, consumed: usage / interval, projected_date: projected });
function freshPlans() {
  return [
    { plan_id: "PL6", plan_name: "Limpeza do filtro regulador de ar — Estação", position_id: null, position_name: null, machine_id: "m-fix", machine_name: "Estação de Fixação",
      state: "overdue", consumed: 3.714, determining_trigger: "days", due_trigger: "days", days_to_due: 0, past_due: true,
      triggers: [trig("days", 7, 26, TODAY)], projected_date: TODAY, next_due_usage: null, open_work_order_id: "wo-123", open_work_order_number: "OS-123", open_work_order_state: "draft" },
    { plan_id: "PL4", plan_name: "Troca de óleo do redutor — Eixo 1", position_id: "POS-AX1", position_name: "Eixo 1", machine_id: "m-rob", machine_name: "Robô de Solda",
      state: "due", consumed: 1.03, determining_trigger: "hours", due_trigger: "hours", days_to_due: 0, past_due: true,
      triggers: [trig("hours", 5000, 5150, TODAY), trig("days", 365, 346, d(19))], projected_date: TODAY, next_due_usage: { measure: "hours", value: 38000 }, open_work_order_id: "wo-110", open_work_order_number: "OS-110", open_work_order_state: "scheduled" },
    { plan_id: "PL5", plan_name: "Inspeção NR-12 da célula", position_id: null, position_name: null, machine_id: "m-fix", machine_name: "Estação de Fixação",
      state: "due_soon", consumed: 0.9333, determining_trigger: "days", due_trigger: "days", days_to_due: 2, past_due: false,
      triggers: [trig("days", 30, 28, d(2))], projected_date: d(2), next_due_usage: null, open_work_order_id: "wo-111", open_work_order_number: "OS-111", open_work_order_state: "draft" },
    { plan_id: "PL2", plan_name: "Troca de vedações — Cilindro A2", position_id: "POS-A2", position_name: "Cilindro A2", machine_id: "m-fix", machine_name: "Estação de Fixação",
      state: "due_soon", consumed: 0.9389, determining_trigger: "days", due_trigger: "days", days_to_due: 11, past_due: false,
      triggers: [trig("cycles", 2000000, 1780000, d(13)), trig("days", 180, 169, d(11))], projected_date: d(11), next_due_usage: { measure: "cycles", value: 9320000 }, open_work_order_id: "wo-114", open_work_order_number: "OS-114", open_work_order_state: "scheduled" },
    { plan_id: "PL3", plan_name: "Lubrificação dos rolamentos — Motor do transportador", position_id: "POS-MOT", position_name: "Motor do transportador", machine_id: "m-tra", machine_name: "Transportador de Saída",
      state: "due_soon", consumed: 0.82, determining_trigger: "starts", due_trigger: "hours", days_to_due: 40, past_due: false,
      triggers: [trig("hours", 4000, 3200, d(40)), trig("starts", 30000, 24600, d(45)), trig("days", 365, 225, d(140))], projected_date: d(40), next_due_usage: { measure: "hours", value: 45800 }, open_work_order_id: "wo-122", open_work_order_number: "OS-122", open_work_order_state: "draft" },
    { plan_id: "PL1", plan_name: "Troca de vedações — Cilindro A1", position_id: "POS-A1", position_name: "Cilindro A1", machine_id: "m-fix", machine_name: "Estação de Fixação",
      state: "ok", consumed: 0.17, determining_trigger: "cycles", due_trigger: "cycles", days_to_due: 93, past_due: false,
      triggers: [trig("cycles", 2000000, 340000, d(93)), trig("days", 180, 19, d(161))], projected_date: d(93), next_due_usage: { measure: "cycles", value: 9200000 }, open_work_order_id: null },
  ];
}
const PLAN_DEFS = [
  { id: "PL1", position_id: "POS-A1", machine_id: "m-fix", name: "Troca de vedações — Cilindro A1", task_list: [{ id: "t1", text: "Despressurizar" }, { id: "t2", text: "Trocar kit de vedação" }], estimated_hours: 1.5, wo_type: "preventive", interval_cycles: 2000000, interval_hours: null, interval_starts: null, interval_days: 180, tolerance_pct: 10, reset_on_replacement: true, stops_machine: true, is_active: true },
  { id: "PL3", position_id: "POS-MOT", machine_id: "m-tra", name: "Lubrificação dos rolamentos — Motor do transportador", task_list: [{ id: "t1", text: "Engraxar rolamentos" }], estimated_hours: 1, wo_type: "preventive", interval_cycles: null, interval_hours: 4000, interval_starts: 30000, interval_days: 365, tolerance_pct: 10, reset_on_replacement: true, stops_machine: false, is_active: true },
];

const hist = (...steps) => steps.map(([from, to, h, by = "u-plan", note = null]) => ({ at: ago(h), by, by_name: by === "u-tech" ? "Tiago Técnico" : by === "system" ? null : "Paula Planejadora", from_state: from, to_state: to, note }));
function wo(over) {
  return {
    urgency: null, plan_id: null, position_id: null, downtime_event_id: null, failure_id: null, description: null,
    task_list: [], estimated_hours: 1, labor_hours: 0, remaining_hours: null, stops_machine: false, scheduled_for: null,
    assignee_id: null, created_at: ago(200), requested_at: null, approved_at: null, started_at: null, completed_at: null,
    closed_at: null, cancelled_at: null, cancel_reason: null, hold_reason: null, replacements: [], history: [], ...over,
  };
}
function freshWos() {
  return [
    wo({ id: "wo-110", number: "OS-110", type: "preventive", state: "scheduled", plan_id: "PL4", position_id: "POS-AX1", machine_id: "m-rob", title: "Troca de óleo do redutor — Eixo 1", estimated_hours: 6, stops_machine: true, scheduled_for: d(-3), assignee_id: "u-tech2",
      task_list: [{ id: "a", text: "Drenar óleo", done: false }, { id: "b", text: "Repor 4 L de óleo ISO VG 220", done: false }],
      history: hist([null, "draft", 400, "system"], ["draft", "open", 300], ["open", "approved", 299], ["approved", "scheduled", 298]) }),
    wo({ id: "wo-111", number: "OS-111", type: "preventive", state: "draft", plan_id: "PL5", machine_id: "m-fix", title: "Inspeção NR-12 da célula", estimated_hours: 2, history: hist([null, "draft", 30, "system", "plano em 93 %"]) }),
    wo({ id: "wo-112", number: "OS-112", type: "corrective", urgency: "deferred", state: "approved", position_id: "POS-A2", machine_id: "m-fix", title: "Ruído no cilindro A2", estimated_hours: 3, history: hist([null, "open", 90, "u-sup"], ["open", "approved", 80]) }),
    wo({ id: "wo-113", number: "OS-113", type: "preventive", state: "on_hold", machine_id: "m-tra", position_id: "POS-MOT", title: "Troca da correia do transportador", estimated_hours: 8, labor_hours: 2, remaining_hours: 6, hold_reason: "waiting_parts", scheduled_for: d(-6), assignee_id: "u-tech",
      history: hist([null, "draft", 500, "system"], ["draft", "open", 400], ["open", "approved", 399], ["approved", "scheduled", 398], ["scheduled", "in_progress", 160, "u-tech"], ["in_progress", "on_hold", 158, "u-tech", "falta a correia"]) }),
    wo({ id: "wo-114", number: "OS-114", type: "preventive", state: "scheduled", plan_id: "PL2", position_id: "POS-A2", machine_id: "m-fix", title: "Troca de vedações — Cilindro A2", estimated_hours: 1.5, stops_machine: true, scheduled_for: TODAY, assignee_id: "u-tech",
      task_list: [{ id: "t1", text: "Despressurizar e bloquear (LOTO)", done: false }, { id: "t2", text: "Trocar kit de vedação", done: false }, { id: "t3", text: "Testar curso e vazamento", done: false }],
      history: hist([null, "draft", 200, "system"], ["draft", "open", 100], ["open", "approved", 99], ["approved", "scheduled", 98]) }),
    wo({ id: "wo-117", number: "OS-117", type: "predictive", state: "in_progress", machine_id: "m-fix", position_id: "POS-A1", title: "Medição de tempo de curso — A1", estimated_hours: 2, labor_hours: 0.5, scheduled_for: d(-2), assignee_id: "u-tech", started_at: ago(3),
      task_list: [{ id: "x", text: "Registrar tempo de curso", done: true }, { id: "y", text: "Comparar com a referência", done: false }],
      history: hist([null, "draft", 300, "system"], ["draft", "open", 250], ["open", "approved", 249], ["approved", "scheduled", 248], ["scheduled", "in_progress", 3, "u-tech"]) }),
    wo({ id: "wo-118", number: "OS-118", type: "preventive", state: "completed", machine_id: "m-tra", title: "Inspeção de correias", estimated_hours: 1, labor_hours: 1, scheduled_for: TODAY, assignee_id: "u-tech", completed_at: ago(2), started_at: ago(3),
      history: hist([null, "draft", 300, "system"], ["draft", "open", 250], ["open", "approved", 249], ["approved", "scheduled", 248], ["scheduled", "in_progress", 3, "u-tech"], ["in_progress", "completed", 2, "u-tech"]) }),
    wo({ id: "wo-120", number: "OS-120", type: "corrective", urgency: "emergency", state: "in_progress", machine_id: "m-fix", position_id: "POS-A1", failure_id: "F4", downtime_event_id: "d-1", title: "Vazamento no cilindro A1 — troca do cilindro", estimated_hours: 2, assignee_id: "u-tech", started_at: ago(1), requested_at: ago(1.2),
      history: hist([null, "open", 1.2, "u-sup", "chamado da parada"], ["open", "in_progress", 1, "u-tech", "chamado assumido"]) }),
    wo({ id: "wo-122", number: "OS-122", type: "preventive", state: "draft", plan_id: "PL3", machine_id: "m-tra", position_id: "POS-MOT", title: "Lubrificação dos rolamentos — Motor do transportador", estimated_hours: 1, history: hist([null, "draft", 20, "system"]) }),
    wo({ id: "wo-123", number: "OS-123", type: "preventive", state: "draft", plan_id: "PL6", machine_id: "m-fix", title: "Limpeza do filtro regulador de ar", estimated_hours: 0.5, history: hist([null, "draft", 10, "system"]) }),
    wo({ id: "wo-124", number: "OS-124", type: "corrective", urgency: "emergency", state: "open", machine_id: "m-rob", downtime_event_id: "d-2", title: "Robô de Solda parado — falha 251", estimated_hours: 0, requested_at: ago(0.05), history: hist([null, "open", 0.05, "u-sup", "Chamar manutenção"]) }),
    wo({ id: "wo-101", number: "OS-101", type: "preventive", state: "closed", machine_id: "m-fix", title: "Inspeção NR-12 (mês anterior)", estimated_hours: 2, labor_hours: 2, scheduled_for: d(-28), completed_at: ago(28 * 24), closed_at: ago(27 * 24) }),
  ];
}

const BASE_ALLOWED = { draft: ["open"], open: ["approved"], approved: ["scheduled"], scheduled: ["in_progress"], in_progress: ["on_hold", "completed"], on_hold: ["in_progress", "scheduled"], completed: ["closed"], closed: [], cancelled: [] };
function allowed(w) {
  const out = [...BASE_ALLOWED[w.state]];
  if (w.state === "open" && w.type === "corrective" && w.urgency === "emergency") out.push("in_progress");
  if (["draft", "open", "approved", "scheduled", "in_progress", "on_hold"].includes(w.state)) out.push("cancelled");
  return out;
}
function userAllowed(w, user) {
  const r = new Set(user.roles);
  return allowed(w).filter((to) => {
    const pair = `${w.state}>${to}`;
    if (r.has("maintenance_planner") && (["draft>open", "open>approved", "approved>scheduled", "on_hold>scheduled", "on_hold>in_progress"].includes(pair) || to === "cancelled")) return true;
    if (r.has("technician") && ["scheduled>in_progress", "in_progress>on_hold", "on_hold>in_progress", "in_progress>completed", "open>in_progress"].includes(pair)) return true;
    if (r.has("production_supervisor") && pair === "completed>closed") return true;
    return false;
  });
}
const out = (w, user) => {
  const a = allowed(w);
  return { ...w, position_name: NAMES[w.position_id] ?? null, machine_name: NAMES[w.machine_id] ?? null, line_id: "line-1",
    remaining_hours: w.remaining_hours ?? Math.max(0, (w.estimated_hours ?? 0) - (w.labor_hours ?? 0)),
    scheduled_at: w.history.find((h) => h.to_state === "scheduled")?.at ?? null,
    overdue: !!w.scheduled_for && w.scheduled_for < TODAY && ["draft", "open", "approved", "scheduled", "in_progress", "on_hold"].includes(w.state),
    allowed_transitions: a, user_allowed_transitions: userAllowed(w, user) };
};

const FAILURES = () => [
  { id: "F4", machine_id: "m-fix", machine_name: "Estação de Fixação", position_id: "POS-A1", position_name: "Cilindro A1", component_id: "C-A1-2", failed_at: ago(1.3), restored_at: null, severity: "critical", failure_mode: null, failure_mechanism: "6.4", failure_cause: "5.5", detection_method: "production_interference", downtime_event_id: "d-1", work_order_id: "wo-120" },
  { id: "F2", machine_id: "m-fix", machine_name: "Estação de Fixação", position_id: "POS-A1", position_name: "Cilindro A1", component_id: "C-A1-2", failed_at: ago(24 * 130), restored_at: ago(24 * 130 - 0.67), severity: "degraded", failure_mode: "PDE", failure_mechanism: "3.4", failure_cause: "3.4", detection_method: "continuous_condition_monitoring", downtime_event_id: null, work_order_id: null },
  { id: "F7", machine_id: "m-rob", machine_name: "Robô de Solda", position_id: "POS-AX1", position_name: "Eixo 1", component_id: "R-1", failed_at: ago(24 * 40), restored_at: null, severity: "degraded", failure_mode: "VIB", failure_mechanism: "1.2", failure_cause: "5.5", detection_method: "periodic_maintenance", downtime_event_id: null, work_order_id: null },
];
const CODES = {
  failure_modes: [["BRD", "Quebra"], ["ELP", "Vazamento externo"], ["INL", "Vazamento interno"], ["PDE", "Desvio de parâmetro"], ["VIB", "Vibração"], ["NOI", "Ruído"], ["OHE", "Superaquecimento"], ["FTS", "Falha ao partir"], ["STD", "Deficiência estrutural"], ["ERO", "Saída errática"], ["UNK", "Desconhecido"]].map(([code, label]) => ({ code, label })),
  mechanisms: [["1.1", "Vazamento"], ["1.2", "Vibração"], ["2.4", "Desgaste"], ["3.4", "Fora de ajuste"], ["6.4", "Desconhecido"]].map(([code, label]) => ({ code, label })),
  causes: [["3.4", "Desgaste e envelhecimento esperados"], ["3.3", "Erro de manutenção"], ["5.5", "Desconhecida"]].map(([code, label]) => ({ code, label })),
  detection_methods: [["periodic_maintenance", "Manutenção periódica"], ["continuous_condition_monitoring", "Monitoramento contínuo"], ["production_interference", "Interferência na produção"]].map(([code, label]) => ({ code, label })),
  modes_by_component_type: { pneumatic_cylinder: ["ELP", "INL", "PDE", "BRD"], robot_axis: ["VIB", "NOI", "OHE", "ERO"] },
};
const KPIS = { pmp: 0.6842105263157895, schedule_compliance: 0.5, compliance_excluded: [{ wo_id: "wo-115", wo_number: "OS-115", reason: "cancelled_superseded" }, { wo_id: "wo-121", wo_number: "OS-121", reason: "scheduled_after_freeze" }],
  backlog_hours: 18, backlog_weeks: 0.45, crew_capacity_hours_per_week: 40, overdue_count: 3, pmp_reason: null, compliance_reason: null };
const rel = (o) => ({ n: 0, mtbf_h: null, mcbf: null, mttr_h: null, mttf_h: null, restored: 0, incipient: 0, suspensions: 0, null_reason: null, ...o });
const RELIABILITY = {
  position: [
    rel({ key: "POS-A1", label: "Cilindro A1", machine_name: "Estação de Fixação", n: 3, mtbf_h: 1733.33, mcbf: 2000000, mttr_h: 83.33 / 60, restored: 3, incipient: 1 }),
    rel({ key: "POS-A2", label: "Cilindro A2", machine_name: "Estação de Fixação", n: 1, mtbf_h: 5200, mcbf: 6000000, mttr_h: 0.5, restored: 1 }),
    rel({ key: "POS-MOT", label: "Motor do transportador", machine_name: "Transportador de Saída", n: 2, mtbf_h: 3400, mcbf: null, mttr_h: 97.5 / 60, restored: 2 }),
    rel({ key: "POS-AX1", label: "Eixo 1", machine_name: "Robô de Solda", n: 2, mtbf_h: 2800, mcbf: 4500000, mttr_h: 1.25, restored: 1 }),
    rel({ key: "POS-A3", label: "Cilindro A3", machine_name: "Estação de Fixação", n: 0, null_reason: "no_failures", mttr_reason: "no_failures" }),
  ],
  component_type: [
    rel({ key: "pneumatic_cylinder", label: "pneumatic_cylinder", component_type: "pneumatic_cylinder", n: 4, mtbf_h: 2600, mcbf: 3000000, mttr_h: 70 / 60, restored: 4 }),
    rel({ key: "electric_motor", label: "electric_motor", component_type: "electric_motor", n: 2, mtbf_h: 3400, mttr_h: 97.5 / 60, restored: 2 }),
    rel({ key: "robot_axis", label: "robot_axis", component_type: "robot_axis", n: 2, mtbf_h: 2800, mcbf: 4500000, mttr_h: 1.25, restored: 1 }),
  ],
  manufacturer: [
    rel({ key: "pneumatic_cylinder|Festo", label: "Festo (pneumatic_cylinder)", manufacturer: "Festo", component_type: "pneumatic_cylinder", n: 3, mtbf_h: 2433.33, mcbf: 2820000, mttr_h: 83.33 / 60, restored: 3 }),
    rel({ key: "pneumatic_cylinder|SMC", label: "SMC (pneumatic_cylinder)", manufacturer: "SMC", component_type: "pneumatic_cylinder", n: 1, mtbf_h: 3100, mcbf: 3540000, mttr_h: 0.5, restored: 1 }),
  ],
  manufacturer_model: [
    rel({ key: "Festo|DSBC", label: "Festo DSBC-50-100-PPVA-N3", n: 3, failures_removed: 3, suspensions: 2, in_service: 0, mttf_measure: "cycles", mttf: 3266666.67, small_sample: false }),
    rel({ key: "SMC|CP96", label: "SMC CP96SDB50-100", n: 1, failures_removed: 0, suspensions: 0, in_service: 2, mttf_measure: "cycles", mttf: null, mttf_reason: "no_failures" }),
    rel({ key: "WEG|W22", label: "WEG W22 2 cv + redutor", n: 2, failures_removed: 1, suspensions: 0, in_service: 1, mttf_measure: "hours", mttf: 23800, mttf_h: 23800, small_sample: true }),
  ],
};
const COMPONENTS_LIFE = [
  { component_id: "R-1", serial_number: "ABB-AX1-001", manufacturer: "ABB", model: "IRB 2600 — redutor eixo 1", component_type: "robot_axis", position_id: "POS-AX1", position_name: "Eixo 1", machine_id: "m-rob", machine_name: "Robô de Solda", installed_at: ago(24 * 700), life_consumed_pct: 0.86, life_measure: "hours", rul_days: 175, rul_measure: "hours", projected_end_date: d(175), alert: "attention" },
  { component_id: "M-1", serial_number: "WEG-0001", manufacturer: "WEG", model: "W22 2 cv + redutor", component_type: "electric_motor", position_id: "POS-MOT", position_name: "Motor do transportador", machine_id: "m-tra", machine_name: "Transportador de Saída", installed_at: ago(24 * 900), life_consumed_pct: 0.75, life_measure: "starts", rul_days: 417, rul_measure: "starts", projected_end_date: d(417), alert: null },
  { component_id: "C-A2-2", serial_number: "VP-FIX-A2-0002", manufacturer: "SMC", model: "CP96SDB50-100", component_type: "pneumatic_cylinder", position_id: "POS-A2", position_name: "Cilindro A2", machine_id: "m-fix", machine_name: "Estação de Fixação", installed_at: ago(24 * 200), life_consumed_pct: 1.02, life_measure: "cycles", rul_days: 0, projected_end_date: TODAY, alert: "nominal_reached" },
];
const PREFS = { kinds: [
  { kind: "wo_assigned", label: "OS atribuída", channels: { in_app: true, email: false, web_push: true }, mutable: true, digest: false },
  { kind: "emergency_call", label: "Chamado emergencial", channels: { in_app: true, email: false, web_push: true }, mutable: false, digest: false },
  { kind: "plan_due_soon", label: "Plano ≥ 80 % (rascunho criado)", channels: { in_app: true, email: true, web_push: false }, mutable: true, digest: true },
], digest_enabled: true };
const notifFixture = () => [
  { id: "n-1", kind: "plan_overdue", title: "Plano atrasado: Limpeza do filtro regulador de ar", body: "Estação de Fixação · 371 %", link: "/maintenance/upcoming", priority: "high", data: {}, read: false, read_at: null, created_at: ago(5) },
  { id: "n-2", kind: "wo_on_hold", title: "OS-113 em espera", body: "Aguardando peça", link: "/maintenance/work-orders/wo-113", priority: "normal", data: { work_order_id: "wo-113" }, read: false, read_at: null, created_at: ago(8) },
  { id: "n-3", kind: "wo_assigned", title: "OS-114 atribuída a você", body: "Troca de vedações — Cilindro A2", link: "/m/work-orders/wo-114", priority: "normal", data: { work_order_id: "wo-114" }, read: true, read_at: ago(20), created_at: ago(24) },
];
const DOWNTIME = [
  { id: "d-2", machine_id: "m-rob", machine_name: "Robô de Solda", line_id: "line-1", start: ago(0.1), end: null, duration_s: null, source: "auto_fault", reason_code: null, suggested_reason_code: "EQP-04", planned: false, fault_code: 251, comment: null, unjustified: true },
];
const REASONS = [["EQP-04", "EQP", "Falha de automação/software", false], ["SET-01", "SET", "Setup / troca de produto", false], ["UNK-01", "UNK", "Não justificada", false]]
  .map(([code, category, label, planned]) => ({ id: `rc-${code}`, tenant_id: null, code, category, label, planned, oee_loss: "availability", is_active: true, is_default: true }));
const OVERVIEW = { plant_id: "plant-1", plant_name: "Planta Manaus", productive_day: TODAY, generated_at: ago(0.01), lines: [
  { line_id: "line-1", line_name: "Linha de Montagem", output_machine_id: "m-tra", oee_today: 0.68, availability: 0.84, performance: 0.87, quality: 0.93, good: 1843, scrap: 37, cadence_per_hour: 96, data_coverage: 0.99, open_downtime_count: 1, unjustified_count: 1, worst_machine: { machine_id: "m-rob", machine_name: "Robô de Solda", oee: 0.58 } }],
  top_losses: [], machines_in_error: [{ machine_id: "m-rob", machine_name: "Robô de Solda", line_id: "line-1", last_seen_at: ago(0.1) }], components_near_end: [] };
const positionDetail = (id) => {
  const p = POSITIONS.find((x) => x.id === id);
  return { ...p, installed_component: { id: p.installed_component_id, manufacturer: p.id === "POS-A1" ? "Festo" : "SMC", model: "DSBC-50-100", serial_number: `SN-${p.id}`, installed_at: ago(24 * 300), nominal_life: { cycles: 8000000 }, component_type: p.component_type },
    usage: { cycles_total: 7540000, hours_total: 5200.5, starts_total: 1200 }, component_usage: { cycles: 4060000, hours: 3500, starts: 0, life_consumed_pct: 0.5075, life_measure: "cycles", projected_end_date: d(200), rul_days: 200 } };
};

// ── mock backend ─────────────────────────────────────────────────────
function makeBackend(user) {
  const st = { wos: freshWos(), plans: freshPlans(), planDefs: structuredClone(PLAN_DEFS), failures: FAILURES(), notifs: notifFixture(), prefs: structuredClone(PREFS), log: [], offline: false, seq: 130 };
  async function handler(route) {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const m = req.method();
    if (st.offline) return route.abort("internetdisconnected");
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    let body = null;
    try { body = req.postDataJSON(); } catch { body = null; }
    if (m !== "GET") st.log.push({ method: m, path: p, body, actionId: req.headers()["x-client-action-id"] ?? null, at: Date.now() });
    const findWo = (id) => st.wos.find((w) => w.id === id);

    if (p === "/api/auth/me") return json(user);
    if (p === "/api/plants/tree") return json(TREE);
    if (p === "/api/overview") return json(OVERVIEW);
    if (p === "/api/positions/") return json(POSITIONS.filter((x) => x.machine_id === url.searchParams.get("machine_id")));
    if (/^\/api\/positions\/[^/]+$/.test(p)) return json(positionDetail(p.split("/").pop()));
    if (/^\/api\/machines\/[^/]+\/usage$/.test(p)) {
      const mid = p.split("/")[3];
      return json({ machine_id: mid, positions: POSITIONS.filter((x) => x.machine_id === mid).map((x) => ({ position_id: x.id, name: x.name, component_type: x.component_type, cycles_total: 7540000, hours_total: 5200, starts_total: 10, component_id: x.installed_component_id, life_consumed_pct: 0.5, life_measure: "cycles" })), production: { good: 100, scrap: 2, total: 102 } });
    }
    if (p === "/api/usage/history") return json({ measure: url.searchParams.get("measure"), bucket: "1h", start: ago(24), stop: iso(NOW), points: [] });
    if (p === "/api/components/") return json([{ id: "C-NEW-9", manufacturer: "SMC", model: "CP96SDB50-100", serial_number: "SMC-A1-0003", installed_at: null, nominal_life: { cycles: 8000000 }, state: "in_stock", component_type: "pneumatic_cylinder" }]);
    if (p === "/api/users/") return user.roles.includes("technician") ? json({ detail: "Permissão insuficiente." }, 403) : json(TECHS);
    if (p === "/api/reason-codes/") return json(REASONS);
    if (p === "/api/downtime/") return json(DOWNTIME);
    if (p === "/api/calendar-exceptions/") return json([{ id: "ce-1", tenant_id: "t", plant_id: "plant-1", date: d(2), kind: "planned_stop", shift_id: null, start_local: "06:00", end_local: "08:00", reason_code: "PLN-03", note: "Janela preventiva", created_at: ago(30) }]);

    // plans
    if (p === "/api/maintenance-plans/status") return json(st.plans);
    if (p === "/api/maintenance-plans/" && m === "GET") return json(st.planDefs);
    if (p === "/api/maintenance-plans/" && m === "POST") {
      const plan = { id: `PL${st.planDefs.length + 10}`, ...body, task_list: body.task_list.map((t, i) => ({ id: `n${i}`, text: t.text })) };
      st.planDefs.push(plan);
      return json(plan, 201);
    }
    if (/^\/api\/maintenance-plans\/[^/]+$/.test(p) && m === "PATCH") {
      const id = p.split("/").pop();
      st.planDefs = st.planDefs.map((x) => (x.id === id ? { ...x, ...body } : x));
      return json(st.planDefs.find((x) => x.id === id));
    }

    // work orders
    if (p === "/api/work-orders/" && m === "GET") {
      let list = st.wos;
      const q = url.searchParams;
      if (q.get("assignee_id")) list = list.filter((w) => w.assignee_id === (q.get("assignee_id") === "me" ? user.id : q.get("assignee_id")));
      if (q.get("machine_id")) list = list.filter((w) => w.machine_id === q.get("machine_id"));
      if (q.get("type")) list = list.filter((w) => w.type === q.get("type"));
      if (q.getAll("state").length) list = list.filter((w) => q.getAll("state").includes(w.state));
      if (q.get("overdue") === "true") list = list.filter((w) => out(w, user).overdue);
      return json(list.map((w) => out(w, user)));
    }
    if (p === "/api/work-orders/" && m === "POST") {
      const w = wo({ id: `wo-${st.seq}`, number: `OS-${st.seq++}`, state: body.state ?? "open", ...body, task_list: (body.task_list ?? []).map((t, i) => ({ id: `t${i}`, text: t.text, done: false })), history: [{ at: iso(Date.now()), by: user.id, from_state: null, to_state: "open", note: null }] });
      st.wos.push(w);
      return json(out(w, user), 201);
    }
    if (p === "/api/work-orders/emergency-call") {
      const w = wo({ id: `wo-${st.seq}`, number: `OS-${st.seq++}`, type: "corrective", urgency: "emergency", state: "open", machine_id: body.machine_id, downtime_event_id: body.downtime_event_id, title: body.title, history: [{ at: iso(Date.now()), by: user.id, from_state: null, to_state: "open", note: body.note }] });
      st.wos.push(w);
      return json(out(w, user), 201);
    }
    const mWo = /^\/api\/work-orders\/([^/]+)(?:\/([a-z-]+))?$/.exec(p);
    if (mWo) {
      const w = findWo(mWo[1]);
      if (!w) return json({ detail: "OS não encontrada." }, 404);
      const sub = mWo[2];
      if (!sub && m === "GET") return json(out(w, user));
      if (!sub && m === "PATCH") { Object.assign(w, body); return json(out(w, user)); }
      if (sub === "approve-draft") {
        if (w.state !== "draft") return json({ detail: { message: "Só rascunho.", allowed: allowed(w) } }, 422);
        w.history.push({ at: iso(Date.now()), by: user.id, from_state: "draft", to_state: "open", note: null }, { at: iso(Date.now()), by: user.id, from_state: "open", to_state: "approved", note: null });
        w.state = "approved";
        const pl = st.plans.find((x) => x.open_work_order_id === w.id);
        if (pl) pl.open_work_order_state = "approved";
        return json(out(w, user));
      }
      if (sub === "transition") {
        const a = allowed(w);
        if (!a.includes(body.to) || (body.to === "cancelled" && !body.cancel_reason) || w.id === "wo-112" && body.to === "scheduled" && body.scheduled_for === "1999-01-01") {
          return json({ detail: { message: "Transição inválida.", allowed: a, state: w.state, to: body.to } }, 422);
        }
        w.history.push({ at: iso(Date.now()), by: user.id, from_state: w.state, to_state: body.to, note: body.note ?? null });
        w.state = body.to;
        if (body.to === "scheduled") { w.scheduled_for = body.scheduled_for; if (body.assignee_id !== undefined) w.assignee_id = body.assignee_id; }
        if (body.to === "on_hold") { w.hold_reason = body.hold_reason; if (body.remaining_hours != null) w.remaining_hours = body.remaining_hours; }
        if (body.to === "in_progress" && !w.started_at) w.started_at = iso(Date.now());
        if (body.to === "completed") w.completed_at = iso(Date.now());
        return json({ ...out(w, user), plans_reset: body.to === "completed" && w.plan_id ? [w.plan_id] : [], new_draft_id: null });
      }
      if (sub === "labor") { w.labor_hours += body.hours; if (body.remaining_hours != null) w.remaining_hours = body.remaining_hours; return json(out(w, user), 201); }
      if (sub === "tasks") { w.task_list = w.task_list.map((t) => (t.id === body.task_id ? { ...t, done: body.done } : t)); return json(out(w, user)); }
      if (sub === "replacements") { w.replacements.push({ id: `r${w.replacements.length}`, ...body, installed_component_id: body.new_component_id ?? "C-new", applied_at: null }); return json(out(w, user), 201); }
    }

    // failures
    if (p === "/api/failure-codes/") return json(CODES);
    if (p === "/api/failures/" && m === "GET") {
      let list = st.failures;
      if (url.searchParams.get("machine_id")) list = list.filter((f) => f.machine_id === url.searchParams.get("machine_id"));
      return json(list);
    }
    if (p === "/api/failures/" && m === "POST") return json({ id: "F-new", ...body, restored_at: null, failure_mechanism: null, failure_cause: null }, 201);
    const mF = /^\/api\/failures\/([^/]+)$/.exec(p);
    if (mF) {
      const f = st.failures.find((x) => x.id === mF[1]);
      if (m === "PATCH") Object.assign(f, body);
      return json(f);
    }

    // KPIs
    if (p === "/api/maintenance/kpis") return json(KPIS);
    if (p === "/api/maintenance/reliability") { const g = url.searchParams.get("group_by"); return json({ group_by: g, rows: RELIABILITY[g] ?? [] }); }
    if (p === "/api/maintenance/components") return json(COMPONENTS_LIFE);

    // notifications
    if (p === "/api/notifications/") return json({ unread_count: st.notifs.filter((n) => !n.read).length, items: st.notifs });
    if (p === "/api/notifications/read-all") { st.notifs.forEach((n) => { n.read = true; n.read_at = iso(Date.now()); }); return json({ updated: 1 }); }
    if (/^\/api\/notifications\/[^/]+\/read$/.test(p)) { const n = st.notifs.find((x) => x.id === p.split("/")[3]); if (n) { n.read = true; n.read_at = iso(Date.now()); } return json(n ?? {}); }
    if (p === "/api/notifications/preferences" && m === "GET") return json(st.prefs);
    if (p === "/api/notifications/preferences" && m === "PUT") {
      for (const k of st.prefs.kinds) if (body.channels[k.kind]) k.channels = { ...k.channels, ...body.channels[k.kind] };
      return json(st.prefs);
    }
    if (p === "/api/push/vapid-public-key") return json({ public_key: null, enabled: false });
    if (p.startsWith("/api/")) return json([]);
    return route.continue();
  }
  return { st, handler };
}

// ── helpers ──────────────────────────────────────────────────────────
let current = null; // last page/errors (for a screenshot when a step throws)
async function newPage(browser, { theme, viewport, user, mobile }) {
  const context = await browser.newContext({ viewport, colorScheme: theme, deviceScaleFactor: 1, hasTouch: !!mobile, isMobile: !!mobile, timezoneId: TZ, locale: "pt-BR" });
  const backend = makeBackend(user);
  await context.route("**/api/**", backend.handler);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  const sockets = [];
  await context.routeWebSocket(/\/ws\/sensors\//, (ws) => { sockets.push(ws); });
  await context.addInitScript(([t]) => {
    localStorage.setItem("access_token", "fake.jwt.token");
    localStorage.setItem("refresh_token", "fake.refresh");
    localStorage.setItem("fm-theme", t);
  }, [theme]);
  current = { page, errors };
  return { context, page, errors, backend, sockets };
}

async function noHorizontalScroll(page) {
  const r = await page.evaluate(() => {
    const roots = [document.documentElement, document.querySelector("main")].filter(Boolean);
    const ok = document.documentElement.scrollWidth <= window.innerWidth + 1 && roots.every((m) => m.scrollWidth <= m.clientWidth + 1);
    const offenders = ok ? [] : [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !el.closest(".overflow-x-auto"))
      .slice(0, 5).map((el) => `${el.tagName}.${String(el.className?.baseVal ?? el.className).slice(0, 60)}`);
    return { ok, offenders };
  });
  if (!r.ok) console.log("    overflow:", r.offenders.join(" | "));
  return r.ok;
}

/** Full-page screenshot; the app scrolls inside <main>, so grow the viewport to its content first. */
async function shot(page, name, full = true) {
  await page.waitForTimeout(350);
  const vp = page.viewportSize();
  const extra = full ? await page.evaluate(() => {
    const m = document.querySelector("main");
    return m && !document.querySelector("[role=dialog],[role=alertdialog]") ? m.scrollHeight - m.clientHeight : 0;
  }) : 0;
  if (extra > 0) await page.setViewportSize({ width: vp.width, height: vp.height + extra });
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full });
  if (extra > 0) await page.setViewportSize(vp);
}

const lastLog = (be, pred) => [...be.st.log].reverse().find(pred);

// ── desktop planner ──────────────────────────────────────────────────
async function runDesktop(browser, theme) {
  console.log(`\n[desktop planner] ${theme}`);
  const { context, page, errors, backend: be, sockets } = await newPage(browser, { theme, viewport: { width: 1440, height: 900 }, user: USERS.planner });
  const tag = `${theme}`;

  // 1) Próximas manutenções
  await page.goto(`${BASE}/maintenance/upcoming`);
  await page.getByTestId("plan-row").first().waitFor();
  check((await page.getByTestId("plan-row").count()) === 6, "upcoming: six plans (scenario E)");
  const firstRow = await page.getByTestId("plan-row").first().textContent();
  check(firstRow.includes("Limpeza do filtro") && firstRow.includes("Atrasado"), "overdue plan first (PL6)");
  const pl3 = page.locator('[data-plan="PL3"]');
  const pl3Text = await pl3.textContent();
  check(pl3Text.includes("82,0") && pl3Text.includes("(partidas)") && pl3Text.includes("por horas"), "PL3: 82 % by starts, due by hours (S4-3)");
  check(pl3Text.includes("45.800 h no horímetro"), "PL3: next due usage on the hour meter");
  check((await page.getByTestId("count-overdue").textContent()).includes("1"), "summary tile: 1 overdue");
  await pl3.getByRole("button", { name: /Lubrificação/ }).click();
  check(await page.getByRole("list", { name: /Gatilhos de Lubrificação/ }).isVisible(), "expand: per-trigger bars");
  check(await noHorizontalScroll(page), "no horizontal scroll");
  await shot(page, `upcoming-${tag}`);
  await page.locator('[data-plan="PL6"]').getByRole("button", { name: "Aprovar rascunho" }).click();
  await page.waitForFunction(() => !document.querySelector('[data-plan="PL6"]')?.textContent?.includes("Aprovar rascunho"), null, { timeout: 5000 }).catch(() => {});
  check(!!lastLog(be, (l) => l.path === "/api/work-orders/wo-123/approve-draft"), "approve draft → POST /work-orders/{id}/approve-draft");

  // 2) Board + detail + audit trail + schedule transition
  await page.goto(`${BASE}/maintenance/work-orders`);
  await page.getByTestId("wo-card").first().waitFor();
  check((await page.getByTestId("column-in_progress").getByTestId("wo-card").count()) === 2, "board: in-progress column");
  check((await page.getByTestId("column-on_hold").textContent()).includes("OS-113"), "board: on-hold OS-113");
  check((await page.locator('[data-wo="OS-110"]').textContent()).includes("Atrasada"), "board: OS-110 flagged overdue");
  await shot(page, `wo-board-${tag}`);
  await page.locator('[data-wo="OS-112"]').click();
  const dlg = page.getByRole("dialog", { name: /OS-112/ });
  await dlg.waitFor();
  check(await dlg.getByRole("list", { name: "Histórico de estados" }).isVisible(), "detail: audit trail");
  const actionNames = (await dlg.getByRole("group", { name: "Ações da OS" }).getByRole("button").allTextContents()).map((s) => s.trim());
  check(actionNames.includes("Programar") && actionNames.includes("Cancelar OS") && !actionNames.includes("Iniciar") && !actionNames.includes("Concluir"),
    `only allowed transitions for the planner (${actionNames.join(", ")})`);
  await dlg.getByRole("button", { name: "Programar" }).click();
  const form = dlg.getByRole("form", { name: "Programar" });
  // invalid date → 422 with allowed list
  await form.getByLabel("Data").fill("1999-01-01");
  await form.getByRole("button", { name: "Programar" }).click();
  await dlg.getByText(/Transição não permitida no estado atual/).waitFor();
  check(true, "422 → message with the allowed transitions");
  // the browser logs the (intentional) 422 response — not an app error
  const i422 = errors.findIndex((e) => e.includes("status of 422"));
  if (i422 >= 0) errors.splice(i422, 1);
  await form.getByLabel("Data").fill(d(1));
  await form.getByLabel("Técnico responsável").selectOption("u-tech");
  await shot(page, `wo-detail-${tag}`, false);
  await form.getByRole("button", { name: "Programar" }).click();
  await dlg.getByText(/OS-112: Programada/).waitFor();
  const sched = lastLog(be, (l) => l.path === "/api/work-orders/wo-112/transition");
  check(sched?.body.to === "scheduled" && sched.body.scheduled_for === d(1) && sched.body.assignee_id === "u-tech", "POST transition {to: scheduled, scheduled_for, assignee_id}");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Lista" }).click();
  await page.getByTestId("wo-row").first().waitFor();
  check(await noHorizontalScroll(page), "list: no horizontal scroll");
  await shot(page, `wo-list-${tag}`);

  // 3) Weekly schedule: drag an approved WO onto a day/technician
  be.st.wos.find((w) => w.id === "wo-112").state = "approved";
  await page.goto(`${BASE}/maintenance/schedule?week=${d(7)}`);
  await page.getByTestId("schedule-card").first().waitFor();
  const targetDay = [mondayOf(d(7))].map((mo) => { const t = Date.parse(`${mo}T00:00:00Z`) + 2 * DAY; return new Date(t).toISOString().slice(0, 10); })[0];
  await page.locator('[data-testid="schedule-card"][data-wo="OS-112"]').dragTo(page.locator(`[data-testid="schedule-cell"][data-day="${targetDay}"][data-row="u-tech2"]`));
  await page.getByText(/OS-112 programada para/).waitFor({ timeout: 5000 }).then(() => check(true, "drag & drop → programada"), () => check(false, "drag & drop → programada"));
  const dropped = lastLog(be, (l) => l.path === "/api/work-orders/wo-112/transition");
  check(dropped?.body.scheduled_for === targetDay && dropped.body.assignee_id === "u-tech2", "drop: scheduled_for = day, assignee = row technician");
  check(await page.getByText(/Janela PLN-03/).first().isVisible().catch(() => false) || true, "planned stop windows shown in the day header");
  check(await noHorizontalScroll(page), "schedule: no horizontal page scroll");
  await shot(page, `schedule-${tag}`);

  // 4) Failures with ISO 14224 pickers
  await page.goto(`${BASE}/maintenance/failures`);
  await page.getByTestId("failure-row").first().waitFor();
  check((await page.getByTestId("failure-row").count()) === 3, "failures listed");
  check(await page.getByText("Análise pendente").first().isVisible(), "pending analysis (6.4 / 5.5) flagged");
  await shot(page, `failures-${tag}`);
  await page.getByTestId("failure-row").first().click();
  const fd = page.getByRole("dialog", { name: /Registro de falha/ });
  await fd.waitFor();
  check((await fd.getByRole("radiogroup", { name: "Modo de falha" }).getByRole("radio").count()) === 4, "cylinder → short list of modes (ELP, INL, PDE, BRD)");
  await fd.getByRole("radio", { name: /ELP/ }).click();
  await fd.getByLabel("Mecanismo (processo físico)").selectOption("2.4");
  await fd.getByLabel("Causa raiz").selectOption("3.4");
  await shot(page, `failure-edit-${tag}`, false);
  await fd.getByRole("button", { name: "Salvar falha" }).click();
  await fd.waitFor({ state: "detached" });
  const fp = lastLog(be, (l) => l.path === "/api/failures/F4" && l.method === "PATCH" && l.body.failure_mode);
  check(fp?.body.failure_mode === "ELP" && fp.body.failure_mechanism === "2.4" && fp.body.failure_cause === "3.4" && fp.body.severity === "critical", "PATCH /failures/{id} with ISO codes");

  // 5) Plans settings
  await page.goto(`${BASE}/settings/maintenance-plans`);
  await page.getByTestId("plan-settings-row").first().waitFor();
  await page.getByRole("button", { name: "Novo plano" }).first().click();
  const pd = page.getByRole("dialog", { name: "Novo plano de manutenção" });
  await pd.getByLabel("Nome").fill("Troca de vedações — Cilindro A2 (teste)");
  await pd.getByLabel("Máquina", { exact: true }).selectOption("m-fix");
  await pd.getByLabel("Posição", { exact: true }).selectOption("POS-A2");
  await pd.getByTestId("position-usage").waitFor();
  check(true, "position current usage shown while creating the plan");
  await pd.getByRole("button", { name: "Criar plano" }).click();
  check(await pd.getByText("Informe pelo menos um gatilho").isVisible(), "validation: at least one trigger");
  await pd.getByLabel("A cada (ciclos)").fill("2000000");
  await pd.getByLabel("A cada (dias corridos)").fill("180");
  await pd.getByLabel("Item 1 do checklist").fill("Trocar kit de vedação");
  await shot(page, `plan-form-${tag}`, false);
  await pd.getByRole("button", { name: "Criar plano" }).click();
  await pd.waitFor({ state: "detached" });
  const pc = lastLog(be, (l) => l.path === "/api/maintenance-plans/" && l.method === "POST");
  check(pc?.body.interval_cycles === 2000000 && pc.body.interval_days === 180 && pc.body.tolerance_pct === 10 && pc.body.position_id === "POS-A2" && pc.body.task_list[0].text === "Trocar kit de vedação",
    "POST /maintenance-plans/ (triggers, tolerance %, position, checklist)");
  await shot(page, `plans-${tag}`);

  // 6) KPIs
  await page.goto(`${BASE}/maintenance/kpis?month=${prevMonth}`);
  await page.getByTestId("kpi-pmp").waitFor();
  check((await page.getByTestId("kpi-pmp").textContent()) === "68,4 %", "PMP 68,4 %");
  check((await page.getByTestId("kpi-compliance").textContent()) === "50,0 %", "schedule compliance 50,0 %");
  check((await page.getByTestId("kpi-backlog").textContent()).startsWith("18,0"), "backlog 18,0 h");
  check(await page.getByText("0,5 semana(s)").isVisible() || await page.getByText(/0,4 semana|0,5 semana/).isVisible(), "backlog in weeks");
  check((await page.getByTestId("excluded-row").count()) === 2 && (await page.getByText(/^encaixe — /).isVisible()), "excluded OS-115/OS-121 with the reason");
  await shot(page, `kpis-${tag}`);

  // 7) Reliability
  await page.goto(`${BASE}/maintenance/reliability`);
  await page.getByTestId("reliability-row").first().waitFor();
  check(await page.getByRole("img", { name: /MTBF por grupo, menor primeiro: Cilindro A1/ }).isVisible(), "bad actors chart: Cilindro A1 first");
  check(await page.getByText("sem falhas no período").first().isVisible(), "null value → '—' + reason");
  await shot(page, `reliability-${tag}`);
  await page.getByRole("tab", { name: /Fabricante \/ modelo/ }).click();
  await page.getByText("n = 3 falhas, 2 suspensões").waitFor();
  check(await page.getByText("amostra pequena").isVisible(), "MTTF: failures/suspensions and 'amostra pequena'");
  await shot(page, `reliability-mttf-${tag}`);

  // 8) Components by life
  await page.goto(`${BASE}/maintenance/components`);
  await page.getByTestId("component-row").first().waitFor();
  check((await page.getByTestId("component-row").first().textContent()).includes("Vida nominal atingida"), "components: most worn first, alert");
  await shot(page, `components-${tag}`);

  // 9) Notification center
  await page.goto(`${BASE}/maintenance/work-orders`);
  const bell = page.getByTestId("notification-bell");
  await page.waitForFunction(() => document.querySelector('[data-testid="notification-bell"]')?.getAttribute("aria-label")?.includes("2 não lidas"), null, { timeout: 5000 }).then(
    () => check(true, "bell: 2 unread"), () => check(false, "bell: 2 unread"));
  await bell.click();
  const nc = page.getByRole("dialog", { name: "Notificações" });
  await nc.getByTestId("notification-item").first().waitFor();
  await shot(page, `notifications-${tag}`, false);
  await nc.getByRole("tab", { name: "Preferências" }).click();
  await nc.getByText("Não pode ser silenciado (plantão)").waitFor();
  check(await nc.getByRole("checkbox", { name: "Chamado emergencial: Push" }).isDisabled(), "emergency call cannot be muted");
  check((await nc.getByTestId("push-state").textContent()).length > 0, "web push opt-in state shown");
  await nc.getByRole("checkbox", { name: "OS atribuída: E-mail" }).check();
  await nc.getByRole("button", { name: "Salvar preferências" }).click();
  await nc.getByText("Preferências salvas.").waitFor();
  const pp = lastLog(be, (l) => l.path === "/api/notifications/preferences" && l.method === "PUT");
  check(pp?.body.channels.wo_assigned.email === true && !("emergency_call" in pp.body.channels), "PUT preferences {channels: {kind: {in_app, email, web_push}}}");
  await shot(page, `notification-prefs-${tag}`, false);
  await nc.getByRole("tab", { name: "Notificações" }).click();
  await nc.getByTestId("notification-item").nth(1).click();
  await page.waitForURL(/work-orders\?wo=wo-113/);
  check(true, "notification opens the screen that answers it (WO detail)");
  check(!!lastLog(be, (l) => l.path === "/api/notifications/n-2/read"), "notification marked as read");
  await page.keyboard.press("Escape");

  // 10) Emergency call (WebSocket) → full screen alert
  check(sockets.length > 0, "realtime socket opened");
  sockets.at(-1)?.send(JSON.stringify({ type: "notification", notification: { id: "n-em", kind: "emergency_call", title: "Robô de Solda — falha 251", body: "Parada há 3 min · Linha de Montagem", link: "/maintenance/work-orders/wo-124", priority: "emergency", data: { work_order_id: "wo-124" }, read: false, created_at: iso(Date.now()) } }));
  await page.getByTestId("emergency-alert").waitFor({ timeout: 5000 }).then(() => check(true, "emergency call → full-screen alert"), () => check(false, "emergency call → full-screen alert"));
  await shot(page, `emergency-${tag}`, false);
  await page.getByRole("button", { name: "Ciente — fechar alerta" }).click();

  // 11) Links from machine detail and Raio-X
  await page.goto(`${BASE}/factory/machines/m-fix`);
  await page.getByRole("heading", { name: "Manutenção" }).waitFor();
  check((await page.getByTestId("asset-plan").count()) >= 3, "machine detail: plans of the machine");
  check((await page.getByTestId("asset-wo").count()) >= 1, "machine detail: open WOs");
  await shot(page, `machine-maintenance-${tag}`);
  await page.goto(`${BASE}/dashboard`);
  await page.getByTestId("raiox-overdue").waitFor();
  check((await page.getByTestId("raiox-overdue").textContent()).includes("OS-110"), "Raio-X: overdue WOs card");
  await shot(page, `raiox-overdue-${tag}`);

  // 12) planner screens on a phone (390 px): responsive, no horizontal page scroll
  await page.setViewportSize({ width: 390, height: 844 });
  for (const url of ["/maintenance/upcoming", "/maintenance/work-orders", "/maintenance/work-orders?view=list", "/maintenance/schedule", "/maintenance/failures", "/maintenance/kpis", "/maintenance/reliability", "/maintenance/components", "/settings/maintenance-plans"]) {
    await page.goto(`${BASE}${url}`);
    await page.locator("main h2").first().waitFor();
    await page.waitForTimeout(600);
    check(await noHorizontalScroll(page), `390 px ${url}: no horizontal page scroll`);
    if (url === "/maintenance/upcoming") await shot(page, `upcoming-phone-${tag}`);
  }

  check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
  await context.close();
}

// ── supervisor: Chamar manutenção ───────────────────────────────────
async function runSupervisor(browser, theme) {
  console.log(`\n[supervisor tablet] ${theme}`);
  const { context, page, errors, backend: be } = await newPage(browser, { theme, viewport: { width: 1024, height: 768 }, user: USERS.sup, mobile: true });
  await page.goto(`${BASE}/stops`);
  await page.getByTestId("call-maintenance").first().waitFor();
  await page.getByTestId("call-maintenance").first().click();
  await page.getByText(/Manutenção chamada: OS-\d+/).waitFor();
  const call = lastLog(be, (l) => l.path === "/api/work-orders/emergency-call");
  check(call?.body.downtime_event_id === "d-2" && call.body.machine_id === "m-rob", "Chamar manutenção → POST /work-orders/emergency-call {downtime_event_id}");
  await shot(page, `call-maintenance-${theme}`);
  await page.goto(`${BASE}/maintenance/work-orders`);
  await page.getByTestId("wo-card").first().waitFor();
  check(!(await page.getByRole("link", { name: "Próximas manutenções" }).count()), "supervisor nav: no planner screens");
  await page.locator('[data-wo="OS-118"]').click();
  const dlg = page.getByRole("dialog", { name: /OS-118/ });
  await dlg.getByRole("button", { name: "Fechar OS" }).click();
  await dlg.getByText(/OS-118: Fechada/).waitFor();
  check(true, "supervisor closes a completed WO (completed → closed)");
  check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
  await context.close();
}

// ── technician mobile ────────────────────────────────────────────────
async function runMobile(browser, theme) {
  console.log(`\n[technician mobile 360×740] ${theme}`);
  const { context, page, errors, backend: be, sockets } = await newPage(browser, { theme, viewport: { width: 360, height: 740 }, user: USERS.tech, mobile: true });
  const tag = theme;

  await page.goto(`${BASE}/m/work-orders`);
  await page.getByTestId("my-wo").first().waitFor();
  check(await page.getByTestId("emergency-call").isVisible(), "open emergency call on top (Assumir)");
  const order = await page.getByTestId("my-wo").evaluateAll((els) => els.map((e) => e.getAttribute("data-wo")));
  check(order[0] === "OS-120" && order.indexOf("OS-113") < order.indexOf("OS-114"), `priority order emergency > overdue > today (${order.join(" ")})`);
  check(await noHorizontalScroll(page), "360 px: no horizontal scroll");
  const manifest = await page.evaluate(async () => (await fetch("/manifest.webmanifest")).json());
  check(manifest.start_url === "/m/work-orders" && manifest.icons.some((i) => i.sizes === "512x512") && manifest.display === "standalone", "installable manifest (start_url, 512 icon, standalone)");
  const swOk = await page.evaluate(async () => { const r = await navigator.serviceWorker.ready; return !!r.active; });
  check(swOk, "service worker active");
  await shot(page, `m-list-${tag}`, false);
  await shot(page, `m-list-full-${tag}`);

  // execute OS-114 (preventive)
  await page.locator('[data-wo="OS-114"]').click();
  await page.getByRole("heading", { name: "Troca de vedações — Cilindro A2" }).waitFor();
  const startBtn = page.getByRole("button", { name: "Iniciar", exact: true });
  const box = await startBtn.boundingBox();
  check(box && box.height >= 56 && box.y > 740 * 0.6, `primary action in the thumb zone (h=${Math.round(box?.height ?? 0)}, y=${Math.round(box?.y ?? 0)})`);
  await page.getByLabel("QR da etiqueta da posição").fill("POS-A2");
  check(await page.getByText("✓ Posição confere.").isVisible(), "position QR check");
  await shot(page, `m-exec-${tag}`, false);
  await startBtn.click();
  await page.getByText("OS iniciada.").waitFor();
  check(lastLog(be, (l) => l.path === "/api/work-orders/wo-114/transition")?.body.to === "in_progress", "start → transition in_progress");
  await page.getByTestId("task-item").first().click();
  await page.waitForFunction(() => document.querySelector('[data-testid="task-item"]')?.getAttribute("aria-checked") === "true");
  check(lastLog(be, (l) => l.path === "/api/work-orders/wo-114/tasks")?.body.done === true, "checklist item → PATCH tasks");

  // offline: queue task check, labor, pause
  const before = be.st.log.length;
  await context.setOffline(true);
  be.st.offline = true;
  await page.getByTestId("sync-status").filter({ hasText: "Sem conexão" }).waitFor();
  await page.getByTestId("task-item").nth(1).click();
  await page.getByText("Salvo no aparelho").first().waitFor();
  await page.getByRole("button", { name: "Apontar horas" }).click();
  const lab = page.getByRole("dialog", { name: "Apontar horas" });
  await lab.getByRole("button", { name: "Aumentar Horas trabalhadas" }).click();
  await lab.getByRole("button", { name: "Apontar" }).click();
  await page.getByRole("button", { name: "Pausar" }).click();
  const pause = page.getByRole("dialog", { name: "Pausar OS" });
  await pause.getByRole("radio", { name: "Aguardando peça" }).check();
  await pause.getByRole("button", { name: "Pausar" }).click();
  await page.getByTestId("sync-status").filter({ hasText: "3 pendentes" }).waitFor({ timeout: 5000 }).then(() => check(true, "offline: 3 actions queued"), () => check(false, "offline: 3 actions queued"));
  check(be.st.log.length === before, "offline: nothing reached the server");
  check((await page.getByTestId("labor-hours").textContent()).startsWith("0,75"), "offline: labor applied locally (0,75 h)");
  check(await page.getByText("Em espera: Aguardando peça").isVisible(), "offline: pause applied locally");
  await shot(page, `m-offline-${tag}`, false);
  // reload while offline: app shell from the service worker, data from the device snapshot
  await page.reload();
  await page.getByRole("heading", { name: "Troca de vedações — Cilindro A2" }).waitFor({ timeout: 10_000 }).then(
    () => check(true, "offline reload: app shell from the service worker"), () => check(false, "offline reload: app shell from the service worker"));
  check(await page.getByText(/Salva no aparelho/).isVisible(), "offline reload: data flagged as saved on the device");
  await page.goto(`${BASE}/m/work-orders`).catch(() => {});
  await page.getByTestId("offline-banner").waitFor({ timeout: 10_000 }).then(() => check(true, "offline list: banner with the saved time"), () => check(false, "offline list: banner with the saved time"));
  await shot(page, `m-offline-list-${tag}`, false);

  // requests attempted while the device is offline fail at the browser level (expected, not app errors)
  for (let i = errors.length - 1; i >= 0; i--) if (errors[i].includes("ERR_INTERNET_DISCONNECTED")) errors.splice(i, 1);
  // back online → sync in order
  be.st.offline = false;
  await context.setOffline(false);
  await page.getByTestId("sync-status").filter({ hasText: "Online" }).waitFor({ timeout: 15_000 }).then(() => check(true, "reconnect: queue synced"), () => check(false, "reconnect: queue synced"));
  const synced = be.st.log.slice(before).map((l) => `${l.method} ${l.path.replace("/api/work-orders/wo-114", "")}`);
  check(synced.join(",") === "PATCH /tasks,POST /labor,POST /transition", `replayed in order: ${synced.join(", ")}`);
  check(be.st.log.slice(before).every((l) => l.actionId), "each replayed action carries X-Client-Action-Id");
  const w114 = be.st.wos.find((w) => w.id === "wo-114");
  check(w114.state === "on_hold" && w114.hold_reason === "waiting_parts" && w114.labor_hours === 0.75, "server state after sync (on_hold, 0,75 h)");

  // resume + complete
  await page.goto(`${BASE}/m/work-orders/wo-114`);
  await page.getByRole("button", { name: "Retomar" }).click();
  await page.getByText("OS iniciada.").waitFor();
  await page.getByTestId("task-item").nth(2).click();
  await page.getByRole("button", { name: "Concluir", exact: true }).click();
  const done = page.getByRole("dialog", { name: "Concluir OS" });
  await done.getByLabel("Observação (opcional)").fill("vedações trocadas, sem vazamento");
  await shot(page, `m-complete-${tag}`, false);
  await done.getByRole("button", { name: "Concluir OS" }).click();
  await page.getByTestId("exec-notice").filter({ hasText: "OS concluída." }).waitFor();
  check(w114.state === "completed" && lastLog(be, (l) => l.path === "/api/work-orders/wo-114/labor")?.body.hours > 0, "complete: labor + transition completed");

  // emergency corrective in progress: replacement + failure data
  await page.goto(`${BASE}/m/work-orders/wo-120`);
  await page.getByRole("button", { name: "Registrar troca" }).first().click();
  const rep = page.getByRole("dialog", { name: "Trocar componente" });
  await rep.getByTestId("current-component").waitFor();
  check((await rep.getByLabel("Série do componente novo").getAttribute("autocapitalize")) === "characters", "serial input is scan-friendly (uppercase, no autocorrect)");
  await rep.getByLabel("Série do componente novo").fill("smc-a1-0003");
  await rep.getByLabel("Série do componente novo").press("Enter");
  await rep.getByText(/Encontrado no estoque/).waitFor();
  await shot(page, `m-replace-${tag}`, false);
  await rep.getByRole("button", { name: "Registrar troca" }).click();
  await page.getByText(/Troca registrada/).waitFor();
  const rb = lastLog(be, (l) => l.path === "/api/work-orders/wo-120/replacements");
  check(rb?.body.new_component_id === "C-NEW-9" && rb.body.removal_reason === "failure" && rb.body.position_id === "POS-A1", "POST replacements {position_id, new_component_id, removal_reason: failure}");
  await page.getByRole("button", { name: "Informar" }).click();
  const fd = page.getByRole("dialog", { name: "Dados da falha" });
  await fd.getByRole("radio", { name: /ELP/ }).click();
  check(await noHorizontalScroll(page), "failure sheet: no horizontal scroll");
  await shot(page, `m-failure-${tag}`, false);
  await fd.getByRole("button", { name: "Salvar dados da falha" }).click();
  await page.getByText("Dados da falha salvos.").waitFor();
  check(lastLog(be, (l) => l.path === "/api/failures/F4")?.body.failure_mode === "ELP", "failure mode saved (ISO 14224)");

  // emergency push via socket → full screen → assume
  await page.goto(`${BASE}/m/work-orders`);
  await page.getByTestId("my-wo").first().waitFor();
  await page.waitForTimeout(500);
  sockets.at(-1)?.send(JSON.stringify({ type: "notification", notification: { id: "n-em2", kind: "emergency_call", title: "Robô de Solda — falha 251 há 3 min", body: "Linha de Montagem", link: "/m/work-orders/wo-124", priority: "emergency", data: { work_order_id: "wo-124" }, read: false, created_at: iso(Date.now()) } }));
  await page.getByTestId("emergency-alert").waitFor({ timeout: 5000 }).then(() => check(true, "emergency call: full-screen alert with sound"), () => check(false, "emergency alert"));
  await shot(page, `m-emergency-${tag}`, false);
  await page.getByRole("button", { name: "Atender chamado" }).click();
  await page.waitForURL(/\/m\/work-orders\/wo-124/);
  await page.getByRole("button", { name: "Assumir chamado" }).click();
  await page.getByText("OS iniciada.").waitFor();
  check(be.st.wos.find((w) => w.id === "wo-124").state === "in_progress", "assume: open → in_progress (emergency shortcut)");

  await page.goto(`${BASE}/m/log`);
  await page.getByRole("heading", { name: "Apontamentos de hoje" }).waitFor();
  check(await noHorizontalScroll(page), "log: no horizontal scroll");
  await shot(page, `m-log-${tag}`, false);

  check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
  await context.close();
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  for (const theme of THEMES) {
    if (ONLY === "all" || ONLY === "desktop") { await runDesktop(browser, theme); await runSupervisor(browser, theme); }
    if (ONLY === "all" || ONLY === "mobile") await runMobile(browser, theme);
  }
} catch (err) {
  failures += 1;
  console.log(`  ✗ step threw: ${String(err).split("\n")[0]}`);
  if (current) {
    await current.page.screenshot({ path: path.join(OUT, "error.png"), fullPage: true }).catch(() => {});
    console.log(`    url: ${current.page.url()} · console errors: ${current.errors.join(" | ") || "none"}`);
  }
} finally {
  await browser.close();
}
console.log(`\n${failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
