"""
Factory Monitor — Roda o seed no VPS via SSH.
Copia seed_dev.py para dentro do container e executa.

Uso:
    VPS_PASS=suasenha python3 scripts/seed_remote.py
"""

import os
import sys
import getpass
import paramiko

HOST    = "187.77.144.79"
USER    = "root"
APP     = "/opt/factory-monitor"
COMPOSE = f"docker compose -f {APP}/infra/docker-compose.prod.yml"

SEED_LOCAL  = os.path.join(os.path.dirname(__file__), "seed_dev.py")
SEED_REMOTE = "/tmp/seed_dev.py"
SEED_CONTAINER = "/app/seed_dev.py"


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    out.channel.recv_exit_status()
    output = out.read().decode().strip()
    errors = err.read().decode().strip()
    combined = "\n".join(filter(None, [output, errors]))
    if combined:
        for line in combined.splitlines():
            print(f"  {line}")
    return output


def main():
    password = os.environ.get("VPS_PASS", "")
    if not password:
        password = getpass.getpass(f"Senha SSH ({USER}@{HOST}): ")

    print(f"\n▶ Conectando a {USER}@{HOST}...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=password, timeout=15)
    print("  ✓ Conectado")

    # Copia seed_dev.py para o VPS
    print("\n▶ Enviando seed_dev.py para o VPS...")
    sftp = c.open_sftp()
    sftp.put(SEED_LOCAL, SEED_REMOTE)
    sftp.close()
    print(f"  ✓ {SEED_REMOTE}")

    # Copia para dentro do container
    print("\n▶ Copiando para dentro do container fm_server...")
    run(c, f"docker cp {SEED_REMOTE} fm_server:{SEED_CONTAINER}")
    print(f"  ✓ {SEED_CONTAINER}")

    # Executa dentro do container (sys.path já aponta para /app)
    print("\n▶ Executando seed...")
    run(c, (
        f"docker exec fm_server python -c \""
        f"import sys; sys.path.insert(0, '/app'); "
        f"import importlib.util, os; "
        f"spec = importlib.util.spec_from_file_location('seed', '{SEED_CONTAINER}'); "
        f"m = importlib.util.module_from_spec(spec); "
        f"spec.loader.exec_module(m); "
        f"import asyncio; asyncio.run(m.seed())\""
    ), timeout=60)

    # Limpa
    run(c, f"docker exec fm_server rm -f {SEED_CONTAINER}", timeout=10)
    c.close()

    print("\n✓ Seed concluído!")
    print("  Superadmin: superadmin@factory-monitor.com / Super@1234")
    print("  Acesse /admin após login.\n")


if __name__ == "__main__":
    main()
