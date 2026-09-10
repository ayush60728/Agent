"""
config.py

Central configuration for personal-agent.
Loads settings from settings.json on import with backward-compatible module
attributes (DRY_RUN, VERBOSITY, TTS_VOICE_ID, TTS_RATE, TTS_VOLUME,
TRAINING_MODE) so existing code that reads/writes these directly keeps working.

New code can use the structured API:
    config.get("model.temperature", 0)
    config.set("voice.tts_rate", 190)
    config.save()
    config.reload()
"""

import json
import sys
from pathlib import Path
from typing import Any, Optional

_DIR = Path(__file__).parent
_SETTINGS_FILE = _DIR / "settings.json"

# ── Default settings (used when settings.json is missing or incomplete) ──

_DEFAULTS: dict = {
    "model": {
        "name": "qwen3-nothink",
        "temperature": 0,
        "num_predict": 512,
        "num_ctx": 4096,
        "keep_alive": "30m",
    },
    "voice": {
        "tts_voice_id": None,
        "tts_rate": 175,
        "tts_volume": 1.0,
    },
    "safety": {
        "dry_run": False,
        "training_mode": False,
        "max_sequence_steps": 15,
    },
    "ux": {
        "verbosity": "terse",
    },
    "paths": {
        "prompt_cache": "prompt_cache.json",
        "app_paths": "app_paths.json",
        "folder_paths": "folder_paths.json",
        "screenshots_dir": "%USERPROFILE%\\Pictures",
    },
}

# Map legacy module attribute names → dotted settings keys.
# This lets existing code like ``config.DRY_RUN`` keep working.
_ATTR_MAP: dict[str, str] = {
    "DRY_RUN":       "safety.dry_run",
    "VERBOSITY":     "ux.verbosity",
    "TTS_VOICE_ID":  "voice.tts_voice_id",
    "TTS_RATE":      "voice.tts_rate",
    "TTS_VOLUME":    "voice.tts_volume",
    "TRAINING_MODE": "safety.training_mode",
}

# ── Internal helpers ──

_settings: dict = {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load() -> dict:
    """Load settings from disk, merging with defaults for any missing keys."""
    if _SETTINGS_FILE.exists():
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as fh:
                user_settings = json.load(fh)
            return _deep_merge(_DEFAULTS, user_settings)
        except (json.JSONDecodeError, OSError):
            pass
    # File missing or corrupt → use a deep copy of defaults
    return json.loads(json.dumps(_DEFAULTS))


def _sync_module_attrs() -> None:
    """Push current settings values into the backward-compatible module
    attributes so that ``config.DRY_RUN`` etc. reflect the latest state."""
    mod = sys.modules[__name__]
    for attr, key in _ATTR_MAP.items():
        val = get(key)
        # Use object.__setattr__ to bypass any future module wrapper
        object.__setattr__(mod, attr, val)


# ── Public API ──

def get(key: str, default: Any = None) -> Any:
    """Retrieve a setting by dotted key.

    >>> config.get("model.temperature", 0)
    0
    >>> config.get("voice.tts_rate")
    175
    """
    parts = key.split(".")
    current: Any = _settings
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def set(key: str, value: Any, *, persist: bool = True) -> None:
    """Update a setting by dotted key.

    Updates the in-memory dict **and** the corresponding legacy module
    attribute (if one exists). When *persist* is True (the default), the
    full settings dict is written to ``settings.json`` immediately.

    >>> config.set("voice.tts_rate", 190)
    """
    parts = key.split(".")
    current = _settings
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value

    # Keep the corresponding module-level attribute in sync.
    mod = sys.modules[__name__]
    for attr, mapped_key in _ATTR_MAP.items():
        if mapped_key == key:
            object.__setattr__(mod, attr, value)
            break

    if persist:
        save()


def save() -> None:
    """Write the current in-memory settings to ``settings.json``."""
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(_settings, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


def reload() -> None:
    """Re-read ``settings.json`` from disk and refresh module attributes."""
    global _settings
    _settings = _load()
    _sync_module_attrs()


def get_settings() -> dict:
    """Return a deep copy of the full settings dict."""
    return json.loads(json.dumps(_settings))


def get_settings_file() -> Path:
    """Return the path to the settings file."""
    return _SETTINGS_FILE


# ── Module-level attributes (backward compatibility) ──
# These are set once on import and updated by reload() / set().

DRY_RUN: bool = False
VERBOSITY: str = "terse"
TTS_VOICE_ID: Optional[str] = None
TTS_RATE: int = 175
TTS_VOLUME: float = 1.0
TRAINING_MODE: bool = False

# ── Initialize on import ──

_settings = _load()
_sync_module_attrs()
