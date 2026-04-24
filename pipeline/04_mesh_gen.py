"""
04_mesh_gen.py — Mesh Generation Pipeline

Converts processed data into 3D assets for GSPro Course Forge:
  1. LiDAR terrain heightmap → 16-bit PNG + metadata (Course Forge terrain input)
  2. LiDAR terrain → OBJ mesh (Blender preview / validation)
  3. PuttView green grids → interpolated OBJ surface patches (one per hole)

GSPro Course Forge uses a Unity terrain driven by a 16-bit grayscale PNG
heightmap. The terrain_meta.json output tells you exactly what numbers to enter
in Course Forge (Terrain Width, Length, and Height Scale).

Usage:
    python pipeline/04_mesh_gen.py --course tedesco
    python pipeline/04_mesh_gen.py --course tedesco --terrain-step 3

Input:
    courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif
    courses/<slug>/data/processed/grids/hole_XX_green.json  (optional)

Output:
    courses/<slug>/output/terrain/<slug>_heightmap_16bit.png  — Course Forge terrain
    courses/<slug>/output/terrain/<slug>_terrain_meta.json    — Course Forge settings
    courses/<slug>/output/terrain/<slug>_terrain.obj          — Blender preview
    courses/<slug>/output/greens/hole_XX_green.obj            — per-hole green meshes

Dependencies:
    pip install rasterio numpy scipy
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import griddata

from utils import REPO_ROOT

YARDS_TO_M = 0.9144
FEET_TO_M = 0.3048


# ---------------------------------------------------------------------------
# Heightmap loading
# ---------------------------------------------------------------------------

def load_heightmap(tif_path: Path) -> tuple[np.ndarray, dict]:
    """
    Loads a Float32 GeoTIFF terrain heightmap.

    Returns:
        data: 2D float32 array of elevations in meters, nodata → NaN
        meta: dict with spatial and elevation statistics
    """
    try:
        import rasterio
    except ImportError:
        print("ERROR: rasterio not installed. Run: pip install rasterio")
        sys.exit(1)

    with rasterio.open(tif_path) as ds:
        data = ds.read(1).astype(np.float32)
        nodata = ds.nodata
        transform = ds.transform
        crs = ds.crs
        h, w = data.shape

    if nodata is not None:
        data[data == nodata] = np.nan

    pixel_size_m = abs(transform.a)
    xmin = transform.c
    ymax = transform.f
    xmax = xmin + w * pixel_size_m
    ymin = ymax - h * pixel_size_m

    meta = {
        "width_px": w,
        "height_px": h,
        "pixel_size_m": pixel_size_m,
        "width_m": w * pixel_size_m,
        "height_m": h * pixel_size_m,
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "elev_min": float(np.nanmin(data)),
        "elev_max": float(np.nanmax(data)),
        "elev_range_m": float(np.nanmax(data) - np.nanmin(data)),
        "nodata_count": int(np.sum(np.isnan(data))),
        "crs": str(crs),
    }

    return data, meta


# ---------------------------------------------------------------------------
# 16-bit PNG export (Course Forge terrain input)
# ---------------------------------------------------------------------------

def export_16bit_png(data: np.ndarray, meta: dict, output_path: Path) -> float:
    """
    Exports terrain as a 16-bit grayscale PNG for GSPro Course Forge.

    Full elevation range is mapped linearly to 0–65535.
    NaN (water/nodata) pixels are set to 0 (sea level).

    Returns height_scale_m: the elevation range, needed for Course Forge setup.
    """
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError:
        print("ERROR: rasterio not installed.")
        sys.exit(1)

    elev_min = meta["elev_min"]
    elev_range = meta["elev_range_m"]

    filled = np.where(np.isnan(data), elev_min, data)

    if elev_range > 0:
        normalized = ((filled - elev_min) / elev_range * 65535).astype(np.uint16)
    else:
        normalized = np.zeros_like(filled, dtype=np.uint16)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    transform = from_bounds(
        meta["xmin"], meta["ymin"], meta["xmax"], meta["ymax"],
        meta["width_px"], meta["height_px"]
    )
    with rasterio.open(
        output_path, "w",
        driver="PNG",
        height=meta["height_px"],
        width=meta["width_px"],
        count=1,
        dtype="uint16",
        crs=meta["crs"],
        transform=transform,
    ) as dst:
        dst.write(normalized, 1)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved 16-bit heightmap: {output_path.name} "
          f"({meta['width_px']}x{meta['height_px']}px, {size_mb:.1f} MB)")
    return elev_range


# ---------------------------------------------------------------------------
# Terrain metadata (Course Forge setup guide)
# ---------------------------------------------------------------------------

def export_terrain_metadata(meta: dict, height_scale_m: float, output_path: Path) -> Path:
    """
    Saves a JSON file with the exact numbers to enter in GSPro Course Forge
    when importing the heightmap PNG.
    """
    params = {
        "course_forge_settings": {
            "terrain_width_m": round(meta["width_m"], 1),
            "terrain_length_m": round(meta["height_m"], 1),
            # Add 2m safety margin so max elevation never clips at 65535
            "terrain_height_m": round(height_scale_m + 2.0, 1),
            "heightmap_resolution": max(meta["width_px"], meta["height_px"]),
        },
        "elevation": {
            "min_m": round(meta["elev_min"], 2),
            "max_m": round(meta["elev_max"], 2),
            "range_m": round(meta["elev_range_m"], 2),
        },
        "spatial": {
            "pixel_size_m": round(meta["pixel_size_m"], 3),
            "width_px": meta["width_px"],
            "height_px": meta["height_px"],
            "crs": meta["crs"],
            "xmin": round(meta["xmin"], 1),
            "ymin": round(meta["ymin"], 1),
            "xmax": round(meta["xmax"], 1),
            "ymax": round(meta["ymax"], 1),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(params, f, indent=2)

    cf = params["course_forge_settings"]
    print(f"  Saved terrain metadata: {output_path.name}")
    print(f"    Course Forge → Width: {cf['terrain_width_m']}m  "
          f"Length: {cf['terrain_length_m']}m  "
          f"Height: {cf['terrain_height_m']}m")
    return output_path


# ---------------------------------------------------------------------------
# OBJ terrain mesh (Blender preview)
# ---------------------------------------------------------------------------

def _write_obj_quads(f, v0, v1, v2, v3):
    """Writes quad faces as two triangles (shared by terrain and green OBJ writers)."""
    faces = np.column_stack([v0, v1, v2, v3])
    tri1 = np.column_stack([faces[:, 0], faces[:, 1], faces[:, 2]])
    tri2 = np.column_stack([faces[:, 1], faces[:, 3], faces[:, 2]])
    all_tris = np.vstack([tri1, tri2])
    # Interleave tri1/tri2 to preserve original face ordering
    interleaved = np.empty((len(v0) * 2, 3), dtype=all_tris.dtype)
    interleaved[0::2] = tri1
    interleaved[1::2] = tri2
    lines = ["f %d %d %d\n" % (a, b, c) for a, b, c in interleaved]
    f.write("".join(lines))


def heightmap_to_obj(
    data: np.ndarray,
    meta: dict,
    output_path: Path,
    step_m: float = 5.0,
) -> Path:
    """
    Exports the terrain as a Wavefront OBJ mesh for Blender preview.

    Coordinate system (Y-up, right-handed):
        X = east  (meters from center)
        Y = elevation (meters)
        Z = -north (meters from center)

    step_m controls mesh density. 5m gives ~600x600 verts for a 3km terrain.
    """
    h, w = data.shape
    px = meta["pixel_size_m"]
    step = max(1, int(round(step_m / px)))

    rows = np.arange(0, h, step)
    cols = np.arange(0, w, step)
    nr, nc = len(rows), len(cols)

    C_idx, R_idx = np.meshgrid(cols, rows)

    elev = data[R_idx, C_idx]
    elev = np.where(np.isnan(elev), meta["elev_min"], elev)

    # Center terrain at origin
    cx = (w * px) / 2.0
    cz = (h * px) / 2.0

    x = (C_idx * px - cx).ravel()
    y = elev.ravel().astype(np.float64)
    z = -(R_idx * px - cz).ravel()

    # Face vertex indices (1-based OBJ)
    ri = np.arange(nr - 1)
    ci = np.arange(nc - 1)
    RI, CI = np.meshgrid(ri, ci, indexing="ij")
    RI, CI = RI.ravel(), CI.ravel()

    v0 = RI * nc + CI + 1
    v1 = (RI + 1) * nc + CI + 1
    v2 = RI * nc + (CI + 1) + 1
    v3 = (RI + 1) * nc + (CI + 1) + 1

    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_verts = len(x)
    n_faces = len(RI) * 2

    with open(output_path, "w") as f:
        f.write(f"# Terrain mesh — {step_m}m grid resolution\n")
        f.write(f"# Vertices: {n_verts:,}  Faces: {n_faces:,}\n")
        verts = np.column_stack([x, y, z])
        f.write("".join("v %.2f %.2f %.2f\n" % (r[0], r[1], r[2]) for r in verts))
        _write_obj_quads(f, v0, v1, v2, v3)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved terrain OBJ: {output_path.name} "
          f"({n_verts:,} verts, {n_faces:,} faces, {size_mb:.1f} MB)")
    return output_path


# ---------------------------------------------------------------------------
# Green surface interpolation (PuttView data)
# ---------------------------------------------------------------------------

def load_green_grid(json_path: Path) -> list:
    """Loads a PuttView green grid JSON and returns the points list."""
    with open(json_path) as f:
        data = json.load(f)
    return data.get("points", [])


def interpolate_green_surface(
    points: list,
    resolution_yards: float = 0.5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Interpolates sparse PuttView 3-yard grid points to a dense surface.

    Uses cubic interpolation, with nearest-neighbour fallback for edge NaNs.

    Returns:
        grid: 2D array of elevation in meters
        xi:   1D x coordinate array in meters
        yi:   1D y coordinate array in meters
    """
    if not points:
        raise ValueError("No points to interpolate")

    xs = np.array([p["x"] for p in points]) * YARDS_TO_M
    ys = np.array([p["y"] for p in points]) * YARDS_TO_M
    zs = np.array([p["elevation_ft"] for p in points]) * FEET_TO_M

    res_m = resolution_yards * YARDS_TO_M
    xi = np.arange(xs.min(), xs.max() + res_m, res_m)
    yi = np.arange(ys.min(), ys.max() + res_m, res_m)
    XI, YI = np.meshgrid(xi, yi)

    grid = griddata((xs, ys), zs, (XI, YI), method="cubic")

    # Fill NaN edges with nearest-neighbour (only at NaN positions)
    nan_mask = np.isnan(grid)
    if nan_mask.any():
        grid[nan_mask] = griddata((xs, ys), zs, (XI[nan_mask], YI[nan_mask]), method="nearest")

    return grid, xi, yi


def green_to_obj(
    grid: np.ndarray,
    xi: np.ndarray,
    yi: np.ndarray,
    output_path: Path,
) -> Path:
    """
    Exports an interpolated green surface as a Wavefront OBJ.

    Coordinates are local (origin at grid minimum), Y-up, meters.
    Attach to terrain by translating to the green's GPS position in Blender/Unity.
    """
    nr, nc = grid.shape
    XI, YI = np.meshgrid(xi, yi)

    x = XI.ravel()
    y = grid.ravel()
    z = -YI.ravel()

    ri = np.arange(nr - 1)
    ci = np.arange(nc - 1)
    RI, CI = np.meshgrid(ri, ci, indexing="ij")
    RI, CI = RI.ravel(), CI.ravel()

    v0 = RI * nc + CI + 1
    v1 = (RI + 1) * nc + CI + 1
    v2 = RI * nc + (CI + 1) + 1
    v3 = (RI + 1) * nc + (CI + 1) + 1

    n_verts = len(x)
    n_faces = len(RI) * 2

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("# Green surface mesh\n")
        f.write(f"# {n_verts} vertices, {n_faces} faces\n")
        verts = np.column_stack([x, y, z])
        f.write("".join("v %.3f %.3f %.3f\n" % (r[0], r[1], r[2]) for r in verts))
        _write_obj_quads(f, v0, v1, v2, v3)

    print(f"  Saved green OBJ: {output_path.name} ({n_verts} verts, {n_faces} faces)")
    return output_path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_meshes(slug: str, terrain_step_m: float = 5.0) -> dict:
    """Full mesh generation pipeline for one course."""
    heightmap_path = (REPO_ROOT / "courses" / slug / "data" / "processed"
                      / "heightmaps" / f"{slug}_terrain.tif")
    grids_dir = REPO_ROOT / "courses" / slug / "data" / "processed" / "grids"
    out_terrain = REPO_ROOT / "courses" / slug / "output" / "terrain"
    out_greens = REPO_ROOT / "courses" / slug / "output" / "greens"

    if not heightmap_path.exists():
        print(f"ERROR: Terrain heightmap not found: {heightmap_path}")
        print("  Run pipeline/01_lidar.py first.")
        sys.exit(1)

    # --- Terrain ---
    print(f"\nLoading heightmap: {heightmap_path.name}")
    data, meta = load_heightmap(heightmap_path)
    print(f"  Size:      {meta['width_px']}x{meta['height_px']}px  "
          f"({meta['width_m']:.0f}x{meta['height_m']:.0f}m)")
    print(f"  Elevation: {meta['elev_min']:.1f}–{meta['elev_max']:.1f}m  "
          f"(range: {meta['elev_range_m']:.1f}m)")

    print("\nExporting terrain assets...")
    height_scale = export_16bit_png(data, meta, out_terrain / f"{slug}_heightmap_16bit.png")
    export_terrain_metadata(meta, height_scale, out_terrain / f"{slug}_terrain_meta.json")
    print(f"\nBuilding terrain mesh ({terrain_step_m}m resolution)...")
    heightmap_to_obj(data, meta, out_terrain / f"{slug}_terrain.obj", step_m=terrain_step_m)

    # --- Greens ---
    green_jsons = sorted(grids_dir.glob("hole_*_green.json")) if grids_dir.exists() else []

    if green_jsons:
        print(f"\nProcessing {len(green_jsons)} green grid(s)...")
        for json_path in green_jsons:
            hole_num = json_path.stem.split("_")[1]
            points = load_green_grid(json_path)
            if not points:
                print(f"  hole {hole_num}: no points, skipping")
                continue
            try:
                grid, xi, yi = interpolate_green_surface(points)
                green_to_obj(grid, xi, yi, out_greens / f"hole_{hole_num}_green.obj")
            except Exception as e:
                print(f"  hole {hole_num}: failed — {e}")
    else:
        print("\nNo green grids found — run 02_puttview_ocr.py when PuttView book is available.")

    return {
        "heightmap_png": out_terrain / f"{slug}_heightmap_16bit.png",
        "terrain_meta": out_terrain / f"{slug}_terrain_meta.json",
        "terrain_obj": out_terrain / f"{slug}_terrain.obj",
        "greens_processed": len(green_jsons),
    }


def main():
    parser = argparse.ArgumentParser(description="Generate meshes from terrain and green data")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--terrain-step", type=float, default=5.0,
                        help="OBJ mesh resolution in meters (default: 5.0)")
    args = parser.parse_args()

    result = process_meshes(args.course, args.terrain_step)
    print(f"\nDone.")
    print(f"  Heightmap PNG: {result['heightmap_png']}")
    print(f"  Terrain meta:  {result['terrain_meta']}")
    print(f"  Terrain OBJ:   {result['terrain_obj']}")
    if result["greens_processed"]:
        print(f"  Greens: {result['greens_processed']} hole(s) processed")


if __name__ == "__main__":
    main()
