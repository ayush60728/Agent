# Design Document

## Overview

**File & system actions.** Six new action types — `copy_file`, `move_file`, `delete_file`, `rename_file`, `open_url`, and `run_command` — are added as functions in `actions.py` and dispatcher cases in `execute_action`. All four file actions route every resolved path through `sandbox.check_path()` before touching the filesystem. Destructive file actions (`move_file`, `delete_file`, `rename_file`) and `run_command` gain `"always"` entries in `security_policy.DEFAULT_POLICY`; `copy_file` and `open_url` are `"never"` (non-destructive). `run_command` never uses `shell=True` and is restricted to a module-level `ALLOWED_COMMANDS` frozenset.

**Fallback strategies.** `open_app` gains a Windows Search UI fallback (Win+S) after all five resolver layers return None. `click_text` gains two additional tiers after the existing UIA `find_control_center` fallback: stem-variant OCR retries and a UIA control-type word search (`find_control_by_word` in `ui_automation.py`). All fallback tiers are strictly ordered and non-retrying. "Did you mean?" suggestions for `open_app` and `open_folder` use `difflib.SequenceMatcher` against cached app keys and `KNOWN_FOLDERS` keys; suggestions appear only when the best ratio ≥ 0.7.

**Clearer error messages.** Every failure path in every new and updated action returns a specific, actionable message matching the patterns in Requirements 7.1–7.10. Generic "I couldn't find X on this computer." strings are replaced. No Python exception traceback ever reaches the user.

---

## Architecture

```
user input
    │
    ▼
agent.process_command()
    ├─ blank-input / undo / switch / confirmation / disambiguation guards
    ├─ builtins / aliases / prompt cache / Qwen LLM
    ├─ normalize_action / validate_action  ◄── new action types added
    ├─ confirmation gate  ◄── security_policy.DEFAULT_POLICY extended
    │       "always":  move_file, delete_file, rename_file, run_command
    │       "never":   copy_file, open_url
    └─ _dispatch → execute_action
            │
            ├─ copy_file(source, destination)
            │       _resolve_path ──► sandbox.check_path ──► shutil.copy2 / copytree
            │
            ├─ move_file(source, destination)
            │       _resolve_path ──► sandbox.check_path (×2) ──► shutil.move
            │
            ├─ delete_file(target, with_contents)
            │       _resolve_path ──► sandbox.check_path ──► send2trash / os.remove / shutil.rmtree
            │
            ├─ rename_file(target, new_name)
            │       _resolve_path ──► sandbox.check_path ──► os.rename
            │
            ├─ open_url(target)
            │       URL validation ──► webbrowser.open
            │
            ├─ run_command(target)
            │       ALLOWED_COMMANDS check ──► shlex.split ──► subprocess.run(shell=False)
            │
            ├─ open_app(app_name)   [updated]
            │       find_app() ──► (cache/StartMenu/PATH/Get-StartApps/Registry)
            │           └─ None ──► Win+S UI fallback ──► "Did you mean?" ──► error
            │
            ├─ open_folder(folder_name)   [updated]
            │       find_folder() ──► None ──► "Did you mean?" (KNOWN_FOLDERS) ──► error
            │
            └─ click_text(target_text)   [updated — 4-tier fallback chain]
                    Tier 1: find_text_matches(target)         ← primary OCR
                    Tier 2: find_control_center(target)       ← UIA (existing)
                    Tier 3: find_text_matches(_stem_variants) ← stem OCR retries
                    Tier 4: find_control_by_word(target)      ← UIA word search (new)
                    Tier 5: error  "I couldn't find '<text>' on screen. …"


sandbox.check_path integration (file actions only):
─────────────────────────────────────────────────
    _resolve_path(path_str)          # always first: relative → absolute
         │
         ▼
    sandbox.check_path(str(abs_path))
         │ returns None → allowed, continue
         │ returns str  → blocked message, return immediately, no I/O
         ▼
    source-exists check              # only after sandbox passes
         │
         ▼
    filesystem operation
```

---

## Components and Interfaces

### actions.py — New functions

#### `_resolve_path`

```python
import os
from pathlib import Path

def _resolve_path(path_str: str) -> Path:
    """Resolve a possibly-relative path against USERPROFILE.

    Relative paths (no drive letter + backslash prefix) are joined to
    %USERPROFILE% so the user can say 'Documents\\report.docx' and have it
    land at C:\\Users\\ayush\\Documents\\report.docx.  Absolute paths are
    returned as-is (as a Path object).
    """
    p = Path(path_str)
    if not p.is_absolute():
        p = Path(os.environ.get("USERPROFILE", "")) / p
    return p
```

#### `_did_you_mean_app`

```python
import json
import difflib
from pathlib import Path
from folder_resolver import KNOWN_FOLDERS

_APP_CACHE_FILE = Path(__file__).parent / "app_paths.json"

def _did_you_mean_app(target: str) -> str | None:
    """Fuzzy-match target against app_paths.json keys and KNOWN_FOLDERS keys.
    Returns the best match name if ratio >= 0.7, else None."""
    try:
        with open(_APP_CACHE_FILE, "r", encoding="utf-8") as f:
            app_keys = list(json.load(f).keys())
    except (OSError, json.JSONDecodeError):
        app_keys = []

    candidates = app_keys + list(KNOWN_FOLDERS.keys())
    if not candidates:
        return None

    t = target.lower().strip()
    best_name, best_ratio = None, 0.0
    for name in candidates:
        ratio = difflib.SequenceMatcher(None, t, name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = name

    return best_name if best_ratio >= 0.7 else None
```

#### `_did_you_mean_folder`

```python
def _did_you_mean_folder(target: str) -> str | None:
    """Fuzzy-match target against KNOWN_FOLDERS keys only.
    Returns the best match name if ratio >= 0.7, else None."""
    t = target.lower().strip()
    best_name, best_ratio = None, 0.0
    for name in KNOWN_FOLDERS.keys():
        ratio = difflib.SequenceMatcher(None, t, name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = name
    return best_name if best_ratio >= 0.7 else None
```

#### `copy_file`

```python
import shutil
from sandbox import check_path

def copy_file(source: str, destination: str) -> str:
    """Copy source to destination; both are sandbox-checked first."""
    src = _resolve_path(source)
    dst = _resolve_path(destination)

    blocked = check_path(str(src))
    if blocked:
        return blocked
    blocked = check_path(str(dst))
    if blocked:
        return blocked

    if not src.exists():
        return f"I couldn't find '{src}'. Check the file name and try again."

    if dst.exists():
        return (f"A file already exists at '{dst}'. "
                "Say 'overwrite' to replace it, or choose a different name.")

    try:
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
    except OSError as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Copied '{src}' to '{dst}'."
```

#### `move_file`

```python
def move_file(source: str, destination: str) -> str:
    """Move source to destination (confirmation already obtained by the gate).
    Both paths are sandbox-checked before any I/O."""
    src = _resolve_path(source)
    dst = _resolve_path(destination)

    blocked = check_path(str(src))
    if blocked:
        return blocked
    blocked = check_path(str(dst))
    if blocked:
        return blocked

    if not src.exists():
        return f"I couldn't find '{src}'. Check the file name and try again."

    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Moved '{src}' to '{dst}'."
```

#### `delete_file`

```python
def delete_file(target: str, with_contents: bool = False) -> str:
    """Delete target, preferring the Recycle Bin (send2trash).
    with_contents must be True for non-empty directories."""
    path = _resolve_path(target)

    blocked = check_path(str(path))
    if blocked:
        return blocked

    if not path.exists():
        return f"I couldn't find '{path}'. Check the file name and try again."

    if path.is_dir() and any(path.iterdir()) and not with_contents:
        return (f"'{path}' is a folder with contents. "
                "Say 'delete " + path.name + " and contents' to confirm recursive deletion.")

    try:
        import send2trash
        send2trash.send2trash(str(path))
        return f"Deleted '{path}' (sent to Recycle Bin)."
    except ImportError:
        pass
    except Exception as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    # send2trash unavailable — permanent delete with warning
    try:
        if path.is_dir():
            shutil.rmtree(str(path))
        else:
            os.remove(str(path))
    except OSError as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return (f"Warning: send2trash is not installed — this deletion is permanent. "
            f"Deleted '{path}'.")
```

#### `rename_file`

```python
def rename_file(target: str, new_name: str) -> str:
    """Rename target to new_name within its parent directory."""
    if os.sep in new_name or "/" in new_name:
        return (f"'{new_name}' looks like a path, not a name. "
                "Use move_file to move to a different folder.")

    path = _resolve_path(target)
    dst = path.parent / new_name

    # Check for '..' traversal in new_name after construction
    try:
        dst.relative_to(path.parent)
    except ValueError:
        return (f"'{new_name}' looks like a path, not a name. "
                "Use move_file to move to a different folder.")

    blocked = check_path(str(path))
    if blocked:
        return blocked

    if not path.exists():
        return f"I couldn't find '{path}'. Check the file name and try again."

    if dst.exists():
        return f"A file already exists at '{dst}'. Choose a different name."

    try:
        os.rename(str(path), str(dst))
    except OSError as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Renamed '{path.name}' to '{new_name}'."
```

#### `open_url`

```python
import re
import webbrowser

_URL_RE = re.compile(
    r"^(https?://)"               # explicit scheme, OR
    r"|"
    r"^[\w.-]+\.[a-zA-Z]{2,}",   # bare domain  e.g. github.com
    re.IGNORECASE,
)

def open_url(target: str) -> str:
    """Validate and open a URL in the default browser."""
    if not _URL_RE.match(target.strip()):
        return (f"'{target}' doesn't look like a URL. "
                "Try including https:// or a domain like github.com.")

    url = target.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    try:
        webbrowser.open(url)
    except Exception as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Opening {url} in your default browser."
```

#### `run_command`

```python
import shlex
import subprocess

ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "echo", "dir", "type", "ping", "ipconfig", "hostname",
    "whoami", "ver", "date", "time",
})

def run_command(target: str) -> str:
    """Run a whitelisted shell command; confirmation already obtained by gate."""
    tokens = target.strip().split()
    if not tokens:
        return "I need a command to run."

    prefix = tokens[0].lower()
    if prefix not in ALLOWED_COMMANDS:
        allowed_list = ", ".join(sorted(ALLOWED_COMMANDS))
        return (f"Command '{prefix}' is not in the allowed list. "
                f"Allowed commands: {allowed_list}.")

    try:
        args = shlex.split(target)
    except ValueError as e:
        return f"Something went wrong: ValueError: {e}"

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return "The command timed out after 10 seconds."
    except OSError as e:
        import traceback; traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        output = f"Command exited with code {result.returncode}. " + output

    if len(output) > 500:
        output = output[:500] + " ... [output truncated]"

    return output
```

#### Updated `execute_action` dispatcher cases

```python
    if action_type == "copy_file":
        return copy_file(action.get("source", ""), action.get("destination", ""))

    if action_type == "move_file":
        return move_file(action.get("source", ""), action.get("destination", ""))

    if action_type == "delete_file":
        return delete_file(action.get("target", ""),
                           bool(action.get("with_contents", False)))

    if action_type == "rename_file":
        return rename_file(action.get("target", ""), action.get("new_name", ""))

    if action_type == "open_url":
        return open_url(action.get("target", ""))

    if action_type == "run_command":
        return run_command(action.get("target", ""))
```

---

### actions.py — Updated `open_app`

The updated failure path appended after `find_app(app_name, force_rescan=True)` returns None:

```python
    # --- after force_rescan also returns None ---

    # UI fallback: open Windows Search and type the app name
    try:
        import pyautogui as _pag
        _pag.hotkey("win", "s")
        time.sleep(0.5)
        _pag.write(app_name, interval=0.05)
        time.sleep(0.5)
        _pag.press("enter")
        time.sleep(1.5)
        suggestion = _did_you_mean_app(app_name)
        msg = (f"I couldn't find {app_name} through normal search — "
               "tried opening it via Windows Search as a best-effort fallback.")
        if suggestion:
            msg += f" Did you mean '{suggestion}'?"
        return msg
    except Exception:
        pass

    # All methods failed
    suggestion = _did_you_mean_app(app_name)
    msg = f"I couldn't find {app_name}."
    if suggestion:
        msg += f" Did you mean '{suggestion}'?"
    msg += (f" Try saying 'open {app_name}' again after installing it, "
            f"or say 'forget {app_name}' to clear a bad cached path.")
    return msg
```

The `time` import is already present at module level in `actions.py` via `desktop_actions` (which imports it). If not, add `import time` at the top of `actions.py`.

---

### actions.py — Updated `open_folder`

After both `find_folder` calls return None:

```python
    # --- after force_rescan also returns None ---
    suggestion = _did_you_mean_folder(folder_name)
    msg = f"I couldn't find a folder called {folder_name}."
    if suggestion:
        msg += f" Did you mean '{suggestion}'?"
    return msg
```

The existing `open_folder` returns `f"I couldn't find a folder called {folder_name} on this computer."` in the not-found cases. Both occurrences are replaced with the new format above (only the final fallback; intermediate OSError paths use the existing `f"I found {folder_name}, but couldn't open it: {e}"` form unchanged).

---

### desktop_actions.py — Updated `click_text` fallback chain

The existing `click_text` function ends (after the UIA `find_control_center` block) with:

```python
    return f"couldn't find '{target_text}' on screen"
```

This section is replaced with the extended 4-tier chain:

```python
    # (existing) Tier 1: OCR  — handled by find_text_matches block above
    # (existing) Tier 2: UIA find_control_center — handled above, returns if pos
    # Tier 3: stem-variant OCR retries
    for variant in _stem_variants(locate):
        stem_matches = find_text_matches(variant)
        if stem_matches:
            m = stem_matches[0]
            label = f"'{variant}' (stem match of '{locate}')"
            return _do_click((m.cx, m.cy), label, button=button)

    # Tier 4: UIA control-type word search
    try:
        from ui_automation import find_control_by_word
        pos = find_control_by_word(locate)
        if pos:
            return _do_click(pos, f"'{locate}' (accessibility word match)", button=button)
    except Exception:
        pass

    # All tiers exhausted
    from desktop_actions import get_current_focus_app
    focused = get_current_focus_app() or "the current window"
    return (f"I couldn't find '{target_text}' on screen. "
            f"Is '{focused}' the right window? Say 'focus <appname>' to switch.")
```

`get_current_focus_app` is already imported from within the same module (`_current_focus_app` is the global; expose it via the existing `get_current_focus_app()` function).

#### `_stem_variants` helper

```python
def _stem_variants(text: str) -> list[str]:
    """Generate morphological simplifications plus case variants of `text`.

    Strips common English suffixes from the final word, then adds ALL_CAPS
    and Title_Case forms. Deduplicates and limits to 6 variants.
    """
    text = text.strip()
    if not text:
        return []

    words = text.split()
    last = words[-1]
    prefix = " ".join(words[:-1])

    SUFFIXES = ("ing", "ed", "er", "ly", "s")
    stems = []
    for suf in SUFFIXES:
        if last.lower().endswith(suf) and len(last) - len(suf) >= 3:
            stem = last[: -len(suf)]
            candidate = (prefix + " " + stem).strip() if prefix else stem
            stems.append(candidate)

    # Case variants of the original
    stems.append(text.upper())
    stems.append(text.title())

    # Deduplicate, preserve order, exclude the original text itself
    seen = {text.lower()}
    out = []
    for v in stems:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
        if len(out) >= 6:
            break
    return out
```

---

### actions.py — Updated `close_app` error message

Inside `actions.close_app`, the `_close_app` call returns a string such as `"brave is not running"`. Replace the return with:

```python
    result = _close_app(target or "")
    if "is not running" in result.lower():
        app_label = (target or "").strip() or "that app"
        return (f"I couldn't close '{app_label}' — it doesn't appear to be running. "
                f"Say 'open {app_label}' to launch it.")
    return result
```

---

### ui_automation.py — New `find_control_by_word`

```python
def find_control_by_word(query: str) -> tuple | None:
    """Return (x, y) center of the first clickable control whose accessible
    Name contains any word from `query` as a case-insensitive substring.

    Only considers ButtonControl, HyperlinkControl, and MenuItemControl.
    Uses the same BFS walk and budget constants as find_control_center.
    Returns None if UIA is unavailable or no match is found.
    """
    root = _active_root()
    if root is None:
        return None

    query_words = [w.lower() for w in query.split() if w]
    if not query_words:
        return None

    TARGET_TYPES = {"ButtonControl", "HyperlinkControl", "MenuItemControl"}

    queue = deque([(root, 0)])
    visited = 0
    start = time.time()

    while queue and visited < _MAX_NODES:
        if time.time() - start > _WALK_BUDGET_S:
            break
        node, depth = queue.popleft()
        visited += 1

        try:
            ctype = node.ControlTypeName
        except Exception:
            ctype = ""

        if ctype in TARGET_TYPES:
            try:
                name = node.Name or ""
            except Exception:
                name = ""

            if name:
                try:
                    offscreen = node.IsOffscreen
                except Exception:
                    offscreen = False

                if not offscreen:
                    try:
                        r = node.BoundingRectangle
                    except Exception:
                        r = None

                    if r is not None:
                        try:
                            is_empty = r.isempty()
                        except Exception:
                            is_empty = True

                        if not is_empty:
                            area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                            if area > 0:
                                name_lower = name.lower()
                                name_words = name_lower.split()
                                for qw in query_words:
                                    for nw in name_words:
                                        if qw in nw:
                                            try:
                                                return (r.xcenter(), r.ycenter())
                                            except Exception:
                                                pass

        if depth < _MAX_DEPTH:
            try:
                for child in node.GetChildren():
                    queue.append((child, depth + 1))
            except Exception:
                pass

    return None
```

---

### security_policy.py — Extended `DEFAULT_POLICY`

Add to `action_type_policy` in `DEFAULT_POLICY`:

```python
        "move_file":   "always",   # always confirm — source disappears
        "delete_file": "always",   # always confirm — destructive
        "rename_file": "always",   # always confirm — name changes permanently
        "copy_file":   "never",    # non-destructive by default
        "open_url":    "never",
        "run_command": "always",   # always confirm — shell execution
```

Add to `describe_action` for new action types:

```python
    if kind == "move_file":
        src = (action.get("source") or "").strip()
        dst = (action.get("destination") or "").strip()
        return f"move '{src}' to '{dst}'"

    if kind == "delete_file":
        target = (action.get("target") or "").strip()
        return f"delete '{target}'"

    if kind == "rename_file":
        target = (action.get("target") or "").strip()
        new_name = (action.get("new_name") or "").strip()
        return f"rename '{target}' to '{new_name}'"

    if kind == "run_command":
        cmd = (action.get("target") or "").strip()
        return f"run '{cmd}'"
```

---

### agent.py — `ALLOWED_ACTIONS` and `SYSTEM_PROMPT` additions

#### Six new entries in `ALLOWED_ACTIONS`

```python
ALLOWED_ACTIONS = {
    # ... existing entries ...
    "copy_file",
    "move_file",
    "delete_file",
    "rename_file",
    "open_url",
    "run_command",
}
```

#### `SYSTEM_PROMPT` additions (append after action 12)

```
13. copy_file
Use this when the user wants to duplicate a file or folder from one location
to another.

Format:
{
    "action": "copy_file",
    "source": "<path or relative filename>",
    "destination": "<path or relative filename>"
}

14. move_file
Use this when the user wants to move a file or folder to a different location.

Format:
{
    "action": "move_file",
    "source": "<path or relative filename>",
    "destination": "<path or relative filename>"
}

15. delete_file
Use this when the user wants to delete a file or folder.
Set "with_contents" to true only when the user explicitly says
"and contents" or "and everything inside".

Format:
{
    "action": "delete_file",
    "target": "<path or relative filename>",
    "with_contents": false
}

16. rename_file
Use this when the user wants to rename a file or folder in place
(not move it to a different directory).
"new_name" must be a filename only, not a path.

Format:
{
    "action": "rename_file",
    "target": "<path or current filename>",
    "new_name": "<new filename only>"
}

17. open_url
Use this when the user wants to open a website or URL in their default browser.
Do NOT require a browser to be open first.

Format:
{
    "action": "open_url",
    "target": "<url or bare domain>"
}

18. run_command
Use this when the user wants to run a simple informational shell command
such as ipconfig, hostname, or whoami.
RESTRICTION: Only whitelisted command prefixes are permitted.
Do not attempt administrative or file-modifying commands.

Format:
{
    "action": "run_command",
    "target": "<command string>"
}
```

#### `validate_action` additions

```python
    if action_type in ("copy_file", "move_file"):
        source = action.get("source")
        destination = action.get("destination")
        if not isinstance(source, str) or not source.strip():
            return False, "source is missing."
        if not isinstance(destination, str) or not destination.strip():
            return False, "destination is missing."

    if action_type in ("delete_file", "rename_file", "open_url", "run_command"):
        target = action.get("target")
        if not isinstance(target, str) or not target.strip():
            return False, "Target is missing."

    if action_type == "rename_file":
        new_name = action.get("new_name")
        if not isinstance(new_name, str) or not new_name.strip():
            return False, "new_name is missing."
```

---

## Data Models

### `ALLOWED_COMMANDS`

```python
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "echo", "dir", "type", "ping", "ipconfig", "hostname",
    "whoami", "ver", "date", "time",
})
```

Defined at module level in `actions.py`. No new cache files; no persistent state is added by any new action.

### Action dict shapes

| Action | Shape |
|---|---|
| `copy_file` | `{"action": "copy_file", "source": str, "destination": str}` |
| `move_file` | `{"action": "move_file", "source": str, "destination": str}` |
| `delete_file` | `{"action": "delete_file", "target": str, "with_contents": bool}` |
| `rename_file` | `{"action": "rename_file", "target": str, "new_name": str}` |
| `open_url` | `{"action": "open_url", "target": str}` |
| `run_command` | `{"action": "run_command", "target": str}` |

`with_contents` defaults to `False` in `execute_action` if the key is absent.

---

## Error Handling

| Failure mode | Action | Return string pattern |
|---|---|---|
| App not found after all layers + UI fallback | `open_app` | `"I couldn't find <app>. [Did you mean '<suggestion>'?] Try saying 'open <app>' again after installing it, or say 'forget <app>' to clear a bad cached path."` |
| App launched via Win+S UI fallback | `open_app` | `"I couldn't find <app> through normal search — tried opening it via Windows Search as a best-effort fallback. [Did you mean '<suggestion>'?]"` |
| Folder not found after all layers | `open_folder` | `"I couldn't find a folder called <name>. [Did you mean '<suggestion>'?]"` |
| `click_text` exhausted all 4 fallback tiers | `click_text` | `"I couldn't find '<text>' on screen. Is '<focused_app>' the right window? Say 'focus <appname>' to switch."` |
| `close_app` target not running | `close_app` | `"I couldn't close '<app>' — it doesn't appear to be running. Say 'open <app>' to launch it."` |
| `sandbox.check_path` returns blocked string | any file action | `"Blocked: '<path>' is outside the allowed directories (Desktop, Documents, Downloads, Pictures, Music, Videos, home folder)."` |
| Source/target path does not exist | `copy_file`, `move_file`, `delete_file`, `rename_file` | `"I couldn't find '<source_path>'. Check the file name and try again."` |
| Destination already exists | `copy_file` | `"A file already exists at '<dest>'. Say 'overwrite' to replace it, or choose a different name."` |
| Destination already exists | `rename_file` | `"A file already exists at '<destination>'. Choose a different name."` |
| Non-empty folder, `with_contents` not set | `delete_file` | `"'<path>' is a folder with contents. Say 'delete <name> and contents' to confirm recursive deletion."` |
| `send2trash` not installed | `delete_file` | Prefixes `"Warning: send2trash is not installed — this deletion is permanent. "` to the success message |
| `send2trash` import failure / OS error | `delete_file` | `"Something went wrong: <ExceptionType>: <message>."` |
| `shutil.copy2` / `copytree` raises `OSError` | `copy_file` | `"Something went wrong: OSError: <message>."` |
| `shutil.move` raises `OSError` | `move_file` | `"Something went wrong: OSError: <message>."` |
| `os.rename` raises `OSError` | `rename_file` | `"Something went wrong: OSError: <message>."` |
| URL fails validation | `open_url` | `"'<target>' doesn't look like a URL. Try including https:// or a domain like github.com."` |
| `webbrowser.open` raises | `open_url` | `"Something went wrong: <ExceptionType>: <message>."` |
| Command prefix not in `ALLOWED_COMMANDS` | `run_command` | `"Command '<cmd>' is not in the allowed list. Allowed commands: <comma-separated list>."` |
| `subprocess.TimeoutExpired` | `run_command` | `"The command timed out after 10 seconds."` |
| Non-zero exit code | `run_command` | `"Command exited with code <N>. <combined stdout+stderr>"` (then truncated to 500 chars) |
| `new_name` contains path separator | `rename_file` | `"'<new_name>' looks like a path, not a name. Use move_file to move to a different folder."` |
| `_stem_variants` called with empty string | `click_text` | Returns `[]`; no stem tier attempted; falls through to Tier 4 / error |
| UIA unavailable in `find_control_by_word` | `click_text` | `_active_root()` returns `None`; function returns `None`; no crash |
| Any unhandled exception in file action | all file actions | `"Something went wrong: <ExceptionType>: <message>."` (traceback to stdout/log only) |

---

## Correctness Properties

### Property 1: Sandbox Invariant

**Validates: Requirements 1.2, 2.2, 3.2, 4.3**

For every call to `copy_file`, `move_file`, `delete_file`, or `rename_file`:
- `_resolve_path` is called first, converting the input to an absolute `Path`.
- `sandbox.check_path(str(resolved_path))` is called on every path parameter **before** any `Path.exists()` check or filesystem operation.
- If `check_path` returns a non-None string, the function returns that string immediately with no I/O.

This invariant holds even when:
- The path is already absolute (no resolve step changes it).
- The source does not exist (check happens before the exists test).
- The operation later fails for another reason (early return on block is unconditional).

### Property 2: No `shell=True`

**Validates: Requirement 6.4**

`run_command` calls `subprocess.run` with `shell=False` always. The command string is split into a list via `shlex.split` before being passed to `subprocess.run`. The `shell` keyword argument is never set to `True`, never conditionally set, and no string is passed as the `args` parameter directly. `ALLOWED_COMMANDS` restricts the executable to known read-only commands, further limiting the blast radius even if the argument list were manipulated.

### Property 3: Confirmation Coverage

**Validates: Requirements 2.4, 3.4, 4.4, 6.3**

The confirmation gate runs in `agent.process_command` after `validate_action` and before `_dispatch`. It calls `security_policy.needs_confirmation(action, DEFAULT_POLICY)`. Because `DEFAULT_POLICY.action_type_policy` maps `move_file`, `delete_file`, `rename_file`, and `run_command` to `"always"`, `needs_confirmation` returns `True` for these actions regardless of whether they arrived via builtin, prompt cache, or LLM. The confirmation prompt names the source/destination or command. The action executes only after an affirmative answer within `CONFIRM_TIMEOUT_S`. This is true for single actions and for these action types when they appear as steps in a sequence (the sequence is confirmed as a whole).

### Property 4: Fallback Chain Monotonicity

**Validates: Requirements 9.1, 9.2, 9.5**

`click_text` attempts fallback tiers strictly in order. Each tier is entered if and only if all prior tiers returned no match:

1. **Primary OCR** (`find_text_matches`): if matches, click and return.
2. **UIA `find_control_center`**: attempted only when OCR returned empty; if match, click and return.
3. **Stem variants** (`_stem_variants` + `find_text_matches` per variant): attempted only when Tiers 1 and 2 both returned nothing; first variant with matches wins.
4. **UIA word search** (`find_control_by_word`): attempted only when Tiers 1–3 all returned nothing; if match, click and return.
5. **Error**: reached only when all four tiers found nothing.

No tier is skipped unless a prior tier succeeded. No tier is retried. Disambiguation (`AmbiguousClick`) is triggered at any tier when that tier produces 2+ matches, preserving the existing disambiguation flow (Requirement 9.5).

---

## Testing Strategy

### File Actions

| Test description | Expected outcome | Requirement |
|---|---|---|
| `copy_file("Documents/a.txt", "Desktop/a.txt")` with `a.txt` present | Returns `"Copied '…' to '…'."` with absolute paths | 1.7 |
| `copy_file` with source outside sandbox (e.g. `C:\Windows\system32\cmd.exe`) | Returns blocked message verbatim | 1.2 |
| `copy_file` where destination already exists | Returns overwrite-prompt message | 1.5, 7.7 |
| `copy_file` with source that does not exist | Returns source-not-found message | 1.4, 7.6 |
| `copy_file` with a directory source | Uses `shutil.copytree`; returns success | 1.6 |
| `copy_file` with relative paths | Resolves both against USERPROFILE before any check | 1.3 |
| `move_file` with valid paths | Returns `"Moved '…' to '…'."` | 2.7 |
| `move_file` with missing source | Returns source-not-found message before confirmation | 2.5, 7.6 |
| `delete_file` on a file | Returns `"Deleted '…' (sent to Recycle Bin)."` when send2trash available | 3.8 |
| `delete_file` on non-empty directory without `with_contents` | Returns non-empty-folder refusal | 3.6 |
| `delete_file` on non-empty directory with `with_contents=True` | Deletes and returns success | 3.6 |
| `delete_file` when `send2trash` not importable | Permanent-delete warning prefixed to success | 3.7 |
| `rename_file` with valid target and new name | Returns `"Renamed '<old>' to '<new>'."` | 4.7 |
| `rename_file` where `new_name` contains `os.sep` | Returns path-separator refusal | 4.2 |
| `rename_file` where destination already exists | Returns name-conflict message | 4.6 |
| `rename_file` with missing target | Returns source-not-found message | 4.5, 7.6 |

### open_url

| Test description | Expected outcome | Requirement |
|---|---|---|
| `open_url("github.com")` | Prepends `https://`, calls `webbrowser.open`, returns success | 5.3, 5.6 |
| `open_url("https://docs.python.org")` | Calls `webbrowser.open` unchanged, returns success | 5.4, 5.6 |
| `open_url("not a url")` | Returns URL-validation error | 5.2 |
| `open_url("")` | Returns validation error (empty string fails regex) | 5.2 |
| `open_url("http://example.com")` | Accepts http:// scheme, does not double-prefix | 5.3 |
| `open_url("sub.domain.co.uk")` | Matches multi-part TLD, prepends https:// | 5.2, 5.3 |
| `open_url` with `webbrowser.open` raising | Returns `"Something went wrong: …"` | 7.10 |
| `open_url` requires no focused app | No `_ensure_focused_app_active` call; no error if no app focused | 5.4 |

### run_command

| Test description | Expected outcome | Requirement |
|---|---|---|
| `run_command("ipconfig")` | Returns truncated stdout, no traceback | 6.5 |
| `run_command("rm -rf /")` | Returns not-in-whitelist message, no process started | 6.2, 7.8 |
| `run_command("ping 127.0.0.1")` with timeout mock | Returns timeout message | 6.6, 7.9 |
| `run_command("echo hello")` | Returns `"hello"` (or `"hello\n"`) | 6.5 |
| `run_command` output > 500 chars | Output truncated with `" ... [output truncated]"` | 6.5 |
| `run_command("dir /invalid_flag")` non-zero exit | Prepends `"Command exited with code <N>. "` to output | 6.7 |
| `run_command` never uses `shell=True` | Static inspection: `shell=False` in every code path | 6.4 |
| `run_command` with empty string | Returns "I need a command to run." | 6.2 |
| `run_command("whoami /format:table")` | `shlex.split` splits correctly; passed as list to subprocess | 6.4 |
| `ALLOWED_COMMANDS` is a `frozenset` | Membership test is O(1); frozenset is immutable | 6.2 |

### Fallbacks and "Did You Mean?"

| Test description | Expected outcome | Requirement |
|---|---|---|
| `open_app` with name ratio 0.75 against cache key | "Did you mean" clause included | 10.1 |
| `open_app` with name ratio 0.65 against cache key | No "Did you mean" clause | 10.4 |
| `open_folder` with name ratio 0.8 against KNOWN_FOLDERS | "Did you mean" clause included | 10.2 |
| `open_folder` with no close match | Error message has no suggestion | 10.4 |
| `_stem_variants("submitting")` | Returns list including `"submit"`, `"SUBMITTING"`, `"Submitting"` | 9.1 |
| `_stem_variants("")` | Returns `[]` | 9.1 |
| `click_text` OCR finds nothing, UIA finds nothing, stem variant finds match | Returns `"clicked … (stem match of '…')"` | 9.1, 9.3 |
| `click_text` all tiers fail | Returns focused-app hint message | 7.3, 9.2 |
| `find_control_by_word` on machine without `uiautomation` | Returns `None` gracefully | 9.2 |
| `open_app` UI fallback invoked | Returns best-effort fallback message | 8.2, 8.4 |
| `open_app` all layers and UI fallback fail | Returns full not-found message with optional suggestion | 7.1 |
| `close_app` with non-running target | Returns not-running message with launch hint | 7.4 |

### Error Messages

| Test description | Expected outcome | Requirement |
|---|---|---|
| File action with path outside sandbox | Returns verbatim blocked message | 7.5 |
| Any file action with missing source | Returns `"I couldn't find '<path>'. Check the file name and try again."` | 7.6 |
| `copy_file` destination exists, no overwrite | Returns overwrite-prompt format | 7.7 |
| `run_command` with forbidden prefix | Returns whitelist-error format with full allowed list | 7.8 |
| `run_command` timeout | Returns `"The command timed out after 10 seconds."` | 7.9 |
| `OSError` inside `copy_file` | Returns `"Something went wrong: OSError: …"`, no traceback to user | 7.10 |
| `delete_file` permanent delete (no send2trash) | Warning prefix present in return string | 3.7 |
| `rename_file` success | Uses only filename components in message, not full paths | 4.7 |
