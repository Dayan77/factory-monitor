from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    app_env: str = "development"
    app_secret_key: str = "change-me"
    app_port: int = 8000

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "factory_monitor"
    postgres_user: str = "fm_user"
    postgres_password: str = ""

    influx_url: str = "http://localhost:8086"
    influx_token: str = ""
    influx_org: str = "factory-monitor"
    influx_bucket: str = "sensors"

    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""

    openai_api_key: str = ""

    sendgrid_api_key: str = ""
    sendgrid_from_email: str = ""
    telegram_bot_token: str = ""

    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # Stored as comma-separated string; parsed into list via property
    cors_origins_raw: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = "../../.env"


settings = ServerSettings()
