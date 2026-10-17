# Stage 07: OPCD package

**Script:** `pipeline/07_opcd_package.py`

---

## What it does

Makes the files the OPCD toolset needs to build the course for GSPro, all framed to one square so they line up:

| File | For | What it is |
|------|-----|------------|
| `<slug>_clender.svg` | Clender | Course shapes in the OPCD colours, 1 mm = 1 m, north up, one layer per surface |
| `<slug>_heightmap_<N>.raw` | Unity | 16-bit little-endian RAW, N x N samples, 1 m apart |
| `<slug>_overlay.jpg` | Blender, Unity | The aerial photo at 1 pixel per metre over the same square |
| `unity_terrain.json` | Unity | The numbers for Unity's terrain import |
| `greenkeeper.json`, `greenkeeper.csv` | GreenKeeper | Par, tees, pins, green centres and penalty areas, in Unity coordinates |
| `README.md` | You | The four OPCD steps with this course's numbers filled in |

All in `courses/<slug>/output/opcd/`.

---

## Usage

```bash
python pipeline/07_opcd_package.py --course tedesco
python pipeline/07_opcd_package.py --course tedesco --margin 150   # more ground round the course
```

Needs Stage 01's terrain (`data/processed/heightmaps/<slug>_terrain.tif`) and Stage 05's `holes.json` and `surfaces.geojson`. The aerial photo from Stage 03 is optional.

---

## How it works

### The square

Unity terrain heightmaps are 513, 1025, 2049 or 4097 samples across. The stage picks the smallest that holds every course shape plus `--margin` metres (100 by default) on each side, centred on the course. Tedesco needs 2049 (a 2,048 m square). Corners are whole metres, so every heightmap sample sits on a whole metre.

### Heightmap

The terrain is resampled to 1 m. Heights are stored from a base (1 m under the lowest point) to the top (1 m over the highest), as 0 to 65,535. Rows go south to north, which is what Unity expects with "Flip Vertically" off. Any part of the square the terrain doesn't cover is filled with the nearest height, and the stage warns how much.

### Coordinates

Unity x = metres east of the square's west edge, z = metres north of its south edge, y = metres above the base height. The terrain sits at (0, 0, 0); the FBX from Blender goes there too.

### SVG

The same writer as Stage 05, framed to the square instead of the shapes. Colours follow osm2svg4opcd's `styles.json`, which targets Clender. Check them against the current OPCD V4 palette before submitting, and change `OPCD` in `05_layout.py` if any differ.

---

## Not done by this stage

- Clender itself (submit the SVG to OPCD)
- Conforming meshes in Blender with OPCD Tools, and the FBX export
- Unity materials, grass and trees (OPCD Arborist)
- Typing the tees, pins and hazards into GreenKeeper (the sheet has every position)
