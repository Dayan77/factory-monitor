import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DowntimeEvent } from "./types";
import { enqueue, flush, memoryStore, pending, setQueueStore, topReasons, type OpAction, type SendResult } from "./operatorQueue";

const ev = { id: "e1", machine_id: "m1", start: "2026-10-07T10:00:00Z" } as DowntimeEvent;

beforeEach(() => setQueueStore(memoryStore()));
afterEach(() => setQueueStore(null));

async function add(label: string, created_at: string, owner = "t:u") {
  return enqueue({ kind: "justify", owner, event_id: "e1", body: { reason_code: "MAT-01" }, label, created_at });
}

describe("operator offline queue (sprint6 §3.2)", () => {
  it("keeps the order and each action's idempotency key", async () => {
    const a = await add("a", "2026-10-07T10:00:00Z");
    const b = await add("b", "2026-10-07T10:01:00Z");
    expect(a.id).not.toBe(b.id);
    expect(a.id).toMatch(/^op-/);
    expect((await pending("t:u")).map((x) => x.label)).toEqual(["a", "b"]);
  });

  it("sends in order once and leaves nothing behind", async () => {
    await add("a", "2026-10-07T10:00:00Z");
    await add("b", "2026-10-07T10:01:00Z");
    const seen: string[] = [];
    const sender = vi.fn(async (x: OpAction): Promise<SendResult> => { seen.push(x.label); return { kind: "sent", event: ev, replay: false }; });
    const rep = await flush("t:u", sender);
    expect(seen).toEqual(["a", "b"]);
    expect(rep.sent).toHaveLength(2);
    expect(await pending("t:u")).toHaveLength(0);
    await flush("t:u", sender);
    expect(sender).toHaveBeenCalledTimes(2);           // a second flush sends nothing again
  });

  it("stops at the first network failure and keeps the rest in order", async () => {
    await add("a", "2026-10-07T10:00:00Z");
    await add("b", "2026-10-07T10:01:00Z");
    const sender = vi.fn(async (): Promise<SendResult> => ({ kind: "offline" }));
    const rep = await flush("t:u", sender);
    expect(sender).toHaveBeenCalledTimes(1);
    expect(rep.remaining).toBe(2);
    const left = await pending("t:u");
    expect(left.map((x) => x.label)).toEqual(["a", "b"]);
    expect(left[0].attempts).toBe(1);
  });

  it("the server wins on conflict: dropped, with what was recorded", async () => {
    await add("a", "2026-10-07T10:00:00Z");
    const rep = await flush("t:u", async () => ({ kind: "conflict", current: { ...ev, reason_code: "EQP-01" }, message: "já justificada" }));
    expect(rep.conflicts[0].current?.reason_code).toBe("EQP-01");
    expect(await pending("t:u")).toHaveLength(0);
  });

  it("replays only the actions of the logged-in operator", async () => {
    await add("mine", "2026-10-07T10:00:00Z", "t:u");
    await add("other", "2026-10-07T10:00:01Z", "t:x");
    const seen: string[] = [];
    await flush("t:u", async (x) => { seen.push(x.label); return { kind: "sent", event: ev, replay: false }; });
    expect(seen).toEqual(["mine"]);
    expect((await pending("t:x")).map((x) => x.label)).toEqual(["other"]);
  });

  it("holds an action during its 5 s undo window, keeping the order", async () => {
    const t = Date.parse("2026-10-07T10:00:00Z");
    await enqueue({ kind: "justify", owner: "t:u", event_id: "e1", body: {}, label: "held", created_at: "2026-10-07T10:00:00Z", not_before: t + 5000 });
    await add("later", "2026-10-07T10:00:01Z");
    const sender = vi.fn(async (): Promise<SendResult> => ({ kind: "sent", event: ev, replay: false }));
    const rep = await flush("t:u", sender, () => t + 1000);
    expect(sender).not.toHaveBeenCalled();
    expect(rep.retryAt).toBe(t + 5000);
    const rep2 = await flush("t:u", sender, () => t + 6000);
    expect(rep2.sent.map((x) => x.label)).toEqual(["held", "later"]);
  });

  it("ranks the most used reasons of a machine", () => {
    const evs = ["MAT-01", "EQP-02", "MAT-01", "UNK-01", "SET-01", "MAT-01", "EQP-02"].map((c) => ({ reason_code: c }));
    expect(topReasons(evs, 2)).toEqual(["MAT-01", "EQP-02"]);
    expect(topReasons(evs)).not.toContain("UNK-01");
    expect(topReasons(evs, 8, (c) => c.startsWith("PLN"))).toEqual(["MAT-01", "EQP-02", "SET-01"]);
  });
});
