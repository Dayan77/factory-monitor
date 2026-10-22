"""Relógio do módulo de manutenção. Tudo que depende de "agora"/"hoje" (estado dos planos, OS
vencidas, backlog, notificações) chama `now()`, de modo que os testes de aceite do cenário E
avaliam no instante do cenário (monkeypatch de `clock.now`)."""
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(t):
    """ISO 8601 UTC with 'Z' (S3-5), like the Pydantic responses; None stays None."""
    if t is None:
        return None
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
