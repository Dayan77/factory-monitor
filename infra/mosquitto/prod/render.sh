#!/bin/sh
# Gera password_file, acl_file e (opcional) o listener TLS do Mosquitto de produção.
# Roda na partida (entrypoint.sh) e em `make mqtt-reload` (docker exec fm_mosquitto sh /fm/render.sh).
# Falha fechado: qualquer entrada inválida aborta sem tocar nos arquivos em uso.
#
# Entradas (env do container):
#   MQTT_SERVER_USERNAME, MQTT_SERVER_PASSWORD  backend — obrigatórios
#   MQTT_GATEWAYS_FILE   (/mosquitto/gateways/gateways.conf) linhas "<usuário> <tenant_uuid> <hash $7$...>"
#   MQTT_TLS_ENABLED     (false) true inclui o listener 8883; exige fullchain.pem e privkey.pem em MQTT_TLS_CERT_DIR
#   MQTT_TLS_CERT_DIR    (/mosquitto/certs)
#   MQTT_TLS_PORT        (8883)
set -eu

RUN=/mosquitto/runtime
GATEWAYS_FILE="${MQTT_GATEWAYS_FILE:-/mosquitto/gateways/gateways.conf}"
TLS_ENABLED="${MQTT_TLS_ENABLED:-false}"
CERT_DIR="${MQTT_TLS_CERT_DIR:-/mosquitto/certs}"
TLS_PORT="${MQTT_TLS_PORT:-8883}"
HEALTH_USER="fm-health"

die() { echo "fm-mqtt-render: $*" >&2; exit 1; }

: "${MQTT_SERVER_USERNAME:?fm-mqtt-render: defina MQTT_USERNAME no .env (usuário MQTT do backend)}"
: "${MQTT_SERVER_PASSWORD:?fm-mqtt-render: defina MQTT_PASSWORD no .env (senha MQTT do backend)}"
[ "${#MQTT_SERVER_PASSWORD}" -ge 16 ] || die "MQTT_PASSWORD precisa de pelo menos 16 caracteres"

valid_user() { printf '%s' "$1" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$'; }
valid_user "$MQTT_SERVER_USERNAME" || die "MQTT_USERNAME inválido (use [A-Za-z0-9_.-], 3-64)"
[ "$MQTT_SERVER_USERNAME" != "$HEALTH_USER" ] || die "MQTT_USERNAME não pode ser $HEALTH_USER"

umask 077
mkdir -p "$RUN/conf.d"
TMP="$(mktemp -d "$RUN/.render.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# Senha do healthcheck: aleatória, só existe dentro do container (reaproveitada entre re-renders).
if [ ! -s "$RUN/health.pw" ]; then
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40 > "$RUN/health.pw"
fi

# Usuários internos: texto puro → mosquitto_passwd -U (hash PBKDF2) sem senha na linha de comando.
printf '%s:%s\n%s:%s\n' "$MQTT_SERVER_USERNAME" "$MQTT_SERVER_PASSWORD" \
  "$HEALTH_USER" "$(cat "$RUN/health.pw")" > "$TMP/passwd"
mosquitto_passwd -U "$TMP/passwd"

cat > "$TMP/acl" <<ACL
# GERADO por render.sh — não edite (refeito a cada partida / make mqtt-reload)
user $MQTT_SERVER_USERNAME
topic read factory/#

user $HEALTH_USER
topic write health/#
ACL

n=0
if [ -f "$GATEWAYS_FILE" ]; then
  set -f   # sem glob ao separar campos
  seen=" $MQTT_SERVER_USERNAME $HEALTH_USER "
  lineno=0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    case "$line" in ''|'#'*) continue ;; esac
    # shellcheck disable=SC2086
    set -- $line
    [ "$#" -eq 3 ] || die "$GATEWAYS_FILE:$lineno: esperado '<usuário> <tenant_uuid> <hash>'"
    user="$1"; tenant="$2"; hash="$3"
    valid_user "$user" || die "$GATEWAYS_FILE:$lineno: usuário inválido '$user'"
    printf '%s' "$tenant" | grep -Eq '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' \
      || die "$GATEWAYS_FILE:$lineno: tenant_id não é UUID: '$tenant'"
    printf '%s' "$hash" | grep -Eq '^\$[67]\$[A-Za-z0-9+/=$]+$' \
      || die "$GATEWAYS_FILE:$lineno: hash inválido para '$user' (gere com make mqtt-gateway-add)"
    case "$seen" in *" $user "*) die "$GATEWAYS_FILE:$lineno: usuário repetido '$user'" ;; esac
    seen="$seen$user "
    printf '%s:%s\n' "$user" "$hash" >> "$TMP/passwd"
    printf '\n# gateway %s\nuser %s\ntopic readwrite factory/%s/#\n' "$user" "$user" "$tenant" >> "$TMP/acl"
    n=$((n + 1))
  done < "$GATEWAYS_FILE"
else
  echo "fm-mqtt-render: $GATEWAYS_FILE não existe — nenhum gateway autorizado ainda" >&2
fi

if [ "$TLS_ENABLED" = "true" ]; then
  for f in fullchain.pem privkey.pem; do
    [ -s "$CERT_DIR/$f" ] || die "MQTT_TLS_ENABLED=true mas $CERT_DIR/$f não existe (veja docs/operations.md §MQTT TLS)"
  done
  # Cópia legível pelo usuário mosquitto (os certificados montados costumam ser root:root 600).
  mkdir -p "$TMP/certs"
  cp "$CERT_DIR/fullchain.pem" "$CERT_DIR/privkey.pem" "$TMP/certs/"
  cat > "$TMP/tls.conf" <<TLS
# GERADO por render.sh — listener TLS dos gateways de campo
listener $TLS_PORT
certfile $RUN/certs/fullchain.pem
keyfile $RUN/certs/privkey.pem
tls_version tlsv1.2
TLS
fi

# Troca atômica (mv no mesmo filesystem); só agora os arquivos em uso mudam.
chmod 0700 "$TMP/passwd" "$TMP/acl"
if [ -d "$TMP/certs" ]; then rm -rf "$RUN/certs"; mv "$TMP/certs" "$RUN/certs"; fi
if [ -f "$TMP/tls.conf" ]; then mv "$TMP/tls.conf" "$RUN/conf.d/tls.conf"; else rm -f "$RUN/conf.d/tls.conf"; fi
mv "$TMP/passwd" "$RUN/passwd"
mv "$TMP/acl" "$RUN/acl"
if [ "$(id -u)" = 0 ]; then chown -R mosquitto:mosquitto "$RUN"; fi
echo "fm-mqtt-render: ok — $n gateway(s), TLS=$TLS_ENABLED"
