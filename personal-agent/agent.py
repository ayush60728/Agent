import argparse
import json
import re
import sys
import ollama

from actions import execute_action
from builtin_commands import get_builtin_action
from prompt_cache import get_cached_action, save_action, forget_prompt, normalize_prompt
import confirmation
import disambiguation


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

MEDIA & VOLUME: media playback and system volume are done with press_key
using the special media keys. Map them like this:
- play / pause / resume        -> press_key "playpause"
- next song / skip / next track-> press_key "nexttrack"
- previous song / last track   -> press_key "prevtrack"
- volume up / louder           -> press_key "volumeup"
- volume down / quieter        -> press_key "volumedown"
- mute / unmute                -> press_key "volumemute"

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
    "get_color",
    "wait",
}


# Upper bound on how many steps a single multi-step request may expand into.
# A guard against a runaway plan (a confused model emitting dozens of steps),
# not a limit anyone should hit in normal use — real chained commands are a
# handful of steps.
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

    # A multi-step sequence: validate the wrapper here (length + each step),
    # BEFORE the single-action ALLOWED_ACTIONS check below — "sequence" is
    # deliberately NOT in ALLOWED_ACTIONS, so a step that is itself a "sequence"
    # is rejected by the recursive call (no nesting).
    if action_type == "sequence":
        steps = action.get("steps")
        if not isinstance(steps, list):
            return False, "A sequence needs a list of steps."
        if len(steps) < 2:
            # A 0/1-step "sequence" shouldn't exist post-normalization; if one
            # slips through, it's malformed rather than a real multi-step plan.
            return False, "A sequence needs at least two steps."
        if len(steps) > MAX_STEPS:
            return False, f"Too many steps ({len(steps)}); the limit is {MAX_STEPS}."
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                return False, f"Step {i} must be an action object."
            if step.get("action") == "sequence":
                return False, "A sequence can't contain another sequence."
            ok, err = validate_action(step)
            if not ok:
                return False, f"Step {i} ({step.get('action')}): {err}"
        return True, None

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

    if action_type == "get_color":
        # Target is optional: empty means "the color under the cursor". If a
        # target is given (an element to read), it must be text.
        target = action.get("target")

        if target is not None and not isinstance(target, str):
            return False, "Color target must be text."

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


def _run_sequence(steps, _prefix=None):
    """Execute a multi-step plan in order, one step at a time, and return a
    single aggregated reply string.

    Each step goes through the ordinary single-action execute_action, so every
    step behaves exactly as it would as a standalone command. Two things make
    this more than a for-loop:

      * Ambiguous click ("ask & resume"): if a click step finds 2+ rival matches
        it comes back as an AmbiguousClick. We arm the disambiguation gate with
        the REMAINING steps as its resume tail and return the numbered prompt.
        The user's pick (handled at the top of process_command) clicks the chosen
        match and then calls _run_sequence again on that tail — so the rest of the
        plan continues after the pick, and a later ambiguous click simply re-arms.

      * _prefix carries the result lines of steps that already ran in an earlier
        turn (e.g. the resolved click), so the resumed reply reads as one whole.

    Destructive steps are NOT re-confirmed here: a sequence containing any
    destructive step is confirmed as a whole batch up front (see
    process_command), so by the time we run the steps the user has already said
    yes to all of them."""
    results = list(_prefix or [])
    for i, step in enumerate(steps):
        print("⚙️ Executing (step):", step)
        result = execute_action(step)

        if isinstance(result, disambiguation.AmbiguousClick):
            # Suspend the plan: ask which match, and stash everything AFTER this
            # click so the pick can resume from there.
            disambiguation.arm(result, resume_steps=steps[i + 1:])
            prompt = disambiguation.prompt_for(result)
            if results:
                return "Done so far: " + "; ".join(results) + ". " + prompt
            return prompt

        results.append(str(result))

    return "; ".join(results)


def _dispatch(action):
    """Run a resolved action and return a reply string, handling both shapes:
    a multi-step sequence (via _run_sequence) or a single action (via
    execute_action, arming the disambiguation gate if the click is ambiguous).

    For a single action this reproduces the old inline main-path behaviour
    exactly, so nothing about single commands changes."""
    if is_sequence(action):
        return _run_sequence(action["steps"])

    result = execute_action(action)
    if isinstance(result, disambiguation.AmbiguousClick):
        disambiguation.arm(result)
        return disambiguation.prompt_for(result)
    return result


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

        # Ignore blank / punctuation-only input before anything else. A bare
        # wake word transcribed as "agent." leaves just ".", which normalizes
        # to the empty string — never run that through builtins, the cache, or
        # Qwen (an empty prompt sent to Qwen is how a bogus "" -> open brave
        # cache entry got born in the first place). Any pending confirmation or
        # disambiguation stays armed: an empty utterance is neither a yes/no
        # nor a pick, so we simply don't consume it.
        if not normalize_prompt(user_input):
            _pet("idle")
            return "I didn't catch that."

        # 0. If a destructive action is armed and waiting for confirmation,
        #    THIS input is the yes/no answer — interpret it here, before the
        #    classifier ever sees it (so "yes"/"no" never get sent to Qwen or
        #    saved to the prompt cache as if they were commands).
        if confirmation.is_pending():
            decision = confirmation.interpret(user_input)
            if decision == "confirm":
                action = confirmation.take()
                print("⚙️ Executing (confirmed):", action)
                result = _dispatch(action)
                _pet("idle")
                return result + confirmation.recovery_hint(action)
            if decision == "cancel":
                action = confirmation.pending_action()
                confirmation.clear()
                _pet("idle")
                return (f"Okay, I won't {confirmation.describe(action)}."
                        if action else "Okay, cancelled.")
            # "unrelated": the user said something that isn't a yes/no — drop
            # the stale pending action (better than holding a loaded close) and
            # fall through to handle this input as a brand-new command.
            confirmation.clear()

        # 0b. If a click is awaiting disambiguation, THIS input is the pick (a
        #     number / position / color). Resolve it here, before the classifier,
        #     so a selection word like "two" or "top" can never collide with a
        #     builtin command or be sent to Qwen.
        if disambiguation.is_pending():
            ambig = disambiguation.pending()
            choice = disambiguation.interpret(user_input, ambig.candidates)
            if isinstance(choice, int):
                cand = ambig.candidates[choice]
                # Read the sequence tail (if this click was mid-sequence) BEFORE
                # clearing — clear() drops it.
                resume_steps = disambiguation.pending_resume_steps()
                disambiguation.clear()
                action = {"action": "click_at", "x": cand["x"], "y": cand["y"],
                          "label": disambiguation.describe_choice(ambig, choice)}
                print("⚙️ Executing (chosen):", action)
                result = execute_action(action)
                # "Ask & resume": if this click was one step of a sequence, carry
                # on with the steps that came after it. _run_sequence re-arms this
                # same gate if a later step is ambiguous too.
                if resume_steps:
                    result = _run_sequence(resume_steps, _prefix=[str(result)])
                _pet("idle")
                return result
            if choice == "cancel":
                disambiguation.clear()
                _pet("idle")
                return "Okay, cancelled."
            # "unrelated": not a pick — drop the pending selection and treat
            # this input as a brand-new command (fall through).
            disambiguation.clear()

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
            # then pull out just the JSON value — belt-and-suspenders in case
            # any reasoning prose sneaks in around it despite format="json".
            cleaned = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL)
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

            # It's normally a single {...} object, but a multi-step plan can
            # arrive as a top-level [...] array — branch on the leading char. A
            # greedy \{.*\} would otherwise swallow an array's inner objects and
            # drop the surrounding brackets, yielding invalid JSON.
            if cleaned.startswith("["):
                json_match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
            else:
                json_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON value found", cleaned, 0)

            action = json.loads(json_match.group(0))

        # Fold whatever the source produced (single dict, {"steps":[...]}, or a
        # bare [...] list) into our two canonical shapes: a single action, or a
        # {"action":"sequence","steps":[...]} composite. Idempotent, so a cached
        # sequence or a builtin single action passes straight through.
        action = normalize_action(action)

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

        # 4. Destructive actions (close a window/tab/app) don't run on the
        #    spot — a misheard command could kill an app in under a second.
        #    Arm it here and ask for a yes/no; the NEXT command resolves it
        #    (see the pending-confirmation check at the top of this function).
        #    Gating here — after every source has produced its action —
        #    covers builtins, cache hits, and the LLM alike.
        if confirmation.needs_confirmation(action):
            confirmation.arm(action)
            _pet("idle")
            return confirmation.prompt_for(action)

        print("⚙️ Executing:", action)

        # Dispatch through the runner: a single action executes exactly as
        # before; a sequence runs step-by-step. Both arm the disambiguation gate
        # on an ambiguous click (a mid-sequence click also stashes its resume
        # tail), keeping this function's return type a plain string for every
        # caller.
        result = _dispatch(action)

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
        # ...but not while a destructive action is awaiting yes/no, or a click
        # is awaiting a pick: there, "stop" means "cancel that", not "exit the
        # agent", so let it through to process_command's handlers.
        if (user_input.lower() in EXIT_WORDS
                and not confirmation.is_pending()
                and not disambiguation.is_pending()):
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
        # If a destructive action is armed (yes/no) or a click is awaiting a
        # pick, this utterance is the answer — route it straight to the
        # pipeline's handlers, ahead of the exit/forget shortcuts, so
        # "stop"/"forget it" here read as "cancel that" rather than "exit voice
        # mode" / "forget a cached prompt".
        if confirmation.is_pending() or disambiguation.is_pending():
            return process_command(command_text, write_pet_state=_pet_state)

        if command_text.lower().strip() in VOICE_EXIT_WORDS:
            # Voice mode doesn't have a clean way to break its own loop
            # from in here; just let it keep listening but say goodbye.
            return "Okay, but I'm still listening — close this window to fully stop."

        if command_text.lower().startswith("forget "):
            forget_prompt(command_text[7:].strip())
            return "Forgotten."

        return process_command(command_text, write_pet_state=_pet_state)

    voice_io.voice_loop(on_command)


def _launch_video_pet():
    """Start the looping corner video pet (video_pet.py) as a background
    process. Best-effort: the agent must still run if this fails (missing file,
    no display, PyAV not installed, ...). Uses pythonw.exe when available so the
    pet doesn't spawn its own console window."""
    import os
    import subprocess

    base = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base, "video_pet.py")
    if not os.path.exists(script):
        return
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    try:
        # Pass our pid so the (now non-interactive, un-closeable) pet exits when
        # the agent does.
        subprocess.Popen([exe, script, "--parent-pid", str(os.getpid())], cwd=base)
    except OSError:
        pass


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

    # Kick off the looping corner video pet as soon as the agent starts (its
    # audio plays once; the visuals loop). Best-effort — never blocks the agent.
    _launch_video_pet()

    if args.voice:
        if args.mic is not None:
            import voice_io
            voice_io.MIC_DEVICE_INDEX = args.mic
        run_voice_mode()
    else:
        run_text_mode()


if __name__ == "__main__":
    main()