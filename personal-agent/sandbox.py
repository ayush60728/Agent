"""
sandbox.py

File-path sandbox for the desktop agent.

Enforces an allowlist of permitted directory roots for file-path operations
(open_folder, and future file actions like copy_file, move_file, delete_file).

Path traversal attacks ("../../Windows") are neutralised by Path.resolve(),
which collapses all ".." components against the filesystem before the
allowlist comparison is made.

Case-insensitive on Windows: all comparisons use lower-cased string
representations of the resolved paths so that "C:\\" and "c:\\" are
treated identically.
"""

from __future__ import annotations

import os
from pathlib import Path


def _expand(raw: str) -> Path:
    """Expand environment variables and return an absolute Path."""
    return Path(os.path.expandvars(raw)).resolve()


# Built once at import time from the current user's environment.
# Adding a new allowed directory requires only adding an entry here.
ALLOWED_DIRS: list[Path] = [
    _expand(r"%USERPROFILE%"),
    _expand(r"%USERPROFILE%\Desktop"),
    _expand(r"%USERPROFILE%\Documents"),
    _expand(r"%USERPROFILE%\Downloads"),
    _expand(r"%USERPROFILE%\Pictures"),
    _expand(r"%USERPROFILE%\Music"),
    _expand(r"%USERPROFILE%\Videos"),
]


def check_path(path_str: str) -> str | None:
    """
    Check whether path_str is within the allowed-directory allowlist.

    Resolution order:
        1. Expand environment variables.
        2. If the path is relative, resolve it against the user's home
           directory.
        3. Call Path.resolve() to canonicalise the path and collapse any
           '..' components (path-traversal defence).
        4. Compare case-insensitively against every allowed directory.

    Returns:
        None                          — path is within the allowlist (allowed)
        "Blocked: <path> …"           — path is outside the allowlist (blocked message string)
    """
    try:
        raw = Path(os.path.expandvars(path_str))
        if not raw.is_absolute():
            raw = Path.home() / raw
        resolved = raw.resolve()
    except (OSError, ValueError) as exc:
        return f"Blocked: could not resolve path '{path_str}': {exc}"

    resolved_lower = str(resolved).lower()

    for allowed in ALLOWED_DIRS:
        allowed_lower = str(allowed).lower()
        # Equal to the allowed dir, or a subdirectory of it.
        # The trailing separator prevents "C:\UsersExtra" from matching "C:\Users".
        if resolved_lower == allowed_lower or resolved_lower.startswith(allowed_lower + os.sep):
            return None

    return f"Blocked: '{resolved}' is outside the allowed directories (Desktop, Documents, Downloads, Pictures, Music, Videos, home folder)."
