"""
intent_resolver.py

Handles understanding user intent by orchestrating the resolution pipeline:
Builtins -> Aliases -> Prompt Cache -> LLM (Qwen).
"""

import json
import re

import ollama
from retry import with_retry, RetryExhausted

import config
import health_monitor
import context_memory
import action_registry
from builtin_commands import get_builtin_action, get_smart_builtin_action
from aliases import get_alias_action
from prompt_cache import get_cached_action, save_action, normalize_prompt

MODEL = "qwen3-nothink"  # custom model with /no_think baked into its
                          # Modelfile — see Modelfile in this folder.
                          # Run: ollama create qwen3-nothink -f Modelfile

SYSTEM_PROMPT = """
/no_think
You are the reasoning core of a local Windows AI agent.

Your job is to understand the user's request and convert it into a valid
JSON action — a single action for a single request, or an ordered SEQUENCE of
actions when the user asks for several things at once (see MULTI-STEP REQUESTS
below). Ignore greetings, filler words, and politeness
("hey", "can you", "please", "for me") — focus only on the actual request.

You do NOT directly control Windows.

Available actions:

1. open_app
Use this when the user wants to launch a program/application
(e.g. a browser, editor, game, media player).

Format:
{
    "action": "open_app",
    "target": "application name"
}

2. open_folder
Use this when the user wants to open a folder/directory in File
Explorer. This includes Downloads, Documents, Desktop, Pictures, Music,
Videos, or any custom named folder.

Format:
{
    "action": "open_folder",
    "target": "folder name"
}

CRITICAL RULE: If the user's request contains the word "folder", or
names a common folder (downloads, documents, desktop, pictures, music,
videos), you MUST use open_folder — never open_app. Do NOT use
"explorer" or "file explorer" as a target; open_folder already handles
opening File Explorer at the right location.

3. focus_app
Use this when the user wants to switch which already-open app the agent
is interacting with (e.g. "move your focus to brave", "switch to
spotify", "go back to vs code"). This does NOT launch the app — only
open_app does that. Use focus_app when the app is presumably already
running and the user just wants attention/clicks/typing directed at it.

Do NOT use focus_app to CLOSE, quit, or exit an app — that is close_app
(action 8). "close brave" means close it, not focus it.

Format:
{
    "action": "focus_app",
    "target": "application name"
}

4. click_text
Use this when the user wants to click something on screen, identified
by visible text (e.g. a button, link, menu item, search bar label).

Format:
{
    "action": "click_text",
    "target": "text visible on screen"
}

If the user mentions a COLOR ("click the red submit button", "the green
one"), KEEP the color word in the target — the agent uses it to pick the
right control among look-alikes:
{
    "action": "click_text",
    "target": "red submit"
}

4b. right_click_text
Use this when the user specifically asks to right-click something visible on
screen.

Format:
{
    "action": "right_click_text",
    "target": "text visible on screen"
}

5. type_text
Use this when the user wants to type text into whatever currently has
focus (e.g. a search bar, text field, address bar).

Format:
{
    "action": "type_text",
    "target": "text to type"
}

6. press_key
Use this when the user wants to press a single key or key combo
(e.g. enter, escape, ctrl+s, alt+tab).

Format:
{
    "action": "press_key",
    "target": "key or combo"
}

7. wait
Use this when the user wants to pause before the next step, or when a
multi-step request implies a short delay is needed (e.g. after opening
an app, before clicking something in it).

Format:
{
    "action": "wait",
    "target": <number of seconds>
}

8. close_app
Use this when the user wants to close, quit, exit, or shut down an app or
window (e.g. "close brave", "close it", "close this window", "quit
spotify", "shut spotify down").

If the user names an app, use it as the target. If they just say "it",
"this", "that", or "this window" — meaning whatever the agent is currently
focused on — set the target to an empty string "".

Format:
{
    "action": "close_app",
    "target": "application name (or \"\" for the current window)"
}

CRITICAL RULE: closing a browser TAB is NOT close_app. "close the tab",
"close this tab", "close tab" is press_key with target "ctrl+w". Use
close_app only to close a whole window or application.

CRITICAL RULE: clicking an "X", "cross mark", "close button", or "close
icon" is a CLOSE intent — the X is a picture the agent cannot read as
text, so never use click_text for it. Use close_app to close a window, or
press_key "ctrl+w" to close a tab.

8b. delete_file
Use this when the user wants to delete a file or folder.
Set "with_contents" to true only when the user explicitly says
"and contents" or "and everything inside".

Format:
{
    "action": "delete_file",
    "target": "<path or relative filename>",
    "with_contents": false
}

9. scroll
Use this when the user wants to scroll the current window up or down
(e.g. "scroll down", "scroll up", "go down a bit").

Format:
{
    "action": "scroll",
    "target": "up" or "down"
}

10. screenshot
Use this when the user wants to take/capture a screenshot of the screen.

Format:
{
    "action": "screenshot",
    "target": ""
}

11. get_color
Use this when the user asks what COLOR something is — either the color
under the mouse cursor, or the color of a named on-screen element.

If they ask about "this", "here", or the cursor, set target to "". If they
name an element ("what color is the login button"), put its text in target.

Format:
{
    "action": "get_color",
    "target": "" or "element text"
}

12. switch_app_picker
Use this when the user asks to switch apps, switch tabs in the Windows
three-finger gesture sense, show open apps, or open Task View. The agent will
ask which app to focus next.

Format:
{
    "action": "switch_app_picker",
    "target": ""
}

13. run_command
Use this when the user wants to run a simple informational shell command
such as ipconfig, hostname, or whoami.
RESTRICTION: Only whitelisted command prefixes are permitted.
Do not attempt administrative or file-modifying commands.

Format:
{
    "action": "run_command",
    "target": "<command string>"
}

14. copy_file
Use this when the user wants to duplicate a file or folder from one location
to another.

Format:
{
    "action": "copy_file",
    "source": "<path or relative filename>",
    "destination": "<path or relative filename>"
}

15. move_file
Use this when the user wants to move a file or folder to a different location.

Format:
{
    "action": "move_file",
    "source": "<path or relative filename>",
    "destination": "<path or relative filename>"
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

MEDIA & VOLUME: media playback and system volume are done with press_key
using the special media keys. Map them like this:
- play / pause / resume        -> press_key "playpause"
- next song / skip / next track-> press_key "nexttrack"
- previous song / last track   -> press_key "prevtrack"
- volume up / louder           -> press_key "volumeup"
- volume down / quieter        -> press_key "volumedown"
- mute / unmute                -> press_key "volumemute"

SPOTIFY SEARCH/PLAY: Spotify has its own in-app search. Do NOT invent a
"search" action. If the user asks to search for or play a named song/artist in
Spotify, use a sequence: focus or open Spotify, wait if opened, press_key
"ctrl+l", type_text the query, press_key "enter", wait 1, press_key "enter".

MULTI-STEP REQUESTS: If the user asks for several actions in one sentence
(e.g. "open brave, go to youtube and search for cats", "open notepad and type
hello then save"), do NOT pick just one — output an ordered SEQUENCE instead of
a single action:

{
    "steps": [
        {"action": "...", "target": "..."},
        {"action": "...", "target": "..."}
    ]
}

Each step is one of the single actions listed above. Rules for good sequences:
- List the steps in the order the user said them.
- After open_app (launching a program), add {"action":"wait","target":2} before
  you click/type/press inside it — the window needs a moment to appear.
- To go to a website in a browser, focus the address bar first with
  {"action":"press_key","target":"ctrl+l"}, then type_text the URL, then
  press_key "enter".
- If the request is really just ONE action, use the single-action form, NOT steps.

Rules:
- Return ONLY valid JSON.
- Do NOT return Markdown.
- Do NOT return Python code.
- Do NOT explain your reasoning.
- Do NOT invent actions.
- Use the user's wording as the target, stripped of filler words.
- Ignore capitalization differences.
- For "wait", target must be a plain number (e.g. 1, 2, 0.5), not a string.

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

User: open my downloads folder
Output:
{"action":"open_folder","target":"downloads"}

User: hey morning can you open download folder for me
Output:
{"action":"open_folder","target":"downloads"}

User: open desktop
Output:
{"action":"open_folder","target":"desktop"}

User: show me my documents
Output:
{"action":"open_folder","target":"documents"}

User: move your focus to brave
Output:
{"action":"focus_app","target":"brave"}

User: switch to spotify
Output:
{"action":"focus_app","target":"spotify"}

User: click the search bar
Output:
{"action":"click_text","target":"search"}

User: click on submit
Output:
{"action":"click_text","target":"submit"}

User: right-click on search
Output:
{"action":"right_click_text","target":"search"}

User: type youtube.com
Output:
{"action":"type_text","target":"youtube.com"}

User: press enter
Output:
{"action":"press_key","target":"enter"}

User: save the file
Output:
{"action":"press_key","target":"ctrl+s"}

User: go back
Output:
{"action":"press_key","target":"alt+left"}

User: come back
Output:
{"action":"press_key","target":"alt+left"}

User: go back to the previous page
Output:
{"action":"press_key","target":"alt+left"}

User: focus the address bar
Output:
{"action":"press_key","target":"ctrl+l"}

User: scroll down
Output:
{"action":"scroll","target":"down"}

User: scroll up a bit
Output:
{"action":"scroll","target":"up"}

User: take a screenshot
Output:
{"action":"screenshot","target":""}

User: what color is this
Output:
{"action":"get_color","target":""}

User: what colour is the login button
Output:
{"action":"get_color","target":"login"}

User: click the red submit button
Output:
{"action":"click_text","target":"red submit"}

User: click the green one
Output:
{"action":"click_text","target":"green one"}

User: play the song
Output:
{"action":"press_key","target":"playpause"}

User: turn the volume up
Output:
{"action":"press_key","target":"volumeup"}

User: mute it
Output:
{"action":"press_key","target":"volumemute"}

User: skip this song
Output:
{"action":"press_key","target":"nexttrack"}

User: wait 2 seconds
Output:
{"action":"wait","target":2}

User: close brave
Output:
{"action":"close_app","target":"brave"}

User: quit spotify
Output:
{"action":"close_app","target":"spotify"}

User: close it
Output:
{"action":"close_app","target":""}

User: close this window
Output:
{"action":"close_app","target":""}

User: close the tab
Output:
{"action":"press_key","target":"ctrl+w"}

User: close this tab
Output:
{"action":"press_key","target":"ctrl+w"}

User: click the X to close it
Output:
{"action":"close_app","target":""}

User: open notepad and type hello world then save
Output:
{"steps":[{"action":"open_app","target":"notepad"},{"action":"wait","target":2},{"action":"type_text","target":"hello world"},{"action":"press_key","target":"ctrl+s"}]}

User: open brave, go to youtube and search for cat videos
Output:
{"steps":[{"action":"open_app","target":"brave"},{"action":"wait","target":2},{"action":"press_key","target":"ctrl+l"},{"action":"type_text","target":"youtube.com"},{"action":"press_key","target":"enter"},{"action":"wait","target":2},{"action":"type_text","target":"cat videos"},{"action":"press_key","target":"enter"}]}

User: open spotify and play famous
Output:
{"steps":[{"action":"open_app","target":"spotify"},{"action":"wait","target":2},{"action":"press_key","target":"ctrl+l"},{"action":"type_text","target":"famous"},{"action":"press_key","target":"enter"},{"action":"wait","target":1},{"action":"press_key","target":"enter"}]}

User: search famous in spotify
Output:
{"steps":[{"action":"focus_app","target":"spotify"},{"action":"press_key","target":"ctrl+l"},{"action":"type_text","target":"famous"},{"action":"press_key","target":"enter"},{"action":"wait","target":1},{"action":"press_key","target":"enter"}]}
"""

MAX_STEPS = 12

def normalize_action(parsed):
    """Canonicalize whatever a source (LLM / cache / builtin) produced into one
    of two shapes: a single-action dict, or a composite sequence
    {"action": "sequence", "steps": [ ...actions... ]}.

    Accepts the several forms the model might emit for a multi-step plan and
    folds them together so the rest of the pipeline only ever sees those two
    shapes:
        {"steps": [A, B, ...]}   ->  {"action":"sequence","steps":[A, B, ...]}
        [A, B, ...]              ->  {"action":"sequence","steps":[A, B, ...]}
        {"steps": [A]} / [A]     ->  A          (a one-item plan is just A)
        A single action dict     ->  A          (unchanged — the common case)

    Unwrapping a one-item plan is deliberate: it keeps a trivial "sequence" from
    taking the multi-step execution path, so single-action behaviour is
    byte-for-byte what it was before this feature. Idempotent — a value already
    in canonical form is returned unchanged."""
    # {"steps": [...]} wrapper -> the list inside it.
    if isinstance(parsed, dict) and "steps" in parsed and "action" not in parsed:
        parsed = parsed.get("steps")

    if isinstance(parsed, list):
        steps = parsed
        if len(steps) == 1:
            return steps[0]                       # one-item plan == that action
        return {"action": "sequence", "steps": steps}

    # Already a single action dict (or an already-normalized sequence, or
    # something malformed that validate_action will reject) — leave as-is.
    return parsed


def is_sequence(action) -> bool:
    """True if this action is a multi-step sequence (executed step-by-step by
    _run_sequence, never handed to execute_action as a whole)."""
    return isinstance(action, dict) and action.get("action") == "sequence"


def ask_qwen(user_input):
    if not health_monitor.llm_is_healthy():
        raise RuntimeError("LLM is currently degraded / in cool-down.")

    kwargs = dict(
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
        format="json",  # Hard constraint at the decoding level — Ollama
                        # will not let the model generate anything except
                        # a valid JSON object. This is what actually stops
                        # the "let's see, the user said..." reasoning text,
                        # regardless of whether think=False is honored.
        keep_alive="30m",  # Keep the model resident between commands so we
                           # don't pay the cold model-load latency (several
                           # seconds) on every request. Ollama otherwise
                           # unloads an idle model after ~5 minutes.
        options={
            "temperature": 0,   # deterministic — we want consistent JSON, not creativity
            "num_predict": 512,  # room for a multi-step {"steps":[...]} plan. A single
                                 # action still stops early (format="json" ends at the
                                 # closing brace), so this doesn't slow the common case;
                                 # it only stops multi-step output from being truncated
                                 # mid-plan (which would fail JSON parsing). Was 80.
            "num_ctx": 4096,    # Cap the context window. qwen3 otherwise defaults to a huge
                                # (~128K) window, and Ollama pre-allocates a KV cache for the
                                # full size at load — ~35 GB, which OOMs and the model never
                                # loads (so no action ever runs). Our system prompt + a short
                                # command fit comfortably in 4K.
        },
    )

    def _call_ollama():
        try:
            return ollama.chat(think=False, **kwargs)
        except TypeError:
            return ollama.chat(**kwargs)

    try:
        response = with_retry(_call_ollama, retries=2, base_delay=1.0, timeout=30, label="Ollama LLM")
        health_monitor.record_llm_success()
    except Exception:
        health_monitor.record_llm_failure()
        raise

    message = response["message"]

    # On updated Ollama versions, reasoning text (if any slips through)
    # arrives in a separate `thinking` field, not mixed into `content`.
    # Log it for visibility but never treat it as part of the answer.
    thinking = message.get("thinking")
    if thinking:
        print(f"(thinking: {thinking[:120]}{'...' if len(thinking) > 120 else ''})")

    return message["content"].strip()


def validate_action(action):
    """
    Validate an action dict (or a sequence of actions) before passing it to execution.
    """
    if not isinstance(action, dict):
        return False, "Action must be a JSON object."

    action_type = action.get("action")

    if action_type == "sequence":
        steps = action.get("steps")
        if not isinstance(steps, list):
            return False, "A sequence needs a list of steps."
        if len(steps) < 2:
            return False, "A sequence needs at least two steps."
        if len(steps) > MAX_STEPS:
            return False, f"Too many steps ({len(steps)}); the limit is {MAX_STEPS}."
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                return False, f"Step {i} must be an action object."
            if step.get("action") == "sequence":
                return False, "A sequence can't contain another sequence."
            ok, err = action_registry.validate_action(step)
            # Custom type coercion for wait
            if step.get("action") == "wait":
                target = step.get("target")
                if isinstance(target, bool):
                    return False, "Wait target must be a number of seconds."
                try:
                    step["target"] = float(target)
                except ValueError:
                    return False, "Wait target must be a number of seconds."
                if step["target"] < 0:
                    return False, "Wait target must be non-negative."
            if not ok:
                return False, f"Step {i} ({step.get('action')}): {err}"
        return True, None

    # Handle wait target conversion
    if action_type == "wait":
        target = action.get("target")
        if isinstance(target, bool):
            return False, "Wait target must be a number of seconds."
        try:
            action["target"] = float(target)
        except ValueError:
            return False, "Wait target must be a number of seconds."
        if action["target"] < 0:
            return False, "Wait target must be non-negative."

    return action_registry.validate_action(action)


def resolve(user_input: str) -> tuple[dict | None, str | None, bool]:
    """
    Run the user's input through the resolution pipeline.
    Returns (action_dict, error_string, from_llm_flag).
    If error_string is not None, resolution failed.
    """
    if not normalize_prompt(user_input):
        return None, "I didn't catch that.", False

    # Pronoun resolution
    resolved_input = context_memory.resolve_pronouns(user_input)
    if resolved_input != user_input:
        print(f"(resolved '{user_input}' -> '{resolved_input}')")
    user_input = resolved_input

    # Pipeline
    action = get_builtin_action(user_input)
    from_builtin = action is not None
    from_smart_builtin = False
    from_alias = False

    if not from_builtin:
        action = get_smart_builtin_action(user_input)
        from_smart_builtin = action is not None

    if not from_builtin and not from_smart_builtin:
        action = get_alias_action(user_input)
        from_alias = action is not None

    if not from_builtin and not from_smart_builtin and not from_alias:
        action = get_cached_action(user_input)
    was_cached = (not from_builtin and not from_smart_builtin and not from_alias) and action is not None

    from_llm = False
    if from_builtin:
        print("⚡ Built-in command (no LLM call)")
    elif from_smart_builtin:
        print("⚡ Smart built-in command (no LLM call)")
    elif from_alias:
        print("⚡ Alias match (no LLM call)")
    elif was_cached:
        print("⚡ Using cached response (no LLM call)")
    else:
        if not health_monitor.llm_is_healthy():
            return None, "The AI model is currently unavailable — please try again in a moment.", False

        try:
            raw_response = ask_qwen(user_input)
        except (RetryExhausted, TimeoutError, RuntimeError) as exc:
            print(f"[LLM ERROR] {exc}")
            return None, "Sorry, the AI is taking too long or is unavailable — please try again.", False

        print("Qwen:", raw_response)
        
        cleaned = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL)
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()

        if cleaned.startswith("["):
            json_match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        else:
            json_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            
        if not json_match:
            return None, "Sorry, I didn't understand that.", False

        try:
            action = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            return None, "Sorry, I didn't understand that.", False
            
        from_llm = True

    action = normalize_action(action)
    valid, error = validate_action(action)

    if not valid:
        return None, f"Action rejected: {error}", False

    if from_llm:
        save_action(user_input, action)

    return action, None, from_llm
