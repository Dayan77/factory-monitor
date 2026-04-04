"""
Factory Monitor — Demo Simulator

Generates realistic factory sensor data and publishes to the MQTT broker.
Automatically creates demo machines and tags via REST API on first run.

Usage:
    python -m edge.src.simulator
    python -m edge.src.simulator --server https://factoryconnect.tech --broker factoryconnect.tech
    python -m edge.src.simulator --email admin@factory-monitor.com --password Admin@1234
"""

import argparse
import asyncio
import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import aiomqtt
import structlog

log = structlog.get_logger()

# ── Sensor profiles ────────────────────────────────────────────────────────────

@dataclass
class SensorProfile:
    name: str
    unit: str
    base: float           # baseline value
    noise: float          # ±noise amplitude
    drift_speed: float    # how fast the value drifts
    min_val: float
    max_val: float
    anomaly_chance: float = 0.005   # probability of spike per tick
    anomaly_magnitude: float = 0.3  # spike as fraction of range


SENSOR_PROFILES: dict[str, SensorProfile] = {
    "temperature":  SensorProfile("Temperatura", "°C",   75.0, 1.5, 0.02,  20.0, 120.0, 0.003, 0.4),
    "pressure":     SensorProfile("Pressão",     "bar",   4.5, 0.2, 0.01,   0.0,  10.0, 0.002, 0.5),
    "rpm":          SensorProfile("Rotação",     "RPM", 950.0,15.0, 0.05, 100.0,1500.0, 0.002, 0.3),
    "vibration":    SensorProfile("Vibração",    "mm/s",  2.1, 0.3, 0.03,   0.0,  20.0, 0.004, 0.6),
    "current":      SensorProfile("Corrente",    "A",    12.5, 0.5, 0.02,   0.0,  30.0, 0.002, 0.4),
    "production":   SensorProfile("Produção",    "pcs",   0.0, 0.0, 0.00,   0.0, 9999.0, 0.0,  0.0),
}

# ── Demo factory layout ────────────────────────────────────────────────────────

DEMO_MACHINES = [
    {
        "name": "Fresadora CNC #1",
        "model": "HAAS VF-2",
        "sensors": ["temperature", "rpm", "vibration", "current"],
    },
    {
        "name": "Torno CNC #2",
        "model": "Mazak QT-200",
        "sensors": ["temperature", "rpm", "current"],
    },
    {
        "name": "Prensa Hidráulica",
        "model": "Schuler 200T",
        "sensors": ["pressure", "temperature", "current"],
    },
    {
        "name": "Linha de Montagem A",
        "model": "Customizado",
        "sensors": ["production", "vibration"],
    },
]

# ── Sensor state (per tag) ─────────────────────────────────────────────────────

@dataclass
class SensorState:
    profile: SensorProfile
    value: float = 0.0
    phase: float = 0.0
    production_count: float = 0.0

    def __post_init__(self):
        self.value = self.profile.base + random.uniform(-self.profile.noise, self.profile.noise)

    def tick(self, dt: float) -> float:
        p = self.profile
        if p.name == "Produção":
            self.production_count += random.choice([0, 0, 0, 1, 1, 2])
            self.value = self.production_count
            return self.value

        # Slow sinusoidal drift + noise
        self.phase += p.drift_speed * dt
        drift = math.sin(self.phase) * p.noise * 2
        noise = random.gauss(0, p.noise * 0.3)

        # Occasional anomaly spike
        if random.random() < p.anomaly_chance:
            spike = random.uniform(0.5, 1.0) * p.anomaly_magnitude * (p.max_val - p.min_val)
            noise += spike if random.random() > 0.5 else -spike
            log.debug("simulator.anomaly", sensor=p.name, spike=round(spike, 2))

        self.value = max(p.min_val, min(p.max_val, p.base + drift + noise))
        return self.value


# ── API client ────────────────────────────────────────────────────────────────

class ApiClient:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._tenant_id: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(ssl=False)
        self._session = aiohttp.ClientSession(connector=connector)
        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def login(self, email: str, password: str) -> str:
        async with self._session.post(
            f"{self._base}/api/auth/login",
            json={"email": email, "password": password},
        ) as r:
            r.raise_for_status()
            data = await r.json()
            self._token = data["access_token"]
            import base64
            payload_b64 = self._token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.b64decode(payload_b64))
            self._tenant_id = payload["tenant_id"]
            # Rebuild session with auth header baked in (survives redirects)
            await self._session.close()
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            log.info("api.logged_in", tenant_id=self._tenant_id)
            return self._tenant_id

    async def get_or_create_line(self, name: str) -> str:
        async with self._session.get(f"{self._base}/api/lines/") as r:
            r.raise_for_status()
            lines = await r.json()

        for line in lines:
            if line["name"] == name:
                return line["id"]

        async with self._session.post(
            f"{self._base}/api/lines/",
            json={"name": name, "description": "Linha criada pelo simulador demo", "location": "Planta A"},
        ) as r:
            r.raise_for_status()
            return (await r.json())["id"]

    async def get_or_create_machine(self, line_id: str, name: str, model: str) -> str:
        async with self._session.get(
            f"{self._base}/api/machines/?line_id={line_id}"
        ) as r:
            r.raise_for_status()
            machines = await r.json()

        for m in machines:
            if m["name"] == name:
                return m["id"]

        async with self._session.post(
            f"{self._base}/api/machines/",
            json={"line_id": line_id, "name": name, "model": model, "serial_number": f"SIM-{random.randint(1000,9999)}"},
        ) as r:
            r.raise_for_status()
            return (await r.json())["id"]

    async def get_or_create_tag(self, machine_id: str, sensor_type: str) -> str:
        profile = SENSOR_PROFILES[sensor_type]
        async with self._session.get(
            f"{self._base}/api/tags/?machine_id={machine_id}"
        ) as r:
            r.raise_for_status()
            tags = await r.json()

        for t in tags:
            if t["name"] == profile.name:
                return t["id"]

        async with self._session.post(
            f"{self._base}/api/tags/",
            json={
                "machine_id": machine_id,
                "name": profile.name,
                "protocol": "mqtt",
                "address": f"sim/{machine_id}/{sensor_type}",
                "data_type": "float",
                "unit": profile.unit,
                "min_value": profile.min_val,
                "max_value": profile.max_val,
                "poll_interval_ms": 1000,
            },
        ) as r:
            r.raise_for_status()
            return (await r.json())["id"]


# ── Simulator ─────────────────────────────────────────────────────────────────

async def run_simulator(
    server_url: str,
    broker_host: str,
    broker_port: int,
    email: str,
    password: str,
    interval: float = 2.0,
) -> None:
    log.info("simulator.starting", server=server_url, broker=broker_host)

    # 1. Login and setup demo data
    async with ApiClient(server_url) as api:
        tenant_id = await api.login(email, password)
        line_id = await api.get_or_create_line("Linha Demo (Simulador)")

        # Build tag registry: tag_id → SensorState
        tag_states: dict[str, SensorState] = {}
        for machine_cfg in DEMO_MACHINES:
            machine_id = await api.get_or_create_machine(
                line_id, machine_cfg["name"], machine_cfg["model"]
            )
            for sensor_type in machine_cfg["sensors"]:
                tag_id = await api.get_or_create_tag(machine_id, sensor_type)
                profile = SENSOR_PROFILES[sensor_type]
                tag_states[tag_id] = SensorState(profile=profile)
                log.info("simulator.tag_ready", tag=profile.name, id=tag_id[:8])

    log.info("simulator.ready", tags=len(tag_states), tenant_id=tenant_id)
    print(f"\n{'='*55}")
    print(f"  Factory Monitor — Demo Simulator")
    print(f"  Tenant:  {tenant_id}")
    print(f"  Tags:    {len(tag_states)}")
    print(f"  Broker:  {broker_host}:{broker_port}")
    print(f"  Server:  {server_url}")
    print(f"{'='*55}")
    print(f"  Publicando dados a cada {interval}s. Ctrl+C para parar.\n")

    # 2. Connect to MQTT and publish indefinitely
    reconnect_delay = 2
    while True:
        try:
            async with aiomqtt.Client(
                hostname=broker_host,
                port=broker_port,
            ) as client:
                log.info("simulator.mqtt_connected")
                reconnect_delay = 2
                last = time.monotonic()

                while True:
                    now = time.monotonic()
                    dt = now - last
                    last = now

                    for tag_id, state in tag_states.items():
                        value = state.tick(dt)
                        topic = f"factory/{tenant_id}/sensors/{tag_id}"
                        payload = json.dumps({
                            "value": round(value, 3),
                            "unit": state.profile.unit,
                            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        })
                        await client.publish(topic, payload.encode())

                    # Print compact status every 10 ticks
                    if random.random() < 0.1:
                        sample = list(tag_states.items())[:3]
                        status = " | ".join(
                            f"{s.profile.name}: {s.value:.1f}{s.profile.unit}"
                            for _, s in sample
                        )
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] {status}")

                    await asyncio.sleep(interval)

        except aiomqtt.MqttError as e:
            log.warning("simulator.mqtt_disconnected", error=str(e), retry_in=reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except KeyboardInterrupt:
            print("\nSimulador encerrado.")
            return


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Factory Monitor — Demo Simulator")
    parser.add_argument("--server",   default="https://factoryconnect.tech", help="API base URL")
    parser.add_argument("--broker",   default="factoryconnect.tech",          help="MQTT broker host")
    parser.add_argument("--port",     default=1883, type=int,                 help="MQTT broker port")
    parser.add_argument("--email",    default="admin@factory-monitor.com",    help="Login email")
    parser.add_argument("--password", default="Admin@1234",                   help="Login password")
    parser.add_argument("--interval", default=2.0, type=float,                help="Publish interval (seconds)")
    args = parser.parse_args()

    asyncio.run(run_simulator(
        server_url=args.server,
        broker_host=args.broker,
        broker_port=args.port,
        email=args.email,
        password=args.password,
        interval=args.interval,
    ))


if __name__ == "__main__":
    main()
