"""QA (Sprint 5): stroke-time cycle blocks must not drift when extraction resumes from the saved cursor.

Real path (virtual plant -> gateway -> MQTT -> Influx) delivers one block per engine run. Each run resumes from
`cycle_cursor.end_at` = the instant of the counter reading that closed the previous block. That reading must not
be counted again in the next block (P-1: block i = cycles (i-1)*N+1 .. i*N, ends at the reading counter = i*N).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant

T = datetime(2027, 5, 3, 4, 0, tzinfo=timezone.utc)
N = 20


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T)
    monkeypatch.setattr(clock, "now", c)
    return c


async def test_cycle_blocks_resumed_one_run_per_block_do_not_drift(client, clk):
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, Tag
    from src.models.tag import ProtocolType, SemanticRole
    from src.services.influx import influx
    ctx = await make_maint_tenant("QAR")
    mach = ctx.machines["Estação de Fixação"]
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=mach, name="Cilindro QA", component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        st = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="curso QA", unit="ms",
                 protocol=ProtocolType.mqtt, address=f"qa/st/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.stroke_time)
        ct = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="ciclos QA", unit="",
                 protocol=ProtocolType.mqtt, address=f"qa/ct/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.cycle_counter,
                 counter_max=2**32 - 1)
        s.add_all([st, ct])
        await s.commit()
        pos, st_id, ct_id = p.id, st.id, ct.id
    hp = ctx.h("maintenance_planner")
    m = await api(client, "POST", "/api/predictive/monitors", hp, 201, json={
        "position_id": pos, "feature": "stroke_time", "tag_id": st_id, "window_cycles": N, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0}})
    # like the playback: cycle c at T + c min writes stroke_time, then counter = c (same instant)
    blocks = 4
    await influx.write(ctx.id, ct_id, 0.0, machine_id=mach, unit="", ts=T)   # counter starts at 0 at install
    for b in range(blocks):
        for k in range(N):
            c = b * N + k + 1
            ts = T + timedelta(minutes=c)
            await influx.write(ctx.id, st_id, 200.0 + b, machine_id=mach, unit="ms", ts=ts)
            await influx.write(ctx.id, ct_id, float(c), machine_id=mach, unit="", ts=ts + timedelta(milliseconds=1))
        # one engine run per closed block (what the 5-min background task does in the real path)
        clk.at = T + timedelta(minutes=(b + 1) * N + 5)
        await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    feats = await api(client, "GET", f"/api/predictive/features?position_id={pos}", hp)
    pts = feats["series"][0]["points"]
    assert [p["i"] for p in pts] == list(range(1, blocks + 1))
    ends = [datetime.fromisoformat(p["end_at"].replace("Z", "+00:00")) for p in pts]
    expected_ends = [T + timedelta(minutes=(b + 1) * N, milliseconds=1) for b in range(blocks)]
    assert [p["n"] for p in pts] == [N] * blocks, f"n per block {[p['n'] for p in pts]}"
    assert ends == expected_ends, f"block ends {ends}"
    assert [p["x"] for p in pts] == [200.0 + b for b in range(blocks)]


@pytest.mark.xfail(strict=True, reason="known limitation (backend report): stroke slices empty for > SKIP_EMPTY_AFTER (6 h) "
                   "are skipped for good, so a gateway backlog flushed later is never cut into blocks — needs a decision")
async def test_cycle_blocks_survive_a_gateway_backlog_older_than_6h(client, clk):
    """A gateway that was offline buffers its readings and flushes them when it reconnects (edge SQLite buffer).
    If the engine ran meanwhile (no data yet), the late readings must still be cut into blocks from the start —
    P-1: block 1 = cycles 1..N after the reset. (Scenario F playback through the real path hits the same case.)"""
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, Tag
    from src.models.tag import ProtocolType, SemanticRole
    from src.services.influx import influx
    ctx = await make_maint_tenant("QAL")
    mach = ctx.machines["Estação de Fixação"]
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=mach, name="Cilindro QL", component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        st = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="curso QL", unit="ms",
                 protocol=ProtocolType.mqtt, address=f"ql/st/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.stroke_time)
        ct = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="ciclos QL", unit="",
                 protocol=ProtocolType.mqtt, address=f"ql/ct/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.cycle_counter,
                 counter_max=2**32 - 1)
        s.add_all([st, ct])
        await s.commit()
        pos, st_id, ct_id = p.id, st.id, ct.id
    hp = ctx.h("maintenance_planner")
    m = await api(client, "POST", "/api/predictive/monitors", hp, 201, json={
        "position_id": pos, "feature": "stroke_time", "tag_id": st_id, "window_cycles": N, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0}})
    # the gateway is offline: the engine runs 10 h after the reset and sees nothing
    clk.at = T + timedelta(hours=10)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    # the gateway comes back and flushes its buffer: 3 blocks recorded one cycle per minute from T
    await influx.write(ctx.id, ct_id, 0.0, machine_id=mach, unit="", ts=T)
    for c in range(1, 3 * N + 1):
        ts = T + timedelta(minutes=c)
        await influx.write(ctx.id, st_id, 200.0, machine_id=mach, unit="ms", ts=ts)
        await influx.write(ctx.id, ct_id, float(c), machine_id=mach, unit="", ts=ts + timedelta(milliseconds=1))
    clk.at = T + timedelta(hours=10, minutes=5)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    feats = await api(client, "GET", f"/api/predictive/features?position_id={pos}", hp)
    pts = feats["series"][0]["points"]
    assert [(p["i"], p["n"]) for p in pts] == [(1, N), (2, N), (3, N)], [(p["i"], p["n"], p["end_at"]) for p in pts]
