"""
pet_ui.py

A tiny always-on-top desktop pet that reflects the agent's current state.
Completely decoupled from agent.py — it just polls agent_state.json
(written by voice_io.py) and redraws itself. Run this in its own
terminal, separate from `python agent.py --voice`.

States (from agent_state.json's "state" field):
    idle     -> pet mostly hidden, just a sliver peeking from the corner
    listening -> pet stands up fully, sideways, visible
    thinking -> pet bobs slightly (reuses the "standing" pose)
    speaking -> pet bobs slightly (reuses the "standing" pose)

This uses simple canvas shapes as placeholder art (a blob with ears) —
swap PET_COLOR / the draw functions for real sprite images later via
PhotoImage if you want actual artwork instead of a shape.
"""

import json
import time
import tkinter as tk

STATE_FILE = "agent_state.json"
POLL_INTERVAL_MS = 150

WINDOW_W = 140
WINDOW_H = 160
PEEK_VISIBLE = 40   # how many pixels poke out from the corner when idle
BOB_AMPLITUDE = 4   # pixels of up/down bob while thinking/speaking

PET_COLOR = "#ffb703"
PET_OUTLINE = "#4a2c00"


class PetWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)       # no title bar / borders
        self.root.attributes("-topmost", True)  # always on top
        self.root.attributes("-transparentcolor", "magenta")  # magenta = invisible

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        self.base_x = screen_w - WINDOW_W
        self.base_y = screen_h - WINDOW_H

        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{self.base_x}+{self.base_y}")

        self.canvas = tk.Canvas(
            self.root, width=WINDOW_W, height=WINDOW_H,
            bg="magenta", highlightthickness=0,
        )
        self.canvas.pack()

        self.current_state = "idle"
        self.bob_phase = 0.0

        self._draw(offset_y=WINDOW_H - PEEK_VISIBLE)
        self._poll_state()
        self._animate()

    # -- drawing -----------------------------------------------------

    def _draw(self, offset_y: float):
        """Redraw the pet, vertically shifted by offset_y (bigger =
        further down / more hidden below the window's bottom edge)."""
        self.canvas.delete("all")

        cx = WINDOW_W // 2
        body_top = 20 + offset_y
        body_bottom = WINDOW_H - 10 + offset_y

        # body
        self.canvas.create_oval(
            cx - 35, body_top, cx + 35, body_bottom,
            fill=PET_COLOR, outline=PET_OUTLINE, width=3,
        )
        # ears
        self.canvas.create_polygon(
            cx - 30, body_top + 10, cx - 45, body_top - 15, cx - 10, body_top,
            fill=PET_COLOR, outline=PET_OUTLINE, width=2,
        )
        self.canvas.create_polygon(
            cx + 30, body_top + 10, cx + 45, body_top - 15, cx + 10, body_top,
            fill=PET_COLOR, outline=PET_OUTLINE, width=2,
        )
        # eyes
        eye_y = body_top + 40
        self.canvas.create_oval(cx - 18, eye_y, cx - 8, eye_y + 10, fill=PET_OUTLINE)
        self.canvas.create_oval(cx + 8, eye_y, cx + 18, eye_y + 10, fill=PET_OUTLINE)

    # -- state polling -------------------------------------------------

    def _poll_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                payload = json.load(f)
            self.current_state = payload.get("state", "idle")
        except (OSError, json.JSONDecodeError):
            # No state file yet (agent hasn't run in voice mode), or a
            # half-written file caught mid-write — just keep last state.
            pass

        self.root.after(POLL_INTERVAL_MS, self._poll_state)

    # -- animation -----------------------------------------------------

    def _animate(self):
        if self.current_state == "idle":
            # Mostly hidden — only PEEK_VISIBLE pixels poking out.
            self._draw(offset_y=WINDOW_H - PEEK_VISIBLE)

        elif self.current_state == "listening":
            # Fully standing, visible.
            self._draw(offset_y=0)

        else:
            # thinking / speaking — fully visible, gentle bob.
            import math
            self.bob_phase += 0.3
            bob = math.sin(self.bob_phase) * BOB_AMPLITUDE
            self._draw(offset_y=bob)

        self.root.after(50, self._animate)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    PetWindow().run()