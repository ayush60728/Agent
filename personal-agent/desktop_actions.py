import os
import time
from collections import namedtuple
from pathlib import Path

import pyautogui
import pytesseract
import pygetwindow as gw
from PIL import Image

import color_vision
import disambiguation

pyautogui.PAUSE = 0.1  # small delay after each pyautogui call, avoids race conditions

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# How far one "scroll up/down" command moves. pyautogui.scroll() takes
# wheel "clicks"; this is a moderate, clearly-visible step. Tunable.
SCROLL_STEP = 500

# OCR confidence (0-100) below which a *fuzzy* (non-exact) text match is treated
# as noise and dropped. An exact word match is always kept, however low its
# confidence — if OCR read the exact word, we trust it. Tunable.
CONF_FLOOR = 40

# One scored OCR (or consolidated) match:
#   cx, cy : click point (center), absolute screen coords
#   box    : (left, top, width, height), absolute screen coords
#   score  : match quality (exact vs partial) x OCR confidence, 0.0-1.0
#   exact  : the OCR word equalled the target exactly
Match = namedtuple("Match", ["cx", "cy", "box", "score", "exact"])


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


def _active_window_bounds():
    """(left, top, width, height) of the active window in absolute screen
    coords, or the full screen if no active-window info is available. The one
    source of truth for both OCR region and position labels."""
    active = gw.getActiveWindow()
    if active is not None and active.width > 0 and active.height > 0:
        return (active.left, active.top, active.width, active.height)
    sw, sh = pyautogui.size()
    return (0, 0, sw, sh)


def _conf_val(raw) -> float:
    """pytesseract conf comes through as a str ('96') or number, with -1 for
    non-text rows. Coerce to float; unparseable -> -1 (treated as no text)."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return -1.0


def _lrtb(box):
    """(left, top, width, height) -> (left, top, right, bottom)."""
    l, t, w, h = box
    return l, t, l + w, t + h


def _group_bounds(group):
    """Union (left, top, right, bottom) over a group of Matches' boxes."""
    ls = [_lrtb(m.box) for m in group]
    return (min(x[0] for x in ls), min(x[1] for x in ls),
            max(x[2] for x in ls), max(x[3] for x in ls))


def _boxes_mergeable(group_lrtb, box) -> bool:
    """Should `box` join a group whose union is `group_lrtb`? True when they're
    on the same text line (vertical spans overlap) AND horizontally overlap or
    sit within a small gap — the signature of one label OCR split into separate
    word boxes ('Sign' + 'In'). The gap threshold is a fraction of line height,
    kept small so two genuinely separate same-text buttons on one line stay
    distinct (they're normally spaced much further apart)."""
    gl, gt, gr, gb = group_lrtb
    bl, bt, br, bb = _lrtb(box)

    if min(gb, bb) - max(gt, bt) <= 0:  # no vertical overlap -> different lines
        return False

    if bl > gr:
        h_gap = bl - gr
    elif gl > br:
        h_gap = gl - br
    else:
        h_gap = 0  # horizontally overlapping
    line_h = min(gb - gt, bb - bt)
    return h_gap <= 0.6 * max(line_h, 1)


def _combine(group) -> Match:
    """Collapse a group of Matches into one: union box + center, best score,
    exact if any member was exact."""
    gl, gt, gr, gb = _group_bounds(group)
    w, h = gr - gl, gb - gt
    return Match((gl + gr) // 2, (gt + gb) // 2, (gl, gt, w, h),
                 max(m.score for m in group), any(m.exact for m in group))


def _merge_matches(matches):
    """Coalesce boxes that belong to one on-screen element (overlapping, or an
    adjacent same-line word fragment). O(n^2) transitive grouping — n is the
    handful of OCR hits for one target, so this is cheap."""
    if not matches:
        return []
    used = [False] * len(matches)
    out = []
    for i in range(len(matches)):
        if used[i]:
            continue
        group = [matches[i]]
        used[i] = True
        changed = True
        while changed:
            changed = False
            bounds = _group_bounds(group)
            for j in range(len(matches)):
                if used[j]:
                    continue
                if _boxes_mergeable(bounds, matches[j].box):
                    group.append(matches[j])
                    used[j] = True
                    changed = True
        out.append(_combine(group))
    return out


def find_text_matches(target_text: str):
    """OCR the active window (not the whole screen) and return the scored,
    consolidated matches for target_text as a list of Match tuples in absolute
    screen coordinates, best-first. Empty list if nothing matches.

    Beyond finding hits, this ranks them: an exact word match outranks a partial
    one, and OCR's own per-word confidence breaks ties and filters noise (a
    low-confidence fuzzy hit is dropped). Overlapping/adjacent same-line boxes
    are merged so a split label ('Sign' + 'In') or a multi-word target counts as
    one candidate, not several — which is what keeps the disambiguation prompt
    from firing on OCR artefacts. find_text_on_screen() wraps this for callers
    that only want the best hit."""
    left, top, width, height = _active_window_bounds()
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    offset_x, offset_y = left, top

    data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)

    target_lower = target_text.lower().strip()
    raw = []

    for i in range(len(data['text'])):
        word = data['text'][i].strip().lower()
        if not word:
            continue

        # Match quality: an exact word beats a prefix/superstring, which beats a
        # fragment of a multi-word target. (Old behaviour treated all three as
        # equal, unranked hits — this is the ranking it was missing.)
        exact = (word == target_lower)
        if exact:
            quality = 1.0
        elif word.startswith(target_lower) or target_lower in word:
            quality = 0.6
        elif word in target_lower:
            quality = 0.4
        else:
            continue

        conf = _conf_val(data['conf'][i])
        if not exact and conf < CONF_FLOOR:
            continue  # low-confidence fuzzy hit -> OCR noise, drop it

        l = offset_x + data['left'][i]
        t = offset_y + data['top'][i]
        w = data['width'][i]
        h = data['height'][i]
        score = quality * max(conf, 0.0) / 100.0
        raw.append(Match(l + w // 2, t + h // 2, (l, t, w, h), score, exact))

    merged = _merge_matches(raw)
    # Exact matches first, then by score — so the best candidate is matches[0]
    # for callers that only want one, and the numbered list reads best-first.
    merged.sort(key=lambda m: (m.exact, m.score), reverse=True)
    return merged


def find_text_on_screen(target_text: str):
    """(x, y) center of the best on-screen match for target_text, or None."""
    matches = find_text_matches(target_text)
    return (matches[0].cx, matches[0].cy) if matches else None


def describe_position(box, win_bounds) -> str:
    """A short human position label for a box's center within the active
    window, over a 3x3 grid: 'top-left', 'center', 'bottom-right', or just
    'top'/'left' for the mid row/column. Used to tell duplicate matches apart
    in the disambiguation prompt."""
    l, t, w, h = box
    cx, cy = l + w / 2.0, t + h / 2.0
    wl, wt, ww, wh = win_bounds
    if ww <= 0 or wh <= 0:
        return "somewhere"

    fx = (cx - wl) / ww
    fy = (cy - wt) / wh
    col = "left" if fx < 1 / 3 else ("right" if fx > 2 / 3 else "center")
    row = "top" if fy < 1 / 3 else ("bottom" if fy > 2 / 3 else "middle")

    if row == "middle" and col == "center":
        return "center"
    if col == "center":
        return row       # "top" / "bottom"
    if row == "middle":
        return col       # "left" / "right"
    return f"{row}-{col}"  # "top-left", "bottom-right", ...


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


def _filter_by_color(matches, color, floor=0.06):
    """Return the subset of Matches whose padded region clearly shows `color`
    (color fraction >= floor). Empty if none do — the caller decides whether to
    fall back to ignoring the color hint.

    This replaces the old 'pick whichever shows it most' tiebreaker: with the
    user-approved 'ask whenever 2+ remain' rule, when several candidates still
    qualify we hand them ALL back so click_text can ask, rather than silently
    guessing the strongest."""
    strong = []
    for m in matches:
        try:
            region = _padded_region_shot(*m.box)
            frac = color_vision.color_fraction(region, color)
        except Exception:
            frac = 0.0
        if frac >= floor:
            strong.append(m)
    return strong


def click_text(target_text: str):
    """Locate target_text in the active window and click it. Returns a result
    string, OR — when 2+ equally-plausible matches remain after scoring and
    consolidation — an AmbiguousClick for process_command to resolve with the
    user (never a silent guess between rival matches)."""
    # A color word ("red submit") is a disambiguator, not part of the text to
    # find — pull it out and use it to choose among look-alike matches.
    color, text = color_vision.extract_color(target_text)
    locate = text if (color and text) else target_text

    matches = find_text_matches(locate)
    if matches:
        candidates = matches
        if color and len(matches) > 1:
            # Color is an explicit disambiguator: narrow to matches that
            # actually show it. Exactly one -> click it; several -> ask among
            # those; none -> the color didn't help, fall back to all matches.
            narrowed = _filter_by_color(matches, color)
            if len(narrowed) == 1:
                m = narrowed[0]
                return _do_click((m.cx, m.cy), f"the {color} '{locate}'")
            candidates = narrowed if narrowed else matches

        if len(candidates) == 1:
            m = candidates[0]
            label = f"the {color} '{locate}'" if color else f"'{locate}'"
            return _do_click((m.cx, m.cy), label)

        # 2+ genuinely-distinct candidates remain: don't gamble on matches[0]
        # (the old coin flip) — surface the ambiguity so the user picks.
        return _build_ambiguous(locate, candidates)

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


def _build_ambiguous(locate, candidates):
    """Package 2+ rival matches into an AmbiguousClick for the disambiguation
    gate. Each candidate gets a position label; a color name is added only when
    the candidates differ in color, so three 'submit' buttons in different spots
    read by position while a red/green pair reads by color — and a same-color
    set doesn't clutter the prompt with a redundant color on every line."""
    win = _active_window_bounds()

    colors = []
    for m in candidates:
        try:
            region = _padded_region_shot(*m.box)
            colors.append(color_vision.dominant_chromatic_color(region))
        except Exception:
            colors.append(None)
    show_color = len({c for c in colors if c}) > 1

    cand_dicts = [
        {
            "x": m.cx,
            "y": m.cy,
            "desc": describe_position(m.box, win),
            "color": (col if show_color else None),
        }
        for m, col in zip(candidates, colors)
    ]
    return disambiguation.AmbiguousClick(text=locate, candidates=cand_dicts)


def _do_click(pos, label) -> str:
    pyautogui.click(pos[0], pos[1])
    return f"clicked {label} at {pos}"


def click_at(x, y, label="that") -> str:
    """Click an absolute screen coordinate the user chose during
    disambiguation. Separate from _do_click so the disambiguation handler can
    construct a click straight from a candidate's stored (x, y)."""
    pyautogui.click(x, y)
    return f"clicked {label} at ({x}, {y})"


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
        box = matches[0].box
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