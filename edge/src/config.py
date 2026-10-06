from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env (dev convenience). Resolved from this file, not the CWD, so the
# gateway loads the same file whether it is started from the repo root or edge/src.
# In Docker (/app/src/config.py) this points at a file that does not exist and is skipped.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class EdgeSettings(BaseSettings):
    # The root .env is shared with the server: ignore keys that belong to it
    # (CORS_ORIGINS, POSTGRES_*, ...) instead of crashing on extra_forbidden.
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore")

    tenant_id: str = ""          # UUID do tenant no servidor (obrigatório em produção)

    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: Optional[int] = None   # vazio = 8883 com TLS, 1883 sem
    # Per-gateway MQTT user (docs/gateways.md): env wins; empty = the user/password received at enrollment.
    mqtt_username: str = ""
    mqtt_password: str = ""
    # TLS to the broker (production: port 8883 — docs/operations.md §MQTT). MQTT_CA_CERT = path to a CA
    # bundle (PEM); empty = the system CAs. The broker certificate and host name are always verified.
    # MQTT_CLIENT_CERT + MQTT_CLIENT_KEY (PEM): mutual TLS (optional).
    mqtt_tls: bool = False
    mqtt_ca_cert: str = ""
    mqtt_client_cert: str = ""
    mqtt_client_key: str = ""

    opcua_endpoint: str = "opc.tcp://localhost:4840"

    modbus_host: str = ""
    modbus_port: int = 502
    modbus_tags: list = []

    buffer_db_path: str = "/tmp/edge_buffer.db"
    deadband_pct: float = 0.5   # ignore changes < 0.5%
    # Heartbeat: re-publish an unchanged value after this many seconds of silence.
    # Must stay well below the server's MACHINE_OFFLINE_TIMEOUT_S (60 s), otherwise
    # a healthy gateway reading stable values gets its machine marked offline.
    deadband_max_silence_s: float = 30.0

    # Timestamp policy for tags outside a plant map (legacy MODBUS_TAGS path); plant-map sources
    # carry their own `timestamps` / `max_clock_skew_s` (edge/src/clock.py). "gateway" | "source".
    timestamp_policy: str = "gateway"
    # Skew (device ts − gateway receive time) above which a device clock is not trusted:
    # a "source" device is demoted to gateway time, a "gateway" device is only warned about.
    max_clock_skew_s: float = 2.0

    encryption_key: str = ""    # 32-byte AES-256 key

    # Mapa de dispositivos/tags (edge/src/plant_map.py). Quando definido, substitui MODBUS_HOST/MODBUS_TAGS.
    plant_map_path: str = ""
    gateway_id: str = ""        # sobrescreve o gateway_id do mapa
    counter_db_path: str = "/tmp/edge_counters.db"   # contadores de borda (cycle_pulse), persistentes
    pulse_poll_ms: int = 0      # 0 = usa o pulse_poll_ms do mapa (padrão 20 ms)
    heartbeat_interval_s: float = 0.0   # 0 = usa o heartbeat_s do mapa (padrão 10 s)

    # Configuração remota (edge/src/remote_config.py). Vazio = mapa do arquivo PLANT_MAP_PATH (padrão).
    gateway_config_url: str = ""        # URL base do servidor, URL completa ou modelo com {gateway_id}
    gateway_token: str = ""             # Authorization: Gateway <token> (segredo: só no .env do gateway)
    gateway_config_cache: str = "/tmp/edge_gateway_config.json"   # cópia local para partir sem servidor
    gateway_config_timeout_s: float = 10.0

    # Gestão de frota (Sprint 5, edge/src/enrollment.py e fleet.py). SERVER_URL = URL base do servidor; com
    # ENROLL_CODE o gateway se cadastra na 1ª partida e grava gateway_id/token/MQTT em GATEWAY_CREDENTIALS_PATH
    # (0600). Com credencial (ou GATEWAY_TOKEN), a configuração vem do servidor e o heartbeat HTTP vai a cada
    # GATEWAY_HEARTBEAT_S; o mapa é recarregado a quente quando o servidor avisa config_changed (e a cada
    # GATEWAY_CONFIG_POLL_S, por garantia).
    server_url: str = ""
    enroll_code: str = ""
    gateway_credentials_path: str = ""   # vazio = /tmp/edge_gateway_credentials.json (imagem: /data/...)
    gateway_heartbeat_s: float = 60.0
    gateway_config_poll_s: float = 300.0
    allow_insecure_http: bool = False    # só rede de testes: permite SERVER_URL http:// fora de localhost
    # Arquivo tocado a cada heartbeat (HEALTHCHECK da imagem/systemd watchdog); vazio = desligado
    health_file: str = ""

    @field_validator("mqtt_broker_port", mode="before")
    @classmethod
    def _empty_port(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


settings = EdgeSettings()
