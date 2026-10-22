import { create } from "zustand";
import type { AppNotification } from "@/services/maintenanceTypes";

/** Emergency calls ("chamado emergencial") shown full screen with sound until acknowledged. */
export function isEmergency(n: Pick<AppNotification, "type" | "severity">): boolean {
  return n.severity === "emergency" || /emergenc/i.test(n.type);
}

interface NotificationState {
  /** Emergency notifications waiting for acknowledgement (newest last). */
  emergencies: AppNotification[];
  /** Ids already alerted (avoid re-alerting the same call from WS + push). */
  seen: Record<string, true>;
  raise: (n: AppNotification) => void;
  dismiss: (id: string) => void;
}

export const useNotificationStore = create<NotificationState>((set) => ({
  emergencies: [],
  seen: {},
  raise: (n) =>
    set((s) => (s.seen[n.id] ? s : { emergencies: [...s.emergencies, n], seen: { ...s.seen, [n.id]: true } })),
  dismiss: (id) => set((s) => ({ emergencies: s.emergencies.filter((e) => e.id !== id) })),
}));
