"""Asset CSV import of the onboarding wizard (docs/sprint6.md §4 step 2): lines, machines, output machine,
positions and components. Preview first (nothing written), then confirm; idempotent (matched by name inside the
parent — line in the plant, machine in the line, position in the machine — and components by serial number).

Columns (`;` or `,`, UTF-8; names matched without accent/case):
  linha*, maquina*, modelo, serie, saida (sim/não: output machine of the line), posicao, tipo (component type:
  pneumatic_cylinder, electric_motor, servo, robot_axis, valve, gripper, bearing, belt, pump, spindle, other),
  fabricante, modelo_componente, serie_componente, vida_ciclos, vida_horas, vida_partidas, ciclos_por_peca
One row per position (or one row with an empty `posicao` for a machine without positions).
Rules: machine names are unique in the plant; at most one output machine per line.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Component, ComponentType, Line, Machine, Plant, Position
from . import csvutil

REQUIRED = ("linha", "maquina")
TYPE_ALIASES = {"servo_motor": "servo", "cilindro": "pneumatic_cylinder", "motor": "electric_motor",
                "rolamento": "bearing", "valvula": "valve", "correia": "belt", "bomba": "pump",
                "eixo_arvore": "spindle", "garra": "gripper", "eixo_robo": "robot_axis", "outro": "other"}
YES = {"sim", "s", "yes", "y", "1", "x", "true", "verdadeiro"}
NO = {"", "nao", "n", "no", "0", "false", "falso"}
TEMPLATE = ("linha;maquina;modelo;serie;saida;posicao;tipo;fabricante;modelo_componente;serie_componente;"
            "vida_ciclos;vida_horas;vida_partidas;ciclos_por_peca\n"
            "Linha de Montagem;Estação de Fixação;Dispositivo;VP-FIX;sim;Cilindro A1;pneumatic_cylinder;Festo;"
            "DSBC;CIL-A1-0001;8000000;;;1\n")


def _ctype(v: str):
    k = csvutil.norm(v).replace(" ", "_").replace("-", "_")
    k = TYPE_ALIASES.get(k, k)
    return ComponentType(k)


async def _plan(db: AsyncSession, tenant_id: str, plant: Plant, data) -> tuple[list[dict], list[dict]]:
    delim, header, it = csvutil.rows(data, REQUIRED)
    lines = {csvutil.norm(ln.name): ln for ln in (await db.execute(select(Line).where(
        Line.tenant_id == tenant_id, Line.plant_id == plant.id))).scalars()}
    machines = (await db.execute(select(Machine).where(
        Machine.tenant_id == tenant_id, Machine.line_id.in_([ln.id for ln in lines.values()])))).scalars().all() \
        if lines else []
    m_by_name = {csvutil.norm(m.name): m for m in machines}
    line_name = {ln.id: ln.name for ln in lines.values()}
    positions = {(p.machine_id, csvutil.norm(p.name)): p for p in (await db.execute(select(Position).where(
        Position.tenant_id == tenant_id, Position.machine_id.in_([m.id for m in machines])))).scalars()} \
        if machines else {}
    file_machine_line: dict[str, str] = {}
    file_output: dict[str, str] = {}
    results, ops = [], []
    for n, r in it:
        ln_key, m_key = csvutil.norm(r.get("linha")), csvutil.norm(r.get("maquina"))
        res = {"line": n, "linha": r.get("linha"), "maquina": r.get("maquina"), "posicao": r.get("posicao") or None}

        def err(msg):
            res.update(status="error", message=f"erro: {msg}")
            results.append(res)
        if not ln_key or not m_key:
            err("linha e maquina são obrigatórias")
            continue
        prev_line = file_machine_line.get(m_key)
        if prev_line is not None and prev_line != ln_key:
            err(f"máquina {r['maquina']!r} aparece em duas linhas do arquivo (nomes únicos por planta)")
            continue
        ex_m = m_by_name.get(m_key)
        ex_line = lines.get(ln_key)
        if ex_m is not None and (ex_line is None or ex_m.line_id != ex_line.id):
            err(f"máquina {r['maquina']!r} já existe na linha {line_name.get(ex_m.line_id)!r} (nomes únicos por planta)")
            continue
        out = csvutil.norm(r.get("saida"))
        if out not in YES | NO:
            err(f"saida deve ser sim ou não ({r.get('saida')!r})")
            continue
        if out in YES:
            if file_output.get(ln_key, m_key) != m_key:
                err(f"linha {r['linha']!r} com mais de uma máquina de saída no arquivo")
                continue
            file_output[ln_key] = m_key
        try:
            ctype = _ctype(r["tipo"]) if r.get("tipo") else None
            life = {k: v for k, v in (("cycles", csvutil.parse_int(r.get("vida_ciclos", ""))),
                                      ("hours", csvutil.parse_float(r.get("vida_horas", ""))),
                                      ("starts", csvutil.parse_int(r.get("vida_partidas", "")))) if v}
            cpp = csvutil.parse_int(r.get("ciclos_por_peca", "")) or 1
        except ValueError as e:
            err(f"valor inválido ({e})")
            continue
        if r.get("posicao") and ctype is None:
            err("posição exige o tipo do componente (coluna tipo)")
            continue
        if cpp < 1:
            err("ciclos_por_peca deve ser ≥ 1")
            continue
        file_machine_line[m_key] = ln_key
        new_parts = []
        if ex_line is None:
            new_parts.append("linha")
        if ex_m is None:
            new_parts.append("máquina")
        pos_ex = positions.get((ex_m.id, csvutil.norm(r["posicao"]))) if ex_m and r.get("posicao") else None
        if r.get("posicao") and pos_ex is None:
            new_parts.append("posição")
        if r.get("serie_componente"):
            new_parts.append("componente")
        status = "ok" if new_parts else "update"
        res.update(status=status, message=("criar: " + ", ".join(new_parts)) if new_parts
                   else "já existe — será atualizada")
        results.append(res)
        ops.append({"line": r["linha"].strip(), "ln_key": ln_key, "machine": r["maquina"].strip(), "m_key": m_key,
                    "model": r.get("modelo") or None, "serial": r.get("serie") or None, "output": out in YES,
                    "position": (r.get("posicao") or "").strip() or None, "ctype": ctype, "cpp": cpp,
                    "manufacturer": r.get("fabricante") or None, "cmodel": r.get("modelo_componente") or None,
                    "cserial": r.get("serie_componente") or None, "life": life})
    return results, ops


async def preview(db: AsyncSession, tenant_id: str, plant: Plant, data) -> dict:
    results, _ = await _plan(db, tenant_id, plant, data)
    summary = {s: sum(1 for x in results if x["status"] == s) for s in ("ok", "update", "error")}
    return {"plant_id": plant.id, "rows": results, "summary": summary,
            "can_confirm": bool(results) and summary["error"] == 0}


async def confirm(db: AsyncSession, tenant_id: str, plant: Plant, data) -> dict:
    pv = await preview(db, tenant_id, plant, data)
    if not pv["can_confirm"]:
        return {**pv, "confirmed": False}
    _, ops = await _plan(db, tenant_id, plant, data)
    created = {"lines": 0, "machines": 0, "positions": 0, "components": 0}
    lines = {csvutil.norm(ln.name): ln for ln in (await db.execute(select(Line).where(
        Line.tenant_id == tenant_id, Line.plant_id == plant.id))).scalars()}
    now = datetime.now(timezone.utc)
    for op in ops:
        ln = lines.get(op["ln_key"])
        if ln is None:
            ln = Line(tenant_id=tenant_id, plant_id=plant.id, name=op["line"])
            db.add(ln)
            await db.flush()
            lines[op["ln_key"]] = ln
            created["lines"] += 1
        m = next((x for x in (await db.execute(select(Machine).where(
            Machine.tenant_id == tenant_id, Machine.line_id == ln.id))).scalars()
            if csvutil.norm(x.name) == op["m_key"]), None)
        if m is None:
            m = Machine(tenant_id=tenant_id, line_id=ln.id, name=op["machine"], model=op["model"],
                        serial_number=op["serial"])
            db.add(m)
            await db.flush()
            created["machines"] += 1
        else:
            m.model = op["model"] or m.model
            m.serial_number = op["serial"] or m.serial_number
        if op["output"]:
            ln.output_machine_id = m.id
        if op["position"]:
            pos = next((p for p in (await db.execute(select(Position).where(
                Position.tenant_id == tenant_id, Position.machine_id == m.id))).scalars()
                if csvutil.norm(p.name) == csvutil.norm(op["position"])), None)
            if pos is None:
                pos = Position(tenant_id=tenant_id, machine_id=m.id, name=op["position"], component_type=op["ctype"],
                               cycles_per_part=op["cpp"])
                db.add(pos)
                await db.flush()
                created["positions"] += 1
            if op["cserial"]:
                comp = (await db.execute(select(Component).where(
                    Component.tenant_id == tenant_id, Component.serial_number == op["cserial"]))).scalars().first()
                if comp is None:
                    comp = Component(tenant_id=tenant_id, component_type=op["ctype"], manufacturer=op["manufacturer"],
                                     model=op["cmodel"], serial_number=op["cserial"], nominal_life=op["life"])
                    db.add(comp)
                    await db.flush()
                    created["components"] += 1
                occupied = (await db.execute(select(Component.id).where(
                    Component.position_id == pos.id, Component.removed_at.is_(None)))).scalar_one_or_none()
                if comp.position_id is None and comp.removed_at is None and occupied is None:
                    comp.position_id = comp.last_position_id = pos.id
                    comp.installed_at = now
    await db.commit()
    return {**pv, "confirmed": True, "created": created}
