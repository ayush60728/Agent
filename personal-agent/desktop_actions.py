import os
import time
from pathlib import Path

import pyautogui
import pytesseract
import pygetwindow as gw
from PIL import Image

pyautogui.PAUSE = 0.1  # small delay after each pyautogui call, avoids race conditions

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# How far one "scroll up/down" command moves. pyautogui.scroll() takes
# wheel "clicks"; this is a moderate, clearly-visible step. Tunable.
SCROLL_STEP = 500


# Tracks which app the agent currently considers "in focus" — set by
# open_app (auto-focus on launch) or focus_app (explicit user request).
# Used to sanity-check that the app is still running before click/type/key.
_current_focus_app = None


def _find_window(app_name: str):
    """Find the first window whose title contains app_name (case-insensitive,
    substring match — 'brave' matches 'New Tab - Brave')."""
    target = app_name.lower().strip()
    for w in gw.getAllWindows():
        if w.title and target in w.title.lower():
            return w
    return None


def focus_app(app_name: str, retries: int = 1, retry_delay: float = 0.5) -> str:
    """Bring the named app's window to the foreground and remember it as
    the 'current' app for later click_text/type_text/press_key calls.

    retries/retry_delay let callers (like open_app, right after launching
    something) give the window a moment to actually appear before giving up.
    """
    global _current_focus_app

    win = None
    for attempt in range(retries + 1):
        win = _find_window(app_name)
        if win is not None:
            break
        if attempt < retries:
            time.sleep(retry_delay)

    if win is None:
        return f"{app_name} is not running"

    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        # Give Windows a beat to actually complete the focus switch
        # before any click/type/key fires — activate() returning doesn't
        # guarantee the OS has finished the transition yet.
        time.sleep(0.15)
    except Exception:
        # Some windows resist activation (admin-elevated, certain UWP apps).
        # Best effort — we still track it as the intended focus target.
        pass

    _current_focus_app = app_name
    return f"switched focus to {app_name}"


def check_focus_app_running() -> str | None:
    """If we're tracking a 'current' app, confirm its window still exists.
    Returns an error message if it's gone, or None if there's nothing to
    check (no app tracked yet) or it's still running fine."""
    if _current_focus_app is None:
        return None

    if _find_window(_current_focus_app) is None:
        return f"{_current_focus_app} is not running"

    return None


def get_current_focus_app() -> str | None:
    return _current_focus_app


def close_app(target: str = "") -> str:
    """Close a window: either a named app ("close brave") or whatever we're
    currently tracking as focused ("close it", "close this window").

    Uses the window's own close() — a graceful WM_CLOSE, exactly like
    clicking the X button — and falls back to focusing the window and
    sending Alt+F4 if that fails. Unlike click/type/key this doesn't need
    the window in the foreground first (it posts the close straight to the
    window handle), so it works even while this terminal has focus.

    If we close the app we were tracking as focused, we stop tracking it so
    the next click/type/key command doesn't try to reactivate a dead window.
    """
    global _current_focus_app

    # Strip filler/pronoun phrasing and trailing punctuation ("it.", "this
    # window") down to something we can match, or empty for "the current app".
    cleaned = target.strip().strip(".!?,").strip() if target else ""
    generic = {
        "", "it", "this", "that", "app", "the app", "this app",
        "window", "the window", "this window", "current", "current app",
        "the current app", "current window", "the current window",
    }

    if cleaned.lower() in generic:
        if not _current_focus_app:
            return ("no app is focused yet - say 'close <app name>', "
                    "or open/focus an app first")
        app_label = _current_focus_app
    else:
        app_label = cleaned

    win = _find_window(app_label)
    if win is None:
        return f"{app_label} is not running"

    try:
        win.close()
    except Exception:
        # Some windows reject PostMessage(WM_CLOSE); fall back to bringing
        # the window forward and sending Alt+F4.
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.15)
            pyautogui.hotkey("alt", "f4")
        except Exception as e:
            return f"couldn't close {app_label}: {e}"

    if _current_focus_app and app_label.lower() == _current_focus_app.lower():
        _current_focus_app = None

    return f"closed {app_label}"


def find_text_on_screen(target_text: str):
    """Screenshot the active window (not the whole screen), OCR it,
    return (x, y) absolute screen coordinates of the first match's
    center, or None."""
    active = gw.getActiveWindow()

    if active is not None and active.width > 0 and active.height > 0:
        left, top, width, height = active.left, active.top, active.width, active.height
        screenshot = pyautogui.screenshot(region=(left, top, width, height))
        offset_x, offset_y = left, top
    else:
        # Fallback: no active window info available, scan everything.
        screenshot = pyautogui.screenshot()
        offset_x, offset_y = 0, 0

    data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)

    target_lower = target_text.lower().strip()
    n_boxes = len(data['text'])

    for i in range(n_boxes):
        word = data['text'][i].strip().lower()
        if not word:
            continue
        # simple substring match — good enough for single words/short phrases
        if target_lower in word or word in target_lower:
            x = offset_x + data['left'][i] + data['width'][i] // 2
            y = offset_y + data['top'][i] + data['height'][i] // 2
            return (x, y)

    return None


def click_text(target_text: str) -> str:
    pos = find_text_on_screen(target_text)
    if pos is None:
        return f"couldn't find '{target_text}' on screen"
    pyautogui.click(pos[0], pos[1])
    return f"clicked '{target_text}' at {pos}"


def type_text(text: str) -> str:
    pyautogui.write(text, interval=0.02)
    return f"typed '{text}'"


def press_key(key: str) -> str:
    # Supports combos like "ctrl+s" or single keys like "enter". Split on
    # '+', trim whitespace around each part ("ctrl + s" is common from the
    # LLM), lowercase, and drop empties so a stray/trailing '+' can't crash us.
    parts = [k.strip().lower() for k in key.split('+') if k.strip()]
    if not parts:
        return "I need a key to press."

    # pyautogui.press() silently no-ops on an unknown key name, which reads to
    # the user as "nothing happened" with no explanation — so check up front
    # and say what we didn't recognise instead.
    valid = set(pyautogui.KEYBOARD_KEYS)
    unknown = [k for k in parts if k not in valid]
    if unknown:
        return f"I don't recognise the key(s): {', '.join(unknown)}"

    try:
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
    except Exception as e:
        return f"couldn't press '{key}': {e}"
    return f"pressed '{key}'"


def scroll(direction: str, clicks: int = SCROLL_STEP) -> str:
    """Scroll the focused window up or down.

    Mouse-wheel events go to whatever window is under the cursor, not
    necessarily the focused app — so first move the cursor over the center
    of the active window (the agent re-focuses the target app right before
    this runs), then scroll. Positive clicks scroll up, negative down."""
    d = direction.strip().lower()
    if d in ("up", "u"):
        amount = clicks
    elif d in ("down", "d"):
        amount = -clicks
    else:
        return f"I can only scroll 'up' or 'down', not '{direction}'."

    active = gw.getActiveWindow()
    if active is not None and active.width > 0 and active.height > 0:
        cx = active.left + active.width // 2
        cy = active.top + active.height // 2
        pyautogui.moveTo(cx, cy)

    pyautogui.scroll(amount)
    return f"scrolled {d}"


def take_screenshot(name: str = "") -> str:
    """Capture the whole screen to a PNG in the user's Pictures folder
    (falling back to the home directory) and return the saved path."""
    pictures = Path(os.environ.get("USERPROFILE", "")) / "Pictures"
    target_dir = pictures if pictures.is_dir() else Path.home()

    fname = (name or "").strip() or f"agent_screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    if not fname.lower().endswith(".png"):
        fname += ".png"

    path = target_dir / fname
    try:
        pyautogui.screenshot().save(str(path))
    except Exception as e:
        return f"couldn't take a screenshot: {e}"
    return f"saved screenshot to {path}"


def wait(seconds) -> str:
    time.sleep(float(seconds))
    return f"waited {seconds}s"