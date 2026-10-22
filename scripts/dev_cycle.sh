#!/usr/bin/env bash
# =============================================================
# Factory Monitor — Dev Cycle
# Para tudo, faz deploy no VPS e reinicia localmente para teste.
#
# Credenciais do VPS vêm do ambiente (nunca do repositório):
#   export VPS_HOST=<ip-ou-host> VPS_USER=deploy VPS_SSH_KEY=~/.ssh/factoryconnect_deploy
# (veja scripts/vps.py). Preferível: deploy pelo GitHub Actions (.github/workflows/deploy.yml).
#
# Uso:
#   ./scripts/dev_cycle.sh             # deploy + reinicia tudo
#   ./scripts/dev_cycle.sh "msg"       # com mensagem de commit
#   ./scripts/dev_cycle.sh stop        # apenas para os processos locais
# =============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv/bin/python3"

cd "$ROOT"

# ── Stop ─────────────────────────────────────────────────────
stop_local() {
  echo "▶ Parando processos locais..."
  for PORT in 7001 7000; do
    PIDS=$(lsof -ti :$PORT 2>/dev/null || true)
    [ -n "$PIDS" ] && echo "$PIDS" | xargs kill -9 2>/dev/null && echo "  ✓ Porta $PORT liberada" || true
  done
  pkill -f "edge.src.simulator" 2>/dev/null && echo "  ✓ Simulador encerrado (estava rodando)" || true
  echo ""
}

if [ "${1:-}" = "stop" ]; then
  stop_local
  echo "  Processos locais encerrados."
  exit 0
fi

MSG="${1:-deploy: atualização}"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Factory Monitor — Dev Cycle              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Parar locais ───────────────────────────────────────────
stop_local

# ── 2. Git push ───────────────────────────────────────────────
echo "▶ Commit e push para GitHub..."
git add -A
git commit -m "$MSG" 2>/dev/null || echo "  (nada novo para commitar)"
git push origin HEAD
echo "  ✓ GitHub atualizado"
echo ""

# ── 3. Deploy VPS ─────────────────────────────────────────────
echo "▶ Deploy no VPS..."
if [ -z "${VPS_HOST:-}" ]; then
  echo "  ✗ VPS_HOST não definido — exporte VPS_HOST/VPS_USER/VPS_SSH_KEY (veja scripts/vps.py)." >&2
  exit 2
fi
"$VENV" "$ROOT/scripts/deploy_remote.py"
echo ""

# ── 4. Edge App local ─────────────────────────────────────────
echo "▶ Iniciando Edge Config App → http://localhost:7001"
"$VENV" -m edge.app > /tmp/edge_app.log 2>&1 &
sleep 3
lsof -ti :7001 > /dev/null 2>&1 && echo "  ✓ Edge App OK (PID $(lsof -ti :7001))" || echo "  ✗ Edge App falhou — veja /tmp/edge_app.log"
echo ""

# ── Resumo ────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════╗"
echo "║  Tudo pronto!                             ║"
echo "║                                           ║"
echo "║  Local:   http://localhost:7001           ║"
echo "║  Online:  https://factoryconnect.tech     ║"
echo "║                                           ║"
echo "║  Para simular dados:                      ║"
echo "║    → Use o botão no app local (7001)      ║"
echo "║    → Ou: python3 -m edge.src.simulator    ║"
echo "║                                           ║"
echo "║  Logs:                                    ║"
echo "║    tail -f /tmp/edge_app.log              ║"
echo "║                                           ║"
echo "║  Para parar:                              ║"
echo "║    ./scripts/dev_cycle.sh stop            ║"
echo "╚══════════════════════════════════════════╝"
echo ""
