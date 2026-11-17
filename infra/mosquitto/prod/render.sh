#!/bin/sh
# Gera password_file, acl_file e (opcional) o listener TLS do Mosquitto de produção.
# Roda na partida (entrypoint.sh) e em `make mqtt-reload` (docker exec fm_mosquitto sh /fm/render.sh).
# Também roda no laço de recarga do entrypoint.sh quando o app file muda.
# Falha fechado: qualquer entrada inválida do OPERADOR aborta sem tocar nos arquivos em uso. O app file
# (gerado pelo fm_server) é TOLERANTE: linha inválida → aviso no stderr e a linha é ignorada — um arquivo
# gerado com defeito nunca impede o broker de subir nem derruba os demais usuários.
#
# Entradas (env do container):
#   MQTT_SERVER_USERNAME, MQTT_SERVER_PASSWORD  backend — obrigatórios
#   MQTT_GATEWAYS_FILE   (/mosquitto/gateways/gateways.conf) arquivo do operador (make mqtt-gateway-add), estrito:
#                        linhas "<usuário> <tenant_uuid> <hash $7$...>"
#   MQTT_APP_GATEWAYS_FILE (/mosquitto/app/gateways.app.conf) gerado pelo fm_server no cadastro/revogação de
#                        gateways, mesmo formato; tolerante. Usuário que colide com o operador/backend/fm-health
#                        ou repete é ignorado (o do operador vale — aviso "sombreado"). Symlink/não regular: ignorado.
#   FM_RENDER_MODE       (vazio) "reload" = recarga automática: gateways.conf inválido não aborta; usa o bloco do
#                        operador da última renderização válida ($RUN/operator.*) e aplica o app file.
#   MQTT_TLS_ENABLED     (false) true inclui o listener 8883; exige fullchain.pem e privkey.pem em MQTT_TLS_CERT_DIR
#   MQTT_TLS_CERT_DIR    (/mosquitto/certs)
#   MQTT_TLS_PORT        (8883)
set -eu

RUN=/mosquitto/runtime
GATEWAYS_FILE="${MQTT_GATEWAYS_FILE:-/mosquitto/gateways/gateways.conf}"
APP_FILE="${MQTT_APP_GATEWAYS_FILE:-/mosquitto/app/gateways.app.conf}"
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

UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
# operador: regra histórica; app file: formato exato do mosquitto_passwd ($7$<iter>$<b64>$<b64> ou $6$<b64>$<b64>)
OP_HASH_RE='^\$[67]\$[A-Za-z0-9+/=$]+$'
# shellcheck disable=SC2016  # $ literal na regex
APP_HASH_RE='^\$(7\$[0-9]{1,7}|6)\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$'
n_skip=0

# Nada de conteúdo cru de linha inválida no log: só o campo usuário, filtrado e truncado.
clean() { printf '%s' "$1" | tr -cd 'A-Za-z0-9_.-' | cut -c1-64; }

# Linha rejeitada: no arquivo do operador aborta (falha fechado); no app file avisa e segue.
reject() {  # reject <modo> <mensagem>
  if [ "$1" = strict ]; then die "$2"; fi
  echo "fm-mqtt-render: AVISO: $2 — linha ignorada" >&2
  n_skip=$((n_skip + 1))
}

# add_gateways <arquivo> <strict|lenient> <prefixo de saída>: grava <prefixo>.passwd/.acl/.users com as linhas
# válidas. Usuários já em $seen (backend, fm-health, operador) não entram.
add_gateways() {
  file="$1"; mode="$2"; out="$3"
  : > "$out.passwd"; : > "$out.acl"; : > "$out.users"
  lineno=0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    case "$line" in ''|'#'*) continue ;; esac
    # shellcheck disable=SC2086
    set -- $line
    if [ "$#" -ne 3 ]; then
      reject "$mode" "$file:$lineno: esperado '<usuário> <tenant_uuid> <hash>' (usuário '$(clean "${1:-}")')"; continue
    fi
    user="$1"; tenant="$2"; hash="$3"; shown="$(clean "$user")"
    if ! valid_user "$user"; then reject "$mode" "$file:$lineno: usuário inválido '$shown'"; continue; fi
    if ! printf '%s' "$tenant" | grep -Eq "$UUID_RE"; then
      reject "$mode" "$file:$lineno: tenant_id não é UUID (usuário '$user')"; continue
    fi
    if [ "$mode" = strict ]; then hash_re="$OP_HASH_RE"; else hash_re="$APP_HASH_RE"; fi
    if ! printf '%s' "$hash" | grep -Eq "$hash_re"; then
      reject "$mode" "$file:$lineno: hash inválido para '$user' (gere com make mqtt-gateway-add)"; continue
    fi
    if [ "$mode" = lenient ] && grep -qxF "$user" "$TMP/op.users"; then
      reject "$mode" "$file:$lineno: usuário $user do app sombreado pelo gateways.conf manual — revogar no app não terá efeito (remova a linha manual: make mqtt-gateway-overlap)"
      continue
    fi
    case "$seen" in *" $user "*)
      reject "$mode" "$file:$lineno: usuário repetido '$user' (backend/fm-health ou repetido neste arquivo)"
      continue ;;
    esac
    seen="$seen$user "
    printf '%s\n' "$user" >> "$out.users"
    printf '%s:%s\n' "$user" "$hash" >> "$out.passwd"
    printf '\n# gateway %s\nuser %s\ntopic readwrite factory/%s/#\n' "$user" "$user" "$tenant" >> "$out.acl"
  done < "$file"
}

set -f   # sem glob ao separar campos

# 1) Operador (estrito). Num subshell: a falha não aborta o render inteiro antes de decidirmos o que fazer.
#    Na partida e no `make mqtt-reload`: inválido → falha (o broker não sobe / a config em uso fica).
#    Na recarga automática (FM_RENDER_MODE=reload, laço do entrypoint.sh): inválido → mantém o bloco do operador
#    da última renderização válida ($RUN/operator.*) e aplica mesmo assim o app file — um gateways.conf quebrado
#    não congela cadastros/revogações feitos no app.
op_from_last=0
if [ -f "$GATEWAYS_FILE" ]; then
  if ! ( seen=" $MQTT_SERVER_USERNAME $HEALTH_USER "; : > "$TMP/op.users"
         add_gateways "$GATEWAYS_FILE" strict "$TMP/op" ) 2> "$TMP/op.err"; then
    if [ "${FM_RENDER_MODE:-}" = reload ] && [ -f "$RUN/operator.passwd" ] && [ -f "$RUN/operator.acl" ] \
        && [ -f "$RUN/operator.users" ]; then
      echo "fm-mqtt-render: AVISO: $GATEWAYS_FILE inválido — mantidos os gateways manuais da última config válida; app file aplicado. Corrija:" >&2
      cat "$TMP/op.err" >&2
      cp "$RUN/operator.passwd" "$TMP/op.passwd"; cp "$RUN/operator.acl" "$TMP/op.acl"; cp "$RUN/operator.users" "$TMP/op.users"
      op_from_last=1
    else
      cat "$TMP/op.err" >&2
      exit 1
    fi
  fi
else
  echo "fm-mqtt-render: $GATEWAYS_FILE não existe — nenhum gateway manual (do operador)" >&2
  : > "$TMP/op.passwd"; : > "$TMP/op.acl"; : > "$TMP/op.users"
fi
n_op="$(grep -c . "$TMP/op.users" || true)"

# 2) App file (tolerante). Só arquivo regular (nada de symlink/dispositivo/diretório no volume).
seen=" $MQTT_SERVER_USERNAME $HEALTH_USER "
: > "$TMP/app.passwd"; : > "$TMP/app.acl"; : > "$TMP/app.users"
if [ -L "$APP_FILE" ]; then
  echo "fm-mqtt-render: AVISO: $APP_FILE é um link simbólico — gateways do app ignorados" >&2
elif [ -e "$APP_FILE" ] && [ ! -f "$APP_FILE" ]; then
  echo "fm-mqtt-render: AVISO: $APP_FILE não é um arquivo regular — gateways do app ignorados" >&2
elif [ -f "$APP_FILE" ]; then
  if [ -r "$APP_FILE" ]; then
    add_gateways "$APP_FILE" lenient "$TMP/app"
  else
    echo "fm-mqtt-render: AVISO: $APP_FILE sem permissão de leitura — gateways do app ignorados" >&2
  fi
fi
set +f
n_app="$(grep -c . "$TMP/app.users" || true)"
n=$((n_op + n_app))
cat "$TMP/op.passwd" "$TMP/app.passwd" >> "$TMP/passwd"
cat "$TMP/op.acl" "$TMP/app.acl" >> "$TMP/acl"

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
# Bloco do operador desta renderização válida: base da recarga automática se o gateways.conf quebrar depois.
if [ "$op_from_last" = 0 ]; then
  for f in passwd acl users; do mv "$TMP/op.$f" "$RUN/operator.$f"; done
fi
if [ "$(id -u)" = 0 ]; then chown -R mosquitto:mosquitto "$RUN"; fi
last=""; [ "$op_from_last" = 1 ] && last=" da última config válida"
echo "fm-mqtt-render: ok — $n gateway(s) (operador: $n_op$last, app: $n_app, ignorados: $n_skip), TLS=$TLS_ENABLED"
