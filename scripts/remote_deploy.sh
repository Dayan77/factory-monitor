#!/usr/bin/env bash
# =============================================================
# FactoryConnect — deploy executado NO VPS.
# Chamado pelo GitHub Actions (.github/workflows/deploy.yml) via:
#   ssh deploy@VPS 'bash -s' -- <sha> [app_dir] < scripts/remote_deploy.sh
#
# Passos: fast-forward até o commit testado → build → backup do Postgres →
# migrations → up -d → healthcheck. Se o healthcheck falhar, volta código e imagens
# para a versão anterior (rollback). Migrations NÃO são revertidas automaticamente:
# toda migration precisa ser compatível com a versão anterior do código; para desfazer
# uma migration destrutiva, restaure o backup pré-deploy (docs/operations.md §Rollback).
# PRE_DEPLOY_BACKUP=0 pula o backup (ex.: deploy sem migration e banco enorme).
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

# Valor de KEY no .env (última ocorrência; sem comentário inline nem aspas). Vazio se ausente.
env_get() {
  local line
  line="$(grep -E "^[[:space:]]*$1=" "$APP_DIR/.env" | tail -n 1 || true)"
  line="${line#*=}"
  line="$(printf '%s' "$line" | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//')"
  line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
  printf '%s' "$line"
}

# Pré-requisitos do broker MQTT de produção (docs/operations.md §6.4). Roda ANTES de build/migrations/up:
# se faltar algo o deploy aborta com o código anterior no ar — nunca sobe um broker que recusaria os
# gateways em campo (ingestão parada) nem um compose que não valida.
preflight_mqtt() {
  local errs=() user pw tls gw_dir cert_dir n
  user="$(env_get MQTT_USERNAME)"; pw="$(env_get MQTT_PASSWORD)"; tls="$(env_get MQTT_TLS_ENABLED)"
  [ -n "$user" ] || errs+=("MQTT_USERNAME ausente no .env (usuário do backend no broker)")
  [ "${#pw}" -ge 16 ] || errs+=("MQTT_PASSWORD ausente ou com menos de 16 caracteres no .env")
  gw_dir="$(env_get MQTT_GATEWAYS_DIR)"; gw_dir="${gw_dir:-$APP_DIR/mqtt}"
  n=0
  if [ -f "$gw_dir/gateways.conf" ]; then
    n="$(grep -cvE '^[[:space:]]*(#|$)' "$gw_dir/gateways.conf" || true)"
  fi
  if [ "$n" -lt 1 ] && [ "$(env_get MQTT_ALLOW_NO_GATEWAYS)" != 1 ]; then
    errs+=("nenhum gateway em $gw_dir/gateways.conf: os gateways em campo seriam recusados (make mqtt-gateway-add; instalação sem gateways: MQTT_ALLOW_NO_GATEWAYS=1)")
  fi
  if [ "$tls" = true ]; then
    cert_dir="$(env_get MQTT_TLS_CERT_DIR)"; cert_dir="${cert_dir:-$APP_DIR/mqtt/certs}"
    for f in fullchain.pem privkey.pem; do
      [ -s "$cert_dir/$f" ] || errs+=("MQTT_TLS_ENABLED=true mas $cert_dir/$f não existe")
    done
  fi
  if [ "${#errs[@]}" -gt 0 ]; then
    echo "✗ pré-requisitos do MQTT de produção não atendidos (docs/operations.md §6.4):" >&2
    printf '  - %s\n' "${errs[@]}" >&2
    return 1
  fi
  "${COMPOSE[@]}" config -q || { echo "✗ docker compose config falhou (variável obrigatória ausente?)" >&2; return 1; }
}

PREV_SHA="$(git rev-parse HEAD)"
log "Atual: ${PREV_SHA:0:12} → alvo: ${SHA:0:12}"

log "Atualizando código (fast-forward, preserva alterações locais)"
git fetch --prune origin main
git merge --ff-only "$SHA"

log "Pré-requisitos (MQTT)"
if ! preflight_mqtt; then
  git reset --keep "$PREV_SHA"
  echo "✗ deploy abortado antes de qualquer build/migration — a versão ${PREV_SHA:0:12} continua no ar" >&2
  exit 1
fi

log "Guardando imagens atuais para rollback"
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img:latest" >/dev/null 2>&1; then
    docker tag "$img:latest" "$img:rollback"
  fi
done

log "Build das imagens"
"${COMPOSE[@]}" build "${SERVICES[@]}"

if [ "${PRE_DEPLOY_BACKUP:-1}" = 1 ] && [ -x scripts/backup.sh ] \
   && [ "$(docker inspect -f '{{.State.Running}}' fm_postgres 2>/dev/null)" = true ]; then
  log "Backup do Postgres antes das migrations ($APP_DIR/backups/pre-deploy, mantém 5)"
  if ! BACKUP_DIR="$APP_DIR/backups/pre-deploy" BACKUP_KEEP=5 BACKUP_SKIP_INFLUX=1 \
       BACKUP_UPLOAD_CMD="" BACKUP_PING_URL="" scripts/backup.sh >/dev/null; then
    echo "✗ backup pré-deploy falhou — deploy abortado antes das migrations" >&2
    git reset --keep "$PREV_SHA"
    exit 1
  fi
fi

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
