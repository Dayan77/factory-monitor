"""
Asset topology snapshot + attribution of usage deltas to accumulators (domain.md §2, §3.5).

Attribution rules:
  * cycle_counter / cycle_pulse on a position (tag.position_id, or position.counter_tag_id)
      → position.cycles (usage_total) + component installed at that time (usage_since_install).
    When the position has a counter_tag_id, only that tag counts; other cycle tags linked to the
    position are cross-checks (processed, not accumulated) — no double counting.
    Without a position → machine.cycles.
  * part_counter_good / _scrap / _total → machine good / scrap / total. When the machine has no
    part_counter_total tag, total = good + scrap. Every part increment also gives the positions of
    that machine **without their own cycle tag** estimated cycles = parts × cycles_per_part (§2).
    When the machine has no part_counter_good tag, good is derived at query time as total − scrap.
  * run_state on a position → that position (+ component) operating_seconds / starts.
    On the machine (no position) → machine, and every position of the machine without its own
    run_state tag (inherits the machine's running time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Component, Gateway, Machine, Position, Tag
from ...models.tag import CYCLE_ROLES, SemanticRole
from .core import hour_floor, resolve_max_rate, split_hourly


@dataclass(frozen=True)
class TagInfo:
    id: str
    tenant_id: str
    machine_id: Optional[str]
    position_id: Optional[str]
    role: SemanticRole
    counter_max: Optional[int]
    max_rate_per_s: Optional[float]
    gateway_revoked: bool = False     # assigned to a revoked gateway: its readings are discarded (gateways.py)


@dataclass(frozen=True)
class PositionInfo:
    id: str
    machine_id: str
    cycles_per_part: int
    has_cycle_tag: bool
    has_run_tag: bool
    counter_tag_id: Optional[str] = None


@dataclass(frozen=True)
class ComponentWindow:
    id: str
    installed_at: Optional[datetime]
    removed_at: Optional[datetime]

    def covers(self, ts: datetime) -> bool:
        if self.installed_at is None or ts < self.installed_at:
            return False
        return self.removed_at is None or ts < self.removed_at


@dataclass
class MachineInfo:
    id: str
    tenant_id: str
    ideal_cycle_time_s: Optional[float]
    has_good_tag: bool = False
    has_scrap_tag: bool = False
    has_total_tag: bool = False
    has_run_tag: bool = False
    position_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Contribution:
    scope: str          # position | component | machine
    entity_id: str
    measure: str        # cycles | operating_seconds | starts | good | scrap | total
    amount: float
    bucket_start: datetime


@dataclass
class Topology:
    tags: dict[str, TagInfo] = field(default_factory=dict)
    machines: dict[str, MachineInfo] = field(default_factory=dict)
    positions: dict[str, PositionInfo] = field(default_factory=dict)
    components: dict[str, list[ComponentWindow]] = field(default_factory=dict)  # by position

    def component_at(self, position_id: str, ts: datetime) -> Optional[str]:
        for c in self.components.get(position_id, ()):
            if c.covers(ts):
                return c.id
        return None

    # ── max_rate for the plausibility filter ────────────────────────────
    def max_rate(self, tag: TagInfo, default_rate: Optional[float]) -> Optional[float]:
        m = self.machines.get(tag.machine_id) if tag.machine_id else None
        per_cycle = 1
        if tag.position_id and tag.position_id in self.positions:
            per_cycle = self.positions[tag.position_id].cycles_per_part
        return resolve_max_rate(tag.max_rate_per_s, m.ideal_cycle_time_s if m else None,
                                per_cycle, default_rate)

    # ── attribution ─────────────────────────────────────────────────────
    def _with_component(self, position_id: str, measure: str, amount: float, ts: datetime,
                        bucket: datetime) -> list[Contribution]:
        out = [Contribution("position", position_id, measure, amount, bucket)]
        comp = self.component_at(position_id, ts)
        if comp:
            out.append(Contribution("component", comp, measure, amount, bucket))
        return out

    def counter_contributions(self, tag: TagInfo, delta: float, ts: datetime) -> list[Contribution]:
        if delta <= 0:
            return []
        b = hour_floor(ts)
        role = tag.role
        if role in CYCLE_ROLES:
            if tag.position_id:
                p = self.positions.get(tag.position_id)
                if p is not None and p.counter_tag_id and p.counter_tag_id != tag.id:
                    return []   # the position counts with its counter_tag_id; this one is a cross-check
                return self._with_component(tag.position_id, "cycles", delta, ts, b)
            if tag.machine_id:
                return [Contribution("machine", tag.machine_id, "cycles", delta, b)]
            return []
        m = self.machines.get(tag.machine_id) if tag.machine_id else None
        if m is None:
            return []
        out: list[Contribution] = []
        parts_total = 0.0
        if role == SemanticRole.part_counter_good:
            out.append(Contribution("machine", m.id, "good", delta, b))
            if not m.has_total_tag:
                parts_total = delta
        elif role == SemanticRole.part_counter_scrap:
            out.append(Contribution("machine", m.id, "scrap", delta, b))
            if not m.has_total_tag:
                parts_total = delta
        elif role == SemanticRole.part_counter_total:
            parts_total = delta
        if parts_total:
            out.append(Contribution("machine", m.id, "total", parts_total, b))
            for pid in m.position_ids:
                p = self.positions[pid]
                if not p.has_cycle_tag:   # estimated cycles = parts × cycles_per_part
                    out += self._with_component(pid, "cycles", parts_total * p.cycles_per_part, ts, b)
        return out

    def run_contributions(self, tag: TagInfo, intervals: Iterable[tuple[datetime, datetime]],
                          starts: int, ts: datetime) -> list[Contribution]:
        targets: list[tuple[str, Optional[str]]] = []   # (scope, id) ; positions get components
        if tag.position_id:
            targets.append(("position", tag.position_id))
        elif tag.machine_id and tag.machine_id in self.machines:
            m = self.machines[tag.machine_id]
            targets.append(("machine", m.id))
            targets += [("position", pid) for pid in m.position_ids
                        if not self.positions[pid].has_run_tag]
        out: list[Contribution] = []
        for a, z in intervals:
            for bucket, secs in split_hourly(a, z):
                piece_ts = max(a, bucket)
                for scope, eid in targets:
                    if scope == "position":
                        out += self._with_component(eid, "operating_seconds", secs, piece_ts, bucket)
                    else:
                        out.append(Contribution(scope, eid, "operating_seconds", secs, bucket))
        if starts:
            b = hour_floor(ts)
            for scope, eid in targets:
                if scope == "position":
                    out += self._with_component(eid, "starts", starts, ts, b)
                else:
                    out.append(Contribution(scope, eid, "starts", starts, b))
        return out


# ── loading from Postgres ───────────────────────────────────────────────────

async def load_tag_info(db: AsyncSession, tenant_id: str, tag_id: str) -> Optional[TagInfo]:
    """The tag, only if it belongs to tenant_id. Resolves position (tag.position_id or a
    position whose counter_tag_id is this tag) and machine (tag.machine_id or the position's)."""
    row = (await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if row is None:
        return None
    position_id = row.position_id
    if position_id is None:
        position_id = (await db.execute(
            select(Position.id).where(Position.counter_tag_id == row.id,
                                      Position.tenant_id == tenant_id).limit(1)
        )).scalar_one_or_none()
    machine_id = row.machine_id
    if machine_id is None and position_id is not None:
        machine_id = (await db.execute(
            select(Position.machine_id).where(Position.id == position_id)
        )).scalar_one_or_none()
    revoked = False
    if row.gateway_id is not None:
        gw = (await db.execute(select(Gateway.mqtt_username, Gateway.mqtt_password_hash).where(
            Gateway.id == row.gateway_id, Gateway.tenant_id == tenant_id))).first()
        revoked = gw is not None and gw[0] is not None and gw[1] is None
    return TagInfo(id=row.id, tenant_id=row.tenant_id, machine_id=machine_id, position_id=position_id,
                   role=row.semantic_role, counter_max=row.counter_max,
                   max_rate_per_s=row.max_rate_per_s, gateway_revoked=revoked)


async def load_topology(db: AsyncSession, tenant_id: str,
                        machine_ids: Optional[list[str]] = None) -> Topology:
    """Snapshot of machines/positions/components/tags of a tenant (optionally some machines)."""
    topo = Topology()
    mq = select(Machine.id, Machine.tenant_id, Machine.ideal_cycle_time_s).where(Machine.tenant_id == tenant_id)
    if machine_ids is not None:
        mq = mq.where(Machine.id.in_(machine_ids))
    for mid, tid, ict in (await db.execute(mq)).all():
        topo.machines[mid] = MachineInfo(id=mid, tenant_id=tid, ideal_cycle_time_s=ict)
    if not topo.machines:
        return topo
    mids = list(topo.machines)

    positions = (await db.execute(
        select(Position).where(Position.tenant_id == tenant_id, Position.machine_id.in_(mids))
        .order_by(Position.created_at, Position.id)
    )).scalars().all()
    pids = [p.id for p in positions]

    tags = (await db.execute(
        select(Tag).where(Tag.tenant_id == tenant_id,
                          or_(Tag.machine_id.in_(mids), Tag.position_id.in_(pids) if pids else false()))
    )).scalars().all()

    counter_tag_of = {p.counter_tag_id: p.id for p in positions if p.counter_tag_id}
    pos_machine = {p.id: p.machine_id for p in positions}
    cycle_pos, run_pos = set(), set()
    for t in tags:
        pid = t.position_id or counter_tag_of.get(t.id)
        mid = t.machine_id or (pos_machine.get(pid) if pid else None)
        topo.tags[t.id] = TagInfo(t.id, t.tenant_id, mid, pid, t.semantic_role, t.counter_max, t.max_rate_per_s)
        if not t.is_active:
            continue
        m = topo.machines.get(mid) if mid else None
        if pid and t.semantic_role in CYCLE_ROLES:
            cycle_pos.add(pid)
        if pid and t.semantic_role == SemanticRole.run_state:
            run_pos.add(pid)
        if m is not None:
            if t.semantic_role == SemanticRole.part_counter_good:
                m.has_good_tag = True
            elif t.semantic_role == SemanticRole.part_counter_scrap:
                m.has_scrap_tag = True
            elif t.semantic_role == SemanticRole.part_counter_total:
                m.has_total_tag = True
            elif t.semantic_role == SemanticRole.run_state and not pid:
                m.has_run_tag = True

    for p in positions:
        topo.positions[p.id] = PositionInfo(
            id=p.id, machine_id=p.machine_id, cycles_per_part=max(1, p.cycles_per_part or 1),
            has_cycle_tag=p.id in cycle_pos or bool(p.counter_tag_id),
            has_run_tag=p.id in run_pos,
            counter_tag_id=p.counter_tag_id,
        )
        topo.machines[p.machine_id].position_ids.append(p.id)

    if pids:
        comps = (await db.execute(
            select(Component.id, Component.last_position_id, Component.installed_at, Component.removed_at)
            .where(Component.tenant_id == tenant_id, Component.last_position_id.in_(pids))
            .order_by(Component.installed_at)
        )).all()
        for cid, pid, inst, rem in comps:
            topo.components.setdefault(pid, []).append(ComponentWindow(cid, inst, rem))
    return topo
