# Requirements Document

## Introduction

The personal-agent is a Windows desktop AI assistant that locates and interacts with UI elements by
OCR-ing the active window (pytesseract + PIL screenshots captured via pyautogui). When OCR finds no
match it falls back to the Windows UI Automation (UIA) accessibility tree (`ui_automation.py`,
`uiautomation` package). Click accuracy depends on OCR quality, and the pipeline has no second
chance if Tesseract misses a target.

This spec improves the pipeline in four independent areas:

1. **Image Preprocessing** — sharpen OCR input with grayscale conversion, contrast enhancement, and
   small-region upscaling before handing the image to Tesseract.
2. **OCR Fallback (easyocr)** — insert a second OCR engine between Tesseract and UIA so a Tesseract
   miss gets a second attempt before the accessibility tree is consulted.
3. **Expanded UIA Interactions** — add `uia_type_into`, `uia_get_value`, and `uia_select_item` to
   `ui_automation.py` so the agent can drive controls entirely through the accessibility layer
   without keyboard simulation.
4. **OCR Result Cache** — avoid re-running Tesseract on an unchanged window region by caching the
   most recent result keyed by a perceptual hash of the screenshot.

All four areas must integrate cleanly with the existing `Match` namedtuple, `CONF_FLOOR` constant,
`find_text_matches()` / `click_text()` call chain, and `actions.py` action dispatcher, and must
never block or crash the agent when optional dependencies are absent or when a subsystem raises.

---

## Requirements

### Requirement 1: Image Preprocessing Pipeline

**User Story:** As the agent's OCR subsystem, I need screenshots to be cleaned up before Tesseract
reads them so that low-contrast text, small buttons, and high-DPI regions that Tesseract currently
misses are recognised reliably — without ever blocking a click because preprocessing itself failed.

---

#### Acceptance Criteria

**1.1 — Grayscale Conversion**

WHEN `find_text_matches()` captures a screenshot region,
THE SYSTEM SHALL convert the PIL image to grayscale (`L` mode) as the first step of the
preprocessing pipeline before passing it to `pytesseract.image_to_data`.

---

#### Acceptance Criteria

**1.2 — Adaptive Contrast Enhancement**

WHEN the grayscale image is available,
THE SYSTEM SHALL apply a contrast-enhancement pass using Pillow's `ImageEnhance.Contrast` or
`ImageFilter`-based sharpening (equivalent to CLAHE-style local contrast) so that text with
low contrast against its background is made more legible before OCR.

---

#### Acceptance Criteria

**1.3 — Small-Region Upscaling**

WHEN either dimension of the screenshot region is fewer than 100 pixels,
THE SYSTEM SHALL upscale the image by 2× (nearest-neighbour or bilinear) before OCR.
The upscaled image is used for OCR only; the original pixel coordinates stored in the
`Match` namedtuple's `box` field (used for mouse targeting) are never altered.

---

#### Acceptance Criteria

**1.4 — Coordinate Preservation After Preprocessing**

WHEN preprocessing scales or otherwise transforms the image,
THE SYSTEM SHALL continue to record all `Match` coordinates (`cx`, `cy`, `box`) in the
original, unscaled screen coordinate space so that `click_text` and `_do_click` targets
the correct physical pixel.

---

#### Acceptance Criteria

**1.5 — Per-Stage Configuration**

WHEN the agent is deployed or configured,
THE SYSTEM SHALL allow each pipeline stage (grayscale, contrast, upscale) to be enabled
or disabled independently via a configuration dictionary or module-level constants without
requiring code changes, so operators can tune the pipeline for their display environment.

---

#### Acceptance Criteria

**1.6 — Silent Fallback on Preprocessing Failure**

WHEN any stage of the preprocessing pipeline raises an exception for any reason
(including missing Pillow capabilities or unexpected image modes),
THE SYSTEM SHALL catch the exception, log a warning, and continue with the raw, unprocessed
screenshot so that OCR is never blocked by a preprocessing error.

---

### Requirement 2: easyocr Fallback Engine

**User Story:** As the agent's click-routing pipeline, I need a second OCR attempt using easyocr
when Tesseract returns zero matches, so that controls with fonts or layouts that Tesseract struggles
with can still be located without immediately escalating to the accessibility tree.

---

#### Acceptance Criteria

**2.1 — Tesseract-First Ordering**

WHEN `find_text_matches()` is called,
THE SYSTEM SHALL run Tesseract first; only if Tesseract returns zero matches SHALL it
attempt the easyocr fallback. The fallback chain order is fixed:
Tesseract → easyocr (if installed) → UIA → "not found".

---

#### Acceptance Criteria

**2.2 — Optional Dependency, Lazy Import**

WHEN the easyocr fallback would be attempted,
THE SYSTEM SHALL import easyocr lazily (inside the fallback branch, not at module load time);
if `import easyocr` raises `ImportError` or any other exception, THE SYSTEM SHALL skip the
easyocr pass silently and proceed directly to UIA, with no error surfaced to the caller.

---

#### Acceptance Criteria

**2.3 — Shared Preprocessed Screenshot**

WHEN the easyocr pass runs,
THE SYSTEM SHALL reuse the same preprocessed image that was passed to Tesseract in the same
call — no second `pyautogui.screenshot()` capture is taken for the easyocr pass.

---

#### Acceptance Criteria

**2.4 — Match Format Compatibility**

WHEN easyocr returns results,
THE SYSTEM SHALL convert each easyocr detection into a `Match` namedtuple
(`cx`, `cy`, `box`, `score`, `exact`) with:
- `box` expressed as `(left, top, width, height)` in absolute screen coordinates,
  using the same `offset_x` / `offset_y` from the active window bounds;
- `score` normalised to the 0.0–1.0 range (easyocr's confidence is already 0–1,
  so it must be multiplied by the same quality factor used for Tesseract);
- `exact` set to `True` when the lowercased recognised word equals the lowercased target,
  `False` otherwise.

---

#### Acceptance Criteria

**2.5 — Consistent Confidence Floor**

WHEN easyocr results are scored,
THE SYSTEM SHALL apply the same `CONF_FLOOR` threshold and match-quality logic
(exact / prefix–superstring / fragment) that `find_text_matches()` already applies to
Tesseract results, so the two engines are held to the same noise-rejection standard.

---

#### Acceptance Criteria

**2.6 — Exception Isolation**

WHEN easyocr is installed but raises an exception during initialisation or during the
recognition call (e.g. model download failure, CUDA error, unexpected input format),
THE SYSTEM SHALL catch the exception, emit a `warnings.warn` message, and proceed
to the UIA fallback — it must never crash the agent or propagate the exception to
`click_text` or `find_text_matches`.

---

#### Acceptance Criteria

**2.7 — No Impact on Tesseract-Success Path**

WHEN Tesseract returns one or more matches,
THE SYSTEM SHALL NOT invoke easyocr at all, so the hot path (Tesseract succeeds, as it
usually does) incurs zero overhead from the fallback machinery.

---

### Requirement 3: Expanded UIA Interactions

**User Story:** As the agent, I need to be able to type into controls, read their current values,
and select list or combo-box items through the Windows accessibility layer, so I can drive forms
and pickers that resist keyboard simulation (e.g. rich-text fields, virtualized lists).

---

#### Acceptance Criteria

**3.1 — uia_type_into: Value Pattern Write**

WHEN `uia_type_into(query, text)` is called,
THE SYSTEM SHALL locate the control in the active window whose accessible name best matches
`query` (using the same `_candidates` / `_score` logic as `find_control_center`), then write
`text` into it using UIA's `ValuePattern` (`IValueProvider.SetValue`), and return a result
string such as `"typed into '<query>'"` on success.

---

#### Acceptance Criteria

**3.2 — uia_type_into: Graceful Failure**

WHEN `uia_type_into` cannot complete because the control is not found, is off-screen,
is not focusable, or does not support `ValuePattern`,
THE SYSTEM SHALL return a descriptive error string (e.g. `"'query' not found"`,
`"'query' does not support text input"`) rather than raising an exception.

---

#### Acceptance Criteria

**3.3 — uia_get_value: Value Pattern Read**

WHEN `uia_get_value(query)` is called,
THE SYSTEM SHALL locate the matching control, attempt to read its current text via
`ValuePattern.CurrentValue`, and return that string on success.

---

#### Acceptance Criteria

**3.4 — uia_get_value: Name Property Fallback**

WHEN `uia_get_value` is called and the best-matching control does not support
`ValuePattern` or `CurrentValue` returns an empty string,
THE SYSTEM SHALL return the control's `Name` property as a fallback string, giving the
caller useful context rather than a silent empty result.

---

#### Acceptance Criteria

**3.5 — uia_get_value: Graceful Failure**

WHEN `uia_get_value` cannot find the control or all value reads raise exceptions,
THE SYSTEM SHALL return a descriptive error string rather than raising.

---

#### Acceptance Criteria

**3.6 — uia_select_item: SelectionItem Pattern**

WHEN `uia_select_item(query)` is called,
THE SYSTEM SHALL locate the control, invoke `SelectionItemPattern.Select()` on it,
and return a result string such as `"selected '<query>'"` on success.

---

#### Acceptance Criteria

**3.7 — uia_select_item: InvokePattern Fallback**

WHEN `uia_select_item` is called and the best-matching control does not support
`SelectionItemPattern`,
THE SYSTEM SHALL fall back to `InvokePattern.Invoke()` and return an appropriate result
string, so that buttons and hyperlinks posing as selectable items can still be activated.

---

#### Acceptance Criteria

**3.8 — uia_select_item: Graceful Failure**

WHEN `uia_select_item` cannot find the control or neither `SelectionItemPattern` nor
`InvokePattern` is available,
THE SYSTEM SHALL return a descriptive error string rather than raising.

---

#### Acceptance Criteria

**3.9 — Defensive COM Pattern**

WHEN any of the three new UIA functions accesses a UIA property or pattern across
a process boundary,
THE SYSTEM SHALL wrap every such access in a `try/except` block, consistent with the
existing `find_control_center` pattern, so that a COM error in a third-party process
never propagates out of `ui_automation.py`.

---

#### Acceptance Criteria

**3.10 — actions.py Wiring (Internal Action Types)**

WHEN `execute_action` in `actions.py` receives an action dict with `"action"` equal to
`"uia_type"`, `"uia_get_value"`, or `"uia_select"`,
THE SYSTEM SHALL dispatch to the corresponding `ui_automation` function with the `"target"`
and (for `uia_type`) `"text"` fields from the dict, and return the result string.
These action types SHALL NOT appear in `ALLOWED_ACTIONS` (or any equivalent list exposed
to the LLM) — they are internal / direct-call only.

---

### Requirement 4: OCR Result Cache

**User Story:** As the agent's performance subsystem, I need repeated OCR calls on an unchanged
window to be served from an in-memory cache instead of re-running Tesseract, so that rapid
multi-step command sequences (e.g. "click X, then click Y") don't re-scan a screen that hasn't
moved.

---

#### Acceptance Criteria

**4.1 — Perceptual Hash Key**

WHEN `find_text_matches()` captures a screenshot region,
THE SYSTEM SHALL compute a perceptual hash of the image using Pillow only (no additional
imaging library), and use that hash as the cache lookup key.

---

#### Acceptance Criteria

**4.2 — Cache Hit: Hamming Distance Threshold**

WHEN a cached entry exists for the current active window and the Hamming distance between
the new screenshot's perceptual hash and the cached hash is at or below a configurable
threshold (default 4 bits),
THE SYSTEM SHALL return the cached `Match` list without re-running Tesseract,
provided the TTL has not expired (see **4.5**).

---

#### Acceptance Criteria

**4.3 — Cache Miss: Full OCR and Update**

WHEN no cached entry exists, the active window has changed, or the Hamming distance
exceeds the threshold,
THE SYSTEM SHALL run the full Tesseract OCR pipeline, store the resulting data dict and
its perceptual hash in the cache, and return the fresh matches.

---

#### Acceptance Criteria

**4.4 — Single-Entry Cache**

THE SYSTEM SHALL maintain at most one cache entry at a time (the most recently seen
window region). No LRU eviction, no multi-entry management, and no disk persistence are
required or permitted.

---

#### Acceptance Criteria

**4.5 — TTL-Based Invalidation**

WHEN the cached entry was computed more than `CACHE_TTL_S` seconds ago (default 2.0 seconds),
THE SYSTEM SHALL treat it as a cache miss and re-run OCR, even if the perceptual hash still
matches, so that animated or auto-updating UI regions are never served stale results.

---

#### Acceptance Criteria

**4.6 — Window-Change Invalidation**

WHEN the active window's `hwnd` (window handle) differs from the handle recorded in the
cache entry,
THE SYSTEM SHALL immediately invalidate the cache entry and run full OCR, so that switching
apps never returns matches from the previous window.

---

#### Acceptance Criteria

**4.7 — In-Memory Only**

THE SYSTEM SHALL store the cache exclusively in process memory.
The cache SHALL NOT be written to disk, logged, or persisted across agent restarts.

---

#### Acceptance Criteria

**4.8 — Configurable Hamming Threshold and TTL**

WHEN the agent is deployed or configured,
THE SYSTEM SHALL allow `CACHE_HASH_THRESHOLD` (Hamming distance, default 4) and
`CACHE_TTL_S` (seconds, default 2.0) to be changed via module-level constants without
requiring code changes, so operators can tune cache aggressiveness for their environment.

---

## Glossary

| Term | Definition |
|---|---|
| **Active window** | The foreground window as returned by `pygetwindow.getActiveWindow()`; the region OCR and UIA operations are scoped to. |
| **AmbiguousClick** | A namedtuple returned by `click_text` when 2+ distinct OCR candidates remain after scoring, triggering the disambiguation prompt rather than a silent guess. |
| **CACHE_TTL_S** | Module-level constant (default 2.0 s) controlling the maximum age of a cache entry before it is treated as stale and discarded regardless of hash match. |
| **CONF_FLOOR** | Module-level constant (default 40) in `desktop_actions.py`; an OCR confidence value below which a fuzzy (non-exact) match is discarded as noise. |
| **easyocr** | An optional Python OCR library (`pip install easyocr`) that uses deep-learning models and may recognise text that Tesseract misses. Used only as a fallback. |
| **Hamming distance** | The number of bit positions at which two binary perceptual hashes differ; used to decide whether two screenshots are "the same" for caching purposes. |
| **hwnd** | Windows window handle — an integer identifying a specific OS window; used by the cache to detect app switches. |
| **InvokePattern** | A UIA control pattern (`IInvokeProvider`) that triggers the default action of a control (equivalent to clicking it), used as a fallback when `SelectionItemPattern` is unavailable. |
| **Match** | A `collections.namedtuple("Match", ["cx", "cy", "box", "score", "exact"])` representing one OCR hit: center coordinates, bounding box in `(left, top, width, height)` screen coords, normalised quality score, and an exact-word flag. |
| **Perceptual hash (pHash)** | A compact fingerprint of an image computed by DCT (or similar) over a down-scaled greyscale thumbnail, such that visually similar images produce hashes with low Hamming distance. Implemented here using Pillow only. |
| **Preprocessing pipeline** | The sequence of image transforms (grayscale → contrast enhancement → upscale) applied to a PIL screenshot before it is passed to `pytesseract.image_to_data`. |
| **SelectionItemPattern** | A UIA control pattern (`ISelectionItemProvider`) that selects an item in a list, combo box, or tab control. |
| **Tesseract** | The open-source OCR engine invoked via the `pytesseract` Python wrapper; the primary text-recognition path in `find_text_matches()`. |
| **UIA (UI Automation)** | The Windows accessibility framework (COM-based) that exposes control names, states, and interaction patterns independently of on-screen rendering. Used as a fallback when OCR finds nothing. |
| **uia_get_value** | New function in `ui_automation.py`; reads a control's current text value via `ValuePattern.CurrentValue` or the `Name` property. |
| **uia_select_item** | New function in `ui_automation.py`; selects a list/combo/tab item via `SelectionItemPattern`, falling back to `InvokePattern`. |
| **uia_type_into** | New function in `ui_automation.py`; writes text into a control via `ValuePattern.SetValue` without keyboard simulation. |
| **ValuePattern** | A UIA control pattern (`IValueProvider`) that allows reading and writing a control's text value programmatically, without simulating keyboard input. |
