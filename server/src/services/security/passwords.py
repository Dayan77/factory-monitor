"""Password policy (docs/security.md §1.2).

* length >= PASSWORD_MIN_LENGTH (12) and <= 72 bytes UTF-8 (bcrypt ignores what comes after 72 bytes);
* not a common/breached password: offline list (common_passwords.txt + PASSWORD_BLOCKLIST_FILE), compared after
  lower-casing, undoing leetspeak and stripping digits/symbols around a word ("Senha@2026!" → "senha");
* not built from the account's own e-mail / name / company, not one character repeated or a plain sequence;
* not one of the last PASSWORD_HISTORY (5) passwords of the user (current one included).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.security import hash_password, verify_password
from ...config import settings

MAX_BYTES = 72
_LEET = str.maketrans({"@": "a", "4": "a", "3": "e", "1": "i", "!": "i", "0": "o", "$": "s", "5": "s", "7": "t",
                       "+": "t", "8": "b", "9": "g"})
_SEQS = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm")


class PasswordPolicyError(ValueError):
    pass


@lru_cache(maxsize=1)
def blocklist() -> frozenset[str]:
    words: set[str] = set()
    files = [Path(__file__).with_name("common_passwords.txt")]
    if settings.password_blocklist_file:
        files.append(Path(settings.password_blocklist_file))
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                w = line.strip().lower()
                if w and not w.startswith("#"):
                    words.add(w)
        except OSError:
            continue
    return frozenset(words)


def _variants(pw: str) -> set[str]:
    """Normalized forms compared with the blocklist: lower-case, digits/symbols around the word stripped, leetspeak
    undone, letters only — combined (two rounds), so "P4ssw0rd!2026" → "password"."""
    ops = (lambda v: re.sub(r"^[^a-z]+|[^a-z]+$", "", v), lambda v: v.translate(_LEET),
           lambda v: re.sub(r"[^a-z]", "", v), lambda v: re.sub(r"[^a-z0-9]", "", v))
    out = {pw.lower()}
    for _ in range(2):
        out |= {op(v) for v in out for op in ops}
    return {v for v in out if v}


def _trivial(pw: str) -> bool:
    low = pw.lower()
    if len(set(low)) <= 2 or low.isdigit():
        return True
    for seq in _SEQS:
        if low in seq or low in seq[::-1]:
            return True
    return False


def check_policy(password: str, context: Iterable[Optional[str]] = ()) -> None:
    """Raises PasswordPolicyError (message in Portuguese, shown to the user) when the password is not acceptable."""
    if not isinstance(password, str):
        raise PasswordPolicyError("Senha inválida.")
    if len(password) < settings.password_min_length:
        raise PasswordPolicyError(f"A senha deve ter no mínimo {settings.password_min_length} caracteres.")
    if len(password.encode("utf-8")) > MAX_BYTES:
        raise PasswordPolicyError(f"A senha deve ter no máximo {MAX_BYTES} bytes.")
    if _trivial(password):
        raise PasswordPolicyError("Senha muito previsível (caracteres repetidos ou sequência).")
    bl = blocklist()
    if any(v in bl for v in _variants(password)):
        raise PasswordPolicyError("Senha muito comum ou presente em vazamentos conhecidos. Escolha outra.")
    low = password.lower()
    for item in context:
        if not item:
            continue
        parts = re.split(r"[@.\s_+-]+", str(item).lower())
        for p in parts:
            if len(p) >= 4 and p in low:
                raise PasswordPolicyError("A senha não pode conter seu nome, e-mail ou o nome da empresa.")


async def check_history(db: AsyncSession, user, password: str) -> None:
    """The new password must differ from the current one and the last PASSWORD_HISTORY - 1 previous ones."""
    from ...models import PasswordHistory
    if user.password_hash and verify_password(password, user.password_hash):
        raise PasswordPolicyError(f"A nova senha não pode repetir nenhuma das últimas {settings.password_history}.")
    if user.id is None:
        return
    rows = (await db.execute(select(PasswordHistory.password_hash).where(PasswordHistory.user_id == user.id)
                             .order_by(PasswordHistory.created_at.desc())
                             .limit(max(0, settings.password_history - 1)))).scalars().all()
    for h in rows:
        if verify_password(password, h):
            raise PasswordPolicyError(f"A nova senha não pode repetir nenhuma das últimas {settings.password_history}.")


async def set_password(db: AsyncSession, user, password: str, *, check: bool = True,
                       context: Iterable[Optional[str]] = ()) -> None:
    """Validates (policy + history) and sets the password; the old hash goes to the history (keeps the last N)."""
    from datetime import datetime, timezone

    from ...models import PasswordHistory
    if check:
        check_policy(password, [user.email, user.name, *context])
        await check_history(db, user, password)
    if user.id is not None and user.password_hash and user.password_hash.startswith("$2"):
        db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash,
                               created_at=datetime.now(timezone.utc)))
        await db.flush()
        keep = (await db.execute(select(PasswordHistory.id).where(PasswordHistory.user_id == user.id)
                                 .order_by(PasswordHistory.created_at.desc())
                                 .limit(max(1, settings.password_history - 1)))).scalars().all()
        await db.execute(delete(PasswordHistory).where(PasswordHistory.user_id == user.id,
                                                       PasswordHistory.id.not_in(keep)))
    user.password_hash = hash_password(password)
    user.password_changed_at = datetime.now(timezone.utc)
