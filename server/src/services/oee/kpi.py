"""
KPIs de eficiência e confiabilidade (kpis.md §1, §2.1, §2.3, §2.6) — funções puras.

Toda agregação (turno → dia, máquina → linha → planta) soma **componentes** (`Components`) e
recalcula as razões (§1.6); nunca há média de porcentagens. Tempos em segundos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

# T_req (§2.6) exclui as paradas planejadas não relacionadas ao equipamento
AO_EXCLUDED = frozenset({"PLN-01", "PLN-02", "PLN-04", "PLN-05"})
AO_EQUIPMENT = frozenset({"PLN-03", "MNT-02"})       # + EQP-*

MICRO = "micro_stops"
SPEED = "speed_loss"
QUALITY = "quality_loss"
PSEUDO_LABELS = {MICRO: "Micro-paradas", SPEED: "Perda de velocidade", QUALITY: "Qualidade (refugo)"}


def is_equipment(code: Optional[str]) -> bool:
    return bool(code) and (code.startswith("EQP-") or code in AO_EQUIPMENT)


@dataclass
class Components:
    pot_s: float = 0.0
    pdot_s: float = 0.0
    nodata_s: float = 0.0
    covered_s: float = 0.0                 # POT with data (or a declared stop) — data_coverage
    adot_s: float = 0.0
    calendar_s: float = 0.0
    produced: float = 0.0
    good: float = 0.0
    scrap: float = 0.0
    ideal_produced_s: float = 0.0          # Σ PRI·PQ
    ideal_good_s: float = 0.0              # Σ PRI·GQ
    pri_missing: bool = False              # production of a product without PRI
    product_unknown: bool = False          # production without ProductionRun (default product used)
    # S3-7: parts counted outside every shift instance (not in PQ/GQ nor in the TEEP numerator)
    out_produced: float = 0.0
    out_good: float = 0.0
    out_scrap: float = 0.0
    # D7: an hourly bucket only partly inside the window/schedule was counted pro rata
    counts_prorated: bool = False
    micro_count: int = 0
    micro_s: float = 0.0
    short_faults: int = 0
    ao_excluded_s: float = 0.0             # PLN-01/02/04/05 (incl. shift breaks)
    ao_equipment_s: float = 0.0            # EQP-*, PLN-03, MNT-02
    losses: dict = field(default_factory=dict)   # reason_code -> [seconds, events]

    def add(self, o: "Components") -> "Components":
        for f in ("pot_s", "pdot_s", "nodata_s", "covered_s", "adot_s", "calendar_s", "produced", "good", "scrap",
                  "ideal_produced_s", "ideal_good_s", "micro_count", "micro_s", "short_faults",
                  "ao_excluded_s", "ao_equipment_s", "out_produced", "out_good", "out_scrap"):
            setattr(self, f, getattr(self, f) + getattr(o, f))
        self.pri_missing |= o.pri_missing
        self.product_unknown |= o.product_unknown
        self.counts_prorated |= o.counts_prorated
        for k, (s, n) in o.losses.items():
            cur = self.losses.setdefault(k, [0.0, 0])
            cur[0] += s
            cur[1] += n
        return self

    @property
    def pbt_s(self) -> float:
        return max(self.pot_s - self.pdot_s - self.nodata_s, 0.0)

    @property
    def apt_s(self) -> float:
        return max(self.pbt_s - self.adot_s, 0.0)

    @property
    def speed_loss_s(self) -> float:
        return max(self.apt_s - self.ideal_produced_s - self.micro_s, 0.0)

    @property
    def quality_loss_s(self) -> float:
        return max(self.ideal_produced_s - self.ideal_good_s, 0.0)


def total(items: Iterable[Components]) -> Components:
    out = Components()
    for c in items:
        out.add(c)
    return out


def _div(a: float, b: float) -> Optional[float]:
    return a / b if b > 0 else None


def oee(c: Components) -> dict:
    """§1.2–1.5, §1.7, §1.9. Undefined → None plus `reason`."""
    pbt, apt = c.pbt_s, c.apt_s
    a = _div(apt, pbt)
    reason = None
    if pbt <= 0:
        # scheduled time exists but none of it had data → insufficient_data, not "no planned time"
        reason = "insufficient_data" if (c.pot_s - c.pdot_s) > 0 and c.nodata_s > 0 else "no_planned_time"
    p = q = None
    if c.pri_missing:
        reason = reason or "insufficient_data"
    else:
        p = _div(c.ideal_produced_s, apt)
        q = _div(c.ideal_good_s, c.ideal_produced_s)
        if c.produced <= 0 and pbt > 0 and reason is None:
            reason = "no_production"
    # OEE = Σ PRI·GQ / PBT (§1.5): defined whenever PBT > 0 and every product has a PRI
    # (no production in planned time is a real 0, not undefined).
    o = None if c.pri_missing else _div(c.ideal_good_s, pbt)
    loading = _div(pbt, c.calendar_s)
    # S3-1: TEEP stays defined whenever there is calendar time (0 on a holiday); unknown only when what
    # was produced cannot be valued (no PRI) or the planned time had no data at all
    teep = None if (c.pri_missing or reason == "insufficient_data") else _div(c.ideal_good_s, c.calendar_s)
    return {
        "availability": a, "performance": p, "quality": q, "oee": o,
        "loading": loading, "teep": teep, "reason": reason,
        "ideal_cycle_time_suspect": bool(p is not None and p > 1.0),
        "pot_s": c.pot_s, "pdot_s": c.pdot_s, "pbt_s": pbt, "adot_s": c.adot_s, "apt_s": apt,
        "nodata_s": c.nodata_s,
        # domain.md §4.1: tempo com dados / POT
        "data_coverage": (min(c.covered_s, c.pot_s) / c.pot_s) if c.pot_s > 0 else None,
        "micro_stop_count": c.micro_count, "micro_stop_loss_s": c.micro_s,
        "micro_stops_per_apt_hour": _div(c.micro_count, apt / 3600.0),
        "speed_loss_s": c.speed_loss_s, "quality_loss_s": c.quality_loss_s,
        "ideal_produced_s": c.ideal_produced_s, "net_productive_s": c.ideal_good_s,
        "produced": c.produced, "good": c.good, "scrap": c.scrap,
        "cadence_planned_per_hour": _div(c.produced, pbt / 3600.0),
        "cadence_running_per_hour": _div(c.produced, apt / 3600.0),
        "product_unknown": c.product_unknown,
        "produced_outside_schedule": {"total": c.out_produced, "good": c.out_good, "scrap": c.out_scrap},
        "counts_prorated": c.counts_prorated,
        "availability_operational": availability_operational(c),
    }


def availability_operational(c: Components) -> Optional[float]:
    """§2.6 Ao = (T_req − D_eq) / T_req, T_req = POT − PLN-01/02/04/05 − sem dados."""
    req = c.pot_s - c.ao_excluded_s - c.nodata_s
    return _div(req - c.ao_equipment_s, req)


def pareto(c: Components, meta: dict[str, tuple[str, str, bool]], by: str = "code") -> list[dict]:
    """§1.10: unplanned stop reasons + micro-stops + speed loss + quality loss, descending.
    meta: code -> (category, label, planned). by='category' groups reasons by category."""
    rows: dict[str, dict] = {}
    for code, (secs, n) in c.losses.items():
        cat, label, planned = meta.get(code, (code.split("-")[0], code, False))
        if planned:
            continue
        key = cat if by == "category" else code
        r = rows.setdefault(key, {"reason_code": key if by != "category" else None, "category": cat,
                                  "label": label if by != "category" else cat, "planned": False,
                                  "duration_s": 0.0, "events": 0})
        r["duration_s"] += secs
        r["events"] += n
    for key, secs, n in ((MICRO, c.micro_s, c.micro_count), (SPEED, c.speed_loss_s, 0),
                         (QUALITY, c.quality_loss_s, 0)):
        rows[key] = {"reason_code": key, "category": key, "label": PSEUDO_LABELS[key], "planned": False,
                     "duration_s": secs, "events": n}
    items = [r for r in rows.values() if r["duration_s"] > 0 or r["reason_code"] in (MICRO, SPEED, QUALITY)]
    items.sort(key=lambda r: (-r["duration_s"], r["reason_code"] or r["category"]))
    tot = sum(r["duration_s"] for r in items)
    acc = 0.0
    for r in items:
        acc += r["duration_s"]
        r["pct"] = r["duration_s"] / tot if tot > 0 else None
        r["cumulative_pct"] = acc / tot if tot > 0 else None
    return items


# ── §2 reliability ─────────────────────────────────────────────────────────

COUNTED_SEVERITIES = ("critical", "degraded")


def reliability(operating_s: float, failures: Iterable[tuple[str, Optional[float]]], short_faults: int = 0) -> dict:
    """failures: (severity, restore_duration_s | None). MTBF uses only critical/degraded (§2.1);
    MTTR the restored ones (§2.3, clock time C24c); Ai = MTBF / (MTBF + MTTR) (§2.6)."""
    fl = list(failures)
    counted = [d for sev, d in fl if sev in COUNTED_SEVERITIES]
    n = len(counted)
    incipient = sum(1 for sev, _ in fl if sev == "incipient")
    op_h = operating_s / 3600.0
    mtbf_h = op_h / n if n else None
    restored = [d for d in counted if d is not None]
    mttr_s = sum(restored) / len(restored) if restored else None
    mttr_h = mttr_s / 3600.0 if mttr_s is not None else None
    ai = mtbf_h / (mtbf_h + mttr_h) if (mtbf_h is not None and mttr_h is not None and mtbf_h + mttr_h > 0) else None
    reason = None if n else "no_failures"
    return {"failures": n, "incipient": incipient, "short_faults": short_faults, "operating_h": op_h,
            "mtbf_h": mtbf_h, "mttr_s": mttr_s, "mttr_h": mttr_h, "availability_inherent": ai,
            "failure_rate_per_h": (n / op_h) if op_h > 0 else None, "reason": reason}
