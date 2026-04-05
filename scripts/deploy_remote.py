"""
Factory Monitor — Remote Deploy Script
Copia arquivos modificados para o VPS e rebuilda os containers necessários.
"""

import os
import sys
import time
import paramiko

HOST = "187.77.144.79"
USER = "root"
APP  = "/opt/factory-monitor"

# Arquivos locais → caminhos remotos
DEPLOY_FILES = {
    "server/src/services/mqtt_subscriber.py": f"{APP}/server/src/services/mqtt_subscriber.py",
    "server/src/services/influx.py":          f"{APP}/server/src/services/influx.py",
    "server/src/main.py":                     f"{APP}/server/src/main.py",
    "server/src/api/routes/sensors.py":       f"{APP}/server/src/api/routes/sensors.py",
    "server/src/websocket/manager.py":        f"{APP}/server/src/websocket/manager.py",
    "server/requirements.txt":                f"{APP}/server/requirements.txt",
}

LOCAL_ROOT = os.path.join(os.path.dirname(__file__), "..")


def get_password() -> str:
    passwd = os.environ.get("VPS_PASS", "")
    if not passwd:
        import getpass
        passwd = getpass.getpass(f"▶ Senha SSH do VPS ({USER}@{HOST}): ")
    return passwd


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 300) -> str:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    output = out.read().decode().strip()
    errors = err.read().decode().strip()
    if output:
        print(output)
    if errors:
        print(errors)
    return output


def main():
    password = get_password()

    print(f"\n▶ Conectando a {USER}@{HOST}...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=password, timeout=15)
    print("  ✓ Conectado")

    # Git pull no VPS
    print("\n▶ Atualizando código no VPS via git pull...")
    run(c, f"cd {APP} && git fetch origin && git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)")
    print("  ✓ Código atualizado")

    # Rebuild server
    print("\n▶ Rebuilding servidor (pode demorar ~1min)...")
    run(c, f"cd {APP} && docker build -t factory-monitor-server:latest ./server 2>&1 | tail -8", timeout=300)
    print("  ✓ Build concluído")

    # Restart
    print("\n▶ Reiniciando servidor...")
    run(c, f"cd {APP} && docker compose -f infra/docker-compose.prod.yml up -d --no-deps server")
    time.sleep(4)

    # Status
    print("\n▶ Status:")
    run(c, 'docker ps --format "  {{.Names}}\t{{.Status}}" | grep fm_')

    # Logs
    print("\n▶ Logs do servidor:")
    run(c, "docker logs fm_server --tail 10 2>&1")

    # Health check
    print("\n▶ Health check...")
    run(c, "curl -sk https://factoryconnect.tech/api/health")

    c.close()
    print("\n╔══════════════════════════════════════════╗")
    print("║  ✓ Deploy concluído!                      ║")
    print("║  https://factoryconnect.tech              ║")
    print("╚══════════════════════════════════════════╝\n")


if __name__ == "__main__":
    main()
