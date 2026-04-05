"""
Factory Monitor — Remote Deploy via SSH (password auth)

Uso:
    python scripts/deploy_remote.py
    VPS_PASS=suasenha python scripts/deploy_remote.py
"""

import os
import sys
import time
import getpass
import paramiko

HOST    = "187.77.144.79"
USER    = "root"
APP     = "/opt/factory-monitor"
COMPOSE = f"docker compose -f {APP}/infra/docker-compose.prod.yml"


def connect(password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=password, timeout=15)
    return c


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 300, check: bool = True) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    exit_code = out.channel.recv_exit_status()
    output = out.read().decode().strip()
    errors = err.read().decode().strip()

    if output:
        for line in output.splitlines():
            print(f"  {line}")
    if errors:
        for line in errors.splitlines():
            print(f"  {line}", file=sys.stderr)

    if check and exit_code != 0:
        print(f"\n✗ Comando falhou (exit {exit_code}): {cmd}", file=sys.stderr)
        sys.exit(1)

    return output


def step(label: str):
    print(f"\n▶ {label}...")


def main():
    password = os.environ.get("VPS_PASS", "")
    if not password:
        password = getpass.getpass(f"Senha SSH ({USER}@{HOST}): ")

    print()
    print("╔══════════════════════════════════════════╗")
    print("║  Factory Monitor — Deploy                 ║")
    print("╚══════════════════════════════════════════╝")

    step(f"Conectando a {USER}@{HOST}")
    c = connect(password)
    print("  ✓ Conectado")

    # 1. Git pull
    step("Atualizando código (git pull)")
    run(c, f"cd {APP} && git pull origin main")
    print("  ✓ Código atualizado")

    # 2. Build server + web
    step("Buildando imagens (server + web)")
    run(c, f"cd {APP} && {COMPOSE} build --no-cache server web 2>&1 | tail -20", timeout=600)
    print("  ✓ Build concluído")

    # 3. Migrations
    step("Aplicando migrations do banco (alembic upgrade head)")
    run(c, f"cd {APP} && {COMPOSE} run --rm -T server alembic upgrade head", timeout=60)
    print("  ✓ Migrations aplicadas")

    # 4. Restart
    step("Reiniciando serviços")
    run(c, f"cd {APP} && {COMPOSE} up -d --no-deps --remove-orphans server web")
    time.sleep(5)

    # 5. Status
    step("Status dos containers")
    run(c, 'docker ps --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep fm_', check=False)

    # 6. Logs recentes do server
    step("Logs recentes (fm_server)")
    run(c, "docker logs fm_server --tail 15 2>&1", check=False)

    # 7. Health check
    step("Health check")
    run(c, "curl -sk https://factoryconnect.tech/api/health || curl -sk http://localhost:8000/api/health", check=False)

    c.close()
    print()
    print("╔══════════════════════════════════════════╗")
    print("║  ✓ Deploy concluído!                      ║")
    print("║  https://factoryconnect.tech              ║")
    print("╚══════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
