# FactoryConnect — Arquitetura

Estado real em 0.7.0 (Sprint 7). Regras de domínio: [domain.md](domain.md) · KPIs: [kpis.md](kpis.md) ·
operação: [operations.md](operations.md) · segurança: [security.md](security.md) · go-live: [go_live.md](go_live.md).

## Visão geral

```
 CHÃO DE FÁBRICA (cliente)                          VPS (Docker Compose + Traefik do Easypanel)
┌──────────────────────────────┐                 ┌───────────────────────────────────────────────────────────────┐
│ CLPs / CNC / robôs / sensores│                 │  Traefik (TLS Let's Encrypt, HTTP→HTTPS)                      │
│  Modbus TCP · OPC UA · MQTT  │                 │   factoryconnect.tech ──► fm_site   (Astro estático, nginx)   │
└──────────────┬───────────────┘                 │   app.factoryconnect.tech ─┬► fm_web (React PWA, nginx)       │
               │ polling / subscrição            │                            └► fm_server /api /ws               │
┌──────────────▼───────────────┐                 │   staging.factoryconnect.tech ─► fm_stg_web / fm_stg_server   │
│ Gateway de borda (edge/)     │                 │                                                               │
│  IPC x86-64 ou Raspberry Pi  │   HTTPS         │  ┌─────────────── fm_server (FastAPI) ─────────────────────┐  │
│  · cadastro por código       ├────────────────►│  │ REST + WebSocket · auth/RBAC/MFA · KPIs · relatórios    │  │
│  · config puxada da nuvem    │ (cadastro,      │  │ IA (OpenAI/Anthropic) · webhooks CMMS · demo-bot        │  │
│  · contagem de borda (pulsos)│  config,        │  │ ingestão MQTT (inline ou processos por shard)  ◄──┐     │  │
│  · deadband nos analógicos   │  heartbeat)     │  │ worker: detector de paradas, preditiva, planos   │     │  │
│  · buffer SQLite offline     │                 │  └──┬───────────────┬───────────────────────────────┼─────┘  │
│  · AES-256-GCM opcional      │   MQTT TLS 8883 │     │ SQL           │ Flux/line protocol            │ 1883   │
│                              ├────────────────►│  ┌──▼─────────┐  ┌──▼──────────┐  ┌─────────────────┴─────┐  │
└──────────────────────────────┘ usuário/ACL por │  │ PostgreSQL │  │ InfluxDB 2  │  │ Mosquitto (auth, ACL  │  │
                                  gateway        │  │ (Alembic)  │  │ séries      │  │ por tenant, TLS 8883) │  │
                                                 │  └────────────┘  └─────────────┘  └───────────────────────┘  │
                                                 │  MediaMTX (RTSP → HLS, câmeras)                               │
                                                 │  profile demo:  planta virtual ──► gateway da demo (rede      │
                                                 │                 interna fm-demo, sem internet) ──► broker     │
                                                 │  profile monitoring: Prometheus · Alertmanager (e-mail) ·     │
                                                 │                 Grafana · node/sql/blackbox exporters         │
                                                 └───────────────────────────────────────────────────────────────┘
```

## Componentes

| Componente | Código | Papel |
|---|---|---|
| **Gateway de borda** | `edge/src/gateway.py`, `protocols/`, `buffer/`, `counting.py` | Lê CLPs (Modbus TCP, OPC UA, MQTT), aplica deadband nos analógicos e **contagem de borda** em pulsos rápidos (sem perder ciclo), publica por MQTT; sem rede guarda tudo num buffer SQLite e reenvia em ordem. Cadastro por código de uso único (`enrollment.py`), configuração e comandos puxados da nuvem (`remote_config.py`, `commands.py`), heartbeat. Imagem multi-arquitetura (`edge/Dockerfile`, `infra/gateway/`). |
| **Planta virtual** | `edge/virtual_plant/` | Servidores OPC UA + Modbus simulados (cilindros, motores, robô, contador de peças, falhas, desgaste). Base dos testes de integração e da **demo pública** (`--demo`, roteiro em tempo real). |
| **Broker** | `infra/mosquitto/prod/` | Mosquitto autenticado: 1 usuário por gateway restrito a `factory/<tenant>/#`, usuário do backend lendo `factory/#`, TLS em 8883, sessão persistente QoS 1 com filas dimensionadas (`max_queued_messages`). |
| **Pipeline de ingestão** | `server/src/services/mqtt_subscriber.py`, `services/ingest/` | Fila ordenada **por tag** com backpressure (`MQTT_MAX_INFLIGHT`), ack QoS 1 só depois de gravado, repetição de falhas transitórias, **dead letters** (`ingest_dead_letters`) em vez de descarte, marcas d'água para leituras atrasadas. Grava no Influx, atualiza acumuladores de uso/estado da máquina e avalia regras de alerta. Roda no processo da API (`MQTT_INGEST=inline`) ou em N processos por shard de tag (`process`). |
| **Worker** | `server/src/worker.py` | Detector de paradas/micro-paradas (OEE, ISO 22400), motor preditivo (desvio de curso/corrente/vibração/temperatura, previsão de troca) e motor de planos de manutenção — fora do event loop da API, com **catch-up por checkpoint** depois de quedas longas (`fm_detector_lag_*`). Mensagens ao vivo chegam ao navegador via Postgres `NOTIFY` (`websocket/relay.py`). |
| **API** | `server/src/main.py`, `api/routes/` | FastAPI: REST (`/api/...`) e WebSocket (`/ws/sensors/{tenant}` com token). Auth com JWT rotacionável, sessões revogáveis, MFA TOTP; RBAC por papel e **isolamento por `tenant_id` em toda consulta** (matriz de autorização gerada das rotas). Módulos: ativos, uso, OEE/produção, manutenção (OS, falhas ISO 14224, MTBF/MCBF/MTTR), preditiva, mapa da planta, onboarding, relatórios PDF/XLSX agendados, IA com ferramentas, integração CMMS/ERP (webhooks + REST), LGPD (exportação/anonimização), demo. |
| **PostgreSQL 16** | `server/alembic/` | Dados de negócio (tenants, ativos, OS, eventos, auditoria, sessões). Schema só por migration Alembic. |
| **InfluxDB 2.7** | `services/influx.py` | Séries temporais das tags (bucket `sensors`). |
| **App web** | `web/` | React 18 + TypeScript + Tailwind + Recharts + React Query + Zustand; design system próprio (claro/escuro), drill-down Fábrica → Linha → Máquina → Componente, mapa ao vivo, PWA do operador (tablet) e do técnico (celular). Servido por nginx com CSP estrita (`web/nginx/`). |
| **Site público** | `site/` | Astro estático em `factoryconnect.tech`: páginas comerciais, formulário de leads (LGPD), botão da demo. CSP com hash dos scripts inline gerado no build (`site/scripts/render-nginx.mjs`). |
| **Demo pública** | `server/src/services/demo/`, profile `demo` | Tenant somente-leitura `demo-publica` alimentado pela planta virtual pelo pipeline normal (nenhum dado fabricado no banco); sessão por visitante com limite por IP; demo-bot executa a troca roteirizada do componente. |
| **Observabilidade** | `infra/monitoring/` | Prometheus (métricas do backend + exporters), Alertmanager por e-mail, Grafana provisionado. Alertas: [operations.md §8.4](operations.md). |

## Fluxo de um dado

1. O gateway lê a tag no CLP (ou conta o pulso na borda) e publica `factory/<tenant>/sensors/<tag>` com `ts` de origem,
   QoS 1, opcionalmente cifrado (AES-256-GCM, `ENCRYPTION_KEY`). Sem conexão, o buffer SQLite guarda e reenvia.
2. O broker autentica o gateway e só aceita tópicos do próprio tenant (ACL).
3. A ingestão valida tópico/payload, confere que a tag pertence ao tenant, enfileira por tag (ordem garantida),
   grava no Influx em lote, atualiza uso/estado e regras; só então confirma ao broker. Falha persistente → dead letter.
4. O worker lê os dados fechados: abre/fecha paradas, calcula OEE por turno, alimenta a preditiva e os planos
   (80%/100% da vida → alerta e OS). Leitura atrasada reprocessa as janelas que toca.
5. Navegadores recebem leituras, estados e eventos por WebSocket (o processo da API retransmite o que os workers
   publicam via `NOTIFY`), sempre filtrados pelo tenant do token.

## Multi-tenancy e segurança

- Toda linha de negócio carrega `tenant_id`; toda consulta filtra por ele; todo endpoint tem teste de isolamento.
- JWT com `tenant_id` e papel (`operator`, `technician`, `production_supervisor`, `maintenance_planner`, `manager`,
  `admin`; `superadmin` da plataforma). Detalhes: [security.md](security.md), privacidade: [privacy.md](privacy.md).
- Borda: credencial MQTT e token de API por gateway, revogáveis; TLS no broker; nenhum segredo no repositório.
- HTTP: HSTS (preload), CSP, `frame-ancestors 'none'`, nosniff, Referrer/Permissions-Policy nos containers e na API.
- Cadeia de suprimentos: pip-audit, npm audit, gitleaks, Trivy, SBOM e Dependabot ([operations.md §13](operations.md)).

## Ambientes e entrega

| Ambiente | Onde | Compose |
|---|---|---|
| Dev | máquina do desenvolvedor (`make dev` → http://localhost) | `infra/docker-compose.yml` (+ profiles `sim`, `monitoring`) |
| Staging | mesmo VPS, `staging.factoryconnect.tech`, banco/volumes próprios | `infra/docker-compose.staging.yml` |
| Produção | VPS, `factoryconnect.tech` / `app.` | `infra/docker-compose.prod.yml` (+ profiles `monitoring`, `demo`) |

CI em todo push/PR (testes, typecheck, build e varredura das imagens, cabeçalhos no Chromium, compose/nginx/
actionlint); tag `vX.Y.Z` → workflow **Release** (imagens no GHCR com SBOM e proveniência); workflow **Deploy**
manual com aprovação do dono para produção. Processo: [operations.md §2, §3, §11, §12](operations.md).
