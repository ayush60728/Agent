import argparse
import json
import re
import sys
import ollama

from actions import execute_action
from builtin_commands import get_builtin_action
from prompt_cache import get_cached_action, save_action, forget_prompt


MODEL = "qwen3-nothink"  # custom model with /no_think baked into its
                          # Modelfile — see Modelfile in this folder.
                          # Run: ollama create qwen3-nothink -f Modelfile


SYSTEM_PROMPT = """
/no_think
You are the reasoning core of a local Windows AI agent.

Your job is to understand the user's request and convert it into
ONE valid JSON action. Ignore greetings, filler words, and politeness
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

MEDIA & VOLUME: media playback and system volume are done with press_key
using the special media keys. Map them like this:
- play / pause / resume        -> press_key "playpause"
- next song / skip / next track-> press_key "nexttrack"
- previous song / last track   -> press_key "prevtrack"
- volume up / louder           -> press_key "volumeup"
- volume down / quieter        -> press_key "volumedown"
- mute / unmute                -> press_key "volumemute"

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
"""


ALLOWED_ACTIONS = {
    "open_app",
    "open_folder",
    "focus_app",
    "close_app",
    "click_text",
    "type_text",
    "press_key",
    "scroll",
    "screenshot",
    "wait",
}


def ask_qwen(user_input):
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
            "num_predict": 80,  # small headroom above the old 60; format=json adds a little overhead
            "num_ctx": 4096,    # Cap the context window. qwen3 otherwise defaults to a huge
                                # (~128K) window, and Ollama pre-allocates a KV cache for the
                                # full size at load — ~35 GB, which OOMs and the model never
                                # loads (so no action ever runs). Our system prompt + a short
                                # command fit comfortably in 4K.
        },
    )

    try:
        # Belt-and-suspenders: also try to skip the internal reasoning pass
        # on models/versions that support it directly, for extra speed.
        response = ollama.chat(think=False, **kwargs)
    except TypeError:
        # Installed ollama package is too old to accept `think=`.
        # format="json" + the Modelfile's baked-in /no_think still do the
        # heavy lifting either way.
        response = ollama.chat(**kwargs)

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
    Make sure Qwen returned an action that our agent actually supports.
    """

    if not isinstance(action, dict):
        return False, "Action must be a JSON object."

    action_type = action.get("action")

    if action_type not in ALLOWED_ACTIONS:
        return False, f"Action '{action_type}' is not allowed."

    if action_type in ("open_app", "open_folder", "focus_app", "click_text", "type_text", "press_key", "scroll"):
        target = action.get("target")

        if not isinstance(target, str) or not target.strip():
            return False, "Target is missing."

    if action_type == "screenshot":
        # Target is optional (an output filename). If present, it must be text.
        target = action.get("target")

        if target is not None and not isinstance(target, str):
            return False, "Screenshot target must be text."

    if action_type == "close_app":
        # Target is optional here: an empty target (or a pronoun like "it")
        # means "close the currently-focused app". If a target is given
        # though, it must be text.
        target = action.get("target")

        if target is not None and not isinstance(target, str):
            return False, "Close target must be text."

    if action_type == "wait":
        target = action.get("target")

        if isinstance(target, bool):
            return False, "Wait target must be a number of seconds."

        if isinstance(target, str):
            # Qwen sometimes emits numbers as JSON strings (e.g. "2"
            # instead of 2) — accept that as long as it actually parses.
            try:
                target = float(target)
            except ValueError:
                return False, "Wait target must be a number of seconds."
        elif not isinstance(target, (int, float)):
            return False, "Wait target must be a number of seconds."

        if target < 0:
            return False, "Wait target must be non-negative."

    return True, None


EXIT_WORDS = {"exit", "quit", "stop", "bye"}

# In VOICE mode "stop" must NOT be treated as an exit word — you can't exit
# voice mode by voice anyway (it just says so), and "stop"/"stop music" should
# reach the media builtin. Text mode keeps "stop" as a convenient typed exit.
VOICE_EXIT_WORDS = {"exit", "quit", "bye", "goodbye"}


def process_command(user_input: str, write_pet_state=None) -> str:
    """
    Run one command through the full pipeline (cache -> Qwen -> validate
    -> execute) and return a plain-text result string.

    write_pet_state, if given, is called at each stage (e.g. "thinking",
    "idle") so callers can drive the pet UI's animation. Text mode and
    voice mode both call this — it's the one place the actual agent
    logic lives, so neither mode can drift out of sync with the other.
    """

    def _pet(state):
        if write_pet_state:
            write_pet_state(state)

    try:
        _pet("thinking")

        # 1. Curated builtins first — instant and deterministic (no LLM, no
        #    cache-file read). Covers the hottest commands: scroll, volume,
        #    media, copy/paste, new tab, and so on.
        action = get_builtin_action(user_input)
        from_builtin = action is not None

        # 2. Then the learned prompt cache — skip Qwen if we've classified
        #    this exact phrasing before.
        if not from_builtin:
            action = get_cached_action(user_input)
        was_cached = (not from_builtin) and action is not None

        if from_builtin:
            print("⚡ Built-in command (no LLM call)")
        elif was_cached:
            print("⚡ Using cached response (no LLM call)")
        else:
            raw_response = ask_qwen(user_input)

            print("Qwen:", raw_response)

            # Defensive cleanup: strip <think> blocks / markdown fences,
            # then pull out just the {...} object — belt-and-suspenders
            # in case any reasoning prose sneaks in around the JSON
            # despite format="json".
            cleaned = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL)
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

            json_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON object found", cleaned, 0)

            action = json.loads(json_match.group(0))

        valid, error = validate_action(action)

        if not valid:
            _pet("idle")
            return f"Action rejected: {error}"

        # 3. Only cache a freshly LLM-classified, validated action — never
        #    re-save a cache hit (it bumped its own hit_count already), and
        #    never persist a builtin (the builtin table already catches it,
        #    faster than the cache would).
        if not from_builtin and not was_cached:
            save_action(user_input, action)

        print("⚙️ Executing:", action)

        result = execute_action(action)
        _pet("idle")
        return result

    except json.JSONDecodeError:
        _pet("idle")
        return "Sorry, I didn't understand that."

    except Exception as e:
        _pet("idle")
        return f"Error: {e}"


def run_text_mode():
    print("=" * 50)
    print("🤖 Personal Agent")
    print("=" * 50)
    print("Model:", MODEL)
    print("Type 'exit' or 'quit' to stop.\n")

    while True:

        user_input = input("You: ").strip()

        if not user_input:
            continue

        # Control commands bypass the LLM entirely — there's no reason to
        # send "quit" to Qwen and wait for a JSON classification of it.
        if user_input.lower() in EXIT_WORDS:
            print("Agent stopped.")
            break

        # "forget <phrase>" clears a bad cached classification, in case
        # Qwen got something wrong once and we don't want it repeated.
        if user_input.lower().startswith("forget "):
            forget_prompt(user_input[7:].strip())
            print()
            continue

        result = process_command(user_input)
        print("🤖 Agent:", result)
        print()


def run_voice_mode():
    # Imported lazily so text-mode users don't need voice deps installed
    # (speech_recognition / faster-whisper / pyttsx3 / pyaudio) just to
    # run the agent by keyboard.
    import voice_io

    print("=" * 50)
    print("🤖 Personal Agent — voice mode")
    print("=" * 50)
    print("Model:", MODEL)
    print(f"Say '{voice_io.WAKE_WORD}' to give a command. Ctrl+C to stop.\n")

    def _pet_state(state):
        voice_io._write_state(state)

    def on_command(command_text: str) -> str:
        if command_text.lower().strip() in VOICE_EXIT_WORDS:
            # Voice mode doesn't have a clean way to break its own loop
            # from in here; just let it keep listening but say goodbye.
            return "Okay, but I'm still listening — close this window to fully stop."

        if command_text.lower().startswith("forget "):
            forget_prompt(command_text[7:].strip())
            return "Forgotten."

        return process_command(command_text, write_pet_state=_pet_state)

    voice_io.voice_loop(on_command)


def main():
    # Windows terminals default to a legacy code page (cp1252) that can't
    # encode the emoji / ✓ / ⚠ characters this app prints for status — which
    # otherwise crashes with UnicodeEncodeError mid-command (e.g. when the
    # resolver prints "✓ Found in cache" while executing a voice command).
    # Force UTF-8 so output is safe in any terminal.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Personal Agent")
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Run in voice mode (wake word + speech-to-text + text-to-speech) instead of typed commands.",
    )
    parser.add_argument(
        "--mic",
        type=int,
        default=None,
        metavar="INDEX",
        help="Microphone device index to listen through (see --list-mics). "
             "Default: whatever Windows reports as the default input device.",
    )
    parser.add_argument(
        "--list-mics",
        action="store_true",
        help="List available microphone input devices with their indices, then exit.",
    )
    args = parser.parse_args()

    # Both of these need the audio stack, so import voice_io lazily — typed-mode
    # users shouldn't need speech deps installed just to run the agent.
    if args.list_mics:
        import voice_io
        voice_io.print_input_devices()
        return

    if args.voice:
        if args.mic is not None:
            import voice_io
            voice_io.MIC_DEVICE_INDEX = args.mic
        run_voice_mode()
    else:
        run_text_mode()


if __name__ == "__main__":
    main()