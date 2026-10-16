"""Polite HTTP client. curl_cffi impersonates Chrome's TLS fingerprint, which
gets us past the Cloudflare bot check that several Danish housing sites use."""
from __future__ import annotations

import logging
import random
import time

from curl_cffi import requests

log = logging.getLogger(__name__)


class Http:
    def __init__(self, delay: float = 0.8, retries: int = 3, timeout: int = 60):
        self.session = requests.Session(impersonate="chrome124")
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed + random.uniform(0, 0.3))
        self._last = time.monotonic()

    def request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait()
            try:
                resp = self.session.request(method, url, **kw)
                if resp.status_code in (403, 429, 500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {resp.status_code} for {url}")
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                backoff = 2 ** attempt + random.uniform(0, 1)
                log.warning("%s (attempt %d/%d), retrying in %.1fs", exc, attempt, self.retries, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"giving up on {url}: {last_exc}")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw):
        resp = self.get(url, **kw)
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"non-JSON response from {url}: {resp.text[:200]!r}") from exc
