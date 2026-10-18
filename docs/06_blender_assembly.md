# Stage 06: Blender assembly

**Script:** `pipeline/06_blender_assembly.py`

---

## What it does

Builds the whole course as one Blender scene, with no clicking:

1. **Terrain:** the Stage 04 terrain mesh
2. **Surfaces:** every Arccos shape (green, fairway, tee, bunker, water, trees) wrapped onto the terrain, one object per shape, named by hole: `h07_green`, `h12_bunker_03`
3. **PuttView greens:** each Stage 04 green mesh turned and moved to best fit the Arccos outline of that green, then set to the terrain's height
4. **Markers:** every tee and pin spot from `holes.json`
5. **Cameras:** one per hole, behind and above the back tee looking at the green. With `--render`, a tee view and a top view (tee at the bottom, green at the top) of every hole are saved for checking. Each render shows that hole's shapes plus every tree and native area (Arccos files many trees under a neighbouring hole), with tees as white discs and your pin spots as red discs. `--views top` renders only the top views.

Each hole's objects go in a collection, `Hole 01` to `Hole 18`.

---

## Usage

```bash
# Check the inputs and print the plan (no Blender needed)
python pipeline/06_blender_assembly.py --course tedesco --plan-only

# Build the scene (inside Blender, no window)
blender --background --python pipeline/06_blender_assembly.py -- --course tedesco

# Build and render a tee view and a top view of every hole
blender --background --python pipeline/06_blender_assembly.py -- --course tedesco --render

# On a machine without a GPU, render with Cycles on the CPU (slower)
blender --background --python pipeline/06_blender_assembly.py -- --course tedesco --render --engine cycles
```

Instead of the Blender app, `pip install bpy` gives Blender as a Python module (tested with Blender 5.2.2). Then run the stage with that Python: `python pipeline/06_blender_assembly.py --course tedesco`.

---

## Inputs

| File | From | Required |
|------|------|----------|
| `output/terrain/<slug>_terrain.obj` | Stage 04 | Yes |
| `output/layout/holes.json`, `surfaces_local.json` | Stage 05 | Yes |
| `output/greens/hole_XX_green.obj` | Stage 04 | No: Arccos green outlines are used either way |
| `data/processed/grids/hole_XX_green.json` | Stage 02 | No: gives a better green fit |

Optional, in `course.json`, to set a green's turn by hand:

```json
"blender": {"green_rotation_deg": {"7": 90}}
```

---

## Outputs

All in `courses/<slug>/output/blender/`.

| File | Description |
|------|-------------|
| `<slug>.blend` | The assembled scene |
| `assembly_report.json` | Per hole: green turn, how well it fits, how the turn was set; counts; any surface that failed to build |
| `renders/hole_XX_tee.png`, `hole_XX_top.png` | QA renders (`--render`) |

---

## How it works

### Plan, then build

`plan_scene()` is plain Python and numpy. It works out every vertex, name and position, and is fully tested without Blender. `build_scene()` only turns the plan into Blender objects, so the Blender-specific code is small.

### Coordinates

Blender X = east and Y = north, in metres from the terrain centre (Stage 04's frame); Z = elevation in metres above sea level. Stage 04's terrain OBJ stores (east, elevation, north), and is read directly rather than through Blender's OBJ importer, so no axis settings can go wrong.

### Surfaces

Each outline is cut into triangles by ear clipping, which stays inside curved and bent outlines (Blender's own ngon fill spilled outside them: hole 14's water band filled three times its real area). Blender then subdivides edges longer than `--max-edge` (3 m by default) and sets every vertex to the terrain height under it. All 421 Tedesco surfaces cover their outline area to within 1%. Each surface type sits a little higher than the one below it (fairway, tee, trees, green, bunker, water, 1 cm apart), and overlapping shapes of one type are a few millimetres apart, so nothing flickers. A shape that appears on two holes' maps (Tedesco has 156, such as a shared pond) is built once and shown on both holes. Penalty areas (from `extra_surfaces.geojson`) are drawn see-through red.

### Greens

When Stage 05 has shaped a green from its PuttView page, the green mesh is placed with that same fit (`green.puttview_fit` in `holes.json`), so the mesh and the green's outline line up. Otherwise:

PuttView pages aren't necessarily printed north-up. Each green is turned in 5° steps about its centre, which is placed on the Arccos green's centre, and the turn that puts the most PuttView grid points inside the Arccos outline is kept. The report gives that share as `fit`. Below 0.8, the stage prints a warning: check the green in Blender and set `green_rotation_deg` if needed. The green's height is set so its average matches the terrain under the Arccos outline.

Stage 04's green mesh is filled out to a rectangle, so the fit uses the Stage 02 grid points (which only cover the green) when they're there.

---

## Tedesco smoke test

Run on the real Tedesco layout over a stand-in flat terrain (stage 01 not yet run): 379 surfaces, no failures, 162 tee and pin markers, 18 cameras. Renders checked for hole 7.
