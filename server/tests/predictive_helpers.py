"""Helpers for the Sprint 5 predictive tests: golden scenario F (docs/scenarios/scenario_f_predictive.json)
as engine inputs (windows, usage, rules)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
F = json.loads((ROOT / "docs/scenarios/scenario_f_predictive.json").read_text(encoding="utf-8"))
X = F["expected"]
TZ = ZoneInfo(F["plant"]["timezone"])
DET = F["detectors"]


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


def f1_windows() -> list[dict]:
    c = F["cases"]["F1"]
    g = c["generator"]
    t0 = ts(c["installed_at"])
    out = []
    for row in c["windows"]:
        i = row["i"]
        out.append({"i": i, "x": row["x"], "u": float(i * c["window"]["cycles"]),
                    "start": t0 + timedelta(hours=g["hours_per_window"] * (i - 1)),
                    "end": t0 + timedelta(hours=g["hours_per_window"] * i), "n": c["window"]["cycles"]})
    return out


def f1_hourly_usage() -> dict[datetime, float]:
    """20 000 cycles/day, continuous (also before the installation: the position kept producing)."""
    c = F["cases"]["F1"]
    per_h = c["generator"]["cycles_per_day"] / 24.0
    t0 = ts(c["installed_at"]) - timedelta(days=30)
    t0 = t0.replace(minute=0, second=0, microsecond=0)
    return {t0 + timedelta(hours=k): per_h for k in range(24 * 120)}


def f2_windows() -> list[dict]:
    c = F["cases"]["F2"]
    d1 = date.fromisoformat(c["first_day"])
    out, u = [], 0.0
    for row in c["windows"]:
        d = d1 + timedelta(days=row["i"] - 1)
        u += row["running_h"]
        start = datetime(d.year, d.month, d.day, tzinfo=TZ)
        out.append({"i": row["i"], "x": row["x"], "u": u, "start": start.astimezone(timezone.utc),
                    "end": (start + timedelta(days=1)).astimezone(timezone.utc), "running_h": row["running_h"]})
    return out


def f2_hourly_usage() -> dict[datetime, float]:
    """Operating hours per UTC hour: the day's running hours spread over its local day (as the oracle); 30 days
    before day 1 at the same rhythm."""
    c = F["cases"]["F2"]
    d1 = date.fromisoformat(c["first_day"])
    per_day = {d1 - timedelta(days=k): c["generator"]["running_hours_per_day"] for k in range(1, 31)}
    for row in c["windows"]:
        per_day[d1 + timedelta(days=row["i"] - 1)] = row["running_h"]
    out = {}
    for d, hrs in per_day.items():
        start = datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc)
        for k in range(24):
            out[start + timedelta(hours=k)] = hrs / 24.0
    return out


def rules_of(case: str) -> list[dict]:
    return F["cases"][case]["rules"]


def f1_reset_at() -> datetime:
    return ts(F["cases"]["F1"]["installed_at"])


def f2_reset_at() -> datetime:
    d1 = date.fromisoformat(F["cases"]["F2"]["first_day"])
    return datetime(d1.year, d1.month, d1.day, tzinfo=TZ).astimezone(timezone.utc)


def f2_close_at() -> datetime:
    """End of the window after which the planner closes the alert (`actions[0].after_window`)."""
    after = F["cases"]["F2"]["actions"][0]["after_window"]
    return next(w["end"] for w in f2_windows() if w["i"] == after)
