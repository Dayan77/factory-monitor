import { create } from "zustand";
import type { SensorReading } from "@/services/types";

export type WsStatus = "connecting" | "connected" | "disconnected";

/** Last N values per tag for sparklines. */
export const SPARK_POINTS = 30;

interface RealtimeState {
  status: WsStatus;
  /** When the socket stopped being connected (ms epoch); null while connected or before the first attempt. */
  downSince: number | null;
  /** Last message received (ms epoch) — "última atualização HH:MM:SS" of the paused banner. */
  lastMessageAt: number | null;
  readings: Record<string, SensorReading>;
  history: Record<string, number[]>;
  /** Counter per position/machine bumped on every usage message (drives the "live" flash). */
  usageTick: Record<string, number>;
  setStatus: (s: WsStatus) => void;
  touch: () => void;
  pushReading: (r: SensorReading) => void;
  bumpUsage: (key: string) => void;
  reset: () => void;
}

export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: "disconnected",
  downSince: null,
  lastMessageAt: null,
  readings: {},
  history: {},
  usageTick: {},
  setStatus: (status) =>
    set((s) => ({
      status,
      downSince: status === "connected" ? null : s.downSince ?? Date.now(),
      lastMessageAt: status === "connected" ? s.lastMessageAt ?? Date.now() : s.lastMessageAt,
    })),
  touch: () => set({ lastMessageAt: Date.now() }),
  pushReading: (r) =>
    set((s) => ({
      readings: { ...s.readings, [r.tag_id]: r },
      history: { ...s.history, [r.tag_id]: [...(s.history[r.tag_id] ?? []), r.value].slice(-SPARK_POINTS) },
    })),
  bumpUsage: (key) => set((s) => ({ usageTick: { ...s.usageTick, [key]: (s.usageTick[key] ?? 0) + 1 } })),
  reset: () => set({ status: "disconnected", downSince: null, lastMessageAt: null, readings: {}, history: {}, usageTick: {} }),
}));
