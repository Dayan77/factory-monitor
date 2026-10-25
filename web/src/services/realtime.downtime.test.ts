import { describe, expect, it } from "vitest";
import { applyDowntimeToList, isUnjustified, parseRealtimeMessage } from "./realtime";
import { downtime } from "@/test/fixtures";

describe("downtime socket messages", () => {
  it("parses {type:'downtime'} keeping only the keys it carries", () => {
    const msg = parseRealtimeMessage(JSON.stringify({
      type: "downtime", action: "justify",
      event: { id: "d-1", machine_id: "m-1", start: "2026-10-05T11:30:00Z", end: null, reason_code: "EQP-03", planned: false, duration_s: null },
    }));
    expect(msg).toEqual({
      type: "downtime", action: "justify",
      event: { id: "d-1", machine_id: "m-1", start: "2026-10-05T11:30:00Z", end: null, reason_code: "EQP-03", planned: false, duration_s: null },
    });
  });
  it("rejects malformed events", () => {
    expect(parseRealtimeMessage(JSON.stringify({ type: "downtime", event: { id: 1 } }))).toBeNull();
    expect(parseRealtimeMessage(JSON.stringify({ type: "downtime" }))).toBeNull();
  });
  it("isUnjustified: null or UNK-01", () => {
    expect(isUnjustified({ reason_code: null })).toBe(true);
    expect(isUnjustified({ reason_code: "UNK-01" })).toBe(true);
    expect(isUnjustified({ reason_code: "EQP-01" })).toBe(false);
  });
});

describe("applyDowntimeToList", () => {
  const list = [downtime({ id: "a", end: null, duration_s: null }), downtime({ id: "b" })];
  it("merges a known event without wiping fields the message lacks", () => {
    const next = applyDowntimeToList(list, {}, { event: { id: "a", machine_id: "m-1", start: list[0].start, end: "2026-10-05T12:00:00Z", duration_s: 1800 } });
    expect(next).not.toBe(list);
    expect(Array.isArray(next) && next[0]).toMatchObject({ id: "a", end: "2026-10-05T12:00:00Z", duration_s: 1800, machine_name: "Prensa P-01", fault_code: 17 });
  });
  it("drops an event that no longer matches the query (justified / closed)", () => {
    const justified = applyDowntimeToList(list, { unjustified: true }, { event: { id: "a", machine_id: "m-1", start: list[0].start, reason_code: "EQP-03" } });
    expect(Array.isArray(justified) && justified.map((e) => e.id)).toEqual(["b"]);
    const closed = applyDowntimeToList(list, { open: true }, { event: { id: "a", machine_id: "m-1", start: list[0].start, end: "2026-10-05T12:00:00Z" } });
    expect(Array.isArray(closed) && closed.map((e) => e.id)).toEqual(["b"]);
  });
  it("removes an event the detector deleted (late readings re-detected) and ignores unknown deletes", () => {
    const next = applyDowntimeToList(list, {}, { action: "delete", event: { id: "a", machine_id: "m-1", start: list[0].start } });
    expect(Array.isArray(next) && next.map((e) => e.id)).toEqual(["b"]);
    expect(applyDowntimeToList(list, {}, { action: "delete", event: { id: "zz", machine_id: "m-1", start: list[0].start } })).toBe(list);
  });
  it("asks for a refetch on unknown events (unless clearly for another machine)", () => {
    expect(applyDowntimeToList(list, { line_id: "line-1" }, { event: { id: "new", machine_id: "m-9", start: "2026-10-05T12:00:00Z" } })).toBe("refetch");
    expect(applyDowntimeToList(list, { machine_id: "m-1" }, { event: { id: "new", machine_id: "m-9", start: "2026-10-05T12:00:00Z" } })).toBe(list);
    expect(applyDowntimeToList(undefined, {}, { event: { id: "new", machine_id: "m-9", start: "x" } })).toBeUndefined();
  });
});
