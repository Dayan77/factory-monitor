"""Shared fixtures for edge tests. Servers (Modbus/OPC UA) run in-process on free ports."""

import socket
import sys
from pathlib import Path

import pytest

# Make `import edge.src...` work no matter where pytest is launched from.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def port() -> int:
    return free_port()
