"""
cancel_token.py

Thread-safe cancellation flag.  Long-running operations (LLM call, OCR,
Whisper transcription) should poll token.cancelled and return early when set.

Usage:
    import cancel_token

    # Before dispatching a new command:
    cancel_token.GLOBAL_TOKEN.reset()

    # Inside a long op:
    if cancel_token.GLOBAL_TOKEN.cancelled:
        return []          # or raise CancelledError, etc.

    # From another thread (e.g. voice loop receives a new utterance):
    cancel_token.GLOBAL_TOKEN.cancel()
"""

import threading


class CancelToken:
    """A one-shot cancellation flag safe to set/check from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        """Signal cancellation.  Idempotent."""
        with self._lock:
            self._cancelled = True

    def reset(self) -> None:
        """Clear the flag — call once before each new command."""
        with self._lock:
            self._cancelled = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def __repr__(self) -> str:
        return f"CancelToken(cancelled={self.cancelled})"


# Module-level singleton shared by agent.py, voice_io.py, desktop_actions.py.
GLOBAL_TOKEN: CancelToken = CancelToken()
