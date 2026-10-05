import type { DowntimeEvent, OeeResult, ParetoRow, ReasonCode, Shift } from "@/services/types";
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

// ── Eficiência (Fase 2) ───────────────────────────────────────────


export function reasonCode(over: Partial<ReasonCode> = {}): ReasonCode {
  return {
    id: `rc-${over.code ?? "EQP-01"}`, tenant_id: null, code: "EQP-01", category: "EQP", label: "Falha mecânica",
    planned: false, oee_loss: "availability", is_active: true, is_default: true, ...over,
  };
}

export function reasonCodes(): ReasonCode[] {
  return [
    reasonCode({ code: "PLN-01", category: "PLN", label: "Refeição / pausa", planned: true, oee_loss: "none" }),
    reasonCode({ code: "EQP-01", category: "EQP", label: "Falha mecânica" }),
    reasonCode({ code: "EQP-03", category: "EQP", label: "Falha pneumática/hidráulica" }),
    reasonCode({ code: "SET-01", category: "SET", label: "Setup / troca de produto" }),
    reasonCode({ code: "MAT-01", category: "MAT", label: "Falta de material" }),
    reasonCode({ code: "UNK-01", category: "UNK", label: "Não justificada" }),
    reasonCode({ code: "EQP-01-HID", category: "EQP", label: "Vazamento hidráulico", tenant_id: "t1", is_default: false }),
    reasonCode({ code: "OPR-02", category: "OPR", label: "Antigo", is_active: false, tenant_id: "t1", is_default: false }),
  ];
}

export function downtime(over: Partial<DowntimeEvent> = {}): DowntimeEvent {
  return {
    id: "d-1", machine_id: "m-1", machine_name: "Prensa P-01", line_id: "line-1",
    start: "2026-10-05T11:30:00Z", end: "2026-10-05T11:45:00Z", duration_s: 900, source: "auto_fault",
    reason_code: null, suggested_reason_code: "EQP-03", planned: false, fault_code: 17, comment: null,
    unjustified: true, ...over,
  };
}

export function oeeResult(over: Partial<OeeResult> = {}): OeeResult {
  // kpis.md example E1: POT 480 min, PDOT 30, ADOT 60 → A = 390/450; ΣPRI·PQ 350 → P = 350/390; Q = 339.5/350
  return {
    scope: "line", id: "line-1", availability: 390 / 450, performance: 350 / 390, quality: 339.5 / 350, oee: 339.5 / 450,
    teep: 0.2357, loading: 0.3125, pot_s: 480 * 60, pdot_s: 30 * 60, pbt_s: 450 * 60, adot_s: 60 * 60, apt_s: 390 * 60,
    nodata_s: 0, micro_stop_count: 12, micro_stop_loss_s: 10 * 60, speed_loss_s: 30 * 60, quality_loss_s: 10.5 * 60,
    ideal_produced_s: 350 * 60, net_productive_s: 339.5 * 60, produced: 700, good: 679, scrap: 21,
    cadence_planned_per_hour: 93.3, cadence_running_per_hour: 107.7, data_coverage: 1, product_unknown: false,
    calendar_default: false, ideal_cycle_time_suspect: false,
    points: [
      { t: "2026-10-05T09:00:00Z", label: "06h", availability: 0.9, performance: 0.92, quality: 0.97, oee: 0.8 },
      { t: "2026-10-05T10:00:00Z", label: "07h", availability: 0.8, performance: 0.88, quality: 0.98, oee: 0.69 },
    ],
    ...over,
  };
}

export function paretoRows(): ParetoRow[] {
  return [
    { reason_code: "EQP-03", category: "EQP", label: "Falha pneumática/hidráulica", planned: false, duration_s: 1500, events: 1, pct: 0.3, cumulative_pct: 0.3 },
    { reason_code: "speed_loss", category: "speed_loss", label: "Perda de velocidade", planned: false, duration_s: 1800, events: 0, pct: 0.36, cumulative_pct: 0.66 },
    { reason_code: "SET-01", category: "SET", label: "Setup / troca de produto", planned: false, duration_s: 1200, events: 1, pct: 0.24, cumulative_pct: 0.9 },
    { reason_code: "micro_stops", category: "micro_stops", label: "Micro-paradas", planned: false, duration_s: 500, events: 12, pct: 0.1, cumulative_pct: 1 },
    { reason_code: "quality_loss", category: "quality_loss", label: "Qualidade (refugo)", planned: false, duration_s: 0, events: 0, pct: 0, cumulative_pct: 1 },
  ];
}

export function shift(over: Partial<Shift> = {}): Shift {
  return {
    id: "s-1", tenant_id: "t1", plant_id: "plant-1", name: "Turno A", start_local: "06:00", end_local: "14:00",
    weekdays: [0, 1, 2, 3, 4], breaks: [{ start_local: "10:00", end_local: "10:30", reason_code: "PLN-01" }],
    is_active: true, created_at: TS, updated_at: TS, ...over,
  };
}

/** Server-shaped overview payload (OverviewOut). */
export function overviewPayload() {
  return {
    plant_id: "plant-1", plant_name: "Planta Manaus", productive_day: "2026-10-05", generated_at: TS,
    lines: [
      {
        line_id: "line-1", line_name: "Linha 1 — Montagem", output_machine_id: "m-2", oee_today: 0.724, oee_current_shift: 0.81,
        current_shift_name: "Turno A", availability: 0.88, performance: 0.86, quality: 0.957, good: 1843, scrap: 37,
        cadence_per_hour: 96.4, data_coverage: 1, open_downtime_count: 0, unjustified_count: 2,
        worst_machine: { machine_id: "m-1", machine_name: "Prensa P-01", oee: 0.612 },
      },
      {
        line_id: "line-2", line_name: "Linha 2 — Envase", output_machine_id: "m-5", oee_today: 0.41, oee_current_shift: null,
        current_shift_name: null, availability: 0.5, performance: 0.9, quality: 0.91, good: 500, scrap: 50,
        cadence_per_hour: 40, data_coverage: 0.8, open_downtime_count: 1, unjustified_count: 0, worst_machine: null,
      },
    ],
    top_losses: paretoRows().slice(0, 3),
    machines_in_error: [{ machine_id: "m-3", machine_name: "Envasadora E-01", line_id: "line-2", last_seen_at: "2026-10-05T11:50:00Z" }],
    components_near_end: [
      { component_id: "c-2", position_id: "pos-2", position_name: "Motor principal", machine_id: "m-1", machine_name: "Prensa P-01", life_consumed_pct: 0.95, life_measure: "starts" },
    ],
  };
}
