import type { AlertEvent, Line, Machine, Tag } from "@/services/types";

const TS = "2026-10-05T12:00:00.000Z";

export function line(over: Partial<Line> = {}): Line {
  return {
    id: "line-1", tenant_id: "t1", name: "Linha 1 — Montagem", description: null,
    location: "Pavilhão A", is_active: true, created_at: TS, updated_at: TS, ...over,
  };
}

export function machine(over: Partial<Machine> = {}): Machine {
  return {
    id: "m-1", tenant_id: "t1", line_id: "line-1", name: "Torno CNC-01", model: "Mazak QT-200",
    serial_number: null, status: "running", last_seen_at: TS, created_at: TS, updated_at: TS, ...over,
  };
}

export function tag(over: Partial<Tag> = {}): Tag {
  return {
    id: "tag-1", tenant_id: "t1", machine_id: "m-1", name: "Temperatura do fuso", description: null,
    protocol: "modbus_tcp", address: "40001", data_type: "float", unit: "°C", scale_factor: 1, offset: 0,
    min_value: null, max_value: null, poll_interval_ms: 1000, is_active: true, created_at: TS, ...over,
  };
}

export function alertEvent(over: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: "a-1", rule_id: "r-1", rule_name: "Temperatura alta", severity: "critical",
    machine_name: "Torno CNC-01", message: "Temperatura 92 °C (limite 80 °C)",
    triggered_at: TS, resolved_at: null, value_at_trigger: 92, notified: true, ...over,
  };
}
