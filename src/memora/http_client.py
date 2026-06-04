"""Shared HTTP client for Memora modules.

Wraps urllib.request with retries, circuit breaker, uniform headers, and
JSON (de)serialization.  No external dependencies.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from ._version import __version__

logger = logging.getLogger(__name__)


@dataclass
class CircuitState:
    """Mutable circuit-breaker state."""

    open: bool = False
    open_until: float = 0.0
    consecutive_failures: int = 0


@dataclass
class HttpConfig:
    """Configuration for the shared HTTP client."""

    base_url: str = ""
    token: str = ""
    timeout: float = 30.0
    max_retries: int = 3
    base_delay: float = 1.0
    user_agent: str = field(default_factory=lambda: f"memora/{__version__}")
    circuit: CircuitState = field(default_factory=CircuitState)


class HttpClient:
    """Minimal HTTP client with retry, circuit breaker, and JSON helpers.

    All methods raise on failure after retries are exhausted.
    """

    def __init__(self, config: HttpConfig | None = None) -> None:
        self.cfg = config or HttpConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET *path* (query string built from *params*)."""
        url = self._url(path, params)
        return self._request(url, method="GET")

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST JSON *body* to *path*."""
        url = self._url(path)
        data = json.dumps(body).encode("utf-8") if body else None
        return self._request(url, method="POST", data=data)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        base = self.cfg.base_url.rstrip("/")
        url = f"{base}{path}"
        if params:
            qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items() if v is not None)
            if qs:
                url += "?" + qs
        return url

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
    ) -> dict[str, Any]:
        # Circuit breaker check
        if self.cfg.circuit.open:
            if time.time() < self.cfg.circuit.open_until:
                raise Exception("Circuit breaker is open")
            self.cfg.circuit.open = False
            self.cfg.circuit.consecutive_failures = 0

        last_exc: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.cfg.token}",
                    "Content-Type": "application/json",
                    "User-Agent": self.cfg.user_agent,
                },
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    self.cfg.circuit.consecutive_failures = 0
                    return result
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                last_exc = e
                should_retry = False
                if isinstance(e, urllib.error.HTTPError):
                    if e.code >= 500 or e.code == 429:
                        should_retry = True
                else:
                    should_retry = True

                if not should_retry or attempt == self.cfg.max_retries:
                    self._trip_breaker()
                    raise

                delay = self.cfg.base_delay * (2 ** attempt)
                logger.debug("HTTP %s failed (attempt %d/%d), retrying in %.1fs: %s",
                             method, attempt + 1, self.cfg.max_retries + 1, delay, e)
                time.sleep(delay)
            except Exception:
                self._trip_breaker()
                raise

        # Should never be reached, but satisfy the type checker
        raise last_exc  # type: ignore[misc]

    def _trip_breaker(self) -> None:
        self.cfg.circuit.consecutive_failures += 1
        if self.cfg.circuit.consecutive_failures >= 3:
            self.cfg.circuit.open = True
            self.cfg.circuit.open_until = time.time() + 60.0
            logger.warning("Circuit breaker opened after %d consecutive failures",
                           self.cfg.circuit.consecutive_failures)
