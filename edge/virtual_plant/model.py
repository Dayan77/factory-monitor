"""
Modelo determinístico da Planta Manaus (sem I/O).

Determinismo: o tempo simulado avança em passos fixos de `TICK_S` (0,1 s) e todos os eventos
discretos (fim de ciclo, peça boa/refugo, golpes de cilindro, falhas e suas durações/códigos) usam um
único `random.Random(seed)`. Mesma seed + mesmo tempo simulado ⇒ mesmos contadores, falhas e tempos de
curso, independentemente da velocidade (`--speed`) ou do relógio real. Grandezas analógicas (rotação,
corrente) usam outro gerador e são calculadas só na exportação: não fazem parte do contrato de
determinismo e não consomem números do gerador principal.

Comportamento (resumo):
- Torno CNC: ciclo ideal 45 s (+0–12 % de perda de velocidade). Eixo-árvore e servos ligam na fase de
  corte (15 %–90 % do ciclo) → partidas do eixo-árvore = 1 por peça. Refugo 2 %.
- Centro de Usinagem: ciclo ideal 90 s, 4 trocas de ferramenta por peça (garra +1 ciclo por troca),
  bomba de refrigeração liga no corte. Refugo 1,5 %.
- Linha de Montagem (célula acoplada): ciclo ideal 40 s. Fixação: A1, A2, A4 1 golpe/peça, A3 2 golpes;
  robô: 1 ciclo de solda e N movimentos por eixo por peça; transportador roda enquanto a célula roda
  (partidas = retomadas após falha/turno). Peças boas/refugo (1,5 %) contadas na saída (transportador).
  Falha em qualquer máquina da célula para a célula inteira.
- Cilindros: contador do CLP de 16 bits (rola 65 535 → 0); tempo de curso = base + desgaste × vida
  acumulada + ruído; A2 tem degradação acelerada (+20 ms/1000 golpes) para exercitar alertas de desvio.
- Falhas: taxa constante por tempo **em operação** (MTBF), duração exponencial (MTTR, mínimo 30 s),
  código sorteado da tabela da máquina (layout.FAULT_CODES).
- Turnos (opcional): fora do turno tudo para (run_state = 0, sem falha).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .layout import FAULT_CODES, PLANTA_MANAUS, PlantLayout, signal_key

TICK_S = 0.1
U16 = 0x1_0000
U32 = 0x1_0000_0000


# ── Turnos ────────────────────────────────────────────────────────────────────

def parse_shifts(spec: Optional[str]) -> list[tuple[int, int]]:
    """'06:00-14:00,14:00-22:00' → [(21600, 50400), (50400, 79200)] em segundos do dia.

    Um turno que cruza a meia-noite ('22:00-06:00') é aceito. Vazio/None → sem calendário (24 h).
    """
    if not spec:
        return []
    out = []
    for part in spec.split(","):
        a, b = part.strip().split("-")
        out.append((_hhmm(a), _hhmm(b)))
    return out


def _hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    v = int(h) * 3600 + int(m) * 60
    if not 0 <= v <= 86400:
        raise ValueError(f"horário inválido: {s!r}")
    return v


def in_shift(shifts: list[tuple[int, int]], second_of_day: float) -> bool:
    if not shifts:
        return True
    t = second_of_day % 86400
    for a, b in shifts:
        if (a <= t < b) if a < b else (t >= a or t < b):
            return True
    return False


# ── Blocos ────────────────────────────────────────────────────────────────────

class FaultProcess:
    """Falhas com taxa constante por tempo em operação; duração exponencial."""

    def __init__(self, rng: random.Random, key: str, mtbf_s: float, mttr_s: float, enabled: bool = True) -> None:
        self.rng = rng
        self.key = key
        self.codes = sorted(FAULT_CODES[key])
        self.mtbf_s = mtbf_s
        self.mttr_s = mttr_s
        self.enabled = enabled
        self.code = 0
        self.remaining_s = 0.0
        self.events: list[dict] = []
        self._run_left = rng.expovariate(1.0 / mtbf_s) if enabled else math.inf

    @property
    def active(self) -> bool:
        return self.code != 0

    def tick_running(self, dt: float, now: float) -> bool:
        """Consome tempo em operação; retorna True se uma falha começou agora."""
        self._run_left -= dt
        if self._run_left > 0:
            return False
        self.code = self.rng.choice(self.codes)
        self.remaining_s = max(30.0, round(self.rng.expovariate(1.0 / self.mttr_s), 1))
        self.events.append({"start_s": round(now, 1), "duration_s": self.remaining_s, "code": self.code})
        self._run_left = self.rng.expovariate(1.0 / self.mtbf_s)
        return True

    def tick_fault(self, dt: float) -> None:
        self.remaining_s -= dt
        if self.remaining_s <= 1e-9:
            self.code = 0
            self.remaining_s = 0.0


class BoolTracker:
    """Verdade de campo de um run_state: tempo ligado e partidas (bordas 0→1)."""

    def __init__(self) -> None:
        self.state = False
        self.on_s = 0.0
        self.starts = 0

    def update(self, value: bool, dt: float) -> None:
        if value and not self.state:
            self.starts += 1
        self.state = value
        if value:
            self.on_s += dt


@dataclass
class Cylinder:
    name: str
    strokes_at: tuple[float, ...]   # frações do ciclo da célula em que o cilindro faz um golpe
    plc_counter: int                # contador do CLP (16 bits) no início
    life_cycles: int                # vida acumulada do componente no início (desgaste)
    base_ms: float
    wear_ms_per_kcycle: float
    has_eos: bool = False
    degrade_ms_per_kcycle: float = 0.0   # degradação acelerada (aplicada aos golpes desta simulação)
    strokes: int = 0                # golpes desde o início da simulação
    rollovers: int = 0
    stroke_ms: float = 0.0

    def stroke(self, rng: random.Random) -> None:
        self.strokes += 1
        self.life_cycles += 1
        nxt = self.plc_counter + 1
        if nxt >= U16:
            self.rollovers += 1
        self.plc_counter = nxt % U16
        self.stroke_ms = round(self.nominal_stroke_ms() + rng.gauss(0.0, 1.5), 1)

    def nominal_stroke_ms(self) -> float:
        return (self.base_ms + self.wear_ms_per_kcycle * self.life_cycles / 1000.0
                + self.degrade_ms_per_kcycle * self.strokes / 1000.0)


# ── Máquinas ──────────────────────────────────────────────────────────────────

class MachiningMachine:
    """Torno / centro de usinagem: ciclo carga → corte → descarga, com falhas."""

    CUT_START, CUT_END = 0.15, 0.90

    def __init__(self, rng: random.Random, key: str, ideal_cycle_s: float, scrap_rate: float,
                 mtbf_s: float, mttr_s: float, faults: bool, tool_changes: int = 0) -> None:
        self.rng = rng
        self.key = key
        self.ideal = ideal_cycle_s
        self.scrap_rate = scrap_rate
        self.tool_changes = tool_changes
        self.fault = FaultProcess(rng, key, mtbf_s, mttr_s, enabled=faults)
        self.running = False
        self.cycle_t = 0.0
        self.cycle_len = self._new_cycle_len()
        self.tc_done = 0
        self.good = 0
        self.scrap = 0
        self.gripper = 0
        self.cutting = False

    def _new_cycle_len(self) -> float:
        return round(self.ideal * (1.0 + self.rng.uniform(0.0, 0.12)), 1)

    def step(self, dt: float, now: float, shift_on: bool) -> None:
        if self.fault.active:
            self.fault.tick_fault(dt)
            self.running = self.cutting = False
            return
        if not shift_on:
            self.running = self.cutting = False
            return
        if self.fault.tick_running(dt, now):
            self.running = self.cutting = False
            self.cycle_t, self.tc_done = 0.0, 0     # ciclo abortado: recomeça após o reparo
            return
        self.running = True
        self.cycle_t += dt
        frac = self.cycle_t / self.cycle_len
        self.cutting = self.CUT_START <= frac < self.CUT_END
        if self.tool_changes:
            span = (self.CUT_END - self.CUT_START) / self.tool_changes
            while self.tc_done < self.tool_changes and frac >= self.CUT_START + self.tc_done * span:
                self.tc_done += 1
                self.gripper = (self.gripper + 1) % U32
        if self.cycle_t >= self.cycle_len - 1e-9:
            if self.rng.random() < self.scrap_rate:
                self.scrap = (self.scrap + 1) % U32
            else:
                self.good = (self.good + 1) % U32
            self.cycle_t, self.tc_done = 0.0, 0
            self.cycle_len = self._new_cycle_len()
            self.cutting = False


class AssemblyCell:
    """Robô de solda + estação de fixação (4 cilindros) + transportador de saída, acoplados."""

    WELD_START, WELD_END = 0.25, 0.80
    AXIS_MOVES = (14, 14, 10, 8, 8, 6)

    def __init__(self, rng: random.Random, faults: bool) -> None:
        self.rng = rng
        self.ideal = 40.0
        self.scrap_rate = 0.015
        self.robot_fault = FaultProcess(rng, "montagem.robo", 3 * 3600, 6 * 60, faults)
        self.fix_fault = FaultProcess(rng, "montagem.fixacao", 4 * 3600, 4 * 60, faults)
        self.conv_fault = FaultProcess(rng, "montagem.transportador", 8 * 3600, 3 * 60, faults)
        self.cylinders = [
            # nome, frações do golpe, contador CLP inicial, vida inicial, base ms, desgaste ms/1000 ciclos
            Cylinder("A1", (0.05,), 12_000, 1_250_000, 180.0, 0.004, has_eos=True),
            # A2: vedação gasta — além do desgaste normal, +20 ms a cada 1000 golpes (≈ +40 ms/dia de produção)
            Cylinder("A2", (0.10,), 40_210, 3_400_000, 205.0, 0.012, degrade_ms_per_kcycle=20.0),
            Cylinder("A3", (0.15, 0.90), 65_200, 2_100_000, 160.0, 0.004),
            Cylinder("A4", (0.95,), 63_900, 600_000, 230.0, 0.004),
        ]
        for c in self.cylinders:   # tempo de curso inicial coerente com o desgaste
            c.stroke_ms = round(c.nominal_stroke_ms(), 1)
        self.running = False
        self.welding = False
        self.cycle_t = 0.0
        self.cycle_len = self._new_cycle_len()
        self.done_strokes: dict[str, int] = {c.name: 0 for c in self.cylinders}
        self.weld_cycles = 0
        self.axis = [0] * 6
        self.good = 0
        self.scrap = 0

    def _new_cycle_len(self) -> float:
        return round(self.ideal * (1.0 + self.rng.uniform(0.0, 0.10)), 1)

    @property
    def faults(self) -> list[FaultProcess]:
        return [self.robot_fault, self.fix_fault, self.conv_fault]

    def step(self, dt: float, now: float, shift_on: bool) -> None:
        if any(f.active for f in self.faults):
            for f in self.faults:
                if f.active:
                    f.tick_fault(dt)
            self.running = self.welding = False
            return
        if not shift_on:
            self.running = self.welding = False
            return
        started = [f.tick_running(dt, now) for f in self.faults]
        if any(started):
            self.running = self.welding = False
            self.cycle_t = 0.0
            self.done_strokes = {c.name: 0 for c in self.cylinders}
            return
        self.running = True
        self.cycle_t += dt
        frac = self.cycle_t / self.cycle_len
        self.welding = self.WELD_START <= frac < self.WELD_END
        for c in self.cylinders:
            while self.done_strokes[c.name] < len(c.strokes_at) and frac >= c.strokes_at[self.done_strokes[c.name]]:
                self.done_strokes[c.name] += 1
                c.stroke(self.rng)
        if self.cycle_t >= self.cycle_len - 1e-9:
            self.weld_cycles = (self.weld_cycles + 1) % U32
            self.axis = [(a + n) % U32 for a, n in zip(self.axis, self.AXIS_MOVES)]
            if self.rng.random() < self.scrap_rate:
                self.scrap = (self.scrap + 1) % U32
            else:
                self.good = (self.good + 1) % U32
            self.cycle_t = 0.0
            self.done_strokes = {c.name: 0 for c in self.cylinders}
            self.cycle_len = self._new_cycle_len()


# ── Planta ────────────────────────────────────────────────────────────────────

ANALOG_ROLES = {"analog", "speed", "current"}


class PlantModel:
    def __init__(
        self,
        seed: int = 42,
        layout: PlantLayout = PLANTA_MANAUS,
        faults: bool = True,
        shifts: Optional[str] = None,
        start_second_of_day: int = 6 * 3600,
    ) -> None:
        self.seed = seed
        self.layout = layout
        self.rng = random.Random(seed)
        self.noise = random.Random(seed ^ 0x5EED_A5A5)
        self.shifts = parse_shifts(shifts)
        self.start_sod = start_second_of_day
        self.ticks = 0
        self.torno = MachiningMachine(self.rng, "usinagem.torno", 45.0, 0.02, 2 * 3600, 5 * 60, faults)
        self.centro = MachiningMachine(self.rng, "usinagem.centro", 90.0, 0.015, 3 * 3600, 8 * 60, faults,
                                       tool_changes=4)
        self.cell = AssemblyCell(self.rng, faults)
        self.trackers: dict[str, BoolTracker] = {}
        self._keys = {signal_key(line, m, s) for line, m in layout.machines() for s in m.signals}
        self._roles = {signal_key(line, m, s): s.role for line, m in layout.machines() for s in m.signals}
        for key, role in self._roles.items():
            if role == "run_state":
                self.trackers[key] = BoolTracker()

    # tempo
    @property
    def sim_time_s(self) -> float:
        return round(self.ticks * TICK_S, 1)

    def step(self) -> None:
        now = (self.ticks + 1) * TICK_S
        shift_on = in_shift(self.shifts, self.start_sod + now)
        self.torno.step(TICK_S, now, shift_on)
        self.centro.step(TICK_S, now, shift_on)
        self.cell.step(TICK_S, now, shift_on)
        self.ticks += 1
        values = self._discrete()
        for key, tr in self.trackers.items():
            tr.update(bool(values[key]), TICK_S)

    def advance_to(self, sim_time_s: float, max_ticks: Optional[int] = None) -> int:
        """Avança em passos inteiros até sim_time_s (nunca volta). Retorna quantos passos deu."""
        target = int(math.floor(sim_time_s / TICK_S + 1e-9))
        n = max(0, target - self.ticks)
        if max_ticks is not None:
            n = min(n, max_ticks)
        for _ in range(n):
            self.step()
        return n

    def run_for(self, seconds: float) -> None:
        self.advance_to(self.sim_time_s + seconds)

    # valores
    def _discrete(self) -> dict[str, float | int | bool]:
        t, c, cell = self.torno, self.centro, self.cell
        v: dict[str, float | int | bool] = {
            "usinagem.torno.run_state": t.running,
            "usinagem.torno.fault_state": t.fault.code,
            "usinagem.torno.parts_good": t.good,
            "usinagem.torno.parts_scrap": t.scrap,
            "usinagem.torno.spindle.run": t.cutting,
            "usinagem.torno.servo_x.run": t.cutting,
            "usinagem.torno.servo_z.run": t.cutting,
            "usinagem.centro.run_state": c.running,
            "usinagem.centro.fault_state": c.fault.code,
            "usinagem.centro.parts_good": c.good,
            "usinagem.centro.parts_scrap": c.scrap,
            "usinagem.centro.spindle.run": c.cutting,
            "usinagem.centro.gripper.cycles": c.gripper,
            "usinagem.centro.coolant.run": c.cutting,
            "montagem.robo.run_state": cell.running,
            "montagem.robo.fault_state": cell.robot_fault.code,
            "montagem.robo.weld_cycles": cell.weld_cycles,
            "montagem.fixacao.run_state": cell.running,
            "montagem.fixacao.fault_state": cell.fix_fault.code,
            "montagem.transportador.fault_state": cell.conv_fault.code,
            "montagem.transportador.motor.run": cell.running,
            "montagem.transportador.parts_good": cell.good,
            "montagem.transportador.parts_scrap": cell.scrap,
        }
        for i, n in enumerate(cell.axis, start=1):
            v[f"montagem.robo.axis{i}.cycles"] = n
        for cyl in cell.cylinders:
            k = cyl.name.lower()
            v[f"montagem.fixacao.{k}.cycles"] = cyl.plc_counter
            v[f"montagem.fixacao.{k}.stroke_time"] = cyl.stroke_ms
        # O bit de fim de curso é gerado pelo runtime (trem de pulsos com largura mínima real);
        # o modelo expõe o número de golpes que ele deve emitir.
        v["montagem.fixacao.a1.eos"] = False
        return v

    def _analog(self) -> dict[str, float]:
        n = self.noise.gauss
        t, c, cell = self.torno, self.centro, self.cell

        def on(flag: bool, val: float, sd: float, idle: float = 0.0) -> float:
            return round(max(0.0, val + n(0, sd)) if flag else max(0.0, idle + abs(n(0, idle * 0.1 + 1e-6))), 2)

        return {
            "usinagem.torno.spindle.speed": on(t.cutting, 3200.0, 15.0),
            "usinagem.torno.spindle.current": on(t.cutting, 22.0, 0.8, 0.4),
            "usinagem.torno.servo_x.current": on(t.cutting, 4.5, 0.3, 0.2),
            "usinagem.torno.servo_z.current": on(t.cutting, 5.2, 0.3, 0.2),
            "usinagem.centro.spindle.speed": on(c.cutting, 8000.0, 40.0),
            "usinagem.centro.spindle.current": on(c.cutting, 18.5, 0.7, 0.5),
            "usinagem.centro.coolant.current": on(c.cutting, 6.5, 0.2),
            "montagem.transportador.motor.current": on(cell.running, 3.2, 0.1),
        }

    def values(self, include_analog: bool = True) -> dict[str, float | int | bool]:
        v = self._discrete()
        if include_analog:
            v.update(self._analog())
        missing = self._keys - set(v)
        if include_analog and missing:  # garante que layout e modelo não divergem
            raise RuntimeError(f"sinais sem valor no modelo: {sorted(missing)}")
        return v

    def pulse_targets(self) -> dict[str, int]:
        """Pulsos de fim de curso que já deveriam ter sido emitidos (por chave de sinal cycle_pulse)."""
        a1 = next(c for c in self.cell.cylinders if c.has_eos)
        return {"montagem.fixacao.a1.eos": a1.strokes}

    def snapshot(self) -> dict:
        """Estado determinístico (sem analógicos) + verdade de campo para o QA."""
        cell = self.cell
        faults = {
            f.key: f.events for f in (self.torno.fault, self.centro.fault, *cell.faults)
        }
        return {
            "seed": self.seed,
            "sim_time_s": self.sim_time_s,
            "values": self.values(include_analog=False),
            "truth": {
                "run_state": {k: {"on_s": round(tr.on_s, 1), "starts": tr.starts} for k, tr in self.trackers.items()},
                "cylinders": {c.name: {"strokes": c.strokes, "plc_counter": c.plc_counter,
                                       "rollovers": c.rollovers, "life_cycles": c.life_cycles,
                                       "stroke_ms": c.stroke_ms} for c in cell.cylinders},
                "pulse_targets": self.pulse_targets(),
                "faults": faults,
            },
        }
