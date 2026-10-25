import { describe, expect, it } from "vitest";
import { normalizeOverview } from "./efficiency";
import { overviewPayload } from "@/test/fixtures";

describe("normalizeOverview", () => {
  it("maps the server schema (line_name, oee_today, cadence_per_hour, components_near_end…) to the UI shape", () => {
    const o = normalizeOverview(overviewPayload());
    expect(o.plant_name).toBe("Planta Manaus");
    expect(o.lines[0]).toMatchObject({
      line_id: "line-1", name: "Linha 1 — Montagem", oee: 0.724, oee_current_shift: 0.81, cadence: 96.4,
      unjustified_count: 2, worst_machine: { id: "m-1", name: "Prensa P-01", oee: 0.612 },
    });
    expect(o.lines[1].worst_machine).toBeNull();
    expect(o.machines_in_error[0]).toEqual({ id: "m-3", name: "Envasadora E-01", line_id: "line-2", since: "2026-10-05T11:50:00Z", fault_code: null });
    expect(o.components_near_eol[0]).toMatchObject({ position_id: "pos-2", machine_id: "m-1", life_consumed_pct: 0.95, life_measure: "starts", projected_end_date: null });
    expect(o.top_losses).toHaveLength(3);
  });

  it("also accepts the sprint-contract spelling (name, oee, cadence, components_near_eol, since, fault_code)", () => {
    const o = normalizeOverview({
      lines: [{ line_id: "l", name: "L", oee: 0.5, availability: null, performance: null, quality: null, good: 1, scrap: 0, cadence: 10,
        open_downtime_count: 0, unjustified_count: 0, worst_machine: { id: "m", name: "M", oee: 0.4 } }],
      top_losses: [],
      machines_in_error: [{ id: "m", name: "M", since: "2026-10-05T10:00:00Z", fault_code: 12 }],
      components_near_eol: [{ position_id: "p", position_name: "P", machine_name: "M", life_consumed_pct: 0.9, projected_end_date: "2026-11-01" }],
    });
    expect(o.lines[0]).toMatchObject({ name: "L", oee: 0.5, cadence: 10, worst_machine: { id: "m", name: "M", oee: 0.4 } });
    expect(o.machines_in_error[0]).toMatchObject({ since: "2026-10-05T10:00:00Z", fault_code: 12 });
    expect(o.components_near_eol[0].projected_end_date).toBe("2026-11-01");
  });

  it("never invents values: missing ratios stay null, garbage is dropped", () => {
    const o = normalizeOverview({ lines: [{ line_id: "x" }, { nope: true }, null], top_losses: [{ label: "no duration" }], machines_in_error: "bad" });
    expect(o.lines).toHaveLength(1);
    expect(o.lines[0]).toMatchObject({ oee: null, availability: null, cadence: null, good: 0, worst_machine: null });
    expect(o.top_losses).toEqual([]);
    expect(o.machines_in_error).toEqual([]);
    expect(normalizeOverview(null).lines).toEqual([]);
  });
});
