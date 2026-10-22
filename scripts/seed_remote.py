"""
Factory Monitor — Roda o seed no VPS via SSH.
Copia seed_dev.py para dentro do container e executa.

Host, usuário e credencial vêm do ambiente (nada hardcoded) — veja scripts/vps.py.

Uso:
    export VPS_HOST=<ip-ou-host> VPS_USER=deploy VPS_SSH_KEY=~/.ssh/factoryconnect_deploy
    python3 scripts/seed_remote.py

As senhas do seed vêm de SEED_ADMIN_PASSWORD / SEED_SUPERADMIN_PASS no .env do VPS
(ambiente do container fm_server). O seed roda com SEED_ENFORCE_PROD_CREDENTIALS=1:
se alguma estiver ausente, curta ou for a senha padrão de dev (Admin@1234 /
Super@1234), ele é recusado antes de tocar no banco e este script sai com erro.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vps import VpsConfigError, connect, load_config  # noqa: E402

SEED_LOCAL  = os.path.join(os.path.dirname(__file__), "seed_dev.py")
SEED_REMOTE = "/tmp/seed_dev.py"
SEED_CONTAINER = "/app/seed_dev.py"


class RemoteCommandError(RuntimeError):
    pass


def run(client, cmd: str, timeout: int = 60, check: bool = False) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    status = out.channel.recv_exit_status()
    output = out.read().decode().strip()
    errors = err.read().decode().strip()
    combined = "\n".join(filter(None, [output, errors]))
    if combined:
        for line in combined.splitlines():
            print(f"  {line}")
    if check and status != 0:
        raise RemoteCommandError(f"comando remoto falhou (exit {status})")
    return output


def seed_command() -> str:
    """docker exec that runs seed() with production credential enforcement on."""
    return (
        f"docker exec -e SEED_ENFORCE_PROD_CREDENTIALS=1 fm_server python -c \""
        f"import sys; sys.path.insert(0, '/app'); "
        f"import importlib.util, os; "
        f"spec = importlib.util.spec_from_file_location('seed', '{SEED_CONTAINER}'); "
        f"m = importlib.util.module_from_spec(spec); "
        f"spec.loader.exec_module(m); "
        f"import asyncio; asyncio.run(m.seed())\""
    )


def run_seed(c) -> bool:
    """Copies, runs and cleans up the seed. Returns False if the seed failed/was refused."""
    print("\n▶ Enviando seed_dev.py para o VPS...")
    sftp = c.open_sftp()
    sftp.put(SEED_LOCAL, SEED_REMOTE)
    sftp.close()
    print(f"  ✓ {SEED_REMOTE}")

    print("\n▶ Copiando para dentro do container fm_server...")
    run(c, f"docker cp {SEED_REMOTE} fm_server:{SEED_CONTAINER}", check=True)
    print(f"  ✓ {SEED_CONTAINER}")

    print("\n▶ Executando seed (credenciais de produção obrigatórias)...")
    try:
        run(c, seed_command(), timeout=60, check=True)
        return True
    except RemoteCommandError as exc:
        print(f"  ✗ Seed falhou/recusado: {exc}", file=sys.stderr)
        print("    Defina SEED_ADMIN_PASSWORD e SEED_SUPERADMIN_PASS (fortes, não as de dev) "
              "no .env do VPS e recrie o fm_server.", file=sys.stderr)
        return False
    finally:
        run(c, f"docker exec fm_server rm -f {SEED_CONTAINER}", timeout=10)
        run(c, f"rm -f {SEED_REMOTE}", timeout=10)


def main():
    try:
        cfg = load_config()
    except VpsConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"\n▶ Conectando a {cfg.user}@{cfg.host} ({cfg.auth_method})...")
    c = connect(cfg)
    print("  ✓ Conectado")

    try:
        ok = run_seed(c)
    finally:
        c.close()
    if not ok:
        sys.exit(1)

    print("\n✓ Seed concluído!")
    print("  Credenciais: as definidas em SEED_ADMIN_* / SEED_SUPERADMIN_* no .env do VPS.")
    print("  Acesse /admin após login.\n")


if __name__ == "__main__":
    main()
