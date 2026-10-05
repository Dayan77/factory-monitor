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

export type RealtimeMessage = SensorReadingMessage | MachineStatusMessage;

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
