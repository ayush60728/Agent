"""
app_switch.py

Small pending state for the Windows Task View flow:
    "switch tabs" -> open Task View -> ask which app
    "brave"       -> focus Brave
"""

import re
import time


SWITCH_TIMEOUT_S = 30.0

_pending = None

_DENY = {
    "no", "n", "nope", "nah", "cancel", "stop", "dont", "abort",
    "never mind", "nevermind", "forget it", "leave it", "none",
}


def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9\s]", "", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def arm() -> None:
    global _pending
    _pending = {"ts": time.time()}


def is_pending() -> bool:
    global _pending
    if _pending is None:
        return False
    if time.time() - _pending["ts"] > SWITCH_TIMEOUT_S:
        _pending = None
        return False
    return True


def clear() -> None:
    global _pending
    _pending = None


def interpret(text: str):
    norm = _norm(text)
    if not norm:
        return "unrelated"
    if norm in _DENY:
        return "cancel"
    return norm
