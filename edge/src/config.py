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

    encryption_key: str = ""    # 32-byte AES-256 key


settings = EdgeSettings()
