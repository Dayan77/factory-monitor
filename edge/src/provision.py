"""
Provisionamento: cria/atualiza no servidor (REST) os ativos e tags descritos no mapa do gateway e grava
os ids de volta no mapa — `planta virtual + provision + gateway` é o caminho real completo.

    python -m edge.src.provision                                   # localhost:8000, usuário admin do seed
    python -m edge.src.provision --server http://localhost:8000 \\
        --email admin@factory-monitor.com --password '...' \\
        --map edge/config/plant.json --out edge/config/plant.local.json

Variáveis equivalentes: PROVISION_SERVER_URL, PROVISION_EMAIL, PROVISION_PASSWORD, PROVISION_MAP, PROVISION_OUT.
Padrões apontam para o stack LOCAL (nunca produção).

Idempotente: cada entidade é procurada pelo nome dentro do pai (componente: pelo número de série) antes de
criar; rodar de novo não duplica nada e devolve os mesmos ids. Campos divergentes são atualizados com PATCH
(falha de PATCH vira aviso, não erro). Contrato REST usado:

    POST /api/plants/      {name, timezone, address}
    POST /api/lines/       {plant_id, name}
    POST /api/machines/    {line_id, name, model, serial_number, ideal_cycle_time_s}
    POST /api/positions/   {machine_id, name, component_type, cycles_per_part}
    POST /api/components/  {component_type, manufacturer, model, serial_number, nominal_life:{cycles,hours,starts}}
    POST /api/positions/{id}/install  {component_id}
    POST /api/tags/        {machine_id, position_id, name, protocol, address, data_type, unit, semantic_role,
                            counter_max, max_rate_per_s, scale_factor, poll_interval_ms}
    GET  das mesmas coleções (com ?plant_id= / ?line_id= / ?machine_id= como filtro; o filtro é refeito no
    cliente caso o servidor ignore o parâmetro). Listas podem vir como [..] ou {"items": [..]}.
    Opcional (aviso se não suportado): PATCH /api/lines/{id} {output_machine_id},
    PATCH /api/positions/{id} {counter_tag_id}.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import aiohttp

EDGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MAP = EDGE_DIR / "config" / "plant.json"
DEFAULT_OUT = EDGE_DIR / "config" / "plant.local.json"
DEFAULT_SERVER_URL = "http://localhost:8000"

PROTOCOL = {"opcua": "opc_ua", "modbus": "modbus_tcp"}


class ProvisionError(RuntimeError):
    pass


def _items(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("items", "results", "data"):
            if isinstance(data.get(k), list):
                return data[k]
    raise ProvisionError(f"resposta de lista inesperada: {str(data)[:200]}")


def _jwt_claim(token: str, claim: str) -> Optional[str]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get(claim)
    except Exception:  # noqa: BLE001
        return None


class Api:
    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        self.base = base_url.rstrip("/")
        self.session = session
        self.token: Optional[str] = None
        self.tenant_id: Optional[str] = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def login(self, email: str, password: str) -> None:
        async with self.session.post(f"{self.base}/api/auth/login",
                                     json={"email": email, "password": password}) as r:
            if r.status != 200:
                raise ProvisionError(f"login falhou ({r.status}): {(await r.text())[:200]}")
            data = await r.json()
        self.token = data["access_token"]
        self.tenant_id = _jwt_claim(self.token, "tenant_id")

    async def request(self, method: str, path: str, *, json_body: Any = None,
                      params: Optional[dict] = None) -> tuple[int, Any]:
        async with self.session.request(method, f"{self.base}{path}", json=json_body, params=params,
                                        headers=self._headers()) as r:
            text = await r.text()
            try:
                body = json.loads(text) if text else None
            except ValueError:
                body = text
            return r.status, body

    async def list(self, path: str, params: Optional[dict] = None) -> list[dict]:
        status, body = await self.request("GET", path, params=params)
        if status != 200:
            raise ProvisionError(f"GET {path} → {status}: {str(body)[:300]}")
        return _items(body)

    async def create(self, path: str, body: dict) -> dict:
        status, data = await self.request("POST", path, json_body=body)
        if status not in (200, 201):
            raise ProvisionError(f"POST {path} → {status}: {str(data)[:300]}")
        return data


class Provisioner:
    def __init__(self, api: Api, log=print, speed: float = 1.0) -> None:
        if speed <= 0:
            raise ValueError("speed deve ser > 0")
        self.speed = speed      # aceleração da planta virtual: escala max_rate_per_s dos contadores
        self.api = api
        self.log = log
        self.stats = {"created": 0, "updated": 0, "unchanged": 0, "warnings": 0}

    def _warn(self, msg: str) -> None:
        self.stats["warnings"] += 1
        self.log(f"  ! {msg}")

    async def _patch_diff(self, path: str, current: dict, desired: dict, label: str) -> dict:
        """PATCH só os campos que o servidor devolve e que divergem do desejado."""
        diff = {k: v for k, v in desired.items() if k in current and current.get(k) != v}
        if not diff:
            self.stats["unchanged"] += 1
            return current
        status, data = await self.api.request("PATCH", path, json_body=diff)
        if status in (200, 201) and isinstance(data, dict):
            ignored = sorted(k for k, v in diff.items() if k in data and data.get(k) != v)
            if ignored:
                self._warn(f"{label}: servidor ignorou {ignored} no PATCH")
            else:
                self.stats["updated"] += 1
                self.log(f"  ~ {label}: atualizado {sorted(diff)}")
            return data
        self._warn(f"{label}: PATCH {sorted(diff)} não aplicado ({status})")
        return current

    async def ensure(self, collection: str, match: dict, body: dict, label: str,
                     parent_filter: Optional[dict] = None, updatable: tuple[str, ...] = ()) -> dict:
        """Procura por `match` na coleção; cria se não houver.

        `name` precisa casar sempre; chaves do pai (plant_id, line_id...) só são comparadas quando o
        servidor as devolve (a listagem já veio filtrada pelo pai via `parent_filter`).
        """
        path = f"/api/{collection}/"
        existing = await self.api.list(path, params=parent_filter)
        for item in existing:
            if all(item.get(k) == v for k, v in match.items() if k == "name" or k in item):
                if updatable:
                    item = await self._patch_diff(f"/api/{collection}/{item['id']}", item,
                                                  {k: body[k] for k in updatable if k in body}, label)
                else:
                    self.stats["unchanged"] += 1
                return item
        created = await self.api.create(path, body)
        self.stats["created"] += 1
        self.log(f"  + {label}")
        return created

    async def run(self, doc: dict) -> dict:
        out = copy.deepcopy(doc)
        assets = doc["assets"]
        ids: dict[str, Any] = {"lines": {}, "machines": {}, "positions": {}, "components": {}}

        p = assets["plant"]
        plant = await self.ensure("plants", {"name": p["name"]},
                                  {"name": p["name"], "timezone": p.get("timezone"), "address": p.get("address")},
                                  f"planta {p['name']}", updatable=("timezone", "address"))
        ids["plant"] = plant["id"]

        machine_ids: dict[tuple[str, str], str] = {}
        position_ids: dict[tuple[str, str, str], str] = {}
        positions: dict[str, dict] = {}
        for line in assets["lines"]:
            ln = await self.ensure("lines", {"name": line["name"], "plant_id": plant["id"]},
                                   {"plant_id": plant["id"], "name": line["name"]},
                                   f"linha {line['name']}", parent_filter={"plant_id": plant["id"]})
            ids["lines"][line["name"]] = ln["id"]
            for m in line["machines"]:
                mach = await self.ensure(
                    "machines", {"name": m["name"], "line_id": ln["id"]},
                    {"line_id": ln["id"], "name": m["name"], "model": m.get("model"),
                     "serial_number": m.get("serial_number"), "ideal_cycle_time_s": m.get("ideal_cycle_time_s")},
                    f"máquina {m['name']}", parent_filter={"line_id": ln["id"]},
                    updatable=("model", "serial_number", "ideal_cycle_time_s"),
                )
                machine_ids[(line["name"], m["name"])] = mach["id"]
                ids["machines"][f"{line['name']}/{m['name']}"] = mach["id"]
                for pos in m.get("positions", []):
                    pz = await self.ensure(
                        "positions", {"name": pos["name"], "machine_id": mach["id"]},
                        {"machine_id": mach["id"], "name": pos["name"], "component_type": pos["component_type"],
                         "cycles_per_part": pos.get("cycles_per_part", 1)},
                        f"posição {m['name']} / {pos['name']}", parent_filter={"machine_id": mach["id"]},
                        updatable=("component_type", "cycles_per_part"),
                    )
                    position_ids[(line["name"], m["name"], pos["name"])] = pz["id"]
                    positions[pz["id"]] = pz
                    ids["positions"][f"{line['name']}/{m['name']}/{pos['name']}"] = pz["id"]
                    if pos.get("component"):
                        await self._ensure_component(pz, pos["component"], f"{m['name']} / {pos['name']}", ids)

            if line.get("output_machine"):
                out_id = machine_ids.get((line["name"], line["output_machine"]))
                if out_id and ln.get("output_machine_id") != out_id:
                    status, _ = await self.api.request("PATCH", f"/api/lines/{ln['id']}",
                                                       json_body={"output_machine_id": out_id})
                    if status in (200, 201):
                        self.stats["updated"] += 1
                    else:
                        self._warn(f"linha {line['name']}: output_machine_id não aplicado ({status})")

        # Tags
        tags_by_machine: dict[str, list[dict]] = {}
        key_to_tag_id: dict[str, str] = {}
        sources = {s["id"]: s for s in doc.get("sources", [])}
        for i, tag in enumerate(doc.get("tags", [])):
            mid = machine_ids.get((tag["line"], tag["machine"]))
            if mid is None:
                raise ProvisionError(f"tag {tag['key']}: máquina {tag['line']}/{tag['machine']} não está nos assets")
            pid = None
            if tag.get("position"):
                pid = position_ids.get((tag["line"], tag["machine"], tag["position"]))
                if pid is None:
                    raise ProvisionError(f"tag {tag['key']}: posição {tag['position']} não está nos assets")
            if mid not in tags_by_machine:
                tags_by_machine[mid] = await self.api.list("/api/tags/", params={"machine_id": mid})
            src = sources.get(tag["source"], {})
            body = {
                "machine_id": mid, "position_id": pid, "name": tag["name"],
                "protocol": PROTOCOL.get(src.get("protocol"), "mqtt"),
                "address": _address_string(tag),
                "data_type": tag.get("data_type", "float"), "unit": tag.get("unit") or None,
                "semantic_role": tag.get("semantic_role", "analog"),
                "counter_max": tag.get("counter_max"),
                "max_rate_per_s": (round(tag["max_rate_per_s"] * self.speed, 4)
                                   if tag.get("max_rate_per_s") else None),
                "scale_factor": 1.0,   # o gateway já publica valor de engenharia
                "poll_interval_ms": tag.get("poll_interval_ms", 1000),
            }
            found = next((t for t in tags_by_machine[mid]
                          if t.get("name") == tag["name"] and t.get("machine_id") in (mid, None)), None)
            label = f"tag {tag['machine']} / {tag['name']}"
            if found:
                found = await self._patch_diff(
                    f"/api/tags/{found['id']}", found,
                    {k: body[k] for k in ("position_id", "address", "unit", "semantic_role", "counter_max",
                                          "max_rate_per_s", "poll_interval_ms")}, label)
                tag_id = found["id"]
            else:
                created = await self.api.create("/api/tags/", body)
                self.stats["created"] += 1
                self.log(f"  + {label}")
                tags_by_machine[mid].append(created)
                tag_id = created["id"]
            out["tags"][i]["tag_id"] = tag_id
            key_to_tag_id[tag["key"]] = tag_id

        # Posição → tag que conta seus ciclos (opcional no servidor)
        for line in assets["lines"]:
            for m in line["machines"]:
                for pos in m.get("positions", []):
                    key = pos.get("counter_tag")
                    if not key:
                        continue
                    pid = position_ids[(line["name"], m["name"], pos["name"])]
                    if positions[pid].get("counter_tag_id") == key_to_tag_id[key]:
                        continue
                    status, _ = await self.api.request("PATCH", f"/api/positions/{pid}",
                                                       json_body={"counter_tag_id": key_to_tag_id[key]})
                    if status in (200, 201):
                        self.stats["updated"] += 1
                    else:
                        self._warn(f"posição {m['name']} / {pos['name']}: counter_tag_id não aplicado ({status})")

        out["tenant_id"] = self.api.tenant_id or doc.get("tenant_id")
        out["server_url"] = self.api.base
        out["asset_ids"] = ids
        return out

    async def _ensure_component(self, position: dict, comp: dict, label: str, ids: dict) -> None:
        serial = comp["serial_number"]
        existing = await self.api.list("/api/components/", params={"serial_number": serial})
        found = next((c for c in existing if c.get("serial_number") == serial), None)
        if found is None:
            found = await self.api.create("/api/components/", {
                "component_type": comp["component_type"], "manufacturer": comp.get("manufacturer"),
                "model": comp.get("model"), "serial_number": serial,
                "nominal_life": comp.get("nominal_life") or {},
            })
            self.stats["created"] += 1
            self.log(f"  + componente {serial}")
        else:
            self.stats["unchanged"] += 1
        ids["components"][serial] = found["id"]

        installed_here = (
            found.get("position_id") == position["id"]
            or position.get("current_component_id") == found["id"]
            or position.get("installed_component_id") == found["id"]
            or (position.get("installed_component") or {}).get("id") == found["id"]
            or (position.get("current_component") or {}).get("id") == found["id"]
        )
        if installed_here:
            return
        if found.get("position_id") and not found.get("removed_at"):
            self._warn(f"componente {serial} está instalado em outra posição; não reinstalado")
            return
        status, data = await self.api.request("POST", f"/api/positions/{position['id']}/install",
                                              json_body={"component_id": found["id"]})
        if status in (200, 201, 204):
            self.stats["created"] += 1
            self.log(f"  + instalação {serial} em {label}")
        elif status == 409:
            self._warn(f"{label}: posição já tem outro componente instalado ({str(data)[:120]})")
        else:
            raise ProvisionError(f"install {serial} em {label} → {status}: {str(data)[:300]}")


def _address_string(tag: dict) -> str:
    if isinstance(tag.get("address"), str):
        return tag["address"]
    area = {"discrete": "di", "coil": "co", "input": "ir"}.get(tag.get("register_type"), "hr")
    dt = f":{tag['data_type_plc']}" if tag.get("data_type_plc") and area in ("hr", "ir") else ""
    return f"unit={tag.get('unit_id', 1)};{area}={tag['address']}{dt}"


async def provision(server_url: str, email: str, password: str, doc: dict, log=print,
                    speed: float = 1.0) -> tuple[dict, dict]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        api = Api(server_url, session)
        await api.login(email, password)
        p = Provisioner(api, log=log, speed=speed)
        out = await p.run(doc)
        return out, p.stats


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    ap = argparse.ArgumentParser(description="Provisiona no servidor os ativos/tags do mapa do gateway")
    ap.add_argument("--server", default=env("PROVISION_SERVER_URL", DEFAULT_SERVER_URL))
    ap.add_argument("--email", default=env("PROVISION_EMAIL", "admin@factory-monitor.com"))
    ap.add_argument("--password", default=env("PROVISION_PASSWORD", "Admin@1234"),
                    help="senha (padrão: usuário admin do seed de desenvolvimento)")
    ap.add_argument("--map", default=env("PROVISION_MAP", str(DEFAULT_MAP)))
    ap.add_argument("--out", default=env("PROVISION_OUT", str(DEFAULT_OUT)),
                    help="mapa com tag_id preenchidos (consumido pelo gateway via PLANT_MAP_PATH)")
    ap.add_argument("--speed", type=float, default=float(env("PROVISION_SPEED", "1")),
                    help="aceleração da planta virtual (--speed dela): sem isso o servidor descarta como "
                         "counter_glitch os incrementos acelerados (filtro de plausibilidade §3.3)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.map, encoding="utf-8") as f:
        doc = json.load(f)
    print(f"Provisionando {doc['assets']['plant']['name']} em {args.server} ...")
    try:
        out, stats = asyncio.run(provision(args.server, args.email, args.password, doc, speed=args.speed))
    except (ProvisionError, aiohttp.ClientError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"OK: {stats} → {args.out} (tenant {out.get('tenant_id')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
