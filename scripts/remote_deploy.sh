#!/usr/bin/env bash
# =============================================================
# FactoryConnect — deploy executado NO VPS.
# Chamado pelo GitHub Actions (.github/workflows/deploy.yml) via:
#   ssh deploy@VPS 'bash -s' -- <sha> [app_dir] < scripts/remote_deploy.sh
#
# Passos: fast-forward até o commit testado → build → migrations → up -d →
# healthcheck. Se o healthcheck falhar, volta código e imagens para a versão
# anterior (rollback). Migrations NÃO são revertidas automaticamente: toda
# migration precisa ser compatível com a versão anterior do código.
# =============================================================
set -euo pipefail

SHA="${1:?uso: remote_deploy.sh <sha> [app_dir]}"
APP_DIR="${2:-/opt/factory-monitor}"
HEALTH_RETRIES="${HEALTH_RETRIES:-24}"   # x5s = 2 min
SERVICES=(server web)
IMAGES=(factory-monitor-server factory-monitor-web)

cd "$APP_DIR"
test -f .env || { echo "✗ $APP_DIR/.env não existe no VPS" >&2; exit 1; }
COMPOSE=(docker compose --env-file "$APP_DIR/.env" -f infra/docker-compose.prod.yml)

log() { printf '\n▶ %s\n' "$*"; }

health() {
  docker exec fm_server python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" \
    >/dev/null 2>&1
}

wait_healthy() {
  for _ in $(seq 1 "$HEALTH_RETRIES"); do
    if health; then return 0; fi
    sleep 5
  done
  return 1
}

PREV_SHA="$(git rev-parse HEAD)"
log "Atual: ${PREV_SHA:0:12} → alvo: ${SHA:0:12}"

log "Atualizando código (fast-forward, preserva alterações locais)"
git fetch --prune origin main
git merge --ff-only "$SHA"

log "Guardando imagens atuais para rollback"
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img:latest" >/dev/null 2>&1; then
    docker tag "$img:latest" "$img:rollback"
  fi
done

log "Build das imagens"
"${COMPOSE[@]}" build "${SERVICES[@]}"

log "Migrations (alembic upgrade head)"
"${COMPOSE[@]}" run --rm -T server alembic upgrade head

log "Subindo serviços"
"${COMPOSE[@]}" up -d --remove-orphans

log "Healthcheck"
if wait_healthy; then
  echo "  ✓ saudável em ${SHA:0:12}"
  docker image prune -f >/dev/null || true
  "${COMPOSE[@]}" ps
  exit 0
fi

echo "✗ Healthcheck falhou — logs do server:" >&2
docker logs fm_server --tail 80 >&2 2>&1 || true

log "ROLLBACK para ${PREV_SHA:0:12}"
git reset --keep "$PREV_SHA"
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img:rollback" >/dev/null 2>&1; then
    docker tag "$img:rollback" "$img:latest"
  fi
done
"${COMPOSE[@]}" up -d --no-build "${SERVICES[@]}"
if wait_healthy; then
  echo "  ✓ rollback saudável em ${PREV_SHA:0:12}" >&2
else
  echo "  ✗ rollback também não respondeu — intervenção manual necessária" >&2
fi
exit 1
