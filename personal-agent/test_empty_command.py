"""
test_empty_command.py

Regression guard for the "bare wake word opens Brave" bug. Root cause was a
two-part defect:

  * a blank / punctuation-only utterance ("agent." -> "." after the wake word is
    stripped) normalizes to the empty string, and
  * the prompt cache had a poison ""-> open brave entry that any empty input
    replayed (and save_action would happily re-create it).

These tests pin the fixes: normalize_prompt collapses punctuation to "",
get_cached_action/save_action refuse the empty key, and process_command bails
out on blank input BEFORE builtins / cache / Qwen — while a normal command still
flows through untouched. Hermetic: no Ollama, no screen; execute_action and
ask_qwen are monkeypatched so nothing real runs.

Run with the project venv:
    .venv/Scripts/python.exe test_empty_command.py
"""

import sys
import json

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import prompt_cache
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


# --- 1. normalization: blank / punctuation-only -> "" ---------------------
print("\n1. normalize_prompt — punctuation-only collapses to empty")
for t in (".", " . ", "?", "!!", "  ", ",;:", "\"'"):
    check(f"norm {t!r}", prompt_cache.normalize_prompt(t), "")
check("norm 'open brave' stays", prompt_cache.normalize_prompt("open brave."), "open brave")


# --- 2. cache refuses the empty key (read + write) ------------------------
print("\n2. prompt cache — empty key never matches, never saves")
check("get_cached_action('.') -> None", prompt_cache.get_cached_action("."), None)
check("get_cached_action('') -> None", prompt_cache.get_cached_action(""), None)
check("get_cached_action('   ') -> None", prompt_cache.get_cached_action("   "), None)

# save under a blank key is a no-op — the real cache file is untouched and no
# "" key appears (this is the write-side guard against re-poisoning).
prompt_cache.save_action(".", {"action": "open_app", "target": "brave"})
prompt_cache.save_action("", {"action": "open_app", "target": "brave"})
disk = json.loads((prompt_cache.CACHE_FILE).read_text(encoding="utf-8"))
check_true("no '' key on disk after blank saves", "" not in disk)
check_true("cache file still valid json", isinstance(disk, dict))


# --- 3. process_command bails on blank input, runs real commands ----------
print("\n3. process_command — blank ignored, real command still flows")

_ran = []


def _recorder(action):
    _ran.append(action)
    return f"[executed {action.get('action')}]"


def _boom(*a, **k):
    raise AssertionError("ask_qwen must NOT be called for blank input")


agent.execute_action = _recorder
agent.ask_qwen = _boom

for blank in (".", "", "   ", "?!", ",,,"):
    _ran.clear()
    reply = agent.process_command(blank)
    check(f"blank {blank!r} -> polite miss", reply, "I didn't catch that.")
    check(f"blank {blank!r} -> nothing executed", _ran, [])

# a real command still resolves (via the cache) and executes — proves the guard
# only trips on genuinely empty input.
agent.ask_qwen = _boom  # still must not be needed; "open brave" is cached
agent.get_cached_action = lambda text: (
    {"action": "open_app", "target": "brave"}
    if prompt_cache.normalize_prompt(text) == "open brave" else None
)
_ran.clear()
reply = agent.process_command("open brave")
check("real command -> executed", _ran, [{"action": "open_app", "target": "brave"}])
check_true("real command -> result string", isinstance(reply, str) and "open_app" in reply)


print("\n" + ("ALL PASSED" if _failures == 0 else f"{_failures} CHECK(S) FAILED"))
sys.exit(1 if _failures else 0)
