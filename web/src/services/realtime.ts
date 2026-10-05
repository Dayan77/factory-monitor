/**
 * Parsing and cache-merging for messages on /ws/sensors/{tenant_id}.
 *
 * Server messages:
 *   {type: "sensor_reading", tag_id, value, unit, timestamp}
 *   {type: "machine_status", machine_id, status, last_seen_at, ...}
 *   {type: "usage", position_id|null, machine_id|null, measure, delta, total, timestamp}
 * Legacy sensor messages without `type` are accepted as readings.
 */

import type {
  Machine, MachineStatus, MachineUsage, PositionDetail, RealtimeMessage, StatusSummary, TreePlant,
  UsageMeasure, UsageMessage,
} from "./types";

const STATUSES: readonly MachineStatus[] = ["running", "stopped", "maintenance", "error", "unknown"];
const MEASURES: readonly UsageMeasure[] = ["cycles", "hours", "starts", "good", "scrap", "total"];

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function isStatus(v: unknown): v is MachineStatus {
  return typeof v === "string" && (STATUSES as readonly string[]).includes(v);
}

function optString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

export function parseRealtimeMessage(raw: string): RealtimeMessage | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data)) return null;

  if (data.type === "machine_status") {
    if (typeof data.machine_id !== "string" || !isStatus(data.status)) return null;
    return {
      type: "machine_status",
      machine_id: data.machine_id,
      machine_name: typeof data.machine_name === "string" ? data.machine_name : undefined,
      status: data.status,
      previous_status: isStatus(data.previous_status) ? data.previous_status : undefined,
      last_seen_at: optString(data.last_seen_at),
      timestamp: typeof data.timestamp === "string" ? data.timestamp : undefined,
    };
  }

  if (data.type === "usage") {
    const position_id = optString(data.position_id);
    const machine_id = optString(data.machine_id);
    if (!position_id && !machine_id) return null;
    if (typeof data.measure !== "string" || !(MEASURES as readonly string[]).includes(data.measure)) return null;
    if (!isFiniteNumber(data.delta) || !isFiniteNumber(data.total)) return null;
    return {
      type: "usage",
      position_id,
      machine_id,
      measure: data.measure as UsageMeasure,
      delta: data.delta,
      total: data.total,
      timestamp: typeof data.timestamp === "string" ? data.timestamp : undefined,
    };
  }

  if (data.type === undefined || data.type === "sensor_reading") {
    if (typeof data.tag_id !== "string" || typeof data.value !== "number") return null;
    return {
      type: "sensor_reading",
      tag_id: data.tag_id,
      value: data.value,
      unit: optString(data.unit),
      timestamp: typeof data.timestamp === "string" ? data.timestamp : new Date().toISOString(),
    };
  }

  return null;
}

/** Applies a machine_status message to a cached machine list (returns the same array if unaffected). */
export function applyMachineStatus(
  machines: Machine[] | undefined,
  msg: { machine_id: string; status: MachineStatus; last_seen_at?: string | null },
): Machine[] | undefined {
  if (!machines) return machines;
  let changed = false;
  const next = machines.map((m) => {
    if (m.id !== msg.machine_id) return m;
    changed = true;
    return {
      ...m,
      status: msg.status,
      // keep the previous value when the message doesn't carry one
      last_seen_at: msg.last_seen_at !== undefined && msg.last_seen_at !== null ? msg.last_seen_at : m.last_seen_at,
    };
  });
  return changed ? next : machines;
}

/** Recounts a line's status summary from its machines. */
export function summarize(statuses: MachineStatus[]): StatusSummary {
  const out: StatusSummary = { running: 0, stopped: 0, error: 0, unknown: 0, maintenance: 0 };
  for (const st of statuses) out[st] = (out[st] ?? 0) + 1;
  return out;
}

/** Applies a machine_status message to the cached plants tree (same reference if unaffected). */
export function applyMachineStatusToTree(
  tree: TreePlant[] | undefined,
  msg: { machine_id: string; status: MachineStatus; last_seen_at?: string | null },
): TreePlant[] | undefined {
  if (!tree) return tree;
  let changed = false;
  const next = tree.map((plant) => {
    let plantChanged = false;
    const lines = plant.lines.map((line) => {
      if (!line.machines.some((m) => m.id === msg.machine_id)) return line;
      plantChanged = true;
      const machines = line.machines.map((m) =>
        m.id !== msg.machine_id
          ? m
          : { ...m, status: msg.status, last_seen_at: msg.last_seen_at ?? m.last_seen_at },
      );
      return { ...line, machines, status_summary: summarize(machines.map((m) => m.status)) };
    });
    if (!plantChanged) return plant;
    changed = true;
    return { ...plant, lines };
  });
  return changed ? next : tree;
}

const POSITION_TOTAL_KEY = { cycles: "cycles_total", hours: "hours_total", starts: "starts_total" } as const;
type PositionMeasure = keyof typeof POSITION_TOTAL_KEY;

function isPositionMeasure(m: UsageMeasure): m is PositionMeasure {
  return m === "cycles" || m === "hours" || m === "starts";
}

/**
 * Applies a usage message to a machine's cached usage:
 *  - position measure → that position's *_total = msg.total (accumulator value, idempotent)
 *  - machine production (position_id null, total/scrap) → today's counts += delta
 */
export function applyUsageToMachineUsage(
  usage: MachineUsage | undefined,
  machineId: string,
  msg: UsageMessage,
): MachineUsage | undefined {
  if (!usage) return usage;
  if (msg.position_id) {
    if (!isPositionMeasure(msg.measure)) return usage;
    const key = POSITION_TOTAL_KEY[msg.measure];
    let changed = false;
    const positions = usage.positions.map((p) => {
      if (p.position_id !== msg.position_id) return p;
      changed = true;
      return { ...p, [key]: msg.total };
    });
    return changed ? { ...usage, positions } : usage;
  }
  if (msg.machine_id !== machineId) return usage;
  // The server always emits `total` for every part (good+scrap or the total counter) and `scrap`
  // when there is a scrap counter, so today's good = total − scrap. `good` messages are ignored to
  // avoid double counting; the periodic refetch reconciles with the server's exact numbers.
  if (msg.measure === "total" || msg.measure === "scrap") {
    const p = usage.production;
    const total = msg.measure === "total" ? p.total + msg.delta : p.total;
    const scrap = msg.measure === "scrap" ? p.scrap + msg.delta : p.scrap;
    return { ...usage, production: { ...p, total, scrap, good: Math.max(0, total - scrap) } };
  }
  if (msg.measure === "good") return usage;
  if (msg.measure === "hours") return { ...usage, hours_total: msg.total };
  if (msg.measure === "starts") return { ...usage, starts_total: msg.total };
  return usage;
}

/** Applies a usage message to a cached position detail (totals, component usage and life). */
export function applyUsageToPosition(detail: PositionDetail | undefined, msg: UsageMessage): PositionDetail | undefined {
  if (!detail || msg.position_id !== detail.id || !isPositionMeasure(msg.measure)) return detail;
  const m = msg.measure;
  const usage = { ...detail.usage, [POSITION_TOTAL_KEY[m]]: msg.total };
  let component_usage = detail.component_usage;
  if (component_usage && detail.installed_component) {
    const cu = { ...component_usage, [m]: component_usage[m] + msg.delta };
    const nominal = detail.installed_component.nominal_life;
    let best: { v: number; measure: PositionMeasure } | null = null;
    for (const k of ["cycles", "hours", "starts"] as const) {
      const life = nominal[k];
      if (!life) continue;
      const v = cu[k] / life;
      if (!best || v > best.v) best = { v, measure: k };
    }
    if (best) {
      cu.life_consumed_pct = best.v;
      cu.life_measure = best.measure;
    }
    component_usage = cu;
  }
  return { ...detail, usage, component_usage };
}

// ── Connection / auth ─────────────────────────────────────────────

/** Close codes sent by the server on /ws/sensors/{tenant_id}. */
export const WS_CLOSE_UNAUTHORIZED = 4401; // missing / invalid / expired token
export const WS_CLOSE_FORBIDDEN = 4403;    // token of another tenant

export const WS_BEARER_PROTOCOL = "bearer";

/**
 * URL + subprotocols for the tenant's realtime socket. The access JWT goes in
 * the `Sec-WebSocket-Protocol` header (["bearer", token]) instead of the URL,
 * so it never lands in proxy access logs.
 */
export function realtimeSocketParams(
  tenantId: string,
  token: string,
  loc: { protocol: string; host: string } = location,
): { url: string; protocols: string[] } {
  const scheme = loc.protocol === "https:" ? "wss" : "ws";
  return {
    url: `${scheme}://${loc.host}/ws/sensors/${encodeURIComponent(tenantId)}`,
    protocols: [WS_BEARER_PROTOCOL, token],
  };
}

export type ReconnectAction = "retry" | "refresh-then-retry" | "stop";

/** What to do after the socket closed with `code`. */
export function reconnectActionFor(code: number): ReconnectAction {
  if (code === WS_CLOSE_UNAUTHORIZED) return "refresh-then-retry";
  if (code === WS_CLOSE_FORBIDDEN) return "stop";
  return "retry";
}
