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
        venv test test-server test-edge test-scripts test-web web-check

help: ## Lista os alvos
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

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
	$(PY) -m venv $(VENV_EDGE) && $(VENV_EDGE)/bin/pip install -q -r edge/requirements.txt pytest==8.2.0 pytest-asyncio==0.23.7

test: test-scripts test-server test-edge test-web ## Roda todos os testes

test-server: ## pytest do server (cria/derruba o banco fm_test_server no postgres local)
	cd server && ../$(VENV)/bin/python -m pytest -q

test-edge: ## pytest do edge
	@if [ -d edge/tests ]; then $(VENV_EDGE)/bin/python -m pytest -q edge/tests; else echo "(edge/tests ainda não existe)"; fi

test-scripts: ## pytest dos scripts de operação
	$(VENV)/bin/python -m pytest -q scripts/tests

test-web: ## vitest
	cd web && npx vitest run

web-check: ## typecheck + build do frontend
	cd web && npx tsc --noEmit && npx vite build
