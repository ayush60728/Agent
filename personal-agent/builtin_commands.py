"""
builtin_commands.py

A curated table of the most common desktop commands, mapped straight to
their action dict — no LLM call, no cache file read.

Why this exists:
    Commands like "scroll down", "volume up", "copy", "new tab" are issued
    constantly and always mean the same thing. Round-tripping them through
    Qwen is both slow (a full LLM decode) and occasionally wrong (the model
    might classify "copy" as click_text). Matching them here first makes
    them instant and deterministic.

Layering (see agent.process_command):
    1. builtin table  (this file — instant, in-memory, curated)
    2. user alias table (aliases.py — personal shortcuts saved by the user)
    3. prompt cache   (learned from past LLM classifications)
    4. Qwen           (anything genuinely novel)

Matching is EXACT after the same light normalization the prompt cache uses
(lowercase, trim, collapse whitespace, strip surrounding punctuation) — so
"Scroll down." and "scroll down" both hit, but we never fuzzy-guess intent.
Anything with extra words ("please scroll down for me") falls through to the
LLM, which strips filler and then caches the result for next time.
"""

import re

from prompt_cache import normalize_prompt


# Each entry: (list of phrasings, action dict). Kept as a list of tuples so
# one action can carry many aliases without repeating the dict; it's expanded
# into BUILTINS (normalized phrase -> action) at import time below.
_COMMANDS = [
    # --- editing / clipboard (press_key combos) ---
    (["select all", "select everything"], {"action": "press_key", "target": "ctrl+a"}),
    (["copy", "copy that", "copy this"], {"action": "press_key", "target": "ctrl+c"}),
    (["cut"], {"action": "press_key", "target": "ctrl+x"}),
    (["paste"], {"action": "press_key", "target": "ctrl+v"}),
    (["undo"], {"action": "press_key", "target": "ctrl+z"}),
    (["redo"], {"action": "press_key", "target": "ctrl+y"}),
    (["save", "save it", "save the file", "save file"], {"action": "press_key", "target": "ctrl+s"}),
    (["find", "find on page", "search the page"], {"action": "press_key", "target": "ctrl+f"}),

    # --- single keys ---
    (["enter", "press enter", "hit enter", "return"], {"action": "press_key", "target": "enter"}),
    (["escape", "press escape", "esc"], {"action": "press_key", "target": "esc"}),
    (["delete", "press delete"], {"action": "press_key", "target": "delete"}),
    (["backspace", "press backspace"], {"action": "press_key", "target": "backspace"}),
    (["tab", "press tab"], {"action": "press_key", "target": "tab"}),
    (["page down"], {"action": "press_key", "target": "pagedown"}),
    (["page up"], {"action": "press_key", "target": "pageup"}),

    # --- browser / window navigation ---
    (["go back", "come back", "back", "previous page",
      "go to the previous page", "go back to the previous page"],
     {"action": "press_key", "target": "alt+left"}),
    (["go forward", "forward", "next page"], {"action": "press_key", "target": "alt+right"}),
    (["refresh", "reload", "refresh the page", "reload the page"],
     {"action": "press_key", "target": "f5"}),
    (["hard refresh", "hard reload"], {"action": "press_key", "target": "ctrl+f5"}),
    (["new tab", "open a new tab", "open new tab"], {"action": "press_key", "target": "ctrl+t"}),
    (["close tab", "close the tab", "close this tab"], {"action": "press_key", "target": "ctrl+w"}),
    (["reopen tab", "reopen closed tab", "reopen last tab", "undo close tab"],
     {"action": "press_key", "target": "ctrl+shift+t"}),
    (["next tab"], {"action": "press_key", "target": "ctrl+tab"}),
    (["previous tab", "prev tab", "last tab"], {"action": "press_key", "target": "ctrl+shift+tab"}),
    (["new window"], {"action": "press_key", "target": "ctrl+n"}),
    (["switch window", "switch windows", "alt tab", "next window"],
     {"action": "press_key", "target": "alt+tab"}),
    (["switch app", "switch apps", "switch tab", "switch tabs",
      "three finger gesture", "show open apps", "task view"],
     {"action": "switch_app_picker", "target": ""}),
    (["address bar", "focus address bar", "focus the address bar", "url bar"],
     {"action": "press_key", "target": "ctrl+l"}),
    (["zoom in"], {"action": "press_key", "target": "ctrl+="}),
    (["zoom out"], {"action": "press_key", "target": "ctrl+-"}),
    (["reset zoom", "actual size"], {"action": "press_key", "target": "ctrl+0"}),

    # --- scrolling ---
    (["scroll down", "scroll", "go down"], {"action": "scroll", "target": "down"}),
    (["scroll up", "go up"], {"action": "scroll", "target": "up"}),
    (["scroll to top", "go to top", "top of page"], {"action": "press_key", "target": "ctrl+home"}),
    (["scroll to bottom", "go to bottom", "bottom of page"],
     {"action": "press_key", "target": "ctrl+end"}),

    # --- media (media keys, verified present in pyautogui.KEYBOARD_KEYS) ---
    (["play", "pause", "play pause", "play or pause", "resume", "play music", "pause music"],
     {"action": "press_key", "target": "playpause"}),
    (["next song", "next track", "skip song"], {"action": "press_key", "target": "nexttrack"}),
    (["previous song", "previous track", "last song"],
     {"action": "press_key", "target": "prevtrack"}),
    (["stop music", "stop the music", "stop playback"], {"action": "press_key", "target": "stop"}),

    # --- volume ---
    (["volume up", "turn it up", "turn up the volume", "louder", "increase volume"],
     {"action": "press_key", "target": "volumeup"}),
    (["volume down", "turn it down", "turn down the volume", "quieter",
      "decrease volume", "lower the volume"],
     {"action": "press_key", "target": "volumedown"}),
    (["mute", "unmute", "mute it", "mute the volume", "toggle mute"],
     {"action": "press_key", "target": "volumemute"}),

    # --- system / window management ---
    (["show desktop", "minimize everything", "hide everything"],
     {"action": "press_key", "target": "win+d"}),
    (["lock", "lock the pc", "lock the computer", "lock screen", "lock my pc"],
     {"action": "press_key", "target": "win+l"}),
    (["task manager", "open task manager"], {"action": "press_key", "target": "ctrl+shift+esc"}),
    (["minimize", "minimize window", "minimize this", "minimize the window"],
     {"action": "press_key", "target": "win+down"}),
    (["maximize", "maximize window", "maximize this", "maximize the window"],
     {"action": "press_key", "target": "win+up"}),
    (["close window", "close this window", "close the window"],
     {"action": "press_key", "target": "alt+f4"}),

    # --- screenshot ---
    (["screenshot", "take a screenshot", "take screenshot", "capture screen",
      "grab a screenshot"], {"action": "screenshot", "target": ""}),

    # --- undo stack ---
    (["undo last action", "undo action", "undo that"], {"action": "undo", "target": ""}),
]


def _build() -> dict:
    table = {}
    for phrasings, action in _COMMANDS:
        for phrase in phrasings:
            table[normalize_prompt(phrase)] = action
    return table


BUILTINS = _build()


def get_builtin_action(prompt: str):
    """Return a copy of the action dict for this exact (normalized) command,
    or None if it's not one of the curated builtins. A copy is returned so a
    caller mutating the result can't corrupt the shared table."""
    action = BUILTINS.get(normalize_prompt(prompt))
    return dict(action) if action is not None else None


_SPOTIFY_FILLER = re.compile(
    r"\b(?:song|music|track|for me|please|on spotify|in spotify)\b",
    re.IGNORECASE,
)


def _spotify_search_steps(query: str, *, open_first: bool = False) -> dict:
    """Build a Spotify search/play plan that avoids OCR.

    Spotify's visible Search button/text can be hard for OCR to distinguish,
    but Ctrl+L focuses Spotify search reliably on the desktop app. Enter runs
    the search, and the second Enter starts the highlighted/top result.
    """
    steps = []
    if open_first:
        steps.extend([
            {"action": "open_app", "target": "spotify"},
            {"action": "wait", "target": 2},
        ])
    else:
        steps.append({"action": "focus_app", "target": "spotify"})

    steps.extend([
        {"action": "press_key", "target": "ctrl+l"},
        {"action": "type_text", "target": query},
        {"action": "press_key", "target": "enter"},
        {"action": "wait", "target": 1},
        {"action": "press_key", "target": "enter"},
    ])
    return {"action": "sequence", "steps": steps}


def _clean_spotify_query(text: str) -> str:
    text = _SPOTIFY_FILLER.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,!?;:\"'()[]")


def _current_focus_is_spotify() -> bool:
    try:
        from desktop_actions import get_current_focus_app
    except Exception:
        return False

    current = get_current_focus_app()
    return bool(current and "spotify" in current.lower())


def get_smart_builtin_action(prompt: str):
    """Dynamic deterministic commands that need a tiny bit of parsing.

    These sit between exact builtins and the LLM. They cover hot voice intents
    where the model has been unreliable, especially Spotify search/play.
    """
    norm = normalize_prompt(prompt)
    if not norm:
        return None

    # "open spotify and play/search <song>" should stay inside Spotify after
    # launch and immediately start the requested result.
    m = re.match(
        r"^(?:open|launch|start)\s+spotify(?:\s+and)?\s+"
        r"(?:play|search(?:\s+for)?)\s+(.+)$",
        norm,
    )
    if m:
        query = _clean_spotify_query(m.group(1))
        if query:
            return _spotify_search_steps(query, open_first=True)

    # Plain "open spotify and play" means open/focus it and toggle playback.
    if re.match(r"^(?:open|launch|start)\s+spotify(?:\s+and)?\s+play$", norm):
        return {"action": "sequence", "steps": [
            {"action": "open_app", "target": "spotify"},
            {"action": "wait", "target": 2},
            {"action": "press_key", "target": "playpause"},
        ]}

    # Once Spotify is the tracked app, "search/play <song>" should not invent
    # an unknown "search" action. Route it to Spotify's keyboard search.
    m = re.match(r"^(?:spotify\s+)?(?:play|search(?:\s+for)?)\s+(.+)$", norm)
    if m and (norm.startswith("spotify ") or _current_focus_is_spotify()):
        query = _clean_spotify_query(m.group(1))
        if query:
            return _spotify_search_steps(query)

    # "search" alone in Spotify focuses the search field without OCR.
    if norm in {"spotify search", "search spotify", "search in spotify"}:
        return {"action": "sequence", "steps": [
            {"action": "focus_app", "target": "spotify"},
            {"action": "press_key", "target": "ctrl+l"},
        ]}

    if norm in {
        "click search",
        "click on search",
        "click the search",
        "click search bar",
        "click on search bar",
        "click the search bar",
        "open search",
        "open spotify search",
    } and ("spotify" in norm or _current_focus_is_spotify()):
        return {"action": "sequence", "steps": [
            {"action": "focus_app", "target": "spotify"},
            {"action": "press_key", "target": "ctrl+l"},
        ]}

    # --- Long-term memory commands ---
    if norm in {"clear all memories", "clear memories", "delete all memories", "wipe memories", "forget all memories"}:
        return {"action": "clear_memories", "target": ""}

    if norm in {"list memories", "show memories", "view memories", "all memories", "show my memories"}:
        return {"action": "list_memories", "target": ""}

    if norm in {"how many memories", "memory count", "how many memories do you have", "count memories"}:
        return {"action": "memory_count", "target": ""}

    m = re.match(r"^(?:remember\s+that|remember|note\s+that|take\s+a\s+note(?:\s+that)?)\s+(.+)$", norm)
    if m:
        fact = m.group(1).strip()
        if fact:
            return {"action": "remember", "target": fact}

    m = re.match(
        r"^(?:what\s+do\s+you\s+know\s+about|what\s+do\s+you\s+remember\s+about|"
        r"what\s+do\s+you\s+remember\s+for|do\s+you\s+remember|recall|"
        r"search\s+memory\s+for|search\s+memories\s+for)\s+(.+)$",
        norm,
    )
    if m:
        query = m.group(1).strip()
        if query:
            return {"action": "recall", "target": query}

    m = re.match(
        r"^(?:forget\s+memory\s+about|forget\s+memory|forget\s+fact\s+about|forget\s+about)\s+(.+)$",
        norm,
    )
    if m:
        query = m.group(1).strip()
        if query:
            return {"action": "forget_memory", "target": query}

    # --- Verbosity commands ---
    if norm in {"set verbosity terse", "verbosity terse", "terse mode", "be terse", "be brief", "short mode", "quiet mode"}:
        return {"action": "set_verbosity", "target": "terse"}

    if norm in {"set verbosity detailed", "verbosity detailed", "detailed mode", "be detailed", "verbose mode", "be verbose", "full mode"}:
        return {"action": "set_verbosity", "target": "detailed"}

    # --- TTS Voice & Speech commands ---
    if norm in {"list voices", "show voices", "available voices", "list tts voices", "all voices"}:
        return {"action": "list_tts_voices", "target": ""}

    m = re.match(r"^(?:set\s+voice\s+to|change\s+voice\s+to|switch\s+voice\s+to|use\s+voice)\s+(.+)$", norm)
    if m:
        voice_name = m.group(1).strip()
        if voice_name:
            return {"action": "set_tts_voice", "target": voice_name}

    if norm in {"speak faster", "talk faster", "speed up speech", "faster speech"}:
        return {"action": "set_tts_rate", "target": "faster"}

    if norm in {"speak slower", "talk slower", "slow down speech", "slower speech"}:
        return {"action": "set_tts_rate", "target": "slower"}

    if norm in {"normal speech", "reset speech rate", "default speech rate"}:
        return {"action": "set_tts_rate", "target": "normal"}

    m = re.match(r"^(?:set\s+speech\s+rate|speech\s+rate|set\s+voice\s+rate|voice\s+rate)\s+(?:to\s+)?(\d+)$", norm)
    if m:
        return {"action": "set_tts_rate", "target": m.group(1)}

    # --- Training commands ---
    if norm in {"training stats", "show training stats", "training info", "how many corrections", "training count"}:
        return {"action": "show_training_stats", "target": ""}

    return None


def get_alias_action(prompt: str):
    """Return the action saved under a user-defined alias for this prompt,
    or None if no alias matches.

    Sits between the curated builtin table and the prompt cache in the
    resolution chain — instant (disk read, no LLM), and user-managed
    rather than auto-learned.  A copy is returned by aliases.get_alias_action
    already, so callers can mutate it freely.
    """
    from aliases import get_alias_action as _lookup
    return _lookup(prompt)
