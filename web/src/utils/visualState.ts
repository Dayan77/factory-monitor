/**
 * Visual state of a machine on the plant map / operator tablet (docs/sprint6.md §1.2, block `s6-status-precedence`).
 * The server computes it (services/layout/state.py); the client uses the same rule to update a marker the moment a
 * `machine_status` message arrives (before the authoritative refetch lands), and to label every state with
 * colour + icon + short text (never colour only).
 */
import type { MachineStatus } from "@/services/types";
import type { MachineVisual, VisualState } from "@/services/sprint6Types";

/** Precedence order: the first true rule wins. */
export const STATE_ORDER: readonly VisualState[] = [
  "offline", "failure", "maintenance", "planned_stop", "off_shift", "stopped", "running",
];

export const MACHINE_STATUS_MAP: Record<MachineStatus, VisualState> = {
  unknown: "offline", error: "failure", maintenance: "maintenance", stopped: "stopped", running: "running",
};

/** Facts of §1.2 (same keys as the spec block and the server). */
export interface StateFacts {
  status?: MachineStatus | string | null;
  /** No reading for longer than machine_offline_timeout_s. */
  stale?: boolean;
  open_failure?: boolean;
  wo_in_progress_stops_machine?: boolean;
  in_break?: boolean;
  planned_stop?: boolean;
  open_pln?: boolean;
  off_shift?: boolean;
  open_downtime?: boolean;
}

export function visualState(f: StateFacts): VisualState {
  const st = f.status;
  if (st === "unknown" || f.stale) return "offline";
  if (f.open_failure || st === "error") return "failure";
  if (st === "maintenance" || f.wo_in_progress_stops_machine) return "maintenance";
  if (f.in_break || f.planned_stop || f.open_pln) return "planned_stop";
  if (f.off_shift) return "off_shift";
  if (f.open_downtime || st === "stopped") return "stopped";
  if (st === "running") return "running";
  return "offline";
}

/**
 * Facts implied by the server's marker that do not come from `Machine.status` (open failure, WO in progress, break,
 * off shift, open stop). Used to re-evaluate the precedence when only the status changes.
 */
export function factsFromMarker(m: Pick<MachineVisual, "state" | "status" | "detail">): StateFacts {
  const f: StateFacts = { status: m.status ?? undefined };
  switch (m.state) {
    case "failure":
      if (m.status !== "error") f.open_failure = true;
      break;
    case "maintenance":
      if (m.status !== "maintenance") f.wo_in_progress_stops_machine = true;
      break;
    case "planned_stop":
      f.in_break = true;
      break;
    case "off_shift":
      f.off_shift = true;
      break;
    case "stopped":
      if (m.detail?.downtime_id) f.open_downtime = true;
      break;
    default:
      break;
  }
  return f;
}

/**
 * The marker after a `machine_status` message (optimistic; the refetch that follows is authoritative).
 * Returns the same object when nothing visible changes.
 */
export function applyStatusToMarker(m: MachineVisual, status: MachineStatus, lastSeenAt: string | null, now: string): MachineVisual {
  const facts = { ...factsFromMarker(m), status, stale: false };
  const state = visualState(facts);
  if (state === m.state && status === m.status) return m;
  return {
    ...m,
    status,
    state,
    icon: STATE_META[state].icon,
    text: state === m.state ? m.text : STATE_META[state].label,
    since: state === m.state ? m.since : now,
    last_seen_at: lastSeenAt ?? m.last_seen_at,
  };
}

export type StateTone = "neutral" | "danger" | "info" | "warn" | "ok" | "muted" | "dark";

export interface StateMeta {
  label: string;
  icon: string;
  tone: StateTone;
  /** Tailwind classes of the marker (fill / border / text) — AA in both themes (semantic tokens). */
  marker: string;
  dashed?: boolean;
}

export const STATE_META: Record<VisualState, StateMeta> = {
  // Fixed colours (same in both themes, so a marker reads the same on the TV and on a laptop); every pair is ≥ 4.5:1.
  offline: { label: "sem dados", icon: "cloud-off", tone: "neutral", dashed: true,
    marker: "border-dashed border-fg-subtle bg-surface text-fg-muted" },
  failure: { label: "falha", icon: "wrench", tone: "danger", marker: "border-[#7f1d1d] bg-[#b91c1c] text-white" },
  maintenance: { label: "manutenção", icon: "hard-hat", tone: "info", marker: "border-[#1e3a8a] bg-[#1d4ed8] text-white" },
  planned_stop: { label: "pausa", icon: "clock", tone: "muted", marker: "border-[#9ca3af] bg-[#d1d5db] text-[#111827]" },
  off_shift: { label: "fora do turno", icon: "moon", tone: "dark", marker: "border-[#1f2937] bg-[#374151] text-white" },
  stopped: { label: "parada", icon: "pause", tone: "warn", marker: "border-[#b45309] bg-[#f59e0b] text-[#111827]" },
  running: { label: "rodando", icon: "play", tone: "ok", marker: "border-[#14532d] bg-[#15803d] text-white" },
};

/** "há 12 min" style duration from `since` to `now` (ms). */
export function formatSince(since: string | null | undefined, now: number): string | null {
  if (!since) return null;
  const t = Date.parse(since);
  if (!Number.isFinite(t)) return null;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `há ${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `há ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `há ${h} h ${String(m % 60).padStart(2, "0")} min`;
  return `há ${Math.floor(h / 24)} d`;
}

/** Text of the marker with its duration, e.g. "parada — sem motivo · há 6 min" (offline: "sem dados há 12 min"). */
export function markerText(m: Pick<MachineVisual, "state" | "text" | "since" | "last_seen_at">, now: number): string {
  if (m.state === "offline") {
    const ago = formatSince(m.last_seen_at ?? m.since, now);
    return ago ? `sem dados ${ago}` : "sem dados";
  }
  if (m.state === "off_shift" || m.state === "running") return m.text;
  const ago = formatSince(m.since, now);
  return ago ? `${m.text} · ${ago}` : m.text;
}

/** Most important problem first (map side list, operator tablet). */
export function bySeverity<T extends { state: VisualState; name: string }>(a: T, b: T): number {
  const rank: Record<VisualState, number> = {
    failure: 0, stopped: 1, maintenance: 2, offline: 3, planned_stop: 4, off_shift: 5, running: 6,
  };
  return rank[a.state] - rank[b.state] || a.name.localeCompare(b.name, "pt-BR");
}
