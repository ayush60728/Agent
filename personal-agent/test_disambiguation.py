"""
test_disambiguation.py

Hermetic tests for confidence-scored matching + the ambiguous-click gate. No
Ollama, no screen, no cache writes:

  * scoring/consolidation feeds find_text_matches a FAKE pytesseract dict
    (monkeypatched) so ranking/merge/noise-drop run with no real OCR or screen;
  * interpret is pure;
  * the integration cases drive agent.process_command() with a monkeypatched
    prompt cache (so "click submit" resolves WITHOUT the LLM) and a
    monkeypatched agent.execute_action recorder that returns an AmbiguousClick
    for click_text (so nothing is actually clicked).

Run with the project venv:
    .venv/Scripts/python.exe test_disambiguation.py
"""

import sys
import time

# The sandbox shell is cp1252; production reconfigures to UTF-8 in main().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import desktop_actions
import disambiguation
import confirmation
import color_vision
import actions
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


# --- 1. scoring / consolidation / noise-drop ------------------------------
print("\n1. find_text_matches — scoring, consolidation, noise-drop")

# Pin the window and skip the real screen/OCR entirely. image_to_data's content
# is ignored (the fake dict is returned regardless), so the screenshot is a
# throwaway.
desktop_actions._active_window_bounds = lambda: (0, 0, 1000, 800)
desktop_actions.pyautogui.screenshot = lambda *a, **k: None

_FAKE = {}


def _fake_image_to_data(*a, **k):
    return _FAKE


desktop_actions.pytesseract.image_to_data = _fake_image_to_data


def set_ocr(words, lefts, tops, widths, heights, confs):
    global _FAKE
    _FAKE = {"text": words, "left": lefts, "top": tops,
             "width": widths, "height": heights, "conf": confs}


# (a) exact outranks partial; a low-confidence fuzzy hit is dropped; two
#     far-apart same-line boxes stay distinct (NOT merged).
set_ocr(
    words=["Submit", "submitted", "submits"],
    lefts=[100, 400, 750], tops=[100, 100, 100],
    widths=[80, 120, 80], heights=[20, 20, 20],
    confs=["95", "90", "20"],   # "submits" is a conf-20 fuzzy hit -> dropped
)
m = desktop_actions.find_text_matches("submit")
check("two candidates (noise dropped)", len(m), 2)
check_true("best is exact", m[0].exact)
check("best is the exact 'Submit' box", m[0].box, (100, 100, 80, 20))
check_true("exact scores above partial", m[0].score > m[1].score)
check("partial kept, not exact", m[1].exact, False)
check_true("dropped fuzzy not present", all(box_left != 750 for box_left in (mm.box[0] for mm in m)))

# (b) a split label ("Sign" + "In") merges into ONE candidate.
set_ocr(
    words=["Sign", "In"],
    lefts=[100, 165], tops=[300, 300],
    widths=[60, 25], heights=[20, 20],
    confs=["92", "88"],
)
m = desktop_actions.find_text_matches("sign in")
check("split label merges to one", len(m), 1)
check("merged union box", m[0].box, (100, 300, 90, 20))


# --- 2. describe_position (pure grid math) --------------------------------
print("\n2. describe_position — 3x3 grid labels")
W = (0, 0, 900, 900)  # thirds at 300 / 600
check("top-left", desktop_actions.describe_position((90, 90, 20, 20), W), "top-left")
check("center", desktop_actions.describe_position((440, 440, 20, 20), W), "center")
check("bottom-right", desktop_actions.describe_position((790, 790, 20, 20), W), "bottom-right")
check("top (mid col)", desktop_actions.describe_position((440, 90, 20, 20), W), "top")
check("left (mid row)", desktop_actions.describe_position((90, 440, 20, 20), W), "left")


# --- 3. interpret ---------------------------------------------------------
print("\n3. interpret — reading the pick")
C = [
    {"x": 11, "y": 11, "desc": "top-left", "color": "blue"},
    {"x": 22, "y": 22, "desc": "center", "color": "red"},
    {"x": 33, "y": 33, "desc": "bottom-right", "color": None},
]
check("digit '2'", disambiguation.interpret("2", C), 1)
check("word 'two'", disambiguation.interpret("two", C), 1)
check("'the second one'", disambiguation.interpret("the second one", C), 1)
check("'number 3'", disambiguation.interpret("number 3", C), 2)
check("'first'", disambiguation.interpret("first", C), 0)
check("'top left'", disambiguation.interpret("top left", C), 0)
check("'the top one' (pos, not #1)", disambiguation.interpret("the top one", C), 0)
check("'the bottom one' (pos, not #1)", disambiguation.interpret("the bottom one", C), 2)
check("'center'", disambiguation.interpret("center", C), 1)
check("'the red one'", disambiguation.interpret("the red one", C), 1)
check("'the blue one'", disambiguation.interpret("the blue one", C), 0)
for t in ("no", "never mind", "cancel", "none of them"):
    check(f"deny {t!r}", disambiguation.interpret(t, C), "cancel")
check("out-of-range '5'", disambiguation.interpret("5", C), "unrelated")
check("unrelated 'open brave'", disambiguation.interpret("open brave", C), "unrelated")
check("empty", disambiguation.interpret("", C), "unrelated")


# --- 4. integration via process_command -----------------------------------
print("\n4. process_command — armed, picked, cancelled, superseded")

AMBIG = disambiguation.AmbiguousClick("submit", [
    {"x": 11, "y": 11, "desc": "top-left", "color": "blue"},
    {"x": 22, "y": 22, "desc": "center", "color": "red"},
])

_ran = []


def _recorder(action):
    _ran.append(action)
    if action.get("action") == "click_text":
        return AMBIG  # simulate "found 2 rival matches"
    return f"[executed {action.get('action')}]"


# Route "click submit" through the cache (no LLM); real builtins still handle
# "scroll down". execute_action is the recorder, so nothing is really clicked.
agent.execute_action = _recorder
agent.get_cached_action = (
    lambda text: {"action": "click_text", "target": "submit"}
    if text.strip().lower() == "click submit" else None
)


def run(text):
    return agent.process_command(text)


# (a) ambiguous click -> numbered prompt, NOTHING clicked, selection pending
confirmation.clear()
disambiguation.clear()
_ran.clear()
reply = run("click submit")
check_contains("ambiguous -> numbered prompt", reply, "1)")
check_contains("ambiguous -> lists 2nd option", reply, "2)")
check_contains("ambiguous -> shows differing color", reply, "(red)")
check("ambiguous -> only the classify ran, no click", _ran, [{"action": "click_text", "target": "submit"}])
check_true("ambiguous -> selection pending", disambiguation.is_pending())

# (b) "two" -> clicks candidate 2 via click_at, pending cleared
_ran.clear()
reply = run("two")
check("pick 'two' -> click_at candidate 2", _ran,
      [{"action": "click_at", "x": 22, "y": 22, "label": "the red center 'submit'"}])
check("pick -> pending cleared", disambiguation.is_pending(), False)

# (c) "no" -> cancels, nothing clicked
disambiguation.clear()
_ran.clear()
run("click submit")     # arm
_ran.clear()
reply = run("no")
check("cancel -> nothing clicked", _ran, [])
check_contains("cancel -> acknowledges", reply, "cancel")
check("cancel -> pending cleared", disambiguation.is_pending(), False)

# (d) unrelated command supersedes the pending selection (and runs itself)
disambiguation.clear()
_ran.clear()
run("click submit")             # arm (records the classify->execute)
_ran.clear()                    # ignore the arming call; watch only the supersede
reply = run("scroll down")      # not a pick -> drop pending, run as fresh cmd
check("supersede -> ran scroll", _ran, [{"action": "scroll", "target": "down"}])
check("supersede -> pending cleared", disambiguation.is_pending(), False)

# (e) a stale armed selection expires
disambiguation.clear()
disambiguation.arm(AMBIG)
check_true("armed -> pending", disambiguation.is_pending())
disambiguation._pending["ts"] = time.time() - disambiguation.SELECT_TIMEOUT_S - 1
check("expired -> not pending", disambiguation.is_pending(), False)
check("expired -> no pick target", disambiguation.pending(), None)


# --- 5. real click_text -> execute_action passthrough ---------------------
# Section 4 mocked agent.execute_action, so the REAL path that *produces* an
# AmbiguousClick (actions.execute_action -> desktop_actions.click_text ->
# _build_ambiguous) was untested. Drive it here with matches injected and the
# screen neutralized: no focus check, no real click, no color sampling.
print("\n5. execute_action -> click_text -> AmbiguousClick (real path)")

actions._ensure_focused_app_active = lambda: None          # pretend an app is focused
desktop_actions.pyautogui.click = lambda *a, **k: None      # never actually click
desktop_actions._padded_region_shot = lambda *a, **k: None  # no screenshot for color
color_vision.dominant_chromatic_color = lambda img: None    # colors all unknown -> omitted

_M = desktop_actions.Match
_INJECT = []
desktop_actions.find_text_matches = lambda t: list(_INJECT)

# two rival matches -> AmbiguousClick out of the real execute_action
_INJECT[:] = [
    _M(150, 120, (100, 100, 100, 40), 0.95, True),
    _M(150, 620, (100, 600, 100, 40), 0.95, True),
]
res = actions.execute_action({"action": "click_text", "target": "submit"})
check_true("2 matches -> AmbiguousClick", isinstance(res, disambiguation.AmbiguousClick))
check("AmbiguousClick has 2 candidates", len(res.candidates) if isinstance(res, disambiguation.AmbiguousClick) else -1, 2)
check_true("candidates carry a position desc",
           isinstance(res, disambiguation.AmbiguousClick) and all(c["desc"] for c in res.candidates))

# one match -> a normal click result string (no prompt)
_INJECT[:] = [_M(150, 120, (100, 100, 100, 40), 0.95, True)]
res = actions.execute_action({"action": "click_text", "target": "submit"})
check_true("1 match -> result string", isinstance(res, str))
check_contains("1 match -> clicked", res, "clicked")

# the internal click_at action dispatches through execute_action
res = actions.execute_action({"action": "click_at", "x": 7, "y": 9, "label": "the 'x'"})
check_contains("click_at -> clicked at coords", res, "(7, 9)")


print("\n" + ("ALL PASSED" if _failures == 0 else f"{_failures} CHECK(S) FAILED"))
sys.exit(1 if _failures else 0)
