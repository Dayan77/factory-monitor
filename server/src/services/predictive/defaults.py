"""Default parameters of the predictive module (docs/predictive.md, block `predictive-defaults`).

The test `test_predictive_core.py::test_defaults_match_docs` checks that this dict is exactly the documented block.
"""
from __future__ import annotations

DEFAULTS: dict = {
    "baseline_min_windows": 20,
    "baseline_min_span_h": 24,
    "warmup_windows": {"stroke_time": 5, "current": 1, "vibration": 1, "temperature": 1},
    "window": {"stroke_time": {"cycles": 1000}, "current": {"hours": 8}, "vibration": {"hours": 8},
               "temperature": {"hours": 8}},
    "sigma_floor_frac": 0.01,
    "z_clip": 4.0,
    "ewma_lambda": 0.2,
    "ewma_L": 3.0,
    "cusum_k": 0.5,
    "cusum_h": 5.0,
    "rolling_window": 20,
    "rolling_z_limit": 3.5,
    "anomaly_min": 3,
    "anomaly_of": 10,
    "trend_windows": 10,
    "trend_t_min": 2.0,
    "rul_warning_days": 30,
    "rul_critical_days": 7,
    "sustained_for": 2,
    "auto_close_after_windows": 6,
    "false_alarm_snooze_days": 7,
    "evaluation_horizon_days": 30,
    "min_useful_lead_h": 24,
    "stroke_time_failure_limit_of_baseline": 1.2,
}

FEATURES = ("stroke_time", "current", "vibration", "temperature")
# unit of the usage axis `u` (P-1/P-5): cycles for stroke_time, operating hours for the others
USAGE_MEASURE = {"stroke_time": "cycles", "current": "hours", "vibration": "hours", "temperature": "hours"}
FEATURE_LABEL = {"stroke_time": "tempo de curso", "current": "corrente", "vibration": "vibração",
                 "temperature": "temperatura"}
DEFAULT_UNIT = {"stroke_time": "ms", "current": "A", "vibration": "mm/s", "temperature": "°C"}

# P-10: ISO 14224 failure modes compatible with each signal
COMPATIBLE_MODES = {
    "stroke_time": ("PDE", "INL", "ELP", "DOP"),
    "current": ("OHE", "PDE", "BRD", "FTS"),
    "vibration": ("VIB", "NOI", "BRD"),
    "temperature": ("OHE", "BRD"),
}

SEVERITIES = ("watch", "warning", "critical")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEV_LABEL = {"watch": "Atenção", "warning": "Alerta", "critical": "Crítico"}
STATISTICAL_DETECTORS = ("EWMA", "CUSUM", "ROLLING_Z")
RUL_DETECTORS = ("RUL_WARNING", "RUL_CRITICAL")
ALERT_STATES = ("open", "acknowledged", "converted", "closed")
RESOLUTIONS = ("confirmed", "false_alarm", "failed_before_action", "rebaseline", "duplicate", "known_cause",
               "auto_cleared")
MANUAL_CLOSE_RESOLUTIONS = ("false_alarm", "rebaseline", "duplicate", "known_cause")
# P-10: outcomes that count as false alarm (no compatible failure in H, no confirmed defect)
FALSE_ALARM_RESOLUTIONS = ("false_alarm", "rebaseline", "auto_cleared")
RULE_TYPES = ("threshold", "pct_of_baseline", "rate_of_change")
