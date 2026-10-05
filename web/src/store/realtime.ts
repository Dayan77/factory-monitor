import { create } from "zustand";
import type { SensorReading } from "@/services/types";

export type WsStatus = "connecting" | "connected" | "disconnected";

/** Last N values per tag for sparklines. */
export const SPARK_POINTS = 30;

interface RealtimeState {
  status: WsStatus;
  readings: Record<string, SensorReading>;
  history: Record<string, number[]>;
  /** Counter per position/machine bumped on every usage message (drives the "live" flash). */
  usageTick: Record<string, number>;
  setStatus: (s: WsStatus) => void;
  pushReading: (r: SensorReading) => void;
  bumpUsage: (key: string) => void;
  reset: () => void;
}

export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: "disconnected",
  readings: {},
  history: {},
  usageTick: {},
  setStatus: (status) => set({ status }),
  pushReading: (r) =>
    set((s) => ({
      readings: { ...s.readings, [r.tag_id]: r },
      history: { ...s.history, [r.tag_id]: [...(s.history[r.tag_id] ?? []), r.value].slice(-SPARK_POINTS) },
    })),
  bumpUsage: (key) => set((s) => ({ usageTick: { ...s.usageTick, [key]: (s.usageTick[key] ?? 0) + 1 } })),
  reset: () => set({ status: "disconnected", readings: {}, history: {}, usageTick: {} }),
}));
