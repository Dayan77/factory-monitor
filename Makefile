# FactoryConnect — atalhos de desenvolvimento.
# Todas as variáveis vêm de .env (copie de .env.example). `make help` lista os alvos.

SHELL       := /bin/bash
ENV_FILE    ?= .env
COMPOSE     := docker compose --env-file $(ENV_FILE) -f infra/docker-compose.yml
INFRA_SVCS  := postgres influxdb mosquitto mediamtx
VENV        ?= .venv
VENV_EDGE   ?= .venv-edge
PY          ?= python3

.DEFAULT_GOAL := help
.PHONY: help env dev up infra down stop ps logs config migrate seed \
        venv test test-server test-edge test-scripts test-golden test-web web-check \
        monitoring test-infra lint-infra backup restore backup-restore-test backup-restore-test-dev \
        mqtt-gateway-add mqtt-gateway-remove mqtt-gateway-list mqtt-reload \
        security secrets-scan deps-audit headers-test release-check staging-config

help: ## Lista os alvos
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

env: ## Cria .env a partir de .env.example (se ainda não existir)
	@test -f $(ENV_FILE) || { cp .env.example $(ENV_FILE) && echo "✓ $(ENV_FILE) criado — revise as senhas"; }

dev: env ## Sobe a stack completa (postgres, influx, mosquitto, mediamtx, server, web, nginx) → http://localhost
	$(COMPOSE) up -d --build
	@echo "✓ http://localhost  (API: http://localhost/api/docs)"

up: dev ## Alias de `make dev`

infra: env ## Sobe só a infraestrutura (para rodar backend/frontend localmente)
	$(COMPOSE) up -d $(INFRA_SVCS)

down: ## Para e remove os containers (mantém os volumes)
	$(COMPOSE) down

stop: ## Para os containers sem removê-los
	$(COMPOSE) stop

ps: ## Status dos containers
	$(COMPOSE) ps

logs: ## Logs (ex.: make logs s=server)
	$(COMPOSE) logs -f --tail=100 $(s)

config: env ## Valida e mostra o compose com as variáveis resolvidas
	$(COMPOSE) config

migrate: ## alembic upgrade head (backend local, usando .env)
	cd server && set -a && . ../$(ENV_FILE) && set +a && ../$(VENV)/bin/alembic upgrade head

seed: ## Popula o banco de dev (usuários admin/superadmin)
	cd server && set -a && . ../$(ENV_FILE) && set +a && ../$(VENV)/bin/python ../scripts/seed_dev.py

venv: ## Cria .venv (server) e .venv-edge (edge) com as dependências fixadas
	$(PY) -m venv $(VENV) && $(VENV)/bin/pip install -q -r server/requirements-dev.txt -r scripts/requirements.txt
	$(PY) -m venv $(VENV_EDGE) && $(VENV_EDGE)/bin/pip install -q -r edge/requirements-dev.txt

test: test-scripts test-server test-golden test-edge test-web ## Roda todos os testes (infra/docker: make test-infra)

test-server: ## pytest do server (cria/derruba o banco fm_test_server no postgres local)
	cd server && ../$(VENV)/bin/python -m pytest -q

test-edge: ## pytest do edge
	@if [ -d edge/tests ]; then $(VENV_EDGE)/bin/python -m pytest -q edge/tests; else echo "(edge/tests ainda não existe)"; fi

test-scripts: ## pytest dos scripts de operação
	$(VENV)/bin/python -m pytest -q scripts/tests

test-golden: ## Cenários de ouro (docs/scenarios) + casos da especificação de KPIs
	$(VENV)/bin/python -m pytest -q docs/scenarios
	cd server && FM_TEST_DB=$${FM_TEST_DB:-fm_test_golden} ../$(VENV)/bin/python -m pytest -q tests/test_kpi_spec_cases.py

test-web: ## vitest
	cd web && npx vitest run

web-check: ## typecheck + build do frontend
	cd web && npx tsc --noEmit && npx vite build

# ── Operação: observabilidade, backup, MQTT (docs/operations.md) ─────────────
monitoring: env ## Sobe Prometheus (:9090) + Grafana (:3000, painel FactoryConnect — Operação)
	$(COMPOSE) --profile monitoring up -d prometheus grafana
	@echo "✓ Grafana http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD, padrão admin) · Prometheus http://localhost:9090"

test-infra: ## Testes de infra com docker: mosquitto prod, Traefik/XFF, monitoring/alertas, staging, cabeçalhos/CSP
	$(VENV)/bin/python -m pytest -q infra/tests

lint-infra: ## shellcheck + actionlint (via docker, versões fixas)
	docker run --rm -v "$(CURDIR):/mnt:ro" -w /mnt koalaman/shellcheck:v0.10.0 -S warning scripts/*.sh infra/mosquitto/prod/*.sh infra/monitoring/alertmanager/*.sh
	docker run --rm -v "$(CURDIR):/repo:ro" -w /repo rhysd/actionlint:1.7.7 -color

BACKUP_DIR ?= ./backups
backup: ## Backup do Postgres + Influx em $(BACKUP_DIR) (scripts/backup.sh; VPS: cron, ver docs/operations.md)
	BACKUP_DIR="$(BACKUP_DIR)" scripts/backup.sh

restore: ## Restaura um backup: make restore FILE=backups/fm-backup-....tar (DESTRUTIVO, pede confirmação)
	@test -n "$(FILE)" || { echo "uso: make restore FILE=<arquivo>"; exit 2; }
	@read -r -p "Substituir Postgres e Influx por $(FILE)? digite 'restaurar': " a && [ "$$a" = restaurar ]
	RESTORE_CONFIRM=yes INFLUX_TOKEN="$${INFLUX_TOKEN:-$$(sed -n 's/^INFLUX_TOKEN=\([^[:space:]#]*\).*/\1/p' $(ENV_FILE))}" \
	  scripts/restore.sh "$(FILE)"

backup-restore-test: ## Backup → restore em containers NOVOS, conferindo linhas/pontos (origem sintética)
	scripts/backup_restore_test.sh

backup-restore-test-dev: ## Igual, mas fazendo backup da stack de dev rodando (só leitura nela)
	BRT_SOURCE=dev scripts/backup_restore_test.sh

MQTT_GATEWAYS_FILE ?= mqtt/gateways.conf
mqtt-gateway-add: ## Cria credencial MQTT de gateway: make mqtt-gateway-add GATEWAY=gw-x TENANT=<uuid>
	@test -n "$(GATEWAY)" -a -n "$(TENANT)" || { echo "uso: make mqtt-gateway-add GATEWAY=<nome> TENANT=<uuid>"; exit 2; }
	MQTT_GATEWAYS_FILE="$(MQTT_GATEWAYS_FILE)" scripts/mqtt_gateway.sh add "$(GATEWAY)" "$(TENANT)"

mqtt-gateway-remove: ## Revoga credencial MQTT: make mqtt-gateway-remove GATEWAY=gw-x
	@test -n "$(GATEWAY)" || { echo "uso: make mqtt-gateway-remove GATEWAY=<nome>"; exit 2; }
	MQTT_GATEWAYS_FILE="$(MQTT_GATEWAYS_FILE)" scripts/mqtt_gateway.sh remove "$(GATEWAY)"

mqtt-gateway-list: ## Lista gateways com credencial MQTT
	@MQTT_GATEWAYS_FILE="$(MQTT_GATEWAYS_FILE)" scripts/mqtt_gateway.sh list

mqtt-reload: ## Re-renderiza passwd/acl no fm_mosquitto de produção e recarrega (SIGHUP)
	MQTT_GATEWAYS_FILE="$(MQTT_GATEWAYS_FILE)" scripts/mqtt_gateway.sh reload

# ── Segurança e release (docs/operations.md §11 e §13) ──────────────────────────
GITLEAKS_IMAGE ?= zricethezav/gitleaks:v8.30.1
AUDIT_VENV     ?= .venv-audit

security: secrets-scan deps-audit headers-test ## Tudo que o workflow Security roda (gitleaks, pip-audit, npm audit, cabeçalhos)

secrets-scan: ## gitleaks no histórico completo do git (aceites em .github/security/gitleaksignore)
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) git /repo --config /repo/.github/security/gitleaks.toml \
	  --gitleaks-ignore-path /repo/.github/security/gitleaksignore --redact --no-banner

deps-audit: ## pip-audit (server, edge, scripts) + npm audit (web, site) → portão com a lista de aceites
	@test -x $(AUDIT_VENV)/bin/pip-audit || { $(PY) -m venv $(AUDIT_VENV) && $(AUDIT_VENV)/bin/pip install -q pip-audit==2.10.1; }
	@mkdir -p .audit && rc=0; \
	for p in server:server/requirements-dev.txt edge:edge/requirements-dev.txt scripts:scripts/requirements.txt; do \
	  $(AUDIT_VENV)/bin/pip-audit -r $${p#*:} --progress-spinner off -f json -o .audit/pip-$${p%%:*}.json >/dev/null 2>&1; \
	  $(PY) scripts/security/audit_gate.py pip .audit/pip-$${p%%:*}.json --project $${p%%:*} || rc=1; \
	done; \
	for p in web site; do \
	  (cd $$p && npm audit --json --package-lock-only > ../.audit/npm-$$p.json 2>/dev/null); \
	  $(PY) scripts/security/audit_gate.py npm .audit/npm-$$p.json --project $$p || rc=1; \
	done; exit $$rc

headers-test: ## Cabeçalhos de segurança/CSP com nginx real + Chromium (usa web/dist e site/dist se existirem)
	$(VENV)/bin/python -m pytest -q -rs infra/tests/test_security_headers.py

release-check: ## VERSION é SemVer e tem seção datada no CHANGELOG.md (opcional: TAG=vX.Y.Z)
	$(PY) scripts/release/check_release.py $(if $(TAG),--tag $(TAG))

staging-config: ## Valida o compose de staging com .env.staging.example
	docker compose --env-file .env.staging.example -f infra/docker-compose.staging.yml config -q && echo "✓ staging"
