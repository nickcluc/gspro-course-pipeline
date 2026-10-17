# Stage 05b: Photo check (bunkers and native grass)

**Script:** `pipeline/05b_photo_surfaces.py`

---

## What it does

Checks the Arccos map against the NAIP aerial photo from Stage 03, which has red, green, blue and near-infrared bands:

| Finding | Meaning | Overlay colour |
|---------|---------|----------------|
| `bunker_missing_from_arccos` | Sand in the photo, no Arccos bunker there | red |
| `bunker_matches_arccos` | Sand matching an Arccos bunker; its photo outline is usually more accurate than Arccos's | green |
| `arccos_bunker_no_sand_seen` | An Arccos bunker with grass in the photo: grassed over or misplaced | orange |
| `native_grass` | Brown, dry long-grass areas, which Arccos doesn't map | purple |

The shapes you accept go into `courses/<slug>/extra_surfaces.geojson`. Stage 05 adds every shape in that file to the layout, so stages 05 and 06 include them on every rebuild.

---

## Usage

```bash
python pipeline/03_imagery.py --course tedesco --buffer-km 1.1   # 4-band photo covering the course
python pipeline/05b_photo_surfaces.py --course tedesco             # findings + overlays
python pipeline/05b_photo_surfaces.py --course tedesco --write-extras   # also seed extra_surfaces.geojson
python pipeline/05_layout.py --course tedesco                      # layout now includes the extras
```

Review `extra_surfaces.geojson` in QGIS over the photo: delete anything wrong, fix edges, and add what was missed. Each shape needs the properties `surface` (`bunker`, `native`, `green`, `fairway`, `tee`, `water` or `trees`) and `hole`. Running `--write-extras` again overwrites the file, so do it only before you start editing.

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

### Native grass

Native areas are browner and drier than the course's own turf: their red-to-green ratio is above the fairways' 99th percentile, and their plant signal below the fairways' 5th. The image is smoothed over 2 m first. Pavement (almost no plant signal), anything Arccos already maps, and anything more than 35 m from turf or bunkers is left out, and each area must be at least 150 m².

This is less reliable than the bunker check. Summer photos make native grass look close to rough, so some areas are missed and some edges are loose. Treat these as suggestions.

---

## Tedesco results (NAIP 2023, 0.3 m)

- 55 sand areas match Arccos bunkers; 53 of 55 pass the learned limits
- 17 bunkers missing from Arccos, including the fairway bunker left of hole 1 in front of the trees, and four around hole 2's green
- 14 Arccos bunkers with no sand seen (for example 3 on hole 14, where the photo shows grass)
- 27 native grass areas, about 13,000 m²
- Runs in about a minute
