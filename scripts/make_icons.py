"""Draw the extension's toolbar icons: extension/icons/{off,on}-{16,24,32,48,128}.png.

    uv run --with pillow python scripts/make_icons.py [--preview out.png]

The icons are generated rather than hand-drawn so their geometry lives in one
reviewable place; rerun this after changing it (Pillow is only needed here,
hence `--with` rather than requirements.txt).

The mark is a CAPTURE FRAME around a TRACE — the list page's own signature, a
line of events ending where the wait begins. Its two states follow the app's
one colour rule (.claude/rules/web-ui.md rule 1: colour means the state of the
wait, everything at rest is grey):

  off  grey tile, hollow end dot — a page the extension does not capture on
       (the toolbar popup can still capture it by hand)
  on   blue tile, AMBER end dot — a page it captures on: submit an
       application here and its wait begins

Hollow against filled keeps the two apart without relying on colour. At 16 px
the trace inside the frame turns to mush, so that size draws the frame round a
single dot, on the whole-pixel grid so every stroke stays sharp; 24 px and up
draw the trace. Concepts tried and set aside (24 Sep 2026): a bare trace (a
key at 16 px, a dumbbell at 128), a rising trace (a wrench when grey), a list
of bars (any menu icon), a bare viewfinder (legible, but every screenshot
tool's).
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "extension" / "icons"
SS = 16                                        # supersampling factor

BLUE, GREY, WHITE, AMBER = "#1F53BE", "#6B7780", "#FFFFFF", "#F4B13E"
SIZES = (16, 24, 32, 48, 128)


def _draw(size, on):
    """Render one icon at `size`, supersampled, and return it downsampled."""
    big = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    if size == 16:
        _glyph16(d, on)
    else:
        _glyph32(d, size / 32, on)
    return big.resize((size, size), Image.LANCZOS)


def _px(v, k=1.0):
    return v * k * SS


def _tile(d, grid, radius, on, k=1.0):
    d.rounded_rectangle([0, 0, _px(grid, k) - 1, _px(grid, k) - 1], radius=_px(radius, k),
                        fill=BLUE if on else GREY)


def _corners(d, lo, hi, arm, t, k=1.0):
    """Four L-brackets; the arms run `arm` inward from each corner point."""
    for (x, y, sx, sy) in [(lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)]:
        xa, xb = sorted([x, x + sx * arm]); ya, yb = sorted([y, y + sy * arm])
        ht = 0 if sy > 0 else -t                       # stroke grows inward
        vt = 0 if sx > 0 else -t
        d.rectangle([_px(xa, k), _px(y + ht, k), _px(xb, k), _px(y + ht + t, k)], fill=WHITE)
        d.rectangle([_px(x + vt, k), _px(ya, k), _px(x + vt + t, k), _px(yb, k)], fill=WHITE)


def _dot(d, cx, cy, r, on, ring, k=1.0):
    box = [_px(cx - r, k), _px(cy - r, k), _px(cx + r, k), _px(cy + r, k)]
    if on:
        d.ellipse(box, fill=AMBER)
    else:
        d.ellipse(box, outline=WHITE, width=int(_px(ring, k)))


def _glyph16(d, on):
    # Whole-pixel grid: 1 px strokes on integer edges, 2 px inset, 4 px arms,
    # a 4 px gap mid-side. Compared at real size against 2 px strokes and 3 px
    # insets: every heavier variant read as four blobs round a dot (a die's
    # five), not as a frame.
    _tile(d, 16, 3.5, on)
    _corners(d, 2, 14, 4, 1)
    _dot(d, 8, 8, 2.6, on, ring=1.25)


def _glyph32(d, k, on):
    # Designed on a 32 grid and scaled by k (24 px = 0.75, 128 px = 4).
    _tile(d, 32, 7, on, k)
    _corners(d, 6, 26, 7, 3, k)
    y = 16
    d.rounded_rectangle([_px(11.2, k), _px(y - 1.3, k), _px(19.5, k), _px(y + 1.3, k)],
                        radius=_px(1.3, k), fill=WHITE)            # the trace
    r0 = 2.7
    d.ellipse([_px(11.6 - r0, k), _px(y - r0, k), _px(11.6 + r0, k), _px(y + r0, k)], fill=WHITE)
    _dot(d, 20.6, y, 3.7, on, ring=2.0, k=k)                        # where the wait begins


def write_icons():
    OUT.mkdir(parents=True, exist_ok=True)
    for on in (False, True):
        for size in SIZES:
            _draw(size, on).save(OUT / f"{'on' if on else 'off'}-{size}.png", optimize=True)
    return sorted(p.name for p in OUT.glob("*.png"))


def preview(path):
    """Every size in both states, on a light and a dark toolbar, plus the two
    toolbar sizes enlarged pixel for pixel."""
    col, bars = 150, [("#F1F3F4", "light toolbar"), ("#202124", "dark toolbar")]
    sheet = Image.new("RGB", (40 + col * 7, 40 + 2 * 180), "#FFFFFF")
    sd = ImageDraw.Draw(sheet)
    for r, on in enumerate((False, True)):
        y = 30 + r * 180
        sd.text((10, y - 22), "on: a page it captures on" if on else "off: any other page", fill="#000")
        x = 10
        for bg, label in bars:
            for size in (16, 32):
                sheet.paste(bg, (x, y, x + 64, y + 64))
                ic = _draw(size, on)
                sheet.paste(ic, (x + 32 - size // 2, y + 32 - size // 2), ic)
                sd.text((x, y + 68), f"{size} {label}", fill="#444")
                x += col // 2 + 10
        big = _draw(128, on)
        sheet.paste(big, (x + 10, y), big)
        x += 150
        for size in (16, 32):
            z = _draw(size, on).resize((128, 128), Image.NEAREST)
            sheet.paste("#F1F3F4", (x, y, x + 128, y + 128))
            sheet.paste(z, (x, y), z)
            sd.text((x, y + 132), f"{size} px, enlarged", fill="#444")
            x += 150
    sheet.save(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", metavar="PNG", help="also write a review sheet here")
    args = ap.parse_args()
    print("wrote", ", ".join(write_icons()))
    if args.preview:
        preview(args.preview)
        print("preview", args.preview)
