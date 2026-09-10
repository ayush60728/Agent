"""
test_cancel.py

Unit tests for cancel_token.py and early-exit in find_text_matches.
"""

import threading
import cancel_token
from desktop_actions import find_text_matches


def test_token_lifecycle():
    print("\n--- 1. CancelToken Lifecycle ---")
    tok = cancel_token.CancelToken()
    assert not tok.cancelled
    print("  [OK] Initial state is not cancelled")

    tok.cancel()
    assert tok.cancelled
    print("  [OK] cancel() sets cancelled to True")

    tok.reset()
    assert not tok.cancelled
    print("  [OK] reset() clears cancelled to False")


def test_token_threading():
    print("\n--- 2. Thread-safe cancellation ---")
    tok = cancel_token.CancelToken()

    def worker():
        tok.cancel()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert tok.cancelled
    print("  [OK] Cancelled from another thread")


def test_desktop_actions_cancel():
    print("\n--- 3. find_text_matches early exit on cancel ---")
    cancel_token.GLOBAL_TOKEN.cancel()
    matches = find_text_matches("submit")
    assert matches == []
    print("  [OK] find_text_matches returned empty list immediately when cancelled")
    cancel_token.GLOBAL_TOKEN.reset()


if __name__ == "__main__":
    test_token_lifecycle()
    test_token_threading()
    test_desktop_actions_cancel()
    print("\nALL CANCEL TESTS PASSED")
