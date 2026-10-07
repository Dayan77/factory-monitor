"""Scenario F1 check against the QA server after the REAL-path playback (f_real.py: setup → monitor F1 → run F1;
then wait until the server's MQTT backlog is drained). Compares every window, the baseline, the alert (instants,
escalations, triggers), the detector trace, the RUL checkpoints with their bands and the lead times with
docs/scenarios/scenario_f_predictive.json (clock shifted SHIFT days), then POST /recompute must give identical alerts.

    API=http://127.0.0.1:8160 STATE=.../f_state.json SHIFT_DAYS=182 python f1_check.py
"""
import json, os, sys
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import call, login, recompute  # noqa: E402

SHIFT = int(os.environ.get("SHIFT_DAYS", "182"))
DOC = json.load(open("/home/user/factory-monitor/docs/scenarios/scenario_f_predictive.json"))
X, C1 = DOC["expected"]["F1"], DOC["cases"]["F1"]
st = json.load(open(os.environ.get("STATE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "f_state.json"))))
t = login()
res = []


def check(ok, name, got=None):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got={str(got)[:500]}"), flush=True)


def P(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def sh(s):
    return (P(s) - timedelta(days=SHIFT)).strftime("%Y-%m-%dT%H:%M:%SZ")


def shd(s):
    return (date.fromisoformat(s) - timedelta(days=SHIFT)).isoformat() if s else s


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(b))


s, f = call("GET", f"/api/predictive/features?position_id={st['pos1']}", t=t)
pts = {p["i"]: p for p in f["series"][0]["points"]}
rows = {w["i"]: w for w in C1["windows"]}
inst = P(C1["installed_at"]) - timedelta(days=SHIFT)
check(sorted(pts) == list(range(1, 71)), f"70 windows extracted ({len(pts)})", sorted(pts)[-3:])
bad = [(i, pts.get(i, {}).get("x"), rows[i]["x"]) for i in rows if not close(pts.get(i, {}).get("x"), rows[i]["x"], 1e-12)]
check(not bad, "x (block median) exact for every window", bad[:10])
badn = [(i, pts[i]["n"]) for i in pts if pts[i]["n"] != C1["window"]["cycles"]]
check(not badn, "n = 10 000 strokes in every block (no drift on resume)", badn[:10])
bade = [(i, pts[i]["end_at"]) for i in pts if P(pts[i]["end_at"]) != inst + timedelta(hours=12 * i)]
check(not bade, "every block ends exactly at installed_at + i·12 h", bade[:5])
s, mon = call("GET", f"/api/predictive/monitors/{st['m1']}", t=t)
b = [x for x in mon["baselines"] if x.get("established_at")][0]
eb = X["baseline"]
check(b["from_i"] == eb["from_i"] and b["to_i"] == eb["to_i"] and b["established_at"] == sh(eb["established_at"])
      and close(b["m0"], eb["m0"]) and close(b["sigma"], eb["sigma"]),
      f"baseline {eb['from_i']}–{eb['to_i']} m0 {eb['m0']} σ {eb['sigma']} at {sh(eb['established_at'])}",
      {k: b.get(k) for k in ("from_i", "to_i", "established_at", "m0", "sigma")})
for tr in X["trace"]:
    p = pts[tr["i"]]
    d = [(k, p.get(k), v) for k, v in tr.items() if k not in ("i", "triggers") and not close(p.get(k), v, 1e-3)]
    if sorted(p.get("triggers") or []) != sorted(tr["triggers"]):
        d.append(("triggers", p.get("triggers"), tr["triggers"]))
    check(not d, f"trace window {tr['i']} (x/z/EWMA/CUSUM/rolling z/slope/triggers)", d)
s, al = call("GET", f"/api/predictive/alerts?position_id={st['pos1']}&state=open&state=acknowledged&state=closed&state=converted", t=t)
check(len(al) == 1, f"exactly one alert for (position, signal) ({len(al)})", al)
ea = X["alert"]
if al:
    a = al[0]
    check(a["opened_window"] == ea["opened_window"] and a["opened_at"] == sh(ea["opened_at"]),
          f"alert opened at window {ea['opened_window']} ({sh(ea['opened_at'])})", {k: a.get(k) for k in ("opened_window", "opened_at")})
    check(sorted(a["first_triggers"]) == sorted(ea["first_triggers"]), f"first triggers {ea['first_triggers']}", a["first_triggers"])
    check(a["severity"] == ea["severity"], f"severity {ea['severity']}", a["severity"])
    check(a.get("escalations") == {k: sh(v) for k, v in ea["escalations"].items()} and a.get("escalation_windows") == ea["escalation_windows"],
          f"escalations warning@35 critical@48 ({ea['escalation_windows']})", (a.get("escalations"), a.get("escalation_windows")))
    check(a.get("triggers_seen") == ea["triggers_seen"], f"triggers_seen {ea['triggers_seen']}", a.get("triggers_seen"))
    check(a["state"] == ea["state"], f"state {ea['state']}", a["state"])
    fail = P(C1["failure"]["failed_at"]) - timedelta(days=SHIFT)
    lt = {k: (fail - P(v)).total_seconds() / 3600 for k, v in (a.get("escalations") or {}).items()}
    check(lt == X["lead_time_h"], f"lead time by severity {X['lead_time_h']} h", lt)
    ltr = {k: (fail - P(pts[w]["end_at"])).total_seconds() / 3600 for k, w in (a.get("triggers_seen") or {}).items()}
    check(ltr == {k: float(v) for k, v in X["lead_time_by_rule_h"].items()}, f"lead time by rule {X['lead_time_by_rule_h']}", ltr)
for cp in X["rul_checkpoints"]:
    r = pts[cp["window"]]["rul"] or {}
    diffs = []
    for k, v in cp.items():
        if k in ("window",) or k.startswith("true_"):
            continue
        got = r.get(k)
        if k in ("today", "projected_date", "projected_date_low", "projected_date_high"):
            v = shd(v)
        if isinstance(v, float):
            if not close(got, v, 1e-4):
                diffs.append((k, got, v))
        elif got != v:
            diffs.append((k, got, v))
    check(not diffs, f"RUL checkpoint window {cp['window']}: {cp['rul_days']} d ({cp['rul_days_low']}–{cp['rul_days_high']}) → {shd(cp['projected_date'])}", diffs)
s, rc = recompute({"monitor_id": st["m1"]}, t)
check(s == 200 and rc.get("same") is True, "recompute reproduces identical alerts", rc)
print(f"{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
