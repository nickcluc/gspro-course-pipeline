# Stage 4: Mesh Generation

**Script:** `pipeline/04_mesh_gen.py`

---

## What it does

Converts processed terrain and green data into 3D assets:

1. **16-bit heightmap PNG** — the terrain input for GSPro Course Forge, with a companion JSON of the exact numbers to enter in Course Forge
2. **Terrain OBJ** — a low-poly mesh of the full course for visual QA in Blender
3. **Per-hole green OBJ** — one interpolated surface mesh per green, generated from PuttView elevation grids

---

## Usage

```bash
# Full run — terrain + all available greens
python pipeline/04_mesh_gen.py --course tedesco

# Denser terrain mesh for closer Blender inspection (smaller step = more polygons)
python pipeline/04_mesh_gen.py --course tedesco --terrain-step 2
```

The greens are processed automatically if `data/processed/grids/hole_XX_green.json` files exist. If not, the terrain-only outputs are generated and the greens are skipped with a message.

---

## Inputs

| File | Description | Required |
|------|-------------|----------|
| `courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif` | Terrain DEM from Stage 1 | Yes |
| `courses/<slug>/data/processed/grids/hole_*_green.json` | Per-green grids from Stage 2 | No |

---

## Outputs

| File | Description |
|------|-------------|
| `courses/<slug>/output/terrain/<slug>_heightmap_16bit.png` | 16-bit grayscale PNG for Course Forge |
| `courses/<slug>/output/terrain/<slug>_terrain_meta.json` | Course Forge import settings |
| `courses/<slug>/output/terrain/<slug>_terrain.obj` | Full terrain mesh (Blender preview) |
| `courses/<slug>/output/greens/hole_XX_green.obj` | Per-hole green surface mesh |

---

## How it works

### Terrain: 16-bit PNG

`export_16bit_png()` normalizes the terrain elevation range to 0–65535 and writes a 16-bit grayscale PNG. The normalization is linear:

```
pixel_value = (elevation - elev_min) / elev_range * 65535
```

NaN pixels (water/nodata) are set to `elev_min` (maps to 0 — sea level or lowest point). This PNG is written with rasterio so it retains CRS and transform metadata, though Course Forge only uses the raw pixel values.

**Important:** the `height_scale_m` return value (the elevation range in meters) is what you enter as "Terrain Height" in Course Forge. It's written to `terrain_meta.json` with a 2m safety margin added so the max elevation never clips at 65535.

### Terrain: metadata JSON

`export_terrain_metadata()` writes the exact values to enter in GSPro Course Forge when importing the heightmap:

```json
{
  "course_forge_settings": {
    "terrain_width_m": 3050.0,
    "terrain_length_m": 2980.0,
    "terrain_height_m": 48.3,
    "heightmap_resolution": 3050
  },
  ...
}
```

Enter these values in Course Forge → Terrain → Width, Length, and Height Scale. Getting these wrong will cause the terrain to be scaled incorrectly.

### Terrain: OBJ mesh

`heightmap_to_obj()` samples the DEM at `--terrain-step` meter intervals and writes a Wavefront OBJ mesh for Blender preview. The coordinate system is Y-up, right-handed:

- X = east (meters from terrain center)
- Y = elevation (meters)
- Z = -north (meters from terrain center)

At the default 5m step, a 3km terrain produces ~600×600 vertices. At 2m, it's ~1500×1500. Vertices are written in batch using numpy string formatting for speed.

### Greens: interpolation

`interpolate_green_surface()` takes the sparse 3-yard PuttView grid points and interpolates to a dense 0.5-yard grid using scipy's `griddata` with cubic method. NaN values at the grid edges (where cubic interpolation can't extrapolate) are filled with nearest-neighbour interpolation — only the NaN positions are re-interpolated, not the full grid.

Coordinate conversion:
- Input: x/y in yards, elevation in feet
- Output: x/y in meters (`× 0.9144`), elevation in meters (`× 0.3048`)

### Greens: OBJ mesh

`green_to_obj()` exports the interpolated surface as a local-coordinate OBJ (origin at grid minimum). In Blender/Unity, translate the green mesh to the GPS position of the green to place it correctly on the terrain.

The same quad-to-triangle face generation logic is shared between terrain and green OBJ writers via `_write_obj_quads()`.

---

## Understanding the Course Forge settings

### Why 16-bit?

Unity terrains (which Course Forge uses) take 16-bit heightmaps. The full 65536 levels give sub-millimeter precision for a 50m elevation range, which is more than sufficient for a golf course.

### terrain_height_m

This value is **not** the maximum elevation — it's the **elevation range** (max minus min) plus a 2m safety margin. If you set it too small, the terrain peaks will be clipped. If you set it too large, the terrain will appear flatter than it really is (the same 65535-pixel range represents more meters).

Use the value from `terrain_meta.json` exactly.

### Terrain width and length

These are the real-world dimensions of the terrain in meters. Course Forge uses these to set the terrain's physical size in Unity. Matching these to the actual DEM dimensions ensures that distances and GPS positions are correct.

---

## Green mesh placement in Blender

Each `hole_XX_green.obj` has its origin at the minimum x/y coordinate of that green's data. To place it on the terrain:

1. Import the terrain OBJ and green OBJ into Blender
2. Find the GPS position of the green's lowest x/y corner (from the PuttView grid origin)
3. Convert that GPS coordinate to the same UTM CRS as the terrain (EPSG:32619 for Tedesco)
4. Subtract the terrain's origin coordinate to get the offset in terrain-local space
5. Set the green mesh's location to that offset

In practice, visual alignment in Blender is often sufficient — import both, visually position the green mesh on the terrain surface.

---

## Troubleshooting

**`ERROR: Terrain heightmap not found`**
- Run Stage 1 first: `python pipeline/01_lidar.py --course tedesco`

**Green mesh has visible NaN patches**
- The nearest-neighbour fallback should prevent this — check if `interpolate_green_surface` raised an exception for that hole
- Very sparse grids (few valid OCR readings) may produce poor interpolation; re-scan and re-run Stage 2

**Terrain OBJ is enormous / slow to open in Blender**
- Increase `--terrain-step` (default 5m). 5m is already coarse enough for course QA; increase to 10m for faster iteration

**Course Forge terrain looks wrong scale**
- Verify you used the exact values from `terrain_meta.json`
- Especially check `terrain_height_m` — this is the most common source of scale errors

---

## Dependencies

```
pip install rasterio numpy scipy
```
