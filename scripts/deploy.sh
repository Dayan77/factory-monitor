#!/bin/bash
# =============================================================
# Factory Monitor — Deploy Script
# Execute no VPS após o setup inicial
# Uso: ./deploy.sh [git-branch]
# =============================================================
set -euo pipefail

APP_DIR="/opt/factory-monitor"
BRANCH="${1:-main}"
COMPOSE="docker compose -f infra/docker-compose.prod.yml"

echo "==> Deploy branch: $BRANCH"
cd "$APP_DIR"

echo "==> Atualizando código..."
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "==> Buildando imagens..."
$COMPOSE build --no-cache server web

echo "==> Aplicando migrations..."
$COMPOSE run --rm server alembic upgrade head

echo "==> Reiniciando serviços..."
$COMPOSE up -d --remove-orphans

echo "==> Removendo imagens antigas..."
docker image prune -f

echo ""
echo "✓ Deploy concluído!"
$COMPOSE ps
