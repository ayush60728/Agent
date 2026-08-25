"""
test_sequence.py

Hermetic tests for the multi-step action scheduler. No Ollama, no screen, no
cache writes:

  * normalize_action / validate_action / is_sequence are pure — tested directly;
  * the integration cases drive agent.process_command() with agent.execute_action
    monkeypatched to a recorder (so nothing is really clicked/typed/closed), the
    builtin table and prompt cache stubbed out, and — for the parse path —
    agent.ask_qwen replaced with canned JSON. save_action is stubbed so no test
    ever touches prompt_cache.json.

Covers: folding the several plan shapes into a canonical sequence, validating the
wrapper (length, per-step, no nesting), in-order execution, the two safety gates
on a batch (confirm-whole-batch for a destructive step, ask-&-resume for an
ambiguous click mid-sequence), and the full parse->normalize->validate->run path
from a raw LLM string.

Run with the project venv:
    .venv/Scripts/python.exe test_sequence.py
"""

import sys

# The sandbox shell is cp1252; production reconfigures to UTF-8 in main().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import confirmation
import disambiguation
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


# Reusable single actions.
A = {"action": "open_app", "target": "notepad"}
B = {"action": "type_text", "target": "hello"}
C_ = {"action": "press_key", "target": "enter"}


# --- 1. normalize_action — fold every source shape into two canonical forms ---
print("\n1. normalize_action — wrap/unwrap into single-action or sequence")

# {"steps":[...]} and bare [...] with 2+ items -> a sequence.
check("{'steps':[A,B]} -> sequence",
      agent.normalize_action({"steps": [A, B]}),
      {"action": "sequence", "steps": [A, B]})
check("[A,B] -> sequence",
      agent.normalize_action([A, B]),
      {"action": "sequence", "steps": [A, B]})
check("[A,B,C] -> sequence keeps order",
      agent.normalize_action([A, B, C_]),
      {"action": "sequence", "steps": [A, B, C_]})

# A one-item plan is just that action — keeps the single-action path unchanged.
check("{'steps':[A]} -> unwrapped A", agent.normalize_action({"steps": [A]}), A)
check("[A] -> unwrapped A", agent.normalize_action([A]), A)

# A plain single action is returned untouched (the common case).
check("single dict unchanged", agent.normalize_action(A), A)

# Idempotent: an already-canonical sequence passes straight through (it has an
# "action" key, so the unwrap branch is correctly skipped).
SEQ_AB = {"action": "sequence", "steps": [A, B]}
check("sequence is idempotent", agent.normalize_action(SEQ_AB), SEQ_AB)

# is_sequence recognizes only the canonical composite.
check("is_sequence(sequence)", agent.is_sequence(SEQ_AB), True)
check("is_sequence(single)", agent.is_sequence(A), False)
check("is_sequence(non-dict)", agent.is_sequence([A, B]), False)


# --- 2. validate_action — the sequence wrapper -----------------------------
print("\n2. validate_action — sequence length, per-step, no nesting")

ok, err = agent.validate_action({"action": "sequence", "steps": [A, B, C_]})
check("valid sequence passes", (ok, err), (True, None))

# A bad step fails, and the error names which step.
ok, err = agent.validate_action(
    {"action": "sequence", "steps": [A, {"action": "frobnicate", "target": "x"}]})
check("invalid step -> rejected", ok, False)
check_contains("invalid step -> error names step 2", err, "Step 2")

# A step may not itself be a sequence (no nesting).
ok, err = agent.validate_action(
    {"action": "sequence", "steps": [A, {"action": "sequence", "steps": [B, C_]}]})
check("nested sequence -> rejected", ok, False)
check_contains("nested -> explains no nesting", err, "another sequence")

# A "sequence" that is too short shouldn't exist post-normalization, but a raw
# one is malformed rather than a real plan.
ok, err = agent.validate_action({"action": "sequence", "steps": [A]})
check("1-step sequence -> rejected", ok, False)
ok, err = agent.validate_action({"action": "sequence", "steps": []})
check("0-step sequence -> rejected", ok, False)
ok, err = agent.validate_action({"action": "sequence", "steps": "nope"})
check("non-list steps -> rejected", ok, False)

# Over the cap is rejected (runaway-plan guard).
big = {"action": "sequence", "steps": [dict(A) for _ in range(agent.MAX_STEPS + 1)]}
ok, err = agent.validate_action(big)
check("> MAX_STEPS -> rejected", ok, False)
check_contains("over-cap -> mentions the limit", err, str(agent.MAX_STEPS))

# At the cap is allowed.
atcap = {"action": "sequence", "steps": [dict(A) for _ in range(agent.MAX_STEPS)]}
ok, err = agent.validate_action(atcap)
check("== MAX_STEPS -> allowed", (ok, err), (True, None))

# "sequence" is deliberately NOT a standalone allowed action.
check("'sequence' not in ALLOWED_ACTIONS", "sequence" in agent.ALLOWED_ACTIONS, False)


# --- shared recorder for the integration cases ------------------------------
# Records each dispatched action and returns a readable string — except a
# click_text, which comes back as an AmbiguousClick to drive the ask-&-resume
# path (mirrors test_disambiguation's recorder).
_ran = []

AMBIG = disambiguation.AmbiguousClick("submit", [
    {"x": 11, "y": 11, "desc": "top-left", "color": "blue"},
    {"x": 22, "y": 22, "desc": "center", "color": "red"},
])


def _recorder(action):
    _ran.append(action)
    if action.get("action") == "click_text":
        return AMBIG
    return f"[ran {action.get('action')}:{action.get('target')}]"


# Neutralize every real side-effect path. process_command resolves these names
# at call time, so reassigning them here fully sandboxes it.
agent.execute_action = _recorder
agent.get_builtin_action = lambda text: None      # never short-circuit to a builtin
agent.save_action = lambda *a, **k: None           # never write prompt_cache.json

_cached = {"val": None}                            # per-case cache stand-in
agent.get_cached_action = lambda text: _cached["val"]


def run(text):
    return agent.process_command(text)


def reset(cache_val=None):
    confirmation.clear()
    disambiguation.clear()
    _ran.clear()
    _cached["val"] = cache_val


# --- 3. _run_sequence — in-order execution + aggregated reply --------------
print("\n3. _run_sequence — every step runs in order")

reset()
steps = [
    {"action": "open_app", "target": "notepad"},
    {"action": "wait", "target": 2},
    {"action": "type_text", "target": "hello world"},
    {"action": "press_key", "target": "ctrl+s"},
]
reply = agent._run_sequence(steps)
check("all steps ran in order", _ran, steps)
check_contains("reply aggregates first step", reply, "[ran open_app:notepad]")
check_contains("reply aggregates last step", reply, "[ran press_key:ctrl+s]")

# _prefix seeds the reply with lines from steps that ran in an earlier turn.
reset()
reply = agent._run_sequence([{"action": "scroll", "target": "down"}], _prefix=["PRIOR"])
check("_prefix carried into reply", reply.startswith("PRIOR"), True)
check("_prefix + tail ran", _ran, [{"action": "scroll", "target": "down"}])


# --- 4. confirm whole batch (a destructive step in the sequence) -----------
print("\n4. process_command — a sequence with a close is confirmed up front")

SEQ_CLOSE = {"action": "sequence", "steps": [
    {"action": "open_app", "target": "brave"},
    {"action": "press_key", "target": "ctrl+w"},   # destructive (close tab)
    {"action": "type_text", "target": "hello"},
]}

# (a) arming: the whole batch is described once, and NOTHING runs yet.
reset(cache_val=SEQ_CLOSE)
reply = run("do my three step thing")
check_contains("batch -> asks once to confirm", reply, "Say 'yes'")
check_contains("batch -> describes the whole batch", reply, "run 3 steps")
check("batch -> nothing ran yet", _ran, [])
check_true("batch -> confirmation pending", confirmation.is_pending())

# (b) "yes" -> runs EVERY step in order, plus the reopen-tab recovery hint.
reply = run("yes")
check("yes -> ran all 3 steps in order", _ran, SEQ_CLOSE["steps"])
check_contains("yes -> reopen-tab hint (a tab was closed)", reply, "reopen tab")
check("yes -> pending cleared", confirmation.is_pending(), False)

# (c) "no" -> cancels the whole batch, nothing runs.
reset(cache_val=SEQ_CLOSE)
run("do my three step thing")     # arm
_ran.clear()
reply = run("no")
check("no -> nothing ran", _ran, [])
check_contains("no -> acknowledges cancel", reply, "won't")
check("no -> pending cleared", confirmation.is_pending(), False)


# --- 5. ask & resume (an ambiguous click mid-sequence) ---------------------
print("\n5. process_command — ambiguous click suspends, pick resumes the tail")

SEQ_CLICK = {"action": "sequence", "steps": [
    {"action": "open_app", "target": "brave"},
    {"action": "wait", "target": 1},
    {"action": "click_text", "target": "submit"},   # <- ambiguous
    {"action": "type_text", "target": "hello"},
    {"action": "press_key", "target": "enter"},
]}

# (a) the sequence runs up to the ambiguous click, then suspends: earlier steps
#     ran, a selection is pending, and the tail is stashed.
reset(cache_val=SEQ_CLICK)
reply = run("open brave then click submit and type hello")
check("ran up to (and incl.) the click",
      _ran,
      [{"action": "open_app", "target": "brave"},
       {"action": "wait", "target": 1},
       {"action": "click_text", "target": "submit"}])
check_contains("suspend -> numbered prompt", reply, "1)")
check_contains("suspend -> notes progress so far", reply, "Done so far")
check_true("suspend -> selection pending", disambiguation.is_pending())
check("suspend -> tail stashed for resume",
      disambiguation.pending_resume_steps(),
      [{"action": "type_text", "target": "hello"},
       {"action": "press_key", "target": "enter"}])

# (b) the pick clicks the chosen candidate, THEN continues the remaining steps
#     in order — all in one resumed turn.
_ran.clear()
reply = run("two")
check("pick -> click_at then the tail, in order",
      _ran,
      [{"action": "click_at", "x": 22, "y": 22, "label": "the red center 'submit'"},
       {"action": "type_text", "target": "hello"},
       {"action": "press_key", "target": "enter"}])
check("resume -> pending cleared", disambiguation.is_pending(), False)

# (c) cancelling the pick clicks nothing and drops the stashed tail.
reset(cache_val=SEQ_CLICK)
run("open brave then click submit and type hello")   # arm mid-sequence
_ran.clear()
reply = run("never mind")
check("cancel pick -> nothing else ran", _ran, [])
check("cancel pick -> pending cleared", disambiguation.is_pending(), False)
check("cancel pick -> tail dropped", disambiguation.pending_resume_steps(), None)


# --- 6. full parse path (raw LLM string -> normalize -> validate -> run) ----
print("\n6. process_command — parse a multi-step plan straight from the LLM")

# (a) a {"steps":[...]} object string.
reset()
agent.ask_qwen = lambda text: (
    '{"steps":[{"action":"open_app","target":"notepad"},'
    '{"action":"type_text","target":"hi"}]}')
reply = run("open notepad and type hi")
check("object plan parsed & ran in order",
      _ran,
      [{"action": "open_app", "target": "notepad"},
       {"action": "type_text", "target": "hi"}])

# (b) a top-level [...] array string — the leading-'[' branch of the extractor
#     (a greedy {.*} would drop the brackets and corrupt the JSON).
reset()
agent.ask_qwen = lambda text: (
    '[{"action":"scroll","target":"down"},{"action":"scroll","target":"up"}]')
reply = run("scroll down then back up")
check("array plan parsed & ran in order",
      _ran,
      [{"action": "scroll", "target": "down"},
       {"action": "scroll", "target": "up"}])

# (c) a single-action string is unwrapped and runs as one action (regression:
#     the common case is untouched by the multi-step machinery).
reset()
agent.ask_qwen = lambda text: '{"action":"open_app","target":"brave"}'
reply = run("open brave")
check("single action still runs as one", _ran, [{"action": "open_app", "target": "brave"}])


print("\n" + ("ALL PASSED" if _failures == 0 else f"{_failures} CHECK(S) FAILED"))
sys.exit(1 if _failures else 0)
