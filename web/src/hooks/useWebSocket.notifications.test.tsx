import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useRealtimeConnection } from "./useWebSocket";
import NotificationBell from "@/components/notifications/NotificationCenter";
import { notificationsApi } from "@/services/maintenanceApi";
import type { AppNotification } from "@/services/maintenanceTypes";
import { useNotificationStore } from "@/store/notifications";
import { makeQueryClient, ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({ authApi: { me: vi.fn() } }));
vi.mock("@/services/maintenanceApi", () => ({
  notificationsApi: { list: vi.fn(), read: vi.fn(), readAll: vi.fn(), preferences: vi.fn(), savePreferences: vi.fn() },
  pushApi: { vapidKey: vi.fn(), subscribe: vi.fn() },
}));

class FakeSocket {
  static last: FakeSocket | null = null;
  static CONNECTING = 0;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string, public protocols: string[]) { FakeSocket.last = this; }
  close() {}
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

const N = (over: Partial<AppNotification>): AppNotification => ({ id: "n", type: "wo_assigned", title: "OS-114 atribuída", body: null, url: "/m/work-orders/wo-114", created_at: "2026-12-01T10:00:00Z", read_at: null, ...over });

describe("realtime notifications", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeSocket);
    localStorage.setItem("access_token", "jwt");
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("prepends to the bell list, raises emergency calls and refreshes maintenance queries", () => {
    const client = makeQueryClient();
    client.setQueryData(["notifications"], [N({ id: "old", read_at: "2026-12-01T09:00:00Z" })]);
    client.setQueryData(["maintenance", "work-orders", {}], []);
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    renderHook(() => useRealtimeConnection("t1"), { wrapper });
    const ws = FakeSocket.last!;
    act(() => ws.onopen?.());

    act(() => ws.emit({ type: "notification", notification: { id: "n1", kind: "wo_assigned", title: "OS-114 atribuída", link: "/m/work-orders/wo-114", data: { work_order_id: "wo-114" }, read: false } }));
    expect(client.getQueryData<AppNotification[]>(["notifications"])!.map((n) => n.id)).toEqual(["n1", "old"]);
    expect(client.getQueryState(["maintenance", "work-orders", {}])!.isInvalidated).toBe(true);
    expect(useNotificationStore.getState().emergencies).toHaveLength(0);

    act(() => ws.emit({ type: "notification", notification: { id: "n2", kind: "emergency_call", priority: "emergency", title: "Robô — falha 251", read: false } }));
    expect(useNotificationStore.getState().emergencies.map((e) => e.id)).toEqual(["n2"]);
    // duplicates (WS reconnect / push) are ignored
    act(() => ws.emit({ type: "notification", notification: { id: "n1", kind: "wo_assigned", title: "dup" } }));
    expect(client.getQueryData<AppNotification[]>(["notifications"])).toHaveLength(3);
  });
});

describe("notification bell", () => {
  it("shows the unread count, marks as read and opens the screen that answers it", async () => {
    const u = userEvent.setup();
    vi.mocked(notificationsApi.list).mockResolvedValue([N({ id: "a", work_order_id: "wo-114" }), N({ id: "b", title: "Plano vencido", type: "plan_due", url: "/maintenance/upcoming" }), N({ id: "c", read_at: "2026-12-01T11:00:00Z" })]);
    vi.mocked(notificationsApi.read).mockResolvedValue(ok({}));
    renderRoute(<NotificationBell />, "/maintenance/work-orders", "/maintenance/work-orders");
    const bell = await screen.findByRole("button", { name: "Notificações: 2 não lidas" });
    await u.click(bell);
    const dlg = screen.getByRole("dialog", { name: "Notificações" });
    const items = await within(dlg).findAllByTestId("notification-item");
    expect(items).toHaveLength(3);
    await u.click(items[1]);
    await waitFor(() => expect(notificationsApi.read).toHaveBeenCalledWith("b"));
    expect(screen.getByTestId("location")).toHaveTextContent("/maintenance/upcoming");
  });
});
