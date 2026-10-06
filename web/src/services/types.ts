import type { NotificationMessage } from "./maintenanceTypes";

// ── Admin / Superadmin ────────────────────────────────────────────

export type TenantPlan = "trial" | "starter" | "professional" | "enterprise";

export interface Tenant {
  id:               string;
  name:             string;
  slug:             string;
  is_active:        boolean;
  plan:             TenantPlan;
  trial_ends_at:    string | null;
  suspended_reason: string | null;
  max_users:        number;
  max_machines:     number;
  created_at:       string;
  updated_at:       string;
}

export interface TenantWithStats extends Tenant {
  user_count:    number;
  machine_count: number;
}

export interface TenantUser {
  id:            string;
  tenant_id:     string;
  email:         string;
  name:          string;
  role:          "operator" | "manager" | "admin" | "superadmin";
  is_active:     boolean;
  last_login_at: string | null;
  created_at:    string;
}

// ── Domain types (mirror server/src/api/schemas.py) ───────────────

export type MachineStatus = "running" | "stopped" | "maintenance" | "error" | "unknown";

export interface Machine {
  id: string;
  tenant_id: string;
  line_id: string;
  name: string;
  model: string | null;
  serial_number: string | null;
  status: MachineStatus;
  /** Server receive time of the last reading for any tag of this machine. */
  last_seen_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Line {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  location: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type SemanticRole =
  | "analog" | "cycle_counter" | "cycle_pulse" | "run_state"
  | "part_counter_good" | "part_counter_scrap" | "part_counter_total"
  | "fault_state" | "stroke_time" | "speed" | "current";

export type ProtocolType = "modbus_tcp" | "opc_ua" | "mqtt" | "http";
export type DataType = "float" | "int" | "bool" | "string";

export interface Tag {
  id: string;
  tenant_id: string;
  machine_id: string | null;
  name: string;
  description: string | null;
  protocol: ProtocolType;
  address: string;
  data_type: DataType;
  unit: string | null;
  scale_factor: number;
  offset: number;
  min_value: number | null;
  max_value: number | null;
  poll_interval_ms: number;
  is_active: boolean;
  /** Sprint 5: field gateway that reads this tag (docs/gateways.md). */
  gateway_id?: string | null;
  created_at: string;
  /** Fase 1 — what the tag means for usage processing (default "analog"). */
  semantic_role?: SemanticRole;
  position_id?: string | null;
  /** PLC counter rollover value (counter tags). */
  counter_max?: number | null;
}

export interface SensorReading {
  tag_id: string;
  value: number;
  unit?: string | null;
  timestamp: string;
}

export interface SensorHistory {
  tag_id: string;
  points: { timestamp: string; value: number }[];
}

export type Severity = "info" | "warning" | "critical";

export interface AlertEvent {
  id: string;
  rule_id: string;
  rule_name: string;
  severity: Severity;
  machine_name?: string | null;
  message?: string | null;
  triggered_at: string;
  resolved_at?: string | null;
  value_at_trigger?: number | null;
  notified?: boolean;
}

// ── WebSocket messages (/ws/sensors/{tenant_id}) ─────────────────

export interface SensorReadingMessage extends SensorReading {
  type: "sensor_reading";
}

export interface MachineStatusMessage {
  type: "machine_status";
  machine_id: string;
  machine_name?: string;
  status: MachineStatus;
  previous_status?: MachineStatus;
  last_seen_at?: string | null;
  timestamp?: string;
}

export type UsageMeasure = "cycles" | "hours" | "starts" | "good" | "scrap" | "total";

/**
 * Usage delta (throttled ≤1/s per position+measure). `total` is the accumulator value
 * (position usage_total, or machine total when position_id is null); hours in hours.
 */
export interface UsageMessage {
  type: "usage";
  position_id: string | null;
  machine_id: string | null;
  measure: UsageMeasure;
  delta: number;
  total: number;
  timestamp?: string;
}

/** Sprint 5: {type: 'predictive_alert', event, alert} — only to users who may read predictive alerts. */
export interface PredictiveAlertRealtimeMessage {
  type: "predictive_alert";
  event: string | null;
  alert: { id: string; severity: string; state: string; position_id: string | null; machine_id: string | null };
}

export type RealtimeMessage =
  | SensorReadingMessage | MachineStatusMessage | UsageMessage | DowntimeMessage | NotificationMessage
  | PredictiveAlertRealtimeMessage;

// ── Assets & usage (Fase 1 — mirror server/src/api/schemas_assets.py) ──
// Conventions: life_consumed_pct is a FRACTION (0..1, may exceed 1, never truncated);
// hours in hours; timestamps ISO-8601 UTC; projected_end_date is a date (YYYY-MM-DD).

export type ComponentType =
  | "pneumatic_cylinder" | "electric_motor" | "servo" | "robot_axis" | "valve"
  | "gripper" | "bearing" | "belt" | "pump" | "spindle" | "other";

export const COMPONENT_TYPES: readonly ComponentType[] = [
  "pneumatic_cylinder", "electric_motor", "servo", "robot_axis", "valve",
  "gripper", "bearing", "belt", "pump", "spindle", "other",
];

export type RemovalReason = "failure" | "preventive_replacement" | "upgrade" | "other";
export type LifeMeasure = "cycles" | "hours" | "starts";

export interface StatusSummary {
  running: number;
  stopped: number;
  error: number;
  unknown: number;
  maintenance?: number;
}

export interface TreeMachine {
  id: string;
  name: string;
  status: MachineStatus;
  last_seen_at: string | null;
  positions_count: number;
}

export interface TreeLine {
  id: string;
  name: string;
  output_machine_id?: string | null;
  bottleneck_machine_id?: string | null;
  status_summary: StatusSummary;
  machines: TreeMachine[];
}

export interface TreePlant {
  id: string;
  name: string;
  timezone: string;
  lines: TreeLine[];
}

export interface NominalLife {
  cycles?: number | null;
  hours?: number | null;
  starts?: number | null;
}

export interface UsageValues {
  cycles: number;
  hours: number;
  starts: number;
}

export interface Component {
  id: string;
  tenant_id?: string;
  position_id?: string | null;
  last_position_id?: string | null;
  component_type?: ComponentType;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  installed_at: string | null;
  removed_at?: string | null;
  removal_reason?: RemovalReason | null;
  nominal_life: NominalLife;
  state?: "installed" | "in_stock" | "removed";
  usage_since_install?: UsageValues;
  life_consumed_pct?: number | null;
  life_measure?: LifeMeasure | null;
  created_at?: string;
  updated_at?: string;
}

export interface Position {
  id: string;
  machine_id: string;
  name: string;
  component_type: ComponentType;
  cycles_per_part: number;
  counter_tag_id?: string | null;
  installed_component_id?: string | null;
}

export interface PositionUsage {
  cycles_total: number;
  hours_total: number;
  starts_total: number;
  /** No own cycle tag: cycles = machine parts × cycles_per_part. */
  cycles_estimated?: boolean;
}

export interface ComponentUsage {
  cycles: number;
  hours: number;
  starts: number;
  life_consumed_pct: number | null;
  life_measure?: LifeMeasure | null;
  projected_end_date: string | null;
  rul_days?: number | null;
  rul_reason?: "no_nominal_life" | "no_recent_usage" | "insufficient_data" | string | null;
  low_confidence?: boolean;
  overdue?: boolean;
}

export interface PositionDetail extends Position {
  installed_component: Component | null;
  usage: PositionUsage;
  component_usage: ComponentUsage | null;
}

export interface MachineUsagePosition {
  position_id: string;
  name: string;
  component_type: ComponentType;
  cycles_total: number;
  hours_total: number;
  starts_total: number;
  cycles_estimated?: boolean;
  component_id?: string | null;
  life_consumed_pct: number | null;
  life_measure?: LifeMeasure | null;
}

export interface ProductionCounts {
  good: number;
  scrap: number;
  total: number;
  /** Start of the productive day (plant local midnight, UTC). */
  start?: string | null;
  timezone?: string | null;
}

export interface MachineUsage {
  machine_id?: string;
  positions: MachineUsagePosition[];
  production: ProductionCounts;
  hours_total?: number;
  starts_total?: number;
}

export interface UsagePoint {
  t: string;
  value: number;
}

export interface UsageHistory {
  measure?: UsageMeasure;
  bucket?: string;
  start?: string;
  stop?: string;
  points: UsagePoint[];
}

export interface ProductionPoint {
  t: string;
  good: number;
  scrap: number;
}

export interface LineProduction {
  line_id?: string;
  output_machine_id?: string | null;
  start?: string;
  stop?: string;
  good: number;
  scrap: number;
  total: number;
  cadence_per_hour: number | null;
  cadence_running_per_hour?: number | null;
  /** e.g. "no_output_machine" when the line has no production counter. */
  reason?: string | null;
  points: ProductionPoint[];
}

export interface ComponentCreateBody {
  component_type: ComponentType;
  manufacturer?: string | null;
  model?: string | null;
  serial_number?: string | null;
  nominal_life?: NominalLife;
  /** Install right away in this position. */
  position_id?: string | null;
}

// ── Public site ───────────────────────────────────────────────────

export interface LeadBody {
  name: string;
  email: string;
  company: string;
  phone: string;
  message: string;
  consent: boolean;
  /** Honeypot — must stay empty. */
  website: string;
  source?: string;
}

// ── Camera ────────────────────────────────────────────────────────

export interface Camera {
  id:          string;
  tenant_id:   string;
  machine_id:  string | null;
  name:        string;
  rtsp_url:    string;
  description: string | null;
  is_active:   boolean;
  created_at:  string;
  updated_at:  string;
}

// ── Eficiência produtiva (Fase 2 — mirror server/src/api/schemas_production.py) ──
// Conventions: every ratio (availability, performance, quality, oee, teep, pct, data_coverage) is a
// FRACTION 0..1 (performance may exceed 1, never truncated) or null when undefined (e.g. PBT = 0);
// durations in seconds (*_s) or hours (*_h); timestamps ISO-8601 UTC; productive_day is YYYY-MM-DD.

export type OeeScope = "machine" | "line" | "plant";
export type OeeGroupBy = "none" | "hour" | "shift" | "day";

/** Reason-code categories seeded by the server (tenants may add their own). */
export type ReasonCategory = "PLN" | "EQP" | "SET" | "MAT" | "OPR" | "QLT" | "UTL" | "MNT" | "UNK" | string;
export type OeeLossKind = "none" | "availability" | "performance" | "quality";

export interface ReasonCode {
  id: string;
  tenant_id: string | null;
  code: string;
  category: ReasonCategory;
  label: string;
  planned: boolean;
  oee_loss: OeeLossKind | string;
  is_active: boolean;
  /** Seeded default (tenant_id null) — read-only for tenants. */
  is_default: boolean;
}

export interface ReasonCodeCreateBody {
  code: string;
  category: string;
  label: string;
  planned?: boolean;
  oee_loss?: OeeLossKind;
}

export type ReasonCodeUpdateBody = Partial<Omit<ReasonCodeCreateBody, "code"> & { is_active: boolean }>;

export interface ShiftBreak {
  start_local: string; // "HH:MM"
  end_local: string;
  reason_code?: string;
}

/** 0 = segunda … 6 = domingo (productive day of the shift start). */
export type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6;

export interface Shift {
  id: string;
  tenant_id: string;
  plant_id: string;
  name: string;
  start_local: string;
  end_local: string;
  weekdays: number[];
  breaks: ShiftBreak[];
  is_active: boolean;
  /** Version in effect for productive days [valid_from, valid_to] (null = open) — kpis.md D11. */
  valid_from?: string | null;
  valid_to?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShiftBody {
  plant_id?: string;
  name: string;
  start_local: string;
  end_local: string;
  weekdays: number[];
  breaks: ShiftBreak[];
  is_active?: boolean;
  /** Edit: the new hours/days/breaks apply from this productive day (YYYY-MM-DD); before it, the old ones. */
  effective_from?: string;
}

export type CalendarExceptionKind = "holiday" | "extra_shift" | "planned_stop";

export interface CalendarException {
  id: string;
  tenant_id: string;
  plant_id: string;
  date: string;
  kind: CalendarExceptionKind;
  shift_id: string | null;
  start_local: string | null;
  end_local: string | null;
  reason_code: string | null;
  note: string | null;
  created_at: string;
}

export interface CalendarExceptionBody {
  plant_id?: string;
  date: string;
  kind: CalendarExceptionKind;
  shift_id?: string | null;
  start_local?: string | null;
  end_local?: string | null;
  reason_code?: string | null;
  note?: string | null;
}

export interface Product {
  id: string;
  tenant_id: string;
  code: string;
  name: string;
  created_at: string;
}

export interface MachineProduct {
  id: string;
  machine_id: string;
  product_id: string;
  product_code: string | null;
  product_name: string | null;
  /** PRI — ideal cycle time of one machine cycle (s). */
  ideal_cycle_time_s: number;
  parts_per_cycle: number;
  is_default: boolean;
  ideal_time_per_part_s: number;
}

export interface MachineProductBody {
  product_id: string;
  ideal_cycle_time_s: number;
  parts_per_cycle?: number;
  is_default?: boolean;
}

export interface ProductionRun {
  id: string;
  machine_id: string;
  product_id: string;
  product_code: string | null;
  started_at: string;
  ended_at: string | null;
}

export type DowntimeSource = "auto_fault" | "auto_run_state" | "auto_cycle_gap" | "manual";

export interface DowntimeEvent {
  id: string;
  machine_id: string;
  machine_name?: string | null;
  line_id?: string | null;
  start: string;
  /** null while the stop is still open. */
  end: string | null;
  duration_s: number | null;
  stop_start?: string | null;
  source: DowntimeSource | string;
  /** null or "UNK-01" = not justified yet. */
  reason_code: string | null;
  suggested_reason_code: string | null;
  effective_reason_code?: string;
  planned: boolean;
  fault_code: number | null;
  fault_since?: string | null;
  failure_id?: string | null;
  comment: string | null;
  shift_id?: string | null;
  productive_day?: string | null;
  justified_by?: string | null;
  justified_at?: string | null;
  unjustified?: boolean;
}

export interface DowntimeQuery {
  machine_id?: string;
  line_id?: string;
  start?: string;
  stop?: string;
  open?: boolean;
  unjustified?: boolean;
  limit?: number;
}

export interface DowntimePatchBody {
  reason_code?: string | null;
  comment?: string | null;
}

export interface DowntimeCreateBody {
  machine_id: string;
  start: string;
  end?: string | null;
  reason_code: string;
  comment?: string | null;
}

/** Subset of DowntimeEvent carried by the socket (absent keys = unchanged). */
export type DowntimeEventPatch = Partial<DowntimeEvent> & Pick<DowntimeEvent, "id" | "machine_id" | "start">;

/** WebSocket {type: "downtime", action, event} — a stop opened, changed, closed or was justified. */
export interface DowntimeMessage {
  type: "downtime";
  action?: string;
  event: DowntimeEventPatch;
}

export interface OeePoint {
  t: string;
  end?: string;
  label: string;
  availability: number | null;
  performance: number | null;
  quality: number | null;
  oee: number | null;
  teep?: number | null;
  pbt_s?: number;
  good?: number;
  scrap?: number;
}

export interface OeeResult {
  scope?: OeeScope;
  id?: string;
  period_start?: string;
  period_end?: string;
  granularity?: string;
  availability: number | null;
  performance: number | null;
  quality: number | null;
  oee: number | null;
  teep: number | null;
  loading?: number | null;
  /** e.g. "no_planned_time" when PBT = 0. */
  reason?: string | null;
  ideal_cycle_time_suspect?: boolean;
  pot_s: number;
  pdot_s: number;
  pbt_s: number;
  adot_s: number;
  apt_s: number;
  nodata_s?: number;
  micro_stop_count: number;
  micro_stop_loss_s: number;
  micro_stops_per_apt_hour?: number | null;
  speed_loss_s: number;
  quality_loss_s?: number;
  ideal_produced_s?: number;
  net_productive_s?: number;
  produced?: number;
  good: number;
  scrap: number;
  cadence_planned_per_hour?: number | null;
  cadence_running_per_hour?: number | null;
  availability_operational?: number | null;
  data_coverage: number | null;
  product_unknown: boolean;
  /** S3-7: parts counted outside every shift instance (not in P/Q/OEE/TEEP). */
  produced_outside_schedule?: { total: number; good: number; scrap: number };
  /** D7: an hourly count bucket was only partly inside the window/schedule (counted pro rata). */
  counts_prorated?: boolean;
  calendar_default?: boolean;
  points: OeePoint[];
}

export interface OeeQuery {
  scope: OeeScope;
  id: string;
  start?: string;
  stop?: string;
  group_by?: OeeGroupBy;
}

/** Pseudo reason codes in the Pareto (performance/quality losses). */
export const PSEUDO_LOSS_CODES = ["micro_stops", "speed_loss", "quality_loss"] as const;

export interface ParetoRow {
  /** Reason code, or a pseudo code (micro_stops / speed_loss / quality_loss); null when grouped by category. */
  reason_code: string | null;
  category: string;
  label: string;
  planned: boolean;
  duration_s: number;
  events: number;
  pct: number | null;
  cumulative_pct: number | null;
}

export interface Reliability {
  scope?: string;
  id?: string;
  period_start?: string;
  period_end?: string;
  failures: number;
  incipient?: number;
  short_faults: number;
  operating_h: number;
  mtbf_h: number | null;
  mttr_h: number | null;
  mttr_s?: number | null;
  availability_inherent: number | null;
  failure_rate_per_h?: number | null;
  /** e.g. "no_failures". */
  reason?: string | null;
}

export interface MachineProductionRow {
  machine_id: string;
  machine_name: string;
  good: number;
  scrap: number;
  total: number;
  availability?: number | null;
  performance?: number | null;
  quality?: number | null;
  oee?: number | null;
}

export interface ShiftReport {
  line_id: string;
  productive_day: string;
  shift_id: string | null;
  shift_name: string | null;
  period_start: string | null;
  period_end: string | null;
  oee: OeeResult | null;
  pareto: ParetoRow[];
  downtime: DowntimeEvent[];
  production: MachineProductionRow[];
  /** e.g. "no_shift" when no shift instance exists that day. */
  reason?: string | null;
}

// Overview ("raio-x"). The server payload is normalized by services/efficiency.ts → normalizeOverview,
// so these are the shapes the UI consumes.

export interface OverviewWorstMachine {
  id: string;
  name: string;
  oee: number | null;
}

export interface OverviewLine {
  line_id: string;
  name: string;
  output_machine_id?: string | null;
  /** OEE of the productive day so far. */
  oee: number | null;
  oee_current_shift?: number | null;
  current_shift_name?: string | null;
  availability: number | null;
  performance: number | null;
  quality: number | null;
  good: number;
  scrap: number;
  /** Realized cadence (parts/h). */
  cadence: number | null;
  data_coverage?: number | null;
  open_downtime_count: number;
  unjustified_count: number;
  worst_machine: OverviewWorstMachine | null;
}

export interface OverviewMachineInError {
  id: string;
  name: string;
  line_id?: string | null;
  since: string | null;
  fault_code: number | null;
}

export interface OverviewComponentNearEol {
  component_id?: string | null;
  position_id: string;
  position_name: string;
  machine_id?: string | null;
  machine_name: string | null;
  life_consumed_pct: number;
  life_measure?: LifeMeasure | null;
  projected_end_date: string | null;
}

export interface Overview {
  plant_id?: string;
  plant_name?: string;
  productive_day?: string;
  generated_at?: string;
  lines: OverviewLine[];
  top_losses: ParetoRow[];
  machines_in_error: OverviewMachineInError[];
  components_near_eol: OverviewComponentNearEol[];
  /** Sprint 5: field gateways of the plant (docs/gateways.md). Absent on older servers. */
  gateways?: OverviewGateway[];
}

export interface OverviewGateway {
  id: string;
  name: string;
  status: "pending" | "online" | "degraded" | "offline";
  version: string | null;
  last_seen_at: string | null;
  config_version: number | null;
}
