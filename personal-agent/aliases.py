"""
aliases.py

Persistent user-defined shortcut/alias store for the personal agent.

Lets the user teach the agent personal shortcuts that are too specific
for the builtin table and too personal for the shared prompt cache:

    "my work folder"  → open_folder("C:/Work")
    "start my stack"  → sequence [open VS Code, open terminal, open Brave]
    "morning routine" → sequence [open Brave, open Spotify, play music]

Aliases are stored in aliases.json (same directory as the agent) and are
resolved BEFORE the LLM — instant, no network, no model load.

Public API
----------
    get_alias_action(prompt)        → action dict or None
    save_alias(name, action)        → None  (persists to disk)
    delete_alias(name)              → bool  (True if found and removed)
    list_aliases()                  → list[dict] sorted by name
    rename_alias(old, new)          → bool

Design follows prompt_cache.py conventions so the two modules feel
consistent: normalization via normalize_prompt(), JSON on disk, same
error-handling style.
"""

import json
from pathlib import Path
from datetime import date

from prompt_cache import normalize_prompt

_BASE_DIR = Path(__file__).resolve().parent
ALIASES_FILE = _BASE_DIR / "aliases.json"


# ── I/O helpers ────────────────────────────────────────────────────────────

def _load() -> dict:
    """Return the full aliases dict from disk, or {} on any failure."""
    if not ALIASES_FILE.exists():
        return {}
    try:
        data = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        ALIASES_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"⚠ Could not save aliases: {e}")


# ── public API ─────────────────────────────────────────────────────────────

def get_alias_action(prompt: str):
    """Return the action dict saved under this alias name, or None.

    Match is exact after normalize_prompt() — same strictness as builtins
    and the prompt cache.  A copy is returned so mutations don't corrupt
    the stored value.
    """
    key = normalize_prompt(prompt)
    if not key:
        return None
    data = _load()
    entry = data.get(key)
    if not entry:
        return None
    # Bump usage counter in-place (best-effort — don't crash on save error)
    entry["hit_count"] = entry.get("hit_count", 0) + 1
    entry["last_used"] = str(date.today())
    data[key] = entry
    _save(data)
    import copy
    return copy.deepcopy(entry.get("action"))


def save_alias(name: str, action) -> None:
    """Persist a new alias mapping name → action.

    action may be:
    - a single action dict  {"action": "open_app", "target": "brave"}
    - a sequence dict       {"action": "sequence", "steps": [...]}
    - a raw list of steps   [{"action": ...}, ...]  (normalized to sequence)

    An existing alias with the same name is silently overwritten.
    """
    key = normalize_prompt(name)
    if not key:
        print("⚠ Alias name is empty — not saved.")
        return

    # Normalize a bare list into a sequence dict so the rest of the pipeline
    # only ever sees one of the two canonical shapes.
    if isinstance(action, list):
        action = {"action": "sequence", "steps": action} if len(action) > 1 else action[0]

    data = _load()
    existing = data.get(key, {})
    data[key] = {
        "action":    action,
        "created":   existing.get("created", str(date.today())),
        "updated":   str(date.today()),
        "hit_count": existing.get("hit_count", 0),
        "last_used": existing.get("last_used"),
        # Store the human-readable name alongside the normalized key so
        # list_aliases() can display it nicely.
        "display_name": name.strip(),
    }
    _save(data)
    print(f"✓ Alias '{name}' saved.")


def delete_alias(name: str) -> bool:
    """Remove an alias by name. Returns True if it existed, False otherwise."""
    key = normalize_prompt(name)
    data = _load()
    if key in data:
        del data[key]
        _save(data)
        return True
    return False


def rename_alias(old_name: str, new_name: str) -> bool:
    """Rename an existing alias. Returns True on success, False if not found."""
    old_key = normalize_prompt(old_name)
    new_key = normalize_prompt(new_name)
    if not old_key or not new_key:
        return False
    data = _load()
    if old_key not in data:
        return False
    entry = data.pop(old_key)
    entry["display_name"] = new_name.strip()
    entry["updated"] = str(date.today())
    data[new_key] = entry
    _save(data)
    return True


def list_aliases() -> list:
    """Return a list of alias metadata dicts sorted alphabetically by display name.

    Each item contains: display_name, action, created, updated, hit_count.
    """
    data = _load()
    rows = []
    for key, entry in data.items():
        rows.append({
            "name":       entry.get("display_name", key),
            "action":     entry.get("action"),
            "created":    entry.get("created"),
            "updated":    entry.get("updated"),
            "hit_count":  entry.get("hit_count", 0),
            "last_used":  entry.get("last_used"),
        })
    return sorted(rows, key=lambda r: r["name"].lower())


def format_alias_list() -> str:
    """Return a printable table of all saved aliases, or a 'none yet' message."""
    rows = list_aliases()
    if not rows:
        return "No aliases saved yet. Use 'alias <name>' to save the last command as a shortcut."

    lines = ["Saved aliases:"]
    for r in rows:
        action = r["action"] or {}
        kind = action.get("action", "?")
        if kind == "sequence":
            steps = action.get("steps", [])
            detail = f"sequence ({len(steps)} steps)"
        else:
            detail = f"{kind}: {action.get('target', '')}"
        hits = r["hit_count"]
        lines.append(f"  • {r['name']!r:30s}  {detail}  (used {hits}×)")
    return "\n".join(lines)
