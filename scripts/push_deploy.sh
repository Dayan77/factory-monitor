#!/usr/bin/env bash
# =============================================================
# Factory Monitor — Push & Deploy
# Faz git push e deploya no VPS automaticamente.
# Uso: ./scripts/push_deploy.sh [mensagem do commit]
# =============================================================
set -euo pipefail

VPS="187.77.144.79"
VPS_USER="root"
APP_DIR="/opt/factory-monitor"
COMPOSE="docker compose -f infra/docker-compose.prod.yml"
MSG="${1:-deploy: atualização automática}"

# ── 1. Git push ──────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Factory Monitor — Push & Deploy          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

echo "▶ Commit e push para GitHub..."
git add -A
git commit -m "$MSG" 2>/dev/null || echo "  (nada novo para commitar)"
git push origin HEAD
echo "  ✓ GitHub atualizado"

# ── 2. SSH password ──────────────────────────────────────────
echo ""
read -rsp "▶ Senha SSH do VPS ($VPS_USER@$VPS): " SSH_PASS
echo ""

_ssh() { sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$VPS_USER@$VPS" "$@"; }
_scp() { sshpass -p "$SSH_PASS" scp -o StrictHostKeyChecking=no "$@"; }

# ── 3. Testar conexão ────────────────────────────────────────
echo "▶ Conectando ao VPS..."
_ssh "echo '  ✓ SSH OK'" || { echo "  ✗ Falha na conexão SSH"; exit 1; }

# ── 4. Git pull no VPS ──────────────────────────────────────
echo "▶ Atualizando código no VPS..."
_ssh "
  cd $APP_DIR
  git fetch origin
  git reset --hard origin/\$(git rev-parse --abbrev-ref HEAD)
  echo '  ✓ Código atualizado'
"

# ── 5. Rebuild server ────────────────────────────────────────
echo "▶ Rebuilding servidor..."
_ssh "
  cd $APP_DIR
  $COMPOSE build --no-cache server
  echo '  ✓ Build concluído'
"

# ── 6. Restart server ────────────────────────────────────────
echo "▶ Reiniciando servidor..."
_ssh "
  cd $APP_DIR
  $COMPOSE up -d --no-deps server
  sleep 3
  $COMPOSE ps server
"

# ── 7. Fix InfluxDB se necessário ────────────────────────────
echo ""
echo "▶ Verificando InfluxDB..."
_ssh "
  # Pegar token atual do .env
  TOKEN=\$(grep INFLUX_TOKEN $APP_DIR/.env | cut -d= -f2 | tr -d '\"' | tr -d \"'\")
  ORG=\$(grep INFLUX_ORG $APP_DIR/.env | cut -d= -f2 | tr -d '\"' | tr -d \"'\")
  BUCKET=\$(grep INFLUX_BUCKET $APP_DIR/.env | cut -d= -f2 | tr -d '\"' | tr -d \"'\")

  echo \"  Token: \${TOKEN:0:16}...\"
  echo \"  Org:   \$ORG\"
  echo \"  Bucket: \$BUCKET\"

  # Testar se o token funciona
  STATUS=\$(curl -s -o /dev/null -w '%{http_code}' \
    -H \"Authorization: Token \$TOKEN\" \
    http://localhost:8086/api/v2/buckets)

  if [ \"\$STATUS\" = '200' ]; then
    echo '  ✓ InfluxDB OK'
  else
    echo \"  ✗ InfluxDB retornou \$STATUS — regenerando token...\"
    # Gerar novo token de admin
    NEW_TOKEN=\$(docker exec fm_influxdb influx auth create \
      --org \"\$ORG\" --all-access \
      --json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"token\"])' 2>/dev/null || echo '')

    if [ -n \"\$NEW_TOKEN\" ]; then
      # Atualizar .env
      sed -i \"s|INFLUX_TOKEN=.*|INFLUX_TOKEN=\$NEW_TOKEN|\" $APP_DIR/.env
      echo \"  ✓ Novo token gerado e salvo no .env\"
      # Reiniciar server para pegar novo token
      $COMPOSE up -d --no-deps server
      echo '  ✓ Servidor reiniciado com novo token'
    else
      echo '  ⚠ Não foi possível gerar novo token automaticamente'
      echo '  Execute manualmente: docker exec fm_influxdb influx auth list'
    fi
  fi
"

# ── 8. Health check ──────────────────────────────────────────
echo ""
echo "▶ Health check..."
sleep 3
HTTP=$(curl -sk https://$VPS/api/health 2>/dev/null || echo '{}')
echo "  $HTTP"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✓ Deploy concluído!                      ║"
echo "║  Acesse: https://factoryconnect.tech      ║"
echo "╚══════════════════════════════════════════╝"
echo ""
