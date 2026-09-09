"""
test_dry_run.py

Test suite for config.DRY_RUN mode across desktop actions and file actions.
"""

from pathlib import Path
import os
import config
import actions
import desktop_actions

_failures = 0


def check(label, got, expected):
    global _failures
    if got == expected:
        print(f"  [OK] {label}: got {got!r}")
    else:
        print(f"  [FAIL] {label}: got {got!r}, expected {expected!r}")
        _failures += 1


def check_contains(label, haystack, needle):
    global _failures
    if needle in haystack:
        print(f"  [OK] {label}: found {needle!r}")
    else:
        print(f"  [FAIL] {label}: {needle!r} not in {haystack!r}")
        _failures += 1


def main():
    print("=" * 50)
    print("1. Testing DRY_RUN mode for desktop & file actions")
    print("=" * 50)

    config.DRY_RUN = True

    # 1. type_text
    res = desktop_actions.type_text("hello world")
    check("dry-run type_text", res, "[DRY-RUN] Would type 'hello world'")

    # 2. press_key
    res = desktop_actions.press_key("enter")
    check("dry-run press_key", res, "[DRY-RUN] Would press 'enter'")

    # 3. scroll
    res = desktop_actions.scroll("down")
    check("dry-run scroll", res, "[DRY-RUN] Would scroll down")

    # 4. screenshot
    res = desktop_actions.take_screenshot()
    check("dry-run take_screenshot", res, "[DRY-RUN] Would take screenshot")

    # 5. wait
    res = desktop_actions.wait(2)
    check("dry-run wait", res, "[DRY-RUN] Would wait 2s")

    # 6. copy_file
    docs = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    src = docs / "dry_src.txt"
    dst = docs / "dry_dst.txt"
    src.write_text("dry test")
    try:
        res = actions.copy_file(str(src), str(dst))
        check_contains("dry-run copy_file", res, "[DRY-RUN] Would copy")
        check("destination not created in dry-run", dst.exists(), False)
    finally:
        if src.exists():
            src.unlink()

    # 7. move_file
    src.write_text("dry test")
    try:
        res = actions.move_file(str(src), str(dst))
        check_contains("dry-run move_file", res, "[DRY-RUN] Would move")
        check("source file still exists after dry-run move", src.exists(), True)
    finally:
        if src.exists():
            src.unlink()

    # 8. delete_file
    src.write_text("dry test")
    try:
        res = actions.delete_file(str(src))
        check_contains("dry-run delete_file", res, "[DRY-RUN] Would delete")
        check("source file still exists after dry-run delete", src.exists(), True)
    finally:
        if src.exists():
            src.unlink()

    config.DRY_RUN = False

    if _failures == 0:
        print("\nALL DRY-RUN TESTS PASSED")
    else:
        print(f"\n{_failures} TEST(S) FAILED")
        exit(1)


if __name__ == "__main__":
    main()
