import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useRealtimeConnection } from "./useWebSocket";
import { queryKeys } from "./useAssets";
import type { MachineUsage, PositionDetail, TreePlant } from "@/services/types";
import { useRealtimeStore } from "@/store/realtime";
import { machineUsage, plantTree, positionDetail } from "@/test/fixtures";
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

describe("useRealtimeConnection — assets", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeSocket);
    localStorage.setItem("access_token", "jwt");
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("patches tree status, machine usage and position detail from realtime messages", () => {
    const client = makeQueryClient();
    client.setQueryData(queryKeys.tree, plantTree());
    client.setQueryData(queryKeys.machineUsage("m-1"), machineUsage());
    client.setQueryData(queryKeys.position("pos-1"), positionDetail());
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    renderHook(() => useRealtimeConnection("t1"), { wrapper });
    const ws = FakeSocket.last!;
    act(() => ws.onopen?.());

    act(() => ws.emit({ type: "machine_status", machine_id: "m-2", status: "error" }));
    const tree = client.getQueryData<TreePlant[]>(queryKeys.tree)!;
    expect(tree[0].lines[0].status_summary).toMatchObject({ running: 1, error: 1 });

    act(() => ws.emit({ type: "usage", position_id: "pos-1", machine_id: "m-1", measure: "cycles", delta: 5, total: 3_600_005 }));
    expect(client.getQueryData<MachineUsage>(queryKeys.machineUsage("m-1"))!.positions[0].cycles_total).toBe(3_600_005);
    expect(client.getQueryData<PositionDetail>(queryKeys.position("pos-1"))!.component_usage!.cycles).toBe(3_600_005);
    expect(useRealtimeStore.getState().usageTick["pos-1"]).toBe(1);

    act(() => ws.emit({ type: "usage", position_id: null, machine_id: "m-1", measure: "total", delta: 3, total: 50 }));
    expect(client.getQueryData<MachineUsage>(queryKeys.machineUsage("m-1"))!.production.total).toBe(1003);
  });
});
