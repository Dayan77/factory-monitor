-- Factory Monitor — Initial Schema
-- Multi-tenant isolation via tenant_id on all tables

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Tenants ────────────────────────────────────────────────────────
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Users & RBAC ──────────────────────────────────────────────────
CREATE TYPE user_role AS ENUM ('operator', 'manager', 'admin', 'superadmin');

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           VARCHAR(255) NOT NULL,
    password_hash   TEXT NOT NULL,
    name            VARCHAR(255) NOT NULL,
    role            user_role NOT NULL DEFAULT 'operator',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, email)
);

-- ── Factory Lines ─────────────────────────────────────────────────
CREATE TABLE lines (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    location    VARCHAR(255),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Machines ──────────────────────────────────────────────────────
CREATE TYPE machine_status AS ENUM ('running', 'stopped', 'maintenance', 'error', 'unknown');

CREATE TABLE machines (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    line_id         UUID NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    model           VARCHAR(255),
    serial_number   VARCHAR(255),
    status          machine_status NOT NULL DEFAULT 'unknown',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Sensor/Tag Mapping ────────────────────────────────────────────
CREATE TYPE protocol_type AS ENUM ('modbus_tcp', 'opc_ua', 'mqtt', 'http');
CREATE TYPE data_type AS ENUM ('float', 'int', 'bool', 'string');

CREATE TABLE tags (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    machine_id      UUID REFERENCES machines(id) ON DELETE SET NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    protocol        protocol_type NOT NULL,
    address         VARCHAR(512) NOT NULL,   -- e.g. "40001" for Modbus, node ID for OPC UA
    data_type       data_type NOT NULL DEFAULT 'float',
    unit            VARCHAR(50),             -- e.g. "°C", "RPM", "bar"
    scale_factor    FLOAT NOT NULL DEFAULT 1.0,
    offset          FLOAT NOT NULL DEFAULT 0.0,
    min_value       FLOAT,
    max_value       FLOAT,
    poll_interval_ms INT NOT NULL DEFAULT 1000,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Alert Rules ───────────────────────────────────────────────────
CREATE TYPE alert_condition AS ENUM ('gt', 'gte', 'lt', 'lte', 'eq', 'neq', 'change');
CREATE TYPE alert_severity AS ENUM ('info', 'warning', 'critical');

CREATE TABLE alert_rules (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tag_id          UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    condition       alert_condition NOT NULL,
    threshold       FLOAT,
    severity        alert_severity NOT NULL DEFAULT 'warning',
    cooldown_minutes INT NOT NULL DEFAULT 5,
    notify_email    BOOLEAN NOT NULL DEFAULT TRUE,
    notify_telegram BOOLEAN NOT NULL DEFAULT FALSE,
    notify_whatsapp BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Alert Events ──────────────────────────────────────────────────
CREATE TABLE alert_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rule_id         UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    value_at_trigger FLOAT,
    notified        BOOLEAN NOT NULL DEFAULT FALSE
);

-- ── Indexes ───────────────────────────────────────────────────────
CREATE INDEX idx_users_tenant ON users(tenant_id);
CREATE INDEX idx_lines_tenant ON lines(tenant_id);
CREATE INDEX idx_machines_tenant_line ON machines(tenant_id, line_id);
CREATE INDEX idx_tags_tenant_machine ON tags(tenant_id, machine_id);
CREATE INDEX idx_alert_rules_tenant_tag ON alert_rules(tenant_id, tag_id);
CREATE INDEX idx_alert_events_tenant ON alert_events(tenant_id, triggered_at DESC);
