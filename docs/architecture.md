# Factory Monitor — Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLOUD / SERVER                         │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │  FastAPI     │   │  PostgreSQL  │   │     InfluxDB       │  │
│  │  (REST + WS) │   │  (config,    │   │  (time-series      │  │
│  │              │   │   users,RBAC)│   │   sensor data)     │  │
│  └──────┬───────┘   └──────────────┘   └────────────────────┘  │
│         │                                                       │
│  ┌──────┴───────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │  Rules       │   │  AI / LLM    │   │  Notifications     │  │
│  │  Engine      │   │  (LangChain) │   │  (Email/TG/WA)     │  │
│  └──────────────┘   └──────────────┘   └────────────────────┘  │
│                              │                                  │
│              ┌───────────────┴──────────────┐                  │
│              │       MQTT Broker             │                  │
│              │       (Mosquitto)             │                  │
│              └───────────────┬──────────────┘                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │ TLS + AES-256
┌──────────────────────────────┼──────────────────────────────────┐
│                         EDGE (Chão de Fábrica)                  │
│                              │                                  │
│              ┌───────────────┴──────────────┐                  │
│              │       Edge Gateway            │                  │
│              │   - Deadband filter           │                  │
│              │   - SQLite buffer (offline)   │                  │
│              └──┬─────────┬──────────┬──────┘                  │
│                 │         │          │                          │
│            Modbus    OPC UA       MQTT                          │
│            TCP       Client      Subscribe                      │
│                 │         │          │                          │
│              PLCs      SCADA     Sensors/IoT                    │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **Edge Gateway** polls PLCs/sensors via Modbus/OPC UA or receives via MQTT.
2. **Deadband filter** suppresses readings below noise threshold.
3. Readings are **AES-256 encrypted** and published to MQTT broker over TLS.
4. **FastAPI** consumes from MQTT, writes to InfluxDB (time-series) and evaluates rules.
5. **Rules Engine** triggers notifications if thresholds are breached.
6. **WebSocket** broadcasts live readings to all subscribed dashboard clients of the same tenant.
7. **AI Chat** queries InfluxDB + PostgreSQL context via LangChain to answer natural language questions.

## Multi-Tenancy

Every DB row carries `tenant_id`. JWT tokens embed `tenant_id` and `role`.
All query layers filter by `tenant_id` automatically — no cross-tenant data leakage.

## RBAC Roles

| Role        | Capabilities                                      |
|-------------|---------------------------------------------------|
| operator    | View dashboards, receive alerts                   |
| manager     | + Create/edit alert rules, export reports         |
| admin       | + Manage users, configure lines/machines/tags     |
| superadmin  | + Manage tenants (internal use only)              |

## Phase Roadmap

| Phase | Scope                                                     | Status     |
|-------|-----------------------------------------------------------|------------|
| 1     | Modbus/OPC UA + Dashboards + Email alerts                 | In Progress|
| 2     | AI Chat, custom reports, WhatsApp/Telegram                | Planned    |
| 3     | ML predictive maintenance, anomaly detection              | Planned    |
