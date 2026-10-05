"""Helpers for the Sprint 4 maintenance tests: tenants with the maintenance roles, the golden scenario E
seeded into the database, a fixed clock (scenario `evaluate_at`)."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_E = json.loads((ROOT / "docs/scenarios/scenario_e_maintenance.json").read_text(encoding="utf-8"))
PASSWORD = "Test@12345"

MAINT_ROLES = ("admin", "manager", "maintenance_planner", "technician", "production_supervisor", "operator")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


@dataclass
class MaintTenant:
    id: str
    users: dict = field(default_factory=dict)      # role -> {"id", "token", "email"}
    plant_id: str = ""
    line_id: str = ""
    line2_id: str = ""
    machines: dict = field(default_factory=dict)   # name -> id
    positions: dict = field(default_factory=dict)  # scenario id -> id
    components: dict = field(default_factory=dict)

    def h(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.users[role]['token']}"}


_PW_HASH: list = []


async def add_user(session, tenant_id: str, role: str, label: str, roles=None, line_ids=None, on_call=False) -> dict:
    from src.auth.security import create_access_token, hash_password
    from src.models import User, UserRole
    if not _PW_HASH:
        _PW_HASH.append(hash_password(PASSWORD))
    email = f"{label}.{uuid.uuid4().hex[:8]}@example.com"
    u = User(tenant_id=tenant_id, email=email, password_hash=_PW_HASH[0], name=label,
             role=UserRole(role), roles=sorted(set(roles or [role]) | {role}), line_ids=line_ids or [], on_call=on_call)
    session.add(u)
    await session.flush()
    token = create_access_token({"sub": u.id, "tenant_id": tenant_id, "role": role})
    return {"id": u.id, "token": token, "email": email}


async def make_maint_tenant(label: str = "M", tz: str = "America/Manaus") -> MaintTenant:
    """Tenant with plant, two lines, three machines and one user per role (technician on call for line 1,
    supervisor of line 1)."""
    from src.db import AsyncSessionLocal
    from src.models import Line, Machine, Plant, Tenant
    async with AsyncSessionLocal() as s:
        t = Tenant(name=f"Tenant {label}", slug=f"t-{label.lower()}-{uuid.uuid4().hex[:8]}", max_users=50, max_machines=50)
        s.add(t)
        await s.flush()
        plant = Plant(tenant_id=t.id, name="Planta Manaus", timezone=tz)
        s.add(plant)
        await s.flush()
        line = Line(tenant_id=t.id, plant_id=plant.id, name="Linha de Montagem")
        line2 = Line(tenant_id=t.id, plant_id=plant.id, name="Linha 2")
        s.add_all([line, line2])
        await s.flush()
        ctx = MaintTenant(id=t.id, plant_id=plant.id, line_id=line.id, line2_id=line2.id)
        for name in ("Estação de Fixação", "Transportador de Saída", "Robô de Solda"):
            m = Machine(tenant_id=t.id, line_id=line.id, name=name)
            s.add(m)
            await s.flush()
            ctx.machines[name] = m.id
        m2 = Machine(tenant_id=t.id, line_id=line2.id, name="Prensa L2")
        s.add(m2)
        await s.flush()
        ctx.machines["Prensa L2"] = m2.id
        for role in MAINT_ROLES:
            kw = {}
            if role == "technician":
                kw = {"line_ids": [line.id], "on_call": True}
            if role == "production_supervisor":
                kw = {"line_ids": [line.id]}
            ctx.users[role] = await add_user(s, t.id, role, f"{role}-{label}", **kw)
        await s.commit()
    return ctx


class FixedClock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def scenario_clock(monkeypatch):
    from src.services import clock
    c = FixedClock(ts(SCENARIO_E["evaluate_at"]))
    monkeypatch.setattr(clock, "now", c)
    return c


H = 3600.0


async def seed_usage(session, tenant_id: str, scope: str, entity_id: str, totals: dict, buckets: list[tuple[str, dict]]):
    """totals / bucket values in API units (cycles, hours, starts)."""
    from src.models import UsageBucket, UsageTotal
    stored = {"cycles": "cycles", "hours": "operating_seconds", "starts": "starts"}
    for m, v in totals.items():
        session.add(UsageTotal(scope=scope, entity_id=entity_id, measure=stored[m], tenant_id=tenant_id,
                               value=float(v) * (H if m == "hours" else 1)))
    for b, vals in buckets:
        for m, v in vals.items():
            session.add(UsageBucket(scope=scope, entity_id=entity_id, measure=stored[m], bucket_start=ts(b),
                                    tenant_id=tenant_id, value=float(v) * (H if m == "hours" else 1)))


# Hourly buckets consistent with every number of scenario E (README "Cenário E"):
#  * the 12-month window [2025-12-01 04:00Z, 2026-12-01 04:00Z) per position = window_12m, per component =
#    usage_in_window_12m; * the last 14 days before evaluate_at = usage_last_14_days (bucket 2026-11-25 12:00Z);
#  * POS-A1 after the OS-103 swap (2026-11-12 19:00Z) = 340 000 cycles → position usage at the swap = 7 200 000.
POSITION_BUCKETS = {
    "POS-A1": [("2026-01-10T12:00:00Z", {"cycles": 1600000, "hours": 1400}),
               ("2026-06-01T12:00:00Z", {"cycles": 4060000, "hours": 3500}),
               ("2026-11-14T12:00:00Z", {"cycles": 88000, "hours": 48}),
               ("2026-11-25T12:00:00Z", {"cycles": 252000, "hours": 252})],
    "POS-A2": [("2026-02-01T12:00:00Z", {"cycles": 2800000, "hours": 2400}),
               ("2026-08-01T12:00:00Z", {"cycles": 2948000, "hours": 2548}),
               ("2026-11-25T12:00:00Z", {"cycles": 252000, "hours": 252})],
    "POS-MOT": [("2026-05-01T12:00:00Z", {"hours": 6520, "starts": 30000}),
                ("2026-11-25T12:00:00Z", {"hours": 280, "starts": 1680})],
    "POS-AX1": [("2026-03-01T12:00:00Z", {"hours": 5376, "cycles": 8650000}),
                ("2026-11-25T12:00:00Z", {"hours": 224, "cycles": 350000})],
}
COMPONENT_BUCKETS = {
    "C-A1-1": [("2026-01-10T12:00:00Z", {"cycles": 1600000, "hours": 1400})],
    "C-A1-2": [("2026-06-01T12:00:00Z", {"cycles": 4060000, "hours": 3500})],
    "C-A1-3": [("2026-11-14T12:00:00Z", {"cycles": 88000, "hours": 48}),
               ("2026-11-25T12:00:00Z", {"cycles": 252000, "hours": 252})],
    "C-A2-1": [("2026-02-01T12:00:00Z", {"cycles": 2800000, "hours": 2400})],
    "C-A2-2": [("2026-08-01T12:00:00Z", {"cycles": 2948000, "hours": 2548}),
               ("2026-11-25T12:00:00Z", {"cycles": 252000, "hours": 252})],
    "M-1": [("2026-05-01T12:00:00Z", {"hours": 6520, "starts": 30000}),
            ("2026-11-25T12:00:00Z", {"hours": 280, "starts": 1680})],
    "R-1": [("2026-03-01T12:00:00Z", {"hours": 5376, "cycles": 8650000}),
            ("2026-11-25T12:00:00Z", {"hours": 224, "cycles": 350000})],
}
EXTRA_POSITIONS = {"POS-A3": ("Cilindro A3", "Estação de Fixação", "pneumatic_cylinder"),
                   "POS-A4": ("Cilindro A4", "Estação de Fixação", "pneumatic_cylinder")}


async def seed_scenario_e_assets(ctx: MaintTenant) -> None:
    """Positions, components (C-A1-2 still installed: OS-103 swaps it through the API), usage."""
    from src.db import AsyncSessionLocal
    from src.models import Component, ComponentType, Position, RemovalReason, Tag
    from src.models.tag import ProtocolType, SemanticRole
    E = SCENARIO_E
    async with AsyncSessionLocal() as s:
        specs = {p["id"]: (p["name"], p["machine"], p["component_type"]) for p in E["positions"]}
        specs.update(EXTRA_POSITIONS)
        for pid, (name, machine, ctype) in specs.items():
            p = Position(tenant_id=ctx.id, machine_id=ctx.machines[machine], name=name,
                         component_type=ComponentType(ctype))
            s.add(p)
            await s.flush()
            ctx.positions[pid] = p.id
        # POS-MOT has its own run_state (hours_source = position)
        s.add(Tag(tenant_id=ctx.id, machine_id=ctx.machines["Transportador de Saída"], position_id=ctx.positions["POS-MOT"],
                  name="run motor", protocol=ProtocolType.mqtt, address="mot/run", semantic_role=SemanticRole.run_state))
        for c in E["components"]:
            stock = c["id"] == "C-A1-3"                       # installed by OS-103 through the API
            if c["id"] == "C-A1-2":                           # removed by OS-103 through the API
                installed, removed_at, reason = True, None, None
            else:
                installed = c["removed_at"] is None and not stock
                removed_at = ts(c["removed_at"]) if c["removed_at"] else None
                reason = RemovalReason(c["removal_reason"]) if c["removal_reason"] else None
            comp = Component(tenant_id=ctx.id, component_type=ComponentType(c["component_type"]),
                             manufacturer=c["manufacturer"], model=c["model"], serial_number=c["serial_number"],
                             nominal_life=c["nominal_life"],
                             installed_at=None if stock else ts(c["installed_at"]), removed_at=removed_at,
                             removal_reason=reason,
                             position_id=ctx.positions[c["position_id"]] if installed else None,
                             last_position_id=None if stock else ctx.positions[c["position_id"]])
            s.add(comp)
            await s.flush()
            ctx.components[c["id"]] = comp.id
        for p in E["positions"]:
            await seed_usage(s, ctx.id, "position", ctx.positions[p["id"]], p["usage_total_now"], POSITION_BUCKETS[p["id"]])
        for c in E["components"]:
            await seed_usage(s, ctx.id, "component", ctx.components[c["id"]], c["usage_since_install"],
                             COMPONENT_BUCKETS.get(c["id"], []))
        await s.commit()


async def api(client, method, url, headers, status=200, **kw):
    r = await client.request(method, url, headers=headers, **kw)
    assert r.status_code == status, (method, url, r.status_code, r.text)
    return r.json() if r.content else None
