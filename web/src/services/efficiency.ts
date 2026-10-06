/**
 * Normalization of the "raio-x" overview payload.
 *
 * The sprint contract and the server schema (OverviewOut) name a few fields differently
 * (`name` vs `line_name`, `oee` vs `oee_today`, `cadence` vs `cadence_per_hour`,
 * `components_near_eol` vs `components_near_end`, worst machine `id/name` vs `machine_id/machine_name`,
 * machine in error `since` vs `last_seen_at`). Both spellings are accepted so the UI never breaks on
 * either; a missing value stays null (never invented).
 */

import type {
  Overview, OverviewComponentNearEol, OverviewGateway, OverviewLine, OverviewMachineInError, OverviewWorstMachine, ParetoRow,
} from "./types";

type Rec = Record<string, unknown>;

const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function num(...vs: unknown[]): number | null {
  for (const v of vs) if (typeof v === "number" && Number.isFinite(v)) return v;
  return null;
}

function str(...vs: unknown[]): string | null {
  for (const v of vs) if (typeof v === "string" && v) return v;
  return null;
}

function worst(v: unknown): OverviewWorstMachine | null {
  if (!isRec(v)) return null;
  const id = str(v.id, v.machine_id);
  if (!id) return null;
  return { id, name: str(v.name, v.machine_name) ?? "—", oee: num(v.oee) };
}

function line(v: unknown): OverviewLine | null {
  if (!isRec(v)) return null;
  const line_id = str(v.line_id, v.id);
  if (!line_id) return null;
  return {
    line_id,
    name: str(v.name, v.line_name) ?? "—",
    output_machine_id: str(v.output_machine_id),
    oee: num(v.oee, v.oee_today),
    oee_current_shift: num(v.oee_current_shift),
    current_shift_name: str(v.current_shift_name),
    availability: num(v.availability),
    performance: num(v.performance),
    quality: num(v.quality),
    good: num(v.good) ?? 0,
    scrap: num(v.scrap) ?? 0,
    cadence: num(v.cadence, v.cadence_per_hour),
    data_coverage: num(v.data_coverage),
    open_downtime_count: num(v.open_downtime_count) ?? 0,
    unjustified_count: num(v.unjustified_count) ?? 0,
    worst_machine: worst(v.worst_machine),
  };
}

function machineInError(v: unknown): OverviewMachineInError | null {
  if (!isRec(v)) return null;
  const id = str(v.id, v.machine_id);
  if (!id) return null;
  return {
    id,
    name: str(v.name, v.machine_name) ?? "—",
    line_id: str(v.line_id),
    since: str(v.since, v.fault_since, v.last_seen_at),
    fault_code: num(v.fault_code),
  };
}

function nearEol(v: unknown): OverviewComponentNearEol | null {
  if (!isRec(v)) return null;
  const position_id = str(v.position_id);
  const life = num(v.life_consumed_pct);
  if (!position_id || life == null) return null;
  const measure = str(v.life_measure);
  return {
    component_id: str(v.component_id),
    position_id,
    position_name: str(v.position_name) ?? "—",
    machine_id: str(v.machine_id),
    machine_name: str(v.machine_name),
    life_consumed_pct: life,
    life_measure: measure === "cycles" || measure === "hours" || measure === "starts" ? measure : null,
    projected_end_date: str(v.projected_end_date),
  };
}

function pareto(v: unknown): ParetoRow | null {
  if (!isRec(v)) return null;
  const duration_s = num(v.duration_s);
  if (duration_s == null) return null;
  return {
    reason_code: str(v.reason_code),
    category: str(v.category) ?? "",
    label: str(v.label) ?? str(v.reason_code) ?? "—",
    planned: v.planned === true,
    duration_s,
    events: num(v.events) ?? 0,
    pct: num(v.pct),
    cumulative_pct: num(v.cumulative_pct),
  };
}

const notNull = <T,>(v: T | null): v is T => v !== null;

export function normalizeOverview(raw: unknown): Overview {
  const r = isRec(raw) ? raw : {};
  return {
    plant_id: str(r.plant_id) ?? undefined,
    plant_name: str(r.plant_name) ?? undefined,
    productive_day: str(r.productive_day) ?? undefined,
    generated_at: str(r.generated_at) ?? undefined,
    lines: arr(r.lines).map(line).filter(notNull),
    top_losses: arr(r.top_losses).map(pareto).filter(notNull),
    machines_in_error: arr(r.machines_in_error).map(machineInError).filter(notNull),
    components_near_eol: arr(r.components_near_eol ?? r.components_near_end).map(nearEol).filter(notNull)
      .sort((a, b) => b.life_consumed_pct - a.life_consumed_pct),
    ...(Array.isArray(r.gateways) ? { gateways: arr(r.gateways).map(gateway).filter(notNull) } : {}),
  };
}

const GW_STATUSES = ["pending", "online", "degraded", "offline"] as const;

function gateway(v: unknown): OverviewGateway | null {
  if (!isRec(v) || typeof v.id !== "string") return null;
  const st = (GW_STATUSES as readonly string[]).includes(String(v.status)) ? (v.status as OverviewGateway["status"]) : "offline";
  return {
    id: v.id, name: str(v.name) ?? "Gateway", status: st, version: str(v.version),
    last_seen_at: str(v.last_seen_at), config_version: typeof v.config_version === "number" ? v.config_version : null,
  };
}
