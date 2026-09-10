"""
test_tray_app.py

Unit tests for System Tray icon generation, color mapping, and menu callbacks.
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image
import tray_app
import config


def test_tray_icon_generation():
    print("\n--- 1. Tray Icon Generation for All States ---")
    states = ["idle", "listening", "thinking", "speaking", "degraded", "unknown"]
    for state in states:
        img = tray_app.create_tray_image(state=state, size=64)
        assert isinstance(img, Image.Image)
        assert img.size == (64, 64)
        print(f"  [OK] Generated badge for state '{state}'")


def test_tray_state_detection():
    print("\n--- 2. Tray State Detection ---")
    state = tray_app.get_current_state()
    assert state in ("idle", "listening", "thinking", "speaking", "degraded")
    print(f"  [OK] Successfully resolved current agent state: {state}")


def test_tray_menu_actions():
    print("\n--- 3. Tray Menu Callback Handlers ---")
    app = tray_app.AgentTrayApp()

    # Toggle Verbosity
    config.VERBOSITY = "terse"
    app._on_toggle_verbosity(None, None)
    assert config.VERBOSITY == "detailed"
    app._on_toggle_verbosity(None, None)
    assert config.VERBOSITY == "terse"
    print("  [OK] Verbosity toggle handler inverted setting accurately")

    # Toggle Dry-Run
    config.DRY_RUN = False
    app._on_toggle_dry_run(None, None)
    assert config.DRY_RUN is True
    app._on_toggle_dry_run(None, None)
    assert config.DRY_RUN is False
    print("  [OK] Dry-run toggle handler inverted setting accurately")


if __name__ == "__main__":
    test_tray_icon_generation()
    test_tray_state_detection()
    test_tray_menu_actions()
    print("\nALL TRAY APP TESTS PASSED")
