"""
tray_app.py

System Tray companion application for the Personal Desktop Agent.
Provides real-time visual status in the Windows taskbar and quick settings context menu.

Features:
    - Dynamic status badge icon (🟢 Idle, 🔵 Listening, 🟠 Thinking, 🟣 Speaking, 🔴 Degraded)
    - Background polling of agent_state.json for zero-latency state sync
    - Context Menu for:
        * Verbosity toggle (Terse vs. Detailed)
        * Dry-Run safety mode toggle
        * Speech rate adjustments
        * Quick access to voice log, prompt cache, and training stats
        * Clean shutdown
"""

import json
import os
import sys
import time
import threading
from pathlib import Path
from PIL import Image, ImageDraw

import config
import health_monitor
import prompt_cache
import trainer

try:
    import pystray
    from pystray import MenuItem as item, Menu
except ImportError:
    pystray = None

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "agent_state.json"
LOG_FILE = BASE_DIR / "voice_log.json"

STATE_COLORS = {
    "idle": (34, 197, 94),        # Green
    "listening": (59, 130, 246),   # Blue
    "thinking": (249, 115, 22),   # Orange
    "speaking": (168, 85, 247),   # Purple
    "degraded": (239, 68, 68),    # Red
}


def create_tray_image(state: str = "idle", size: int = 64) -> Image.Image:
    """Generate a crisp circular badge icon with status color and white center dot."""
    color = STATE_COLORS.get(state.lower(), STATE_COLORS["idle"])

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer circle
    draw.ellipse([4, 4, size - 5, size - 5], fill=color, outline=(255, 255, 255, 200), width=2)

    # Inner bright dot
    dot_margin = size // 3
    draw.ellipse(
        [dot_margin, dot_margin, size - dot_margin, size - dot_margin],
        fill=(255, 255, 255, 230),
    )

    return img


def get_current_state() -> str:
    """Read agent_state.json or check health monitor."""
    if not health_monitor.llm_is_healthy():
        return "degraded"

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return str(data.get("state", "idle")).lower()
        except Exception:
            pass
    return "idle"


class AgentTrayApp:
    def __init__(self):
        self._current_state = "idle"
        self._running = False
        self._icon: Optional[pystray.Icon] = None

    def _on_toggle_verbosity(self, icon, item_obj):
        target = "detailed" if getattr(config, "VERBOSITY", "terse") == "terse" else "terse"
        config.VERBOSITY = target
        print(f"Tray: Set verbosity to {target}")

    def _on_toggle_dry_run(self, icon, item_obj):
        config.DRY_RUN = not getattr(config, "DRY_RUN", False)
        print(f"Tray: Set DRY_RUN to {config.DRY_RUN}")

    def _on_set_speech_rate(self, rate: int):
        def handler(icon, item_obj):
            import voice_io
            voice_io.set_tts_rate(rate)
            print(f"Tray: Set TTS rate to {rate} wpm")
        return handler

    def _on_open_log(self, icon, item_obj):
        if LOG_FILE.exists():
            os.startfile(str(LOG_FILE))

    def _on_open_cache(self, icon, item_obj):
        cache_path = prompt_cache.CACHE_FILE
        if cache_path.exists():
            os.startfile(str(cache_path))

    def _on_exit(self, icon, item_obj):
        self._running = False
        if self._icon:
            self._icon.stop()
        sys.exit(0)

    def _build_menu(self) -> pystray.Menu:
        return Menu(
            item(lambda text: f"Status: {self._current_state.upper()}", None, enabled=False),
            item(
                "Verbosity: Terse (Concise)",
                self._on_toggle_verbosity,
                checked=lambda item: getattr(config, "VERBOSITY", "terse") == "terse",
            ),
            item(
                "Dry-Run Mode (Safe)",
                self._on_toggle_dry_run,
                checked=lambda item: getattr(config, "DRY_RUN", False),
            ),
            Menu.SEPARATOR,
            item("Speech Rate", Menu(
                item("Slower (150 wpm)", self._on_set_speech_rate(150)),
                item("Normal (175 wpm)", self._on_set_speech_rate(175)),
                item("Faster (200 wpm)", self._on_set_speech_rate(200)),
            )),
            Menu.SEPARATOR,
            item("Open Voice Log", self._on_open_log),
            item("Open Prompt Cache", self._on_open_cache),
            Menu.SEPARATOR,
            item("Exit Agent", self._on_exit),
        )

    def _poll_state_loop(self):
        while self._running:
            state = get_current_state()
            if state != self._current_state:
                self._current_state = state
                if self._icon:
                    self._icon.icon = create_tray_image(state)
                    self._icon.title = f"Personal Agent ({state})"
            time.sleep(0.5)

    def run(self):
        if pystray is None:
            print("pystray not installed; system tray running in headless simulation mode.")
            return

        self._running = True
        self._current_state = get_current_state()
        initial_img = create_tray_image(self._current_state)

        self._icon = pystray.Icon(
            "PersonalAgent",
            initial_img,
            f"Personal Agent ({self._current_state})",
            menu=self._build_menu(),
        )

        poll_thread = threading.Thread(target=self._poll_state_loop, daemon=True)
        poll_thread.start()

        self._icon.run()


def launch_tray():
    app = AgentTrayApp()
    app.run()


if __name__ == "__main__":
    launch_tray()
