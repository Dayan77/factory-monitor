#!/usr/bin/env bash
# =============================================================
# FactoryConnect — deploy executado NO VPS.
# Chamado pelo GitHub Actions (.github/workflows/deploy.yml, aprovação manual no environment) via:
#   ssh deploy@VPS 'bash -s' -- <ref> [app_dir] < scripts/remote_deploy.sh
#
#   <ref>   tag de release vX.Y.Z (recomendado) ou sha de commit da main
#   app_dir checkout no VPS (produção /opt/factory-monitor; staging /opt/factory-monitor-staging)
#
# Variáveis (opcionais):
#   DEPLOY_TARGET        production (padrão) | staging   → compose, prefixo dos containers e serviços
#   MIGRATION_TIMEOUT_S  tempo máximo do `alembic upgrade head` (600) — estourou: deploy abortado, versão anterior no ar
#   PRE_DEPLOY_BACKUP=0  pula o backup do Postgres antes das migrations (ex.: banco enorme e release sem migration)
#   HEALTH_RETRIES       tentativas do healthcheck, 5 s cada (24 = 2 min)
#   ALLOW_NON_MAIN=1     permite em PRODUÇÃO uma tag/commit fora da main (hotfix excepcional)
#   GHCR_TOKEN/GHCR_USER login temporário no registro para o pull (o workflow injeta o GITHUB_TOKEN do job)
#
# Imagens: com FM_IMAGE_PREFIX=<registro>/<dono>/factoryconnect no .env, as imagens da release são PUXADAS
# (build/scan/SBOM/proveniência no workflow Release); sem ele, são construídas aqui (compatível com o deploy antigo).
# Toda imagem leva a tag da versão: FM_VERSION = X.Y.Z (tag vX.Y.Z) ou sha-<12 hex> — nunca :latest.
#
# Passos: código no commit alvo → pré-requisitos → pull/build → backup do Postgres → migrations (com timeout) →
# up -d → healthcheck. Healthcheck falhou: volta código e containers para a versão anterior (rollback).
# Migrations NÃO são revertidas automaticamente: toda migration é compatível com o código anterior (expand/contract);
# para desfazer uma destrutiva, restaure o backup pré-deploy (docs/operations.md §3 Rollback).
# =============================================================
# O workflow manda este script pelo stdin do ssh (`bash -s`). Tudo fica num bloco { … } </dev/null: o bash lê o
# arquivo inteiro antes de executar, e nenhum comando (docker compose run mantém o stdin aberto) consome o resto
# do script — em 25/10/2026 o `compose run … alembic current` fez o bash sair com 0 logo após o backup.
{
set -euo pipefail

REF="${1:?uso: remote_deploy.sh <tag vX.Y.Z | sha> [app_dir]}"
TARGET="${DEPLOY_TARGET:-production}"
case "$TARGET" in
  production) DEF_DIR=/opt/factory-monitor;         COMPOSE_FILE=infra/docker-compose.prod.yml;    PFX=fm_
              SERVICES=(server web site) ;;
  staging)    DEF_DIR=/opt/factory-monitor-staging; COMPOSE_FILE=infra/docker-compose.staging.yml; PFX=fm_stg_
              SERVICES=(server web) ;;
  *) echo "✗ DEPLOY_TARGET inválido: $TARGET (production | staging)" >&2; exit 2 ;;
esac
APP_DIR="${2:-$DEF_DIR}"
HEALTH_RETRIES="${HEALTH_RETRIES:-24}"   # x5s = 2 min
MIGRATION_TIMEOUT_S="${MIGRATION_TIMEOUT_S:-600}"
[[ "$MIGRATION_TIMEOUT_S" =~ ^[1-9][0-9]*$ ]] || { echo "✗ MIGRATION_TIMEOUT_S inválido" >&2; exit 2; }

cd "$APP_DIR"
test -f .env || { echo "✗ $APP_DIR/.env não existe no VPS" >&2; exit 1; }
COMPOSE=(docker compose --env-file "$APP_DIR/.env" -f "$COMPOSE_FILE")
STATE_DIR="$APP_DIR/.deploy"

log() { printf '\n▶ %s\n' "$*"; }

health() {
  docker exec "${PFX}server" python -c \
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

# O VPS é compartilhado com outros apps (ex.: Easypanel, projetos.factoryconnect.tech). `up --remove-orphans`
# remove containers do MESMO projeto compose que não estão no arquivo, e os container_name fixos (fm_*) colidem
# com containers de outro projeto. Roda ANTES de pull/build/migrations/up: se o projeto compose tiver container
# que não é do FactoryConnect, ou se um nome fm_* pertencer a outro projeto, aborta sem tocar em nada.
preflight_isolation() {
  local cfg ps report
  command -v python3 >/dev/null || { echo "✗ python3 ausente no VPS (necessário para a checagem de isolamento)" >&2; return 1; }
  cfg="$("${COMPOSE[@]}" config --format json 2>/dev/null)" || { echo "✗ docker compose config falhou" >&2; return 1; }
  ps="$(docker ps -a --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Ports}}')" \
    || { echo "✗ docker ps falhou" >&2; return 1; }
  report="$(CFG="$cfg" PS="$ps" PFX="$PFX" python3 -c '
import json, os
cfg = json.loads(os.environ["CFG"])
project = cfg.get("name", "")
ours = {s.get("container_name") for s in cfg.get("services", {}).values() if s.get("container_name")}
WILD = {"", "0.0.0.0", "::", "[::]"}
want = []   # portas do host publicadas pelo compose: (ip, porta, protocolo, serviço)
for svc, s in cfg.get("services", {}).items():
    for p in s.get("ports") or []:
        if isinstance(p, dict) and p.get("published"):
            want.append((p.get("host_ip") or "", str(p["published"]), p.get("protocol") or "tcp", svc))
errs = []
for line in os.environ["PS"].splitlines():
    if not line.strip():
        continue
    name, proj, ports = (line.split("\t") + ["", ""])[:3]
    if proj != project:   # porta já publicada por outro app: o nosso up falharia ao publicar a mesma
        for spec in filter(None, (x.strip() for x in ports.split(","))):
            if "->" not in spec:
                continue
            host, _, rest = spec.partition("->")
            hip, _, hport = host.rpartition(":")
            proto = rest.rpartition("/")[2] or "tcp"
            for ip, port, wproto, svc in want:
                if port == hport and proto == wproto and (ip == hip or ip in WILD or hip in WILD):
                    errs.append(f"porta {port}/{proto} do serviço {svc} já é usada por {name} ({spec})")
    if proj == project and not name.startswith(os.environ["PFX"]):
        errs.append(f"container {name} está no projeto compose {project!r} mas não é do FactoryConnect "
                    "(up --remove-orphans o removeria)")
    if name in ours and proj != project:
        errs.append(f"o nome {name} já é usado pelo projeto {proj or chr(34)+chr(34)} (outro app ou deploy antigo)")
print(f"projeto={project}")
for e in errs:
    print("ERRO " + e)
')" || { echo "✗ checagem de isolamento falhou" >&2; return 1; }
  echo "  ${report%%$'\n'*}"
  if grep -q '^ERRO ' <<<"$report"; then
    echo "✗ o deploy afetaria containers de outro app no VPS — nada foi alterado:" >&2
    grep '^ERRO ' <<<"$report" | sed 's/^ERRO /  - /' >&2
    return 1
  fi
}

# Pré-requisitos do broker MQTT de produção (docs/operations.md §6.4). Roda ANTES de pull/build/migrations/up:
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
PREV_VERSION=latest                       # deploy anterior ao versionamento: imagens :latest
if [ -s "$STATE_DIR/$TARGET.current" ]; then
  read -r PREV_VERSION _ < "$STATE_DIR/$TARGET.current" || PREV_VERSION=latest
fi

abort_restore_code() {   # $1 = mensagem
  git reset -q --keep "$PREV_SHA" || true
  echo "✗ $1 — a versão $PREV_VERSION (${PREV_SHA:0:12}) continua no ar" >&2
  exit 1
}

# ── Versão e commit alvo ─────────────────────────────────────────
if [[ "$REF" =~ ^v([0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?)$ ]]; then
  FM_VERSION="${BASH_REMATCH[1]}"
  log "Release $REF ($TARGET) — buscando tags"
  git fetch -q --prune --tags origin main
  git rev-parse -q --verify "refs/tags/$REF" >/dev/null || { echo "✗ tag $REF não existe no origin" >&2; exit 1; }
  SHA="$(git rev-parse "$REF^{commit}")"
elif [[ "$REF" =~ ^[0-9a-f]{7,40}$ ]]; then
  log "Commit $REF ($TARGET)"
  git fetch -q --prune origin main
  SHA="$(git rev-parse "$REF^{commit}")"
  FM_VERSION="sha-${SHA:0:12}"
else
  echo "✗ ref inválida: $REF (use vX.Y.Z ou o sha do commit)" >&2; exit 2
fi
export FM_VERSION
log "Atual: $PREV_VERSION (${PREV_SHA:0:12}) → alvo: $FM_VERSION (${SHA:0:12})"

# Commits locais no VPS que não estão no origin: nunca descartar em silêncio.
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "✗ o checkout do VPS tem commits que não estão em origin/main — resolva à mão antes do deploy" >&2
  exit 1
fi
if [ "$TARGET" = production ] && [ "${ALLOW_NON_MAIN:-0}" != 1 ] && ! git merge-base --is-ancestor "$SHA" origin/main; then
  echo "✗ $REF não está na main: produção só recebe releases da main (exceção: ALLOW_NON_MAIN=1)" >&2
  exit 1
fi

log "Atualizando código para ${SHA:0:12} (preserva alterações locais não commitadas)"
git reset -q --keep "$SHA"

log "Pré-requisitos (MQTT, compose)"
preflight_mqtt || abort_restore_code "deploy abortado antes de qualquer pull/build/migration"
log "Isolamento dos outros apps do VPS (projeto compose, nomes fm_*)"
preflight_isolation || abort_restore_code "deploy abortado antes de qualquer pull/build/migration"

PREFIX="$(env_get FM_IMAGE_PREFIX)"
IMAGE_SERVICES=("${SERVICES[@]}")
# demo pública ativa (COMPOSE_PROFILES=...demo...): a imagem do edge (planta + gateway da demo) acompanha a versão
if [ "$TARGET" = production ] && [[ ",$(env_get COMPOSE_PROFILES)," == *,demo,* ]] && [ -z "$(env_get DEMO_EDGE_IMAGE_TAG)" ]; then
  IMAGE_SERVICES+=(demo-plant)
fi
if [[ "$PREFIX" == */* ]]; then
  log "Baixando imagens $PREFIX-*:$FM_VERSION"
  if [ -n "${GHCR_TOKEN:-}" ]; then
    printf '%s' "$GHCR_TOKEN" | docker login "${PREFIX%%/*}" -u "${GHCR_USER:-deploy}" --password-stdin >/dev/null \
      || abort_restore_code "login no registro ${PREFIX%%/*} falhou"
  fi
  pulled=0
  "${COMPOSE[@]}" pull "${IMAGE_SERVICES[@]}" && pulled=1
  if [ -n "${GHCR_TOKEN:-}" ]; then docker logout "${PREFIX%%/*}" >/dev/null 2>&1 || true; fi
  [ "$pulled" = 1 ] || abort_restore_code "pull das imagens $FM_VERSION falhou (a release terminou? o VPS tem acesso ao registro?)"
else
  log "Build das imagens (FM_VERSION=$FM_VERSION)"
  "${COMPOSE[@]}" build "${IMAGE_SERVICES[@]}" || abort_restore_code "build falhou"
fi

if [ "${PRE_DEPLOY_BACKUP:-1}" = 1 ] && [ -x scripts/backup.sh ] \
   && [ "$(docker inspect -f '{{.State.Running}}' "${PFX}postgres" 2>/dev/null)" = true ]; then
  log "Backup do Postgres antes das migrations ($APP_DIR/backups/pre-deploy, mantém 5)"
  if ! BACKUP_DIR="$APP_DIR/backups/pre-deploy" BACKUP_KEEP=5 BACKUP_SKIP_INFLUX=1 PG_CONTAINER="${PFX}postgres" \
       BACKUP_UPLOAD_CMD="" BACKUP_PING_URL="" BACKUP_METRICS_FILE="" scripts/backup.sh >/dev/null; then
    abort_restore_code "backup pré-deploy falhou — deploy abortado antes das migrations"
  fi
fi

mkdir -p "$STATE_DIR"
log "Revisão do banco antes das migrations (para o rollback: docs/operations.md §3)"
"${COMPOSE[@]}" run --rm -T server alembic current 2>/dev/null | tail -n 1 > "$STATE_DIR/$TARGET.alembic-before" || true

log "Migrations (alembic upgrade head, timeout ${MIGRATION_TIMEOUT_S}s)"
if ! timeout --kill-after=30 "$MIGRATION_TIMEOUT_S" "${COMPOSE[@]}" run --rm -T server alembic upgrade head; then
  echo "✗ migrations falharam ou passaram de ${MIGRATION_TIMEOUT_S}s. Cada migration roda numa transação: a que" \
       "falhou foi desfeita; as anteriores do mesmo upgrade ficam aplicadas (compatíveis com o código anterior)." >&2
  echo "  Revisão antes: $(cat "$STATE_DIR/$TARGET.alembic-before" 2>/dev/null || echo '?'); backup: $APP_DIR/backups/pre-deploy" >&2
  abort_restore_code "deploy abortado nas migrations, containers não foram trocados"
fi

log "Subindo serviços ($FM_VERSION)"
"${COMPOSE[@]}" up -d --remove-orphans

log "Healthcheck"
if wait_healthy; then
  echo "  ✓ saudável em $FM_VERSION (${SHA:0:12})"
  printf '%s %s\n' "$FM_VERSION" "$SHA" > "$STATE_DIR/$TARGET.current"
  printf '%s %s %s %s\n' "$(date -u +%FT%TZ)" "$TARGET" "$FM_VERSION" "$SHA" >> "$STATE_DIR/history.log"
  docker image prune -f >/dev/null || true
  "${COMPOSE[@]}" ps
  exit 0
fi

echo "✗ Healthcheck falhou — logs do server:" >&2
docker logs "${PFX}server" --tail 80 >&2 2>&1 || true

log "ROLLBACK para $PREV_VERSION (${PREV_SHA:0:12})"
git reset -q --keep "$PREV_SHA"
FM_VERSION="$PREV_VERSION" "${COMPOSE[@]}" up -d --no-build "${SERVICES[@]}"
if wait_healthy; then
  echo "  ✓ rollback saudável em $PREV_VERSION (${PREV_SHA:0:12})" >&2
else
  echo "  ✗ rollback também não respondeu — intervenção manual necessária (docs/operations.md §3)" >&2
fi
exit 1
} </dev/null
