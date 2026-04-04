#!/bin/bash
# =============================================================
# Factory Monitor — Ativa SSL com Let's Encrypt
# Execute no VPS APÓS o domínio factoryconnect.tech apontar para o IP
# =============================================================
set -euo pipefail

DOMAIN="factoryconnect.tech"
EMAIL="${1:-}"
APP_DIR="/opt/factory-monitor"

if [ -z "$EMAIL" ]; then
    echo "Uso: $0 seu@email.com"
    exit 1
fi

cd "$APP_DIR"

echo "==> Usando nginx temporário (HTTP) para validação..."
cp infra/nginx/nginx.prod.conf infra/nginx/nginx.prod.conf.bak
cp infra/nginx/nginx.bootstrap.conf infra/nginx/nginx.prod.conf
docker exec fm_nginx nginx -s reload

echo "==> Obtendo certificado para $DOMAIN..."
docker compose -f infra/docker-compose.prod.yml run --rm certbot \
    certonly --webroot \
    -w /var/www/certbot \
    -d "$DOMAIN" \
    -d "www.$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive

echo "==> Restaurando nginx com HTTPS..."
cp infra/nginx/nginx.prod.conf.bak infra/nginx/nginx.prod.conf
rm infra/nginx/nginx.prod.conf.bak
docker exec fm_nginx nginx -s reload

echo ""
echo "✓ SSL ativo! Acesse: https://$DOMAIN"
echo ""
echo "Renovação automática já está configurada no container certbot."
