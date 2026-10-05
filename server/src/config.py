from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

# Public site + app (domain decision: site at factoryconnect.tech, app at app.factoryconnect.tech).
# Always allowed by CORS, whatever CORS_ORIGINS says.
PUBLIC_ORIGINS = (
    "https://factoryconnect.tech",
    "https://www.factoryconnect.tech",
    "https://app.factoryconnect.tech",
)


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
    # AES-256-GCM payload envelope shared with the edge (services/payload_crypto.py). Empty = plaintext.
    encryption_key: str = ""
    mqtt_require_encryption: bool = False

    mediamtx_api_url: str = "http://fm_mediamtx:9997"
    mediamtx_hls_url: str = "http://fm_mediamtx:8888"

    openai_api_key: str = ""

    # Machine status derived from data: a machine with no reading for longer than
    # this is marked 'unknown' (offline). The sweeper runs every N seconds.
    machine_offline_timeout_s: int = 60
    machine_status_sweep_interval_s: int = 15
    # Minimum interval between persisted last_seen_at updates (limits DB writes).
    machine_last_seen_write_interval_s: int = 5

    # Usage processing (domain.md §3.3–3.4)
    # Fallback max_rate (counts/s) of the counter plausibility filter when neither the tag
    # (max_rate_per_s) nor the machine (ideal_cycle_time_s) define one.
    usage_default_max_rate_per_s: float = 10.0
    # A run_state 0 shorter than this does not create a new start.
    run_state_debounce_s: float = 1.0
    # MQTT hot path: tag/topology lookups are cached this long (in-process; cleared on any committed
    # asset change in this process). 0 disables the cache.
    usage_topology_cache_ttl_s: float = 10.0
    # Recompute (POST /api/usage/recompute) reads history from Influx one tag × one window at a
    # time, so memory is bounded by a window, not by the size of the history.
    usage_recompute_window_h: float = 24.0
    # Max time one reading may spend in usage processing, incl. waiting for a recompute(apply)
    # of its tenant to commit (usage/locks.py). A reading that times out is not counted live.
    usage_process_timeout_s: float = 60.0
    # SQL statement logging (very slow under MQTT load). Was implied by APP_ENV=development.
    sql_echo: bool = False
    # Minimum structlog level (DEBUG logs one line per MQTT reading: costly under load).
    log_level: str = "INFO"
    # Max MQTT readings processed at once. Must stay well below the DB pool (10 + 20 overflow):
    # unbounded handlers took every pooled connection under a backlog and API requests waited
    # for the 30 s pool timeout. Excess readings wait (in arrival order per tag) instead.
    mqtt_max_concurrency: int = 12
    # Payload ts older than this vs. receive time is not sane (PLC/gateway clock unset):
    # the reading is stamped with the receive time instead.
    source_ts_max_age_s: int = 30 * 24 * 3600
    # WebSocket {type:'usage'} messages are coalesced and flushed at most once per interval.
    usage_ws_interval_s: float = 1.0

    # Downtime detection / OEE (domain.md §4.3, kpis.md C22–C24)
    # Stops separated by less than this much running are merged (C24a).
    downtime_merge_gap_s: float = 10.0
    # Extra wait after the merge gap before a stop is final (late readings of another tag).
    downtime_finalize_delay_s: float = 5.0
    # Detector pass interval and per-machine timeout; history read on a machine's first pass.
    downtime_tick_s: float = 10.0
    downtime_machine_timeout_s: float = 30.0
    downtime_initial_lookback_h: float = 24.0
    # Readings may reach InfluxDB out of order (the MQTT handler processes messages concurrently):
    # the checkpoint only passes readings that were already visible this long ago. A reading that
    # arrives later than this behind its own timestamp (gateway buffer flush, replay) is "late" and
    # makes the detector rewind to an older snapshot and re-detect (idempotent).
    downtime_settle_s: float = 30.0
    # Detector snapshots (rewind points): at most one per interval, kept for the horizon. Late data
    # older than the horizon is stored but not re-detected (logged).
    downtime_snapshot_interval_s: float = 900.0
    downtime_rewind_horizon_h: float = 168.0
    # Lifespan shutdown waits at most this long for background / in-flight MQTT tasks.
    shutdown_timeout_s: float = 5.0

    sendgrid_api_key: str = ""
    sendgrid_from_email: str = ""

    # Public lead capture (POST /api/public/leads)
    leads_to_email: str = ""
    leads_rate_limit_per_hour: int = 5
    # Reverse proxies whose X-Forwarded-For / X-Forwarded-Proto are trusted (IPs or CIDRs,
    # comma-separated). The client IP is the right-most X-Forwarded-For entry that is NOT a
    # trusted proxy, so a client-supplied (left-most) value is never used. Never "*": with "*"
    # uvicorn takes the left-most entry, which the client controls (lead rate-limit bypass).
    # Default: loopback + private ranges (docker networks of Traefik/nginx).
    forwarded_allow_ips: str = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    telegram_bot_token: str = ""

    # Web Push (VAPID) — notifications.webpush. Without a private key push is disabled.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""
    # Web Push endpoints are only accepted (and only POSTed to) on these push-service hosts (SSRF guard).
    # Comma-separated; "*.example.com" matches subdomains.
    push_allowed_hosts: str = ("fcm.googleapis.com,android.googleapis.com,updates.push.services.mozilla.com,"
                               "*.push.services.mozilla.com,*.push.apple.com,*.notify.windows.com")
    # Maintenance module (Sprint 4): engine tick (emergency calls, escalation, overdue WOs, digests),
    # full plan recompute interval (also recomputed when usage changes) and webhook delivery tick.
    maintenance_tick_s: float = 30.0
    maintenance_plan_recompute_s: float = 3600.0
    webhook_tick_s: float = 5.0
    webhook_timeout_s: float = 10.0
    # Outbound webhooks to private/loopback addresses are refused (SSRF) unless this is true.
    webhook_allow_private_hosts: bool = False
    # Public URL of the web app (links in e-mails / push).
    app_public_url: str = "https://app.factoryconnect.tech"

    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # Comma-separated. Read from CORS_ORIGINS (the name used in .env.example / .env.prod.example;
    # the old field name CORS_ORIGINS_RAW is still accepted).
    cors_origins_raw: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        validation_alias=AliasChoices("CORS_ORIGINS", "CORS_ORIGINS_RAW", "cors_origins_raw"),
    )

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]
        for o in PUBLIC_ORIGINS:
            if o not in origins:
                origins.append(o)
        return origins

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = "../../.env"


settings = ServerSettings()
