"""Late-data check (QA 5b): after an uplink outage + restore, every complete F1 block must equal the golden value
(n = 10 000, x, end), usage cycles must equal the plant truth, recompute (reextract_all) must be identical.
    API=... STATE=.../f_state.json TRUTH_CYCLES=<cycles from the plant /truth at the end> python late_check.py"""
import json, os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sprint5", "scenario_f_real"))
from api import call, login
DOC = json.load(open("/home/user/factory-monitor/docs/scenarios/scenario_f_predictive.json"))
C1 = DOC["cases"]["F1"]; SHIFT = 182
st = json.load(open(os.environ["STATE"])); t = login(); res = []
def check(ok, name, got=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got={str(got)[:600]}"), flush=True)
P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
truth = int(os.environ["TRUTH_CYCLES"]); full = truth // 10000
s, f = call("GET", f"/api/predictive/features?position_id={st['pos1']}", t=t)
pts = {p["i"]: p for p in f["series"][0]["points"]}
rows = {w["i"]: w for w in C1["windows"]}
inst = P(C1["installed_at"]) - timedelta(days=SHIFT)
check(sorted(pts) == list(range(1, full + 1)), f"{full} complete blocks extracted (truth {truth} cycles)", sorted(pts)[-3:])
check(all(pts.get(i, {}).get("n") == 10000 for i in range(1, full + 1)), "n = 10 000 in every block",
      [(i, pts.get(i, {}).get("n")) for i in range(1, full + 1) if pts.get(i, {}).get("n") != 10000][:10])
check(all(pts.get(i) and abs(pts[i]["x"] - rows[i]["x"]) < 1e-12 for i in range(1, full + 1)), "x exact (golden)",
      [(i, pts.get(i, {}).get("x"), rows[i]["x"]) for i in range(1, full + 1) if not pts.get(i) or abs(pts[i]["x"] - rows[i]["x"]) >= 1e-12][:10])
check(all(pts.get(i) and P(pts[i]["end_at"]) == inst + timedelta(hours=12 * i) for i in range(1, full + 1)), "every block ends at installed + i·12 h")
s, pos = call("GET", f"/api/positions/{st['pos1']}", t=t)
u = (pos.get("usage") or {}).get("cycles_total")
check(u == truth, f"usage cycles_total == plant truth ({truth})", u)
import httpx, time as _t
_t0 = _t.time()
_r = httpx.post(os.environ["API"] + "/api/predictive/recompute", json={"monitor_id": st["m1"], "reextract_all": True},
               headers={"Authorization": "Bearer " + t}, timeout=1800, trust_env=False)
s, rc = _r.status_code, _r.json()
print(f"recompute took {_t.time() - _t0:.0f} s", flush=True)
check(s == 200 and rc.get("same") is True and all(m.get("windows_reextracted", 0) == 0 for m in rc.get("monitors", [])),
      "recompute reextract_all: same alerts, 0 windows differ", rc)
print(f"{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
