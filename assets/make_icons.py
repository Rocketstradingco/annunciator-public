"""Render Annunciator icons: a four-node grid with the lead node live.

Drawn with Pillow rather than rasterising the SVGs because ImageMagick's
built-in SVG renderer drops stroked shapes. Supersampled 4x for clean edges.
Run from the project root: python3 assets/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

BG = (7, 17, 22, 255)
INK = (82, 218, 250, 255)
LIT = (60, 243, 170, 255)
SS = 4

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "android/app/src/main/res"


def grid(draw, size, span, stroke):
    """Draw the grid centred, `span` = fraction of the canvas it covers."""
    total = size * span
    gap = total * 0.12
    cell = (total - gap) / 2
    origin = (size - total) / 2
    radius = cell * 0.11
    for row in range(2):
        for col in range(2):
            x = origin + col * (cell + gap)
            y = origin + row * (cell + gap)
            box = [x, y, x + cell, y + cell]
            if row == 0 and col == 0:
                draw.rounded_rectangle(box, radius, fill=LIT)
            else:
                inset = stroke / 2
                draw.rounded_rectangle([box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset],
                                       radius, outline=INK, width=round(stroke))


def render(size, span, background=True, corner=0.0, stroke_frac=0.03):
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if background:
        if corner:
            draw.rounded_rectangle([0, 0, big - 1, big - 1], big * corner, fill=BG)
        else:
            draw.rectangle([0, 0, big, big], fill=BG)
    grid(draw, big, span, big * stroke_frac)
    return img.resize((size, size), Image.LANCZOS)


def main():
    web = ROOT / "web/icons"
    render(192, 0.46, corner=0.19).save(web / "icon-192.png")
    render(512, 0.46, corner=0.19).save(web / "icon-512.png")

    if not RES.exists():
        print("no android project yet; web icons only")
        return

    # Legacy launcher icons (pre-Android 8) and adaptive layers (8+).
    legacy = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    adaptive = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
    for density, px in legacy.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        render(px, 0.5, corner=0.16).save(folder / "ic_launcher.png")
        render(px, 0.5, corner=0.5).save(folder / "ic_launcher_round.png")
    for density, px in adaptive.items():
        folder = RES / f"mipmap-{density}"
        # Adaptive foreground: the grid must sit inside the central 66% safe zone.
        render(px, 0.36, background=False).save(folder / "ic_launcher_foreground.png")

    # Replace Capacitor's stock splash art (its own logo) in every drawable
    # folder with the panel background and a small centred grid.
    for splash in RES.glob("drawable*/splash.png"):
        with Image.open(splash) as old:
            w, h = old.size
        big = Image.new("RGBA", (w * 2, h * 2), BG)
        side = min(w, h) * 2
        mark = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        grid(ImageDraw.Draw(mark), side, 0.16, side * 0.006)
        big.alpha_composite(mark, ((w * 2 - side) // 2, (h * 2 - side) // 2))
        big.resize((w, h), Image.LANCZOS).save(splash)

    values = RES / "values/ic_launcher_background.xml"
    values.write_text('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                      '    <color name="ic_launcher_background">#071116</color>\n</resources>\n')
    print("android icons written")


if __name__ == "__main__":
    main()
