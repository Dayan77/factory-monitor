import type {
  AlertEvent, Component, Line, LineProduction, Machine, MachineUsage, MachineUsagePosition, PositionDetail, Tag, TreePlant,
} from "@/services/types";

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

// ── Assets & usage ────────────────────────────────────────────────

export function plantTree(): TreePlant[] {
  return [
    {
      id: "plant-1", name: "Planta Manaus", timezone: "America/Manaus",
      lines: [
        {
          id: "line-1", name: "Linha 1 — Montagem",
          status_summary: { running: 2, stopped: 0, error: 0, unknown: 0, maintenance: 0 },
          machines: [
            { id: "m-1", name: "Prensa P-01", status: "running", last_seen_at: TS, positions_count: 3 },
            { id: "m-2", name: "Robô R-02", status: "running", last_seen_at: TS, positions_count: 6 },
          ],
        },
        {
          id: "line-2", name: "Linha 2 — Envase",
          status_summary: { running: 1, stopped: 1, error: 1, unknown: 0, maintenance: 0 },
          machines: [
            { id: "m-3", name: "Envasadora E-01", status: "error", last_seen_at: TS, positions_count: 2 },
            { id: "m-4", name: "Tampadora T-01", status: "stopped", last_seen_at: TS, positions_count: 1 },
            { id: "m-5", name: "Rotuladora R-01", status: "running", last_seen_at: TS, positions_count: 1 },
          ],
        },
      ],
    },
  ];
}

export function usagePosition(over: Partial<MachineUsagePosition> = {}): MachineUsagePosition {
  return {
    position_id: "pos-1", name: "Cilindro de fixação", component_type: "pneumatic_cylinder",
    cycles_total: 3_600_000, hours_total: 1200.5, starts_total: 40, component_id: "c-1",
    life_consumed_pct: 0.72, life_measure: "cycles", ...over,
  };
}

export function machineUsage(over: Partial<MachineUsage> = {}): MachineUsage {
  return {
    machine_id: "m-1",
    positions: [
      usagePosition(),
      usagePosition({ position_id: "pos-2", name: "Motor principal", component_type: "electric_motor", cycles_total: 0, hours_total: 12000, starts_total: 130000, component_id: "c-2", life_consumed_pct: 0.95, life_measure: "starts" }),
      usagePosition({ position_id: "pos-3", name: "Garra", component_type: "gripper", cycles_total: 10, hours_total: 1, starts_total: 1, component_id: "c-3", life_consumed_pct: 0.1 }),
    ],
    production: { good: 980, scrap: 20, total: 1000, timezone: "America/Manaus" },
    ...over,
  };
}

export function component(over: Partial<Component> = {}): Component {
  return {
    id: "c-1", position_id: "pos-1", component_type: "pneumatic_cylinder", manufacturer: "Festo", model: "DSBC-50",
    serial_number: "SN-001", installed_at: "2026-06-01T10:00:00Z", removed_at: null, removal_reason: null,
    nominal_life: { cycles: 5_000_000 }, state: "installed", life_consumed_pct: 0.72, life_measure: "cycles", ...over,
  };
}

export function positionDetail(over: Partial<PositionDetail> = {}): PositionDetail {
  return {
    id: "pos-1", machine_id: "m-1", name: "Cilindro de fixação", component_type: "pneumatic_cylinder", cycles_per_part: 2,
    installed_component: component(),
    usage: { cycles_total: 3_600_000, hours_total: 1200.5, starts_total: 40 },
    component_usage: {
      cycles: 3_600_000, hours: 1200.5, starts: 40, life_consumed_pct: 0.72, life_measure: "cycles",
      projected_end_date: "2026-12-14", rul_days: 70,
    },
    ...over,
  };
}

export function lineProduction(over: Partial<LineProduction> = {}): LineProduction {
  return {
    line_id: "line-1", start: "2026-10-05T00:00:00Z", stop: "2026-10-05T03:00:00Z",
    good: 290, scrap: 10, total: 300, cadence_per_hour: 100,
    points: [
      { t: "2026-10-05T00:00:00Z", good: 95, scrap: 5 },
      { t: "2026-10-05T01:00:00Z", good: 100, scrap: 0 },
      { t: "2026-10-05T02:00:00Z", good: 95, scrap: 5 },
    ],
    ...over,
  };
}
