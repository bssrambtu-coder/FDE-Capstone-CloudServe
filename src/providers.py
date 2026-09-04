"""Model provider access: caching, backoff, circuit breaker, fault injection.

The spine ships with a deterministic local provider so a clean checkout runs
with no API key. Swapping in OpenRouter or Groq means implementing `complete`
on one class; nothing upstream changes.

Every wrapper here exists to serve A11 ("the system handles failure without
crashing") and the Build Specification's insistence that free-tier rate limits
are a design problem rather than an obstacle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Base class: anything the caller should degrade on rather than crash."""


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


class Provider(Protocol):
    def complete(self, prompt: str, *, max_tokens: int = 512) -> str: ...


class StubProvider:
    """Deterministic stand-in. Returns nothing useful and is not meant to —
    it exists so the spine runs end to end before a key is configured, and so
    tests never touch the network. Same prompt always yields the same output,
    which keeps A5 determinism honest.
    """

    name = "stub"

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        return json.dumps({"stub": True, "digest": digest})


class FaultInjector:
    """Wraps a provider and induces the A11 failure conditions on demand.

    Set FAULT_MODE to outage, timeout, ratelimit or malformed. This is how the
    provider is "disconnected entirely" during testing without unplugging
    anything, and it is exercised by the test suite rather than only by hand.
    """

    def __init__(self, inner: Provider, mode: str | None = None):
        self.inner = inner
        self.mode = (mode if mode is not None else os.environ.get("FAULT_MODE", "")).strip().lower()

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        if self.mode == "outage":
            raise ProviderUnavailable("FAULT_MODE=outage")
        if self.mode == "timeout":
            raise ProviderTimeout("FAULT_MODE=timeout")
        if self.mode == "ratelimit":
            raise RateLimited("FAULT_MODE=ratelimit")
        if self.mode == "malformed":
            return "{not json at all"
        return self.inner.complete(prompt, max_tokens=max_tokens)


class Cache:
    """On-disk response cache keyed by prompt.

    Saves the free-tier allowance, makes runs reproducible, and means a
    re-run after a mid-run failure costs nothing. The Build Specification
    encourages this explicitly.
    """

    def __init__(self, path: str | Path = "storage/model_cache.json"):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("cache at %s unreadable, starting empty", self.path)
        self._dirty = False

    @staticmethod
    def key(prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()

    def get(self, prompt: str) -> str | None:
        return self._data.get(self.key(prompt))

    def put(self, prompt: str, value: str) -> None:
        self._data[self.key(prompt)] = value
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data), encoding="utf-8")
        self._dirty = False


class ResilientProvider:
    """Cache, then retry with exponential backoff, then trip a circuit breaker.

    Once the breaker is open the run stops calling the provider at all and
    every caller degrades immediately. That is what keeps a 120-ticket
    unattended run from spending an hour retrying a dead endpoint.
    """

    def __init__(
        self,
        inner: Provider,
        *,
        cache: Cache | None = None,
        max_retries: int = 3,
        base_delay: float = 0.5,
        breaker_threshold: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.inner = inner
        self.cache = cache if cache is not None else Cache()
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.breaker_threshold = breaker_threshold
        self._sleep = sleep
        self.consecutive_failures = 0
        self.calls = 0
        self.cache_hits = 0
        self.degradations = 0

    @property
    def breaker_open(self) -> bool:
        return self.consecutive_failures >= self.breaker_threshold

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        hit = self.cache.get(prompt)
        if hit is not None:
            self.cache_hits += 1
            return hit

        if self.breaker_open:
            self.degradations += 1
            raise ProviderUnavailable(
                f"circuit breaker open after {self.consecutive_failures} consecutive failures"
            )

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self.calls += 1
                value = self.inner.complete(prompt, max_tokens=max_tokens)
            except RateLimited as exc:
                last = exc
                delay = self.base_delay * (2 ** attempt)
                log.warning("rate limited, backing off %.1fs (attempt %d)", delay, attempt + 1)
                self._sleep(delay)
            except (ProviderTimeout, ProviderUnavailable) as exc:
                last = exc
                delay = self.base_delay * (2 ** attempt)
                log.warning("provider error %s, retrying in %.1fs", exc, delay)
                self._sleep(delay)
            else:
                self.consecutive_failures = 0
                self.cache.put(prompt, value)
                return value

        self.consecutive_failures += 1
        self.degradations += 1
        raise ProviderUnavailable(f"exhausted {self.max_retries} attempts: {last}")


def get_provider(*, mode: str | None = None, cache_path: str = "storage/model_cache.json"):
    """Build the provider stack the pipeline uses.

    Replace StubProvider here with an OpenRouter/Groq client and the rest of
    the system is unchanged.
    """
    from .config import CONFIG

    return ResilientProvider(
        FaultInjector(StubProvider(), mode=mode),
        cache=Cache(cache_path),
        max_retries=CONFIG.provider_max_retries,
    )
