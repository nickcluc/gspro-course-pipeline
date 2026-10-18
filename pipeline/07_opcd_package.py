"""
07_opcd_package.py — Everything the OPCD toolset needs to build the course for GSPro.

OPCD (Open Platform Course Design) builds a GSPro course in four steps:
  1. Clender (OPCD's mesher) takes a colour-coded course SVG and returns a .blend
  2. OPCD Tools in Blender conform those meshes to the terrain; export FBX
  3. Unity (OPCD V4 BaseProject) takes the FBX, the terrain heightmap and textures
  4. GreenKeeper sets tees, pins, hazards and out of bounds

This stage makes the inputs for all four, framed to one square so they line up:

  output/opcd/<slug>_heightmap_<N>.raw     16-bit little-endian RAW, N x N (N = 513, 1025, 2049 or 4097),
                                           1 m per sample, rows south to north (Unity: no flip)
  output/opcd/<slug>_clender.svg           course shapes, OPCD colours, 1 mm = 1 m, framed to the heightmap
  output/opcd/<slug>_overlay.jpg           aerial photo, 1 pixel per metre, same square, north up
  output/opcd/unity_terrain.json           numbers to type into Unity's terrain import
  output/opcd/greenkeeper.json / .csv      per hole: par, tees, pins, penalty areas, in Unity coordinates
  output/opcd/README.md                    the steps, with this course's numbers filled in

Unity coordinates: x = metres east of the square's west edge, z = metres north of its
south edge, y = metres above the heightmap's base height.

Usage:
    python pipeline/07_opcd_package.py --course tedesco
    python pipeline/07_opcd_package.py --course tedesco --margin 150

Inputs:
    courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif   (Stage 01)
    courses/<slug>/output/layout/holes.json, surfaces.geojson      (Stage 05)
    courses/<slug>/data/processed/textures/<slug>_imagery.tif      (Stage 03, optional)
"""

import argparse
import csv
import importlib
import json
import math
from pathlib import Path

import numpy as np

from utils import REPO_ROOT, load_course_config

layout = importlib.import_module("05_layout")

SIZES = (513, 1025, 2049, 4097)   # Unity terrain heightmap sizes (2^n + 1)
MARGIN_M = 100.0                  # ground kept round the course on every side


def get_paths(slug: str) -> dict:
    base = REPO_ROOT / "courses" / slug
    return {
        "terrain": base / "data" / "processed" / "heightmaps" / f"{slug}_terrain.tif",
        "imagery": base / "data" / "processed" / "textures" / f"{slug}_imagery.tif",
        "holes": base / "output" / "layout" / "holes.json",
        "surfaces": base / "output" / "layout" / "surfaces.geojson",
        "out": base / "output" / "opcd",
    }


# ---------------------------------------------------------------------------
# The square everything is framed to
# ---------------------------------------------------------------------------

def choose_square(surfaces: list[dict], margin_m: float = MARGIN_M) -> dict:
    """
    The smallest Unity terrain size that holds every shape plus the margin, centred
    on the course. Corners are whole metres so heightmap samples land on whole metres.
    """
    es = [e for s in surfaces for e, _ in s["en"]]
    ns = [n for s in surfaces for _, n in s["en"]]
    need = max(max(es) - min(es), max(ns) - min(ns)) + 2 * margin_m
    n = next((k for k in SIZES if k - 1 >= need), None)
    if n is None:
        raise ValueError(f"The course needs {need:.0f} m of terrain; the largest Unity terrain is {SIZES[-1] - 1} m.")
    size = n - 1
    cx, cy = (max(es) + min(es)) / 2, (max(ns) + min(ns)) / 2
    west, south = math.floor(cx - size / 2), math.floor(cy - size / 2)
    return {"samples": n, "size_m": float(size), "west": float(west), "south": float(south),
            "east": float(west + size), "north": float(south + size)}


def to_unity(sq: dict, e: float, n: float) -> tuple[float, float]:
    return e - sq["west"], n - sq["south"]


# ---------------------------------------------------------------------------
# Heightmap
# ---------------------------------------------------------------------------

def sample_terrain(tif: Path, sq: dict, epsg: int) -> np.ndarray:
    """Elevation (m) at every whole metre of the square, rows north to south, as float32."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    n = sq["samples"]
    # pixel centres on whole metres: sample (r, c) sits at (west + c, north - r)
    dst_tf = from_origin(sq["west"] - 0.5, sq["north"] + 0.5, 1.0, 1.0)
    out = np.full((n, n), np.nan, np.float32)
    with rasterio.open(tif) as ds:
        reproject(source=rasterio.band(ds, 1), destination=out, src_transform=ds.transform, src_crs=ds.crs,
                  src_nodata=ds.nodata, dst_transform=dst_tf, dst_crs=f"EPSG:{epsg}", dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    return out


def fill_gaps(z: np.ndarray) -> tuple[np.ndarray, float]:
    """Fills cells the terrain doesn't cover with the nearest covered height. Returns the share filled."""
    from scipy import ndimage as ndi
    bad = ~np.isfinite(z)
    share = float(bad.mean())
    if bad.all():
        raise ValueError("The terrain doesn't cover any of the course square.")
    if share:
        idx = ndi.distance_transform_edt(bad, return_distances=False, return_indices=True)
        z = z[tuple(idx)]
    return z, share


def write_raw(z: np.ndarray, path: Path) -> dict:
    """
    16-bit little-endian RAW for Unity. Rows go south to north (Unity reads the first
    row as the terrain's south edge, so no "Flip Vertically").
    """
    base = math.floor(float(z.min())) - 1.0
    height = math.ceil(float(z.max()) - base) + 1.0
    q = np.clip(np.round((z - base) / height * 65535), 0, 65535).astype("<u2")
    q[::-1].tofile(path)  # z is north-first; write south-first
    return {"base_m": base, "height_m": height, "min_m": round(float(z.min()), 2), "max_m": round(float(z.max()), 2)}


def height_at(z: np.ndarray, sq: dict, e: float, n: float) -> float:
    """Bilinear height (m) at a point, from the north-first grid."""
    x, y = e - sq["west"], sq["north"] - n
    c0, r0 = int(np.clip(math.floor(x), 0, z.shape[1] - 2)), int(np.clip(math.floor(y), 0, z.shape[0] - 2))
    fx, fy = min(max(x - c0, 0.0), 1.0), min(max(y - r0, 0.0), 1.0)
    top = z[r0, c0] * (1 - fx) + z[r0, c0 + 1] * fx
    bot = z[r0 + 1, c0] * (1 - fx) + z[r0 + 1, c0 + 1] * fx
    return float(top * (1 - fy) + bot * fy)


# ---------------------------------------------------------------------------
# SVG, overlay, GreenKeeper sheet
# ---------------------------------------------------------------------------

def load_surfaces(geojson: Path, project) -> list[dict]:
    out = []
    for i, f in enumerate(json.loads(geojson.read_text())["features"]):
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0][:-1]
        out.append({"hole": p.get("hole"), "surface": p.get("surface"), "index": i,
                    "en": [project(x, y) for x, y in ring]})
    return out


def write_overlay(tif: Path, sq: dict, epsg: int, path: Path) -> bool:
    """The aerial photo resampled to 1 pixel per metre over the square, north up."""
    if not tif.exists():
        return False
    import rasterio
    from PIL import Image
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    size = int(sq["size_m"])
    rgb = np.zeros((3, size, size), np.uint8)
    with rasterio.open(tif) as ds:
        for b in range(3):
            reproject(source=rasterio.band(ds, b + 1), destination=rgb[b], src_transform=ds.transform, src_crs=ds.crs,
                      dst_transform=from_origin(sq["west"], sq["north"], 1.0, 1.0), dst_crs=f"EPSG:{epsg}",
                      resampling=Resampling.bilinear)
    Image.fromarray(np.moveaxis(rgb, 0, -1)).save(path, quality=90)
    return True


def greenkeeper_sheet(holes: list[dict], z: np.ndarray, sq: dict, base: float) -> list[dict]:
    """Per hole: par, tees, pins, green centre and penalty areas in Unity coordinates (metres)."""
    def spot(p):
        x, zz = to_unity(sq, p["easting"], p["northing"])
        return {"x": round(x, 2), "y": round(height_at(z, sq, p["easting"], p["northing"]) - base, 2), "z": round(zz, 2)}

    rows = []
    for h in holes:
        row = {"hole": h["hole"], "par": h.get("par"), "stroke_index": h.get("stroke_index"),
               "green": spot(h["green"]) if h.get("green") else None, "tees": [], "pins": [], "penalty_areas": []}
        for t in h.get("tees") or []:
            if t.get("position"):
                row["tees"].append(dict(name=t.get("name"), yards=t.get("yards"), **spot(t["position"])))
        for i, p in enumerate(h.get("pins") or [], 1):
            row["pins"].append(dict(pin=i, rounds=p.get("rounds"), **spot(p)))
        for a in h.get("penalty_areas") or []:
            row["penalty_areas"].append({"stakes": a.get("stakes"), "note": a.get("note"),
                                         "points": [[round(v, 2) for v in to_unity(sq, q["easting"], q["northing"])]
                                                    for q in a.get("points") or []]})
        rows.append(row)
    return rows


def write_sheet_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hole", "par", "kind", "name", "yards", "x", "y", "z"])
        for r in rows:
            for t in r["tees"]:
                w.writerow([r["hole"], r["par"], "tee", t["name"], t["yards"], t["x"], t["y"], t["z"]])
            for p in r["pins"]:
                w.writerow([r["hole"], r["par"], "pin", f"pin {p['pin']}", "", p["x"], p["y"], p["z"]])


README = """# {name}: OPCD build files

Made by Stage 07. Everything is framed to one {size:.0f} m square:
west edge {west:.0f} E, south edge {south:.0f} N (EPSG:{epsg}).

## 1. Clender
Submit `{slug}_clender.svg` to Clender. It's 1 mm = 1 m, north up, {size:.0f} x {size:.0f} mm,
one layer per surface in the OPCD colours. Clender returns a .blend.

## 2. Blender (OPCD Tools)
Open the .blend, import the terrain, and conform the meshes with OPCD Tools.
Use `{slug}_overlay.jpg` (1 pixel = 1 m, same square) to check the shapes line up. Export FBX.

## 3. Unity (OPCD V4 BaseProject)
Terrain: import `{raw}` as RAW.
- Depth 16 bit, byte order Windows, width and height {samples}, flip vertically OFF
- Terrain size: width {size:.0f}, length {size:.0f}, height {height:.0f}
- Terrain position: (0, 0, 0). Real elevation = Unity y + {base:.0f} m.
Then import the FBX at (0, 0, 0) and set the mesh materials.

## 4. GreenKeeper
`greenkeeper.csv` lists every tee and pin as Unity x, y, z (metres).
`greenkeeper.json` adds green centres and penalty areas (outline points, with stakes colour).
{gaps}
"""


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def build_package(slug: str, margin_m: float = MARGIN_M) -> dict:
    cfg = load_course_config(slug)
    paths = get_paths(slug)
    for key, stage in (("terrain", "01_lidar.py"), ("holes", "05_layout.py"), ("surfaces", "05_layout.py")):
        if not paths[key].exists():
            raise FileNotFoundError(f"Missing {paths[key]}. Run {stage} first.")
    holes_doc = json.loads(paths["holes"].read_text())
    epsg = int(holes_doc.get("epsg") or 32619)
    project = layout.make_projector(epsg)
    surfaces = load_surfaces(paths["surfaces"], project)

    sq = choose_square(surfaces, margin_m)
    out = paths["out"]
    out.mkdir(parents=True, exist_ok=True)

    z, filled = fill_gaps(sample_terrain(paths["terrain"], sq, epsg))
    raw = out / f"{slug}_heightmap_{sq['samples']}.raw"
    hm = write_raw(z, raw)

    frame = {"xmin": sq["west"], "ymin": sq["south"], "xmax": sq["east"], "ymax": sq["north"]}
    layout.write_svg(surfaces, frame, out / f"{slug}_clender.svg", f"{cfg.get('name')} for Clender, EPSG:{epsg}")
    has_overlay = write_overlay(paths["imagery"], sq, epsg, out / f"{slug}_overlay.jpg")

    rows = greenkeeper_sheet(holes_doc["holes"], z, sq, hm["base_m"])
    (out / "greenkeeper.json").write_text(json.dumps({"units": "m", "axes": "Unity x = east, z = north, y = up",
                                                      "holes": rows}, indent=2))
    write_sheet_csv(rows, out / "greenkeeper.csv")

    unity = {"heightmap": raw.name, "resolution": sq["samples"], "depth_bits": 16, "byte_order": "Windows",
             "flip_vertically": False, "terrain_width_m": sq["size_m"], "terrain_length_m": sq["size_m"],
             "terrain_height_m": hm["height_m"], "base_elevation_m": hm["base_m"],
             "square": {k: sq[k] for k in ("west", "south", "east", "north")}, "epsg": epsg,
             "elevation_range_m": [hm["min_m"], hm["max_m"]], "share_filled_outside_terrain": round(filled, 4)}
    (out / "unity_terrain.json").write_text(json.dumps(unity, indent=2))

    gaps = (f"\nNote: {filled:.0%} of the square is outside the terrain and was filled with the nearest height."
            if filled > 0.001 else "")
    (out / "README.md").write_text(README.format(
        name=cfg.get("name", slug), slug=slug, size=sq["size_m"], west=sq["west"], south=sq["south"], epsg=epsg,
        raw=raw.name, samples=sq["samples"], height=hm["height_m"], base=hm["base_m"], gaps=gaps))
    return {"square": sq, "heightmap": hm, "overlay": has_overlay, "holes": len(rows),
            "tees": sum(len(r["tees"]) for r in rows), "pins": sum(len(r["pins"]) for r in rows),
            "share_filled": filled, "out": out}


def main():
    parser = argparse.ArgumentParser(description="Build the OPCD input files (Clender SVG, Unity heightmap, GreenKeeper sheet)")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--margin", type=float, default=MARGIN_M, help="metres of ground kept round the course")
    args = parser.parse_args()
    print(f"OPCD package for {args.course}")
    r = build_package(args.course, args.margin)
    sq, hm = r["square"], r["heightmap"]
    print(f"  Square: {sq['size_m']:.0f} m, heightmap {sq['samples']} x {sq['samples']}")
    print(f"  Heights {hm['min_m']}–{hm['max_m']} m; Unity height {hm['height_m']:.0f} m from base {hm['base_m']:.0f} m")
    print(f"  {r['holes']} holes, {r['tees']} tees, {r['pins']} pins in greenkeeper.csv")
    if r["share_filled"] > 0.001:
        print(f"  Warning: {r['share_filled']:.0%} of the square is outside the terrain (filled with nearest height)")
    if not r["overlay"]:
        print("  No aerial photo found: overlay skipped (run 03_imagery.py)")
    print(f"  Files in {r['out']}")


if __name__ == "__main__":
    main()
