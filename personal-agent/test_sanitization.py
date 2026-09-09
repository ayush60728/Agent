"""
test_sanitization.py

Test suite for keystroke and text sanitization in desktop_actions.py.
"""

from desktop_actions import sanitize_text, type_text
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
    print("1. Testing sanitize_text()")
    print("=" * 50)

    # 1. Plain text remains unchanged
    check("plain text", sanitize_text("hello world"), "hello world")

    # 2. Raw newlines \n, \r converted to spaces
    check("newlines removed", sanitize_text("hello\nworld\r\ntest"), "hello world test")

    # 3. Tabs \t converted to space
    check("tabs removed", sanitize_text("search\tterm"), "search term")

    # 4. ASCII control characters (0x00 - 0x1F, 0x7F) stripped
    check("control chars stripped", sanitize_text("hello\x00\x07world\x1f!"), "helloworld!")

    # 5. Mixed prompt injection attempt
    check("prompt injection text", sanitize_text("youtube.com\nrm -rf /"), "youtube.com rm -rf /")

    print("\n" + "=" * 50)
    print("2. Testing type_text() with DRY_RUN")
    print("=" * 50)

    config.DRY_RUN = True
    check("dry run type_text", type_text("youtube.com\nrm -rf /"), "[DRY-RUN] Would type 'youtube.com rm -rf /'")
    config.DRY_RUN = False

    if _failures == 0:
        print("\nALL SANITIZATION TESTS PASSED")
    else:
        print(f"\n{_failures} TEST(S) FAILED")
        exit(1)


if __name__ == "__main__":
    main()
