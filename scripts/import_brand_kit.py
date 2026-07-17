"""Import a Skeptic brand kit into the app.

Usage: uv run --with pillow python scripts/import_brand_kit.py <kit-dir>

Exists because the v2 kit import (BUILD-LOG 2026-07-16) tripped on two
kit-source defects that are invisible until you measure:

- the parametric tile renderer (kit.py favicon_svg()) hardcodes the
  glyph's y-offset, so every raster favicon ships with the S ~10% above
  vertical center;
- a stroke inserted into the draw animation without re-cascading the
  letters after it breaks the strictly left-to-right stagger.

This script makes the import one command: it copies the SVG masters and
og-image, re-cascades draw delays when the kit ships them out of order,
re-centers the favicon tiles, rebuilds app/favicon.ico, and verifies
every output by measurement. Running it against the v2 kit reproduces
the committed assets byte-identically.
"""

from __future__ import annotations

import math
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "frontend" / "public"
BRAND = PUBLIC / "brand"
SPLASH = REPO / "frontend" / "components" / "boot-splash.tsx"

BRAND_HEXES = {"#101014", "#F4F4F5", "#8E8E93", "#FFFFFF"}
STROKE_S = 0.75  # every draw stroke animates for 0.75s (the SVGs' `dw` keyframes)
MIN_GAP_S = 0.01  # the kit's own letter-boundary rhythm between stroke starts

# app filenames -> kit-relative sources. Copied verbatim (draw SVGs may be
# re-cascaded first); tiles are re-centered, favicon.ico rebuilt from them.
SVG_MASTERS = {
    "s-mark-black.svg": "svg/s-mark-black.svg",
    "s-mark-gray.svg": "svg/s-mark-gray.svg",
    "s-mark-white.svg": "svg/s-mark-white.svg",
    "wordmark-black.svg": "svg/wordmark-black.svg",
    "wordmark-gray.svg": "svg/wordmark-gray.svg",
    "wordmark-white.svg": "svg/wordmark-white.svg",
    "wordmark-white-slash-gray.svg": "svg/wordmark-white-slash-gray.svg",
}
DRAW_MASTERS = {
    "skeptic-draw-black.svg": "animation/skeptic-draw-black.svg",
    "skeptic-draw-white.svg": "animation/skeptic-draw-white.svg",
}
TILE_SIZES = (32, 180, 512)

_DELAY = re.compile(r"animation-delay:(\d+\.\d+)s")


def fail(msg: str) -> None:
    sys.exit(f"REFUSED: {msg}")


def check_svg_tokens(name: str, text: str) -> None:
    hexes = {h.upper() for h in re.findall(r"#[0-9a-fA-F]{6}", text)}
    if not hexes <= BRAND_HEXES:
        fail(f"{name} uses non-brand colors {sorted(hexes - BRAND_HEXES)}")
    if "<text" in text or "font-" in text:
        fail(f"{name} embeds text/fonts; marks are geometry only (typography directive)")


def splash_budget_s() -> float:
    """ANIMATION_MS from boot-splash.tsx — the timing contract the draw must fit."""
    m = re.search(r"const ANIMATION_MS = (\d+);", SPLASH.read_text())
    if not m:
        fail(f"could not find ANIMATION_MS in {SPLASH}")
    return int(m.group(1)) / 1000


def cascade(text: str, name: str) -> str:
    """Re-time draw delays so strokes start strictly left to right.

    Walks paths in document order carrying a cumulative shift: any stroke
    starting earlier than (previous start + MIN_GAP_S) is pushed to exactly
    that, and everything after it shifts by the same amount — which is how
    the kit's own boundary rhythm reads. Applied to the v2 kit this yields
    P/T/slash/C at 0.45/0.51/0.58/0.64/0.71/0.84, the committed fix.
    """
    delays = [round(float(d) * 100) for d in _DELAY.findall(text)]  # hundredths
    if not delays:
        fail(f"{name} has no animation-delay tokens")
    gap = round(MIN_GAP_S * 100)
    shift, prev, fixed = 0, None, []
    for d in delays:
        d += shift
        if prev is not None and d < prev + gap:
            shift += prev + gap - d
            d = prev + gap
        fixed.append(d)
        prev = d
    moved = sum(a != b for a, b in zip(delays, fixed, strict=True))
    if moved:
        print(f"  {name}: re-cascaded {moved} delays")
    it = iter(fixed)
    return _DELAY.sub(lambda _: f"animation-delay:{next(it) / 100:.2f}s", text)


def glyph_bbox(im: Image.Image) -> tuple[int, int, int, int]:
    px = im.load()
    w, h = im.size
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a >= 128 and (r + g + b) / 3 > 220:
                xs.append(x)
                ys.append(y)
    if not xs:
        fail("no white glyph found in tile")
    return min(xs), min(ys), max(xs), max(ys)


def recenter(src: Path) -> Image.Image:
    """Shift the white S so its bbox center sits on the tile's vertical center.

    Half-pixel ties round the glyph UP, never down (owner call 2026-07-16:
    the low half-pixel read as off-center in the tab).
    """
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    x0, y0, x1, y1 = glyph_bbox(im)
    dy = math.ceil(h / 2 - (y0 + y1 + 1) / 2 - 0.5)
    pad = 3  # carry the antialiased halo; it blends into the same solid tile color
    box = (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + 1 + pad), min(h, y1 + 1 + pad))
    region = im.crop(box)
    tile = im.getpixel((w // 2, 2))
    im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), tile), box[:2])
    im.paste(region, (box[0], box[1] + dy))
    return im


def verify_tile(label: str, im: Image.Image, size: int) -> None:
    if im.size != (size, size):
        fail(f"{label} is {im.size}, expected {size}x{size}")
    x0, y0, x1, y1 = glyph_bbox(im)
    cx = (x0 + x1 + 1) / 2 / size
    cy = (y0 + y1 + 1) / 2 / size
    # half a pixel is the residual an integer shift can leave
    tol = 0.5 / size + 1e-9
    if abs(cx - 0.5) > tol or abs(cy - 0.5) > tol:
        fail(f"{label} glyph center ({cx:.3f},{cy:.3f}) off by more than half a pixel")
    r, g, b, a = im.getpixel((size // 2, 1 if size <= 16 else 2))
    if not (a == 255 and max(r, g, b) < 40):
        fail(f"{label} background {(r, g, b, a)} is not the ink tile")
    print(f"  {label}: center ({cx:.3f},{cy:.3f}) on ink — ok")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[2])
    kit = Path(sys.argv[1]).expanduser()
    if not kit.is_dir():
        fail(f"{kit} is not a directory")

    print("SVG masters:")
    for name, rel in SVG_MASTERS.items():
        text = (kit / rel).read_text()
        check_svg_tokens(name, text)
        (BRAND / name).write_text(text)
        print(f"  {name}: copied")

    print("Draw animations:")
    budget = splash_budget_s()
    for name, rel in DRAW_MASTERS.items():
        text = cascade((kit / rel).read_text(), name)
        check_svg_tokens(name, text)
        last = max(float(d) for d in _DELAY.findall(text))
        if last + STROKE_S > budget:
            fail(
                f"{name} last stroke ends at {last + STROKE_S:.2f}s, over the "
                f"{budget:.2f}s ANIMATION_MS budget — raise it in {SPLASH.name} first"
            )
        (BRAND / name).write_text(text)
        print(f"  {name}: last stroke {last:.2f}s + {STROKE_S}s ≤ {budget:.2f}s — ok")

    print("Favicon tiles (owner-picked ink-black; kit renders ride high, re-centered):")
    tiles = {}
    for size in TILE_SIZES:
        tiles[size] = recenter(kit / "png" / "favicon" / f"favicon-dark-tile-{size}.png")
        tiles[size].save(PUBLIC / f"favicon-dark-tile-{size}.png")
        verify_tile(f"favicon-dark-tile-{size}.png", tiles[size], size)

    print("favicon.ico (16/48/64 Lanczos from the fixed 512, native 32 frame):")
    frames = {s: tiles[512].resize((s, s), Image.LANCZOS) for s in (16, 48, 64)}
    frames[32] = tiles[32]
    ico = REPO / "frontend" / "app" / "favicon.ico"
    frames[64].save(
        ico,
        format="ICO",
        append_images=[frames[48], frames[32], frames[16]],
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )
    for s in (16, 32, 48, 64):
        frame = Image.open(ico)
        frame.size = (s, s)
        verify_tile(f"favicon.ico {s}px frame", frame.convert("RGBA"), s)

    og = Image.open(kit / "social" / "og-image-1200x630.png")
    if og.size != (1200, 630):
        fail(f"og-image is {og.size}, expected 1200x630")
    shutil.copyfile(kit / "social" / "og-image-1200x630.png", PUBLIC / "og-image-1200x630.png")
    print("og-image-1200x630.png: copied (1200x630 — ok)")


if __name__ == "__main__":
    main()
