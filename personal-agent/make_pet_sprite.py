"""
make_pet_sprite.py

One-time asset builder: turns the source artwork (petui.jpg, a pixel-art
Charmander on a baked-in checkerboard "transparency") into sprite PNGs that
pet_ui.py can load with plain Tkinter (no Pillow needed at runtime).

What it does:
    1. Flood-fill the checkerboard background (from the image borders) into a
       magenta color key. The sprite's solid black outline walls the fill off,
       so enclosed light areas (belly, eye highlights) are preserved.
    2. Erode a couple of neutral-gray "halo" rings left by JPEG artifacts.
    3. Crop to the sprite's bounding box with a little padding.
    4. Downscale with nearest-neighbor so the pixels stay crisp.
    5. Save pet_sprite.png (magenta key) and a few cheap animation variants.

Pillow is only required to run THIS script. Re-run it if petui.jpg changes:

    python make_pet_sprite.py
"""

from PIL import Image
from collections import deque

SRC = "petui.jpg"
OUT = "pet_sprite.png"
KEY = (255, 0, 255)          # magenta color key -> transparent in pet_ui.py
TARGET_H = 240               # sprite height in px after downscale
PAD = 6                      # transparent padding around the crop (source px)


def is_background(px):
    """Light + near-neutral => checkerboard/halo, not the colored sprite."""
    r, g, b = px
    return (max(r, g, b) - min(r, g, b) < 30) and (min(r, g, b) > 115)


def is_neutral_halo(px):
    """Slightly looser test used only to erode JPEG halo rings."""
    r, g, b = px
    return (max(r, g, b) - min(r, g, b) < 42) and (min(r, g, b) > 95)


def main():
    im = Image.open(SRC).convert("RGB")
    W, H = im.size
    px = im.load()

    filled = bytearray(W * H)  # 1 where background

    # 1. Flood fill background inward from every border pixel.
    q = deque()

    def seed(x, y):
        i = y * W + x
        if not filled[i] and is_background(px[x, y]):
            filled[i] = 1
            q.append((x, y))

    for x in range(W):
        seed(x, 0)
        seed(x, H - 1)
    for y in range(H):
        seed(0, y)
        seed(W - 1, y)

    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H:
                i = ny * W + nx
                if not filled[i] and is_background(px[nx, ny]):
                    filled[i] = 1
                    q.append((nx, ny))

    # 2. Erode neutral-gray halo: grow the filled region into adjacent
    #    neutral-ish pixels a few times (stops at black outline / colors).
    for _ in range(3):
        grow = []
        for y in range(H):
            row = y * W
            for x in range(W):
                if filled[row + x]:
                    continue
                if not is_neutral_halo(px[x, y]):
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and filled[ny * W + nx]:
                        grow.append((x, y))
                        break
        for x, y in grow:
            filled[y * W + x] = 1

    # 3. Build RGBA: background -> transparent, sprite -> opaque.
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    op = out.load()
    minx, miny, maxx, maxy = W, H, 0, 0
    for y in range(H):
        row = y * W
        for x in range(W):
            if filled[row + x]:
                continue
            op[x, y] = (*px[x, y], 255)
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y

    # 4. Crop to sprite bbox + padding, then downscale (nearest = crisp).
    minx = max(0, minx - PAD)
    miny = max(0, miny - PAD)
    maxx = min(W, maxx + 1 + PAD)
    maxy = min(H, maxy + 1 + PAD)
    cropped = out.crop((minx, miny, maxx, maxy))

    cw, ch = cropped.size
    scale = TARGET_H / ch
    new_w = max(1, round(cw * scale))
    sprite = cropped.resize((new_w, TARGET_H), Image.NEAREST)

    # 5a. Composite onto the magenta key so plain Tk PhotoImage can key it out.
    keyed = Image.new("RGB", sprite.size, KEY)
    keyed.paste(sprite, (0, 0), sprite)
    keyed.save(OUT)
    print(f"wrote {OUT} ({sprite.size[0]}x{sprite.size[1]})")

    # 5b. Cheap "blink" frame: fill the eyes with the dark outline color so
    #     they read as shut. We locate the eyes by their blue iris pixels
    #     rather than a fixed y-band — the old band was a fraction of height
    #     and landed on the mouth, giving the pet a "mustache" instead of a
    #     blink. Within the eyes' bounding box we darken the blue iris and the
    #     near-white sparkle, but leave the orange face and black outline.
    DARK = (60, 32, 10)

    def is_iris(r, g, b):
        return b > r + 25 and b > 90 and b >= g

    blink = sprite.copy()
    bp = blink.load()
    bw, bh = blink.size

    eye_xs, eye_ys = [], []
    for yy in range(bh):
        for xx in range(bw):
            r, g, b, a = bp[xx, yy]
            if a > 0 and is_iris(r, g, b):
                eye_xs.append(xx)
                eye_ys.append(yy)

    if eye_ys:
        x0, x1 = min(eye_xs) - 2, max(eye_xs) + 2
        y0, y1 = min(eye_ys) - 1, max(eye_ys) + 1
        for yy in range(max(0, y0), min(bh, y1 + 1)):
            for xx in range(max(0, x0), min(bw, x1 + 1)):
                r, g, b, a = bp[xx, yy]
                if a == 0:
                    continue
                # blue iris, or the white sparkle inside it (all channels
                # high). Orange face has a low blue channel, so it's spared.
                if is_iris(r, g, b) or min(r, g, b) > 150:
                    bp[xx, yy] = (*DARK, a)

    keyed_blink = Image.new("RGB", blink.size, KEY)
    keyed_blink.paste(blink, (0, 0), blink)
    keyed_blink.save("pet_sprite_blink.png")
    print("wrote pet_sprite_blink.png")


if __name__ == "__main__":
    main()
