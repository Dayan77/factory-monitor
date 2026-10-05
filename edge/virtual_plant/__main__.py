"""
Planta virtual "Planta Manaus" — CLI.

    # servidores OPC UA (4840) + Modbus TCP (5020) + estado HTTP (8091), tempo real
    python -m edge.virtual_plant
    # determinístico e acelerado: 1 h simulada por minuto
    python -m edge.virtual_plant --seed 42 --speed 60
    # com turnos (fora do turno tudo para); relógio simulado começa às 06:00
    python -m edge.virtual_plant --shifts 06:00-14:00,14:00-22:00 --start-time 06:00
    # sem servidores: roda N segundos simulados e imprime o snapshot (números conhecidos para o QA)
    python -m edge.virtual_plant --seed 42 --simulate 3600
    # (re)gera o mapa do gateway a partir do layout
    python -m edge.virtual_plant --write-map edge/config/plant.json

No container do edge o pacote é `virtual_plant` (python -m virtual_plant ...).
Escuta em 127.0.0.1 por padrão; use --host 0.0.0.0 só em rede de testes (sem segurança OPC UA/Modbus).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys

from .layout import build_plant_map
from .model import PlantModel, _hhmm


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="virtual_plant", description="Planta virtual (OPC UA + Modbus TCP)")
    p.add_argument("--seed", type=int, default=int(env("VP_SEED", "42")), help="semente (padrão 42)")
    p.add_argument("--speed", type=float, default=float(env("VP_SPEED", "1")),
                   help="aceleração do tempo: 60 = 1 h simulada por minuto (padrão 1)")
    p.add_argument("--host", default=env("VP_HOST", "127.0.0.1"), help="interface de escuta (padrão 127.0.0.1)")
    p.add_argument("--opcua-port", type=int, default=int(env("VP_OPCUA_PORT", "4840")))
    p.add_argument("--modbus-port", type=int, default=int(env("VP_MODBUS_PORT", "5020")))
    p.add_argument("--status-port", type=int, default=int(env("VP_STATUS_PORT", "8091")),
                   help="HTTP GET /state e /health (0 desliga)")
    p.add_argument("--shifts", default=env("VP_SHIFTS", ""), help="ex. 06:00-14:00,14:00-22:00 (vazio = 24 h)")
    p.add_argument("--start-time", default=env("VP_START_TIME", "06:00"), help="hora do dia no tempo simulado 0")
    p.add_argument("--no-faults", action="store_true", help="desliga falhas aleatórias")
    p.add_argument("--pulse-ms", type=int, default=int(env("VP_PULSE_MS", "50")),
                   help="largura real do pulso de fim de curso, alto e baixo (padrão 50 ms)")
    p.add_argument("--update-ms", type=int, default=100, help="período de atualização dos servidores")
    p.add_argument("--simulate", type=float, metavar="SECONDS",
                   help="não sobe servidores: simula N segundos e imprime o snapshot JSON")
    p.add_argument("--write-map", metavar="PATH", help="grava o mapa do gateway (JSON) e sai")
    return p


def make_model(args: argparse.Namespace) -> PlantModel:
    return PlantModel(seed=args.seed, faults=not args.no_faults, shifts=args.shifts or None,
                      start_second_of_day=_hhmm(args.start_time))


async def _serve(args: argparse.Namespace) -> None:
    from .runtime import VirtualPlantRuntime   # importa asyncua/pymodbus só quando precisa

    rt = VirtualPlantRuntime(
        make_model(args), speed=args.speed, host=args.host, opcua_port=args.opcua_port,
        modbus_port=args.modbus_port, status_port=args.status_port or None, update_ms=args.update_ms,
        pulse_high_ms=args.pulse_ms, pulse_low_ms=args.pulse_ms,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await rt.start()
    print(f"Planta virtual no ar — OPC UA {rt.opcua_endpoint} | Modbus {args.host}:{args.modbus_port} | "
          f"estado http://{args.host}:{args.status_port}/state | seed={args.seed} speed={args.speed}x",
          flush=True)
    await stop.wait()
    await rt.stop()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("asyncua", "pymodbus"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.write_map:
        with open(args.write_map, "w", encoding="utf-8") as f:
            json.dump(build_plant_map(), f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"mapa gravado em {args.write_map}")
        return 0
    if args.simulate is not None:
        model = make_model(args)
        model.run_for(args.simulate)
        json.dump(model.snapshot(), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    asyncio.run(_serve(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
