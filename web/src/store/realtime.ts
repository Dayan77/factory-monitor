import { create } from "zustand";
import type { SensorReading } from "@/services/types";

export type WsStatus = "connecting" | "connected" | "disconnected";

/** Last N values per tag for sparklines. */
export const SPARK_POINTS = 30;

interface RealtimeState {
  status: WsStatus;
  readings: Record<string, SensorReading>;
  history: Record<string, number[]>;
  setStatus: (s: WsStatus) => void;
  pushReading: (r: SensorReading) => void;
  reset: () => void;
}

export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: "disconnected",
  readings: {},
  history: {},
  setStatus: (status) => set({ status }),
  pushReading: (r) =>
    set((s) => ({
      readings: { ...s.readings, [r.tag_id]: r },
      history: { ...s.history, [r.tag_id]: [...(s.history[r.tag_id] ?? []), r.value].slice(-SPARK_POINTS) },
    })),
  reset: () => set({ status: "disconnected", readings: {}, history: {} }),
}));
