# GSPro Course Pipeline — Project Planning

## Project Goal

Build Tedesco Country Club (Marblehead, MA) as a custom course for the GSPro golf simulator platform, using a reusable data-driven pipeline that can be applied to any course in the future.

---

## Builder Profile

- Knows Python — proficient but prefers not to use it; will use it because it's required
- No Blender experience — will learn basics for visual QA only, not manual modeling
- Solo build with Claude assistance
- Preference: code does the work, human makes visual decisions

---

## Guiding Principles

- **Code-first**: every processing step is a Python script, not a GUI operation
- **Config-driven**: swap `course.json` to run the pipeline on a different course
- **Data fidelity**: accuracy matters — especially green undulation
- **Solo build**: one person + Claude assistance; minimize manual steps

---

## Course: Tedesco Country Club

- **Location**: Marblehead, Massachusetts
- **Type**: Private club (builder is a member — full on-site access)
- **Holes**: 18, Par 72
- **GSPro status**: Not yet built — original build

---

## GSPro Course Build Phases (High Level)

```
Phase 1: Data Acquisition
  - LiDAR terrain data, aerial imagery, PuttView book, on-site photography

Phase 2: Terrain & Layout
  - Process elevation data into heightmap
  - Rough in hole routing, fairway corridors, green complexes, tee boxes
  - Establish correct scale (everything else depends on this)

Phase 3: Course Shaping
  - Fine-tune terrain: bunker edges, green undulation, mounding
  - Place hazards, water features, OB boundaries
  - Define playable surfaces (fairway, rough, fringe, green, sand)

Phase 4: Assets & Visuals
  - Textures for all surface types
  - Vegetation placement (trees, shrubs, rough grass)
  - Structures (clubhouse, bridges, cart paths, fencing)
  - Skybox, lighting, environment

Phase 5: GSPro Integration
  - Export to GSPro-compatible format via Course Forge
  - Define surface physics properties per surface type
  - Pin positions, tee positions, hole metadata
  - Collision and boundary setup

Phase 6: Testing & Polish
  - Play-test each hole for accuracy
  - Fix physics and visual issues
  - Final release packaging
```

---

## Why PuttView Over Golf Logix

Golf Logix uses a **color heat map only** (5-yard grid) — extracting elevation requires sampling colors and mapping them to the scale bar, which is error-prone due to scan color shift and color band resolution (~0.5ft bands).

PuttView prints **actual numeric elevation values** at every grid point (3-yard grid, 0.1ft precision). Extraction is OCR instead of color sampling — dramatically more accurate and simpler to automate. PuttView also includes a companion hole diagram with full layout data (fairway shape, bunkers, water, elevation markers) that Golf Logix does not.

---

## Data Sources

### PuttView Book (PURCHASED)
- Numeric elevation values at every **3-yard grid point** on every green
- Color heat map overlay
- Break arrows (putt direction per grid square)
- Full hole diagrams: fairway shape, bunker positions, water, trees, elevation markers along fairway
- **How to use**: Scan at 600 DPI+ as PNG → OCR numeric values → JSON elevation grids

### USGS LiDAR (3DEP)
- Massachusetts has excellent coverage, likely 1m resolution
- Free download via USGS The National Map
- Covers terrain, fairways, rough, overall course shape
- **Limitation**: tree canopy on wooded holes may obscure ground returns — supplement with on-site photos

### Satellite Imagery
- Aerial reference for textures and layout
- Sources: USGS, Google Earth (reference only), Bing Maps

### On-Site Photography
- Member access = can walk every hole
- Capture: bunker faces/lips, green surrounds, tree lines, structures, cart paths
- Low-angle green shots reveal undulation the data may miss

---

## Tool Stack

| Tool | Purpose |
|------|---------|
| Python | All scripting and pipeline orchestration |
| PDAL | LiDAR point cloud processing |
| GDAL + rasterio | Raster/heightmap processing |
| numpy + scipy | Mesh math, interpolation |
| OpenCV | Image processing for PuttView scans |
| pytesseract | OCR of numeric elevation values from PuttView |
| trimesh | 3D mesh generation and export |
| Blender (bpy API) | 3D assembly, visual QA, scripted via Python |
| GSPro Course Forge | Final Unity-based integration and packaging |

---

## Pipeline Stages

```
Stage 1: LiDAR Acquisition + Processing
  Input:  Course coordinates (lat/lon bounding box)
  Script: pipeline/01_lidar.py
  Output: courses/tedesco/data/processed/heightmaps/terrain.tif

Stage 2: PuttView OCR
  Input:  Scanned PNG pages from PuttView book (600 DPI+)
  Script: pipeline/02_puttview_ocr.py
  Output: courses/tedesco/data/processed/grids/hole_XX_green.json
  Format: { "hole": N, "grid_spacing_yards": 3, "points": [{x, y, elevation_ft}] }

Stage 3: Satellite Imagery Processing
  Input:  Downloaded aerial imagery tiles
  Script: pipeline/03_imagery.py
  Output: courses/tedesco/data/processed/textures/

Stage 4: Mesh Generation
  Input:  Terrain heightmap + green elevation grids
  Script: pipeline/04_mesh_gen.py
  Output: Terrain mesh + per-green meshes (.obj or .fbx)

Stage 5: Blender Assembly (scripted via bpy)
  Input:  All meshes + textures
  Script: pipeline/05_blender_assembly.py
  Output: courses/tedesco/output/tedesco.blend

Stage 6: GSPro Export
  Input:  Blender scene
  Script: pipeline/06_gspro_export.py
  Output: courses/tedesco/output/gspro/ (final course package)
```

---

## PuttView Import Detail

The PuttView book contains two views per green:
1. **Numeric grid** — actual elevation values (e.g. `3.4`, `5.8`) printed at each 3-yard grid point
2. **Heat map** — color overlay confirming high/low zones

Extraction pipeline:
1. Flatbed scan at 600 DPI minimum, output as PNG (not JPEG — compression corrupts color values)
2. Per-page: detect green boundary, locate grid, OCR each cell value
3. Output one JSON file per green with `{x, y, elevation_ft}` at 3-yard intervals
4. Validate using heat map color — OCR values should match color gradient

The hole diagram pages also contain:
- Fairway outline and width
- Bunker positions and shapes
- Water hazard geometry
- Elevation change markers along the fairway (+2, +1, 0, -1, -2 feet)
- Distances to key landing areas

---

## Project Structure

```
gspro-course-pipeline/
  config/
    course.json              # Course metadata and coordinates
  courses/
    tedesco/
      data/
        raw/
          lidar/             # Raw .laz / .las files from USGS
          imagery/           # Satellite image tiles
          puttview/          # Scanned PNG pages
        processed/
          heightmaps/        # Terrain rasters
          grids/             # Per-green elevation JSON files
          textures/          # Processed texture maps
      output/
        gspro/               # Final GSPro course package
  pipeline/
    01_lidar.py
    02_puttview_ocr.py
    03_imagery.py
    04_mesh_gen.py
    05_blender_assembly.py
    06_gspro_export.py
  docs/
    PLANNING.md              # This file
  README.md
```

---

## Reusability

Every pipeline script is course-agnostic. To build a new course:
1. Create `courses/<slug>/` directory structure
2. Populate `config/course.json` with new course details
3. Acquire data (LiDAR, PuttView book for that course, imagery)
4. Run pipeline

Courses that have PuttView coverage get the full precision green treatment automatically.

---

## Immediate Next Steps (book not yet arrived)

1. **Confirm Tedesco coordinates** — needed for LiDAR tile download
2. **Download USGS LiDAR** for Tedesco bounding box
3. **Write + run `01_lidar.py`** — terrain heightmap pipeline
4. **Pull satellite imagery** for course footprint
5. **Design `holes.json` schema** — per-hole metadata structure

## When PuttView Book Arrives

1. Scan all pages at 600 DPI (flatbed scanner preferred)
2. Write + run `02_puttview_ocr.py`
3. Validate OCR output against heat map colors
4. Generate green meshes

---

## Open Questions

- [ ] Exact lat/lon coordinates for Tedesco (needed for LiDAR)
- [ ] Does Tedesco have a drone policy? (useful for fairway contouring)
- [ ] GSPro Course Forge exact file format requirements (need to research)
- [ ] Blender version compatibility with bpy scripting approach
- [ ] Surface property definitions for GSPro (what physics parameters are needed per surface type)
