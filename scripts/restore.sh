#!/usr/bin/env bash
# =============================================================
# FactoryConnect — restaura um backup de scripts/backup.sh e CONFERE o resultado.
#
#   RESTORE_CONFIRM=yes scripts/restore.sh backups/fm-backup-20261005T030000Z.tar
#   (make restore FILE=backups/fm-backup-....tar  — pede confirmação)
#
# DESTRUTIVO: o banco PG_DB do container é recriado do zero e o InfluxDB é substituído
# por inteiro (`influx restore --full`: dados, buckets, usuários e tokens da origem).
# Pare o backend antes (docker stop fm_server) — o script recusa se ele estiver rodando.
#
# Depois de restaurar, confere:
#   - sha256 de cada arquivo (manifest) e do .tar (.sha256 ao lado, se existir);
#   - linhas por tabela no Postgres == linhas no dump (exato);
#   - pontos por bucket no Influx até count_stop >= o contado na origem antes do backup.
# Sai != 0 se qualquer verificação falhar.
#
# Variáveis:
#   PG_CONTAINER (fm_postgres)  INFLUX_CONTAINER (fm_influxdb)  APP_CONTAINER (fm_server)
#   RESTORE_PG_DB               banco de destino (padrão: POSTGRES_DB do container)
#   INFLUX_TOKEN                token da instância de ORIGEM (o restore --full traz os tokens dela);
#                               usado para a verificação. Sem ele a contagem do Influx é pulada.
#   RESTORE_INFLUX_TOKEN        token de operador do destino ANTES do restore (padrão: config do CLI do container)
#   RESTORE_SKIP_INFLUX=1       só Postgres
#   BACKUP_PASSPHRASE_FILE      para arquivos .tar.enc
#   RESTORE_ALLOW_RUNNING_APP=1 não recusa com o backend rodando (não recomendado)
# =============================================================
set -euo pipefail

ARCHIVE="${1:?uso: RESTORE_CONFIRM=yes scripts/restore.sh <fm-backup-*.tar[.enc]>}"
PG_CONTAINER="${PG_CONTAINER:-fm_postgres}"
INFLUX_CONTAINER="${INFLUX_CONTAINER:-fm_influxdb}"
APP_CONTAINER="${APP_CONTAINER:-fm_server}"
SKIP_INFLUX="${RESTORE_SKIP_INFLUX:-0}"

log() { printf '[restore %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERRO: $*"; exit 1; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

[ -f "$ARCHIVE" ] || die "arquivo não encontrado: $ARCHIVE"
[ "${RESTORE_CONFIRM:-}" = yes ] || die "restore é destrutivo: rode com RESTORE_CONFIRM=yes"
if running "$APP_CONTAINER" && [ "${RESTORE_ALLOW_RUNNING_APP:-0}" != 1 ]; then
  die "$APP_CONTAINER está rodando — pare antes (docker stop $APP_CONTAINER) para não gravar durante o restore"
fi
running "$PG_CONTAINER" || die "container $PG_CONTAINER não está rodando"

# ── Integridade do arquivo ────────────────────────────────────────
if [ -f "$ARCHIVE.sha256" ]; then
  ( cd "$(dirname "$ARCHIVE")" && sha256sum -c --quiet "$(basename "$ARCHIVE").sha256" ) \
    || die "sha256 do arquivo não confere ($ARCHIVE.sha256)"
  log "sha256 do arquivo ok"
else
  log "aviso: $ARCHIVE.sha256 não encontrado — seguindo só com os hashes internos do manifest"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/fm-restore.XXXXXX")"
IN_CONTAINER_DIR="/tmp/fm-restore-$$"
cleanup() {
  rm -rf "$WORK"
  if [ "$SKIP_INFLUX" != 1 ]; then docker exec "$INFLUX_CONTAINER" rm -rf "$IN_CONTAINER_DIR" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

case "$ARCHIVE" in
  *.enc)
    [ -r "${BACKUP_PASSPHRASE_FILE:-}" ] || die "arquivo cifrado: defina BACKUP_PASSPHRASE_FILE"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$BACKUP_PASSPHRASE_FILE" -in "$ARCHIVE" \
      | tar -C "$WORK" -xf - || die "falha ao decifrar/extrair (senha errada?)"
    ;;
  *) tar -C "$WORK" -xf "$ARCHIVE" ;;
esac
[ -f "$WORK/manifest.json" ] && [ -f "$WORK/files.tsv" ] && [ -f "$WORK/postgres.dump" ] \
  || die "arquivo não parece um backup do FactoryConnect (manifest/files/postgres.dump ausentes)"

while IFS=$'\t' read -r f sum _bytes; do
  [ -f "$WORK/$f" ] || die "arquivo ausente no backup: $f"
  [ "$(sha256sum "$WORK/$f" | cut -d' ' -f1)" = "$sum" ] || die "sha256 não confere: $f"
done < "$WORK/files.tsv"
log "hashes do manifest ok ($(wc -l < "$WORK/files.tsv" | tr -d ' ') arquivos)"

# ── PostgreSQL ────────────────────────────────────────────────────
# shellcheck disable=SC2016  # variáveis expandidas DENTRO do container
PG_DB="${RESTORE_PG_DB:-$(docker exec "$PG_CONTAINER" sh -c 'printf %s "$POSTGRES_DB"')}"
[[ "$PG_DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "nome de banco inválido: '$PG_DB'"
log "recriando banco $PG_DB em $PG_CONTAINER"
# shellcheck disable=SC2016
docker exec -e DB="$PG_DB" "$PG_CONTAINER" sh -c '
  set -e
  psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" \
    -c "CREATE DATABASE \"$DB\" OWNER \"$POSTGRES_USER\""'
log "pg_restore"
# shellcheck disable=SC2016
docker exec -i -e DB="$PG_DB" "$PG_CONTAINER" sh -c \
  'exec pg_restore -U "$POSTGRES_USER" -d "$DB" --no-owner --role="$POSTGRES_USER" --exit-on-error' \
  < "$WORK/postgres.dump"

log "conferindo linhas por tabela"
fail=0; ntab=0
while IFS=$'\t' read -r table expected; do
  [ -n "$table" ] || continue
  ntab=$((ntab + 1))
  # shellcheck disable=SC2016
  got="$(docker exec -e DB="$PG_DB" -e T="$table" "$PG_CONTAINER" sh -c \
    'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$DB" -c "select count(*) from $T"' | tr -d '\r')"
  if [ "$got" != "$expected" ]; then
    log "  ✗ $table: esperado $expected, restaurado $got"; fail=1
  fi
done < "$WORK/pg_counts.tsv"
[ "$fail" = 0 ] || die "contagem do Postgres não confere"
log "  ✓ $ntab tabelas com a mesma contagem do dump"

# ── InfluxDB ──────────────────────────────────────────────────────
if [ "$SKIP_INFLUX" = 1 ]; then
  log "Influx pulado (RESTORE_SKIP_INFLUX=1)"
elif [ ! -d "$WORK/influx" ]; then
  log "backup sem Influx — pulado"
else
  running "$INFLUX_CONTAINER" || die "container $INFLUX_CONTAINER não está rodando"
  log "influx restore --full em $INFLUX_CONTAINER"
  docker exec "$INFLUX_CONTAINER" mkdir -p "$IN_CONTAINER_DIR"
  tar -C "$WORK/influx" -cf - . | docker exec -i "$INFLUX_CONTAINER" tar -C "$IN_CONTAINER_DIR" -xf -
  RTOKEN=()
  [ -n "${RESTORE_INFLUX_TOKEN:-}" ] && RTOKEN=(-t "$RESTORE_INFLUX_TOKEN")
  docker exec "$INFLUX_CONTAINER" influx restore "${RTOKEN[@]}" --full "$IN_CONTAINER_DIR" >/dev/null

  stop="$(awk -F'"' '/"count_stop"/ { for (i = 1; i <= NF; i++) if ($i == "count_stop") { print $(i + 2); exit } }' "$WORK/manifest.json")"
  if [ ! -s "$WORK/influx_counts.tsv" ] || [ -z "$stop" ]; then
    log "aviso: backup sem contagem do Influx (BACKUP_INFLUX_COUNT=0) — verificação pulada"
  elif [ -z "${INFLUX_TOKEN:-}" ]; then
    log "aviso: INFLUX_TOKEN (token da origem) não definido — contagem do Influx não verificada"
  else
    log "conferindo pontos por bucket até $stop"
    # o restore --full troca o banco de autenticação: espere a API aceitar o token da origem
    for _ in $(seq 1 30); do
      docker exec "$INFLUX_CONTAINER" influx bucket list -t "$INFLUX_TOKEN" >/dev/null 2>&1 && break
      sleep 1
    done
    fail=0
    while IFS=$'\t' read -r bucket expected; do
      [ -n "$bucket" ] || continue
      got="$(docker exec "$INFLUX_CONTAINER" influx query -t "$INFLUX_TOKEN" --raw \
        "from(bucket: \"$bucket\") |> range(start: 0, stop: $stop) |> count() |> group() |> sum()" \
        | awk -F, '$0 !~ /^#/ && $4 ~ /^[0-9]+/ {gsub(/\r/,"",$4); print $4}')"
      got="${got:-0}"
      if [ "$got" -lt "$expected" ]; then
        log "  ✗ bucket $bucket: origem tinha $expected pontos, restaurado $got"; fail=1
      else
        log "  ✓ bucket $bucket: $got pontos (origem: $expected)"
      fi
    done < "$WORK/influx_counts.tsv"
    [ "$fail" = 0 ] || die "contagem do Influx não confere"
  fi
fi

log "restore concluído e verificado: $(basename "$ARCHIVE")"
