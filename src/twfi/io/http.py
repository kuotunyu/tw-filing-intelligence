"""The single outbound HTTP path for this project.

Everything the harness fetches goes through :class:`PoliteClient`. That is a
deliberate choke point: the host allowlist, the politeness budget, and the
download caps are properties of *this module*, not of the call sites, so a new
script cannot accidentally opt out of them.

Enforced here (see ``docs/THREAT_MODEL.md`` T3-T5 and ``docs/DATA_PROVENANCE.md``):

* **T3 SSRF** — https only, hard-coded host allowlist, no IP literals, no
  credentials in the URL, no non-443 ports, and every redirect hop is
  re-validated against the same allowlist.
* **T4 resource exhaustion** — per-file and per-run byte ceilings enforced while
  streaming, so an oversized response is aborted mid-download rather than after.
* **T5 politeness** — a minimum interval per host, serial requests, bounded
  retries with exponential backoff on 5xx and transport errors only, a per-host
  request cap, and an explicit User-Agent.

A URL never comes from document content or model output; callers pass URLs that
originate in a committed manifest.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from twfi.errors import (
    DataAccessError,
    DisallowedHostError,
    DownloadLimitExceededError,
    RateLimitViolationError,
)
from twfi.io.hashing import sha256_bytes

__all__ = [
    "ALLOWED_HOSTS",
    "USER_AGENT",
    "PolitenessBudget",
    "RequestStats",
    "FetchResult",
    "PoliteClient",
    "assert_url_allowed",
    "is_forbidden_address",
]

#: The only hosts this project may contact. Adding one requires updating
#: ``docs/DATA_PROVENANCE.md`` and ``docs/THREAT_MODEL.md`` in the same change.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "mops.twse.com.tw",  # S1/S2 Market Observation Post System
        "doc.twse.com.tw",  # S1 document server for filings
        "openapi.twse.com.tw",  # S3 TWSE OpenAPI
    }
)

USER_AGENT = "tw-filing-intelligence/0.1 (feasibility study; contact via repo)"

#: Redirects are followed manually so each hop can be re-validated.
MAX_REDIRECTS = 5

_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class PolitenessBudget:
    """Limits applied to every request. Defaults mirror ``docs/DATA_PROVENANCE.md``."""

    min_interval_s: float = 1.5
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 60.0
    max_retries: int = 3
    backoff_base_s: float = 2.0
    max_bytes_per_file: int = 80 * 1024 * 1024
    max_bytes_per_run: int = 600 * 1024 * 1024
    max_requests_per_host: int = 40

    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout_s,
            read=self.read_timeout_s,
            write=self.connect_timeout_s,
            pool=self.connect_timeout_s,
        )

    def backoff_delay(self, attempt: int) -> float:
        """Delay before retry ``attempt`` (1-based): 2s, 4s, 8s."""
        return self.backoff_base_s * 2.0 ** (attempt - 1)


@dataclass(slots=True)
class RequestStats:
    """Mutable per-run accounting, reported alongside provenance."""

    requests_per_host: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0
    retries: int = 0

    @property
    def total_requests(self) -> int:
        return sum(self.requests_per_host.values())


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Everything provenance needs about one successful fetch."""

    requested_url: str
    resolved_url: str
    status_code: int
    num_bytes: int
    sha256: str
    elapsed_s: float
    retrieved_at: str
    redirects: tuple[str, ...] = ()
    path: Path | None = None


def is_forbidden_address(host: str) -> bool:
    """True if ``host`` is an IP literal or an obviously non-public name.

    IP literals are rejected outright: every legitimate target is a named TWSE
    host, so a literal can only be an attempt to reach somewhere else -- and the
    loopback / private / link-local ranges (including the cloud metadata address
    ``169.254.169.254``) are the ones that matter.
    """
    lowered = host.strip("[]").lower()
    if lowered in {"localhost", "localhost.localdomain", ""}:
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or not address.is_global
    )


def assert_url_allowed(url: str) -> str:
    """Validate a URL against the allowlist and return its normalised host.

    Raises:
        DisallowedHostError: If the scheme, credentials, port, or host are not
            exactly what this project is permitted to contact.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DisallowedHostError(f"only https is permitted, got {parts.scheme!r} in {url!r}")
    if parts.username or parts.password:
        raise DisallowedHostError(f"credentials in URL are not permitted: {url!r}")

    host = (parts.hostname or "").lower()
    if not host:
        raise DisallowedHostError(f"cannot determine host from {url!r}")
    if is_forbidden_address(host):
        raise DisallowedHostError(f"host {host!r} is an IP literal or non-public address")
    if host not in ALLOWED_HOSTS:
        raise DisallowedHostError(
            f"host {host!r} is not on the allowlist {sorted(ALLOWED_HOSTS)}; "
            "add it to twfi.io.http.ALLOWED_HOSTS and document it in "
            "docs/DATA_PROVENANCE.md and docs/THREAT_MODEL.md first"
        )
    if parts.port not in (None, 443):
        raise DisallowedHostError(f"only port 443 is permitted, got {parts.port} in {url!r}")
    return host


class PoliteClient:
    """A deliberately small, deliberately restricted HTTP client.

    Args:
        budget: Politeness and size limits.
        transport: Injected for tests (``httpx.MockTransport``), so the suite
            exercises the real code path without touching a socket.
        sleep: Injected so rate limiting and backoff are testable instantly.
        monotonic: Injected clock for the rate limiter.
        now: Injected wall clock for ``retrieved_at`` provenance stamps.
    """

    def __init__(
        self,
        *,
        budget: PolitenessBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._budget = budget or PolitenessBudget()
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._stats = RequestStats()
        self._last_request_at: dict[str, float] = {}
        self._client = httpx.Client(
            transport=transport,
            timeout=self._budget.timeout(),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )

    # ------------------------------------------------------------------ dunder

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ----------------------------------------------------------------- getters

    @property
    def budget(self) -> PolitenessBudget:
        return self._budget

    @property
    def stats(self) -> RequestStats:
        return self._stats

    # ------------------------------------------------------------ public fetch

    def get_bytes(self, url: str) -> tuple[bytes, FetchResult]:
        """Fetch a small resource into memory (still subject to the byte caps)."""
        started = self._monotonic()
        response, redirects = self._request_with_retries("GET", url)
        payload = response.content
        self._account_bytes(url, len(payload))
        result = FetchResult(
            requested_url=url,
            resolved_url=str(response.request.url),
            status_code=response.status_code,
            num_bytes=len(payload),
            sha256=sha256_bytes(payload),
            elapsed_s=self._monotonic() - started,
            retrieved_at=self._now().isoformat(),
            redirects=redirects,
        )
        return payload, result

    def get_json(self, url: str) -> tuple[Any, FetchResult]:
        """Fetch and decode a JSON document."""
        payload, result = self.get_bytes(url)
        try:
            return json.loads(payload), result
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataAccessError(f"{url} did not return valid JSON: {exc}") from exc

    def download(self, url: str, destination: Path) -> FetchResult:
        """Stream a resource to disk, aborting if it exceeds the byte ceilings.

        A partial file is always removed, so a failed download can never be
        mistaken for a complete artifact by the manifest verifier.
        """
        started = self._monotonic()
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".partial")

        response, redirects = self._request_with_retries("GET", url, stream=True)
        written = 0
        try:
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    written += len(chunk)
                    self._check_size(url, written)
                    handle.write(chunk)
            declared = response.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) != written:
                raise DataAccessError(
                    f"{url} declared Content-Length {declared} but delivered {written} bytes"
                )
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        finally:
            response.close()

        self._account_bytes(url, written)
        partial.replace(destination)
        return FetchResult(
            requested_url=url,
            resolved_url=str(response.request.url),
            status_code=response.status_code,
            num_bytes=written,
            sha256=sha256_bytes(destination.read_bytes()),
            elapsed_s=self._monotonic() - started,
            retrieved_at=self._now().isoformat(),
            redirects=redirects,
            path=destination,
        )

    # ------------------------------------------------------------------ internals

    def _request_with_retries(
        self, method: str, url: str, *, stream: bool = False
    ) -> tuple[httpx.Response, tuple[str, ...]]:
        """Perform one logical request, following and re-validating redirects."""
        current = url
        redirects: list[str] = []

        for _hop in range(MAX_REDIRECTS + 1):
            response = self._single_request(method, current, stream=stream)
            if not response.is_redirect:
                return response, tuple(redirects)

            location = response.headers.get("Location")
            response.close()
            if not location:
                raise DataAccessError(f"{current} returned {response.status_code} without Location")
            target = str(httpx.URL(current).join(location))
            # A redirect is attacker-influenceable, so it gets the same scrutiny
            # as the original URL rather than being trusted because it came from
            # an allowlisted host.
            assert_url_allowed(target)
            redirects.append(target)
            current = target

        raise DataAccessError(f"{url} exceeded {MAX_REDIRECTS} redirects: {redirects}")

    def _single_request(self, method: str, url: str, *, stream: bool) -> httpx.Response:
        host = assert_url_allowed(url)
        last_error: Exception | None = None

        for attempt in range(1, self._budget.max_retries + 1):
            self._account_request(host)
            self._throttle(host)
            try:
                request = self._client.build_request(method, url)
                response = self._client.send(request, stream=stream)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.is_server_error:
                    response.close()
                    last_error = DataAccessError(f"{url} returned {response.status_code}")
                else:
                    # 4xx is not retried: it means we asked for the wrong thing,
                    # and retrying would only add load (docs/DATA_PROVENANCE.md 2.6).
                    if response.is_client_error:
                        response.close()
                        raise DataAccessError(f"{url} returned {response.status_code}")
                    return response

            if attempt < self._budget.max_retries:
                self._stats.retries += 1
                self._sleep(self._budget.backoff_delay(attempt))

        raise DataAccessError(
            f"{url} failed after {self._budget.max_retries} attempts: {last_error}"
        ) from last_error

    def _throttle(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            remaining = self._budget.min_interval_s - (self._monotonic() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at[host] = self._monotonic()

    def _account_request(self, host: str) -> None:
        count = self._stats.requests_per_host.get(host, 0) + 1
        if count > self._budget.max_requests_per_host:
            raise RateLimitViolationError(
                f"refusing request {count} to {host}: this run's cap is "
                f"{self._budget.max_requests_per_host}"
            )
        self._stats.requests_per_host[host] = count

    def _check_size(self, url: str, file_bytes: int) -> None:
        if file_bytes > self._budget.max_bytes_per_file:
            raise DownloadLimitExceededError(
                f"{url} exceeded the {self._budget.max_bytes_per_file} byte per-file limit"
            )
        if self._stats.total_bytes + file_bytes > self._budget.max_bytes_per_run:
            raise DownloadLimitExceededError(
                f"{url} would exceed the {self._budget.max_bytes_per_run} byte per-run limit"
            )

    def _account_bytes(self, url: str, file_bytes: int) -> None:
        self._check_size(url, file_bytes)
        self._stats.total_bytes += file_bytes

    def snapshot(self) -> Mapping[str, object]:
        """A JSON-serialisable record of this run's network behaviour."""
        return {
            "user_agent": USER_AGENT,
            "allowed_hosts": sorted(ALLOWED_HOSTS),
            "requests_per_host": dict(self._stats.requests_per_host),
            "total_requests": self._stats.total_requests,
            "total_bytes": self._stats.total_bytes,
            "retries": self._stats.retries,
            "min_interval_s": self._budget.min_interval_s,
        }
