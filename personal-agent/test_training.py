import actions
import intent_resolver
"""
test_training.py

Unit and integration tests for Training Mode, runtime corrections,
cache replacement, and dataset logging.
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import trainer
import prompt_cache
import agent
from mock_win_api import MockWindowsEnvironment


def test_correction_phrase_extraction():
    print("\n--- 1. Correction Phrase Extraction ---")
    assert trainer.extract_correction_phrase("that was wrong, do open brave") == "open brave"
    assert trainer.extract_correction_phrase("no, I meant click submit") == "click submit"
    assert trainer.extract_correction_phrase("correct that to scroll down") == "scroll down"
    assert trainer.extract_correction_phrase("i meant volume up") == "volume up"
    assert trainer.extract_correction_phrase("open brave") is None
    print("  [OK] Successfully extracted target command from various correction prefixes")


def test_apply_correction_and_cache_update():
    print("\n--- 2. Apply Correction & Prompt Cache Replacement ---")
    test_prompt = "launch browser please"
    old_action = {"action": "open_app", "target": "notepad"}
    new_action = {"action": "open_app", "target": "brave"}

    # Seed bad entry
    prompt_cache.save_action(test_prompt, old_action)
    assert prompt_cache.get_cached_action(test_prompt) == old_action
    print("  [OK] Seeded initial incorrect cache entry")

    # Apply correction
    res = trainer.apply_correction(test_prompt, new_action, wrong_action=old_action)
    assert "Learned" in res

    # Verify cache was updated to new action
    cached = prompt_cache.get_cached_action(test_prompt)
    assert cached == new_action
    print("  [OK] Cache updated to corrected action")


def test_training_stats():
    print("\n--- 3. Training Stats ---")
    stats = trainer.get_training_stats()
    assert "dataset_corrections_count" in stats
    assert "prompt_cache_entries" in stats
    print(f"  [OK] Retrieved stats: {stats['dataset_corrections_count']} dataset records, {stats['prompt_cache_entries']} cache entries")


def test_end_to_end_runtime_correction():
    print("\n--- 4. End-to-End Runtime Correction in process_command ---")
    import confirmation, disambiguation, app_switch
    confirmation.clear()
    disambiguation.clear()
    app_switch.clear()
    with MockWindowsEnvironment():
        # Seed an initial command in cache
        prompt_cache.save_action("open editor", {"action": "open_app", "target": "notepad"})
        prompt_cache.save_action("open brave", {"action": "open_app", "target": "brave"})

        # Step 1: Execute initial command
        agent.process_command("open editor")
        last_prompt, last_act, _ = trainer.get_last_turn()
        assert last_prompt == "open editor", f"Expected 'open editor', got '{last_prompt}'"
        print("  [OK] Step 1 executed and turn recorded in trainer")

        # Step 2: Issue correction phrase
        correction_reply = agent.process_command("that was wrong, do open brave")
        assert "Learned:" in correction_reply
        assert "brave" in correction_reply.lower()

        # Step 3: Verify the original command "open editor" now maps to "open_app brave" in cache
        cached_action = prompt_cache.get_cached_action("open editor")
        assert cached_action == {"action": "open_app", "target": "brave"}
        print("  [OK] In-session correction updated prompt cache and executed immediately")


if __name__ == "__main__":
    test_correction_phrase_extraction()
    test_apply_correction_and_cache_update()
    test_training_stats()
    test_end_to_end_runtime_correction()
    print("\nALL TRAINING TESTS PASSED")
