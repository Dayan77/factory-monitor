/**
 * Maintenance (Sprint 4 / Fase 3) API shapes — mirror the backend contract
 * (docs/domain.md §5, docs/maintenance_workflows.md). Optional fields marked "UI extra" are
 * read when the server sends them and never required.
 */

// ── roles ─────────────────────────────────────────────────────────

export type UserRoleName =
  | "maintenance_planner" | "technician" | "production_supervisor" | "manager" | "admin" | "operator" | "superadmin";

// ── plans ─────────────────────────────────────────────────────────

export type PlanState = "ok" | "due_soon" | "due" | "overdue";
export type TriggerMeasure = "cycles" | "hours" | "starts" | "days";
export type PlanWoType = "preventive" | "predictive";

export interface PlanTriggerStatus {
  measure: TriggerMeasure;
  interval: number;
  /** Usage since the last reset (days for the calendar trigger). */
  usage: number;
  /** usage / interval (fraction, not truncated). */
  consumed: number;
  /** Projected due date of this trigger (plant-local ISO date) — null when it cannot be projected. */
  projected_date: string | null;
}

export interface PlanStatus {
  plan_id: string;
  plan_name: string;
  position_id: string | null;
  position_name: string | null;
  machine_id: string | null;
  machine_name: string | null;
  state: PlanState;
  /** max_i(consumed_i) — fraction, may exceed 1 (null when the plan has no evaluable trigger). */
  consumed: number | null;
  /** Trigger that determined `consumed` (and the state). */
  determining_trigger: TriggerMeasure;
  triggers: PlanTriggerStatus[];
  /** Earliest projected date among the triggers (S4-3). */
  projected_date: string | null;
  /** Absolute counter value at which the plan is due ("vence em 45 800 h no horímetro"). */
  next_due_usage: { measure: TriggerMeasure; value: number } | null;
  open_work_order_id: string | null;
  /** Extras sent by the server (S4-3): trigger whose date comes first, days until it, open WO summary. */
  due_trigger?: TriggerMeasure | null;
  days_to_due?: number | null;
  past_due?: boolean;
  calendar_due_date?: string | null;
  open_work_order_number?: string | null;
  open_work_order_state?: WoState | null;
  line_id?: string | null;
  /** Why no date could be projected (no_recent_usage, no_triggers…). */
  reason?: string | null;
}

export interface PlanStatusQuery {
  plant_id?: string;
  line_id?: string;
  state?: PlanState;
}

export interface TaskItem {
  id: string;
  text: string;
}

export interface MaintenancePlan {
  id: string;
  position_id: string | null;
  machine_id: string | null;
  name: string;
  task_list: TaskItem[];
  estimated_hours: number;
  wo_type: PlanWoType;
  interval_cycles: number | null;
  interval_hours: number | null;
  interval_starts: number | null;
  interval_days: number | null;
  /** Overdue tolerance in percent of the interval (0–100, default 10). */
  tolerance_pct: number | null;
  reset_on_replacement: boolean;
  stops_machine: boolean;
  is_active: boolean;
  /** Server extras */
  last_reset_at?: string | null;
  state?: PlanState;
  consumed?: number | null;
}

export type MaintenancePlanBody = Omit<MaintenancePlan, "id" | "task_list" | "last_reset_at" | "state" | "consumed"> & {
  task_list: { id?: string; text: string }[];
  /** Plan that already existed on paper: last execution (create only). */
  last_done_at?: string | null;
};

// ── work orders ───────────────────────────────────────────────────

export type WoState =
  | "draft" | "open" | "approved" | "scheduled" | "in_progress" | "on_hold" | "completed" | "closed" | "cancelled";
export type WoType = "preventive" | "predictive" | "corrective" | "improvement";
export type WoUrgency = "emergency" | "deferred";
export type CancelReason = "superseded" | "production_conflict" | "duplicate" | "not_needed" | "other";

export interface WoTask {
  id: string;
  text: string;
  done: boolean;
}

export interface WoHistoryEntry {
  at: string;
  /** User id (or name) who made the change. */
  by: string | null;
  /** UI extra */
  by_name?: string | null;
  from_state: WoState | null;
  to_state: WoState;
  note: string | null;
}

export interface WoReplacement {
  id?: string;
  position_id: string;
  position_name?: string | null;
  removed_component_id?: string | null;
  removed_component?: ComponentSummary | null;
  new_component_id?: string | null;
  /** Server name of the new component id. */
  installed_component_id?: string | null;
  new_component?: ComponentSummary | null;
  removal_reason: string;
  /** Set when the WO completed and the swap was applied. */
  applied_at?: string | null;
  at?: string | null;
}

export interface ComponentSummary {
  id?: string;
  manufacturer?: string | null;
  model?: string | null;
  serial_number?: string | null;
  usage_since_install?: { cycles?: number; hours?: number; starts?: number } | null;
}

export interface WorkOrder {
  id: string;
  number: string;
  type: WoType;
  urgency: WoUrgency | null;
  state: WoState;
  plan_id: string | null;
  position_id: string | null;
  machine_id: string | null;
  downtime_event_id: string | null;
  failure_id: string | null;
  title: string;
  description: string | null;
  task_list: WoTask[];
  estimated_hours: number | null;
  labor_hours: number;
  remaining_hours: number | null;
  stops_machine: boolean;
  /** Plant-local date (or datetime) the work is scheduled for. */
  scheduled_for: string | null;
  assignee_id: string | null;
  created_at: string;
  requested_at: string | null;
  approved_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  closed_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  hold_reason: string | null;
  replacements: WoReplacement[];
  history: WoHistoryEntry[];
  allowed_transitions: WoState[];
  /** Transitions the caller's roles may perform (server-filtered subset of allowed_transitions). */
  user_allowed_transitions?: WoState[];
  scheduled_at?: string | null;
  overdue?: boolean;
  /** UI extras (resolved client-side from the plant tree / users when absent). */
  machine_name?: string | null;
  position_name?: string | null;
  assignee_name?: string | null;
  plan_name?: string | null;
  line_id?: string | null;
}

export interface WorkOrderQuery {
  state?: WoState | string;
  type?: WoType;
  assignee_id?: string;
  machine_id?: string;
  line_id?: string;
  plan_id?: string;
  overdue?: boolean;
  from?: string;
  to?: string;
}

export interface WorkOrderCreateBody {
  type: WoType;
  urgency?: WoUrgency | null;
  machine_id: string;
  position_id?: string | null;
  downtime_event_id?: string | null;
  plan_id?: string | null;
  title: string;
  description?: string | null;
  estimated_hours?: number | null;
  stops_machine?: boolean;
  task_list?: { text: string }[];
}

export interface TransitionResult extends WorkOrder {
  plans_reset?: string[];
  new_draft_id?: string | null;
}

export interface TransitionBody {
  to: WoState;
  note?: string | null;
  scheduled_for?: string | null;
  assignee_id?: string | null;
  hold_reason?: string | null;
  cancel_reason?: CancelReason | null;
  /** Technician's estimate when pausing (S4-5 / S4-7). Sent alongside; ignored by servers that don't take it. */
  remaining_hours?: number | null;
}

export interface LaborBody {
  hours: number;
  note?: string | null;
  remaining_hours?: number | null;
}

export interface ReplacementBody {
  position_id: string;
  new_component_id?: string;
  new_component?: { manufacturer: string | null; model: string | null; serial_number: string | null; component_type?: string | null };
  removal_reason: string;
}

// ── failures (ISO 14224) ──────────────────────────────────────────

export type FailureSeverity = "critical" | "degraded" | "incipient";

export interface FailureRecord {
  id: string;
  machine_id: string;
  position_id: string | null;
  component_id: string | null;
  failed_at: string;
  restored_at: string | null;
  detected_at?: string | null;
  severity: FailureSeverity;
  failure_mode: string | null;
  failure_mechanism: string | null;
  failure_cause: string | null;
  detection_method: string | null;
  downtime_event_id: string | null;
  work_order_id: string | null;
  note?: string | null;
  /** UI extras */
  machine_name?: string | null;
  position_name?: string | null;
  component_type?: string | null;
  line_id?: string | null;
  component?: { id: string; manufacturer: string | null; model: string | null; serial_number: string | null; component_type: string } | null;
}

export type FailurePatchBody = Partial<Pick<FailureRecord,
  "position_id" | "component_id" | "severity" | "failure_mode" | "failure_mechanism" | "failure_cause" | "detection_method" | "restored_at" | "note">>;

export interface FailureQuery {
  machine_id?: string;
  position_id?: string;
  open?: boolean;
  from?: string;
  to?: string;
}

export interface FailureCode {
  code: string;
  label: string;
  /** Mechanism/cause group ("1 Mecânico") — optional. */
  group?: string | null;
  /** Component types for which this mode is in the short list. */
  component_types?: string[] | null;
}

export interface FailureCodes {
  modes: FailureCode[];
  mechanisms: FailureCode[];
  causes: FailureCode[];
  detection_methods: FailureCode[];
}

// ── KPIs ──────────────────────────────────────────────────────────

export interface MaintenanceKpis {
  pmp: number | null;
  schedule_compliance: number | null;
  compliance_excluded: { wo_id: string; reason: string; number?: string | null; wo_number?: string | null }[];
  backlog_hours: number;
  backlog_weeks: number | null;
  crew_capacity_hours_per_week: number | null;
  overdue_count: number;
  /** UI extras */
  pmp_reason?: string | null;
  compliance_reason?: string | null;
  backlog_weeks_reason?: string | null;
  backlog_count?: number;
  overdue_hours?: number;
  drafts_not_in_backlog?: string[];
  requests_pending?: string[];
  scheduled_count?: number;
  on_time_count?: number;
  late_count?: number;
  not_done_count?: number;
  planned_hours?: number;
  total_hours?: number;
  technicians?: number;
}

export type ReliabilityGroupBy = "position" | "component_type" | "manufacturer" | "manufacturer_model";

export interface ReliabilityRow {
  key: string;
  label: string;
  n: number;
  mtbf_h: number | null;
  mcbf: number | null;
  mttr_h: number | null;
  mttf_h: number | null;
  restored: number;
  incipient: number;
  suspensions: number;
  null_reason: string | null;
  /** Extras (server) */
  mttr_reason?: string | null;
  operating_hours?: number | null;
  machine_name?: string | null;
  component_type?: string | null;
  manufacturer?: string | null;
  model?: string | null;
  /** MTTF by model (S4-11): failed removals, suspensions, in service, measure of the nominal life. */
  failures_removed?: number | null;
  in_service?: number | null;
  mttf_measure?: "cycles" | "hours" | "starts" | null;
  mttf?: number | null;
  mttf_reason?: string | null;
  small_sample?: boolean;
}

/** GET /api/maintenance/components — installed components by % of life (S4-12). */
export interface ComponentLifeRow {
  component_id: string;
  serial_number: string | null;
  manufacturer: string | null;
  model: string | null;
  component_type: string;
  position_id: string;
  position_name: string;
  machine_id: string;
  machine_name: string;
  installed_at: string | null;
  life_consumed_pct: number | null;
  life_measure: "cycles" | "hours" | "starts" | null;
  life_reason?: string | null;
  rul_days: number | null;
  rul_measure?: string | null;
  rul_reason?: string | null;
  projected_end_date: string | null;
  low_confidence?: boolean;
  alert: "attention" | "nominal_reached" | null;
}

// ── notifications ─────────────────────────────────────────────────

export interface AppNotification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  /** In-app route that answers the notification (e.g. /maintenance/work-orders?wo=…). */
  url?: string | null;
  severity?: "info" | "warning" | "critical" | "emergency" | string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  work_order_id?: string | null;
  downtime_event_id?: string | null;
  /** Grouped notifications ("12 planos ≥ 80 %"). */
  count?: number;
  created_at: string;
  read_at: string | null;
}

export interface NotificationMessage {
  type: "notification";
  notification: AppNotification;
}

export interface TechnicianUser {
  id: string;
  name: string;
  email?: string;
  roles?: string[];
}
