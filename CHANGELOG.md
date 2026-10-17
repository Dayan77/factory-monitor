# Changelog

Todas as mudanças relevantes do FactoryConnect ficam aqui. Formato
[Keep a Changelog 1.1](https://keepachangelog.com/pt-BR/1.1.0/), versões em
[SemVer 2.0](https://semver.org/lang/pt-BR/):

- **MAJOR** — quebra de compatibilidade da API pública (`/api/v1`, integração CMMS/ERP), do protocolo do gateway
  (tópicos/payload MQTT, cadastro) ou migração que exige ação manual;
- **MINOR** — funcionalidade nova compatível (um sprint do roadmap normalmente é um MINOR);
- **PATCH** — correção sem mudança de contrato.

Release = tag `vX.Y.Z` na `main` igual ao arquivo `VERSION` e com seção aqui (o workflow
`.github/workflows/release.yml` confere, constrói, varre, gera SBOM e publica as imagens). Processo completo:
[docs/operations.md §11](docs/operations.md). As versões 0.1.0–0.6.0 são retroativas (sprints anteriores ao
processo de release; não há tag nem imagem publicada delas).

## [Unreleased]

Sprint 7b — Fase 6 (conclusão): auditoria de KPIs, detectores, escala e gate completo do Sprint 7.

### Adicionado
- `GET /api/downtime/no-data?start&stop&line_id|machine_id`: intervalos explícitos **sem dados** por máquina
  (S7-22). A linha do tempo de estados pinta "sem dados" em vez de verde "sem parada registrada".
- Campo `open_stale` em `GET /api/failures/` e `GET /api/failures/{id}`, e selo `stale_failure` ("falha aberta há
  muito tempo — verificar") nos marcadores do mapa, tablet e Raio-X (S7-23). Configuração nova
  `FAILURE_STALE_OPEN_H` (padrão 72 h).
- Auditoria de KPIs (`docs/audit_kpis.md`), com um teste para cada contraexemplo corrigido.

### Alterado
- A janela padrão de `GET /api/maintenance/reliability` termina **agora** (antes: hoje 00:00 local) (S7-21). A tela
  de Confiabilidade não envia mais `stop`, então a falha de hoje entra em MTBF/MTTR no mesmo dia.
- Uma falha aberta só é marcada "verificar" quando os dados a contradizem: a parada ligada já foi encerrada ou a
  máquina voltou a rodar. A idade sozinha não basta. Uma quebra real longa, com parada ligada ainda aberta, continua
  "falha". Registros `incipient` nunca põem a máquina em falha (S7-23).
- O seed de desenvolvimento restabelece as falhas históricas que cria (e corrige bancos já semeados).
- Broker MQTT (prod e dev): `max_inflight_messages` passa de 100 para 2000, igual a `MQTT_MAX_INFLIGHT`. Com 100 a
  vazão tinha teto de ≈ 2 000 leituras/s.
- InfluxDB em produção e staging com limite de memória do contêiner (`INFLUX_MEM_LIMIT`) e limites por consulta
  (`INFLUXD_QUERY_*`) (docs/performance.md §5.1).

### Corrigido
- As tasks de agregação do InfluxDB (1 min e 1 h) usavam um fluxo compartilhado entre os cinco agregados, o que
  impedia o pushdown para o armazenamento. Na carga nominal, uma execução levava 60 s e ~3 GB, e o InfluxDB era morto
  por falta de memória em loop. Agora leva 6 s, com os mesmos valores. As tasks existentes são atualizadas na partida
  do worker.
- Acessibilidade: contraste do texto secundário dos marcadores do mapa (verde "rodando") e quadro da semana da
  programação rolável pelo teclado (axe).
- Cadência ao vivo do mapa (A-19b): uma leitura corrompida do contador (CLP reiniciando, palavra de 32 bits lida em
  duas metades) virava centenas de milhares de pç/h no marcador. Agora `live_parts` aplica o mesmo filtro de
  plausibilidade da ingestão (`counter_step` com `max_rate` da tag ou da máquina): o glitch é descartado e mantém a
  última leitura aceita; um salto real confirmado pela leitura seguinte faz nova base (docs/audit_kpis.md).
- Acessibilidade (tema escuro): o token `--c-danger-solid` passa de `red-500` para `red-600` (texto branco sobre ele: 4,8:1;
  antes 3,8:1), o hover dos botões de perigo vai para `red-700` e o cartão de chamado de emergência usa fundo de
  superfície com borda vermelha.

## [0.7.0] - 2026-10-07

Sprint 7 — Fase 6: endurecimento e prontidão para produção.

### Adicionado
- Autenticação endurecida: rotação de chaves JWT (`kid`), sessões revogáveis, MFA TOTP opcional, redefinição de
  senha e convites com token de uso único, bloqueio progressivo por força bruta, lista de senhas comuns
  (docs/security.md).
- LGPD: exportação dos dados pessoais, anonimização, retenção configurável, configurações de privacidade
  (docs/privacy.md).
- Matriz de autorização gerada da tabela de rotas (todo endpoint × todo papel × outro tenant) e testes de
  isolamento por tenant em todos os módulos.
- Ingestão em processos separados (`MQTT_INGEST=process`, shards por tag) e worker dos detectores; teste de escala
  (e2e/scale) com resultado versionado.
- Detectores de parada e preditiva robustos a quedas longas (catch-up por checkpoint) com métricas de atraso
  `fm_detector_lag_*`.
- Cadeia de suprimentos no CI: pip-audit (server, edge, scripts), npm audit (web, site) com lista de aceites com
  prazo (`.github/security/audit-allowlist.toml`), gitleaks no histórico completo (`.github/security/`), Trivy
  nas imagens e SBOM CycloneDX como artefato; Dependabot para pip, npm, Docker e GitHub Actions.
- Cabeçalhos de segurança no app e no site: CSP sem `unsafe-inline` em `script-src` (hash dos scripts inline do
  site gerado no build), HSTS pronto para preload, `frame-ancestors 'none'`, Permissions-Policy, COOP; testados com
  nginx real e Chromium no CI.
- Processo de release: `VERSION`, este changelog, workflow **Release** (tag → build → Trivy → SBOM → GHCR com
  proveniência SLSA), imagens com tag `X.Y.Z` e `sha-<commit>`.
- Deploy de uma versão específica com aprovação manual (environment `production`), staging no mesmo VPS
  (`staging.factoryconnect.tech`, banco/volumes próprios, `infra/docker-compose.staging.yml`).
- Observabilidade de produção: Alertmanager com e-mail, alertas de atraso de ingestão, dead letters, atraso dos
  detectores, gateway sem contato, taxa de erro, disco, idade do backup, demo pública parada, endpoint público fora
  do ar e certificado expirando; node-exporter, sql-exporter e blackbox-exporter; painel do Grafana atualizado.
- Rotação de logs dos containers (json-file) e retenção por tamanho no Prometheus.
- `docs/go_live.md`: checklist de entrada em produção para o dono do projeto.

### Alterado
- Deploy por tag `vX.Y.Z` (ou sha), sem rebuild quando as imagens vêm do registro; rollback volta para a versão
  anterior registrada (não para `:latest`); migrations com timeout e revisão do banco registrada antes.
- Dependências: fastapi 0.136.3 + starlette 1.7.0, python-multipart 0.0.32, cryptography 50.0.2,
  prometheus-fastapi-instrumentator 8.1.0, pyopenssl 26.4.0 (edge); lockfile do web atualizado (`npm audit fix`).
- `docs/architecture.md` e `README.md` reescritos para a arquitetura real.

### Corrigido
- Itens abertos da auditoria de KPIs (docs/audit_kpis.md).
- 20+ vulnerabilidades conhecidas em dependências do server/edge (starlette, python-multipart, cryptography,
  pyopenssl) e 11 no lockfile do web (axios, form-data, postcss, nanoid, js-yaml, entre outras).

### Segurança
- Vazamento histórico da senha SSH do VPS (commit `cc1d1e7`, abril/2026) registrado como achado conhecido do
  gitleaks; rotação e desativação do login por senha conferidas no go-live (docs/go_live.md §1).

## [0.6.0] - 2026-10-07

Sprint 6 — Fase 5: raio-x da fábrica, UX, relatórios, onboarding e demo.

### Adicionado
- Mapa/layout da planta com status ao vivo (planta baixa enviada pelo cliente ou layout automático).
- Drill-down Fábrica → Linha → Máquina → Componente com KPIs em cada nível.
- PWA do operador (tablet) e do técnico (celular), com leitura de código de barras.
- Assistente de implantação: importação de ativos/tags por CSV e navegação OPC UA pelo gateway.
- Relatórios exportáveis (PDF/XLSX) e agendados por e-mail.
- Tenant de demonstração somente-leitura alimentado pela planta virtual (profile `demo`), com sessão por visitante.
- Gateway de borda empacotado: imagem multi-arquitetura (amd64/arm64) e cadastro por código.

## [0.5.1] - 2026-10-07

Sprint 5b — correções de ingestão e preditiva.

### Corrigido
- Pipeline de ingestão MQTT ordenado por tag, com backpressure, sessão persistente QoS 1, dead letters
  (`ingest_dead_letters`) e métricas `fm_ingest_*` — nenhuma leitura descartada em silêncio.
- Leituras atrasadas (buffer do gateway) reprocessam as janelas da preditiva que tocam (marcas d'água por tag).
- Recálculos longos em segundo plano (`?background=true`).

## [0.5.0] - 2026-10-06

Sprint 5 — Fase 4: preditiva, IA e gateways.

### Adicionado
- Previsão da data de troca por ritmo de uso; detecção de desvio (tempo de curso, corrente, vibração, temperatura).
- Assistente de IA com ferramentas que consultam KPIs, histórico e ordens de serviço (OpenAI ou Anthropic).
- Gestão de gateways pela nuvem: cadastro, configuração remota, heartbeat, credencial MQTT por gateway.
- Relatórios de vida útil por fabricante/modelo.

## [0.4.0] - 2026-10-05

Sprint 4 — Fase 3: manutenção.

### Adicionado
- Planos por componente (ciclos, horas ou dias — o que vier primeiro) e % de vida consumida.
- Ordens de serviço preventiva/corretiva/preditiva; troca de componente zera contadores e mantém histórico.
- Registro de falhas com taxonomia ISO 14224; MTBF, MCBF, MTTR e disponibilidade em vários níveis.
- Painel de próximas manutenções, alertas em 80%/100% da vida, webhooks e REST para CMMS/ERP.

## [0.3.0] - 2026-10-05

Sprint 3 — Fase 2: eficiência produtiva.

### Adicionado
- OEE real (ISO 22400) por máquina/linha/turno, detecção automática de paradas e micro-paradas, motivos do operador.
- Turnos e calendário de produção, Pareto de perdas, UI do raio-x.
- Endurecimento de operação: broker MQTT autenticado com TLS em produção, backups com teste de restauração,
  Prometheus/Grafana.

## [0.2.0] - 2026-10-05

Sprint 2 — Fase 1: ativos e coleta de uso.

### Adicionado
- Hierarquia Planta → Linha → Máquina → Componente, papéis semânticos de tag e acumuladores de uso com
  tratamento de reset/rollover.
- Contagem de borda no edge para pulsos rápidos; planta virtual (OPC UA + Modbus) com desgaste e falhas.
- Site público `factoryconnect.tech` (Astro) com captura de leads e consentimento LGPD; app em
  `app.factoryconnect.tech`.

## [0.1.0] - 2026-10-05

Sprint 1 — Fase 0: fundação.

### Adicionado
- Testes (pytest, vitest), CI no GitHub Actions e deploy por chave SSH com healthcheck e rollback.
- Ambiente de dev com um comando (`make dev`), imagens com versão fixa.
- Status da máquina derivado dos dados; especificação de domínio e KPIs (docs/domain.md, docs/kpis.md).

### Segurança
- Senha e IP do VPS removidos do repositório; segredos só em `.env`/secrets do CI.
