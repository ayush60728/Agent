"""
color_vision.py

Give the agent a basic sense of on-screen color.

Two jobs:
  * name_color(r, g, b) -> a human word for one pixel's color ("red",
    "green", "gray", ...). Answers "what color is this?".
  * region color analysis (color_fraction / dominant_chromatic_color) -> how
    much of a screen region is a given color. Used as a *tiebreaker* in
    click_text: when the user says "click the red submit button", OCR finds
    every "submit" on screen and this picks the one whose button area is red.

Pure standard library + Pillow (already a dependency): colorsys does the
RGB->HSV classification, no numpy/opencv. The regions we analyze are small (a
single button's box), so a plain Python pixel loop is plenty fast.
"""

import colorsys


# The color vocabulary we can both NAME and MATCH against. A word the user
# says must normalize into one of these for the tiebreaker to compare.
_CANON = {
    "red", "orange", "yellow", "green", "cyan", "blue",
    "purple", "pink", "brown", "black", "white", "gray",
}
# Everyday synonyms -> our canonical name.
_ALIASES = {
    "grey": "gray", "silver": "gray",
    "violet": "purple",
    "magenta": "pink", "fuchsia": "pink",
    "turquoise": "cyan", "teal": "cyan",
    "gold": "yellow", "lime": "green",
}
COLOR_WORDS = _CANON | set(_ALIASES)


def canonical_color(word):
    """Normalize a spoken color word to our vocabulary, or None."""
    if not word:
        return None
    w = word.strip().lower()
    if w in _ALIASES:
        return _ALIASES[w]
    if w in _CANON:
        return w
    return None


def name_color(r, g, b) -> str:
    """Best single-word name for an (r, g, b) pixel (each 0-255)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue = h * 360.0

    # Achromatic first: very dark = black, washed-out = white/gray.
    if v < 0.16:
        return "black"
    if s < 0.13:
        return "white" if v > 0.82 else "gray"

    # A dark, saturated orange reads as brown, not orange.
    if 10 <= hue < 45 and v < 0.55 and s >= 0.4 and v < 0.75:
        return "brown"

    if hue < 15 or hue >= 345:
        return "red"
    if hue < 45:
        return "orange"
    if hue < 67:
        return "yellow"
    if hue < 170:
        return "green"
    if hue < 200:
        return "cyan"
    if hue < 255:
        return "blue"
    if hue < 292:
        return "purple"
    return "pink"


def extract_color(text):
    """Split a click target into (canonical_color, remaining_text).

        "red submit"      -> ("red", "submit")
        "download green"  -> ("green", "download")
        "submit"          -> (None, "submit")

    Removes the FIRST color word found anywhere in the phrase (so word order
    doesn't matter) and leaves the rest intact as the text to locate."""
    if not text:
        return None, text
    tokens = text.split()
    for i, tok in enumerate(tokens):
        c = canonical_color(tok.strip(".,!?"))
        if c is not None:
            remaining = " ".join(tokens[:i] + tokens[i + 1:]).strip()
            return c, remaining
    return None, text


def _iter_pixels(image, max_samples=2500):
    """Yield (r, g, b) for pixels in a PIL image, subsampling so we never
    classify more than ~max_samples pixels (keeps big regions cheap)."""
    px = image.load()
    w, h = image.size
    total = w * h
    if total == 0:
        return
    step = int((total / max_samples) ** 0.5) if total > max_samples else 1
    step = max(step, 1)
    for y in range(0, h, step):
        for x in range(0, w, step):
            p = px[x, y]
            if isinstance(p, int):          # 'L' (grayscale) mode
                yield p, p, p
            else:
                yield p[0], p[1], p[2]


def color_fraction(image, color_name) -> float:
    """Fraction of (sampled) pixels in a PIL image whose name matches
    color_name. 0.0 for an empty image or unknown color."""
    target = canonical_color(color_name) or color_name
    n = hit = 0
    for r, g, b in _iter_pixels(image):
        n += 1
        if name_color(r, g, b) == target:
            hit += 1
    return (hit / n) if n else 0.0


def dominant_chromatic_color(image) -> str:
    """The most common *chromatic* (non black/white/gray) color name in the
    image — or the most common name overall if it's essentially colorless."""
    counts = {}
    for r, g, b in _iter_pixels(image):
        cname = name_color(r, g, b)
        counts[cname] = counts.get(cname, 0) + 1
    if not counts:
        return "black"
    chromatic = {k: v for k, v in counts.items()
                 if k not in ("black", "white", "gray")}
    pool = chromatic or counts
    return max(pool, key=pool.get)


if __name__ == "__main__":
    # Quick self-check of the pure logic (no screen needed).
    swatches = {
        "red": (220, 30, 30), "green": (30, 170, 60), "blue": (40, 80, 220),
        "yellow": (240, 220, 40), "orange": (240, 140, 20),
        "purple": (130, 50, 180), "pink": (230, 90, 190),
        "white": (250, 250, 250), "black": (12, 12, 12), "gray": (128, 128, 128),
        "brown": (110, 70, 30),
    }
    for expected, rgb in swatches.items():
        got = name_color(*rgb)
        print(f"  {rgb!s:18} -> {got:8} (expected {expected})", "OK" if got == expected else "**")
    for phrase in ("red submit", "download green", "submit", "the blue one", "grey"):
        print(f"  extract_color({phrase!r}) -> {extract_color(phrase)}")
