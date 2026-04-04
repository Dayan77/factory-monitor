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
