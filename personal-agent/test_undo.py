"""
test_undo.py

Test suite for undo_stack.py and reversible action execution.
"""

from pathlib import Path
import os
import shutil
import undo_stack
import actions
import config

_failures = 0


def check(label, got, expected):
    global _failures
    if got == expected:
        print(f"  [OK] {label}: got {got!r}")
    else:
        print(f"  [FAIL] {label}: got {got!r}, expected {expected!r}")
        _failures += 1


def main():
    print("=" * 50)
    print("1. Testing undo_stack basic stack ops")
    print("=" * 50)

    undo_stack.clear()
    check("empty stack count", undo_stack.count(), 0)
    check("undo on empty stack", undo_stack.undo_last(), "Nothing to undo.")

    # Push dummy item
    dummy_ran = False
    def _revert():
        nonlocal dummy_ran
        dummy_ran = True
        return "reverted dummy"

    undo_stack.push_undo("test", _revert, "test action")
    check("stack count after push", undo_stack.count(), 1)
    check("peek description", undo_stack.peek_description(), "test action")

    res = undo_stack.undo_last()
    check("undo execution result", res, "Undone: test action (reverted dummy)")
    check("dummy function executed", dummy_ran, True)
    check("stack count after undo", undo_stack.count(), 0)

    print("\n" + "=" * 50)
    print("2. Testing move_file and undo")
    print("=" * 50)

    config.DRY_RUN = False
    docs = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    src = docs / "test_undo_src.txt"
    dst = docs / "test_undo_dst.txt"

    # Setup file
    src.write_text("undo test content")

    try:
        # Move file
        move_res = actions.move_file(str(src), str(dst))
        check("move_file result", move_res, f"Moved '{src}' to '{dst}'.")
        check("src no longer exists", src.exists(), False)
        check("dst exists", dst.exists(), True)

        # Undo move
        undo_res = actions.execute_action({"action": "undo", "target": ""})
        print(" ", undo_res)
        check("src restored", src.exists(), True)
        check("dst deleted after undo move", dst.exists(), False)
    finally:
        if src.exists():
            src.unlink()
        if dst.exists():
            dst.unlink()

    print("\n" + "=" * 50)
    print("3. Testing copy_file and undo")
    print("=" * 50)

    src = docs / "test_copy_src.txt"
    dst = docs / "test_copy_dst.txt"
    src.write_text("copy test content")

    try:
        copy_res = actions.copy_file(str(src), str(dst))
        check("copy_file result", copy_res, f"Copied '{src}' to '{dst}'.")
        check("dst copy created", dst.exists(), True)

        undo_res = actions.execute_action({"action": "undo", "target": ""})
        print(" ", undo_res)
        check("copy deleted after undo", dst.exists(), False)
        check("original src still exists", src.exists(), True)
    finally:
        if src.exists():
            src.unlink()
        if dst.exists():
            dst.unlink()

    if _failures == 0:
        print("\nALL UNDO TESTS PASSED")
    else:
        print(f"\n{_failures} TEST(S) FAILED")
        exit(1)


if __name__ == "__main__":
    main()
