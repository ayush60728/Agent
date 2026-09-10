import action_runner
import actions
import action_registry
"""
test_verbosity.py

Unit and integration tests for verbosity controls (terse vs. detailed mode).
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
import agent
from mock_win_api import MockWindowsEnvironment


def test_verbosity_config_and_toggle():
    print("\n--- 1. Verbosity config and action toggling ---")
    assert config.VERBOSITY in ("terse", "detailed")

    res_det = action_registry.execute({"action": "set_verbosity", "target": "detailed"})
    assert config.VERBOSITY == "detailed"
    assert "detailed" in res_det.lower()
    print("  [OK] Successfully toggled to detailed mode")

    res_terse = action_registry.execute({"action": "set_verbosity", "target": "terse"})
    assert config.VERBOSITY == "terse"
    assert "terse" in res_terse.lower()
    print("  [OK] Successfully toggled to terse mode")


def test_terse_vs_detailed_dispatch():
    print("\n--- 2. Terse vs. Detailed Dispatch Output ---")
    action = {"action": "open_app", "target": "brave"}

    # Mock window environment
    with MockWindowsEnvironment():
        # In Detailed mode: raw verbose string
        config.VERBOSITY = "detailed"
        reply_detailed = action_runner._dispatch(action, for_speech=False)
        assert "opened" in reply_detailed.lower() or "launched" in reply_detailed.lower()

        # In Terse mode: concise summary
        config.VERBOSITY = "terse"
        reply_terse = action_runner._dispatch(action, for_speech=False)
        assert reply_terse == "Opened brave."
        print("  [OK] Terse mode returned crisp summary ('Opened brave.')")


def test_in_session_verbosity_command():
    print("\n--- 3. In-session Verbosity Built-in Commands ---")
    reply = agent.process_command("set verbosity detailed")
    assert config.VERBOSITY == "detailed"
    assert "detailed" in reply.lower()

    reply2 = agent.process_command("terse mode")
    assert config.VERBOSITY == "terse"
    assert "terse" in reply2.lower()
    print("  [OK] In-session command switched verbosity seamlessly")


if __name__ == "__main__":
    test_verbosity_config_and_toggle()
    test_terse_vs_detailed_dispatch()
    test_in_session_verbosity_command()
    print("\nALL VERBOSITY TESTS PASSED")
