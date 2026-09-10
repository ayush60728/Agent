"""
context_memory.py

Short-term in-session memory for the personal agent.

Tracks the most recently used values for each class of action so the
agent can resolve pronouns and relative references without calling the
LLM a second time:

    "open brave … now go to youtube"   → last_opened_app = "brave"
    "type hello … type that again"     → last_typed_text = "hello"
    "click submit … click it again"    → last_clicked_element = "submit"
    "close it"                         → resolves to last_opened_app or
                                          last_focused_app

Design principles
-----------------
- In-memory first: the dict is updated on every successful action, so
  intra-session recall is instant with no I/O on the hot path.
- Best-effort persistence: on every update the dict is written to
  short_term_memory.json so memory survives a crash or restart.
  Failures are logged but never raise — memory loss beats a crash.
- No secrets: only action metadata (names, text strings the user
  already spoke aloud) is stored, never credentials or screen content.
- Single source of truth: every part of the agent imports from here;
  nobody maintains their own "last app" variable.
"""

import json
import time
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_MEMORY_FILE = _BASE_DIR / "short_term_memory.json"

# ── in-memory store ────────────────────────────────────────────────────────
# Keys are intentionally stable strings so the rest of the code can rely on
# them without fragile positional indexing.
_MEMORY: dict = {
    "last_opened_app":       None,   # str — name of the last app opened
    "last_focused_app":      None,   # str — last app explicitly focused
    "last_typed_text":       None,   # str — last text sent via type_text
    "last_clicked_element":  None,   # str — OCR label of the last click
    "last_action":           None,   # dict — full action dict most recently run
    "last_search_query":     None,   # str — last search term typed + submitted
    "updated_at":            None,   # float — unix timestamp of last write
}


# ── persistence helpers ────────────────────────────────────────────────────

def _load() -> None:
    """Read short_term_memory.json into _MEMORY at import time.
    Silent on missing / malformed file — we just start with a blank slate."""
    if not _MEMORY_FILE.exists():
        return
    try:
        data = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in _MEMORY:
                if k in data:
                    _MEMORY[k] = data[k]
    except (OSError, json.JSONDecodeError):
        pass


def _save() -> None:
    """Write the current _MEMORY dict to disk. Best-effort — never raises."""
    try:
        _MEMORY_FILE.write_text(
            json.dumps(_MEMORY, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"(context_memory: couldn't persist memory: {e})")


# ── public API ─────────────────────────────────────────────────────────────

def get(key: str):
    """Return the current value for a memory key, or None if unset."""
    return _MEMORY.get(key)


def get_all() -> dict:
    """Return a shallow copy of the entire memory dict."""
    return dict(_MEMORY)


def update(**kwargs) -> None:
    """Set one or more memory keys and persist to disk.

    Usage:
        context_memory.update(last_opened_app="brave")
        context_memory.update(last_typed_text="cats", last_action={...})
    """
    changed = False
    for k, v in kwargs.items():
        if k in _MEMORY:
            _MEMORY[k] = v
            changed = True
        else:
            # Warn on unknown keys so typos surface at dev time.
            print(f"(context_memory: unknown key '{k}' — ignored)")
    if changed:
        _MEMORY["updated_at"] = time.time()
        _save()


def clear() -> None:
    """Wipe all memory (useful for testing or an explicit 'forget everything')."""
    for k in list(_MEMORY.keys()):
        _MEMORY[k] = None
    _save()


def summary() -> str:
    """Return a short human-readable summary of what the agent remembers.

    Used by the 'what do you remember?' builtin and injected into the LLM
    context line when pronoun-resolution enrichment is active.

    Example output:
        Last app: brave | Last typed: youtube.com | Last click: search
    """
    parts = []
    if _MEMORY["last_opened_app"]:
        parts.append(f"last app: {_MEMORY['last_opened_app']}")
    if _MEMORY["last_focused_app"] and _MEMORY["last_focused_app"] != _MEMORY["last_opened_app"]:
        parts.append(f"focused: {_MEMORY['last_focused_app']}")
    if _MEMORY["last_typed_text"]:
        preview = _MEMORY["last_typed_text"][:40]
        if len(_MEMORY["last_typed_text"]) > 40:
            preview += "…"
        parts.append(f"last typed: {preview}")
    if _MEMORY["last_clicked_element"]:
        parts.append(f"last click: {_MEMORY['last_clicked_element']}")
    if _MEMORY["last_search_query"]:
        parts.append(f"last search: {_MEMORY['last_search_query']}")
    return " | ".join(parts) if parts else "nothing remembered yet"


def active_app() -> str | None:
    """Best-guess at the currently relevant app.

    Returns the most recently opened or focused app name, preferring the
    one that was most recently set (i.e. whichever update happened last).
    In practice the caller just wants "what should 'it' resolve to?" and
    either field answers that question equally well.
    """
    # Both can be None; if one is set return it; if both are set, last_focused_app
    # wins because focus is always *at least* as recent as open (open_app calls
    # focus_app itself right after launching).
    return _MEMORY["last_focused_app"] or _MEMORY["last_opened_app"]


# ── pronoun resolution ─────────────────────────────────────────────────────
# These helpers let the agent pre-process a command before it reaches the
# LLM, turning vague references into concrete ones.  They are intentionally
# conservative: if the memory slot is empty they return the original text
# unchanged so the LLM can still try.

_CLOSE_IT_PATTERNS = frozenset({
    "close it", "close that", "close this", "quit it", "quit that",
    "exit it", "exit that", "kill it", "kill that",
})
_OPEN_IT_AGAIN_PATTERNS = frozenset({
    "open it again", "reopen it", "launch it again", "start it again",
    "open that again", "reopen that",
})
_TYPE_AGAIN_PATTERNS = frozenset({
    "type that again", "retype that", "type it again",
    "type the same thing", "repeat that",
})
_CLICK_AGAIN_PATTERNS = frozenset({
    "click it again", "click that again", "click the same thing",
})
_SEARCH_AGAIN_PATTERNS = frozenset({
    "search that again", "search for it again", "search again",
})


def resolve_pronouns(text: str) -> str:
    """Replace pronoun phrases with the concrete value from memory.

    Called at the very start of process_command, before builtin lookup or
    LLM, so that downstream logic never has to know about pronouns.

    Returns the original text unchanged when:
    - no pronoun pattern is detected, OR
    - the relevant memory slot is empty (we can't resolve — let LLM try)
    """
    from prompt_cache import normalize_prompt   # local import avoids circular deps
    norm = normalize_prompt(text)

    # "close it" → "close brave"
    if norm in _CLOSE_IT_PATTERNS:
        app = active_app()
        if app:
            return f"close {app}"

    # "open it again" → "open brave"
    if norm in _OPEN_IT_AGAIN_PATTERNS:
        app = _MEMORY["last_opened_app"]
        if app:
            return f"open {app}"

    # "type that again" → "type youtube.com"
    if norm in _TYPE_AGAIN_PATTERNS:
        t = _MEMORY["last_typed_text"]
        if t:
            return f"type {t}"

    # "click it again" → "click submit"
    if norm in _CLICK_AGAIN_PATTERNS:
        el = _MEMORY["last_clicked_element"]
        if el:
            return f"click {el}"

    # "search again" → "search for cats"
    if norm in _SEARCH_AGAIN_PATTERNS:
        q = _MEMORY["last_search_query"]
        if q:
            return f"search for {q}"

    # Generic "focus it" / "switch to it" — resolve to active app
    if norm in {"focus it", "switch to it", "go back to it", "go to it"}:
        app = active_app()
        if app:
            return f"focus {app}"

    return text   # nothing resolved — pass through unchanged


# ── module init ────────────────────────────────────────────────────────────
_load()
