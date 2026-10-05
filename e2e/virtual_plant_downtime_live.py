"""Accelerated virtual plant (standard calendar) → gateway → MQTT → server: live downtime detection vs. the plant's
ground truth (/state).

The virtual plant at --speed N stamps readings with the HOST clock, so a stop of D simulated seconds lasts D/N real
seconds on the server. Expected on the server, per machine:
* a downtime EVENT for every stop whose real duration ≥ the machine threshold (120 s), after merging stops less
  than 10 s (real) apart (C22–C24); everything shorter (≥ 1 s debounce) is a MICRO-STOP, never an event;
* every event opened live (WebSocket action 'open' received before its end) and closed live ('close').
Ground-truth stops = faults + setups + micro-stops + starved/blocked + the plant's calendar breaks (sim time).

Env: API (http://localhost:8176), VP_STATE (http://127.0.0.1:8189/state), PLANT_JSON (edge/config/plant.json),
WS_LOG (JSON lines of 'downtime' socket messages), SPEED (20), START_TIME (06:00, the plant's --start-time),
TOL_S (5, real seconds), EMAIL/PASSWORD. Exit 0 = every check passed.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

API = os.getenv("API", "http://localhost:8176")
VP = os.getenv("VP_STATE", "http://127.0.0.1:8189/state")
SPEED = float(os.getenv("SPEED", "20"))
TOL = float(os.getenv("TOL_S", "5"))
THRESH, MERGE, DEBOUNCE = float(os.getenv("THRESH_S", "120")), 10.0, 1.0
NAMES = {"usinagem.torno": "Torno CNC", "usinagem.centro": "Centro de Usinagem", "montagem.robo": "Robô de Solda",
         "montagem.fixacao": "Estação de Fixação", "montagem.transportador": "Transportador de Saída"}
SB = [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in json.loads(os.getenv("SERVER_BREAKS", "[]"))]
results: list[tuple[bool, str]] = []


def check(ok, name, got=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r}"))


def hm(s: str) -> float:
    h, m = map(int, s.split(":"))
    return h * 3600 + m * 60


def breaks_sim(plant: dict, start_s: float, horizon_s: float) -> list[tuple[float, float]]:
    out = []
    for day in range(0, int(horizon_s // 86400) + 2):
        for sh in plant["assets"]["plant"]["shifts"]:
            for b in sh.get("breaks", []):
                a, e = hm(b["start_local"]), hm(b["end_local"])
                for base in (day * 86400 - 86400, day * 86400):
                    s0, s1 = base + a - start_s, base + e - start_s
                    if s1 > 0 and s0 < horizon_s:
                        out.append((max(0.0, s0), min(horizon_s, s1)))
    return sorted(set(out))


def union(iv):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def union_merged(stops):
    merged = []
    for a, b in stops:
        if merged and (a - merged[-1][1]) / SPEED < MERGE:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def main() -> int:
    st = httpx.get(VP, timeout=10).json()
    plant = json.load(open(os.getenv("PLANT_JSON", "edge/config/plant.json"), encoding="utf-8"))
    t0 = datetime.fromisoformat(st["runtime"]["started_at"])
    sim_now = st["sim_time_s"]
    start_s = hm(os.getenv("START_TIME", "06:00"))
    real = lambda s: t0 + timedelta(seconds=s / SPEED)  # noqa: E731
    br = breaks_sim(plant, start_s, sim_now)
    tok = httpx.post(f"{API}/api/auth/login", json={"email": os.getenv("EMAIL", "admin@factory-monitor.com"),
                                                   "password": os.getenv("PASSWORD", "Admin@1234")}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    machines = {m["name"]: m for m in httpx.get(f"{API}/api/machines/", headers=h).json()}
    ws = [json.loads(x) for x in open(os.environ["WS_LOG"]) if x.strip()] if os.getenv("WS_LOG") else []
    # ignore the first and last minute of the run (warm-up, still-open stop at the end)
    lo, hi = 60.0 * SPEED, sim_now - 60.0 * SPEED
    print(f"run: {t0.isoformat()} + {sim_now / 3600:.2f} h simulated ({sim_now / SPEED / 60:.1f} min real) at {SPEED:g}×;"
          f" checked window sim [{lo:.0f}, {hi:.0f}] s")
    tot_exp_ev = tot_got_ev = 0
    for key, name in NAMES.items():
        ev = st["truth"]["events"].get(key, {})
        stops = [(f["start_s"], f["start_s"] + f["duration_s"]) for f in st["truth"]["faults"].get(key, [])
                 if f.get("duration_s") is not None]
        for kind in ("micro_stops", "setups", "starved", "blocked"):
            stops += [(e["start_s"], e["start_s"] + e["duration_s"]) for e in ev.get(kind, []) if e.get("duration_s")]
        stops = union(stops + br)
        merged = []
        for a, b in stops:                       # merge gaps < 10 s real
            if merged and (a - merged[-1][1]) / SPEED < MERGE:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        merged = [(a, b) for a, b in merged if (b - a) / SPEED >= DEBOUNCE and a >= lo and b <= hi]
        # SERVER_BREAKS: real-time breaks of the server calendar; a plant stop entirely inside one is planned
        # downtime → no event (C10/C11) and no micro-stop
        planned = [(a, b) for a, b in merged if any(bs <= real(a) and real(b) <= be for bs, be in SB)]
        merged = [x for x in merged if x not in planned]
        exp_ev = [(a, b) for a, b in merged if (b - a) / SPEED >= THRESH + TOL]   # borderline: either way
        borderline = [(a, b) for a, b in merged if abs((b - a) / SPEED - THRESH) <= TOL]
        exp_micro = [(a, b) for a, b in merged if (b - a) / SPEED < THRESH - TOL]
        m = machines[name]
        got = httpx.get(f"{API}/api/downtime/", headers=h, params={
            "machine_id": m["id"], "start": real(lo).isoformat(), "stop": real(hi).isoformat(), "limit": 1000}).json()
        got = [e for e in got if e["source"] != "manual"]
        # a stop split at a shift boundary is two events with the same stop_start
        by_stop = {}
        for e in got:
            by_stop.setdefault(e["stop_start"], []).append(e)
        print(f"\n[{name}] truth: {len(merged)} stops ({len(exp_ev)} ≥ {THRESH:g}+{TOL:g} s real, {len(exp_micro)} micro,"
              f" {len(borderline)} within ±{TOL:g} s of the threshold) — server: {len(by_stop)} stops / {len(got)} events")
        tot_exp_ev += len(exp_ev)
        tot_got_ev += len(by_stop)
        unmatched_truth = []
        for a, b in exp_ev:
            hit = [k for k in by_stop if abs((datetime.fromisoformat(k) - real(a)).total_seconds()) <= TOL]
            if not hit:
                unmatched_truth.append((real(a).strftime("%H:%M:%S"), round((b - a) / SPEED, 1)))
                continue
            dur = sum(e["duration_s"] or 0 for e in by_stop[hit[0]])
            # merge-gap boundary: a following truth stop that starts 10 s ± 0.5 s (real) after this one ends may
            # legitimately be merged by the server (sampling jitter of the transition timestamps)
            nxt = [(c, d) for c, d in stops if c > b and abs((c - b) / SPEED - MERGE) <= 0.5]
            alt = ((nxt[0][1] - a) / SPEED) if nxt else None
            if alt is not None and abs(dur - alt) <= 2 * TOL:
                print(f"    (boundary) {real(a).strftime('%H:%M:%S')}: merged with a stop {(nxt[0][0] - b) / SPEED:.2f} s"
                      f" later (merge gap {MERGE:g} s) → {dur:.1f} s")
                continue
            if abs(dur - (b - a) / SPEED) > 2 * TOL:
                unmatched_truth.append((real(a).strftime("%H:%M:%S"), round((b - a) / SPEED, 1), "dur", dur))
        check(not unmatched_truth, f"{name}: every stop ≥ {THRESH:g} s real is an event with the right start/duration",
              unmatched_truth)
        spurious = []
        for k, evs in by_stop.items():
            s = datetime.fromisoformat(k)
            dur = sum(e["duration_s"] or 0 for e in evs)
            if dur < THRESH:
                spurious.append(("short", s.strftime("%H:%M:%S"), dur))
            if not any(abs((s - real(a)).total_seconds()) <= TOL for a, _ in merged):
                spurious.append(("no truth stop", s.strftime("%H:%M:%S"), dur))
        check(not spurious, f"{name}: no event shorter than {THRESH:g} s and none without a real stop", spurious)
        if SB:
            in_brk = [(k[11:19], sum(e["duration_s"] or 0 for e in v)) for k, v in by_stop.items()
                      if any(bs <= datetime.fromisoformat(k) < be for bs, be in SB)]
            print(f"    planned stops entirely inside a server break: {len(planned)}"
                  f" ({[round((b - a) / SPEED) for a, b in planned]} s real)")
            check(planned and not in_brk, f"{name}: break stops ({len(planned)}) produced no event", in_brk)
        ws_ids = {}
        for msg in ws:
            if msg["event"]["machine_id"] == m["id"]:
                ws_ids.setdefault(msg["event"]["id"], []).append((msg["action"], msg["_rx"], msg["event"].get("end")))
        not_live = []
        for e in got:
            acts = ws_ids.get(e["id"], [])
            opened = [rx for a, rx, _ in acts if a == "open"]
            closed = [rx for a, rx, _ in acts if a == "close"]
            if not opened or not closed:
                not_live.append((e["start"][11:19], "open" if opened else "-", "close" if closed else "-"))
            elif e["end"] and datetime.fromisoformat(opened[0]) > datetime.fromisoformat(e["end"]):
                not_live.append((e["start"][11:19], "opened only after it ended"))
        check(not not_live, f"{name}: every event opened live and closed over the WebSocket", not_live)
        # micro-stops: whole run (hour-aligned window, D7), every truth stop < 120 s real counted once, none an event
        all_micro = [(a, b) for a, b in union_merged(stops) if DEBOUNCE <= (b - a) / SPEED < THRESH - TOL
                     and (b - a) / SPEED and b < sim_now - 30 * SPEED]
        w0 = t0.replace(minute=0, second=0, microsecond=0)
        o = httpx.get(f"{API}/api/oee", headers=h, params={"scope": "machine", "id": m["id"], "start": w0.isoformat(),
                                                            "stop": datetime.now(timezone.utc).isoformat()}).json()
        exp_loss = sum((b - a) / SPEED for a, b in all_micro)
        print(f"    micro-stops: truth {len(all_micro)} ({exp_loss:.1f} s) — server {o.get('micro_stop_count')}"
              f" ({o.get('micro_stop_loss_s')} s)")
        nb = sum(1 for a, b in union_merged(stops) if abs((b - a) / SPEED - THRESH) <= TOL)
        check(abs((o.get("micro_stop_count") or 0) - len(all_micro)) <= 1 + nb,
              f"{name}: micro-stop count = truth ±{1 + nb} (boundary + {nb} near-threshold stops)",
              (o.get("micro_stop_count"), len(all_micro)))
    print(f"\ntotal: truth events {tot_exp_ev}, server stops {tot_got_ev}")
    bad = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
