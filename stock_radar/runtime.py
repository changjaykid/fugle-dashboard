"""Shared runtime guards for external API usage: a single-flight process
lock and a simple call-rate throttle. These exist so multiple invocations
of `sync-quotes --source fugle` (e.g. an 08:30 cron tick overlapping with a
manually-triggered re-run, or two cron schedules that happen to fire close
together) cannot both hit Fugle's rate-limited free-tier quota at the same
time and both burn through it, or silently double-count usage against
whatever per-minute/per-day cap the plan has.

Neither piece is Fugle-specific -- both are generic enough to reuse for any
future external quote source this project adds -- and neither modifies
stock_radar/fugle.py (owned by Codex for this round); they compose with it
purely via the `session=` parameter fetch_quotes()/fetch_quote()/
fetch_ticker() already accept.
"""
from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path


class LockBusyError(RuntimeError):
    """Raised when another process already holds the lock. Callers should
    treat this as 'skip this run, don't queue/retry indefinitely' for a
    quote-sync context -- stacking up blocked cron ticks waiting on a lock
    is worse than one tick cleanly skipping with a clear log line."""


@contextmanager
def single_flight_lock(lock_path: Path, *, blocking: bool = False):
    """POSIX advisory file lock (fcntl.flock) so only one process at a time
    can be inside the `with` block. Non-blocking by default: if another
    process already holds the lock, raises LockBusyError immediately rather
    than waiting (a cron-triggered sync-quotes run that can't get the lock
    should skip this tick cleanly, not pile up waiting processes each
    burning their own timeout against a rate-limited API).

    This is a real OS-level lock (works across separate `python3 -m
    stock_radar.cli` process invocations, not just threads within one
    process), released automatically on process exit even if the process
    is killed -- flock locks are held by the file descriptor/process, not
    by any application-level bookkeeping that could be left in a stuck
    state after a crash.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, 'a+')
    try:
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(fh.fileno(), flags)
        except OSError as exc:
            raise LockBusyError(f'another process already holds {lock_path}') from exc
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


class ThrottledSession:
    """Wraps a `requests`-like session so every .get() call is spaced at
    least `min_interval_seconds` apart, shared across ALL calls made
    through this one instance (not per-symbol) -- this is what makes it a
    real shared-quota throttle rather than a per-call decorator that a
    multi-symbol loop (like fugle.fetch_quotes()'s one-ticker+one-quote-
    call-per-symbol loop) would otherwise blow straight through.

    Deliberately simple (sleep-based, single-threaded pacing) rather than a
    token-bucket -- this project's call pattern is one sequential loop over
    a symbol list, not concurrent workers, so the extra complexity of a
    real token bucket isn't earning its keep yet. If a genuinely concurrent
    caller shows up later, replace this, don't extend it in place with ad
    hoc locking.
    """

    def __init__(self, min_interval_seconds: float = 0.34, *, real_session=None, sleep=time.sleep, clock=time.monotonic):
        # 0.34s default -> ~3 req/s, a conservative guess for a free-tier
        # plan with no documented rate limit found yet (Fugle's own docs
        # page did not specify a numeric requests-per-second cap as of
        # 2026-09-10); callers with a confirmed real limit should pass
        # their own min_interval_seconds rather than trust this default.
        import requests
        self._session = real_session or requests.Session()
        self._min_interval = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_call = None

    def _wait_turn(self):
        if self._last_call is not None:
            elapsed = self._clock() - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_call = self._clock()

    def get(self, *args, **kwargs):
        self._wait_turn()
        return self._session.get(*args, **kwargs)
