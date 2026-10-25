import { describe, expect, it } from "vitest";
import {
  buildChartModel, countBySeverity, daysText, evidenceText, formatSignal, learningProgress, ruleText, rulView, slopeText,
  sortAlerts, triggerLabel,
} from "./predictive";
import { alert, learningMonitor, monitor, rulF1, series } from "@/test/predictiveFixtures";

describe("RUL text (P-6)", () => {
  it("F1 block 50: 11 days, 09/02/2027, band 06/02–14/02", () => {
    const v = rulView(rulF1());
    expect(v.headline).toBe("em 11 dias");
    expect(v.detail).toBe("09/02/2027 (faixa 06/02/2027 – 14/02/2027)");
    expect(v.compact).toBe("11 dias (8–16)");
    expect(v.tone).toBe("warn");
  });
  it("no trend → null with the reason, never a number", () => {
    const v = rulView({ rul_days: null, reason: "no_significant_trend" });
    expect(v.headline).toBeNull();
    expect(v.reason).toBe("sem tendência de degradação significativa");
  });
  it("limit reached and > 1 year", () => {
    expect(rulView({ rul_days: 0, limit_reached: true }).headline).toBe("limite de falha atingido");
    expect(rulView({ rul_days: 80, rul_days_low: 39, rul_days_high: 528 }).compact).toBe("80 dias (39–> 1 ano)");
    expect(daysText(400)).toBe("> 1 ano");
    expect(daysText(1)).toBe("1 dia");
  });
  it("pessimistic ≤ 7 days is critical tone", () => {
    expect(rulView({ rul_days: 12, rul_days_low: 6 }).tone).toBe("danger");
  });
});

describe("formatting", () => {
  it("pt-BR signal and stats", () => {
    expect(formatSignal(206.5, "ms")).toBe("206,5 ms");
    expect(formatSignal(0.148, "A")).toBe("0,15 A");
    expect(formatSignal(null, "ms")).toBe("—");
  });
  it("slope per usage (stroke_time per 10 000 cycles)", () => {
    expect(slopeText(9.394e-5, "stroke_time", "ms")).toBe("+0,94 ms a cada 10.000 ciclos");
    expect(slopeText(0.002, "current", "A")).toBe("+0,002 A por hora de operação");
  });
  it("rule texts resolve % of baseline against m0", () => {
    expect(ruleText({ type: "pct_of_baseline", value: 1.1, sustained_for: 2 }, "ms", 200)).toBe("> 110 % da referência (220,0 ms) por 2 janelas seguidas");
    expect(ruleText({ type: "threshold", value: 230, sustained_for: 1 }, "ms")).toBe("> 230,0 ms");
  });
  it("evidence: detector in σ, rule in signal units", () => {
    const [ewma, pct] = alert().triggers;
    expect(evidenceText(ewma, "ms")).toBe("EWMA 1,34 > 1,00");
    expect(evidenceText(pct, "ms")).toBe("Regra PCT-110 221,0 ms > 220,0 ms");
    expect(triggerLabel("RUL_CRITICAL")).toBe("RUL pessimista ≤ 7 dias");
  });
});

describe("alerts ordering", () => {
  it("open before closed, critical first, newest first; counts ignore closed", () => {
    const list = [
      alert({ id: "w", severity: "watch" }),
      alert({ id: "c", severity: "critical", opened_at: "2027-01-01T00:00:00Z" }),
      alert({ id: "x", severity: "critical", state: "closed" }),
      alert({ id: "w2", severity: "warning", opened_at: "2027-01-28T00:00:00Z" }),
      alert({ id: "w3", severity: "warning", opened_at: "2027-01-20T00:00:00Z" }),
    ];
    expect(sortAlerts(list).map((a) => a.id)).toEqual(["c", "w2", "w3", "w", "x"]);
    expect(countBySeverity(list)).toEqual({ watch: 1, warning: 2, critical: 1 });
  });
});

describe("learning", () => {
  it("progress only while learning", () => {
    expect(learningProgress(learningMonitor())).toBeCloseTo(0.6);
    expect(learningProgress(monitor())).toBeNull();
  });
});

describe("chart model", () => {
  it("invalid windows are gaps, the band only after the baseline, the projection ends at L_f on the projected date", () => {
    const m = buildChartModel(series(), rulF1());
    const byI = m.rows;
    expect(m.invalidCount).toBe(1);
    expect(byI.find((r) => r.phase === "warmup")?.band).toBeNull();
    const monitoring = byI.filter((r) => r.phase === "monitoring");
    expect(monitoring.every((r) => r.band?.[0] === 194 && r.band?.[1] === 206)).toBe(true);
    expect(byI.some((r) => r.x === null && r.phase === "monitoring")).toBe(true); // window 9 (invalid) → gap, not 0
    const last = byI[byI.length - 1];
    expect(last.phase).toBe("projection");
    expect(last.proj).toBe(240);
    expect(byI[byI.length - 2].proj).toBe(219.73);
    expect(m.failureLimit).toBe(240);
    expect(m.ruleLines).toEqual([{ value: 220.00000000000003, label: "PCT-110", severity: "warning" }]);
    expect(m.rulBand && m.rulBand.from < m.rulBand.to).toBe(true);
  });
  it("without RUL there is no projection", () => {
    const m = buildChartModel(series(), { rul_days: null, reason: "no_significant_trend" });
    expect(m.rows.some((r) => r.phase === "projection")).toBe(false);
    expect(m.rulBand).toBeNull();
  });
});

describe("nice axis", () => {
  it("round bounds and ticks", async () => {
    const { niceScale } = await import("./predictive");
    expect(niceScale(190.3, 243.7)).toEqual({ domain: [180, 260], ticks: [180, 200, 220, 240, 260] });
    expect(niceScale(0, 62.2, 3).ticks).toEqual([0, 25, 50, 75]);
    expect(niceScale(5, 5).domain[0]).toBeLessThan(5);
  });
});
