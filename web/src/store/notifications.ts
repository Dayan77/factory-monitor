import { create } from "zustand";
import type { AppNotification } from "@/services/maintenanceTypes";

/** Emergency calls ("chamado emergencial") shown full screen with sound until acknowledged. */
export function isEmergency(n: Pick<AppNotification, "type" | "severity">): boolean {
  return n.severity === "emergency" || /emergenc/i.test(n.type);
}

interface NotificationState {
  /** Emergency notifications waiting for acknowledgement (newest last). */
  emergencies: AppNotification[];
  /** Ids already alerted or acknowledged (avoid re-alerting the same call from WS + push + other tabs). */
  seen: Record<string, true>;
  raise: (n: AppNotification) => void;
  /** Local only: removes the call from this tab's queue (see acknowledge for the full flow). */
  dismiss: (id: string) => void;
  /**
   * The calls were acknowledged elsewhere (another tab via BroadcastChannel, another browser/device via the
   * WebSocket `notifications_read`) or taken: stop the siren here too. `all` clears every queued call.
   */
  resolve: (ids: string[], all?: boolean) => void;
}

export const useNotificationStore = create<NotificationState>((set) => ({
  emergencies: [],
  seen: {},
  raise: (n) =>
    set((s) => (s.seen[n.id] ? s : { emergencies: [...s.emergencies, n], seen: { ...s.seen, [n.id]: true } })),
  dismiss: (id) => set((s) => ({ emergencies: s.emergencies.filter((e) => e.id !== id) })),
  resolve: (ids, all = false) =>
    set((s) => {
      const drop = new Set(ids);
      const seen = { ...s.seen };
      for (const id of ids) seen[id] = true;
      if (all) for (const e of s.emergencies) seen[e.id] = true;
      return { emergencies: all ? [] : s.emergencies.filter((e) => !drop.has(e.id)), seen };
    }),
}));

// ── same-browser tabs: acknowledging in one tab silences the others at once ─────────────────────────────

const CHANNEL = "fc-notifications";
type TabMessage = { type: "read"; ids: string[] };

function openChannel(): BroadcastChannel | null {
  try {
    return typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
}

let channel: BroadcastChannel | null = null;

/** Starts listening to the other tabs (idempotent; called by the app shell). */
export function listenToOtherTabs(): void {
  if (channel) return;
  channel = openChannel();
  if (!channel) return;
  channel.onmessage = (e: MessageEvent<TabMessage>) => {
    const m = e.data;
    if (m && m.type === "read" && Array.isArray(m.ids)) useNotificationStore.getState().resolve(m.ids);
  };
}

/** Tells the other tabs of this browser (different browsers/devices are told by the server over the WebSocket). */
export function broadcastRead(ids: string[]): void {
  listenToOtherTabs();
  try {
    channel?.postMessage({ type: "read", ids } satisfies TabMessage);
  } catch {
    /* tab channel unavailable: the server message still reaches the other tabs */
  }
}

/**
 * "Ciente" / "Atender": removes the call here, silences the other tabs of this browser and marks it read on the
 * server, which tells the user's other browsers and devices. `markRead` failing (offline) still silences
 * this browser; the call stays unread on the server and shows again on the other devices until they ack it.
 */
export function acknowledge(id: string, markRead: (id: string) => Promise<unknown>): void {
  useNotificationStore.getState().resolve([id]);
  broadcastRead([id]);
  void Promise.resolve().then(() => markRead(id)).catch(() => undefined);
}
