"""Onboarding step 9 — "pronto para produção" checklist (docs/sprint6.md §4, block `s6-readiness-checks`).

Every check answers {id, required, ok, status: ok | fail | warn, detail, items}. The plant is ready when every
required check is ok; `data_flowing` itself needs settings.readiness_window_min (15) minutes of continuous data:
for every required tag (the run_state / counters that make `run_state_or_cycles` and `part_counter_output`
pass), each of the consecutive machine_offline_timeout_s windows of the last 15 min has at least one reading in
InfluxDB (a gap longer than the offline timeout breaks continuity — the machine would show as offline).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import (Gateway, Line, Machine, MachineProduct, OnboardingProgress, Plant, Shift, Tag, User,
                       UserRole)
from ...models.tag import COUNTER_ROLES, CYCLE_ROLES, PART_ROLES, SemanticRole
from ..gateways import effective_status
from ..influx import influx
from ..validation import ensure_uuid

_DOC = Path(__file__).resolve().parents[4] / "docs" / "sprint6.md"
# ids/required of the spec block (the server keeps its own copy: the docs are not in the image; a test checks both)
CHECKS = [
    ("timezone_confirmed", True), ("output_machine_per_line", True), ("calendar", True),
    ("ideal_cycle_time_output", True), ("gateway_online", True), ("run_state_or_cycles", True),
    ("part_counter_output", True), ("data_flowing", True), ("counter_max_set", False),
    ("fault_code_map", False), ("second_admin", False),
]
RUN_OR_CYCLE = {SemanticRole.run_state} | set(CYCLE_ROLES) | set(PART_ROLES)
OUTPUT_PART = {SemanticRole.part_counter_good, SemanticRole.part_counter_total}


def _check(cid: str, required: bool, ok: bool, detail: str, items: Optional[list] = None, fix: Optional[str] = None):
    return {"id": cid, "required": required, "ok": ok, "status": "ok" if ok else ("fail" if required else "warn"),
            "detail": detail, "items": items or [], "fix_link": None if ok else fix}


async def continuous_tags(tenant_id: str, tag_ids: list[str], now: datetime, window_min: int,
                          step_s: int) -> dict[str, bool]:
    """tag_id → True when every `step_s` window of the last `window_min` minutes has ≥ 1 reading."""
    if not tag_ids:
        return {}
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    ids = [ensure_uuid(t, "tag_id") for t in tag_ids]
    start = now - timedelta(minutes=window_min)
    n_windows = max(1, int(window_min * 60 // step_s))
    start = now - timedelta(seconds=n_windows * step_s)
    flt = " or ".join(f'r["tag_id"] == "{t}"' for t in ids)
    flux = f"""
    from(bucket: "{settings.influx_bucket}")
      |> range(start: {start.astimezone(timezone.utc).isoformat()}, stop: {now.astimezone(timezone.utc).isoformat()})
      |> filter(fn: (r) => r["_measurement"] == "sensors" and r["_field"] == "value")
      |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
      |> filter(fn: (r) => {flt})
      |> keep(columns: ["_time", "tag_id"])
    """
    tables = await influx._client.query_api().query(flux, org=settings.influx_org)
    seen: dict[str, set[int]] = {t: set() for t in ids}
    for table in tables:
        for rec in table.records:
            tid = rec.values.get("tag_id")
            if tid in seen:
                k = int((rec.get_time() - start).total_seconds() // step_s)
                if 0 <= k < n_windows:
                    seen[tid].add(k)
    return {t: len(s) == n_windows for t, s in seen.items()}


async def evaluate(db: AsyncSession, tenant_id: str, plant: Plant, now: Optional[datetime] = None,
                   progress: Optional[OnboardingProgress] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if progress is None:
        progress = await db.get(OnboardingProgress, tenant_id)
    steps = (progress.steps if progress else {}) or {}
    s1 = (steps.get("1") or {}).get("data") or {}
    s3 = (steps.get("3") or {}).get("data") or {}
    out = []

    tz_ok = bool(s1.get("timezone_confirmed")) and s1.get("plant_id") == plant.id and \
        s1.get("timezone") == plant.timezone
    out.append(_check("timezone_confirmed", True, tz_ok,
                      f"fuso {plant.timezone} confirmado" if tz_ok else
                      f"confirme o fuso da planta ({plant.timezone}) no passo 1", fix="/onboarding/1"))

    lines = list((await db.execute(select(Line).where(Line.tenant_id == tenant_id, Line.plant_id == plant.id)
                                   .order_by(Line.created_at))).scalars())
    machines = list((await db.execute(select(Machine).where(
        Machine.tenant_id == tenant_id, Machine.line_id.in_([ln.id for ln in lines])))).scalars()) if lines else []
    no_out = [ln.name for ln in lines if not ln.output_machine_id]
    out.append(_check("output_machine_per_line", True, bool(lines) and not no_out,
                      "nenhuma linha cadastrada" if not lines else
                      (f"{len(lines)} linha(s) com máquina de saída" if not no_out else
                       f"linhas sem máquina de saída: {', '.join(no_out)}"), no_out, fix="/onboarding/2"))

    n_shifts = (await db.execute(select(func.count()).select_from(Shift).where(
        Shift.tenant_id == tenant_id, Shift.plant_id == plant.id, Shift.is_active.is_(True)))).scalar_one()
    op24 = bool(s3.get("operation_24h"))
    out.append(_check("calendar", True, n_shifts > 0 or op24,
                      f"{n_shifts} turno(s) ativo(s)" if n_shifts else
                      ("operação 24 h declarada" if op24 else "cadastre ao menos 1 turno ou declare operação 24 h"),
                      fix="/onboarding/3"))

    out_ids = [ln.output_machine_id for ln in lines if ln.output_machine_id]
    pri = {}
    if out_ids:
        for mid, ict in (await db.execute(select(MachineProduct.machine_id, MachineProduct.ideal_cycle_time_s).where(
                MachineProduct.tenant_id == tenant_id, MachineProduct.machine_id.in_(out_ids),
                MachineProduct.is_default.is_(True)))).all():
            pri[mid] = ict
    mname = {m.id: m.name for m in machines}
    no_pri = [mname.get(mid, mid) for mid in out_ids if not pri.get(mid)]
    out.append(_check("ideal_cycle_time_output", True, bool(out_ids) and not no_pri,
                      "sem máquina de saída" if not out_ids else
                      ("PRI do produto padrão cadastrado nas máquinas de saída" if not no_pri else
                       f"máquina de saída sem PRI do produto padrão (OEE ficará indefinido): {', '.join(no_pri)}"),
                      no_pri, fix="/onboarding/4"))

    gws = list((await db.execute(select(Gateway).where(Gateway.tenant_id == tenant_id,
                                                       Gateway.plant_id == plant.id))).scalars())
    gw_bad = [f"{g.name} ({effective_status(g, now)})" for g in gws if effective_status(g, now) != "online"]
    out.append(_check("gateway_online", True, bool(gws) and not gw_bad,
                      "nenhum gateway na planta" if not gws else
                      (f"{len(gws)} gateway(s) online" if not gw_bad else f"gateways fora do ar: {', '.join(gw_bad)}"),
                      gw_bad, fix="/onboarding/5"))

    tags = list((await db.execute(select(Tag).where(
        Tag.tenant_id == tenant_id, Tag.is_active.is_(True),
        Tag.machine_id.in_([m.id for m in machines])))).scalars()) if machines else []
    by_m: dict[str, list[Tag]] = {}
    for t in tags:
        by_m.setdefault(t.machine_id, []).append(t)
    missing_run = [m.name for m in machines if not any(t.semantic_role in RUN_OR_CYCLE for t in by_m.get(m.id, []))]
    out.append(_check("run_state_or_cycles", True, bool(machines) and not missing_run,
                      "nenhuma máquina" if not machines else
                      ("toda máquina com run_state ou contador" if not missing_run else
                       f"máquinas sem run_state nem contador de ciclos/peças: {', '.join(missing_run)}"),
                      missing_run, fix="/onboarding/6"))

    no_part = [mname.get(mid, mid) for mid in out_ids
               if not any(t.semantic_role in OUTPUT_PART for t in by_m.get(mid, []))]
    out.append(_check("part_counter_output", True, bool(out_ids) and not no_part,
                      "sem máquina de saída" if not out_ids else
                      ("máquinas de saída com contador de peças" if not no_part else
                       f"máquina de saída sem part_counter_good/total: {', '.join(no_part)}"),
                      no_part, fix="/onboarding/6"))

    # required tags: what makes the two checks above pass
    required: dict[str, Tag] = {}
    for m in machines:
        ts = by_m.get(m.id, [])
        for t in ts:
            if t.semantic_role == SemanticRole.run_state or t.semantic_role in CYCLE_ROLES or \
                    (t.semantic_role in PART_ROLES and m.id in out_ids):
                required[t.id] = t
        if not any(t.id in required for t in ts):
            for t in ts:
                if t.semantic_role in RUN_OR_CYCLE:
                    required[t.id] = t
    window = settings.readiness_window_min
    step = max(10, int(settings.machine_offline_timeout_s))
    flowing, not_flowing, influx_error = {}, [], None
    if required:
        try:
            flowing = await continuous_tags(tenant_id, list(required), now, window, step)
        except Exception as e:  # noqa: BLE001 — Influx down: the check fails, it never passes by default
            influx_error = str(e)[:200]
    not_flowing = [f"{mname.get(t.machine_id, '?')} / {t.name}" for tid, t in required.items() if not flowing.get(tid)]
    ok_flow = bool(required) and not not_flowing and influx_error is None
    out.append(_check("data_flowing", True, ok_flow,
                      ("nenhuma tag obrigatória" if not required else
                       f"leituras contínuas nos últimos {window} min em {len(required)} tag(s)") if ok_flow or not required
                      else (f"sem dados contínuos nos últimos {window} min: {', '.join(not_flowing[:20])}"
                            if influx_error is None else f"não foi possível consultar as leituras: {influx_error}"),
                      not_flowing, fix="/onboarding/6"))

    no_max = [f"{mname.get(t.machine_id, '?')} / {t.name}" for t in tags
              if t.semantic_role in COUNTER_ROLES and t.semantic_role != SemanticRole.cycle_pulse and not t.counter_max]
    out.append(_check("counter_max_set", False, not no_max,
                      "contadores com counter_max" if not no_max else
                      f"contadores sem counter_max (rollover C5): {', '.join(no_max[:20])}", no_max))
    fault_ms = {t.machine_id for t in tags if t.semantic_role == SemanticRole.fault_state}
    no_map = [m.name for m in machines if m.id in fault_ms and not (m.fault_code_map or {})]
    out.append(_check("fault_code_map", False, not no_map,
                      "máquinas com fault_state têm fault_code_map" if not no_map else
                      f"sem fault_code_map (paradas por falha viram EQP-01): {', '.join(no_map)}", no_map,
                      fix="/onboarding/7"))
    admins = (await db.execute(select(func.count()).select_from(User).where(
        User.tenant_id == tenant_id, User.is_active.is_(True),
        (User.role == UserRole.admin) | User.roles.any("admin")))).scalar_one()
    out.append(_check("second_admin", False, admins > 1,
                      f"{admins} administrador(es) ativo(s)" + ("" if admins > 1 else
                                                                 " — recomendado ao menos 2"), fix="/onboarding/8"))
    ready = all(c["ok"] for c in out if c["required"])
    return {"plant_id": plant.id, "checked_at": now, "ready": ready, "window_min": window,
            "checks": out, "warnings": [c["detail"] for c in out if c["status"] == "warn"]}


def spec_checks() -> Optional[list[dict]]:
    """The `s6-readiness-checks` block of docs/sprint6.md when the docs are available (tests)."""
    import re
    if not _DOC.exists():
        return None
    m = re.search(r"```json s6-readiness-checks\n(.*?)\n```", _DOC.read_text(encoding="utf-8"), re.S)
    return json.loads(m.group(1)) if m else None
