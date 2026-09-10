"""
undo_stack.py

In-memory undo stack for reversible desktop agent actions.

Tracks actions that can be undone (such as closing a browser tab, copying/moving/renaming files,
deleting files to the Recycle Bin, or typing text) so the user can say "undo" or "undo that".

Design Principles
-----------------
- LIFO stack: most recent reversible action is at the top.
- Self-contained revert functions: each entry carries a callable that executes the reversal.
- Thread-safe: protected by a threading lock.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Dict, Any, Optional

_lock = threading.Lock()
_STACK: List[Dict[str, Any]] = []
MAX_UNDO_ITEMS = 20


def push_undo(action_type: str, revert_fn: Callable[[], str], description: str) -> None:
    """Push a reversible action onto the undo stack.
    
    Args:
        action_type: Name of the action (e.g. 'close_tab', 'move_file', 'delete_file')
        revert_fn: Zero-arg function returning a result string when executed
        description: Human-readable description of what will be undone (e.g. "reopen closed tab")
    """
    with _lock:
        _STACK.append({
            "action_type": action_type,
            "revert_fn": revert_fn,
            "description": description,
        })
        if len(_STACK) > MAX_UNDO_ITEMS:
            _STACK.pop(0)


def pop_undo() -> Optional[Dict[str, Any]]:
    """Pop the most recent reversible action from the stack."""
    with _lock:
        if not _STACK:
            return None
        return _STACK.pop()


def undo_last() -> str:
    """Execute reversal of the most recent action on the stack."""
    item = pop_undo()
    if not item:
        return "Nothing to undo."
    
    try:
        revert_fn = item["revert_fn"]
        res = revert_fn()
        return f"Undone: {item['description']} ({res})"
    except Exception as e:
        return f"Failed to undo '{item['description']}': {e}"


def clear() -> None:
    """Clear all undo history."""
    with _lock:
        _STACK.clear()


def count() -> int:
    """Return number of reversible actions in stack."""
    with _lock:
        return len(_STACK)


def peek_description() -> Optional[str]:
    """Return description of top undo item without popping."""
    with _lock:
        if not _STACK:
            return None
        return _STACK[-1]["description"]
