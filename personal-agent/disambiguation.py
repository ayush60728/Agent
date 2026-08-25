"""
disambiguation.py

A "which one did you mean?" gate in front of ambiguous clicks.

The problem: click_text finds text by OCR, and a screen often has the SAME
label in several places — three "Submit" buttons, a dozen "Download" links.
The old behaviour clicked matches[0] (the first one OCR happened to return),
so "click submit" with three submits was a coin flip: a 1-in-3 chance of the
wrong button, with no way for the user to steer it.

The fix: when 2+ genuinely-distinct candidates remain after confidence scoring
and box consolidation (see desktop_actions.find_text_matches), don't guess.
List them — numbered, with a position ("top-left") and a color when the
candidates differ — and let the user's NEXT utterance pick one:

    🤖 I found 3 'submit' buttons: 1) top-left, 2) center (red),
       3) bottom-right. Say a number, or e.g. 'the red one'.
    You: two
    🤖 clicked the red 'submit' at (640, 300)

This is the exact shape of confirmation.py's destructive-action gate, for the
same reasons: process_command() is the one chokepoint every command funnels
through (builtins, cache, and the LLM all converge there), so arming a pending
selection there works identically in typed and voice mode, and — crucially —
the pick ("two", "the top one") is interpreted BEFORE the builtins/cache/LLM,
so a selection word can never collide with a builtin command.

    1. click_text detects the ambiguity and returns an AmbiguousClick signal
       (it can't ask on its own — only OCR knows there were several matches).
       process_command arms it here and speaks the numbered prompt.
    2. The user's NEXT utterance is read as the pick:
         "2" / "two" / "the second one"     -> that candidate's index
         "top left" / "the red one"         -> matched by position / color
         "no" / "cancel" / "never mind"     -> cancel, nothing clicked
         anything else                      -> unrelated: drop the pending
                                               selection, treat as a fresh command
    3. An armed selection auto-expires after SELECT_TIMEOUT_S. The candidate
       coordinates were captured a turn ago; the timeout bounds how stale they
       can get if the window moved before the pick (same risk class as the
       confirmation gate's stashed action).

Standard library (re + time) plus color_vision (itself a leaf — only colorsys),
so this module sits at the bottom of the import graph and can't import-cycle
against agent/actions/desktop_actions.
"""

import re
import time
from collections import namedtuple

import color_vision


# How long an armed selection waits for its pick before it expires. Matches the
# confirmation gate's window: long enough for a voice round-trip (agent speaks
# the list, user says "agent two"), short enough that the stored coordinates
# can't go stale for minutes if the target window moves after the prompt.
SELECT_TIMEOUT_S = 30.0


# The signal click_text returns instead of a result string when it finds 2+
# candidates it won't choose between. `candidates` is a list of dicts, each:
#   {"x": int, "y": int, "desc": str, "color": str | None}
# desc is a position label ("top-left"); color is a dominant-color name, set
# only when the candidates differ in color (so the prompt stays uncluttered).
AmbiguousClick = namedtuple("AmbiguousClick", ["text", "candidates"])


# Number words and ordinals the user might say to pick from the list. "one" is
# deliberately handled with care in _parse_index: on its own it means candidate
# #1, but in "the top one" / "the red one" it's a noun, not the number — so
# position and color are matched BEFORE numbers in interpret().
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
    "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10,
}

# Position vocabulary. Synonyms fold onto the canonical tokens that
# desktop_actions.describe_position emits ("top", "bottom", "left", "right",
# "center"), so a spoken "middle"/"upper" matches a candidate's stored desc.
_POS_TOKENS = {"top", "bottom", "left", "right", "center"}
_POS_SYNONYMS = {
    "upper": "top", "lower": "bottom",
    "middle": "center", "centre": "center", "mid": "center",
}

# Whole-utterance denials — the natural ways to wave off a list of choices.
# Matched against the normalized transcript as one whole phrase, so a real
# command that merely contains "no" ("open notepad") is never read as a cancel.
_DENY = {
    "no", "n", "nope", "nah", "cancel", "stop", "dont", "abort",
    "never mind", "nevermind", "forget it", "leave it", "none",
    "none of them", "neither", "no thanks", "no thank you", "not that",
}


def _norm(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — the same shape the
    confirmation gate and the voice layer's _is_cancel use."""
    t = re.sub(r"[^a-z0-9\s]", "", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _pos_tokens(s: str) -> set:
    """The set of canonical position tokens in a phrase. 'top-left' and
    'the upper left one' both -> {top, left}; 'middle' -> {center}."""
    out = set()
    for raw in re.split(r"[\s\-]+", s or ""):
        w = _POS_SYNONYMS.get(raw, raw)
        if w in _POS_TOKENS:
            out.add(w)
    return out


def _match_position(norm: str, candidates):
    """Index of the single candidate whose position matches the spoken phrase,
    or None if nothing matches or the phrase is ambiguous (e.g. "top" when two
    candidates are top-left and top-right — better to fall through than guess)."""
    want = _pos_tokens(norm)
    if not want:
        return None
    hits = [i for i, c in enumerate(candidates)
            if want and want <= _pos_tokens(c.get("desc", ""))]
    return hits[0] if len(hits) == 1 else None


def _match_color(norm: str, candidates):
    """Index of the single candidate whose color matches a spoken color word
    ('the red one'), or None if no color word / no unique color match."""
    wanted = None
    for tok in norm.split():
        c = color_vision.canonical_color(tok)
        if c is not None:
            wanted = c
            break
    if wanted is None:
        return None
    hits = [i for i, c in enumerate(candidates) if c.get("color") == wanted]
    return hits[0] if len(hits) == 1 else None


def _parse_index(norm: str, n: int):
    """Index from an explicit number/ordinal ('2', 'two', 'second', 'number
    3'), or None. Out-of-range numbers return None (not a valid pick). Called
    AFTER position/color, so a trailing noun 'one' in 'the top one' has already
    been resolved by position and won't be misread here as the number 1."""
    for tok in norm.split():
        m = re.match(r"(\d+)", tok)
        if m:
            v = int(m.group(1))
            return v - 1 if 1 <= v <= n else None
        if tok in _ORDINALS:
            v = _ORDINALS[tok]
            return v - 1 if 1 <= v <= n else None
        if tok in _NUM_WORDS:
            v = _NUM_WORDS[tok]
            return v - 1 if 1 <= v <= n else None
    return None


def interpret(text: str, candidates):
    """Read an utterance as the pick for a pending disambiguation.

        int (0-based index)   -> click that candidate
        "cancel"              -> user waved it off, click nothing
        "unrelated"           -> not a pick; treat as a brand-new command

    Position and color are matched before numbers on purpose: it keeps the
    trailing noun in "the top one" / "the red one" from being read as the
    number 1."""
    norm = _norm(text)
    if not norm or not candidates:
        return "unrelated"
    if norm in _DENY:
        return "cancel"

    idx = _match_position(norm, candidates)
    if idx is not None:
        return idx
    idx = _match_color(norm, candidates)
    if idx is not None:
        return idx
    idx = _parse_index(norm, len(candidates))
    if idx is not None:
        return idx
    return "unrelated"


def prompt_for(ambig: AmbiguousClick) -> str:
    """The numbered list spoken/printed back to the user. Colors appear only on
    candidates that carry one (desktop_actions attaches colors only when they
    differ across candidates, so a same-color set stays clean)."""
    parts = []
    for i, c in enumerate(ambig.candidates, 1):
        desc = c.get("desc") or "?"
        label = f"{i}) {desc}"
        if c.get("color"):
            label += f" ({c['color']})"
        parts.append(label)
    listing = ", ".join(parts)
    n = len(ambig.candidates)
    return (f"I found {n} '{ambig.text}' matches: {listing}. "
            f"Say a number, or e.g. 'the top one'.")


def describe_choice(ambig: AmbiguousClick, index: int) -> str:
    """A short human label for the chosen candidate, for the click's result
    line ('the red top-left \"submit\"')."""
    c = ambig.candidates[index]
    quoted = f"'{ambig.text}'"
    prefix = " ".join(p for p in (c.get("color"), c.get("desc")) if p)
    return f"the {prefix} {quoted}" if prefix else quoted


# --- pending-selection state ----------------------------------------------
# One armed selection at a time — same scope rationale as confirmation.py: only
# one mode (text or voice) runs per process, each calling process_command
# serially, so there's no concurrency to guard against.
_pending = None  # {"ambig": AmbiguousClick, "resume_steps": list|None, "ts": float} or None


def arm(ambig: AmbiguousClick, resume_steps=None) -> None:
    """Stash an ambiguous click, awaiting the user's pick.

    resume_steps is the tail of a multi-step sequence that should run AFTER this
    click is resolved (the "ask & resume" behaviour). It's None for a plain
    standalone click — that keeps this signature backward-compatible with every
    existing single-click caller, which passes only the AmbiguousClick."""
    global _pending
    _pending = {"ambig": ambig, "resume_steps": resume_steps, "ts": time.time()}


def is_pending() -> bool:
    """True if a selection is still waiting to be picked. Auto-clears (and
    returns False) once SELECT_TIMEOUT_S has passed, so a stray number said
    much later can't fire a click on coordinates the user has moved on from."""
    global _pending
    if _pending is None:
        return False
    if time.time() - _pending["ts"] > SELECT_TIMEOUT_S:
        _pending = None
        return False
    return True


def pending():
    """The armed AmbiguousClick (a peek, without clearing), or None."""
    return _pending["ambig"] if is_pending() else None


def pending_resume_steps():
    """The sequence tail to run after this click resolves (the "ask & resume"
    continuation), or None if this was a standalone click / nothing is pending.
    Read this BEFORE clear() when resolving a pick — clear() drops it."""
    return _pending.get("resume_steps") if is_pending() else None


def take():
    """Pop the armed AmbiguousClick and clear the pending state."""
    global _pending
    ambig = pending()
    _pending = None
    return ambig


def clear() -> None:
    """Drop any armed selection (on cancel, pick, or supersede)."""
    global _pending
    _pending = None
