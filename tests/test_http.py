"""The HTTP choke point is a security control, so it is tested like one.

Every test runs through the real ``PoliteClient`` code path with an
``httpx.MockTransport``, so no socket is opened and the offline guard in
``conftest.py`` stays satisfied.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from twfi.errors import (
    DataAccessError,
    DisallowedHostError,
    DownloadLimitExceededError,
    RateLimitViolationError,
)
from twfi.io.http import (
    ALLOWED_HOSTS,
    MAX_REDIRECTS,
    USER_AGENT,
    PoliteClient,
    PolitenessBudget,
    assert_url_allowed,
    is_forbidden_address,
)

OPENAPI = "https://openapi.twse.com.tw/v1/swagger.json"
MOPS = "https://mops.twse.com.tw/mops/web/index"
DOC = "https://doc.twse.com.tw/pdf/example.pdf"


class FakeClock:
    """A clock that only advances when someone sleeps, making timing exact."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    budget: PolitenessBudget | None = None,
    clock: FakeClock | None = None,
) -> PoliteClient:
    clock = clock or FakeClock()
    return PoliteClient(
        budget=budget,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


# --------------------------------------------------------------------- allowlist


@pytest.mark.parametrize("host", sorted(ALLOWED_HOSTS))
def test_allowlisted_hosts_are_accepted(host: str) -> None:
    assert assert_url_allowed(f"https://{host}/some/path?q=1") == host


def test_allowlist_is_exactly_the_three_official_sources() -> None:
    assert ALLOWED_HOSTS == {"mops.twse.com.tw", "doc.twse.com.tw", "openapi.twse.com.tw"}


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://mops.twse.com.tw/x", "only https"),
        ("ftp://mops.twse.com.tw/x", "only https"),
        ("https://evil.example.com/x", "not on the allowlist"),
        ("https://mops.twse.com.tw.evil.com/x", "not on the allowlist"),
        ("https://user:pw@mops.twse.com.tw/x", "credentials in URL"),
        ("https://127.0.0.1/x", "IP literal"),
        ("https://localhost/x", "IP literal or non-public"),
        ("https://169.254.169.254/latest/meta-data", "IP literal"),
        ("https://10.0.0.5/x", "IP literal"),
        ("https://[::1]/x", "IP literal"),
        ("https://mops.twse.com.tw:8443/x", "only port 443"),
        ("https:///x", "cannot determine host"),
    ],
)
def test_disallowed_urls_are_rejected(url: str, reason: str) -> None:
    with pytest.raises(DisallowedHostError, match=reason):
        assert_url_allowed(url)


def test_explicit_port_443_is_fine() -> None:
    assert assert_url_allowed("https://mops.twse.com.tw:443/x") == "mops.twse.com.tw"


def test_host_matching_is_case_insensitive() -> None:
    assert assert_url_allowed("https://MOPS.TWSE.COM.TW/x") == "mops.twse.com.tw"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "0.0.0.0",  # noqa: S104 - this is the value under test, not a bind address
        "::1",
        "[::1]",
        "fe80::1",
        "localhost",
        "",
    ],
)
def test_forbidden_addresses(host: str) -> None:
    assert is_forbidden_address(host) is True


@pytest.mark.parametrize("host", ["mops.twse.com.tw", "example.com", "8.8.8.8", "1.1.1.1"])
def test_public_addresses_are_not_forbidden(host: str) -> None:
    assert is_forbidden_address(host) is False


# ------------------------------------------------------------------- basic fetch


def test_get_bytes_returns_payload_and_provenance() -> None:
    payload = b'{"ok": true}'
    client = make_client(lambda _r: httpx.Response(200, content=payload))
    with client:
        body, result = client.get_bytes(OPENAPI)

    assert body == payload
    assert result.status_code == 200
    assert result.num_bytes == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.requested_url == OPENAPI
    assert result.retrieved_at.startswith("20")
    assert result.redirects == ()


def test_requests_carry_the_declared_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, content=b"{}")

    with make_client(handler) as client:
        client.get_bytes(OPENAPI)

    assert seen == [USER_AGENT]


def test_get_json_decodes() -> None:
    body = {"paths": {"/v1/opendata/x": {}}}
    with make_client(lambda _r: httpx.Response(200, content=json.dumps(body).encode())) as client:
        decoded, _result = client.get_json(OPENAPI)
    assert decoded == body


def test_get_json_rejects_non_json() -> None:
    with make_client(lambda _r: httpx.Response(200, content=b"<html>nope</html>")) as client:
        with pytest.raises(DataAccessError, match="did not return valid JSON"):
            client.get_json(OPENAPI)


def test_fetching_a_disallowed_host_never_reaches_the_transport() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the transport must not be reached")

    with make_client(handler) as client:
        with pytest.raises(DisallowedHostError):
            client.get_bytes("https://evil.example.com/x")
    assert client.stats.total_requests == 0


# ------------------------------------------------------------------------ retries


def test_client_errors_are_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, content=b"")

    with make_client(handler) as client:
        with pytest.raises(DataAccessError, match="returned 404"):
            client.get_bytes(MOPS)

    assert calls == 1, "4xx means we asked for the wrong thing; retrying only adds load"
    assert client.stats.retries == 0


def test_server_errors_are_retried_with_exponential_backoff() -> None:
    calls = 0
    clock = FakeClock()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, content=b"")

    with make_client(handler, clock=clock) as client:
        with pytest.raises(DataAccessError, match="failed after 3 attempts"):
            client.get_bytes(MOPS)

    assert calls == 3
    assert client.stats.retries == 2
    assert clock.sleeps == [2.0, 4.0]


def test_transport_errors_are_retried_then_succeed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, content=b"{}")

    with make_client(handler) as client:
        _body, result = client.get_bytes(OPENAPI)

    assert calls == 2
    assert result.status_code == 200
    assert client.stats.retries == 1


# ---------------------------------------------------------------------- redirects


def test_redirects_are_followed_and_recorded() -> None:
    target = "https://doc.twse.com.tw/final.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pdf/example.pdf":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, content=b"%PDF-1.7")

    with make_client(handler) as client:
        body, result = client.get_bytes(DOC)

    assert body == b"%PDF-1.7"
    assert result.redirects == (target,)


def test_redirect_off_the_allowlist_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "evil" in str(request.url):  # pragma: no cover - must never be requested
            raise AssertionError("followed a redirect off the allowlist")
        return httpx.Response(302, headers={"Location": "https://evil.example.com/x"})

    with make_client(handler) as client:
        with pytest.raises(DisallowedHostError, match="not on the allowlist"):
            client.get_bytes(DOC)


def test_redirect_to_a_private_address_is_refused() -> None:
    with make_client(
        lambda _r: httpx.Response(302, headers={"Location": "https://169.254.169.254/latest"})
    ) as client:
        with pytest.raises(DisallowedHostError):
            client.get_bytes(DOC)


def test_redirect_without_location_is_an_error() -> None:
    with make_client(lambda _r: httpx.Response(302)) as client:
        with pytest.raises(DataAccessError, match="without Location"):
            client.get_bytes(DOC)


def test_redirect_loops_terminate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        nxt = f"https://doc.twse.com.tw/hop{len(str(request.url))}"
        return httpx.Response(302, headers={"Location": nxt})

    with make_client(handler) as client:
        with pytest.raises(DataAccessError, match=f"exceeded {MAX_REDIRECTS} redirects"):
            client.get_bytes(DOC)


# --------------------------------------------------------------------- politeness


def test_consecutive_requests_to_one_host_are_spaced() -> None:
    clock = FakeClock()
    with make_client(lambda _r: httpx.Response(200, content=b"{}"), clock=clock) as client:
        client.get_bytes(OPENAPI)
        client.get_bytes(OPENAPI)

    assert clock.sleeps == [1.5], "the first request is immediate, the second waits"


def test_the_first_request_to_a_host_is_not_delayed() -> None:
    clock = FakeClock()
    with make_client(lambda _r: httpx.Response(200, content=b"{}"), clock=clock) as client:
        client.get_bytes(OPENAPI)
    assert clock.sleeps == []


def test_per_host_request_cap_is_enforced() -> None:
    budget = PolitenessBudget(min_interval_s=0.0, max_requests_per_host=2)
    with make_client(lambda _r: httpx.Response(200, content=b"{}"), budget=budget) as client:
        client.get_bytes(OPENAPI)
        client.get_bytes(OPENAPI)
        with pytest.raises(RateLimitViolationError, match="cap is 2"):
            client.get_bytes(OPENAPI)

    assert client.stats.requests_per_host == {"openapi.twse.com.tw": 2}


def test_backoff_delays_are_two_four_eight() -> None:
    budget = PolitenessBudget()
    assert [budget.backoff_delay(n) for n in (1, 2, 3)] == [2.0, 4.0, 8.0]


def test_timeouts_match_the_documented_budget() -> None:
    timeout = PolitenessBudget().timeout()
    assert timeout.connect == 10.0
    assert timeout.read == 60.0


# ---------------------------------------------------------------------- downloads


def test_download_writes_the_file_and_hashes_it(tmp_path: Path) -> None:
    payload = b"%PDF-1.7\n" + b"x" * 4096
    destination = tmp_path / "nested" / "report.pdf"

    with make_client(lambda _r: httpx.Response(200, content=payload)) as client:
        result = client.download(DOC, destination)

    assert destination.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.num_bytes == len(payload)
    assert result.path == destination
    assert not list(tmp_path.rglob("*.partial"))


def test_download_aborts_above_the_per_file_limit(tmp_path: Path) -> None:
    budget = PolitenessBudget(min_interval_s=0.0, max_bytes_per_file=128)
    destination = tmp_path / "big.pdf"

    with make_client(lambda _r: httpx.Response(200, content=b"y" * 4096), budget=budget) as client:
        with pytest.raises(DownloadLimitExceededError, match="per-file limit"):
            client.download(DOC, destination)

    assert not destination.exists()
    assert not list(tmp_path.rglob("*.partial")), "a partial file must never survive"


def test_download_aborts_above_the_per_run_limit(tmp_path: Path) -> None:
    budget = PolitenessBudget(min_interval_s=0.0, max_bytes_per_run=100)

    with make_client(lambda _r: httpx.Response(200, content=b"z" * 60), budget=budget) as client:
        client.download(DOC, tmp_path / "one.pdf")
        with pytest.raises(DownloadLimitExceededError, match="per-run limit"):
            client.download(DOC, tmp_path / "two.pdf")

    assert (tmp_path / "one.pdf").exists()
    assert not (tmp_path / "two.pdf").exists()


def test_download_rejects_a_content_length_mismatch(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"short", headers={"Content-Length": "9999"})

    destination = tmp_path / "truncated.pdf"
    with make_client(handler) as client:
        with pytest.raises(DataAccessError, match="declared Content-Length"):
            client.download(DOC, destination)

    assert not destination.exists()
    assert not list(tmp_path.rglob("*.partial"))


def test_download_accounts_bytes_across_calls(tmp_path: Path) -> None:
    budget = PolitenessBudget(min_interval_s=0.0)
    with make_client(lambda _r: httpx.Response(200, content=b"a" * 10), budget=budget) as client:
        client.download(DOC, tmp_path / "a.pdf")
        client.download(DOC, tmp_path / "b.pdf")
        assert client.stats.total_bytes == 20


# ---------------------------------------------------------------------- reporting


def test_snapshot_records_what_the_run_actually_did() -> None:
    budget = PolitenessBudget(min_interval_s=0.0)
    with make_client(lambda _r: httpx.Response(200, content=b"{}"), budget=budget) as client:
        client.get_bytes(OPENAPI)
        snapshot = client.snapshot()

    assert snapshot["user_agent"] == USER_AGENT
    assert snapshot["allowed_hosts"] == sorted(ALLOWED_HOSTS)
    assert snapshot["total_requests"] == 1
    assert snapshot["total_bytes"] == 2
    assert snapshot["retries"] == 0


def test_client_is_usable_as_a_context_manager() -> None:
    client = make_client(lambda _r: httpx.Response(200, content=b"{}"))
    with client as entered:
        assert entered is client
    # Closing twice must not raise.
    client.close()


def test_client_exposes_the_budget_it_is_enforcing() -> None:
    """Callers and provenance records read the budget rather than re-declaring it."""
    with make_client(lambda _r: httpx.Response(200, content=b"{}")) as client:
        assert client.budget.min_interval_s == 1.5
        assert client.budget.max_bytes_per_file == 80 * 1024 * 1024
        assert client.budget.max_requests_per_host == 40
