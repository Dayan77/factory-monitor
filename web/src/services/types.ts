export type MachineStatus = "running" | "stopped" | "maintenance" | "error" | "unknown";

export interface Machine {
  id: string;
  name: string;
  model?: string;
  status: MachineStatus;
  line_id: string;
  line_name: string;
  oee: number;          // 0–100
  availability: number; // 0–100
  performance: number;  // 0–100
  quality: number;      // 0–100
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
  tag_name: string;
  value: number;
  unit: string;
  timestamp: string;
}

export interface AlertEvent {
  id: string;
  rule_name: string;
  machine_name: string;
  severity: "info" | "warning" | "critical";
  message: string;
  triggered_at: string;
  resolved_at?: string;
}

export interface OeeSnapshot {
  oee: number;
  availability: number;
  performance: number;
  quality: number;
  timestamp: string;
}
