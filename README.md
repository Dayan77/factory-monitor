# Factory Monitor

Plataforma Industry 4.0 para monitoramento de chão de fábrica em tempo real.

## Stack

| Camada     | Tecnologia                                              |
|------------|---------------------------------------------------------|
| Edge       | Python 3.12 · OPC UA · Modbus TCP · MQTT · AES-256     |
| Backend    | FastAPI · PostgreSQL · InfluxDB · WebSockets            |
| Frontend   | React 18 · TypeScript · Recharts · Tailwind CSS        |
| IA         | LangChain · OpenAI GPT-4                               |
| Infra      | Docker Compose · Mosquitto · MediaMTX · Nginx · GitHub Actions |

## Estrutura

```
factory-monitor/
├── edge/        # Gateway de chão de fábrica (Modbus/OPC UA/MQTT)
├── server/      # API REST + WebSocket (FastAPI)
├── web/         # Dashboard (React + TypeScript)
├── infra/       # Docker Compose, Nginx, Mosquitto, MediaMTX, configs
├── scripts/     # deploy, seed e testes dos scripts de operação
├── .github/     # CI e deploy (GitHub Actions)
└── docs/        # Arquitetura e documentação técnica
```

## Início Rápido

Pré-requisitos: Docker + Docker Compose v2, GNU Make. Para rodar fora do Docker: Python 3.12 e Node 22.

### Stack completa em Docker (um comando)

```bash
make dev        # cria .env a partir de .env.example (se faltar) e sobe tudo com --env-file .env
```

| Serviço | URL |
|---|---|
| App (nginx → web + API) | http://localhost |
| API docs (Swagger) | http://localhost/api/docs |
| InfluxDB UI | http://localhost:8086 |
| MQTT | localhost:1883 |

O container `server` aplica `alembic upgrade head` antes de subir. Para criar os usuários de dev:
`make venv && make seed` (login `admin@factory-monitor.com` / `Admin@1234` — senhas em `SEED_*` no `.env`).

Outros alvos: `make ps`, `make logs s=server`, `make down`, `make config`, `make help`.
Sem make: `docker compose --env-file .env -f infra/docker-compose.yml up -d --build`
(o `--env-file` é obrigatório — sem ele o compose procura `infra/.env`).

### Backend e frontend locais (infra em Docker)

Cada comando a partir da raiz do repositório (backend, frontend e simulador em terminais separados):

```bash
make env infra        # postgres, influxdb, mosquitto e mediamtx
make venv             # .venv (server, deps fixadas) e .venv-edge (edge)
make migrate seed     # alembic upgrade head + usuários de dev
(cd server && set -a && . ../.env && set +a && ../.venv/bin/uvicorn src.main:app --port 8000)
(cd web && npm ci && npx vite --port 5173)    # proxy /api e /ws → :8000
.venv-edge/bin/python -m edge.src.simulator --server http://localhost:8000 --broker localhost
```

**Nunca** rode o simulador sem `--server/--broker`: o padrão aponta para produção.

### Testes

```bash
make test             # scripts + server (pytest, banco efêmero fm_test_server) + edge + web (vitest)
make web-check        # tsc --noEmit + vite build
```

## CI/CD

- **CI** (`.github/workflows/ci.yml`) — em todo push e PR: pytest do server (com Postgres, InfluxDB e
  Mosquitto), pytest do edge, `tsc --noEmit` + `vitest` + `vite build` do web, validação dos composes/nginx/shell
  e build das imagens Docker (server, edge, web).
- **Deploy** (`.github/workflows/deploy.yml`) — depois do CI verde em push na `main`, ou manual
  (Actions → Deploy → Run workflow). Faz SSH no VPS e roda `scripts/remote_deploy.sh`:
  `git merge --ff-only <sha>` → `compose build` → `alembic upgrade head` → `compose up -d` → healthcheck,
  com rollback automático de código e imagens se o healthcheck falhar.
  - Secrets: `VPS_HOST`, `VPS_USER` (usuário dedicado, não root), `VPS_SSH_KEY` (chave privada) e,
    recomendado, `VPS_KNOWN_HOSTS` (`ssh-keyscan -H <host>`). Sem os três primeiros o deploy é pulado.
  - Variables opcionais: `VPS_APP_DIR` (padrão `/opt/factory-monitor`), `PUBLIC_HEALTH_URL`.
  - Configure *Required reviewers* no environment `production` — deploy só com aprovação do dono do projeto.
- Deploy manual de emergência: `VPS_HOST=... VPS_USER=deploy VPS_SSH_KEY=~/.ssh/<chave> python scripts/deploy_remote.py`
  (`pip install -r scripts/requirements.txt`). Nenhum host, usuário ou senha fica no repositório.

## Roadmap

- **Fase 1** — MVP: coleta Modbus/OPC UA + Dashboards + Alertas por e-mail
- **Fase 2** — Chat com IA e relatórios customizados
- **Fase 3** — Machine Learning para Manutenção Preditiva

Veja [`docs/architecture.md`](docs/architecture.md) para diagrama completo.
