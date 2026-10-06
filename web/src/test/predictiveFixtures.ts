/** Predictive fixtures shaped like server/src/api/routes/predictive.py, numbers from scenario F1 (predictive.md). */
import type { FeatureSeries, Monitor, PredictiveAlert } from "@/services/predictiveTypes";

export const F1_TEXT = "Cilindro B3 — tempo de curso 206,5 ms (referência 200,0 ± 2,0 ms desde 14/01): +3,25 σ. EWMA 1,34 > 1,00; CUSUM 6,0 > 5,0. Falha projetada em 26 dias (17–50) ao atingir 240 ms.";

export function rulF1() {
  return {
    rul_days: 11, rul_days_low: 8, rul_days_high: 16, projected_date: "2027-02-09", projected_date_low: "2027-02-06",
    projected_date_high: "2027-02-14", failure_limit: 240, slope: 9.394e-5, slope_se: 1.492e-5, x_fit: 219.73,
    rate_source: "history_14d" as const, today: "2027-01-29",
  };
}

export function alert(over: Partial<PredictiveAlert> = {}): PredictiveAlert {
  return {
    id: "al-1", monitor_id: "mon-1", baseline_id: "b-1", position_id: "POS-B3", position_name: "Cilindro B3",
    machine_id: "m-1", machine_name: "Prensa P-01", line_id: "line-1", line_name: "Linha 1 — Montagem",
    feature: "stroke_time", feature_label: "tempo de curso", unit: "ms", state: "open", severity: "warning",
    severity_label: "Alerta", opened_at: "2027-01-22T12:00:00Z", opened_window: 32,
    triggers: [
      { source: "EWMA", first_i: 30, last_i: 50, first_at: "2027-01-21T00:00:00Z", last_at: "2027-01-29T00:00:00Z", value: 1.34, limit: 1, severity: "watch" },
      { source: "PCT-110", first_i: 40, last_i: 50, first_at: "2027-01-25T00:00:00Z", last_at: "2027-01-29T00:00:00Z", value: 221, limit: 220, severity: "warning" },
    ],
    explanation: F1_TEXT, rul_condition: rulF1(), acknowledged: false, work_order_id: null, work_order_number: null,
    work_order_state: null, resolution: null, closed_at: null, allowed_transitions: ["acknowledged", "closed", "converted"],
    user_allowed_transitions: ["acknowledged", "closed", "converted"],
    history: [{ at: "2027-01-22T12:00:00Z", action: "open", source: "engine", from_state: null, to_state: "open", severity: "watch", resolution: null, comment: null, by: null, by_name: null, work_order_id: null, window: 32 }],
    baseline: { id: "b-1", reset_at: "2027-01-10T00:00:00Z", reset_kind: "initial", warmup: true, reason: null, user_id: null, m0: 200, mad: 1, sigma: 2, sigma_floor: 2, from_i: 5, to_i: 24, n: 20, established_at: "2027-01-14T00:00:00Z", state: "established" },
    ...over,
  };
}

export function monitor(over: Partial<Monitor> = {}): Monitor {
  return {
    id: "mon-1", position_id: "POS-B3", position_name: "Cilindro B3", machine_id: "m-1", machine_name: "Prensa P-01",
    line_id: "line-1", line_name: "Linha 1 — Montagem", feature: "stroke_time", feature_label: "tempo de curso", unit: "ms",
    tag_id: "tag-1", ambient_tag_id: null, direction: "up", window: { cycles: 10000 }, anchor_at: "2027-01-10T00:00:00Z",
    warmup_windows: 5, failure_limit: 1.2, failure_limit_relative: true, failure_limit_value: 240, sensor_resolution: 0,
    params: {}, enabled: true, state: "monitoring", phase: "monitoring", learning: null,
    baseline: alert().baseline ?? null,
    last: { i: 50, end: "2027-01-29T00:00:00Z", x: 220.1, z: 4, ewma: 2.9, cusum: 40.2, slope: 9.394e-5 },
    z: 4, ewma: 2.9, cusum: 40.2, ewma_limit: 1, cusum_h: 5, rul_condition: rulF1(), rul_nominal: null,
    rul_effective: { rul_days: 11, projected_date: "2027-02-09" }, rul_source: "condition",
    open_alert: { id: "al-1", severity: "warning", state: "open" }, processed_until: "2027-01-29T00:00:00Z",
    rules: [{ id: "r-1", monitor_id: "mon-1", code: "PCT-110", type: "pct_of_baseline", value: 1.1, sustained_for: 2, severity: "warning", enabled: true, description: null }],
    ...over,
  };
}

export function learningMonitor(): Monitor {
  return monitor({
    id: "mon-2", position_id: "POS-M1", position_name: "Motor esteira", feature: "current", feature_label: "corrente", unit: "A",
    window: { hours: 8 }, state: "learning", phase: "learning", learning: { windows: 12, required: 20, label: "aprendendo (12/20 janelas)" },
    baseline: null, z: null, ewma: null, cusum: null, rul_condition: { rul_days: null, reason: "baseline_learning" }, open_alert: null,
    last: { i: 13, x: 12.3 }, rules: [],
  });
}

export function series(over: Partial<FeatureSeries> = {}): FeatureSeries {
  const pts = Array.from({ length: 12 }, (_, k) => {
    const i = k + 1;
    const phase = i <= 2 ? "warmup" : i <= 6 ? "baseline" : "monitoring";
    const valid = i !== 9;
    return {
      i, start_at: null, end_at: new Date(Date.UTC(2027, 0, 10 + i)).toISOString(), x: valid ? 200 + Math.max(0, i - 6) * 2 : null, n: 1000,
      valid, u: i * 10000, phase, z: phase === "monitoring" && valid ? Math.min(4, (i - 6)) : null, ewma: phase === "monitoring" && valid ? (i - 6) * 0.3 : null,
      cusum: phase === "monitoring" && valid ? (i - 6) * 1.2 : null, rolling_z: null, anomaly: false, slope: null, triggers: [], severity: null, rul: null, baseline_id: "b-1",
    };
  });
  return {
    monitor_id: "mon-1", position_id: "POS-B3", position_name: "Cilindro B3", feature: "stroke_time", feature_label: "tempo de curso",
    unit: "ms", window: { cycles: 10000 }, baselines: [alert().baseline!],
    bands: [{ baseline_id: "b-1", from_end_at: new Date(Date.UTC(2027, 0, 16)).toISOString(), m0: 200, sigma: 2, low: 194, high: 206 }],
    limits: { failure_limit: 240, ewma_limit: 1, cusum_h: 5, rules: monitor().rules ?? [] },
    points: pts,
    ...over,
  };
}
