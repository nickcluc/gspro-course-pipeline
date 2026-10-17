# GSPro Course Pipeline

A data-driven pipeline for building accurate golf courses for the GSPro simulator platform.

## Pipeline Stages

| Step | Script | Input | Output |
|------|--------|-------|--------|
| 0 | `pipeline/00_arccos.py` | Arccos login + course id | Hole maps, your rounds |
| 1 | `pipeline/01_lidar.py` | USGS coordinates | Terrain heightmap |
| 2 | `pipeline/02_puttview_ocr.py` | PuttView scans | Per-green elevation grids |
| 3 | `pipeline/03_imagery.py` | Satellite imagery | Texture maps |
| 4 | `pipeline/04_mesh_gen.py` | Heightmaps + grids | 3D meshes |
| 5 | `pipeline/05_layout.py` | Arccos maps + terrain | `holes.json`, surface outlines, OPCD SVG, height check |
| 5b | `pipeline/05b_photo_surfaces.py` | Arccos maps + aerial photo | Missing bunkers, native grass, `extra_surfaces.geojson` |
| 6 | `pipeline/06_blender_assembly.py` | Terrain + greens + layout | Blender scene + QA renders |
| 7 | `pipeline/07_opcd_package.py` | Terrain + layout + photo | OPCD inputs: Clender SVG, Unity heightmap, GreenKeeper sheet |

## Courses

- `courses/tedesco/` — Tedesco Country Club, Marblehead MA

## Adding a New Course

1. Copy `config/course.json` and populate with course details
2. Add raw data to `courses/<slug>/data/raw/`
3. Run pipeline stages in order

## Dependencies

```
pip install pdal gdal rasterio numpy scipy opencv-python pytesseract trimesh
```
