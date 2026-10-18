"""
07_opcd_package.py — Everything the OPCD toolset needs to build the course for GSPro.

OPCD (Open Platform Course Design) builds a GSPro course through its web app (app.opcdgolf.com):
  1. Terrain / LiDAR makes the Unity terrain for a square you pick (set it in course.json "opcd.terrain")
  2. SVG Jobs (Clender, OPCD's mesher) takes a colour-coded course SVG and returns a .blend
  3. OPCD Tools in Blender 4.2 conform those meshes to the terrain; export FBX
  4. Unity 2018.2.8f1 (OPCD V4 BaseProject) takes the terrain package and the FBX; GreenKeeper sets
     tees, pins, hazards and out of bounds; Build Course (Windows only) makes the GSPro course

This stage makes the inputs for 2 to 4, framed to OPCD's terrain square so they line up:

  output/opcd/<slug>_clender.svg           course shapes as an Inkscape SVG, OPCD colours, 1 mm = 1 m; each
                                           colour's shapes merged, corner contacts joined (Clender gaps)
  output/opcd/clender_check.json           what was merged, closed, repaired or left out of the SVG
  output/opcd/<slug>_terrain_opcd.obj      the square's terrain in Clender's frame, for conforming (2 m grid)
  output/opcd/<slug>_overlay.jpg           aerial photo, 1 pixel per metre, same square, north up
  output/opcd/greenkeeper.json / .csv      per hole: par, tees, pins, penalty areas, in Unity coordinates
  output/opcd/README.md                    the steps, with this course's numbers filled in
Without "opcd.terrain" (fallback) it picks its own square and also writes a Unity RAW heightmap
(<slug>_heightmap_<N>.raw, N = 513, 1025, 2049 or 4097) and unity_terrain.json.

Frames, from the square's south-west corner: Unity x = metres east, z = metres north, y = up
(real elevation with an OPCD terrain, else metres above the RAW's base). Clender's .blend is
Unity turned 180 degrees: X = -(metres east), Y = -(metres north).

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
OBJ_STEP_M = 2                    # terrain OBJ vertex spacing (1025 x 1025 vertices for a 2048 m square)
MIN_AREA_M2 = 1.0                 # Clender shapes smaller than this are dropped
CLOSE_M = 0.5                     # same-colour shapes closer than 2 x this are joined (Clender gaps)

# Fill colours Clender is known to read: the golf entries of osm2svg4opcd's styles.json
# (github.com/csites/osm2svg4opcd), which targets Clender. The OPCD V4 palette itself comes
# with OPCD registration; add to this if it has more colours. Surfaces whose colour isn't
# here are left out of the Clender SVG (they stay rough) rather than risk a Clender colour error.
CLENDER_PALETTE = {
    "#278438": "leisure.golf_course (rough)",
    "#43E561": "golf.fairway",
    "#36B74D": "golf.semi-rough",
    "#A0E5B8": "golf.tee",
    "#BCE5A4": "golf.green",
    "#E5E5AA": "golf.bunker",
    "#0000C0": "golf.water_hazard",
    "#164B20": "natural.wood",
}


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


def square_from_center(lat: float, lon: float, size_m: float, epsg: int) -> dict:
    """The square of a terrain made elsewhere (OPCD web app's lidar tool): north up, centred on lat/lon."""
    from pyproj import Transformer
    e, n = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    h = size_m / 2
    return {"samples": int(round(size_m)) + 1, "size_m": float(size_m), "west": e - h, "south": n - h,
            "east": e + h, "north": n + h, "center": {"lat": lat, "lon": lon}}


def course_margins(surfaces: list[dict], sq: dict) -> dict:
    """Metres of ground between the course shapes and each edge of the square (negative = shapes outside)."""
    es = [e for s in surfaces for e, _ in s["en"]]
    ns = [n for s in surfaces for _, n in s["en"]]
    return {"west": round(min(es) - sq["west"]), "east": round(sq["east"] - max(es)),
            "south": round(min(ns) - sq["south"]), "north": round(sq["north"] - max(ns))}


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


def clender_shapes(surfaces: list[dict], sq: dict, close_m: float = CLOSE_M) -> tuple[list[dict], dict]:
    """
    Gets the shapes ready for Clender, which wants closed, non-overlapping shapes per colour:
    repairs self-crossing outlines, merges every shape of one colour (Arccos files many shapes
    under two holes, and native grass shares the wood colour), closes gaps narrower than
    2 x close_m (shapes touching at a corner leave a gap in Clender's mesh), clips to the
    square and drops slivers. Colours not in CLENDER_PALETTE are skipped. Each merged shape
    goes in the layer of the highest-stacked surface of its colour. Returns the shapes and a
    report per colour, keyed by that surface.
    """
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    from shapely.validation import make_valid

    square = box(sq["west"], sq["south"], sq["east"], sq["north"])
    by_fill: dict[str, list] = {}
    for s in surfaces:
        if s["surface"] in layout.OPCD and len(s["en"]) >= 3:
            by_fill.setdefault(layout.OPCD[s["surface"]][1].upper(), []).append(s)

    out, report = [], {"skipped_colours": {}, "surfaces": {}}
    for fill, items in by_fill.items():
        kinds = sorted({s["surface"] for s in items}, key=lambda k: layout.OPCD[k][2])
        if fill not in CLENDER_PALETTE:
            for k in kinds:
                report["skipped_colours"][k] = {"fill": fill, "shapes": sum(s["surface"] == k for s in items)}
            continue
        surface = kinds[-1]
        polys, repaired = [], 0
        for s in items:
            p = Polygon(s["en"])
            if not p.is_valid:
                p, repaired = make_valid(p), repaired + 1
            polys.append((p, s["hole"]))
        union = unary_union([p for p, _ in polys])
        merged = union
        if close_m > 0:
            # Round joins: mitred ones rebuild sharp corners and reopen a corner-to-corner contact
            merged = merged.buffer(close_m, quad_segs=4).buffer(-close_m, quad_segs=4)
        merged = merged.intersection(square)
        parts = [g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon" and g.area >= MIN_AREA_M2]
        for k, g in enumerate(parts):
            holes = sorted({h for p, h in polys if h is not None and p.intersects(g)})
            out.append({"surface": surface, "hole": "_".join(map(str, holes)) or "x", "index": k,
                        "en": [tuple(c) for c in g.exterior.coords[:-1]],
                        "interiors": [[tuple(c) for c in r.coords[:-1]] for r in g.interiors]})
        report["surfaces"][surface] = {
            "fill": fill, "includes": kinds, "shapes_in": len(items), "shapes_out": len(parts),
            "repaired": repaired, "overlap_merged_m2": round(max(0.0, sum(p.area for p, _ in polys) - union.area), 1),
            "gaps_closed_m2": round(max(0.0, sum(g.area for g in parts) - union.intersection(square).area), 1)}
    return out, report


def write_terrain_obj(z: np.ndarray, sq: dict, base: float, path: Path, step: int = OBJ_STEP_M) -> dict:
    """
    The square's terrain as an OBJ for conforming Clender's meshes in Blender. Clender's .blend
    is the Unity frame turned 180 degrees: X = -(metres east of the west edge), Y = -(metres
    north of the south edge), Z = metres above the base. Written Y-up, so Blender's default
    OBJ import lands it there with no moving.
    """
    zs = z[::-1][::step, ::step]              # south-first rows, every step metres
    rows, cols = zs.shape
    xs = np.arange(cols, dtype=np.float64) * step
    ys = np.arange(rows, dtype=np.float64) * step
    gx, gy = np.meshgrid(xs, ys)
    # OBJ (x, y, z) imports to Blender (x, -z, y), so this gives Blender (-east, -north, height)
    verts = np.column_stack([-gx.ravel(), (zs - base).ravel(), gy.ravel()])
    i = np.arange(rows * cols).reshape(rows, cols) + 1  # OBJ indices start at 1
    a, b, c, d = i[:-1, :-1].ravel(), i[:-1, 1:].ravel(), i[1:, 1:].ravel(), i[1:, :-1].ravel()
    faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    with open(path, "w") as f:
        f.write(f"# Terrain for OPCD, {step} m grid, Clender's frame. -X east, Y up, Z north of the square's south-west corner.\n")
        np.savetxt(f, verts, fmt="v %.2f %.3f %.2f")
        np.savetxt(f, faces, fmt="f %d %d %d")
    return {"vertices": int(rows * cols), "faces": int(len(faces)), "step_m": step}


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
west edge {west:.0f} E, south edge {south:.0f} N (EPSG:{epsg}).{source}
Ground between the course and the square's edges: west {margins[west]} m, east {margins[east]} m,
south {margins[south]} m, north {margins[north]} m.

## 1. Clender (web app, SVG Jobs)
Upload `{slug}_clender.svg` at app.opcdgolf.com > SVG Jobs. It's an Inkscape SVG, 1 mm = 1 m, north up,
{size:.0f} x {size:.0f} mm, one layer per surface in the OPCD colours. Overlapping shapes of the
same colour are already merged and corner contacts joined; `clender_check.json` says what was
merged, closed or left out. Clender returns a zip with a Blender 4.2 .blend and a validation log.

## 2. Blender 4.2.2+ (OPCD Tools)
Open the .blend and allow its script (it installs the OPCD Blender tools), then import `{slug}_terrain_opcd.obj` (File > Import > Wavefront, default axes).
It lands in Clender's frame, which is the Unity frame turned 180 degrees: X = -(metres east)
and Y = -(metres north) of the square's south-west corner, Z = height. So it sits right under
Clender's meshes with no moving. Conform the meshes with OPCD Tools.
Use `{slug}_overlay.jpg` (1 pixel = 1 m, same square) to check the shapes line up. Export FBX.

## 3. Unity 2018.2.8f1 (OPCD V4 BaseProject, Windows)
{unity}
Then import the FBX at (0, 0, 0) and set the mesh materials.

## 4. GreenKeeper
`greenkeeper.csv` lists every tee and pin as x, y, z (metres): x east and z north of the
square's south-west corner, y {y_meaning}.
`greenkeeper.json` adds green centres and penalty areas (outline points, with stakes colour).
{gaps}
"""

UNITY_OWN = """Terrain: import `{raw}` as RAW.
- Depth 16 bit, byte order Windows, width and height {samples}, flip vertically OFF
- Terrain size: width {size:.0f}, length {size:.0f}, height {height:.0f}
- Terrain position: (0, 0, 0). Real elevation = Unity y + {base:.0f} m."""

UNITY_OPCD = """Terrain: use the OPCD web app's lidar package for this square (course.json "opcd.terrain").
Check where its terrain sits: if its south-west corner is not at Unity (0, 0), add that corner's
x and z to every GreenKeeper position, and move the Blender terrain OBJ and FBX the same way."""


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def build_package(slug: str, margin_m: float = MARGIN_M, obj_step: int = OBJ_STEP_M) -> dict:
    cfg = load_course_config(slug)
    paths = get_paths(slug)
    for key, stage in (("terrain", "01_lidar.py"), ("holes", "05_layout.py"), ("surfaces", "05_layout.py")):
        if not paths[key].exists():
            raise FileNotFoundError(f"Missing {paths[key]}. Run {stage} first.")
    holes_doc = json.loads(paths["holes"].read_text())
    epsg = int(holes_doc.get("epsg") or 32619)
    project = layout.make_projector(epsg)
    surfaces = load_surfaces(paths["surfaces"], project)

    # A terrain made by the OPCD web app's lidar tool sets the square; otherwise pick our own
    ext = (cfg.get("opcd") or {}).get("terrain")
    if ext:
        sq = square_from_center(ext["center_lat"], ext["center_lon"], ext["size_m"], epsg)
    else:
        sq = choose_square(surfaces, margin_m)
    margins = course_margins(surfaces, sq)
    out = paths["out"]
    out.mkdir(parents=True, exist_ok=True)

    z, filled = fill_gaps(sample_terrain(paths["terrain"], sq, epsg))
    if ext:
        # Their terrain replaces our heightmap: heights stay real elevations, and stale RAW files go
        raw = None
        hm = {"base_m": 0.0, "height_m": None, "min_m": round(float(z.min()), 2), "max_m": round(float(z.max()), 2)}
        for stale in [*out.glob(f"{slug}_heightmap_*.raw"), out / "unity_terrain.json"]:
            stale.unlink(missing_ok=True)
    else:
        raw = out / f"{slug}_heightmap_{sq['samples']}.raw"
        hm = write_raw(z, raw)

    obj = write_terrain_obj(z, sq, hm["base_m"], out / f"{slug}_terrain_opcd.obj", obj_step)

    frame = {"xmin": sq["west"], "ymin": sq["south"], "xmax": sq["east"], "ymax": sq["north"]}
    shapes, clean = clender_shapes(surfaces, sq)
    layout.write_svg(shapes, frame, out / f"{slug}_clender.svg", f"{cfg.get('name')} for Clender, EPSG:{epsg}")
    (out / "clender_check.json").write_text(json.dumps(clean, indent=2))
    has_overlay = write_overlay(paths["imagery"], sq, epsg, out / f"{slug}_overlay.jpg")

    rows = greenkeeper_sheet(holes_doc["holes"], z, sq, hm["base_m"])
    (out / "greenkeeper.json").write_text(json.dumps({"units": "m", "axes": "Unity x = east, z = north, y = up",
                                                      "holes": rows}, indent=2))
    write_sheet_csv(rows, out / "greenkeeper.csv")

    if raw:
        unity = {"heightmap": raw.name, "resolution": sq["samples"], "depth_bits": 16, "byte_order": "Windows",
                 "flip_vertically": False, "terrain_width_m": sq["size_m"], "terrain_length_m": sq["size_m"],
                 "terrain_height_m": hm["height_m"], "base_elevation_m": hm["base_m"],
                 "square": {k: sq[k] for k in ("west", "south", "east", "north")}, "epsg": epsg,
                 "elevation_range_m": [hm["min_m"], hm["max_m"]], "share_filled_outside_terrain": round(filled, 4)}
        (out / "unity_terrain.json").write_text(json.dumps(unity, indent=2))
        unity_text = UNITY_OWN.format(raw=raw.name, samples=sq["samples"], size=sq["size_m"],
                                      height=hm["height_m"], base=hm["base_m"])
        y_meaning, source = f"metres above the heightmap base ({hm['base_m']:.0f} m)", ""
    else:
        unity_text = UNITY_OPCD
        y_meaning = "real elevation in metres (their terrain's base is unknown; tees and pins snap to the ground)"
        source = (f"\nThe square is the OPCD web app's lidar terrain: centre {sq['center']['lat']}, "
                  f"{sq['center']['lon']}, {sq['size_m']:.0f} m across.")

    gaps = (f"\nNote: {filled:.0%} of the square is outside the terrain and was filled with the nearest height."
            if filled > 0.001 else "")
    (out / "README.md").write_text(README.format(
        name=cfg.get("name", slug), slug=slug, size=sq["size_m"], west=sq["west"], south=sq["south"], epsg=epsg,
        source=source, margins=margins, unity=unity_text, y_meaning=y_meaning, gaps=gaps))
    return {"square": sq, "heightmap": hm, "raw": raw, "margins": margins, "overlay": has_overlay, "obj": obj,
            "clender": clean, "holes": len(rows), "tees": sum(len(r["tees"]) for r in rows),
            "pins": sum(len(r["pins"]) for r in rows), "share_filled": filled, "out": out}


def main():
    parser = argparse.ArgumentParser(description="Build the OPCD input files (Clender SVG, terrain OBJ, GreenKeeper sheet)")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--margin", type=float, default=MARGIN_M, help="metres of ground kept round the course")
    parser.add_argument("--obj-step", type=int, default=OBJ_STEP_M, help="terrain OBJ vertex spacing in metres")
    args = parser.parse_args()
    print(f"OPCD package for {args.course}")
    r = build_package(args.course, args.margin, args.obj_step)
    sq, hm, obj, m = r["square"], r["heightmap"], r["obj"], r["margins"]
    if r["raw"]:
        print(f"  Square: {sq['size_m']:.0f} m, heightmap {sq['samples']} x {sq['samples']}")
        print(f"  Heights {hm['min_m']}–{hm['max_m']} m; Unity height {hm['height_m']:.0f} m from base {hm['base_m']:.0f} m")
    else:
        print(f"  Square: OPCD lidar terrain, {sq['size_m']:.0f} m centred on {sq['center']['lat']}, {sq['center']['lon']}"
              " (no heightmap written: use theirs)")
    print(f"  Ground round the course: west {m['west']} m, east {m['east']} m, south {m['south']} m, north {m['north']} m")
    if min(m.values()) < MARGIN_M:
        print(f"  Warning: under {MARGIN_M:.0f} m of ground on at least one side"
              + (" and shapes outside the square are cut off" if min(m.values()) < 0 else ""))
    print(f"  Terrain OBJ: {obj['vertices']:,} vertices, {obj['step_m']} m apart")
    for surface, c in r["clender"]["surfaces"].items():
        print(f"  Clender {'+'.join(c['includes'])}: {c['shapes_in']} shapes -> {c['shapes_out']}"
              f" ({c['overlap_merged_m2']:,.0f} m² of overlap merged, {c['gaps_closed_m2']:,.0f} m² of gaps closed,"
              f" {c['repaired']} outlines repaired)")
    for surface, c in r["clender"]["skipped_colours"].items():
        print(f"  Warning: {surface} left out of the Clender SVG: {c['fill']} isn't a known Clender colour"
              f" ({c['shapes']} shapes stay rough). Set its colour in 05_layout.OPCD once you have the V4 palette.")
    print(f"  {r['holes']} holes, {r['tees']} tees, {r['pins']} pins in greenkeeper.csv")
    if r["share_filled"] > 0.001:
        print(f"  Warning: {r['share_filled']:.0%} of the square is outside the terrain (filled with nearest height)")
    if not r["overlay"]:
        print("  No aerial photo found: overlay skipped (run 03_imagery.py)")
    print(f"  Files in {r['out']}")


if __name__ == "__main__":
    main()
