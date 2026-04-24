# Stage 3: Aerial Imagery Acquisition

**Script:** `pipeline/03_imagery.py`

---

## What it does

Downloads NAIP aerial imagery for the course via Microsoft Planetary Computer's free STAC API, mosaics the tiles, crops to the course bounding box, and exports an RGB PNG texture for use in GSPro Course Forge. The PNG texture is applied to the terrain mesh to give the course a realistic aerial-photo appearance.

---

## What is NAIP?

NAIP (National Agriculture Imagery Program) is a USDA aerial photography program that covers the entire contiguous US. Key properties:

- **Resolution**: 30cm–1m per pixel (Massachusetts typically 0.6m)
- **Bands**: RGB + NIR (near-infrared) — pipeline uses RGB only
- **Coverage**: Annual acquisitions for most states; Massachusetts has coverage through at least 2023
- **Cost**: Free via Planetary Computer, no API key required

---

## Usage

```bash
# Default run — 1.5km buffer, 8192px max output
python pipeline/03_imagery.py --course tedesco

# Wider coverage area
python pipeline/03_imagery.py --course tedesco --buffer-km 2.0

# Smaller texture for faster Course Forge iteration
python pipeline/03_imagery.py --course tedesco --max-png-pixels 4096
```

---

## Inputs

| Source | Details |
|--------|---------|
| `config/course.json` | `location.latitude`, `location.longitude` |
| Planetary Computer STAC API | Free, no auth. Live query at runtime. |

### Key parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--buffer-km` | 1.5 | BBox buffer around course center in km |
| `--max-png-pixels` | 8192 | Max pixel dimension on the longer edge of output PNG |

For GSPro, 4096px is usually sufficient for course textures; 8192px gives finer detail at the cost of file size (~40–80 MB PNG for a typical course).

---

## Outputs

| File | Description |
|------|-------------|
| `courses/<slug>/data/raw/imagery/*.tif` | Cached raw NAIP tile GeoTIFFs |
| `courses/<slug>/data/processed/textures/<slug>_imagery.tif` | Mosaiced + cropped GeoTIFF |
| `courses/<slug>/data/processed/textures/<slug>_imagery.png` | RGB PNG texture for Course Forge |

---

## How it works

### 1. STAC query
`query_naip()` sends a POST to the Planetary Computer STAC search endpoint for the `naip` collection with the course bounding box. Returns feature metadata for all overlapping tiles.

### 2. Tile selection
`select_best_naip_tiles()` selects all tiles from the **most recent acquisition year** only. Mixing tiles from different years creates visible color seams where lighting and vegetation conditions differ.

### 3. URL signing
Each tile's `image` asset href must be signed before downloading. `sign_url()` calls the Planetary Computer signing endpoint (no auth required) which returns a short-lived SAS token URL. Signing happens one tile at a time immediately before download.

### 4. Download
`download_tile()` streams each signed tile to `data/raw/imagery/`. Files are skipped if already cached. Shows progress percentage.

### 5. Mosaic and crop
`mosaic_and_crop()` uses `rasterio.merge` to merge all tiles into a single raster and crop to the course bounding box in a single pass. The WGS84 bbox is reprojected to the tile's native CRS (UTM) before merging. Output is a compressed GeoTIFF with LZW compression.

All opened datasets are guaranteed to be closed via `try/finally`, even if the merge operation fails.

### 6. PNG export
`export_png()` reads all bands in a single pass, extracts RGB (bands 1–3), and saves as PNG. If the longer image dimension exceeds `max_png_pixels`, the image is downscaled using Lanczos resampling before saving.

---

## Using the texture in Course Forge

After running this stage you have two files:
- `<slug>_imagery.tif` — GeoTIFF with full spatial metadata (for reference/GIS use)
- `<slug>_imagery.png` — RGB PNG ready to import into GSPro Course Forge

In Course Forge, import the PNG as the terrain texture. The PNG is aligned to the same bounding box as the terrain heightmap, so UV coordinates should match directly when both are sized to the same terrain dimensions.

---

## Troubleshooting

**No NAIP tiles found**
- Try `--buffer-km 2.0` — rare, but some course locations near state boundaries may have sparse tile coverage
- Check directly: https://planetarycomputer.microsoft.com/dataset/naip
- NAIP covers only the contiguous 48 states — not applicable for Hawaii/Alaska courses

**Download fails with HTTP error**
- SAS tokens from the signing endpoint are short-lived (~1 hour) — if you received the signed URLs and then left the download running for a long time, restart
- Check network connectivity; Planetary Computer is generally very reliable

**Color seams visible in the mosaic**
- This means tiles from different years were mixed — verify `select_best_naip_tiles()` is working correctly by checking the `tile_year` value printed during the run
- If all tiles report the same year and seams still appear, it may be within-year lighting variation (rare for NAIP)

**PNG too large / too small**
- Adjust `--max-png-pixels`: `4096` for fast iteration, `8192` for final build, `16384` for extreme detail (very large file)

---

## Dependencies

```
pip install requests rasterio numpy pillow pyproj
```
