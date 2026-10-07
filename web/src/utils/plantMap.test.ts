import { describe, expect, it } from "vitest";
import type { LayoutMarker, MachineVisual, MapView } from "@/services/sprint6Types";
import {
  align, applyStatusToMapView, duplicateMachines, historyInit, historyPush, historyRedo, historyUndo, liveness,
  markerErrors, markerKey, mergeKpis, normRotation, snap, toNormalized, unplaced,
} from "./plantMap";

function mv(over: Partial<MachineVisual> = {}): MachineVisual {
  return {
    machine_id: "m1", name: "Torno", line_id: "l1", line_name: "Linha", state: "running", icon: "play", text: "rodando",
    since: null, detail: {}, badges: [], status: "running", last_seen_at: null, kpis: null, ...over,
  };
}

function view(machines: MachineVisual[]): MapView {
  return {
    kind: "auto", banner: "", lanes: [], markers: [], plant_id: "p", plant_name: "P", generated_at: "2026-10-07T12:00:00Z",
    machines, lines: [{ line_id: "l1", name: "Linha", output_machine_id: "m1", output_state: "running", output_text: "rodando", kpis: null }],
    shift: null, unplaced_machine_ids: [],
  };
}

describe("live merge", () => {
  it("patches the marker and the output machine of the line label", () => {
    const v = view([mv(), mv({ machine_id: "m2", name: "B" })]);
    const next = applyStatusToMapView(v, { type: "machine_status", machine_id: "m1", status: "error", last_seen_at: null })!;
    expect(next.machines[0].state).toBe("failure");
    expect(next.machines[1]).toBe(v.machines[1]);
    expect(next.lines[0].output_state).toBe("failure");
  });
  it("keeps the same reference for another machine", () => {
    const v = view([mv()]);
    expect(applyStatusToMapView(v, { type: "machine_status", machine_id: "x", status: "error", last_seen_at: null })).toBe(v);
  });
  it("merges shift KPIs without inventing values", () => {
    const live = view([mv(), mv({ machine_id: "m2" })]);
    const k = view([mv({ kpis: { oee: 0.8, cadence_per_hour: 100, good: 10, scrap: 0 } })]);
    const out = mergeKpis(live, k);
    expect(out.machines[0].kpis?.oee).toBe(0.8);
    expect(out.machines[1].kpis).toBeNull();
  });
});

describe("liveness §1.3", () => {
  const t0 = 1_000_000;
  it("live while connected", () => expect(liveness(true, null, t0)).toBe("live"));
  it("reconnecting up to 10 s", () => expect(liveness(false, t0, t0 + 9_000)).toBe("reconnecting"));
  it("polling after 10 s", () => expect(liveness(false, t0, t0 + 11_000)).toBe("polling"));
  it("paused within 30 s", () => {
    expect(liveness(false, t0, t0 + 28_000)).toBe("polling");
    expect(liveness(false, t0, t0 + 29_500)).toBe("paused");
  });
});

describe("editor model", () => {
  it("snaps to the 1 % grid and clamps", () => {
    expect(snap(0.1234)).toBe(0.12);
    expect(snap(0.126)).toBe(0.13);
    expect(snap(1.4)).toBe(1);
    expect(snap(-0.2)).toBe(0);
    expect(snap(0.1234, 0.01, false)).toBe(0.1234);
  });
  it("normalizes rotation to [0, 360)", () => {
    expect(normRotation(360)).toBe(0);
    expect(normRotation(-90)).toBe(270);
    expect(normRotation(450)).toBe(90);
  });
  it("converts pointer to normalized coordinates", () => {
    expect(toNormalized(150, 75, { left: 100, top: 50, width: 200, height: 100 })).toEqual({ x: 0.25, y: 0.25 });
    expect(toNormalized(1000, -5, { left: 0, top: 0, width: 100, height: 100 })).toEqual({ x: 1, y: 0 });
  });
  const m = (machine_id: string, x = 0.5, y = 0.5): LayoutMarker => ({ kind: "machine", machine_id, line_id: null, label: null, x, y, rotation_deg: 0, size: "m" });
  it("detects duplicates and unplaced machines", () => {
    expect(duplicateMachines([m("a"), m("b"), m("a")])).toEqual(["a"]);
    expect(unplaced(["a", "b", "c"], [m("a")])).toEqual(["b", "c"]);
  });
  it("validates like the server (422)", () => {
    expect(markerErrors([m("a", 1.2)])[0]).toMatch(/fora da planta/);
    expect(markerErrors([{ ...m("a"), rotation_deg: 360 }])[0]).toMatch(/rotação/);
    expect(markerErrors([{ kind: "area_label", machine_id: null, line_id: null, label: " ", x: 0.1, y: 0.1, rotation_deg: 0, size: "m" }])[0]).toMatch(/rótulo/);
    expect(markerErrors([m("a"), m("a")])).toContain("máquina posicionada mais de uma vez");
    expect(markerErrors([m("a"), m("b")])).toEqual([]);
  });
  it("aligns the selection on its mean", () => {
    const list = [m("a", 0.1, 0.2), m("b", 0.3, 0.4), m("c", 0.9, 0.9)];
    const keys = new Set([markerKey(list[0], 0), markerKey(list[1], 1)]);
    const out = align(list, keys, "y");
    expect(out[0].y).toBe(0.3);
    expect(out[1].y).toBe(0.3);
    expect(out[2].y).toBe(0.9);
  });
  it("undo / redo", () => {
    let h = historyInit(1);
    h = historyPush(h, 2);
    h = historyPush(h, 3);
    h = historyUndo(h);
    expect(h.present).toBe(2);
    h = historyRedo(h);
    expect(h.present).toBe(3);
    h = historyUndo(historyUndo(h));
    expect(h.present).toBe(1);
    expect(historyUndo(h)).toBe(h);
    h = historyPush(h, 9);
    expect(h.future).toEqual([]);
  });
});
