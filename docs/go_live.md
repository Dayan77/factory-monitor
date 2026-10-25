# Go-live — checklist do dono do projeto

Passo a passo para colocar o FactoryConnect em produção em `factoryconnect.tech` (VPS com Easypanel/Traefik).
Faça **na ordem**; cada item tem como conferir. Comandos no VPS rodam em `/opt/factory-monitor` com o usuário da
aplicação (`fmonitor`, grupo `docker`), salvo indicação. Detalhes de cada assunto: [operations.md](operations.md).

```bash
alias dc='docker compose --env-file /opt/factory-monitor/.env -f infra/docker-compose.prod.yml'
alias dcs='docker compose --env-file /opt/factory-monitor-staging/.env -f infra/docker-compose.staging.yml'
```

> Nenhum agente aprova deploy em produção. O workflow **Deploy** para no environment `production` até **você**
> aprovar.

---

## 1. Acesso ao VPS e o vazamento antigo

O commit `cc1d1e7` (abril/2026) publicou a senha SSH do VPS no histórico do git. Ele continua lá (registrado em
`.github/security/gitleaksignore`), então a senha antiga deve ser considerada pública.

- [ ] **1.1** Senha do `root` (e de qualquer usuário que a reutilizava) **trocada** depois de abril/2026:
      `sudo passwd root`. Se não tem certeza, troque agora.
- [ ] **1.2** Login SSH **só por chave** e sem root:
      `sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication)'` →
      `passwordauthentication no`, `permitrootlogin no` (ou `prohibit-password`), `kbdinteractiveauthentication no`.
      Ajuste em `/etc/ssh/sshd_config.d/99-fm.conf` e `sudo systemctl reload ssh` — **com uma segunda sessão
      aberta** para não se trancar fora.
- [ ] **1.3** Painel do provedor e do Easypanel com senha nova e 2FA. Confira `last -a | head` e
      `sudo journalctl -u ssh --since -30d | grep Accepted` por acessos estranhos.
- [ ] **1.4** Chave de deploy dedicada: `ssh-keygen -t ed25519 -f fm_deploy -C deploy@github` (no seu computador);
      a pública em `~fmonitor/.ssh/authorized_keys`; a privada só no secret `VPS_SSH_KEY` (item 5.2).
- [ ] **1.5** Firewall: `sudo ufw status` → só 22, 80, 443, 8883 (scripts/setup_vps.sh). Portas do Docker ignoram
      o UFW: `sudo ss -ltnp | grep -v 127.0.0.1` deve mostrar só sshd, Traefik (80/443) e o Mosquitto (8883; e 1883
      só se `MQTT_PLAIN_BIND=0.0.0.0` estiver em transição).

## 2. DNS

No provedor de DNS de `factoryconnect.tech` (TTL 300 durante a virada):

| Registro | Tipo | Valor | Para quê |
|---|---|---|---|
| `factoryconnect.tech` | A | IP do VPS | site público |
| `www.factoryconnect.tech` | A (ou CNAME → apex) | IP do VPS | redireciona para o apex |
| `app.factoryconnect.tech` | A | IP do VPS | app + API + WebSocket |
| `staging.factoryconnect.tech` | A | IP do VPS | pré-produção (§3) |
| `mqtt.factoryconnect.tech` | A | IP do VPS | gateways em campo (MQTT TLS 8883) |
| `factoryconnect.tech` | CAA | `0 issue "letsencrypt.org"` | só o Let's Encrypt emite certificado |

- [ ] **2.1** `dig +short app.factoryconnect.tech staging.factoryconnect.tech mqtt.factoryconnect.tech` → IP do VPS.
- [ ] **2.2** Depois do primeiro deploy (§5), os certificados saem sozinhos pelo Traefik:
      `curl -sI https://staging.factoryconnect.tech | head -1` → `HTTP/2 200`.
- [ ] **2.3** Só **depois** de tudo em HTTPS por algumas semanas: submeter o apex em https://hstspreload.org (o
      cabeçalho já vai pronto: `max-age=63072000; includeSubDomains; preload`). Difícil de desfazer — não antecipe.

## 3. Segredos e `.env` (produção e staging)

Gere **todos** novos, com `openssl rand -hex 32` (ou `-hex 24` onde indicado). Nada de valor de exemplo nem de dev.
Guarde uma cópia no seu cofre de senhas (não no VPS só).

- [ ] **3.1** Produção: `cp .env.prod.example .env && chmod 600 .env` e preencha:
      `APP_SECRET_KEY`, `POSTGRES_PASSWORD`, `INFLUX_TOKEN`, `INFLUX_ADMIN_PASSWORD`, `MQTT_PASSWORD` (≥ 16),
      `GRAFANA_ADMIN_PASSWORD` (≥ 12), `ENCRYPTION_KEY` (se os gateways cifram o payload),
      `DEMO_VIRTUAL_PLANT_CONTROL_TOKEN` (demo), SMTP/SendGrid, `LEADS_TO_EMAIL`, `ALERT_EMAIL_TO`,
      `FM_IMAGE_PREFIX=ghcr.io/dayan77/factoryconnect`, `COMPOSE_PROFILES=monitoring` (mais `,demo` no §9).
- [ ] **3.2** Staging, checkout próprio e segredos **diferentes**:
      ```bash
      sudo install -d -o fmonitor -g fmonitor /opt/factory-monitor-staging
      git clone https://github.com/Dayan77/factory-monitor.git /opt/factory-monitor-staging
      cd /opt/factory-monitor-staging && cp .env.staging.example .env && chmod 600 .env   # preencha
      ```
- [ ] **3.3** Conferir que nenhum valor de exemplo sobrou:
      `grep -nE '=(change-me|changeme|dev-token|fm_dev_password|Admin@1234|Super@1234)' .env` → nada.
- [ ] **3.4** `dc config -q && echo ok` e `cd /opt/factory-monitor-staging && dcs config -q && echo ok`.

## 4. GitHub: environments, secrets e proteção

- [ ] **4.1** Settings → Environments → **production**: *Required reviewers* = você; *Deployment branches and
      tags* = só tags `v*`; secrets `VPS_HOST`, `VPS_USER` (`fmonitor`), `VPS_SSH_KEY` (privada do item 1.4),
      `VPS_KNOWN_HOSTS` (`ssh-keyscan -H <host>` conferido contra `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
      no VPS); variables `VPS_APP_DIR=/opt/factory-monitor`, `PUBLIC_HEALTH_URL=https://app.factoryconnect.tech/api/health`.
- [ ] **4.2** Environment **staging**: mesmos secrets de SSH; `VPS_APP_DIR=/opt/factory-monitor-staging`,
      `PUBLIC_HEALTH_URL=https://staging.factoryconnect.tech/api/health`; sem reviewers.
- [ ] **4.3** Settings → Branches → `main`: exigir PR e os checks de **CI** e **Security**; bloquear force-push.
- [ ] **4.4** Settings → Code security: Dependabot alerts + security updates ligados; *Secret scanning* e *Push
      protection* ligados.
- [ ] **4.5** Settings → Actions → General: *Workflow permissions* = read (os workflows pedem o que precisam).

## 5. Primeiro deploy (staging → produção)

- [ ] **5.1** Release: na `main` atualizada, `make release-check TAG=v0.7.0`, depois
      `git tag -a v0.7.0 -m "FactoryConnect 0.7.0" && git push origin v0.7.0`. Acompanhe Actions → **Release** até o
      fim (Trivy verde, imagens no GHCR, GitHub Release com SBOMs).
- [ ] **5.2** Actions → **Deploy** → `version=v0.7.0`, `target=staging`. Depois: `dcs ps` (tudo healthy) e smoke
      do [operations.md §12](operations.md).
- [ ] **5.3** Actions → **Deploy** → `version=v0.7.0`, `target=production` → **aprove** no environment. O script
      faz backup pré-deploy, migrations com timeout e healthcheck; falhou → volta sozinho.
- [ ] **5.4** `cat /opt/factory-monitor/.deploy/production.current` → `0.7.0 <sha>`; `dc ps` tudo healthy.
- [ ] **5.5** **Meta de escala aberta — confirmar antes de abrir para clientes com mais de ~100 máquinas.** O
      atraso de ingestão p95 (leitura → WebSocket) ficou em 8,4 s contra a meta de 2 s no host de teste de 4 vCPU
      compartilhado ([performance.md §1.0](performance.md)); as outras 6 metas fecharam. No VPS de produção
      (8 vCPU, [performance.md §1.2](performance.md)) refaça a medição **antes** de liberar o tráfego real. O harness
      precisa da infraestrutura de dev (`make infra`: containers `fm_postgres`, `fm_influxdb`; ele cria banco,
      bucket, broker, API e worker próprios e apaga tudo no fim), então rode-o numa VM do mesmo tamanho do VPS ou
      no próprio VPS enquanto a produção ainda não tem clientes, num checkout com `make venv`:
      `DURATION_S=1800 API_PORT=8190 BROKER_PORT=8192 DB=fm_scale_golive .venv/bin/python e2e/scale/load.py` →
      sai 0 e `targets.ingestion_lag_p95_lt_2s=true` em `e2e/scale/result.json`. Registre o resultado em
      performance.md §1.0. Se falhar, limite o piloto a ≤ 100 máquinas (≈ 1 300 leituras/s, onde todas as metas
      foram medidas) até resolver.

## 6. Primeiro acesso: superadmin sem senha padrão

As senhas `Admin@1234` / `Super@1234` do README são **só de desenvolvimento** e públicas. O seed recusa rodar
em produção sem senhas novas fortes (`APP_ENV=production` → `SEED_ADMIN_PASSWORD` e `SEED_SUPERADMIN_PASS`
obrigatórios, diferentes dos padrões).

- [ ] **6.1** Criar o superadmin e o admin do primeiro tenant com senhas geradas na hora (o arquivo temporário evita
      a senha no histórico do shell e no `ps`):
      ```bash
      umask 077; f=$(mktemp)
      cat > "$f" <<EOF
      SEED_SUPERADMIN_EMAIL=voce@suaempresa.com.br
      SEED_SUPERADMIN_PASS=$(openssl rand -base64 24)
      SEED_ADMIN_EMAIL=admin@cliente-piloto.com.br
      SEED_ADMIN_PASSWORD=$(openssl rand -base64 24)
      SEED_TENANT_NAME=Cliente Piloto
      SEED_TENANT_SLUG=cliente-piloto
      SEED_DEMO_PLANT=0
      PYTHONPATH=/app
      EOF
      cat "$f"     # copie as senhas para o cofre AGORA
      dc run --rm -T --env-from-file "$f" -v "$PWD/scripts:/scripts:ro" server python /scripts/seed_dev.py   # compose ≥ 2.24
      shred -u "$f"
      ```
- [ ] **6.2** Entre em `https://app.factoryconnect.tech` com o superadmin, **ative o MFA** (Conta → Segurança) e
      troque a senha se ela passou por qualquer canal inseguro.
- [ ] **6.3** **Se a produção já foi semeada com as senhas de dev** em algum momento (ex.: deploys antigos com
      `seed_remote.py`): confira de fora que elas não funcionam mais —
      ```bash
      for u in admin@factory-monitor.com:Admin@1234 superadmin@factory-monitor.com:Super@1234; do
        curl -s -o /dev/null -w "${u%%:*} → %{http_code}\n" -X POST https://app.factoryconnect.tech/api/auth/login \
          -H 'content-type: application/json' -d "{\"email\":\"${u%%:*}\",\"password\":\"${u#*:}\"}"
      done                     # esperado: 401 nas duas (429 = limite de tentativas; repita em 1 min)
      ```
      Se der `200`: entre com essa conta, troque a senha (Conta → Senha), **encerre as outras sessões** e, se a
      conta não é sua, desative-a (Admin → Usuários). Repita para `planejador@`, `tecnico@`, `supervisor@` se existirem.
- [ ] **6.4** `APP_SECRET_KEY` nova (item 3.1) invalida todos os tokens emitidos antes — confirme que ela não é a
      mesma de algum `.env` antigo.

## 7. Monitoramento e alertas

- [ ] **7.1** Papel só de leitura para o sql-exporter (no VPS):
      ```bash
      pw=$(openssl rand -hex 24); echo "SQL_EXPORTER_USER=fm_monitor" >> .env; echo "SQL_EXPORTER_PASSWORD=$pw" >> .env
      docker exec -i fm_postgres psql -U "$(grep ^POSTGRES_USER= .env | cut -d= -f2)" -d factory_monitor <<SQL
      CREATE ROLE fm_monitor LOGIN PASSWORD '$pw';
      GRANT pg_monitor TO fm_monitor;
      GRANT CONNECT ON DATABASE factory_monitor TO fm_monitor;
      GRANT USAGE ON SCHEMA public TO fm_monitor;
      GRANT SELECT ON gateways, tenants, machines, ingest_dead_letters TO fm_monitor;
      SQL
      ```
- [ ] **7.2** `ALERT_EMAIL_TO` (e `ALERT_EMAIL_CRITICAL_TO`, se quiser os críticos em outra caixa) + SMTP no `.env`;
      `dc up -d prometheus alertmanager grafana node-exporter sql-exporter blackbox-exporter`; `dc ps` → todos
      `running`/`healthy` (o Alertmanager recusa subir sem destino — veja `docker logs fm_alertmanager`).
- [ ] **7.3** Teste o e-mail de ponta a ponta:
      `docker exec fm_alertmanager amtool alert add FmTesteGoLive severity=warning summary=teste --alertmanager.url=http://localhost:9093`
      → e-mail em ~1 min.
- [ ] **7.4** Grafana por túnel (`ssh -L 3001:127.0.0.1:3001 fmonitor@VPS` → http://localhost:3001): painel
      **FactoryConnect — Operação** com dados; nenhum alerta disparando exceto `FmBackupNeverReported` (some no §8).
- [ ] **7.5** Monitor **externo** (UptimeRobot/healthchecks.io, 1 min): `https://app.factoryconnect.tech/api/health`,
      `https://factoryconnect.tech/healthz`, TCP `mqtt.factoryconnect.tech:8883` — pega o VPS inteiro fora do ar,
      que o Prometheus interno não vê.

## 8. Backups e teste de restauração

- [ ] **8.1** Cron diário do [operations.md §4.2](operations.md) com cópia **off-site cifrada**
      (`BACKUP_UPLOAD_CMD` + `BACKUP_PASSPHRASE_FILE`; passphrase também no cofre), `BACKUP_PING_URL` e
      `BACKUP_METRICS_FILE=/opt/factory-monitor/backups/metrics/fm_backup.prom`.
- [ ] **8.2** Rode uma vez à mão e confira: `scripts/backup.sh` (sai 0), arquivo no destino off-site,
      `cat backups/metrics/fm_backup.prom`, alerta `FmBackupNeverReported` resolvido.
- [ ] **8.3** **Teste de restauração** fora da produção: baixe um backup do destino off-site para outra máquina
      (ou para o staging), decifre se for `.tar.enc` e restaure numa stack descartável —
      `make infra && make restore FILE=<arquivo> && make dev` (o `restore.sh` recusa com o app no ar; confere linhas por tabela e pontos do Influx contra o
      manifest). Entre no app restaurado e abra uma máquina com histórico. Anote a data; repita **todo mês**
      (operations.md §4.3). O CI já prova o mecanismo a cada push (`scripts/backup_restore_test.sh`); este passo prova
      **o seu** backup e a sua passphrase.

## 9. Gateways em campo e demo pública

- [ ] **9.1** Certificado do broker (`mqtt.factoryconnect.tech`, operations.md §6.3), `MQTT_TLS_ENABLED=true`,
      `dc up -d mosquitto`; de fora: `openssl s_client -connect mqtt.factoryconnect.tech:8883 -servername mqtt.factoryconnect.tech </dev/null | head -5`.
- [ ] **9.2** Uma credencial MQTT **por gateway**, restrita ao tenant:
      `make mqtt-gateway-add GATEWAY=gw-<cliente>-<linha> TENANT=<uuid do tenant>` → `make mqtt-reload`; senha
      impressa uma vez vai só para o `.env` do gateway (infra/gateway, docs/gateways.md).
- [ ] **9.3** Gateways antigos em 1883 sem TLS migrados → `MQTT_PLAIN_BIND=127.0.0.1` no `.env` → `dc up -d mosquitto`.
- [ ] **9.4** Demo pública (opcional): bootstrap do [operations.md §10.1–10.2](operations.md), `COMPOSE_PROFILES=monitoring,demo`,
      `dc up -d demo-plant demo-gateway`; `curl -fsS https://app.factoryconnect.tech/api/public/demo-status` →
      `"plant_running":true`; botão "Ver demonstração" do site abre a demo.

## 10. Smoke test final (produção)

- [ ] `curl -fsS https://app.factoryconnect.tech/api/health` e `curl -fsS https://factoryconnect.tech/healthz` → 200.
- [ ] `curl -sI http://app.factoryconnect.tech | head -3` → redireciona para `https://`.
- [ ] Cabeçalhos: `curl -sI https://app.factoryconnect.tech/login | grep -iE 'strict-transport|content-security|x-frame|x-content-type|referrer|permissions'`
      (seis linhas) — e o mesmo em `https://factoryconnect.tech/`. SSL Labs (https://www.ssllabs.com/ssltest/) → A ou A+.
- [ ] Login do admin do tenant, dashboard, uma máquina com dado ao vivo (gateway do §9 ou simulador), WebSocket
      conectado (indicador "ao vivo"), relatório PDF gerado.
- [ ] Formulário de contato do site → e-mail chega em `LEADS_TO_EMAIL`.
- [ ] `https://staging.factoryconnect.tech` responde com `X-Robots-Tag: noindex`.
- [ ] `make security` local verde (gitleaks, pip-audit, npm audit, cabeçalhos) na tag publicada.

## 11. Rollback (se algo der errado)

1. **Healthcheck falhou no deploy** → o script já voltou sozinho para a versão anterior (veja a saída do job).
2. **Versão no ar está ruim** → Actions → **Deploy** com a **tag anterior** (`target=production`, aprove).
3. **Migration estragou dados** → restaure o backup pré-deploy (`backups/pre-deploy/`, mais recente) com o código
   anterior — [operations.md §3](operations.md).
4. **Tudo fora e o GitHub indisponível** → no VPS: `tail -5 .deploy/history.log`, depois
   `git reset --keep <sha-bom> && FM_VERSION=<versão-boa> dc up -d --no-build server web site`.
5. Registre o incidente (operations.md §9): horário, impacto, causa, o que muda para não repetir.

Depois do go-live: Dependabot e o workflow **Security** semanal mantêm as dependências em dia; revise os aceites
de `.github/security/audit-allowlist.toml` antes de vencerem (2027-01-31).
