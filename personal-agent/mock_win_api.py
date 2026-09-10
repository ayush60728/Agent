"""
mock_win_api.py

Hermetic mock Windows API layer for testing window focus, desktop actions,
OCR text detection, and GUI automation without requiring a physical screen or microphone.

Components:
    - MockWindow / MockWindowManager (replaces pygetwindow)
    - MockOCR (replaces pytesseract)
    - MockPyAutoGUI (replaces pyautogui)
    - MockAudioEnvironment (simulates voice inputs for speech_recognition / voice_io)
    - MockWindowsEnvironment (context manager patching all desktop subsystems)
"""

import sys
from typing import List, Dict, Tuple, Optional, Any
from unittest.mock import patch, MagicMock
from PIL import Image


# ── 1. Mock Window Manager (pygetwindow) ─────────────────────────────────────

class MockWindow:
    """Represents a simulated Windows desktop application window."""

    def __init__(
        self,
        title: str,
        left: int = 100,
        top: int = 100,
        width: int = 1200,
        height: int = 800,
        isMinimized: bool = False,
        manager: Optional["MockWindowManager"] = None,
    ):
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.isMinimized = isMinimized
        self.closed = False
        self.activated = False
        self._manager = manager

    def activate(self) -> None:
        self.activated = True
        if self._manager:
            self._manager._active_window = self

    def restore(self) -> None:
        self.isMinimized = False

    def close(self) -> None:
        self.closed = True
        if self._manager and self._manager._active_window == self:
            self._manager._active_window = None

    def __repr__(self) -> str:
        return (
            f"<MockWindow title={self.title!r} "
            f"bounds=({self.left}, {self.top}, {self.width}, {self.height}) "
            f"minimized={self.isMinimized} closed={self.closed}>"
        )


class MockWindowManager:
    """Simulates pygetwindow functions getAllWindows() and getActiveWindow()."""

    def __init__(self):
        self._windows: List[MockWindow] = []
        self._active_window: Optional[MockWindow] = None

    def add_window(
        self,
        title: str,
        left: int = 100,
        top: int = 100,
        width: int = 1200,
        height: int = 800,
        isMinimized: bool = False,
        active: bool = True,
    ) -> MockWindow:
        win = MockWindow(
            title=title,
            left=left,
            top=top,
            width=width,
            height=height,
            isMinimized=isMinimized,
            manager=self,
        )
        self._windows.append(win)
        if active:
            self._active_window = win
            win.activated = True
        return win

    def getAllWindows(self) -> List[MockWindow]:
        return [w for w in self._windows if not w.closed]

    def getActiveWindow(self) -> Optional[MockWindow]:
        if self._active_window and not self._active_window.closed:
            return self._active_window
        live = self.getAllWindows()
        return live[0] if live else None

    def clear(self) -> None:
        self._windows.clear()
        self._active_window = None


# ── 2. Mock OCR (pytesseract) ────────────────────────────────────────────────

class MockUIElement:
    """A simulated on-screen text element with bounding box and confidence."""

    def __init__(
        self,
        text: str,
        left: int,
        top: int,
        width: int,
        height: int,
        conf: float = 95.0,
    ):
        self.text = text
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.conf = conf


class MockOCR:
    """Simulates pytesseract.image_to_data by returning structured bounding boxes."""

    def __init__(self):
        self.elements: List[MockUIElement] = []

    def add_element(
        self,
        text: str,
        left: int,
        top: int,
        width: int = 80,
        height: int = 25,
        conf: float = 95.0,
    ) -> None:
        self.elements.append(
            MockUIElement(text=text, left=left, top=top, width=width, height=height, conf=conf)
        )

    def clear(self) -> None:
        self.elements.clear()

    def image_to_data(self, image: Any, output_type: Any = None) -> Dict[str, List[Any]]:
        """Produces pytesseract.Output.DICT output format."""
        data: Dict[str, List[Any]] = {
            "text": [],
            "conf": [],
            "left": [],
            "top": [],
            "width": [],
            "height": [],
        }

        for el in self.elements:
            words = el.text.split()
            if not words:
                continue
            word_w = max(1, el.width // len(words))
            for i, word in enumerate(words):
                data["text"].append(word)
                data["conf"].append(str(int(el.conf)))
                data["left"].append(el.left + i * word_w)
                data["top"].append(el.top)
                data["width"].append(word_w)
                data["height"].append(el.height)

        return data


# ── 3. Mock PyAutoGUI (pyautogui) ────────────────────────────────────────────

class MockPyAutoGUI:
    """Simulates mouse and keyboard automation and records all actions."""

    def __init__(self, screen_size: Tuple[int, int] = (1920, 1080)):
        self.screen_width, self.screen_height = screen_size
        self.cursor_pos: Tuple[int, int] = (0, 0)
        self.PAUSE = 0.0

        # Action logs
        self.clicks: List[Tuple[int, int, str]] = []  # (x, y, button)
        self.mouse_moves: List[Tuple[int, int]] = []   # (x, y)
        self.typed_texts: List[str] = []
        self.pressed_keys: List[str] = []
        self.hotkeys: List[Tuple[str, ...]] = []
        self.scrolls: List[int] = []
        self.screenshots_taken: int = 0

        # Recognized key names matching standard pyautogui
        self.KEYBOARD_KEYS = [
            "enter", "return", "tab", "space", "backspace", "delete",
            "escape", "esc", "ctrl", "alt", "shift", "win", "f4", "f5",
            "up", "down", "left", "right", "w", "s", "c", "v", "z", "q", "t",
        ]

    def size(self) -> Tuple[int, int]:
        return (self.screen_width, self.screen_height)

    def position(self) -> Tuple[int, int]:
        return self.cursor_pos

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:
        self.cursor_pos = (x, y)
        self.mouse_moves.append((x, y))

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> None:
        cx = self.cursor_pos[0] if x is None else x
        cy = self.cursor_pos[1] if y is None else y
        self.cursor_pos = (cx, cy)
        self.clicks.append((cx, cy, button))

    def write(self, text: str, interval: float = 0.0) -> None:
        self.typed_texts.append(text)

    def press(self, key: str) -> None:
        self.pressed_keys.append(key)

    def hotkey(self, *keys: str) -> None:
        self.hotkeys.append(keys)

    def scroll(self, clicks: int) -> None:
        self.scrolls.append(clicks)

    def pixel(self, x: int, y: int) -> Tuple[int, int, int]:
        return (255, 255, 255)

    def screenshot(self, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        self.screenshots_taken += 1
        if region:
            _, _, w, h = region
            w, h = max(1, w), max(1, h)
        else:
            w, h = self.screen_width, self.screen_height
        return Image.new("RGB", (w, h), color=(240, 240, 240))

    def clear(self) -> None:
        self.clicks.clear()
        self.mouse_moves.clear()
        self.typed_texts.clear()
        self.pressed_keys.clear()
        self.hotkeys.clear()
        self.scrolls.clear()
        self.screenshots_taken = 0


# ── 4. Mock Audio Environment (speech_recognition / voice_io) ────────────────

class MockAudioEnvironment:
    """Feeds canned speech transcripts into voice_io._listen_on_source."""

    def __init__(self, canned_inputs: Optional[List[str]] = None):
        self._inputs: List[str] = list(canned_inputs or [])
        self.spoken_replies: List[str] = []

    def queue_input(self, text: str) -> None:
        self._inputs.append(text)

    def pop_input(self) -> Optional[str]:
        return self._inputs.pop(0) if self._inputs else None

    def mock_listen(self, *args, **kwargs) -> Optional[str]:
        return self.pop_input()

    def mock_speak(self, text: str) -> None:
        self.spoken_replies.append(text)


# ── 5. Unified Mock Environment Context Manager ──────────────────────────────

class MockWindowsEnvironment:
    """
    Context manager that patches pygetwindow, pytesseract, and pyautogui
    with hermetic mock implementations.

    Usage:
        with MockWindowsEnvironment() as env:
            env.windows.add_window("Brave", 100, 100, 800, 600)
            env.ocr.add_element("Submit", left=200, top=150)
            desktop_actions.click_text("Submit")
            assert len(env.gui.clicks) == 1
    """

    def __init__(self, screen_size: Tuple[int, int] = (1920, 1080)):
        self.windows = MockWindowManager()
        self.ocr = MockOCR()
        self.gui = MockPyAutoGUI(screen_size=screen_size)
        self.audio = MockAudioEnvironment()
        self._patches: List[Any] = []

    def __enter__(self) -> "MockWindowsEnvironment":
        # 1. Patch pygetwindow
        p_gw_all = patch("pygetwindow.getAllWindows", side_effect=self.windows.getAllWindows)
        p_gw_act = patch("pygetwindow.getActiveWindow", side_effect=self.windows.getActiveWindow)

        # 2. Patch pytesseract
        p_ocr = patch("pytesseract.image_to_data", side_effect=self.ocr.image_to_data)

        # 3. Patch pyautogui in desktop_actions and global
        p_gui_click = patch("pyautogui.click", side_effect=self.gui.click)
        p_gui_move = patch("pyautogui.moveTo", side_effect=self.gui.moveTo)
        p_gui_write = patch("pyautogui.write", side_effect=self.gui.write)
        p_gui_press = patch("pyautogui.press", side_effect=self.gui.press)
        p_gui_hotkey = patch("pyautogui.hotkey", side_effect=self.gui.hotkey)
        p_gui_scroll = patch("pyautogui.scroll", side_effect=self.gui.scroll)
        p_gui_size = patch("pyautogui.size", side_effect=self.gui.size)
        p_gui_pos = patch("pyautogui.position", side_effect=self.gui.position)
        p_gui_pixel = patch("pyautogui.pixel", side_effect=self.gui.pixel)
        p_gui_shot = patch("pyautogui.screenshot", side_effect=self.gui.screenshot)

        self._patches = [
            p_gw_all, p_gw_act, p_ocr,
            p_gui_click, p_gui_move, p_gui_write, p_gui_press,
            p_gui_hotkey, p_gui_scroll, p_gui_size, p_gui_pos,
            p_gui_pixel, p_gui_shot,
        ]

        for p in self._patches:
            p.start()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._patches.clear()
