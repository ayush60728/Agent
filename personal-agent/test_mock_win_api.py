"""
test_mock_win_api.py

Unit tests to verify mock_win_api.py behavior and environment patching.
"""

import pygetwindow as gw
import pytesseract
import pyautogui
pyautogui.FAILSAFE = False
from mock_win_api import MockWindowsEnvironment, MockWindowManager, MockOCR, MockPyAutoGUI


def test_window_manager():
    print("\n--- 1. MockWindowManager tests ---")
    wm = MockWindowManager()
    w1 = wm.add_window("Browser", 50, 50, 1000, 700)
    w2 = wm.add_window("Editor", 100, 100, 800, 600)

    assert len(wm.getAllWindows()) == 2
    assert wm.getActiveWindow() == w2
    print("  [OK] Added windows and correctly set active window")

    w1.activate()
    assert wm.getActiveWindow() == w1
    print("  [OK] Switched active window via activate()")

    w2.close()
    assert len(wm.getAllWindows()) == 1
    assert wm.getAllWindows()[0] == w1
    print("  [OK] Window close removes from active window list")


def test_ocr_data():
    print("\n--- 2. MockOCR tests ---")
    ocr = MockOCR()
    ocr.add_element("Submit Order", left=200, top=300, width=100, height=40)
    data = ocr.image_to_data(None)

    assert data["text"] == ["Submit", "Order"]
    assert len(data["left"]) == 2
    assert data["top"] == [300, 300]
    print("  [OK] image_to_data returns valid word-split bounding box dict")


def test_pyautogui_mock():
    print("\n--- 3. MockPyAutoGUI tests ---")
    gui = MockPyAutoGUI()
    gui.click(150, 250, button="left")
    gui.write("hello")
    gui.press("enter")
    gui.hotkey("ctrl", "c")
    gui.scroll(-300)

    assert gui.clicks == [(150, 250, "left")]
    assert gui.typed_texts == ["hello"]
    assert gui.pressed_keys == ["enter"]
    assert gui.hotkeys == [("ctrl", "c")]
    assert gui.scrolls == [-300]
    print("  [OK] Recorded click, write, press, hotkey, and scroll actions")


def test_environment_context_manager():
    print("\n--- 4. MockWindowsEnvironment context manager tests ---")
    with MockWindowsEnvironment() as env:
        env.windows.add_window("My App", 0, 0, 1920, 1080)
        env.ocr.add_element("Login", 500, 400, 100, 30)

        # PyGetWindow check
        wins = gw.getAllWindows()
        assert len(wins) == 1
        assert wins[0].title == "My App"

        # PyTesseract check
        ocr_res = pytesseract.image_to_data(None)
        assert ocr_res["text"] == ["Login"]

        # PyAutoGUI check
        pyautogui.click(550, 415)
        assert env.gui.clicks == [(550, 415, "left")]

    print("  [OK] Environment patched and verified seamlessly")


if __name__ == "__main__":
    test_window_manager()
    test_ocr_data()
    test_pyautogui_mock()
    test_environment_context_manager()
    print("\nALL MOCK WIN API TESTS PASSED")
