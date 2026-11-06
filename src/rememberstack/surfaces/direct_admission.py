"""Direct-path admission: per-credential and per-deployment request limits.

SDK and CLI calls reach the engine directly, not through a host that could
meter them, so the perimeter can bound what one credential and the whole
deployment may ask for (D136, one-key design §7.6). Every limit is **off by
default**: a limit applies only when it is configured to a positive number,
and with none configured no admission object is built and the perimeter does
no admission work at all. After authentication and before the route runs,
every request (except ``GET /healthz``) must pass the configured checks, for
its credential and for the deployment:

- **Rate** — a token bucket: a counter that refills continuously at the
  configured rate up to a *burst* ceiling; each request takes one token. A
  caller that has been quiet may send a burst at once, then settles to the
  rate. The burst is a quarter of the per-minute rate (15 seconds of
  traffic): 30 at 120 per minute.
- **In flight** — a counting semaphore: at most N requests of one credential
  (and M of the deployment) running at the same time. A slot is taken on
  admission and given back when the response finishes, fails, or the client
  disconnects.

A request is admitted only if every check passes, and nothing is consumed
when any fails, so a refused request never costs the caller a token. The
refusal is ``429`` with ``rate_limited`` or ``concurrency_limited`` and a
``Retry-After`` in whole seconds.

Counters live in this process's memory. Postgres-backed counters were
rejected: a write on every request, contention on hot rows, and a slow
database refusing healthy reads. With N API replicas the effective ceilings
are N times the configured numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import threading
import time
from typing import Literal

from rememberstack.model import AuthenticatedContext

#: Burst ceiling as a fraction of the per-minute rate: 15 seconds of traffic.
_BURST_SECONDS = 15

#: The per-credential table is swept of idle entries once it outgrows this.
_SWEEP_FLOOR = 1024

RefusalCode = Literal["rate_limited", "concurrency_limited"]


def admission_key(context: AuthenticatedContext | None) -> str | None:
    """The credential identity that per-credential limits are counted against.

    The credential's audit identity, its kind marker and ``jti``
    (``keycred:<jti>``, ``browsercred:<jti>``, ``dpcred:<jti>``): two
    credentials of one person are limited separately, one credential is limited
    the same wherever it is presented from, and credentials of different kinds
    never share a bucket. ``None`` — the shared-secret bearer, which names no
    credential, or a deployment without an auth perimeter — is bounded by the
    deployment limits only.
    """
    if context is None:
        return None
    return context.actor_id


@dataclass(frozen=True)
class AdmissionLimits:
    """The four configurable numbers; ``0`` (the default) means no limit."""

    key_per_minute: int = 0
    key_in_flight: int = 0
    deployment_per_minute: int = 0
    deployment_in_flight: int = 0

    def __post_init__(self) -> None:
        """Refuse a negative limit."""
        for name in (
            "key_per_minute",
            "key_in_flight",
            "deployment_per_minute",
            "deployment_in_flight",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"admission limit {name} must be 0 (off) or more")

    @property
    def enabled(self) -> bool:
        """Whether any limit is configured."""
        return (
            self.key_per_minute > 0
            or self.key_in_flight > 0
            or self.deployment_per_minute > 0
            or self.deployment_in_flight > 0
        )


class AdmissionRefused(Exception):
    """A request refused by a rate or in-flight limit."""

    def __init__(self, *, code: RefusalCode, retry_after: int) -> None:
        """Bind the error code and the whole seconds to wait."""
        super().__init__(code)
        self.code: RefusalCode = code
        self.retry_after = retry_after


class _Counter:
    """One bucket plus one in-flight count."""

    __slots__ = ("in_flight", "tokens", "updated")

    def __init__(self, *, tokens: float, now: float) -> None:
        self.tokens = tokens
        self.updated = now
        self.in_flight = 0

    def refill(self, *, now: float, per_second: float, burst: float) -> None:
        """Add the tokens accrued since the last look, up to the burst."""
        self.tokens = min(burst, self.tokens + (now - self.updated) * per_second)
        self.updated = now

    def wait(self, *, per_second: float) -> float:
        """Seconds until one whole token is available (0 when it already is)."""
        return max(0.0, (1.0 - self.tokens) / per_second)


@dataclass
class AdmissionSlot:
    """Proof of admission; hand it back to :meth:`DirectPathAdmission.release`."""

    key: str | None
    released: bool = False


class RequestHold:
    """One request's admission slot, held until the request and its handlers end.

    A client disconnect cancels the request's coroutine, but a synchronous
    handler already running on a worker thread cannot be stopped: it keeps
    working, and keeps holding database connections. So the slot is released
    only once the request is over *and* no handler thread started under it is
    still running, whichever happens last.
    """

    def __init__(self, *, admission: DirectPathAdmission, slot: AdmissionSlot) -> None:
        """Hold ``slot`` on behalf of one request."""
        self._admission = admission
        self._slot = slot
        self._lock = threading.Lock()
        self._handlers = 0
        self._request_over = False

    def handler_started(self) -> None:
        """A handler thread began running under this request."""
        with self._lock:
            self._handlers += 1

    def handler_finished(self) -> None:
        """A handler thread returned or raised."""
        with self._lock:
            self._handlers -= 1
            done = self._request_over and self._handlers == 0
        if done:
            self._admission.release(self._slot)

    def request_finished(self) -> None:
        """The ASGI request is over: answered, failed or cancelled."""
        with self._lock:
            self._request_over = True
            done = self._handlers == 0
        if done:
            self._admission.release(self._slot)


class DirectPathAdmission:
    """In-process rate and in-flight limits for one deployment's API process.

    Thread-safe: admission happens on the event loop and releases can happen
    on handler threads. One lock guards all counters; every critical section
    is a few arithmetic operations.
    """

    def __init__(
        self, *, limits: AdmissionLimits, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Start with full buckets and nothing in flight."""
        self._limits = limits
        self._clock = clock
        self._lock = threading.Lock()
        # A zero limit is off; its rate is then never read.
        self._key_rate = limits.key_per_minute / 60
        self._key_burst = float(max(1, limits.key_per_minute * _BURST_SECONDS // 60))
        self._deployment_rate = limits.deployment_per_minute / 60
        self._deployment_burst = float(
            max(1, limits.deployment_per_minute * _BURST_SECONDS // 60)
        )
        #: Per-credential counters are kept only when a per-credential limit is on.
        self._per_key = limits.key_per_minute > 0 or limits.key_in_flight > 0
        self._deployment = _Counter(tokens=self._deployment_burst, now=clock())
        self._keys: dict[str, _Counter] = {}
        self._sweep_above = _SWEEP_FLOOR

    def admit(self, *, key: str | None) -> AdmissionSlot:
        """Take one token and one in-flight slot for ``key`` and the deployment.

        Raises :class:`AdmissionRefused` without consuming anything when any
        configured limit is reached; a limit of ``0`` is never checked.
        """
        limits = self._limits
        with self._lock:
            now = self._clock()
            deployment = self._deployment
            if limits.deployment_per_minute:
                deployment.refill(
                    now=now,
                    per_second=self._deployment_rate,
                    burst=self._deployment_burst,
                )
            counter: _Counter | None = None
            if key is not None and self._per_key:
                counter = self._keys.get(key)
                if counter is None:
                    counter = _Counter(tokens=self._key_burst, now=now)
                elif limits.key_per_minute:
                    counter.refill(
                        now=now, per_second=self._key_rate, burst=self._key_burst
                    )

            wait = 0.0
            if limits.deployment_per_minute:
                wait = deployment.wait(per_second=self._deployment_rate)
            if counter is not None and limits.key_per_minute:
                wait = max(wait, counter.wait(per_second=self._key_rate))
            if wait > 0:
                raise AdmissionRefused(
                    code="rate_limited", retry_after=max(1, math.ceil(wait))
                )
            if (
                limits.deployment_in_flight
                and deployment.in_flight >= limits.deployment_in_flight
            ) or (
                counter is not None
                and limits.key_in_flight
                and counter.in_flight >= limits.key_in_flight
            ):
                raise AdmissionRefused(code="concurrency_limited", retry_after=1)

            if limits.deployment_per_minute:
                deployment.tokens -= 1
            deployment.in_flight += 1
            if counter is not None and key is not None:
                if limits.key_per_minute:
                    counter.tokens -= 1
                counter.in_flight += 1
                self._keys[key] = counter
                if len(self._keys) > self._sweep_above:
                    self._sweep(now=now)
            return AdmissionSlot(key=key)

    def release(self, slot: AdmissionSlot) -> None:
        """Give back the in-flight slot taken by :meth:`admit`; idempotent."""
        with self._lock:
            if slot.released:
                return
            slot.released = True
            self._deployment.in_flight -= 1
            if slot.key is not None:
                counter = self._keys.get(slot.key)
                if counter is not None:
                    counter.in_flight -= 1

    def _sweep(self, *, now: float) -> None:
        """Forget credentials that are idle with a full bucket.

        Such an entry is indistinguishable from an absent one, so dropping it
        changes no decision; it only keeps the table from growing with every
        credential ever seen. Called with the lock held, amortised by doubling
        the threshold.
        """
        for key, counter in list(self._keys.items()):
            if self._limits.key_per_minute:
                counter.refill(
                    now=now, per_second=self._key_rate, burst=self._key_burst
                )
            if counter.in_flight == 0 and counter.tokens >= self._key_burst:
                del self._keys[key]
        self._sweep_above = max(_SWEEP_FLOOR, 2 * len(self._keys))
