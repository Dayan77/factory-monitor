"""
Modelo determinístico da Planta Manaus (sem I/O).

Determinismo: o tempo simulado avança em passos fixos de `TICK_S` (0,1 s) e todos os eventos
discretos (fim de ciclo, peça boa/refugo, golpes de cilindro, falhas e suas durações/códigos, micro-paradas,
setups, falta de material) usam um único `random.Random(seed)` (ou nenhum sorteio, quando o evento é de
calendário). Mesma seed + mesmo tempo simulado ⇒ mesmos contadores, falhas, eventos e tempos de curso,
independentemente da velocidade (`--speed`) ou do relógio real. Grandezas analógicas (rotação, corrente) usam
outro gerador e são calculadas só na exportação: não fazem parte do contrato de determinismo.

Caminhos de OEE exercitados (docs/domain.md §4, kpis.md §1):
- **Calendário** (padrão `layout.STANDARD_SHIFTS`): 3 turnos 06–14, 14–22, 22–06; em cada um 1 h de refeição e
  2 cafés de 10 min (`PLN-01`). Fora do turno e nas pausas tudo para (run_state = 0, fault_state = 0).
- **Produto e ciclo ideal** por (máquina, produto) (`layout.Machine.products`); a tag `product_code` publica o
  `plc_code` do produto ativo. **Setup (`SET-01`) uma vez por turno**: em `setup_offset_s` após o início do
  turno a máquina para por `setup_s` e troca para o próximo produto da lista (a receita muda no INÍCIO do
  setup). O tempo de setup só corre em tempo produtivo (pausa no café/refeição).
- **Perda de velocidade**: ciclo real = ideal × (1 + `speed_loss_pct`/100 + U(0, `speed_jitter_pct`/100)).
- **Micro-paradas**: taxa constante `micro_stops_per_h` por hora EM OPERAÇÃO, duração U(15 s, 110 s) — sempre
  < 120 s (limiar padrão), run_state = 0 e fault_state = 0.
- **Falhas**: taxa constante por tempo em operação (MTBF), duração exponencial (MTTR, mínimo 30 s — as < 120 s
  viram micro-paradas no servidor), código sorteado da tabela da máquina; a centena dá a categoria e o motivo
  (`layout.FAULT_CATEGORIES`: 1xx mecânica EQP-01, 2xx elétrica EQP-02, 3xx automação EQP-04,
  4xx pneumática EQP-03, 5xx ferramental EQP-05).
- **Linha de Montagem** (célula acoplada fixação → robô → transportador): parada de uma máquina para a célula;
  quem está a montante fica `blocked`, quem está a jusante fica `starved`. A célula é abastecida por um buffer
  (`buffer_capacity_s` segundos de produção) alimentado pelo Centro de Usinagem: com o centro parado (falha,
  setup) o buffer esvazia e a célula inteira fica `starved` (motivo sugerido `MAT-01`) até repor
  `buffer_resume_s`.
- **Refugo** por máquina (`scrap_rate`), sorteado por peça; o Centro com FLG-B faz 2 peças por ciclo.
- Cilindros: contador do CLP de 16 bits (rola 65 535 → 0); tempo de curso = base + desgaste × vida
  acumulada + ruído; A2 tem degradação acelerada (+20 ms/1000 golpes) para exercitar alertas de desvio.

- **Desgaste e falhas por uso** (Sprint 4, `wear.py`): vedações dos cilindros, rolamentos de motores, redutores do
  robô e ferramentas têm vida até a falha sorteada na instalação (gerador próprio por componente, não desloca os
  sorteios acima); a falha entra no `FaultProcess` da máquina (`source = "wear"`). Falhas impostas pela API de
  controle (`inject_fault` / `clear_fault`) também não consomem sorteios da planta.

`snapshot()["truth"]` traz a verdade de campo para o QA: eventos (falha, micro-parada, setup, starved, blocked)
por máquina com início/duração em segundos simulados, produto ativo e tempos de run_state.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Sequence, Union

from .layout import (
    FAULT_CODES, PLANTA_MANAUS, Machine, MachineProduct, PlantLayout, ShiftDef, fault_category, signal_key,
)
from .wear import WearModel

TICK_S = 0.1
U16 = 0x1_0000
U32 = 0x1_0000_0000
DAY_S = 86400


def det_gauss(seed: int, key: str, bucket: int) -> float:
    """N(0, 1) determinístico por (seed, chave, bloco de tempo) — não depende de quantas vezes foi chamado."""
    return random.Random(f"{seed}/{key}/{bucket}").gauss(0.0, 1.0)


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
    parts = s.strip().split(":")
    h, m = parts[0], parts[1]
    v = int(h) * 3600 + int(m) * 60 + (int(parts[2]) if len(parts) > 2 else 0)
    if not 0 <= v <= DAY_S:
        raise ValueError(f"horário inválido: {s!r}")
    return v


def in_shift(shifts: list[tuple[int, int]], second_of_day: float) -> bool:
    if not shifts:
        return True
    t = second_of_day % DAY_S
    for a, b in shifts:
        if (a <= t < b) if a < b else (t >= a or t < b):
            return True
    return False


@dataclass(frozen=True)
class CalendarState:
    production: bool                 # em turno e fora de pausa
    shift: Optional[str] = None      # nome do turno (None = fora de turno)
    shift_start: Optional[float] = None   # início desta instância do turno (s absolutos de planta)
    in_break: bool = False
    break_reason: Optional[str] = None


class ShiftCalendar:
    """Calendário semanal de turnos com pausas. Tempo de planta = segundos desde 00:00 do dia 0 (local)."""

    def __init__(self, shifts: Sequence[ShiftDef], start_weekday: int = 0) -> None:
        self.start_weekday = start_weekday
        self._shifts = []
        for sh in shifts:
            a, b = _hhmm(sh.start_local), _hhmm(sh.end_local)
            dur = (b - a) % DAY_S or DAY_S
            breaks = []
            for br in sh.breaks:
                ra = (_hhmm(br.start_local) - a) % DAY_S
                rb = (_hhmm(br.end_local) - a) % DAY_S
                if not 0 <= ra < rb <= dur:
                    raise ValueError(f"pausa {br.start_local}-{br.end_local} fora do turno {sh.name}")
                breaks.append((ra, rb, br.reason_code))
            self._shifts.append((sh.name, a, dur, frozenset(sh.weekdays), tuple(breaks)))

    def __bool__(self) -> bool:
        return bool(self._shifts)

    def at(self, plant_s: float) -> CalendarState:
        if not self._shifts:
            return CalendarState(production=True)
        day = int(plant_s // DAY_S)
        for name, a, dur, weekdays, breaks in self._shifts:
            for d in (day, day - 1):      # turno que começou hoje ou ontem (cruza a meia-noite)
                start = d * DAY_S + a
                if start <= plant_s < start + dur and (self.start_weekday + d) % 7 in weekdays:
                    rel = plant_s - start
                    for ra, rb, reason in breaks:
                        if ra <= rel < rb:
                            return CalendarState(False, name, start, True, reason)
                    return CalendarState(True, name, start)
        return CalendarState(production=False)


def _legacy_shift_defs(spec: str) -> list[ShiftDef]:
    out = []
    for i, part in enumerate(spec.split(","), start=1):
        a, b = part.strip().split("-")
        out.append(ShiftDef(f"Turno {i}", a.strip(), b.strip(), (0, 1, 2, 3, 4, 5, 6)))
    return out


# ── Configuração ──────────────────────────────────────────────────────────────

@dataclass
class PlantConfig:
    """Parâmetros do comportamento (padrões documentados no docstring do módulo)."""
    speed_loss_pct: float = 5.0          # ciclo real ≥ ideal × (1 + speed_loss_pct/100)
    speed_jitter_pct: float = 2.0        # + U(0, jitter) por ciclo
    micro_stops_per_h: dict = field(default_factory=lambda: {"torno": 3.0, "centro": 2.0, "cell": 4.0})
    micro_stop_s: tuple[float, float] = (15.0, 110.0)
    setups: bool = True
    setup_s: dict = field(default_factory=lambda: {"torno": 900.0, "centro": 1200.0, "cell": 720.0})
    # após o início do turno (cai antes do 1º café em todos os turnos do calendário padrão)
    setup_offset_s: dict = field(default_factory=lambda: {"torno": 3600.0, "centro": 4500.0, "cell": 6300.0})
    scrap_rate: dict = field(default_factory=lambda: {"torno": 0.02, "centro": 0.015, "cell": 0.015})
    buffer_capacity_s: float = 900.0     # buffer centro → célula, em segundos de produção da célula
    buffer_refill_per_s: float = 2.0     # reposição enquanto o centro roda
    buffer_resume_s: float = 60.0        # célula volta a rodar quando o buffer chega aqui
    wear_failures: bool = True           # falhas por desgaste (wear.py); o desgaste/sintomas existem sempre
    wear_repair: str = "auto"            # "auto" (plantão troca o componente) | "manual" (espera a API)
    # Sprint 5 (predictive.md P-11): perfil lento do cilindro B3 — 30 000 golpes saudáveis (aquecimento + linha
    # de base de 25 blocos de 1 000 cabem) e depois +0,5 ms a cada 1 000 golpes até 120 % do curso novo (240 ms)
    # → falha 447 em 110 000 golpes (~70 dias de produção da célula).
    b3_onset_strokes: int = 30_000
    b3_drift_ms_per_kstroke: float = 0.5
    # Motor B: corrente RMS por produto (carga) — SUP-200 é mais pesado (+0,5 A, deriva sem falha, cenário F2)
    motor_b_current_a: dict = field(default_factory=lambda: {"SUP-100": 12.0, "SUP-200": 12.5})

    def with_micro_rate(self, per_h: Optional[float]) -> "PlantConfig":
        if per_h is not None:
            self.micro_stops_per_h = {k: per_h for k in self.micro_stops_per_h}
        return self


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
        self.source: Optional[str] = None     # "random" | "wear" | "forced" (falha ativa)
        self._open: Optional[dict] = None     # evento da falha ativa
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
        cat = fault_category(self.code)
        self._open = {"start_s": round(now, 1), "duration_s": self.remaining_s, "code": self.code,
                      "category": cat["category"], "reason_code": cat["reason_code"]}
        self.events.append(self._open)
        self.source = "random"
        self._run_left = self.rng.expovariate(1.0 / self.mtbf_s)
        return True

    def tick_fault(self, dt: float) -> None:
        self.remaining_s -= dt
        if self.remaining_s <= 1e-9:
            self.code = 0
            self.remaining_s = 0.0
            self._open = None
            self.source = None

    def inject(self, code: int, now: float, duration_s: Optional[float] = None, source: str = "forced",
               **extra) -> dict:
        """Falha imposta de fora (desgaste, API de controle). `duration_s=None` = até `clear()`.
        Não usa o gerador da planta (não desloca os sorteios das falhas aleatórias)."""
        if self.active:
            self.clear(now)
        cat = fault_category(code)
        self.code = int(code)
        self.remaining_s = math.inf if duration_s is None else max(TICK_S, float(duration_s))
        self._open = {"start_s": round(now, 1), "duration_s": None if duration_s is None else self.remaining_s,
                      "code": self.code, "category": cat["category"], "reason_code": cat["reason_code"],
                      "source": source, **extra}
        self.events.append(self._open)
        self.source = source
        return self._open

    def clear(self, now: float) -> int:
        """Encerra a falha ativa agora (reparo). Retorna o código que estava ativo (0 = nenhum)."""
        if not self.active:
            return 0
        code = self.code
        if self._open is not None:
            self._open["duration_s"] = round(now - self._open["start_s"], 1)
        self.code = 0
        self.remaining_s = 0.0
        self._open = None
        self.source = None
        return code

    @property
    def active_event(self) -> Optional[dict]:
        return self._open if self.active else None


class MicroStopProcess:
    """Micro-paradas (< limiar de 120 s) com taxa constante por hora em operação."""

    def __init__(self, rng: random.Random, per_h: float, dur_s: tuple[float, float]) -> None:
        self.rng = rng
        self.per_h = per_h
        self.dur_s = dur_s
        self.remaining_s = 0.0
        self.origin: Optional[int] = None     # célula: índice da máquina que parou
        self.events: list[dict] = []
        self._run_left = rng.expovariate(per_h / 3600.0) if per_h > 0 else math.inf

    @property
    def active(self) -> bool:
        return self.remaining_s > 0

    def tick_running(self, dt: float, now: float, n_origins: int = 0) -> bool:
        self._run_left -= dt
        if self._run_left > 0:
            return False
        lo, hi = self.dur_s
        self.remaining_s = round(self.rng.uniform(lo, hi), 1)
        self.origin = self.rng.randrange(n_origins) if n_origins else None
        ev = {"start_s": round(now, 1), "duration_s": self.remaining_s}
        if self.origin is not None:
            ev["origin"] = self.origin
        self.events.append(ev)
        self._run_left = self.rng.expovariate(self.per_h / 3600.0)
        return True

    def tick(self, dt: float) -> None:
        self.remaining_s -= dt
        if self.remaining_s <= 1e-9:
            self.remaining_s = 0.0
            self.origin = None


class SetupProcess:
    """Troca de produto (`SET-01`) uma vez por instância de turno, em `offset_s` após o início."""

    def __init__(self, products: Sequence[MachineProduct], duration_s: float, offset_s: float,
                 enabled: bool) -> None:
        self.products = list(products)
        self.index = self.products.index(next(p for p in products if p.is_default)) if any(
            p.is_default for p in products) else 0
        self.duration_s = duration_s
        self.offset_s = offset_s
        self.enabled = enabled and len(self.products) > 1
        self.remaining_s = 0.0
        self._done_for: Optional[float] = None
        self.events: list[dict] = []

    @property
    def product(self) -> MachineProduct:
        return self.products[self.index]

    @property
    def active(self) -> bool:
        return self.remaining_s > 0

    def maybe_start(self, cal: CalendarState, plant_s: float, now: float) -> bool:
        if not self.enabled or cal.shift_start is None or self._done_for == cal.shift_start:
            return False
        if plant_s < cal.shift_start + self.offset_s:
            return False
        self._done_for = cal.shift_start
        prev = self.product.product
        self.index = (self.index + 1) % len(self.products)
        self.remaining_s = self.duration_s
        self.events.append({"start_s": round(now, 1), "duration_s": self.duration_s, "reason_code": "SET-01",
                            "from_product": prev, "to_product": self.product.product})
        return True

    def tick(self, dt: float) -> None:
        self.remaining_s -= dt
        if self.remaining_s <= 1e-9:
            self.remaining_s = 0.0


class IntervalRecorder:
    """Registra intervalos de um estado booleano (ex.: starved) em segundos simulados."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._start: Optional[float] = None

    def update(self, on: bool, now: float) -> None:
        if on and self._start is None:
            self._start = now
        elif not on and self._start is not None:
            self.events.append({"start_s": round(self._start, 1), "duration_s": round(now - self._start, 1)})
            self._start = None

    def as_list(self, now: float) -> list[dict]:
        out = list(self.events)
        if self._start is not None:
            out.append({"start_s": round(self._start, 1), "duration_s": None,
                        "open_for_s": round(now - self._start, 1)})
        return out


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
    strokes_at_install: int = 0     # `strokes` quando o componente atual foi instalado (troca, wear.py)
    leak_threshold_ms: float = math.inf   # deriva do curso que vira vazamento (wear.py)
    # Sprint 5 (predictive.md P-11): perfil "lento" — saudável até `onset_strokes` golpes desde a instalação,
    # depois deriva linear `degrade_ms_per_kcycle`; falha quando a deriva chega a `failure_frac` · base.
    onset_strokes: int = 0
    failure_frac: Optional[float] = None
    counter_mod: int = U16          # contador do CLP: 16 bits (padrão) ou 32 bits (terminal de válvulas)
    own_rng: Optional[random.Random] = None   # gerador próprio (não desloca os sorteios da célula)

    @property
    def strokes_since_install(self) -> int:
        return self.strokes - self.strokes_at_install

    def stroke(self, rng: random.Random) -> None:
        self.strokes += 1
        self.life_cycles += 1
        nxt = self.plc_counter + 1
        if nxt >= self.counter_mod:
            self.rollovers += 1
        self.plc_counter = nxt % self.counter_mod
        self.stroke_ms = round(self.nominal_stroke_ms() + (self.own_rng or rng).gauss(0.0, 1.5), 1)

    def degraded_strokes(self) -> int:
        return max(0, self.strokes_since_install - self.onset_strokes)

    def nominal_stroke_ms(self) -> float:
        return (self.base_ms + self.wear_ms_per_kcycle * self.life_cycles / 1000.0
                + self.degrade_ms_per_kcycle * self.degraded_strokes() / 1000.0)


def _machine(layout: PlantLayout, line_key: str, machine_key: str) -> Machine:
    return next(m for line, m in layout.machines() if line.key == line_key and m.key == machine_key)


# ── Máquinas ──────────────────────────────────────────────────────────────────

class MachiningMachine:
    """Torno / centro de usinagem: ciclo carga → corte → descarga, com falhas, micro-paradas e setup."""

    CUT_START, CUT_END = 0.15, 0.90

    def __init__(self, rng: random.Random, key: str, machine: Machine, cfg: PlantConfig, short: str,
                 mtbf_s: float, mttr_s: float, faults: bool, tool_changes: int = 0) -> None:
        self.rng = rng
        self.key = key
        self.cfg = cfg
        self.scrap_rate = cfg.scrap_rate[short]
        self.tool_changes = tool_changes
        self.fault = FaultProcess(rng, key, mtbf_s, mttr_s, enabled=faults)
        self.micro = MicroStopProcess(rng, cfg.micro_stops_per_h[short], cfg.micro_stop_s)
        self.setup = SetupProcess(machine.products, cfg.setup_s[short], cfg.setup_offset_s[short], cfg.setups)
        self.running = False
        self.cycle_t = 0.0
        self.cycle_len = self._new_cycle_len()
        self.tc_done = 0
        self.good = 0
        self.scrap = 0
        self.gripper = 0
        self.cutting = False
        self.cycles_done = 0            # ciclos completos (interno, não rola; desgaste da ferramenta)

    @property
    def ideal(self) -> float:
        return self.setup.product.ideal_cycle_time_s

    def abort_cycle(self) -> None:
        """Falha imposta (desgaste/API): o ciclo em curso é abortado, como numa falha aleatória."""
        self.cycle_t, self.tc_done = 0.0, 0
        self._stop()

    def _new_cycle_len(self) -> float:
        loss = self.cfg.speed_loss_pct / 100.0 + self.rng.uniform(0.0, self.cfg.speed_jitter_pct / 100.0)
        return round(self.ideal * (1.0 + loss), 1)

    def _stop(self) -> None:
        self.running = self.cutting = False

    def step(self, dt: float, now: float, cal: CalendarState, plant_s: float) -> None:
        if self.fault.active:
            self.fault.tick_fault(dt)
            return self._stop()
        if self.micro.active:
            self.micro.tick(dt)
            return self._stop()
        if not cal.production:
            return self._stop()
        if self.setup.active:
            self.setup.tick(dt)
            return self._stop()
        if self.setup.maybe_start(cal, plant_s, now):
            self.cycle_t, self.tc_done = 0.0, 0     # ciclo em curso abortado; novo produto → novo ciclo
            self.cycle_len = self._new_cycle_len()
            return self._stop()
        if self.fault.tick_running(dt, now):
            self.cycle_t, self.tc_done = 0.0, 0     # ciclo abortado: recomeça após o reparo
            return self._stop()
        if self.micro.tick_running(dt, now):
            return self._stop()                     # ciclo pausado (retoma de onde parou)
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
            for _ in range(self.setup.product.parts_per_cycle):
                if self.rng.random() < self.scrap_rate:
                    self.scrap = (self.scrap + 1) % U32
                else:
                    self.good = (self.good + 1) % U32
            self.cycles_done += 1
            self.cycle_t, self.tc_done = 0.0, 0
            self.cycle_len = self._new_cycle_len()
            self.cutting = False


class AssemblyCell:
    """Fixação → robô de solda → transportador de saída, acoplados, abastecidos por um buffer do centro."""

    WELD_START, WELD_END = 0.25, 0.80
    AXIS_MOVES = (14, 14, 10, 8, 8, 6)
    FLOW = ("montagem.fixacao", "montagem.robo", "montagem.transportador")   # montante → jusante

    def __init__(self, rng: random.Random, layout: PlantLayout, cfg: PlantConfig, faults: bool,
                 seed: int = 0) -> None:
        self.rng = rng
        self.cfg = cfg
        self.scrap_rate = cfg.scrap_rate["cell"]
        self.robot_fault = FaultProcess(rng, "montagem.robo", 3 * 3600, 6 * 60, faults)
        self.fix_fault = FaultProcess(rng, "montagem.fixacao", 4 * 3600, 4 * 60, faults)
        self.conv_fault = FaultProcess(rng, "montagem.transportador", 8 * 3600, 3 * 60, faults)
        self.micro = MicroStopProcess(rng, cfg.micro_stops_per_h["cell"], cfg.micro_stop_s)
        self.setup = SetupProcess(_machine(layout, "montagem", "fixacao").products, cfg.setup_s["cell"],
                                  cfg.setup_offset_s["cell"], cfg.setups)
        self.cylinders = [
            # nome, frações do golpe, contador CLP inicial, vida inicial, base ms, desgaste ms/1000 ciclos
            Cylinder("A1", (0.05,), 12_000, 1_250_000, 180.0, 0.004, has_eos=True),
            # A2: vedação gasta — além do desgaste normal, +20 ms a cada 1000 golpes (≈ +40 ms/dia de produção)
            Cylinder("A2", (0.10,), 40_210, 3_400_000, 205.0, 0.012, degrade_ms_per_kcycle=20.0),
            Cylinder("A3", (0.15, 0.90), 65_200, 2_100_000, 160.0, 0.004),
            Cylinder("A4", (0.95,), 63_900, 600_000, 230.0, 0.004),
            # B3 (Sprint 5, predictive.md P-11): novo, saudável até `b3_onset_strokes`, depois +x ms/1000 golpes
            # até 120 % do curso novo → 447 (timeout de curso). Gerador próprio: não muda os sorteios acima.
            Cylinder("B3", (0.50,), 0, 0, 200.0, 0.0, degrade_ms_per_kcycle=cfg.b3_drift_ms_per_kstroke,
                     onset_strokes=cfg.b3_onset_strokes, failure_frac=0.20, counter_mod=U32,
                     own_rng=random.Random(f"{seed}/b3")),
        ]
        for c in self.cylinders:   # tempo de curso inicial coerente com o desgaste
            c.stroke_ms = round(c.nominal_stroke_ms(), 1)
        self.running = False
        self.welding = False
        self.cycle_t = 0.0
        self.cycle_len = self._new_cycle_len()
        self.done_strokes: dict[str, int] = {c.name: 0 for c in self.cylinders}
        self.weld_cycles = 0
        self.cycles_done = 0            # ciclos completos (interno, não rola nem zera com o CLP)
        self.axis = [0] * 6
        self.good = 0
        self.scrap = 0
        self.buffer_s = cfg.buffer_capacity_s
        self.supply_starved = False
        self.stop_reason: Optional[str] = None
        self.flags = {k: {"starved": False, "blocked": False} for k in self.FLOW}
        self._any_flag = False
        self.recorders = {(k, f): IntervalRecorder() for k in self.FLOW for f in ("starved", "blocked")}

    @property
    def ideal(self) -> float:
        return self.setup.product.ideal_cycle_time_s

    def _new_cycle_len(self) -> float:
        loss = self.cfg.speed_loss_pct / 100.0 + self.rng.uniform(0.0, self.cfg.speed_jitter_pct / 100.0)
        return round(self.ideal * (1.0 + loss), 1)

    @property
    def faults(self) -> list[FaultProcess]:
        return [self.robot_fault, self.fix_fault, self.conv_fault]

    def _fault_of(self, key: str) -> FaultProcess:
        return {"montagem.robo": self.robot_fault, "montagem.fixacao": self.fix_fault,
                "montagem.transportador": self.conv_fault}[key]

    def _reset_cycle(self) -> None:
        self.cycle_t = 0.0
        self.done_strokes = {c.name: 0 for c in self.cylinders}

    def abort_cycle(self) -> None:
        """Falha imposta (desgaste/API): célula para e o ciclo em curso é abortado."""
        self._reset_cycle()
        self.running, self.welding, self.stop_reason = False, False, "fault"

    def _run(self, dt: float, now: float, cal: CalendarState, plant_s: float) -> None:
        self.stop_reason = None
        if any(f.active for f in self.faults):
            for f in self.faults:
                if f.active:
                    f.tick_fault(dt)
            self.running, self.stop_reason = False, "fault"
            return
        if self.micro.active:
            self.micro.tick(dt)
            self.running, self.stop_reason = False, "micro"
            return
        if not cal.production:
            self.running, self.stop_reason = False, "calendar"
            return
        if self.setup.active:
            self.setup.tick(dt)
            self.running, self.stop_reason = False, "setup"
            return
        if self.setup.maybe_start(cal, plant_s, now):
            self._reset_cycle()
            self.cycle_len = self._new_cycle_len()
            self.running, self.stop_reason = False, "setup"
            return
        if self.supply_starved:
            if self.buffer_s < self.cfg.buffer_resume_s:
                self.running, self.stop_reason = False, "supply"
                return
            self.supply_starved = False
        started = [f.tick_running(dt, now) for f in self.faults]
        if any(started):
            self.running, self.stop_reason = False, "fault"
            self._reset_cycle()
            return
        if self.micro.tick_running(dt, now, n_origins=len(self.FLOW)):
            self.running, self.stop_reason = False, "micro"
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
            self.cycles_done += 1
            self.axis = [(a + n) % U32 for a, n in zip(self.axis, self.AXIS_MOVES)]
            for _ in range(self.setup.product.parts_per_cycle):
                if self.rng.random() < self.scrap_rate:
                    self.scrap = (self.scrap + 1) % U32
                else:
                    self.good = (self.good + 1) % U32
            self._reset_cycle()
            self.cycle_len = self._new_cycle_len()

    def step(self, dt: float, now: float, cal: CalendarState, plant_s: float, upstream_running: bool) -> None:
        self._run(dt, now, cal, plant_s)
        if not self.running:
            self.welding = False
        # buffer centro → célula (determinístico, sem sorteio)
        if upstream_running:
            self.buffer_s = min(self.cfg.buffer_capacity_s, self.buffer_s + self.cfg.buffer_refill_per_s * dt)
        if self.running:
            self.buffer_s -= dt
            if self.buffer_s <= 1e-9:
                self.buffer_s = 0.0
                self.supply_starved = True
        self._update_flags(now)

    def _update_flags(self, now: float) -> None:
        if self.stop_reason not in ("fault", "micro", "supply") and not self._any_flag:
            return                       # caminho quente: rodando/pausa/setup sem flags ativas
        if self.stop_reason == "fault":
            origins = {i for i, k in enumerate(self.FLOW) if self._fault_of(k).active}
        elif self.stop_reason == "micro" and self.micro.origin is not None:
            origins = {self.micro.origin}
        else:
            origins = set()
        for i, k in enumerate(self.FLOW):
            if origins:
                starved = i not in origins and any(j < i for j in origins)
                blocked = i not in origins and any(j > i for j in origins)
            else:
                starved = self.stop_reason == "supply"
                blocked = False
            self.flags[k]["starved"] = starved
            self.flags[k]["blocked"] = blocked
            self.recorders[(k, "starved")].update(starved, now)
            self.recorders[(k, "blocked")].update(blocked, now)
        self._any_flag = any(f["starved"] or f["blocked"] for f in self.flags.values())


# ── Planta ────────────────────────────────────────────────────────────────────

ANALOG_ROLES = {"analog", "speed", "current"}
ShiftsArg = Union[None, str, Sequence[ShiftDef]]


class PlantModel:
    def __init__(
        self,
        seed: int = 42,
        layout: PlantLayout = PLANTA_MANAUS,
        faults: bool = True,
        shifts: ShiftsArg = "standard",
        start_second_of_day: int = 6 * 3600,
        start_weekday: int = 0,
        config: Optional[PlantConfig] = None,
    ) -> None:
        """`shifts`: "standard" (calendário do layout, com pausas), None/"" (24 h, sem setups),
        uma especificação "06:00-14:00,14:00-22:00" (sem pausas) ou uma lista de `ShiftDef`."""
        self.seed = seed
        self.layout = layout
        self.config = config or PlantConfig()
        self.rng = random.Random(seed)
        self.noise = random.Random(seed ^ 0x5EED_A5A5)
        if shifts == "standard":
            defs: Sequence[ShiftDef] = layout.shifts
        elif not shifts:
            defs = ()
        elif isinstance(shifts, str):
            defs = _legacy_shift_defs(shifts)
        else:
            defs = shifts
        self.calendar = ShiftCalendar(defs, start_weekday)
        self.shifts = [(_hhmm(d.start_local), _hhmm(d.end_local)) for d in defs]
        self.start_sod = start_second_of_day
        self.ticks = 0
        cfg = self.config
        self.torno = MachiningMachine(self.rng, "usinagem.torno", _machine(layout, "usinagem", "torno"), cfg,
                                      "torno", 2 * 3600, 5 * 60, faults)
        self.centro = MachiningMachine(self.rng, "usinagem.centro", _machine(layout, "usinagem", "centro"), cfg,
                                       "centro", 3 * 3600, 8 * 60, faults, tool_changes=4)
        self.cell = AssemblyCell(self.rng, layout, cfg, faults, seed=seed)
        self.cal_state = self.calendar.at(self.start_sod)
        self.trackers: dict[str, BoolTracker] = {}
        self._keys = {signal_key(line, m, s) for line, m in layout.machines() for s in m.signals}
        self._roles = {signal_key(line, m, s): s.role for line, m in layout.machines() for s in m.signals}
        for key, role in self._roles.items():
            if role == "run_state":
                self.trackers[key] = BoolTracker()
        self._plc_code = {p.code: p.plc_code for p in layout.products}
        self._machine_names = {f"{line.key}.{m.key}": m.name for line, m in layout.machines()}
        self.wear = WearModel(self, repair_mode=cfg.wear_repair, failures=faults and cfg.wear_failures)

    # tempo
    @property
    def sim_time_s(self) -> float:
        return round(self.ticks * TICK_S, 1)

    def plant_seconds(self, sim_time_s: Optional[float] = None) -> float:
        """Segundos de planta (desde 00:00 local do dia 0) para um tempo simulado."""
        return self.start_sod + (self.sim_time_s if sim_time_s is None else sim_time_s)

    def step(self) -> None:
        now = (self.ticks + 1) * TICK_S
        plant_s = self.start_sod + now
        cal = self.calendar.at(plant_s)
        self.cal_state = cal
        self.torno.step(TICK_S, now, cal, plant_s)
        self.centro.step(TICK_S, now, cal, plant_s)
        self.cell.step(TICK_S, now, cal, plant_s, upstream_running=self.centro.running)
        self.wear.tick(TICK_S, now)
        self.ticks += 1
        values = self._run_states()
        for key, tr in self.trackers.items():
            tr.update(values[key], TICK_S)

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

    # falhas impostas (desgaste / API de controle da manutenção)
    def fault_process(self, machine: str) -> FaultProcess:
        cell = self.cell
        procs = {self.torno.key: self.torno.fault, self.centro.key: self.centro.fault,
                 "montagem.robo": cell.robot_fault, "montagem.fixacao": cell.fix_fault,
                 "montagem.transportador": cell.conv_fault}
        try:
            return procs[machine]
        except KeyError:
            raise KeyError(machine) from None

    def machine_name(self, machine: str) -> str:
        return self._machine_names[machine]

    def machine_key(self, ref: str) -> str:
        """Chave da máquina a partir da chave ("montagem.robo") ou do nome ("Robô de Solda")."""
        if ref in self._machine_names:
            return ref
        for k, name in self._machine_names.items():
            if name.lower() == ref.strip().lower():
                return k
        raise KeyError(ref)

    def inject_fault(self, machine: str, code: int, duration_s: Optional[float] = None, source: str = "forced",
                     **extra) -> dict:
        """Falha agora na máquina (para e aborta o ciclo). `duration_s=None` = até `clear_fault()`."""
        fp = self.fault_process(machine)
        ev = fp.inject(code, self.sim_time_s, duration_s, source, **extra)
        if machine in (self.torno.key, self.centro.key):
            (self.torno if machine == self.torno.key else self.centro).abort_cycle()
        else:
            self.cell.abort_cycle()
        return ev

    def clear_fault(self, machine: str, only_item: Optional[str] = None) -> int:
        """Encerra a falha ativa da máquina (0 = não havia). `only_item`: só se a falha é desse item de desgaste."""
        fp = self.fault_process(machine)
        ev = fp.active_event
        if only_item is not None and (ev is None or ev.get("item") != only_item):
            return 0
        return fp.clear(self.sim_time_s)

    # valores
    def _run_states(self) -> dict[str, bool]:
        """Só os sinais run_state (caminho quente do passo de 0,1 s; o teste garante = _discrete())."""
        t, c, cell = self.torno, self.centro, self.cell
        return {
            "usinagem.torno.run_state": t.running, "usinagem.torno.spindle.run": t.cutting,
            "usinagem.torno.servo_x.run": t.cutting, "usinagem.torno.servo_z.run": t.cutting,
            "usinagem.centro.run_state": c.running, "usinagem.centro.spindle.run": c.cutting,
            "usinagem.centro.coolant.run": c.cutting, "montagem.robo.run_state": cell.running,
            "montagem.fixacao.run_state": cell.running, "montagem.transportador.motor.run": cell.running,
            "montagem.transportador.motor_b.run": cell.running,
        }

    def _discrete(self) -> dict[str, float | int | bool]:
        t, c, cell = self.torno, self.centro, self.cell
        cell_code = self._plc_code[cell.setup.product.product]
        fl = cell.flags
        v: dict[str, float | int | bool] = {
            "usinagem.torno.run_state": t.running,
            "usinagem.torno.fault_state": t.fault.code,
            "usinagem.torno.parts_good": t.good,
            "usinagem.torno.parts_scrap": t.scrap,
            "usinagem.torno.spindle.run": t.cutting,
            "usinagem.torno.servo_x.run": t.cutting,
            "usinagem.torno.servo_z.run": t.cutting,
            "usinagem.torno.product_code": self._plc_code[t.setup.product.product],
            "usinagem.centro.run_state": c.running,
            "usinagem.centro.fault_state": c.fault.code,
            "usinagem.centro.parts_good": c.good,
            "usinagem.centro.parts_scrap": c.scrap,
            "usinagem.centro.spindle.run": c.cutting,
            "usinagem.centro.gripper.cycles": c.gripper,
            "usinagem.centro.coolant.run": c.cutting,
            "usinagem.centro.product_code": self._plc_code[c.setup.product.product],
            "montagem.robo.run_state": cell.running,
            "montagem.robo.fault_state": cell.robot_fault.code,
            "montagem.robo.weld_cycles": cell.weld_cycles,
            "montagem.fixacao.run_state": cell.running,
            "montagem.fixacao.fault_state": cell.fix_fault.code,
            "montagem.transportador.fault_state": cell.conv_fault.code,
            "montagem.transportador.motor.run": cell.running,
            "montagem.transportador.motor_b.run": cell.running,
            "montagem.transportador.parts_good": cell.good,
            "montagem.transportador.parts_scrap": cell.scrap,
        }
        for k in AssemblyCell.FLOW:
            v[f"{k}.product_code"] = cell_code
            v[f"{k}.starved"] = fl[k]["starved"]
            v[f"{k}.blocked"] = fl[k]["blocked"]
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
        w = self.wear.items
        f_tt = w["usinagem.torno.tool"].wear_frac
        f_ct = w["usinagem.centro.tool"].wear_frac
        f_mot = w["montagem.transportador.motor"].wear_frac
        f_pump = w["usinagem.centro.coolant"].wear_frac

        def on(flag: bool, val: float, sd: float, idle: float = 0.0) -> float:
            return round(max(0.0, val + n(0, sd)) if flag else max(0.0, idle + abs(n(0, idle * 0.1 + 1e-6))), 2)

        return {
            "usinagem.torno.spindle.speed": on(t.cutting, 3200.0, 15.0),
            "usinagem.torno.spindle.current": on(t.cutting, 22.0 * (1 + 0.25 * f_tt ** 2), 0.8, 0.4),
            "usinagem.torno.servo_x.current": on(t.cutting, 4.5, 0.3, 0.2),
            "usinagem.torno.servo_z.current": on(t.cutting, 5.2, 0.3, 0.2),
            "usinagem.centro.spindle.speed": on(c.cutting, 8000.0, 40.0),
            "usinagem.centro.spindle.current": on(c.cutting, 18.5 * (1 + 0.25 * f_ct ** 2), 0.7, 0.5),
            "usinagem.centro.coolant.current": on(c.cutting, 6.5 * (1 + 0.3 * f_pump ** 2), 0.2),
            "montagem.transportador.motor.current": on(cell.running, 3.2 * (1 + 0.3 * f_mot ** 2), 0.1),
            # vibração (mm/s RMS) cresce com o desgaste do rolamento; parado ≈ ruído de fundo
            "usinagem.centro.coolant.vibration": on(c.cutting, 1.1 + 6.0 * f_pump ** 4, 0.08, 0.05),
            "montagem.transportador.motor.vibration": on(cell.running, 1.1 + 6.0 * f_mot ** 4, 0.08, 0.05),
            **self._motor_b(),
        }

    def _motor_b(self) -> dict[str, float]:
        """Motor B (Sprint 5): sinais da preditiva DENTRO do contrato de determinismo (predictive.md P-11 item 5):
        ruído = função da seed, da tag e do bloco de 10 s simulados — mesmo valor no mesmo instante, qualquer
        que seja a frequência de exportação. Corrente RMS depende da carga (produto ativo): deriva sem falha."""
        cell = self.cell
        k = "montagem.transportador.motor_b"
        if not cell.running:
            return {f"{k}.current": 0.0, f"{k}.vibration": 0.05, f"{k}.vibration_peak": 0.12}
        b = int(self.sim_time_s // 10)
        cur = self.config.motor_b_current_a.get(cell.setup.product.product, 12.0)
        vib = 1.30 + 0.03 * det_gauss(self.seed, f"{k}.vibration", b)
        return {f"{k}.current": round(cur + 0.05 * det_gauss(self.seed, f"{k}.current", b), 3),
                f"{k}.vibration": round(vib, 3),
                f"{k}.vibration_peak": round(vib * 2.4 + 0.05 * det_gauss(self.seed, f"{k}.peak", b), 3)}

    def degradations(self) -> list[dict]:
        """Verdade da preditiva (predictive.md P-11 item 4): desgaste dos componentes + Motor B (carga)."""
        out = self.wear.degradations()
        out.append({"position": "montagem.transportador.motor_b", "machine": "montagem.transportador",
                    "position_name": "Motor B", "feature": "current", "profile": "load_by_product",
                    "measure": "operating_hours", "onset_s": None, "failure_s": None, "state": "healthy",
                    "current_by_product_a": dict(self.config.motor_b_current_a),
                    "product": self.cell.setup.product.product})
        return out

    def truth(self) -> dict:
        """Verdade de campo para validar a preditiva no servidor (GET /truth da API de controle)."""
        return {"kind": "live", "seed": self.seed, "sim_time_s": self.sim_time_s,
                "degradations": self.degradations(), "failures": self.wear.failures()}

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

    def _events(self) -> dict[str, dict[str, list]]:
        now = self.sim_time_s
        t, c, cell = self.torno, self.centro, self.cell
        out = {
            t.key: {"micro_stops": t.micro.events, "setups": t.setup.events},
            c.key: {"micro_stops": c.micro.events, "setups": c.setup.events},
        }
        for i, k in enumerate(cell.FLOW):
            out[k] = {
                # micro-parada da célula: atribuída à máquina de origem (as outras ficam starved/blocked)
                "micro_stops": [e for e in cell.micro.events if e.get("origin") == i],
                "setups": cell.setup.events,
                "starved": cell.recorders[(k, "starved")].as_list(now),
                "blocked": cell.recorders[(k, "blocked")].as_list(now),
            }
        return out

    def snapshot(self) -> dict:
        """Estado determinístico (sem analógicos) + verdade de campo para o QA."""
        cell = self.cell
        faults = {
            f.key: f.events for f in (self.torno.fault, self.centro.fault, *cell.faults)
        }
        cal = self.cal_state
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
                "events": self._events(),
                "products": {
                    self.torno.key: self.torno.setup.product.product,
                    self.centro.key: self.centro.setup.product.product,
                    **{k: cell.setup.product.product for k in cell.FLOW},
                },
                "calendar": {"production": cal.production, "shift": cal.shift, "in_break": cal.in_break,
                             "plant_s": round(self.plant_seconds(), 1)},
                "buffer_s": round(cell.buffer_s, 1),
                "wear": self.wear.status(),
                "degradations": self.degradations(),
            },
        }
