import { afterEach, describe, expect, it, vi } from "vitest";
import { acknowledge, broadcastRead, listenToOtherTabs, useNotificationStore } from "./notifications";
import type { AppNotification } from "@/services/maintenanceTypes";

const call = (id: string): AppNotification => ({ id, type: "emergency_call", severity: "emergency", title: `Chamado ${id}`,
  body: null, created_at: "2026-11-18T10:00:00Z", read_at: null });

afterEach(() => useNotificationStore.setState({ emergencies: [], seen: {} }));

describe("emergency call acknowledgement", () => {
  it("'Ciente' silences this tab, tells the other tabs and marks it read on the server", async () => {
    const other = new BroadcastChannel("fc-notifications");
    const got: unknown[] = [];
    other.onmessage = (e) => got.push(e.data);
    useNotificationStore.getState().raise(call("n1"));
    useNotificationStore.getState().raise(call("n2"));
    const markRead = vi.fn().mockResolvedValue({});
    acknowledge("n1", markRead);
    expect(useNotificationStore.getState().emergencies.map((e) => e.id)).toEqual(["n2"]);
    await vi.waitFor(() => expect(markRead).toHaveBeenCalledWith("n1"));
    await vi.waitFor(() => expect(got).toEqual([{ type: "read", ids: ["n1"] }]));
    other.close();
  });

  it("a server failure (offline) still silences this browser", async () => {
    useNotificationStore.getState().raise(call("n1"));
    acknowledge("n1", vi.fn().mockRejectedValue(new Error("offline")));
    expect(useNotificationStore.getState().emergencies).toEqual([]);
  });

  it("an acknowledgement from another tab stops the siren here and the call is not raised again", async () => {
    listenToOtherTabs();
    useNotificationStore.getState().raise(call("n1"));
    const sender = new BroadcastChannel("fc-notifications");
    sender.postMessage({ type: "read", ids: ["n1"] });
    await vi.waitFor(() => expect(useNotificationStore.getState().emergencies).toEqual([]));
    useNotificationStore.getState().raise(call("n1"));          // same call arriving late (push / WS replay)
    expect(useNotificationStore.getState().emergencies).toEqual([]);
    sender.close();
    broadcastRead([]);
  });

  it("resolve(all) clears the whole queue", () => {
    useNotificationStore.getState().raise(call("a"));
    useNotificationStore.getState().raise(call("b"));
    useNotificationStore.getState().resolve([], true);
    expect(useNotificationStore.getState().emergencies).toEqual([]);
  });
});
