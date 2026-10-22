"""Report catalog (docs/reports.md §1 and block `report-catalog`; recipients by default §5.1)."""
from __future__ import annotations

GENERATOR_VERSION = "reports-1.0.0"

# who may generate / download on demand: the screen-equivalent roles (§0.5). admin/superadmin always pass.
CATALOG = [
    {"code": "daily_production", "name": "Produção diária", "title": "Relatório diário de produção",
     "question": "Como foi ontem? Batemos a meta? Onde perdemos?",
     "scope": ["plant"], "period": "productive_day", "formats": ["pdf", "xlsx"], "settle_min": 30,
     "deliver_at_local": "06:30", "weekday": None, "month_day": None,
     "recipients": ["manager", "production_supervisor", "admin"], "area_filter": "user_lines",
     "roles": ["*"], "landscape": False, "screen": "/oee"},
    {"code": "shift", "name": "Relatório de turno", "title": "Relatório de turno",
     "question": "O que aconteceu no meu turno, o que fica para o próximo?",
     "scope": ["line"], "period": "shift_instance", "formats": ["pdf"], "settle_min": 15,
     "deliver_at_local": None, "weekday": None, "month_day": None,
     "recipients": ["production_supervisor"], "area_filter": "report_line",
     "roles": ["*"], "landscape": False, "screen": "/reports/shift"},
    {"code": "weekly_oee", "name": "Revisão semanal de OEE", "title": "Revisão semanal de OEE e perdas",
     "question": "Estamos melhorando? Quais 3 perdas atacar esta semana?",
     "scope": ["plant", "line"], "period": "week", "formats": ["pdf", "xlsx"], "settle_min": 30,
     "deliver_at_local": "07:00", "weekday": 0, "month_day": None,
     "recipients": ["manager", "production_supervisor"], "area_filter": "user_lines",
     "roles": ["*"], "landscape": True, "screen": "/oee"},
    {"code": "monthly_maintenance", "name": "Gestão da manutenção (mensal)",
     "title": "Relatório mensal de gestão da manutenção",
     "question": "Estamos mais preventivos? A equipe dá conta? Quem são os maus atores?",
     "scope": ["plant"], "period": "month", "formats": ["pdf", "xlsx"], "settle_min": 30,
     "deliver_at_local": "07:00", "weekday": None, "month_day": 1,
     "recipients": ["manager", "maintenance_planner", "admin"], "area_filter": "plant",
     "roles": ["maintenance_planner", "manager", "production_supervisor"], "landscape": False,
     "screen": "/maintenance/kpis"},
    {"code": "reliability_life", "name": "Confiabilidade e vida (Weibull)",
     "title": "Confiabilidade e vida por fabricante/modelo (Weibull)",
     "question": "Qual marca/modelo dura mais? A vida nominal é real? Quando trocar?",
     "scope": ["tenant", "plant"], "period": "history_to_quarter_start", "formats": ["pdf", "xlsx"], "settle_min": 30,
     "deliver_at_local": "07:00", "weekday": None, "month_day": None,
     "recipients": ["manager", "maintenance_planner"], "area_filter": "tenant",
     "roles": ["maintenance_planner", "manager", "production_supervisor"], "landscape": True,
     "screen": "/maintenance/reliability"},
]
BY_CODE = {c["code"]: c for c in CATALOG}
CODES = tuple(BY_CODE)


def can_view(user, code: str) -> bool:
    from ...auth import has_any
    roles = BY_CODE[code]["roles"]
    return roles == ["*"] or has_any(user, roles)


def public_entry(c: dict) -> dict:
    return {k: c[k] for k in ("code", "name", "title", "question", "scope", "period", "formats", "settle_min",
                              "deliver_at_local", "weekday", "month_day", "recipients", "landscape", "screen")}
