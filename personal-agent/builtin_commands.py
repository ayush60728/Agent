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
    2. prompt cache   (learned from past LLM classifications)
    3. Qwen           (anything genuinely novel)

Matching is EXACT after the same light normalization the prompt cache uses
(lowercase, trim, collapse whitespace, strip surrounding punctuation) — so
"Scroll down." and "scroll down" both hit, but we never fuzzy-guess intent.
Anything with extra words ("please scroll down for me") falls through to the
LLM, which strips filler and then caches the result for next time.
"""

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

    # --- screenshot ---
    (["screenshot", "take a screenshot", "take screenshot", "capture screen",
      "grab a screenshot"], {"action": "screenshot", "target": ""}),
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
