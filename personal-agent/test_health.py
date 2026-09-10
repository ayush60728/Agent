import actions
"""
test_health.py

Unit tests for health_monitor.py (voice tick error tracking, mic recalibrate triggering,
LLM failure streak cool-down).
"""

import time
import health_monitor


def test_voice_health():
    print("\n--- 1. Voice health & recalibration trigger ---")
    health_monitor.reset_voice_health()
    assert not health_monitor.needs_recalibrate()

    # Record 10 successes, 10 errors -> 50% error rate (not > 50%)
    for _ in range(10):
        health_monitor.record_voice_tick(ok=True)
    for _ in range(10):
        health_monitor.record_voice_tick(ok=False)

    assert not health_monitor.needs_recalibrate()
    print("  [OK] 50% error rate does not trigger recalibration")

    # One more failure -> 11/20 = 55% > 50% threshold
    health_monitor.record_voice_tick(ok=False)
    assert health_monitor.needs_recalibrate()
    print("  [OK] >50% error rate triggers needs_recalibrate()")

    # Second check should return False (one-shot flag cleared)
    assert not health_monitor.needs_recalibrate()
    print("  [OK] Flag cleared after being read")


def test_llm_health():
    print("\n--- 2. LLM failure streak & cool-down ---")
    # Reset
    health_monitor.record_llm_success()
    assert health_monitor.llm_is_healthy()
    print("  [OK] LLM initially healthy")

    # Two failures (threshold is 3)
    health_monitor.record_llm_failure()
    health_monitor.record_llm_failure()
    assert health_monitor.llm_is_healthy()
    print("  [OK] 2 failures still considered healthy")

    # Success resets counter
    health_monitor.record_llm_success()
    assert health_monitor.llm_status()["consecutive_failures"] == 0
    print("  [OK] Success resets failure counter")

    # 3 consecutive failures triggers cool-down
    orig_cooldown = health_monitor.LLM_COOL_DOWN_SECS
    health_monitor.LLM_COOL_DOWN_SECS = 0.2
    try:
        health_monitor.record_llm_failure()
        health_monitor.record_llm_failure()
        health_monitor.record_llm_failure()
        assert not health_monitor.llm_is_healthy()
        print("  [OK] 3 consecutive failures marks LLM unhealthy")

        time.sleep(0.25)
        assert health_monitor.llm_is_healthy()
        print("  [OK] LLM recovers after cool-down period")
    finally:
        health_monitor.LLM_COOL_DOWN_SECS = orig_cooldown


if __name__ == "__main__":
    test_voice_health()
    test_llm_health()
    print("\nALL HEALTH MONITOR TESTS PASSED")
