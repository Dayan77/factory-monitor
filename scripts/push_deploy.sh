#!/usr/bin/env bash
# =============================================================
# Factory Monitor — Push & Deploy
# Faz git push e deploya no VPS automaticamente.
# Uso: ./scripts/push_deploy.sh ["mensagem do commit"]
# =============================================================
set -euo pipefail

MSG="${1:-deploy: atualização automática}"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Factory Monitor — Push & Deploy          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Git push ──────────────────────────────────────────────
echo "▶ Commit e push para GitHub..."
git add -A
git commit -m "$MSG" 2>/dev/null || echo "  (nada novo para commitar)"
git push origin HEAD
echo "  ✓ GitHub atualizado"

# ── 2. Deploy via Python/paramiko ────────────────────────────
echo ""
echo "▶ Iniciando deploy no VPS..."
"$(dirname "$0")/../.venv/bin/python3" "$(dirname "$0")/deploy_remote.py"
