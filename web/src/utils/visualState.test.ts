// the binding spec itself (docs/sprint6.md), not a copy
import sprint6Doc from "../../../docs/sprint6.md?raw";
import { describe, expect, it } from "vitest";
import type { MachineVisual } from "@/services/sprint6Types";
import {
  applyStatusToMarker, bySeverity, factsFromMarker, formatSince, MACHINE_STATUS_MAP, markerText, STATE_META, STATE_ORDER,
  visualState, type StateFacts,
} from "./visualState";

/** The binding spec block (docs/sprint6.md §7 `s6-status-precedence`), read from the doc itself. */
function specBlock(name: string): unknown {
  const m = sprint6Doc.match(new RegExp("```json " + name + "\\n([\\s\\S]*?)```"));
  if (!m) throw new Error(`block ${name} not found`);
  return JSON.parse(m[1]);
}

interface PrecedenceBlock {
  order: string[];
  machine_status_map: Record<string, string>;
  cases: { id: string; facts: StateFacts; expected: string }[];
}

const block = specBlock("s6-status-precedence") as PrecedenceBlock;

describe("s6-status-precedence (docs/sprint6.md §1.2)", () => {
  it("has the same precedence order as the spec", () => {
    expect([...STATE_ORDER]).toEqual(block.order);
  });

  it("maps Machine.status like the spec", () => {
    expect(MACHINE_STATUS_MAP).toEqual(block.machine_status_map);
    for (const [status, expected] of Object.entries(block.machine_status_map)) {
      expect(visualState({ status })).toBe(expected);
    }
  });

  it.each(block.cases.map((c) => [c.id, c] as const))("case %s", (_id, c) => {
    expect(visualState(c.facts)).toBe(c.expected);
  });

  it("covers each of the 7 rules with its own condition", () => {
    expect(visualState({ status: "running", stale: true })).toBe("offline");                       // 1 no reading
    expect(visualState({ status: "running", open_failure: true })).toBe("failure");                // 2 FailureRecord
    expect(visualState({ status: "error", in_break: true, off_shift: true })).toBe("failure");     // 2 before 4/5
    expect(visualState({ status: "maintenance", in_break: true })).toBe("maintenance");            // 3
    expect(visualState({ status: "stopped", planned_stop: true, open_downtime: true })).toBe("planned_stop"); // 4
    expect(visualState({ status: "stopped", open_pln: true })).toBe("planned_stop");               // 4 PLN-* open
    expect(visualState({ status: "running", off_shift: true })).toBe("off_shift");                 // 5
    expect(visualState({ status: "running", open_downtime: true })).toBe("stopped");               // 6 open stop
    expect(visualState({ status: "running" })).toBe("running");                                    // 7
  });

  it("labels every state with icon and text, never colour only", () => {
    for (const s of STATE_ORDER) {
      expect(STATE_META[s].label.length).toBeGreaterThan(0);
      expect(STATE_META[s].icon.length).toBeGreaterThan(0);
    }
    expect(STATE_META.offline.dashed).toBe(true);
  });
});

function marker(over: Partial<MachineVisual> = {}): MachineVisual {
  return {
    machine_id: "m1", name: "Torno", line_id: "l1", line_name: "Linha", state: "running", icon: "play", text: "rodando",
    since: null, detail: {}, badges: [], status: "running", last_seen_at: null, kpis: null, ...over,
  };
}

describe("optimistic update on machine_status", () => {
  const now = "2026-10-07T12:00:00Z";

  it("running → error shows failure at once", () => {
    const m = applyStatusToMarker(marker(), "error", now, now);
    expect(m.state).toBe("failure");
    expect(m.text).toBe("falha");
    expect(m.since).toBe(now);
  });

  it("a break keeps planned_stop when the machine stops (rule 4 before 6)", () => {
    const m = marker({ state: "planned_stop", text: "pausa (refeição) até 12:30", since: "2026-10-07T11:30:00Z" });
    const next = applyStatusToMarker(m, "stopped", now, now);
    expect(next.state).toBe("planned_stop");
    expect(next.text).toBe("pausa (refeição) até 12:30");
    expect(next.since).toBe("2026-10-07T11:30:00Z");
  });

  it("a fault during the break turns the marker red (kpis S5-1)", () => {
    const m = marker({ state: "planned_stop", status: "stopped" });
    expect(applyStatusToMarker(m, "error", now, now).state).toBe("failure");
  });

  it("an open FailureRecord stays failure even if the machine runs again", () => {
    const m = marker({ state: "failure", status: "stopped" });
    expect(factsFromMarker(m).open_failure).toBe(true);
    expect(applyStatusToMarker(m, "running", now, now).state).toBe("failure");
  });

  it("an offline machine that reports again leaves offline", () => {
    const m = marker({ state: "offline", status: "unknown" });
    expect(applyStatusToMarker(m, "running", now, now).state).toBe("running");
  });

  it("returns the same object when nothing changes", () => {
    const m = marker();
    expect(applyStatusToMarker(m, "running", now, now)).toBe(m);
  });
});

describe("texts", () => {
  const now = Date.parse("2026-10-07T12:00:00Z");
  it("formats durations", () => {
    expect(formatSince("2026-10-07T11:54:00Z", now)).toBe("há 6 min");
    expect(formatSince("2026-10-07T09:50:00Z", now)).toBe("há 2 h 10 min");
    expect(formatSince(null, now)).toBeNull();
  });
  it("offline says how long without data", () => {
    expect(markerText({ state: "offline", text: "sem dados", since: null, last_seen_at: "2026-10-07T11:48:00Z" }, now))
      .toBe("sem dados há 12 min");
  });
  it("stopped carries its duration", () => {
    expect(markerText({ state: "stopped", text: "parada — sem motivo", since: "2026-10-07T11:54:00Z", last_seen_at: null }, now))
      .toBe("parada — sem motivo · há 6 min");
  });
  it("orders the most important problem first", () => {
    const list = [{ state: "running" as const, name: "A" }, { state: "failure" as const, name: "B" }, { state: "stopped" as const, name: "C" }];
    expect(list.sort(bySeverity).map((x) => x.name)).toEqual(["B", "C", "A"]);
  });
});
