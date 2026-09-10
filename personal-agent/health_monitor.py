"""
health_monitor.py

Lightweight background health monitor for voice input and the LLM.

Two independent trackers:

  Voice health
  ────────────
  Each listen/transcribe tick is recorded as success or failure via
  record_voice_tick().  A sliding window of the last VOICE_WINDOW ticks
  drives a degraded flag: if the failure rate exceeds VOICE_ERROR_RATE
  the monitor requests a mic recalibrate.  voice_io.py checks
  needs_recalibrate() in its heartbeat loop and tears down/reopens the mic.

  LLM health
  ──────────
  record_llm_failure() / record_llm_success() track consecutive LLM
  failures.  After LLM_FAIL_THRESHOLD consecutive failures the LLM is
  declared unhealthy for LLM_COOL_DOWN_SECS seconds; llm_is_healthy()
  returns False during that window so agent.py can skip the Ollama call
  and fall back to cache/builtins, avoiding a long freeze per command.

Usage:
    import health_monitor

    # voice_io.py — after each listen attempt:
    health_monitor.record_voice_tick(ok=True)
    if health_monitor.needs_recalibrate():
        break   # outer loop reopens mic

    # agent.py — around ask_qwen():
    if not health_monitor.llm_is_healthy():
        return "The AI model is currently unavailable; please try again shortly."
    try:
        result = ask_qwen(...)
        health_monitor.record_llm_success()
    except Exception:
        health_monitor.record_llm_failure()
        raise
"""

import threading
import time
from collections import deque

# ── Tunable constants ────────────────────────────────────────────────────────

# Voice: sliding window size and failure-rate threshold that triggers recalibrate.
VOICE_WINDOW        = 20      # number of recent ticks to consider
VOICE_ERROR_RATE    = 0.50    # if >50 % of last VOICE_WINDOW ticks are errors → degrade

# LLM: consecutive failures before declaring the LLM unhealthy.
LLM_FAIL_THRESHOLD  = 3       # three back-to-back failures → cool-down
LLM_COOL_DOWN_SECS  = 60      # seconds to wait before trying the LLM again

# ── Internal state ───────────────────────────────────────────────────────────

_lock = threading.Lock()

# Voice
_voice_window: deque = deque(maxlen=VOICE_WINDOW)   # True=ok, False=error
_recalibrate_requested: bool = False

# LLM
_llm_consecutive_failures: int = 0
_llm_degraded_until: float = 0.0   # epoch time after which LLM is healthy again


# ── Voice API ────────────────────────────────────────────────────────────────

def record_voice_tick(ok: bool) -> None:
    """Record one voice listen/transcribe result.  Call after every attempt,
    whether it returned audio, silence, or raised an exception."""
    global _recalibrate_requested
    with _lock:
        _voice_window.append(ok)
        if len(_voice_window) >= VOICE_WINDOW:
            error_rate = _voice_window.count(False) / len(_voice_window)
            if error_rate > VOICE_ERROR_RATE and not _recalibrate_requested:
                print(
                    f"[HEALTH] Voice error rate {error_rate:.0%} over last "
                    f"{VOICE_WINDOW} ticks — requesting mic recalibrate."
                )
                _recalibrate_requested = True


def needs_recalibrate() -> bool:
    """True once after the voice error rate crosses the threshold.
    Calling this clears the flag, so it fires exactly once per degraded episode."""
    global _recalibrate_requested
    with _lock:
        if _recalibrate_requested:
            _recalibrate_requested = False
            _voice_window.clear()   # reset window so we don't immediately re-trigger
            return True
        return False


def reset_voice_health() -> None:
    """Called after a successful mic recalibrate to start a fresh window."""
    global _recalibrate_requested
    with _lock:
        _voice_window.clear()
        _recalibrate_requested = False


# ── LLM API ─────────────────────────────────────────────────────────────────

def llm_is_healthy() -> bool:
    """Return False during the cool-down window after LLM_FAIL_THRESHOLD
    consecutive failures — agent.py should skip the LLM call entirely."""
    with _lock:
        if time.monotonic() < _llm_degraded_until:
            return False
        return True


def record_llm_success() -> None:
    """Reset the consecutive failure counter after a successful LLM call."""
    global _llm_consecutive_failures
    with _lock:
        _llm_consecutive_failures = 0


def record_llm_failure() -> None:
    """Increment consecutive failure count; enter cool-down if threshold hit."""
    global _llm_consecutive_failures, _llm_degraded_until
    with _lock:
        _llm_consecutive_failures += 1
        if _llm_consecutive_failures >= LLM_FAIL_THRESHOLD:
            _llm_degraded_until = time.monotonic() + LLM_COOL_DOWN_SECS
            print(
                f"[HEALTH] LLM failed {_llm_consecutive_failures} times in a row — "
                f"skipping Ollama for {LLM_COOL_DOWN_SECS}s (cool-down)."
            )
            _llm_consecutive_failures = 0   # reset so next window starts fresh


def llm_status() -> dict:
    """Return a snapshot of current LLM health for diagnostics."""
    with _lock:
        now = time.monotonic()
        degraded = now < _llm_degraded_until
        return {
            "healthy": not degraded,
            "consecutive_failures": _llm_consecutive_failures,
            "cool_down_remaining_secs": max(0.0, _llm_degraded_until - now),
        }
