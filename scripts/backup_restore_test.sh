#!/usr/bin/env bash
# =============================================================
# FactoryConnect — teste automatizado de backup + restauração (make backup-restore-test; job do CI).
#
#   scripts/backup_restore_test.sh            # origem sintética: sobe Postgres/Influx descartáveis com dados
#   BRT_SOURCE=dev scripts/backup_restore_test.sh
#                                             # origem = stack de dev rodando (fm_postgres/fm_influxdb),
#                                             # só LEITURA nela (pg_dump + influx backup)
#
# 1. backup.sh da origem
# 2. sobe Postgres e InfluxDB NOVOS (mesmas imagens da produção, outra senha/token)
# 3. restore.sh neles (confere linhas por tabela == dump e pontos por bucket >= origem)
# 4. compara também com a contagem AO VIVO da origem (exata na origem sintética; na de dev,
#    que pode estar recebendo dados, só avisa)
# Tudo que o teste cria (containers, rede, diretório) é removido no final.
# =============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PG_IMAGE="${PG_IMAGE:-postgres:16.15-alpine}"
INFLUX_IMAGE="${INFLUX_IMAGE:-influxdb:2.7.12-alpine}"
SOURCE="${BRT_SOURCE:-synthetic}"
ID="brt$$"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fm-brt.XXXXXX")"
CREATED=()

log() { printf '\n▶ %s\n' "$*" >&2; }
die() { echo "✗ $*" >&2; exit 1; }

cleanup() {
  for c in "${CREATED[@]}"; do docker rm -f "$c" >/dev/null 2>&1 || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

wait_pg() {
  for _ in $(seq 1 60); do
    # pg_isready via socket fica "pronto" ainda no initdb; uma query TCP só passa no servidor final
    docker exec "$1" psql -X -h 127.0.0.1 -U fm_user -d factory_monitor -Atc 'select 1' >/dev/null 2>&1 && return 0
    sleep 1
  done
  docker logs "$1" | tail -20 >&2; die "postgres $1 não ficou pronto"
}
wait_influx() {
  for _ in $(seq 1 90); do
    # --host explícito: durante o setup o entrypoint sobe um influxd temporário na INFLUXD_INIT_PORT (9999) e o
    # config do CLI aponta para ele; sem --host o "pronto" passa no servidor de init, que é derrubado em seguida
    docker exec "$1" influx bucket list --host http://localhost:8086 >/dev/null 2>&1 && return 0
    sleep 1
  done
  docker logs "$1" | tail -20 >&2; die "influx $1 não ficou pronto"
}
start_pg() {   # nome senha
  docker run -d --name "$1" -e POSTGRES_DB=factory_monitor -e POSTGRES_USER=fm_user -e POSTGRES_PASSWORD="$2" \
    -v "$ROOT/infra/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro" "$PG_IMAGE" >/dev/null
  CREATED+=("$1"); wait_pg "$1"
}
start_influx() {   # nome token
  docker run -d --name "$1" -e DOCKER_INFLUXDB_INIT_MODE=setup -e DOCKER_INFLUXDB_INIT_USERNAME=admin \
    -e DOCKER_INFLUXDB_INIT_PASSWORD=brt-admin-password -e DOCKER_INFLUXDB_INIT_ORG=factory-monitor \
    -e DOCKER_INFLUXDB_INIT_BUCKET=sensors -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN="$2" "$INFLUX_IMAGE" >/dev/null
  CREATED+=("$1"); wait_influx "$1"
}

pg_live_counts() {   # container db → "schema.tabela<TAB>n" (todas as tabelas de usuário)
  docker exec "$1" psql -X -At -F $'\t' -U fm_user -d "$2" -v ON_ERROR_STOP=1 -c "
    select format('%I.%I', schemaname, relname),
           (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', schemaname, relname),
                                                false, true, '')))[1]::text::bigint
      from pg_stat_user_tables order by 1"
}
influx_live_count() {   # container token bucket stop
  docker exec "$1" influx query -t "$2" --raw \
    "from(bucket: \"$3\") |> range(start: 0, stop: $4) |> count() |> group() |> sum()" \
    | awk -F, '$0 !~ /^#/ && $4 ~ /^[0-9]+/ {gsub(/\r/,"",$4); print $4}'
}

# ── Origem ────────────────────────────────────────────────────────
if [ "$SOURCE" = dev ]; then
  SRC_PG="${PG_CONTAINER:-fm_postgres}"; SRC_INFLUX="${INFLUX_CONTAINER:-fm_influxdb}"
  # shellcheck disable=SC2016
  SRC_TOKEN="${INFLUX_TOKEN:-$(docker exec "$SRC_INFLUX" sh -c 'printf %s "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"')}"
  log "origem: stack de dev ($SRC_PG, $SRC_INFLUX) — somente leitura"
else
  SRC_PG="fm_${ID}_src_pg"; SRC_INFLUX="fm_${ID}_src_influx"; SRC_TOKEN="brt-source-token-$ID"
  log "origem sintética: $SRC_PG + $SRC_INFLUX"
  start_pg "$SRC_PG" "brt-source-password"
  start_influx "$SRC_INFLUX" "$SRC_TOKEN"
  # Dados com os casos chatos de COPY/restore: NULL, tab/newline/barra no texto, bytea, jsonb, uuid,
  # extensão (pgcrypto), FK, sequência, schema extra, timestamptz.
  docker exec -i "$SRC_PG" psql -X -q -v ON_ERROR_STOP=1 -U fm_user -d factory_monitor <<'SQL'
create table tenants (id uuid primary key default gen_random_uuid(), name text not null);
create table machines (id serial primary key, tenant_id uuid references tenants(id), name text, meta jsonb,
                       blob bytea, created_at timestamptz default now());
create schema audit;
create table audit.events (id bigserial primary key, msg text);
insert into tenants (name) select 'tenant ' || g from generate_series(1, 7) g;
insert into machines (tenant_id, name, meta, blob)
  select (select id from tenants order by name limit 1 offset (g % 7)),
         E'máquina\t' || g || E'\nlinha 2 \\ barra', jsonb_build_object('g', g, 'tags', jsonb_build_array(g, null)),
         decode(md5(g::text), 'hex')
    from generate_series(1, 1234) g;
insert into machines (tenant_id, name) values (null, null);
insert into audit.events (msg) select repeat('x', g % 50) from generate_series(1, 321) g;
create table empty_table (id int);
SQL
  # 3 séries × 2 campos × 600 pontos no bucket sensors + um bucket extra
  now_s="$(date -u +%s)"
  awk -v now="$now_s" 'BEGIN { for (t = 1; t <= 3; t++) for (i = 0; i < 600; i++)
      printf "readings,tag_id=tag-%d,tenant_id=t1 value=%d.5,quality=%di %d\n", t, i, i % 3, now - 7200 + i * 10 }' \
    > "$WORK/points.lp"
  docker exec -i "$SRC_INFLUX" influx write -t "$SRC_TOKEN" -b sensors -p s < "$WORK/points.lp"
  docker exec "$SRC_INFLUX" influx bucket create -t "$SRC_TOKEN" -n extra -o factory-monitor >/dev/null
  echo "usage,machine=m1 cycles=42i $((now_s - 60))" | docker exec -i "$SRC_INFLUX" influx write -t "$SRC_TOKEN" -b extra -p s
fi

# ── 1. Backup ─────────────────────────────────────────────────────
log "backup.sh"
ARCHIVE="$(PG_CONTAINER="$SRC_PG" INFLUX_CONTAINER="$SRC_INFLUX" INFLUX_TOKEN="$SRC_TOKEN" \
  BACKUP_DIR="$WORK/backups" BACKUP_KEEP=3 "$ROOT/scripts/backup.sh")"
[ -f "$ARCHIVE" ] && [ -f "$ARCHIVE.sha256" ] || die "backup.sh não gerou $ARCHIVE(.sha256)"
tar -xOf "$ARCHIVE" ./manifest.json > "$WORK/manifest.json"
tar -xOf "$ARCHIVE" ./pg_counts.tsv > "$WORK/pg_counts.tsv"
tar -xOf "$ARCHIVE" ./influx_counts.tsv > "$WORK/influx_counts.tsv"
STOP="$(awk -F'"' '/"count_stop"/ { for (i = 1; i <= NF; i++) if ($i == "count_stop") { print $(i + 2); exit } }' "$WORK/manifest.json")"
echo "  arquivo: $(basename "$ARCHIVE") · $(wc -l < "$WORK/pg_counts.tsv" | tr -d ' ') tabelas · buckets: $(cut -f1 "$WORK/influx_counts.tsv" | tr '\n' ' ')"

# ── 2. Destino novo ───────────────────────────────────────────────
DST_PG="fm_${ID}_dst_pg"; DST_INFLUX="fm_${ID}_dst_influx"
log "destino novo: $DST_PG + $DST_INFLUX"
start_pg "$DST_PG" "brt-target-password"
start_influx "$DST_INFLUX" "brt-target-token-$ID"

# ── 3. Restore (verifica contra o manifest) ───────────────────────
log "restore.sh"
RESTORE_CONFIRM=yes PG_CONTAINER="$DST_PG" INFLUX_CONTAINER="$DST_INFLUX" APP_CONTAINER="fm_${ID}_no_app" \
  INFLUX_TOKEN="$SRC_TOKEN" "$ROOT/scripts/restore.sh" "$ARCHIVE"

# ── 4. Comparação com a origem ao vivo ────────────────────────────
log "comparando destino com a origem ao vivo"
src_db="$(docker exec "$SRC_PG" sh -c 'printf %s "$POSTGRES_DB"')"
pg_live_counts "$SRC_PG" "$src_db" > "$WORK/src.tsv"
pg_live_counts "$DST_PG" factory_monitor > "$WORK/dst.tsv"
strict=1; [ "$SOURCE" = dev ] && strict=0
problems=0
if ! diff -u "$WORK/src.tsv" "$WORK/dst.tsv" > "$WORK/pg.diff"; then
  if [ "$strict" = 1 ]; then cat "$WORK/pg.diff" >&2; problems=1
  else echo "  ! contagem da origem mudou depois do backup (dev recebendo dados):"; head -20 "$WORK/pg.diff"; fi
else
  echo "  ✓ Postgres: $(wc -l < "$WORK/dst.tsv" | tr -d ' ') tabelas idênticas à origem"
fi
# as tabelas do manifest são exatamente as tabelas da origem
if ! diff <(cut -f1 "$WORK/src.tsv" | sed 's/"//g' | sort) <(cut -f1 "$WORK/pg_counts.tsv" | sed 's/"//g' | sort) >/dev/null; then
  echo "  ✗ tabelas no dump ≠ tabelas da origem" >&2; problems=1
fi
if [ "$strict" = 1 ]; then
  grep -q $'^public.machines\t1235$' "$WORK/dst.tsv" || { echo "  ✗ machines deveria ter 1235 linhas" >&2; problems=1; }
  grep -q $'^audit.events\t321$' "$WORK/dst.tsv" || { echo "  ✗ audit.events deveria ter 321 linhas" >&2; problems=1; }
  # conteúdo, não só contagem: hash das linhas da tabela mais "chata"
  q="select md5(string_agg(t::text, '|' order by id)) from machines t"
  [ "$(docker exec "$SRC_PG" psql -X -At -U fm_user -d factory_monitor -c "$q")" = \
    "$(docker exec "$DST_PG" psql -X -At -U fm_user -d factory_monitor -c "$q")" ] \
    || { echo "  ✗ conteúdo de machines difere" >&2; problems=1; }
  docker exec "$DST_PG" psql -X -At -U fm_user -d factory_monitor -c "select gen_random_uuid()" >/dev/null \
    || { echo "  ✗ extensão pgcrypto ausente no destino" >&2; problems=1; }
fi
while IFS=$'\t' read -r bucket expected; do
  [ -n "$bucket" ] || continue
  src_n="$(influx_live_count "$SRC_INFLUX" "$SRC_TOKEN" "$bucket" "$STOP")"; src_n="${src_n:-0}"
  dst_n="$(influx_live_count "$DST_INFLUX" "$SRC_TOKEN" "$bucket" "$STOP")"; dst_n="${dst_n:-0}"
  if [ "$src_n" = "$dst_n" ]; then echo "  ✓ Influx $bucket: $dst_n pontos (manifest: $expected)"
  elif [ "$strict" = 1 ]; then echo "  ✗ Influx $bucket: origem $src_n, destino $dst_n" >&2; problems=1
  else echo "  ! Influx $bucket: origem $src_n agora, destino $dst_n (manifest: $expected)"; fi
done < "$WORK/influx_counts.tsv"
if [ "$strict" = 1 ]; then
  [ "$(awk -F'\t' '$1=="sensors"{print $2}' "$WORK/influx_counts.tsv")" = 3600 ] \
    || { echo "  ✗ bucket sensors deveria ter 3600 pontos no manifest" >&2; problems=1; }
  [ "$(awk -F'\t' '$1=="extra"{print $2}' "$WORK/influx_counts.tsv")" = 1 ] \
    || { echo "  ✗ bucket extra deveria ter 1 ponto no manifest" >&2; problems=1; }
fi

[ "$problems" = 0 ] || die "backup/restore: divergências encontradas"
echo
echo "✓ backup → restore em containers novos verificado ($SOURCE)"
