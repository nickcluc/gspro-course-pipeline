# Stage 05b: Photo check (bunkers, greens, native grass and trees)

**Script:** `pipeline/05b_photo_surfaces.py`

---

## What it does

Checks the Arccos map against the NAIP aerial photo from Stage 03, which has red, green, blue and near-infrared bands:

| Finding | Meaning | Overlay colour |
|---------|---------|----------------|
| `bunker_missing_from_arccos` | Sand in the photo, no Arccos bunker there | red |
| `bunker_matches_arccos` | Sand matching an Arccos bunker; its photo outline is usually more accurate than Arccos's | green |
| `arccos_bunker_no_sand_seen` | An Arccos bunker with grass in the photo: grassed over or misplaced | orange |
| `green_from_photo` | The green's real edge, for every Arccos green | pink outline |
| `fairway_with_runup` | A fairway redrawn to carry its mown run-up on to the green | cyan outline |
| `native_grass` | Brown, dry long-grass areas, which Arccos doesn't map | purple |
| `trees_missing_from_arccos` | Woods near the holes that Arccos doesn't map | blue |

The shapes you accept go into `courses/<slug>/extra_surfaces.geojson`. Stage 05 adds every shape in that file to the layout, so stages 05 and 06 include them on every rebuild.

---

## Usage

```bash
python pipeline/03_imagery.py --course tedesco --buffer-km 1.1   # 4-band photo covering the course
python pipeline/05b_photo_surfaces.py --course tedesco             # findings + overlays
python pipeline/05b_photo_surfaces.py --course tedesco --write-extras   # also seed extra_surfaces.geojson
python pipeline/05_layout.py --course tedesco                      # layout now includes the extras
```

Review `extra_surfaces.geojson` in QGIS over the photo: delete anything wrong, fix edges, and add what was missed. Each shape needs the properties `surface` (`bunker`, `native`, `green`, `fairway`, `tee`, `water` or `trees`) and `hole`. Running `--write-extras` again replaces only the shapes it wrote before (those with `source: "aerial photo"`). Your own entries, such as corrections, are kept. To keep a photo shape you edited, change its `source`.

---

## How it works

### It learns from the course itself

What sand looks like is learned from the clean sand inside the bunkers Arccos already maps: pixels that are bright and have no plant signal. At Tedesco that's 27,431 pixels, giving a sand colour of about red 214, green 208, blue 198, near-infrared 193. Every pixel then gets a statistical distance from that sand colour, and anything close counts as sand.

The limits that separate bunkers from look-alikes are also learned, from the sand areas that match Arccos bunkers (55 at Tedesco):

| Limit | What it rejects | Tedesco value |
|-------|-----------------|---------------|
| Near-infrared at least | Paths, pavement, bare dirt (about 155–168) | 174 |
| Length-to-width at most | Cart paths and edges | 3.9 |
| Fill of its bounding box at least | Ragged strips | 0.33 |
| Gap to turf at most | Sand far off the course (car parks, building sites) | 15.5 m |

Areas must also be 15–3,000 m² and at least 2.5 m wide. With fewer than 8 matching bunkers, the stage uses those Tedesco values as defaults.

### Greens

Arccos draws greens as rounded blobs 1–3 m inside the real edge. A green is smooth and evenly mown, so its edge shows as a step in colour from the collar and fringe. For each Arccos green:

1. Its middle (more than 2 m inside the outline) is marked as green. Anything more than 7 m outside it, and any sand, trees or water, is marked as not green.
2. The edge settles on the strongest colour step between the two (a watershed).
3. Tree shadow is dark, so the edge stops at it. The outline is traced as a distance from the green's centre at every degree, and where the edge stops at shadow, the distance comes from the clear edge either side.
4. The distances are smoothed round the circle, so the outline is a clean, rounded shape.

Arccos's own middle always stays in. Turn on `greens_from_photo` in `course.json` to use these outlines in Stage 05.

### Fairway run-ups

Most greens have fairway running up to them, but Arccos often stops the fairway short, and par 3s have no fairway at all. What fairway looks like (colour and smoothness) is learned from inside Arccos's fairways. In front of each green the stage looks toward the fairway's end, and toward the back tee (as wide as the green plus 10 m each side), up to 50 m out. Fairway-looking grass at least 3 m wide that comes within 5 m of the green (the collar can sit between) is the run-up. Where it reaches the fairway, the two are redrawn as one shape; otherwise it's a fairway shape of its own, such as a par 3's approach. Edges are smoothed.

Options in `course.json` (`layout`):

- `fairway_runups_from_photo`: use these fairways in Stage 05
- `rough_in_front_of_green`: holes with rough, not fairway, in front of the green; nothing is added there. Tedesco: `[6, 8, 12, 13]`
- `runup_limit_m`: per hole, how far from the green the run-up may reach, for a fairway that gives way to rough and then starts again just short of the green. Tedesco: `{"9": 25}`

### Trees

Arccos maps only some of the woods. Tree pixels are told apart from turf and rough by three models learned from the course: the Arccos tree areas, the fairways, and a rough strip 2–8 m outside the fairways. Each model uses brightness, plant signal, near-infrared and texture (trees are darker and patchier than grass). Each pixel goes to the closest model. The result is cleaned up, gaps under 50 m² are filled, and anything already mapped as turf, sand or water is left out. Only woods within 60 m of turf are kept. An area must be at least 30 m² (a single tree about 6 m across); under 100 m² it must also stand 4 m clear of Arccos's trees, because next to them it's only the edge of a crown Arccos already has. Large areas are cut into 60 m tiles so each piece stays a simple shape.

The check finds about 70% of the area Arccos already maps as trees, so its edges run a little inside the real tree line.

Two things look like trees from above and are taken out:

- **Shadows.** Tree shadow on grass is dark enough to pass as tree. The stage works out which way shadows fall in this photo (the direction in which Arccos's trees, shifted a few metres, land most often on dark pixels; west at Tedesco, so a morning photo). Dark, smooth pixels up to 12 m down-sun of a tree, and within 15 m of open grass, are dropped (a crown's shady side is dark too, but patchy, so it stays). The inside of woods is left alone.
- **Roofs and paving.** Any tree patch whose typical plant signal is under 10 is dropped; real canopy is around 30.

### Native grass

Native areas are browner and drier than the course's own turf: their red-to-green ratio is above the fairways' 99th percentile, and their plant signal below the fairways' 5th. The image is smoothed over 2 m first. Pavement (almost no plant signal), anything Arccos already maps, and anything more than 35 m from turf or bunkers is left out, and each area must be at least 150 m².

This is less reliable than the bunker check. Summer photos make native grass look close to rough, so some areas are missed and some edges are loose. Treat these as suggestions.

---

## Tedesco results (NAIP 2023, 0.3 m)

- 55 sand areas match Arccos bunkers; 53 of 55 pass the learned limits
- 17 bunkers missing from Arccos, including the fairway bunker left of hole 1 in front of the trees, and four around hole 2's green
- 14 Arccos bunkers with no sand seen (for example 3 on hole 14, where the photo shows grass)
- 27 native grass areas, about 13,000 m²
- 18 greens, 1.1 to 2.2 times the Arccos size (average 1.5); hole 16's is 436 m² against Arccos's 268 m²
- Fairway in front of the green on 14 holes, about 13,300 m² in all, including the par 3s 3 and 7; none on 6, 8, 12 and 13, which have rough in front
- 231 tree areas Arccos doesn't map: 151 on the back nine (29 on hole 16), 80 on the front, where Arccos already maps most single trees
- 12,200 m² of tree shadow and 182 roof or paving patches taken out of the tree areas
- Runs in about 2½ minutes
