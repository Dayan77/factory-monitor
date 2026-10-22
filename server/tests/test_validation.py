import uuid

import pytest

from src.services.validation import (
    InvalidIdentifier, ensure_flux_duration, ensure_uuid, is_uuid,
)


def test_ensure_uuid_canonicalizes():
    u = uuid.uuid4()
    assert ensure_uuid(str(u).upper()) == str(u)
    assert is_uuid(str(u))


@pytest.mark.parametrize("bad", ["", "x", '"; drop', None, 123, "1234", str(uuid.uuid4()) + '"'])
def test_ensure_uuid_rejects(bad):
    with pytest.raises(InvalidIdentifier):
        ensure_uuid(bad)
    assert not is_uuid(bad)


@pytest.mark.parametrize("ok", ["1s", "30s", "1m", "15m", "1h", "1d", "1w", "500ms"])
def test_flux_duration_ok(ok):
    assert ensure_flux_duration(ok) == ok


@pytest.mark.parametrize("bad", ["", "0m", "1", "m", "1m ", "1m)", "-1m", "1mo", "1M"])
def test_flux_duration_bad(bad):
    with pytest.raises(InvalidIdentifier):
        ensure_flux_duration(bad)
