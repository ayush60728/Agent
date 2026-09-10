import actions
"""
test_e2e_voice.py

End-to-End Voice Interaction tests:
    1. Wake-word alias & fuzzy mishearing recognition
    2. Direct command bypassing wake word
    3. Voice cancellation phrasings
    4. Multi-turn voice dialogue with confirmation gating
    5. Voice health monitor degradation & recalibration triggering
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import confirmation
import health_monitor
import voice_io
import agent
import desktop_actions
from mock_win_api import MockWindowsEnvironment


def test_wake_word_and_aliases():
    print("\n--- 1. E2E Wake-Word & Mishearing Matcher ---")
    assert voice_io._find_wake_word("agent open brave") in voice_io.WAKE_WORD_ALIASES
    assert voice_io._find_wake_word("hey agent please scroll down") in voice_io.WAKE_WORD_ALIASES
    # Fuzzy mishearings from Whisper
    assert voice_io._find_wake_word("asian open brave") == "asian"
    assert voice_io._find_wake_word("ancient switch to code") == "ancient"
    assert voice_io._find_wake_word("urgent close window") == "urgent"
    # Non wake word
    assert voice_io._find_wake_word("hello world") is None
    print("  [OK] Standard wake-words and common Whisper mishearings recognized")


def test_direct_commands():
    print("\n--- 2. E2E Direct Command Detection ---")
    assert voice_io._looks_like_direct_command("open brave")
    assert voice_io._looks_like_direct_command("scroll down")
    assert voice_io._looks_like_direct_command("type hello world")
    assert voice_io._looks_like_direct_command("close tab")
    assert not voice_io._looks_like_direct_command("i want to search for cars")
    print("  [OK] Direct imperative command verbs recognized without wake word")


def test_cancel_phrases():
    print("\n--- 3. E2E Voice Cancellation Phrases ---")
    assert voice_io._is_cancel("never mind")
    assert voice_io._is_cancel("nevermind")
    assert voice_io._is_cancel("cancel")
    assert voice_io._is_cancel("forget that")
    assert voice_io._is_cancel("leave it")
    assert not voice_io._is_cancel("cancel the meeting")
    print("  [OK] Abort and cancel phrases correctly matched")


def test_voice_dialogue_with_confirmation():
    print("\n--- 4. E2E Multi-turn Voice Dialogue & Confirmation ---")
    confirmation.clear()
    health_monitor.reset_voice_health()

    with MockWindowsEnvironment() as env:
        win = env.windows.add_window("Brave - New Tab", left=100, top=100, width=1200, height=800)
        desktop_actions.focus_app("brave")

        # Step 1: User says destructive command "close tab"
        cmd1 = "close tab"
        reply1 = agent.process_command(cmd1, for_speech=True)
        print("REPLY1:", reply1)
        assert confirmation.is_pending()
        assert "Say 'yes' to confirm" in reply1
        print("  [OK] Voice turn 1: Gated destructive command and requested confirmation")

        # Step 2: User says "yes" by voice
        cmd2 = "yes"
        reply2 = agent.process_command(cmd2, for_speech=True)
        assert not confirmation.is_pending()
        assert "closed that tab" in reply2.lower() or "tab" in reply2.lower() or "executed" in reply2.lower() or "pressed" in reply2.lower()
        assert env.gui.hotkeys == [("ctrl", "w")]
        print("  [OK] Voice turn 2: Confirmed via voice and executed tab close (ctrl+w)")


def test_voice_health_recalibration():
    print("\n--- 5. E2E Voice Health Degradation & Recalibrate ---")
    health_monitor.reset_voice_health()
    assert not health_monitor.needs_recalibrate()

    # Simulate 15 failures out of 20 ticks (75% error rate)
    for _ in range(5):
        health_monitor.record_voice_tick(ok=True)
    for _ in range(15):
        health_monitor.record_voice_tick(ok=False)

    assert health_monitor.needs_recalibrate()
    print("  [OK] Voice error accumulation triggered mic recalibration signal")

    # Flag clears on read
    assert not health_monitor.needs_recalibrate()
    print("  [OK] Recalibration flag cleared cleanly for next cycle")


if __name__ == "__main__":
    test_wake_word_and_aliases()
    test_direct_commands()
    test_cancel_phrases()
    test_voice_dialogue_with_confirmation()
    test_voice_health_recalibration()
    print("\nALL E2E VOICE TESTS PASSED")
