#!/usr/bin/env bash
# =============================================================
# Factory Monitor — Push & Deploy
# Faz git push e deploya no VPS via SSH.
# Uso: ./scripts/push_deploy.sh
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${SCRIPT_DIR}/../.venv/bin/python3"

# Usa python do .venv se disponível, senão usa o do sistema
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Factory Monitor — Push & Deploy          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Git push ──────────────────────────────────────────────
echo "▶ Push para GitHub..."
git push origin HEAD
echo "  ✓ GitHub atualizado"

# ── 2. Deploy remoto via SSH ─────────────────────────────────
echo ""
"$PYTHON" "$SCRIPT_DIR/deploy_remote.py"
