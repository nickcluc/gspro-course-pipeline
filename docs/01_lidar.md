# Stage 1: LiDAR Acquisition & Terrain Heightmap

**Script:** `pipeline/01_lidar.py`

---

## What it does

Downloads USGS 3DEP LiDAR point cloud tiles for the course bounding box, processes them through a PDAL pipeline to extract ground returns, and produces a terrain heightmap GeoTIFF. The heightmap is the foundation for everything else — Stage 4 uses it to produce the 16-bit PNG and OBJ mesh that GSPro Course Forge ingests.

---

## Usage

```bash
# Full run — downloads LiDAR and processes it
python pipeline/01_lidar.py --course tedesco

# If .laz files are already downloaded, skip re-downloading
python pipeline/01_lidar.py --course tedesco --skip-download

# Just see what LiDAR products are available, don't process
python pipeline/01_lidar.py --course tedesco --list-only
```

---

## Inputs

| Source | Details |
|--------|---------|
| `config/course.json` | `location.latitude`, `location.longitude`, `lidar.bbox_buffer_km`, `lidar.target_resolution_m`, `lidar.epsg_output` |
| USGS TNM API | Free, no auth required. Queried live at runtime. |

### Key config values

```json
"lidar": {
  "bbox_buffer_km": 1.5,        // How far beyond course center to include
  "target_resolution_m": 1.0,   // Output DEM resolution in meters
  "epsg_output": 32619          // UTM Zone 19N — correct for Massachusetts
}
```

---

## Outputs

| File | Description |
|------|-------------|
| `courses/<slug>/data/raw/lidar/*.laz` | Raw downloaded LiDAR tiles |
| `courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif` | Float32 GeoTIFF terrain DEM |

The terrain `.tif` is projected in the EPSG specified by config (UTM Zone 19N for Tedesco). Elevation values are in meters.

---

## How it works

### 1. Bounding box
`bbox_from_center()` (in `utils.py`) converts the course center lat/lon + buffer distance into a WGS84 bounding box. The buffer should be large enough to cover the entire course perimeter with some margin.

### 2. USGS TNM query
`query_tnm()` calls the USGS The National Map API for all `"Lidar Point Cloud (LPC)"` products overlapping the bbox. Returns metadata including tile URLs, publication dates, and ground sample distance (GSD).

### 3. Product selection
`select_best_products()` picks **all tiles from the single best survey** — highest resolution (lowest GSD) with tie-breaking by most recent date. Critically, all tiles from the same survey are returned together; mixing surveys from different years causes seam artifacts in the DEM.

### 4. Download
`download_file()` streams each `.laz` tile to `data/raw/lidar/`. Files are skipped if they already exist. Shows download progress as a percentage.

### 5. PDAL pipeline
`build_pdal_pipeline()` constructs a JSON pipeline that:
1. Reads all `.laz` files (CRS taken from file headers — don't force `in_srs`)
2. Reprojects to UTM (the EPSG from config)
3. Crops to the bounding box in UTM coordinates
4. Runs the SMRF ground filter (`slope=0.15, window=18m, threshold=0.5, scalar=1.25`)
5. Keeps only Classification=2 (ground) points
6. Writes a compressed GeoTIFF DEM (`output_type=mean`, 1m resolution, LZW+tiled)

The pipeline JSON is written to a temp file and run via `pdal pipeline <file>`.

### 6. Post-processing
`postprocess_heightmap()` fills NoData voids (from tree canopy gaps where LiDAR ground returns were blocked) using `rasterio.fill.fillnodata` with a 50-pixel search radius, then applies a 3×3 uniform smoothing filter to reduce point cloud noise.

---

## SMRF filter parameters

The Simple Morphological Filter parameters are tuned for golf course terrain:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `slope` | 0.15 | Gentle slopes — golf greens/fairways are not steep |
| `window` | 18.0 | 18m window catches large flat areas like fairways |
| `threshold` | 0.5 | 0.5m height threshold for above-ground point rejection |
| `scalar` | 1.25 | Mild scalar — preserve gentle undulations |

These may need tuning for heavily wooded holes where returns are sparse.

---

## Troubleshooting

**No LiDAR products found**
- Try increasing `bbox_buffer_km` — some courses near state boundaries have limited tile coverage
- Check the USGS LiDAR Explorer: https://apps.nationalmap.gov/lidar-explorer/
- Massachusetts has excellent 3DEP coverage; this is unlikely to be an issue for Tedesco

**PDAL pipeline fails**
- Ensure PDAL is installed system-wide: `pdal --version`
- Check the error output — common causes: CRS mismatch (verify `epsg_output`), corrupt download
- Run `--skip-download` and manually inspect `.laz` files if needed

**Many NoData holes in output DEM**
- Dense tree canopy blocking ground returns — the void fill in post-processing helps, but heavily wooded holes may need manual attention in Blender
- Try wider SMRF window or reduced slope threshold

---

## Dependencies

```
pip install requests rasterio scipy pyproj
```

PDAL must be installed system-wide (not via pip):
- macOS: `brew install pdal`
- Full install docs: https://pdal.io/en/stable/install.html
