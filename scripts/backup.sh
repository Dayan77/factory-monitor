#!/usr/bin/env bash
# =============================================================
# FactoryConnect — backup do PostgreSQL + InfluxDB (roda no host, via `docker exec`).
#
#   scripts/backup.sh                      # ou: make backup
#
# Gera $BACKUP_DIR/fm-backup-<UTC>.tar (+ .sha256) com:
#   postgres.dump      pg_dump -Fc do banco do app (snapshot consistente)
#   influx/            `influx backup` (todos os buckets, gzip)
#   manifest.json      versões, sha256 de cada arquivo, linhas por tabela (contadas NO dump)
#                      e pontos por bucket do Influx até `influx_count_stop`
# restore.sh confere tudo isso depois de restaurar.
#
# Variáveis (todas opcionais):
#   BACKUP_DIR              destino local                       (./backups; VPS: /var/backups/factory-monitor)
#   BACKUP_KEEP             quantos backups manter (rotação)    (14)
#   PG_CONTAINER            container do Postgres               (fm_postgres)
#   INFLUX_CONTAINER        container do InfluxDB               (fm_influxdb)
#   INFLUX_TOKEN            token de operador do Influx         (padrão: config do CLI dentro do container)
#   BACKUP_SKIP_INFLUX=1    só Postgres
#   BACKUP_INFLUX_COUNT=0   não conta pontos do Influx no manifest (bancos muito grandes)
#   BACKUP_PASSPHRASE_FILE  cifra o arquivo (openssl AES-256-CBC + PBKDF2) → .tar.enc
#   BACKUP_UPLOAD_CMD       comando de cópia off-site; recebe o caminho do arquivo em $1. Ex.:
#                             'rclone copy "$1" b2:fm-backups/'
#                             'aws s3 cp "$1" s3://fm-backups/'
#                             'scp "$1" backup@nas:/srv/fm/'
#   BACKUP_PING_URL         monitor de cron (ex. healthchecks.io): GET no sucesso, GET <url>/fail na falha
#   BACKUP_METRICS_FILE     arquivo .prom do textfile collector do node-exporter (profile monitoring), ex.
#                             /opt/factory-monitor/backups/metrics/fm_backup.prom → alerta FmBackupTooOld
#                           (sucesso: fm_backup_last_success_timestamp_seconds/_size_bytes/_duration_seconds;
#                            falha: fm_backup_last_failure_timestamp_seconds em <arquivo>_failure.prom)
#
# Sai != 0 em qualquer falha (nada é rotacionado nesse caso). Seguro rodar com a stack no ar.
# =============================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
PG_CONTAINER="${PG_CONTAINER:-fm_postgres}"
INFLUX_CONTAINER="${INFLUX_CONTAINER:-fm_influxdb}"
SKIP_INFLUX="${BACKUP_SKIP_INFLUX:-0}"
INFLUX_COUNT="${BACKUP_INFLUX_COUNT:-1}"

log() { printf '[backup %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
T0="$(date +%s)"

# Métricas para o Prometheus (node-exporter textfile): escrita atômica (tmp + mv) para nunca ler meio arquivo.
write_metrics() {   # $1 = success <arquivo> | failure
  local f="${BACKUP_METRICS_FILE:-}" now tmp
  [ -n "$f" ] || return 0
  now="$(date +%s)"
  ( umask 022; mkdir -p "$(dirname "$f")" ) 2>/dev/null || { log "aviso: não criou $(dirname "$f")"; return 0; }
  if [ "$1" = success ]; then
    tmp="$f.$$.tmp"
    {
      echo "# HELP fm_backup_last_success_timestamp_seconds Fim do último backup completo (scripts/backup.sh)."
      echo "# TYPE fm_backup_last_success_timestamp_seconds gauge"
      echo "fm_backup_last_success_timestamp_seconds $now"
      echo "# HELP fm_backup_last_size_bytes Tamanho do último arquivo de backup."
      echo "# TYPE fm_backup_last_size_bytes gauge"
      echo "fm_backup_last_size_bytes $(stat -c %s "$2" 2>/dev/null || echo 0)"
      echo "# HELP fm_backup_last_duration_seconds Duração do último backup completo."
      echo "# TYPE fm_backup_last_duration_seconds gauge"
      echo "fm_backup_last_duration_seconds $((now - T0))"
    } > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$f" || log "aviso: não gravou $f"
  else
    tmp="${f%.prom}_failure.prom.$$.tmp"
    {
      echo "# HELP fm_backup_last_failure_timestamp_seconds Última execução de scripts/backup.sh que FALHOU."
      echo "# TYPE fm_backup_last_failure_timestamp_seconds gauge"
      echo "fm_backup_last_failure_timestamp_seconds $now"
    } > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "${f%.prom}_failure.prom" || log "aviso: não gravou métrica de falha"
  fi
}
die() { log "ERRO: $*"; exit 1; }

ping_monitor() {   # $1 = "" (sucesso) | "/fail"
  [ -n "${BACKUP_PING_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 3 "${BACKUP_PING_URL}$1" >/dev/null 2>&1 || log "aviso: ping em BACKUP_PING_URL falhou"
}

[[ "$BACKUP_KEEP" =~ ^[1-9][0-9]*$ ]] || die "BACKUP_KEEP precisa ser inteiro >= 1 (veio '$BACKUP_KEEP')"
command -v docker >/dev/null || die "docker não encontrado"
if [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
  [ -r "$BACKUP_PASSPHRASE_FILE" ] || die "BACKUP_PASSPHRASE_FILE não legível: $BACKUP_PASSPHRASE_FILE"
  command -v openssl >/dev/null || die "openssl necessário para cifrar"
fi

mkdir -p "$BACKUP_DIR"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

# Uma execução por vez (cron atrasado + execução manual).
LOCK="$BACKUP_DIR/.lock"
mkdir "$LOCK" 2>/dev/null || die "outro backup em andamento ($LOCK existe; apague se for resto de uma falha)"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="fm-backup-$STAMP"
WORK="$BACKUP_DIR/.work-$STAMP"
IN_CONTAINER_DIR="/tmp/$NAME"
OK=0

cleanup() {
  rm -rf "$WORK"
  if [ "$SKIP_INFLUX" != 1 ]; then
    docker exec "$INFLUX_CONTAINER" rm -rf "$IN_CONTAINER_DIR" >/dev/null 2>&1 || true
  fi
  rmdir "$LOCK" 2>/dev/null || true
  if [ "$OK" != 1 ]; then ping_monitor /fail; write_metrics failure; fi
}
trap cleanup EXIT
umask 077
mkdir -p "$WORK"

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
running "$PG_CONTAINER" || die "container $PG_CONTAINER não está rodando"

# ── PostgreSQL ────────────────────────────────────────────────────
log "pg_dump ($PG_CONTAINER)"
# shellcheck disable=SC2016  # variáveis expandidas DENTRO do container
docker exec "$PG_CONTAINER" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z 6' > "$WORK/postgres.dump"
[ -s "$WORK/postgres.dump" ] || die "pg_dump gerou arquivo vazio"
# shellcheck disable=SC2016
PG_DB="$(docker exec "$PG_CONTAINER" sh -c 'printf %s "$POSTGRES_DB"')"
PG_VERSION="$(docker exec "$PG_CONTAINER" sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "show server_version"' | tr -d '\r')"

# Linhas por tabela contadas no PRÓPRIO dump (exatamente o que o restore deve reproduzir).
log "contando linhas no dump"
docker exec -i "$PG_CONTAINER" pg_restore --data-only -f - < "$WORK/postgres.dump" \
  | awk '
      /^COPY / { t = $2; n = 0; inside = 1; next }
      inside && $0 == "\\." { print t "\t" n; inside = 0; next }
      inside { n++ }
    ' > "$WORK/pg_counts.tsv"

# ── InfluxDB ──────────────────────────────────────────────────────
INFLUX_VERSION=""
COUNT_STOP=""
: > "$WORK/influx_counts.tsv"
if [ "$SKIP_INFLUX" != 1 ]; then
  running "$INFLUX_CONTAINER" || die "container $INFLUX_CONTAINER não está rodando (BACKUP_SKIP_INFLUX=1 para pular)"
  TOKEN_ARGS=()
  [ -n "${INFLUX_TOKEN:-}" ] && TOKEN_ARGS=(-t "$INFLUX_TOKEN")
  INFLUX_VERSION="$(docker exec "$INFLUX_CONTAINER" influxd version 2>/dev/null | awk '{print $2}' || true)"

  if [ "$INFLUX_COUNT" = 1 ]; then
    # Pontos com _time < COUNT_STOP; contados ANTES do backup: o restore precisa ter >= isso.
    COUNT_STOP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log "contando pontos do Influx até $COUNT_STOP"
    buckets="$(docker exec "$INFLUX_CONTAINER" influx query "${TOKEN_ARGS[@]}" --raw \
      'buckets() |> filter(fn: (r) => r.name !~ /^_/) |> keep(columns: ["name"])' \
      | awk -F, '{ sub(/\r$/, "") } $0 !~ /^#/ && $3 ~ /^[0-9]+$/ && $4 != "" { print $4 }')"
    for b in $buckets; do
      n="$(docker exec "$INFLUX_CONTAINER" influx query "${TOKEN_ARGS[@]}" --raw \
        "from(bucket: \"$b\") |> range(start: 0, stop: $COUNT_STOP) |> count() |> group() |> sum()" \
        | awk -F, '$0 !~ /^#/ && $4 ~ /^[0-9]+/ {gsub(/\r/,"",$4); print $4}')"
      printf '%s\t%s\n' "$b" "${n:-0}" >> "$WORK/influx_counts.tsv"
    done
  fi

  log "influx backup ($INFLUX_CONTAINER)"
  docker exec "$INFLUX_CONTAINER" influx backup "${TOKEN_ARGS[@]}" "$IN_CONTAINER_DIR" >/dev/null
  mkdir -p "$WORK/influx"
  docker exec "$INFLUX_CONTAINER" tar -C "$IN_CONTAINER_DIR" -cf - . | tar -C "$WORK/influx" -xf -
  [ -n "$(ls -A "$WORK/influx")" ] || die "influx backup vazio"
fi

# ── Manifest ──────────────────────────────────────────────────────
log "manifest"
(
  cd "$WORK"
  find . -type f ! -name manifest.json ! -name '*.tsv' | sed 's#^\./##' | LC_ALL=C sort | while read -r f; do
    printf '%s\t%s\t%s\n' "$f" "$(sha256sum "$f" | cut -d' ' -f1)" "$(wc -c < "$f" | tr -d ' ')"
  done > files.tsv
)
manifest_json() {   # sem dependência de python/jq no host: JSON montado em awk
  awk -v created="$STAMP" -v host="$(hostname)" -v db="$PG_DB" -v pgv="$PG_VERSION" \
      -v ifv="$INFLUX_VERSION" -v stop="$COUNT_STOP" -v skip="$SKIP_INFLUX" '
    function q(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return "\"" s "\"" }
    FILENAME ~ /files.tsv$/  { files[++nf] = "    " q($1) ": {\"sha256\": " q($2) ", \"bytes\": " $3 "}"; next }
    FILENAME ~ /pg_counts/   { pg[++np] = "    " q($1) ": " $2; next }
    FILENAME ~ /influx_counts/ { ifx[++ni] = "    " q($1) ": " $2; next }
    END {
      print "{"
      print "  \"format\": 1,"
      print "  \"created_at\": " q(created) ","
      print "  \"host\": " q(host) ","
      print "  \"postgres\": {\"database\": " q(db) ", \"server_version\": " q(pgv) "},"
      print "  \"influx\": {\"included\": " (skip == "1" ? "false" : "true") ", \"version\": " q(ifv) ", \"count_stop\": " (stop == "" ? "null" : q(stop)) "},"
      print "  \"files\": {"; for (i = 1; i <= nf; i++) print files[i] (i < nf ? "," : ""); print "  },"
      print "  \"postgres_rows\": {"; for (i = 1; i <= np; i++) print pg[i] (i < np ? "," : ""); print "  },"
      print "  \"influx_points\": {"; for (i = 1; i <= ni; i++) print ifx[i] (i < ni ? "," : ""); print "  }"
      print "}"
    }' "$WORK/files.tsv" "$WORK/pg_counts.tsv" "$WORK/influx_counts.tsv"
}
manifest_json > "$WORK/manifest.json"
# Os .tsv vão junto: restore.sh lê deles (sem jq/python no host); o JSON é para humanos/ferramentas.

# ── Arquivo final ─────────────────────────────────────────────────
OUT="$BACKUP_DIR/$NAME.tar"
tar -C "$WORK" -cf "$OUT.partial" .
if [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$BACKUP_PASSPHRASE_FILE" \
    -in "$OUT.partial" -out "$OUT.enc.partial"
  rm -f "$OUT.partial"
  OUT="$OUT.enc"
fi
mv "$OUT.partial" "$OUT"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
log "ok: $OUT ($(du -h "$OUT" | cut -f1)) — $(wc -l < "$WORK/pg_counts.tsv" | tr -d ' ') tabelas"

# ── Cópia off-site ────────────────────────────────────────────────
if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
  log "upload: BACKUP_UPLOAD_CMD"
  sh -c "$BACKUP_UPLOAD_CMD" fm-backup-upload "$OUT" || die "upload falhou (o backup local foi mantido: $OUT)"
  sh -c "$BACKUP_UPLOAD_CMD" fm-backup-upload "$OUT.sha256" || die "upload do .sha256 falhou"
fi

# ── Rotação (só depois de um backup completo) ─────────────────────
mapfile -t ALL < <(cd "$BACKUP_DIR" && find . -maxdepth 1 -type f \( -name 'fm-backup-*.tar' -o -name 'fm-backup-*.tar.enc' \) \
                   | sed 's#^\./##' | LC_ALL=C sort)
if [ "${#ALL[@]}" -gt "$BACKUP_KEEP" ]; then
  for old in "${ALL[@]:0:${#ALL[@]}-BACKUP_KEEP}"; do
    rm -f "$BACKUP_DIR/$old" "$BACKUP_DIR/$old.sha256"
    log "rotação: removido $old"
  done
fi

OK=1
write_metrics success "$OUT"
ping_monitor ""
echo "$OUT"
