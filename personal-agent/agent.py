import json
import ollama

from actions import execute_action


MODEL = "qwen3:4b"


SYSTEM_PROMPT = """
You are the reasoning core of a local Windows AI agent.

Your job is to understand the user's request and convert it into
ONE valid JSON action.

You do NOT directly control Windows.

Available actions:

1. open_app

Format:
{
    "action": "open_app",
    "target": "application name"
}

Rules:
- Return ONLY valid JSON.
- Do NOT return Markdown.
- Do NOT return Python code.
- Do NOT explain your reasoning.
- Do NOT invent actions.
- Use the user's application name as the target.
- Ignore capitalization differences.

Examples:

User: open brave
Output:
{"action":"open_app","target":"brave"}

User: launch VS Code
Output:
{"action":"open_app","target":"vs code"}

User: start spotify
Output:
{"action":"open_app","target":"spotify"}
"""


ALLOWED_ACTIONS = {
    "open_app",
}


def ask_qwen(user_input):
    response = ollama.chat(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_input,
            },
        ],
    )

    return response["message"]["content"].strip()


def validate_action(action):
    """
    Make sure Qwen returned an action that our agent actually supports.
    """

    if not isinstance(action, dict):
        return False, "Action must be a JSON object."

    action_type = action.get("action")

    if action_type not in ALLOWED_ACTIONS:
        return False, f"Action '{action_type}' is not allowed."

    if action_type == "open_app":
        target = action.get("target")

        if not isinstance(target, str) or not target.strip():
            return False, "Application name is missing."

    return True, None


def main():

    print("=" * 50)
    print("🤖 Personal Agent")
    print("=" * 50)
    print("Model:", MODEL)
    print("Type 'exit' to quit.\n")

    while True:

        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() == "exit":
            print("Agent stopped.")
            break

        try:

            raw_response = ask_qwen(user_input)

            print("Qwen:", raw_response)

            action = json.loads(raw_response)

            valid, error = validate_action(action)

            if not valid:
                print("❌ Action rejected:", error)
                continue

            print("⚙️ Executing:", action)

            result = execute_action(action)

            print("🤖 Agent:", result)

        except json.JSONDecodeError:
            print("❌ Qwen returned invalid JSON.")

        except Exception as e:
            print("❌ Error:", e)

        print()


if __name__ == "__main__":
    main()