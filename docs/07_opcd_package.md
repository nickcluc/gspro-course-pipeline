# Stage 07: OPCD package

**Script:** `pipeline/07_opcd_package.py`

---

## What it does

Makes the files the OPCD toolset needs to build the course for GSPro, all framed to one square so they line up with OPCD's terrain:

| File | For | What it is |
|------|-----|------------|
| `<slug>_clender.svg` | OPCD web app, SVG Jobs (Clender) | Course shapes as an Inkscape SVG in the OPCD colours, 1 mm = 1 m, north up, one layer per colour, overlaps merged and corner contacts joined |
| `clender_check.json` | You | Per colour: shapes in and out, overlap merged, gaps closed, outlines repaired, and any surface left out for an unknown colour |
| `<slug>_terrain_opcd.obj` | Blender | The square's terrain on a 2 m grid in Clender's frame, for conforming the Clender meshes |
| `<slug>_overlay.jpg` | You | The aerial photo at 1 pixel per metre over the same square, for checking alignment |
| `greenkeeper.json`, `greenkeeper.csv` | GreenKeeper | Par, tees, pins, green centres and penalty areas, in Unity coordinates |
| `README.md` | You | The OPCD steps with this course's numbers filled in |
| `<slug>_heightmap_<N>.raw`, `unity_terrain.json` | Unity | Only without an OPCD terrain (fallback, below) |

All in `courses/<slug>/output/opcd/`.

---

## The OPCD workflow this feeds

OPCD now runs through its web app, [app.opcdgolf.com](https://app.opcdgolf.com/) (sign in with Discord). The steps, and where this stage fits:

1. **Terrain / LiDAR tab:** pick the inner square (centre and size) and download the terrain `.unitypackage`. Put the centre and size in `course.json` (below) and run this stage, so everything is framed to that square.
2. **SVG Jobs tab (Clender):** upload `<slug>_clender.svg`. It returns a zip with a Blender 4.2 `.blend` of meshes and a validation log (gaps are marked as `Gap_Issue_…` objects).
3. **Blender 4.2.2 or newer:** open the `.blend` and allow its script, which installs the OPCD Blender tools. Import `<slug>_terrain_opcd.obj` with default settings (it lands under the meshes), conform, run the bunker and water tools, export FBX.
4. **Unity 2018.2.8f1 with the OPCD V4 BaseProject:** import the terrain package, then the FBX at (0, 0, 0), set mesh materials, plant trees with Arborist.
5. **GreenKeeper V4:** tees, pins, hazards and scorecard from `greenkeeper.csv` / `.json`. Then Build Course and load it into GSPro.

Steps 4 and 5 need Windows: Build Course calls a native Windows library (`AesDecrypt.dll`), Unity 2018.2's Mac editor fails Gatekeeper on current macOS, and GSPro is Windows-only. Use Unity 2018.2.8f1, the version the BaseProject and terrain packages are saved with; the older written guides' 2018.4.36f1 and Blender 4.0.2 are out of date (OPCD's Blender add-on installer requires 4.2.2+).

---

## Usage

```bash
python pipeline/07_opcd_package.py --course tedesco
python pipeline/07_opcd_package.py --course tedesco --obj-step 1   # full-detail terrain OBJ (4x the size)
python pipeline/07_opcd_package.py --course tedesco --margin 150   # fallback only: more ground round the course
```

Needs Stage 01's terrain (`data/processed/heightmaps/<slug>_terrain.tif`) and Stage 05's `holes.json` and `surfaces.geojson`. The aerial photo from Stage 03 is optional.

The OPCD terrain goes in `course.json`, exactly as the web app's Terrain Selection shows it:

```json
"opcd": {"terrain": {"center_lat": 42.48031, "center_lon": -70.89911, "size_m": 2000}}
```

---

## How it works

### The square

With `opcd.terrain` set, the square is OPCD's inner terrain: `size_m` across, north up, centred on the given point in the course's UTM zone. At Tedesco this was checked against the downloaded package: its hillshade matches the USGS terrain within 1 m, and the course shapes sit on its 8k overlay. The stage prints how much ground lies between the course and each edge, and warns under 100 m. In the web app, centre the square on the course, not the clubhouse.

Without it (fallback), the stage picks the smallest Unity terrain (513, 1025, 2049 or 4097 samples) that holds every shape plus `--margin` metres, and writes its own 16-bit RAW heightmap (base 1 m under the lowest point, rows south to north, "Flip Vertically" off) and `unity_terrain.json`.

### Coordinates

Three frames describe the same square:

| Frame | Axes, from the square's south-west corner | Used by |
|-------|------|---------|
| Unity | x = m east, z = m north, y = up | OPCD terrain (Terrain (Inner) sits at 0, 0, 0), `greenkeeper.*` |
| Clender `.blend` | X = -(m east), Y = -(m north), Z = up: Unity turned 180° | Clender meshes, `<slug>_terrain_opcd.obj` |
| Real world | EPSG of the course's UTM zone | Pipeline |

With an OPCD terrain, GreenKeeper `y` is real elevation in metres (OPCD's base height isn't in its package in readable form); tees and pins snap to the ground in GreenKeeper anyway.

### SVG

The same writer as Stage 05, framed to the square, written as an Inkscape SVG (Inkscape layers and a named view in millimetres).

Clender wants closed shapes, with no two shapes of one colour overlapping or touching. Arccos breaks that: many shapes are filed under two holes, and tree areas overlap each other. So before writing, all shapes of one colour are merged (shapely), self-crossing outlines repaired, gaps under 1 m closed, everything clipped to the square, and slivers under 1 m² dropped. Merged shapes with a gap inside keep it as a hole (even-odd fill).

Gaps are closed by growing each colour's shapes by 0.5 m and shrinking them back with round joins (`CLOSE_M`). Convex outlines don't move; inside corners round off by 0.5 m. This joins shapes that meet at a single corner, which Clender reports as a gap: at Tedesco the first submission had one, between tree areas of holes 6 and 12. Round joins matter: mitred ones rebuild the sharp corners and reopen the contact.

Colours follow osm2svg4opcd's `styles.json`, which targets Clender (`CLENDER_PALETTE` in `07_opcd_package.py`). What Clender made of them at Tedesco:

| Pipeline surface | Colour | OPCD mesh type |
|------------------|--------|----------------|
| green, fairway, tee, bunker | `#BCE5A4`, `#43E561`, `#A0E5B8`, `#E5E5AA` | Green, Fairway, Tee, Bunker, each with a blend |
| water | `#0000C0` | Lake |
| trees, native | `#164B20` | Deep (deep rough; trees are planted later with Arborist) |
| rough (background) | `#278438` | Rough |

Native grass shares the wood colour, so it becomes deep rough. A surface whose colour isn't in `CLENDER_PALETTE` is left out with a warning (it stays rough) rather than risk a Clender colour error. OPCD's full V4 palette comes with registration (it also has Semi, Pinestraw, Concrete, Creek and custom types): check it, then change `OPCD` in `05_layout.py` and `CLENDER_PALETTE` if any differ.

### Terrain OBJ

The same heights as the terrain, on a 2 m grid (`--obj-step`), written Y-up in Clender's frame, so Blender's default OBJ import puts it right under Clender's meshes (checked on Tedesco: the ground under the greens matches the GreenKeeper sheet to the centimetre). Use it instead of exporting the terrain from Unity.

---

## Not done by this stage

- Submitting the SVG (web app, SVG Jobs)
- Conforming meshes in Blender with OPCD Tools, and the FBX export
- Unity materials, grass and trees (OPCD Arborist)
- Typing the tees, pins and hazards into GreenKeeper (the sheet has every position)
- Cart paths (OPCD's Concrete type): Arccos doesn't map them
