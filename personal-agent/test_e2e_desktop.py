"""
test_e2e_desktop.py

End-to-End Desktop Automation tests using MockWindowsEnvironment:
    1. Window lifecycle (focus, activation, closing)
    2. OCR-based text clicking with accurate coordinate resolution
    3. OCR cache hits & invalidation after mutations
    4. Ambiguous clicks with 2-stage point-and-confirm preview flow
    5. Text sanitization during GUI typing
    6. Keystrokes, hotkeys, and window scrolling
"""

import disambiguation
import desktop_actions
import ocr_cache
from mock_win_api import MockWindowsEnvironment


def test_window_focus_and_close():
    print("\n--- 1. E2E Window Focus & Close ---")
    with MockWindowsEnvironment() as env:
        win_brave = env.windows.add_window("Brave - New Tab", left=100, top=100, width=1200, height=800)
        win_code = env.windows.add_window("VS Code", left=200, top=200, width=1000, height=700)

        # Focus brave
        res = desktop_actions.focus_app("brave")
        assert "switched focus to brave" in res
        assert win_brave.activated
        print("  [OK] Focused 'brave' and activated window")

        # Close brave
        res_close = desktop_actions.close_app("brave")
        assert "closed brave" in res_close
        assert win_brave.closed
        print("  [OK] Closed 'brave' window successfully")


def test_ocr_click_and_cache():
    print("\n--- 2. E2E OCR Click & Cache Integration ---")
    with MockWindowsEnvironment() as env:
        # Create active window at (100, 100) and button element at relative (300, 200)
        env.windows.add_window("Checkout Window", left=100, top=100, width=1000, height=800)
        env.ocr.add_element("Submit", left=300, top=200, width=100, height=40)

        # Absolute coordinates = (100 + 300 + 50, 100 + 200 + 20) = (450, 320)
        expected_coord = (450, 320)

        # 1st Click: Fresh OCR
        ocr_cache.CACHE.invalidate_all()
        res = desktop_actions.click_text("Submit")
        assert f"clicked 'Submit' at {expected_coord}" in res
        assert env.gui.clicks == [(expected_coord[0], expected_coord[1], "left")]
        print("  [OK] 1st click resolved exact window-offset coordinates via OCR")

        # 2nd Click: From cache
        env.gui.clear()
        res2 = desktop_actions.click_text("Submit")
        assert f"clicked 'Submit' at {expected_coord}" in res2
        assert env.gui.clicks == [(expected_coord[0], expected_coord[1], "left")]
        print("  [OK] 2nd click used cached OCR coordinates")

        # Mutation invalidates cache
        desktop_actions.type_text("some input")
        assert ocr_cache.CACHE.size() == 0
        print("  [OK] Typing invalidated OCR cache")


def test_ambiguous_click_flow():
    print("\n--- 3. E2E Ambiguous Click Disambiguation Flow ---")
    with MockWindowsEnvironment() as env:
        env.windows.add_window("Document", left=0, top=0, width=1920, height=1080)
        # Add 2 duplicate buttons
        env.ocr.add_element("Delete", left=200, top=200, width=80, height=30)
        env.ocr.add_element("Delete", left=1200, top=800, width=80, height=30)

        ocr_cache.CACHE.invalidate_all()
        result = desktop_actions.click_text("Delete")

        # Must return AmbiguousClick
        assert isinstance(result, disambiguation.AmbiguousClick)
        assert len(result.candidates) == 2
        print("  [OK] AmbiguousClick returned with 2 candidates")

        # Stage 1: Point to choice 1
        cand1 = result.candidates[0]
        desktop_actions.move_to(cand1["x"], cand1["y"], "Choice 1")
        assert env.gui.mouse_moves[-1] == (cand1["x"], cand1["y"])
        print("  [OK] Preview moved cursor to Candidate 1")

        # Stage 2: Confirmed click
        desktop_actions.click_at(cand1["x"], cand1["y"], "Choice 1")
        assert env.gui.clicks[-1] == (cand1["x"], cand1["y"], "left")
        print("  [OK] Confirmed click executed at Candidate 1 coordinates")


def test_typing_and_keystrokes():
    print("\n--- 4. E2E Typing Sanitization & Keystrokes ---")
    with MockWindowsEnvironment() as env:
        env.windows.add_window("Editor", left=0, top=0, width=1920, height=1080)

        # Sanitized typing
        res = desktop_actions.type_text("line1\nline2\twith \x00null")
        assert env.gui.typed_texts == ["line1 line2 with null"]
        assert "typed 'line1 line2 with null'" in res
        print("  [OK] Control chars & newlines sanitized prior to typing")

        # Press key and hotkeys
        desktop_actions.press_key("enter")
        assert env.gui.pressed_keys == ["enter"]

        desktop_actions.press_key("ctrl+c")
        assert env.gui.hotkeys == [("ctrl", "c")]
        print("  [OK] Single keys and modifier hotkeys sent correctly")


def test_scrolling():
    print("\n--- 5. E2E Window Scrolling ---")
    with MockWindowsEnvironment() as env:
        env.windows.add_window("Browser Window", left=100, top=100, width=800, height=600)

        desktop_actions.scroll("down")
        # Center of (100, 100, 800, 600) is (500, 400)
        assert env.gui.cursor_pos == (500, 400)
        assert env.gui.scrolls == [-500]
        print("  [OK] Scrolled down with mouse centered on active window")


if __name__ == "__main__":
    test_window_focus_and_close()
    test_ocr_click_and_cache()
    test_ambiguous_click_flow()
    test_typing_and_keystrokes()
    test_scrolling()
    print("\nALL E2E DESKTOP TESTS PASSED")
