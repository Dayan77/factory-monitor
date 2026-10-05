# FactoryConnect (factory-monitor)

Plataforma SaaS multi-tenant de Indústria 4.0. Roadmap e critérios de aceite: `docs/roadmap.md`.
Arquitetura: `docs/architecture.md`.

## Estrutura
- `edge/` — gateway de chão de fábrica (Modbus TCP, OPC UA, MQTT, buffer SQLite) + simulador + app de config (:7001)
- `server/` — FastAPI (REST + WebSocket), PostgreSQL (Alembic), InfluxDB, rules engine, MQTT subscriber
- `web/` — React 18 + TypeScript + Tailwind + Recharts + React Query + Zustand
- `infra/` — docker-compose (dev e prod), nginx, mosquitto, mediamtx
- `scripts/` — deploy e seed

## Rodar localmente (verificado)
```bash
cp .env.example .env            # POSTGRES_PASSWORD=fm_dev_password, INFLUX_TOKEN=dev-token-change-me, sem comentários inline
cd infra && docker compose --env-file ../.env up -d postgres influxdb mosquitto
python -m venv .venv && .venv/bin/pip install -r server/requirements.txt
python -m venv .venv-edge && .venv-edge/bin/pip install -r edge/requirements.txt   # deps conflitam com o server
cd server && set -a && . ../.env && set +a && ../.venv/bin/alembic upgrade head && ../.venv/bin/python ../scripts/seed_dev.py
../.venv/bin/uvicorn src.main:app --port 8000                      # backend
cd web && npm ci && npx vite --port 5173                            # frontend (proxy /api e /ws → :8000)
.venv-edge/bin/python -m edge.src.simulator --server http://localhost:8000 --broker localhost
```
Login: `admin@factory-monitor.com` / `Admin@1234` (superadmin: `superadmin@factory-monitor.com` / `Super@1234`).
**Nunca** rode o simulador sem `--server/--broker`: o padrão aponta para produção.

## Regras do projeto
- Todo dado é multi-tenant: toda query filtra por `tenant_id`. Nenhum endpoint sem teste de isolamento.
- Schema só muda via migration Alembic (nunca editar migration já publicada).
- KPIs seguem definições de norma (OEE: ISO 22400; falhas: ISO 14224) e ficam documentados em `docs/kpis.md`.
- Frontend nunca mostra dado falso sem aviso: sem fallback silencioso para mocks.
- Segredos só em `.env`/secrets do CI. Nunca commitar senha, token ou IP+credencial.
- Toda mudança vem com teste; toda mudança de UI é verificada no navegador (Playwright).
- Deploy em produção só com confirmação explícita do dono do projeto.
- Mensagens de commit em inglês, estilo `feat:/fix:/chore:`; UI e docs em português.

## Time de agentes (`.claude/agents/`)
| Agente | Responsabilidade |
|---|---|
| `edge-engineer` | protocolos industriais, gateway, contadores, planta virtual |
| `backend-engineer` | API, modelos, migrations, Influx, KPIs, rules engine |
| `frontend-engineer` | app web, design system, UX fluida |
| `reliability-engineer` | especialista de domínio: OEE, MTBF/MCBF/MTTR, manutenção, ISO 22400/14224 |
| `qa-engineer` | testes unitários, integração com planta virtual, e2e Playwright |
| `devops-engineer` | Docker, CI/CD, deploy, segurança, observabilidade |
| `code-reviewer` | revisão adversarial de cada entrega antes do merge |

Fluxo de um sprint: o orquestrador (sessão principal) escolhe itens do roadmap →
`reliability-engineer` especifica regras de domínio → engenheiros implementam em paralelo
(áreas que não se sobrepõem) → `qa-engineer` valida na planta virtual → `code-reviewer` revisa →
orquestrador integra, roda tudo localmente e abre o PR.
