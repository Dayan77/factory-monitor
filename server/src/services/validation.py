"""
Input validation helpers for values interpolated into queries we cannot
parameterize (Flux). Every identifier that reaches a Flux string MUST pass
through one of these functions first.
"""

import re
from uuid import UUID

_FLUX_DURATION_RE = re.compile(r"^[1-9][0-9]{0,5}(ms|s|m|h|d|w)$")


class InvalidIdentifier(ValueError):
    """Raised when a value is not safe to interpolate into a query."""


def ensure_uuid(value: str, field: str = "id") -> str:
    """Returns the canonical (lower-case, hyphenated) form of a UUID string.

    Raises InvalidIdentifier for anything that is not a UUID — this is what
    keeps tenant_id/tag_id from injecting Flux.
    """
    if not isinstance(value, str):
        raise InvalidIdentifier(f"{field} deve ser um UUID.")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise InvalidIdentifier(f"{field} deve ser um UUID.") from None


def is_uuid(value: str) -> bool:
    try:
        ensure_uuid(value)
        return True
    except InvalidIdentifier:
        return False


def ensure_flux_duration(value: str, field: str = "window") -> str:
    """Validates a Flux duration literal such as '30s', '1m', '1h'."""
    if not isinstance(value, str) or not _FLUX_DURATION_RE.match(value):
        raise InvalidIdentifier(f"{field} deve ser uma duração como 30s, 1m, 1h.")
    return value
