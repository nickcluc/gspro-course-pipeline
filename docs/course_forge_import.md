# GSPro Course Forge Import Guide

How to use the Stage 4 outputs in GSPro Course Forge to set up the terrain.

---

## Files you need

After running `python pipeline/04_mesh_gen.py --course tedesco`:

| File | Purpose |
|------|---------|
| `output/terrain/<slug>_heightmap_16bit.png` | Import into Course Forge as the terrain heightmap |
| `output/terrain/<slug>_terrain_meta.json` | Contains the exact numbers to enter in Course Forge |
| `output/terrain/<slug>_terrain.obj` | Open in Blender to visually verify terrain before importing |

---

## Step 1: Read the terrain metadata

Open `<slug>_terrain_meta.json`. The key section is:

```json
"course_forge_settings": {
  "terrain_width_m": 3050.0,
  "terrain_length_m": 2980.0,
  "terrain_height_m": 48.3,
  "heightmap_resolution": 3050
}
```

Keep this file open — you'll enter these values in Course Forge.

---

## Step 2: Import the heightmap

In GSPro Course Forge:

1. Create a new course or open an existing one
2. Navigate to **Terrain** settings
3. Import the 16-bit PNG as the heightmap
4. Enter the values from `terrain_meta.json`:

| Course Forge field | JSON field | Example |
|--------------------|------------|---------|
| Terrain Width | `terrain_width_m` | 3050.0 |
| Terrain Length | `terrain_length_m` | 2980.0 |
| Height Scale | `terrain_height_m` | 48.3 |

> **Critical:** use the exact values from the JSON. The Height Scale is the actual elevation range of the terrain in meters (plus a 2m safety margin). Setting it incorrectly makes the terrain too flat or excessively hilly.

---

## Step 3: Import the aerial texture

Import `output/textures/<slug>_imagery.png` as the terrain base texture. The PNG is aligned to the same bounding box as the heightmap, so UV mapping should match without adjustment when the terrain dimensions match.

---

## Step 4: Visual verification in Blender

Before committing to Course Forge, open `<slug>_terrain.obj` in Blender to spot-check the terrain:

1. Import OBJ: File → Import → Wavefront (.obj)
2. Enter Material Preview mode (Alt+Z) to see shading
3. Check: are the general contours recognizable as the course?
4. Check: are there obvious holes or flat spots that shouldn't be there? (Tree canopy voids from LiDAR)

The OBJ uses the same 5m grid as the default run — good enough for course-wide sanity checking but not a final-quality mesh.

---

## Understanding the coordinate system

The terrain OBJ is centered at origin:
- **X** = east (positive = east of center)
- **Y** = elevation in meters above minimum terrain elevation
- **Z** = -north (positive Z = south of center)

This is Y-up, right-handed — standard for Blender and Unity.

The green OBJs have local coordinates (origin at minimum x/y of the green grid). To place them in Blender:
1. Import the terrain OBJ
2. Import a green OBJ
3. Translate the green to its GPS position on the terrain (convert from lat/lon to UTM, then offset relative to terrain origin)

---

## Elevation values in the JSON

The `elevation` section of `terrain_meta.json` gives absolute values:

```json
"elevation": {
  "min_m": 12.4,
  "max_m": 58.7,
  "range_m": 46.3
}
```

These are absolute UTM elevations (meters above sea level). The heightmap PNG encodes elevation as a normalized 0–65535 value where 0 = `min_m` and 65535 = `min_m + terrain_height_m`. This normalization means the lowest terrain point maps to pure black and the highest to near-white.
