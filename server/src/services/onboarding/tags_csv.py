"""Tag CSV import of the onboarding wizard (docs/sprint6.md §4.1, block `s6-tags-csv`).

Preview first (nothing written): one result per CSV line — `ok`, `update` ("já existe — será atualizada") or
`error` with the message. Each row becomes a map-v1 tag entry and goes through the same checks as the edge parser
(edge_rules.tag_error, kept identical to edge/src/plant_map.py by tests), so the messages are the edge's.
Confirm writes the tags (machine/position/role/address/edge fields) and assigns them to the gateway; re-importing
the same file is idempotent (identity = `chave` on the gateway).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Gateway, Line, Machine, Position, Tag
from ...models.tag import DataType, ProtocolType, SemanticRole
from .. import gateways as gs
from . import csvutil
from .edge_rules import tag_error

REQUIRED = ("chave", "linha", "maquina", "papel", "fonte", "endereco")
KNOWN = set(REQUIRED) | {"posicao", "unit_id", "tipo_registro", "tipo_dado", "ordem_palavras", "contador_max",
                         "unidade", "escala", "offset", "intervalo_ms"}
PLC_TYPES = {"uint16", "int16", "uint32", "int32", "float32"}
PROTO = {"opcua": ProtocolType.opc_ua, "modbus": ProtocolType.modbus_tcp}
BOOL_ROLES = {"run_state", "cycle_pulse"}
INT_ROLES = {"cycle_counter", "part_counter_good", "part_counter_scrap", "part_counter_total", "fault_state"}
STATUS_TEXT = {"ok": "ok", "update": "já existe — será atualizada"}


@dataclass
class RowResult:
    line: int
    chave: str
    status: str                       # ok | update | error
    message: str
    tag: Optional[dict] = None        # map-v1 entry (preview)
    _write: Optional[dict] = field(default=None, repr=False)

    def out(self) -> dict:
        return {"line": self.line, "chave": self.chave, "status": self.status, "message": self.message,
                **({"tag": self.tag} if self.tag else {})}


async def _assets(db: AsyncSession, tenant_id: str, plant_id: str):
    lines = (await db.execute(select(Line).where(Line.tenant_id == tenant_id, Line.plant_id == plant_id))).scalars().all()
    by_line = {csvutil.norm(ln.name): ln for ln in lines}
    machines = (await db.execute(select(Machine).where(
        Machine.tenant_id == tenant_id, Machine.line_id.in_([ln.id for ln in lines])))).scalars().all() if lines else []
    by_machine = {(m.line_id, csvutil.norm(m.name)): m for m in machines}
    positions = (await db.execute(select(Position).where(
        Position.tenant_id == tenant_id, Position.machine_id.in_([m.id for m in machines])))).scalars().all() \
        if machines else []
    by_pos = {(p.machine_id, csvutil.norm(p.name)): p for p in positions}
    return by_line, by_machine, by_pos, {m.id for m in machines}


async def existing_by_key(db: AsyncSession, gw: Gateway, machine_ids: set[str]) -> dict[str, Tag]:
    """Tags already imported for this gateway (edge.key), the identity of a re-import."""
    if not machine_ids:
        return {}
    rows = (await db.execute(select(Tag).where(
        Tag.tenant_id == gw.tenant_id, Tag.machine_id.in_(machine_ids), Tag.edge.is_not(None)))).scalars().all()
    out = {}
    for t in rows:
        k = (t.edge or {}).get("key")
        if k and (t.gateway_id in (None, gw.id)):
            out.setdefault(k, t)
    return out


async def preview(db: AsyncSession, gw: Gateway, data) -> dict:
    sources = {d["id"]: d["protocol"] for d in (gw.devices or []) if d.get("id")}
    delim, header, it = csvutil.rows(data, REQUIRED)
    warnings = []
    unknown = [h for h in header if h and h not in KNOWN]
    if unknown:
        warnings.append(f"colunas ignoradas: {', '.join(unknown)}")
    by_line, by_machine, by_pos, mids = await _assets(db, gw.tenant_id, gw.plant_id)
    existing = await existing_by_key(db, gw, mids)
    keys_seen: set[str] = set()
    results: list[RowResult] = []
    for n, r in it:
        results.append(_row(n, r, sources, by_line, by_machine, by_pos, existing, keys_seen))
    summary = {s: sum(1 for x in results if x.status == s) for s in ("ok", "update", "error")}
    return {"gateway_id": gw.id, "delimiter": delim, "rows": [x.out() for x in results], "summary": summary,
            "can_confirm": bool(results) and summary["error"] == 0, "warnings": warnings, "_results": results}


def _err(n: int, chave: str, msg: str) -> RowResult:
    return RowResult(n, chave, "error", f"erro: {msg}")


def _row(n, r, sources, by_line, by_machine, by_pos, existing, keys_seen) -> RowResult:
    chave = r.get("chave", "")
    ln = by_line.get(csvutil.norm(r.get("linha")))
    if ln is None:
        return _err(n, chave, f"linha {r.get('linha')!r} não cadastrada nesta planta (passo 2)")
    m = by_machine.get((ln.id, csvutil.norm(r.get("maquina"))))
    if m is None:
        return _err(n, chave, f"máquina {r.get('maquina')!r} não cadastrada na linha {ln.name!r}")
    pos = None
    if r.get("posicao"):
        pos = by_pos.get((m.id, csvutil.norm(r["posicao"])))
        if pos is None:
            return _err(n, chave, f"posição {r['posicao']!r} não cadastrada na máquina {m.name!r}")
    proto = sources.get(r.get("fonte"))
    entry: dict = {"key": chave, "tag_id": "csv", "semantic_role": r.get("papel") or "", "source": r.get("fonte"),
                   "machine": m.name, "position": pos.name if pos else None, "unit": r.get("unidade") or ""}
    try:
        addr = r.get("endereco", "")
        if proto == "modbus":
            try:
                entry["address"] = int(addr)
            except ValueError:
                entry["address"] = addr
            for col, key, conv in (("unit_id", "unit_id", csvutil.parse_int), ("tipo_registro", "register_type", str),
                                   ("tipo_dado", "data_type_plc", str), ("ordem_palavras", "word_order", str)):
                v = r.get(col, "")
                if v:
                    entry[key] = conv(v)
        else:
            entry["address"] = addr
        cm = r.get("contador_max", "")
        if cm:
            try:
                entry["counter_max"] = int(cm)
            except ValueError:
                entry["counter_max"] = cm
        for col, key, conv in (("escala", "scale", csvutil.parse_float), ("offset", "offset", csvutil.parse_float),
                               ("intervalo_ms", "poll_interval_ms", csvutil.parse_int)):
            v = r.get(col, "")
            if v:
                entry[key] = conv(v)
    except ValueError as e:
        return _err(n, chave, f"valor numérico inválido ({e})")
    msg = tag_error(entry, sources, keys_seen)
    if msg:
        return _err(n, chave, msg)
    if proto == "modbus":
        uid = entry.get("unit_id", 1)
        if not 1 <= int(uid) <= 247:
            return _err(n, chave, f"tag {chave}: unit_id fora de 1–247 ({uid})")
        dt = entry.get("data_type_plc")
        if dt is not None and dt not in PLC_TYPES:
            return _err(n, chave, f"tag {chave}: tipo_dado inválido {dt!r} ({'|'.join(sorted(PLC_TYPES))})")
        wo = entry.get("word_order")
        if wo is not None and wo not in ("big", "little"):
            return _err(n, chave, f"tag {chave}: ordem_palavras inválida {wo!r} (big|little)")
    pim = entry.get("poll_interval_ms")
    if pim is not None and not 10 <= pim <= 3_600_000:
        return _err(n, chave, f"tag {chave}: intervalo_ms fora de 10–3600000 ({pim})")
    role = entry["semantic_role"]
    write = {
        "machine_id": m.id, "position_id": pos.id if pos else None, "name": chave[:255],
        "protocol": PROTO[proto], "address": str(entry["address"]),
        "data_type": DataType.bool if role in BOOL_ROLES else (DataType.int if role in INT_ROLES else DataType.float),
        "unit": (entry.get("unit") or None), "scale_factor": entry.get("scale", 1.0), "offset": entry.get("offset", 0.0),
        "poll_interval_ms": entry.get("poll_interval_ms", 1000), "semantic_role": SemanticRole(role),
        "counter_max": entry.get("counter_max"),
        "edge": {k: v for k, v in {"source": entry["source"], "key": chave, "unit_id": entry.get("unit_id"),
                                    "register_type": entry.get("register_type"),
                                    "data_type_plc": entry.get("data_type_plc"),
                                    "word_order": entry.get("word_order")}.items() if v is not None},
    }
    status = "update" if chave in existing else "ok"
    tag_preview = {k: v for k, v in entry.items() if k != "tag_id"}
    return RowResult(n, chave, status, STATUS_TEXT[status], tag_preview, write)


async def confirm(db: AsyncSession, gw: Gateway, data) -> dict:
    """Writes the preview (only when it has no error). Returns the preview + config_version / warnings."""
    pv = await preview(db, gw, data)
    results: list[RowResult] = pv.pop("_results")
    if not pv["can_confirm"]:
        return {**pv, "confirmed": False}
    _, _, _, mids = await _assets(db, gw.tenant_id, gw.plant_id)
    existing = await existing_by_key(db, gw, mids)
    created = updated = 0
    devices = list(gw.devices or [])
    touched: list[Tag] = []
    for res in results:
        w = dict(res._write)
        tag = existing.get(res.chave)
        if tag is None:
            tag = Tag(tenant_id=gw.tenant_id, is_active=True, **w)
            db.add(tag)
            created += 1
        else:
            for k, v in w.items():
                setattr(tag, k, v)
            tag.is_active = True
            updated += 1
        try:
            tag.edge = gs.validate_tag_edge(tag, dict(w["edge"]), devices)
        except gs.ConfigError as e:          # same rules as the edge; not expected after the preview
            await db.rollback()
            raise ValueError(f"linha {res.line}: {e}")
        tag.gateway_id = gw.id
        touched.append(tag)
    await db.flush()
    # a cycle counter imported for a position becomes that position's counter when it has none (domain.md §3.3)
    from ...models.tag import CYCLE_ROLES
    for t in touched:
        if t.position_id and t.semantic_role in CYCLE_ROLES:
            pos = await db.get(Position, t.position_id)
            if pos is not None and pos.tenant_id == gw.tenant_id and pos.counter_tag_id is None:
                pos.counter_tag_id = t.id
    await db.flush()
    from ..usage.cache import topology_cache
    body, _, warnings = await gs.config_document(db, gw)
    await db.commit()
    topology_cache.invalidate()
    return {**pv, "confirmed": True, "created": created, "updated": updated,
            "config_version": body["config_version"], "config_warnings": warnings}
