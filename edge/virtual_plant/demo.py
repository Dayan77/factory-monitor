"""
Roteiro da demonstração pública (docs/sprint6.md §5): a planta virtual que alimenta o tenant DEMO em produção.

    python -m edge.virtual_plant --demo --host 0.0.0.0 --state-file /data/demo_state.json
    (no compose de produção: profile `demo`, serviço demo-plant — docs/operations.md §Demo)

O que o modo `--demo` garante, para a demo ser interessante a qualquer hora:

| roteiro | como |
|---|---|
| turnos 3 × 8 h todos os dias no fuso `America/Sao_Paulo` | relógio da planta = hora local de São Paulo na partida (`--speed 1` fixo); calendário padrão (06–14, 14–22, 22–06, refeição + 2 cafés `PLN-01`) em `ALL_DAYS` — igual ao calendário que o bootstrap do servidor cadastra |
| falhas com código algumas vezes por turno | MTBF das falhas aleatórias × `DEMO_FAULT_MTBF_SCALE` (2,5): ≈ 4 falhas por turno na planta toda (+ falhas por desgaste) |
| um setup por turno | `SET-01` padrão do modelo (troca de produto 1×/turno em cada máquina) |
| micro-paradas | padrão do modelo (3/h torno, 2/h centro, 4/h célula, 15–110 s) |
| desgaste do Cilindro B3 → alerta preditivo + RUL (cenário F) | B3 com `DEMO_B3_STROKES_PER_CYCLE` (12) golpes por ciclo da célula (≈ 20 mil golpes/dia): linha de base do servidor (5 + 20 blocos de 1 000) no 1º dia e meio, deriva a partir de 30 000 golpes (+2 ms/1 000 golpes) → `pct_of_baseline 1,10` (≈ +20 ms) ≈ meio dia depois; falha 447 só em +40 ms |
| troca do cilindro reinicia o desgaste | o `demo-bot` do servidor conclui a OS e chama `POST /replace {"position": "montagem.fixacao.b3"}` na API de controle (`DEMO_VIRTUAL_PLANT_CONTROL_URL` no servidor, com `X-Control-Token`); o desgaste recomeça do zero (mesmo perfil) |
| reinício do container não zera o desgaste | `--state-file`: golpes desde a instalação, contador do CLP e nº da instalação do B3 gravados a cada 30 s (escrita atômica) e na parada |

Saúde (`GET :8091/health`, healthcheck do container): 200 com `{"ok": true, ...}` quando o tempo simulado avança, o
atraso é < 60 s e o relógio da planta está a menos de `DEMO_MAX_CLOCK_DRIFT_S` (300 s) da hora de São Paulo;
senão 503 com o motivo. Desvio maior que 2 × esse limite (mudança de fuso/horário de verão, relógio do host
corrigido) → a planta grava o estado e sai com código 3; o `restart: always` do compose a sobe realinhada.

O modo demo nunca é o padrão (o simulador nunca aponta para produção por padrão): exige `--demo`/`VP_DEMO=1`.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .model import DAY_S, PlantConfig, PlantModel

DEMO_TZ = "America/Sao_Paulo"
B3_ITEM = "montagem.fixacao.b3"
STATE_VERSION = 1


@dataclass
class DemoSettings:
    tz: str = DEMO_TZ
    fault_mtbf_scale: float = 2.5
    b3_strokes_per_cycle: int = 12
    b3_onset_strokes: int = 30_000
    b3_drift_ms_per_kstroke: float = 2.0
    max_clock_drift_s: float = 300.0
    max_lag_s: float = 60.0
    stall_s: float = 30.0
    save_every_s: float = 30.0

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "DemoSettings":
        env = os.environ if env is None else env
        d = cls()
        conv = {"DEMO_TZ": ("tz", str), "DEMO_FAULT_MTBF_SCALE": ("fault_mtbf_scale", float),
                "DEMO_B3_STROKES_PER_CYCLE": ("b3_strokes_per_cycle", int),
                "DEMO_B3_ONSET_STROKES": ("b3_onset_strokes", int),
                "DEMO_B3_DRIFT_MS_PER_KSTROKE": ("b3_drift_ms_per_kstroke", float),
                "DEMO_MAX_CLOCK_DRIFT_S": ("max_clock_drift_s", float)}
        for var, (attr, fn) in conv.items():
            if env.get(var):
                setattr(d, attr, fn(env[var]))
        if d.fault_mtbf_scale <= 0 or d.b3_strokes_per_cycle < 1 or d.b3_drift_ms_per_kstroke <= 0:
            raise ValueError("configuração da demo inválida (escala de MTBF > 0, golpes ≥ 1, deriva > 0)")
        ZoneInfo(d.tz)                 # fuso inválido falha na partida
        return d


def demo_config(ds: DemoSettings) -> PlantConfig:
    cfg = PlantConfig(setups=True, wear_failures=True, wear_repair="auto")
    cfg.fault_mtbf_scale = ds.fault_mtbf_scale
    cfg.b3_strokes_per_cycle = ds.b3_strokes_per_cycle
    cfg.b3_onset_strokes = ds.b3_onset_strokes
    cfg.b3_drift_ms_per_kstroke = ds.b3_drift_ms_per_kstroke
    return cfg


def local_start(tz: str = DEMO_TZ, now: Optional[datetime] = None) -> tuple[int, int, datetime]:
    """(segundo do dia local, dia da semana local 0=segunda, instante UTC) do relógio de parede no fuso."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    loc = now.astimezone(ZoneInfo(tz))
    sod = loc.hour * 3600 + loc.minute * 60 + loc.second
    return sod, loc.weekday(), now


def make_demo_model(seed: int, ds: DemoSettings, now: Optional[datetime] = None) -> PlantModel:
    sod, weekday, t0 = local_start(ds.tz, now)
    model = PlantModel(seed=seed, faults=True, shifts="standard", start_second_of_day=sod, start_weekday=weekday,
                       config=demo_config(ds))
    model.demo = {"tz": ds.tz, "wall_start": t0, "start_sod": sod, "start_weekday": weekday}
    return model


def clock_drift_s(model: PlantModel, now: Optional[datetime] = None) -> float:
    """Relógio da planta (segundos locais desde o 00:00 do dia da partida) − hora local de parede no fuso da demo.
    > 0 = planta adiantada; < 0 = atrasada (atraso do loop ou mudança de fuso)."""
    info = model.demo
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(info["tz"]))
    start_loc = info["wall_start"].astimezone(ZoneInfo(info["tz"]))
    days = (now.date() - start_loc.date()).days
    wall_s = days * DAY_S + now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1e6
    return model.plant_seconds() - wall_s


# ── estado persistente (desgaste do B3) ──────────────────────────────────────

def _b3(model: PlantModel):
    return next(c for c in model.cell.cylinders if c.name == "B3")


def state_of(model: PlantModel) -> dict:
    c = _b3(model)
    it = model.wear.items[B3_ITEM]
    return {"version": STATE_VERSION, "saved_at": datetime.now(timezone.utc).isoformat(), "seed": model.seed,
            "b3": {"strokes_since_install": int(c.strokes_since_install), "plc_counter": int(c.plc_counter),
                   "install_no": int(it.install_no)}}


def save_state(path: str | Path, model: PlantModel) -> dict:
    """Escrita atômica (tmp + rename) do estado de desgaste."""
    doc = state_of(model)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".demo-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return doc


def restore_state(path: str | Path, model: PlantModel) -> Optional[dict]:
    """Recoloca o B3 no ponto de desgaste salvo (antes de subir os servidores). Arquivo ausente/ilegível → None
    (cilindro novo). Só o B3 é restaurado: os demais contadores voltam ao valor inicial e o servidor trata o reset
    (kpis.md C5)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        b3 = doc["b3"]
        strokes = int(b3["strokes_since_install"])
        counter = int(b3["plc_counter"])
        install_no = int(b3.get("install_no", 0))
        if doc.get("version") != STATE_VERSION or strokes < 0 or counter < 0:
            raise ValueError("versão/valores inválidos")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    c = _b3(model)
    it = model.wear.items[B3_ITEM]
    c.strokes = strokes
    c.strokes_at_install = 0
    c.life_cycles = strokes
    c.plc_counter = counter % c.counter_mod
    it.install_no = max(0, install_no)
    model.wear._init_seal(it, c, original=False)
    c.stroke_ms = round(c.nominal_stroke_ms(), 1)
    return doc


# ── saúde ────────────────────────────────────────────────────────────────────

class DemoHealth:
    """Saúde do modo demo: tempo simulado avançando, atraso pequeno, relógio alinhado ao fuso."""

    def __init__(self, model: PlantModel, ds: DemoSettings, clock=time.monotonic) -> None:
        self.model = model
        self.ds = ds
        self._clock = clock
        self._last_sim = model.sim_time_s
        self._last_change = clock()

    def check(self, lag_s: float = 0.0, now: Optional[datetime] = None) -> tuple[bool, dict]:
        t = self._clock()
        if self.model.sim_time_s != self._last_sim:
            self._last_sim, self._last_change = self.model.sim_time_s, t
        stalled_s = t - self._last_change
        drift = clock_drift_s(self.model, now)
        problems = []
        if stalled_s > self.ds.stall_s:
            problems.append(f"tempo simulado parado há {stalled_s:.0f} s")
        if lag_s > self.ds.max_lag_s:
            problems.append(f"atraso de {lag_s:.0f} s")
        if abs(drift) > self.ds.max_clock_drift_s:
            problems.append(f"relógio da planta {drift:+.0f} s fora da hora de {self.ds.tz}")
        cal = self.model.cal_state
        body = {"ok": not problems, "mode": "demo", "sim_time_s": self.model.sim_time_s, "tz": self.ds.tz,
                "clock_drift_s": round(drift, 1), "lag_s": round(lag_s, 1), "shift": cal.shift,
                "production": cal.production, "in_break": cal.in_break, "problems": problems,
                "b3": {"strokes_since_install": _b3(self.model).strokes_since_install,
                       "stroke_ms": _b3(self.model).stroke_ms}}
        return not problems, body

    def must_restart(self, now: Optional[datetime] = None) -> bool:
        """Desvio de relógio grande demais para corrigir no lugar (fuso mudou, relógio do host acertado)."""
        return abs(clock_drift_s(self.model, now)) > 2 * self.ds.max_clock_drift_s
