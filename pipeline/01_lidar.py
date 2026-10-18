"""
01_lidar.py — LiDAR Acquisition + Terrain Heightmap Generation

Downloads USGS 3DEP LiDAR point cloud data for the course bounding box,
filters ground returns, and outputs a terrain heightmap GeoTIFF.

Usage:
    python pipeline/01_lidar.py --course tedesco
    python pipeline/01_lidar.py --course tedesco --skip-download  # if .laz files already present

Dependencies:
    pip install requests pdal numpy rasterio scipy
    PDAL must also be installed system-wide: https://pdal.io/en/stable/install.html
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from utils import REPO_ROOT, bbox_from_center, bbox_to_string

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_paths(slug: str) -> dict:
    course_dir = REPO_ROOT / "courses" / slug
    return {
        "raw_lidar": course_dir / "data" / "raw" / "lidar",
        "heightmaps": course_dir / "data" / "processed" / "heightmaps",
        "config": course_dir / "course.json",
    }


# ---------------------------------------------------------------------------
# USGS TNM API — find available LiDAR products
# ---------------------------------------------------------------------------

TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"

# USGS dataset tag for 3DEP LiDAR point clouds
LIDAR_DATASET = "Lidar Point Cloud (LPC)"


def _fetch_tnm_json(params: dict) -> dict:
    """Fetches TNM API with up to 3 retries on invalid JSON (transient backend outages)."""
    import time
    for attempt in range(1, 4):
        resp = requests.get(TNM_API, params=params, timeout=30)
        resp.raise_for_status()
        try:
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            if attempt < 3:
                print(f"  TNM API returned invalid JSON (attempt {attempt}/3), retrying in 15s...")
                time.sleep(15)
            else:
                print("ERROR: USGS TNM API returned invalid JSON after 3 attempts (backend outage).")
                print(f"  Response: {resp.text[:300]}")
                print("  Check https://www.usgs.gov/core-science-systems/ngp/tnm-delivery")
                sys.exit(1)
    sys.exit(1)  # unreachable, satisfies type checker


def query_tnm(bbox: dict) -> list[dict]:
    """
    Query USGS The National Map API for LiDAR products within the bounding box.
    Returns a list of product metadata dicts.
    """
    params = {
        "datasets": LIDAR_DATASET,
        "bbox": bbox_to_string(bbox),
        "outputFormat": "JSON",
        "max": 50,
    }

    print("Querying USGS TNM API for LiDAR coverage...")
    data = _fetch_tnm_json(params)
    items = data.get("items", [])
    print(f"  Found {len(items)} LiDAR product(s)")
    return items


def select_best_products(items: list[dict]) -> list[dict]:
    """
    From all available products, select the single best dataset (most recent,
    highest resolution) and return all tiles from that dataset.

    Multiple tiles from the same survey are needed to cover the full bbox.
    Mixing tiles from different surveys creates seam artifacts.
    """
    if not items:
        return []

    def sort_key(item):
        # Negate GSD so lower GSD = higher score (better resolution wins).
        # Newer date = higher score.
        gsd = float(item.get("groundSampleDistance") or 999)
        pub_date = item.get("publicationDate") or ""
        return (-gsd, pub_date)

    # Find the single best product by GSD + date
    best = max(items, key=sort_key)
    best_gsd = float(best.get("groundSampleDistance") or 999)
    best_date = best.get("publicationDate") or ""

    # Return all tiles that share the same dataset (same GSD + date)
    same_dataset = [
        item for item in items
        if float(item.get("groundSampleDistance") or 999) == best_gsd
        and (item.get("publicationDate") or "") == best_date
    ]

    # Extract the dataset name from the best product title for display
    # Titles follow pattern: "USGS Lidar Point Cloud <DATASET_NAME> <TILE_ID>"
    best_title = best.get("title", "")
    best_dataset = " ".join(best_title.split()[4:-1]) if len(best_title.split()) > 5 else best_title
    print(f"  Selected dataset: {best_dataset} ({best_date}), {len(same_dataset)} tile(s)")
    return same_dataset


def print_products(items: list[dict]):
    print("\nAvailable LiDAR products:")
    for i, item in enumerate(items):
        title = item.get("title", "Unknown")
        gsd = item.get("groundSampleDistance", "?")
        pub = item.get("publicationDate", "?")
        size = item.get("sizeInBytes", 0)
        size_mb = size / (1024 * 1024) if size else 0
        url = item.get("downloadURL", "")
        print(f"  [{i}] {title}")
        print(f"       GSD: {gsd}m  |  Published: {pub}  |  Size: {size_mb:.1f} MB")
        print(f"       URL: {url}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

DOWNLOAD_WORKERS = 4   # tiles fetched at once; USGS serves each connection slowly
DOWNLOAD_TRIES = 8


def download_file(url: str, dest: Path) -> Path:
    """
    Downloads a file from url to dest directory. Returns path to downloaded file.
    Skips if already complete. Downloads into a .part file and renames it only when
    complete, so an interrupted download is resumed (HTTP Range) on the next try or
    the next run, instead of a half file being mistaken for a finished one.
    """
    filename = url.split("/")[-1].split("?")[0]
    out_path = dest / filename

    if out_path.exists():
        print(f"  Already exists, skipping: {out_path.name}", flush=True)
        return out_path

    dest.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with requests.get(url, stream=True, timeout=(30, 60), headers=headers) as r:
                if r.status_code == 416:  # nothing left to fetch
                    break
                r.raise_for_status()
                resumed = r.status_code == 206
                total = int(r.headers.get("content-length", 0)) + (have if resumed else 0)
                with open(part, "ab" if resumed else "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            if not total or part.stat().st_size >= total:
                break
        except requests.RequestException as e:
            print(f"  {filename}: connection dropped ({e.__class__.__name__}), "
                  f"resuming ({attempt}/{DOWNLOAD_TRIES})", flush=True)
            time.sleep(min(60, 5 * attempt))
    else:
        raise RuntimeError(f"Couldn't download {filename}; run again to resume.")
    part.rename(out_path)
    print(f"  Done: {out_path.name} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)", flush=True)
    return out_path


# ---------------------------------------------------------------------------
# PDAL pipeline — ground filter + heightmap
# ---------------------------------------------------------------------------

def bbox_to_utm(bbox: dict, epsg_out: int) -> dict:
    """
    Converts a WGS84 bounding box to the target projected CRS using pyproj.
    """
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg_out}", always_xy=True)
    xmin, ymin = transformer.transform(bbox["xmin"], bbox["ymin"])
    xmax, ymax = transformer.transform(bbox["xmax"], bbox["ymax"])
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def build_pdal_pipeline(laz_files: list[Path], bbox: dict, output_tif: Path, resolution_m: float, epsg_out: int) -> dict:
    """
    Builds a PDAL pipeline dict that:
    1. Reads all .laz/.las files (CRS read from file headers)
    2. Reprojects to target CRS (UTM Zone 19N)
    3. Crops to bounding box (in UTM coordinates)
    4. Classifies ground returns (SMRF filter)
    5. Keeps only ground points
    6. Writes a DEM GeoTIFF
    """
    readers = [{"type": "readers.las", "filename": str(f)} for f in laz_files]

    # Convert crop bounds from WGS84 to UTM so they match post-reprojection coords
    utm_bbox = bbox_to_utm(bbox, epsg_out)

    pipeline = {
        "pipeline": readers + [
            # Reproject first — use CRS from file header, don't force in_srs
            {
                "type": "filters.reprojection",
                "out_srs": f"EPSG:{epsg_out}"
            },
            # Crop to UTM bounding box after reprojection
            {
                "type": "filters.crop",
                "bounds": f"([{utm_bbox['xmin']},{utm_bbox['xmax']}],[{utm_bbox['ymin']},{utm_bbox['ymax']}])"
            },
            # Assign ground classification using Simple Morphological Filter
            {
                "type": "filters.smrf",
                "slope": 0.15,
                "window": 18.0,
                "threshold": 0.5,
                "scalar": 1.25
            },
            # Keep only ground points (classification 2)
            {
                "type": "filters.range",
                "limits": "Classification[2:2]"
            },
            # Write DEM as GeoTIFF
            {
                "type": "writers.gdal",
                "filename": str(output_tif),
                "resolution": resolution_m,
                "output_type": "mean",
                "gdalopts": "COMPRESS=LZW,TILED=YES"
            }
        ]
    }

    return pipeline


def run_pdal_pipeline(pipeline: dict) -> bool:
    """
    Writes pipeline to a temp JSON file and runs it via pdal pipeline CLI.
    Returns True on success.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(pipeline, f, indent=2)
        pipeline_path = f.name

    try:
        print(f"\nRunning PDAL pipeline...")
        result = subprocess.run(
            ["pdal", "pipeline", pipeline_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"PDAL error:\n{result.stderr}")
            return False
        print("PDAL pipeline complete.")
        return True
    finally:
        os.unlink(pipeline_path)


# ---------------------------------------------------------------------------
# Post-processing — fill voids, smooth
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# USGS 1 m DEM — the bare-earth surface USGS already made from the same Lidar
# ---------------------------------------------------------------------------

DEM_DATASET = "Digital Elevation Model (DEM) 1 meter"
NODATA = -9999.0


def _project_of(item: dict) -> str:
    """The 3DEP project a tile belongs to, from its download URL (.../Projects/<name>/...)."""
    parts = item.get("downloadURL", "").split("/")
    return parts[parts.index("Projects") + 1] if "Projects" in parts else item.get("title", "")


def select_dem_tiles(items: list[dict]) -> list[dict]:
    """GeoTIFF tiles from the most recently published project (the newest Lidar survey)."""
    tifs = [i for i in items if (i.get("format") or "").lower() == "geotiff" and i.get("downloadURL")]
    if not tifs:
        return []
    newest = max(tifs, key=lambda i: i.get("publicationDate") or "")
    project = _project_of(newest)
    return [i for i in tifs if _project_of(i) == project]


def build_from_dem(bbox: dict, output_tif: Path, resolution_m: float, epsg_out: int) -> bool:
    """
    Writes the terrain GeoTIFF from USGS's 1 m bare-earth DEM instead of the raw point
    cloud. USGS builds these DEMs from the same 3DEP Lidar, already ground-classified, so
    PDAL isn't needed. Only the course's area is read, straight from the cloud-hosted
    GeoTIFFs (HTTP range requests), so nothing large is downloaded.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    print("Querying USGS TNM API for 1 m DEM coverage...")
    items = _fetch_tnm_json({"datasets": DEM_DATASET, "bbox": bbox_to_string(bbox),
                             "outputFormat": "JSON", "max": 50}).get("items", [])
    tiles = select_dem_tiles(items)
    if not tiles:
        print("  No 1 m DEM covers this area.")
        return False
    print(f"  Using {len(tiles)} tile(s) from {_project_of(tiles[0])} "
          f"(published {tiles[0].get('publicationDate')})")

    utm = bbox_to_utm(bbox, epsg_out)
    west, north = np.floor(utm["xmin"]), np.ceil(utm["ymax"])
    width = int(np.ceil((utm["xmax"] - west) / resolution_m))
    height = int(np.ceil((north - utm["ymin"]) / resolution_m))
    transform = from_origin(west, north, resolution_m, resolution_m)
    out = np.full((height, width), NODATA, np.float32)

    env = rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5",
                       GDAL_HTTP_RETRY_DELAY="2", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
    with env:
        for t in tiles:
            url = t["downloadURL"]
            print(f"  Reading {url.split('/')[-1]}...", flush=True)
            part = np.full_like(out, NODATA)
            with rasterio.open("/vsicurl/" + url) as src:
                reproject(source=rasterio.band(src, 1), destination=part, src_transform=src.transform,
                          src_crs=src.crs, src_nodata=src.nodata, dst_transform=transform,
                          dst_crs=f"EPSG:{epsg_out}", dst_nodata=NODATA, resampling=Resampling.bilinear)
            fill = (out == NODATA) & (part != NODATA)
            out[fill] = part[fill]

    covered = float((out != NODATA).mean())
    print(f"  Covered {covered:.0%} of the course area")
    if covered == 0:
        return False
    output_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_tif, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
                       crs=f"EPSG:{epsg_out}", transform=transform, nodata=NODATA,
                       compress="LZW", tiled=True) as dst:
        dst.write(out, 1)
    return True


def postprocess_heightmap(tif_path: Path):
    """
    Fill NoData voids (from tree canopy gaps) using interpolation,
    then apply light smoothing.
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.fill import fillnodata
        from scipy.ndimage import uniform_filter
    except ImportError:
        print("Warning: rasterio/scipy not available, skipping post-processing")
        return

    print("Post-processing heightmap (filling voids, smoothing)...")

    with rasterio.open(tif_path, "r+") as ds:
        data = ds.read(1)
        nodata = ds.nodata

        # Fill voids caused by tree canopy blocking LiDAR ground returns
        mask = (data != nodata).astype("uint8")
        filled = fillnodata(data, mask=mask, max_search_distance=50, smoothing_iterations=2)

        # Light smoothing to reduce point cloud noise
        smoothed = uniform_filter(filled, size=3)

        ds.write(smoothed.astype(data.dtype), 1)

    print(f"  Heightmap saved: {tif_path}")
    print(f"  Size: {tif_path.stat().st_size / 1024 / 1024:.1f} MB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Download and process LiDAR for a course")
    parser.add_argument("--course", default="tedesco", help="Course slug (matches courses/ directory)")
    parser.add_argument("--skip-download", action="store_true", help="Skip download, use existing .laz files")
    parser.add_argument("--list-only", action="store_true", help="List available products and exit")
    parser.add_argument("--source", choices=("auto", "dem", "points"), default="auto",
                        help="dem: USGS 1 m bare-earth DEM (no PDAL, small download); points: raw "
                             "point cloud through PDAL; auto: points if PDAL is installed, else dem")
    args = parser.parse_args()

    # Load course config
    paths = get_paths(args.course)
    with open(paths["config"]) as f:
        config = json.load(f)

    lat = config["location"]["latitude"]
    lon = config["location"]["longitude"]
    buffer_km = config["lidar"]["bbox_buffer_km"]
    resolution_m = config["lidar"]["target_resolution_m"]
    epsg_out = config["lidar"]["epsg_output"]

    print(f"\nCourse: {config['name']}")
    print(f"Center: {lat}, {lon}")
    print(f"Buffer: {buffer_km} km")

    # Build bounding box
    bbox = bbox_from_center(lat, lon, buffer_km)
    print(f"Bounding box: {bbox_to_string(bbox)}")

    import shutil
    source = args.source
    if source == "auto":
        source = "points" if shutil.which("pdal") else "dem"
        if source == "dem":
            print("PDAL isn't installed: using USGS's 1 m bare-earth DEM (same Lidar, already processed).")
    if source == "dem" and not args.list_only:
        output_tif = paths["heightmaps"] / f"{args.course}_terrain.tif"
        if not build_from_dem(bbox, output_tif, resolution_m, epsg_out):
            print("\nNo DEM for this area; install PDAL and rerun with --source points.")
            sys.exit(1)
        postprocess_heightmap(output_tif)
        return

    # Query available LiDAR products
    items = query_tnm(bbox)

    if not items:
        print("\nNo LiDAR products found for this area.")
        print("Try increasing bbox_buffer_km in course.json, or check USGS LiDAR Explorer:")
        print("  https://apps.nationalmap.gov/lidar-explorer/")
        sys.exit(1)

    products = select_best_products(items)
    print_products(products)

    if args.list_only:
        sys.exit(0)

    # Download
    paths["raw_lidar"].mkdir(parents=True, exist_ok=True)
    paths["heightmaps"].mkdir(parents=True, exist_ok=True)

    laz_files = []

    if args.skip_download:
        laz_files = list(paths["raw_lidar"].glob("*.la[sz]"))
        print(f"\nUsing {len(laz_files)} existing file(s) in {paths['raw_lidar']}")
    else:
        print(f"\nDownloading {len(products)} file(s) to {paths['raw_lidar']} "
              f"({DOWNLOAD_WORKERS} at a time)", flush=True)
        urls = []
        for product in products:
            url = product.get("downloadURL")
            if not url:
                print(f"  No download URL for: {product.get('title')}")
                continue
            urls.append(url)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
            laz_files = list(pool.map(lambda u: download_file(u, paths["raw_lidar"]), urls))

    if not laz_files:
        print("No .laz/.las files to process.")
        sys.exit(1)

    # Build and run PDAL pipeline
    output_tif = paths["heightmaps"] / f"{args.course}_terrain.tif"
    pipeline = build_pdal_pipeline(laz_files, bbox, output_tif, resolution_m, epsg_out)
    success = run_pdal_pipeline(pipeline)

    if not success:
        print("\nPDAL pipeline failed. Check error output above.")
        sys.exit(1)

    # Post-process
    postprocess_heightmap(output_tif)

    print(f"\nDone. Terrain heightmap: {output_tif}")


if __name__ == "__main__":
    main()
