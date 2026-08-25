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

Behaviour the user asked for:
    * VERY small, pinned to the bottom-right corner.       (see TARGET_H)
    * NOT clickable and NOT movable — the window is click-through (mouse events
      pass straight to whatever is behind it), so it can't be dragged, focused,
      or clicked. There is therefore no way to close it by hand, so instead its
      lifetime is tied to the agent: pass --parent-pid <agent pid> and the pet
      exits automatically when the agent process exits.

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


def ensure_audio():
    """Make sure pet_video_audio.wav exists; extract it from the video if not.
    Best-effort — returns True if the WAV is available to play. (The launcher
    deletes a stale WAV when the video changes, so this re-derives it from the
    current video.)"""
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

        # Open the video and work out the display size from its aspect ratio.
        self.container = av.open(VIDEO)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        vw = self.stream.codec_context.width
        vh = self.stream.codec_context.height
        self.h = TARGET_H
        self.w = max(1, round(vw * (TARGET_H / vh)))

        rate = self.stream.average_rate or 24
        self.delay = max(1, round(1000 / float(rate)))
        self._gen = self._frames()
        self.photo = None

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - self.w - MARGIN_X
        y = sh - self.h - MARGIN_Y
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

        self.label = tk.Label(self.root, borderwidth=0, highlightthickness=0,
                              bg=KEY)
        self.label.pack()

        # No drag / click bindings on purpose: the pet is non-interactive.
        # On Windows we also make the whole window click-through below.
        self.root.update_idletasks()
        self._make_click_through()
        self._open_parent_handle()

        self._play_audio_once()
        self._tick()
        self._watch_parent()

    # -- windows: click-through + parent-liveness ----------------------

    def _make_click_through(self):
        """Set WS_EX_LAYERED | WS_EX_TRANSPARENT so mouse events pass straight
        through the window — it can't be clicked, dragged, or focused. Best
        effort: if it fails the pet still runs, just interactive."""
        if not IS_WIN:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  cur | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception:
            pass

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
        rgb = frame.reformat(width=self.w, height=self.h, format="rgb24").to_image()
        self.photo = ImageTk.PhotoImage(self._key(rgb))
        self.label.config(image=self.photo)
        self.root.after(self.delay, self._tick)

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
