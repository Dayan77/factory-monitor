"""
Factory Monitor — Deploy remoto via SSH (fallback manual do workflow .github/workflows/deploy.yml).

Host, usuário e credencial vêm do ambiente (nada hardcoded) — veja scripts/vps.py.

Uso:
    export VPS_HOST=<ip-ou-host> VPS_USER=deploy VPS_SSH_KEY=~/.ssh/factoryconnect_deploy
    python scripts/deploy_remote.py

Deploy em produção só com confirmação explícita do dono do projeto.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vps import VpsConfigError, connect, load_config  # noqa: E402


def run(client, cmd: str, timeout: int = 300, check: bool = True) -> str:
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
    try:
        cfg = load_config()
    except VpsConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(2)
    APP, COMPOSE = cfg.app_dir, cfg.compose

    print()
    print("╔══════════════════════════════════════════╗")
    print("║  Factory Monitor — Deploy                 ║")
    print("╚══════════════════════════════════════════╝")

    step(f"Conectando a {cfg.user}@{cfg.host} ({cfg.auth_method})")
    c = connect(cfg)
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
    healthy = False
    for _ in range(12):
        _, out, _ = c.exec_command(f"curl -fsS --max-time 5 {cfg.health_url}", timeout=15)
        if out.channel.recv_exit_status() == 0:
            print(f"  {out.read().decode().strip()}")
            healthy = True
            break
        time.sleep(5)
    if not healthy:
        print(f"\n✗ Healthcheck falhou: {cfg.health_url}", file=sys.stderr)
        run(c, "docker logs fm_server --tail 50 2>&1", check=False)
        c.close()
        sys.exit(1)

    c.close()
    print()
    print("╔══════════════════════════════════════════╗")
    print("║  ✓ Deploy concluído!                      ║")
    print("╚══════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
