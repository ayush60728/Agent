"""
retry.py

Lightweight retry helper with exponential backoff and optional per-call timeout.

No external dependencies — only stdlib (concurrent.futures, time, functools).

Usage:
    from retry import with_retry

    result = with_retry(
        lambda: ollama.chat(**kwargs),
        retries=2,        # total extra attempts after first failure
        base_delay=1.0,   # seconds before first retry; doubles each time
        timeout=30,       # seconds per attempt (None = no timeout)
        label="Ollama",   # printed in warning messages
    )
"""

import time
import functools
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError


class RetryExhausted(RuntimeError):
    """Raised when all retry attempts fail."""


def _call_with_timeout(fn, timeout):
    """Run fn() in a background thread; raise TimeoutError if it takes
    longer than `timeout` seconds.  Returns the function's return value."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            raise TimeoutError(f"call timed out after {timeout}s")


def with_retry(fn, *, retries: int = 2, base_delay: float = 1.0,
               timeout=None, label: str = "operation"):
    """
    Call fn() up to (1 + retries) times.

    On each failure:
        - print a warning with the error and attempt number
        - wait base_delay * 2^(attempt-1) seconds before the next try
          (1s → 2s → 4s with base_delay=1, retries=2)

    If `timeout` is given (seconds), each individual call is wrapped in a
    thread so it can be interrupted — raises TimeoutError for that attempt,
    which counts as one failure and is subject to the same retry logic.

    Raises RetryExhausted (with the last exception as __cause__) when every
    attempt has failed.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            if timeout is not None:
                return _call_with_timeout(fn, timeout)
            return fn()
        except Exception as exc:
            last_exc = exc
            remaining = retries - attempt
            if remaining <= 0:
                break
            delay = base_delay * (2 ** attempt)
            print(
                f"[RETRY] {label} failed (attempt {attempt + 1}/{retries + 1}): "
                f"{type(exc).__name__}: {exc}  — retrying in {delay:.1f}s"
            )
            time.sleep(delay)

    raise RetryExhausted(
        f"{label} failed after {retries + 1} attempt(s)"
    ) from last_exc
