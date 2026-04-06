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

// ── Domain types ──────────────────────────────────────────────────

export type MachineStatus = "running" | "stopped" | "maintenance" | "error" | "unknown";

export interface Machine {
  id: string;
  name: string;
  model?: string;
  serial_number?: string;
  status: MachineStatus;
  line_id: string;
  line_name?: string;   // joined on backend or from mock
  // OEE fields — computed by backend or mock
  oee?: number;
  availability?: number;
  performance?: number;
  quality?: number;
  updated_at: string;
}

export interface Line {
  id: string;
  name: string;
  location?: string;
  is_active: boolean;
  machine_count: number;
  running_count: number;
  oee: number;
}

export interface SensorReading {
  tag_id: string;
  tag_name?: string;
  value: number;
  unit?: string;
  timestamp: string;
}

export interface AlertEvent {
  id: string;
  rule_id: string;
  rule_name?: string;    // populated via join or mock
  machine_name?: string; // populated via join or mock
  severity: "info" | "warning" | "critical";
  message?: string;
  triggered_at: string;
  resolved_at?: string;
  value_at_trigger?: number;
  notified?: boolean;
}

export interface OeeSnapshot {
  oee: number;
  availability: number;
  performance: number;
  quality: number;
  timestamp: string;
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
