"""pt-BR presentation of report values (kpis.md S3-5; reports.md §0.2, §0.6).

Fractions → percent with 1 decimal, half-up of the raw value, non-breaking space before '%'; MTBF/MTTR with 1
decimal; counts with thousands separator; durations ≥ 10 min without decimals, < 10 min with 1 decimal.
Undefined → "—" (the reason goes next to it, never 0).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

DASH = "—"
NBSP = " "

REASONS_PT = {
    "no_planned_time": "sem tempo planejado neste período",
    "insufficient_data": "dados insuficientes",
    "no_production": "sem produção",
    "no_output_machine": "linha sem máquina de saída",
    "no_failures": "sem falhas no período",
    "no_restored_failures": "nenhuma falha restabelecida",
    "insufficient_failures": "menos de 3 falhas (Weibull exige r ≥ 3)",
    "degenerate": "dados degenerados (sem solução da verossimilhança)",
    "no_capacity": "capacidade da equipe não cadastrada",
    "no_recent_usage": "sem uso nos últimos 14 dias",
    "no_fit": "sem ajuste de Weibull no grupo",
    "no_usage": "sem uso registrado",
    "no_measure": "tipo sem medida de vida (sem vida nominal)",
    "no_shift": "sem turno no dia",
    "no_nominal_life": "sem vida nominal",
    "forbidden": "sem permissão para esta seção",
    "no_data": "sem dados",
    "singular_information": "matriz de informação singular",
    "snapshot_only": "backlog só existe como fotografia no fechamento do mês (S4-7)",
    "no_decided_alerts": "nenhum alerta com desfecho no período",
    "no_monitored_time": "sem tempo monitorado",
}


def reason_pt(reason: Optional[str]) -> str:
    if not reason:
        return ""
    return REASONS_PT.get(reason, reason)


def _half_up(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.floor(x * f + 0.5) / f


def _group(int_part: str) -> str:
    """Thousands separator '.' like Intl.NumberFormat('pt-BR') in the web app (formatInt)."""
    neg = int_part.startswith("-")
    s = int_part.lstrip("-")
    if len(s) <= 3:
        return int_part
    parts = []
    while s:
        parts.insert(0, s[-3:])
        s = s[:-3]
    return ("-" if neg else "") + ".".join(parts)


def number(x: Optional[float], decimals: int = 1) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return DASH
    v = _half_up(float(x), decimals)
    txt = f"{v:.{decimals}f}"
    if decimals:
        ip, dp = txt.split(".")
        return f"{_group(ip)},{dp}"
    return _group(txt)


def pct(x: Optional[float]) -> str:
    if x is None:
        return DASH
    return f"{number(x * 100, 1)}{NBSP}%"


def pct_plain(x: Optional[float]) -> str:
    """Same as pct with a regular space (subjects/plain-text bodies and tests)."""
    return pct(x).replace(NBSP, " ")


def pp(x: Optional[float]) -> str:
    """Difference of fractions in percentage points, signed."""
    if x is None:
        return DASH
    v = _half_up(x * 100, 1)
    sign = "+" if v > 0 else ""
    return f"{sign}{number(v, 1)}{NBSP}pp"


def count(x: Optional[float]) -> str:
    if x is None:
        return DASH
    return number(round(float(x)), 0)


def minutes(seconds: Optional[float]) -> str:
    if seconds is None:
        return DASH
    m = seconds / 60.0
    return f"{number(m, 0 if m >= 10 else 1)}{NBSP}min"


def hours(h: Optional[float], unit: str = "h") -> str:
    if h is None:
        return DASH
    return f"{number(h, 1)}{NBSP}{unit}"


def with_reason(text: str, reason: Optional[str]) -> str:
    if text == DASH and reason:
        return f"{DASH} ({reason_pt(reason)})"
    return text


def local_dt(ts, tz: ZoneInfo, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if ts is None:
        return DASH
    if isinstance(ts, str):
        ts = parse_ts(ts)
    return ts.astimezone(tz).strftime(fmt)


def parse_ts(s) -> Optional[datetime]:
    if s is None or isinstance(s, datetime):
        return s
    from datetime import timezone
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def flag_min(value: Optional[float], ref: float) -> Optional[str]:
    """Reference mark: green when value ≥ ref, amber below; None when undefined."""
    if value is None:
        return None
    return "green" if value >= ref else "amber"


def flag_range(value: Optional[float], lo: float, hi: float) -> Optional[str]:
    if value is None:
        return None
    return "green" if lo <= value <= hi else "amber"
