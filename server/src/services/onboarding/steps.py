"""Onboarding wizard progress (docs/sprint6.md §4): 9 steps, stored per tenant, each validated before it can be
marked `done`. Nothing is "ready" without step 9 (readiness checks with 15 min of continuous data).

States: pending | in_progress | done. A step that fails its validation is not changed (422 with the errors);
warnings never block (e.g. step 4 without PRI: "OEE ficará indefinido"; step 8 with a single admin).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import available_timezones

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Gateway, Line, Machine, OnboardingProgress, Plant, ReasonCode, Shift, User, UserRole
from ..gateways import config_document
from ..oee.calendar import ShiftDef, shifts_overlap
from ..usage import queries as uq
from . import csvutil, readiness
from .edge_rules import tag_error

STEPS = {
    1: ("company_plant", "Empresa e planta"),
    2: ("lines_machines", "Linhas e máquinas"),
    3: ("shifts_calendar", "Turnos e calendário"),
    4: ("products_cycle_time", "Produtos e tempo de ciclo ideal"),
    5: ("gateway", "Conectar gateway"),
    6: ("tags", "Importar tags"),
    7: ("reasons_fault_codes", "Motivos de parada e códigos de falha"),
    8: ("users_roles", "Usuários e papéis"),
    9: ("readiness", "Verificação \"pronto para produção\""),
}
STATES = ("pending", "in_progress", "done")
STANDARD_PREFIXES = ("EQP-", "SET-", "MAT-", "PLN-")


class StepError(ValueError):
    def __init__(self, errors: list[str], warnings: Optional[list[str]] = None):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.warnings = warnings or []


async def get_progress(db: AsyncSession, tenant_id: str, lock: bool = False) -> OnboardingProgress:
    q = select(OnboardingProgress).where(OnboardingProgress.tenant_id == tenant_id)
    if lock:
        q = q.with_for_update()
    p = (await db.execute(q)).scalar_one_or_none()
    if p is None:
        p = OnboardingProgress(tenant_id=tenant_id, steps={})
        db.add(p)
        await db.flush()
    return p


def progress_out(p: OnboardingProgress) -> dict:
    steps = []
    for n, (key, title) in STEPS.items():
        s = (p.steps or {}).get(str(n)) or {}
        steps.append({"step": n, "key": key, "title": title, "state": s.get("state", "pending"),
                      "data": s.get("data") or {}, "warnings": s.get("warnings") or [], "at": s.get("at"),
                      "by": s.get("by")})
    current = next((s["step"] for s in steps if s["state"] != "done"), None)
    return {"tenant_id": p.tenant_id, "plant_id": p.plant_id, "steps": steps, "current_step": current,
            "ready_at": p.ready_at, "completed": current is None}


async def _plant(db: AsyncSession, tenant_id: str, plant_id: Optional[str]) -> Optional[Plant]:
    if not plant_id:
        return None
    return (await db.execute(select(Plant).where(Plant.id == str(plant_id), Plant.tenant_id == tenant_id))
            ).scalar_one_or_none()


async def validate(db: AsyncSession, user: User, p: OnboardingProgress, step: int, data: dict) -> tuple[dict, list[str]]:
    """Validates step `step` for `done`. Returns (data to store, warnings) or raises StepError."""
    tid = user.tenant_id
    errors: list[str] = []
    warnings: list[str] = []
    data = dict(data or {})
    plant = await _plant(db, tid, data.get("plant_id") or p.plant_id)
    if step == 1:
        tzname = data.get("timezone") or (plant.timezone if plant else None)
        if not tzname or tzname not in available_timezones():
            raise StepError([f"fuso inválido: {tzname!r} (use um nome IANA, ex. America/Sao_Paulo)"])
        if data.get("timezone_confirmed") is not True:
            raise StepError(["confirme o fuso da planta (timezone_confirmed) — nunca o do navegador sem confirmar"])
        if plant is None:
            name = (data.get("name") or "").strip()
            if data.get("plant_id"):
                raise StepError(["planta não encontrada"])
            if not name:
                raise StepError(["informe o nome da planta (ou plant_id de uma planta já criada)"])
            plant = Plant(tenant_id=tid, name=name[:255], timezone=tzname, address=(data.get("address") or None))
            db.add(plant)
            await db.flush()
        else:
            plant.timezone = tzname
            if data.get("address"):
                plant.address = data["address"][:500]
        p.plant_id = plant.id
        return {"plant_id": plant.id, "timezone": tzname, "timezone_confirmed": True,
                "confirmed_at": datetime.now(timezone.utc).isoformat()}, warnings
    if plant is None:
        raise StepError(["conclua o passo 1 (planta e fuso) antes"])
    data["plant_id"] = plant.id
    if step == 2:
        lines = (await db.execute(select(Line).where(Line.tenant_id == tid, Line.plant_id == plant.id))).scalars().all()
        if not lines:
            errors.append("cadastre ao menos uma linha com máquinas")
        for ln in lines:
            if not ln.output_machine_id:
                errors.append(f"linha {ln.name}: defina a máquina de saída")
        names = (await db.execute(select(Machine.name).join(Line, Line.id == Machine.line_id).where(
            Machine.tenant_id == tid, Line.plant_id == plant.id))).scalars().all()
        seen, dups = set(), set()
        for n in names:
            k = csvutil.norm(n)
            (dups if k in seen else seen).add(k)
        if dups:
            errors.append(f"nomes de máquina repetidos na planta: {', '.join(sorted(dups))}")
    elif step == 3:
        shifts = (await db.execute(select(Shift).where(Shift.tenant_id == tid, Shift.plant_id == plant.id,
                                                       Shift.is_active.is_(True)))).scalars().all()
        tz = uq.tzinfo(plant.timezone)
        defs = [ShiftDef(s.id, s.name, s.start_local, s.end_local, tuple(s.weekdays or ()), tuple(s.breaks or ()))
                for s in shifts]
        for i, a in enumerate(defs):
            for b in defs[i + 1:]:
                if shifts_overlap(a, b, tz):
                    errors.append(f"turnos sobrepostos: {a.name} e {b.name} (D9)")
        if not shifts and data.get("operation_24h") is not True:
            errors.append("cadastre ao menos 1 turno ativo ou declare operação 24 h (operation_24h)")
        data["operation_24h"] = bool(data.get("operation_24h")) and not shifts
    elif step == 4:
        rd = await readiness.evaluate(db, tid, plant, progress=p)
        c = next(c for c in rd["checks"] if c["id"] == "ideal_cycle_time_output")
        if not c["ok"]:
            warnings.append(f"{c['detail']} — OEE ficará indefinido")
    elif step == 5:
        gws = (await db.execute(select(Gateway).where(Gateway.tenant_id == tid,
                                                      Gateway.plant_id == plant.id))).scalars().all()
        from ..gateways import effective_status
        now = datetime.now(timezone.utc)
        if not any(effective_status(g, now) == "online" for g in gws):
            errors.append("nenhum gateway online nesta planta: crie o gateway, cadastre-o com o código e aguarde o "
                          "primeiro heartbeat")
    elif step == 6:
        gws = (await db.execute(select(Gateway).where(Gateway.tenant_id == tid,
                                                      Gateway.plant_id == plant.id))).scalars().all()
        if not gws:
            errors.append("nenhum gateway na planta")
        for g in gws:
            body, _, warns = await config_document(db, g)
            pm = body["plant_map"]
            if not pm["tags"]:
                errors.append(f"gateway {g.name}: nenhuma tag atribuída")
            errors += [f"gateway {g.name}: {w}" for w in warns]
            sources = {s["id"]: s["protocol"] for s in pm["sources"]}
            keys: set = set()
            for t in pm["tags"]:
                msg = tag_error(t, sources, keys)
                if msg:
                    errors.append(f"gateway {g.name}: {msg}")
    elif step == 7:
        codes = (await db.execute(select(ReasonCode.code).where(
            ReasonCode.is_active.is_(True), (ReasonCode.tenant_id.is_(None)) | (ReasonCode.tenant_id == tid)))
        ).scalars().all()
        for pre in STANDARD_PREFIXES:
            if not any(c.startswith(pre) for c in codes):
                errors.append(f"catálogo sem os códigos padrão {pre}*")
        rd = await readiness.evaluate(db, tid, plant, progress=p)
        c = next(c for c in rd["checks"] if c["id"] == "fault_code_map")
        if not c["ok"]:
            warnings.append(c["detail"])
    elif step == 8:
        admins = (await db.execute(select(func.count()).select_from(User).where(
            User.tenant_id == tid, User.is_active.is_(True),
            (User.role == UserRole.admin) | User.roles.any("admin")))).scalar_one()
        if admins <= 1:
            warnings.append("recomendado ao menos 1 admin além de quem implantou")
    elif step == 9:
        rd = await readiness.evaluate(db, tid, plant, progress=p)
        bad = [c for c in rd["checks"] if c["required"] and not c["ok"]]
        if bad:
            raise StepError([f"{c['id']}: {c['detail']}" for c in bad], rd["warnings"])
        warnings += rd["warnings"]
        p.ready_at = p.ready_at or datetime.now(timezone.utc)
    if errors:
        raise StepError(errors, warnings)
    return data, warnings


async def put_step(db: AsyncSession, user: User, step: int, state: str, data: Optional[dict]) -> OnboardingProgress:
    p = await get_progress(db, user.tenant_id, lock=True)
    steps = dict(p.steps or {})
    cur = dict(steps.get(str(step)) or {})
    warnings: list[str] = []
    stored = dict(data or cur.get("data") or {})
    if state == "done":
        stored, warnings = await validate(db, user, p, step, stored)
    elif step == 9:
        p.ready_at = None
    if step == 1 and state != "done":
        stored.pop("timezone_confirmed", None)
    cur.update(state=state, data=stored, warnings=warnings, at=datetime.now(timezone.utc).isoformat(), by=user.id)
    steps[str(step)] = cur
    if state != "done" and step != 9 and (steps.get("9") or {}).get("state") == "done":
        steps["9"] = {**steps["9"], "state": "in_progress"}       # a reopened step reopens the final check
        p.ready_at = None
    p.steps = steps
    p.updated_by = user.id
    return p
