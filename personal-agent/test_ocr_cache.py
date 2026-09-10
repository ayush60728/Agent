import actions
"""
test_ocr_cache.py

Unit tests for ocr_cache.py (hit, miss, TTL expiry, invalidation, LRU eviction).
"""

import time
from PIL import Image
import ocr_cache


def test_cache_hit_and_miss():
    print("\n--- 1. Cache hit and miss ---")
    cache = ocr_cache.OcrCache()
    img1 = Image.new("RGB", (100, 100), color="blue")
    h1 = ocr_cache.screen_hash(img1)

    # Miss
    assert cache.get(h1, "submit") is None
    print("  [OK] Cache miss on empty cache")

    # Put and Hit
    dummy_matches = [{"text": "submit", "cx": 50, "cy": 50}]
    cache.put(h1, "submit", dummy_matches)
    hit = cache.get(h1, "submit")
    assert hit == dummy_matches
    print("  [OK] Cache hit on matching hash and target")

    # Miss on different target
    assert cache.get(h1, "cancel") is None
    print("  [OK] Cache miss on different target")

    # Miss on different image
    img2 = Image.new("RGB", (100, 100), color="red")
    h2 = ocr_cache.screen_hash(img2)
    assert cache.get(h2, "submit") is None
    print("  [OK] Cache miss on different image hash")


def test_cache_ttl():
    print("\n--- 2. Cache TTL expiry ---")
    cache = ocr_cache.OcrCache()
    # Temporarily set TTL small
    orig_ttl = ocr_cache.TTL
    ocr_cache.TTL = 0.1
    try:
        cache.put("hash1", "button", ["match1"])
        assert cache.get("hash1", "button") == ["match1"]
        time.sleep(0.15)
        assert cache.get("hash1", "button") is None
        print("  [OK] Entry expired after TTL")
    finally:
        ocr_cache.TTL = orig_ttl


def test_cache_invalidation():
    print("\n--- 3. Invalidate all ---")
    cache = ocr_cache.OcrCache()
    cache.put("h1", "btn1", [1])
    cache.put("h2", "btn2", [2])
    assert cache.size() == 2

    cache.invalidate_all()
    assert cache.size() == 0
    assert cache.get("h1", "btn1") is None
    print("  [OK] invalidate_all clears entire store")


def test_lru_eviction():
    print("\n--- 4. LRU Eviction on MAX_ENTRIES ---")
    cache = ocr_cache.OcrCache()
    orig_max = ocr_cache.MAX_ENTRIES
    ocr_cache.MAX_ENTRIES = 3
    try:
        cache.put("h1", "t1", [1])
        cache.put("h2", "t2", [2])
        cache.put("h3", "t3", [3])
        # Access h1 so h2 is oldest accessed
        cache.get("h1", "t1")
        # Put 4th item -> should evict h2
        cache.put("h4", "t4", [4])
        assert cache.size() == 3
        assert cache.get("h1", "t1") == [1]
        assert cache.get("h2", "t2") is None
        assert cache.get("h4", "t4") == [4]
        print("  [OK] Oldest accessed entry evicted when exceeding capacity")
    finally:
        ocr_cache.MAX_ENTRIES = orig_max


if __name__ == "__main__":
    test_cache_hit_and_miss()
    test_cache_ttl()
    test_cache_invalidation()
    test_lru_eviction()
    print("\nALL OCR CACHE TESTS PASSED")
