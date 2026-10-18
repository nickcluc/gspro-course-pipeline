# Stage 2: PuttView OCR

**Script:** `pipeline/02_puttview_ocr.py`

---

## What it does

Processes scanned PuttView green map pages and extracts per-cell elevation values into JSON grids. Each output JSON represents one green as a 3-yard grid of `{x, y, elevation_ft}` points. Stage 4 uses these grids to produce interpolated OBJ surface meshes for each hole.

---

## Why PuttView (not Golf Logix)

Golf Logix uses a color heat map only — extracting elevation requires color-to-scale mapping, which is unreliable due to scan color variation and low color resolution (~0.5ft bands).

PuttView prints **numeric elevation values** at every 3-yard grid point with 0.1ft precision. Extraction is OCR, not color inference — far more accurate and easier to automate. PuttView also provides companion hole diagrams with full layout geometry.

---

## Usage

```bash
# Process a directory of scanned pages (one PNG per hole, named in order)
python pipeline/02_puttview_ocr.py --course tedesco --scans-dir path/to/scans/

# Process a single page
python pipeline/02_puttview_ocr.py --course tedesco --page hole_01.png --hole 1

# Override scale if auto-detection fails (see Scale Detection below)
python pipeline/02_puttview_ocr.py --course tedesco --scans-dir scans/ --px-per-yard 18.5
```

---

## Inputs

| Source | Details |
|--------|---------|
| Scanned PNG pages | 300 DPI minimum, 600 DPI recommended. PNG only — JPEG compression corrupts colors. |

### Scan preparation

1. Flatbed scan at 600 DPI — the numeric values are small, higher resolution improves OCR accuracy
2. Output as PNG (not JPEG — compression degrades text edges)
3. Each page is one hole; name files in hole order (e.g. `hole_01.png`, `hole_02.png`)
4. The full double-panel page is fine — the script auto-crops to the top (numeric) panel

---

## Outputs

| File | Description |
|------|-------------|
| `courses/<slug>/data/processed/grids/hole_XX_green.json` | Per-hole elevation grid |

### JSON format

```json
{
  "hole": 1,
  "grid_spacing_yards": 3,
  "px_per_yard": 18.4,
  "outline_yards": [[2.1, 0.4], [14.8, 0.0], ...],
  "points": [
    { "x": 6.0, "y": 3.0, "elevation_ft": 3.4 },
    { "x": 6.0, "y": 6.0, "elevation_ft": 3.8 },
    ...
  ]
}
```

`x` and `y` are in yards from the grid origin (top-left of the green's bounding box, y down the page). `outline_yards` is the green's edge in the same frame; Stage 05 uses it as the green's shape. `elevation_ft` is absolute elevation from PuttView (0–10 feet range).

---

## How it works

### 1. Panel crop
PuttView pages have two stacked panels: numeric grid (top) and color heat map (bottom). `crop_to_numeric_panel()` detects a double-height layout (h > 1.3×w) and takes the top half. Single-panel images pass through unchanged.

### 2. Green boundary detection
`detect_green_boundary()` uses HSV color segmentation to find the green-filled area (hue range covering greens). After morphological closing and dilation to fill gaps, the largest valid contour is selected as the green boundary. A binary mask is produced for filtering grid points to those inside the green.

Contours touching the image edge (within 2% margin) are excluded as likely artifacts. If no valid interior contour is found, the largest overall contour is used as a fallback.

### 3. Scale detection
The script needs to know pixels-per-yard to place the grid correctly.

**Primary: axis label detection** — `estimate_scale_from_axis_labels()` OCRs the top margin of the image looking for yard labels (0, 5, 10, 15...). It computes px/yard from the spacing between detected labels.

**Fallback: green size estimate** — if axis detection fails, `fallback_scale_from_green()` estimates scale from the green contour width assuming a 28-yard average green diameter.

**Manual override** — pass `--px-per-yard <value>` to skip detection entirely. Use this if auto-detection gives incorrect results.

### 4. Grid sampling
The grid origin is snapped to the nearest cell boundary before the green's bounding box. Every candidate grid point (at 3-yard intervals) is checked against the green mask — only points whose center falls inside the green are processed.

### 5. OCR per cell
For each valid grid point, `extract_cell_image()` extracts the upper-center region of the cell (where the elevation number sits, above the break arrow). `ocr_elevation()` then:

1. Scales up small cells to at least 30px height (Tesseract accuracy improves at larger sizes)
2. Tries 4 preprocessing combinations: adaptive threshold + normal/inverted, Otsu threshold + normal/inverted
3. Runs Tesseract with `--psm 7` (single line) and a digit-only whitelist
4. Parses results via `_parse_elevation_text()` which handles common OCR errors: `O→0`, `l→1`, `I→1`, `,→.`
5. Returns the median of all valid readings, range-validated to 0–10ft

### 6. Coordinate output
Each valid reading is converted from pixel position to yards from the grid origin and stored in the points list.

---

## Validating OCR output

The best validation is comparing the JSON output against the PuttView color heat map:
- High elevation cells (warm colors on the heat map) should have higher `elevation_ft` values
- The gradient should be smooth — sudden jumps suggest OCR errors on individual cells
- Overall elevation range is typically 2–6 feet across a green

The `px_per_yard` value in the JSON output can be used to verify scale detection was reasonable: typical PuttView book scans at 600 DPI will be roughly 15–25 px/yard.

---

## Troubleshooting

**Low OCR accuracy (many None cells)**
- Increase scan DPI — 300 DPI minimum, 600 DPI recommended
- Try `--px-per-yard` manual override if scale detection is off
- Check that the scan is in PNG format, not JPEG

**Green boundary not detected**
- Check scan color balance — the green fill needs to read as clearly green in HSV
- Avoid scanning under yellow lighting (shifts hue)
- Verify the image is the numeric panel (top), not the heat map (bottom)

**Scale detection fails**
- The axis labels along the top/left margin must be visible and legible
- Use `--px-per-yard` to bypass: measure the pixel distance between two known yard labels and divide

---

## Dependencies

```
pip install opencv-python pytesseract numpy scipy
brew install tesseract
```
