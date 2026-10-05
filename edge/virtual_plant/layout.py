"""
Layout da "Planta Manaus": hierarquia de ativos, sinais de cada CLP e seus endereços.

É a fonte da verdade de três coisas que precisam concordar entre si:
1. os servidores OPC UA / Modbus da planta virtual (runtime.py) expõem estes sinais nestes endereços;
2. o mapa do gateway (`build_plant_map()` → edge/config/plant.json) aponta para estes endereços;
3. o provisionamento (edge/src/provision.py) cria no servidor estes ativos e tags.

Codificação dos sinais (`kind`):
    bool       OPC UA Boolean        | Modbus discrete input (FC02)
    counter16  OPC UA UInt16         | Modbus holding uint16 (contador de CLP de 16 bits, rola em 65 535)
    counter32  OPC UA UInt32         | Modbus holding uint32, 2 registradores, palavra alta primeiro (ABCD)
    code       OPC UA UInt16         | Modbus holding uint16 (código de falha; 0 = sem falha)
    float      OPC UA Double         | Modbus holding uint16 bruto, valor = bruto × scale (prática comum de CLP)

Endereços Modbus são alocados em ordem de declaração por unidade (unit id): bits em DI a partir de 0,
registradores em HR a partir de 0. O mapa gerado registra o endereço de cada tag explicitamente. Sinais novos
são sempre acrescentados no FIM da lista da máquina, para não mudar endereços já publicados.

Sprint 3 (OEE): o layout também descreve o calendário de turnos (com pausas planejadas), o catálogo de
produtos (código no CLP = `plc_code`, publicado na tag `product_code` da máquina), o tempo de ciclo ideal por
(máquina, produto) e a tabela de códigos de falha → categoria → motivo de parada (`FAULT_CATEGORIES`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

OPCUA_NAMESPACE = "urn:factoryconnect:virtual-plant"
OPCUA_PATH = "/factoryconnect/virtual-plant/"

# Endereços padrão do mapa: placeholders ${VAR:-default} expandidos pelo gateway na carga,
# para o mesmo arquivo servir em localhost e dentro do docker compose (host "virtual-plant").
DEFAULT_OPCUA_ENDPOINT = (
    "opc.tcp://${VIRTUAL_PLANT_HOST:-localhost}:${VIRTUAL_PLANT_OPCUA_PORT:-4840}" + OPCUA_PATH
)
DEFAULT_MODBUS_HOST = "${VIRTUAL_PLANT_HOST:-localhost}"
DEFAULT_MODBUS_PORT = "${VIRTUAL_PLANT_MODBUS_PORT:-5020}"

# Papéis de borda do Sprint 3 que o servidor ainda não tem em Tag.semantic_role: no mapa a tag vai como
# `analog` (aceito pelo servidor e pelo seed) e o papel de borda em `signal_role`. Quando o servidor ganhar
# esses papéis, basta tirar a entrada daqui e regenerar o mapa (--write-map).
SERVER_ROLE = {"product_code": "analog", "starved": "analog", "blocked": "analog"}
COUNTER_ROLES = {"cycle_counter", "cycle_pulse", "part_counter_good", "part_counter_scrap", "part_counter_total"}
COUNTER_MAX = {"counter16": 65_535, "counter32": 4_294_967_295}
SERVER_DATA_TYPE = {"bool": "bool", "counter16": "int", "counter32": "int", "code": "int", "float": "float"}
MODBUS_DATA_TYPE = {"counter16": "uint16", "counter32": "uint32", "code": "uint16", "float": "uint16"}
OPCUA_VARIANT = {"bool": "Boolean", "counter16": "UInt16", "counter32": "UInt32", "code": "UInt16", "float": "Double"}


@dataclass(frozen=True)
class Signal:
    suffix: str                 # chave relativa à máquina, ex. "a1.cycles"
    name: str                   # nome da tag no servidor (único por máquina)
    role: str                   # semantic_role (docs/domain.md §3.1)
    kind: str                   # ver docstring do módulo
    unit: str = ""
    position: Optional[str] = None   # nome da posição (None = tag da máquina)
    scale: float = 1.0          # só Modbus/float: valor = bruto × scale
    poll_interval_ms: int = 1000
    counts_per_cycle: float = 1     # contadores: incrementos por ciclo da máquina (filtro de plausibilidade)


@dataclass(frozen=True)
class Component:
    manufacturer: str
    model: str
    serial_number: str
    nominal_life: dict          # {"cycles": int|None, "hours": float|None, "starts": int|None}


@dataclass(frozen=True)
class Position:
    name: str
    component_type: str
    component: Component
    cycles_per_part: float = 1
    counter_signal: Optional[str] = None   # suffix do sinal que conta os ciclos desta posição


@dataclass(frozen=True)
class Product:
    code: str                   # código do produto no servidor (Product.code)
    name: str
    plc_code: int               # número da receita/programa no CLP (valor da tag product_code)


@dataclass(frozen=True)
class MachineProduct:
    product: str                # Product.code
    ideal_cycle_time_s: float   # ISO 22400: tempo de ciclo ideal da MÁQUINA para este produto
    parts_per_cycle: int = 1    # peças por ciclo (ex.: dispositivo duplo = 2)
    is_default: bool = False


@dataclass(frozen=True)
class Break:
    start_local: str
    end_local: str
    reason_code: str = "PLN-01"
    label: str = ""


@dataclass(frozen=True)
class ShiftDef:
    name: str
    start_local: str            # "HH:MM", hora local da planta
    end_local: str              # pode cruzar a meia-noite ("22:00" → "06:00")
    weekdays: tuple[int, ...]   # 0 = segunda … 6 = domingo (datetime.weekday())
    breaks: tuple[Break, ...] = ()


@dataclass(frozen=True)
class Machine:
    key: str
    name: str
    model: str
    serial_number: str
    protocol: str               # "opcua" | "modbus"
    positions: tuple[Position, ...]
    signals: tuple[Signal, ...]
    unit_id: int = 1            # Modbus unit/slave id
    ideal_cycle_time_s: float = 60.0     # do produto padrão (mantido em Machine.ideal_cycle_time_s)
    products: tuple[MachineProduct, ...] = ()


@dataclass(frozen=True)
class Line:
    key: str
    name: str
    machines: tuple[Machine, ...]
    output_machine: str         # nome da máquina cuja contagem de peças é a produção da linha


@dataclass(frozen=True)
class PlantLayout:
    name: str
    timezone: str
    address: str
    lines: tuple[Line, ...]
    gateway_id: str = "gw-planta-manaus-01"
    shifts: tuple[ShiftDef, ...] = ()
    products: tuple[Product, ...] = ()

    def machines(self) -> list[tuple[Line, Machine]]:
        return [(line, m) for line in self.lines for m in line.machines]


def _life(cycles=None, hours=None, starts=None) -> dict:
    return {"cycles": cycles, "hours": hours, "starts": starts}


def _cyl(name: str, serial: str) -> Position:
    return Position(
        name=f"Cilindro {name}", component_type="pneumatic_cylinder",
        component=Component("Festo", "DSBC-50-100-PPVA-N3", serial, _life(cycles=8_000_000)),
        cycles_per_part=2 if name == "A3" else 1,
        counter_signal=f"{name.lower()}.cycles",
    )


def _product_signal() -> Signal:
    # Número da receita ativa no CLP (Product.plc_code). Acrescentado no fim: não muda endereços antigos.
    return Signal("product_code", "Produto atual (código no CLP)", "product_code", "code", poll_interval_ms=500)


def _flow_signals() -> tuple[Signal, ...]:
    # Célula acoplada: parada por falta de peça a montante (starved) ou saída bloqueada a jusante (blocked).
    return (
        Signal("starved", "Aguardando material (starved)", "starved", "bool", poll_interval_ms=500),
        Signal("blocked", "Saída bloqueada (blocked)", "blocked", "bool", poll_interval_ms=500),
    )


ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)

# Calendário padrão: 3 turnos de 8 h, cada um com 1 h de refeição + 2 cafés de 10 min (PLN-01).
STANDARD_SHIFTS = (
    ShiftDef("Turno A", "06:00", "14:00", ALL_DAYS, (
        Break("08:30", "08:40", "PLN-01", "café"), Break("10:30", "11:30", "PLN-01", "refeição"),
        Break("12:30", "12:40", "PLN-01", "café"))),
    ShiftDef("Turno B", "14:00", "22:00", ALL_DAYS, (
        Break("16:30", "16:40", "PLN-01", "café"), Break("18:00", "19:00", "PLN-01", "refeição"),
        Break("20:30", "20:40", "PLN-01", "café"))),
    ShiftDef("Turno C", "22:00", "06:00", ALL_DAYS, (
        Break("00:30", "00:40", "PLN-01", "café"), Break("02:00", "03:00", "PLN-01", "refeição"),
        Break("04:30", "04:40", "PLN-01", "café"))),
)

PRODUCTS = (
    Product("EIXO-20", "Eixo Ø20 mm", 20),
    Product("EIXO-32", "Eixo Ø32 mm", 32),
    Product("FLG-A", "Flange tipo A", 101),
    Product("FLG-B", "Flange tipo B (dispositivo duplo)", 102),
    Product("SUP-100", "Suporte soldado 100", 210),
    Product("SUP-200", "Suporte soldado 200", 220),
)

_CELL_PRODUCTS = (MachineProduct("SUP-100", 40.0, 1, True), MachineProduct("SUP-200", 44.0, 1))


def _axis(n: int) -> Position:
    return Position(
        name=f"Eixo {n}", component_type="robot_axis",
        component=Component("ABB", f"IRB 2600 — redutor eixo {n}", f"VP-ROB-AX{n}-0001",
                            _life(cycles=4_000_000, hours=20_000)),
        cycles_per_part=(14, 14, 10, 8, 8, 6)[n - 1],
        counter_signal=f"axis{n}.cycles",
    )


PLANTA_MANAUS = PlantLayout(
    name="Planta Manaus",
    timezone="America/Manaus",
    address="Distrito Industrial, Manaus - AM (planta virtual)",
    lines=(
        Line(
            key="usinagem", name="Linha de Usinagem", output_machine="Centro de Usinagem",
            machines=(
                Machine(
                    key="torno", name="Torno CNC", model="Romi GL 250", serial_number="VP-TOR-0001",
                    protocol="opcua", ideal_cycle_time_s=45.0,
                    products=(MachineProduct("EIXO-20", 45.0, 1, True), MachineProduct("EIXO-32", 52.0, 1)),
                    positions=(
                        Position("Eixo-árvore", "spindle",
                                 Component("Romi", "Spindle A2-6 4000rpm", "VP-TOR-SPD-0001", _life(hours=15_000))),
                        Position("Servo eixo X", "servo_motor",
                                 Component("Siemens", "1FK7 060", "VP-TOR-SVX-0001", _life(hours=25_000))),
                        Position("Servo eixo Z", "servo_motor",
                                 Component("Siemens", "1FK7 080", "VP-TOR-SVZ-0001", _life(hours=25_000))),
                    ),
                    signals=(
                        Signal("run_state", "Em operação", "run_state", "bool", poll_interval_ms=500),
                        Signal("fault_state", "Código de falha", "fault_state", "code", poll_interval_ms=500),
                        Signal("parts_good", "Peças boas", "part_counter_good", "counter32", "pcs"),
                        Signal("parts_scrap", "Peças refugadas", "part_counter_scrap", "counter32", "pcs"),
                        Signal("spindle.run", "Eixo-árvore — em operação", "run_state", "bool",
                               position="Eixo-árvore", poll_interval_ms=500),
                        Signal("spindle.speed", "Eixo-árvore — rotação", "speed", "float", "rpm",
                               position="Eixo-árvore"),
                        Signal("spindle.current", "Eixo-árvore — corrente", "current", "float", "A",
                               position="Eixo-árvore"),
                        Signal("servo_x.run", "Servo X — em operação", "run_state", "bool",
                               position="Servo eixo X", poll_interval_ms=500),
                        Signal("servo_x.current", "Servo X — corrente", "current", "float", "A",
                               position="Servo eixo X"),
                        Signal("servo_z.run", "Servo Z — em operação", "run_state", "bool",
                               position="Servo eixo Z", poll_interval_ms=500),
                        Signal("servo_z.current", "Servo Z — corrente", "current", "float", "A",
                               position="Servo eixo Z"),
                        _product_signal(),
                    ),
                ),
                Machine(
                    key="centro", name="Centro de Usinagem", model="Romi D 800", serial_number="VP-CUS-0001",
                    protocol="opcua", ideal_cycle_time_s=90.0,
                    products=(MachineProduct("FLG-A", 90.0, 1, True), MachineProduct("FLG-B", 150.0, 2)),
                    positions=(
                        Position("Eixo-árvore", "spindle",
                                 Component("Romi", "Spindle BT40 10000rpm", "VP-CUS-SPD-0001", _life(hours=12_000))),
                        Position("Garra do trocador de ferramentas", "gripper",
                                 Component("Schunk", "PGN-plus 100", "VP-CUS-GRP-0001", _life(cycles=6_000_000)),
                                 cycles_per_part=4, counter_signal="gripper.cycles"),
                        Position("Motor da bomba de refrigeração", "pump",
                                 Component("WEG", "W22 1,5 cv", "VP-CUS-PMP-0001",
                                           _life(hours=12_000, starts=300_000))),
                    ),
                    signals=(
                        Signal("run_state", "Em operação", "run_state", "bool", poll_interval_ms=500),
                        Signal("fault_state", "Código de falha", "fault_state", "code", poll_interval_ms=500),
                        Signal("parts_good", "Peças boas", "part_counter_good", "counter32", "pcs"),
                        Signal("parts_scrap", "Peças refugadas", "part_counter_scrap", "counter32", "pcs"),
                        Signal("spindle.run", "Eixo-árvore — em operação", "run_state", "bool",
                               position="Eixo-árvore", poll_interval_ms=500),
                        Signal("spindle.speed", "Eixo-árvore — rotação", "speed", "float", "rpm",
                               position="Eixo-árvore"),
                        Signal("spindle.current", "Eixo-árvore — corrente", "current", "float", "A",
                               position="Eixo-árvore"),
                        Signal("gripper.cycles", "Garra do trocador — ciclos (CLP)", "cycle_counter", "counter32",
                               "ciclos", position="Garra do trocador de ferramentas", counts_per_cycle=4),
                        Signal("coolant.run", "Bomba de refrigeração — em operação", "run_state", "bool",
                               position="Motor da bomba de refrigeração", poll_interval_ms=500),
                        Signal("coolant.current", "Bomba de refrigeração — corrente", "current", "float", "A",
                               position="Motor da bomba de refrigeração"),
                        _product_signal(),
                    ),
                ),
            ),
        ),
        Line(
            key="montagem", name="Linha de Montagem", output_machine="Transportador de Saída",
            machines=(
                Machine(
                    key="robo", name="Robô de Solda", model="ABB IRB 2600", serial_number="VP-ROB-0001",
                    protocol="modbus", unit_id=1, ideal_cycle_time_s=40.0, products=_CELL_PRODUCTS,
                    positions=tuple(_axis(n) for n in range(1, 7)),
                    signals=(
                        Signal("run_state", "Em operação", "run_state", "bool", poll_interval_ms=500),
                        Signal("fault_state", "Código de falha", "fault_state", "code", poll_interval_ms=500),
                        Signal("weld_cycles", "Ciclos de solda (CLP)", "cycle_counter", "counter32", "ciclos"),
                        *(Signal(f"axis{n}.cycles", f"Eixo {n} — movimentos (CLP)", "cycle_counter", "counter32",
                                 "ciclos", position=f"Eixo {n}", counts_per_cycle=(14, 14, 10, 8, 8, 6)[n - 1])
                          for n in range(1, 7)),
                        _product_signal(),
                        *_flow_signals(),
                    ),
                ),
                Machine(
                    key="fixacao", name="Estação de Fixação", model="Dispositivo pneumático 4 cilindros",
                    serial_number="VP-FIX-0001", protocol="modbus", unit_id=2, ideal_cycle_time_s=40.0,
                    products=_CELL_PRODUCTS,
                    positions=(
                        Position(
                            name="Cilindro A1", component_type="pneumatic_cylinder",
                            component=Component("Festo", "DSBC-50-100-PPVA-N3", "VP-FIX-A1-0001",
                                                _life(cycles=8_000_000)),
                            # A1 conta pelo pulso de fim de curso (contagem de borda no edge, §3.2); o contador
                            # de 16 bits do CLP vira "ciclos de fixação" da máquina (verificação cruzada).
                            counter_signal="a1.eos",
                        ),
                        _cyl("A2", "VP-FIX-A2-0001"),
                        _cyl("A3", "VP-FIX-A3-0001"),
                        _cyl("A4", "VP-FIX-A4-0001"),
                    ),
                    signals=(
                        Signal("run_state", "Em operação", "run_state", "bool", poll_interval_ms=500),
                        Signal("fault_state", "Código de falha", "fault_state", "code", poll_interval_ms=500),
                        Signal("a1.eos", "Cilindro A1 — fim de curso (pulsos)", "cycle_pulse", "bool", "ciclos",
                               position="Cilindro A1"),
                        # Contador de 16 bits do A1 ligado à MÁQUINA (ciclos de fixação da estação), não à
                        # posição: cada posição tem uma única fonte de ciclos (aqui, o pulso de fim de curso);
                        # duas tags de ciclo na mesma posição seriam somadas pelo servidor.
                        *(s for n in ("A1", "A2", "A3", "A4") for s in (
                            Signal(f"{n.lower()}.cycles",
                                   f"Cilindro {n} — ciclos (CLP 16 bits)" if n != "A1"
                                   else "Ciclos de fixação — CLP 16 bits (cilindro A1)",
                                   "cycle_counter", "counter16", "ciclos",
                                   position=f"Cilindro {n}" if n != "A1" else None,
                                   counts_per_cycle=2 if n == "A3" else 1),
                            Signal(f"{n.lower()}.stroke_time", f"Cilindro {n} — tempo de curso", "stroke_time",
                                   "float", "ms", position=f"Cilindro {n}", scale=0.1),
                        )),
                        _product_signal(),
                        *_flow_signals(),
                    ),
                ),
                Machine(
                    key="transportador", name="Transportador de Saída", model="Esteira 6 m",
                    serial_number="VP-TRP-0001", protocol="modbus", unit_id=3, ideal_cycle_time_s=40.0,
                    products=_CELL_PRODUCTS,
                    positions=(
                        Position("Motor do transportador", "electric_motor",
                                 Component("WEG", "W22 2 cv + redutor", "VP-TRP-MOT-0001",
                                           _life(hours=30_000, starts=200_000))),
                    ),
                    signals=(
                        Signal("fault_state", "Código de falha", "fault_state", "code", poll_interval_ms=500),
                        Signal("motor.run", "Motor — em operação", "run_state", "bool",
                               position="Motor do transportador", poll_interval_ms=500),
                        Signal("motor.current", "Motor — corrente", "current", "float", "A",
                               position="Motor do transportador", scale=0.01),
                        Signal("parts_good", "Peças boas (saída da linha)", "part_counter_good", "counter32", "pcs"),
                        Signal("parts_scrap", "Peças refugadas (saída da linha)", "part_counter_scrap",
                               "counter32", "pcs"),
                        _product_signal(),
                        *_flow_signals(),
                    ),
                ),
            ),
        ),
    ),
    shifts=STANDARD_SHIFTS,
    products=PRODUCTS,
)

# Faixa do código de falha (centena) → categoria → motivo de parada sugerido (docs/domain.md §4.4).
# 3xx = automação e 4xx = pneumática para concordar com o Cenário C (305 → EQP-04).
FAULT_CATEGORIES: dict[int, dict[str, str]] = {
    1: {"category": "mechanical", "reason_code": "EQP-01", "label": "Falha mecânica"},
    2: {"category": "electrical", "reason_code": "EQP-02", "label": "Falha elétrica"},
    3: {"category": "automation", "reason_code": "EQP-04", "label": "Falha de automação/software"},
    4: {"category": "pneumatic", "reason_code": "EQP-03", "label": "Falha pneumática/hidráulica"},
    5: {"category": "tooling", "reason_code": "EQP-05", "label": "Ferramenta/molde/dispositivo"},
}


def fault_category(code: int) -> dict[str, str]:
    """Categoria/motivo de um código de falha pela centena (101 → mecânica/EQP-01)."""
    try:
        return FAULT_CATEGORIES[int(code) // 100]
    except KeyError:
        raise ValueError(f"código de falha fora das faixas 100–599: {code!r}") from None


# Códigos de falha por máquina (código → descrição). Cada máquina tem códigos de várias categorias, para o
# Pareto por categoria ter o que mostrar. Usados pelo modelo e documentados no mapa.
FAULT_CODES: dict[str, dict[int, str]] = {
    "usinagem.torno": {101: "Travamento da placa", 102: "Quebra da correia do eixo-árvore",
                       204: "Sobrecarga do motor do eixo-árvore", 205: "Erro de servo eixo X",
                       305: "Falha de comunicação CNC/CLP", 410: "Pressão de ar baixa na placa pneumática",
                       520: "Quebra de ferramenta"},
    "usinagem.centro": {111: "Falha mecânica do trocador de ferramentas", 212: "Sobrecarga do eixo-árvore",
                        213: "Falha no motor da bomba de refrigeração", 311: "Erro de programa CNC",
                        421: "Falha de fixação pneumática da peça", 521: "Quebra de ferramenta"},
    "montagem.robo": {131: "Colisão detectada", 231: "Falha da fonte de solda",
                      331: "Erro de encoder / controlador do robô", 532: "Bico de contato da tocha gasto"},
    "montagem.fixacao": {141: "Travamento do dispositivo", 241: "Sensor de presença de peça com falha",
                         441: "Pressão de ar baixa",
                         442: "Cilindro não atingiu fim de curso (timeout)", 541: "Gabarito de fixação gasto"},
    "montagem.transportador": {151: "Correia desalinhada", 251: "Sobrecarga do motor",
                               351: "Emergência acionada (CLP de segurança)"},
}


def product_by_code(layout: "PlantLayout", code: str) -> Product:
    return next(p for p in layout.products if p.code == code)


def default_product(machine: Machine) -> MachineProduct:
    return next((mp for mp in machine.products if mp.is_default), machine.products[0])


# ── Endereçamento ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Address:
    key: str                    # chave global do sinal, ex. "montagem.fixacao.a1.cycles"
    line: Line
    machine: Machine
    signal: Signal
    protocol: str
    node_id: str = ""           # OPC UA
    unit_id: int = 0            # Modbus
    register_type: str = ""     # "discrete" | "holding"
    register: int = 0
    data_type: str = ""         # Modbus data_type do gateway


def signal_key(line: Line, machine: Machine, signal: Signal) -> str:
    return f"{line.key}.{machine.key}.{signal.suffix}"


def addresses(layout: PlantLayout = PLANTA_MANAUS, ns_index: int = 2) -> list[Address]:
    """Endereço de cada sinal, em ordem de declaração (determinístico)."""
    out: list[Address] = []
    for line, m in layout.machines():
        di = hr = 0
        for s in m.signals:
            key = signal_key(line, m, s)
            if m.protocol == "opcua":
                out.append(Address(key, line, m, s, "opcua", node_id=f"ns={ns_index};s={key}"))
                continue
            if s.kind == "bool":
                out.append(Address(key, line, m, s, "modbus", unit_id=m.unit_id,
                                   register_type="discrete", register=di))
                di += 1
            else:
                dt = MODBUS_DATA_TYPE[s.kind]
                out.append(Address(key, line, m, s, "modbus", unit_id=m.unit_id,
                                   register_type="holding", register=hr, data_type=dt))
                hr += 2 if dt.endswith("32") else 1
    return out


def address_string(a: Address) -> str:
    """Endereço legível gravado em Tag.address no servidor."""
    if a.protocol == "opcua":
        return a.node_id
    area = "di" if a.register_type == "discrete" else "hr"
    suffix = f":{a.data_type}" if a.data_type else ""
    return f"unit={a.unit_id};{area}={a.register}{suffix}"


# ── Mapa do gateway ───────────────────────────────────────────────────────────

def build_plant_map(
    layout: PlantLayout = PLANTA_MANAUS,
    opcua_endpoint: str = DEFAULT_OPCUA_ENDPOINT,
    modbus_host: str = DEFAULT_MODBUS_HOST,
    modbus_port: str | int = DEFAULT_MODBUS_PORT,
    pulse_poll_ms: int = 20,
    heartbeat_s: float = 10.0,
    shifts: Optional[tuple[ShiftDef, ...]] = None,
) -> dict:
    """Mapa de dispositivos/tags consumido pelo gateway (edge/src/plant_map.py) e pelo provisionamento.
    `shifts` substitui o calendário do layout (ex.: o turno de um cenário de ouro, para o servidor ter as
    mesmas pausas planejadas que o cenário)."""
    shifts = layout.shifts if shifts is None else shifts
    lines = []
    for line in layout.lines:
        machines = []
        for m in line.machines:
            machines.append({
                "name": m.name, "model": m.model, "serial_number": m.serial_number,
                "ideal_cycle_time_s": m.ideal_cycle_time_s,
                "products": [{"product": mp.product, "ideal_cycle_time_s": mp.ideal_cycle_time_s,
                              "parts_per_cycle": mp.parts_per_cycle, "is_default": mp.is_default}
                             for mp in m.products],
                "fault_code_map": {str(c): fault_category(c)["reason_code"]
                                   for c in sorted(FAULT_CODES.get(f"{line.key}.{m.key}", {}))},
                "positions": [{
                    "name": p.name, "component_type": p.component_type,
                    "cycles_per_part": p.cycles_per_part,
                    "counter_tag": f"{line.key}.{m.key}.{p.counter_signal}" if p.counter_signal else None,
                    "component": {
                        "component_type": p.component_type,
                        "manufacturer": p.component.manufacturer, "model": p.component.model,
                        "serial_number": p.component.serial_number,
                        "nominal_life": dict(p.component.nominal_life),
                    },
                } for p in m.positions],
            })
        lines.append({"name": line.name, "output_machine": line.output_machine, "machines": machines})

    tags = []
    for a in addresses(layout):
        s = a.signal
        tag = {
            "key": a.key,
            "tag_id": None,
            "name": s.name,
            "line": a.line.name,
            "machine": a.machine.name,
            "position": s.position,
            "semantic_role": SERVER_ROLE.get(s.role, s.role),
            "data_type": SERVER_DATA_TYPE[s.kind],
            "unit": s.unit,
            "counter_max": COUNTER_MAX.get(s.kind) if s.role != "cycle_pulse" else None,
            # contagens/s máximas fisicamente plausíveis (2× a taxa ideal); o provisionamento multiplica
            # por --speed quando a planta virtual roda acelerada (docs/domain.md §3.3 regra 4)
            "max_rate_per_s": (round(2 * s.counts_per_cycle / a.machine.ideal_cycle_time_s, 4)
                               if s.role in COUNTER_ROLES else None),
            "source": "opcua-plc" if a.protocol == "opcua" else "modbus-plc",
            "address": a.node_id if a.protocol == "opcua" else a.register,
            "scale": s.scale if a.protocol == "modbus" else 1.0,
            "poll_interval_ms": s.poll_interval_ms,
        }
        if s.role in SERVER_ROLE:
            tag["signal_role"] = s.role
        if a.protocol == "modbus":
            tag["unit_id"] = a.unit_id
            tag["register_type"] = a.register_type
            if a.data_type:
                tag["data_type_plc"] = a.data_type
                tag["word_order"] = "big"
        tags.append(tag)

    return {
        "version": 1,
        "description": f"{layout.name} — mapa de dispositivos/tags da planta virtual. "
                       "Gerado por `python -m edge.virtual_plant --write-map`; tag_id é preenchido por "
                       "`python -m edge.src.provision`.",
        "gateway_id": layout.gateway_id,
        "tenant_id": None,
        "heartbeat_s": heartbeat_s,
        "pulse_poll_ms": pulse_poll_ms,
        "sources": [
            # Política de timestamp (edge/src/clock.py): o CLP virtual usa o relógio do host (sincronizado),
            # então o SourceTimestamp é confiável; acima de 2 s de desvio o gateway passa a usar o próprio
            # relógio. Playback de cenário com relógio deslocado: VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off.
            {"id": "opcua-plc", "protocol": "opcua", "endpoint": opcua_endpoint, "publishing_interval_ms": 250,
             "timestamps": "${VIRTUAL_PLANT_TIMESTAMPS:-source}",
             "max_clock_skew_s": "${VIRTUAL_PLANT_MAX_CLOCK_SKEW_S:-2}",
             # o playback de cenário re-carimba as tags paradas (heartbeat no relógio do cenário); no modo
             # normal discretos só são escritos quando mudam, então notificar por timestamp não custa nada
             "device_heartbeat": True},
            {"id": "modbus-plc", "protocol": "modbus", "host": modbus_host, "port": modbus_port},
        ],
        "assets": {
            "plant": {
                "name": layout.name, "timezone": layout.timezone, "address": layout.address,
                "shifts": [{
                    "name": sh.name, "start_local": sh.start_local, "end_local": sh.end_local,
                    "weekdays": list(sh.weekdays),
                    "breaks": [{"start_local": b.start_local, "end_local": b.end_local,
                                "reason_code": b.reason_code, "label": b.label} for b in sh.breaks],
                } for sh in shifts],
            },
            "products": [{"code": p.code, "name": p.name, "plc_code": p.plc_code} for p in layout.products],
            "lines": lines,
        },
        "fault_categories": {f"{k}00-{k}99": v for k, v in FAULT_CATEGORIES.items()},
        "fault_codes": {k: {str(c): {"description": d, **fault_category(c)} for c, d in sorted(v.items())}
                        for k, v in FAULT_CODES.items()},
        "product_codes": {str(p.plc_code): p.code for p in layout.products},
        "tags": tags,
    }
