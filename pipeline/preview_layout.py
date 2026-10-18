"""
preview_layout.py — Quick top-down preview of the course layout, no Blender needed

Draws every hole from Stage 05's output in the course colours: tee at the bottom,
green at the top, with every shape near the hole (trees Arccos files under a
neighbouring hole included), tees as white dots and pin spots as red dots.

Usage:
    python pipeline/preview_layout.py --course tedesco
    python pipeline/preview_layout.py --course tedesco --holes 1,9,16

Output:
    courses/<slug>/output/preview/holes.jpg         all holes on one sheet
    courses/<slug>/output/preview/hole_XX.jpg       one image per hole
"""

import argparse
import json
import math
from pathlib import Path

from utils import REPO_ROOT, load_course_config

COLOURS = {
    "rough": "#278438", "native": "#8A7F3D", "fairway": "#43E561", "tee": "#A0E5B8", "trees": "#164B20",
    "green": "#BCE5A4", "bunker": "#E5E5AA", "water": "#3050D0", "penalty_area": "#D9534F",
}
ORDER = ("native", "fairway", "tee", "trees", "green", "bunker", "water", "penalty_area")
TILE_W, TILE_H, SS = 450, 800, 2  # tile size in pixels, drawn at SS times and shrunk for smooth edges


def render_hole(hole: dict, surfaces: list[dict], name: str):
    from PIL import Image, ImageDraw, ImageFont
    tees = [t["position"] for t in hole.get("tees") or [] if t.get("position")]
    g = hole.get("green")
    if not tees or not g:
        return None
    gx, gz = g["x"], g["z"]
    back = max(tees, key=lambda p: math.dist((p["x"], p["z"]), (gx, gz)))
    bx, bz = back["x"], back["z"]
    length = math.dist((bx, bz), (gx, gz)) or 1.0
    ux, uz = (gx - bx) / length, (gz - bz) / length  # up the image: tee to green
    wx, wz = uz, -ux                                  # right of the image
    mx, mz = (bx + gx) / 2, (bz + gz) / 2
    W, H = TILE_W * SS, TILE_H * SS
    scale = H / (length * 1.25 + 60)

    def to_px(x, z):
        dx, dz = x - mx, z - mz
        return (W / 2 + (dx * wx + dz * wz) * scale, H / 2 - (dx * ux + dz * uz) * scale)

    reach = math.hypot(W, H) / scale / 2
    im = Image.new("RGB", (W, H), COLOURS["rough"])
    d = ImageDraw.Draw(im, "RGBA")
    for kind in ORDER:
        for s in surfaces:
            if s["surface"] != kind or len(s["points"]) < 3:
                continue
            if min(math.dist(p, (mx, mz)) for p in s["points"]) > reach:
                continue
            fill = COLOURS[kind] + ("80" if kind == "penalty_area" else "")
            d.polygon([to_px(*p) for p in s["points"]], fill=fill)
    r = 2.0 * scale
    for p in tees:
        x, y = to_px(p["x"], p["z"])
        d.ellipse((x - r, y - r, x + r, y + r), fill="white", outline="black")
    for p in hole.get("pins") or []:
        x, y = to_px(p["x"], p["z"])
        d.ellipse((x - r * 0.8, y - r * 0.8, x + r * 0.8, y + r * 0.8), fill="#E03C31")
    im = im.resize((TILE_W, TILE_H), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=24)
    except TypeError:
        font = ImageFont.load_default()
    yd = next((t.get("yards") for t in hole["tees"] if t.get("name") == "Blue"), None)
    label = f"{name} {hole['hole']} · Par {hole.get('par')}" + (f" · {round(yd)} yd" if yd else "")
    d.rectangle((0, 0, TILE_W, 36), fill="black")
    d.text((10, 6), label, fill="white", font=font)
    return im


def main():
    parser = argparse.ArgumentParser(description="Top-down preview of every hole from the Stage 05 layout")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--holes", help="comma-separated hole numbers (default: all)")
    args = parser.parse_args()
    load_course_config(args.course)
    base = REPO_ROOT / "courses" / args.course / "output"
    holes = json.loads((base / "layout" / "holes.json").read_text())["holes"]
    surfaces = json.loads((base / "layout" / "surfaces_local.json").read_text())["surfaces"]
    want = {int(h) for h in args.holes.split(",")} if args.holes else None
    out = base / "preview"
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for h in holes:
        if want and h["hole"] not in want:
            continue
        im = render_hole(h, surfaces, "Hole")
        if im is None:
            continue
        im.save(out / f"hole_{h['hole']:02d}.jpg", quality=88)
        tiles.append(im)
    if not tiles:
        raise SystemExit("No holes drawn: run 05_layout.py first.")
    from PIL import Image
    cols = min(6, len(tiles))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * (TILE_W + 6), rows * (TILE_H + 6)), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * (TILE_W + 6), (i // cols) * (TILE_H + 6)))
    sheet.save(out / "holes.jpg", quality=88)
    print(f"Preview: {len(tiles)} holes -> {out / 'holes.jpg'}")


if __name__ == "__main__":
    main()
