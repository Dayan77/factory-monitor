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

export type RealtimeMessage = SensorReadingMessage | MachineStatusMessage | UsageMessage;

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
