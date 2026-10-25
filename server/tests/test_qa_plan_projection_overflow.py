"""QA regression (Sprint 4 gate): a usage trigger with a tiny recent pace must not crash the plan status.

Reported live by edge-engineer and reproduced by QA: `compute_plan_status` projects `today + timedelta(days=…)`
without a bound, so a 5 000 h plan on a position that barely ran in the last 14 days raises OverflowError
(`date value out of range`). Through the API that is a 500 on GET /api/maintenance-plans/status (for the whole
plant/tenant listing) and a tenant-wide `maintenance.engine.tenant_error`, i.e. no drafts/notifications for ANY
plan of the tenant. Expected: the plan is listed (days_to_due capped or null with a reason, e.g. no_recent_usage)
and the other plans keep working.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from maint_helpers import api, make_maint_tenant, seed_usage
from src.services.maintenance import core


def test_compute_plan_status_tiny_pace_does_not_overflow():
    st = core.compute_plan_status({"hours": 5000}, datetime(2026, 10, 1, tzinfo=timezone.utc), {},
                                  {"hours": 0.01}, {"hours": 0.001}, date(2026, 10, 5), ZoneInfo("America/Manaus"))
    assert st is not None and st.state == "ok"


async def test_plan_status_endpoint_survives_tiny_pace(client):
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position
    ctx = await make_maint_tenant("OVF")
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=ctx.machines["Transportador de Saída"], name="Motor",
                     component_type=ComponentType.electric_motor)
        s.add(p)
        await s.flush()
        b = (now - timedelta(days=1)).replace(minute=0, second=0, microsecond=0).isoformat()
        await seed_usage(s, ctx.id, "position", p.id, {"hours": 0.01}, [(b, {"hours": 0.001})])
        await s.commit()
    h = ctx.h("maintenance_planner")
    await api(client, "POST", "/api/maintenance-plans/", h, 201,
              json={"name": "Lubrificação 5000 h", "wo_type": "preventive", "estimated_hours": 1,
                    "position_id": p.id, "interval_hours": 5000, "reset_on_replacement": True,
                    "task_list": [{"text": "lubrificar"}]})
    await api(client, "POST", "/api/maintenance-plans/", h, 201,
              json={"name": "Inspeção 30 d", "wo_type": "preventive", "estimated_hours": 1,
                    "machine_id": ctx.machines["Estação de Fixação"], "interval_days": 30,
                    "task_list": [{"text": "inspecionar"}]})
    r = await client.get("/api/maintenance-plans/status", headers=h, params={"plant_id": ctx.plant_id})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2
