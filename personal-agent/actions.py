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
    click_text  -> finds text on screen (via OCR, scoped to the current
                   focused app's window) and clicks it.
    type_text   -> types a string via keyboard simulation.
    press_key   -> presses a single key or key combo (e.g. "enter", "ctrl+s").
    wait        -> pauses for N seconds between steps.

click_text/type_text/press_key all check that the currently-focused app is
still running before acting — if the user closed it manually, we say so
instead of clicking/typing into whatever happens to be in the foreground.

Handles both kinds of results find_app() can return:
    - a normal filesystem path (.exe / .lnk / .url)  -> os.startfile(path)
    - a "shell:AppsFolder\\<AppID>" identifier (UWP/Store apps) -> os.startfile(identifier)
"""

import os

from app_resolver import find_app
from folder_resolver import find_folder
from desktop_actions import click_text as _click_text
from desktop_actions import type_text as _type_text
from desktop_actions import press_key as _press_key
from desktop_actions import wait as _wait
from desktop_actions import focus_app as _focus_app
from desktop_actions import check_focus_app_running
from desktop_actions import get_current_focus_app


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
        return f"Opened {folder_name}."

    except (FileNotFoundError, OSError):
        path = find_folder(folder_name, force_rescan=True)

        if not path:
            return f"I couldn't find a folder called {folder_name} on this computer."

        try:
            os.startfile(path)
            return f"Opened {folder_name}."
        except OSError as e:
            return f"I found {folder_name}, but couldn't open it: {e}"


def focus_app(app_name: str) -> str:
    """Switch the agent's tracked 'current' app to an already-running app,
    without launching or relaunching it."""

    if not app_name:
        return "I need an app name to focus."

    return _focus_app(app_name)


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


def click_text(target_text: str) -> str:
    """Find text on screen (within the focused app's window) via OCR and
    click its center point. Refuses if the focused app has been closed."""

    if not target_text:
        return "I need some text to click."

    error = _ensure_focused_app_active()
    if error:
        return error

    return _click_text(target_text)


def type_text(text: str) -> str:
    """Type a string via simulated keyboard input. Refuses if the focused
    app has been closed."""

    if not text:
        return "I need some text to type."

    error = _ensure_focused_app_active()
    if error:
        return error

    return _type_text(text)


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

    if action_type == "click_text":
        return click_text(action.get("target", ""))

    if action_type == "type_text":
        return type_text(action.get("target", ""))

    if action_type == "press_key":
        return press_key(action.get("target", ""))

    if action_type == "wait":
        return wait(action.get("target"))

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
        else:
            # No prefix given -> assume it's an app, same as before.
            result = execute_action({"action": "open_app", "target": user_input})

        print(result, "\n")