/**
 * Predictive maintenance (Sprint 5 — docs/predictive.md P-12). Mirrors server/src/api/routes/predictive.py
 * (monitor_out, alert_out, feature_out, baseline_out, rule_out). Timestamps are ISO-8601 UTC; dates are
 * plant-local YYYY-MM-DD. Nothing here is ever filled with invented values: missing data stays null.
 */

export type Feature = "stroke_time" | "current" | "vibration" | "temperature";
export type PredSeverity = "watch" | "warning" | "critical";
export type AlertState = "open" | "acknowledged" | "converted" | "closed";
export type MonitorState = "learning" | "monitoring" | "disabled";
export type FeaturePhase = "warmup" | "baseline" | "monitoring" | "learning";
export type RuleType = "threshold" | "pct_of_baseline" | "rate_of_change";
export type CloseResolution = "false_alarm" | "rebaseline" | "duplicate" | "known_cause";
export type Resolution = CloseResolution | "confirmed" | "failed_before_action" | "auto_cleared";

/** P-6 condition RUL. `rul_days = null` comes with a `reason` (no_significant_trend, insufficient_data, …). */
export interface ConditionRul {
  rul_days: number | null;
  rul_days_low?: number | null;
  rul_days_high?: number | null;
  projected_date?: string | null;
  projected_date_low?: string | null;
  projected_date_high?: string | null;
  reason?: string | null;
  failure_limit?: number | null;
  slope?: number | null;
  slope_se?: number | null;
  x_fit?: number | null;
  rul_usage?: number | null;
  rul_usage_low?: number | null;
  rul_usage_high?: number | null;
  rate_per_day?: number | null;
  rate_low?: number | null;
  rate_high?: number | null;
  rate_source?: "history_14d" | "production_plan" | null;
  limit_reached?: boolean;
  today?: string | null;
}

export interface Baseline {
  id: string;
  reset_at: string | null;
  reset_kind: string;
  warmup: boolean;
  reason: string | null;
  user_id: string | null;
  m0: number | null;
  mad: number | null;
  sigma: number | null;
  sigma_floor: number | null;
  from_i: number | null;
  to_i: number | null;
  n: number | null;
  established_at: string | null;
  state: "established" | "learning";
}

export interface PredRule {
  id: string;
  monitor_id: string;
  code: string;
  type: RuleType;
  value: number;
  sustained_for: number;
  severity: PredSeverity;
  enabled: boolean;
  description: string | null;
}

export interface MonitorLast {
  i?: number | null;
  end?: string | null;
  x?: number | null;
  z?: number | null;
  ewma?: number | null;
  cusum?: number | null;
  rolling_z?: number | null;
  slope?: number | null;
  triggers?: string[] | null;
  phase?: string | null;
}

export interface NominalRul {
  component_id: string;
  rul_days: number | null;
  measure: string | null;
  reason: string | null;
  projected_date: string | null;
  life_consumed_pct: number | null;
}

export interface Monitor {
  id: string;
  position_id: string;
  position_name: string | null;
  machine_id: string;
  machine_name: string | null;
  line_id?: string | null;
  line_name?: string | null;
  feature: Feature;
  feature_label: string | null;
  unit: string;
  tag_id: string | null;
  ambient_tag_id: string | null;
  direction: "up" | "down";
  window: { cycles?: number | null; hours?: number | null };
  anchor_at: string | null;
  warmup_windows: number;
  failure_limit: number | null;
  failure_limit_relative: boolean;
  /** Failure limit in signal units (relative limits resolved against the current m0). */
  failure_limit_value: number | null;
  sensor_resolution: number;
  params: Record<string, unknown>;
  enabled: boolean;
  state: MonitorState;
  phase: string;
  learning: { windows: number; required: number; label: string } | null;
  baseline: Baseline | null;
  last: MonitorLast | null;
  z: number | null;
  ewma: number | null;
  cusum: number | null;
  ewma_limit: number | null;
  cusum_h: number | null;
  rul_condition: ConditionRul | null;
  rul_nominal: NominalRul | null;
  rul_effective: { rul_days: number | null; projected_date: string | null } | null;
  rul_source: "nominal" | "condition" | null;
  open_alert: { id: string; severity: PredSeverity; state: AlertState } | null;
  processed_until: string | null;
  rules?: PredRule[];
  baselines?: Baseline[];
}

export interface MonitorQuery {
  plant_id?: string;
  line_id?: string;
  machine_id?: string;
  position_id?: string;
  feature?: Feature;
}

export interface MonitorCreateBody {
  position_id: string;
  feature: Feature;
  tag_id?: string | null;
  direction?: "up" | "down";
  window_cycles?: number | null;
  window_hours?: number | null;
  warmup_windows?: number | null;
  failure_limit?: number | null;
  failure_limit_relative?: boolean | null;
  sensor_resolution?: number;
  unit?: string | null;
  enabled?: boolean;
  default_rules?: boolean;
}

export type MonitorPatchBody = Partial<Omit<MonitorCreateBody, "position_id" | "feature" | "default_rules">> & {
  reason?: string | null;
};

export interface RuleCreateBody {
  monitor_id: string;
  type: RuleType;
  value: number;
  sustained_for?: number | null;
  severity: PredSeverity;
  enabled?: boolean;
  description?: string | null;
}

export type RulePatchBody = Partial<Pick<PredRule, "value" | "sustained_for" | "severity" | "enabled" | "description">>;

/** One window of the condition series (feature_out). Invalid windows have x = null and no detector values. */
export interface FeaturePoint {
  i: number;
  start_at: string | null;
  end_at: string;
  x: number | null;
  n: number | null;
  valid: boolean;
  u: number | null;
  running_h?: number | null;
  coverage?: number | null;
  phase: string | null;
  z: number | null;
  ewma: number | null;
  cusum: number | null;
  rolling_z: number | null;
  anomaly: boolean | null;
  slope: number | null;
  triggers: string[];
  severity: PredSeverity | null;
  rul: ConditionRul | null;
  baseline_id: string | null;
}

export interface BaselineBand {
  baseline_id: string;
  from_end_at: string | null;
  m0: number;
  sigma: number;
  low: number;
  high: number;
}

export interface FeatureSeries {
  monitor_id: string;
  position_id: string;
  position_name: string | null;
  feature: Feature;
  feature_label: string | null;
  unit: string;
  window: { cycles?: number | null; hours?: number | null };
  baselines: Baseline[];
  bands: BaselineBand[];
  limits: { failure_limit: number | null; ewma_limit: number | null; cusum_h: number | null; rules: PredRule[] };
  points: FeaturePoint[];
}

export interface FeaturesResponse {
  position_id: string | null;
  series: FeatureSeries[];
}

export interface FeatureQuery {
  position_id?: string;
  monitor_id?: string;
  feature?: Feature;
  start?: string;
  stop?: string;
}

/** Evidence of an alert: one entry per detector/rule that fired (P-8 "triggers"). */
export interface AlertTrigger {
  source: string;
  first_i: number | null;
  last_i: number | null;
  first_at: string | null;
  last_at: string | null;
  value: number | null;
  limit: number | null;
  severity: PredSeverity | null;
}

export interface AlertHistoryEntry {
  at: string | null;
  action: string;
  source: string;
  from_state: AlertState | null;
  to_state: AlertState | null;
  severity: PredSeverity | null;
  resolution: Resolution | null;
  comment: string | null;
  by: string | null;
  by_name: string | null;
  work_order_id: string | null;
  window: number | null;
}

export interface PredictiveAlert {
  id: string;
  monitor_id: string;
  baseline_id: string | null;
  position_id: string;
  position_name: string | null;
  machine_id: string;
  machine_name: string | null;
  line_id: string | null;
  line_name: string | null;
  feature: Feature;
  feature_label: string | null;
  unit: string | null;
  state: AlertState;
  severity: PredSeverity;
  severity_label: string;
  opened_at: string;
  opened_window: number | null;
  first_triggers?: string[] | null;
  escalations?: Record<string, string> | null;
  triggers: AlertTrigger[];
  /** Server-generated explanation in pt-BR (P-8). Shown verbatim. */
  explanation: string | null;
  rul_condition: ConditionRul | null;
  acknowledged: boolean;
  work_order_id: string | null;
  work_order_number: string | null;
  work_order_state: string | null;
  resolution: Resolution | null;
  closed_at: string | null;
  allowed_transitions: (AlertState)[];
  user_allowed_transitions: (AlertState)[] | null;
  history?: AlertHistoryEntry[];
  last_point?: Record<string, unknown> | null;
  baseline?: Baseline | null;
}

export interface AlertQuery {
  state?: AlertState[];
  severity?: PredSeverity;
  line_id?: string;
  position_id?: string;
  machine_id?: string;
  feature?: Feature;
}

export interface TransitionAlertBody {
  to: "acknowledged" | "closed";
  resolution?: CloseResolution | null;
  comment?: string | null;
}

export interface ConvertAlertBody {
  estimated_hours?: number | null;
  note?: string | null;
}

/** GET /api/predictive/evaluation (P-10). Fields are read defensively: the evaluator may add more. */
export type Evaluation = Record<string, unknown>;

/** WebSocket message {type: 'predictive_alert', event, alert}. */
export interface PredictiveAlertMessage {
  type: "predictive_alert";
  event: string | null;
  alert: Pick<PredictiveAlert, "id" | "severity" | "state" | "position_id" | "machine_id"> & Partial<PredictiveAlert>;
}
