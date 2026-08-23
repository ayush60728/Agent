"""
prompt_cache.py

Caches the mapping from a user's sentence -> Qwen's decoded action, so
repeating the same command doesn't need a fresh LLM call every time.

Deliberately STRICT matching (exact, after light normalization) rather
than fuzzy — a wrong cache hit here means silently executing the wrong
action, which is worse than just being slightly slower on a rare phrasing
that hasn't been seen before.
"""

import re
import json
from pathlib import Path
from datetime import date


CACHE_FILE = Path(__file__).parent / "prompt_cache.json"


def normalize_prompt(text: str) -> str:
    """Light normalization only — lowercase, trim, collapse whitespace, and
    strip surrounding punctuation. No fuzzy matching, no filler-word
    stripping: we want this cache to only hit on genuinely repeated phrasing,
    not guess at intent.

    Stripping surrounding punctuation matters because voice transcripts arrive
    with trailing "." / "?" ("open brave." vs the typed "open brave"), which
    would otherwise split the cache into duplicate entries and drop the hit
    rate. Only the ends are trimmed — internal punctuation like the "." in
    "type youtube.com" is preserved."""
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text.strip(" .,!?;:\"'()[]")


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4)
    except OSError as e:
        print(f"⚠ Could not save prompt cache: {e}")


def get_cached_action(prompt: str):
    """Return the cached action dict for this exact (normalized) prompt,
    or None if it hasn't been seen before."""
    key = normalize_prompt(prompt)
    cache = _load_cache()

    entry = cache.get(key)
    if not entry:
        return None

    entry["hit_count"] = entry.get("hit_count", 0) + 1
    cache[key] = entry
    _save_cache(cache)

    return entry.get("action")


def save_action(prompt: str, action: dict) -> None:
    """Remember what Qwen decided for this prompt, for next time."""
    key = normalize_prompt(prompt)
    cache = _load_cache()

    cache[key] = {
        "action": action,
        "last_used": str(date.today()),
        "hit_count": cache.get(key, {}).get("hit_count", 0),
    }
    _save_cache(cache)


def forget_prompt(prompt: str) -> None:
    """Remove a cached prompt (e.g. if Qwen classified it wrong once)."""
    key = normalize_prompt(prompt)
    cache = _load_cache()
    if key in cache:
        del cache[key]
        _save_cache(cache)
        print(f"🗑 Removed cached response for '{prompt}'")
    else:
        print(f"'{prompt}' was not in the prompt cache")