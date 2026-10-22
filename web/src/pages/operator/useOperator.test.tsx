import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useIdleLogout } from "./useOperator";
import { enqueue, memoryStore, pendingByOwner, setQueueStore } from "@/services/operatorQueue";

describe("operator session with pending actions (§3.2: sent when the network is back)", () => {
  beforeEach(() => { vi.useFakeTimers(); setQueueStore(memoryStore()); });
  afterEach(() => { vi.useRealTimers(); setQueueStore(null); });

  it("ends the idle session after 15 min when nothing is pending", () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleLogout(onIdle, 1000, false));
    vi.advanceTimersByTime(1001);
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it("keeps the session while the operator has queued actions, and ends it once they are sent", () => {
    const onIdle = vi.fn();
    const { rerender } = renderHook(({ hold }) => useIdleLogout(onIdle, 1000, hold), { initialProps: { hold: true } });
    vi.advanceTimersByTime(5000);
    expect(onIdle).not.toHaveBeenCalled();
    rerender({ hold: false });
    vi.advanceTimersByTime(1001);
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it("lists the pending actions of each operator for the login screen", async () => {
    await enqueue({ kind: "justify", owner: "t:a", owner_name: "Ana", event_id: "e1", body: {}, label: "x" });
    await enqueue({ kind: "justify", owner: "t:a", owner_name: "Ana", event_id: "e2", body: {}, label: "y" });
    await enqueue({ kind: "create_stop", owner: "t:b", body: {}, label: "z" });
    const l = await pendingByOwner();
    expect(l).toEqual(expect.arrayContaining([
      { owner: "t:a", owner_name: "Ana", count: 2 },
      { owner: "t:b", owner_name: null, count: 1 },
    ]));
  });
});
