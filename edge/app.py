"""
Edge Config App — Interface local para configuração e monitoramento da coleta de dados.
Roda em http://localhost:7000

Permite:
- Configurar conexão com servidor e broker MQTT
- Cadastrar linhas, máquinas e sensores
- Iniciar/parar o simulador
- Monitorar dados sendo enviados em tempo real
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import aiohttp
import aiomqtt
import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = structlog.get_logger()

CONFIG_PATH = Path(__file__).parent / "config.json"
STATIC_PATH = Path(__file__).parent / "static"

DEFAULT_CONFIG = {
    "server_url": "https://factoryconnect.tech",
    "mqtt_host": "factoryconnect.tech",
    "mqtt_port": 1883,
    "email": "admin@factory-monitor.com",
    "password": "Admin@1234",
    "simulator_interval": 2.0,
}

# ── Global state ───────────────────────────────────────────────────────────────

_config: dict = {}
_token: Optional[str] = None
_tenant_id: Optional[str] = None
_simulator_task: Optional[asyncio.Task] = None
_live_subscribers: list[WebSocket] = []
_live_readings: list[dict] = []   # ring buffer — last 50 readings

# ── Config helpers ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    global _config
    if CONFIG_PATH.exists():
        _config = json.loads(CONFIG_PATH.read_text())
    else:
        _config = DEFAULT_CONFIG.copy()
        save_config(_config)
    return _config

def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    global _config
    _config = cfg

# ── API helpers ────────────────────────────────────────────────────────────────

def _api_session() -> aiohttp.ClientSession:
    headers = {"Authorization": f"Bearer {_token}"} if _token else {}
    connector = aiohttp.TCPConnector(ssl=False)
    return aiohttp.ClientSession(headers=headers, connector=connector)

async def _api_login(email: str, password: str, server_url: str) -> tuple[str, str]:
    """Returns (token, tenant_id)."""
    import base64
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        async with s.post(
            f"{server_url}/api/auth/login",
            json={"email": email, "password": password},
        ) as r:
            r.raise_for_status()
            data = await r.json()
            token = data["access_token"]
            b64 = token.split(".")[1]
            b64 += "=" * (4 - len(b64) % 4)
            tenant_id = json.loads(base64.b64decode(b64))["tenant_id"]
            return token, tenant_id

# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="Factory Monitor — Edge Config", docs_url=None, redoc_url=None)

STATIC_PATH.mkdir(exist_ok=True)

@app.on_event("startup")
async def startup():
    load_config()
    log.info("edge_app.started", port=7001)

# ── Config endpoints ───────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    safe = {k: v for k, v in cfg.items() if k != "password"}
    safe["password"] = "***" if cfg.get("password") else ""
    return safe

class ConfigUpdate(BaseModel):
    server_url: str
    mqtt_host: str
    mqtt_port: int
    email: str
    password: Optional[str] = None
    simulator_interval: float = 2.0

@app.post("/api/config")
async def update_config(body: ConfigUpdate):
    cfg = load_config()
    cfg["server_url"] = body.server_url
    cfg["mqtt_host"] = body.mqtt_host
    cfg["mqtt_port"] = body.mqtt_port
    cfg["email"] = body.email
    cfg["simulator_interval"] = body.simulator_interval
    if body.password and body.password != "***":
        cfg["password"] = body.password
    save_config(cfg)
    return {"ok": True}

# ── Connection test ────────────────────────────────────────────────────────────

@app.post("/api/connect")
async def connect():
    global _token, _tenant_id
    cfg = load_config()
    result = {"server": False, "mqtt": False, "tenant_id": None, "error": None}
    try:
        _token, _tenant_id = await _api_login(cfg["email"], cfg["password"], cfg["server_url"])
        result["server"] = True
        result["tenant_id"] = _tenant_id
    except Exception as e:
        result["error"] = f"Servidor: {e}"
        return result
    try:
        async with aiomqtt.Client(hostname=cfg["mqtt_host"], port=cfg["mqtt_port"]):
            result["mqtt"] = True
    except Exception as e:
        result["error"] = f"MQTT: {e}"
    return result

@app.get("/api/status")
async def status():
    return {
        "connected": _token is not None,
        "tenant_id": _tenant_id,
        "simulator_running": _simulator_task is not None and not _simulator_task.done(),
    }

# ── Proxy endpoints (call remote server API) ──────────────────────────────────

async def _proxy_get(path: str):
    cfg = load_config()
    async with _api_session() as s:
        async with s.get(f"{cfg['server_url']}{path}") as r:
            r.raise_for_status()
            return await r.json()

async def _proxy_post(path: str, body: dict):
    cfg = load_config()
    async with _api_session() as s:
        async with s.post(f"{cfg['server_url']}{path}", json=body) as r:
            r.raise_for_status()
            return await r.json()

async def _proxy_delete(path: str):
    cfg = load_config()
    async with _api_session() as s:
        async with s.delete(f"{cfg['server_url']}{path}") as r:
            r.raise_for_status()
            return {"ok": True}

@app.get("/api/lines")
async def list_lines():
    return await _proxy_get("/api/lines/")

@app.post("/api/lines")
async def create_line(body: dict):
    return await _proxy_post("/api/lines/", body)

@app.delete("/api/lines/{line_id}")
async def delete_line(line_id: str):
    return await _proxy_delete(f"/api/lines/{line_id}")

@app.get("/api/machines")
async def list_machines(line_id: Optional[str] = None):
    path = f"/api/machines/?line_id={line_id}" if line_id else "/api/machines/"
    return await _proxy_get(path)

@app.post("/api/machines")
async def create_machine(body: dict):
    return await _proxy_post("/api/machines/", body)

@app.delete("/api/machines/{machine_id}")
async def delete_machine(machine_id: str):
    return await _proxy_delete(f"/api/machines/{machine_id}")

@app.get("/api/tags")
async def list_tags(machine_id: Optional[str] = None):
    path = f"/api/tags/?machine_id={machine_id}" if machine_id else "/api/tags/"
    return await _proxy_get(path)

@app.post("/api/tags")
async def create_tag(body: dict):
    return await _proxy_post("/api/tags/", body)

@app.delete("/api/tags/{tag_id}")
async def delete_tag(tag_id: str):
    return await _proxy_delete(f"/api/tags/{tag_id}")

# ── Simulator control ──────────────────────────────────────────────────────────

@app.post("/api/simulator/start")
async def start_simulator():
    global _simulator_task
    if _simulator_task and not _simulator_task.done():
        return {"ok": True, "message": "já em execução"}
    if not _token:
        return {"ok": False, "message": "Conecte ao servidor primeiro"}
    _simulator_task = asyncio.create_task(_run_simulator_loop())
    return {"ok": True, "message": "Simulador iniciado"}

@app.post("/api/simulator/stop")
async def stop_simulator():
    global _simulator_task
    if _simulator_task and not _simulator_task.done():
        _simulator_task.cancel()
        _simulator_task = None
    return {"ok": True}

async def _run_simulator_loop():
    """Runs the simulator and broadcasts readings to WebSocket subscribers."""
    import math, random, time
    from datetime import datetime, timezone

    cfg = load_config()

    # Lazy import to avoid circular deps
    from edge.src.simulator import DEMO_MACHINES, SENSOR_PROFILES, SensorState, ApiClient

    async with ApiClient(cfg["server_url"]) as api:
        await api.login(cfg["email"], cfg["password"])
        line_id = await api.get_or_create_line("Linha Demo (Simulador)")
        tag_states: dict[str, dict] = {}
        for machine_cfg in DEMO_MACHINES:
            machine_id = await api.get_or_create_machine(line_id, machine_cfg["name"], machine_cfg["model"])
            for sensor_type in machine_cfg["sensors"]:
                tag_id = await api.get_or_create_tag(machine_id, sensor_type)
                tag_states[tag_id] = {
                    "state": SensorState(profile=SENSOR_PROFILES[sensor_type]),
                    "machine": machine_cfg["name"],
                    "sensor": SENSOR_PROFILES[sensor_type].name,
                    "unit": SENSOR_PROFILES[sensor_type].unit,
                }

    interval = cfg.get("simulator_interval", 2.0)
    while True:
        try:
            async with aiomqtt.Client(hostname=cfg["mqtt_host"], port=cfg["mqtt_port"]) as client:
                last = time.monotonic()
                while True:
                    now = time.monotonic()
                    dt = now - last
                    last = now

                    for tag_id, info in tag_states.items():
                        value = info["state"].tick(dt)
                        topic = f"factory/{_tenant_id}/sensors/{tag_id}"
                        payload = json.dumps({
                            "value": round(value, 3),
                            "unit": info["unit"],
                            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        })
                        await client.publish(topic, payload.encode())

                        reading = {
                            "tag_id": tag_id,
                            "machine": info["machine"],
                            "sensor": info["sensor"],
                            "value": round(value, 3),
                            "unit": info["unit"],
                            "ts": datetime.now().strftime("%H:%M:%S"),
                        }
                        _live_readings.append(reading)
                        if len(_live_readings) > 200:
                            _live_readings.pop(0)
                        await _broadcast_live(reading)

                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("simulator.error", error=str(e))
            await asyncio.sleep(5)

async def _broadcast_live(reading: dict):
    dead = []
    for ws in _live_subscribers:
        try:
            await ws.send_json(reading)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _live_subscribers.remove(ws)

@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    _live_subscribers.append(ws)
    # Send buffered readings on connect
    for r in _live_readings[-50:]:
        await ws.send_json(r)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _live_subscribers.remove(ws)

# ── Serve UI ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html = (STATIC_PATH / "index.html").read_text()
    return HTMLResponse(html)

if __name__ == "__main__":
    import uvicorn
    print("\n  Factory Monitor — Edge Config App")
    print("  Abra no browser: http://localhost:7001\n")
    uvicorn.run("edge.app:app", host="0.0.0.0", port=7001, reload=False)
