#!/bin/sh
# Entrypoint do Mosquitto de produção: gera passwd/acl/TLS (render.sh) e executa o mosquitto como
# PID 1 (SIGHUP recarrega passwd/acl). Ele parte como root e baixa para o usuário `mosquitto`.
# Não usa o /docker-entrypoint.sh da imagem: o `chown -R /mosquitto` dele falha (e polui o log)
# nas montagens somente-leitura de config/gateways/certs.
set -eu
sh /fm/render.sh
if [ "$(id -u)" = 0 ]; then chown -R mosquitto:mosquitto /mosquitto/data; fi
exec "$@"
