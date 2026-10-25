#!/usr/bin/env bash
# =============================================================
# FactoryConnect — credenciais MQTT por gateway (Mosquitto de produção).
#
#   scripts/mqtt_gateway.sh add <usuário> <tenant_uuid>   cria (ou troca a senha de) um gateway
#   scripts/mqtt_gateway.sh remove <usuário>              revoga
#   scripts/mqtt_gateway.sh list                          lista usuário + tenant (sem hashes)
#   scripts/mqtt_gateway.sh reload                        re-renderiza passwd/acl no container e envia SIGHUP
#
# Atalhos: make mqtt-gateway-add GATEWAY=gw-x TENANT=<uuid> · mqtt-gateway-remove · mqtt-gateway-list · mqtt-reload
#
# Guarda só o HASH (PBKDF2, mosquitto_passwd) em $MQTT_GATEWAYS_FILE; a senha é impressa UMA vez.
# O ACL gerado restringe o gateway a factory/<tenant_uuid>/#.
#
# Env: MQTT_GATEWAYS_FILE (padrão ./mqtt/gateways.conf, = /opt/factory-monitor/mqtt/gateways.conf no VPS),
#      MQTT_CONTAINER (fm_mosquitto), MQTT_IMAGE (eclipse-mosquitto:2.0.22), MQTT_NO_RELOAD=1 (não recarrega),
#      MQTT_PUBLIC_HOST (host mostrado no resumo, padrão mqtt.factoryconnect.tech), MQTT_TLS_PORT (8883).
# =============================================================
set -euo pipefail

FILE="${MQTT_GATEWAYS_FILE:-mqtt/gateways.conf}"
CONTAINER="${MQTT_CONTAINER:-fm_mosquitto}"
IMAGE="${MQTT_IMAGE:-eclipse-mosquitto:2.0.22}"
PUBLIC_HOST="${MQTT_PUBLIC_HOST:-mqtt.factoryconnect.tech}"
TLS_PORT="${MQTT_TLS_PORT:-8883}"
RESERVED_RE='^(fm-health)$'
USER_RE='^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$'
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

die() { echo "✗ $*" >&2; exit 1; }
usage() { sed -n '3,9p' "$0" >&2; exit 2; }

running() { docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; }

reload() {
  [ "${MQTT_NO_RELOAD:-0}" = 1 ] && { echo "  (MQTT_NO_RELOAD=1: rode 'make mqtt-reload' depois)"; return 0; }
  if running; then
    docker exec "$CONTAINER" sh /fm/render.sh
    docker kill -s HUP "$CONTAINER" >/dev/null
    echo "  ✓ $CONTAINER recarregado (SIGHUP)"
  else
    echo "  ! $CONTAINER não está rodando — a credencial vale na próxima partida"
  fi
}

ensure_file() {
  mkdir -p "$(dirname "$FILE")"
  if [ ! -f "$FILE" ]; then
    (umask 077; printf '# <usuário> <tenant_uuid> <hash> — gerado por scripts/mqtt_gateway.sh\n' > "$FILE")
  fi
  chmod 600 "$FILE"
}

# Imprime "usuário:hash" para a senha em $FM_GW_PASSWORD (passada por env, nunca na linha de comando).
hash_password() {
  local user="$1"
  docker run --rm -i -e FM_GW_USER="$user" -e FM_GW_PASSWORD --entrypoint sh "$IMAGE" -c '
    set -e; f=$(mktemp)
    printf "%s:%s\n" "$FM_GW_USER" "$FM_GW_PASSWORD" > "$f"
    mosquitto_passwd -U "$f"
    cat "$f"' </dev/null
}

remove_line() {
  local user="$1" tmp
  tmp="$(mktemp "$FILE.XXXXXX")"
  awk -v u="$user" '$1 != u' "$FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$FILE"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  add)
    [ $# -eq 2 ] || usage
    user="$1"; tenant="$2"
    [[ "$user" =~ $USER_RE ]] || die "usuário inválido '$user' (use [A-Za-z0-9_.-], 3–64 caracteres)"
    [[ "$user" =~ $RESERVED_RE ]] && die "usuário reservado: $user"
    [[ "$tenant" =~ $UUID_RE ]] || die "TENANT precisa ser o UUID do tenant: '$tenant'"
    ensure_file
    FM_GW_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 || true)"
    [ "${#FM_GW_PASSWORD}" -eq 32 ] || die "falha ao gerar senha"
    export FM_GW_PASSWORD
    line="$(hash_password "$user")"
    hash="${line#"$user":}"
    [[ "$hash" =~ ^\$7\$ ]] || die "mosquitto_passwd não devolveu um hash PBKDF2"
    existed=0; awk -v u="$user" '$1 == u {f=1} END {exit !f}' "$FILE" && existed=1
    remove_line "$user"
    printf '%s %s %s\n' "$user" "$tenant" "$hash" >> "$FILE"
    if [ "$existed" = 1 ]; then echo "✓ senha de '$user' trocada (a anterior deixa de valer no reload)"; else echo "✓ gateway '$user' criado"; fi
    reload
    cat <<MSG

  Configure no gateway (edge .env) — a senha NÃO é guardada e não será mostrada de novo:
    TENANT_ID=$tenant
    MQTT_BROKER_HOST=$PUBLIC_HOST
    MQTT_BROKER_PORT=$TLS_PORT
    MQTT_TLS=true
    MQTT_USERNAME=$user
    MQTT_PASSWORD=$FM_GW_PASSWORD
MSG
    unset FM_GW_PASSWORD
    ;;
  remove)
    [ $# -eq 1 ] || usage
    [ -f "$FILE" ] || die "$FILE não existe"
    awk -v u="$1" '$1 == u {f=1} END {exit !f}' "$FILE" || die "gateway '$1' não encontrado em $FILE"
    remove_line "$1"
    echo "✓ gateway '$1' revogado"
    reload
    echo "  Conexões já abertas continuam até reconectar: para derrubar agora, docker restart $CONTAINER"
    ;;
  list)
    [ -f "$FILE" ] || { echo "(nenhum gateway: $FILE não existe)"; exit 0; }
    awk '!/^[[:space:]]*(#|$)/ {printf "%-32s %s\n", $1, $2}' "$FILE"
    ;;
  reload)
    reload
    ;;
  *) usage ;;
esac
