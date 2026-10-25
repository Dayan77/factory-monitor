"""IA-10 — what leaves for the model provider never carries e-mail, phone or token; IA-9 — free text typed by users
is data. Names (e.g. the assigned technician) stay: they are needed for "OS do João"."""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = re.compile(r"(e-?mail|phone|telefone|celular|whatsapp|token|password|senha|secret|api_key|"
                            r"authorization|cookie|endpoint_secret|vapid|mqtt_password)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# +55 (92) 99999-9999 / (92) 3333-4444 / 92999998888 — 10+ digits with phone punctuation
PHONE = re.compile(r"(?<![\w/:-])(\+\d{1,3}[\s.-]?)?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]\d{4}(?![\d/:-])|"
                   r"(?<![\w/:-])\(\d{2}\)\s?\d{8,9}(?!\d)")
TOKENISH = re.compile(r"\b(fct_|fcg_|sk-|sk-ant-|eyJ)[A-Za-z0-9_\-.]{8,}")


def redact_text(s: str) -> str:
    s = EMAIL.sub("[e-mail removido]", s)
    s = TOKENISH.sub("[token removido]", s)
    s = PHONE.sub("[telefone removido]", s)
    return s


def redact(value: Any) -> Any:
    """Recursively drops sensitive keys and scrubs e-mails / phones / tokens out of strings."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if not SENSITIVE_KEYS.search(str(k))}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
