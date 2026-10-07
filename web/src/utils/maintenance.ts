/**
 * Pure helpers of the maintenance screens (Sprint 4). No React here — unit-tested.
 * Rules: docs/domain.md §5 + S4-1…S4-12, docs/maintenance_workflows.md.
 */
import type {
  CancelReason, FailureCode, FailureCodes, FailureSeverity, PlanState, PlanStatus, PlanTriggerStatus, TriggerMeasure,
  WoState, WoType, WoUrgency, WorkOrder,
} from "@/services/maintenanceTypes";
import { formatDec1, formatInt } from "@/utils/format";

// ── labels ────────────────────────────────────────────────────────

export const WO_STATE_LABEL: Record<WoState, string> = {
  draft: "Rascunho",
  open: "Solicitada",
  approved: "Aprovada",
  scheduled: "Programada",
  in_progress: "Em execução",
  on_hold: "Em espera",
  completed: "Concluída",
  closed: "Fechada",
  cancelled: "Cancelada",
};

export type StateTone = "neutral" | "info" | "accent" | "warn" | "danger" | "ok";

export const WO_STATE_TONE: Record<WoState, StateTone> = {
  draft: "neutral",
  open: "info",
  approved: "info",
  scheduled: "accent",
  in_progress: "warn",
  on_hold: "danger",
  completed: "ok",
  closed: "neutral",
  cancelled: "neutral",
};

export const WO_TYPE_LABEL: Record<WoType, string> = {
  preventive: "Preventiva",
  predictive: "Preditiva",
  corrective: "Corretiva",
  improvement: "Melhoria",
};

export const URGENCY_LABEL: Record<WoUrgency, string> = {
  emergency: "Emergencial",
  deferred: "Programada",
};

export const PLAN_STATE_LABEL: Record<PlanState, string> = {
  ok: "Em dia",
  due_soon: "Vence em breve",
  due: "Vencido",
  overdue: "Atrasado",
};

/** Severity order of plan states (overdue first). */
export const PLAN_STATE_ORDER: Record<PlanState, number> = { overdue: 0, due: 1, due_soon: 2, ok: 3 };

export const TRIGGER_LABEL: Record<TriggerMeasure, string> = {
  cycles: "ciclos",
  hours: "horas",
  starts: "partidas",
  days: "calendário",
};

/** "por ciclos", "por horas", "por calendário". */
export function byTrigger(m: TriggerMeasure): string {
  return `por ${TRIGGER_LABEL[m]}`;
}

export const CANCEL_REASON_LABEL: Record<CancelReason, string> = {
  production_conflict: "Conflito com a produção",
  superseded: "Substituída por outra OS",
  duplicate: "Duplicada",
  not_needed: "Não é mais necessária",
  other: "Outro motivo",
};

export const HOLD_REASONS = [
  { value: "waiting_parts", label: "Aguardando peça" },
  { value: "waiting_release", label: "Aguardando liberação da produção" },
  { value: "other", label: "Outro" },
] as const;

export function holdReasonLabel(r: string | null | undefined): string {
  if (!r) return "—";
  if (r === "shift_handover") return "Passagem de turno";
  return HOLD_REASONS.find((h) => h.value === r)?.label ?? r;
}

export const SEVERITY_LABEL: Record<FailureSeverity, string> = {
  critical: "Crítica — perda total da função",
  degraded: "Degradada — função parcial",
  incipient: "Incipiente — início de degradação",
};

export const SEVERITY_SHORT: Record<FailureSeverity, string> = {
  critical: "Crítica",
  degraded: "Degradada",
  incipient: "Incipiente",
};

export const RELIABILITY_REASON_LABEL: Record<string, string> = {
  no_failures: "sem falhas no período",
  no_recent_usage: "sem uso recente",
  insufficient_data: "dados insuficientes",
  no_usage: "sem uso registrado",
  no_restorations: "nenhuma falha com restabelecimento",
  no_restored_failures: "nenhuma falha com restabelecimento",
  no_triggers: "plano sem gatilho",
  beyond_horizon: "ritmo recente projeta além de 10 anos",
  no_cycles: "sem contagem de ciclos",
};

export function reliabilityReason(r: string | null | undefined): string {
  if (!r) return "sem dado";
  return RELIABILITY_REASON_LABEL[r] ?? r.replace(/_/g, " ");
}

export const EXCLUDED_REASON_LABEL: Record<string, string> = {
  scheduled_after_freeze: "encaixe — programada depois do congelamento da semana",
  cancelled_superseded: "cancelada como substituída",
  emergency: "corretiva emergencial (sem programação)",
};

// ── ISO 14224 normative lists (domain.md §5.5) ────────────────────
// Reference data from the standard — used to label codes and as the picker list when the server
// list is not available. Not sample data.

export const ISO_MODES: FailureCode[] = [
  ["BRD", "Quebra"], ["FTS", "Falha ao partir"], ["STP", "Falha ao parar"], ["UST", "Parada espúria"],
  ["ELP", "Vazamento externo"], ["INL", "Vazamento interno"], ["OHE", "Superaquecimento"], ["VIB", "Vibração"],
  ["NOI", "Ruído"], ["PDE", "Desvio de parâmetro"], ["ERO", "Saída errática"], ["LOO", "Perda de saída"],
  ["STD", "Deficiência estrutural"], ["AIR", "Leitura anormal de instrumento"], ["SER", "Problemas menores"],
  ["FTO", "Falha ao abrir"], ["FTC", "Falha ao fechar"], ["OTH", "Outro"], ["UNK", "Desconhecido"],
].map(([code, label]) => ({ code, label }));

export const ISO_MECHANISMS: FailureCode[] = [
  ["1.1", "Vazamento", "1 Mecânico"], ["1.2", "Vibração", "1 Mecânico"], ["1.3", "Folga / desalinhamento", "1 Mecânico"],
  ["1.4", "Deformação", "1 Mecânico"], ["1.5", "Afrouxamento", "1 Mecânico"], ["1.6", "Travamento", "1 Mecânico"],
  ["2.1", "Cavitação", "2 Material"], ["2.2", "Corrosão", "2 Material"], ["2.3", "Erosão", "2 Material"],
  ["2.4", "Desgaste", "2 Material"], ["2.5", "Ruptura", "2 Material"], ["2.6", "Fadiga", "2 Material"],
  ["2.7", "Superaquecimento", "2 Material"], ["2.8", "Explosão / estouro", "2 Material"],
  ["3.1", "Falha de controle", "3 Instrumento"], ["3.2", "Sem sinal", "3 Instrumento"], ["3.3", "Sinal falho", "3 Instrumento"],
  ["3.4", "Fora de ajuste", "3 Instrumento"], ["3.5", "Erro de software", "3 Instrumento"], ["3.6", "Falha de causa comum", "3 Instrumento"],
  ["4.1", "Curto-circuito", "4 Elétrico"], ["4.2", "Circuito aberto", "4 Elétrico"], ["4.3", "Sem energia", "4 Elétrico"],
  ["4.4", "Energia com defeito", "4 Elétrico"], ["4.5", "Falha de aterramento / isolação", "4 Elétrico"],
  ["5.1", "Bloqueio / entupimento", "5 Influência externa"], ["5.2", "Contaminação", "5 Influência externa"],
  ["5.3", "Outra influência externa", "5 Influência externa"],
  ["6.1", "Sem causa encontrada", "6 Diversos"], ["6.2", "Causas combinadas", "6 Diversos"], ["6.3", "Outro", "6 Diversos"],
  ["6.4", "Desconhecido", "6 Diversos"],
].map(([code, label, group]) => ({ code, label, group }));

export const ISO_CAUSES: FailureCode[] = [
  ["1.1", "Capacidade inadequada", "1 Projeto"], ["1.2", "Material inadequado", "1 Projeto"],
  ["2.1", "Erro de fabricação", "2 Fabricação / instalação"], ["2.2", "Erro de instalação", "2 Fabricação / instalação"],
  ["3.1", "Serviço fora do projeto", "3 Operação / manutenção"], ["3.2", "Erro de operação", "3 Operação / manutenção"],
  ["3.3", "Erro de manutenção", "3 Operação / manutenção"], ["3.4", "Desgaste e envelhecimento esperados", "3 Operação / manutenção"],
  ["4.1", "Erro de documentação", "4 Gestão"], ["4.2", "Erro de gestão", "4 Gestão"],
  ["5.1", "Sem causa encontrada", "5 Diversos"], ["5.2", "Causa comum", "5 Diversos"], ["5.3", "Causa combinada", "5 Diversos"],
  ["5.4", "Outro", "5 Diversos"], ["5.5", "Desconhecida", "5 Diversos"],
].map(([code, label, group]) => ({ code, label, group }));

export const ISO_DETECTION: FailureCode[] = [
  ["periodic_maintenance", "Manutenção periódica"], ["functional_test", "Teste funcional"], ["inspection", "Inspeção"],
  ["periodic_condition_monitoring", "Monitoramento de condição periódico"],
  ["continuous_condition_monitoring", "Monitoramento contínuo (alarme do FactoryConnect)"],
  ["production_interference", "Interferência na produção (parou a produção)"], ["casual_observation", "Observação casual"],
  ["corrective_maintenance", "Manutenção corretiva"], ["on_demand", "Sob demanda"], ["other", "Outro"],
].map(([code, label]) => ({ code, label }));

export const ISO_CODES: FailureCodes = { modes: ISO_MODES, mechanisms: ISO_MECHANISMS, causes: ISO_CAUSES, detection_methods: ISO_DETECTION };

/** Short mode list per component type (workflows §3: cilindro → ELP, INL, PDE, BRD…). */
export const SHORT_MODES: Record<string, string[]> = {
  pneumatic_cylinder: ["ELP", "INL", "PDE", "BRD", "STD", "NOI"],
  valve: ["FTO", "FTC", "ELP", "INL", "PDE"],
  electric_motor: ["FTS", "OHE", "VIB", "NOI", "BRD", "LOO"],
  servo: ["FTS", "OHE", "VIB", "ERO", "LOO", "AIR"],
  robot_axis: ["VIB", "NOI", "OHE", "ERO", "PDE", "BRD"],
  bearing: ["VIB", "NOI", "OHE", "BRD"],
  gripper: ["FTO", "FTC", "PDE", "BRD", "ELP"],
  pump: ["ELP", "INL", "VIB", "LOO", "FTS", "OHE"],
  belt: ["BRD", "NOI", "VIB", "PDE"],
  spindle: ["VIB", "OHE", "NOI", "PDE", "BRD"],
};

/** Modes offered first for a component type (server `component_types` wins; else the local short list). */
export function shortModes(modes: FailureCode[], componentType: string | null | undefined): FailureCode[] {
  if (!componentType) return [];
  const tagged = modes.filter((m) => m.component_types?.includes(componentType));
  if (tagged.length) return tagged;
  const codes = SHORT_MODES[componentType] ?? [];
  return codes.map((c) => modes.find((m) => m.code === c)).filter((m): m is FailureCode => !!m);
}

export function codeLabel(list: FailureCode[], code: string | null | undefined): string {
  if (!code) return "—";
  const hit = list.find((c) => c.code === code);
  return hit ? `${code} — ${hit.label}` : code;
}

/** Group a code list by `group` preserving order. */
export function groupCodes(list: FailureCode[]): { group: string; items: FailureCode[] }[] {
  const out: { group: string; items: FailureCode[] }[] = [];
  for (const c of list) {
    const g = c.group ?? "";
    const last = out[out.length - 1];
    if (last && last.group === g) last.items.push(c);
    else out.push({ group: g, items: [c] });
  }
  return out;
}

/** ISO analysis still pending (mechanism 6.4 / cause 5.5 / missing). */
export function analysisPending(f: { failure_mode: string | null; failure_mechanism: string | null; failure_cause: string | null }): boolean {
  return !f.failure_mode || f.failure_mode === "UNK" || !f.failure_mechanism || f.failure_mechanism === "6.4" || !f.failure_cause || f.failure_cause === "5.5";
}

// ── roles & permissions ───────────────────────────────────────────

export interface RoleCarrier {
  role?: string | null;
  roles?: string[] | null;
}

export function rolesOf(u: RoleCarrier | null | undefined): string[] {
  if (!u) return [];
  const set = new Set<string>(u.roles ?? []);
  if (u.role) set.add(u.role);
  return [...set];
}

export function hasRole(u: RoleCarrier | null | undefined, ...wanted: string[]): boolean {
  const r = rolesOf(u);
  return wanted.some((w) => r.includes(w));
}

const PLANNERS = ["maintenance_planner", "manager", "admin", "superadmin"];

export type Capability =
  | "maintenance.view"      // maintenance menu (planner desktop screens)
  | "maintenance.plan"      // approve / schedule / cancel WOs, edit plans
  | "maintenance.kpis"      // management KPIs & reliability
  | "maintenance.execute"   // technician actions (start/pause/complete, labor, tasks, replacement)
  | "maintenance.close"     // completed → closed
  | "maintenance.request"   // "Chamar manutenção" / request a WO
  | "maintenance.failures"  // failures screen
  | "predictive.view"       // condition alerts, charts (predictive.md P-8 read roles)
  | "predictive.configure"  // monitors, rules, limits, rebaseline
  | "predictive.decide"     // close alerts / generate the predictive WO
  | "predictive.ack"        // acknowledge an alert
  | "predictive.evaluate"   // P-10 performance metrics
  | "gateways.view"         // field gateways fleet (manager+)
  | "gateways.admin"        // create / enroll / assign tags (admin)
  | "layout.edit"           // plant map editor (sprint6 §1.1: admin, manager)
  | "onboarding.admin"      // onboarding wizard (sprint6 §4: admin of the tenant)
  | "reports.schedule"      // scheduled reports (reports.md §4: manager; admin always)
  | "operator.tablet";      // operator tablet PWA (sprint6 §3)

const CAPS: Record<Capability, string[]> = {
  "maintenance.view": [...PLANNERS, "production_supervisor", "technician"],
  "maintenance.plan": PLANNERS,
  "maintenance.kpis": PLANNERS,
  "maintenance.execute": ["technician", "admin", "superadmin"],
  "maintenance.close": ["production_supervisor", "manager", "admin", "superadmin"],
  "maintenance.request": ["production_supervisor", "operator", ...PLANNERS, "technician"],
  "maintenance.failures": [...PLANNERS, "production_supervisor", "technician"],
  "predictive.view": [...PLANNERS, "production_supervisor", "technician"],
  "predictive.configure": PLANNERS,
  "predictive.decide": PLANNERS,
  "predictive.ack": [...PLANNERS, "technician"],
  "predictive.evaluate": PLANNERS,
  "gateways.view": ["manager", "admin", "superadmin"],
  "gateways.admin": ["admin", "superadmin"],
  "layout.edit": ["manager", "admin", "superadmin"],
  "onboarding.admin": ["admin", "superadmin"],
  "reports.schedule": ["manager", "admin", "superadmin"],
  "operator.tablet": ["operator", "production_supervisor", "manager", "admin", "superadmin"],
};

export function can(u: RoleCarrier | null | undefined, cap: Capability): boolean {
  return hasRole(u, ...CAPS[cap]);
}

/** Who may trigger each transition (workflows §1). The server is authoritative; the UI is conservative. */
export function transitionCapability(from: WoState, to: WoState): Capability {
  if (to === "closed") return "maintenance.close";
  if (to === "cancelled" || to === "open" || to === "approved" || to === "scheduled") return "maintenance.plan";
  if (from === "on_hold" && to === "in_progress") return "maintenance.execute";
  return "maintenance.execute";
}

/** Transitions offered to this user: the server's `allowed_transitions` filtered by role. */
export function offeredTransitions(wo: Pick<WorkOrder, "state" | "allowed_transitions" | "user_allowed_transitions">, u: RoleCarrier | null | undefined): WoState[] {
  // the server already filtered by the caller's roles (and area): trust it
  if (wo.user_allowed_transitions) return wo.user_allowed_transitions.filter((t) => (wo.allowed_transitions ?? []).includes(t));
  return (wo.allowed_transitions ?? []).filter((to) => {
    if (can(u, transitionCapability(wo.state, to))) return true;
    // planner may hand an on_hold WO back to the technician (workflows §2)
    return wo.state === "on_hold" && to === "in_progress" && can(u, "maintenance.plan");
  });
}

// ── state machine (S4-5) — used offline to predict the next allowed transitions ──

const BEFORE_COMPLETED: WoState[] = ["draft", "open", "approved", "scheduled", "in_progress", "on_hold"];

export function localAllowedTransitions(state: WoState, type: WoType, urgency: WoUrgency | null): WoState[] {
  const base: Record<WoState, WoState[]> = {
    draft: ["open"],
    open: ["approved"],
    approved: ["scheduled"],
    scheduled: ["in_progress"],
    in_progress: ["on_hold", "completed"],
    on_hold: ["in_progress", "scheduled"],
    completed: ["closed"],
    closed: [],
    cancelled: [],
  };
  const out = [...base[state]];
  if (state === "open" && type === "corrective" && urgency === "emergency") out.push("in_progress");
  if (BEFORE_COMPLETED.includes(state)) out.push("cancelled");
  return out;
}

export const ACTIVE_STATES: WoState[] = ["draft", "open", "approved", "scheduled", "in_progress", "on_hold"];
export const BACKLOG_STATES: WoState[] = ["approved", "scheduled", "on_hold", "in_progress"];

export function isActive(state: WoState): boolean {
  return ACTIVE_STATES.includes(state);
}

/** Button label for a transition (from the current state). */
export function transitionLabel(from: WoState, to: WoState): string {
  if (to === "open") return "Enviar para aprovação";
  if (to === "approved") return "Aprovar";
  if (to === "scheduled") return from === "on_hold" ? "Reprogramar" : "Programar";
  if (to === "in_progress") return from === "open" ? "Assumir" : from === "on_hold" ? "Retomar" : "Iniciar";
  if (to === "on_hold") return "Pausar";
  if (to === "completed") return "Concluir";
  if (to === "closed") return "Fechar OS";
  if (to === "cancelled") return "Cancelar OS";
  return WO_STATE_LABEL[to];
}

// ── dates ─────────────────────────────────────────────────────────

/** Local calendar date "YYYY-MM-DD" (browser time zone unless tz). */
export function isoDay(ms: number = Date.now(), tz?: string): string {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
  return fmt.format(new Date(ms));
}

/** Date part of a date or datetime string ("2026-12-03T…" → "2026-12-03"). */
export function dayOf(v: string | null | undefined, tz?: string): string | null {
  if (!v) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : isoDay(t, tz);
}

/** Whole days from `fromDay` to `toDay` (both YYYY-MM-DD). */
export function daysBetween(fromDay: string, toDay: string): number {
  return Math.round((Date.parse(`${toDay}T00:00:00Z`) - Date.parse(`${fromDay}T00:00:00Z`)) / 86_400_000);
}

export function addDays(day: string, n: number): string {
  const t = Date.parse(`${day}T00:00:00Z`) + n * 86_400_000;
  return new Date(t).toISOString().slice(0, 10);
}

/** Monday of the week of `day` (ISO week, Monday first). */
export function mondayOf(day: string): string {
  const dow = new Date(`${day}T00:00:00Z`).getUTCDay(); // 0 = Sunday
  return addDays(day, -((dow + 6) % 7));
}

// ── work orders ───────────────────────────────────────────────────

/** S4-5: overdue = scheduled_for < today (local) and before completed. */
export function isWoOverdue(wo: Pick<WorkOrder, "state" | "scheduled_for">, today: string): boolean {
  const d = dayOf(wo.scheduled_for);
  return !!d && d < today && isActive(wo.state);
}

/** Instant the WO was (first) scheduled — from the audit trail. */
export function scheduledAt(wo: Pick<WorkOrder, "history">): string | null {
  return wo.history?.find((h) => h.to_state === "scheduled")?.at ?? null;
}

/** S4-6: scheduled after Monday 00:00 of the week of scheduled_for → "encaixe" (does not count for compliance). */
export function isBreakIn(wo: Pick<WorkOrder, "history" | "scheduled_for">, tz?: string): boolean {
  const target = dayOf(wo.scheduled_for);
  const at = scheduledAt(wo);
  if (!target || !at) return false;
  const atDay = dayOf(at, tz);
  return !!atDay && atDay >= mondayOf(target);
}

/** Remaining hours (S4-5): technician's estimate, else max(estimated − labor, 0). */
export function remainingHours(wo: Pick<WorkOrder, "remaining_hours" | "estimated_hours" | "labor_hours">): number {
  if (wo.remaining_hours != null) return Math.max(0, wo.remaining_hours);
  return Math.max(0, (wo.estimated_hours ?? 0) - (wo.labor_hours ?? 0));
}

/** Technician order (workflows §3): emergency > overdue > today > the rest (by date). */
export function technicianRank(wo: WorkOrder, today: string): number {
  if (wo.type === "corrective" && wo.urgency === "emergency" && isActive(wo.state)) return 0;
  if (isWoOverdue(wo, today)) return 1;
  if (wo.state === "in_progress") return 2;
  if (dayOf(wo.scheduled_for) === today) return 3;
  return 4;
}

export function sortForTechnician(list: WorkOrder[], today: string): WorkOrder[] {
  return list.slice().sort((a, b) =>
    technicianRank(a, today) - technicianRank(b, today)
    || (dayOf(a.scheduled_for) ?? "9999").localeCompare(dayOf(b.scheduled_for) ?? "9999")
    || a.number.localeCompare(b.number, "pt-BR", { numeric: true }));
}

const hoursFmt = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });

/** Hours with up to 2 decimals (quarter-hour labor: "0,75 h", "1,5 h"). */
export function hoursLabel(h: number | null | undefined): string {
  return h == null || !Number.isFinite(h) ? "—" : `${hoursFmt.format(h)} h`;
}

// ── plans (S4-3) ──────────────────────────────────────────────────

/** Trigger whose date comes first (tie → larger consumed). Null when no trigger projects a date. */
export function dueTrigger(p: Pick<PlanStatus, "triggers" | "due_trigger">): PlanTriggerStatus | null {
  if (p.due_trigger) {
    const hit = p.triggers.find((t) => t.measure === p.due_trigger);
    if (hit) return hit;
  }
  const withDate = p.triggers.filter((t) => !!t.projected_date);
  if (!withDate.length) return null;
  return withDate.slice().sort((a, b) =>
    (a.projected_date as string).localeCompare(b.projected_date as string) || b.consumed - a.consumed)[0];
}

/** Days until the projected date (0 when overdue/due — S4-3: past due → today). */
export function daysToDue(p: Pick<PlanStatus, "projected_date" | "consumed" | "days_to_due">, today: string): number | null {
  if (p.days_to_due != null) return p.days_to_due;
  if ((p.consumed ?? 0) >= 1) return 0;
  if (!p.projected_date) return null;
  return Math.max(0, daysBetween(today, p.projected_date));
}

export function sortPlans(list: PlanStatus[], today: string): PlanStatus[] {
  return list.slice().sort((a, b) => {
    const da = daysToDue(a, today);
    const db = daysToDue(b, today);
    if (da !== db) return (da ?? Number.MAX_SAFE_INTEGER) - (db ?? Number.MAX_SAFE_INTEGER);
    return PLAN_STATE_ORDER[a.state] - PLAN_STATE_ORDER[b.state] || (b.consumed ?? 0) - (a.consumed ?? 0);
  });
}

/** Plan at or past 100 % of an interval (S4-3). */
export function isPastDue(p: Pick<PlanStatus, "consumed" | "past_due">): boolean {
  return p.past_due ?? (p.consumed ?? 0) >= 1;
}

/** "45.800 h no horímetro", "9.320.000 ciclos no contador". */
export function nextDueUsageLabel(n: PlanStatus["next_due_usage"]): string | null {
  if (!n) return null;
  if (n.measure === "hours") return `${formatInt(n.value)} h no horímetro`;
  if (n.measure === "cycles") return `${formatInt(n.value)} ciclos no contador`;
  if (n.measure === "starts") return `${formatInt(n.value)} partidas no contador`;
  return null;
}

export function triggerUsageLabel(t: Pick<PlanTriggerStatus, "measure" | "usage" | "interval">): string {
  const unit = t.measure === "hours" ? " h" : t.measure === "days" ? " dias" : t.measure === "cycles" ? " ciclos" : " partidas";
  const f = (v: number) => (t.measure === "hours" ? formatDec1(v) : formatInt(v));
  return `${f(t.usage)} / ${f(t.interval)}${unit}`;
}

/** Plan state from a consumed fraction (domain §5.1) — used for plan previews. */
export function planStateFor(consumed: number, tolerance = 0.1): PlanState {
  if (consumed >= 1 + tolerance) return "overdue";
  if (consumed >= 1) return "due";
  if (consumed >= 0.8) return "due_soon";
  return "ok";
}
