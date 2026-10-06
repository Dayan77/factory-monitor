#!/usr/bin/env bash
# FactoryConnect — instala o gateway de campo como serviço systemd (alternativa ao Docker; IPC Debian/Ubuntu ou
# Raspberry Pi OS 64 bits). Rodar como root a partir de um checkout/cópia do repositório:
#
#   sudo edge/deploy/install.sh --server https://app.factoryconnect.tech --code ABCD-EFGH-JKLM \
#        --mqtt-host mqtt.factoryconnect.tech
#   sudo edge/deploy/install.sh --uninstall            # remove serviço e código (mantém /var/lib e /etc)
#
# O que faz (idempotente — rodar de novo atualiza o código e mantém a configuração e o estado):
#   1. usuário de sistema `factoryconnect` (sem login);
#   2. código do edge em /opt/factoryconnect/edge e venv em /opt/factoryconnect/venv (wheels binários, sem gcc);
#   3. /etc/factoryconnect/gateway.env (0640 root:factoryconnect) a partir de infra/gateway/gateway.env.example,
#      só se ainda não existir (--server/--code/--mqtt-host preenchem as linhas);
#   4. unidade factoryconnect-gateway.service (estado em /var/lib/factoryconnect, 0700) habilitada e iniciada.
#
# Testes/empacotamento: DESTDIR=/tmp/raiz instala tudo sob esse prefixo; --no-systemd não chama o systemctl;
# --no-venv não cria o venv (sem rede). Requisitos: python3 ≥ 3.11 com venv (apt install python3-venv).
set -euo pipefail

PREFIX="/opt/factoryconnect"
ETC="/etc/factoryconnect"
UNIT_DIR="/etc/systemd/system"
SERVICE="factoryconnect-gateway"
USER_NAME="factoryconnect"
DESTDIR="${DESTDIR:-}"
SERVER="" CODE="" MQTT_HOST=""
DO_SYSTEMD=1 DO_VENV=1 UNINSTALL=0
PYTHON="${PYTHON:-python3}"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --code) CODE="$2"; shift 2 ;;
    --mqtt-host) MQTT_HOST="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --no-systemd) DO_SYSTEMD=0; shift ;;
    --no-venv) DO_VENV=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "opção desconhecida: $1" >&2; usage 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_SRC="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$EDGE_SRC/.." && pwd)"
ENV_EXAMPLE="$REPO/infra/gateway/gateway.env.example"

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
  echo "rode como root (sudo) — ou use DESTDIR=<dir> para instalar num diretório de teste" >&2
  exit 1
fi

run_systemctl() { [ "$DO_SYSTEMD" -eq 1 ] && systemctl "$@" || true; }

if [ "$UNINSTALL" -eq 1 ]; then
  run_systemctl disable --now "$SERVICE" 2>/dev/null
  rm -f "$DESTDIR$UNIT_DIR/$SERVICE.service"
  run_systemctl daemon-reload
  rm -rf "$DESTDIR$PREFIX"
  echo "removido. Mantidos: $ETC (configuração) e /var/lib/factoryconnect (credencial, buffer, contadores)."
  exit 0
fi

case "$SERVER" in
  ""|https://*|http://localhost*|http://127.0.0.1*) ;;
  *) echo "--server deve ser https:// (o token do gateway viaja na resposta do cadastro)" >&2; exit 2 ;;
esac

"$PYTHON" - <<'EOF' || { echo "precisa de python3 >= 3.11" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF

# 1. usuário de sistema
if [ -z "$DESTDIR" ] && ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/factoryconnect --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi

# 2. código + venv
install -d -m 0755 "$DESTDIR$PREFIX" "$DESTDIR$PREFIX/edge"
for d in src virtual_plant config; do
  rm -rf "$DESTDIR$PREFIX/edge/$d"
  cp -a "$EDGE_SRC/$d" "$DESTDIR$PREFIX/edge/$d"
done
install -m 0644 "$EDGE_SRC/requirements.txt" "$DESTDIR$PREFIX/edge/requirements.txt"
find "$DESTDIR$PREFIX/edge" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$DESTDIR$PREFIX/edge" -name '*.local.json' -delete
if [ "$DO_VENV" -eq 1 ]; then
  [ -x "$DESTDIR$PREFIX/venv/bin/python" ] || "$PYTHON" -m venv "$DESTDIR$PREFIX/venv"
  "$DESTDIR$PREFIX/venv/bin/pip" install --quiet --only-binary=:all: -r "$DESTDIR$PREFIX/edge/requirements.txt"
fi

# 3. configuração (nunca sobrescreve uma existente)
install -d -m 0750 "$DESTDIR$ETC"
ENV_FILE="$DESTDIR$ETC/gateway.env"
if [ ! -f "$ENV_FILE" ]; then
  umask 027
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  [ -n "$SERVER" ] && sed -i "s|^SERVER_URL=.*|SERVER_URL=$SERVER|" "$ENV_FILE"
  [ -n "$CODE" ] && sed -i "s|^ENROLL_CODE=.*|ENROLL_CODE=$CODE|" "$ENV_FILE"
  [ -n "$MQTT_HOST" ] && sed -i "s|^MQTT_BROKER_HOST=.*|MQTT_BROKER_HOST=$MQTT_HOST|" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
  echo "configuração criada: $ETC/gateway.env (revise broker MQTT/TLS antes de iniciar)"
elif [ -n "$CODE" ]; then
  if grep -q '^ENROLL_CODE=' "$ENV_FILE"; then
    sed -i "s|^ENROLL_CODE=.*|ENROLL_CODE=$CODE|" "$ENV_FILE"
  else
    echo "ENROLL_CODE=$CODE" >> "$ENV_FILE"
  fi
fi
[ -z "$DESTDIR" ] && chown -R root:"$USER_NAME" "$ETC"

# 4. serviço
install -d -m 0755 "$DESTDIR$UNIT_DIR"
install -m 0644 "$HERE/systemd/$SERVICE.service" "$DESTDIR$UNIT_DIR/$SERVICE.service"
if [ "$PREFIX" != "/opt/factoryconnect" ]; then
  sed -i "s|/opt/factoryconnect|$PREFIX|g" "$DESTDIR$UNIT_DIR/$SERVICE.service"
fi
run_systemctl daemon-reload
run_systemctl enable --now "$SERVICE"
run_systemctl restart "$SERVICE"

cat <<EOF
FactoryConnect gateway instalado em ${DESTDIR}${PREFIX}.
  estado:   systemctl status $SERVICE
  logs:     journalctl -u $SERVICE -f      (procure enrollment.done e edge_gateway.config)
  config:   $ETC/gateway.env   ·   credencial do cadastro: /var/lib/factoryconnect/gateway_credentials.json (0600)
Depois do cadastro, o ENROLL_CODE pode ser apagado do gateway.env (é de uso único).
EOF
