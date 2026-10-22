"""
Playback de cenário: a planta virtual serve, nos mesmos endereços OPC UA / Modbus, exatamente as transições
descritas num cenário de ouro (ex.: docs/scenarios/scenario_c_oee_shift.json), acelerado por `--speed`, para o
QA rodar o caminho real completo (CLP → gateway → MQTT → servidor) e comparar com o bloco `expected`.

    python -m edge.virtual_plant --scenario docs/scenarios/scenario_c_oee_shift.json --speed 60

O que é reproduzido na máquina do cenário (`machine.name`, ex. "Torno CNC"):
- `run_state` / `fault_state`: cada entrada de `signals`, no instante exato (só mudanças são escritas);
- tags de posição `run_state` (eixo-árvore, servos) acompanham o `run_state` da máquina; analógicos de
  rotação/corrente são constantes por estado (rodando/parado), sem ruído;
- `product_code`: o `plc_code` de `machine.product` desde o início;
- contadores de peças: `production.produced` peças distribuídas uniformemente pelo tempo com run_state = 1
  dentro do turno (peça k no instante (k − ½)·T/P do tempo rodando), `production.scrap` delas refugadas e
  espalhadas uniformemente (peça i é refugo quando ⌊i·S/P⌋ > ⌊(i−1)·S/P⌋). Os contadores partem de 0.
As demais máquinas ficam paradas (run_state 0, sem falha, contadores 0).

Relógio do dispositivo (SourceTimestamp OPC UA de cada escrita):
- `shifted` (padrão): o horário do próprio cenário deslocado por um número INTEIRO de dias para o passado
  (`--scenario-shift-days`, padrão = o menor N ≥ 0 que deixa o fim do cenário ≤ agora). Fusos sem horário de
  verão (Manaus) mantêm a hora local, então os números do `expected` valem como estão. No gateway use a
  política `source` sem checagem de desvio (VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off), senão o relógio "atrasado"
  é rejeitado. O servidor trata leituras > 120 s como histórico (replay): entram no uso/OEE, não no status
  ao vivo.
- `wall`: o horário real de cada escrita (durações divididas por `--speed`; só bate com o `expected` em
  `--speed 1`).
Modbus não carrega timestamp: o gateway carimba com o próprio relógio, então playback fiel em máquina
Modbus só em `--speed 1`. (O Cenário C usa o Torno CNC, que é OPC UA.)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .layout import ALL_DAYS, PLANTA_MANAUS, Break, PlantLayout, ShiftDef, signal_key
from .model import ANALOG_ROLES

DAY = timedelta(days=1)

# valores analógicos por estado (rodando, parado) — determinísticos no playback
_ANALOG = {
    "speed": (3200.0, 0.0),
    "current": (20.0, 0.4),
    "analog": (1.0, 0.0),
}


class ScenarioError(ValueError):
    pass


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ScenarioError(f"timestamp sem fuso: {s!r}")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Write:
    t: float          # segundos desde o início do cenário
    key: str
    value: object


class Scenario:
    """Cenário carregado + linha do tempo de escritas (determinística, ordenada por instante)."""

    def __init__(self, doc: dict, layout: PlantLayout = PLANTA_MANAUS) -> None:
        self.doc = doc
        self.id = doc.get("id", "scenario")
        self.layout = layout
        mdoc = doc.get("machine") or {}
        found = [(line, m) for line, m in layout.machines() if m.name == mdoc.get("name")]
        if not found:
            raise ScenarioError(f"máquina do cenário não existe no layout: {mdoc.get('name')!r}")
        self.line, self.machine = found[0]
        self.machine_key = f"{self.line.key}.{self.machine.key}"
        signals = doc.get("signals") or []
        if not signals:
            raise ScenarioError("cenário sem 'signals'")
        shift = doc.get("shift") or {}
        self.start = _parse_ts(shift.get("start_utc") or signals[0]["ts"])
        self.end = _parse_ts(shift.get("end_utc") or signals[-1]["ts"])
        if self.end <= self.start:
            raise ScenarioError("fim do cenário antes do início")
        self.duration_s = (self.end - self.start).total_seconds()
        self._sig = {s.role: signal_key(self.line, self.machine, s) for s in self.machine.signals
                     if s.position is None}
        self._pos_run = [signal_key(self.line, self.machine, s) for s in self.machine.signals
                         if s.position is not None and s.role == "run_state"]
        self._analog = [(signal_key(self.line, self.machine, s), s.role) for s in self.machine.signals
                        if s.role in ANALOG_ROLES]
        for need in ("run_state", "fault_state"):
            if need not in self._sig:
                raise ScenarioError(f"máquina {self.machine.name} não tem tag {need}")
        self.transitions = self._transitions(signals)
        self.writes = self._timeline()

    @classmethod
    def load(cls, path: str | Path, layout: PlantLayout = PLANTA_MANAUS) -> "Scenario":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), layout)

    # ── linha do tempo ────────────────────────────────────────────────────
    def _transitions(self, signals: list[dict]) -> list[tuple[float, int, int]]:
        out: list[tuple[float, int, int]] = []
        prev_t = -math.inf
        for s in signals:
            t = (_parse_ts(s["ts"]) - self.start).total_seconds()
            if t < prev_t:
                raise ScenarioError(f"signals fora de ordem em {s['ts']}")
            if not 0 <= t <= self.duration_s:
                raise ScenarioError(f"sinal {s['ts']} fora do intervalo do cenário")
            prev_t = t
            out.append((t, int(s["run_state"]), int(s.get("fault_state", 0))))
        return out

    def run_intervals(self) -> list[tuple[float, float]]:
        """Intervalos [a, b) com run_state = 1 dentro do cenário (segundos)."""
        out, on_at = [], None
        for t, run, _ in self.transitions:
            if run and on_at is None:
                on_at = t
            elif not run and on_at is not None:
                if t > on_at:
                    out.append((on_at, t))
                on_at = None
        if on_at is not None and self.duration_s > on_at:
            out.append((on_at, self.duration_s))
        return out

    def part_times(self) -> list[tuple[float, bool]]:
        """(instante, é_refugo) de cada peça, distribuídas pelo tempo rodando."""
        prod = self.doc.get("production") or {}
        produced = int(prod.get("produced", 0))
        scrap = int(prod.get("scrap", 0))
        if produced and "good" in prod and int(prod["good"]) + scrap != produced:
            raise ScenarioError("production: good + scrap ≠ produced")
        intervals = self.run_intervals()
        total = sum(b - a for a, b in intervals)
        if produced and total <= 0:
            raise ScenarioError("peças produzidas sem tempo com run_state = 1")
        out = []
        for k in range(1, produced + 1):
            off = (k - 0.5) * total / produced
            for a, b in intervals:
                if off < b - a:
                    t = a + off
                    break
                off -= b - a
            else:  # pragma: no cover - arredondamento
                t = intervals[-1][1]
            is_scrap = (k * scrap) // produced > ((k - 1) * scrap) // produced
            out.append((round(t, 3), is_scrap))
        return out

    def _timeline(self) -> list[Write]:
        w: list[Write] = []
        sig = self._sig
        prev_run = prev_fault = None
        for t, run, fault in self.transitions:
            running = bool(run)
            if running != prev_run:
                w.append(Write(t, sig["run_state"], running))
                for k in self._pos_run:
                    w.append(Write(t, k, running))
                for k, role in self._analog:
                    on, off = _ANALOG.get(role, (0.0, 0.0))
                    w.append(Write(t, k, on if running else off))
            if fault != prev_fault:
                w.append(Write(t, sig["fault_state"], fault))
            prev_run, prev_fault = running, fault
        product = (self.doc.get("machine") or {}).get("product")
        if product and "product_code" in sig:
            p = next((p for p in self.layout.products if p.code == product), None)
            if p is None:
                raise ScenarioError(f"produto {product!r} não está no catálogo do layout")
            w.append(Write(0.0, sig["product_code"], p.plc_code))
        good = scrap = 0
        for t, is_scrap in self.part_times():
            if is_scrap and "part_counter_scrap" in sig:
                scrap += 1
                w.append(Write(t, sig["part_counter_scrap"], scrap))
            elif not is_scrap and "part_counter_good" in sig:
                good += 1
                w.append(Write(t, sig["part_counter_good"], good))
        order = {id(x): i for i, x in enumerate(w)}
        return sorted(w, key=lambda x: (x.t, order[id(x)]))

    def shift_defs(self) -> tuple[ShiftDef, ...]:
        """Turno do cenário (pausas = eventos `planned_break`) para provisionar o calendário igual ao cenário.
        Todos os dias da semana: o relógio `shifted` muda o dia da semana."""
        sh = self.doc.get("shift") or {}
        if not sh.get("start_local") or not sh.get("end_local"):
            raise ScenarioError("cenário sem shift.start_local/end_local")
        breaks = tuple(Break(e["from_local"][:5], e["to_local"][:5], e.get("reason_code") or "PLN-01",
                             e.get("label", ""))
                       for e in self.doc.get("events") or [] if e.get("kind") == "planned_break")
        return (ShiftDef(sh.get("name") or "Turno", sh["start_local"], sh["end_local"], ALL_DAYS, breaks),)

    def values_at(self, t: float) -> dict[str, object]:
        """Valores da máquina do cenário no instante t (última escrita com instante ≤ t)."""
        out: dict[str, object] = {}
        for x in self.writes:
            if x.t > t:
                break
            out[x.key] = x.value
        return out


class ScenarioClock:
    """Relógio do dispositivo durante o playback (ver docstring do módulo)."""

    def __init__(self, scenario: Scenario, mode: str = "shifted", shift_days: Optional[int] = None,
                 speed: float = 1.0, now: Optional[datetime] = None) -> None:
        if mode not in ("shifted", "wall"):
            raise ScenarioError(f"relógio de cenário inválido: {mode!r} (use shifted|wall)")
        now = now or datetime.now(timezone.utc)
        self.mode = mode
        self.speed = speed
        self.scenario = scenario
        if shift_days is None:
            shift_days = max(0, math.ceil((scenario.end - now) / DAY))
        if shift_days < 0:
            raise ScenarioError("--scenario-shift-days deve ser ≥ 0")
        self.shift_days = shift_days
        self.origin = scenario.start - shift_days * DAY if mode == "shifted" else now

    def device_time(self, t: float) -> datetime:
        if self.mode == "shifted":
            return self.origin + timedelta(seconds=t)
        return self.origin + timedelta(seconds=t / self.speed)

    def info(self) -> dict:
        return {"clock": self.mode, "shift_days": self.shift_days if self.mode == "shifted" else None,
                "device_start": self.device_time(0).isoformat(),
                "device_end": self.device_time(self.scenario.duration_s).isoformat()}


class ScenarioModel:
    """Adaptador do cenário para o runtime (mesma interface usada do PlantModel + mudanças com instante)."""

    def __init__(self, scenario: Scenario, clock: Optional[ScenarioClock] = None, seed: int = 0,
                 heartbeat_s: float = 10.0) -> None:
        """`heartbeat_s`: a cada N segundos do cenário, re-escreve (mesmo valor, novo SourceTimestamp) as tags
        discretas/contadores da máquina do cenário — o "estou vivo" no relógio do dispositivo, que o gateway
        recebe com assinatura por timestamp (`device_heartbeat`). Sem isso o servidor veria "sem dados" nas
        paradas longas (uma leitura vale 60 s). 0 desliga."""
        self.scenario = scenario
        self.layout = scenario.layout
        self.seed = seed
        self.clock = clock or ScenarioClock(scenario)
        self.heartbeat_s = heartbeat_s
        self._hb_keys = [k for k in scenario._sig.values()
                         if k not in {a for a, _ in scenario._analog}] + list(scenario._pos_run)
        self._next_hb = heartbeat_s if heartbeat_s > 0 else math.inf
        self._t = 0.0
        self._i = 0                      # próxima escrita a aplicar
        self._pending: list[Write] = []
        self._base = self.idle_values()
        self._current = dict(self._base)
        self._apply_until(0.0)
        self._pending.clear()            # o estado inicial vai no push forçado

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
        return v

    @property
    def sim_time_s(self) -> float:
        return round(self._t, 3)

    @property
    def finished(self) -> bool:
        return self._t >= self.scenario.duration_s

    def _apply_until(self, t: float) -> None:
        w = self.scenario.writes
        while self._i < len(w) and w[self._i].t <= t:
            x = w[self._i]
            if self._current.get(x.key) != x.value:
                self._current[x.key] = x.value
                self._pending.append(x)
            self._i += 1

    def advance_to(self, sim_time_s: float, max_ticks: Optional[int] = None) -> int:
        t = min(max(self._t, sim_time_s), self.scenario.duration_s)
        n = len(self._pending)
        while self._next_hb <= t:
            hb = self._next_hb
            self._apply_until(hb)
            self._pending.extend(Write(hb, k, self._current[k]) for k in self._hb_keys)
            self._next_hb += self.heartbeat_s
        self._apply_until(t)
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
        if include_analog:
            return dict(self._current)
        return {k: v for k, v in self._current.items() if k not in {a for a, _ in self.scenario._analog}}

    def pulse_targets(self) -> dict[str, int]:
        return {signal_key(line, m, s): 0 for line, m in self.layout.machines() for s in m.signals
                if s.role == "cycle_pulse"}

    def snapshot(self) -> dict:
        sc = self.scenario
        return {
            "seed": self.seed,
            "sim_time_s": self.sim_time_s,
            "values": self.values(include_analog=False),
            "scenario": {
                "id": sc.id, "machine": sc.machine_key, "duration_s": sc.duration_s,
                "finished": self.finished, "writes_total": len(sc.writes), "writes_applied": self._i,
                "device_now": self.device_time(self._t).isoformat(), **self.clock.info(),
            },
        }
