"""
Box ueaPowerDriver SIMULADO com o código REAL do box — para demonstrar/validar a coleta `ueapd` do gateway sem
os equipamentos (nunca aponta para a planta: escuta em 127.0.0.1 por padrão).

O que roda (tudo do repositório ueapowerdriver, SÓ LEITURA — nada é escrito lá; rode com `python -B`):
- o orquestrador de produção `AutoOrchestrator` (plc/app/auto/orchestrator.py) com o trace em disco ligado
  (`trace_dir`): os `passes-*.jsonl` e `manut-*.jsonl` saem no formato real, gravados pelo próprio TraceLog;
- a planta simulada do box: M1 = `M1Plant` (plc/sim/m1_plant.py, transporte por SW, layout de produção
  `hipot_v2_real`) em laço fechado com o orquestrador sobre o `FakeSvc` (coils em memória). M2: o laço fechado do
  repositório (plc/sim/sim.py) trava com o orquestrador atual, então o fluxo de pares é modelado aqui (ver M2Box)
  e o passe é aberto/fechado pelo próprio orquestrador ATS2 (`_begin_pass` / `_finish_pass` → TraceLog);
- a API HTTP real do box (`plc/app/api/auto.py`: /auto/state, /auto/trace/files, /auto/trace/pull, /auto/manut,
  /auto/trace) num uvicorn, com `app.state.auto` = esse orquestrador.

Por cima, este script faz o papel do mundo físico/testadores, em tempo real:
- alimenta pallets continuamente (M1 pela entrada; M2 em pares a cada 26–46 s, ~18 s/peça);
- "testadores": no fim de cada ensaio responde pela rota oficial `complete()` (a mesma do POST
  /auto/stage/{key}/complete) com NG numa fração (`--ng-pct`); sem NG o timer de ensaio (`auto_complete_s`) conclui;
- falhas do CLP: de tempos em tempos sobe uma coil de falha da linha (ERRO_CLAMP, ERRO_STOP, ... e GRAVADORA_ERRO no
  M1) por 30–300 s e a máquina sai do automático (MODO_AUTO baixo) até normalizar — o orquestrador grava as bordas
  `falha_clp`/`falha_clp_fim` no manut;
- pausas de operador (MODO_AUTO baixo por 1–3 min, `--pause-every-min`): gate.ok cai, run_state 0 sem falha.

Uso (venv do box: python3.13 + plc/requirements.txt):
    UEAPD_REPO=/home/user/ueapowerdriver \\
    <venv>/bin/python -B edge/tools/ueapd_box_sim.py --box m1 --port 18003 --trace-dir /tmp/ueapd/m1/trace
    ... --box m2 --port 18004 --trace-dir /tmp/ueapd/m2/trace
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import time

sys.dont_write_bytecode = True                 # nunca escrever __pycache__ no repositório do box

M1_LINE = "hipot_v2_real"                      # layout de produção do M1 (line.py)
M1_TEST_S = {"laser_1": 6.0, "laser_2": 6.0, "hipot_1": 7.0, "hipot_2": 10.3,
             "transito_e2p2": 0.5, "transito_e3p2": 0.5}
M2_TEST_S = {"leitura": 0.6, "ats2_1": 20.0, "ats2_2": 20.0, "funcional": 8.0}   # ATS: 10–43 s por par em campo
M1_NG_STAGES = {"hipot_1": None, "hipot_2": "hipot2"}          # estágio → source do complete()


def _prepare_env(work: str) -> None:
    """Tudo o que o código do box grava fora do trace vai para o diretório de trabalho (nunca ~/.config)."""
    for k, v in {"PLC_AUTO_CONFIG": "auto.json", "PLC_CONN_CONFIG": "plc-conn.json",
                 "PLC_TESTERS_CONFIG": "plc-testers.json", "PLC_PRODUCTS": "products.json",
                 "PLC_FUNC_LIMITS": "func_limits.json", "PLC_RESULTS_DIR": "resultados"}.items():
        os.environ.setdefault(k, os.path.join(work, v))
    os.environ.setdefault("PLC_DRIVER", "mock")
    os.environ.setdefault("PLC_SW_TRANSPORT_M1", "1")           # M1Plant exige o transporte por SW


class Box:
    """Base: orquestrador real + planta + testadores + falhas, num laço de tempo real."""

    def __init__(self, args) -> None:
        self.args = args
        self.rng = random.Random(args.seed)
        self.clock = {"t": 0.0}
        self.fault: tuple[str, float] | None = None              # (tag, fim)
        self.next_fault = self._exp(args.fault_every_min * 60)
        self.pause_until: float | None = None
        self.next_pause = self._exp(args.pause_every_min * 60) if args.pause_every_min > 0 else None
        self._ready_seen: dict[str, bool] = {}
        self._pending_ng: dict[str, float] = {}                   # estágio → instante do complete(NG)
        self.ng_stages: dict[str, str | None] = {}
        self.test_s: dict[str, float] = {}
        self.fed = 0

    def _exp(self, mean_s: float) -> float:
        return self.clock["t"] + self.rng.expovariate(1.0 / max(1.0, mean_s))

    # falhas e pausas (coils do FakeSvc)
    def _faults_and_pauses(self) -> bool:
        """Atualiza falha/pausa; True = máquina parada (não alimenta). A falha do CLP derruba o modo
        automático (a máquina para de forma controlada: o próprio orquestrador desliga motores/clamps no gate
        e retoma com resync) — congelar a física com o SW acionando motores viraria "pallet preso"."""
        t, svc = self.clock["t"], self.svc
        if self.fault is None and t >= self.next_fault and self.pause_until is None:
            tag = self.rng.choice(sorted(self.orch.line.get("faults") or {}))
            dur = self.rng.uniform(30, 300)     # > 120 s vira parada no servidor; abaixo, micro-parada
            self.fault = (tag, t + dur)
            svc.set(tag, 1)
            svc.set("MODO_AUTO", 0)
            print(f"[{self.args.box}] falha do CLP {tag} por {dur:.0f}s", flush=True)
        elif self.fault is not None and t >= self.fault[1]:
            svc.set(self.fault[0], 0)
            svc.set("MODO_AUTO", 1)
            print(f"[{self.args.box}] falha {self.fault[0]} normalizada", flush=True)
            self.fault = None
            self.next_fault = self._exp(self.args.fault_every_min * 60)
        if self.next_pause is not None and self.pause_until is None and t >= self.next_pause and self.fault is None:
            self.pause_until = t + self.rng.uniform(60, 180)
            svc.set("MODO_AUTO", 0)
            print(f"[{self.args.box}] pausa do operador (MODO_AUTO=0) por {self.pause_until - t:.0f}s", flush=True)
        elif self.pause_until is not None and t >= self.pause_until:
            svc.set("MODO_AUTO", 1)
            self.pause_until = None
            self.next_pause = self._exp(self.args.pause_every_min * 60)
            print(f"[{self.args.box}] retomada (MODO_AUTO=1)", flush=True)
        return self.fault is not None or self.pause_until is not None

    # testadores: NG pela rota oficial complete()
    def _testers(self) -> None:
        t = self.clock["t"]
        for st in self.orch.stages:
            k = st.key
            ready = st.phase == "ready"
            if ready and not self._ready_seen.get(k) and k in self.ng_stages \
                    and self.rng.random() < self.args.ng_pct / 100.0:
                self._pending_ng[k] = t + 0.6 * self.test_s.get(k, 5.0)
            self._ready_seen[k] = ready
            due = self._pending_ng.get(k)
            if due is not None and t >= due:
                self._pending_ng.pop(k, None)
                if not ready:
                    continue
                n = len(st.presence)
                bad = self.rng.randrange(n)
                results = [{"ok": i != bad, "detail": {"resultado": "Fail" if i == bad else "Pass",
                                                       "fonte": "ueapd_box_sim"}} for i in range(n)]
                src = self.ng_stages[k]
                self.orch.complete(k, results, source=src)
                for other in (st.spec.get("complete_sources") or []):
                    if other != src:                                   # co-locado: as outras fontes OK
                        self.orch.complete(k, [{"ok": True} for _ in range(n)], source=other)


class M1Box(Box):
    def __init__(self, args) -> None:
        super().__init__(args)
        from plc.sim.fake_svc import FakeSvc
        from plc.sim.m1_plant import M1Plant
        from plc.app.auto import transport_m1 as TM
        from plc.app.auto.orchestrator import AutoOrchestrator
        from plc.app.auto.line import LINES
        TM.ENABLED = True
        self.svc = FakeSvc(M1_LINE)
        self.orch = AutoOrchestrator(self.svc, M1_LINE, config_path=None, clock=lambda: self.clock["t"],
                                     trace_dir=args.trace_dir)
        self.orch._state_path = None
        self.orch.set_config({"enabled": True, "read_error_random": True, "auto_complete_s": dict(M1_TEST_S),
                              "release_hold_s": {k: 0.5 for k in M1_TEST_S}, "release_stagger_s": 0.0,
                              "engage_settle_s": 0.8, "results_dir": ""})
        for g in ("MODO_AUTO", "SAFETY_OK", "EMERG_OK"):
            self.svc.set(g, 1)
        self.svc.set("SMEMA_M2_PRONTO", 1)
        tm = self.tm = self.orch._transport_m1
        tm.start_fast_loop = lambda: None                 # o passo rápido é chamado daqui (como no gabarito)
        real_write = self.orch._write_coil

        async def write_with_echo(tag, value):            # eco fiel dos motores (ladder novo)
            ok = await real_write(tag, value)
            for bl in tm.belts:
                if tag == bl.motor and bl.echo:
                    self.svc.set(bl.echo, int(bool(value)))
            return ok
        self.orch._write_coil = write_with_echo
        self.plant = M1Plant(self.svc, LINES[M1_LINE], stop_down_value=tm.stop_down_value)
        self.ng_stages, self.test_s = dict(M1_NG_STAGES), dict(M1_TEST_S)
        self._next_tick = 0.0
        self._next_feed = 0.0

    async def step(self, dt: float) -> None:
        t = self.clock["t"]
        stopped = self._faults_and_pauses()
        if not stopped and t >= self._next_feed and self.svc.get("ACION_ESTEIRA_INPUT_MAN") == 0 \
                and sum(1 for p in self.plant.pallets if not p.out) < 6 and self.plant.feed(self.fed):
            self.fed += 1
            self._next_feed = t + self.rng.uniform(2.0, 6.0)
        self.plant.advance(dt, self.orch)
        if t >= self._next_tick:
            await self.orch.tick()
            self._next_tick = t + 0.3
        else:
            await self.tm.step(await self.orch._read(), t)
        self._testers()
        self.plant.pallets = [p for p in self.plant.pallets if not p.out]
        del self.plant.exits[:-50]
        self.clock["t"] = round(t + dt, 3)


class M2Box(Box):
    """M2 (ATS2). O laço fechado do repositório do box (plc/sim/sim.py, "CLP ideal") não roda contínuo com o
    orquestrador atual (trava depois do 1º par; o 2º pallet do par sai sem funcional) — então aqui o FLUXO
    físico é modelado por este script e o resto é o código real: o orquestrador ATS2 (FakeSvc com gate e coils
    de falha, `tick()` real → /auto/state e as bordas `falha_clp` no manut), a abertura/fechamento do passe
    (`_begin_pass` / `_finish_pass` → TraceLog real) e os contadores de turno/cadência do box.
    Fluxo: pares chegam; leitor (posto 2 da entrada); ATS2-2 é o testador prioritário, ATS2-1 só no overflow
    (o par testado lá cruza o ATS2-2 com o evento de passagem `skip`); funcional; NG no ATS2 → funcional
    "pulado" (skip_after_ng). Eventos com os mesmos campos do orquestrador (stage, t, ok, posto, detail, skip)."""

    def __init__(self, args) -> None:
        super().__init__(args)
        from plc.sim.fake_svc import FakeSvc
        from plc.app.auto.orchestrator import AutoOrchestrator
        self.svc = FakeSvc("ats2")
        self.orch = AutoOrchestrator(self.svc, "ats2", config_path=None, clock=lambda: self.clock["t"],
                                     trace_dir=args.trace_dir)
        self.orch._state_path = None
        self.orch.set_config({"enabled": True, "results_dir": "", "auto_complete_s": dict(M2_TEST_S)})
        for g in ("MODO_AUTO", "SAFETY_OK", "EMERG_OK"):
            self.svc.set(g, 1)
        self.ng_stages, self.test_s = {}, dict(M2_TEST_S)
        self.day = time.strftime("%y%m%d")
        self.busy: dict[str, float] = {"ats2_1": 0.0, "ats2_2": 0.0, "funcional": 0.0, "leitura": 0.0}
        self.flows: list[dict] = []
        self.next_pair = 0.0
        self._next_tick = 0.0

    def _code(self) -> str:
        self.fed += 1
        return f"PD{self.day}{self.fed:05d}"

    def _ev(self, code: str, ev: dict) -> None:
        rec = self.orch._active_pass.get(code)
        if rec is not None:
            rec["events"].append({"t": time.time(), **ev} if "t" not in ev else ev)

    def _ats_detail(self, st: str, ok: bool) -> dict:
        nome = f"{time.strftime('%Y%m%d_%H%M%S')}_{st.upper()}_ATS.xlsx"
        return {"resultado": "Pass" if ok else "Fail", "arquivo": nome,
                "caminho": f"C:/ATS/Reports/{nome}", "modificado": time.time()}

    def _advance_flow(self, f: dict, t: float) -> bool:
        """Avança um par pelas estações quando cada etapa vence. True = par terminou."""
        o, rng, ng = self.orch, self.rng, self.args.ng_pct / 100.0
        if t < f["due"]:
            return False
        step = f["step"]
        if step == "read":
            for c in f["codes"]:
                o._begin_pass(c)
                self._ev(c, {"stage": "leitura", "ok": True, "posto": 1})
            f["step"], f["due"] = "ats", t + 2.0
        elif step == "ats":
            st = "ats2_2" if self.busy["ats2_2"] <= t else ("ats2_1" if self.busy["ats2_1"] <= t else None)
            if st is None:
                f["due"] = t + 0.5                       # os dois ATS ocupados: espera na esteira
                return False
            dur = self.test_s[st] * rng.uniform(0.6, 1.6)
            self.busy[st] = t + dur + 2.0
            f.update(step="ats_done", st=st, due=t + dur)
        elif step == "ats_done":
            st = f["st"]
            for i, c in enumerate(f["codes"]):
                ok = rng.random() >= ng
                f["ok"][c] = ok
                f["why"][c] = [] if ok else [f"NG no ATS2 ({o._by_key[st].label})"]
                self._ev(c, {"stage": st, "ok": ok, "posto": i, "detail": self._ats_detail(st, ok)})
            if st == "ats2_1":                           # cruza o ATS2-2 sem testar (passagem)
                for c in f["codes"]:
                    self._ev(c, {"stage": "ats2_2", "ok": None, "skip": "já testado no outro ATS2 (passagem)"})
            f["step"], f["due"] = "func", t + 3.0
        elif step == "func":
            if self.busy["funcional"] > t:
                f["due"] = t + 0.5
                return False
            dur = self.test_s["funcional"] * rng.uniform(0.9, 1.2)
            self.busy["funcional"] = t + dur + 1.5
            f["step"], f["due"] = "func_done", t + dur
        elif step == "func_done":
            for i, c in enumerate(f["codes"]):
                if not f["ok"][c]:                       # skip_after_ng: funcional não executado
                    motivo = f"reprovado em {o._by_key[f['st']].label} — Funcional não executado"
                    self._ev(c, {"stage": "funcional", "ok": False, "posto": i, "skip": motivo,
                                 "detail": {"tipo": "pulado", "pulado": "Funcional", "motivo": motivo}})
                    continue
                ok = rng.random() >= ng / 2
                f["ok"][c] = ok
                if not ok:
                    f["why"][c] = [f"NG no funcional P{i + 1}"]
                self._ev(c, {"stage": "funcional", "ok": ok, "posto": i})
            tnow = time.time()
            for c in f["codes"]:
                rec = o._active_pass.pop(c, None)
                if rec is None:
                    continue
                rec["verdict"] = {"ok": f["ok"][c], "reasons": f["why"][c]}
                o._finish_pass(rec, tnow)                # TraceLog real: uma linha no passes-*.jsonl
                if f["ok"][c]:
                    o._shift_ok += 1
                else:
                    o._shift_ng += 1
                o._marca_saida()
            return True
        return False

    async def step(self, dt: float) -> None:
        t = self.clock["t"]
        stopped = self._faults_and_pauses()
        if t >= self._next_tick:
            await self.orch.tick()                       # gate, falhas (bordas no manut), estado do /auto/state
            self._next_tick = t + 0.3
        if not stopped:
            if t >= self.next_pair and len(self.flows) < 3:
                self.flows.append({"codes": [self._code(), self._code()], "step": "read", "due": t,
                                   "ok": {}, "why": {}})
                self.next_pair = t + self.rng.uniform(26.0, 46.0)     # ~18 s por peça (par a cada ~36 s)
            self.flows = [f for f in self.flows if not self._advance_flow(f, t)]
        else:                                            # parada: tudo empurra o prazo (nada anda)
            for f in self.flows:
                f["due"] += dt
            for k in self.busy:
                self.busy[k] += dt
            self.next_pair += dt
        self.clock["t"] = round(t + dt, 3)


def build_app(box: Box):
    from fastapi import FastAPI
    from plc.app.api.auto import router as auto_router
    app = FastAPI(title=f"ueaPowerDriver {box.args.box.upper()} (simulado — ueapd_box_sim)")
    app.include_router(auto_router)
    app.state.auto = box.orch
    app.state.trace_sync = None

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "driver": "sim", "box": box.args.box, "trace_dir": box.args.trace_dir}
    return app


async def main_async(args) -> None:
    import uvicorn
    box = M1Box(args) if args.box == "m1" else M2Box(args)
    server = uvicorn.Server(uvicorn.Config(build_app(box), host=args.host, port=args.port, log_level="warning"))
    dt = args.dt

    async def plant_loop():
        nxt = time.monotonic()
        last_report = 0.0
        while not server.should_exit:
            try:
                await box.step(dt * args.speed)
            except Exception as e:  # noqa: BLE001 — a demo segue; o erro aparece no log
                print(f"[{args.box}] erro no passo da planta: {type(e).__name__}: {e}", flush=True)
            if time.monotonic() - last_report > 60:
                last_report = time.monotonic()
                st = box.orch.state()
                print(f"[{args.box}] turno ok={st['shift']['ok']} ng={st['shift']['ng']} "
                      f"cadência={st['cadencia'].get('ph')} p/h armado={st['armed']} gate={st['gate'].get('ok')} "
                      f"falhas={[f['tag'] for f in st['faults']]}", flush=True)
            nxt += dt
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))

    print(f"[{args.box}] box simulado em http://{args.host}:{args.port} trace={args.trace_dir}", flush=True)
    await asyncio.gather(server.serve(), plant_loop())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--box", choices=("m1", "m2"), required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1", help="padrão: só localhost (nunca a rede da planta)")
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--work-dir", default=None, help="configs do box (padrão: <trace-dir>/..)")
    ap.add_argument("--repo", default=os.environ.get("UEAPD_REPO", "/home/user/ueapowerdriver"))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--ng-pct", type=float, default=4.0, help="%% de ensaios NG por testador")
    ap.add_argument("--fault-every-min", type=float, default=12.0, help="média entre falhas do CLP (min)")
    ap.add_argument("--pause-every-min", type=float, default=40.0, help="média entre pausas (0 = sem pausas)")
    ap.add_argument("--dt", type=float, default=None,
                    help="passo (s reais); padrão M1 0,1 (física do M1Plant) e M2 0,3 (tick do Sim do ATS2)")
    ap.add_argument("--speed", type=float, default=1.0, help="aceleração do relógio da planta (1 = tempo real)")
    args = ap.parse_args(argv)
    args.dt = args.dt or (0.1 if args.box == "m1" else 0.3)
    args.trace_dir = os.path.abspath(args.trace_dir)
    work = os.path.abspath(args.work_dir or os.path.dirname(args.trace_dir))
    os.makedirs(args.trace_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    _prepare_env(work)
    if args.repo not in sys.path:
        sys.path.insert(0, args.repo)
    logging.getLogger("uea").setLevel(logging.WARNING)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
