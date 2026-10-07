# Operação — runbook do FactoryConnect

Procedimentos de produção: deploy, rollback, backup/restauração, rotação de segredos, gateways MQTT,
observabilidade e resposta a incidentes. Todo comando abaixo roda **no VPS**, em `/opt/factory-monitor`,
com o usuário da aplicação (grupo `docker`), salvo indicação. Atalho usado no texto:

```bash
cd /opt/factory-monitor
alias dc='docker compose --env-file /opt/factory-monitor/.env -f infra/docker-compose.prod.yml'
```

> **Deploy em produção só com confirmação explícita do dono do projeto.** O workflow `Deploy` usa o
> environment `production` com *required reviewers*: ninguém (nem agente) aprova em nome do dono.

## 1. Visão geral

| Container | Função | Exposto |
|---|---|---|
| `fm_postgres` | dados de negócio (Alembic) | só rede `fm-net` |
| `fm_influxdb` | séries temporais (bucket `sensors`) | só rede `fm-net` |
| `fm_mosquitto` | broker MQTT, **autenticado** | `8883/tcp` (TLS, gateways) · `127.0.0.1:1883` |
| `fm_server` | FastAPI + assinante MQTT | Traefik: `app.factoryconnect.tech/api`, `/ws` |
| `fm_web` | app React (nginx) | Traefik: `app.factoryconnect.tech` |
| `fm_site` | site estático | Traefik: `factoryconnect.tech` |
| `fm_mediamtx` | RTSP → HLS | só rede `fm-net` |
| `fm_prometheus`, `fm_grafana` | profile `monitoring` | Grafana em `127.0.0.1:3000` (túnel SSH) |
| `fm_demo_plant`, `fm_demo_gateway` | profile `demo`: planta virtual + gateway do tenant DEMO (§10) | nada publicado (rede interna `fm-demo`) |

Arquivos fora do git no VPS: `.env` (segredos), `mqtt/gateways.conf` e `mqtt/certs/` (MQTT),
`backups/pre-deploy/` (backups automáticos do deploy) e `/var/backups/factory-monitor/` (backups noturnos).

> Portas publicadas pelo Docker **ignoram o UFW** (o Docker escreve as regras de iptables antes dele).
> O que limita a exposição é o `ports:` do compose — por isso tudo que é interno é `127.0.0.1:` ou não é publicado.

## 2. Deploy

Caminho normal: merge na `main` → CI verde → workflow **Deploy** (aguarda aprovação no environment
`production`) → `scripts/remote_deploy.sh` no VPS por SSH (usuário dedicado, chave em `VPS_SSH_KEY`):

1. `git merge --ff-only <sha>` (alterações locais no VPS abortam o deploy);
2. guarda as imagens atuais como `:rollback` e faz o build das novas;
3. **backup do Postgres** em `backups/pre-deploy/` (mantém 5; `PRE_DEPLOY_BACKUP=0` pula) — falhou → aborta antes das migrations;
4. `alembic upgrade head` (migrations **antes** do restart);
5. `up -d`; healthcheck em `/api/health` por até 2 min;
6. falhou → **rollback automático** de código e imagens (seção 3).

Manual (mesmo script, do seu computador): `ssh deploy@VPS 'bash -s' -- <sha> /opt/factory-monitor < scripts/remote_deploy.sh`.

Antes de aprovar um deploy, confira:
- [ ] CI verde no commit (todos os jobs, inclusive `backup` e `infra`);
- [ ] migrations novas são **compatíveis com o código anterior** (aditivas; nada de `DROP`/rename no mesmo deploy);
- [ ] variáveis novas do `.env` já estão no VPS (compare com `.env.prod.example`; o compose falha com
      mensagem clara se faltar `MQTT_USERNAME`/`MQTT_PASSWORD`, por exemplo);
- [ ] janela combinada se houver mudança no broker MQTT (gateways reconectam).

Depois: `dc ps` (tudo `healthy`), `curl -fsS https://app.factoryconnect.tech/api/health`, painel
**FactoryConnect — Operação** sem alerta, mensagens MQTT chegando (seção 7).

## 3. Rollback

**Automático**: se o healthcheck falhar, `remote_deploy.sh` volta o código (`git reset --keep`) e as imagens
(`:rollback` → `:latest`) e sobe de novo. A saída do job mostra `rollback saudável` ou pede intervenção.

**Manual** (o deploy passou no healthcheck mas a versão está ruim):

```bash
git log --oneline -5                          # escolha o commit bom
docker tag factory-monitor-server:rollback factory-monitor-server:latest   # se a imagem anterior ainda existe
docker tag factory-monitor-web:rollback factory-monitor-web:latest
git reset --keep <sha-bom>
dc up -d --no-build server web
```
Ou dispare o workflow **Deploy** manualmente apontando para o commit bom (depois de reverter na `main`).

**Migrations**: não são revertidas automaticamente. Como toda migration é aditiva, o código anterior roda
com o schema novo. Se uma migration destruiu dados, restaure o backup pré-deploy (seção 4.4) com o código
anterior:

```bash
ls -1t backups/pre-deploy/        # o mais recente é o feito antes do deploy problemático
docker stop fm_server
RESTORE_CONFIRM=yes RESTORE_SKIP_INFLUX=1 scripts/restore.sh backups/pre-deploy/fm-backup-<UTC>.tar
dc up -d server
```

## 4. Backup e restauração

### 4.1 O que é salvo
`scripts/backup.sh` (via `docker exec`, com a stack no ar) gera `fm-backup-<UTC>.tar` + `.sha256`:
- `postgres.dump` — `pg_dump -Fc` do banco do app (snapshot consistente);
- `influx/` — `influx backup` de todos os buckets (inclui metadados, usuários e tokens);
- `manifest.json` — versões, sha256 de cada arquivo, **linhas por tabela** (contadas no dump) e
  **pontos por bucket** (até `count_stop`).

Variáveis: `BACKUP_DIR` (padrão `./backups`), `BACKUP_KEEP` (14), `BACKUP_UPLOAD_CMD`, `BACKUP_PASSPHRASE_FILE`,
`BACKUP_PING_URL`, `BACKUP_SKIP_INFLUX`, `BACKUP_INFLUX_COUNT` — documentadas no topo do script.
A rotação só acontece depois de um backup completo; um backup com falha não apaga nada.

**Arquivos dos tenants (`FILES_DIR`)** — plantas baixas do mapa (`<tenant_id>/layouts/<asset_id>.<ext>`). Em produção
o compose fixa `FILES_DIR=/app/data/files` no volume nomeado `server-files` (dono uid 10001, criado pela imagem):
sobrevive a `dc up -d` com imagem nova e a recriação do container. **Ainda não entra no `backup.sh`**; até lá, copie
o volume junto com o backup:
`docker run --rm -v factory-monitor_server-files:/f:ro -v "$BACKUP_DIR":/b alpine tar czf /b/files-$(date -u +%Y%m%dT%H%M%SZ).tgz -C /f .`

### 4.2 Agendamento no VPS (cron)
```bash
sudo install -d -m 700 -o fmonitor -g fmonitor /var/backups/factory-monitor   # setup_vps.sh já cria
openssl rand -base64 48 | sudo -u fmonitor tee /home/fmonitor/.fm-backup-pass >/dev/null
sudo chmod 600 /home/fmonitor/.fm-backup-pass       # guarde uma cópia FORA do VPS (cofre de senhas)
sudo -u fmonitor crontab -e
```
```cron
# Backup diário 03:17 (UTC do VPS), 14 cópias locais, cópia cifrada off-site, monitor de cron
17 3 * * * cd /opt/factory-monitor && BACKUP_DIR=/var/backups/factory-monitor BACKUP_KEEP=14 BACKUP_PASSPHRASE_FILE=/home/fmonitor/.fm-backup-pass BACKUP_UPLOAD_CMD='rclone copy "$1" b2:fm-backups/' BACKUP_PING_URL=https://hc-ping.com/<uuid> scripts/backup.sh 2>&1 | logger -t fm-backup
```
- Off-site é obrigatório para considerar o backup confiável (o VPS pode sumir). Qualquer comando serve em
  `BACKUP_UPLOAD_CMD` (`rclone`, `aws s3 cp`, `scp`); o arquivo vai cifrado se `BACKUP_PASSPHRASE_FILE` estiver definido.
- `BACKUP_PING_URL` (ex. healthchecks.io) avisa quando o backup **não** rodou ou falhou (`/fail`).
- Logs: `journalctl -t fm-backup` (ou `grep fm-backup /var/log/syslog`).
- Manual: `make backup BACKUP_DIR=/var/backups/factory-monitor`.

### 4.3 Teste de restauração (automático e mensal)
- **CI** (job `backup`): `scripts/backup_restore_test.sh` cria Postgres/Influx com dados "difíceis" (bytea, jsonb,
  tab/newline, schema extra, extensão, buckets), faz backup, restaura em **containers novos** e confere linhas por
  tabela, conteúdo (md5) e pontos por bucket.
- **Local com a stack de dev**: `make backup-restore-test-dev` (somente leitura na origem).
- **Mensal em produção** (anote a data aqui): copie o último backup off-site para uma máquina de teste e rode
  `RESTORE_CONFIRM=yes PG_CONTAINER=... INFLUX_CONTAINER=... INFLUX_TOKEN=<token de produção> scripts/restore.sh <arquivo>`
  contra containers descartáveis (`docker run postgres:16.15-alpine` / `influxdb:2.7.12-alpine`). Backup que nunca foi
  restaurado não é backup.

### 4.4 Restaurar em produção
1. Avise os usuários; pare o backend (para não gravar durante o restore): `docker stop fm_server`.
2. Se o arquivo veio do off-site, confira o `.sha256` junto dele.
3. Rode (o script recusa se `fm_server` estiver rodando ou sem `RESTORE_CONFIRM=yes`):
   ```bash
   set -a; . ./.env; set +a          # INFLUX_TOKEN = token da instância de origem (para a verificação)
   RESTORE_CONFIRM=yes BACKUP_PASSPHRASE_FILE=/home/fmonitor/.fm-backup-pass \
     scripts/restore.sh /var/backups/factory-monitor/fm-backup-<UTC>.tar.enc
   ```
   ou `make restore FILE=...` (pede para digitar `restaurar`).
4. O script recria o banco (`DROP DATABASE … WITH (FORCE)`), roda `pg_restore`, faz `influx restore --full`
   e **confere** linhas por tabela e pontos por bucket; sai ≠ 0 se algo não bater.
5. `dc up -d server` → `alembic upgrade head` roda na subida do deploy seguinte; se o backup for de uma versão
   anterior do schema, rode `dc run --rm -T server alembic upgrade head` antes.
6. O `influx restore --full` traz os tokens da origem: o `INFLUX_TOKEN` do `.env` precisa ser o da época do backup.

## 5. Segredos

Ficam só no `/opt/factory-monitor/.env` (modo 600) e nos *secrets* do GitHub. Nunca no repositório.
Se um segredo vazar (commit, log, print): **rotacione** — apagar não basta.

| Segredo | Onde | Rotação |
|---|---|---|
| `APP_SECRET_KEY` | `.env` | `openssl rand -hex 32` → `.env` → `dc up -d server`. Invalida todos os JWT (todos relogam). |
| `POSTGRES_PASSWORD` | `.env` | `docker exec -it fm_postgres psql -U $POSTGRES_USER -d postgres -c "ALTER USER \"$POSTGRES_USER\" PASSWORD '<nova>'"` → `.env` → `dc up -d server`. (A variável do container só vale na criação do volume.) |
| `INFLUX_TOKEN` | `.env` | `docker exec fm_influxdb influx auth create --all-access -o factory-monitor -t <token atual>` → novo token no `.env` → `dc up -d server` → `influx auth delete --id <id antigo>`. Backups antigos restauram com o token antigo (seção 4.4). |
| `INFLUX_ADMIN_PASSWORD` | `.env` | `docker exec fm_influxdb influx user password -n admin -t <token>` |
| `MQTT_PASSWORD` (backend) | `.env` | `openssl rand -hex 24` → `.env` → `dc up -d mosquitto server` (o broker re-renderiza o passwd na partida). |
| Senha de gateway MQTT | `mqtt/gateways.conf` (só hash) | `make mqtt-gateway-add GATEWAY=<mesmo nome> TENANT=<uuid>` gera outra e invalida a anterior no reload; atualize o `.env` do gateway. |
| `ENCRYPTION_KEY` (payload edge→server) | `.env` do server **e** de cada gateway | Troca coordenada: sem suporte a duas chaves, há janela de mensagens recusadas. Agende; troque nos gateways e no server em seguida. |
| `GRAFANA_ADMIN_PASSWORD` | `.env` | `docker exec fm_grafana grafana cli admin reset-admin-password <nova>` + `.env` |
| `SENDGRID_API_KEY`, `SMTP_PASSWORD`, `OPENAI_API_KEY` | `.env` | gere no provedor → `.env` → `dc up -d server` → revogue a antiga no provedor. |
| `VPS_SSH_KEY` (deploy) | secret do GitHub + `~deploy/.ssh/authorized_keys` | `ssh-keygen -t ed25519 -f fm_deploy -C deploy@github` → adicione a pública no VPS → troque o secret → remova a antiga. |
| `VPS_KNOWN_HOSTS` | secret do GitHub | `ssh-keyscan -H <host>` se a host key do VPS mudar (reinstalação). |
| Passphrase dos backups | `~fmonitor/.fm-backup-pass` + cofre | Nova passphrase vale para os backups novos; **mantenha a antiga** enquanto houver backups cifrados com ela. |

Depois de qualquer rotação: `dc ps` saudável, login no app, mensagens MQTT chegando, `make backup` ok.

## 6. MQTT em produção

### 6.1 Como funciona
- **Nunca anônimo.** `infra/mosquitto/prod/render.sh` gera na partida o `passwd` (hash PBKDF2) e o `acl`:
  - backend `MQTT_USERNAME`: `read factory/#` (não publica);
  - cada gateway: `readwrite factory/<tenant_id>/#` — não lê nem publica em outro tenant;
  - `fm-health` (senha aleatória interna): healthcheck.
- **TLS** em `8883` para os gateways quando `MQTT_TLS_ENABLED=true` (padrão `false`; certificado em
  `MQTT_TLS_CERT_DIR`, padrão `/opt/factory-monitor/mqtt/certs`: `fullchain.pem` + `privkey.pem`). O gateway liga
  TLS com `MQTT_TLS=true` (+ `MQTT_CA_CERT` = caminho de uma CA PEM; vazio = CAs do sistema; certificado e nome do
  host sempre verificados). `1883` sem TLS: rede interna e `MQTT_PLAIN_BIND` (loopback por padrão no compose).
- Entrada inválida (usuário, tenant que não é UUID, hash, certificado ausente com TLS ligado, senha do backend
  com menos de 16 caracteres) → o broker **não sobe** (falha fechado; `docker logs fm_mosquitto` diz o motivo).
- Dev (`infra/docker-compose.yml`) continua anônimo em `1883`.
- Testado de verdade em `infra/tests/test_mosquitto_prod.py` (anônimo recusado, ACL entre tenants, TLS, reload).

### 6.2 Adicionar um gateway
```bash
make mqtt-gateway-add GATEWAY=gw-manaus-01 TENANT=<uuid do tenant>   # MQTT_GATEWAYS_FILE=mqtt/gateways.conf
```
Gera uma senha aleatória, guarda **só o hash**, recarrega o broker (SIGHUP, sem derrubar ninguém) e imprime
**uma única vez** o bloco para o `.env` do gateway:
```
TENANT_ID=<uuid>  MQTT_BROKER_HOST=mqtt.factoryconnect.tech  MQTT_BROKER_PORT=8883
MQTT_TLS=true  MQTT_USERNAME=gw-manaus-01  MQTT_PASSWORD=<gerada>
```
Entregue a senha por canal seguro (não por e-mail/WhatsApp em claro). Perdeu? Rode o mesmo comando de novo (troca a senha).
Nome: `[A-Za-z0-9_.-]`, 3–64 caracteres; sugestão `gw-<cliente>-<linha>-<n>`.

Revogar: `make mqtt-gateway-remove GATEWAY=gw-manaus-01` (novas conexões recusadas; para derrubar a sessão aberta
na hora, `docker restart fm_mosquitto`). Listar: `make mqtt-gateway-list`.

### 6.3 Certificado TLS (8883)
Opção A — **direto no Mosquitto** (padrão do compose): DNS `mqtt.factoryconnect.tech` → VPS e um certificado
Let's Encrypt via **DNS-01** (a porta 80 é do Traefik):
```bash
sudo certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/.cf.ini -d mqtt.factoryconnect.tech \
  --deploy-hook 'install -m 644 $RENEWED_LINEAGE/fullchain.pem /opt/factory-monitor/mqtt/certs/fullchain.pem;
                 install -m 600 $RENEWED_LINEAGE/privkey.pem /opt/factory-monitor/mqtt/certs/privkey.pem;
                 docker restart fm_mosquitto'
```
O broker copia os arquivos na partida (por isso o `restart` no hook). Os gateways validam com as CAs do sistema
(`MQTT_CA_CERT` vazio).

Opção B — **Traefik TCP** (TLS no Traefik, certificado pelo `letsencrypt` dele): exige um entrypoint `mqtts`
(`:8883`) na configuração **estática** do Traefik do Easypanel. Então `MQTT_TLS_ENABLED=false`, remova a porta
8883 do serviço e acrescente ao `mosquitto` a rede `easypanel` e os labels:
```yaml
- "traefik.enable=true"
- "traefik.docker.network=easypanel"
- "traefik.tcp.routers.fm-mqtt.rule=HostSNI(`mqtt.factoryconnect.tech`)"
- "traefik.tcp.routers.fm-mqtt.entrypoints=mqtts"
- "traefik.tcp.routers.fm-mqtt.tls=true"
- "traefik.tcp.routers.fm-mqtt.tls.certresolver=letsencrypt"
- "traefik.tcp.services.fm-mqtt.loadbalancer.server.port=1883"
```
Desvantagem: o broker passa a ver o IP do Traefik, não o do gateway, nos logs.

### 6.4 Migração do broker anônimo (uma vez) — **pré-requisito do deploy**
O broker de produção deixou de ser anônimo. **Não faça o deploy (nem o merge em `main`, que dispara o deploy)
antes dos passos 1–3 no VPS**, senão os gateways em campo, que hoje conectam sem senha, são recusados e a ingestão
para. O `scripts/remote_deploy.sh` confere os itens abaixo **antes** de build/migrations/up e aborta com a versão
anterior no ar se faltar algum (mensagem `pré-requisitos do MQTT de produção não atendidos`).
1. No VPS, `.env`: `MQTT_USERNAME`/`MQTT_PASSWORD` (≥ 16 caracteres: `openssl rand -hex 24`).
2. `make mqtt-gateway-add GATEWAY=... TENANT=...` para **cada** gateway em campo (um tenant por gateway) e o bloco
   impresso no `.env` de cada gateway (`MQTT_USERNAME`/`MQTT_PASSWORD`). Instalação sem nenhum gateway ainda:
   `MQTT_ALLOW_NO_GATEWAYS=1` no `.env`.
3. Transição sem TLS: `MQTT_PLAIN_BIND=0.0.0.0` (padrão do `.env.prod.example`) publica `1883` **com senha, sem
   TLS** — os gateways continuam em `MQTT_BROKER_PORT=1883`.
4. TLS (opcional no primeiro deploy): certificado em `mqtt/certs/` (§6.3) → `MQTT_TLS_ENABLED=true` → gateways com
   `MQTT_TLS=true`, `MQTT_BROKER_PORT=8883` (e `MQTT_CA_CERT` se a CA não for pública). Quando todos estiverem em
   8883: `MQTT_PLAIN_BIND=127.0.0.1`. `MQTT_TLS_ENABLED=true` sem `fullchain.pem`/`privkey.pem` → o deploy aborta.
5. Deploy. `docker logs fm_mosquitto | grep -i "not authorised"` mostra quem ficou para trás.

## 7. IP do cliente atrás do proxy (rate limit de leads)

- O `fm_server` roda o uvicorn com `--no-proxy-headers`; o único lugar que confia em `X-Forwarded-For` é o app
  (`ProxyHeadersMiddleware` com `FORWARDED_ALLOW_IPS`): IP do cliente = **entrada mais à direita que não é proxy
  confiável**. Um XFF forjado à esquerda nunca é usado. `FORWARDED_ALLOW_IPS="*"` é recusado na partida.
- `FORWARDED_ALLOW_IPS` padrão: loopback + faixas privadas (cobre a rede docker do Traefik). Mais estrito:
  `docker network inspect easypanel -f '{{(index .IPAM.Config 0).Subnet}}'` e use só essa sub-rede.
- **Requisito do Traefik do Easypanel**: os entrypoints `http`/`https` **não** podem ter
  `forwardedHeaders.insecure=true` nem `forwardedHeaders.trustedIPs` com faixas públicas — no padrão o Traefik
  descarta o XFF do cliente e envia só o IP real. (Mesmo com `insecure`, a regra "mais à direita" do app ainda
  acha o IP certo — `infra/tests/test_traefik_forwarded.py` prova os dois casos com Traefik v2 e v3.)
- **CDN/Cloudflare na frente**: aí o Traefik vê o IP da Cloudflare. Configure `forwardedHeaders.trustedIPs` do
  entrypoint com as faixas da Cloudflare **e** acrescente essas faixas a `FORWARDED_ALLOW_IPS`; senão todos os
  visitantes compartilham o mesmo limite.
- nginx (alternativa ao Traefik, `infra/nginx/nginx.prod.conf`): sobrescreve `X-Forwarded-For` com `$remote_addr`
  em todas as rotas — nunca repassa o valor do cliente.

## 8. Observabilidade

### 8.1 Prometheus + Grafana
```bash
echo 'COMPOSE_PROFILES=monitoring' >> .env      # e GRAFANA_ADMIN_PASSWORD (>= 12 caracteres)
dc up -d prometheus grafana
ssh -L 3000:127.0.0.1:3000 deploy@VPS          # no seu computador → http://localhost:3000
```
Painel provisionado **FactoryConnect → FactoryConnect — Operação** (`infra/monitoring/grafana/dashboards/`,
gerado por `infra/monitoring/tools/build_dashboard.py`). Regras em `infra/monitoring/prometheus/alerts.yml`
(Prometheus → Alerts). Para receber notificação, crie um *contact point* no Grafana (e-mail/Telegram) e um alerta
sobre as mesmas consultas. Dev: `make monitoring` (Grafana `:3000`, Prometheus `:9090`). O Prometheus de dev
coleta o backend local em `host.docker.internal:8000`: suba o uvicorn com `--host 0.0.0.0` (com o padrão
`127.0.0.1` o container não o alcança e o alvo fica `down`; `infra/tests/test_monitoring.py` pula a coleta).
`/metrics` **não** é publicado no Traefik: o Prometheus coleta `server:8000` pela rede interna.

### 8.2 Métricas
| Métrica | Tipo | Origem |
|---|---|---|
| `http_requests_total{handler,method,status}` | counter | instrumentator (existe) |
| `http_request_duration_seconds_bucket{handler,method}` / `http_request_duration_highr_seconds_bucket` | histogram | instrumentator (existe) |
| `fm_mqtt_inflight_messages` | gauge | backend (existe) — backlog de leituras |
| `fm_mqtt_messages_total{result}` | counter | backend (`services/metrics.py`) — `result` = `ok`·`rejected` (tag desconhecida/outro tenant)·`invalid` (tópico/payload)·`error` |
| `fm_mqtt_processing_lag_seconds` | histogram | backend — fim do processamento − `ts` da leitura (leituras ao vivo; replays do buffer do edge ficam fora) |
| `fm_ingest_queue_depth` / `fm_ingest_queue_capacity` | gauge | backend (Sprint 5b) — leituras dentro do pipeline de ingestão (filas por tag + processando + escrita no Influx pendente) e o teto (`MQTT_MAX_INFLIGHT`); no teto o servidor para de ler e o broker guarda o resto |
| `fm_ingest_unacked` | gauge | backend — entregas QoS 1 ainda sem PUBACK (confirmadas em ordem, só depois de gravadas) |
| `fm_ingest_active_tags` | gauge | backend — tags com leituras na fila ordenada |
| `fm_ingest_latency_seconds` | histogram | backend — recebimento → leitura concluída (Influx + uso), toda leitura |
| `fm_ingest_dead_letters_total{stage}` | counter | backend — leitura válida que falhou depois das repetições (`lookup`·`influx`·`usage`·`process`), guardada em `ingest_dead_letters` |
| `fm_ingest_dropped_total` | counter | backend — leitura perdida (nem processada nem em dead letter). **Tem de ser 0** |
| `fm_ingest_retries_total{stage}`, `fm_influx_write_retries_total` | counter | backend — falhas transitórias repetidas (banco/Influx) |
| `fm_influx_write_batch_points` | histogram | backend — pontos por requisição de escrita no Influx |
| `fm_ingest_late_readings_total` | counter | backend — leituras gravadas muito depois do próprio `ts` (buffer do gateway, backlog): as janelas preditivas que elas tocam são recalculadas |
| `fm_downtime_events_total{kind}` | counter | backend — paradas criadas pela detecção (`stop`) e micro-paradas definitivas (`micro_stop`) |
| `process_resident_memory_bytes`, `process_cpu_seconds_total` | — | prometheus_client (existe) |

Todas as séries existem desde a primeira coleta (rótulos pré-criados, valor 0); `FmMqttNoMessages` também dispara
se a série sumir com o servidor no ar (`absent()`). `infra/tests/test_monitoring.py` confere que toda métrica
`fm_*` das regras e do painel é exportada pelo backend.

### 8.3 Uptime e saúde
- Containers: `dc ps` — `postgres`, `influxdb`, `mosquitto`, `server`, `site` têm healthcheck (o do Mosquitto
  autentica de verdade).
- Externo (de fora do VPS, ex. UptimeRobot/healthchecks.io, intervalo 1 min, alerta após 2 falhas):
  - `https://app.factoryconnect.tech/api/health` → 200
  - `https://factoryconnect.tech/healthz` → 200
  - TCP `mqtt.factoryconnect.tech:8883` (handshake TLS)
- Backup: `BACKUP_PING_URL` (seção 4.2) — alerta se o cron não rodar em 26 h.
- Demo pública (profile `demo`): `https://app.factoryconnect.tech/api/public/demo-status` com
  `"plant_running":true` — alerta se ficar falso por > 5 min (§10.4).
- Workflow **Deploy** também confere `PUBLIC_HEALTH_URL` (variable do repositório) após cada deploy.

## 9. Checklist de incidente

1. **Registrar**: abra uma nota com horário (UTC) de início, sintoma, quem está atuando.
2. **Escopo**: só um tenant/gateway? toda a API? só dados ao vivo? Veja o painel de operação e `dc ps`.
3. **Mudou algo?** Último deploy (`git log -1`, Actions → Deploy), rotação de segredo, renovação de certificado.
   Se coincide com deploy: **rollback primeiro** (seção 3), investigação depois.
4. **Sintomas comuns**:
   - API fora: `docker logs fm_server --tail 200`; `dc up -d server`; banco cheio? `df -h`, `docker system df`.
   - Dados ao vivo parados: `FmMqttNoMessages`/backlog → `docker logs fm_mosquitto | tail` (`not authorised`?
     certificado expirado?), `fm_mqtt_inflight_messages`/`fm_ingest_queue_depth` alto → servidor não acompanha
     (`MQTT_MAX_CONCURRENCY` = workers do pipeline, CPU); no teto (`MQTT_MAX_INFLIGHT`) o backlog passa para a fila do
     broker (sessão persistente `MQTT_CLIENT_ID`) — olhar `$SYS/broker/store/messages/count`. **O broker tem de rodar
     com `max_queued_messages`/`max_queued_bytes` de `infra/mosquitto/*.conf`**: com o padrão do Mosquitto (1000) o
     log mostra `Outgoing messages are being dropped for client …` e leituras se perdem no broker (visto no QA do
     Sprint 5b: container iniciado antes da mudança do arquivo — `docker restart fm_mosquitto` ou `kill -HUP`).
   - `fm_ingest_dead_letters_total` > 0: leituras guardadas em `ingest_dead_letters` (banco/Influx fora além de
     `MQTT_TRANSIENT_RETRY_S`, ou erro de programa — ver o log `ingest.dead_letter`). Corrigida a causa:
     `cd server && python -m src.services.ingest.dead_letter --replay [--tenant <id>]` (idempotente).
   - Gateway específico: credencial revogada? tenant errado no `.env` do gateway (ACL nega em silêncio)? relógio?
   - Latência alta: p95 por rota no painel; `docker stats`; consultas longas
     `docker exec fm_postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "select pid, now()-query_start, query from pg_stat_activity where state<>'idle' order by 2 desc limit 5"`.
   - Disco: backups em `/var/backups/factory-monitor` (`BACKUP_KEEP`), retenção do Prometheus, imagens (`docker image prune`).
5. **Suspeita de vazamento/invasão**: rotacione os segredos envolvidos (seção 5), revogue gateways suspeitos,
   preserve logs (`docker logs ... > incidente-<data>.log`) antes de reiniciar, avise o dono do projeto.
6. **Perda de dados**: pare a escrita (`docker stop fm_server`), **não** rode nada destrutivo; restaure o último
   backup bom em containers descartáveis primeiro para conferir (seção 4.3), depois em produção (4.4).
7. **Encerrar**: confirme recuperação (seção 2, "Depois"), registre causa, impacto (tempo, tenants) e ações
   para não repetir (teste, alerta, runbook).

## 10. Demo

Tenant **DEMO** somente-leitura do botão "Ver demonstração" do site (docs/sprint6.md §5). Não há dado fabricado
no banco: a planta virtual (`edge/virtual_plant`, modo `--demo`) alimenta o pipeline normal por um gateway próprio
(MQTT → Influx/Postgres → detectores → KPIs). Profile `demo` do `infra/docker-compose.prod.yml`:

| Serviço | O que faz | Rede |
|---|---|---|
| `demo-plant` (`fm_demo_plant`) | planta virtual em tempo real no fuso `America/Sao_Paulo` (roteiro abaixo); OPC UA 4840, Modbus 5020, estado/saúde 8091, API de controle 7070 (exige `X-Control-Token`) | só `fm-demo` (interna, sem internet, sem banco) |
| `demo-gateway` (`fm_demo_gateway`) | gateway da frota (`python -m src.gateway`): config e heartbeat em `http://server:8000`, MQTT em `mosquitto:1883` com usuário próprio | `fm-net` + `fm-demo` |
| `server` | o demo-bot conclui a OS da troca e chama `POST http://demo-plant:7070/replace` | também em `fm-demo` |

Imagem `factory-monitor-edge:0.6.0` (build local de `edge/`, base `python:3.12.15-slim` fixada; troque de propósito
com `DEMO_EDGE_IMAGE_TAG`). `restart: always` nos dois; a planta tem healthcheck (`/health`), o gateway o
healthcheck da imagem (arquivo de saúde tocado a cada heartbeat). Sem os segredos abaixo os containers **não sobem**
(saem com código 2 e a mensagem do que falta — `docker logs fm_demo_plant`).

### 10.1 Bootstrap (uma vez)
```bash
# 1) tenant demo, planta, linhas, máquinas, tags, turnos 3×8 h (America/Sao_Paulo), monitores preditivos,
#    usuários demo-bot e visitante, gateway da demo — idempotente; credenciais do gateway impressas UMA vez
docker exec -it fm_server python -m src.demo bootstrap        # anote tenant_id, gateway_id e (opção B) o token
# 2) credencial MQTT própria, restrita a factory/<tenant demo>/# (ACL do broker, §6.1)
make mqtt-gateway-add GATEWAY=gw-demo TENANT=<tenant_id do passo 1>
# 3) .env do VPS (modo 600):
#    DEMO_GATEWAY_MQTT_USERNAME=gw-demo   DEMO_GATEWAY_MQTT_PASSWORD=<senha impressa no passo 2>
#    DEMO_VIRTUAL_PLANT_CONTROL_TOKEN=$(openssl rand -hex 24)
#    DEMO_VIRTUAL_PLANT_CONTROL_URL=http://demo-plant:7070     # lido pelo servidor (demo-bot)
#    COMPOSE_PROFILES=demo            # ou monitoring,demo
dc up -d server                       # o servidor passa a ver DEMO_VIRTUAL_PLANT_CONTROL_URL/_TOKEN
# o demo-bot manda X-Control-Token em POST /replace; resposta não-2xx fica em audit_events
# (demo_bot.virtual_plant_replace_failed) e o bot deixa a posição em paz por DEMO_BOT_FAILURE_BACKOFF_S (6 h)
```

### 10.2 Cadastro do gateway da demo
- **Opção A — código de cadastro** (mesmo fluxo de um cliente, docs/gateways.md): o tenant demo não tem admin que
  entre com senha, então gere o código pela CLI — `docker exec fm_server python -m src.demo enroll-code` (vale 24 h,
  uso único; equivale a `POST /api/gateways/{gateway_id}/enroll-token`) — e ponha `DEMO_GATEWAY_ENROLL_CODE=<código>`
  no `.env`. Na 1ª partida o gateway troca o código pelo token e grava a
  credencial em `/data/gateway_credentials.json` (0600, volume `demo-gateway-data`); depois o código pode sair do
  `.env` (é ignorado). A senha MQTT do cadastro **não** é usada: o broker só aceita o usuário do passo 2.
- **Opção B — token do bootstrap**: `DEMO_GATEWAY_ID=<gateway_id>` e `DEMO_GATEWAY_TOKEN=<token>` impressos no
  passo 1 (ou `python -m src.demo bootstrap --rotate-gateway` para gerar outro; o anterior deixa de valer).

```bash
dc up -d demo-plant demo-gateway
dc ps demo-plant demo-gateway                                   # os dois healthy
docker logs fm_demo_gateway | grep -E "enrollment.done|edge_gateway.config"
docker exec fm_demo_plant python -c "import urllib.request,sys; sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8091/health').read().decode())"
curl -fsS https://app.factoryconnect.tech/api/public/demo-status   # "plant_running": true em ~1 min
```

### 10.3 Roteiro da planta (edge/virtual_plant/demo.py)
- Relógio da planta = hora local de São Paulo (alinhado na partida, `--speed 1` fixo); turnos 06–14, 14–22, 22–06
  todos os dias, com refeição e 2 cafés (`PLN-01`) — o mesmo calendário que o bootstrap cadastra.
- Falhas com código ≈ 3–5 por turno na planta toda (`DEMO_FAULT_MTBF_SCALE`, padrão 2,5), mais as por desgaste;
  um setup `SET-01` por turno em cada máquina; micro-paradas (15–110 s).
- **Cilindro B3** (Estação de Fixação): 12 golpes por ciclo (`DEMO_B3_STROKES_PER_CYCLE`, ≈ 17–20 mil golpes/dia).
  O servidor estabelece a linha de base (5 + 20 blocos de 1 000 golpes) no 1º dia e meio; a deriva começa em
  30 000 golpes (+2 ms/1 000) e cruza 110 % da referência ≈ meio dia depois → alerta preditivo + RUL. O demo-bot
  converte o alerta em OS, executa a troca e chama `/replace`: o desgaste recomeça (ciclo de ≈ 2,5 dias). Sem a
  troca o cilindro falharia (447) em +40 ms e o "plantão" automático da planta o trocaria.
- O desgaste do B3 (golpes desde a instalação, contador do CLP, nº da troca) é gravado a cada 30 s em
  `/data/demo_state.json` (volume `demo-plant-data`): `docker restart fm_demo_plant` não zera o ciclo. Para recomeçar
  do zero: `dc stop demo-plant && docker run --rm -v factory-monitor_demo-plant-data:/data alpine rm /data/demo_state.json && dc up -d demo-plant`.

### 10.4 Saúde e alerta
- `GET :8091/health` (healthcheck do container, a cada 30 s): 503 quando o tempo simulado parou (> 30 s), o atraso
  passa de 60 s ou o relógio da planta se afastou > 5 min da hora de São Paulo. Desvio > 10 min (fuso/horário de
  verão mudou, relógio do host corrigido) → a planta grava o estado e sai com código 3; o `restart: always` a sobe
  realinhada.
- **Alerta para devops** (parada > 5 min): monitor externo (UptimeRobot/healthchecks.io, tipo *keyword*, a cada
  1 min) em `https://app.factoryconnect.tech/api/public/demo-status` esperando `"plant_running":true`, alertando
  após 5 falhas seguidas. O servidor marca `plant_running=false` quando nenhuma leitura da demo chega há
  `DEMO_PLANT_STOPPED_AFTER_S` (300 s) — cobre planta, gateway, broker e ingestão de uma vez — e a faixa da demo
  passa a dizer "planta simulada temporariamente parada".
- Diagnóstico: `dc ps demo-plant demo-gateway`; `docker logs --tail 100 fm_demo_plant` (`demo.clock_misaligned`?);
  `docker logs --tail 100 fm_demo_gateway` (`not authorised` no broker → passo 2; `HTTP 401` → token/código, §10.2);
  `docker logs fm_server | grep demo_bot` (`virtual_plant_replace_failed` → URL/token do §10.1).

### 10.5 Rotação e desligamento
- Token da API de controle: novo `DEMO_VIRTUAL_PLANT_CONTROL_TOKEN` → `dc up -d server demo-plant`.
- Senha MQTT do gateway da demo: `make mqtt-gateway-add GATEWAY=gw-demo TENANT=<uuid>` → `.env` → `dc up -d demo-gateway`.
- Token do gateway: `python -m src.demo bootstrap --rotate-gateway` (opção B) ou novo código
  (`python -m src.demo enroll-code`, opção A; apague
  `/data/gateway_credentials.json` do volume `demo-gateway-data`).
- Desligar a demo: tire `demo` de `COMPOSE_PROFILES` e `dc stop demo-plant demo-gateway` (o site continua com o
  botão; a sessão abre com a faixa "planta simulada temporariamente parada").

