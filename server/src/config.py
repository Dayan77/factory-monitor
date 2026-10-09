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
    # Sprint 7 (docs/performance.md §5): retention + downsampling (services/influx_downsampling.py). The worker
    # process creates <bucket>_1m (1-minute aggregates) and <bucket>_1h (hourly, forever) and two Influx tasks at
    # startup (idempotent) and sets the raw bucket's retention (INFLUX_MANAGE_RETENTION=false leaves it alone).
    influx_downsampling: bool = True
    influx_raw_retention_days: int = 90
    influx_1m_retention_days: int = 730
    influx_manage_retention: bool = True

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

    # AI assistant (Sprint 5 — docs/ai_assistant.md). Provider: openai | anthropic; empty = whichever key is set
    # (ANTHROPIC_API_KEY preferred when both are). AI_MODEL empty = the provider default.
    anthropic_api_key: str = ""
    ai_provider: str = ""
    ai_model: str = ""
    ai_enabled: bool = True
    # tenants with the assistant switched off (comma-separated tenant ids) — AI_ENABLED=false turns it off for all
    ai_disabled_tenants: str = ""
    ai_temperature: float = 0.1                # ≤ 0.2 (IA spec); only sent to models that accept sampling params
    ai_max_tool_calls: int = 6                 # per turn
    ai_turn_timeout_s: float = 20.0
    ai_max_rows: int = 50                      # rows per tool result sent to the model
    ai_rate_limit_per_min: int = 20            # messages per user per minute
    ai_daily_token_budget: int = 2_000_000     # input + output tokens per tenant per day (UTC); 0 = unlimited
    ai_max_output_tokens: int = 4096
    ai_retention_days: int = 90

    # Field gateways (Sprint 5): offline after max(3 × heartbeat_s, this); degraded when the buffer exceeds
    gateway_offline_after_s: float = 90.0
    gateway_degraded_buffer: int = 1000
    gateway_enroll_ttl_h: float = 24.0

    # Machine status derived from data: a machine with no reading for longer than
    # this is marked 'unknown' (offline). The sweeper runs every N seconds.
    machine_offline_timeout_s: int = 60
    # Sprint 7b (kpis.md S7-23): an open failure (no restored_at) older than this is "falha aberta há muito tempo —
    # verificar" on the map/tablet/Raio-X, not a live failure (unless the machine itself reports a fault now)
    failure_stale_open_h: float = 72.0
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
    # Sprint 7 (scale, docs/performance.md): one pipeline turn serves up to this many tags with readings waiting,
    # processed together (one usage transaction per tenant, one machine-status transaction, one Influx request).
    # 1 = one tag per turn (the Sprint 5b behaviour).
    mqtt_keys_per_turn: int = 128
    # Max MQTT readings received and not finished yet (backpressure). When reached, the subscriber stops
    # taking messages: QoS 1 deliveries are acknowledged only when accepted, so the broker stops sending
    # (its max_inflight_messages window) and keeps the backlog in its own bounded queue instead of the
    # server growing one task per reading without limit (Sprint 5 QA: 448k in flight, 1.5 GB RSS).
    mqtt_max_inflight: int = 2000
    # A transient failure (timeout, database/Influx unavailable) on the tag check or the Influx write is
    # retried with backoff for up to this long before the reading is given up (and logged as lost).
    mqtt_transient_retry_s: float = 600.0
    # Persistent MQTT session (clean_session=False) under a stable client id: while the server is down or slow the
    # broker keeps the QoS 1 backlog for it (max_queued_messages) and redelivers what was not acknowledged.
    # Empty = "fm-server-<POSTGRES_DB>" (stable across restarts, distinct per deployment database). Two processes
    # must never share it (the broker disconnects the older one).
    mqtt_client_id: str = ""
    mqtt_persistent_session: bool = True
    # Sprint 7 — where MQTT ingestion runs: inline = in the API process (default, dev/tests); process = N child
    # processes started and supervised by the app (python -m src.ingest_worker, MQTT_INGEST_SHARDS of them);
    # external = run `python -m src.ingest_worker` yourself (one per shard, MQTT_SHARD_INDEX/MQTT_SHARD_COUNT).
    # Each shard processes the tags with crc32(tag_id) % count == index (order per tag kept: a tag has one shard),
    # acknowledges the others at once, and uses its own client id (<client id>-s<index>). Live WebSocket messages
    # reach the app through Postgres NOTIFY (websocket/relay.py, batched).
    mqtt_ingest: str = "inline"
    # Prometheus metrics of the worker processes (they have no HTTP API): ingestion shard k listens on
    # WORKER_METRICS_PORT + k, the detector/predictive worker on WORKER_METRICS_PORT + 100. 0 = off.
    worker_metrics_port: int = 0
    # TTL of the tag/topology/alert-rule caches inside the ingestion processes: changes made by the API process are
    # pushed to them (Postgres NOTIFY, usage/cache.py), so the TTL only bounds a lost notification.
    ingest_cache_ttl_s: float = 300.0
    mqtt_ingest_shards: int = 1
    # SQLAlchemy pool of each process (db.py); the ingestion processes get INGEST_DB_POOL_SIZE (main.py)
    # Sprint 7 (docs/performance.md §6): TTL of the per-user response cache of /api/overview, /api/oee,
    # /api/losses/pareto and /api/maintenance-plans/status (services/response_cache.py). 0 = off.
    api_cache_ttl_s: float = 10.0
    db_pool_size: int = 10
    db_max_overflow: int = 20
    ingest_db_pool_size: int = 24
    mqtt_shard_index: int = 0
    mqtt_shard_count: int = 1
    # Valid readings that still fail after the retries go to the table ingest_dead_letters; if even the database
    # is unavailable, to this JSON-lines file (relative to the working directory).
    ingest_dead_letter_file: str = "data/ingest_dead_letters.jsonl"
    # Per-tag watermarks / late-data marks are written to Postgres at this interval (services/ingest/watermarks.py).
    ingest_watermark_flush_s: float = 1.0
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
    # Sprint 7 — catch-up after long outages (QA: 40 h without data never caught up): a pass reads at most this many
    # readings per tag (a bounded slice that advances the checkpoint); a machine keeps passing slices for at most
    # downtime_catchup_budget_s per tick, then yields to the others (resumes next tick). Detectors run with this many
    # machines in parallel, so one machine with a backlog never delays the other machines / tenants.
    downtime_slice_readings: int = 20000
    downtime_catchup_budget_s: float = 20.0
    detector_concurrency: int = 4
    # Sprint 7 (scale): machines whose checkpoint is within this window share ONE Influx query per tenant and tick
    # (oee/detector.TenantPrefetch); 0 = one query per machine
    downtime_prefetch_window_s: float = 600.0
    # Per-machine detector lag (latest reading seen − checkpoint) above this → log warning + metric alert
    detector_lag_alert_s: float = 900.0
    # Sprint 7: a predictive run stops extracting after this long and commits (resumed next tick) — must stay well
    # below predictive_monitor_timeout_s, which rolls the run back
    predictive_extract_budget_s: float = 60.0
    predictive_monitor_timeout_s: float = 240.0
    # Sprint 7: the downtime detector and the maintenance (plan) engine run in the worker process (src/worker.py)
    # together with the predictive engine whenever PREDICTIVE_WORKER is process/external; false keeps them inline.
    detectors_in_worker: bool = True
    # Sprint 7: CPU priority of the worker process (os.nice increment; 0 = same as the app)
    worker_nice: int = 5
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
    # Predictive module (Sprint 5, docs/predictive.md P-12): pipeline tick (closed windows → detectors → alerts)
    predictive_tick_s: float = 300.0
    predictive_grace_s: float = 120.0          # a window is extracted this long after it closes (late readings)
    predictive_enabled: bool = True
    # Where the predictive engine runs (its Influx extraction must not share the event loop with MQTT ingestion):
    # process  = a child process started and supervised by the app (python -m src.worker) — default;
    # external = not started by the app (run `python -m src.worker` as its own service, e.g. compose `worker`);
    # inline   = background task in the app's event loop (old behaviour; tests/debug only).
    predictive_worker: str = "process"
    webhook_timeout_s: float = 10.0
    # Outbound webhooks to private/loopback addresses are refused (SSRF) unless this is true.
    webhook_allow_private_hosts: bool = False
    # Public URL of the web app (links in e-mails / push).
    app_public_url: str = "https://app.factoryconnect.tech"

    # Sprint 6 (backend A) — tenant file volume (layout images; never in the database): FILES_DIR/<tenant_id>/…
    files_dir: str = "data/files"
    layout_image_max_bytes: int = 10 * 1024 * 1024
    layout_image_max_side_px: int = 8000
    # gateway pulled commands (OPC UA browse, docs/sprint6.md §4.2) expire without an answer
    gateway_command_ttl_s: float = 300.0
    # onboarding step 9: required tags must have data continuously for this long
    readiness_window_min: int = 15
    # public demo (docs/sprint6.md §5): read-only tenant fed by the virtual plant
    # not "demo": scripts/seed_dev.py uses that slug for the dev tenant (SEED_TENANT_SLUG)
    demo_tenant_slug: str = "demo-publica"
    demo_session_hours: float = 2.0
    demo_rate_limit_per_hour: int = 10
    demo_ai_questions_per_session: int = 10
    demo_plant_stopped_after_s: float = 300.0
    # demo-bot (scripted replacement WO when the cylinder alert fires); virtual plant control API (optional)
    demo_bot_enabled: bool = True
    demo_bot_tick_s: float = 60.0
    demo_bot_replace_after_s: float = 900.0
    demo_virtual_plant_control_url: str = ""
    # X-Control-Token of the virtual plant control API (VP_CONTROL_TOKEN of the demo-plant container): required
    # whenever the control API is reached from another host/container (edge/virtual_plant/control.py)
    demo_virtual_plant_control_token: str = ""
    # after the virtual plant refused/failed a /replace, the bot leaves that position alone for this long
    demo_bot_failure_backoff_s: float = 6 * 3600.0

    # Sprint 6 (backend B) — reports (docs/reports.md §4–§5): scheduling engine tick, attachment limit
    # (above it the e-mail carries only a signed link valid 7 days) and catch-up window after downtime
    reports_tick_s: float = 60.0
    reports_enabled: bool = True
    reports_attachment_max_bytes: int = 10 * 1024 * 1024
    reports_catchup_days: int = 7

    jwt_algorithm: str = "HS256"
    # Sprint 7 (backend B, docs/security.md): short access tokens; the refresh token (rotated on every use, reuse
    # = the whole session is revoked) keeps the user signed in.
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    # Key rotation: "kid1:secret1,kid2:secret2" (the active one signs, all verify). Empty = APP_SECRET_KEY (kid k0).
    jwt_keys: str = ""
    jwt_active_kid: str = ""
    jwt_leeway_s: int = 30                     # clock skew tolerated on exp/nbf/iat
    # a refresh token presented again within this window (two tabs refreshing at once) is not treated as theft
    refresh_reuse_grace_s: float = 10.0
    # WebSocket: the session behind the token is re-checked at this interval (revoked → close 4401)
    ws_revalidate_s: float = 60.0
    # Password policy and hashing
    password_min_length: int = 12
    password_history: int = 5
    bcrypt_rounds: int = 12
    password_blocklist_file: str = ""          # optional extra list (one password per line), e.g. from HIBP
    # Brute force: per account / per IP failures within the window → temporary lock with exponential backoff
    login_window_s: float = 900.0
    login_max_failures_account: int = 5
    login_max_failures_ip: int = 20
    login_lock_base_s: float = 30.0
    login_lock_max_s: float = 3600.0
    login_rate_per_ip_per_min: int = 30
    password_reset_ttl_min: int = 15
    password_reset_per_ip_per_hour: int = 10
    password_reset_per_email_per_hour: int = 3
    invite_ttl_hours: float = 168.0
    mfa_issuer: str = "FactoryConnect"
    mfa_encryption_key: str = ""               # empty = derived from APP_SECRET_KEY
    # HTTP hardening
    max_body_bytes: int = 1024 * 1024          # per request; uploads have their own limit (layout_image_max_bytes)
    # LGPD (docs/privacy.md)
    lead_retention_days: int = 365
    audit_retention_days: int = 1825
    privacy_janitor_interval_s: float = 3600.0

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
    def mqtt_client_identifier(self) -> str:
        return self.mqtt_client_id or f"fm-server-{self.postgres_db}"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = "../../.env"


settings = ServerSettings()
