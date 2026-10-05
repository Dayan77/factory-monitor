"""
Planta virtual "Planta Manaus" — CLI.

    # servidores OPC UA (4840) + Modbus TCP (5020) + estado HTTP (8091), tempo real
    python -m edge.virtual_plant
    # determinístico e acelerado: 1 h simulada por minuto
    python -m edge.virtual_plant --seed 42 --speed 60
    # calendário padrão (3 turnos com refeição + 2 cafés, setup SET-01 1×/turno); 24 h sem calendário:
    python -m edge.virtual_plant --calendar none
    # turnos simples sem pausas (legado); relógio simulado começa às 06:00
    python -m edge.virtual_plant --shifts 06:00-14:00,14:00-22:00 --start-time 06:00
    # perda de velocidade de 8 % e 6 micro-paradas por hora em operação em todas as máquinas
    python -m edge.virtual_plant --speed-loss-pct 8 --micro-stops-per-h 6
    # playback fiel de um cenário de ouro (8 h em 8 min), relógio do cenário deslocado N dias para o passado
    python -m edge.virtual_plant --scenario docs/scenarios/scenario_c_oee_shift.json --speed 60
    # manutenção (Sprint 4): falhas por desgaste esperam a equipe (API de controle em 127.0.0.1:7070)
    python -m edge.virtual_plant --speed 60 --wear-repair manual
    python -m edge.virtual_plant.control force-failure --machine montagem.robo --code 331
    # sem servidores: roda N segundos simulados e imprime o snapshot (números conhecidos para o QA)
    python -m edge.virtual_plant --seed 42 --simulate 3600
    # (re)gera o mapa do gateway a partir do layout
    python -m edge.virtual_plant --write-map edge/config/plant.json
    # mapa com o calendário de um cenário (para provisionar um tenant de QA com as pausas do cenário)
    python -m edge.virtual_plant --write-map /tmp/plant.scenario_c.json --scenario docs/scenarios/scenario_c_oee_shift.json

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
from .model import PlantConfig, PlantModel, _hhmm


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
    p.add_argument("--calendar", choices=("standard", "none"), default=env("VP_CALENDAR", "standard"),
                   help="standard = 3 turnos com pausas e setup por turno (padrão); none = 24 h")
    p.add_argument("--shifts", default=env("VP_SHIFTS", ""),
                   help="turnos simples sem pausas, ex. 06:00-14:00,14:00-22:00 (substitui --calendar)")
    p.add_argument("--speed-loss-pct", type=float, default=float(env("VP_SPEED_LOSS_PCT", "5")),
                   help="ciclo real = ideal × (1 + x%% + U(0, 2%%)) (padrão 5)")
    p.add_argument("--micro-stops-per-h", type=float, default=None,
                   help="micro-paradas por hora em operação, todas as máquinas (padrão por máquina: 3/2/4)")
    p.add_argument("--no-setups", action="store_true", help="sem troca de produto (SET-01) por turno")
    p.add_argument("--scenario", default=env("VP_SCENARIO", ""), metavar="JSON",
                   help="playback de cenário (ex. docs/scenarios/scenario_c_oee_shift.json)")
    p.add_argument("--scenario-clock", choices=("shifted", "wall"), default=env("VP_SCENARIO_CLOCK", "shifted"),
                   help="timestamps do playback: horário do cenário deslocado N dias (padrão) ou relógio real")
    p.add_argument("--scenario-start", choices=("subscribe", "now"), default=env("VP_SCENARIO_START", "subscribe"),
                   help="subscribe = só começa quando um cliente OPC UA (gateway) assinar (padrão); now = já")
    p.add_argument("--scenario-heartbeat-s", type=float, default=float(env("VP_SCENARIO_HEARTBEAT_S", "10")),
                   help="re-escrita periódica (relógio do cenário) das tags da máquina; 0 desliga (padrão 10)")
    p.add_argument("--scenario-shift-days", type=int, default=None,
                   help="dias a subtrair do horário do cenário (padrão: o menor que deixa o fim ≤ agora)")
    p.add_argument("--start-time", default=env("VP_START_TIME", "06:00"), help="hora do dia no tempo simulado 0")
    p.add_argument("--no-faults", action="store_true", help="desliga falhas aleatórias (e as por desgaste)")
    p.add_argument("--no-wear-failures", action="store_true",
                   help="desgaste e sintomas continuam, mas sem falha quando a vida do componente acaba")
    p.add_argument("--wear-repair", choices=("auto", "manual"), default=env("VP_WEAR_REPAIR", "auto"),
                   help="auto = o plantão troca o componente após um tempo sorteado (padrão); manual = a falha "
                        "fica ativa até POST /replace ou /repair na API de controle")
    p.add_argument("--control-port", type=int, default=int(env("VP_CONTROL_PORT", "7070")),
                   help="API de controle da manutenção (GET /status, POST /replace /repair /force-failure); 0 desliga")
    p.add_argument("--control-host", default=env("VP_CONTROL_HOST", "127.0.0.1"),
                   help="interface da API de controle (padrão 127.0.0.1; outra exige VP_CONTROL_TOKEN)")
    p.add_argument("--pulse-ms", type=int, default=int(env("VP_PULSE_MS", "50")),
                   help="largura real do pulso de fim de curso, alto e baixo (padrão 50 ms)")
    p.add_argument("--update-ms", type=int, default=100, help="período de atualização dos servidores")
    p.add_argument("--simulate", type=float, metavar="SECONDS",
                   help="não sobe servidores: simula N segundos e imprime o snapshot JSON")
    p.add_argument("--write-map", metavar="PATH", help="grava o mapa do gateway (JSON) e sai")
    return p


def make_model(args: argparse.Namespace):
    if args.scenario:
        from .scenario import Scenario, ScenarioClock, ScenarioModel
        sc = Scenario.load(args.scenario)
        clock = ScenarioClock(sc, args.scenario_clock, args.scenario_shift_days, speed=args.speed)
        return ScenarioModel(sc, clock, seed=args.seed, heartbeat_s=args.scenario_heartbeat_s)
    cfg = PlantConfig(speed_loss_pct=args.speed_loss_pct, setups=not args.no_setups,
                      wear_failures=not args.no_wear_failures, wear_repair=args.wear_repair)
    cfg.with_micro_rate(args.micro_stops_per_h)
    shifts = args.shifts or ("standard" if args.calendar == "standard" else None)
    return PlantModel(seed=args.seed, faults=not args.no_faults, shifts=shifts,
                      start_second_of_day=_hhmm(args.start_time), config=cfg)


async def _serve(args: argparse.Namespace) -> None:
    from .runtime import VirtualPlantRuntime   # importa asyncua/pymodbus só quando precisa

    rt = VirtualPlantRuntime(
        make_model(args), speed=args.speed, host=args.host, opcua_port=args.opcua_port,
        modbus_port=args.modbus_port, status_port=args.status_port or None, update_ms=args.update_ms,
        pulse_high_ms=args.pulse_ms, pulse_low_ms=args.pulse_ms,
        control_port=(args.control_port or None) if not args.scenario else None,
        control_host=args.control_host, control_token=os.environ.get("VP_CONTROL_TOKEN", ""),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    paused = bool(args.scenario) and args.scenario_start == "subscribe"
    await rt.start(paused=paused)
    print(f"Planta virtual no ar — OPC UA {rt.opcua_endpoint} | Modbus {args.host}:{args.modbus_port} | "
          f"estado http://{args.host}:{args.status_port}/state | seed={args.seed} speed={args.speed}x",
          flush=True)
    if args.speed > 1 and args.wear_repair == "auto" and not args.scenario:
        print(f"Aviso: a {args.speed:g}× um reparo automático de 40 min dura {40 * 60 / args.speed:.0f} s reais — o "
              "servidor vê micro-paradas, não falhas. Para o ciclo de manutenção use --wear-repair manual.", flush=True)
    if rt._control_runner is not None:
        print(f"Manutenção: http://{args.control_host}:{args.control_port}/status "
              f"(POST /replace /repair /force-failure; reparo {args.wear_repair})", flush=True)
    if args.scenario:
        info = rt.model.snapshot()["scenario"]
        print(f"Cenário {info['id']} na máquina {info['machine']}: {info['duration_s'] / 3600:.2f} h, relógio "
              f"{info['clock']} {info['device_start']} → {info['device_end']} "
              f"(gateway: VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off)", flush=True)
    if paused:
        print("Aguardando o gateway assinar as tags OPC UA para iniciar o playback ...", flush=True)
        waiter = asyncio.create_task(rt.wait_for_subscribers())
        done, _ = await asyncio.wait({waiter, asyncio.create_task(stop.wait())},
                                     return_when=asyncio.FIRST_COMPLETED)
        if waiter in done:
            rt.resume()
            print(f"Playback iniciado ({rt.monitored_items()} itens assinados).", flush=True)
    await stop.wait()
    await rt.stop()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("asyncua", "pymodbus"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.write_map:
        shifts = None
        if args.scenario:      # calendário do servidor = turno do cenário (mesmas pausas planejadas)
            from .scenario import Scenario
            shifts = Scenario.load(args.scenario).shift_defs()
        with open(args.write_map, "w", encoding="utf-8") as f:
            json.dump(build_plant_map(shifts=shifts), f, ensure_ascii=False, indent=2)
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
