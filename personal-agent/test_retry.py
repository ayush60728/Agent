import actions
"""
test_retry.py

Unit tests for retry.py with exponential backoff and timeout.
"""

import time
from retry import with_retry, RetryExhausted


def test_success_first_try():
    print("\n--- 1. Success on first try ---")
    calls = 0

    def work():
        nonlocal calls
        calls += 1
        return "success"

    res = with_retry(work, retries=2, base_delay=0.05, label="TestWork")
    assert res == "success"
    assert calls == 1
    print("  [OK] Returned immediately on 1st attempt")


def test_retry_then_succeed():
    print("\n--- 2. Fail once then succeed ---")
    calls = 0

    def work():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise ValueError("transient error")
        return "recovered"

    res = with_retry(work, retries=2, base_delay=0.05, label="TestRecover")
    assert res == "recovered"
    assert calls == 2
    print("  [OK] Succeeded on 2nd attempt after retry")


def test_retry_exhausted():
    print("\n--- 3. Always fail raises RetryExhausted ---")
    calls = 0

    def work():
        nonlocal calls
        calls += 1
        raise RuntimeError("fatal")

    raised = False
    try:
        with_retry(work, retries=2, base_delay=0.05, label="TestFatal")
    except RetryExhausted as e:
        raised = True
        assert isinstance(e.__cause__, RuntimeError)

    assert raised
    assert calls == 3  # attempt 1 + 2 retries
    print("  [OK] Raised RetryExhausted after 3 attempts")


def test_timeout():
    print("\n--- 4. Timeout per attempt ---")

    def slow_work():
        time.sleep(0.5)
        return "slow"

    raised = False
    try:
        with_retry(slow_work, retries=1, base_delay=0.05, timeout=0.1, label="TestSlow")
    except RetryExhausted as e:
        raised = True
        assert isinstance(e.__cause__, TimeoutError)

    assert raised
    print("  [OK] Interrupted by per-call timeout and exhausted retries")


if __name__ == "__main__":
    test_success_first_try()
    test_retry_then_succeed()
    test_retry_exhausted()
    test_timeout()
    print("\nALL RETRY TESTS PASSED")
