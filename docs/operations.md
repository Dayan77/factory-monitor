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
| `fm_prometheus`, `fm_alertmanager`, `fm_grafana`, `fm_node_exporter`, `fm_sql_exporter`, `fm_blackbox` | profile `monitoring` (§8) | Grafana `127.0.0.1:3000`, Alertmanager `127.0.0.1:9093` (túnel SSH) |
| `fm_demo_plant`, `fm_demo_gateway` | profile `demo`: planta virtual + gateway do tenant DEMO (§10) | nada publicado (rede interna `fm-demo`) |

Arquivos fora do git no VPS: `.env` (segredos), `mqtt/gateways.conf` e `mqtt/certs/` (MQTT),
`backups/pre-deploy/` (backups automáticos do deploy), `backups/metrics/` (idade do backup para o Prometheus),
`.deploy/` (versão no ar e histórico de deploys) e `/var/backups/factory-monitor/` (backups noturnos).
Staging (§12): mesmo VPS, checkout `/opt/factory-monitor-staging`, containers `fm_stg_*`.

> Portas publicadas pelo Docker **ignoram o UFW** (o Docker escreve as regras de iptables antes dele).
> O que limita a exposição é o `ports:` do compose — por isso tudo que é interno é `127.0.0.1:` ou não é publicado.

## 2. Deploy

Caminho normal (detalhes da release em §11):

1. merge na `main` com CI e **Security** verdes;
2. tag `vX.Y.Z` igual ao `VERSION`, com seção no `CHANGELOG.md` → workflow **Release** constrói, varre (Trivy),
   gera SBOM e publica `ghcr.io/dayan77/factoryconnect-{server,web,site,edge}:X.Y.Z` com proveniência;
3. **Actions → Deploy → Run workflow** com `version=vX.Y.Z`, `target=staging` → smoke em
   `https://staging.factoryconnect.tech` (§12);
4. mesmo workflow com `target=production` → **aguarda a aprovação do dono do projeto** (environment
   `production`, *required reviewers*) → `scripts/remote_deploy.sh` no VPS por SSH (usuário dedicado, chave do
   environment).

O que o `remote_deploy.sh <ref>` faz (produção: `/opt/factory-monitor`, staging: `DEPLOY_TARGET=staging`):

1. resolve a versão: tag `vX.Y.Z` → `FM_VERSION=X.Y.Z`; sha → `sha-<12 hex>`. Produção só aceita commit da
   `main` (`ALLOW_NON_MAIN=1` para hotfix excepcional). Commits locais no VPS abortam;
2. `git reset --keep` no commit alvo (preserva arquivos locais não commitados; funciona para frente **e para trás**);
3. pré-requisitos: credenciais do broker, gateways cadastrados, TLS, `compose config` (§6.4);
4. imagens: com `FM_IMAGE_PREFIX` no `.env` → `docker compose pull` das imagens da release (login temporário no
   GHCR com o token do job, logout em seguida); sem ele → build local com a tag da versão. **Nunca `:latest`**;
5. **backup do Postgres** em `backups/pre-deploy/` (mantém 5; `PRE_DEPLOY_BACKUP=0` pula) — falhou → aborta;
6. guarda a revisão atual do banco (`alembic current` → `.deploy/<alvo>.alembic-before`);
7. `alembic upgrade head` com **timeout** (`MIGRATION_TIMEOUT_S`, padrão 600 s; input do workflow) — falhou ou
   estourou → aborta **sem trocar containers** (a migration que falhou é desfeita pela transação);
8. `up -d`; healthcheck em `/api/health` por até 2 min;
9. ok → grava `.deploy/<alvo>.current` (`versão sha`) e `.deploy/history.log`; falhou → **rollback automático**
   (§3).

Manual (do seu computador, mesmo script):
`ssh deploy@VPS 'DEPLOY_TARGET=production bash -s' -- v0.7.0 /opt/factory-monitor < scripts/remote_deploy.sh`.

Antes de aprovar um deploy em produção, confira:
- [ ] a mesma tag rodou em **staging** e passou no smoke (§12);
- [ ] CI, Security e Release verdes na tag (Trivy sem HIGH/CRITICAL corrigível; SBOM anexado à GitHub Release);
- [ ] migrations novas são **compatíveis com o código anterior** (expand/contract: aditivas; `DROP`/rename só numa
      release seguinte, quando nenhum código ativo depende da coluna);
- [ ] variáveis novas do `.env` já estão no VPS (compare com `.env.prod.example`; o compose falha com
      mensagem clara se faltar `MQTT_USERNAME`/`MQTT_PASSWORD`, por exemplo);
- [ ] janela combinada se houver mudança no broker MQTT (gateways reconectam).

Depois: `dc ps` (tudo `healthy`), `curl -fsS https://app.factoryconnect.tech/api/health`, painel
**FactoryConnect — Operação** sem alerta, mensagens MQTT chegando (§8), `cat .deploy/production.current`.

## 3. Rollback

**Automático**: se o healthcheck falhar, `remote_deploy.sh` volta o código (`git reset --keep`) e sobe as imagens
da **versão anterior registrada** (`.deploy/<alvo>.current`; no primeiro deploy versionado, as `:latest` antigas,
que o deploy novo não sobrescreve). A saída do job mostra `rollback saudável` ou pede intervenção.

**Manual** (o deploy passou no healthcheck, mas a versão está ruim): rode o workflow **Deploy** com a **tag
anterior** (`version=v0.6.x`) — as imagens dela continuam no GHCR (e no VPS) e o script faz o caminho de volta
como qualquer deploy (com backup pré-deploy e migrations, que não fazem nada se o schema já está à frente).
Sem GitHub (emergência), no VPS:

```bash
# do seu computador (mesmo script do workflow):
ssh deploy@VPS 'bash -s' -- v0.6.1 /opt/factory-monitor < scripts/remote_deploy.sh
# ou direto no VPS, sem migrations nem backup (só troca código e containers):
tail -5 .deploy/history.log                                         # versões e commits que já rodaram
git reset --keep <sha-bom> && FM_VERSION=<versão-boa> dc up -d --no-build server web site
```

**Migrations** — não são revertidas automaticamente (por isso a regra expand/contract acima: o código anterior
roda com o schema novo). Tempo máximo: `MIGRATION_TIMEOUT_S`; cada migration roda numa transação (DDL transacional
do Postgres), então uma falha no meio desfaz só aquela migration. Para desfazer de propósito:

1. **migration reversível e sem perda** (ex.: coluna nova): com o código anterior no ar,
   `dc run --rm -T server alembic downgrade <revisão>` — a revisão anterior está em
   `.deploy/production.alembic-before`;
2. **migration que apagou/transformou dados**: restaure o backup pré-deploy com o código anterior:

```bash
ls -1t backups/pre-deploy/        # o mais recente é o feito antes do deploy problemático
docker stop fm_server
RESTORE_CONFIRM=yes RESTORE_SKIP_INFLUX=1 scripts/restore.sh backups/pre-deploy/fm-backup-<UTC>.tar
FM_VERSION=<versão-boa> dc up -d server
```
O backup pré-deploy é só do Postgres (o Influx não muda com migration). Dados escritos entre o backup e o restore
se perdem — registre a janela no incidente (§9).

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
17 3 * * * cd /opt/factory-monitor && BACKUP_DIR=/var/backups/factory-monitor BACKUP_KEEP=14 BACKUP_PASSPHRASE_FILE=/home/fmonitor/.fm-backup-pass BACKUP_UPLOAD_CMD='rclone copy "$1" b2:fm-backups/' BACKUP_PING_URL=https://hc-ping.com/<uuid> BACKUP_METRICS_FILE=/opt/factory-monitor/backups/metrics/fm_backup.prom scripts/backup.sh 2>&1 | logger -t fm-backup
```
- `BACKUP_METRICS_FILE` alimenta os alertas `FmBackupTooOld`/`FmBackupFailed` (node-exporter lê
  `BACKUP_METRICS_DIR`, padrão `/opt/factory-monitor/backups/metrics`, §8).
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

### 8.1 Stack (profile `monitoring`)
| Container | Função |
|---|---|
| `fm_prometheus` | coleta e regras (`alerts.yml` = backend, `alerts.platform.yml` = plataforma); retenção 30 dias **ou** 5 GB (`PROMETHEUS_RETENTION`, `PROMETHEUS_RETENTION_SIZE`) |
| `fm_alertmanager` | agrupa e envia os alertas por **e-mail** (§8.3); UI em `127.0.0.1:9093` |
| `fm_grafana` | painel **FactoryConnect — Operação**; `127.0.0.1:3000` |
| `fm_node_exporter` | disco/CPU/memória do VPS + textfile collector (métricas do backup) |
| `fm_sql_exporter` | consultas de negócio no Postgres: gateways sem contato, dead letters pendentes, demo |
| `fm_blackbox` | sondas HTTPS públicas (app, site, staging; validade do certificado) e `/health` da planta da demo |

```bash
# .env: COMPOSE_PROFILES=monitoring, GRAFANA_ADMIN_PASSWORD (>= 12), ALERT_EMAIL_TO + SMTP_* (§8.3)
dc up -d prometheus alertmanager grafana node-exporter sql-exporter blackbox-exporter
ssh -L 3000:127.0.0.1:3000 -L 9093:127.0.0.1:9093 -L 9090:127.0.0.1:9090 deploy@VPS   # Grafana, Alertmanager
```
(Prometheus não publica porta; para a UI: `docker exec fm_prometheus wget -qO- localhost:9090/api/v1/alerts` ou
acrescente `127.0.0.1:9090:9090` temporariamente.) Painel provisionado de
`infra/monitoring/grafana/dashboards/` (gerado por `infra/monitoring/tools/build_dashboard.py`). Dev:
`make monitoring` (Grafana `:3000`, Prometheus `:9090`; sem Alertmanager/exporters). O Prometheus de dev coleta o
backend local em `host.docker.internal:8000`: suba o uvicorn com `--host 0.0.0.0`.
`/metrics` **não** é publicado no Traefik: o Prometheus coleta `server:8000` pela rede interna.
Papel só de leitura do sql-exporter: docs/go_live.md §7.

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
| `fm_detector_lag_seconds{detector,tenant_id,entity_id}` / `fm_detector_lag_max_seconds{detector}` / `fm_detector_lagging{detector}` | gauge | backend (Sprint 7, `services/oee/lag.py`) — atraso do detector de paradas e da preditiva em relação aos próprios dados (checkpoint); cresce depois de uma queda longa e volta a 0 no catch-up |
| `process_resident_memory_bytes`, `process_cpu_seconds_total` | — | prometheus_client (existe) |
| `fm_gateway_seconds_since_seen{tenant,gateway}`, `fm_gateway_heartbeat_seconds` | gauge | **sql-exporter** (`infra/monitoring/sql-exporter`) — gateways cadastrados (status ≠ `pending`) |
| `fm_ingest_dead_letters_pending` | gauge | sql-exporter — dead letters ainda não reprocessadas (estoque; o counter acima conta as novas) |
| `fm_demo_last_data_age_seconds` | gauge | sql-exporter — idade do último dado das máquinas do tenant `demo-publica` (sem linha = demo não instalada) |
| `fm_backup_last_success_timestamp_seconds`, `_size_bytes`, `_duration_seconds`, `fm_backup_last_failure_timestamp_seconds` | gauge | `scripts/backup.sh` → textfile do **node-exporter** (`BACKUP_METRICS_FILE`) |
| `node_filesystem_avail_bytes` / `_size_bytes` | gauge | node-exporter — disco do VPS |
| `probe_success`, `probe_ssl_earliest_cert_expiry`, `probe_duration_seconds` | gauge | **blackbox-exporter** — `https://app…/api/health`, `https://factoryconnect.tech/healthz`, staging e `demo-plant:8091/health` |

Todas as séries existem desde a primeira coleta (rótulos pré-criados, valor 0); `FmMqttNoMessages` também dispara
se a série sumir com o servidor no ar (`absent()`). `infra/tests/test_monitoring.py` confere que toda métrica
`fm_*` das regras e do painel é exportada pelo backend (as da plataforma: pelo exporter indicado — as consultas
do sql-exporter rodam contra o schema real em `test_sql_exporter_queries_run_against_the_real_schema`).
**Atenção**: com `MQTT_INGEST=process` ou `PREDICTIVE_WORKER=process`, métricas que nascem dentro dos processos
filhos (ingestão, detectores) ficam no `WORKER_METRICS_PORT` deles, que hoje só escuta em `127.0.0.1` do
container — o Prometheus não alcança. Em produção mantenha `MQTT_INGEST=inline` (padrão) até o backend expor
essas portas (pendência registrada no Sprint 7); `fm_detector_lag_*` vem do processo da API e não é afetada.

### 8.3 Alertas por e-mail (Alertmanager)
`infra/monitoring/alertmanager/alertmanager.yml.tmpl` é renderizado na partida por `entrypoint.sh` a partir do
`.env`: `ALERT_EMAIL_TO` (obrigatório — sem ele o container **não sobe**, e o Prometheus mostra
`FmAlertmanagerDown`), `ALERT_EMAIL_CRITICAL_TO` (padrão = `ALERT_EMAIL_TO`), `ALERT_EMAIL_FROM` (padrão
`SMTP_FROM`) e o SMTP já usado pelos leads (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`; SendGrid:
`smtp.sendgrid.net`, usuário `apikey`). A senha vai para um arquivo próprio no volume, não para a config.
Roteamento: `critical` repete a cada 1 h, `warning` a cada 12 h; `FmServerDown` suprime os alertas que dependem
das métricas do backend. Teste de ponta a ponta depois de configurar:
```bash
docker exec fm_alertmanager amtool alert add FmTesteManual severity=warning summary="teste do go-live" \
  --alertmanager.url=http://localhost:9093      # o e-mail chega em ~1 min; some sozinho em 5 min
```
Silenciar durante manutenção: `amtool silence add alertname=~".+" --duration=1h --comment="janela"`.

### 8.4 Alertas e o que fazer (runbook)
| Alerta | Sev. | Significa | Primeiro passo |
|---|---|---|---|
| `FmServerDown` | crit | Prometheus não coleta o backend | `dc ps server`, `docker logs fm_server --tail 200`; deploy recente? → §3 |
| `FmHttp5xxRate` / `FmHttp5xxRateCritical` | warn/crit | > 5% / > 20% de respostas 5xx | painel "p95 por rota"; log `request_id` dos 500 |
| `FmHttpLatencyP95High` | warn | p95 > 1 s | consultas longas (`pg_stat_activity`, §9), `docker stats` |
| `FmMqttNoMessages` | warn | nenhuma leitura em 15 min | broker (`docker logs fm_mosquitto`), credenciais, gateways |
| `FmMqttBacklog`, `FmIngestQueueSaturated` | warn/crit | fila de ingestão crescendo / > 80% | CPU, Influx lento, `MQTT_MAX_CONCURRENCY`; o broker segura o resto |
| `FmMqttProcessingLagHigh`, `FmIngestionLagHigh` | warn | leituras demorando a ser gravadas | idem; buffer de gateway descarregando é normal por alguns minutos |
| `FmIngestDeadLetters`, `FmDeadLettersPending` | warn | leituras guardadas em `ingest_dead_letters` | causa no log `ingest.dead_letter`; corrigida: replay (§9) |
| `FmIngestDropped` | crit | leitura **perdida** | bug: preserve logs, abra incidente (§9) |
| `FmDetectorLagHigh`, `FmDetectorLagging` | warn | detector de paradas/preditiva atrás dos dados | normal logo depois de uma queda longa (catch-up); persistindo: `docker logs fm_server \| grep -i detector` |
| `FmGatewayOffline` | warn | gateway sem contato > 6 heartbeats | avise o cliente; energia/rede da planta? `make mqtt-gateway-list`; credencial revogada? |
| `FmDiskSpaceLow` / `Critical` / `FmDiskWillFillIn24h` | warn/crit | disco do VPS | §9 "Disco"; `docker system df`; retenção do Influx/Prometheus |
| `FmBackupTooOld`, `FmBackupNeverReported`, `FmBackupFailed` | crit/warn | sem backup completo em 26 h / falhou | `journalctl -t fm-backup --since -2d`; rode `scripts/backup.sh` à mão (§4) |
| `FmDemoPlantDown`, `FmDemoDataStale` | warn | demo pública parada | §10.4 |
| `FmPublicEndpointDown` | crit | app/site/staging não responde 200 por HTTPS | Traefik/Easypanel, DNS, certificado |
| `FmTlsCertificateExpiring` | warn | certificado vence em < 14 dias | renovação do Let's Encrypt no Traefik (logs do Easypanel) |
| `FmAlertmanagerDown`, `FmNodeExporterDown`, `FmSqlExporterDown` | crit/warn | o próprio monitoramento | `dc ps`, `.env` (ALERT_EMAIL_TO/SMTP), `docker logs fm_<nome>` |

### 8.5 Logs e retenção
- Containers: driver `json-file` com rotação — até `LOG_MAX_FILE` (5) × `LOG_MAX_SIZE` (20 MB) por container em
  produção, 3 × 10 MB em staging. Ver: `docker logs fm_server --since 1h`; nada de log fora do Docker.
- Prometheus: 30 dias ou 5 GB (o que vier primeiro). Alertmanager: 120 h de silêncios/notificações.
- Backups: `BACKUP_KEEP` (14 locais) + cópia off-site (§4.2). Pré-deploy: 5. Logs do cron: `journalctl -t fm-backup`
  (retenção do journald do sistema).
- Dados de aplicação (auditoria, sessões, leads): retenção em docs/privacy.md §4.
- Retenção do Influx: bucket `sensors` sem expiração em produção (histórico de uso dos componentes); staging 30 dias.

### 8.6 Uptime externo e saúde
- Containers: `dc ps` — `postgres`, `influxdb`, `mosquitto`, `server`, `site` têm healthcheck (o do Mosquitto
  autentica de verdade).
- O blackbox-exporter sonda os endpoints públicos **de dentro do VPS** (DNS + Traefik + TLS). Isso não pega o VPS
  inteiro fora do ar (o próprio Prometheus cai junto), então mantenha **também** um monitor externo
  (UptimeRobot/healthchecks.io, intervalo 1 min, alerta após 2 falhas):
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
- **Alerta para devops** (parada > 5 min): com o profile `monitoring`, `FmDemoPlantDown` (sonda do `/health` da
  planta pelo blackbox) e `FmDemoDataStale` (sem dado do tenant demo há 10 min) chegam por e-mail (§8.4). Além
  disso, monitor externo (UptimeRobot/healthchecks.io, tipo *keyword*, a cada
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

## 11. Releases e versionamento

- **SemVer** no arquivo `VERSION` (sem `v`); tag `vX.Y.Z` (pré-release: `vX.Y.Z-rc.N`). MAJOR = quebra a API
  pública, o protocolo do gateway ou exige ação manual na migração; MINOR = funcionalidade compatível (um sprint);
  PATCH = correção. Critérios no topo do `CHANGELOG.md` (Keep a Changelog).
- **Cortar uma release** (na `main`, depois do merge do sprint):
  ```bash
  # 1) VERSION + CHANGELOG: mova o conteúdo de [Unreleased] para "## [X.Y.Z] - AAAA-MM-DD"
  make release-check TAG=vX.Y.Z           # confere VERSION, tag, seção datada e ordem do changelog
  git commit -am "chore: release vX.Y.Z" && git tag -a vX.Y.Z -m "FactoryConnect X.Y.Z" && git push --follow-tags
  ```
  O workflow **Release** (`.github/workflows/release.yml`) então: confere de novo (tag na `main`, VERSION,
  CHANGELOG) → para cada imagem (`server`, `web`, `site`, `edge` amd64+arm64) build → **Trivy** (HIGH/CRITICAL com
  correção reprovam antes de publicar) → **SBOM CycloneDX** → push no GHCR com **proveniência SLSA**
  (`provenance: mode=max`, SBOM do buildx e attestation assinada) → GitHub Release com as notas e os SBOMs.
- **Tags das imagens**: `ghcr.io/dayan77/factoryconnect-<nome>:X.Y.Z`, `:X.Y` e `:sha-<commit>`; nunca `:latest`.
  Conferir a origem de uma imagem: `gh attestation verify oci://ghcr.io/dayan77/factoryconnect-server:X.Y.Z --owner Dayan77`.
- **Visibilidade do pacote**: o primeiro push cria os pacotes como privados; o deploy faz login temporário com o
  token do job (`packages: read`). Gateways em campo puxam `factoryconnect-edge` — torne só esse pacote público
  (Packages → factoryconnect-edge → Package settings) ou use um token de leitura no IPC.
- **Hotfix**: branch da tag (`hotfix/vX.Y.Z+1`), PR para a `main`, release normal. Produção só aceita commits da
  `main` (`ALLOW_NON_MAIN=1` no script é exceção registrada no incidente).

## 12. Staging (pré-produção no mesmo VPS)

`infra/docker-compose.staging.yml`: projeto compose `fm-staging`, containers `fm_stg_*`, volumes `fm-staging_*`,
rede `fm-stg-net`, broker próprio (só `127.0.0.1:11883`), routers Traefik `fm-stg-*` para
`staging.factoryconnect.tech` com `X-Robots-Tag: noindex`. Sem site, sem demo, sem monitoramento próprio (o
Prometheus da produção sonda `https://staging.factoryconnect.tech/api/health`). Mesmas imagens da produção — o
que passou em staging vai para a produção sem rebuild. Preparação (uma vez): docs/go_live.md §3.

```bash
alias dcs='docker compose --env-file /opt/factory-monitor-staging/.env -f infra/docker-compose.staging.yml'
cd /opt/factory-monitor-staging && dcs ps
```
Smoke depois de cada deploy em staging (5 min): login, dashboard, uma máquina com dados (simulador apontando para
o broker de staging por túnel SSH: `ssh -L 11883:127.0.0.1:11883 deploy@VPS`), um relatório PDF, e
`curl -sI https://staging.factoryconnect.tech | grep -i -E 'strict-transport|content-security|x-robots'`.
Banco de staging: dados sintéticos ou cópia **anonimizada** da produção — nunca dados pessoais reais de clientes
(LGPD, docs/privacy.md). Recriar do zero: `dcs down -v` (apaga só os volumes `fm-staging_*`).

## 13. Segurança da cadeia de suprimentos e cabeçalhos

| O quê | Onde | Quando reprova |
|---|---|---|
| Segredos no **histórico inteiro** do git | gitleaks — `.github/workflows/security.yml`, config `.github/security/gitleaks.toml` | qualquer achado fora de `.github/security/gitleaksignore` |
| Dependências Python (server, edge, scripts) | pip-audit → `scripts/security/audit_gate.py` | qualquer vulnerabilidade sem aceite em `.github/security/audit-allowlist.toml` |
| Dependências npm (web, site) | `npm audit` → mesmo portão | high/critical sem aceite (moderate/low: aviso) |
| Imagens (server, web, site, edge) | Trivy no job `docker` do CI e na Release | HIGH/CRITICAL **com correção**, salvo `.github/security/trivyignore` |
| SBOM CycloneDX | Security (fontes) · CI e Release (imagens) | — (artefatos; anexados à GitHub Release) |
| Atualizações | Dependabot (`.github/dependabot.yml`): pip, npm, Docker, docker-compose, Actions | — (PRs semanais; segurança imediata) |
| Cabeçalhos HTTP | `web/nginx/security-headers.conf`, `site/nginx/security-headers.conf`; API: `server/src/services/security/http.py` | `infra/tests/test_security_headers.py` (nginx real + Chromium sem violação de CSP) |

Regras:
- **Corrigir antes de aceitar**: bump da versão fixada e CI verde. Aceite só sem correção ou com migração grande,
  sempre com motivo, escopo (`runtime`/`dev`) e `expires` (máx. ~1 ano; vencido reprova o CI).
- **Segredo encontrado** (gitleaks ou olho humano): **rotacione primeiro** (§5), depois registre o fingerprint em
  `gitleaksignore` com o comentário do que foi rotacionado e quando. Apagar o commit não basta. O vazamento
  histórico da senha do VPS (`cc1d1e7`) está registrado assim — conferência no go-live (docs/go_live.md §1).
- Local: `make security` (gitleaks + pip-audit + npm audit + cabeçalhos), ou só `make deps-audit` / `make secrets-scan`.
- **CSP**: app com `script-src 'self'` (nenhum script inline — `theme-init.js`/`manifest-init.js` são arquivos);
  site com o hash SHA-256 de cada script inline do build (`site/scripts/render-nginx.mjs` no `Dockerfile`).
  Script inline novo no site entra sozinho no próximo build; no app, use arquivo em `public/`.
  `style-src` mantém `'unsafe-inline'` (atributos `style=` do React/Recharts e CSS embutido do Astro — sem execução).
- **HSTS** `max-age=63072000; includeSubDomains; preload` em app e site. Antes de submeter `factoryconnect.tech` a
  hstspreload.org, todos os subdomínios (app, staging, mqtt, www) precisam responder em HTTPS — a inclusão na
  lista dos navegadores é difícil de desfazer.
- **TLS**: no caminho principal o TLS termina no Traefik do Easypanel (padrão: TLS 1.2+, cifras modernas; HTTP→HTTPS
  pelos routers `*-http`). Na alternativa nginx (`infra/nginx/nginx.prod.conf`): só TLS 1.2/1.3, ECDHE+AEAD,
  sem session tickets, OCSP stapling. Meta no SSL Labs: **A+**.

