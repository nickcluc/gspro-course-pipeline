"""
05_layout.py — Course layout from Arccos

Turns the Arccos hole maps from Stage 00 into the course layout the rest of
the pipeline needs:

  1. holes.json — per hole: par, stroke index, tees (metres and yards) placed
     on tee boxes, green outline and centre, and your most-used pins (from rounds)
  2. surfaces.geojson — every surface outline in WGS84, for QGIS checks
  3. surfaces_local.json — every outline in the terrain's local metres
     (same frame as Stage 04's terrain OBJ), for Blender assembly
  4. <slug>_surfaces_opcd.svg — surfaces as OPCD-coloured shapes, 1 mm = 1 m,
     framed to the Stage 01 terrain so it lines up with the heightmap
  5. elevation_check.json — Arccos elevation points vs the Lidar terrain

Arccos shape types come from the Arccos dashboard's own lookup table. Surface
colours follow the OPCD scheme used by csites/osm2svg4opcd.

Usage:
    python pipeline/05_layout.py --course tedesco
    python pipeline/05_layout.py --course tedesco --pins 3

Input:
    courses/<slug>/data/raw/arccos/holes/hole_XX.json          (Stage 00)
    courses/<slug>/data/raw/arccos/rounds/*.json               (optional)
    courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif (optional, Stage 01)
    courses/<slug>/extra_surfaces.geojson                      (optional: shapes Arccos lacks, see Stage 05b)

Output:
    courses/<slug>/output/layout/holes.json
    courses/<slug>/output/layout/surfaces.geojson
    courses/<slug>/output/layout/surfaces_local.json
    courses/<slug>/output/layout/<slug>_surfaces_opcd.svg
    courses/<slug>/output/layout/elevation_check.json

Dependencies:
    pip install pyproj numpy rasterio
"""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from utils import REPO_ROOT, load_course_config

YARDS_PER_M = 1.0936133
MAX_TEE_GAP_M = 15.0  # a tee further than this from every mapped box is treated as unmapped

# Arccos featureId -> name (from the Arccos dashboard's lookup table)
ARCCOS_TYPES = {
    1: "Green", 2: "Sand Trap", 3: "Sand Trap (by green)", 4: "Water",
    5: "Water (by green)", 6: "Tee", 7: "Fairway", 8: "Fairway Center",
    9: "Trees", 10: "Bounding Box", 11: "No Shot", 12: "Out Of Bounds",
}
# Arccos featureId -> pipeline surface name
SURFACE = {
    1: "green", 2: "bunker", 3: "bunker", 4: "water", 5: "water", 6: "tee",
    7: "fairway", 8: "fairway_center", 9: "trees", 10: "boundary",
    11: "no_shot", 12: "out_of_bounds",
}
# Surfaces drawn in the OPCD SVG: (OPCD class, fill colour, stacking order)
OPCD = {
    "fairway": ("golf.fairway", "#43E561", 60),
    "tee": ("golf.tee", "#A0E5B8", 70),
    "trees": ("natural.wood", "#164B20", 80),
    "green": ("golf.green", "#BCE5A4", 90),
    "bunker": ("golf.bunker", "#E5E5AA", 100),
    "water": ("golf.water_hazard", "#0000C0", 110),
    "native": ("natural.scrub", "#8A7F3D", 55),  # long native grass; check its OPCD class
}
ROUGH = ("leisure.golf_course", "#278438")


# ---------------------------------------------------------------------------
# Paths and loading
# ---------------------------------------------------------------------------

def get_paths(slug: str) -> dict:
    base = REPO_ROOT / "courses" / slug
    return {
        "holes": base / "data" / "raw" / "arccos" / "holes",
        "rounds": base / "data" / "raw" / "arccos" / "rounds",
        "terrain": base / "data" / "processed" / "heightmaps" / f"{slug}_terrain.tif",
        "out": base / "output" / "layout",
        "extras": base / "extra_surfaces.geojson",
    }


def load_holes(holes_dir: Path) -> list[dict]:
    files = sorted(holes_dir.glob("hole_*.json"))
    if not files:
        raise FileNotFoundError(f"No Arccos hole maps in {holes_dir}. Run 00_arccos.py first.")
    return [json.loads(f.read_text()) for f in files]


def load_extras(path: Path, project) -> list[dict]:
    """
    Shapes you add by hand or accept from Stage 05b (courses/<slug>/extra_surfaces.geojson):
    WGS84 polygons with properties `surface` (bunker, native, green, ...) and `hole`.
    """
    if not path.exists():
        return []
    out = []
    for i, f in enumerate(json.loads(path.read_text()).get("features") or []):
        p = f.get("properties") or {}
        ring = (f.get("geometry") or {}).get("coordinates", [[]])[0]
        if p.get("surface") and len(ring) >= 4:
            ll = [tuple(c[:2]) for c in ring[:-1]]
            out.append({"hole": int(p.get("hole") or 0), "index": 1000 + i, "arccos_type": None,
                        "arccos_name": "Extra (" + str(p.get("source") or "added") + ")",
                        "surface": p["surface"], "lonlat": ll, "en": [project(x, y) for x, y in ll]})
    return out


def _inside(x: float, y: float, poly: list) -> bool:
    hit, j = False, len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            hit = not hit
        j = i
    return hit


def apply_photo_bunkers(surfaces: list[dict], photo_path: Path, project,
                        replace: bool, drop_unseen: bool) -> dict:
    """
    Uses Stage 05b's findings on Arccos bunkers:
      replace     — swap each Arccos bunker for the photo's outline of the same sand
                    (Arccos outlines are often too small or a few metres off)
      drop_unseen — remove Arccos bunkers where the photo shows only grass
    A photo outline matches an Arccos bunker when either one's centre is inside the other.
    """
    stats = {"replaced": 0, "dropped": 0}
    if not (replace or drop_unseen) or not photo_path.exists():
        return stats
    feats = json.loads(photo_path.read_text()).get("features") or []

    def ring(f):
        ll = [tuple(c[:2]) for c in f["geometry"]["coordinates"][0][:-1]]
        return {"lonlat": ll, "en": [project(x, y) for x, y in ll]}

    photo = [ring(f) for f in feats if f["properties"].get("finding") == "bunker_matches_arccos"]
    unseen = [ring(f)["en"] for f in feats if f["properties"].get("finding") == "arccos_bunker_no_sand_seen"]
    keep = []
    for s in surfaces:
        if s["surface"] == "bunker" and s.get("arccos_type") is not None:
            c = centroid(s["en"])
            if drop_unseen and any(_inside(*c, u) or _inside(*centroid(u), s["en"]) for u in unseen):
                stats["dropped"] += 1
                continue
            if replace:
                hits = [p for p in photo if _inside(*centroid(p["en"]), s["en"]) or _inside(*c, p["en"])]
                if hits:
                    best = max(hits, key=lambda p: len(p["en"]))
                    s = dict(s, en=best["en"], lonlat=best["lonlat"], arccos_name=s["arccos_name"] + " (photo outline)")
                    stats["replaced"] += 1
        keep.append(s)
    surfaces[:] = keep
    return stats


def load_rounds(rounds_dir: Path) -> list[dict]:
    return [json.loads(f.read_text()) for f in sorted(rounds_dir.glob("*.json"))] if rounds_dir.exists() else []


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def make_projector(epsg: int):
    """Returns f(lon, lat) -> (easting, northing) in the given metric CRS."""
    from pyproj import Transformer
    tf = Transformer.from_crs(4326, epsg, always_xy=True)
    return lambda lon, lat: tf.transform(lon, lat)


def centroid(pts: list[tuple[float, float]]) -> tuple[float, float]:
    """Area-weighted centroid of a polygon (falls back to the mean for slivers)."""
    a = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-9:
        return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    return cx / (3 * a), cy / (3 * a)


def local_frame(spatial: dict):
    """
    Returns f(easting, northing) -> (x, z) in Stage 04's terrain frame:
    origin at the terrain centre, x = east, z = north (as 04's heightmap_to_obj computes it).
    """
    cx = (spatial["xmin"] + spatial["xmax"]) / 2.0
    cz = (spatial["ymin"] + spatial["ymax"]) / 2.0
    return lambda e, n: (e - cx, n - cz)


def terrain_spatial(tif_path: Path) -> dict | None:
    """Bounds and CRS of the Stage 01 terrain, or None if it isn't there yet."""
    if not tif_path.exists():
        return None
    import rasterio
    with rasterio.open(tif_path) as ds:
        b = ds.bounds
        return {"xmin": b.left, "ymin": b.bottom, "xmax": b.right, "ymax": b.top,
                "crs": str(ds.crs), "epsg": ds.crs.to_epsg()}


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

def build_surfaces(holes: list[dict], project) -> list[dict]:
    """One record per Arccos shape, in WGS84 and projected metres."""
    out = []
    for h in holes:
        for i, f in enumerate(h.get("features") or []):
            ll = [(c["longitude"], c["latitude"]) for c in f.get("coordinates") or []]
            if len(ll) < 3:
                continue
            t = f.get("featureId")
            out.append({
                "hole": h["holeId"], "index": i, "arccos_type": t,
                "arccos_name": ARCCOS_TYPES.get(t, "Unknown"),
                "surface": SURFACE.get(t, "unknown"),
                "lonlat": ll, "en": [project(x, y) for x, y in ll],
            })
    return out


def write_geojson(surfaces: list[dict], path: Path) -> Path:
    feats = []
    for s in surfaces:
        cls = OPCD.get(s["surface"])
        feats.append({
            "type": "Feature",
            "properties": {"hole": s["hole"], "surface": s["surface"], "arccos_type": s["arccos_type"],
                           "arccos_name": s["arccos_name"], "opcd_class": cls[0] if cls else None},
            "geometry": {"type": "Polygon", "coordinates": [s["lonlat"] + [s["lonlat"][0]]]},
        })
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return path


def write_local(surfaces: list[dict], to_local, frame: dict, path: Path) -> Path:
    """Outlines as [x, z] metres in the terrain frame, grouped by surface."""
    data = {"frame": frame, "units": "m", "axes": "x = east, z = north, origin = terrain centre",
            "surfaces": [{"hole": s["hole"], "surface": s["surface"], "arccos_type": s["arccos_type"],
                          "points": [[round(v, 2) for v in to_local(e, n)] for e, n in s["en"]]}
                         for s in surfaces if s["surface"] not in ("fairway_center", "boundary")]}
    path.write_text(json.dumps(data))
    return path


def svg_frame(surfaces: list[dict], spatial: dict | None, margin_m: float = 40.0) -> dict:
    """The SVG covers the terrain extent when known, else the shapes plus a margin."""
    if spatial:
        return {k: spatial[k] for k in ("xmin", "ymin", "xmax", "ymax")}
    es = [e for s in surfaces for e, _ in s["en"]]
    ns = [n for s in surfaces for _, n in s["en"]]
    return {"xmin": min(es) - margin_m, "ymin": min(ns) - margin_m,
            "xmax": max(es) + margin_m, "ymax": max(ns) + margin_m}


def write_svg(surfaces: list[dict], frame: dict, path: Path, label: str) -> Path:
    """OPCD-coloured SVG, 1 mm = 1 m, north up, one Inkscape layer per surface."""
    w, h = frame["xmax"] - frame["xmin"], frame["ymax"] - frame["ymin"]
    groups = {}
    for s in surfaces:
        if s["surface"] in OPCD:
            groups.setdefault(s["surface"], []).append(s)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'width="{w:.2f}mm" height="{h:.2f}mm" viewBox="0 0 {w:.2f} {h:.2f}">',
        f"<!-- {label}. Top-left = ({frame['xmin']:.2f} E, {frame['ymax']:.2f} N). 1 unit = 1 m. -->",
        f'<g inkscape:groupmode="layer" id="layer-rough" inkscape:label="{ROUGH[0]}">'
        f'<rect id="{ROUGH[0]}-0" x="0" y="0" width="{w:.2f}" height="{h:.2f}" fill="{ROUGH[1]}" stroke="none"/></g>',
    ]
    for surface in sorted(groups, key=lambda k: OPCD[k][2]):
        cls, fill, _ = OPCD[surface]
        lines.append(f'<g inkscape:groupmode="layer" id="layer-{cls}" inkscape:label="{cls}">')
        for s in groups[surface]:
            d = "M" + " L".join(f"{e - frame['xmin']:.2f},{frame['ymax'] - n:.2f}" for e, n in s["en"]) + " Z"
            lines.append(f'<path id="{cls}-hole{s["hole"]}-{s["index"]}" d="{d}" fill="{fill}" stroke="none"/>')
        lines.append("</g>")
    lines.append("</svg>")
    path.write_text("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Holes, tees and pins
# ---------------------------------------------------------------------------

def match_tees(hole_tees: list[dict], boxes: list[tuple[float, float]], green_en: tuple[float, float],
               route_en: tuple[float, float] | None = None, observed: dict | None = None) -> list[dict]:
    """
    Places each Arccos tee (a name plus a distance) on one of the hole's tee boxes.

    Arccos's tee `distance` is in METRES (checked at Tedesco: tee shots measured
    by GPS match it in metres, and the course total of 5,876 is ~6,426 yards).

    Placement, best first:
      1. observed: the median start of your first shots from that tee (from
         your rounds), snapped to the nearest tee box
      2. distance: the box whose distance to the green, via the fairway centre
         line when there is one, is closest to the listed distance
    """
    def route_m(p):
        if route_en:
            return math.dist(p, route_en) + math.dist(route_en, green_en)
        return math.dist(p, green_en)

    out = []
    for t in hole_tees:
        metres = t.get("distance")
        row = {"tee_id": t.get("teeId"), "name": t.get("name"),
               "metres": round(metres, 1) if metres is not None else None,
               "yards": round(metres * YARDS_PER_M, 1) if metres is not None else None,
               "box": None, "placed_by": None, "error_m": None}
        seen = (observed or {}).get(t.get("teeId"))
        if seen:
            row["placed_by"] = f"your tee shots ({seen['rounds']} rounds)"
            near = min(range(len(boxes)), key=lambda k: math.dist(boxes[k], seen["en"])) if boxes else None
            gap = math.dist(boxes[near], seen["en"]) if near is not None else None
            if gap is not None and gap <= MAX_TEE_GAP_M:
                row.update(box=near, en=boxes[near], error_m=round(gap, 1))
            else:
                # Your shots start away from every mapped box: the map is likely missing this tee
                row.update(en=seen["en"], error_m=round(gap, 1) if gap is not None else None,
                           note="no mapped tee box near your tee shots; Arccos map likely missing this tee, "
                                "so your tee-shot position is used")
        elif metres is not None and boxes:
            i = min(range(len(boxes)), key=lambda k: abs(route_m(boxes[k]) - metres))
            row.update(box=i, en=boxes[i], placed_by="distance", error_m=round(abs(route_m(boxes[i]) - metres), 1))
        out.append(row)
    return out


def tee_starts(rounds: list[dict], version: int | None, project, min_rounds: int = 3) -> dict:
    """Median first-shot start per (hole, teeId) from your rounds."""
    pts: dict[tuple[int, int], list] = {}
    for r in rounds:
        if version is not None and r.get("courseVersion") not in (None, version):
            continue
        for h in r.get("holes") or []:
            shots = (h or {}).get("shots") or []
            if shots and shots[0].get("startLat") is not None:
                pts.setdefault((h["holeId"], r.get("teeId")), []).append(
                    project(shots[0]["startLong"], shots[0]["startLat"]))
    out: dict[int, dict] = {}
    for (hole, tee), p in pts.items():
        if len(p) >= min_rounds:
            es, ns = sorted(x for x, _ in p), sorted(y for _, y in p)
            out.setdefault(hole, {})[tee] = {"en": (es[len(es) // 2], ns[len(ns) // 2]), "rounds": len(p)}
    return out


def pins_by_hole(rounds: list[dict], version: int | None, project, top_n: int, cell_m: float = 3.0) -> dict:
    """Your most-used pin spots per hole, clustered on a 3 m grid."""
    cells: dict[int, Counter] = {}
    for r in rounds:
        if version is not None and r.get("courseVersion") not in (None, version):
            continue
        for h in r.get("holes") or []:
            if not h or h.get("pinLat") is None:
                continue
            e, n = project(h["pinLong"], h["pinLat"])
            key = (round(e / cell_m) * cell_m, round(n / cell_m) * cell_m)
            cells.setdefault(h["holeId"], Counter())[key] += 1
    return {hole: [{"easting": k[0], "northing": k[1], "rounds": c} for k, c in cnt.most_common(top_n)]
            for hole, cnt in cells.items()}


def build_holes(holes: list[dict], surfaces: list[dict], pins: dict, to_local, starts: dict | None = None) -> list[dict]:
    by_hole = {}
    for s in surfaces:
        by_hole.setdefault(s["hole"], []).append(s)
    out = []
    for h in sorted(holes, key=lambda x: x["holeId"]):
        hs = by_hole.get(h["holeId"], [])
        greens = [s for s in hs if s["surface"] == "green"]
        green = max(greens, key=lambda s: len(s["en"])) if greens else None
        g_en = centroid(green["en"]) if green else None
        boxes = [centroid(s["en"]) for s in hs if s["surface"] == "tee"]
        lines = [centroid(s["en"]) for s in hs if s["surface"] == "fairway_center"]

        def place(e, n):
            x, z = to_local(e, n)
            return {"easting": round(e, 2), "northing": round(n, 2), "x": round(x, 2), "z": round(z, 2)}

        out.append({
            "hole": h["holeId"],
            "par": h.get("mensPar"),
            "stroke_index": h.get("mensHandicap") or None,  # Arccos uses 0 for "not set"
            "green": place(*g_en) if g_en else None,
            "tee_boxes": [place(e, n) for e, n in boxes],
            "tees": [dict({k: v for k, v in t.items() if k != "en"},
                          position=place(*t["en"]) if t.get("en") else None)
                     for t in (match_tees(h.get("holeTees") or [], boxes, g_en, lines[0] if lines else None,
                                          (starts or {}).get(h["holeId"])) if g_en else [])],
            "pins": [dict(place(p["easting"], p["northing"]), rounds=p["rounds"])
                     for p in pins.get(h["holeId"], [])],
            "surface_counts": dict(Counter(s["surface"] for s in hs)),
        })
    return out


# ---------------------------------------------------------------------------
# Elevation check
# ---------------------------------------------------------------------------

def elevation_points(holes: list[dict], project) -> list[dict]:
    pts = []
    for h in holes:
        for p in h.get("elevations") or []:
            e, n = project(p["longitude"], p["latitude"])
            pts.append({"hole": h["holeId"], "easting": e, "northing": n, "arccos_m": p["elevation"]})
    return pts


def check_elevation(points: list[dict], tif_path: Path, tolerance_m: float = 0.5) -> dict:
    """Compares Arccos heights with the Lidar terrain under each point."""
    import numpy as np
    import rasterio
    with rasterio.open(tif_path) as ds:
        nodata = ds.nodata
        vals = [v[0] for v in ds.sample([(p["easting"], p["northing"]) for p in points])]
    per_hole: dict[int, list[float]] = {}
    for p, v in zip(points, vals):
        if v is None or (nodata is not None and v == nodata) or np.isnan(v):
            continue
        per_hole.setdefault(p["hole"], []).append(float(v) - p["arccos_m"])
    holes = {}
    for hole, diffs in sorted(per_hole.items()):
        a = np.array(diffs)
        holes[hole] = {"points": len(a), "mean_diff_m": round(float(a.mean()), 3),
                       "max_abs_diff_m": round(float(np.abs(a).max()), 3),
                       "over_tolerance": int((np.abs(a) > tolerance_m).sum())}
    all_d = np.array([d for v in per_hole.values() for d in v]) if per_hole else np.array([])
    return {"tolerance_m": tolerance_m, "note": "diff = Lidar terrain minus Arccos height",
            "overall_mean_diff_m": round(float(all_d.mean()), 3) if all_d.size else None,
            "holes": holes}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def process_layout(slug: str, top_pins: int = 3) -> dict:
    cfg = load_course_config(slug)
    paths = get_paths(slug)
    paths["out"].mkdir(parents=True, exist_ok=True)

    spatial = terrain_spatial(paths["terrain"])
    epsg = (spatial or {}).get("epsg") or cfg.get("lidar", {}).get("epsg_output", 32619)
    project = make_projector(epsg)

    holes = load_holes(paths["holes"])
    surfaces = build_surfaces(holes, project)
    opts = cfg.get("layout") or {}
    photo_stats = apply_photo_bunkers(surfaces, paths["out"] / "photo_surfaces.geojson", project,
                                      bool(opts.get("bunkers_from_photo")), bool(opts.get("drop_bunkers_without_sand")))
    extras = load_extras(paths["extras"], project)
    surfaces += extras
    frame = svg_frame(surfaces, spatial)
    frame_info = dict(frame, epsg=epsg, source="terrain" if spatial else "shapes + 40 m margin")
    to_local = local_frame(frame)

    version = (cfg.get("arccos") or {}).get("course_version")
    rounds = load_rounds(paths["rounds"])
    pins = pins_by_hole(rounds, version, project, top_pins)
    hole_rows = build_holes(holes, surfaces, pins, to_local, tee_starts(rounds, version, project))

    out = paths["out"]
    (out / "holes.json").write_text(json.dumps(
        {"course": cfg.get("name"), "epsg": epsg, "frame": frame_info, "holes": hole_rows}, indent=2))
    write_geojson(surfaces, out / "surfaces.geojson")
    write_local(surfaces, to_local, frame_info, out / "surfaces_local.json")
    write_svg(surfaces, frame, out / f"{slug}_surfaces_opcd.svg", f"{cfg.get('name')} from Arccos, EPSG:{epsg}")

    result = {"holes": len(hole_rows), "surfaces": len(surfaces), "extra_surfaces": len(extras),
              "photo_bunkers": photo_stats,
              "framed_to_terrain": bool(spatial),
              "pins": sum(len(h["pins"]) for h in hole_rows)}
    if spatial:
        check = check_elevation(elevation_points(holes, project), paths["terrain"])
        (out / "elevation_check.json").write_text(json.dumps(check, indent=2))
        result["elevation_mean_diff_m"] = check["overall_mean_diff_m"]
    return result


def main():
    parser = argparse.ArgumentParser(description="Build the course layout from Arccos hole maps")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--pins", type=int, default=3, help="most-used pin spots to keep per hole")
    args = parser.parse_args()
    print(f"Layout for {args.course}")
    r = process_layout(args.course, args.pins)
    print(f"  {r['holes']} holes, {r['surfaces']} shapes ({r['extra_surfaces']} from extra_surfaces.geojson), {r['pins']} pins")
    if any(r["photo_bunkers"].values()):
        print(f"  Arccos bunkers: {r['photo_bunkers']['replaced']} given photo outlines, "
              f"{r['photo_bunkers']['dropped']} dropped (no sand in the photo)")
    print("  SVG framed to the terrain" if r["framed_to_terrain"]
          else "  No terrain yet: SVG framed to the shapes. Run 01_lidar.py, then this stage again.")
    if "elevation_mean_diff_m" in r:
        print(f"  Lidar minus Arccos height, mean: {r['elevation_mean_diff_m']} m (see elevation_check.json)")


if __name__ == "__main__":
    main()
