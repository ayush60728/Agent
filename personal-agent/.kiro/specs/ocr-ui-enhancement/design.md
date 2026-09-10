# Design Document

## Overview

This document describes the technical design for four independent enhancements to the OCR and UI interaction pipeline in `desktop_actions.py`, `ui_automation.py`, and `actions.py`.

**Requirement 1 — Image Preprocessing Pipeline.** Before passing a screenshot to Tesseract, `find_text_matches()` now runs a configurable three-stage pipeline: grayscale conversion (PIL `'L'` mode), contrast enhancement (`ImageEnhance.Contrast`), and small-region upscaling (2× resize when either dimension is below 100 px). Every stage is independently togglable via module-level constants and the entire pipeline is wrapped in a silent try/except, so a preprocessing failure never blocks OCR.

**Requirement 2 — easyocr Fallback Engine.** When Tesseract returns zero matches, `find_text_matches()` attempts a second OCR pass using the optional `easyocr` library. The library is imported lazily only on a Tesseract miss, so the hot path (Tesseract succeeds) incurs zero overhead. The same preprocessed PIL image is reused; no second screenshot is taken. easyocr results are normalised into the existing `Match` namedtuple using the same `CONF_FLOOR` and quality scoring as Tesseract. Any import or runtime failure is silently swallowed and execution falls through to the UIA accessibility tree.

**Requirement 3 — Expanded UIA Interactions.** Three new exported functions — `uia_type_into`, `uia_get_value`, and `uia_select_item` — are added to `ui_automation.py`. They reuse the existing `_candidates()`, `_score()`, and BFS-walk machinery from `find_control_center` to locate a control, then interact with it through the UIA `ValuePattern` and `SelectionItemPattern`/`InvokePattern` COM interfaces. All COM property accesses are individually guarded in try/except blocks consistent with the existing code. Three thin wrapper functions and three dispatcher cases are added to `actions.py`; these action types are internal-only and not exposed in `ALLOWED_ACTIONS`.

**Requirement 4 — OCR Result Cache.** A single-entry, in-memory cache stored as module-level state in `desktop_actions.py` avoids re-running Tesseract when the active window has not changed. The cache key is a perceptual average-hash of the preprocessed screenshot, implemented in pure Pillow. A cache hit requires: the same window handle (`hwnd`), a Hamming distance ≤ `CACHE_HASH_THRESHOLD` (default 4) between the new and stored hash, and an age ≤ `CACHE_TTL_S` (default 2.0 s). Any of these failing triggers a full OCR run and cache update.

---

## Architecture

### Updated `find_text_matches()` Call Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         find_text_matches(target_text)                       │
│                                                                               │
│  1. _active_window_bounds()  →  left, top, width, height, hwnd               │
│                                                                               │
│  2. CACHE CHECK ──────────────────────────────────────────────────────────── │
│     ├─ hwnd matches AND TTL not expired AND hamming(new_hash, cached) ≤ thr  │
│     │   └─ return cached data dict  ──────────────────────────────────────┐  │
│     └─ cache miss: continue to step 3                                      │  │
│                                                                            │  │
│  3. pyautogui.screenshot()  →  raw PIL image                               │  │
│                                                                            │  │
│  4. _preprocess(img)  →  preprocessed PIL image                            │  │
│     ├─ grayscale  (PREPROCESS_GRAYSCALE)                                   │  │
│     ├─ contrast   (PREPROCESS_CONTRAST)                                    │  │
│     └─ upscale    (PREPROCESS_UPSCALE + PREPROCESS_MIN_DIM)                │  │
│                                                                            │  │
│  5. pytesseract.image_to_data(preprocessed)  →  data dict                 │  │
│                                                                            │  │
│  6. _ocr_cache update: store data, _phash(preprocessed), time, hwnd       │  │
│           ◄──────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  7. Match / score / merge logic on data dict  →  merged []                   │
│                                                                               │
│  8. merged empty?                                                             │
│     ├─ YES → _easyocr_matches(preprocessed, target, offset_x, offset_y)     │
│     │         ├─ import easyocr (lazy) — ImportError? → []                   │
│     │         ├─ reader.readtext(img)  →  raw detections                     │
│     │         ├─ convert to Match namedtuples                                │
│     │         └─ return matches (may still be [])                            │
│     └─ NO  → return merged                                                   │
│                                                                               │
│  9. easyocr matches empty? → caller (click_text) falls through to UIA        │
│     (UIA fallback is in click_text, unchanged)                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### `click_text()` Fallback Chain (unchanged structure, new middle tier shown)

```
click_text(target_text)
    │
    ├─► find_text_matches()  ──► Tesseract  →  matches? ──► return matches
    │                                │ miss
    │                                └─► _easyocr_matches()  →  matches? ──► return matches
    │                                          │ miss
    │                       (returns [])       │
    ├─► UIA: find_control_center()  ──────────┘  → pos? ──► _do_click(pos)
    │
    └─► "couldn't find '…' on screen"
```

### New UIA Functions in `ui_automation.py`

```
ui_automation.py exports
    find_control_center(query)      ← unchanged
    uia_type_into(query, text)      ← new
    uia_get_value(query)            ← new
    uia_select_item(query)          ← new

All four share:
    _active_root()  →  UIA root control for active window
    _candidates(query)  →  ordered name substrings
    _score(name, ctype, area, win_area, candidates)  →  int
    BFS walk (_MAX_NODES, _MAX_DEPTH, _WALK_BUDGET_S)
```

---

## Components and Interfaces

### desktop_actions.py changes

#### Preprocessing

New module-level configuration constants:

```python
PREPROCESS_GRAYSCALE         = True
PREPROCESS_CONTRAST          = True
PREPROCESS_UPSCALE           = True
PREPROCESS_MIN_DIM           = 100   # upscale if either dimension < this
PREPROCESS_SCALE             = 2     # upscale factor
PREPROCESS_CONTRAST_FACTOR   = 1.5  # ImageEnhance.Contrast multiplier
```

New helper function:

```python
def _preprocess(img: Image.Image) -> Image.Image:
    """Apply the configurable preprocessing pipeline to a PIL screenshot
    before it is passed to Tesseract.  Each stage is individually enabled
    by its module-level flag.  Any failure returns the image unchanged so
    OCR is never blocked by a preprocessing error."""
    import warnings
    try:
        from PIL import ImageEnhance

        if PREPROCESS_GRAYSCALE:
            img = img.convert('L')

        if PREPROCESS_CONTRAST:
            img = ImageEnhance.Contrast(img).enhance(PREPROCESS_CONTRAST_FACTOR)

        if PREPROCESS_UPSCALE and min(img.width, img.height) < PREPROCESS_MIN_DIM:
            new_w = img.width  * PREPROCESS_SCALE
            new_h = img.height * PREPROCESS_SCALE
            img = img.resize((new_w, new_h), Image.LANCZOS)

        return img
    except Exception as exc:
        warnings.warn(f"_preprocess failed ({exc}); using raw image")
        return img
```

#### OCR Cache

New module-level constants and state:

```python
CACHE_TTL_S          = 2.0   # seconds before a cache entry is stale
CACHE_HASH_THRESHOLD = 4     # Hamming distance at or below which images match

_ocr_cache = {
    "hash": None,   # int perceptual hash of the last preprocessed screenshot
    "data": None,   # pytesseract output dict from the last OCR run
    "ts":   0.0,    # time.time() when the entry was stored
    "hwnd": None,   # window handle (int) of the window that was OCR'd
}
```

Perceptual hash (pure Pillow average-hash):

```python
def _phash(img: Image.Image, hash_size: int = 8) -> int:
    """Average-hash of img using Pillow only.  Returns an integer bitmask
    whose bits indicate which pixels are above the mean pixel value.
    Visually similar images produce hashes with low Hamming distance.
    Returns 0 on any error (treated as a cache miss by the caller)."""
    import warnings
    try:
        thumb = img.convert('L').resize((hash_size, hash_size), Image.LANCZOS)
        pixels = list(thumb.getdata())
        mean   = sum(pixels) / len(pixels)
        bits   = 0
        for px in pixels:
            bits = (bits << 1) | (1 if px >= mean else 0)
        return bits
    except Exception as exc:
        warnings.warn(f"_phash failed ({exc}); treating as cache miss")
        return 0
```

Hamming distance helper:

```python
def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
```

#### Updated `find_text_matches()`

```python
def find_text_matches(target_text: str):
    """OCR the active window and return scored, consolidated matches for
    target_text as a list of Match tuples in absolute screen coordinates,
    best-first.  Preprocessing, caching, and an easyocr fallback are all
    applied transparently before the existing match/score/merge logic."""
    import time as _time
    import warnings

    left, top, width, height = _active_window_bounds()
    offset_x, offset_y = left, top

    # --- hwnd for cache invalidation ----------------------------------
    hwnd = None
    try:
        import pygetwindow as gw
        active = gw.getActiveWindow()
        hwnd = getattr(active, '_hWnd', None)
    except Exception:
        hwnd = None

    # --- cache check --------------------------------------------------
    now = _time.time()
    cached = _ocr_cache
    if (
        cached["data"] is not None
        and cached["hwnd"] == hwnd
        and hwnd is not None
        and (now - cached["ts"]) <= CACHE_TTL_S
    ):
        # Take a fresh screenshot only for hashing; reuse cached data if
        # the image hasn't changed.
        try:
            probe = pyautogui.screenshot(region=(left, top, width, height))
            new_hash = _phash(_preprocess(probe))
            if _hamming(new_hash, cached["hash"]) <= CACHE_HASH_THRESHOLD:
                data = cached["data"]
                # Skip to match/score/merge using cached data
                return _score_matches(data, target_text, offset_x, offset_y)
        except Exception:
            pass  # fall through to full OCR on any error

    # --- full OCR run -------------------------------------------------
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    preprocessed = _preprocess(screenshot)
    data = pytesseract.image_to_data(preprocessed, output_type=pytesseract.Output.DICT)

    # Update cache
    try:
        _ocr_cache["hash"] = _phash(preprocessed)
        _ocr_cache["data"] = data
        _ocr_cache["ts"]   = _time.time()
        _ocr_cache["hwnd"] = hwnd
    except Exception:
        pass

    merged = _score_matches(data, target_text, offset_x, offset_y)

    # --- easyocr fallback (only on zero Tesseract matches) ------------
    if not merged:
        merged = _easyocr_matches(preprocessed, target_text, offset_x, offset_y)

    return merged
```

> **Note on `_score_matches`:** To avoid duplicating the match/score/merge loop, the existing loop body inside `find_text_matches()` is extracted into a private helper `_score_matches(data, target_text, offset_x, offset_y) -> list[Match]`. This helper contains the exact loop, quality/score computation, and `_merge_matches()` call currently in `find_text_matches()`. No logic changes.

#### easyocr fallback helper

Module-level lazy singleton:

```python
_easyocr_reader = None  # easyocr.Reader | None; initialised on first use
```

```python
def _easyocr_matches(
    img,            # PIL Image (the same preprocessed image Tesseract used)
    target_text: str,
    offset_x: int,
    offset_y: int,
) -> list:
    """Attempt an easyocr pass on `img`.  Returns a list of Match namedtuples
    using the same quality/score logic as the Tesseract path.  Returns [] on
    ImportError (easyocr not installed) or any runtime exception."""
    import warnings

    global _easyocr_reader

    try:
        import easyocr
    except ImportError:
        return []

    try:
        import numpy as np

        if _easyocr_reader is None:
            _easyocr_reader = easyocr.Reader(['en'], verbose=False)

        img_array = np.array(img)
        detections = _easyocr_reader.readtext(img_array)

        target_lower = target_text.lower().strip()
        raw = []

        for (bbox, text, conf) in detections:
            word = (text or "").strip().lower()
            if not word:
                continue

            exact = (word == target_lower)
            if exact:
                quality = 1.0
            elif word.startswith(target_lower) or target_lower in word:
                quality = 0.6
            elif word in target_lower:
                quality = 0.4
            else:
                continue

            # conf is already 0.0–1.0 from easyocr
            conf_pct = conf * 100.0
            if not exact and conf_pct < CONF_FLOOR:
                continue

            # bbox: [[x1,y1],[x2,y1],[x2,y2],[x1,y2]] in image-local coords
            x1 = int(bbox[0][0])
            y1 = int(bbox[0][1])
            x2 = int(bbox[2][0])
            y2 = int(bbox[2][1])

            l = x1 + offset_x
            t = y1 + offset_y
            w = x2 - x1
            h = y2 - y1
            score = quality * max(conf, 0.0)

            raw.append(Match(l + w // 2, t + h // 2, (l, t, w, h), score, exact))

        merged = _merge_matches(raw)
        merged.sort(key=lambda m: (m.exact, m.score), reverse=True)
        return merged

    except Exception as exc:
        warnings.warn(f"easyocr fallback failed ({exc}); skipping to UIA")
        return []
```

---

### ui_automation.py changes

Three new exported functions. Each follows the same BFS walk pattern as `find_control_center`, returning the best-scored control and then interacting with it through UIA patterns.

```python
def uia_type_into(query: str, text: str) -> str:
    """Write `text` into the control in the active window whose accessible
    name best matches `query`, using UIA ValuePattern.  Returns a result
    string on success or a descriptive error string on failure."""
    candidates = _candidates(query)
    if not candidates:
        return f"'{query}' — no candidates to match"

    root = _active_root()
    if root is None:
        return f"UI Automation unavailable"

    control = _find_best_control(root, candidates)
    if control is None:
        return f"'{query}' not found"

    try:
        vp = control.GetValuePattern()
    except Exception:
        vp = None

    if vp is None:
        return f"'{query}' does not support text input"

    try:
        vp.SetValue(text)
        return f"typed into '{query}'"
    except Exception as exc:
        return f"couldn't type into '{query}': {exc}"


def uia_get_value(query: str) -> str:
    """Return the current text value of the control whose accessible name
    best matches `query`.  Falls back to the control's Name property if
    ValuePattern is unavailable or returns an empty string."""
    candidates = _candidates(query)
    if not candidates:
        return f"'{query}' — no candidates to match"

    root = _active_root()
    if root is None:
        return f"UI Automation unavailable"

    control = _find_best_control(root, candidates)
    if control is None:
        return f"'{query}' not found"

    # Try ValuePattern first
    try:
        vp = control.GetValuePattern()
        if vp is not None:
            val = vp.CurrentValue
            if val:
                return val
    except Exception:
        pass

    # Fallback: Name property
    try:
        name = control.Name
        if name:
            return name
    except Exception:
        pass

    return f"'{query}' has no readable value"


def uia_select_item(query: str) -> str:
    """Select the list/combo/tab item whose accessible name best matches
    `query` via SelectionItemPattern, falling back to InvokePattern."""
    candidates = _candidates(query)
    if not candidates:
        return f"'{query}' — no candidates to match"

    root = _active_root()
    if root is None:
        return f"UI Automation unavailable"

    control = _find_best_control(root, candidates)
    if control is None:
        return f"'{query}' not found"

    # Try SelectionItemPattern
    try:
        sip = control.GetSelectionItemPattern()
        if sip is not None:
            sip.Select()
            return f"selected '{query}'"
    except Exception:
        pass

    # Fallback: InvokePattern
    try:
        ip = control.GetInvokePattern()
        if ip is not None:
            ip.Invoke()
            return f"selected '{query}' (via invoke)"
    except Exception as exc:
        pass

    return f"'{query}' does not support selection or invocation"
```

#### Private BFS helper shared by all four UIA functions

To avoid repeating the BFS walk, a private `_find_best_control` function is extracted. `find_control_center` is refactored to call it internally (no behaviour change).

```python
def _find_best_control(root, candidates):
    """BFS the UIA tree rooted at `root` and return the control whose
    accessible name best matches `candidates` (using _score), or None if
    no control exceeds _MIN_SCORE.  Same walk bounds as find_control_center."""
    import time as _time

    win_area = 0
    try:
        wr = root.BoundingRectangle
        if wr and not wr.isempty():
            win_area = max(0, wr.right - wr.left) * max(0, wr.bottom - wr.top)
    except Exception:
        win_area = 0

    queue   = deque([(root, 0)])
    visited = 0
    start   = _time.time()
    best    = None  # (score, area, control)

    while queue and visited < _MAX_NODES:
        if _time.time() - start > _WALK_BUDGET_S:
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
                        if s > 0 and (best is None or s > best[0]
                                      or (s == best[0] and area < best[1])):
                            best = (s, area, node)

        if depth < _MAX_DEPTH:
            try:
                for child in node.GetChildren():
                    queue.append((child, depth + 1))
            except Exception:
                pass

    if best is None or best[0] < _MIN_SCORE:
        return None
    return best[2]
```

---

### actions.py changes

Three new thin wrapper functions:

```python
def uia_type(query: str, text: str) -> str:
    """Write text into a UIA control by accessible name.  Internal only."""
    try:
        from ui_automation import uia_type_into
        return uia_type_into(query, text)
    except Exception as exc:
        return f"uia_type failed: {exc}"


def uia_get_value(query: str) -> str:
    """Read the current value of a UIA control by accessible name.  Internal only."""
    try:
        from ui_automation import uia_get_value as _uia_get_value
        return _uia_get_value(query)
    except Exception as exc:
        return f"uia_get_value failed: {exc}"


def uia_select(query: str) -> str:
    """Select a UIA list/combo/tab item by accessible name.  Internal only."""
    try:
        from ui_automation import uia_select_item
        return uia_select_item(query)
    except Exception as exc:
        return f"uia_select failed: {exc}"
```

Three new dispatcher cases in `execute_action()` (added after the existing `switch_app_picker` block):

```python
    if action_type == "uia_type":
        return uia_type(action.get("target", ""), action.get("text", ""))

    if action_type == "uia_get_value":
        return uia_get_value(action.get("target", ""))

    if action_type == "uia_select":
        return uia_select(action.get("target", ""))
```

These three action types are **not** added to `ALLOWED_ACTIONS` (or any LLM-facing list) — they are internal / direct-call only, consistent with Requirement 3.10.

---

## Data Models

### `_ocr_cache` dict

```python
_ocr_cache = {
    "hash": int | None,   # average-hash bitmask of the last preprocessed screenshot
    "data": dict | None,  # pytesseract.image_to_data() output dict (keys: text, conf, left, top, width, height, ...)
    "ts":   float,        # time.time() timestamp of the last OCR run
    "hwnd": int | None,   # OS window handle of the window that was OCR'd
}
```

Initial state: `{"hash": None, "data": None, "ts": 0.0, "hwnd": None}`.

A cache entry is valid when: `data is not None AND hwnd == current_hwnd AND (now - ts) <= CACHE_TTL_S AND hamming(new_hash, hash) <= CACHE_HASH_THRESHOLD`.

### `_easyocr_reader` singleton

```python
_easyocr_reader: easyocr.Reader | None = None
```

Initialised to `None` at module load. Set to an `easyocr.Reader(['en'], verbose=False)` instance on first successful use inside `_easyocr_matches`. Never reset to `None` after initialisation — one instance is reused for the lifetime of the process.

### `Match` namedtuple (unchanged)

```python
Match = namedtuple("Match", ["cx", "cy", "box", "score", "exact"])
# cx, cy : int   — click point center, absolute screen coordinates (pixels)
# box    : tuple — (left, top, width, height) in absolute screen coordinates
# score  : float — match quality × OCR confidence, range 0.0–1.0
# exact  : bool  — True when the OCR word equalled the target exactly
```

### easyocr detection format

Raw easyocr `readtext()` output is a list of `(bbox, text, confidence)` tuples where:

- `bbox` — `[[x1,y1], [x2,y1], [x2,y2], [x1,y2]]` in image-local pixel coordinates (top-left origin, not screen-absolute)
- `text` — recognised string
- `confidence` — float in `[0.0, 1.0]`

**Conversion to `Match`:**

```
left   = bbox[0][0] + offset_x
top    = bbox[0][1] + offset_y
width  = bbox[2][0] - bbox[0][0]
height = bbox[2][1] - bbox[0][1]
cx     = left + width  // 2
cy     = top  + height // 2
box    = (left, top, width, height)
score  = quality * max(confidence, 0.0)   # quality ∈ {1.0, 0.6, 0.4}
exact  = (word == target_lower)
```

`offset_x` and `offset_y` are the active window's `left` and `top` from `_active_window_bounds()`, the same values used on the Tesseract path.

---

## Error Handling

| Failure mode | Handling |
|---|---|
| Any stage of `_preprocess()` raises | `warnings.warn(...)`, return original `img` unchanged |
| `_phash()` raises | `warnings.warn(...)`, return `0` (treated as cache miss — full OCR runs) |
| Tesseract (`pytesseract.image_to_data`) raises | Not caught here; propagates to caller as before (no change in behaviour) |
| `easyocr` not installed (`ImportError`) | Silent return `[]` from `_easyocr_matches`; no warning |
| `easyocr.Reader()` init raises (e.g. model download failure) | `warnings.warn(...)`, return `[]` |
| `reader.readtext()` raises (CUDA error, unexpected input) | `warnings.warn(...)`, return `[]` |
| `_easyocr_matches` bbox/conversion arithmetic raises | Caught by outer `except Exception`; `warnings.warn`, return `[]` |
| UIA unavailable (`import uiautomation` fails) | `_active_root()` returns `None`; caller returns `"UI Automation unavailable"` |
| `_find_best_control` BFS node property access raises | Per-node `try/except`; node skipped, walk continues |
| `uia_type_into`: control not found | Returns `"'query' not found"` |
| `uia_type_into`: control has no `ValuePattern` | Returns `"'query' does not support text input"` |
| `uia_type_into`: `SetValue` raises | Returns `"couldn't type into 'query': <exc>"` |
| `uia_get_value`: control not found | Returns `"'query' not found"` |
| `uia_get_value`: `ValuePattern` and `Name` both fail/empty | Returns `"'query' has no readable value"` |
| `uia_select_item`: control not found | Returns `"'query' not found"` |
| `uia_select_item`: neither `SelectionItemPattern` nor `InvokePattern` available | Returns `"'query' does not support selection or invocation"` |
| `actions.py` wrapper import fails (uiautomation missing) | Caught in wrapper; returns `"uia_* failed: <exc>"` |

---

## Correctness Properties

### Property 1: Coordinate Preservation

**Validates: Requirements 1.3, 1.4**

For every `Match` returned by `find_text_matches(target)`, the `box` and `(cx, cy)` values are expressed in the original, unscaled absolute screen coordinate space, regardless of whether `_preprocess` upscaled the image.

Formally: let `(offset_x, offset_y)` be the active window origin and `(l_raw, t_raw, w_raw, h_raw)` be the Tesseract output in raw (pre-upscale) image coordinates. Then for every returned Match `m`:

```
m.box == (offset_x + l_raw, offset_y + t_raw, w_raw, h_raw)
m.cx  == offset_x + l_raw + w_raw // 2
m.cy  == offset_y + t_raw + h_raw // 2
```

The upscaled image is consumed by Tesseract internally; the coordinates stored in `data['left']`, `data['top']`, `data['width']`, `data['height']` are returned by Tesseract in the upscaled space. Because `_preprocess` is applied to the image passed to Tesseract but Tesseract's coordinate output is in the image's own pixel space, the caller must **not** divide by `PREPROCESS_SCALE` when constructing `Match` — only the raw screenshot coordinates (pre-upscale) are correct for mouse targeting.

> Implementation note: when upscaling is active, `_score_matches` receives the Tesseract output from the **upscaled** image. To satisfy this property, the design requires that upscaling is applied to a copy of the image used for OCR, while coordinates are sourced from the **original** screenshot dimensions. Concretely: the `offset_x / offset_y` window origin is captured before preprocessing; Tesseract coordinate output from the upscaled image is divided by `PREPROCESS_SCALE` before storing in the `Match`. This scale-back is performed inside `_score_matches` when `PREPROCESS_UPSCALE` is True.

### Property 2: Fallback Chain Monotonicity

**Validates: Requirements 2.1, 2.7**

Let `T(q)` be the set of Tesseract matches for query `q`, `E(q)` the easyocr matches, and `U(q)` the UIA result.

- If `T(q) ≠ ∅`: `find_text_matches` returns `T(q)` and `_easyocr_matches` is never called.
- If `T(q) = ∅` and easyocr is available: `_easyocr_matches` is called; `find_text_matches` returns `E(q)` (which may be `∅`).
- UIA (`find_control_center`) is called in `click_text` only when `find_text_matches` returns `∅` (both OCR engines produced nothing).

Invariant: easyocr is invoked **if and only if** Tesseract returns zero matches. UIA is invoked **if and only if** both OCR engines return zero matches.

### Property 3: Cache Safety

**Validates: Requirements 4.5, 4.6**

A cached `data` dict is returned by `find_text_matches` only when all three of the following hold simultaneously:

1. `_ocr_cache["hwnd"] == current_hwnd` and `current_hwnd is not None`
2. `time.time() - _ocr_cache["ts"] <= CACHE_TTL_S`
3. `_hamming(_phash(new_screenshot), _ocr_cache["hash"]) <= CACHE_HASH_THRESHOLD`

If any condition is false, full OCR runs and the cache is overwritten. In particular:

- When the active window changes (`hwnd` changes), condition 1 fails immediately — no Hamming computation is performed.
- When `CACHE_TTL_S` seconds have elapsed since the last OCR, condition 2 fails — the cache is never served stale regardless of visual similarity.

### Property 4: easyocr Isolation

**Validates: Requirements 2.2, 2.6**

`find_text_matches(target)` always returns `list[Match]` (possibly empty). An easyocr failure in any of these forms never changes that return type or raises to the caller:

- `ImportError` on `import easyocr` → `_easyocr_matches` returns `[]`
- `Exception` during `easyocr.Reader()` initialisation → `_easyocr_matches` returns `[]` after `warnings.warn`
- `Exception` during `reader.readtext()` → `_easyocr_matches` returns `[]` after `warnings.warn`
- `Exception` during bbox conversion → caught by outer `except Exception` in `_easyocr_matches`, returns `[]`

In every path, `find_text_matches` receives a `list` (empty or not) from `_easyocr_matches` and returns it. The return type of `find_text_matches` is `list[Match]` unconditionally.

---

## Testing Strategy

### Area 1: Image Preprocessing (`_preprocess`)

| # | Test description | Expected outcome | Requirement |
|---|---|---|---|
| 1.1 | Call `_preprocess` on a small RGB image with all flags True | Returns a grayscale (`'L'` mode) image | 1.1 |
| 1.2 | Call `_preprocess` with `PREPROCESS_GRAYSCALE=False` | Returned image keeps its original mode | 1.5 |
| 1.3 | Call `_preprocess` with `PREPROCESS_CONTRAST=False` | Pixel values are not altered by contrast pass | 1.5 |
| 1.4 | Pass a 50×80 image with `PREPROCESS_UPSCALE=True`, `PREPROCESS_MIN_DIM=100` | Returned size is 100×160 | 1.3 |
| 1.5 | Pass a 200×200 image with `PREPROCESS_UPSCALE=True` | Image is **not** upscaled (both dims ≥ 100) | 1.3 |
| 1.6 | Pass a 50×50 image with `PREPROCESS_UPSCALE=False` | Image is **not** upscaled | 1.5 |
| 1.7 | Monkeypatch `ImageEnhance.Contrast` to raise; call `_preprocess` | Returns the original image unchanged; `warnings.warn` called | 1.6 |
| 1.8 | Monkeypatch `Image.convert` to raise; call `_preprocess` | Returns the original image unchanged; `warnings.warn` called | 1.6 |
| 1.9 | Call `_preprocess` with all flags True on a 1×1 image | Does not raise; returns a valid PIL Image | 1.6 |
| 1.10 | Verify that `_preprocess` returns a PIL Image in all flag combinations | Return type is always `PIL.Image.Image` | 1.5, 1.6 |

### Area 2: OCR Cache (`_phash`, `_hamming`, cache logic in `find_text_matches`)

| # | Test description | Expected outcome | Requirement |
|---|---|---|---|
| 2.1 | `_phash` on two identical images | Returns equal integers | 4.1 |
| 2.2 | `_phash` on two visually similar images (one pixel changed) | Hamming distance ≤ 4 | 4.2 |
| 2.3 | `_phash` on two completely different images | Hamming distance > 4 | 4.3 |
| 2.4 | `_hamming(x, x)` for any `x` | Returns 0 | 4.2 |
| 2.5 | `_hamming(0, 0xFF)` | Returns 8 (all bits differ) | 4.2 |
| 2.6 | Populate `_ocr_cache` with a valid entry; call `find_text_matches` with matching hwnd, fresh ts, and identical screenshot | Returns cached matches without calling `pytesseract.image_to_data` | 4.2 |
| 2.7 | Populate cache; advance clock by `CACHE_TTL_S + 1`; call `find_text_matches` | Cache miss — `pytesseract.image_to_data` called again | 4.5 |
| 2.8 | Populate cache; change the active window hwnd; call `find_text_matches` | Cache miss — `pytesseract.image_to_data` called | 4.6 |
| 2.9 | Populate cache with hash H1; supply screenshot with hash H2 where `hamming(H1,H2) > CACHE_HASH_THRESHOLD` | Cache miss — full OCR | 4.3 |
| 2.10 | Verify `_ocr_cache` is a single dict (not a list or other collection) | Only one entry exists at all times | 4.4 |
| 2.11 | `_phash` raises (monkeypatched `Image.convert`); call `find_text_matches` | Falls back to full OCR; no exception propagates | 4.1 |
| 2.12 | After a cache miss, verify `_ocr_cache["data"]` is updated to the new `pytesseract` output | Cache always reflects last OCR run | 4.3 |

### Area 3: easyocr Fallback (`_easyocr_matches`)

| # | Test description | Expected outcome | Requirement |
|---|---|---|---|
| 3.1 | `easyocr` not installed; call `_easyocr_matches` | Returns `[]`; no warning, no exception | 2.2 |
| 3.2 | `easyocr` installed, `reader.readtext` raises; call `_easyocr_matches` | Returns `[]`; `warnings.warn` called | 2.6 |
| 3.3 | Tesseract returns 1+ matches; verify `_easyocr_matches` is never called | `_easyocr_matches` call count == 0 | 2.7 |
| 3.4 | Tesseract returns 0 matches; `easyocr` installed and returns one hit | `find_text_matches` returns that hit as a `Match` | 2.1 |
| 3.5 | easyocr detection with `confidence < CONF_FLOOR/100` and non-exact word | Filtered out; `Match` not included | 2.5 |
| 3.6 | easyocr detection with exact word match and low confidence | Included (exact matches bypass `CONF_FLOOR`) | 2.5 |
| 3.7 | easyocr bbox `[[10,20],[110,20],[110,45],[10,45]]` with `offset_x=5, offset_y=8` | `Match.box == (15, 28, 100, 25)` | 2.4 |
| 3.8 | easyocr confidence 0.82, quality 1.0 | `Match.score == 0.82` | 2.4 |
| 3.9 | `easyocr.Reader.__init__` raises; call `_easyocr_matches` | Returns `[]`; `warnings.warn` called; `_easyocr_reader` remains `None` | 2.6 |
| 3.10 | Return type of `find_text_matches` when easyocr raises | Always `list`; never raises | 2.6 |
| 3.11 | easyocr returns multiple detections; verify `_merge_matches` is called on results | Overlapping easyocr boxes are merged | 2.4 |
| 3.12 | Verify `_easyocr_matches` does not call `pyautogui.screenshot` | Screenshot call count is 0 inside `_easyocr_matches` | 2.3 |

### Area 4: UIA Expanded Interactions (`uia_type_into`, `uia_get_value`, `uia_select_item`)

| # | Test description | Expected outcome | Requirement |
|---|---|---|---|
| 4.1 | `uia_type_into` — control found with `ValuePattern`; `SetValue` succeeds | Returns `"typed into 'query'"` | 3.1 |
| 4.2 | `uia_type_into` — control not found (score < `_MIN_SCORE`) | Returns `"'query' not found"` | 3.2 |
| 4.3 | `uia_type_into` — control found, `GetValuePattern()` returns `None` | Returns `"'query' does not support text input"` | 3.2 |
| 4.4 | `uia_type_into` — `SetValue` raises a COM exception | Returns error string containing query name; does not raise | 3.2, 3.9 |
| 4.5 | `uia_get_value` — control with `ValuePattern.CurrentValue == "hello"` | Returns `"hello"` | 3.3 |
| 4.6 | `uia_get_value` — `ValuePattern` unavailable; `control.Name == "Submit"` | Returns `"Submit"` (Name fallback) | 3.4 |
| 4.7 | `uia_get_value` — control not found | Returns `"'query' not found"` | 3.5 |
| 4.8 | `uia_get_value` — `ValuePattern` and `Name` both raise | Returns `"'query' has no readable value"` | 3.5 |
| 4.9 | `uia_select_item` — control supports `SelectionItemPattern`; `Select()` succeeds | Returns `"selected 'query'"` | 3.6 |
| 4.10 | `uia_select_item` — no `SelectionItemPattern`; `InvokePattern.Invoke()` succeeds | Returns `"selected 'query' (via invoke)"` | 3.7 |
| 4.11 | `uia_select_item` — neither pattern available | Returns error string containing query name | 3.8 |
| 4.12 | All three functions with `uiautomation` import failing | Returns descriptive error string; no exception propagates | 3.9 |
| 4.13 | `execute_action({"action": "uia_type", "target": "search", "text": "hi"})` | Calls `uia_type_into("search", "hi")` | 3.10 |
| 4.14 | `execute_action({"action": "uia_get_value", "target": "username"})` | Calls `uia_get_value("username")` | 3.10 |
| 4.15 | `execute_action({"action": "uia_select", "target": "Dark mode"})` | Calls `uia_select_item("Dark mode")` | 3.10 |
| 4.16 | Verify `"uia_type"`, `"uia_get_value"`, `"uia_select"` are absent from `ALLOWED_ACTIONS` | These action types are internal-only | 3.10 |
