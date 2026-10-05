from pathlib import Path

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
    mqtt_broker_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    # TLS to the broker (production: port 8883 — docs/operations.md §MQTT). MQTT_CA_CERT = path to a CA
    # bundle (PEM); empty = the system CAs. The broker certificate and host name are always verified.
    mqtt_tls: bool = False
    mqtt_ca_cert: str = ""

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


settings = EdgeSettings()
