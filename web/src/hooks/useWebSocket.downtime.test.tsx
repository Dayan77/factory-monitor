import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useRealtimeConnection } from "./useWebSocket";
import { effKeys } from "./useEfficiency";
import type { DowntimeEvent } from "@/services/types";
import { downtime } from "@/test/fixtures";
import { makeQueryClient } from "@/test/utils";

vi.mock("@/services/api", () => ({ authApi: { me: vi.fn() } }));

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

describe("useRealtimeConnection — downtime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    localStorage.setItem("access_token", "jwt");
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("patches stop lists live and refreshes KPI views (throttled)", async () => {
    const client = makeQueryClient();
    const unjustQ = { unjustified: true };
    const allQ = { line_id: "line-1" };
    client.setQueryData(effKeys.downtime(unjustQ), [downtime({ id: "a" }), downtime({ id: "b" })]);
    client.setQueryData(effKeys.downtime(allQ), [downtime({ id: "a" })]);
    client.setQueryData(effKeys.overview("plant-1"), { lines: [] });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    renderHook(() => useRealtimeConnection("t1"), { wrapper });
    const ws = FakeSocket.last!;
    act(() => ws.onopen?.());

    // justified elsewhere (another tablet): leaves the unjustified list, updated in the full list
    act(() => ws.emit({ type: "downtime", action: "justify", event: { id: "a", machine_id: "m-1", start: "2026-10-05T11:30:00Z", reason_code: "EQP-03", planned: false } }));
    expect(client.getQueryData<DowntimeEvent[]>(effKeys.downtime(unjustQ))!.map((e) => e.id)).toEqual(["b"]);
    expect(client.getQueryData<DowntimeEvent[]>(effKeys.downtime(allQ))![0]).toMatchObject({ reason_code: "EQP-03", machine_name: "Prensa P-01" });

    // a brand-new stop: lists are re-fetched (the message has no line membership)
    act(() => ws.emit({ type: "downtime", action: "open", event: { id: "new", machine_id: "m-2", start: "2026-10-05T12:00:00Z", end: null } }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: effKeys.downtime(allQ), exact: true });

    // KPI refresh is throttled into one invalidation
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["efficiency"] });
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(invalidate.mock.calls.filter(([a]) => JSON.stringify(a) === JSON.stringify({ queryKey: ["efficiency"] }))).toHaveLength(1);
  });
});
