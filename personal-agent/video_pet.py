"""
video_pet.py

A tiny, non-interactive desktop "pet" that loops a video in the bottom-right
corner of the screen — the replacement for the old sprite pet (pet_ui.py).

It decodes pet_video.mp4 frame-by-frame with PyAV (the ffmpeg binding already in
the venv), keys out the background so only the cat floats, and shows the result
in a small borderless, always-on-top Tkinter window that loops forever.

Transparency:
    The source is an exported "transparent" clip, but MP4/H.264 can't carry a
    real alpha channel, so the exporter baked the editor's gray transparency
    *checkerboard* into the pixels. We reconstruct the transparency: the
    checkerboard is neutral gray (r≈g≈b) at a mid/high brightness, while the cat
    is colored, so we mark neutral-gray pixels as background and keep everything
    else. Crucially we only remove gray that is *connected to the frame border*
    (a flood fill from the edges), so enclosed light areas — the cat's eyes —
    are preserved instead of being punched into holes. The background pixels are
    painted magenta and the window keys magenta out via -transparentcolor.

Behaviour:
    * Small, pinned to the bottom-right corner.            (see TARGET_H)
    * Resizable. Right-click the pet for size presets (Small/Medium/Large) or
      hold Ctrl and left-drag it to scale freely; width always follows the
      video's aspect ratio and the window keeps its bottom-right anchor,
      growing toward the top-left. The chosen size is remembered across runs
      in pet_config.json. (This is why the window is interactive rather than
      click-through, as it originally was — catching those resize gestures is
      the whole point.)
    * NOT movable and has no close button. Its lifetime is tied to the agent:
      pass --parent-pid <agent pid> and the pet exits automatically when the
      agent process exits.

Audio: the clip's sound plays ONCE at startup (via winsound, from the
pre-extracted pet_video_audio.wav), then the visual just keeps looping silently.

Launched automatically by agent.py at startup. Run it standalone to preview
(no parent to watch, so kill it via Task Manager):

    python video_pet.py
"""

import argparse
import os
import sys
import wave
from collections import deque
import tkinter as tk

import av
import numpy as np
from PIL import Image, ImageTk

try:
    import winsound
except ImportError:          # non-Windows: just skip the one-time sound
    winsound = None

IS_WIN = sys.platform.startswith("win")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.join(BASE_DIR, "pet_video.mp4")
AUDIO = os.path.join(BASE_DIR, "pet_video_audio.wav")
LOG = os.path.join(BASE_DIR, "video_pet_error.log")

TARGET_H = 100               # window height in px (THE size knob). width follows
                             # the video's aspect ratio. smaller = tinier pet.
MARGIN_X = 16                # gap from the right screen edge
MARGIN_Y = 24                # gap from the bottom (lower = closer to the taskbar)

# Background (checkerboard) keying. A pixel is background if it is near-neutral
# (max-min channel spread small) AND its brightness sits in the checker band
# (bright enough to be the light/dark gray squares, but capped below pure white
# so the cat's white eye highlights are never mistaken for background).
KEY = "magenta"
KEY_RGB = (255, 0, 255)
NEUTRAL_TOL = 26             # max-min channel spread to count as "gray"
BRIGHT_LO = 145              # darkest checker gray to key
BRIGHT_HI = 246              # brightest to key (< 255 keeps white highlights)

POLL_PARENT_MS = 800         # how often to check the agent is still alive

# Resizing. The pet started out strictly click-through (mouse passed straight
# through). To let users resize it we make it interactive instead: right-click
# for the size presets below, or Ctrl+left-drag to scale freely. Height is the
# size knob; width always follows the video's aspect ratio so the cat never
# distorts. The window stays pinned to the bottom-right corner and grows toward
# the top-left. The picked height is remembered in CONFIG across runs.
MIN_H = 60                   # smallest allowed height (px)
MAX_H = 200                  # largest allowed height (px). Kept modest on
                             # purpose: the per-frame background flood-fill
                             # (_border_connected_bg) costs grow with the pixel
                             # area, so a huge pet would animate sluggishly.
SIZE_PRESETS = (("Small", 70), ("Medium", 100), ("Large", 150))
CONFIG = os.path.join(BASE_DIR, "pet_config.json")


def ensure_audio():
    """Make sure pet_video_audio.wav exists; extract it from the video if not.
    Best-effort — returns True if the WAV is available to play. NOTE: a stale
    WAV is NOT auto-purged (nothing in agent.py does this), so whoever swaps
    pet_video.mp4 must delete the old pet_video_audio.wav by hand for the audio
    to re-derive from the new clip."""
    if os.path.exists(AUDIO):
        return True
    try:
        c = av.open(VIDEO)
        if not c.streams.audio:
            c.close()
            return False
        res = av.AudioResampler(format="s16", layout="stereo", rate=48000)
        wf = wave.open(AUDIO, "wb")
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)

        def pump(fr):
            out = res.resample(fr)
            if out is None:
                out = []
            elif not isinstance(out, list):
                out = [out]
            for r in out:
                wf.writeframes(bytes(r.planes[0]))

        for frame in c.decode(audio=0):
            pump(frame)
        pump(None)  # flush
        wf.close()
        c.close()
        return True
    except Exception:
        return False


def _border_connected_bg(cand, w, h):
    """Given a boolean candidate-background mask (neutral gray pixels), return
    the subset that is connected to the frame border by a 4-neighbour flood
    fill from every border candidate pixel. Enclosed candidate blobs (the eyes)
    are NOT reached, so they stay part of the cat."""
    flat = cand.reshape(-1)
    reached = np.zeros(flat.shape, dtype=bool)
    dq = deque()

    def seed(i):
        if flat[i] and not reached[i]:
            reached[i] = True
            dq.append(i)

    for x in range(w):
        seed(x)                    # top row
        seed((h - 1) * w + x)      # bottom row
    for y in range(h):
        seed(y * w)                # left col
        seed(y * w + (w - 1))      # right col

    while dq:
        i = dq.popleft()
        col = i % w
        if col > 0:
            seed(i - 1)
        if col < w - 1:
            seed(i + 1)
        if i >= w:
            seed(i - w)
        if i < (h - 1) * w:
            seed(i + w)
    return reached.reshape(h, w)


class VideoPet:
    def __init__(self, parent_pid=None):
        self.parent_pid = parent_pid
        self._parent_handle = None
        self._closed = False

        self.root = tk.Tk()
        self.root.overrideredirect(True)          # borderless, no taskbar entry
        self.root.attributes("-topmost", True)    # always on top
        self.root.attributes("-transparentcolor", KEY)
        self.root.configure(bg=KEY)               # erase-to-key so a forced
                                                  # repaint clears to transparent

        # Open the video and work out the display size from its aspect ratio.
        self.container = av.open(VIDEO)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        vw = self.stream.codec_context.width
        vh = self.stream.codec_context.height
        self._aspect = vw / vh                     # width = round(height * aspect)
        self.h = self._load_size()                 # persisted size, or TARGET_H
        self.w = self._width_for(self.h)
        self._resizing = False                     # mid Ctrl-drag?
        self._last_frame = None                    # last decoded frame, for
                                                   # crisp re-render on resize

        rate = self.stream.average_rate or 24
        self.delay = max(1, round(1000 / float(rate)))
        self._gen = self._frames()
        self.photo = None

        self._apply_geometry()

        self.label = tk.Label(self.root, borderwidth=0, highlightthickness=0,
                              bg=KEY)
        self.label.pack(fill="both", expand=True)

        # The pet is interactive so it can be resized: right-click for size
        # presets, Ctrl+left-drag to scale freely. It is deliberately NOT
        # click-through (that would swallow these gestures). It still can't be
        # moved and has no close button; its lifetime is tied to the agent pid.
        self.root.update_idletasks()
        self._build_menu()
        self._bind_interactions()
        self._open_parent_handle()

        self._play_audio_once()
        self._tick()
        self._watch_parent()

    # -- resizing / interaction ----------------------------------------

    def _width_for(self, h):
        """Width that keeps the video's aspect ratio at height h."""
        return max(1, round(h * self._aspect))

    def _apply_geometry(self):
        """Pin the window (at its current w/h) to the bottom-right corner, so
        it grows toward the top-left as it gets bigger."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - self.w - MARGIN_X
        y = sh - self.h - MARGIN_Y
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _build_menu(self):
        """Right-click context menu of size presets. Radiobuttons show a check
        next to the preset matching the current height (freeform sizes match
        none)."""
        self._size_var = tk.IntVar(value=self.h)
        self.menu = tk.Menu(self.root, tearoff=0)
        for name, h in SIZE_PRESETS:
            self.menu.add_radiobutton(
                label=f"{name}  ({h}px)", value=h, variable=self._size_var,
                command=lambda h=h: self._set_size(h))

    def _bind_interactions(self):
        """Bind the resize gestures on the visible label — it fills the window,
        so it (not the root) is what the cursor is actually over."""
        self.label.bind("<Button-3>", self._popup_menu)          # right-click
        self.label.bind("<Control-Button-1>", self._on_resize_start)
        self.label.bind("<Control-B1-Motion>", self._on_resize_drag)
        self.label.bind("<ButtonRelease-1>", self._on_resize_end)

    def _popup_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _set_size(self, h, save=True, render=True):
        """Resize to height h px (clamped to [MIN_H, MAX_H]), width following
        the aspect ratio, keeping the bottom-right anchor. render re-paints the
        last frame at once (crisp preset snaps); the frame tick would otherwise
        catch up within ~one frame anyway."""
        self.h = int(max(MIN_H, min(MAX_H, round(h))))
        self.w = self._width_for(self.h)
        self._size_var.set(self.h)
        self._apply_geometry()
        if render and self._last_frame is not None:
            self._render(self._last_frame)
        if save:
            self._save_size()

    def _on_resize_start(self, event):
        self._resizing = True
        self._drag_start_y = event.y_root
        self._drag_start_h = self.h

    def _on_resize_drag(self, event):
        if not self._resizing:
            return
        # The window is anchored bottom-right and expands up-left, so dragging
        # the cursor up grows the pet and dragging down shrinks it. Skip the
        # per-motion re-render (render=False) to avoid firing the O(area) key
        # flood-fill on every mouse move; the frame tick keeps it looking live.
        delta = self._drag_start_y - event.y_root
        self._set_size(self._drag_start_h + delta, save=False, render=False)

    def _on_resize_end(self, event):
        if not self._resizing:
            return
        self._resizing = False
        if self._last_frame is not None:
            self._render(self._last_frame)     # crisp final frame
        self._save_size()

    def _load_size(self):
        """Return the persisted pet height if one was saved and is in range,
        else the TARGET_H default. Best-effort."""
        try:
            import json
            with open(CONFIG, encoding="utf-8") as f:
                h = int(json.load(f).get("height", TARGET_H))
            if MIN_H <= h <= MAX_H:
                return h
        except (OSError, ValueError, TypeError):
            pass
        return TARGET_H

    def _save_size(self):
        """Persist the current height so the size survives restarts.
        Best-effort — a failure here must never take the pet down."""
        try:
            import json
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump({"height": self.h}, f)
        except OSError:
            pass

    # -- windows: parent-liveness --------------------------------------

    def _open_parent_handle(self):
        """Open a handle to the agent process so we can detect when it exits.
        Using a handle (not just the pid) avoids a pid-reuse race."""
        if not (IS_WIN and self.parent_pid):
            return
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
            SYNCHRONIZE = 0x00100000
            self._parent_handle = k32.OpenProcess(SYNCHRONIZE, False,
                                                  int(self.parent_pid))
        except Exception:
            self._parent_handle = None

    def _watch_parent(self):
        """If the agent process has exited, close the pet too."""
        if self._closed:
            return
        if self._parent_handle:
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                k32.WaitForSingleObject.restype = ctypes.c_uint
                WAIT_OBJECT_0 = 0x0
                if k32.WaitForSingleObject(self._parent_handle, 0) == WAIT_OBJECT_0:
                    self._quit()
                    return
            except Exception:
                pass
        self.root.after(POLL_PARENT_MS, self._watch_parent)

    # -- frames / keying / audio ---------------------------------------

    def _frames(self):
        """Infinite frame generator: decode to the end, seek back, repeat."""
        while not self._closed:
            produced = False
            for frame in self.container.decode(video=0):
                produced = True
                yield frame
            if not produced:
                return  # nothing decodable — bail rather than spin
            try:
                self.container.seek(0)
            except av.AVError:
                self.container.close()
                self.container = av.open(VIDEO)
                self.stream = self.container.streams.video[0]
                self.stream.thread_type = "AUTO"

    def _key(self, rgb_img):
        """Paint the baked-in checkerboard background magenta (-> transparent),
        keeping the cat and its enclosed eyes."""
        a = np.asarray(rgb_img).astype(np.int16)
        mx = a.max(2)
        mn = a.min(2)
        mean = a.mean(2)
        cand = ((mx - mn) <= NEUTRAL_TOL) & (mean >= BRIGHT_LO) & (mean <= BRIGHT_HI)
        bg = _border_connected_bg(cand, self.w, self.h)
        out = np.array(rgb_img)          # uint8 copy we can paint into
        out[bg] = KEY_RGB
        return Image.fromarray(out, "RGB")

    def _play_audio_once(self):
        if winsound is None:
            return
        if ensure_audio():
            try:
                winsound.PlaySound(AUDIO, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except RuntimeError:
                pass

    def _tick(self):
        if self._closed:
            return
        try:
            frame = next(self._gen)
        except StopIteration:
            self._quit()
            return
        self._last_frame = frame
        self._render(frame)
        self.root.after(self.delay, self._tick)

    def _render(self, frame):
        """Reformat frame to the current w/h, key out the background, and show
        it. Called by the frame tick and again on resize so the pet re-renders
        at the new size immediately."""
        rgb = frame.reformat(width=self.w, height=self.h, format="rgb24").to_image()
        self.photo = ImageTk.PhotoImage(self._key(rgb))
        self.label.config(image=self.photo)
        self._force_repaint()

    def _force_repaint(self):
        """Force a full erase+repaint of the window each frame. Windows'
        color-key transparency (-transparentcolor) can otherwise leave 'ghost'
        trails of moving content (e.g. the typing text) because stale pixels
        linger in the keyed layer instead of being cleared; invalidating +
        erasing the whole window (to the key colour, i.e. transparent) forces
        those pixels to be repainted every frame. Best-effort, Windows-only."""
        if not IS_WIN:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            RDW_INVALIDATE = 0x0001
            RDW_ERASE = 0x0004
            RDW_ALLCHILDREN = 0x0080
            RDW_UPDATENOW = 0x0100
            user32.RedrawWindow(hwnd, None, None,
                                RDW_INVALIDATE | RDW_ERASE
                                | RDW_ALLCHILDREN | RDW_UPDATENOW)
        except Exception:
            pass

    def _quit(self):
        self._closed = True
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)  # stop any playback
            except RuntimeError:
                pass
        try:
            self.container.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, default=None,
                        help="exit when this process (the agent) exits")
    args = parser.parse_args()
    if not os.path.exists(VIDEO):
        return
    VideoPet(parent_pid=args.parent_pid).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Launched headless (pythonw) by agent.py, so log instead of vanishing.
        import traceback
        try:
            with open(LOG, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
