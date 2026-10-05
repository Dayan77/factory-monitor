"""
API de controle da manutenção da planta virtual (HTTP, só localhost; padrão 127.0.0.1:7070).

Simula o que a equipe de manutenção faz no chão de fábrica, para o QA/demonstração fechar o ciclo
planta → gateway → servidor → OS → ação física → planta:

    GET  /status                       verdade de campo: uso, vida até a falha e restante por componente,
                                       falha ativa por máquina, tempo simulado
    POST /replace        {"position": "montagem.fixacao.a2", "reset_counter": false, "reason": "preventive_replacement"}
                                       troca o componente: desgaste zerado, nova vida sorteada; com
                                       reset_counter=true o contador do CLP da posição também vai a 0 (como numa
                                       troca real em que o contador é zerado — o servidor trata o reset, §3.3).
                                       Se o componente estava em falha, a troca a encerra (a máquina volta).
    POST /repair         {"machine": "montagem.robo"}
                                       libera a máquina (encerra a falha ativa). Falha por desgaste sem troca =
                                       reparo paliativo (o componente gasto volta a falhar após uma margem).
    POST /force-failure  {"machine": "montagem.robo", "code": 331, "duration_s": null}
                         {"position": "montagem.robo.axis1"}
                                       falha agora. Por máquina: código da tabela da máquina (padrão: o menor), até
                                       /repair (ou `duration_s` simulados). Por posição: falha por desgaste do
                                       componente (código dele), encerrada por /replace (ou /repair, paliativo).

`position` aceita o id do item ("montagem.fixacao.a2", ver GET /status) ou "Máquina / Posição"
("Estação de Fixação / Cilindro A2"); `machine` aceita a chave ("montagem.robo") ou o nome ("Robô de Solda").
Erros: 400 corpo inválido, 404 máquina/posição desconhecida, 409 ação impossível no estado atual (ex.: playback de
cenário, falha já ativa).

Segurança: escuta em 127.0.0.1 por padrão e recusa clientes que não sejam loopback. Para expor (ex.: compose em
rede de testes) é preciso `VP_CONTROL_TOKEN`; aí clientes remotos mandam `X-Control-Token`. Nunca em produção.

Cliente de linha de comando (stdlib):
    python -m edge.virtual_plant.control status
    python -m edge.virtual_plant.control replace montagem.fixacao.a2 [--reset-counter]
    python -m edge.virtual_plant.control repair montagem.robo
    python -m edge.virtual_plant.control force-failure --machine montagem.robo --code 331
    python -m edge.virtual_plant.control force-failure --position montagem.robo.axis1
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Awaitable, Callable, Optional

from .layout import all_fault_codes, fault_category

REMOVAL_REASONS = ("failure", "preventive_replacement", "upgrade", "other")


class ControlError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.body = {"error": message, **extra}


class PlantControl:
    """Ações de manutenção sobre um PlantModel (sem I/O; o runtime chama `after_change` para publicar)."""

    def __init__(self, model) -> None:
        self.model = model

    @property
    def wear(self):
        w = getattr(self.model, "wear", None)
        if w is None:
            raise ControlError(409, "modelo sem desgaste (playback de cenário): API de manutenção indisponível")
        return w

    def _machine(self, ref: Any) -> str:
        if not isinstance(ref, str) or not ref.strip():
            raise ControlError(400, "informe 'machine' (chave ou nome da máquina)")
        try:
            return self.model.machine_key(ref)
        except KeyError:
            raise ControlError(404, f"máquina desconhecida: {ref!r}",
                               machines=sorted(self.model._machine_names)) from None

    def _item(self, ref: Any):
        if not isinstance(ref, str) or not ref.strip():
            raise ControlError(400, "informe 'position' (id do item de desgaste ou 'Máquina / Posição')")
        try:
            return self.wear.find(ref)
        except KeyError:
            raise ControlError(404, f"posição sem modelo de desgaste: {ref!r}",
                               positions=sorted(self.wear.items)) from None

    def machines_status(self) -> dict:
        out = {}
        for key in sorted(self.model._machine_names):
            fp = self.model.fault_process(key)
            ev = fp.active_event
            out[key] = {"name": self.model.machine_name(key), "fault_code": fp.code,
                        "fault_source": fp.source if fp.active else None,
                        "fault_since_s": ev["start_s"] if ev else None,
                        "fault_item": ev.get("item") if ev else None}
        return out

    def status(self) -> dict:
        w = self.wear
        return {"sim_time_s": self.model.sim_time_s, "seed": self.model.seed, "repair_mode": w.repair_mode,
                "wear_failures": w.failures_enabled, "machines": self.machines_status(), "components": w.status()}

    def replace(self, body: dict) -> dict:
        it = self._item(body.get("position"))
        reset = body.get("reset_counter", False)
        if not isinstance(reset, bool):
            raise ControlError(400, "'reset_counter' deve ser booleano")
        reason = body.get("reason", "preventive_replacement")
        if reason not in REMOVAL_REASONS:
            raise ControlError(400, f"'reason' deve ser um de {REMOVAL_REASONS}")
        return self.wear.replace(it.id, reset_counter=reset, reason=reason)

    def repair(self, body: dict) -> dict:
        machine = self._machine(body.get("machine"))
        out = self.wear.repair(machine)
        if not out["cleared_code"] and out["wear_item"] is None:
            out["action"] = "nothing_to_repair"
        else:
            out["action"] = "repaired"
        return out

    def force_failure(self, body: dict) -> dict:
        duration = body.get("duration_s")
        if duration is not None and (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                                     or duration <= 0):
            raise ControlError(400, "'duration_s' deve ser número > 0 (ou null = até /repair)")
        if body.get("position") is not None:
            if body.get("machine") is not None:
                raise ControlError(400, "informe 'machine' ou 'position', não os dois")
            it = self._item(body.get("position"))
            if it.failed:
                raise ControlError(409, f"{it.id} já está em falha")
            fp = self.model.fault_process(it.machine)
            if fp.active and fp.source == "wear":
                raise ControlError(409, f"{it.machine} já está em falha por desgaste ({fp.code})")
            return {"forced": "wear", "component": self.wear.force(it.id),
                    "machine": self.machines_status()[it.machine]}
        machine = self._machine(body.get("machine"))
        codes = all_fault_codes(machine)
        code = body.get("code", min(codes))
        if isinstance(code, bool) or not isinstance(code, int) or code not in codes:
            raise ControlError(400, f"'code' inválido para {machine}", codes={str(c): d for c, d in sorted(codes.items())})
        fp = self.model.fault_process(machine)
        if fp.active and fp.source == "wear":
            raise ControlError(409, f"{machine} já está em falha por desgaste ({fp.code}): use /replace ou /repair")
        ev = self.model.inject_fault(machine, code, duration_s=duration, source="forced")
        return {"forced": "machine", "event": dict(ev), "category": fault_category(code),
                "machine": self.machines_status()[machine]}


def _is_loopback(remote: Optional[str]) -> bool:
    try:
        return ipaddress.ip_address((remote or "").split("%")[0]).is_loopback
    except ValueError:
        return False


def build_app(control: PlantControl, token: str = "", after_change: Optional[Callable[[], Awaitable[None]]] = None):
    """aiohttp app da API de controle. `after_change`: publica os valores novos nos servidores OPC UA/Modbus."""
    from aiohttp import web

    @web.middleware
    async def guard(request, handler):
        if not _is_loopback(request.remote):
            given = request.headers.get("X-Control-Token", "")
            if not token or not hmac.compare_digest(given, token):
                return web.json_response({"error": "API de controle só aceita localhost (ou X-Control-Token)"},
                                         status=403)
        try:
            return await handler(request)
        except ControlError as e:
            return web.json_response(e.body, status=e.status)

    def action(fn: Callable[[dict], dict]):
        async def handler(request):
            try:
                body = await request.json() if request.can_read_body else {}
            except ValueError:
                raise ControlError(400, "corpo JSON inválido") from None
            if not isinstance(body, dict):
                raise ControlError(400, "corpo deve ser um objeto JSON")
            out = fn(body)
            if after_change is not None:
                await after_change()
            out["sim_time_s"] = control.model.sim_time_s
            return web.json_response(out)
        return handler

    async def status(_request):
        return web.json_response(control.status())

    app = web.Application(middlewares=[guard])
    app.router.add_get("/status", status)
    app.router.add_post("/replace", action(control.replace))
    app.router.add_post("/repair", action(control.repair))
    app.router.add_post("/force-failure", action(control.force_failure))
    return app


# ── cliente de linha de comando ──────────────────────────────────────────────

def _call(base: str, method: str, path: str, body: Optional[dict] = None, token: str = "") -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base.rstrip('/')}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **({"X-Control-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:   # noqa: S310 (URL do operador, localhost)
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main(argv: Optional[list[str]] = None) -> int:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="virtual_plant.control", description="API de controle da planta virtual")
    p.add_argument("--url", default=env("VP_CONTROL_URL", f"http://127.0.0.1:{env('VP_CONTROL_PORT', '7070')}"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    r = sub.add_parser("replace")
    r.add_argument("position")
    r.add_argument("--reset-counter", action="store_true")
    r.add_argument("--reason", default="preventive_replacement", choices=REMOVAL_REASONS)
    rp = sub.add_parser("repair")
    rp.add_argument("machine")
    f = sub.add_parser("force-failure")
    g = f.add_mutually_exclusive_group(required=True)
    g.add_argument("--machine")
    g.add_argument("--position")
    f.add_argument("--code", type=int)
    f.add_argument("--duration-s", type=float)
    a = p.parse_args(argv)
    token = env("VP_CONTROL_TOKEN", "")
    if a.cmd == "status":
        st, out = _call(a.url, "GET", "/status", token=token)
    elif a.cmd == "replace":
        st, out = _call(a.url, "POST", "/replace", {"position": a.position, "reset_counter": a.reset_counter,
                                                    "reason": a.reason}, token)
    elif a.cmd == "repair":
        st, out = _call(a.url, "POST", "/repair", {"machine": a.machine}, token)
    else:
        body: dict = {"machine": a.machine} if a.machine else {"position": a.position}
        if a.code is not None:
            body["code"] = a.code
        if a.duration_s is not None:
            body["duration_s"] = a.duration_s
        st, out = _call(a.url, "POST", "/force-failure", body, token)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0 if st == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
