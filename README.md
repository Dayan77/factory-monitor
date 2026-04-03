# Factory Monitor

Plataforma Industry 4.0 para monitoramento de chão de fábrica em tempo real.

## Stack

| Camada     | Tecnologia                                              |
|------------|---------------------------------------------------------|
| Edge       | Python 3.12 · OPC UA · Modbus TCP · MQTT · AES-256     |
| Backend    | FastAPI · PostgreSQL · InfluxDB · WebSockets            |
| Frontend   | React 18 · TypeScript · Recharts · Tailwind CSS        |
| IA         | LangChain · OpenAI GPT-4                               |
| Infra      | Docker Compose · Mosquitto · Nginx                     |

## Estrutura

```
factory-monitor/
├── edge/        # Gateway de chão de fábrica (Modbus/OPC UA/MQTT)
├── server/      # API REST + WebSocket (FastAPI)
├── web/         # Dashboard (React + TypeScript)
├── infra/       # Docker Compose, Nginx, schema SQL, configs
└── docs/        # Arquitetura e documentação técnica
```

## Início Rápido

```bash
# 1. Configure variáveis de ambiente
cp .env.example .env
# edite .env com suas credenciais

# 2. Suba toda a stack
cd infra
docker compose up -d

# 3. Acesse
# Dashboard:  http://localhost
# API Docs:   http://localhost/api/docs
# InfluxDB:   http://localhost:8086
```

## Roadmap

- **Fase 1** — MVP: coleta Modbus/OPC UA + Dashboards + Alertas por e-mail
- **Fase 2** — Chat com IA e relatórios customizados
- **Fase 3** — Machine Learning para Manutenção Preditiva

Veja [`docs/architecture.md`](docs/architecture.md) para diagrama completo.
