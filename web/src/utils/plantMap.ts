/**
 * Pure helpers of the plant map (docs/sprint6.md §1): live merge of status messages, freshness of the live view
 * (§1.3), and the layout editor model (normalized coordinates, 1 % grid, undo/redo, duplicates).
 */
import type { MachineStatusMessage } from "@/services/types";
import type { LayoutMarker, MachineKpis, MachineVisual, MapView, MarkerSize } from "@/services/sprint6Types";
import { applyStatusToMarker } from "./visualState";

// ── live merge ────────────────────────────────────────────────────

/** Applies a machine_status message to a cached map view (same reference when unaffected). */
export function applyStatusToMapView(view: MapView | undefined, msg: MachineStatusMessage): MapView | undefined {
  if (!view) return view;
  let changed = false;
  const now = msg.timestamp ?? new Date().toISOString();
  const machines = view.machines.map((m) => {
    if (m.machine_id !== msg.machine_id) return m;
    const next = applyStatusToMarker(m, msg.status, msg.last_seen_at ?? null, now);
    if (next !== m) changed = true;
    return next;
  });
  if (!changed) return view;
  const byId = new Map(machines.map((m) => [m.machine_id, m]));
  const lines = view.lines.map((l) => {
    const om = l.output_machine_id ? byId.get(l.output_machine_id) : undefined;
    return om && om.state !== l.output_state ? { ...l, output_state: om.state, output_text: om.text } : l;
  });
  return { ...view, machines, lines };
}

/**
 * Live state (fetched often, `kpis=false`) + current-shift KPIs (fetched every minute). The KPIs of a machine never
 * come from another scope: missing → null ("—").
 */
export function mergeKpis(live: MapView, kpis: MapView | undefined): MapView {
  if (!kpis) return live;
  const km = new Map<string, MachineKpis | null>(kpis.machines.map((m) => [m.machine_id, m.kpis]));
  const kl = new Map<string, MachineKpis | null>(kpis.lines.map((l) => [l.line_id, l.kpis]));
  return {
    ...live,
    shift: live.shift ?? kpis.shift,
    machines: live.machines.map((m) => ({ ...m, kpis: m.kpis ?? km.get(m.machine_id) ?? null })),
    lines: live.lines.map((l) => ({ ...l, kpis: l.kpis ?? kl.get(l.line_id) ?? null })),
  };
}

export const POLL_AFTER_MS = 10_000;
/** 29 s + the 1 s clock tick: the banner is always up within 30 s of the drop (sprint6 §6 item 1). */
export const PAUSED_AFTER_MS = 29_000;
export const POLL_INTERVAL_MS = 15_000;

export type Liveness = "live" | "reconnecting" | "polling" | "paused";

/**
 * §1.3: socket down > 10 s → polling every 15 s; down > 30 s → "ao vivo pausado" banner and dimmed markers.
 * `downSince` null while connected.
 */
export function liveness(connected: boolean, downSince: number | null, now: number): Liveness {
  if (connected) return "live";
  const down = now - (downSince ?? now);
  if (down > PAUSED_AFTER_MS) return "paused";
  if (down > POLL_AFTER_MS) return "polling";
  return "reconnecting";
}

/** "HH:MM:SS" in the plant timezone. */
export function clockTime(ms: number, tz: string): string {
  return new Intl.DateTimeFormat("pt-BR", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })
    .format(new Date(ms));
}

/** Server clock offset (ms to add to Date.now()) from a response's `generated_at`. */
export function serverOffset(generatedAt: string | null | undefined, receivedAt: number): number {
  if (!generatedAt) return 0;
  const t = Date.parse(generatedAt);
  return Number.isFinite(t) ? t - receivedAt : 0;
}

// ── editor model ──────────────────────────────────────────────────

export const GRID = 0.01;

export function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

/** Snap to the 1 % grid (and clamp to [0, 1]); 4 decimals to avoid float noise. */
export function snap(v: number, grid = GRID, enabled = true): number {
  const c = clamp01(v);
  const s = enabled ? Math.round(c / grid) * grid : c;
  return Math.round(clamp01(s) * 10_000) / 10_000;
}

export function normRotation(deg: number): number {
  const r = ((Math.round(deg) % 360) + 360) % 360;
  return r;
}

export const SIZES: MarkerSize[] = ["s", "m", "l"];
/** Marker box size in px at 100 % zoom (the map scales them with its own width). */
export const SIZE_PX: Record<MarkerSize, { w: number; h: number }> = {
  s: { w: 92, h: 40 }, m: { w: 148, h: 64 }, l: { w: 196, h: 84 },
};

export function markerKey(m: Pick<LayoutMarker, "kind" | "machine_id" | "line_id" | "label" | "id">, i: number): string {
  if (m.kind === "machine" && m.machine_id) return `m:${m.machine_id}`;
  if (m.kind === "line_label" && m.line_id) return `l:${m.line_id}`;
  return m.id ? `a:${m.id}` : `a:${i}:${m.label ?? ""}`;
}

/** Machines placed more than once (409 on the server) — the editor never lets it happen, but checks before saving. */
export function duplicateMachines(markers: LayoutMarker[]): string[] {
  const seen = new Set<string>();
  const dup = new Set<string>();
  for (const m of markers) {
    if (m.kind !== "machine" || !m.machine_id) continue;
    if (seen.has(m.machine_id)) dup.add(m.machine_id);
    seen.add(m.machine_id);
  }
  return [...dup];
}

/** Machines of the plant without a marker: the "não posicionadas" tray (never disappear). */
export function unplaced(machineIds: string[], markers: LayoutMarker[]): string[] {
  const placed = new Set(markers.filter((m) => m.kind === "machine").map((m) => m.machine_id));
  return machineIds.filter((id) => !placed.has(id));
}

/** Everything the server validates (422) before a save: coordinates in [0, 1], rotation in [0, 360). */
export function markerErrors(markers: LayoutMarker[]): string[] {
  const out: string[] = [];
  markers.forEach((m, i) => {
    if (!(m.x >= 0 && m.x <= 1 && m.y >= 0 && m.y <= 1)) out.push(`marcador ${i + 1}: posição fora da planta`);
    if (!(m.rotation_deg >= 0 && m.rotation_deg < 360)) out.push(`marcador ${i + 1}: rotação inválida`);
    if (m.kind === "area_label" && !(m.label ?? "").trim()) out.push(`marcador ${i + 1}: rótulo de área sem texto`);
  });
  const dup = duplicateMachines(markers);
  if (dup.length) out.push("máquina posicionada mais de uma vez");
  return out;
}

/** Aligns the selected markers on one axis (their mean), snapped. */
export function align(markers: LayoutMarker[], keys: Set<string>, axis: "x" | "y"): LayoutMarker[] {
  const sel = markers.filter((m, i) => keys.has(markerKey(m, i)));
  if (sel.length < 2) return markers;
  const v = snap(sel.reduce((s, m) => s + m[axis], 0) / sel.length);
  return markers.map((m, i) => (keys.has(markerKey(m, i)) ? { ...m, [axis]: v } : m));
}

// undo / redo

export interface History<T> {
  past: T[];
  present: T;
  future: T[];
}

export const HISTORY_LIMIT = 100;

export function historyInit<T>(present: T): History<T> {
  return { past: [], present, future: [] };
}

export function historyPush<T>(h: History<T>, next: T): History<T> {
  if (next === h.present) return h;
  return { past: [...h.past, h.present].slice(-HISTORY_LIMIT), present: next, future: [] };
}

export function historyUndo<T>(h: History<T>): History<T> {
  if (h.past.length === 0) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function historyRedo<T>(h: History<T>): History<T> {
  if (h.future.length === 0) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

/** Pointer position (client px) → normalized coordinates of the map box. */
export function toNormalized(clientX: number, clientY: number, rect: { left: number; top: number; width: number; height: number }) {
  return {
    x: clamp01((clientX - rect.left) / Math.max(1, rect.width)),
    y: clamp01((clientY - rect.top) / Math.max(1, rect.height)),
  };
}

/** Machines of the map view by id (marker lookup). */
export function machineIndex(view: Pick<MapView, "machines">): Map<string, MachineVisual> {
  return new Map(view.machines.map((m) => [m.machine_id, m]));
}

/** Aspect ratio of the map box: the image's, or 16:9 for the automatic layout (grows with the number of lines). */
export function mapAspect(width: number | null | undefined, height: number | null | undefined, lanes = 0): number {
  if (width && height && width > 0 && height > 0) return width / height;
  return lanes > 3 ? 16 / (2.4 * lanes) : 16 / 9;
}
