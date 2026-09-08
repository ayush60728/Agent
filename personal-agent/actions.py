"""
actions.py

Takes a structured action (as Qwen will eventually produce) and executes it.

Currently supports:
    open_app    -> resolves the app via app_resolver.find_app(), launches it,
                   then auto-focuses its window so follow-up click/type/key
                   commands act on it without an extra step.
    open_folder -> resolves the folder via folder_resolver.find_folder() and
                   opens it in File Explorer.
    focus_app   -> explicitly switch the agent's "current" focused app
                   (e.g. "move your focus to brave") without relaunching it.
    close_app   -> close a named app's window ("close brave"), or the
                   currently-focused app when the user says "close it".
    click_text  -> finds text on screen (via OCR, scoped to the current
                   focused app's window) and clicks it.
    type_text   -> types a string via keyboard simulation.
    press_key   -> presses a single key or key combo (e.g. "enter", "ctrl+s").
    scroll      -> scrolls the focused window up or down.
    screenshot  -> captures the screen to a PNG in the user's Pictures folder.
    wait        -> pauses for N seconds between steps.

click_text/type_text/press_key all check that the currently-focused app is
still running before acting — if the user closed it manually, we say so
instead of clicking/typing into whatever happens to be in the foreground.

Handles both kinds of results find_app() can return:
    - a normal filesystem path (.exe / .lnk / .url)  -> os.startfile(path)
    - a "shell:AppsFolder\\<AppID>" identifier (UWP/Store apps) -> os.startfile(identifier)
"""

import os
import json
import difflib
import shlex
import subprocess
import shutil
from pathlib import Path

import context_memory
import sandbox
from app_resolver import find_app
from folder_resolver import find_folder, KNOWN_FOLDERS
from desktop_actions import click_text as _click_text
from desktop_actions import click_at as _click_at
from desktop_actions import move_to as _move_to
from desktop_actions import type_text as _type_text
from desktop_actions import press_key as _press_key
from desktop_actions import wait as _wait
from desktop_actions import focus_app as _focus_app
from desktop_actions import close_app as _close_app
from desktop_actions import scroll as _scroll
from desktop_actions import take_screenshot as _take_screenshot
from desktop_actions import get_color as _get_color
from desktop_actions import get_current_focus_app
from sandbox import check_path


# ---------------------------------------------------------------------------
# Helper functions for path resolution and fuzzy suggestions
# ---------------------------------------------------------------------------

_APP_CACHE_FILE = Path(__file__).parent / "app_paths.json"


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


# ---------------------------------------------------------------------------
# File actions
# ---------------------------------------------------------------------------

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
        import traceback
        traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Copied '{src}' to '{dst}'."


def open_app(app_name: str) -> str:
    """Resolve and launch an application by name, then auto-focus its
    window so a follow-up click/type/key command doesn't need an explicit
    'focus' step first.

    If the cached path fails to launch (e.g. this machine's cache came
    from someone else, or a shell:AppsFolder entry went stale), force a
    fresh scan once and retry before giving up.
    """

    if not app_name:
        return "I need an app name to open."

    path = find_app(app_name)

    if not path:
        return f"I couldn't find {app_name} on this computer."

    def _launch(p):
        os.startfile(p)
        # Window may take a beat to appear after startfile returns —
        # give focus_app a couple of retries before giving up.
        _focus_app(app_name, retries=3, retry_delay=0.5)
        context_memory.update(
            last_opened_app=app_name,
            last_focused_app=app_name,
            last_action={"action": "open_app", "target": app_name},
        )
        return f"Opened {app_name}."

    try:
        return _launch(path)

    except (FileNotFoundError, OSError):
        # Cached path didn't actually work on this machine — rescan and
        # try once more before giving up.
        path = find_app(app_name, force_rescan=True)

        if not path:
            return f"I couldn't find {app_name} on this computer."

        try:
            return _launch(path)
        except OSError as e:
            return f"I found {app_name}, but couldn't open it: {e}"


def open_folder(folder_name: str) -> str:
    """Resolve and open a folder in File Explorer.

    Same self-healing pattern as open_app: if the cached path fails to
    open on this machine, force a rescan and retry once.
    """

    if not folder_name:
        return "I need a folder name to open."

    path = find_folder(folder_name)

    if not path:
        return f"I couldn't find a folder called {folder_name} on this computer."

    try:
        os.startfile(path)
        context_memory.update(
            last_action={"action": "open_folder", "target": folder_name},
        )
        return f"Opened {folder_name}."

    except (FileNotFoundError, OSError):
        path = find_folder(folder_name, force_rescan=True)

        if not path:
            return f"I couldn't find a folder called {folder_name} on this computer."

        try:
            os.startfile(path)
            context_memory.update(
                last_action={"action": "open_folder", "target": folder_name},
            )
            return f"Opened {folder_name}."
        except OSError as e:
            return f"I found {folder_name}, but couldn't open it: {e}"


def focus_app(app_name: str) -> str:
    """Switch the agent's tracked 'current' app to an already-running app,
    without launching or relaunching it."""

    if not app_name:
        return "I need an app name to focus."

    result = _focus_app(app_name)
    # _focus_app returns "Switched to <name>." on success; only record
    # memory when the switch actually worked — not on "is not running" errors.
    if result.lower().startswith("switched"):
        context_memory.update(
            last_focused_app=app_name,
            last_action={"action": "focus_app", "target": app_name},
        )
    return result


def close_app(target: str = "") -> str:
    """Close a window — either a named app ("close brave") or the currently
    focused app when the user just says "close it" / "close this window"
    (empty/pronoun target).

    Unlike click/type/key, this does NOT require re-activating the app
    first — it posts a close straight to the window handle — so it works
    even when this terminal has the foreground. That's also why it doesn't
    call _ensure_focused_app_active()."""

    return _close_app(target or "")


def delete_file(target: str, with_contents: bool = False) -> str:
    """Delete target, preferring the Recycle Bin (send2trash).
    with_contents must be True for non-empty directories.
    
    Calls _resolve_path and sandbox.check_path, checks target exists,
    detects non-empty folders and requires "and contents" phrase,
    prefers send2trash over os.remove/shutil.rmtree with warning if unavailable.
    """
    path = _resolve_path(target)

    # Sandbox check first
    blocked = check_path(str(path))
    if blocked:
        return blocked

    # Check if target exists
    if not path.exists():
        return f"I couldn't find '{path}'. Check the file name and try again."

    # Check for non-empty directory
    if path.is_dir() and any(path.iterdir()) and not with_contents:
        return (f"'{path}' is a folder with contents. "
                f"Say 'delete {path.name} and contents' to confirm recursive deletion.")

    # Try send2trash first (Recycle Bin)
    try:
        import send2trash
        send2trash.send2trash(str(path))
        return f"Deleted '{path}' (sent to Recycle Bin)."
    except ImportError:
        # send2trash not available - fall back to permanent deletion
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    # Permanent delete with warning (send2trash unavailable)
    try:
        if path.is_dir():
            shutil.rmtree(str(path))
        else:
            os.remove(str(path))
    except OSError as e:
        import traceback
        traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return (f"Warning: send2trash is not installed — this deletion is permanent. "
            f"Deleted '{path}'.")


def _ensure_focused_app_active() -> str | None:
    """Re-activate the tracked focused app right before a click/type/key
    action. This matters because typing the *next* command into this
    terminal steals foreground focus back to the terminal itself — so
    just checking the app is still 'running' isn't enough; we have to
    bring it back to the foreground every single time, immediately
    before acting. Returns an error string if it fails, else None."""

    current = get_current_focus_app()

    if current is None:
        return "No app is currently focused. Say 'move your focus to <app>' first."

    result = _focus_app(current)
    if "is not running" in result:
        return result

    return None


def click_text(target_text: str):
    """Find text on screen (within the focused app's window) via OCR and
    click its center point. Refuses if the focused app has been closed.

    Usually returns a result string, but may return an AmbiguousClick when 2+
    rival matches remain — process_command consumes that to ask the user which
    one; it never reaches an end caller as a value to print."""

    if not target_text:
        return "I need some text to click."

    error = _ensure_focused_app_active()
    if error:
        return error

    result = _click_text(target_text)
    # Only record memory for a clean click — not for AmbiguousClick objects
    # or error strings.  The disambiguation handler will record the final
    # click when the user picks a candidate (via click_at below).
    if isinstance(result, str) and result.lower().startswith("clicked"):
        context_memory.update(
            last_clicked_element=target_text,
            last_action={"action": "click_text", "target": target_text},
        )
    return result


def right_click_text(target_text: str):
    """Find text on screen and right-click it. Same focus guard and ambiguity
    flow as click_text, but uses the right mouse button for the final click."""

    if not target_text:
        return "I need some text to right-click."

    error = _ensure_focused_app_active()
    if error:
        return error

    result = _click_text(target_text, button="right")
    if isinstance(result, str) and result.lower().startswith("right-clicked"):
        context_memory.update(
            last_clicked_element=target_text,
            last_action={"action": "right_click_text", "target": target_text},
        )
    return result


def click_at(x, y, label: str = "that", button: str = "left") -> str:
    """Click an absolute screen coordinate the user chose during
    disambiguation. Re-focuses the tracked app first: the pick arrives a turn
    after the numbered prompt, by which point typing/speaking the choice has
    likely pulled foreground focus back to this terminal, so we must bring the
    target window forward again before clicking (same reason click_text does).

    Internal only — not in ALLOWED_ACTIONS and never emitted by Qwen; the
    disambiguation handler builds the click_at action directly from a stored
    candidate, so validate_action is intentionally bypassed for it."""

    error = _ensure_focused_app_active()
    if error:
        return error

    result = _click_at(x, y, label, button=button)
    if isinstance(result, str) and result.lower().startswith(("clicked", "right-clicked")):
        context_memory.update(
            last_clicked_element=label,
            last_action={"action": "click_at", "x": x, "y": y, "label": label, "button": button},
        )
    return result


def move_to(x, y, label: str = "that") -> str:
    """Move the cursor to a disambiguation candidate without clicking."""

    error = _ensure_focused_app_active()
    if error:
        return error

    return _move_to(x, y, label)


def type_text(text: str) -> str:
    """Type a string via simulated keyboard input. Refuses if the focused
    app has been closed."""

    if not text:
        return "I need some text to type."

    error = _ensure_focused_app_active()
    if error:
        return error

    result = _type_text(text)
    if isinstance(result, str) and not result.lower().startswith(("error", "couldn't")):
        context_memory.update(
            last_typed_text=text,
            last_action={"action": "type_text", "target": text},
        )
    return result


def press_key(key: str) -> str:
    """Press a single key or key combo (e.g. 'enter', 'ctrl+s'). Refuses
    if the focused app has been closed."""

    if not key:
        return "I need a key to press."

    error = _ensure_focused_app_active()
    if error:
        return error

    return _press_key(key)


def wait(seconds) -> str:
    """Pause execution for the given number of seconds."""

    if seconds in (None, ""):
        return "I need a duration to wait."

    try:
        return _wait(float(seconds))
    except (TypeError, ValueError):
        return f"'{seconds}' isn't a valid wait duration."


def scroll(direction: str) -> str:
    """Scroll the focused window up or down. Unlike click/type/key this
    doesn't hard-fail when no app is tracked — scrolling whatever's in the
    foreground is harmless — but if we ARE tracking a focused app, bring it
    forward first so the wheel lands on it and not this terminal."""

    if not direction:
        return "I need a direction to scroll (up or down)."

    current = get_current_focus_app()
    if current:
        _focus_app(current)  # best-effort; _scroll targets the active window

    return _scroll(direction)


def take_screenshot(name: str = "") -> str:
    """Capture the screen to a PNG in the user's Pictures folder. Needs no
    focused app — it grabs the whole screen."""

    return _take_screenshot(name)


def get_color(target: str = "") -> str:
    """Name a color on screen: the pixel under the cursor (empty/"this"
    target), or the dominant color of a named element ("what color is the
    submit button"). Passive read — desktop_actions.get_color brings the
    tracked app forward itself for the element case, so no focus guard here."""

    return _get_color(target or "")


def switch_app_picker() -> str:
    """Open Windows Task View, equivalent to the three-finger swipe up."""

    return _press_key("win+tab")


# ---------------------------------------------------------------------------
# run_command action with whitelist
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "echo", "dir", "type", "ping", "ipconfig", "hostname",
    "whoami", "ver", "date", "time",
})

# Windows CMD built-in commands that need to be invoked via cmd.exe /c
_CMD_BUILTINS: frozenset[str] = frozenset({
    "echo", "dir", "type", "ver", "date", "time",
})


def run_command(target: str) -> str:
    """Run a whitelisted shell command; confirmation already obtained by gate.
    
    Only commands in ALLOWED_COMMANDS are permitted. Executes with subprocess.run
    (shell=False, timeout=10), captures and truncates output to 500 chars,
    returns specific error messages for blocked commands and timeouts.
    
    Windows built-in commands (echo, dir, type, ver, date, time) are executed
    via cmd.exe /c to work with shell=False.
    """
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

    # Windows CMD built-ins need to be invoked via cmd.exe /c
    if prefix in _CMD_BUILTINS:
        args = ["cmd.exe", "/c"] + args

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
        import traceback
        traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        output = f"Command exited with code {result.returncode}. " + output

    if len(output) > 500:
        output = output[:500] + " ... [output truncated]"

    return output


def move_file(source: str, destination: str) -> str:
    """Move source to destination (confirmation already obtained by the gate).
    Both paths are sandbox-checked before any I/O."""
    src = _resolve_path(source)
    dst = _resolve_path(destination)

    blocked = sandbox.check_path(str(src))
    if blocked:
        return blocked
    blocked = sandbox.check_path(str(dst))
    if blocked:
        return blocked

    if not src.exists():
        return f"I couldn't find '{src}'. Check the file name and try again."

    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        import traceback
        traceback.print_exc()
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Moved '{src}' to '{dst}'."


def execute_action(action: dict) -> str:
    """
    Dispatch a structured action to the right handler.

    Expected shape (this is what Qwen will eventually output):
        {"action": "open_app", "target": "brave"}
        {"action": "open_folder", "target": "downloads"}
        {"action": "focus_app", "target": "brave"}
        {"action": "click_text", "target": "search"}
        {"action": "type_text", "target": "youtube.com"}
        {"action": "press_key", "target": "enter"}
        {"action": "wait", "target": 1}
    """

    action_type = action.get("action")

    if action_type == "open_app":
        return open_app(action.get("target", ""))

    if action_type == "open_folder":
        return open_folder(action.get("target", ""))

    if action_type == "focus_app":
        return focus_app(action.get("target", ""))

    if action_type == "close_app":
        return close_app(action.get("target", ""))

    if action_type == "copy_file":
        return copy_file(action.get("source", ""), action.get("destination", ""))

    if action_type == "move_file":
        return move_file(action.get("source", ""), action.get("destination", ""))

    if action_type == "delete_file":
        return delete_file(action.get("target", ""),
                           bool(action.get("with_contents", False)))

    if action_type == "click_text":
        return click_text(action.get("target", ""))

    if action_type == "right_click_text":
        return right_click_text(action.get("target", ""))

    if action_type == "click_at":
        # Internal action: a disambiguated click on stored coordinates. Not
        # user/LLM-facing (see click_at above) — constructed by the
        # disambiguation handler in agent.process_command.
        return click_at(action.get("x"), action.get("y"),
                        action.get("label", "that"),
                        action.get("button", "left"))

    if action_type == "move_to":
        return move_to(action.get("x"), action.get("y"),
                       action.get("label", "that"))

    if action_type == "type_text":
        return type_text(action.get("target", ""))

    if action_type == "press_key":
        return press_key(action.get("target", ""))

    if action_type == "wait":
        return wait(action.get("target"))

    if action_type == "scroll":
        return scroll(action.get("target", ""))

    if action_type == "screenshot":
        return take_screenshot(action.get("target", ""))

    if action_type == "get_color":
        return get_color(action.get("target", ""))

    if action_type == "switch_app_picker":
        return switch_app_picker()

    if action_type == "run_command":
        return run_command(action.get("target", ""))

    return f"Unknown action: {action_type}"


if __name__ == "__main__":
    # Quick manual test, independent of Qwen.
    print("Action Executor Test")
    print("Type 'app <name>' to open an app, 'folder <name>' to open a folder,")
    print("'focus <name>' to switch focus, 'click <text>' to click text on screen,")
    print("'type <text>' to type text, 'key <key>' to press a key,")
    print("'wait <seconds>' to pause, or 'quit' to exit.\n")

    while True:
        user_input = input("> ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye.")
            break

        if user_input.lower().startswith("app "):
            result = execute_action({"action": "open_app", "target": user_input[4:].strip()})
        elif user_input.lower().startswith("folder "):
            result = execute_action({"action": "open_folder", "target": user_input[7:].strip()})
        elif user_input.lower().startswith("focus "):
            result = execute_action({"action": "focus_app", "target": user_input[6:].strip()})
        elif user_input.lower().startswith("click "):
            result = execute_action({"action": "click_text", "target": user_input[6:].strip()})
        elif user_input.lower().startswith("type "):
            result = execute_action({"action": "type_text", "target": user_input[5:].strip()})
        elif user_input.lower().startswith("key "):
            result = execute_action({"action": "press_key", "target": user_input[4:].strip()})
        elif user_input.lower().startswith("wait "):
            result = execute_action({"action": "wait", "target": user_input[5:].strip()})
        elif user_input.lower().startswith("scroll "):
            result = execute_action({"action": "scroll", "target": user_input[7:].strip()})
        elif user_input.lower() in ("screenshot", "screen"):
            result = execute_action({"action": "screenshot", "target": ""})
        else:
            # No prefix given -> assume it's an app, same as before.
            result = execute_action({"action": "open_app", "target": user_input})

        print(result, "\n")
