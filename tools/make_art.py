"""Draws the app icon and the Steam library artwork as SVG and renders them to PNG.

    python tools/make_art.py            -> packaging/art/*.svg, *.png, emu-save-manager.ico

Needs rsvg-convert (librsvg) to render, and ImageMagick's `convert` for the Windows .ico.
Original artwork (a generic memory card, a cloud and sync arrows), no console makers' marks.
The PNGs are committed, so builds don't need these tools.
"""
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "packaging" / "art"
NAVY, BLUE, SKY, CYAN, WHITE = "#0d1830", "#1f5fa8", "#3d8bfd", "#5fd3f3", "#ffffff"
FONT = "DejaVu Sans, Verdana, Arial, sans-serif"


def defs():
    return f"""
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{NAVY}"/>
    </linearGradient>
    <linearGradient id="card" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f4f8ff"/><stop offset="1" stop-color="#c9d9f2"/>
    </linearGradient>
    <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{CYAN}"/><stop offset="1" stop-color="{SKY}"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity="0.35"/>
    </filter>
  </defs>"""


def emblem(cx, cy, s):
    """The symbol, centred at (cx, cy), about 2*s wide: a save card with a cloud badge and a
    ring of two sync arrows around it."""
    # memory card: rounded rectangle with a clipped corner, a label and gold contacts
    w, h = 0.98 * s, 1.26 * s
    x, y = cx - w / 2 - 0.08 * s, cy - h / 2
    cut = 0.26 * s
    card = (f"M{x + 0.1 * s},{y} H{x + w - cut} L{x + w},{y + cut} V{y + h - 0.1 * s} "
            f"Q{x + w},{y + h} {x + w - 0.1 * s},{y + h} H{x + 0.1 * s} Q{x},{y + h} {x},{y + h - 0.1 * s} "
            f"V{y + 0.1 * s} Q{x},{y} {x + 0.1 * s},{y} Z")
    contacts = "".join(
        f'<rect x="{x + 0.14 * s + i * 0.15 * s:.1f}" y="{y + h - 0.3 * s:.1f}" width="{0.09 * s:.1f}" '
        f'height="{0.2 * s:.1f}" rx="{0.02 * s:.1f}" fill="#e7b74a"/>' for i in range(5))
    label = (f'<rect x="{x + 0.14 * s:.1f}" y="{y + 0.18 * s:.1f}" width="{w - 0.48 * s:.1f}" '
             f'height="{0.5 * s:.1f}" rx="{0.06 * s:.1f}" fill="{BLUE}" opacity="0.9"/>'
             f'<rect x="{x + 0.24 * s:.1f}" y="{y + 0.3 * s:.1f}" width="{w - 0.78 * s:.1f}" '
             f'height="{0.07 * s:.1f}" rx="{0.03 * s:.1f}" fill="{WHITE}" opacity="0.85"/>'
             f'<rect x="{x + 0.24 * s:.1f}" y="{y + 0.45 * s:.1f}" width="{w - 0.98 * s:.1f}" '
             f'height="{0.07 * s:.1f}" rx="{0.03 * s:.1f}" fill="{WHITE}" opacity="0.6"/>')
    # cloud badge at the lower right
    bx, by, r = cx + 0.42 * s, cy + 0.36 * s, 0.2 * s
    cloud = (f'<g filter="url(#shadow)"><path d="M{bx - 1.6 * r},{by + 0.7 * r} '
             f'a{0.75 * r},{0.75 * r} 0 0 1 {0.35 * r},{-1.4 * r} a{r},{r} 0 0 1 {1.75 * r},{-0.45 * r} '
             f'a{0.8 * r},{0.8 * r} 0 0 1 {1.25 * r},{1.05 * r} a{0.62 * r},{0.62 * r} 0 0 1 {-0.2 * r},{1.2 * r} Z" '
             f'fill="{WHITE}"/>'
             f'<path d="M{bx - 0.15 * r},{by - 0.25 * r} v{0.8 * r} m{-0.38 * r},{-0.36 * r} l{0.38 * r},{0.4 * r} '
             f'l{0.38 * r},{-0.4 * r}" stroke="{BLUE}" stroke-width="{0.2 * r:.1f}" fill="none" '
             f'stroke-linecap="round" stroke-linejoin="round"/></g>')
    # sync ring: two arcs with arrow heads
    R, sw = 0.98 * s, 0.13 * s
    def arc(a0, a1):
        import math
        p0 = (cx + R * math.cos(math.radians(a0)), cy + R * math.sin(math.radians(a0)))
        p1 = (cx + R * math.cos(math.radians(a1)), cy + R * math.sin(math.radians(a1)))
        tip = math.radians(a1)
        tx, ty = -math.sin(tip), math.cos(tip)                 # direction of travel (clockwise)
        nx, ny = math.cos(tip), math.sin(tip)
        hl = 0.24 * s
        head = (f"M{p1[0] + tx * hl:.1f},{p1[1] + ty * hl:.1f} L{p1[0] + nx * hl * 0.75:.1f},"
                f"{p1[1] + ny * hl * 0.75:.1f} L{p1[0] - nx * hl * 0.75:.1f},{p1[1] - ny * hl * 0.75:.1f} Z")
        return (f'<path d="M{p0[0]:.1f},{p0[1]:.1f} A{R:.1f},{R:.1f} 0 0 1 {p1[0]:.1f},{p1[1]:.1f}" '
                f'stroke="url(#ring)" stroke-width="{sw:.1f}" fill="none" stroke-linecap="round"/>'
                f'<path d="{head}" fill="url(#ring)"/>')
    ring = arc(200, 330) + arc(20, 150)
    return (f'<g filter="url(#shadow)">{ring}</g>'
            f'<g filter="url(#shadow)"><path d="{card}" fill="url(#card)"/>{label}{contacts}</g>{cloud}')


def pixels(w, h, step, opacity):
    """A faint pattern of squares, a nod to pixel art."""
    import random
    rnd = random.Random(7)
    out = []
    for yy in range(0, h, step):
        for xx in range(0, w, step):
            if rnd.random() < 0.08:
                out.append(f'<rect x="{xx}" y="{yy}" width="{step - 4}" height="{step - 4}" rx="3" '
                           f'fill="{WHITE}" opacity="{opacity * rnd.random():.3f}"/>')
    return "".join(out)


def title(x, y, size, anchor="middle", lines=("Emulator", "Save Manager")):
    t = "".join(
        f'<text x="{x}" y="{y + i * size * 1.12:.0f}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="bold" fill="{WHITE}" text-anchor="{anchor}">{line}</text>' for i, line in enumerate(lines))
    return f'<g filter="url(#shadow)">{t}</g>'


def svg(w, h, body, background=True):
    bg = f'<rect width="{w}" height="{h}" fill="url(#bg)"/>' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'{defs()}{bg}{body}</svg>')


ART = {
    # app icon: rounded square
    "icon": (512, 512, (f'<rect x="16" y="16" width="480" height="480" rx="104" fill="url(#bg)"/>'
                        f'{emblem(256, 256, 170)}'), False),
    # Steam: portrait library capsule 600x900, wide capsule 920x430, hero 1920x620, logo
    "capsule": (600, 900, pixels(600, 900, 40, 0.10) + emblem(300, 340, 190) + title(300, 700, 62), True),
    "wide": (920, 430, pixels(920, 430, 36, 0.10) + emblem(215, 215, 140)
             + title(420, 190, 60, anchor="start"), True),
    "hero": (1920, 620, pixels(1920, 620, 48, 0.12) + emblem(1560, 310, 200), True),
    "logo": (1280, 420, emblem(200, 210, 150) + title(400, 185, 92, anchor="start"), False),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (w, h, body, background) in ART.items():
        (OUT / f"{name}.svg").write_text(svg(w, h, body, background), encoding="utf-8")
    if not shutil.which("rsvg-convert"):
        print("rsvg-convert not found: wrote the SVGs only")
        return
    for name in ART:
        subprocess.run(["rsvg-convert", "-o", str(OUT / f"{name}.png"), str(OUT / f"{name}.svg")], check=True)
    for size in (16, 32, 48, 64, 128, 256):
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o",
                        str(OUT / f"icon-{size}.png"), str(OUT / "icon.svg")], check=True)
    if shutil.which("convert"):
        subprocess.run(["convert"] + [str(OUT / f"icon-{s}.png") for s in (16, 32, 48, 64, 128, 256)]
                       + [str(OUT / "emu-save-manager.ico")], check=True)
    print("Wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
