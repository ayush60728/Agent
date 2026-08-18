"""
actions.py

Takes a structured action (as Qwen will eventually produce) and executes it.

Currently supports:
    open_app -> resolves the app via app_resolver.find_app() and launches it.

Handles both kinds of results find_app() can return:
    - a normal filesystem path (.exe / .lnk / .url)  -> os.startfile(path)
    - a "shell:AppsFolder\\<AppID>" identifier (UWP/Store apps) -> os.startfile(identifier)
"""

import os

from app_resolver import find_app


def open_app(app_name: str) -> str:
    """Resolve and launch an application by name."""

    if not app_name:
        return "I need an app name to open."

    path = find_app(app_name)

    if not path:
        return f"I couldn't find {app_name} on this computer."

    try:
        os.startfile(path)
        return f"Opened {app_name}."

    except FileNotFoundError:
        return f"I found {app_name}, but the path no longer exists: {path}"

    except OSError as e:
        return f"I found {app_name}, but couldn't open it: {e}"


def execute_action(action: dict) -> str:
    """
    Dispatch a structured action to the right handler.

    Expected shape (this is what Qwen will eventually output):
        {"action": "open_app", "target": "brave"}
    """

    action_type = action.get("action")

    if action_type == "open_app":
        return open_app(action.get("target", ""))

    return f"Unknown action: {action_type}"


if __name__ == "__main__":
    # Quick manual test, independent of Qwen.
    print("Action Executor Test")
    print("Type an app name to open it, or 'quit' to exit.\n")

    while True:
        user_input = input("Open app: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye.")
            break

        result = execute_action({"action": "open_app", "target": user_input})
        print(result, "\n")