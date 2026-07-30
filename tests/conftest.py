"""Test-suite guarantees enforced for every test.

The protocol requires the suite to be offline, CPU-only, ``.env``-free, and
incapable of touching the real feasibility artifacts. Rather than trusting that,
these autouse fixtures make violations *fail loudly*.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from twfi.paths import find_repo_root

#: Environment variables that could smuggle credentials or remote endpoints into
#: a test run. Removed before every test.
_SCRUBBED_ENV = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACE_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_HOST",
    "TWFI_ALLOW_NETWORK",
    "TWFI_MODELS_DIR",
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
)


class OfflineTestViolation(RuntimeError):
    """Raised when a test attempts a non-loopback network connection."""


def _is_loopback(address: object) -> bool:
    """Best-effort check for loopback / unix-socket targets."""
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        return isinstance(host, str) and (host in {"localhost", "::1"} or host.startswith("127."))
    return False


@pytest.fixture(autouse=True)
def _offline_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block outbound sockets unless the test is explicitly marked ``network``."""
    if request.node.get_closest_marker("network") is not None:
        return

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> None:
        if _is_loopback(address):
            real_connect(self, address)
            return
        raise OfflineTestViolation(
            f"tests must not reach the network (attempted connect to {address!r})"
        )

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        raise OfflineTestViolation(
            f"tests must not reach the network (attempted connect to {address!r})"
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove credential/endpoint env vars and force offline model libraries."""
    for name in _SCRUBBED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The checked-out repository root."""
    return find_repo_root()


@pytest.fixture()
def sandbox_repo(tmp_path: Path) -> Iterator[Path]:
    """A throwaway directory shaped like the repo, for path-sensitive tests."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    yield tmp_path
