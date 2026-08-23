"""Per-IP login brute-force throttle used by the auth router.

LoginThrottle keeps a bounded in-memory table of failed-login counters keyed
by client IP. The module-level `login_throttle` is the one shared instance.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

FAILURE_WINDOW_SECONDS = 600   # forget stale failures older than this
LOCKOUT_THRESHOLD = 5          # consecutive failures before lockout
LOCKOUT_SECONDS = 60           # base cooldown, doubles each subsequent failure
MAX_BACKOFF_EXPONENT = 6       # cap doubling at ~64 min
MAX_TRACKED_IPS = 1024         # bound the table, evict the stalest IP past this


@dataclass
class ThrottleState:
    """Per-IP brute-force counter: failure streak, lockout deadline, last-seen."""

    failed_attempts: int = 0
    locked_until: float = 0.0
    last_failure_at: float = 0.0


class LoginThrottle:
    """Bounded per-IP failed-login throttle with exponential lockout backoff.

    API: retry_after(ip) reports an active lockout, record_failure(ip) counts
    a failure and arms the lockout past the threshold, reset(ip) clears one
    source on a completed login, clear() wipes the table (tests).

    Per-IP keying gives each source its own counter, so an unauthenticated
    third party cannot lock the one legitimate owner out by trickling wrong
    passwords past a shared global counter.

    Defense-in-depth limitation (accepted): this throttle is BEST-EFFORT and
    NOT restart-durable. The table lives in process memory, so any process
    restart (the documented `uvicorn --reload` dev loop, a crash, a redeploy,
    or a container restart) resets it. The real protection is the
    bcrypt-hashed password. The lockout only slows online guessing between
    restarts. A DB-backed counter (a user_setting row) would make it durable
    and is the natural future hardening, but is deferred to avoid adding a
    write on every failed login for a single-user box.
    """

    def __init__(self) -> None:
        self._by_ip: dict[str, ThrottleState] = {}

    def retry_after(self, ip: str) -> int | None:
        """Whole seconds until this IP may retry, or None when not locked out."""
        state = self._by_ip.get(ip)
        now = time.monotonic()
        if state is not None and now < state.locked_until:
            return int(state.locked_until - now) + 1
        return None

    def record_failure(self, ip: str) -> None:
        """Record a failed login for one source IP and arm its lockout once
        the threshold trips."""
        now = time.monotonic()
        # Opportunistic cleanup: drop entries whose streak has gone stale and whose
        # lockout has passed, so the table does not grow without bound.
        for key in [
            k
            for k, st in self._by_ip.items()
            if now - st.last_failure_at > FAILURE_WINDOW_SECONDS and now >= st.locked_until
        ]:
            del self._by_ip[key]

        state = self._by_ip.get(ip)
        if state is None:
            # Memory bound: evict the stalest tracked source before adding a new one.
            if len(self._by_ip) >= MAX_TRACKED_IPS:
                oldest = min(self._by_ip, key=lambda k: self._by_ip[k].last_failure_at)
                del self._by_ip[oldest]
            state = ThrottleState()
            self._by_ip[ip] = state

        # Forget a stale failure streak so a slow trickle never accumulates a lockout.
        if now - state.last_failure_at > FAILURE_WINDOW_SECONDS:
            state.failed_attempts = 0
        state.last_failure_at = now
        state.failed_attempts += 1
        if state.failed_attempts >= LOCKOUT_THRESHOLD:
            exponent = min(state.failed_attempts - LOCKOUT_THRESHOLD, MAX_BACKOFF_EXPONENT)
            state.locked_until = now + LOCKOUT_SECONDS * (2 ** exponent)

    def reset(self, ip: str) -> None:
        """Drop one source's entry (a completed login)."""
        self._by_ip.pop(ip, None)

    def clear(self) -> None:
        """Drop the whole table (test isolation)."""
        self._by_ip.clear()

    def state(self, ip: str) -> ThrottleState | None:
        """The tracked state for one IP, or None (test inspection)."""
        return self._by_ip.get(ip)

    @property
    def tracked_ip_count(self) -> int:
        """Number of tracked source IPs (test inspection of the memory bound)."""
        return len(self._by_ip)


# The one shared instance. Module state is safe here: single-user,
# single-worker deployment (see scheduler.py WEB_CONCURRENCY=1 enforcement).
login_throttle = LoginThrottle()
