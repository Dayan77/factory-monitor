#!/bin/bash
# =============================================================
# Factory Monitor — VPS Setup Script (Ubuntu 22.04/24.04)
# Execute como root ou usuário com sudo
# =============================================================
set -euo pipefail

APP_DIR="/opt/factory-monitor"
APP_USER="fmonitor"

echo "==> Atualizando sistema..."
apt-get update -qq && apt-get upgrade -y -qq

echo "==> Instalando dependências..."
apt-get install -y -qq \
    curl git ufw fail2ban \
    ca-certificates gnupg lsb-release

echo "==> Instalando Docker..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable --now docker

echo "==> Criando usuário da aplicação..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -s /bin/bash "$APP_USER"
fi
usermod -aG docker "$APP_USER"

echo "==> Configurando diretório da aplicação..."
mkdir -p "$APP_DIR"
chown "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> Configurando firewall (UFW)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 1883/tcp   # MQTT (remova se não precisar de acesso externo ao broker)
ufw --force enable

echo "==> Configurando fail2ban..."
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
maxretry = 5
bantime = 3600
findtime = 600

[sshd]
enabled = true
EOF
systemctl enable --now fail2ban

echo ""
echo "✓ Setup concluído!"
echo ""
echo "Próximos passos:"
echo "  1. Clone o repositório em $APP_DIR"
echo "  2. Copie e preencha o .env: cp .env.example .env && nano .env"
echo "  3. Execute: cd $APP_DIR/infra && docker compose -f docker-compose.prod.yml up -d"
echo "  4. Rode o seed: docker exec fm_server python ../scripts/seed_dev.py"
