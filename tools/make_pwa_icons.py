"""Generates the Rithavo PWA icons locally with Pillow (no network, no service).

The artwork is the site's own favicon mark (the white chevron on the indigo -> violet -> coral
gradient, see the inline <link rel="icon"> in index.html), redrawn at icon sizes. Nothing here
uses the restricted Ritanya portrait or any generated image.

Run from the repository root:  python tools/make_pwa_icons.py
Writes assets/pwa/*.png. The PNGs are committed; this script is the record of how they were made.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "pwa"
STOPS = [(0.0, (0x4B, 0x3B, 0xD4)), (0.52, (0xA2, 0x4B, 0xD6)), (1.0, (0xF2, 0x73, 0x4A))]
CHEVRON = [(22, 16), (40, 32), (22, 48)]        # favicon viewBox is 64 x 64
INK = (0xFA, 0xF9, 0xF6)
SS = 4                                           # supersampling factor


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _colour_at(t):
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            return _lerp(c0, c1, (t - t0) / (t1 - t0))
    return STOPS[-1][1]


def gradient(size):
    """Bottom-left (indigo) to top-right (coral), as in the favicon."""
    small = 128
    img = Image.new("RGB", (small, small))
    px = img.load()
    for y in range(small):
        for x in range(small):
            t = ((x / (small - 1)) + (1 - y / (small - 1))) / 2
            px[x, y] = _colour_at(t)
    return img.resize((size, size), Image.BICUBIC)


def chevron_layer(size, content_scale):
    """The white chevron, centred, occupying `content_scale` of the canvas."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    unit = size * content_scale / 64
    offset = size * (1 - content_scale) / 2
    # centre the chevron's own bounding box (x 22..40, y 16..48) on the canvas
    cx = offset + 32 * unit - 31 * unit
    pts = [(cx + x * unit, offset + y * unit) for x, y in CHEVRON]
    width = round(7 * unit)
    d.line(pts, fill=INK, width=width, joint="curve")
    r = width / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=INK)
    return layer


def render(size, *, rounded, content_scale):
    big = size * SS
    base = gradient(big).convert("RGBA")
    base.alpha_composite(chevron_layer(big, content_scale))
    if rounded:
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.25), fill=255)
        base.putalpha(mask)
    return base.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # "any" icons: rounded tile with transparent corners, chevron fills most of the tile
    render(192, rounded=True, content_scale=1.0).save(OUT / "icon-192.png", optimize=True)
    render(512, rounded=True, content_scale=1.0).save(OUT / "icon-512.png", optimize=True)
    # maskable: full bleed, chevron kept inside the central 60% safe zone
    render(512, rounded=False, content_scale=0.6).save(OUT / "icon-maskable-512.png", optimize=True)
    # iOS home screen: full bleed, opaque (iOS rounds the corners itself)
    render(180, rounded=False, content_scale=0.8).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    # small badge for notifications / shortcuts
    render(96, rounded=True, content_scale=1.0).save(OUT / "icon-96.png", optimize=True)
    for p in sorted(OUT.glob("*.png")):
        print(p.name, Image.open(p).size, p.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
