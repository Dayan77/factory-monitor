"""Public demo tenant CLI (docs/sprint6.md §5).

    cd server && python -m src.demo bootstrap [--map ../edge/config/plant.json] [--rotate-gateway]
    cd server && python -m src.demo bot-once          # one pass of demo-bot (normally a background task)
    cd server && python -m src.demo status

`bootstrap` is idempotent. The gateway token and MQTT password of the demo gateway are printed ONCE (only their
hashes are stored); put them in the secrets of the virtual-plant gateway container (never in the repository).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _bootstrap(args) -> int:
    from .db import AsyncSessionLocal
    from .services.demo.bootstrap import BootstrapError, bootstrap, load_map
    try:
        doc = load_map(args.map)
        async with AsyncSessionLocal() as db:
            out = await bootstrap(db, doc, rotate_gateway=args.rotate_gateway)
    except BootstrapError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    creds = out.pop("gateway_credentials")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if creds:
        print("\n=== Credenciais do gateway da demo (mostradas UMA vez; guarde nos secrets do gateway) ===")
        print(json.dumps(creds, ensure_ascii=False, indent=2))
        print("Broker de produção: adicione a linha gateways_conf_line ao gateways.conf e recarregue "
              "(make mqtt-reload).")
    else:
        print("\nGateway já cadastrado: credenciais não são mostradas de novo (use --rotate-gateway para gerar novas).")
    return 0


async def _bot_once(_args) -> int:
    from .db import AsyncSessionLocal
    from .services.demo.bot import run_once
    async with AsyncSessionLocal() as db:
        done = await run_once(db)
    print(json.dumps(done, ensure_ascii=False, indent=2))
    return 0


async def _status(_args) -> int:
    from .db import AsyncSessionLocal
    from .services.demo.session import demo_status
    async with AsyncSessionLocal() as db:
        print(json.dumps(await demo_status(db), ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.demo", description="Tenant DEMO (somente leitura)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="cria/completa o tenant demo a partir do mapa da planta virtual")
    b.add_argument("--map", default=None, help="mapa v1 da planta virtual (padrão: edge/config/plant.json)")
    b.add_argument("--rotate-gateway", action="store_true", help="gera novo token/senha MQTT do gateway da demo")
    sub.add_parser("bot-once", help="uma passada do demo-bot")
    sub.add_parser("status", help="estado da demo (planta virtual parada?)")
    args = ap.parse_args(argv)
    fn = {"bootstrap": _bootstrap, "bot-once": _bot_once, "status": _status}[args.cmd]
    return asyncio.run(fn(args))


if __name__ == "__main__":
    sys.exit(main())
