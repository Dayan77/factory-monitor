import { describe, expect, it } from "vitest";
import {
  buildTimeline, buildWaterfall, formatDuration, formatPct1, formatShortDuration, formatTimer, groupReasonCodes, oeeTone, reasonLabel, shapePareto,
} from "./efficiency";
import { downtime, oeeResult, paretoRows, reasonCodes } from "@/test/fixtures";

describe("formatters", () => {
  it("formatDuration picks a readable unit", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(12 * 60 + 5)).toBe("12 min");
    expect(formatDuration(3600 + 5 * 60)).toBe("1 h 05 min");
    expect(formatDuration(2 * 86400 + 3 * 3600)).toBe("2 d 3 h");
    expect(formatDuration(-5)).toBe("0 s");
  });
  it("formatShortDuration is compact for chart labels", () => {
    expect(formatShortDuration(3180)).toBe("53 min");
    expect(formatShortDuration(21180)).toBe("5,9 h");
    expect(formatShortDuration(null)).toBe("—");
  });
  it("formatTimer is a stopwatch", () => {
    expect(formatTimer(247)).toBe("04:07");
    expect(formatTimer(3847)).toBe("1:04:07");
    expect(formatTimer(undefined)).toBe("—");
  });
  it("formatPct1 keeps one decimal and never truncates above 100%", () => {
    expect(formatPct1(0.7244)).toBe("72,4\u00a0%");
    expect(formatPct1(1.053)).toBe("105,3\u00a0%");
    expect(formatPct1(null)).toBe("—");
  });
  it("oeeTone bands: ≥85% ok, ≥60% warn, else danger; null neutral", () => {
    expect(oeeTone(0.85)).toBe("ok");
    expect(oeeTone(0.84)).toBe("warn");
    expect(oeeTone(0.6)).toBe("warn");
    expect(oeeTone(0.59)).toBe("danger");
    expect(oeeTone(null)).toBe("neutral");
  });
});

describe("reason codes", () => {
  it("groups active codes by category, unplanned first, excluding 'não justificada' and inactive", () => {
    const groups = groupReasonCodes(reasonCodes());
    expect(groups.map((g) => g.category)).toEqual(["EQP", "SET", "MAT", "PLN"]);
    expect(groups[0].label).toBe("Equipamento");
    expect(groups[0].codes.map((c) => c.code)).toEqual(["EQP-01", "EQP-01-HID", "EQP-03"]);
    expect(groups.flatMap((g) => g.codes).some((c) => c.code === "UNK-01" || c.code === "OPR-02")).toBe(false);
  });
  it("reasonLabel resolves codes, pseudo codes and the unjustified state", () => {
    expect(reasonLabel("EQP-03", reasonCodes())).toBe("Falha pneumática/hidráulica");
    expect(reasonLabel(null, reasonCodes())).toBe("Não justificada");
    expect(reasonLabel("UNK-01", reasonCodes())).toBe("Não justificada");
    expect(reasonLabel("micro_stops", undefined)).toBe("Micro-paradas");
    expect(reasonLabel("XYZ-99", reasonCodes())).toBe("XYZ-99");
  });
});

describe("shapePareto", () => {
  it("sorts descending, drops zero/planned rows and accumulates to 100%", () => {
    const rows = [...paretoRows(), { ...paretoRows()[0], reason_code: "PLN-01", planned: true, duration_s: 9999 }];
    const bars = shapePareto(rows);
    expect(bars.map((b) => b.key)).toEqual(["speed_loss", "EQP-03", "SET-01", "micro_stops"]);
    expect(bars[0].pct).toBeCloseTo(36);
    expect(bars[0].minutes).toBe(30);
    expect(bars[bars.length - 1].cumulative).toBeCloseTo(100);
    expect(bars.find((b) => b.key === "speed_loss")?.pseudo).toBe(true);
    expect(bars.find((b) => b.key === "EQP-03")?.pseudo).toBe(false);
  });
  it("folds the tail into 'Outros' keeping the cumulative line honest", () => {
    const rows = Array.from({ length: 10 }, (_, i) => ({
      reason_code: `EQP-0${i}`, category: "EQP", label: `M${i}`, planned: false, duration_s: 100 - i, events: 1, pct: null, cumulative_pct: null,
    }));
    const bars = shapePareto(rows, 5);
    expect(bars).toHaveLength(5);
    expect(bars[4].label).toBe("Outros (6)");
    expect(bars[4].duration_s).toBe(96 + 95 + 94 + 93 + 92 + 91);
    expect(bars[4].cumulative).toBeCloseTo(100);
  });
  it("last cumulative row renders exactly '100,0 %' even when rounded rows sum to 99,9 % (S3-5)", () => {
    // three equal losses: each row 33,3 % (sum of rounded = 99,9 %), cumulative from the raw values
    const rows = ["A", "B", "C"].map((k) => ({
      reason_code: `EQP-0${k}`, category: "EQP", label: k, planned: false, duration_s: 100, events: 1, pct: null, cumulative_pct: null,
    }));
    const bars = shapePareto(rows);
    expect(bars.map((b) => formatPct1(b.pct / 100))).toEqual(["33,3\u00a0%", "33,3\u00a0%", "33,3\u00a0%"]);
    expect(formatPct1(bars[2].cumulative / 100)).toBe("100,0\u00a0%");
  });
  it("returns nothing when there is no loss", () => {
    expect(shapePareto([])).toEqual([]);
    expect(shapePareto([paretoRows()[4]])).toEqual([]);
  });
});

describe("buildWaterfall", () => {
  it("walks POT → PBT → APT → net productive with floating losses (kpis.md E1)", () => {
    const steps = buildWaterfall(oeeResult());
    const byKey = Object.fromEntries(steps.map((s) => [s.key, s]));
    expect(steps.map((s) => s.key)).toEqual(["pot", "pdot", "pbt", "adot", "apt", "micro", "speed", "quality", "net"]);
    expect(byKey.pot.value_s).toBe(480 * 60);
    expect(byKey.pdot).toMatchObject({ kind: "loss", value_s: 30 * 60, base_s: 450 * 60 });
    expect(byKey.pbt).toMatchObject({ kind: "total", value_s: 450 * 60, base_s: 0 });
    expect(byKey.adot).toMatchObject({ value_s: 60 * 60, base_s: 390 * 60 });
    expect(byKey.micro.base_s).toBe((390 - 10) * 60);
    expect(byKey.speed.base_s).toBe(350 * 60);
    expect(byKey.quality.base_s).toBe(339.5 * 60);
    expect(byKey.net.value_s).toBe(339.5 * 60);
  });
  it("shows a 'sem dados' step when part of POT has no data, and clamps losses at zero", () => {
    const steps = buildWaterfall(oeeResult({ pot_s: 1000, pdot_s: 100, nodata_s: 200, pbt_s: 700, adot_s: 800, apt_s: 0, micro_stop_loss_s: 50, speed_loss_s: 0, net_productive_s: 0 }));
    const nodata = steps.find((s) => s.key === "nodata");
    expect(nodata).toMatchObject({ value_s: 200, base_s: 700 });
    expect(steps.find((s) => s.key === "adot")?.value_s).toBe(700); // clamped to PBT
    expect(steps.find((s) => s.key === "micro")?.value_s).toBe(0);
    expect(steps.every((s) => s.base_s >= 0 && s.value_s >= 0)).toBe(true);
  });
});

describe("buildTimeline", () => {
  const T = (h: number, m = 0) => Date.UTC(2026, 9, 5, h, m);
  it("fills gaps as running, classifies events and marks the future as no-data", () => {
    const evs = [
      downtime({ id: "a", start: new Date(T(8)).toISOString(), end: new Date(T(8, 30)).toISOString(), fault_code: 17 }),
      downtime({ id: "b", start: new Date(T(10)).toISOString(), end: new Date(T(10, 30)).toISOString(), planned: true, fault_code: null, source: "manual", reason_code: "PLN-01" }),
      downtime({ id: "c", start: new Date(T(11)).toISOString(), end: null, fault_code: null, source: "auto_run_state" }),
    ];
    const segs = buildTimeline(evs, T(7), T(13), T(12));
    expect(segs.map((s) => [s.state, (s.start - T(7)) / 60000, (s.end - T(7)) / 60000])).toEqual([
      ["running", 0, 60], ["error", 60, 90], ["running", 90, 180], ["planned", 180, 210], ["running", 210, 240],
      ["stopped", 240, 300], ["nodata", 300, 360],
    ]);
    expect(segs[1].event?.id).toBe("a");
  });
  it("clips events to the window and resolves overlaps", () => {
    const evs = [
      downtime({ id: "a", start: new Date(T(6)).toISOString(), end: new Date(T(7, 30)).toISOString(), fault_code: null, source: "auto_run_state" }),
      downtime({ id: "b", start: new Date(T(7, 15)).toISOString(), end: new Date(T(7, 45)).toISOString(), fault_code: null, source: "auto_run_state" }),
    ];
    const segs = buildTimeline(evs, T(7), T(8), T(9));
    expect(segs.map((s) => [s.state, (s.start - T(7)) / 60000, (s.end - T(7)) / 60000])).toEqual([
      ["stopped", 0, 30], ["stopped", 30, 45], ["running", 45, 60],
    ]);
  });
});

describe("trendLabel", () => {
  it("formats in the plant timezone by granularity", async () => {
    const { trendLabel } = await import("./efficiency");
    const p = { t: "2026-10-05T17:00:00Z", label: "2026-10-05 14:00" };
    expect(trendLabel(p, "hour", "America/Sao_Paulo")).toBe("14h");
    expect(trendLabel({ t: "2026-10-05T03:00:00Z", label: "x" }, "day", "America/Sao_Paulo")).toBe("05/10");
    expect(trendLabel({ t: "2026-10-05T09:00:00Z", label: "05/10 Turno A" }, "shift", "UTC")).toBe("05/10 Turno A");
  });
});
