/**
 * Parsing and cache-merging for messages on /ws/sensors/{tenant_id}.
 *
 * Server messages:
 *   {type: "sensor_reading", tag_id, value, unit, timestamp}
 *   {type: "machine_status", machine_id, status, last_seen_at, ...}
 * Legacy sensor messages without `type` are accepted as readings.
 */

import type { Machine, MachineStatus, RealtimeMessage } from "./types";

const STATUSES: readonly MachineStatus[] = ["running", "stopped", "maintenance", "error", "unknown"];

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
