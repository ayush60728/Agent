"""
test_confirmation.py

Hermetic tests for the destructive-action confirmation gate. No Ollama, no
screen, no cache writes: the integration cases drive agent.process_command()
through BUILTIN phrasings (so the LLM is never called) and monkeypatch
agent.execute_action to a recorder (so nothing is actually clicked/closed).

Run with the project venv:
    .venv/Scripts/python.exe test_confirmation.py
"""

import sys
import time

# The sandbox shell is cp1252; production reconfigures to UTF-8 in main().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import confirmation
import agent


_failures = 0


def check(label, got, expected):
    global _failures
    ok = got == expected
    if not ok:
        _failures += 1
    print(f"  [{'OK' if ok else '**FAIL**'}] {label}: got {got!r}, expected {expected!r}")


def check_true(label, got):
    check(label, bool(got), True)


def check_contains(label, haystack, needle):
    global _failures
    ok = needle in (haystack or "")
    if not ok:
        _failures += 1
    print(f"  [{'OK' if ok else '**FAIL**'}] {label}: {needle!r} in {haystack!r}")


# --- 1. needs_confirmation ------------------------------------------------
print("\n1. needs_confirmation — the gated set")
CONFIRM = [
    {"action": "close_app", "target": "brave"},
    {"action": "close_app", "target": ""},
    {"action": "press_key", "target": "ctrl+w"},
    {"action": "press_key", "target": "CTRL+W"},       # case-insensitive
    {"action": "press_key", "target": "ctrl + w"},     # spacing-insensitive
    {"action": "press_key", "target": "w+ctrl"},       # order-insensitive
    {"action": "press_key", "target": "alt+f4"},
    {"action": "press_key", "target": "ctrl+f4"},
    {"action": "press_key", "target": "ctrl+shift+w"},
    {"action": "press_key", "target": "ctrl+q"},
    {"action": "press_key", "target": "ctrl+shift+q"},
]
for a in CONFIRM:
    check_true(f"gate {a['action']}:{a.get('target')!r}", confirmation.needs_confirmation(a))

PASS = [
    {"action": "press_key", "target": "enter"},
    {"action": "press_key", "target": "ctrl+s"},
    {"action": "press_key", "target": "ctrl+z"},        # undo — recovery, not destructive
    {"action": "press_key", "target": "ctrl+shift+t"},  # reopen tab — recovery
    {"action": "press_key", "target": "ctrl+c"},
    {"action": "scroll", "target": "down"},
    {"action": "open_app", "target": "brave"},
    {"action": "type_text", "target": "hello"},
]
for a in PASS:
    check("no gate " + f"{a['action']}:{a.get('target')!r}", confirmation.needs_confirmation(a), False)


# --- 2. interpret ---------------------------------------------------------
print("\n2. interpret — reading the yes/no answer")
for t in ("yes", "YES", "Yeah", "do it", "ok", "confirm", "go ahead"):
    check(f"affirm {t!r}", confirmation.interpret(t), "confirm")
for t in ("no", "Nope", "cancel", "never mind", "Never mind.", "stop", "don't"):
    check(f"deny {t!r}", confirmation.interpret(t), "cancel")
for t in ("scroll down", "open brave", "what color is this", "yes open brave"):
    check(f"unrelated {t!r}", confirmation.interpret(t), "unrelated")


# --- 3. timeout -----------------------------------------------------------
print("\n3. timeout — a stale armed action expires")
confirmation.clear()
confirmation.arm({"action": "close_app", "target": "brave"})
check_true("armed -> pending", confirmation.is_pending())
confirmation._pending["ts"] = time.time() - confirmation.CONFIRM_TIMEOUT_S - 1
check("expired -> not pending", confirmation.is_pending(), False)
check("expired -> no action", confirmation.pending_action(), None)


# --- 4. integration via process_command -----------------------------------
print("\n4. process_command — armed, confirmed, cancelled, superseded")

_ran = []


def _recorder(action):
    _ran.append(action)
    return f"[executed {action['action']}:{action.get('target')}]"


agent.execute_action = _recorder  # process_command resolves this at call time


def run(text):
    return agent.process_command(text)


# (a) destructive builtin -> armed, NOT executed
confirmation.clear()
_ran.clear()
reply = run("close tab")  # builtin -> {press_key, ctrl+w}
check_contains("close tab -> asks to confirm", reply, "Say 'yes'")
check("close tab -> nothing ran yet", _ran, [])
check_true("close tab -> pending", confirmation.is_pending())

# (b) "yes" -> executes the armed action + recovery hint
reply = run("yes")
check("yes -> ran ctrl+w", _ran, [{"action": "press_key", "target": "ctrl+w"}])
check_contains("yes -> reopen-tab hint", reply, "reopen tab")
check("yes -> pending cleared", confirmation.is_pending(), False)

# (c) "no" -> cancels, nothing runs
_ran.clear()
run("close tab")
reply = run("no")
check("no -> nothing ran", _ran, [])
check_contains("no -> acknowledges cancel", reply, "won't")
check("no -> pending cleared", confirmation.is_pending(), False)

# (d) unrelated command supersedes the pending action (and runs itself)
_ran.clear()
run("close tab")                 # armed
reply = run("scroll down")       # not yes/no -> drop pending, run as fresh command
check("supersede -> ran scroll", _ran, [{"action": "scroll", "target": "down"}])
check("supersede -> pending cleared", confirmation.is_pending(), False)

# (e) non-destructive command runs immediately, no prompt
_ran.clear()
reply = run("copy")              # builtin -> {press_key, ctrl+c}
check("copy -> ran immediately", _ran, [{"action": "press_key", "target": "ctrl+c"}])
check("copy -> no pending", confirmation.is_pending(), False)


print("\n" + ("ALL PASSED" if _failures == 0 else f"{_failures} CHECK(S) FAILED"))
sys.exit(1 if _failures else 0)
