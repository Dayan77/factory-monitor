"""Seeds golden scenario F1 (shifted so block LAST ends ~now) into the dev DB of the web verification server."""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
import httpx
sys.path.insert(0, os.path.join(os.getcwd()))           # server/
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from predictive_helpers import F, f1_windows, f1_hourly_usage, rules_of  # noqa
BASE = os.environ.get("BASE", "http://localhost:8150")
LAST = int(os.environ.get("LAST", "52"))

async def main():
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, PredictiveMonitor, UsageBucket, Machine
    from src.services.predictive import engine as pe, service as ps
    c = F["cases"]["F1"]
    wins = [w for w in f1_windows() if w["i"] <= LAST]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    shift = now - timedelta(hours=1) - wins[-1]["end"]
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as cl:
        tok = (await cl.post("/api/auth/login", json={"email": "planejador@factory-monitor.com", "password": "Planner@1234"})).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        tree = (await cl.get("/api/plants/tree", headers=h)).json()
        mach = {m["name"]: m["id"] for p in tree for l in p["lines"] for m in l["machines"]}
        async with AsyncSessionLocal() as s:
            mid = mach["Estação de Fixação"]
            tenant = (await s.get(Machine, mid)).tenant_id
            pos = (await s.execute(select(Position).where(Position.machine_id == mid, Position.name == ("Cilindro F1 (cenário F)" + os.environ.get("SUFFIX", ""))))).scalar_one_or_none()
            if pos is None:
                pos = Position(tenant_id=tenant, machine_id=mid, name=("Cilindro F1 (cenário F)" + os.environ.get("SUFFIX", "")), component_type=ComponentType("pneumatic_cylinder"))
                s.add(pos); await s.flush()
                for t, v in f1_hourly_usage().items():
                    s.add(UsageBucket(scope="position", entity_id=pos.id, measure="cycles", bucket_start=t + shift, tenant_id=tenant, value=v))
            await s.commit()
            pid = pos.id
        rules = [{"code": r["id"], "type": r["type"], "value": r["value"], "sustained_for": r["sustained_for"], "severity": r["severity"]} for r in rules_of("F1")]
        r = await cl.post("/api/predictive/monitors", headers=h, json={
            "position_id": pid, "feature": "stroke_time", "window_cycles": c["window"]["cycles"], "warmup_windows": c["warmup_windows"],
            "failure_limit": c["failure_limit"]["value"], "failure_limit_relative": True,
            "start_at": (datetime.fromisoformat(c["installed_at"].replace("Z", "+00:00")) + shift).isoformat(), "rules": rules})
        print("monitor", r.status_code, r.text[:200])
        m = r.json()
        async with AsyncSessionLocal() as s:
            mm = await s.get(PredictiveMonitor, m["id"])
            for w in wins:
                reset = await ps.latest_reset(s, mm)
                await pe.store_window(s, mm, reset, start=w["start"] + shift, end=w["end"] + shift, x=w["x"], n=w["n"], u=w["u"])
            await s.commit()
        r = await cl.post(f"/api/predictive/monitors/{m['id']}/process", headers=h)
        print("process", r.status_code, r.text[:300])
        # a second monitor that is still learning (motor current of the conveyor)
        mid2 = mach["Transportador de Saída"]
        async with AsyncSessionLocal() as s:
            p2 = (await s.execute(select(Position).where(Position.machine_id == mid2))).scalars().first()
        if p2 and not os.environ.get("SUFFIX"):
            r = await cl.post("/api/predictive/monitors", headers=h, json={"position_id": p2.id, "feature": "current", "failure_limit": 14.0})
            print("monitor2", r.status_code, r.text[:120])
        al = (await cl.get("/api/predictive/alerts", headers=h)).json()
        print(json.dumps([{k: a[k] for k in ("id", "severity", "state", "position_name")} for a in al], ensure_ascii=False))
        print((al[0]["explanation"] if al else None))

asyncio.run(main())
