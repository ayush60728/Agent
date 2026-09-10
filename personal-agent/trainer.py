"""
trainer.py

Training Mode & Interactive Correction Engine for the Personal Desktop Agent.

Responsibilities:
    - Tracks recent prompt -> action pairs for runtime corrections.
    - Handles correction phrases: "that was wrong, do X", "no, I meant X", "correct that to X".
    - Purges bad prompt cache entries and saves corrected action mappings.
    - Records training pairs to training_dataset.jsonl for dataset logging and fine-tuning.
    - Provides interactive verification loop for agent.py --train mode.
"""

import json
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import prompt_cache
import config

TRAINING_DATASET_FILE = Path(__file__).parent / "training_dataset.jsonl"

# In-memory record of the most recent turn
_last_prompt: Optional[str] = None
_last_action: Optional[dict] = None
_last_result: Optional[str] = None


def record_turn(prompt: str, action: dict, result: str) -> None:
    """Record the latest executed command turn for possible subsequent correction."""
    global _last_prompt, _last_action, _last_result
    _last_prompt = prompt
    _last_action = action
    _last_result = result


def get_last_turn() -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    return _last_prompt, _last_action, _last_result


def log_training_correction(
    original_prompt: str,
    wrong_action: Optional[dict],
    corrected_action: dict,
    notes: str = "",
) -> None:
    """Append a training correction record to training_dataset.jsonl."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prompt": original_prompt,
        "wrong_action": wrong_action,
        "corrected_action": corrected_action,
        "notes": notes,
    }
    try:
        with open(TRAINING_DATASET_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"⚠ Could not write to training dataset: {e}")


def apply_correction(
    original_prompt: str,
    corrected_action: dict,
    wrong_action: Optional[dict] = None,
    notes: str = "User runtime correction",
) -> str:
    """
    Apply a user correction:
    1. Forget the old mapping from prompt_cache.
    2. Save the corrected mapping into prompt_cache.
    3. Log the correction pair to training_dataset.jsonl.
    """
    # 1. Purge old cache entry
    prompt_cache.forget_prompt(original_prompt)

    # 2. Save new correct mapping
    prompt_cache.save_action(original_prompt, corrected_action)

    # 3. Log to dataset
    log_training_correction(
        original_prompt=original_prompt,
        wrong_action=wrong_action,
        corrected_action=corrected_action,
        notes=notes,
    )

    return f"Learned: '{original_prompt}' -> {corrected_action.get('action')}"


def extract_correction_phrase(user_input: str) -> Optional[str]:
    """
    Check if user_input is a correction command and extract the intended new command.

    Examples:
      - "that was wrong, do open brave" -> "open brave"
      - "no, I meant click submit" -> "click submit"
      - "correct that to press enter" -> "press enter"
      - "teach: open site => open_app brave" -> handled separately
    """
    text = user_input.strip().lower()

    triggers = [
        "that was wrong, do ",
        "that was wrong do ",
        "that's wrong, do ",
        "thats wrong do ",
        "wrong, do ",
        "no, i meant ",
        "no i meant ",
        "no, meant ",
        "correct that to ",
        "correct to ",
        "i meant ",
    ]

    for trigger in triggers:
        if text.startswith(trigger):
            return user_input.strip()[len(trigger):].strip()

    return None


def get_training_stats() -> Dict[str, Any]:
    """Return count of training dataset entries and prompt cache size."""
    dataset_count = 0
    if TRAINING_DATASET_FILE.exists():
        try:
            with open(TRAINING_DATASET_FILE, "r", encoding="utf-8") as f:
                dataset_count = sum(1 for _ in f if _.strip())
        except OSError:
            pass

    cache_size = len(prompt_cache._load_cache())

    return {
        "dataset_corrections_count": dataset_count,
        "prompt_cache_entries": cache_size,
        "training_mode_active": getattr(config, "TRAINING_MODE", False),
    }
