# Design Document

## Overview

This design covers four security and safety improvements to the Windows desktop AI agent:

1. **Extended Confirmation** — a configuration-driven policy replaces the hard-coded `close_app` / `press_key` destructive-combo check in `confirmation.py`. Policy lives in a new `security_policy.py` module. No changes to `agent.py`'s logic structure are required to add new action types to the gate.

2. **File Sandbox** — a new `sandbox.py` module enforces an allowlist of permitted directories for all file-path operations. `open_folder` (and any future file-write/delete actions) routes its resolved path through `sandbox.check_path()` before `os.startfile` is called.

3. **Undo Stack** — a new `undo_stack.py` module maintains a bounded, session-only deque of completed reversible actions. The user can say "undo" to reverse the last reversible action. The intercept happens before the LLM/cache/builtin pipeline in `process_command`.

4. **Type Text Sanitization** — `actions.type_text()` gains a sanitization check before calling `_type_text`. Control characters (C0, DEL, C1) are detected and the call is refused in full with a message naming every offending character by its Unicode code point and name.

All four features are enforced **after** `validate_action` and **before** `execute_action` in `agent.process_command`, and where applicable inside `actions.py` for deep enforcement.

---

## Architecture

### Existing pipeline

```
voice_io / text input
        │
        ▼
agent.process_command()
  ├─ blank-input guard
  ├─ [NEW] undo intercept  ◄── undo_stack.py
  ├─ app_switch pending?
  ├─ confirmation pending?
  ├─ disambiguation pending?
  ├─ builtins / smart builtins
  ├─ prompt cache
  ├─ Qwen LLM
  ├─ normalize_action / validate_action
  ├─ [EXTENDED] confirmation gate  ◄── confirmation.py → security_policy.py
  ├─ _dispatch / execute_action
  │     └─ actions.py
  │           ├─ type_text  → [NEW] sanitization check (_sanitize_text)
  │           └─ open_folder → [NEW] sandbox.check_path()  ◄── sandbox.py
  └─ [NEW] undo stack push  ◄── undo_stack.py
```

### New / changed files

| File | Status | Role |
|---|---|---|
| `security_policy.py` | **New** | Confirmation policy config + `needs_confirmation` / `describe_action` logic |
| `sandbox.py` | **New** | Allowed-directory allowlist + `check_path` |
| `undo_stack.py` | **New** | Bounded undo deque + `make_record` / `reverse` |
| `confirmation.py` | **Extended** | `needs_confirmation` and `describe` delegate to `security_policy` |
| `actions.py` | **Extended** | `type_text` sanitization; `open_folder` sandbox check |
| `agent.py` | **Extended** | Three surgical insertions (undo intercept, undo push, imports + startup log) |

---

## Components and Interfaces

### Module: security_policy.py (new)

This module is the single source of truth for what requires confirmation and how to describe it. `confirmation.py` delegates to it; nothing else in the existing pipeline needs to change.

```python
"""
security_policy.py

Configuration-driven confirmation policy for the desktop agent.

Defines which action types and key combos require a yes/no confirmation
before executing, at three levels:
    "always"         — always prompt
    "never"          — always run instantly
    "if_destructive" — current close_app / destructive-combo behaviour

All confirmation logic that previously lived inline in confirmation.py
now lives here so adding a new action to the gate requires only editing
DEFAULT_POLICY — no changes to confirmation.py, actions.py, or agent.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConfirmationPolicy:
    """
    action_type_policy maps an action type string to one of:
        "always"         — gate this action unconditionally
        "never"          — never gate this action
        "if_destructive" — gate only when the action meets the destructive test
                           (close_app always qualifies; press_key qualifies when
                           its combo is in system_key_combos OR in the legacy
                           _DESTRUCTIVE_COMBOS set imported from confirmation.py)

    system_key_combos maps a frozenset of key-name strings to a plain-language
    description of what that combo does, used in the confirmation prompt.
    """
    action_type_policy: dict[str, str] = field(default_factory=dict)
    system_key_combos: dict[frozenset, str] = field(default_factory=dict)


# frozensets are not hashable as dict keys directly when used as values in a
# dataclass default_factory, so we build DEFAULT_POLICY after the class.

DEFAULT_POLICY = ConfirmationPolicy(
    action_type_policy={
        # --- actions that use if_destructive logic ---
        "close_app":          "if_destructive",
        "press_key":          "if_destructive",  # combo-checked in needs_confirmation

        # --- actions that default to never (safe / read-only) ---
        "open_app":           "never",
        "open_folder":        "never",
        "focus_app":          "never",
        "click_text":         "never",
        "right_click_text":   "never",
        "type_text":          "never",
        "scroll":             "never",
        "screenshot":         "never",
        "wait":               "never",
        "get_color":          "never",
        "switch_app_picker":  "never",
    },
    system_key_combos={
        frozenset({"win", "l"}):              "lock the screen",
        frozenset({"ctrl", "shift", "esc"}):  "open Task Manager",
    },
)


# The destructive combos originally defined in confirmation.py are kept here
# as well so needs_confirmation can check them under "if_destructive" for
# press_key. confirmation.py's _DESTRUCTIVE_COMBOS is preserved for backward
# compatibility but no longer drives the logic.
_DESTRUCTIVE_COMBOS: set[frozenset] = {
    frozenset({"alt", "f4"}),
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "f4"}),
    frozenset({"ctrl", "shift", "w"}),
    frozenset({"ctrl", "q"}),
    frozenset({"ctrl", "shift", "q"}),
}


def _combo_parts(target: str) -> frozenset:
    """Normalise a combo string to a frozenset of lowercase key names."""
    return frozenset(p.strip().lower() for p in str(target or "").split("+") if p.strip())


def needs_confirmation(action: dict, policy: ConfirmationPolicy) -> bool:
    """
    Return True if this action requires a yes/no confirmation before running.

    Handles all policy levels:
        "always"         -> True unconditionally
        "never"          -> False unconditionally
        "if_destructive" -> True when the action meets its own destructive test

    Sequences are handled recursively: a sequence needs confirmation if ANY
    of its steps does (the whole batch is confirmed up front).
    """
    if not isinstance(action, dict):
        return False

    kind = action.get("action")

    if kind == "sequence":
        return any(needs_confirmation(s, policy) for s in action.get("steps", []))

    level = policy.action_type_policy.get(kind, "never")

    if level == "always":
        return True
    if level == "never":
        return False
    # "if_destructive"
    if kind == "close_app":
        return True
    if kind == "press_key":
        parts = _combo_parts(action.get("target", ""))
        # System-affecting combos (lock screen, Task Manager, …)
        if parts in policy.system_key_combos:
            return True
        # Legacy destructive combos (close window/tab/app)
        if parts in _DESTRUCTIVE_COMBOS:
            return True
    return False


def describe_action(action: dict, policy: ConfirmationPolicy) -> str:
    """
    Return a short plain-language phrase describing what the action will do,
    for use in the confirmation prompt ("That will <describe_action>. Say
    'yes' to confirm…").
    """
    if not isinstance(action, dict):
        return "do that"

    kind = action.get("action")

    if kind == "sequence":
        steps = action.get("steps", [])
        n = len(steps)
        destructive = [describe_action(s, policy) for s in steps
                       if needs_confirmation(s, policy)]
        if len(destructive) == 1:
            return f"run {n} steps, including one that will {destructive[0]}"
        if destructive:
            return (f"run {n} steps, including {len(destructive)} that will: "
                    + ", ".join(destructive))
        return f"run {n} steps"

    if kind == "close_app":
        target = (action.get("target") or "").strip()
        return f"close {target}" if target else "close the current window"

    if kind == "press_key":
        parts = _combo_parts(action.get("target", ""))
        # System combos get their registered description
        desc = policy.system_key_combos.get(parts)
        if desc:
            return desc
        # Legacy destructive combos
        if parts in (frozenset({"ctrl", "w"}), frozenset({"ctrl", "f4"})):
            return "close this tab"
        if parts in (frozenset({"alt", "f4"}), frozenset({"ctrl", "shift", "w"})):
            return "close this window"
        if parts in (frozenset({"ctrl", "q"}), frozenset({"ctrl", "shift", "q"})):
            return "quit this app"
        return f"press {action.get('target')}"

    return str(kind)


def log_active_policy(policy: ConfirmationPolicy) -> None:
    """
    Log which action types have the 'always' policy active at startup,
    so the user can verify what is and isn't gated (requirement 1.9).
    """
    always_types = [k for k, v in policy.action_type_policy.items() if v == "always"]
    system_combos = [
        "+".join(sorted(parts)) for parts in policy.system_key_combos
    ]
    if always_types:
        print(f"[security_policy] Confirmation ALWAYS required for: {', '.join(sorted(always_types))}")
    else:
        print("[security_policy] No action types set to 'always' confirmation.")
    if system_combos:
        print(f"[security_policy] System key combos requiring confirmation: {', '.join(sorted(system_combos))}")
```

---

### Module: sandbox.py (new)

```python
"""
sandbox.py

File-path sandbox for the desktop agent.

Enforces an allowlist of permitted directory roots for file-path operations
(currently open_folder; future save_file / delete_file / move_file).

Path traversal attacks ("../../Windows") are neutralised by Path.resolve(),
which collapses all ".." components against the filesystem before the
allowlist comparison is made.

Case-insensitive on Windows: all comparisons use lower-cased string
representations of the resolved paths so that "C:\\" and "c:\\" are
treated identically (requirement 2.7).
"""

from __future__ import annotations

import os
from pathlib import Path


def _expand(raw: str) -> Path:
    """Expand environment variables and return an absolute Path."""
    return Path(os.path.expandvars(raw)).resolve()


# Built once at import time from the current user's environment.
# Adding a new allowed directory requires only adding an entry here
# (requirement 2.5).
ALLOWED_DIRS: list[Path] = [
    _expand(r"%USERPROFILE%"),
    _expand(r"%USERPROFILE%\Desktop"),
    _expand(r"%USERPROFILE%\Documents"),
    _expand(r"%USERPROFILE%\Downloads"),
    _expand(r"%USERPROFILE%\Pictures"),
    _expand(r"%USERPROFILE%\Music"),
    _expand(r"%USERPROFILE%\Videos"),
]


def check_path(path_str: str) -> tuple[bool, str]:
    """
    Check whether path_str is within the allowed-directory allowlist.

    Resolution order:
        1. Expand environment variables.
        2. If the path is relative, resolve it against the user's home
           directory (requirement 2.3).
        3. Call Path.resolve() to canonicalise the path and collapse any
           '..' components (path-traversal defence).
        4. Compare case-insensitively against every allowed directory
           (requirement 2.7).

    Returns:
        (True, "")                    — path is within the allowlist
        (False, "Blocked: <path> …")  — path is outside the allowlist
    """
    try:
        raw = Path(os.path.expandvars(path_str))
        if not raw.is_absolute():
            raw = Path.home() / raw
        resolved = raw.resolve()
    except (OSError, ValueError) as exc:
        return False, f"Blocked: could not resolve path '{path_str}': {exc}"

    resolved_lower = str(resolved).lower()

    for allowed in ALLOWED_DIRS:
        allowed_lower = str(allowed).lower()
        # Equal to the allowed dir, or a subdirectory of it (requirement 2.6).
        # The trailing separator prevents "C:\UsersExtra" from matching "C:\Users".
        if resolved_lower == allowed_lower or resolved_lower.startswith(allowed_lower + os.sep):
            return True, ""

    return False, f"Blocked: '{resolved}' is outside the allowed directories."
```

### Path-traversal example

```
path_str = r"..\..\Windows\System32"
raw      = Path.home() / r"..\..\Windows\System32"
resolved = Path("C:\\Windows\\System32")   # after Path.resolve()
# "c:\\windows\\system32" does not start with any ALLOWED_DIRS entry → blocked
```

---

### Module: undo_stack.py (new)

```python
"""
undo_stack.py

In-session, bounded undo history for the desktop agent.

Design notes:
  - collections.deque with maxlen=MAX_STACK handles the bounded-push
    automatically: appending to a full deque silently drops the oldest
    entry, so no manual trimming is needed (requirement 3.2).
  - The stack is never written to disk (requirement 3.7).
  - reverse() for close_window attempts open_app before falling back to the
    informational message (requirement 3.9).
  - UNDO_PHRASES is checked against normalize_prompt() output so
    punctuation/case variants ("Undo that." == "undo that").
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field


MAX_STACK = 20

# Phrases (already normalized via prompt_cache.normalize_prompt) that
# should trigger the undo handler instead of the normal pipeline.
UNDO_PHRASES: set[str] = {
    "undo",
    "undo that",
    "undo last action",
    "revert",
}


@dataclass
class UndoRecord:
    action_type: str          # "close_tab", "close_window", "type_text", "screenshot"
    target: str               # the original target string (text typed, app name, …)
    description: str          # human-readable, e.g. "typed 'hello world'"
    timestamp: float          # time.time() at push
    can_reverse: bool         # True if a compensating action exists
    extra: dict = field(default_factory=dict)
    # extra keys used per type:
    #   close_window  -> {"window_title": str}   (app name to attempt re-open)
    #   screenshot    -> {"path": str}            (saved file path)
    #   compound      -> {"steps": list[UndoRecord]}  (for sequence undo, req 3.5)


# Module-level stack — session only, never persisted.
_stack: collections.deque[UndoRecord] = collections.deque(maxlen=MAX_STACK)


def push(record: UndoRecord) -> None:
    """Push a reversible action record onto the stack (requirement 3.1)."""
    _stack.append(record)


def pop() -> UndoRecord | None:
    """Pop and return the top record, or None if the stack is empty."""
    return _stack.pop() if _stack else None


def is_empty() -> bool:
    return len(_stack) == 0


# Key combos that map to close_tab (reversible via ctrl+shift+t)
_CLOSE_TAB_COMBOS: set[frozenset] = {
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "f4"}),
}

# Key combos that map to close_window (not mechanically reversible)
_CLOSE_WINDOW_COMBOS: set[frozenset] = {
    frozenset({"alt", "f4"}),
    frozenset({"ctrl", "shift", "w"}),
}


def _combo_parts(target: str) -> frozenset:
    return frozenset(p.strip().lower() for p in str(target or "").split("+") if p.strip())


def make_record(action: dict, result: str) -> UndoRecord | None:
    """
    Inspect a completed action and build an UndoRecord for trackable types.
    Returns None for non-trackable actions (requirement 3.6).

    Trackable types (requirement 3.1):
        press_key ctrl+w / ctrl+f4  → close_tab
        press_key alt+f4            → close_window
        type_text                   → type_text
        screenshot                  → screenshot

    For sequences (requirement 3.5): the caller in agent.py unwraps the
    sequence and calls make_record on each individual step; make_record
    itself only handles single actions. The caller is responsible for
    wrapping the per-step records into a compound UndoRecord if needed.
    """
    if not isinstance(action, dict):
        return None

    kind = action.get("action")
    target = str(action.get("target") or "")

    if kind == "press_key":
        parts = _combo_parts(target)
        if parts in _CLOSE_TAB_COMBOS:
            return UndoRecord(
                action_type="close_tab",
                target=target,
                description="closed a tab",
                timestamp=time.time(),
                can_reverse=True,
            )
        if parts in _CLOSE_WINDOW_COMBOS:
            # Try to extract a window title from the result string if available.
            # execute_action returns e.g. "closed brave" — parse out the app name.
            window_title = ""
            if isinstance(result, str) and result.lower().startswith("closed "):
                window_title = result[len("closed "):].strip()
            return UndoRecord(
                action_type="close_window",
                target=target,
                description=f"closed window{': ' + window_title if window_title else ''}",
                timestamp=time.time(),
                can_reverse=False,
                extra={"window_title": window_title},
            )
        return None

    if kind == "type_text":
        return UndoRecord(
            action_type="type_text",
            target=target,
            description=f"typed '{target}'",
            timestamp=time.time(),
            can_reverse=True,
        )

    if kind == "screenshot":
        # extract path from result like "saved screenshot to C:\...\file.png"
        path = ""
        if isinstance(result, str) and "saved screenshot to " in result.lower():
            path = result.split("saved screenshot to ", 1)[-1].strip()
        return UndoRecord(
            action_type="screenshot",
            target=target,
            description="took a screenshot",
            timestamp=time.time(),
            can_reverse=False,
            extra={"path": path},
        )

    return None


def reverse(record: UndoRecord) -> str:
    """
    Attempt to reverse the action described by record (requirement 3.3).

    close_tab    → press ctrl+shift+t
    close_window → attempt open_app by window title (req 3.9), else report
    type_text    → press ctrl+z up to 3 times
    screenshot   → report path (cannot delete automatically)
    compound     → reverse each step in reverse order
    """
    # Compound record (from a sequence undo, requirement 3.5)
    if record.action_type == "compound":
        steps: list[UndoRecord] = record.extra.get("steps", [])
        results = []
        for step in reversed(steps):
            results.append(reverse(step))
        return " | ".join(results) if results else "Nothing to reverse."

    if record.action_type == "close_tab":
        try:
            import desktop_actions
            desktop_actions.press_key("ctrl+shift+t")
        except Exception as e:
            return f"Tried to reopen the tab but got an error: {e}"
        return "Reopened the closed tab."

    if record.action_type == "close_window":
        window_title = record.extra.get("window_title", "").strip()
        if window_title:
            try:
                import actions as _actions
                result = _actions.open_app(window_title)
                if "couldn't" not in result.lower() and "error" not in result.lower():
                    return f"Reopened '{window_title}'."
            except Exception:
                pass
        fallback = (f"I can't reopen a closed window, but I've recorded that "
                    f"'{window_title}' was closed." if window_title
                    else "I can't reopen a closed window.")
        return fallback

    if record.action_type == "type_text":
        try:
            import desktop_actions
            for _ in range(3):
                desktop_actions.press_key("ctrl+z")
        except Exception as e:
            return f"Tried to undo typing but got an error: {e}"
        return "Sent 3 Ctrl+Z presses to undo the typed text."

    if record.action_type == "screenshot":
        path = record.extra.get("path", "")
        if path:
            return f"The screenshot was saved at '{path}'. I can't delete it automatically."
        return "The screenshot was taken. I can't delete it automatically."

    return f"I don't know how to reverse '{record.action_type}'."
```

---

### Changes to confirmation.py

Only two functions change. Everything else — `arm`, `take`, `clear`, `is_pending`, `interpret`, `recovery_hint`, `_pending`, `CONFIRM_TIMEOUT_S`, `_DESTRUCTIVE_COMBOS`, `_REOPENABLE_COMBOS` — stays **identical**.

Add import at the top of `confirmation.py`:

```python
import security_policy as _sp
```

### Replacement `needs_confirmation`

```python
def needs_confirmation(action) -> bool:
    """True if this action should be confirmed before it runs.
    Delegates to security_policy.needs_confirmation() so the policy is
    configuration-driven (requirement 1.1)."""
    return _sp.needs_confirmation(action, _sp.DEFAULT_POLICY)
```

### Replacement `describe`

```python
def describe(action) -> str:
    """A short human phrase for what the action will do.
    Delegates to security_policy.describe_action() (requirement 1.1)."""
    return _sp.describe_action(action, _sp.DEFAULT_POLICY)
```

`prompt_for`, `recovery_hint`, and all state management functions are unchanged.

---

### Changes to actions.py

Two functions gain new guards. No other changes.

### 7a. `type_text` — sanitization (requirements 4.1–4.8)

Add `import unicodedata` at the top of `actions.py`.

Add the helper function (module-level, not exported):

```python
def _sanitize_text(text: str) -> tuple[bool, str]:
    """
    Inspect `text` for characters that must not be sent to pyautogui.write().

    Prohibited:
        - Empty or whitespace-only strings (after stripping tab U+0009)
        - C0 controls U+0000–U+001F, EXCEPT tab U+0009
        - DEL U+007F
        - C1 controls U+0080–U+009F
        - Newline U+000A gets its own specific message

    Returns (True, "") when clean, (False, error_message) when not.
    """
    if not text or not text.strip("\t"):
        return False, "Refused: text is empty or contains only whitespace."

    bad: list[str] = []
    has_newline = False

    for ch in text:
        cp = ord(ch)
        if cp == 0x0A:  # newline — special message
            has_newline = True
        elif cp == 0x09:  # tab — permitted
            continue
        elif cp < 0x20 or cp == 0x7F or (0x80 <= cp <= 0x9F):
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "UNKNOWN"
            bad.append(f"U+{cp:04X} ({name})")

    if has_newline:
        return False, "Use 'press enter' to send a newline instead of typing it."

    if bad:
        return False, f"Refused: text contains control characters: {', '.join(bad)}."

    return True, ""
```

Replace the `type_text` function body:

```python
def type_text(text: str) -> str:
    """Type a string via simulated keyboard input. Refuses if the focused
    app has been closed, or if the text contains prohibited control characters
    (requirements 4.1–4.8)."""

    if not text:
        return "I need some text to type."

    ok, err = _sanitize_text(text)
    if not ok:
        return err

    error = _ensure_focused_app_active()
    if error:
        return error

    return _type_text(text)
```

### 7b. `open_folder` — sandbox check (requirements 2.1–2.7)

Add `import sandbox` at the top of `actions.py`.

Insert the sandbox check inside `open_folder`, immediately after `find_folder` returns a non-None path and before `os.startfile`:

```python
def open_folder(folder_name: str) -> str:
    """Resolve and open a folder in File Explorer.
    Blocked if the resolved path is outside the allowed-directory allowlist
    (requirements 2.2, 2.3, 2.7)."""

    if not folder_name:
        return "I need a folder name to open."

    path = find_folder(folder_name)

    if not path:
        return f"I couldn't find a folder called {folder_name} on this computer."

    # Sandbox check — must pass before os.startfile (requirement 2.2)
    allowed, err = sandbox.check_path(path)
    if not allowed:
        return err

    try:
        os.startfile(path)
        return f"Opened {folder_name}."

    except (FileNotFoundError, OSError):
        path = find_folder(folder_name, force_rescan=True)

        if not path:
            return f"I couldn't find a folder called {folder_name} on this computer."

        # Re-check the rescanned path through the sandbox as well.
        allowed, err = sandbox.check_path(path)
        if not allowed:
            return err

        try:
            os.startfile(path)
            return f"Opened {folder_name}."
        except OSError as e:
            return f"I found {folder_name}, but couldn't open it: {e}"
```

---

### Changes to agent.py

Three surgical insertions only. No restructuring of `process_command`.

### Insertion A — undo intercept

Location: immediately after the blank-input guard (`if not normalize_prompt(user_input): ...`) and before the `app_switch.is_pending()` check.

```python
# 0-pre. Undo command — intercept before any other pipeline stage
# (requirement 3.8) so "undo" is never sent to builtins, cache, or Qwen.
if normalize_prompt(user_input) in undo_stack.UNDO_PHRASES \
        and not confirmation.is_pending() \
        and not disambiguation.is_pending():
    record = undo_stack.pop()
    _pet("idle")
    if record is None:
        return "There's nothing to undo."
    return undo_stack.reverse(record)
```

### Insertion B — undo stack push

Location: after `result = _dispatch(action, for_speech=for_speech)` and before the `switch_app_picker` check.

```python
# Push a record for reversible actions (requirements 3.1, 3.5).
if is_sequence(action):
    # For sequences, collect per-step records and push a compound record.
    seq_records = [
        undo_stack.make_record(step, "")
        for step in action.get("steps", [])
    ]
    seq_records = [r for r in seq_records if r is not None]
    if seq_records:
        compound = undo_stack.UndoRecord(
            action_type="compound",
            target="",
            description=f"sequence of {len(seq_records)} reversible step(s)",
            timestamp=__import__("time").time(),
            can_reverse=True,
            extra={"steps": seq_records},
        )
        undo_stack.push(compound)
else:
    _undo_rec = undo_stack.make_record(action, result if isinstance(result, str) else "")
    if _undo_rec is not None:
        undo_stack.push(_undo_rec)
```

### Insertion C — imports and startup

At the top of `agent.py`, add three imports alongside the existing ones:

```python
import security_policy
import sandbox  # noqa: F401 — imported for side-effects (ALLOWED_DIRS built at import)
import undo_stack
```

In `main()`, before the `run_text_mode()` / `run_voice_mode()` call:

```python
security_policy.log_active_policy(security_policy.DEFAULT_POLICY)
```

---

## Data Models

### 9a. Normal command flow (all four safety layers)

```
User input: "open downloads folder"
        │
        ▼
 normalize_prompt → not blank, not undo phrase
        │
        ▼
 builtin / cache / Qwen  →  action = {"action": "open_folder", "target": "downloads"}
        │
        ▼
 validate_action  →  OK
        │
        ▼
 confirmation.needs_confirmation(action)
   └─ security_policy.needs_confirmation → policy["open_folder"] = "never" → False
        │ (no prompt)
        ▼
 execute_action → actions.open_folder("downloads")
   ├─ find_folder("downloads") → "C:\Users\ayush\Downloads"
   ├─ sandbox.check_path("C:\Users\ayush\Downloads")
   │    └─ resolved = C:\Users\ayush\Downloads
   │       allowed (subdirectory of USERPROFILE) → (True, "")
   └─ os.startfile("C:\Users\ayush\Downloads") → "Opened downloads."
        │
        ▼
 undo_stack.make_record(action, "Opened downloads.") → None (open_folder not tracked)
        │
        ▼
 return "Opened downloads."
```

```
User input: "type hello\x1bworld"   (ESC injected)
        │
        ▼
 action = {"action": "type_text", "target": "hello\x1bworld"}
        │
        ▼
 confirmation.needs_confirmation → "never" → False
        │
        ▼
 execute_action → actions.type_text("hello\x1bworld")
   └─ _sanitize_text("hello\x1bworld")
        └─ U+001B (ESCAPE) detected
           return (False, "Refused: text contains control characters: U+001B (ESCAPE).")
        │ (early return — _type_text never called)
        ▼
 return "Refused: text contains control characters: U+001B (ESCAPE)."
```

### 9b. Undo command flow

```
User input: "undo that"
        │
        ▼
 normalize_prompt("undo that") → "undo that"  ∈  UNDO_PHRASES
 confirmation.is_pending()     → False
 disambiguation.is_pending()   → False
        │
        ▼
 undo_stack.pop()
   ├─ stack empty? → return "There's nothing to undo."
   └─ top record = UndoRecord(action_type="close_tab", …)
        │
        ▼
 undo_stack.reverse(record)
   └─ action_type == "close_tab"
      → desktop_actions.press_key("ctrl+shift+t")
      → return "Reopened the closed tab."
        │
        ▼
 return "Reopened the closed tab."
```

---

## Error Handling

- **sandbox.check_path** returns (False, message) on OSError/ValueError during resolution — the caller in ctions.open_folder returns the message directly without crashing.
- **undo_stack.reverse** catches exceptions from desktop_actions calls (e.g. if the focused app was closed) and returns a human-readable error string rather than propagating.
- **_sanitize_text** never raises — it inspects the string and returns (False, message) on any disallowed content.
- **security_policy.log_active_policy** is best-effort — it uses print() so a missing logger never blocks startup.
- **sandbox.ALLOWED_DIRS** is built at import time; if %USERPROFILE% is unset the paths fall back to empty strings which Path.resolve() will canonicalise to the CWD — the sandbox will then block most paths, failing safe.

## Correctness Properties

### Property 1: Sandbox Monotonicity
**Validates: Requirements 2.2, 2.6, 2.7**

check_path(p) returns True for any p that is an allowed dir or a subdirectory thereof, and False for all others. Adding entries to ALLOWED_DIRS only widens the allowed set, never narrows it.

### Property 2: Sanitization Totality
**Validates: Requirements 4.3, 4.4, 4.6**

_sanitize_text inspects every character in the input string before returning. There is no short-circuit path that could allow a bad character to pass if it appears after a good character.

### Property 3: Undo Stack Boundedness
**Validates: Requirements 3.2, 3.7**

collections.deque(maxlen=20) guarantees at most 20 entries at all times regardless of push frequency. No manual trimming is needed or performed.

### Property 4: Policy Completeness
**Validates: Requirements 1.1, 1.5, 1.8**

Every action type in ALLOWED_ACTIONS (gent.py) has an entry in DEFAULT_POLICY.action_type_policy, so 
eeds_confirmation never falls through to an undefined state. The default return for an unknown type is False (fail-open, not fail-closed — unknown actions run without prompting).

## Testing Strategy

### sandbox.py

| Test | Expected outcome | Covers |
|---|---|---|
| `check_path(USERPROFILE)` | `(True, "")` | 2.1, 2.6 |
| `check_path(USERPROFILE + r"\Documents\Projects\foo")` | `(True, "")` | 2.6 |
| `check_path(r"C:\Windows\System32")` | `(False, "Blocked: …")` | 2.2 |
| `check_path(r"..\..\Windows")` resolved against home | `(False, "Blocked: …")` | 2.3 |
| `check_path(r"c:\users\<user>\downloads")` (lowercase drive) | `(True, "")` | 2.7 |
| `check_path(r"C:\USERS\<USER>\DOWNLOADS")` (all caps) | `(True, "")` | 2.7 |
| `check_path("")` empty string | `(False, "Blocked: …")` | 2.2 |

### undo_stack.py

| Test | Expected outcome | Covers |
|---|---|---|
| `pop()` on empty stack | `None` | 3.4 |
| Push 21 records; `len(_stack) == 20` | Oldest dropped | 3.2 |
| `make_record({"action":"press_key","target":"ctrl+w"}, "")` | `UndoRecord(action_type="close_tab", can_reverse=True)` | 3.1 |
| `make_record({"action":"press_key","target":"alt+f4"}, "closed brave")` | `UndoRecord(action_type="close_window", extra={"window_title":"brave"})` | 3.1 |
| `make_record({"action":"type_text","target":"hello"}, "")` | `UndoRecord(action_type="type_text")` | 3.1 |
| `make_record({"action":"screenshot","target":""}, "saved screenshot to C:\\…\\file.png")` | `UndoRecord(action_type="screenshot", extra={"path":"C:\\…\\file.png"})` | 3.1 |
| `make_record({"action":"open_app","target":"brave"}, "")` | `None` | 3.6 |
| `reverse(UndoRecord("close_tab",…))` | Returns `"Reopened the closed tab."` | 3.3 |
| `reverse(UndoRecord("screenshot",…, extra={"path":"x.png"}))` | Returns message containing `"x.png"` | 3.3 |
| Stack not written to disk after push | No file created | 3.7 |

### security_policy.py

| Test | Expected outcome | Covers |
|---|---|---|
| `needs_confirmation({"action":"close_app","target":"brave"}, DEFAULT_POLICY)` | `True` | 1.1 |
| `needs_confirmation({"action":"open_app","target":"brave"}, DEFAULT_POLICY)` | `False` | 1.2 |
| `needs_confirmation({"action":"open_folder","target":"downloads"}, DEFAULT_POLICY)` | `False` | 1.3 |
| `needs_confirmation({"action":"press_key","target":"win+l"}, DEFAULT_POLICY)` | `True` | 1.4 |
| `needs_confirmation({"action":"press_key","target":"ctrl+shift+esc"}, DEFAULT_POLICY)` | `True` | 1.4 |
| `needs_confirmation({"action":"scroll","target":"down"}, DEFAULT_POLICY)` | `False` | 1.8 |
| Sequence with one `close_app` step | `True` | 1.6 |
| Sequence with only `scroll`/`screenshot` steps | `False` | 1.8 |
| `describe_action({"action":"press_key","target":"win+l"}, DEFAULT_POLICY)` | `"lock the screen"` | 1.4 |
| Set `open_app` policy to `"always"`; `needs_confirmation` returns `True` | `True` | 1.5 |
| `log_active_policy` with default policy prints "No action types set to 'always'" | Output matches | 1.9 |

### _sanitize_text (actions.py)

| Test | Expected outcome | Covers |
|---|---|---|
| `_sanitize_text("hello world")` | `(True, "")` | 4.4 |
| `_sanitize_text("hello\tworld")` (tab) | `(True, "")` | 4.4 |
| `_sanitize_text("")` | `(False, "Refused: … empty or contains only whitespace.")` | 4.7 |
| `_sanitize_text("\t\t")` | `(False, "Refused: … empty or contains only whitespace.")` | 4.7 |
| `_sanitize_text("hello\nworld")` | `(False, "Use 'press enter' …")` | 4.1 |
| `_sanitize_text("hello\x1bworld")` | `(False, "Refused: … U+001B (ESCAPE).")` | 4.1, 4.8 |
| `_sanitize_text("a\x00b")` | `(False, "Refused: … U+0000 …")` | 4.1 |
| `_sanitize_text("a\x7fb")` | `(False, "Refused: … U+007F …")` | 4.2 |
| `_sanitize_text("a\x80b")` | `(False, "Refused: … U+0080 …")` | 4.5 |
| `_sanitize_text("a\x9fb")` | `(False, "Refused: … U+009F …")` | 4.5 |
| Multiple bad chars produce all code points in message | Error lists each one | 4.8 |
| No call to `_type_text` on refusal | `_type_text` not invoked | 4.3, 4.6 |
