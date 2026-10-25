"""Scenario F2 check against the QA server after the real-path playback (see f_real.py: setup → run F2 → monitor F2). Usage: API=http://127.0.0.1:8163 python f2_check.py 62"""
import json, os, sys
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("API", "http://127.0.0.1:8163")
from api import call, login, recompute

SHIFT = 182
DOC = json.load(open("/home/user/factory-monitor/docs/scenarios/scenario_f_predictive.json"))
X = DOC["expected"]["F2"]
st = json.load(open(os.path.join(os.path.dirname(__file__), "f2_state.json")))
t = login()


def sh(s):
    return (datetime.fromisoformat(s.replace("Z", "+00:00")) - timedelta(days=SHIFT)).strftime("%Y-%m-%dT%H:%M:%SZ")


def shd(s):
    return (date.fromisoformat(s) - timedelta(days=SHIFT)).isoformat() if s else s


res = []


def check(ok, name, got=None):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got={str(got)[:400]}"))


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(b))


s, f = call("GET", f"/api/predictive/features?position_id={st['pos2']}", t=t)
ser = f["series"][0]
pts = {p["i"]: p for p in ser["points"]}
rows = {w["i"]: w for w in DOC["cases"]["F2"]["windows"]}
upto = int(sys.argv[1]) if len(sys.argv) > 1 else 62
bad = [(i, pts.get(i, {}).get("x"), rows[i]["x"]) for i in range(1, upto + 1)
       if rows[i]["running_h"] > 0 and not close(pts.get(i, {}).get("x"), rows[i]["x"], 1e-9)]
check(not bad, f"F2 x (running-only time-weighted mean) exact for days 1..{upto} except holiday", bad[:10])
check(pts[47]["valid"] is False and pts[47]["phase"] == "invalid", "day 47 (holiday) invalid", pts.get(47))
rh = [(i, pts[i].get("running_h")) for i in range(1, upto + 1) if not close(pts[i].get("running_h"), rows[i]["running_h"], 1e-6)]
check(not rh, "running_h per day = scenario", rh[:10])
s, mon = call("GET", f"/api/predictive/monitors/{st['m2']}", t=t)
b = [x for x in mon["baselines"] if x.get("established_at")][0]
eb = X["baselines"][0]
check(b["from_i"] == eb["from_i"] and b["to_i"] == eb["to_i"] and b["established_at"] == sh(eb["established_at"])
      and close(b["m0"], X["baselines_m0"][0]) and close(b["sigma"], X["baselines_sigma"][0]),
      f"baseline {eb['from_i']}–{eb['to_i']} m0 {X['baselines_m0'][0]} σ {X['baselines_sigma'][0]} at {sh(eb['established_at'])}",
      {k: b.get(k) for k in ("from_i", "to_i", "established_at", "m0", "sigma")})
s, al = call("GET", f"/api/predictive/alerts?position_id={st['pos2']}&state=open&state=acknowledged&state=closed&state=converted", t=t)
ea = X["alert"]
check(len(al) == 1, f"one alert for the position ({len(al)})", al)
if al:
    a = al[0]
    check(a["opened_window"] == ea["opened_window"] and a["opened_at"] == sh(ea["opened_at"]) and a["severity"] == "watch"
          and a["first_triggers"] == ea["first_triggers"],
          f"watch alert opened at window {ea['opened_window']} {sh(ea['opened_at'])} by {ea['first_triggers']}",
          {k: a.get(k) for k in ("opened_window", "opened_at", "severity", "first_triggers")})
    ts_ = {k: v for k, v in (a.get("triggers_seen") or {}).items() if v <= upto}
    check(ts_ == ea["triggers_seen"], f"triggers_seen ≤ {upto} = {ea['triggers_seen']}", a.get("triggers_seen"))
    check(a.get("escalations") == {"watch": sh(ea["escalations"]["watch"])} or upto < 62,
          "no escalation above watch", a.get("escalations"))
for cp in X["rul_checkpoints"]:
    if cp["window"] > upto:
        continue
    r = pts[cp["window"]]["rul"] or {}
    diffs = []
    for k, v in cp.items():
        if k == "window":
            continue
        got = r.get(k)
        if k in ("today", "projected_date", "projected_date_low", "projected_date_high"):
            v = shd(v)
        if isinstance(v, float):
            if not close(got, v):
                diffs.append((k, got, v))
        elif got != v:
            diffs.append((k, got, v))
    check(not diffs, f"RUL checkpoint window {cp['window']} (rul_days {cp.get('rul_days')}, band {cp.get('rul_days_low')}–{cp.get('rul_days_high')})", diffs)
s, rc = recompute({"monitor_id": st["m2"]}, t)
check(rc.get("same") is True, "recompute reproduces identical alerts", rc)
print(f"{sum(res)}/{len(res)} passed")
