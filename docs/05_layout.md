# Stage 05: Course layout

**Script:** `pipeline/05_layout.py`

---

## What it does

Turns the Arccos hole maps (Stage 00) into the course layout:

1. **`holes.json`** — for each hole: par, stroke index, green centre, tee boxes, each tee placed with its distance in metres and yards, and your most-used pin spots
2. **`surfaces.geojson`** — every surface outline in WGS84, to check against imagery in QGIS
3. **`surfaces_local.json`** — every outline in the terrain's own metres, the same frame as Stage 04's terrain OBJ, ready for Blender assembly (Stage 06)
4. **`<slug>_surfaces_opcd.svg`** — surfaces as OPCD-coloured shapes, 1 mm = 1 m, north up, framed exactly to the Stage 01 terrain
5. **`elevation_check.json`** — the Lidar terrain height against Arccos's elevation points, per hole

---

## Usage

```bash
python pipeline/05_layout.py --course tedesco
python pipeline/05_layout.py --course tedesco --pins 5   # keep 5 pin spots per hole
```

Run it after Stage 01 for the best result. Without the terrain, the SVG is framed to the shapes with a 40 m border and the elevation check is skipped; the script says so.

---

## Inputs

| File | Description | Required |
|------|-------------|----------|
| `courses/<slug>/data/raw/arccos/holes/hole_XX.json` | Arccos hole maps (Stage 00) | Yes |
| `courses/<slug>/data/raw/arccos/rounds/*.json` | Your rounds (Stage 00 `--rounds`) | No: gives pins and real tee positions |
| `courses/<slug>/data/processed/heightmaps/<slug>_terrain.tif` | Terrain (Stage 01) | No: frames the SVG and enables the height check |
| `courses/<slug>/extra_surfaces.geojson` | Stage 05b, then reviewed by you | No: shapes Arccos lacks (missing bunkers, native grass), added to the layout |

---

## Outputs

All in `courses/<slug>/output/layout/`.

| File | Description |
|------|-------------|
| `holes.json` | Per-hole metadata for GreenKeeper / Course Forge hole setup |
| `surfaces.geojson` | Outlines in WGS84 |
| `surfaces_local.json` | Outlines in terrain-local metres (x = east, z = north, origin = terrain centre) |
| `<slug>_surfaces_opcd.svg` | OPCD-coloured SVG |
| `elevation_check.json` | Lidar minus Arccos height, per hole |

---

## How it works

### Coordinates

Arccos points are WGS84. They're projected to the terrain's CRS (or `lidar.epsg_output` from `course.json` if there's no terrain yet). Local coordinates use Stage 04's frame: origin at the terrain centre, x east, z north. Stage 04's `heightmap_to_obj` computes z as north-positive, although its docstring says "Z = -north"; this stage follows the code.

### Tees

Arccos lists tees (name and distance) separately from the tee box outlines. Each tee is placed, best method first:

1. **From your rounds:** the median start of your first shots from that tee (at least 3 rounds), snapped to the nearest tee box. If no box is within 15 m, the map is probably missing that tee, so your tee-shot position is used and the tee gets a `note`.
2. **From the distance:** the box whose distance to the green (via the fairway centre line, for doglegs) is closest to the listed distance.

Each tee records how it was placed (`placed_by`) and the gap in metres (`error_m`).

### Pins

Your pin positions are grouped on a 3 m grid, and the most-used spots are kept (3 by default). Only rounds on the configured `course_version` are used.

### Surfaces

| Arccos type | Surface | OPCD class | Fill |
|-------------|---------|------------|------|
| 1 | green | golf.green | #BCE5A4 |
| 2, 3 | bunker | golf.bunker | #E5E5AA |
| 4, 5 | water | golf.water_hazard | #0000C0 |
| 6 | tee | golf.tee | #A0E5B8 |
| 7 | fairway | golf.fairway | #43E561 |
| 8 | fairway_center | not drawn (a guide line) | — |
| 9 | trees | natural.wood | #164B20 |
| none | rough | leisure.golf_course | #278438 (SVG base) |

Colours follow the community csites/osm2svg4opcd scheme. Check them against the current OPCD template before submitting to OPCD's tools.

### Elevation check

Each Arccos elevation point is compared with the terrain directly below it. Per hole: mean difference, largest difference, and points more than 0.5 m off. A whole-course offset usually means a different height datum. A few bad points are usually trees or buildings in the Lidar.

---

## Tedesco results (course version 4)

- 18 holes, 549 shapes, 54 pin spots from 67 rounds
- Arccos's map has no tee box near your tee shots for: hole 1 (black and blue tees, ~55 m off), hole 2 black (44 m), hole 3 (black and blue, ~21 m), hole 8 (black and blue, 46 m) and hole 18 blue (18 m). Those tees use your tee-shot positions. Check them on the imagery.
