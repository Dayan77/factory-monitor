"""EdgeSettings must tolerate the shared root .env (server-only keys) and not depend on the CWD."""
from edge.src.config import _ROOT_ENV, EdgeSettings


def test_server_only_keys_in_env_file_are_ignored(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "CORS_ORIGINS=http://localhost:5173\nPOSTGRES_PASSWORD=x\nDEADBAND_PCT=1.5\n",
        encoding="utf-8",
    )
    s = EdgeSettings(_env_file=env)
    assert s.deadband_pct == 1.5


def test_root_env_path_is_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _ROOT_ENV.is_absolute()
    assert (_ROOT_ENV.parent / "edge" / "src" / "config.py").is_file()
    EdgeSettings()  # must not raise, whatever the CWD


def test_env_example_encryption_key_is_not_an_invalid_placeholder():
    """A gateway inheriting .env.example must not refuse to start on a bogus key."""
    from edge.src.security.payload_crypto import load_key

    example = _ROOT_ENV.parent / ".env.example"
    values = dict(
        line.split("=", 1) for line in example.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    key = values.get("ENCRYPTION_KEY", "").strip()
    if key:
        load_key(key)  # raises if malformed
