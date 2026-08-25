"""
confirmation.py

A safety gate in front of destructive desktop actions.

The problem: commands that close a window run the instant they're classified.
In voice mode a misheard "close this window" (or Whisper turning room noise
into a command) can kill an app in under a second — and a closed window can't
be brought back with its state intact.

The fix: destructive actions must be confirmed ("yes") before they run. This
module holds the small state machine that makes that work for BOTH typed and
voice input, without blocking:

    1. A destructive action is *armed* here instead of executed — stashed in a
       module global with a timestamp — and the agent asks for confirmation.
    2. The user's NEXT utterance is interpreted as the yes/no answer:
         "yes"/"do it"      -> confirm  -> the caller runs the stashed action
         "no"/"cancel"      -> cancel   -> the caller drops it
         anything else      -> unrelated -> caller drops it and treats the
                                            input as a brand-new command
    3. An armed action auto-expires after CONFIRM_TIMEOUT_S, so a stray "yes"
       minutes later can never fire an old close.

Why here (and not in actions.py): confirmation is an orchestration concern, so
it lives beside validation in the agent layer. agent.process_command() is the
single point every action funnels through (builtins, prompt cache, and the LLM
all converge there), so gating there catches destructive commands from every
source at once.

True *undo* of a killed window is infeasible, so confirmation (prevention) is
the real protection. Where a genuine cheap recovery exists — a closed browser
tab reopens with ctrl+shift+t — recovery_hint() adds an honest nudge.

Pure standard library (time + re); no other imports, so nothing can import-cycle
against it.
"""

import re
import time


# How long an armed action waits for its yes/no before it expires. Long enough
# for a voice round-trip (agent speaks the prompt, user says "agent yes"), short
# enough that a "yes" said much later — for something else entirely — can't
# reach back and fire a close the user has long forgotten about.
CONFIRM_TIMEOUT_S = 30.0


# Destructive key combos, stored as frozensets of their parts so matching is
# independent of order, spacing, and case ("ctrl+w", "Ctrl + W", "w+ctrl" all
# match). Scope (user-approved): things that close a window, tab, or app.
#   alt+f4         close the active window
#   ctrl+w         close the current tab / document
#   ctrl+f4        close the current tab / MDI child
#   ctrl+shift+w   close the whole window / all tabs
#   ctrl+q         quit the application
#   ctrl+shift+q   quit the application (some apps)
_DESTRUCTIVE_COMBOS = {
    frozenset({"alt", "f4"}),
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "f4"}),
    frozenset({"ctrl", "shift", "w"}),
    frozenset({"ctrl", "q"}),
    frozenset({"ctrl", "shift", "q"}),
}

# Combos we can genuinely undo, for the recovery hint. Closing a tab is the one
# destructive action with a real, one-keystroke reversal (reopen last tab).
_REOPENABLE_COMBOS = {
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "f4"}),
}


# Whole-utterance answers. Matched against a normalized (punctuation-stripped,
# lowercased, space-collapsed) transcript, exactly as one whole phrase — so a
# real command that merely contains "no" ("open notepad") is never mistaken for
# a denial. Kept deliberately broad on the words people actually say to a
# yes/no prompt out loud.
_AFFIRM = {
    "yes", "y", "yeah", "yep", "yup", "yphm", "sure", "ok", "okay", "confirm",
    "confirmed", "do it", "go ahead", "go for it", "proceed", "affirmative",
    "correct", "yes please", "definitely", "please do",
}
_DENY = {
    "no", "n", "nope", "nah", "cancel", "stop", "dont", "abort", "negative",
    "never mind", "nevermind", "forget it", "leave it", "nothing", "no thanks",
    "no thank you", "dont do it", "do not", "do not do it",
}


def _norm(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — the same shape the
    voice layer's _is_cancel uses, so 'Never mind.' and 'never mind' match."""
    t = re.sub(r"[^a-z0-9\s]", "", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _combo_parts(target) -> frozenset:
    """The set of key names in a combo string ('ctrl+shift+w' -> {ctrl,shift,w}),
    lowercased and trimmed so spacing/order/case don't matter."""
    return frozenset(p.strip().lower() for p in str(target or "").split("+") if p.strip())


def needs_confirmation(action) -> bool:
    """True if this action should be confirmed before it runs: any close_app,
    or a press_key whose combo is in the destructive set. A sequence needs
    confirmation if ANY of its steps does — the whole batch is then confirmed
    up front (see agent.process_command), so no destructive step ever runs
    unconfirmed."""
    if not isinstance(action, dict):
        return False
    kind = action.get("action")
    if kind == "close_app":
        return True
    if kind == "press_key":
        return _combo_parts(action.get("target", "")) in _DESTRUCTIVE_COMBOS
    if kind == "sequence":
        return any(needs_confirmation(s) for s in action.get("steps", []))
    return False


def describe(action) -> str:
    """A short human phrase for what the action will do, for the spoken/printed
    prompt ('close Brave', 'close this tab', 'quit this app')."""
    if not isinstance(action, dict):
        return "do that"
    kind = action.get("action")
    if kind == "sequence":
        # Only reached when the sequence is being confirmed, i.e. it contains at
        # least one destructive step. Name those steps — they're the reason we're
        # asking — and let the count convey the rest.
        steps = action.get("steps", [])
        n = len(steps)
        destructive = [describe(s) for s in steps if needs_confirmation(s)]
        if len(destructive) == 1:
            return f"run {n} steps, including one that will {destructive[0]}"
        if destructive:
            return (f"run {n} steps, including {len(destructive)} that will: "
                    + ", ".join(destructive))
        return f"run {n} steps"
    if kind == "close_app":
        target = (action.get("target") or "").strip()
        return f"close {target}" if target else "close the current window"
    if kind == "press_key":
        parts = _combo_parts(action.get("target", ""))
        if parts in (frozenset({"ctrl", "w"}), frozenset({"ctrl", "f4"})):
            return "close this tab"
        if parts in (frozenset({"alt", "f4"}), frozenset({"ctrl", "shift", "w"})):
            return "close this window"
        if parts in (frozenset({"ctrl", "q"}), frozenset({"ctrl", "shift", "q"})):
            return "quit this app"
        return f"press {action.get('target')}"
    return str(kind)


def prompt_for(action) -> str:
    """The confirmation question shown/spoken back to the user."""
    return f"That will {describe(action)}. Say 'yes' to confirm or 'no' to cancel."


def recovery_hint(action) -> str:
    """A trailing, honest 'you can undo this' note for the actions that truly
    have a one-step reversal (closing a tab). Empty for everything else — we
    don't pretend a killed window can be resurrected."""
    if not isinstance(action, dict):
        return ""
    kind = action.get("action")
    if kind == "press_key":
        if _combo_parts(action.get("target", "")) in _REOPENABLE_COMBOS:
            return " (say 'reopen tab' to undo)"
    if kind == "sequence":
        # Offer the tab-reopen nudge if the batch closed a tab (the one
        # reversible destructive action). Fires only when a step actually
        # qualifies — a killed window still gets no false promise of undo.
        if any(recovery_hint(s) for s in action.get("steps", [])):
            return " (say 'reopen tab' to undo)"
    return ""


# --- pending-action state -------------------------------------------------
# One armed action at a time. Module-global is the right scope: only one mode
# (text or voice) runs per process, each calling process_command serially, so
# there's no concurrency to guard against.
_pending = None  # {"action": dict, "ts": float} or None


def arm(action) -> None:
    """Stash a destructive action, awaiting confirmation. Stores a copy so a
    later mutation of the caller's dict can't change what we execute."""
    global _pending
    _pending = {"action": dict(action), "ts": time.time()}


def is_pending() -> bool:
    """True if an armed action is still waiting for its answer. Auto-clears
    (and returns False) once CONFIRM_TIMEOUT_S has passed, so a stale 'yes'
    never fires an action the user has moved on from."""
    global _pending
    if _pending is None:
        return False
    if time.time() - _pending["ts"] > CONFIRM_TIMEOUT_S:
        _pending = None
        return False
    return True


def pending_action():
    """The armed action dict (a peek, without clearing), or None if none is
    pending / it has expired."""
    return _pending["action"] if is_pending() else None


def take():
    """Pop the armed action for execution — returns it and clears the pending
    state. None if nothing valid is pending."""
    global _pending
    action = pending_action()
    _pending = None
    return action


def clear() -> None:
    """Drop any armed action (on cancel, or when a new command supersedes it)."""
    global _pending
    _pending = None


def interpret(text: str) -> str:
    """Read an utterance as the answer to a pending confirmation.

        'yes' / 'do it' / 'ok'   -> 'confirm'
        'no' / 'cancel' / 'stop' -> 'cancel'
        anything else            -> 'unrelated'  (a fresh command, not an answer)

    Whole-phrase match only, so a real command is never misread as yes/no."""
    norm = _norm(text)
    if norm in _AFFIRM:
        return "confirm"
    if norm in _DENY:
        return "cancel"
    return "unrelated"
