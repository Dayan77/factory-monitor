#!/bin/sh
# Entrypoint do Mosquitto de produção: gera passwd/acl/TLS (render.sh) e executa o mosquitto como
# PID 1 (SIGHUP recarrega passwd/acl). Ele parte como root e baixa para o usuário `mosquitto`.
# Não usa o /docker-entrypoint.sh da imagem: o `chown -R /mosquitto` dele falha (e polui o log)
# nas montagens somente-leitura de config/gateways/certs.
#
# Recarga automática: um laço em segundo plano compara, a cada MQTT_RELOAD_POLL_S segundos (padrão 5; 0 desliga),
# o cksum do arquivo do operador (MQTT_GATEWAYS_FILE) e do app file gerado pelo fm_server (MQTT_APP_GATEWAYS_FILE).
# Mudou → `sh /fm/render.sh` e, se ok, SIGHUP no mosquitto (o PID deste shell, que vira o mosquitto no exec;
# é o PID 1 do container): o gateway cadastrado no app passa a conectar e
# o revogado deixa de conectar sem restart. Se o render falhar, a config em uso continua valendo (só loga) e a
# próxima tentativa acontece quando algum dos arquivos mudar de novo (sem repetir o erro a cada volta).
set -eu

GATEWAYS_FILE="${MQTT_GATEWAYS_FILE:-/mosquitto/gateways/gateways.conf}"
APP_FILE="${MQTT_APP_GATEWAYS_FILE:-/mosquitto/app/gateways.app.conf}"
POLL="${MQTT_RELOAD_POLL_S:-5}"
case "$POLL" in ''|*[!0-9]*) echo "fm-mqtt-reload: MQTT_RELOAD_POLL_S inválido ('$POLL'), usando 5" >&2; POLL=5 ;; esac

# Assinatura dos dois arquivos; ausente/ilegível não é erro (o app file só existe depois do 1º sync do servidor).
files_sig() {
  for f in "$GATEWAYS_FILE" "$APP_FILE"; do
    if [ ! -e "$f" ]; then
      echo absent
    elif [ -r "$f" ]; then
      { cksum < "$f"; } 2>/dev/null || echo unreadable
    else
      echo unreadable
    fi
  done
}

reload_loop() {
  set +e   # o laço nunca morre por um comando que falhe
  last="$1"
  while :; do
    sleep "$POLL"
    cur="$(files_sig)"
    [ "$cur" = "$last" ] && continue
    last="$cur"
    if FM_RENDER_MODE=reload sh /fm/render.sh; then
      kill -HUP "$MAIN_PID" || echo "fm-mqtt-reload: falha ao enviar SIGHUP ao mosquitto" >&2
    else
      echo "fm-mqtt-reload: render falhou — mantida a configuração em uso (corrija o arquivo de gateways)" >&2
    fi
  done
}

# Assinatura ANTES do render inicial: uma mudança durante a partida é vista na primeira volta do laço.
initial_sig="$(files_sig)"
MAIN_PID=$$
sh /fm/render.sh
if [ "$(id -u)" = 0 ]; then chown -R mosquitto:mosquitto /mosquitto/data; fi
if [ "$POLL" -gt 0 ]; then
  reload_loop "$initial_sig" &
fi
exec "$@"
