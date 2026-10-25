"""
Desgaste e falhas por uso dos componentes da Planta Manaus (Sprint 4 — manutenção).

Cada item de desgaste é um componente físico numa posição (ou a ferramenta de um eixo-árvore). A **vida até a
falha** (`life_to_failure`) é sorteada quando o componente é instalado, com um gerador próprio
`random.Random(f"{seed}/{item}/{instalação}")` — independe do tempo real, da velocidade e dos outros
componentes. A falha acontece quando o **uso desde a instalação** atinge essa vida, então o instante de cada falha
deriva do uso (ciclos/horas) e os MTBF/MCBF calculados pelo servidor são reproduzíveis com a mesma seed.
`status()` expõe a verdade de campo (uso, vida sorteada, restante) para os oráculos dos testes.

| tipo (`mode`) | posição | medida | sintoma antes da falha | falha (código → categoria) |
|---|---|---|---|---|
| `seal_leak` | Cilindros A1–A4 (Estação de Fixação) | ciclos | `stroke_time` sobe com os ciclos (linear) | deriva ≥ limiar (27–33 % do curso novo) → 443–446 vazamento na vedação (4xx pneumática, `EQP-03`) |
| `bearing_wear` | motor do transportador, motor da bomba de refrigeração | horas de operação | corrente ×(1 + 0,3·f²), vibração 1,1 + 6·f⁴ mm/s | 252 / 214 rolamento travando, proteção do motor (2xx elétrica, `EQP-02`) |
| `reducer_wear` | Eixos 1–6 do robô | ciclos (movimentos do eixo) | — | Weibull(η = 4,5 M, β = 2,5) condicionada à idade → 132–137 folga no redutor (1xx mecânica, `EQP-01`) |
| `tool_wear` | ferramenta do eixo-árvore (torno, centro) | ciclos de corte | corrente do eixo-árvore ×(1 + 0,25·f²) | Weibull(η = 800 / 380 ciclos, β = 4) → 523 / 524 ferramenta no fim de vida (5xx ferramental, `EQP-05`) |

(`f` = uso / vida até a falha.) Os códigos seguem a tabela de categorias por centena do layout (`FAULT_CATEGORIES`,
docs/domain.md §4.4): vazamento de vedação é pneumático (4xx) e folga de redutor é mecânica (1xx).

Componente já instalado no início da simulação: vida sorteada **condicionada** a ter sobrevivido até a idade
atual (`L = η·((idade/η)^β − ln U)^(1/β)`). Cilindros originais mantêm o modelo de desgaste do Sprint 2 (A2 com a
vedação gasta: +20 ms a cada 1000 golpes) e a vida é o número de ciclos em que a deriva cruza o limiar.

Reparo (`repair_mode`):
- `auto` (padrão): a falha dura um tempo sorteado do mesmo gerador do componente (plantão) e termina com a
  **troca** do componente (desgaste zerado; contador do CLP mantido).
- `manual`: a falha fica ativa até `replace()` (troca) ou `repair()` (reparo paliativo: a máquina volta, mas o
  componente gasto falha de novo logo — margem de 5 % do limiar de deriva para vedações, 1 % da vida para
  redutores, 0,5 % para rolamentos; ferramenta quebrada é sempre trocada).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from .layout import fault_category

if TYPE_CHECKING:   # pragma: no cover
    from .model import Cylinder, FaultProcess, PlantModel

REPAIR_MODES = ("auto", "manual")

# Duração (s simulados) do reparo automático (plantão) por modo de falha: U(lo, hi).
AUTO_REPAIR_S = {
    "seal_leak": (25 * 60, 50 * 60),
    "bearing_wear": (60 * 60, 120 * 60),
    "reducer_wear": (90 * 60, 180 * 60),
    "tool_wear": (6 * 60, 15 * 60),
}
PALLIATIVE_FRAC = {"seal_leak": 0.05, "bearing_wear": 0.005, "reducer_wear": 0.01}

SEAL_LEAK_PCT = (0.27, 0.33)            # deriva do curso (fração do curso novo) que vira vazamento
SEAL_NEW_LIFE = (9_000_000, 3.0)        # vedação nova: vida Weibull (η ciclos, β)
AXIS_LIFE = (4_500_000, 2.5)
AXIS_INITIAL_AGE = (3_400_000, 1_100_000, 2_000_000, 900_000, 700_000, 400_000)   # movimentos
BEARINGS = {
    # id: (máquina, posição, componente, código, η h, β, idade inicial h)
    "montagem.transportador.motor": ("montagem.transportador", "Motor do transportador", "electric_motor",
                                     252, 32_000.0, 2.0, 26_000.0),
    "usinagem.centro.coolant": ("usinagem.centro", "Motor da bomba de refrigeração", "pump",
                                214, 14_000.0, 2.0, 6_000.0),
}
TOOLS = {
    # id: (máquina, posição, código, η ciclos, β)
    "usinagem.torno.tool": ("usinagem.torno", "Eixo-árvore", 523, 800.0, 4.0),
    "usinagem.centro.tool": ("usinagem.centro", "Eixo-árvore", 524, 380.0, 4.0),
}
SEAL_CODES = {"A1": 443, "A2": 444, "A3": 445, "A4": 446, "B3": 447}

def weibull_life(rng: random.Random, eta: float, beta: float, age: float = 0.0) -> float:
    """Vida até a falha ~ Weibull(η, β) condicionada a ter sobrevivido até `age`."""
    u = 1.0 - rng.random()          # (0, 1]
    return eta * ((age / eta) ** beta - math.log(u)) ** (1.0 / beta)


@dataclass
class WearItem:
    id: str                 # "montagem.fixacao.a2"
    machine: str            # chave da máquina, "montagem.fixacao"
    position: str           # nome da posição no servidor
    component_type: str
    mode: str
    measure: str            # "cycles" | "operating_hours"
    code: int
    seed: int
    install_no: int = 0
    usage: float = 0.0      # uso desde a instalação (na medida do item)
    life: float = math.inf  # vida até a falha sorteada para esta instalação
    fail_at: float = math.inf   # uso em que falha (vida; ou uso + margem após reparo paliativo)
    failed: bool = False
    failures: list = field(default_factory=list)
    installs: list = field(default_factory=list)
    starts: int = 0
    installed_s: float = 0.0            # instante (s simulados) da instalação atual
    onset_s: Optional[float] = None     # início da degradação na instalação atual (predictive.md P-11)
    _running: bool = False

    def rng(self, *suffix) -> random.Random:
        return random.Random("/".join(str(x) for x in (self.seed, self.id, self.install_no, *suffix)))

    @property
    def remaining(self) -> float:
        return max(0.0, self.fail_at - self.usage)

    @property
    def wear_frac(self) -> float:
        """Uso / vida (0 = novo, 1 = no fim) — alimenta os sintomas analógicos."""
        return min(1.0, self.usage / self.life) if self.life > 0 else 1.0

    def status(self) -> dict:
        def r(x: float) -> Optional[float]:
            return None if math.isinf(x) else round(x, 3 if self.measure == "operating_hours" else 0)

        return {
            "id": self.id, "machine": self.machine, "position": self.position,
            "component_type": self.component_type, "mode": self.mode, "measure": self.measure,
            "failure_code": self.code, "install_no": self.install_no,
            "usage_since_install": r(self.usage), "life_to_failure": r(self.life),
            "fails_at_usage": r(self.fail_at), "remaining": r(self.remaining),
            "consumed": round(self.usage / self.life, 6) if self.life and not math.isinf(self.life) else None,
            "state": "failed" if self.failed else "ok",
            **({"starts_since_install": self.starts} if self.measure == "operating_hours" else {}),
            "failures": list(self.failures), "replacements": list(self.installs),
        }


class WearModel:
    """Todos os itens de desgaste da planta; avançado pelo PlantModel a cada passo."""

    def __init__(self, plant: "PlantModel", repair_mode: str = "auto", failures: bool = True) -> None:
        if repair_mode not in REPAIR_MODES:
            raise ValueError(f"repair_mode deve ser um de {REPAIR_MODES}")
        self.plant = plant
        self.repair_mode = repair_mode
        self.failures_enabled = failures
        seed = plant.seed
        cell = plant.cell
        self.items: dict[str, WearItem] = {}
        self._cyl: dict[str, "Cylinder"] = {}
        for c in cell.cylinders:
            it = WearItem(f"montagem.fixacao.{c.name.lower()}", "montagem.fixacao", f"Cilindro {c.name}",
                          "pneumatic_cylinder", "seal_leak", "cycles", SEAL_CODES[c.name], seed)
            self._cyl[it.id] = c
            self.items[it.id] = it
            self._init_seal(it, c, original=True)
        self._axes: list[WearItem] = []
        for n in range(1, 7):
            it = WearItem(f"montagem.robo.axis{n}", "montagem.robo", f"Eixo {n}", "robot_axis", "reducer_wear",
                          "cycles", 131 + n, seed, usage=float(AXIS_INITIAL_AGE[n - 1]))
            it.life = it.fail_at = weibull_life(it.rng("life"), *AXIS_LIFE, age=it.usage)
            self.items[it.id] = it
            self._axes.append(it)
        self._bearings: list[tuple[WearItem, Callable[[], bool]]] = []
        flags = {"montagem.transportador.motor": lambda: cell.running,
                 "usinagem.centro.coolant": lambda: plant.centro.cutting}
        for iid, (mk, pos, ctype, code, eta, beta, age) in BEARINGS.items():
            it = WearItem(iid, mk, pos, ctype, "bearing_wear", "operating_hours", code, seed, usage=age)
            it.life = it.fail_at = weibull_life(it.rng("life"), eta, beta, age=age)
            self.items[iid] = it
            self._bearings.append((it, flags[iid]))
        self._tools: list[tuple[WearItem, object]] = []
        machines = {"usinagem.torno": plant.torno, "usinagem.centro": plant.centro}
        for iid, (mk, pos, code, eta, beta) in TOOLS.items():
            it = WearItem(iid, mk, pos, "tool", "tool_wear", "cycles", code, seed)
            it.life = it.fail_at = weibull_life(it.rng("life"), eta, beta)
            self.items[iid] = it
            self._tools.append((it, machines[mk]))
        self._last_cell_cycles = cell.cycles_done
        self._last_tool_cycles = [m.cycles_done for _, m in self._tools]
        self._pending: dict[str, float] = {}      # item → instante do fim do reparo automático

    # ── instalação ─────────────────────────────────────────────────────
    def _init_seal(self, it: WearItem, c: "Cylinder", original: bool) -> None:
        if c.failure_frac is not None:
            # perfil lento determinístico (B3, predictive.md P-11): falha quando a deriva nominal chega a
            # failure_frac · base, depois de `onset_strokes` golpes saudáveis; a troca reinstala o mesmo perfil
            c.leak_threshold_ms = c.failure_frac * c.base_ms
            rate = c.degrade_ms_per_kcycle / 1000.0
            life = c.onset_strokes + c.leak_threshold_ms / rate if rate > 0 else math.inf
            it.usage = float(c.strokes_since_install)
            it.life = it.fail_at = life
            return
        lo, hi = SEAL_LEAK_PCT
        rng = it.rng("seal")
        threshold = c.base_ms * rng.uniform(lo, hi)
        if original:
            # deriva = wear·vida/1000 + degrade·(vida − vida0)/1000 → vida em que cruza o limiar
            life0 = c.life_cycles - c.strokes_since_install
            rate = c.wear_ms_per_kcycle + c.degrade_ms_per_kcycle
            life = (threshold * 1000.0 + c.degrade_ms_per_kcycle * life0) / rate if rate > 0 else math.inf
            life = max(life, float(c.life_cycles) + 1.0)
        else:
            life = weibull_life(rng, *SEAL_NEW_LIFE)
            c.wear_ms_per_kcycle = threshold * 1000.0 / life
            c.degrade_ms_per_kcycle = 0.0
        c.leak_threshold_ms = threshold
        it.usage = float(c.life_cycles)
        it.life = it.fail_at = life

    def _install_new(self, it: WearItem, now: float, reason: str, reset_counter: bool) -> dict:
        old = {"install_no": it.install_no, "usage_at_removal": round(it.usage, 3), "reason": reason,
               "at_s": round(now, 1), "plc_counter_reset": reset_counter}
        it.install_no += 1
        it.usage = 0.0
        it.starts = 0
        it.failed = False
        it.installed_s = round(now, 1)
        it.onset_s = None
        if it.mode == "seal_leak":
            c = self._cyl[it.id]
            c.life_cycles = 0
            c.strokes_at_install = c.strokes
            self._init_seal(it, c, original=False)
            c.stroke_ms = round(c.nominal_stroke_ms(), 1)
            if reset_counter:
                c.plc_counter = 0
        elif it.mode == "reducer_wear":
            it.life = it.fail_at = weibull_life(it.rng("life"), *AXIS_LIFE)
            if reset_counter:
                n = int(it.id[-1])
                self.plant.cell.axis[n - 1] = 0
        elif it.mode == "bearing_wear":
            _, _, _, _, eta, beta, _ = BEARINGS[it.id]
            it.life = it.fail_at = weibull_life(it.rng("life"), eta, beta)
        elif it.mode == "tool_wear":
            _, _, _, eta, beta = TOOLS[it.id]
            it.life = it.fail_at = weibull_life(it.rng("life"), eta, beta)
        it.installs.append(old)
        return old

    # ── passo ───────────────────────────────────────────────────────────
    def tick(self, dt: float, now: float) -> None:
        plant = self.plant
        cell = plant.cell
        for it, running in self._bearings:
            on = running()
            if on:
                if not it._running:
                    it.starts += 1
                it.usage += dt / 3600.0
                if it.usage >= it.fail_at:
                    self._fail(it, now)
            it._running = on
        if cell.running:
            for iid, c in self._cyl.items():
                it = self.items[iid]
                it.usage = float(c.life_cycles)
                if it.onset_s is None and c.onset_strokes and c.degraded_strokes() > 0:
                    it.onset_s = round(now, 1)
                if it.usage >= it.fail_at:
                    self._fail(it, now)
        if cell.cycles_done != self._last_cell_cycles:
            n = cell.cycles_done - self._last_cell_cycles
            self._last_cell_cycles = cell.cycles_done
            for it, moves in zip(self._axes, cell.AXIS_MOVES):
                it.usage += n * moves
                if it.usage >= it.fail_at:
                    self._fail(it, now)
        for i, (it, m) in enumerate(self._tools):
            if m.cycles_done != self._last_tool_cycles[i]:
                it.usage += m.cycles_done - self._last_tool_cycles[i]
                self._last_tool_cycles[i] = m.cycles_done
                if it.usage >= it.fail_at:
                    self._fail(it, now)
        if self._pending:
            for iid in [k for k in self._pending]:
                it = self.items[iid]
                if not self.fault_process(it.machine).active:     # o plantão terminou o reparo
                    del self._pending[iid]
                    self._close_failure(it, now, "auto_replace")
                    self._install_new(it, now, "failure", reset_counter=False)

    def _fail(self, it: WearItem, now: float) -> None:
        if it.failed or not self.failures_enabled:
            return
        fp = self.fault_process(it.machine)
        if fp.active:              # máquina já parada por outra falha: a deste item espera ela voltar
            return
        it.failed = True
        duration = None
        if self.repair_mode == "auto":
            lo, hi = AUTO_REPAIR_S[it.mode]
            duration = round(it.rng("repair", len(it.failures)).uniform(lo, hi), 1)
            self._pending[it.id] = now + duration
        it.failures.append({"start_s": round(now, 1), "usage": round(it.usage, 3), "code": it.code,
                            "install_no": it.install_no, "end_s": None, "action": None})
        self.plant.inject_fault(it.machine, it.code, duration_s=duration, source="wear", position=it.position,
                                item=it.id)

    def _close_failure(self, it: WearItem, now: float, action: str) -> None:
        it.failed = False
        if it.failures and it.failures[-1]["end_s"] is None:
            it.failures[-1]["end_s"] = round(now, 1)
            it.failures[-1]["action"] = action

    # ── ações de manutenção (API de controle) ──────────────────────────
    def fault_process(self, machine: str) -> "FaultProcess":
        return self.plant.fault_process(machine)

    def find(self, ref: str) -> WearItem:
        """Item por id ("montagem.fixacao.a2") ou "Máquina / Posição" ("Estação de Fixação / Cilindro A2")."""
        if ref in self.items:
            return self.items[ref]
        norm = " / ".join(p.strip().lower() for p in ref.replace("/", " / ").split("/") if p.strip())
        for it in self.items.values():
            mname = self.plant.machine_name(it.machine)
            if norm in (f"{mname} / {it.position}".lower(), f"{it.machine} / {it.position}".lower()) \
                    and it.mode != "tool_wear":
                return it
        raise KeyError(ref)

    def replace(self, ref: str, reset_counter: bool = False, reason: str = "preventive_replacement") -> dict:
        it = self.find(ref)
        now = self.plant.sim_time_s
        was_failed = it.failed
        if was_failed:
            self._pending.pop(it.id, None)
            self._close_failure(it, now, "replace")
            reason = "failure"
            self.plant.clear_fault(it.machine, only_item=it.id)
        removed = self._install_new(it, now, reason, reset_counter)
        return {"item": it.id, "removed": removed, "repaired_failure": was_failed,
                "installed": self.item_status(it.id)}

    def repair(self, machine: str) -> dict:
        """Libera a máquina: limpa a falha ativa. Falha por desgaste sem troca = reparo paliativo (o componente
        continua gasto e falha de novo após uma margem pequena); ferramenta quebrada é trocada."""
        now = self.plant.sim_time_s
        cleared = self.plant.clear_fault(machine)
        out: dict = {"machine": machine, "cleared_code": cleared, "wear_item": None}
        for it in self.items.values():
            if it.machine == machine and it.failed:
                self._pending.pop(it.id, None)
                if it.mode == "tool_wear":
                    self._close_failure(it, now, "replace")
                    self._install_new(it, now, "failure", reset_counter=False)
                    out["wear_item"] = {"id": it.id, "action": "replaced"}
                    continue
                self._close_failure(it, now, "palliative_repair")
                if it.mode == "seal_leak":
                    c = self._cyl[it.id]
                    rate = (c.wear_ms_per_kcycle + c.degrade_ms_per_kcycle) / 1000.0
                    margin = PALLIATIVE_FRAC["seal_leak"] * c.leak_threshold_ms / rate if rate > 0 else 1000.0
                else:
                    margin = PALLIATIVE_FRAC[it.mode] * it.life
                it.fail_at = it.usage + margin
                out["wear_item"] = {"id": it.id, "action": "palliative_repair", "fails_at_usage": round(it.fail_at, 3)}
        return out

    def force(self, ref: str) -> dict:
        """Falha por desgaste agora (o uso salta para a vida até a falha)."""
        it = self.find(ref)
        if it.failed:
            raise ValueError(f"{it.id} já está em falha")
        fp = self.fault_process(it.machine)
        if fp.active:
            self.plant.clear_fault(it.machine)
        it.fail_at = it.usage
        enabled, self.failures_enabled = self.failures_enabled, True
        try:
            self._fail(it, self.plant.sim_time_s)
        finally:
            self.failures_enabled = enabled
        return self.item_status(it.id)

    def stroke_indicators(self, iid: str) -> dict:
        c = self._cyl[iid]
        return {"stroke_ms": c.stroke_ms, "nominal_stroke_ms": round(c.nominal_stroke_ms(), 2),
                "base_ms": c.base_ms, "drift_ms": round(c.nominal_stroke_ms() - c.base_ms, 2),
                "leak_threshold_ms": round(c.leak_threshold_ms, 2)}

    def status(self) -> dict:
        return {iid: self.item_status(iid) for iid in self.items}

    # ── verdade de campo da preditiva (predictive.md P-11) ─────────────
    def degradations(self) -> list[dict]:
        """Um registro por (item, sinal) com degradação: perfil, início, falha (instalação atual) e estado."""
        out = []
        for iid, c in self._cyl.items():
            it = self.items[iid]
            if c.onset_strokes:
                profile, onset = "linear_after_onset", it.onset_s
                onset_usage: Optional[float] = float(c.onset_strokes)
            else:
                profile = "accelerated" if c.degrade_ms_per_kcycle > 0 else "linear_wear"
                onset, onset_usage = it.installed_s, 0.0
            out.append(self._degradation(it, "stroke_time", profile, onset, onset_usage, {
                "base_ms": c.base_ms, "nominal_ms": round(c.nominal_stroke_ms(), 2),
                "failure_limit_ms": round(c.base_ms + c.leak_threshold_ms, 2),
                "iso14224_mode": "PDE" if c.onset_strokes else "ELP"}))
        for it, _ in self._bearings:
            for feature in ("vibration", "current"):
                out.append(self._degradation(it, feature, "bearing_wear_quartic", it.installed_s, 0.0, {
                    "iso14224_mode": "BRD", **(self.item_status(it.id).get("indicators") or {})}))
        return out

    def _degradation(self, it: WearItem, feature: str, profile: str, onset: Optional[float],
                     onset_usage: Optional[float], extra: dict) -> dict:
        fails = [f for f in it.failures if f["install_no"] == it.install_no]
        if it.failed:
            state = "failed"
        elif onset is not None and (onset_usage is None or it.usage > onset_usage):
            state = "degrading"
        else:
            state = "healthy"
        return {"position": it.id, "machine": it.machine, "position_name": it.position, "feature": feature,
                "profile": profile, "measure": it.measure, "install_no": it.install_no,
                "installed_s": it.installed_s, "onset_s": onset, "onset_usage": onset_usage,
                "failure_s": fails[0]["start_s"] if fails else None,
                "failure_usage": None if math.isinf(it.fail_at) else round(it.fail_at, 3),
                "usage": round(it.usage, 3), "remaining_usage": None if math.isinf(it.remaining)
                else round(it.remaining, 3), "failure_code": it.code, "state": state, **extra}

    def failures(self) -> list[dict]:
        """Todas as falhas por desgaste já ocorridas (todas as instalações), em ordem de início."""
        out = [{"position": it.id, "machine": it.machine, "position_name": it.position, "mode": it.mode, **f}
               for it in self.items.values() for f in it.failures]
        return sorted(out, key=lambda f: f["start_s"])

    def item_status(self, iid: str) -> dict:
        it = self.items[iid]
        st = it.status()
        if iid in self._cyl:
            st["indicators"] = self.stroke_indicators(iid)
        elif it.mode == "bearing_wear":
            f = it.wear_frac
            st["indicators"] = {"current_factor": round(1 + 0.3 * f * f, 4),
                                "vibration_mm_s": round(1.1 + 6.0 * f ** 4, 3)}
        elif it.mode == "tool_wear":
            f = it.wear_frac
            st["indicators"] = {"spindle_current_factor": round(1 + 0.25 * f * f, 4)}
        elif it.mode == "reducer_wear":
            eta, beta = AXIS_LIFE
            st["indicators"] = {"weibull_eta": eta, "weibull_beta": beta,
                                "population_failure_probability": round(1 - math.exp(-(it.usage / eta) ** beta), 6)}
        cat = fault_category(it.code)
        st["category"], st["reason_code"] = cat["category"], cat["reason_code"]
        return st
