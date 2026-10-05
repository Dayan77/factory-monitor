import { afterEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import {
  applyPending, enqueue, flushQueue, getFailed, getQueue, loadList, loadWorkOrder, saveList, withSnapshot,
} from "./offline";
import { workOrder } from "@/test/maintenanceFixtures";

function networkError() {
  return new AxiosError("Network Error", "ERR_NETWORK");
}
function http(status: number) {
  return new AxiosError("x", "ERR", undefined, undefined, { status, data: { detail: { message: "Transição inválida.", allowed: ["on_hold"] } }, statusText: "", headers: {}, config: { headers: new AxiosHeaders() } });
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("offline action queue", () => {
  it("replays in order and empties the queue", async () => {
    enqueue({ kind: "task", wo_id: "wo-1", body: { task_id: "t1", done: true } });
    enqueue({ kind: "labor", wo_id: "wo-1", body: { hours: 0.5 } });
    enqueue({ kind: "transition", wo_id: "wo-1", body: { to: "on_hold", hold_reason: "waiting_parts" } });
    const seen: string[] = [];
    const r = await flushQueue(async (a) => { seen.push(`${a.kind}:${a.id.length > 0}`); }, () => "err");
    expect(seen).toEqual(["task:true", "labor:true", "transition:true"]);
    expect(r).toEqual({ sent: 3, failed: 0, stalled: false });
    expect(getQueue()).toEqual([]);
  });

  it("stops on a network error and keeps the rest (retry later)", async () => {
    enqueue({ kind: "task", wo_id: "wo-1", body: { task_id: "t1", done: true } });
    enqueue({ kind: "labor", wo_id: "wo-1", body: { hours: 1 } });
    const r = await flushQueue(async () => { throw networkError(); }, () => "err");
    expect(r.stalled).toBe(true);
    expect(getQueue()).toHaveLength(2);
  });

  it("a server rejection goes to the failed list (shown to the technician) and the queue continues", async () => {
    enqueue({ kind: "transition", wo_id: "wo-1", wo_number: "OS-1", body: { to: "completed" } });
    enqueue({ kind: "labor", wo_id: "wo-1", body: { hours: 1 } });
    const exec = vi.fn().mockRejectedValueOnce(http(422)).mockResolvedValueOnce({});
    const r = await flushQueue(exec, () => "Transição inválida.");
    expect(r).toEqual({ sent: 1, failed: 1, stalled: false });
    expect(getQueue()).toEqual([]);
    expect(getFailed()[0]).toMatchObject({ message: "Transição inválida.", action: { kind: "transition", wo_number: "OS-1" } });
  });
});

describe("applyPending (what the technician sees before the sync)", () => {
  it("applies task checks, labor and transitions in order with the local state machine", () => {
    const wo = workOrder({ state: "in_progress", labor_hours: 0.5, allowed_transitions: ["on_hold", "completed", "cancelled"] });
    enqueue({ kind: "task", wo_id: wo.id, body: { task_id: "t2", done: true } });
    enqueue({ kind: "labor", wo_id: wo.id, body: { hours: 0.75, remaining_hours: 0.5 } });
    enqueue({ kind: "transition", wo_id: wo.id, body: { to: "on_hold", hold_reason: "waiting_parts" } });
    enqueue({ kind: "labor", wo_id: "other", body: { hours: 9 } });
    const out = applyPending(wo, getQueue());
    expect(out.task_list.find((t) => t.id === "t2")?.done).toBe(true);
    expect(out.labor_hours).toBe(1.25);
    expect(out.remaining_hours).toBe(0.5);
    expect(out.state).toBe("on_hold");
    expect(out.hold_reason).toBe("waiting_parts");
    expect(out.allowed_transitions).toEqual(["in_progress", "scheduled", "cancelled"]);
    expect(out.history[out.history.length - 1]).toMatchObject({ from_state: "in_progress", to_state: "on_hold" });
  });
});

describe("snapshots (offline reads are flagged, never presented as live)", () => {
  it("network ok → saves the snapshot; network down → returns it as 'cache'", async () => {
    const list = [workOrder()];
    const live = await withSnapshot(async () => list, saveList, loadList);
    expect(live.source).toBe("network");
    expect(loadWorkOrder("wo-1")?.data.number).toBe("OS-114");
    const off = await withSnapshot(async () => { throw networkError(); }, saveList, loadList);
    expect(off.source).toBe("cache");
    expect(off.data[0].id).toBe("wo-1");
  });

  it("does not even try while the browser is offline", async () => {
    saveList([workOrder()]);
    vi.stubGlobal("navigator", { onLine: false });
    const fetcher = vi.fn();
    const r = await withSnapshot(fetcher, saveList, loadList);
    expect(fetcher).not.toHaveBeenCalled();
    expect(r.source).toBe("cache");
  });

  it("server errors are not masked by the snapshot", async () => {
    saveList([workOrder()]);
    await expect(withSnapshot(async () => { throw http(500); }, saveList, loadList)).rejects.toBeInstanceOf(AxiosError);
  });
});
