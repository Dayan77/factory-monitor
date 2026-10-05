"""
Runtime da planta virtual: liga o modelo determinístico a servidores reais.

- OPC UA (asyncua): Objects/Planta Manaus/<linha>/<máquina>/<sinal>, NodeId string = chave do sinal
  (`ns=2;s=usinagem.torno.run_state`). Cada escrita leva SourceTimestamp (UTC, relógio real).
- Modbus TCP (pymodbus 3.15, SimDevice por unit id): bits em discrete inputs, valores em holding
  registers (ver layout.py). Leitura sempre reflete a memória atual (hook `action`).
- Bit de fim de curso (cycle_pulse): trem de pulsos com largura mínima **real** (padrão 50 ms alto /
  50 ms baixo) que emite exatamente os golpes do modelo. Se a aceleração (`--speed`) pedir mais pulsos do
  que cabem nessa largura, sobra fila (`backlog` no /state) — nunca se perde nem se inventa pulso.
- HTTP de estado (aiohttp): GET /state = snapshot determinístico + verdade de campo + pulsos emitidos;
  GET /health.

Tempo: o modelo avança `speed` segundos simulados por segundo real, sempre em passos inteiros, então o
estado num dado tempo simulado não depende do relógio real. Timestamps publicados são o relógio real (UTC).

Playback de cenário (`ScenarioModel`, edge/virtual_plant/scenario.py): o modelo entrega cada mudança com o seu
instante exato (`drain_changes()`); o runtime escreve TODAS, em ordem, cada uma com SourceTimestamp = relógio
do dispositivo naquele instante (`device_time()`), mesmo várias do mesmo nó num só ciclo de atualização — o
gateway assina com fila (queue size) e recebe cada transição com o seu timestamp.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from aiohttp import web
from asyncua import Server, ua
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import SimData, SimDevice
from pymodbus.simulator.simutils import DataType

from .layout import OPCUA_NAMESPACE, OPCUA_PATH, OPCUA_VARIANT, Address, addresses
from .model import ANALOG_ROLES, PlantModel

log = logging.getLogger("virtual_plant")

_FC_AREA = {1: "co", 5: "co", 15: "co", 2: "di", 3: "hr", 6: "hr", 16: "hr", 4: "ir"}
MODBUS_AREA_SIZE = 64


class PulseTrain:
    """Gera no bit exatamente `target` pulsos, cada um com largura mínima alto/baixo em tempo real."""

    def __init__(self, high_s: float = 0.05, low_s: float = 0.05, clock: Callable[[], float] = time.monotonic):
        self.high_s = high_s
        self.low_s = low_s
        self._clock = clock
        self.target = 0
        self.emitted = 0
        self.level = False
        self._next = 0.0

    def update(self, target: int, now: Optional[float] = None) -> bool:
        now = self._clock() if now is None else now
        self.target = max(self.target, target)
        if self.level:
            if now >= self._next:
                self.level = False
                self._next = now + self.low_s
        elif now >= self._next and self.emitted < self.target:
            self.level = True
            self.emitted += 1
            self._next = now + self.high_s
        return self.level

    @property
    def backlog(self) -> int:
        return self.target - self.emitted


def encode_modbus(a: Address, value) -> list[int]:
    """Valor de engenharia → registradores (ou [bit]) conforme layout."""
    if a.register_type == "discrete":
        return [1 if value else 0]
    if a.data_type == "uint32":
        v = int(value) & 0xFFFF_FFFF
        return [(v >> 16) & 0xFFFF, v & 0xFFFF]
    raw = round(float(value) / a.signal.scale) if a.signal.kind == "float" else int(value)
    return [max(0, min(0xFFFF, raw))]


class ModbusPlc:
    """Servidor Modbus TCP com uma memória por unit id (co/di/hr/ir), refletida a cada requisição."""

    def __init__(self, host: str, port: int, unit_ids: list[int], size: int = MODBUS_AREA_SIZE) -> None:
        self.size = size
        self.memory = {u: {area: [0] * size for area in ("co", "di", "hr", "ir")} for u in unit_ids}
        devices = [self._device(u) for u in unit_ids]
        self.server = ModbusTcpServer(devices, address=(host, port))
        self.host, self.port = host, port

    def _device(self, unit: int) -> SimDevice:
        size = self.size
        bits = lambda: [SimData(0, count=size, values=False, datatype=DataType.BITS)]  # noqa: E731
        regs = lambda: [SimData(0, count=size, values=0, datatype=DataType.REGISTERS)]  # noqa: E731

        async def action(function_code, start_address, address, count, current_registers, set_values):
            mem = self.memory[unit][_FC_AREA[function_code]]
            for i in range(len(current_registers)):
                reg = start_address + i
                if _FC_AREA[function_code] in ("co", "di"):
                    word = 0   # áreas de bit empacotadas: 16 bits por palavra, bit 0 = LSB
                    for b in range(16):
                        n = reg * 16 + b
                        if n < size and mem[n]:
                            word |= 1 << b
                    current_registers[i] = word
                elif 0 <= reg < size:
                    current_registers[i] = mem[reg]
            return None

        return SimDevice(unit, simdata=(bits(), bits(), regs(), regs()), action=action)

    def write(self, unit: int, area: str, address: int, values: list[int]) -> None:
        mem = self.memory[unit][area]
        mem[address:address + len(values)] = values

    async def start(self) -> None:
        await self.server.serve_forever(background=True)
        for _ in range(100):
            try:
                _, w = await asyncio.open_connection(self.host if self.host not in ("", "0.0.0.0") else "127.0.0.1",
                                                     self.port)
                w.close()
                await w.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.05)
        raise RuntimeError(f"Modbus não subiu em {self.host}:{self.port}")

    async def stop(self) -> None:
        await self.server.shutdown()


class VirtualPlantRuntime:
    def __init__(
        self,
        model: PlantModel,
        speed: float = 1.0,
        host: str = "127.0.0.1",
        opcua_port: int = 4840,
        modbus_port: int = 5020,
        status_port: Optional[int] = 8091,
        update_ms: int = 100,
        pulse_high_ms: int = 50,
        pulse_low_ms: int = 50,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed deve ser > 0")
        self.model = model
        self.speed = speed
        self.host = host
        self.opcua_port = opcua_port
        self.modbus_port = modbus_port
        self.status_port = status_port
        self.update_s = update_ms / 1000.0
        self.addresses = addresses(model.layout)
        self.by_key = {a.key: a for a in self.addresses}
        self.pulses = {k: PulseTrain(pulse_high_ms / 1000, pulse_low_ms / 1000) for k in model.pulse_targets()}
        self.opcua: Optional[Server] = None
        self.modbus: Optional[ModbusPlc] = None
        self._nodes: dict[str, ua.NodeId] = {}
        self._last: dict[str, object] = {}
        self._tasks: list[asyncio.Task] = []
        self._sim_task: Optional[asyncio.Task] = None
        self._runner: Optional[web.AppRunner] = None
        self._running = False
        self.lag_s = 0.0
        self._finished_logged = False
        self.started_at: Optional[datetime] = None

    @property
    def opcua_endpoint(self) -> str:
        return f"opc.tcp://{self.host}:{self.opcua_port}{OPCUA_PATH}"

    # ── start/stop ────────────────────────────────────────────────────────
    async def start(self, paused: bool = False) -> None:
        """Sobe os servidores. `paused=True`: tempo simulado parado até `resume()` (playback de cenário
        espera o gateway assinar para não perder as primeiras transições)."""
        await self._start_opcua()
        units = sorted({a.unit_id for a in self.addresses if a.protocol == "modbus"})
        self.modbus = ModbusPlc(self.host, self.modbus_port, units)
        await self.modbus.start()
        await self._push(force=True)
        if self.status_port:
            await self._start_http()
        self._running = True
        self.started_at = datetime.now(timezone.utc)
        self._tasks = [asyncio.create_task(self._pulse_loop())]
        if not paused:
            self.resume()
        log.info("virtual_plant.started opcua=%s modbus=%s:%s speed=%s seed=%s paused=%s",
                 self.opcua_endpoint, self.host, self.modbus_port, self.speed, self.model.seed, paused)

    def resume(self) -> None:
        """(Re)inicia o tempo simulado a partir do tempo atual do modelo."""
        if self._sim_task is None or self._sim_task.done():
            self._sim_task = asyncio.create_task(self._sim_loop())
            self._tasks.append(self._sim_task)

    def monitored_items(self) -> int:
        """Quantos itens OPC UA estão assinados agora (clientes como o gateway)."""
        try:
            subs = self.opcua.iserver.subscription_service.subscriptions.values()
            return sum(len(sub.monitored_item_srv._monitored_items) for sub in subs)
        except AttributeError:
            return 0

    async def wait_for_subscribers(self, min_items: int = 1, settle_s: float = 1.0,
                                   timeout: Optional[float] = None) -> bool:
        """Espera um cliente OPC UA assinar ao menos `min_items` itens (+ `settle_s` para receber os
        valores iniciais). Retorna False no timeout."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while self.monitored_items() < min_items:
            if deadline is not None and loop.time() >= deadline:
                return False
            await asyncio.sleep(0.1)
        await asyncio.sleep(settle_s)
        return True

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []
        self._sim_task = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self.modbus:
            await self.modbus.stop()
            self.modbus = None
        if self.opcua:
            await self.opcua.stop()
            self.opcua = None

    async def __aenter__(self) -> "VirtualPlantRuntime":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def freeze(self) -> None:
        """Para o tempo simulado (servidores seguem no ar com os últimos valores). Útil em testes/QA."""
        if self._sim_task is not None:
            self._sim_task.cancel()
            try:
                await self._sim_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._sim_task = None
        await self._push()

    # ── OPC UA ────────────────────────────────────────────────────────────
    async def _start_opcua(self) -> None:
        server = Server()
        await server.init()
        server.set_endpoint(self.opcua_endpoint)
        server.set_server_name("FactoryConnect — Planta Virtual (Planta Manaus)")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])   # dev/testes apenas
        idx = await server.register_namespace(OPCUA_NAMESPACE)
        if idx != 2:
            raise RuntimeError(f"namespace da planta virtual deveria ser ns=2 (veio {idx})")
        plant = await server.nodes.objects.add_object(idx, self.model.layout.name)
        values = self.model.values()
        folders: dict[str, object] = {}
        for a in self.addresses:
            if a.protocol != "opcua":
                continue
            lkey = a.line.key
            if lkey not in folders:
                folders[lkey] = await plant.add_object(idx, a.line.name)
            mkey = f"{lkey}.{a.machine.key}"
            if mkey not in folders:
                folders[mkey] = await folders[lkey].add_object(idx, a.machine.name)
            vt = getattr(ua.VariantType, OPCUA_VARIANT[a.signal.kind])
            node = await folders[mkey].add_variable(
                ua.NodeId(a.key, idx), a.signal.name, ua.Variant(self._opc_value(a, values[a.key]), vt)
            )
            self._nodes[a.key] = node.nodeid
        await server.start()
        self.opcua = server

    @staticmethod
    def _opc_value(a: Address, value):
        kind = a.signal.kind
        if kind == "bool":
            return bool(value)
        if kind == "float":
            return float(value)
        return int(value)

    # ── loops ─────────────────────────────────────────────────────────────
    async def _write(self, key: str, value, ts: datetime) -> None:
        a = self.by_key[key]
        self._last[key] = value
        if a.protocol == "opcua" and self.opcua is not None:
            vt = getattr(ua.VariantType, OPCUA_VARIANT[a.signal.kind])
            await self.opcua.write_attribute_value(
                self._nodes[key],
                ua.DataValue(Value=ua.Variant(self._opc_value(a, value), vt),
                             SourceTimestamp=ts, ServerTimestamp=datetime.now(timezone.utc)),
            )
        elif a.protocol == "modbus" and self.modbus is not None:
            area = "di" if a.register_type == "discrete" else "hr"
            self.modbus.write(a.unit_id, area, a.register, encode_modbus(a, value))

    async def _push(self, force: bool = False) -> None:
        drain = getattr(self.model, "drain_changes", None)
        if drain is not None:          # playback: cada mudança com o seu instante exato
            if force:
                ts = self.model.device_time(self.model.sim_time_s)
                for key, value in self.model.values().items():
                    if self.by_key[key].signal.role != "cycle_pulse":
                        await self._write(key, value, ts)
            for t, key, value in drain():
                await self._write(key, value, self.model.device_time(t))
            return
        values = self.model.values()
        now = datetime.now(timezone.utc)
        for key, value in values.items():
            a = self.by_key[key]
            if a.signal.role == "cycle_pulse":
                continue   # bit escrito pelo _pulse_loop
            analog = a.signal.role in ANALOG_ROLES
            if not force and not analog and self._last.get(key) == value:
                continue
            await self._write(key, value, now)

    async def _sim_loop(self) -> None:
        t0 = time.monotonic()
        sim0 = self.model.sim_time_s
        warned = False
        while self._running:
            target = sim0 + (time.monotonic() - t0) * self.speed
            # Em blocos para não travar o event loop (servidores precisam responder).
            self.model.advance_to(target, max_ticks=5000)
            self.lag_s = max(0.0, target - self.model.sim_time_s)
            if self.lag_s > 5 * self.speed and not warned:
                log.warning("virtual_plant.lagging lag_s=%.1f — reduza --speed", self.lag_s)
                warned = True
            await self._push()
            if getattr(self.model, "finished", False) and not self._finished_logged:
                self._finished_logged = True
                log.info("virtual_plant.scenario_finished sim_time_s=%s — servidores seguem no ar com o "
                         "estado final", self.model.sim_time_s)
            await asyncio.sleep(self.update_s if self.lag_s < 1 else 0)

    async def _pulse_loop(self) -> None:
        while self._running:
            targets = self.model.pulse_targets()
            now = time.monotonic()
            for key, train in self.pulses.items():
                level = train.update(targets[key], now)
                a = self.by_key[key]
                if self.modbus is not None:
                    self.modbus.write(a.unit_id, "di", a.register, [1 if level else 0])
            await asyncio.sleep(0.002)

    # ── HTTP de estado ────────────────────────────────────────────────────
    def state(self) -> dict:
        snap = self.model.snapshot()
        snap["runtime"] = {
            "speed": self.speed,
            "lag_s": round(self.lag_s, 1),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "opcua_endpoint": self.opcua_endpoint,
            "modbus": f"{self.host}:{self.modbus_port}",
            "pulses": {k: {"target": p.target, "emitted": p.emitted, "backlog": p.backlog}
                       for k, p in self.pulses.items()},
        }
        return snap

    async def _start_http(self) -> None:
        app = web.Application()
        app.router.add_get("/state", lambda _r: web.json_response(self.state()))
        app.router.add_get("/health", lambda _r: web.json_response({"ok": True, "sim_time_s": self.model.sim_time_s}))
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.status_port).start()
