#!/bin/sh
# Renderiza alertmanager.yml a partir do modelo + variáveis do .env e inicia o Alertmanager.
# Recusa subir sem destino de e-mail ou SMTP: alerta que ninguém recebe é pior que alerta nenhum (o Prometheus
# mostra FmAlertmanagerDown). Variáveis: ALERT_EMAIL_TO (obrigatória), ALERT_EMAIL_CRITICAL_TO (padrão = TO),
# ALERT_EMAIL_FROM (padrão SMTP_FROM), SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD,
# SMTP_REQUIRE_TLS (true). Para SendGrid: SMTP_HOST=smtp.sendgrid.net, SMTP_USER=apikey, SMTP_PASSWORD=<API key>.
set -eu
fail() { echo "alertmanager: $*" >&2; exit 1; }
: "${ALERT_EMAIL_TO:=}"; : "${SMTP_HOST:=}"; : "${SMTP_PORT:=587}"; : "${SMTP_USER:=}"; : "${SMTP_PASSWORD:=}"
: "${SMTP_FROM:=}"; : "${ALERT_EMAIL_FROM:=$SMTP_FROM}"; : "${ALERT_EMAIL_CRITICAL_TO:=$ALERT_EMAIL_TO}"
: "${SMTP_REQUIRE_TLS:=true}"
[ -n "$ALERT_EMAIL_TO" ] || fail "defina ALERT_EMAIL_TO no .env (destino dos alertas)"
[ -n "$SMTP_HOST" ] || fail "defina SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD no .env"
[ -n "$ALERT_EMAIL_FROM" ] || fail "defina ALERT_EMAIL_FROM (ou SMTP_FROM) no .env"
case "$SMTP_REQUIRE_TLS" in true|false) ;; *) fail "SMTP_REQUIRE_TLS deve ser true ou false" ;; esac
for v in "$ALERT_EMAIL_TO" "$ALERT_EMAIL_CRITICAL_TO" "$ALERT_EMAIL_FROM" "$SMTP_HOST" "$SMTP_PORT" "$SMTP_USER"; do
  case "$v" in *'"'*|*'|'*|*'\'*) fail "caractere inválido (\" | \\) numa variável ALERT_*/SMTP_*" ;; esac
done
RUN=/alertmanager/runtime
mkdir -p "$RUN"
umask 077
printf '%s' "$SMTP_PASSWORD" > "$RUN/smtp_password"
sed -e "s|__SMTP_SMARTHOST__|$SMTP_HOST:$SMTP_PORT|" -e "s|__ALERT_EMAIL_FROM__|$ALERT_EMAIL_FROM|" \
    -e "s|__SMTP_USER__|$SMTP_USER|" -e "s|__SMTP_REQUIRE_TLS__|$SMTP_REQUIRE_TLS|" \
    -e "s|__ALERT_EMAIL_TO__|$ALERT_EMAIL_TO|" -e "s|__ALERT_EMAIL_CRITICAL_TO__|$ALERT_EMAIL_CRITICAL_TO|" \
    /etc/alertmanager/alertmanager.yml.tmpl > "$RUN/alertmanager.yml"
amtool check-config "$RUN/alertmanager.yml" >/dev/null || fail "configuração inválida (amtool check-config)"
if [ "${ALERTMANAGER_CHECK_ONLY:-0}" = 1 ]; then echo "alertmanager: configuração OK"; exit 0; fi   # CI/testes
exec /bin/alertmanager --config.file="$RUN/alertmanager.yml" --storage.path=/alertmanager --cluster.listen-address= \
  --data.retention="${ALERTMANAGER_RETENTION:-120h}" "$@"
