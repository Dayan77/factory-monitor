"""
Planos de manutenção da Planta Manaus (Sprint 4): cria/atualiza via REST um plano por posição da planta virtual.

    # demonstração acelerada (planta com --speed 60): alguns planos chegam a due_soon/due em ~1 h real
    python -m edge.src.provision_maintenance --speed 60 --demo-minutes 60
    # intervalos reais de fábrica (planta em tempo real)
    python -m edge.src.provision_maintenance --profile realistic

Contrato:
    GET   /api/plants/ · /api/lines/?plant_id= · /api/machines/?line_id= · /api/positions/?machine_id=
    GET   /api/maintenance-plans/?position_id=… | ?machine_id=…
    POST  /api/maintenance-plans/  {position_id | machine_id, name, task_list:[{id,text}], estimated_hours,
                                    wo_type, interval_cycles?, interval_hours?, interval_starts?, interval_days?,
                                    stops_machine, last_done_at?}
    PATCH /api/maintenance-plans/{id}  (só os campos de configuração que divergem)

Idempotente pelo **nome** dentro da posição/máquina: rodar de novo não duplica; mudar de perfil (ou de --speed)
ajusta os intervalos com PATCH. `last_done_at` (planos de calendário que "já existiam no papel") só vai na
criação — é estado do plano, não configuração; rodar de novo não zera o plano.

Perfil `demo`: o intervalo de uso de cada plano = uso esperado da posição na janela da demonstração
(`--demo-minutes` reais × `--speed` para ciclos/partidas; horas de operação NÃO aceleram, porque o servidor as
integra pelo relógio real das leituras) ÷ `demo_target`, a partir do ritmo medido na planta virtual (`RATES`). Com
`demo_target` 1,5 o plano passa de 80 % por volta de 32 min e vence (due) em ~40 min; com 1,0 chega a due_soon em
~48 min. Planos de calendário usam `last_done_at` no passado (o servidor conta dias corridos no relógio real).
Ritmo real varia ±10 % com a seed e a hora de partida: o estado exato vem do servidor (GET /api/maintenance-plans/status).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp

from .provision import DEFAULT_SERVER_URL, Api, ProvisionError

PLANT = "Planta Manaus"
PLAN_SUFFIX = " (planta virtual)"

# Ritmo de uso por hora SIMULADA por (máquina, posição), medido na planta virtual com o calendário padrão
# (média das seeds 42 e 7, 48 h: turnos com pausas, setups, falhas aleatórias e por desgaste, micro-paradas,
# célula parada por falta de peça). cycles = ciclos da posição; hours = horas de operação; starts = partidas.
# `test_demo_rates_match_virtual_plant` confere contra o modelo.
CELL_CYCLES_PER_H = 57.4
RATES: dict[tuple[str, Optional[str]], dict[str, float]] = {
    ("Estação de Fixação", "Cilindro A1"): {"cycles": CELL_CYCLES_PER_H},
    ("Estação de Fixação", "Cilindro A2"): {"cycles": CELL_CYCLES_PER_H},
    ("Estação de Fixação", "Cilindro A3"): {"cycles": 2 * CELL_CYCLES_PER_H},
    ("Estação de Fixação", "Cilindro A4"): {"cycles": CELL_CYCLES_PER_H},
    **{("Robô de Solda", f"Eixo {n}"): {"cycles": mv * CELL_CYCLES_PER_H, "hours": 0.70}
       for n, mv in zip(range(1, 7), (14, 14, 10, 8, 8, 6))},
    ("Transportador de Saída", "Motor do transportador"): {"hours": 0.70, "starts": 3.8},
    ("Centro de Usinagem", "Motor da bomba de refrigeração"): {"hours": 0.54, "starts": 22.8},
    ("Centro de Usinagem", "Garra do trocador de ferramentas"): {"cycles": 86.7},
    ("Centro de Usinagem", "Eixo-árvore"): {"hours": 0.54},
    ("Torno CNC", "Eixo-árvore"): {"hours": 0.545},
}

LOTO = {"id": "t1", "text": "Bloquear e etiquetar a energia (LOTO)"}
RELEASE = {"id": "t9", "text": "Testar e liberar para a produção"}


def _tasks(*items: str) -> list[dict]:
    return [LOTO, *({"id": f"t{i}", "text": t} for i, t in enumerate(items, start=2)), RELEASE]


# Catálogo: intervalos reais (`realistic`) e o gatilho escalado na demo (`demo_trigger` → `demo_target`).
# Gatilhos de uso sem escala na demo ficam com o valor real (não interferem: muito maiores).
PLANS: list[dict[str, Any]] = [
    {"machine": "Estação de Fixação", "position": "Cilindro A1", "name": "Troca de vedações — Cilindro A1",
     "tasks": _tasks("Trocar o kit de vedações", "Medir o tempo de curso (referência nova)"),
     "estimated_hours": 1.5, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"cycles": 2_000_000, "days": 180}, "demo_trigger": "cycles", "demo_target": 1.5},
    {"machine": "Estação de Fixação", "position": "Cilindro A2", "name": "Troca de vedações — Cilindro A2",
     "tasks": _tasks("Trocar o kit de vedações", "Medir o tempo de curso (referência nova)"),
     "estimated_hours": 1.5, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"cycles": 2_000_000, "days": 180}, "demo_trigger": "cycles", "demo_target": 1.0},
    {"machine": "Estação de Fixação", "position": "Cilindro A3", "name": "Inspeção e lubrificação — Cilindro A3",
     "tasks": _tasks("Inspecionar haste e vedações", "Lubrificar"),
     "estimated_hours": 0.5, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"cycles": 1_000_000}, "demo_trigger": "cycles", "demo_target": 0.5},
    {"machine": "Estação de Fixação", "position": "Cilindro A4", "name": "Troca de vedações — Cilindro A4",
     "tasks": _tasks("Trocar o kit de vedações"),
     "estimated_hours": 1.5, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"cycles": 2_000_000, "days": 180}, "demo_trigger": None},
    {"machine": "Robô de Solda", "position": "Eixo 1", "name": "Troca de óleo do redutor — Eixo 1",
     "tasks": _tasks("Drenar e trocar o óleo do redutor", "Verificar folga"),
     "estimated_hours": 3.0, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"hours": 5_000, "cycles": 1_500_000}, "demo_trigger": "cycles", "demo_target": 1.5},
    {"machine": "Robô de Solda", "position": "Eixo 2", "name": "Medição de folga do redutor — Eixo 2",
     "tasks": _tasks("Medir a folga do redutor", "Registrar a medida"),
     "estimated_hours": 0.5, "wo_type": "predictive", "stops_machine": True,
     "intervals": {"cycles": 1_000_000}, "demo_trigger": "cycles", "demo_target": 1.0},
    {"machine": "Transportador de Saída", "position": "Motor do transportador",
     "name": "Lubrificação dos rolamentos — Motor do transportador",
     "tasks": _tasks("Relubrificar os rolamentos", "Medir vibração e corrente"),
     "estimated_hours": 1.0, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"hours": 2_000, "starts": 30_000}, "demo_trigger": "hours", "demo_target": 1.0},
    {"machine": "Centro de Usinagem", "position": "Motor da bomba de refrigeração",
     "name": "Inspeção do selo e rolamentos — Bomba de refrigeração",
     "tasks": _tasks("Inspecionar selo mecânico", "Medir vibração do mancal"),
     "estimated_hours": 1.0, "wo_type": "preventive", "stops_machine": True,
     # demo por horas: a 60× o intervalo entre cortes (< 1 s real) cai no debounce de run_state do servidor e as
     # partidas aceleradas da bomba são subcontadas
     "intervals": {"hours": 4_000, "starts": 100_000}, "demo_trigger": "hours", "demo_target": 1.5},
    {"machine": "Centro de Usinagem", "position": "Garra do trocador de ferramentas",
     "name": "Revisão da garra do trocador",
     "tasks": _tasks("Verificar força de fechamento", "Trocar vedações da garra se necessário"),
     "estimated_hours": 2.0, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"cycles": 1_500_000}, "demo_trigger": "cycles", "demo_target": 0.4},
    {"machine": "Torno CNC", "position": "Eixo-árvore", "name": "Lubrificação do eixo-árvore — Torno",
     "tasks": _tasks("Lubrificar os mancais do eixo-árvore", "Medir batimento"),
     "estimated_hours": 2.0, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"hours": 2_000}, "demo_trigger": "hours", "demo_target": 0.3},
    # planos de máquina (calendário): vencimento em dias corridos do relógio real
    {"machine": "Estação de Fixação", "position": None, "name": "Inspeção NR-12 da célula",
     "tasks": _tasks("Testar cortina de luz e botões de emergência", "Verificar proteções fixas"),
     "estimated_hours": 2.0, "wo_type": "preventive", "stops_machine": True,
     "intervals": {"days": 30}, "last_done_days_ago": 26},
    {"machine": "Torno CNC", "position": None, "name": "Troca do filtro de ar do painel — Torno",
     "tasks": _tasks("Trocar o filtro de ar do painel elétrico"),
     "estimated_hours": 0.5, "wo_type": "preventive", "stops_machine": False,
     "intervals": {"days": 90}, "last_done_days_ago": 92},
]

_FIELD = {"cycles": "interval_cycles", "hours": "interval_hours", "starts": "interval_starts", "days": "interval_days"}
CONFIG_FIELDS = ("task_list", "estimated_hours", "wo_type", "stops_machine",
                 "interval_cycles", "interval_hours", "interval_starts", "interval_days")


def window_hours(measure: str, speed: float, demo_minutes: float) -> float:
    """Horas de planta que a janela da demo "vale" para a medida. Ciclos e partidas vêm de contadores e bordas e
    aceleram com --speed; horas de operação o servidor integra pelo timestamp das leituras (relógio real), então
    NÃO aceleram: 1 h de demo = 1 h de horímetro × fração em operação."""
    return demo_minutes / 60.0 * (1.0 if measure == "hours" else speed)


def plan_intervals(plan: dict, profile: str, speed: float, demo_minutes: float) -> dict[str, Optional[float]]:
    """Intervalos do plano (`interval_*`) para o perfil. Demo: o gatilho escalado vira uso_na_janela / alvo."""
    out: dict[str, Optional[float]] = {f: None for f in _FIELD.values()}
    for k, v in plan["intervals"].items():
        out[_FIELD[k]] = v
    trig = plan.get("demo_trigger")
    if profile == "demo" and trig:
        rate = RATES[(plan["machine"], plan["position"])][trig]
        usage = rate * window_hours(trig, speed, demo_minutes) / plan["demo_target"]
        out[_FIELD[trig]] = max(0.01, round(usage, 2)) if trig == "hours" else float(max(1, round(usage)))
    return out


def plan_body(plan: dict, profile: str, speed: float, demo_minutes: float, anchor: dict,
              now: Optional[datetime] = None) -> dict:
    body: dict[str, Any] = {
        **anchor,
        "name": plan["name"] + PLAN_SUFFIX,
        "task_list": [dict(t) for t in plan["tasks"]],
        "estimated_hours": plan["estimated_hours"],
        "wo_type": plan["wo_type"],
        "stops_machine": plan["stops_machine"],
        **plan_intervals(plan, profile, speed, demo_minutes),
    }
    if plan.get("last_done_days_ago"):
        now = now or datetime.now(timezone.utc)
        body["last_done_at"] = (now - timedelta(days=plan["last_done_days_ago"])).isoformat()
    return body


def _norm_tasks(tasks: Any) -> list[tuple]:
    return [(t.get("id"), t.get("text")) for t in tasks or [] if isinstance(t, dict)]


def _num(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def config_diff(current: dict, desired: dict) -> dict:
    diff = {}
    for k in CONFIG_FIELDS:
        if k not in desired:
            continue
        cur, want = current.get(k), desired[k]
        if k == "task_list":
            if _norm_tasks(cur) != _norm_tasks(want):
                diff[k] = want
        elif k.startswith("interval_"):
            if _num(cur) != _num(want):
                diff[k] = want
        elif cur != want:
            diff[k] = want
    return diff


class MaintenanceProvisioner:
    def __init__(self, api: Api, profile: str = "demo", speed: float = 60.0, demo_minutes: float = 60.0,
                 log=print, plant: str = PLANT, plans: Optional[list[dict]] = None) -> None:
        if profile not in ("demo", "realistic"):
            raise ValueError("profile deve ser demo ou realistic")
        if speed <= 0 or demo_minutes <= 0:
            raise ValueError("speed e demo_minutes devem ser > 0")
        self.api, self.profile, self.speed, self.demo_minutes = api, profile, speed, demo_minutes
        self.log, self.plant = log, plant
        self.plans = PLANS if plans is None else plans
        self.stats = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    async def _find(self, path: str, name: str, params: Optional[dict] = None, parent: Optional[tuple] = None):
        items = await self.api.list(path, params=params)
        for it in items:
            if it.get("name") != name:
                continue
            if parent and parent[0] in it and it[parent[0]] != parent[1]:
                continue
            return it
        return None

    async def resolve(self) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
        plant = await self._find("/api/plants/", self.plant)
        if plant is None:
            raise ProvisionError(f"planta {self.plant!r} não existe no servidor: rode python -m edge.src.provision")
        machines: dict[str, str] = {}
        positions: dict[tuple[str, str], str] = {}
        for line in [x for x in await self.api.list("/api/lines/", {"plant_id": plant["id"]})
                     if x.get("plant_id") in (plant["id"], None)]:
            for m in [x for x in await self.api.list("/api/machines/", {"line_id": line["id"]})
                      if x.get("line_id") in (line["id"], None)]:
                machines[m["name"]] = m["id"]
                for p in [x for x in await self.api.list("/api/positions/", {"machine_id": m["id"]})
                          if x.get("machine_id") in (m["id"], None)]:
                    positions[(m["name"], p["name"])] = p["id"]
        return machines, positions

    async def run(self) -> dict[str, str]:
        machines, positions = await self.resolve()
        ids: dict[str, str] = {}
        for plan in self.plans:
            if plan["position"]:
                pid = positions.get((plan["machine"], plan["position"]))
                anchor, params = {"position_id": pid}, {"position_id": pid}
                missing = pid is None
            else:
                mid = machines.get(plan["machine"])
                anchor, params = {"machine_id": mid}, {"machine_id": mid}
                missing = mid is None
            label = f"plano {plan['name']}"
            if missing:
                self.stats["skipped"] += 1
                self.log(f"  ! {label}: {plan['machine']} / {plan['position']} não existe no servidor")
                continue
            body = plan_body(plan, self.profile, self.speed, self.demo_minutes, anchor)
            existing = [x for x in await self.api.list("/api/maintenance-plans/", params=params)
                        if all(x.get(k) in (v, None) for k, v in anchor.items())
                        and (plan["position"] or not x.get("position_id"))]
            found = next((x for x in existing if x.get("name") == body["name"]), None)
            if found is None:
                created = await self.api.create("/api/maintenance-plans/", body)
                self.stats["created"] += 1
                self.log(f"  + {label} ({_triggers(body)})")
                ids[body["name"]] = created["id"]
                continue
            ids[body["name"]] = found["id"]
            diff = config_diff(found, body)
            if not diff:
                self.stats["unchanged"] += 1
                continue
            status, data = await self.api.request("PATCH", f"/api/maintenance-plans/{found['id']}", json_body=diff)
            if status not in (200, 201):
                raise ProvisionError(f"PATCH plano {body['name']} → {status}: {str(data)[:300]}")
            self.stats["updated"] += 1
            self.log(f"  ~ {label}: {sorted(diff)}")
        return ids


def _triggers(body: dict) -> str:
    return ", ".join(f"{k.removeprefix('interval_')}={v:g}" for k, v in body.items()
                     if k.startswith("interval_") and v is not None)


async def provision_plans(server_url: str, email: str, password: str, profile: str, speed: float,
                          demo_minutes: float, log=print) -> tuple[dict, dict]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        api = Api(server_url, session)
        await api.login(email, password)
        p = MaintenanceProvisioner(api, profile, speed, demo_minutes, log=log)
        ids = await p.run()
        return ids, p.stats


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    ap = argparse.ArgumentParser(description="Planos de manutenção da Planta Manaus (planta virtual)")
    ap.add_argument("--server", default=env("PROVISION_SERVER_URL", DEFAULT_SERVER_URL))
    ap.add_argument("--email", default=env("PROVISION_EMAIL", "admin@factory-monitor.com"))
    ap.add_argument("--password", default=env("PROVISION_PASSWORD", "Admin@1234"),
                    help="senha (padrão: usuário admin do seed de desenvolvimento)")
    ap.add_argument("--speed", type=float, default=float(env("PROVISION_SPEED", "60")),
                    help="aceleração da planta virtual (padrão 60)")
    ap.add_argument("--demo-minutes", type=float, default=float(env("PROVISION_DEMO_MINUTES", "60")),
                    help="duração real da demonstração (padrão 60 min)")
    ap.add_argument("--profile", choices=("demo", "realistic"), default=None,
                    help="demo = intervalos escalados para a janela; realistic = intervalos de fábrica "
                         "(padrão: demo se --speed > 1)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    profile = a.profile or ("demo" if a.speed > 1 else "realistic")
    print(f"Planos de manutenção ({profile}, speed {a.speed:g}×, janela {a.demo_minutes:g} min) em {a.server} ...")
    try:
        _, stats = asyncio.run(provision_plans(a.server, a.email, a.password, profile, a.speed, a.demo_minutes))
    except (ProvisionError, aiohttp.ClientError, ValueError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    print(f"OK: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
