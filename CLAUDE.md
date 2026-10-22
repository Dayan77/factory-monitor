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
make env && make venv          # .env a partir do exemplo; .venv (server) e .venv-edge (edge)
make dev                       # stack completa via docker → http://localhost   (ou: make infra + backend/frontend locais)
make migrate && make seed
make test                      # server + edge + scripts + web; e2e: node e2e/fase0.test.mjs (stack rodando)
.venv-edge/bin/python -m edge.src.simulator   # planta simulada (padrão: localhost)
```
Backend local: `cd server && set -a && . ../.env && set +a && ../.venv/bin/uvicorn src.main:app --port 8000`;
frontend local: `cd web && npx vite --port 5173` (proxy /api e /ws → :8000). `make help` lista todos os alvos.
Login: `admin@factory-monitor.com` / `Admin@1234` (superadmin: `superadmin@factory-monitor.com` / `Super@1234`).
WebSocket `/ws/sensors/{tenant_id}` exige token (subprotocolo `bearer, <jwt>` ou `?token=`).

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
