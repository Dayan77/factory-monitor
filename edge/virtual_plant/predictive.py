"""
Playback do cenário de ouro F (preditiva, docs/scenarios/scenario_f_predictive.json) na planta virtual.

    python -m edge.virtual_plant --scenario docs/scenarios/scenario_f_predictive.json --speed 500
    python -m edge.virtual_plant --scenario docs/scenarios/scenario_f_predictive.json --scenario-cases F2 --speed 3000

O que é servido (predictive.md P-1, P-11), sempre no **relógio do cenário** (SourceTimestamp OPC UA deslocado um
número inteiro de SEMANAS para o passado: hora local e dia da semana preservados, o plano de produção de P-7 vale):

- **F1 — Estação de Fixação › Cilindro B3** (`montagem.fixacao.b3.*`): um golpe a cada `86 400 / cycles_per_day`
  s desde `installed_at`; a cada golpe o terminal de válvulas escreve o **tempo de curso do golpe** (`stroke_time`)
  e depois o **contador** (`b3.cycles`, 32 bits, parte de 0). Os golpes do bloco i (ciclos `(i−1)·N+1 … i·N`)
  valem `x_i ± d`, com os desvios `d` (0 … 3,0 ms) em pares simétricos e ≥ 2 % dos golpes exatamente em `x_i`:
  a **mediana do bloco é exatamente `x_i`** do JSON (P-1, agregado `median`). O bloco i termina no golpe `i·N`
  (contador = i·N), em `installed_at + i · hours_per_window`. No fim do bloco `failure.window` o CLP declara a
  falha `447` (4xx pneumática → `EQP-03`, modo ISO 14224 `PDE`) na Estação de Fixação e o cilindro para.
- **F2 — Transportador de Saída › Motor B** (`montagem.transportador.motor_b.*`): dia i = `[first_day 00:00
  local + (i−1) dias, +24 h)`; o motor roda `running_h` horas a partir das 02:00 local (20 h → 02:00–22:00) com a
  corrente RMS **constante = `x_i`** (a média ponderada no tempo só com `run_state = 1` dá exatamente `x_i`);
  `running_h = 0` (dia 47, feriado) → parado o dia todo (janela inválida). Vibração RMS/pico constantes (saudável).
- Heartbeat do dispositivo (padrão a cada 10 s do cenário): re-escreve run_state/fault_state/corrente/vibração
  com o mesmo valor e SourceTimestamp novo (o servidor considera "sem dados" depois de 60 s). Tempo de curso e
  contador não têm heartbeat: são eventos por golpe.

As duas máquinas do cenário são servidas em **OPC UA** (`OPCUA_MACHINES`): o Modbus não carrega timestamp e o
playback acelerado precisa do instante de cada golpe. Gere o mapa do gateway do playback com
`python -m edge.virtual_plant --write-map /tmp/plant.f.json --scenario docs/scenarios/scenario_f_predictive.json`
(as tags dessas máquinas saem na fonte `opcua-plc`). No gateway: `VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off`.

Vazão: dois itens OPC UA por golpe. A fila de cada item assinado é de 64 notificações por publicação (250 ms);
o modelo entrega no máximo `max_burst` (25) golpes por passo de 100 ms do runtime → ≤ 63 por publicação. Acima de
~860× o F1 atrasa (aviso `virtual_plant.lagging`), mas nada se perde. F1 (35 dias, 700 000 golpes) a 500× ≈ 1 h 40.

Verdade de campo (`truth()`; `GET /truth` na API de controle, `GET :8091/state` → `truth`): por caso o perfil, o
início da degradação, a falha real (instante no relógio do dispositivo — o que o servidor vê — e no do JSON),
o estado verdadeiro agora (valor nominal sem ruído, fração da degradação, vida remanescente real) e o bloco
`expected.evaluation` do JSON, para conferir `GET /api/predictive/evaluation`.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from .layout import PLANTA_MANAUS, PlantLayout, fault_category, signal_key
from .scenario import DAY, ScenarioClock, ScenarioError, Write, _parse_ts

# Posição do JSON → (máquina, posição no layout, sinais por papel)
POSITION_CODES: dict[str, dict] = {
    "POS-B3": {"machine": "montagem.fixacao", "position": "Cilindro B3",
               "signals": {"stroke_time": "b3.stroke_time", "counter": "b3.cycles"}, "failure_code": 447},
    "POS-MOTB": {"machine": "montagem.transportador", "position": "Motor B",
                 "signals": {"run_state": "motor_b.run", "current": "motor_b.current",
                             "vibration": "motor_b.vibration", "vibration_peak": "motor_b.vibration_peak"}},
}
OPCUA_MACHINES = frozenset({"montagem.fixacao", "montagem.transportador"})
RUN_START_LOCAL_H = 2.0              # F2: o motor liga às 02:00 local (20 h → até 22:00)
VIBRATION_RUNNING = (1.30, 3.12)     # mm/s RMS, pico (saudável, constantes no playback)
VIBRATION_STOPPED = (0.05, 0.12)
MAX_STROKE_OFFSET_STEPS = 31         # desvios por golpe: 0,0 … 3,0 ms em passos de 0,1


def stroke_offset(j: int) -> float:
    """Desvio do golpe j (0-based) dentro do bloco: pares (+d, −d) — multiconjunto simétrico, mediana 0 —, com
    d = 0 em 1 de cada 31 pares (os valores exatamente iguais a x ficam no meio da ordenação)."""
    p = j // 2
    d = 0.1 * ((p * 7919) % MAX_STROKE_OFFSET_STEPS)
    return d if j % 2 == 0 else -d


@dataclass
class CycleCase:
    id: str
    key_prefix: str            # "montagem.fixacao"
    stroke_key: str
    counter_key: str
    run_key: str
    fault_key: str
    t_install: float           # s desde o início do playback
    period_s: float            # um golpe a cada period_s
    block: int                 # golpes por bloco
    x: dict[int, float]        # bloco → mediana do tempo de curso
    last_window: int
    failure_window: Optional[int]
    failure_code: Optional[int]
    doc: dict

    @property
    def n_cycles(self) -> int:
        return self.last_window * self.block

    def block_end(self, i: int) -> float:
        return self.t_install + i * self.block * self.period_s

    def nominal(self, i: int) -> float:
        g = self.doc.get("generator") or {}
        base = float(g.get("base", 0.0))
        return base + float(g.get("drift_per_window", 0.0)) * max(0, i - int(g.get("drift_after_window", 0)))

    def writes(self) -> Iterator[Write]:
        yield Write(self.t_install, self.run_key, True)
        yield Write(self.t_install, self.fault_key, 0)
        for k in range(1, self.n_cycles + 1):
            t = round(self.t_install + k * self.period_s, 3)
            i = (k - 1) // self.block + 1
            yield Write(t, self.stroke_key, round(self.x[i] + stroke_offset((k - 1) % self.block), 1))
            yield Write(t, self.counter_key, k)
        if self.failure_window is not None:
            t = round(self.block_end(self.failure_window), 3)
            yield Write(t, self.fault_key, self.failure_code)
            yield Write(t, self.run_key, False)


@dataclass
class TimeCase:
    id: str
    run_key: str
    current_key: str
    vib_key: str
    peak_key: str
    t_day1: float              # início do dia 1 (s desde o início do playback)
    days: dict[int, tuple[Optional[float], float]]   # dia → (x, horas rodando)
    last_day: int
    doc: dict

    def day_start(self, i: int) -> float:
        return self.t_day1 + (i - 1) * 86400.0

    def run_interval(self, i: int) -> Optional[tuple[float, float]]:
        x, h = self.days.get(i, (None, 0.0))
        if not h or x is None:
            return None
        a = self.day_start(i) + RUN_START_LOCAL_H * 3600.0
        return a, a + h * 3600.0

    def writes(self) -> Iterator[Write]:
        for i in range(1, self.last_day + 1):
            iv = self.run_interval(i)
            if iv is None:
                continue
            a, b = iv
            x = self.days[i][0]
            yield Write(a, self.run_key, True)
            yield Write(a, self.current_key, float(x))
            yield Write(a, self.vib_key, VIBRATION_RUNNING[0])
            yield Write(a, self.peak_key, VIBRATION_RUNNING[1])
            yield Write(b, self.run_key, False)
            yield Write(b, self.current_key, 0.0)
            yield Write(b, self.vib_key, VIBRATION_STOPPED[0])
            yield Write(b, self.peak_key, VIBRATION_STOPPED[1])

    def nominal(self, i: int) -> float:
        g = self.doc.get("generator") or {}
        after = int(g.get("drift_after_day", 0))
        return float(g.get("base", 0.0)) + float(g.get("drift_per_day", 0.0)) * min(
            max(0, i - after), int(g.get("drift_days", 10 ** 9)))


class PredictiveScenario:
    """Cenário F carregado: casos, instantes e geradores de escrita (sem I/O)."""

    def __init__(self, doc: dict, layout: PlantLayout = PLANTA_MANAUS, cases: Optional[list[str]] = None) -> None:
        if "cases" not in doc:
            raise ScenarioError("cenário sem 'cases' (não é um cenário preditivo)")
        self.doc = doc
        self.id = doc.get("id", "scenario_f")
        self.layout = layout
        self.utc_offset_h = float((doc.get("plant") or {}).get("utc_offset_h", 0))
        wanted = cases or sorted(doc["cases"])
        unknown = [c for c in wanted if c not in doc["cases"]]
        if unknown:
            raise ScenarioError(f"caso(s) inexistente(s) no cenário: {unknown} (há {sorted(doc['cases'])})")
        self.case_ids = wanted
        self._keys = {signal_key(line, m, s) for line, m in layout.machines() for s in m.signals}
        # instantes absolutos de cada caso → início do playback = o mais cedo
        starts: dict[str, datetime] = {}
        for cid in wanted:
            c = doc["cases"][cid]
            if (c.get("window") or {}).get("kind") == "cycles":
                starts[cid] = _parse_ts(c["installed_at"])
            else:
                starts[cid] = self._local_midnight(c["first_day"])
        self.start = min(starts.values())
        self.cycle_cases: list[CycleCase] = []
        self.time_cases: list[TimeCase] = []
        for cid in wanted:
            c = doc["cases"][cid]
            pos = POSITION_CODES.get(c.get("position"))
            if pos is None:
                raise ScenarioError(f"{cid}: posição {c.get('position')!r} sem correspondência no layout")
            sig = {role: f"{pos['machine']}.{suffix}" for role, suffix in pos["signals"].items()}
            for k in sig.values():
                if k not in self._keys:
                    raise ScenarioError(f"{cid}: sinal {k} não existe no layout")
            t0 = (starts[cid] - self.start).total_seconds()
            g = c.get("generator") or {}
            if (c.get("window") or {}).get("kind") == "cycles":
                block = int(c["window"]["cycles"])
                period = 86400.0 / float(g["cycles_per_day"])
                if abs(block * period - float(g.get("hours_per_window", block * period / 3600)) * 3600) > 1e-6:
                    raise ScenarioError(f"{cid}: cycles_per_day e hours_per_window não batem")
                x = {int(w["i"]): float(w["x"]) for w in c["windows"]}
                last = int(g.get("last_window", max(x)))
                if sorted(x) != list(range(1, last + 1)):
                    raise ScenarioError(f"{cid}: janelas 1…{last} incompletas")
                fail = c.get("failure") or None
                cc = CycleCase(cid, pos["machine"], sig["stroke_time"], sig["counter"], f"{pos['machine']}.run_state",
                               f"{pos['machine']}.fault_state", t0, period, block, x, last,
                               int(fail["window"]) if fail else None, pos.get("failure_code") if fail else None, c)
                if fail:
                    exp = (_parse_ts(fail["failed_at"]) - self.start).total_seconds()
                    if abs(cc.block_end(cc.failure_window) - exp) > 1e-3:
                        raise ScenarioError(f"{cid}: failed_at não é o fim do bloco {cc.failure_window}")
                    fault_category(cc.failure_code)
                self.cycle_cases.append(cc)
            else:
                days = {int(w["i"]): (None if w.get("x") is None else float(w["x"]),
                                      float(w.get("running_h", g.get("running_hours_per_day", 0.0)) or 0.0))
                        for w in c["windows"]}
                last = int(g.get("last_day", max(days)))
                self.time_cases.append(TimeCase(cid, sig["run_state"], sig["current"], sig["vibration"],
                                                sig["vibration_peak"], t0, days, last, c))
        ends = [cc.block_end(cc.last_window) for cc in self.cycle_cases] + \
               [tc.day_start(tc.last_day + 1) for tc in self.time_cases]
        self.duration_s = max(ends)
        self.end = self.start + timedelta(seconds=self.duration_s)
        self.machine_keys = sorted({cc.key_prefix for cc in self.cycle_cases} |
                                   {tc.run_key.rsplit(".", 2)[0] for tc in self.time_cases})
        self.heartbeat_keys = sorted(
            [k for cc in self.cycle_cases for k in (cc.run_key, cc.fault_key)] +
            [k for tc in self.time_cases for k in (tc.run_key, tc.current_key, tc.vib_key, tc.peak_key)] +
            [f"{m}.fault_state" for m in self.machine_keys
             if f"{m}.fault_state" in self._keys and m not in {cc.key_prefix for cc in self.cycle_cases}])

    @classmethod
    def load(cls, path: str | Path, layout: PlantLayout = PLANTA_MANAUS,
             cases: Optional[list[str]] = None) -> "PredictiveScenario":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), layout, cases)

    def _local_midnight(self, day: str) -> datetime:
        d = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        return d - timedelta(hours=self.utc_offset_h)

    def writes(self) -> Iterator[Write]:
        """Todas as escritas, em ordem de instante (empate: ordem dos casos e, dentro do caso, de geração)."""
        streams = [c.writes() for c in self.cycle_cases] + [c.writes() for c in self.time_cases]
        return heapq.merge(*streams, key=lambda w: w.t)

    def installations(self, clock: ScenarioClock) -> dict[tuple[str, str], str]:
        """Instalação de cada componente do cenário no relógio do dispositivo (ISO-8601), para o provisionamento:
        F1 = `installed_at`, F2 = início do dia 1 (rolamentos trocados) — o reinício da linha de base (P-2)."""
        out = {}
        for cc in self.cycle_cases:
            pos = POSITION_CODES[cc.doc["position"]]
            out[(pos["machine"], pos["position"])] = clock.device_time(cc.t_install).isoformat()
        for tc in self.time_cases:
            pos = POSITION_CODES[tc.doc["position"]]
            out[(pos["machine"], pos["position"])] = clock.device_time(tc.t_day1).isoformat()
        return out

    def default_shift_days(self, now: Optional[datetime] = None) -> int:
        """Menor múltiplo de 7 dias que deixa o fim do cenário ≤ agora (preserva o dia da semana)."""
        now = now or datetime.now(timezone.utc)
        days = max(0, math.ceil((self.end - now) / DAY))
        return 7 * math.ceil(days / 7)


class PredictiveScenarioModel:
    """Adaptador do cenário F para o runtime (mesma interface do ScenarioModel: mudanças com instante exato)."""

    def __init__(self, scenario: PredictiveScenario, clock: Optional[ScenarioClock] = None, seed: int = 0,
                 heartbeat_s: float = 10.0, max_burst: int = 25) -> None:
        self.scenario = scenario
        self.layout = scenario.layout
        self.seed = seed
        self.opcua_machines = OPCUA_MACHINES
        self.clock = clock or ScenarioClock(scenario, "shifted", scenario.default_shift_days())
        self.heartbeat_s = heartbeat_s
        self.max_burst = max_burst
        self._next_hb = heartbeat_s if heartbeat_s > 0 else math.inf
        self._burst_keys = {cc.stroke_key for cc in scenario.cycle_cases}
        self._it = scenario.writes()
        self._peek: Optional[Write] = next(self._it, None)
        self._t = 0.0
        self._last_t = 0.0
        self.applied = 0
        self._pending: list[Write] = []
        self._current = self.idle_values()
        self._last_cycle: dict[str, int] = {cc.id: 0 for cc in scenario.cycle_cases}
        self._apply_until(0.0)
        self._pending.clear()            # o estado inicial vai no push forçado

    # ── interface do runtime ──────────────────────────────────────────────
    def idle_values(self) -> dict[str, object]:
        v: dict[str, object] = {}
        plc = {p.code: p.plc_code for p in self.layout.products}
        for line, m in self.layout.machines():
            default = next((mp.product for mp in m.products if mp.is_default), None)
            for s in m.signals:
                k = signal_key(line, m, s)
                if s.kind == "bool":
                    v[k] = False
                elif s.kind == "float":
                    v[k] = 0.0
                elif s.role == "product_code":
                    v[k] = plc.get(default, 0)
                else:
                    v[k] = 0
        for tc in self.scenario.time_cases:
            v[tc.vib_key], v[tc.peak_key] = VIBRATION_STOPPED
        return v

    @property
    def sim_time_s(self) -> float:
        return round(self._t, 3)

    @property
    def finished(self) -> bool:
        return self._t >= self.scenario.duration_s

    def _apply_until(self, t: float, budget: Optional[list[int]] = None) -> bool:
        """Aplica as escritas com instante ≤ t. Com `budget` ([n]), no máximo n golpes; esgotado → False."""
        while self._peek is not None and self._peek.t <= t:
            x = self._peek
            if budget is not None and x.key in self._burst_keys:
                if budget[0] <= 0:
                    return False
                budget[0] -= 1
            self._current[x.key] = x.value
            self._pending.append(x)
            self._last_t = x.t
            self.applied += 1
            self._peek = next(self._it, None)
        return True

    def advance_to(self, sim_time_s: float, max_ticks: Optional[int] = None) -> int:
        """Avança até `sim_time_s` (nunca volta). Com `max_ticks` (runtime), no máximo `max_burst` golpes por
        chamada: o tempo do modelo para no último golpe entregue e a próxima chamada continua dali."""
        t = min(max(self._t, sim_time_s), self.scenario.duration_s)
        budget = [self.max_burst] if max_ticks is not None else None
        n = len(self._pending)
        while True:
            hb = self._next_hb
            if not self._apply_until(min(hb, t), budget):
                self._t = max(self._t, self._last_t)
                return len(self._pending) - n
            if hb > t:
                break
            self._pending.extend(Write(hb, k, self._current[k]) for k in self.scenario.heartbeat_keys)
            self._last_t = hb
            self._next_hb += self.heartbeat_s
        self._t = t
        return len(self._pending) - n

    def run_for(self, seconds: float) -> None:
        self.advance_to(self._t + seconds)

    def drain_changes(self) -> list[tuple[float, str, object]]:
        out = [(x.t, x.key, x.value) for x in self._pending]
        self._pending.clear()
        return out

    def device_time(self, t: float) -> datetime:
        return self.clock.device_time(t)

    def values(self, include_analog: bool = True) -> dict[str, object]:
        return dict(self._current)

    def pulse_targets(self) -> dict[str, int]:
        return {signal_key(line, m, s): 0 for line, m in self.layout.machines() for s in m.signals
                if s.role == "cycle_pulse"}

    def snapshot(self) -> dict:
        sc = self.scenario
        return {
            "seed": self.seed,
            "sim_time_s": self.sim_time_s,
            "values": self.values(),
            "scenario": {
                "id": sc.id, "kind": "predictive", "cases": sc.case_ids, "machines": sc.machine_keys,
                "machine": ", ".join(sc.machine_keys), "duration_s": sc.duration_s, "finished": self.finished,
                "writes_applied": self.applied, "device_now": self.device_time(self._t).isoformat(),
                **self.clock.info(),
            },
            "truth": self.truth(),
        }

    # ── verdade de campo ──────────────────────────────────────────────────
    def _at(self, t: Optional[float]) -> Optional[str]:
        return None if t is None else self.device_time(t).isoformat()

    def _scenario_at(self, t: Optional[float]) -> Optional[str]:
        return None if t is None else (self.scenario.start + timedelta(seconds=t)).isoformat()

    def truth(self) -> dict:
        """Estado verdadeiro da degradação e falhas reais (predictive.md P-10/P-11), no relógio do dispositivo
        (`*_at`, o que o servidor recebe) e no do JSON (`scenario_*_at`)."""
        sc = self.scenario
        now = self._t
        degr, failures = [], []
        for cc in sc.cycle_cases:
            c = cc.doc
            pos = POSITION_CODES[c["position"]]
            g = c.get("generator") or {}
            onset_w = int(g.get("drift_after_window", 0))
            onset_s = cc.block_end(onset_w)
            fail_s = cc.block_end(cc.failure_window) if cc.failure_window else None
            cycles = 0 if now < cc.t_install else min(cc.n_cycles, int((now - cc.t_install) / cc.period_s + 1e-9))
            i = max(1, min(cc.last_window, -(-cycles // cc.block)))     # bloco do último golpe
            base = float(g.get("base", 0.0))
            fl = c.get("failure_limit") or {}
            limit = float(fl.get("value", 0.0)) * (base if fl.get("relative_to_baseline") else 1.0)
            failed = fail_s is not None and now >= fail_s
            nominal = cc.nominal(i)
            rul = None if fail_s is None else max(0, cc.failure_window * cc.block - cycles)
            per_day = 86400.0 / cc.period_s
            degr.append({
                "case": cc.id, "position": f"{pos['machine']}.{pos['signals']['stroke_time'].split('.')[0]}",
                "position_code": c["position"], "machine": pos["machine"], "position_name": pos["position"],
                "feature": c["feature"], "unit": c.get("unit"), "direction": c.get("direction"),
                "profile": "linear_after_onset", "measure": "cycles",
                "onset_window": onset_w, "onset_s": onset_s, "onset_at": self._at(onset_s),
                "scenario_onset_at": self._scenario_at(onset_s),
                "failure_s": fail_s, "failure_at": self._at(fail_s), "scenario_failure_at": self._scenario_at(fail_s),
                "failure_limit": limit,
                "state": "failed" if failed else ("degrading" if now > onset_s else "healthy"),
                "now": {"cycles": cycles, "window": i, "nominal": nominal,
                        "scenario_x": cc.x.get(i), "degradation_frac": round(
                            (nominal - base) / (limit - base), 6) if limit != base else None,
                        "true_rul_cycles": None if failed else rul,
                        "true_rul_days": None if (failed or rul is None) else round(rul / per_day, 4)},
            })
            if cc.failure_window is not None:
                f = c["failure"]
                failures.append({
                    "case": cc.id, "position": degr[-1]["position"], "position_code": c["position"],
                    "machine": pos["machine"], "position_name": pos["position"], "fault_code": cc.failure_code,
                    "reason_code": fault_category(cc.failure_code)["reason_code"],
                    "failure_mode": f.get("failure_mode"), "failure_mechanism": f.get("failure_mechanism"),
                    "failure_cause": f.get("failure_cause"), "severity": f.get("severity"),
                    "window": cc.failure_window, "failure_s": fail_s, "failed_at": self._at(fail_s),
                    "scenario_failed_at": f.get("failed_at"), "occurred": failed})
        for tc in sc.time_cases:
            c = tc.doc
            pos = POSITION_CODES[c["position"]]
            g = c.get("generator") or {}
            after = int(g.get("drift_after_day", 0))
            onset_s = tc.day_start(after + 1)
            day = min(tc.last_day, max(1, int((now - tc.t_day1) // 86400) + 1)) if now >= tc.t_day1 else 0
            degr.append({
                "case": tc.id, "position": f"{pos['machine']}.motor_b", "position_code": c["position"],
                "machine": pos["machine"], "position_name": pos["position"], "feature": c["feature"],
                "unit": c.get("unit"), "direction": c.get("direction"), "profile": "load_step_no_failure",
                "measure": "operating_hours", "onset_day": after + 1, "onset_s": onset_s,
                "onset_at": self._at(onset_s), "scenario_onset_at": self._scenario_at(onset_s),
                "failure_s": None, "failure_at": None, "scenario_failure_at": None,
                "failure_limit": float((c.get("failure_limit") or {}).get("value", 0.0)),
                "state": "load_change" if now > onset_s else "healthy",
                "invalid_days": [i for i, (x, h) in sorted(tc.days.items()) if x is None or not h],
                "now": {"day": day, "nominal": tc.nominal(day) if day else None,
                        "scenario_x": tc.days.get(day, (None, 0))[0] if day else None},
            })
        observed = (sc.doc["cases"].get("F2") or {}).get("observed_until")
        return {
            "kind": "scenario", "scenario": sc.id, "cases": sc.case_ids, "sim_time_s": self.sim_time_s,
            "device_now": self.device_time(now).isoformat(), "shift_days": self.clock.shift_days,
            "device_start": self.device_time(0).isoformat(),
            "device_end": self.device_time(sc.duration_s).isoformat(),
            "observed_until": self._at((_parse_ts(observed) - sc.start).total_seconds()) if observed else None,
            "degradations": degr, "failures": failures,
            "expected_evaluation": (sc.doc.get("expected") or {}).get("evaluation"),
        }
