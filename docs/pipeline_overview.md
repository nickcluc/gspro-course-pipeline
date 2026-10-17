# Pipeline Overview

This pipeline converts publicly available geospatial data, Arccos course maps and PuttView green maps into 3D course assets for GSPro Course Forge.

---

## Stage map

```
config/course.json
       │
       ▼
┌─────────────────────┐
│  00_arccos.py       │  Arccos API → hole maps (+ your rounds)
└─────────────────────┘
       │
       │  courses/<slug>/data/raw/arccos/holes/  +  rounds/
       │
┌─────────────────────┐
│  01_lidar.py        │  USGS TNM API → .laz tiles → PDAL → terrain.tif
└─────────────────────┘
       │
       │  courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif
       │
┌─────────────────────┐
│  02_puttview_ocr.py │  Scanned PNG → OpenCV + Tesseract → hole_XX_green.json
└─────────────────────┘
       │
       │  courses/<slug>/data/processed/grids/hole_XX_green.json
       │
┌─────────────────────┐
│  03_imagery.py      │  Planetary Computer STAC → NAIP tiles → imagery.png
└─────────────────────┘
       │
       │  courses/<slug>/data/processed/textures/<slug>_imagery.png
       │
┌─────────────────────┐
│  04_mesh_gen.py     │  terrain.tif + green JSONs → heightmap_16bit.png + OBJs
└─────────────────────┘
       │
       │  courses/<slug>/output/terrain/  +  output/greens/
       │
┌─────────────────────┐
│  05_layout.py       │  Arccos maps + terrain → holes.json, surfaces, OPCD SVG
└─────────────────────┘
       │
       │  courses/<slug>/output/layout/
       │
┌──────────────────────────┐
│  05b_photo_surfaces.py   │  aerial photo vs Arccos → missing bunkers, native grass
└──────────────────────────┘
       │
       │  courses/<slug>/extra_surfaces.geojson (reviewed) → back into 05
       │
┌──────────────────────────┐
│  06_blender_assembly.py  │  terrain + greens + layout → .blend + QA renders
└──────────────────────────┘
       │
       │  courses/<slug>/output/blender/
       │
       ▼
 07 GSPro export (planned)
```

Stages 0, 1, 2 and 3 are independent and can run in any order. Stage 4 requires Stage 1 output and optionally uses Stage 2 output. Stage 5 requires Stage 0 output and uses Stage 1's terrain to frame its outputs.

---

## Running the full pipeline

```bash
# One-time environment setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Stage 0: Arccos hole maps and your rounds (needs an Arccos login; see docs/00_arccos.md)
python pipeline/00_arccos.py --course tedesco --rounds

# Stage 1: LiDAR terrain
python pipeline/01_lidar.py --course tedesco

# Stage 2: PuttView OCR (requires scanned pages)
python pipeline/02_puttview_ocr.py --course tedesco --scans-dir path/to/scans/

# Stage 3: Aerial imagery
python pipeline/03_imagery.py --course tedesco

# Stage 4: Mesh generation
python pipeline/04_mesh_gen.py --course tedesco

# Stage 5: Course layout from Arccos (run after Stage 1 so the SVG lines up with the terrain)
python pipeline/05_layout.py --course tedesco

# Stage 5b: Find bunkers and native grass Arccos is missing (needs Stage 3's 4-band photo)
python pipeline/05b_photo_surfaces.py --course tedesco --write-extras
python pipeline/05_layout.py --course tedesco   # rerun to include the reviewed extras

# Stage 6: Blender assembly (inside Blender; add --render for per-hole QA images)
blender --background --python pipeline/06_blender_assembly.py -- --course tedesco --render
```

---

## Adding a new course

1. Create the directory structure:
   ```
   courses/<slug>/
     data/raw/lidar/
     data/raw/imagery/
     data/raw/puttview/
   ```

2. Add a `config/course.json` entry for the new slug (currently the file contains one course; extend it to a dict keyed by slug if needed).

3. Run each pipeline stage with `--course <slug>`.

All scripts are course-agnostic — the slug is the only required argument.

---

## Shared utilities

`pipeline/utils.py` contains helpers used across multiple stages:

| Function | Used by | Description |
|----------|---------|-------------|
| `REPO_ROOT` | 01, 03, 04 | Absolute path to the repo root |
| `load_course_config(slug)` | 01, 03 | Loads `config/course.json` |
| `bbox_from_center(lat, lon, buffer_km)` | 01, 03 | WGS84 bounding box around a center point |
| `bbox_to_string(bbox)` | 01, 03 | `"xmin,ymin,xmax,ymax"` string |

---

## Directory structure

```
gspro-course-pipeline/
  config/
    course.json               # Course metadata, coordinates, LiDAR settings
  courses/
    tedesco/
      data/
        raw/
          lidar/              # .laz tiles from USGS 3DEP
          imagery/            # Cached NAIP GeoTIFFs
          puttview/           # Scanned PNG pages
        processed/
          heightmaps/         # terrain.tif (float32 DEM, UTM)
          grids/              # hole_XX_green.json (3-yard elevation grids)
          textures/           # imagery.tif + imagery.png
      output/
        terrain/              # heightmap_16bit.png, terrain_meta.json, terrain.obj
        greens/               # hole_XX_green.obj (one per hole)
  pipeline/
    utils.py                  # Shared utilities
    01_lidar.py
    02_puttview_ocr.py
    03_imagery.py
    04_mesh_gen.py
  docs/
    pipeline_overview.md      # This file
    01_lidar.md
    02_puttview_ocr.md
    03_imagery.md
    04_mesh_gen.md
    PLANNING.md               # Project goals and design decisions
  tests/
    test_01_lidar.py
    test_02_puttview_ocr.py
    test_03_imagery.py
    test_04_mesh_gen.py
    fixtures/                 # Synthetic test images and data
  requirements.txt
```

---

## Data source summary

| Stage | Data Source | Cost | Auth required |
|-------|-------------|------|---------------|
| 1 – LiDAR | USGS 3DEP via TNM API | Free | None |
| 2 – Greens | PuttView book (purchased) | ~$30–50/book | N/A |
| 3 – Imagery | NAIP via Planetary Computer STAC | Free | None |
| 0, 5 – Layout | Arccos course maps and your rounds | Arccos membership | Your Arccos login (personal builds only) |

---

## Design principles

**Config-driven** — `config/course.json` is the only place course-specific values live. Changing the course slug runs the entire pipeline on a new course.

**Idempotent downloads** — all download functions skip files that already exist. Re-running after a partial failure is safe and won't re-download completed files.

**No GUI operations** — every processing step is a Python script. Blender is used only for visual QA, scripted via the bpy API in Stage 6.

**Accuracy over speed** — green elevation data uses OCR of printed numeric values (not color approximation), and LiDAR terrain uses the highest-resolution available survey. These choices exist because physical accuracy in GSPro directly affects gameplay quality.

---

## Running tests

```bash
pytest tests/ -v
```

The test suite uses synthetic fixtures (generated images and mock HTTP responses) and does not require network access or external tools like PDAL/Tesseract.
