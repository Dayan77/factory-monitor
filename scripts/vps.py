"""
Configuração e conexão SSH com o VPS, compartilhada por deploy_remote.py e seed_remote.py.

Nada de host, usuário ou senha no código: tudo vem do ambiente.

Variáveis:
    VPS_HOST         (obrigatória) IP ou hostname do VPS
    VPS_USER         usuário SSH (padrão: "deploy" — use um usuário dedicado, não root)
    VPS_SSH_KEY      caminho da chave privada (recomendado)
    VPS_PASS         senha SSH (alternativa à chave; evite)
    VPS_PORT         porta SSH (padrão: 22)
    VPS_APP_DIR      diretório do app no VPS (padrão: /opt/factory-monitor)
    VPS_HEALTH_URL   URL do healthcheck executado no VPS (padrão: http://localhost:8000/api/health)
    VPS_ACCEPT_NEW_HOST_KEY=1  aceita (e grava) a host key na primeira conexão;
                     sem isso, o host precisa estar em ~/.ssh/known_hosts.

Sem VPS_SSH_KEY nem VPS_PASS, usa o ssh-agent e as chaves padrão de ~/.ssh.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_USER = "deploy"
DEFAULT_APP_DIR = "/opt/factory-monitor"
DEFAULT_HEALTH_URL = "http://localhost:8000/api/health"


class VpsConfigError(RuntimeError):
    """Configuração do VPS ausente ou inválida."""


@dataclass(frozen=True)
class VpsConfig:
    host: str
    user: str = DEFAULT_USER
    port: int = 22
    key_path: Optional[str] = None
    password: Optional[str] = None
    app_dir: str = DEFAULT_APP_DIR
    health_url: str = DEFAULT_HEALTH_URL
    accept_new_host_key: bool = False

    @property
    def compose(self) -> str:
        return f"docker compose --env-file {self.app_dir}/.env -f {self.app_dir}/infra/docker-compose.prod.yml"

    @property
    def auth_method(self) -> str:
        if self.key_path:
            return "key"
        if self.password:
            return "password"
        return "agent"

    def __repr__(self) -> str:  # nunca vaza a senha em logs/tracebacks
        return (
            f"VpsConfig(host={self.host!r}, user={self.user!r}, port={self.port}, "
            f"auth={self.auth_method}, app_dir={self.app_dir!r})"
        )


def load_config(env: Optional[Mapping[str, str]] = None) -> VpsConfig:
    env = os.environ if env is None else env

    host = env.get("VPS_HOST", "").strip()
    if not host:
        raise VpsConfigError(
            "VPS_HOST não definido. Exporte VPS_HOST (e VPS_USER, VPS_SSH_KEY ou VPS_PASS) "
            "antes de rodar — veja o cabeçalho de scripts/vps.py."
        )

    port_raw = env.get("VPS_PORT", "22").strip() or "22"
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise VpsConfigError(f"VPS_PORT inválida: {port_raw!r}") from exc

    key_path = env.get("VPS_SSH_KEY", "").strip() or None
    if key_path:
        key_path = os.path.expanduser(key_path)
        if not os.path.isfile(key_path):
            raise VpsConfigError(f"VPS_SSH_KEY aponta para um arquivo inexistente: {key_path}")

    return VpsConfig(
        host=host,
        user=env.get("VPS_USER", "").strip() or DEFAULT_USER,
        port=port,
        key_path=key_path,
        password=env.get("VPS_PASS") or None,
        app_dir=(env.get("VPS_APP_DIR", "").strip() or DEFAULT_APP_DIR).rstrip("/"),
        health_url=env.get("VPS_HEALTH_URL", "").strip() or DEFAULT_HEALTH_URL,
        accept_new_host_key=env.get("VPS_ACCEPT_NEW_HOST_KEY", "") in ("1", "true", "yes"),
    )


def connect(cfg: VpsConfig, timeout: int = 15):
    """Abre a conexão SSH. Importa paramiko só aqui (os testes não precisam dele)."""
    import paramiko

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = os.path.expanduser("~/.ssh/known_hosts")
    if os.path.isfile(known_hosts):
        client.load_host_keys(known_hosts)
    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy() if cfg.accept_new_host_key else paramiko.RejectPolicy()
    )

    kwargs = dict(hostname=cfg.host, port=cfg.port, username=cfg.user, timeout=timeout)
    if cfg.key_path:
        kwargs.update(key_filename=cfg.key_path, allow_agent=False, look_for_keys=False)
    elif cfg.password:
        kwargs.update(password=cfg.password, allow_agent=False, look_for_keys=False)
    else:
        kwargs.update(allow_agent=True, look_for_keys=True)

    client.connect(**kwargs)
    if cfg.accept_new_host_key and os.path.isdir(os.path.dirname(known_hosts)):
        client.save_host_keys(known_hosts)
    return client
