"""
ui_automation.py

Click controls by their *accessible name* via the Windows UI Automation
(UIA) tree — the same accessibility layer screen readers use.

Why this exists: click_text() locates things by OCR-ing the screen, so it can
only click things that show visible *text*. Icon-only buttons — the hamburger
menu (three lines), a gear/settings cog, a back arrow, the window's close X —
render as pictures with no text, so OCR is blind to them. But almost every
such button still exposes an accessible *name* ("Customize and control Brave",
"Settings", "Back", "Close") through UIA. This module walks the focused
window's UIA tree, finds the control whose name best matches what the user
asked for (with a small synonym table so shape-words like "hamburger" or
"three lines" map to the real button names), and returns its center point.

It's used as a *fallback* inside desktop_actions.click_text: OCR runs first
(fast, and right for real on-screen text), and only when OCR finds nothing do
we consult the accessibility tree here.

Everything is defensive. UIA is COM under the hood and individual property
reads can raise across process boundaries, so every access is guarded and any
failure just yields "no match" rather than crashing the agent. `uiautomation`
is imported lazily so a machine without it still runs the rest of the agent.
"""

import re
import time
from collections import deque

import pygetwindow as gw


# How the things people SAY about an icon map to the accessible names those
# icons actually expose. Keys are the cleaned, lowercased phrase; values are
# candidate name-substrings to look for, roughly in priority order. Covers the
# common browser / window-chrome icons across Chrome, Brave, Edge and Firefox.
_MENU_NAMES = [
    "customize and control", "settings and more", "application menu",
    "main menu", "more", "menu",
]
_SYNONYMS = {
    # the three-line "main menu" hamburger
    "hamburger": _MENU_NAMES,
    "hamburger menu": _MENU_NAMES,
    "three lines": _MENU_NAMES,
    "three line": _MENU_NAMES,
    "3 lines": _MENU_NAMES,
    "3 line": _MENU_NAMES,
    "menu": ["menu"] + _MENU_NAMES,
    "main menu": ["main menu"] + _MENU_NAMES,
    # the vertical "more options" overflow (three dots / kebab)
    "three dots": ["more", "more options", "options"] + _MENU_NAMES,
    "three dot": ["more", "more options", "options"] + _MENU_NAMES,
    "3 dots": ["more", "more options", "options"] + _MENU_NAMES,
    "kebab": ["more", "more options", "options", "menu"],
    "more": ["more", "more options", "customize and control", "options"],
    # gear / cog
    "gear": ["settings", "options", "preferences"],
    "cog": ["settings", "options", "preferences"],
    "settings": ["settings", "options", "preferences"],
    "options": ["options", "settings", "preferences"],
    # magnifier
    "magnifying glass": ["search", "find"],
    "search": ["search", "find"],
    # navigation
    "back": ["back"],
    "back arrow": ["back"],
    "forward": ["forward"],
    "forward arrow": ["forward"],
    "refresh": ["reload", "refresh"],
    "reload": ["reload", "refresh"],
    "home": ["home"],
    # tabs / add
    "plus": ["new tab", "add", "new", "create"],
    "add": ["add", "new", "create", "new tab"],
    "new tab": ["new tab", "add"],
    # bookmarks
    "star": ["bookmark", "favorite", "favourite", "add to favorites"],
    "bookmark": ["bookmark", "favorite", "favourite", "add to favorites"],
    "favorite": ["favorite", "favourite", "bookmark"],
    "favourite": ["favourite", "favorite", "bookmark"],
    # account / profile
    "profile": ["profile", "account", "you", "sign in"],
    "account": ["account", "profile", "you", "sign in"],
    "avatar": ["profile", "account", "you"],
    # notifications
    "bell": ["notification", "notifications", "alerts"],
    "notification": ["notification", "notifications", "alerts"],
    "notifications": ["notifications", "notification", "alerts"],
    # window chrome
    "close": ["close"],
    "cross": ["close"],
    "cross mark": ["close"],
    "x": ["close"],
    "minimize": ["minimize"],
    "maximize": ["maximize", "restore"],
    "restore": ["restore", "maximize"],
}

# Control types that are genuinely clickable get a scoring bonus, so a real
# button named "Search" beats a random pane that merely contains the word.
_CLICKABLE_TYPES = {
    "ButtonControl", "HyperlinkControl", "MenuItemControl", "TabItemControl",
    "ListItemControl", "CheckBoxControl", "RadioButtonControl",
    "SplitButtonControl", "MenuControl", "TreeItemControl",
}
# Icons are frequently exposed as images / custom controls — clickable enough,
# but a weaker signal than a real Button.
_ICONISH_TYPES = {"ImageControl", "CustomControl", "TextControl", "GroupControl"}

# Bounds on the tree walk so a huge web page can't make a lookup hang. Icon
# buttons live in the window chrome (shallow), so a breadth-first walk reaches
# them well within these caps.
_MAX_NODES = 1500
_MAX_DEPTH = 30
_WALK_BUDGET_S = 1.5

# Below this score we refuse to click — we'd rather report "not found" than
# fire the mouse at a loose partial match.
_MIN_SCORE = 45


def _clean(query: str) -> str:
    """Strip filler people tack onto an icon description down to the core
    word: "the menu icon" -> "menu", "close button" -> "close"."""
    q = (query or "").strip().lower()
    for suffix in (" icon", " button", " symbol", " mark", " sign", " glyph"):
        if q.endswith(suffix):
            q = q[: -len(suffix)].strip()
    for prefix in ("the ", "a ", "an "):
        if q.startswith(prefix):
            q = q[len(prefix):].strip()
    return q


def _candidates(query: str):
    """Ordered, de-duplicated list of accessible-name substrings to look for,
    expanding shape-words ("hamburger", "gear") into the real button names."""
    cleaned = _clean(query)
    cands = [cleaned] if cleaned else []
    if cleaned in _SYNONYMS:
        cands += _SYNONYMS[cleaned]
    else:
        # Allow partial hits: "hamburger menu thing" still finds key "hamburger".
        # Match single-word keys only on a whole-word boundary — otherwise a
        # one-letter key like "x" would fire on the 'x' inside "next"/"exit"
        # and drag in the wrong candidate. Multi-word keys match as a phrase.
        words = set(re.findall(r"\w+", cleaned))
        for key, vals in _SYNONYMS.items():
            if not key:
                continue
            hit = (key in cleaned) if " " in key else (key in words)
            if hit:
                cands += vals
    seen = set()
    out = []
    for c in cands:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _score(name_lower, ctype, area, win_area, candidates):
    """Rank one control's accessible name against the candidate substrings.
    Higher is better; 0 means no match at all."""
    best = 0
    padded = f" {name_lower} "
    for cand in candidates:
        if name_lower == cand:
            s = 100                       # exact name
        elif name_lower.startswith(cand):
            s = 82                        # "Close" for cand "close"
        elif f" {cand} " in padded:
            s = 68                        # whole word inside a longer name
        elif cand in name_lower:
            s = 45                        # loose substring
        else:
            s = 0
        if s > best:
            best = s
    if best == 0:
        return 0

    if ctype in _CLICKABLE_TYPES:
        best += 15
    elif ctype in _ICONISH_TYPES:
        best += 3

    # A control that fills most of the window is a container that happens to
    # contain the word, not the icon itself — push it down.
    if win_area and area > 0:
        frac = area / win_area
        if frac > 0.6:
            best -= 40
        elif frac > 0.3:
            best -= 15
    return best


def _active_root():
    """A UIA control for the currently active window, or None. Mirrors
    find_text_on_screen's use of the active window, so OCR and the
    accessibility fallback both target the same thing."""
    try:
        import uiautomation as auto  # lazy: COM init cost + optional dependency
    except Exception:
        return None

    try:
        # Default per-lookup timeout is 10s; a miss would otherwise hang the
        # agent for that long. We do our own bounded walk, so keep this tight.
        auto.SetGlobalSearchTimeout(1.0)
    except Exception:
        pass

    hwnd = None
    try:
        active = gw.getActiveWindow()
        hwnd = getattr(active, "_hWnd", None)
    except Exception:
        hwnd = None

    try:
        if hwnd:
            return auto.ControlFromHandle(hwnd)
        return auto.GetForegroundControl()
    except Exception:
        return None


def find_control_center(query: str):
    """Return (x, y) absolute screen coordinates of the center of the control
    in the active window whose accessible name best matches `query`, or None
    if nothing matches well enough.

    Safe to call unconditionally: if UIA is unavailable or errors anywhere,
    this returns None instead of raising."""
    candidates = _candidates(query)
    if not candidates:
        return None

    root = _active_root()
    if root is None:
        return None

    # Window area, used to penalize giant container controls (see _score).
    win_area = 0
    try:
        wr = root.BoundingRectangle
        if wr and not wr.isempty():
            win_area = max(0, wr.right - wr.left) * max(0, wr.bottom - wr.top)
    except Exception:
        win_area = 0

    queue = deque([(root, 0)])
    visited = 0
    start = time.time()
    best = None  # (score, area, (cx, cy))

    while queue and visited < _MAX_NODES:
        if time.time() - start > _WALK_BUDGET_S:
            break
        node, depth = queue.popleft()
        visited += 1

        try:
            name = node.Name or ""
        except Exception:
            name = ""
        try:
            ctype = node.ControlTypeName
        except Exception:
            ctype = ""

        if name:
            try:
                offscreen = node.IsOffscreen
            except Exception:
                offscreen = False
            if not offscreen:
                try:
                    r = node.BoundingRectangle
                except Exception:
                    r = None
                if r is not None and not r.isempty():
                    area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                    if area > 0:
                        s = _score(name.lower(), ctype, area, win_area, candidates)
                        # Higher score wins; on a tie prefer the smaller
                        # control (the icon itself, not the pane holding it).
                        if s > 0 and (best is None or s > best[0]
                                      or (s == best[0] and area < best[1])):
                            best = (s, area, (r.xcenter(), r.ycenter()))

        if depth < _MAX_DEPTH:
            try:
                for child in node.GetChildren():
                    queue.append((child, depth + 1))
            except Exception:
                pass

    if best is None or best[0] < _MIN_SCORE:
        return None
    return best[2]


if __name__ == "__main__":
    # Manual test: python ui_automation.py "close"  (acts on the active window)
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    q = " ".join(sys.argv[1:]).strip() or "close"
    print(f"query      : {q!r}")
    print(f"candidates : {_candidates(q)}")
    print(f"match      : {find_control_center(q)}")
