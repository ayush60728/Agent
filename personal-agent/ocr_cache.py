"""
ocr_cache.py

In-memory TTL cache for Tesseract OCR results.

Key insight: Tesseract on a complex window takes 0.5–3+ seconds. If the user
says "click submit" and the screen hasn't changed, we can return the cached
match list instantly instead of re-running the full OCR pass.

Cache key: (perceptual_hash_of_screenshot, target_text_lower)
    - The hash is computed from a tiny 32×32 downscale of the screenshot, so
      any visible screen change (window redraw, popup, text update) invalidates
      the entry automatically. hashlib.md5 on raw pixel bytes is fast (~0.5ms).
    - TTL of 3 s is a hard upper bound: even if the hash matched, a result older
      than 3 s is discarded so a slow-redrawn screen doesn't serve stale coords.

Invalidation is also explicit: any action that definitely changes the screen
(click, key press, scroll, type) calls invalidate_all(), which wipes all entries
so the very next OCR call is always fresh.

No external dependencies — PIL/Pillow is already required by desktop_actions.py.

Usage (from desktop_actions.py):
    import ocr_cache

    screenshot = pyautogui.screenshot(region=...)
    h = ocr_cache.screen_hash(screenshot)

    cached = ocr_cache.CACHE.get(h, target_lower)
    if cached is not None:
        return cached          # ← free cache hit

    matches = <run tesseract>
    ocr_cache.CACHE.put(h, target_lower, matches)
    return matches

    # After any click/key/scroll that mutates the screen:
    ocr_cache.CACHE.invalidate_all()
"""

import hashlib
import time
import threading
from collections import OrderedDict
from typing import Optional


# ── Tunable constants ────────────────────────────────────────────────────────

# Time-to-live per entry in seconds. After this, the screen may have changed.
TTL = 3.0

# Maximum number of (hash, target) pairs kept at once. Old entries are evicted
# LRU-style when the cap is hit to prevent unbounded memory growth.
MAX_ENTRIES = 50

# Downscale dimension for perceptual hash (width × height = 32×32 = 1024 px).
HASH_THUMB_SIZE = (32, 32)


# ── Hash helper ──────────────────────────────────────────────────────────────

def screen_hash(screenshot) -> str:
    """Return a short hex digest of a PIL Image's visual content.
    Downscales to HASH_THUMB_SIZE first so hashing is O(1024) not O(megapixels)."""
    try:
        thumb = screenshot.convert("RGB").resize(HASH_THUMB_SIZE)
        return hashlib.md5(thumb.tobytes()).hexdigest()
    except Exception:
        # If something goes wrong, return a unique hash so no stale hit fires.
        return str(time.monotonic())


# ── Cache ────────────────────────────────────────────────────────────────────

class OcrCache:
    """Thread-safe TTL + LRU cache for OCR match lists."""

    def __init__(self):
        self._lock = threading.Lock()
        # key → (matches, insert_time)
        self._store: OrderedDict[tuple, tuple] = OrderedDict()

    def get(self, hash_: str, target: str) -> Optional[list]:
        """Return cached matches for (hash_, target), or None on miss/expiry."""
        key = (hash_, target.lower())
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            matches, insert_time = entry
            if time.monotonic() - insert_time > TTL:
                del self._store[key]
                return None
            # Move to end to mark as most recently used (LRU).
            self._store.move_to_end(key)
            return matches

    def put(self, hash_: str, target: str, matches: list) -> None:
        """Store matches for (hash_, target). Evicts oldest entry if at cap."""
        key = (hash_, target.lower())
        now = time.monotonic()
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (matches, now)
            if len(self._store) > MAX_ENTRIES:
                # Evict the oldest entry (first item).
                self._store.popitem(last=False)

    def invalidate_all(self) -> None:
        """Wipe all cached entries — call after any action that changes the screen."""
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def stats(self) -> dict:
        with self._lock:
            now = time.monotonic()
            live = sum(
                1 for (_, ins) in self._store.values()
                if now - ins <= TTL
            )
            return {"total": len(self._store), "live": live, "ttl": TTL}


# Module-level singleton.
CACHE = OcrCache()
