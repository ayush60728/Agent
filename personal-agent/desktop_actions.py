import os
import time
from pathlib import Path

import pyautogui
import pytesseract
import pygetwindow as gw
from PIL import Image

import color_vision

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


def find_text_matches(target_text: str):
    """OCR the active window (not the whole screen) and return EVERY match for
    target_text as a list of (center_x, center_y, (left, top, width, height))
    tuples in absolute screen coordinates. Empty list if nothing matches.

    find_text_on_screen() wraps this for callers that only want the first hit;
    the bounding boxes are what the color tiebreaker needs to sample each
    candidate's on-screen area."""
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
    matches = []

    for i in range(len(data['text'])):
        word = data['text'][i].strip().lower()
        if not word:
            continue
        # simple substring match — good enough for single words/short phrases
        if target_lower in word or word in target_lower:
            l = offset_x + data['left'][i]
            t = offset_y + data['top'][i]
            w = data['width'][i]
            h = data['height'][i]
            matches.append((l + w // 2, t + h // 2, (l, t, w, h)))

    return matches


def find_text_on_screen(target_text: str):
    """(x, y) center of the first on-screen match for target_text, or None."""
    matches = find_text_matches(target_text)
    return (matches[0][0], matches[0][1]) if matches else None


def _padded_region_shot(l, t, w, h, pad_ratio=0.4):
    """Screenshot a box padded around (l, t, w, h) so a button's fill — not
    just its text glyphs — is included when we sample its color. Clamped to
    the screen so an edge element can't ask for an off-screen region."""
    pad_x = int(w * pad_ratio)
    pad_y = int(h * pad_ratio)
    rl = max(0, l - pad_x)
    rt = max(0, t - pad_y)
    sw, sh = pyautogui.size()
    rw = min(w + 2 * pad_x, sw - rl)
    rh = min(h + 2 * pad_y, sh - rt)
    if rw <= 0 or rh <= 0:
        return pyautogui.screenshot(region=(max(0, l), max(0, t), max(1, w), max(1, h)))
    return pyautogui.screenshot(region=(rl, rt, rw, rh))


def _pick_by_color(matches, color, floor=0.06):
    """Among OCR matches [(cx, cy, box), ...], return the (x, y) of the one
    whose padded region most strongly shows `color`. Falls back to the first
    match if none clearly show the color (the color was only a hint — better
    to click the text we found than to refuse)."""
    best = None  # (fraction, (cx, cy))
    for cx, cy, (l, t, w, h) in matches:
        try:
            region = _padded_region_shot(l, t, w, h)
            frac = color_vision.color_fraction(region, color)
        except Exception:
            frac = 0.0
        if best is None or frac > best[0]:
            best = (frac, (cx, cy))
    if best and best[0] >= floor:
        return best[1]
    return (matches[0][0], matches[0][1])


def click_text(target_text: str) -> str:
    # A color word ("red submit") is a disambiguator, not part of the text to
    # find — pull it out and use it to choose among look-alike matches.
    color, text = color_vision.extract_color(target_text)
    locate = text if (color and text) else target_text

    matches = find_text_matches(locate)
    if matches:
        if color and len(matches) > 1:
            pos = _pick_by_color(matches, color)
            return _do_click(pos, f"the {color} '{locate}'")
        # One match (color moot) or no color asked for: click the first hit.
        return _do_click((matches[0][0], matches[0][1]),
                         f"the {color} '{locate}'" if color else f"'{locate}'")

    # OCR found no text. Fall back to the Windows UI Automation tree, which
    # exposes icon-only controls (hamburger menu, gear, back arrow, close X)
    # by their accessible name. Lazy import + guarded so a machine without the
    # UIA library still runs everything else.
    try:
        from ui_automation import find_control_center
        pos = find_control_center(locate)
        if pos is not None:
            print(f"(no visible text — matched '{locate}' "
                  f"via the accessibility tree)")
            return _do_click(pos, f"'{locate}'")
    except Exception:
        pass

    return f"couldn't find '{target_text}' on screen"


def _do_click(pos, label) -> str:
    pyautogui.click(pos[0], pos[1])
    return f"clicked {label} at {pos}"


def get_color(target: str = "") -> str:
    """Name a color on screen: the pixel under the cursor (empty / "this" /
    "here" target), or the dominant color of a named element ("what color is
    the submit button")."""
    cleaned = (target or "").strip().strip(".!?,").strip().lower()
    cursor_words = {
        "", "this", "that", "it", "here", "cursor", "the cursor",
        "under the cursor", "mouse", "pointer", "the pointer",
    }

    if cleaned in cursor_words:
        try:
            x, y = pyautogui.position()
            r, g, b = pyautogui.pixel(x, y)
        except Exception as e:
            return f"couldn't read the color under the cursor: {e}"
        return (f"the color under the cursor is {color_vision.name_color(r, g, b)} "
                f"(RGB {r}, {g}, {b})")

    # Named element: bring the tracked app forward (so we OCR the right
    # window), locate the element (OCR first, then the accessibility tree),
    # and name the dominant color of its area.
    if _current_focus_app:
        focus_app(_current_focus_app)

    matches = find_text_matches(cleaned)
    if matches:
        _, _, box = matches[0]
        try:
            region = _padded_region_shot(*box)
            cname = color_vision.dominant_chromatic_color(region)
        except Exception as e:
            return f"found '{cleaned}' but couldn't read its color: {e}"
        return f"the '{cleaned}' looks {cname}"

    pos = None
    try:
        from ui_automation import find_control_center
        pos = find_control_center(cleaned)
    except Exception:
        pos = None
    if pos is None:
        return f"couldn't find '{cleaned}' on screen to read its color"
    try:
        r, g, b = pyautogui.pixel(pos[0], pos[1])
    except Exception as e:
        return f"found '{cleaned}' but couldn't read its color: {e}"
    return f"the '{cleaned}' looks {color_vision.name_color(r, g, b)}"


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