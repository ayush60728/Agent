import action_registry
"""
test_tts_config.py

Unit tests for TTS voice listing, selection, speaking rate, and volume customization.
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
import voice_io
import actions


def test_voice_listing():
    print("\n--- 1. TTS Voice Listing ---")
    voices = voice_io.get_available_voices()
    assert isinstance(voices, list)
    print(f"  [OK] Found {len(voices)} installed SAPI5 voices on system")


def test_rate_and_volume_settings():
    print("\n--- 2. TTS Rate & Volume Configuration ---")
    res_rate = voice_io.set_tts_rate(200)
    assert config.TTS_RATE == 200
    assert "200 wpm" in res_rate
    print("  [OK] Successfully configured speech rate to 200 wpm")

    res_vol = voice_io.set_tts_volume(0.85)
    assert config.TTS_VOLUME == 0.85
    assert "85%" in res_vol
    print("  [OK] Successfully configured speech volume to 85%")

    # Reset
    voice_io.set_tts_rate(175)
    voice_io.set_tts_volume(1.0)


def test_voice_selection():
    print("\n--- 3. TTS Voice Selection ---")
    voices = voice_io.get_available_voices()
    if voices:
        first_voice = voices[0]
        res = voice_io.set_tts_voice(first_voice["name"])
        assert "Set voice to" in res
        print(f"  [OK] Successfully set active voice to '{first_voice['name']}'")

    res_unknown = voice_io.set_tts_voice("nonexistent_voice_xyz")
    assert "not found" in res_unknown
    print("  [OK] Handled unknown voice gracefully")


def test_action_integration():
    print("\n--- 4. Actions Integration for TTS Settings ---")
    res1 = action_registry.execute({"action": "set_tts_rate", "target": "faster"})
    assert "wpm" in res1
    print("  [OK] Handled relative rate change 'faster'")

    res2 = action_registry.execute({"action": "list_tts_voices", "target": ""})
    assert "Available TTS voices" in res2 or "No TTS voices" in res2
    print("  [OK] Executed list_tts_voices action")


if __name__ == "__main__":
    test_voice_listing()
    test_rate_and_volume_settings()
    test_voice_selection()
    test_action_integration()
    print("\nALL TTS CONFIG TESTS PASSED")
