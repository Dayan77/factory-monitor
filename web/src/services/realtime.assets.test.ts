import { describe, expect, it } from "vitest";
import {
  applyMachineStatusToTree, applyUsageToMachineUsage, applyUsageToPosition, parseRealtimeMessage,
} from "./realtime";
import type { UsageMessage } from "./types";
import { machineUsage, plantTree, positionDetail } from "@/test/fixtures";

function usage(over: Partial<UsageMessage>): UsageMessage {
  return { type: "usage", position_id: "pos-1", machine_id: "m-1", measure: "cycles", delta: 2, total: 3_600_002, ...over };
}

describe("parseRealtimeMessage — usage", () => {
  it("parses a usage message", () => {
    const msg = parseRealtimeMessage(JSON.stringify({
      type: "usage", position_id: "pos-1", machine_id: "m-1", measure: "cycles", delta: 2, total: 10, timestamp: "t",
    }));
    expect(msg).toEqual({ type: "usage", position_id: "pos-1", machine_id: "m-1", measure: "cycles", delta: 2, total: 10, timestamp: "t" });
  });

  it("accepts machine-level messages (position_id null)", () => {
    const msg = parseRealtimeMessage(JSON.stringify({ type: "usage", position_id: null, machine_id: "m-1", measure: "total", delta: 1, total: 5 }));
    expect(msg).toMatchObject({ position_id: null, machine_id: "m-1", measure: "total" });
  });

  it.each([
    { type: "usage", position_id: null, machine_id: null, measure: "cycles", delta: 1, total: 1 },
    { type: "usage", position_id: "p", machine_id: "m", measure: "bogus", delta: 1, total: 1 },
    { type: "usage", position_id: "p", machine_id: "m", measure: "cycles", delta: "1", total: 1 },
    { type: "usage", position_id: "p", machine_id: "m", measure: "cycles", delta: 1, total: null },
  ])("rejects malformed %j", (raw) => {
    expect(parseRealtimeMessage(JSON.stringify(raw))).toBeNull();
  });
});

describe("applyMachineStatusToTree", () => {
  it("updates the machine and recounts the line summary", () => {
    const tree = plantTree();
    const next = applyMachineStatusToTree(tree, { machine_id: "m-1", status: "error" })!;
    expect(next).not.toBe(tree);
    const line = next[0].lines[0];
    expect(line.machines[0].status).toBe("error");
    expect(line.status_summary).toMatchObject({ running: 1, error: 1 });
    // untouched line keeps its reference
    expect(next[0].lines[1]).toBe(tree[0].lines[1]);
  });

  it("returns the same reference for unknown machines", () => {
    const tree = plantTree();
    expect(applyMachineStatusToTree(tree, { machine_id: "zzz", status: "error" })).toBe(tree);
  });
});

describe("applyUsageToMachineUsage", () => {
  it("sets the position total to the accumulator value (idempotent)", () => {
    const u = machineUsage();
    const next = applyUsageToMachineUsage(u, "m-1", usage({ total: 3_600_010 }))!;
    expect(next.positions[0].cycles_total).toBe(3_600_010);
    expect(applyUsageToMachineUsage(next, "m-1", usage({ total: 3_600_010 }))!.positions[0].cycles_total).toBe(3_600_010);
  });

  it("maps hours/starts measures to their totals", () => {
    const next = applyUsageToMachineUsage(machineUsage(), "m-1", usage({ measure: "hours", total: 1201 }))!;
    expect(next.positions[0].hours_total).toBe(1201);
  });

  it("adds machine production deltas: good = total − scrap", () => {
    let u = machineUsage(); // good 980, scrap 20, total 1000
    u = applyUsageToMachineUsage(u, "m-1", usage({ position_id: null, measure: "total", delta: 5, total: 99999 }))!;
    expect(u.production).toMatchObject({ total: 1005, scrap: 20, good: 985 });
    u = applyUsageToMachineUsage(u, "m-1", usage({ position_id: null, measure: "scrap", delta: 1, total: 1 }))!;
    expect(u.production).toMatchObject({ total: 1005, scrap: 21, good: 984 });
    // good messages are ignored (total already carries those parts)
    expect(applyUsageToMachineUsage(u, "m-1", usage({ position_id: null, measure: "good", delta: 4, total: 1 }))).toBe(u);
  });

  it("ignores production of other machines and unknown positions", () => {
    const u = machineUsage();
    expect(applyUsageToMachineUsage(u, "m-1", usage({ position_id: null, machine_id: "m-9", measure: "total" }))).toBe(u);
    expect(applyUsageToMachineUsage(u, "m-1", usage({ position_id: "pos-x" }))).toBe(u);
  });
});

describe("applyUsageToPosition", () => {
  it("updates totals, component usage and recomputes life from nominal life", () => {
    const d = positionDetail();
    const next = applyUsageToPosition(d, usage({ delta: 400_000, total: 4_000_000 }))!;
    expect(next.usage.cycles_total).toBe(4_000_000);
    expect(next.component_usage?.cycles).toBe(4_000_000);
    expect(next.component_usage?.life_consumed_pct).toBeCloseTo(0.8);
    expect(next.component_usage?.life_measure).toBe("cycles");
  });

  it("ignores messages for other positions", () => {
    const d = positionDetail();
    expect(applyUsageToPosition(d, usage({ position_id: "pos-9" }))).toBe(d);
  });
});
