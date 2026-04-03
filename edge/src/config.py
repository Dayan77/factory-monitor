from pydantic_settings import BaseSettings


class EdgeSettings(BaseSettings):
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

    encryption_key: str = ""    # 32-byte AES-256 key

    class Config:
        env_file = "../../.env"


settings = EdgeSettings()
