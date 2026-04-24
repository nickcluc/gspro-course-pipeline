"""
03_imagery.py — Aerial Imagery Acquisition Pipeline

Downloads NAIP aerial imagery via Microsoft Planetary Computer's STAC API.
NAIP (National Agriculture Imagery Program) provides 30cm–1m true-color RGB+IR
imagery for the entire US. Massachusetts has annual coverage through 2023.

The Planetary Computer STAC API is free with no account required. Downloads
use short-lived SAS tokens returned by the signing endpoint.

Usage:
    python pipeline/03_imagery.py --course tedesco
    python pipeline/03_imagery.py --course tedesco --buffer-km 1.5 --max-png-pixels 4096

Input:  config/course.json
Output: courses/<slug>/data/raw/imagery/          — downloaded tile GeoTIFFs
        courses/<slug>/data/processed/textures/<slug>_imagery.tif  — mosaiced + cropped
        courses/<slug>/data/processed/textures/<slug>_imagery.png  — RGB PNG for GSPro

Dependencies:
    pip install requests rasterio numpy pillow pyproj
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import requests
from pyproj import Transformer

from utils import REPO_ROOT, bbox_from_center, bbox_to_string, load_course_config

STAC_SEARCH_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
STAC_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"


# ---------------------------------------------------------------------------
# Bounding box utilities
# ---------------------------------------------------------------------------

def bbox_to_list(bbox: dict) -> list:
    """Returns [xmin, ymin, xmax, ymax] as required by STAC search."""
    return [bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"]]


# ---------------------------------------------------------------------------
# Planetary Computer STAC query
# ---------------------------------------------------------------------------

def query_naip(bbox: dict) -> list:
    """
    Searches Planetary Computer STAC for NAIP tiles overlapping bbox.
    Returns a list of STAC feature dicts.
    """
    payload = {
        "collections": ["naip"],
        "bbox": bbox_to_list(bbox),
        "limit": 100,
    }
    resp = requests.post(STAC_SEARCH_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("features", [])


def select_best_naip_tiles(items: list) -> list:
    """
    Returns all tiles from the most recent NAIP acquisition year.
    Mixing years across tiles causes color seams, so we take one year's set.
    """
    if not items:
        return []

    def naip_year(item):
        return str(item.get("properties", {}).get("naip:year", "0000"))

    most_recent = max(naip_year(i) for i in items)
    return [i for i in items if naip_year(i) == most_recent]


# ---------------------------------------------------------------------------
# Signing + download
# ---------------------------------------------------------------------------

def sign_url(href: str) -> str:
    """
    Returns a short-lived signed URL for a Planetary Computer asset.
    No authentication required — the signing endpoint is publicly accessible.
    """
    resp = requests.get(f"{STAC_SIGN_URL}?href={href}", timeout=15)
    resp.raise_for_status()
    return resp.json()["href"]


def download_tile(url: str, dest: Path) -> Path:
    """Downloads a tile to dest. Skips if already present. Shows progress."""
    if dest.exists():
        print(f"    Cached: {dest.name}")
        return dest

    print(f"    Downloading: {dest.name} ...")
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r      {downloaded / total * 100:.0f}%", end="", flush=True)
    print()
    return dest


# ---------------------------------------------------------------------------
# Mosaic + crop
# ---------------------------------------------------------------------------

def mosaic_and_crop(tile_paths: list, bbox_wgs84: dict, output_path: Path) -> Path:
    """
    Merges NAIP GeoTIFF tiles and crops to the WGS84 bounding box.

    Transforms the WGS84 bbox to match the tile CRS (UTM for NAIP),
    then passes as bounds to rasterio.merge for a combined merge+crop.
    """
    try:
        import rasterio
        from rasterio.merge import merge
    except ImportError:
        print("ERROR: rasterio not installed. Run: pip install rasterio")
        sys.exit(1)

    print(f"  Opening {len(tile_paths)} tile(s)...")
    datasets = [rasterio.open(p) for p in tile_paths]

    try:
        tile_crs = datasets[0].crs
        transformer = Transformer.from_crs("EPSG:4326", tile_crs, always_xy=True)
        xmin, ymin = transformer.transform(bbox_wgs84["xmin"], bbox_wgs84["ymin"])
        xmax, ymax = transformer.transform(bbox_wgs84["xmax"], bbox_wgs84["ymax"])

        print("  Mosaicking and cropping...")
        mosaic, transform = merge(datasets, bounds=(xmin, ymin, xmax, ymax))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        meta = datasets[0].meta.copy()
        meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": transform,
            "compress": "lzw",
            "count": mosaic.shape[0],
        })

        with rasterio.open(output_path, "w", **meta) as dst:
            dst.write(mosaic)
    finally:
        for ds in datasets:
            ds.close()

    h, w = mosaic.shape[1], mosaic.shape[2]
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved GeoTIFF: {output_path.name} ({w}x{h}px, {size_mb:.1f} MB)")
    return output_path


# ---------------------------------------------------------------------------
# PNG export
# ---------------------------------------------------------------------------

def export_png(tif_path: Path, png_path: Path, max_pixels: int = 8192) -> Path:
    """
    Exports the first three bands of a GeoTIFF as an RGB PNG.
    Downscales if the longer dimension exceeds max_pixels.
    GSPro texture maps work well at 4096–8192px on the long edge.
    """
    try:
        import rasterio
        from PIL import Image
    except ImportError:
        print("ERROR: rasterio or Pillow not installed. Run: pip install rasterio pillow")
        sys.exit(1)

    with rasterio.open(tif_path) as ds:
        n_bands = ds.count
        bands = ds.read()
    r = bands[0]
    g = bands[1] if n_bands >= 2 else r
    b = bands[2] if n_bands >= 3 else r

    rgb = np.stack([r, g, b], axis=-1).astype(np.uint8)
    img = Image.fromarray(rgb, mode="RGB")

    w, h = img.size
    if max(w, h) > max_pixels:
        scale = max_pixels / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        print(f"  Downscaled: {w}x{h} → {new_w}x{new_h}")

    png_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(png_path, "PNG")
    size_mb = png_path.stat().st_size / 1024 / 1024
    print(f"  Saved PNG: {png_path.name} ({img.width}x{img.height}px, {size_mb:.1f} MB)")
    return png_path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_imagery(
    slug: str,
    buffer_km: float = 1.5,
    max_png_pixels: int = 8192,
) -> dict:
    """
    Full imagery pipeline for one course.

    Returns:
        {
            "tif": Path,
            "png": Path,
            "tiles_downloaded": int,
            "tile_year": str,
        }
    """
    config = load_course_config(slug)
    lat = config["location"]["latitude"]
    lon = config["location"]["longitude"]
    bbox = bbox_from_center(lat, lon, buffer_km)

    print(f"\nCourse: {config['name']}")
    print(f"Center: {lat}, {lon}")
    print(f"BBox (WGS84): {bbox_to_string(bbox)}")

    raw_dir = REPO_ROOT / "courses" / slug / "data" / "raw" / "imagery"
    processed_dir = REPO_ROOT / "courses" / slug / "data" / "processed" / "textures"
    raw_dir.mkdir(parents=True, exist_ok=True)

    tif_out = processed_dir / f"{slug}_imagery.tif"
    png_out = processed_dir / f"{slug}_imagery.png"

    # Query
    print("\nQuerying Planetary Computer for NAIP imagery...")
    items = query_naip(bbox)
    print(f"  Found {len(items)} NAIP tile(s)")

    if not items:
        print("ERROR: No NAIP tiles found for this location.")
        print("  Try --buffer-km 2.0, or check https://planetarycomputer.microsoft.com/dataset/naip")
        sys.exit(1)

    best_tiles = select_best_naip_tiles(items)
    tile_year = best_tiles[0].get("properties", {}).get("naip:year", "unknown")
    print(f"  Selected {len(best_tiles)} tile(s) from {tile_year}")

    # Download
    print("\nDownloading tiles...")
    tile_paths = []
    for tile in best_tiles:
        href = tile.get("assets", {}).get("image", {}).get("href", "")
        if not href:
            print(f"  WARNING: No image asset for tile: {tile.get('id', 'unknown')}")
            continue
        signed = sign_url(href)
        tile_id = tile.get("id", Path(href).stem)
        dest = raw_dir / f"{tile_id}.tif"
        tile_paths.append(download_tile(signed, dest))

    if not tile_paths:
        print("ERROR: No tiles could be downloaded.")
        sys.exit(1)

    # Mosaic + crop
    print("\nMosaicking and cropping to course bbox...")
    mosaic_and_crop(tile_paths, bbox, tif_out)

    # Export PNG
    print("\nExporting PNG texture...")
    export_png(tif_out, png_out, max_png_pixels)

    return {
        "tif": tif_out,
        "png": png_out,
        "tiles_downloaded": len(tile_paths),
        "tile_year": str(tile_year),
    }


def main():
    parser = argparse.ArgumentParser(description="Download NAIP aerial imagery for a course")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--buffer-km", type=float, default=1.5,
                        help="BBox buffer around course center in km (default: 1.5)")
    parser.add_argument("--max-png-pixels", type=int, default=8192,
                        help="Max pixel dimension for output PNG (default: 8192)")
    args = parser.parse_args()

    result = process_imagery(args.course, args.buffer_km, args.max_png_pixels)
    print(f"\nDone.")
    print(f"  GeoTIFF: {result['tif']}")
    print(f"  PNG:     {result['png']}")


if __name__ == "__main__":
    main()
