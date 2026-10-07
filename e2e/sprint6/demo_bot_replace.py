"""Sprint 6 §5 — demo-bot closes the loop on the LIVE demo: alert → WO → replacement → virtual plant wear restarts.

The B3 stroke-time alert of the demo plant takes ~2.5 days of wall clock to appear naturally, so this check opens
one on the real demo tenant (as the predictive engine would: stroke_time feature on the B3 position, monitor of
that position) and runs one pass of the real demo-bot (`services/demo/bot.run_once`) with
DEMO_VIRTUAL_PLANT_CONTROL_URL pointing at the running demo virtual plant's control API. It then checks:

  - the WO went scheduled → in_progress → completed (finding defect_confirmed) → closed, authored by demo-bot,
  - a replacement and an ISO 14224 failure record were written, the alert is closed/linked,
  - an audit event demo_bot.replacement exists,
  - the virtual plant's B3 cylinder was replaced (install_no + 1, usage since install back near 0).

opened_at is set 14 min ago and run_once gets now + 2 min, so the dev server's own demo-bot loop (900 s threshold,
no control URL) cannot take this alert first.

    cd server && set -a && . ../.env && set +a && DEMO_TENANT_SLUG=fc-demo \
      DEMO_VIRTUAL_PLANT_CONTROL_URL=http://127.0.0.1:8163 ../.venv/bin/python ../e2e/sprint6/demo_bot_replace.py
"""
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.getcwd())

from sqlalchemy import select  # noqa: E402

from src.config import settings  # noqa: E402
from src.db import AsyncSessionLocal  # noqa: E402
from src.models import (FailureRecord, PredictiveAlert, Tag, WorkOrder, WorkOrderReplacement)  # noqa: E402
from src.models.predictive import PredictiveMonitor  # noqa: E402
from src.models.tag import SemanticRole  # noqa: E402
from src.services.demo.bootstrap import bot_user, demo_tenant  # noqa: E402
from src.services.demo.bot import run_once  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(f"  {'✓' if ok else '✗'} {msg}" + ("" if ok or got is None else f"   got={str(got)[:300]}"))
    fails += 0 if ok else 1


def vp_b3():
    url = settings.demo_virtual_plant_control_url.rstrip("/")
    with urllib.request.urlopen(f"{url}/status", timeout=10) as r:
        return json.load(r)["components"]["montagem.fixacao.b3"]


async def main():
    assert settings.demo_virtual_plant_control_url, "set DEMO_VIRTUAL_PLANT_CONTROL_URL"
    before = vp_b3()
    print(f"  virtual plant B3 before: install_no={before['install_no']} usage={before['usage_since_install']:.0f}")
    check(before["usage_since_install"] > 0, "precondition: B3 has worn (usage since install > 0) — run when the cell is producing")
    async with AsyncSessionLocal() as db:
        t = await demo_tenant(db)
        check(t is not None and t.is_demo, "demo tenant exists (is_demo)")
        bot = await bot_user(db, t.id)
        tag = (await db.execute(select(Tag).where(Tag.tenant_id == t.id, Tag.semantic_role == SemanticRole.stroke_time)
                                )).scalars().all()
        b3 = next(tg for tg in tag if (tg.edge or {}).get("key", "").startswith("montagem.fixacao.b3"))
        mon = (await db.execute(select(PredictiveMonitor).where(PredictiveMonitor.tenant_id == t.id,
                                                                PredictiveMonitor.position_id == b3.position_id))
               ).scalars().first()
        check(mon is not None, "B3 has a stroke-time monitor (bootstrap)")
        now = datetime.now(timezone.utc)
        a = PredictiveAlert(tenant_id=t.id, monitor_id=mon.id, position_id=b3.position_id, machine_id=b3.machine_id,
                            feature="stroke_time", state="open", severity="warning",
                            opened_at=now - timedelta(minutes=14), explanation="QA: alerta de desgaste do B3 (teste)",
                            evidence={})
        db.add(a)
        await db.commit()
        aid = a.id
    async with AsyncSessionLocal() as db:
        done = await run_once(db, now=datetime.now(timezone.utc) + timedelta(minutes=2))
    mine = [d for d in done if d["alert_id"] == aid]
    check(len(mine) == 1 and mine[0]["virtual_plant_ref"] == "montagem.fixacao.b3", "demo-bot handled the alert", done)
    async with AsyncSessionLocal() as db:
        a = await db.get(PredictiveAlert, aid)
        wo = await db.get(WorkOrder, a.work_order_id) if a.work_order_id else None
        check(wo is not None and wo.state == "closed", "WO closed", wo and wo.state)
        check(wo is not None and wo.finding == "defect_confirmed", "WO finding defect_confirmed", wo and wo.finding)
        from src.models import WorkOrderHistory
        hist = (await db.execute(select(WorkOrderHistory).where(WorkOrderHistory.work_order_id == wo.id)
                                 .order_by(WorkOrderHistory.at))).scalars().all()
        states = [h.to_state for h in hist if getattr(h, "to_state", None)]
        check(all(s in states for s in ("scheduled", "in_progress", "completed", "closed")),
              f"WO history {states}", states)
        check(all(h.by == bot.id for h in hist if getattr(h, "by", None)), "every history entry by demo-bot")
        rep = (await db.execute(select(WorkOrderReplacement).where(WorkOrderReplacement.work_order_id == wo.id))
               ).scalars().all()
        fr = (await db.execute(select(FailureRecord).where(FailureRecord.work_order_id == wo.id))).scalars().all()
        check(len(rep) == 1 and len(fr) == 1 and fr[0].detection_method == "continuous_condition_monitoring",
              "replacement + failure record (continuous condition monitoring)", (len(rep), len(fr)))
        from src.models.audit import AuditEvent
        au = (await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == wo.tenant_id,
                                                        AuditEvent.action == "demo_bot.replacement",
                                                        AuditEvent.entity_id == wo.id))).scalars().all()
        check(len(au) == 1, "audit event demo_bot.replacement")
        print(f"  alert state={a.state} resolution={a.resolution}; WO #{wo.number}")
    after = vp_b3()
    print(f"  virtual plant B3 after: install_no={after['install_no']} usage={after['usage_since_install']:.0f}")
    check(after["install_no"] == before["install_no"] + 1, "virtual plant: B3 replaced (install_no + 1)",
          (before["install_no"], after["install_no"]))
    check(after["usage_since_install"] < before["usage_since_install"],
          "virtual plant: B3 wear restarted (usage since install back near 0)", after["usage_since_install"])
    print("OK" if fails == 0 else f"FAIL ({fails})")
    return 0 if fails == 0 else 1


sys.exit(asyncio.run(main()))
