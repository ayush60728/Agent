"""
pet_ui.py

A tiny always-on-top desktop pet that reflects the agent's current state.
Completely decoupled from agent.py — it just polls agent_state.json
(written by voice_io.py) and redraws itself. Run this in its own
terminal, separate from `python agent.py --voice`.

States (from agent_state.json's "state" field, written by voice_io.py):
    idle      -> pet hides, only its head peeks over the bottom edge
    listening -> pet pops fully into view, standing still (ears up)
    thinking  -> fully visible, slow gentle bob
    speaking  -> fully visible, quicker/bigger bob (talking energy)

Art comes from pet_sprite.png / pet_sprite_blink.png, which are built once
from petui.jpg by make_pet_sprite.py. Both are magenta-keyed (their
background is pure magenta), and the window keys magenta out as transparent
via -transparentcolor, so plain Tkinter draws them with no Pillow at runtime.
If the sprites are missing, we fall back to a simple blob so the pet still
runs — re-run `python make_pet_sprite.py` to restore the artwork.

Interaction: left-drag to move the pet, right-click to close it (the window
is borderless, so there's no title-bar X).
"""

import json
import math
import os
import random
import time
import tkinter as tk

STATE_FILE = "agent_state.json"
SPRITE_FILE = "pet_sprite.png"
BLINK_FILE = "pet_sprite_blink.png"

POLL_INTERVAL_MS = 150   # how often we re-read agent_state.json
FRAME_MS = 50            # animation tick (~20 fps)

TRANSPARENT = "magenta"  # color key: these pixels show the desktop through
PEEK_VISIBLE = 62        # px of the pet's head left showing while idle
BOB_HEADROOM = 14        # transparent px above the pet so it can bob up
GLIDE = 0.28             # 0..1 easing toward the target position each tick

# Blink timing (seconds).
BLINK_HOLD = 0.13
BLINK_GAP_MIN = 2.4
BLINK_GAP_MAX = 6.0

# Per-state vertical behavior. "amp"/"speed" only matter for the bob modes.
ANIM = {
    "idle":      {"mode": "peek"},
    "listening": {"mode": "stand"},
    "thinking":  {"mode": "bob", "speed": 0.18, "amp": 4},
    "speaking":  {"mode": "bob", "speed": 0.34, "amp": 7},
}

# Fallback-blob look, only used if the sprite PNGs can't be loaded.
FALLBACK_W, FALLBACK_H = 140, 240
PET_COLOR = "#ff7a33"
PET_OUTLINE = "#4a2c00"


class PetWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)                     # no title bar / borders
        self.root.attributes("-topmost", True)               # always on top
        self.root.attributes("-transparentcolor", TRANSPARENT)

        self.sprite = self._load_image(SPRITE_FILE)
        self.blink_sprite = self._load_image(BLINK_FILE) or self.sprite

        if self.sprite is not None:
            self.sprite_w = self.sprite.width()
            self.sprite_h = self.sprite.height()
        else:
            self.sprite_w, self.sprite_h = FALLBACK_W, FALLBACK_H

        self.win_w = self.sprite_w
        self.win_h = self.sprite_h + BOB_HEADROOM

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.win_x = screen_w - self.win_w
        self.win_y = screen_h - self.win_h
        self.root.geometry(f"{self.win_w}x{self.win_h}+{self.win_x}+{self.win_y}")

        self.canvas = tk.Canvas(
            self.root, width=self.win_w, height=self.win_h,
            bg=TRANSPARENT, highlightthickness=0,
        )
        self.canvas.pack()

        self.current_state = "idle"
        self.bob_phase = 0.0
        # dy = how far the sprite's top sits below the window top. Resting
        # (fully standing) is BOB_HEADROOM; larger dy sinks the pet so only
        # its head peeks. We glide dy toward its target for smooth pop-ups.
        self.rest_dy = BOB_HEADROOM
        self.peek_dy = BOB_HEADROOM + (self.sprite_h - PEEK_VISIBLE)
        # base_dy is the glided resting position (peek vs stand); the bob is
        # added on top at full amplitude so it stays crisp while transitions
        # between states still glide smoothly.
        self.base_dy = self.peek_dy
        self.dy = self.peek_dy

        self.next_blink = time.time() + self._blink_gap()
        self.blinking_until = 0.0

        # Interaction: drag to move, right-click to quit.
        self._drag_dx = self._drag_dy = 0
        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Button-3>", lambda _e: self.root.destroy())

        self._poll_state()
        self._animate()

    # -- asset loading -------------------------------------------------

    def _load_image(self, path):
        """Return a PhotoImage for path, or None if it can't be loaded."""
        if not os.path.exists(path):
            return None
        try:
            return tk.PhotoImage(file=path)
        except tk.TclError:
            return None

    # -- drawing -------------------------------------------------------

    def _render(self, dy: float, blink: bool):
        """Draw the pet with its top edge at y=dy inside the window."""
        self.canvas.delete("all")
        if self.sprite is not None:
            img = self.blink_sprite if blink else self.sprite
            # anchor="n": x is the horizontal center, y is the top edge.
            self.canvas.create_image(self.win_w // 2, dy, image=img, anchor="n")
        else:
            self._render_fallback(dy, blink)

    def _render_fallback(self, dy: float, blink: bool):
        """Simple blob-with-ears, used only when the sprite PNGs are absent."""
        cx = self.win_w // 2
        top = dy + 20
        bottom = dy + self.sprite_h - 10
        self.canvas.create_oval(cx - 35, top, cx + 35, bottom,
                                 fill=PET_COLOR, outline=PET_OUTLINE, width=3)
        self.canvas.create_polygon(cx - 30, top + 10, cx - 45, top - 15, cx - 10, top,
                                   fill=PET_COLOR, outline=PET_OUTLINE, width=2)
        self.canvas.create_polygon(cx + 30, top + 10, cx + 45, top - 15, cx + 10, top,
                                   fill=PET_COLOR, outline=PET_OUTLINE, width=2)
        eye_y = top + 40
        if blink:
            self.canvas.create_line(cx - 18, eye_y + 5, cx - 8, eye_y + 5, fill=PET_OUTLINE, width=3)
            self.canvas.create_line(cx + 8, eye_y + 5, cx + 18, eye_y + 5, fill=PET_OUTLINE, width=3)
        else:
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
            # half-written file caught mid-write — just keep the last state.
            pass
        self.root.after(POLL_INTERVAL_MS, self._poll_state)

    # -- animation -----------------------------------------------------

    def _blink_gap(self) -> float:
        return random.uniform(BLINK_GAP_MIN, BLINK_GAP_MAX)

    def _animate(self):
        anim = ANIM.get(self.current_state, ANIM["idle"])
        mode = anim["mode"]

        # Glide the resting position (peek when idle, standing otherwise).
        base_target = self.peek_dy if mode == "peek" else self.rest_dy
        self.base_dy += (base_target - self.base_dy) * GLIDE

        # Bob is layered on top at full amplitude. abs(sin) so the pet bobs
        # *up* from its feet-down rest — never sinking below the screen edge.
        bob = 0.0
        if mode == "bob":
            self.bob_phase += anim["speed"]
            bob = abs(math.sin(self.bob_phase)) * anim["amp"]
        self.dy = self.base_dy - bob

        now = time.time()
        if now >= self.blinking_until and now >= self.next_blink:
            self.blinking_until = now + BLINK_HOLD
            self.next_blink = now + BLINK_HOLD + self._blink_gap()
        blink = now < self.blinking_until

        self._render(self.dy, blink)
        self.root.after(FRAME_MS, self._animate)

    # -- drag to move --------------------------------------------------

    def _start_drag(self, event):
        self._drag_dx = event.x
        self._drag_dy = event.y

    def _on_drag(self, event):
        self.win_x = self.root.winfo_x() + (event.x - self._drag_dx)
        self.win_y = self.root.winfo_y() + (event.y - self._drag_dy)
        self.root.geometry(f"+{self.win_x}+{self.win_y}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    PetWindow().run()
