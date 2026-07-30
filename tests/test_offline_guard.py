"""The suite claims to be offline. Prove it."""

from __future__ import annotations

import socket

import pytest

from tests.conftest import OfflineTestViolation, _is_loopback


def test_outbound_socket_connect_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OfflineTestViolation):
            sock.connect(("openapi.twse.com.tw", 443))
    finally:
        sock.close()


def test_create_connection_is_blocked() -> None:
    with pytest.raises(OfflineTestViolation):
        socket.create_connection(("mops.twse.com.tw", 443), timeout=1)


@pytest.mark.parametrize(
    "address",
    [("127.0.0.1", 8080), ("localhost", 11434), ("::1", 443), "unix.sock", b"unix.sock"],
)
def test_loopback_targets_are_recognised(address: object) -> None:
    assert _is_loopback(address) is True


@pytest.mark.parametrize(
    "address",
    [("openapi.twse.com.tw", 443), ("8.8.8.8", 53), ("169.254.169.254", 80), 12345, None],
)
def test_remote_targets_are_not_loopback(address: object) -> None:
    assert _is_loopback(address) is False


def test_credential_env_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in ("HF_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_HOST"):
        assert name not in os.environ, f"{name} must be scrubbed before tests run"
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["CUDA_VISIBLE_DEVICES"] == ""
