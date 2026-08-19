from app_resolver import find_app


def open_app(app_name):

    path = find_app(app_name)

    if not path:
        return f"I couldn't find '{app_name}' on this computer."

    try:
        import os

        os.startfile(path)

        return f"Opened {app_name}."

    except Exception as e:

        return f"I found '{app_name}', but couldn't open it: {e}"


def execute_action(action):

    action_type = action.get("action")

    if action_type == "open_app":
        return open_app(action.get("target", ""))

    return f"Unknown action: {action_type}"