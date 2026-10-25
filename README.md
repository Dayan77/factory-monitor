# FactoryConnect

[![CI](https://github.com/Dayan77/factory-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/Dayan77/factory-monitor/actions/workflows/ci.yml)
[![Security](https://github.com/Dayan77/factory-monitor/actions/workflows/security.yml/badge.svg)](https://github.com/Dayan77/factory-monitor/actions/workflows/security.yml)

Plataforma SaaS multi-tenant de Indústria 4.0: conecta CLPs e linhas de produção, coleta os dados fundamentais e
entrega o **raio-x da fábrica** para produção e manutenção — OEE (ISO 22400), paradas e perdas, confiabilidade
(MTBF/MCBF/MTTR, ISO 14224), uso de cada componente, planos e ordens de manutenção, preditiva e um assistente de IA
que consulta os dados reais.

- **Site**: https://factoryconnect.tech · **App**: https://app.factoryconnect.tech · versão atual: [`VERSION`](VERSION)
  ([CHANGELOG](CHANGELOG.md))
- Arquitetura: [docs/architecture.md](docs/architecture.md) · roadmap: [docs/roadmap.md](docs/roadmap.md) ·
  operação: [docs/operations.md](docs/operations.md) · go-live: [docs/go_live.md](docs/go_live.md) ·
  manuais por papel: [docs/manual/](docs/manual/README.md)

## O que tem

| Área | Funcionalidades |
|---|---|
| Coleta | Gateway de borda (IPC x86-64 ou Raspberry Pi) com Modbus TCP, OPC UA e MQTT; contagem de pulsos na borda; buffer offline; cadastro por código e configuração pela nuvem; credencial MQTT e TLS por gateway |
| Ativos e uso | Planta → Linha → Máquina → Componente; vida nominal (ciclos/horas/partidas) e % consumida; acumuladores com tratamento de reset do CLP |
| Eficiência | OEE por máquina/linha/turno, paradas e micro-paradas automáticas com motivo do operador, Pareto, calendário de turnos |
| Manutenção | Planos por ciclos/horas/dias, OS preventiva/corretiva/preditiva, falhas ISO 14224, MTBF/MCBF/MTTR, integração CMMS/ERP (webhooks + REST) |
| Preditiva e IA | Desvio de tempo de curso/corrente/vibração/temperatura, previsão da data de troca, chat de IA com ferramentas sobre KPIs/histórico/OS |
| UX | Mapa da planta ao vivo, drill-down com KPIs, PWA do operador e do técnico, relatórios PDF/XLSX agendados, onboarding guiado, demo pública |
| Plataforma | Multi-tenant com isolamento testado em todo endpoint, RBAC, MFA, LGPD (exportação/anonimização), auditoria, observabilidade com alertas por e-mail |

## Estrutura

```
edge/      gateway de borda (Modbus TCP, OPC UA, MQTT, buffer SQLite) + planta virtual + app de config (:7001)
server/    FastAPI (REST + WebSocket), ingestão MQTT, worker (paradas, preditiva, planos), Alembic, InfluxDB
web/       app React 18 + TypeScript + Tailwind + Recharts + React Query + Zustand (PWA)
site/      site público (Astro estático)
infra/     docker-compose (dev, staging, prod), nginx, Mosquitto, MediaMTX, Prometheus/Alertmanager/Grafana
scripts/   deploy, backup/restore, seed, credenciais MQTT, portões de segurança e de release
docs/      arquitetura, domínio, KPIs, operação, segurança, privacidade, manuais
```

## Início rápido (dev)

Pré-requisitos: Docker + Compose v2, GNU Make; para rodar fora do Docker, Python 3.12 e Node 22.

```bash
make env && make venv        # .env a partir do exemplo; .venv (server) e .venv-edge (edge)
make dev                     # stack completa em Docker → http://localhost   (API: http://localhost/api/docs)
make migrate && make seed    # schema + usuários de dev
.venv-edge/bin/python -m edge.src.simulator --server http://localhost:8000 --broker localhost   # planta simulada
```

Login de **desenvolvimento**: `admin@factory-monitor.com` / `Admin@1234` (superadmin:
`superadmin@factory-monitor.com` / `Super@1234`). Essas senhas são públicas: o seed **recusa** usá-las em produção
(primeiro acesso em produção: [docs/go_live.md §6](docs/go_live.md)).

Backend e frontend fora do Docker (infra em Docker):

```bash
make infra                   # postgres, influxdb, mosquitto, mediamtx
(cd server && set -a && . ../.env && set +a && ../.venv/bin/uvicorn src.main:app --port 8000)
(cd web && npx vite --port 5173)                     # proxy /api e /ws → :8000
```

Nunca rode o simulador sem `--server/--broker` apontando para o seu ambiente. `make help` lista todos os alvos.

## Testes e verificações

```bash
make test                    # server (pytest, banco efêmero) + edge + scripts + golden + web (vitest)
make web-check               # tsc --noEmit + vite build
make test-infra              # compose, mosquitto prod, Traefik, monitoring, cabeçalhos de segurança (docker)
make security                # gitleaks (histórico completo), pip-audit, npm audit, cabeçalhos/CSP no Chromium
make release-check           # VERSION (SemVer) consistente com o CHANGELOG
node e2e/fase0.test.mjs      # e2e com a stack rodando
```

## CI/CD

| Workflow | Quando | O que faz |
|---|---|---|
| **CI** (`.github/workflows/ci.yml`) | todo push/PR | pytest (server com Postgres/Influx/Mosquitto, edge, scripts, golden), tsc + vitest + build (web, site), e2e do site, cabeçalhos/CSP com nginx real + Chromium, compose/nginx/shellcheck/actionlint, build das imagens + **Trivy** + SBOM |
| **Security** (`security.yml`) | todo push/PR + semanal | gitleaks no histórico completo, pip-audit e npm audit com lista de aceites com prazo, SBOM das dependências |
| **Release** (`release.yml`) | tag `vX.Y.Z` | confere VERSION/CHANGELOG, build multi-arquitetura, Trivy antes de publicar, SBOM, push no GHCR com proveniência SLSA, GitHub Release |
| **Deploy** (`deploy.yml`) | manual | versão específica → staging ou produção (produção **só com aprovação do dono do projeto**); backup antes das migrations, timeout, healthcheck e rollback automático |

Dependabot (`.github/dependabot.yml`) mantém pip, npm, imagens Docker e Actions atualizados. Segredos ficam só
no `.env` do servidor e nos secrets dos environments do GitHub — nunca no repositório.

## Contato

Formulário em https://factoryconnect.tech/contato.
